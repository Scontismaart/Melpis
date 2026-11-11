from __future__ import annotations

import logging
from typing import Any

from src.core.inbound.service import InboundProcessingService

logger = logging.getLogger(__name__)


class InboundWorker:
    """
    Worker puro dedicato al background polling e alla gestione dei claim su DB.
    Completamente disaccoppiato dalla logica di business e dai canali di messaggistica.
    """

    def __init__(
        self,
        repo: Any,
        inbound_service: InboundProcessingService,
        batch_size: int = 10,
        private_booking_pool: Any = None,
    ):
        self.repo = repo
        self.inbound_service = inbound_service
        self.batch_size = batch_size
        self._booking_worker = None
        self.private_booking_pool = private_booking_pool
        # The synchronous A05 post-A04 bridge may use only the dedicated
        # service-role pool. It never falls back to BookingService writers.
        if self.inbound_service is not None:
            self.inbound_service.private_booking_pool = private_booking_pool

    async def process_next_batch(self):
        """Esegue il reaper dei claim pendenti/scaduti e preleva il prossimo batch atomico."""
        await self.repo.reap_stale_claims()
        messages = await self.repo.claim_inbound_messages(limit=self.batch_size)
        for msg in messages:
            try:
                await self.inbound_service.process_message(msg)
            except Exception as e:
                logger.error("Error processing message %s: %s", msg.get("id"), e)
        from src.core.db.inbound_worker_provenance import private_booking_worker_enabled
        if private_booking_worker_enabled() and self.private_booking_pool is not None:
            if self._booking_worker is None:
                from src.core.bookings.worker_authority import WorkerBookingAuthority
                self._booking_worker = WorkerBookingAuthority()
            try:
                await self._booking_worker.process_next_batch(self.private_booking_pool)
            except Exception as exc:
                logger.error("Private booking worker batch failed: %s", type(exc).__name__)
