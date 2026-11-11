"""SQL091 hotel lifecycle, fencing and privacy regression tests (synthetic PG only)."""
from uuid import uuid4

import pytest

from src.core.bookings.booking_mutation_contract import BookingMutation, apply_booking_mutation
from src.core.gdpr.operations import Operations
from tests.core.bookings.test_booking_stay_lifecycle_postgres import _approve_retention, _create_stay
from tests.core.bookings.test_booking_stays_postgres import hotel_setup
from tests.core.bookings.test_hotel_allocation_archive_postgres import _complete_with_old_evidence, _archive

pytestmark = pytest.mark.asyncio


async def test_archive_releases_room_before_twelve_month_purge_gate(pg_pool):
    env, _, booking = await _complete_with_old_evidence(pg_pool)
    # Historical completion evidence is moved inside the 12-month window. The
    # allocation ended earlier, so checkout is proven while content is not yet
    # eligible for retention purge.
    await pg_pool.execute(
        "UPDATE bookings SET completata_at=now()-interval '30 days' WHERE organization_id=$1 AND id=$2",
        env.org, booking,
    )
    async with pg_pool.acquire() as conn, conn.transaction():
        await conn.execute('ALTER TABLE booking_stay_completion_evidence DISABLE TRIGGER completion_evidence_immutable')
        await conn.execute(
            "UPDATE booking_stay_completion_evidence SET completed_at=now()-interval '30 days' WHERE organization_id=$1 AND booking_id=$2",
            env.org, booking,
        )
        await conn.execute('ALTER TABLE booking_stay_completion_evidence ENABLE TRIGGER completion_evidence_immutable')
    assert not await pg_pool.fetchval(
        "SELECT hotel_completed_stay_retention_eligible(completed_at,timezone,now()) "
        "FROM booking_stay_completion_evidence WHERE organization_id=$1 AND booking_id=$2",
        env.org, booking,
    )
    assert await _archive(pg_pool, env, booking) is True
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2',
        env.org, booking,
    ) == 0
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM booking_stays WHERE organization_id=$1 AND booking_id=$2', env.org, booking,
    ) == 1
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking,
    ) == 1


async def test_retention_purge_removes_booking_content_and_fences_replay(pg_pool):
    env, lineage, booking = await _complete_with_old_evidence(pg_pool)
    assert await _archive(pg_pool, env, booking) is True
    deleted_receipts = await pg_pool.fetchval(
        "SELECT booking_stay_admin_purge($1,$2,'retention',NULL,'synthetic-stay-v1')",
        env.org, booking,
    )
    assert deleted_receipts >= 2
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking,
    ) == 0
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM booking_stays WHERE organization_id=$1 AND booking_id=$2', env.org, booking,
    ) == 0
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM booking_stay_purge_fences WHERE organization_id=$1 AND booking_id=$2', env.org, booking,
    ) == 1
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM booking_stay_tombstones WHERE organization_id=$1 AND booking_id=$2', env.org, booking,
    ) == deleted_receipts
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM booking_stay_purge_audit WHERE organization_id=$1 AND booking_id=$2', env.org, booking,
    ) == 1
    with pytest.raises(Exception, match='Purged booking or request cannot be replayed'):
        await apply_booking_mutation(
            pg_pool, env.org, env.auth,
            env.request(booking, action='allocate'),
            mutation=BookingMutation('create_stay', 'Synthetic', '', '', '2024-01-10', '14:00', 1,
                                     arrival_date='2024-01-10', departure_date='2024-01-11'),
            new_booking=lineage,
        )


async def test_owner_source_requires_active_owner_marker_and_same_transaction_receipt(pg_pool):
    env, lineage = await hotel_setup(pg_pool)
    booking = uuid4()
    # Even a trusted backend cannot create source_kind=owner rows by supplying
    # NULL inbound identifiers; the database marker is mandatory.
    await pg_pool.execute(
        "INSERT INTO bookings(id,organization_id,contact_id,nome_cliente,telefono,data,ora,coperti,stato) "
        "VALUES($1,$2,$3,'Synthetic','3900000001','2026-10-28','14:00',1,'in_attesa')",
        booking, env.org, lineage.contact_id,
    )
    with pytest.raises(Exception, match='Owner stay authorization marker required'):
        await pg_pool.execute(
            "INSERT INTO booking_stays(organization_id,booking_id,original_subject_id,arrival_date,departure_date,revision,source_kind) "
            "VALUES($1,$2,$3,'2026-10-28','2026-10-29',1,'owner')",
            env.org, booking, lineage.contact_id,
        )
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM booking_stays WHERE organization_id=$1 AND booking_id=$2', env.org, booking,
    ) == 0


