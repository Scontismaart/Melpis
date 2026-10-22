from collections import deque
import time


class ConversationStore:
    def __init__(self, max_scambi: int = 10, ttl_seconds: int = 60 * 60 * 24,
                 max_conversations: int = 5000):
        self._store: dict[str, deque[tuple[str, str]]] = {}
        self._max = max_scambi
        self._ttl_seconds = ttl_seconds
        self._max_conversations = max(1, max_conversations)
        self._updated_at: dict[str, float] = {}
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
        return self._purge_expired(time.monotonic())

    def aggiungi(self, id_conv: str, msg_cliente: str, msg_bot: str) -> None:
        now = time.monotonic()
        self._purge_expired(now)
        if id_conv not in self._store:
            if len(self._store) >= self._max_conversations:
                if self._store:
                    oldest = min(
                        self._store,
                        key=lambda key: self._updated_at.get(key, 0.0),
                    )
                    self.cancella(oldest)
            self._store[id_conv] = deque(maxlen=self._max)
        self._store[id_conv].append((msg_cliente, msg_bot))
        self._updated_at[id_conv] = now

    def recupera_cronologia(self, id_conv: str) -> list[tuple[str, str]]:
        now = time.monotonic()
        self._purge_expired(now)
        if id_conv in self._store:
            self._updated_at[id_conv] = now
        return list(self._store.get(id_conv, []))

    def cancella(self, id_conv: str) -> None:
        self._store.pop(id_conv, None)
        self._updated_at.pop(id_conv, None)
        self._tentativi_prenotazione.pop(id_conv, None)

    def erase_organization(self, organization_id: str) -> int:
        """Remove only this tenant's simulator keys; anonymous demo is separate."""
        keys = [key for key in self._store if key.startswith(organization_id + ':')]
        for key in keys:
            self.cancella(key)
        return len(keys)

    def incrementa_tentativi_prenotazione(self, id_conv: str) -> int:
        """Incrementa il contatore di tentativi falliti e restituisce il nuovo valore."""
        now = time.monotonic()
        self._purge_expired(now)
        self._updated_at[id_conv] = now
        self._tentativi_prenotazione[id_conv] = self._tentativi_prenotazione.get(id_conv, 0) + 1
        return self._tentativi_prenotazione[id_conv]

    def resetta_tentativi_prenotazione(self, id_conv: str) -> None:
        """Reset del contatore (il cliente ha fornito un dato nuovo o la prenotazione è completata)."""
        self._tentativi_prenotazione.pop(id_conv, None)

    def tentativi_prenotazione(self, id_conv: str) -> int:
        """Restituisce il numero corrente di tentativi falliti."""
        return self._tentativi_prenotazione.get(id_conv, 0)


store = ConversationStore()
