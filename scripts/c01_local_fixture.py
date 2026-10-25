"""Create exactly the two authorized local C01 fixture databases, never drop."""

from __future__ import annotations

import asyncio
import ipaddress
import os
from urllib.parse import unquote, urlsplit, urlunsplit
import uuid

import asyncpg

from c01_migration_preflight import (
    APPLICATION_NAME,
    MARKER_KEY,
    MARKER_VALUE,
    ROOT,
    PreflightError,
    _apply,
    _create_local_auth_stubs,
    migration_files,
    valid_server_identity,
)


FIXTURE_DATABASES = (
    ("melpis_c01_clean_20261025", 0),
    ("melpis_c01_upgrade_20261025", 0),
)
FIXTURE_APPLICATION = "melpis-c01-preflight"


def _admin_dsn_info(dsn: str) -> dict[str, str | int]:
    try:
        parsed = urlsplit(dsn)
        port = parsed.port
        database = unquote(parsed.path.lstrip("/"))
    except (ValueError, AttributeError):
        raise PreflightError("DSN admin non valido") from None
    if (parsed.scheme not in {"postgres", "postgresql"}
            or parsed.hostname != "127.0.0.1" or port != 55496
            or database != "postgres" or not parsed.username or parsed.password is None
            or parsed.query or parsed.fragment):
        raise PreflightError("DSN admin rifiutato: solo postgres su 127.0.0.1:55496")
    return {"host": parsed.hostname, "port": port, "database": database}


def _target_dsn(admin_dsn: str, database: str) -> str:
    parsed = urlsplit(admin_dsn)
    return urlunsplit((parsed.scheme, parsed.netloc, f"/{database}", "", ""))


async def _connect_verified(dsn: str, expected_database: str):
    conn = await asyncpg.connect(
        dsn,
        server_settings={"application_name": APPLICATION_NAME, "search_path": "public, extensions"},
        timeout=10,
    )
    identity = await conn.fetchrow("""
        SELECT current_database() AS database, inet_server_addr()::text AS address,
               inet_server_port() AS port, current_setting('application_name') AS app,
               current_setting('is_superuser') AS superuser
    """)
    if expected_database == "postgres":
        try:
            address = ipaddress.ip_interface(identity["address"] or "").ip
        except ValueError:
            await conn.close()
            raise PreflightError("Cluster PostgreSQL non locale") from None
        valid = (identity["database"] == "postgres" and identity["port"] == 5432
                 and (address.is_private or address.is_loopback)
                 and identity["app"] == APPLICATION_NAME and identity["superuser"] == "on")
    else:
        valid = (valid_server_identity(identity["address"], identity["port"],
                                       identity["database"], identity["app"])
                 and identity["database"] == expected_database
                 and identity["superuser"] == "on")
    if not valid:
        await conn.close()
        raise PreflightError("Identità cluster/database non conforme; nessun DDL eseguito")
    return conn


async def _marker(conn, version: int) -> None:
    present = await conn.fetchval("SELECT to_regclass('public.c01_synthetic_marker') IS NOT NULL")
    if present:
        raise PreflightError("Marker già presente; fixture esistente, stop senza riuso")
    await conn.execute("""
        CREATE TABLE public.c01_synthetic_marker (
            marker_key text PRIMARY KEY,
            marker_value text NOT NULL,
            baseline_version integer NOT NULL
        )
    """)
    await conn.execute("""
        INSERT INTO public.c01_synthetic_marker(marker_key,marker_value,baseline_version)
        VALUES ($1,$2,$3)
    """, MARKER_KEY, MARKER_VALUE, version)


async def _prepare_upgrade_database(conn) -> uuid.UUID:
    await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto; CREATE EXTENSION IF NOT EXISTS vector")
    await conn.execute("CREATE SCHEMA IF NOT EXISTS extensions")
    await _create_local_auth_stubs(conn)
    await _apply(conn, ROOT / "src" / "whatsapp" / "schema.sql")
    await _apply(conn, ROOT / "src" / "core" / "db" / "schema.sql")
    await _apply(conn, ROOT / "src" / "core" / "db" / "triggers.sql")
    for path in migration_files(1, 59, require_contiguous=False):
        await _apply(conn, path)

    organization_id = uuid.UUID("95dafd8e-27df-4d2d-9e61-8a8c5ef2f90e")
    await conn.execute("""
        INSERT INTO public.organizations
            (id,name,plan,subscription_status,messages_used_this_period,messages_limit)
        VALUES ($1,'C01 synthetic historical tenant','pro','active',7,100)
    """, organization_id)
    document_id = uuid.UUID("60e6c597-8c5e-4c89-bfd3-73552ce5dbf6")
    await conn.execute("""
        INSERT INTO public.documents(organization_id,id,nome,tipo,fonte,stato,metadata)
        VALUES ($1,$2,'C01 historical upload','upload','fixture','indicizzata',
                '{"bytes":1234,"fixture":"legacy-unknown-original"}'::jsonb)
    """, organization_id, document_id)
    vector = "[1," + ",".join(["0"] * 383) + "]"
    await conn.execute("""
        INSERT INTO public.document_chunks(organization_id,document_id,chunk_index,content,embedding,metadata)
        VALUES ($1,$2,0,'synthetic historical chunk',$3::vector,'{}'::jsonb)
    """, organization_id, document_id, vector)
    count = await conn.fetchval(
        "SELECT messages_used_this_period FROM public.organizations WHERE id=$1", organization_id,
    )
    docs = await conn.fetchval(
        "SELECT count(*) FROM public.documents WHERE organization_id=$1 AND tipo='upload'", organization_id,
    )
    if count != 7 or docs != 1:
        raise PreflightError("Seed storico sintetico non verificato")
    await conn.execute("""
        UPDATE public.c01_synthetic_marker SET baseline_version=59
        WHERE marker_key=$1 AND marker_value=$2
    """, MARKER_KEY, MARKER_VALUE)
    return organization_id


async def prepare(admin_dsn: str) -> None:
    _admin_dsn_info(admin_dsn)
    admin = await _connect_verified(admin_dsn, "postgres")
    try:
        # Check both exact targets before creating either; never reset/reuse.
        existing = await admin.fetchval("""
            SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname=$1)
                OR EXISTS (SELECT 1 FROM pg_database WHERE datname=$2)
        """, FIXTURE_DATABASES[0][0], FIXTURE_DATABASES[1][0])
        if existing:
            raise PreflightError("Uno dei database fixture esiste già; stop senza riuso né DROP")
        for database, _ in FIXTURE_DATABASES:
            await admin.execute(f'CREATE DATABASE "{database}"')
    finally:
        await admin.close()

    for database, version in FIXTURE_DATABASES:
        conn = await _connect_verified(_target_dsn(admin_dsn, database), database)
        try:
            await _marker(conn, version)
            if database == "melpis_c01_upgrade_20261025":
                await _prepare_upgrade_database(conn)
        finally:
            await conn.close()
    print("Fixture preparato: clean marker=0; upgrade baseline=59 con 1 tenant, 1 upload storico e counter=7.")
    print("Nessun database è stato eliminato. Ora eseguire clean e upgrade con il preflight autorizzato.")


def main() -> int:
    dsn = os.getenv("C01_ADMIN_DSN", "")
    if not dsn:
        raise SystemExit("C01_ADMIN_DSN assente; nessun .env viene letto")
    try:
        asyncio.run(prepare(dsn))
    except PreflightError as exc:
        raise SystemExit(f"Fixture fermato: {exc}") from None
    except Exception as exc:
        raise SystemExit(f"Fixture fallito ({type(exc).__name__}); nessun DROP automatico") from None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
