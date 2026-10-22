"""Independent A04 review regressions against disposable PostgreSQL.

Provider responses are simulated; reservations, response persistence, commercial
usage, booking capacity/idempotency and lease fencing use the real database.
"""

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import httpx

from src.core.billing.quota_reservations import (
    QuotaConflict,
    QuotaLease,
    QuotaReservations,
    validate_final_response,
)
from src.core.bookings.service import BookingService
from src.core.db.repositories.booking_repo import BookingRepository
from src.core.documenti.rag_context import ContestoDocumenti
from src.core.receptionist.conversation_orchestrator import ConversationOrchestrator
from src.core.ai_safety import BOOKING_RECEIVED_REPLY
from src.models.schemas import DatiPrenotazione, RispostaOutput
from tests.core.test_quota_reservation_lifecycle import (
    _auth_member,
    _final,
    _inbound_message,
    _inbound_operation,
    _inbound_service_with_real_quota,
    _operation,
    _organization,
    _reservation_metadata,
    _used,
)


async def _worker(pool, monkeypatch, *, legacy=False,
                  text="Vorrei prenotare un tavolo per 2 persone il 2030-12-22 alle 20:00"):
    org = await _organization(pool)
    message_id = await _inbound_message(pool, org["id"])
    await pool.execute(
        "UPDATE messages SET content_text=$3,content=$4::jsonb WHERE organization_id=$1 AND id=$2",
        org["id"], message_id, text, json.dumps({"from": "+390000000001"}),
    )
    conversation_id = await pool.fetchval(
        "SELECT conversation_id FROM messages WHERE organization_id=$1 AND id=$2",
        org["id"], message_id,
    )
    service, repo, ai, _, send, config = _inbound_service_with_real_quota(pool, org, None)
    repo.list_conversation_messages.return_value = []
    repo.escalate_to_human.return_value = None
    repo.mark_ai_disclosure_sent.return_value = False
    repo.get_or_create_contact.return_value = {"id": uuid4()}
    booking = BookingService(booking_repo=BookingRepository(pool))
    booking.handle_reminder_reply = AsyncMock(return_value=None)
    service.booking_service = booking
    service.legacy_pipeline.booking_service = booking
    service.use_orchestrator = not legacy
    service.orchestrator = ConversationOrchestrator(
        org_repo=repo, doc_repo=repo, billing_repo=repo, conv_repo=repo,
        booking_service=booking,
    )
    proposal = RispostaOutput(
        risposta="**Richiesta ricevuta, in attesa di verifica.**",
        richiede_umano=False, motivo="", categoria="prenotazione",
        prenotazione=DatiPrenotazione(
            nome_cliente="Cliente sintetico", data="2030-12-22", ora="20:00", coperti=2,
        ),
    )
    generate = AsyncMock(side_effect=lambda *a, **k: proposal.model_copy(deep=True))
    monkeypatch.setattr("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=config))
    monkeypatch.setattr("src.core.receptionist.conversation_orchestrator.genera_risposta_async", generate)
    monkeypatch.setattr("src.whatsapp.inbound_processor.genera_risposta_async", generate)
    monkeypatch.setattr("src.core.receptionist.conversation_orchestrator.recupera_contesto_documenti",
                        AsyncMock(return_value=ContestoDocumenti()))
    monkeypatch.setattr("src.whatsapp.inbound_processor.recupera_contesto_documenti",
                        AsyncMock(return_value=ContestoDocumenti()))
    monkeypatch.setattr("src.core.guardrails.faq_cache.cache_enabled", lambda: False)
    msg = {"id": message_id, "organization_id": org["id"],
           "conversation_id": conversation_id, "content_text": text,
           "content": {"from": "+390000000001"}, "canale": "whatsapp"}
    return SimpleNamespace(org=org, msg=msg, service=service, repo=repo,
                           send=send, generate=generate, proposal=proposal)


async def _bookings(pool, org_id):
    return await pool.fetch("SELECT * FROM bookings WHERE organization_id=$1", org_id)


async def _stored(pool, operation):
    raw = await pool.fetchval(
        "SELECT response FROM simulation_requests WHERE organization_id=$1 AND request_id=$2",
        operation.organization_id, operation.id,
    )
    return json.loads(raw) if isinstance(raw, str) else raw


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_booking_only_after_successful_canonical_commit_and_replay(pg_pool, monkeypatch, legacy):
    case = await _worker(pg_pool, monkeypatch, legacy=legacy)
    operation = _inbound_operation(case.org["id"], case.msg["id"], text=case.msg["content_text"])
    real_create = case.service.booking_service.create_booking

    async def checked_create(*args, **kwargs):
        # Real persistence is visible before even the first booking side effect.
        assert await _used(pg_pool, case.org["id"]) == 1
        assert (await _reservation_metadata(pg_pool, case.org["id"], operation.id))["state"] == "committed"
        assert await _stored(pg_pool, operation)
        return await real_create(*args, **kwargs)

    case.service.booking_service.create_booking = checked_create
    first = await case.service.process_message(case.msg)
    assert first.action == "handled"
    assert len(await _bookings(pg_pool, case.org["id"])) == 1
    second = await case.service.process_message(case.msg)
    assert second.action == "handled"
    assert await _used(pg_pool, case.org["id"]) == 1
    assert len(await _bookings(pg_pool, case.org["id"])) == 1
    assert case.generate.await_count == 1
    canonical = await _stored(pg_pool, operation)
    assert "**" not in canonical["risposta"]
    assert canonical["risposta"] == BOOKING_RECEIVED_REPLY
    assert canonical["prenotazione"] is not None
    for call in case.send.await_args_list:
        assert call.args[4] == canonical["risposta"]


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_commit_postgres_failure_leaves_no_booking_or_charge_then_retry(pg_pool, monkeypatch, legacy):
    case = await _worker(pg_pool, monkeypatch, legacy=legacy)
    # Fault after response UPDATE: the whole A04 transaction must roll back.
    await pg_pool.execute("""
        CREATE FUNCTION reject_review_commit() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'synthetic A04 persistence failure'; END $$;
        CREATE TRIGGER reject_review_commit BEFORE UPDATE OF billed_at ON messages
        FOR EACH ROW WHEN (NEW.billed_at IS NOT NULL) EXECUTE FUNCTION reject_review_commit();
    """)
    first = await case.service.process_message(case.msg)
    assert first.action == "error"
    assert not await _bookings(pg_pool, case.org["id"])
    assert await _used(pg_pool, case.org["id"]) == 0
    case.send.assert_not_awaited()
    await pg_pool.execute("DROP TRIGGER reject_review_commit ON messages")
    second = await case.service.process_message(case.msg)
    assert second.action == "handled"
    assert len(await _bookings(pg_pool, case.org["id"])) == 1
    assert await _used(pg_pool, case.org["id"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_real_guardrail_handoff_has_no_commercial_charge_or_booking(pg_pool, monkeypatch, legacy):
    case = await _worker(pg_pool, monkeypatch, legacy=legacy)
    case.proposal.risposta = "Il prezzo è €9876."
    outcome = await case.service.process_message(case.msg)
    assert outcome.action == "handled" and outcome.richiede_umano
    assert await _used(pg_pool, case.org["id"]) == 0
    assert not await _bookings(pg_pool, case.org["id"])
    case.repo.escalate_to_human.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_useful_valid_ai_answer_with_human_followup_is_not_refunded(pg_pool, monkeypatch, legacy):
    case = await _worker(pg_pool, monkeypatch, legacy=legacy, text="Come funziona il servizio?")
    case.proposal.risposta = "Il servizio è disponibile su appuntamento. Lo staff verificherà i dettagli."
    case.proposal.richiede_umano = True
    case.proposal.prenotazione = None
    outcome = await case.service.process_message(case.msg)
    assert outcome.action == "handled" and outcome.richiede_umano
    assert await _used(pg_pool, case.org["id"]) == 1
    assert not await _bookings(pg_pool, case.org["id"])


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_booking_shaped_model_output_does_not_authorize_a_booking_for_faq(pg_pool, monkeypatch, legacy):
    case = await _worker(pg_pool, monkeypatch, legacy=legacy, text="Quali sono gli ingredienti della pizza?")
    await case.service.process_message(case.msg)
    assert not await _bookings(pg_pool, case.org["id"])
    assert await _used(pg_pool, case.org["id"]) == 0
    case.repo.escalate_to_human.assert_awaited_once()


@pytest.mark.asyncio
async def test_booking_authorization_uses_stored_source_not_modified_worker_envelope(pg_pool, monkeypatch):
    case = await _worker(pg_pool, monkeypatch)
    await pg_pool.execute(
        "UPDATE messages SET content_text='Quali sono gli ingredienti?' WHERE organization_id=$1 AND id=$2",
        case.org["id"], case.msg["id"],
    )
    await case.service.process_message(case.msg)
    assert not await _bookings(pg_pool, case.org["id"])


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("field,value", [(None, None), ("data", "2030-12-23"),
                                         ("ora", "21:00"), ("coperti", 4)])
async def test_customer_intent_cannot_authorize_invented_or_mismatched_booking_values(
    pg_pool, monkeypatch, legacy, field, value,
):
    text = ("Vorrei prenotare" if field is None
            else "Vorrei prenotare un tavolo per 2 persone il 2030-12-22 alle 20:00")
    case = await _worker(pg_pool, monkeypatch, legacy=legacy, text=text)
    if field is not None:
        setattr(case.proposal.prenotazione, field, value)
    await case.service.process_message(case.msg)
    assert not await _bookings(pg_pool, case.org["id"])
    assert await _used(pg_pool, case.org["id"]) == 0
    case.repo.escalate_to_human.assert_awaited_once()


@pytest.mark.asyncio
async def test_unrelated_faq_cannot_reuse_an_earlier_booking_request(pg_pool, monkeypatch):
    case = await _worker(pg_pool, monkeypatch, text="Quali ingredienti contiene la pizza?")
    await pg_pool.execute(
        "INSERT INTO messages(id,organization_id,conversation_id,direction,message_type,content,content_text,status,created_at) "
        "SELECT $1,organization_id,conversation_id,'inbound','text','{}'::jsonb,'Vorrei prenotare un tavolo',"
        "'processing',created_at-interval '2 minutes' FROM messages WHERE id=$2",
        uuid4(), case.msg["id"],
    )
    await case.service.process_message(case.msg)
    assert not await _bookings(pg_pool, case.org["id"])
    assert await _used(pg_pool, case.org["id"]) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_delivery_failure_after_valid_reply_never_refunds_or_regenerates(pg_pool, monkeypatch, legacy):
    case = await _worker(pg_pool, monkeypatch, legacy=legacy, text="Come funziona il servizio?")
    case.proposal.prenotazione = None
    case.proposal.risposta = "**Il servizio è disponibile su appuntamento.**"
    case.send.side_effect = [RuntimeError("synthetic delivery failure"), {"wam_id": "synthetic-retry"}]
    first = await case.service.process_message(case.msg)
    assert first.action == "error"
    assert await _used(pg_pool, case.org["id"]) == 1
    second = await case.service.process_message(case.msg)
    assert second.action == "handled"
    assert await _used(pg_pool, case.org["id"]) == 1
    assert case.generate.await_count == 1
    assert case.send.await_args_list[0].args[4] == case.send.await_args_list[1].args[4]
    assert "**" not in case.send.await_args_list[0].args[4]


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_final_guardrail_rejection_cannot_leave_a_booking(pg_pool, monkeypatch, legacy):
    from src.core.guardrails.validator import EsitoValidazione, FALLBACK_STAFF_TEXT

    case = await _worker(pg_pool, monkeypatch, legacy=legacy)
    monkeypatch.setattr(
        "src.core.billing.quota_reservations.valida_risposta",
        lambda *args, **kwargs: EsitoValidazione("block", FALLBACK_STAFF_TEXT, "synthetic_final_rejection"),
    )
    await case.service.process_message(case.msg)
    assert not await _bookings(pg_pool, case.org["id"])
    assert await _used(pg_pool, case.org["id"]) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_lease_expires_between_generation_and_commit_without_booking(pg_pool, monkeypatch, legacy):
    case = await _worker(pg_pool, monkeypatch, legacy=legacy)

    async def expire_before_return(*args, **kwargs):
        operation = kwargs["provider_context"]._quota_operation
        metadata = await _reservation_metadata(pg_pool, case.org["id"], operation.id)
        now = await pg_pool.fetchval("SELECT clock_timestamp()")
        metadata.update(lease_start=(now-timedelta(seconds=60)).isoformat(),
                        lease_end=(now-timedelta(seconds=30)).isoformat())
        await pg_pool.execute("UPDATE usage_events SET metadata=$2::jsonb WHERE id=$1",
                              operation.id, json.dumps(metadata))
        return case.proposal.model_copy(deep=True)

    case.generate.side_effect = expire_before_return
    await case.service.process_message(case.msg)
    assert not await _bookings(pg_pool, case.org["id"])
    assert await _used(pg_pool, case.org["id"]) == 0
    case.send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("failure", ["429", "500", "timeout"])
async def test_uncertain_provider_failure_keeps_capacity_and_prevents_second_generation(
    pg_pool, monkeypatch, legacy, failure,
):
    case = await _worker(pg_pool, monkeypatch, legacy=legacy)

    async def uncertain_provider(*args, **kwargs):
        context = kwargs["provider_context"]
        await pg_pool.execute(
            "INSERT INTO usage_events(id,organization_id,event_type,quantity,metadata) "
            "VALUES($1,$2,'system.provider_attempt',0,$3::jsonb)",
            uuid4(), case.org["id"], json.dumps({
                "accounting_status": "unresolved", "outcome": "unknown", "context": context.context,
            }),
        )
        request = httpx.Request("POST", "https://provider.invalid/chat/completions")
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic uncertain failure", request=request)
        response = httpx.Response(int(failure), request=request)
        raise httpx.HTTPStatusError("synthetic uncertain failure", request=request, response=response)

    case.generate.side_effect = uncertain_provider
    first = await case.service.process_message(case.msg)
    second = await case.service.process_message(case.msg)
    assert first.action == "error"
    assert second.action in {"error", "yielded"}
    assert case.generate.await_count == 1
    assert await _used(pg_pool, case.org["id"]) == 0
    assert not await _bookings(pg_pool, case.org["id"])
    operation = _inbound_operation(case.org["id"], case.msg["id"], text=case.msg["content_text"])
    assert (await _reservation_metadata(pg_pool, case.org["id"], operation.id))["state"] == "reserved"


@pytest.mark.asyncio
async def test_crash_after_commit_before_booking_resumes_from_durable_payload_without_ai(pg_pool, monkeypatch):
    case = await _worker(pg_pool, monkeypatch)
    operation = _inbound_operation(case.org["id"], case.msg["id"], text=case.msg["content_text"])
    quota = QuotaReservations(pg_pool)
    reserved = await quota.reserve(operation)
    final = validate_final_response(operation, reserved["ticket"], case.proposal)
    canonical = await quota.commit(operation, reserved["ticket"], final)
    # Simulated crash: no booking/delivery was executed after this commit.
    assert not await _bookings(pg_pool, case.org["id"])
    await asyncio.gather(*(case.service.process_message(case.msg) for _ in range(4)))
    case.generate.assert_not_awaited()
    assert await _used(pg_pool, case.org["id"]) == 1
    assert len(await _bookings(pg_pool, case.org["id"])) == 1
    assert await _stored(pg_pool, operation) == canonical


@pytest.mark.asyncio
async def test_concurrent_booking_continuations_share_original_authorizing_source(pg_pool, monkeypatch):
    case = await _worker(pg_pool, monkeypatch, text="sì")
    original_id, second_id = uuid4(), uuid4()
    await pg_pool.execute(
        "INSERT INTO messages(id,organization_id,conversation_id,direction,message_type,content,content_text,status,created_at) "
        "SELECT $1,organization_id,conversation_id,'inbound','text',content,"
        "'Vorrei prenotare un tavolo per 2 il 2030-12-22 alle 20:00','processing',"
        "created_at-interval '2 minutes' FROM messages WHERE id=$2",
        original_id, case.msg["id"],
    )
    await pg_pool.execute(
        "INSERT INTO messages(id,organization_id,conversation_id,direction,message_type,content,content_text,status) "
        "SELECT $1,organization_id,conversation_id,'inbound','text',content,'sì','processing' "
        "FROM messages WHERE id=$2", second_id, case.msg["id"],
    )
    barrier = asyncio.Barrier(2)
    calls = {}
    authorize = case.service._booking_context

    async def overlapping_authorization(*args, **kwargs):
        source = await authorize(*args, **kwargs)
        task = asyncio.current_task()
        calls[task] = calls.get(task, 0) + 1
        if calls[task] == 1:
            await asyncio.wait_for(barrier.wait(), timeout=10)
        return source

    monkeypatch.setattr(case.service, "_booking_context", overlapping_authorization)
    second_message = dict(case.msg, id=second_id)
    results = await asyncio.gather(case.service.process_message(case.msg),
                                   case.service.process_message(second_message))
    assert all(result.action == "handled" for result in results)
    bookings = await _bookings(pg_pool, case.org["id"])
    assert len(bookings) == 1
    assert str(bookings[0]["source_message_id"]) == str(original_id)
    assert await _used(pg_pool, case.org["id"]) == 2


@pytest.mark.asyncio
async def test_stale_committed_reply_remains_replayable_but_cannot_create_a_new_booking(pg_pool, monkeypatch):
    case = await _worker(pg_pool, monkeypatch)
    await pg_pool.execute(
        "UPDATE messages SET created_at=clock_timestamp()-interval '31 minutes' WHERE id=$1",
        case.msg["id"],
    )
    operation = _inbound_operation(case.org["id"], case.msg["id"], text=case.msg["content_text"])
    quota = QuotaReservations(pg_pool)
    reserved = await quota.reserve(operation)
    canonical = await quota.commit(
        operation, reserved["ticket"], validate_final_response(operation, reserved["ticket"], case.proposal),
    )
    await case.service.process_message(case.msg)
    assert not await _bookings(pg_pool, case.org["id"])
    assert await _used(pg_pool, case.org["id"]) == 1
    assert await _stored(pg_pool, operation) == canonical
    case.generate.assert_not_awaited()
    case.repo.escalate_to_human.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("booking", [False, True])
async def test_already_committed_reply_can_be_delivered_when_new_generation_is_blocked(pg_pool, monkeypatch, booking):
    case = await _worker(pg_pool, monkeypatch)
    if not booking:
        case.proposal.prenotazione = None
        case.proposal.risposta = "Il servizio è disponibile su appuntamento."
    operation = _inbound_operation(case.org["id"], case.msg["id"], text=case.msg["content_text"])
    quota = QuotaReservations(pg_pool)
    reserved = await quota.reserve(operation)
    canonical = await quota.commit(
        operation, reserved["ticket"], validate_final_response(operation, reserved["ticket"], case.proposal),
    )
    await pg_pool.execute("UPDATE organizations SET ai_accounting_blocked=TRUE WHERE id=$1", case.org["id"])
    case.repo.get_org_subscription_state.return_value = {"subscription_status": "active", "ai_accounting_blocked": True}
    case.repo.claim_message_for_processing.return_value = {"status": "claimed", "ai_reply_cache": canonical}
    await case.service.process_message(case.msg)
    assert case.send.await_args.args[4] == canonical["risposta"]
    assert await _used(pg_pool, case.org["id"]) == 1
    assert not await _bookings(pg_pool, case.org["id"])
    case.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_lease_covers_generation_and_postprocessing_beyond_thirty_seconds(pg_pool):
    org = await _organization(pg_pool)
    actor = await _auth_member(pg_pool, org["id"])
    operation = _operation(org["id"], actor_id=actor)
    quota = QuotaReservations(pg_pool)
    reserved = await quota.reserve(operation)
    ticket = reserved["ticket"]
    async with QuotaLease() as lease:
        lease.bind(quota, operation, ticket)
        await asyncio.sleep(16)  # controlled long generation
        await asyncio.sleep(16)  # controlled post-processing after provider close
        await lease.checkpoint()
        metadata = await _reservation_metadata(pg_pool, org["id"], operation.id)
        assert metadata["claim_id"] == str(ticket.claim_id)
        assert metadata["lease_end"] > ticket.lease_end.isoformat()
        await quota.commit(operation, ticket, _final(operation, ticket))
    assert await _used(pg_pool, org["id"]) == 1


@pytest.mark.asyncio
async def test_crash_or_lease_loss_never_releases_uncertain_provider_or_authorizes_retry(pg_pool):
    org = await _organization(pg_pool)
    actor = await _auth_member(pg_pool, org["id"])
    operation = _operation(org["id"], actor_id=actor)
    quota = QuotaReservations(pg_pool)
    reserved = await quota.reserve(operation)
    ticket = reserved["ticket"]
    async with QuotaLease() as lease:
        lease.bind(quota, operation, ticket)
        await pg_pool.execute(
            "INSERT INTO usage_events(id,organization_id,event_type,quantity,metadata) "
            "VALUES($1,$2,'system.provider_attempt',0,$3::jsonb)",
            uuid4(), org["id"], json.dumps({"accounting_status": "unresolved", "outcome": "unknown",
                "context": {"quota_operation_id": str(operation.id)}}),
        )
        metadata = await _reservation_metadata(pg_pool, org["id"], operation.id)
        now = await pg_pool.fetchval("SELECT clock_timestamp()")
        metadata.update(lease_start=(now-timedelta(seconds=60)).isoformat(),
                        lease_end=(now-timedelta(seconds=30)).isoformat())
        await pg_pool.execute("UPDATE usage_events SET metadata=$2::jsonb WHERE id=$1",
                              operation.id, json.dumps(metadata))
        with pytest.raises(QuotaConflict):
            await lease.checkpoint()
    assert (await quota.reserve(operation))["status"] == "reconciliation_required"
    with pytest.raises(QuotaConflict):
        await quota.release(operation, ticket)
    assert (await _reservation_metadata(pg_pool, org["id"], operation.id))["state"] == "reserved"
    assert await _used(pg_pool, org["id"]) == 0


@pytest.mark.asyncio
async def test_concurrent_renewals_do_not_change_owner_and_stale_owner_cannot_finalize(pg_pool):
    org = await _organization(pg_pool)
    actor = await _auth_member(pg_pool, org["id"])
    operation = _operation(org["id"], actor_id=actor)
    quota = QuotaReservations(pg_pool)
    reserved = await quota.reserve(operation)
    ticket = reserved["ticket"]
    renewed = await asyncio.gather(*(quota.renew(operation, ticket) for _ in range(6)))
    assert {item.claim_id for item in renewed} == {ticket.claim_id}
    final = _final(operation, ticket)
    assert await quota.release(operation, ticket)
    replacement = await quota.reserve(operation)
    assert replacement["ticket"].claim_id != ticket.claim_id
    async with QuotaLease() as lease:
        lease.bind(quota, operation, ticket)
        with pytest.raises(QuotaConflict):
            await lease.checkpoint()
        with pytest.raises(QuotaConflict):
            await quota.commit(operation, ticket, final)
    await quota.commit(operation, replacement["ticket"], _final(operation, replacement["ticket"]))
    assert await _used(pg_pool, org["id"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", [False, True])
async def test_provider_context_cannot_dispatch_after_caller_lease_failure_or_stop(pg_pool, monkeypatch, stop):
    from src.core.provider_accounting import ProviderAccountingContext

    org = await _organization(pg_pool)
    actor = await _auth_member(pg_pool, org["id"])
    operation = _operation(org["id"], actor_id=actor)
    quota = QuotaReservations(pg_pool)
    reserved = await quota.reserve(operation)
    ticket = reserved["ticket"]
    async with QuotaLease() as lease:
        lease.bind(quota, operation, ticket)
        context = await ProviderAccountingContext.open(
            pg_pool, org["id"], task_type="simulator", actor_auth_user_id=actor,
            simulation_request_id=operation.id, simulation_claim_token=ticket.claim_id,
            quota_operation=operation, quota_ticket=ticket,
        )
        try:
            if stop:
                await lease.stop()
            else:
                monkeypatch.setattr(quota, "renew", AsyncMock(side_effect=ConnectionError("synthetic renewal failure")))
                with pytest.raises(ConnectionError):
                    await lease.checkpoint()
            # DB ownership is still valid; execution-local failure nevertheless
            # prevents another attempt before rate-limit or transport work.
            async with pg_pool.acquire() as conn, conn.transaction():
                await QuotaReservations(pg_pool).verify_ticket(conn, operation, ticket)
            with pytest.raises(QuotaConflict):
                await context.begin("openrouter", "openrouter/free", "review_failure")
        finally:
            await context.close()
    assert await pg_pool.fetchval(
        "SELECT count(*) FROM usage_events WHERE organization_id=$1 AND event_type='system.provider_attempt'",
        org["id"],
    ) == 0
    assert (await _reservation_metadata(pg_pool, org["id"], operation.id))["state"] == "reserved"
