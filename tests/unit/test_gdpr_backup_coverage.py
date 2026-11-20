from datetime import datetime, timezone
from hashlib import sha256
import json
import hmac
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.core.gdpr.independent_tip import IndependentTipStore
from src.core.gdpr.journal_publication import LocalJournalStore, publish_checkpoint, publish_segment
from src.core.gdpr.restore_checkpoint import RestoreBlocked
from src.core.gdpr.restore_policy_state import PolicyStatePin


def test_coverage_requires_every_independently_pinned_backup_and_reports_headroom(tmp_path):
    from src.core.gdpr.backup_coverage import assess_backup_coverage

    objects, authority_dir = tmp_path / "objects", tmp_path / "authority"
    objects.mkdir(mode=0o700)
    authority_dir.mkdir(mode=0o700)
    installation, org = uuid4(), uuid4()
    store = LocalJournalStore(objects)
    authority = IndependentTipStore.initialize(authority_dir / "tip.sqlite", installation)
    journal_key, signer = Fernet.generate_key(), Ed25519PrivateKey.generate()
    key, ciphertext = bytes(range(32)), Fernet.generate_key()
    policy_key, policy_cipher = bytes(range(1, 33)), Fernet.generate_key()
    old_backup = {"backup_id": "old", "manifest_sha256": "1" * 64,
                  "completed_at": "2026-10-01T12:00:00+00:00",
                  "organizations": [str(org)], "anchor": {"sequence": 0, "sha256": "0" * 64},
                  "exception": None}
    recent_backup = dict(old_backup, backup_id="recent", manifest_sha256="2" * 64,
                         completed_at="2026-11-18T12:00:00+00:00")
    inventory = {"version": 1, "installation_id": str(installation), "backups": [old_backup, recent_backup]}
    raw = json.dumps(inventory).encode()
    args = dict(latest_inventory_sha256=sha256(raw).hexdigest(),
                audit_at=datetime(2026, 11, 20, 12, tzinfo=timezone.utc),
                trusted_public_key=signer.public_key(), journal_encryption_key=journal_key,
                checkpoint_verification_key=key, checkpoint_encryption_key=ciphertext,
                policy_verification_key=policy_key, policy_encryption_key=policy_cipher)
    for backup in (old_backup, recent_backup):
        with pytest.raises(RestoreBlocked):
            assess_backup_coverage(authority, store, raw, **args)
        payload = {"version": "c24-restore-v3", "organization_id": str(org), "backup_id": backup["backup_id"],
                   "sequence": 1, "complete": True, "erasures": [], "stay_purges": [], "receipt_prunes": []}
        pin = publish_checkpoint(store, installation, payload, verification_key=key, encryption_key=ciphertext)
        authority.advance_checkpoint(store, None, pin, verification_key=key, encryption_key=ciphertext)
        policy = {"version": "gdpr-policy-state-v1", "installation_id": str(installation),
                  "organization_id": str(org), "backup_id": backup["backup_id"], "sequence": 1,
                  "layout": "baseline",
                  "rows": dict.fromkeys(("gdpr_policies", "gdpr_retention_holds", "gdpr_retention_hold_events"), [])}
        policy_raw = json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()
        signature = hmac.new(policy_key, b"melpis.restore.policy-state.v1\0" + policy_raw, sha256).hexdigest()
        sealed = Fernet(policy_cipher).encrypt(json.dumps({"payload": policy, "signature": signature}).encode())
        authority.publish_policy(
            store, None, PolicyStatePin(installation, org, backup["backup_id"], 1, sha256(policy_raw).hexdigest()),
            sealed, verification_key=policy_key, encryption_key=policy_cipher)
    proof = assess_backup_coverage(authority, store, raw, **args)
    assert proof["backups_with_verified_recovery_evidence"] == 2
    assert proof["backup_objects_verified"] is False
    assert proof["checkpoint_source_commitments_verified"] is False
    assert proof["overdue_backups"] == ("old",)
    assert proof["minimum_checkpoint_entries_remaining"] == 10000
    assert proof["minimum_checkpoint_bytes_remaining"] > 0
    assert proof["deletion_authorized"] is False
    # An independently pinned inventory is still rejected if its UUIDs are
    # malformed or identify an invalid/zero tenant, rather than interpreted as
    # an empty tenant set.
    for invalid_org in ("not-a-uuid", "00000000-0000-0000-0000-000000000000", 123):
        malformed = json.dumps(dict(inventory, backups=[dict(old_backup, organizations=[invalid_org])])).encode()
        with pytest.raises(RestoreBlocked):
            assess_backup_coverage(authority, store, malformed,
                                   **dict(args, latest_inventory_sha256=sha256(malformed).hexdigest()))
    with pytest.raises(RestoreBlocked):
        assess_backup_coverage(authority, store, raw[:-1], **args)
    old_backup["exception"] = {"authority_reference": "controller:verified-retention",
                               "review_by": "2026-11-22T09:00:00+00:00"}
    reviewed = json.dumps(inventory).encode()
    reviewed_args = dict(args, latest_inventory_sha256=sha256(reviewed).hexdigest())
    assert assess_backup_coverage(authority, store, reviewed, **reviewed_args)["overdue_backups"] == ()
    later = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)
    assert assess_backup_coverage(authority, store, reviewed, **dict(reviewed_args, audit_at=later))[
        "overdue_backups"] == ("old",)
    # The authenticated checkpoint may be genuine yet incomplete for an
    # earlier backup if erasure evidence has since been signed and published.
    record = {"organization_id": str(org), "operation_id": str(uuid4()),
              "scope": "organization", "subject_id": None,
              "identifier_digest": None, "key_id": None}
    previous = authority.current()
    tip = publish_segment(store, installation, previous, [record],
                          signer=signer, encryption_key=journal_key)
    authority.advance(store, previous, tip, trusted_public_key=signer.public_key(), encryption_key=journal_key)
    with pytest.raises(RestoreBlocked, match="omits signed erasure evidence"):
        assess_backup_coverage(authority, store, reviewed, **reviewed_args)
    # Covering every backup's post-anchor entry makes the proof valid again.
    for backup in (old_backup, recent_backup):
        prior_pin = authority.checkpoint_pin(org, backup["backup_id"])
        payload = {"version": "c24-restore-v3", "organization_id": str(org), "backup_id": backup["backup_id"],
                   "sequence": 2, "complete": True, "stay_purges": [], "receipt_prunes": [],
                   "erasures": [{field: record[field] for field in ("operation_id", "scope", "subject_id")}]}
        new_pin = publish_checkpoint(store, installation, payload,
                                     verification_key=key, encryption_key=ciphertext, previous_pin=prior_pin)
        authority.advance_checkpoint(store, prior_pin, new_pin, verification_key=key, encryption_key=ciphertext)
    assert assess_backup_coverage(authority, store, reviewed, **reviewed_args)["source_chain_entries_remaining"] == 99999
    next(path for path in objects.iterdir() if path.name.startswith("policy-")).unlink()
    with pytest.raises(RestoreBlocked):
        assess_backup_coverage(authority, store, reviewed, **reviewed_args)
