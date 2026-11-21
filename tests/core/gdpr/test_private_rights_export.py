"""Scoped encrypted snapshots of private completion and ingress evidence."""
import json
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from src.core.db.inbound_worker_provenance import record_receipt
from src.core.gdpr.exporter import Exporter
from src.core.gdpr.operations import Operations
from tests.core.bookings.test_hotel_allocation_archive_postgres import _complete_with_old_evidence
from tests.core.gdpr.test_exports_a06 import export_bytes
from tests.core.gdpr.test_ordinary_booking_completion_evidence import _complete, _managed_booking


async def _owner(pool, org):
    auth = uuid4()
    await pool.execute("INSERT INTO auth.users(id,email) VALUES($1,$2)", auth, f"{auth}@test.invalid")
    profile = await pool.fetchval("SELECT id FROM user_profiles WHERE auth_user_id=$1", auth)
    await pool.execute(
        "INSERT INTO organization_memberships(organization_id,user_id,ruolo,joined_at) VALUES($1,$2,'owner',now())",
        org, profile,
    )
    return auth


async def _receipt(pool, org):
    subject, conversation, message, inbox, lease = (uuid4() for _ in range(5))
    provider_id = f"synthetic-{message}"
    await pool.execute("INSERT INTO contacts(id,organization_id,phone_number) VALUES($1,$2,$3)",
                       subject, org, str(subject))
    await pool.execute("INSERT INTO conversations(id,organization_id,contact_id) VALUES($1,$2,$3)",
                       conversation, org, subject)
    await pool.execute("""INSERT INTO messages(id,organization_id,conversation_id,direction,wam_id,
                   message_type,content,content_text,status)
               VALUES($1,$2,$3,'inbound',$4,'text','{}','synthetic','received_pending_ai')""",
                       message, org, conversation, provider_id)
    envelope = {"entry": [{"changes": [{"value": {"messages": [{"id": provider_id}]}}]}]}
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("""INSERT INTO meta_webhook_inbox
            (id,organization_id,channel,event_key,payload,trace_id,status,lease_token,lease_until)
            VALUES($1,$2,'whatsapp',$3,$4::jsonb,'synthetic','processing',$5,now()+interval '1 minute')""",
                           inbox, org, provider_id, json.dumps(envelope), lease)
        await record_receipt(conn, {"id": inbox, "organization_id": org, "lease_token": lease}, provider_id)
        await conn.execute("UPDATE meta_webhook_inbox SET status='completed' WHERE organization_id=$1 AND id=$2",
                           org, inbox)
    return subject, message


async def _snapshot(pool, org, auth, **scope):
    exporter = Exporter(pool)
    manifest = await exporter.prepare(org, auth, request_id=uuid4(), **scope)
    return json.loads(await export_bytes(exporter, org, manifest["export_id"], auth))


