"""Focused regression coverage for quarantined stay-purge restore and stay edges."""
import asyncio
import hashlib
import hmac
import json
from datetime import date, timedelta
from uuid import uuid4

import pytest

from src.core.bookings.booking_mutation_contract import (
    BookingMutation,
    NewBookingLineage,
    apply_booking_mutation,
)
from src.core.gdpr.restore_checkpoint import RestoreBlocked, verify_restore_checkpoint
from tests.core.bookings.test_booking_stay_lifecycle_postgres import _purge_retention
from tests.core.bookings.test_hotel_allocation_archive_postgres import _archive, _complete_with_old_evidence
from tests.core.bookings.test_booking_stays_postgres import hotel_setup

pytestmark = pytest.mark.asyncio


_KEY = b"synthetic-stay-restore-key-32bytes"
_BACKUP = "synthetic-stay-backup"


def _signed_checkpoint(org, stay_purges, *, version="c24-restore-v2", sequence=7):
    payload = {
        "version": version,
        "organization_id": str(org),
        "backup_id": _BACKUP,
        "sequence": sequence,
        "complete": True,
        "erasures": [],
    }
    if version == "c24-restore-v2":
        payload["stay_purges"] = stay_purges
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return {
        "payload": payload,
        "signature": hmac.new(_KEY, raw, hashlib.sha256).hexdigest(),
    }


async def _purged_stay(pg_pool):
    env, _, booking = await _complete_with_old_evidence(pg_pool)
    await _archive(pg_pool, env, booking)
    await _purge_retention(env.pool, env.org, booking)
    fence = await env.pool.fetchrow(
        """SELECT original_subject_id,purpose,operation_id,policy_version
           FROM booking_stay_purge_fences WHERE organization_id=$1 AND booking_id=$2""",
        env.org, booking,
    )
    request_ids = await env.pool.fetch(
        "SELECT request_id FROM booking_stay_tombstones WHERE organization_id=$1 AND booking_id=$2",
        env.org, booking,
    )
    journal_item = {
        "booking_id": str(booking),
        "subject_id": str(fence["original_subject_id"]),
        "purpose": fence["purpose"],
        "operation_id": str(fence["operation_id"]) if fence["operation_id"] else None,
        "policy_version": fence["policy_version"],
        "request_ids": [str(row["request_id"]) for row in request_ids],
    }
    return env, booking, journal_item


async def _verify(env, checkpoint):
    async with env.pool.acquire() as conn, conn.transaction():
        return await verify_restore_checkpoint(
            conn,
            env.org,
            checkpoint,
            verification_key=_KEY,
            backup_id=_BACKUP,
            minimum_sequence=7,
        )


async def test_signed_v2_restore_journal_matches_actual_stay_fences_and_tombstones(pg_pool):
    env, _, purge = await _purged_stay(pg_pool)

    proof = await _verify(env, _signed_checkpoint(env.org, [purge]))

    assert proof["scope"] == "primary_c24_only"
    assert proof["traffic_authorized"] is False
    assert proof["external_copies_certified"] is False


async def test_signed_v2_restore_rejects_missing_fence_or_tombstone(pg_pool):
    env, booking, purge = await _purged_stay(pg_pool)
    checkpoint = _signed_checkpoint(env.org, [purge])

    await env.pool.execute(
        "DELETE FROM booking_stay_purge_fences WHERE organization_id=$1 AND booking_id=$2",
        env.org,
        booking,
    )
    with pytest.raises(RestoreBlocked, match="fences do not match"):
        await _verify(env, checkpoint)


