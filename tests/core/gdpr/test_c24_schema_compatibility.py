"""Exercise C2.4 rights code before SQL070 on a disposable database."""

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from cryptography.fernet import Fernet
import pytest

from src.core.gdpr.c24_schema import conversation_schema
from src.core.gdpr.deletion import Deletion
from src.core.gdpr.exporter import Exporter
from tests.core import conftest as core_fixtures


@pytest.fixture
async def legacy_pool(postgres_container, monkeypatch, request):
    glob = core_fixtures.glob.glob
    # Run the real fixture bootstrap, restricted to the distributed schema.
    def through_069(pattern):
        return [path for path in glob(pattern) if int(Path(path).name[:3]) <= 69]
    with monkeypatch.context() as patch:
        patch.setattr(core_fixtures, 'glob', SimpleNamespace(glob=through_069))
        source = core_fixtures.pg_pool.__wrapped__(postgres_container, request)
        pool = await anext(source)
    try:
        yield pool
    finally:
        await source.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('partial', [False, True])
async def test_rights_on_legacy_or_partial_schema(legacy_pool, monkeypatch, partial):
    org, contact = uuid4(), uuid4()
    monkeypatch.setenv('ENCRYPTION_KEY', Fernet.generate_key().decode())
    monkeypatch.delenv('GDPR_ERASURE_EXECUTION_ENABLED', raising=False)
    async with legacy_pool.acquire() as conn:
        if partial:
            await conn.execute('ALTER TABLE messages ADD COLUMN c24_original_subject_id uuid')
        assert await conversation_schema(conn) == ('partial' if partial else 'absent')
        await conn.execute("INSERT INTO organizations(id,name) VALUES($1,'Synthetic compatibility')", org)
        await conn.execute("INSERT INTO contacts(id,organization_id,phone_number) VALUES($1,$2,'+15550000100')", contact, org)
    exported = await Exporter(legacy_pool).prepare(org, 'synthetic', scope='organization')
    categories = {row['category']: row for row in exported['manifest']['categories']}
    assert categories['simulation_requests']['status'] == 'included'
    assert categories['booking_conversation_states']['status'] == 'excluded'
    assert categories['booking_conversation_states']['reason'] == (
        'conversation_schema_partial' if partial else 'conversation_schema_absent'
    )
    erasure = await Deletion(legacy_pool).request(
        org, 'synthetic', uuid4(), scope='contact', subject_id=contact,
    )
    assert erasure['status'] == 'blocked'
    blockers = erasure['manifest']['blockers']
    assert 'operational_cutover_not_authorized' in blockers
    assert ('conversation_schema_incomplete' in blockers) is partial
    assert await legacy_pool.fetchval(
        'SELECT deleted_at IS NULL FROM contacts WHERE organization_id=$1 AND id=$2', org, contact,
    )
