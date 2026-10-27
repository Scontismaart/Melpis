"""Real PostgreSQL regressions for atomic booking mode changes."""
import asyncio
import json
from contextlib import asynccontextmanager
from uuid import uuid4

import asyncpg
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.routes.integrations import router
from src.core.auth.dependencies import get_current_user, get_organization_context
from src.core.db.repositories.external_booking_repo import ExternalBookingRepository

URL = '/api/v1/integrations/booking'


@pytest.fixture
async def realpg_pool(pg_pool, monkeypatch):
    monkeypatch.setenv('ENCRYPTION_KEY', Fernet.generate_key().decode())
    return pg_pool


def config(row):
    value = row['config']
    return json.loads(value) if isinstance(value, str) else value


async def row(pool, org):
    return dict(await pool.fetchrow('SELECT * FROM external_booking_credentials WHERE organization_id=$1', org))


async def setup(pool, org, dpa=True, provider='fake'):
    repo = ExternalBookingRepository(pool)
    await repo.save_credentials(org, provider, {'api_key': 'synthetic-secret'},
                                {'mode': 'shadow', 'medical_dpa_signed': dpa, 'nested': {'keep': [1, 2]}})
    return repo


class GatePool:
    """Pause after a read or before an update, regardless of the repository strategy."""
    def __init__(self, pool):
        self.pool = pool
        self.reached = asyncio.Event()
        self.release = asyncio.Event()
        self.paused = False

    @asynccontextmanager
    async def acquire(self):
        async with self.pool.acquire() as conn:
            outer = self

            class Proxy:
                async def fetchrow(self, sql, *args):
                    command = ' '.join(sql.split()).upper()
                    if not outer.paused and command.startswith('SELECT'):
                        result = await conn.fetchrow(sql, *args)
                        outer.paused = True
                        outer.reached.set()
                        await outer.release.wait()
                        return result
                    if not outer.paused and command.startswith('UPDATE'):
                        outer.paused = True
                        outer.reached.set()
                        await outer.release.wait()
                    return await conn.fetchrow(sql, *args)

                def __getattr__(self, name):
                    return getattr(conn, name)

            yield Proxy()


async def owner_revoke(pool, org):
    app = FastAPI()
    app.include_router(router)
    app.state.pool = pool
    identity = {'organization_id': str(org), 'ruolo': 'owner', 'source': 'jwt', 'aal': 'aal2'}

    async def verified_identity():
        return dict(identity)

    app.dependency_overrides[get_current_user] = verified_identity
    app.dependency_overrides[get_organization_context] = verified_identity
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post(URL, json={'provider': 'fake', 'credentials': {'api_key': 'owner-new'},
                                              'mode': 'shadow', 'config': {'medical_dpa_signed': False,
                                              'nested': {'keep': [1, 2]}, 'owner': 'changed'}})
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_owner_route_revokes_dpa_during_mode_change(realpg_pool, sample_org):
    pool, org = realpg_pool, sample_org['id']
    await setup(pool, org)
    gate = GatePool(pool)
    task = asyncio.create_task(ExternalBookingRepository(gate).update_mode(org, 'mirror'))
    try:
        async with asyncio.timeout(5):
            await gate.reached.wait()
            await owner_revoke(pool, org)
            owner_row = await row(pool, org)
            gate.release.set()
            result = await task
        final = await row(pool, org)
        assert result['config']['mode'] == 'mirror'
        assert config(final) == {**config(owner_row), 'mode': 'mirror'}
        assert config(final)['medical_dpa_signed'] is False
        for key in ('organization_id', 'provider', 'credentials_encrypted', 'is_active'):
            assert final[key] == owner_row[key]
    finally:
        gate.release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_mode_then_owner_revocation(realpg_pool, sample_org):
    pool, org = realpg_pool, sample_org['id']
    repo = await setup(pool, org)
    assert (await repo.update_mode(org, 'mirror'))['config']['mode'] == 'mirror'
    await owner_revoke(pool, org)
    assert config(await row(pool, org))['medical_dpa_signed'] is False


