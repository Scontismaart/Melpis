"""Authenticate post-backup erasure evidence before restore reconciliation.

The verification key and current tip must come from the independent recovery
store, never from the restored database or the submitted journal segment.
Verification alone does not replay erasure or authorize traffic.
"""

from dataclasses import dataclass
from hashlib import sha256
import hmac
import json
import re
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


_DOMAIN = b"melpis.restore.erasure-journal.v1\0"
_MAX_BYTES = 1024 * 1024
_MAX_ENTRIES = 1000
_ZERO_DIGEST = "0" * 64


class RestoreJournalInvalid(ValueError):
    """A safe error code: never retain evidence content in exception text."""


@dataclass(frozen=True, slots=True)
class JournalCheckpoint:
    sequence: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ErasureEvidence:
    sequence: int
    organization_id: UUID
    operation_id: UUID
    scope: str
    subject_id: UUID | None
    identifier_digest: str | None
    key_id: str | None


def _hex(value, length):
    return type(value) is str and re.fullmatch(f"[0-9a-f]{{{length}}}", value) is not None


def _checkpoint(value):
    return (type(value) is JournalCheckpoint and type(value.sequence) is int
            and 0 <= value.sequence < 2**63 and _hex(value.sha256, 64)
            and (value.sequence == 0) == (value.sha256 == _ZERO_DIGEST))


def _uuid(value):
    if type(value) is not str:
        raise ValueError
    parsed = UUID(value)
    if not parsed.int or str(parsed) != value:
        raise ValueError
    return parsed


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def verify_erasure_segment(
    payload: bytes, signature: bytes, *,
    installation_id: UUID, trusted_public_key: Ed25519PublicKey,
    backup_checkpoint: JournalCheckpoint, independent_tip: JournalCheckpoint,
) -> tuple[ErasureEvidence, ...]:
    """Verify one bounded, contiguous segment reaching its independent tip.

    The backup checkpoint must be bound to the encrypted backup manifest.
    For a long journal, verify each segment against trusted external
    checkpoints and verify coverage through the latest independent tip.
    """
    if (type(installation_id) is not UUID or not installation_id.int
            or not _checkpoint(backup_checkpoint) or not _checkpoint(independent_tip)
            or not 1 <= independent_tip.sequence - backup_checkpoint.sequence <= _MAX_ENTRIES
            or type(payload) is not bytes or not 1 <= len(payload) <= _MAX_BYTES
            or type(signature) is not bytes or len(signature) != 64
            or not isinstance(trusted_public_key, Ed25519PublicKey)):
        raise RestoreJournalInvalid("journal_bounds_invalid")
    if not hmac.compare_digest(sha256(payload).hexdigest(), independent_tip.sha256):
        raise RestoreJournalInvalid("journal_tip_mismatch")
    try:
        trusted_public_key.verify(signature, _DOMAIN + payload)
    except InvalidSignature:
        raise RestoreJournalInvalid("journal_signature_invalid") from None
    try:
        segment = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object)
        if (type(segment) is not dict or set(segment) != {
                "version", "installation_id", "previous_sha256", "entries"}
                or type(segment["version"]) is not int or segment["version"] != 1
                or _uuid(segment["installation_id"]) != installation_id
                or segment["previous_sha256"] != backup_checkpoint.sha256):
            raise ValueError
        entries = segment["entries"]
        if (type(entries) is not list
                or len(entries) != independent_tip.sequence - backup_checkpoint.sequence):
            raise ValueError
        result = []
        operations = set()
        for sequence, record in enumerate(entries, backup_checkpoint.sequence + 1):
            if (type(record) is not dict or set(record) != {
                    "sequence", "organization_id", "operation_id", "scope",
                    "subject_id", "identifier_digest", "key_id"}
                    or type(record["sequence"]) is not int or record["sequence"] != sequence):
                raise ValueError
            org, operation = _uuid(record["organization_id"]), _uuid(record["operation_id"])
            if (org, operation) in operations:
                raise ValueError
            operations.add((org, operation))
            subject, digest, key = record["subject_id"], record["identifier_digest"], record["key_id"]
            if record["scope"] == "contact":
                subject = _uuid(subject)
                if not _hex(digest, 64) or not _hex(key, 16):
                    raise ValueError
            elif record["scope"] != "organization" or any(v is not None for v in (subject, digest, key)):
                raise ValueError
            result.append(ErasureEvidence(sequence, org, operation, record["scope"], subject, digest, key))
        return tuple(result)
    except (ValueError, TypeError, RecursionError):
        raise RestoreJournalInvalid("journal_record_invalid") from None
