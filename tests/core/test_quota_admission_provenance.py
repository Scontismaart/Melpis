"""Fresh-DB contract for inactive legacy quota-admission provenance."""

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.core.billing.suspension import AIGenerationBlocked
from src.core.db.quota_provenance import (
    TRUST_MARKER_ID,
    _PROTOCOL_FIELDS,
    legacy_admission_id,
    load_legacy_admission,
)


def _metadata(value):
    return json.loads(value) if isinstance(value, str) else value


async def _seed_case(conn, *, used=150, subscription_status="trialing"):
    now = await conn.fetchval("SELECT clock_timestamp()")
    organization_id, contact_id, conversation_id, message_id = (uuid4() for _ in range(4))
    trial_start = now - timedelta(minutes=1)
    trial_end = trial_start + timedelta(days=7)
    processing_at = now - timedelta(seconds=1)
    billed_at = now - timedelta(seconds=2)
    lease_expires_at = processing_at + timedelta(seconds=30)
    claim_uuid = uuid4()
    epoch = await conn.fetchval(
        "SELECT metadata->>'trust_epoch' FROM public.usage_events WHERE id=$1",
        TRUST_MARKER_ID,
    )
    await conn.execute(
        """INSERT INTO public.organizations
               (id, name, subscription_status, subscription_id, trial_start, trial_end,
                messages_limit, messages_used_this_period, ai_accounting_blocked)
             VALUES ($1, 'Synthetic quota provenance', $2, NULL, $3, $4, 150, $5, FALSE)""",
        organization_id, subscription_status, trial_start, trial_end, used,
    )
    await conn.execute(
        """INSERT INTO public.contacts (id, organization_id, phone_number)
             VALUES ($1, $2, $3)""",
        contact_id, organization_id, f"quota-{contact_id}@example.test",
    )
    await conn.execute(
        """INSERT INTO public.conversations (id, organization_id, contact_id)
             VALUES ($1, $2, $3)""",
        conversation_id, organization_id, contact_id,
    )
    await conn.execute(
        """INSERT INTO public.messages
               (id, organization_id, conversation_id, direction, message_type, content,
                status, claimed_at, billed_at, processing_at)
             VALUES ($1, $2, $3, 'inbound', 'text', '{}'::jsonb,
                     'processing', $4, $5, $6)""",
        message_id, organization_id, conversation_id, processing_at,
        billed_at, processing_at,
    )
    metadata = {
        "protocol_version": 1,
        "organization_id": str(organization_id),
        "message_id": str(message_id),
        "trust_epoch": epoch,
        "claim_uuid": str(claim_uuid),
        "period_kind": "trial",
        "period_anchor": trial_start.isoformat(),
        "subscription_id": None,
        "charged_ordinal": 150,
        "limit_at_charge": 150,
        "billed_at": billed_at.isoformat(),
        "processing_at": processing_at.isoformat(),
        "lease_expires_at": lease_expires_at.isoformat(),
    }
    assert set(metadata) == _PROTOCOL_FIELDS
    await conn.execute(
        """INSERT INTO public.usage_events(id, organization_id, event_type, quantity, metadata)
             VALUES ($1, $2, 'legacy_quota_admission', 0, $3::jsonb)""",
        legacy_admission_id(organization_id, message_id), organization_id, json.dumps(metadata),
    )
    return {
        "organization_id": organization_id,
        "message_id": message_id,
        "trial_start": trial_start,
        "trial_end": trial_end,
        "billed_at": billed_at,
        "processing_at": processing_at,
        "claim_uuid": claim_uuid,
    }


