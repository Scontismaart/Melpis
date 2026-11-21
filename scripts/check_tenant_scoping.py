#!/usr/bin/env python3
"""Check CI anti-bypass del tenant scoping (Invariante #1: Tenant Isolation).

Scansione statica (AST) dei moduli repository e dei moduli applicativi che
eseguono SQL: richiede un predicato organization_id legato a un parametro
oppure organization_id tra le colonne di INSERT. Questo controlla la forma
del SQL, non dimostra che il parametro corrisponda all'organizzazione
autenticata: l'autorizzazione resta responsabilita' del codice applicativo.

Uso:
    python scripts/check_tenant_scoping.py [file ...]

Senza argomenti controlla DEFAULT_TARGETS. Exit 0 = OK, exit 1 = violazioni.
"""

import ast
import hashlib
import re
import sys
from pathlib import Path
from string import Formatter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.db.scoping import (
    INDIRECT_SCOPED_TABLES,
    TENANT_SCOPED_TABLES,
    extract_tables,
    has_non_dml_operation,
    has_explicit_org_scope,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# Keep the gate aligned with the current source tree so newly-added SQL-bearing
# modules cannot silently fall outside a hand-maintained inventory.
DEFAULT_TARGETS = sorted(
    path.relative_to(REPO_ROOT).as_posix()
    for path in (REPO_ROOT / "src").rglob("*.py")
)

# A query exception is keyed by function and normalized-literal SHA-256.
# It can never exempt another SQL literal in the same function. The hash is
# surfaced by the checker when a new exact exception needs review.
ALLOWLISTED_SQL_FINGERPRINTS: dict[tuple[str, str], str] = {
    # These exact query literals interpolate only quarantine_predicate's two
    # schema-compatible outcomes (TRUE or the protected, tenant-bound ID test).
    # Relation/ID column pairs are enumerated by that helper, not user inputs.
    (
        "src/api/routes/dashboard.py::recupera_eventi_dashboard",
        "5791b6719f3b4040cf1c9ec9475d7ca8d40f510641c1612b3a18ae9b323a0fe9",
    ): "tenant-bounded dashboard event projection excludes exact held booking sources and linked outbound confirmations",
    (
        "src/api/routes/dashboard.py::recupera_eventi_prioritari",
        "c9f87e1a4a6fd78d0b44a041b83c347e56a29cb18e8ae3b6ca2ed27f278f3370",
    ): "tenant-bounded priority event projection excludes exact held booking sources and linked outbound confirmations",
    (
        "src/core/db/repositories/conversation_repo.py::list_tickets",
        "0a1a84daf70d1d2dcb2cf67b76a5535ed6af6d9329810f3aefefb33b2a1fddcc",
    ): "tenant ticket preview uses fixed exact confirmation visibility predicate",
    (
        "src/core/db/repositories/conversation_repo.py::get_conversation",
        "90f2fa2b103603b4e035c89d38f0bc099ee5a6ca4f7a93ceef653b570d327173",
    ): "tenant conversation preview uses fixed exact confirmation visibility predicate",
    (
        "src/core/db/repositories/conversation_repo.py::list_conversation_messages",
        "c64126a73b2241649c49dc9d113a100d561d47478b4726601c8ce3bf47c63f1c",
    ): "tenant conversation history uses fixed exact confirmation visibility predicate",
    (
        "src/core/db/repositories/message_repo.py::get_last_ai_outbound_message",
        "398b5646258a7f26183c9fbd6765286a286edda63e791788431377eea26b3e15",
    ): "tenant latest outbound excludes exact held confirmation",
    (
        "src/core/db/repositories/message_repo.py::get_message_org_scoped",
        "e934b53cf9312b0102419b7b56cb76f7881d1e45b151182a2f110185b714961c",
    ): "tenant message detail excludes exact held confirmation",
    (
        "src/core/db/repositories/message_repo.py::get_messages_by_org",
        "e8f944e06ea526b50cb723dafd8270425126a4e76b4a9c7456045f716b561813",
    ): "tenant message list excludes exact held confirmation",
    (
        "src/core/bookings/routes.py::booking_c24_context",
        "6da9a388757ad0852e8702bb86c53f0ec2884566e7fa1043278a17b2f3c0146b",
    ): "exact same-tenant booking and allocation context visibility",
    (
        "src/core/calendar/repository.py::claim",
        "2feee09f853ad50379428944457c300ec8d20767d02bbdf7b84f05cf13e959d6",
    ): "bounded system claim excluding quarantined exact booking IDs",
    (
        "src/core/calendar/repository.py::current",
        "c7a52d42e86a3547995feca3b1aa200ec94ab34242bb0a35c384eeb683e81fa7",
    ): "tenant and lease ownership check with booking visibility",
    (
        "src/core/calendar/repository.py::dispatch",
        "8f5212c586140acecf760d235b163056ec87d091c1ce343c5ce7ef77340aa696",
    ): "tenant, lease and revision checked dispatch with booking visibility",
    (
        "src/core/db/repositories/booking_repo.py::get_booking",
        "c0f2a177734597284c25584e5142b3e3e8fe3768bd7e78dbe0ea2ec84c9cf525",
    ): "exact tenant booking detail visibility",
    (
        "src/core/db/repositories/booking_repo.py::list_bookings",
        "b54fd1d1cd325ec6d4e7c2a4b1d9a4aa63655298b6bade9b2fb66391ec64cfa9",
    ): "tenant/date booking list visibility",
    (
        "src/core/db/repositories/booking_repo.py::list_bookings",
        "f6998ac4715b1cf1bf8d955d9e09966ebbbf456ff446e1bf621d969dfc6785d3",
    ): "tenant booking list visibility",
    (
        "src/core/db/repositories/booking_repo.py::list_bookings_by_stato",
        "1740a36767cd3502babbe1488c1796cdb795901b5543ba39e29dbdc16e832b58",
    ): "tenant booking status list visibility",
    (
        "src/core/db/repositories/booking_repo.py::list_bookings_for_reminder",
        "1db2f2bd0b6ff2023830054b90a5faa1b3b0b21269db908d40c4d77755a1f686",
    ): "tenant reminder eligible list visibility",
    (
        "src/core/db/repositories/booking_repo.py::list_pending_reminders_for_phone",
        "8b026550a309bb79530f57469c0b0e679e7860b5d254dc40e573309084e5d8b3",
    ): "tenant pending reminder visibility",
    (
        "src/core/db/repositories/booking_repo.py::list_reminder_replies_for_phone",
        "a154c91239cc8234a9209b721c2b4d81d195f2ed56869cd5efc268486b49c854",
    ): "tenant prior reminder reply visibility",
    (
        "src/core/db/repositories/booking_repo.py::mark_reminder_sent",
        "9c074482858cc23e86e4bf366819cdca617cd950bde4d43fefd5958bb4a7391a",
    ): "exact tenant reminder dispatch mark visibility",
    (
        "src/core/db/repositories/booking_repo.py::list_reminders_timed_out",
        "65d8c0c2b85d69c0831b1a60a0cd5311e8bda0dc5437fa4df7af9a63741147b1",
    ): "tenant timed-out reminder visibility",
    (
        "src/core/db/repositories/booking_repo.py::list_bookings_da_verificare",
        "64e788218081b814b7ef4f42d1beff5fc148e4d53e3e9db9b733cfff3c7928af",
    ): "tenant scheduled review list visibility",
    (
        "src/core/db/repositories/external_booking_repo.py::get_latest_sync",
        "cfbfb73ba29db98eced65ed3a170345d35d5c7c6856c511785198dc2d77853bb",
    ): "tenant latest sync visibility",
    (
        "src/core/db/repositories/external_booking_repo.py::get_sync_record",
        "a6d0d3b875f54c799a067363bcb595784cf66143c284a4991a3145a4e5c50abc",
    ): "tenant exact idempotency record visibility",
    (
        "src/core/db/repositories/external_booking_repo.py::get_pending_syncs",
        "c0daa4cd6186871cedd463bcd0725b44a60134fd2213e30468df78bad885900c",
    ): "bounded system sync sweep excluding held booking IDs",
    (
        "src/core/db/repositories/message_repo.py::enqueue_ready_booking_confirmations",
        "687c0fe1ce55b7e3a35d6b9d03d6aab844e2a72db2e584d8ae8e93aa3560cac7",
    ): "bounded system confirmation enqueue with exact confirmed booking revision and visibility",
    (
        "src/core/db/repositories/message_repo.py::booking_confirmation_delivery_guard",
        "5c9ae14469883dad165dcc0024678f15361eca95fe9c76d3268cf4b94a969260",
    ): "tenant exact locked booking revision delivery visibility",
    (
        "src/core/report/csv_export.py::get_prenotazioni_completate",
        "c5ac5cef29e40bcbea6c6469d23e046a5fe4e1624b79a675edcf634b833e801b",
    ): "tenant bounded period/status CSV visibility",
    ("src/core/bookings/privacy_boundary.py::booking_note_classification_from_connection", "fecf5dbd08454a6dc999049ef2bd54b0151abd3128e5cb5a0bfddc4384b8f77d"):
        "exact tenant lookup filters organizations.id=$1 and joins the protected reader on organization_id; the caller holds the shared classification lock and FOR SHARE of organizations stabilizes the profile during the decision",
    (
        "src/core/gdpr/exporter.py::prepare",
        "4eddc60a274ebc571076499b0c2887de3cab50e8cb88ffc784699471317faf7c",
    ): "bounded row wrapper over frozen catalogue and fixed booking quarantine projections; catalogue_query proves classified sources and every tenant bind",
    ("src/core/gdpr/deletion.py::advance", "f9a258519e3fa406f40097d69d11998cfd74a40e5319170972e11d64c84a1b56"):
        "parent-derived consent deletion: exact child contact_id=contacts.id join, contacts.organization_id=$1 and optional subject=$2; parser supports derived SELECT only",
    ("src/core/gdpr/deletion.py::advance", "bb690d4a5d40fbbd2a3bcb80dffd00368028d46b26ed7b9eb2e9ce9a0588440b"):
        "parent-derived delivery deletion: exact message FK join and both messages/conversations organization_id=$1, optional subject=$2; cross-tenant regression required",
}

# system_scope is an annotation, not proof that a query is safe. Only these
# reviewed worker/tenant-resolution functions may omit a tenant bind filter.
# A new decorator use remains visible to the checker unless explicitly added.
SYSTEM_SCOPE_ALLOWLIST: dict[str, str] = {
    "src/core/backup/drill.py::_table_counts":
        "isolated encrypted backup parity check counts every table in the verified source/quarantine snapshot; identifiers come only from pg_catalog and are quoted",
    "src/core/gdpr/account_erasure_review.py::review_account_erasure":
        "owner-only review of an exact blocked account erasure request; global profile dependencies return booleans only",
    "src/core/bookings/private_worker_lifecycle.py::assert_private_booking_ready":
        "read-only PostgreSQL catalog readiness check for the dedicated private worker principal; no tenant records are read",
    "src/core/retention_job.py::_expired_exports":
        "enumerates expired snapshot IDs with SKIP LOCKED LIMIT 100; each mutation binds its stored tenant",
    "src/core/retention_job.py::_oldest_overdue_export_seconds":
        "reports only the oldest overdue export age platform-wide; no row identity or payload is returned",
    "src/core/retention_job.py::_retention_policies":
        "enumerates immutable policy metadata only; all primary-data mutations bind a policy tenant and require explicit approval and execution gate",
    "src/core/retention_job.py::scrub_expired_worker_capability_payloads":
        "owner-only bounded cleanup of expired unused and orphan capability payloads; oldest due age is aggregate-only",
    "src/core/db/quota_provenance.py::require_quota_trust_epoch":
        "reads the fixed NULL-organization protocol marker only; no caller-controlled row selector",
    "src/core/gdpr/orphan_proposal.py::_read_only_quota_epoch":
        "reads the same fixed protocol marker in a read-only owner planning transaction",
    "src/core/db/repositories/message_repo.py::claim_inbound_messages":
        "trusted worker claims the global inbound queue with SKIP LOCKED",
    "src/core/db/repositories/message_repo.py::reconstruct_payload_for_retry":
        "trusted retry worker resolves one message by primary key, then uses its stored organization_id downstream",
    "src/core/db/repositories/message_repo.py::reap_stale_claims":
        "trusted scheduled worker reaps claims across organizations",
    "src/core/db/repositories/message_repo.py::claim_delivery_attempts":
        "trusted retry worker claims globally queued attempts through their message parent",
    "src/core/db/repositories/message_repo.py::insert_delivery_attempt":
        "trusted retry worker inserts an attempt using an existing message parent",
    "src/core/db/repositories/message_repo.py::update_delivery_attempt":
        "trusted retry worker updates an attempt through its message parent",
    "src/core/db/repositories/organization_repo.py::get_memberships_by_auth":
        "server-validated principal lookup enumerates only that user's memberships",
    "src/core/db/repositories/organization_repo.py::get_or_create_organization_with_owner":
        "JIT provisioning checks membership for the server-validated auth subject before creating its organization",
    "src/core/db/repositories/organization_repo.py::get_org_by_phone_number_id":
        "pre-auth webhook tenant resolution by platform-issued phone number id",
    "src/core/db/repositories/organization_repo.py::get_org_by_waba_id":
        "pre-auth webhook tenant resolution by platform-issued WABA id",
    "src/core/db/repositories/organization_repo.py::get_orgs_by_waba_id":
        "pre-auth webhook fan-out by platform-issued WABA id",
    "src/instagram/repository.py::get_org_by_instagram_user_id":
        "pre-auth webhook tenant resolution by platform-issued Instagram id",
    "src/core/db/repositories/billing_repo.py::drain_governance_outbox":
        "trusted billing governance worker drains the durable cross-tenant queue",
    "src/core/scheduler.py::_calendar_sync_job":
        "trusted scheduler enumerates organizations with calendar sync enabled",
    "src/core/scheduler.py::_nonce_cleanup_job":
        "trusted scheduled cleanup removes expired OAuth nonces platform-wide after their short TTL",
    "src/core/db/repositories/external_booking_repo.py::get_pending_syncs":
        "trusted reconciliation worker claims pending syncs across organizations",
    "src/core/calendar/repository.py::claim":
        "bounded Calendar worker joins credential epoch and carries each stored tenant into every fenced dispatch/finalization",
    "src/core/db/repositories/message_repo.py::enqueue_ready_booking_confirmations":
        "trusted worker schedules bounded confirmations whose booking and messages agree on organization_id",
    "src/integrations/airtable/repository.py::reap_stale_processing":
        "trusted Airtable worker reclaims stale webhook queue events across organizations",
    "src/integrations/airtable/repository.py::get_subscription_by_webhook_id":
        "pre-auth webhook resolution uses the globally unique provider webhook id before tenant assignment",
    "src/core/db/repositories/webhook_inbox_repo.py::claim":
        "trusted worker claims the durable Meta inbox globally with SKIP LOCKED",
    "src/core/db/repositories/webhook_inbox_repo.py::purge_completed":
        "trusted retention worker purges completed raw webhook payloads after seven days",
}
# Exact literal exceptions: the function still scans every other SQL literal.
SYSTEM_SCOPE_SQL_FINGERPRINTS: dict[tuple[str, str], str] = {
    ("src/core/gdpr/account_erasure_review.py::review_account_erasure", "0c65c55ed53503cbc93de25dbdc939cda6f7a7e31c6c79d9e64980b264361501"):
        "fixed existence checks for the exact persisted account identity; no foreign tenant identifiers or contents and no mutations",
    ("src/core/backup/drill.py::_table_counts", "f08aece79bca5267c69ac02de5fcd8cc60586cb968d40e30674403c0ec39c395"):
        "dynamic count is restricted to schema/table identifiers obtained from pg_catalog, each identifier is independently quoted, and the function compares only verified backup/restore snapshots",
    ("src/core/bookings/private_worker_lifecycle.py::assert_private_booking_ready", "8c0fb690e1bc6108c73e7b401eedd14b509df0387cbf2ed71b40779c49a40e4a"):
        "exact read-only pg_class/pg_attribute readiness query; verifies table ownership and required schema only",
    ("src/core/retention_job.py::_expired_exports", "8b4e5b8f26cbb94ae68c4ba7993ad7aa3a945a2a4f80581233568cf5557b6687"):
        "bounded expiration of derived encrypted export artifacts, not source data",
    ("src/core/retention_job.py::_oldest_overdue_export_seconds", "63a55c537924bad0f118de5babe9f904afacf6f6574d532af010f603ee0a82d3"):
        "fixed projection of one scalar age for overdue encrypted export artifacts, without tenant identity or content",
    ("src/core/retention_job.py::scrub_expired_worker_capability_payloads", "c1920d9f082c06da3f9db25611a98231a15098188300c8c4f1d601f6c849182d"):
        "one fixed scalar age for due unused and orphan capability payloads without a matching context; no tenant, row identity or content",
    ("src/core/retention_job.py::_retention_policies", "672e839eec8bb38ab3c9c981b8e7aff866b18b10f656c5140476015fd979ed86"):
        "reviewed fixed-category policy inventory including C24 metadata only; no content/credentials; every mutation revalidates the exact tenant policy under its fence",
    ("src/core/calendar/repository.py::claim", "ca49781fa9e8cc26c388a66f2dbb7256dcd072bed1493150c5c3255bd4693c5e"): "bounded global claim joins credentials by tenant and epoch, locks only queue rows; each downstream operation revalidates tenant/fence; wall-clock lease expiry",
    ("src/core/db/repositories/message_repo.py::enqueue_ready_booking_confirmations", "5639484c3963e6f369b37c9d105964bc2155a57dbdc5de2ab8a98e162e1e85ee"): "bounded claim requires same-tenant outbound and confirmed exact booking revision; optional inbound is only absent for explicit staff confirmation",
    ("src/core/db/repositories/message_repo.py::enqueue_ready_booking_confirmations", "810f12dd6ee94b01cec490aa87e2061c03e8912f033fc178d29326a7e20b4330"): "attempt insertion uses only the outbound message id from the locked, tenant-consistent outbox row",
    ("src/core/db/repositories/message_repo.py::enqueue_ready_booking_confirmations", "10033a45b8df8fa6c14cc2fcd746e5cf6d6c2e7da8bcbd5d8d90e31d0bdca9b4"): "outbox status update uses only the id selected and locked by the same transaction",
    ("src/core/db/quota_provenance.py::require_quota_trust_epoch", "6ca4c3cfbc19d39d2db05a3d9ece55d1391817dfdf370d18a81fc4860bb2c4df"): "exact read-only marker UUID, NULL organization, protocol event type and zero quantity; every other SQL literal remains checked",
    ("src/core/gdpr/orphan_proposal.py::_read_only_quota_epoch", "b5964ff4cf5c103aae15a074bed3691f861d2e1e38f7e59ffad2934d671692a9"): "read-only owner planning checks only the fixed global quota trust marker UUID, NULL organization, protocol event type and zero quantity",
    (
        "src/core/db/repositories/organization_repo.py::get_memberships_by_auth",
        "af2dbc9265dbe944a21aeccc3004c8a09a32f30d09d0b1312e491403c6d9dfcf",
    ): "principal membership resolution, bound to verified auth subject and joined erasure barrier",
    ("src/core/db/repositories/organization_repo.py::get_or_create_organization_with_owner", "09355492d38d518d6e5b8044e4886cd594f11e26619c1966c643e37bb5da63a6"): "JIT lookup is bound to verified auth subject and joined erasure barrier",
    ("src/core/db/repositories/organization_repo.py::get_or_create_organization_with_owner", "784b96773a542ef34051427862f19594e87ad778d6c29dc50be36fe2e06ed072"): "JIT owner membership is inserted from the newly created organization row",
    ("src/core/db/repositories/organization_repo.py::get_org_by_phone_number_id", "b92af521898b894d1bd12f9675852502de316ca4377020173b8bbd3da20acfe2"): "pre-auth tenant resolution by provider phone-number identity",
    ("src/core/db/repositories/organization_repo.py::get_org_by_waba_id", "5ae1bafbb425db6399573777d3f9fbe9d1d49e98a9c52bdc850265e2a22baf2c"): "pre-auth tenant resolution by provider WABA identity",
    ("src/core/db/repositories/organization_repo.py::get_orgs_by_waba_id", "08dbcdc809f291ac5696cc84a315bac35613a455368122b65dee1c939bc1ba28"): "pre-auth webhook fan-out by provider WABA identity",
    ("src/core/db/repositories/message_repo.py::claim_inbound_messages", "4568dfab7495caee116ce882890de8cf2df5ffc5550a1c32243fb21790e46e8e"): "trusted worker atomically claims the global inbound queue",
    ("src/core/db/repositories/message_repo.py::claim_delivery_attempts", "a4e36173608f1673980cbc9361e85a122926e74faf7af0d6156e00aa4f2b66ab"): "trusted worker claims global retry queue rows joined to their message parent while locking attempts only",
    ("src/core/db/repositories/message_repo.py::claim_delivery_attempts", "8dfb5b50fd1234602121c89eac6b7a96ff310337c003ebb4370d3f5467af8106"): "trusted worker marks only claimed parent-linked retry rows processing",
    ("src/core/db/repositories/message_repo.py::insert_delivery_attempt", "593d164bc0fccb22f10f2238498a116694b38711823c590a17cf2985871d23ed"): "retry insertion selects its message foreign-key from the parent table",
    ("src/core/db/repositories/message_repo.py::update_delivery_attempt", "cdaf5d2595ada923b5b306edc8e56630a4741e3fd1e4cc60dffafd7dda8f99ad"): "retry update joins the referenced message parent and returns only attempt columns",
    ("src/core/db/repositories/message_repo.py::reconstruct_payload_for_retry", "5cf49adf35248386fcabb73ce0ee0d59ba7decf11818119ade91808b5287e7d1"): "trusted retry worker resolves a message by its queue-derived primary key",
    ("src/core/db/repositories/message_repo.py::reap_stale_claims", "ca6613b0fa56e333c5abed058b4da2b202367c76ee6f80a617455ed9eeb24e53"): "scheduled worker reaps stale message claims across tenants",
    ("src/core/db/repositories/message_repo.py::reap_stale_claims", "1ff3aa85ac5cb9ea545f420bd0d23143d16a56a237e8a8b6e735428305433e87"): "scheduled worker releases expired message claims across tenants",
    ("src/core/db/repositories/message_repo.py::reap_stale_claims", "a79cb5a46919dc706b5e58d826658adaac1a0f68c690deb5ae3bb065d20a7001"): "scheduled retry worker reaps parent-linked delivery attempts across tenants",
    ("src/core/db/repositories/billing_repo.py::drain_governance_outbox", "6e7739b56f19833341fa065f30ac955c665065930ccac86fb0610141a9bd9f1c"): "trusted governance worker enumerates its durable organization queue",
    ("src/instagram/repository.py::get_org_by_instagram_user_id", "cde7bca5abefaa42d6944ad8df14ff31708bdae92ff87826e5ca62bf169c14e2"): "pre-auth tenant resolution by provider Instagram identity",
    ("src/core/scheduler.py::_calendar_sync_job", "5e19f185ac041a63375f34617ac2d54a90b71987c48020e75637aaa948657ffc"): "trusted scheduler enumerates organization_id keys with calendar sync enabled; bookings remain tenant-bound",
    ("src/core/scheduler.py::_nonce_cleanup_job", "e670664a98b194f0df4daeee9a8962afedfc25153f0a5bd56e00f90ea2a47b1c"): "trusted scheduler removes expired OAuth nonce rows platform-wide",
    ("src/core/db/repositories/external_booking_repo.py::get_pending_syncs", "9b988cf8f99ec81ab4e543af18360472580a62649a13a6091c609782c42d3369"): "trusted reconciliation worker claims pending syncs across organizations",
    ("src/integrations/airtable/repository.py::get_subscription_by_webhook_id", "9fac9734a127f4f89d521c91c873754428a637b920dadc9d6a31b1e80088593f"): "pre-auth webhook resolution by provider-unique webhook id",
    ("src/integrations/airtable/repository.py::reap_stale_processing", "265bbdcffdcb619cfce2a4e967685582dc50fd8f46e197af27173912d1d6da71"): "trusted Airtable worker locks bounded stale claims; UPDATE correlates selected id and organization_id",
    ("src/core/db/repositories/webhook_inbox_repo.py::claim", "a72d6bfaa56e4423b4aa169cae408e790ae480c58dc5c0d1128e28908faeb2b1"): "trusted worker claims the durable Meta inbox globally with SKIP LOCKED",
    ("src/core/db/repositories/webhook_inbox_repo.py::purge_completed", "da0121e8a54d90132082510cf95a4bb1dde88485d3e3a1ec7909b7e26308ff86"): "trusted retention worker purges completed webhook payloads after seven days",
}
ALLOWLISTED_SQL_FINGERPRINTS.update({
    ("src/core/gdpr/independent_tip.py::initialize", "02f968861007c0f4b57bb1fd9a248386df734d8f0fd4d94533bc3bef0ac68e62"): "operator-only SQLite authority schema, never executed against PostgreSQL",
    ("src/core/gdpr/independent_tip.py::initialize", "ac2751436fa3df89a035eb837716fe1d482bc738757b228c5781a695832731de"): "operator-only SQLite chain index schema, never executed against PostgreSQL",
    ("src/core/gdpr/independent_tip.py::initialize", "18a9d8868485140149979e6bdb5e7a22a5af0dd759a97dd5dd85a28b480efa37"): "operator-only SQLite replay uniqueness schema, never executed against PostgreSQL",
    ("src/core/gdpr/independent_tip.py::initialize", "4e55f440180c5817f5c9b212569903979299f1138e1c44ce02bd4cde3eb5816c"): "operator-only SQLite pinned recovery checkpoints, never executed against PostgreSQL",
    ("src/core/gdpr/independent_tip.py::initialize", "13fe726e0b3426c88142d0f196852cd98a4c6f243b557d93610f4241daddb09d"): "operator-only SQLite pinned policy evidence, never executed against PostgreSQL",
    ("src/core/gdpr/independent_tip.py::initialize", "a75a61ffa4f7536de2f012ada6e6fb8923e8dafdbe10ef4b9b627e68d9b844ea"): "operator-only SQLite registry of approved key fingerprints, never executed against PostgreSQL",
    ("src/core/gdpr/independent_tip.py::promote_paged_checkpoints", "c68e789b41c41de23d751a2d5c8a008b6863e540c63a6a34a77c1c00f2f2f491"): "operator-only SQLite inventory history, never executed against PostgreSQL",
    ("src/core/gdpr/independent_tip.py::promote_paged_checkpoints", "87b9b94f3354adf56c77096f21e8561c7b4ca5b7152998cf6b88a0c5a86db8e8"): "operator-only SQLite retirement evidence digest, never executed against PostgreSQL",
    ("src/core/gdpr/independent_tip.py::activate_archive", "232bc2e73a42aeffdcf3773d2f68cfce326800c10033b5c1ed44002e09993730"): "operator-only SQLite archive-pin history, never executed against PostgreSQL",
    ("src/core/gdpr/journal_chain_pages.py::scan_verified_chain", "16c0e71a9321da76ebc5327186a740da906bd94c2a3d57f78f096e2821ca8550"): "private temporary SQLite index of ephemeral keyed operation digests, never executed against PostgreSQL",
    ("src/core/gdpr/backup_coverage.py::_covered_backup_evidence", "a8f2fae3ab3876a7bf97e80cdd2497555244ae2b89014fd6edb85f71fed3fa38"): "temporary SQLite table of authenticated sequence and keyed digests, never executed against PostgreSQL",
    ("src/core/gdpr/backup_coverage.py::_covered_backup_evidence", "02330e9d7588474209abbd04f52fea954da5510c4c93f055594b1026ebb972a0"): "temporary SQLite evidence lookup index, never executed against PostgreSQL",
    ("src/core/gdpr/source_journal_pages.py::capture_erasure_journal_delta_paged", "2529ccf635c43ea4195882cb9f8e8da1f8eef647c21d28821895903a3b44a220"): "private temporary SQLite index of ephemeral keyed source evidence digests, never executed against PostgreSQL",
    ("src/core/db/repositories/message_repo.py::booking_confirmation_delivery_guard", "30e6a02241fdc3b332a8d599f7c901550d390092c76974f6f3a6b4d21eb91566"): "locks only the booking joined through an outbox row bound to both organization_id and outbound_message_id; booking organization must match the outbox",
    ("src/core/db/repositories/organization_repo.py::create_organization_with_owner", "784b96773a542ef34051427862f19594e87ad778d6c29dc50be36fe2e06ed072"): "single-organization provisioning uses the id returned by its new_org CTE",
    ("src/api/routes/team.py::list_my_organizations", "f4b6826caa80db6bff31e20d43be652fb95d1bf30f387c7ffce178e8b963400d"): "enumerates memberships of the authenticated principal after checking each tenant erasure barrier",
    ("src/core/team_invitations.py::accept_invitation", "b890eeb6b0db2692e70655634732f3cd3e534f279df2919da0253b31dbd269be"): "hashed invite-token lookup resolves its organization before scoped acceptance",
    ("src/api/routes/dashboard.py::recupera_eventi_dashboard", "714cd7a5b2014322865036e5a6829d11712ad9119a9d102f8040f40dc9d44724"): "dashboard CTE binds all related tables and immutable message lineage checks to the authenticated organization parameter",
    ("src/api/routes/dashboard.py::recupera_eventi_prioritari", "de240f63e3f69701f547d5ad1ab207582a9c671aead8f0dc43854f6068c3c008"): "dashboard CTE binds all related tables and immutable message lineage checks to the authenticated organization parameter",
    ("src/core/gdpr/legacy_message_reconciliation.py::inventory_quarantined_messages", "303546eda544538ad910486d3110bb55d7f4f98fe5305f1c65c278d5f38c3331"): "bounded read-only lifecycle owner inventory joins all three relations on the exact tenant, intentionally preserves absent joins, returns only message ID and booleans",
})

_SQL_KEYWORD_RE = re.compile(
    r"\b(select|insert|update|delete|truncate|alter|drop|create|copy|lock|grant|revoke|comment|vacuum|refresh|reindex|call|do)\b",
    re.IGNORECASE,
)
_SQL_STATEMENT_START_RE = re.compile(
    r"^\s*(select|with|insert|update|delete|truncate|alter|drop|create|copy|lock|grant|revoke|comment|vacuum|refresh|reindex|call|do)\b",
    re.IGNORECASE,
)
_DYNAMIC_TABLE_RE = re.compile(
    r"(?<!for )\b(?:from|join|into|update)\s+$", re.IGNORECASE,
)
_DYNAMIC_TABLE_PREFIX_RE = re.compile(
    r"(?<!for )\b(?:from|join|into|update)\s+(?:[\w$\"`]+\s*\.\s*)?$",
    re.IGNORECASE,
)
_DYNAMIC_TABLE_TEMPLATE_RE = re.compile(
    r"(?<!for )\b(?:from|join|into|update)\s+"
    r"(?:[\w$\"`]+\s*\.\s*)?(?:\{[^{}]*\}|%s|%\([\w]+\)s|"
    r"[\w$\"`]*__CODEX_DYNAMIC_VALUE__[\w$\"`]*)",
    re.IGNORECASE,
)
_DYNAMIC_TABLE_RELATION_END_RE = re.compile(
    r"(?<!for )\b(?:from|join|into|update)\s+"
    r"(?:[\w$\"`]+\s*\.\s*)?[\w$\"`]+\s*$",
    re.IGNORECASE,
)

_DYNAMIC_TABLE_DETAIL = "dynamic table identifier"
_DYNAMIC_VALUE = "__CODEX_DYNAMIC_VALUE__"
_GUARDED_TABLES = TENANT_SCOPED_TABLES | INDIRECT_SCOPED_TABLES
_SQL_SINK_METHODS = frozenset({"execute", "executemany", "fetch", "fetchrow", "fetchval"})
_DB_RECEIVER_NAMES = frozenset({
    "conn", "connection", "pool", "cursor", "cur", "db", "session",
    "db_conn", "db_connection", "db_pool",
})
_VALIDATED_SQL_FORWARDERS = {
    "src/core/db/scoping.py::fetch": "assert_org_scoped",
    "src/core/db/scoping.py::fetchrow": "assert_org_scoped",
    "src/core/db/scoping.py::fetchval": "assert_org_scoped",
    "src/core/db/scoping.py::execute": "assert_org_scoped",
    "src/core/db/scoping.py::executemany": "assert_org_scoped",
    "src/core/workers/webhook_inbox_worker.py::fetchrow": "WEBHOOK_IDEMPOTENCY_SQL",
}


def _is_database_receiver(node) -> bool:
    """Recognize the repository's asyncpg-style receivers, not HTTP fetchers."""
    if isinstance(node, ast.Name):
        name = node.id.lower()
        return (
            name in _DB_RECEIVER_NAMES
            or name.endswith(("_conn", "_connection", "_pool", "_cursor"))
        )
    if isinstance(node, ast.Attribute):
        name = node.attr.lower()
        if name.endswith("_fetcher") or name in {"payload_fetcher", "http_client"}:
            return False
        if name in _DB_RECEIVER_NAMES or name in {"_conn", "_pool", "_connection", "_cursor"}:
            return True
        return _is_database_receiver(node.value)
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute) and node.func.attr in {"acquire", "transaction"}:
            return _is_database_receiver(node.func.value)
        if isinstance(node.func, ast.Name):
            name = node.func.id.lower()
            return name.startswith(("get_", "create_")) and name.endswith(
                ("pool", "connection", "conn")
            )
    return False


