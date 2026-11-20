"""Dormant restore-quarantine verifier; never enables traffic or performs erasure.

A backup cannot prove the absence of newer erasures. The controller must supply
an independently retained authenticated journal checkpoint and a trusted minimum
sequence obtained outside the restored database. Neither comes from a client.
This verifier only covers primary C2.4 copies, not external providers or backups.
"""
import hashlib
import hmac
import json
import re
from datetime import datetime
import os
from pathlib import Path
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken

from src.core.gdpr.restore_journal import RestoreJournalInvalid, verify_erasure_segment


class RestoreBlocked(RuntimeError):
    pass


_MAX_SEALED_CHECKPOINT = 3 * 1024 * 1024


async def verify_restore_checkpoint(conn, organization_id, checkpoint, *, verification_key,
                                    backup_id, minimum_sequence, checkpoint_sha256=None):
    """Verify one tenant while the restored environment remains quarantined.

    Checkpoint signing/approval and replay are separate administrative operations.
    No journal, wrong signature, missing tombstone, or restored content fails
    closed. A successful result is evidence for C2.4 only, never a global GO.
    """
    org, entries, sequence, purges, receipt_prunes, ordinary_purges = _authenticate_checkpoint(
        organization_id, checkpoint, verification_key=verification_key,
        backup_id=backup_id, minimum_sequence=minimum_sequence, checkpoint_sha256=checkpoint_sha256,
    )
    return await _verify_replayed_erasures(
        conn, org, entries, sequence, purges=purges, receipt_prunes=receipt_prunes,
        ordinary_purges=ordinary_purges,
    )


