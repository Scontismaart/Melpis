"""Synthetic Windows/Linux authority recovery rehearsal with no network.

Run from the repository root. The designated local application image supplies
Python and its dependencies. The receiving container sees source code and
encrypted artifacts only, and receives synthetic recovery trust through stdin.
"""

import argparse
from pathlib import Path
import json
import subprocess
import sys
import tempfile
from uuid import UUID, uuid4

from cryptography.fernet import Fernet

from src.core.gdpr.independent_tip import AuthorityRecoveryPin, IndependentTipStore
from src.core.gdpr.journal_custody import JournalKeys, recover_keys, seal_keys
from src.core.gdpr.journal_publication import LocalJournalStore, publish_segment, read_chain
from src.core.gdpr.restore_journal import JournalCheckpoint, RestoreJournalInvalid


def receive():
    evidence = json.load(sys.stdin)
    installation = UUID(evidence["installation_id"])
    tip = JournalCheckpoint(**evidence["tip"])
    pin = AuthorityRecoveryPin(installation, tip, evidence["digest"])
    root = Path("/recovery")
    keys = recover_keys((root / "journal-keys.enc").read_bytes(), evidence["key_wrap"].encode(), installation)
    with tempfile.TemporaryDirectory(prefix="melpis-a06-replacement-") as directory:
        path = Path(directory) / "authority.sqlite"
        authority_key = evidence["authority_wrap"].encode()
        try:
            IndependentTipStore.recover(path, installation, pin, (root / "older.enc").read_bytes(), authority_key)
        except RestoreJournalInvalid:
            if path.exists():
                raise RuntimeError("Rejected recovery must leave the destination empty") from None
        else:
            raise RuntimeError("Older authority was accepted")
        authority = IndependentTipStore.recover(
            path, installation, pin, (root / "current.enc").read_bytes(), authority_key)
        # Docker's bind mount may expose a host-mapped owner/mode. Copy only
        # encrypted entries into a private local directory before use.
        object_root = Path(directory) / "objects"
        object_root.mkdir(mode=0o700)
        sealed_name = f"{installation}-{tip.sequence}-{tip.sha256}.fernet"
        store = LocalJournalStore(object_root)
        store.put_once(sealed_name, (root / sealed_name).read_bytes())
        genesis = JournalCheckpoint(0, "0" * 64)
        entries = read_chain(
            store, installation, genesis, authority.chain_after(genesis), authority.current(),
            trusted_public_key=keys.signer().public_key(), encryption_key=keys.journal_fernet)
        if len(entries) != 1 or str(entries[0].operation_id) != evidence["operation_id"]:
            raise RuntimeError("Recovered journal mismatch")
    print("Authority and journal recovered; outdated sealed authority rejected")


def run(image):
    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="melpis-a06-synthetic-portability-") as directory:
        root = Path(directory)
        incoming, original = root / "transfer", root / "original"
        incoming.mkdir(mode=0o700)
        original.mkdir(mode=0o700)
        installation = uuid4()
        keys = JournalKeys.generate(installation)
        authority = IndependentTipStore.initialize(original / "authority.sqlite", installation)
        authority_wrap, key_wrap = Fernet.generate_key(), Fernet.generate_key()
        previous, previous_enc = authority.seal_for_recovery(authority_wrap)
        (incoming / "older.enc").write_bytes(previous_enc)
        store = LocalJournalStore(incoming)
        entry = {"organization_id": str(uuid4()), "operation_id": str(uuid4()), "scope": "organization",
                 "subject_id": None, "identifier_digest": None, "key_id": None}
        signer = keys.signer()
        tip = publish_segment(store, installation, previous.tip, [entry],
                              signer=signer, encryption_key=keys.journal_fernet)
        authority.advance(store, previous.tip, tip,
                          trusted_public_key=signer.public_key(), encryption_key=keys.journal_fernet)
        pin, sealed = authority.seal_for_recovery(authority_wrap)
        (incoming / "current.enc").write_bytes(sealed)
        (incoming / "journal-keys.enc").write_bytes(seal_keys(keys, key_wrap))
        inputs = {
            "installation_id": str(installation), "tip": {"sequence": tip.sequence, "sha256": tip.sha256},
            "digest": pin.sha256, "authority_wrap": authority_wrap.decode(), "key_wrap": key_wrap.decode(),
            "operation_id": entry["operation_id"],
        }
        result = subprocess.run([
            "docker", "run", "--rm", "--pull", "never", "--network", "none", "-i",
            "--mount", f"type=bind,src={repo / 'src'},target=/tool/src,readonly",
            "--mount", f"type=bind,src={Path(__file__).resolve()},target=/tool/drill.py,readonly",
            "--mount", f"type=bind,src={incoming},target=/recovery,readonly",
            "--entrypoint", "python", image, "/tool/drill.py", "--receive",
        ], input=json.dumps(inputs), text=True, capture_output=True, timeout=120, check=False)
    if result.returncode or result.stdout.strip() != "Authority and journal recovered; outdated sealed authority rejected":
        raise RuntimeError("Isolated authority recovery rehearsal failed")
    print(result.stdout.strip())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="phase8-api:qa-amd64")
    parser.add_argument("--receive", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    receive() if args.receive else run(args.image)
