"""GDPR export-token storage and atomic-consumption contracts."""
import asyncio
import json
import os
import time

import pytest
from cryptography.fernet import Fernet

from src.core.gdpr import token_store


class FakeRedis:
    """A small Redis model: GET is read-only and Lua compare-delete is atomic."""

    def __init__(self):
        self.d = {}
        self._lock = asyncio.Lock()

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.d:
            return None
        self.d[key] = value
        return True

    async def get(self, key):
        return self.d.get(key)

    async def getdel(self, key):
        async with self._lock:
            return self.d.pop(key, None)

    async def eval(self, _script, numkeys, key, expected):
        assert numkeys == 1
        async with self._lock:
            if self.d.get(key) == expected:
                del self.d[key]
                return 1
            return 0


@pytest.fixture(autouse=True)
def _reset_store(monkeypatch):
    monkeypatch.setattr(token_store, "_redis", None)
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setattr(token_store, "_is_production", lambda: False)


def _use(fake, monkeypatch):
    async def _get():
        return fake

    monkeypatch.setenv("REDIS_URL", "redis://redis.test/0")
    monkeypatch.setattr(token_store, "_get_redis", _get)


async def _save(token, org_id="org-1", data=None):
    await token_store.save_token(
        token,
        org_id,
        data or {"safe": True},
        auth_user_id="auth-user-1",
        session_id="session-1",
        user_id="profile-1",
    )


@pytest.mark.asyncio
async def test_token_consumo_one_time(monkeypatch):
    fake = FakeRedis()
    _use(fake, monkeypatch)
    await _save("t1")
    meta = await token_store.pop_token("t1")
    assert meta["org_id"] == "org-1"
    assert meta["auth_user_id"] == "auth-user-1"
    assert meta["session_id"] == "session-1"
    assert meta["role"] == "owner"
    assert await token_store.pop_token("t1") is None


@pytest.mark.asyncio
async def test_redis_contiene_solo_ciphertext_e_chiave_hashata(monkeypatch):
    fake = FakeRedis()
    _use(fake, monkeypatch)
    await _save("secret-download-token", data={"email": "private@example.test"})
    key, encrypted = next(iter(fake.d.items()))
    assert key == token_store._storage_key("secret-download-token")
    assert "secret-download-token" not in key + encrypted
    assert "private@example.test" not in encrypted
    assert "auth-user-1" not in encrypted


@pytest.mark.asyncio
async def test_repr_does_not_expose_data_or_cas_payload(monkeypatch):
    fake = FakeRedis()
    _use(fake, monkeypatch)
    await _save("hidden-payload-token", data={"email": "private@example.test"})
    pending = await token_store.read_token("hidden-payload-token")

    rendered = repr(pending)
    assert "private@example.test" not in rendered
    assert pending.payload not in rendered


@pytest.mark.asyncio
async def test_errore_chiave_non_consuma_token_redis(monkeypatch):
    fake = FakeRedis()
    _use(fake, monkeypatch)
    await _save("key-rotation-token")
    stored_key = token_store._storage_key("key-rotation-token")
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))

    with pytest.raises(token_store.TokenStoreUnavailable):
        await token_store.read_token("key-rotation-token")

    assert stored_key in fake.d


@pytest.mark.asyncio
async def test_cifratura_invalida_non_consuma_token(monkeypatch):
    fake = FakeRedis()
    _use(fake, monkeypatch)
    await _save("key-rotation-memory-token")
    stored_key = token_store._storage_key("key-rotation-memory-token")
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))

    with pytest.raises(token_store.TokenStoreUnavailable):
        await token_store.read_token("key-rotation-memory-token")

    assert stored_key in fake.d


@pytest.mark.asyncio
async def test_token_sconosciuto(monkeypatch):
    _use(FakeRedis(), monkeypatch)
    assert await token_store.pop_token("nope") is None


@pytest.mark.asyncio
async def test_redis_required_in_all_environments(monkeypatch):
    with pytest.raises(token_store.TokenStoreUnavailable):
        await _save("t2", "org-2", {})

    with pytest.raises(token_store.TokenStoreUnavailable):
        await token_store.read_token("t2")


@pytest.mark.asyncio
async def test_production_requires_shared_redis(monkeypatch):
    monkeypatch.setattr(token_store, "_is_production", lambda: True)

    with pytest.raises(token_store.TokenStoreUnavailable):
        await _save("prod-token", "org-3", {})



@pytest.mark.asyncio
async def test_production_redis_write_failure_does_not_fall_back_to_memory(monkeypatch):
    class FailingRedis:
        async def set(self, *_args, **_kwargs):
            raise OSError("synthetic Redis outage")

    _use(FailingRedis(), monkeypatch)
    with pytest.raises(token_store.TokenStoreUnavailable):
        await _save("prod-token", "org-3", {})



