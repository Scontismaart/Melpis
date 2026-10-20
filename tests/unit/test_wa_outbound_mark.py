"""P1.2 Send-Then-Mark outbound WhatsApp (DB-free).

Copre: pre-mark ambiguous prima della chiamata Meta, salto reinvii
(sent/ambiguous fresca), invio con continuita' di marcatura per righe mai
partite, uso quota solo su invio reale, guard retry-worker, threading chiave
inbound reply:{msg_id}, rank laterale consentito.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.db.repositories.message_repo import apply_status_update as real_guard
from src.whatsapp.repository import apply_status_update as facade_guard
from src.whatsapp.service import (
    AMBIGUOUS_WEBHOOK_WAIT_SECONDS,
    WhatsAppService,
    _row_awaiting_webhook,
    _row_is_delivered,
)

ORG = uuid.uuid4()


@pytest.fixture(autouse=True)
def sandbox_test_recipient(monkeypatch):
    # All providers in this module are local fakes; authorize only the fixture.
    monkeypatch.setenv("WHATSAPP_TEST_RECIPIENTS", "+3900000")


def _payload(text="x", *, category=None):
    result = {"to": "+3900000", "type": "text", "text": {"body": text}}
    if category:
        result["_delivery_category"] = category
    return result


def _now():
    return datetime.now(timezone.utc)


class FakeMsgRepo:
    def __init__(self):
        self.by_key: dict[tuple[str, str], dict] = {}
        self.by_id: dict[str, dict] = {}
        self.transitions: list[tuple] = []
        self.usage_increments = 0

    async def check_idempotency(self, org_id, key):
        return self.by_key.get((str(org_id), key))

    async def check_message_usage(self, org_id):
        return None

    async def get_contact_prefs(self, org_id, number):
        return None

    async def get_or_create_contact(self, org_id, number):
        return {"id": uuid.uuid4()}

    async def get_or_create_conversation(self, org_id, contact_id):
        return {"id": uuid.uuid4()}

    async def upsert_message(self, id, organization_id, conversation_id, wam_id,
                             direction, message_type, content, content_text,
                             status, handling_type=None, idempotency_key=None,
                             conn=None):
        if idempotency_key:
            hit = self.by_key.get((str(organization_id), idempotency_key))
            if hit:
                return dict(hit)
        row = {"id": id, "organization_id": organization_id,
               "conversation_id": conversation_id, "wam_id": wam_id,
               "direction": direction, "message_type": message_type,
               "content": content, "content_text": content_text,
               "status": status, "handling_type": handling_type,
               "idempotency_key": idempotency_key, "updated_at": _now()}
        self.by_id[str(id)] = row
        if idempotency_key:
            self.by_key[(str(organization_id), idempotency_key)] = row
        return dict(row)

    async def update_message_status(self, message_id, new_status, wam_id=None,
                                    error_code=None, error_title=None,
                                    error_details=None, organization_id=None):
        row = self.by_id.get(str(message_id))
        if not row:
            return None
        old = row["status"]
        if not real_guard(old, new_status):
            return dict(row)
        row["status"] = new_status
        if wam_id:
            row["wam_id"] = wam_id
        row["updated_at"] = _now()
        self.transitions.append((old, new_status))
        return dict(row)

    async def increment_message_usage(self, org_id):
        self.usage_increments += 1

    async def claim_outbound_delivery(self, message_id, *, organization_id):
        row = self.by_id.get(str(message_id))
        if not row or row["organization_id"] != organization_id or row["status"] not in {"queued", "failed"} or row.get("wam_id"):
            return None
        previous = row["status"]
        row["status"] = "sending_ambiguous"
        self.transitions.append((previous, "sending_ambiguous"))
        return dict(row)


class FakeMetaClient:
    def __init__(self, fail=False):
        self.calls: list = []
        self.fail = fail

    async def send_message(self, req):
        self.calls.append(req)
        if self.fail:
            raise RuntimeError("timeout dopo invio reale")
        return SimpleNamespace(messages=[SimpleNamespace(id="wamid.out.1")])


def _tenant():
    from src.whatsapp.config import TenantConfig
    return TenantConfig(organization_id=ORG, phone_number_id="pid1",
                        waba_id="waba1", access_token="tok")


def _service(repo=None):
    return WhatsAppService(app_config=MagicMock(), repo=repo or FakeMsgRepo())


class TestPreMark:
    @pytest.mark.asyncio
    async def test_missing_durable_row_prevents_send(self):
        repo, meta = FakeMsgRepo(), FakeMetaClient()
        svc = _service(repo)
        from src.core.channels.delivery import DeliveryUnconfirmed
        with pytest.raises(DeliveryUnconfirmed):
            await svc.attempt_delivery(
                message_id=uuid.uuid4(), phone_number_id="pid1", access_token="t",
                payload=_payload("ciao"), meta_client=meta, organization_id=ORG)
        assert meta.calls == []

    @pytest.mark.asyncio
    async def test_pre_mark_then_sent_order(self):
        repo = FakeMsgRepo()
        row = await repo.upsert_message(
            id=uuid.uuid4(), organization_id=ORG, conversation_id=uuid.uuid4(),
            wam_id=None, direction="outbound", message_type="text",
            content=_payload(), content_text="x", status="queued")
        svc, meta = _service(repo), FakeMetaClient()
        await svc.attempt_delivery(
            message_id=row["id"], phone_number_id="p", access_token="t",
            payload={"to": "+3900000", "type": "text", "text": {"body": "x"}},
            meta_client=meta, organization_id=ORG)
        assert repo.transitions[0] == ("queued", "sending_ambiguous")
        assert repo.transitions[1] == ("sending_ambiguous", "sent")
        assert repo.by_id[str(row["id"])]["wam_id"] == "wamid.out.1"

    @pytest.mark.asyncio
    async def test_exception_preserves_ambiguous(self):
        repo = FakeMsgRepo()
        row = await repo.upsert_message(
            id=uuid.uuid4(), organization_id=ORG, conversation_id=uuid.uuid4(),
            wam_id=None, direction="outbound", message_type="text",
            content=_payload(), content_text="x", status="queued")
        svc = _service(repo)
        with pytest.raises(RuntimeError):
            await svc.attempt_delivery(
                message_id=row["id"], phone_number_id="p", access_token="t",
                payload={"to": "+3900000", "type": "text", "text": {"body": "x"}},
                meta_client=FakeMetaClient(fail=True), organization_id=ORG)
        final = repo.by_id[str(row["id"])]
        assert final["status"] == "sending_ambiguous"
        assert ("sending_ambiguous", "failed") not in repo.transitions


class TestKeyDiscipline:
    @pytest.mark.asyncio
    async def test_existing_sent_no_resend_no_quota(self):
        repo = FakeMsgRepo()
        row = await repo.upsert_message(
            id=uuid.uuid4(), organization_id=ORG, conversation_id=uuid.uuid4(),
            wam_id="wamid.old", direction="outbound", message_type="text",
            content=_payload(category="service"), content_text="x", status="sent",
            idempotency_key="reply:inb-1")
        svc, meta = _service(repo), FakeMetaClient()
        res = await svc.send_whatsapp_message(
            org_id=ORG, to_number="+3900000", payload=_payload(),
            category="service", meta_client=meta, tenant_config=_tenant(),
            idempotency_key="reply:inb-1")
        assert res["id"] == row["id"]
        assert meta.calls == [] and repo.usage_increments == 0

    @pytest.mark.asyncio
    async def test_existing_queued_sends_with_same_id(self):
        repo = FakeMsgRepo()
        row = await repo.upsert_message(
            id=uuid.uuid4(), organization_id=ORG, conversation_id=uuid.uuid4(),
            wam_id=None, direction="outbound", message_type="text",
            content=_payload(category="service"), content_text="x", status="queued",
            idempotency_key="reply:inb-2")
        svc, meta = _service(repo), FakeMetaClient()
        res = await svc.send_whatsapp_message(
            org_id=ORG, to_number="+3900000",
            payload={"to": "+3900000", "type": "text", "text": {"body": "x"}},
            category="service", meta_client=meta, tenant_config=_tenant(),
            idempotency_key="reply:inb-2")
        assert meta.calls and res["status"] == "sent"
        assert res["id"] == row["id"]  # continuita' di marcatura
        assert repo.usage_increments == 0  # inbound reply already reserved quota

    @pytest.mark.asyncio
    async def test_existing_fresh_ambiguous_no_resend(self):
        repo = FakeMsgRepo()
        await repo.upsert_message(
            id=uuid.uuid4(), organization_id=ORG, conversation_id=uuid.uuid4(),
            wam_id=None, direction="outbound", message_type="text",
            content=_payload(category="service"), content_text="x", status="sending_ambiguous",
            idempotency_key="reply:inb-3")
        svc, meta = _service(repo), FakeMetaClient()
        await svc.send_whatsapp_message(
            org_id=ORG, to_number="+3900000",
            payload={"to": "+3900000", "type": "text", "text": {"body": "x"}},
            category="service", meta_client=meta, tenant_config=_tenant(),
            idempotency_key="reply:inb-3")
        assert meta.calls == [] and repo.usage_increments == 0

    @pytest.mark.asyncio
    async def test_existing_stale_ambiguous_never_resends(self):
        repo = FakeMsgRepo()
        row = await repo.upsert_message(
            id=uuid.uuid4(), organization_id=ORG, conversation_id=uuid.uuid4(),
            wam_id=None, direction="outbound", message_type="text",
            content=_payload(category="service"), content_text="x", status="sending_ambiguous",
            idempotency_key="reply:inb-4")
        repo.by_id[str(row["id"])]["updated_at"] = _now() - timedelta(
            seconds=AMBIGUOUS_WEBHOOK_WAIT_SECONDS + 60)
        svc, meta = _service(repo), FakeMetaClient()
        res = await svc.send_whatsapp_message(
            org_id=ORG, to_number="+3900000",
            payload={"to": "+3900000", "type": "text", "text": {"body": "x"}},
            category="service", meta_client=meta, tenant_config=_tenant(),
            idempotency_key="reply:inb-4")
        assert not meta.calls and res["status"] == "sending_ambiguous"


class TestRankLateral:
    def test_copies_agree_and_allow_premark(self):
        assert real_guard("queued", "sending_ambiguous") is True
        assert real_guard("processing", "sending_ambiguous") is True
        assert real_guard("sending_ambiguous", "sent") is True
        assert real_guard("sent", "sending_ambiguous") is False
        assert facade_guard("queued", "sending_ambiguous") is True
        assert facade_guard("sent", "sending_ambiguous") is False

    def test_row_helpers(self):
        assert _row_is_delivered({"status": "sent"}) is True
        assert _row_is_delivered({"status": "queued", "wam_id": "wamid.1"}) is True
        assert _row_is_delivered({"status": "queued"}) is False
        fresh = {"status": "sending_ambiguous", "updated_at": _now()}
        assert _row_awaiting_webhook(fresh) is True
        stale = {"status": "sending_ambiguous",
                 "updated_at": _now() - timedelta(hours=1)}
        assert _row_awaiting_webhook(stale) is True
        assert _row_awaiting_webhook({"status": "queued"}) is False


class TestWorkerGuard:
    def _worker(self, payload, service=None):
        from src.core.workers.retry_worker import MultiChannelRetryWorker

        repo = MagicMock()
        repo.reconstruct_payload_for_retry = AsyncMock(return_value=payload)
        repo.update_delivery_attempt = AsyncMock()
        repo.insert_delivery_attempt = AsyncMock()
        worker = MultiChannelRetryWorker(
            app_config=SimpleNamespace(max_retry_attempts=5), repo=repo,
            service=service, channel_router=MagicMock())
        return worker, repo

    @pytest.mark.asyncio
    async def test_sent_payload_skips_delivery(self):
        payload = {"organization_id": ORG, "status": "sent", "wam_id": "wamid.1",
                   "content": {"to": "+3900000"}, "channel": "whatsapp"}
        worker, repo = self._worker(payload, service=MagicMock())
        await worker._process_one({"id": "att-1", "message_id": "m-1",
                                   "attempt_number": 1})
        repo.update_delivery_attempt.assert_awaited_once()
        args = repo.update_delivery_attempt.call_args[0]
        assert args[1] == "succeeded"

    @pytest.mark.asyncio
    async def test_fresh_ambiguous_skips_delivery_untouched(self):
        payload = {"organization_id": ORG, "status": "sending_ambiguous",
                   "wam_id": None, "updated_at": _now(),
                   "content": {"to": "+3900000"}, "channel": "whatsapp"}
        worker, repo = self._worker(payload, service=MagicMock())
        await worker._process_one({"id": "att-2", "message_id": "m-2",
                                   "attempt_number": 1})
        repo.update_delivery_attempt.assert_not_awaited()
        repo.insert_delivery_attempt.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stale_ambiguous_never_retries_via_adapter(self):
        from unittest.mock import patch

        from src.core.channels.base import OutboundSendResult

        payload = {"organization_id": ORG, "status": "sending_ambiguous",
                   "wam_id": None,
                   "updated_at": _now() - timedelta(hours=1),
                   "content": {"to": "+3900000", "text": {"body": "x"}},
                   "channel": "whatsapp"}
        sender = AsyncMock(return_value=OutboundSendResult(
            success=True, channel="whatsapp"))
        router = MagicMock()
        router.get_adapter = MagicMock(return_value=MagicMock(send_reply=sender))
        worker, repo = self._worker(payload, service=SimpleNamespace())
        worker.channel_router = router
        tenant = SimpleNamespace(phone_number_id="pid", access_token="tok")
        with patch("src.whatsapp.config.load_tenant_config",
                   new=AsyncMock(return_value=tenant)):
            await worker._process_one({"id": "att-3", "message_id": "m-3",
                                       "attempt_number": 1})
        sender.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_queued_proceeds(self):
        from unittest.mock import patch

        from src.core.channels.base import OutboundSendResult

        payload = {"organization_id": ORG, "status": "queued",
                   "content": {"to": "+3900000", "text": {"body": "x"}},
                   "channel": "whatsapp"}
        sender = AsyncMock(return_value=OutboundSendResult(
            success=True, channel="whatsapp"))
        router = MagicMock()
        router.get_adapter = MagicMock(return_value=MagicMock(send_reply=sender))
        worker, repo = self._worker(payload, service=SimpleNamespace())
        worker.channel_router = router
        tenant = SimpleNamespace(phone_number_id="pid", access_token="tok")
        with patch("src.whatsapp.config.load_tenant_config",
                   new=AsyncMock(return_value=tenant)):
            await worker._process_one({"id": "att-4", "message_id": "m-4",
                                       "attempt_number": 1})
        sender.assert_awaited_once()


class TestAdapterKeyThreading:
    @pytest.mark.asyncio
    async def test_send_reply_passes_key(self):
        from src.core.channels.whatsapp_adapter import WhatsAppOutboundAdapter

        seen: dict = {}

        class FakeService:
            async def send_whatsapp_message(self, **kwargs):
                seen.update(kwargs)
                return {"status": "sent", "wam_id": "wamid.k"}

        adapter = WhatsAppOutboundAdapter(FakeService())
        res = await adapter.send_reply(
            org_id=ORG, to_destination="+3900000", text="ciao",
            tenant_config=MagicMock(), idempotency_key="reply:inb-9")
        assert res.success is True
        assert seen["idempotency_key"] == "reply:inb-9"

    @pytest.mark.asyncio
    async def test_inbound_send_reply_uses_reply_key(self):
        from src.core.inbound.service import InboundProcessingService

        seen: dict = {}

        class FakeAdapter:
            async def send_reply(self, **kwargs):
                seen.update(kwargs)
                from src.core.channels.base import OutboundSendResult
                return OutboundSendResult(success=True, channel="whatsapp",
                                          wam_id="wamid.k")

        router = MagicMock()
        router.get_adapter = MagicMock(return_value=FakeAdapter())
        svc = InboundProcessingService(
            app_config=MagicMock(), repo=MagicMock(), channel_router=router)
        msg = {"id": "inb-42", "organization_id": ORG, "canale": "whatsapp"}
        await svc._send_reply(ORG, msg, {"from": "+3900000"}, MagicMock(), "ciao")
        assert seen["idempotency_key"] == "reply:inb-42"


class TestQuotaExceededBehavior:
    @pytest.mark.asyncio
    async def test_outbound_and_escalation_remain_available_at_zero_ai_quota(self):
        repo = FakeMsgRepo()
        # Mock usage as maxed out
        repo.check_message_usage = AsyncMock(return_value={
            "messages_limit": 100,
            "messages_used_this_period": 100,
        })
        svc, meta = _service(repo), FakeMetaClient()

        normal = await svc.send_whatsapp_message(
            org_id=ORG, to_number="+3900000",
            payload={"to": "+3900000", "type": "text", "text": {"body": "normal msg"}},
            category="service", meta_client=meta, tenant_config=_tenant(),
        )
        assert normal["status"] == "sent"

        # Human quota escalation remains deliverable too.
        res = await svc.send_whatsapp_message(
            org_id=ORG, to_number="+3900000",
            payload={"to": "+3900000", "type": "text", "text": {"body": "troppe richieste"}},
            category="service", meta_client=meta, tenant_config=_tenant(),
            handling_type="quota_exceeded",
        )
        assert res["status"] == "sent"
        assert res["wam_id"] == "wamid.out.1"
        assert repo.usage_increments == 0
        repo.check_message_usage.assert_not_awaited()
