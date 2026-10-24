import asyncio
import os
import json
import threading
import uuid
import hashlib
import re
import asyncpg
from datetime import date, datetime
from contextlib import asynccontextmanager
from dotenv import load_dotenv

dotenv_path = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
load_dotenv(dotenv_path=dotenv_path)

import logging
from src.core.logging_filter import configure_logging

configure_logging(level=logging.INFO)
logger = logging.getLogger(__name__)

from fastapi import FastAPI, HTTPException, File, UploadFile, Depends, Request
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import JSONResponse
from src.api.cors import configure_cors

from src.core.notifications.email_service import start_worker, stop_worker as stop_email_worker
from src.core.crew_runner import genera_risposta
from src.core.llm_config import LLMRouteRequest, budget_ratio_from_billing, route_llm
from src.core.llm_routing import stima_costo_eur
from src.core.priorita import calcola_priorita, calcola_priorita_recensione
from src.core.conversation_store import store as conv_store

from src.core.scheduler import (
    imposta_fonte_dati,
    avvia_scheduler,
    ferma_scheduler,
    get_report_cache,
    set_report_cache,
)
from src.core.crew_runner_report import genera_report as genera_report_completo
from src.core.crew_runner_review import genera_risposta_recensione
from src.core.documenti.embeddings import vettorizza
from src.core.documenti.extractor import estrai_testo
from src.core.documenti.qa_agent import rispondi
from src.core.documenti.chunking import chunk_testo
from src.core.documenti.web_extractor import estrai_da_url
from src.core.documenti.priorita import rileva_conflitti_prezzo
from src.core.onboarding import (
    generate_preview,
    get_profile,
    list_verticals,
    save_profile,
)
from src.models.business_profile import PROFILI_DEMO
from src.models.schemas import (
    CaricaDocumentoInput,
    FAQInput,
    WebImportInput,
    ServizioStrutturato,
    DatiStrutturaInput,
    DomandaInput,
    RispostaDocumento,
    LINGUE_DISPONIBILI,
    MessaggioInput,
    RispostaOutput,
    RecensioneInput,
    RispostaRecensioneOutput,
    EventoDashboard,
    ReportOutput,
    OnboardingProfileInput,
    PreviewInput,
)
from src.core.observability import configure_error_reporting
configure_error_reporting()

from src.core.auth.csrf import validate_csrf_request
from src.core.auth.dependencies import get_repo, require_ruolo, close_http_client
from src.core.auth.routes import router as auth_router
from src.core.auth.register import router as register_router
from src.core.rate_limit import close_rate_limiter, get_rate_limiter, reset_memory_rate_limiter
from src.core.security.docs import is_production, require_docs_access
from src.core.db.repository import CoreRepository
from src.core.calendar import GoogleCalendarService
from src.core.google_feature_flags import google_calendar_enabled
from src.core.calendar.routes import router as calendar_router
from src.core.auth.audit import audit_log
from src.core.billing.routes import router as billing_router
from src.core.billing.config import BillingConfig
from src.core.gdpr.routes import router as gdpr_router
from src.core.inbox.routes import router as inbox_router
from src.core.bookings.routes import router as bookings_router
from src.core.reviews.routes import router as reviews_router
from src.core.reviews.google_routes import router as reviews_google_router
from src.whatsapp.repository import Repository as WhatsAppRepository
from src.whatsapp.router import create_router as create_whatsapp_router
from src.whatsapp.routes import router as whatsapp_account_router
from src.whatsapp.config import AppConfig as WhatsAppAppConfig
from src.instagram.routes import router as instagram_account_router
from src.instagram.router import create_router as create_instagram_router
from src.instagram.repository import InstagramRepository


def _background_jobs_enabled_from_env() -> bool:
    """Resolve whether this API process may run its in-process job loops."""
    app_env = os.getenv(
        "APP_ENV", os.getenv("ENVIRONMENT", "development")
    ).strip().lower()
    configured = os.getenv("MELPIS_BACKGROUND_JOBS_ENABLED")

    # Staging runtimes must opt in explicitly; this prevents an Auth-only test
    # deployment from silently starting queues or scheduled maintenance.
    if app_env == "staging" and configured is None:
        raise RuntimeError("MELPIS_BACKGROUND_JOBS_ENABLED must be explicit in staging")
    if configured is None:
        return True

    value = configured.strip().lower()
    if value not in {"true", "false"}:
        raise RuntimeError("MELPIS_BACKGROUND_JOBS_ENABLED must be 'true' or 'false'")
    if value == "false" and app_env != "staging":
        raise RuntimeError("Background jobs may only be disabled when APP_ENV=staging")
    return value == "true"


