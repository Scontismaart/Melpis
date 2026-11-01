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
        if (not isinstance(payload, dict) or set(payload) != {
                'version', 'organization_id', 'backup_id', 'sequence', 'complete', 'erasures'}
                or payload['version'] != 'c24-restore-v1' or payload['complete'] is not True
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
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        raise RestoreBlocked('Authenticated external restore checkpoint required') from None

    if not conn.is_in_transaction():
        raise RestoreBlocked('Restore verification requires a quarantined transaction')
    await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1::text,606))', str(org))
    for operation, scope, subject in entries:
        present = await conn.fetchval('''SELECT EXISTS(SELECT 1 FROM gdpr_erasure_barriers
            WHERE organization_id=$1 AND operation_id=$2 AND scope=$3
              AND subject_id IS NOT DISTINCT FROM $4::uuid)''', org, operation, scope, subject)
        if not present:
            raise RestoreBlocked('Newer erasure journal has not been replayed')
        remaining = await conn.fetchval('''SELECT
            EXISTS(SELECT 1 FROM booking_conversation_states WHERE organization_id=$1
                   AND ($2::uuid IS NULL OR subject_id=$2))
            OR EXISTS(SELECT 1 FROM booking_conversation_requests r
                JOIN conversations c ON c.organization_id=r.organization_id AND c.id=r.conversation_id
                WHERE r.organization_id=$1 AND c.organization_id=$1 AND ($2::uuid IS NULL OR c.contact_id=$2))
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
    return {'organization_id': str(org), 'sequence': payload['sequence'],
            'verified_erasures': len(entries), 'scope': 'primary_c24_only',
            'traffic_authorized': False, 'external_copies_certified': False}
