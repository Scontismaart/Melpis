import asyncio
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from src.core.gdpr.restore_checkpoint import RestoreBlocked
from src.core.gdpr.restore_policy_state import (
    capture_policy_state,
    reconcile_policy_state,
    verify_policy_state,
)


async def add_policy(conn, org, category, version):
    await conn.execute('''INSERT INTO gdpr_policies(organization_id,category,version,
        erasure_allowed,retention_days,legal_hold,approval_reference,approved_by,approved_at,valid_until)
        VALUES($1,$2,$3,true,30,false,'qa:policy','qa',now()-interval '1 day',now()+interval '1 day')''',
        org, category, version)


async def add_hold(conn, org, hold):
    await conn.execute('''INSERT INTO gdpr_retention_holds(organization_id,hold_id,category,scope,
        reason_code,authority_reference,approved_by,approved_at,review_by)
        VALUES($1,$2,'messages_content','organization','litigation','qa:authority','qa',
        now()-interval '1 hour',now()+interval '2 weeks')''', org, hold)


async def event(conn, org, hold, kind):
    await conn.execute('''INSERT INTO gdpr_retention_hold_events(organization_id,hold_id,event_id,kind,
        authority_reference,approved_by,occurred_at,next_review_by)
        VALUES($1,$2,$3,$4,'qa:authority','qa',clock_timestamp(),
        CASE WHEN $4='reviewed' THEN clock_timestamp()+interval '1 week' END)''', org, hold, uuid4(), kind)


def trust():
    return dict(installation_id=uuid4(), backup_id='synthetic-backup', verification_key=bytes(range(32)),
                encryption_key=Fernet.generate_key())


@pytest.mark.asyncio
@pytest.mark.parametrize('calendar_policy', [False, True])
@pytest.mark.migration_limit(105)
async def test_reconcile_fresh_immutable_governance_in_quarantine(pg_pool, sample_org, calendar_policy):
    org, other, hold = sample_org['id'], uuid4(), uuid4()
    args = trust()
    async with pg_pool.acquire() as conn:
        if calendar_policy:
            migration = Path('src/core/db/migrations/106_ordinary_booking_completion_evidence.sql')
            await conn.execute(migration.read_text(encoding='utf-8'))
        await conn.execute('INSERT INTO organizations(id,name) VALUES($1,\'other tenant\')', other)
        await add_policy(conn, org, 'messages_content', 'old')
        await add_policy(conn, other, 'messages_content', 'untouched')
        old_records = await conn.fetch('''SELECT organization_id,category,version,erasure_allowed,
            retention_days,legal_hold,approval_reference,approved_by,approved_at,valid_until FROM gdpr_policies''')
        async with conn.transaction(isolation='read_committed'):
            await add_policy(conn, org, 'technical_receipts', 'new')
            if calendar_policy:
                await conn.execute('''INSERT INTO gdpr_policies(organization_id,category,version,
                    erasure_allowed,retention_calendar_months,legal_hold,approval_reference,
                    approved_by,approved_at,valid_until)
                    VALUES($1,'completed_bookings_and_stays','civil',true,12,false,
                    'qa:civil','qa',now()-interval '1 day',now()+interval '1 day')''', org)
            await add_hold(conn, org, hold)
            await event(conn, org, hold, 'reviewed')
            await event(conn, org, hold, 'released')
        async with conn.transaction(isolation='read_committed', readonly=True):
            pin, sealed = await capture_policy_state(conn, org, sequence=2, **args)
        # Emulate older restored tables only in this disposable synthetic DB,
        # with the exact saved old rows (including original timestamps).
        await conn.execute('TRUNCATE gdpr_policies, gdpr_retention_holds, gdpr_retention_hold_events')
        await conn.executemany('''INSERT INTO gdpr_policies(organization_id,category,version,erasure_allowed,
            retention_days,legal_hold,approval_reference,approved_by,approved_at,valid_until)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)''', [tuple(row) for row in old_records])

        async with conn.transaction(isolation='read_committed'):
            with pytest.raises(RestoreBlocked):
                await verify_policy_state(conn, org, sealed, independent_pin=pin, **args)
            result = await reconcile_policy_state(conn, org, sealed, independent_pin=pin, **args)
            assert result['inserted'] == (5 if calendar_policy else 4)
            assert result['traffic_authorized'] is False
            assert (await verify_policy_state(conn, org, sealed, independent_pin=pin, **args))['traffic_authorized'] is False
            assert (await reconcile_policy_state(conn, org, sealed, independent_pin=pin, **args))['inserted'] == 0
            assert await conn.fetchval('SELECT count(*) FROM gdpr_policies WHERE organization_id=$1', org) == (3 if calendar_policy else 2)
            if calendar_policy:
                assert await conn.fetchval('''SELECT retention_calendar_months FROM gdpr_policies
                    WHERE organization_id=$1 AND category='completed_bookings_and_stays' ''', org) == 12
            assert await conn.fetchval('SELECT count(*) FROM gdpr_retention_hold_events WHERE organization_id=$1', org) == 2
            assert await conn.fetchval('SELECT version FROM gdpr_policies WHERE organization_id=$1', other) == 'untouched'


