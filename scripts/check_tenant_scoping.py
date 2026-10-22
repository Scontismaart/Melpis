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
ALLOWLISTED_SQL_FINGERPRINTS: dict[tuple[str, str], str] = {}

# system_scope is an annotation, not proof that a query is safe. Only these
# reviewed worker/tenant-resolution functions may omit a tenant bind filter.
# A new decorator use remains visible to the checker unless explicitly added.
SYSTEM_SCOPE_ALLOWLIST: dict[str, str] = {
    "src/core/db/quota_provenance.py::require_quota_trust_epoch":
        "reads the fixed NULL-organization protocol marker only; no caller-controlled row selector",
    "src/core/db/repositories/message_repo.py::claim_inbound_messages":
        "trusted worker claims the global inbound queue with SKIP LOCKED",
    "src/core/db/repositories/message_repo.py::reconstruct_payload_for_retry":
        "trusted retry worker resolves one message by primary key, then uses its stored organization_id downstream",
    "src/core/db/repositories/message_repo.py::reap_stale_claims":
        "trusted scheduled worker reaps claims across organizations",
    "src/core/db/repositories/message_repo.py::delete_expired_messages":
        "trusted retention worker expires messages across organizations",
    "src/core/db/repositories/message_repo.py::purge_soft_deleted_messages":
        "trusted retention worker purges messages across organizations",
    "src/core/db/repositories/message_repo.py::purge_simulation_requests":
        "trusted retention worker expires simulation records across organizations",
    "src/core/db/repositories/message_repo.py::claim_delivery_attempts":
        "trusted retry worker claims globally queued attempts through their message parent",
    "src/core/db/repositories/message_repo.py::insert_delivery_attempt":
        "trusted retry worker inserts an attempt using an existing message parent",
    "src/core/db/repositories/message_repo.py::update_delivery_attempt":
        "trusted retry worker updates an attempt through its message parent",
    "src/core/db/repositories/conversation_repo.py::cleanup_empty_conversations":
        "trusted retention worker cleans empty conversations across organizations",
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
    "src/core/db/repositories/booking_repo.py::claim_calendar_sync":
        "trusted worker claims a bounded optional Calendar queue, then carries each stored organization_id into scoped processing",
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
    ("src/core/db/repositories/booking_repo.py::claim_calendar_sync", "b51862a676b035b96cf9501d89b9c2c21d9d7a69331c148090a71bfdb9edc1bd"): "bounded global claim joins each calendar sync to its booking on organization_id and locks only sync rows",
    ("src/core/db/repositories/message_repo.py::enqueue_ready_booking_confirmations", "36dabbca79e0d1236dcf4361946dc3629066096493ebe13a20f2cec92ab2127b"): "bounded global confirmation claim joins inbound, outbound and booking to the same organization_id",
    ("src/core/db/repositories/message_repo.py::enqueue_ready_booking_confirmations", "810f12dd6ee94b01cec490aa87e2061c03e8912f033fc178d29326a7e20b4330"): "attempt insertion uses only the outbound message id from the locked, tenant-consistent outbox row",
    ("src/core/db/repositories/message_repo.py::enqueue_ready_booking_confirmations", "10033a45b8df8fa6c14cc2fcd746e5cf6d6c2e7da8bcbd5d8d90e31d0bdca9b4"): "outbox status update uses only the id selected and locked by the same transaction",
    ("src/core/db/quota_provenance.py::require_quota_trust_epoch", "6ca4c3cfbc19d39d2db05a3d9ece55d1391817dfdf370d18a81fc4860bb2c4df"): "exact read-only marker UUID, NULL organization, protocol event type and zero quantity; every other SQL literal remains checked",
    ("src/core/db/repositories/organization_repo.py::get_memberships_by_auth", "29778c3d709058faa351c858baae4a2eb2a9d9b77bcaa9a503cb77f1f6990b01"): "principal membership resolution, bound to verified auth subject",
    ("src/core/db/repositories/organization_repo.py::get_or_create_organization_with_owner", "48f99c0cd04ace9aed7749c723e6a46fb3e8eb78bc31defca15f5d36198b8776"): "JIT lookup is bound to verified auth subject",
    ("src/core/db/repositories/organization_repo.py::get_or_create_organization_with_owner", "784b96773a542ef34051427862f19594e87ad778d6c29dc50be36fe2e06ed072"): "JIT owner membership is inserted from the newly created organization row",
    ("src/core/db/repositories/organization_repo.py::get_org_by_phone_number_id", "b92af521898b894d1bd12f9675852502de316ca4377020173b8bbd3da20acfe2"): "pre-auth tenant resolution by provider phone-number identity",
    ("src/core/db/repositories/organization_repo.py::get_org_by_waba_id", "5ae1bafbb425db6399573777d3f9fbe9d1d49e98a9c52bdc850265e2a22baf2c"): "pre-auth tenant resolution by provider WABA identity",
    ("src/core/db/repositories/organization_repo.py::get_orgs_by_waba_id", "08dbcdc809f291ac5696cc84a315bac35613a455368122b65dee1c939bc1ba28"): "pre-auth webhook fan-out by provider WABA identity",
    ("src/core/db/repositories/conversation_repo.py::cleanup_empty_conversations", "010fe930de511feda8a9870ddc1f79abf061f5172c61d1d8069e48d5e11d67f1"): "scheduled retention cleanup of empty conversations across tenants",
    ("src/core/db/repositories/message_repo.py::claim_inbound_messages", "4568dfab7495caee116ce882890de8cf2df5ffc5550a1c32243fb21790e46e8e"): "trusted worker atomically claims the global inbound queue",
    ("src/core/db/repositories/message_repo.py::claim_delivery_attempts", "a4e36173608f1673980cbc9361e85a122926e74faf7af0d6156e00aa4f2b66ab"): "trusted worker claims global retry queue rows joined to their message parent while locking attempts only",
    ("src/core/db/repositories/message_repo.py::claim_delivery_attempts", "8dfb5b50fd1234602121c89eac6b7a96ff310337c003ebb4370d3f5467af8106"): "trusted worker marks only claimed parent-linked retry rows processing",
    ("src/core/db/repositories/message_repo.py::insert_delivery_attempt", "593d164bc0fccb22f10f2238498a116694b38711823c590a17cf2985871d23ed"): "retry insertion selects its message foreign-key from the parent table",
    ("src/core/db/repositories/message_repo.py::update_delivery_attempt", "cdaf5d2595ada923b5b306edc8e56630a4741e3fd1e4cc60dffafd7dda8f99ad"): "retry update joins the referenced message parent and returns only attempt columns",
    ("src/core/db/repositories/message_repo.py::reconstruct_payload_for_retry", "5cf49adf35248386fcabb73ce0ee0d59ba7decf11818119ade91808b5287e7d1"): "trusted retry worker resolves a message by its queue-derived primary key",
    ("src/core/db/repositories/message_repo.py::reap_stale_claims", "ca6613b0fa56e333c5abed058b4da2b202367c76ee6f80a617455ed9eeb24e53"): "scheduled worker reaps stale message claims across tenants",
    ("src/core/db/repositories/message_repo.py::reap_stale_claims", "1ff3aa85ac5cb9ea545f420bd0d23143d16a56a237e8a8b6e735428305433e87"): "scheduled worker releases expired message claims across tenants",
    ("src/core/db/repositories/message_repo.py::reap_stale_claims", "a79cb5a46919dc706b5e58d826658adaac1a0f68c690deb5ae3bb065d20a7001"): "scheduled retry worker reaps parent-linked delivery attempts across tenants",
    ("src/core/db/repositories/message_repo.py::delete_expired_messages", "6cd0eca8b451c7811b6bd5155cf0df79ea1efb4d0c6cf77732ec3886f865b275"): "scheduled retention worker expires messages across tenants",
    ("src/core/db/repositories/message_repo.py::purge_soft_deleted_messages", "cefaf30a03b9e93f1108a3b617f065c8c3c52cd2d35ed40845295817e3bf02da"): "scheduled retention worker purges soft-deleted messages across tenants",
    ("src/core/db/repositories/message_repo.py::purge_simulation_requests", "344f2b7345d48ff8602807867ac5f921e171089c5f169b4a46e171e204b39c80"): "scheduled retention worker purges expired simulation requests across tenants",
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
    ("src/core/db/repositories/message_repo.py::booking_confirmation_delivery_guard", "30e6a02241fdc3b332a8d599f7c901550d390092c76974f6f3a6b4d21eb91566"): "locks only the booking joined through an outbox row bound to both organization_id and outbound_message_id; booking organization must match the outbox",
    ("src/core/db/repositories/organization_repo.py::create_organization_with_owner", "784b96773a542ef34051427862f19594e87ad778d6c29dc50be36fe2e06ed072"): "single-organization provisioning uses the id returned by its new_org CTE",
    ("src/api/routes/team.py::list_my_organizations", "f018234f9a40424316a12de8391d250b8c03179c1a30953029f60b3928893ecf"): "enumerates only memberships bound to the authenticated principal",
    ("src/core/team_invitations.py::accept_invitation", "b890eeb6b0db2692e70655634732f3cd3e534f279df2919da0253b31dbd269be"): "hashed invite-token lookup resolves its organization before scoped acceptance",
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
