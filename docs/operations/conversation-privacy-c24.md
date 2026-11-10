# C2.4 conversation privacy decisions

This feature remains dark until the controller approves the legal and operational
policy and the restore procedure. Identifiers, hashes, provenance, dates and
administrative booking details are linkable personal data; a medical appointment
may also be sensitive. The table describes required handling, not legal approval.

| Copy or evidence | Export | Erasure and retention | Open decision |
| --- | --- | --- | --- |
| Current conversation snapshot and field provenance | Curated organization and original-subject projection, tenant scoped | Privacy V1.0 maximum 30 days from relevant activity; invalidate on source/consent revocation and honor holds | Tenant policy records and operational cutover remain unverified |
| Immutable request receipts, hashes, actor and source references | Curated lineage without unrelated content | Privacy V1.0 maximum 90 days from request conclusion; cleanup is owner-only and policy/hold gated | Tenant technical-receipt policy records and operational cutover remain unverified |
| Canonical A04 message cache and simulation response | Existing curated catalog, avoid exposing raw proposal or third-party text | Existing source and simulator policies must cover these copies; coordinate with the conversation policy | Retention and erasure approval for proposal-bearing caches and simulations |
| Source and confirmation messages, superseded summaries | Existing scoped message projection and curated lineage | Source redaction invalidates the whole derived state, including receipts; do not reconstruct from stale evidence | Source and proof retention, legal holds and safe rebuild rules |
| Usage counters, billing and audit | Existing bounded catalog | Preserve only as authorized by applicable policies; do not equate preserved accounting with preserved content | Lawful retention and minimization of linkable metadata |
| Encrypted exports and external provider copies | Snapshot excludes external copies and backups | Expire/revoke exports; track external deletion separately with verified receipts | Provider inventory, processor commitments and completion evidence |
| Backups and restored databases | Not included in normal export | Quarantine restoration and reconcile newer erasure barriers before serving traffic; fail closed if authoritative reconciliation is unavailable | Backup expiry, barrier authority, reconciliation proof and recovery ownership |

Contact and organization erasure must remain blocked until the applicable
`booking_conversation_content` policy is approved along with existing required
policies. A local partial result does not certify external copies or backups.
Retention values in synthetic tests are fixtures and are not production policy.
Consent withdrawal is an invalidation event; restoring consent must not restore
previously removed state. Coordinate ongoing writes with erasure and retention,
and verify tenant isolation and original subject lineage before any reprocessing.

## Technical contract and decisions reserved to the controller

Privacy V1.0 approves maxima of 30 days for structured conversation state and
90 days from conclusion for technical receipts. Each tenant still needs a valid
approved policy row, purpose, lawful basis, hold review and operational cutover;
these values are not seeded as legal approval. Destructive execution flags remain
disabled.

