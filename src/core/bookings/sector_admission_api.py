"""Explicit owner activation of the permanent sector admission fence."""
import hashlib
from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from src.core.auth.dependencies import require_mfa, require_ruolo
from src.core.bookings.catalog_api import parse_body, repository
from src.core.calendar.connection import authority_from
from src.core.db.repositories.booking_catalog_repo import canonical, decode

router = APIRouter()


class SectorAdmissionRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', hide_input_in_errors=True)
    request_id: UUID
    expected_published_revision: Annotated[int, Field(strict=True, gt=0)]
    enabled: bool


@router.get('/sector-admission')
async def sector_admission_status(request: Request, user: dict = Depends(require_ruolo('owner'))):
    """Expose only the tenant's published opt-in state to its owner."""
    repo = repository(request)
    try:
        org = UUID(user['organization_id'])
        state = await repo.read(org)
        async with repo.booking_repo._connection() as conn:
            row = await conn.fetchrow('SELECT config FROM booking_settings WHERE organization_id=$1', org)
        config = decode(row['config']) if row else {}
        policy = config.get('c24_sector_auto') if isinstance(config, dict) else None
        sector = state.get('organization_sector')
        valid_policy = (isinstance(policy, dict)
            and set(policy) == {'enabled', 'version', 'sector', 'authority'}
            and policy.get('enabled') is True and type(policy.get('version')) is int
            and policy.get('version') == 1 and policy.get('sector') == sector
            and policy.get('authority') == 'internal')
        return {
            'enabled': bool(valid_policy and state.get('published_revision') is not None),
            'eligible': sector in {'parrucchiere', 'centro_estetico', 'hotel_bnb', 'studio_medico_dentista'},
            'published_revision': state.get('published_revision'),
        }
    except (ValueError, TypeError, KeyError):
        raise HTTPException(422, 'Admission status unavailable') from None
    except (asyncpg.PostgresError, OSError, TimeoutError):
        raise HTTPException(503, 'Admission status unavailable') from None


@router.put('/sector-admission')
async def sector_admission(request: Request, user: dict = Depends(require_ruolo('owner')),
                           mfa: dict = Depends(require_mfa())):
    body = await parse_body(request, SectorAdmissionRequest)
    repo = repository(request)
    try:
        org, actor, auth = map(UUID, (user['organization_id'], user['user_id'], user['auth_user_id']))
        digest = hashlib.sha256(canonical({'actor': str(actor), 'enabled': body.enabled,
            'expected_published_revision': body.expected_published_revision}).encode()).hexdigest()
        async with repo.booking_repo.booking_settings_lock(org), repo.booking_repo._connection() as conn:
            membership = await conn.fetchrow('''SELECT up.id FROM organization_memberships om
                JOIN user_profiles up ON up.id=om.user_id
                JOIN auth_access_lifecycle a ON a.auth_user_id=up.auth_user_id
                WHERE om.organization_id=$1 AND up.auth_user_id=$2 AND up.id=$3
                  AND om.ruolo='owner' AND om.joined_at IS NOT NULL AND a.disabled_at IS NULL
                FOR SHARE OF om,up,a''', org, auth, actor)
            if not membership:
                raise HTTPException(403, 'Owner membership required')
            row = await conn.fetchrow('SELECT id,config FROM booking_settings WHERE organization_id=$1 FOR UPDATE', org)
            if not row:
                raise HTTPException(409, 'Published catalog required')
            config = decode(row['config'])
            state = repo._state(config, org)
            await repo._verify_evidence(conn, org, state)
            previous = await conn.fetchrow('''SELECT payload_hash,enabled,published_revision
                FROM booking_sector_admission_requests WHERE organization_id=$1 AND request_id=$2''', org, body.request_id)
            if previous:
                if previous['payload_hash'] != digest:
                    raise HTTPException(409, 'Request identifier already used')
                return {'enabled': previous['enabled'], 'published_revision': previous['published_revision'], 'replayed': True}
            sector = await repo._sector(conn, org)
            if (state['published'] is None or state['published_revision'] != body.expected_published_revision
                    or sector not in {'parrucchiere', 'centro_estetico', 'hotel_bnb', 'studio_medico_dentista'}
                    or state['published']['sector'] != sector):
                raise HTTPException(409, 'Published catalog or sector changed')
            if authority_from({'config': config}) != 'internal':
                raise HTTPException(409, 'Internal authority required')
            fence = await conn.fetchrow('SELECT sector FROM booking_sector_admission_fences WHERE organization_id=$1', org)
            if fence and fence['sector'] != sector:
                raise HTTPException(409, 'Admission fence sector changed')
            if body.enabled and not fence:
                # The SQL function validates empty history and serializes against legacy writers.
                try:
                    await conn.execute('SELECT public.booking_sector_activate_empty($1,$2)', org, sector)
                except asyncpg.RaiseError:
                    raise HTTPException(409, 'Existing booking history prevents activation') from None
            policy = {'enabled': body.enabled, 'version': 1, 'sector': sector, 'authority': 'internal'}
            await conn.execute("""UPDATE booking_settings SET config=jsonb_set(config,'{c24_sector_auto}',$2::jsonb,true),
                updated_at=NOW() WHERE organization_id=$1""", org, canonical(policy))
            await conn.execute('''INSERT INTO booking_sector_admission_requests
                (organization_id,request_id,actor_user_id,payload_hash,published_revision,enabled)
                VALUES($1,$2,$3,$4,$5,$6)''', org, body.request_id, actor, digest,
                body.expected_published_revision, body.enabled)
            await conn.execute('''INSERT INTO audit_log
                (organization_id,user_id,auth_user_id,action,target_table,target_id,details)
                VALUES($1,$2,$3,'booking_sector_admission.changed','booking_settings',$4,$5::jsonb)''',
                org, actor, str(auth), row['id'], canonical({'request_id': str(body.request_id),
                'published_revision': body.expected_published_revision, 'enabled': body.enabled}))
            return {'enabled': body.enabled, 'published_revision': body.expected_published_revision, 'replayed': False}
    except HTTPException:
        raise
    except (ValueError, TypeError, KeyError):
        raise HTTPException(422, 'Invalid admission request') from None
    except (asyncpg.PostgresError, OSError, TimeoutError):
        raise HTTPException(503, 'Admission unavailable; retry the same request') from None
