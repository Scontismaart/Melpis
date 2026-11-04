import uuid
from unittest.mock import AsyncMock, MagicMock
import pytest

from src.whatsapp.models import MessageEntry
from src.instagram.models import IgMessagingEvent

class Context:
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False
    def transaction(self): return self
class Pool:
    def acquire(self): return Context()

@pytest.mark.asyncio
@pytest.mark.parametrize('clinical,expected', [('dolore e referto segreto', 'Richiesta riservata: intervento umano'), ('STOP referto segreto', 'stop')])
async def test_whatsapp_actual_ingestion_minimizes_and_preserves_stop(monkeypatch, clinical, expected):
    from src.whatsapp import router
    repo = MagicMock(pool=Pool())
    repo.get_org_business_profile = AsyncMock(return_value={'verticale': 'studio_medico'})
    repo.get_or_create_contact = AsyncMock(return_value={'id': uuid.uuid4()})
    repo.get_or_create_conversation = AsyncMock(return_value={'id': uuid.uuid4()})
    repo.upsert_message = AsyncMock()
    monkeypatch.setattr(router, 'dedup_check', AsyncMock(return_value=True))
    msg = MessageEntry.model_validate({'id':'mid', 'from':'123', 'type':'text', 'text':{'body':clinical}, 'context':{'id':'referto segreto'}})
    await router._handle_inbound_message(repo, uuid.uuid4(), msg, [])
    saved = repo.upsert_message.await_args.kwargs
    assert saved['content_text'] == expected
    assert clinical not in str(saved['content'])

@pytest.mark.asyncio
async def test_instagram_actual_ingestion_minimizes(monkeypatch):
    from src.instagram import router
    repo = MagicMock(pool=Pool())
    repo.get_org_business_profile = AsyncMock(return_value={'verticale': 'studio_medico'})
    repo.get_or_create_contact = AsyncMock(return_value={'id': uuid.uuid4()})
    repo.get_or_create_conversation = AsyncMock(return_value={'id': uuid.uuid4()})
    repo.upsert_message = AsyncMock()
    org = uuid.uuid4()
    igrepo = MagicMock(get_org_by_instagram_user_id=AsyncMock(return_value={'organization_id': org}))
    monkeypatch.setattr(router, 'dedup_check', AsyncMock(return_value=True))
    event = IgMessagingEvent.model_validate({'sender':{'id':'123'}, 'recipient':{'id':'456'},
        'message':{'mid':'mid', 'text':'referto segreto', 'attachments':[{'secret':'referto'}]}})
    await router._handle_dm(repo, igrepo, 'entry', event)
    saved = repo.upsert_message.await_args.kwargs
    assert saved['content_text'] == 'Richiesta riservata: intervento umano'
    assert 'referto' not in str(saved['content'])

@pytest.mark.parametrize('channel', ['whatsapp', 'instagram'])
def test_webhook_minimizes_before_inbox_enqueue(monkeypatch, channel):
    import json
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.whatsapp import router as wa
    from src.instagram import router as ig
    from types import SimpleNamespace
    org = uuid.uuid4()
    repo = MagicMock(pool=Pool())
    repo.get_org_business_profile = AsyncMock(return_value={'verticale': 'studio_medico'})
    repo.get_org_by_phone_number_id = AsyncMock(return_value={'organization_id': org})
    igrepo = MagicMock(get_org_by_instagram_user_id=AsyncMock(return_value={'organization_id': org}))
    if channel == 'whatsapp':
        raw = {'object':'whatsapp_business_account','entry':[{'id':'entry','changes':[{'field':'messages','value':{'metadata':{'phone_number_id':'1', 'display_phone_number':'referto segreto'},'contacts':[{'profile':{'name':'sensitive name'}}], 'messages':[{'id':'m','from':'123','type':'text','text':{'body':'STOP referto segreto'}, 'context':{'id':'referto segreto', 'from':'referto segreto'}}], 'statuses':[{'id':'status1','status':'failed','timestamp':'1','errors':[{'title':'referto segreto'}], 'conversation':{'secret':'referto segreto'}}]}}]}]}
        mod = wa
    else:
        raw = {'object':'instagram','entry':[{'id':'entry','messaging':[{'sender':{'id':'123'},'recipient':{'id':'456'},'message':{'mid':'m','text':'referto segreto','attachments':[{'payload':'secret'}]}, 'unexpected':{'secret':'referto segreto'}}]}]}
        mod = ig
    security = MagicMock(app_secret='', verify_token='')
    security.authenticate_and_read = AsyncMock(return_value=json.dumps(raw).encode())
    captured = AsyncMock()
    monkeypatch.setattr(mod, 'WebhookInboxRepository', lambda pool: SimpleNamespace(enqueue=captured))
    app = FastAPI()
    config = SimpleNamespace(app_secret='', verify_token='')
    app.include_router(wa.create_router(config, repo, security) if channel == 'whatsapp' else ig.create_router(config, repo, igrepo, security))
    response = TestClient(app).post('/webhooks/' + channel, json=raw)
    assert response.status_code == 200
    payload = captured.await_args.args[2]
    assert 'referto' not in str(payload)
    assert 'secret' not in str(payload)
    if channel == 'whatsapp':
        assert payload['entry'][0]['changes'][0]['value']['messages'][0]['text']['body'] == 'stop'

