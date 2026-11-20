from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
import sqlite3
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.core.gdpr.checkpoint_pages import publish_checkpoint_pages
from src.core.gdpr.independent_tip import IndependentTipStore
from src.core.gdpr.journal_publication import CheckpointPin, LocalJournalStore, _canonical, _checkpoint_name, publish_checkpoint
from src.core.gdpr.restore_checkpoint import RestoreBlocked
from src.core.gdpr.restore_policy_state import PolicyStatePin


@pytest.fixture
def promotion(tmp_path):
    objects = tmp_path / "objects"
    objects.mkdir(mode=0o700)
    installation, org = uuid4(), uuid4()
    store = LocalJournalStore(objects)
    path = tmp_path / "tip.sqlite"
    authority = IndependentTipStore.initialize(path, installation)
    signer, journal_key = Ed25519PrivateKey.generate(), Fernet.generate_key()
    keys = dict(verification_key=bytes(range(32)), encryption_key=Fernet.generate_key())
    policy_keys = dict(verification_key=bytes(range(1, 33)), encryption_key=Fernet.generate_key())
    trust = dict(trusted_public_key=signer.public_key(), journal_encryption_key=journal_key,
                 checkpoint_verification_key=keys["verification_key"], checkpoint_encryption_key=keys["encryption_key"],
                 policy_verification_key=policy_keys["verification_key"], policy_encryption_key=policy_keys["encryption_key"],
                 audit_at=datetime(2026, 11, 20, tzinfo=timezone.utc))
    backups, current, candidates = [], {}, {}
    for number, backup in enumerate(("alpha", "beta"), start=1):
        backups.append(dict(backup_id=backup, manifest_sha256=str(number) * 64,
                            completed_at="2026-11-18T00:00:00+00:00", organizations=[str(org)],
                            anchor=dict(sequence=0, sha256="0" * 64), exception=None))
        evidence = dict(operation_id=str(uuid4()), scope="organization", subject_id=None)
        payload = dict(version="c24-restore-v4", organization_id=str(org), backup_id=backup, sequence=1,
                       complete=True, erasures=[evidence], stay_purges=[], receipt_prunes=[], ordinary_booking_purges=[])
        pin = publish_checkpoint(store, installation, payload, **keys)
        current[org, backup] = authority.advance_checkpoint(store, None, pin, **keys)
        candidates[org, backup] = publish_checkpoint_pages(
            store, installation, dict(payload, version="c24-restore-v5", sequence=2), previous_pin=pin, **keys)
        policy = dict(version="gdpr-policy-state-v1", installation_id=str(installation), organization_id=str(org),
                      backup_id=backup, sequence=1, layout="baseline",
                      rows=dict(gdpr_policies=[], gdpr_retention_holds=[], gdpr_retention_hold_events=[]))
        raw = _canonical(policy)
        signature = hmac.new(policy_keys["verification_key"], b"melpis.restore.policy-state.v1\0" + raw, sha256).hexdigest()
        sealed = Fernet(policy_keys["encryption_key"]).encrypt(_canonical(dict(payload=policy, signature=signature)))
        authority.publish_policy(store, None, PolicyStatePin(installation, org, backup, 1, sha256(raw).hexdigest()),
                                 sealed, **policy_keys)
    inventory = json.dumps(dict(version=1, installation_id=str(installation), backups=backups)).encode()
    return authority, store, path, inventory, current, candidates, trust


def _promote(promotion, *, store=None, candidates=None):
    authority, objects, _, inventory, _, intended, trust = promotion
    return authority.promote_paged_checkpoints(objects if store is None else store, inventory,
                latest_inventory_sha256=sha256(inventory).hexdigest(),
                candidates=intended if candidates is None else candidates, **trust)


def test_promotion_advances_exact_inventory_in_one_operation(promotion):
    authority, store, path, inventory, current, candidates, trust = promotion
    inventory_digest = sha256(inventory).hexdigest()
    with pytest.raises(RestoreBlocked):
        authority.assert_paged_inventory(inventory_digest)
    with pytest.raises(RestoreBlocked):
        (scope, candidate), *_ = candidates.items()
        authority.advance_checkpoint(store, current[scope], candidate,
            verification_key=trust["checkpoint_verification_key"], encryption_key=trust["checkpoint_encryption_key"])
    assert _promote(promotion) == {"promoted_checkpoints": 2, "authority_advanced": True,
                                   "traffic_authorized": False, "deletion_authorized": False}
    assert {scope: authority.checkpoint_pin(*scope) for scope in current} == candidates
    fresh = IndependentTipStore(path, authority.installation_id)
    fresh.assert_paged_inventory(inventory_digest)
    for invalid in (None, "f" * 64, "invalid"):
        with pytest.raises(RestoreBlocked):
            fresh.assert_paged_inventory(invalid)


