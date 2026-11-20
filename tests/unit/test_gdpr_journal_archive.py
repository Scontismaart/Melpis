"""Synthetic journal prefix packing with independent backup authority."""

from datetime import datetime, timezone
from hashlib import sha256
import json
import hmac
from uuid import uuid4

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from src.core.gdpr.independent_tip import IndependentTipStore
from src.core.gdpr.journal_publication import LocalJournalStore, _name, publish_segment, read_segment
from src.core.gdpr.restore_journal import RestoreJournalInvalid


@pytest.fixture
def recovery(tmp_path):
    objects, archive = tmp_path / "objects", tmp_path / "archive"
    objects.mkdir(mode=0o700)
    archive.mkdir(mode=0o700)
    installation, org = uuid4(), uuid4()
    source, archive = LocalJournalStore(objects), LocalJournalStore(archive)
    authority = IndependentTipStore.initialize(tmp_path / "tip.sqlite", installation)
    signer, journal_key = Ed25519PrivateKey.generate(), Fernet.generate_key()
    tips = [authority.current()]
    for _ in range(4):
        record = dict(organization_id=str(org), operation_id=str(uuid4()), scope="organization",
                      subject_id=None, identifier_digest=None, key_id=None)
        tip = publish_segment(source, installation, tips[-1], [record], signer=signer, encryption_key=journal_key)
        authority.advance(source, tips[-1], tip, trusted_public_key=signer.public_key(), encryption_key=journal_key)
        tips.append(tip)
    # Inventory order and timestamp order both differ from anchor order. The
    # overdue first anchor is still a recoverable artifact and must count.
    backups = [
        dict(backup_id="later-anchor", manifest_sha256="1" * 64, completed_at="2026-10-01T01:00:00+00:00",
             anchor=dict(sequence=tips[3].sequence, sha256=tips[3].sha256),
             organizations=[str(org)], exception={"authority_reference": "test:case",
                                                  "review_by": "2026-11-25T00:00:00+00:00"}),
        dict(backup_id="earliest-anchor", manifest_sha256="2" * 64, completed_at="2026-10-02T00:00:00+00:00",
             anchor=dict(sequence=tips[2].sequence, sha256=tips[2].sha256),
             organizations=[str(org)], exception=None),
    ]
    inventory = json.dumps(dict(version=1, installation_id=str(installation), backups=backups)).encode()
    trust = dict(latest_inventory_sha256=sha256(inventory).hexdigest(),
                 audit_at=datetime(2026, 11, 20, tzinfo=timezone.utc),
                 trusted_public_key=signer.public_key(), journal_encryption_key=journal_key,
                 archive_verification_key=bytes(range(32)), archive_encryption_key=Fernet.generate_key())
    return source, archive, authority, tips, inventory, trust


def test_prefix_uses_earliest_recoverable_anchor_and_reconstructs_exact_ciphertext(recovery):
    from src.core.gdpr.journal_archive import open_archived_journal, publish_archive_prefix

    source, archive, authority, tips, inventory, trust = recovery
    installation = authority.installation_id
    pin = publish_archive_prefix(source, archive, authority, inventory, max_segments_per_pack=1, **trust)
    assert pin.floor == tips[2]
    assert authority.current() == tips[4]
    names = [_name(installation, tip) for tip in tips[1:]]
    originals = [source.read(name) for name in names]
    assert len(list(archive.root.iterdir())) == 3
    reader = open_archived_journal(source, archive, authority, inventory, pin, **trust)
    # Prove serving the archived prefix does not depend on a loose source copy.
    for name in names[:2]:
        (source.root / name).unlink()
    assert [reader.read(name) for name in names] == originals
    assert len(read_segment(reader, installation, tips[1], tips[2],
                            trusted_public_key=trust["trusted_public_key"],
                            encryption_key=trust["journal_encryption_key"])) == 1
    with pytest.raises(RestoreJournalInvalid):
        reader.put_once(names[0], originals[0])