def _path_label(path) -> str:
    p = Path(path)
    try:
        return p.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return p.as_posix()


def _is_system_scope(fn) -> bool:
    for dec in fn.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Name) and target.id == "system_scope":
            return True
        if isinstance(target, ast.Attribute) and target.attr == "system_scope":
            return True
    return False


def sql_fingerprint(sql: str) -> str:
    normalized = " ".join(sql.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _compose_template(node, constants) -> str:
    """Conservatively compose string expressions, marking unknown values."""
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else _DYNAMIC_VALUE
    if isinstance(node, ast.Name):
        value = constants.get(node.id)
        return value if isinstance(value, str) else _DYNAMIC_VALUE
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                parts.append(_compose_template(value.value, constants))
        return "".join(parts)
    if isinstance(node, ast.BinOp):
        left = _compose_template(node.left, constants)
        right = _compose_template(node.right, constants)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Mod):
            if _DYNAMIC_VALUE not in left and _DYNAMIC_VALUE not in right:
                try:
                    return left % right
                except (TypeError, ValueError):
                    pass
            return _replace_percent_fields(left)
        return _DYNAMIC_VALUE
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr == "join":
            separator = _compose_template(node.func.value, constants)
            if len(node.args) != 1 or _DYNAMIC_VALUE in separator:
                return _DYNAMIC_VALUE
            items = node.args[0]
            if not isinstance(items, (ast.List, ast.Tuple)):
                return _DYNAMIC_VALUE
            parts = [_compose_template(item, constants) for item in items.elts]
            if any(_DYNAMIC_VALUE in part for part in parts):
                return _DYNAMIC_VALUE
            return separator.join(parts)
        if node.func.attr == "format":
            return _apply_format_template(
                _compose_template(node.func.value, constants), node, constants,
            )
        if node.func.attr == "format_map":
            return _replace_brace_fields(_compose_template(node.func.value, constants))
    return _DYNAMIC_VALUE


