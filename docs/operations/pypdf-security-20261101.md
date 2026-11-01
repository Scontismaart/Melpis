# pypdf advisory matrix

Checked 2026-11-01 against unauthenticated GitHub Advisory API, OSV records, PyPI release metadata, and the existing c24-pr153-audit/pip-audit.json. GitHub and OSV agree on identifiers, affected ranges, and first fixed versions. The audit lists pypdf 6.21.2 and these seven findings; it also contains unrelated findings, so this is not a claim of a clean overall audit.

| CVE | Upstream advisory | Affected / first fixed | Trigger / effect | Upstream fix commit |
|---|---|---|---|---|
| CVE-2026-119203 | [GHSA-5849-834g-hvw5](https://github.com/py-pdf/pypdf/security/advisories/GHSA-5849-834g-hvw5) | <6.21.4 / 6.21.4 | Incomplete PDF without trailer: infinite loop in xref trailer search | [06593f9](https://github.com/py-pdf/pypdf/commit/06593f993789a4b573dd955730c1a7051128bb2a) |
| CVE-2026-119204 | [GHSA-cggf-277g-3q54](https://github.com/py-pdf/pypdf/security/advisories/GHSA-cggf-277g-3q54) | <6.21.4 / 6.21.4 | Page tree with many shared resources: high memory | [ea4ac66](https://github.com/py-pdf/pypdf/commit/ea4ac663133f1b3de1d7a8f2e6fa239802fde79e) |
| CVE-2026-119205 | [GHSA-mfr5-77xh-9j7h](https://github.com/py-pdf/pypdf/security/advisories/GHSA-mfr5-77xh-9j7h) | <6.21.4 / 6.21.4 | Cloning a crafted dictionary: infinite loop | [f176db1](https://github.com/py-pdf/pypdf/commit/f176db1a1f7f9af3e472e69a843ef1175653b044) |
| CVE-2026-119206 | [GHSA-42gh-23hr-vphw](https://github.com/py-pdf/pypdf/security/advisories/GHSA-42gh-23hr-vphw) | <6.21.3 / 6.21.3 | Retrieving page labels: long runtime | [b891fef](https://github.com/py-pdf/pypdf/commit/b891fef37000cd86baa6e9848a3387d080b3b03e) |
| CVE-2026-119207 | [GHSA-4vqv-7265-gm2w](https://github.com/py-pdf/pypdf/security/advisories/GHSA-4vqv-7265-gm2w) | <6.21.3 / 6.21.3 | Invalid dictionary exception in strict mode: high memory | [6427025](https://github.com/py-pdf/pypdf/commit/64270258c0eced720f7960aecf8cef31afd39f8d) |
| CVE-2026-119208 | [GHSA-62m5-6wh8-28x7](https://github.com/py-pdf/pypdf/security/advisories/GHSA-62m5-6wh8-28x7) | <6.21.3 / 6.21.3 | Page count on encrypted PDF with wrong /Count: high runtime/memory | [82b9195](https://github.com/py-pdf/pypdf/commit/82b91951dc74496bb3efe6465434f041bb6a5e6b) |
| CVE-2026-119209 | [GHSA-x3rh-gjgm-fmqc](https://github.com/py-pdf/pypdf/security/advisories/GHSA-x3rh-gjgm-fmqc) | <6.21.3 / 6.21.3 | Opening cross-reference streams: high memory | [a93fe3a](https://github.com/py-pdf/pypdf/commit/a93fe3a901fd00af38f0a105c3ba7175fa72dcb4) |

Each GitHub API record is available at https://api.github.com/advisories/<GHSA-ID>; OSV record at https://api.osv.dev/v1/vulns/<GHSA-ID>. OSV cites the upstream advisories and commits above. [PyPI 6.21.4 metadata](https://pypi.org/pypi/pypdf/6.21.4/json) lists wheel SHA256 `33e4fb41ca352810eb4e9838aa0d82d6888a5f8bf682f43e99cce5faa666255c` and source archive SHA256 `c8817e3f888d47a087872ded69812c1ef30cacb871d6e053e0c00bbaf0bc2163`. [Upstream release](https://github.com/py-pdf/pypdf/releases/tag/6.21.4).

Local applicability: safe_extraction.py constructs PdfReader before rejecting encryption and before the page-count check; parser work therefore precedes those checks. It also calls get_data() before checking decompressed size. The isolated worker provides timeout and process resource controls, but those limits are defense in depth and do not prove the parser vulnerabilities unreachable. Dictionary cloning, page-label retrieval, and strict-mode exception conditions are advisory-specific and were not established as reachable in this extraction path. No exploit or performance test was performed here.


## Melpis scoped change and observed verification

Baseline main: `2a23abab12797ddb6b4c7e135a9f96adc9839923`. The only production change is `requirements.txt`: pypdf6.21.2 to6.21.4. No application code, dependency metadata, reviewed CrewAI backport/provenance, audit policy, workflow, privilege or C2.4 branch is modified.

Official wheel/source hashes above were verified after download; wheel CRC/RECORD and all63 installed wheel files match. Clean Python3.12 venv used the existing reviewed-runtime path and standard pip resolver, without dependency overrides or `--no-deps`; pip check and reviewed provenance verification passed. Installed: pypdf6.21.4, CrewAI/Core1.15.4, LiteLLM1.103.10, Pillow13.0.0, RapidOCR1.4.4, sentence-transformers5.6.0, filelock4.2.1. LiteLLM's optional proxy-runtime constraint pypdf>=6.16.1,<7.0 is compatible; no new CrewAI pypdf constraint is needed.

The new regression sends a tiny synthetic incomplete xref, following the upstream EOF regression shape, only through the real isolated worker:256MiB memory ceiling and8s parent deadline, with process/job cleanup assertions. With6.21.2 it exceeds the deadline and the owned child is terminated; with6.21.4 it rejects safely and a subsequent healthy PDF extracts exact text/page/OCR metadata. Encrypted input is also rejected safely. No untrusted parsing occurs in the shared pytest parent; no large-memory exploit corpus or provider call is used.

Focused extraction, worker limits, B02 replacement/publication/retry/quota/tenant and RAG/route suite:127 passed,1 existing POSIX-only skip on Windows,1 deprecation warning. The Linux CI executes the POSIX limit test; no test was removed or newly skipped. This does not claim every advisory exploit was independently reproduced.

Fresh strict installed-inventory audit:204 Python distributions, no pypdf findings; unchanged policy passed. The raw audit retains9 findings in2 previously reviewed packages (chromadb1.1.1 and json-repair0.25.3;5 unique advisory IDs), not zero vulnerabilities. Frontend full checks passed at the existing high-severity gate; raw npm audit retains5 moderate development-tool findings,0 high/critical. No exceptions, suppression or policy changes were added.

Exact-head full backend/CI/four-build receipts belong in the PR once completed. No merge, deployment, remote SQL, operational activation or real provider call is authorized by this change. C2.4 #153/#154 and C05/A06 new-data lifecycle approval remain separate; a secure dependency baseline does not certify that stack or privacy gate.