def _authenticate_checkpoint(organization_id, checkpoint, *, verification_key, backup_id, minimum_sequence,
                             checkpoint_sha256=None):
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
        version = payload.get('version')
        if version in {'c24-restore-v2', 'c24-restore-v3', 'c24-restore-v4'}:
            expected.add('stay_purges')
        if version in {'c24-restore-v3', 'c24-restore-v4'}:
            expected.add('receipt_prunes')
        if version == 'c24-restore-v4':
            expected.add('ordinary_booking_purges')
        if (set(payload) != expected or version not in {'c24-restore-v1', 'c24-restore-v2', 'c24-restore-v3', 'c24-restore-v4'}
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
        if version in {'c24-restore-v3','c24-restore-v4'} and (not isinstance(checkpoint_sha256, str)
                or not hmac.compare_digest(checkpoint_sha256, hashlib.sha256(raw).hexdigest())):
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
        purges = None
        if 'stay_purges' in expected:
            purges = []
            if not isinstance(payload['stay_purges'], list) or len(payload['stay_purges']) > 10000:
                raise ValueError
            seen_bookings = set()
            for item in payload['stay_purges']:
                if not isinstance(item, dict) or set(item) != {'booking_id','subject_id','purpose','operation_id','policy_version','request_ids'}:
                    raise ValueError
                booking, subject = UUID(item['booking_id']), UUID(item['subject_id'])
                operation = UUID(item['operation_id']) if item['operation_id'] is not None else None
                purpose, policy_version, ids = item['purpose'], item['policy_version'], item['request_ids']
                if (booking in seen_bookings or purpose not in {'erasure','retention'}
                    or (purpose == 'erasure') != (operation is not None)
                    or not isinstance(policy_version, str) or not 1 <= len(policy_version) <= 80
                    or not isinstance(ids, list) or len(ids) > 10000):
                    raise ValueError
                requests = {UUID(value) for value in ids}
                if len(requests) != len(ids):
                    raise ValueError
                seen_bookings.add(booking)
                purges.append((booking,subject,purpose,operation,policy_version,requests))
        receipt_prunes = None
        if 'receipt_prunes' in expected:
            receipt_prunes = _parse_receipt_prunes(payload['receipt_prunes'])
        ordinary_purges = None
        if 'ordinary_booking_purges' in expected:
            ordinary_purges = _parse_ordinary_purges(org, payload['ordinary_booking_purges'])
            if ({str(p[0]) for p in purges} & {item["booking_id"] for item in ordinary_purges}
                    or {str(request) for purge in purges for request in purge[5]}
                    & {request for item in ordinary_purges for request in item["request_ids"]}):
                raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        raise RestoreBlocked('Authenticated external restore checkpoint required') from None

    return org, entries, payload['sequence'], purges, receipt_prunes, ordinary_purges


def _parse_ordinary_purges(org, plans):
    from src.core.gdpr.restore_journal import _uuid

    fields = {"kind", "organization_id", "booking_id", "subject_id", "policy_version",
              "completed_at", "timezone", "allocation_revision", "request_ids", "deletion_authorized"}
    if type(plans) is not list or len(plans)>10000:
        raise ValueError
    bookings, all_requests = set(), set()
    for plan in plans:
        if (type(plan) is not dict or set(plan)!=fields or plan["kind"]!="ordinary_managed_booking_candidate_v1"
                or _uuid(plan["organization_id"])!=org or plan["deletion_authorized"] is not False
                or type(plan["allocation_revision"]) is not int or not 1<=plan["allocation_revision"]<2**63
                or type(plan["policy_version"]) is not str or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}",plan["policy_version"])
                or type(plan["timezone"]) is not str or not re.fullmatch(r"[A-Za-z0-9_+./-]{1,100}",plan["timezone"])
                or type(plan["completed_at"]) is not str or not re.fullmatch(
                    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z",plan["completed_at"])
                or type(plan["request_ids"]) is not list or not 1<=len(plan["request_ids"])<=1000):
            raise ValueError
        datetime.fromisoformat(plan["completed_at"].replace("Z","+00:00"))
        booking = _uuid(plan["booking_id"])
        _uuid(plan["subject_id"])
        requests = [_uuid(value) for value in plan["request_ids"]]
        if booking in bookings or requests!=sorted(set(requests)) or all_requests.intersection(requests):
            raise ValueError
        bookings.add(booking)
        all_requests.update(requests)
    return plans


def _parse_receipt_prunes(items):
    if not isinstance(items, list) or len(items) > 10000:
        raise ValueError
    fields = ('request_id', 'source_message_id', 'conversation_id', 'subject_id')
    requests, sources, prunes = set(), set(), []
    for item in items:
        if not isinstance(item, dict) or set(item) != set(fields):
            raise ValueError
        request, source, conversation, subject = (UUID(item[field]) for field in fields)
        if request in requests or source in sources:
            raise ValueError
        requests.add(request)
        sources.add(source)
        prunes.append((request, source, conversation, subject))
    return prunes


def _receipt_prune_digest(org, prune):
    material = ':'.join(('melpis.receipt-prune.v1', str(org), *(str(value) for value in prune)))
    return hashlib.sha256(material.encode('utf-8')).hexdigest()


def _retained_checkpoint_path(path):
    if not isinstance(path, Path) or not path.is_absolute() or path.is_symlink():
        raise RestoreBlocked('Trusted independent checkpoint path required')
    try:
        if path.parent.resolve(strict=True) != path.parent:
            raise RestoreBlocked('Trusted independent checkpoint path required')
    except OSError:
        raise RestoreBlocked('Trusted independent checkpoint directory unavailable') from None
    return path


def retain_receipt_prune_checkpoint(path, organization_id, checkpoint, *, encryption_key,
                                     verification_key, backup_id, minimum_sequence, checkpoint_sha256):
    """Seal already authenticated evidence in the configured independent store.

    The caller owns the trusted directory, key and independent anchor. Existing
    artifacts are never overwritten. The database is untouched.
    """
    _, _, _, _, prunes, _ = _authenticate_checkpoint(
        organization_id, checkpoint, verification_key=verification_key, backup_id=backup_id,
        minimum_sequence=minimum_sequence, checkpoint_sha256=checkpoint_sha256,
    )
    if prunes is None:
        raise RestoreBlocked('Authenticated receipt expiry checkpoint required')
    path = _retained_checkpoint_path(path)
    try:
        raw = json.dumps(checkpoint, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        sealed = Fernet(encryption_key).encrypt(raw)
        if len(sealed)>_MAX_SEALED_CHECKPOINT:
            raise ValueError
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as output:
            output.write(sealed)
            output.flush()
            os.fsync(output.fileno())
        if os.name != 'nt':
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except (OSError, TypeError, ValueError):
        raise RestoreBlocked('Unable to retain encrypted receipt checkpoint') from None
    return checkpoint_sha256


async def bind_retained_receipt_prune_checkpoint(conn, organization_id, path, *, encryption_key,
                                                 verification_key, backup_id, minimum_sequence,
                                                 checkpoint_sha256):
    """Read back independently retained, authenticated evidence before pruning.

    The verification key, backup identity, digest and minimum sequence must
    originate in the trusted independent store. Never accept client paths.
    """
    path = _retained_checkpoint_path(path)
    try:
        with path.open('rb') as source:
            sealed = source.read(_MAX_SEALED_CHECKPOINT+1)
        if not sealed or len(sealed)>_MAX_SEALED_CHECKPOINT:
            raise ValueError
        checkpoint = json.loads(Fernet(encryption_key).decrypt(sealed))
    except (OSError, ValueError, TypeError, InvalidToken):
        raise RestoreBlocked('Encrypted receipt checkpoint unavailable') from None
    return await _bind_receipt_prune_checkpoint(
        conn, organization_id, checkpoint, verification_key=verification_key, backup_id=backup_id,
        minimum_sequence=minimum_sequence, checkpoint_sha256=checkpoint_sha256,
    )


async def _bind_receipt_prune_checkpoint(conn, organization_id, checkpoint, *,
                                         verification_key, backup_id, minimum_sequence, checkpoint_sha256):
    """Bind a cumulative checkpoint before pruning.

    The key, backup identity, digest and minimum sequence come from the trusted
    recovery store. The caller must durably retain the checkpoint first. This
    function only records owner-scoped commitments and never deletes content.
    """
    org, _, sequence, _, prunes, _ = _authenticate_checkpoint(
        organization_id, checkpoint, verification_key=verification_key, backup_id=backup_id,
        minimum_sequence=minimum_sequence, checkpoint_sha256=checkpoint_sha256,
    )
    if prunes is None or not conn.is_in_transaction() or await conn.fetchval('SHOW transaction_isolation') != 'read committed':
        raise RestoreBlocked('Current receipt checkpoint requires a read committed transaction')
    await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1::text,606))', str(org))
    expected = {prune[0]: _receipt_prune_digest(org, prune) for prune in prunes}
    bindings = await conn.fetch('''SELECT request_id,tuple_sha256,checkpoint_sequence
        FROM c24_receipt_prune_checkpoint_bindings WHERE organization_id=$1''', org)
    if any(expected.get(row['request_id']) != row['tuple_sha256'] or row['checkpoint_sequence'] > sequence
           for row in bindings):
        raise RestoreBlocked('Receipt checkpoint omits prior commitments')
    for request, source, conversation, subject in prunes:
        if not await conn.fetchval('SELECT public.c24_bind_receipt_prune_checkpoint($1,$2,$3,$4,$5,$6,$7)',
                                   org, request, source, conversation, subject, checkpoint_sha256, sequence):
            raise RestoreBlocked('Receipt checkpoint does not match verified source')
    return len(prunes)