def _replace_brace_fields(template: str) -> str:
    return re.sub(r"\{[^{}]*\}", _DYNAMIC_VALUE, template)


def _replace_percent_fields(template: str) -> str:
    return re.sub(
        r"%(?:\([^)]+\))?[-+#0 ]*\d*(?:\.\d+)?[a-zA-Z]",
        _DYNAMIC_VALUE,
        template,
    )


def _apply_format_template(template: str, call: ast.Call, constants) -> str:
    positional = [_compose_template(arg, constants) for arg in call.args]
    keywords = {
        keyword.arg: _compose_template(keyword.value, constants)
        for keyword in call.keywords
        if keyword.arg is not None
    }
    result = []
    next_positional = 0
    try:
        parsed = Formatter().parse(template)
        for literal, field_name, format_spec, conversion in parsed:
            result.append(literal)
            if field_name is None:
                continue
            if field_name == "":
                value = positional[next_positional] if next_positional < len(positional) else _DYNAMIC_VALUE
                next_positional += 1
            elif field_name.isdecimal():
                index = int(field_name)
                value = positional[index] if index < len(positional) else _DYNAMIC_VALUE
            elif field_name in keywords:
                value = keywords[field_name]
            else:
                value = _DYNAMIC_VALUE
            if conversion or format_spec or _DYNAMIC_VALUE in value:
                result.append(_DYNAMIC_VALUE)
            else:
                result.append(value)
    except ValueError:
        return _replace_brace_fields(template)
    return "".join(result)


