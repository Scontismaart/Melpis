"""Commercial quota protocol: reserve, persist once, release safely.

The sole commercial counter remains organizations.messages_used_this_period.
Pending reservations live in usage_events; simulation_requests is the existing
private response/idempotency store. Callers authenticate and authorize the
current tenant before every action.
"""

import asyncio
import hashlib
import json
import math
import re
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from src.core.billing.suspension import (
    AIGenerationBlocked,
    assert_ai_generation_allowed,
)
from src.core.db.quota_provenance import require_quota_trust_epoch
from src.core.guardrails.validator import applica_guardrail, valida_risposta
from src.models.schemas import ProfiloAttivita, RispostaOutput

_EVENT = "system.quota_reservation"
_WORKER = uuid5(NAMESPACE_URL, "melpis:quota:inbound-worker:v1")
_CAPABILITY = object()
_LEASE = timedelta(seconds=30)
_CHANNELS = frozenset({"inbound", "simulator", "qa"})
_CURRENT_LEASE = ContextVar("a04_caller_lease", default=None)


class QuotaConflict(AIGenerationBlocked):
    """The operation cannot safely be reused or completed."""


class QuotaLease:
    """Caller-owned renewal from reservation through final persistence.

    Cancellation stops renewal only. It never proves a failed provider call or
    releases capacity. The database claim remains authoritative at every POST
    and commit, including when a worker loses this heartbeat.
    """

    INTERVAL_SECONDS = 8

    def __init__(self):
        self._task = None
        self._failed = False
        self._stopped = False

    async def __aenter__(self):
        self._context_token = _CURRENT_LEASE.set(self)
        return self

    def bind(self, reservations, operation, ticket):
        if self._task is not None or self._stopped:
            raise RuntimeError("Quota lease already bound")
        self.reservations = reservations
        self.operation = operation
        self.ticket = ticket
        self._initial_ticket = ticket
        self._task = asyncio.create_task(self._renew())

    @classmethod
    def current(cls, operation, ticket):
        lease = _CURRENT_LEASE.get()
        if (lease is not None and lease._task is not None
                and lease.operation is operation and lease._initial_ticket is ticket):
            return lease
        return None

    def assert_healthy(self):
        if self._failed or self._stopped:
            _deny()

    async def checkpoint(self):
        self.assert_healthy()
        try:
            self.ticket = await self.reservations.renew(self.operation, self.ticket)
        except Exception:
            self._failed = True
            raise
        if self._failed:
            _deny()

    async def _renew(self):
        try:
            while True:
                await asyncio.sleep(self.INTERVAL_SECONDS)
                await self.checkpoint()
        except asyncio.CancelledError:
            raise
        except Exception:
            self._failed = True

    async def __aexit__(self, *_exc):
        try:
            await self.stop()
        finally:
            _CURRENT_LEASE.reset(self._context_token)

    async def stop(self):
        self._stopped = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None


def _deny():
    raise QuotaConflict("Quota operation requires reconciliation")


def _uuid(value):
    return type(value) is UUID and value.int != 0


def _aware(value):
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class QuotaOperation:
    organization_id: UUID
    source_id: UUID
    channel: str
    payload_hash: str
    auth_user_id: UUID | None = None

    def __post_init__(self):
        if (not _uuid(self.organization_id) or not _uuid(self.source_id)
                or type(self.channel) is not str or self.channel not in _CHANNELS
                or type(self.payload_hash) is not str
                or re.fullmatch(r"[0-9a-f]{64}", self.payload_hash) is None):
            _deny()
        if self.channel == "inbound":
            if self.auth_user_id is not None:
                _deny()
        elif not _uuid(self.auth_user_id):
            _deny()

    @property
    def actor_id(self):
        # An internal worker identifier, never an auth.users identity or grant.
        return _WORKER if self.channel == "inbound" else self.auth_user_id

    @property
    def id(self):
        return uuid5(NAMESPACE_URL, f"melpis:quota:v1:{self.organization_id}:"
                     f"{self.actor_id}:{self.channel}:{self.source_id}")


