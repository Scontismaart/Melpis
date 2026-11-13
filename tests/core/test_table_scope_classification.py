"""Table scope inventory must cover every migration/bootstrap table."""

import re
from pathlib import Path

from src.core.db.scoping import TABLE_SCOPE_CLASSIFICATION, TENANT_SCOPED_TABLES

REPO_ROOT = Path(__file__).resolve().parents[2]
CREATE_TABLE_RE = re.compile(
    r"\bcreate\s+table\s+(?:if\s+not\s+exists\s+)?([a-z_][a-z0-9_$.]*)",
    re.IGNORECASE,
)
CREATE_VIEW_RE = re.compile(
    r"^\s*create\s+(?:or\s+replace\s+)?view\s+([a-z_][a-z0-9_$.]*)",
    re.IGNORECASE | re.MULTILINE,
)
VALID_CLASSES = {"direct", "parent-derived", "system/root", "platform/infra"}


def _created_tables() -> set[str]:
    sources = list((REPO_ROOT / "src/core/db/migrations").glob("*.sql")) + [
        REPO_ROOT / "src/core/db/schema.sql",
        REPO_ROOT / "src/whatsapp/schema.sql",
    ]
    return {
        table.split(".")[-1].lower()
        for path in sources
        for pattern in (CREATE_TABLE_RE, CREATE_VIEW_RE)
        for table in pattern.findall(path.read_text(encoding="utf-8"))
    }


def test_every_migration_and_bootstrap_table_has_scope_class_and_rationale():
    created = _created_tables()
    classified = set(TABLE_SCOPE_CLASSIFICATION)

    assert created == classified
    assert all(
        classification in VALID_CLASSES and rationale.strip()
        for classification, rationale in TABLE_SCOPE_CLASSIFICATION.values()
    )


def test_direct_scope_inventory_matches_static_guard_tables():
    direct = {
        table
        for table, (classification, _rationale) in TABLE_SCOPE_CLASSIFICATION.items()
        if classification == "direct"
    }
    assert direct == TENANT_SCOPED_TABLES
    assert {
        "oauth_nonces",
        "billing_checkout_intents",
        "governance_outbox",
        "team_invitations",
        "worker_capabilities",
        "worker_context",
        "booking_availability",
        "booking_resource_availability",
        "worker_booking_inserts",
        "worker_booking_stays_inserts",
        "worker_allocation_sets",
        "worker_resource_allocations",
        "worker_allocation_requests",
        "worker_confirmation_outbox",
        "worker_outbound_messages",
    } <= direct


def test_nullable_stripe_event_ledger_is_not_treated_as_direct_tenant_data():
    classification, rationale = TABLE_SCOPE_CLASSIFICATION["processed_stripe_events"]
    assert classification == "platform/infra"
    assert "optional" in rationale
    assert "processed_stripe_events" not in TENANT_SCOPED_TABLES
