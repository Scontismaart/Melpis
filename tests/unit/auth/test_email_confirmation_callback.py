import asyncio
import json
import time
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlencode, urlsplit

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from src.core.auth import bff
from src.core.auth.csrf import (
    CSRF_EXEMPT_PATHS,
    csrf_cookie_name,
    validate_csrf_request,
)
from src.core.auth.register import router as register_router

SUPABASE_URL = "https://supabase.test"
ANON_KEY = "synthetic-anon-key"
PUBLIC_APP_URL = "http://localhost:4174"
CALLBACK = "/api/auth/signup/callback"
ERROR_LOCATION = "/accedi/?errore=conferma"
SUCCESS_LOCATION = "/app/"
CODE = "synthetic-pkce-authorization-code"
VERIFIER = "synthetic-pkce-verifier-abcdefghijklmnopqrstuvwxyz1234"
ACCESS_TOKEN = "synthetic-confirmation-access-token"
REFRESH_TOKEN = "synthetic-confirmation-refresh-token"
USER_ID = "123e4567-e89b-12d3-a456-426614174000"
EMAIL = "new@example.test"
BUSINESS_NAME = "Studio Nuovo"
FINAL_PASSWORD = "Strong-pass-2026!"


def _response(status_code: int, payload: dict | None = None):
    return SimpleNamespace(status_code=status_code, json=lambda: payload or {})


@pytest.fixture
def signup_client(monkeypatch):
    from src.core.auth import register

    monkeypatch.setenv("SUPABASE_URL", SUPABASE_URL)
    monkeypatch.setenv("SUPABASE_ANON_KEY", ANON_KEY)
    monkeypatch.setenv("PUBLIC_APP_URL", PUBLIC_APP_URL)
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("MELPIS_BETA_ALLOWED_EMAILS", EMAIL)
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    register._pending_memory.clear()
    register._password_capsule_memory.clear()
    register._callback_claim_memory.clear()
    register._completion_marker_memory.clear()
    post = AsyncMock(return_value=_response(200, {"user": {"id": USER_ID}}))
    put = AsyncMock(return_value=_response(200, {"id": USER_ID}))
    client = SimpleNamespace(post=post, put=put)
    monkeypatch.setattr(register.bff, "_client", AsyncMock(return_value=client))
    monkeypatch.setattr(register.throttle, "is_throttled", AsyncMock(return_value=False))
    monkeypatch.setattr(register.throttle, "record_event", AsyncMock())
    monkeypatch.setattr(register, "_client_ip", lambda _request: "192.0.2.10")

    app = FastAPI()
    repo = SimpleNamespace(
        get_auth_access_allowed=AsyncMock(return_value=True),
        get_or_create_organization_with_owner=AsyncMock(
            return_value={"organization_id": "123e4567-e89b-12d3-a456-426614174001"}
        )
    )
    app.state.repo = repo
    app.include_router(register_router)

    @app.middleware("http")
    async def csrf_guard(request, call_next):
        request.state.trace_id = "synthetic-trace-id"
        ok, detail = validate_csrf_request(request)
        if not ok:
            return JSONResponse({"detail": detail}, status_code=403)
        return await call_next(request)

    return TestClient(app, base_url="http://api-internal:8000", follow_redirects=False), post, put, repo


def _register(client, *, email: str = EMAIL, business_name: str = BUSINESS_NAME):
    return client.post(
        "/api/auth/register",
        json={"email": email, "nome_attivita": business_name, "password": FINAL_PASSWORD},
        headers={"Origin": PUBLIC_APP_URL},
    )


def _cookie_headers(response):
    return response.headers.get_list("set-cookie")


def _cookie_header(response, name: str) -> str:
    return next(cookie for cookie in _cookie_headers(response) if cookie.startswith(f"{name}="))


def _cookie_max_age(cookie: str) -> int:
    value = next(part.split("=", 1)[1] for part in cookie.split("; ") if part.startswith("Max-Age="))
    return int(value)


def _assert_host_only_cookie(cookie: str, *, httponly: bool = True, secure: bool = False):
    assert "Path=/" in cookie
    assert "SameSite=lax" in cookie or "SameSite=strict" in cookie
    assert "Domain=" not in cookie
    assert ("HttpOnly" in cookie) is httponly
    assert ("Secure" in cookie) is secure


def _assert_security_headers(response):
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"


def _assert_no_auth_secrets(response, caplog=None):
    for value in (CODE, VERIFIER, ACCESS_TOKEN, REFRESH_TOKEN, FINAL_PASSWORD, ANON_KEY):
        assert value not in response.text
        assert value not in response.headers.get("location", "")
        if caplog is not None:
            assert value not in caplog.text
    location = response.headers.get("location", "")
    for key in ("code=", "access_token", "refresh_token", "password", "verifier"):
        assert key not in location.lower()


def _begin_signup(client, post):
    response = _register(client)
    assert response.status_code == 202
    assert response.json() == {"ok": True, "message": "Se l'indirizzo può essere registrato, riceverai un link per continuare."}
    post.assert_awaited_once()
    return response


def _stage_code(client):
    response = client.get(f"{CALLBACK}?code={CODE}")
    assert response.status_code == 303
    assert response.headers["location"] == CALLBACK
    _assert_security_headers(response)
    cookie = _cookie_header(response, "wa_signup_code")
    assert cookie.startswith(f"wa_signup_code={CODE};")
    assert "Max-Age=300" in cookie
    _assert_host_only_cookie(cookie)
    _assert_no_auth_secrets(response)
    return response


def _complete_form(client, *, password: str | None = None, confirm: str | None = None, origin: str = PUBLIC_APP_URL):
    values = {"complete": "1"} if password is None and confirm is None else {
        "password": password or "",
        "confirm_password": confirm or "",
    }
    return client.post(
        CALLBACK,
        content=urlencode(values),
        headers={"Origin": origin, "Content-Type": "application/x-www-form-urlencoded"},
    )


def _copy_flow_cookies(source, target):
    for name in ("wa_signup_email", "wa_signup_next", "wa_signup_flow", "wa_signup_capsule"):
        value = source.cookies.get(name)
        if value:
            target.cookies.set(name, value)


def _session_payload(*, email: str = EMAIL, confirmed: bool = True):
    user = {
        "id": USER_ID,
        "email": email,
        "user_metadata": {"nome_attivita": BUSINESS_NAME},
    }
    if confirmed:
        user["email_confirmed_at"] = "2026-09-30T12:00:00Z"
    return {"access_token": ACCESS_TOKEN, "refresh_token": REFRESH_TOKEN, "user": user}


def _confirmed_remote_user(*, email: str = EMAIL, user_id: str = USER_ID):
    return {
        "id": user_id,
        "email": email,
        "email_confirmed_at": "2026-09-30T12:00:00Z",
    }


def test_register_generates_unreturned_temporary_password_and_pkce_without_provisioning(signup_client):
    from src.core.auth import register

    client, post, _put, repo = signup_client
    response = _begin_signup(client, post)

    assert response.headers.get_list("set-cookie")
    verifier_cookie = _cookie_header(response, "wa_signup_verifier")
    assert "HttpOnly" in verifier_cookie and "SameSite=lax" in verifier_cookie
    assert "Domain=" not in verifier_cookie and "Path=/" in verifier_cookie
    verifier = client.cookies.get("wa_signup_verifier")
    assert verifier and verifier != VERIFIER
    assert len(verifier) >= 43
    flow_id = client.cookies.get("wa_signup_flow")
    capsule_id = client.cookies.get("wa_signup_capsule")
    assert flow_id and len(flow_id) >= 43
    assert capsule_id and len(capsule_id) >= 43
    assert client.cookies.get("wa_signup_email") == sha256(EMAIL.encode()).hexdigest()

    request = post.await_args
    request_url = urlsplit(request.args[0])
    assert request_url.path == "/auth/v1/signup"
    redirect_to = request.kwargs["params"]["redirect_to"]
    assert redirect_to == f"{PUBLIC_APP_URL}{CALLBACK}"
    payload = request.kwargs["json"]
    assert payload["email"] == EMAIL
    assert payload["code_challenge"] == bff.pkce_challenge(verifier)
    assert payload["code_challenge_method"].lower() == "s256"
    assert payload["data"]["nome_attivita"] == BUSINESS_NAME
    assert payload["password"] not in response.text
    assert payload["password"] != FINAL_PASSWORD
    assert "password" not in response.json()
    assert FINAL_PASSWORD not in repr(register._password_capsule_memory[capsule_id][1])
    decrypted_capsule = register._password_capsule_fernet().decrypt(
        register._password_capsule_memory[capsule_id][1].encode("ascii")
    )
    assert json.loads(decrypted_capsule)["password"] == FINAL_PASSWORD
    capsule_cookie = _cookie_header(response, "wa_signup_capsule")
    _assert_host_only_cookie(capsule_cookie)
    max_age = int(capsule_cookie.split("Max-Age=", 1)[1].split(";", 1)[0])
    assert 0 < max_age <= register._PASSWORD_CAPSULE_TTL_SECONDS
    assert not any(cookie.startswith((bff.access_cookie_name() + "=", bff.refresh_cookie_name() + "=")) for cookie in _cookie_headers(response))
    repo.get_or_create_organization_with_owner.assert_not_awaited()


