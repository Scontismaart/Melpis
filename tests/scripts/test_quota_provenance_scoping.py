"""Regression coverage for the exact global quota marker scope exception."""

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = REPO_ROOT / "scripts" / "check_tenant_scoping.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("check_tenant_scoping_quota_test", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_tenant_scoping = _load_script()

_MARKER_QUERY = """SELECT organization_id, event_type, quantity, metadata
             FROM public.usage_events
            WHERE id='6206d423-cea9-5480-bb9b-59d757503f14'::uuid
              AND organization_id IS NULL
              AND event_type='system.quota_trust_epoch' AND quantity=0
            FOR SHARE"""


def _module(tmp_path, *, query=_MARKER_QUERY, decorator=True, function_name="require_quota_trust_epoch",
            extra_query=None):
    decorator_line = '@system_scope("read only the fixed global marker")\n' if decorator else ""
    extra = f'    await conn.fetch("{extra_query}")\n' if extra_query else ""
    source = (
        "from src.core.db.scoping import system_scope\n"
        f"{decorator_line}"
        f"async def {function_name}(conn):\n"
        f"    await conn.fetchrow({query!r})\n"
        f"{extra}"
    )
    path = tmp_path / "src" / "core" / "db" / "quota_provenance.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def test_real_quota_provenance_module_passes_exact_scope_exception():
    path = REPO_ROOT / "src" / "core" / "db" / "quota_provenance.py"
    assert check_tenant_scoping.check_file(path) == []


@pytest.mark.parametrize("mutation", [
    "marker_uuid",
    "remove_null_org",
    "event_type",
    "quantity",
    "bind_marker_id",
    "extra_broad_query",
    "remove_decorator",
    "rename_function",
])
def test_marker_exception_rejects_nearby_or_unrelated_queries(tmp_path, monkeypatch, mutation):
    query = _MARKER_QUERY
    decorator = True
    function_name = "require_quota_trust_epoch"
    extra_query = None
    if mutation == "marker_uuid":
        query = query.replace("6206d423-cea9-5480-bb9b-59d757503f14", "7206d423-cea9-5480-bb9b-59d757503f14")
    elif mutation == "remove_null_org":
        query = query.replace("AND organization_id IS NULL", "")
    elif mutation == "event_type":
        query = query.replace("system.quota_trust_epoch", "ordinary_usage")
    elif mutation == "quantity":
        query = query.replace("quantity=0", "quantity=1")
    elif mutation == "bind_marker_id":
        query = query.replace("id='6206d423-cea9-5480-bb9b-59d757503f14'::uuid", "id=$1")
    elif mutation == "extra_broad_query":
        extra_query = "SELECT * FROM public.usage_events"
    elif mutation == "remove_decorator":
        decorator = False
    elif mutation == "rename_function":
        function_name = "load_quota_marker"

    path = _module(
        tmp_path, query=query, decorator=decorator,
        function_name=function_name, extra_query=extra_query,
    )
    monkeypatch.setattr(check_tenant_scoping, "REPO_ROOT", tmp_path)
    violations = check_tenant_scoping.check_file(path)
    assert violations, mutation
    assert any("usage_events" in violation[3] for violation in violations), mutation
