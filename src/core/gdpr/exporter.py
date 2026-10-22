"""Bounded snapshot export: PostgreSQL ciphertext, small one-time Redis ticket."""
import hashlib
import json
import asyncio
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID, uuid4

from src.core.gdpr.catalog import CATEGORIES
from src.core.gdpr.operations import Operations, OperationUnavailable, document, encode, event
from src.core.gdpr.token_store import _fernet
from src.core.db.scoping import extract_tables, has_explicit_org_scope, TABLE_SCOPE_CLASSIFICATION, TENANT_SCOPED_TABLES, INDIRECT_SCOPED_TABLES

_BLOCK = 65536
_MAX_ROW = 524288
_MAX_BYTES = 512 * 1024 * 1024


def catalogue_query(category, scope):
    if category not in CATEGORIES or scope not in {'organization', 'contact', 'account'}:
        raise ValueError('Only the frozen GDPR catalogue is executable')
    query = category.query if scope == 'organization' else getattr(category, scope + '_query')
    if not query:
        return None
    if (not query.lstrip().upper().startswith('SELECT ') or ';' in query or '--' in query
            or '*' in query or re.search(r'\b(insert|update|delete|drop|alter|truncate|grant|revoke|copy|call|execute)\b', query, re.I)):
        raise ValueError('Invalid GDPR catalogue projection')
    tables = extract_tables(query)
    if not tables or tables - TABLE_SCOPE_CLASSIFICATION.keys():
        raise ValueError('Unclassified GDPR source')
    guarded = tables & (TENANT_SCOPED_TABLES | INDIRECT_SCOPED_TABLES)
    if guarded and not has_explicit_org_scope(query, guarded):
        raise ValueError('GDPR source is not tenant bound')
    if not guarded and not (
        (tables == {'organizations'} and re.search(r'WHERE\s+id\s*=\s*\$1::uuid', query, re.I))
        or (tables == {'processed_stripe_events'} and re.search(r'WHERE\s+organization_id\s*=\s*\$1::uuid', query, re.I))
    ):
        raise ValueError('GDPR root source is not tenant bound')
    return query


@asynccontextmanager
async def snapshot(conn, org, oid):
    # Session lock is acquired OUTSIDE repeatable-read so a waiting SELECT
    # cannot pin a stale snapshot preceding a concurrently committed erasure.
    await conn.execute('SELECT pg_advisory_lock_shared(hashtextextended($1::text,606))', str(org))
    try:
        await conn.execute('SELECT pg_advisory_lock(hashtextextended($1::text,607))', str(oid))
        try:
            async with conn.transaction(isolation='repeatable_read'):
                yield
        finally:
            cleanup = asyncio.create_task(conn.execute('SELECT pg_advisory_unlock(hashtextextended($1::text,607))', str(oid)))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise
    finally:
        cleanup = asyncio.create_task(conn.execute('SELECT pg_advisory_unlock_shared(hashtextextended($1::text,606))', str(org)))
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            await cleanup
            raise


