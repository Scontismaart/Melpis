"""Local PostgreSQL structural checks for the dark schema; no production writes."""
import pytest

pytestmark = pytest.mark.asyncio

async def test_scope_and_role_privileges(pg_pool):
    async with pg_pool.acquire() as conn:
        for table in ('booking_allocation_sets', 'booking_resource_allocations', 'booking_allocation_requests'):
            assert await conn.fetchval('SELECT relrowsecurity FROM pg_class WHERE oid=$1::regclass', table)
            for role in ('anon', 'authenticated'):
                for privilege in ('SELECT', 'INSERT', 'UPDATE', 'DELETE'):
                    assert not await conn.fetchval('SELECT has_table_privilege($1,$2,$3)', role, table, privilege)
            assert await conn.fetchval("SELECT has_table_privilege('service_role',$1,'SELECT')", table)
        for privilege in ('UPDATE', 'DELETE', 'TRUNCATE'):
            assert not await conn.fetchval("SELECT has_table_privilege('service_role','booking_allocation_requests',$1)", privilege)

async def test_constraints_are_composite_deferred_and_cross_revision(pg_pool):
    async with pg_pool.acquire() as conn:
        rows = await conn.fetch("SELECT contype::text AS contype,condeferrable,pg_get_constraintdef(oid) AS definition FROM pg_constraint WHERE conrelid='booking_resource_allocations'::regclass")
        fk = next(r for r in rows if r['contype'] == 'f')
        assert fk['condeferrable'] and 'organization_id, booking_id, state, allocation_revision, catalog_revision, starts_at, ends_at' in fk['definition']
        exclusion = next(r['definition'] for r in rows if r['contype'] == 'x')
        assert 'organization_id WITH =' in exclusion and 'resource_id WITH =' in exclusion and 'catalog_revision' not in exclusion

from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone
import asyncpg

MIGRATION = (Path(__file__).parents[3] / 'src/core/db/migrations/069_booking_allocations_dark.sql').read_text(encoding='utf-8')

@pytest.fixture
async def allocation_data(pg_pool):
    org, other, contact, conversation, booking, booking2 = (uuid4() for _ in range(6))
    async with pg_pool.acquire() as c:
        await c.execute("INSERT INTO organizations(id,name) VALUES($1,'Synthetic'),($2,'Other')", org, other)
        await c.execute("INSERT INTO contacts(id,organization_id,phone_number) VALUES($1,$2,$3)", contact, org, str(uuid4()))
        await c.execute("INSERT INTO conversations(id,organization_id,contact_id) VALUES($1,$2,$3)", conversation, org, contact)
        for tenant in (org, other):
            await c.execute("INSERT INTO booking_settings(organization_id) VALUES($1)", tenant)
            for rev in (1,2):
                await c.execute("INSERT INTO booking_catalog_publications(organization_id,revision,catalog) VALUES($1,$2,'{}')", tenant, rev)
        for bid in (booking, booking2):
            await c.execute("INSERT INTO bookings(id,organization_id,contact_id,id_conversazione,nome_cliente,telefono,data,ora,coperti,stato) VALUES($1,$2,$3,$4,'Synthetic','','2026-10-28','10:00',1,'in_attesa')", bid, org, contact, str(conversation))
    return pg_pool, org, other, contact, conversation, booking, booking2

async def put_set(c, org, booking, *, revision=1, start='2026-10-28 10:00+00', end='2026-10-28 11:00+00', kind='minutes', arrival=None, departure=None):
    await c.execute("""INSERT INTO booking_allocation_sets(organization_id,booking_id,allocation_revision,state,catalog_revision,service_id,timezone,temporal_kind,starts_at,ends_at,arrival_date,departure_date,booking_fingerprint,booking_calendar_revision)
        VALUES($1,$2,1,'active',$3,'service','Europe/Rome',$4,$5::text::timestamptz,$6::text::timestamptz,$7::text::date,$8::text::date,$9,0)""", org,booking,revision,kind,start,end,arrival,departure,'a'*64)

