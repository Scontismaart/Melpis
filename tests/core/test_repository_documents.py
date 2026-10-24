import asyncio
import hashlib
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("reset_db")


async def _enable_rag(pg_pool, organization_id):
    await pg_pool.execute(
        "UPDATE organizations SET plan='business', subscription_status='trialing', "
        "trial_start=now()-interval '1 hour', trial_end=now()+interval '6 days' "
        "WHERE id=$1",
        organization_id,
    )


async def _publish(repo, organization_id, name, chunks, embeddings):
    await _enable_rag(repo.pool, organization_id)
    source = "\n".join(chunks)
    claim = await repo.doc_repo.begin_index(
        organization_id,
        name,
        str(uuid.uuid4()),
        hashlib.sha256(source.encode("utf-8")).hexdigest(),
        tipo="upload",
        fonte="test",
    )
    assert claim["status"] == "claimed"
    published = await repo.doc_repo.publish_index(
        organization_id,
        claim["document"]["id"],
        claim["token"],
        claim["fence"],
        chunks,
        embeddings,
        {"fixture": "repository-documents"},
    )
    return claim, published

@pytest.mark.asyncio
async def test_create_document_and_add_chunks(repo, sample_org):
    doc = await repo.create_document(
        organization_id=sample_org["id"],
        nome="menu_estivo.pdf",
        tipo="upload",
        fonte="dashboard",
    )
    assert doc["nome"] == "menu_estivo.pdf"

    chunk = await repo.add_chunk(
        organization_id=sample_org["id"],
        document_id=doc["id"],
        chunk_index=0,
        content="Antipasto misto della casa: 12€",
        embedding=[0.1] * 384,
        metadata={"fonte": "menu_estivo.pdf"},
    )
    assert chunk["chunk_index"] == 0
    assert chunk["content"] == "Antipasto misto della casa: 12€"


@pytest.mark.asyncio
async def test_search_similar_returns_chunks(repo, sample_org):
    await _publish(
        repo,
        sample_org["id"],
        "menu.pdf",
        ["Pizza margherita: 8€", "Pasta carbonara: 12€"],
        [[0.1] * 384, [0.2] * 384],
    )

    results = await repo.search_similar(
        organization_id=sample_org["id"],
        embedding=[0.15] * 384,
        k=2,
    )
    assert len(results) >= 1
    contents = [r["content"] for r in results]
    assert any("Pizza" in c or "Pasta" in c for c in contents)


@pytest.mark.asyncio
async def test_concurrent_publishers_and_reader_observe_only_complete_index(repo, sample_org):
    org_id = sample_org["id"]
    chunks = ["publish race first", "publish race second"]
    embeddings = [[0.1] * 384, [0.2] * 384]
    claim, _ = await _publish(repo, org_id, "race.pdf", ["baseline"], [[0.3] * 384])

    # Reclaim the same document with a newer content identity for two racing publishers.
    claim = await repo.doc_repo.begin_index(
        org_id,
        "race.pdf",
        str(uuid.uuid4()),
        hashlib.sha256("\n".join(chunks).encode("utf-8")).hexdigest(),
        tipo="upload",
        fonte="test",
        document_id=claim["document"]["id"],
    )
    assert claim["status"] == "claimed"

    async def publish():
        try:
            return await repo.doc_repo.publish_index(
                org_id, claim["document"]["id"], claim["token"], claim["fence"],
                chunks, embeddings, {"fixture": "publication-race"},
            )
        except ValueError as exc:
            return exc

    outcomes = await asyncio.gather(
        publish(),
        publish(),
        repo.search_similar(org_id, [0.1] * 384, k=5),
    )
    successes = [item for item in outcomes[:2] if isinstance(item, dict)]
    failures = [item for item in outcomes[:2] if isinstance(item, ValueError)]
    assert len(successes) == 1
    assert len(failures) == 1

    read_during_publish = outcomes[2]
    observed = {row["content"] for row in read_during_publish}
    assert observed in (set(), set(chunks))
    final = await repo.search_similar(org_id, [0.1] * 384, k=5)
    assert {row["content"] for row in final} == set(chunks)


