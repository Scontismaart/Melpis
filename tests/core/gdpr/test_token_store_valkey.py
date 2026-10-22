"""Integration contracts against the disposable local Valkey on port 56381."""
import asyncio
import os
import uuid

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from src.core.gdpr import token_store


VALKEY_URL = os.getenv("GDPR_TEST_VALKEY_URL", "redis://127.0.0.1:56381/15")


@pytest_asyncio.fixture
async def valkey(monkeypatch):
    redis_mod = pytest.importorskip("redis.asyncio")
    client = redis_mod.Redis.from_url(VALKEY_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception:
        await client.aclose()
        if os.getenv('GDPR_TEST_VALKEY_URL'):
            pytest.fail('Required disposable Valkey is unavailable')
        pytest.skip('Optional disposable local Valkey is unavailable')

    prefix = f"gdpr:export:test:{uuid.uuid4().hex}:"
    monkeypatch.setattr(token_store, "_PREFIX", prefix)
    monkeypatch.setattr(token_store, "_redis", client)
    async def _get_client():
        return client
    monkeypatch.setattr(token_store, "_get_redis", _get_client)
    monkeypatch.setenv("REDIS_URL", VALKEY_URL)
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setattr(token_store, "_is_production", lambda: False)
    owned_keys = set()
    original_storage_key = token_store._storage_key

    def tracked_storage_key(token):
        key = original_storage_key(token)
        owned_keys.add(key)
        return key

    monkeypatch.setattr(token_store, "_storage_key", tracked_storage_key)
    try:
        yield client, owned_keys
    finally:
        if owned_keys:
            await client.delete(*owned_keys)
        await client.aclose()


async def save(token="integration-token", data=None):
    await token_store.save_token(
        token,
        "synthetic-org",
        data or {"synthetic": "export"},
        auth_user_id="synthetic-auth-user",
        session_id="synthetic-session",
        user_id="synthetic-profile",
    )


@pytest.mark.asyncio
async def test_twenty_concurrent_consumers_have_one_winner(valkey):
    token = "concurrent-" + uuid.uuid4().hex
    await save(token)
    pending = await token_store.read_token(token)
    assert pending is not None

    results = await asyncio.gather(*(
        token_store.consume_token(token, pending) for _ in range(20)
    ))
    assert results.count({"synthetic": "export"}) == 1
    assert results.count(None) == 19
    assert await token_store.read_token(token) is None


@pytest.mark.asyncio
async def test_expired_redis_key_is_not_read_or_consumed(valkey):
    token = "expiry-" + uuid.uuid4().hex
    await save(token)
    key = token_store._storage_key(token)
    await valkey[0].pexpire(key, 100)
    pending = await token_store.read_token(token)
    if pending is not None:
        await asyncio.sleep(0.12)
        assert await token_store.consume_token(token, pending) is None
    assert await token_store.read_token(token) is None


@pytest.mark.asyncio
async def test_revocation_is_atomic_and_prevents_replay(valkey):
    token = "revoke-" + uuid.uuid4().hex
    await save(token)
    pending = await token_store.read_token(token)
    assert pending is not None

    assert await token_store.revoke_token(token, pending) is True
    assert await token_store.revoke_token(token, pending) is False
    assert await token_store.consume_token(token, pending) is None
    assert await token_store.pop_token(token) is None


@pytest.mark.asyncio
async def test_redis_contains_only_ciphertext_and_hashed_token_key(valkey):
    token = "secret-token-" + uuid.uuid4().hex
    await save(token, {"email": "synthetic-private@example.test"})
    key = token_store._storage_key(token)
    ciphertext = await valkey[0].get(key)

    assert token not in key + ciphertext
    for secret in ("synthetic-private@example.test", "synthetic-auth-user", "synthetic-session"):
        assert secret not in key + ciphertext


@pytest.mark.asyncio
async def test_lost_consume_reply_fails_closed_and_replay_returns_no_data(valkey, monkeypatch):
    token = "lost-reply-" + uuid.uuid4().hex
    await save(token)
    pending = await token_store.read_token(token)
    original_eval = valkey[0].eval

    async def lose_reply(*args, **kwargs):
        await original_eval(*args, **kwargs)
        raise OSError("synthetic lost Valkey reply")

    monkeypatch.setattr(valkey[0], "eval", lose_reply)
    with pytest.raises(token_store.TokenStoreUnavailable):
        await token_store.consume_token(token, pending)
    assert await valkey[0].get(token_store._storage_key(token)) is None
    assert await token_store.read_token(token) is None


@pytest.mark.asyncio
async def test_valkey_outage_fails_closed_without_memory_fallback(monkeypatch):
    class OfflineRedis:
        async def set(self, *_args, **_kwargs):
            raise OSError("synthetic Valkey outage")

    async def _offline_client():
        return OfflineRedis()
    monkeypatch.setattr(token_store, "_get_redis", _offline_client)
    monkeypatch.setenv("REDIS_URL", VALKEY_URL)
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setattr(token_store, "_is_production", lambda: False)
    with pytest.raises(token_store.TokenStoreUnavailable):
        await save("outage-" + uuid.uuid4().hex)
