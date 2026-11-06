"""Explicitly targeted SQL074 lifecycle continuation, disabled by default.

The caller supplies reviewed tenant/record pairs and an owner-capable pool. This
module neither discovers targets nor grants lifecycle privileges to a worker.
SQL074 remains the authority for policy, hold, lineage and concurrency checks.
"""
import os
from itertools import islice


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
        (message_targets, "c24_purge_source", "messages_purged"),
        (conversation_targets, "c24_expire_idle", "conversations_expired"),
        (receipt_targets, "c24_cleanup_receipt", "receipts_cleaned"),
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
                    applied = await conn.fetchval(
                        f"SELECT public.{function}($1::uuid,$2::uuid)",
                        organization_id, record_id,
                    )
            counts[counter] += int(applied is True)
    return counts
