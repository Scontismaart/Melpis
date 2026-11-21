"""Exercise mutation admission with mixed independently pinned formats."""

from contextlib import asynccontextmanager
from uuid import uuid4

import pytest

from src.core.gdpr import restore_coordinator, restore_policy_state, source_checkpoint
from src.core.gdpr.journal_publication import CheckpointPin
from src.core.gdpr.restore_checkpoint import RestoreBlocked


class Connection:
    def __init__(self):
        self.events = []

    def is_in_transaction(self):
        return True

    async def fetchrow(self, *_args):
        return None

    async def execute(self, *_args):
        return None

    async def fetchval(self, sql, *args):
        if sql == 'SHOW transaction_isolation':
            return 'read committed'
        if sql == 'SELECT current_database()':
            return 'synthetic-restore'
        if 'public.ordinary_bind_booking_retention_checkpoint' in sql:
            self.events.append(('bind_booking', args))
        elif 'public.ordinary_admin_purge_booking' in sql:
            self.events.append(('purge_booking', args))
        return True

    @asynccontextmanager
    async def transaction(self):
        initial = len(self.events)
        try:
            yield
        except BaseException:
            del self.events[initial:]
            raise


class Authority:
    installation_id = uuid4()

    def __init__(self, pins, events):
        self.pins = pins
        self.events = events
        self.inventory_calls = 0
        self.fail_at = None

    def current(self):
        return 'tip'

    def checkpoint_pin(self, org, backup):
        pin = self.pins[backup]
        assert pin.organization_id == org
        return pin

    def policy_pin(self, org, backup):
        return org, backup

    def read_policy(self, store, pin, **_keys):
        return pin, None

    def assert_paged_inventory(self, digest, *, required=True):
        assert digest == 'f' * 64
        self.inventory_calls += 1
        if self.inventory_calls == self.fail_at:
            raise RestoreBlocked('Paged inventory changed')
        self.events.append(('inventory', digest))
        return True


@pytest.fixture
def scenario(monkeypatch):
    org = uuid4()
    old, paged = CheckpointPin(org, 'old', 8, 'a' * 64), CheckpointPin(org, 'paged', 6, 'b' * 64)
    conn = Connection()
    authority = Authority({'old': old, 'paged': paged}, conn.events)
    request = dict(zip(('request_id', 'source_message_id', 'conversation_id', 'subject_id'),
                       (str(uuid4()) for _ in range(4))))
    plan = {'booking_id': str(uuid4())}
    payloads = {
        'old': {'version': 'c24-restore-v4', 'receipt_prunes': [request], 'ordinary_booking_purges': [plan]},
        'paged': {'version': 'c24-restore-v5', 'receipt_prunes': [request], 'ordinary_booking_purges': [plan]},
    }

    def read(_store, _installation, requested_org, backup, pin, **_keys):
        assert requested_org == org and authority.pins[backup] == pin
        return payloads[backup]

    async def verify_policy(_conn, requested_org, data, *, independent_pin, **_trust):
        assert requested_org == org and data == independent_pin
        conn.events.append(('verify_policy', data))

    async def bind(_conn, requested_org, prunes, *, sequence, checkpoint_sha256):
        assert requested_org == org
        conn.events.append(('bind_receipts', (tuple(prunes), sequence, checkpoint_sha256)))
        return len(prunes)

    async def assert_quarantine(_conn, name):
        assert name == 'synthetic-restore'

    monkeypatch.setattr(restore_coordinator, '_assert_quarantine', assert_quarantine)
    monkeypatch.setattr(source_checkpoint, 'read_any_checkpoint', read)
    monkeypatch.setattr(source_checkpoint, 'assess_backup_coverage', lambda *_a, **_kw: None)
    monkeypatch.setattr(source_checkpoint, '_backups', lambda *_a: [('old', {org}, None, None), ('paged', {org}, None, None)])
    monkeypatch.setattr(source_checkpoint, '_bind_verified_receipt_prunes', bind)
    monkeypatch.setattr(restore_policy_state, 'verify_policy_state', verify_policy)
    monkeypatch.setenv('GDPR_RETENTION_EXECUTION_ENABLED', 'true')
    args = (conn, authority, object(), org, b'inventory')
    trust = dict(latest_inventory_sha256='f' * 64, checkpoint_verification_key=b'checkpoint-sign',
                 checkpoint_encryption_key=b'checkpoint-encrypt', policy_verification_key=b'policy-sign',
                 policy_encryption_key=b'policy-encrypt')
    return args, trust, payloads, request


