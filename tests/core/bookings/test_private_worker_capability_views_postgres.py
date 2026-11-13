"""PostgreSQL coverage for the 093 private booking-worker bridge."""

import asyncpg
import json
import pytest
from pathlib import Path
from uuid import uuid4

from tests.core.bookings.test_conversation_state_postgres import apply, commit_source, state_env  # noqa: F401
from tests.core.bookings.test_worker_authority_postgres import _persist_ingress
from tests.core.test_quota_reservation_lifecycle import _organization

pytestmark = [pytest.mark.asyncio, pytest.mark.migration_limit(93)]

_PRIVATE_VIEW_NAMES = {
    'organizations', 'bookings', 'booking_stays', 'booking_settings', 'messages',
    'conversations', 'contacts', 'booking_catalog_publications', 'booking_catalog_requests',
    'booking_conversation_states', 'booking_conversation_requests', 'booking_conversation_revocations',
    'booking_worker_claims', 'inbound_worker_operations', 'inbound_worker_receipts',
    'meta_webhook_inbox', 'booking_sector_admission_fences', 'booking_sector_admission_epochs',
    'booking_allocation_sets', 'booking_resource_allocations', 'booking_allocation_requests',
    'booking_confirmation_outbox', 'booking_stay_purge_fences', 'booking_stay_tombstones',
    'gdpr_erasure_barriers', 'gdpr_retention_holds', 'usage_events',
    'c24_idle_watermarks', 'c24_source_fences', 'message_delivery_attempts',
    'booking_availability', 'booking_resource_availability', 'simulation_requests',
    'worker_outbound_messages',
}

_PRIVATE_EXECUTABLES = {
    'c24_worker_readiness()',
    'c24_quota_trust_epoch()',
    'c24_worker_next_jobs(uuid,integer)',
    'c24_worker_begin_job(uuid,text)',
    'c24_worker_consume_capability(uuid)',
    'c24_worker_renew_job(uuid,uuid,uuid,bigint,integer)',
    'c24_worker_finish_job()',
    'c24_worker_scope_allows(uuid,text)',
    'c24_worker_scope_allows_message(uuid,text)',
    'c24_worker_scope_allows_claim_source(uuid,text)',
    'c24_worker_scope_allows_operation(uuid,uuid,text)',
    'c24_worker_scope_allows_request(uuid,uuid,uuid,text)',
    'c24_worker_scope_allows_conversation(uuid,uuid,text)',
    'c24_worker_scope_allows_contact(uuid,uuid,text)',
    'c24_worker_scope_allows_inbox(uuid,uuid,text)',
    'c24_worker_scope_allows_booking(uuid,uuid,text)',
    'c24_worker_scope_allows_bound_booking(uuid,uuid,text)',
    'c24_worker_scope_allows_simulation_request(uuid,uuid,uuid,text)',
    'c24_worker_claim_source_allows(uuid,text,text,text)',
    'c24_worker_claim_booking_insert_allows(uuid,uuid,text,text,text)',
    'c24_worker_outbound_booking_message_allows(uuid,uuid,text,text,text)',
    'c24_worker_mark_sector_insert(uuid,uuid)',
    'c24_worker_attach_calendar_source(uuid,uuid,uuid)',
    'c24_worker_gdpr_guard(text,text,jsonb,jsonb)',
}


async def test_093_request_scope_separates_inbound_actor_from_simulator_membership(pg_pool):
    definition = await pg_pool.fetchval("""
        SELECT pg_get_functiondef(
            'c24_private.c24_worker_issue_capability(uuid,uuid,uuid,bigint,uuid,jsonb,text,integer)'::regprocedure
        )
    """)
    scope_definition = await pg_pool.fetchval("""
        SELECT pg_get_functiondef(
            'c24_private.c24_worker_scope_allows_simulation_request(uuid,uuid,uuid,text)'::regprocedure
        )
    """)
    # Inbound's quota actor is a deterministic internal identity rather than
    # an Auth user. It is admitted only through the persisted inbound proof;
    # simulator rows retain live, tenant-scoped membership validation.
    assert "event.metadata->>'channel'='inbound'" in definition
    assert "inbox.channel='whatsapp' AND inbox.status='completed'" in definition
    assert "NOT EXISTS (SELECT 1 FROM public.auth_access_lifecycle AS access" in definition
    assert "NOT EXISTS (SELECT 1 FROM public.user_profiles AS profile" in definition
    assert "event.metadata->>'channel'='inbound'" in scope_definition
    assert "event.metadata->>'channel'='simulator'" in scope_definition
    assert "FROM public.auth_access_lifecycle AS access" in scope_definition
    assert "access.disabled_at IS NULL" in scope_definition
    assert "JOIN public.user_profiles AS profile" in scope_definition
    assert "JOIN public.organization_memberships AS membership" in scope_definition
    assert "membership.joined_at IS NOT NULL" in scope_definition
    assert "membership.ruolo IN ('owner','manager','staff')" in scope_definition
    assert "membership.organization_id=request.organization_id" in scope_definition
    assert "request.auth_user_id=p_actor_id" in scope_definition
    assert "request.auth_user_id='" not in definition
    assert "request.auth_user_id='" not in scope_definition


