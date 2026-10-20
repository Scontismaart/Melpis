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
_PROVIDER_LEASE_SECONDS = 90


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
    worker_id: UUID
    lease_epoch: int
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
                 billing_snapshot, quota_operation, quota_ticket, issuer):
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
        self._quota_operation = quota_operation
        self._quota_ticket = quota_ticket
        self.execution_id = uuid4()
        self._loop = asyncio.get_running_loop()
        self._closed = False
        self._quota_lease_failed = False
        self._state_lock = threading.Lock()
        self._quota_lease_task = (
            self._loop.create_task(self._renew_quota_lease())
            if quota_operation is not None else None
        )

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
                   simulation_claim_token=None, context=None,
                   quota_operation=None, quota_ticket=None):
        """Verify scope and current entitlement before constructing an LLM client.

        A04 paths must present an unforgeable reservation ticket. Preview and
        non-answer AI tasks retain their separate A03 authorization contract.
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
        quota_bound = quota_operation is not None or quota_ticket is not None
        if ((quota_operation is None) != (quota_ticket is None)
                or not _uuid(org_id) or task_type not in _TASKS
                or (actor_id is None) == (message_id is None)):
            _deny()
        if task_type in {"customer_message", "simulator", "document_qa"} and not quota_bound:
            _deny()
        if quota_bound:
            from src.core.billing.quota_reservations import QuotaOperation, QuotaTicket
            if (type(quota_operation) is not QuotaOperation or type(quota_ticket) is not QuotaTicket
                    or quota_operation.organization_id != org_id):
                _deny()
            expected_channel = {
                "customer_message": "inbound", "simulator": "simulator", "document_qa": "qa",
            }.get(task_type)
            if expected_channel is None or quota_operation.channel != expected_channel:
                _deny()
            if quota_operation.channel == "inbound":
                if (actor_id is not None or message_id != quota_operation.source_id
                        or simulation_request_id is not None or simulation_claim_token is not None):
                    _deny()
            else:
                if actor_id != quota_operation.auth_user_id or message_id is not None:
                    _deny()
                if task_type == "simulator":
                    if (simulation_id != quota_operation.id
                            or simulation_claim != quota_ticket.claim_id):
                        _deny()
                elif simulation_id is not None or simulation_claim is not None:
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
        if (set(context) - {"message_id", "conversation_id", "trace_id", "simulation_request_id",
                            "quota_operation_id", "quota_claim_id", "quota_period_id"}
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
        if quota_bound:
            expected_scope = {
                "quota_operation_id": str(quota_operation.id),
                "quota_claim_id": str(quota_ticket.claim_id),
                "quota_period_id": str(quota_ticket.period_id),
            }
            if any(context.get(key) not in {None, value} for key, value in expected_scope.items()):
                _deny()
            context.update(expected_scope)
        if not context.get("trace_id"):
            context["trace_id"] = str(uuid4())

        try:
            async with pool.acquire() as conn, conn.transaction():
                if quota_bound:
                    from src.core.billing.quota_reservations import QuotaReservations
                    billing = await QuotaReservations(pool).verify_ticket(
                        conn, quota_operation, quota_ticket,
                    )
                else:
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
                   task_type, context, billing, quota_operation, quota_ticket, _ISSUER)

    async def _renew_quota_lease(self):
        """Keep a live A04 claim fenced while a long provider call is running."""
        from src.core.billing.quota_reservations import QuotaReservations

        try:
            while True:
                await asyncio.sleep(8)
                if self._closed:
                    return
                self._quota_ticket = await QuotaReservations(self.pool).renew(
                    self._quota_operation, self._quota_ticket,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            self._quota_lease_failed = True

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
        if self._quota_operation is not None:
            from src.core.billing.quota_reservations import QuotaReservations
            return await QuotaReservations(self.pool).verify_ticket(
                conn, self._quota_operation, self._quota_ticket,
            )
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
        worker_id = uuid4()
        supports_user_correlation = provider in {"groq", "openrouter"}
        metadata = {
            "version": 1, "execution_id": str(self.execution_id), "attempt_id": str(attempt_id),
            "provider": provider, "requested_model": model, "actual_model": None,
            "correlation_contract": "openai_user" if supports_user_correlation else None,
            "client_correlation_id": str(attempt_id) if supports_user_correlation else None,
            "provider_request_id": None,
            "reason": reason, "task_type": self.task_type, "context": self.context,
            "outcome": "in_flight", "accounting_status": "unresolved",
            "dispatch_state": "prepared",
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
                        await conn.execute(
                            """INSERT INTO provider_attempt_runtime
                               (organization_id,attempt_id,worker_id,lease_epoch,lease_expires_at,dispatch_state)
                               VALUES($1,$2,$3,1,now()+make_interval(secs=>$4),'prepared')""",
                            self.organization_id, attempt_id, worker_id, _PROVIDER_LEASE_SECONDS,
                        )
                        await self._append_evidence(
                            conn, attempt_id, worker_id, 1, "prepared", {},
                        )
            except ProviderAccountingError:
                raise
            except Exception:
                _deny()
            if not wait_for_in_flight:
                return ProviderAttempt(
                    self.execution_id, attempt_id, provider, model, worker_id, 1, _ISSUER,
                )
            if self._loop.time() >= deadline:
                _deny()
            await asyncio.sleep(min(_ADMISSION_RETRY_SECONDS, max(0, deadline - self._loop.time())))

    async def _append_evidence(self, conn, attempt_id, worker_id, lease_epoch, event_type, details):
        if type(details) is not dict:
            _deny()
        await conn.execute(
            """INSERT INTO provider_attempt_evidence
               (id,organization_id,attempt_id,worker_id,lease_epoch,event_type,details)
               VALUES($1,$2,$3,$4,$5,$6,$7::jsonb)""",
            uuid4(), self.organization_id, attempt_id, worker_id, lease_epoch,
            event_type, _json(details),
        )

    def _validate_attempt(self, attempt):
        if (type(attempt) is not ProviderAttempt or attempt._issuer is not _ISSUER
                or attempt.execution_id != self.execution_id
                or not _uuid(attempt.worker_id) or type(attempt.lease_epoch) is not int
                or attempt.lease_epoch < 1 or attempt.provider not in PROVIDERS):
            _deny()

    async def mark_dispatching(self, attempt: ProviderAttempt) -> None:
        """Atomically commit one-use HTTP dispatch authorization before transport."""
        self._validate_attempt(attempt)
        async with self.pool.acquire() as conn, conn.transaction():
            await self._recheck_scope_and_billing(conn)
            if await conn.fetchval(
                "SELECT id FROM organizations WHERE id=$1 FOR UPDATE", self.organization_id,
            ) is None:
                _deny()
            row = await conn.fetchrow(
                """UPDATE provider_attempt_runtime
                      SET dispatch_state='dispatching',updated_at=now()
                    WHERE organization_id=$1 AND attempt_id=$2 AND worker_id=$3
                      AND lease_epoch=$4 AND dispatch_state='prepared'
                      AND lease_expires_at > now()
                    RETURNING attempt_id""",
                self.organization_id, attempt.attempt_id, attempt.worker_id, attempt.lease_epoch,
            )
            if row is None:
                _deny()
            await self._append_evidence(
                conn, attempt.attempt_id, attempt.worker_id, attempt.lease_epoch,
                "dispatch_authorized", {},
            )

    async def fail_before_dispatch(self, attempt: ProviderAttempt, failure_code: str,
                                   latency_ms: int) -> None:
        """Resolve only a PREPARED attempt; never infer NOT_SENT after dispatch authorization."""
        self._validate_attempt(attempt)
        if (failure_code not in {"local_setup_error", "request_serialization_error"}
                or type(latency_ms) is not int or latency_ms < 0):
            _deny()
        async with self.pool.acquire() as conn, conn.transaction():
            if await conn.fetchval(
                "SELECT id FROM organizations WHERE id=$1 FOR UPDATE", self.organization_id,
            ) is None:
                _deny()
            state = await conn.fetchrow(
                """UPDATE provider_attempt_runtime
                      SET dispatch_state='not_sent',worker_id=NULL,lease_expires_at=NULL,updated_at=now()
                    WHERE organization_id=$1 AND attempt_id=$2 AND worker_id=$3
                      AND lease_epoch=$4 AND dispatch_state='prepared'
                    RETURNING attempt_id""",
                self.organization_id, attempt.attempt_id, attempt.worker_id, attempt.lease_epoch,
            )
            if state is None:
                # A failed or ambiguous database write cannot prove the row stayed PREPARED.
                _deny()
            event = await conn.fetchval(
                """UPDATE usage_events SET metadata=jsonb_set(
                       jsonb_set(
                         jsonb_set(metadata,'{dispatch_state}','\"not_sent\"'::jsonb),
                         '{outcome}','\"provider_not_sent\"'::jsonb),
                       '{accounting_status}','\"resolved\"'::jsonb)
                     WHERE organization_id=$1 AND id=$2
                       AND event_type=$3 AND metadata->>'accounting_status'='unresolved'
                       AND metadata->>'outcome'='in_flight'
                     RETURNING id""",
                self.organization_id, attempt.attempt_id, _ATTEMPT,
            )
            if event is None:
                _deny()
            await self._append_evidence(
                conn, attempt.attempt_id, attempt.worker_id, attempt.lease_epoch,
                "attempt_not_sent", {"failure_code": failure_code, "latency_ms": latency_ms},
            )
            await self._refresh_ai_accounting_block(conn)

    async def record_provider_ids(self, attempt: ProviderAttempt, *, generation_id=None,
                                  provider_request_id=None) -> None:
        """Persist trusted response-header IDs early, keeping their namespaces separate."""
        from src.core.provider_evidence import safe_provider_generation_id, safe_provider_request_id

        self._validate_attempt(attempt)
        if ((generation_id is not None and not safe_provider_generation_id(generation_id))
                or (provider_request_id is not None and not safe_provider_request_id(provider_request_id))
                or (generation_id is None and provider_request_id is None)):
            _deny()
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.fetchrow("SELECT id FROM organizations WHERE id=$1 FOR UPDATE", self.organization_id)
            row = await conn.fetchrow(
                """UPDATE provider_attempt_runtime
                      SET generation_id=COALESCE($5,generation_id),
                          provider_request_id=COALESCE($6,provider_request_id),updated_at=now()
                    WHERE organization_id=$1 AND attempt_id=$2 AND worker_id=$3
                      AND lease_epoch=$4 AND dispatch_state='dispatching'
                    RETURNING attempt_id""",
                self.organization_id, attempt.attempt_id, attempt.worker_id,
                attempt.lease_epoch, generation_id, provider_request_id,
            )
            if row is None:
                # The response may arrive after fencing. Keep only evidence linked
                # to a prior dispatch authorization; never mutate the live lease.
                authorized = await conn.fetchval(
                    """SELECT EXISTS(SELECT 1 FROM provider_attempt_evidence
                         WHERE organization_id=$1 AND attempt_id=$2 AND worker_id=$3
                           AND lease_epoch=$4 AND event_type='dispatch_authorized')""",
                    self.organization_id, attempt.attempt_id, attempt.worker_id, attempt.lease_epoch,
                )
                if not authorized:
                    _deny()
                await self._append_evidence(
                    conn, attempt.attempt_id, attempt.worker_id, attempt.lease_epoch,
                    "provider_ids_observed", {
                        "generation_id": generation_id,
                        "provider_request_id": provider_request_id,
                    },
                )
                return
            await conn.execute(
                """UPDATE usage_events SET metadata=metadata || $3::jsonb
                     WHERE organization_id=$1 AND id=$2 AND event_type=$4""",
                self.organization_id, attempt.attempt_id,
                _json({key: value for key, value in {
                    "provider_generation_id": generation_id,
                    "provider_request_id": provider_request_id,
                }.items() if value is not None}), _ATTEMPT,
            )
            await self._append_evidence(
                conn, attempt.attempt_id, attempt.worker_id, attempt.lease_epoch,
                "provider_ids_observed", {
                    "generation_id": generation_id,
                    "provider_request_id": provider_request_id,
                },
            )

    async def _refresh_ai_accounting_block(self, conn):
        await conn.execute(
            """UPDATE organizations SET ai_accounting_blocked=(
                 EXISTS(SELECT 1 FROM governance_outbox
                         WHERE organization_id=$1 AND event_kind='usage')
                 OR EXISTS(SELECT 1 FROM usage_events
                            WHERE organization_id=$1
                              AND metadata->>'accounting_status'='unresolved')
               ) WHERE id=$1""",
            self.organization_id,
        )

    async def finish(self, attempt: ProviderAttempt, usage: RawProviderUsage, latency_ms: int,
                     *, output_valid: bool = True, provider_request_id: str | None = None,
                     provider_generation_id: str | None = None):
        from src.core.llm_routing import stima_costo_eur
        from src.core.provider_evidence import safe_provider_generation_id, safe_provider_request_id

        self._validate_attempt(attempt)
        if (attempt.provider not in PROVIDERS
                or type(usage) is not RawProviderUsage
                or not provider_model_matches(attempt.model, usage.actual_model)
                or type(latency_ms) is not int or latency_ms < 0
                or type(output_valid) is not bool
                or (provider_request_id is not None
                    and not safe_provider_request_id(provider_request_id))
                or (provider_generation_id is not None
                    and not safe_provider_generation_id(provider_generation_id))):
            _deny()
        cost = stima_costo_eur(attempt.model, usage.prompt_tokens, usage.completion_tokens)
        expected = {
            "outcome": "response_received" if output_valid else "invalid_response",
            "accounting_status": "recorded" if cost is not None else "unresolved",
            "dispatch_state": "verified",
            "actual_model": usage.actual_model, "usage_present": True,
            "provider_request_id": provider_request_id,
            "provider_generation_id": provider_generation_id,
            "prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens, "latency_ms": latency_ms,
            "estimated_cost_eur": cost,
            "application_outcome": "accepted" if output_valid else "invalid_content",
            "failure_code": ("cost_unavailable" if cost is None else
                             (None if output_valid else "invalid_content")),
        }
        finalized = False
        async with self.pool.acquire() as conn, conn.transaction():
            # A provider has already been dispatched under an authorized
            # admission. Persist its actual result even if entitlement or the
            # initiating membership changed while the request was in flight.
            org = await conn.fetchrow("SELECT id FROM organizations WHERE id=$1 FOR UPDATE", self.organization_id)
            if org is None:
                _deny()
            lease = await conn.fetchrow(
                """SELECT worker_id,lease_epoch,lease_expires_at,dispatch_state,
                          generation_id,provider_request_id
                     FROM provider_attempt_runtime
                    WHERE organization_id=$1 AND attempt_id=$2 FOR UPDATE""",
                self.organization_id, attempt.attempt_id,
            )
            if lease is None:
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
            provider_request_id = provider_request_id or lease["provider_request_id"]
            provider_generation_id = provider_generation_id or lease["generation_id"]
            expected["provider_request_id"] = provider_request_id
            expected["provider_generation_id"] = provider_generation_id
            if metadata.get("accounting_status") == "recorded":
                if any(metadata.get(key) != value for key, value in expected.items()):
                    _deny()
                return True
            if metadata.get("accounting_status") != "unresolved":
                _deny()
            evidence = {
                "actual_model": usage.actual_model,
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
                "latency_ms": latency_ms,
                "estimated_cost_eur": cost,
                "output_valid": output_valid,
                "provider_generation_id": provider_generation_id,
                "provider_request_id": provider_request_id,
            }
            prior_dispatch = await conn.fetchval(
                """SELECT EXISTS(SELECT 1 FROM provider_attempt_evidence
                     WHERE organization_id=$1 AND attempt_id=$2 AND worker_id=$3
                       AND lease_epoch=$4 AND event_type='dispatch_authorized')""",
                self.organization_id, attempt.attempt_id, attempt.worker_id, attempt.lease_epoch,
            )
            if not prior_dispatch:
                _deny()
            current_lease = (
                lease["worker_id"] == attempt.worker_id
                and lease["lease_epoch"] == attempt.lease_epoch
                and lease["dispatch_state"] == "dispatching"
                and lease["lease_expires_at"] is not None
            )
            lease_active = current_lease and await conn.fetchval(
                "SELECT $1::timestamptz > now()", lease["lease_expires_at"],
            )
            if not lease_active:
                existing = await conn.fetchrow(
                    """INSERT INTO provider_attempt_evidence
                       (id,organization_id,attempt_id,worker_id,lease_epoch,event_type,details)
                       VALUES($1,$2,$3,$4,$5,'provider_response_verified',$6::jsonb)
                       ON CONFLICT (organization_id,attempt_id,lease_epoch)
                         WHERE event_type='provider_response_verified' DO NOTHING
                       RETURNING id""",
                    uuid4(), self.organization_id, attempt.attempt_id, attempt.worker_id,
                    attempt.lease_epoch, _json(evidence),
                )
                if existing is None:
                    stored = await conn.fetchval(
                        """SELECT details FROM provider_attempt_evidence
                             WHERE organization_id=$1 AND attempt_id=$2 AND lease_epoch=$3
                               AND event_type='provider_response_verified'""",
                        self.organization_id, attempt.attempt_id, attempt.lease_epoch,
                    )
                    if _object(stored) != evidence:
                        _deny()
                return False
            if metadata.get("outcome") != "in_flight":
                _deny()
            metadata.update(expected)
            state_updated = await conn.fetchval(
                """UPDATE provider_attempt_runtime SET dispatch_state='verified',updated_at=now()
                     WHERE organization_id=$1 AND attempt_id=$2 AND worker_id=$3
                       AND lease_epoch=$4 AND dispatch_state='dispatching'
                       AND lease_expires_at>now() RETURNING attempt_id""",
                self.organization_id, attempt.attempt_id, attempt.worker_id, attempt.lease_epoch,
            )
            if state_updated is None:
                _deny()
            await conn.execute(
                "UPDATE usage_events SET metadata=$3::jsonb WHERE organization_id=$1 AND id=$2",
                self.organization_id, attempt.attempt_id, _json(metadata),
            )
            await conn.execute(
                """UPDATE provider_attempt_runtime
                      SET generation_id=COALESCE($3,generation_id),
                          provider_request_id=COALESCE($4,provider_request_id),updated_at=now()
                    WHERE organization_id=$1 AND attempt_id=$2""",
                self.organization_id, attempt.attempt_id,
                provider_generation_id, provider_request_id,
            )
            await conn.execute(
                """INSERT INTO provider_attempt_evidence
                   (id,organization_id,attempt_id,worker_id,lease_epoch,event_type,details)
                   VALUES($1,$2,$3,$4,$5,'provider_response_verified',$6::jsonb)""",
                uuid4(), self.organization_id, attempt.attempt_id, attempt.worker_id,
                attempt.lease_epoch, _json(evidence),
            )
            if cost is None:
                await conn.execute(
                    "UPDATE organizations SET ai_accounting_blocked=TRUE WHERE id=$1",
                    self.organization_id,
                )
            finalized = True
        return finalized

    async def fail(self, attempt: ProviderAttempt, outcome: str, latency_ms: int,
                   actual_model: str | None = None, usage_present: bool | None = None,
                   *, provider_request_id: str | None = None,
                   provider_generation_id: str | None = None):
        from src.core.provider_evidence import safe_provider_generation_id, safe_provider_request_id

        self._validate_attempt(attempt)
        if (outcome not in _FAILURE_OUTCOMES
                or type(latency_ms) is not int or latency_ms < 0
                or (actual_model is not None
                    and not provider_model_matches(attempt.model, actual_model))
                or (usage_present is not None and type(usage_present) is not bool)
                or (provider_request_id is not None
                    and not safe_provider_request_id(provider_request_id))
                or (provider_generation_id is not None
                    and not safe_provider_generation_id(provider_generation_id))):
            _deny()
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.fetchrow("SELECT id FROM organizations WHERE id=$1 FOR UPDATE", self.organization_id)
            lease = await conn.fetchrow(
                """SELECT worker_id,lease_epoch,lease_expires_at,dispatch_state,
                          generation_id,provider_request_id
                     FROM provider_attempt_runtime
                    WHERE organization_id=$1 AND attempt_id=$2 FOR UPDATE""",
                self.organization_id, attempt.attempt_id,
            )
            row = await conn.fetchrow(
                """SELECT event_type,quantity,metadata FROM usage_events
                    WHERE organization_id=$1 AND id=$2 FOR UPDATE""",
                self.organization_id, attempt.attempt_id,
            )
            if (lease is None or row is None or row["event_type"] != _ATTEMPT
                    or row["quantity"] != 1):
                _deny()
            metadata = _object(row["metadata"])
            if (metadata.get("execution_id") != str(self.execution_id)
                    or metadata.get("provider") != attempt.provider
                    or metadata.get("requested_model") != attempt.model
                    or metadata.get("accounting_status") not in {"unresolved", "recorded"}):
                _deny()
            # A connection can report an error after the commit reached PostgreSQL.
            # In that case a repeated failure finalizer must preserve the success.
            if metadata.get("accounting_status") in {"recorded", "resolved"}:
                return
            provider_request_id = provider_request_id or lease["provider_request_id"]
            provider_generation_id = provider_generation_id or lease["generation_id"]
            expected = {
                "outcome": outcome, "latency_ms": latency_ms, "failure_code": outcome,
                "actual_model": actual_model, "usage_present": usage_present,
                "provider_request_id": provider_request_id,
                "provider_generation_id": provider_generation_id,
                "dispatch_state": "uncertain",
            }
            authorized = await conn.fetchval(
                """SELECT EXISTS(SELECT 1 FROM provider_attempt_evidence
                     WHERE organization_id=$1 AND attempt_id=$2 AND worker_id=$3
                       AND lease_epoch=$4 AND event_type='dispatch_authorized')""",
                self.organization_id, attempt.attempt_id, attempt.worker_id, attempt.lease_epoch,
            )
            if not authorized:
                _deny()
            lease_current = (
                lease["worker_id"] == attempt.worker_id
                and lease["lease_epoch"] == attempt.lease_epoch
                and lease["dispatch_state"] == "dispatching"
            )
            if metadata.get("outcome") == "in_flight" and lease_current:
                await conn.execute(
                    """UPDATE provider_attempt_runtime SET dispatch_state='uncertain',updated_at=now()
                         WHERE organization_id=$1 AND attempt_id=$2 AND worker_id=$3 AND lease_epoch=$4
                           AND dispatch_state='dispatching'""",
                    self.organization_id, attempt.attempt_id, attempt.worker_id, attempt.lease_epoch,
                )
                metadata.update(expected)
                await conn.execute(
                    "UPDATE usage_events SET metadata=$3::jsonb WHERE organization_id=$1 AND id=$2",
                    self.organization_id, attempt.attempt_id, _json(metadata),
                )
                await conn.execute(
                    """UPDATE provider_attempt_runtime
                          SET generation_id=COALESCE($3,generation_id),
                              provider_request_id=COALESCE($4,provider_request_id),updated_at=now()
                        WHERE organization_id=$1 AND attempt_id=$2""",
                    self.organization_id, attempt.attempt_id,
                    provider_generation_id, provider_request_id,
                )
            else:
                # A fenced worker may contribute a failure observation, but it
                # cannot mutate the authoritative attempt or clear its hold.
                await self._append_evidence(
                    conn, attempt.attempt_id, attempt.worker_id, attempt.lease_epoch,
                    "provider_failure_uncertain", expected,
                )
            await conn.execute(
                "UPDATE organizations SET ai_accounting_blocked=TRUE WHERE id=$1",
                self.organization_id,
            )
            if lease_current and metadata.get("outcome") != "in_flight":
                await self._append_evidence(
                    conn, attempt.attempt_id, attempt.worker_id, attempt.lease_epoch,
                    "provider_failure_uncertain", expected,
                )

    async def close(self):
        with self._state_lock:
            self._closed = True
        if self._quota_lease_task is not None:
            self._quota_lease_task.cancel()
            try:
                await self._quota_lease_task
            except asyncio.CancelledError:
                pass

    def assert_http_admission(self):
        with self._state_lock:
            if self._closed or self._quota_lease_failed:
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
