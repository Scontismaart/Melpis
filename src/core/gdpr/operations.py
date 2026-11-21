"""Durable GDPR requests and fenced provider evidence. No implicit execution."""
import hashlib
import json
import os
import re
from uuid import UUID, uuid4

_PROVIDER_LOCAL_ATTEMPTS = 5
_UNSENT_EXHAUSTED = 'confirmed_not_sent_budget_exhausted'

class RequestConflict(ValueError):
    pass


class OperationUnavailable(RuntimeError):
    pass


def encode(value):
    return json.dumps(value, default=str, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def document(value):
    return json.loads(value) if isinstance(value, str) else value


def _provider_attempt_limit(evidence):
    value = document(evidence)
    limit = value.get('dispatch_ceiling') if isinstance(value, dict) else None
    return limit if type(limit) is int and _PROVIDER_LOCAL_ATTEMPTS < limit < 2**31 else _PROVIDER_LOCAL_ATTEMPTS


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
    allowed = {'count', 'fence', 'attempts', 'category', 'reason', 'digest', 'provider',
               'conversation_states', 'conversation_receipts'}
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

    @staticmethod
    def _dispatchable_provider(name):
        if not re.fullmatch(r'provider\.[a-z][a-z0-9_.-]{0,65}', name):
            raise ValueError('Invalid provider stage')
        # Inventory review is a controller approval gate, not an adapter stage.
        return name != 'provider.inventory_review'

    @staticmethod
    async def _lock_provider_operation(conn, org, oid):
        # Local erasure and provider transitions use the same operation lock.
        # Check local authorization while that lock remains held, never in an
        # earlier transaction that could authorize a different lifecycle state.
        op = await conn.fetchrow(
            '''SELECT kind,status FROM gdpr_operations
               WHERE organization_id=$1 AND id=$2 FOR UPDATE''', org, oid,
        )
        if not op or op['kind'] != 'erase' or op['status'] != 'partial':
            raise OperationUnavailable('Local erasure and durable authorization required')
        if not await conn.fetchval(
            '''SELECT EXISTS (SELECT 1 FROM gdpr_operation_steps
               WHERE organization_id=$1 AND operation_id=$2
                 AND name='local' AND status='confirmed')''', org, oid,
        ):
            raise OperationUnavailable('Local erasure and durable authorization required')

    async def claim_provider(self, org, operation_id, name):
        """Reserve before HTTP. Expired workers become uncertain, never unsent."""
        org, oid = UUID(str(org)), UUID(str(operation_id))
        if not self._dispatchable_provider(name):
            return None
        async with self.pool.acquire() as conn, conn.transaction():
            await self._lock_provider_operation(conn, org, oid)
            row = await conn.fetchrow(
                '''SELECT status,attempts,evidence FROM gdpr_operation_steps
                   WHERE organization_id=$1 AND operation_id=$2 AND name=$3 FOR UPDATE''', org, oid, name,
            )
            # Only the inventory captured during authorized local erasure can
            # establish a provider step. Dispatch never creates a new one.
            if row is None:
                return None
            if row['status'] == 'running':
                await self._expire_provider_claim(conn, org, oid, name)
                return None
            if row['status'] != 'pending':
                return None
            limit = _provider_attempt_limit(row['evidence'])
            if row['attempts'] >= limit:
                await self._record_unsent_exhaustion(conn, org, oid, name, row['attempts'], limit)
                return None
            fence = await conn.fetchval(
                '''UPDATE gdpr_operation_steps SET status='running',fence=fence+1,attempts=attempts+1,
                       lease_until=NOW()+INTERVAL '30 seconds',updated_at=NOW()
                   WHERE organization_id=$1 AND operation_id=$2 AND name=$3 RETURNING fence''', org, oid, name,
            )
            await event(conn, org, oid, 'provider.claimed', provider=name, fence=fence)
            await refresh_provider_coverage(conn, org, oid)
            return fence

    @staticmethod
    async def _expire_provider_claim(conn, org, oid, name):
        result = await conn.execute(
            '''UPDATE gdpr_operation_steps SET status='uncertain',fence=fence+1,lease_until=NULL,updated_at=NOW()
               WHERE organization_id=$1 AND operation_id=$2 AND name=$3 AND status='running'
                 AND (lease_until IS NULL OR lease_until<=NOW())''', org, oid, name,
        )
        if result == 'UPDATE 1':
            await event(conn, org, oid, 'provider.uncertain', provider=name)
            await refresh_provider_coverage(conn, org, oid)

    async def provider_result(self, org, operation_id, name, fence, *, receipt=None, not_sent=False):
        """Trusted adapter boundary; bare HTTP success is NOT a deletion receipt."""
        org, oid = UUID(str(org)), UUID(str(operation_id))
        valid = verified_receipt(receipt, org, oid, name)
        status = 'confirmed' if valid else 'pending' if not_sent is True and receipt is None else 'uncertain'
        evidence = {'digest': receipt['evidence_digest']} if valid else {}
        async with self.pool.acquire() as conn, conn.transaction():
            if not self._dispatchable_provider(name):
                raise OperationUnavailable('Provider stage is not dispatchable')
            await self._lock_provider_operation(conn, org, oid)
            if status == 'pending':
                row = await conn.fetchrow('''SELECT attempts,evidence FROM gdpr_operation_steps
                    WHERE organization_id=$1 AND operation_id=$2 AND name=$3 AND fence=$4
                      AND status='running' AND lease_until>NOW() FOR UPDATE''', org, oid, name, fence)
                if row is None:
                    raise RequestConflict('Stale provider worker')
                limit = _provider_attempt_limit(row['evidence'])
                if row['attempts'] >= limit:
                    await self._record_unsent_exhaustion(conn, org, oid, name, row['attempts'], limit)
                    return 'failed'
                evidence = {'dispatch_ceiling': limit}
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

    @staticmethod
    async def _record_unsent_exhaustion(conn, org, oid, name, attempts, limit):
        await conn.execute('''UPDATE gdpr_operation_steps SET status='failed',fence=fence+1,evidence=$4::jsonb,
                 lease_until=NULL,updated_at=NOW() WHERE organization_id=$1 AND operation_id=$2 AND name=$3''',
            org, oid, name, encode({'reason': _UNSENT_EXHAUSTED, 'dispatch_ceiling': limit}))
        await event(conn, org, oid, 'provider.dead_letter', provider=name, attempts=attempts, reason=_UNSENT_EXHAUSTED)
        await refresh_provider_coverage(conn, org, oid)

    async def requeue_definitely_unsent(self, org, operation_id, name, *, evidence_reference):
        """Authorize one more local attempt after an operator verifies remediation."""
        org, oid = UUID(str(org)), UUID(str(operation_id))
        if (not self._dispatchable_provider(name) or type(evidence_reference) is not str
                or not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,128}', evidence_reference)):
            raise OperationUnavailable('Exact provider remediation evidence required')
        reference_digest = hashlib.sha256(evidence_reference.encode('ascii')).hexdigest()
        async with self.pool.acquire() as conn, conn.transaction():
            await self._lock_provider_operation(conn, org, oid)
            if await conn.fetchval('''SELECT EXISTS(SELECT FROM gdpr_operation_events
                WHERE organization_id=$1 AND operation_id=$2 AND event_type='provider.requeued_unsent'
                  AND details->>'provider'=$3 AND details->>'digest'=$4)''', org, oid, name, reference_digest):
                return
            row = await conn.fetchrow('''SELECT attempts,evidence FROM gdpr_operation_steps
                WHERE organization_id=$1 AND operation_id=$2 AND name=$3 AND status='failed' FOR UPDATE''',
                org, oid, name)
            evidence = document(row['evidence']) if row else None
            if (type(evidence) is not dict or evidence.get('reason') != _UNSENT_EXHAUSTED
                    or not _PROVIDER_LOCAL_ATTEMPTS <= row['attempts'] < 2**31 - 1):
                raise OperationUnavailable('Provider stage has no verified unsent dead letter')
            await conn.execute('''UPDATE gdpr_operation_steps SET status='pending',fence=fence+1,evidence=$4::jsonb,
                updated_at=NOW() WHERE organization_id=$1 AND operation_id=$2 AND name=$3''',
                org, oid, name, encode({'dispatch_ceiling': row['attempts'] + 1}))
            await event(conn, org, oid, 'provider.requeued_unsent', provider=name, attempts=row['attempts'],
                        digest=reference_digest)
            await refresh_provider_coverage(conn, org, oid)

    async def provider_reconcilable(self, org, operation_id, name):
        """Check authorization before consulting an external receipt endpoint."""
        org, oid = UUID(str(org)), UUID(str(operation_id))
        if not self._dispatchable_provider(name):
            return False
        async with self.pool.acquire() as conn, conn.transaction():
            await self._lock_provider_operation(conn, org, oid)
            await self._expire_provider_claim(conn, org, oid, name)
            return await conn.fetchval(
                '''SELECT EXISTS (SELECT 1 FROM gdpr_operation_steps
                   WHERE organization_id=$1 AND operation_id=$2 AND name=$3
                     AND status='uncertain')''', org, oid, name,
            )

    async def reconcile_provider_inventory(self, org, operation_id, providers, *, evidence_digest):
        """Record an authorized controller's exact inventory disposition.

        This owner-only maintenance boundary requires the operator to verify
        the independently retained, scoped review document before supplying
        its digest. Newly identified copies become pending provider steps.
        No provider receipt or deletion is inferred from inventory review.
        """
        org, oid = UUID(str(org)), UUID(str(operation_id))
        if (os.getenv('GDPR_PROVIDER_ERASURE_EXECUTION_ENABLED') != 'true'
                or type(providers) not in (list, tuple) or len(providers) > 100
                or any(type(name) is not str or not self._dispatchable_provider(name) for name in providers)
                or len(set(providers)) != len(providers)
                or type(evidence_digest) is not str
                or not re.fullmatch(r'[a-f0-9]{64}', evidence_digest)):
            raise OperationUnavailable('Exact authorized provider inventory evidence required')
        reviewed = set(providers)
        async with self.pool.acquire() as conn, conn.transaction():
            if not await conn.fetchval("""SELECT EXISTS(SELECT FROM pg_class
                WHERE oid=to_regclass('public.gdpr_operations') AND relowner=current_user::regrole)"""):
                raise OperationUnavailable('Provider inventory review requires the lifecycle owner')
            await self._lock_provider_operation(conn, org, oid)
            steps = await conn.fetch("""SELECT name,status,evidence FROM gdpr_operation_steps
                WHERE organization_id=$1 AND operation_id=$2 AND name LIKE 'provider.%'
                ORDER BY name FOR UPDATE""", org, oid)
            current = {row['name'] for row in steps if row['name'] != 'provider.inventory_review'}
            gate = next((row for row in steps if row['name'] == 'provider.inventory_review'), None)
            if gate is None or not current <= reviewed:
                raise OperationUnavailable('Reviewed inventory omits a recorded provider')
            if gate['status'] == 'confirmed':
                if current == reviewed and document(gate['evidence']) == {'digest': evidence_digest}:
                    return
                raise RequestConflict('Provider inventory review already recorded')
            if gate['status'] != 'held':
                raise OperationUnavailable('Provider inventory review unavailable')
            for name in sorted(reviewed - current):
                await conn.execute("""INSERT INTO gdpr_operation_steps(organization_id,operation_id,name,status)
                    VALUES($1,$2,$3,'pending')""", org, oid, name)
            await conn.execute("""UPDATE gdpr_operation_steps SET status='confirmed',evidence=$3::jsonb,
                updated_at=NOW() WHERE organization_id=$1 AND operation_id=$2 AND name='provider.inventory_review'""",
                org, oid, encode({'digest': evidence_digest}))
            await event(conn, org, oid, 'provider.inventory_reconciled', count=len(reviewed), digest=evidence_digest)
            await refresh_provider_coverage(conn, org, oid)

    async def reconcile_provider(self, org, operation_id, name, *, receipt):
        """Offline, trusted reconciliation: never called from a user route.

        Evidence retrieval must be independently authenticated by the adapter.
        No retries of a destructive POST are authorized by lease expiry.
        """
        org, oid = UUID(str(org)), UUID(str(operation_id))
        if not verified_receipt(receipt, org, oid, name):
            raise ValueError('Verified provider receipt required')
        async with self.pool.acquire() as conn, conn.transaction():
            if not self._dispatchable_provider(name):
                raise OperationUnavailable('Provider stage is not dispatchable')
            await self._lock_provider_operation(conn, org, oid)
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