async def test_093_worker_role_and_capability_acl_inventory(pg_pool):
    role = await pg_pool.fetchrow("""
        SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,rolcreaterole,
               rolreplication,rolbypassrls
        FROM pg_roles WHERE rolname='melpis_c24_booking_worker'
    """)
    assert dict(role) == {
        'rolcanlogin': False, 'rolinherit': False, 'rolsuper': False,
        'rolcreatedb': False, 'rolcreaterole': False,
        'rolreplication': False, 'rolbypassrls': False,
    }

    memberships = await pg_pool.fetch("""
        SELECT member.rolname AS member_name,grantor.rolname AS grantor_name,
               m.admin_option,m.set_option,m.inherit_option
        FROM pg_auth_members m
        JOIN pg_roles parent ON parent.oid=m.roleid
        JOIN pg_roles member ON member.oid=m.member
        JOIN pg_roles grantor ON grantor.oid=m.grantor
        WHERE parent.rolname='melpis_c24_booking_worker'
           OR member.rolname='melpis_c24_booking_worker'
    """)
    assert all(row['member_name'] == 'postgres'
               and row['grantor_name'] in {'postgres', 'supabase_admin'}
               and row['admin_option'] is True
               and row['set_option'] is False
               and row['inherit_option'] is False
               for row in memberships)
    for principal in ('anon', 'authenticated', 'service_role'):
        assert not await pg_pool.fetchval(
            "SELECT pg_has_role($1,'melpis_c24_booking_worker','MEMBER')", principal,
        )

    public_acl = await pg_pool.fetchval("""
        SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname NOT IN ('pg_catalog','information_schema','pg_toast','c24_private')
          AND n.nspname NOT LIKE 'pg_temp_%' AND n.nspname NOT LIKE 'pg_toast_temp_%'
          AND c.relkind IN ('r','p','v','m','f','S')
          AND ((c.relkind='S' AND has_sequence_privilege(
                   'melpis_c24_booking_worker',c.oid,'USAGE,SELECT,UPDATE'))
            OR (c.relkind<>'S' AND (
                 has_table_privilege('melpis_c24_booking_worker',c.oid,
                    'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                 OR EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid
                      AND a.attnum>0 AND NOT a.attisdropped
                      AND (has_column_privilege('melpis_c24_booking_worker',c.oid,a.attnum,'SELECT')
                        OR has_column_privilege('melpis_c24_booking_worker',c.oid,a.attnum,'INSERT')
                        OR has_column_privilege('melpis_c24_booking_worker',c.oid,a.attnum,'UPDATE')
                        OR has_column_privilege('melpis_c24_booking_worker',c.oid,a.attnum,'REFERENCES')))
            )))
          OR (current_setting('server_version_num')::int>=170000
              AND CASE WHEN current_setting('server_version_num')::int>=170000
                       THEN has_table_privilege('melpis_c24_booking_worker',c.oid,'MAINTAIN')
                       ELSE false END)
    """)
    assert public_acl == 0
    public_definer = await pg_pool.fetchval("""
        SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname NOT IN ('pg_catalog','information_schema','pg_toast','c24_private')
          AND n.nspname NOT LIKE 'pg_temp_%' AND n.nspname NOT LIKE 'pg_toast_temp_%'
          AND p.prosecdef AND has_schema_privilege('melpis_c24_booking_worker',n.oid,'USAGE')
          AND has_function_privilege('melpis_c24_booking_worker',p.oid,'EXECUTE')
    """)
    assert public_definer == 0

    private_acl = await pg_pool.fetch("""
        SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='c24_private' AND c.relkind='v'
          AND has_table_privilege('melpis_c24_booking_worker',c.oid,'SELECT')
    """)
    assert {row['relname'] for row in private_acl} == _PRIVATE_VIEW_NAMES
    assert await pg_pool.fetchval("""
        SELECT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='c24_private' AND c.relname='worker_booking_inserts' AND c.relkind='v')
    """) is True
    insert_views = await pg_pool.fetch("""
        SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='c24_private' AND c.relkind='v'
          AND has_table_privilege('melpis_c24_booking_worker',c.oid,'INSERT')
    """)
    assert {row['relname'] for row in insert_views} == {
        'worker_booking_inserts','worker_booking_stays_inserts','worker_allocation_sets',
        'worker_resource_allocations','worker_allocation_requests',
        'worker_confirmation_outbox','worker_outbound_messages',
    }
    select_views = await pg_pool.fetch("""
        SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='c24_private' AND c.relkind='v'
          AND has_table_privilege('melpis_c24_booking_worker',c.oid,'SELECT')
    """)
    selected_view_names = {row['relname'] for row in select_views}
    assert selected_view_names == _PRIVATE_VIEW_NAMES
    assert not (selected_view_names & {
        'worker_booking_inserts','worker_booking_stays_inserts','worker_allocation_sets',
        'worker_resource_allocations','worker_allocation_requests','worker_confirmation_outbox',
    })
    delete_views = await pg_pool.fetch("""
        SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='c24_private' AND c.relkind='v'
          AND has_table_privilege('melpis_c24_booking_worker',c.oid,'DELETE')
    """)
    assert {row['relname'] for row in delete_views} == set()
    assert await pg_pool.fetchval("""
        SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='c24_private' AND c.relkind='v'
          AND has_table_privilege('melpis_c24_booking_worker',c.oid,'UPDATE')
    """) == 0
    update_columns = await pg_pool.fetch("""
        SELECT c.relname,a.attname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_attribute a ON a.attrelid=c.oid
        WHERE n.nspname='c24_private' AND c.relkind='v'
          AND a.attnum>0 AND NOT a.attisdropped
          AND has_column_privilege('melpis_c24_booking_worker',c.oid,a.attnum,'UPDATE')
    """)
    assert {f"{row['relname']}.{row['attname']}" for row in update_columns} == {
        'organizations.id','messages.id','conversations.id','contacts.id','bookings.id',
        'booking_settings.organization_id','inbound_worker_receipts.organization_id',
        'inbound_worker_operations.organization_id','meta_webhook_inbox.organization_id',
        'usage_events.id','booking_conversation_states.organization_id',
        'booking_conversation_states.conversation_id','booking_conversation_requests.organization_id',
        'booking_conversation_requests.conversation_id','booking_stays.revision',
        'booking_worker_claims.claim_id',
        'simulation_requests.request_id',
    }
    inbox_columns = await pg_pool.fetch("""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema='c24_private' AND table_name='meta_webhook_inbox'
    """)
    assert {row['column_name'] for row in inbox_columns} == {
        'id','organization_id','channel','status',
    }
    context_policy = await pg_pool.fetchrow("""
        SELECT relrowsecurity,relforcerowsecurity,pg_get_userbyid(relowner) AS owner_name
        FROM pg_class WHERE oid='c24_private.worker_context'::regclass
    """)
    assert context_policy['relrowsecurity'] is True
    assert context_policy['relforcerowsecurity'] is False
    assert context_policy['owner_name'] not in {
        'anon','authenticated','service_role','melpis_c24_booking_worker',
    }
    assert await pg_pool.fetchval("SELECT has_schema_privilege('melpis_c24_booking_worker','public','USAGE')")
    assert not await pg_pool.fetchval("SELECT has_schema_privilege('melpis_c24_booking_worker','public','CREATE')")
    protected_updates = {
        'organizations','messages','conversations','contacts','bookings','booking_settings',
        'inbound_worker_receipts','inbound_worker_operations','meta_webhook_inbox','usage_events',
        'simulation_requests','booking_conversation_states','booking_conversation_requests',
        'booking_stays','booking_worker_claims',
    }
    readonly_triggers = await pg_pool.fetch("""
        SELECT c.relname,t.tgenabled,p.proname
        FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        JOIN pg_proc p ON p.oid=t.tgfoid JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND t.tgname='c24_private_worker_readonly_update'
          AND NOT t.tgisinternal
    """)
    assert {row['relname'] for row in readonly_triggers} == protected_updates
    assert all(row['tgenabled'] != 'D' and row['proname']=='c24_private_worker_reject_update'
               for row in readonly_triggers)
    if await pg_pool.fetchval("SELECT current_setting('server_version_num')::int >= 170000"):
        assert not await pg_pool.fetchval("SELECT has_table_privilege('melpis_c24_booking_worker','public.messages','MAINTAIN')")
    trigger_helpers = await pg_pool.fetch("""
        SELECT p.proname,p.prosecdef,pg_get_userbyid(p.proowner) AS owner_name,
               p.proconfig
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE (n.nspname='public' AND p.proname IN
               ('booking_allocation_guard','calendar_track_booking','booking_message_original_subject'))
           OR (n.nspname='c24_private' AND p.proname='c24_worker_gdpr_guard')
    """)
    assert {row['proname'] for row in trigger_helpers} == {
        'booking_allocation_guard','calendar_track_booking',
        'booking_message_original_subject','c24_worker_gdpr_guard',
    }
    assert all(row['prosecdef'] is True and row['owner_name']==context_policy['owner_name']
               for row in trigger_helpers)
    assert all('search_path=pg_catalog, pg_temp' in (row['proconfig'] or [])
               for row in trigger_helpers)
    effect_trigger = await pg_pool.fetchrow("""
        SELECT t.tgtype::int AS trigger_type, t.tgenabled
        FROM pg_trigger t
        WHERE t.tgrelid='public.bookings'::regclass
          AND t.tgname='c24_worker_capability_effect_guard_after_insert'
          AND NOT t.tgisinternal
    """)
    assert effect_trigger is not None
    # Row-level AFTER INSERT: the guard sees values written by calendar_track_booking
    # and any other BEFORE normalizer, while still aborting the same transaction.
    assert effect_trigger['trigger_type'] & 1  # ROW
    assert effect_trigger['trigger_type'] & 4  # INSERT
    assert not effect_trigger['trigger_type'] & 2  # not BEFORE
    assert effect_trigger['tgenabled'] != 'D'
    stay_source_guard = await pg_pool.fetchrow("""
        SELECT p.prosecdef,p.prosrc FROM pg_proc p
        WHERE p.oid='public.booking_stay_source_guard()'::regprocedure
    """)
    assert stay_source_guard['prosecdef'] is False
    assert 'c24_worker_scope_allows_message' in stay_source_guard['prosrc']
    stay_trigger_security = await pg_pool.fetchrow("""
        SELECT p.prosecdef,pg_get_userbyid(p.proowner) AS owner_name,p.proconfig
        FROM pg_proc p WHERE p.oid='public.booking_stay_guard()'::regprocedure
    """)
    stay_helper_security = await pg_pool.fetchrow("""
        SELECT p.prosecdef,pg_get_userbyid(p.proowner) AS owner_name,p.proconfig
        FROM pg_proc p WHERE p.oid='c24_private.c24_worker_stay_insert_guard(uuid,uuid,uuid,uuid,uuid,text)'::regprocedure
    """)
    for security in (stay_trigger_security,stay_helper_security):
        assert security['prosecdef'] is True
        assert security['owner_name']==context_policy['owner_name']
        assert 'search_path=pg_catalog, pg_temp' in (security['proconfig'] or [])
    assert not await pg_pool.fetchval("""
        SELECT has_function_privilege('melpis_c24_booking_worker',
            'c24_private.c24_worker_stay_insert_guard(uuid,uuid,uuid,uuid,uuid,text)','EXECUTE')
    """)
    assert not await pg_pool.fetchval("""
        SELECT has_table_privilege('melpis_c24_booking_worker',
            'c24_private.worker_context','SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
    """)

    executable = await pg_pool.fetch("""
        SELECT replace(p.oid::regprocedure::text,'c24_private.','') AS signature
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='c24_private'
          AND has_function_privilege('melpis_c24_booking_worker',p.oid,'EXECUTE')
    """)
    assert {row['signature'] for row in executable} == _PRIVATE_EXECUTABLES
    for principal in ('anon','authenticated','service_role'):
        assert not await pg_pool.fetchval(
            "SELECT has_schema_privilege($1,'c24_private','USAGE,CREATE')", principal,
        )
        assert await pg_pool.fetchval("""
            SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='c24_private'
              AND has_table_privilege($1,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
        """, principal) == 0
        assert await pg_pool.fetchval("""
            SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='c24_private' AND has_function_privilege($1,p.oid,'EXECUTE')
        """, principal) == 0
    lock_guard = await pg_pool.fetchval("""
        SELECT count(*) FROM pg_trigger t
        JOIN pg_class c ON c.oid=t.tgrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE t.tgname='c24_private_worker_readonly_update' AND NOT t.tgisinternal
          AND n.nspname='public'
    """)
    assert lock_guard == 15
    assert await pg_pool.fetchval(
        "SELECT to_regprocedure('public.c24_private_worker_reject_update()') IS NOT NULL"
    ) is True