@pytest.mark.asyncio
async def test_fresh_db_legacy_provenance_fails_closed_and_150th_is_nonmutating(pg_pool):
    async with pg_pool.acquire() as conn:
        outer = conn.transaction()
        await outer.start()
        try:
            base = await _seed_case(conn)
            before = await conn.fetchrow(
                """SELECT o.messages_used_this_period, m.billed_at, m.processing_at,
                          m.ai_reply_cache, m.sent_at, m.replied_at,
                          (SELECT count(*) FROM public.usage_events u
                            WHERE u.organization_id=o.id AND u.event_type='legacy_quota_admission') AS admissions
                     FROM public.organizations o JOIN public.messages m ON m.organization_id=o.id
                    WHERE o.id=$1 AND m.id=$2""",
                base["organization_id"], base["message_id"],
            )
            proof, billing, context = await load_legacy_admission(
                conn, organization_id=base["organization_id"], message_id=base["message_id"],
                claim_uuid=base["claim_uuid"],
            )
            assert proof.charged_ordinal == proof.limit_at_charge == 150
            assert billing["messages_used_this_period"] == billing["messages_limit"] == 150
            assert context["period_kind"] == "trial"
            assert context["period_anchor"] == base["trial_start"]
            assert context["period_end"] == base["trial_end"]

            # Loading and accepting the already charged 150th item never writes or refunds.
            from src.core.billing.quota_admission import assert_ai_generation_allowed_with_legacy_admission

            assert_ai_generation_allowed_with_legacy_admission(billing, proof, **context)
            after = await conn.fetchrow(
                """SELECT o.messages_used_this_period, m.billed_at, m.processing_at,
                          m.ai_reply_cache, m.sent_at, m.replied_at,
                          (SELECT count(*) FROM public.usage_events u
                            WHERE u.organization_id=o.id AND u.event_type='legacy_quota_admission') AS admissions
                     FROM public.organizations o JOIN public.messages m ON m.organization_id=o.id
                    WHERE o.id=$1 AND m.id=$2""",
                base["organization_id"], base["message_id"],
            )
            assert after == before

            async def denied(change=None, *, organization_id=None, message_id=None, claim_uuid=...):
                savepoint = conn.transaction()
                await savepoint.start()
                try:
                    row = await _seed_case(conn)
                    if change is not None:
                        await change(row)
                    with pytest.raises(AIGenerationBlocked):
                        await load_legacy_admission(
                            conn,
                            organization_id=(organization_id(row) if callable(organization_id)
                                            else organization_id or row["organization_id"]),
                            message_id=message_id or row["message_id"],
                            claim_uuid=row["claim_uuid"] if claim_uuid is ... else claim_uuid,
                        )
                finally:
                    await savepoint.rollback()

            async def set_org(field, value):
                async def apply(row):
                    await conn.execute(
                        f"UPDATE public.organizations SET {field}=$2 WHERE id=$1",
                        row["organization_id"], value,
                    )
                return apply

            await denied(await set_org("messages_used_this_period", 151))
            # A different tenant cannot use the first tenant's message/ledger pair.
            async def foreign_org(row):
                other_id = uuid4()
                await conn.execute(
                    "INSERT INTO public.organizations(id,name) VALUES ($1,'Other tenant')", other_id,
                )
                row["other_org_id"] = other_id
            await denied(foreign_org, organization_id=lambda row: row["other_org_id"])
            await denied(message_id=uuid4())
            await denied(claim_uuid=uuid4())
            await denied(claim_uuid=None)
            await denied(claim_uuid="not-a-uuid")
            await denied(claim_uuid=1)

            async def delete_marker(_row):
                await conn.execute("DELETE FROM public.usage_events WHERE id=$1", TRUST_MARKER_ID)
            await denied(delete_marker)

            async def move_marker(_row):
                await conn.execute(
                    "UPDATE public.usage_events SET id=$2 WHERE id=$1",
                    TRUST_MARKER_ID, uuid4(),
                )
            await denied(move_marker)

            async def stale_epoch(row):
                entry = legacy_admission_id(row["organization_id"], row["message_id"])
                metadata = _metadata(await conn.fetchval(
                    "SELECT metadata FROM public.usage_events WHERE id=$1", entry,
                ))
                metadata["trust_epoch"] = str(uuid4())
                await conn.execute(
                    "UPDATE public.usage_events SET metadata=$2::jsonb WHERE id=$1",
                    entry, json.dumps(metadata),
                )
            await denied(stale_epoch)

            async def grant_sql(sql):
                async def apply(_row):
                    await conn.execute(sql)
                return apply
            await denied(await grant_sql("GRANT INSERT ON public.messages TO anon"))
            await denied(await grant_sql("GRANT INSERT (content) ON public.messages TO authenticated"))
            await denied(await grant_sql("GRANT UPDATE ON public.messages TO PUBLIC"))

            async def inherited_write(row):
                role = f"quota_provenance_parent_{uuid4().hex}"
                await conn.execute(f'CREATE ROLE "{role}" NOLOGIN')
                await conn.execute(f'GRANT "{role}" TO authenticated')
                await conn.execute(f'GRANT UPDATE ON public.messages TO "{role}"')
            await denied(inherited_write)

            async def remove_ledger(row):
                await conn.execute(
                    "DELETE FROM public.usage_events WHERE id=$1",
                    legacy_admission_id(row["organization_id"], row["message_id"]),
                )
            await denied(remove_ledger)

            async def malformed(row, metadata):
                entry = legacy_admission_id(row["organization_id"], row["message_id"])
                await conn.execute(
                    "UPDATE public.usage_events SET metadata=$2::jsonb WHERE id=$1",
                    entry, json.dumps(metadata),
                )
            async def malformed_ordinal(row):
                entry = legacy_admission_id(row["organization_id"], row["message_id"])
                metadata = _metadata(await conn.fetchval(
                    "SELECT metadata FROM public.usage_events WHERE id=$1", entry,
                ))
                metadata["charged_ordinal"] = True
                await malformed(row, metadata)
            await denied(malformed_ordinal)

            async def missing_metadata_field(row):
                entry = legacy_admission_id(row["organization_id"], row["message_id"])
                metadata = _metadata(await conn.fetchval(
                    "SELECT metadata FROM public.usage_events WHERE id=$1", entry,
                ))
                metadata.pop("claim_uuid")
                await malformed(row, metadata)
            await denied(missing_metadata_field)

            async def mismatched_claim_time(row):
                entry = legacy_admission_id(row["organization_id"], row["message_id"])
                metadata = _metadata(await conn.fetchval(
                    "SELECT metadata FROM public.usage_events WHERE id=$1", entry,
                ))
                metadata["processing_at"] = (row["processing_at"] + timedelta(seconds=1)).isoformat()
                await malformed(row, metadata)
            await denied(mismatched_claim_time)

            await denied(await set_org("trial_end", datetime.now(timezone.utc) - timedelta(seconds=1)))
            await denied(await set_org("subscription_status", "canceled"))
            await denied(await set_org("ai_accounting_blocked", True))
            await denied(await set_org("trial_start", datetime.now(timezone.utc) - timedelta(minutes=2)))
            await denied(await set_org("subscription_id", "sub_changed123"))

            async def expired_lease(row):
                entry = legacy_admission_id(row["organization_id"], row["message_id"])
                metadata = _metadata(await conn.fetchval(
                    "SELECT metadata FROM public.usage_events WHERE id=$1", entry,
                ))
                processing = datetime.now(timezone.utc) - timedelta(seconds=40)
                billed = processing - timedelta(seconds=1)
                metadata.update(processing_at=processing.isoformat(), billed_at=billed.isoformat(),
                                lease_expires_at=(processing + timedelta(seconds=30)).isoformat())
                await malformed(row, metadata)
                await conn.execute(
                    "UPDATE public.messages SET billed_at=$2,processing_at=$3 WHERE id=$1",
                    row["message_id"], billed, processing,
                )
            await denied(expired_lease)

            async def cached_reply(row):
                await conn.execute(
                    "UPDATE public.messages SET ai_reply_cache='{}'::jsonb WHERE id=$1",
                    row["message_id"],
                )
            async def sent(row):
                await conn.execute(
                    "UPDATE public.messages SET sent_at=clock_timestamp() WHERE id=$1",
                    row["message_id"],
                )
            async def replied(row):
                await conn.execute(
                    "UPDATE public.messages SET replied_at=clock_timestamp() WHERE id=$1",
                    row["message_id"],
                )
            async def outbound(row):
                await conn.execute(
                    "UPDATE public.messages SET direction='outbound',status='queued' WHERE id=$1",
                    row["message_id"],
                )
            async def requeued(row):
                await conn.execute(
                    "UPDATE public.messages SET status='received_pending_ai' WHERE id=$1",
                    row["message_id"],
                )
            async def soft_deleted(row):
                await conn.execute(
                    "UPDATE public.messages SET deleted_at=clock_timestamp() WHERE id=$1",
                    row["message_id"],
                )
            for change in (cached_reply, sent, replied, outbound, requeued, soft_deleted):
                await denied(change)
        finally:
            await outer.rollback()

