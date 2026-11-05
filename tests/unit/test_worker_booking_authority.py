import asyncio
import gc
from weakref import ref
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest

from src.core.bookings.worker_authority import WorkerBookingAuthority, WorkerProofDenied, service_receipt_v2


class Connection:
    def __init__(self):
        self.active = True
        self.txid = 1
        self.isolation = 'read committed'
    def is_in_transaction(self):
        return self.active
    async def fetchval(self, query, *args):
        return self.isolation if query == 'SHOW transaction_isolation' else self.txid


@pytest.mark.asyncio
async def test_capability_is_single_use_and_same_transaction():
    authority = WorkerBookingAuthority()
    conn = Connection()
    org, conversation, message = uuid4(), uuid4(), uuid4()
    proof = (org, conversation, message, uuid4(), 1, 'source', 'a' * 64, 1, 'state', 1)
    authority._proof = AsyncMock(return_value=proof)
    capability = await authority.mint(conn, object(), conversation, expected_state_revision=1, expected_catalog_revision=1)
    result = await authority.revalidate(conn, capability)
    assert result['runtime_enabled'] is False and result['confirmation_authorized'] is False
    with pytest.raises(WorkerProofDenied):
        await authority.revalidate(conn, capability)
    capability = await authority.mint(conn, object(), conversation, expected_state_revision=1, expected_catalog_revision=1)
    conn.txid = 2
    with pytest.raises(WorkerProofDenied):
        await authority.revalidate(conn, capability)


@pytest.mark.asyncio
async def test_capability_cannot_cross_connection_task_or_factory():
    authority = WorkerBookingAuthority()
    conn = Connection()
    org, conversation, message = uuid4(), uuid4(), uuid4()
    authority._proof = AsyncMock(return_value=(org, conversation, message, uuid4(), 1, 'x', 'a'*64, 1, 'x', 1))
    cap = await authority.mint(conn, object(), conversation, expected_state_revision=1, expected_catalog_revision=1)
    for attempt in (authority.revalidate(Connection(), cap), WorkerBookingAuthority().revalidate(conn, cap),
                    asyncio.create_task(authority.revalidate(conn, cap))):
        with pytest.raises(WorkerProofDenied):
            await attempt


@pytest.mark.asyncio
async def test_claim_rejects_untrusted_scope_before_database_access():
    authority = WorkerBookingAuthority()
    conn = Connection()
    conn.active = False
    with pytest.raises(WorkerProofDenied):
        await authority.claim(conn, uuid4(), uuid4())
    conn.active = True
    for lease in (True, 0, 61):
        with pytest.raises(WorkerProofDenied):
            await authority.claim(conn, uuid4(), uuid4(), lease_seconds=lease)
    with pytest.raises(WorkerProofDenied):
        await authority.claim(conn, str(uuid4()), uuid4())


def test_receipt_identity_is_scoped_and_strict():
    org, message = uuid4(), uuid4()
    assert service_receipt_v2(org, message, 'create', 'a'*64) == service_receipt_v2(org, message, 'create', 'a'*64)
    assert service_receipt_v2(org, message, 'create', 'a'*64) != service_receipt_v2(uuid4(), message, 'create', 'a'*64)
    with pytest.raises(ValueError):
        service_receipt_v2(org, message, 'cancel', 'a'*64)

@pytest.mark.asyncio
async def test_claim_requires_persisted_ingress_even_with_valid_a04(monkeypatch):
    from src.core.bookings.worker_authority import BookingConversationRepository
    org, message, conversation = uuid4(), uuid4(), uuid4()
    class ClaimConnection(Connection):
        async def execute(self, *args):
            pass
        async def fetchrow(self, query, *args):
            if 'SELECT id FROM organizations' in query:
                return {'id': org}
            if 'SELECT id,conversation_id FROM messages' in query:
                return {'id': message, 'conversation_id': conversation}
            return None
    monkeypatch.setattr(BookingConversationRepository, '_source', AsyncMock(return_value=({'content_text': 'hello'}, 1, 'hash')))
    monkeypatch.setattr(BookingConversationRepository, '_canonical_response', AsyncMock(return_value=({}, 'a'*64)))
    with pytest.raises(WorkerProofDenied, match='provenance'):
        await WorkerBookingAuthority().claim(ClaimConnection(), org, message)


@pytest.mark.asyncio
async def test_abandoned_capability_is_not_retained_by_authority():
    authority, conn = WorkerBookingAuthority(), Connection()
    org, conversation, message = uuid4(), uuid4(), uuid4()
    authority._proof = AsyncMock(return_value=(org, conversation, message, uuid4(), 1, 'x', 'a'*64, 1, 'x', 1))
    token = await authority.mint(conn, object(), conversation, expected_state_revision=1, expected_catalog_revision=1)
    token_ref = ref(token)
    del token
    gc.collect()
    assert token_ref() is None


@pytest.mark.asyncio
async def test_claim_rejects_repeatable_read_snapshot():
    conn = Connection()
    conn.isolation = 'repeatable read'
    with pytest.raises(WorkerProofDenied, match='read committed'):
        await WorkerBookingAuthority().claim(conn, uuid4(), uuid4())