@asynccontextmanager
async def lifespan(app: FastAPI):
    from src.core.startup_guard import assert_production_safe
    assert_production_safe()
    background_jobs_enabled = _background_jobs_enabled_from_env()
    app.state.background_jobs_enabled = background_jobs_enabled
    # Config globale — settato incondizionatamente, prima di qualsiasi
    # dipendenza dal DB, cosi' e' disponibile anche in modalita' demo
    # (DATABASE_URL assente o DB irraggiungibile).
    app.state.billing_config = BillingConfig(
        stripe_trial_days=int(os.getenv("STRIPE_TRIAL_DAYS", "7")),
    )
    reset_memory_rate_limiter()
    if background_jobs_enabled:
        start_worker()
    app.state.inbound_task = None
    app.state.retry_task = None
    app.state.governance_task = None
    dsn = os.getenv("DATABASE_URL")
    if dsn:
        import asyncpg
        try:
            # Task18 Fase 4: max_size configurabile via env (scalabilità).
            # Default 5 come prima; con più worker/replica o carico alto,
            # aumenta senza toccare codice. Per picchi reali valutare un
            # pooler (Supavisor/pgBouncer) — vedi report task18.
            db_pool_max = int(os.getenv("DB_POOL_MAX_SIZE", "5"))
            # command_timeout: nessuna query web puo' restare appesa
            # all'infinito (difesa post-incidente pool scheduler).
            pool = await asyncpg.create_pool(
                dsn=dsn, min_size=1, max_size=db_pool_max, command_timeout=30
            )
            app.state.repo = CoreRepository(pool=pool)
            app.state.pool = pool
            logger.info("[startup] Database pool created successfully.")

            # Webhook WhatsApp reale: prima non era mai montato, quindi Meta non
            # poteva raggiungere l'app in nessun deploy. Serve il pool (per
            # persistere contatti/conversazioni/messaggi in arrivo), quindi lo
            # registriamo qui a runtime invece che a import time del modulo.
            wrepo = WhatsAppRepository(pool=pool)
            app.state.wrepo = wrepo
            whatsapp_app_config = WhatsAppAppConfig(
                app_secret=os.getenv("META_APP_SECRET", ""),
                encryption_key=os.getenv("ENCRYPTION_KEY", ""),
                postgres_dsn=dsn,
                verify_token=os.getenv("META_VERIFY_TOKEN", ""),
            )
            if whatsapp_app_config.app_secret and whatsapp_app_config.verify_token:
                whatsapp_router = create_whatsapp_router(whatsapp_app_config, wrepo)
                app.include_router(whatsapp_router)
                # Instagram DM: stessa app Meta (stesso app_secret/verify_token),
                # webhook dedicato /webhooks/instagram con lookup tenant su
                # instagram_accounts (migration 030).
                instagram_router = create_instagram_router(
                    whatsapp_app_config, wrepo, InstagramRepository(pool=pool)
                )
                app.include_router(instagram_router)
            else:
                logger.warning(
                    "[startup] META_APP_SECRET o META_VERIFY_TOKEN non configurati: "
                    "webhook WhatsApp/Instagram NON montati. Impostali in .env per riceverli."
                )

            from src.core.bookings import BookingService
            from src.whatsapp.service import WhatsAppService
            wservice = WhatsAppService(app_config=whatsapp_app_config, repo=wrepo)
            calendar_service = GoogleCalendarService(
                repo=CoreRepository(pool=pool),
                encryption_key=os.getenv("ENCRYPTION_KEY", ""),
            )
            app.state.calendar_service = calendar_service
            if google_calendar_enabled():
                await calendar_service.assert_schema()
            from src.core.db.repositories.booking_repo import BookingRepository
            from src.core.db.repositories.organization_repo import OrganizationRepository
            app.state.booking_service = BookingService(
                booking_repo=BookingRepository(pool=pool),
                org_repo=OrganizationRepository(pool=pool),
                repo=BookingRepository(pool=pool),
                whatsapp_service=wservice,
                app_config=whatsapp_app_config,
                calendar_service=calendar_service,
            )

            from src.core.receptionist.conversation_orchestrator import ConversationOrchestrator
            try:
                from src.integrations.airtable.wiring import build_airtable_tool_factory
                airtable_tool_factory = build_airtable_tool_factory(
                    pool, getattr(app.state, "repo", None)
                )
            except Exception as e:
                logger.warning("[startup] Airtable AI tools non disponibili: %s", e)
                airtable_tool_factory = None
            app.state.orchestrator = ConversationOrchestrator(
                org_repo=getattr(app.state.repo, "org_repo", app.state.repo),
                doc_repo=getattr(app.state.repo, "doc_repo", app.state.repo),
                billing_repo=getattr(app.state.repo, "billing_repo", app.state.repo),
                conv_repo=getattr(app.state.repo, "conv_repo", app.state.repo),
                booking_service=app.state.booking_service,
                fast_path_matcher=wservice.fast_path_match if wservice else None,
                airtable_tool_factory=airtable_tool_factory,
            )

            from src.whatsapp.inbound_processor import InboundProcessor
            from src.whatsapp.retry_worker import RetryWorker
            inbound_processor = InboundProcessor(
                app_config=whatsapp_app_config,
                repo=wrepo,
                service=wservice,
                booking_service=app.state.booking_service,
                orchestrator=app.state.orchestrator,
            )
            retry_worker = RetryWorker(
                app_config=whatsapp_app_config,
                repo=wrepo,
                service=wservice,
            )

            async def _inbound_loop():
                while True:
                    try:
                        await inbound_processor.process_next_batch()
                    except asyncio.CancelledError:
                        break
                    except Exception as e:
                        logger.error("Inbound worker loop error: %s", e)
                    await asyncio.sleep(1.0)

            async def _retry_loop():
                while True:
                    try:
                        await retry_worker.process_next_batch()
                        await app.state.booking_service.process_pending_calendar_sync()
                    except asyncio.CancelledError:
                        break
                    except Exception as e:
                        logger.error("Retry worker loop error: %s", e)
                    await asyncio.sleep(5.0)

            async def _governance_loop():
                billing_repo = app.state.repo.billing_repo
                while True:
                    try:
                        processed = await billing_repo.drain_governance_outbox(limit=100)
                    except asyncio.CancelledError:
                        break
                    except Exception as e:
                        logger.error("Governance outbox worker loop error: %s", e)
                        processed = 0
                    # Drain bursts promptly while avoiding a hot loop when idle
                    # or when a persistent database error needs operator action.
                    await asyncio.sleep(0.05 if processed else 1.0)

            if background_jobs_enabled:
                app.state.inbound_task = asyncio.create_task(_inbound_loop())
                app.state.retry_task = asyncio.create_task(_retry_loop())
                app.state.governance_task = asyncio.create_task(_governance_loop())
        except Exception as e:
            if google_calendar_enabled():
                raise RuntimeError("Calendar enabled requires verified database and worker readiness") from None
            logger.warning("[startup] Database connection failed: %s. Running without pool.", e)
            app.state.repo = None
            app.state.pool = None
            app.state.wrepo = None
            from src.core.bookings import BookingService
            from src.core.bookings.memory_repo import InMemoryBookingRepo
            app.state.booking_service = BookingService(
                repo=InMemoryBookingRepo(),
                whatsapp_service=None, app_config=None,
            )
            from src.core.receptionist.conversation_orchestrator import ConversationOrchestrator
            app.state.orchestrator = ConversationOrchestrator(
                booking_service=app.state.booking_service,
            )
            if background_jobs_enabled:
                _imposta_fonte_dati_per_scheduler()
    else:
        app.state.repo = None
        app.state.pool = None
        app.state.wrepo = None
        from src.core.bookings import BookingService
        from src.core.bookings.memory_repo import InMemoryBookingRepo
        app.state.booking_service = BookingService(
            repo=InMemoryBookingRepo(),
            whatsapp_service=None, app_config=None,
        )
        from src.core.receptionist.conversation_orchestrator import ConversationOrchestrator
        app.state.orchestrator = ConversationOrchestrator(
            booking_service=app.state.booking_service,
        )
        if background_jobs_enabled:
            _imposta_fonte_dati_per_scheduler()
    if background_jobs_enabled:
        avvia_scheduler()
    yield
    if background_jobs_enabled:
        ferma_scheduler()
        stop_email_worker()
    background_tasks = [
        task for task in (
            getattr(app.state, "inbound_task", None),
            getattr(app.state, "retry_task", None),
            getattr(app.state, "governance_task", None),
        ) if task is not None
    ]
    for task in background_tasks:
        task.cancel()
    if background_tasks:
        await asyncio.gather(*background_tasks, return_exceptions=True)
    if app.state.pool:
        await app.state.pool.close()
    from src.core.documenti.isolated import shutdown_local_workers
    await shutdown_local_workers()
    await close_http_client()
    await close_rate_limiter()


