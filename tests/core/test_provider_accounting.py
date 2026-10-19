import asyncio
import json
import threading
import time
import uuid

import httpx
import pytest

from src.core.ai_providers import PROVIDERS
from src.core.llm_client_policy import LLMClientPolicy
from src.core.observed_llm import ObservedChatLLM, parse_raw_provider_response
from src.core.provider_accounting import (
    ProviderAccountingContext,
    ProviderAccountingError,
    RawProviderUsage,
)
from src.core.llm_routing import LLMRoute


@pytest.fixture(autouse=True)
def offline_provider_policy(monkeypatch):
    monkeypatch.setenv("LLM_COST_POLICY", "standard")


async def _tenant(pool, org_id, *, used=0, limit=150, status="trialing", blocked=False):
    user_id = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO auth.users(id,email) VALUES($1,$2)",
            user_id, f"{user_id}@example.test",
        )
        profile_id = await conn.fetchval(
            "SELECT id FROM user_profiles WHERE auth_user_id=$1", user_id,
        )
        await conn.execute(
            "INSERT INTO organization_memberships(organization_id,user_id,ruolo,joined_at) "
            "VALUES($1,$2,'owner',now())",
            org_id, profile_id,
        )
        await conn.execute(
            """UPDATE organizations SET subscription_status=$2, subscription_id=$3,
                      trial_start=now()-interval '1 hour', trial_end=now()+interval '2 days',
                      current_period_start=now()-interval '1 day',
                      current_period_end=now()+interval '29 days',
                      messages_limit=$4, messages_used_this_period=$5,
                      ai_accounting_blocked=$6
                 WHERE id=$1""",
            org_id, status, "sub_test" if status == "active" else None,
            limit, used, blocked,
        )
    return user_id


async def _manager(pool, org_id):
    user_id = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO auth.users(id,email) VALUES($1,$2)",
            user_id, f"{user_id}@example.test",
        )
        profile_id = await conn.fetchval(
            "SELECT id FROM user_profiles WHERE auth_user_id=$1", user_id,
        )
        await conn.execute(
            "INSERT INTO organization_memberships(organization_id,user_id,ruolo,joined_at) "
            "VALUES($1,$2,'manager',now())",
            org_id, profile_id,
        )
    return user_id


async def _events(pool, org_id):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id,event_type,quantity,metadata FROM usage_events "
            "WHERE organization_id=$1 AND event_type='system.provider_attempt' "
            "ORDER BY created_at,id",
            org_id,
        )
    result = []
    for row in rows:
        value = dict(row)
        if isinstance(value.get("metadata"), str):
            value["metadata"] = json.loads(value["metadata"])
        result.append(value)
    return result


