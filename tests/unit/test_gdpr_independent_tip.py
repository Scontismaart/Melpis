from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.core.gdpr.restore_journal import JournalCheckpoint, RestoreJournalInvalid


def test_independent_tip_is_durable_cas_and_never_advances_after_failed_readback(tmp_path):
    from src.core.gdpr.journal_publication import LocalJournalStore, publish_segment
    from src.core.gdpr.independent_tip import IndependentTipStore

    root, authority_dir = tmp_path / "objects", tmp_path / "authority"
    root.mkdir(mode=0o700)
    authority_dir.mkdir(mode=0o700)
    store = LocalJournalStore(root)
    installation, signer, key = uuid4(), Ed25519PrivateKey.generate(), Fernet.generate_key()
    authority = IndependentTipStore.initialize(authority_dir / "tip.sqlite", installation)
    genesis = JournalCheckpoint(0, "0" * 64)
    assert authority.current() == genesis
    entry = {"organization_id": str(uuid4()), "operation_id": str(uuid4()),
             "scope": "organization", "subject_id": None, "identifier_digest": None, "key_id": None}
    first = publish_segment(store, installation, genesis, [entry], signer=signer, encryption_key=key)
    assert authority.current() == genesis
    args = dict(trusted_public_key=signer.public_key(), encryption_key=key)
    authority.advance(store, genesis, first, **args)
    assert IndependentTipStore(authority_dir / "tip.sqlite", installation).current() == first
    assert authority.chain_after(genesis) == (first,)
    alternative = publish_segment(store, installation, genesis,
                                  [dict(entry, operation_id=str(uuid4()))], signer=signer, encryption_key=key)
    with pytest.raises(RestoreJournalInvalid):
        authority.advance(store, genesis, alternative, **args)
    latest = publish_segment(store, installation, first,
                             [dict(entry, operation_id=str(uuid4()))], signer=signer, encryption_key=key)
    next(p for p in root.iterdir() if latest.sha256 in p.name).unlink()
    with pytest.raises(RestoreJournalInvalid):
        authority.advance(store, first, latest, **args)
    assert authority.current() == first


def test_authority_is_not_implicitly_reinitialized_if_lost(tmp_path):
    from src.core.gdpr.independent_tip import IndependentTipStore

    path = tmp_path / "tip.sqlite"
    installation = uuid4()
    with pytest.raises(RestoreJournalInvalid):
        IndependentTipStore(path, installation)
    IndependentTipStore.initialize(path, installation)
    with pytest.raises(RestoreJournalInvalid):
        IndependentTipStore.initialize(path, installation)
    with pytest.raises(RestoreJournalInvalid):
        IndependentTipStore(path, uuid4())


def test_portable_authority_requires_current_external_pin(tmp_path):
    from src.core.gdpr.independent_tip import IndependentTipStore
    from src.core.gdpr.journal_publication import LocalJournalStore, publish_segment

    authority_dir, recovery_dir, object_dir = (tmp_path / name for name in ("authority", "recovery", "objects"))
    for directory in (authority_dir, recovery_dir, object_dir):
        directory.mkdir(mode=0o700)
    installation = uuid4()
    authority = IndependentTipStore.initialize(authority_dir / "tip.sqlite", installation)
    recovery_key, journal_key, signer = Fernet.generate_key(), Fernet.generate_key(), Ed25519PrivateKey.generate()
    old_pin, old_bytes = authority.seal_for_recovery(recovery_key)
    store = LocalJournalStore(object_dir)
    entry = {"organization_id": str(uuid4()), "operation_id": str(uuid4()),
             "scope": "organization", "subject_id": None, "identifier_digest": None, "key_id": None}
    tip = publish_segment(store, installation, old_pin.tip, [entry], signer=signer, encryption_key=journal_key)
    authority.advance(store, old_pin.tip, tip, trusted_public_key=signer.public_key(), encryption_key=journal_key)
    current_pin, current_bytes = authority.seal_for_recovery(recovery_key)
    destination = recovery_dir / "recovered.sqlite"
    for invalid_cipher, invalid_key in [(old_bytes, recovery_key), (current_bytes[:-1], recovery_key),
                                        (current_bytes, Fernet.generate_key())]:
        with pytest.raises(RestoreJournalInvalid):
            IndependentTipStore.recover(destination, installation, current_pin, invalid_cipher, invalid_key)
        assert not destination.exists()
    recovered = IndependentTipStore.recover(destination, installation, current_pin, current_bytes, recovery_key)
    assert recovered.current() == tip
    assert recovered.chain_after(old_pin.tip) == (tip,)
    with pytest.raises(RestoreJournalInvalid):
        IndependentTipStore.recover(destination, installation, current_pin, current_bytes, recovery_key)


