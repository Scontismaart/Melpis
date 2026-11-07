"""Transactional, idempotent local stay confirmation; no provider calls."""
import json
from uuid import UUID, uuid4


async def enqueue_hotel_confirmation(conn, booking, source_message, arrival, departure):
    org = booking['organization_id']
    conversation = UUID(str(booking['id_conversazione']))
    phone = booking['telefono']
    if not phone:
        raise ValueError('Hotel confirmation destination unavailable')
    body = f'Soggiorno confermato in Melpis: arrivo {arrival.isoformat()}, partenza {departure.isoformat()}.'
    payload = {'to': phone, 'type': 'text', 'text': {'body': body}, '_delivery_category': 'service'}
    key = f"booking-confirm:{org}:{booking['source_message_id']}"
    row = await conn.fetchrow("""INSERT INTO messages
        (id,organization_id,conversation_id,direction,message_type,content,content_text,status,handling_type,idempotency_key)
        VALUES($1,$2,$3,'outbound','text',$4::jsonb,$5,'queued','booking_confirmation',$6)
        ON CONFLICT (organization_id,idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
        RETURNING id,content""", uuid4(), org, conversation, json.dumps(payload), body, key)
    if row is None:
        row = await conn.fetchrow('SELECT id,content FROM messages WHERE organization_id=$1 AND idempotency_key=$2', org, key)
        stored = row['content'] if row else None
        if isinstance(stored, str):
            stored = json.loads(stored)
        if stored != payload:
            raise ValueError('Hotel confirmation idempotency conflict')
    await conn.execute("""INSERT INTO booking_confirmation_outbox
        (organization_id,booking_id,inbound_message_id,outbound_message_id,booking_revision)
        VALUES($1,$2,$3,$4,$5) ON CONFLICT (organization_id,booking_id,booking_revision) DO NOTHING""",
        org, booking['id'], source_message, row['id'], booking['calendar_revision'])
