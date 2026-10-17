from __future__ import annotations

import asyncio
import math
import time

import httpx
import pytest

from src.core.ai_raw_transport import (
    BoundedRawResponseTransport,
    RawTransportError,
    create_bounded_http_client,
)


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


class TrackingMockTransport(httpx.MockTransport):
    def __init__(self, handler) -> None:
        super().__init__(handler)
        self.close_calls = 0
        self.close_started = asyncio.Event()

    async def aclose(self) -> None:
        self.close_calls += 1
        self.close_started.set()
        await super().aclose()


@pytest.mark.asyncio
async def test_chunked_body_without_length_is_capped_before_buffering(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stream = ChunkStream([b"1234", b"5678", b"SENTINEL"])
    client = create_bounded_http_client(
        max_response_bytes=8,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream)),
    )

    async with client:
        response = await client.send(
            client.build_request("GET", "https://provider.invalid/response"),
            stream=True,
        )
        assert "content-length" not in response.headers
        with pytest.raises(RawTransportError, match="response_too_large") as raised:
            await response.aread()

    assert "SENTINEL" not in str(raised.value)
    assert "SENTINEL" not in repr(raised.value)
    assert "SENTINEL" not in caplog.text
    assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("declared_length", ["1", "999999"])
async def test_content_length_is_not_used_as_the_body_limit(
    declared_length: str,
) -> None:
    stream = ChunkStream([b"1234", b"5678", b"9"])
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            headers={"content-length": declared_length},
            stream=stream,
        )
    )
    client = create_bounded_http_client(max_response_bytes=8, transport=transport)

    async with client:
        response = await client.send(
            client.build_request("GET", "https://provider.invalid/response"),
            stream=True,
        )
        with pytest.raises(RawTransportError, match="response_too_large"):
            await response.aread()


@pytest.mark.asyncio
async def test_oversized_error_response_is_capped_too() -> None:
    stream = ChunkStream([b"error", b" body exceeds cap"])
    client = create_bounded_http_client(
        max_response_bytes=8,
        transport=httpx.MockTransport(lambda _: httpx.Response(503, stream=stream)),
    )

    async with client:
        response = await client.send(
            client.build_request("GET", "https://provider.invalid/response"),
            stream=True,
        )
        assert response.status_code == 503
        with pytest.raises(RawTransportError, match="response_too_large"):
            await response.aread()


@pytest.mark.asyncio
async def test_requests_identity_and_rejects_compressed_responses() -> None:
    seen_encoding: list[str] = []
    stream = ChunkStream([b"compressed bytes"])

    def handler(request: httpx.Request) -> httpx.Response:
        seen_encoding.append(request.headers["accept-encoding"])
        return httpx.Response(
            200,
            headers={"content-encoding": "gzip"},
            stream=stream,
        )

    client = create_bounded_http_client(transport=httpx.MockTransport(handler))
    async with client:
        with pytest.raises(RawTransportError, match="content_encoding_rejected"):
            await client.get("https://provider.invalid/response")

    assert seen_encoding == ["identity"]
    assert stream.closed


@pytest.mark.asyncio
async def test_redirect_is_rejected_without_forwarding_credentials_again() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"location": "https://elsewhere.invalid/"})

    client = create_bounded_http_client(transport=httpx.MockTransport(handler))
    async with client:
        with pytest.raises(RawTransportError, match="redirect_rejected"):
            await client.get(
                "https://provider.invalid/response",
                headers={"authorization": "Bearer AUTH_SENTINEL"},
                follow_redirects=True,
            )

    assert len(requests) == 1
    assert requests[0].headers["authorization"] == "Bearer AUTH_SENTINEL"


