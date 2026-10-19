"""Tenant-bound authorization and durable accounting for provider dispatches.

The accounting context is an internal capability. It is not a quota reservation
and does not charge a final answer; those remain separate concerns. Every
network dispatch first creates a tenant-scoped unresolved ledger row. A crash
or unknown provider usage therefore prevents later dispatches until reconciled.
"""

import asyncio
import json
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

from src.core.billing.suspension import AIGenerationBlocked, assert_ai_generation_allowed
from src.core.ai_providers import PROVIDERS

_ISSUER = object()
_ATTEMPT = "system.provider_attempt"
_TASKS = frozenset({
    "customer_message", "simulator", "onboarding_preview", "document_qa",
    "review", "report", "intent_classification",
})
_FAILURE_OUTCOMES = frozenset({
    "transport_error", "http_error", "invalid_response", "invalid_usage",
    "cancelled", "deadline_exceeded", "provider_error",
})
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")
_ADMISSION_WAIT_SECONDS = 35
_ADMISSION_RETRY_SECONDS = 0.05
_STALE_IN_FLIGHT_SECONDS = 300


class ProviderAccountingError(AIGenerationBlocked):
    """Fixed error text; provider bodies, credentials and user content are omitted."""


def _deny():
    raise ProviderAccountingError("AI generation is not authorized or accounting is unavailable") from None


def _uuid(value):
    return type(value) is UUID and value.int != 0


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _object(value):
    return json.loads(value) if isinstance(value, str) else dict(value)


def provider_model_matches(requested, actual):
    """Accept only the server-authorized requested model (or its free alias)."""
    if type(requested) is not str or type(actual) is not str:
        return False
    expected = requested
    for prefix in ("openrouter/", "groq/", "cerebras/", "mistral/", "openai/"):
        if expected.startswith(prefix):
            expected = expected.removeprefix(prefix)
            break
    return actual == expected or (
        requested.startswith("openrouter/")
        and expected.endswith(":free")
        and actual == expected.removesuffix(":free")
    )


@dataclass(frozen=True, slots=True, repr=False)
class ProviderAttempt:
    execution_id: UUID
    attempt_id: UUID
    provider: str
    model: str
    _issuer: object

    def __repr__(self):
        return "ProviderAttempt(<internal>)"

    def __reduce_ex__(self, protocol):
        raise TypeError("Provider attempts cannot be serialized")


@dataclass(frozen=True, slots=True)
class RawProviderUsage:
    actual_model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

    def __post_init__(self):
        values = (self.prompt_tokens, self.completion_tokens, self.total_tokens)
        if (type(self.actual_model) is not str or not self.actual_model or len(self.actual_model) > 256
                or any(type(value) is not int or not 0 <= value <= 10_000_000 for value in values)
                or self.total_tokens != self.prompt_tokens + self.completion_tokens):
            _deny()


