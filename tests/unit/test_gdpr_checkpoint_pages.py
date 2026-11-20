"""Authenticated, bounded v5 checkpoints preserve complete cumulative evidence."""
from uuid import UUID, uuid4
from hashlib import sha256
import hmac
import json

import pytest
from cryptography.fernet import Fernet

from src.core.gdpr.journal_publication import CheckpointPin, LocalJournalStore, _canonical, _checkpoint_name, publish_checkpoint
from src.core.gdpr.restore_checkpoint import RestoreBlocked


@pytest.fixture
def recovery(tmp_path):
    installation, org = uuid4(), uuid4()
    keys = dict(verification_key=bytes(range(32)), encryption_key=Fernet.generate_key())
    return LocalJournalStore(tmp_path), installation, org, keys


def _payload(org, *, sequence=1):
    return dict(
        version="c24-restore-v5", organization_id=str(org), backup_id="synthetic",
        sequence=sequence, complete=True, erasures=[], stay_purges=[], receipt_prunes=[],
        ordinary_booking_purges=[],
    )


def _with_records(org, category, records, *, sequence=1):
    return dict(_payload(org, sequence=sequence), **{category: records})


def _erasure(index):
    return dict(operation_id=str(UUID(int=index)), scope="contact", subject_id=str(UUID(int=index + 300_000)))


def test_checkpoint_pages_roundtrip_exceeds_legacy_aggregate_bounds(recovery):
    from src.core.gdpr.checkpoint_pages import publish_checkpoint_pages, read_checkpoint_pages

    store, installation, org, keys = recovery
    records = [_erasure(index) for index in range(1, 18_501)]
    payload = _with_records(org, "erasures", records)
    assert len(records) > 10_000 and len(_canonical(payload)) > 2 * 1024 * 1024

    pin = publish_checkpoint_pages(store, installation, payload, **keys)
    assert pin.sequence == 1
    assert read_checkpoint_pages(store, installation, org, "synthetic", pin, **keys) == payload
    assert publish_checkpoint_pages(store, installation, payload, **keys) == pin
    assert len(list(store.root.iterdir())) > 2
    assert all(path.stat().st_size <= 3 * 1024 * 1024 for path in store.root.iterdir())
    assert all(records[0]["subject_id"].encode() not in path.read_bytes() for path in store.root.iterdir())


@pytest.mark.parametrize("damage", ["missing", "altered"])
def test_checkpoint_requires_every_pinned_page(recovery, damage):
    from src.core.gdpr.checkpoint_pages import publish_checkpoint_pages, read_checkpoint_pages

    store, installation, org, keys = recovery
    payload = _with_records(org, "erasures", [_erasure(index) for index in range(1, 1_501)])
    pin = publish_checkpoint_pages(store, installation, payload, **keys)
    page = next(path for path in store.root.iterdir() if pin.sha256 not in path.name)
    if damage == "missing":
        page.unlink()
    else:
        page.write_bytes(b"synthetic malformed encryption")
    with pytest.raises(RestoreBlocked):
        read_checkpoint_pages(store, installation, org, "synthetic", pin, **keys)


@pytest.mark.parametrize("inconsistent", ["duplicate", "order", "count", "size"])
def test_authenticated_manifest_still_requires_exact_descriptor_structure(recovery, inconsistent):
    from src.core.gdpr.checkpoint_pages import publish_checkpoint_pages, read_checkpoint_pages

    store, installation, org, keys = recovery
    payload = _with_records(org, "erasures", [_erasure(index) for index in range(1, 2_001)])
    pin = publish_checkpoint_pages(store, installation, payload, **keys)
    cipher = Fernet(keys["encryption_key"])
    original = store.read(_checkpoint_name(installation, pin))
    envelope = json.loads(cipher.decrypt(original))
    manifest = envelope["payload"]
    if inconsistent == "duplicate":
        manifest["pages"][1] = dict(manifest["pages"][0])
    elif inconsistent == "order":
        manifest["pages"].reverse()
    elif inconsistent == "count":
        manifest["counts"]["erasures"] += 1
    else:
        manifest["pages"][0]["bytes"] += 1
    raw = _canonical(manifest)
    invalid = CheckpointPin(org, "synthetic", pin.sequence, sha256(raw).hexdigest())
    test_signature = hmac.new(keys["verification_key"], b"melpis.restore.checkpoint-v5.manifest\0" + raw,
                              sha256).hexdigest()
    store.put_once(_checkpoint_name(installation, invalid),
                   cipher.encrypt(_canonical({"payload": manifest, "signature": test_signature})))
    with pytest.raises(RestoreBlocked):
        read_checkpoint_pages(store, installation, org, "synthetic", invalid, **keys)


def test_checkpoint_rejects_record_identity_repeated_between_pages(recovery):
    from src.core.gdpr.checkpoint_pages import publish_checkpoint_pages

    store, installation, org, keys = recovery
    records = [_erasure(index) for index in range(1, 2_001)]
    records.append(dict(records[999]))
    with pytest.raises(RestoreBlocked):
        publish_checkpoint_pages(store, installation, _with_records(org, "erasures", records), **keys)
    assert not list(store.root.iterdir())


