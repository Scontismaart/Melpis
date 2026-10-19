"""Real PostgreSQL contracts for booking capacity admission.

These tests intentionally seed occupied bookings through the repository only
where a pre-existing reservation is part of the scenario. Admissions always
go through BookingService (or the HTTP/internal adapter entry point).
"""
from datetime import date, datetime, time, timezone
import asyncio
import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("reset_db")]

DAY = "2026-10-21"
HOUR = "20:00"


async def _set_capacity(repo, org_id, capacity, hour=HOUR):
    current = await repo.get_booking_settings(org_id)
    slots = current["fasce_orarie"] if current else [f"{h:02d}:00" for h in range(24)]
    caps = dict(current["capienze_orarie"]) if current else {slot: 40 for slot in slots}
    caps[hour] = capacity
    return await repo.upsert_booking_settings(org_id, slots, caps)


async def _create(service, org_id, *, day=DAY, hour=HOUR, guests=1, name="Guest"):
    return await service.create_booking(
        org_id, nome_cliente=name, data=day, ora=hour, coperti=guests,
    )


async def _count(repo, org_id, day, hour):
    bookings = await repo.list_bookings(org_id, day)
    return sum(
        b["coperti"] for b in bookings
        if (b["ora"].strftime("%H:%M") if isinstance(b["ora"], time) else str(b["ora"])[:5]) == hour
        and b["stato"] not in {"cancellata", "cancellato", "rifiutata", "no_show"}
    )


async def test_service_settings_preserve_zero_capacity(repo, sample_org, booking_service):
    saved = await booking_service.aggiorna_impostazioni(
        sample_org["id"], capienze_orarie={HOUR: 0},
    )
    reloaded = await repo.get_booking_settings(sample_org["id"])

    assert saved["capienze_orarie"][HOUR] == 0
    assert reloaded["capienze_orarie"][HOUR] == 0


@pytest.fixture
async def capacity_client(repo, booking_service, sample_org, install_test_identity):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from src.core.bookings.routes import router

    app = FastAPI()
    app.include_router(router)
    install_test_identity(app, "capacity-test-key", default_org_id=sample_org["id"])
    app.state.repo = repo
    app.state.booking_service = booking_service
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def test_settings_api_zero_roundtrip_and_denies_creation(capacity_client, repo, sample_org):
    headers = {"X-API-Key": "capacity-test-key", "X-Organization-Id": str(sample_org["id"])}
    put = await capacity_client.put(
        "/api/bookings/settings", json={"capienze_orarie": {HOUR: 0}}, headers=headers,
    )
    get = await capacity_client.get("/api/bookings/settings", headers=headers)
    create = await capacity_client.post("/api/bookings", json={
        "nome_cliente": "Zero Capacity", "data": DAY, "ora": HOUR, "coperti": 1,
    }, headers=headers)

    assert put.status_code == 200
    assert get.json()["capienze_orarie"][HOUR] == 0
    assert create.status_code == 409
    assert await _count(repo, sample_org["id"], DAY, HOUR) == 0


@pytest.mark.parametrize("action", ["confirm", "mark-completed"])
async def test_closed_capacity_lifecycle_returns_conflict(action, capacity_client, repo, sample_org):
    booking = await repo.create_booking(
        organization_id=sample_org["id"], nome_cliente="Cancelled", data=DAY,
        ora=HOUR, coperti=1, stato="cancellata",
    )
    await _set_capacity(repo, sample_org["id"], 0)
    response = await capacity_client.post(
        f"/api/bookings/{booking['id']}/{action}",
        headers={"X-API-Key": "capacity-test-key", "X-Organization-Id": str(sample_org["id"])},
    )
    assert response.status_code == 409
    assert (await repo.get_booking(sample_org["id"], booking["id"]))["stato"] == "cancellata"


async def test_status_compare_and_set_preserves_concurrent_cancellation(repo, sample_org):
    booking = await repo.create_booking(
        organization_id=sample_org["id"], nome_cliente="Original", data=DAY,
        ora=HOUR, coperti=1, stato="in_attesa",
    )
    await repo.update_booking_status(sample_org["id"], booking["id"], "cancellata")
    updated = await repo.update_booking_status(
        sample_org["id"], booking["id"], "confermata", expected_status="in_attesa", expected=booking,
    )
    assert updated is None
    assert (await repo.get_booking(sample_org["id"], booking["id"]))["stato"] == "cancellata"