class Exporter:
    def __init__(self, pool):
        self.pool = pool

    async def prepare(self, org, requester, *, scope='organization', subject_id=None, request_id=None):
        org = UUID(str(org))
        operations = Operations(self.pool)
        oid = await operations.create(org, requester, request_id or uuid4(), kind='export', scope=scope, subject_id=subject_id)
        try:
            async with self.pool.acquire() as conn, snapshot(conn, org, oid):
                op = await conn.fetchrow(
                    '''SELECT status,manifest,expires_at FROM gdpr_operations
                       WHERE organization_id=$1 AND id=$2 AND requester=$3 FOR UPDATE''', org, oid, str(requester),
                )
                if op['status'] == 'ready' and op['expires_at'] > datetime.now(timezone.utc):
                    return {'export_id': str(oid), 'manifest': document(op['manifest'])}
                if op['status'] in {'expired', 'revoked'}:
                    raise OperationUnavailable('Export requires a new request identifier')
                if await conn.fetchval(
                    '''SELECT EXISTS(SELECT 1 FROM gdpr_erasure_barriers WHERE organization_id=$1
                       AND (scope='organization' OR subject_id=$2::uuid))''', org, UUID(str(subject_id)) if subject_id else None,
                ):
                    raise OperationUnavailable('Erasure barrier prevents a new snapshot')
                await conn.execute('DELETE FROM gdpr_export_chunks WHERE organization_id=$1 AND operation_id=$2', org, oid)
                await conn.execute(
                    "UPDATE gdpr_operations SET status='running',updated_at=NOW() WHERE organization_id=$1 AND id=$2", org, oid,
                )
                cipher = _fernet()
                buffer = bytearray()
                sequence, total = 0, 0

                async def append(value, *, flush=False):
                    nonlocal sequence, total
                    buffer.extend(value)
                    total += len(value)
                    if total > _MAX_BYTES:
                        raise OperationUnavailable('Export size requires an offline controlled export')
                    if len(buffer) >= _BLOCK or (flush and buffer):
                        ciphertext = cipher.encrypt(bytes(buffer))
                        await conn.execute(
                            '''INSERT INTO gdpr_export_chunks(organization_id,operation_id,sequence,ciphertext,digest)
                               VALUES($1,$2,$3,$4,$5)''', org, oid, sequence, ciphertext, hashlib.sha256(ciphertext).hexdigest(),
                        )
                        sequence += 1
                        buffer.clear()

                manifest = {'version': 'a06-v1', 'scope': scope, 'categories': [],
                            'external_copies': 'not_included', 'backups': 'not_included',
                            'legal_policy_approval': 'C05-100-pending'}
                partial = False
                await append(('{"organization_id":' + encode(str(org)) + ',"exported_at":' +
                              encode(datetime.now(timezone.utc).isoformat())).encode())
                for category in CATEGORIES:
                    query = catalogue_query(category, scope)
                    item = {'category': category.name, 'count': 0}
                    if not query:
                        item.update(status='excluded', reason=category.exclusion_reason or 'no_verified_subject_relation')
                        # Credential-only/system data are intentionally non-portable;
                        # an unproved subject relation needs controller review.
                        partial |= scope != 'organization'
                        manifest['categories'].append(item)
                        continue
                    name = 'simulator_responses' if category.name == 'simulation_requests' else category.name
                    await append((',' + encode(name) + ':[').encode())
                    # Query comes exclusively from the frozen server catalogue.
                    # Size is checked inside PG before any record enters Python.
                    bounded = ('SELECT CASE WHEN octet_length(row_to_json(g)::text)<=$' +
                               ('2' if scope == 'organization' else '3') +
                               ' THEN row_to_json(g)::text ELSE NULL END AS record FROM (' + query + ') AS g')
                    values = ([org] if scope == 'organization' else
                              [org, UUID(str(subject_id))] if scope == 'contact' else [org, str(requester)])
                    values.append(_MAX_ROW)
                    first = True
                    async for row in conn.cursor(bounded, *values, prefetch=32):
                        if row['record'] is None:
                            item['oversize_records'] = item.get('oversize_records', 0) + 1
                            partial = True
                            continue
                        await append((('' if first else ',') + row['record']).encode('utf-8'))
                        first = False
                        item['count'] += 1
                    await append(b']')
                    if scope != 'organization' and category.exclusion_reason:
                        item.update(status='partial', reason=category.exclusion_reason)
                        partial = True
                    else:
                        item['status'] = 'included'
                    manifest['categories'].append(item)
                manifest['status'] = 'partial' if partial else 'complete_for_catalogue'
                await append((',"manifest":' + encode(manifest) + '}').encode(), flush=True)
                manifest['chunks'] = sequence
                manifest['bytes'] = total
                await conn.execute(
                    '''UPDATE gdpr_operations SET status='ready',manifest=$3::jsonb,
                           expires_at=NOW()+INTERVAL '15 minutes',updated_at=NOW()
                       WHERE organization_id=$1 AND id=$2''', org, oid, encode(manifest),
                )
                await event(conn, org, oid, 'export.ready', count=sequence)
                return {'export_id': str(oid), 'manifest': manifest}
        except Exception:
            async with self.pool.acquire() as conn, conn.transaction():
                await conn.execute(
                    '''UPDATE gdpr_operations SET status='failed',manifest='{"reason":"export_failed"}',updated_at=NOW()
                       WHERE organization_id=$1 AND id=$2 AND status IN ('requested','running','failed')''', org, oid,
                )
                await event(conn, org, oid, 'export.failed', reason='snapshot_unavailable')
            raise

    async def preflight(self, org, oid, requester):
        org, oid = UUID(str(org)), UUID(str(oid))
        async with self.pool.acquire() as conn, conn.transaction():
            op = await conn.fetchrow(
                '''SELECT manifest FROM gdpr_operations WHERE organization_id=$1 AND id=$2 AND requester=$3
                   AND kind='export' AND status='ready' AND expires_at>NOW() FOR SHARE''', org, oid, str(requester),
            )
            if not op:
                raise LookupError('Export unavailable')
            count = 0
            cipher = _fernet()
            async for row in conn.cursor(
                '''SELECT sequence,ciphertext,digest FROM gdpr_export_chunks
                   WHERE organization_id=$1 AND operation_id=$2 ORDER BY sequence''', org, oid, prefetch=1,
            ):
                if row['sequence'] != count or hashlib.sha256(row['ciphertext']).hexdigest() != row['digest']:
                    raise OperationUnavailable('Export integrity verification failed')
                cipher.decrypt(row['ciphertext'])
                count += 1
            if count != document(op['manifest']).get('chunks') or not count:
                raise OperationUnavailable('Export is incomplete')

    async def stream(self, org, oid, requester):
        org, oid = UUID(str(org)), UUID(str(oid))
        async with self.pool.acquire() as conn, conn.transaction():
            valid = await conn.fetchval(
                '''SELECT id FROM gdpr_operations WHERE organization_id=$1 AND id=$2 AND requester=$3
                   AND status='ready' AND expires_at>NOW() FOR SHARE''', org, oid, str(requester),
            )
            if not valid:
                raise OperationUnavailable('Export unavailable')
            cipher = _fernet()
            async for row in conn.cursor(
                '''SELECT ciphertext,digest FROM gdpr_export_chunks
                   WHERE organization_id=$1 AND operation_id=$2 ORDER BY sequence''', org, oid, prefetch=1,
            ):
                if hashlib.sha256(row['ciphertext']).hexdigest() != row['digest']:
                    raise OperationUnavailable('Export integrity verification failed')
                yield cipher.decrypt(row['ciphertext'])

    async def revoke(self, org, oid, requester):
        async with self.pool.acquire() as conn, conn.transaction():
            row = await conn.fetchval(
                '''UPDATE gdpr_operations SET status='revoked',updated_at=NOW()
                   WHERE organization_id=$1 AND id=$2 AND requester=$3 AND kind='export' RETURNING id''',
                UUID(str(org)), UUID(str(oid)), str(requester),
            )
            if row:
                await conn.execute('DELETE FROM gdpr_export_chunks WHERE organization_id=$1 AND operation_id=$2', UUID(str(org)), UUID(str(oid)))
                await event(conn, UUID(str(org)), UUID(str(oid)), 'export.revoked')