@pytest.mark.asyncio
async def test_redis_read_failure_fails_closed_without_fallback(monkeypatch):
    class FailingRedis:
        async def get(self, _key):
            raise OSError("synthetic Redis outage")

    _use(FailingRedis(), monkeypatch)

    with pytest.raises(token_store.TokenStoreUnavailable):
        await token_store.read_token("prod-token")



@pytest.mark.asyncio
async def test_expired_token_is_not_consumed_during_read():
    fake = FakeRedis()
    async def _get():
        return fake
    token_store._get_redis = _get
    cipher = Fernet(os.environ["ENCRYPTION_KEY"].encode())
    fake.d[token_store._storage_key("expired-token")] = cipher.encrypt(json.dumps({
        "org_id": "org-3",
        "auth_user_id": "auth-user-1",
        "session_id": "session-1",
        "user_id": "profile-1",
        "role": "owner",
        "data": {},
        "expires": time.time() - 1,
    }).encode()).decode()

    assert await token_store.read_token("expired-token") is None
    assert token_store._storage_key("expired-token") in fake.d


@pytest.mark.asyncio
@pytest.mark.parametrize("expires", [float("nan"), float("inf"), float("-inf"), True])
async def test_non_finite_or_boolean_expiry_is_rejected(expires, monkeypatch):
    fake = FakeRedis()
    _use(fake, monkeypatch)
    cipher = Fernet(os.environ["ENCRYPTION_KEY"].encode())
    document = {
        "org_id": "org-1",
        "auth_user_id": "auth-user-1",
        "session_id": "session-1",
        "user_id": "profile-1",
        "role": "owner",
        "data": {},
        "expires": expires,
    }
    fake.d[token_store._storage_key("bad-expiry")] = cipher.encrypt(
        json.dumps(document).encode()
    ).decode()

    with pytest.raises(token_store.TokenStoreUnavailable):
        await token_store.read_token("bad-expiry")


@pytest.mark.asyncio
async def test_cifratura_obbligatoria_senza_chiave(monkeypatch):
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    with pytest.raises(token_store.TokenStoreUnavailable):
        await _save("t4", "org-4", {"sensitive": True})


@pytest.mark.asyncio
async def test_two_concurrent_consumers_have_exactly_one_winner(monkeypatch):
    fake = FakeRedis()
    _use(fake, monkeypatch)
    await _save("concurrent-token")
    first, second = await asyncio.gather(
        token_store.read_token("concurrent-token"),
        token_store.read_token("concurrent-token"),
    )

    results = await asyncio.gather(
        token_store.consume_token("concurrent-token", first),
        token_store.consume_token("concurrent-token", second),
    )

    assert results.count({"safe": True}) == 1
    assert results.count(None) == 1
    assert token_store._storage_key("concurrent-token") not in fake.d


@pytest.mark.asyncio
async def test_redis_read_error_keeps_token_and_fails_closed(monkeypatch):
    class FailingReadRedis(FakeRedis):
        async def get(self, _key):
            raise OSError("synthetic Redis read failure")

    fake = FailingReadRedis()
    _use(fake, monkeypatch)
    await _save("read-error-token")
    stored_key = token_store._storage_key("read-error-token")

    with pytest.raises(token_store.TokenStoreUnavailable):
        await token_store.read_token("read-error-token")

    assert stored_key in fake.d


@pytest.mark.asyncio
async def test_redis_atomic_consume_error_keeps_token_and_fails_closed(monkeypatch):
    class FailingEvalRedis(FakeRedis):
        async def eval(self, *_args):
            raise OSError("synthetic Redis script failure")

    fake = FailingEvalRedis()
    _use(fake, monkeypatch)
    await _save("consume-error-token")
    pending = await token_store.read_token("consume-error-token")
    stored_key = token_store._storage_key("consume-error-token")

    with pytest.raises(token_store.TokenStoreUnavailable):
        await token_store.consume_token("consume-error-token", pending)

    assert stored_key in fake.d


@pytest.mark.asyncio
async def test_redis_reply_loss_after_atomic_delete_is_reported_without_data(monkeypatch):
    class LostReplyRedis(FakeRedis):
        async def eval(self, _script, numkeys, key, expected):
            await super().eval(_script, numkeys, key, expected)
            raise OSError("synthetic lost Redis reply")

    fake = LostReplyRedis()
    _use(fake, monkeypatch)
    await _save("ambiguous-consume-token")
    pending = await token_store.read_token("ambiguous-consume-token")

    with pytest.raises(token_store.TokenStoreUnavailable):
        await token_store.consume_token("ambiguous-consume-token", pending)

    assert token_store._storage_key("ambiguous-consume-token") not in fake.d