@pytest.mark.asyncio
async def test_active_toggle_hides_complete_publication_from_default_retrieval(repo, sample_org):
    org_id = sample_org["id"]
    _, published = await _publish(
        repo, org_id, "toggle.pdf", ["active source one", "active source two"],
        [[0.1] * 384, [0.2] * 384],
    )
    assert published["index_verified"] is True
    assert await repo.doc_repo.toggle_document_active(org_id, published["id"]) is not None

    assert await repo.search_similar(org_id, [0.1] * 384, k=5) == []
    inactive_included = await repo.search_similar(
        org_id, [0.1] * 384, k=5, only_active=False,
    )
    # The legacy argument is retained for call compatibility, never authority
    # to make inactive knowledge available to AI.
    assert inactive_included == []


@pytest.mark.asyncio
async def test_mid_batch_database_error_rolls_back_all_chunks_and_ready_state(
    repo, sample_org, pg_pool,
):
    org_id = sample_org["id"]
    await _enable_rag(pg_pool, org_id)
    chunks = ["first valid row", "second\x00invalid row"]
    key = str(uuid.uuid4())
    claim = await repo.doc_repo.begin_index(
        org_id,
        "rollback.pdf",
        key,
        hashlib.sha256("rollback-payload".encode()).hexdigest(),
        tipo="upload",
        fonte="test",
    )

    with pytest.raises(Exception):
        await repo.doc_repo.publish_index(
            org_id,
            claim["document"]["id"],
            claim["token"],
            claim["fence"],
            chunks,
            [[0.1] * 384, [0.2] * 384],
            {},
        )

    document = await pg_pool.fetchrow(
        "SELECT stato,index_verified FROM documents WHERE id=$1 AND organization_id=$2",
        claim["document"]["id"], org_id,
    )
    assert document["stato"] == "in_elaborazione"
    assert document["index_verified"] is False
    assert await pg_pool.fetchval(
        "SELECT count(*) FROM document_chunks WHERE document_id=$1", claim["document"]["id"],
    ) == 0


@pytest.mark.asyncio
async def test_document_chunk_cross_tenant_trigger(repo, sample_org, other_org):
    doc = await repo.create_document(
        organization_id=sample_org["id"],
        nome="doc1.pdf", tipo="upload",
    )
    with pytest.raises(Exception, match="organization_id mismatch"):
        await repo.add_chunk(
            organization_id=other_org["id"],
            document_id=doc["id"],
            chunk_index=0, content="test",
            embedding=[0.1] * 384,
        )


@pytest.mark.asyncio
async def test_list_documents(repo, sample_org):
    await repo.create_document(
        organization_id=sample_org["id"],
        nome="doc1.pdf", tipo="upload",
    )
    await repo.create_document(
        organization_id=sample_org["id"],
        nome="doc2.pdf", tipo="upload",
    )
    docs = await repo.list_documents(sample_org["id"])
    assert len(docs) == 2


@pytest.mark.asyncio
async def test_count_chunks(repo, sample_org, other_org):
    await _publish(repo, sample_org["id"], "doc.pdf", ["c1", "c2"], [[0.1]*384, [0.2]*384])
    assert await repo.count_chunks(sample_org["id"]) == 2
    assert await repo.count_chunks(other_org["id"]) == 0


@pytest.mark.asyncio
async def test_list_sources_groups_by_document(repo, sample_org):
    doc = await repo.create_document(organization_id=sample_org["id"], nome="menu.pdf", tipo="upload", fonte="dashboard")
    await repo.add_chunk(organization_id=sample_org["id"], document_id=doc["id"],
                         chunk_index=0, content="c1", embedding=[0.1] * 384)
    await repo.add_chunk(organization_id=sample_org["id"], document_id=doc["id"],
                         chunk_index=1, content="c2", embedding=[0.2] * 384)
    fonti = await repo.list_sources(sample_org["id"])
    assert len(fonti) == 1
    assert fonti[0]["nome"] == "menu.pdf"
    assert fonti[0]["chunk"] == 2
    assert str(fonti[0]["id"]) == str(doc["id"])


@pytest.mark.asyncio
async def test_delete_document_org_scoped(repo, sample_org, other_org):
    doc = await repo.create_document(organization_id=sample_org["id"], nome="doc.pdf", tipo="upload")
    await repo.add_chunk(organization_id=sample_org["id"], document_id=doc["id"],
                         chunk_index=0, content="c1", embedding=[0.1] * 384)
    # org sbagliata non tocca nulla
    assert await repo.delete_document(other_org["id"], doc["id"]) == 0
    # org giusta elimina documento + chunk via cascade
    assert await repo.delete_document(sample_org["id"], doc["id"]) == 1
    assert await repo.count_chunks(sample_org["id"]) == 0