async def verify_restore_journal_replay(conn, organization_id, payload, signature, *,
                                        stay_checkpoint=None, checkpoint_verification_key=None,
                                        backup_id=None, minimum_sequence=None, checkpoint_sha256=None, **trust):
    """Check scoped replay against signed evidence and an independently pinned tip."""
    org = UUID(str(organization_id))
    try:
        verified = verify_erasure_segment(payload, signature, **trust)
    except RestoreJournalInvalid:
        raise RestoreBlocked('Authenticated external restore journal required') from None
    purges, receipt_prunes, ordinary_purges = None, None, None
    if stay_checkpoint is not None:
        _, checkpoint_entries, checkpoint_sequence, purges, receipt_prunes, ordinary_purges = _authenticate_checkpoint(
            org, stay_checkpoint, verification_key=checkpoint_verification_key,
            backup_id=backup_id, minimum_sequence=minimum_sequence, checkpoint_sha256=checkpoint_sha256,
        )
        if purges is None:
            raise RestoreBlocked('Authenticated stay purge journal required')
        await _verify_replayed_erasures(
            conn, org, checkpoint_entries, checkpoint_sequence, purges=purges, receipt_prunes=receipt_prunes,
            ordinary_purges=ordinary_purges,
        )
    scoped = tuple(entry for entry in verified if entry.organization_id == org)
    return await _verify_replayed_erasures(
        conn, org, [(entry.operation_id, entry.scope, entry.subject_id) for entry in scoped],
        trust['independent_tip'].sequence,
        fingerprints={entry.operation_id: (entry.identifier_digest, entry.key_id) for entry in scoped},
        purges=purges, receipt_prunes=receipt_prunes,
        ordinary_purges=ordinary_purges,
    )


