"""Synthetic organization erasure revokes tenant grants without touching shared Auth."""
from uuid import UUID, uuid4
from types import SimpleNamespace

import asyncpg
import pytest

from src.core.db.repository import CoreRepository
from src.core.db.repositories.organization_repo import OrganizationRepository
from src.api.routes.team import list_my_organizations
from src.core.gdpr.deletion import Deletion
from src.core.gdpr.guard import ErasureBlocked
from src.core.gdpr.operations import Operations
from src.core.gdpr.restore_checkpoint import RestoreBlocked, _verify_replayed_erasures
from tests.core.auth.test_access_revocation import add_membership, create_auth_user
from tests.core.gdpr.test_deletion_a06 import approve_policies
from tests.core.gdpr.test_scoped_erasure_holds import add_hold, release_hold


async def _invite(pool, org, profile):
    invite = uuid4()
    await pool.execute("""INSERT INTO team_invitations(id,organization_id,email,ruolo,token_hash,invited_by,expires_at)
        VALUES($1,$2,$3,'staff',$4,$5,NOW()+interval '1 day')""", invite, org,
        f'{invite}@example.invalid', invite.hex * 2, profile)
    return invite


async def _setup_two_tenants(pool, org, other):
    shared, profile = await create_auth_user(pool)
    solo, solo_profile = await create_auth_user(pool)
    await add_membership(pool, org, profile, 'owner')
    await add_membership(pool, other, profile, 'owner')
    await add_membership(pool, org, solo_profile, 'staff')
    await _invite(pool, org, profile)
    other_invite = await _invite(pool, other, profile)
    return shared, profile, solo, solo_profile, other_invite


@pytest.mark.asyncio
async def test_organization_erasure_revokes_only_its_memberships_and_invites(pg_pool, sample_org, other_org, monkeypatch):
    org, other = sample_org['id'], other_org['id']
    shared, profile, solo, solo_profile, other_invite = await _setup_two_tenants(pg_pool, org, other)
    await approve_policies(pg_pool, org, organization=True)
    monkeypatch.setenv('GDPR_ERASURE_EXECUTION_ENABLED', 'true')

    request_id = uuid4()
    deletion = Deletion(pg_pool)
    async with pg_pool.acquire() as erasure, erasure.transaction():
        await erasure.execute('SELECT pg_advisory_xact_lock(hashtextextended($1::text,606))', str(org))
        with pytest.raises(asyncpg.LockNotAvailableError):
            await pg_pool.execute("UPDATE organization_memberships SET ruolo='manager' WHERE organization_id=$1 AND user_id=$2", org, solo_profile, timeout=2)
        await pg_pool.execute("UPDATE organization_memberships SET ruolo='owner' WHERE organization_id=$1 AND user_id=$2", other, profile, timeout=2)
    result = await deletion.request(org, shared, request_id)
    assert result['status'] == 'partial'
    assert result['manifest']['tenant_access'] == 'revoked'
    assert result['manifest']['shared_auth'] == 'retained'
    assert await pg_pool.fetchval('SELECT count(*) FROM organization_memberships WHERE organization_id=$1', org) == 0
    assert await pg_pool.fetchval('SELECT count(*) FROM team_invitations WHERE organization_id=$1', org) == 0
    assert await pg_pool.fetchval('SELECT count(*) FROM team_invitations WHERE organization_id=$1 AND id=$2', other, other_invite) == 1
    assert await pg_pool.fetchval('SELECT count(*) FROM auth.users WHERE id=ANY($1::uuid[])', [shared, solo]) == 2
    assert await pg_pool.fetchval('SELECT count(*) FROM user_profiles WHERE id=ANY($1::uuid[])', [profile, solo_profile]) == 2
    repo = CoreRepository(pool=pg_pool)
    assert await repo.get_membership_by_auth(str(shared), str(org)) is None
    assert {row['organization_id'] for row in await repo.get_memberships_by_auth(str(shared))} == {other}
    assert await repo.get_auth_access_allowed(str(shared)) is True
    assert await repo.get_auth_access_allowed(str(solo)) is False

    with pytest.raises(PermissionError):
        await repo.get_or_create_organization_with_owner(str(solo), 'No automatic reprovisioning')
    with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError):
        await add_membership(pg_pool, org, profile)
    assert (await deletion.request(org, shared, request_id))['id'] == result['id']