def test_promotion_rejects_incomplete_inventory_and_missing_candidate_page(promotion):
    authority, store, _, _, current, candidates, _ = promotion
    with pytest.raises(RestoreBlocked):
        _promote(promotion, candidates=dict(list(candidates.items())[:1]))
    candidate = list(candidates.values())[1]
    from src.core.gdpr.checkpoint_pages import _MANIFEST_DOMAIN, _read_object
    trust = promotion[-1]
    manifest, _ = _read_object(store, _checkpoint_name(authority.installation_id, candidate), candidate.sha256,
        _MANIFEST_DOMAIN, 1024 * 1024, trust["checkpoint_verification_key"],
        Fernet(trust["checkpoint_encryption_key"]))
    page = CheckpointPin(candidate.organization_id, candidate.backup_id, candidate.sequence,
                         manifest["pages"][0]["sha256"])
    store._path(_checkpoint_name(authority.installation_id, page)).unlink()
    with pytest.raises(RestoreBlocked):
        _promote(promotion)
    assert {scope: authority.checkpoint_pin(*scope) for scope in current} == current


def test_promotion_rolls_back_sqlite_batch_on_second_pin_write_failure(promotion):
    authority, _, path, inventory, current, _, _ = promotion
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TRIGGER fixture_block_second_pin BEFORE UPDATE ON checkpoints
            WHEN NEW.backup='beta' BEGIN SELECT RAISE(ABORT, 'synthetic pin write failure'); END""")
    with pytest.raises(RestoreBlocked):
        _promote(promotion)
    assert {scope: authority.checkpoint_pin(*scope) for scope in current} == current
    with pytest.raises(RestoreBlocked):
        authority.assert_paged_inventory(sha256(inventory).hexdigest())


def test_promotion_rejects_same_tip_key_epoch_change_after_assessment(promotion, monkeypatch):
    from src.core.gdpr import backup_coverage

    authority, _, _, inventory, current, _, _ = promotion
    key = Fernet.generate_key()
    signer = Ed25519PrivateKey.generate()
    tip = authority.current()
    real_assessment = backup_coverage.assess_paged_backup_candidates

    def assessment_then_rotate(*args, **kwargs):
        proof = real_assessment(*args, **kwargs)
        authority.approve_segment_epoch(tip, signer.public_key(), key)
        return proof

    monkeypatch.setattr(backup_coverage, "assess_paged_backup_candidates", assessment_then_rotate)
    with pytest.raises(RestoreBlocked):
        _promote(promotion)
    assert authority.current() == tip
    assert {scope: authority.checkpoint_pin(*scope) for scope in current} == current
    with pytest.raises(RestoreBlocked):
        authority.assert_paged_inventory(sha256(inventory).hexdigest())


def test_active_replay_rejects_hold_present_only_on_another_backup(promotion):
    from src.core.gdpr.backup_coverage import _backups
    from src.core.gdpr.restore_coordinator import _current_restore_policies

    authority, store, _, inventory, _, candidates, trust = promotion
    _promote(promotion)
    org = next(iter(candidates))[0]
    backups = list(_backups(inventory, sha256(inventory).hexdigest(), authority.installation_id, trust["audit_at"]))
    keys = dict(verification_key=trust["policy_verification_key"], encryption_key=trust["policy_encryption_key"])
    assert set(_current_restore_policies(authority, store, {org}, "alpha", backups, keys)) == {
        (org, "alpha"), (org, "beta")}
    previous = authority.policy_pin(org, "beta")
    sealed, _ = authority.read_policy(store, previous, **keys)
    cipher = Fernet(keys["encryption_key"])
    payload = json.loads(cipher.decrypt(sealed))["payload"]
    payload["sequence"] = 2
    payload["rows"]["gdpr_retention_holds"] = [dict(organization_id=str(org), hold_id=str(uuid4()),
        category="messages_content", scope="organization", scope_id=None, reason_code="litigation",
        authority_reference="redacted-test", approved_by="qa", approved_at="2026-11-19T00:00:00+00:00",
        review_by="2026-12-19T00:00:00+00:00")]
    raw = _canonical(payload)
    signature = hmac.new(keys["verification_key"], b"melpis.restore.policy-state.v1\0" + raw, sha256).hexdigest()
    authority.publish_policy(store, previous, PolicyStatePin(authority.installation_id, org, "beta", 2,
        sha256(raw).hexdigest()), cipher.encrypt(_canonical(dict(payload=payload, signature=signature))), **keys)
    with pytest.raises(RestoreBlocked, match="legal holds differ"):
        _current_restore_policies(authority, store, {org}, "alpha", backups, keys)