app = FastAPI(
    title="WhatsApp AI Responder - Demo API",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/openapi.json", include_in_schema=False)
async def protected_openapi(request: Request):
    require_docs_access(request)
    return app.openapi()


@app.get("/docs", include_in_schema=False)
async def protected_docs(request: Request):
    require_docs_access(request)
    return get_swagger_ui_html(openapi_url="/openapi.json", title=f"{app.title} - Docs")


@app.get("/redoc", include_in_schema=False)
async def protected_redoc(request: Request):
    require_docs_access(request)
    return get_redoc_html(openapi_url="/openapi.json", title=f"{app.title} - ReDoc")

app.include_router(billing_router)
app.include_router(gdpr_router)
app.include_router(inbox_router)
app.include_router(bookings_router)
app.include_router(calendar_router)
app.include_router(reviews_router)
app.include_router(reviews_google_router)
app.include_router(whatsapp_account_router)
app.include_router(instagram_account_router)
app.include_router(create_whatsapp_router())
app.include_router(auth_router)
app.include_router(register_router)
from src.api.routes.organization import router as organization_router
app.include_router(organization_router)
from src.api.routes.integrations import router as integrations_router
app.include_router(integrations_router)
from src.api.routes.airtable import router as airtable_router
app.include_router(airtable_router)
from src.api.routes.dashboard import (
    router as dashboard_router,
    DASHBOARD_EVENTI_WINDOW_DAYS,
    DASHBOARD_EVENTI_MAX,
    _DASHBOARD_EVENTI_CTE,
    recupera_eventi_dashboard,
    recupera_eventi_prioritari,
)
app.include_router(dashboard_router)
from src.api.routes.knowledge import router as knowledge_router
app.include_router(knowledge_router)
from src.api.routes.simulator import router as simulator_router
app.include_router(simulator_router)
from src.api.routes.team import router as team_router
app.include_router(team_router)

configure_cors(app)

# ── Rate limiting ──────────────────────────────────────────────
RATE_LIMIT_LIMIT = int(os.getenv("RATE_LIMIT_REQUESTS", "100"))
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
LLM_GLOBAL_RATE_LIMIT = int(os.getenv("LLM_GLOBAL_RATE_LIMIT", "200"))
LLM_GLOBAL_RATE_WINDOW = int(os.getenv("LLM_GLOBAL_RATE_WINDOW_SECONDS", "60"))
LLM_ROUTES = {
    "/api/messaggio",
    "/api/recensione",
    "/api/documenti/chiedi",
    "/api/reviews/google/sync",
    "/api/onboarding/preview",
}


async def _rate_limit_check(key: str, limit: int | None = None,
                            window_seconds: int | None = None) -> bool:
    """True se key ha superato il limite nella finestra corrente.
    Se limit/window_seconds sono None, usa i valori globali."""
    if limit is None:
        limit = RATE_LIMIT_LIMIT
    if window_seconds is None:
        window_seconds = RATE_LIMIT_WINDOW
    limiter = await get_rate_limiter()
    return await limiter.hit(key, limit, window_seconds)


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/auth/mfa"):
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["Pragma"] = "no-cache"
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; font-src 'self' data:; img-src 'self' data:; connect-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; form-action 'self'",
    )
    return response


