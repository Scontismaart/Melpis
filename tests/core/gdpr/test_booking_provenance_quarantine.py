"""Synthetic regression coverage for exact, tenant-scoped booking quarantine."""
import json
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import pytest
from fastapi import HTTPException
from cryptography.fernet import Fernet

from src.core.bookings.routes import get_booking, list_bookings
from src.core.db.repositories.booking_repo import BookingRepository
from src.core.db.repositories.message_repo import MessageRepository
from src.core.gdpr.exporter import Exporter
from tests.core.gdpr.test_exports_a06 import export_bytes
from src.core.report.csv_export import get_prenotazioni_completate, genera_csv
from tests.core.test_quota_reservation_lifecycle import _auth_member, _organization

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def quarantine_case(pg_pool):
    org = (await _organization(pg_pool))['id']
    other = (await _organization(pg_pool))['id']
    auth = await _auth_member(pg_pool, org, role='owner')
    held, guest, foreign = uuid4(), uuid4(), uuid4()
    for booking, tenant, name in ((held, org, 'Held synthetic'),
                                  (guest, org, 'New guest synthetic'),
                                  (foreign, other, 'Other tenant synthetic')):
        await pg_pool.execute("""INSERT INTO bookings
            (id,organization_id,nome_cliente,telefono,data,ora,coperti,stato)
            VALUES($1,$2,$3,'','2026-11-01','10:00',1,'completata')""",
            booking, tenant, name)
    await pg_pool.execute("""INSERT INTO booking_provenance_quarantine
        (organization_id,booking_id,review_reference,reason_code,reviewed_at,review_due_at)
        VALUES($1,$2,'synthetic-review','historical_provenance_unverified',
               clock_timestamp()-interval '1 minute',clock_timestamp()+interval '30 days')""", org, held)
    return SimpleNamespace(pool=pg_pool, org=org, other=other, auth=auth,
                           held=held, guest=guest, foreign=foreign)


async def test_exact_quarantine_fences_reads_exports_and_writes(quarantine_case):
    case = quarantine_case
    repo = BookingRepository(case.pool)
    assert await repo.get_booking(case.org, case.held) is None
    assert (await repo.get_booking(case.org, case.guest))['id'] == case.guest
    assert await repo.get_booking(case.org, case.foreign) is None
    assert (await repo.get_booking(case.other, case.foreign))['id'] == case.foreign
    visible = {row['id'] for row in await repo.list_bookings(case.org)}
    assert case.guest in visible and case.held not in visible and case.foreign not in visible
    occupancy = await repo.booking_capacity_by_hour(case.org, date(2026, 11, 1), {'cancellata', 'no_show'})
    assert occupancy == {10: 2}

    class Service:
        def __init__(self):
            self.repo = repo

        async def _minimize_booking_rows(self, rows, org):
            return rows

        async def _minimize_existing_booking(self, row, org):
            return row

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(booking_service=Service())))
    user = {'organization_id': case.org}
    api_rows = await list_bookings(request, data=None, user=user)
    assert {row['id'] for row in api_rows} == visible
    with pytest.raises(HTTPException) as error:
        await get_booking(str(case.held), request, user=user)
    assert error.value.status_code == 404
    assert (await get_booking(str(case.guest), request, user=user))['id'] == case.guest

    rows = await get_prenotazioni_completate(case.pool, str(case.org), date(2026, 11, 1), date(2026, 11, 1))
    csv = genera_csv(rows).decode('utf-8-sig')
    assert 'New guest synthetic' in csv
    assert 'Held synthetic' not in csv and 'Other tenant synthetic' not in csv
    for sql in ("UPDATE bookings SET nome_cliente='changed' WHERE organization_id=$1 AND id=$2",
                'DELETE FROM bookings WHERE organization_id=$1 AND id=$2'):
        with pytest.raises(asyncpg.PostgresError):
            await case.pool.execute(sql, case.org, case.held)
    assert await case.pool.fetchval('SELECT nome_cliente FROM bookings WHERE organization_id=$1 AND id=$2',
                                    case.org, case.held) == 'Held synthetic'
    await case.pool.execute("UPDATE bookings SET nome_cliente='Guest updated' WHERE organization_id=$1 AND id=$2",
                            case.org, case.guest)