@pytest.mark.asyncio
async def test_receipt_binding_uses_the_exact_paged_root_and_minimum_sequence(scenario):
    args, trust, _payloads, request = scenario
    assert await source_checkpoint.bind_inventory_covered_receipts(*args, **trust) == 1
    events = args[0].events
    binding = next(value for action, value in events if action == 'bind_receipts')
    assert tuple(map(str, binding[0][0])) == tuple(request.values())
    assert binding[1:] == (6, 'b' * 64)
    actions = [action for action, _ in events]
    assert actions.count('verify_policy') == 2
    assert actions.index('inventory') < actions.index('bind_receipts')
    assert actions[-1] == 'inventory'


@pytest.mark.asyncio
async def test_receipt_binding_accepts_a_legacy_backup_but_keeps_the_lowest_exact_pin(scenario):
    args, trust, payloads, _ = scenario
    payloads['old']['version'] = 'c24-restore-v3'
    old = args[1].pins['old']
    args[1].pins['old'] = CheckpointPin(old.organization_id, old.backup_id, 3, old.sha256)
    assert await source_checkpoint.bind_inventory_covered_receipts(*args, **trust) == 1
    binding = next(value for action, value in args[0].events if action == 'bind_receipts')
    assert binding[1:] == (3, 'a' * 64)
    assert args[1].inventory_calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['receipts', 'ordinary'])
async def test_changed_paged_inventory_rolls_back_source_mutation(scenario, mode):
    args, trust, _, _ = scenario
    args[1].fail_at = 2
    operation = (source_checkpoint.bind_inventory_covered_receipts if mode == 'receipts'
                 else source_checkpoint._purge_inventory_covered_ordinary_bookings_replay)
    options = {'quarantine_database': 'synthetic-restore'} if mode == 'ordinary' else {}
    with pytest.raises(RestoreBlocked, match='inventory changed'):
        await operation(*args, **options, **trust)
    assert not {'bind_receipts', 'bind_booking', 'purge_booking'}.intersection(action for action, _ in args[0].events)


@pytest.mark.asyncio
async def test_mixed_ordinary_purge_rejects_a_v3_pin_even_if_v5_is_complete(scenario):
    args, trust, payloads, _ = scenario
    payloads['old']['version'] = 'c24-restore-v3'
    with pytest.raises(RestoreBlocked, match='v4 or v5'):
        await source_checkpoint._purge_inventory_covered_ordinary_bookings_replay(
            *args, quarantine_database='synthetic-restore', **trust)
    assert not {'bind_receipts', 'bind_booking', 'purge_booking'}.intersection(action for action, _ in args[0].events)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['receipts', 'ordinary'])
async def test_legacy_only_inventory_cannot_bypass_commissioned_pins(scenario, mode):
    args, trust, payloads, _ = scenario
    payloads['paged']['version'] = 'c24-restore-v4'
    args[1].fail_at = 1
    operation = (source_checkpoint.bind_inventory_covered_receipts if mode == 'receipts'
                 else source_checkpoint._purge_inventory_covered_ordinary_bookings_replay)
    options = {'quarantine_database': 'synthetic-restore'} if mode == 'ordinary' else {}
    with pytest.raises(RestoreBlocked, match='inventory changed'):
        await operation(*args, **options, **trust)
    assert args[0].events == []


@pytest.mark.asyncio
async def test_receipt_mutation_rejects_missing_tuple_on_any_recoverable_backup(scenario):
    args, trust, payloads, _ = scenario
    payloads['paged']['receipt_prunes'] = []
    with pytest.raises(RestoreBlocked, match='differ across recoverable backups'):
        await source_checkpoint.bind_inventory_covered_receipts(*args, **trust)
    assert not any(action == 'bind_receipts' for action, _ in args[0].events)


@pytest.mark.asyncio
async def test_ordinary_purge_accepts_exact_mixed_v4_v5_plans(scenario):
    args, trust, _, _ = scenario
    assert await source_checkpoint._purge_inventory_covered_ordinary_bookings_replay(
        *args, quarantine_database='synthetic-restore', **trust) == 1
    mutations = [value for action, value in args[0].events if action in {'bind_booking', 'purge_booking'}]
    assert len(mutations) == 2 and mutations[0] == mutations[1]
    assert mutations[0][-3:] == ('b' * 64, 6, 'f' * 64)


@pytest.mark.asyncio
async def test_live_ordinary_purge_requires_external_rehearsal_attestation(scenario):
    args, trust, _payloads, _ = scenario
    with pytest.raises(RestoreBlocked, match='rehearsal'):
        await source_checkpoint.purge_inventory_covered_ordinary_bookings(*args, **trust)
    assert not {'bind_booking', 'purge_booking'}.intersection(action for action, _ in args[0].events)
