"""Bounded synthetic PDF -> shared embedding -> pgvector benchmark.

Only the explicitly named disposable database and the private embedding API
are accessed. No provider, WhatsApp, external storage, migrations, or Docker
control operations are part of this harness.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
import traceback
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DB_NAMES = {"melpis_inf01_mixed_20261025", "melpis_inf01_mixed_20261025_b",
            "melpis_inf01_mixed_20261025_c", "melpis_inf01_mixed_20261025_d"}
DB_HOST_PORTS = {("melpis-cycle-pg-20261025", 5432), ("127.0.0.1", 55495)}
EMBEDDING_HOSTS = {"embedding", "melpis-embedding", "localhost", "127.0.0.1"}
SYNTHETIC_PLAN_BYTES = 64 * 1024 * 1024


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, min(len(ordered) - 1, math.ceil(p * len(ordered)) - 1))], 6)


def _summary(values: list[float]) -> dict[str, Any]:
    return {
        "samples": len(values),
        "p50_seconds": _percentile(values, 0.50),
        "p95_seconds": _percentile(values, 0.95),
        "p99_seconds": _percentile(values, 0.99),
        "mean_seconds": round(statistics.mean(values), 6) if values else None,
    }


def _safe_failure_trace(exc: BaseException) -> list[dict[str, Any]]:
    """Return locations only; never serialize exception text or source lines."""
    return [
        {"file": Path(frame.filename).name, "function": frame.name, "line": frame.lineno}
        for frame in traceback.extract_tb(exc.__traceback__)[-16:]
    ]


def _synthetic_pdf(text: str) -> bytes:
    """Generate a one-page, selectable-text PDF in memory with PyMuPDF."""
    import fitz

    doc = fitz.open()
    try:
        page = doc.new_page(width=595, height=842)
        page.insert_text((54, 70), "MELPIS synthetic benchmark fixture", fontsize=14)
        # Keep the content synthetic, short, and comfortably inside one page.
        page.insert_textbox(fitz.Rect(54, 105, 540, 780), text, fontsize=11, lineheight=1.3)
        return doc.tobytes(garbage=4, deflate=True)
    finally:
        doc.close()


def _activities(size: int) -> tuple[list[str], list[str], list[int], list[str]]:
    fixture_path = ROOT / "scripts" / "benchmark_inf01.py"
    spec = importlib.util.spec_from_file_location("inf01_mixed_synthetic_fixtures", fixture_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("synthetic_fixture_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._activities(size)


def _database_target(dsn: str) -> tuple[str, int, str]:
    parsed = urlparse(dsn)
    try:
        host, port = parsed.hostname, parsed.port
    except ValueError:
        raise RuntimeError("database_dsn_invalid") from None
    database = parsed.path.lstrip("/")
    if (parsed.scheme not in {"postgres", "postgresql"}
            or (host, port) not in DB_HOST_PORTS
            or database not in DB_NAMES
            or parsed.username is None
            or parsed.password is None
            or parsed.query or parsed.fragment):
        raise RuntimeError("database_target_not_allowlisted")
    return host, port, database


def _embedding_target(url: str) -> str:
    parsed = urlparse(url.rstrip("/"))
    try:
        port = parsed.port
    except ValueError:
        raise RuntimeError("embedding_url_invalid") from None
    if (parsed.scheme != "http" or parsed.hostname not in EMBEDDING_HOSTS
            or port is None or not 1 <= port <= 65535
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}):
        raise RuntimeError("embedding_url_not_internal")
    return url.rstrip("/")


def _environment() -> tuple[str, str, str]:
    dsn = os.environ.get("INF01_TEST_DSN", "")
    _database_target(dsn)
    embedding_url = _embedding_target(os.environ.get("MELPIS_EMBEDDING_URL", ""))
    token = os.environ.get("MELPIS_EMBEDDING_TOKEN", "")
    if (not 32 <= len(token) <= 256
            or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-" for char in token)):
        raise RuntimeError("embedding_token_invalid")
    if os.environ.get("MELPIS_EMBEDDING_BACKEND", "").strip().lower() != "shared":
        raise RuntimeError("shared_embedding_backend_required")
    if os.environ.get("MELPIS_EMBEDDING_ENGINE", "").strip().lower() != "onnx":
        raise RuntimeError("onnx_embedding_engine_required")
    return dsn, embedding_url, token


async def _check_embedding_service(base_url: str, token: str) -> dict[str, Any]:
    from src.core.documenti.embedding_contract import ARTIFACTS, DIMENSION, MODEL_ID, REVISION, SPACE_ID, strict_json

    async with httpx.AsyncClient(
        base_url=base_url, headers={"Authorization": f"Bearer {token}"},
        timeout=httpx.Timeout(5, connect=2), trust_env=False,
    ) as client:
        response = await client.get("/health")
    if response.status_code != 200:
        raise RuntimeError("embedding_service_not_ready")
    try:
        health = strict_json(response.content)
    except ValueError:
        raise RuntimeError("embedding_health_invalid") from None
    expected = {
        "ready": True, "model_id": MODEL_ID, "revision": REVISION,
        "embedding_space": SPACE_ID, "dimension": DIMENSION,
        "artifact_sha256": ARTIFACTS["onnx"],
    }
    if any(health.get(key) != value for key, value in expected.items()):
        raise RuntimeError("embedding_fingerprint_mismatch")
    return {key: health.get(key) for key in (
        "ready", "model_id", "revision", "embedding_space", "dimension", "artifact_sha256",
    )}


async def _preflight_database(pool, expected_database: str) -> dict[str, Any]:
    async with pool.acquire() as conn:
        identity = await conn.fetchrow("SELECT current_database() AS db, current_schema() AS schema")
        if identity["db"] not in DB_NAMES or identity["db"] != expected_database or identity["schema"] != "public":
            raise RuntimeError("database_identity_mismatch")
        required = await conn.fetchval("""
            SELECT to_regclass('public.organizations') IS NOT NULL
               AND to_regclass('public.documents') IS NOT NULL
               AND to_regclass('public.document_chunks') IS NOT NULL
               AND to_regclass('public.document_index_requests') IS NOT NULL
               AND EXISTS(SELECT 1 FROM information_schema.columns
                           WHERE table_schema='public' AND table_name='documents'
                             AND column_name='index_verified')
               AND EXISTS(SELECT 1 FROM information_schema.columns
                           WHERE table_schema='public' AND table_name='documents'
                             AND column_name='index_file_bytes')
               AND EXISTS(SELECT 1 FROM information_schema.columns
                           WHERE table_schema='public' AND table_name='documents'
                             AND column_name='file_sha256')
        """)
        if not required:
            raise RuntimeError("database_schema_missing")
        vector_version = await conn.fetchval("SELECT extversion FROM pg_extension WHERE extname='vector'")
        if vector_version is None:
            raise RuntimeError("pgvector_extension_missing")
        vector_operator = await conn.fetchval("SELECT '[1,0]'::vector <=> '[1,0]'::vector")
        if vector_operator != 0:
            raise RuntimeError("pgvector_operator_unavailable")
        org_count = await conn.fetchval("SELECT count(*) FROM public.organizations")
        document_count = await conn.fetchval("SELECT count(*) FROM public.documents")
        chunk_count = await conn.fetchval("SELECT count(*) FROM public.document_chunks")
        if (org_count, document_count, chunk_count) != (0, 0, 0):
            raise RuntimeError("target_database_not_empty")
        backend = await conn.fetchval("SELECT pg_backend_pid()")
    return {
        "database": identity["db"], "schema": "public", "pgvector_version": vector_version,
        "initial_organization_count": int(org_count),
        "initial_document_count": int(document_count),
        "initial_chunk_count": int(chunk_count),
        "benchmark_backend_pid": int(backend),
    }


async def _create_fixture_org(pool, org_id, *, entitled=True) -> None:
    async with pool.acquire() as conn:
        if entitled:
            await conn.execute("""
            INSERT INTO organizations
                (id,name,plan,subscription_status,subscription_id,
                 current_period_start,current_period_end,ai_accounting_blocked,messages_limit)
            VALUES($1,$2,'business','active',$3,now()-interval '1 hour',
                   now()+interval '7 days',FALSE,10000)
        """, org_id, f"MELPIS INF01 synthetic benchmark {str(org_id)[:8]}",
             f"sub_inf01mixed{str(org_id).replace('-', '')[:12]}")
        else:
            await conn.execute("""
                INSERT INTO organizations
                    (id,name,plan,subscription_status,ai_accounting_blocked)
                VALUES($1,$2,NULL,'incomplete',FALSE)
            """, org_id, f"MELPIS INF01 synthetic no-access {str(org_id)[:8]}")


async def _benchmark_tier(pool, repo, org_id, cross_org_id, no_access_org_id,
                          size: int, timeout_seconds: float) -> dict[str, Any]:
    from src.core.documenti import indexing

    queries, passages, expected_indices, languages = _activities(size)
    extract_latencies: list[float] = []
    passage_embed_latencies: list[float] = []
    publish_latencies: list[float] = []
    index_latencies: list[float] = []
    query_embed_latencies: list[float] = []
    pg_search_latencies: list[float] = []
    query_total_latencies: list[float] = []
    pdf_sizes: list[int] = []
    pdf_digests: list[str] = []
    extract_page_counts: list[int] = []
    extract_char_counts: list[int] = []
    indexed_doc_ids: list[Any] = []
    query_vectors: list[list[float] | None] = [None] * size
    extracted_pages = 0
    extracted_characters = 0
    tenant_scope_verified = True
    first_request_replay: dict[str, Any] | None = None
    current_activity_index = -1
    later_pdf_extraction_started = asyncio.Event()
    later_extraction_started_at = None
    later_extraction_finished_at = None
    early_query_started_at = None
    early_query_finished_at = None
    early_query_record: dict[str, Any] | None = None
    early_query_task: asyncio.Task | None = None

    original_extract = indexing.extract_local
    original_embed = indexing.embed_local
    from src.core.documenti import isolated
    original_isolated_embed = isolated.embed_local
    original_publish = repo.doc_repo.publish_index

    async def timed_extract(*args, **kwargs):
        started = time.perf_counter()
        nonlocal current_activity_index, later_extraction_started_at, later_extraction_finished_at
        if current_activity_index == 1:
            extraction = asyncio.create_task(original_extract(*args, **kwargs))
            # Let extract_local launch its isolated worker before releasing the
            # query task; the query and this real PDF extraction then run on
            # overlapping async tasks.
            await asyncio.sleep(0.02)
            later_extraction_started_at = time.perf_counter()
            later_pdf_extraction_started.set()
            result = await extraction
            later_extraction_finished_at = time.perf_counter()
        else:
            result = await original_extract(*args, **kwargs)
        extract_latencies.append(time.perf_counter() - started)
        nonlocal extracted_pages, extracted_characters
        extracted_pages += int(result.get("pages", 0))
        extracted_characters += sum(len(text) for text in result.get("page_texts", []))
        extract_page_counts.append(int(result.get("pages", 0)))
        extract_char_counts.append(sum(len(text) for text in result.get("page_texts", [])))
        return result

    async def timed_embed(texts, tipo="passage", **kwargs):
        started = time.perf_counter()
        result = await original_isolated_embed(texts, tipo, **kwargs)
        elapsed = time.perf_counter() - started
        (query_embed_latencies if tipo == "query" else passage_embed_latencies).append(elapsed)
        return result

    async def timed_publish(*args, **kwargs):
        started = time.perf_counter()
        result = await original_publish(*args, **kwargs)
        publish_latencies.append(time.perf_counter() - started)
        return result

    indexing.extract_local = timed_extract
    indexing.embed_local = timed_embed
    isolated.embed_local = timed_embed
    repo.doc_repo.publish_index = timed_publish
    fixture_started = time.perf_counter()
    parent_cpu_start = time.process_time()
    try:
        async with asyncio.timeout(timeout_seconds):
            for index, (query, passage) in enumerate(zip(queries, passages)):
                current_activity_index = index
                content = _synthetic_pdf(
                    f"Synthetic activity {index + 1}. Language {languages[index]}. "
                    f"Query: {query}\n\nVerified synthetic information: {passage}\n\n"
                    "This fictional page is generated only for a disposable benchmark."
                )
                pdf_sizes.append(len(content))
                pdf_digests.append(hashlib.sha256(content).hexdigest())
                request_key = uuid4()
                started = time.perf_counter()
                result = await indexing.index_content(
                    repo, org_id, f"synthetic-activity-{index + 1:04d}.pdf",
                    request_key=request_key, file_content=content, content_type="application/pdf",
                    tipo="upload", fonte="inf01-synthetic-benchmark",
                    metadata={"benchmark": "inf01-mixed", "synthetic": True},
                )
                index_latencies.append(time.perf_counter() - started)
                document = result.get("document") or {}
                if (result.get("status") != "indexed" or document.get("index_verified") is not True
                        or document.get("stato") != "indicizzata"):
                    raise RuntimeError("synthetic_index_not_certified")
                indexed_doc_ids.append(document["id"])

                if index == 0:
                    passage_calls_before_replay = len(passage_embed_latencies)
                    replay = await indexing.index_content(
                        repo, org_id, f"synthetic-activity-{index + 1:04d}.pdf",
                        request_key=request_key, file_content=content, content_type="application/pdf",
                        tipo="upload", fonte="inf01-synthetic-benchmark",
                        metadata={"benchmark": "inf01-mixed", "synthetic": True},
                    )
                    first_request_replay = {
                        "status": replay.get("status"),
                        "same_document_id": str((replay.get("document") or {}).get("id")) == str(document["id"]),
                        "additional_passage_embedding_requests": len(passage_embed_latencies) - passage_calls_before_replay,
                    }

                if index == 0 and size > 1:
                    async def retrieve_first_during_next_index() -> None:
                        nonlocal early_query_started_at, early_query_finished_at, early_query_record
                        early_query_started_at = time.perf_counter()
                        query_started = early_query_started_at
                        vectors = await isolated.embed_local(
                            [queries[0]], "query", organization_id=str(org_id),
                            timeout=timeout_seconds,
                        )
                        query_embedded_at = time.perf_counter()
                        await later_pdf_extraction_started.wait()
                        search_started = time.perf_counter()
                        rows = await repo.doc_repo.search_similar(
                            org_id, vectors[0], k=1,
                        )
                        early_query_finished_at = time.perf_counter()
                        query_total_latencies.append(early_query_finished_at - query_started)
                        pg_search_latencies.append(early_query_finished_at - search_started)
                        query_vectors[0] = vectors[0]
                        early_query_record = {
                            "queried_published_document_id": str(indexed_doc_ids[0]),
                            "returned_document_ids": [str(row["document_id"]) for row in rows],
                            "first_document_retrieved": bool(rows and str(rows[0]["document_id"]) == str(indexed_doc_ids[0])),
                            "query_to_search_total_seconds": round(early_query_finished_at - query_started, 6),
                            "query_embedding_seconds": round(query_embedded_at - query_started, 6),
                            "pgvector_search_seconds": round(early_query_finished_at - search_started, 6),
                            "wait_for_next_pdf_extraction_seconds": round(search_started - query_embedded_at, 6),
                        }

                    early_query_task = asyncio.create_task(retrieve_first_during_next_index())

            if early_query_task is not None:
                await early_query_task

            from src.core.documenti.isolated import embed_local

            # A small, bounded two-request query fan-out exercises shared
            # inference and tenant-scoped pgvector reads after indexing.
            semaphore = asyncio.Semaphore(2)
            ranks: list[int] = []
            retrieval_results: Counter[str] = Counter()

            async def retrieve(index: int) -> None:
                nonlocal tenant_scope_verified
                async with semaphore:
                    query_started = time.perf_counter()
                    vectors = await embed_local(
                        [queries[index]], "query", organization_id=str(org_id),
                        timeout=timeout_seconds,
                    )
                    query_vectors[index] = vectors[0]
                    query_total_latencies.append(time.perf_counter() - query_started)
                    pg_started = time.perf_counter()
                    rows = await repo.doc_repo.search_similar(
                        org_id, vectors[0], k=min(20, size),
                    )
                    pg_search_latencies.append(time.perf_counter() - pg_started)
                    found_ids = [str(row["document_id"]) for row in rows]
                    if not set(found_ids).issubset({str(doc) for doc in indexed_doc_ids}):
                        tenant_scope_verified = False
                    wanted = str(indexed_doc_ids[expected_indices[index]])
                    rank = found_ids.index(wanted) + 1 if wanted in found_ids else size + 1
                    ranks.append(rank)
                    retrieval_results["top1"] += int(rank == 1)
                    retrieval_results["recall5"] += int(rank <= min(5, size))

            await asyncio.gather(*(retrieve(index) for index in range(size)))
    finally:
        indexing.extract_local = original_extract
        indexing.embed_local = original_embed
        isolated.embed_local = original_isolated_embed
        repo.doc_repo.publish_index = original_publish

    elapsed = time.perf_counter() - fixture_started
    parent_cpu_seconds = time.process_time() - parent_cpu_start
    chunk_count = await repo.doc_repo.count_chunks(org_id)
    storage = await pool.fetchrow("""
        SELECT count(*) AS chunks,
               COALESCE(sum(pg_column_size(content)+pg_column_size(embedding)+pg_column_size(metadata)),0)::bigint AS payload_bytes
          FROM document_chunks
         WHERE organization_id=$1
    """, org_id)
    # Keep an independently indexed tenant corpus beside the primary corpus;
    # the third organization is intentionally unentitled and remains empty.
    cross_payload = _synthetic_pdf(
        "Synthetic cross-tenant canary. "
        f"Other organization only: {passages[0]}\n\n"
        "This fictional PDF belongs to another synthetic organization."
    )
    cross_index_started = time.perf_counter()
    cross_index = await indexing.index_content(
        repo, cross_org_id, "synthetic-cross-tenant-canary.pdf",
        request_key=uuid4(), file_content=cross_payload, content_type="application/pdf",
        tipo="upload", fonte="inf01-synthetic-cross-tenant",
        metadata={"benchmark": "inf01-mixed", "synthetic": True, "role": "cross-tenant-canary"},
    )
    cross_index_seconds = time.perf_counter() - cross_index_started
    cross_document = cross_index["document"]
    cross_vector = query_vectors[0]
    if cross_vector is None:
        raise RuntimeError("synthetic_query_vector_missing")
    primary_cross_results = await repo.doc_repo.search_similar(org_id, cross_vector, k=min(20, size))
    cross_own_results = await repo.doc_repo.search_similar(cross_org_id, cross_vector, k=20)
    no_access_results = await repo.doc_repo.search_similar(no_access_org_id, cross_vector, k=20)
    from fastapi import HTTPException
    try:
        await indexing.index_content(
            repo, no_access_org_id, "synthetic-denied.txt", text="Synthetic denied access probe.",
            content_type="text/plain", tipo="upload", fonte="inf01-synthetic-denied",
        )
        no_access_index_status = "unexpectedly_indexed"
    except HTTPException as exc:
        no_access_index_status = "denied_403" if exc.status_code == 403 else f"http_{exc.status_code}"
    cross_tenant_verified = (
        all(str(row["document_id"]) in {str(doc) for doc in indexed_doc_ids} for row in primary_cross_results)
        and all(str(row["document_id"]) == str(cross_document["id"]) for row in cross_own_results)
        and bool(cross_own_results)
        and not no_access_results
        and no_access_index_status == "denied_403"
    )

    from src.models.schemas import MessaggioInput, ProfiloAttivita, RispostaOutput
    from src.core.guardrails.validator import applica_guardrail, valida_risposta
    from src.core import crew_runner
    from types import SimpleNamespace
    from unittest.mock import patch
    from tests.provider_accounting_fakes import provider_context_stub

    responder_rows = await repo.doc_repo.search_similar(org_id, cross_vector, k=min(20, size))
    responder_context = "\n\n".join(row["content"] for row in responder_rows)
    responder_capture: dict[str, Any] = {}

    class DeterministicCrew:
        async def kickoff_async(self):
            return SimpleNamespace(pydantic=RispostaOutput(
                risposta="Informazione sintetica disponibile nel documento.",
                richiede_umano=False, motivo="", categoria="generico",
            ))

    def deterministic_crew_factory(*_args, **kwargs):
        responder_capture["context"] = kwargs.get("contesto_documenti", "")
        return DeterministicCrew()

    synthetic_profile = ProfiloAttivita(
        nome="Synthetic cafe", tipo_attivita="ristorante", tono="cordiale",
        orari="Solo dati fittizi di benchmark.",
    )
    synthetic_message = MessaggioInput(testo=queries[0])
    synthetic_provider_context = provider_context_stub(
        task_type="customer_message", organization_id=org_id,
    )
    responder_started = time.perf_counter()
    with patch.object(crew_runner, "crea_crew", side_effect=deterministic_crew_factory):
        responder_output = await crew_runner.genera_risposta_async(
            synthetic_message, synthetic_profile,
            contesto_documenti=responder_context,
            provider_context=synthetic_provider_context,
        )
    responder_seconds = time.perf_counter() - responder_started
    guardrail = valida_risposta(
        responder_output,
        contesto_chunks=responder_rows,
        profilo=synthetic_profile,
    )
    guarded_output = applica_guardrail(responder_output, guardrail)
    responder_stub = {
        "runner": "genera_risposta_async",
        "deterministic_crew_stub_calls": 1,
        "provider_calls": 0,
        "context_passed_to_responder": bool(responder_capture.get("context")),
        "retrieved_context_characters": len(responder_capture.get("context", "")),
        "schema_output_validated": isinstance(responder_output, RispostaOutput),
        "guardrail_action": guardrail.azione,
        "guardrail_blocked_output": bool(getattr(guarded_output, "_guardrail_blocked", False)),
        "wall_seconds": round(responder_seconds, 6),
    }
    overlap_seconds = None
    if (early_query_started_at is not None and early_query_finished_at is not None
            and later_extraction_started_at is not None and later_extraction_finished_at is not None):
        overlap_seconds = max(
            0.0,
            min(early_query_finished_at, later_extraction_finished_at)
            - max(early_query_started_at, later_extraction_started_at),
        )
    return {
        "activities": size,
        "synthetic_languages": {key: languages.count(key) for key in sorted(set(languages))},
        "pdf_extraction": {
            "pages_extracted": extracted_pages,
            "characters_extracted": extracted_characters,
            "actual_extractor_process_per_pdf": True,
            "latency": _summary(extract_latencies),
            "per_pdf_page_counts": extract_page_counts,
            "per_pdf_character_counts": extract_char_counts,
            "pdf_bytes_total": sum(pdf_sizes),
            "pdf_bytes_mean": round(statistics.mean(pdf_sizes), 1) if pdf_sizes else None,
            "synthetic_pdf_sha256": pdf_digests,
        },
        "shared_embedding": {
            "passage_requests": len(passage_embed_latencies),
            "query_requests": len(query_embed_latencies),
            "passage_latency": _summary(passage_embed_latencies),
            "query_latency": _summary(query_embed_latencies),
            "query_embedding_plus_wait_latency": _summary(query_total_latencies),
            "model_cpu_seconds": None,
            "model_rss_bytes": None,
        },
        "index_pipeline": {
            "successful_documents": len(indexed_doc_ids),
            "verified_chunks": int(chunk_count),
            "full_index_latency": _summary(index_latencies),
            "pg_publish_transaction_latency": _summary(publish_latencies),
            "activities_per_second": round(size / elapsed, 4) if elapsed else None,
            "wall_seconds": round(elapsed, 6),
            "parent_process_cpu_seconds": round(parent_cpu_seconds, 6),
            "parent_process_peak_rss_bytes": _parent_peak_rss_bytes(),
        },
        "pgvector_retrieval": {
            "searches": len(ranks),
            "latency": _summary(pg_search_latencies),
            "top1_accuracy": round(retrieval_results["top1"] / len(ranks), 4) if ranks else None,
            "recall_at_5": round(retrieval_results["recall5"] / len(ranks), 4) if ranks else None,
            "mean_reciprocal_rank": round(sum(1 / rank for rank in ranks) / len(ranks), 4) if ranks else None,
            "ranked_results": {"rank_1": sum(rank == 1 for rank in ranks),
                               "rank_2_to_5": sum(2 <= rank <= 5 for rank in ranks),
                               "rank_6_or_missing": sum(rank > 5 for rank in ranks)},
            "tenant_scope_verified": tenant_scope_verified,
            "tenant_scoped_chunk_count": int(storage["chunks"]),
            "tenant_scoped_payload_bytes": int(storage["payload_bytes"]),
            "search_cpu_seconds": None,
        },
        "mixed_concurrency": {
            "early_query_started_after_first_publication": bool(early_query_record),
            "query_overlapped_actual_next_pdf_extraction": bool(overlap_seconds and overlap_seconds > 0),
            "early_query_and_next_extraction_overlap_seconds": round(overlap_seconds, 6) if overlap_seconds is not None else None,
            "early_query_sample": early_query_record,
            "next_document_index_latency_seconds": round(index_latencies[1], 6) if len(index_latencies) > 1 else None,
        },
        "cross_tenant_isolation": {
            "primary_organization_id": str(org_id),
            "other_organization_id": str(cross_org_id),
            "unentitled_organization_id": str(no_access_org_id),
            "other_organization_canary_document_id": str(cross_document["id"]),
            "primary_search_excludes_other_organization": all(
                str(row["document_id"]) != str(cross_document["id"]) for row in primary_cross_results
            ),
            "other_organization_search_returns_only_own_canary": bool(cross_own_results) and all(
                str(row["document_id"]) == str(cross_document["id"]) for row in cross_own_results
            ),
            "unentitled_organization_search_results": len(no_access_results),
            "unentitled_organization_index_status": no_access_index_status,
            "verified": cross_tenant_verified,
            "cross_tenant_canary_index_seconds": round(cross_index_seconds, 6),
        },
        "retained_fixture_ids": {
            "primary_documents": [str(document_id) for document_id in indexed_doc_ids],
            "cross_tenant_document": str(cross_document["id"]),
        },
        "first_request_replay": first_request_replay,
        "deterministic_responder": responder_stub,
        "scope_note": "PDF extraction, embedding HTTP inference, fenced document publication, and pgvector retrieval were measured; no WhatsApp webhook or LLM/provider path ran.",
    }


def _parent_peak_rss_bytes() -> int | None:
    try:
        import resource
        value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return value * (1024 if sys.platform.startswith("linux") else 1)
    except (ImportError, OSError, ValueError):
        return None


async def _run_existing_meta_ack_unit_test() -> dict[str, Any]:
    """Run the repository's existing route ACK unit test, without conftests.

    ``tests/whatsapp/conftest.py`` has an autouse fixture that truncates a test
    database. ``--noconftest`` is mandatory here: this delegated unit test uses
    its module-local mocked repository and must never touch any database.
    """
    command = [
        sys.executable, "-m", "pytest", "-q", "--noconftest", "--disable-warnings", "--no-header",
        "tests/whatsapp/test_router.py::TestRouter::test_post_inbound_message",
    ]
    allow_env = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL", "VIRTUAL_ENV", "PYTHONHOME"}
    child_env = {key: value for key, value in os.environ.items() if key.upper() in allow_env}
    child_env["PYTHONPATH"] = str(ROOT)
    child_env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    started = time.perf_counter()
    try:
        process = await asyncio.create_subprocess_exec(
            *command, cwd=str(ROOT), env=child_env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        output, _ = await asyncio.wait_for(process.communicate(), timeout=120)
    except FileNotFoundError:
        return {"status": "unavailable", "reason": "pytest_runtime_unavailable"}
    except asyncio.TimeoutError:
        try:
            process.kill()
            await process.wait()
        except Exception:
            pass
        return {"status": "timeout", "duration_seconds": round(time.perf_counter() - started, 6)}
    return {
        "status": "passed" if process.returncode == 0 else "failed",
        "test": "tests/whatsapp/test_router.py::TestRouter::test_post_inbound_message",
        "return_code": process.returncode,
        "duration_seconds": round(time.perf_counter() - started, 6),
        "output_bytes": len(output),
        "scope": "Existing signed-route test with in-memory mocked repository; no Meta network, database, responder, or LLM/provider call.",
    }


async def _final_row_counts(pool) -> dict[str, int]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT (SELECT count(*) FROM organizations) AS organizations,
                   (SELECT count(*) FROM documents) AS documents,
                   (SELECT count(*) FROM document_chunks) AS chunks,
                   (SELECT count(*) FROM document_index_requests) AS index_requests
        """)
    return {key: int(row[key]) for key in ("organizations", "documents", "chunks", "index_requests")}


