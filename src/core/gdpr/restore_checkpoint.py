"""Dormant restore-quarantine verifier; never enables traffic or performs erasure.

A backup cannot prove the absence of newer erasures. The controller must supply
an independently retained authenticated journal checkpoint and a trusted minimum
sequence obtained outside the restored database. Neither comes from a client.
This verifier only covers primary C2.4 copies, not external providers or backups.
"""
import hashlib
import hmac
import json
from uuid import UUID

from src.core.gdpr.restore_journal import RestoreJournalInvalid, verify_erasure_segment


class RestoreBlocked(RuntimeError):
    pass


async def verify_restore_checkpoint(conn, organization_id, checkpoint, *, verification_key,
                                    backup_id, minimum_sequence):
    """Verify one tenant while the restored environment remains quarantined.

    Checkpoint signing/approval and replay are separate administrative operations.
    No journal, wrong signature, missing tombstone, or restored content fails
    closed. A successful result is evidence for C2.4 only, never a global GO.
    """
    try:
        org = UUID(str(organization_id))
        if (not isinstance(verification_key, bytes) or len(verification_key) < 32
                or type(minimum_sequence) is not int or minimum_sequence < 0
                or not isinstance(backup_id, str) or not 1 <= len(backup_id) <= 128
                or not isinstance(checkpoint, dict) or set(checkpoint) != {'payload', 'signature'}):
            raise ValueError
        payload, signature = checkpoint['payload'], checkpoint['signature']
        if not isinstance(payload, dict):
            raise ValueError
        expected = {'version', 'organization_id', 'backup_id', 'sequence', 'complete', 'erasures'}
        if payload.get('version') == 'c24-restore-v2':
            expected.add('stay_purges')
        if (set(payload) != expected or payload.get('version') not in {'c24-restore-v1', 'c24-restore-v2'}
                or payload['complete'] is not True
                or payload['organization_id'] != str(org) or payload['backup_id'] != backup_id
                or type(payload['sequence']) is not int or payload['sequence'] < minimum_sequence
                or not isinstance(payload['erasures'], list) or len(payload['erasures']) > 10000
                or not isinstance(signature, str) or len(signature) != 64):
            raise ValueError
        raw = json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        if len(raw) > 2 * 1024 * 1024 or not hmac.compare_digest(
                signature, hmac.new(verification_key, raw, hashlib.sha256).hexdigest()):
            raise ValueError
        entries, seen = [], set()
        for entry in payload['erasures']:
            if not isinstance(entry, dict) or set(entry) != {'operation_id', 'scope', 'subject_id'}:
                raise ValueError
            operation = UUID(entry['operation_id'])
            scope = entry['scope']
            subject = UUID(entry['subject_id']) if entry['subject_id'] is not None else None
            if scope not in {'organization', 'contact'} or (scope == 'contact') != (subject is not None):
                raise ValueError
            if operation in seen:
                raise ValueError
            seen.add(operation)
            entries.append((operation, scope, subject))
        purges = []
        if payload['version'] == 'c24-restore-v2':
            if not isinstance(payload['stay_purges'], list) or len(payload['stay_purges']) > 10000:
                raise ValueError
            seen_bookings = set()
            for item in payload['stay_purges']:
                if not isinstance(item, dict) or set(item) != {'booking_id','subject_id','purpose','operation_id','policy_version','request_ids'}:
                    raise ValueError
                booking, subject = UUID(item['booking_id']), UUID(item['subject_id'])
                operation = UUID(item['operation_id']) if item['operation_id'] is not None else None
                purpose, version, ids = item['purpose'], item['policy_version'], item['request_ids']
                if (booking in seen_bookings or purpose not in {'erasure','retention'}
                    or (purpose == 'erasure') != (operation is not None)
                    or not isinstance(version, str) or not 1 <= len(version) <= 80
                    or not isinstance(ids, list) or len(ids) > 10000):
                    raise ValueError
                requests = {UUID(value) for value in ids}
                if len(requests) != len(ids):
                    raise ValueError
                seen_bookings.add(booking)
                purges.append((booking,subject,purpose,operation,version,requests))
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        raise RestoreBlocked('Authenticated external restore checkpoint required') from None

    return await _verify_replayed_erasures(
        conn, org, entries, payload['sequence'], purges=purges if payload['version']=='c24-restore-v2' else None,
    )


