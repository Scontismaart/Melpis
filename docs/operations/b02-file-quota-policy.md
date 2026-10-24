# B02 file quota policy proposal

**Status: historical proposal, superseded by the founder mandate of 2026-10-24. No commercial amounts are configured runtime limits.**

The former 5/15/40 MB and 1 MB trial figures below are historical benchmark
context, not the current product recommendation. The preferred larger-storage
direction requires separate INF01/INF02 capacity and original-file lifecycle
review; trial remains undecided. See `v1-private-beta-progress.md`. B02 does
not implement object storage or raise its uniform 20 MiB technical ceiling.

## Current behavior shown in the dashboard

PDF/RAG is included for Essenziale, Crescita, and Scala when the account is active or has a valid trial. The commercial file quota has no approved value yet. The technical upload ceiling is 20 MiB per file and remains a separate server-side check.

Original files are not retained after processing. The system keeps extracted text chunks, embeddings, and document metadata. A replacement needs the customer to provide the source file again. Historical records can lack trustworthy original byte counts; the quota must report that state as unknown rather than treating it as zero.

The proposed `file_quota` response distinguishes `new_uploads_allowed` from `replacements_allowed`. A full quota can block new files while still allowing a replacement attempt, since the backend can determine whether the replacement reduces occupied bytes. `historical_usage_unknown` must gate new file uploads until the historical usage is reconciled. FAQ, pasted text, structured business data, and web sources do not consume this original-file quota.

The dashboard displays byte values as decimal MB (`bytes / 1,000,000`). The API keeps the technical upload ceiling and document/page/chunk usage separate from commercial file quota. `originals_retained` is false.

## Superseded amounts — historical context only

| Account | Proposed original-file quota | Scope |
| --- | ---: | --- |
| Essenziale | 5 MB | Paid subscription |
| Crescita | 15 MB | Paid subscription |
| Scala | 40 MB | Paid subscription |
| Trial | 1 MB shared | Seven days, with the existing 150-response trial allowance |

The trial allowance would be shared across the organization, independent of any selected paid plan. On conversion, existing byte usage would be retained and evaluated against the paid budget; no second allocation or reset is granted. RAG generation would still stop at the first existing trial limit (seven days or 150 replies), while manual dashboard functions remain accessible.

These figures are a product proposal only. They do not set runtime behavior, and response limits and plan prices are unchanged. A billing renewal would not reset stored usage; usage falls when files are deleted or replaced with smaller originals.

## What this quota measures

The proposal measures the size of original uploaded files. It does not measure extraction CPU time, memory use, OCR work, or PostgreSQL/pgvector storage. A benchmark example indexed an approximately 0.7 MB, 200-page source PDF; extracted field data was approximately 6.9 MB and encoding took approximately 186 seconds. These are separate resource costs and need their own operational controls.

## Historical data and rollout gate

The C01 migration/cutover must account for older documents whose original byte count is missing or cannot be verified. Treat those rows as unknown and expose `historical_usage_unknown`; do not silently certify them as zero-byte usage. Use a separately reviewed, evidence-based C01 reconciliation, or an explicit owner deletion followed by a fresh upload after unknown records have been resolved. The current publisher rejects replacement of an unknown record; supplying text or changing its type cannot silently certify zero bytes. Keep the original technical 20 MiB per-file ceiling in force throughout the rollout.

## API fields

`file_quota` reports:

- `status`: `not_configured`, `historical_usage_unknown`, `available`, `full`, or `over_limit`;
- `limit_bytes`, `committed_bytes`, `reserved_bytes`, `occupied_bytes`, and `remaining_bytes`;
- `unknown_documents` and `scope` (`trial` or `paid`);
- `originals_retained` (false), `new_uploads_allowed`, and `replacements_allowed`.

`commercial_limits` reports only the configured file-byte policy and its status. Existing technical limits and document/page/chunk usage remain available for operations and diagnostics; they are not commercial document or page entitlements.

## Capacity rationale and uncertainty

5 MB is a conservative starter trial-to-paid step for small text menus; 15 MB
and 40 MB provide larger retained libraries without selecting a different
model, parser, safety ceiling or feature subset. These are launch candidates,
not measured safe maximum tenant capacities. In the baseline, 0.695 MB of
selectable source data produced 3,600 chunks and about 6.89 MB in vector/text/
metadata fields; publication took 13.8 seconds, and embedding took 186.6
seconds with about 1.53 GB peak worker RSS. The complete shared relation plus
indexes after the tiers was 17.77 MB, not a per-tenant storage bill.

Do not multiply those ratios into a guarantee: compression, text density,
format and scans vary considerably. A tiny file can produce expensive OCR or
many chunks; manual/FAQ/web inputs have no original-file charge. All inputs
therefore retain bounded bytes/text/pages/chunks, rate limits, serialized
preparation, timeouts and isolated process budgets, uniformly for every plan.
C01 must measure the real host and establish operational capacity before
activation; two independent resident model workers remain uncertified.

No document-count or aggregate-page commercial allowance is proposed or
implemented. MB is decimal (1,000,000 bytes); the technical per-file safety
ceiling remains 20 MiB (20,971,520 bytes) for all plans. Product defaults for
all three plan budgets and the trial budget remain None until approval.
