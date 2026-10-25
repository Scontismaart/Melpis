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
