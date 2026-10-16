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


@pytest.mark.parametrize("entry", [
    {"name": "torch", "skip_reason": "Dependency not found on PyPI"},
    {"name": "torch", "version": "2.14.0+cpu", "vulns": [], "skip_reason": ""},
    {"name": "package", "version": "1.0"},
    {"name": "package", "vulns": []},
    {"name": "", "version": "1.0", "vulns": []},
    {"name": "package", "version": 1, "vulns": []},
    {"name": "package", "version": "1.0", "vulns": None},
    {"name": "package", "version": "1.0", "vulns": [{}]},
    {"name": "package", "version": "1.0", "vulns": [{"id": ""}]},
    {"name": "package", "version": "1.0", "vulns": [None]},
    None,
])
def test_skipped_or_malformed_dependency_fails_closed(tmp_path, entry, capsys):
    dependencies = [{"name": f"clean-package-{n}", "version": "1.0", "vulns": []}
                    for n in range(50)]
    dependencies.append(entry)
    report = tmp_path / "audit.json"
    report.write_text(json.dumps({"dependencies": dependencies}), encoding="utf-8")
    assert AUDIT["main"](str(report)) == 2
    assert "Audited" not in capsys.readouterr().out


@pytest.mark.parametrize("contents", ["null", "[]", "7", "{broken", "{}",
                                      '{"dependencies":{}}'])
def test_invalid_report_fails_closed(tmp_path, contents):
    report = tmp_path / "audit.json"
    report.write_text(contents, encoding="utf-8")
    assert AUDIT["main"](str(report)) == 2


def test_complete_clean_report_passes(tmp_path):
    dependencies = [{"name": f"clean-package-{n}", "version": "1.0", "vulns": []}
                    for n in range(50)]
    report = tmp_path / "audit.json"
    report.write_text(json.dumps({"dependencies": dependencies}), encoding="utf-8")
    assert AUDIT["main"](str(report)) == 0


def test_duplicate_dependency_cannot_inflate_audit_coverage(tmp_path):
    dependencies = [{"name": "package", "version": "1.0", "vulns": []}] * 50
    report = tmp_path / "audit.json"
    report.write_text(json.dumps({"dependencies": dependencies}), encoding="utf-8")
    assert AUDIT["main"](str(report)) == 2


@pytest.mark.parametrize("alias", ["PACKAGE_name", "package.name", "package---name", "package__._name"])
def test_pep503_aliases_are_duplicate_dependencies(tmp_path, alias):
    dependencies = [{"name": f"clean-package-{n}", "version": "1.0", "vulns": []}
                    for n in range(50)]
    dependencies.extend({"name": name, "version": "1.0", "vulns": []}
                        for name in ("package-name", alias))
    report = tmp_path / "audit.json"
    report.write_text(json.dumps({"dependencies": dependencies}), encoding="utf-8")
    assert AUDIT["main"](str(report)) == 2


def test_pep503_alias_preserves_exact_reviewed_package_policy(tmp_path):
    dependencies = [{"name": f"clean-package-{n}", "version": "1.0", "vulns": []}
                    for n in range(50)]
    dependencies.append({"name": "JSON__REPAIR", "version": "0.25.3",
                         "vulns": [{"id": "GHSA-xf7x-x43h-rpqh"}]})
    report = tmp_path / "audit.json"
    report.write_text(json.dumps({"dependencies": dependencies}), encoding="utf-8")
    assert AUDIT["main"](str(report)) == 0


@pytest.mark.parametrize("field", ["name", "version", "id"])
@pytest.mark.parametrize("control", ["\n", "\r", "\t", "\x00", "\x1b", "\x85", "\u202e"])
def test_control_characters_fail_before_printing_report_details(tmp_path, capsys, field, control):
    dependencies = [{"name": f"clean-package-{n}", "version": "1.0", "vulns": []}
                    for n in range(50)]
    entry = {"name": "package", "version": "1.0", "vulns": [{"id": "GHSA-unreviewed"}]}
    marker = f"private-marker{control}payload"
    if field == "id":
        entry["vulns"][0]["id"] = marker
    else:
        entry[field] = marker
    dependencies.append(entry)
    report = tmp_path / "audit.json"
    report.write_text(json.dumps({"dependencies": dependencies}), encoding="utf-8")
    assert AUDIT["main"](str(report)) == 2
    captured = capsys.readouterr()
    assert "private-marker" not in captured.out + captured.err
    assert "Audited" not in captured.out
