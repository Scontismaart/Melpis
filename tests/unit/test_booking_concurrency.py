import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from datetime import date
from unittest.mock import AsyncMock, MagicMock
from types import SimpleNamespace
import pytest

from src.core.bookings.service import BookingService, SlotPienoError
from src.core.bookings.memory_repo import InMemoryBookingRepo
from src.core.channels.base import OutboundSendResult
from src.core.channels.router import OutboundChannelRouter
from src.core.inbound.service import InboundProcessingService
from src.whatsapp.config import AppConfig


class LockingInMemoryBookingRepo(InMemoryBookingRepo):
    """InMemory repo equipped with an asyncio lock to simulate pg_advisory_xact_lock."""
    def __init__(self):
        super().__init__()
        self._async_locks: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def slot_lock(self, organization_id, data, ora):
        key = f"{organization_id}:{data}:{ora}"
        if key not in self._async_locks:
            self._async_locks[key] = asyncio.Lock()
        async with self._async_locks[key]:
            yield


@pytest.mark.asyncio
async def test_concurrency_competing_for_last_slot_capacity():
    """5 competing coroutines attempt to book 2 seats each at 20:00 when
    only 4 seats are available.
    Advisory lock ensures exactly 2 succeed (total 4) and 3 receive SlotPienoError.
    """
    repo = LockingInMemoryBookingRepo()
    org_id = uuid.uuid4()
    d = "2026-10-15"

    # Set capacity to 4 for 20:00
    await repo.upsert_booking_settings(
        organization_id=org_id,
        fasce_orarie=["20:00"],
        capienze_orarie={"20:00": 4},
    )

    service = BookingService(repo=repo)

    async def _try_book(i: int):
        await asyncio.sleep(0.001)  # introduce slight scheduling interleaving
        return await service.create_booking(
            org_id=org_id,
            nome_cliente=f"Cliente {i}",
            data=d,
            ora="20:00",
            coperti=2,
            telefono=f"+39333000000{i}",
        )

    results = await asyncio.gather(*[_try_book(i) for i in range(5)], return_exceptions=True)

    successes = [r for r in results if isinstance(r, dict)]
    failures = [r for r in results if isinstance(r, SlotPienoError)]

    assert len(successes) == 2, f"Expected exactly 2 bookings to fit, got {len(successes)}"
    assert len(failures) == 3, f"Expected exactly 3 overcapacity errors, got {len(failures)}"

    # Check total booked seats in DB
    booked_slots = await repo.list_bookings(org_id, d)
    total_coperti = sum(b["coperti"] for b in booked_slots if b["stato"] == "in_attesa")
    assert total_coperti == 4


@pytest.mark.asyncio
async def test_concurrency_duplicate_source_message_id_idempotency():
    """Two concurrent requests with the SAME source_message_id.
    Repository unique constraint / ON CONFLICT DO NOTHING returns the existing booking.
    """
    repo = LockingInMemoryBookingRepo()
    org_id = uuid.uuid4()
    d = "2026-10-15"
    source_msg_id = uuid.uuid4()

    await repo.upsert_booking_settings(
        organization_id=org_id,
        fasce_orarie=["20:00"],
        capienze_orarie={"20:00": 10},
    )

    service = BookingService(repo=repo)

    async def _book_with_source_id():
        return await service.create_booking(
            org_id=org_id,
            nome_cliente="Mario Rossi",
            data=d,
            ora="20:00",
            coperti=2,
            telefono="+393331112233",
            source_message_id=source_msg_id,
        )

    b1, b2 = await asyncio.gather(_book_with_source_id(), _book_with_source_id())

    assert b1["id"] == b2["id"]
    all_bookings = await repo.list_bookings(org_id, d)
    assert len(all_bookings) == 1


