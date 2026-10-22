"""Real-PostgreSQL GDPR snapshot, projection, and integrity contracts."""
import uuid

import pytest
from cryptography.fernet import Fernet

from src.core.gdpr.catalog import CATEGORIES
from src.core.gdpr.exporter import Exporter
from src.core.gdpr.operations import OperationUnavailable


@pytest.fixture(autouse=True)
def _synthetic_encryption_key(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))


async def seed_export_fixture(pool, org_id, *, long_text=False):
    contact_a, contact_b = uuid.uuid4(), uuid.uuid4()
    conversation_a, conversation_b = uuid.uuid4(), uuid.uuid4()
    message_a, message_b = uuid.uuid4(), uuid.uuid4()
    document_id = uuid.uuid4()
    auth_user = uuid.uuid4()
    body_a = "synthetic-private-message-A" + ("a" * 70000 if long_text else "")
    body_b = "synthetic-third-party-message-B"

    async with pool.acquire() as conn:
        await conn.executemany(
            "INSERT INTO contacts(id,organization_id,phone_number) VALUES($1,$2,$3)",
            [(contact_a, org_id, "+10000000001"), (contact_b, org_id, "+10000000002")],
        )
        await conn.executemany(
            "INSERT INTO conversations(id,organization_id,contact_id) VALUES($1,$2,$3)",
            [(conversation_a, org_id, contact_a), (conversation_b, org_id, contact_b)],
        )
        await conn.executemany(
            """INSERT INTO messages(id,organization_id,conversation_id,direction,
                   message_type,content,content_text,status)
               VALUES($1,$2,$3,'inbound','text',$4::jsonb,$5,'received_pending_ai')""",
            [
                (message_a, org_id, conversation_a, '{"body":"synthetic"}', body_a),
                (message_b, org_id, conversation_b, '{"body":"synthetic"}', body_b),
            ],
        )
        await conn.execute(
            "INSERT INTO documents(id,organization_id,nome,tipo) VALUES($1,$2,'synthetic doc','upload')",
            document_id, org_id,
        )
        vector = "[" + ",".join(["0"] * 384) + "]"
        await conn.execute(
            """INSERT INTO document_chunks(organization_id,document_id,chunk_index,content,embedding)
               VALUES($1,$2,0,'synthetic document chunk',$3::vector)""",
            org_id, document_id, vector,
        )
        await conn.execute(
            "INSERT INTO usage_events(organization_id,event_type,quantity,metadata) "
            "VALUES($1,'synthetic_llm_usage',7,'{\"provider\":\"synthetic\",\"total_tokens\":\"19\"}'::jsonb)",
            org_id,
        )
        await conn.execute(
            """INSERT INTO simulation_requests(organization_id,auth_user_id,request_id,payload_hash)
               VALUES($1,$2,$3,$4)""",
            org_id, auth_user, uuid.uuid4(), "a" * 64,
        )
    return {
        "contact_a": contact_a,
        "contact_b": contact_b,
        "body_a": body_a,
        "body_b": body_b,
        "document_id": document_id,
        "auth_user": auth_user,
    }


async def export_bytes(exporter, org, export_id, requester):
    chunks = []
    async for chunk in exporter.stream(org, export_id, requester):
        chunks.append(chunk)
    return b"".join(chunks)


@pytest.mark.asyncio
async def test_catalog_queries_parse_and_blank_tenant_export_is_honestly_bounded(pg_pool, sample_org):
    org, requester = sample_org["id"], "owner-blank"
    async with pg_pool.acquire() as conn:
        for scope, subject in (("organization", None), ("account", str(uuid.uuid4()))):
            for category in CATEGORIES:
                query = category.query if scope == "organization" else getattr(category, scope + "_query")
                if not query:
                    continue
                values = [org] if scope == "organization" else [org, subject]
                values.append(524288)
                bounded = (
                    "SELECT CASE WHEN octet_length(row_to_json(g)::text)<=$"
                    + str(len(values)) + " THEN row_to_json(g)::text ELSE NULL END AS record FROM ("
                    + query + ") AS g"
                )
                try:
                    await conn.fetch(bounded, *values)
                except Exception as exc:
                    pytest.fail(f"{scope} catalog category {category.name} failed: {exc!r}")
    result = await Exporter(pg_pool).prepare(
        org, requester, scope="organization", request_id=uuid.uuid4(),
    )

    assert result["manifest"]["status"] == "complete_for_catalogue"
    assert result["manifest"]["external_copies"] == "not_included"
    assert result["manifest"]["backups"] == "not_included"
    await Exporter(pg_pool).preflight(org, result["export_id"], requester)
    assert b'"organization_id"' in await export_bytes(
        Exporter(pg_pool), org, result["export_id"], requester,
    )


