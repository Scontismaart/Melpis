"""B02 PostgreSQL regression coverage for atomic, fenced document publication."""

import hashlib
import uuid

import pytest


pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def entitled_test_organizations(pg_pool, sample_org, other_org):
    await pg_pool.execute(
        "UPDATE organizations SET plan='business', subscription_status='trialing', "
        "trial_start=now()-interval '1 hour', trial_end=now()+interval '6 days' "
        "WHERE id=ANY($1::uuid[])",
        [sample_org["id"], other_org["id"]],
    )


def _vectors(count=1):
    return [[0.01] * 384 for _ in range(count)]


async def _claim(repo, org_id, *, key=None, payload="pdf-content", name="menu.pdf"):
    return await repo.doc_repo.begin_index(
        org_id,
        name,
        key or str(uuid.uuid4()),
        hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        tipo="upload",
        fonte="test",
    )


async def _publish(repo, org_id, claim, chunks=None, embeddings=None):
    chunks = chunks or ["Pizza margherita: 8 euro."]
    embeddings = embeddings or _vectors(len(chunks))
    return await repo.doc_repo.publish_index(
        org_id,
        claim["document"]["id"],
        claim["token"],
        claim["fence"],
        chunks,
        embeddings,
        {"source": "b02-test"},
    )


async def test_publish_is_atomic_and_marks_verified_ready(repo, sample_org, pg_pool):
    claim = await _claim(repo, sample_org["id"])
    published = await _publish(
        repo,
        sample_org["id"],
        claim,
        ["chunk one", "chunk two", "chunk three"],
    )
    document_id = claim["document"]["id"]

    row = await pg_pool.fetchrow(
        "SELECT stato, index_verified FROM documents WHERE id=$1 AND organization_id=$2",
        document_id,
        sample_org["id"],
    )
    assert row["stato"] == "indicizzata"
    assert row["index_verified"] is True
    assert published["stato"] == "indicizzata"
    assert await pg_pool.fetchval(
        "SELECT count(*) FROM document_chunks WHERE document_id=$1 AND organization_id=$2",
        document_id,
        sample_org["id"],
    ) == 3


async def test_invalid_vector_mid_batch_leaves_no_partial_publication(
    repo, sample_org, pg_pool,
):
    claim = await _claim(repo, sample_org["id"])
    vectors = _vectors(3)
    vectors[1] = [float("nan")] * 384

    with pytest.raises(ValueError):
        await _publish(
            repo,
            sample_org["id"],
            claim,
            ["one", "two", "three"],
            vectors,
        )

    document_id = claim["document"]["id"]
    assert await pg_pool.fetchval(
        "SELECT count(*) FROM document_chunks WHERE document_id=$1", document_id,
    ) == 0
    row = await pg_pool.fetchrow(
        "SELECT stato, index_verified FROM documents WHERE id=$1", document_id,
    )
    assert row["stato"] == "in_elaborazione"
    assert row["index_verified"] is False


async def test_same_idempotency_key_and_payload_replays_existing_claim(repo, sample_org):
    key = str(uuid.uuid4())
    first = await _claim(repo, sample_org["id"], key=key)
    in_progress = await _claim(repo, sample_org["id"], key=key)
    await _publish(repo, sample_org["id"], first)
    replay = await _claim(repo, sample_org["id"], key=key)

    assert first["status"] == "claimed"
    assert in_progress["status"] == "in_progress"
    assert in_progress["document"]["id"] == first["document"]["id"]
    assert replay["status"] == "replay"
    assert replay["document"]["id"] == first["document"]["id"]


async def test_same_idempotency_key_with_different_payload_conflicts(repo, sample_org):
    key = str(uuid.uuid4())
    first = await _claim(repo, sample_org["id"], key=key, payload="payload-a")
    second = await _claim(repo, sample_org["id"], key=key, payload="payload-b")

    assert first["status"] == "claimed"
    assert second["status"] == "conflict"
    assert second["document"] is None


async def test_legacy_metadata_writer_cannot_promote_a_verified_source(repo, sample_org):
    claim = await _claim(repo, sample_org["id"])
    await _publish(repo, sample_org["id"], claim)
    changed = await repo.update_document(sample_org["id"], claim["document"]["id"], tipo="dati_struttura")
    assert changed["index_verified"] is False
    assert await repo.search_similar(sample_org["id"], _vectors()[0]) == []


async def test_idempotency_key_is_tenant_scoped(repo, sample_org, other_org):
    key = str(uuid.uuid4())
    first = await _claim(repo, sample_org["id"], key=key)
    other = await _claim(repo, other_org["id"], key=key)

    assert other["status"] == "claimed"
    assert other["document"]["id"] != first["document"]["id"]


async def test_cross_tenant_document_reference_is_denied(repo, sample_org, other_org):
    claim = await _claim(repo, sample_org["id"])

    with pytest.raises(ValueError):
        await repo.doc_repo.publish_index(
            other_org["id"],
            claim["document"]["id"],
            claim["token"],
            claim["fence"],
            ["must not leak"],
            _vectors(),
            {},
        )


async def test_expired_lease_reclaim_fences_old_publisher(repo, sample_org, pg_pool):
    key = str(uuid.uuid4())
    first = await _claim(repo, sample_org["id"], key=key)
    await pg_pool.execute(
        "UPDATE documents SET index_lease_until=now()-interval '1 second' WHERE id=$1",
        first["document"]["id"],
    )
    second = await _claim(repo, sample_org["id"], key=key)

    assert second["status"] == "claimed"
    assert second["token"] != first["token"]
    assert second["fence"] > first["fence"]
    with pytest.raises(ValueError):
        await _publish(repo, sample_org["id"], first)
    assert await repo.doc_repo.fail_index(
        sample_org["id"],
        first["document"]["id"],
        first["token"],
        first["fence"],
        "stale_worker",
    ) is False
    await _publish(repo, sample_org["id"], second)


