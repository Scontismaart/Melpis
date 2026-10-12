"""Issuer and required-claim checks using real signed tokens."""

import pytest
from fastapi import HTTPException

from src.core.auth import dependencies as deps
from tests.core.auth.test_jwt_verification import (
    ISS, SUPABASE_URL, _make_jwk, _make_token, _ec_key,
)


@pytest.fixture(autouse=True)
def jwt_env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", SUPABASE_URL)
    monkeypatch.setenv("SUPABASE_JWT_AUD", "authenticated")
    monkeypatch.setattr(deps, "JWKS_CACHE", {"keys": None, "expires_at": 0})


@pytest.fixture
def install_jwks(monkeypatch):
    async def install(keys):
        async def get_keys():
            return keys
        monkeypatch.setattr(deps, "_get_supabase_jwks", get_keys)
    return install


@pytest.mark.asyncio
async def test_valid_issuer_accepted_with_real_signature(install_jwks):
    key = _ec_key()
    jwk = _make_jwk(key, "ES256")
    await install_jwks([jwk])
    payload = await deps.verify_supabase_jwt(_make_token(key, "ES256", kid=jwk["kid"]))
    assert payload["iss"] == ISS


@pytest.mark.asyncio
async def test_audience_uses_existing_authenticated_default(monkeypatch, install_jwks):
    monkeypatch.delenv("SUPABASE_JWT_AUD")
    key = _ec_key()
    jwk = _make_jwk(key, "ES256")
    await install_jwks([jwk])
    assert (await deps.verify_supabase_jwt(_make_token(key, "ES256", kid=jwk["kid"]))) ["sub"] == "user-1"


@pytest.mark.asyncio
async def test_wrong_issuer_rejected(install_jwks):
    key = _ec_key()
    jwk = _make_jwk(key, "ES256")
    await install_jwks([jwk])
    with pytest.raises(HTTPException) as exc:
        await deps.verify_supabase_jwt(
            _make_token(key, "ES256", iss="https://other.supabase.co/auth/v1", kid=jwk["kid"])
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_missing_supabase_url_fails_closed(monkeypatch, install_jwks):
    monkeypatch.delenv("SUPABASE_URL")
    await install_jwks([{"kid": "k1"}])
    with pytest.raises(HTTPException) as exc:
        await deps.verify_supabase_jwt("malformed.token.value")
    assert exc.value.status_code == 500