@pytest.mark.asyncio
async def test_organization_snapshot_is_multichunk_ciphertext_and_request_replay_is_stable(
    pg_pool, sample_org,
):
    org, requester, request_id = sample_org["id"], "owner-export", uuid.uuid4()
    data = await seed_export_fixture(pg_pool, org, long_text=True)
    exporter = Exporter(pg_pool)

    first = await exporter.prepare(org, requester, scope="organization", request_id=request_id)
    second = await exporter.prepare(org, requester, scope="organization", request_id=request_id)
    assert first["export_id"] == second["export_id"]
    assert first["manifest"] == second["manifest"]
    assert first["manifest"]["chunks"] > 1

    async with pg_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT ciphertext FROM gdpr_export_chunks WHERE organization_id=$1 AND operation_id=$2",
            org, uuid.UUID(first["export_id"]),
        )
        assert len(rows) == first["manifest"]["chunks"]
        assert all(data["body_a"].encode() not in row["ciphertext"] for row in rows)
        assert all(data["body_b"].encode() not in row["ciphertext"] for row in rows)

    raw = await export_bytes(exporter, org, first["export_id"], requester)
    assert data["body_a"].encode() in raw
    assert data["body_b"].encode() in raw
    assert b"synthetic document chunk" in raw
    assert b"synthetic_llm_usage" in raw
    assert b"simulation_requests" in raw
    assert b"synthetic-third-party-message-B" in raw


@pytest.mark.asyncio
async def test_contact_scope_excludes_other_people_and_unstructured_message_text(
    pg_pool, sample_org,
):
    org, requester = sample_org["id"], "owner-subject"
    data = await seed_export_fixture(pg_pool, org)
    result = await Exporter(pg_pool).prepare(
        org, requester, scope="contact", subject_id=data["contact_a"], request_id=uuid.uuid4(),
    )
    assert result["manifest"]["status"] == "partial"
    raw = await export_bytes(Exporter(pg_pool), org, result["export_id"], requester)

    assert b"+10000000001" in raw
    assert b"+10000000002" not in raw
    assert data["body_a"].encode() not in raw
    assert data["body_b"].encode() not in raw
    assert b"synthetic document chunk" not in raw


@pytest.mark.asyncio
async def test_account_scope_catalog_queries_parse(pg_pool, sample_org):
    org, requester = sample_org["id"], str(uuid.uuid4())
    result = await Exporter(pg_pool).prepare(
        org, requester, scope="account", request_id=uuid.uuid4(),
    )
    assert result["manifest"]["scope"] == "account"
    assert result["manifest"]["status"] == "partial"


@pytest.mark.asyncio
async def test_expired_revoked_and_corrupted_exports_fail_closed(pg_pool, sample_org):
    org, requester = sample_org["id"], "owner-integrity"
    exporter = Exporter(pg_pool)

    expired = await exporter.prepare(org, requester, request_id=uuid.uuid4())
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "UPDATE gdpr_operations SET expires_at=NOW()-INTERVAL '1 second' WHERE id=$1",
            uuid.UUID(expired["export_id"]),
        )
    with pytest.raises(LookupError):
        await exporter.preflight(org, expired["export_id"], requester)
    with pytest.raises(OperationUnavailable):
        await export_bytes(exporter, org, expired["export_id"], requester)

    revoked = await exporter.prepare(org, requester, request_id=uuid.uuid4())
    await exporter.revoke(org, uuid.UUID(revoked["export_id"]), requester)
    with pytest.raises(LookupError):
        await exporter.preflight(org, revoked["export_id"], requester)
    with pytest.raises(OperationUnavailable):
        await export_bytes(exporter, org, revoked["export_id"], requester)

    corrupt = await exporter.prepare(org, requester, request_id=uuid.uuid4())
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "UPDATE gdpr_export_chunks SET ciphertext=ciphertext || '\\x00'::bytea "
            "WHERE organization_id=$1 AND operation_id=$2 AND sequence=0",
            org, uuid.UUID(corrupt["export_id"]),
        )
    with pytest.raises(OperationUnavailable):
        await exporter.preflight(org, corrupt["export_id"], requester)
    with pytest.raises(OperationUnavailable):
        await export_bytes(exporter, org, corrupt["export_id"], requester)