async def test_delayed_old_request_conflicts_after_newer_reindex(repo, sample_org):
    org_id = sample_org["id"]
    old_key = str(uuid.uuid4())
    old_payload = "original-version"
    first = await _claim(repo, org_id, key=old_key, payload=old_payload)
    await _publish(repo, org_id, first)

    newer = await repo.doc_repo.begin_index(
        org_id,
        "menu.pdf",
        str(uuid.uuid4()),
        hashlib.sha256(b"new-version").hexdigest(),
        document_id=first["document"]["id"],
    )
    assert newer["status"] == "claimed"

    delayed = await _claim(repo, org_id, key=old_key, payload=old_payload)
    assert delayed["status"] == "conflict"
    assert delayed["document"] is None


async def test_deleted_document_request_is_a_persistent_tombstone(
    repo, sample_org,
):
    org_id = sample_org["id"]
    key = str(uuid.uuid4())
    claim = await _claim(repo, org_id, key=key, payload="deleted-source")
    await _publish(repo, org_id, claim)
    assert await repo.doc_repo.delete_document(org_id, claim["document"]["id"]) == 1

    retry = await _claim(repo, org_id, key=key, payload="deleted-source")
    assert retry["status"] == "conflict"
    assert retry["document"] is None


async def test_successful_publication_toggle_and_delete_advance_knowledge_epoch(
    repo, sample_org, pg_pool,
):
    org_id = sample_org["id"]
    epoch = await pg_pool.fetchval(
        "SELECT knowledge_epoch FROM organizations WHERE id=$1", org_id,
    )
    claim = await _claim(repo, org_id)
    await _publish(repo, org_id, claim)
    after_publish = await pg_pool.fetchval(
        "SELECT knowledge_epoch FROM organizations WHERE id=$1", org_id,
    )
    assert after_publish > epoch

    assert await repo.doc_repo.toggle_document_active(org_id, claim["document"]["id"])
    after_toggle = await pg_pool.fetchval(
        "SELECT knowledge_epoch FROM organizations WHERE id=$1", org_id,
    )
    assert after_toggle > after_publish

    assert await repo.doc_repo.delete_document(org_id, claim["document"]["id"]) == 1
    after_delete = await pg_pool.fetchval(
        "SELECT knowledge_epoch FROM organizations WHERE id=$1", org_id,
    )
    assert after_delete > after_toggle


async def test_historical_ready_document_with_chunks_is_not_retrievable(
    repo, sample_org,
):
    historical = await repo.doc_repo.create_document(
        sample_org["id"], "legacy.pdf", stato="indicizzata",
    )
    await repo.doc_repo.add_chunk(
        sample_org["id"], historical["id"], 0, "legacy secret text", _vectors()[0],
    )

    results = await repo.doc_repo.search_similar(
        sample_org["id"], _vectors()[0], k=10,
    )
    assert all(str(item["document_id"]) != str(historical["id"]) for item in results)


async def test_moving_chunk_invalidates_both_certified_documents(repo, sample_org, pg_pool):
    org_id = sample_org["id"]
    source = await _claim(repo, org_id, name="source.pdf")
    target = await _claim(repo, org_id, name="target.pdf")
    await _publish(repo, org_id, source)
    await _publish(repo, org_id, target)
    chunk_id = await pg_pool.fetchval(
        "SELECT id FROM document_chunks WHERE document_id=$1", source["document"]["id"],
    )

    await pg_pool.execute(
        "UPDATE document_chunks SET document_id=$1 WHERE id=$2",
        target["document"]["id"],
        chunk_id,
    )
    rows = await pg_pool.fetch(
        "SELECT id, stato, index_verified FROM documents WHERE id=ANY($1::uuid[])",
        [source["document"]["id"], target["document"]["id"]],
    )
    assert len(rows) == 2
    assert all(row["stato"] == "caricato" and not row["index_verified"] for row in rows)


@pytest.mark.parametrize("plan", ["starter", "pro", "business"])
async def test_all_plans_can_claim_publication(repo, sample_org, pg_pool, plan):
    await pg_pool.execute(
        "UPDATE organizations SET plan=$2 WHERE id=$1", sample_org["id"], plan,
    )

    claim = await _claim(repo, sample_org["id"])
    assert claim["status"] == "claimed"


async def test_downgrade_keeps_text_publication_available(
    repo, sample_org, pg_pool,
):
    org_id = sample_org["id"]
    claim = await _claim(repo, org_id)
    await pg_pool.execute(
        "UPDATE organizations SET plan='starter' WHERE id=$1", org_id,
    )

    published = await _publish(repo, org_id, claim)
    assert published["index_verified"] is True
    assert await pg_pool.fetchval(
        "SELECT count(*) FROM document_chunks WHERE document_id=$1",
        claim["document"]["id"],
    ) == 1


async def test_client_roles_cannot_write_documents_chunks_or_faq_cache(pg_pool):
    # Check grants directly so the regression remains clear even when RLS
    # would otherwise hide rows from a client role.
    for role in ("anon", "authenticated"):
        for table in ("documents", "document_chunks"):
            for privilege in ("INSERT", "UPDATE", "DELETE"):
                allowed = await pg_pool.fetchval(
                    "SELECT has_table_privilege($1, $2, $3)", role, table, privilege,
                )
                assert allowed is False, f"{role} unexpectedly has {privilege} on {table}"
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            allowed = await pg_pool.fetchval(
                "SELECT has_table_privilege($1, 'faq_cache', $2)", role, privilege,
            )
            assert allowed is False, f"{role} unexpectedly has {privilege} on faq_cache"
