import base64
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from src.core.gdpr.restore_journal import RestoreJournalInvalid


def test_portable_bundle_recovers_separate_keys_without_original_host(tmp_path):
    from src.core.gdpr.journal_custody import JournalKeys, seal_keys, recover_keys
    from src.core.gdpr.independent_tip import IndependentTipStore
    from src.core.gdpr.journal_publication import LocalJournalStore, publish_segment, read_chain

    installation = uuid4()
    original = JournalKeys.generate(installation)
    distinct = {original.ed25519_seed, original.checkpoint_hmac,
                base64.urlsafe_b64decode(original.journal_fernet),
                base64.urlsafe_b64decode(original.checkpoint_fernet)}
    assert len(distinct) == 4
    wrapping_key = Fernet.generate_key()  # independent from application/backup keys
    sealed = seal_keys(original, wrapping_key)
    assert original.ed25519_seed not in sealed and original.checkpoint_hmac not in sealed
    obj_dir, authority_dir = tmp_path / "objects", tmp_path / "authority"
    obj_dir.mkdir(mode=0o700)
    authority_dir.mkdir(mode=0o700)
    store = LocalJournalStore(obj_dir)
    authority = IndependentTipStore.initialize(authority_dir / "tip.sqlite", installation)
    anchor = authority.current()
    entry = {"organization_id": str(uuid4()), "operation_id": str(uuid4()),
             "scope": "organization", "subject_id": None, "identifier_digest": None, "key_id": None}
    tip = publish_segment(store, installation, anchor, [entry], signer=original.signer(),
                          encryption_key=original.journal_fernet)
    authority.advance(store, anchor, tip, trusted_public_key=original.signer().public_key(),
                      encryption_key=original.journal_fernet)
    del original
    recovered = recover_keys(sealed, wrapping_key, installation)
    journal = read_chain(store, installation, anchor, authority.chain_after(anchor), authority.current(),
                         trusted_public_key=recovered.signer().public_key(),
                         encryption_key=recovered.journal_fernet)
    assert str(journal[0].operation_id) == entry["operation_id"]
    for wrong_key, wrong_installation in [(Fernet.generate_key(), installation), (wrapping_key, uuid4())]:
        with pytest.raises(RestoreJournalInvalid):
            recover_keys(sealed, wrong_key, wrong_installation)


def test_bundle_rejects_reuse_and_bad_ciphertext():
    from src.core.gdpr.journal_custody import JournalKeys, seal_keys, recover_keys

    installation = uuid4()
    keys = JournalKeys.generate(installation)
    with pytest.raises(RestoreJournalInvalid):
        seal_keys(keys, keys.checkpoint_fernet)
    with pytest.raises(RestoreJournalInvalid):
        recover_keys(b"unverified", Fernet.generate_key(), installation)