async def _run(args) -> dict[str, Any]:
    import asyncpg
    from src.core.billing import plans
    from src.core.db.repository import CoreRepository

    dsn, embedding_url, token = _environment()
    target = _database_target(dsn)
    health = await _check_embedding_service(embedding_url, token)
    pool = await asyncpg.create_pool(
        dsn=dsn, min_size=1, max_size=4, command_timeout=20,
        server_settings={"search_path": "public,extensions"},
    )
    org_ids = []
    meta_ack_task = None
    report: dict[str, Any] = {
        "benchmark": "INF01_MIXED_SYNTHETIC_PDF_PGVECTOR",
        "status": "running",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "database_target": {"host": target[0], "port": target[1], "database": target[2]},
        "embedding_fingerprint": health,
        "tiers": {},
        "provider_calls": 0,
        "whatsapp_webhook_calls": 0,
        "limitations": [
            "Synthetic PDF fixtures and synthetic organizations only.",
            "No real PDF, tenant, WhatsApp, provider, or LLM data or calls.",
            "Model CPU/RSS and PostgreSQL server CPU/RSS are not read from the HTTP or SQL interfaces; collect those separately from the container runtime.",
            "The delegated Meta ACK unit test uses a mocked repository and is not a live Meta or WhatsApp E2E test.",
        ],
    }
    try:
        report["database_preflight"] = await _preflight_database(pool, target[2])
        # Benchmark-only in-memory commercial allowance: the synthetic test
        # organization is entitled solely for this run. No plan defaults change.
        plans.PLANS["business"].rag_file_bytes_limit = SYNTHETIC_PLAN_BYTES
        repo = CoreRepository(pool)
        meta_ack_task = asyncio.create_task(_run_existing_meta_ack_unit_test())
        for size in args.sizes:
            org_id = uuid4()
            cross_org_id = uuid4()
            no_access_org_id = uuid4()
            org_ids.extend((org_id, cross_org_id, no_access_org_id))
            await _create_fixture_org(pool, org_id)
            await _create_fixture_org(pool, cross_org_id)
            await _create_fixture_org(pool, no_access_org_id, entitled=False)
            report["tiers"][str(size)] = await _benchmark_tier(
                pool, repo, org_id, cross_org_id, no_access_org_id,
                size, args.tier_timeout,
            )
        if meta_ack_task is not None:
            report["existing_meta_ack_unit_test"] = await meta_ack_task
        report["verification_checks"] = {
            "concurrent_query_during_next_extraction": all(
                tier.get("mixed_concurrency", {}).get("query_overlapped_actual_next_pdf_extraction")
                for tier in report["tiers"].values()
            ),
            "first_request_idempotent_replay": all(
                (tier.get("first_request_replay") or {}).get("status") == "replay"
                and (tier.get("first_request_replay") or {}).get("same_document_id") is True
                and (tier.get("first_request_replay") or {}).get("additional_passage_embedding_requests") == 0
                for tier in report["tiers"].values()
            ),
            "tenant_isolation_and_no_access": all(
                tier.get("cross_tenant_isolation", {}).get("verified") is True
                for tier in report["tiers"].values()
            ),
            "deterministic_responder_and_guardrail": all(
                tier.get("deterministic_responder", {}).get("schema_output_validated") is True
                and tier.get("deterministic_responder", {}).get("guardrail_blocked_output") is False
                and tier.get("deterministic_responder", {}).get("provider_calls") == 0
                for tier in report["tiers"].values()
            ),
        }
        report["status"] = "complete"
    except Exception as exc:
        # Exception text is deliberately excluded to avoid leaking connection
        # details, service responses, or synthetic PDF content into the receipt.
        report["status"] = "failed"
        report["failure_type"] = type(exc).__name__
        report["failure_code"] = str(exc) if str(exc).isidentifier() else "benchmark_failed"
        report["failure_trace"] = _safe_failure_trace(exc)
    finally:
        if meta_ack_task is not None and not meta_ack_task.done():
            report["existing_meta_ack_unit_test"] = await meta_ack_task
        report["fixture_organizations_retained"] = [str(org_id) for org_id in org_ids]
        report["synthetic_rows_retained_for_inspection"] = True
        try:
            report["final_row_counts"] = await _final_row_counts(pool)
        except Exception as exc:
            report["final_row_counts_error_type"] = type(exc).__name__
        await pool.close()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", default="5,25,100", help="synthetic PDF activity tiers (default: 5,25,100)")
    parser.add_argument("--tier-timeout", type=float, default=600.0, help="hard timeout per tier in seconds")
    parser.add_argument("--receipt", type=Path, default=ROOT / "artifacts" / "inf01" / "mixed-pdf-pg.json")
    args = parser.parse_args()
    try:
        sizes = tuple(int(part) for part in args.sizes.split(",") if part)
    except ValueError:
        parser.error("--sizes must be comma-separated integers")
    if not sizes or any(size not in {5, 25, 100} for size in sizes) or len(set(sizes)) != len(sizes):
        parser.error("--sizes must contain unique values from 5,25,100")
    if not 60 <= args.tier_timeout <= 1800:
        parser.error("--tier-timeout must be between 60 and 1800 seconds")
    args.sizes = sizes
    try:
        report = asyncio.run(_run(args))
    except Exception as exc:
        report = {
            "benchmark": "INF01_MIXED_SYNTHETIC_PDF_PGVECTOR", "status": "failed",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "failure_type": type(exc).__name__,
            "failure_code": str(exc) if str(exc).isidentifier() else "benchmark_preflight_failed",
            "failure_trace": _safe_failure_trace(exc),
            "provider_calls": 0, "whatsapp_webhook_calls": 0,
        }
    serialized = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    checks = report.get("verification_checks", {})
    return 0 if report.get("status") == "complete" and checks and all(checks.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
