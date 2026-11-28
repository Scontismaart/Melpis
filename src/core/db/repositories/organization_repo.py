import json
import os
import uuid
from datetime import date
from cryptography.fernet import Fernet

from src.core.db.scoping import TenantScopedRepository, system_scope
from src.core.billing.plans import TRIAL_DAYS, TRIAL_MESSAGES_LIMIT


class OrganizationRepository(TenantScopedRepository):
    """Repository specializzato per il dominio Organizzazioni, Tenant, Profili e Configurazione."""

    def __init__(self, pool):
        self.pool = pool

    # ── Profilo & Configurazione Organizzazione ───────────────────

    async def get_organization(self, organization_id: uuid.UUID | str) -> dict | None:
        """Riga minima dell'organizzazione (nome + business_profile per il
        responder/simulatore). None se l'org non esiste."""
        if isinstance(organization_id, str):
            organization_id = uuid.UUID(organization_id)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT id, name, business_profile
                   FROM organizations WHERE id = $1""",
                organization_id,
            )
            return dict(row) if row else None

    @system_scope("root PK delete, cascade DB, endpoint owner-only")
    async def delete_organization(self, organization_id: uuid.UUID | str) -> None:
        if isinstance(organization_id, str):
            organization_id = uuid.UUID(organization_id)
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM organizations WHERE id = $1", organization_id)

    async def get_org_business_profile(self, organization_id):
        """Profilo business a livello organizzazione (canale-agnostico).
        Implementazione canonica unificata."""
        if isinstance(organization_id, str):
            try:
                organization_id = uuid.UUID(organization_id)
            except Exception:
                pass
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT business_profile FROM organizations WHERE id = $1", organization_id)
            if not row or not row["business_profile"]:
                return {}
            bp = row["business_profile"]
            if isinstance(bp, str):
                try:
                    bp = json.loads(bp)
                except Exception:
                    bp = {}
            return bp

    async def update_org_business_profile(self, organization_id, business_profile: dict):
        if isinstance(organization_id, str):
            try:
                organization_id = uuid.UUID(organization_id)
            except Exception:
                pass
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE organizations SET business_profile = $2::jsonb WHERE id = $1",
                organization_id,
                json.dumps(business_profile),
            )

    # ── Onboarding ────────────────────────────────────────────────

    @staticmethod
    def _json_fields_onboarding(result: dict) -> dict:
        for key in ("servizi", "regole_escalation", "profilo", "lingue_supportate"):
            if isinstance(result.get(key), str):
                result[key] = json.loads(result[key])
        return result

    async def get_onboarding_profile(self, organization_id):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM onboarding_profiles WHERE organization_id = $1",
                organization_id,
            )
            return self._json_fields_onboarding(dict(row)) if row else None

    async def save_onboarding_profile(self, organization_id, verticale, nome_attivita,
                                      orari, tono, servizi, regole_escalation,
                                      whatsapp_collegato, documenti_importati, profilo,
                                      lingue_supportate=None, lingua_default=None,
                                      descrizione=""):
        """Upsert del profilo onboarding dell'org + sync atomico su
        organizations.business_profile in una sola transazione."""
        if lingue_supportate is None:
            lingue_supportate = ["it"]
        if lingua_default is None:
            lingua_default = "it"
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow("""
                    INSERT INTO onboarding_profiles (organization_id, verticale,
                                                     nome_attivita, orari, tono,
                                                     descrizione,
                                                     servizi, regole_escalation,
                                                     whatsapp_collegato,
                                                     documenti_importati, profilo,
                                                     lingue_supportate, lingua_default)
                    VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9, $10,
                            $11::jsonb, $12::jsonb, $13)
                    ON CONFLICT (organization_id) DO UPDATE SET
                        verticale = EXCLUDED.verticale,
                        nome_attivita = EXCLUDED.nome_attivita,
                        orari = EXCLUDED.orari,
                        tono = EXCLUDED.tono,
                        descrizione = EXCLUDED.descrizione,
                        servizi = EXCLUDED.servizi,
                        regole_escalation = EXCLUDED.regole_escalation,
                        whatsapp_collegato = EXCLUDED.whatsapp_collegato,
                        documenti_importati = EXCLUDED.documenti_importati,
                        profilo = EXCLUDED.profilo,
                        lingue_supportate = EXCLUDED.lingue_supportate,
                        lingua_default = EXCLUDED.lingua_default,
                        updated_at = NOW()
                    RETURNING *
                """, organization_id, verticale, nome_attivita, orari, tono,
                descrizione,
                json.dumps(servizi), json.dumps(regole_escalation),
                whatsapp_collegato, documenti_importati, json.dumps(profilo),
                json.dumps(lingue_supportate), lingua_default)
                await conn.execute(
                    "UPDATE organizations SET business_profile = $2::jsonb WHERE id = $1",
                    organization_id,
                    json.dumps(profilo),
                )
            return self._json_fields_onboarding(dict(row))

    # ── Auth & Memberships ────────────────────────────────────────

    async def get_auth_access_allowed(self, auth_user_id: str) -> bool:
        """Check durable principal policy on every authenticated request."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT provisioned_at, disabled_at FROM auth_access_lifecycle WHERE auth_user_id = $1",
                uuid.UUID(auth_user_id),
            )
        if row is None or row["disabled_at"] is not None:
            return False
        # No memberships are normal only BEFORE the first successful grant.
        # Once provisioned, account access needs a remaining legitimate grant.
        return row["provisioned_at"] is None or bool(
            await self.get_memberships_by_auth(auth_user_id)
        )

    async def disable_auth_access(self, auth_user_id: str) -> bool:
        """Trusted operator-only capability, not exposed as a client route/RPC.

        UPDATE locks the same durable row as provisioning. Once committed,
        subsequent requests using ANY already-issued JWT are denied.
        """
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE auth_access_lifecycle SET disabled_at = COALESCE(disabled_at, NOW()) "
                "WHERE auth_user_id = $1",
                uuid.UUID(auth_user_id),
            )
        return result == "UPDATE 1"

    async def get_membership_by_auth(self, auth_user_id: str, organization_id: str) -> dict | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT om.ruolo, om.organization_id, up.id as user_id
                FROM organization_memberships om
                JOIN user_profiles up ON up.id = om.user_id
                WHERE up.auth_user_id = $1 AND om.organization_id = $2::uuid
                  AND NOT EXISTS (SELECT 1 FROM gdpr_erasure_barriers b
                    WHERE b.organization_id=$2::uuid AND b.scope='organization')
            """, auth_user_id, organization_id)
            return dict(row) if row else None

    @system_scope("risoluzione multi-org da JWT validato server-side")
    async def get_memberships_by_auth(self, auth_user_id: str) -> list[dict]:
        """Tutti i membership dell'utente. Fonte unica per la risoluzione del
        tenant server-side (task18): l'org NON si deduce più da un header
        client, ma dall'identità nel JWT validato."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT om.ruolo, om.organization_id, up.id as user_id
                FROM organization_memberships om
                JOIN user_profiles up ON up.id = om.user_id
                WHERE up.auth_user_id = $1
                  AND NOT EXISTS (SELECT 1 FROM gdpr_erasure_barriers b
                    WHERE b.organization_id=om.organization_id AND b.scope='organization')
            """, auth_user_id)
            return [dict(r) for r in rows]

    async def get_organization_owners(self, org_id: str) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT up.id, up.email, up.nome
                   FROM user_profiles up
                   JOIN organization_memberships om ON om.user_id = up.id
                   WHERE om.organization_id = $1::uuid AND om.ruolo = 'owner'
                     AND NOT EXISTS (SELECT 1 FROM gdpr_erasure_barriers b
                       WHERE b.organization_id=$1::uuid AND b.scope='organization')""",
                org_id
            )
            return [dict(r) for r in rows]

    async def list_team_members(self, org_id: str) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT up.id AS user_id, up.nome, up.email, om.ruolo
                FROM organization_memberships om
                JOIN user_profiles up ON up.id = om.user_id
                WHERE om.organization_id = $1::uuid
                  AND om.ruolo IN ('owner', 'manager', 'staff')
                  AND NOT EXISTS (SELECT 1 FROM gdpr_erasure_barriers b
                    WHERE b.organization_id=$1::uuid AND b.scope='organization')
                ORDER BY
                  CASE om.ruolo WHEN 'owner' THEN 0 WHEN 'manager' THEN 1 ELSE 2 END,
                  up.nome
            """, org_id)
            return [dict(r) for r in rows]

    async def create_organization_with_owner(
        self,
        auth_user_id: str,
        nome_attivita: str,
        trial_days: int = TRIAL_DAYS,
    ) -> dict:
        """Compatibility wrapper: never bypass the first-provisioning policy."""
        return await self.get_or_create_organization_with_owner(
            auth_user_id, nome_attivita, trial_days
        )

    @system_scope("provisioning JIT org al primo accesso OAuth")
    async def get_or_create_organization_with_owner(
        self,
        auth_user_id: str,
        nome_attivita: str,
        trial_days: int = TRIAL_DAYS,
    ) -> dict:
        """Allow existing access or one first provisioning, never re-provision."""
        uid = uuid.UUID(auth_user_id)
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1::text, 0))",
                str(uid),
            )
            access = await conn.fetchrow(
                "SELECT provisioned_at, disabled_at FROM auth_access_lifecycle "
                "WHERE auth_user_id = $1 FOR UPDATE",
                uid,
            )
            if access is None or access["disabled_at"] is not None:
                raise PermissionError("Account access denied")
            existing = await conn.fetchrow("""
                SELECT om.organization_id::text AS organization_id
                FROM organization_memberships om
                JOIN user_profiles up ON up.id = om.user_id
                WHERE up.auth_user_id = $1
                  AND NOT EXISTS (SELECT 1 FROM gdpr_erasure_barriers b
                    WHERE b.organization_id=om.organization_id AND b.scope='organization')
                LIMIT 1
            """, uid)
            if existing:
                return dict(existing)
            if access["provisioned_at"] is not None:
                raise PermissionError("Previously provisioned account has no membership")
            org_id = uuid.uuid4()
            row = await conn.fetchrow("""
                WITH new_org AS (
                    INSERT INTO organizations
                        (id, name, subscription_status, trial_start, trial_end, messages_limit, users_limit)
                    VALUES ($1, $2, 'trialing', NOW(),
                            NOW() + make_interval(days => $3), $4, 3)
                    RETURNING id
                )
                INSERT INTO organization_memberships
                    (organization_id, user_id, ruolo, joined_at)
                SELECT o.id, up.id, 'owner', NOW()
                FROM new_org o
                JOIN user_profiles up ON up.auth_user_id = $5::uuid
                RETURNING organization_id, user_id
            """, org_id, nome_attivita, trial_days, TRIAL_MESSAGES_LIMIT, uid)
            if not row:
                raise RuntimeError(
                    "user_profiles non trovato per l'utente OAuth"
                )
        return {"organization_id": str(org_id)}

    # ── Canali & Account WhatsApp / Meta ───────────────────────────

    @system_scope("tenant-resolution: lookup da webhook Meta (identita' platform-unique, pre-auth)")
    async def get_org_by_phone_number_id(self, phone_number_id: str):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT o.id as organization_id, o.name, o.business_profile,
                       wa.id as account_id, wa.phone_number_id, wa.waba_id,
                       wa.access_token, wa.verify_token
                FROM whatsapp_accounts wa
                JOIN organizations o ON o.id = wa.organization_id
                WHERE wa.phone_number_id = $1
            """, phone_number_id)
            return dict(row) if row else None

    @system_scope("tenant-resolution: lookup da webhook Meta (identita' platform-unique, pre-auth)")
    async def get_org_by_waba_id(self, waba_id: str):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT o.id as organization_id, o.name, o.business_profile,
                       wa.id as account_id, wa.phone_number_id, wa.waba_id,
                       wa.access_token, wa.verify_token
                FROM whatsapp_accounts wa
                JOIN organizations o ON o.id = wa.organization_id
                WHERE wa.waba_id = $1
            """, waba_id)
            return dict(row) if row else None

    @system_scope("tenant-resolution: lookup fan-out da webhook Meta (waba_id 1:N, pre-auth)")
    async def get_orgs_by_waba_id(self, waba_id: str) -> list:
        """Tutte le organizzazioni collegate a un waba_id (un WABA contiene N numeri).

        Il waba_id NON e' globalmente unique per realta' Meta (WABA condiviso tra
        org/utenze): il chiamante deve applicare la write a OGNI org restituita,
        ciascuna scoped sul proprio organization_id. Ordinamento deterministico.
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT o.id as organization_id, o.name, o.business_profile,
                       wa.id as account_id, wa.phone_number_id, wa.waba_id,
                       wa.access_token, wa.verify_token
                FROM whatsapp_accounts wa
                JOIN organizations o ON o.id = wa.organization_id
                WHERE wa.waba_id = $1
                ORDER BY o.id
            """, waba_id)
            return [dict(r) for r in rows]

    async def get_tenant_config(self, org_id):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT wa.access_token, wa.phone_number_id, wa.waba_id,
                       o.business_profile
                FROM whatsapp_accounts wa
                JOIN organizations o ON o.id = wa.organization_id
                WHERE wa.organization_id = $1
                LIMIT 1
            """, org_id)
            return dict(row) if row else None

    @staticmethod
    def encrypt_token(plaintext: str) -> str:
        key = os.environ.get("ENCRYPTION_KEY")
        if not key:
            raise RuntimeError("ENCRYPTION_KEY not set")
        return Fernet(key.encode()).encrypt(plaintext.encode()).decode()

    @staticmethod
    def decrypt_token(ciphertext: str) -> str:
        key = os.environ.get("ENCRYPTION_KEY")
        if not key:
            raise RuntimeError("ENCRYPTION_KEY not set")
        return Fernet(key.encode()).decrypt(ciphertext.encode()).decode()

    async def save_tenant_config(self, org_id, phone_number_id: str, waba_id: str, access_token: str):
        encrypted = self.encrypt_token(access_token)
        async with self.pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT id FROM whatsapp_accounts WHERE organization_id = $1", org_id
            )
            if existing:
                row = await conn.fetchrow("""
                    UPDATE whatsapp_accounts
                    SET phone_number_id = $2, waba_id = $3, access_token = $4, updated_at = NOW()
                    WHERE organization_id = $1
                    RETURNING *
                """, org_id, phone_number_id, waba_id, encrypted)
            else:
                row = await conn.fetchrow("""
                    INSERT INTO whatsapp_accounts (id, organization_id, phone_number_id, waba_id, access_token)
                    VALUES ($1, $2, $3, $4, $5)
                    RETURNING *
                """, uuid.uuid4(), org_id, phone_number_id, waba_id, encrypted)
            return dict(row)

    async def delete_tenant_config(self, org_id) -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM whatsapp_accounts WHERE organization_id = $1", org_id
            )
            return result.endswith("1")

    # ── Template WhatsApp ─────────────────────────────────────────

    async def upsert_template(self, organization_id, name, language, category, status, components):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO whatsapp_templates (id, organization_id, name, language, category, status, components)
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
                ON CONFLICT (organization_id, name, language) DO UPDATE
                    SET status = $6, components = $7::jsonb, updated_at = NOW()
                RETURNING *
            """, uuid.uuid4(), organization_id, name, language, category, status,
                json.dumps(components))
            return dict(row)

    async def update_template_status(self, organization_id, name, language, status,
                                      rejected_reason=None):
        """Aggiorna lo stato del template SOLO entro l'organizzazione:
        organization_id e' obbligatorio e va in WHERE (mai nel SET)."""
        async with self.scoped_conn(organization_id) as conn:
            await conn.execute("""
                UPDATE whatsapp_templates
                SET status = $3,
                    rejected_reason = COALESCE($4, rejected_reason),
                    updated_at = NOW()
                WHERE organization_id = $1 AND name = $2 AND language = $5
            """, organization_id, name, status, rejected_reason, language)

    # ── Email Configs ─────────────────────────────────────────────

    async def add_email_config(self, organization_id, indirizzo, is_active=True):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO email_configs (id, organization_id, indirizzo, is_active)
                VALUES ($1, $2, $3, $4)
                RETURNING *
            """, uuid.uuid4(), organization_id, indirizzo, is_active)
            return dict(row)

    async def list_email_configs(self, organization_id):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM email_configs WHERE organization_id = $1 ORDER BY created_at",
                organization_id,
            )
            return [dict(r) for r in rows]

    async def remove_email_config(self, organization_id, indirizzo):
        async with self.pool.acquire() as conn:
            result = await conn.execute("""
                DELETE FROM email_configs
                WHERE organization_id = $1 AND indirizzo = $2
            """, organization_id, indirizzo)
            return result != "DELETE 0"
