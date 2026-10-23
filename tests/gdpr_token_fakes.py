"""Explicit in-memory Redis fake for GDPR token-store unit/route tests only."""

import asyncio


class FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def set(self, key, value, ex=None):
        self.values[key] = value
        return True

    async def get(self, key):
        return self.values.get(key)

    async def eval(self, _script, numkeys, key, expected):
        assert numkeys == 1
        async with self._lock:
            if self.values.get(key) == expected:
                del self.values[key]
                return 1
            return 0