async def test_093_removes_only_the_legacy_086_update_guard(pg_pool):
    await pg_pool.execute("""
        CREATE OR REPLACE FUNCTION public.c24_private_worker_reject_update()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'legacy guard'; END $$;
        DROP TRIGGER IF EXISTS c24_private_worker_readonly_update ON public.bookings;
        CREATE TRIGGER c24_private_worker_readonly_update
        BEFORE UPDATE ON public.bookings
        FOR EACH ROW EXECUTE FUNCTION public.c24_private_worker_reject_update();
    """)
    sql = Path('src/core/db/migrations/093_private_worker_capability_views.sql').read_text(encoding='utf-8')
    await pg_pool.execute(sql)
    assert await pg_pool.fetchval("""
        SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE t.tgname='c24_private_worker_readonly_update' AND NOT t.tgisinternal
          AND n.nspname='public'
    """) == 15
    assert await pg_pool.fetchval(
        "SELECT to_regprocedure('public.c24_private_worker_reject_update()') IS NOT NULL"
    ) is True


async def test_auto_updatable_view_preserves_primary_key_conflict_arbiter(pg_pool):
    plan = await pg_pool.fetch('''EXPLAIN INSERT INTO c24_private.booking_allocation_sets
            (organization_id,booking_id,allocation_revision,state,catalog_revision,
             service_id,timezone,temporal_kind,starts_at,ends_at,booking_fingerprint,
             booking_calendar_revision)
            VALUES(gen_random_uuid(),gen_random_uuid(),1,'active',1,'standard','UTC','minutes',
                clock_timestamp(),clock_timestamp()+interval '1 hour',repeat('a',64),0)
            ON CONFLICT (organization_id,booking_id) DO NOTHING''')
    rendered = '\n'.join(row['QUERY PLAN'] for row in plan)
    assert 'Conflict Resolution: NOTHING' in rendered
    assert 'booking_allocation_sets_pkey' in rendered


