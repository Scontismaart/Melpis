"""Private CrewAI chat adapter with raw, durable provider accounting.

No application factory activates this implementation until its callers supply
an execution-owned accounting session. No SDK usage defaults are trusted.
"""

import json
import asyncio
import threading
import time
from urllib.parse import urlsplit

import httpx
from crewai import BaseLLM
from pydantic import BaseModel, PrivateAttr

from src.core.ai_raw_transport import create_bounded_http_client
from src.core.ai_providers import PROVIDERS
from src.core.cost_policy import assert_model_allowed
from src.core.llm_client_policy import LLMClientPolicy
from src.core.provider_accounting import (
    ProviderAccountingError,
    ProviderAccountingContext,
    RawProviderUsage,
    provider_model_matches,
)
from src.core.provider_evidence import safe_provider_generation_id, safe_provider_request_id

_NETWORK_SLOTS = threading.BoundedSemaphore(3)
_MAX_BODY = 1_048_576
_ENDPOINTS = {"groq": "https://api.groq.com/openai/v1",
              "openrouter": "https://openrouter.ai/api/v1",
              "cerebras": "https://api.cerebras.ai/v1",
              "mistral": "https://api.mistral.ai/v1"}


def _error(message="Provider response could not be verified"):
    raise ProviderAccountingError(message) from None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("invalid JSON constant")


class ProviderResponseVerificationError(ProviderAccountingError):
    """Safe parse error with only independently verified response metadata."""

    def __init__(self, actual_model=None, usage_present=None):
        super().__init__("Provider response could not be verified")
        self.actual_model = actual_model
        self.usage_present = usage_present