async def test_details_compare_and_set_preserves_concurrent_edit(repo, sample_org):
    booking = await repo.create_booking(
        organization_id=sample_org["id"], nome_cliente="Original", data=DAY,
        ora=HOUR, coperti=1, stato="in_attesa",
    )
    await repo.update_booking_details(
        sample_org["id"], booking["id"], nome_cliente="Changed", telefono="",
        data=DAY, ora=HOUR, coperti=1, note="New note", stato="in_attesa",
    )
    updated = await repo.update_booking_details(
        sample_org["id"], booking["id"], nome_cliente="Original", telefono="",
        data=DAY, ora=HOUR, coperti=1, note="", stato="in_attesa", expected=booking,
    )
    assert updated is None
    assert (await repo.get_booking(sample_org["id"], booking["id"]))["note"] == "New note"


async def test_positive_capacity_admits_until_full(booking_service, repo, sample_org):
    await _set_capacity(repo, sample_org["id"], 2)
    await _create(booking_service, sample_org["id"], guests=2)

    with pytest.raises(ValueError, match="slot pieno"):
        await _create(booking_service, sample_org["id"], name="Overflow")
    assert await _count(repo, sample_org["id"], DAY, HOUR) == 2


async def test_internal_adapter_uses_service_capacity_policy(booking_service, repo, sample_org):
    from src.core.bookings.adapters.internal_adapter import InternalBookingAdapter
    from src.core.bookings.ports.base import CreateBookingRequest, CustomerResult

    await _set_capacity(repo, sample_org["id"], 0)
    adapter = InternalBookingAdapter(booking_service=booking_service)
    request = CreateBookingRequest(
        idempotency_key="capacity-zero-test",
        customer=CustomerResult(customer_id="guest", nome="Guest", telefono=""),
        data=date.fromisoformat(DAY), ora_inizio=time.fromisoformat(HOUR), coperti=1,
    )

    result = await adapter.create_booking(sample_org["id"], request)
    assert result.success is False
    assert result.error_code == "internal_create_failed"
    assert await _count(repo, sample_org["id"], DAY, HOUR) == 0


async def test_capacity_is_scoped_to_organization(booking_service, repo, sample_org, other_org):
    await _set_capacity(repo, sample_org["id"], 0)
    await _set_capacity(repo, other_org["id"], 1)

    with pytest.raises(ValueError, match="slot pieno"):
        await _create(booking_service, sample_org["id"])
    admitted = await _create(booking_service, other_org["id"])

    assert admitted["organization_id"] == other_org["id"]
    assert await _count(repo, sample_org["id"], DAY, HOUR) == 0
    assert await _count(repo, other_org["id"], DAY, HOUR) == 1


async def test_independent_services_compete_for_last_capacity(repo, sample_org):
    from src.core.bookings.service import BookingService

    await _set_capacity(repo, sample_org["id"], 1)
    services = [BookingService(repo, None, None), BookingService(repo, None, None)]
    outcomes = await asyncio.gather(
        *(_create(s, sample_org["id"], name=f"Guest {i}") for i, s in enumerate(services)),
        return_exceptions=True,
    )

    assert sum(not isinstance(item, BaseException) for item in outcomes) == 1
    assert sum(isinstance(item, ValueError) for item in outcomes) == 1
    assert await _count(repo, sample_org["id"], DAY, HOUR) == 1


async def test_canceled_booking_cannot_be_confirmed_when_capacity_closed(
    booking_service, repo, sample_org,
):
    await _set_capacity(repo, sample_org["id"], 1)
    booking = await _create(booking_service, sample_org["id"])
    await booking_service.cancel(sample_org["id"], booking["id"])
    await _set_capacity(repo, sample_org["id"], 0)

    with pytest.raises(ValueError, match="slot pieno"):
        await booking_service.confirm(sample_org["id"], booking["id"])
    stored = await repo.get_booking(sample_org["id"], booking["id"])
    assert stored["stato"] == "cancellata"


async def test_canceled_booking_can_be_confirmed_when_capacity_is_available(
    booking_service, repo, sample_org,
):
    await _set_capacity(repo, sample_org["id"], 1)
    booking = await _create(booking_service, sample_org["id"])
    await booking_service.cancel(sample_org["id"], booking["id"])

    confirmed = await booking_service.confirm(sample_org["id"], booking["id"])

    assert confirmed["stato"] == "confermata"
    assert await _count(repo, sample_org["id"], DAY, HOUR) == 1
    with pytest.raises(ValueError, match="slot pieno"):
        await _create(booking_service, sample_org["id"], name="Overflow")


