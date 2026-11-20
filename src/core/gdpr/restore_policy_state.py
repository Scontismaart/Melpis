"""Operator-only snapshot/reconciliation of current, immutable GDPR decisions.

A historical database cannot establish current policy or absence of a hold.
An external operator captures the committed authoritative rows, retains the
encrypted artifact and its exact pin independently, and reconciles the isolated
restore before retention or erasure replay. The result cannot authorize traffic.
"""

import base64
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from uuid import UUID

import asyncpg
from cryptography.fernet import Fernet, InvalidToken

from src.core.gdpr.restore_checkpoint import RestoreBlocked
from src.core.gdpr.restore_journal import _unique_object

_DOMAIN = b'melpis.restore.policy-state.v1\0'
_MAX_RECORDS = 10000
_MAX_PAYLOAD = 2 * 1024 * 1024
_MAX_SEALED = 3 * 1024 * 1024

_COLUMNS = {
    'gdpr_policies': ('organization_id', 'category', 'version', 'erasure_allowed', 'retention_days',
                      'legal_hold', 'approval_reference', 'approved_by', 'approved_at', 'valid_until'),
    'gdpr_retention_holds': ('organization_id', 'hold_id', 'category', 'scope', 'scope_id', 'reason_code',
                             'authority_reference', 'approved_by', 'approved_at', 'review_by'),
    'gdpr_retention_hold_events': ('organization_id', 'hold_id', 'event_id', 'kind', 'authority_reference',
                                  'approved_by', 'occurred_at', 'next_review_by'),
}
_KEYS = {
    'gdpr_policies': ('category', 'version'),
    'gdpr_retention_holds': ('hold_id',),
    'gdpr_retention_hold_events': ('event_id',),
}
_UUIDS = {'organization_id', 'hold_id', 'event_id', 'scope_id'}
_DATES = {'approved_at', 'valid_until', 'review_by', 'occurred_at', 'next_review_by'}
_INTS = {'retention_days', 'retention_calendar_months'}
_BOOLS = {'erasure_allowed', 'legal_hold'}
_NULLABLE = {'scope_id', 'next_review_by'} | _INTS


@dataclass(frozen=True, slots=True)
class PolicyStatePin:
    installation_id: UUID
    organization_id: UUID
    backup_id: str
    sequence: int
    sha256: str


