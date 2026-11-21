import hashlib
import json
import logging
from collections import deque
from contextlib import asynccontextmanager
from threading import RLock
import time
from uuid import UUID


logger = logging.getLogger(__name__)


@asynccontextmanager
async def _locked_erasure_snapshot(pool, organization_id):
    """Read the current tenant barrier while fencing local cache operations."""
    org = UUID(str(organization_id))
    async with pool.acquire() as conn, conn.transaction(isolation="read_committed"):
        await conn.execute(
            "SELECT pg_advisory_xact_lock_shared(hashtextextended($1::text,606))",
            str(org),
        )
        rows = await conn.fetch(
            """SELECT operation_id,scope,subject_id,identifier_digest,key_id
               FROM gdpr_erasure_barriers WHERE organization_id=$1 ORDER BY operation_id""",
            org,
        )
        snapshot = hashlib.sha256(json.dumps(
            [list(row.values()) for row in rows], default=str, separators=(",", ":"),
        ).encode()).digest()
        yield snapshot, any(row["scope"] == "organization" for row in rows)


class ConversationStore:
    def __init__(self, max_scambi: int = 10, ttl_seconds: int = 60 * 60 * 24,
                 max_conversations: int = 5000):
        self._store: dict[str, deque[tuple[str, str]]] = {}
        self._max = max_scambi
        self._ttl_seconds = ttl_seconds
        self._max_conversations = max(1, max_conversations)
        self._updated_at: dict[str, float] = {}
        self._lock = RLock()
        self._tenant_snapshots: dict[str, bytes] = {}
        # Contatore tentativi raccolta dati prenotazione per conversazione.
        # Conta solo i giri in cui il cliente NON fornisce nessun dato nuovo
        # dopo una richiesta esplicita dell'assistente. Un dato ambiguo
        # (es. "verso sera") NON conta come tentativo fallito — conta solo
        # l'assenza totale del dato richiesto.
        self._tentativi_prenotazione: dict[str, int] = {}

    def _purge_expired(self, now: float) -> int:
        expired = [
            key for key, updated in self._updated_at.items()
            if now - updated >= self._ttl_seconds
        ]
        for key in expired:
            self.cancella(key)
        return len(expired)

    def purge_expired(self) -> int:
        """Remove idle histories so private simulator text is not retained indefinitely."""
        with self._lock:
            return self._purge_expired(time.monotonic())

    def aggiungi(self, id_conv: str, msg_cliente: str, msg_bot: str) -> None:
        with self._lock:
            now = time.monotonic()
            self._purge_expired(now)
            self._ensure_capacity(id_conv)
            if id_conv not in self._store:
                self._store[id_conv] = deque(maxlen=self._max)
            self._store[id_conv].append((msg_cliente, msg_bot))
            self._updated_at[id_conv] = now

    def _ensure_capacity(self, id_conv: str) -> None:
        if id_conv not in self._updated_at and len(self._updated_at) >= self._max_conversations:
            self.cancella(min(self._updated_at, key=self._updated_at.__getitem__))

    def recupera_cronologia(self, id_conv: str) -> list[tuple[str, str]]:
        with self._lock:
            now = time.monotonic()
            self._purge_expired(now)
            if id_conv in self._store:
                self._updated_at[id_conv] = now
            return list(self._store.get(id_conv, []))

    def cancella(self, id_conv: str) -> None:
        with self._lock:
            self._store.pop(id_conv, None)
            self._updated_at.pop(id_conv, None)
            self._tentativi_prenotazione.pop(id_conv, None)
            tenant = id_conv.split(":", 1)[0]
            if tenant in self._tenant_snapshots and not any(
                key.startswith(tenant + ":") for key in self._updated_at
            ):
                self._tenant_snapshots.pop(tenant, None)

    def erase_organization(self, organization_id: str) -> int:
        """Remove only this tenant's simulator keys; anonymous demo is separate."""
        with self._lock:
            keys = [key for key in self._updated_at if key.startswith(organization_id + ':')]
            self._tenant_snapshots.pop(organization_id, None)
            for key in keys:
                self.cancella(key)
            return len(keys)

    def _refresh_organization(self, organization_id: str, snapshot: bytes) -> None:
        if self._tenant_snapshots.get(organization_id) != snapshot:
            self.erase_organization(organization_id)

    async def tenant_history(self, pool, organization_id, id_conv):
        """Return history and its exact barrier generation, failing closed on DB errors."""
        tenant = str(UUID(str(organization_id)))
        if not id_conv.startswith(tenant + ":"):
            raise ValueError("Unexpected simulator cache scope")
        try:
            async with _locked_erasure_snapshot(pool, tenant) as (snapshot, blocked):
                with self._lock:
                    self._refresh_organization(tenant, snapshot)
                    if blocked:
                        self.erase_organization(tenant)
                        raise RuntimeError("Simulator organization erased")
                    return self.recupera_cronologia(id_conv), snapshot
        except Exception:
            self.erase_organization(tenant)
            raise

    async def add_tenant_history(
        self, pool, organization_id, id_conv, expected_snapshot, msg_cliente, msg_bot,
    ) -> bool:
        """Append only if no erasure changed the generation during orchestration."""
        tenant = str(UUID(str(organization_id)))
        if not id_conv.startswith(tenant + ":"):
            raise ValueError("Unexpected simulator cache scope")
        try:
            async with _locked_erasure_snapshot(pool, tenant) as (snapshot, blocked):
                with self._lock:
                    self._refresh_organization(tenant, snapshot)
                    if blocked:
                        self.erase_organization(tenant)
                        return False
                    if snapshot != expected_snapshot:
                        return False
                    self.aggiungi(id_conv, msg_cliente, msg_bot)
                    self._tenant_snapshots[tenant] = snapshot
                    return True
        except Exception:
            self.erase_organization(tenant)
            raise

    async def reconcile_tenant_histories(self, pool) -> int:
        """Reconcile only active tenant histories in this process, not the demo."""
        self.purge_expired()
        with self._lock:
            tenants = tuple(self._tenant_snapshots)
        failures = 0
        for tenant in tenants:
            try:
                async with _locked_erasure_snapshot(pool, tenant) as (snapshot, blocked):
                    with self._lock:
                        if blocked:
                            self.erase_organization(tenant)
                        else:
                            self._refresh_organization(tenant, snapshot)
            except Exception:
                self.erase_organization(tenant)
                failures += 1
        if failures:
            logger.warning("simulator_history reconciliation_failures=%d", failures)
        return len(tenants)

    def incrementa_tentativi_prenotazione(self, id_conv: str) -> int:
        """Incrementa il contatore di tentativi falliti e restituisce il nuovo valore."""
        with self._lock:
            now = time.monotonic()
            self._purge_expired(now)
            self._ensure_capacity(id_conv)
            self._updated_at[id_conv] = now
            self._tentativi_prenotazione[id_conv] = self._tentativi_prenotazione.get(id_conv, 0) + 1
            return self._tentativi_prenotazione[id_conv]

    def resetta_tentativi_prenotazione(self, id_conv: str) -> None:
        """Reset del contatore (il cliente ha fornito un dato nuovo o la prenotazione è completata)."""
        with self._lock:
            self._tentativi_prenotazione.pop(id_conv, None)
            if id_conv not in self._store:
                self.cancella(id_conv)

    def tentativi_prenotazione(self, id_conv: str) -> int:
        """Restituisce il numero corrente di tentativi falliti."""
        with self._lock:
            self._purge_expired(time.monotonic())
            return self._tentativi_prenotazione.get(id_conv, 0)


store = ConversationStore()
