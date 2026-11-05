from copy import deepcopy
from hashlib import sha256
import json
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from src.core.gdpr.restore_journal import (
    JournalCheckpoint, RestoreJournalInvalid, verify_erasure_segment,
)


@pytest.fixture
def journal():
    private = Ed25519PrivateKey.generate()
    installation, organization = uuid4(), uuid4()
    anchor = JournalCheckpoint(17, "a" * 64)
    entries = [
        {"sequence": 18, "organization_id": str(organization),
         "operation_id": str(uuid4()), "scope": "contact",
         "subject_id": str(uuid4()), "identifier_digest": "c" * 64, "key_id": "b" * 16},
        {"sequence": 19, "organization_id": str(uuid4()),
         "operation_id": str(uuid4()), "scope": "organization",
         "subject_id": None, "identifier_digest": None, "key_id": None},
    ]
    document = {"version": 1, "installation_id": str(installation),
                "previous_sha256": anchor.sha256, "entries": entries}

    def signed(doc, *, count=19, signer=private):
        raw = doc if type(doc) is bytes else json.dumps(doc).encode("utf-8")
        return raw, signer.sign(b"melpis.restore.erasure-journal.v1\0" + raw), JournalCheckpoint(count, sha256(raw).hexdigest())

    def verify(raw, signature, tip):
        return verify_erasure_segment(
            raw, signature, installation_id=installation,
            trusted_public_key=private.public_key(), backup_checkpoint=anchor, independent_tip=tip,
        )

    return document, signed, verify


def test_journal_preserves_minimal_erasure_scope_and_order(journal):
    document, signed, verify = journal
    result = verify(*signed(document))
    assert [entry.sequence for entry in result] == [18, 19]
    assert str(result[0].subject_id) == document["entries"][0]["subject_id"]
    assert result[0].organization_id != result[1].organization_id
    assert result[1].scope == "organization" and result[1].identifier_digest is None


def test_old_signed_segment_cannot_replace_latest_independent_tip(journal):
    document, signed, verify = journal
    _, _, latest = signed(document)
    document["entries"].pop()
    old_payload, old_signature, _ = signed(document, count=18)
    with pytest.raises(RestoreJournalInvalid, match="journal_tip_mismatch"):
        verify(old_payload, old_signature, latest)


def test_a_new_signature_from_an_untrusted_key_is_rejected(journal):
    document, signed, verify = journal
    with pytest.raises(RestoreJournalInvalid, match="journal_signature_invalid"):
        verify(*signed(document, signer=Ed25519PrivateKey.generate()))


@pytest.mark.parametrize("change", ["sequence", "anchor", "installation", "duplicate", "scope", "text"])
def test_signed_but_invalid_evidence_is_rejected(journal, change):
    document, signed, verify = journal
    if change == "sequence":
        document["entries"].reverse()
    elif change == "anchor":
        document["previous_sha256"] = "d" * 64
    elif change == "installation":
        document["installation_id"] = str(uuid4())
    elif change == "duplicate":
        document["entries"][1] = deepcopy(document["entries"][0])
        document["entries"][1]["sequence"] = 19
    elif change == "scope":
        document["entries"][1]["subject_id"] = str(uuid4())
    else:
        document["entries"][0]["body"] = "unapproved sensitive input"
    with pytest.raises(RestoreJournalInvalid, match="^journal_record_invalid$"):
        verify(*signed(document))


def test_even_a_valid_signature_cannot_authorize_duplicate_json_keys(journal):
    document, signed, verify = journal
    raw = json.dumps(document).replace('"version": 1', '"version": 1, "version": 1').encode()
    with pytest.raises(RestoreJournalInvalid, match="journal_record_invalid"):
        verify(*signed(raw))
