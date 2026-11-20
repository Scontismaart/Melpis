"""Read-only evidence for a verified, never-applied C2.4 proposal source."""

from datetime import timezone
from hashlib import sha256
from uuid import UUID

from src.core.billing.quota_reservations import QuotaOperation, _decode, _hash, QuotaConflict
from src.core.bookings.conversation_state import canonical, digest, parse_proposal_json
from src.core.db.scoping import system_scope
from src.core.gdpr.operations import document
from src.core.gdpr.restore_checkpoint import RestoreBlocked


@system_scope('Read only the fixed global quota trust marker in an owner read-only planning transaction')
async def _read_only_quota_epoch(conn):
    # The live A04 trust getter takes FOR SHARE, which is deliberately
    # unavailable in a PostgreSQL READ ONLY transaction. Check the same
    # marker and client mutation ACLs without taking a writable row lock.
    writes = ['INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER']
    if int(await conn.fetchval('SHOW server_version_num')) >= 170000:
        writes.append('MAINTAIN')
    safe = await conn.fetchval('''SELECT count(*)=2 AND bool_and(c.relrowsecurity)
        AND NOT EXISTS(SELECT FROM pg_class t JOIN pg_namespace s ON s.oid=t.relnamespace
          CROSS JOIN (VALUES ('anon'),('authenticated')) roles(name)
          CROSS JOIN unnest($1::text[]) privilege(name)
          WHERE s.nspname='public' AND t.relname IN ('messages','usage_events')
            AND has_table_privilege(roles.name,t.oid,privilege.name))
        AND NOT EXISTS(SELECT FROM pg_class t JOIN pg_namespace s ON s.oid=t.relnamespace
          CROSS JOIN (VALUES ('anon'),('authenticated')) roles(name)
          CROSS JOIN (VALUES ('INSERT'),('UPDATE'),('REFERENCES')) privilege(name)
          WHERE s.nspname='public' AND t.relname IN ('messages','usage_events')
            AND has_any_column_privilege(roles.name,t.oid,privilege.name))
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname IN ('messages','usage_events')''', writes)
    if safe is not True:
        return None
    marker = await conn.fetchval('''SELECT metadata FROM public.usage_events
        WHERE id='6206d423-cea9-5480-bb9b-59d757503f14'::uuid AND organization_id IS NULL
          AND event_type='system.quota_trust_epoch' AND quantity=0''')
    try:
        marker = document(marker)
        if (type(marker) is not dict or set(marker) != {'protocol_version', 'trust_epoch'}
                or type(marker['protocol_version']) is not int or marker['protocol_version'] != 1
                or type(marker['trust_epoch']) is not str):
            return None
        epoch = UUID(marker['trust_epoch'])
        return epoch if epoch.int and str(epoch) == marker['trust_epoch'] else None
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


async def plan_orphan_proposal_expiry(conn, organization_id, source_message_id):
    """Identify one exact known-source candidate, without removing either copy.

    Run under an owner-controlled, frozen source maintenance window. The
    tenant lock makes the observation coherent with the GDPR lifecycle. A
    future operation must independently retain the typed recovery tuple and
    recheck this evidence, current holds and the full lifecycle in its write
    transaction. Unknown/ambiguous sources deliberately produce no candidate.
    """
    return await _plan_orphan_proposal(conn, organization_id, source_message_id, read_only=True)