async def put_claim(c, org, booking, *, revision=1, start='2026-10-28 10:00+00', end='2026-10-28 11:00+00'):
    await c.execute("""INSERT INTO booking_resource_allocations(organization_id,booking_id,allocation_revision,catalog_revision,resource_id,resource_kind,exclusive,quantity,starts_at,ends_at)
        VALUES($1,$2,1,$3,'operator','operator',true,1,$4::text::timestamptz,$5::text::timestamptz)""",org,booking,revision,start,end)

async def put_receipt(c, org, booking):
    await c.execute("""INSERT INTO booking_allocation_requests(organization_id,request_id,booking_id,actor_user_id,action,payload_hash,outcome,applied_allocation_revision,catalog_revision)
        VALUES($1,$2,$3,$4,'allocate',$5,'allocated',1,1)""",org,uuid4(),booking,uuid4(),'b'*64)

@pytest.mark.parametrize('changes', [
    {'start':'infinity'}, {'start':'2026-10-28 10:00:01+00'}, {'end':'2026-10-28 10:00+00'},
    {'kind':'minutes','arrival':'2026-10-28','departure':'2026-10-29'},
    {'kind':'civil_nights'}, {'kind':'civil_nights','arrival':'2026-10-29','departure':'2026-10-28'},
    {'kind':'civil_nights','arrival':'infinity','departure':'infinity'},
])
async def test_invalid_interval_and_civil_constraints(allocation_data, changes):
    pool,org,_,_,_,booking,_=allocation_data
    async with pool.acquire() as c:
        with pytest.raises(asyncpg.PostgresError):
            async with c.transaction(): await put_set(c,org,booking,**changes)

async def test_composite_booking_fk_cross_tenant(allocation_data):
    pool,org,other,_,_,booking,_=allocation_data
    async with pool.acquire() as c:
        with pytest.raises(asyncpg.PostgresError):
            async with c.transaction(): await put_set(c,other,booking)

async def test_exclusive_adjacent_and_collision_across_catalog_revisions(allocation_data):
    pool,org,_,_,_,booking,booking2=allocation_data
    async with pool.acquire() as c:
        await put_set(c,org,booking); await put_claim(c,org,booking)
        await put_set(c,org,booking2,revision=2,start='2026-10-28 11:00+00',end='2026-10-28 12:00+00')
        await put_claim(c,org,booking2,revision=2,start='2026-10-28 11:00+00',end='2026-10-28 12:00+00')
        with pytest.raises(asyncpg.PostgresError):
            async with c.transaction():
                await c.execute("DELETE FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2",org,booking2)
                await c.execute("UPDATE booking_allocation_sets SET starts_at='2026-10-28 10:30+00' WHERE organization_id=$1 AND booking_id=$2",org,booking2)
                await put_claim(c,org,booking2,revision=2,start='2026-10-28 10:30+00',end='2026-10-28 12:00+00')

async def test_receipt_immutable_and_structural_booking_cascade(allocation_data):
    pool,org,_,_,_,booking,_=allocation_data
    async with pool.acquire() as c:
        await put_set(c,org,booking); await put_claim(c,org,booking); await put_receipt(c,org,booking)
        for sql in ('UPDATE booking_allocation_requests SET payload_hash=payload_hash', 'DELETE FROM booking_allocation_requests', 'TRUNCATE booking_allocation_requests'):
            with pytest.raises(asyncpg.PostgresError):
                async with c.transaction(): await c.execute(sql)
        await c.execute('DELETE FROM bookings WHERE organization_id=$1 AND id=$2',org,booking)
        for table in ('booking_allocation_sets','booking_resource_allocations','booking_allocation_requests'):
            assert await c.fetchval(f'SELECT count(*) FROM {table} WHERE organization_id=$1 AND booking_id=$2',org,booking)==0

async def test_migration_reapply_and_inherited_noinherit_client_privilege(allocation_data):
    pool,_,_,_,_,_,_=allocation_data
    async with pool.acquire() as c:
        await c.execute(MIGRATION)
        role='allocation_test_'+uuid4().hex
        try:
            await c.execute(f'CREATE ROLE {role} NOINHERIT; GRANT {role} TO authenticated WITH INHERIT FALSE, SET TRUE; GRANT SELECT(resource_id) ON booking_resource_allocations TO {role}')
            with pytest.raises(asyncpg.PostgresError):
                async with c.transaction(): await c.execute(MIGRATION)
        finally:
            await c.execute(f'REVOKE {role} FROM authenticated; DROP OWNED BY {role}; DROP ROLE {role}')

