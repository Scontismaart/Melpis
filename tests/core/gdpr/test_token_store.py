"""Bloccante B3: i token di export GDPR vivono in Redis con TTL 15 min."""
import json
import os
import time

import pytest
from cryptography.fernet import Fernet
from src.core.gdpr import token_store


class FakeRedis:
    def __init__(self):
        self.d = {}

    async def set(self, key, value, ex=None):
        self.d[key] = value

    async def get(self, key):
        return self.d.get(key)

    async def getdel(self, key):
        return self.d.pop(key, None)


@pytest.fixture(autouse=True)
def _reset_store(monkeypatch):
    monkeypatch.setattr(token_store, "_memory", {})
    monkeypatch.setattr(token_store, "_redis", None)
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))


def _use(fake, monkeypatch):
    async def _get():
        return fake
    monkeypatch.setattr(token_store, "_get_redis", _get)


@pytest.mark.asyncio
async def test_token_consumo_one_time(monkeypatch):
    fake = FakeRedis()
    _use(fake, monkeypatch)
    await token_store.save_token("t1", "org-1", {"dati": [1]})
    meta = await token_store.pop_token("t1")
    assert meta["org_id"] == "org-1"
    assert await token_store.pop_token("t1") is None  # one-time


@pytest.mark.asyncio
async def test_redis_contiene_solo_ciphertext_e_chiave_hashata(monkeypatch):
    fake = FakeRedis()
    _use(fake, monkeypatch)
    await token_store.save_token("secret-download-token", "org-1", {"email": "private@example.test"})
    key, encrypted = next(iter(fake.d.items()))
    assert key == token_store._storage_key("secret-download-token")
    assert "secret-download-token" not in key + encrypted
    assert "private@example.test" not in encrypted


@pytest.mark.asyncio
async def test_memoria_contiene_solo_ciphertext_e_chiave_hashata(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "")
    await token_store.save_token(
        "secret-memory-token", "org-1", {"email": "private@example.test"}
    )
    key, encrypted = next(iter(token_store._memory.items()))

    assert key == token_store._storage_key("secret-memory-token")
    assert isinstance(encrypted, str)
    assert "secret-memory-token" not in key + encrypted
    assert "private@example.test" not in encrypted


@pytest.mark.asyncio
async def test_errore_chiave_non_consuma_token_redis(monkeypatch):
    fake = FakeRedis()
    _use(fake, monkeypatch)
    await token_store.save_token("key-rotation-token", "org-1", {"safe": True})
    stored_key = token_store._storage_key("key-rotation-token")
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))

    with pytest.raises(token_store.TokenStoreUnavailable):
        await token_store.pop_token("key-rotation-token")

    assert stored_key in fake.d


@pytest.mark.asyncio
async def test_cifratura_invalida_non_consuma_token_in_memoria(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "")
    await token_store.save_token("key-rotation-memory-token", "org-1", {"safe": True})
    stored_key = token_store._storage_key("key-rotation-memory-token")
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))

    with pytest.raises(token_store.TokenStoreUnavailable):
        await token_store.pop_token("key-rotation-memory-token")

    assert stored_key in token_store._memory


@pytest.mark.asyncio
async def test_token_sconosciuto(monkeypatch):
    _use(FakeRedis(), monkeypatch)
    assert await token_store.pop_token("nope") is None


@pytest.mark.asyncio
async def test_fallback_memory_senza_redis(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "")
    await token_store.save_token("t2", "org-2", {})
    assert (await token_store.pop_token("t2"))["org_id"] == "org-2"


@pytest.mark.asyncio
async def test_production_requires_shared_redis(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setattr(token_store, "_is_production", lambda: True)

    with pytest.raises(token_store.TokenStoreUnavailable):
        await token_store.save_token("prod-token", "org-3", {})

    assert token_store._storage_key("prod-token") not in token_store._memory


@pytest.mark.asyncio
async def test_production_redis_write_failure_does_not_fall_back_to_memory(monkeypatch):
    class FailingRedis:
        async def set(self, *_args, **_kwargs):
            raise OSError("unavailable")

    _use(FailingRedis(), monkeypatch)
    monkeypatch.setattr(token_store, "_is_production", lambda: True)

    with pytest.raises(token_store.TokenStoreUnavailable):
        await token_store.save_token("prod-token", "org-3", {})

    assert token_store._storage_key("prod-token") not in token_store._memory


@pytest.mark.asyncio
async def test_production_redis_read_failure_does_not_use_memory_fallback(monkeypatch):
    class FailingRedis:
        async def get(self, _key):
            raise OSError("unavailable")

    token_store._memory[token_store._storage_key("prod-token")] = "ciphertext"
    _use(FailingRedis(), monkeypatch)
    monkeypatch.setattr(token_store, "_is_production", lambda: True)

    with pytest.raises(token_store.TokenStoreUnavailable):
        await token_store.pop_token("prod-token")

    assert token_store._storage_key("prod-token") in token_store._memory


@pytest.mark.asyncio
async def test_token_scaduto(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "")
    await token_store.save_token("t3", "org-3", {})
    cipher = Fernet(os.environ["ENCRYPTION_KEY"].encode())
    token_store._memory[token_store._storage_key("t3")] = cipher.encrypt(json.dumps({
        "org_id": "org-3", "data": {}, "expires": time.time() - 1,
    }).encode()).decode()
    assert await token_store.pop_token("t3") is None


@pytest.mark.asyncio
async def test_cifratura_obbligatoria_senza_chiave(monkeypatch):
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    with pytest.raises(token_store.TokenStoreUnavailable):
        await token_store.save_token("t4", "org-4", {"sensitive": True})
