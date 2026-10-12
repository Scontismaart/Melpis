"""Regression checks for dependency versions required by security advisories."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_weasyprint_pin_contains_ssrf_fix() -> None:
    """GHSA-jf6q-chmf-3h3v (URL fetcher bypass) is fixed in WeasyPrint 70."""
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    match = re.search(r"(?m)^weasyprint==(\d+)\.(\d+)(?:\.(\d+))?$", requirements)

    assert match, "WeasyPrint must remain exactly pinned"
    version = tuple(int(part or 0) for part in match.groups())
    assert version >= (70, 0, 0)


def test_torch_pin_is_cpu_only_for_release_images() -> None:
    """Oracle launch hosts have no GPU; CUDA wheels waste free-tier disk."""
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")

    assert "--find-links https://download.pytorch.org/whl/cpu/torch/" in requirements
    assert "--extra-index-url" not in requirements
    assert re.search(r"(?m)^torch==2\.14\.0\+cpu$", requirements)


def test_pyjwt_pin_fixes_raw_token_revocation_advisory() -> None:
    """GHSA-hxm8-2xgr-2p9m is fixed in PyJWT 2.14.0 and later."""
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    match = re.search(r"(?mi)^PyJWT\[crypto\]==(\d+)\.(\d+)\.(\d+)$", requirements)

    assert match, "PyJWT[crypto] must remain exactly pinned"
    version = tuple(int(part) for part in match.groups())
    assert version >= (2, 15, 1)
    assert not re.search(r"(?mi)^python-jose(?:\[.*\])?==", requirements)