async def barrier(c, org, subject=None):
    operation=uuid4(); scope='contact' if subject else 'organization'
    await c.execute("INSERT INTO gdpr_operations(id,organization_id,requester,request_id,request_hash,kind,scope,status,subject_id) VALUES($1,$2,'synthetic',$3,$4,'erase',$5,'running',$6)",operation,org,uuid4(),'a'*64,scope,subject)
    await c.execute("INSERT INTO gdpr_erasure_barriers(organization_id,operation_id,scope,subject_id,identifier_digest,key_id) VALUES($1,$2,$3,$4,$5,$6)",org,operation,scope,subject,'c'*64 if subject else None,'test' if subject else None)

@pytest.mark.parametrize('scenario', ['org_barrier','direct_barrier','conversation_barrier','deleted_conversation','deleted_contact','malformed','cross_lineage','subjectless_barrier'])
async def test_gdpr_lineage_and_barriers_fail_closed(allocation_data,scenario):
    pool,org,other,contact,conversation,booking,_=allocation_data
    async with pool.acquire() as c:
        if scenario=='org_barrier': await barrier(c,org)
        elif scenario=='direct_barrier':
            await c.execute('UPDATE bookings SET id_conversazione=NULL WHERE id=$1',booking); await barrier(c,org,contact)
        elif scenario=='conversation_barrier':
            await c.execute('UPDATE bookings SET contact_id=NULL WHERE id=$1',booking); await barrier(c,org,contact)
        elif scenario=='deleted_conversation': await c.execute('UPDATE conversations SET deleted_at=now() WHERE id=$1',conversation)
        elif scenario=='deleted_contact': await c.execute('UPDATE contacts SET deleted_at=now() WHERE id=$1',contact)
        elif scenario=='malformed': await c.execute("UPDATE bookings SET id_conversazione='not-a-uuid' WHERE id=$1",booking)
        elif scenario=='cross_lineage':
            other_contact=uuid4(); await c.execute("INSERT INTO contacts(id,organization_id,phone_number) VALUES($1,$2,$3)",other_contact,other,str(uuid4()))
            await c.execute('UPDATE bookings SET contact_id=$2 WHERE id=$1',booking,other_contact)
        else:
            await c.execute('UPDATE bookings SET contact_id=NULL,id_conversazione=NULL WHERE id=$1',booking); await barrier(c,org,contact)
        with pytest.raises(asyncpg.PostgresError):
            async with c.transaction(): await put_set(c,org,booking)

async def test_backend_inherited_forbidden_privileges_abort_reapply(allocation_data):
    pool,_,_,_,_,_,_=allocation_data
    async with pool.acquire() as c:
        role='allocation_backend_'+uuid4().hex
        try:
            await c.execute(f'CREATE ROLE {role}; GRANT {role} TO service_role WITH INHERIT FALSE, SET TRUE; GRANT TRUNCATE ON booking_allocation_sets TO {role}')
            with pytest.raises(asyncpg.PostgresError):
                async with c.transaction(): await c.execute(MIGRATION)
        finally:
            await c.execute(f'REVOKE {role} FROM service_role; DROP OWNED BY {role}; DROP ROLE {role}')

async def test_no_operational_runtime_import():
    root=Path(__file__).parents[3] / 'src'
    matches=[]
    for path in root.rglob('*.py'):
        # Only the disconnected engine may consume these contracts. Neither
        # component may be imported by routes, workers, facades or old writers.
        if path.relative_to(root).as_posix() in {
            'core/bookings/allocation_contracts.py',
            'core/db/repositories/booking_allocation_repo.py',
        }: continue
        text = path.read_text(encoding='utf-8')
        if any(name in text for name in ('allocation_contracts', 'booking_allocation_repo',
                                         'BookingAllocationRepository')):
            matches.append(path)
    assert not matches


