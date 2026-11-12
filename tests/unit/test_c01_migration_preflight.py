import asyncio
import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "c01_migration_preflight.py"
_SPEC = importlib.util.spec_from_file_location("c01_migration_preflight", _SCRIPT)
preflight = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(preflight)


def test_local_dsn_requires_dedicated_host_port_and_database():
    assert preflight.parse_local_dsn(
        "postgresql://test:secret@127.0.0.1:55496/melpis_c01_upgrade"
    ) == {"host": "127.0.0.1", "port": 55496, "database": "melpis_c01_upgrade"}


def test_server_identity_accepts_private_postgres_container_behind_nat():
    assert preflight.valid_server_identity(
        "172.19.0.3", 5432, "melpis_c01_clean_20261025", preflight.APPLICATION_NAME
    )
    assert preflight.valid_server_identity(
        "127.0.0.1", 5432, "melpis_c01_upgrade_20261025", preflight.APPLICATION_NAME
    )
    assert preflight.valid_server_identity(
        "172.17.0.2/32", 5432, "melpis_c01_clean_20261025", preflight.APPLICATION_NAME
    )


@pytest.mark.parametrize("identity", [
    ("8.8.8.8", 5432, "melpis_c01_clean_20261025", preflight.APPLICATION_NAME),
    ("172.19.0.3", 55496, "melpis_c01_clean_20261025", preflight.APPLICATION_NAME),
    ("172.19.0.3", 5432, "production", preflight.APPLICATION_NAME),
    ("172.19.0.3", 5432, "melpis_c01_clean_20261025", "other-application"),
])
def test_server_identity_rejects_non_synthetic_target(identity):
    assert not preflight.valid_server_identity(*identity)


@pytest.mark.parametrize("dsn", [
    "postgresql://u:p@localhost:55496/melpis_c01_test",
    "postgresql://u:p@192.168.1.10:55496/melpis_c01_test",
    "postgresql://u:p@127.0.0.1:54322/melpis_c01_test",
    "postgresql://u:p@127.0.0.1:5432/melpis_c01_test",
    "postgresql://u:p@127.0.0.1:55496/production",
    "postgresql://u:p@127.0.0.1:55496/melpis_c01_test?sslmode=require",
    "postgresql://u:p@127.0.0.1:55496/melpis_c01_test#fragment",
])
def test_local_dsn_refuses_other_targets(dsn):
    with pytest.raises(preflight.PreflightError):
        preflight.parse_local_dsn(dsn)


def test_migration_inventory_is_ordered_and_replay_is_exact():
    files = preflight.migration_files(60, 66)
    assert [path.name[:3] for path in files] == ["060", "061", "062", "063", "064", "065", "066"]
    assert preflight.migration_plan("upgrade") == files
    assert preflight.migration_plan("replay") == files


def test_migration_versions_are_unique_across_entire_catalog():
    files = preflight.migration_files(1, 999, require_contiguous=False)
    versions = [int(path.name[:3]) for path in files]
    assert len(versions) == len(set(versions))
    assert any(path.name == "072_hotel_retention_eligibility.sql" for path in files)
    assert any(path.name == "088_calendar_refresh_dispatch_dark.sql" for path in files)


def test_hotel_prerequisite_precedes_consumers_and_calendar_column_is_not_used_early():
    files = preflight.migration_files(1, 999, require_contiguous=False)
    names = [path.name for path in files]
    hotel = names.index("072_hotel_retention_eligibility.sql")
    calendar = names.index("088_calendar_refresh_dispatch_dark.sql")
    assert hotel < names.index("077_hotel_allocation_archive.sql")
    assert hotel < names.index("080_hotel_retention_purge.sql")
    assert all("refresh_dispatch_id" not in path.read_text(encoding="utf-8")
               for path in files[:calendar])


def test_private_booking_worker_role_is_preprovisioned_by_superuser():
    migration = (preflight.MIGRATIONS / "086_private_booking_worker_role.sql").read_text(encoding="utf-8")
    preflight_block = migration.split("GRANT USAGE ON SCHEMA public", 1)[0]
    assert "CREATE ROLE melpis_c24_booking_worker" not in migration
    assert "ALTER ROLE melpis_c24_booking_worker" not in migration
    assert "worker role must be provisioned by a superuser before migration 086" in preflight_block
    assert "rolcanlogin OR rolinherit" in preflight_block
    workflow = (Path(__file__).resolve().parents[2] / ".github" / "workflows" / "migrations.yml").read_text(encoding="utf-8")
    assert "CREATE ROLE melpis_c24_booking_worker NOLOGIN" in workflow
    assert "m.member=worker_role OR m.roleid=worker_role" in preflight_block
    assert migration.index("worker role must be provisioned") < migration.index("GRANT USAGE")


def test_duplicate_outside_requested_c01_range_is_rejected(tmp_path, monkeypatch):
    for name in ("060_baseline.sql", "088_first.sql", "088_second.sql"):
        (tmp_path / name).write_text("-- synthetic migration\n", encoding="utf-8")
    monkeypatch.setattr(preflight, "MIGRATIONS", tmp_path)

    with pytest.raises(preflight.PreflightError, match="Migrazione duplicata"):
        preflight.migration_files(60, 60)


@pytest.mark.parametrize("name", ["88_unpadded.sql", "000_invalid.sql", "nested/090_ignored.sql"])
def test_unusable_migration_file_cannot_escape_catalog_check(tmp_path, monkeypatch, name):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("-- synthetic migration\n", encoding="utf-8")
    monkeypatch.setattr(preflight, "MIGRATIONS", tmp_path)

    with pytest.raises(preflight.PreflightError, match="Nome o percorso migrazione non valido"):
        preflight.migration_files(1, 999, require_contiguous=False)


def test_empty_migration_catalog_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight, "MIGRATIONS", tmp_path)

    with pytest.raises(preflight.PreflightError, match="Catalogo migrazioni vuoto"):
        preflight.migration_files(1, 999, require_contiguous=False)


def test_upgrade_requires_explicit_go_before_importing_database_driver(monkeypatch):
    monkeypatch.delenv("C01_PREFLIGHT_GO", raising=False)
    with pytest.raises(preflight.PreflightError, match="Manca C01_PREFLIGHT_GO"):
        asyncio.run(preflight.run(
            "upgrade", "postgresql://test:secret@127.0.0.1:55496/melpis_c01_upgrade"
        ))


def test_clean_inventory_allows_historical_numbering_gaps():
    files = preflight.migration_plan("clean")
    assert files[0].name.startswith("002_")
    assert files[-1].name.startswith("066_")
