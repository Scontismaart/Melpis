"""Fail CI on new Python advisories while documenting narrow exceptions."""

import json
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


def main(path: str) -> int:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    dependencies = data.get("dependencies")
    if not isinstance(dependencies, list) or len(dependencies) < 50:
        print("Incomplete pip-audit report", file=sys.stderr)
        return 2

    unknown = []
    accepted = []
    for dependency in dependencies:
        name = str(dependency.get("name", "")).lower().replace("_", "-")
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
