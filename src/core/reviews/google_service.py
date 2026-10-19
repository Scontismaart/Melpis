import asyncio
import logging
import os
import re
import time
from datetime import timezone

from cryptography.fernet import Fernet
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleAuthRequest
from googleapiclient.discovery import build
from google.auth.exceptions import RefreshError

from src.core.google_feature_flags import google_business_enabled

logger = logging.getLogger(__name__)

# Scope per gestire recensioni e dati del profilo Business.
SCOPES = ["https://www.googleapis.com/auth/business.manage"]
REVIEWS_TABLE = "google_business_credentials"
STAR_RATING_MAP = {
    "ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5,
}
MAX_GOOGLE_REVIEW_PAGES = 5
MAX_GOOGLE_REVIEWS_PER_SYNC = 250
MAX_GOOGLE_FETCH_SECONDS = 30
MAX_GOOGLE_SYNC_SECONDS = 120


def _is_invalid_page_token(error: Exception) -> bool:
    status = getattr(getattr(error, "resp", None), "status", None)
    content = getattr(error, "content", b"")
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="ignore")
    text = f"{error} {content}".casefold()
    return status == 400 and "page" in text and "token" in text and any(
        marker in text for marker in ("invalid", "expired", "not valid")
    )


class GoogleBusinessService:
    """Client per le recensioni di Google Business Profile.

    Simmetrico a GoogleCalendarService: token cifrati con Fernet a riposo,
    refresh automatico del token scaduto, lock per-org contro refresh
    concorrenti. La chiamata di rete e' isolata in _list_reviews cosi' i
    test la mockano senza toccare l'API reale.
    """

    def __init__(self, repo, encryption_key):
        self.repo = repo
        if not encryption_key:
            raise ValueError(
                "ENCRYPTION_KEY mancante: richiesto per cifrare i token "
                "OAuth Google Business. Impostala in .env."
            )
        self.fernet = Fernet(encryption_key.encode() if isinstance(encryption_key, str) else encryption_key)
        self._locks: dict[str, asyncio.Lock] = {}
        self._lock_init = asyncio.Lock()

    async def _get_org_lock(self, org_id):
        async with self._lock_init:
            if org_id not in self._locks:
                self._locks[org_id] = asyncio.Lock()
            return self._locks[org_id]

    def _decrypt(self, value: str) -> str:
        return self.fernet.decrypt(value.encode()).decode()

    def _encrypt(self, value: str) -> str:
        return self.fernet.encrypt(value.encode()).decode()

    def encrypt_secret(self, value: str) -> str:
        return self._encrypt(value)

    def _get_client_config(self):
        if not google_business_enabled():
            raise RuntimeError("Google Business disabled")
        client_id = os.environ.get("GOOGLE_CLIENT_ID")
        client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise RuntimeError(
                "GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET mancanti in env."
            )
        return {
            "client_id": client_id,
            "client_secret": client_secret,
            "token_uri": "https://oauth2.googleapis.com/token",
        }

    async def _get_credentials(self, org_id):
        if not google_business_enabled():
            return None
        async with await self._get_org_lock(org_id):
            async with self.repo.pool.acquire() as conn:
                row = await conn.fetchrow(
                    f"SELECT * FROM {REVIEWS_TABLE} WHERE organization_id = $1",
                    org_id,
                )
                if not row:
                    return None
                try:
                    cfg = self._get_client_config()
                except RuntimeError as e:
                    logger.warning("business=no_client_config org_id=%s err=%s", org_id, e)
                    return None
                expiry = row["token_expiry"]
                if expiry and getattr(expiry, "tzinfo", None) is not None:
                    expiry = expiry.astimezone(timezone.utc).replace(tzinfo=None)
                creds = Credentials(
                    token=self._decrypt(row["access_token"]),
                    refresh_token=self._decrypt(row["refresh_token"]),
                    token_uri=cfg["token_uri"],
                    client_id=cfg["client_id"],
                    client_secret=cfg["client_secret"],
                    scopes=SCOPES,
                    expiry=expiry,
                )
                if creds.expired and creds.refresh_token:
                    try:
                        await asyncio.to_thread(creds.refresh, GoogleAuthRequest())
                        await conn.execute(
                            f"""UPDATE {REVIEWS_TABLE}
                                SET access_token = $2, token_expiry = $3, updated_at = NOW()
                                WHERE organization_id = $1""",
                            org_id,
                            self._encrypt(creds.token),
                            creds.expiry,
                        )
                    except RefreshError:
                        logger.error("business=token_revoked org_id=%s", org_id)
                        await conn.execute(
                            f"UPDATE {REVIEWS_TABLE} SET updated_at = NOW() WHERE organization_id = $1",
                            org_id,
                        )
                        return None
            return creds

    async def _build_service(self, org_id):
        if not google_business_enabled():
            return None
        creds = await self._get_credentials(org_id)
        if not creds:
            return None
        # Google Business Profile (ex "My Business") non e' nel discovery
        # statico bundled di googleapiclient: static_discovery=False forza il
        # fetch remoto del discovery doc. My Business API v4: le review
        # vivono in accounts/{account}/locations/{location}/reviews.
        return await asyncio.to_thread(
            build, "mybusiness", "v4", credentials=creds, static_discovery=False,
            discoveryServiceUrl="https://mybusiness.googleapis.com/$discovery/rest?version=v4",
        )

    async def _list_reviews(self, service, account_name, location_name, page_size=50,
                            max_pages=MAX_GOOGLE_REVIEW_PAGES,
                            max_reviews=MAX_GOOGLE_REVIEWS_PER_SYNC, metadata=None,
                            page_token=None):
        """Chiamata di rete isolata — mockata nei test.

        Ritorna le review grezze come restituite dall'API (dict). La firma
        prende service + identificativi cosi' i test costruiscono un fake
        senza passare per build().
        """
        if not google_business_enabled():
            return []
        if not isinstance(account_name, str) or not re.fullmatch(r"accounts/[A-Za-z0-9_-]+", account_name):
            raise ValueError("Invalid Google Business account resource")
        if not isinstance(location_name, str):
            raise ValueError("Invalid Google Business location resource")
        if re.fullmatch(r"locations/[A-Za-z0-9_-]+", location_name):
            parent = f"{account_name}/{location_name}"
        elif re.fullmatch(re.escape(account_name) + r"/locations/[A-Za-z0-9_-]+", location_name):
            parent = location_name
        else:
            raise ValueError("Google Business location does not belong to selected account")
        reviews = []
        start_token = page_token
        seen_tokens = set()
        for _ in range(max_pages):
            kwargs = {
                "parent": parent,
                "pageSize": page_size,
            }
            if page_token:
                kwargs["pageToken"] = page_token
            result = await asyncio.to_thread(
                service.accounts().locations().reviews().list(**kwargs).execute
            )
            page_reviews = result.get("reviews", [])
            remaining = max_reviews - len(reviews)
            reviews.extend(page_reviews[:remaining])
            next_token = result.get("nextPageToken")
            if len(page_reviews) > remaining:
                # Cannot resume in the middle of a page; restart this same
                # page on the next run and rely on durable review deduplication.
                if metadata is not None:
                    metadata.update({"complete": False, "pages": _ + 1,
                                     "reason": "review_limit_mid_page",
                                     "next_page_token": None, "start_page_token": start_token})
                return reviews
            if not next_token:
                if metadata is not None:
                    metadata.update({"complete": True, "pages": _ + 1,
                                     "reason": None, "next_page_token": None,
                                     "start_page_token": start_token})
                return reviews
            if len(reviews) >= max_reviews:
                if metadata is not None:
                    metadata.update({"complete": False, "pages": _ + 1, "reason": "review_limit",
                                     "next_page_token": next_token, "start_page_token": start_token})
                return reviews
            if next_token in seen_tokens:
                raise RuntimeError("Google reviews pagination token repeated")
            seen_tokens.add(next_token)
            page_token = next_token
        if metadata is not None:
            metadata.update({"complete": False, "pages": max_pages, "reason": "page_limit",
                             "next_page_token": page_token, "start_page_token": start_token})
        return reviews

    def _map_review(self, raw: dict) -> dict:
        comment = raw.get("comment", {}) or {}
        reviewer = raw.get("reviewer", {}) or {}
        star = STAR_RATING_MAP.get(raw.get("starRating", "").upper(), None)
        external_id = raw.get("reviewId")
        if not external_id:
            resource_name = raw.get("name") or ""
            match = __import__("re").fullmatch(r"accounts/[^/]+/locations/[^/]+/reviews/([^/]+)", resource_name)
            external_id = match.group(1) if match else None
        return {
            "external_id": external_id,
            "testo": comment.get("comment", ""),
            "valutazione_stelle": star,
            "fonte": "google",
            "autore": reviewer.get("displayName", ""),
        }

    async def _genera_bozza(self, testo, stelle, autore,
                           lingue_supportate=None, lingua_default=None,
                           profilo_attivita=None, contesto_documenti="",
                           provider_context=None, billing=None):
        """Generazione bozza isolata in un metodo — mockabile nei test
        come _build_service/_list_reviews, senza toccare il crew AI reale."""
        from src.core.crew_runner_review import genera_risposta_recensione
        return await asyncio.to_thread(
            genera_risposta_recensione,
            testo=testo, stelle=stelle, autore=autore,
            lingue_supportate=lingue_supportate,
            lingua_default=lingua_default,
            profilo_attivita=profilo_attivita,
            contesto_documenti=contesto_documenti,
            provider_context=provider_context,
            billing=billing,
        )

    async def fetch_reviews(self, org_id, page_size=50, *, actor_auth_user_id=None):
        if not google_business_enabled():
            return {"nuove": 0, "fallimenti": 0, "parziale": False}
        from src.core.reviews.idempotency import claim_google_review_sync
        try:
            async with claim_google_review_sync(self.repo.pool, str(org_id)) as claimed:
                if not claimed:
                    return {"nuove": 0, "fallimenti": 1, "saltate": 0, "parziale": True,
                            "pagine_lette": 0, "recensioni_lette": 0,
                            "parziale_motivo": ["sync_in_progress"]}
                return await self._fetch_reviews_locked(org_id, page_size, actor_auth_user_id=actor_auth_user_id)
        except Exception:
            logger.error("business=sync_claim_failed org_id=%s", org_id)
            return {"nuove": 0, "fallimenti": 1, "saltate": 0, "parziale": True,
                    "pagine_lette": 0, "recensioni_lette": 0,
                    "parziale_motivo": ["sync_unavailable"]}

    async def _fetch_reviews_locked(self, org_id, page_size=50, *, actor_auth_user_id=None):
        """Recupera e persiste le review Google dell'org (con dedup).

        Ritorna il numero di review nuove inserite (0 se non connessi o se
        account/location non ancora configurati).
        """
        async with self.repo.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT account_name, location_name, review_page_token, "
                f"review_page_account_name, review_page_location_name "
                f"FROM {REVIEWS_TABLE} WHERE organization_id = $1",
                org_id,
            )
        if not row or not row["account_name"] or not row["location_name"]:
            logger.warning("business=sync_missing_ids org_id=%s", org_id)
            return {"nuove": 0, "fallimenti": 0, "saltate": 0, "parziale": False}

        service = await self._build_service(org_id)
        if service is None:
            return {"nuove": 0, "fallimenti": 1, "saltate": 0, "parziale": True,
                    "pagine_lette": 0, "recensioni_lette": 0,
                    "parziale_motivo": ["google_service_unavailable"]}

        pagination = {"complete": True, "pages": 0}
        expected_cursor = row["review_page_token"]
        expected_cursor_account = row["review_page_account_name"]
        expected_cursor_location = row["review_page_location_name"]
        cursor = row["review_page_token"]
        if (row["review_page_account_name"] != row["account_name"]
                or row["review_page_location_name"] != row["location_name"]):
            cursor = None
        try:
            raw_reviews = await asyncio.wait_for(
                self._list_reviews(
                    service, row["account_name"], row["location_name"],
                    page_size=min(max(1, page_size), 50), metadata=pagination,
                    page_token=cursor,
                ), timeout=MAX_GOOGLE_FETCH_SECONDS,
            )
        except Exception as error:
            if cursor and _is_invalid_page_token(error):
                async with self.repo.pool.acquire() as conn:
                    cleared = await conn.fetchval(
                        f"""UPDATE {REVIEWS_TABLE}
                            SET review_page_token = NULL,
                                review_page_account_name = NULL,
                                review_page_location_name = NULL
                            WHERE organization_id = $1 AND account_name = $2 AND location_name = $3
                              AND review_page_token = $4
                              AND review_page_account_name = $2
                              AND review_page_location_name = $3
                            RETURNING 1""",
                        org_id, row["account_name"], row["location_name"], cursor,
                    )
                if cleared:
                    expected_cursor = expected_cursor_account = expected_cursor_location = None
                    cursor = None
                    pagination = {"complete": True, "pages": 0}
                    try:
                        raw_reviews = await asyncio.wait_for(
                            self._list_reviews(
                                service, row["account_name"], row["location_name"],
                                page_size=min(max(1, page_size), 50), metadata=pagination,
                                page_token=None,
                            ), timeout=MAX_GOOGLE_FETCH_SECONDS,
                        )
                    except Exception:
                        logger.error("business=review_fetch_failed org_id=%s", org_id)
                        return {"nuove": 0, "fallimenti": 1, "saltate": 0, "parziale": True,
                                "pagine_lette": 0, "recensioni_lette": 0,
                                "parziale_motivo": ["fetch_timeout_or_error"]}
                else:
                    return {"nuove": 0, "fallimenti": 1, "saltate": 0, "parziale": True,
                            "pagine_lette": 0, "recensioni_lette": 0,
                            "parziale_motivo": ["credentials_changed"]}
            else:
                logger.error("business=review_fetch_failed org_id=%s", org_id)
                return {"nuove": 0, "fallimenti": 1, "saltate": 0, "parziale": True,
                        "pagine_lette": 0, "recensioni_lette": 0,
                        "parziale_motivo": ["fetch_timeout_or_error"]}
        profilazione = None
        profilo_pubblico = None
        lingue = lingua_default = None
        billing = None
        nuove = 0
        fallimenti = 0
        saltate = 0
        parziale = not pagination.get("complete", True)
        parziale_motivi = [pagination["reason"]] if pagination.get("reason") else []
        sync_started = time.monotonic()
        time_limit_hit = False
        import asyncpg
        from src.core.reviews.idempotency import claim_external_review
        for raw in raw_reviews:
            if time.monotonic() - sync_started >= MAX_GOOGLE_SYNC_SECONDS:
                parziale = True
                time_limit_hit = True
                parziale_motivi.append("sync_time_limit")
                fallimenti += 1
                break
            m = self._map_review(raw)
            if not m["external_id"] or not m["testo"]:
                # These records are permanently unprocessable (no stable ID or
                # no review text), so skip/report them without stalling cursor.
                saltate += 1
                continue
            try:
              async with claim_external_review(self.repo.pool, str(org_id), m["external_id"]) as claimed:
                if not claimed:
                    fallimenti += 1
                    continue
                # Dedupe before profile/RAG/LLM; the unique index remains the race backstop.
                if m["external_id"] and await self.repo.get_review_by_external_id(str(org_id), m["external_id"]):
                    continue
                from src.core.reviews.ai_governance import authorize_review_generation
                billing = await authorize_review_generation(self.repo, str(org_id))
                if profilazione is None:
                    from src.core.onboarding import get_profile
                    profilazione = await get_profile(str(org_id), self.repo) or {}
                    profilo_pubblico = {
                        "nome_attivita": profilazione.get("nome_attivita") or "",
                        "verticale": profilazione.get("verticale") or "",
                        "tono": profilazione.get("tono") or "",
                        "lingue_supportate": profilazione.get("lingue_supportate") or None,
                        "lingua_default": profilazione.get("lingua_default") or None,
                    }
                    lingue = profilazione.get("lingue_supportate")
                    lingua_default = profilazione.get("lingua_default")
                from src.core.documenti.rag_context import recupera_contesto_documenti

                contesto = await recupera_contesto_documenti(
                    str(org_id), m["testo"], self.repo
                )
                from src.core.reviews.ai_governance import generic_review_fallback, validate_review_draft
                if not contesto.testo.strip():
                    output = generic_review_fallback(m["testo"])
                else:
                    from src.core.provider_accounting import ProviderAccountingContext
                    provider_context = await ProviderAccountingContext.open(
                        getattr(self.repo, "pool", None),
                        org_id,
                        task_type="review",
                        actor_auth_user_id=actor_auth_user_id,
                        context={"trace_id": None},
                    )
                    try:
                        output = await self._genera_bozza(
                            m["testo"], m["valutazione_stelle"], m["autore"],
                            lingue_supportate=lingue,
                            lingua_default=lingua_default,
                            profilo_attivita=profilo_pubblico,
                            contesto_documenti=contesto.testo,
                            provider_context=provider_context, billing=billing,
                        )
                    finally:
                        await provider_context.close()
                validate_review_draft(output.bozza_risposta, m["testo"], contesto.testo)
                await self.repo.create_review(
                    organization_id=org_id,
                    testo=m["testo"],
                    valutazione_stelle=m["valutazione_stelle"],
                    fonte="google",
                    autore=m["autore"],
                    external_id=m["external_id"],
                    bozza_risposta=output.bozza_risposta,
                    sentiment=output.sentiment,
                    categoria=output.categoria,
                    richiede_revisione_urgente=output.richiede_revisione_urgente,
                    stato="bozza_generata",
                )
                nuove += 1
            except asyncpg.UniqueViolationError:
                # Dedup: la review con questo external_id esiste gia' per
                # l'org (partial unique index), il sync e' idempotente.
                continue
            except Exception:
                fallimenti += 1
                parziale = True
                parziale_motivi.append("review_processing_failed")
                logger.error("business=review_process_fail org_id=%s", org_id)
        if fallimenti == 0 and not time_limit_hit:
            # A page-bounded run advances to the next page. A complete run resets
            # the cursor; failed/time-limited runs retain their starting cursor,
            # so a retry deduplicates saved rows and retries remaining work.
            async with self.repo.pool.acquire() as conn:
                if not pagination.get("complete") and pagination.get("next_page_token"):
                    updated = await conn.fetchval(
                        f"""UPDATE {REVIEWS_TABLE}
                            SET review_page_token = $2,
                                review_page_account_name = $3,
                                review_page_location_name = $4
                            WHERE organization_id = $1 AND account_name = $3 AND location_name = $4
                              AND review_page_token IS NOT DISTINCT FROM $5
                              AND review_page_account_name IS NOT DISTINCT FROM $6
                              AND review_page_location_name IS NOT DISTINCT FROM $7
                            RETURNING 1""",
                        org_id, pagination["next_page_token"], row["account_name"],
                        row["location_name"], expected_cursor,
                        expected_cursor_account, expected_cursor_location,
                    )
                elif pagination.get("complete"):
                    updated = await conn.fetchval(
                        f"""UPDATE {REVIEWS_TABLE}
                            SET last_sync_at = NOW(), review_page_token = NULL,
                                review_page_account_name = NULL,
                                review_page_location_name = NULL
                            WHERE organization_id = $1 AND account_name = $2 AND location_name = $3
                              AND review_page_token IS NOT DISTINCT FROM $4
                              AND review_page_account_name IS NOT DISTINCT FROM $5
                              AND review_page_location_name IS NOT DISTINCT FROM $6
                            RETURNING 1""",
                        org_id, row["account_name"], row["location_name"], expected_cursor,
                        expected_cursor_account, expected_cursor_location,
                    )
                else:
                    updated = True
            if not updated:
                parziale = True
                parziale_motivi.append("credentials_changed")
        return {"nuove": nuove, "fallimenti": fallimenti, "saltate": saltate,
                "parziale": parziale or fallimenti > 0,
                "pagine_lette": pagination.get("pages", 0),
                "recensioni_lette": len(raw_reviews),
                "parziale_motivo": sorted(set(parziale_motivi)) or None}
