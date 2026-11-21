"""SQL117 contracts on disposable local PostgreSQL, including the old schema."""

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import pytest

from src.core.workers.webhook_inbox_worker import TransactionPool
from src.whatsapp.idempotency import dedup_check, purge_webhook_idempotency


_MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "src/core/db/migrations/117_webhook_receipt_minimization.sql"
)


def _digest(*components):
    framed = b"".join(
        len(part).to_bytes(4, "big") + part
        for part in (component.encode("utf-8") for component in components)
    )
    return hashlib.sha256(framed).digest()


@pytest.mark.migration_limit(115)
async def test_global_receipt_upgrade_preserves_keys_without_raw_fields(pg_pool):
    historical = datetime(2020, 1, 1, tzinfo=timezone.utc)
    tuples = [
        ("wamid.synthetic", "message", ""),
        ("ig:synthetic", "message", ""),
        ("wamid.synthetic", "status", "delivered"),
        ("wamid.synthetic", "status", "custom synthetic é"),
        ("a", "bc", "d"),
        ("ab", "c", "d"),
    ]
    async with pg_pool.acquire() as conn:
        await conn.executemany(
            """INSERT INTO webhook_idempotency(wam_id, resource_type, status_value, created_at)
               VALUES ($1,$2,$3,$4)""",
            [(*components, historical) for components in tuples],
        )
        # New worker + old schema must fail closed, never create a second key.
        with pytest.raises(asyncpg.UndefinedColumnError):
            await dedup_check(TransactionPool(conn), *tuples[0])

        for _ in range(2):
            await conn.execute(_MIGRATION.read_text(encoding="utf-8"))
        rows = await conn.fetch("SELECT * FROM webhook_idempotency")
        assert {row["receipt_key_sha256"] for row in rows} == {
            _digest(*components) for components in tuples
        }
        assert all(row["created_at"] == historical for row in rows)
        assert all(set(row.keys()) == {"receipt_key_sha256", "created_at"} for row in rows)

        # Old worker + new schema must also fail closed.
        with pytest.raises(asyncpg.UndefinedColumnError):
            await conn.execute(
                "INSERT INTO webhook_idempotency(wam_id,resource_type,status_value) VALUES($1,$2,$3)",
                *tuples[0],
            )
        for components in tuples:
            assert not await dedup_check(TransactionPool(conn), *components)
        assert await dedup_check(TransactionPool(conn), "wamid.synthetic", "status", "read")
        assert not await dedup_check(TransactionPool(conn), "wamid.synthetic", "status", "read")
        with pytest.raises(RuntimeError, match="verified conclusion"):
            await purge_webhook_idempotency(conn, days=90)
        assert await conn.fetchval("SELECT COUNT(*) FROM webhook_idempotency") == len(tuples) + 1


async def test_digest_dedup_still_commits_or_rolls_back_with_worker_transaction(pg_pool):
    key = ("wamid.rollback.synthetic", "message", "")
    async with pg_pool.acquire() as conn:
        async with conn.transaction():
            rollback = conn.transaction()
            await rollback.start()
            assert await dedup_check(TransactionPool(conn), *key)
            assert not await dedup_check(TransactionPool(conn), *key)
            await rollback.rollback()
            assert await dedup_check(TransactionPool(conn), *key)
        assert not await dedup_check(TransactionPool(conn), *key)
        assert await conn.fetchval("SELECT receipt_key_sha256 FROM webhook_idempotency") == _digest(*key)
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute("INSERT INTO webhook_idempotency(receipt_key_sha256) VALUES($1)", b"short")
        # The pure digest helper does not grant client access to global keys.
        for role in ("anon", "authenticated"):
            assert not await conn.fetchval(
                "SELECT has_function_privilege($1,'public.webhook_receipt_key_sha256(text,text,text)','EXECUTE')",
                role,
            )