@pytest.mark.asyncio
async def test_slot_remains_owned_until_response_stream_closes() -> None:
    requests = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, stream=ChunkStream([b"ok"]))

    transport = BoundedRawResponseTransport(
        max_concurrency=1,
        inner=httpx.MockTransport(handler),
    )
    client = httpx.AsyncClient(transport=transport)
    first = await client.send(
        client.build_request("GET", "https://provider.invalid/1"), stream=True
    )
    second_task = asyncio.create_task(
        client.send(
            client.build_request("GET", "https://provider.invalid/2"), stream=True
        )
    )
    await asyncio.sleep(0)
    assert requests == 1
    assert not second_task.done()

    await first.aclose()
    second = await asyncio.wait_for(second_task, timeout=1)
    assert requests == 2
    await second.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_shutdown_denies_queued_request_before_inner_dispatch() -> None:
    close_started = asyncio.Event()
    allow_close = asyncio.Event()
    calls = 0

    class DelayedCloseStream(ChunkStream):
        async def aclose(self) -> None:
            close_started.set()
            await allow_close.wait()
            self.closed = True

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            stream=DelayedCloseStream([b"first"])
            if calls == 1
            else ChunkStream([b"unexpected"]),
        )

    inner = TrackingMockTransport(handler)
    client = create_bounded_http_client(max_concurrency=1, transport=inner)
    bounded_transport = client._transport
    await client.send(
        client.build_request("GET", "https://provider.invalid/1"), stream=True
    )
    queued = asyncio.create_task(
        client.send(
            client.build_request("GET", "https://provider.invalid/2"), stream=True
        )
    )
    while len(bounded_transport._lifetimes) < 1:
        await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert calls == 1

    shutdown = asyncio.create_task(client.aclose())
    await close_started.wait()
    assert not shutdown.done()
    assert not queued.done()
    allow_close.set()
    with pytest.raises(RawTransportError, match="transport_closed"):
        await queued
    await shutdown
    assert calls == 1
    assert inner.close_calls == 1
    assert bounded_transport._shutdown_task.done()
    assert bounded_transport._shutdown_task.result() is True


@pytest.mark.asyncio
async def test_late_header_response_is_closed_and_shutdown_waits_for_lifecycle() -> (
    None
):
    headers_started = asyncio.Event()
    release_headers = asyncio.Event()
    body_close_started = asyncio.Event()

    class CountedCloseStream(ChunkStream):
        def __init__(self) -> None:
            super().__init__([b"late"])
            self.close_calls = 0

        async def aclose(self) -> None:
            self.close_calls += 1
            body_close_started.set()
            self.closed = True

    body = CountedCloseStream()

    class DelayedHeaderTransport(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self.close_calls = 0

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            headers_started.set()
            await release_headers.wait()
            return httpx.Response(200, stream=body, request=request)

        async def aclose(self) -> None:
            self.close_calls += 1

    inner = DelayedHeaderTransport()
    client = create_bounded_http_client(
        cleanup_timeout_seconds=0.03,
        transport=inner,
    )
    bounded_transport = client._transport
    request_task = asyncio.create_task(
        client.send(
            client.build_request("GET", "https://provider.invalid/late"), stream=True
        )
    )
    await headers_started.wait()
    assert len(bounded_transport._lifetimes) == 1

    with pytest.raises(RawTransportError, match="transport_close_incomplete"):
        await client.aclose()
    assert not bounded_transport._shutdown_task.done()
    assert inner.close_calls == 1

    release_headers.set()
    with pytest.raises(RawTransportError, match="transport_closed"):
        await request_task
    await body_close_started.wait()
    assert body.close_calls == 1
    assert await asyncio.wait_for(bounded_transport._shutdown_task, timeout=1) is True
    assert inner.close_calls == 1
    assert body.close_calls == 1
    assert not bounded_transport._lifetimes


@pytest.mark.asyncio
async def test_cancelled_queued_request_settles_lifecycle_without_releasing_slot() -> (
    None
):
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, stream=ChunkStream([b"body"]))

    inner = TrackingMockTransport(handler)
    client = create_bounded_http_client(max_concurrency=1, transport=inner)
    bounded_transport = client._transport
    first = await client.send(
        client.build_request("GET", "https://provider.invalid/1"), stream=True
    )
    queued = asyncio.create_task(
        client.send(
            client.build_request("GET", "https://provider.invalid/2"), stream=True
        )
    )
    while not bounded_transport._lifetimes:
        await asyncio.sleep(0)
    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    assert not bounded_transport._lifetimes
    assert calls == 1

    third_task = asyncio.create_task(
        client.send(
            client.build_request("GET", "https://provider.invalid/3"), stream=True
        )
    )
    await asyncio.sleep(0)
    assert not third_task.done()
    assert calls == 1
    await first.aclose()
    third = await third_task
    assert calls == 2
    await third.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_cancelled_header_request_settles_lifecycle() -> None:
    headers_started = asyncio.Event()

    class DelayedHeaderTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            headers_started.set()
            await asyncio.Event().wait()
            return httpx.Response(200, stream=ChunkStream([b"never"]), request=request)

        async def aclose(self) -> None:
            return None

    client = create_bounded_http_client(transport=DelayedHeaderTransport())
    bounded_transport = client._transport
    request_task = asyncio.create_task(
        client.send(
            client.build_request("GET", "https://provider.invalid/header"), stream=True
        )
    )
    await headers_started.wait()
    assert len(bounded_transport._lifetimes) == 1
    request_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request_task
    assert not bounded_transport._lifetimes
    await client.aclose()
    assert bounded_transport._shutdown_task.done()
    assert bounded_transport._shutdown_task.result() is True