@pytest.mark.asyncio
async def test_classification_errors_quarantine_and_missing_schema_stays_unknown():
    from src.core.bookings.privacy_boundary import inbound_classification_status
    repo = MagicMock(pool=Pool())
    repo.get_org_business_profile = AsyncMock(side_effect=RuntimeError('unavailable'))
    assert await inbound_classification_status(repo, uuid.uuid4()) == 'error'
    repo.get_org_business_profile = AsyncMock(return_value={})
    conn = MagicMock(fetchval=AsyncMock(return_value=None))
    class Acquire:
        async def __aenter__(self): return conn
        async def __aexit__(self, *args): return False
    repo.pool = MagicMock(acquire=MagicMock(return_value=Acquire()))
    assert await inbound_classification_status(repo, uuid.uuid4()) == 'unknown'

@pytest.mark.asyncio
async def test_ingestion_database_error_persists_only_quarantine(monkeypatch):
    from src.whatsapp import router
    from src.core.bookings.privacy_boundary import QUARANTINE_TEXT
    repo = MagicMock(pool=Pool())
    repo.get_org_business_profile = AsyncMock(side_effect=RuntimeError('database unavailable'))
    repo.get_or_create_contact = AsyncMock(return_value={'id': uuid.uuid4()})
    repo.get_or_create_conversation = AsyncMock(return_value={'id': uuid.uuid4()})
    repo.upsert_message = AsyncMock()
    monkeypatch.setattr(router, 'dedup_check', AsyncMock(return_value=True))
    msg = MessageEntry.model_validate({'id':'mid', 'from':'123', 'type':'text', 'text':{'body':'referto segreto'}})
    await router._handle_inbound_message(repo, uuid.uuid4(), msg, [])
    saved = repo.upsert_message.await_args.kwargs
    assert saved['content_text'] == QUARANTINE_TEXT
    assert 'referto' not in str(saved['content'])

@pytest.mark.asyncio
async def test_unclassified_healthy_old_schema_retains_ordinary_message(monkeypatch):
    from src.whatsapp import router
    class Conn(Context):
        async def fetchval(self, sql): return None
    class OldPool:
        def acquire(self): return Conn()
    repo = MagicMock(pool=OldPool())
    repo.get_org_business_profile = AsyncMock(return_value={})
    repo.get_or_create_contact = AsyncMock(return_value={'id': uuid.uuid4()})
    repo.get_or_create_conversation = AsyncMock(return_value={'id': uuid.uuid4()})
    repo.upsert_message = AsyncMock()
    monkeypatch.setattr(router, 'dedup_check', AsyncMock(return_value=True))
    msg = MessageEntry.model_validate({'id':'mid', 'from':'123', 'type':'text', 'text':{'body':'Che orari fate?'}})
    await router._handle_inbound_message(repo, uuid.uuid4(), msg, [])
    assert repo.upsert_message.await_args.kwargs['content_text'] == 'Che orari fate?'

@pytest.mark.asyncio
async def test_medical_attestation_is_tenant_bound_and_read_only(pg_pool, sample_org, other_org):
    from src.core.bookings.privacy_boundary import inbound_classification_status
    from src.whatsapp.repository import Repository

    org, other = sample_org["id"], other_org["id"]
    await pg_pool.execute("""INSERT INTO booking_privacy_classifications
        (organization_id,sector,revision,evidence_reference)
        VALUES($1,'medical_disabled',1,'synthetic:medical')""", org)
    repo = Repository(pg_pool)
    assert await inbound_classification_status(repo, org) == "restricted"
    assert await inbound_classification_status(repo, other) == "unknown"

    async with pg_pool.acquire() as conn, conn.transaction():
        await conn.execute("SET LOCAL ROLE service_role")
        assert await conn.fetchval(
            "SELECT sector FROM booking_privacy_classification_reader WHERE organization_id=$1", org,
        ) == "medical_disabled"
        assert not await conn.fetchval(
            "SELECT has_table_privilege(current_user,'booking_privacy_classifications','SELECT,INSERT,UPDATE,DELETE')",
        )