def _looks_like_sql(sql: str) -> bool:
    """Ignore ordinary prose while retaining SQL statements and templates."""
    match = _SQL_STATEMENT_START_RE.match(sql)
    if match is None:
        return False
    statement = match.group(1).lower()
    if statement in {"select", "with"}:
        return bool(re.search(r"\b(?:from|join|into|update|delete)\b", sql, re.IGNORECASE))
    if statement == "insert":
        return bool(re.search(r"\binto\b", sql, re.IGNORECASE))
    if statement == "update":
        # Complete UPDATE statements include SET; the trailing target prefix
        # is also SQL when a dynamic relation is being assembled.
        return bool(
            re.search(r"\bset\b", sql, re.IGNORECASE)
            or re.search(r"^\s*update\s+(?:(?:[\w$\"`]+\s*\.\s*)?)$", sql, re.IGNORECASE)
            or _DYNAMIC_TABLE_TEMPLATE_RE.search(sql)
        )
    if statement == "delete":
        return bool(re.search(r"\bdelete\s+from\b", sql, re.IGNORECASE))
    if statement == "copy":
        return bool(re.search(r"\bcopy\s+(?:\(|[\w$\"`])", sql, re.IGNORECASE))
    if statement in {"grant", "revoke", "comment"}:
        return bool(re.search(r"\bon\b", sql, re.IGNORECASE))
    return bool(re.search(r"\b(?:table|into|from|on)\b", sql, re.IGNORECASE))


