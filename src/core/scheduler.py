import asyncio
import logging
import os
import time
from datetime import datetime

import asyncpg
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from src.core.db.scoping import system_scope
from src.core.google_feature_flags import google_calendar_enabled
from src.models.schemas import ReportOutput

logger = logging.getLogger(__name__)

_report_cache: dict[str, ReportOutput] = {}
_scheduler: BackgroundScheduler | None = None


# I job girano in thread APScheduler: ogni asyncio.run() crea un event loop
# NUOVO. Le connessioni asyncpg sono legate al loop che le ha create:
# condividere il pool di uvicorn da qui corrompe il protocollo
# (RuntimeError _check_state) e le connessioni avvelenate tornano nel pool,
# appiccicando le richieste web senza alcun errore visibile. Ogni job usa
# quindi un proprio pool effimero, come i worker standalone.
_JOB_POOL_MIN = 1
_JOB_POOL_MAX = 2
_JOB_COMMAND_TIMEOUT = 30  # secondi: niente piu' query appese all'infinito


def _dsn() -> str:
    dsn = os.getenv("DATABASE_URL", "")
    if not dsn:
        raise RuntimeError("DATABASE_URL non configurato: scheduler non operativo")
    return dsn


async def _con_pool_esimero(job_coro, lock_name: str | None = None):
    """Esegue la coroutine del job su un pool creato DENTRO il suo event
    loop e lo chiude sempre. Log di durata per diagnostiche post-incidente.
    Protegge l'esecuzione distribuita multi-istanza con PostgreSQL advisory lock."""
    inizio = time.monotonic()
    nome = getattr(job_coro, "__name__", "job")
    effective_lock_name = lock_name or f"scheduler_{nome}"
    pool = await asyncpg.create_pool(
        dsn=_dsn(),
        min_size=_JOB_POOL_MIN,
        max_size=_JOB_POOL_MAX,
        command_timeout=_JOB_COMMAND_TIMEOUT,
    )
    try:
        from src.core.jobs.base import execute_with_advisory_lock
        executed = await execute_with_advisory_lock(pool, effective_lock_name, job_coro)
        if executed:
            logger.info(
                "scheduler=job_ok job=%s durata=%.1fs", nome, time.monotonic() - inizio
            )
        else:
            logger.info(
                "scheduler=job_skipped job=%s (advisory_lock_held)", nome
            )
    except Exception:
        logger.exception(
            "scheduler=job_ko job=%s durata=%.1fs",
            nome,
            time.monotonic() - inizio,
        )
        raise
    finally:
        await pool.close()


def _ottieni_storico_ref():
    raise RuntimeError("ottieni_storico_ref non impostato. Chiama imposta_fonte_dati().")


_ottieni_storico = _ottieni_storico_ref


def imposta_fonte_dati(callback):
    global _ottieni_storico
    _ottieni_storico = callback


def get_report_cache(data: str) -> ReportOutput | None:
    return _report_cache.get(data)


def set_report_cache(data: str, report: ReportOutput):
    _report_cache[data] = report


def genera_e_caching():
    from src.core.crew_runner_report import genera_report

    oggi = datetime.now().strftime("%Y-%m-%d")
    storico = _ottieni_storico()
    report = genera_report(storico)
    _report_cache[oggi] = report
    logger.info("[scheduler] Report per %s generato e cachato.", oggi)


def _run_retention():
    # This state is process-local, so sweep it on every instance before the
    # distributed database job takes its advisory lock.
    _purge_local_simulator_history()
    asyncio.run(_con_pool_esimero(_retention_job))


def _purge_local_simulator_history():
    from src.core.conversation_store import store as conversation_store
    purged = conversation_store.purge_expired()
    if purged:
        logger.info("scheduler=local_retention idle_simulator_histories_purged=%d", purged)


async def _retention_job(pool):
    from src.core.retention_job import run_retention

    await run_retention(pool)


def _run_reminder_check():
    asyncio.run(_con_pool_esimero(_reminder_check_job))


async def _reminder_check_job(pool):
    from src.core.bookings import BookingService
    from src.core.bookings.reminder_job import send_reminders_for_org
    from src.core.db.repositories.booking_repo import BookingRepository
    from src.core.db.repositories.organization_repo import OrganizationRepository
    from src.whatsapp.repository import Repository as WhatsAppRepository
    from src.whatsapp.service import WhatsAppService
    orgs = await pool.fetch("""
        SELECT id, timezone FROM organizations
        WHERE subscription_status NOT IN ('canceled', 'incomplete', 'past_due')
    """)
    for org in orgs:
        wrepo = WhatsAppRepository(pool)
        booking_repo = BookingRepository(pool)
        org_repo = OrganizationRepository(pool)
        whatsapp = WhatsAppService(None, wrepo)
        service = BookingService(
            booking_repo=booking_repo,
            org_repo=org_repo,
            whatsapp_service=whatsapp,
            app_config=None,
        )
        await send_reminders_for_org(service, org["id"], org.get("timezone", "Europe/Rome"))