async def _record_provider_request_id(pool, org_id, attempt_id, value="chatcmpl_test_fixture"):
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE usage_events
                  SET metadata=jsonb_set(metadata,'{provider_request_id}',to_jsonb($3::text),true)
                WHERE organization_id=$1 AND id=$2""",
            org_id, attempt_id, value,
        )


async def _inbound_message(pool, org_id, *, phone_suffix="00000000"):
    contact_id, conversation_id, message_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO contacts(id,organization_id,phone_number) VALUES($1,$2,$3)",
            contact_id, org_id, f"+39{phone_suffix}",
        )
        await conn.execute(
            "INSERT INTO conversations(id,organization_id,contact_id) VALUES($1,$2,$3)",
            conversation_id, org_id, contact_id,
        )
        await conn.execute(
            """INSERT INTO messages(id,organization_id,conversation_id,direction,message_type,
                       content,status,billed_at,processing_at)
                 VALUES($1,$2,$3,'inbound','text','{"text":"test sintetico"}'::jsonb,
                        'processing',now(),now())""",
            message_id, org_id, conversation_id,
        )
    return message_id


def _response(model="llama-3.1-8b-instant", *, prompt=0, completion=0, total=0):
    return {
        "id": "chatcmpl_test_fixture",
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "Risposta test."},
                      "finish_reason": "stop"}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total},
    }


def _stream_response(payload, status_code=200):
    body = json.dumps(payload).encode()
    return httpx.Response(
        status_code,
        stream=httpx.ByteStream(body),
        headers={"content-type": "application/json", "content-length": str(len(body))},
    )


def _policy():
    return LLMClientPolicy(
        provider=PROVIDERS["groq"],
        model="groq/llama-3.1-8b-instant",
        api_key="offline-test-only",
        base_url=None,
    )


def test_raw_response_preserves_explicit_zero_and_rejects_unknown_usage():
    payload, usage = parse_raw_provider_response(
        json.dumps(_response()).encode(), "groq/llama-3.1-8b-instant",
    )
    assert payload["model"] == "llama-3.1-8b-instant"
    assert usage == RawProviderUsage("llama-3.1-8b-instant", 0, 0, 0)

    for body in (
        b'{"model":"llama-3.1-8b-instant","usage":{},"choices":[]}',
        b'{"model":"llama-3.1-8b-instant","usage":{"prompt_tokens":1,"completion_tokens":2},"choices":[]}',
        b'{"model":"llama-3.1-8b-instant","model":"other","usage":{},"choices":[]}',
        json.dumps(_response(model="other-model")).encode(),
    ):
        with pytest.raises(ProviderAccountingError):
            parse_raw_provider_response(body, "groq/llama-3.1-8b-instant")

    with pytest.raises(ProviderAccountingError) as missing_usage:
        parse_raw_provider_response(
            b'{"model":"llama-3.1-8b-instant","choices":[]}',
            "groq/llama-3.1-8b-instant",
        )
    assert missing_usage.value.actual_model == "llama-3.1-8b-instant"
    assert missing_usage.value.usage_present is False

    with pytest.raises(ProviderAccountingError) as malformed_usage:
        parse_raw_provider_response(
            b'{"model":"llama-3.1-8b-instant","usage":{},"choices":[]}',
            "groq/llama-3.1-8b-instant",
        )
    assert malformed_usage.value.actual_model == "llama-3.1-8b-instant"
    assert malformed_usage.value.usage_present is True


def test_factory_refuses_to_construct_a_provider_client_without_context():
    from src.core.llm_config import crea_llm

    with pytest.raises(ProviderAccountingError):
        crea_llm(model="groq/llama-3.1-8b-instant")


@pytest.mark.parametrize("provider", ["groq", "openrouter"])
def test_documented_provider_user_field_correlates_attempt_without_changing_prompt(provider):
    from types import SimpleNamespace
    from src.core.observed_llm import _provider_request_id, _with_provider_correlation

    attempt_id = uuid.uuid4()
    body = json.dumps({"messages": [{"role": "user", "content": "test sintetico"}]}).encode()
    correlated = json.loads(_with_provider_correlation(
        body, SimpleNamespace(provider=provider, attempt_id=attempt_id),
    ))
    assert correlated["user"] == str(attempt_id)
    assert correlated["messages"] == [{"role": "user", "content": "test sintetico"}]
    assert _provider_request_id({"id": "chatcmpl_test_fixture"}) == "chatcmpl_test_fixture"
    assert _provider_request_id(headers={"x-request-id": "req_headerfixture"}) == "req_headerfixture"


def test_undocumented_provider_does_not_receive_user_correlation_field():
    from types import SimpleNamespace
    from src.core.observed_llm import _with_provider_correlation

    body = b'{"messages":[]}'
    assert _with_provider_correlation(
        body, SimpleNamespace(provider="mistral", attempt_id=uuid.uuid4()),
    ) == body


@pytest.mark.asyncio
async def test_postgres_records_real_provider_response_before_returning(pg_pool, sample_org):
    pool = pg_pool
    org_id = sample_org["id"]
    actor = await _tenant(pool, org_id)
    context = await ProviderAccountingContext.open(
        pool, org_id, task_type="document_qa", actor_auth_user_id=actor,
        context={"trace_id": str(uuid.uuid4())},
    )
    loop = asyncio.get_running_loop()
    dispatch_observed = asyncio.Event()

    def handler(request):
        async def check_pre_dispatch_row():
            rows = await _events(pool, org_id)
            assert len(rows) == 1
            assert rows[0]["event_type"] == "system.provider_attempt"
            assert rows[0]["metadata"]["outcome"] == "in_flight"
            assert rows[0]["metadata"]["accounting_status"] == "unresolved"
            assert rows[0]["metadata"]["client_correlation_id"] == rows[0]["metadata"]["attempt_id"]
            assert rows[0]["metadata"]["correlation_contract"] == "openai_user"
            dispatch_observed.set()

        asyncio.run_coroutine_threadsafe(check_pre_dispatch_row(), loop).result(timeout=5)
        assert request.url.path.endswith("/chat/completions")
        rows = asyncio.run_coroutine_threadsafe(_events(pool, org_id), loop).result(timeout=5)
        assert json.loads(request.content)["user"] == rows[0]["metadata"]["attempt_id"]
        return _stream_response(_response(prompt=12, completion=7, total=19))

    llm = ObservedChatLLM(policy=_policy(), session=context,
                          sync_transport=httpx.MockTransport(handler))
    try:
        result = await asyncio.to_thread(llm.call, "domanda sintetica")
        assert result == "Risposta test."
        assert dispatch_observed.is_set()
        rows = await _events(pool, org_id)
        assert len(rows) == 1
        metadata = rows[0]["metadata"]
        assert metadata["provider"] == "groq"
        assert metadata["requested_model"] == "groq/llama-3.1-8b-instant"
        assert metadata["actual_model"] == "llama-3.1-8b-instant"
        assert metadata["prompt_tokens"] == 12
        assert metadata["completion_tokens"] == 7
        assert metadata["total_tokens"] == 19
        assert isinstance(metadata["latency_ms"], int)
        assert metadata["estimated_cost_eur"] == 0.0
        assert metadata["accounting_status"] == "recorded"
        assert metadata["outcome"] == "response_received"
        assert metadata["provider_request_id"] == "chatcmpl_test_fixture"
    finally:
        await context.close()


@pytest.mark.asyncio
async def test_valid_usage_with_invalid_content_is_measured_as_rejected_output(pg_pool, sample_org):
    org_id = sample_org["id"]
    actor = await _tenant(pg_pool, org_id)
    context = await ProviderAccountingContext.open(
        pg_pool, org_id, task_type="report", actor_auth_user_id=actor,
    )
    llm = ObservedChatLLM(
        policy=_policy(), session=context,
        sync_transport=httpx.MockTransport(
            lambda request: _stream_response({
                "model": "llama-3.1-8b-instant",
                "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
                "choices": [],
            })
        ),
    )
    try:
        with pytest.raises(ProviderAccountingError):
            await asyncio.to_thread(llm.call, "test")
        rows = await _events(pg_pool, org_id)
        assert len(rows) == 1
        metadata = rows[0]["metadata"]
        assert metadata["outcome"] == "invalid_response"
        assert metadata["application_outcome"] == "invalid_content"
        assert metadata["accounting_status"] == "recorded"
        assert metadata["actual_model"] == "llama-3.1-8b-instant"
        assert (metadata["prompt_tokens"], metadata["completion_tokens"],
                metadata["total_tokens"]) == (5, 2, 7)
        assert type(metadata["latency_ms"]) is int
        assert metadata["failure_code"] == "invalid_content"
    finally:
        await context.close()


@pytest.mark.asyncio
async def test_transient_finish_failure_retries_same_attempt_with_verified_usage(pg_pool, sample_org):
    org_id = sample_org["id"]
    actor = await _tenant(pg_pool, org_id)
    context = await ProviderAccountingContext.open(
        pg_pool, org_id, task_type="report", actor_auth_user_id=actor,
    )
    original_finish = context.finish
    finish_calls = 0

    async def finish_once_fails(attempt, usage, latency_ms, *, output_valid=True,
                                provider_request_id=None):
        nonlocal finish_calls
        finish_calls += 1
        if finish_calls == 1:
            raise RuntimeError("synthetic accounting write failure")
        return await original_finish(
            attempt, usage, latency_ms, output_valid=output_valid,
            provider_request_id=provider_request_id,
        )

    context.finish = finish_once_fails
    llm = ObservedChatLLM(
        policy=_policy(), session=context,
        sync_transport=httpx.MockTransport(
            lambda request: _stream_response(_response(prompt=6, completion=3, total=9))
        ),
    )
    try:
        assert await asyncio.to_thread(llm.call, "test retry contabilita'") == "Risposta test."
        assert finish_calls == 2
        rows = await _events(pg_pool, org_id)
        assert len(rows) == 1
        metadata = rows[0]["metadata"]
        assert metadata["accounting_status"] == "recorded"
        assert metadata["outcome"] == "response_received"
        assert metadata["actual_model"] == "llama-3.1-8b-instant"
        assert (metadata["prompt_tokens"], metadata["completion_tokens"],
                metadata["total_tokens"]) == (6, 3, 9)
        assert type(metadata["latency_ms"]) is int
    finally:
        await context.close()


@pytest.mark.asyncio
async def test_finish_commit_with_lost_ack_retries_idempotently(pg_pool, sample_org):
    org_id = sample_org["id"]
    actor = await _tenant(pg_pool, org_id)
    context = await ProviderAccountingContext.open(
        pg_pool, org_id, task_type="report", actor_auth_user_id=actor,
    )
    original_finish = context.finish
    finish_calls = 0
    committed_latency = None

    async def commit_then_lose_ack(attempt, usage, latency_ms, *, output_valid=True,
                                   provider_request_id=None):
        nonlocal finish_calls, committed_latency
        finish_calls += 1
        result = await original_finish(
            attempt, usage, latency_ms, output_valid=output_valid,
            provider_request_id=provider_request_id,
        )
        if finish_calls == 1:
            committed_latency = latency_ms
            await asyncio.sleep(0.02)
            raise RuntimeError("synthetic lost acknowledgement after commit")
        return result

    context.finish = commit_then_lose_ack
    llm = ObservedChatLLM(
        policy=_policy(), session=context,
        sync_transport=httpx.MockTransport(
            lambda request: _stream_response(_response(prompt=6, completion=3, total=9))
        ),
    )
    try:
        assert await asyncio.to_thread(llm.call, "test commit then lost ack") == "Risposta test."
        assert finish_calls == 2
        rows = await _events(pg_pool, org_id)
        assert len(rows) == 1
        metadata = rows[0]["metadata"]
        assert metadata["accounting_status"] == "recorded"
        assert metadata["outcome"] == "response_received"
        assert metadata["latency_ms"] == committed_latency
        assert (metadata["prompt_tokens"], metadata["completion_tokens"],
                metadata["total_tokens"]) == (6, 3, 9)
    finally:
        await context.close()


@pytest.mark.asyncio
async def test_sync_transport_enforces_total_deadline_for_trickling_response(pg_pool, sample_org):
    org_id = sample_org["id"]
    actor = await _tenant(pg_pool, org_id)
    context = await ProviderAccountingContext.open(
        pg_pool, org_id, task_type="report", actor_auth_user_id=actor,
    )
    body = json.dumps(_response(prompt=4, completion=3, total=7)).encode()

    class TrickleStream(httpx.SyncByteStream):
        def __iter__(self):
            yield body[:20]
            time.sleep(0.08)
            yield body[20:40]
            time.sleep(0.08)
            yield body[40:]

        def close(self):
            return None

    def handler(request):
        return httpx.Response(
            200, stream=TrickleStream(),
            headers={"content-type": "application/json", "content-length": str(len(body))},
        )

    llm = ObservedChatLLM(
        policy=_policy(), session=context, timeout=0.12,
        sync_transport=httpx.MockTransport(handler),
    )
    try:
        started = time.monotonic()
        with pytest.raises(ProviderAccountingError):
            await asyncio.to_thread(llm.call, "test deadline")
        assert time.monotonic() - started < 1.5
        rows = await _events(pg_pool, org_id)
        assert len(rows) == 1
        metadata = rows[0]["metadata"]
        assert metadata["outcome"] == "deadline_exceeded"
        assert metadata["accounting_status"] == "unresolved"
        assert isinstance(metadata["latency_ms"], int)
        assert metadata["failure_code"] == "deadline_exceeded"
    finally:
        await context.close()


@pytest.mark.asyncio
async def test_sync_transport_deadline_closes_a_blocked_read_without_next_chunk(
    pg_pool, sample_org,
):
    import threading

    org_id = sample_org["id"]
    actor = await _tenant(pg_pool, org_id)
    context = await ProviderAccountingContext.open(
        pg_pool, org_id, task_type="report", actor_auth_user_id=actor,
    )
    read_started, stream_closed = threading.Event(), threading.Event()

    class WaitingStream(httpx.SyncByteStream):
        def __iter__(self):
            read_started.set()
            stream_closed.wait(timeout=3)
            return
            yield b"unreachable"

        def close(self):
            stream_closed.set()

    llm = ObservedChatLLM(
        policy=_policy(), session=context, timeout=0.15,
        sync_transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, stream=WaitingStream(), headers={"content-type": "application/json"},
            )
        ),
    )
    try:
        started = time.monotonic()
        with pytest.raises(ProviderAccountingError):
            await asyncio.to_thread(llm.call, "test deadline stream bloccato")
        elapsed = time.monotonic() - started
        assert read_started.is_set()
        assert stream_closed.is_set()
        assert elapsed < 1.0
        rows = await _events(pg_pool, org_id)
        assert len(rows) == 1
        assert rows[0]["metadata"]["outcome"] == "deadline_exceeded"
        assert rows[0]["metadata"]["accounting_status"] == "unresolved"
    finally:
        await context.close()


@pytest.mark.asyncio
async def test_async_adapter_records_provider_usage_from_raw_response(pg_pool, sample_org):
    org_id = sample_org["id"]
    actor = await _tenant(pg_pool, org_id)
    context = await ProviderAccountingContext.open(
        pg_pool, org_id, task_type="document_qa", actor_auth_user_id=actor,
    )
    llm = ObservedChatLLM(
        policy=_policy(), session=context,
        async_transport=httpx.MockTransport(
            lambda request: _stream_response(_response(prompt=4, completion=3, total=7))
        ),
    )
    try:
        assert await llm.acall("domanda sintetica") == "Risposta test."
        rows = await _events(pg_pool, org_id)
        assert len(rows) == 1
        metadata = rows[0]["metadata"]
        assert metadata["actual_model"] == "llama-3.1-8b-instant"
        assert (metadata["prompt_tokens"], metadata["completion_tokens"],
                metadata["total_tokens"]) == (4, 3, 7)
        assert metadata["accounting_status"] == "recorded"
    finally:
        await context.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [429, 500, 503])
async def test_failure_is_durable_and_blocks_the_next_generation(pg_pool, sample_org, status_code):
    org_id = sample_org["id"]
    actor = await _tenant(pg_pool, org_id)
    context = await ProviderAccountingContext.open(
        pg_pool, org_id, task_type="review", actor_auth_user_id=actor,
    )
    llm = ObservedChatLLM(
        policy=_policy(), session=context,
        sync_transport=httpx.MockTransport(
            lambda request: httpx.Response(status_code, text="private provider body")
        ),
    )
    try:
        with pytest.raises(ProviderAccountingError):
            await asyncio.to_thread(llm.call, "review text")
        rows = await _events(pg_pool, org_id)
        assert len(rows) == 1
        assert rows[0]["metadata"]["outcome"] == "http_error"
        assert rows[0]["metadata"]["failure_code"] == "http_error"
        assert rows[0]["metadata"]["accounting_status"] == "unresolved"
        assert rows[0]["metadata"]["actual_model"] is None
        assert rows[0]["metadata"]["prompt_tokens"] is None
        assert rows[0]["metadata"]["completion_tokens"] is None
        assert rows[0]["metadata"]["total_tokens"] is None
        assert rows[0]["metadata"]["estimated_cost_eur"] is None
        assert "private provider body" not in json.dumps(rows[0]["metadata"])
        async with pg_pool.acquire() as conn:
            assert await conn.fetchval(
                "SELECT ai_accounting_blocked FROM organizations WHERE id=$1", org_id,
            ) is True
        with pytest.raises(ProviderAccountingError):
            await ProviderAccountingContext.open(
                pg_pool, org_id, task_type="report", actor_auth_user_id=actor,
            )
    finally:
        await context.close()


@pytest.mark.asyncio
async def test_invalid_usage_keeps_only_verified_model_and_usage_presence(pg_pool, sample_org):
    org_id = sample_org["id"]
    actor = await _tenant(pg_pool, org_id)
    context = await ProviderAccountingContext.open(
        pg_pool, org_id, task_type="document_qa", actor_auth_user_id=actor,
    )
    malformed = _response(prompt=12, completion=7, total=19)
    malformed["usage"] = {"prompt_tokens": 12, "completion_tokens": 7}
    llm = ObservedChatLLM(
        policy=_policy(), session=context,
        sync_transport=httpx.MockTransport(lambda request: _stream_response(malformed)),
    )
    try:
        with pytest.raises(ProviderAccountingError):
            await asyncio.to_thread(llm.call, "domanda sintetica")
        rows = await _events(pg_pool, org_id)
        assert len(rows) == 1
        metadata = rows[0]["metadata"]
        assert metadata["outcome"] == "invalid_usage"
        assert metadata["actual_model"] == "llama-3.1-8b-instant"
        assert metadata["usage_present"] is True
        assert metadata["prompt_tokens"] is None
        assert metadata["completion_tokens"] is None
        assert metadata["total_tokens"] is None
        assert metadata["estimated_cost_eur"] is None
        assert metadata["accounting_status"] == "unresolved"
    finally:
        await context.close()


@pytest.mark.asyncio
async def test_runner_fallback_is_metered_and_invalid_usage_stops_second_dispatch(
    pg_pool, sample_org, monkeypatch,
):
    from src.core import crew_runner
    from src.models.schemas import MessaggioInput, ProfiloAttivita, RispostaOutput
    from types import SimpleNamespace

    org_id = sample_org["id"]
    actor = await _tenant(pg_pool, org_id)
    route = LLMRoute(
        model="groq/llama-3.1-8b-instant", tier="cheap", reason="test",
        fallback_models=("groq/openai/gpt-oss-120b",),
    )
    monkeypatch.setattr(crew_runner, "route_llm", lambda _request: route)
    context = await ProviderAccountingContext.open(
        pg_pool, org_id, task_type="customer_message", actor_auth_user_id=actor,
    )
    dispatched = []

    def build_crew(*args, model, provider_context, reason, **kwargs):
        actual_model = ("llama-3.1-8b-instant" if model == route.model
                        else "openai/gpt-oss-120b")
        response = _response(model=actual_model, prompt=4, completion=2, total=6)
        llm = ObservedChatLLM(
            policy=LLMClientPolicy(
                provider=PROVIDERS["groq"], model=model, api_key="offline-test-only",
                base_url=None,
            ),
            session=provider_context,
            reason=reason,
            sync_transport=httpx.MockTransport(
                lambda request, payload=response: (dispatched.append(payload["model"]),
                                                    _stream_response(payload))[1]
            ),
        )

        class Crew:
            def kickoff(self):
                llm.call("rispondi")
                parsed = (None if model == route.model else RispostaOutput.model_construct(
                    risposta="Risposta fallback", richiede_umano=False, motivo="",
                ))
                return SimpleNamespace(pydantic=parsed)

        return Crew()

    monkeypatch.setattr(crew_runner, "crea_crew", build_crew)
    try:
        output = await asyncio.to_thread(
            crew_runner.genera_risposta,
            MessaggioInput(testo="domanda"),
            ProfiloAttivita(nome="Test", tipo_attivita="test", tono="neutro", orari=""),
            provider_context=context,
        )
        assert output.risposta == "Risposta fallback"
        rows = await _events(pg_pool, org_id)
        assert dispatched == ["llama-3.1-8b-instant", "openai/gpt-oss-120b"]
        assert len(rows) == 2
        assert rows[0]["id"] != rows[1]["id"]
        assert all(row["metadata"]["accounting_status"] == "recorded" for row in rows)
        attempts_by_reason = {row["metadata"]["reason"]: row["metadata"] for row in rows}
        assert set(attempts_by_reason) == {"test", "fallback:test"}
        assert attempts_by_reason["test"]["actual_model"] == "llama-3.1-8b-instant"
        assert attempts_by_reason["fallback:test"]["actual_model"] == "openai/gpt-oss-120b"
        for metadata in attempts_by_reason.values():
            assert (metadata["prompt_tokens"], metadata["completion_tokens"],
                    metadata["total_tokens"]) == (4, 2, 6)
            assert type(metadata["latency_ms"]) is int
    finally:
        await context.close()

    context = await ProviderAccountingContext.open(
        pg_pool, org_id, task_type="customer_message", actor_auth_user_id=actor,
    )
    async with pg_pool.acquire() as conn:
        await conn.execute("DELETE FROM usage_events WHERE organization_id=$1", org_id)
        await conn.execute("UPDATE organizations SET ai_accounting_blocked=FALSE WHERE id=$1", org_id)
    dispatched.clear()

    def build_failing_crew(*args, model, provider_context, reason, **kwargs):
        actual_model = ("llama-3.1-8b-instant" if model == route.model
                        else "openai/gpt-oss-120b")
        payload = {"model": actual_model, "usage": {"prompt_tokens": 4},
                   "choices": [{"message": {"content": "non usare"}}]}
        llm = ObservedChatLLM(
            policy=LLMClientPolicy(
                provider=PROVIDERS["groq"], model=model, api_key="offline-test-only",
                base_url=None,
            ),
            session=provider_context,
            reason=reason,
            sync_transport=httpx.MockTransport(
                lambda request, response=payload: (dispatched.append(response["model"]),
                                                    _stream_response(response))[1]
            ),
        )

        class Crew:
            def kickoff(self):
                return SimpleNamespace(pydantic=llm.call("rispondi"))

        return Crew()

    monkeypatch.setattr(crew_runner, "crea_crew", build_failing_crew)
    try:
        with pytest.raises(RuntimeError):
            await asyncio.to_thread(
                crew_runner.genera_risposta,
                MessaggioInput(testo="domanda"),
                ProfiloAttivita(nome="Test", tipo_attivita="test", tono="neutro", orari=""),
                provider_context=context,
            )
        rows = await _events(pg_pool, org_id)
        assert dispatched == ["llama-3.1-8b-instant"]
        assert len(rows) == 1
        assert rows[0]["metadata"]["accounting_status"] == "unresolved"
        assert rows[0]["metadata"]["actual_model"] == "llama-3.1-8b-instant"
        assert rows[0]["metadata"]["usage_present"] is True
    finally:
        await context.close()


@pytest.mark.asyncio
async def test_retry_attempts_are_distinct_and_terminal_updates_are_idempotent(
    pg_pool, sample_org,
):
    org_id = sample_org["id"]
    actor = await _tenant(pg_pool, org_id)
    context = await ProviderAccountingContext.open(
        pg_pool, org_id, task_type="customer_message", actor_auth_user_id=actor,
    )
    try:
        primary = await context.begin(
            "groq", "groq/llama-3.1-8b-instant", "primary_model",
        )
        first_usage = RawProviderUsage("llama-3.1-8b-instant", 5, 2, 7)
        await context.finish(primary, first_usage, 42)
        await context.finish(primary, first_usage, 42)

        fallback = await context.begin(
            "groq", "groq/openai/gpt-oss-20b", "fallback_model",
        )
        await context.finish(
            fallback, RawProviderUsage("openai/gpt-oss-20b", 8, 3, 11), 55,
        )

        rows = await _events(pg_pool, org_id)
        assert len(rows) == 2
        assert rows[0]["id"] != rows[1]["id"]
        assert rows[0]["metadata"]["execution_id"] == rows[1]["metadata"]["execution_id"]
        assert rows[0]["metadata"]["requested_model"] == "groq/llama-3.1-8b-instant"
        assert rows[0]["metadata"]["actual_model"] == "llama-3.1-8b-instant"
        assert rows[0]["metadata"]["reason"] == "primary_model"
        assert rows[1]["metadata"]["requested_model"] == "groq/openai/gpt-oss-20b"
        assert rows[1]["metadata"]["actual_model"] == "openai/gpt-oss-20b"
        assert rows[1]["metadata"]["reason"] == "fallback_model"
        assert all(row["metadata"]["accounting_status"] == "recorded" for row in rows)
    finally:
        await context.close()


@pytest.mark.asyncio
async def test_context_denies_expired_trial_and_accounting_hold(pg_pool, sample_org):
    org_id = sample_org["id"]
    actor = await _tenant(pg_pool, org_id)
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "UPDATE organizations SET subscription_status='trialing', "
            "trial_end=now()-interval '1 second' WHERE id=$1", org_id,
        )
    with pytest.raises(ProviderAccountingError):
        await ProviderAccountingContext.open(
            pg_pool, org_id, task_type="report", actor_auth_user_id=actor,
        )
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "UPDATE organizations SET subscription_status='active', subscription_id='sub_test', "
            "ai_accounting_blocked=TRUE WHERE id=$1", org_id,
        )
    with pytest.raises(ProviderAccountingError):
        await ProviderAccountingContext.open(
            pg_pool, org_id, task_type="report", actor_auth_user_id=actor,
        )


@pytest.mark.asyncio
async def test_past_due_billing_denies_new_provider_dispatch(pg_pool, sample_org):
    org_id = sample_org["id"]
    actor = await _tenant(pg_pool, org_id, status="active")
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "UPDATE organizations SET subscription_status='past_due' WHERE id=$1", org_id,
        )
    with pytest.raises(ProviderAccountingError):
        await ProviderAccountingContext.open(
            pg_pool, org_id, task_type="report", actor_auth_user_id=actor,
        )


@pytest.mark.asyncio
async def test_context_denies_cross_tenant_membership_and_invalid_entitlements(pg_pool, sample_org, other_org):
    actor = await _tenant(pg_pool, sample_org["id"])
    await _tenant(pg_pool, other_org["id"])
    with pytest.raises(ProviderAccountingError):
        await ProviderAccountingContext.open(
            pg_pool, other_org["id"], task_type="report", actor_auth_user_id=actor,
        )

    async with pg_pool.acquire() as conn:
        await conn.execute(
            "UPDATE organizations SET subscription_status='canceled' WHERE id=$1", sample_org["id"],
        )
    with pytest.raises(ProviderAccountingError):
        await ProviderAccountingContext.open(
            pg_pool, sample_org["id"], task_type="report", actor_auth_user_id=actor,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"subscription_id": None},
        {"current_period_start": None},
        {"current_period_end": None},
        {"current_period_end": "past"},
        {"current_period_start": "future"},
    ],
)
async def test_active_entitlement_requires_subscription_and_current_period(
    pg_pool, sample_org, changes,
):
    org_id = sample_org["id"]
    actor = await _tenant(pg_pool, org_id, status="active")
    assignments = []
    values = [org_id]
    for column, value in changes.items():
        if value == "past":
            assignments.append(f"{column}=now()-interval '1 day'")
        elif value == "future":
            assignments.append(f"{column}=now()+interval '1 day'")
        else:
            assignments.append(f"{column}=${len(values) + 1}")
            values.append(value)
    async with pg_pool.acquire() as conn:
        await conn.execute(f"UPDATE organizations SET {', '.join(assignments)} WHERE id=$1", *values)
    with pytest.raises(ProviderAccountingError):
        await ProviderAccountingContext.open(
            pg_pool, org_id, task_type="report", actor_auth_user_id=actor,
        )


@pytest.mark.asyncio
async def test_trial_entitlement_requires_started_trial_window(pg_pool, sample_org):
    org_id = sample_org["id"]
    actor = await _tenant(pg_pool, org_id)
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "UPDATE organizations SET trial_start=now()+interval '1 second' WHERE id=$1", org_id,
        )
    with pytest.raises(ProviderAccountingError):
        await ProviderAccountingContext.open(
            pg_pool, org_id, task_type="report", actor_auth_user_id=actor,
        )


@pytest.mark.asyncio
async def test_context_denies_disabled_membership_and_exhausted_quota(pg_pool, sample_org):
    org_id = sample_org["id"]
    actor = await _tenant(pg_pool, org_id, used=150, limit=150)
    with pytest.raises(ProviderAccountingError):
        await ProviderAccountingContext.open(
            pg_pool, org_id, task_type="report", actor_auth_user_id=actor,
        )
    async with pg_pool.acquire() as conn:
        await conn.execute("UPDATE organizations SET messages_used_this_period=0 WHERE id=$1", org_id)
        await conn.execute(
            "UPDATE auth_access_lifecycle SET disabled_at=now() WHERE auth_user_id=$1", actor,
        )
    with pytest.raises(ProviderAccountingError):
        await ProviderAccountingContext.open(
            pg_pool, org_id, task_type="report", actor_auth_user_id=actor,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing_billing", "zero_quota"])
async def test_context_denies_missing_billing_state_and_zero_quota(pg_pool, sample_org, case):
    org_id = sample_org["id"]
    actor = await _tenant(pg_pool, org_id)
    async with pg_pool.acquire() as conn:
        if case == "missing_billing":
            await conn.execute(
                "UPDATE organizations SET subscription_status='incomplete' WHERE id=$1", org_id,
            )
        else:
            await conn.execute(
                "UPDATE organizations SET messages_limit=0 WHERE id=$1", org_id,
            )
    with pytest.raises(ProviderAccountingError):
        await ProviderAccountingContext.open(
            pg_pool, org_id, task_type="report", actor_auth_user_id=actor,
        )


@pytest.mark.asyncio
async def test_claimed_billed_inbound_message_allows_last_quota_unit_without_a04_reservation(
    pg_pool, sample_org,
):
    org_id = sample_org["id"]
    await _tenant(pg_pool, org_id, used=150, limit=150)
    contact_id, conversation_id, message_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO contacts(id,organization_id,phone_number) VALUES($1,$2,'+39000000000')",
            contact_id, org_id,
        )
        await conn.execute(
            "INSERT INTO conversations(id,organization_id,contact_id) VALUES($1,$2,$3)",
            conversation_id, org_id, contact_id,
        )
        await conn.execute(
            """INSERT INTO messages(id,organization_id,conversation_id,direction,message_type,
                       content,status,billed_at,processing_at)
                 VALUES($1,$2,$3,'inbound','text','{"text":"test"}'::jsonb,'processing',now(),now())""",
            message_id, org_id, conversation_id,
        )
    context = await ProviderAccountingContext.open(
        pg_pool, org_id, task_type="customer_message", worker_message_id=message_id,
    )
    try:
        assert context.billing_snapshot["messages_used_this_period"] == 149
        async with pg_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT messages_used_this_period FROM organizations WHERE id=$1", org_id,
            )
            reservations = await conn.fetchval(
                "SELECT count(*) FROM usage_events WHERE organization_id=$1 AND event_type='system.quota_reservation'",
                org_id,
            )
        assert count == 150
        assert reservations == 0
    finally:
        await context.close()


@pytest.mark.asyncio
async def test_authenticated_simulator_claim_allows_last_quota_unit_without_a04_reservation(
    pg_pool, sample_org,
):
    from src.core.db.repositories.message_repo import MessageRepository

    org_id = sample_org["id"]
    actor = await _tenant(pg_pool, org_id, used=149, limit=150)
    request_id = uuid.uuid4()
    payload_hash = "a" * 64
    repository = MessageRepository(pg_pool)
    reservation = await repository.reserve_simulation_request(
        org_id, actor, request_id, payload_hash,
    )
    assert reservation["status"] == "reserved"
    claim_token = reservation["claim_token"]

    context = await ProviderAccountingContext.open(
        pg_pool, org_id, task_type="simulator", actor_auth_user_id=actor,
        simulation_request_id=request_id, simulation_claim_token=claim_token,
        context={"trace_id": str(uuid.uuid4())},
    )
    loop = asyncio.get_running_loop()
    dispatch_observed = asyncio.Event()

    def handler(request):
        async def verify_durable_admission():
            attempts = await _events(pg_pool, org_id)
            assert len(attempts) == 1
            assert attempts[0]["metadata"]["outcome"] == "in_flight"
            assert attempts[0]["metadata"]["accounting_status"] == "unresolved"
            dispatch_observed.set()

        asyncio.run_coroutine_threadsafe(verify_durable_admission(), loop).result(timeout=5)
        return _stream_response(_response(prompt=9, completion=4, total=13))

    llm = ObservedChatLLM(
        policy=_policy(), session=context, sync_transport=httpx.MockTransport(handler),
    )
    try:
        assert context.billing_snapshot["messages_used_this_period"] == 149
        assert await asyncio.to_thread(llm.call, "test simulatore") == "Risposta test."
        assert dispatch_observed.is_set()
        attempts = await _events(pg_pool, org_id)
        assert len(attempts) == 1
        metadata = attempts[0]["metadata"]
        assert metadata["task_type"] == "simulator"
        assert metadata["context"]["simulation_request_id"] == str(request_id)
        assert metadata["actual_model"] == "llama-3.1-8b-instant"
        assert metadata["prompt_tokens"] == 9
        assert metadata["completion_tokens"] == 4
        assert metadata["total_tokens"] == 13
        assert str(claim_token) not in json.dumps(metadata)
        async with pg_pool.acquire() as conn:
            usage = await conn.fetchval(
                "SELECT messages_used_this_period FROM organizations WHERE id=$1", org_id,
            )
            a04_reservations = await conn.fetchval(
                "SELECT count(*) FROM usage_events WHERE organization_id=$1 "
                "AND event_type='system.quota_reservation'", org_id,
            )
        assert usage == 150
        assert a04_reservations == 0
    finally:
        await context.close()


@pytest.mark.asyncio
async def test_simulator_provider_gate_rejects_missing_or_stale_claim(pg_pool, sample_org):
    from src.core.db.repositories.message_repo import MessageRepository

    org_id = sample_org["id"]
    actor = await _tenant(pg_pool, org_id)
    request_id = uuid.uuid4()
    reservation = await MessageRepository(pg_pool).reserve_simulation_request(
        org_id, actor, request_id, "b" * 64,
    )
    assert reservation["status"] == "reserved"
    with pytest.raises(ProviderAccountingError):
        await ProviderAccountingContext.open(
            pg_pool, org_id, task_type="simulator", actor_auth_user_id=actor,
        )
    with pytest.raises(ProviderAccountingError):
        await ProviderAccountingContext.open(
            pg_pool, org_id, task_type="simulator", actor_auth_user_id=actor,
            simulation_request_id=request_id,
            simulation_claim_token=uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_simulator_claim_rotation_after_open_blocks_dispatch_before_transport(
    pg_pool, sample_org,
):
    from src.core.db.repositories.message_repo import MessageRepository

    org_id = sample_org["id"]
    actor = await _tenant(pg_pool, org_id)
    request_id = uuid.uuid4()
    reservation = await MessageRepository(pg_pool).reserve_simulation_request(
        org_id, actor, request_id, "c" * 64,
    )
    context = await ProviderAccountingContext.open(
        pg_pool, org_id, task_type="simulator", actor_auth_user_id=actor,
        simulation_request_id=request_id,
        simulation_claim_token=reservation["claim_token"],
    )
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "UPDATE simulation_requests SET claim_token=$4 "
            "WHERE organization_id=$1 AND auth_user_id=$2 AND request_id=$3",
            org_id, actor, request_id, uuid.uuid4(),
        )

    transport_calls = 0

    def handler(request):
        nonlocal transport_calls
        transport_calls += 1
        return _stream_response(_response(prompt=1, completion=1, total=2))

    llm = ObservedChatLLM(
        policy=_policy(), session=context, sync_transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(ProviderAccountingError):
            await asyncio.to_thread(llm.call, "non deve essere inviato")
        assert transport_calls == 0
        assert await _events(pg_pool, org_id) == []
    finally:
        await context.close()


@pytest.mark.asyncio
async def test_concurrent_whatsapp_messages_wait_per_tenant_and_allow_other_tenant_progress(
    pg_pool, sample_org, other_org,
):
    org_id, other_org_id = sample_org["id"], other_org["id"]
    await _tenant(pg_pool, org_id, used=2)
    actor_two = await _tenant(pg_pool, other_org_id)
    message_one = await _inbound_message(pg_pool, org_id, phone_suffix="00000001")
    message_two = await _inbound_message(pg_pool, org_id, phone_suffix="00000002")
    first = await ProviderAccountingContext.open(
        pg_pool, org_id, task_type="customer_message", worker_message_id=message_one,
    )
    contender = await ProviderAccountingContext.open(
        pg_pool, org_id, task_type="customer_message", worker_message_id=message_two,
    )
    other = await ProviderAccountingContext.open(
        pg_pool, other_org_id, task_type="report", actor_auth_user_id=actor_two,
    )
    first_entered, release_first = threading.Event(), threading.Event()
    second_entered, other_entered = threading.Event(), threading.Event()

    def first_handler(_request):
        first_entered.set()
        if not release_first.wait(timeout=5):
            raise AssertionError("first provider request was not released")
        return _stream_response(_response(prompt=4, completion=2, total=6))

    def second_handler(_request):
        second_entered.set()
        return _stream_response(_response(prompt=3, completion=2, total=5))

    def other_handler(_request):
        other_entered.set()
        return _stream_response(_response(prompt=2, completion=1, total=3))

    first_llm = ObservedChatLLM(policy=_policy(), session=first,
                                sync_transport=httpx.MockTransport(first_handler))
    second_llm = ObservedChatLLM(policy=_policy(), session=contender,
                                 sync_transport=httpx.MockTransport(second_handler))
    other_llm = ObservedChatLLM(policy=_policy(), session=other,
                                sync_transport=httpx.MockTransport(other_handler))
    try:
        first_task = asyncio.create_task(asyncio.to_thread(first_llm.call, "messaggio uno"))
        assert await asyncio.to_thread(first_entered.wait, 5)
        second_task = asyncio.create_task(asyncio.to_thread(second_llm.call, "messaggio due"))
        other_task = asyncio.create_task(asyncio.to_thread(other_llm.call, "altro tenant"))
        assert await other_task == "Risposta test."
        assert other_entered.is_set()
        assert not second_task.done()
        assert not await asyncio.to_thread(second_entered.wait, 0.15)
        assert len(await _events(pg_pool, org_id)) == 1

        release_first.set()
        assert await first_task == "Risposta test."
        assert await second_task == "Risposta test."
        assert second_entered.is_set()
        attempts = await _events(pg_pool, org_id)
        assert len(attempts) == 2
        assert all(row["metadata"]["accounting_status"] == "recorded" for row in attempts)
        assert {row["metadata"]["context"]["message_id"] for row in attempts} == {
            str(message_one), str(message_two),
        }
        assert len(await _events(pg_pool, other_org_id)) == 1
    finally:
        release_first.set()
        await first.close()
        await contender.close()
        await other.close()


@pytest.mark.asyncio
async def test_waiting_dispatch_rechecks_message_state_before_provider_call(
    pg_pool, sample_org, monkeypatch,
):
    import src.core.provider_accounting as accounting

    monkeypatch.setattr(accounting, "_ADMISSION_RETRY_SECONDS", 0.01)
    org_id = sample_org["id"]
    await _tenant(pg_pool, org_id, used=2)
    message_one = await _inbound_message(pg_pool, org_id, phone_suffix="00000003")
    message_two = await _inbound_message(pg_pool, org_id, phone_suffix="00000004")
    first = await ProviderAccountingContext.open(
        pg_pool, org_id, task_type="customer_message", worker_message_id=message_one,
    )
    contender = await ProviderAccountingContext.open(
        pg_pool, org_id, task_type="customer_message", worker_message_id=message_two,
    )
    first_entered, release_first = threading.Event(), threading.Event()
    second_calls = 0

    def first_handler(_request):
        first_entered.set()
        if not release_first.wait(timeout=5):
            raise AssertionError("first provider request was not released")
        return _stream_response(_response(prompt=1, completion=1, total=2))

    def second_handler(_request):
        nonlocal second_calls
        second_calls += 1
        return _stream_response(_response(prompt=1, completion=1, total=2))

    first_llm = ObservedChatLLM(policy=_policy(), session=first,
                                sync_transport=httpx.MockTransport(first_handler))
    second_llm = ObservedChatLLM(policy=_policy(), session=contender,
                                 sync_transport=httpx.MockTransport(second_handler))
    first_task = None
    try:
        first_task = asyncio.create_task(asyncio.to_thread(first_llm.call, "messaggio uno"))
        assert await asyncio.to_thread(first_entered.wait, 5)
        second_task = asyncio.create_task(asyncio.to_thread(second_llm.call, "messaggio due"))
        await asyncio.sleep(0.08)
        async with pg_pool.acquire() as conn:
            await conn.execute(
                "UPDATE messages SET status='dead' WHERE organization_id=$1 AND id=$2",
                org_id, message_two,
            )
        release_first.set()
        assert await first_task == "Risposta test."
        with pytest.raises(ProviderAccountingError):
            await second_task
        assert second_calls == 0
    finally:
        release_first.set()
        await first.close()
        await contender.close()


@pytest.mark.asyncio
async def test_unknown_cost_keeps_measured_tokens_and_recovery_cannot_invent_price(pg_pool, sample_org):
    org_id = sample_org["id"]
    actor = await _tenant(pg_pool, org_id)
    approver = await _manager(pg_pool, org_id)
    context = await ProviderAccountingContext.open(
        pg_pool, org_id, task_type="report", actor_auth_user_id=actor,
    )
    try:
        attempt = await context.begin("groq", "groq/unknown-model", "test")
        await context.finish(attempt, RawProviderUsage("unknown-model", 8, 4, 12), 123)
        await _record_provider_request_id(pg_pool, org_id, attempt.attempt_id)
        rows = await _events(pg_pool, org_id)
        assert len(rows) == 1
        metadata = rows[0]["metadata"]
        assert metadata["outcome"] == "response_received"
        assert metadata["actual_model"] == "unknown-model"
        assert metadata["prompt_tokens"] == 8 and metadata["completion_tokens"] == 4
        assert metadata["total_tokens"] == 12
        assert metadata["estimated_cost_eur"] is None
        assert metadata["accounting_status"] == "unresolved"
        assert metadata["failure_code"] == "cost_unavailable"
        async with pg_pool.acquire() as conn:
            assert await conn.fetchval(
                "SELECT ai_accounting_blocked FROM organizations WHERE id=$1", org_id,
            ) is True
        from src.core.db.repositories.billing_repo import BillingRepository
        payload = _evidence_bytes(
            rows[0]["id"], outcome="provider_usage_confirmed",
            actual_model="unknown-model", prompt_tokens=8,
            completion_tokens=4, total_tokens=12,
        )
        _, _, _, evidence = await _verified_evidence(org_id, rows[0]["id"], payload)
        repository = BillingRepository(pg_pool)
        proposal_version = await repository.propose_provider_reconciliation(
            org_id, rows[0]["id"], actor_auth_user_id=actor, aal="aal2",
            evidence=evidence, attestations=_RECOVERY_ATTESTATIONS,
        )
        assert proposal_version == 1
        with pytest.raises(ValueError, match="verified model price"):
            await repository.approve_provider_reconciliation(
                org_id, rows[0]["id"], actor_auth_user_id=approver, aal="aal2",
                proposal_version=proposal_version, evidence=evidence,
                attestations=_RECOVERY_ATTESTATIONS,
            )
        after = (await _events(pg_pool, org_id))[0]["metadata"]
        assert after["accounting_status"] == "unresolved"
        assert after["prompt_tokens"] == 8 and after["estimated_cost_eur"] is None
        assert after["reconciliation"]["state"] == "proposed"
    finally:
        await context.close()


_RECOVERY_ATTESTATIONS = {
    "provider_account_and_request_verified": True,
    "terminal_outcome_verified": True,
    "metrics_verified": True,
}


class _ImmutableTestEvidenceStore:
    def __init__(self, organization_id, payload):
        self.organization_id = uuid.UUID(str(organization_id))
        self.payload = payload
        self.objects = {}
        self.versions = {}

    async def read_immutable(self, organization_id, object_id):
        from datetime import datetime, timedelta, timezone
        from src.core.provider_evidence import ImmutableEvidenceBlob

        assert organization_id == self.organization_id
        payload = self.objects.get(object_id)
        if payload is None:
            return None
        version_id = self.versions.setdefault(object_id, "v_" + uuid.uuid4().hex)
        return ImmutableEvidenceBlob(
            data=payload, version_id=version_id,
            retention_mode="compliance",
            retained_until=datetime.now(timezone.utc) + timedelta(days=100),
        )


def _evidence_bytes(attempt_id, *, outcome="provider_confirms_not_billed",
                    actual_model=None, prompt_tokens=None,
                    completion_tokens=None, total_tokens=None,
                    provider_event_at=None, provider_request_id="chatcmpl_test_fixture"):
    from datetime import datetime, timezone

    return json.dumps({
        "schema_version": 1,
        "attempt_id": str(attempt_id),
        "provider": "groq",
        "provider_account_ref": "acct_123456789",
        "provider_correlation_id": str(attempt_id),
        "provider_request_id": provider_request_id,
        "provider_event_at": (provider_event_at or datetime.now(timezone.utc)).isoformat(),
        "terminal": True,
        "outcome": outcome,
        "actual_model": actual_model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }, sort_keys=True, separators=(",", ":")).encode()


async def _verified_evidence(org_id, attempt_id, payload):
    import hashlib
    from src.core.provider_evidence import verify_provider_evidence

    store = _ImmutableTestEvidenceStore(org_id, payload)
    object_id = f"a03e_{uuid.uuid4().hex}"
    store.objects[object_id] = payload
    digest = hashlib.sha256(payload).hexdigest()
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("GROQ_PROVIDER_ACCOUNT_REF", "acct_123456789")
        verified = await verify_provider_evidence(
            store, uuid.UUID(str(org_id)), object_id, digest, uuid.UUID(str(attempt_id)),
        )
    return store, object_id, digest, verified


@pytest.mark.asyncio
async def test_recovery_requires_distinct_aal2_tenant_approvers_and_audits_atomically(
    pg_pool, sample_org, other_org,
):
    from src.core.db.repositories.billing_repo import BillingRepository

    org_id = sample_org["id"]
    proposer = await _tenant(pg_pool, org_id)
    approver = await _manager(pg_pool, org_id)
    foreign_actor = await _tenant(pg_pool, other_org["id"])
    context = await ProviderAccountingContext.open(
        pg_pool, org_id, task_type="report", actor_auth_user_id=proposer,
    )
    repository = BillingRepository(pg_pool)
    try:
        attempt = await context.begin("groq", "groq/llama-3.1-8b-instant", "test")
        await context.fail(attempt, "http_error", 125, usage_present=None)
        payload = _evidence_bytes(attempt.attempt_id)
        _, object_id, digest, evidence = await _verified_evidence(org_id, attempt.attempt_id, payload)

        with pytest.raises(PermissionError):
            await repository.propose_provider_reconciliation(
                org_id, attempt.attempt_id, actor_auth_user_id=proposer, aal="aal1",
                evidence=evidence, attestations=_RECOVERY_ATTESTATIONS,
            )
        with pytest.raises(ValueError, match="attestations"):
            await repository.propose_provider_reconciliation(
                org_id, attempt.attempt_id, actor_auth_user_id=proposer, aal="aal2",
                evidence=evidence,
                attestations={**_RECOVERY_ATTESTATIONS, "metrics_verified": False},
            )
        assert await repository.propose_provider_reconciliation(
            other_org["id"], attempt.attempt_id, actor_auth_user_id=foreign_actor, aal="aal2",
            evidence=evidence, attestations=_RECOVERY_ATTESTATIONS,
        ) is None
        # Without a provider-returned ID in the ledger, the human artifact cannot
        # establish a unique provider request for this attempt.
        with pytest.raises(ValueError, match="correlation"):
            await repository.propose_provider_reconciliation(
                org_id, attempt.attempt_id, actor_auth_user_id=proposer, aal="aal2",
                evidence=evidence, attestations=_RECOVERY_ATTESTATIONS,
            )
        await _record_provider_request_id(pg_pool, org_id, attempt.attempt_id)
        from datetime import datetime, timedelta, timezone
        stale_payload = _evidence_bytes(
            attempt.attempt_id,
            provider_event_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        _, _, _, stale_evidence = await _verified_evidence(org_id, attempt.attempt_id, stale_payload)
        with pytest.raises(ValueError, match="correlation"):
            await repository.propose_provider_reconciliation(
                org_id, attempt.attempt_id, actor_auth_user_id=proposer, aal="aal2",
                evidence=stale_evidence, attestations=_RECOVERY_ATTESTATIONS,
            )

        version = await repository.propose_provider_reconciliation(
            org_id, attempt.attempt_id, actor_auth_user_id=proposer, aal="aal2",
            evidence=evidence, attestations=_RECOVERY_ATTESTATIONS,
        )
        assert version == 1
        assert await repository.propose_provider_reconciliation(
            org_id, attempt.attempt_id, actor_auth_user_id=proposer, aal="aal2",
            evidence=evidence, attestations=_RECOVERY_ATTESTATIONS,
        ) is None
        with pytest.raises(PermissionError):
            await repository.approve_provider_reconciliation(
                org_id, attempt.attempt_id, actor_auth_user_id=approver, aal="aal1",
                proposal_version=version, evidence=evidence,
                attestations=_RECOVERY_ATTESTATIONS,
            )
        assert not await repository.approve_provider_reconciliation(
            org_id, attempt.attempt_id, actor_auth_user_id=proposer, aal="aal2",
            proposal_version=version, evidence=evidence,
            attestations=_RECOVERY_ATTESTATIONS,
        )
        assert not await repository.approve_provider_reconciliation(
            other_org["id"], attempt.attempt_id, actor_auth_user_id=foreign_actor, aal="aal2",
            proposal_version=version, evidence=evidence,
            attestations=_RECOVERY_ATTESTATIONS,
        )
        assert (await _events(pg_pool, org_id))[0]["metadata"]["accounting_status"] == "unresolved"

        assert await repository.approve_provider_reconciliation(
            org_id, attempt.attempt_id, actor_auth_user_id=approver, aal="aal2",
            proposal_version=version, evidence=evidence,
            attestations=_RECOVERY_ATTESTATIONS,
        )
        metadata = (await _events(pg_pool, org_id))[0]["metadata"]
        assert metadata["accounting_status"] == "resolved"
        assert metadata["outcome"] == "provider_confirmed_not_billed"
        assert metadata["original_outcome"] == "http_error"
        assert metadata["actual_model"] is None and metadata["estimated_cost_eur"] is None
        assert metadata["reconciliation"]["state"] == "approved"
        assert metadata["reconciliation"]["proposed_by"] == str(proposer)
        assert metadata["reconciliation"]["approved_by"] == str(approver)
        assert metadata["reconciliation"]["evidence_object_id"] == object_id
        assert metadata["reconciliation"]["evidence_sha256"] == digest
        assert metadata["reconciliation"]["evidence_object_version_id"].startswith("v_")
        # Raw provider account/request references and artifact content are not persisted.
        serialized = json.dumps(metadata)
        assert "acct_123456789" not in serialized and "req_123456789" not in serialized
        assert "provider_account_ref" not in serialized
        async with pg_pool.acquire() as conn:
            assert await conn.fetchval(
                "SELECT ai_accounting_blocked FROM organizations WHERE id=$1", org_id,
            ) is False
            audit_rows = await conn.fetch(
                "SELECT action,details FROM audit_log WHERE organization_id=$1 AND target_id=$2 "
                "AND action LIKE 'provider_attempt.%' ORDER BY created_at,id",
                org_id, attempt.attempt_id,
            )
        assert [row["action"] for row in audit_rows] == [
            "provider_attempt.reconciliation_proposed", "provider_attempt.reconciled",
        ]
        assert all("req_123456789" not in str(row["details"]) for row in audit_rows)
        assert not await repository.approve_provider_reconciliation(
            org_id, attempt.attempt_id, actor_auth_user_id=approver, aal="aal2",
            proposal_version=version, evidence=evidence,
            attestations=_RECOVERY_ATTESTATIONS,
        )
    finally:
        await context.close()


@pytest.mark.asyncio
async def test_in_flight_recovery_is_never_enabled_by_age_or_timeout(pg_pool, sample_org):
    from src.core.db.repositories.billing_repo import BillingRepository

    org_id = sample_org["id"]
    proposer = await _tenant(pg_pool, org_id)
    approver = await _manager(pg_pool, org_id)
    context = await ProviderAccountingContext.open(
        pg_pool, org_id, task_type="report", actor_auth_user_id=proposer,
    )
    repository = BillingRepository(pg_pool)
    try:
        attempt = await context.begin("groq", "groq/llama-3.1-8b-instant", "test")
        async with pg_pool.acquire() as conn:
            await conn.execute(
                "UPDATE usage_events SET created_at=now()-interval '6 days' WHERE id=$1",
                attempt.attempt_id,
            )
        payload = _evidence_bytes(attempt.attempt_id)
        _, _, _, evidence = await _verified_evidence(org_id, attempt.attempt_id, payload)
        assert await repository.propose_provider_reconciliation(
            org_id, attempt.attempt_id, actor_auth_user_id=proposer, aal="aal2",
            evidence=evidence, attestations=_RECOVERY_ATTESTATIONS,
        ) is None
        assert not await repository.approve_provider_reconciliation(
            org_id, attempt.attempt_id, actor_auth_user_id=approver, aal="aal2",
            proposal_version=1, evidence=evidence,
            attestations=_RECOVERY_ATTESTATIONS,
        )
        metadata = (await _events(pg_pool, org_id))[0]["metadata"]
        assert metadata["outcome"] == "in_flight"
        assert metadata.get("reconciliation") is None
        with pytest.raises(ProviderAccountingError):
            await ProviderAccountingContext.open(
                pg_pool, org_id, task_type="report", actor_auth_user_id=proposer,
            )
    finally:
        await context.close()


@pytest.mark.asyncio
async def test_usage_recovery_uses_artifact_metrics_and_server_pricing(pg_pool, sample_org):
    from src.core.db.repositories.billing_repo import BillingRepository

    org_id = sample_org["id"]
    proposer = await _tenant(pg_pool, org_id)
    approver = await _manager(pg_pool, org_id)
    context = await ProviderAccountingContext.open(
        pg_pool, org_id, task_type="report", actor_auth_user_id=proposer,
    )
    repository = BillingRepository(pg_pool)
    try:
        attempt = await context.begin("groq", "groq/llama-3.1-8b-instant", "test")
        await context.fail(attempt, "deadline_exceeded", 30_000)
        await _record_provider_request_id(pg_pool, org_id, attempt.attempt_id)
        payload = _evidence_bytes(
            attempt.attempt_id, outcome="provider_usage_confirmed",
            actual_model="llama-3.1-8b-instant", prompt_tokens=7,
            completion_tokens=5, total_tokens=12,
        )
        _, _, _, evidence = await _verified_evidence(org_id, attempt.attempt_id, payload)
        version = await repository.propose_provider_reconciliation(
            org_id, attempt.attempt_id, actor_auth_user_id=proposer, aal="aal2",
            evidence=evidence, attestations=_RECOVERY_ATTESTATIONS,
        )
        assert version == 1
        assert await repository.approve_provider_reconciliation(
            org_id, attempt.attempt_id, actor_auth_user_id=approver, aal="aal2",
            proposal_version=version, evidence=evidence,
            attestations=_RECOVERY_ATTESTATIONS,
        )
        metadata = (await _events(pg_pool, org_id))[0]["metadata"]
        assert metadata["accounting_status"] == "recorded"
        assert metadata["outcome"] == "provider_usage_confirmed"
        assert metadata["original_outcome"] == "deadline_exceeded"
        assert (metadata["actual_model"], metadata["prompt_tokens"],
                metadata["completion_tokens"], metadata["total_tokens"]) == (
                    "llama-3.1-8b-instant", 7, 5, 12,
                )
        assert metadata["estimated_cost_eur"] is not None
    finally:
        await context.close()


@pytest.mark.asyncio
async def test_concurrent_approval_has_one_cas_winner(pg_pool, sample_org):
    from src.core.db.repositories.billing_repo import BillingRepository

    org_id = sample_org["id"]
    proposer = await _tenant(pg_pool, org_id)
    approver = await _manager(pg_pool, org_id)
    context = await ProviderAccountingContext.open(
        pg_pool, org_id, task_type="report", actor_auth_user_id=proposer,
    )
    repository = BillingRepository(pg_pool)
    try:
        attempt = await context.begin("groq", "groq/llama-3.1-8b-instant", "test")
        await context.fail(attempt, "http_error", 100)
        await _record_provider_request_id(pg_pool, org_id, attempt.attempt_id)
        payload = _evidence_bytes(attempt.attempt_id)
        _, _, _, evidence = await _verified_evidence(org_id, attempt.attempt_id, payload)
        version = await repository.propose_provider_reconciliation(
            org_id, attempt.attempt_id, actor_auth_user_id=proposer, aal="aal2",
            evidence=evidence, attestations=_RECOVERY_ATTESTATIONS,
        )
        results = await asyncio.gather(*(
            repository.approve_provider_reconciliation(
                org_id, attempt.attempt_id, actor_auth_user_id=approver, aal="aal2",
                proposal_version=version, evidence=evidence,
                attestations=_RECOVERY_ATTESTATIONS,
            ) for _ in range(2)
        ))
        assert sorted(results) == [False, True]
        async with pg_pool.acquire() as conn:
            assert await conn.fetchval(
                "SELECT count(*) FROM audit_log WHERE organization_id=$1 "
                "AND action='provider_attempt.reconciled' AND target_id=$2",
                org_id, attempt.attempt_id,
            ) == 1
    finally:
        await context.close()


@pytest.mark.asyncio
async def test_reserved_attempt_type_cannot_be_written_through_generic_billing_repository(pg_pool, sample_org):
    from src.core.db.repositories.billing_repo import BillingRepository

    with pytest.raises(ValueError):
        await BillingRepository(pg_pool).record_usage(
            sample_org["id"], "system.provider_attempt", metadata={"accounting_status": "recorded"},
        )
    with pytest.raises(ValueError):
        await BillingRepository(pg_pool).enqueue_governance(
            sample_org["id"], "usage", {"event_type": "system.provider_attempt"},
        )


@pytest.mark.asyncio
async def test_database_admission_failure_never_reaches_transport(pg_pool, sample_org):
    org_id = sample_org["id"]
    actor = await _tenant(pg_pool, org_id)
    context = await ProviderAccountingContext.open(
        pg_pool, org_id, task_type="document_qa", actor_auth_user_id=actor,
    )
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_response())

    class BrokenAcquire:
        def __call__(self):
            raise RuntimeError("database unavailable")

    class BrokenPool:
        acquire = BrokenAcquire()

    context.pool = BrokenPool()
    llm = ObservedChatLLM(policy=_policy(), session=context,
                          sync_transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ProviderAccountingError):
            await asyncio.to_thread(llm.call, "private prompt")
        assert calls == 0
    finally:
        await context.close()
