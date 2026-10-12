"""Reviewed residuals never authorize other versions or advisory IDs."""
import json
import runpy
from pathlib import Path

import pytest

AUDIT = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/check_dependency_audit.py"))


@pytest.mark.parametrize("name,version,advisory", [
    ("python-jose", "3.5.0", "CVE-2026-85394"),
    ("PyJWT", "2.15.1", "GHSA-hxm8-2xgr-2p9m"),
])
def test_jwt_libraries_have_no_advisory_exception(tmp_path, name, version, advisory):
    dependencies = [{"name": f"clean-package-{n}", "version": "1.0", "vulns": []}
                    for n in range(50)]
    dependencies.append({"name": name, "version": version,
                         "vulns": [{"id": advisory}]})
    report = tmp_path / "audit.json"
    report.write_text(json.dumps({"dependencies": dependencies}), encoding="utf-8")
    assert AUDIT["main"](str(report)) == 1


def test_pyjwt_crypto_pin_replaces_python_jose():
    requirements = (Path(__file__).resolve().parents[2] / "requirements.txt").read_text(
        encoding="utf-8"
    )
    assert "PyJWT[crypto]==2.15.1" in requirements
    assert "python-jose" not in requirements
    assert not any(name.startswith("python-jose") for name, _version in AUDIT["ACCEPTED"])


def test_pyjwt_raw_token_revocation_advisory_is_not_accepted():
    assert ("pyjwt", "2.13.0") not in AUDIT["ACCEPTED"]


def test_incomplete_audit_still_fails_closed(tmp_path):
    report = tmp_path / "audit.json"
    report.write_text(json.dumps({"dependencies": []}), encoding="utf-8")
    assert AUDIT["main"](str(report)) == 2