@dataclass(frozen=True, slots=True, repr=False)
class QuotaTicket:
    operation_id: UUID
    claim_id: UUID
    epoch: UUID
    period_id: UUID
    lease_end: datetime
    _capability: object

    def __repr__(self):
        return "QuotaTicket(<internal>)"

    def __reduce_ex__(self, protocol):
        raise TypeError("Quota tickets cannot be serialized")


@dataclass(frozen=True, slots=True, repr=False)
class FinalQuotaResponse:
    operation_id: UUID
    payload_hash: str
    claim_id: UUID
    epoch: UUID
    period_id: UUID
    serialized: str
    _capability: object

    def __repr__(self):
        return "FinalQuotaResponse(<internal>)"

    def __reduce_ex__(self, protocol):
        raise TypeError("Final quota responses cannot be serialized")


def validate_final_response(
    operation: QuotaOperation, ticket: QuotaTicket, response: RispostaOutput, *,
    context_chunks: list[dict] | None = None, profile: ProfiloAttivita | None = None,
) -> FinalQuotaResponse:
    """Run the real deterministic guardrail; JSON approval flags have no effect.

    A blocked fallback is not a valid generated reply. Callers may still send
    deterministic human escalation outside the commercial AI response count.
    """
    if (type(operation) is not QuotaOperation or type(ticket) is not QuotaTicket
            or ticket._capability is not _CAPABILITY or ticket.operation_id != operation.id
            or not isinstance(response, RispostaOutput)):
        _deny()
    if response._guardrail_blocked:
        raise AIGenerationBlocked("Final AI response did not pass guardrails")
    verdict = valida_risposta(response, context_chunks, profile)
    if verdict.azione == "block":
        raise AIGenerationBlocked("Final AI response did not pass guardrails")
    guarded = applica_guardrail(response.model_copy(deep=True), verdict)
    return FinalQuotaResponse(operation.id, operation.payload_hash,
                              ticket.claim_id, ticket.epoch, ticket.period_id,
                              _json(guarded.model_dump(mode="json")), _CAPABILITY)


def validate_qa_final_response(
    operation: QuotaOperation, ticket: QuotaTicket, response: dict, *,
    context_chunks: list[dict] | None = None, profile: ProfiloAttivita | None = None,
) -> FinalQuotaResponse:
    """Guardrail and bind the complete, safe document-Q&A response for replay."""
    if (type(operation) is not QuotaOperation or operation.channel != "qa"
            or type(ticket) is not QuotaTicket or not isinstance(response, dict)
            or type(response.get("risposta")) is not str
            or type(response.get("fonti")) is not list):
        _deny()
    safe_sources = []
    for source in response["fonti"]:
        if not isinstance(source, dict) or source.get("is_active") is False:
            continue
        name = source.get("fonte", source.get("documento"))
        kind = source.get("tipo")
        score = source.get("score")
        status = source.get("stato", "indicizzata")
        priority = source.get("priorita")
        if (type(name) is not str or not name.strip() or len(name) > 300
                or type(kind) is not str or len(kind) > 80
                or type(status) is not str or len(status) > 80
                or isinstance(score, bool) or not isinstance(score, (int, float))
                or not math.isfinite(float(score))):
            _deny()
        if type(priority) is not int or priority < 1 or priority > 5:
            _deny()
        safe_sources.append({
            "documento": name.strip(), "tipo": kind, "score": float(score),
            "stato": status, "is_active": True, "priorita": priority,
        })
    validated = validate_final_response(
        operation, ticket,
        RispostaOutput(risposta=response["risposta"], richiede_umano=False,
                       motivo="", categoria="document_qa"),
        context_chunks=context_chunks, profile=profile,
    )
    guarded = json.loads(validated.serialized)
    payload = {"risposta": guarded["risposta"], "fonti": safe_sources}
    return FinalQuotaResponse(
        operation.id, operation.payload_hash, ticket.claim_id, ticket.epoch,
        ticket.period_id, _json(payload), _CAPABILITY,
    )


