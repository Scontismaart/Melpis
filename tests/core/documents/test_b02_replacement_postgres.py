"""B02 replacement failure must preserve the previous certified revision."""

import hashlib
import asyncio
import json
import uuid

import pytest


pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def entitled_test_organization(pg_pool, sample_org):
    await pg_pool.execute(
        "UPDATE organizations SET plan='business', subscription_status='trialing', "
        "trial_start=now()-interval '1 hour', trial_end=now()+interval '6 days' "
        "WHERE id=$1",
        sample_org["id"],
    )


def _vector():
    return [0.01] * 384


async def _publish_file(repo, org_id, *, name="old-menu.pdf", tipo="upload", expected_profile=None,
                        file_bytes=20):
    content = (b"01234567890123456789" * ((file_bytes + 19) // 20))[:file_bytes]
    claim = await repo.doc_repo.begin_index(
        org_id,
        name,
        uuid.uuid4(),
        hashlib.sha256((name + "-payload").encode()).hexdigest(),
        tipo=tipo,
        fonte="certified source",
        expected_profile=expected_profile,
        file_bytes=file_bytes,
        file_sha256=hashlib.sha256(content).hexdigest() if file_bytes else None,
    )
    published = await repo.doc_repo.publish_index(
        org_id,
        claim["document"]["id"],
        claim["token"],
        claim["fence"],
        ["Original menu: margherita 8 euro."],
        [_vector()],
        {"label": "certified-original"},
    )
    return content, published


def _replacement_args(document_id, *, key=None, name="new-menu.pdf", payload=b"new replacement payload",
                     tipo="upload", expected_profile=None, file_bytes=30):
    return {
        "name": name,
        "request_key": key or uuid.uuid4(),
        "payload_hash": hashlib.sha256(payload).hexdigest(),
        "tipo": tipo,
        "fonte": "replacement source",
        "document_id": document_id,
        "expected_profile": expected_profile,
        "file_bytes": file_bytes,
        "file_sha256": hashlib.sha256(b"r" * file_bytes).hexdigest() if file_bytes else None,
    }


async def _replacement_claim(repo, org_id, document_id, **kwargs):
    args = _replacement_args(document_id, **kwargs)
    name = args.pop("name")
    payload_hash = args.pop("payload_hash")
    request_key = args.pop("request_key")
    return await repo.doc_repo.begin_index(org_id, name, request_key, payload_hash, **args)


async def _assert_certified_source(repo, org_id, published, *, bytes_count=20):
    current = await repo.doc_repo.get_document(org_id, published["id"])
    assert current["nome"] == "old-menu.pdf"
    assert current["revision"] == published["revision"]
    assert current["stato"] == "indicizzata"
    assert current["index_verified"] is True
    assert current["file_bytes"] == bytes_count
    assert current["metadata"]["label"] == "certified-original"
    results = await repo.doc_repo.search_similar(org_id, _vector())
    assert any(row["document_id"] == published["id"] for row in results)
    assert await repo.doc_repo.count_chunks(org_id) == 1
    return current


async def test_failed_replacement_keeps_previous_certified_source(repo, sample_org):
    org_id = sample_org["id"]
    original = b"01234567890123456789"
    replacement = b"012345678901234567890123456789"
    original_key = uuid.uuid4()
    first = await repo.doc_repo.begin_index(
        org_id,
        "old-menu.pdf",
        original_key,
        hashlib.sha256(b"original payload").hexdigest(),
        tipo="upload",
        fonte="original source",
        file_bytes=len(original),
        file_sha256=hashlib.sha256(original).hexdigest(),
    )
    assert first["status"] == "claimed"
    published = await repo.doc_repo.publish_index(
        org_id,
        first["document"]["id"],
        first["token"],
        first["fence"],
        ["Original menu: margherita 8 euro."],
        [_vector()],
        {"label": "certified-original"},
    )

    replacement_key = uuid.uuid4()
    replacement_claim = await repo.doc_repo.begin_index(
        org_id,
        "new-menu.pdf",
        replacement_key,
        hashlib.sha256(b"replacement payload").hexdigest(),
        tipo="upload",
        fonte="replacement source",
        document_id=published["id"],
        file_bytes=len(replacement),
        file_sha256=hashlib.sha256(replacement).hexdigest(),
    )
    assert replacement_claim["status"] == "claimed"

    during = await repo.doc_repo.get_document(org_id, published["id"])
    assert during["nome"] == "old-menu.pdf"
    assert during["revision"] == published["revision"]
    assert during["stato"] == "indicizzata"
    assert during["index_verified"] is True
    assert during["metadata"]["label"] == "certified-original"
    assert during["index_key"] == published["index_key"]
    assert during["content_hash"] == published["content_hash"]
    assert during["file_bytes"] == len(original)
    assert during["index_file_bytes"] == len(replacement)
    intent = during["index_intent"]
    if isinstance(intent, str):
        intent = json.loads(intent)
    assert intent["request_key"] == str(replacement_key)
    assert intent["payload_hash"] == hashlib.sha256(b"replacement payload").hexdigest()

    assert await repo.doc_repo.fail_index(
        org_id,
        published["id"],
        replacement_claim["token"],
        replacement_claim["fence"],
        "ocr_unavailable",
    ) is True

    current = await _assert_certified_source(repo, org_id, published)
    assert current["file_sha256"] == hashlib.sha256(original).hexdigest()

    quota = await repo.doc_repo.file_quota_status(org_id)
    assert quota["file_quota"]["committed_bytes"] == len(original)
    assert quota["file_quota"]["reserved_bytes"] == 0


async def test_source_listing_exposes_request_state_without_internal_claim_fields(repo, sample_org):
    _, old = await _publish_file(repo, sample_org["id"])
    new_key = uuid.uuid4()
    claim = await _replacement_claim(repo, sample_org["id"], old["id"], key=new_key)

    during = next(
        source for source in await repo.doc_repo.list_sources(sample_org["id"])
        if source["id"] == old["id"]
    )
    assert during["index_status"] == "in_progress"
    assert during["index_request_id"] == str(new_key)
    assert during["published_request_id"] == str(old["index_key"])
    assert during["nome"] == "old-menu.pdf"
    assert during["revision"] == old["revision"]
    for internal in (
        "index_token", "index_fence", "index_lease_until", "index_intent",
        "content_hash", "index_file_sha256", "file_sha256",
    ):
        assert internal not in during

    assert await repo.doc_repo.fail_index(
        sample_org["id"], old["id"], claim["token"], claim["fence"], "ocr_unavailable",
    ) is True
    after = next(
        source for source in await repo.doc_repo.list_sources(sample_org["id"])
        if source["id"] == old["id"]
    )
    assert after["index_status"] == "failed"
    assert after["index_request_id"] == str(new_key)
    assert after["published_request_id"] == str(old["index_key"])
    assert after["nome"] == "old-menu.pdf"
    assert after["revision"] == old["revision"]
    for internal in (
        "index_token", "index_fence", "index_lease_until", "index_intent",
        "content_hash", "index_file_sha256", "file_sha256",
    ):
        assert internal not in after
    await _assert_certified_source(repo, sample_org["id"], old)


@pytest.mark.parametrize("failure", ["ocr", "embedding", "timeout"])
async def test_index_content_failures_keep_old_source_and_release_reservation(
    repo, sample_org, monkeypatch, failure,
):
    from fastapi import HTTPException
    from src.core.documenti import indexing

    _, old = await _publish_file(repo, sample_org["id"])
    replacement = b"r" * 30

    async def extracted(*_args):
        if failure == "ocr":
            raise ValueError("ocr_unavailable")
        if failure == "timeout":
            await asyncio.sleep(0.05)
        return {"page_texts": ["Replacement menu with a new item."], "pages": 1, "ocr_pages": 0}

    def embedded(texts, tipo="passage"):
        if failure == "embedding":
            raise RuntimeError("synthetic embedding failure")
        return [_vector() for _ in texts]

    monkeypatch.setattr(indexing, "extract_local", extracted)
    monkeypatch.setattr(indexing.limits, "INDEX_SECONDS", 0.01 if failure == "timeout" else 2)
    with pytest.raises(HTTPException):
        await indexing.index_content(
            repo,
            sample_org["id"],
            "new-menu.pdf",
            request_key=uuid.uuid4(),
            file_content=replacement,
            content_type="application/pdf",
            embedding_fn=embedded,
            document_id=old["id"],
            fonte="replacement source",
        )

    await _assert_certified_source(repo, sample_org["id"], old)
    quota = await repo.doc_repo.file_quota_status(sample_org["id"])
    assert quota["file_quota"]["committed_bytes"] == len(replacement[:20])
    assert quota["file_quota"]["reserved_bytes"] == 0


async def test_failed_same_key_replacement_can_retry_and_publish(repo, sample_org, monkeypatch):
    from fastapi import HTTPException
    from src.core.documenti import indexing

    _, old = await _publish_file(repo, sample_org["id"])
    request_key = uuid.uuid4()
    replacement = b"new content for replacement"

    async def broken_extract(*_args):
        raise ValueError("ocr_unavailable")

    async def good_extract(*_args):
        return {"page_texts": ["Replacement menu has a new item."], "pages": 1, "ocr_pages": 0}

    monkeypatch.setattr(indexing, "extract_local", broken_extract)
    with pytest.raises(HTTPException):
        await indexing.index_content(
            repo, sample_org["id"], "new-menu.pdf", request_key=request_key,
            file_content=replacement, content_type="application/pdf", document_id=old["id"],
        )
    await _assert_certified_source(repo, sample_org["id"], old)

    monkeypatch.setattr(indexing, "extract_local", good_extract)
    result = await indexing.index_content(
        repo, sample_org["id"], "new-menu.pdf", request_key=request_key,
        file_content=replacement, content_type="application/pdf", document_id=old["id"],
        embedding_fn=lambda texts, tipo="passage": [_vector() for _ in texts],
    )
    assert result["status"] == "indexed"
    assert result["document"]["revision"] == old["revision"] + 1
    assert result["document"]["nome"] == "new-menu.pdf"

    from src.core.documenti.sources import sources_current
    async with repo.pool.acquire() as conn:
        assert await sources_current(conn, sample_org["id"], [
            {"document_id": str(old["id"]), "revision": old["revision"]},
        ]) is False


async def test_cancellation_during_replacement_releases_claim_and_keeps_old_context(
    repo, sample_org, monkeypatch,
):
    from src.core.documenti import indexing
    from src.core.documenti.rag_context import recupera_contesto_documenti, require_current_context

    _, old = await _publish_file(repo, sample_org["id"])
    started = asyncio.Event()

    async def blocked_extract(*_args):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(indexing, "extract_local", blocked_extract)
    task = asyncio.create_task(indexing.index_content(
        repo,
        sample_org["id"],
        "cancelled-menu.pdf",
        request_key=uuid.uuid4(),
        file_content=b"c" * 30,
        content_type="application/pdf",
        document_id=old["id"],
    ))
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await _assert_certified_source(repo, sample_org["id"], old)
    context = await recupera_contesto_documenti(
        str(sample_org["id"]), "original menu", repo, q_emb=_vector(),
    )
    assert context.reason == "verified"
    assert any(item["document_id"] == old["id"] for item in context.chunks)
    await require_current_context(context, repo, sample_org["id"])
    quota = await repo.doc_repo.file_quota_status(sample_org["id"])
    assert quota["file_quota"]["committed_bytes"] == 20
    assert quota["file_quota"]["reserved_bytes"] == 0


async def test_two_simultaneous_claims_for_same_request_only_one_claims(repo, sample_org):
    _, old = await _publish_file(repo, sample_org["id"])
    key = uuid.uuid4()
    kwargs = _replacement_args(old["id"], key=key)
    first_name = kwargs.pop("name")
    payload_hash = kwargs.pop("payload_hash")
    key = kwargs.pop("request_key")

    async def claim_once():
        return await repo.doc_repo.begin_index(
            sample_org["id"], first_name, key, payload_hash, **kwargs,
        )

    results = await asyncio.gather(claim_once(), claim_once())
    assert sorted(result["status"] for result in results) == ["claimed", "in_progress"]
    claimed = next(result for result in results if result["status"] == "claimed")
    assert await repo.doc_repo.fail_index(
        sample_org["id"], old["id"], claimed["token"], claimed["fence"], "test_cleanup",
    ) is True
    await _assert_certified_source(repo, sample_org["id"], old)


async def test_expired_reclaim_rejects_old_fail_without_disturbing_new_claim(repo, sample_org, pg_pool):
    _, old = await _publish_file(repo, sample_org["id"])
    key = uuid.uuid4()
    first = await _replacement_claim(repo, sample_org["id"], old["id"], key=key)
    await pg_pool.execute(
        "UPDATE documents SET index_lease_until=now()-interval '1 second' WHERE id=$1", old["id"],
    )
    second = await _replacement_claim(repo, sample_org["id"], old["id"], key=key)
    assert second["status"] == "claimed"
    assert second["fence"] > first["fence"]
    assert await repo.doc_repo.fail_index(
        sample_org["id"], old["id"], first["token"], first["fence"], "stale_failure",
    ) is False
    current = await repo.doc_repo.get_document(sample_org["id"], old["id"])
    assert current["index_token"] == second["token"]
    assert current["index_file_bytes"] == 30
    await repo.doc_repo.fail_index(sample_org["id"], old["id"], second["token"], second["fence"], "cleanup")
    await _assert_certified_source(repo, sample_org["id"], old)


async def test_disable_during_replacement_fences_job_and_never_reactivates(repo, sample_org):
    _, old = await _publish_file(repo, sample_org["id"])
    claim = await _replacement_claim(repo, sample_org["id"], old["id"])
    disabled = await repo.doc_repo.toggle_document_active(sample_org["id"], old["id"])
    assert disabled["is_active"] is False
    assert await repo.doc_repo.fail_index(
        sample_org["id"], old["id"], claim["token"], claim["fence"], "stale_failure",
    ) is False
    with pytest.raises(ValueError):
        await repo.doc_repo.publish_index(
            sample_org["id"], old["id"], claim["token"], claim["fence"],
            ["replacement"], [_vector()], {},
        )
    current = await repo.doc_repo.get_document(sample_org["id"], old["id"])
    assert current["is_active"] is False
    assert await repo.doc_repo.search_similar(sample_org["id"], _vector()) == []


async def test_delete_during_replacement_cannot_be_undone_by_late_failure(repo, sample_org):
    _, old = await _publish_file(repo, sample_org["id"])
    claim = await _replacement_claim(repo, sample_org["id"], old["id"])
    assert await repo.doc_repo.delete_document(sample_org["id"], old["id"]) == 1
    assert await repo.doc_repo.fail_index(
        sample_org["id"], old["id"], claim["token"], claim["fence"], "late_failure",
    ) is False
    assert await repo.doc_repo.get_document(sample_org["id"], old["id"]) is None


async def test_metadata_edit_cancels_pending_replacement(repo, sample_org):
    _, old = await _publish_file(repo, sample_org["id"])
    claim = await _replacement_claim(repo, sample_org["id"], old["id"])
    edited = await repo.update_document(sample_org["id"], old["id"], nome="operator-edited.pdf")
    assert edited["nome"] == "operator-edited.pdf"
    assert await repo.doc_repo.fail_index(
        sample_org["id"], old["id"], claim["token"], claim["fence"], "late_failure",
    ) is False
    with pytest.raises(ValueError):
        await repo.doc_repo.publish_index(
            sample_org["id"], old["id"], claim["token"], claim["fence"],
            ["replacement"], [_vector()], {},
        )
    current = await repo.doc_repo.get_document(sample_org["id"], old["id"])
    assert current["nome"] == "operator-edited.pdf"
    assert current["index_verified"] is False
    assert current["index_token"] is None


async def test_chunk_mutation_invalidates_certificate_and_fences_pending_job(repo, sample_org):
    _, old = await _publish_file(repo, sample_org["id"])
    claim = await _replacement_claim(repo, sample_org["id"], old["id"])
    await repo.doc_repo.add_chunk(sample_org["id"], old["id"], 8, "unauthorized chunk", _vector())
    assert await repo.doc_repo.fail_index(
        sample_org["id"], old["id"], claim["token"], claim["fence"], "late_failure",
    ) is False
    current = await repo.doc_repo.get_document(sample_org["id"], old["id"])
    assert current["index_verified"] is False
    assert current["index_token"] is None
    assert await repo.doc_repo.search_similar(sample_org["id"], _vector()) == []


async def test_pending_structured_data_claim_is_fenced_by_profile_edit(repo, sample_org, pg_pool):
    org_id = sample_org["id"]
    profile = {"name": "Hotel A", "prices": {"room": 100}}
    await pg_pool.execute("UPDATE organizations SET business_profile=$2::jsonb WHERE id=$1", org_id, json.dumps(profile))
    _, old = await _publish_file(
        repo, org_id, name="profile.json", tipo="dati_struttura",
        expected_profile=profile, file_bytes=0,
    )
    claim = await _replacement_claim(
        repo, org_id, old["id"], tipo="dati_struttura", expected_profile=profile,
        name="profile-new.json", file_bytes=0,
    )
    await pg_pool.execute(
        "UPDATE organizations SET business_profile=$2::jsonb WHERE id=$1",
        org_id, json.dumps({"name": "Hotel A", "prices": {"room": 110}}),
    )
    assert await repo.doc_repo.fail_index(
        org_id, old["id"], claim["token"], claim["fence"], "stale_profile",
    ) is False
    current = await repo.doc_repo.get_document(org_id, old["id"])
    assert current["index_token"] is None
    assert current["index_intent"] is None
    assert current["index_verified"] is False


async def test_entitlement_revocation_rejects_publish_but_keeps_old_certificate(repo, sample_org, pg_pool):
    _, old = await _publish_file(repo, sample_org["id"])
    claim = await _replacement_claim(repo, sample_org["id"], old["id"])
    await pg_pool.execute(
        "UPDATE organizations SET subscription_status='canceled', trial_end=now()-interval '1 day' WHERE id=$1",
        sample_org["id"],
    )
    with pytest.raises(ValueError, match="rag_not_authorized"):
        await repo.doc_repo.publish_index(
            sample_org["id"], old["id"], claim["token"], claim["fence"],
            ["replacement"], [_vector()], {},
        )
    assert await repo.doc_repo.fail_index(
        sample_org["id"], old["id"], claim["token"], claim["fence"], "rag_not_authorized",
    ) is True
    current = await repo.doc_repo.get_document(sample_org["id"], old["id"])
    assert current["revision"] == old["revision"]
    assert current["index_verified"] is True
    quota = await repo.doc_repo.file_quota_status(sample_org["id"])
    assert quota["file_quota"]["committed_bytes"] == 20
    assert quota["file_quota"]["reserved_bytes"] == 0


async def test_authenticated_and_anon_cannot_update_index_intent(pg_pool):
    for role in ("authenticated", "anon"):
        allowed = await pg_pool.fetchval(
            "SELECT has_column_privilege($1, 'public.documents', 'index_intent', 'UPDATE')",
            role,
        )
        assert allowed is False


async def test_downgrade_rejects_growth_but_preserves_old_retrieval_and_smaller_replacement(
    repo, sample_org, pg_pool, monkeypatch,
):
    from src.core.billing import plans
    from src.core.documenti.rag_context import recupera_contesto_documenti, require_current_context

    org_id = sample_org["id"]
    monkeypatch.setattr(plans.PLANS["business"], "rag_file_bytes_limit", 100)
    monkeypatch.setattr(plans.PLANS["starter"], "rag_file_bytes_limit", 50)
    await pg_pool.execute(
        """UPDATE organizations SET plan='business',subscription_status='active',
             subscription_id='sub_replacementTest',
             current_period_start=clock_timestamp()-interval '1 day',
             current_period_end=clock_timestamp()+interval '20 days',
             messages_limit=10000 WHERE id=$1""",
        org_id,
    )
    _, old = await _publish_file(repo, org_id, file_bytes=80)
    growth = await _replacement_claim(repo, org_id, old["id"], file_bytes=90)
    await pg_pool.execute("UPDATE organizations SET plan='starter' WHERE id=$1", org_id)

    with pytest.raises(ValueError, match="file_quota_exceeded"):
        await repo.doc_repo.publish_index(
            org_id, old["id"], growth["token"], growth["fence"],
            ["larger replacement"], [_vector()], {"label": "too-large"},
        )
    assert await repo.doc_repo.fail_index(
        org_id, old["id"], growth["token"], growth["fence"], "file_quota_exceeded",
    ) is True
    await _assert_certified_source(repo, org_id, old, bytes_count=80)
    after_failure = await repo.doc_repo.file_quota_status(org_id)
    assert after_failure["file_quota"]["committed_bytes"] == 80
    assert after_failure["file_quota"]["reserved_bytes"] == 0

    context = await recupera_contesto_documenti(
        str(org_id), "old certified menu", repo, q_emb=_vector(),
    )
    assert context.reason == "verified"
    assert any(item["document_id"] == old["id"] for item in context.chunks)
    await require_current_context(context, repo, org_id)

    smaller = await _replacement_claim(repo, org_id, old["id"], file_bytes=60)
    replacement = await repo.doc_repo.publish_index(
        org_id, old["id"], smaller["token"], smaller["fence"],
        ["smaller replacement"], [_vector()], {"label": "smaller"},
    )
    assert replacement["file_bytes"] == 60
    assert replacement["revision"] == old["revision"] + 1
    usage = await repo.doc_repo.file_quota_status(org_id)
    assert usage["file_quota"]["committed_bytes"] == 60
    assert usage["file_quota"]["occupied_bytes"] == 60


async def test_failed_partial_publication_rolls_back_to_previous_certificate(repo, sample_org, pg_pool):
    _, old = await _publish_file(repo, sample_org["id"])
    claim = await _replacement_claim(repo, sample_org["id"], old["id"])
    async with pg_pool.acquire() as conn:
        await conn.execute("""
            CREATE OR REPLACE FUNCTION reject_test_replacement_chunk() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
                IF NEW.content = 'synthetic publication fault' THEN
                    RAISE EXCEPTION 'synthetic publication fault';
                END IF;
                RETURN NEW;
            END $$;
            CREATE TRIGGER reject_test_replacement_chunk BEFORE INSERT ON document_chunks
            FOR EACH ROW EXECUTE FUNCTION reject_test_replacement_chunk();
        """)
    try:
        with pytest.raises(Exception, match="synthetic publication fault"):
            await repo.doc_repo.publish_index(
                sample_org["id"], old["id"], claim["token"], claim["fence"],
                ["new chunk", "synthetic publication fault"], [_vector(), _vector()],
                {"label": "replacement"},
            )
    finally:
        await pg_pool.execute("DROP TRIGGER IF EXISTS reject_test_replacement_chunk ON document_chunks")
        await pg_pool.execute("DROP FUNCTION IF EXISTS reject_test_replacement_chunk()")
    await _assert_certified_source(repo, sample_org["id"], old)
    quota = await repo.doc_repo.file_quota_status(sample_org["id"])
    assert quota["file_quota"]["committed_bytes"] == 20
    assert quota["file_quota"]["reserved_bytes"] == 10