def _run_reminder_timeout():
    asyncio.run(_con_pool_esimero(_reminder_timeout_job))


async def _reminder_timeout_job(pool):
    from src.core.bookings import BookingService
    from src.core.bookings.reminder_job import check_timeouts_for_org
    from src.core.db.repositories.booking_repo import BookingRepository
    from src.core.db.repositories.organization_repo import OrganizationRepository
    orgs = await pool.fetch("""
        SELECT id, timezone FROM organizations
        WHERE subscription_status NOT IN ('canceled', 'incomplete', 'past_due')
    """)
    for org in orgs:
        service = BookingService(
            booking_repo=BookingRepository(pool),
            org_repo=OrganizationRepository(pool),
        )
        await check_timeouts_for_org(service, org["id"], org.get("timezone", "Europe/Rome"))


def _run_no_show_check():
    asyncio.run(_con_pool_esimero(_no_show_check_job))


async def _no_show_check_job(pool):
    from src.core.bookings import BookingService
    from src.core.bookings.no_show_job import mark_da_verificare_for_org
    from src.core.db.repositories.booking_repo import BookingRepository
    from src.core.db.repositories.organization_repo import OrganizationRepository
    orgs = await pool.fetch("""
        SELECT id, timezone FROM organizations
        WHERE subscription_status NOT IN ('canceled', 'incomplete', 'past_due')
    """)
    for org in orgs:
        service = BookingService(
            booking_repo=BookingRepository(pool),
            org_repo=OrganizationRepository(pool),
        )
        await mark_da_verificare_for_org(service, org["id"], org.get("timezone", "Europe/Rome"))


def _run_calendar_sync():
    if not google_calendar_enabled():
        logger.info("calendar=sync_skipped reason=feature_disabled")
        return
    asyncio.run(_con_pool_esimero(_calendar_sync_job))


@system_scope("worker queue: enumerazione org con sync calendar abilitata")
async def _calendar_sync_job(pool):
    if not google_calendar_enabled():
        logger.info("calendar=sync_skipped reason=feature_disabled")
        return
    encryption_key = os.getenv("ENCRYPTION_KEY", "")
    if not encryption_key:
        logger.warning("calendar=sync_skipped reason=no_encryption_key")
        return
    from src.core.db.repositories.organization_repo import OrganizationRepository
    from src.core.calendar import GoogleCalendarService
    repo = OrganizationRepository(pool)
    calendar_service = GoogleCalendarService(repo, encryption_key)
    await calendar_service.process_pending(limit=20)


def _run_nonce_cleanup():
    asyncio.run(_con_pool_esimero(_nonce_cleanup_job))


@system_scope("trusted scheduler: cleanup globale nonces OAuth scaduti")
async def _nonce_cleanup_job(pool):
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM oauth_nonces WHERE created_at < NOW() - INTERVAL '1 day'"
        )
        logger = __import__("logging").getLogger(__name__)
        logger.info("cleanup=oauth_nonces deleted=%s", result)


def _run_booking_sync_sweep():
    asyncio.run(_con_pool_esimero(_booking_sync_sweep_job))


async def _booking_sync_sweep_job(pool):
    """Riconcilia i sync esterni orfani (pending oltre soglia -> failed + log
    escalation). Idempotente: righe gia' terminali non vengono toccate."""
    from src.core.bookings.sync_sweep_job import sweep_stale_syncs

    outcome = await sweep_stale_syncs(pool)
    logger = __import__("logging").getLogger(__name__)
    logger.info("booking_sync_sweep=completato examined=%d swept=%d",
                outcome["examined"], outcome["swept_to_failed"])


def _run_airtable_webhook_reap():
    asyncio.run(_con_pool_esimero(_airtable_webhook_reap_job))


async def _airtable_webhook_reap_job(pool):
    """Rimette in coda gli eventi webhook Airtable orfani in processing."""
    from src.integrations.airtable.repository import AirtableWebhookRepository
    from src.integrations.airtable.webhook_service import AirtableWebhookService

    service = AirtableWebhookService(repo=AirtableWebhookRepository(pool))
    outcome = await service.reap_stale_events()
    logger = __import__("logging").getLogger(__name__)
    logger.info("airtable_webhook_reap=completato reaped=%d", outcome["reaped"])


def _run_suspension_notice():
    asyncio.run(_con_pool_esimero(_suspension_notice_job))