def _period(billing, now, organization_id):
    status = billing.get("subscription_status")
    trial = status == "trialing"
    anchor = billing.get("trial_start" if trial else "current_period_start")
    end = billing.get("trial_end" if trial else "current_period_end")
    subscription = billing.get("subscription_id")
    if (status not in {"active", "trialing"} or not _aware(anchor) or not _aware(end)
            or not anchor <= now < end):
        _deny()
    if trial:
        end = min(end, anchor + timedelta(days=7))
        if now >= end:
            _deny()
    if subscription is not None and (type(subscription) is not str
            or re.fullmatch(r"sub_[A-Za-z0-9]+", subscription) is None):
        _deny()
    if not trial and subscription is None:
        _deny()
    # Linking a Checkout subscription during the signup trial does not create
    # a fresh free allowance or hide pending reservations from that trial.
    identity = f"{organization_id}:{'trial' if trial else 'paid'}:{'' if trial else subscription}:{anchor.astimezone(timezone.utc).isoformat()}"
    return uuid5(NAMESPACE_URL, identity), end


def _entitlement(billing, *, for_reservation=False):
    """Never allow an inconsistent stored trial limit to grant extra replies."""
    verified = dict(billing)
    if (verified.get("subscription_status") == "trialing"
            and type(verified.get("messages_limit")) is int):
        verified["messages_limit"] = min(150, verified["messages_limit"])
    # A reservation request at the quota boundary must return the explicit
    # exhausted result so callers can offer human escalation. Validate the
    # entitlement independently of remaining capacity, then account for
    # committed usage and pending reservations atomically below.
    if (for_reservation and type(verified.get("messages_limit")) is int
            and type(verified.get("messages_used_this_period")) is int
            and verified["messages_used_this_period"] >= verified["messages_limit"]):
        check = dict(verified, messages_used_this_period=0)
        assert_ai_generation_allowed(check)
    else:
        assert_ai_generation_allowed(verified)
    return verified


def _decode(row, operation):
    if not row or row["event_type"] != _EVENT or row["quantity"] != 0:
        _deny()
    value = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
    if not isinstance(value, dict) or set(value) != {
        "version", "epoch", "period_id", "payload_hash", "channel", "state",
        "claim_id", "lease_start", "lease_end", "period_end", "response_hash",
    }:
        _deny()
    if (type(value["version"]) is not int or value["version"] != 1
            or value["payload_hash"] != operation.payload_hash or value["channel"] != operation.channel
            or value["state"] not in {"reserved", "committed", "released"}):
        _deny()
    try:
        for field in ("epoch", "period_id", "claim_id"):
            if type(value[field]) is not str or not _uuid(UUID(value[field])):
                _deny()
        start, end = (datetime.fromisoformat(value[key]) for key in ("lease_start", "lease_end"))
        original_end = datetime.fromisoformat(value["period_end"])
        if (not _aware(start) or not _aware(end) or not _aware(original_end)
                or not timedelta(0) < end - start <= _LEASE or end > original_end):
            _deny()
        if value["state"] == "committed":
            if type(value["response_hash"]) is not str or re.fullmatch(r"[0-9a-f]{64}", value["response_hash"]) is None:
                _deny()
        elif value["response_hash"] is not None:
            _deny()
    except (ValueError, TypeError, KeyError):
        _deny()
    return value