def test_checkpoint_rejects_same_receipt_source_in_distinct_pages(recovery):
    from src.core.gdpr.checkpoint_pages import publish_checkpoint_pages

    store, installation, org, keys = recovery
    records = [dict(request_id=str(UUID(int=index)), source_message_id=str(UUID(int=index + 5000)),
                    conversation_id=str(uuid4()), subject_id=str(uuid4()))
               for index in range(1, 2002)]
    records[-1]["source_message_id"] = records[0]["source_message_id"]
    with pytest.raises(RestoreBlocked):
        publish_checkpoint_pages(store, installation, _with_records(org, "receipt_prunes", records), **keys)
    assert not list(store.root.iterdir())


def test_checkpoint_rejects_cross_category_booking_and_request_conflicts(recovery):
    from src.core.gdpr.checkpoint_pages import publish_checkpoint_pages

    store, installation, org, keys = recovery
    booking, subject, request = str(uuid4()), str(uuid4()), str(uuid4())
    stay = dict(booking_id=booking, subject_id=subject, purpose="retention", operation_id=None,
                policy_version="synthetic", request_ids=[request])
    ordinary = dict(kind="ordinary_managed_booking_candidate_v1", organization_id=str(org),
                    booking_id=booking, subject_id=subject, policy_version="synthetic",
                    completed_at="2024-01-01T12:00:00.000000Z", timezone="Europe/Rome",
                    allocation_revision=2, request_ids=[str(uuid4())], deletion_authorized=False)
    for plan in (ordinary, dict(ordinary, booking_id=str(uuid4()), request_ids=[request])):
        payload = dict(_payload(org), stay_purges=[stay], ordinary_booking_purges=[plan])
        with pytest.raises(RestoreBlocked):
            publish_checkpoint_pages(store, installation, payload, **keys)
    assert not list(store.root.iterdir())


def test_checkpoint_scope_and_keys_are_exact(recovery):
    from src.core.gdpr.checkpoint_pages import publish_checkpoint_pages, read_checkpoint_pages

    store, installation, org, keys = recovery
    pin = publish_checkpoint_pages(store, installation, _payload(org), **keys)
    for args in (
        (uuid4(), org, "synthetic", keys), (installation, uuid4(), "synthetic", keys),
        (installation, org, "different-backup", keys),
        (installation, org, "synthetic", dict(keys, encryption_key=Fernet.generate_key())),
        (installation, org, "synthetic", dict(keys, verification_key=bytes(32))),
    ):
        with pytest.raises(RestoreBlocked):
            read_checkpoint_pages(store, args[0], args[1], args[2], pin, **args[3])


def test_v5_successor_preserves_legacy_and_paged_commitments(recovery):
    from src.core.gdpr.checkpoint_pages import publish_checkpoint_pages, read_checkpoint_pages

    store, installation, org, keys = recovery
    old_payload = dict(_with_records(org, "erasures", [_erasure(1)]), version="c24-restore-v4")
    legacy = publish_checkpoint(store, installation, old_payload, **keys)
    with pytest.raises(RestoreBlocked):
        publish_checkpoint_pages(store, installation, _payload(org, sequence=2), previous_pin=legacy, **keys)
    next_payload = _with_records(org, "erasures", [_erasure(1), _erasure(2)], sequence=2)
    next_pin = publish_checkpoint_pages(store, installation, next_payload, previous_pin=legacy, **keys)
    assert read_checkpoint_pages(store, installation, org, "synthetic", next_pin, **keys) == next_payload
    with pytest.raises(RestoreBlocked):
        publish_checkpoint_pages(store, installation, _with_records(org, "erasures", [_erasure(2)], sequence=3),
                                 previous_pin=next_pin, **keys)


def test_any_reader_dispatches_only_the_exact_authenticated_pin(recovery):
    from src.core.gdpr.checkpoint_pages import publish_checkpoint_pages, read_any_checkpoint

    store, installation, org, keys = recovery
    legacy_payload = dict(_payload(org), version="c24-restore-v4")
    legacy = publish_checkpoint(store, installation, legacy_payload, **keys)
    paged_payload = _with_records(org, "erasures", [_erasure(1)], sequence=2)
    paged = publish_checkpoint_pages(store, installation, paged_payload, previous_pin=legacy, **keys)
    assert read_any_checkpoint(store, installation, org, "synthetic", legacy, **keys) == legacy_payload
    assert read_any_checkpoint(store, installation, org, "synthetic", paged, **keys) == paged_payload
    page = next(path for path in store.root.iterdir()
                if legacy.sha256 not in path.name and paged.sha256 not in path.name)
    page.unlink()
    with pytest.raises(RestoreBlocked):
        read_any_checkpoint(store, installation, org, "synthetic", paged, **keys)
    assert read_any_checkpoint(store, installation, org, "synthetic", legacy, **keys) == legacy_payload


