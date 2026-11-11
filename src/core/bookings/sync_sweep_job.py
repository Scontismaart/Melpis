"""Sweep dei sync esterni orfani (riconciliazione Send-Then-Mark).

Una riga 'pending'/'pending_retry' piu' vecchia della soglia significa che il
processo e' crashato tra prepare e mark, o che l'adapter non ha mai risposto:
l'esito della chiamata esterna e' INCERTO. Ritentarе alla cieca creerebbe
duplicati sui provider senza idempotenza nativa, quindi lo sweep NON richiama
l'esterna: marca 'uncertain' e richiede la verifica sul gestionale. Il tempo
trascorso non prova la mancata consegna e non sblocca la chiave.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

STALE_SECONDS_DEFAULT = 1800  # 30 min: una call esterna dura secondi
SWEEP_LIMIT_DEFAULT = 50


async def sweep_stale_syncs(pool, stale_seconds: int = STALE_SECONDS_DEFAULT,
                            limit: int = SWEEP_LIMIT_DEFAULT) -> dict:
    """Marca incerti i sync pendenti oltre soglia, senza riarmare la chiave."""
    from src.core.db.repositories.external_booking_repo import ExternalBookingRepository

    repo = ExternalBookingRepository(pool)
    rows = await repo.get_pending_syncs(older_than_seconds=stale_seconds, limit=limit)
    swept = 0
    for row in rows:
        if row.get("provider") == "google":
            # Calendar has its own fenced GET-only uncertain-outcome recovery.
            continue
        org_id = row.get("organization_id")
        key = row.get("idempotency_key")
        try:
            marked = await repo.record_sync_failure(
                org_id,
                key,
                "reconciliation: esito chiamata esterna incerto dopo timeout "
                "(possibile crash tra prepare e mark). Verificare sul gestionale "
                "prima di qualsiasi riconciliazione.",
                sync_status="uncertain",
            )
            if not marked:
                continue  # A concurrent completion owns the newer state.
            swept += 1
            logger.error(
                "booking_sync_sweep org=%s key=%s provider=%s "
                "esito_incerto_verificare_sul_gestionale",
                org_id, key, row.get("provider"),
            )
        except Exception as exc:
            logger.warning(
                "booking_sync_sweep_failed org=%s key=%s err=%s", org_id, key, exc
            )
    return {"examined": len(rows), "swept_to_uncertain": swept}