async def test_begin_job_rejects_a04_ingress_without_persisted_proposal_state(
    pg_pool, private_worker_pool, monkeypatch,
):
    from tests.core.bookings.test_a05_private_worker_postgres import _commit_synthetic_a04, _setup

    org, _ = await _setup(pg_pool)
    day = await pg_pool.fetchval("SELECT (now() AT TIME ZONE 'Europe/Rome')::date + 10")
    text = f'Prenotazione sintetica {day.isoformat()} 19:00'
    monkeypatch.setenv('C24_PRIVATE_BOOKING_WORKER_ENABLED', 'true')
    message, _, _ = await _commit_synthetic_a04(pg_pool, org, text, day=day)

    async with private_worker_pool.acquire() as conn:
        for kind in ('a05', 'confirmation'):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.fetchrow(
                    'SELECT * FROM c24_private.c24_worker_begin_job($1,$2)', message['id'], kind,
                )
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM booking_worker_claims WHERE organization_id=$1 AND message_id=$2',
        org, message['id'],
    ) == 0


async def test_db_job_claims_do_not_grant_worker_capabilities(state_env, private_worker_pool, monkeypatch):
    from src.core.bookings.conversation_state import render_confirmation_prompt

    env = state_env
    pool, org, conversation, message = env['pool'], env['org'], env['conv'], env['message']
    monkeypatch.setenv('C24_PRIVATE_BOOKING_WORKER_ENABLED', 'true')
    provider_id = f'synthetic-private-worker-{uuid4().hex}'
    await pool.execute('UPDATE messages SET wam_id=$3 WHERE organization_id=$1 AND id=$2',
                       org, message, provider_id)
    await _persist_ingress(pool, org, provider_id)
    provider_timestamp = await pool.fetchval('SELECT floor(extract(epoch from clock_timestamp()))::bigint')
    await commit_source(env, message, 'Richiesta tavolo 2026-12-01 19:00',
        {'service_id':'standard','customer_name':True,'appointment_date':'2026-12-01',
         'appointment_time':'19:00','party_size':2},
        provider_timestamp, provider_id=provider_id)
    assert await pool.fetchval('''SELECT count(*) FROM inbound_worker_operations o
        JOIN inbound_worker_receipts r ON r.organization_id=o.organization_id AND r.inbox_id=o.inbox_id
         AND r.message_id=o.message_id
        WHERE o.organization_id=$1 AND o.message_id=$2''', org, message) == 1
    state_result = await env['repo'].apply_worker_source(org, conversation, message)
    assert state_result['applied_revision'] == 1

    other_org = (await _organization(pool))['id']
    from tests.core.test_quota_reservation_lifecycle import _inbound_message
    other_message = await _inbound_message(pool, other_org, canale='whatsapp')
    day = await pool.fetchval("SELECT (now() AT TIME ZONE 'Europe/Rome')::date + 11")
    same_tenant_booking = uuid4()
    other_tenant_booking = uuid4()
    await pool.execute("""
        INSERT INTO bookings(id,organization_id,nome_cliente,data,ora,source_message_id)
        VALUES($1,$2,'Synthetic same-tenant row',$3,'20:00','unrelated-source')
    """, same_tenant_booking,org,day)
    await pool.execute("""
        INSERT INTO bookings(id,organization_id,nome_cliente,data,ora,source_message_id)
        VALUES($1,$2,'Synthetic other-tenant row',$3,'20:00','other-source')
    """, other_tenant_booking,other_org,day)
    before_event_log = await pool.fetchval(
        'SELECT count(*) FROM public.event_log WHERE organization_id=$1',org,
    )
    async with private_worker_pool.acquire() as conn, conn.transaction():
        job = await conn.fetchrow(
            "SELECT * FROM c24_private.c24_worker_begin_job($1,'a05')", message,
        )
        assert job['status'] == 'claimed'
        assert job['organization_id'] == org and job['conversation_id'] == conversation
        assert job['claim_message_id'] == message
        assert await conn.fetchval('''SELECT count(*) FROM c24_private.booking_worker_claims
            WHERE organization_id=$1 AND message_id=$2''',org,message) == 0
        assert await conn.fetchval(
            'SELECT count(*) FROM c24_private.messages WHERE organization_id=$1 AND id=$2',
            org, message,
        ) == 0
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.fetchval('SELECT * FROM c24_private.worker_context')
        assert await conn.fetchval(
            'SELECT count(*) FROM c24_private.organizations WHERE id=$1', org,
        ) == 0
        assert await conn.fetchval(
            'SELECT count(*) FROM c24_private.messages WHERE organization_id=$1 AND id=$2',
            org, message,
        ) == 0
        assert await conn.fetchval(
            'SELECT count(*) FROM c24_private.messages WHERE organization_id=$1 AND id=$2',
            other_org, other_message,
        ) == 0
        assert await conn.fetchval(
            'SELECT count(*) FROM c24_private.bookings WHERE id=$1',same_tenant_booking,
        ) == 0
        assert await conn.fetchval(
            'SELECT count(*) FROM c24_private.bookings WHERE id=$1',other_tenant_booking,
        ) == 0
        # A queue claim alone is not a database capability. Until an
        # application-verified capability is consumed, the worker cannot read
        # even same-tenant booking rows.
        assert await conn.fetchval(
            'SELECT count(*) FROM c24_private.booking_availability WHERE id=$1',same_tenant_booking,
        ) == 0
        assert await conn.fetchval(
            'SELECT count(*) FROM c24_private.booking_availability WHERE id=$1',other_tenant_booking,
        ) == 0
        assert {'nome_cliente','telefono','note'}.isdisjoint({
            row['column_name'] for row in await conn.fetch("""
                SELECT column_name FROM information_schema.columns
                WHERE table_schema='c24_private' AND table_name='booking_availability'
            """)
        })
        async with conn.transaction():
            assert await conn.execute(
                'UPDATE c24_private.messages SET id=id WHERE organization_id=$1 AND id=$2',
                org,message,
            ) == 'UPDATE 0'
        async with conn.transaction():
            assert await conn.execute(
                'UPDATE c24_private.booking_worker_claims SET claim_id=claim_id '
                'WHERE organization_id=$1 AND message_id=$2',org,message,
            ) == 'UPDATE 0'
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.execute(
                    'UPDATE c24_private.messages SET content_text=$1 WHERE id=$2',
                    'forbidden mutation',message,
                )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.execute(
                    'INSERT INTO c24_private.messages(id,organization_id,conversation_id,direction,message_type,content) '
                    "VALUES($1,$2,$3,'outbound','text','{}'::jsonb)",uuid4(),org,conversation,
                )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.fetchval(
                    'SELECT c24_private.c24_worker_mark_sector_insert($1,$2)',org,uuid4(),
                )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.fetchval('SELECT c24_private.c24_worker_consume_capability($1)',uuid4())
        # A claim without a consumed capability has no transaction-local context
        # to finish. It must not be represented as a successful booking finalizer.
        assert await conn.fetchval('SELECT c24_private.c24_worker_finish_job()') is False
        assert await conn.fetchval(
            'SELECT count(*) FROM c24_private.messages WHERE organization_id=$1 AND id=$2',
            org, message,
        ) == 0
    # The dormant/claim-only worker has no direct mutation grants. Keep each
    # expected denial in autocommit so one failure cannot poison later probes.
    forbidden_writes = [
        ("INSERT INTO c24_private.worker_booking_inserts "
         "(id,organization_id,nome_cliente,data,ora,source_message_id,id_conversazione) "
         "VALUES($1,$2,'Wrong booking',$3,'21:00',$4,$5)",
         (same_tenant_booking,org,day,str(message),str(conversation))),
        ("INSERT INTO c24_private.worker_allocation_sets "
         "(organization_id,booking_id,allocation_revision,state,catalog_revision,service_id,timezone,"
         "temporal_kind,starts_at,ends_at,booking_fingerprint,booking_calendar_revision) "
         "VALUES($1,$2,1,'active',1,'standard','UTC','minutes',clock_timestamp(),"
         "clock_timestamp()+interval '1 hour',repeat('a',64),0)", (org,same_tenant_booking)),
        ("INSERT INTO c24_private.worker_confirmation_outbox "
         "(id,organization_id,booking_id,inbound_message_id,status,booking_revision) "
         "VALUES($1,$2,$3,$4,'pending',0)", (uuid4(),org,same_tenant_booking,message)),
        ("INSERT INTO c24_private.message_delivery_attempts(id,message_id,next_retry_at) "
         "VALUES($1,$2,clock_timestamp())", (uuid4(),message)),
        ("UPDATE public.messages SET content_text=$1 WHERE id=$2", ('forbidden mutation',message)),
    ]
    for sql, values in forbidden_writes:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with private_worker_pool.acquire() as direct_conn:
                await direct_conn.execute(sql,*values)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with private_worker_pool.acquire() as direct_conn:
            await direct_conn.execute("UPDATE c24_private.worker_allocation_sets SET state='deleted'")
    assert await pool.fetchval(
        'SELECT count(*) FROM public.event_log WHERE organization_id=$1',org,
    ) == before_event_log

    await pool.execute('''UPDATE booking_worker_claims SET lease_until=clock_timestamp()-interval '1 second'
        WHERE organization_id=$1 AND message_id=$2''', org, message)
    prompt_id = uuid4()
    prompt_body = render_confirmation_prompt(state_result['state'], prompt_id)
    await pool.execute('''INSERT INTO messages
        (id,organization_id,conversation_id,direction,message_type,content,content_text,status,
         handling_type,wam_id,sent_at)
        VALUES($1,$2,$3,'outbound','text','{}'::jsonb,$4,'sent','c24_confirmation_prompt',$5,
               clock_timestamp()-interval '1 minute')''',
        prompt_id, org, conversation, prompt_body, f'synthetic-prompt-{uuid4().hex}')
    reply_id = uuid4()
    reply_wam = f'synthetic-confirmation-{uuid4().hex}'
    reply_text = f'CONFIRM {prompt_id}'
    reply_timestamp = await pool.fetchval(
        'SELECT GREATEST(floor(extract(epoch from clock_timestamp()))::bigint,$1+1)',
        provider_timestamp,
    )
    await pool.execute('''INSERT INTO messages
        (id,organization_id,conversation_id,direction,message_type,content,status,content_text,wam_id)
        VALUES($1,$2,$3,'inbound','text',$4::jsonb,'processing',$5,$6)''',
        reply_id, org, conversation,
        json.dumps({'id':reply_wam,'type':'text','timestamp':str(reply_timestamp),
                    'text':{'body':reply_text}}),
        reply_text, reply_wam)
    await _persist_ingress(pool, org, reply_wam)
    confirmed = await env['repo'].apply_worker_source(org, conversation, reply_id)
    assert confirmed['applied_revision'] == 2
    async with private_worker_pool.acquire() as conn, conn.transaction():
        job = await conn.fetchrow(
            "SELECT * FROM c24_private.c24_worker_begin_job($1,'confirmation')", reply_id,
        )
        assert job['status'] == 'claimed'
        assert job['claim_message_id'] == message
        assert job['generation'] == 2
        assert await conn.fetchval(
            'SELECT count(*) FROM c24_private.messages WHERE organization_id=$1 AND id=$2',
            org, message,
        ) == 0
        assert await conn.fetchval('SELECT c24_private.c24_worker_finish_job()') is False