async def _plan_orphan_proposal(conn, organization_id, source_message_id, *, read_only):
    if (not isinstance(organization_id, UUID) or not organization_id.int
            or not isinstance(source_message_id, UUID) or not source_message_id.int):
        raise RestoreBlocked('Exact orphan proposal organization and source required')
    org, source = UUID(str(organization_id)), UUID(str(source_message_id))
    if (not conn.is_in_transaction() or not await conn.fetchval('''SELECT
            current_setting('transaction_read_only')=$1 AND current_setting('transaction_isolation')='read committed'
            AND (SELECT count(*)=4 AND bool_and(relowner=current_user::regrole)
                 FROM pg_class WHERE oid IN ('public.messages'::regclass,'public.usage_events'::regclass,
                   'public.simulation_requests'::regclass,'public.gdpr_policies'::regclass))''', 'on' if read_only else 'off')):
        raise RestoreBlocked('Orphan proposal planning requires a read-only lifecycle owner transaction' if read_only
                             else 'Orphan proposal replay requires a writable lifecycle owner transaction')
    if not await conn.fetchval('SELECT pg_try_advisory_xact_lock(hashtextextended($1::text,606))', str(org)):
        return None
    row = await conn.fetchrow('''SELECT m.id,m.conversation_id,m.c24_original_subject_id,m.c24_received_at,
          m.wam_id,m.content,m.content_text,m.ai_reply_cache,p.version AS policy_version,
          clock_timestamp() AS observed_at
        FROM public.messages m
        JOIN public.conversations c ON c.organization_id=m.organization_id AND c.id=m.conversation_id
        JOIN public.contacts k ON k.organization_id=c.organization_id AND k.id=c.contact_id
        JOIN LATERAL (SELECT version,retention_days,valid_until,erasure_allowed,legal_hold
          FROM public.gdpr_policies WHERE organization_id=$1 AND category='booking_conversation_content'
            AND approved_at<=clock_timestamp() ORDER BY approved_at DESC,version DESC LIMIT 1) p ON true
        WHERE m.organization_id=$1 AND c.organization_id=$1 AND k.organization_id=$1 AND m.id=$2
          AND m.direction='inbound' AND c.canale='whatsapp' AND m.status='handled'
          AND m.replied_at IS NOT NULL AND m.replied_at<=clock_timestamp() AND m.billed_at IS NOT NULL
          AND m.deleted_at IS NULL AND m.content_purged_at IS NULL AND c.deleted_at IS NULL AND k.deleted_at IS NULL
          AND m.c24_original_subject_id=c.contact_id AND m.c24_received_at IS NOT NULL
          AND m.wam_id IS NOT NULL AND COALESCE(m.ai_reply_cache ? 'booking_state_proposal',false)
          AND p.valid_until>clock_timestamp() AND NOT p.legal_hold AND p.erasure_allowed
          AND p.retention_days BETWEEN 1 AND 30
          AND m.c24_received_at<=clock_timestamp()-make_interval(days=>p.retention_days)''', org, source)
    if row is None:
        return None
    conversation, subject = row['conversation_id'], row['c24_original_subject_id']
    try:
        text = row['content_text']
        body, cached = document(row['content']), document(row['ai_reply_cache'])
        if (type(text) is not str or len(text.encode()) > 65536 or type(cached) is not dict
                or len(canonical(cached).encode()) > 131072 or type(body) is not dict
                or body['id'] != row['wam_id'] or body['type'] != 'text'
                or body['text']['body'] != text
                or type(body['timestamp']) is not str or not body['timestamp'].isascii()
                or not body['timestamp'].isdigit() or not 1 <= len(body['timestamp']) <= 12
                or not 0 < int(body['timestamp']) <= int(row['observed_at'].timestamp()) + 300):
            return None
        parse_proposal_json(canonical(cached['booking_state_proposal']))
        operation = QuotaOperation(org, source, 'inbound', digest({
            'organization_id': str(org), 'message_id': str(source), 'channel': 'whatsapp', 'text': text}))
    except (ValueError, TypeError, KeyError, AttributeError, QuotaConflict):
        return None

    if await conn.fetchval('''SELECT EXISTS(SELECT FROM public.booking_conversation_states
           WHERE organization_id=$1 AND conversation_id=$2)
        OR EXISTS(SELECT FROM public.booking_conversation_requests WHERE organization_id=$1 AND conversation_id=$2)
        OR EXISTS(SELECT FROM public.c24_source_fences WHERE organization_id=$1 AND conversation_id=$2 AND source_message_id=$3)
        OR EXISTS(SELECT FROM public.booking_conversation_revocations WHERE organization_id=$1 AND conversation_id=$2)
        OR EXISTS(SELECT FROM public.messages WHERE organization_id=$1 AND conversation_id=$2
           AND (status IN ('processing','queued','received_pending_ai','sending_ambiguous')
             OR (direction='inbound' AND id<>$3 AND (c24_received_at IS NULL OR c24_received_at>=$4))))
        OR EXISTS(SELECT FROM public.message_delivery_attempts a JOIN public.messages m ON m.id=a.message_id
            WHERE m.organization_id=$1 AND m.conversation_id=$2 AND a.status NOT IN ('sent','delivered','read','completed'))
        OR EXISTS(SELECT FROM public.gdpr_retention_holds h WHERE h.organization_id=$1
            AND (h.scope='organization' OR (h.scope='subject' AND h.scope_id=$5)
              OR (h.scope='conversation' AND h.scope_id=$2) OR (h.scope='record' AND h.scope_id IN ($3,$6)))
            AND NOT EXISTS(SELECT FROM public.gdpr_retention_hold_events v WHERE v.organization_id=$1
              AND v.hold_id=h.hold_id AND v.kind='released'))
        OR EXISTS(SELECT FROM public.usage_events WHERE organization_id=$1
           AND ((event_type='system.quota_reservation' AND metadata->>'state'='reserved')
             OR (event_type='system.provider_attempt' AND metadata->>'accounting_status'='unresolved')))
        OR EXISTS(SELECT FROM public.simulation_requests WHERE organization_id=$1 AND (status='reserved'
            OR (c24_source_message_id IS NULL AND COALESCE(response ? 'booking_state_proposal',false))))
        OR EXISTS(SELECT FROM c24_private.worker_context WHERE organization_id=$1 AND conversation_id=$2)
        OR EXISTS(SELECT FROM c24_private.worker_capabilities WHERE organization_id=$1 AND message_id=$3
             AND (completed_at IS NULL OR payload IS NOT NULL))''',
        org, conversation, source, row['c24_received_at'], subject, operation.id):
        return None

    # Exact immutable source relation and deterministic A04 identity. Any
    # second response for that source is unresolved, even if hashes agree.
    responses = await conn.fetch('''SELECT c24_subject_id,c24_conversation_id,c24_source_message_id,
          auth_user_id,request_id,payload_hash,claim_token,status,response
        FROM public.simulation_requests WHERE organization_id=$1
          AND (c24_source_message_id=$2 OR (auth_user_id=$3 AND request_id=$4)) LIMIT 2''',
        org, source, operation.actor_id, operation.id)
    ledger = await conn.fetchrow('''SELECT event_type,quantity,metadata FROM public.usage_events
        WHERE organization_id=$1 AND id=$2''', org, operation.id)
    epoch = await _read_only_quota_epoch(conn)
    if epoch is None or len(responses) != 1:
        return None
    response = responses[0]
    try:
        metadata = _decode(ledger, operation)
        stored = document(response['response'])
        if (metadata['state'] != 'committed' or metadata['epoch'] != str(epoch)
                or response['status'] != 'completed' or response['payload_hash'].strip() != operation.payload_hash
                or response['claim_token'] != UUID(metadata['claim_id'])
                or (response['c24_subject_id'], response['c24_conversation_id'], response['c24_source_message_id']) != (subject, conversation, source)
                or response['auth_user_id'] != operation.actor_id or response['request_id'] != operation.id
                or type(stored) is not dict or len(canonical(stored).encode()) > 131072 or stored != cached
                or _hash(stored) != metadata['response_hash'] or _hash(cached) != metadata['response_hash']):
            return None
    except (ValueError, TypeError, KeyError, AttributeError, QuotaConflict):
        return None
    # Some legacy A04 commits precede the private worker bridge. If its
    # linkage exists, it must corroborate this exact operation, never infer
    # new linkage from an unrecognized worker identity.
    if await conn.fetchval('''SELECT EXISTS(SELECT FROM public.inbound_worker_operations
        WHERE organization_id=$1 AND message_id=$2 AND operation_id<>$3)''', org, source, operation.id):
        return None
    if await _read_only_quota_epoch(conn) != epoch:
        return None
    return {
        'kind': 'known_source_orphan_proposal_candidate_v1',
        'organization_id': str(org), 'conversation_id': str(conversation),
        'subject_id': str(subject), 'source_message_id': str(source),
        'received_at': row['c24_received_at'].astimezone(timezone.utc).isoformat(),
        'policy_version': row['policy_version'], 'quota_operation_id': str(operation.id),
        'response_hash': metadata['response_hash'],
        'provider_message_sha256': sha256(row['wam_id'].encode('utf-8')).hexdigest(),
        'deletion_authorized': False,
    }
