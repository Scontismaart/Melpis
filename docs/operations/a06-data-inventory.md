# A06 data inventory and export projection

This inventory is a static review of `src/whatsapp/schema.sql`,
`src/core/db/schema.sql`, and data tables introduced through migrations 002–063. It describes SQL declarations
and checked-in code; it is not confirmation of the deployed database or of
external provider configuration. The export query allowlist is
[`src/core/gdpr/catalog.py`](../../src/core/gdpr/catalog.py). It contains one
frozen category per table, explicit selected columns, and no `SELECT *`.

## Scope and identity keys

**A06 status: technical partial.** The catalog contains 51 unique categories
and 70 executable projections, including narrow subject views. This is not a
legal sign-off or issue closure. C05 policy/legal approval and C01 operational
cutover remain pending, as do shared authentication-account disposition,
subject-level review of unstructured content, verified external-provider
erasure, backup and other-worker-copy handling, and rollout evidence.

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

The 51-table inventory above covers application data through migration 062
and the six lifecycle tables added by migration 063. The catalog contains 70
executable organization/contact/account projections across its 51 categories,
including safe
projections for five lifecycle tables; `gdpr_export_chunks` remains excluded
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
  reports `policy_version=tenant-approval-required`; retention-day fields are
  `null`, and automatic erasure reflects the execution flag. Its
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
  DPA technical template. C05 legal/controller policy approval and C01 cutover
  remain pending; no approved retention schedule is established by this
  inventory. The DPA content is a technical template and does not constitute
  executed legal approval.

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