@pytest.mark.parametrize('bypass_rls', [False, True])
async def test_worker_membership_cannot_become_authority_for_other_logins(
    pg_pool, postgres_container, bypass_rls,
):
    suffix = uuid4().hex[:16]
    principal = f'c24_cap_{suffix}'
    child = f'c24_child_{suffix}' if not bypass_rls else None
    password = uuid4().hex
    org = (await _organization(pg_pool))['id']
    try:
        bypass = 'BYPASSRLS' if bypass_rls else 'NOBYPASSRLS'
        await pg_pool.execute(
            f'CREATE ROLE "{principal}" LOGIN INHERIT NOSUPERUSER NOCREATEDB '
            f'NOCREATEROLE NOREPLICATION {bypass} PASSWORD \'{password}\''
        )
        await pg_pool.execute(
            f'GRANT melpis_c24_booking_worker TO "{principal}" '
            'WITH INHERIT TRUE, SET TRUE, ADMIN TRUE'
        )
        if child:
            child_password = uuid4().hex
            await pg_pool.execute(
                f'CREATE ROLE "{child}" LOGIN INHERIT NOSUPERUSER NOCREATEDB '
                f'NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD \'{child_password}\''
            )
        else:
            child_password = None

        dsn = postgres_container.get_connection_url().replace('+psycopg2', '')
        async with asyncpg.create_pool(dsn=dsn, user=principal, password=password,
                                       min_size=1, max_size=1) as attacker:
            async with attacker.acquire() as conn:
                assert await conn.fetchval('SELECT session_user') == principal
                assert await conn.fetchval('SELECT count(*) FROM c24_private.organizations WHERE id=$1', org) == 0
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await conn.fetchval('SELECT count(*) FROM public.organizations WHERE id=$1', org)
                assert await conn.execute(
                    'UPDATE c24_private.organizations SET id=id WHERE id=$1', org,
                ) == 'UPDATE 0'
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await conn.execute('INSERT INTO c24_private.organizations(id,name) VALUES($1,$2)', uuid4(), 'denied')
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await conn.fetchrow(
                        "SELECT * FROM c24_private.c24_worker_begin_job($1,'a05')", uuid4(),
                    )
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await conn.fetchval("""
                        SELECT c24_private.c24_worker_gdpr_guard(
                                'messages','INSERT',jsonb_build_object('organization_id',$1::uuid),'{}'::jsonb)
                    """,org)
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await conn.fetchrow('SELECT * FROM c24_private.c24_worker_readiness()')
                assert await conn.fetchval(
                    "SELECT c24_private.c24_worker_scope_allows($1,'melpis_c24_booking_worker')", org,
                ) is False
                await conn.execute('SET ROLE melpis_c24_booking_worker')
                assert await conn.fetchval('SELECT session_user=current_user') is False
                assert await conn.fetchval('SELECT count(*) FROM c24_private.organizations WHERE id=$1', org) == 0
                other_org = (await _organization(pg_pool))['id']
                for tenant in (org,other_org):
                    assert await conn.fetchval(
                        'SELECT count(*) FROM c24_private.booking_availability WHERE organization_id=$1',
                        tenant,
                    ) == 0
                    assert await conn.fetchval(
                        'SELECT count(*) FROM c24_private.messages WHERE organization_id=$1',tenant,
                    ) == 0
                for query,args in (
                    ('SELECT c24_private.c24_worker_scope_allows($1,$2)',(org,'melpis_c24_booking_worker')),
                    ('SELECT c24_private.c24_worker_scope_allows_message($1,$2)',(uuid4(),'melpis_c24_booking_worker')),
                    ('SELECT c24_private.c24_worker_scope_allows_claim_source($1,$2)',(uuid4(),'melpis_c24_booking_worker')),
                    ('SELECT c24_private.c24_worker_scope_allows_operation($1,$2,$3)',(org,uuid4(),'melpis_c24_booking_worker')),
                    ('SELECT c24_private.c24_worker_scope_allows_request($1,$2,$3,$4)',(org,uuid4(),uuid4(),'melpis_c24_booking_worker')),
                    ('SELECT c24_private.c24_worker_scope_allows_conversation($1,$2,$3)',(org,uuid4(),'melpis_c24_booking_worker')),
                    ('SELECT c24_private.c24_worker_scope_allows_contact($1,$2,$3)',(org,uuid4(),'melpis_c24_booking_worker')),
                    ('SELECT c24_private.c24_worker_scope_allows_inbox($1,$2,$3)',(org,uuid4(),'melpis_c24_booking_worker')),
                    ('SELECT c24_private.c24_worker_scope_allows_booking($1,$2,$3)',(org,uuid4(),'melpis_c24_booking_worker')),
                    ('SELECT c24_private.c24_worker_scope_allows_bound_booking($1,$2,$3)',(org,uuid4(),'melpis_c24_booking_worker')),
                    ('SELECT c24_private.c24_worker_scope_allows_simulation_request($1,$2,$3,$4)',
                     (org,uuid4(),uuid4(),'melpis_c24_booking_worker')),
                    ('SELECT c24_private.c24_worker_claim_source_allows($1,$2,$3,$4)',(org,str(uuid4()),str(uuid4()),'melpis_c24_booking_worker')),
                    ('SELECT c24_private.c24_worker_claim_booking_insert_allows($1,$2,$3,$4,$5)',(org,uuid4(),str(uuid4()),str(uuid4()),'melpis_c24_booking_worker')),
                    ('SELECT c24_private.c24_worker_outbound_booking_message_allows($1,$2,$3,$4,$5)',
                     (org,uuid4(),'booking-confirm','+390000000000','melpis_c24_booking_worker')),
                ):
                    assert await conn.fetchval(query,*args) is False
                for query,args in (
                    ('SELECT * FROM c24_private.c24_worker_readiness()',()),
                    ('SELECT * FROM c24_private.c24_worker_next_jobs(NULL::uuid,10)',()),
                    ('SELECT * FROM c24_private.c24_worker_begin_job($1,$2)',(uuid4(),'a05')),
                    ('SELECT c24_private.c24_worker_finish_job()',()),
                    ('SELECT c24_private.c24_worker_mark_sector_insert($1,$2)',(org,uuid4())),
                    ('SELECT c24_private.c24_worker_attach_calendar_source($1,$2,$3)',(org,uuid4(),uuid4())),
                    ("SELECT c24_private.c24_worker_gdpr_guard('bookings','INSERT','{}'::jsonb,NULL)",()),
                ):
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        async with conn.transaction():
                            await conn.fetchrow(query,*args)
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await conn.fetchrow(
                        "SELECT * FROM c24_private.c24_worker_begin_job($1,'a05')", uuid4(),
                    )
                await conn.execute('RESET ROLE')

        if child:
            async with asyncpg.create_pool(dsn=dsn, user=principal, password=password,
                                           min_size=1, max_size=1) as admin:
                async with admin.acquire() as conn:
                    await conn.execute(
                        f'GRANT melpis_c24_booking_worker TO "{child}" '
                        'WITH INHERIT TRUE, SET TRUE, ADMIN TRUE'
                    )
            async with asyncpg.create_pool(dsn=dsn, user=child, password=child_password,
                                           min_size=1, max_size=1) as delegated:
                async with delegated.acquire() as conn:
                    assert await conn.fetchval('SELECT count(*) FROM c24_private.organizations WHERE id=$1', org) == 0
                    await conn.execute('SET ROLE melpis_c24_booking_worker')
                    assert await conn.fetchval('SELECT session_user=current_user') is False
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        await conn.fetchrow(
                            "SELECT * FROM c24_private.c24_worker_begin_job($1,'a05')", uuid4(),
                        )
    finally:
        if child:
            await pg_pool.execute(f'REVOKE melpis_c24_booking_worker FROM "{child}"')
            await pg_pool.execute(f'DROP ROLE IF EXISTS "{child}"')
        await pg_pool.execute(f'REVOKE melpis_c24_booking_worker FROM "{principal}"')
        await pg_pool.execute(f'DROP ROLE IF EXISTS "{principal}"')


