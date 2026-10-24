"""Disposable local B02 probe; assumes schema 066 exists and never migrates/resets."""
import argparse, asyncio, hashlib, json, os, subprocess, sys, time, uuid
from pathlib import Path
from urllib.parse import urlsplit

def validate_dsn(value):
    try:
        u = urlsplit(value)
        if (u.scheme not in {"postgres", "postgresql"} or u.hostname not in {"localhost", "127.0.0.1"}
                or u.port != 55439 or u.path.lstrip("/") != "b02_test" or u.query or u.fragment):
            raise ValueError
        return {"host": u.hostname, "port": 55439, "user": u.username, "password": u.password, "database": "b02_test"}
    except Exception:
        raise ValueError("invalid_local_b02_benchmark_dsn") from None

def source_info(root):
    files = ["src/core/documenti/indexing.py", "src/core/documenti/rag_context.py", "src/core/documenti/local_worker.py", "src/core/documenti/limits.py",
             "src/core/documenti/extractor.py", "src/core/db/repositories/document_publication.py",
             "src/core/db/repositories/document_repo.py", "src/core/db/migrations/065_document_publication.sql",
             "src/core/documenti/file_quota.py", "src/core/documenti/access.py", "src/core/billing/plans.py",
             "src/core/db/migrations/066_document_file_bytes.sql"]
    hashes = {f: hashlib.sha256((root / f).read_bytes()).hexdigest() for f in files if (root / f).is_file()}
    git = lambda *a: subprocess.run(["git", *a], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
    return {"commit": git("rev-parse", "HEAD"), "working_tree_dirty": bool(git("status", "--porcelain")), "source_sha256": hashes}


def make_pdf(fitz):
    doc = fitz.open()
    for n in range(5):
        doc.new_page().insert_text((72, 72), f"Synthetic B02 hours page {n+1}. Open daily 09:00 to 18:00.")
    data = doc.tobytes(); doc.close()
    return data


async def ocr_probe():
    try:
        import io
        from PIL import Image, ImageDraw, ImageFont
        from src.core.documenti.isolated import extract_local
        image = Image.new("RGB", (1800, 260), "white")
        ImageDraw.Draw(image).text((30, 50), "Synthetic OCR check", font=ImageFont.truetype("arial.ttf", 72), fill="black")
        stream = io.BytesIO(); image.save(stream, format="PNG"); start = time.perf_counter()
        out = await extract_local(stream.getvalue(), "synthetic-ocr.png", "image/png")
        assert "synthetic" in " ".join(out["page_texts"]).lower()
        return "passed", time.perf_counter() - start
    except Exception as exc:
        return f"not_certified:{type(exc).__name__}", 0.0


async def run(args):
    root, pool, created = Path(__file__).resolve().parents[1], None, []
    plans_module, original_trial_budget = None, None
    sys.path.insert(0, str(root))
    report = {"status": "failed", "metrics": {}, "ocr_status": "not_run"}
    try:
        if sys.version_info[:2] != (3, 12): raise RuntimeError("reviewed_python_312_required")
        value = os.environ.get("B02_BENCHMARK_DB_DSN")
        if not value: raise ValueError("missing_b02_benchmark_dsn")
        connect = validate_dsn(value)
        os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
        import asyncpg, fitz
        from src.core.db.repository import CoreRepository
        from src.core.documenti.indexing import index_content
        from src.core.documenti.rag_context import recupera_contesto_documenti
        pool = await asyncpg.create_pool(**connect, min_size=2, max_size=6, server_settings={"search_path": "public, extensions"})
        orgs = [uuid.uuid4(), uuid.uuid4()]
        async with pool.acquire() as conn:
            for i, org in enumerate(orgs):
                created.append(org)
                await conn.execute("INSERT INTO organizations(id,name) VALUES($1,$2)", org, f"B02 synthetic {i}")
                await conn.execute("UPDATE organizations SET plan='business',subscription_status='trialing',trial_start=now()-interval '1 hour',trial_end=now()+interval '6 days' WHERE id=$1", org)
        report["ocr_status"], ocr_seconds = await ocr_probe()
        repo, pdf, docs, counts, wall, cpu = CoreRepository(pool), make_pdf(fitz), [], [], [], []
        from src.core.billing import plans as plans_module
        original_trial_budget = plans_module.TRIAL_RAG_FILE_BYTES_LIMIT
        # Only the disposable local synthetic database is allowed above.
        # This temporary test allowance is never a commercial default.
        plans_module.TRIAL_RAG_FILE_BYTES_LIMIT = len(pdf) * 2
        report["synthetic_file_budget_bytes"] = len(pdf) * 2
        for i, org in enumerate(orgs):
            key, name = uuid.uuid4(), f"synthetic-{i}.pdf"
            t, c = time.perf_counter(), time.process_time()
            item = await index_content(repo, org, name, request_key=str(key), file_content=pdf, content_type="application/pdf", fonte="b02-synthetic")
            wall.append(time.perf_counter()-t); cpu.append(time.process_time()-c)
            assert item["status"] == "indexed"
            docs.append(item["document"]["id"]); counts.append(item["indexed"])
            if i == 0:
                replay = await index_content(repo, org, name, request_key=str(key), file_content=pdf, content_type="application/pdf", fonte="b02-synthetic")
                assert replay["status"] == "replay"
        t = time.perf_counter()
        contexts = await asyncio.gather(*(recupera_contesto_documenti(str(org), "opening hours 09:00 18:00", repo) for org in orgs))
        query_wall = time.perf_counter()-t
        for i, ctx in enumerate(contexts):
            assert ctx.chunks and all(str(c["document_id"]) == str(docs[i]) for c in ctx.chunks)
        await repo.doc_repo.toggle_document_active(orgs[0], docs[0])
        inactive = await recupera_contesto_documenti(str(orgs[0]), "opening hours 09:00 18:00", repo)
        usage = await pool.fetch("SELECT messages_used_this_period FROM organizations WHERE id=ANY($1::uuid[])", orgs)
        assert not inactive.chunks and len(usage) == 2 and all(r["messages_used_this_period"] == 0 for r in usage)
        file_usage = [(await repo.doc_repo.file_quota_status(org))["file_quota"] for org in orgs]
        assert all(item["committed_bytes"] == len(pdf) and item["reserved_bytes"] == 0 for item in file_usage)
        report.update(status="passed", metrics={"pdf_pages": 5, "pdf_bytes": len(pdf), "index_seconds_per_tenant": wall,
            "parent_cpu_seconds_per_tenant": cpu, "chunks_per_tenant": counts, "parallel_query_wall_seconds": query_wall,
            "parallel_query_count": 2, "replay_confirmed": 1, "results_after_toggle": len(inactive.chunks),
            "messages_used_after_pipeline": 0, "ocr_seconds": ocr_seconds,
            "committed_file_bytes_per_tenant": [item["committed_bytes"] for item in file_usage]})
    except Exception as exc:
        report.update(status="failed", error_type=type(exc).__name__)
    finally:
        if plans_module is not None:
            plans_module.TRIAL_RAG_FILE_BYTES_LIMIT = original_trial_budget
        try:
            from src.core.documenti.isolated import shutdown_local_workers
            await shutdown_local_workers()
        except Exception: report["worker_cleanup"] = "failed"
        if pool:
            try:
                if created:
                    async with pool.acquire() as conn, conn.transaction():
                        for table in ("document_index_requests", "document_chunks", "documents", "faq_cache"):
                            await conn.execute(f"DELETE FROM {table} WHERE organization_id=ANY($1::uuid[])", created)
                        await conn.execute("DELETE FROM organizations WHERE id=ANY($1::uuid[])", created)
            except Exception: report["database_cleanup"] = "failed"
            await pool.close()
    report["source"] = source_info(root)
    Path(args.output).write_text(json.dumps(report, sort_keys=True, indent=2), encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return report["status"] == "passed"


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--output", required=True)
    if not asyncio.run(run(parser.parse_args())): raise SystemExit(1)


if __name__ == "__main__": main()