def _canonical(document):
    return json.dumps(document, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _identity(installation_id, org, backup_id, sequence):
    return (type(installation_id) is UUID and installation_id.int != 0
            and type(org) is UUID and org.int != 0
            and type(backup_id) is str and 1 <= len(backup_id) <= 128
            and type(sequence) is int and 0 <= sequence < 2**63)


def _cipher(verification_key, encryption_key):
    if (type(verification_key) is not bytes or len(verification_key) < 32
            or hmac.compare_digest(verification_key, base64.urlsafe_b64decode(encryption_key))):
        raise ValueError
    return Fernet(encryption_key)


def _columns(layout, table):
    columns = _COLUMNS[table]
    if layout == 'calendar_months' and table == 'gdpr_policies':
        columns += ('retention_calendar_months',)
    return columns


def _sql_type(column):
    if column in _UUIDS:
        return 'uuid'
    if column in _DATES:
        return 'timestamp with time zone'
    if column in _INTS:
        return 'integer'
    if column in _BOOLS:
        return 'boolean'
    return 'text'


def _serialize(value):
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return value


def _parse_row(record, *, org, columns):
    if type(record) is not dict or set(record) != set(columns):
        raise ValueError
    result = {}
    for column in columns:
        value = record[column]
        if value is None and column in _NULLABLE:
            result[column] = None
            continue
        if column in _UUIDS:
            if type(value) is not str:
                raise ValueError
            parsed = UUID(value)
            if str(parsed) != value:
                raise ValueError
            value = parsed
        elif column in _DATES:
            if type(value) is not str:
                raise ValueError
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None or _serialize(parsed) != value:
                raise ValueError
            value = parsed
        elif column in _INTS and type(value) is not int:
            raise ValueError
        elif column in _BOOLS and type(value) is not bool:
            raise ValueError
        elif column not in _INTS | _BOOLS and type(value) is not str:
            raise ValueError
        result[column] = value
    if result['organization_id'] != org:
        raise ValueError
    return result


def _key(table, row):
    return tuple(row[name] for name in _KEYS[table])


def _parse_rows(payload, org):
    layout, tables = payload['layout'], payload['rows']
    if layout not in {'baseline', 'calendar_months'} or type(tables) is not dict or set(tables) != set(_COLUMNS):
        raise ValueError
    result, count = {}, 0
    for table in _COLUMNS:
        records = tables[table]
        if type(records) is not list:
            raise ValueError
        count += len(records)
        if count > _MAX_RECORDS:
            raise ValueError
        result[table] = {}
        for record in records:
            row = _parse_row(record, org=org, columns=_columns(layout, table))
            key = _key(table, row)
            if key in result[table]:
                raise ValueError
            result[table][key] = row
    return layout, result


def _authenticate(org, sealed, *, installation_id, backup_id, independent_pin, verification_key, encryption_key):
    try:
        pin = independent_pin
        if (type(pin) is not PolicyStatePin or not _identity(installation_id, org, backup_id, pin.sequence)
                or (pin.installation_id, pin.organization_id, pin.backup_id) != (installation_id, org, backup_id)
                or not isinstance(pin.sha256, str) or re.fullmatch(r'[0-9a-f]{64}', pin.sha256) is None
                or type(sealed) is not bytes or not 1 <= len(sealed) <= _MAX_SEALED):
            raise ValueError
        cipher = _cipher(verification_key, encryption_key)
        envelope = json.loads(cipher.decrypt(sealed), object_pairs_hook=_unique_object)
        if type(envelope) is not dict or set(envelope) != {'payload', 'signature'}:
            raise ValueError
        payload, signature = envelope['payload'], envelope['signature']
        if (type(payload) is not dict or set(payload) != {
                'version', 'installation_id', 'organization_id', 'backup_id', 'sequence', 'layout', 'rows'}
                or payload['version'] != 'gdpr-policy-state-v1'
                or payload['installation_id'] != str(installation_id) or payload['organization_id'] != str(org)
                or payload['backup_id'] != backup_id or type(payload['sequence']) is not int
                or payload['sequence'] != pin.sequence or type(signature) is not str):
            raise ValueError
        raw = _canonical(payload)
        if (len(raw) > _MAX_PAYLOAD or not hmac.compare_digest(sha256(raw).hexdigest(), pin.sha256)
                or not hmac.compare_digest(hmac.new(verification_key, _DOMAIN + raw, sha256).hexdigest(), signature)):
            raise ValueError
        return _parse_rows(payload, org)
    except (ValueError, TypeError, KeyError, UnicodeError, OverflowError, RecursionError, InvalidToken):
        raise RestoreBlocked('Authenticated independent policy state required') from None


async def _lock(conn, org, *, capture=False, writable=False):
    if not conn.is_in_transaction() or await conn.fetchval('SHOW transaction_isolation') != 'read committed':
        raise RestoreBlocked('Policy-state operation requires a read committed transaction')
    read_only = await conn.fetchval('SHOW transaction_read_only') == 'on'
    if (capture and not read_only) or (writable and read_only):
        raise RestoreBlocked('Policy-state transaction mode invalid')
    owner = await conn.fetchval('''SELECT current_user=session_user AND count(*)=3
        AND bool_and(pg_get_userbyid(c.relowner)=current_user)
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind='r' AND c.relname=ANY($1::text[])''', list(_COLUMNS))
    if not owner:
        raise RestoreBlocked('Policy-state operation requires lifecycle owner')
    # The immutable policy/hold insertion triggers take this same exclusive
    # tenant lock. READ COMMITTED sees all previously committed decisions.
    await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1::text,606))', str(org))
    if not await conn.fetchval('SELECT EXISTS(SELECT FROM public.organizations WHERE id=$1)', org):
        raise RestoreBlocked('Policy-state organization unavailable')


