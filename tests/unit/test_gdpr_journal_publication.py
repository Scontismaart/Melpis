from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.core.gdpr.restore_journal import JournalCheckpoint, RestoreJournalInvalid


@pytest.fixture
def publication(tmp_path):
    from src.core.gdpr.journal_publication import LocalJournalStore

    return (LocalJournalStore(tmp_path), uuid4(), Ed25519PrivateKey.generate(),
            Fernet.generate_key(), JournalCheckpoint(0, "0" * 64))


def _entry():
    return {"organization_id": str(uuid4()), "operation_id": str(uuid4()),
            "scope": "organization", "subject_id": None,
            "identifier_digest": None, "key_id": None}


def test_signed_encrypted_publication_and_idempotent_readback(publication):
    from src.core.gdpr.journal_publication import publish_segment, read_segment

    store, installation, signer, key, anchor = publication
    entries = [_entry(), _entry()]
    tip = publish_segment(store, installation, anchor, entries,
                          signer=signer, encryption_key=key)
    assert tip.sequence == 2
    assert publish_segment(store, installation, anchor, entries,
                           signer=signer, encryption_key=key) == tip
    restored = read_segment(store, installation, anchor, tip,
                            trusted_public_key=signer.public_key(), encryption_key=key)
    assert [str(entry.operation_id) for entry in restored] == [
        entry["operation_id"] for entry in entries]
    assert entries[0]["operation_id"].encode() not in next(store.root.iterdir()).read_bytes()


def test_publication_rejects_missing_or_corrupted_object(publication):
    from src.core.gdpr.journal_publication import publish_segment, read_segment

    store, installation, signer, key, anchor = publication
    tip = publish_segment(store, installation, anchor, [_entry()],
                          signer=signer, encryption_key=key)
    path = next(store.root.iterdir())
    path.write_bytes(b"not an authenticated object")
    with pytest.raises(RestoreJournalInvalid):
        read_segment(store, installation, anchor, tip,
                     trusted_public_key=signer.public_key(), encryption_key=key)
    path.unlink()
    with pytest.raises(RestoreJournalInvalid):
        read_segment(store, installation, anchor, tip,
                     trusted_public_key=signer.public_key(), encryption_key=key)


def test_signer_rejects_non_minimal_evidence_without_publishing(publication):
    from src.core.gdpr.journal_publication import publish_segment

    store, installation, signer, key, anchor = publication
    with pytest.raises(RestoreJournalInvalid):
        publish_segment(store, installation, anchor, [dict(_entry(), body="not permitted")],
                        signer=signer, encryption_key=key)
    assert not list(store.root.iterdir())


def test_independent_tip_not_derived_from_object_listing(publication):
    from src.core.gdpr.journal_publication import publish_segment, read_segment

    store, installation, signer, key, anchor = publication
    first = _entry()
    old = publish_segment(store, installation, anchor, [first],
                          signer=signer, encryption_key=key)
    latest = publish_segment(store, installation, anchor, [first, _entry()],
                             signer=signer, encryption_key=key)
    assert old != latest
    # Even a valid older artifact cannot replace the externally pinned tip.
    latest_path = next(p for p in store.root.iterdir() if latest.sha256 in p.name)
    latest_path.unlink()
    with pytest.raises(RestoreJournalInvalid):
        read_segment(store, installation, anchor, latest,
                     trusted_public_key=signer.public_key(), encryption_key=key)


def test_complete_chain_requires_all_segments_and_unique_operations(publication):
    from src.core.gdpr.journal_publication import publish_segment, read_chain

    store, installation, signer, key, anchor = publication
    first, second = _entry(), _entry()
    middle = publish_segment(store, installation, anchor, [first],
                             signer=signer, encryption_key=key)
    latest = publish_segment(store, installation, middle, [second],
                             signer=signer, encryption_key=key)
    args = dict(trusted_public_key=signer.public_key(), encryption_key=key)
    entries = read_chain(store, installation, anchor, [middle, latest], latest, **args)
    assert [str(entry.operation_id) for entry in entries] == [
        first["operation_id"], second["operation_id"]]
    with pytest.raises(RestoreJournalInvalid):
        read_chain(store, installation, anchor, [middle], latest, **args)
    with pytest.raises(RestoreJournalInvalid):
        read_chain(store, installation, anchor, [latest], latest, **args)
    duplicate = publish_segment(store, installation, middle, [first],
                                signer=signer, encryption_key=key)
    with pytest.raises(RestoreJournalInvalid):
        read_chain(store, installation, anchor, [middle, duplicate], duplicate, **args)