@pytest.mark.asyncio
async def test_mode_waits_for_concurrent_row_update(realpg_pool, sample_org):
    pool, org = realpg_pool, sample_org['id']
    repo = await setup(pool, org)
    async with pool.acquire() as owner, pool.acquire() as manager, pool.acquire() as observer:
        tx = owner.transaction()
        await tx.start()
        task = None
        committed = False
        try:
            owner_pid = await owner.fetchval('SELECT pg_backend_pid()')
            manager_pid = await manager.fetchval('SELECT pg_backend_pid()')
            await owner.execute("UPDATE external_booking_credentials SET config=jsonb_set(config, '{medical_dpa_signed}', 'false'::jsonb) || '{\"owner\":\"changed\"}'::jsonb WHERE organization_id=$1", org)
            class OneConnection:
                @asynccontextmanager
                async def acquire(self):
                    yield manager
            task = asyncio.create_task(ExternalBookingRepository(OneConnection()).update_mode(org, 'mirror'))
            async with asyncio.timeout(5):
                while owner_pid not in await observer.fetchval('SELECT pg_blocking_pids($1)', manager_pid):
                    assert not task.done(), 'mode update did not wait for owner lock'
                    await asyncio.sleep(0)
                await tx.commit()
                committed = True
                result = await task
            assert result['config']['medical_dpa_signed'] is False
            final = await row(pool, org)
            assert config(final) == {'mode': 'mirror', 'medical_dpa_signed': False,
                                     'nested': {'keep': [1, 2]}, 'owner': 'changed'}
            original = await repo.get_credentials(org)
            assert original['credentials'] == {'api_key': 'synthetic-secret'}
            assert final['provider'] == 'fake' and final['is_active'] is True and final['organization_id'] == org
        finally:
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if not committed:
                await tx.rollback()


@pytest.mark.asyncio
async def test_tenant_isolation_and_concurrent_modes(realpg_pool, sample_org, other_org):
    pool, a, b = realpg_pool, sample_org['id'], other_org['id']
    repo = await setup(pool, a)
    await setup(pool, b, False)
    before_a, before_b = await row(pool, a), await row(pool, b)
    results = await asyncio.gather(repo.update_mode(a, 'mirror'), repo.update_mode(a, 'authoritative'))
    assert all(result is not None for result in results)
    after = await row(pool, a)
    assert config(after)['mode'] in {'mirror', 'authoritative'}
    assert {k: v for k, v in config(after).items() if k != 'mode'} == {k: v for k, v in config(before_a).items() if k != 'mode'}
    for key in ('organization_id', 'provider', 'credentials_encrypted', 'is_active'):
        assert after[key] == before_a[key]
    assert await row(pool, b) == before_b


@pytest.mark.asyncio
@pytest.mark.parametrize('dpa', [True, False, 'true', 'false'])
async def test_mode_retries_preserve_dpa_type_and_credentials(realpg_pool, sample_org, dpa):
    pool, org = realpg_pool, sample_org['id']
    repo = await setup(pool, org, dpa)
    before = await row(pool, org)
    for mode in ('authoritative', 'shadow', 'mirror', 'local_only', 'local_only'):
        result = await repo.update_mode(org, mode)
        assert result['config']['mode'] == mode
        assert result['config']['medical_dpa_signed'] == dpa
        assert type(result['config']['medical_dpa_signed']) is type(dpa)
    after = await row(pool, org)
    assert after['credentials_encrypted'] == before['credentials_encrypted']
    assert config(after)['nested'] == {'keep': [1, 2]}


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['zak', 'wubook', 'wubook_zak', 'ZaK'])
async def test_provider_mode_restrictions(realpg_pool, sample_org, provider):
    pool, org = realpg_pool, sample_org['id']
    repo = await setup(pool, org, provider=provider)
    for mode in ('shadow', 'local_only'):
        assert (await repo.update_mode(org, mode))['config']['mode'] == mode
    before = await row(pool, org)
    for mode in ('authoritative', 'mirror'):
        assert await repo.update_mode(org, mode) is None
        assert await row(pool, org) == before