@pytest.mark.parametrize("damage", ["missing", "swapped"])
def test_archived_prefix_refuses_missing_or_substituted_pack(recovery, damage):
    from src.core.gdpr.journal_archive import open_archived_journal, publish_archive_prefix

    source, archive, authority, tips, inventory, trust = recovery
    pin = publish_archive_prefix(source, archive, authority, inventory, max_segments_per_pack=1, **trust)
    packs = [path for path in archive.root.iterdir() if pin.sha256 not in path.name]
    if damage == "missing":
        packs[0].unlink()
    else:
        packs[0].write_bytes(packs[1].read_bytes())
    with pytest.raises(RestoreJournalInvalid):
        open_archived_journal(source, archive, authority, inventory, pin, **trust)
    assert authority.current() == tips[-1]
    assert source.read(_name(authority.installation_id, tips[1]))


def test_crash_before_manifest_keeps_loose_segments_and_authority(recovery):
    from src.core.gdpr.journal_archive import open_archived_journal, publish_archive_prefix

    source, archive, authority, tips, inventory, trust = recovery
    installation = authority.installation_id

    class InterruptedStore:
        def put_once(self, name, ciphertext):
            if len(list(archive.root.iterdir())) == 2:
                raise OSError("synthetic interruption before manifest")
            archive.put_once(name, ciphertext)

        def read(self, name):
            return archive.read(name)

    with pytest.raises(RestoreJournalInvalid):
        publish_archive_prefix(source, InterruptedStore(), authority, inventory, max_segments_per_pack=1, **trust)
    assert len(list(archive.root.iterdir())) == 2
    assert authority.current() == tips[-1]
    assert len(read_segment(source, installation, tips[0], tips[1],
                            trusted_public_key=trust["trusted_public_key"],
                            encryption_key=trust["journal_encryption_key"])) == 1
    with pytest.raises(RestoreJournalInvalid):
        open_archived_journal(source, archive, authority, inventory, None, **trust)
    pin = publish_archive_prefix(source, archive, authority, inventory, max_segments_per_pack=1, **trust)
    assert len(list(archive.root.iterdir())) == 3
    assert open_archived_journal(source, archive, authority, inventory, pin, **trust)


@pytest.mark.parametrize("lost_key", ["archive_encryption_key", "archive_verification_key", "journal_encryption_key"])
def test_archive_does_not_replace_historical_key_custody(recovery, lost_key):
    from src.core.gdpr.journal_archive import open_archived_journal, publish_archive_prefix

    source, archive, authority, _, inventory, trust = recovery
    pin = publish_archive_prefix(source, archive, authority, inventory, **trust)
    replacement = bytes(32) if lost_key == "archive_verification_key" else Fernet.generate_key()
    with pytest.raises(RestoreJournalInvalid):
        open_archived_journal(source, archive, authority, inventory, pin, **dict(trust, **{lost_key: replacement}))


def test_archive_rejects_unattested_inventory_and_genesis_floor(recovery):
    from src.core.gdpr.journal_archive import open_archived_journal, publish_archive_prefix

    source, archive, authority, tips, inventory, trust = recovery
    pin = publish_archive_prefix(source, archive, authority, inventory, **trust)
    changed = json.loads(inventory)
    changed["backups"][0]["anchor"] = dict(sequence=tips[0].sequence, sha256=tips[0].sha256)
    older_inventory = json.dumps(changed).encode()
    with pytest.raises(RestoreJournalInvalid):
        open_archived_journal(source, archive, authority, older_inventory, pin, **trust)
    new_trust = dict(trust, latest_inventory_sha256=sha256(older_inventory).hexdigest())
    with pytest.raises(RestoreJournalInvalid):
        open_archived_journal(source, archive, authority, older_inventory, pin, **new_trust)
    with pytest.raises(RestoreJournalInvalid):
        publish_archive_prefix(source, archive, authority, older_inventory, **new_trust)


def test_disappearing_archived_pack_does_not_fall_back_to_loose_prefix(recovery):
    from src.core.gdpr.journal_archive import open_archived_journal, publish_archive_prefix

    source, archive, authority, tips, inventory, trust = recovery
    pin = publish_archive_prefix(source, archive, authority, inventory, **trust)
    reader = open_archived_journal(source, archive, authority, inventory, pin, **trust)
    pack = next(path for path in archive.root.iterdir() if pin.sha256 not in path.name)
    pack.unlink()
    name = _name(authority.installation_id, tips[1])
    assert source.read(name)
    with pytest.raises(RestoreJournalInvalid):
        reader.read(name)