async def _layout(conn):
    layout = 'baseline'
    for table, base in _COLUMNS.items():
        rows = await conn.fetch('''SELECT attname::text AS field, format_type(atttypid,atttypmod) AS actual_type
            FROM pg_attribute WHERE attrelid=to_regclass($1) AND attnum>0 AND NOT attisdropped''', 'public.' + table)
        fields = {row['field']: row['actual_type'] for row in rows}
        baseline = {column: _sql_type(column) for column in base}
        if table == 'gdpr_policies' and fields == baseline | {'retention_calendar_months': 'integer'}:
            layout = 'calendar_months'
        elif fields != baseline:
            raise RestoreBlocked('Unknown policy schema; reconciliation requires review')
    return layout


async def _read_rows(conn, org, layout):
    policies = await conn.fetch('''SELECT organization_id,category,version,erasure_allowed,retention_days,
        legal_hold,approval_reference,approved_by,approved_at,valid_until,
        (to_jsonb(p)->>'retention_calendar_months')::integer AS retention_calendar_months
        FROM public.gdpr_policies p WHERE organization_id=$1 ORDER BY category,version LIMIT $2''',
        org, _MAX_RECORDS + 1)
    holds = await conn.fetch('''SELECT organization_id,hold_id,category,scope,scope_id,reason_code,
        authority_reference,approved_by,approved_at,review_by FROM public.gdpr_retention_holds
        WHERE organization_id=$1 ORDER BY hold_id LIMIT $2''', org, _MAX_RECORDS + 1)
    events = await conn.fetch('''SELECT organization_id,hold_id,event_id,kind,authority_reference,
        approved_by,occurred_at,next_review_by FROM public.gdpr_retention_hold_events
        WHERE organization_id=$1 ORDER BY event_id LIMIT $2''', org, _MAX_RECORDS + 1)
    if len(policies) + len(holds) + len(events) > _MAX_RECORDS:
        raise RestoreBlocked('Policy-state record limit exceeded')
    result = {}
    for table, rows in zip(_COLUMNS, (policies, holds, events), strict=True):
        columns = _columns(layout, table)
        records = {_key(table, row): {column: row[column] for column in columns} for row in rows}
        if len(records) != len(rows):
            raise RestoreBlocked('Ambiguous policy-state record identity')
        result[table] = records
    return result


async def _append_rows(conn, layout, missing):
    def parameters(table, entries):
        columns = _columns(layout, table)
        return [tuple(row[column] for column in columns) for row in entries]

    policies = parameters('gdpr_policies', missing['gdpr_policies'])
    if layout == 'calendar_months':
        await conn.executemany('''INSERT INTO public.gdpr_policies(organization_id,category,version,
            erasure_allowed,retention_days,legal_hold,approval_reference,approved_by,approved_at,
            valid_until,retention_calendar_months) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)''', policies)
    else:
        await conn.executemany('''INSERT INTO public.gdpr_policies(organization_id,category,version,
            erasure_allowed,retention_days,legal_hold,approval_reference,approved_by,approved_at,
            valid_until) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)''', policies)
    await conn.executemany('''INSERT INTO public.gdpr_retention_holds(organization_id,hold_id,category,
        scope,scope_id,reason_code,authority_reference,approved_by,approved_at,review_by)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)''',
        parameters('gdpr_retention_holds', missing['gdpr_retention_holds']))
    # Reviews at the same timestamp precede terminal release. The original
    # trigger rejects records that conflict with any imported event chronology.
    events = sorted(missing['gdpr_retention_hold_events'], key=lambda row: (
        row['hold_id'], row['occurred_at'], row['kind'] == 'released', row['event_id']))
    await conn.executemany('''INSERT INTO public.gdpr_retention_hold_events(organization_id,hold_id,
        event_id,kind,authority_reference,approved_by,occurred_at,next_review_by)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8)''', parameters('gdpr_retention_hold_events', events))


