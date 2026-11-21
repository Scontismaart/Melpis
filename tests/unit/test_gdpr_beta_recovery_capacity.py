"""Synthetic capacity and replacement-authority recovery for the beta cohort."""

from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import json
from time import perf_counter
from uuid import UUID

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.core.gdpr.backup_coverage import assess_backup_coverage
from src.core.gdpr.checkpoint_pages import publish_checkpoint_pages, read_any_checkpoint
from src.core.gdpr.independent_tip import IndependentTipStore
from src.core.gdpr.journal_publication import LocalJournalStore, _canonical, _checkpoint_name, publish_checkpoint, publish_segment
from src.core.gdpr.restore_checkpoint import RestoreBlocked
from src.core.gdpr.restore_journal import RestoreJournalInvalid
from src.core.gdpr.restore_policy_state import PolicyStatePin


def _synthetic_contact_erasure(org, ordinal):
    return dict(organization_id=str(org), operation_id=str(UUID(int=10000 + ordinal)), scope="contact",
                subject_id=str(UUID(int=20000 + ordinal)),
                identifier_digest=sha256(f"synthetic-subject:{ordinal}".encode()).hexdigest(),
                key_id="0123456789abcdef")


def _pin_policy(authority, store, org, backup, verification_key, encryption_key):
    policy = dict(version="gdpr-policy-state-v1", installation_id=str(authority.installation_id),
                  organization_id=str(org), backup_id=backup, sequence=1, layout="baseline",
                  rows=dict(gdpr_policies=[], gdpr_retention_holds=[], gdpr_retention_hold_events=[]))
    raw = _canonical(policy)
    signature = hmac.new(verification_key, b"melpis.restore.policy-state.v1\0" + raw, sha256).hexdigest()
    sealed = Fernet(encryption_key).encrypt(_canonical(dict(payload=policy, signature=signature)))
    authority.publish_policy(store, None, PolicyStatePin(authority.installation_id, org, backup, 1,
                             sha256(raw).hexdigest()), sealed, verification_key=verification_key,
                             encryption_key=encryption_key)


