import asyncio
import gc
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4
from weakref import ref

import pytest

from src.core.bookings.worker_authority import (
    WorkerBookingAuthority,
    WorkerProofDenied,
    _Claim,
    _renew_claim,
    service_receipt_v2,
)


class Connection:
    def __init__(self):
        self.active = True
        self.txid = 1
        self.isolation = 'read committed'
    def is_in_transaction(self):
        return self.active
    async def fetchval(self, query, *args):
        return self.isolation if query == 'SHOW transaction_isolation' else self.txid
    async def fetchrow(self, query, *args):
        return None


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
async def test_claim_requires_database_derived_job_evidence():
    org, message = uuid4(), uuid4()
    with pytest.raises(WorkerProofDenied, match='already held'):
        await WorkerBookingAuthority().claim(Connection(), org, message)


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


@pytest.mark.asyncio
async def test_poll_advances_past_failed_confirmation(monkeypatch):
    monkeypatch.setenv('C24_PRIVATE_BOOKING_WORKER_ENABLED', 'true')
    jobs = [uuid4(), uuid4()]
    authority = WorkerBookingAuthority()
    authority._confirmed_jobs = AsyncMock(return_value=jobs)
    authority.process_confirmation = AsyncMock(side_effect=[RuntimeError('synthetic'), uuid4()])
    assert await authority.process_next_batch(None, application_pool=object()) == 1
    assert authority.process_confirmation.await_count == 2
    assert authority.process_confirmation.await_args_list[0].args[1:] == (None, None, jobs[0])
    assert authority._cursor == jobs[1]


@pytest.mark.asyncio
async def test_worker_lease_renewal_is_tenant_bound_and_uses_server_expiry():
    org, message, claim_id = uuid4(), uuid4(), uuid4()
    claim = _Claim(org, message, claim_id, 4)
    renewed_until = datetime.now(timezone.utc) + timedelta(minutes=4)
    seen = []

    class Conn:
        def __init__(self): self.args = None; seen.append(self)
        def is_in_transaction(self): return True
        def transaction(self, **kwargs): return Acquire()
        async def fetchval(self, query, *args):
            if 'c24_worker_renew_job' in query:
                self.args = args
                return renewed_until
            return True
    class Acquire:
        async def __aenter__(self): self.conn = Conn(); return self.conn
        async def __aexit__(self, *args): return False
    class Pool:
        def acquire(self): return Acquire()

    await _renew_claim(Pool(), claim)
    assert seen[0].args == (org, message, claim_id, 4, 300)


@pytest.mark.asyncio
@pytest.mark.parametrize('returned', [True, None, datetime.now(timezone.utc) - timedelta(seconds=1)])
async def test_worker_lease_renewal_fails_closed_without_live_server_timestamp(returned):
    class Conn:
        def is_in_transaction(self): return True
        def transaction(self, **kwargs): return Acquire()
        async def fetchval(self, query, *args):
            return returned if 'c24_worker_renew_job' in query else True
    class Acquire:
        async def __aenter__(self): return Conn()
        async def __aexit__(self, *args): return False
    class Pool:
        def acquire(self): return Acquire()

    with pytest.raises(WorkerProofDenied, match='lease lost'):
        await _renew_claim(Pool(), _Claim(uuid4(), uuid4(), uuid4(), 1))
