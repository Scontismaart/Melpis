"""Guarded local-only preflight for replaying database migrations 060-066.

This tool intentionally does not create databases or synthetic markers. A
disposable database must be provisioned and marked by its owner first.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import ipaddress
import os
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "src" / "core" / "db" / "migrations"
GO_VALUE = "C01-LOCAL-DISPOSABLE-ONLY-20261025"
MARKER_KEY = "melpis-c01-preflight"
MARKER_VALUE = "C01_SYNTHETIC_DISPOSABLE_V1"
APPLICATION_NAME = "melpis-c01-preflight"
REPORT_PATH = ROOT / "docs" / "operations" / "c01-local-preflight-results-20261025.json"
ALLOWED_DATABASE = re.compile(r"^melpis_c01_[a-z0-9_]{1,48}$")


class PreflightError(RuntimeError):
    """Fail-closed preflight validation error without secret-bearing text."""


def parse_local_dsn(dsn: str) -> dict[str, str | int]:
    """Accept only a PostgreSQL URI targeting the dedicated local C01 port."""
    try:
        parsed = urlsplit(dsn)
        host = parsed.hostname
        port = parsed.port
        database = unquote(parsed.path.lstrip("/"))
    except (ValueError, AttributeError):
        raise PreflightError("DSN non valido") from None
    if (parsed.scheme not in {"postgres", "postgresql"} or host != "127.0.0.1"
            or port != 55496 or port == 54322 or not ALLOWED_DATABASE.fullmatch(database)
            or not parsed.username or parsed.password is None or parsed.query or parsed.fragment):
        raise PreflightError("DSN rifiutato: richiesti 127.0.0.1:55496 e database melpis_c01_*")
    return {"host": host, "port": port, "database": database}


def migration_files(first: int, last: int, *, require_contiguous: bool = True) -> list[Path]:
    if first < 1 or last < first:
        raise PreflightError("Intervallo migrazioni non valido")
    files = sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))
    selected = [path for path in files if first <= int(path.name[:3]) <= last]
    found = [int(path.name[:3]) for path in selected]
    if require_contiguous and found != list(range(first, last + 1)):
        raise PreflightError("Sequenza migrazioni incompleta o duplicata")
    if len(found) != len(set(found)):
        raise PreflightError("Migrazione duplicata")
    return selected


def migration_plan(scenario: str) -> list[Path]:
    if scenario not in {"clean", "upgrade", "replay"}:
        raise PreflightError("Scenario non valido")
    if scenario == "clean":
        return migration_files(1, 66, require_contiguous=False)
    if scenario == "upgrade":
        return migration_files(60, 66)
    return migration_files(60, 66)


def valid_server_identity(address: str | None, port: int, database: str, app: str) -> bool:
    """Accept local PostgreSQL and private container NAT behind host port 55496."""
    try:
        parsed_address = ipaddress.ip_interface(address or "").ip
    except ValueError:
        return False
    if not (parsed_address.is_loopback or parsed_address.is_private):
        return False
    return port == 5432 and bool(ALLOWED_DATABASE.fullmatch(database)) and app == APPLICATION_NAME


def _print_plan(scenario: str, files: list[Path]) -> None:
    print(f"Scenario C01: {scenario}")
    for path in files:
        print(path.relative_to(ROOT).as_posix())
    if scenario != "replay":
        print("Dopo l'applicazione, replay automatico delle migrazioni 060-066.")
    print("Modalità preflight: nessuna connessione o modifica al database.")


async def _verify_identity(conn, scenario: str) -> None:
    identity = await conn.fetchrow("""
        SELECT current_database() AS database, inet_server_addr()::text AS address,
               inet_server_port() AS port, current_setting('application_name') AS app
    """)
    if not valid_server_identity(identity["address"], identity["port"],
                                 identity["database"], identity["app"]):
        raise PreflightError("Identità PostgreSQL non sintetica: nessuna modifica eseguita")
    try:
        marker = await conn.fetchrow("""
            SELECT marker_value, baseline_version
            FROM public.c01_synthetic_marker WHERE marker_key=$1
        """, MARKER_KEY)
    except Exception:
        raise PreflightError("Marker sintetico C01 assente o non leggibile; nessuna modifica eseguita") from None
    if marker is None or marker["marker_value"] != MARKER_VALUE:
        raise PreflightError("Marker sintetico C01 non valido; nessuna modifica eseguita")
    if scenario == "clean" and marker["baseline_version"] != 0:
        raise PreflightError("Scenario clean richiede marker baseline_version=0")
    if scenario == "upgrade" and marker["baseline_version"] != 59:
        raise PreflightError("Scenario upgrade richiede marker baseline_version=59")
    if scenario == "replay" and marker["baseline_version"] != 66:
        raise PreflightError("Scenario replay richiede marker baseline_version=66")
    roles = await conn.fetchrow("""
        SELECT count(*) AS total,
               coalesce(bool_or(rolbypassrls) FILTER (WHERE rolname='service_role'),false) AS service_bypasses_rls
        FROM pg_roles WHERE rolname IN ('anon','authenticated','service_role')
    """)
    if roles["total"] not in (0, 3) or (roles["total"] == 3 and not roles["service_bypasses_rls"]):
        raise PreflightError("Ruoli Supabase sintetici incompleti o non conformi")
    if roles["total"] == 0 and scenario != "clean":
        raise PreflightError("Ruoli Supabase sintetici mancanti; nessuna modifica eseguita")
    if not await conn.fetchval("SELECT current_setting('is_superuser')='on'"):
        raise PreflightError("Il preflight richiede il ruolo amministrativo del solo DB sintetico")
    if scenario == "clean":
        exists = await conn.fetchval("""
            SELECT EXISTS (SELECT 1 FROM pg_tables WHERE schemaname='public'
                           AND tablename <> 'c01_synthetic_marker')
        """)
        if exists:
            raise PreflightError("Scenario clean richiede schema applicativo vuoto")
    if scenario == "upgrade":
        exists = await conn.fetchval("""
            SELECT to_regclass('public.documents') IS NOT NULL
               AND to_regclass('public.document_chunks') IS NOT NULL
               AND to_regclass('public.usage_events') IS NOT NULL
               AND to_regclass('public.auth_access_lifecycle') IS NOT NULL
        """)
        if not exists:
            raise PreflightError("Schema baseline 059 incompleto")
        already_upgraded = await conn.fetchval("""
            SELECT to_regclass('public.provider_attempt_runtime') IS NOT NULL
                OR to_regclass('public.gdpr_operations') IS NOT NULL
                OR to_regclass('public.document_index_requests') IS NOT NULL
                OR EXISTS (SELECT 1 FROM usage_events WHERE event_type='system.quota_trust_epoch')
        """)
        if already_upgraded:
            raise PreflightError("Scenario upgrade richiede baseline 059 non ancora migrato")
    if scenario == "replay":
        exists = await conn.fetchval("""
            SELECT to_regclass('public.provider_attempt_runtime') IS NOT NULL
               AND to_regclass('public.gdpr_operations') IS NOT NULL
               AND to_regclass('public.document_index_requests') IS NOT NULL
               AND EXISTS (SELECT 1 FROM usage_events WHERE event_type='system.quota_trust_epoch')
        """)
        if not exists:
            raise PreflightError("Scenario replay richiede migrazioni 060-066 già applicate")


async def _create_local_auth_stubs(conn) -> None:
    await conn.execute("""
        CREATE SCHEMA IF NOT EXISTS auth;
        CREATE TABLE IF NOT EXISTS auth.users (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(), email TEXT
        );
        CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid AS $$
            SELECT NULL::uuid
        $$ LANGUAGE sql STABLE;
        CREATE OR REPLACE FUNCTION auth.jwt() RETURNS jsonb AS $$
            SELECT '{}'::jsonb
        $$ LANGUAGE sql STABLE;
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='anon') THEN CREATE ROLE anon NOLOGIN; END IF;
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='authenticated') THEN CREATE ROLE authenticated NOLOGIN; END IF;
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='service_role') THEN
                CREATE ROLE service_role NOLOGIN BYPASSRLS;
            END IF;
        END $$;
    """)


async def _apply(conn, path: Path) -> None:
    await conn.execute(path.read_text(encoding="utf-8"))


async def _assert_postconditions(conn, *, historical_baseline: bool) -> None:
    failures = await conn.fetch("""
        WITH required(table_name) AS (VALUES
            ('documents'),('document_chunks'),('faq_cache'),('usage_events'),('messages'))
        SELECT r.table_name FROM required r
        LEFT JOIN pg_class c ON c.oid=to_regclass('public.'||r.table_name)
        WHERE c.oid IS NULL OR NOT c.relrowsecurity
    """)
    if failures:
        raise PreflightError("RLS mancante su una tabella C01 richiesta")
    tenant_scope = await conn.fetchval("""
        SELECT bool_and(attnotnull) FROM pg_attribute
        WHERE attrelid IN ('public.documents'::regclass,'public.document_chunks'::regclass,
                           'public.faq_cache'::regclass)
          AND attname='organization_id' AND attnum>0 AND NOT attisdropped
    """)
    if not tenant_scope:
        raise PreflightError("organization_id diretto e obbligatorio mancante su tabella knowledge")
    client_writes = await conn.fetchval("""
        SELECT bool_or(has_table_privilege(role_name, relation_name, privilege_name))
        FROM (VALUES ('anon'),('authenticated')) AS roles(role_name)
        CROSS JOIN (VALUES ('public.documents'),('public.document_chunks'),
                           ('public.faq_cache'),('public.usage_events'),('public.messages')) AS rels(relation_name)
        CROSS JOIN (VALUES ('INSERT'),('UPDATE'),('DELETE'),('TRUNCATE'),('REFERENCES'),('TRIGGER')) AS p(privilege_name)
    """)
    if client_writes:
        raise PreflightError("Privilege di scrittura client presente su tabella protetta")
    column_writes = await conn.fetchval("""
        SELECT bool_or(has_any_column_privilege(role_name, relation_name, privilege_name))
        FROM (VALUES ('anon'),('authenticated')) AS roles(role_name)
        CROSS JOIN (VALUES ('public.documents'),('public.document_chunks'),
                           ('public.faq_cache'),('public.usage_events'),('public.messages')) AS rels(relation_name)
        CROSS JOIN (VALUES ('INSERT'),('UPDATE'),('REFERENCES')) AS p(privilege_name)
    """)
    if column_writes:
        raise PreflightError("Privilege di scrittura per colonna presente su tabella protetta")
    evidence = await conn.fetchval("""
        SELECT to_regclass('public.document_index_requests') IS NOT NULL
           AND to_regclass('public.provider_attempt_evidence') IS NOT NULL
           AND to_regclass('public.gdpr_operation_events') IS NOT NULL
           AND EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_schema='public' AND table_name='documents' AND column_name='file_bytes')
           AND EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_schema='public' AND table_name='documents' AND column_name='index_intent')
    """)
    if not evidence:
        raise PreflightError("Schema 060-066 incompleto")
    append_only = await conn.fetchval("""
        SELECT NOT has_table_privilege('service_role','public.provider_attempt_evidence','UPDATE')
           AND NOT has_table_privilege('service_role','public.provider_attempt_evidence','DELETE')
           AND NOT has_table_privilege('service_role','public.gdpr_operation_events','UPDATE')
           AND NOT has_table_privilege('service_role','public.gdpr_operation_events','DELETE')
    """)
    if not append_only:
        raise PreflightError("Evidenza append-only con grants non conformi")
    if historical_baseline:
        unknown_bytes = await conn.fetchval("SELECT count(*) FROM documents WHERE file_bytes IS NOT NULL")
        if unknown_bytes:
            raise PreflightError("Byte storici valorizzati senza provenienza verificata")


async def _service_role_capabilities(conn, relations: tuple[str, ...]) -> dict[str, bool]:
    values = {}
    for relation in relations:
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
            values[f"{relation}:{privilege}"] = await conn.fetchval(
                "SELECT has_table_privilege('service_role',$1,$2)", relation, privilege,
            )
    return values


async def _real_client_role_checks(conn) -> dict[str, dict[str, object]]:
    import asyncpg

    protected = ("documents", "document_chunks", "faq_cache", "usage_events", "messages")
    report: dict[str, dict[str, object]] = {}
    for role in ("anon", "authenticated"):
        denied = {}
        visible_documents = None
        can_select_documents = await conn.fetchval(
            "SELECT has_table_privilege($1,'public.documents','SELECT')", role,
        )
        # Temporarily grant only SELECT inside a transaction, then exercise the
        # actual RLS policy as the real role. The outer rollback removes it.
        transaction = conn.transaction()
        await transaction.start()
        try:
            # Older local/Supabase grants may permit the policy's membership
            # lookup. Make only those SELECT grants transient so policy
            # evaluation reaches the tenant predicate; the outer rollback
            # restores the exact production-facing ACL state.
            for relation in ("documents", "document_chunks", "faq_cache", "usage_events", "messages",
                             "organizations", "organization_memberships", "user_profiles"):
                await conn.execute(f"GRANT SELECT ON public.{relation} TO {role}")
            await conn.execute(f"SET LOCAL ROLE {role}")
            async with conn.transaction():
                visible_documents = await conn.fetchval("SELECT count(*) FROM public.documents")
            for table in protected:
                try:
                    async with conn.transaction():
                        await conn.execute(f"DELETE FROM public.{table} WHERE false")
                except asyncpg.InsufficientPrivilegeError:
                    denied[table] = True
                else:
                    denied[table] = False
        finally:
            await transaction.rollback()
        if any(value is not True for value in denied.values()):
            raise PreflightError("Ruolo client reale ha superato una prova DELETE")
        if visible_documents != 0:
            raise PreflightError("RLS ha esposto il documento storico a un ruolo client")
        select_restored = await conn.fetchval(
            "SELECT has_table_privilege($1,'public.documents','SELECT')", role,
        )
        if select_restored != can_select_documents:
            raise PreflightError("Probe RLS ha lasciato grants temporanei")
        report[role] = {
            "delete_denied_by_database": denied,
            "visible_document_count": visible_documents,
            "document_select_denied_by_acl": not can_select_documents,
            "rls_hides_historical_document": visible_documents == 0,
            "temporary_select_grant_rolled_back": select_restored == can_select_documents,
        }
    return report


async def _report_state(conn, scenario: str) -> dict[str, object]:
    file_state = await conn.fetchrow("""
        SELECT count(*) AS total,
               count(*) FILTER (WHERE tipo='upload') AS uploads,
               count(*) FILTER (WHERE file_bytes IS NULL) AS unknown_original_bytes,
               count(*) FILTER (WHERE file_bytes IS NOT NULL) AS attested_original_bytes
        FROM public.documents
    """)
    tenant = await conn.fetchrow("""
        SELECT id, messages_used_this_period FROM public.organizations
        WHERE name='C01 synthetic historical tenant'
    """)
    if tenant is not None:
        if tenant["messages_used_this_period"] != 7:
            raise PreflightError("Counter commerciale modificato rispetto al seed 7")
        if (file_state["total"] != 1 or file_state["uploads"] != 1
                or file_state["unknown_original_bytes"] != 1
                or file_state["attested_original_bytes"] != 0):
            raise PreflightError("Provenienza byte storica non è rimasta fail-closed")
    if tenant is None and file_state["total"] != 0:
        raise PreflightError("Database clean contiene righe fixture inattese")
    role_checks = await _real_client_role_checks(conn)
    backend_after = await _service_role_capabilities(
        conn, ("public.messages", "public.usage_events", "public.documents",
               "public.document_chunks", "public.faq_cache", "public.document_index_requests"),
    )
    expected_doc_grants = {
        f"public.{table}:{privilege}": True
        for table in ("documents", "document_chunks", "faq_cache", "document_index_requests")
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE")
    }
    if any(backend_after.get(key) is not value for key, value in expected_doc_grants.items()):
        raise PreflightError("Grant service_role su tabelle knowledge non conforme")
    bypass_state = await conn.fetch("""
        SELECT rolname,rolbypassrls FROM pg_roles
        WHERE rolname IN ('anon','authenticated','service_role') ORDER BY rolname
    """)
    bypass_report = {row["rolname"]: row["rolbypassrls"] for row in bypass_state}
    if bypass_report != {"anon": False, "authenticated": False, "service_role": True}:
        raise PreflightError("BYPASSRLS non conforme ai ruoli del cluster sintetico")
    return {
        "scenario": scenario,
        "rls_client_real_role_checks": role_checks,
        "service_role_table_privileges": backend_after,
        "role_bypassrls": bypass_report,
        "historical_documents": dict(file_state),
        "commercial_counter": None if tenant is None else {
            "organization_id": str(tenant["id"]),
            "messages_used_this_period": tenant["messages_used_this_period"],
        },
    }


async def run(scenario: str, dsn: str) -> dict[str, object]:
    # parse_local_dsn is checked before importing/connecting via asyncpg.
    parse_local_dsn(dsn)
    if os.getenv("C01_PREFLIGHT_GO") != GO_VALUE:
        raise PreflightError("Manca C01_PREFLIGHT_GO esplicito del responsabile")
    import asyncpg

    conn = await asyncpg.connect(
        dsn,
        server_settings={"application_name": APPLICATION_NAME, "search_path": "public, extensions"},
        timeout=10,
    )
    try:
        # The database identity and pre-seeded marker are verified before DDL.
        await _verify_identity(conn, scenario)
        if scenario == "clean":
            await _create_local_auth_stubs(conn)
            await _apply(conn, ROOT / "src" / "whatsapp" / "schema.sql")
            await _apply(conn, ROOT / "src" / "core" / "db" / "schema.sql")
            await _apply(conn, ROOT / "src" / "core" / "db" / "triggers.sql")
            for path in migration_files(1, 59, require_contiguous=False):
                await _apply(conn, path)
        backend_before = await _service_role_capabilities(conn, ("public.messages", "public.usage_events"))
        selected = migration_files(60, 66)
        if scenario != "replay":
            for path in selected:
                await _apply(conn, path)
        for path in selected:
            await _apply(conn, path)
        await _assert_postconditions(conn, historical_baseline=scenario in {"clean", "upgrade"})
        backend_after = await _service_role_capabilities(conn, ("public.messages", "public.usage_events"))
        if backend_before != backend_after:
            raise PreflightError("Migrazione 060 ha alterato grant preesistenti di service_role")
        result = await _report_state(conn, scenario)
        result["migration_060_service_role_grants_preserved"] = backend_before == backend_after
        result["marker_baseline_version"] = 66
        if scenario in {"clean", "upgrade"}:
            await conn.execute("""
                UPDATE public.c01_synthetic_marker SET baseline_version=66
                WHERE marker_key=$1 AND marker_value=$2
            """, MARKER_KEY, MARKER_VALUE)
        return result
    finally:
        await conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=("clean", "upgrade", "replay"), required=True)
    parser.add_argument("--execute", action="store_true", help="esegue sul solo DB sintetico locale verificato")
    parser.add_argument("--dsn-env", default="C01_TEST_DSN", help="variabile ambiente contenente il DSN locale")
    args = parser.parse_args()
    files = migration_plan(args.scenario)
    if not args.execute:
        _print_plan(args.scenario, files)
        return 0
    dsn = os.getenv(args.dsn_env, "")
    if not dsn:
        parser.error(f"variabile {args.dsn_env} assente")
    try:
        result = asyncio.run(run(args.scenario, dsn))
    except PreflightError as exc:
        parser.exit(2, f"C01 preflight fermato: {exc}\n")
    except Exception as exc:
        # Do not print connection strings, SQL text, or server diagnostics.
        parser.exit(2, f"C01 preflight fallito ({type(exc).__name__}); nessun rollback automatico.\n")
    print("C01 preflight completato e postcondizioni verificate.")
    _append_report(args.dsn_env, result)
    return 0


def _append_report(dsn_env: str, result: dict[str, object]) -> None:
    # Report only the target database label; the DSN and credentials stay private.
    dsn = os.getenv(dsn_env, "")
    target = str(parse_local_dsn(dsn)["database"])
    try:
        existing = json.loads(REPORT_PATH.read_text(encoding="utf-8")) if REPORT_PATH.exists() else {
            "as_of": "2026-10-25", "environment": "local synthetic PostgreSQL; no remote provider verified",
            "runs": [],
        }
    except (OSError, ValueError):
        existing = {"as_of": "2026-10-25", "environment": "local synthetic PostgreSQL; no remote provider verified", "runs": []}
    run_record = {
        "database": target,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        **result,
    }
    existing["runs"] = [entry for entry in existing.get("runs", [])
                        if not (entry.get("database") == target and entry.get("scenario") == result["scenario"])]
    existing["runs"].append(run_record)
    REPORT_PATH.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
