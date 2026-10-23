"""Static validation tests for the frozen GDPR export SQL catalogue."""
from dataclasses import replace
import pytest

from src.core.gdpr import catalog, exporter


def _register(monkeypatch, source, **changes):
    candidate = replace(source, **changes)
    monkeypatch.setattr(exporter, "CATEGORIES", (*catalog.CATEGORIES, candidate))
    return candidate


def test_every_nonempty_frozen_projection_passes_runtime_validator():
    count = 0
    for category in catalog.CATEGORIES:
        for scope in ("organization", "contact", "account"):
            query = category.query if scope == "organization" else getattr(category, scope + "_query")
            if query:
                assert exporter.catalogue_query(category, scope) == query
                count += 1
    assert count == 70
    assert len({category.name for category in catalog.CATEGORIES}) == 51


def test_empty_scope_is_not_executable():
    category = next(item for item in catalog.CATEGORIES if not item.contact_query)
    assert exporter.catalogue_query(category, "contact") is None


def test_category_outside_frozen_catalogue_is_rejected():
    foreign = catalog.Category(
        "foreign", "SELECT id FROM contacts WHERE organization_id = $1::uuid",
    )
    with pytest.raises(ValueError, match="frozen GDPR catalogue"):
        exporter.catalogue_query(foreign, "organization")


@pytest.mark.parametrize("query", [
    "SELECT id FROM contacts",  # tenant predicate removed
    "SELECT id FROM pg_user WHERE organization_id = $1::uuid",  # unclassified source
    "SELECT * FROM contacts WHERE organization_id = $1::uuid",  # wildcard projection
    "INSERT INTO contacts(id) VALUES ($1)",  # mutating statement
    "SELECT id FROM contacts WHERE organization_id = $1::uuid; DROP TABLE contacts",
    "SELECT id FROM contacts WHERE organization_id = $1::uuid -- ' || request_text",
])
def test_mutated_projection_is_rejected_fail_closed(monkeypatch, query):
    source = next(item for item in catalog.CATEGORIES if item.name == "contacts")
    candidate = _register(monkeypatch, source, query=query)
    with pytest.raises(ValueError):
        exporter.catalogue_query(candidate, "organization")


def test_contact_projection_cannot_drop_explicit_tenant_scope(monkeypatch):
    source = next(item for item in catalog.CATEGORIES if item.name == "messages")
    unsafe = source.contact_query.replace("AND c.organization_id = $1::uuid", "")
    candidate = _register(monkeypatch, source, contact_query=unsafe)
    with pytest.raises(ValueError, match="tenant bound"):
        exporter.catalogue_query(candidate, "contact")


def test_requested_text_cannot_become_query_or_mutator(monkeypatch):
    source = next(item for item in catalog.CATEGORIES if item.name == "contacts")
    request_text = "x' UNION SELECT secret FROM auth.users; DELETE FROM contacts --"
    unsafe = f"SELECT id FROM contacts WHERE organization_id = $1::uuid AND phone_number = '{request_text}'"
    candidate = _register(monkeypatch, source, query=unsafe)
    with pytest.raises(ValueError):
        exporter.catalogue_query(candidate, "organization")
