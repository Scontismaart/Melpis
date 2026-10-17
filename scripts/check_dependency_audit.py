"""Fail CI on new Python advisories while documenting narrow exceptions."""

import json
import re
import sys
from pathlib import Path

# Reviewed 2026-09-27. Chroma findings concern its HTTP server, which Melpis
# does not run. json-repair 0.25.3 predates the affected schema parser, and
# Melpis supplies no untrusted schema. Reassess on every dependency change.
ACCEPTED = {
    ("chromadb", "1.1.1"): {
        "PYSEC-2026-311",
        "PYSEC-2026-3813",
        "PYSEC-2026-3814",
        "PYSEC-2026-3815",
    },
    ("json-repair", "0.25.3"): {"GHSA-xf7x-x43h-rpqh"},
}


def _safe_text(value) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value.isprintable()


def _canonical_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def main(path: str) -> int:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        print("Invalid pip-audit report", file=sys.stderr)
        return 2
    dependencies = data.get("dependencies") if isinstance(data, dict) else None
    if not isinstance(dependencies, list) or len(dependencies) < 50:
        print("Incomplete pip-audit report", file=sys.stderr)
        return 2

    # A skipped package is unverified, even if the remaining graph is clean.
    # Validate the whole report before printing any successful audit summary.
    seen = set()
    for dependency in dependencies:
        if (
            not isinstance(dependency, dict)
            or "skip_reason" in dependency
            or any(not _safe_text(dependency.get(key))
                   for key in ("name", "version"))
            or not isinstance(dependency.get("vulns"), list)
            or any(not isinstance(vulnerability, dict)
                   or not _safe_text(vulnerability.get("id"))
                   for vulnerability in dependency["vulns"])
        ):
            print("Incomplete or invalid dependency audit coverage", file=sys.stderr)
            return 2
        identity = (_canonical_name(dependency["name"]), dependency["version"])
        if identity in seen:
            print("Duplicate dependency in audit report", file=sys.stderr)
            return 2
        seen.add(identity)

    unknown = []
    accepted = []
    for dependency in dependencies:
        name = _canonical_name(dependency["name"])
        version = str(dependency.get("version", ""))
        allowed = ACCEPTED.get((name, version), set())
        for vulnerability in dependency.get("vulns", []):
            advisory = vulnerability.get("id", "")
            record = f"{name}=={version}: {advisory}"
            (accepted if advisory in allowed else unknown).append(record)

    for record in sorted(set(accepted)):
        print(f"Reviewed residual: {record}")
    for record in sorted(set(unknown)):
        print(f"Unreviewed vulnerability: {record}", file=sys.stderr)
    print(f"Audited {len(dependencies)} Python dependencies")
    return 1 if unknown else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: check_dependency_audit.py <pip-audit.json>")
    raise SystemExit(main(sys.argv[1]))
