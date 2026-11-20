from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.core.gdpr.independent_tip import IndependentTipStore
from src.core.gdpr.journal_publication import LocalJournalStore, publish_segment, SegmentKeyEpoch
from src.core.gdpr.restore_journal import JournalCheckpoint, RestoreJournalInvalid


def record():
    return dict(organization_id=str(uuid4()), operation_id=str(uuid4()), scope="organization",
                subject_id=None, identifier_digest=None, key_id=None)


@pytest.fixture
def journal(tmp_path):
    installation = uuid4()
    store = LocalJournalStore(tmp_path)
    authority = IndependentTipStore.initialize(tmp_path / "authority.sqlite", installation)
    signer, key = Ed25519PrivateKey.generate(), Fernet.generate_key()
    anchor = authority.current()
    tips, records = [anchor], [record() for _ in range(5)]
    for entry in records:
        tip = publish_segment(store, installation, tips[-1], [entry], signer=signer, encryption_key=key)
        authority.advance(store, tips[-1], tip, trusted_public_key=signer.public_key(), encryption_key=key)
        tips.append(tip)
    return store, authority, signer, key, tips, records


def test_pages_have_exact_boundaries_and_stale_authority_fails(journal):
    _, authority, _, _, tips, _ = journal
    assert authority.chain_page_after(tips[0], tips[-1], max_segments=2) == tuple(tips[1:3])
    assert authority.chain_page_after(tips[2], tips[-1], max_segments=2) == tuple(tips[3:5])
    assert authority.chain_page_after(tips[4], tips[-1], max_segments=2) == tuple(tips[5:])
    assert authority.chain_page_after(tips[-1], tips[-1], max_segments=2) == ()
    for anchor, expected in [(tips[0], tips[4]), (JournalCheckpoint(2, "f" * 64), tips[-1])]:
        with pytest.raises(RestoreJournalInvalid):
            authority.chain_page_after(anchor, expected, max_segments=2)


def test_restore_checks_exact_index_prefix_before_scanning_suffix(journal, monkeypatch):
    from src.core.gdpr.restore_coordinator import _verify_anchor_index

    _, authority, _, _, tips, _ = journal
    original = authority.chain_page_after
    monkeypatch.setattr(authority, "chain_page_after", lambda anchor, tip: original(anchor, tip, max_segments=1))
    _verify_anchor_index(authority, tips[3], tips[-1])
    with pytest.raises(RestoreJournalInvalid, match="independent_authority_invalid"):
        _verify_anchor_index(authority, JournalCheckpoint(tips[3].sequence, "9" * 64), tips[-1])
    with pytest.raises(RestoreJournalInvalid, match="independent_authority_conflict"):
        _verify_anchor_index(authority, tips[2], tips[4])


def test_paged_verified_scan_covers_suffix_without_partial_success(journal):
    from src.core.gdpr.journal_chain_pages import scan_verified_chain

    store, authority, signer, key, tips, records = journal
    seen = []
    result = scan_verified_chain(store, authority, tips[1], tips[-1],
        trusted_public_key=signer.public_key(), encryption_key=key,
        max_segments=2, consume=lambda page: seen.extend(page))
    assert result == {"segments": 4, "entries": 4, "traffic_authorized": False}
    assert [str(row.operation_id) for row in seen] == [row["operation_id"] for row in records[1:]]
    # A missing last artifact invalidates the whole scan, even after earlier
    # provisional pages were delivered to a quarantine-only consumer.
    path = next(p for p in store.root.glob("*.fernet") if tips[-1].sha256 in p.name)
    path.unlink()
    with pytest.raises(RestoreJournalInvalid):
        scan_verified_chain(store, authority, tips[0], tips[-1], trusted_public_key=signer.public_key(),
                            encryption_key=key, max_segments=2, consume=lambda page: None)


def test_paged_scan_key_epoch_boundary_and_concurrent_advancement(journal):
    from src.core.gdpr.journal_chain_pages import scan_verified_chain
    store, authority, signer, key, tips, _ = journal
    next_signer, next_key = Ed25519PrivateKey.generate(), Fernet.generate_key()
    tip = tips[-1]
    authority.approve_segment_epoch(tip, next_signer.public_key(), next_key)
    successor = publish_segment(store, authority.installation_id, tip, [record()],
                                signer=next_signer, encryption_key=next_key)
    authority.advance(store, tip, successor, trusted_public_key=next_signer.public_key(), encryption_key=next_key)
    epochs = (SegmentKeyEpoch(tips[0], signer.public_key(), key),
              SegmentKeyEpoch(tip, next_signer.public_key(), next_key))
    args = dict(trusted_public_key=signer.public_key(), encryption_key=key, key_epochs=epochs, max_segments=1)
    assert scan_verified_chain(store, authority, tips[0], successor, consume=lambda page: None, **args)["entries"] == 6
    advanced = False
    def concurrent(page):
        nonlocal advanced
        if not advanced:
            new = publish_segment(store, authority.installation_id, successor, [record()],
                                  signer=next_signer, encryption_key=next_key)
            authority.advance(store, successor, new, trusted_public_key=next_signer.public_key(), encryption_key=next_key)
            advanced = True
    with pytest.raises(RestoreJournalInvalid):
        scan_verified_chain(store, authority, tips[0], successor, consume=concurrent, **args)


def test_paged_scan_checks_global_duplicate_operation_even_across_pages(tmp_path):
    # This test intentionally gives the verifier a synthetic read-only index
    # containing conflicting signed segments; no real authority is changed.
    from types import SimpleNamespace
    from src.core.gdpr.journal_chain_pages import scan_verified_chain

    installation = uuid4()
    store = LocalJournalStore(tmp_path)
    signer, key = Ed25519PrivateKey.generate(), Fernet.generate_key()
    anchor = JournalCheckpoint(0, "0" * 64)
    duplicate = record()
    first = publish_segment(store, installation, anchor, [duplicate], signer=signer, encryption_key=key)
    second = publish_segment(store, installation, first, [duplicate], signer=signer, encryption_key=key)
    pages = {anchor: (first,), first: (second,), second: ()}
    reader = SimpleNamespace(installation_id=installation, current=lambda: second,
        validate_segment_schedule=lambda *args: None,
        chain_page_after=lambda cursor, expected, max_segments: pages[cursor])
    with pytest.raises(RestoreJournalInvalid, match="journal_chain_invalid"):
        scan_verified_chain(store, reader, anchor, second, trusted_public_key=signer.public_key(),
                            encryption_key=key, max_segments=1, consume=lambda page: None)