def test_beta_retained_backups_promote_and_recover_exact_authority(tmp_path):
    started = perf_counter()
    installation = UUID(int=100)
    organizations = tuple(UUID(int=200 + index) for index in range(5))
    objects, authority_dir, replacement = (tmp_path / name for name in ("objects", "authority", "replacement"))
    for directory in (objects, authority_dir, replacement):
        directory.mkdir(mode=0o700)
    store = LocalJournalStore(objects)
    authority = IndependentTipStore.initialize(authority_dir / "tip.sqlite", installation)
    signer = Ed25519PrivateKey.generate()
    journal_key, checkpoint_cipher, policy_cipher, recovery_key = (Fernet.generate_key() for _ in range(4))
    checkpoint_key, policy_key = bytes(range(32)), bytes(range(1, 33))
    checkpoint_keys = dict(verification_key=checkpoint_key, encryption_key=checkpoint_cipher)
    end = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
    trust = dict(trusted_public_key=signer.public_key(), journal_encryption_key=journal_key,
                 checkpoint_verification_key=checkpoint_key, checkpoint_encryption_key=checkpoint_cipher,
                 policy_verification_key=policy_key, policy_encryption_key=policy_cipher, audit_at=end)

    backups, erasures = [], {org: [] for org in organizations}
    tip = authority.current()
    # Six synthetic contacts erased per day. Each tenant has one daily
    # erasure, with the sixth rotating so every tenant totals 36 operations.
    for day in range(30):
        backup = f"synthetic-day-{day + 1:02d}"
        backups.append(dict(backup_id=backup, manifest_sha256=sha256(backup.encode()).hexdigest(),
                            completed_at=(end - timedelta(days=30 - day)).isoformat(),
                            organizations=[str(org) for org in organizations], anchor=asdict(tip), exception=None))
        segment = []
        for slot, org in enumerate((*organizations, organizations[day % 5]), start=1):
            record = _synthetic_contact_erasure(org, day * 6 + slot)
            segment.append(record)
            erasures[org].append({key: record[key] for key in ("operation_id", "scope", "subject_id")})
        next_tip = publish_segment(store, installation, tip, segment, signer=signer, encryption_key=journal_key)
        authority.advance(store, tip, next_tip, trusted_public_key=signer.public_key(), encryption_key=journal_key)
        tip = next_tip
    assert tip.sequence == 180
    assert [len(records) for records in erasures.values()] == [36] * 5

    inventory = _canonical(dict(version=1, installation_id=str(installation), backups=backups))
    inventory_digest = sha256(inventory).hexdigest()
    candidates = {}
    for row in backups:
        backup = row["backup_id"]
        for org in organizations:
            legacy = dict(version="c24-restore-v4", organization_id=str(org), backup_id=backup, sequence=1,
                          complete=True, erasures=erasures[org], stay_purges=[], receipt_prunes=[],
                          ordinary_booking_purges=[])
            legacy_pin = publish_checkpoint(store, installation, legacy, **checkpoint_keys)
            authority.advance_checkpoint(store, None, legacy_pin, **checkpoint_keys)
            _pin_policy(authority, store, org, backup, policy_key, policy_cipher)
            candidates[org, backup] = publish_checkpoint_pages(
                store, installation, dict(legacy, version="c24-restore-v5", sequence=2),
                previous_pin=legacy_pin, **checkpoint_keys)
    assert len(candidates) == 150
    _, superseded_snapshot = authority.seal_for_recovery(recovery_key)
    setup_elapsed = perf_counter() - started

    promotion_started = perf_counter()
    result = authority.promote_paged_checkpoints(store, inventory, latest_inventory_sha256=inventory_digest,
                                                 candidates=candidates, **trust)
    promotion_elapsed = perf_counter() - promotion_started
    assert result == dict(promoted_checkpoints=150, authority_advanced=True, traffic_authorized=False,
                          deletion_authorized=False)

    recovery_started = perf_counter()
    pin, snapshot = authority.seal_for_recovery(recovery_key)
    destination = replacement / "tip.sqlite"
    with pytest.raises(RestoreJournalInvalid):
        IndependentTipStore(destination, installation)
    for wrong_pin, ciphertext, key in ((pin, superseded_snapshot, recovery_key),
                                       (pin, snapshot[:-1], recovery_key),
                                       (pin, snapshot, Fernet.generate_key()),
                                       (replace(pin, sha256="f" * 64), snapshot, recovery_key)):
        with pytest.raises(RestoreJournalInvalid):
            IndependentTipStore.recover(destination, installation, wrong_pin, ciphertext, key)
        assert not destination.exists()
    recovered = IndependentTipStore.recover(destination, installation, pin, snapshot, recovery_key)
    assert recovered.current() == tip
    recovered.assert_paged_inventory(inventory_digest)
    assert {scope: recovered.checkpoint_pin(*scope) for scope in candidates} == candidates
    coverage = assess_backup_coverage(recovered, store, inventory, latest_inventory_sha256=inventory_digest, **trust)
    assert coverage["backups_with_verified_recovery_evidence"] == 30
    assert coverage["overdue_backups"] == ("synthetic-day-01",)
    assert coverage["deletion_authorized"] is False
    assert coverage["minimum_checkpoint_entries_remaining"] > 0
    assert coverage["minimum_checkpoint_bytes_remaining"] > 0
    assert coverage["minimum_paged_checkpoint_pages_remaining"] > 0
    recovery_elapsed = perf_counter() - recovery_started

    # Missing and corrupted published objects never fall back to legacy pins.
    selected = candidates[organizations[0], "synthetic-day-01"]
    missing_name = _checkpoint_name(installation, selected)

    class BadObject:
        def __init__(self, corrupt):
            self.corrupt = corrupt

        def read(self, name):
            if name == missing_name:
                if self.corrupt:
                    return b"corrupted synthetic object"
                raise RestoreJournalInvalid("synthetic_missing_object")
            return store.read(name)

    for corrupt in (False, True):
        with pytest.raises(RestoreBlocked):
            read_any_checkpoint(BadObject(corrupt), installation, organizations[0], "synthetic-day-01",
                                recovered.checkpoint_pin(organizations[0], "synthetic-day-01"), **checkpoint_keys)
    object_names = [item.name for item in objects.iterdir()]
    object_bytes = sum(store._path(name).stat().st_size for name in object_names)
    print(json.dumps(dict(organizations=5, daily_inventory_backups=30, tenant_backup_pins=150, erasures=180,
                          journal_segments=30, journal_store_objects=len(object_names), journal_store_bytes=object_bytes,
                          authority_snapshot_bytes=len(snapshot), setup_seconds=round(setup_elapsed, 3),
                          promotion_seconds=round(promotion_elapsed, 3), recovery_seconds=round(recovery_elapsed, 3),
                          total_seconds=round(perf_counter() - started, 3),
                          checkpoint_entries_remaining=coverage["minimum_checkpoint_entries_remaining"],
                          checkpoint_bytes_remaining=coverage["minimum_checkpoint_bytes_remaining"],
                          checkpoint_pages_remaining=coverage["minimum_paged_checkpoint_pages_remaining"],
                          source_segments_remaining=coverage["source_chain_segments_remaining"],
                          source_entries_remaining=coverage["source_chain_entries_remaining"]), sort_keys=True))