async def _suspension_notice_job(pool):
    """Notifica via email i gestori delle org con trial scaduto e mai
    notificati. Idempotente per costruzione: l'UPDATE con WHERE
    suspension_notified_at IS NULL e' un claim atomico — un'org viene
    notificata esattamente una volta (condiviso con subscription.deleted)."""
    from src.core.notifications.email_service import enqueue_suspension_notice
    claimed = await pool.fetch("""
        UPDATE organizations SET suspension_notified_at = NOW()
        WHERE trial_end < NOW()
          AND subscription_status IN ('trialing', 'incomplete')
          AND suspension_notified_at IS NULL
        RETURNING id
    """)
    for org in claimed:
        enqueue_suspension_notice(str(org["id"]), pool)
    if claimed:
        logger = __import__("logging").getLogger(__name__)
        logger.info("suspension=notice_enqueued count=%d", len(claimed))


def _run_weekly_report():
    asyncio.run(_con_pool_esimero(_weekly_report_job))


async def _weekly_report_job(pool):
    """Genera e invia il report settimanale per tutte le org attive.
    Idempotente: un report gia' inviato per lo stesso periodo non viene
    reinviato (weekly_report_log con constraint UNIQUE)."""
    from src.core.report.weekly_report import genera_report_tutte_le_org
    risultati = await genera_report_tutte_le_org(pool)
    logger = __import__("logging").getLogger(__name__)
    inviati = sum(1 for r in risultati if r.get("esito") == "inviato")
    errori = sum(1 for r in risultati if r.get("esito") == "errore")
    logger.info("report_settimanale=completato inviati=%d errori=%d totale=%d",
                inviati, errori, len(risultati))


def avvia_scheduler():
    global _scheduler
    if _scheduler is not None:
        return

    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        genera_e_caching,
        CronTrigger(hour=20, minute=0),
        id="report_giornaliero",
        name="Genera report di fine giornata",
        replace_existing=True,
    )
    _scheduler.add_job(
        _run_retention,
        CronTrigger(hour=3, minute=0),
        id="retention_giornaliero",
        name="Data retention — soft-delete e purge",
        replace_existing=True,
    )
    _scheduler.add_job(
        _purge_local_simulator_history,
        IntervalTrigger(minutes=15),
        id="simulator_history_retention",
        name="Pulisce cronologia simulatore inattiva su questa istanza",
        replace_existing=True,
    )
    _scheduler.add_job(
        _run_reminder_check,
        CronTrigger(minute="*/30"),
        id="booking_reminder_send",
        name="Invia reminder prenotazioni 24h prima",
        replace_existing=True,
    )
    _scheduler.add_job(
        _run_reminder_timeout,
        CronTrigger(minute="*/30"),
        id="booking_reminder_timeout",
        name="Flagga reminder senza risposta dopo 12h",
        replace_existing=True,
    )
    _scheduler.add_job(
        _run_no_show_check,
        CronTrigger(hour=23, minute=30),
        id="booking_no_show",
        name="Marca da_verificare prenotazioni non completate",
        replace_existing=True,
    )
    _scheduler.add_job(
        _run_calendar_sync,
        CronTrigger(minute=0),
        id="calendar_sync",
        name="Riconciliazione eventi Google Calendar",
        replace_existing=True,
    )
    _scheduler.add_job(
        _run_nonce_cleanup,
        CronTrigger(hour=4, minute=0),
        id="oauth_nonce_cleanup",
        name="Pulisce nonce OAuth scaduti",
        replace_existing=True,
    )
    _scheduler.add_job(
        _run_suspension_notice,
        CronTrigger(hour=8, minute=0),
        id="suspension_notice",
        name="Notifica email org con trial scaduto",
        replace_existing=True,
    )
    _scheduler.add_job(
        _run_booking_sync_sweep,
        CronTrigger(minute="*/30"),
        id="booking_sync_sweep",
        name="Riconcilia sync esterni orfani (pending oltre soglia)",
        replace_existing=True,
    )
    _scheduler.add_job(
        _run_airtable_webhook_reap,
        CronTrigger(minute="*/30"),
        id="airtable_webhook_reap",
        name="Rimette in coda eventi webhook Airtable orfani",
        replace_existing=True,
    )
    _scheduler.add_job(
        _run_weekly_report,
        CronTrigger(day_of_week="mon", hour=8, minute=30),
        id="report_settimanale",
        name="Report settimanale PDF via email (tutti i tenant attivi)",
        replace_existing=True,
    )
    _purge_local_simulator_history()
    _scheduler.start()
    logger.info("[scheduler] Avviato — report 20:00, retention 03:00, reminders every 30min, no-show 23:30, calendar sync every 60min, nonce cleanup 04:00, suspension notice 08:00, report settimanale lun 08:30.")


def ferma_scheduler():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("[scheduler] Arrestato.")
