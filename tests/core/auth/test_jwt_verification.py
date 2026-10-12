"""Test verifica JWT Supabase (verify_supabase_jwt).

Contratto:
- token firmati ES256 (chiavi asimmetriche, default Supabase 2025+) e RS256
  (progetti legacy) sono entrambi accettati;
- l'algoritmo è pinnato al campo "alg" del JWKS: un token firmato con una
  chiave estranea o con alg non supportato è rifiutato fail-closed (403);
- iss e aud restano verificati.
"""

import base64
import hashlib
import hmac
import json
import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import jwt
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives import serialization
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

from src.core.auth import dependencies as deps

SUPABASE_URL = "https://myproj.supabase.co"
ISS = f"{SUPABASE_URL}/auth/v1"
AUD = "authenticated"

pytestmark = pytest.mark.asyncio


def _make_jwk(private_key, alg: str) -> dict:
    public_key = private_key.public_key()
    jwk = json.loads(
        (RSAAlgorithm.to_jwk(public_key) if alg == "RS256" else ECAlgorithm.to_jwk(public_key))
    )
    jwk["kid"] = str(uuid.uuid4())
    jwk["alg"] = alg
    return jwk


def _make_token(private_key, alg: str, *, iss=ISS, aud=AUD, sub="user-1", exp_offset=600,
                extra_claims=None, kid=None) -> str:
    now = int(time.time())
    claims = {
        "sub": sub,
        "aud": aud,
        "iss": iss,
        "iat": now,
        "exp": now + exp_offset,
        "role": "authenticated",
    }
    if extra_claims:
        claims.update(extra_claims)
    headers = {"kid": kid} if kid is not None else {}
    return jwt.encode(claims, private_key, algorithm=alg, headers=headers)


def _ec_key():
    return ec.generate_private_key(ec.SECP256R1())


def _rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", SUPABASE_URL)
    monkeypatch.setenv("SUPABASE_JWT_AUD", AUD)
    monkeypatch.setattr(deps, "JWKS_CACHE", {"keys": None, "expires_at": 0})


@pytest.fixture
def patch_jwks(monkeypatch):
    async def _install(keys):
        async def fake():
            return keys

        monkeypatch.setattr(deps, "_get_supabase_jwks", fake)

    return _install


async def test_token_es256_accettato(patch_jwks):
    key = _ec_key()
    jwk = _make_jwk(key, "ES256")
    await patch_jwks([jwk])
    payload = await deps.verify_supabase_jwt(_make_token(key, "ES256", kid=jwk["kid"]))
    assert payload["sub"] == "user-1"


async def test_token_rs256_legacy_accettato(patch_jwks):
    key = _rsa_key()
    jwk = _make_jwk(key, "RS256")
    await patch_jwks([jwk])
    payload = await deps.verify_supabase_jwt(_make_token(key, "RS256", kid=jwk["kid"]))
    assert payload["sub"] == "user-1"


@pytest.mark.parametrize("alg,key_factory", [("RS256", _rsa_key), ("ES256", _ec_key)])
async def test_kid_assente_accettato_solo_con_una_chiave( patch_jwks, alg, key_factory):
    key = key_factory()
    jwk = _make_jwk(key, alg)
    await patch_jwks([jwk])
    assert (await deps.verify_supabase_jwt(_make_token(key, alg)))["sub"] == "user-1"
    await patch_jwks([jwk, _make_jwk(key_factory(), alg)])
    with pytest.raises(deps.HTTPException) as exc:
        await deps.verify_supabase_jwt(_make_token(key, alg))
    assert exc.value.status_code == 403


async def test_kid_sconosciuto_e_duplicato_rifiutati(patch_jwks):
    key = _ec_key()
    jwk = _make_jwk(key, "ES256")
    await patch_jwks([jwk])
    with pytest.raises(deps.HTTPException):
        await deps.verify_supabase_jwt(_make_token(key, "ES256", kid="unknown"))
    await patch_jwks([jwk, dict(jwk)])
    with pytest.raises(deps.HTTPException):
        await deps.verify_supabase_jwt(_make_token(key, "ES256", kid=jwk["kid"]))


