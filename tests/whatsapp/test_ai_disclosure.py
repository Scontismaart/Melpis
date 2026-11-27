import uuid
import asyncpg
import pytest


pytestmark = pytest.mark.usefixtures("reset_db")


@pytest.fixture
async def disclosure_pool(postgres_container):
    dsn = postgres_container.get_connection_url().replace("+psycopg2", "")
    pool = await asyncpg.create_pool(dsn=dsn, min_size=2, max_size=10)
    async with pool.acquire() as conn:
        await conn.execute("""
            DROP SCHEMA IF EXISTS public CASCADE;
            CREATE SCHEMA public;
        """)
        with open("src/whatsapp/schema.sql") as f:
            await conn.execute(f.read())
        with open("src/core/db/migrations/004_gdpr.sql") as f:
            await conn.execute(f.read())
        with open("src/core/db/migrations/005_gdpr_consent.sql") as f:
            await conn.execute(f.read())
        with open("src/core/db/migrations/029_ai_disclosure.sql") as f:
            await conn.execute(f.read())
    yield pool
    await pool.close()


@pytest.fixture(autouse=True)
async def reset_db():
    pass  # Shadow del conftest: questo file ha pool e reset propri


@pytest.fixture(autouse=True)
async def reset_disclosure_db(disclosure_pool):
    async with disclosure_pool.acquire() as conn:
        await conn.execute("TRUNCATE TABLE contacts, conversations, messages, contact_consent_log CASCADE")


@pytest.fixture
async def repo(disclosure_pool):
    from src.whatsapp.repository import Repository
    return Repository(pool=disclosure_pool)


async def _make_contact(repo) -> dict:
    async with repo.pool.acquire() as conn:
        org = await conn.fetchrow(
            "INSERT INTO organizations (id, name) VALUES ($1, 'Disclosure Test') RETURNING id",
            uuid.uuid4(),
        )
        contact = await conn.fetchrow(
            "INSERT INTO contacts (id, organization_id, phone_number) VALUES ($1, $2, $3) RETURNING id, organization_id",
            uuid.uuid4(), org["id"], "+391234567891",
        )
    return dict(contact)


class TestMarkAiDisclosureSent:
    async def test_first_call_returns_true_and_sets_timestamp(self, repo):
        contact = await _make_contact(repo)
        result = await repo.mark_ai_disclosure_sent(contact["id"], contact["organization_id"])
        assert result is True
        async with repo.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT ai_disclosure_sent_at FROM contacts WHERE id = $1", contact["id"])
        assert row["ai_disclosure_sent_at"] is not None

    async def test_second_call_returns_false(self, repo):
        contact = await _make_contact(repo)
        first = await repo.mark_ai_disclosure_sent(contact["id"], contact["organization_id"])
        second = await repo.mark_ai_disclosure_sent(contact["id"], contact["organization_id"])
        assert first is True
        assert second is False

    async def test_missing_contact_returns_false(self, repo):
        result = await repo.mark_ai_disclosure_sent(uuid.uuid4(), uuid.uuid4())
        assert result is False


async def test_first_disclosure_guard_serializes_workers_per_tenant_contact(repo):
    contact = await _make_contact(repo)
    other_contact = uuid.uuid4()
    org, contact_id = contact["organization_id"], contact["id"]

    async with repo.ai_disclosure_guard(org, contact_id) as first:
        assert first
        async with repo.ai_disclosure_guard(org, contact_id) as concurrent:
            assert not concurrent
        async with repo.ai_disclosure_guard(org, other_contact) as independent:
            assert independent
    async with repo.ai_disclosure_guard(org, contact_id) as retry:
        assert retry