@app.middleware("http")
async def trace_id_middleware(request: Request, call_next):
    supplied_id = request.headers.get("X-Request-ID", "")
    trace_id = supplied_id if re.fullmatch(r"[A-Za-z0-9_-]{8,64}", supplied_id) else uuid.uuid4().hex[:16]
    request.state.trace_id = trace_id
    response = await call_next(request)
    response.headers["X-Trace-ID"] = trace_id
    return response


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if request.url.path.startswith("/api/health") or request.url.path in ("/webhooks/whatsapp", "/webhooks/instagram", "/api/billing/webhook"):
        return await call_next(request)

    # Before authentication, tenant headers are untrusted and cannot partition
    # the quota. Only a validated proxy chain can supply the client address.
    from src.core.auth.trusted_network import get_client_ip
    client_ip = str(get_client_ip(request) or "unknown")
    if await _rate_limit_check(f"ip:{client_ip}"):
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit superato per l'organizzazione. Riprova tra poco."},
        )

    # Limite per utente/credenziale (Bearer JWT o X-API-Key), indipendente dal tenant:
    # evita che un singolo utente saturi la finestra condivisa dell'organizzazione.
    from src.core.auth.bff import access_cookie_name
    user_token = request.headers.get("Authorization") or request.cookies.get(access_cookie_name())
    token_digest = hashlib.sha256(user_token.encode()).hexdigest() if user_token else None
    if token_digest and await _rate_limit_check(f"credential:{token_digest}"):
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit superato per l'utente. Riprova tra poco."},
        )

    # Audit 3.3: cap aggregato su TUTTE le chiamate LLM, indipendentemente
    # dal tenant/utente — protegge il budget del provider LLM condiviso da un
    # "noisy neighbor" fatto di molti tenant piccoli.
    if request.url.path in LLM_ROUTES:
        if await _rate_limit_check("llm:global", LLM_GLOBAL_RATE_LIMIT, LLM_GLOBAL_RATE_WINDOW):
            return JSONResponse(
                status_code=429,
                content={"detail": "Limite globale chiamate AI raggiunto. Riprova tra poco."},
            )

    return await call_next(request)