def test_signup_response_does_not_distinguish_existing_email_and_never_sets_session(signup_client):
    client, post, _put, repo = signup_client
    post.side_effect = [_response(422, {"msg": "synthetic existing account"}), _response(200, {"user": {"id": USER_ID}, "access_token": ACCESS_TOKEN})]

    first = _register(client)
    client.cookies.clear()
    second = _register(client)

    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    assert _cookie_header(first, "wa_signup_verifier")
    assert _cookie_header(second, "wa_signup_verifier")
    assert not any(cookie.startswith((bff.access_cookie_name() + "=", bff.refresh_cookie_name() + "=")) for response in (first, second) for cookie in _cookie_headers(response))
    repo.get_or_create_organization_with_owner.assert_not_awaited()


def test_register_keeps_generic_response_but_does_not_create_unlisted_auth_user(
    signup_client, monkeypatch
):
    from src.core.auth import register

    client, post, _put, repo = signup_client
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("MELPIS_BETA_ALLOWED_EMAILS", EMAIL)
    allowed = _register(client)
    client.cookies.clear()
    post.reset_mock()
    monkeypatch.setenv("MELPIS_BETA_ALLOWED_EMAILS", "reviewed@example.test")

    response = _register(client)

    def cookie_signature(result):
        return sorted(
            (
                cookie.split(";", 1)[0].split("=", 1)[0],
                tuple(attribute.strip().lower() for attribute in cookie.split(";")[1:]),
            )
            for cookie in _cookie_headers(result)
        )
    assert allowed.status_code == response.status_code == 202
    assert allowed.json() == response.json()
    assert cookie_signature(allowed) == cookie_signature(response)
    assert response.status_code == 202
    assert response.json() == {
        "ok": True,
        "message": "Se l'indirizzo può essere registrato, riceverai un link per continuare.",
    }
    post.assert_not_awaited()
    repo.get_or_create_organization_with_owner.assert_not_awaited()

    client.cookies.clear()
    monkeypatch.setenv("MELPIS_BETA_ALLOWED_EMAILS", EMAIL)
    monkeypatch.setattr(
        register, "_save_password_capsule", AsyncMock(side_effect=RuntimeError("unavailable"))
    )
    capsule_failure = _register(client)

    assert capsule_failure.status_code == response.status_code == 202
    assert capsule_failure.json() == response.json()
    assert cookie_signature(capsule_failure) == cookie_signature(response)
    post.assert_not_awaited()


def test_signup_confirmation_rechecks_roster_before_setting_password_or_provisioning(
    signup_client, monkeypatch
):
    from src.core.auth import register

    client, post, put, repo = signup_client
    monkeypatch.setattr(register.bff, "exchange_pkce", AsyncMock(return_value=_session_payload()))
    monkeypatch.setattr(
        register, "_get_supabase_user", AsyncMock(return_value=_confirmed_remote_user())
    )
    _begin_signup(client, post)
    _stage_code(client)
    monkeypatch.setenv("MELPIS_BETA_ALLOWED_EMAILS", "reviewed@example.test")

    response = _complete_form(client, password=FINAL_PASSWORD, confirm=FINAL_PASSWORD)

    assert response.status_code == 303
    assert response.headers["location"] == ERROR_LOCATION
    put.assert_not_awaited()
    repo.get_or_create_organization_with_owner.assert_not_awaited()


@pytest.mark.parametrize(("status_code", "error_class"), [(422, "client_error"), (503, "server_error")])
def test_signup_logs_only_sanitized_upstream_status_and_keeps_generic_response(
    signup_client, status_code, error_class, caplog
):
    client, post, _put, _repo = signup_client
    post.return_value = _response(status_code, {"message": f"private detail for {EMAIL}"})

    response = _register(client)

    assert response.status_code == 202
    assert response.json() == {
        "ok": True,
        "message": "Se l'indirizzo può essere registrato, riceverai un link per continuare.",
    }
    assert f"status={status_code}" in caplog.text
    assert f"error_class={error_class}" in caplog.text
    assert EMAIL not in caplog.text
    assert "private detail" not in caplog.text


@pytest.mark.parametrize(
    ("requested_next", "expected_location"),
    [
        ("/app/inbox", "/app/inbox"),
        ("/app/settings", "/app/settings"),
        ("/area/impostazioni/", "/area/impostazioni/"),
        ("https://attacker.test/steal", SUCCESS_LOCATION),
        ("//attacker.test/steal", SUCCESS_LOCATION),
        ("/%2f%2fattacker.test/steal", SUCCESS_LOCATION),
        ("/%0A/attacker.test", SUCCESS_LOCATION),
        ("/line\nbreak", SUCCESS_LOCATION),
    ],
)
def test_callback_redirects_only_to_strict_internal_next(
    signup_client, monkeypatch, requested_next, expected_location
):
    from src.core.auth import bff as auth_bff

    client, _post, _put, _repo = signup_client
    response = client.post(
        "/api/auth/register",
        json={"email": EMAIL, "nome_attivita": BUSINESS_NAME, "password": FINAL_PASSWORD, "next": requested_next},
        headers={"Origin": PUBLIC_APP_URL},
    )
    assert response.status_code == 202
    _stage_code(client)
    monkeypatch.setattr(auth_bff, "exchange_pkce", AsyncMock(return_value=_session_payload()))

    completed = _complete_form(client)

    assert completed.status_code == 303
    assert completed.headers["location"] == expected_location
    assert "code=" not in completed.headers["location"]
    assert "access_token" not in completed.headers["location"]


def test_chosen_password_is_capsuled_and_never_sent_to_supabase_signup(signup_client):
    client, post, _put, _repo = signup_client
    response = client.post(
        "/api/auth/register",
        json={"email": EMAIL, "password": FINAL_PASSWORD, "nome_attivita": BUSINESS_NAME},
        headers={"Origin": PUBLIC_APP_URL},
    )

    assert response.status_code == 202
    assert FINAL_PASSWORD not in response.text
    assert post.await_args.kwargs["json"]["password"] != FINAL_PASSWORD
    assert not any(cookie.startswith("wa_csrf=") for cookie in _cookie_headers(response))


def test_callback_get_stages_pkce_code_then_clean_get_renders_completion_form(signup_client, monkeypatch):
    from src.core.auth import bff as auth_bff

    client, post, _put, _repo = signup_client
    _begin_signup(client, post)
    exchange = AsyncMock(return_value=_session_payload())
    monkeypatch.setattr(auth_bff, "exchange_pkce", exchange)

    staged = _stage_code(client)
    assert post.await_count == 1
    assert client.get(CALLBACK).status_code == 200
    form = client.get(CALLBACK)
    assert form.status_code == 200
    _assert_security_headers(form)
    assert 'name="complete"' in form.text
    assert 'name="password"' not in form.text
    assert 'name="confirm_password"' not in form.text
    _assert_no_auth_secrets(form)
    assert "completa la registrazione" in form.text.lower()
    exchange.assert_not_awaited()
    assert not any(cookie.startswith((bff.access_cookie_name() + "=", bff.refresh_cookie_name() + "=")) for cookie in _cookie_headers(staged))


