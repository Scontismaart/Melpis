# Privacy & Data Governance V1.0 — implementation matrix

The founder approved the V1.0 product standards. They do not establish effective
per-tenant database policies, authorize destructive execution, prove deployment,
or establish provider agreements. Controller instructions must be compatible with
the standards and applicable obligations; ordinary tenant overrides cannot extend
standard durations. A specific documented legal obligation is assessed separately.
All holds require a documented reason, minimum scope, competent authority, and an
expiry or review. The existing `legal_hold` flag alone does not demonstrate these
requirements. Periods below are maximums, subject to earlier removal when required
and to applicable documented obligations; external copies must not be represented
as already deleted.

The stores named below reflect the checked-in inventory and code, not a verified
production inventory. Access means the intended authorized path, not proof of a
live permission audit. Export projections are curated and may be partial; excluded
free text and unverified subject relationships require review. See the [A06
inventory](../operations/a06-data-inventory.md) and [C2.4 privacy
contract](../operations/conversation-privacy-c24.md).

The code-owned `src/core/gdpr/product_policy.py` registry now publishes the approved
version and limits. Its worker guard rejects over-cap messages/C2.4 schedules at
enumeration, latest-row recheck and the C2.4 cross-category gate. It never clamps
or inserts tenant policy rows. Unknown simulator schedules are not automatically
executed. The public endpoint distinguishes product maxima from uncertified
effective tenant schedules; an environment flag alone is not operational GO.

Legacy methods `MessageRepository.delete_expired_messages`,
`purge_soft_deleted_messages` and `purge_simulation_requests` remain separate
global mutation helpers exposed by repository wrappers. Static caller inspection
found no current scheduling caller beyond those wrappers. They do not implement
the new policy protocol and must be retired or routed through the approved
tenant-scoped process before cutover; their existence is not retention enforcement.
Likewise `gdpr_policies.retention_days` cannot represent twelve calendar months or
document a complete bounded legal hold. Those are schema/lifecycle implementation
tasks, not new founder preference decisions.

Melpis support access is not granted by this policy or by the customer staff role.
Actual support access requires verified scope, documented reason, authorization
and traceability; its operational procedure and audit evidence must be established
before use. No support user, privilege or remote access was created here.

