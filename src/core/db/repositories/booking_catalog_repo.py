"""Administrative catalog persistence. Never admits or confirms a booking."""
from __future__ import annotations

import hashlib
import json
from uuid import UUID

from src.core.bookings.catalog_contracts import validate_catalog_json
from src.core.db.scoping import TenantScopedRepository


class CatalogConflict(ValueError):
    pass


class CatalogUnavailable(RuntimeError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def decode(value):
    return json.loads(value) if isinstance(value, str) else value


class BookingCatalogRepository(TenantScopedRepository):
    def __init__(self, booking_repo):
        self.booking_repo = booking_repo
        self.pool = booking_repo.pool

    def _state(self, config, org):
        config = decode(config)
        if not isinstance(config, dict):
            raise CatalogUnavailable("Invalid settings configuration")
        if "catalog_v2" not in config:
            return {"revision": 0, "draft": None, "published": None, "published_revision": None}
        state = config["catalog_v2"]
        if not isinstance(state, dict) or set(state) != {"revision", "draft", "published", "published_revision"}:
            raise CatalogUnavailable("Invalid catalog state")
        revision = state["revision"]
        published_revision = state["published_revision"]
        if type(revision) is not int or revision <= 0:
            raise CatalogUnavailable("Invalid catalog revision")
        if (state["published"] is None) != (published_revision is None):
            raise CatalogUnavailable("Inconsistent publication state")
        if published_revision is not None and (
            type(published_revision) is not int or not 0 < published_revision <= revision
        ):
            raise CatalogUnavailable("Invalid publication revision")
        for key in ("draft", "published"):
            body = state[key]
            if body is None:
                continue
            try:
                catalog = validate_catalog_json(canonical(body), organization_id=org).catalog
            except (ValueError, TypeError):
                raise CatalogUnavailable("Invalid stored catalogue") from None
            expected = revision if key == "draft" else published_revision
            if catalog.revision != expected:
                raise CatalogUnavailable("Inconsistent catalogue revision")
        return state

    async def _verify_evidence(self, conn, org, state):
        if state["revision"]:
            found = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM booking_catalog_requests WHERE organization_id=$1 AND applied_revision=$2)",
                org, state["revision"],
            )
            if not found:
                raise CatalogUnavailable("Catalog state has no administrative receipt")
        if state["published"] is not None:
            snapshot = await conn.fetchval(
                "SELECT catalog FROM booking_catalog_publications WHERE organization_id=$1 AND revision=$2",
                org, state["published_revision"],
            )
            if decode(snapshot) != state["published"]:
                raise CatalogUnavailable("Published catalogue has no matching snapshot")

    async def read(self, organization_id):
        org = UUID(str(organization_id))
        async with self.pool.acquire() as conn, conn.transaction(isolation="repeatable_read", readonly=True):
            # Assert the migration is present even for historic tenants without settings.
            ready = await conn.fetchval("SELECT to_regclass('public.booking_catalog_requests') IS NOT NULL AND to_regclass('public.booking_catalog_publications') IS NOT NULL")
            if not ready:
                raise CatalogUnavailable("Catalogue migration is not installed")
            row = await conn.fetchrow(
                "SELECT config FROM booking_settings WHERE organization_id=$1", org)
            state = self._state(row["config"] if row else {}, org)
            await self._verify_evidence(conn, org, state)
            sector = await self._sector(conn, org)
            return {**state, "organization_sector": sector, "runtime_enabled": False}

    async def _sector(self, conn, org):
        profile = await conn.fetchval("SELECT business_profile FROM organizations WHERE id=$1", org)
        profile = decode(profile)
        return profile.get("verticale") if isinstance(profile, dict) else None

    async def write(self, organization_id, actor_user_id, auth_user_id, *, action,
                    expected_revision, request_id, catalog=None):
        """Caller must authorize current owner/MFA BEFORE this transaction/replay."""
        org, actor, request = UUID(str(organization_id)), UUID(str(actor_user_id)), UUID(str(request_id))
        if action not in {"draft_saved", "published"} or type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("Invalid catalogue operation")
        if action == "draft_saved":
            model = validate_catalog_json(canonical(catalog), organization_id=org).catalog
            if model.revision != expected_revision + 1:
                raise CatalogConflict("La revisione della bozza non corrisponde")
            catalog = model.model_dump(mode="json")
        elif catalog is not None:
            raise ValueError("Publication does not accept a new catalogue")
        digest = hashlib.sha256(canonical({
            "action": action, "expected_revision": expected_revision,
            "catalog": catalog, "actor": str(actor),
        }).encode("utf-8")).hexdigest()

        async with self.booking_repo.booking_settings_lock(org), self.booking_repo._connection() as conn:
            ready = await conn.fetchval("SELECT to_regclass('public.booking_catalog_requests') IS NOT NULL AND to_regclass('public.booking_catalog_publications') IS NOT NULL")
            if not ready:
                raise CatalogUnavailable("Catalogue migration is not installed")
            row = await conn.fetchrow("SELECT id,config FROM booking_settings WHERE organization_id=$1 FOR UPDATE", org)
            state = self._state(row["config"] if row else {}, org)
            await self._verify_evidence(conn, org, state)
            previous = await conn.fetchrow(
                "SELECT actor_user_id,payload_hash,applied_revision FROM booking_catalog_requests WHERE organization_id=$1 AND request_id=$2",
                org, request,
            )
            sector = await self._sector(conn, org)
            if previous:
                if previous["actor_user_id"] != actor or previous["payload_hash"] != digest:
                    raise CatalogConflict("Identificativo richiesta già usato per un'altra operazione")
                return {**state, "runtime_enabled": False, "organization_sector": sector,
                        "applied_revision": previous["applied_revision"], "replayed": True}
            if expected_revision != state["revision"]:
                raise CatalogConflict("Configurazione modificata: ricarica il catalogo")
            revision = expected_revision + 1
            if action == "draft_saved":
                if catalog["sector"] != sector:
                    raise ValueError("Il settore deve corrispondere al profilo dell'attività")
                state = {**state, "revision": revision, "draft": catalog}
            else:
                if state["draft"] is None:
                    raise CatalogConflict("Nessuna bozza da pubblicare")
                publication = {**state["draft"], "revision": revision}
                model = validate_catalog_json(canonical(publication), organization_id=org).catalog
                if model.sector != sector:
                    raise CatalogConflict("Il profilo dell'attività è cambiato: aggiorna la bozza")
                state = {"revision": revision, "draft": None, "published": model.model_dump(mode="json"),
                         "published_revision": revision}
            # Only patch this namespace, on the current row. Never overwrite A05/Calendar.
            saved = await conn.fetchrow("""INSERT INTO booking_settings(organization_id,config)
                VALUES($1,jsonb_build_object('catalog_v2',$2::jsonb))
                ON CONFLICT(organization_id) DO UPDATE SET
                    config=jsonb_set(booking_settings.config,'{catalog_v2}',$2::jsonb,true),updated_at=NOW()
                RETURNING id""", org, canonical(state))
            if action == "published":
                await conn.execute("""INSERT INTO booking_catalog_publications(organization_id,revision,catalog)
                    VALUES($1,$2,$3::jsonb)""", org, revision, canonical(state["published"]))
            await conn.execute("""INSERT INTO booking_catalog_requests
                (organization_id,request_id,actor_user_id,action,payload_hash,applied_revision)
                VALUES($1,$2,$3,$4,$5,$6)""", org, request, actor, action, digest, revision)
            await conn.execute("""INSERT INTO audit_log
                (organization_id,user_id,auth_user_id,action,target_table,target_id,details)
                VALUES($1,$2,$3,$4,'booking_settings',$5,$6::jsonb)""",
                org, actor, str(auth_user_id), "booking_catalog." + action, saved["id"],
                canonical({"request_id": str(request), "revision": revision}))
            return {**state, "runtime_enabled": False, "organization_sector": sector,
                    "applied_revision": revision, "replayed": False}

    async def audit(self, organization_id):
        org = UUID(str(organization_id))
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""SELECT action,created_at,details->>'revision' AS revision,
                details->>'request_id' AS request_id FROM audit_log
                WHERE organization_id=$1 AND action IN ('booking_catalog.draft_saved','booking_catalog.published')
                ORDER BY created_at DESC,id DESC LIMIT 50""", org)
            return {"events": [dict(row) for row in rows]}