@pytest.mark.asyncio
async def test_verified_private_receipts_follow_exact_subject_and_role(pg_pool, sample_org, other_org, monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())
    org, other = sample_org["id"], other_org["id"]
    auth = await _owner(pg_pool, org)
    foreign_owner = await _owner(pg_pool, other)
    subject, message = await _receipt(pg_pool, org)
    _, unrelated_message = await _receipt(pg_pool, org)
    _, foreign_message = await _receipt(pg_pool, other)
    future_subject, future_message = await _receipt(pg_pool, org)
    async with pg_pool.acquire() as conn, conn.transaction():
        await conn.execute('ALTER TABLE messages DISABLE TRIGGER c24_source_received')
        await conn.execute("UPDATE messages SET c24_received_at=now()+interval '1 day' WHERE organization_id=$1 AND id=$2",
                           org, future_message)
        await conn.execute('ALTER TABLE messages ENABLE TRIGGER c24_source_received')

    contact = await _snapshot(pg_pool, org, auth, scope="contact", subject_id=subject)
    receipt, = contact["inbound_worker_receipts"]
    assert set(receipt) == {"message_id", "received_at", "completed_at"}
    assert receipt["message_id"] == str(message)
    assert receipt["received_at"] and receipt["completed_at"]
    assert str(unrelated_message) not in json.dumps(contact["inbound_worker_receipts"])
    assert str(foreign_message) not in json.dumps(contact["inbound_worker_receipts"])
    assert contact["manifest"]["status"] == "partial"
    org_export = await _snapshot(pg_pool, org, auth)
    assert {r["message_id"] for r in org_export["inbound_worker_receipts"]} == {
        str(message), str(unrelated_message),
    }
    future_contact = await _snapshot(pg_pool, org, auth, scope="contact", subject_id=future_subject)
    assert future_contact["inbound_worker_receipts"] == []
    account = await _snapshot(pg_pool, org, auth, scope="account")
    assert "inbound_worker_receipts" not in account

    # Test the exact database capability independently, with the non-owner
    # role whose calls are used by the application.
    operation = await Operations(pg_pool).create(
        org, auth, uuid4(), kind="export", scope="contact", subject_id=subject,
    )
    async with pg_pool.acquire() as conn, conn.transaction():
        name = "public.gdpr_private_export_records(uuid,uuid,text,text)"
        assert await conn.fetchval("SELECT has_function_privilege('service_role',$1,'EXECUTE')", name)
        assert await conn.fetchval("""SELECT bool_and(NOT has_function_privilege(role,$1,'EXECUTE'))
            FROM unnest(ARRAY['anon','authenticated','melpis_c24_booking_worker','melpis_c24_retention']) role""", name)
        assert await conn.fetchval("""SELECT bool_and(NOT has_table_privilege('service_role',relation,'SELECT'))
            FROM unnest(ARRAY['inbound_worker_receipts','booking_stay_completion_evidence',
                              'ordinary_booking_completion_evidence']) relation""")
        query = "SELECT record FROM public.gdpr_private_export_records($1,$2,$3,$4)"
        await conn.execute("SET LOCAL ROLE service_role")
        args = org, operation, str(auth), "inbound_worker_receipts"
        assert await conn.fetch(query, *args) == []
        await conn.execute("RESET ROLE")
        await conn.execute("UPDATE gdpr_operations SET status='running' WHERE organization_id=$1 AND id=$2",
                           org, operation)
        await conn.execute("SET LOCAL ROLE service_role")
        rows = await conn.fetch(query, *args)
        assert rows[0]["record"] is None
        assert json.loads(rows[1]["record"])["message_id"] == str(message)
        assert len(rows) == 2
        assert await conn.fetch(query, other, operation, str(auth), args[3]) == []
        assert await conn.fetch(query, org, operation, str(foreign_owner), args[3]) == []
        assert await conn.fetch(query, org, operation, str(auth), "worker_context") == []
        await conn.execute("RESET ROLE")
        await conn.execute("UPDATE auth_access_lifecycle SET disabled_at=now() WHERE auth_user_id=$1", auth)
        await conn.execute("SET LOCAL ROLE service_role")
        assert await conn.fetch(query, *args) == []


@pytest.mark.asyncio
async def test_private_booking_completion_export_has_only_verified_dates(pg_pool, monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())
    ordinary, booking = await _managed_booking(pg_pool)
    await _complete(ordinary, booking)
    hotel, _, stay = await _complete_with_old_evidence(pg_pool)
    subject = await pg_pool.fetchval(
        "SELECT subject_id FROM ordinary_booking_completion_evidence WHERE organization_id=$1 AND booking_id=$2",
        ordinary.org, booking,
    )
    stay_subject = await pg_pool.fetchval(
        "SELECT original_subject_id FROM booking_stays WHERE organization_id=$1 AND booking_id=$2",
        hotel.org, stay,
    )
    for env, category, key, contact in (
        (ordinary, "ordinary_booking_completion_evidence", booking, subject),
        (hotel, "booking_stay_completion_evidence", stay, stay_subject),
    ):
        data = await _snapshot(pg_pool, env.org, env.auth, scope="contact", subject_id=contact)
        result, = data[category]
        assert set(result) == {"booking_id", "completed_at", "timezone"}
        assert result["booking_id"] == str(key)
        assert result["completed_at"] and result["timezone"] == "Europe/Rome"
        other_subject = uuid4()
        await pg_pool.execute(
            "INSERT INTO contacts(id,organization_id,phone_number) VALUES($1,$2,$3)",
            other_subject, env.org, str(other_subject),
        )
        unrelated = await _snapshot(pg_pool, env.org, env.auth, scope="contact", subject_id=other_subject)
        assert unrelated[category] == []

        await pg_pool.execute("""INSERT INTO booking_provenance_quarantine
            (organization_id,booking_id,review_reference,reason_code,reviewed_at,review_due_at)
            VALUES($1,$2,'synthetic-private-review','historical_provenance_unverified',
                clock_timestamp(),clock_timestamp()+interval '30 days')""", env.org, key)
        operation = await Operations(pg_pool).create(
            env.org, env.auth, uuid4(), kind="export", scope="contact", subject_id=contact,
        )
        async with pg_pool.acquire() as conn, conn.transaction():
            await conn.execute("UPDATE gdpr_operations SET status='running' WHERE organization_id=$1 AND id=$2",
                               env.org, operation)
            await conn.execute("SET LOCAL ROLE service_role")
            rows = await conn.fetch("SELECT record FROM public.gdpr_private_export_records($1,$2,$3,$4)",
                                    env.org, operation, str(env.auth), category)
            assert [row['record'] for row in rows] == [None]