@pytest.mark.parametrize("extra_claims", [
    {"exp": None}, {"exp": True}, {"exp": "9999999999"}, {"exp": float("inf")},
    {"exp": 10**400}, {"iat": True}, {"iat": float("nan")}, {"iat": 10**400},
    {"nbf": "1"}, {"nbf": None}, {"sub": ""},
    {"iat": int(time.time()) + 3600}, {"nbf": int(time.time()) + 3600},
    {"aud": "wrong"},
])
async def test_claim_temporali_malformati_o_non_validi_negati_anche_in_refresh(
    patch_jwks, extra_claims
):
    key = _ec_key()
    jwk = _make_jwk(key, "ES256")
    await patch_jwks([jwk])
    token = _make_token(key, "ES256", exp_offset=-1, extra_claims=extra_claims, kid=jwk["kid"])
    with pytest.raises(deps.HTTPException) as exc:
        await deps.verify_supabase_jwt(token, allow_expired=True)
    assert exc.value.status_code == 403


@pytest.mark.parametrize("missing", ["exp", "iat", "iss", "aud", "sub"])
async def test_claim_obbligatorio_mancante_rifiutato(patch_jwks, missing):
    key = _ec_key()
    jwk = _make_jwk(key, "ES256")
    await patch_jwks([jwk])
    claims = {"sub": "user-1", "aud": AUD, "iss": ISS,
              "iat": int(time.time()), "exp": int(time.time()) + 600}
    del claims[missing]
    token = jwt.encode(claims, key, algorithm="ES256", headers={"kid": jwk["kid"]})
    with pytest.raises(deps.HTTPException) as exc:
        await deps.verify_supabase_jwt(token, allow_expired=True)
    assert exc.value.status_code == 403


@pytest.mark.parametrize("alg", ["HS256", "none"])
async def test_algoritmo_non_autorizzato_rifiutato(patch_jwks, alg):
    key = _ec_key()
    await patch_jwks([_make_jwk(key, "ES256")])
    if alg == "HS256":
        token = jwt.encode(
            {"sub": "user-1", "aud": AUD, "iss": ISS,
             "iat": int(time.time()), "exp": int(time.time()) + 600},
            "a-secret-that-is-at-least-thirty-two-bytes-long", algorithm=alg
        )
    else:
        token = jwt.encode({"sub": "user-1", "aud": AUD, "iss": ISS,
                            "iat": int(time.time()), "exp": int(time.time()) + 600},
                           key=None, algorithm=alg)
    with pytest.raises(deps.HTTPException) as exc:
        await deps.verify_supabase_jwt(token)
    assert exc.value.status_code == 403


@pytest.mark.parametrize("jwk_update", [
    {"alg": "RS256"}, {"use": "enc"}, {"key_ops": ["sign"]}, {"kty": "oct"},
])
async def test_binding_alg_e_usage_jwk_rifiutati_se_incompatibili(patch_jwks, jwk_update):
    key = _ec_key()
    jwk = _make_jwk(key, "ES256")
    jwk.update(jwk_update)
    await patch_jwks([jwk])
    with pytest.raises(deps.HTTPException) as exc:
        await deps.verify_supabase_jwt(_make_token(key, "ES256", kid=jwk["kid"]))
    assert exc.value.status_code == 403


async def test_jwk_malformato_e_kid_header_nonstring_rifiutati(patch_jwks):
    await patch_jwks([{"kid": "k1", "kty": "EC", "crv": "P-256"}])
    with pytest.raises(deps.HTTPException) as exc:
        await deps.verify_supabase_jwt("eyJhbGciOiJFUzI1NiIsImtpZCI6ImsxIn0.eyJzdWIiOiJ4In0.AA")
    assert exc.value.status_code == 403

    def encode(value):
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode()
    token = ".".join((encode(json.dumps({"alg": "ES256", "kid": 123}).encode()),
                      encode(json.dumps({"sub": "user-1"}).encode()), "AA"))
    with pytest.raises(deps.HTTPException) as exc:
        await deps.verify_supabase_jwt(token)
    assert exc.value.status_code == 403