class ProviderAccountingContext:
    """One invocation's tenant and actor scope; no global usage collectors."""

    def __init__(self, pool, organization_id, actor_auth_user_id, worker_message_id,
                 simulation_request_id, simulation_claim_token, task_type, context,
                 billing_snapshot, issuer):
        if issuer is not _ISSUER:
            _deny()
        self.pool = pool
        self.organization_id = organization_id
        self.actor_auth_user_id = actor_auth_user_id
        self.worker_message_id = worker_message_id
        self._simulation_request_id = simulation_request_id
        self._simulation_claim_token = simulation_claim_token
        self.task_type = task_type
        self.context = dict(context)
        self.billing_snapshot = dict(billing_snapshot)
        self.execution_id = uuid4()
        self._loop = asyncio.get_running_loop()
        self._closed = False
        self._state_lock = threading.Lock()

    def __repr__(self):
        return "ProviderAccountingContext(<internal>)"

    def __deepcopy__(self, memo):
        return self

    def assert_task(self, task_types):
        if self.task_type not in set(task_types) or self._closed:
            _deny()

    def assert_scope(self, organization_id, task_types):
        """Bind an internal generation helper to this tenant and purpose."""
        try:
            org_id = UUID(str(organization_id))
        except (TypeError, ValueError, AttributeError):
            _deny()
        self.assert_task(task_types)
        if org_id != self.organization_id:
            _deny()

    def __reduce_ex__(self, protocol):
        raise TypeError("Provider accounting contexts cannot be serialized")

    @classmethod
    async def open(cls, pool, organization_id, *, task_type, actor_auth_user_id=None,
                   worker_message_id=None, simulation_request_id=None,
                   simulation_claim_token=None, context=None):
        """Verify scope and current entitlement before constructing an LLM client.

        User-driven calls must provide the authenticated auth.users identity.
        The only worker path accepted here is an inbound message already claimed
        and billed by the legacy inbound processor. A client-supplied org UUID
        alone can never create this capability.
        """
        try:
            org_id = UUID(str(organization_id))
            actor_id = UUID(str(actor_auth_user_id)) if actor_auth_user_id is not None else None
            message_id = UUID(str(worker_message_id)) if worker_message_id is not None else None
            simulation_id = (UUID(str(simulation_request_id))
                             if simulation_request_id is not None else None)
            simulation_claim = (UUID(str(simulation_claim_token))
                                if simulation_claim_token is not None else None)
        except (TypeError, ValueError, AttributeError):
            _deny()
        if (not _uuid(org_id) or task_type not in _TASKS
                or (actor_id is None) == (message_id is None)):
            _deny()
        simulation_claimed = simulation_id is not None and simulation_claim is not None
        if ((simulation_id is None) != (simulation_claim is None)
                or (simulation_claimed and (actor_id is None or message_id is not None))
                or (task_type == "simulator" and not (simulation_claimed or message_id is not None))
                or (task_type != "simulator" and simulation_claimed)):
            _deny()
        if message_id is not None and task_type not in {"customer_message", "simulator"}:
            _deny()
        context = dict(context or {})
        if (set(context) - {"message_id", "conversation_id", "trace_id", "simulation_request_id"}
                or any(value is not None and (type(value) is not str or not _IDENTIFIER.fullmatch(value))
                       for value in context.values())):
            _deny()
        if message_id is not None and context.get("message_id") not in {None, str(message_id)}:
            _deny()
        if simulation_claimed:
            if context.get("simulation_request_id") not in {None, str(simulation_id)}:
                _deny()
            context["simulation_request_id"] = str(simulation_id)
        elif context.get("simulation_request_id") is not None:
            _deny()
        if message_id is not None:
            context["message_id"] = str(message_id)
        if not context.get("trace_id"):
            context["trace_id"] = str(uuid4())

        try:
            async with pool.acquire() as conn, conn.transaction():
                inbound_message = None
                simulation_reservation = None
                if actor_id is not None:
                    await cls._verify_actor(conn, org_id, actor_id)
                    if simulation_claimed:
                        simulation_reservation = await cls._verify_simulation_reservation(
                            conn, org_id, actor_id, simulation_id, simulation_claim,
                        )
                else:
                    inbound_message = await cls._verify_inbound_message(conn, org_id, message_id)
                billing_row = await conn.fetchrow(
                    """SELECT subscription_status, subscription_id, trial_start, trial_end,
                              current_period_start, current_period_end, messages_limit,
                              messages_used_this_period, ai_accounting_blocked
                         FROM organizations WHERE id=$1 FOR UPDATE""",
                    org_id,
                )
                if billing_row is None:
                    _deny()
                billing = cls._authorize_billing(
                    dict(billing_row), inbound_message, simulation_reservation,
                )
                state = await cls._unresolved_state(conn, org_id)
                if state == "blocked":
                    _deny()
        except ProviderAccountingError:
            raise
        except Exception:
            _deny()
        return cls(pool, org_id, actor_id, message_id, simulation_id, simulation_claim,
                   task_type, context, billing, _ISSUER)

    @staticmethod
    async def _verify_actor(conn, organization_id, auth_user_id):
        access = await conn.fetchrow(
            "SELECT disabled_at FROM auth_access_lifecycle WHERE auth_user_id=$1 FOR SHARE",
            auth_user_id,
        )
        if access is None or access["disabled_at"] is not None:
            _deny()
        membership = await conn.fetchrow(
            """SELECT om.ruolo FROM organization_memberships om
                 JOIN user_profiles up ON up.id=om.user_id
                WHERE om.organization_id=$1 AND up.auth_user_id=$2
                  AND om.ruolo IN ('owner','manager','staff')
                  AND om.joined_at IS NOT NULL
                FOR SHARE OF om, up""",
            organization_id, auth_user_id,
        )
        if membership is None:
            _deny()

    @staticmethod
    async def _verify_inbound_message(conn, organization_id, message_id):
        row = await conn.fetchrow(
            """SELECT direction, status, deleted_at, billed_at, sent_at, replied_at,
                      quota_exceeded_at, processing_at
                 FROM messages WHERE id=$1 AND organization_id=$2 FOR SHARE""",
            message_id, organization_id,
        )
        if (row is None or row["direction"] != "inbound" or row["status"] != "processing"
                or row["deleted_at"] is not None or row["billed_at"] is None
                or row["sent_at"] is not None or row["replied_at"] is not None
                or row["quota_exceeded_at"] is not None or row["processing_at"] is None):
            _deny()
        return dict(row)

    @staticmethod
    async def _verify_simulation_reservation(
        conn, organization_id, auth_user_id, request_id, claim_token,
    ):
        row = await conn.fetchrow(
            """SELECT reserved_at FROM simulation_requests
                WHERE organization_id=$1 AND auth_user_id=$2 AND request_id=$3
                  AND claim_token=$4 AND status='reserved'
                FOR SHARE""",
            organization_id, auth_user_id, request_id, claim_token,
        )
        if row is None or not isinstance(row["reserved_at"], datetime):
            _deny()
        return dict(row)

    @staticmethod
    def _authorize_billing(billing, message, simulation_reservation=None):
        # Inbound processing and the authenticated simulator reserve one quota
        # unit before entering A03. Subtract exactly that verified charge only
        # from this local snapshot; this neither refunds nor activates A04.
        if message is not None and simulation_reservation is not None:
            _deny()
        status = billing.get("subscription_status")
        now = datetime.now(timezone.utc)

        def in_window(start, end):
            if not isinstance(start, datetime) or not isinstance(end, datetime):
                return False
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            return start <= now < end

        if status == "active":
            subscription_id = billing.get("subscription_id")
            if (type(subscription_id) is not str or not subscription_id.strip()
                    or len(subscription_id) > 255
                    or not in_window(billing.get("current_period_start"),
                                     billing.get("current_period_end"))):
                _deny()
        elif status == "trialing":
            if not in_window(billing.get("trial_start"), billing.get("trial_end")):
                _deny()
        else:
            _deny()

        if message is not None or simulation_reservation is not None:
            status = billing.get("subscription_status")
            anchor_key, end_key = (("trial_start", "trial_end") if status == "trialing"
                                   else ("current_period_start", "current_period_end"))
            anchor, end = billing.get(anchor_key), billing.get(end_key)
            billed_at = (message["billed_at"] if message is not None
                         else simulation_reservation["reserved_at"])
            if (not isinstance(anchor, datetime) or not isinstance(end, datetime)
                    or not isinstance(billed_at, datetime) or not anchor <= billed_at < end):
                _deny()
            used = billing.get("messages_used_this_period")
            if type(used) is not int or used < 1:
                _deny()
            billing = dict(billing)
            billing["messages_used_this_period"] = used - 1
        try:
            assert_ai_generation_allowed(billing)
        except AIGenerationBlocked:
            _deny()
        return billing

    @staticmethod
    async def _unresolved_state(conn, organization_id):
        """Return none, waitable, or blocked without holding locks across waits.

        A single recent in-flight provider attempt is the only unresolved state
        that a new request may wait behind. Outbox work, multiple in-flight
        rows, failed attempts, and stale rows remain fail-closed.
        """
        if await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM governance_outbox "
            "WHERE organization_id=$1 AND event_kind='usage')",
            organization_id,
        ):
            return "blocked"
        rows = await conn.fetch(
            """SELECT metadata->>'outcome' AS outcome,
                      created_at >= now() - make_interval(secs => $2) AS recent
                 FROM usage_events
                WHERE organization_id=$1
                  AND metadata->>'accounting_status'='unresolved'
                ORDER BY created_at,id
                LIMIT 2""",
            organization_id, _STALE_IN_FLIGHT_SECONDS,
        )
        if not rows:
            return "none"
        if len(rows) == 1 and rows[0]["outcome"] == "in_flight" and rows[0]["recent"]:
            return "waitable"
        return "blocked"

    async def _recheck_scope_and_billing(self, conn):
        inbound_message = None
        simulation_reservation = None
        if self.actor_auth_user_id is not None:
            await self._verify_actor(conn, self.organization_id, self.actor_auth_user_id)
            if self._simulation_request_id is not None:
                simulation_reservation = await self._verify_simulation_reservation(
                    conn, self.organization_id, self.actor_auth_user_id,
                    self._simulation_request_id, self._simulation_claim_token,
                )
        else:
            inbound_message = await self._verify_inbound_message(
                conn, self.organization_id, self.worker_message_id,
            )
        row = await conn.fetchrow(
            """SELECT subscription_status, subscription_id, trial_start, trial_end,
                      current_period_start, current_period_end, messages_limit,
                      messages_used_this_period, ai_accounting_blocked
                 FROM organizations WHERE id=$1 FOR UPDATE""",
            self.organization_id,
        )
        if row is None:
            _deny()
        return self._authorize_billing(dict(row), inbound_message, simulation_reservation)

    async def begin(self, provider: str, model: str, reason: str) -> ProviderAttempt:
        if (provider not in PROVIDERS or type(model) is not str or not model or len(model) > 256
                or type(reason) is not str or not reason or len(reason) > 128):
            _deny()
        self.assert_http_admission()
        from src.core.rate_limit import get_rate_limiter

        limiter = await get_rate_limiter()
        if await limiter.hit(f"provider-http:{self.organization_id}", 30, 60) is not False:
            _deny()
        attempt_id = uuid4()
        supports_user_correlation = provider in {"groq", "openrouter"}
        metadata = {
            "version": 1, "execution_id": str(self.execution_id), "attempt_id": str(attempt_id),
            "provider": provider, "requested_model": model, "actual_model": None,
            "correlation_contract": "openai_user" if supports_user_correlation else None,
            "client_correlation_id": str(attempt_id) if supports_user_correlation else None,
            "provider_request_id": None,
            "reason": reason, "task_type": self.task_type, "context": self.context,
            "outcome": "in_flight", "accounting_status": "unresolved",
            "usage_present": None, "prompt_tokens": None, "completion_tokens": None,
            "total_tokens": None, "latency_ms": None, "estimated_cost_eur": None,
            "application_outcome": None,
            "failure_code": None,
        }
        deadline = self._loop.time() + _ADMISSION_WAIT_SECONDS
        while True:
            self.assert_http_admission()
            wait_for_in_flight = False
            try:
                # Organization row locking serializes admissions across workers.
                # The transaction is deliberately released before every sleep
                # and before provider HTTP is dispatched.
                async with self.pool.acquire() as conn, conn.transaction():
                    await self._recheck_scope_and_billing(conn)
                    state = await self._unresolved_state(conn, self.organization_id)
                    if state == "blocked":
                        _deny()
                    if state == "waitable":
                        wait_for_in_flight = True
                    else:
                        await conn.execute(
                            """INSERT INTO usage_events(id,organization_id,event_type,quantity,metadata)
                                 VALUES($1,$2,$3,1,$4::jsonb)""",
                            attempt_id, self.organization_id, _ATTEMPT, _json(metadata),
                        )
            except ProviderAccountingError:
                raise
            except Exception:
                _deny()
            if not wait_for_in_flight:
                return ProviderAttempt(self.execution_id, attempt_id, provider, model, _ISSUER)
            if self._loop.time() >= deadline:
                _deny()
            await asyncio.sleep(min(_ADMISSION_RETRY_SECONDS, max(0, deadline - self._loop.time())))

    async def finish(self, attempt: ProviderAttempt, usage: RawProviderUsage, latency_ms: int,
                     *, output_valid: bool = True, provider_request_id: str | None = None):
        from src.core.llm_routing import stima_costo_eur
        from src.core.provider_evidence import safe_provider_request_id

        if (type(attempt) is not ProviderAttempt or attempt._issuer is not _ISSUER
                or attempt.execution_id != self.execution_id or attempt.provider not in PROVIDERS
                or type(usage) is not RawProviderUsage
                or not provider_model_matches(attempt.model, usage.actual_model)
                or type(latency_ms) is not int or latency_ms < 0
                or type(output_valid) is not bool
                or (provider_request_id is not None
                    and not safe_provider_request_id(provider_request_id))):
            _deny()
        cost = stima_costo_eur(attempt.model, usage.prompt_tokens, usage.completion_tokens)
        expected = {
            "outcome": "response_received" if output_valid else "invalid_response",
            "accounting_status": "recorded" if cost is not None else "unresolved",
            "actual_model": usage.actual_model, "usage_present": True,
            "provider_request_id": provider_request_id,
            "prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens, "latency_ms": latency_ms,
            "estimated_cost_eur": cost,
            "application_outcome": "accepted" if output_valid else "invalid_content",
            "failure_code": ("cost_unavailable" if cost is None else
                             (None if output_valid else "invalid_content")),
        }
        async with self.pool.acquire() as conn, conn.transaction():
            # A provider has already been dispatched under an authorized
            # admission. Persist its actual result even if entitlement or the
            # initiating membership changed while the request was in flight.
            org = await conn.fetchrow("SELECT id FROM organizations WHERE id=$1 FOR UPDATE", self.organization_id)
            if org is None:
                _deny()
            row = await conn.fetchrow(
                """SELECT event_type,quantity,metadata FROM usage_events
                    WHERE organization_id=$1 AND id=$2 FOR UPDATE""",
                self.organization_id, attempt.attempt_id,
            )
            if row is None or row["event_type"] != _ATTEMPT or row["quantity"] != 1:
                _deny()
            metadata = _object(row["metadata"])
            if (metadata.get("execution_id") != str(self.execution_id)
                    or metadata.get("attempt_id") != str(attempt.attempt_id)
                    or metadata.get("provider") != attempt.provider
                    or metadata.get("requested_model") != attempt.model
                    or metadata.get("task_type") != self.task_type
                    or metadata.get("context") != self.context):
                _deny()
            if metadata.get("accounting_status") == "recorded":
                if any(metadata.get(key) != value for key, value in expected.items()):
                    _deny()
                return
            if metadata.get("accounting_status") != "unresolved" or metadata.get("outcome") != "in_flight":
                _deny()
            metadata.update(expected)
            await conn.execute(
                "UPDATE usage_events SET metadata=$3::jsonb WHERE organization_id=$1 AND id=$2",
                self.organization_id, attempt.attempt_id, _json(metadata),
            )
            if cost is None:
                await conn.execute(
                    "UPDATE organizations SET ai_accounting_blocked=TRUE WHERE id=$1",
                    self.organization_id,
                )

    async def fail(self, attempt: ProviderAttempt, outcome: str, latency_ms: int,
                   actual_model: str | None = None, usage_present: bool | None = None,
                   *, provider_request_id: str | None = None):
        from src.core.provider_evidence import safe_provider_request_id

        if (type(attempt) is not ProviderAttempt or attempt._issuer is not _ISSUER
                or attempt.execution_id != self.execution_id or outcome not in _FAILURE_OUTCOMES
                or type(latency_ms) is not int or latency_ms < 0
                or (actual_model is not None
                    and not provider_model_matches(attempt.model, actual_model))
                or (usage_present is not None and type(usage_present) is not bool)
                or (provider_request_id is not None
                    and not safe_provider_request_id(provider_request_id))):
            _deny()
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.fetchrow("SELECT id FROM organizations WHERE id=$1 FOR UPDATE", self.organization_id)
            row = await conn.fetchrow(
                """SELECT event_type,quantity,metadata FROM usage_events
                    WHERE organization_id=$1 AND id=$2 FOR UPDATE""",
                self.organization_id, attempt.attempt_id,
            )
            if row is None or row["event_type"] != _ATTEMPT or row["quantity"] != 1:
                _deny()
            metadata = _object(row["metadata"])
            if (metadata.get("execution_id") != str(self.execution_id)
                    or metadata.get("provider") != attempt.provider
                    or metadata.get("requested_model") != attempt.model
                    or metadata.get("accounting_status") not in {"unresolved", "recorded"}):
                _deny()
            # A connection can report an error after the commit reached PostgreSQL.
            # In that case a repeated failure finalizer must preserve the success.
            if metadata.get("accounting_status") == "recorded":
                return
            expected = {
                "outcome": outcome, "latency_ms": latency_ms, "failure_code": outcome,
                "actual_model": actual_model, "usage_present": usage_present,
                "provider_request_id": provider_request_id,
            }
            if metadata.get("outcome") == "in_flight":
                metadata.update(expected)
                await conn.execute(
                    "UPDATE usage_events SET metadata=$3::jsonb WHERE organization_id=$1 AND id=$2",
                    self.organization_id, attempt.attempt_id, _json(metadata),
                )
            elif any(metadata.get(key) != value for key, value in expected.items()):
                _deny()
            await conn.execute(
                "UPDATE organizations SET ai_accounting_blocked=TRUE WHERE id=$1",
                self.organization_id,
            )

    async def close(self):
        with self._state_lock:
            self._closed = True

    def assert_http_admission(self):
        with self._state_lock:
            if self._closed:
                _deny()

    def bridge(self, callback, *args, **kwargs):
        """Run async DB accounting on its owning loop from CrewAI's sync worker."""
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if current is self._loop or not self._loop.is_running():
            _deny()
        future = asyncio.run_coroutine_threadsafe(callback(*args, **kwargs), self._loop)
        try:
            return future.result(timeout=32)
        except Exception:
            _deny()
