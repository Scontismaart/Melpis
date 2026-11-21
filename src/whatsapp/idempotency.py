WEBHOOK_IDEMPOTENCY_SQL = """
    INSERT INTO public.webhook_idempotency (receipt_key_sha256)
    VALUES (public.webhook_receipt_key_sha256($1, $2, $3))
    ON CONFLICT (receipt_key_sha256) DO NOTHING
    RETURNING receipt_key_sha256
"""


async def dedup_check(pool, wam_id: str, resource_type: str, status_value: str) -> bool:
    """Atomic idempotency check via INSERT ON CONFLICT DO NOTHING.

    The database stores only the framed tuple's digest, never raw provider IDs
    or status text. The digest remains pseudonymized replay evidence.
    Returns True if this is a new event (lock acquired), False if duplicate."""
    row = await pool.fetchrow(
        WEBHOOK_IDEMPOTENCY_SQL, wam_id, resource_type, status_value
    )
    return row is not None


async def purge_webhook_idempotency(pool, days: int = 7) -> int:
    """Retired age-only path; creation is not verified request conclusion."""
    raise RuntimeError("Webhook receipt expiry requires verified conclusion and replay protection")