async def test_signature_segment_non_canonico_rifiutato(patch_jwks):
    key = _rsa_key()
    jwk = _make_jwk(key, "RS256")
    await patch_jwks([jwk])
    token = _make_token(key, "RS256", kid=jwk["kid"])
    header, payload, signature = token.split(".")
    assert jwt.decode(f"{token}==", key.public_key(), algorithms=["RS256"],
                      audience=AUD, issuer=ISS)["sub"] == "user-1"
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    index = alphabet.index(signature[-1])
    noncanonical = signature[:-1] + alphabet[(index & 0b110000) | ((index ^ 1) & 0b001111)]
    assert base64.urlsafe_b64decode(noncanonical + "==") == base64.urlsafe_b64decode(signature + "==")
    for invalid in (f"{token}=", f"{token}==", f"{header}.{payload}.{signature}!!!!",
                    f"{header}.{payload}.{noncanonical}"):
        with pytest.raises(deps.HTTPException) as exc:
            await deps.verify_supabase_jwt(invalid, allow_expired=True)
        assert exc.value.status_code == 403


@pytest.mark.parametrize("exp_offset", [600, -60])
@pytest.mark.parametrize("allow_expired", [False, True])
async def test_signature_falsa_rifiutata(patch_jwks, exp_offset, allow_expired):
    key = _rsa_key()
    jwk = _make_jwk(key, "RS256")
    await patch_jwks([jwk])
    header, payload, signature = _make_token(
        key, "RS256", kid=jwk["kid"], exp_offset=exp_offset
    ).split(".")
    signature_bytes = bytearray(base64.urlsafe_b64decode(signature + "=="))
    signature_bytes[0] ^= 1
    forged_signature = base64.urlsafe_b64encode(signature_bytes).rstrip(b"=").decode()
    with pytest.raises(deps.HTTPException) as exc:
        await deps.verify_supabase_jwt(
            f"{header}.{payload}.{forged_signature}", allow_expired=allow_expired
        )
    assert exc.value.status_code == 403


async def test_cache_jwks_ruota_dopo_ttl(monkeypatch):
    now = [1000.0]
    responses = [
        SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"keys": [{"kid": "old"}]}),
        SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"keys": [{"kid": "new"}]}),
    ]
    client = SimpleNamespace(get=AsyncMock(side_effect=responses))
    monkeypatch.setenv("SUPABASE_URL", SUPABASE_URL)
    monkeypatch.setattr(deps, "JWKS_CACHE", {"keys": None, "expires_at": 0})
    monkeypatch.setattr(deps.time, "time", lambda: now[0])
    monkeypatch.setattr(deps, "get_http_client", AsyncMock(return_value=client))

    assert await deps._get_supabase_jwks() == [{"kid": "old"}]
    assert await deps._get_supabase_jwks() == [{"kid": "old"}]
    assert client.get.await_count == 1
    now[0] += 301
    assert await deps._get_supabase_jwks() == [{"kid": "new"}]
    assert client.get.await_count == 2


@pytest.mark.parametrize("alg,key_factory", [("ES256", _ec_key), ("RS256", _rsa_key)])
async def test_token_scaduto_valido_richiede_refresh(patch_jwks, alg, key_factory):
    key = key_factory()
    jwk = _make_jwk(key, alg)
    await patch_jwks([jwk])
    with pytest.raises(deps.HTTPException) as exc:
        await deps.verify_supabase_jwt(_make_token(key, alg, exp_offset=-60, kid=jwk["kid"]))
    assert exc.value.status_code == 401


async def test_token_scaduto_valido_conserva_l_identita_per_refresh(patch_jwks):
    key = _ec_key()
    jwk = _make_jwk(key, "ES256")
    await patch_jwks([jwk])
    payload = await deps.verify_supabase_jwt(
        _make_token(key, "ES256", sub="user-refresh", exp_offset=-60, kid=jwk["kid"]),
        allow_expired=True,
    )
    assert payload["sub"] == "user-refresh"


async def test_token_scaduto_con_issuer_estraneo_resta_rifiutato(patch_jwks):
    key = _ec_key()
    jwk = _make_jwk(key, "ES256")
    await patch_jwks([jwk])
    with pytest.raises(deps.HTTPException) as exc:
        await deps.verify_supabase_jwt(
            _make_token(key, "ES256", iss="https://other.supabase.co/auth/v1", exp_offset=-60, kid=jwk["kid"])
        )
    assert exc.value.status_code == 403


@pytest.mark.parametrize("alg,key_factory", [("RS256", _rsa_key), ("ES256", _ec_key)])
async def test_chiave_senza_campo_alg_accettata(patch_jwks, alg, key_factory):
    key = key_factory()
    jwk = _make_jwk(key, alg)
    jwk.pop("alg")
    await patch_jwks([jwk])
    payload = await deps.verify_supabase_jwt(_make_token(key, alg, kid=jwk["kid"]))
    assert payload["sub"] == "user-1"


