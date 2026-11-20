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