def test_authority_pins_checkpoint_and_rejects_omissions_even_if_signed(tmp_path):
    from src.core.gdpr.independent_tip import IndependentTipStore
    from src.core.gdpr.journal_publication import LocalJournalStore, publish_checkpoint
    from src.core.gdpr.restore_checkpoint import RestoreBlocked

    root, authority_dir = tmp_path / "objects", tmp_path / "authority"
    root.mkdir(mode=0o700)
    authority_dir.mkdir(mode=0o700)
    installation, org = uuid4(), uuid4()
    store = LocalJournalStore(root)
    authority = IndependentTipStore.initialize(authority_dir / "tip.sqlite", installation)
    payload = {"version": "c24-restore-v3", "organization_id": str(org),
               "backup_id": "backup", "sequence": 1, "complete": True,
               "erasures": [{"operation_id": str(uuid4()), "scope": "organization", "subject_id": None}],
               "stay_purges": [], "receipt_prunes": []}
    args = dict(verification_key=bytes(range(32)), encryption_key=Fernet.generate_key())
    pin = publish_checkpoint(store, installation, payload, **args)
    authority.advance_checkpoint(store, None, pin, **args)
    assert authority.checkpoint_pin(org, "backup") == pin
    omitted = publish_checkpoint(store, installation, dict(payload, sequence=2, erasures=[]), **args)
    with pytest.raises(RestoreBlocked):
        authority.advance_checkpoint(store, pin, omitted, **args)
    assert authority.checkpoint_pin(org, "backup") == pin
    next_pin = publish_checkpoint(store, installation, dict(payload, sequence=3), previous_pin=pin, **args)
    authority.advance_checkpoint(store, pin, next_pin, **args)
    assert authority.checkpoint_pin(org, "backup") == next_pin


def test_checkpoint_rotation_requires_authenticated_previous_generation(tmp_path):
    from src.core.gdpr.independent_tip import IndependentTipStore
    from src.core.gdpr.journal_publication import ArtifactKeyPair, LocalJournalStore, publish_checkpoint, read_checkpoint
    from src.core.gdpr.restore_checkpoint import RestoreBlocked

    data, pins = tmp_path / "data", tmp_path / "pins"
    data.mkdir(mode=0o700)
    pins.mkdir(mode=0o700)
    installation, org = uuid4(), uuid4()
    store = LocalJournalStore(data)
    authority = IndependentTipStore.initialize(pins / "tip.sqlite", installation)
    old = ArtifactKeyPair(bytes(range(32)), Fernet.generate_key())
    new = ArtifactKeyPair(bytes(range(1, 33)), Fernet.generate_key())
    payload = {"version": "c24-restore-v3", "organization_id": str(org), "backup_id": "backup",
               "sequence": 1, "complete": True, "erasures": [], "stay_purges": [], "receipt_prunes": []}
    prior = publish_checkpoint(store, installation, payload,
                               verification_key=old.verification_key, encryption_key=old.encryption_key)
    authority.advance_checkpoint(store, None, prior,
                                 verification_key=old.verification_key, encryption_key=old.encryption_key)
    rotated = publish_checkpoint(store, installation, dict(payload, sequence=2),
                                 verification_key=new.verification_key, encryption_key=new.encryption_key,
                                 previous_pin=prior, previous_keys=old)
    with pytest.raises(RestoreBlocked):
        authority.advance_checkpoint(store, prior, rotated,
                                     verification_key=new.verification_key, encryption_key=new.encryption_key)
    authority.advance_checkpoint(store, prior, rotated, verification_key=new.verification_key,
                                 encryption_key=new.encryption_key, previous_keys=old)
    result = read_checkpoint(store, installation, org, "backup", authority.checkpoint_pin(org, "backup"),
                             verification_key=new.verification_key, encryption_key=new.encryption_key)
    assert result["payload"]["sequence"] == 2


def test_tip_authority_rejects_unapproved_epoch_and_retains_approved_boundary(tmp_path):
    from src.core.gdpr.independent_tip import IndependentTipStore
    from src.core.gdpr.journal_publication import LocalJournalStore, SegmentKeyEpoch, publish_segment

    objects, pins = tmp_path / "objects", tmp_path / "pins"
    objects.mkdir(mode=0o700)
    pins.mkdir(mode=0o700)
    installation = uuid4()
    authority = IndependentTipStore.initialize(pins / "authority.sqlite", installation)
    store = LocalJournalStore(objects)
    old, new = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    old_key, new_key = Fernet.generate_key(), Fernet.generate_key()
    genesis = authority.current()
    entry = {"organization_id": str(uuid4()), "operation_id": str(uuid4()), "scope": "organization",
             "subject_id": None, "identifier_digest": None, "key_id": None}
    middle = publish_segment(store, installation, genesis, [entry], signer=old, encryption_key=old_key)
    authority.advance(store, genesis, middle, trusted_public_key=old.public_key(), encryption_key=old_key)
    second = [dict(entry, operation_id=str(uuid4()))]
    latest = publish_segment(store, installation, middle, second, signer=new, encryption_key=new_key)
    with pytest.raises(RestoreJournalInvalid):
        authority.advance(store, middle, latest, trusted_public_key=new.public_key(), encryption_key=new_key)
    authority.approve_segment_epoch(middle, new.public_key(), new_key)
    authority.advance(store, middle, latest, trusted_public_key=new.public_key(), encryption_key=new_key)
    schedule = [SegmentKeyEpoch(genesis, old.public_key(), old_key),
                SegmentKeyEpoch(middle, new.public_key(), new_key)]
    reopened = IndependentTipStore(pins / "authority.sqlite", installation)
    reopened.validate_segment_schedule(latest, old.public_key(), old_key, schedule)
    with pytest.raises(RestoreJournalInvalid):
        reopened.validate_segment_schedule(latest, new.public_key(), new_key, schedule[1:])