@pytest.mark.asyncio
async def test_policy_state_rejects_unknown_rows_wrong_pin_and_authentication(pg_pool, sample_org):
    org = sample_org['id']
    args = trust()
    async with pg_pool.acquire() as conn:
        await add_policy(conn, org, 'messages_content', 'old')
        async with conn.transaction(isolation='read_committed', readonly=True):
            pin, sealed = await capture_policy_state(conn, org, sequence=1, **args)
        await add_policy(conn, org, 'technical_receipts', 'unknown-extra')
        async with conn.transaction(isolation='read_committed'):
            for invalid_pin, invalid_data, invalid_args in (
                (replace(pin, sequence=2), sealed, args),
                (replace(pin, sha256='0' * 64), sealed, args),
                (pin, sealed, dict(args, installation_id=uuid4())),
                (pin, sealed, dict(args, backup_id='different-backup')),
                (pin, sealed, dict(args, verification_key=bytes(32))),
                (pin, sealed[:-1], args),
            ):
                with pytest.raises(RestoreBlocked):
                    await reconcile_policy_state(conn, org, invalid_data, independent_pin=invalid_pin, **invalid_args)
            with pytest.raises(RestoreBlocked):
                await reconcile_policy_state(conn, org, sealed, independent_pin=pin, **args)
            assert await conn.fetchval('SELECT count(*) FROM gdpr_policies WHERE organization_id=$1', org) == 2


@pytest.mark.asyncio
async def test_policy_capture_requires_owner_and_read_committed(pg_pool, sample_org):
    org = sample_org['id']
    args = trust()
    async with pg_pool.acquire() as conn:
        with pytest.raises(RestoreBlocked):
            await capture_policy_state(conn, org, sequence=0, **args)
        async with conn.transaction(isolation='repeatable_read'):
            with pytest.raises(RestoreBlocked):
                await capture_policy_state(conn, org, sequence=0, **args)
        async with conn.transaction(isolation='read_committed'):
            with pytest.raises(RestoreBlocked):
                await capture_policy_state(conn, org, sequence=0, **args)
        async with conn.transaction(isolation='read_committed', readonly=True):
            await conn.execute('SET LOCAL ROLE service_role')
            with pytest.raises(RestoreBlocked):
                await capture_policy_state(conn, org, sequence=0, **args)


@pytest.mark.asyncio
async def test_policy_reconciliation_rolls_back_if_later_hold_insert_is_rejected(pg_pool, sample_org):
    org, hold = sample_org['id'], uuid4()
    args = trust()
    async with pg_pool.acquire() as conn:
        await add_policy(conn, org, 'messages_content', 'new')
        await add_hold(conn, org, hold)
        async with conn.transaction(isolation='read_committed', readonly=True):
            pin, sealed = await capture_policy_state(conn, org, sequence=1, **args)
        # Exercise rollback after policy insertion followed by a real target
        # constraint error on the later hold INSERT. No partial result commits.
        await conn.execute('TRUNCATE gdpr_policies, gdpr_retention_holds, gdpr_retention_hold_events')
        await conn.execute("""ALTER TABLE gdpr_retention_holds ADD CONSTRAINT fixture_reject_litigation
            CHECK (reason_code <> 'litigation')""")
        async with conn.transaction(isolation='read_committed'):
            with pytest.raises(RestoreBlocked):
                await reconcile_policy_state(conn, org, sealed, independent_pin=pin, **args)
            assert not await conn.fetchval('SELECT EXISTS(SELECT FROM gdpr_policies WHERE organization_id=$1)', org)
            assert not await conn.fetchval('SELECT EXISTS(SELECT FROM gdpr_retention_holds WHERE organization_id=$1)', org)


@pytest.mark.asyncio
async def test_capture_waits_for_concurrent_hold_and_reads_committed_result(pg_pool, sample_org):
    org, hold = sample_org['id'], uuid4()
    args = trust()

    async def capture(conn):
        async with conn.transaction(isolation='read_committed', readonly=True):
            return await capture_policy_state(conn, org, sequence=1, **args)

    async with pg_pool.acquire() as writer, pg_pool.acquire() as reader, pg_pool.acquire() as observer:
        reader_pid = await reader.fetchval('SELECT pg_backend_pid()')
        task = None
        try:
            async with writer.transaction(isolation='read_committed'):
                await add_hold(writer, org, hold)
                task = asyncio.create_task(capture(reader))
                async with asyncio.timeout(5):
                    while not await observer.fetchval('''SELECT EXISTS(SELECT FROM pg_locks
                        WHERE pid=$1 AND locktype='advisory' AND NOT granted)''', reader_pid):
                        await asyncio.sleep(0.02)
            pin, sealed = await asyncio.wait_for(task, timeout=5)
            async with observer.transaction(isolation='read_committed'):
                result = await verify_policy_state(observer, org, sealed, independent_pin=pin, **args)
            assert result['records'] == 1 and result['traffic_authorized'] is False
        finally:
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