def test_candidate_verification_rejects_v5_to_legacy_downgrade(recovery):
    from src.core.gdpr.checkpoint_pages import publish_checkpoint_pages, verify_checkpoint_candidate

    store, installation, org, keys = recovery
    paged_payload = _with_records(org, "erasures", [_erasure(1)])
    paged = publish_checkpoint_pages(store, installation, paged_payload, **keys)
    downgraded_payload = dict(paged_payload, version="c24-restore-v4", sequence=2)
    downgraded = publish_checkpoint(store, installation, downgraded_payload, **keys)
    with pytest.raises(RestoreBlocked):
        verify_checkpoint_candidate(store, installation, paged, downgraded, **keys)


def test_authority_prepares_paged_candidate_without_committing_pin(recovery, tmp_path):
    from src.core.gdpr.checkpoint_pages import publish_checkpoint_pages
    from src.core.gdpr.independent_tip import IndependentTipStore

    store, installation, org, keys = recovery
    authority_dir = tmp_path / "authority"
    authority_dir.mkdir(mode=0o700)
    authority = IndependentTipStore.initialize(authority_dir / "tip.sqlite", installation)
    prior_payload = dict(_with_records(org, "erasures", [_erasure(1)]), version="c24-restore-v4")
    prior = publish_checkpoint(store, installation, prior_payload, **keys)
    authority.advance_checkpoint(store, None, prior, **keys)
    candidate_payload = _with_records(org, "erasures", [_erasure(1), _erasure(2)], sequence=2)
    candidate = publish_checkpoint_pages(store, installation, candidate_payload, previous_pin=prior, **keys)
    prepared = authority.prepare_paged_checkpoint(store, prior, candidate, **keys)
    assert prepared.candidate == candidate
    assert prepared.expected_pin == prior
    assert prepared.expected_tip == authority.current()
    assert authority.checkpoint_pin(org, "synthetic") == prior
    with pytest.raises(RestoreBlocked):
        authority.advance_checkpoint(store, prior, candidate, **keys)
    assert authority.checkpoint_pin(org, "synthetic") == prior


def test_authority_requires_current_prior_and_checks_every_candidate_page(recovery, tmp_path):
    from src.core.gdpr.checkpoint_pages import publish_checkpoint_pages
    from src.core.gdpr.independent_tip import IndependentTipStore

    store, installation, org, keys = recovery
    authority_dir = tmp_path / "authority"
    authority_dir.mkdir(mode=0o700)
    authority = IndependentTipStore.initialize(authority_dir / "tip.sqlite", installation)
    prior_payload = dict(_with_records(org, "erasures", [_erasure(1)]), version="c24-restore-v4")
    prior = publish_checkpoint(store, installation, prior_payload, **keys)
    authority.advance_checkpoint(store, None, prior, **keys)
    omission = publish_checkpoint_pages(store, installation, _payload(org, sequence=2), **keys)
    with pytest.raises(RestoreBlocked):
        authority.prepare_paged_checkpoint(store, prior, omission, **keys)
    candidate = publish_checkpoint_pages(store, installation,
                                         _with_records(org, "erasures", [_erasure(1)], sequence=3), **keys)
    successor = publish_checkpoint(store, installation,
                                   dict(prior_payload, sequence=2), previous_pin=prior, **keys)
    authority.advance_checkpoint(store, prior, successor, **keys)
    with pytest.raises(RestoreBlocked):
        authority.prepare_paged_checkpoint(store, prior, candidate, **keys)
    prepared = authority.prepare_paged_checkpoint(store, successor, candidate, **keys)
    assert prepared.expected_pin == successor
    page = next(path for path in store.root.iterdir()
                if path.is_file() and all(pin.sha256 not in path.name for pin in
                                           (prior, omission, candidate, successor)))
    page.unlink()
    with pytest.raises(RestoreBlocked):
        authority.prepare_paged_checkpoint(store, successor, candidate, **keys)
    assert authority.checkpoint_pin(org, "synthetic") == successor


def test_authority_detects_pin_change_during_candidate_readback(recovery, tmp_path):
    from src.core.gdpr.checkpoint_pages import publish_checkpoint_pages
    from src.core.gdpr.independent_tip import IndependentTipStore

    store, installation, org, keys = recovery
    authority_dir = tmp_path / "authority"
    authority_dir.mkdir(mode=0o700)
    authority = IndependentTipStore.initialize(authority_dir / "tip.sqlite", installation)
    previous_payload = dict(_payload(org), version="c24-restore-v4")
    previous = publish_checkpoint(store, installation, previous_payload, **keys)
    authority.advance_checkpoint(store, None, previous, **keys)
    successor = publish_checkpoint(store, installation, dict(previous_payload, sequence=2), **keys)
    candidate = publish_checkpoint_pages(store, installation, _payload(org, sequence=3), **keys)

    class AdvancingStore:
        def read(self, name):
            if name == _checkpoint_name(installation, candidate) and authority.checkpoint_pin(org, "synthetic") == previous:
                authority.advance_checkpoint(store, previous, successor, **keys)
            return store.read(name)

        def put_once(self, name, ciphertext):
            raise AssertionError("Candidate verification must not publish objects")

    with pytest.raises(RestoreBlocked):
        authority.prepare_paged_checkpoint(AdvancingStore(), previous, candidate, **keys)
    assert authority.checkpoint_pin(org, "synthetic") == successor
