"""Bounded HTTP transport for raw AI provider responses.

This module deliberately does not parse provider JSON or choose providers. It
limits response bytes before an HTTPX/OpenAI caller can buffer them.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator, Callable

import httpx


class RawTransportError(Exception):
    """A transport failure with a fixed, non-sensitive reason code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"raw_transport_{code}")


class _BoundedResponseStream(httpx.AsyncByteStream):
    def __init__(
        self,
        stream: httpx.AsyncByteStream,
        *,
        max_response_bytes: int,
        deadline: float,
        cleanup_timeout: float,
        begin_close: Callable[
            ["_BoundedResponseStream", httpx.AsyncByteStream], asyncio.Task[bool]
        ],
    ) -> None:
        self._stream = stream
        self._max_response_bytes = max_response_bytes
        self._deadline = deadline
        self._cleanup_timeout = cleanup_timeout
        self._begin_close = begin_close
        self._bytes_read = 0
        self._close_task: asyncio.Task[bool] | None = None
        self._read_cancelled = False

    def start_close(self) -> asyncio.Task[bool]:
        if self._close_task is None:
            self._close_task = self._begin_close(self, self._stream)
        return self._close_task

    def _check_deadline(self) -> None:
        if asyncio.get_running_loop().time() >= self._deadline:
            raise RawTransportError("deadline_exceeded") from None

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            iterator = self._stream.__aiter__()
            while True:
                self._check_deadline()
                try:
                    async with asyncio.timeout_at(self._deadline):
                        chunk = await anext(iterator)
                except StopAsyncIteration:
                    self._check_deadline()
                    break
                except TimeoutError:
                    if asyncio.get_running_loop().time() >= self._deadline:
                        raise RawTransportError("deadline_exceeded") from None
                    raise RawTransportError("response_read_failed") from None
                except asyncio.CancelledError:
                    raise
                except Exception:
                    raise RawTransportError("response_read_failed") from None

                self._check_deadline()
                if self._bytes_read + len(chunk) > self._max_response_bytes:
                    raise RawTransportError("response_too_large") from None
                self._bytes_read += len(chunk)
                self._check_deadline()
                yield chunk
        except asyncio.CancelledError:
            # Keep cleanup owned, but never replace caller cancellation with a
            # close error or wait for an untrusted stream to finish closing.
            self._read_cancelled = True
            self.start_close()
            raise
        except RawTransportError as primary_error:
            try:
                await self._close_before(self._deadline)
            except RawTransportError:
                raise primary_error from None
            raise
        except Exception:
            try:
                await self._close_before(self._deadline)
            except RawTransportError:
                raise RawTransportError("response_read_failed") from None
            raise RawTransportError("response_read_failed") from None
        else:
            await self._close_before(self._deadline)

    async def aclose(self) -> None:
        """Close explicitly with the configured bounded cleanup allowance."""
        if self._read_cancelled:
            self.start_close()
            return
        await self._wait_for_close(
            asyncio.get_running_loop().time() + self._cleanup_timeout
        )

    async def close_before_deadline(self) -> None:
        await self._close_before(self._deadline)

    async def _close_before(self, deadline: float) -> None:
        await self._wait_for_close(deadline)

    async def _wait_for_close(self, deadline: float) -> None:
        task = self.start_close()
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise RawTransportError("response_close_incomplete") from None
        try:
            done, _ = await asyncio.wait({task}, timeout=remaining)
        except asyncio.CancelledError:
            raise
        if not done or asyncio.get_running_loop().time() >= deadline:
            raise RawTransportError("response_close_incomplete") from None
        if not task.result():
            raise RawTransportError("response_close_failed") from None