async def _verify_replayed_erasures(conn, org, entries, sequence, *, fingerprints=None, purges=None,
                                  receipt_prunes=None, ordinary_purges=None):
    if not conn.is_in_transaction():
        raise RestoreBlocked('Restore verification requires a quarantined transaction')
    if await conn.fetchval('SHOW transaction_isolation') != 'read committed':
        raise RestoreBlocked('Restore verification requires read committed isolation')
    if not await conn.fetchval('SELECT EXISTS(SELECT 1 FROM organizations WHERE id=$1)', org):
        raise RestoreBlocked('Restored organization is unavailable')
    await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1::text,606))', str(org))
    stay_schema = await conn.fetchval("SELECT to_regclass('public.booking_stays') IS NOT NULL")
    if stay_schema:
        if purges is None:
            raise RestoreBlocked('Authenticated stay purge journal required')
        actual = await conn.fetch('''SELECT booking_id,original_subject_id,purpose,operation_id,policy_version,
            COALESCE(to_jsonb(booking_stay_purge_fences)->>'purge_kind','stay') AS purge_kind
            FROM booking_stay_purge_fences WHERE organization_id=$1''', org)
        expected_fences = {(b,s,p,o,v) for b,s,p,o,v,_ in purges}
        expected_ordinary = {(UUID(plan["booking_id"]), UUID(plan["subject_id"]), "retention", None, plan["policy_version"])
                             for plan in (ordinary_purges or [])}
        def fence_tuple(row):
            return tuple(row[field] for field in ('booking_id','original_subject_id','purpose','operation_id','policy_version'))
        if ({fence_tuple(row) for row in actual if row["purge_kind"]=="stay"} != expected_fences
                or {fence_tuple(row) for row in actual if row["purge_kind"]=="ordinary"} != expected_ordinary
                or any(row["purge_kind"] not in ("stay","ordinary") for row in actual)):
            raise RestoreBlocked('Stay purge fences do not match authenticated journal')
        if await conn.fetchval('SELECT count(*) FROM booking_stay_tombstones WHERE organization_id=$1', org) != (
                sum(len(p[5]) for p in purges)+sum(len(plan["request_ids"]) for plan in (ordinary_purges or []))):
            raise RestoreBlocked('Stay purge tombstones do not match authenticated journal')
        for booking, subject, purpose, operation, version, requests in purges:
            rows = await conn.fetch('''SELECT request_id,original_subject_id,purpose,operation_id,policy_version
                FROM booking_stay_tombstones WHERE organization_id=$1 AND booking_id=$2''', org, booking)
            if ({r['request_id'] for r in rows} != requests
                or any((r['original_subject_id'],r['purpose'],r['operation_id'],r['policy_version']) != (subject,purpose,operation,version) for r in rows)
                or await conn.fetchval('''SELECT EXISTS(SELECT 1 FROM bookings WHERE organization_id=$1 AND id=$2)
                    OR EXISTS(SELECT 1 FROM booking_stays WHERE organization_id=$1 AND booking_id=$2)
                    OR EXISTS(SELECT 1 FROM booking_allocation_requests WHERE organization_id=$1 AND booking_id=$2)
                    OR EXISTS(SELECT 1 FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2)
                    OR EXISTS(SELECT 1 FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2)
                    OR EXISTS(SELECT 1 FROM booking_confirmation_outbox WHERE organization_id=$1 AND booking_id=$2)
                    OR EXISTS(SELECT 1 FROM external_booking_sync WHERE organization_id=$1 AND internal_booking_id=$2)
                    OR EXISTS(SELECT 1 FROM booking_stay_completion_evidence WHERE organization_id=$1 AND booking_id=$2)''', org, booking)):
                raise RestoreBlocked('Stay purge copies do not match authenticated journal')
    elif purges:
        raise RestoreBlocked('Stay purge schema required by authenticated journal')
    await _verify_ordinary_purges(conn, org, ordinary_purges)
    await _verify_replayed_receipt_prunes(conn, org, receipt_prunes)
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
                  AND (m.deleted_at IS NULL OR m.content IS DISTINCT FROM '{}'::jsonb
                       OR m.content_text IS NOT NULL OR m.ai_reply_cache IS NOT NULL
                       OR m.error_title IS NOT NULL OR m.error_details IS NOT NULL))
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


