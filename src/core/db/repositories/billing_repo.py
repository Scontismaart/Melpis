from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
import asyncpg

from src.core.db.scoping import TenantScopedRepository, system_scope


def _assert_regular_usage_type(event_type) -> None:
    # Protocol records must only be written by the dedicated trusted path.
    # In particular the generic outbox drain must not turn an arbitrary queued
    # ID/payload into an authorization record in usage_events.
    if isinstance(event_type, str) and event_type in {
        "system.quota_trust_epoch", "legacy_quota_admission", "system.quota_reservation",
        "system.provider_attempt",
    }:
        raise ValueError("Reserved quota protocol event type")


class BillingRepository(TenantScopedRepository):
    """Repository specializzato per abbonamenti, limiti, eventi di utilizzo e integrazione Stripe."""

    def __init__(self, pool):
        self.pool = pool

    # ── Usage events ──────────────────────────────────────────

    async def record_usage(
        self, organization_id: uuid.UUID | str, event_type: str, quantity: int = 1, metadata: dict | None = None
    ) -> dict:
        _assert_regular_usage_type(event_type)
        if isinstance(organization_id, str):
            organization_id = uuid.UUID(organization_id)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO usage_events (id, organization_id, event_type,
                                          quantity, metadata)
                VALUES ($1, $2, $3, $4, $5::jsonb)
                RETURNING *
            """, uuid.uuid4(), organization_id, event_type, quantity,
            json.dumps(metadata or {}))
            return dict(row)

    async def record_usage_batch(
        self, organization_id: uuid.UUID | str, records: list[dict], *, block_ai: bool = False
    ) -> None:
        """Atomically persist provider usage attempts and optional unresolved hold."""
        if isinstance(organization_id, str):
            organization_id = uuid.UUID(organization_id)
        if not isinstance(records, list):
            raise ValueError("Usage records must be a list")
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                for record in records:
                    event_type = record.get("event_type", "ai_response")
                    quantity = record.get("quantity", 1)
                    metadata = record.get("metadata", {})
                    _assert_regular_usage_type(event_type)
                    if (not isinstance(event_type, str) or not event_type.strip()
                            or isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0
                            or not isinstance(metadata, dict)):
                        raise ValueError("Invalid usage record")
                    await conn.execute(
                        """INSERT INTO usage_events
                           (id, organization_id, event_type, quantity, metadata)
                           VALUES ($1, $2, $3, $4, $5::jsonb)""",
                        uuid.uuid4(), organization_id, event_type, quantity, json.dumps(metadata),
                    )
                if block_ai:
                    await conn.execute(
                        "UPDATE organizations SET ai_accounting_blocked = TRUE WHERE id = $1",
                        organization_id,
                    )

    @staticmethod
    async def _provider_reconciliation_member(conn, organization_id, actor_auth_user_id):
        member = await conn.fetchrow(
            """SELECT up.id AS profile_id, om.ruolo
                 FROM auth_access_lifecycle lifecycle
                 JOIN user_profiles up ON up.auth_user_id=lifecycle.auth_user_id
                 JOIN organization_memberships om ON om.user_id=up.id
                WHERE lifecycle.auth_user_id=$1 AND lifecycle.disabled_at IS NULL
                  AND om.organization_id=$2 AND om.joined_at IS NOT NULL
                  AND om.ruolo IN ('owner','manager')
                FOR SHARE OF lifecycle, up, om""",
            actor_auth_user_id, organization_id,
        )
        if member is None:
            raise PermissionError("Active owner or manager membership is required")
        return member

    @staticmethod
    def _provider_evidence_matches_attempt(metadata, evidence, attempt_id, created_at) -> bool:
        from src.core.provider_evidence import safe_provider_request_id

        if (evidence.provider not in {"groq", "openrouter"}
                or metadata.get("provider") != evidence.provider
                or metadata.get("correlation_contract") != "openai_user"
                or metadata.get("client_correlation_id") != str(attempt_id)
                or evidence.provider_correlation_id != metadata.get("client_correlation_id")
                or not safe_provider_request_id(metadata.get("provider_request_id"))
                or metadata.get("provider_request_id") != evidence.provider_request_id):
            return False
        if not isinstance(created_at, datetime) or created_at.tzinfo is None:
            return False
        # Provider event time must be close to the durable local attempt start.
        return abs((evidence.provider_event_at - created_at.astimezone(timezone.utc)).total_seconds()) <= 900

    async def propose_provider_reconciliation(
        self, organization_id: uuid.UUID | str, attempt_id: uuid.UUID | str, *,
        actor_auth_user_id: uuid.UUID | str, aal: str, evidence, attestations: dict,
    ) -> int | None:
        """Record a non-resolving proposal backed by verified immutable evidence."""
        from src.core.provider_evidence import is_verified_provider_evidence, validate_attestations

        try:
            organization_id, attempt_id, actor_auth_user_id = map(
                lambda value: uuid.UUID(str(value)),
                (organization_id, attempt_id, actor_auth_user_id),
            )
        except (TypeError, ValueError, AttributeError):
            raise ValueError("Invalid reconciliation scope") from None
        if aal != "aal2":
            raise PermissionError("A verified MFA session is required")
        validate_attestations(attestations)
        if (not is_verified_provider_evidence(evidence)
                or evidence.attempt_id != attempt_id):
            raise ValueError("Verified immutable provider evidence is required")

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                if await conn.fetchval(
                    "SELECT id FROM organizations WHERE id=$1 FOR UPDATE", organization_id,
                ) is None:
                    return None
                member = await self._provider_reconciliation_member(
                    conn, organization_id, actor_auth_user_id,
                )
                row = await conn.fetchrow(
                    """SELECT event_type,quantity,metadata,created_at FROM usage_events
                         WHERE organization_id=$1 AND id=$2 FOR UPDATE""",
                    organization_id, attempt_id,
                )
                if (row is None or row["event_type"] != "system.provider_attempt"
                        or row["quantity"] != 1):
                    return None
                metadata = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else dict(row["metadata"])
                if (metadata.get("accounting_status") != "unresolved"
                        or metadata.get("outcome") == "in_flight"):
                    # Age is not terminality. A worker or provider may still finish.
                    return None
                if not self._provider_evidence_matches_attempt(
                    metadata, evidence, attempt_id, row["created_at"],
                ):
                    raise ValueError("Provider evidence has no verified unique attempt correlation")
                current_version = metadata.get("reconciliation_version", 0)
                if type(current_version) is not int or current_version < 0:
                    return None
                if metadata.get("reconciliation") is not None:
                    return None
                proposal_version = current_version + 1
                metadata["reconciliation_version"] = proposal_version
                metadata["reconciliation"] = {
                    "state": "proposed",
                    "version": proposal_version,
                    "proposed_by": str(actor_auth_user_id),
                    "proposed_at": datetime.now(timezone.utc).isoformat(),
                    "evidence_object_id": evidence.object_id,
                    "evidence_object_version_id": evidence.object_version_id,
                    "evidence_sha256": evidence.sha256,
                    "attestations": dict(attestations),
                    "original_outcome": metadata.get("outcome"),
                }
                updated = await conn.fetchval(
                    """UPDATE usage_events SET metadata=$3::jsonb
                         WHERE organization_id=$1 AND id=$2
                           AND metadata->>'accounting_status'='unresolved'
                           AND metadata->>'outcome'=$4
                           AND COALESCE((metadata->>'reconciliation_version')::int,0)=$5
                         RETURNING id""",
                    organization_id, attempt_id,
                    json.dumps(metadata, sort_keys=True, separators=(",", ":"), allow_nan=False),
                    metadata["outcome"], current_version,
                )
                if updated is None:
                    return None
                await conn.execute(
                    """INSERT INTO audit_log
                           (id,organization_id,user_id,auth_user_id,action,target_table,target_id,details)
                         VALUES($1,$2,$3,$4,'provider_attempt.reconciliation_proposed',
                                'usage_events',$5,$6::jsonb)""",
                    uuid.uuid4(), organization_id, member["profile_id"],
                    str(actor_auth_user_id), attempt_id,
                    json.dumps({"proposal_version": proposal_version,
                                "evidence_object_id": evidence.object_id,
                                "evidence_object_version_id": evidence.object_version_id,
                                "evidence_sha256": evidence.sha256,
                                "provider": evidence.provider}, sort_keys=True),
                )
                return proposal_version

    async def approve_provider_reconciliation(
        self, organization_id: uuid.UUID | str, attempt_id: uuid.UUID | str, *,
        actor_auth_user_id: uuid.UUID | str, aal: str, proposal_version: int,
        evidence, attestations: dict,
    ) -> bool:
        """Apply a proposed resolution after an independent AAL2 approval."""
        from src.core.provider_evidence import is_verified_provider_evidence, validate_attestations

        try:
            organization_id, attempt_id, actor_auth_user_id = map(
                lambda value: uuid.UUID(str(value)),
                (organization_id, attempt_id, actor_auth_user_id),
            )
        except (TypeError, ValueError, AttributeError):
            raise ValueError("Invalid reconciliation scope") from None
        if aal != "aal2":
            raise PermissionError("A verified MFA session is required")
        if type(proposal_version) is not int or proposal_version < 1:
            raise ValueError("Invalid proposal version")
        validate_attestations(attestations)
        if (not is_verified_provider_evidence(evidence)
                or evidence.attempt_id != attempt_id):
            raise ValueError("Verified immutable provider evidence is required")

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                if await conn.fetchval(
                    "SELECT id FROM organizations WHERE id=$1 FOR UPDATE", organization_id,
                ) is None:
                    return False
                member = await self._provider_reconciliation_member(
                    conn, organization_id, actor_auth_user_id,
                )
                row = await conn.fetchrow(
                    """SELECT event_type,quantity,metadata,created_at FROM usage_events
                         WHERE organization_id=$1 AND id=$2 FOR UPDATE""",
                    organization_id, attempt_id,
                )
                if (row is None or row["event_type"] != "system.provider_attempt"
                        or row["quantity"] != 1):
                    return False
                metadata = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else dict(row["metadata"])
                if (metadata.get("accounting_status") != "unresolved"
                        or metadata.get("outcome") == "in_flight"):
                    # Dual approval cannot substitute for verifiable worker fencing.
                    return False
                proposal = metadata.get("reconciliation")
                if (not isinstance(proposal, dict) or proposal.get("state") != "proposed"
                        or proposal.get("version") != proposal_version
                        or metadata.get("reconciliation_version") != proposal_version
                        or proposal.get("proposed_by") == str(actor_auth_user_id)
                        or proposal.get("evidence_object_id") != evidence.object_id
                        or proposal.get("evidence_object_version_id") != evidence.object_version_id
                        or proposal.get("evidence_sha256") != evidence.sha256):
                    return False
                if not self._provider_evidence_matches_attempt(
                    metadata, evidence, attempt_id, row["created_at"],
                ):
                    return False

                resolution = evidence.outcome
                reconciled_cost = None
                if resolution == "provider_usage_confirmed":
                    from src.core.provider_accounting import RawProviderUsage, provider_model_matches
                    from src.core.llm_routing import stima_costo_eur

                    usage = RawProviderUsage(
                        evidence.actual_model, evidence.prompt_tokens,
                        evidence.completion_tokens, evidence.total_tokens,
                    )
                    requested_model = metadata.get("requested_model")
                    if not provider_model_matches(requested_model, usage.actual_model):
                        raise ValueError("Provider evidence does not match the authorized attempt")
                    observed = (metadata.get("actual_model"), metadata.get("prompt_tokens"),
                                metadata.get("completion_tokens"), metadata.get("total_tokens"))
                    if any(value is not None for value in observed) and observed != (
                        usage.actual_model, usage.prompt_tokens,
                        usage.completion_tokens, usage.total_tokens,
                    ):
                        raise ValueError("Provider evidence conflicts with recorded metrics")
                    reconciled_cost = stima_costo_eur(
                        requested_model, usage.prompt_tokens, usage.completion_tokens,
                    )
                    if reconciled_cost is None:
                        raise ValueError("A verified model price is required before recovery")
                elif (resolution == "provider_confirms_not_billed"
                        and metadata.get("usage_present") is True):
                    raise ValueError("A provider response with usage cannot be marked not billed")

                original_outcome = metadata.get("outcome")
                metadata["original_outcome"] = original_outcome
                metadata["resolution"] = resolution
                metadata["provider_request_id"] = evidence.provider_request_id
                metadata["reconciled_by"] = str(actor_auth_user_id)
                metadata["reconciled_at"] = datetime.now(timezone.utc).isoformat()
                proposal.update({
                    "state": "approved",
                    "approved_by": str(actor_auth_user_id),
                    "approved_at": metadata["reconciled_at"],
                    "approval_attestations": dict(attestations),
                })
                if resolution == "provider_usage_confirmed":
                    metadata.update({
                        "accounting_status": "recorded",
                        "outcome": "provider_usage_confirmed",
                        "actual_model": evidence.actual_model,
                        "usage_present": True,
                        "prompt_tokens": evidence.prompt_tokens,
                        "completion_tokens": evidence.completion_tokens,
                        "total_tokens": evidence.total_tokens,
                        "estimated_cost_eur": reconciled_cost,
                    })
                else:
                    # The immutable artifact must explicitly state a terminal no-bill record.
                    metadata["accounting_status"] = "resolved"
                    metadata["outcome"] = "provider_confirmed_not_billed"
                updated = await conn.fetchval(
                    """UPDATE usage_events SET metadata=$3::jsonb
                         WHERE organization_id=$1 AND id=$2
                           AND metadata->>'accounting_status'='unresolved'
                           AND metadata->>'outcome'=$4
                           AND COALESCE((metadata->>'reconciliation_version')::int,0)=$5
                         RETURNING id""",
                    organization_id, attempt_id,
                    json.dumps(metadata, sort_keys=True, separators=(",", ":"), allow_nan=False),
                    original_outcome, proposal_version,
                )
                if updated is None:
                    return False
                audit_details = {
                    "resolution": resolution,
                    "provider": evidence.provider,
                    "evidence_object_id": evidence.object_id,
                    "evidence_object_version_id": evidence.object_version_id,
                    "evidence_sha256": evidence.sha256,
                    "proposal_version": proposal_version,
                }
                if resolution == "provider_usage_confirmed":
                    audit_details.update({
                        "actual_model": evidence.actual_model,
                        "prompt_tokens": evidence.prompt_tokens,
                        "completion_tokens": evidence.completion_tokens,
                        "total_tokens": evidence.total_tokens,
                        "estimated_cost_eur": reconciled_cost,
                    })
                await conn.execute(
                    """INSERT INTO audit_log
                           (id,organization_id,user_id,auth_user_id,action,target_table,target_id,details)
                         VALUES($1,$2,$3,$4,'provider_attempt.reconciled',
                                'usage_events',$5,$6::jsonb)""",
                    uuid.uuid4(), organization_id, member["profile_id"],
                    str(actor_auth_user_id), attempt_id,
                    json.dumps(audit_details, sort_keys=True, separators=(",", ":"), allow_nan=False),
                )
                await conn.execute(
                    """UPDATE organizations SET ai_accounting_blocked = (
                         EXISTS (SELECT 1 FROM governance_outbox
                                 WHERE organization_id = $1 AND event_kind = 'usage')
                         OR EXISTS (SELECT 1 FROM usage_events
                                    WHERE organization_id = $1
                                      AND metadata->>'accounting_status' = 'unresolved')
                       ) WHERE id = $1""",
                    organization_id,
                )
                return True

    async def get_usage_by_month(
        self, organization_id: uuid.UUID | str, year: int, month: int
    ) -> list[dict]:
        if isinstance(organization_id, str):
            organization_id = uuid.UUID(organization_id)
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM usage_events
                WHERE organization_id = $1
                  AND billing_month = $2
                ORDER BY created_at
            """, organization_id, date(year, month, 1))
            return [dict(r) for r in rows]

    async def get_usage_summary(
        self, organization_id: uuid.UUID | str, year: int, month: int
    ) -> dict[str, int]:
        if isinstance(organization_id, str):
            organization_id = uuid.UUID(organization_id)
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT event_type, SUM(quantity)::int as total
                FROM usage_events
                WHERE organization_id = $1
                  AND billing_month = $2
                GROUP BY event_type
            """, organization_id, date(year, month, 1))
            return {r["event_type"]: r["total"] for r in rows}

    # ── Abbonamento & Limiti Organizzazione ──────────────────

    async def get_organization_billing(self, organization_id: uuid.UUID | str) -> dict:
        if isinstance(organization_id, str):
            organization_id = uuid.UUID(organization_id)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT stripe_customer_id, subscription_id, subscription_status,
                       plan, messages_used_this_period, messages_limit,
                       users_limit, whatsapp_numbers_limit,
                       current_period_start, current_period_end,
                       trial_start, trial_end, suspension_notified_at,
                       COALESCE((to_jsonb(o)->>'ai_accounting_blocked')::boolean, FALSE) AS ai_accounting_blocked
                FROM organizations o WHERE id = $1
            """, organization_id)
            if row is None:
                raise ValueError(f"Organization {organization_id} not found")
            return dict(row)

    async def get_org_subscription_state(self, org_id: uuid.UUID | str) -> dict | None:
        if isinstance(org_id, str):
            org_id = uuid.UUID(org_id)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT subscription_status, trial_end,
                       COALESCE((to_jsonb(o)->>'ai_accounting_blocked')::boolean, FALSE) AS ai_accounting_blocked,
                       messages_used_this_period, messages_limit
                FROM organizations o WHERE id = $1
            """, org_id)
            return dict(row) if row else None

    async def update_organization_billing(
        self, organization_id: uuid.UUID | str, data: dict
    ) -> dict:
        if isinstance(organization_id, str):
            organization_id = uuid.UUID(organization_id)
        allowed = {"stripe_customer_id", "subscription_id", "subscription_status", "plan",
                   "messages_used_this_period", "messages_limit", "users_limit", "whatsapp_numbers_limit",
                   "current_period_start", "current_period_end", "trial_start", "trial_end",
                   "suspension_notified_at", "ai_accounting_blocked"}
        if not data or not set(data) <= allowed:
            raise ValueError("Invalid billing fields")
        fields = (
            "stripe_customer_id", "subscription_id", "subscription_status", "plan",
            "messages_used_this_period", "messages_limit", "users_limit",
            "whatsapp_numbers_limit", "current_period_start", "current_period_end",
            "trial_start", "trial_end", "suspension_notified_at", "ai_accounting_blocked",
        )
        values = [organization_id]
        for field in fields:
            values.extend((field in data, data.get(field)))
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """UPDATE organizations SET
                    stripe_customer_id = CASE WHEN $2 THEN $3 ELSE stripe_customer_id END,
                    subscription_id = CASE WHEN $4 THEN $5 ELSE subscription_id END,
                    subscription_status = CASE WHEN $6 THEN $7 ELSE subscription_status END,
                    plan = CASE WHEN $8 THEN $9 ELSE plan END,
                    messages_used_this_period = CASE WHEN $10 THEN $11 ELSE messages_used_this_period END,
                    messages_limit = CASE WHEN $12 THEN $13 ELSE messages_limit END,
                    users_limit = CASE WHEN $14 THEN $15 ELSE users_limit END,
                    whatsapp_numbers_limit = CASE WHEN $16 THEN $17 ELSE whatsapp_numbers_limit END,
                    current_period_start = CASE WHEN $18 THEN $19 ELSE current_period_start END,
                    current_period_end = CASE WHEN $20 THEN $21 ELSE current_period_end END,
                    trial_start = CASE WHEN $22 THEN $23 ELSE trial_start END,
                    trial_end = CASE WHEN $24 THEN $25 ELSE trial_end END,
                    suspension_notified_at = CASE WHEN $26 THEN $27 ELSE suspension_notified_at END,
                    ai_accounting_blocked = CASE WHEN $28 THEN $29 ELSE ai_accounting_blocked END
                    WHERE id = $1
                    RETURNING stripe_customer_id, subscription_id, subscription_status,
                              plan, messages_used_this_period, messages_limit,
                              users_limit, whatsapp_numbers_limit,
                              current_period_start, current_period_end,
                              trial_start, trial_end""",
                *values,
            )
            return dict(row)

    async def set_subscription_status(
        self, organization_id: uuid.UUID | str, status: str
    ) -> None:
        if isinstance(organization_id, str):
            organization_id = uuid.UUID(organization_id)
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE organizations SET subscription_status = $1 WHERE id = $2",
                status,
                organization_id,
            )

    async def check_message_usage(self, org_id: uuid.UUID | str) -> dict | None:
        if isinstance(org_id, str):
            org_id = uuid.UUID(org_id)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT messages_used_this_period, messages_limit
                FROM organizations WHERE id = $1
            """, org_id)
            return dict(row) if row else None

    async def increment_message_usage(
        self, organization_id: uuid.UUID | str, conn=None
    ) -> int | None:
        if isinstance(organization_id, str):
            organization_id = uuid.UUID(organization_id)
        if conn is not None:
            return await self._increment_message_usage(conn, organization_id)
        async with self.pool.acquire() as c:
            return await self._increment_message_usage(c, organization_id)

    async def _increment_message_usage(self, conn, org_id: uuid.UUID) -> int | None:
        row = await conn.fetchrow("""
            UPDATE organizations
            SET messages_used_this_period = messages_used_this_period + 1
            WHERE id = $1 AND (messages_limit IS NULL OR messages_used_this_period < messages_limit)
            RETURNING messages_used_this_period
        """, org_id)
        return row["messages_used_this_period"] if row else None

    async def reset_message_usage(
        self,
        organization_id: uuid.UUID | str,
        period_start: datetime,
        period_end: datetime,
    ) -> None:
        if isinstance(organization_id, str):
            organization_id = uuid.UUID(organization_id)
        async with self.pool.acquire() as conn:
            await conn.execute(
                """UPDATE organizations
                   SET messages_used_this_period = 0,
                       current_period_start = $1,
                       current_period_end = $2
                   WHERE id = $3""",
                period_start,
                period_end,
                organization_id,
            )

    # ── Stripe Integration ────────────────────────────────────

    async def process_stripe_event(
        self, event_id: str, organization_id: uuid.UUID | str
    ) -> bool:
        if isinstance(organization_id, str):
            organization_id = uuid.UUID(organization_id)
        async with self.pool.acquire() as conn:
            try:
                await conn.execute(
                    "INSERT INTO processed_stripe_events (event_id, organization_id) VALUES ($1, $2)",
                    event_id,
                    organization_id,
                )
                return True
            except asyncpg.exceptions.UniqueViolationError:
                return False

    async def process_stripe_event_in_tx(
        self, conn, event_id: str, organization_id: uuid.UUID | str
    ) -> bool:
        """Like process_stripe_event but uses an existing connection/transaction
        so the dedup INSERT and the billing effect run atomically together."""
        if isinstance(organization_id, str):
            organization_id = uuid.UUID(organization_id)
        # ON CONFLICT does not poison the surrounding transaction on replay.
        return await conn.fetchval(
            "INSERT INTO processed_stripe_events (event_id, organization_id) VALUES ($1, $2) "
            "ON CONFLICT (event_id, organization_id) DO NOTHING RETURNING event_id",
            event_id, organization_id,
        ) is not None

    async def enqueue_governance(self, organization_id, event_kind: str, payload: dict) -> str:
        if event_kind not in {"audit", "usage"}:
            raise ValueError("Invalid governance kind")
        if not isinstance(payload, dict):
            raise ValueError("Governance payload must be an object")
        if event_kind == "usage":
            _assert_regular_usage_type(payload.get("event_type"))
            quantity = payload.get("quantity", 1)
            if (not isinstance(payload.get("event_type"), str)
                    or not payload["event_type"].strip()
                    or isinstance(quantity, bool) or not isinstance(quantity, int)
                    or quantity <= 0 or not isinstance(payload.get("metadata", {}), dict)):
                raise ValueError("Invalid usage governance payload")
        elif (not isinstance(payload.get("action"), str)
              or not payload["action"].strip()
              or not isinstance(payload.get("details", {}), dict)):
            raise ValueError("Invalid audit governance payload")
        # Reject poison messages before they can permanently hold the tenant's
        # accounting flag. The drain casts these fields to UUID in PostgreSQL.
        for key in ("user_id", "target_id"):
            value = payload.get(key)
            if value is not None:
                try:
                    uuid.UUID(str(value))
                except (TypeError, ValueError, AttributeError) as exc:
                    raise ValueError(f"Invalid audit {key}") from exc
        serialized = json.dumps(payload, default=str)
        if len(serialized.encode("utf-8")) > 64 * 1024:
            raise ValueError("Governance payload exceeds 64 KiB")
        event_id = uuid.uuid4()
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT id FROM organizations WHERE id = $1::uuid FOR UPDATE", str(organization_id)
                )
                await conn.execute("""
                    INSERT INTO governance_outbox(id, organization_id, event_kind, payload)
                    VALUES ($1, $2::uuid, $3, $4::jsonb)
                """, event_id, str(organization_id), event_kind, serialized)
                if event_kind == "usage":
                    await conn.execute(
                        "UPDATE organizations SET ai_accounting_blocked = TRUE WHERE id = $1::uuid",
                        str(organization_id),
                    )
        return str(event_id)

    @system_scope("worker drains durable org-scoped governance events; every write includes organization_id")
    async def drain_governance_outbox(self, limit: int = 100) -> int:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("Governance drain limit must be between 1 and 1000")
        processed = 0
        # Lock organization first, as enqueue does, so clearing its hold cannot
        # race a newly queued event or deadlock a concurrent accounting writer.
        async with self.pool.acquire() as conn:
            organizations = await conn.fetch(
                "SELECT DISTINCT organization_id FROM governance_outbox ORDER BY organization_id LIMIT $1", limit
            )
            for organization in organizations:
                org_id = organization["organization_id"]
                async with conn.transaction():
                    await conn.execute("SELECT id FROM organizations WHERE id = $1 FOR UPDATE", org_id)
                    rows = await conn.fetch("""
                        SELECT * FROM governance_outbox WHERE organization_id = $1
                        ORDER BY created_at, id LIMIT $2 FOR UPDATE SKIP LOCKED
                    """, org_id, limit - processed)
                    for row in rows:
                        payload = row["payload"]
                        if isinstance(payload, str):
                            payload = json.loads(payload)
                        if row["event_kind"] == "usage":
                            _assert_regular_usage_type(payload.get("event_type"))
                            await conn.execute("""
                                INSERT INTO usage_events(id, organization_id, event_type, quantity, metadata)
                                VALUES ($1, $2, $3, $4, $5::jsonb) ON CONFLICT (id) DO NOTHING
                            """, row["id"], org_id, payload["event_type"], payload.get("quantity", 1),
                                json.dumps(payload.get("metadata", {})))
                        else:
                            await conn.execute("""
                                INSERT INTO audit_log(id, organization_id, user_id, auth_user_id,
                                                      action, target_table, target_id, details)
                                VALUES ($1, $2, $3::uuid, $4, $5, $6, $7::uuid, $8::jsonb)
                                ON CONFLICT (id) DO NOTHING
                            """, row["id"], org_id, payload.get("user_id"), payload.get("auth_user_id"),
                                payload["action"], payload.get("target_table"), payload.get("target_id"),
                                json.dumps(payload.get("details", {})))
                        await conn.execute("DELETE FROM governance_outbox WHERE id = $1 AND organization_id = $2", row["id"], org_id)
                        processed += 1
                    await conn.execute("""
                        UPDATE organizations SET ai_accounting_blocked = (
                            EXISTS (SELECT 1 FROM governance_outbox
                                    WHERE organization_id = $1 AND event_kind = 'usage')
                            OR EXISTS (SELECT 1 FROM usage_events
                                       WHERE organization_id = $1
                                         AND metadata->>'accounting_status' = 'unresolved')
                        ) WHERE id = $1
                    """, org_id)
                if processed >= limit:
                    break
        return processed

    async def update_plan_limits(
        self, organization_id: uuid.UUID | str, plan_slug: str
    ) -> dict:
        from src.core.billing.plans import get_plan
        plan = get_plan(plan_slug)
        if isinstance(organization_id, str):
            organization_id = uuid.UUID(organization_id)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """UPDATE organizations
                   SET plan = $1,
                       messages_limit = $2,
                       users_limit = $3,
                       whatsapp_numbers_limit = $4
                   WHERE id = $5
                   RETURNING stripe_customer_id, subscription_id, subscription_status,
                             plan, messages_used_this_period, messages_limit,
                             users_limit, whatsapp_numbers_limit,
                             current_period_start, current_period_end,
                             trial_start, trial_end""",
                plan_slug,
                plan.messages_limit,
                plan.users_limit,
                plan.whatsapp_numbers_limit,
                organization_id,
            )
            return dict(row)

    @system_scope("risoluzione tenant da stripe_customer_id platform-unique")
    async def get_organization_by_stripe_customer(
        self, stripe_customer_id: str
    ) -> dict | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, stripe_customer_id, subscription_status, plan, "
                "current_period_start "
                "FROM organizations WHERE stripe_customer_id = $1",
                stripe_customer_id,
            )
            if row is None:
                return None
            return dict(row)
