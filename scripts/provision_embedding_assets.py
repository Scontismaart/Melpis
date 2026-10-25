"""Explicit build-time provisioning of immutable public assets; never used by runtime."""

import argparse
import hashlib
import json
import shutil
from pathlib import Path
import httpx

REVISION = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
FILES = {
    "onnx/model.onnx": (
        470301610,
        "10f7a088420252b26caf819236ca2c9d2987afd0fc06fec7553b542a5655a05a",
    ),
    "onnx/model_quint8_avx2.onnx": (
        118453870,
        "98a01d88b7de996cdea58c32ca71208c09968d143798814b2ea09d3439dc334f",
    ),
}


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--destination", type=Path, required=True)
    p.add_argument("--allow-download", action="store_true")
    a = p.parse_args()
    cached = (
        Path.home()
        / ".cache/huggingface/hub/models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2/snapshots"
        / REVISION
    )
    a.destination.mkdir(parents=True, exist_ok=True)
    for name in (
        "tokenizer.json",
        "sentence_bert_config.json",
        "1_Pooling/config.json",
    ):
        target = a.destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if sha(target) != sha(cached / name):
                raise SystemExit("existing_asset_mismatch")
        else:
            shutil.copyfile(cached / name, target)
    receipt = {"revision": REVISION, "files": {}}
    with httpx.Client(
        trust_env=False,
        follow_redirects=True,
        verify=True,
        timeout=httpx.Timeout(30, connect=8),
    ) as client:
        for name, (size, expected) in FILES.items():
            target = a.destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                if not a.allow_download:
                    raise SystemExit("explicit_build_download_required")
                temporary = target.with_suffix(target.suffix + ".partial")
                if temporary.exists():
                    raise SystemExit("partial_asset_exists_requires_review")
                h = hashlib.sha256()
                count = 0
                with client.stream(
                    "GET",
                    f"https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2/resolve/{REVISION}/{name}",
                ) as response:
                    response.raise_for_status()
                    with temporary.open("xb") as out:
                        for part in response.iter_bytes(1024 * 1024):
                            count += len(part)
                            if count > size:
                                raise SystemExit("asset_size_mismatch")
                            h.update(part)
                            out.write(part)
                if count != size or h.hexdigest() != expected:
                    raise SystemExit("asset_checksum_mismatch")
                temporary.rename(target)
            if target.stat().st_size != size or sha(target) != expected:
                raise SystemExit("asset_checksum_mismatch")
            receipt["files"][name] = {"bytes": size, "sha256": expected}
            print(
                json.dumps({"verified_asset": name, "bytes": size, "sha256": expected}),
                flush=True,
            )
    (a.destination / "provisioning-receipt.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