async def test_signed_v2_restore_rejects_missing_or_wrong_tombstone_and_tampering(pg_pool):
    env, booking, purge = await _purged_stay(pg_pool)

    # Even a freshly signed journal cannot name a different request receipt.
    wrong = dict(purge)
    wrong["request_ids"] = [str(uuid4()) for _ in purge["request_ids"]]
    with pytest.raises(RestoreBlocked, match="purge copies do not match"):
        await _verify(env, _signed_checkpoint(env.org, [wrong]))

    await env.pool.execute(
        "DELETE FROM booking_stay_tombstones WHERE organization_id=$1 AND booking_id=$2",
        env.org,
        booking,
    )
    with pytest.raises(RestoreBlocked, match="tombstones do not match"):
        await _verify(env, _signed_checkpoint(env.org, [purge]))

    valid = _signed_checkpoint(env.org, [purge])
    valid["payload"]["stay_purges"][0]["policy_version"] = "tampered-policy"
    with pytest.raises(RestoreBlocked, match="checkpoint"):
        await _verify(env, valid)
    invalid_mac = _signed_checkpoint(env.org, [purge])
    invalid_mac["signature"] = "0" * 64
    with pytest.raises(RestoreBlocked, match="checkpoint"):
        await _verify(env, invalid_mac)


async def test_signed_v1_checkpoint_always_quarantines_stay_purges(pg_pool):
    env, _, purge = await _purged_stay(pg_pool)

    with pytest.raises(RestoreBlocked, match="Authenticated stay purge journal required"):
        await _verify(env, _signed_checkpoint(env.org, [purge], version="c24-restore-v1"))


@pytest.mark.parametrize(
    "arrival,departure,elapsed_hours",
    [(date(2026, 3, 28), date(2026, 3, 30), 47),
     (date(2026, 10, 24), date(2026, 10, 26), 49)],
)
async def test_source_proven_hotel_stays_cover_dst_and_adjacent_checkout(
    pg_pool, arrival, departure, elapsed_hours,
):
    env, lineage = await hotel_setup(pg_pool)
    arrival_text, departure_text = arrival.isoformat(), departure.isoformat()
    booking = uuid4()
    create = BookingMutation(
        "create_stay", "Synthetic", "", "", arrival_text, "14:00", 1,
        arrival_date=arrival_text, departure_date=departure_text,
    )

    result = await apply_booking_mutation(
        env.pool,
        env.org,
        env.auth,
        env.request(booking, arrival_date=arrival_text, departure_date=departure_text),
        mutation=create,
        new_booking=lineage,
    )
    assert result["outcome"] == "allocated"
    assert result["runtime_enabled"] is False
    assert result["confirmation_authorized"] is False

    claim = await env.pool.fetchrow(
        "SELECT starts_at,ends_at FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2",
        env.org,
        booking,
    )
    assert (claim["ends_at"] - claim["starts_at"]).total_seconds() == elapsed_hours * 3600

    # A second source-proven stay may begin exactly at the first checkout.
    next_arrival = departure
    next_departure = departure + timedelta(days=1)
    adjacent_lineage = NewBookingLineage(*[uuid4() for _ in range(3)])
    await env.pool.execute(
        "INSERT INTO contacts(id,organization_id,phone_number) VALUES($1,$2,$3)",
        adjacent_lineage.contact_id, env.org, str(uuid4()),
    )
    await env.pool.execute(
        "INSERT INTO conversations(id,organization_id,contact_id) VALUES($1,$2,$3)",
        adjacent_lineage.conversation_id, env.org, adjacent_lineage.contact_id,
    )
    await env.pool.execute(
        """INSERT INTO messages(id,organization_id,conversation_id,direction,message_type,
                  content,content_text,status,handling_type)
           VALUES($1,$2,$3,'inbound','text','{}'::jsonb,'Synthetic adjacent stay','handled','manual')""",
        adjacent_lineage.source_message_id, env.org, adjacent_lineage.conversation_id,
    )
    next_booking = uuid4()
    next_arrival_text, next_departure_text = next_arrival.isoformat(), next_departure.isoformat()
    adjacent = BookingMutation(
        "create_stay", "Synthetic", "", "", next_arrival_text, "14:00", 1,
        arrival_date=next_arrival_text, departure_date=next_departure_text,
    )
    adjacent_result = await apply_booking_mutation(
        env.pool,
        env.org,
        env.auth,
        env.request(next_booking, arrival_date=next_arrival_text, departure_date=next_departure_text),
        mutation=adjacent,
        new_booking=adjacent_lineage,
    )
    assert adjacent_result["outcome"] == "allocated"
    assert adjacent_result["runtime_enabled"] is False
    assert adjacent_result["confirmation_authorized"] is False
    assert await env.pool.fetchval(
        "SELECT count(*) FROM booking_confirmation_outbox WHERE organization_id=$1 AND booking_id=ANY($2::uuid[])",
        env.org, [booking, next_booking],
    ) == 0