def test_reusable_archive_after_new_tip_and_key_rotation(recovery):
    from src.core.gdpr.journal_archive import open_archived_journal, publish_archive_prefix
    from src.core.gdpr.journal_publication import SegmentKeyEpoch

    source, archive, authority, tips, inventory, trust = recovery
    pin = publish_archive_prefix(source, archive, authority, inventory, **trust)
    signer, key = Ed25519PrivateKey.generate(), Fernet.generate_key()
    authority.approve_segment_epoch(tips[-1], signer.public_key(), key)
    record = dict(organization_id=str(uuid4()), operation_id=str(uuid4()), scope="organization",
                  subject_id=None, identifier_digest=None, key_id=None)
    latest = publish_segment(source, authority.installation_id, tips[-1], [record], signer=signer, encryption_key=key)
    authority.advance(source, tips[-1], latest, trusted_public_key=signer.public_key(), encryption_key=key)
    epochs = [SegmentKeyEpoch(tips[0], trust["trusted_public_key"], trust["journal_encryption_key"]),
              SegmentKeyEpoch(tips[-1], signer.public_key(), key)]
    reader = open_archived_journal(source, archive, authority, inventory, pin, key_epochs=epochs, **trust)
    assert reader.read(_name(authority.installation_id, latest)) == source.read(_name(authority.installation_id, latest))
    with pytest.raises(RestoreJournalInvalid):
        open_archived_journal(source, archive, authority, inventory, pin, key_epochs=epochs[1:], **trust)


def _promote_inventory(recovery, inventory, previous_digest=None):
    from src.core.gdpr.checkpoint_pages import publish_checkpoint_pages
    from src.core.gdpr.journal_chain_pages import scan_verified_chain
    from src.core.gdpr.journal_publication import _canonical, publish_checkpoint
    from src.core.gdpr.restore_policy_state import PolicyStatePin
    from src.core.gdpr.restore_checkpoint import RestoreBlocked

    source, _, authority, tips, _, trust = recovery
    keys = dict(verification_key=bytes(range(1, 33)), encryption_key=Fernet.generate_key())
    policy_key, policy_cipher = bytes(range(2, 34)), Fernet.generate_key()
    signed = []
    scan_verified_chain(source, authority, tips[0], authority.current(),
                        trusted_public_key=trust["trusted_public_key"], encryption_key=trust["journal_encryption_key"],
                        consume=signed.extend)
    candidates = {}
    for row in json.loads(inventory)["backups"]:
        from uuid import UUID
        org = UUID(row["organizations"][0])
        backup = row["backup_id"]
        installation = authority.installation_id
        payload = dict(version="c24-restore-v4", organization_id=str(org), backup_id=backup, sequence=1,
                       complete=True, stay_purges=[], receipt_prunes=[], ordinary_booking_purges=[],
                       erasures=[dict(operation_id=str(entry.operation_id), scope=entry.scope, subject_id=None)
                                 for entry in signed if entry.organization_id == org])
        try:
            prior = authority.checkpoint_pin(org, backup)
        except RestoreBlocked:
            prior = publish_checkpoint(source, installation, payload, **keys)
            authority.advance_checkpoint(source, None, prior, **keys)
            policy = dict(version="gdpr-policy-state-v1", installation_id=str(installation),
                          organization_id=str(org), backup_id=backup, sequence=1, layout="baseline",
                          rows=dict(gdpr_policies=[], gdpr_retention_holds=[], gdpr_retention_hold_events=[]))
            raw = _canonical(policy)
            signature = hmac.new(policy_key, b"melpis.restore.policy-state.v1\0" + raw, sha256).hexdigest()
            authority.publish_policy(
                source, None, PolicyStatePin(installation, org, backup, 1, sha256(raw).hexdigest()),
                Fernet(policy_cipher).encrypt(_canonical(dict(payload=policy, signature=signature))),
                verification_key=policy_key, encryption_key=policy_cipher,
            )
        candidates[org, backup] = publish_checkpoint_pages(
            source, installation, dict(payload, version="c24-restore-v5", sequence=prior.sequence + 1),
            previous_pin=prior, **keys)
    digest = sha256(inventory).hexdigest()
    authority.promote_paged_checkpoints(
        source, inventory, latest_inventory_sha256=digest, candidates=candidates,
        previous_inventory_sha256=previous_digest, audit_at=trust["audit_at"],
        trusted_public_key=trust["trusted_public_key"], journal_encryption_key=trust["journal_encryption_key"],
        checkpoint_verification_key=keys["verification_key"], checkpoint_encryption_key=keys["encryption_key"],
        policy_verification_key=policy_key, policy_encryption_key=policy_cipher)
    return digest