def parse_raw_provider_response(body: bytes, requested_model: str):
    """Preserve missing versus explicit zero before any SDK normalization."""
    try:
        payload = json.loads(body, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (ValueError, TypeError, RecursionError):
        raise ProviderResponseVerificationError() from None
    if type(payload) is not dict:
        raise ProviderResponseVerificationError() from None

    raw_model = payload.get("model")
    actual_model = (raw_model if type(raw_model) is str and raw_model
                    and len(raw_model) <= 256
                    and provider_model_matches(requested_model, raw_model) else None)
    usage_present = "usage" in payload
    if actual_model is None:
        raise ProviderResponseVerificationError(None, usage_present) from None
    if not usage_present:
        raise ProviderResponseVerificationError(actual_model, False) from None
    usage = payload["usage"]
    if type(usage) is not dict:
        raise ProviderResponseVerificationError(actual_model, True) from None
    try:
        verified = RawProviderUsage(actual_model, usage["prompt_tokens"],
                                    usage["completion_tokens"], usage["total_tokens"])
    except (KeyError, TypeError, ProviderAccountingError):
        raise ProviderResponseVerificationError(actual_model, True) from None
    return payload, verified


def _provider_request_id(payload=None, headers=None):
    """Return a provider request ID; completion/generation IDs are separate."""
    candidates = []
    if type(payload) is dict:
        candidates.append(payload.get("request_id"))
    if headers is not None:
        candidates.extend((headers.get("x-request-id"), headers.get("request-id")))
    return next((value for value in candidates if safe_provider_request_id(value)), None)


def _provider_generation_id(payload=None, headers=None, provider=None):
    """Accept only OpenRouter's explicit generation namespace, never req/chatcmpl IDs."""
    if provider != "openrouter":
        return None
    candidates = []
    if headers is not None:
        candidates.append(headers.get("x-generation-id"))
    if type(payload) is dict:
        candidates.append(payload.get("id"))
    return next((value for value in candidates if safe_provider_generation_id(value)), None)


def _with_provider_correlation(body: bytes, attempt):
    """Send the Melpis attempt UUID as OpenAI-compatible `user` metadata where documented."""
    if attempt.provider not in {"groq", "openrouter"}:
        return body
    try:
        payload = json.loads(body)
        payload["user"] = str(attempt.attempt_id)
        encoded = json.dumps(payload, allow_nan=False, ensure_ascii=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeDecodeError):
        _error()
    if len(encoded) > 262_144:
        _error()
    return encoded


def _content(payload):
    try:
        choices = payload["choices"]
        if type(choices) is not list or len(choices) != 1:
            _error()
        message = choices[0]["message"]
        text = message["content"]
        if (not isinstance(text, str) or not text.strip()
                or message.get("tool_calls") or message.get("function_call")):
            _error()
        return text
    except (AttributeError, KeyError, TypeError):
        _error()


class ObservedChatLLM(BaseLLM):
    """An execution-local model; no tools, streaming or ambient HTTP clients."""

    _policy: LLMClientPolicy = PrivateAttr()
    _session: ProviderAccountingContext | None = PrivateAttr(default=None)
    _reason: str = PrivateAttr(default="generation")
    _timeout: float = PrivateAttr(default=20)
    _sync_transport: object = PrivateAttr(default=None)
    _async_transport: object = PrivateAttr(default=None)

    def __init__(self, *, policy, session=None, reason="generation", temperature=0.4,
                 max_tokens=250, timeout=20, sync_transport=None, async_transport=None):
        if type(policy) is not LLMClientPolicy:
            _error()
        if policy.provider is not PROVIDERS.get(policy.provider.name):
            _error()
        assert_model_allowed(policy.model)
        params = policy.client_params()
        super().__init__(model=params["model"], temperature=temperature,
                         max_tokens=max(1, min(int(max_tokens), 1000)), stream=False)
        self._policy, self._session, self._reason = policy, session, reason
        self._timeout = min(30, max(0.1, float(timeout)))
        # Test seams are private instance state, never configuration or JSON.
        self._sync_transport, self._async_transport = sync_transport, async_transport

    def supports_function_calling(self):
        return False

    def supports_stop_words(self):
        return True

    def _parameters(self, messages, tools, available_functions, response_model):
        if type(self._session) is not ProviderAccountingContext or tools or available_functions:
            _error()
        assert_model_allowed(self._policy.model)
        self._session.assert_http_admission()
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]
        if (type(messages) is not list or not messages
                or any(type(item) is not dict or item.get("role") not in {"system", "user", "assistant"}
                       or type(item.get("content")) is not str for item in messages)):
            _error()
        parameters = self._policy.client_params()
        body = {"model": parameters["model"].removeprefix(self._policy.provider.model_prefix),
                "messages": [{"role": item["role"], "content": item["content"]} for item in messages],
                "temperature": self.temperature, "max_tokens": self.max_tokens, "stream": False}
        if self.stop_sequences:
            body["stop"] = self.stop_sequences[:4]
        if response_model is not None:
            if not isinstance(response_model, type) or not issubclass(response_model, BaseModel):
                _error()
            body["response_format"] = {"type": "json_object"}
            body["messages"] = [{"role": "system", "content": "Return one JSON object matching: "
                                 + json.dumps(response_model.model_json_schema())}, *body["messages"]]
        extra = parameters.get("additional_params", {}).get("extra_body", {})
        body.update(extra)
        encoded = json.dumps(body, allow_nan=False, ensure_ascii=False).encode("utf-8")
        if len(encoded) > 262_144:
            _error()
        endpoint = parameters.get("base_url") or _ENDPOINTS.get(self._policy.provider.name)
        if not endpoint:
            _error()
        parsed = urlsplit(endpoint)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            _error()
        headers = {"Authorization": "Bearer " + self._policy.api_key,
                   "Content-Type": "application/json", "Accept-Encoding": "identity"}
        return endpoint.rstrip("/") + "/chat/completions", headers, encoded

    def _remaining(self, deadline_at=None):
        self._session.assert_http_admission()
        if deadline_at is None:
            return self._timeout
        remaining = deadline_at - time.monotonic()
        if remaining <= 0:
            raise httpx.ReadTimeout("Provider request deadline exceeded") from None
        return remaining

    def _headers(self, response):
        if not 200 <= response.status_code < 300:
            _error("Provider returned an unsuccessful HTTP status")
        if response.headers.get("content-encoding", "identity").strip().lower() not in {"", "identity"}:
            _error("Provider response encoding is unsupported")
        length = response.headers.get("content-length")
        if length is not None:
            try:
                if not 0 <= int(length) <= _MAX_BODY:
                    _error()
            except ValueError:
                _error()

    def call(self, messages, tools=None, callbacks=None, available_functions=None,
             from_task=None, from_agent=None, response_model=None):
        url, headers, body = self._parameters(messages, tools, available_functions, response_model)
        if not _NETWORK_SLOTS.acquire(blocking=False):
            _error()
        closed = True
        client = None
        deadline_timer = None
        deadline_expired = threading.Event()
        response_ref = {}
        attempt = None
        dispatch_authorized = False
        started = None
        recorded = False
        usage = None
        provider_request_id = None
        provider_generation_id = None
        content = None
        output_valid = None
        finish_latency_ms = None
        try:
            attempt = self._session.bridge(
                self._session.begin, self._policy.provider.name, self.model, self._reason,
            )
            body = _with_provider_correlation(body, attempt)
            self._session.assert_http_admission()
            started = time.monotonic()
            deadline_at = started + self._timeout
            client = httpx.Client(transport=self._sync_transport or httpx.HTTPTransport(
                trust_env=False, verify=True, retries=0), trust_env=False, follow_redirects=False,
                timeout=self._remaining(deadline_at))

            def abort_at_deadline():
                deadline_expired.set()
                response = response_ref.get("response")
                if response is not None:
                    try:
                        response.close()
                    except Exception:
                        pass
                try:
                    client.close()
                except Exception:
                    pass

            deadline_timer = threading.Timer(self._timeout, abort_at_deadline)
            deadline_timer.daemon = True
            deadline_timer.start()
            closed = False
            self._session.bridge(self._session.mark_dispatching, attempt)
            dispatch_authorized = True
            with client.stream("POST", url, headers=headers, content=body) as response:
                response_ref["response"] = response
                provider_request_id = _provider_request_id(headers=response.headers)
                provider_generation_id = _provider_generation_id(
                    headers=response.headers, provider=attempt.provider,
                )
                if provider_request_id is not None or provider_generation_id is not None:
                    self._session.bridge(
                        self._session.record_provider_ids, attempt,
                        generation_id=provider_generation_id,
                        provider_request_id=provider_request_id,
                    )
                self._remaining(deadline_at)
                self._headers(response)
                data = bytearray()
                for chunk in response.iter_raw():
                    self._remaining(deadline_at)
                    self._session.assert_http_admission()
                    if len(data) + len(chunk) > _MAX_BODY:
                        _error()
                    data.extend(chunk)
                self._remaining(deadline_at)
                self._session.assert_http_admission()
            deadline_timer.cancel()
            deadline_timer = None
            payload, usage = parse_raw_provider_response(bytes(data), self.model)
            provider_request_id = _provider_request_id(payload) or provider_request_id
            provider_generation_id = _provider_generation_id(
                payload, provider=attempt.provider,
            ) or provider_generation_id
            if provider_request_id is not None or provider_generation_id is not None:
                self._session.bridge(
                    self._session.record_provider_ids, attempt,
                    generation_id=provider_generation_id,
                    provider_request_id=provider_request_id,
                )
            try:
                content = _content(payload)
            except ProviderAccountingError:
                output_valid = False
                finish_latency_ms = int((time.monotonic() - started) * 1000)
                finalized = self._session.bridge(
                    self._session.finish, attempt, usage,
                    finish_latency_ms, output_valid=False,
                    provider_request_id=provider_request_id,
                    provider_generation_id=provider_generation_id,
                )
                if finalized is not True:
                    _error("Provider result is awaiting fenced recovery")
                recorded = True
                raise
            output_valid = True
            finish_latency_ms = int((time.monotonic() - started) * 1000)
            finalized = self._session.bridge(
                self._session.finish, attempt, usage, finish_latency_ms,
                provider_request_id=provider_request_id,
                provider_generation_id=provider_generation_id,
            )
            if finalized is not True:
                _error("Provider result is awaiting fenced recovery")
            recorded = True
            return content
        except Exception as exc:
            if attempt is not None and not recorded:
                elapsed = (finish_latency_ms if finish_latency_ms is not None else
                           (int((time.monotonic() - started) * 1000) if started is not None else 0))
                if usage is not None and output_valid is not None:
                    # Retrying the same finalizer is idempotent by attempt ID.
                    # If PostgreSQL remains unavailable the durable in_flight
                    # row stays unresolved; do not overwrite known metrics with
                    # an unmeasured generic failure.
                    try:
                        finalized = self._session.bridge(
                            self._session.finish, attempt, usage, elapsed,
                            output_valid=output_valid, provider_request_id=provider_request_id,
                            provider_generation_id=provider_generation_id,
                        )
                    except Exception:
                        pass
                    else:
                        if finalized is True:
                            recorded = True
                            if output_valid and content is not None:
                                return content
                else:
                    outcome = "invalid_response"
                    actual_model = None
                    usage_present = None
                    if deadline_expired.is_set() or isinstance(exc, httpx.TimeoutException):
                        outcome = "deadline_exceeded"
                    elif isinstance(exc, httpx.TransportError):
                        outcome = "transport_error"
                    elif isinstance(exc, ProviderAccountingError) and "HTTP" in str(exc):
                        outcome = "http_error"
                    if isinstance(exc, ProviderResponseVerificationError):
                        actual_model = exc.actual_model
                        usage_present = exc.usage_present
                        if usage_present is True:
                            outcome = "invalid_usage"
                    try:
                        if dispatch_authorized:
                            self._session.bridge(
                                self._session.fail, attempt, outcome, elapsed,
                                actual_model, usage_present, provider_request_id=provider_request_id,
                                provider_generation_id=provider_generation_id,
                            )
                        else:
                            failure_code = ("request_serialization_error"
                                            if isinstance(exc, ProviderAccountingError)
                                            else "local_setup_error")
                            self._session.bridge(
                                self._session.fail_before_dispatch, attempt,
                                failure_code, elapsed,
                            )
                    except Exception:
                        pass
            _error()
        finally:
            if deadline_timer is not None:
                deadline_timer.cancel()
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
                closed = True
            if closed:
                _NETWORK_SLOTS.release()

    async def acall(self, messages, tools=None, callbacks=None, available_functions=None,
                    from_task=None, from_agent=None, response_model=None):
        url, headers, body = self._parameters(messages, tools, available_functions, response_model)
        if not _NETWORK_SLOTS.acquire(blocking=False):
            _error()
        closed = True
        client = None
        attempt = None
        dispatch_authorized = False
        started = None
        recorded = False
        usage = None
        provider_request_id = None
        provider_generation_id = None
        content = None
        output_valid = None
        finish_latency_ms = None
        try:
            attempt = await self._session.begin(
                self._policy.provider.name, self.model, self._reason,
            )
            body = _with_provider_correlation(body, attempt)
            self._session.assert_http_admission()
            started = time.monotonic()
            client = create_bounded_http_client(max_response_bytes=_MAX_BODY, max_concurrency=1,
                        deadline_seconds=self._remaining(), transport=self._async_transport)
            closed = False
            await self._session.mark_dispatching(attempt)
            dispatch_authorized = True
            response = await client.post(url, headers=headers, content=body)
            provider_request_id = _provider_request_id(headers=response.headers)
            provider_generation_id = _provider_generation_id(
                headers=response.headers, provider=attempt.provider,
            )
            if provider_request_id is not None or provider_generation_id is not None:
                await self._session.record_provider_ids(
                    attempt, generation_id=provider_generation_id,
                    provider_request_id=provider_request_id,
                )
            self._headers(response)
            payload, usage = parse_raw_provider_response(response.content, self.model)
            provider_request_id = _provider_request_id(payload) or provider_request_id
            provider_generation_id = _provider_generation_id(
                payload, provider=attempt.provider,
            ) or provider_generation_id
            if provider_request_id is not None or provider_generation_id is not None:
                await self._session.record_provider_ids(
                    attempt, generation_id=provider_generation_id,
                    provider_request_id=provider_request_id,
                )
            try:
                content = _content(payload)
            except ProviderAccountingError:
                output_valid = False
                finish_latency_ms = int((time.monotonic() - started) * 1000)
                finalized = await self._session.finish(
                    attempt, usage, finish_latency_ms, output_valid=False,
                    provider_request_id=provider_request_id,
                    provider_generation_id=provider_generation_id,
                )
                if finalized is not True:
                    _error("Provider result is awaiting fenced recovery")
                recorded = True
                raise
            output_valid = True
            finish_latency_ms = int((time.monotonic() - started) * 1000)
            finalized = await self._session.finish(
                attempt, usage, finish_latency_ms, provider_request_id=provider_request_id,
                provider_generation_id=provider_generation_id,
            )
            if finalized is not True:
                _error("Provider result is awaiting fenced recovery")
            recorded = True
            return content
        except Exception as exc:
            if attempt is not None and not recorded:
                elapsed = (finish_latency_ms if finish_latency_ms is not None else
                           (int((time.monotonic() - started) * 1000) if started is not None else 0))
                if usage is not None and output_valid is not None:
                    try:
                        finalized = await self._session.finish(
                            attempt, usage, elapsed, output_valid=output_valid,
                            provider_request_id=provider_request_id,
                            provider_generation_id=provider_generation_id,
                        )
                    except Exception:
                        pass
                    else:
                        if finalized is True:
                            recorded = True
                            if output_valid and content is not None:
                                return content
                else:
                    outcome = "invalid_response"
                    actual_model = None
                    usage_present = None
                    if isinstance(exc, httpx.TimeoutException):
                        outcome = "deadline_exceeded"
                    elif isinstance(exc, httpx.TransportError):
                        outcome = "transport_error"
                    elif isinstance(exc, ProviderAccountingError) and "HTTP" in str(exc):
                        outcome = "http_error"
                    if isinstance(exc, ProviderResponseVerificationError):
                        actual_model = exc.actual_model
                        usage_present = exc.usage_present
                        if usage_present is True:
                            outcome = "invalid_usage"
                    try:
                        if dispatch_authorized:
                            await self._session.fail(
                                attempt, outcome, elapsed, actual_model, usage_present,
                                provider_request_id=provider_request_id,
                                provider_generation_id=provider_generation_id,
                            )
                        else:
                            failure_code = ("request_serialization_error"
                                            if isinstance(exc, ProviderAccountingError)
                                            else "local_setup_error")
                            await self._session.fail_before_dispatch(
                                attempt, failure_code, elapsed,
                            )
                    except Exception:
                        pass
            _error()
        except asyncio.CancelledError:
            if attempt is not None and not recorded:
                try:
                    elapsed = int((time.monotonic() - started) * 1000) if started is not None else 0
                    if dispatch_authorized:
                        await asyncio.shield(self._session.fail(
                            attempt, "cancelled", elapsed,
                            provider_request_id=provider_request_id,
                            provider_generation_id=provider_generation_id,
                        ))
                    else:
                        await asyncio.shield(self._session.fail_before_dispatch(
                            attempt, "local_setup_error", elapsed,
                        ))
                except Exception:
                    pass
            raise
        finally:
            if client is not None:
                try:
                    await client.aclose()
                except Exception:
                    pass
                closed = True
            if closed:
                _NETWORK_SLOTS.release()