async def capture_policy_state(conn, organization_id, *, installation_id, backup_id, sequence,
                               verification_key, encryption_key):
    """Capture committed authoritative state in an owner read-only transaction.

    Persist the returned pin independently with the current checkpoint/tip. A
    stale, signed but no longer independently current pin is not proof of currency.
    """
    try:
        org = UUID(str(organization_id))
        if not _identity(installation_id, org, backup_id, sequence):
            raise ValueError
        cipher = _cipher(verification_key, encryption_key)
        await _lock(conn, org, capture=True)
        layout = await _layout(conn)
        rows = await _read_rows(conn, org, layout)
        payload = dict(version='gdpr-policy-state-v1', installation_id=str(installation_id),
                       organization_id=str(org), backup_id=backup_id, sequence=sequence, layout=layout,
                       rows={table: [{column: _serialize(value) for column, value in row.items()}
                                     for row in records.values()] for table, records in rows.items()})
        raw = _canonical(payload)
        if len(raw) > _MAX_PAYLOAD:
            raise ValueError
        pin = PolicyStatePin(installation_id, org, backup_id, sequence, sha256(raw).hexdigest())
        sealed = cipher.encrypt(_canonical({'payload': payload, 'signature': hmac.new(
            verification_key, _DOMAIN + raw, sha256).hexdigest()}))
        _authenticate(org, sealed, installation_id=installation_id, backup_id=backup_id,
                      independent_pin=pin, verification_key=verification_key, encryption_key=encryption_key)
        return pin, sealed
    except (ValueError, TypeError, OverflowError, RecursionError, asyncpg.PostgresError, TimeoutError):
        raise RestoreBlocked('Authoritative policy-state capture unavailable') from None


async def _verify_or_reconcile(conn, organization_id, sealed, *, reconcile, installation_id, backup_id,
                               independent_pin, verification_key, encryption_key):
    try:
        org = UUID(str(organization_id))
        layout, expected = _authenticate(org, sealed, installation_id=installation_id, backup_id=backup_id,
                                         independent_pin=independent_pin, verification_key=verification_key,
                                         encryption_key=encryption_key)
        await _lock(conn, org, writable=reconcile)
        if await _layout(conn) != layout:
            raise RestoreBlocked('Restored and authoritative policy schemas differ')
        async with conn.transaction():
            actual = await _read_rows(conn, org, layout)
            if any(expected[table].get(key) != row for table in _COLUMNS for key, row in actual[table].items()):
                raise RestoreBlocked('Unknown or conflicting restored policy evidence')
            inserted = 0
            if reconcile:
                missing = {table: [row for key, row in expected[table].items() if key not in actual[table]]
                           for table in _COLUMNS}
                await _append_rows(conn, layout, missing)
                inserted = sum(len(rows) for rows in missing.values())
                actual = await _read_rows(conn, org, layout)
            if actual != expected:
                raise RestoreBlocked('Restored policy and hold evidence incomplete')
        return {'organization_id': str(org), 'sequence': independent_pin.sequence,
                'records': sum(len(rows) for rows in expected.values()), 'inserted': inserted,
                'traffic_authorized': False}
    except (ValueError, TypeError, OverflowError, RecursionError, asyncpg.PostgresError, TimeoutError):
        raise RestoreBlocked('Policy-state reconciliation unavailable') from None


async def verify_policy_state(conn, organization_id, sealed, **trust):
    """Require exact current policy evidence; never apply changes."""
    return await _verify_or_reconcile(conn, organization_id, sealed, reconcile=False, **trust)


async def reconcile_policy_state(conn, organization_id, sealed, **trust):
    """Append only missing authenticated decisions in the isolated restored DB.

    Existing conflicting/extra rows and chronology violations fail closed. The
    operator must separately resolve current holds conflicting with a recorded
    historical deletion; this function never authorizes any deletion or traffic.
    """
    return await _verify_or_reconcile(conn, organization_id, sealed, reconcile=True, **trust)
