"""Synthetic medical ingress through the webhook inbox and real worker.

The privacy boundary deliberately turns even administrative source text into a
human-handoff marker. This does not imply a conversation-to-booking bridge.
"""
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.core.workers.webhook_inbox_worker import WebhookInboxWorker
from src.whatsapp.repository import Repository
from src.whatsapp.router import create_router


@pytest.mark.asyncio
async def test_medical_webhook_to_worker_persists_only_handoff_marker(pg_pool, sample_org, monkeypatch):
    org = sample_org['id']
    await pg_pool.execute("""INSERT INTO booking_privacy_classifications
        (organization_id,sector,revision,evidence_reference)
        VALUES($1,'medical_disabled',1,'synthetic:medical-ingress')""", org)
    repo = Repository(pg_pool)
    # Routing lookup represents the configured Meta phone ID; persistence and
    # classification use the actual repository and PostgreSQL.
    monkeypatch.setattr(repo, 'get_org_by_phone_number_id', AsyncMock(return_value={'organization_id': org}))
    monkeypatch.delenv('PRIVATE_BOOKING_WORKER_ENABLED', raising=False)
    marker = 'synthetic clinical note never stored'
    wam_id = 'synthetic-medical-' + uuid4().hex
    payload = {'object': 'whatsapp_business_account', 'entry': [{'id': 'entry', 'changes': [
        {'field': 'messages', 'value': {'messaging_product': 'whatsapp',
         'metadata': {'phone_number_id': '123', 'display_phone_number': '123'},
         'messages': [{'id': wam_id, 'from': '15550001234', 'type': 'text',
                       'text': {'body': 'Administrative appointment: ' + marker}}]}}
    ]}]}
    security = type('Security', (), {'app_secret': '', 'verify_token': '',
        'authenticate_and_read': AsyncMock(return_value=json.dumps(payload).encode())})()
    app = FastAPI()
    app.include_router(create_router(repo=repo, security=security))
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/webhooks/whatsapp', json=payload)
    assert response.status_code == 200
    row = await pg_pool.fetchrow('SELECT payload, status FROM meta_webhook_inbox WHERE organization_id=$1', org)
    assert row['status'] == 'pending'
    assert marker not in str(row['payload'])

    worker = WebhookInboxWorker(repo)
    assert await worker.process_next_batch() >= 1
    row = await pg_pool.fetchrow('SELECT payload, status FROM meta_webhook_inbox WHERE organization_id=$1', org)
    assert row['status'] == 'completed'
    messages = await pg_pool.fetch('SELECT content, content_text FROM messages WHERE organization_id=$1 AND wam_id=$2', org, wam_id)
    assert len(messages) == 1
    assert messages[0]['content_text'] == 'Richiesta riservata: intervento umano'
    assert marker not in str(messages[0]['content'])
    assert await pg_pool.fetchval('SELECT count(*) FROM bookings WHERE organization_id=$1', org) == 0