@pytest.mark.parametrize('role,table,privilege,column', [
    ('authenticated','booking_resource_allocations','SELECT','resource_id'),
    ('anon','booking_allocation_sets','INSERT','service_id'),
    ('service_role','booking_allocation_sets','TRUNCATE',None),
    ('service_role','booking_allocation_requests','UPDATE','payload_hash'),
])
async def test_set_role_noninherited_grants_reject_atomically(allocation_data, role, table, privilege, column):
    pool,_,_,_,_,_,_=allocation_data
    helper='allocation_edge_'+uuid4().hex
    async with pool.acquire() as c:
        try:
            await c.execute(f'CREATE ROLE {helper}; GRANT {helper} TO {role} WITH INHERIT FALSE, SET TRUE')
            await c.execute(f'GRANT {privilege}{"("+column+")" if column else ""} ON {table} TO {helper}')
            assert await c.fetchval('SELECT pg_has_role($1,$2,\'MEMBER\')', role, helper)
            query='SELECT has_column_privilege($1,$2,$3,$4)' if column else 'SELECT has_table_privilege($1,$2,$3)'
            args=(role,table,column,privilege) if column else (role,table,privilege)
            assert not await c.fetchval(query,*args)
            async with c.transaction():
                with pytest.raises(asyncpg.PostgresError):
                    async with c.transaction(): await c.execute(MIGRATION)
                assert await c.fetchval(query,helper,table,column,privilege) if column else await c.fetchval(query,helper,table,privilege)
        finally:
            await c.execute(f'REVOKE {helper} FROM {role}; DROP OWNED BY {helper}; DROP ROLE {helper}')

async def test_client_reachable_table_owner_rejects_migration(allocation_data):
    pool,_,_,_,_,_,_=allocation_data
    helper='allocation_owner_'+uuid4().hex
    async with pool.acquire() as c:
        await c.execute(f'CREATE ROLE {helper}')
        try:
            async with c.transaction():
                await c.execute(f'GRANT {helper} TO authenticated WITH INHERIT FALSE, SET TRUE; ALTER TABLE booking_allocation_sets OWNER TO {helper}')
                with pytest.raises(asyncpg.PostgresError):
                    async with c.transaction(): await c.execute(MIGRATION)
                await c.execute('ALTER TABLE booking_allocation_sets OWNER TO postgres')
                await c.execute(f'REVOKE {helper} FROM authenticated')
        finally:
            await c.execute(f'DROP OWNED BY {helper}; DROP ROLE {helper}')

async def test_regrant_is_capped_and_old_table_acl_unchanged(allocation_data):
    pool,_,_,_,_,_,_=allocation_data
    async with pool.acquire() as c:
        before=await c.fetchval("SELECT relacl::text FROM pg_class WHERE oid='bookings'::regclass")
        await c.execute('GRANT ALL ON booking_allocation_sets,booking_resource_allocations,booking_allocation_requests TO service_role')
        await c.execute(MIGRATION)
        assert await c.fetchval("SELECT relacl::text FROM pg_class WHERE oid='bookings'::regclass") == before
        for table in ('booking_allocation_sets','booking_resource_allocations','booking_allocation_requests'):
            for privilege in ('SELECT','INSERT'):
                assert await c.fetchval('SELECT has_table_privilege($1,$2,$3)','service_role',table,privilege)
            for privilege in ('TRUNCATE','REFERENCES','TRIGGER'):
                assert not await c.fetchval('SELECT has_table_privilege($1,$2,$3)','service_role',table,privilege)

async def test_backend_can_write_scoped_allocation(allocation_data):
    pool,org,_,_,_,booking,_=allocation_data
    async with pool.acquire() as c:
        tx = c.transaction()
        await tx.start()
        try:
            # Recreate existing Supabase backend access, not new migration grants.
            await c.execute('ALTER ROLE service_role BYPASSRLS; GRANT SELECT ON bookings,contacts,conversations,gdpr_erasure_barriers TO service_role')
            await c.execute('SET LOCAL ROLE service_role')
            await put_set(c,org,booking)
            await put_claim(c,org,booking)
            await put_receipt(c,org,booking)
        finally:
            await tx.rollback()

