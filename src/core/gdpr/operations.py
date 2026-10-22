"""Durable GDPR requests and fenced provider evidence. No implicit execution."""
import hashlib
import json
import re
from datetime import datetime
from uuid import UUID, uuid4


class RequestConflict(ValueError):
    pass


class OperationUnavailable(RuntimeError):
    pass


def encode(value):
    return json.dumps(value, default=str, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def document(value):
    return json.loads(value) if isinstance(value, str) else value


def verified_receipt(receipt, org, oid, provider):
    return (isinstance(receipt, dict) and receipt.get('operation_id') == str(oid)
            and receipt.get('organization_id') == str(org) and receipt.get('provider') == provider
            and receipt.get('status') == 'deleted' and isinstance(receipt.get('evidence_digest'), str)
            and re.fullmatch(r'[a-f0-9]{64}', receipt['evidence_digest']) is not None)


async def refresh_provider_coverage(conn, org, oid):
    rows = await conn.fetch(
        '''SELECT name,status FROM gdpr_operation_steps
           WHERE organization_id=$1 AND operation_id=$2 AND name LIKE 'provider.%' ORDER BY name''', org, oid,
    )
    state = 'confirmed' if rows and all(r['status'] == 'confirmed' for r in rows) else 'pending_verified_receipts'
    # Confirmed providers alone cannot override retained-record/subject/legal
    # review. Final completion requires a separately approved retention decision.
    await conn.execute(
        '''UPDATE gdpr_operations SET manifest=manifest || $3::jsonb,updated_at=NOW()
           WHERE organization_id=$1 AND id=$2 AND kind='erase' ''',
        org, oid, encode({'external_copies': state, 'provider_status': {r['name']: r['status'] for r in rows}}),
    )


async def event(conn, org, operation_id, code, **details):
    # The only accepted details are technical codes/counts/digests, not free text.
    allowed = {'count', 'fence', 'attempts', 'category', 'reason', 'digest', 'provider'}
    if set(details) - allowed:
        raise ValueError('Unsupported GDPR audit detail')
    for key, value in details.items():
        if not isinstance(value, int) and not (
            isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', value)
        ):
            raise ValueError('Invalid GDPR audit value')
    await conn.execute(
        '''INSERT INTO gdpr_operation_events(organization_id,operation_id,event_type,details)
           VALUES($1,$2,$3,$4::jsonb)''', org, operation_id, code, encode(details),
    )


class Operations:
    def __init__(self, pool):
        self.pool = pool

    async def create(self, organization_id, requester, request_id, *, kind, scope, subject_id=None):
        org, request_id = UUID(str(organization_id)), UUID(str(request_id))
        subject = UUID(str(subject_id)) if subject_id else None
        if kind not in {'export', 'erase'} or scope not in {'organization', 'contact', 'account'}:
            raise ValueError('Invalid GDPR request')
        if (scope == 'contact') != (subject is not None):
            raise ValueError('Contact scope requires exactly one contact identifier')
        request_hash = hashlib.sha256(encode([kind, scope, str(subject)]).encode()).hexdigest()
        async with self.pool.acquire() as conn, conn.transaction():
            if subject and not await conn.fetchval(
                'SELECT id FROM contacts WHERE organization_id=$1 AND id=$2', org, subject,
            ):
                raise LookupError('Subject not found')
            oid = uuid4()
            result = await conn.execute(
                '''INSERT INTO gdpr_operations(id,organization_id,requester,request_id,request_hash,kind,scope,subject_id,status)
                   VALUES($1,$2,$3,$4,$5,$6,$7,$8,'requested')
                   ON CONFLICT(organization_id,requester,request_id) DO NOTHING''',
                oid, org, str(requester), request_id, request_hash, kind, scope, subject,
            )
            row = await conn.fetchrow(
                '''SELECT id,request_hash FROM gdpr_operations
                   WHERE organization_id=$1 AND requester=$2 AND request_id=$3 FOR UPDATE''', org, str(requester), request_id,
            )
            if row['request_hash'] != request_hash:
                raise RequestConflict('Request identifier already used with different scope')
            if result == 'INSERT 0 1':
                await event(conn, org, row['id'], 'requested')
            return row['id']

    async def get(self, org, operation_id, requester):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                '''SELECT id,kind,scope,status,manifest,expires_at,created_at,updated_at
                   FROM gdpr_operations WHERE organization_id=$1 AND id=$2 AND requester=$3''',
                UUID(str(org)), UUID(str(operation_id)), str(requester),
            )
            if not row:
                raise LookupError('GDPR operation not found')
            steps = await conn.fetch(
                '''SELECT name,status,attempts,fence,updated_at FROM gdpr_operation_steps
                   WHERE organization_id=$1 AND operation_id=$2 ORDER BY name''', UUID(str(org)), UUID(str(operation_id)),
            )
        result = dict(row)
        result['manifest'] = document(result['manifest'])
        result['steps'] = [dict(s) for s in steps]
        return result

    async def claim_provider(self, org, operation_id, name):
        """Reserve before HTTP. Expired workers become uncertain, never unsent."""
        org, oid = UUID(str(org)), UUID(str(operation_id))
        if not re.fullmatch(r'provider\.[a-z][a-z0-9_.-]{0,65}', name):
            raise ValueError('Invalid provider stage')
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.fetchrow('SELECT id FROM gdpr_operations WHERE organization_id=$1 AND id=$2 FOR UPDATE', org, oid)
            await conn.execute(
                '''INSERT INTO gdpr_operation_steps(organization_id,operation_id,name)
                   VALUES($1,$2,$3) ON CONFLICT DO NOTHING''', org, oid, name,
            )
            row = await conn.fetchrow(
                '''SELECT status,fence,lease_until,lease_until>NOW() AS live FROM gdpr_operation_steps
                   WHERE organization_id=$1 AND operation_id=$2 AND name=$3 FOR UPDATE''', org, oid, name,
            )
            if row['status'] == 'running':
                if not row['live']:
                    await conn.execute(
                        '''UPDATE gdpr_operation_steps SET status='uncertain',fence=fence+1,updated_at=NOW()
                           WHERE organization_id=$1 AND operation_id=$2 AND name=$3''', org, oid, name,
                    )
                    await event(conn, org, oid, 'provider.uncertain', provider=name)
                return None
            if row['status'] != 'pending':
                return None
            fence = await conn.fetchval(
                '''UPDATE gdpr_operation_steps SET status='running',fence=fence+1,attempts=attempts+1,
                       lease_until=NOW()+INTERVAL '30 seconds',updated_at=NOW()
                   WHERE organization_id=$1 AND operation_id=$2 AND name=$3 RETURNING fence''', org, oid, name,
            )
            await event(conn, org, oid, 'provider.claimed', provider=name, fence=fence)
            return fence

    async def provider_result(self, org, operation_id, name, fence, *, receipt=None, not_sent=False):
        """Trusted adapter boundary; bare HTTP success is NOT a deletion receipt."""
        org, oid = UUID(str(org)), UUID(str(operation_id))
        valid = verified_receipt(receipt, org, oid, name)
        status = 'confirmed' if valid else 'pending' if not_sent else 'uncertain'
        evidence = {'digest': receipt['evidence_digest']} if valid else {}
        async with self.pool.acquire() as conn, conn.transaction():
            result = await conn.execute(
                '''UPDATE gdpr_operation_steps SET status=$5,evidence=$6::jsonb,lease_until=NULL,updated_at=NOW()
                   WHERE organization_id=$1 AND operation_id=$2 AND name=$3 AND fence=$4
                     AND status='running' AND lease_until>NOW()''', org, oid, name, fence, status, encode(evidence),
            )
            if result != 'UPDATE 1':
                raise RequestConflict('Stale provider worker')
            await event(conn, org, oid, 'provider.' + status, provider=name, fence=fence)
            await refresh_provider_coverage(conn, org, oid)
        return status

    async def reconcile_provider(self, org, operation_id, name, *, receipt):
        """Offline, trusted reconciliation: never called from a user route.

        Evidence retrieval must be independently authenticated by the adapter.
        No retries of a destructive POST are authorized by lease expiry.
        """
        org, oid = UUID(str(org)), UUID(str(operation_id))
        if not verified_receipt(receipt, org, oid, name):
            raise ValueError('Verified provider receipt required')
        async with self.pool.acquire() as conn, conn.transaction():
            result = await conn.execute(
                '''UPDATE gdpr_operation_steps SET status='confirmed',fence=fence+1,evidence=$4::jsonb,
                       lease_until=NULL,updated_at=NOW()
                   WHERE organization_id=$1 AND operation_id=$2 AND name=$3 AND status='uncertain' ''',
                org, oid, name, encode({'digest': receipt['evidence_digest']}),
            )
            if result != 'UPDATE 1':
                raise RequestConflict('Provider stage is not reconcilable')
            await event(conn, org, oid, 'provider.reconciled', provider=name, digest=receipt['evidence_digest'])
            await refresh_provider_coverage(conn, org, oid)
