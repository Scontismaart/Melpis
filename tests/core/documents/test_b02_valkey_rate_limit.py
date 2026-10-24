"""Real Valkey admission shared across upload workers, scoped by tenant."""
import asyncio
import os
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from src.core.rate_limit import RedisRateLimiter


@pytest.mark.asyncio
async def test_knowledge_upload_rate_limit_is_atomic_and_tenant_scoped():
    url = os.getenv('GDPR_TEST_VALKEY_URL')
    if not url:
        pytest.skip('Disposable Valkey URL not configured')
    client = Redis.from_url(url, decode_responses=True)
    prefix = f'b02-synthetic-{uuid4()}'
    first, second = RedisRateLimiter(client, prefix), RedisRateLimiter(client, prefix)
    a, b = f'org:{uuid4()}:knowledge-index', f'org:{uuid4()}:knowledge-index'
    try:
        await client.ping()
        results = await asyncio.gather(*[
            (first if i % 2 else second).hit(a, 20, 600) for i in range(40)
        ])
        assert results.count(False) == 20 and results.count(True) == 20
        assert await first.hit(b, 20, 600) is False
        assert 0 < await client.ttl(f'{prefix}:{a}') <= 600
    finally:
        await client.delete(f'{prefix}:{a}', f'{prefix}:{b}')
        await client.aclose()