@pytest.mark.asyncio
async def test_concurrent_callback_post_cannot_consume_or_invalidate_active_signup(signup_client, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from src.core.auth import bff as auth_bff
    from src.core.auth import register

    client, post, put, repo = signup_client
    _begin_signup(client, post)
    _stage_code(client)
    exchange_started = asyncio.Event()
    finish_exchange = asyncio.Event()

    async def slow_exchange(code, verifier):
        exchange_started.set()
        await finish_exchange.wait()
        return _session_payload()

    monkeypatch.setattr(auth_bff, "exchange_pkce", AsyncMock(side_effect=slow_exchange))
    monkeypatch.setattr(register, "_get_supabase_user", AsyncMock(return_value=_confirmed_remote_user()))
    headers = {
        "Origin": PUBLIC_APP_URL,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    payload = urlencode({"complete": "1"})
    cookies = dict(client.cookies)

    async with AsyncClient(
        transport=ASGITransport(app=client.app),
        base_url="http://api-internal:8000",
        cookies=cookies,
        follow_redirects=False,
    ) as async_client:
        active = asyncio.create_task(async_client.post(CALLBACK, content=payload, headers=headers))
        await asyncio.wait_for(exchange_started.wait(), timeout=1)
        duplicate = await async_client.post(CALLBACK, content=payload, headers=headers)
        assert duplicate.status_code == 303
        assert duplicate.headers["location"] == CALLBACK
        assert not duplicate.headers.get_list("set-cookie")
        assert put.await_count == 0

        finish_exchange.set()
        completed = await active

    assert completed.status_code == 303
    assert completed.headers["location"] == SUCCESS_LOCATION
    assert put.await_count == 1
    repo.get_or_create_organization_with_owner.assert_awaited_once()
    assert not register._callback_claim_memory


@pytest.mark.asyncio
async def test_shared_redis_claim_blocks_duplicate_callback_post(signup_client, monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from redis.asyncio import Redis

    from src.core.auth import bff as auth_bff

    client, post, put, repo = signup_client
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("REDIS_URL", "redis://valkey:6379/0")
    storage = {}
    set_calls = []
    release_calls = []

    class SharedFakeRedis:
        async def set(self, key, value, *, ex, nx):
            set_calls.append((key, value, ex, nx))
            if nx and key in storage:
                return False
            storage[key] = value
            return True

        async def get(self, key):
            return storage.get(key)

        async def delete(self, key):
            return int(storage.pop(key, None) is not None)

        async def getdel(self, key):
            return storage.pop(key, None)

        async def eval(self, _script, _numkeys, key, token):
            release_calls.append((key, token))
            if storage.get(key) != token:
                return 0
            storage.pop(key)
            return 1

        async def aclose(self):
            return None

    monkeypatch.setattr(
        Redis, "from_url", staticmethod(lambda *_args, **_kwargs: SharedFakeRedis())
    )
    _begin_signup(client, post)
    _stage_code(client)
    exchange_started = asyncio.Event()
    finish_exchange = asyncio.Event()

    async def slow_exchange(code, verifier):
        exchange_started.set()
        await finish_exchange.wait()
        return _session_payload()

    monkeypatch.setattr(auth_bff, "exchange_pkce", AsyncMock(side_effect=slow_exchange))
    headers = {
        "Origin": PUBLIC_APP_URL,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    payload = urlencode({"complete": "1"})
    flow_cookies = dict(client.cookies)

    async with (
        AsyncClient(
            transport=ASGITransport(app=client.app),
            base_url="http://api-internal:8000",
            cookies=flow_cookies,
            follow_redirects=False,
        ) as first_client,
        AsyncClient(
            transport=ASGITransport(app=client.app),
            base_url="http://api-internal:8000",
            cookies=flow_cookies,
            follow_redirects=False,
        ) as duplicate_client,
    ):
        active = asyncio.create_task(
            first_client.post(CALLBACK, content=payload, headers=headers)
        )
        await asyncio.wait_for(exchange_started.wait(), timeout=1)
        claim_key = "auth:signup:callback-claim:" + client.cookies.get("wa_signup_flow")
        assert claim_key in storage

        duplicate = await duplicate_client.post(
            CALLBACK, content=payload, headers=headers
        )

        assert duplicate.status_code == 303
        assert duplicate.headers["location"] == CALLBACK
        assert not duplicate.headers.get_list("set-cookie")
        claim_attempts = [call for call in set_calls if call[0] == claim_key]
        assert len(claim_attempts) == 2
        assert claim_attempts[0][3] is True and claim_attempts[1][3] is True
        assert put.await_count == 0

        finish_exchange.set()
        completed = await active

    assert completed.status_code == 303
    assert completed.headers["location"] == SUCCESS_LOCATION
    assert put.await_count == 1
    assert claim_key not in storage
    assert len(release_calls) == 1 and release_calls[0][0] == claim_key
    repo.get_or_create_organization_with_owner.assert_awaited_once()


def test_replay_with_stale_pkce_cookies_recovers_from_retained_retry_state(
    signup_client, monkeypatch
):
    from src.core.auth import bff as auth_bff
    from src.core.auth import register

    client, post, put, repo = signup_client
    _begin_signup(client, post)
    _stage_code(client)
    stale_cookies = dict(client.cookies)
    exchange = AsyncMock(
        side_effect=[_session_payload(), RuntimeError("synthetic code already consumed")]
    )
    monkeypatch.setattr(auth_bff, "exchange_pkce", exchange)
    monkeypatch.setattr(register, "_get_supabase_user", AsyncMock(return_value=_confirmed_remote_user()))
    put.side_effect = [RuntimeError("synthetic password setup failure"), _response(200)]

    retry_response = _complete_form(client)
    assert retry_response.status_code == 303
    assert retry_response.headers["location"] == CALLBACK
    retry_id = client.cookies.get("wa_signup_pending")
    capsule_id = client.cookies.get("wa_signup_capsule")
    capsule_entry = register._password_capsule_memory[capsule_id]

    replay = TestClient(client.app, base_url="http://api-internal:8000", follow_redirects=False)
    for name, value in stale_cookies.items():
        replay.cookies.set(name, value)
    stale_response = _complete_form(replay)

    assert stale_response.status_code == 303
    assert stale_response.headers["location"] == SUCCESS_LOCATION
    assert _cookie_header(stale_response, bff.access_cookie_name()).startswith(
        f"{bff.access_cookie_name()}={ACCESS_TOKEN};"
    )
    assert client.cookies.get("wa_signup_pending") == retry_id
    assert capsule_id not in register._password_capsule_memory
    assert capsule_entry[1] not in repr(register._password_capsule_memory)
    assert put.await_count == 2

    completed = _complete_form(client)
    assert completed.status_code == 303
    assert completed.headers["location"] == "/accedi/?conferma=ok"
    assert not any(
        cookie.startswith((bff.access_cookie_name() + "=", bff.refresh_cookie_name() + "="))
        for cookie in _cookie_headers(completed)
    )
    assert exchange.await_count == 1
    assert put.await_count == 2
    repo.get_or_create_organization_with_owner.assert_awaited_once()


@pytest.mark.parametrize(
    "query",
    [
        "",
        f"code={CODE}&code={CODE}",
        "code=malformed",
        f"code={CODE}&state=unexpected",
        f"code={CODE}&type=email",
        f"code={CODE}&next=/area/&next=//attacker.test",
    ],
)
def test_missing_duplicate_malformed_or_extra_callback_query_fails_closed(signup_client, query):
    client, post, _put, _repo = signup_client
    _begin_signup(client, post)
    response = client.get(CALLBACK + (f"?{query}" if query else ""))

    assert response.status_code == 303
    assert response.headers["location"] == ERROR_LOCATION
    _assert_no_auth_secrets(response)
    assert post.await_count == 1


def test_wrong_origin_is_rejected_without_exchanging_code(signup_client, monkeypatch):
    from src.core.auth import bff as auth_bff

    client, post, _put, _repo = signup_client
    _begin_signup(client, post)
    _stage_code(client)
    exchange = AsyncMock(return_value=_session_payload())
    monkeypatch.setattr(auth_bff, "exchange_pkce", exchange)

    response = _complete_form(client, origin="https://attacker.test")

    assert response.status_code == 303
    assert response.headers["location"] == ERROR_LOCATION
    exchange.assert_not_awaited()
    assert "Max-Age=0" in _cookie_header(response, "wa_signup_code")


def test_callback_exchanges_pkce_sets_final_password_session_and_provisions_after_confirmation(signup_client, monkeypatch, caplog):
    from src.core.auth import bff as auth_bff

    client, post, put, repo = signup_client
    _begin_signup(client, post)
    verifier = client.cookies.get("wa_signup_verifier")
    _stage_code(client)
    exchange = AsyncMock(return_value=_session_payload())
    monkeypatch.setattr(auth_bff, "exchange_pkce", exchange)

    response = _complete_form(client)

    assert response.status_code == 303
    assert response.headers["location"] == SUCCESS_LOCATION
    _assert_security_headers(response)
    _assert_no_auth_secrets(response, caplog)
    exchange.assert_awaited_once_with(CODE, verifier)
    put.assert_awaited_once()
    assert put.await_args.args[0] == f"{SUPABASE_URL}/auth/v1/user"
    assert put.await_args.kwargs["json"] == {"password": FINAL_PASSWORD}
    assert put.await_args.kwargs["headers"]["Authorization"] == f"Bearer {ACCESS_TOKEN}"
    repo.get_or_create_organization_with_owner.assert_awaited_once_with(USER_ID, BUSINESS_NAME, pytest.approx(7))

    access = _cookie_header(response, bff.access_cookie_name())
    refresh = _cookie_header(response, bff.refresh_cookie_name())
    csrf = _cookie_header(response, csrf_cookie_name())
    assert access.startswith(f"{bff.access_cookie_name()}={ACCESS_TOKEN};")
    assert refresh.startswith(f"{bff.refresh_cookie_name()}={REFRESH_TOKEN};")
    _assert_host_only_cookie(access)
    _assert_host_only_cookie(refresh)
    _assert_host_only_cookie(csrf, httponly=False)
    for flow_cookie in ("wa_signup_code", "wa_signup_verifier", "wa_signup_email"):
        assert "Max-Age=0" in _cookie_header(response, flow_cookie)
    assert "trace_id=synthetic-trace-id" not in caplog.text


def test_disabled_account_does_not_set_confirmed_password_or_issue_session(signup_client, monkeypatch):
    from src.core.auth import bff as auth_bff

    client, post, put, repo = signup_client
    _begin_signup(client, post)
    _stage_code(client)
    monkeypatch.setattr(auth_bff, "exchange_pkce", AsyncMock(return_value=_session_payload()))
    repo.get_auth_access_allowed.return_value = False

    response = _complete_form(client)

    assert response.status_code == 303
    assert response.headers["location"] == ERROR_LOCATION
    repo.get_auth_access_allowed.assert_awaited_once_with(USER_ID)
    put.assert_not_awaited()
    assert not any(
        cookie.startswith(("wa_at=", "wa_rt=", "wa_csrf="))
        for cookie in _cookie_headers(response)
    )


@pytest.mark.parametrize("upstream_status", [429, 503])
def test_transient_pkce_exchange_errors_preserve_signup_flow_without_session(
    signup_client, upstream_status, caplog
):
    from src.core.auth import register

    client, post, put, repo = signup_client
    _begin_signup(client, post)
    _stage_code(client)
    capsule_id = client.cookies.get("wa_signup_capsule")
    assert capsule_id in register._password_capsule_memory
    post.return_value = _response(
        upstream_status, {"message": "synthetic private provider detail"}
    )

    deferred = _complete_form(client)

    assert deferred.status_code == 303
    assert deferred.headers["location"] == CALLBACK
    _assert_security_headers(deferred)
    _assert_no_auth_secrets(deferred, caplog)
    assert "synthetic private provider detail" not in caplog.text
    assert client.cookies.get("wa_signup_code") == CODE
    assert client.cookies.get("wa_signup_verifier")
    assert client.cookies.get("wa_signup_capsule") == capsule_id
    assert capsule_id in register._password_capsule_memory
    assert not register._pending_memory
    assert not any(
        cookie.startswith((bff.access_cookie_name() + "=", bff.refresh_cookie_name() + "="))
        for cookie in _cookie_headers(deferred)
    )
    assert put.await_count == 0
    repo.get_or_create_organization_with_owner.assert_not_awaited()

    post.return_value = _response(200, _session_payload())
    retried = _complete_form(client)

    assert retried.status_code == 303
    assert retried.headers["location"] == SUCCESS_LOCATION
    assert capsule_id not in register._password_capsule_memory
    assert _cookie_header(retried, bff.access_cookie_name()).startswith(
        f"{bff.access_cookie_name()}={ACCESS_TOKEN};"
    )
    assert post.await_count == 3
    put.assert_awaited_once()
    repo.get_or_create_organization_with_owner.assert_awaited_once_with(
        USER_ID, BUSINESS_NAME, pytest.approx(7)
    )


def test_password_setup_failure_retries_without_reexchanging_pkce_or_setting_early_session(
    signup_client, monkeypatch, caplog
):
    from src.core.auth import bff as auth_bff

    client, post, put, repo = signup_client
    _begin_signup(client, post)
    verifier = client.cookies.get("wa_signup_verifier")
    _stage_code(client)
    exchange = AsyncMock(return_value=_session_payload())
    monkeypatch.setattr(auth_bff, "exchange_pkce", exchange)
    identity_check = AsyncMock(return_value=_confirmed_remote_user())
    monkeypatch.setattr("src.core.auth.register._get_supabase_user", identity_check)
    put.side_effect = [
        RuntimeError("synthetic password setup failure"),
        RuntimeError("synthetic retry failure"),
        _response(200),
    ]

    failed_setup = _complete_form(client)

    assert failed_setup.status_code == 303
    assert failed_setup.headers["location"] == CALLBACK
    _assert_security_headers(failed_setup)
    _assert_no_auth_secrets(failed_setup, caplog)
    exchange.assert_awaited_once_with(CODE, verifier)
    assert put.await_count == 1
    assert not any(
        cookie.startswith(
            (bff.access_cookie_name() + "=", bff.refresh_cookie_name() + "=", csrf_cookie_name() + "=")
        )
        for cookie in _cookie_headers(failed_setup)
    )
    pending_cookie = _cookie_header(failed_setup, "wa_signup_pending")
    _assert_host_only_cookie(pending_cookie)
    assert 0 < _cookie_max_age(pending_cookie) <= 300
    pending_id = client.cookies.get("wa_signup_pending")
    assert pending_id and ACCESS_TOKEN not in pending_id and REFRESH_TOKEN not in pending_id
    assert "Max-Age=0" in _cookie_header(failed_setup, "wa_signup_code")
    assert repo.get_or_create_organization_with_owner.await_count == 0

    form = client.get(CALLBACK)
    assert form.status_code == 200
    _assert_no_auth_secrets(form, caplog)
    assert not any(
        cookie.startswith((bff.access_cookie_name() + "=", bff.refresh_cookie_name() + "="))
        for cookie in _cookie_headers(form)
    )

    first_retry_id = pending_id
    failed_retry = _complete_form(client)

    assert failed_retry.status_code == 303
    assert failed_retry.headers["location"] == CALLBACK
    assert put.await_count == 2
    second_retry_id = client.cookies.get("wa_signup_pending")
    assert second_retry_id == first_retry_id
    assert not any(
        cookie.startswith(
            (bff.access_cookie_name() + "=", bff.refresh_cookie_name() + "=", csrf_cookie_name() + "=")
        )
        for cookie in _cookie_headers(failed_retry)
    )

    flow_cookies = dict(client.cookies)
    retried = _complete_form(client)

    assert retried.status_code == 303
    assert retried.headers["location"] == SUCCESS_LOCATION
    exchange.assert_awaited_once_with(CODE, verifier)
    assert put.await_count == 3
    assert identity_check.await_count == 2
    repo.get_or_create_organization_with_owner.assert_awaited_once()
    assert _cookie_header(retried, bff.access_cookie_name()).startswith(
        f"{bff.access_cookie_name()}={ACCESS_TOKEN};"
    )
    assert _cookie_header(retried, bff.refresh_cookie_name()).startswith(
        f"{bff.refresh_cookie_name()}={REFRESH_TOKEN};"
    )
    assert "Max-Age=0" in _cookie_header(retried, "wa_signup_pending")
    _assert_no_auth_secrets(retried, caplog)

    replay_client = TestClient(client.app, base_url="http://api-internal:8000", follow_redirects=False)
    for name, value in flow_cookies.items():
        replay_client.cookies.set(name, value)
    replay_client.cookies.set("wa_signup_pending", first_retry_id)
    replay = _complete_form(replay_client)
    assert replay.status_code == 303
    assert replay.headers["location"] == "/accedi/?conferma=ok"
    assert put.await_count == 3
    replay_client.cookies.set("wa_signup_pending", second_retry_id)
    replay_again = _complete_form(replay_client)
    assert replay_again.headers["location"] == "/accedi/?conferma=ok"
    assert put.await_count == 3


def test_retry_after_applied_password_put_timeout_keeps_pending_state_and_capsule(
    signup_client, monkeypatch
):
    from src.core.auth import bff as auth_bff
    from src.core.auth import register

    client, post, put, repo = signup_client
    _begin_signup(client, post)
    _stage_code(client)
    exchange = AsyncMock(return_value=_session_payload())
    monkeypatch.setattr(auth_bff, "exchange_pkce", exchange)
    identity_check = AsyncMock(return_value=_confirmed_remote_user())
    monkeypatch.setattr(register, "_get_supabase_user", identity_check)
    put.side_effect = [httpx.ReadTimeout("synthetic response lost after apply"), _response(200)]

    first = _complete_form(client)
    pending_id = client.cookies.get("wa_signup_pending")
    capsule_id = client.cookies.get("wa_signup_capsule")
    assert first.status_code == 303
    assert first.headers["location"] == CALLBACK
    assert pending_id == register._pending_id_for_flow(client.cookies.get("wa_signup_flow"))
    assert pending_id in register._pending_memory
    assert capsule_id in register._password_capsule_memory
    assert not any(
        cookie.startswith((bff.access_cookie_name() + "=", bff.refresh_cookie_name() + "="))
        for cookie in _cookie_headers(first)
    )

    retried = _complete_form(client)

    assert retried.status_code == 303
    assert retried.headers["location"] == SUCCESS_LOCATION
    assert client.cookies.get("wa_signup_pending") is None
    assert exchange.await_count == 1
    assert identity_check.await_count == 1
    assert put.await_count == 2
    assert pending_id not in register._pending_memory
    assert capsule_id not in register._password_capsule_memory
    assert _cookie_header(retried, bff.access_cookie_name()).startswith(
        f"{bff.access_cookie_name()}={ACCESS_TOKEN};"
    )
    repo.get_or_create_organization_with_owner.assert_awaited_once()


def test_provisioning_failure_retries_with_submitted_business_name_before_completion(
    signup_client, monkeypatch
):
    from src.core.auth import bff as auth_bff
    from src.core.auth import register

    client, post, put, repo = signup_client
    _begin_signup(client, post)
    _stage_code(client)
    exchange = AsyncMock(return_value=_session_payload())
    monkeypatch.setattr(auth_bff, "exchange_pkce", exchange)
    monkeypatch.setattr(
        register, "_get_supabase_user", AsyncMock(return_value=_confirmed_remote_user())
    )
    put.return_value = _response(200)
    provisioning_calls = []
    order = []

    async def provision(user_id, business_name, trial_days):
        provisioning_calls.append((user_id, business_name, trial_days))
        order.append("provision")
        if len(provisioning_calls) == 1:
            raise RuntimeError("synthetic provisioning failure")
        return {"organization_id": "synthetic-organization-id"}

    repo.get_or_create_organization_with_owner = AsyncMock(side_effect=provision)
    write_marker = register._write_completion_marker
    cleanup = register._cleanup_completed_signup

    async def ordered_marker(state):
        order.append("marker")
        return await write_marker(state)

    async def ordered_cleanup(pending_id, capsule_id, trace_id):
        order.append("cleanup")
        assert register._completion_marker_memory
        await cleanup(pending_id, capsule_id, trace_id)

    monkeypatch.setattr(register, "_write_completion_marker", ordered_marker)
    monkeypatch.setattr(register, "_cleanup_completed_signup", ordered_cleanup)

    first = _complete_form(client)
    pending_id = client.cookies.get("wa_signup_pending")
    capsule_id = client.cookies.get("wa_signup_capsule")

    assert first.status_code == 303
    assert first.headers["location"] == CALLBACK
    assert order == ["provision"]
    assert not register._completion_marker_memory
    assert pending_id in register._pending_memory
    assert capsule_id in register._password_capsule_memory
    assert not any(
        cookie.startswith((bff.access_cookie_name() + "=", bff.refresh_cookie_name() + "="))
        for cookie in _cookie_headers(first)
    )

    completed = _complete_form(client)

    assert completed.status_code == 303
    assert completed.headers["location"] == SUCCESS_LOCATION
    assert order == ["provision", "provision", "marker", "cleanup"]
    assert provisioning_calls == [
        (USER_ID, BUSINESS_NAME, pytest.approx(7)),
        (USER_ID, BUSINESS_NAME, pytest.approx(7)),
    ]
    assert exchange.await_count == 1
    assert put.await_count == 2
    assert pending_id not in register._pending_memory
    assert capsule_id not in register._password_capsule_memory
    assert _cookie_header(completed, bff.access_cookie_name()).startswith(
        f"{bff.access_cookie_name()}={ACCESS_TOKEN};"
    )


@pytest.mark.parametrize("bad_pending_cookie", ["malformed", "B" * 43])
def test_malformed_or_mismatched_pending_cookie_cannot_delete_valid_retry_state(
    signup_client, monkeypatch, bad_pending_cookie
):
    from src.core.auth import bff as auth_bff
    from src.core.auth import register

    client, post, put, repo = signup_client
    _begin_signup(client, post)
    _stage_code(client)
    monkeypatch.setattr(auth_bff, "exchange_pkce", AsyncMock(return_value=_session_payload()))
    monkeypatch.setattr(
        register, "_get_supabase_user", AsyncMock(return_value=_confirmed_remote_user())
    )
    put.side_effect = [RuntimeError("synthetic retryable setup failure"), _response(200)]

    initial_retry = _complete_form(client)
    pending_id = client.cookies.get("wa_signup_pending")
    capsule_id = client.cookies.get("wa_signup_capsule")
    assert initial_retry.status_code == 303
    assert initial_retry.headers["location"] == CALLBACK
    assert pending_id == register._pending_id_for_flow(client.cookies.get("wa_signup_flow"))
    assert capsule_id in register._password_capsule_memory

    tampered_browser = TestClient(
        client.app, base_url="http://api-internal:8000", follow_redirects=False
    )
    _copy_flow_cookies(client, tampered_browser)
    tampered_browser.cookies.set("wa_signup_pending", bad_pending_cookie)
    rejected = _complete_form(tampered_browser)

    assert rejected.status_code == 303
    assert rejected.headers["location"] == ERROR_LOCATION
    assert pending_id in register._pending_memory
    assert capsule_id in register._password_capsule_memory
    assert put.await_count == 1

    completed = _complete_form(client)

    assert completed.status_code == 303
    assert completed.headers["location"] == SUCCESS_LOCATION
    assert put.await_count == 2
    assert pending_id not in register._pending_memory
    assert capsule_id not in register._password_capsule_memory
    repo.get_or_create_organization_with_owner.assert_awaited_once_with(
        USER_ID, BUSINESS_NAME, pytest.approx(7)
    )


def test_lost_pending_state_write_response_recovers_using_stable_flow_id(
    signup_client, monkeypatch
):
    from src.core.auth import bff as auth_bff
    from src.core.auth import register

    client, post, put, repo = signup_client
    _begin_signup(client, post)
    _stage_code(client)
    exchange = AsyncMock(return_value=_session_payload())
    monkeypatch.setattr(auth_bff, "exchange_pkce", exchange)
    identity_check = AsyncMock(return_value=_confirmed_remote_user())
    monkeypatch.setattr(register, "_get_supabase_user", identity_check)
    put.side_effect = [_response(200)]
    original_save = register._save_pending_session
    calls = 0

    async def save_then_lose_response(state):
        nonlocal calls
        saved = await original_save(state)
        calls += 1
        if calls == 1:
            raise RuntimeError("synthetic lost write response")
        return saved

    monkeypatch.setattr(register, "_save_pending_session", save_then_lose_response)
    first = _complete_form(client)
    flow_id = client.cookies.get("wa_signup_flow")
    pending_id = client.cookies.get("wa_signup_pending")
    capsule_id = client.cookies.get("wa_signup_capsule")

    assert first.status_code == 303
    assert first.headers["location"] == CALLBACK
    assert pending_id == register._pending_id_for_flow(flow_id)
    assert pending_id in register._pending_memory
    assert capsule_id in register._password_capsule_memory
    assert exchange.await_count == 1
    assert put.await_count == 0

    retried = _complete_form(client)

    assert retried.status_code == 303
    assert retried.headers["location"] == SUCCESS_LOCATION
    assert exchange.await_count == 1
    assert calls == 2
    assert identity_check.await_count == 1
    assert put.await_count == 1
    assert pending_id not in register._pending_memory
    assert capsule_id not in register._password_capsule_memory
    repo.get_or_create_organization_with_owner.assert_awaited_once()


@pytest.mark.asyncio
async def test_redis_read_exceptions_preserve_pending_state_and_password_capsule(
    signup_client, monkeypatch
):
    from redis.asyncio import Redis

    from src.core.auth import register

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("REDIS_URL", "redis://valkey:6379/0")
    storage = {}
    fail_once = set()
    getdel_calls = []

    class FakeRedis:
        async def set(self, key, value, *, ex, nx):
            if nx and key in storage:
                return False
            storage[key] = value
            return True

        async def get(self, key):
            if key not in fail_once:
                fail_once.add(key)
                raise RuntimeError("synthetic Redis read failure")
            return storage.get(key)

        async def getdel(self, key):
            getdel_calls.append(key)
            return storage.pop(key, None)

        async def aclose(self):
            return None

    monkeypatch.setattr(Redis, "from_url", staticmethod(lambda *_a, **_k: FakeRedis()))
    email_hash = sha256(EMAIL.encode()).hexdigest()
    flow_id = "A" * 43
    capsule_id, _ttl, _expiry = await register._save_password_capsule(
        FINAL_PASSWORD, email_hash, flow_id
    )
    session = _session_payload()
    state = register._pending_state(
        session,
        session["user"],
        USER_ID,
        EMAIL,
        email_hash,
        "/app/",
        flow_id,
        capsule_id,
    )
    pending_id, _pending_ttl = await register._save_pending_session(state)

    with pytest.raises(RuntimeError, match="synthetic Redis read failure"):
        await register._read_pending_session(pending_id)
    retained = await register._read_pending_session(pending_id)
    assert retained == state

    with pytest.raises(RuntimeError, match="synthetic Redis read failure"):
        await register._consume_password_capsule(capsule_id, email_hash, flow_id)
    retained_capsule = await register._consume_password_capsule(
        capsule_id, email_hash, flow_id
    )
    assert retained_capsule["password"] == FINAL_PASSWORD
    assert not getdel_calls
    assert register._PENDING_PREFIX + pending_id in storage
    assert register._PASSWORD_CAPSULE_PREFIX + capsule_id in storage


def test_retry_state_write_outage_does_not_advertise_retry_or_delete_capsule(
    signup_client, monkeypatch
):
    from redis.asyncio import Redis

    from src.core.auth import bff as auth_bff
    from src.core.auth import register

    client, post, put, repo = signup_client
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("REDIS_URL", "redis://valkey:6379/0")
    storage = {}
    pending_set_attempts = []
    getdel_calls = []

    class UnavailablePendingRedis:
        async def set(self, key, value, *, ex, nx):
            if key.startswith(register._PENDING_PREFIX):
                pending_set_attempts.append((key, value, ex, nx))
                return False
            if nx and key in storage:
                return False
            storage[key] = value
            return True

        async def get(self, key):
            return storage.get(key)

        async def delete(self, key):
            return int(storage.pop(key, None) is not None)

        async def getdel(self, key):
            getdel_calls.append(key)
            return storage.pop(key, None)

        async def eval(self, _script, _numkeys, key, token):
            if storage.get(key) != token:
                return 0
            storage.pop(key)
            return 1

        async def aclose(self):
            return None

    monkeypatch.setattr(
        Redis,
        "from_url",
        staticmethod(lambda *_args, **_kwargs: UnavailablePendingRedis()),
    )
    _begin_signup(client, post)
    _stage_code(client)
    capsule_id = client.cookies.get("wa_signup_capsule")
    monkeypatch.setattr(
        auth_bff, "exchange_pkce", AsyncMock(return_value=_session_payload())
    )

    deferred = _complete_form(client)

    assert deferred.status_code == 303
    assert deferred.headers["location"] == CALLBACK
    assert not _cookie_headers(deferred)
    assert client.cookies.get("wa_signup_code") == CODE
    assert client.cookies.get("wa_signup_verifier")
    assert client.cookies.get("wa_signup_pending") is None
    assert len(pending_set_attempts) == 2
    pending_key = register._PENDING_PREFIX + register._pending_id_for_flow(
        client.cookies.get("wa_signup_flow")
    )
    assert all(
        call[0] == pending_key
        and 0 < call[2] <= register._PENDING_TTL_SECONDS
        and call[3] is True
        for call in pending_set_attempts
    )
    assert pending_key not in storage
    capsule_key = register._PASSWORD_CAPSULE_PREFIX + capsule_id
    assert capsule_key in storage
    assert put.await_count == 0
    assert repo.get_or_create_organization_with_owner.await_count == 0

    monkeypatch.setattr(
        auth_bff,
        "exchange_pkce",
        AsyncMock(side_effect=HTTPException(401, "synthetic invalid code")),
    )
    rejected = _complete_form(client)

    assert rejected.status_code == 303
    assert rejected.headers["location"] == ERROR_LOCATION
    assert capsule_key in storage
    assert not getdel_calls
    assert put.await_count == 0
    assert not any(
        cookie.startswith((bff.access_cookie_name() + "=", bff.refresh_cookie_name() + "="))
        for cookie in _cookie_headers(rejected)
    )
    repo.get_or_create_organization_with_owner.assert_not_awaited()


def test_lost_completion_marker_write_fails_closed_without_reissuing_session(
    signup_client, monkeypatch
):
    from src.core.auth import bff as auth_bff
    from src.core.auth import register

    client, post, put, repo = signup_client
    _begin_signup(client, post)
    _stage_code(client)
    monkeypatch.setattr(auth_bff, "exchange_pkce", AsyncMock(return_value=_session_payload()))
    monkeypatch.setattr(register, "_get_supabase_user", AsyncMock(return_value=_confirmed_remote_user()))
    original_write = register._write_completion_marker

    async def write_then_lose_response(state):
        await original_write(state)
        raise RuntimeError("synthetic marker response lost")

    monkeypatch.setattr(register, "_write_completion_marker", write_then_lose_response)
    first = _complete_form(client)

    assert first.status_code == 303
    assert first.headers["location"] == CALLBACK
    assert put.await_count == 1
    assert not any(
        cookie.startswith((bff.access_cookie_name() + "=", bff.refresh_cookie_name() + "="))
        for cookie in _cookie_headers(first)
    )
    assert register._completion_marker_memory

    retried = _complete_form(client)

    assert retried.status_code == 303
    assert retried.headers["location"] == "/accedi/?conferma=ok"
    assert put.await_count == 1
    assert not any(
        cookie.startswith((bff.access_cookie_name() + "=", bff.refresh_cookie_name() + "="))
        for cookie in _cookie_headers(retried)
    )
    repo.get_or_create_organization_with_owner.assert_awaited_once_with(
        USER_ID, BUSINESS_NAME, pytest.approx(7)
    )


@pytest.mark.parametrize("transient_failure", ["timeout", "server_error"])
def test_transient_identity_revalidation_reissues_retry_state_before_password_update(
    signup_client, monkeypatch, transient_failure
):
    from src.core.auth import bff as auth_bff
    from src.core.auth import register

    client, post, put, repo = signup_client
    _begin_signup(client, post)
    verifier = client.cookies.get("wa_signup_verifier")
    _stage_code(client)
    exchange = AsyncMock(return_value=_session_payload())
    monkeypatch.setattr(auth_bff, "exchange_pkce", exchange)
    put.side_effect = [RuntimeError("synthetic setup failure"), _response(200)]

    first = _complete_form(client)
    assert first.headers["location"] == CALLBACK
    first_retry_id = client.cookies.get("wa_signup_pending")

    temporary_error = (
        httpx.ReadTimeout("synthetic timeout")
        if transient_failure == "timeout"
        else _response(503, {"detail": "synthetic unavailable"})
    )
    identity_response = SimpleNamespace(
        get=AsyncMock(side_effect=[temporary_error, _response(200, _confirmed_remote_user())]),
        put=put,
    )
    monkeypatch.setattr(
        register.bff, "_client", AsyncMock(return_value=identity_response)
    )

    deferred = _complete_form(client)

    assert deferred.status_code == 303
    assert deferred.headers["location"] == CALLBACK
    second_retry_id = client.cookies.get("wa_signup_pending")
    assert second_retry_id == first_retry_id
    assert put.await_count == 1
    assert not any(
        cookie.startswith((bff.access_cookie_name() + "=", bff.refresh_cookie_name() + "="))
        for cookie in _cookie_headers(deferred)
    )

    completed = _complete_form(client)

    assert completed.status_code == 303
    assert completed.headers["location"] == SUCCESS_LOCATION
    assert identity_response.get.await_count == 2
    assert put.await_count == 2
    exchange.assert_awaited_once_with(CODE, verifier)
    repo.get_or_create_organization_with_owner.assert_awaited_once()


def test_production_pending_retry_uses_retained_encrypted_redis_state_until_marker(
    signup_client, monkeypatch, caplog
):
    from redis.asyncio import Redis

    from src.core.auth import bff as auth_bff
    from src.core.auth import register

    client, post, put, repo = signup_client
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("REDIS_URL", raising=False)
    encryption_key = Fernet.generate_key()
    monkeypatch.setenv("ENCRYPTION_KEY", encryption_key.decode("ascii"))
    clock = [1_800_000.0]
    monkeypatch.setattr(register, "_pending_now", lambda: clock[0])

    storage = {}
    set_calls = []
    getdel_calls = []
    delete_calls = []
    claim_release_calls = []
    urls = []

    class FakeRedis:
        def __init__(self):
            self.storage = storage
            self.set_calls = set_calls
            self.getdel_calls = getdel_calls

        async def set(self, key, value, *, ex, nx):
            self.set_calls.append((key, value, ex, nx))
            if nx and key in self.storage:
                return False
            self.storage[key] = value
            return True

        async def get(self, key):
            return self.storage.get(key)

        async def delete(self, key):
            delete_calls.append(key)
            return int(self.storage.pop(key, None) is not None)

        async def getdel(self, key):
            self.getdel_calls.append(key)
            return self.storage.pop(key, None)

        async def eval(self, _script, _numkeys, key, token):
            claim_release_calls.append((key, token))
            if self.storage.get(key) != token:
                return 0
            self.storage.pop(key)
            return 1

        async def aclose(self):
            return None

    def fake_from_url(url, *, decode_responses):
        urls.append((url, decode_responses))
        return FakeRedis()

    monkeypatch.setattr(Redis, "from_url", staticmethod(fake_from_url))
    _begin_signup(client, post)
    initial_capsule_id = client.cookies.get("wa_signup_capsule")
    assert initial_capsule_id
    _stage_code(client)
    exchange = AsyncMock(return_value=_session_payload())
    monkeypatch.setattr(auth_bff, "exchange_pkce", exchange)
    identity_check = AsyncMock(return_value=_confirmed_remote_user())
    monkeypatch.setattr(register, "_get_supabase_user", identity_check)
    put.side_effect = [
        RuntimeError("synthetic setup failure"),
        RuntimeError("synthetic retry failure"),
        _response(200),
    ]

    failed = _complete_form(client)

    assert failed.status_code == 303
    assert failed.headers["location"] == CALLBACK
    pending_id = client.cookies.get("wa_signup_pending")
    pending_cookie = _cookie_header(failed, "wa_signup_pending")
    assert pending_id and ACCESS_TOKEN not in pending_id and REFRESH_TOKEN not in pending_id
    _assert_host_only_cookie(pending_cookie)
    assert "Max-Age=300" in pending_cookie
    assert urls and all(call == ("redis://valkey:6379/0", True) for call in urls)
    capsule_sets = [
        call for call in set_calls if call[0].startswith(register._PASSWORD_CAPSULE_PREFIX)
    ]
    pending_sets = [call for call in set_calls if call[0].startswith(register._PENDING_PREFIX)]
    assert len(capsule_sets) == 1
    assert len(pending_sets) == 2
    claim_sets = [call for call in set_calls if call[0].startswith(register._CALLBACK_CLAIM_PREFIX)]
    assert len(claim_sets) == 1
    claim_key, _claim_token, claim_ttl, claim_nx = claim_sets[0]
    assert claim_key.endswith(client.cookies.get("wa_signup_flow"))
    assert claim_ttl == register._CALLBACK_CLAIM_TTL_SECONDS and claim_nx is True
    assert claim_key not in storage
    assert claim_release_calls == [(claim_key, _claim_token)]
    initial_capsule_key, initial_capsule_ciphertext, initial_capsule_ttl, initial_capsule_nx = capsule_sets[0]
    assert initial_capsule_key.endswith(initial_capsule_id)
    assert 0 < initial_capsule_ttl <= register._PASSWORD_CAPSULE_TTL_SECONDS
    assert initial_capsule_nx is True
    assert FINAL_PASSWORD not in initial_capsule_ciphertext
    key, ciphertext, ttl, nx = pending_sets[0]
    assert key.endswith(pending_id)
    assert ttl == 300 and nx is True
    assert pending_sets[1][0] == key
    assert pending_sets[1][2:] == (300, True)
    assert ACCESS_TOKEN not in ciphertext and REFRESH_TOKEN not in ciphertext
    decrypted = Fernet(encryption_key).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    assert ACCESS_TOKEN in decrypted and REFRESH_TOKEN in decrypted
    assert json.loads(decrypted)["expires_at"] == clock[0] + 300
    _assert_no_auth_secrets(failed, caplog)

    clock[0] += 120
    failed_retry = _complete_form(client)

    assert failed_retry.status_code == 303
    assert failed_retry.headers["location"] == CALLBACK
    second_pending_id = client.cookies.get("wa_signup_pending")
    second_pending_cookie = _cookie_header(failed_retry, "wa_signup_pending")
    assert second_pending_id == pending_id
    assert "Max-Age=180" in second_pending_cookie
    assert put.await_count == 2
    flow_cookies = dict(client.cookies)
    capsule_sets = [
        call for call in set_calls if call[0].startswith(register._PASSWORD_CAPSULE_PREFIX)
    ]
    pending_sets = [call for call in set_calls if call[0].startswith(register._PENDING_PREFIX)]
    assert len(capsule_sets) == 1
    assert len(pending_sets) == 3
    claim_sets = [call for call in set_calls if call[0].startswith(register._CALLBACK_CLAIM_PREFIX)]
    assert len(claim_sets) == 2
    assert len(claim_release_calls) == 2
    assert pending_sets[0][0] == pending_sets[1][0] == pending_sets[2][0] == key
    assert pending_sets[2][2:] == (180, True)

    clock[0] += 60
    flow_id = client.cookies.get("wa_signup_flow")
    flow_cookies = dict(client.cookies)
    completed = _complete_form(client)

    assert completed.status_code == 303
    assert completed.headers["location"] == SUCCESS_LOCATION
    assert not [item for item in getdel_calls if item.startswith(register._PENDING_PREFIX)]
    assert getdel_calls == [register._PASSWORD_CAPSULE_PREFIX + initial_capsule_id]
    assert delete_calls == [key]
    assert key not in storage
    marker_key = register._COMPLETION_PREFIX + register._pending_id_for_flow(flow_id)
    assert marker_key in storage
    assert FINAL_PASSWORD not in storage[marker_key]
    assert put.await_count == 3
    assert identity_check.await_count == 2
    claim_sets = [call for call in set_calls if call[0].startswith(register._CALLBACK_CLAIM_PREFIX)]
    assert len(claim_sets) == 3
    assert len(claim_release_calls) == 3
    assert all(claim[0] not in storage for claim in claim_sets)
    repo.get_or_create_organization_with_owner.assert_awaited_once()
    _assert_no_auth_secrets(completed, caplog)

    pending_delete_before_replays = list(delete_calls)
    for replay_id in (pending_id, second_pending_id):
        replay = TestClient(client.app, base_url="http://api-internal:8000", follow_redirects=False)
        for name, value in flow_cookies.items():
            replay.cookies.set(name, value)
        replay.cookies.set("wa_signup_pending", replay_id)
        rejected = _complete_form(replay)
        assert rejected.headers["location"] == "/accedi/?conferma=ok"
    assert delete_calls == pending_delete_before_replays + [key, key]
    assert getdel_calls == [
        register._PASSWORD_CAPSULE_PREFIX + initial_capsule_id
    ] * 3
    assert put.await_count == 3


def test_staging_memory_pending_store_evicts_expired_entries_on_reads(
    signup_client, monkeypatch
):
    from src.core.auth import register

    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("REDIS_URL", "")
    register._pending_memory.clear()
    register._pending_memory["expired-retry-id"] = (time.monotonic() - 1, {})

    result = asyncio.run(register._read_pending_session("missing-retry-id"))

    assert result is None
    assert "expired-retry-id" not in register._pending_memory


def test_expired_pending_signup_state_cannot_be_consumed_or_reissued(
    signup_client, monkeypatch
):
    from src.core.auth import bff as auth_bff
    from src.core.auth import register

    client, post, put, repo = signup_client
    clock = [1_900_000.0]
    monkeypatch.setattr(register, "_pending_now", lambda: clock[0])
    _begin_signup(client, post)
    _stage_code(client)
    exchange = AsyncMock(return_value=_session_payload())
    monkeypatch.setattr(auth_bff, "exchange_pkce", exchange)
    identity_check = AsyncMock(return_value=_confirmed_remote_user())
    monkeypatch.setattr(register, "_get_supabase_user", identity_check)
    put.side_effect = RuntimeError("synthetic setup failure")

    first = _complete_form(client)
    pending_id = client.cookies.get("wa_signup_pending")
    assert first.headers["location"] == CALLBACK
    assert pending_id in register._pending_memory

    clock[0] += 301
    expired = _complete_form(client)

    assert expired.status_code == 303
    assert expired.headers["location"] == ERROR_LOCATION
    assert pending_id not in register._pending_memory
    assert identity_check.await_count == 0
    assert put.await_count == 1
    repo.get_or_create_organization_with_owner.assert_not_awaited()
    assert "Max-Age=0" in _cookie_header(expired, "wa_signup_pending")
    assert not any(
        cookie.startswith((bff.access_cookie_name() + "=", bff.refresh_cookie_name() + "="))
        for cookie in _cookie_headers(expired)
    )


def test_pending_password_retry_still_requires_configured_same_origin(signup_client, monkeypatch):
    from src.core.auth import bff as auth_bff

    client, post, put, _repo = signup_client
    _begin_signup(client, post)
    verifier = client.cookies.get("wa_signup_verifier")
    _stage_code(client)
    exchange = AsyncMock(return_value=_session_payload())
    monkeypatch.setattr(auth_bff, "exchange_pkce", exchange)
    monkeypatch.setattr(
        "src.core.auth.register._get_supabase_user",
        AsyncMock(return_value=_confirmed_remote_user()),
    )
    put.side_effect = [RuntimeError("synthetic setup failure"), _response(200)]
    first = _complete_form(client)
    assert first.headers["location"] == CALLBACK
    pending_id = client.cookies.get("wa_signup_pending")

    other_origin = TestClient(client.app, base_url="http://api-internal:8000", follow_redirects=False)
    _copy_flow_cookies(client, other_origin)
    other_origin.cookies.set("wa_signup_pending", pending_id)
    rejected = _complete_form(other_origin, origin="https://attacker.test")

    assert rejected.status_code == 303
    assert rejected.headers["location"] == ERROR_LOCATION
    assert put.await_count == 1
    assert not any(
        cookie.startswith((bff.access_cookie_name() + "=", bff.refresh_cookie_name() + "="))
        for cookie in _cookie_headers(rejected)
    )

    retried = _complete_form(client)
    assert retried.headers["location"] == SUCCESS_LOCATION
    exchange.assert_awaited_once_with(CODE, verifier)
    assert put.await_count == 2


def test_pending_password_retry_rejects_email_cookie_mismatch(signup_client, monkeypatch):
    from src.core.auth import bff as auth_bff

    client, post, put, _repo = signup_client
    _begin_signup(client, post)
    _stage_code(client)
    exchange = AsyncMock(return_value=_session_payload())
    monkeypatch.setattr(auth_bff, "exchange_pkce", exchange)
    put.side_effect = RuntimeError("synthetic setup failure")
    first = _complete_form(client)
    pending_id = client.cookies.get("wa_signup_pending")

    other_browser = TestClient(client.app, base_url="http://api-internal:8000", follow_redirects=False)
    _copy_flow_cookies(client, other_browser)
    other_browser.cookies.set("wa_signup_pending", pending_id)
    other_browser.cookies.set("wa_signup_email", sha256(b"different@example.test").hexdigest())
    rejected = _complete_form(other_browser)

    assert first.headers["location"] == CALLBACK
    assert rejected.status_code == 303
    assert rejected.headers["location"] == ERROR_LOCATION
    assert put.await_count == 1
    assert not any(
        cookie.startswith((bff.access_cookie_name() + "=", bff.refresh_cookie_name() + "="))
        for cookie in _cookie_headers(rejected)
    )


def test_pending_password_retry_revalidates_confirmed_supabase_identity(signup_client, monkeypatch):
    from src.core.auth import bff as auth_bff

    client, post, put, repo = signup_client
    _begin_signup(client, post)
    _stage_code(client)
    exchange = AsyncMock(return_value=_session_payload())
    monkeypatch.setattr(auth_bff, "exchange_pkce", exchange)
    put.side_effect = RuntimeError("synthetic setup failure")
    first = _complete_form(client)
    assert first.headers["location"] == CALLBACK

    identity_check = AsyncMock(
        return_value=_confirmed_remote_user(email="changed@example.test")
    )
    monkeypatch.setattr("src.core.auth.register._get_supabase_user", identity_check)
    rejected = _complete_form(client)

    assert rejected.status_code == 303
    assert rejected.headers["location"] == ERROR_LOCATION
    identity_check.assert_awaited_once_with(ACCESS_TOKEN)
    assert put.await_count == 1
    repo.get_or_create_organization_with_owner.assert_not_awaited()
    assert not any(
        cookie.startswith((bff.access_cookie_name() + "=", bff.refresh_cookie_name() + "="))
        for cookie in _cookie_headers(rejected)
    )


def test_callback_rejects_legacy_password_body_without_setting_or_echoing_it(signup_client, monkeypatch):
    from src.core.auth import bff as auth_bff

    client, post, put, repo = signup_client
    _begin_signup(client, post)
    _stage_code(client)
    exchange = AsyncMock(return_value=_session_payload())
    monkeypatch.setattr(auth_bff, "exchange_pkce", exchange)

    response = _complete_form(client, password="short", confirm="different")

    assert response.status_code == 303
    assert response.headers["location"] == ERROR_LOCATION
    assert "short" not in response.text
    assert "different" not in response.text
    assert "Max-Age=0" in _cookie_header(response, "wa_signup_code")
    exchange.assert_not_awaited()
    put.assert_not_awaited()
    repo.get_or_create_organization_with_owner.assert_not_awaited()


@pytest.mark.parametrize(
    "payload",
    [
        _session_payload(email="other@example.test"),
        _session_payload(confirmed=False),
        {"access_token": ACCESS_TOKEN, "user": {"id": USER_ID, "email": EMAIL, "email_confirmed_at": "2026-09-30"}},
        {"access_token": ACCESS_TOKEN, "refresh_token": REFRESH_TOKEN, "user": {"id": "invalid-id", "email": EMAIL, "email_confirmed_at": "2026-09-30"}},
    ],
)
def test_mismatched_unconfirmed_or_incomplete_session_fails_closed(signup_client, monkeypatch, payload):
    from src.core.auth import bff as auth_bff

    client, post, put, repo = signup_client
    _begin_signup(client, post)
    _stage_code(client)
    monkeypatch.setattr(auth_bff, "exchange_pkce", AsyncMock(return_value=payload))

    response = _complete_form(client)

    assert response.status_code == 303
    assert response.headers["location"] == ERROR_LOCATION
    assert put.await_count == 0
    repo.get_or_create_organization_with_owner.assert_not_awaited()
    assert not any(cookie.startswith((bff.access_cookie_name() + "=", bff.refresh_cookie_name() + "=", csrf_cookie_name() + "=")) for cookie in _cookie_headers(response))
    assert "Max-Age=0" in _cookie_header(response, "wa_signup_code")


def test_cross_browser_callback_without_verifier_fails_before_staging(signup_client):
    client, post, _put, _repo = signup_client
    _begin_signup(client, post)
    other_browser = TestClient(client.app, base_url="http://api-internal:8000", follow_redirects=False)

    response = other_browser.get(f"{CALLBACK}?code={CODE}")

    assert response.status_code == 303
    assert response.headers["location"] == ERROR_LOCATION
    assert not any(cookie.startswith(f"wa_signup_code={CODE};") for cookie in _cookie_headers(response))
    assert post.await_count == 1


@pytest.mark.parametrize("cookie_name", [bff.ACCESS_COOKIE, bff.REFRESH_COOKIE])
def test_existing_session_cookie_is_never_overwritten_by_signup_callback(signup_client, cookie_name, monkeypatch):
    from src.core.auth import bff as auth_bff

    client, post, _put, _repo = signup_client
    _begin_signup(client, post)
    existing_value = f"existing-{cookie_name}"
    client.cookies.set(cookie_name, existing_value)
    exchange = AsyncMock(return_value=_session_payload())
    monkeypatch.setattr(auth_bff, "exchange_pkce", exchange)

    response = client.get(f"{CALLBACK}?code={CODE}")

    assert response.status_code == 303
    assert response.headers["location"] == ERROR_LOCATION
    exchange.assert_not_awaited()
    assert client.cookies.get(cookie_name) == existing_value
    assert not any(cookie.startswith(f"{cookie_name}=") for cookie in _cookie_headers(response))


def test_expired_or_replayed_pkce_code_clears_flow_and_logs_only_trace_id(signup_client, monkeypatch, caplog):
    from src.core.auth import bff as auth_bff

    client, post, _put, _repo = signup_client
    _begin_signup(client, post)
    _stage_code(client)
    exchange = AsyncMock(
        side_effect=HTTPException(
            401,
            f"{CODE} {VERIFIER} {ACCESS_TOKEN} {REFRESH_TOKEN} {FINAL_PASSWORD}"
        )
    )
    monkeypatch.setattr(auth_bff, "exchange_pkce", exchange)

    expired = _complete_form(client)
    replay = _complete_form(client)

    assert expired.headers["location"] == ERROR_LOCATION
    assert replay.headers["location"] == ERROR_LOCATION
    assert exchange.await_count == 1
    assert "Max-Age=0" in _cookie_header(expired, "wa_signup_code")
    assert "Max-Age=0" in _cookie_header(replay, "wa_signup_code")
    _assert_no_auth_secrets(expired, caplog)
    assert "trace_id=synthetic-trace-id" in caplog.text


def test_callback_csrf_exemption_is_exact_and_post_uses_configured_origin(signup_client, monkeypatch):
    from src.core.auth import bff as auth_bff

    client, post, _put, _repo = signup_client
    _begin_signup(client, post)
    _stage_code(client)
    exchange = AsyncMock(return_value=_session_payload())
    monkeypatch.setattr(auth_bff, "exchange_pkce", exchange)

    assert CALLBACK in CSRF_EXEMPT_PATHS
    assert "/api/auth/confirm" not in CSRF_EXEMPT_PATHS
    response = _complete_form(client, origin=PUBLIC_APP_URL)

    assert response.status_code == 303
    assert response.headers["location"] == SUCCESS_LOCATION


def test_https_uses_host_prefixed_secure_flow_and_session_cookies(monkeypatch):
    from src.core.auth import bff as auth_bff
    from src.core.auth import register

    monkeypatch.setenv("SUPABASE_URL", SUPABASE_URL)
    monkeypatch.setenv("SUPABASE_ANON_KEY", ANON_KEY)
    monkeypatch.setenv("PUBLIC_APP_URL", "https://staging.test")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("MELPIS_BETA_ALLOWED_EMAILS", EMAIL)
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    post = AsyncMock(return_value=_response(200, {"user": {"id": USER_ID}}))
    put = AsyncMock(return_value=_response(200, {"id": USER_ID}))
    monkeypatch.setattr(register.bff, "_client", AsyncMock(return_value=SimpleNamespace(post=post, put=put)))
    monkeypatch.setattr(register.throttle, "is_throttled", AsyncMock(return_value=False))
    monkeypatch.setattr(register.throttle, "record_event", AsyncMock())
    app = FastAPI()
    app.state.repo = SimpleNamespace(
        get_auth_access_allowed=AsyncMock(return_value=True),
        get_or_create_organization_with_owner=AsyncMock(return_value={"organization_id": USER_ID})
    )
    app.include_router(register_router)
    client = TestClient(app, base_url="https://staging.test", follow_redirects=False)

    created = client.post(
        "/api/auth/register",
        json={"email": EMAIL, "nome_attivita": BUSINESS_NAME, "password": FINAL_PASSWORD},
        headers={"Origin": "https://staging.test"},
    )
    assert created.status_code == 202
    for base_name in ("wa_signup_verifier", "wa_signup_email"):
        cookie = _cookie_header(created, f"__Host-{base_name}")
        _assert_host_only_cookie(cookie, secure=True)

    staged = client.get(f"{CALLBACK}?code={CODE}")
    assert staged.status_code == 303
    assert staged.headers["location"] == CALLBACK
    _assert_host_only_cookie(_cookie_header(staged, "__Host-wa_signup_code"), secure=True)
    monkeypatch.setattr(auth_bff, "exchange_pkce", AsyncMock(return_value=_session_payload()))

    completed = _complete_form(client, origin="https://staging.test")

    assert completed.status_code == 303
    assert completed.headers["location"] == SUCCESS_LOCATION
    for cookie_name in ("__Host-wa_at", "__Host-wa_rt", "__Host-wa_csrf"):
        cookie = _cookie_header(completed, cookie_name)
        assert "Secure" in cookie and "Path=/" in cookie and "Domain=" not in cookie
    assert "HttpOnly" in _cookie_header(completed, "__Host-wa_at")
    assert "HttpOnly" in _cookie_header(completed, "__Host-wa_rt")
    assert "HttpOnly" not in _cookie_header(completed, "__Host-wa_csrf")
