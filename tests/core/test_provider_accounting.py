import asyncio
import json
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
            dispatch_observed.set()

        asyncio.run_coroutine_threadsafe(check_pre_dispatch_row(), loop).result(timeout=5)
        assert request.url.path.endswith("/chat/completions")
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

    async def finish_once_fails(attempt, usage, latency_ms, *, output_valid=True):
        nonlocal finish_calls
        finish_calls += 1
        if finish_calls == 1:
            raise RuntimeError("synthetic accounting write failure")
        return await original_finish(
            attempt, usage, latency_ms, output_valid=output_valid,
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

    async def commit_then_lose_ack(attempt, usage, latency_ms, *, output_valid=True):
        nonlocal finish_calls, committed_latency
        finish_calls += 1
        result = await original_finish(
            attempt, usage, latency_ms, output_valid=output_valid,
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
async def test_failure_is_durable_and_blocks_the_next_generation(pg_pool, sample_org):
    org_id = sample_org["id"]
    actor = await _tenant(pg_pool, org_id)
    context = await ProviderAccountingContext.open(
        pg_pool, org_id, task_type="review", actor_auth_user_id=actor,
    )
    llm = ObservedChatLLM(
        policy=_policy(), session=context,
        sync_transport=httpx.MockTransport(lambda request: httpx.Response(503, text="private provider body")),
    )
    try:
        with pytest.raises(ProviderAccountingError):
            await asyncio.to_thread(llm.call, "review text")
        rows = await _events(pg_pool, org_id)
        assert len(rows) == 1
        assert rows[0]["metadata"]["outcome"] == "http_error"
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
async def test_concurrent_dispatches_are_serialized_per_tenant_but_not_across_tenants(
    pg_pool, sample_org, other_org,
):
    actor_one = await _tenant(pg_pool, sample_org["id"])
    actor_two = await _tenant(pg_pool, other_org["id"])
    first = await ProviderAccountingContext.open(
        pg_pool, sample_org["id"], task_type="report", actor_auth_user_id=actor_one,
    )
    contender = await ProviderAccountingContext.open(
        pg_pool, sample_org["id"], task_type="report", actor_auth_user_id=actor_one,
    )
    other = await ProviderAccountingContext.open(
        pg_pool, other_org["id"], task_type="report", actor_auth_user_id=actor_two,
    )
    try:
        results = await asyncio.gather(
            first.begin("groq", "groq/llama-3.1-8b-instant", "test"),
            contender.begin("groq", "groq/llama-3.1-8b-instant", "test"),
            other.begin("groq", "groq/llama-3.1-8b-instant", "test"),
            return_exceptions=True,
        )
        assert sum(not isinstance(result, Exception) for result in results) == 2
        assert sum(isinstance(result, ProviderAccountingError) for result in results) == 1
        assert len(await _events(pg_pool, sample_org["id"])) == 1
        assert len(await _events(pg_pool, other_org["id"])) == 1
    finally:
        await first.close()
        await contender.close()
        await other.close()


@pytest.mark.asyncio
async def test_unknown_cost_keeps_measured_tokens_and_blocks_until_reconciliation(pg_pool, sample_org):
    org_id = sample_org["id"]
    actor = await _tenant(pg_pool, org_id)
    context = await ProviderAccountingContext.open(
        pg_pool, org_id, task_type="report", actor_auth_user_id=actor,
    )
    try:
        attempt = await context.begin("groq", "groq/unknown-model", "test")
        await context.finish(attempt, RawProviderUsage("unknown-model", 8, 4, 12), 123)
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