def _has_relation_fragment(value: str | None) -> bool:
    if not value:
        return False
    return bool(
        _DYNAMIC_TABLE_RE.search(value)
        or _DYNAMIC_TABLE_PREFIX_RE.search(value)
        or _DYNAMIC_TABLE_TEMPLATE_RE.search(value)
        or _DYNAMIC_TABLE_RELATION_END_RE.search(value)
    )


def _has_dynamic_table_identifier(node, constants) -> bool:
    template = _compose_template(node, constants)
    if _DYNAMIC_TABLE_TEMPLATE_RE.search(template):
        return True
    return bool(
        _looks_like_sql(template)
        and (
            _DYNAMIC_TABLE_RE.search(template)
            or _DYNAMIC_TABLE_PREFIX_RE.search(template)
        )
    )


class _Visitor(ast.NodeVisitor):
    """Attribuisce ogni letterale SQL alla funzione piu' interna."""

    def __init__(self, label: str, violations: list, seen: set, docstring_nodes: set[int]):
        self._label = label
        self._violations = violations
        self._seen = seen
        self._docstring_nodes = docstring_nodes
        self._stack: list[tuple] = []
        self._module_constants: dict[str, str] = {}
        self._module_assigned: set[str] = set()
        self._module_invalid: set[str] = set()
        self._constants: dict[str, str] = {}
        self._local_assigned: set[str] = set()
        self._invalid_names: set[str] = set()
        self._invalid_hints: dict[str, str] = {}
        self._control_depth = 0
        self._covered_string_nodes: set[int] = set()

    def visit_Assign(self, node):
        self._record_assignments(node.targets, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        self._record_assignments([node.target], node.value)
        self.generic_visit(node)

    def visit_AugAssign(self, node):
        self._record_assignments([node.target], None)
        self.generic_visit(node)

    def visit_NamedExpr(self, node):
        self._record_assignments([node.target], node.value)
        self.generic_visit(node)

    def visit_Delete(self, node):
        self._record_assignments(node.targets, None)
        self.generic_visit(node)

    @staticmethod
    def _target_names(target) -> set[str]:
        return {
            child.id for child in ast.walk(target)
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)
        }

    def _record_assignments(self, targets, value) -> None:
        names = set().union(*(self._target_names(target) for target in targets))
        if not names:
            return
        if not self._stack:
            for name in names:
                if name in self._module_assigned or self._control_depth:
                    previous = self._module_constants.get(name)
                    candidate = (
                        _compose_template(value, self._module_constants)
                        if value is not None else _DYNAMIC_VALUE
                    )
                    hint = previous if _has_relation_fragment(previous) else candidate
                    self._module_constants[name] = (
                        hint if _has_relation_fragment(hint) else ""
                    ) + _DYNAMIC_VALUE
                    self._module_invalid.add(name)
                    continue
                self._module_assigned.add(name)
                if name in self._module_invalid or value is None:
                    self._module_constants[name] = _DYNAMIC_VALUE
                    continue
                candidate = _compose_template(value, self._module_constants)
                self._module_constants[name] = candidate
                if _DYNAMIC_VALUE in candidate:
                    self._module_invalid.add(name)
            self._constants = self._module_constants
            return

        candidate = _compose_template(value, self._constants) if value is not None else _DYNAMIC_VALUE
        for name in names:
            previous = self._constants.get(name) or self._invalid_hints.get(name)
            if self._control_depth or name in self._local_assigned or name in self._invalid_names:
                hint = previous if _has_relation_fragment(previous) else candidate
                unresolved = (
                    hint if _has_relation_fragment(hint) else ""
                ) + _DYNAMIC_VALUE
                self._constants[name] = unresolved
                self._invalid_names.add(name)
                if _has_relation_fragment(unresolved):
                    self._invalid_hints[name] = unresolved
                self._local_assigned.add(name)
                continue
            self._local_assigned.add(name)
            self._constants[name] = candidate
            if _DYNAMIC_VALUE in candidate:
                self._invalid_names.add(name)
                if _has_relation_fragment(candidate):
                    self._invalid_hints[name] = candidate

    def _visit_ambiguous_control_flow(self, node, targets=()) -> None:
        for target in targets:
            self._record_assignments([target], None)
        self._control_depth += 1
        self.generic_visit(node)
        self._control_depth -= 1

    def visit_If(self, node):
        self._visit_ambiguous_control_flow(node)

    def visit_For(self, node):
        self._visit_ambiguous_control_flow(node, (node.target,))

    visit_AsyncFor = visit_For

    def visit_While(self, node):
        self._visit_ambiguous_control_flow(node)

    def visit_Try(self, node):
        self._visit_ambiguous_control_flow(node)

    visit_TryStar = visit_Try

    def visit_With(self, node):
        # A context manager is not a branch: assignments in its body are
        # deterministic once execution enters the body. Only the bound
        # resource itself is unknown to this string resolver.
        for item in node.items:
            if item.optional_vars is not None:
                self._record_assignments([item.optional_vars], None)
        self.generic_visit(node)

    visit_AsyncWith = visit_With

    def visit_Match(self, node):
        self._visit_ambiguous_control_flow(node)

    def visit_FunctionDef(self, node):
        previous_constants = self._constants
        previous_local_assigned = self._local_assigned
        previous_invalid_names = self._invalid_names
        previous_invalid_hints = self._invalid_hints
        previous_control_depth = self._control_depth
        self._constants = dict(self._module_constants)
        self._local_assigned = set()
        self._invalid_names = set()
        self._invalid_hints = {}
        self._control_depth = 0
        args = node.args
        argument_names = {
            argument.arg
            for argument in (
                list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)
                + ([args.vararg] if args.vararg is not None else [])
                + ([args.kwarg] if args.kwarg is not None else [])
            )
        }
        for name in argument_names:
            self._constants[name] = _DYNAMIC_VALUE
            self._local_assigned.add(name)
            self._invalid_names.add(name)
        key = f"{self._label}::{node.name}"
        self._stack.append((node, key, _is_system_scope(node)))
        self.generic_visit(node)
        self._stack.pop()
        self._constants = previous_constants
        self._local_assigned = previous_local_assigned
        self._invalid_names = previous_invalid_names
        self._invalid_hints = previous_invalid_hints
        self._control_depth = previous_control_depth

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_JoinedStr(self, node):
        if id(node) not in self._covered_string_nodes:
            self._cover_string_expression(node)
            self._handle_sql_expression(node)
        self.generic_visit(node)

    def visit_BinOp(self, node):
        if (
            id(node) not in self._covered_string_nodes
            and isinstance(node.op, (ast.Add, ast.Mod))
        ):
            self._cover_string_expression(node)
            self._handle_sql_expression(node)
        self.generic_visit(node)

    def visit_Call(self, node):
        if (
            id(node) not in self._covered_string_nodes
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"format", "format_map", "join"}
        ):
            self._cover_string_expression(node)
            self._handle_sql_expression(node)
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in _SQL_SINK_METHODS
            and _is_database_receiver(node.func.value)
        ):
            sql_expression = node.args[0] if node.args else next(
                (keyword.value for keyword in node.keywords
                 if keyword.arg in {"sql", "query", "command"}),
                None,
            )
            self._handle_sql_sink(node, sql_expression)
        self.generic_visit(node)

    def visit_Constant(self, node):
        if (
            isinstance(node.value, str)
            and id(node) not in self._docstring_nodes
            and id(node) not in self._covered_string_nodes
        ):
            self._handle_sql_expression(node)

    def _cover_string_expression(self, node) -> None:
        """Avoid duplicate fragment reports while retaining nested SQL calls."""
        if isinstance(node, ast.Constant):
            if isinstance(node.value, str):
                self._covered_string_nodes.add(id(node))
            return
        if isinstance(node, ast.JoinedStr):
            self._covered_string_nodes.add(id(node))
            for value in node.values:
                self._cover_string_expression(value)
            return
        if isinstance(node, ast.FormattedValue):
            self._cover_string_expression(node.value)
            return
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
            self._covered_string_nodes.add(id(node))
            self._cover_string_expression(node.left)
            self._cover_string_expression(node.right)
            return
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"format", "format_map", "join"}
        ):
            self._covered_string_nodes.add(id(node))
            self._cover_string_expression(node.func.value)
            for argument in node.args:
                self._cover_string_expression(argument)
            for keyword in node.keywords:
                self._cover_string_expression(keyword.value)
            return
        if isinstance(node, (ast.List, ast.Tuple)):
            for item in node.elts:
                self._cover_string_expression(item)

    def _add(self, fn, detail: str, sql: str) -> None:
        key = (fn.name, sql_fingerprint(sql))
        if key in self._seen:
            return
        self._seen.add(key)
        self._violations.append(
            (self._label, fn.name, fn.lineno, detail, sql)
        )

    def _handle_sql_sink(self, node, sql_expression) -> None:
        if not self._stack:
            return
        fn, function_key, _decorated_system_scope = self._stack[-1]
        validation_symbol = _VALIDATED_SQL_FORWARDERS.get(function_key)
        if validation_symbol and any(
            isinstance(child, ast.Name) and child.id == validation_symbol
            for child in ast.walk(fn)
        ):
            return
        if sql_expression is None:
            self._add(fn, "unresolved SQL sink expression", "<missing SQL argument>")
            return
        static = _compose_template(sql_expression, self._constants)
        if _DYNAMIC_VALUE not in static:
            self._handle_sql_expression(sql_expression)
            return
        # A visible SQL prefix still receives the normal, more specific check.
        # Opaque parameters/helper returns must never silently disappear.
        if _looks_like_sql(static):
            self._handle_sql_expression(sql_expression)
            return
        self._add(fn, "unresolved SQL sink expression", static)

    def _handle_sql_expression(self, node) -> None:
        if not self._stack:
            return
        static = _compose_template(node, self._constants)
        fn, function_key, decorated_system_scope = self._stack[-1]
        fingerprint = sql_fingerprint(static)
        if (function_key, fingerprint) in ALLOWLISTED_SQL_FINGERPRINTS:
            return
        if (
            decorated_system_scope
            and function_key in SYSTEM_SCOPE_ALLOWLIST
            and (function_key, fingerprint) in SYSTEM_SCOPE_SQL_FINGERPRINTS
        ):
            return
        if _has_dynamic_table_identifier(node, self._constants):
            self._add(fn, _DYNAMIC_TABLE_DETAIL, static)
            return
        if not _SQL_KEYWORD_RE.search(static) or not _looks_like_sql(static):
            return
        if _DYNAMIC_VALUE in static:
            try:
                tables = extract_tables(static) & _GUARDED_TABLES
            except ValueError:
                tables = set()
            detail = "dynamic SQL interpolation"
            if tables:
                detail += f" ({', '.join(sorted(tables))})"
            self._add(fn, detail, static)
            return
        try:
            if has_non_dml_operation(static):
                tables = extract_tables(static) & _GUARDED_TABLES
                detail = ", ".join(sorted(tables)) or "non-DML operation"
                self._add(fn, detail, static)
                return
            tables = extract_tables(static) & _GUARDED_TABLES
            scoped = not tables or has_explicit_org_scope(static, tables)
        except ValueError:
            self._add(fn, "unparseable SQL literal", static)
            return
        if not scoped:
            self._add(fn, ", ".join(sorted(tables)), static)


