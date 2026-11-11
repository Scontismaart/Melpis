"""Synthetic database proof of the external mutation uncertainty fence."""
from datetime import date, time
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import asyncpg
import pytest

from src.core.bookings.ports.base import (
    BookingSystemPort,
    CancelBookingRequest,
    CreateBookingRequest,
    CustomerResult,
)
from src.core.bookings.router import BookingAdapterRouter, BookingMode
from src.core.db.repositories.external_booking_repo import ExternalBookingRepository


@pytest.fixture
async def sync_repo(postgres_container):
    dsn = postgres_container.get_connection_url().replace('+psycopg2', '')
    schema = 'c24_sync_test_' + uuid4().hex
    admin = await asyncpg.connect(dsn=dsn)
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    await admin.execute(f'''CREATE TABLE "{schema}".external_booking_sync (
        id uuid PRIMARY KEY, organization_id uuid NOT NULL, idempotency_key text NOT NULL,
        provider text NOT NULL, internal_booking_id uuid, sync_status text NOT NULL,
        external_booking_id text, sync_error text, retry_count integer NOT NULL DEFAULT 0,
        last_attempt_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE (organization_id,idempotency_key))''')
    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=4,
                                     server_settings={'search_path': schema})
    try:
        yield ExternalBookingRepository(pool)
    finally:
        await pool.close()
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


def create_request(key):
    return CreateBookingRequest(idempotency_key=key, internal_booking_id=uuid4(),
        customer=CustomerResult(customer_id='synthetic', nome='Synthetic', telefono='123'),
        data=date(2027, 1, 1), ora_inizio=time(10))


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['create', 'cancel'])
async def test_post_timeout_keeps_tenant_scoped_uncertain_receipt(sync_repo, operation):
    org, other = uuid4(), uuid4()
    adapter = MagicMock(spec=BookingSystemPort)
    adapter.provider_name = 'synthetic'
    adapter.create_booking = AsyncMock(side_effect=TimeoutError('sensitive marker'))
    adapter.cancel_booking = AsyncMock(side_effect=TimeoutError('sensitive marker'))
    router = BookingAdapterRouter(repo=sync_repo)
    req = create_request('synthetic:create') if operation == 'create' else CancelBookingRequest(
        idempotency_key='synthetic:cancel', external_booking_id='synthetic-external')
    if operation == 'create':
        call = lambda tenant: router.dispatch_create_booking(tenant, req, adapter=adapter,
            mode=BookingMode.AUTHORITATIVE, verticale='ristorante')
        send = adapter.create_booking
    else:
        call = lambda tenant: router.dispatch_cancel_booking(tenant, req, adapter=adapter,
            mode=BookingMode.AUTHORITATIVE)
        send = adapter.cancel_booking

    first = await call(org)
    second = await call(org)
    assert not first.success and not second.success
    assert first.sync_status == second.sync_status == 'uncertain'
    send.assert_awaited_once()
    row = await sync_repo.get_sync_record(org, req.idempotency_key)
    assert row['sync_status'] == 'uncertain'
    assert row['sync_error'] == 'external_outcome_unverified'
    assert 'sensitive marker' not in row['sync_error']
    assert await sync_repo.get_sync_record(other, req.idempotency_key) is None
    # A stale worker or generic database write cannot fabricate reconciliation.
    assert await sync_repo.record_sync_success(org, req.idempotency_key, 'invented') is None
    assert (await sync_repo.get_sync_record(org, req.idempotency_key))['sync_status'] == 'uncertain'


@pytest.mark.asyncio
async def test_historical_failed_and_stale_sweep_do_not_rearm_or_overwrite(sync_repo):
    org, key, booking = uuid4(), 'synthetic:historical', uuid4()
    row, acquired = await sync_repo.claim_sync_slot(org, key, 'synthetic', booking)
    assert acquired and row['sync_status'] == 'pending'
    assert await sync_repo.record_sync_failure(org, key, 'legacy', sync_status='failed')
    row, acquired = await sync_repo.claim_sync_slot(org, key, 'synthetic', booking)
    assert not acquired and row['sync_status'] == 'failed'
    with pytest.raises(ValueError, match='scope mismatch'):
        await sync_repo.claim_sync_slot(org, key, 'other-provider', booking)

    complete_key = 'synthetic:complete'
    await sync_repo.claim_sync_slot(org, complete_key, 'synthetic', booking)
    assert await sync_repo.record_sync_success(org, complete_key, 'verified-external')
    # A sweeper that selected the earlier pending row loses against completion.
    assert await sync_repo.record_sync_failure(org, complete_key, 'stale', sync_status='uncertain') is None
    row = await sync_repo.get_sync_record(org, complete_key)
    assert (row['sync_status'], row['external_booking_id']) == ('synced', 'verified-external')
