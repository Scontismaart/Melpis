"""Deterministic PostgreSQL races, never live customer/provider data."""
import asyncio
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from src.core.gdpr.deletion import Deletion
from src.core.gdpr.exporter import Exporter
from tests.core.gdpr.test_deletion_a06 import approve_policies, seed_contact_data


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setenv('ENCRYPTION_KEY', Fernet.generate_key().decode())
    monkeypatch.setenv('GDPR_ERASURE_EXECUTION_ENABLED', 'true')


async def waiting(pool, fragment):
    async with asyncio.timeout(5):
        while True:
            async with pool.acquire() as conn:
                found = await conn.fetchval(
                    '''SELECT EXISTS(SELECT 1 FROM pg_stat_activity
                       WHERE datname=current_database() AND wait_event_type='Lock'
                       AND query LIKE $1)''', '%' + fragment + '%',
                )
            if found:
                return
            await asyncio.sleep(.02)


async def install_pause(conn, table, key):
    # Only test-owned objects in a fresh synthetic fixture database.
    await conn.execute(f'''CREATE FUNCTION qa_a06_pause() RETURNS TRIGGER AS $$
        BEGIN PERFORM pg_advisory_xact_lock({key}); RETURN NEW; END;
        $$ LANGUAGE plpgsql''')
    await conn.execute(f'CREATE TRIGGER qa_a06_pause BEFORE INSERT ON {table} FOR EACH ROW EXECUTE FUNCTION qa_a06_pause()')
    await conn.execute('SELECT pg_advisory_lock($1)', key)


async def release_pause(conn, table, key):
    await conn.execute('SELECT pg_advisory_unlock($1)', key)
    await conn.execute(f'DROP TRIGGER IF EXISTS qa_a06_pause ON {table}')
    await conn.execute('DROP FUNCTION IF EXISTS qa_a06_pause()')


async def test_export_waiting_for_erasure_uses_post_erasure_snapshot(pg_pool, sample_org):
    org = sample_org['id']
    subject, _, _, _ = await seed_contact_data(pg_pool, org, phone='+15558880101')
    await approve_policies(pg_pool, org)
    export_task = erase_task = None
    async with pg_pool.acquire() as control:
        await install_pause(control, 'gdpr_erasure_barriers', 707)
        try:
            erase_task = asyncio.create_task(Deletion(pg_pool).request(
                org, 'synthetic-controller', uuid4(), scope='contact', subject_id=subject,
            ))
            await waiting(pg_pool, 'INSERT INTO gdpr_erasure_barriers')
            export_task = asyncio.create_task(Exporter(pg_pool).prepare(org, str(uuid4())))
            await waiting(pg_pool, 'pg_advisory_lock_shared')
            assert not export_task.done()
            await control.execute('SELECT pg_advisory_unlock($1)', 707)
            await asyncio.wait_for(erase_task, 5)
            result = await asyncio.wait_for(export_task, 5)
            async with pg_pool.acquire() as conn:
                requester = await conn.fetchval(
                    'SELECT requester FROM gdpr_operations WHERE organization_id=$1 AND id=$2', org, result['export_id'],
                )
            body = b''.join([chunk async for chunk in Exporter(pg_pool).stream(org, result['export_id'], requester)])
            assert b'+15558880101' not in body
            assert b'synthetic private message body' not in body
        finally:
            await control.execute('SELECT pg_advisory_unlock($1)', 707)
            for task in (erase_task, export_task):
                if task and not task.done():
                    task.cancel()
            await asyncio.gather(*(t for t in (erase_task, export_task) if t), return_exceptions=True)
            await release_pause(control, 'gdpr_erasure_barriers', 707)


async def test_erasure_waits_for_snapshot_then_revokes_its_chunks(pg_pool, sample_org):
    org, requester = sample_org['id'], str(uuid4())
    subject, _, _, _ = await seed_contact_data(pg_pool, org, phone='+15558880102')
    await approve_policies(pg_pool, org)
    export_task = erase_task = None
    async with pg_pool.acquire() as control:
        await install_pause(control, 'gdpr_export_chunks', 708)
        try:
            export_task = asyncio.create_task(Exporter(pg_pool).prepare(org, requester))
            await waiting(pg_pool, 'INSERT INTO gdpr_export_chunks')
            erase_task = asyncio.create_task(Deletion(pg_pool).request(
                org, 'synthetic-controller', uuid4(), scope='contact', subject_id=subject,
            ))
            await waiting(pg_pool, 'pg_advisory_xact_lock(hashtextextended')
            assert not erase_task.done()
            await control.execute('SELECT pg_advisory_unlock($1)', 708)
            descriptor = await asyncio.wait_for(export_task, 5)
            await asyncio.wait_for(erase_task, 5)
            with pytest.raises(LookupError):
                await Exporter(pg_pool).preflight(org, descriptor['export_id'], requester)
            async with pg_pool.acquire() as conn:
                assert await conn.fetchval('SELECT count(*) FROM gdpr_export_chunks WHERE organization_id=$1', org) == 0
        finally:
            await control.execute('SELECT pg_advisory_unlock($1)', 708)
            for task in (erase_task, export_task):
                if task and not task.done():
                    task.cancel()
            await asyncio.gather(*(t for t in (erase_task, export_task) if t), return_exceptions=True)
            await release_pause(control, 'gdpr_export_chunks', 708)


async def test_concurrent_same_export_request_has_one_snapshot(pg_pool, sample_org):
    org, requester, request_id = sample_org['id'], str(uuid4()), uuid4()
    results = await asyncio.gather(*(
        Exporter(pg_pool).prepare(org, requester, request_id=request_id) for _ in range(8)
    ))
    assert len({r['export_id'] for r in results}) == 1
    assert all(r['manifest'] == results[0]['manifest'] for r in results)
    async with pg_pool.acquire() as conn:
        assert await conn.fetchval(
            "SELECT count(*) FROM gdpr_operation_events WHERE organization_id=$1 AND event_type='export.ready'", org,
        ) == 1
