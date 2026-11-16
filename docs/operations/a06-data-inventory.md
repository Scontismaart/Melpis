# A06 data inventory and export projection

This inventory is a static review of `src/whatsapp/schema.sql`,
`src/core/db/schema.sql`, and data tables and lifecycle state introduced
through migrations 002–095. It describes SQL declarations
and checked-in code; it is not confirmation of the deployed database or of
external provider configuration. The export query allowlist is
[`src/core/gdpr/catalog.py`](../../src/core/gdpr/catalog.py). It contains one
frozen category per table, explicit selected columns, and no `SELECT *`.

## Scope and identity keys

**A06 status: technical partial.** The reviewed export catalog contains 60 unique
categories and 89 executable projections, including narrow subject views. This
is not a legal sign-off or issue closure. The five Privacy V1.0 product
decisions are founder-approved; effective tenant records and operational
cutover are not established by this source review. Shared authentication-account
disposition, subject-level review of unstructured content, verified external-
provider erasure, backup and other-worker-copy handling, and rollout evidence
remain open.

- **Organization** means the tenant UUID (`organization_id`). Tenant export
  queries bind it as `$1`; derived rows join through a declared organization
  or contact/booking/message relationship.
- **Customer subject** means a `contacts.id` within that organization. Subject
  queries bind it as `$2` and must retain the organization predicate. They use
  only the contact foreign key paths shown below.
- **Account subject** means the authenticated Supabase UUID
  (`auth_user_id`), distinct from a tenant and from a customer contact. Account
  queries bind it as `$2`; profile, membership, and invitation queries join to
  the matching profile identity.
- Free text may mention other people. Subject projections therefore omit
  unstructured message, review, booking, feedback, and error text. Those
  exclusions make the subject export partial and require a documented manual
  review/redaction path before claiming completeness.

## Joined policy and implementation matrix

This matrix groups the frozen export-catalog tables into personal-data
categories and joins their purpose, the approved V1 standard, access boundary,
export projection, and erasure/retention path. Table-level scope and columns
remain in the SQL data map below. “Not assigned” means the approved V1.0 policy
does not set a period for that category; it must not be guessed from a code
default. Effective tenant instructions, deployed grants, and scheduled runs
are separate evidence and are not certified by this source review.