@pytest.mark.asyncio
async def test_invalid_mode_and_missing_row(realpg_pool, sample_org):
    repo = ExternalBookingRepository(realpg_pool)
    assert await repo.update_mode(uuid4(), 'shadow') is None
    await setup(realpg_pool, sample_org['id'])
    before = await row(realpg_pool, sample_org['id'])
    with pytest.raises(ValueError):
        await repo.update_mode(sample_org['id'], 'invalid')
    assert await row(realpg_pool, sample_org['id']) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', ['null', '[]', '"scalar"', '42', 'true'])
async def test_malformed_config_fails_closed(realpg_pool, sample_org, bad):
    pool, org = realpg_pool, sample_org['id']
    repo = await setup(pool, org)
    await pool.execute('UPDATE external_booking_credentials SET config=$2::jsonb WHERE organization_id=$1', org, bad)
    before = await row(pool, org)
    with pytest.raises(asyncpg.PostgresError):
        await repo.update_mode(org, 'shadow')
    assert await row(pool, org) == before


@pytest.mark.asyncio
async def test_empty_object_config_is_valid(realpg_pool, sample_org):
    pool, org = realpg_pool, sample_org['id']
    repo = await setup(pool, org)
    await pool.execute("UPDATE external_booking_credentials SET config='{}'::jsonb WHERE organization_id=$1", org)
    assert (await repo.update_mode(org, 'shadow'))['config'] == {'mode': 'shadow'}



@pytest.mark.asyncio
async def test_provider_change_while_mode_update_waits(realpg_pool, sample_org):
    pool, org = realpg_pool, sample_org['id']
    await setup(pool, org)
    before = await row(pool, org)
    async with pool.acquire() as owner, pool.acquire() as manager, pool.acquire() as observer:
        tx = owner.transaction()
        await tx.start()
        task = None
        committed = False
        try:
            owner_pid = await owner.fetchval('SELECT pg_backend_pid()')
            manager_pid = await manager.fetchval('SELECT pg_backend_pid()')
            await owner.execute("UPDATE external_booking_credentials SET provider='zak' WHERE organization_id=$1", org)

            class OneConnection:
                @asynccontextmanager
                async def acquire(self):
                    yield manager

            task = asyncio.create_task(ExternalBookingRepository(OneConnection()).update_mode(org, 'authoritative'))
            async with asyncio.timeout(5):
                while owner_pid not in await observer.fetchval('SELECT pg_blocking_pids($1)', manager_pid):
                    assert not task.done(), 'mode update did not wait for provider change'
                    await asyncio.sleep(0)
                await tx.commit()
                committed = True
                assert await task is None
            after = await row(pool, org)
            assert after['provider'] == 'zak'
            assert config(after) == config(before)
            for key in ('organization_id', 'credentials_encrypted', 'is_active'):
                assert after[key] == before[key]
        finally:
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if not committed:
                await tx.rollback()


@pytest.mark.asyncio
async def test_mode_route_reports_provider_race_as_conflict(realpg_pool, sample_org, monkeypatch):
    import src.api.routes.integrations as integrations

    pool, org = realpg_pool, sample_org['id']
    repo = await setup(pool, org)
    before = await row(pool, org)

    class RacingRepository:
        async def get_credentials(self, organization_id):
            return await repo.get_credentials(organization_id)

        async def update_mode(self, organization_id, mode):
            await pool.execute("UPDATE external_booking_credentials SET provider='zak' WHERE organization_id=$1", org)
            return await repo.update_mode(organization_id, mode)

    monkeypatch.setattr(integrations, 'get_external_booking_repo', lambda request: RacingRepository())
    app = FastAPI()
    app.include_router(router)
    app.state.pool = pool
    identity = {'organization_id': str(org), 'ruolo': 'manager', 'source': 'jwt', 'aal': 'aal1'}

    async def verified_identity():
        return dict(identity)

    app.dependency_overrides[get_current_user] = verified_identity
    app.dependency_overrides[get_organization_context] = verified_identity
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.patch(URL + '/mode', json={'mode': 'authoritative'})
    assert response.status_code == 409
    assert response.json().get('success') is not True
    after = await row(pool, org)
    assert after['provider'] == 'zak'
    assert config(after) == config(before)
    assert after['credentials_encrypted'] == before['credentials_encrypted']
