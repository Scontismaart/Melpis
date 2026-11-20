from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.core import retention_job


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_args):
        return False


class _Pool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return _AsyncContext(self.connection)


class _Connection:
    def __init__(self):
        self.executed = []

    def transaction(self):
        return _AsyncContext(self)

    async def execute(self, query, *_args):
        self.executed.append(query)

    async def fetchval(self, query, *_args):
        assert "to_regclass('public.gdpr_retention_holds')" in query
        return False

    async def fetchrow(self, query, *_args):
        if "category='booking_conversation_content'" in query:
            return None
        assert "FROM gdpr_policies" in query
        return {
            "version": "synthetic-policy",
            "retention_days": 30,
            "erasure_allowed": True,
            "legal_hold": False,
            "valid": True,
        }

    async def fetch(self, *_args):
        raise AssertionError("message content must not be aged without verified provenance")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("schema", "category", "blocked_reason"),
    [
        ("absent", "messages_content", "legacy_message_provenance_required"),
        ("ready", "messages_content", "c24_retention_controller_required"),
        ("ready", "booking_conversation_content", "booking_message_provenance_required"),
    ],
)
async def test_unmanaged_content_retention_blocks_unverified_age_and_keeps_export_expiry(
    monkeypatch, caplog, schema, category, blocked_reason,
):
    monkeypatch.setenv("GDPR_RETENTION_EXECUTION_ENABLED", "true")
    expire_exports = AsyncMock(return_value=2)
    monkeypatch.setattr(retention_job, "_expired_exports", expire_exports)
    monkeypatch.setattr(
        retention_job,
        "_retention_policies",
        AsyncMock(return_value=[{
            "organization_id": uuid4(),
            "category": category,
            "version": "synthetic-policy",
            "retention_days": 30,
            "legal_hold": False,
            "erasure_allowed": True,
            "valid": True,
        }]),
    )
    monkeypatch.setattr(
        retention_job, "conversation_schema", AsyncMock(return_value=schema),
    )
    connection = _Connection()

    counts = await retention_job.run_retention(_Pool(connection))

    assert counts == {
        "exports_expired": 2,
        "messages_redacted": 0,
        "simulator_purged": 0,
    }
    expire_exports.assert_awaited_once()
    assert any("pg_advisory_xact_lock_shared" in query for query in connection.executed)
    assert not any("UPDATE messages" in query for query in connection.executed)
    assert blocked_reason in caplog.text