class QuotaReservations:
    """Internal database component; contains no provider or outbound calls."""

    def __init__(self, pool):
        self.pool = pool

    async def _lock(self, conn, operation, *, for_release=False):
        if type(operation) is not QuotaOperation:
            _deny()
        epoch = await require_quota_trust_epoch(conn)
        if operation.auth_user_id is not None and not for_release:
            access = await conn.fetchrow(
                """SELECT disabled_at FROM auth_access_lifecycle
                    WHERE auth_user_id=$1 FOR SHARE""", operation.auth_user_id,
            )
            if access is None or access["disabled_at"] is not None:
                _deny()
        billing = await conn.fetchrow(
            """SELECT subscription_status, subscription_id, trial_start, trial_end,
                      current_period_start, current_period_end, messages_limit,
                      messages_used_this_period, ai_accounting_blocked
                 FROM organizations WHERE id=$1 FOR UPDATE""", operation.organization_id,
        )
        if billing is None:
            _deny()
        if operation.auth_user_id is not None and not for_release:
            membership = await conn.fetchrow(
                    """SELECT om.ruolo FROM organization_memberships om
                    JOIN user_profiles up ON up.id=om.user_id
                    WHERE om.organization_id=$1 AND up.auth_user_id=$2
                      AND om.ruolo IN ('owner','manager','staff')
                      AND om.joined_at IS NOT NULL FOR SHARE OF om,up""",
                operation.organization_id, operation.auth_user_id,
            )
            if membership is None:
                _deny()
        entry = await conn.fetchrow(
            """SELECT event_type, quantity, metadata FROM usage_events
                WHERE organization_id=$1 AND id=$2 FOR UPDATE""",
            operation.organization_id, operation.id,
        )
        response = await conn.fetchrow(
            """SELECT payload_hash, claim_token, status, response, reserved_at FROM simulation_requests
                WHERE organization_id=$1 AND auth_user_id=$2 AND request_id=$3 FOR UPDATE""",
            operation.organization_id, operation.actor_id, operation.id,
        )
        if operation.channel == "inbound":
            message = await conn.fetchrow(
                """SELECT direction, status, deleted_at, sent_at, replied_at,
                          billed_at, ai_reply_cache, quota_exceeded_at FROM messages
                    WHERE organization_id=$1 AND id=$2 FOR UPDATE""",
                operation.organization_id, operation.source_id,
            )
            if (not message or message["direction"] != "inbound" or message["deleted_at"] is not None):
                _deny()
            metadata = _decode(entry, operation) if entry is not None else None
            committed = metadata is not None and metadata["state"] == "committed"
            if committed:
                # An inbound cache is only a delivery retry source after the
                # A04 transaction committed both its request record and the
                # billed message cache. Do not trust legacy or independently
                # modified cache contents.
                if (message["billed_at"] is None or message["ai_reply_cache"] is None
                        or response is None or response["status"] != "completed"
                        or response["payload_hash"].strip() != operation.payload_hash
                        or response["claim_token"] != UUID(metadata["claim_id"])):
                    _deny()
                try:
                    cached = (json.loads(message["ai_reply_cache"])
                              if isinstance(message["ai_reply_cache"], str)
                              else message["ai_reply_cache"])
                    stored = (json.loads(response["response"])
                              if isinstance(response["response"], str)
                              else response["response"])
                except (TypeError, ValueError):
                    _deny()
                if (not isinstance(cached, dict) or not isinstance(stored, dict)
                        or _hash(cached) != metadata["response_hash"]
                        or _hash(stored) != metadata["response_hash"]):
                    _deny()
            elif (message["status"] != "processing"
                    or any(message[key] is not None for key in
                           ("sent_at", "replied_at", "billed_at", "ai_reply_cache", "quota_exceeded_at"))):
                _deny()
        return epoch, dict(billing), entry, response, await conn.fetchval("SELECT clock_timestamp()")

    async def _save(self, conn, operation, metadata):
        await conn.execute(
            """UPDATE usage_events SET metadata=$3::jsonb
                WHERE organization_id=$1 AND id=$2""",
            operation.organization_id, operation.id, _json(metadata),
        )

    def _ticket(self, operation, metadata):
        return QuotaTicket(operation.id, UUID(metadata["claim_id"]), UUID(metadata["epoch"]),
                           UUID(metadata["period_id"]), datetime.fromisoformat(metadata["lease_end"]),
                           _CAPABILITY)

    def _check_ticket(self, operation, ticket, metadata, epoch, period, now, *, live=True):
        if (type(ticket) is not QuotaTicket or ticket._capability is not _CAPABILITY
                or ticket.operation_id != operation.id or ticket.claim_id != UUID(metadata["claim_id"])
                or ticket.epoch != epoch or metadata["epoch"] != str(epoch)
                or ticket.period_id != period or metadata["period_id"] != str(period)
                or ticket.lease_end > datetime.fromisoformat(metadata["lease_end"])
                or metadata["state"] != "reserved"):
            _deny()
        if live and not datetime.fromisoformat(metadata["lease_start"]) <= now < datetime.fromisoformat(metadata["lease_end"]):
            _deny()

    async def verify_ticket(self, conn, operation: QuotaOperation, ticket: QuotaTicket):
        """Revalidate a private ticket on the caller's transaction/connection."""
        epoch, billing, row, response, now = await self._lock(conn, operation)
        metadata = _decode(row, operation)
        billing = _entitlement(billing)
        period, _ = _period(billing, now, operation.organization_id)
        self._check_ticket(operation, ticket, metadata, epoch, period, now)
        if (not response or response["status"] != "reserved" or response["response"] is not None
                or response["claim_token"] != ticket.claim_id):
            _deny()
        return billing

    async def reserve(self, operation: QuotaOperation):
        async with self.pool.acquire() as conn, conn.transaction():
            epoch, billing, row, response, now = await self._lock(conn, operation)
            if row:
                metadata = _decode(row, operation)
                if metadata["epoch"] != str(epoch):
                    _deny()
                if (not response or response["payload_hash"].strip() != operation.payload_hash
                        or response["claim_token"] != UUID(metadata["claim_id"])):
                    _deny()
                if metadata["state"] == "committed":
                    if not response or response["status"] != "completed":
                        _deny()
                    payload = json.loads(response["response"]) if isinstance(response["response"], str) else response["response"]
                    if (response["payload_hash"].strip() != operation.payload_hash
                            or _hash(payload) != metadata["response_hash"]):
                        _deny()
                    return {"status": "replay", "response": payload}
                expected = "reserved" if metadata["state"] == "reserved" else "failed"
                if response["status"] != expected or response["response"] is not None:
                    _deny()
            elif response:
                # Never adopt a legacy simulator row or charge from another path.
                _deny()
            billing = _entitlement(billing, for_reservation=True)
            period, period_end = _period(billing, now, operation.organization_id)
            if row:
                if metadata["period_id"] != str(period):
                    _deny()
                period_end = min(period_end, datetime.fromisoformat(metadata["period_end"]))
                if now >= period_end:
                    _deny()
                if metadata["state"] == "reserved":
                    # Expiry alone is not proof that generation failed.
                    return {"status": "in_progress" if now < datetime.fromisoformat(metadata["lease_end"])
                            else "reconciliation_required"}
            pending = await conn.fetchval(
                """SELECT count(*) FROM usage_events
                    WHERE organization_id=$1 AND event_type=$2 AND quantity=0
                      AND metadata->>'state'='reserved' AND metadata->>'period_id'=$3""",
                operation.organization_id, _EVENT, str(period),
            )
            if billing["messages_used_this_period"] + pending >= billing["messages_limit"]:
                return {"status": "quota_exceeded"}
            claim_id = uuid4()
            metadata = {"version": 1, "epoch": str(epoch), "period_id": str(period),
                        "payload_hash": operation.payload_hash, "channel": operation.channel,
                        "state": "reserved", "claim_id": str(claim_id),
                        "lease_start": now.isoformat(), "lease_end": min(now + _LEASE, period_end).isoformat(),
                        "period_end": period_end.isoformat(),
                        "response_hash": None}
            if row:
                await self._save(conn, operation, metadata)
                await conn.execute(
                    """UPDATE simulation_requests SET status='reserved', response=NULL,
                           completed_at=NULL, reserved_at=$4, claim_token=$5
                        WHERE organization_id=$1 AND auth_user_id=$2 AND request_id=$3""",
                    operation.organization_id, operation.actor_id, operation.id, now, claim_id,
                )
            else:
                await conn.execute(
                    """INSERT INTO usage_events(id,organization_id,event_type,quantity,metadata)
                        VALUES ($1,$2,$3,0,$4::jsonb)""",
                    operation.id, operation.organization_id, _EVENT, _json(metadata),
                )
                await conn.execute(
                    """INSERT INTO simulation_requests
                           (organization_id,auth_user_id,request_id,payload_hash,claim_token,reserved_at)
                        VALUES ($1,$2,$3,$4,$5,$6)""",
                    operation.organization_id, operation.actor_id, operation.id, operation.payload_hash, claim_id, now,
                )
            return {"status": "reserved", "ticket": self._ticket(operation, metadata), "billing": billing}

    async def commit(self, operation: QuotaOperation, ticket: QuotaTicket, final: FinalQuotaResponse):
        if (type(operation) is not QuotaOperation or type(ticket) is not QuotaTicket
                or ticket._capability is not _CAPABILITY or ticket.operation_id != operation.id
                or type(final) is not FinalQuotaResponse or final._capability is not _CAPABILITY
                or final.operation_id != operation.id or final.payload_hash != operation.payload_hash
                or final.claim_id != ticket.claim_id or final.epoch != ticket.epoch
                or final.period_id != ticket.period_id):
            _deny()
        payload = json.loads(final.serialized)
        async with self.pool.acquire() as conn, conn.transaction():
            epoch, billing, row, response, now = await self._lock(conn, operation)
            metadata = _decode(row, operation)
            if metadata["state"] == "committed":
                # Idempotent persistence; never overwrite the first response.
                if (metadata["claim_id"] != str(ticket.claim_id) or metadata["epoch"] != str(epoch)
                        or ticket.epoch != epoch or ticket.period_id != UUID(metadata["period_id"])
                        or ticket.lease_end > datetime.fromisoformat(metadata["lease_end"])
                        or not response or response["status"] != "completed"):
                    _deny()
                stored = json.loads(response["response"]) if isinstance(response["response"], str) else response["response"]
                if _hash(stored) != metadata["response_hash"]:
                    _deny()
                return stored
            billing = _entitlement(billing)
            period, _ = _period(billing, now, operation.organization_id)
            self._check_ticket(operation, ticket, metadata, epoch, period, now)
            if (not response or response["status"] != "reserved" or response["response"] is not None
                    or response["claim_token"] != ticket.claim_id
                    or response["payload_hash"].strip() != operation.payload_hash):
                _deny()
            await conn.execute(
                """UPDATE simulation_requests SET status='completed',response=$4::jsonb,completed_at=$5
                    WHERE organization_id=$1 AND auth_user_id=$2 AND request_id=$3""",
                operation.organization_id, operation.actor_id, operation.id, final.serialized, now,
            )
            if operation.channel == "inbound":
                await conn.execute(
                    """UPDATE messages SET ai_reply_cache=$3::jsonb,
                           ai_reply_generated_at=$4,billed_at=$4
                        WHERE organization_id=$1 AND id=$2""",
                    operation.organization_id, operation.source_id, final.serialized, now,
                )
            await conn.execute(
                """UPDATE organizations SET messages_used_this_period=messages_used_this_period+1
                    WHERE id=$1""", operation.organization_id,
            )
            metadata.update(state="committed", response_hash=_hash(payload))
            await self._save(conn, operation, metadata)
            return payload

    async def renew(self, operation: QuotaOperation, ticket: QuotaTicket):
        """Refresh a live owner's lease; never revive an expired claim or trial."""
        async with self.pool.acquire() as conn, conn.transaction():
            epoch, billing, row, response, now = await self._lock(conn, operation)
            metadata = _decode(row, operation)
            billing = _entitlement(billing)
            period, current_end = _period(billing, now, operation.organization_id)
            self._check_ticket(operation, ticket, metadata, epoch, period, now)
            if (not response or response["status"] != "reserved" or response["response"] is not None
                    or response["claim_token"] != ticket.claim_id):
                _deny()
            end = min(current_end, datetime.fromisoformat(metadata["period_end"]))
            if now >= end:
                _deny()
            metadata.update(lease_start=now.isoformat(), lease_end=min(now + _LEASE, end).isoformat())
            await self._save(conn, operation, metadata)
            return self._ticket(operation, metadata)

    async def release(self, operation: QuotaOperation, ticket: QuotaTicket):
        """Release only after locked DB checks prove no response was persisted.

        This invalidates the old claim before returning, so an in-flight worker
        cannot subsequently persist its result. Technical provider costs remain
        accounted separately. A completed response is never refunded.
        """
        if (type(operation) is not QuotaOperation or type(ticket) is not QuotaTicket
                or ticket._capability is not _CAPABILITY or ticket.operation_id != operation.id):
            _deny()
        async with self.pool.acquire() as conn, conn.transaction():
            # Cleanup of the original private ticket can cancel unpersisted
            # work after principal revocation; it cannot expose a response,
            # authorize new generation or refund a committed counter.
            epoch, _billing, row, response, now = await self._lock(conn, operation, for_release=True)
            metadata = _decode(row, operation)
            if metadata["state"] == "committed":
                return False
            if metadata["state"] == "released":
                if (metadata["claim_id"] != str(ticket.claim_id) or metadata["epoch"] != str(epoch)
                        or ticket.epoch != epoch or ticket.period_id != UUID(metadata["period_id"])
                        or not response or response["status"] != "failed"
                        or response["response"] is not None or response["claim_token"] != ticket.claim_id):
                    _deny()
                return True
            self._check_ticket(operation, ticket, metadata, epoch, UUID(metadata["period_id"]), now, live=False)
            if (not response or response["status"] != "reserved" or response["response"] is not None
                    or response["claim_token"] != ticket.claim_id):
                _deny()
            # A03 is independent of the commercial counter. We may release a
            # failed final answer only when every associated provider attempt
            # has terminal, persisted accounting. An unknown outcome keeps the
            # reservation pending for reconciliation.
            uncertain_attempt = await conn.fetchval(
                """SELECT EXISTS (
                       SELECT 1 FROM usage_events
                        WHERE organization_id=$1
                          AND event_type='system.provider_attempt'
                          AND created_at >= $3
                          AND (
                            metadata->'context'->>'quota_operation_id'=$2
                            OR (metadata->'context'->>'quota_operation_id' IS NULL
                                AND (
                                  metadata->'context'->>'message_id'=$4
                                  OR metadata->'context'->>'simulation_request_id'=$2
                                ))
                          )
                          AND COALESCE(metadata->>'accounting_status','')
                              NOT IN ('recorded','resolved')
                   )""",
                operation.organization_id, str(operation.id), response["reserved_at"],
                str(operation.source_id),
            )
            if uncertain_attempt:
                _deny()
            # The state transition fences this claim. A later retry receives a
            # new nonce; repeated release of this same claim is idempotent.
            metadata.update(state="released")
            await self._save(conn, operation, metadata)
            await conn.execute(
                """UPDATE simulation_requests SET status='failed',completed_at=$4,claim_token=$5
                    WHERE organization_id=$1 AND auth_user_id=$2 AND request_id=$3""",
                operation.organization_id, operation.actor_id, operation.id, now, UUID(metadata["claim_id"]),
            )
            return True
