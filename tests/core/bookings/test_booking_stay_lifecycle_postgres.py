"""Security and lifecycle checks for owner-only hotel stay purging."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.core.bookings.booking_mutation_contract import (
    BookingMutation,
    NewBookingLineage,
    apply_booking_mutation,
)
from tests.core.bookings.test_booking_stays_postgres import hotel_setup

pytestmark = pytest.mark.asyncio


async def _new_lineage(env):
    contact, conversation, source = uuid4(), uuid4(), uuid4()
    await env.pool.execute(
        'INSERT INTO contacts(id,organization_id,phone_number) VALUES($1,$2,$3)',
        contact, env.org, str(uuid4()),
    )
    await env.pool.execute(
        'INSERT INTO conversations(id,organization_id,contact_id) VALUES($1,$2,$3)',
        conversation, env.org, contact,
    )
    await env.pool.execute(
        """INSERT INTO messages(id,organization_id,conversation_id,direction,message_type,content,content_text,status,handling_type)
           VALUES($1,$2,$3,'inbound','text','{}'::jsonb,'Synthetic hotel request','handled','manual')""",
        source, env.org, conversation,
    )
    return NewBookingLineage(contact, conversation, source)


async def _create_stay(env, lineage, *, terminal=False, arrival='2026-10-28', departure='2026-10-29'):
    booking = uuid4()
    command = BookingMutation(
        'create_stay', 'Synthetic', '', '', arrival, '14:00', 1,
        arrival_date=arrival, departure_date=departure,
    )
    await apply_booking_mutation(
        env.pool, env.org, env.auth,
        env.request(booking, arrival_date=arrival, departure_date=departure),
        mutation=command, new_booking=lineage,
    )
    await env.pool.execute(
        "UPDATE messages SET status='handled' WHERE organization_id=$1 AND id=$2",
        env.org, lineage.source_message_id,
    )
    if terminal:
        await apply_booking_mutation(
            env.pool, env.org, env.auth,
            env.request(booking, action='cancel', allocation_revision=1),
            mutation=BookingMutation('cancel', '', '', ''),
        )
    return booking


async def _approve_retention(pool, org, *, version='synthetic-stay-v1', days=1,
                             held=False, expired=False):
    valid_until = datetime.now(timezone.utc) - timedelta(minutes=1) if expired else datetime.now(timezone.utc) + timedelta(days=1)
    await pool.execute(
        """INSERT INTO gdpr_policies(organization_id,category,version,erasure_allowed,
               retention_days,legal_hold,approval_reference,approved_by,approved_at,valid_until)
           VALUES($1,'booking_stay_content',$2,true,$3,$4,'test:stay','qa-stay',
                  now()-interval '1 day',$5)""",
        org, version, days, held, valid_until,
    )


async def _purge_retention(pool, org, booking, version='synthetic-stay-v1'):
    return await pool.fetchval(
        "SELECT public.booking_stay_admin_purge($1,$2,'retention',NULL,$3)",
        org, booking, version,
    )


@pytest.mark.parametrize('scope', ['organization', 'subject', 'conversation', 'record'])
async def test_retention_respects_active_scoped_holds(pg_pool, scope):
    from tests.core.bookings.test_hotel_allocation_archive_postgres import _complete_with_old_evidence, _archive
    env, lineage, booking = await _complete_with_old_evidence(pg_pool)
    await _archive(pg_pool, env, booking)
    scope_id = {'organization': None, 'subject': lineage.contact_id,
                'conversation': lineage.conversation_id, 'record': booking}[scope]
    hold = uuid4()
    await pg_pool.execute('''INSERT INTO gdpr_retention_holds
        (organization_id,hold_id,category,scope,scope_id,reason_code,authority_reference,approved_by,approved_at,review_by)
        VALUES($1,$2,'booking_stay_content',$3,$4,'test','test:hold','qa',now(),now()+interval '1 month')''',
        env.org, hold, scope, scope_id)
    with pytest.raises(Exception, match='Completed archived stay retention evidence required'):
        await _purge_retention(pg_pool, env.org, booking)
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_stays WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 1
    await pg_pool.execute('''INSERT INTO gdpr_retention_hold_events
        (organization_id,hold_id,event_id,kind,authority_reference,approved_by,occurred_at)
        VALUES($1,$2,$3,'released','test:release','qa',now())''', env.org, hold, uuid4())
    assert await _purge_retention(pg_pool, env.org, booking) == 2


async def test_owner_purge_is_scoped_and_preserves_booking_and_source_records(pg_pool):
    from tests.core.bookings.test_hotel_allocation_archive_postgres import _complete_with_old_evidence, _archive
    env, target_lineage, target = await _complete_with_old_evidence(pg_pool)
    await _archive(pg_pool, env, target)
    same_org_lineage = await _new_lineage(env)
    same_org_other = await _create_stay(
        env, same_org_lineage, arrival='2026-10-29', departure='2026-10-30',
    )

    other_env, other_lineage = await hotel_setup(pg_pool)
    other_tenant_booking = await _create_stay(other_env, other_lineage)

    await pg_pool.execute(
        "UPDATE bookings SET updated_at=now()-interval '2 days' WHERE organization_id=$1 AND id=$2",
        env.org, target,
    )

    source_before = await pg_pool.fetchrow(
        'SELECT organization_id,contact_id,id_conversazione,data,ora,coperti,stato FROM bookings WHERE organization_id=$1 AND id=$2',
        env.org, target,
    )
    assert await _purge_retention(pg_pool, env.org, target) == 2

    source_after = await pg_pool.fetchrow(
        'SELECT organization_id,contact_id,id_conversazione,data,ora,coperti,stato FROM bookings WHERE organization_id=$1 AND id=$2',
        env.org, target,
    )
    assert dict(source_after) == dict(source_before)
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM booking_stays WHERE organization_id=$1 AND booking_id=$2', env.org, target,
    ) == 0
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1 AND booking_id=$2', env.org, target,
    ) == 0
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2', env.org, target,
    ) == 0
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM booking_stay_tombstones WHERE organization_id=$1 AND booking_id=$2 AND original_subject_id=$3',
        env.org, target, target_lineage.contact_id,
    ) == 2
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM booking_stay_purge_fences WHERE organization_id=$1 AND booking_id=$2 AND original_subject_id=$3',
        env.org, target, target_lineage.contact_id,
    ) == 1
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM booking_stays WHERE organization_id=$1 AND booking_id=$2', env.org, same_org_other,
    ) == 1
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM booking_stays WHERE organization_id=$1 AND booking_id=$2', other_env.org, other_tenant_booking,
    ) == 1
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM messages WHERE organization_id=$1 AND conversation_id=$2 AND id=$3',
        env.org, target_lineage.conversation_id, target_lineage.source_message_id,
    ) == 1

    audit = await pg_pool.fetchrow(
        'SELECT * FROM booking_stay_purge_audit WHERE organization_id=$1 AND booking_id=$2', env.org, target,
    )
    assert audit['purpose'] == 'retention' and audit['receipt_count'] == 2
    assert audit['operation_id'] is None
    assert audit['policy_version'] == 'synthetic-stay-v1'


@pytest.mark.parametrize('policy_state', ['held', 'expired', 'unset'])
async def test_owner_purge_fails_closed_without_current_unheld_policy(pg_pool, policy_state):
    env, lineage = await hotel_setup(pg_pool)
    booking = await _create_stay(env, lineage, terminal=True)
    await pg_pool.execute(
        "UPDATE bookings SET updated_at=now()-interval '2 days' WHERE organization_id=$1 AND id=$2",
        env.org, booking,
    )
    if policy_state != 'unset':
        await _approve_retention(
            pg_pool, env.org, held=policy_state == 'held', expired=policy_state == 'expired',
        )

    with pytest.raises(Exception, match='Approved current policy required'):
        await _purge_retention(pg_pool, env.org, booking)
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM booking_stays WHERE organization_id=$1 AND booking_id=$2', env.org, booking,
    ) == 1
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1 AND booking_id=$2', env.org, booking,
    ) == 2
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM booking_stay_purge_fences WHERE organization_id=$1 AND booking_id=$2', env.org, booking,
    ) == 0


async def test_owner_erasure_purge_requires_barrier_matching_operation_scope_and_subject(pg_pool):
    from src.core.gdpr.operations import Operations

    env, lineage = await hotel_setup(pg_pool)
    booking = await _create_stay(env, lineage, terminal=True)
    await _approve_retention(pg_pool, env.org)
    for category in ('contact_content', 'cutover'):
        await pg_pool.execute(
            """INSERT INTO gdpr_policies(organization_id,category,version,erasure_allowed,
                   retention_days,legal_hold,approval_reference,approved_by,approved_at,valid_until)
               VALUES($1,$2,'synthetic-erasure-v1',true,NULL,false,'test:stay','qa-stay',
                      now()-interval '1 day',now()+interval '1 day')""",
            env.org, category,
        )
    operation = await Operations(pg_pool).create(
        env.org, 'synthetic-admin', uuid4(), kind='erase', scope='contact',
        subject_id=lineage.contact_id,
    )
    # This mismatched organization barrier must not authorize a contact purge.
    with pytest.raises(Exception, match='Barrier does not match erasure operation'):
        await pg_pool.execute(
            '''INSERT INTO gdpr_erasure_barriers(organization_id,operation_id,scope,subject_id)
               VALUES($1,$2,'organization',NULL)''', env.org, operation,
        )

    with pytest.raises(Exception, match='Scoped erasure barrier required'):
        await pg_pool.fetchval(
            "SELECT public.booking_stay_admin_purge($1,$2,'erasure',$3,'synthetic-stay-v1')",
            env.org, booking, operation,
        )
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM booking_stays WHERE organization_id=$1 AND booking_id=$2', env.org, booking,
    ) == 1
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM booking_stay_purge_fences WHERE organization_id=$1 AND booking_id=$2', env.org, booking,
    ) == 0


async def test_purge_rolls_back_receipts_stay_and_tombstones_after_late_failure(pg_pool):
    from tests.core.bookings.test_hotel_allocation_archive_postgres import _complete_with_old_evidence, _archive
    env, lineage, booking = await _complete_with_old_evidence(pg_pool)
    await _archive(pg_pool, env, booking)
    await pg_pool.execute(
        "UPDATE bookings SET updated_at=now()-interval '2 days' WHERE organization_id=$1 AND id=$2",
        env.org, booking,
    )
    suffix = uuid4().hex
    function_name = f'test_stay_purge_fault_{suffix}'
    trigger_name = f'test_stay_purge_fault_{suffix}'
    await pg_pool.execute(
        f"""CREATE FUNCTION public.{function_name}() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'forced stay purge audit failure'; END $$"""
    )
    await pg_pool.execute(
        f'''CREATE TRIGGER {trigger_name} BEFORE INSERT ON public.booking_stay_purge_audit
            FOR EACH ROW EXECUTE FUNCTION public.{function_name}()'''
    )
    try:
        with pytest.raises(Exception, match='forced stay purge audit failure'):
            await _purge_retention(pg_pool, env.org, booking)
    finally:
        await pg_pool.execute(f'DROP TRIGGER IF EXISTS {trigger_name} ON public.booking_stay_purge_audit')
        await pg_pool.execute(f'DROP FUNCTION IF EXISTS public.{function_name}()')

    assert await pg_pool.fetchval(
        'SELECT count(*) FROM booking_stays WHERE organization_id=$1 AND booking_id=$2', env.org, booking,
    ) == 1
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1 AND booking_id=$2', env.org, booking,
    ) == 2
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM booking_stay_tombstones WHERE organization_id=$1 AND booking_id=$2', env.org, booking,
    ) == 0
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM booking_stay_purge_fences WHERE organization_id=$1 AND booking_id=$2', env.org, booking,
    ) == 0


async def test_backend_cannot_call_purge_write_marker_or_delete_stay(pg_pool):
    env, lineage = await hotel_setup(pg_pool)
    booking = await _create_stay(env, lineage, terminal=True)
    statements = [
        ("SELECT public.booking_stay_admin_purge($1,$2,'retention',NULL,'synthetic-stay-v1')", (env.org, booking)),
        ('INSERT INTO public.booking_stay_purge_markers VALUES(txid_current(),$1,$2,$3,\'retention\',NULL,\'v1\')',
         (env.org, booking, lineage.contact_id)),
        ('DELETE FROM public.booking_stays WHERE organization_id=$1 AND booking_id=$2', (env.org, booking)),
    ]
    async with pg_pool.acquire() as conn, conn.transaction():
        for statement, args in statements:
            with pytest.raises(Exception):
                async with conn.transaction():
                    await conn.execute('SET LOCAL ROLE service_role')
                    await conn.execute(statement, *args)
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM booking_stays WHERE organization_id=$1 AND booking_id=$2', env.org, booking,
    ) == 1


async def test_wrong_transaction_marker_cannot_delete_allocation_receipt(pg_pool):
    env, lineage = await hotel_setup(pg_pool)
    booking = await _create_stay(env, lineage, terminal=True)
    request_id = await pg_pool.fetchval(
        'SELECT request_id FROM booking_allocation_requests WHERE organization_id=$1 AND booking_id=$2 LIMIT 1',
        env.org, booking,
    )
    async with pg_pool.acquire() as conn, conn.transaction():
        txid = await conn.fetchval('SELECT txid_current()')
        await conn.execute(
            '''INSERT INTO booking_stay_purge_markers
               (transaction_id,organization_id,booking_id,original_subject_id,purpose,operation_id,policy_version)
               VALUES($1,$2,$3,$4,'retention',NULL,'synthetic-stay-v1')''',
            txid + 1, env.org, booking, lineage.contact_id,
        )
        with pytest.raises(Exception, match='Allocation receipt is immutable'):
            async with conn.transaction():
                await conn.execute(
                    '''DELETE FROM booking_allocation_requests
                       WHERE organization_id=$1 AND booking_id=$2 AND request_id=$3''',
                    env.org, booking, request_id,
                )
        await conn.execute(
            'DELETE FROM booking_stay_purge_markers WHERE transaction_id=$1 AND organization_id=$2 AND booking_id=$3',
            txid + 1, env.org, booking,
        )
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1 AND booking_id=$2', env.org, booking,
    ) == 2


async def test_owner_direct_stay_delete_without_purge_marker_is_denied(pg_pool):
    env, lineage = await hotel_setup(pg_pool)
    booking = await _create_stay(env, lineage, terminal=True)
    with pytest.raises(Exception, match='(?i)(purge|stay|immutable)'):
        async with pg_pool.acquire() as conn, conn.transaction():
            await conn.execute(
                'DELETE FROM booking_stays WHERE organization_id=$1 AND booking_id=$2', env.org, booking,
            )
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM booking_stays WHERE organization_id=$1 AND booking_id=$2', env.org, booking,
    ) == 1

@pytest.mark.parametrize('scope', ['organization', 'subject', 'conversation', 'record'])
async def test_completed_stay_erasure_respects_hold_then_fences_replay(pg_pool, scope):
    from src.core.gdpr.operations import Operations
    from tests.core.bookings.test_hotel_allocation_archive_postgres import _complete_with_old_evidence, _archive

    env, lineage, booking = await _complete_with_old_evidence(pg_pool)
    await _archive(pg_pool, env, booking)
    for category in ('contact_content', 'cutover'):
        await pg_pool.execute("""INSERT INTO gdpr_policies(organization_id,category,version,erasure_allowed,
            retention_days,legal_hold,approval_reference,approved_by,approved_at,valid_until)
            VALUES($1,$2,'erasure-v1',true,NULL,false,'test','qa',now()-interval '1 day',now()+interval '1 day')""", env.org, category)
    operation = await Operations(pg_pool).create(env.org, 'qa', uuid4(), kind='erase', scope='contact', subject_id=lineage.contact_id)
    hold = uuid4()
    scope_id = {'organization': None, 'subject': lineage.contact_id,
                'conversation': lineage.conversation_id, 'record': booking}[scope]
    await pg_pool.execute("""INSERT INTO gdpr_retention_holds
        (organization_id,hold_id,category,scope,scope_id,reason_code,authority_reference,approved_by,approved_at,review_by)
        VALUES($1,$2,'booking_stay_content',$3,$4,'test','test:hold','qa',now(),now()+interval '1 month')""", env.org, hold, scope, scope_id)
    await pg_pool.execute("""INSERT INTO gdpr_erasure_barriers
        (organization_id,operation_id,scope,subject_id,identifier_digest,key_id)
        VALUES($1,$2,'contact',$3,$4,'synthetic')""", env.org, operation, lineage.contact_id, 'a' * 64)
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_stay_completion_evidence WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 1
    with pytest.raises(Exception, match='Stay content held'):
        await pg_pool.fetchval("SELECT booking_stay_admin_purge($1,$2,'erasure',$3,'synthetic-stay-v1')", env.org, booking, operation)
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_stay_purge_fences WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 0
    await pg_pool.execute("""INSERT INTO gdpr_retention_hold_events
        (organization_id,hold_id,event_id,kind,authority_reference,approved_by,occurred_at)
        VALUES($1,$2,$3,'released','test:release','qa',now())""", env.org, hold, uuid4())
    assert await pg_pool.fetchval("SELECT booking_stay_admin_purge($1,$2,'erasure',$3,'synthetic-stay-v1')", env.org, booking, operation) == 2
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_stay_completion_evidence WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 0
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_stay_tombstones WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 2