async def test_stay_conversation_soft_delete_requires_prior_subject_tombstone(pg_pool):
    env, lineage = await hotel_setup(pg_pool)
    booking = uuid4()
    await apply_booking_mutation(
        pg_pool, env.org, env.auth, env.request(booking),
        mutation=BookingMutation('create_stay', 'Synthetic', '', '', '2026-10-28', '14:00', 1,
                                 arrival_date='2026-10-28', departure_date='2026-10-29'),
        new_booking=lineage,
    )
    with pytest.raises(Exception, match='Stay conversation deletion requires tombstoned subject'):
        await pg_pool.execute(
            'UPDATE conversations SET deleted_at=now() WHERE organization_id=$1 AND id=$2',
            env.org, lineage.conversation_id,
        )
    assert await pg_pool.fetchval(
        'SELECT deleted_at IS NULL FROM conversations WHERE organization_id=$1 AND id=$2',
        env.org, lineage.conversation_id,
    )
    await pg_pool.execute(
        "UPDATE contacts SET phone_number='erased:'||id::text,deleted_at=now() "
        'WHERE organization_id=$1 AND id=$2',
        env.org, lineage.contact_id,
    )
    assert await pg_pool.fetchval(
        'SELECT deleted_at IS NOT NULL FROM conversations WHERE organization_id=$1 AND id=$2',
        env.org, lineage.conversation_id,
    )
    assert await pg_pool.fetchval(
        'SELECT contact_id FROM conversations WHERE organization_id=$1 AND id=$2',
        env.org, lineage.conversation_id,
    ) == lineage.contact_id


async def test_erasure_wrapper_is_service_role_only_and_requires_exact_barrier(pg_pool):
    env, lineage, booking = await _complete_with_old_evidence(pg_pool)
    for category in ('contact_content', 'cutover'):
        await pg_pool.execute(
            "INSERT INTO gdpr_policies(organization_id,category,version,erasure_allowed,retention_days,legal_hold,"
            "approval_reference,approved_by,approved_at,valid_until) "
            "VALUES($1,$2,'synthetic-erasure-v1',true,NULL,false,'test:erasure','qa',now()-interval '1 day',now()+interval '1 day')",
            env.org, category,
        )
    operation = await Operations(pg_pool).create(
        env.org, 'synthetic-admin', uuid4(), kind='erase', scope='contact', subject_id=lineage.contact_id,
    )
    assert await pg_pool.fetchval(
        "SELECT has_function_privilege('service_role',"
        "'public.c24_purge_hotel_for_erasure(uuid,uuid,uuid,text)','EXECUTE')"
    )
    assert not await pg_pool.fetchval(
        "SELECT has_function_privilege('authenticated',"
        "'public.c24_purge_hotel_for_erasure(uuid,uuid,uuid,text)','EXECUTE')"
    )
    async with pg_pool.acquire() as conn, conn.transaction():
        await conn.execute('SET LOCAL ROLE service_role')
        with pytest.raises(Exception, match='Exact hotel erasure barrier required'):
            await conn.fetchval(
                "SELECT public.c24_purge_hotel_for_erasure($1,$2,$3,'synthetic-stay-v1')",
                env.org, booking, operation,
            )
    await pg_pool.execute(
        "INSERT INTO gdpr_erasure_barriers(organization_id,operation_id,scope,subject_id,identifier_digest,key_id) "
        "VALUES($1,$2,'contact',$3,$4,'synthetic')",
        env.org, operation, lineage.contact_id, 'a' * 64,
    )
    async with pg_pool.acquire() as conn, conn.transaction():
        await conn.execute('SET LOCAL ROLE service_role')
        assert await conn.fetchval(
            "SELECT public.c24_purge_hotel_for_erasure($1,$2,$3,'synthetic-stay-v1')",
            env.org, booking, operation,
        ) >= 2
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking,
    ) == 0