| Category and actual stores | Approved purpose and duration / trigger | Access and export | Erasure and current gap | Objective gate |
| --- | --- | --- | --- | --- |
| Conversation messages: `messages`, message-bearing `event_log`, related payloads and caches | Requested assistance and necessary booking administration. Ordinary availability at most 60 days; primary content purge within 90 days; earlier erasure when applicable. | Authorized tenant users; tenant export has selected content, subject export is narrower and excludes free text. | Source redaction must invalidate derived C2.4 content. The current worker is gated, processes bounded batches and excludes some records; a separate 60-day visibility control and complete 90-day primary purge are not established. | Map copies and visibility paths; resolve billed and other protected records without losing required counters; verify deadlines, backlog, isolation and replay barriers before GO. |
| C2.4 state, provenance and proposals: `booking_conversation_states`, related source messages, `messages.ai_reply_cache`, `simulation_requests.response` and derived receipts | Continue the administrative request. Maximum 30 days from last pertinent activity; remove earlier when purpose ends or applicable erasure requires it; do not reconstruct from obsolete copies. | Scoped authorized owner/manager and backend; curated tenant and original-subject projections, with unreviewed content excluded. | Whole derived state and associated copies must be invalidated together. Current source-based expiry uses creation or completion timestamps, not a verified last-pertinent-activity timestamp. | Classify A04 copies, determine and protect authoritative activity timestamps, verify original-subject linkage, concurrent updates, expiry and replay fences. |
| Request technical receipts and idempotency: C2.4 request receipts, request IDs/hashes and scoped technical evidence; `webhook_idempotency`, `outbound_dedup` and other receipt stores require classification | Necessary request completion, deduplication and security. Ordinarily 90 days from request conclusion, earlier when purpose ends or applicable erasure requires it. Retain only necessary identifiers and evidence. | Backend and authorized operations; only curated lineage is exported, excluding unrelated content and sensitive integrity details. Global records without a verified subject key are excluded or partial. | C2.4 receipts are removed with invalidated state where applicable. Receipt classifications, conclusion timestamps and expiry coverage are not uniformly established; accounting is separate. | Inventory receipt types and personal content, verify conclusion timestamps and idempotency/replay requirements, implement bounded lifecycle and tests without indefinite content under an audit label. |
| Completed bookings and stays: `bookings`, stay and allocation records, `booking_confirmation_outbox` and relevant external sync | Booking administration and operational archive. Maximum 12 calendar months from completion; no raw conversation text in the archive and no indiscriminate health data. | Authorized tenant staff; curated organization and contact projections omit sensitive free text and selected identifiers. | Supported local erasure is partial where financial, suppression, audit or external copies remain. Calendar-month completion-based retention is not established across stores. | Identify completion timestamp and all dependent records, implement calendar-month expiry, preserve separately justified records and verify external reconciliation before GO. |
| Financial and required business records: `usage_events`, billing records, `processed_stripe_events`, provider accounting | Accounting and specific documented obligations. Duration depends on the particular category and obligation; no universal period is approved. | Restricted authorized finance/operations access; bounded projections omit unnecessary details. | Preserve necessary accounting and counters; retention must not hide usage or reset quotas. Legal basis, scope and lifecycle require documented verification. | Record each specific obligation, applicable period and minimum data; separate it technically from conversational data and verify applicable provider treatment. |
| Security, audit and suppression evidence: `audit_log`, GDPR operation events and steps, `gdpr_erasure_barriers`, conversation revocations and purge fences | Security, accountability and prevention of replay, limited to documented necessity. No universal security period is approved. | Restricted authorized staff and backend; curated exports omit secrets, digests and unreviewed details. | Evidence may need to survive content erasure, but is not a blanket indefinite-content exception. Existing fences and hold flags do not establish every evidence lifecycle. | Document purpose, minimum scope, retention/review and authority per evidence type; keep independent restore evidence and verify no stale content can be recreated. |
| GDPR export snapshots: `gdpr_operations`, encrypted `gdpr_export_chunks` | Fulfil a scoped rights request. Short-lived snapshots expire under the existing operational mechanism; their configured lifetime is not a substitute for product-category retention. | Owner with current MFA, matching organization, identity and session; curated scoped export can be partial. | Snapshot expiry/revocation removes chunks; local result does not certify provider or backup erasure. | Verify scheduling, access checks and partial-manifest handling; separately reconcile provider and backup copies. |
| Backups and restore evidence: database backup objects, independent erasure and purge journals | Recovery and continuity. Encrypted backups, restricted administrative access and ordinary maximum 30 days, except a specific documented obligation. | Restricted administrators; backups are not included in ordinary export. | Existing snapshots are not individually erased by the local operation. Restore must remain isolated and reconcile later erasures using evidence stored independently of the backup. | Verify actual backup inventory, encryption, access and rotation; authenticate complete independent journal, replay and barriers in isolated restore before reopening traffic. |
| External provider copies: Meta, AI providers and upstreams, infrastructure, Calendar, Stripe, email, monitoring and other enabled services | Only necessary approved service functions and documented obligations; provider-specific retention and roles require verification. | Governed by applicable verified agreements and access controls; external copies are not included in a local snapshot. | Local completion does not prove provider deletion; pending or uncertain external work remains visible and prevents an unsupported completion claim. | Verify actual providers, subprocessors, locations, transfers, contracts, retention, no unauthorized training and authenticated deletion evidence; unknown providers remain disabled or fail closed as required. |
| Simulator responses and dashboard proposals: `simulation_requests`, A04 response caches and worker-local copies | Authorized simulation and request replay. No simulator-specific duration was established by the V1 decision; applicable categorized content remains subject to its approved rules. | Tenant/account authorization; curated metadata, with unreviewed response content excluded and marked partial. | Subject lineage can be unknown; do not infer a contact from account identity or delete another subject's data. | Classify each copy and its subject linkage and timestamp, document applicable policy and verify worker-local handling; do not invent a simulator duration. |

Healthcare use remains restricted to administrative functions. Real health-sector
activation is blocked until the specific processing requirements and authorized
scope are verified; clinical or urgent content requires appropriate escalation.
Provider and contractual verification, effective policy records, C01 operational
cutover, backup/restore evidence and completion of the remaining A06 rights workflow
are objective gates. Product approval closes the five founder decisions; it does
not certify those gates or a completed purge, external erasure or restore.