For internal data, normal access is through authenticated, organization-scoped
application routes with their route-specific role checks. The GDPR export and
erasure request routes require an organization owner and MFA. The exact
deployed API database principal and effective grants remain open under [issue
#96](https://github.com/Scontismaart/Melpis/issues/96); `service_role` is not
itself an authorization boundary. Workers must use their dedicated capability
and tenant-scoped paths.

| Personal-data category and principal tables | Technical purpose | Approved V1 standard | Authorized access boundary | Export treatment | Erasure and operational evidence |
|---|---|---|---|---|---|
| Account, tenant, membership, and invitation data (`user_profiles`, `organizations`, `organization_memberships`, `team_invitations`, `oauth_nonces`) | Authenticate users, provision tenants, and enforce membership. | No single product period assigned; account disposition and tenant lifecycle are separate. | Account-scope lookup is bound to the authenticated account UUID; organization routes use role checks and tenant scope. GDPR requests require owner + MFA. Self-service account deletion is not verified. | Frozen account and tenant projections join on the authenticated account and organization; no cross-tenant lookup. | Membership removal is not deletion of a shared authentication account. Account closure and remaining tenant links need the authorized account-lifecycle process; deployed identity-provider cleanup is unverified. |
| Contact identity, channels, and consent (`contacts`, `contact_consent_log`, `whatsapp_accounts`, `instagram_accounts`) | Address service communications and preserve opt-in/opt-out state. | Message content follows the message standard below. No separate period is assigned to consent history or channel configuration. | Tenant roles permitted to manage the contact/channel; owner + MFA for rights operations. Effective access and revocation must be verified on the deployed API. | Exact-contact projections include scoped structured fields; matched consent text and access tokens are excluded. | Contact erasure uses verified same-tenant links and must preserve applicable consent/opt-out barriers. Token revocation at Meta or another channel is a separate provider action; no provider receipt is established here. |
| Message content and delivery copies (`messages`, `meta_webhook_inbox`, `event_log`, `outbound_dedup`, `message_delivery_attempts`, eligible simulator responses) | Receive, process, answer, and deliver customer conversations. | Ordinary availability: at most 60 days. Primary content removal: within 90 days. Earlier erasure applies when due. | Tenant roles authorized for conversation access through scoped routes; owner + MFA for rights operations. | Organization export includes supported message content. Subject export omits unstructured text where it may identify other people; that result is explicitly partial pending review. | The feature-flagged job requires a valid tenant policy. Main's source path still has gaps for soft-deleted content; open PR #186 adds a bounded, hold-aware source purge. Neither a deployed migration nor a live schedule is proven. Copies at Meta, providers, logs, and backups remain separate. |
| Structured booking conversation state (`booking_conversation_states`, `booking_conversation_requests`, `booking_conversation_revocations`, `c24_idle_watermarks`, `c24_source_fences`) | Maintain booking proposals, trusted source lineage, and replay/revocation barriers. | State and proposals: maximum 30 days from the last relevant activity. | Tenant roles permitted to manage bookings; owner + MFA for rights operations. C2.4 workers use the tenant-bound capability, not user-controlled scope. | Organization and exact-subject projections use verified tenant/conversation/contact links; unknown relations are excluded and marked partial. | Migrations 087/089 provide policy-, lineage-, hold-, and fence-aware cleanup. A real scheduled run, all legacy copies, and deployed operator identity are not verified. Replay barriers are retained only as separately classified technical receipts. |
| Technical receipts, idempotency, and suppression evidence (`booking_conversation_requests`, delivery receipts, `c24_suppression_lifecycle_audit`, webhook/idempotency records) | Prevent duplicate side effects, reconcile delivery, and prove operation lineage. | Personal technical receipts: maximum 90 days from conclusion. Financial/accounting evidence is a separate category and is not erased by this rule. | Tenant-scoped operations roles and the dedicated worker identity; owner + MFA for rights requests. Global provider keys without tenant lineage are not subject-exported. | Only the catalog's selected status, timestamps, and verified tenant/subject identifiers are included; raw keys, tokens, and unnecessary hashes are excluded. | SQL089 has bounded lifecycle cleanup for supported C2.4 receipts; other idempotency rows need their own verified lineage and schedule. Holds, uncertain work, and necessary replay tombstones remain bounded exceptions, not indefinite content stores. Live execution is unverified. |
| Booking and stay records (`bookings`, `booking_stays`, allocations, outbox, stay audit/tombstone/fence records) | Schedule and fulfill appointments or stays, allocate resources, and reconcile payment state. | Operational booking/stay archive: at most 12 calendar months from reliable conclusion. Raw conversational text is not part of the archive. Separate legal accounting records follow their own obligations. | Booking roles authorized by the tenant and organization-scoped workers; owner + MFA for rights operations. | Organization export uses explicit selected fields. Subject projections omit direct identifiers and free text unless the relationship is verified. | Migrations 091 and 093 contain guarded stay lifecycle controls, but tenant policy, worker scheduling, and provider reconciliation are not proven in production. Open PR #185 is a forward privacy fix for optional free-text notes; it does not erase historical provider copies. |
| Knowledge content and reviews (`documents`, `document_chunks`, `faq_cache`, `reviews`, onboarding/service configuration) | Provide tenant-configured retrieval, answer FAQs, and manage customer reviews/services. | No common product period assigned. Controller instructions must define a period for each purpose before real traffic; do not inherit the message period. | Tenant roles permitted to manage each feature; access to support/admin tools is separately restricted. Deployed grants remain unverified. | Tenant projections include only catalogued fields. Subject lookup is absent or narrow for many sources; unsupported relationships remain partial. | Tenant deletion or cache invalidation is not proof of subject-level erasure. No complete category schedule or external deletion receipt is certified. |
| Billing, provider accounting, and operational audit (`usage_events`, processed payment events, `audit_log`, security/worker traces) | Meter usage, reconcile financial activity, investigate security events, and preserve operational accountability. | No single period is assigned. Separate mandatory accounting from personal operational receipts; controller/counsel must classify uncovered logs and audit fields. | Authorized billing/organization owners and least-privilege operators; provider and security access is separately controlled. Effective production grants are unverified. | Export returns limited catalogued accounting/audit fields; arbitrary metadata and unnecessary trace text are excluded. | Never delete counters or unresolved side-effect records to disguise spend or enable replay. The approved 90-day technical-receipt period does not set financial-record retention. No complete audit-log schedule is established. |
| Integration credentials and configuration (`google_calendar_credentials`, `external_booking_credentials`, email and provider settings) | Connect tenant-authorized external systems. | No standard duration assigned; retain only while the integration is needed and its approved terms allow. | Tenant owner/admin through integration-management routes; runtime access is secret-scoped. Exact account and role settings are not verified. | Secret values and tokens are excluded; only safe provider/status metadata is projected. | Disable/revoke credentials locally and at the provider when the integration ends. Provider-side revocation receipts and a complete secret-erasure inventory are not available. |
| Export snapshots and request artifacts (`gdpr_operations`, encrypted export chunks, one-time download tokens) | Fulfill a data-access request and deliver its frozen export securely. | Snapshot lifetime: 15 minutes in code. This is an artifact TTL, not a general personal-data retention period. | Organization owner + MFA to request; session-bound, HttpOnly one-time download flow. | Snapshot manifest lists supported, excluded, and partial categories; export is not evidence about providers or backups. | Expiry removes encrypted chunks through the retention worker. Scheduling and failure monitoring are not verified. Redis/Valkey failure must fail closed; bearer tokens must not be placed in URLs or logs. |
| External provider copies and model/upstream data | Deliver messages, calendar/bookings, email, payments, or AI inference. | Provider contract, account controls, and route-specific periods are not verified; no Melpis duration is inferred for a provider copy. | Provider account administrators and provider runtime; no end-user role bypasses Melpis authorization. Actual accounts/upstreams remain unverified. | Only data sent to each identified provider can be requested from that provider. The active provider list and route must first be recorded. | No historical provider deletion is claimed. Provider-specific authorization, idempotency, and a correlated receipt are required; missing or uncertain evidence remains `UNKNOWN`/held. |
| Backups and restored copies | Recover service data after an incident. | Approved product maximum: 30 days, absent a separately documented legal obligation. | Named operators with restricted access and recoverable key custody; actual custodians and region are not proven. | Backup access is not a substitute for a subject export. | A verified isolated restore must replay independent erasure barriers before traffic resumes. No active schedule, recoverable Staging key, or completed restore drill is evidenced. |

The matrix records policy standards and checked-in behavior separately. It does
not certify that the tenant-specific policies are populated, a worker is
scheduled, a provider is active or authorized, or a request has completed.

## SQL data map

“Tenant projection” means a bounded category query is defined. “Subject
projection” names the contact/account-specific query; “none” means no verified
subject lookup was found or the data is intentionally excluded. “Delete basis”
records the code/schema path, not a claim that the deployed migration state was
verified.

| Table | Scope / subject key | Personal or operational data held | Export projection | Delete basis / notes |
|---|---|---|---|---|
| `organizations` | Tenant primary key `id` | Business name, plan and usage settings | Tenant fields; business profile and billing identifiers excluded | Tenant row deletion; cascades depend on migration 014 |
| `whatsapp_accounts` | `organization_id` | WhatsApp phone-number/WABA IDs, timestamps and access/verify tokens | Safe account IDs/timestamps only | Tenant deletion; access/verify tokens excluded |
| `contacts` | Tenant + customer `id` / phone | Phone, effective consent and disclosure timestamps | Tenant and exact-contact projection | Migration 014 contact delete; consent state is separate from historical log |
| `conversations` | Tenant; `contact_id` | Channel, status, staff assignment and timestamps | Tenant and contact join | Contact FK cascades; soft-delete propagation in migration 014 |
| `booking_conversation_revocations` | Tenant + conversation + original contact | Immutable revocation fence, subject identifiers, reason and timestamp | No contact/account content projection; lifecycle metadata requires review | Prevents old A04 evidence from recreating revoked state. Retention, legal basis, subject export and eventual disposition remain open controller decisions. |
| `c24_idle_watermarks` | Tenant + conversation + original contact | Expired conversation generation, cutoff and provider timestamp | Tenant and exact-contact projection; no account projection | Migration 087 permits removal only after approved technical-receipt retention (maximum 90 days), no active state/receipt/unpurged source, and no applicable hold; message tombstones still reject replay. |
| `c24_source_fences` | Tenant + conversation + original contact | Request/source identifiers and fence time | Tenant and exact-contact projection; no account projection | Migration 087 removes only after request receipt is gone and source remains as content-purged tombstone; erasure additionally requires validated A06 barrier and redacted subject. |
| `c24_suppression_lifecycle_audit` | Tenant + original contact/conversation | Cleanup kind/action, opaque digest, operation correlation and timestamp | Tenant and exact-contact projection; raw source/operation identifiers excluded | Migration 087 appends audit atomically; policy-gated cleanup respects organization, subject, conversation and record holds and expires it under approved technical-receipt retention (maximum 90 days). |
| `messages` | Tenant; conversation → contact | Message text, delivery status and timestamps; JSON payload may contain more | Tenant projection includes `content_text`; subject projection returns structure and timestamps only | Conversation FK cascades; retention worker handles messages |
| `message_delivery_attempts` | Message → conversation → contact | Retry state and error JSON | Safe retry fields; error JSON excluded | FK to message; no separate retention policy found |
| `contact_consent_log` | Contact; optional triggering message | Opt-in/out event, method and matched text | Event fields; matched free text excluded | Contact delete cascades; preserve event semantics in lifecycle service |
| `whatsapp_templates` | Tenant | Template identifiers/status and arbitrary component JSON | Metadata only; component JSON excluded | Tenant cascade |
| `bookings` | Tenant; optional `contact_id` | Customer name/phone, dates, party size, notes, deposit/payment state | Tenant includes explicitly selected booking fields; subject view excludes names, phone, notes, URLs and provider session IDs | Migration 014 masks name/phone and nulls contact on contact delete; tenant delete cascades |
| `booking_settings` | Tenant | Capacity and schedule configuration | Identifiers and slot length only; config JSON excluded | Tenant cascade |
| `reviews` | Tenant; optional `contact_id` | Review text, author, rating and generated response | Tenant projection includes reviewed content; subject view includes rating/source/time only | Migration 014 masks author and nulls contact; no separate retention found |
| `documents` | Tenant; document ID | Names, import state, source and JSON metadata | Metadata fields only; arbitrary metadata excluded | Tenant cascade; subject linkage not established |
| `document_chunks` | Tenant; document FK | Extracted chunk text, embedding and metadata | Chunk text is included in tenant export; embedding/metadata excluded | Document FK cascades; no verified subject key |
| `email_configs` | Tenant | Configured email address | Explicit address/status fields | Tenant cascade |
| `usage_events` | Tenant | Usage and provider accounting in JSON metadata | Selected scalar accounting keys only | Tenant cascade; provider attempts cascade from usage row |
| `event_log` | Tenant | Event classification plus original text, AI response and details JSON | Classification/time fields only; text and JSON excluded | Tenant cascade; no separate retention found |
| `simulation_requests` | Tenant + account UUID + request ID | Payload hash, claim token, status and cached response JSON | Status/timestamps only; account view filters `auth_user_id` | Tenant cascade; retention worker purges after configured code value |
| `user_profiles` | Account UUID; tenant membership join | Email and display name | Org membership join and own-account query | Account deletion/lifecycle is separate; tenant deletion removes membership, not necessarily account |
| `organization_memberships` | Tenant + profile/account | Role and membership timestamps | Tenant membership/profile view; own-account view | Tenant/account FK cascades per migration 002 |
| `audit_log` | Tenant; account actor UUID/text | Action and target identifiers; details JSON | Explicit action/target/time only | Tenant cascade can remove deletion evidence; details excluded |
| `processed_stripe_events` | Tenant | Stripe event ID and processing timestamp | Event ID/time | Tenant cascade |
| `webhook_idempotency` | Global WAM ID, no organization FK | Webhook deduplication keys | Excluded; no safe tenant/subject predicate | Global retention cleanup exists; subject deletion cannot be inferred from schema |
| `google_calendar_credentials` | Tenant | Calendar ID, sync state/timestamps and access/refresh tokens | Calendar ID and sync state/timestamps only | Tenant cascade; tokens and token expiry excluded |
| `oauth_nonces` | Tenant/account flow | One-time OAuth nonce | Excluded | Security nonce; never exported |
| `google_business_credentials` | Tenant | Account/location labels, sync state and OAuth/review-page tokens | Account/location labels and sync timestamps only | Tenant cascade; tokens and token expiry excluded |
| `onboarding_profiles` | Tenant | Business profile, services, schedules and escalation settings | Limited identifiers/status/language fields; free-form profile/config excluded | Tenant cascade |
| `instagram_accounts` | Tenant | Instagram account ID and access token | Account ID and timestamps only | Tenant cascade; access token excluded |
| `faq_cache` | Tenant | FAQ question/answer text and embeddings | Question, answer, prompt variant and timestamps; vector/hash excluded | Tenant invalidation helper exists; no retention policy found |
| `message_feedback` | Tenant; message/conversation → contact | Feedback value/source and creator account | Explicit feedback fields; contact projection joins conversation | Tenant cascade; no separate retention found |
| `weekly_report_log` | Tenant | Reporting dates, recipients and status | Dates/status only; recipient addresses and error reason excluded | Tenant cascade; no retention policy found |
| `outbound_dedup` | Tenant; message → conversation → contact | Cached response text | Message ID/time only; response text excluded | Tenant cascade; message FK path supports contact filtering |
| `external_booking_credentials` | Tenant | Provider name, active flag, encrypted credentials and config JSON | Provider name, active flag and timestamps only | Tenant cascade; credentials/config excluded |
| `external_booking_sync` | Tenant; internal booking → contact | External IDs, sync status/error and retry state | Explicit provider/status/IDs/retry fields; free-form error excluded | Tenant cascade; contact query joins internal booking |
| `airtable_connections` | Tenant | Base ID/name, scopes, encrypted credentials and metadata | Base ID/name, token type, scopes, active flag and timestamps | Tenant cascade; credentials and arbitrary metadata excluded |
| `airtable_field_mappings` | Tenant | Field mapping/config JSON | Excluded | Tenant cascade; arbitrary mapping excluded |
| `airtable_webhooks` | Tenant | Base/webhook IDs, cursor, active flag, MAC secret, notification URL and specification | Base/webhook IDs, cursor, active flag and timestamps | Tenant cascade; MAC secret, notification URL and arbitrary specification excluded |
| `airtable_webhook_events` | Tenant | Raw external payload, event metadata and errors | Event type/status/time only | Tenant cascade; payload and error text excluded |
| `meta_webhook_inbox` | Tenant | Raw webhook payload, event/trace/lease data | Channel/status/attempt/time only | Tenant cascade; payload and tokens excluded |
| `billing_checkout_intents` | Tenant | Request/payload data and checkout session URL | ID and timestamps only | Tenant cascade; payload, hash and URL excluded |
| `governance_outbox` | Tenant | Audit/usage event payload JSON | Event kind and time only | Tenant cascade; payload excluded |
| `team_invitations` | Tenant; invitation email; inviter account | Invite email, role, token hash and lifecycle timestamps | Email/role/timestamps; token hash and inviter ID excluded; account view joins exact profile email | Tenant cascade; no separate invitation retention period established |
| `auth_access_lifecycle` | Account UUID, no tenant key | Provisioning/disabled timestamps | Own-account query only | FK to auth user cascades; not part of tenant export |
| `provider_attempt_runtime` | Tenant; composite FK to usage event | Worker/lease state and safe provider IDs | Explicit runtime fields; join confirms org attribution | Migration 061 cascades from usage event; no customer text per design |
| `provider_attempt_evidence` | Tenant; composite FK to usage event | State transitions; details JSON | Event/epoch/time only; details JSON excluded | Migration 061 cascades from usage event; documented as non-content evidence |
| `booking_confirmation_outbox` | Tenant; booking and two message FKs | Confirmation state and scheduling timestamps | IDs/status/time; subject query joins booking contact | Migration 062 FKs use default NO ACTION; remove/reconcile dependents before individual booking/message deletes |
| `gdpr_operations` | Tenant; contact subject or requesting account | Lifecycle status, scope, subject ID and operation timestamps | Safe lifecycle fields; contact requires exact contact scope/ID; account requires exact requester | Migration 063; request identifiers/hashes, manifest and session fields excluded |
| `gdpr_operation_steps` | Tenant; operation → contact/requester | Step status, attempts, fence and update time; evidence/lease fields | Joined to tenant operation; subject scopes follow operation subject/requester | Migration 063; step evidence JSON and lease data excluded |
| `gdpr_operation_events` | Tenant; operation → contact/requester | Event type/time and arbitrary details JSON | Joined to tenant operation; subject scopes follow operation subject/requester | Migration 063; arbitrary details JSON excluded |
| `gdpr_policies` | Tenant policy | Category/version, approval reference, dates, hold and retention days | Organization-only safe policy fields | Migration 063; approver identity and policy internals excluded |
| `gdpr_erasure_barriers` | Tenant; explicitly scoped contact subject | Scope, subject ID, operation and creation time; digest/key ID | Tenant projection; contact projection requires contact scope and exact subject ID | Migration 063; digest and key ID excluded; no account projection |
| `gdpr_export_chunks` | Tenant; operation | Encrypted serialized export chunks and digests | Excluded; recursive export artifact | Migration 063; never included in an export |

## Lifecycle and worker data added after migration 087

These records are not additional categories in the frozen export catalog. The
catalog remains at 60 categories / 89 projections; absence from a projection
does not mean the records are outside a subject-rights review. The controls,
lineage, and short-lived capabilities below have separate access paths and
retention needs.

| Migration / record | Scope and data | Export / erasure treatment | Retention evidence and current limit |
|---|---|---|---|
| 088 `google_calendar_credentials.refresh_dispatch_id` | Tenant-scoped dispatch correlation UUID on an existing credential row; the existing encrypted credential remains secret material. | No new export field; OAuth tokens and credentials stay excluded. Tenant/contact erasure flow does not prove remote Google token revocation. | Dispatch marker is operational state. Actual credential deletion/revocation and Google-side handling require the authorized account workflow and provider evidence. |
| 089 `c24_idle_copy_cleanup_capability`, `c24_inbox_cleanup_capability`, `c24_retention_sweep_cursor`; inbox lifecycle columns | Short-lived cleanup authorization markers and a platform-wide keyset cursor, plus completion/purge timestamps on webhook inbox rows. The cursor contains scan position only; inbox rows can contain raw webhook payload before purge. | Capability/cursor rows are excluded from the export projection. Inbox payload is excluded from export; supported erasure/retention paths depend on current tenant policy and the C2.4 owner sweep. | Migration 089 provides bounded, cursor-backed C2.4 and hotel sweeps. They require the dedicated least-privilege role, tenant policy, no legal hold, and explicit flags; code presence does not prove scheduler or DSN configuration. |
| 090 `booking_sector_admission_fences` | Tenant-scoped sector admission/control metadata. A sector label can reveal service affinity in a health-related deployment. | Not in the frozen export projection; no subject lookup is certified. Review the deployment's sector values and applicable subject/health-data scope before external claims. | Admission state is a safety control. No independent retention schedule or deployed-state verification is established here. |
| 091 `booking_stay_owner_markers` and `booking_stays` lifecycle fields | Transaction-bound owner marker carries organization, booking, subject, request, actor, and intended action identifiers; existing stay records gain source/lifecycle state. | Marker is private and excluded from the catalog; stay content and owner source data are governed by the booking/stay request path and erasure fences. | Marker is transaction-scoped authorization evidence. Durable stay completion, hold, and purge eligibility rely on database lifecycle rules and an approved `booking_stay_content` policy; no schedule execution is certified. |
| 092 calendar inbound source correlation | A database function attaches an inbound source to a tenant-scoped calendar operation; it adds correlation, not a new export category. | No new export projection. Google account/event copies and calendar deletion receipts remain external stages. | Correlation supports idempotency and lineage; it is not proof of calendar erasure or a retention schedule. |
| 093 `c24_private.worker_capabilities` and `worker_context` | Capability rows include organization, source/claim/generation/booking IDs and a JSON payload. Source review found customer name and phone, booking details, and confirmation recipient/text in that payload. Context rows hold tenant and job lineage IDs, claim, generation, and lease timestamps. | Neither private table is in the export catalog. Migration 094 is merged (PR #179, commit `f5f5040`) and provides bounded payload deletion while retaining the replay tombstone. Subject-scope treatment, deployment, and backup/cache cleanup remain unverified, so A06 stays partial. Worker context is transaction-bound and removed on successful finish; this does not establish cache/backup cleanup. | Capability authority expires within a maximum five-minute window, but expiry alone is not deletion. Migration 094 and its PostgreSQL-only cleanup function are source/CI verified, including fresh ordered application through 094 in migration CI. No Staging/Production application or scheduled execution is certified. Capability hashes and replay tombstones remain. |

The original SQL data map above covers the application tables and C2.4 lifecycle
metadata added through migration 087. The supplemental section above records the
later lifecycle and worker records. The frozen catalog contains 89
executable organization/contact/account projections across 60 categories,
including narrow C2.4 projections; `gdpr_export_chunks` remains excluded
because it stores encrypted recursive export artifacts. Lifecycle event and
step evidence JSON, operation manifests, request hashes, session data, and
erasure barrier digests are excluded. These lifecycle records remain governed
by their own access and retention controls.
The contact/account views are intentionally narrower than tenant
views. Unstructured columns omitted from subject exports are not silently
treated as erased or anonymous; they need manual review or a later subject
indexing policy.

## Retention and deletion evidence

- `src/core/retention_job.py` always expires encrypted GDPR snapshots. Primary
  data mutation is separately gated by `GDPR_RETENTION_EXECUTION_ENABLED` and
  valid per-tenant policy records. The route `/api/gdpr/retention-policy`
  reports `policy_version=melpis-privacy-v1.0` with approved product maxima.
  Legacy effective-retention fields remain `null`; the legacy execution
  boolean reports only an environment flag, not operational authorization. Its
  `simulator_history_idle_hours=24` value is not a retention period. The legacy
  `organizations.message_retention_days` column is not approval evidence.
- `src/core/db/migrations/014_contact_fk_strategy.sql` specifies the contact
  cascade/masking rules and dynamically changes direct organization foreign
  keys to cascade. This catalog reflects the checked-in migration intent; a
  live migration audit is needed to confirm what is installed.
- `src/core/gdpr/routes.py` exposes legacy organization export plus scoped
  export, durable erasure, operation status/retry, and one-time download
  routes. The frozen catalog is the reviewed projection source. Subject export
  is partial where no verified subject key exists or free text is excluded.
  Erasure returns a durable blocked/partial status and does not physically
  cascade-delete the organization.
- The legacy Airtable/Softr propagation functions do not make HTTP requests
  and cannot confirm deletion. Real provider erasure, worker-local copies,
  backups, shared authentication accounts, and unstructured subject data remain
  unresolved stages; no external completion should be claimed.
- `scripts/backup_supabase_r2.sh` creates encrypted database backups and has a
  seven-day object cleanup value in the script. This does not erase a subject
  from existing snapshots; actual object-store lifecycle and restore-time
  re-erasure must be confirmed operationally. No other persistent document
  object store was confirmed in this static review.
- Do not infer legal retention periods from code defaults, examples, or the
  DPA technical template. The founder-approved standards are recorded in
  [Privacy V1.0](../governance/privacy-data-governance-v1.0.md); compatible tenant
  records, implemented lifecycle controls and operational GO remain necessary. The DPA content is a technical template and does not constitute
  executed legal approval.

## Request, export, and erasure path

| Stage | Implemented path | Failure, scope, and evidence limit |
|---|---|---|
| Request and authorization | GDPR request routes require an authenticated organization owner and current MFA (`aal2`). A durable `request_id` is idempotent within the organization/requester; contact IDs are looked up inside the selected tenant. | Invalid or absent identity/current membership is rejected. Shared authentication-account erasure is held for transfer/identity review. This source review does not certify deployed authorization or a completed customer request. |
| Export construction | The frozen allowlist uses explicit projections and tenant predicates. Durable export chunks are encrypted in PostgreSQL, streamed in bounded chunks, and downloaded through a short-lived one-use same-origin ticket with Redis and the export encryption key required. | Missing Redis/key/session or an invalid/expired ticket fails closed. The manifest identifies excluded/partial categories; free-text subject records and categories without verified subject keys require manual review. External copies and backups are outside the snapshot. |
| Internal erasure | A durable operation records status and steps; supported tenant/contact flows use a database erasure barrier and scoped relational redaction/deletion after valid policy, approval, cutover, and writer-drain gates. | Missing, expired, held, unsupported, or superseded policy stops mutation. Unknown subject links, active bookings, uncertain provider attempts, shared accounts, billing/audit records, and unsupported tables can keep the operation partial or blocked. An organization is not physically deleted in one synchronous action. |
| Provider propagation | A separately enabled stage requires a configured adapter and operation-correlated authenticated deletion receipt. | A transport error, bare HTTP success, or uncorrelated provider response is uncertain and does not prove deletion. Airtable/Softr helper paths are non-network stubs in the reviewed source. Provider-side copies remain unverified. |
| Backups and worker copies | Export snapshot expiry removes encrypted export chunks; the C2.4 worker has separate fenced lifecycle paths. Backup and worker-local copies are tracked as separate dependencies. | The repository does not establish live backup objects, a running schedule, cache cleanup, or restore-time replay of newer erasure barriers. Do not delete backups as a shortcut or report erasure complete while required dependencies remain open. |

Keep the request and operation IDs in the approved support record, without
copying customer text, tokens, DSNs, or provider secrets. The detailed operator
sequence and cutover holds are in the
[A06 runbook](a06-gdpr-runbook.md).

## External providers and unresolved evidence

The embedded DPA and customer-facing privacy documentation mention Meta,
configured LLM providers, Google, Stripe, Supabase, optional Sentry/SMTP, and
Airtable/Softr propagation. They do not establish the deployed subprocessor
set, processing locations, transfer mechanism, retention configuration, or
contractual approval. Confirm those facts against executed DPAs and service
configuration before publishing claims. No provider payloads or credentials
are included in the export catalog.

## Export exclusions requiring an explicit response

- Credentials, access/refresh tokens, webhook secrets, OAuth nonces, invitation
  token hashes, checkout session URLs, and provider session IDs.
- Unreviewed JSON/arrays, including arbitrary message payloads, document
  metadata, settings, event details, webhook payloads, outbox payloads,
  checkout payloads, and provider evidence details.
- Subject-level free text that may identify third parties: message bodies,
  booking names/phones/notes, review text/authors, consent matched text, and
  provider error text. Tenant exports include selected message/document/review
  text where specified, because the controller export is organization-wide.
- Global webhook idempotency records without an organization key and data
  without a verified contact/account join.

These are deliberate omissions in the current projection, not a legal
determination that the data is outside a data subject's rights. The service
response must identify partial categories and offer the approved manual
review/redaction process until the unresolved scope is approved.
