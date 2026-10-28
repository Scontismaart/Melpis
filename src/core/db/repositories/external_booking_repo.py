"""Repository specializzato per credenziali e sincronizzazione dei gestionali esterni (Task 2).

Gestisce:
1. Tabella external_booking_credentials: envelope JSON di credenziali (API key, company login, OAuth token, ecc.)
   cifrato a riposo con Fernet (AES). Flessibile per qualsiasi provider senza richiedere nuove migrazioni schema.
2. Tabella external_booking_sync: tracking atomico e deduplica per le mutazioni esterne (pattern Send-Then-Mark).
3. Conforme all'Invariante #1 (Tenant Isolation) con estensione TenantScopedRepository.
"""
from __future__ import annotations

import json
import os
import uuid
import logging
from typing import Any
from cryptography.fernet import Fernet

from src.core.db.scoping import TenantScopedRepository, system_scope

logger = logging.getLogger(__name__)


class ExternalBookingRepository(TenantScopedRepository):
    """Repository tenant-scoped per credenziali e stato di sincronizzazione dei gestionali esterni."""

    def __init__(self, pool):
        self.pool = pool

    # ── Crittografia Simmetrica a Riposo (Invariante #10) ────────

    @classmethod
    def _get_fernet(cls) -> Fernet:
        key = os.environ.get("ENCRYPTION_KEY")
        if not key:
            raise RuntimeError(
                "ENCRYPTION_KEY mancante: richiesta per la cifratura delle credenziali dei gestionali."
            )
        return Fernet(key.encode() if isinstance(key, str) else key)

    @classmethod
    def encrypt_secret(cls, plaintext: str) -> str:
        """Cifra un valore sensibile in formato stringa Fernet."""
        if not plaintext:
            return ""
        return cls._get_fernet().encrypt(plaintext.encode()).decode()

    @classmethod
    def decrypt_secret(cls, ciphertext: str) -> str:
        """Decifra un valore precedentemente cifrato con Fernet."""
        if not ciphertext:
            return ""
        return cls._get_fernet().decrypt(ciphertext.encode()).decode()

    @classmethod
    def encrypt_credentials(cls, credentials: dict[str, Any]) -> str:
        """Cifra un envelope arbitrario di credenziali (dict) in un blob Fernet."""
        json_str = json.dumps(credentials or {})
        return cls.encrypt_secret(json_str)

    @classmethod
    def decrypt_credentials(cls, ciphertext: str) -> dict[str, Any]:
        """Decifra un blob Fernet e restituisce il dizionario delle credenziali."""
        if not ciphertext:
            return {}
        try:
            json_str = cls.decrypt_secret(ciphertext)
            return json.loads(json_str)
        except Exception:
            return {}

    # ── Gestione Credenziali Gestionale (Per-Organization) ────────

    async def save_credentials(
        self,
        organization_id: uuid.UUID | str,
        provider: str,
        credentials: dict[str, Any],
        config: dict[str, Any] | None = None,
        is_active: bool = True,
    ) -> dict[str, Any]:
        """Salva o aggiorna le credenziali del gestionale come envelope cifrato a riposo."""
        if isinstance(organization_id, str):
            organization_id = uuid.UUID(organization_id)

        encrypted_envelope = self.encrypt_credentials(credentials)
        config_json = json.dumps(config or {})

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO external_booking_credentials (
                    organization_id, provider, credentials_encrypted,
                    config, is_active, updated_at
                )
                VALUES ($1, $2, $3, $4::jsonb, $5, NOW())
                ON CONFLICT (organization_id) DO UPDATE
                SET provider = EXCLUDED.provider,
                    credentials_encrypted = EXCLUDED.credentials_encrypted,
                    config = EXCLUDED.config,
                    is_active = EXCLUDED.is_active,
                    updated_at = NOW()
                WHERE external_booking_credentials.organization_id = $1
                RETURNING *
                """,
                organization_id,
                provider,
                encrypted_envelope,
                config_json,
                is_active,
            )
            return self._format_credential_row(dict(row))

    async def get_credentials(self, organization_id: uuid.UUID | str) -> dict[str, Any] | None:
        """Recupera le credenziali attive dell'organizzazione decifrando l'envelope in-memory."""
        if isinstance(organization_id, str):
            organization_id = uuid.UUID(organization_id)

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM external_booking_credentials
                WHERE organization_id = $1 AND is_active = TRUE
                """,
                organization_id,
            )
            if not row:
                return None
            return self._format_credential_row(dict(row))

    async def update_credential_fields(
        self, organization_id: uuid.UUID | str, fields: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Merge parziale dell'envelope credenziali (es. token ruotati dopo refresh OAuth).

        Legge la riga esistente, fonde SOLO le chiavi fornite (il resto resta
        intatto: client_id/secret, property_id, provider, config), ricifra
        l'envelope e scrive con WHERE organization_id. Ritorna None se nessuna
        riga esiste per il tenant. Mai loggare i valori (segreti).
        """
        if isinstance(organization_id, str):
            organization_id = uuid.UUID(organization_id)
        if not fields:
            return None

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM external_booking_credentials
                WHERE organization_id = $1
                """,
                organization_id,
            )
            if not row:
                return None

            current = dict(row)
            envelope = self.decrypt_credentials(current.get("credentials_encrypted", ""))
            envelope.update({k: v for k, v in fields.items() if v is not None})

            updated = await conn.fetchrow(
                """
                UPDATE external_booking_credentials
                SET credentials_encrypted = $2, updated_at = NOW()
                WHERE organization_id = $1
                RETURNING *
                """,
                organization_id,
                self.encrypt_credentials(envelope),
            )
            return self._format_credential_row(dict(updated)) if updated else None

    async def delete_credentials(self, organization_id: uuid.UUID | str) -> bool:
        """Rimuove le credenziali per l'organizzazione."""
        if isinstance(organization_id, str):
            organization_id = uuid.UUID(organization_id)

        async with self.pool.acquire() as conn:
            res = await conn.execute(
                """
                DELETE FROM external_booking_credentials
                WHERE organization_id = $1
                """,
                organization_id,
            )
            return res.endswith("1")

    async def update_mode(
        self, organization_id: uuid.UUID | str, new_mode: str
    ) -> dict[str, Any] | None:
        """Patch only mode on the current row, preserving concurrent owner edits.

        None means absent row or a provider policy that no longer permits the
        mode. Malformed JSON raises a PostgreSQL error; never replace it with {}.
        """
        from src.core.bookings.router import BookingMode

        mode = BookingMode(new_mode).value
        if isinstance(organization_id, str):
            organization_id = uuid.UUID(organization_id)

        async with self.pool.acquire() as conn:
            updated = await conn.fetchrow(
                """
                UPDATE external_booking_credentials
                SET config = jsonb_set(config, '{mode}', to_jsonb($2::text), true),
                    updated_at = NOW()
                WHERE organization_id = $1
                  AND (lower(provider) NOT IN ('zak', 'wubook', 'wubook_zak')
                       OR $2::text IN ('shadow', 'local_only'))
                RETURNING *
                """,
                organization_id,
                mode,
            )
            return self._format_credential_row(dict(updated)) if updated else None

    async def get_latest_sync(
        self, organization_id: uuid.UUID | str
    ) -> dict[str, Any] | None:
        """Restituisce l'ultimo record di sincronizzazione per l'organizzazione."""
        if isinstance(organization_id, str):
            organization_id = uuid.UUID(organization_id)

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, organization_id, idempotency_key, sync_status,
                       external_booking_id, sync_error, retry_count,
                       created_at, updated_at
                FROM external_booking_sync
                WHERE organization_id = $1
                ORDER BY created_at DESC
                LIMIT 1
                """,
                organization_id,
            )
            return dict(row) if row else None

    def _format_credential_row(self, row: dict[str, Any]) -> dict[str, Any]:
        """Formatta la riga decifrando l'envelope delle credenziali e parsando il config."""
        cfg = row.get("config")
        if isinstance(cfg, str):
            try:
                row["config"] = json.loads(cfg)
            except Exception:
                row["config"] = {}
        elif not isinstance(cfg, dict):
            row["config"] = {}

        creds = self.decrypt_credentials(row.get("credentials_encrypted", ""))
        row["credentials"] = creds

        # Proprietà di comodità per i campi comuni se presenti nel dizionario
        row["api_key"] = creds.get("api_key")
        row["api_secret"] = creds.get("api_secret")
        row["company_login"] = creds.get("company_login")
        row["client_id"] = creds.get("client_id")
        row["client_secret"] = creds.get("client_secret")
        row["refresh_token"] = creds.get("refresh_token")
        row["property_id"] = creds.get("property_id")
        return row

    # ── Tracking Sincronizzazione ed Idempotenza (Invariante #4) ──

    async def claim_sync_slot(
        self,
        organization_id: uuid.UUID | str,
        idempotency_key: str,
        provider: str,
        internal_booking_id: uuid.UUID | str | None = None,
    ) -> tuple[dict[str, Any] | None, bool]:
        """Claim atomico Send-Then-Mark: un solo chiamante vince per (org, key).

        Sostituisce la sequenza non atomica pre-check + prepare (check-then-act)
        che permetteva doppie chiamate esterne in race. Ritorna (riga, acquired):
        - riga nuova -> (row pending, True): il chiamante ESEGUE la call esterna;
        - riga esistente 'failed' -> transizione atomica a pending, (row, True):
          retry esplicito acquisito;
        - riga esistente 'synced'/'pending' -> (row, False): replay / guard,
          NON chiamare l'esterna.
        """
        if isinstance(organization_id, str):
            organization_id = uuid.UUID(organization_id)
        if isinstance(internal_booking_id, str):
            internal_booking_id = uuid.UUID(internal_booking_id)

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO external_booking_sync (
                    id, organization_id, idempotency_key, provider, internal_booking_id,
                    sync_status, last_attempt_at, updated_at
                )
                VALUES ($1, $2, $3, $4, $5, 'pending', NOW(), NOW())
                ON CONFLICT (organization_id, idempotency_key) DO NOTHING
                RETURNING *
                """,
                uuid.uuid4(),
                organization_id,
                idempotency_key,
                provider,
                internal_booking_id,
            )
            if row:
                return dict(row), True

            row = await conn.fetchrow(
                """
                UPDATE external_booking_sync
                SET sync_status = 'pending',
                    sync_error = NULL,
                    last_attempt_at = NOW(),
                    updated_at = NOW()
                WHERE organization_id = $1
                  AND idempotency_key = $2
                  AND sync_status = 'failed'
                RETURNING *
                """,
                organization_id,
                idempotency_key,
            )
            if row:
                return dict(row), True

            row = await conn.fetchrow(
                """
                SELECT * FROM external_booking_sync
                WHERE organization_id = $1 AND idempotency_key = $2
                """,
                organization_id,
                idempotency_key,
            )
            return (dict(row) if row else None), False

    async def record_sync_prepare(
        self,
        organization_id: uuid.UUID | str,
        idempotency_key: str,
        provider: str,
        internal_booking_id: uuid.UUID | str | None = None,
        sync_status: str = "pending",
    ) -> dict[str, Any]:
        """Registra lo step di prepare sul DB locale prima di chiamare il gateway esterno (Send-Then-Mark).

        Compatibilita': delega al claim atomico e restituisce la riga. A differenza
        del vecchio upsert incondizionato, NON resetta piu' una riga 'synced' o
        'pending' altrui a pending.
        """
        row, _acquired = await self.claim_sync_slot(
            organization_id, idempotency_key, provider, internal_booking_id
        )
        return row or {}

    async def record_sync_success(
        self,
        organization_id: uuid.UUID | str,
        idempotency_key: str,
        external_booking_id: str,
    ) -> dict[str, Any] | None:
        """Marca il sync come completato con successo memorizzando l'ID esterno restituito dal gestionale."""
        if isinstance(organization_id, str):
            organization_id = uuid.UUID(organization_id)

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE external_booking_sync
                SET sync_status = 'synced',
                    external_booking_id = $3,
                    sync_error = NULL,
                    updated_at = NOW()
                WHERE organization_id = $1 AND idempotency_key = $2
                RETURNING *
                """,
                organization_id,
                idempotency_key,
                external_booking_id,
            )
            return dict(row) if row else None

    async def record_sync_failure(
        self,
        organization_id: uuid.UUID | str,
        idempotency_key: str,
        error_message: str,
        sync_status: str = "failed",
    ) -> dict[str, Any] | None:
        """Registra il fallimento del sync o lo stato di pending_retry per il worker di background."""
        if isinstance(organization_id, str):
            organization_id = uuid.UUID(organization_id)

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE external_booking_sync
                SET sync_status = $3,
                    sync_error = $4,
                    retry_count = retry_count + 1,
                    last_attempt_at = NOW(),
                    updated_at = NOW()
                WHERE organization_id = $1 AND idempotency_key = $2
                RETURNING *
                """,
                organization_id,
                idempotency_key,
                sync_status,
                error_message,
            )
            return dict(row) if row else None

    async def get_sync_record(
        self,
        organization_id: uuid.UUID | str,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        """Cerca un record di sync esistente per verificare lo stato di idempotenza."""
        if isinstance(organization_id, str):
            organization_id = uuid.UUID(organization_id)

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM external_booking_sync
                WHERE organization_id = $1 AND idempotency_key = $2
                """,
                organization_id,
                idempotency_key,
            )
            return dict(row) if row else None

    # Alias per compatibilità
    get_sync_by_idempotency_key = get_sync_record

    # ── Job di Riconciliazione Asincrona Worker ───────────────────

    @system_scope("worker queue: scans pending external booking sync across tenants for retry")
    async def get_pending_syncs(self, older_than_seconds: int = 60, limit: int = 50) -> list[dict[str, Any]]:
        """Recupera le sincronizzazioni rimaste in sospeso per riconciliazione da parte del worker."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM external_booking_sync
                WHERE sync_status IN ('pending', 'pending_retry')
                  AND (last_attempt_at IS NULL OR last_attempt_at < NOW() - make_interval(secs => $1))
                ORDER BY created_at ASC
                LIMIT $2
                """,
                older_than_seconds,
                limit,
            )
            return [dict(r) for r in rows]