@pytest.mark.asyncio
async def test_cancellation_during_body_read_releases_capacity() -> None:
    entered = asyncio.Event()

    class WaitingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            entered.set()
            await asyncio.Event().wait()
            yield b"never"

        async def aclose(self) -> None:
            return None

    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        stream = WaitingStream() if calls == 1 else ChunkStream([b"ok"])
        return httpx.Response(200, stream=stream)

    client = create_bounded_http_client(
        max_concurrency=1,
        transport=httpx.MockTransport(handler),
    )
    first = await client.send(
        client.build_request("GET", "https://provider.invalid/1"), stream=True
    )
    reader = asyncio.create_task(first.aread())
    await entered.wait()
    reader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await reader

    second = await asyncio.wait_for(
        client.send(
            client.build_request("GET", "https://provider.invalid/2"), stream=True
        ),
        timeout=1,
    )
    assert calls == 2
    assert await second.aread() == b"ok"
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("close_mode", ["delayed", "failed"])
async def test_httpx_buffered_get_preserves_cancellation_when_close_is_unfinished_or_fails(
    close_mode: str,
) -> None:
    read_started = asyncio.Event()
    close_started = asyncio.Event()
    allow_close = asyncio.Event()
    calls = 0

    class CancelledReadStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            read_started.set()
            await asyncio.Event().wait()
            yield b"unreachable"

        async def aclose(self) -> None:
            close_started.set()
            if close_mode == "delayed":
                await allow_close.wait()
                return
            raise RuntimeError("CLOSE_SENTINEL")

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, stream=CancelledReadStream())
        return httpx.Response(200, stream=ChunkStream([b"next"]))

    client = create_bounded_http_client(
        max_concurrency=1,
        deadline_seconds=0.15,
        cleanup_timeout_seconds=0.02,
        transport=httpx.MockTransport(handler),
    )
    bounded_transport = client._transport
    get_task = asyncio.create_task(client.get("https://provider.invalid/1"))
    await read_started.wait()
    get_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await get_task
    await close_started.wait()

    queued = asyncio.create_task(client.get("https://provider.invalid/2"))
    await asyncio.sleep(0)
    assert calls == 1
    assert not queued.done()

    if close_mode == "delayed":
        allow_close.set()
        assert await asyncio.wait_for(queued, timeout=1)
        assert calls == 2
        await client.aclose()
    else:
        with pytest.raises(RawTransportError, match="deadline_exceeded"):
            await queued
        assert calls == 1
        with pytest.raises(RawTransportError, match="transport_close_failed"):
            await client.aclose()

    assert bounded_transport._close_tasks == set()


@pytest.mark.asyncio
@pytest.mark.parametrize("close_mode", ["failed", "delayed"])
async def test_reader_cancellation_survives_failed_or_hanging_close(
    close_mode: str,
) -> None:
    read_started = asyncio.Event()
    close_started = asyncio.Event()
    allow_close = asyncio.Event()

    class ReaderStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            read_started.set()
            await asyncio.Event().wait()
            yield b"never"

        async def aclose(self) -> None:
            close_started.set()
            if close_mode == "delayed":
                await allow_close.wait()
            else:
                raise RuntimeError("CLOSE_SENTINEL")

    inner = TrackingMockTransport(lambda _: httpx.Response(200, stream=ReaderStream()))
    client = create_bounded_http_client(
        max_concurrency=1,
        deadline_seconds=0.3,
        cleanup_timeout_seconds=0.03,
        transport=inner,
    )
    response = await client.send(
        client.build_request("GET", "https://provider.invalid/1"), stream=True
    )
    reader = asyncio.create_task(response.aread())
    await read_started.wait()
    reader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await reader
    await close_started.wait()

    queued = asyncio.create_task(
        client.send(
            client.build_request("GET", "https://provider.invalid/2"), stream=True
        )
    )
    if close_mode == "delayed":
        await asyncio.sleep(0)
        assert not queued.done()
        allow_close.set()
        second = await asyncio.wait_for(queued, timeout=1)
        await second.aclose()
    else:
        with pytest.raises(RawTransportError, match="deadline_exceeded"):
            await queued
        with pytest.raises(RawTransportError, match="transport_close_failed"):
            await client.aclose()
        assert inner.close_calls == 1

    if close_mode == "delayed":
        await client.aclose()


