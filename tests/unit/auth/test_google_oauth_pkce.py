"""Real SDK OAuth instances must share the server-only PKCE verifier."""
from types import SimpleNamespace
import time
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import Request
from google_auth_oauthlib.flow import Flow

from src.core.calendar import routes as calendar
from src.core.reviews import google_routes as reviews
from src.core.auth.oauth_state import oauth_pkce_verifier
import base64
import hashlib
import json
from oauthlib.oauth2.rfc6749.parameters import parse_token_response

ORG = '11111111-1111-1111-1111-111111111111'
NONCE = 'a' * 32 + '.' + 'b' * 64


@pytest.mark.parametrize('module,channel', [(calendar,'calendar'),(reviews,'reviews_google')])
@pytest.mark.parametrize('scope_variant',['exact','superset','missing','relaxed_missing','relaxed_superset','omitted'])
async def test_separate_sdk_instances_preserve_pkce(monkeypatch,module,channel,scope_variant):
    monkeypatch.setenv('ENCRYPTION_KEY','A'*43+'=')
    if scope_variant.startswith('relaxed_'):
        monkeypatch.setenv('OAUTHLIB_RELAX_TOKEN_SCOPE', '1')
    else:
        monkeypatch.delenv('OAUTHLIB_RELAX_TOKEN_SCOPE', raising=False)
    flow_instances = []
    def make_flow():
        flow = Flow.from_client_config({'web':{
            'client_id':'synthetic','client_secret':'synthetic',
            'auth_uri':'https://accounts.google.test/authorize',
            'token_uri':'https://google.test/token',
        }},scopes=module.SCOPES,redirect_uri='https://app.test/callback')
        flow_instances.append(flow)
        return flow
    monkeypatch.setattr(module,'_make_flow',make_flow)
    monkeypatch.setattr(module,'create_bound_oauth_nonce',lambda *args:NONCE)
    monkeypatch.setattr(module,'validate_oauth_callback_context',AsyncMock(return_value={'verified':True}))
    if module is calendar:
        monkeypatch.setattr(module,'google_calendar_enabled',lambda:True)
    else:
        monkeypatch.setattr(module,'google_business_enabled',lambda:True)
        monkeypatch.setattr(module,'check_feature_blocked_by_plan',AsyncMock(return_value=None))
    class Pool:
        def __init__(self): self.credential_writes = []; self.nonces = {}
        def acquire(self): return self
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def execute(self,query,*args):
            if 'INSERT INTO google_' in query: self.credential_writes.append(args)
        async def fetchrow(self,*args): return {'created_at':None}
    pool = Pool()
    service = SimpleNamespace(encrypt_secret=lambda value:'encrypted')
    if module is calendar:
        class CalendarConnection:
            def __init__(self): self.generations = {}
            async def begin_oauth(self, org, nonce):
                generation = uuid4()
                self.generations[org] = generation
                pool.nonces[(org, nonce)] = {
                    'created_at': datetime.now(timezone.utc),
                    'calendar_generation': generation,
                }
            async def consume_nonce(self, org, nonce):
                row = pool.nonces.get((org, nonce))
                if not row or row['calendar_generation'] != self.generations.get(org):
                    return None
                return pool.nonces.pop((org, nonce))
            async def save_oauth(self, org, generation, creds):
                if generation != self.generations.get(org): return False
                pool.credential_writes.append((org, 'encrypted-access', 'encrypted-refresh', creds.expiry))
                return True
        service.connection = CalendarConnection()
    app=SimpleNamespace(state=SimpleNamespace(pool=pool,repo=object(),calendar_service=service,reviews_service=service))
    request=Request({'type':'http','app':app,'headers':[], 'query_string':b''})
    user={'organization_id':ORG}
    start = module.calendar_auth if module is calendar else module.google_reviews_auth
    response=await start(request,user=user,mfa={})
    challenge=parse_qs(urlsplit(response.headers['location']).query)['code_challenge'][0]
    token_kwargs={}
    def exchange(self,token_url,**kwargs):
        token_kwargs.update(kwargs)
        granted = module.SCOPES if scope_variant=='exact' else module.SCOPES+['openid']
        if scope_variant in ('missing', 'relaxed_missing'): granted=['openid']
        token={'access_token':'synthetic-access','refresh_token':'synthetic-refresh',
               'token_type':'Bearer','expires_at':time.time()+3600,'scope':' '.join(granted)}
        if scope_variant == 'omitted': token.pop('scope')
        self.token=parse_token_response(json.dumps(token),scope=self.scope)
        return self.token
    monkeypatch.setattr('requests_oauthlib.OAuth2Session.fetch_token',exchange)
    callback_request=Request({'type':'http','app':app,'headers':[], 'query_string':('state='+ORG+':'+NONCE+'&code=synthetic-code').encode()})
    callback=module.calendar_oauth2callback if module is calendar else module.google_reviews_oauth2callback
    result=await callback(callback_request)
    assert token_kwargs.get('code_verifier') == flow_instances[0].code_verifier
    assert token_kwargs.get('code_verifier') is not None
    assert token_kwargs['code_verifier'] not in response.headers['location']
    assert challenge
    expected_challenge=base64.urlsafe_b64encode(hashlib.sha256(token_kwargs['code_verifier'].encode()).digest()).rstrip(b'=').decode()
    assert challenge == expected_challenge
    if scope_variant in ('missing', 'relaxed_missing'):
        assert 'error' in result.headers['location']
        assert app.state.pool.credential_writes == []
    else:
        assert 'connected' in result.headers['location']
        assert len(app.state.pool.credential_writes) == 1


def test_pkce_is_server_keyed_and_bound_to_channel_org_and_nonce(monkeypatch):
    monkeypatch.setenv('ENCRYPTION_KEY','A'*43+'=')
    verifier=oauth_pkce_verifier('calendar',ORG,NONCE)
    assert len(verifier)==43
    assert verifier==oauth_pkce_verifier('calendar',ORG,NONCE)
    assert verifier!=oauth_pkce_verifier('reviews_google',ORG,NONCE)
    assert verifier!=oauth_pkce_verifier('calendar','22222222-2222-2222-2222-222222222222',NONCE)
    assert verifier!=oauth_pkce_verifier('calendar',ORG,'c'*32+'.'+'d'*64)
    monkeypatch.setenv('ENCRYPTION_KEY',base64.urlsafe_b64encode(b'X'*32).decode())
    assert verifier!=oauth_pkce_verifier('calendar',ORG,NONCE)