async def verify_restore_journal_replay(conn, organization_id, payload, signature, **trust):
    """Check scoped replay against signed evidence and an independently pinned tip."""
    org = UUID(str(organization_id))
    try:
        verified = verify_erasure_segment(payload, signature, **trust)
    except RestoreJournalInvalid:
        raise RestoreBlocked('Authenticated external restore journal required') from None
    scoped = tuple(entry for entry in verified if entry.organization_id == org)
    return await _verify_replayed_erasures(
        conn, org, [(entry.operation_id, entry.scope, entry.subject_id) for entry in scoped],
        trust['independent_tip'].sequence,
        fingerprints={entry.operation_id: (entry.identifier_digest, entry.key_id) for entry in scoped},
    )


async def _verify_replayed_erasures(conn, org, entries, sequence, *, fingerprints=None, purges=None):
    if not conn.is_in_transaction():
        raise RestoreBlocked('Restore verification requires a quarantined transaction')
    if not await conn.fetchval('SELECT EXISTS(SELECT 1 FROM organizations WHERE id=$1)', org):
        raise RestoreBlocked('Restored organization is unavailable')
    await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1::text,606))', str(org))
    stay_schema = await conn.fetchval("SELECT to_regclass('public.booking_stays') IS NOT NULL")
    if stay_schema:
        if purges is None:
            raise RestoreBlocked('Authenticated stay purge journal required')
        actual = await conn.fetch('''SELECT booking_id,original_subject_id,purpose,operation_id,policy_version
            FROM booking_stay_purge_fences WHERE organization_id=$1''', org)
        expected_fences = {(b,s,p,o,v) for b,s,p,o,v,_ in purges}
        if {(r['booking_id'],r['original_subject_id'],r['purpose'],r['operation_id'],r['policy_version']) for r in actual} != expected_fences:
            raise RestoreBlocked('Stay purge fences do not match authenticated journal')
        if await conn.fetchval('SELECT count(*) FROM booking_stay_tombstones WHERE organization_id=$1', org) != sum(len(p[5]) for p in purges):
            raise RestoreBlocked('Stay purge tombstones do not match authenticated journal')
        for booking, subject, purpose, operation, version, requests in purges:
            rows = await conn.fetch('''SELECT request_id,original_subject_id,purpose,operation_id,policy_version
                FROM booking_stay_tombstones WHERE organization_id=$1 AND booking_id=$2''', org, booking)
            if ({r['request_id'] for r in rows} != requests
                or any((r['original_subject_id'],r['purpose'],r['operation_id'],r['policy_version']) != (subject,purpose,operation,version) for r in rows)
                or await conn.fetchval('''SELECT EXISTS(SELECT 1 FROM booking_stays WHERE organization_id=$1 AND booking_id=$2)
                    OR EXISTS(SELECT 1 FROM booking_allocation_requests WHERE organization_id=$1 AND booking_id=$2)''', org, booking)):
                raise RestoreBlocked('Stay purge copies do not match authenticated journal')
    elif purges:
        raise RestoreBlocked('Stay purge schema required by authenticated journal')
    for operation, scope, subject in entries:
        present = await conn.fetchval('''SELECT EXISTS(SELECT 1 FROM gdpr_erasure_barriers
            WHERE organization_id=$1 AND operation_id=$2 AND scope=$3
              AND subject_id IS NOT DISTINCT FROM $4::uuid)''', org, operation, scope, subject)
        if not present:
            raise RestoreBlocked('Newer erasure journal has not been replayed')
        if fingerprints is not None:
            digest, key_id = fingerprints[operation]
            reconciled = await conn.fetchval('''SELECT EXISTS(
                SELECT 1 FROM gdpr_erasure_barriers b
                JOIN gdpr_operation_steps s ON s.organization_id=b.organization_id AND s.operation_id=b.operation_id
                WHERE b.organization_id=$1 AND s.organization_id=$1 AND b.operation_id=$2 AND b.scope=$3
                  AND b.subject_id IS NOT DISTINCT FROM $4::uuid
                  AND b.identifier_digest IS NOT DISTINCT FROM $5
                  AND b.key_id IS NOT DISTINCT FROM $6
                  AND s.name='local' AND s.status='confirmed')''', org, operation, scope, subject, digest, key_id)
            if not reconciled:
                raise RestoreBlocked('Replayed erasure evidence does not match the journal')
        remaining = False
        if stay_schema:
            remaining = await conn.fetchval('''SELECT
            EXISTS(SELECT 1 FROM booking_stays WHERE organization_id=$1
                   AND ($2::uuid IS NULL OR original_subject_id=$2))
            OR EXISTS(SELECT 1 FROM booking_allocation_requests r
                LEFT JOIN booking_stays s ON s.organization_id=r.organization_id AND s.organization_id=$1 AND s.booking_id=r.booking_id
                WHERE r.organization_id=$1 AND ($2::uuid IS NULL OR s.original_subject_id IS NULL OR s.original_subject_id=$2))''', org, subject)
        remaining = remaining or await conn.fetchval('''SELECT
            EXISTS(SELECT 1 FROM booking_conversation_states WHERE organization_id=$1
                   AND ($2::uuid IS NULL OR subject_id=$2))
            OR EXISTS(SELECT 1 FROM booking_conversation_requests r
                LEFT JOIN conversations c ON c.organization_id=r.organization_id AND c.id=r.conversation_id AND c.organization_id=$1
                WHERE r.organization_id=$1 AND ($2::uuid IS NULL
                    OR COALESCE((to_jsonb(r)->>'original_subject_id')::uuid,c.contact_id)=$2))
            OR EXISTS(SELECT 1 FROM messages m
                JOIN conversations c ON c.organization_id=m.organization_id AND c.id=m.conversation_id
                WHERE m.organization_id=$1 AND c.organization_id=$1 AND ($2::uuid IS NULL OR c.contact_id=$2)
                  AND (m.deleted_at IS NULL OR m.ai_reply_cache IS NOT NULL))
            OR ($2::uuid IS NULL AND EXISTS(SELECT 1 FROM simulation_requests WHERE organization_id=$1))''', org, subject)
        if subject is not None:
            if await conn.fetchval('''SELECT EXISTS(SELECT 1 FROM simulation_requests
                WHERE organization_id=$1 AND response ? 'booking_state_proposal'
                  AND (c24_subject_id=$2 OR c24_subject_id IS NULL))''', org, subject):
                remaining = True
            # Inbound A04 copies use a derived operation key. Check the real key,
            # not an assumed source-message=request-id relation.
            from src.core.billing.quota_reservations import QuotaOperation
            async for message in conn.cursor('''SELECT m.id FROM messages m
                JOIN conversations c ON c.organization_id=m.organization_id AND c.id=m.conversation_id
                WHERE m.organization_id=$1 AND c.organization_id=$1 AND c.contact_id=$2''', org, subject):
                request = QuotaOperation(org, UUID(str(message['id'])), 'inbound', '0' * 64).id
                if await conn.fetchval('SELECT EXISTS(SELECT 1 FROM simulation_requests WHERE organization_id=$1 AND request_id=$2)', org, request):
                    remaining = True
        if remaining:
            raise RestoreBlocked('Restored C2.4 content still requires erasure replay')
    return {'organization_id': str(org), 'sequence': sequence,
            'verified_erasures': len(entries), 'scope': 'primary_c24_only',
            'traffic_authorized': False, 'external_copies_certified': False}
