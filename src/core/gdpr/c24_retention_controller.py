"""Least-privilege C2.4 retention controller, disabled by default.

The dedicated operator can process reviewed targets or a bounded automatic
database sweep. SQL remains authoritative for policy, holds, lineage and locks.
"""
import argparse
import asyncio
import json
import os
from itertools import islice
from pathlib import Path
from uuid import UUID

import asyncpg


def _enabled():
    return (os.getenv("GDPR_RETENTION_EXECUTION_ENABLED") == "true"
            and os.getenv("GDPR_C24_OWNER_RETENTION_ENABLED") == "true")


async def _assume_operator(conn):
    # SET ROLE alone must not allow a privileged login to impersonate this
    # narrow capability. Validate the original session before each batch.
    allowed_login = await conn.fetchval("""SELECT EXISTS (
        SELECT 1 FROM pg_roles r WHERE r.rolname=session_user
          AND NOT (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls)
          AND pg_has_role(r.oid, 'melpis_c24_retention', 'MEMBER')
          AND NOT EXISTS (SELECT 1 FROM pg_namespace n
            WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
              AND has_schema_privilege(r.oid,n.oid,'CREATE'))
          AND NOT EXISTS (SELECT 1 FROM pg_roles granted
            WHERE granted.oid <> r.oid AND granted.oid <> 'melpis_c24_retention'::regrole
              AND pg_has_role(r.oid, granted.oid, 'MEMBER'))
          AND NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
              AND c.relkind IN ('r','p','v','m','f')
              AND (has_table_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                OR has_any_column_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,REFERENCES')))
          AND NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' AND p.prosecdef
              AND NOT ((p.proname='c24_retention_operator_allowed'
                        AND pg_get_function_identity_arguments(p.oid)='')
                       OR (p.proname='c24_run_retention_target'
                        AND pg_get_function_identity_arguments(p.oid)='p_kind text, p_org uuid, p_id uuid'))
              AND has_function_privilege(r.oid,p.oid,'EXECUTE'))
    )""")
    if not allowed_login:
        raise PermissionError("c24_retention_operator_required")
    await conn.execute("SET LOCAL ROLE melpis_c24_retention")
    if not await conn.fetchval("SELECT public.c24_retention_operator_allowed()"):
        raise PermissionError("c24_retention_operator_required")


async def run_c24_retention_targets(
    pool, *, inbox_targets=(), message_targets=(), conversation_targets=(), receipt_targets=(),
    suppression_targets=(), suppression_audit_targets=(),
):
    """Attempt at most 500 explicitly supplied targets, each in its own transaction.

    A target is ``(organization_id, record_id)``. False results are ineligible,
    not failures; database errors propagate so callers cannot mistake a partial
    run for a completed one. SQL074 functions are idempotent on retry.
    """
    counts = {
        "inboxes_purged": 0,
        "messages_purged": 0,
        "conversations_expired": 0,
        "receipts_cleaned": 0,
        "suppressions_cleaned": 0,
        "suppression_audit_rows_expired": 0,
    }
    if not _enabled():
        return counts
    # Bound iteration as well as database work, including generator inputs.
    groups = (
        (inbox_targets, "inbox", "inboxes_purged"),
        (message_targets, "message", "messages_purged"),
        (conversation_targets, "conversation", "conversations_expired"),
        (receipt_targets, "receipt", "receipts_cleaned"),
        (suppression_targets, "suppression", "suppressions_cleaned"),
        (suppression_audit_targets, "suppression_audit", "suppression_audit_rows_expired"),
    )
    remaining = 500
    batches = []
    for targets, function, counter in groups:
        batch = tuple(islice(targets, remaining + 1))
        if len(batch) > remaining:
            raise ValueError("c24_retention_target_limit_exceeded")
        remaining -= len(batch)
        batches.append((batch, function, counter))
    for targets, function, counter in batches:
        for organization_id, record_id in targets:
            async with pool.acquire() as conn:
                async with conn.transaction(isolation="read_committed"):
                    await _assume_operator(conn)
                    applied = await conn.fetchval(
                        "SELECT public.c24_run_retention_target($1::text,$2::uuid,$3::uuid)",
                        function, organization_id, record_id,
                    )
            counts[counter] += int(applied is True)
    return counts


_TARGET_KEYS = (
    "inbox_targets", "message_targets", "conversation_targets", "receipt_targets",
    "suppression_targets", "suppression_audit_targets",
)