@pytest.mark.asyncio
async def test_legacy_committed_org_access_is_denied_and_reconciled_only_under_current_policy(pg_pool, sample_org, other_org, monkeypatch):
    org, other = sample_org['id'], other_org['id']
    shared, profile, solo, _, _ = await _setup_two_tenants(pg_pool, org, other)
    await approve_policies(pg_pool, org, organization=True)
    oid = await Operations(pg_pool).create(org, shared, uuid4(), kind='erase', scope='organization')
    await pg_pool.execute("UPDATE gdpr_operations SET status='partial' WHERE organization_id=$1 AND id=$2", org, oid)
    await pg_pool.execute("INSERT INTO gdpr_erasure_barriers(organization_id,operation_id,scope) VALUES($1,$2,'organization')", org, oid)
    await pg_pool.execute("INSERT INTO gdpr_operation_steps(organization_id,operation_id,name,status) VALUES($1,$2,'local','confirmed')", org, oid)
    repo = CoreRepository(pool=pg_pool)
    assert await repo.get_membership_by_auth(str(shared), str(org)) is None
    assert {row['organization_id'] for row in await repo.get_memberships_by_auth(str(shared))} == {other}
    assert await repo.get_auth_access_allowed(str(solo)) is False
    org_repo = OrganizationRepository(pg_pool)
    assert await org_repo.get_organization_owners(str(org)) == []
    assert await org_repo.list_team_members(str(org)) == []
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(pool=pg_pool)))
    result = await list_my_organizations(request, {'source': 'jwt', 'auth_user_id': str(shared)})
    assert {row['id'] for row in result['organizations']} == {str(other)}

    async with pg_pool.acquire() as conn, conn.transaction():
        with pytest.raises(RestoreBlocked, match='access links remain'):
            await _verify_replayed_erasures(conn, org, [(oid, 'organization', None)], 1,
                purges=[], receipt_prunes=[], ordinary_purges=[], require_committed=True)

    # Actual RLS caller identity and an old retained membership cannot expose
    # the erased tenant, while a valid membership in the other tenant remains.
    async with pg_pool.acquire() as conn, conn.transaction():
        await conn.execute("""CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS $$
            SELECT NULLIF(current_setting('request.jwt.claim.sub',true),'')::uuid $$""")
        await conn.execute('GRANT USAGE ON SCHEMA public,auth TO authenticated')
        await conn.execute('GRANT SELECT ON organization_memberships,user_profiles TO authenticated')
        await conn.execute('SET LOCAL ROLE authenticated')
        await conn.execute("SELECT set_config('request.jwt.claim.sub',$1,true)", str(shared))
        assert await conn.fetchval('SELECT current_auth_access_allowed()') is True
        assert await conn.fetchval('SELECT count(*) FROM organization_memberships WHERE organization_id=$1', org) == 0
        assert await conn.fetchval('SELECT count(*) FROM organization_memberships WHERE organization_id=$1', other) == 1
        await conn.execute("SELECT set_config('request.jwt.claim.sub',$1,true)", str(solo))
        assert await conn.fetchval('SELECT current_auth_access_allowed()') is False

    deletion = Deletion(pg_pool)
    monkeypatch.setenv('GDPR_ERASURE_EXECUTION_ENABLED', 'false')
    with pytest.raises(ErasureBlocked):
        await deletion.advance(org, oid, shared)
    monkeypatch.setenv('GDPR_ERASURE_EXECUTION_ENABLED', 'true')
    hold = await add_hold(pg_pool, org, 'organization', None)
    with pytest.raises(ErasureBlocked):
        await deletion.advance(org, oid, shared)
    assert await pg_pool.fetchval('SELECT count(*) FROM organization_memberships WHERE organization_id=$1', org) == 2
    await release_hold(pg_pool, org, hold)
    await deletion.advance(org, oid, shared)
    assert await pg_pool.fetchval('SELECT count(*) FROM organization_memberships WHERE organization_id=$1', org) == 0
    assert await pg_pool.fetchval('SELECT count(*) FROM team_invitations WHERE organization_id=$1', org) == 0
    status = await Operations(pg_pool).get(org, UUID(str(oid)), shared)
    assert status['manifest']['tenant_access'] == 'revoked'
    assert await repo.get_auth_access_allowed(str(shared)) is True
    assert await pg_pool.fetchval('SELECT count(*) FROM organization_memberships WHERE organization_id=$1 AND user_id=$2', other, profile) == 1
    async with pg_pool.acquire() as conn, conn.transaction():
        await _verify_replayed_erasures(conn, org, [(oid, 'organization', None)], 1,
            purges=[], receipt_prunes=[], ordinary_purges=[], require_committed=True)