@app.middleware("http")
async def csrf_middleware(request: Request, call_next):
    ok, detail = validate_csrf_request(request)
    if not ok:
        return JSONResponse(status_code=403, content={"detail": detail})
    return await call_next(request)


# ── Facade di retrocompatibilità & stato demo in-memory ────────────────
from src.api.routes.common import (
    audit_event as _audit,
    get_billing_snapshot as _get_billing_snapshot,
    check_feature_blocked_by_plan as _piano_blocca_feature,
    get_shared_event_history,
    next_event_id,
)

_storico_eventi = get_shared_event_history()
_prossimo_id = next_event_id


def _imposta_fonte_dati_per_scheduler():
    imposta_fonte_dati(get_shared_event_history)



@app.get("/api/health/live")
async def liveness_check(request: Request):
    """
    Kubernetes / Docker Liveness Probe.
    Verifica che il processo e i task asincroni di background siano vivi.
    Se un worker task è terminato con eccezione non gestita, ritorna 503 per forzare
    il riavvio del container dall'orchestratore.
    """
    workers_ok = True
    worker_status = {}
    jobs_enabled = getattr(request.app.state, "background_jobs_enabled", True)
    for task_name in ("inbound_task", "retry_task", "governance_task"):
        task = getattr(request.app.state, task_name, None)
        if not jobs_enabled:
            worker_status[task_name] = "disabled"
        elif task is not None:
            if task.done():
                exc = task.exception() if not task.cancelled() else "cancelled"
                worker_status[task_name] = "stopped: worker failure" if exc else "stopped"
                if exc:
                    workers_ok = False
            else:
                worker_status[task_name] = "running"
        else:
            worker_status[task_name] = "not_started"

    payload = {
        "status": "ok" if workers_ok else "unhealthy",
        "background_jobs": "enabled" if jobs_enabled else "disabled",
        "workers": worker_status,
    }
    if not workers_ok:
        return JSONResponse(status_code=503, content=payload)
    return payload