@pytest.mark.asyncio
async def test_deadline_includes_stream_reads() -> None:
    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(0.05)
            yield b"late"

        async def aclose(self) -> None:
            return None

    client = create_bounded_http_client(
        deadline_seconds=0.01,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, stream=SlowStream())
        ),
    )
    async with client:
        response = await client.send(
            client.build_request("GET", "https://provider.invalid/response"),
            stream=True,
        )
        with pytest.raises(RawTransportError, match="deadline_exceeded"):
            await response.aread()


@pytest.mark.asyncio
@pytest.mark.parametrize("chunks", [[b"immediate"], []])
async def test_expired_deadline_rejects_immediate_body_and_eof(
    chunks: list[bytes],
) -> None:
    client = create_bounded_http_client(
        deadline_seconds=0.01,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, stream=ChunkStream(chunks))
        ),
    )
    response = await client.send(
        client.build_request("GET", "https://provider.invalid/expired"), stream=True
    )
    await asyncio.sleep(0.02)
    with pytest.raises(RawTransportError, match="deadline_exceeded"):
        await response.aread()
    await client.aclose()


@pytest.mark.asyncio
async def test_expired_deadline_after_non_suspending_headers_keeps_close_owned() -> (
    None
):
    close_finished = asyncio.Event()

    class ObservableStream(ChunkStream):
        async def aclose(self) -> None:
            self.closed = True
            close_finished.set()

    body = ObservableStream([b"unused"])

    def handler(_: httpx.Request) -> httpx.Response:
        time.sleep(0.02)
        return httpx.Response(200, stream=body)

    client = create_bounded_http_client(
        deadline_seconds=0.01,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(RawTransportError, match="deadline_exceeded"):
        await client.send(
            client.build_request("GET", "https://provider.invalid/late-headers"),
            stream=True,
        )
    await asyncio.wait_for(close_finished.wait(), timeout=1)
    assert body.closed
    await client.aclose()


@pytest.mark.parametrize("value", [True, 1.0, 0, -1])
def test_byte_and_concurrency_limits_require_positive_integers(value: object) -> None:
    with pytest.raises(ValueError):
        BoundedRawResponseTransport(max_response_bytes=value)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        BoundedRawResponseTransport(max_concurrency=value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [True, float("nan"), math.inf, -math.inf, 0, -1, "1"])
def test_deadline_must_be_a_finite_positive_number(value: object) -> None:
    with pytest.raises(ValueError, match="invalid_operation_deadline"):
        BoundedRawResponseTransport(deadline_seconds=value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [True, float("nan"), math.inf, -math.inf, 0, -1, "1"])
def test_cleanup_deadline_must_be_a_finite_positive_number(value: object) -> None:
    with pytest.raises(ValueError, match="invalid_cleanup_deadline"):
        BoundedRawResponseTransport(cleanup_timeout_seconds=value)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_read_failure_is_translated_without_exception_text_or_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise RuntimeError("BODY_AND_AUTH_SENTINEL")
            yield b"unused"

        async def aclose(self) -> None:
            return None

    client = create_bounded_http_client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, stream=BrokenStream())
        )
    )
    async with client:
        response = await client.send(
            client.build_request("GET", "https://provider.invalid"), stream=True
        )
        with pytest.raises(RawTransportError, match="response_read_failed") as raised:
            await response.aread()

    assert "BODY_AND_AUTH_SENTINEL" not in str(raised.value)
    assert "BODY_AND_AUTH_SENTINEL" not in repr(raised.value)
    assert "BODY_AND_AUTH_SENTINEL" not in caplog.text