async def test_hotel_last_room_race_commits_one_source_proven_stay_only(pg_pool):
    env, first_lineage = await hotel_setup(pg_pool)
    second_lineage = NewBookingLineage(*[uuid4() for _ in range(3)])
    await env.pool.execute(
        "INSERT INTO contacts(id,organization_id,phone_number) VALUES($1,$2,$3)",
        second_lineage.contact_id, env.org, str(uuid4()),
    )
    await env.pool.execute(
        "INSERT INTO conversations(id,organization_id,contact_id) VALUES($1,$2,$3)",
        second_lineage.conversation_id, env.org, second_lineage.contact_id,
    )
    await env.pool.execute(
        """INSERT INTO messages(id,organization_id,conversation_id,direction,message_type,
                  content,content_text,status,handling_type)
           VALUES($1,$2,$3,'inbound','text','{}'::jsonb,'Synthetic competing stay','handled','manual')""",
        second_lineage.source_message_id, env.org, second_lineage.conversation_id,
    )
    arrival, departure = "2026-11-02", "2026-11-04"
    bookings = [uuid4(), uuid4()]
    outcomes = await asyncio.gather(*[
        apply_booking_mutation(
            env.pool,
            env.org,
            env.auth,
            env.request(booking, arrival_date=arrival, departure_date=departure),
            mutation=BookingMutation(
                "create_stay", "Synthetic", "", "", arrival, "14:00", 1,
                arrival_date=arrival, departure_date=departure,
            ),
            new_booking=lineage,
        )
        for booking, lineage in zip(bookings, (first_lineage, second_lineage))
    ], return_exceptions=True)

    assert sum(isinstance(item, dict) and item.get("outcome") == "allocated" for item in outcomes) == 1
    assert sum(isinstance(item, Exception) for item in outcomes) == 1
    assert all(
        not item.get("runtime_enabled") and not item.get("confirmation_authorized")
        for item in outcomes if isinstance(item, dict)
    )
    assert await env.pool.fetchval(
        "SELECT count(*) FROM booking_stays WHERE organization_id=$1 AND booking_id=ANY($2::uuid[])",
        env.org, bookings,
    ) == 1
    assert await env.pool.fetchval(
        "SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1 AND booking_id=ANY($2::uuid[])",
        env.org, bookings,
    ) == 1
    assert await env.pool.fetchval(
        "SELECT count(*) FROM booking_confirmation_outbox WHERE organization_id=$1 AND booking_id=ANY($2::uuid[])",
        env.org, bookings,
    ) == 0


@pytest.mark.parametrize('target', ['receipt', 'stay'])
async def test_backend_sql_cannot_recreate_a_purged_booking(pg_pool, target):
    env, booking, item = await _purged_stay(pg_pool)
    row = await pg_pool.fetchrow('SELECT contact_id,id_conversazione FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking)
    source = await pg_pool.fetchval('SELECT id FROM messages WHERE organization_id=$1 AND conversation_id=$2', env.org, __import__('uuid').UUID(row['id_conversazione']))
    async with pg_pool.acquire() as conn, conn.transaction():
        with pytest.raises(Exception, match='Purged (booking|stay) cannot be recreated'):
            async with conn.transaction():
                await conn.execute('SET LOCAL ROLE service_role')
                if target == 'receipt':
                    await conn.execute("""INSERT INTO booking_allocation_requests(organization_id,request_id,booking_id,actor_user_id,action,payload_hash,outcome,catalog_revision)
                        VALUES($1,$2,$3,$4,'allocate',$5,'unknown',$6)""", env.org, uuid4(), booking, env.actor, 'a'*64, env.revision)
                else:
                    await conn.execute("""INSERT INTO booking_stays(organization_id,booking_id,original_subject_id,original_conversation_id,source_message_id,arrival_date,departure_date,revision)
                        VALUES($1,$2,$3,$4,$5,'2026-10-28','2026-10-29',1)""", env.org, booking, row['contact_id'], __import__('uuid').UUID(row['id_conversazione']), source)
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_stays WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 0
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 0