async def _verify_ordinary_purges(conn, org, plans):
    relation = await conn.fetchval("SELECT to_regclass('public.ordinary_booking_purge_bindings') IS NOT NULL")
    if not relation:
        if plans:
            raise RestoreBlocked("Ordinary booking purge schema unavailable")
        return
    rows = await conn.fetch("""SELECT booking_id,plan FROM ordinary_booking_purge_bindings
        WHERE organization_id=$1""", org)
    actual = {row["booking_id"]: json.loads(row["plan"]) if isinstance(row["plan"], str)
              else row["plan"] for row in rows}
    expected = {UUID(plan["booking_id"]): plan for plan in (plans or [])}
    if actual != expected:
        raise RestoreBlocked("Ordinary booking purge commitments do not match authenticated journal")
    for booking, plan in expected.items():
        if not await conn.fetchval("SELECT public.ordinary_verify_booking_retention_purge($1,$2,$3::jsonb)",
                                   org, booking, json.dumps(plan, sort_keys=True)):
            raise RestoreBlocked("Ordinary booking purge postconditions do not match authenticated journal")
        tombstones = await conn.fetch("""SELECT request_id,original_subject_id,purpose,operation_id,policy_version
            FROM booking_stay_tombstones WHERE organization_id=$1 AND booking_id=$2""", org, booking)
        if ({str(row["request_id"]) for row in tombstones} != set(plan["request_ids"])
                or any(str(row["original_subject_id"])!=plan["subject_id"]
                       or row["purpose"]!="retention" or row["operation_id"] is not None
                       or row["policy_version"]!=plan["policy_version"] for row in tombstones)):
            raise RestoreBlocked("Ordinary booking request fences do not match authenticated journal")
        if await conn.fetchval("""SELECT EXISTS(SELECT FROM bookings WHERE organization_id=$1 AND id=$2)
            OR EXISTS(SELECT FROM ordinary_booking_completion_evidence WHERE organization_id=$1 AND booking_id=$2)
            OR EXISTS(SELECT FROM booking_allocation_requests WHERE organization_id=$1 AND booking_id=$2)
            OR EXISTS(SELECT FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2)
            OR EXISTS(SELECT FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2)
            OR EXISTS(SELECT FROM booking_confirmation_outbox WHERE organization_id=$1 AND booking_id=$2)
            OR EXISTS(SELECT FROM external_booking_sync WHERE organization_id=$1 AND internal_booking_id=$2)""", org, booking):
            raise RestoreBlocked("Ordinary booking purge copies remain")


async def _verify_replayed_receipt_prunes(conn, org, prunes):
    schema_ready = await conn.fetchval("SELECT to_regclass('public.c24_receipt_prune_checkpoint_bindings') IS NOT NULL")
    if not schema_ready:
        if prunes:
            raise RestoreBlocked('Receipt expiry schema required by authenticated checkpoint')
        return
    if prunes is None:
        raise RestoreBlocked('Authenticated receipt expiry checkpoint required')
    expected = {prune[0]: _receipt_prune_digest(org, prune) for prune in prunes}
    commitments = await conn.fetch('''SELECT request_id,tuple_sha256 FROM c24_receipt_prune_checkpoint_bindings
        WHERE organization_id=$1''', org)
    if {row['request_id']: row['tuple_sha256'] for row in commitments} != expected:
        raise RestoreBlocked('Receipt expiry commitments do not match authenticated checkpoint')
    for request, source, conversation, subject in prunes:
        if not await conn.fetchval("""SELECT EXISTS(SELECT FROM messages m
            WHERE m.organization_id=$1 AND m.id=$2 AND m.conversation_id=$3 AND m.c24_original_subject_id=$4
              AND m.direction='inbound' AND m.wam_id IS NOT NULL AND m.content_purged_at IS NOT NULL
              AND m.content='{}'::jsonb AND m.content_text IS NULL AND m.ai_reply_cache IS NULL)
            AND EXISTS(SELECT FROM c24_receipt_cleanup_audit WHERE organization_id=$1 AND request_id=$5)
            AND NOT EXISTS(SELECT FROM booking_conversation_requests
                WHERE organization_id=$1 AND (request_id=$5 OR source_message_id=$2))
            AND NOT EXISTS(SELECT FROM c24_source_fences
                WHERE organization_id=$1 AND (request_id=$5 OR source_message_id=$2))
            AND NOT EXISTS(SELECT FROM c24_suppression_lifecycle_audit
                WHERE organization_id=$1 AND action='retention' AND (source_record_id=$5 OR c24_source_message_id=$2))
            AND public.c24_receipt_source_copies_absent($1,$2)""",
            org, source, conversation, subject, request,
        ):
            raise RestoreBlocked('Newer receipt expiry checkpoint has not been replayed')