@pytest.mark.asyncio
async def test_outbound_dedup_prevents_duplicate_send_on_worker_crash_retry():
    """Scenario 3: Send-Then-Mark Reliability & Outbound Dedup.
    - Run 1: Normal processing saves outbound dedup and delivers reply via channel adapter.
    - Run 2: If message was marked already_sent in DB, retry worker skips it completely (0 duplicate sends).
    - Run 3: A retry after the A04 commit replays the immutable ledger response,
             never an independent outbound_dedup cache, without re-running AI.
    """
    app_config = AppConfig(
        app_secret="test",
        encryption_key="MDEyMzQ1Njc4OTAxMjM0NTY3ODkwMTIzNDU2Nzg5MDE=",
        postgres_dsn="postgresql://test:test@localhost:5432/test",
        verify_token="test",
        use_conversation_orchestrator=True,
    )

    mock_repo = AsyncMock()
    mock_service = AsyncMock()
    mock_channel = AsyncMock()

    # Configure mock service to not trigger early exits (opt-out, human, fast-path)
    mock_service.check_opt_out = AsyncMock(return_value={"is_opt_out": False})
    mock_service.check_human_request = AsyncMock(return_value=False)
    mock_service.fast_path_match = AsyncMock(return_value=None)

    org_id = uuid.uuid4()
    msg_id = uuid.uuid4()
    fake_msg = {
        "id": msg_id,
        "organization_id": org_id,
        "conversation_id": uuid.uuid4(),
        "content_text": "Ciao",
        "content": {"from": "+393401122334"},
        "canale": "whatsapp",
    }

    # Run 1: claim succeeds, no outbound dedup yet
    mock_repo.claim_message_for_processing.return_value = {
        "status": "claimed",
        "ai_reply_cache": None,
        "sent_at": None,
        "billed_at": None,
        "quota_exceeded_at": None,
        "processing_at": None,
    }
    mock_repo.get_conversation.return_value = {"ticket_status": "OPEN"}
    mock_repo.get_outbound_dedup.return_value = None
    mock_repo.check_booking_exists.return_value = False
    mock_repo.mark_ai_disclosure_sent.return_value = True
    mock_repo.get_or_create_contact.return_value = {
        "id": uuid.uuid4(),
        "ai_disclosure_sent_at": "2026-09-01",
    }
    mock_repo.get_org_subscription_state.return_value = {"subscription_status": "active"}

    class QuotaReservationsStub:
        def __init__(self, _pool):
            self.reserve = AsyncMock(side_effect=[
                {"status": "reserved", "ticket": object()},
                {"status": "replay", "response": {
                    "risposta": "Risposta committed nel ledger A04.",
                    "richiede_umano": False, "motivo": "", "categoria": "generico",
                }},
            ])
            self.commit = AsyncMock(side_effect=lambda _op, _ticket, final: json.loads(final.serialized))
            self.renew = AsyncMock(side_effect=lambda _op, ticket: ticket)
            self.release = AsyncMock()

    quota = QuotaReservationsStub(None)

    # Mock tenant config loader
    mock_tc = MagicMock()
    mock_tc.business_profile = {"nome": "Pizzeria Bella"}

    mock_channel.send_reply = AsyncMock(
        return_value=OutboundSendResult(channel="whatsapp", success=True, wam_id="meta-msg-999", error=None)
    )
    router = OutboundChannelRouter({"whatsapp": mock_channel})

    inbound_svc = InboundProcessingService(
        app_config=app_config,
        repo=mock_repo,
        service=mock_service,
        channel_router=router,
    )
    inbound_svc.orchestrator = AsyncMock()
    inbound_svc.orchestrator.orchestrate.return_value = MagicMock(
        response_text="Benvenuto da Pizzeria Bella!",
        richiede_umano=False,
        motivo_richiesta_umano=None,
        intent="faq",
        source="llm",
    )
    inbound_svc.orchestrator.orchestrate.return_value._quota_final = SimpleNamespace(serialized=json.dumps({
        "risposta": "Benvenuto da Pizzeria Bella!", "richiede_umano": False,
        "motivo": "", "categoria": "faq", "prenotazione": None,
    }))

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.core.inbound.service.load_tenant_config", AsyncMock(return_value=mock_tc))
        mp.setattr("src.core.billing.quota_reservations.QuotaReservations", lambda _pool: quota)

        # Run 1: sends message and saves outbound dedup
        res1 = await inbound_svc.process_message(fake_msg)
        assert res1.action == "handled"
        assert mock_channel.send_reply.await_count == 1
        assert mock_repo.save_outbound_dedup.await_count == 1
        dedup_args = mock_repo.save_outbound_dedup.await_args[0]
        assert dedup_args[0] == msg_id
        assert dedup_args[1] == org_id
        assert "Benvenuto da Pizzeria Bella!" in dedup_args[2]

        # Run 2: Simulating retry when DB has already marked message as sent
        mock_repo.claim_message_for_processing.return_value = {"status": "already_sent"}
        res2 = await inbound_svc.process_message(fake_msg)
        assert res2.action == "ignored"
        assert res2.handling_type == "already_sent"
        # Crucial check: send_reply was NOT called again!
        assert mock_channel.send_reply.await_count == 1

        # Run 3: Simulating retry when worker died after saving dedup but before Meta send
        mock_repo.claim_message_for_processing.return_value = {
            "status": "claimed", "ai_reply_cache": None,
        }
        mock_repo.get_outbound_dedup.return_value = {
            "response_text": "Unverified outbound dedup must not be trusted",
        }
        res3 = await inbound_svc.process_message(fake_msg)
        assert res3.action == "handled"
        assert res3.handling_type == "ai_handled"
        # The A04 committed response is delivered, not the independent cache.
        assert mock_channel.send_reply.await_count == 2
        sent_text = mock_channel.send_reply.await_args.kwargs["text"]
        assert "Risposta committed nel ledger A04." in sent_text
        assert "Unverified outbound dedup" not in sent_text
        # But orchestrator was NOT called again (no duplicate provider call).
        assert inbound_svc.orchestrator.orchestrate.await_count == 1