| Category | Purpose / necessity | Original subject and sources | Access | Export | Erasure | Proposed retention / hold | Consent withdrawal | Restored copies |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| State, typed administrative slots, revisions, corrections and snapshots | Continue one request without inventing details or repeating AI | Immutable state subject; every source/proof is a scoped message with original-subject evidence | Current authorized owner/manager, never an AI-supplied actor | Curated tenant/contact projections; account content excluded | Whole derived state and receipts invalidated atomically | Unset; controller must decide hold behavior | State removed and durable replay fence retained, never restored by reconsent | Remain quarantined until independently authenticated post-backup journal replay |
| Source and prompt messages, provenance IDs | Prove the exact request and subsequent customer clarification | Original subject captured by server on message insertion; historical NULL evidence is ineligible, never backfilled from today's contact | Existing message authorization plus current lineage checks | Existing bounded message projections; free text excluded for individual export | Existing approved source policy plus derived-copy invalidation | Unset for new metadata; source-policy compatibility requires review | New state production denied; original messages remain subject to their own approved policy | Message content and proof caches must be redacted again before verification |
| Request receipts, actor IDs, UUIDs and integrity hashes | CAS and replay without duplicate processing | State FK and scoped source/proof links; actor is staff, not customer | Backend repository, no direct client or worker write privileges | Individual receipt projection excludes staff identity and integrity hashes | Deleted with state; billing ledger is separate | Unset; no perpetual immutable-content exception assumed | Prior receipt cannot rebuild deleted state | Receipt absence must be verified, not inferred from a cascade definition |
| A04 cached responses and inbound simulation copies | Canonical reply replay without a second generation/charge | Inbound response stores immutable original subject, conversation and source UUID independently of source survival | Existing A04 permissions; read proof remains fail-closed | Curated metadata; unreviewed response content explicitly excluded | Contact erasure addresses the durable subject even after physical source deletion; unknown historical proposal copies block completion | Configurable conversation policy; no product default; individual source/simulator policies also remain applicable | Remaining canonical copies cannot bypass the conversation fence | Unknown/orphan proposal-bearing copies prevent contact certification |
| Dashboard simulation proposals | Synthetic administrative exploration | Authenticated account relationship only; no invented contact mapping | Existing tenant/account authorization | Existing curated account metadata; content excluded and marked partial | Organization lifecycle; contact attribution uncertainty blocks rather than deleting another subject's data | Unset; account/shared-account review remains open | No inference of contact rights from an account ID | External checkpoint cannot classify unknown historical copies automatically |
| `booking_conversation_revocations` | Prevent retries or stale canonical copies recreating removed state | Scoped original subject and conversation; no message body or clinical text | Backend read only; trigger-controlled insert, no arbitrary DELETE capability | Curated original-subject projection | Still retained by existing immutable contract; migration 087 does not remove this table's row | No lifecycle cleanup has been certified for this separate fence | Reconsent does not clear the fence | Remains an explicit C2.4 privacy/cutover gate |
| Suppression lifecycle audit | Verify fence/watermark cleanup without exposing source IDs or customer text | Opaque digest plus exact tenant/contact/conversation linkage held privately for hold checks; operation ID only for validated erasure | Backend-only; append-only, then policy-gated expiry at the maximum 90-day technical-receipt period; organization, subject, conversation and record holds block expiry | Tenant/contact exports contain bounded digest/action/kind/time fields; source and operation IDs are omitted |
| Usage, booking/allocation/outbox references | Preserve commercial idempotency and actual side-effect evidence | Existing scoped ledger contracts; no new operational C24 writer connected by PR1 | Existing permissions only | Existing catalogue projections; new writers need their own lifecycle review | No A04 counter reset; external/financial retention is explicit and separate | Existing approved policies only; new linkable fields require review | Cannot authorize a booking from stale state | All operational queues remain quarantined until their own reconciliation |

SQL070 does not certify historical subject attribution. It captures evidence for
new messages, rejects adopting historical NULL evidence and preserves this as a
C01 cutover gate. Changing a conversation's contact after messages exist is
rejected. A06 contact erasure fails closed when a proposal-bearing historical
simulation has no provable contact lineage. It does not erase another subject's
data to make a manifest look complete.

`verify_restore_checkpoint` is a dormant primary-C2.4 verifier, not a restore
service or traffic switch. Its key, backup identity, complete signed journal and
minimum sequence must come from independently controlled administration. Missing,
tampered or stale evidence, missing barriers, restored content and unknown copies
are rejected. A successful result still says `traffic_authorized=false` and does
not certify provider copies, backup expiry, shared accounts or global GDPR
completion. The external checkpoint authority and quarantine/replay procedure
require operational approval; no service or storage was purchased.

## Dark stay and administrative purge categories (unapproved)

The writer draft adds source-proven arrival/departure dates, original subject and
source IDs, stay revisions, operation-key tombstones, booking purge fences and a
private administrator audit. These remain personal data. All retention periods,
lawful bases, holds and backup treatment remain **unset/unapproved**.

Stay dates and curated fence/tombstone evidence are tenant/contact-exportable by
immutable original subject, never by a reassigned current contact. Source IDs
and staff/transaction identifiers are excluded from individual projections;
private audit subject evidence is represented by the correlated fence projection.
An approved inactive/released stay may be removed only through the owner-only
administrative purge, preserving financial booking and billing records; erasure
remains partial while retained financial, audit, suppression or external copies
await their approved lifecycle. Marker rows have no committed lifetime outside
the purge transaction; WAL/backups still require separate controller review.

Administrative retention additionally requires a current non-held policy with an
explicit approved duration; no runtime duration is seeded and no application
worker is granted purge authority. Durable request/booking tombstones prevent
retry or raw legacy writes from recreating purged data. Their own minimization,
hold, retention and eventual disposal must be approved before deployment.

Restoration requires independently authenticated v2 purge journals and exact
fence/tombstone reconciliation. The verifier always leaves traffic unauthorized.
C01 must also certify actual administrator/application role separation; code ACL
tests cannot establish the credentials or permissions in any remote environment.