async def test_093_capability_expiry_bound_allows_bounded_lease_renewal(pg_pool):
    capability = uuid4()
    # Renewal may extend beyond the initial 30-second issue TTL, but the
    # persisted capability remains bounded by the five-minute claim ceiling.
    await pg_pool.execute("""
        INSERT INTO c24_private.worker_capabilities
            (capability_id,organization_id,message_id,claim_id,generation,booking_id,
             payload,payload_hash,issued_at,expires_at)
        VALUES($1,$2,$3,$4,1,$5,'{}'::jsonb,repeat('a',64),clock_timestamp(),
               clock_timestamp()+interval '2 minutes')
    """, capability, uuid4(), uuid4(), uuid4(), uuid4())
    assert await pg_pool.fetchval(
        'SELECT expires_at-issued_at FROM c24_private.worker_capabilities WHERE capability_id=$1',
        capability,
    ) > __import__('datetime').timedelta(seconds=60)
    with pytest.raises(asyncpg.CheckViolationError):
        async with pg_pool.acquire() as conn, conn.transaction():
            await conn.execute("""
                INSERT INTO c24_private.worker_capabilities
                    (capability_id,organization_id,message_id,claim_id,generation,booking_id,
                     payload,payload_hash,issued_at,expires_at)
                VALUES($1,$2,$3,$4,1,$5,'{}'::jsonb,repeat('b',64),clock_timestamp(),
                       clock_timestamp()+interval '5 minutes 1 second')
            """, uuid4(), uuid4(), uuid4(), uuid4(), uuid4())