async def test_existing_pending_booking_can_be_confirmed_after_capacity_closes(
    booking_service, repo, sample_org,
):
    await _set_capacity(repo, sample_org["id"], 1)
    booking = await _create(booking_service, sample_org["id"])
    await _set_capacity(repo, sample_org["id"], 0)

    confirmed = await booking_service.confirm(sample_org["id"], booking["id"])

    assert confirmed["stato"] == "confermata"
    assert await _count(repo, sample_org["id"], DAY, HOUR) == 1


async def test_canceled_booking_cannot_be_marked_completed_when_capacity_closed(
    booking_service, repo, sample_org, capacity_client,
):
    await _set_capacity(repo, sample_org["id"], 1)
    booking = await _create(booking_service, sample_org["id"])
    await booking_service.cancel(sample_org["id"], booking["id"])
    await _set_capacity(repo, sample_org["id"], 0)
    headers = {"X-API-Key": "capacity-test-key", "X-Organization-Id": str(sample_org["id"])}
    response = await capacity_client.post(f"/api/bookings/{booking['id']}/mark-completed", headers=headers)
    assert response.status_code == 409
    assert (await repo.get_booking(sample_org["id"], booking["id"]))["stato"] == "cancellata"


async def test_canceled_booking_can_be_marked_completed_with_capacity(
    booking_service, repo, sample_org,
):
    await _set_capacity(repo, sample_org["id"], 1)
    booking = await _create(booking_service, sample_org["id"])
    await booking_service.cancel(sample_org["id"], booking["id"])
    completed = await booking_service.mark_completed(sample_org["id"], booking["id"])
    assert completed["stato"] == "completata"
    assert completed["completata_at"] is not None


async def test_no_show_job_does_not_revive_cancelled_booking(repo, sample_org):
    from src.core.bookings.no_show_job import mark_da_verificare_for_org
    from src.core.bookings.service import BookingService

    booking = await repo.create_booking(
        organization_id=sample_org["id"], nome_cliente="No show", data=datetime.now(timezone.utc).date(),
        ora=HOUR, coperti=1, stato="confermata",
    )
    service = BookingService(repo, None, None)
    selected = asyncio.Event()
    release = asyncio.Event()
    original = repo.booking_repo.list_bookings_da_verificare

    async def pause_after_select(org_id, target_date):
        rows = await original(org_id, target_date)
        selected.set()
        await release.wait()
        return rows

    repo.booking_repo.list_bookings_da_verificare = pause_after_select
    job = asyncio.create_task(mark_da_verificare_for_org(service, sample_org["id"], "UTC"))
    try:
        await asyncio.wait_for(selected.wait(), timeout=5)
        await service.cancel(sample_org["id"], booking["id"])
    finally:
        release.set()
    marked = await job
    assert marked == []
    assert (await repo.get_booking(sample_org["id"], booking["id"]))["stato"] == "cancellata"


async def test_closing_capacity_serializes_with_inflight_admission(repo, sample_org):
    from src.core.bookings.service import BookingService

    await _set_capacity(repo, sample_org["id"], 1)
    service = BookingService(repo, None, None)
    capacity_read = asyncio.Event()
    continue_admission = asyncio.Event()
    original = service._get_capienze

    async def pause_after_capacity_read(org_id):
        result = await original(org_id)
        capacity_read.set()
        await continue_admission.wait()
        return result

    service._get_capienze = pause_after_capacity_read
    admission = asyncio.create_task(_create(service, sample_org["id"]))
    try:
        await asyncio.wait_for(capacity_read.wait(), timeout=5)
    except asyncio.TimeoutError:
        continue_admission.set()
        await asyncio.gather(admission, return_exceptions=True)
        raise
    close_service = BookingService(repo, None, None)
    close = asyncio.create_task(close_service.aggiorna_impostazioni(
        sample_org["id"], capienze_orarie={HOUR: 0},
    ))
    await asyncio.sleep(0.1)
    close_was_blocked = not close.done()
    continue_admission.set()
    admission_result, saved = await asyncio.gather(admission, close)

    assert close_was_blocked
    assert admission_result["organization_id"] == sample_org["id"]
    assert saved["capienze_orarie"][HOUR] == 0
    assert await _count(repo, sample_org["id"], DAY, HOUR) == 1
    with pytest.raises(ValueError, match="slot pieno"):
        await _create(close_service, sample_org["id"], name="After close")