@pytest.mark.asyncio
async def test_concurrent_double_close_shares_one_owned_close_task() -> None:
    close_started = asyncio.Event()
    allow_close = asyncio.Event()

    class DelayedCloseStream(ChunkStream):
        def __init__(self) -> None:
            super().__init__([b"ok"])
            self.close_calls = 0

        async def aclose(self) -> None:
            self.close_calls += 1
            close_started.set()
            await allow_close.wait()
            self.closed = True

    body = DelayedCloseStream()
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200, stream=body if calls == 1 else ChunkStream([b"next"])
        )

    client = create_bounded_http_client(
        max_concurrency=1,
        transport=httpx.MockTransport(handler),
    )
    response = await client.send(
        client.build_request("GET", "https://provider.invalid/1"), stream=True
    )
    closing = [asyncio.create_task(response.aclose()) for _ in range(2)]
    await close_started.wait()
    allow_close.set()
    await asyncio.gather(*closing)

    assert body.close_calls == 1
    second = await client.send(
        client.build_request("GET", "https://provider.invalid/2"), stream=True
    )
    assert calls == 2
    await second.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_explicit_close_uses_configured_cleanup_budget_and_keeps_slot() -> None:
    close_started = asyncio.Event()
    allow_close = asyncio.Event()
    calls = 0

    class DelayedCloseStream(ChunkStream):
        async def aclose(self) -> None:
            close_started.set()
            await allow_close.wait()
            self.closed = True

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = DelayedCloseStream([b"ok"]) if calls == 1 else ChunkStream([b"next"])
        return httpx.Response(200, stream=body)

    client = create_bounded_http_client(
        max_concurrency=1,
        deadline_seconds=1.0,
        cleanup_timeout_seconds=0.02,
        transport=httpx.MockTransport(handler),
    )
    response = await client.send(
        client.build_request("GET", "https://provider.invalid/1"), stream=True
    )
    started = asyncio.get_running_loop().time()
    with pytest.raises(RawTransportError, match="response_close_incomplete"):
        await response.aclose()
    elapsed = asyncio.get_running_loop().time() - started
    assert elapsed < 0.1
    await close_started.wait()

    queued = asyncio.create_task(
        client.send(
            client.build_request("GET", "https://provider.invalid/2"), stream=True
        )
    )
    await asyncio.sleep(0)
    assert not queued.done()
    assert calls == 1

    allow_close.set()
    second = await asyncio.wait_for(queued, timeout=1)
    assert calls == 2
    await second.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_repeated_caller_cancellation_does_not_cancel_close_or_release_slot() -> (
    None
):
    close_started = asyncio.Event()
    allow_close = asyncio.Event()
    calls = 0

    class DelayedCloseStream(ChunkStream):
        async def aclose(self) -> None:
            close_started.set()
            await allow_close.wait()
            self.closed = True

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = DelayedCloseStream([b"ok"]) if calls == 1 else ChunkStream([b"next"])
        return httpx.Response(200, stream=body)

    client = create_bounded_http_client(
        max_concurrency=1,
        deadline_seconds=0.08,
        cleanup_timeout_seconds=0.04,
        transport=httpx.MockTransport(handler),
    )
    first = await client.send(
        client.build_request("GET", "https://provider.invalid/1"), stream=True
    )
    closer = asyncio.create_task(first.aclose())
    await close_started.wait()
    closer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closer
    second_closer = asyncio.create_task(first.stream.aclose())
    await asyncio.sleep(0)
    second_closer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second_closer

    with pytest.raises(RawTransportError, match="deadline_exceeded"):
        await client.send(
            client.build_request("GET", "https://provider.invalid/2"), stream=True
        )
    assert calls == 1

    allow_close.set()
    third = await asyncio.wait_for(
        client.send(
            client.build_request("GET", "https://provider.invalid/3"), stream=True
        ),
        timeout=1,
    )
    assert calls == 2
    await third.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_redirect_with_hanging_close_times_out_without_freeing_slot() -> None:
    close_started = asyncio.Event()
    allow_close = asyncio.Event()
    calls = 0

    class DelayedCloseStream(ChunkStream):
        async def aclose(self) -> None:
            close_started.set()
            await allow_close.wait()

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                302,
                headers={"location": "https://elsewhere.invalid"},
                stream=DelayedCloseStream([b"redirect"]),
            )
        return httpx.Response(200, stream=ChunkStream([b"ok"]))

    client = create_bounded_http_client(
        max_concurrency=1,
        deadline_seconds=0.04,
        cleanup_timeout_seconds=0.02,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(RawTransportError, match="response_close_incomplete"):
        await client.get("https://provider.invalid/redirect", follow_redirects=True)
    await close_started.wait()
    with pytest.raises(RawTransportError, match="deadline_exceeded"):
        await client.send(
            client.build_request("GET", "https://provider.invalid/queued"), stream=True
        )
    assert calls == 1

    allow_close.set()
    response = await asyncio.wait_for(
        client.send(
            client.build_request("GET", "https://provider.invalid/recovered"),
            stream=True,
        ),
        timeout=1,
    )
    assert calls == 2
    await response.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_failed_close_quarantines_capacity_and_shutdown_reports_safe_failure() -> (
    None
):
    calls = 0

    class FailedCloseStream(ChunkStream):
        async def aclose(self) -> None:
            raise RuntimeError("CLOSE_SENTINEL")

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, stream=FailedCloseStream([b"ok"]))

    inner = TrackingMockTransport(handler)
    client = create_bounded_http_client(
        max_concurrency=1,
        deadline_seconds=0.04,
        transport=inner,
    )
    response = await client.send(
        client.build_request("GET", "https://provider.invalid/1"), stream=True
    )
    with pytest.raises(RawTransportError, match="response_close_failed") as raised:
        await response.aclose()
    assert "CLOSE_SENTINEL" not in str(raised.value)

    with pytest.raises(RawTransportError, match="deadline_exceeded"):
        await client.send(
            client.build_request("GET", "https://provider.invalid/2"), stream=True
        )
    assert calls == 1
    with pytest.raises(
        RawTransportError, match="transport_close_failed"
    ) as shutdown_error:
        await client.aclose()
    assert "CLOSE_SENTINEL" not in str(shutdown_error.value)
    assert inner.close_calls == 1