def read_target_manifest(path: Path):
    """Validate all reviewed targets before opening a database connection."""
    with path.open("rb") as manifest:
        raw = manifest.read(128 * 1024 + 1)
    if len(raw) > 128 * 1024:
        raise ValueError("c24_retention_manifest_too_large")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.keys() - set(_TARGET_KEYS):
        raise ValueError("c24_retention_manifest_invalid")
    batches = {}
    count = 0
    for key in _TARGET_KEYS:
        rows = payload.get(key, [])
        if not isinstance(rows, list):
            raise ValueError("c24_retention_manifest_invalid")
        count += len(rows)
        if count > 500:
            raise ValueError("c24_retention_target_limit_exceeded")
        batch = []
        for row in rows:
            if not isinstance(row, list) or len(row) != 2 or any(not isinstance(value, str) for value in row):
                raise ValueError("c24_retention_manifest_invalid")
            batch.append((UUID(row[0]), UUID(row[1])))
        batches[key] = batch
    return batches


async def _run_manifest(path: Path):
    if not _enabled():
        raise SystemExit("C2.4 retention is disabled.")
    targets = read_target_manifest(path)
    dsn = os.getenv("GDPR_C24_OWNER_RETENTION_DSN")
    if not dsn:
        raise SystemExit("C2.4 retention requires a dedicated connection.")
    try:
        async with asyncpg.create_pool(dsn=dsn, min_size=1, max_size=1) as pool:
            return await run_c24_retention_targets(pool, **targets)
    except Exception as exc:
        # Driver errors can contain connection or row details.
        raise SystemExit("C2.4 retention stopped after a database error; error type: "
                         + type(exc).__name__) from None


async def run_c24_retention_sweep(pool):
    """Run one cursor-backed batch of at most 100 database-selected targets.

    A false ``batch_clear`` means at least one target was deferred. The cursor
    still advances so a held or uncertain record cannot starve other tenants.
    """
    if not _enabled() or os.getenv("GDPR_C24_AUTO_RETENTION_ENABLED") != "true":
        return {"enabled": False, "batch_clear": False}
    async with pool.acquire() as conn, conn.transaction(isolation="read_committed"):
        await _assume_operator(conn)
        zero = UUID(int=0)
        clear = await conn.fetchval(
            "SELECT public.c24_run_retention_target($1::text,$2::uuid,$3::uuid)",
            "sweep", zero, zero,
        )
        hotel_clear = await conn.fetchval(
            "SELECT public.c24_run_retention_target($1::text,$2::uuid,$3::uuid)",
            "hotel_sweep", zero, zero,
        )
    return {"enabled": True, "batch_clear": clear is True and hotel_clear is True}


async def _run_auto(*, loop=False):
    if not _enabled() or os.getenv("GDPR_C24_AUTO_RETENTION_ENABLED") != "true":
        raise SystemExit("C2.4 automatic retention is disabled.")
    dsn = os.getenv("GDPR_C24_OWNER_RETENTION_DSN")
    if not dsn:
        raise SystemExit("C2.4 retention requires a dedicated connection.")
    try:
        interval = int(os.getenv("GDPR_C24_RETENTION_INTERVAL_SECONDS", "3600"))
        if not 60 <= interval <= 86400:
            raise ValueError
    except ValueError:
        raise SystemExit("C2.4 retention interval is invalid.") from None
    try:
        async with asyncpg.create_pool(dsn=dsn, min_size=1, max_size=1) as pool:
            if not loop:
                return await run_c24_retention_sweep(pool)
            while _enabled() and os.getenv("GDPR_C24_AUTO_RETENTION_ENABLED") == "true":
                print(json.dumps(await run_c24_retention_sweep(pool), sort_keys=True), flush=True)
                await asyncio.sleep(interval)
    except Exception as exc:
        raise SystemExit("C2.4 retention stopped after a database error; error type: "
                         + type(exc).__name__) from None


def main():
    parser = argparse.ArgumentParser(description="Run gated C2.4 retention.")
    parser.add_argument("manifest", nargs="?", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--sweep", action="store_true")
    mode.add_argument("--loop", action="store_true")
    args = parser.parse_args()
    if bool(args.manifest) == bool(args.sweep or args.loop):
        parser.error("Choose a reviewed manifest, --sweep or --loop.")
    try:
        counts = asyncio.run(_run_auto(loop=args.loop) if args.sweep or args.loop
                             else _run_manifest(args.manifest))
    except (ValueError, OSError):
        parser.exit(2, "C2.4 retention manifest is invalid.\n")
    if counts is not None:
        print(json.dumps(counts, sort_keys=True))


if __name__ == "__main__":
    main()