async def test_093_renewal_rejects_replaced_generation(
    state_env, private_worker_pool, monkeypatch,
):  # noqa: F811
    env = state_env
    pool, org, message = env['pool'], env['org'], env['message']
    monkeypatch.setenv('C24_PRIVATE_BOOKING_WORKER_ENABLED', 'true')
    from tests.core.bookings.test_worker_authority_postgres import _persist_ingress
    provider_id = f'synthetic-renew-{uuid4().hex}'
    await pool.execute('UPDATE messages SET wam_id=$3 WHERE organization_id=$1 AND id=$2',
                       org, message, provider_id)
    await _persist_ingress(pool, org, provider_id)
    stamp = await pool.fetchval('SELECT floor(extract(epoch from clock_timestamp()))::bigint')
    await commit_source(env, message, 'Richiesta tavolo 2026-12-01 19:00',
        {'service_id':'standard','customer_name':True,'appointment_date':'2026-12-01',
         'appointment_time':'19:00','party_size':2}, stamp, provider_id=provider_id)
    await env['repo'].apply_worker_source(org, env['conv'], message)

    async with private_worker_pool.acquire() as conn, conn.transaction():
        first = await conn.fetchrow(
            "SELECT * FROM c24_private.c24_worker_begin_job($1,'a05')", message,
        )
        assert first['status'] == 'claimed'
    async with private_worker_pool.acquire() as conn:
        renewed = await conn.fetchval(
            'SELECT c24_private.c24_worker_renew_job($1,$2,$3,$4,$5)',
            org, message, first['claim_id'], first['generation'], 300,
        )
        assert renewed > first['lease_until']

    await pool.execute("""UPDATE booking_worker_claims
        SET lease_until=clock_timestamp()-interval '1 second'
        WHERE organization_id=$1 AND message_id=$2 AND claim_id=$3 AND generation=$4""",
        org, message, first['claim_id'], first['generation'])
    async with private_worker_pool.acquire() as conn, conn.transaction():
        replacement = await conn.fetchrow(
            "SELECT * FROM c24_private.c24_worker_begin_job($1,'a05')", message,
        )
        assert replacement['status'] == 'claimed'
        assert replacement['generation'] == first['generation'] + 1
    async with private_worker_pool.acquire() as conn:
        with pytest.raises(asyncpg.SerializationError):
            await conn.fetchval(
                'SELECT c24_private.c24_worker_renew_job($1,$2,$3,$4,$5)',
                org, message, first['claim_id'], first['generation'], 300,
            )
        assert await conn.fetchval(
            'SELECT c24_private.c24_worker_renew_job($1,$2,$3,$4,$5)',
            org, message, replacement['claim_id'], replacement['generation'], 300,
        ) > replacement['lease_until']