@pytest.mark.parametrize('action,outcome,revision', [('allocate','replaced',1),('cancel','cancelled',None),('replace','unknown',1),('allocate','allocated',0)])
async def test_receipt_outcome_revision_invariants(allocation_data,action,outcome,revision):
    pool,org,_,_,_,booking,_=allocation_data
    async with pool.acquire() as c:
        with pytest.raises(asyncpg.CheckViolationError):
            async with c.transaction():
                await c.execute("""INSERT INTO booking_allocation_requests(organization_id,request_id,booking_id,actor_user_id,action,payload_hash,outcome,applied_allocation_revision,catalog_revision)
                    VALUES($1,$2,$3,$4,$5,$6,$7,$8,1)""",org,uuid4(),booking,uuid4(),action,'b'*64,outcome,revision)

async def test_claim_mismatch_fails_deferred_commit(allocation_data):
    pool,org,_,_,_,booking,_=allocation_data
    async with pool.acquire() as c:
        await put_set(c,org,booking)
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            async with c.transaction():
                await c.execute("""INSERT INTO booking_resource_allocations(organization_id,booking_id,allocation_revision,catalog_revision,resource_id,resource_kind,exclusive,quantity,starts_at,ends_at)
                    VALUES($1,$2,2,1,'operator','operator',true,1,'2026-10-28 10:00+00','2026-10-28 11:00+00')""",org,booking)

@pytest.mark.parametrize('via_helper', [False, True])
async def test_backend_owner_path_rejected_without_partial_changes(allocation_data, via_helper):
    pool,*_=allocation_data
    async with pool.acquire() as c:
        tx=c.transaction(); await tx.start()
        try:
            owner='service_role'
            if via_helper:
                owner='allocation_owner_'+uuid4().hex
                await c.execute(f'CREATE ROLE {owner}; GRANT {owner} TO service_role WITH INHERIT FALSE, SET TRUE')
            await c.execute(f'ALTER TABLE booking_allocation_sets OWNER TO {owner}')
            before=await c.fetchrow("SELECT relowner,relacl::text FROM pg_class WHERE oid='booking_allocation_sets'::regclass")
            with pytest.raises(asyncpg.PostgresError):
                async with c.transaction(): await c.execute(MIGRATION)
            assert await c.fetchrow("SELECT relowner,relacl::text FROM pg_class WHERE oid='booking_allocation_sets'::regclass")==before
        finally: await tx.rollback()

async def test_inaccessible_membership_does_not_invent_set_authority(allocation_data):
    pool,*_=allocation_data
    async with pool.acquire() as c:
        tx=c.transaction(); await tx.start()
        try:
            helper='allocation_inaccessible_'+uuid4().hex
            await c.execute(f'CREATE ROLE {helper}; GRANT {helper} TO service_role WITH INHERIT FALSE, SET FALSE; GRANT TRUNCATE ON booking_allocation_sets TO {helper}')
            assert await c.fetchval("SELECT pg_has_role('service_role',$1,'MEMBER')",helper)
            assert not await c.fetchval("SELECT pg_has_role('service_role',$1,'SET')",helper)
            assert not await c.fetchval("SELECT has_table_privilege('service_role','booking_allocation_sets','TRUNCATE')")
            await c.execute(MIGRATION)
        finally: await tx.rollback()

async def test_first_install_late_failure_rolls_back_schema_and_acl(allocation_data):
    pool,*_=allocation_data
    async with pool.acquire() as c:
        tx=c.transaction(); await tx.start()
        try:
            await c.execute('DROP TABLE booking_resource_allocations,booking_allocation_requests,booking_allocation_sets; DROP FUNCTION booking_allocation_guard(); DROP FUNCTION booking_allocation_immutable(); ALTER TABLE bookings DROP CONSTRAINT bookings_org_id_unique')
            before=await c.fetchval("SELECT relacl::text FROM pg_class WHERE oid='bookings'::regclass")
            helper='allocation_defaults_'+uuid4().hex
            await c.execute(f'CREATE ROLE {helper}; GRANT {helper} TO service_role WITH INHERIT FALSE, SET TRUE; ALTER DEFAULT PRIVILEGES GRANT TRUNCATE ON TABLES TO {helper}')
            with pytest.raises(asyncpg.PostgresError, match='backend.*privilege'):
                async with c.transaction(): await c.execute(MIGRATION)
            for table in ('booking_allocation_sets','booking_resource_allocations','booking_allocation_requests'):
                assert await c.fetchval('SELECT to_regclass($1)',table) is None
            assert await c.fetchval("SELECT to_regprocedure('booking_allocation_guard()')") is None
            assert not await c.fetchval("SELECT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='bookings'::regclass AND conname='bookings_org_id_unique')")
            assert await c.fetchval("SELECT relacl::text FROM pg_class WHERE oid='bookings'::regclass")==before
        finally: await tx.rollback()

