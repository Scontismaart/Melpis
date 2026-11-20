"""Historical message reconciliation leaves missing database facts untouched."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import uuid4

import pytest

from src.core.gdpr.restore_checkpoint import RestoreBlocked
from tests.core.bookings.test_conversation_state_postgres import state_env  # noqa: F401
from tests.core.gdpr.test_c24_retention_sweep import approve_messages


@pytest.mark.asyncio
async def test_external_evidence_reconciles_only_the_exact_historical_message(state_env):
    from src.core.gdpr.legacy_message_reconciliation import attest_message_lineage, reconcile_legacy_message

    pool, org, message, conversation = (state_env[name] for name in ("pool", "org", "message", "conv"))
    subject = await pool.fetchval("SELECT c24_original_subject_id FROM messages WHERE organization_id=$1 AND id=$2", org, message)
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("ALTER TABLE messages DISABLE TRIGGER c24_source_received")
        await conn.execute("UPDATE messages SET c24_received_at=NULL WHERE organization_id=$1 AND id=$2", org, message)
        await conn.execute("ALTER TABLE messages ENABLE TRIGGER c24_source_received")
    await approve_messages(pool, org)
    keys = dict(verification_key=bytes(range(32)))
    observed_at = datetime.now(timezone.utc)
    recorded_at = observed_at - timedelta(days=91)
    payload = dict(version="gdpr-legacy-message-lineage-v1", case_id=str(uuid4()), organization_id=str(org),
        message_id=str(message), conversation_id=str(conversation), original_subject_id=str(subject),
        received_at=recorded_at.isoformat(), authority_reference="qa:historical-approval",
        evidence_sha256="a" * 64)
    attestation = attest_message_lineage(payload, **keys)
    async with pool.acquire() as conn, conn.transaction(readonly=True):
        gap = await reconcile_legacy_message(conn, org, message, audit_at=observed_at)
        assert gap["state"] == "contemporaneous_evidence_required"
        assert gap["missing"] == ("received_at",)
        proven = await reconcile_legacy_message(conn, org, message, audit_at=observed_at,
            attestation=attestation, independent_sha256=sha256(attestation).hexdigest(), **keys)
        assert proven["state"] == "independently_attested_lineage"
        assert proven["due_under_current_policy"] is True and proven["deletion_authorized"] is False
        assert proven["received_at"] == recorded_at.isoformat()
        wrong = attest_message_lineage(dict(payload, original_subject_id=str(uuid4())), **keys)
        with pytest.raises(RestoreBlocked, match="disagrees"):
            await reconcile_legacy_message(conn, org, message, audit_at=observed_at,
                attestation=wrong, independent_sha256=sha256(wrong).hexdigest(), **keys)
    assert await pool.fetchval("SELECT c24_received_at IS NULL FROM messages WHERE organization_id=$1 AND id=$2", org, message)
    await pool.execute("""INSERT INTO gdpr_retention_holds(organization_id,hold_id,category,scope,scope_id,
        reason_code,authority_reference,approved_by,approved_at,review_by)
        VALUES($1,$2,'messages_content','record',$3,'litigation','qa:lineage','qa',
                clock_timestamp(),clock_timestamp()+interval '7 days')""", org, uuid4(), message)
    async with pool.acquire() as conn, conn.transaction(readonly=True):
        held = await reconcile_legacy_message(conn, org, message, attestation=attestation,
            independent_sha256=sha256(attestation).hexdigest(), **keys)
        assert held["held"] is True and held["due_under_current_policy"] is None
        with pytest.raises(RestoreBlocked, match="Authenticated independent"):
            await reconcile_legacy_message(conn, org, message, attestation=attestation[:-1],
                independent_sha256=sha256(attestation[:-1]).hexdigest(), **keys)