async def test_token_firmato_da_chiave_esterna_rifiutato(patch_jwks):
    good = _ec_key()
    evil = _ec_key()
    jwk = _make_jwk(good, "ES256")
    await patch_jwks([jwk])
    with pytest.raises(deps.HTTPException) as exc:
        await deps.verify_supabase_jwt(_make_token(evil, "ES256", kid=jwk["kid"]))
    assert exc.value.status_code == 403


async def test_issuer_diverso_rifiutato(patch_jwks):
    key = _ec_key()
    jwk = _make_jwk(key, "ES256")
    await patch_jwks([jwk])
    with pytest.raises(deps.HTTPException) as exc:
        await deps.verify_supabase_jwt(
            _make_token(key, "ES256", iss="https://evil.supabase.co/auth/v1", kid=jwk["kid"])
        )
    assert exc.value.status_code == 403


async def test_audience_diversa_rifiutata(patch_jwks):
    key = _ec_key()
    jwk = _make_jwk(key, "ES256")
    await patch_jwks([jwk])
    with pytest.raises(deps.HTTPException) as exc:
        await deps.verify_supabase_jwt(_make_token(key, "ES256", aud="other", kid=jwk["kid"]))
    assert exc.value.status_code == 403


async def test_alg_non_supportato_saltato(patch_jwks):
    """Una chiave HS256 nel JWKS non deve mai essere usata per RS/ES token."""
    key = _ec_key()
    hs_jwk = {
        "kty": "oct",
        "k": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "alg": "HS256",
    }
    jwk = _make_jwk(key, "ES256")
    await patch_jwks([hs_jwk, jwk])
    payload = await deps.verify_supabase_jwt(_make_token(key, "ES256", kid=jwk["kid"]))
    assert payload["sub"] == "user-1"


async def test_nessuna_chiave_valida_fail_closed(patch_jwks):
    await patch_jwks([])
    with pytest.raises(deps.HTTPException) as exc:
        await deps.verify_supabase_jwt("qualsiasi.token.qui")
    assert exc.value.status_code == 403


async def test_jwks_malformato_rifiutato(patch_jwks):
    key = _ec_key()
    jwk = _make_jwk(key, "ES256")
    token = _make_token(key, "ES256", kid=jwk["kid"])
    for keys in ([None], [{"kty": []}], [{"kty": {}}]):
        await patch_jwks(keys)
        with pytest.raises(deps.HTTPException) as exc:
            await deps.verify_supabase_jwt(token)
        assert exc.value.status_code == 403


@pytest.mark.parametrize("alg,key_factory", [("RS256", _rsa_key), ("ES256", _ec_key)])
@pytest.mark.parametrize("missing_alg", [False, True])
@pytest.mark.parametrize("allow_expired", [False, True])
@pytest.mark.parametrize("exp_offset", [-600, 600])
async def test_der_public_key_hmac_forgery_rejected(
    patch_jwks, alg, key_factory, missing_alg, allow_expired, exp_offset
):
    """CVE-2026-85394: public DER bytes must never authorize HS256 JWTs."""
    key = key_factory()
    der = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    def encode(value):
        return base64.urlsafe_b64encode(value).rstrip(b"=")

    header = encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = encode(json.dumps({"sub": "forged-test-user", "aud": AUD, "iss": ISS,
                                 "iat": int(time.time()),
                                 "exp": int(time.time()) + exp_offset}).encode())
    signed = header + b"." + payload
    token = (signed + b"." + encode(hmac.new(der, signed, hashlib.sha256).digest())).decode()

    # Raw HMAC control proves the forged segment is authentic under the
    # public DER bytes, while the application still rejects HS256.
    signature = hmac.new(der, signed, hashlib.sha256).digest()
    assert hmac.compare_digest(signature, base64.urlsafe_b64decode(token.rsplit(".", 1)[1] + "=="))
    jwk = _make_jwk(key, alg)
    if missing_alg:
        jwk.pop("alg")
    await patch_jwks([jwk])
    with pytest.raises(deps.HTTPException) as exc:
        await deps.verify_supabase_jwt(token, allow_expired=allow_expired)
    assert exc.value.status_code == 403