def test_archive_activation_requires_protected_inventory_and_selects_current_pin(recovery):
    from src.core.gdpr.journal_archive import open_active_archived_journal, publish_archive_prefix
    from src.core.gdpr.journal_chain_pages import scan_verified_chain

    source, archive, authority, tips, inventory, trust = recovery
    pin = publish_archive_prefix(source, archive, authority, inventory, **trust)
    with pytest.raises(RestoreJournalInvalid):
        authority.activate_archive(source, archive, inventory, pin, expected_tip=tips[-1], previous_pin=None, **trust)
    _promote_inventory(recovery, inventory)
    with pytest.raises(RestoreJournalInvalid):
        open_active_archived_journal(source, archive, authority, inventory, **trust)
    assert authority.activate_archive(
        source, archive, inventory, pin, expected_tip=tips[-1], previous_pin=None, **trust) == pin
    assert authority.archive_pin() == pin
    assert authority.activate_archive(
        source, archive, inventory, pin, expected_tip=tips[-1], previous_pin=pin, **trust) == pin
    reader = open_active_archived_journal(source, archive, authority, inventory, **trust)
    name = _name(authority.installation_id, tips[1])
    assert reader.read(name) == source.read(name)
    journal_keys = dict(trusted_public_key=trust["trusted_public_key"], encryption_key=trust["journal_encryption_key"])
    with pytest.raises(RestoreJournalInvalid, match="active_archive_reader_required"):
        scan_verified_chain(source, authority, tips[0], tips[-1], consume=lambda entries: None, **journal_keys)
    (source.root / name).unlink()
    assert scan_verified_chain(reader, authority, tips[0], tips[-1], consume=lambda entries: None, **journal_keys)["entries"] == 4
    next(path for path in archive.root.iterdir() if pin.sha256 not in path.name).unlink()
    with pytest.raises(RestoreJournalInvalid):
        reader.read(name)
    with pytest.raises(RestoreJournalInvalid):
        open_active_archived_journal(source, archive, authority, inventory, **trust)


def test_archive_activation_rejects_repacking_same_inventory_and_stale_tip(recovery):
    from src.core.gdpr.journal_archive import publish_archive_prefix

    source, archive, authority, tips, inventory, trust = recovery
    _promote_inventory(recovery, inventory)
    pin = publish_archive_prefix(source, archive, authority, inventory, **trust)
    alternate = publish_archive_prefix(source, archive, authority, inventory, max_segments_per_pack=1, **trust)
    assert alternate != pin
    with pytest.raises(RestoreJournalInvalid):
        authority.activate_archive(
            source, archive, inventory, pin, expected_tip=tips[-2], previous_pin=None, **trust)
    authority.activate_archive(source, archive, inventory, pin, expected_tip=tips[-1], previous_pin=None, **trust)
    with pytest.raises(RestoreJournalInvalid):
        authority.activate_archive(
            source, archive, inventory, alternate, expected_tip=tips[-1], previous_pin=pin, **trust)
    assert authority.archive_pin() == pin


def test_archive_activation_rechecks_same_tip_key_epoch_after_readback(recovery, monkeypatch):
    from src.core.gdpr import journal_archive

    source, archive, authority, tips, inventory, trust = recovery
    _promote_inventory(recovery, inventory)
    pin = journal_archive.publish_archive_prefix(source, archive, authority, inventory, **trust)
    real_open = journal_archive.open_archived_journal

    def rotate_after_readback(*args, **kwargs):
        reader = real_open(*args, **kwargs)
        signer = Ed25519PrivateKey.generate()
        authority.approve_segment_epoch(tips[-1], signer.public_key(), Fernet.generate_key())
        return reader

    monkeypatch.setattr(journal_archive, "open_archived_journal", rotate_after_readback)
    with pytest.raises(RestoreJournalInvalid):
        authority.activate_archive(
            source, archive, inventory, pin, expected_tip=tips[-1], previous_pin=None, **trust)
    with pytest.raises(RestoreJournalInvalid):
        authority.archive_pin()
