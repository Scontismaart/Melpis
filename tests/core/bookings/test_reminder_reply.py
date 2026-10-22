from datetime import date, time, datetime, timezone
import pytest

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def confirmed_booking(repo, sample_org):
    b = await repo.create_booking(
        organization_id=sample_org["id"], nome_cliente="Mario",
        telefono="+393331234567", data=date(2026, 8, 2), ora=time(20, 0),
        coperti=4, stato="confermata",
    )
    await repo.update_booking_reminder_status(
        sample_org["id"], b["id"], "sent",
    )
    return await repo.get_booking(sample_org["id"], b["id"])


async def test_reminder_reply_confirm(booking_service, sample_org, confirmed_booking):
    result = await booking_service.handle_reminder_reply(
        sample_org["id"], "+393331234567", " SÌ! "
    )
    assert result is not None
    updated = await booking_service.repo.get_booking(sample_org["id"], confirmed_booking["id"])
    assert updated["reminder_status"] == "confirmed"


async def test_reminder_reply_reject(booking_service, sample_org, confirmed_booking):
    result = await booking_service.handle_reminder_reply(
        sample_org["id"], "+393331234567", "NO"
    )
    assert result is not None
    updated = await booking_service.repo.get_booking(sample_org["id"], confirmed_booking["id"])
    assert updated["reminder_status"] == "rejected"
    assert updated["stato"] == "cancellata"


async def test_reminder_reply_ambiguous(booking_service, sample_org, confirmed_booking):
    result = await booking_service.handle_reminder_reply(
        sample_org["id"], "+393331234567", "Forse"
    )
    assert result == "handoff"
    updated = await booking_service.repo.get_booking(sample_org["id"], confirmed_booking["id"])
    assert updated["reminder_status"] == "sent"
    assert updated["stato"] == "confermata"


async def test_reminder_reply_substring_does_not_cancel(booking_service, sample_org, confirmed_booking):
    result = await booking_service.handle_reminder_reply(
        sample_org["id"], "+393331234567", "No, forse vengo"
    )
    assert result == "handoff"
    updated = await booking_service.repo.get_booking(sample_org["id"], confirmed_booking["id"])
    assert updated["reminder_status"] == "sent"
    assert updated["stato"] == "confermata"


@pytest.mark.parametrize("question", ["si?", "no?"])
async def test_reminder_question_never_authorizes_change(
    booking_service, sample_org, confirmed_booking, question,
):
    result = await booking_service.handle_reminder_reply(
        sample_org["id"], "+393331234567", question,
    )
    assert result == "handoff"
    updated = await booking_service.repo.get_booking(sample_org["id"], confirmed_booking["id"])
    assert updated["reminder_status"] == "sent"
    assert updated["stato"] == "confermata"


async def test_reminder_reply_replay_is_idempotent(booking_service, sample_org, confirmed_booking):
    first = await booking_service.handle_reminder_reply(
        sample_org["id"], "+393331234567", "yes"
    )
    replay = await booking_service.handle_reminder_reply(
        sample_org["id"], "+393331234567", "yes"
    )
    assert first == replay == "confirmed"
    updated = await booking_service.repo.get_booking(sample_org["id"], confirmed_booking["id"])
    assert updated["reminder_status"] == "confirmed"


async def test_multiple_pending_reminders_handoff_without_mutation(booking_service, sample_org, confirmed_booking):
    second = await booking_service.repo.create_booking(
        organization_id=sample_org["id"], nome_cliente="Mario",
        telefono="+393331234567", data=date(2026, 8, 3), ora=time(20, 0),
        coperti=2, stato="confermata",
    )
    await booking_service.repo.update_booking_reminder_status(
        sample_org["id"], second["id"], "sent",
    )
    result = await booking_service.handle_reminder_reply(
        sample_org["id"], "+393331234567", "si"
    )
    assert result == "handoff"
    for booking_id in (confirmed_booking["id"], second["id"]):
        updated = await booking_service.repo.get_booking(sample_org["id"], booking_id)
        assert updated["reminder_status"] == "sent"
        assert updated["stato"] == "confermata"


async def test_reminder_reply_no_pending(booking_service, sample_org, confirmed_booking):
    result = await booking_service.handle_reminder_reply(
        sample_org["id"], "+393331234568", "Si confermo"
    )
    assert result is None  # numero diverso, nessun reminder pendente


async def test_reminder_reply_wrong_org(booking_service, sample_org, other_org, confirmed_booking):
    result = await booking_service.handle_reminder_reply(
        other_org["id"], "+393331234567", "Si confermo"
    )
    assert result is None  # altro org, nessun reminder pendente