def test_checkpoint_exact_pin_and_separate_key_readback(publication):
    from src.core.gdpr.journal_publication import publish_checkpoint, read_checkpoint
    from src.core.gdpr.restore_checkpoint import RestoreBlocked

    store, installation, _, encryption_key, _ = publication
    org, op = uuid4(), uuid4()
    hmac_key = bytes(range(32))
    payload = {"version": "c24-restore-v3", "organization_id": str(org),
               "backup_id": "synthetic-backup", "sequence": 2, "complete": True,
               "erasures": [{"operation_id": str(op), "scope": "organization", "subject_id": None}],
               "stay_purges": [], "receipt_prunes": []}
    tip = publish_checkpoint(store, installation, payload, verification_key=hmac_key,
                             encryption_key=encryption_key)
    result = read_checkpoint(store, installation, org, "synthetic-backup", tip,
                             verification_key=hmac_key, encryption_key=encryption_key)
    assert result["payload"] == payload
    for invalid in (dict(verification_key=bytes(32), encryption_key=encryption_key),
                    dict(verification_key=hmac_key, encryption_key=Fernet.generate_key())):
        with pytest.raises(RestoreBlocked):
            read_checkpoint(store, installation, org, "synthetic-backup", tip, **invalid)
    with pytest.raises(RestoreBlocked):
        read_checkpoint(store, installation, org, "other-backup", tip,
                        verification_key=hmac_key, encryption_key=encryption_key)


def test_checkpoint_cannot_omit_previously_pinned_commitments(publication):
    from src.core.gdpr.journal_publication import publish_checkpoint
    from src.core.gdpr.restore_checkpoint import RestoreBlocked

    store, installation, _, encryption_key, _ = publication
    payload = {"version": "c24-restore-v3", "organization_id": str(uuid4()),
               "backup_id": "synthetic-backup", "sequence": 1, "complete": True,
               "erasures": [{"operation_id": str(uuid4()), "scope": "organization", "subject_id": None}],
               "stay_purges": [], "receipt_prunes": []}
    args = dict(verification_key=bytes(range(32)), encryption_key=encryption_key)
    prior = publish_checkpoint(store, installation, payload, **args)
    with pytest.raises(RestoreBlocked):
        publish_checkpoint(store, installation, dict(payload, sequence=2, erasures=[]),
                           previous_pin=prior, **args)
    with pytest.raises(RestoreBlocked):
        publish_checkpoint(store, installation, dict(payload, sequence=0),
                           previous_pin=prior, **args)
    extended = dict(payload, sequence=2)
    assert publish_checkpoint(store, installation, extended, previous_pin=prior, **args).sequence == 2


def test_checkpoint_hmac_and_fernet_cannot_reuse_same_raw_key(publication):
    import base64
    from src.core.gdpr.journal_publication import publish_checkpoint
    from src.core.gdpr.restore_checkpoint import RestoreBlocked

    store, installation, _, encryption_key, _ = publication
    payload = {"version": "c24-restore-v3", "organization_id": str(uuid4()),
               "backup_id": "synthetic-backup", "sequence": 0, "complete": True,
               "erasures": [], "stay_purges": [], "receipt_prunes": []}
    with pytest.raises(RestoreBlocked):
        publish_checkpoint(store, installation, payload,
                           verification_key=base64.urlsafe_b64decode(encryption_key),
                           encryption_key=encryption_key)


def test_r2_create_once_and_ambiguous_write_verified_before_tip(publication):
    import io
    from types import SimpleNamespace
    from src.core.gdpr.journal_publication import R2JournalStore, publish_segment

    class S3:
        meta = SimpleNamespace(endpoint_url="https://" + "a" * 32 + ".eu.r2.cloudflarestorage.com")

        def __init__(self):
            self.objects = {}

        def put_object(self, *, Bucket, Key, Body, IfNoneMatch, ContentType):
            assert Bucket == "melpis-staging-gdpr-journal"
            assert IfNoneMatch == "*" and ContentType == "application/octet-stream"
            if Key not in self.objects:
                self.objects[Key] = Body
            # response lost after storing, or existing object rejected by R2
            raise OSError("synthetic uncertain result")

        def get_object(self, *, Bucket, Key):
            assert Bucket == "melpis-staging-gdpr-journal"
            body = self.objects[Key]
            return {"Body": io.BytesIO(body), "ContentLength": len(body)}

    _, installation, signer, key, anchor = publication
    client = S3()
    store = R2JournalStore(client)
    entries = [_entry()]
    tip = publish_segment(store, installation, anchor, entries, signer=signer, encryption_key=key)
    assert publish_segment(store, installation, anchor, entries, signer=signer, encryption_key=key) == tip
    client.objects[next(iter(client.objects))] = b"invalid"
    with pytest.raises(RestoreJournalInvalid):
        publish_segment(store, installation, anchor, entries, signer=signer, encryption_key=key)


def test_r2_store_rejects_non_eu_or_non_journal_endpoint():
    from types import SimpleNamespace
    from src.core.gdpr.journal_publication import R2JournalStore

    with pytest.raises(RestoreJournalInvalid):
        R2JournalStore(SimpleNamespace(meta=SimpleNamespace(
            endpoint_url="https://" + "a" * 32 + ".r2.cloudflarestorage.com")))