async def test_authenticated_rls_hides_only_exact_quarantined_row(quarantine_case):
    case = quarantine_case
    async with case.pool.acquire() as conn, conn.transaction():
        await conn.execute("""CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS $$
            SELECT NULLIF(current_setting('request.jwt.claim.sub',true),'')::uuid $$""")
        await conn.execute('GRANT USAGE ON SCHEMA public,auth TO authenticated')
        await conn.execute('GRANT SELECT ON bookings,organization_memberships,user_profiles TO authenticated')
        await conn.execute('SET LOCAL ROLE authenticated')
        await conn.execute("SELECT set_config('request.jwt.claim.sub',$1,true)", str(case.auth))
        ids = {row['id'] for row in await conn.fetch('SELECT id FROM bookings WHERE id=ANY($1::uuid[])',
                                                    [case.held, case.guest, case.foreign])}
        assert ids == {case.guest}



async def test_gdpr_organization_export_excludes_quarantine(quarantine_case, monkeypatch):
    case = quarantine_case
    monkeypatch.setenv('ENCRYPTION_KEY', Fernet.generate_key().decode('ascii'))
    exporter = Exporter(case.pool)
    requester = 'synthetic-export-requester'
    ready = await exporter.prepare(case.org, requester, scope='organization', request_id=uuid4())
    result = json.loads(await export_bytes(exporter, case.org, ready['export_id'], requester))
    ids = {row['id'] for row in result['bookings']}
    assert str(case.guest) in ids
    assert str(case.held) not in ids and str(case.foreign) not in ids
    assert 'Held synthetic' not in json.dumps(result)
    category = next(row for row in result['manifest']['categories'] if row['category'] == 'bookings')
    assert category['status'] == 'partial' and category['quarantined_records'] == 1

    # Quarantining another exact record invalidates every previously prepared
    # snapshot of that tenant, including snapshots made by the current reader.
    await case.pool.execute("""INSERT INTO booking_provenance_quarantine
        (organization_id,booking_id,review_reference,reason_code,reviewed_at,review_due_at)
        VALUES($1,$2,'synthetic-second-review','historical_provenance_unverified',
               clock_timestamp(),clock_timestamp()+interval '30 days')""", case.org, case.guest)
    with pytest.raises(LookupError):
        await exporter.preflight(case.org, ready['export_id'], requester)
    assert await case.pool.fetchval('SELECT count(*) FROM bookings WHERE organization_id=$1', case.org) == 2


async def test_separate_release_restores_visibility_and_preserves_evidence(quarantine_case):
    case = quarantine_case
    repo = BookingRepository(case.pool)
    assert await repo.get_booking(case.org, case.held) is None
    # A release must be a separate, later review for the exact tenant and booking.
    for org, reference in ((case.other, 'release-review'), (case.org, 'synthetic-review')):
        with pytest.raises(asyncpg.PostgresError):
            await case.pool.execute("""INSERT INTO booking_provenance_quarantine_releases
                (organization_id,booking_id,review_reference,reviewed_at)
                VALUES($1,$2,$3,clock_timestamp())""", org, case.held, reference)
    await case.pool.execute("""INSERT INTO booking_provenance_quarantine_releases
        (organization_id,booking_id,review_reference,reviewed_at)
        VALUES($1,$2,'release-review',clock_timestamp())""", case.org, case.held)
    assert (await repo.get_booking(case.org, case.held))['id'] == case.held
    assert await repo.get_booking(case.other, case.held) is None
    await case.pool.execute("UPDATE bookings SET nome_cliente='Released synthetic' WHERE organization_id=$1 AND id=$2",
                            case.org, case.held)
    for table in ('booking_provenance_quarantine', 'booking_provenance_quarantine_releases'):
        assert await case.pool.fetchval(f'SELECT count(*) FROM {table} WHERE organization_id=$1 AND booking_id=$2',
                                        case.org, case.held) == 1
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await case.pool.execute(f'DELETE FROM {table} WHERE organization_id=$1 AND booking_id=$2',
                                    case.org, case.held)