@pytest.mark.asyncio
async def test_shutdown_owns_inner_close_and_observes_late_response_close() -> None:
    close_started = asyncio.Event()
    allow_close = asyncio.Event()

    class DelayedCloseStream(ChunkStream):
        async def aclose(self) -> None:
            close_started.set()
            await allow_close.wait()

    inner = TrackingMockTransport(
        lambda _: httpx.Response(200, stream=DelayedCloseStream([b"ok"]))
    )
    client = create_bounded_http_client(
        cleanup_timeout_seconds=0.02,
        transport=inner,
    )
    bounded_transport = client._transport
    response = await client.send(
        client.build_request("GET", "https://provider.invalid/1"), stream=True
    )
    closer = asyncio.create_task(response.aclose())
    await close_started.wait()
    with pytest.raises(RawTransportError, match="response_close_incomplete"):
        await closer
    with pytest.raises(RawTransportError, match="transport_close_incomplete"):
        await client.aclose()
    await asyncio.wait_for(inner.close_started.wait(), timeout=1)
    assert inner.close_calls == 1

    allow_close.set()
    assert await asyncio.wait_for(bounded_transport._shutdown_task, timeout=1) is True
    assert inner.close_calls == 1


@pytest.mark.asyncio
async def test_cancelling_public_shutdown_does_not_discard_owned_shutdown_task() -> (
    None
):
    allow_close = asyncio.Event()

    class DelayedCloseStream(ChunkStream):
        async def aclose(self) -> None:
            await allow_close.wait()

    inner = TrackingMockTransport(
        lambda _: httpx.Response(200, stream=DelayedCloseStream([b"ok"]))
    )
    client = create_bounded_http_client(
        cleanup_timeout_seconds=0.2,
        transport=inner,
    )
    bounded_transport = client._transport
    await client.send(
        client.build_request("GET", "https://provider.invalid/1"), stream=True
    )
    shutdown_waiter = asyncio.create_task(client.aclose())
    await asyncio.wait_for(inner.close_started.wait(), timeout=1)
    shutdown_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await shutdown_waiter
    assert inner.close_calls == 1

    allow_close.set()
    assert await asyncio.wait_for(bounded_transport._shutdown_task, timeout=1) is True
    assert inner.close_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [200, 503])
async def test_openai_sdk_reports_bounded_raw_response_failure(
    status_code: int,
) -> None:
    import openai

    body_sentinel = b"BODY_SENTINEL" * 4
    request_sentinel = "REQUEST_KEY_SENTINEL"
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            status_code,
            stream=ChunkStream([b'{"choices":[]}', body_sentinel]),
        )

    http_client = create_bounded_http_client(
        max_response_bytes=16,
        transport=httpx.MockTransport(handler),
    )
    sdk = openai.AsyncOpenAI(
        api_key=request_sentinel, http_client=http_client, max_retries=0
    )
    try:
        with pytest.raises(openai.APIConnectionError) as raised:
            await sdk.chat.completions.with_raw_response.create(
                model="synthetic-model",
                messages=[{"role": "user", "content": "synthetic"}],
            )
    finally:
        await sdk.close()

    assert calls == 1
    assert isinstance(raised.value.__cause__, RawTransportError)
    assert raised.value.__cause__.code == "response_too_large"
    assert "BODY_SENTINEL" not in str(raised.value)
    assert "BODY_SENTINEL" not in repr(raised.value)
    assert request_sentinel not in str(raised.value)
    assert request_sentinel not in repr(raised.value)