async def test_booking_hour_and_date_key_equivalence(repo, sample_org):
    from src.core.bookings.service import BookingService

    await _set_capacity(repo, sample_org["id"], 1)
    services = [BookingService(repo, None, None), BookingService(repo, None, None)]
    original = repo.booking_repo.list_bookings
    first_read = asyncio.Event()
    second_read = asyncio.Event()
    release_first = asyncio.Event()
    compact_task = None

    async def synchronized_list(org_id, data=None):
        rows = await original(org_id, data)
        if asyncio.current_task() is compact_task and not first_read.is_set():
            first_read.set()
            await release_first.wait()
        elif data == DAY:
            second_read.set()
        return rows

    repo.booking_repo.list_bookings = synchronized_list
    compact = asyncio.create_task(_create(services[0], sample_org["id"], day="20261021", name="Compact"))
    compact_task = compact
    canonical = None
    try:
        await asyncio.wait_for(first_read.wait(), timeout=5)
        canonical = asyncio.create_task(_create(services[1], sample_org["id"], day=DAY, name="Canonical"))
        try:
            await asyncio.wait_for(second_read.wait(), timeout=0.1)
            second_read_before_release = True
        except asyncio.TimeoutError:
            second_read_before_release = False
    finally:
        release_first.set()
    outcomes = await asyncio.gather(
        compact, *( [canonical] if canonical is not None else []), return_exceptions=True,
    )

    assert not second_read_before_release
    assert sum(not isinstance(item, BaseException) for item in outcomes) == 1
    assert sum(isinstance(item, ValueError) for item in outcomes) == 1
    assert await _count(repo, sample_org["id"], DAY, HOUR) == 1


async def test_dst_adjacent_dates_have_independent_capacity(booking_service, repo, sample_org):
    # Europe/Rome changes DST on 2026-03-29; the local wall-clock booking slot
    # is still date-scoped and must not leak into its adjacent calendar date.
    dst_day = "2026-03-29"
    adjacent_day = "2026-03-30"
    await _set_capacity(repo, sample_org["id"], 1)
    await _create(booking_service, sample_org["id"], day=dst_day)

    adjacent = await _create(booking_service, sample_org["id"], day=adjacent_day)
    with pytest.raises(ValueError, match="slot pieno"):
        await _create(booking_service, sample_org["id"], day=dst_day)

    assert adjacent["data"].isoformat() == adjacent_day
    assert await _count(repo, sample_org["id"], dst_day, HOUR) == 1
    assert await _count(repo, sample_org["id"], adjacent_day, HOUR) == 1


async def test_minute_values_share_the_same_hour_capacity(booking_service, repo, sample_org):
    await _set_capacity(repo, sample_org["id"], 1)
    await _create(booking_service, sample_org["id"], hour="20:00")

    with pytest.raises(ValueError, match="slot pieno"):
        await _create(booking_service, sample_org["id"], hour="20:30")
    assert await _count(repo, sample_org["id"], DAY, HOUR) == 1