async def test_linked_confirmation_hidden_from_messages_and_export(quarantine_case, monkeypatch):
    case = quarantine_case
    contact, conversation, inbound, outbound, booking = (uuid4() for _ in range(5))
    await case.pool.execute('INSERT INTO contacts(id,organization_id,phone_number) VALUES($1,$2,$3)',
                            contact, case.org, str(contact))
    await case.pool.execute('INSERT INTO conversations(id,organization_id,contact_id) VALUES($1,$2,$3)',
                            conversation, case.org, contact)
    await case.pool.execute("""INSERT INTO messages
        (id,organization_id,conversation_id,direction,message_type,content,content_text,status,handling_type)
        VALUES($1,$3,$4,'inbound','text','{}','Unrelated inbound synthetic','handled','ai_handled'),
              ($2,$3,$4,'outbound','text','{}','Held confirmation synthetic','queued',NULL)""",
        inbound, outbound, case.org, conversation)
    await case.pool.execute("""INSERT INTO bookings
        (id,organization_id,contact_id,id_conversazione,nome_cliente,telefono,data,ora,coperti,stato)
        VALUES($1,$2,$3,$4,'Linked synthetic','','2026-11-02','10:00',1,'in_attesa')""",
        booking, case.org, contact, str(conversation))
    await case.pool.execute("""INSERT INTO booking_confirmation_outbox
        (organization_id,booking_id,inbound_message_id,outbound_message_id) VALUES($1,$2,$3,$4)""",
        case.org, booking, inbound, outbound)
    await case.pool.execute("""INSERT INTO booking_provenance_quarantine
        (organization_id,booking_id,review_reference,reason_code,reviewed_at,review_due_at)
        VALUES($1,$2,'confirmation-review','historical_provenance_unverified',
               clock_timestamp()-interval '1 minute',clock_timestamp()+interval '30 days')""",
        case.org, booking)

    with pytest.raises(asyncpg.PostgresError):
        await case.pool.execute('DELETE FROM booking_confirmation_outbox WHERE organization_id=$1 AND booking_id=$2',
                                case.org, booking)
    assert await case.pool.fetchval('SELECT count(*) FROM booking_confirmation_outbox WHERE organization_id=$1 AND booking_id=$2',
                                    case.org, booking) == 1

    repo = MessageRepository(case.pool)
    assert await repo.get_message_org_scoped(case.org, outbound) is None
    assert (await repo.get_message_org_scoped(case.org, inbound))['id'] == inbound
    async with repo.outbound_quarantine_delivery_guard(case.org, outbound) as allowed:
        assert allowed is False
    async with repo.outbound_quarantine_delivery_guard(case.org, inbound) as allowed:
        assert allowed is True
    async with case.pool.acquire() as conn, conn.transaction():
        await conn.execute("""CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS $$
            SELECT NULLIF(current_setting('request.jwt.claim.sub',true),'')::uuid $$""")
        await conn.execute('GRANT USAGE ON SCHEMA public,auth TO authenticated')
        await conn.execute('GRANT SELECT ON messages,contacts,conversations,organization_memberships,user_profiles TO authenticated')
        await conn.execute('SET LOCAL ROLE authenticated')
        await conn.execute("SELECT set_config('request.jwt.claim.sub',$1,true)", str(case.auth))
        ids = {row['id'] for row in await conn.fetch('SELECT id FROM messages WHERE id=ANY($1::uuid[])',
                                                    [inbound, outbound])}
        assert ids == {inbound}

    monkeypatch.setenv('ENCRYPTION_KEY', Fernet.generate_key().decode('ascii'))
    exporter = Exporter(case.pool)
    requester = 'synthetic-confirmation-export'
    ready = await exporter.prepare(case.org, requester, scope='organization', request_id=uuid4())
    result = json.loads(await export_bytes(exporter, case.org, ready['export_id'], requester))
    ids = {row['id'] for row in result['messages']}
    assert str(inbound) in ids and str(outbound) not in ids
    assert 'Held confirmation synthetic' not in json.dumps(result)
