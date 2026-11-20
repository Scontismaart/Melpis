"""Portable operator-side key custody; no DPAPI, database or bucket dependency.

The wrapping key and ciphertext MUST be retained independently by authorized
custodians. No application or CI should generate or hold operational keys.
"""

import base64
from dataclasses import dataclass, field
import json
import secrets
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.core.gdpr.restore_journal import RestoreJournalInvalid, _unique_object


def _raw(key):
    if type(key) is not bytes or len(key) != 44:
        raise ValueError
    raw = base64.b64decode(key, altchars=b"-_", validate=True)
    if len(raw) != 32 or base64.urlsafe_b64encode(raw) != key:
        raise ValueError
    return raw


@dataclass(frozen=True, slots=True)
class JournalKeys:
    installation_id: UUID
    ed25519_seed: bytes = field(repr=False)
    checkpoint_hmac: bytes = field(repr=False)
    journal_fernet: bytes = field(repr=False)
    checkpoint_fernet: bytes = field(repr=False)

    def __post_init__(self):
        try:
            self._raw_keys()
        except (ValueError, TypeError):
            raise RestoreJournalInvalid("journal_key_separation_invalid") from None

    def _raw_keys(self):
        if (type(self.installation_id) is not UUID or not self.installation_id.int
                or type(self.ed25519_seed) is not bytes or len(self.ed25519_seed) != 32
                or type(self.checkpoint_hmac) is not bytes or len(self.checkpoint_hmac) != 32):
            raise ValueError
        keys = (self.ed25519_seed, self.checkpoint_hmac, _raw(self.journal_fernet),
                _raw(self.checkpoint_fernet))
        if len(set(keys)) != len(keys):
            raise ValueError
        return keys

    @classmethod
    def generate(cls, installation_id):
        return cls(installation_id, secrets.token_bytes(32), secrets.token_bytes(32),
                   Fernet.generate_key(), Fernet.generate_key())

    def signer(self):
        return Ed25519PrivateKey.from_private_bytes(self.ed25519_seed)


def seal_keys(keys, wrapping_key):
    """Return authenticated portable ciphertext for independent custody."""
    try:
        if type(keys) is not JournalKeys or _raw(wrapping_key) in keys._raw_keys():
            raise ValueError
        raw = json.dumps({
            "version": 1,
            "installation_id": str(keys.installation_id),
            "ed25519": base64.urlsafe_b64encode(keys.ed25519_seed).decode("ascii"),
            "hmac": base64.urlsafe_b64encode(keys.checkpoint_hmac).decode("ascii"),
            "journal": keys.journal_fernet.decode("ascii"),
            "checkpoint": keys.checkpoint_fernet.decode("ascii"),
        }, sort_keys=True, separators=(",", ":")).encode()
        return Fernet(wrapping_key).encrypt(raw)
    except (TypeError, ValueError):
        raise RestoreJournalInvalid("journal_key_seal_failed") from None


def recover_keys(ciphertext, wrapping_key, installation_id):
    """Recover keys on another host from independently supplied sealed material."""
    try:
        if type(ciphertext) is not bytes or not 1 <= len(ciphertext) <= 4096:
            raise ValueError
        raw = Fernet(wrapping_key).decrypt(ciphertext)
        value = json.loads(raw, object_pairs_hook=_unique_object)
        if (type(value) is not dict or set(value) != {
                "version", "installation_id", "ed25519", "hmac", "journal", "checkpoint"}
                or type(value["version"]) is not int or value["version"] != 1
                or value["installation_id"] != str(installation_id)):
            raise ValueError
        keys = JournalKeys(installation_id, _raw(value["ed25519"].encode("ascii")),
                           _raw(value["hmac"].encode("ascii")),
                           value["journal"].encode("ascii"), value["checkpoint"].encode("ascii"))
        if _raw(wrapping_key) in keys._raw_keys():
            raise ValueError
        return keys
    except (TypeError, ValueError, AttributeError, InvalidToken, RecursionError):
        raise RestoreJournalInvalid("journal_key_recovery_failed") from None