@pytest.mark.parametrize("path", ["legacy", "orchestrator"])
async def test_ai_booking_entrypoints_use_real_capacity_admission(
    path, repo, sample_org, mock_provider_context_open,
):
    import uuid
    from contextlib import ExitStack
    from unittest.mock import AsyncMock, MagicMock, patch
    from src.core.bookings.service import BookingService
    from src.models.schemas import DatiPrenotazione, RispostaOutput

    await _set_capacity(repo, sample_org["id"], 0)
    ai_repo = AsyncMock()
    ai_repo.check_booking_exists.return_value = False
    ai_repo.list_conversation_messages.return_value = []
    ai_repo.get_booking_for_message.return_value = None
    ai_repo.get_org_business_profile.return_value = {
        "nome": "Test", "tipo_attivita": "ristorante", "verticale": "ristorante",
    }
    service = BookingService(repo, None, None)
    output = RispostaOutput(
        risposta="Prenotazione confermata!", richiede_umano=False,
        motivo="booking", categoria="ristorante",
        prenotazione=DatiPrenotazione(
            nome_cliente="Guest", data=DAY, ora=HOUR, coperti=1,
        ),
    )
    module = (
        "src.core.receptionist.conversation_orchestrator"
        if path == "orchestrator" else "src.core.inbound.legacy_pipeline"
    )
    message_id = uuid.uuid4()
    profile = {"nome": "Test", "tipo_attivita": "ristorante", "verticale": "ristorante"}
    billing = {"subscription_status": "active", "messages_limit": 1000,
               "messages_used_this_period": 0}

    with ExitStack() as stack:
        stack.enter_context(patch("src.core.guardrails.faq_cache.cache_enabled", return_value=False))
        stack.enter_context(patch(module + ".genera_risposta_async", AsyncMock(return_value=output)))
        stack.enter_context(patch(module + ".classifica_intent", AsyncMock(
            return_value=MagicMock(intent="booking", source="rule", confidence=1.0),
        )))
        stack.enter_context(patch(module + ".recupera_contesto_documenti", AsyncMock(
            return_value=MagicMock(testo="", chunks=[]),
        )))
        if path == "orchestrator":
            from src.core.receptionist.conversation_orchestrator import ConversationOrchestrator
            from src.core.receptionist.models import OrchestrationInput
            engine = ConversationOrchestrator(
                org_repo=ai_repo, doc_repo=ai_repo, billing_repo=ai_repo,
                conv_repo=ai_repo, booking_service=service,
            )
            stack.enter_context(patch.object(engine, "_prefetch_availability", AsyncMock(return_value="")))
            result = await engine.orchestrate(OrchestrationInput(
                organization_id=sample_org["id"], message_id=message_id,
                conversation_id="trusted-conversation", text="Prenota per uno",
                sender_phone="trusted-sender", business_profile=profile,
                billing_state=billing, auth_user_id=uuid.uuid4(),
            ))
            assert result.booking_created is None
            response = result.response_text
        else:
            from src.core.inbound.legacy_pipeline import LegacyInboundPipeline
            engine = LegacyInboundPipeline(ai_repo, service)
            result = await engine.execute(
                org_id=sample_org["id"],
                msg={"id": message_id, "conversation_id": "trusted-conversation"},
                text="Prenota per uno", content={"from": "trusted-sender"},
                canale="whatsapp", business_profile_raw=profile, state=billing,
                claim_result={"status": "claimed", "ai_reply_cache": None},
            )
            response = result.response_text

    assert result.richiede_umano is True
    assert "confermata" not in response.lower()
    assert await _count(repo, sample_org["id"], DAY, HOUR) == 0


async def test_failed_update_rollback_does_not_overbook_old_slot(repo, sample_org):
    from src.core.bookings.service import BookingService

    await _set_capacity(repo, sample_org["id"], 1, hour="19:00")
    await _set_capacity(repo, sample_org["id"], 2, hour="20:00")
    booking = await repo.create_booking(
        organization_id=sample_org["id"], nome_cliente="Moving", data=DAY,
        ora="19:00", coperti=1, stato="confermata",
    )
    service = BookingService(repo, whatsapp_service=object(), app_config=object())
    notify_started = asyncio.Event()
    fail_notification = asyncio.Event()

    async def delayed_failure(_org, _booking):
        notify_started.set()
        await fail_notification.wait()
        raise RuntimeError("simulated reconfirmation delivery failure")

    service.send_booking_reconfirmation = delayed_failure
    update = asyncio.create_task(service.update_booking(
        sample_org["id"], booking["id"], ora="20:00",
    ))
    try:
        await asyncio.wait_for(notify_started.wait(), timeout=5)
    except asyncio.TimeoutError:
        fail_notification.set()
        await asyncio.gather(update, return_exceptions=True)
        raise
    competitor = BookingService(repo, None, None)
    await _create(competitor, sample_org["id"], day=DAY, hour="19:00", name="Competitor")
    fail_notification.set()
    outcome = await asyncio.gather(update, return_exceptions=True)

    assert isinstance(outcome[0], RuntimeError)
    assert await _count(repo, sample_org["id"], DAY, "19:00") <= 1
    stored = await repo.get_booking(sample_org["id"], booking["id"])
    assert stored["ora"] == time(20, 0)
    assert stored["richiede_intervento"] is True


async def test_booking_admission_with_single_connection_pool(postgres_container, sample_org):
    import asyncpg
    from src.core.bookings.adapters.internal_adapter import InternalBookingAdapter
    from src.core.bookings.service import BookingService
    from src.core.db.repositories.booking_repo import BookingRepository

    pool = await asyncpg.create_pool(
        dsn=postgres_container.get_connection_url().replace("+psycopg2", ""),
        min_size=1,
        max_size=1,
        server_settings={"search_path": "public, extensions"},
    )
    try:
        service = BookingService(BookingRepository(pool), None, None)
        result = await asyncio.wait_for(_create(service, sample_org["id"]), timeout=3)
        assert result["organization_id"] == sample_org["id"]
        assert await _count(service.repo, sample_org["id"], DAY, HOUR) == 1
    finally:
        await pool.close()