@app.get("/api/health/ready")
@app.get("/api/health")
async def readiness_check(request: Request):
    """
    Core readiness depends on the database, not an optional AI provider.
    AI configuration is reported separately and never probed remotely here.
    """
    checks: dict[str, str] = {}
    healthy = True
    jobs_enabled = getattr(request.app.state, "background_jobs_enabled", True)
    checks["background_jobs"] = "enabled" if jobs_enabled else "disabled"

    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        checks["database"] = "non configurato (DATABASE_URL assente)"
        healthy = False
    else:
        try:
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            checks["database"] = "ok"
        except Exception:
            logger.exception("readiness_database_failed")
            checks["database"] = "errore di connessione"
            healthy = False

    from src.core.llm_config import ai_configuration_status
    ai_status = ai_configuration_status()
    checks["ai_provider"] = ai_status["status"]

    payload = {
        "status": "ok" if healthy else "degraded",
        "modello_configurato": ai_status["model"],
        "checks": checks,
    }
    if not healthy:
        return JSONResponse(status_code=503, content=payload)
    return payload


@app.get("/api/health/ai")
async def ai_availability_check():
    """Configuration visibility only: no paid inference or remote request."""
    from src.core.llm_config import ai_configuration_status

    status = ai_configuration_status()
    payload = {**status, "remote_status": "non_verificato"}
    if status["status"] != "configurato":
        return JSONResponse(status_code=503, content=payload)
    return payload


# ── Frontend pages & Static files ───────────────────────────────────────
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse

_web_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "web"))
_landing_dir = os.path.join(_web_dir, "landing")

@app.get("/", include_in_schema=False)
async def serve_root():
    if os.path.exists(os.path.join(_landing_dir, "index.html")):
        return FileResponse(os.path.join(_landing_dir, "index.html"))
    return FileResponse(os.path.join(_web_dir, "index.html"))

@app.get("/accedi", include_in_schema=False)
@app.get("/accedi/", include_in_schema=False)
async def serve_accedi():
    return FileResponse(os.path.join(_web_dir, "login.html"))

@app.get("/registrati", include_in_schema=False)
@app.get("/registrati/", include_in_schema=False)
async def serve_registrati():
    return FileResponse(os.path.join(_web_dir, "register.html"))

@app.get("/privacy", include_in_schema=False)
@app.get("/privacy/", include_in_schema=False)
async def serve_privacy():
    p = os.path.join(_landing_dir, "privacy.html")
    if os.path.exists(p):
        return FileResponse(p)
    return FileResponse(os.path.join(_web_dir, "login.html"))

@app.get("/termini", include_in_schema=False)
@app.get("/termini/", include_in_schema=False)
async def serve_termini():
    p = os.path.join(_landing_dir, "termini.html")
    if os.path.exists(p):
        return FileResponse(p)
    return FileResponse(os.path.join(_web_dir, "login.html"))

@app.get("/cookie", include_in_schema=False)
@app.get("/cookie/", include_in_schema=False)
async def serve_cookie():
    p = os.path.join(_landing_dir, "cookie.html")
    if os.path.exists(p):
        return FileResponse(p)
    return FileResponse(os.path.join(_web_dir, "login.html"))

@app.get("/app", include_in_schema=False)
async def serve_app_redirect():
    return RedirectResponse(url="/app/")

@app.get("/app/", include_in_schema=False)
async def serve_app_index():
    return FileResponse(os.path.join(_web_dir, "index.html"))

@app.get("/login.html", include_in_schema=False)
async def serve_login_html():
    return FileResponse(os.path.join(_web_dir, "login.html"))

@app.get("/register.html", include_in_schema=False)
async def serve_register_html():
    return FileResponse(os.path.join(_web_dir, "register.html"))

if os.path.exists(_landing_dir):
    app.mount("/landing", StaticFiles(directory=_landing_dir), name="landing_static")
if os.path.exists(_web_dir):
    app.mount("/app", StaticFiles(directory=_web_dir), name="app_static")

# Root static files: serve landing assets with html=True
if os.path.exists(_landing_dir):
    app.mount("/", StaticFiles(directory=_landing_dir, html=True), name="root_static")
elif os.path.exists(_web_dir):
    app.mount("/", StaticFiles(directory=_web_dir, html=True), name="root_static")


