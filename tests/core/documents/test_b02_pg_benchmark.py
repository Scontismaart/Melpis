"""Disposable-PostgreSQL storage and retrieval measurements for B02.

All index content and vectors are deterministic synthetic fixtures. This test
does not call an embedding model or external service. It emits one
``B02_PG_BENCHMARK`` JSON line for CI to retain as a run receipt.
"""

import asyncio
import hashlib
import json
import math
import os
import statistics
import time
import uuid

import pytest


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
]

TIER_CHUNKS = (90, 900, 3600)
VECTOR_DIMS = 384
SEARCH_K = 10
SEARCH_RUNS = 20
CHUNK_CHARS = 500


@pytest.fixture(autouse=True)
async def _entitle_synthetic_organizations(pg_pool, sample_org, other_org):
    """Use only reset_db's disposable fixture tenants for billable plan state."""
    await pg_pool.execute(
        "UPDATE organizations SET plan='business', subscription_status='trialing', "
        "trial_start=now()-interval '1 hour', trial_end=now()+interval '6 days' "
        "WHERE id=ANY($1::uuid[])",
        [sample_org["id"], other_org["id"]],
    )


def _synthetic_chunks(org_id, tier, count):
    org_tag = str(org_id).replace("-", "")[:12]
    chunks = []
    for index in range(count):
        prefix = f"synthetic benchmark org={org_tag} tier={tier} chunk={index:04d}; "
        body = (
            "Fictional cafe menu sample for database throughput and retrieval. "
            "Coffee, tea, opening hours, and table reservations are generated data. "
        )
        text = (prefix + body * ((CHUNK_CHARS // len(body)) + 2))[:CHUNK_CHARS]
        chunks.append(text.ljust(CHUNK_CHARS, " "))
    return chunks


def _synthetic_vectors(org_id, count):
    """Finite, nonzero, deterministic 384-d vectors; no model inference."""
    tenant_seed = int(uuid.UUID(str(org_id))) % 100_003
    return [
        [((tenant_seed + (index + 1) * 104_729 + dim * 1_543) % 100_003 + 1) / 100_003.0
         for dim in range(VECTOR_DIMS)]
        for index in range(count)
    ]


def _percentiles(samples):
    values = sorted(samples)
    p95_rank = max(1, math.ceil(0.95 * len(values)))
    return {
        "p50_seconds": statistics.median(values),
        "p95_seconds": values[p95_rank - 1],
        "samples": len(values),
    }


async def _claim(repo, organization_id, name, payload):
    return await repo.doc_repo.begin_index(
        organization_id,
        name,
        str(uuid.uuid4()),
        hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        tipo="upload",
        fonte="b02-synthetic-pg-benchmark",
    )


async def _publish(repo, organization_id, name, tier, chunks, vectors):
    payload = f"b02-synthetic-fixture:{organization_id}:{tier}:{len(chunks)}"
    claim = await _claim(repo, organization_id, name, payload)
    assert claim["status"] == "claimed"
    wall_started = time.perf_counter()
    cpu_started = time.process_time()
    published = await repo.doc_repo.publish_index(
        organization_id,
        claim["document"]["id"],
        claim["token"],
        claim["fence"],
        chunks,
        vectors,
        {"benchmark": "b02-pg", "synthetic": True, "tier": tier},
    )
    publish_wall = time.perf_counter() - wall_started
    publish_cpu = time.process_time() - cpu_started
    assert published["stato"] == "indicizzata"
    assert published["index_verified"] is True
    return {
        "document_id": published["id"],
        "chunk_count": len(chunks),
        "publish_wall_seconds": publish_wall,
        "parent_process_cpu_seconds": publish_cpu,
    }


async def _tenant_storage(pg_pool, organization_id, document_ids):
    scoped = await pg_pool.fetchrow(
        """SELECT count(*) AS chunks,
                  COALESCE(sum(pg_column_size(content)
                             + pg_column_size(embedding)
                             + pg_column_size(metadata)), 0)::bigint AS payload_bytes
           FROM document_chunks
           WHERE organization_id=$1 AND document_id=ANY($2::uuid[])""",
        organization_id,
        document_ids,
    )
    # Relation size includes all tenants, indexes, and relation overhead; it is
    # a shared physical-size signal and must never be presented as tenant usage.
    shared_relation_bytes = await pg_pool.fetchval(
        "SELECT pg_total_relation_size('public.document_chunks'::regclass)"
    )
    return {
        "tenant_scoped_chunk_count": scoped["chunks"],
        "tenant_scoped_content_embedding_metadata_bytes": scoped["payload_bytes"],
        "shared_document_chunks_relation_total_bytes_all_tenants_not_tenant_billable": shared_relation_bytes,
    }


async def _timed_search(repo, organization_id, vector, allowed_document_ids):
    started = time.perf_counter()
    rows = await repo.doc_repo.search_similar(organization_id, vector, k=SEARCH_K)
    elapsed = time.perf_counter() - started
    result_ids = {str(row["document_id"]) for row in rows}
    assert result_ids <= allowed_document_ids, "search returned a document outside its tenant fixture scope"
    assert len(rows) == SEARCH_K
    return elapsed, len(rows), result_ids


async def test_b02_pg_vector_storage_retrieval_and_two_tenant_concurrency(
    repo, pg_pool, sample_org, other_org,
):
    org_a = sample_org["id"]
    org_b = other_org["id"]
    report = {
        "benchmark": "B02_PG_BENCHMARK",
        "source": "disposable PostgreSQL fixture; synthetic rows only",
        "tiers": [],
        "model_cpu_seconds": None,
        "database_cpu_seconds": None,
        "database_rss_bytes": None,
        "not_measured": [
            "model CPU (fixed deterministic vectors used)",
            "database server CPU and RSS (not observable from the test client)",
            "commercial tenant storage billing (shared relation size is not tenant billable)",
        ],
    }

    tenant_a_document_ids = set()
    tenant_a_query = _synthetic_vectors(org_a, 1)[0]
    for chunk_count in TIER_CHUNKS:
        tier = str(chunk_count)
        chunks = _synthetic_chunks(org_a, tier, chunk_count)
        vectors = _synthetic_vectors(org_a, chunk_count)
        claimed_at = time.perf_counter()
        claimed_cpu = time.process_time()
        published = await _publish(
            repo, org_a, f"b02-synthetic-{tier}.txt", tier, chunks, vectors,
        )
        claim_publish_elapsed = time.perf_counter() - claimed_at
        claim_publish_cpu = time.process_time() - claimed_cpu
        document_id = published["document_id"]
        tenant_a_document_ids.add(str(document_id))
        storage = await _tenant_storage(pg_pool, org_a, [document_id])
        assert storage["tenant_scoped_chunk_count"] == chunk_count
        assert storage["tenant_scoped_content_embedding_metadata_bytes"] > 0
        report["tiers"].append({
            "chunks_requested": chunk_count,
            "chunks_published": storage["tenant_scoped_chunk_count"],
            "publish_wall_seconds": published["publish_wall_seconds"],
            "publish_parent_process_cpu_seconds": published["parent_process_cpu_seconds"],
            "claim_plus_publish_wall_seconds": claim_publish_elapsed,
            "claim_plus_publish_parent_process_cpu_seconds": claim_publish_cpu,
            **storage,
        })

    # Give tenant B its own active search corpus, without duplicating the large
    # tier; both tenant reads therefore exercise a populated, certified index.
    tenant_b_chunks = _synthetic_chunks(org_b, "concurrency-base", 12)
    tenant_b_vectors = _synthetic_vectors(org_b, 12)
    tenant_b_base = await _publish(
        repo, org_b, "b02-synthetic-concurrency-base.txt", "concurrency-base",
        tenant_b_chunks, tenant_b_vectors,
    )
    tenant_b_document_ids = {str(tenant_b_base["document_id"])}
    tenant_b_query = tenant_b_vectors[0]

    search_samples = []
    search_result_count = None
    for _ in range(SEARCH_RUNS):
        elapsed, result_count, result_ids = await _timed_search(
            repo, org_a, tenant_a_query, tenant_a_document_ids,
        )
        assert result_ids <= tenant_a_document_ids
        search_samples.append(elapsed)
        search_result_count = result_count
    report["complete_index_search_similar"] = {
        **_percentiles(search_samples),
        "k": SEARCH_K,
        "result_count": search_result_count,
        "organization_scope_verified": True,
        "measurement_scope": "search_similar method wall time; includes entitlement lookup and vector SQL, not DB-only CPU",
    }

    concurrent_claims = {}
    for organization_id, name, count in (
        (org_a, "b02-synthetic-concurrent-a.txt", 3),
        (org_b, "b02-synthetic-concurrent-b.txt", 3),
    ):
        tier = f"concurrent-{str(organization_id)[:8]}"
        chunks = _synthetic_chunks(organization_id, tier, count)
        vectors = _synthetic_vectors(organization_id, count)
        claim = await _claim(
            repo, organization_id, name,
            f"b02-synthetic-fixture:{organization_id}:{tier}:{count}",
        )
        assert claim["status"] == "claimed"
        concurrent_claims[organization_id] = (claim, chunks, vectors, tier)

    # Include the newly claimed IDs before starting reads: depending on commit
    # scheduling, a concurrent search may see the publication or its prior view.
    tenant_a_document_ids.add(str(concurrent_claims[org_a][0]["document"]["id"]))
    tenant_b_document_ids.add(str(concurrent_claims[org_b][0]["document"]["id"]))

    start_gate = asyncio.Event()

    async def tenant_read_and_publish(organization_id, query_vector, allowed_ids):
        claim, chunks, vectors, tier = concurrent_claims[organization_id]
        await start_gate.wait()
        wall_started = time.perf_counter()
        cpu_started = time.process_time()
        query_task = asyncio.create_task(
            _timed_search(repo, organization_id, query_vector, allowed_ids),
        )
        publish_task = asyncio.create_task(repo.doc_repo.publish_index(
            organization_id,
            claim["document"]["id"],
            claim["token"],
            claim["fence"],
            chunks,
            vectors,
            {"benchmark": "b02-pg", "synthetic": True, "tier": tier},
        ))
        (elapsed, result_count, result_ids), published = await asyncio.gather(query_task, publish_task)
        assert result_count == SEARCH_K
        assert result_ids <= allowed_ids
        assert published["index_verified"] is True
        return {
            "query_wall_seconds": elapsed,
            "result_count": result_count,
            "tenant_scope_verified": True,
            "small_publication_chunks": len(chunks),
            "small_publication_state": published["stato"],
            "combined_parent_process_cpu_seconds": time.process_time() - cpu_started,
            "combined_tenant_read_and_publication_wall_seconds": time.perf_counter() - wall_started,
            "document_id": str(published["id"]),
        }

    concurrent_started = time.perf_counter()
    concurrent_cpu_started = time.process_time()
    tasks = [
        asyncio.create_task(tenant_read_and_publish(
            org_a, tenant_a_query, tenant_a_document_ids,
        )),
        asyncio.create_task(tenant_read_and_publish(
            org_b, tenant_b_query, tenant_b_document_ids,
        )),
    ]
    start_gate.set()
    concurrent_results = await asyncio.gather(*tasks)
    tenant_a_document_ids.add(concurrent_results[0]["document_id"])
    tenant_b_document_ids.add(concurrent_results[1]["document_id"])
    report["two_tenant_concurrent_reads_and_small_publications"] = {
        "tenant_count": 2,
        "small_publication_chunks_per_tenant": 3,
        "wall_seconds": time.perf_counter() - concurrent_started,
        "parent_process_cpu_seconds": time.process_time() - concurrent_cpu_started,
        "tenant_a": concurrent_results[0],
        "tenant_b": concurrent_results[1],
        "cross_tenant_result_count": 0,
        "tenant_scope_assertions_passed": True,
    }

    print("B02_PG_BENCHMARK " + json.dumps(report, sort_keys=True, default=str), flush=True)
    if os.getenv("CI"):
        from pathlib import Path
        target = Path(".runtime/reports/b02-pg-benchmark.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