class BoundedRawResponseTransport(httpx.AsyncBaseTransport):
    """Enforce raw-body, redirect, deadline, and concurrency boundaries."""

    def __init__(
        self,
        *,
        max_response_bytes: int = 1_048_576,
        max_concurrency: int = 4,
        deadline_seconds: float = 30.0,
        cleanup_timeout_seconds: float = 1.0,
        inner: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if type(max_response_bytes) is not int or max_response_bytes <= 0:
            raise ValueError("invalid_response_byte_limit")
        if type(max_concurrency) is not int or max_concurrency <= 0:
            raise ValueError("invalid_concurrency_limit")
        if (
            isinstance(deadline_seconds, bool)
            or not isinstance(deadline_seconds, (int, float))
            or not math.isfinite(deadline_seconds)
            or deadline_seconds <= 0
        ):
            raise ValueError("invalid_operation_deadline")
        if (
            isinstance(cleanup_timeout_seconds, bool)
            or not isinstance(cleanup_timeout_seconds, (int, float))
            or not math.isfinite(cleanup_timeout_seconds)
            or cleanup_timeout_seconds <= 0
        ):
            raise ValueError("invalid_cleanup_deadline")
        self._max_response_bytes = max_response_bytes
        self._deadline_seconds = float(deadline_seconds)
        self._cleanup_timeout_seconds = float(cleanup_timeout_seconds)
        self._slots = asyncio.Semaphore(max_concurrency)
        self._inner = inner or httpx.AsyncHTTPTransport(
            verify=True,
            trust_env=False,
            retries=0,
        )
        self._open_streams: set[_BoundedResponseStream] = set()
        self._close_tasks: set[asyncio.Task[bool]] = set()
        self._quarantined_slots = 0
        self._closing = False
        self._lifetimes: set[asyncio.Future[None]] = set()
        self._shutdown_task: asyncio.Task[bool] | None = None

    def _begin_close(
        self,
        owner: _BoundedResponseStream,
        stream: httpx.AsyncByteStream,
    ) -> asyncio.Task[bool]:
        async def close_stream() -> bool:
            try:
                await stream.aclose()
            except BaseException:
                return False
            return True

        task = asyncio.create_task(close_stream())
        self._close_tasks.add(task)

        def completed(done_task: asyncio.Task[bool]) -> None:
            self._close_tasks.discard(done_task)
            self._open_streams.discard(owner)
            try:
                closed = done_task.result()
            except BaseException:
                closed = False
            if closed:
                self._slots.release()
            else:
                self._quarantined_slots += 1

        task.add_done_callback(completed)
        return task

    def _check_deadline(self, deadline: float) -> None:
        if asyncio.get_running_loop().time() >= deadline:
            raise RawTransportError("deadline_exceeded") from None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self._closing:
            raise RawTransportError("transport_closed") from None
        loop = asyncio.get_running_loop()
        lifetime: asyncio.Future[None] = loop.create_future()
        self._lifetimes.add(lifetime)
        try:
            return await self._handle_registered_request(
                request, loop.time() + self._deadline_seconds
            )
        finally:
            if not lifetime.done():
                lifetime.set_result(None)
            self._lifetimes.discard(lifetime)

    async def _handle_registered_request(
        self,
        request: httpx.Request,
        deadline: float,
    ) -> httpx.Response:
        acquired = False
        response: httpx.Response | None = None
        try:
            self._check_deadline(deadline)
            async with asyncio.timeout_at(deadline):
                await self._slots.acquire()
                acquired = True
                self._check_deadline(deadline)
                if self._closing:
                    raise RawTransportError("transport_closed") from None
            self._check_deadline(deadline)
            if self._closing:
                raise RawTransportError("transport_closed") from None
            request.headers["accept-encoding"] = "identity"
            self._check_deadline(deadline)
            async with asyncio.timeout_at(deadline):
                response = await self._inner.handle_async_request(request)
        except TimeoutError:
            if acquired:
                self._slots.release()
            raise RawTransportError("deadline_exceeded") from None
        except asyncio.CancelledError:
            if acquired:
                self._slots.release()
            raise
        except RawTransportError:
            if acquired:
                self._slots.release()
            raise
        except BaseException:
            if acquired:
                self._slots.release()
            raise RawTransportError("request_failed") from None

        assert response is not None
        bounded_stream = _BoundedResponseStream(
            response.stream,
            max_response_bytes=self._max_response_bytes,
            deadline=deadline,
            cleanup_timeout=self._cleanup_timeout_seconds,
            begin_close=self._begin_close,
        )
        self._open_streams.add(bounded_stream)
        response.stream = bounded_stream
        if self._closing:
            bounded_stream.start_close()
            raise RawTransportError("transport_closed") from None
        try:
            self._check_deadline(deadline)
        except RawTransportError:
            bounded_stream.start_close()
            raise

        if 300 <= response.status_code < 400:
            await bounded_stream.close_before_deadline()
            raise RawTransportError("redirect_rejected") from None

        encoding = response.headers.get("content-encoding", "identity").strip().lower()
        self._check_deadline(deadline)
        if encoding not in ("", "identity"):
            await bounded_stream.close_before_deadline()
            raise RawTransportError("content_encoding_rejected") from None

        return response

    async def _run_shutdown(self) -> bool:
        # Initiate inner closure immediately and concurrently with body draining,
        # so a response that hangs or fails to close cannot skip it.
        async def close_inner() -> bool:
            try:
                await self._inner.aclose()
            except BaseException:
                return False
            return True

        inner_task = asyncio.create_task(close_inner())
        initial_streams = tuple(self._open_streams)
        initial_tasks = [stream.start_close() for stream in initial_streams]
        initial_tasks.extend(
            task for task in tuple(self._close_tasks) if task not in initial_tasks
        )
        lifetimes = tuple(self._lifetimes)
        if lifetimes:
            await asyncio.gather(*lifetimes)

        final_tasks = [stream.start_close() for stream in tuple(self._open_streams)]
        final_tasks.extend(
            task for task in tuple(self._close_tasks) if task not in final_tasks
        )
        all_close_tasks = list(dict.fromkeys([*initial_tasks, *final_tasks]))
        close_outcomes = await asyncio.gather(*all_close_tasks, return_exceptions=True)
        inner_outcome = await asyncio.gather(inner_task, return_exceptions=True)
        response_ok = all(value is True for value in close_outcomes)
        inner_ok = inner_outcome == [True]
        return response_ok and inner_ok and self._quarantined_slots == 0

    async def aclose(self) -> None:
        if not self._closing:
            self._closing = True
        if self._shutdown_task is None:
            self._shutdown_task = asyncio.create_task(self._run_shutdown())
        task = self._shutdown_task
        try:
            done, _ = await asyncio.wait(
                {task},
                timeout=self._cleanup_timeout_seconds,
            )
        except asyncio.CancelledError:
            # The shutdown task remains owned and will observe all late closes.
            raise
        if not done:
            raise RawTransportError("transport_close_incomplete") from None
        if not task.result():
            raise RawTransportError("transport_close_failed") from None


def create_bounded_http_client(
    *,
    max_response_bytes: int = 1_048_576,
    max_concurrency: int = 4,
    deadline_seconds: float = 30.0,
    cleanup_timeout_seconds: float = 1.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """Create an owned client with explicit TLS and no ambient proxy settings.

    ``transport`` is an injection seam for offline tests. Production callers
    should omit it so this factory owns the verified, environment-independent
    network transport.
    """
    bounded = BoundedRawResponseTransport(
        max_response_bytes=max_response_bytes,
        max_concurrency=max_concurrency,
        deadline_seconds=deadline_seconds,
        cleanup_timeout_seconds=cleanup_timeout_seconds,
        inner=transport,
    )
    return httpx.AsyncClient(
        transport=bounded,
        trust_env=False,
        verify=True,
        follow_redirects=False,
        timeout=None,
    )