@pytest.mark.parametrize('client,member_role', [('anon',None),('authenticated','staff'),('authenticated','manager'),('authenticated','owner')])
@pytest.mark.parametrize('operation', ['SELECT * FROM booking_allocation_sets','INSERT INTO booking_allocation_sets DEFAULT VALUES','TRUNCATE booking_allocation_sets'])
async def test_actual_client_operations_denied_despite_role_claims(allocation_data,client,member_role,operation):
    pool,*_=allocation_data
    async with pool.acquire() as c:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with c.transaction():
                # Supabase clients can resolve public; table access must still fail.
                await c.execute('GRANT USAGE ON SCHEMA public TO anon,authenticated')
                await c.execute("SELECT set_config('request.jwt.claims',$1,true)", '{"role":"authenticated","user_metadata":{"ruolo":"'+str(member_role)+'"}}')
                await c.execute(f'SET LOCAL ROLE {client}')
                await c.execute(operation)

async def test_public_column_regrant_is_removed(allocation_data):
    pool,*_=allocation_data
    async with pool.acquire() as c:
        await c.execute('GRANT SELECT(resource_id) ON booking_resource_allocations TO PUBLIC')
        assert await c.fetchval("SELECT has_column_privilege('authenticated','booking_resource_allocations','resource_id','SELECT')")
        await c.execute(MIGRATION)
        assert not await c.fetchval("SELECT has_column_privilege('authenticated','booking_resource_allocations','resource_id','SELECT')")

async def test_backend_cannot_regrant_client_access(allocation_data):
    pool,*_=allocation_data
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute('SET LOCAL ROLE service_role')
            # PostgreSQL may warn instead of error when no grant option exists.
            await c.execute('GRANT SELECT ON booking_allocation_sets TO authenticated')
        assert not await c.fetchval("SELECT has_table_privilege('authenticated','booking_allocation_sets','SELECT')")

@pytest.mark.parametrize('actor,privilege',[('authenticated','SELECT'),('service_role','TRUNCATE')])
@pytest.mark.parametrize('via_middle',[False,True])
async def test_admin_membership_regrant_path_cannot_hide_privileges(allocation_data,actor,privilege,via_middle):
    pool,*_=allocation_data
    async with pool.acquire() as c:
        tx=c.transaction(); await tx.start()
        try:
            helper='allocation_admin_'+uuid4().hex
            middle='allocation_middle_'+uuid4().hex if via_middle else actor
            await c.execute(f'CREATE ROLE {helper}')
            if via_middle:
                await c.execute(f'CREATE ROLE {middle}; GRANT {middle} TO {actor} WITH INHERIT FALSE, SET TRUE')
            await c.execute(f'GRANT {helper} TO {middle} WITH ADMIN TRUE, INHERIT FALSE, SET FALSE; GRANT {privilege} ON booking_allocation_sets TO {helper}')
            assert not await c.fetchval("SELECT pg_has_role($1,$2,'SET')",actor,helper)
            probe=c.transaction(); await probe.start()
            try:
                await c.execute(f'SET LOCAL ROLE {middle}')
                await c.execute(f'GRANT {helper} TO {middle} WITH SET TRUE')
                await c.execute(f'SET LOCAL ROLE {helper}')
                assert await c.fetchval('SELECT current_user')==helper
            finally: await probe.rollback()
            assert not await c.fetchval("SELECT pg_has_role($1,$2,'SET')",actor,helper)
            with pytest.raises(asyncpg.PostgresError,match='privilege requires review'):
                async with c.transaction(): await c.execute(MIGRATION)
        finally: await tx.rollback()