async def test_093_fixed_quota_epoch_getter_has_narrow_identity_and_acl(
    pg_pool, private_worker_pool,
):
    from src.core.db.quota_provenance import require_quota_trust_epoch
    expected = await pg_pool.fetchval("""SELECT metadata->>'trust_epoch' FROM public.usage_events
        WHERE id='6206d423-cea9-5480-bb9b-59d757503f14'::uuid
          AND organization_id IS NULL AND event_type='system.quota_trust_epoch' AND quantity=0""")
    assert expected
    assert str(await pg_pool.fetchval('SELECT c24_private.c24_quota_trust_epoch()')) == expected
    async with pg_pool.acquire() as app_conn, app_conn.transaction():
        assert str(await require_quota_trust_epoch(app_conn)) == expected
        assert str(await require_quota_trust_epoch(app_conn, private_rpc=True)) == expected
    assert await pg_pool.fetchval("""SELECT has_function_privilege(
        'melpis_c24_booking_worker','c24_private.c24_quota_trust_epoch()','EXECUTE')""") is True
    for role in ('anon','authenticated','service_role'):
        assert await pg_pool.fetchval("""SELECT has_function_privilege(
            $1,'c24_private.c24_quota_trust_epoch()','EXECUTE')""",role) is False
    async with pg_pool.acquire() as app_conn, app_conn.transaction():
        await app_conn.execute('GRANT UPDATE(quantity) ON public.usage_events TO anon')
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with app_conn.transaction():
                await app_conn.fetchval('SELECT c24_private.c24_quota_trust_epoch()')
    async with private_worker_pool.acquire() as worker:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await worker.fetchval('SELECT c24_private.c24_quota_trust_epoch()')


async def test_093_booking_allocation_guard_uses_request_id_only_for_receipts(state_env):  # noqa: F811
    env = state_env
    pool, org, booking = env['pool'], env['org'], uuid4()
    day = await pool.fetchval("SELECT (now() AT TIME ZONE 'Europe/Rome')::date + 12")
    starts_at = await pool.fetchval("SELECT date_trunc('minute',clock_timestamp()+interval '2 days')")
    ends_at = starts_at + __import__('datetime').timedelta(minutes=60)
    revision = env['catalog'].catalog.revision
    await pool.execute("""INSERT INTO bookings
        (id,organization_id,contact_id,id_conversazione,nome_cliente,data,ora,coperti,
         source_message_id,stato,origine)
        VALUES($1,$2,$3,$4,'Synthetic allocation guard',$5,'19:00',2,$6,'confermata','test')""",
        booking, org, (await pool.fetchval(
            'SELECT contact_id FROM conversations WHERE organization_id=$1 AND id=$2',org,env['conv'])),
        str(env['conv']), day, str(env['message']))
    await pool.execute("""INSERT INTO booking_allocation_sets
        (organization_id,booking_id,allocation_revision,state,catalog_revision,service_id,timezone,
         temporal_kind,starts_at,ends_at,booking_fingerprint,booking_calendar_revision)
        VALUES($1,$2,1,'active',$3,'standard','Europe/Rome','minutes',$4,$5,repeat('a',64),0)""",
        org, booking, revision, starts_at, ends_at)
    await pool.execute("""INSERT INTO booking_allocation_requests
        (organization_id,request_id,booking_id,actor_user_id,action,payload_hash,outcome,
         applied_allocation_revision,catalog_revision)
        VALUES($1,$2,$3,$4,'allocate',repeat('b',64),'allocated',1,$5)""",
        org, uuid4(), booking, env['actor'], revision)


async def test_093_issue_capability_rejects_missing_job_kind(pg_pool):
    org, message, claim, booking, conversation, subject, operation, inbox = [uuid4() for _ in range(8)]
    start, end = '2026-12-01T19:00:00+01:00', '2026-12-01T20:00:00+01:00'
    digest = 'a' * 64
    payload = {
        'version': 1, 'organization_id': str(org), 'message_id': str(message),
        'source_message_id': str(message), 'conversation_id': str(conversation),
        'subject_id': str(subject), 'operation_id': str(operation), 'inbox_id': str(inbox),
        'claim_id': str(claim), 'generation': 1, 'booking_id': str(booking),
        'lease_until': '2026-11-12T13:00:00+00:00', 'stay': None,
        'booking': {
            'id': str(booking), 'organization_id': str(org), 'contact_id': str(subject),
            'id_conversazione': str(conversation), 'nome_cliente': 'Synthetic',
            'telefono': '+390000000000', 'data': '2026-12-01', 'ora': '19:00:00',
            'coperti': 2, 'note': '', 'stato': 'confermata', 'origine': 'WhatsApp',
            'source_message_id': str(message), 'calendar_confirm_pending': False,
            'richiede_intervento': False, 'calendar_revision': 0,
            'completata_at': None, 'no_show_at': None,
        },
        'allocation': {
            'semantic_hash': digest,
            'input': {'booking_id': str(booking), 'action': 'allocate',
                      'expected_allocation_revision': 0, 'expected_catalog_revision': 1,
                      'service_id': 'standard'},
            'request': {'organization_id': str(org), 'request_id': str(uuid4()),
                        'booking_id': str(booking), 'actor_user_id': None,
                        'action': 'allocate', 'payload_hash': digest, 'outcome': 'allocated',
                        'applied_allocation_revision': 1, 'catalog_revision': 1,
                        'mutation_hash': None, 'actor_kind': 'inbound_worker',
                        'service_inbox_id': str(inbox), 'service_message_id': str(message),
                        'service_operation_id': str(operation)},
            'set': {'organization_id': str(org), 'booking_id': str(booking),
                    'allocation_revision': 1, 'state': 'active', 'catalog_revision': 1,
                    'service_id': 'standard', 'timezone': 'Europe/Rome', 'temporal_kind': 'minutes',
                    'starts_at': start, 'ends_at': end, 'arrival_date': None,
                    'departure_date': None, 'booking_fingerprint': 'b' * 64,
                    'booking_calendar_revision': 0},
            'resources': [{'organization_id': str(org), 'booking_id': str(booking),
                           'allocation_revision': 1, 'catalog_revision': 1, 'state': 'active',
                           'resource_id': 'table_1', 'resource_kind': 'table', 'exclusive': True,
                           'quantity': 1, 'starts_at': start, 'ends_at': end}],
        },
        'effects': {'outbound_message': None, 'confirmation_outbox': None, 'calendar': None},
    }
    payload_json = json.dumps(payload, separators=(',', ':'))
    async with pg_pool.acquire() as conn:
        payload_hash = await conn.fetchval(
            "SELECT encode(pg_catalog.sha256(convert_to($1::jsonb::text,'UTF8')),'hex')",
            payload_json,
        )
        with pytest.raises(asyncpg.InvalidParameterValueError):
            await conn.fetchval("""SELECT c24_private.c24_worker_issue_capability(
                $1,$2,$3,1,$4,$5::jsonb,$6,20)""",
                org, message, claim, booking, payload_json, payload_hash)