def check_file(path) -> list[tuple]:
    """Violazioni [(percorso, funzione, riga, dettaglio, sql), ...] del file."""
    source = Path(path).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    docstring_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.body and isinstance(node.body[0], ast.Expr):
                value = node.body[0].value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    docstring_nodes.add(id(value))
    violations: list[tuple] = []
    seen: set[tuple[str, str]] = set()
    _Visitor(_path_label(path), violations, seen, docstring_nodes).visit(tree)
    return violations


def main(argv) -> int:
    targets = argv[1:] or DEFAULT_TARGETS
    violations: list[tuple] = []
    checked = 0
    for target in targets:
        path = Path(target)
        if not path.exists():
            print(f"TENANT SCOPING CHECK: file mancante: {target}", file=sys.stderr)
            return 2
        violations.extend(check_file(path))
        checked += 1
    if violations:
        print(f"TENANT SCOPING CHECK: {len(violations)} VIOLAZIONI\n")
        for path, fn_name, lineno, detail, sql in violations:
            print(f"  {path}:{lineno}  {fn_name}()  ->  {detail}")
            print(f"      {sql[:100]!r} fingerprint={sql_fingerprint(sql)}")
        print(f"\nFile controllati: {checked}")
        return 1
    print("TENANT SCOPING CHECK: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
