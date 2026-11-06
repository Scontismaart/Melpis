"""Explicitly targeted SQL074 lifecycle continuation, disabled by default.

The caller supplies reviewed tenant/record pairs and a dedicated retention-operator pool. This
module neither discovers targets nor grants lifecycle privileges to a worker.
SQL074 remains the authority for policy, hold, lineage and concurrency checks.
"""
import argparse
import asyncio
import json
import os
from itertools import islice
from pathlib import Path
from uuid import UUID

import asyncpg


async def run_c24_retention_targets(pool, *, message_targets=(), conversation_targets=(), receipt_targets=()):
    """Attempt at most 500 explicitly supplied targets, each in its own transaction.

    A target is ``(organization_id, record_id)``. False results are ineligible,
    not failures; database errors propagate so callers cannot mistake a partial
    run for a completed one. SQL074 functions are idempotent on retry.
    """
    counts = {"messages_purged": 0, "conversations_expired": 0, "receipts_cleaned": 0}
    if os.getenv("GDPR_C24_OWNER_RETENTION_ENABLED") != "true":
        return counts
    # Bound iteration as well as database work, including generator inputs.
    groups = (
        (message_targets, "message", "messages_purged"),
        (conversation_targets, "conversation", "conversations_expired"),
        (receipt_targets, "receipt", "receipts_cleaned"),
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
                    if not await conn.fetchval("SELECT public.c24_retention_operator_allowed()"):
                        raise PermissionError("c24_retention_operator_required")
                    applied = await conn.fetchval(
                        "SELECT public.c24_run_retention_target($1::text,$2::uuid,$3::uuid)",
                        function, organization_id, record_id,
                    )
            counts[counter] += int(applied is True)
    return counts


_TARGET_KEYS = ("message_targets", "conversation_targets", "receipt_targets")


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
    if os.getenv("GDPR_C24_OWNER_RETENTION_ENABLED") != "true":
        raise SystemExit("C2.4 retention is disabled.")
    targets = read_target_manifest(path)
    dsn = os.getenv("GDPR_C24_OWNER_RETENTION_DSN")
    if not dsn:
        raise SystemExit("C2.4 retention requires a dedicated connection.")
    try:
        async with asyncpg.create_pool(dsn=dsn, min_size=1, max_size=1) as pool:
            return await run_c24_retention_targets(pool, **targets)
    except Exception:
        # Driver errors can contain connection or row details.
        raise SystemExit("C2.4 retention stopped after a database error.") from None


def main():
    parser = argparse.ArgumentParser(description="Run C2.4 retention for a reviewed target manifest.")
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    try:
        counts = asyncio.run(_run_manifest(args.manifest))
    except (ValueError, OSError):
        parser.exit(2, "C2.4 retention manifest is invalid.\n")
    print(json.dumps(counts, sort_keys=True))


if __name__ == "__main__":
    main()
