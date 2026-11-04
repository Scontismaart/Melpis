"""Synthetic, local PostgreSQL and threads only: never contact Google."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
import threading
from types import SimpleNamespace
from uuid import uuid4

from cryptography.fernet import Fernet
from google.oauth2.credentials import Credentials
import pytest

from src.core.calendar.connection import CalendarConnection, CalendarUnavailable, SCOPES

pytestmark = pytest.mark.asyncio


async def prepare(pool, org, monkeypatch):
    assert not await pool.fetchval("""SELECT EXISTS(
        SELECT 1 FROM pg_attribute
        WHERE attrelid='public.google_calendar_credentials'::regclass
        AND attname='refresh_dispatch_id' AND NOT attisdropped)""")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "synthetic-client")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "synthetic-secret")
    fernet = Fernet(Fernet.generate_key())
    connection = CalendarConnection(pool, fernet)
    await pool.execute("""INSERT INTO google_calendar_credentials
        (organization_id,access_token,refresh_token,token_expiry,connection_status,sync_enabled,granted_scopes)
        VALUES($1,$2,$3,$4,'connected',TRUE,$5::jsonb)""", org,
        fernet.encrypt(b"old-access").decode(), fernet.encrypt(b"old-refresh").decode(),
        datetime.now(timezone.utc)-timedelta(hours=1), json.dumps(SCOPES))
    return connection


async def test_cancelled_thread_cannot_retry_or_restore_disconnected_credentials(pg_pool, sample_org, monkeypatch):
    org=sample_org['id']; connection=await prepare(pg_pool,org,monkeypatch)
    entered=threading.Event(); release=threading.Event(); finished=threading.Event(); calls=[]
    def refresh(creds, request):
        calls.append('refresh'); entered.set()
        assert release.wait(10)
        creds.token='late-access'; creds.expiry=datetime.now(timezone.utc).replace(tzinfo=None)+timedelta(hours=1)
        finished.set()
    monkeypatch.setattr(Credentials,'refresh',refresh)
    task=asyncio.create_task(connection.credentials(org))
    try:
        assert await asyncio.to_thread(entered.wait,5)
        marker=await pg_pool.fetchrow('SELECT connection_status,last_error_code,credential_version FROM google_calendar_credentials WHERE organization_id=$1',org)
        assert marker['connection_status']=='reconnect_required' and marker['last_error_code'] == 'refresh_outcome_unknown' and marker['credential_version'] > 0
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        with pytest.raises(CalendarUnavailable,match='reconnect_required'): await connection.credentials(org)
        await connection.disconnect(org)
        release.set(); assert await asyncio.to_thread(finished.wait,5)
        row=await connection.row(org)
        assert row['connection_status']=='disconnected' and row['access_token'] is None
        assert calls==['refresh']
    finally:
        release.set()
        if not task.done(): task.cancel()


async def test_successful_refresh_is_once_and_commits_rotated_credentials(pg_pool, sample_org, monkeypatch):
    org=sample_org['id']; connection=await prepare(pg_pool,org,monkeypatch); calls=[]
    def refresh(creds, request):
        calls.append('refresh'); creds.token='rotated-access'
        creds._refresh_token='rotated-refresh'
        creds.expiry=datetime.now(timezone.utc).replace(tzinfo=None)+timedelta(hours=1)
    monkeypatch.setattr(Credentials,'refresh',refresh)
    results = await asyncio.gather(connection.credentials(org), connection.credentials(org))
    row=await connection.row(org)
    assert calls==['refresh'] and row['last_error_code'] is None and row['connection_status']=='connected'
    assert connection.fernet.decrypt(row['refresh_token'].encode())==b'rotated-refresh'
    # Google auth's before_request can refresh an expired token even with
    # AuthorizedHttp.max_refresh_attempts=0. The resource client has no
    # refresh secret and cannot bypass the durable coordinator.
    from google.auth.exceptions import RefreshError
    access = results[0][0]
    assert access.refresh_token is None and access.client_secret is None
    access.expiry = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
    def forbidden_request(*_args, **_kwargs):
        pytest.fail("resource request attempted a token exchange")
    with pytest.raises(RefreshError, match='calendar_refresh_requires_coordinator'):
        access.before_request(forbidden_request, 'GET', 'https://example.test/calendar', {})
    assert calls == ['refresh']


async def test_timeout_is_unknown_and_never_retried(pg_pool,sample_org,monkeypatch):
    org=sample_org['id']; connection=await prepare(pg_pool,org,monkeypatch); calls=[]
    def refresh(creds, request):
        calls.append('refresh'); raise TimeoutError('synthetic')
    monkeypatch.setattr(Credentials,'refresh',refresh)
    with pytest.raises(CalendarUnavailable,match='refresh_unavailable'): await connection.credentials(org)
    with pytest.raises(CalendarUnavailable,match='reconnect_required'): await connection.credentials(org)
    row=await connection.row(org)
    assert row['last_error_code']=='refresh_outcome_unknown' and row['connection_status']=='reconnect_required' and calls==['refresh']


async def test_actual_google_sdk_cannot_retry_uncertain_token_post(pg_pool, sample_org, monkeypatch):
    from src.core.calendar import connection as module
    org = sample_org['id']
    connection = await prepare(pg_pool, org, monkeypatch)
    calls = []

    def transport(*args, **kwargs):
        calls.append((kwargs.get('method'), kwargs.get('timeout')))
        return SimpleNamespace(status=500, data=b'{"error":"temporarily_unavailable"}', headers={})

    monkeypatch.setattr(module, 'GoogleAuthRequest', lambda: transport)
    # Credentials.refresh is the actual installed SDK, not a synthetic success.
    with pytest.raises(CalendarUnavailable, match='refresh_unavailable'):
        await connection.credentials(org)
    assert calls == [('POST', 10)]
    row = await connection.row(org)
    assert row['connection_status'] == 'reconnect_required' and row['last_error_code'] == 'refresh_outcome_unknown'
    assert connection.fernet.decrypt(row['refresh_token'].encode()) == b'old-refresh'
    with pytest.raises(CalendarUnavailable, match='reconnect_required'):
        await connection.credentials(org)
    assert len(calls) == 1


async def test_reauthorization_fences_old_thread_and_consumes_generation(pg_pool,sample_org,monkeypatch):
    org=sample_org['id']; connection=await prepare(pg_pool,org,monkeypatch)
    entered=threading.Event(); release=threading.Event(); finished=threading.Event()
    def refresh(creds,request):
        entered.set(); assert release.wait(10); creds.token='late-token'; finished.set()
    monkeypatch.setattr(Credentials,'refresh',refresh)
    task=asyncio.create_task(connection.credentials(org))
    try:
        assert await asyncio.to_thread(entered.wait,5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        await connection.begin_oauth(org,'synthetic-nonce')
        row=await connection.row(org); generation=row['oauth_generation']
        new=SimpleNamespace(token='new-token',refresh_token='new-refresh',expiry=datetime.now(timezone.utc)+timedelta(hours=1))
        assert await connection.save_oauth(org,generation,new)
        assert not await connection.save_oauth(org,generation,new)
        release.set(); assert await asyncio.to_thread(finished.wait,5)
        row=await connection.row(org)
        assert connection.fernet.decrypt(row['access_token'].encode())==b'new-token'
        assert row['connection_status']=='connected' and row['last_error_code'] is None
    finally:
        release.set()
        if not task.done(): task.cancel()


async def test_late_refresh_cannot_overwrite_new_epoch_even_if_lock_is_lost(pg_pool,sample_org,monkeypatch):
    org=sample_org['id']; connection=await prepare(pg_pool,org,monkeypatch)
    entered=threading.Event(); release=threading.Event()
    def refresh(creds,request):
        entered.set(); assert release.wait(10)
        creds.token='late-token'; creds.expiry=datetime.now(timezone.utc).replace(tzinfo=None)+timedelta(hours=1)
    monkeypatch.setattr(Credentials,'refresh',refresh)
    task=asyncio.create_task(connection.credentials(org))
    try:
        assert await asyncio.to_thread(entered.wait,5)
        replacement=connection.fernet.encrypt(b'replaced-token').decode()
        await pg_pool.execute("""UPDATE google_calendar_credentials SET connection_epoch=$2,
            credential_version=credential_version+1,connection_status='connected',last_error_code=NULL,
            access_token=$3 WHERE organization_id=$1""",org,uuid4(),replacement)
        release.set()
        with pytest.raises(CalendarUnavailable,match='connection_changed'): await task
        row=await connection.row(org)
        assert connection.fernet.decrypt(row['access_token'].encode())==b'replaced-token'
    finally:
        release.set()
        if not task.done(): task.cancel()


@pytest.mark.parametrize('error_kind,expected_status', [
    ('invalid_grant', 'revoked'),
    ('retryable', 'reconnect_required'),
])
async def test_refresh_errors_keep_durable_marker_and_never_retry(
        pg_pool, sample_org, monkeypatch, error_kind, expected_status):
    from google.auth.exceptions import RefreshError

    org = sample_org['id']
    connection = await prepare(pg_pool, org, monkeypatch)
    calls = []

    def refresh(creds, request):
        calls.append('refresh')
        if error_kind == 'invalid_grant':
            raise RefreshError({'error': 'invalid_grant'})
        error = RefreshError('synthetic transient failure')
        error.retryable = True
        raise error

    monkeypatch.setattr(Credentials, 'refresh', refresh)
    with pytest.raises(CalendarUnavailable):
        await connection.credentials(org)
    row = await connection.row(org)
    assert row['connection_status'] == expected_status
    assert row['credential_version'] > 0
    with pytest.raises(CalendarUnavailable):
        await connection.credentials(org)
    assert calls == ['refresh']
    unchanged = await connection.row(org)
    assert unchanged['credential_version'] == row['credential_version'] and unchanged['last_error_code'] == row['last_error_code']
    assert unchanged['access_token'] == row['access_token']
    assert unchanged['refresh_token'] == row['refresh_token']


@pytest.mark.parametrize('malformation', ['empty_refresh_token', 'missing_expiry'])
async def test_malformed_refresh_result_fails_closed_without_retry(
        pg_pool, sample_org, monkeypatch, malformation):
    org = sample_org['id']
    connection = await prepare(pg_pool, org, monkeypatch)
    calls = []

    def refresh(creds, request):
        calls.append('refresh')
        creds.token = 'synthetic-new-access'
        if malformation == 'empty_refresh_token':
            creds._refresh_token = ''
        else:
            creds.expiry = None

    monkeypatch.setattr(Credentials, 'refresh', refresh)
    with pytest.raises(CalendarUnavailable, match='invalid_token_response'):
        await connection.credentials(org)
    row = await connection.row(org)
    assert row['connection_status'] == 'reconnect_required'
    assert row['credential_version'] > 0
    assert connection.fernet.decrypt(row['access_token'].encode()) == b'old-access'
    assert connection.fernet.decrypt(row['refresh_token'].encode()) == b'old-refresh'
    with pytest.raises(CalendarUnavailable):
        await connection.credentials(org)
    assert calls == ['refresh']


async def test_refresh_success_cas_rejects_changed_version_and_dispatch_marker(
        pg_pool, sample_org, monkeypatch):
    org = sample_org['id']
    connection = await prepare(pg_pool, org, monkeypatch)
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def refresh(creds, request):
        calls.append('refresh')
        entered.set()
        assert release.wait(10)
        creds.token = 'late-access'
        creds.expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)

    monkeypatch.setattr(Credentials, 'refresh', refresh)
    task = asyncio.create_task(connection.credentials(org))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        before = await connection.row(org)
        await pg_pool.execute("""UPDATE google_calendar_credentials
            SET credential_version=credential_version+1,last_error_code='synthetic_replacement'
            WHERE organization_id=$1""", org)
        release.set()
        with pytest.raises(CalendarUnavailable, match='connection_changed'):
            await task
        after = await connection.row(org)
        assert after['credential_version'] == before['credential_version'] + 1
        assert after['last_error_code'] == 'synthetic_replacement'
        assert after['access_token'] == before['access_token']
        assert after['connection_status'] == 'reconnect_required'
        assert calls == ['refresh']
    finally:
        release.set()
        if not task.done():
            task.cancel()
