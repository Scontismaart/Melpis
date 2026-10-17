from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import json
import uuid

import pytest
from fastapi import HTTPException

from src.core.billing.checkout import create_durable_checkout, validate_redirect_url
from src.core.billing.routes import _get_stripe
from src.core.billing.webhook_handler import _handle_checkout_completed, _lock_event_scope
from src.core.db.repositories.billing_repo import BillingRepository


class _AsyncContext:
    def __init__(self, value=None):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _AsyncContext(self.conn)


@pytest.mark.asyncio
async def test_commercial_bootstrap_rejects_yearly_checkout_before_stripe(monkeypatch):
    from src.core.billing.plans import PLANS
    from src.core.billing.routes import CheckoutSessionRequest, create_checkout_session

    monkeypatch.setenv("LAUNCH_PROFILE", "commercial_bootstrap")
    monkeypatch.setenv("STRIPE_ANNUAL_PRICING_ENABLED", "true")
    monkeypatch.setattr(PLANS["starter"], "stripe_price_id_yearly", "price_test_yearly")

    def stripe_must_not_be_initialized():
        raise AssertionError("yearly commercial-bootstrap checkout reached Stripe")

    monkeypatch.setattr("src.core.billing.routes._get_stripe", stripe_must_not_be_initialized)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(repo=object())),
        headers={},
    )
    checkout_request = CheckoutSessionRequest(
        plan="starter",
        interval="yearly",
        success_url="https://app.melpis.it/success",
        cancel_url="https://app.melpis.it/cancel",
    )

    with pytest.raises(HTTPException) as exc:
        await create_checkout_session(
            checkout_request,
            request,
            {"organization_id": str(uuid.uuid4())},
            None,
        )

    assert exc.value.status_code == 503
    assert "Stripe price ID (yearly) non configurato" in exc.value.detail


@pytest.mark.asyncio
async def test_stripe_dedup_matches_composite_primary_key():
    conn = AsyncMock()
    conn.fetchval.return_value = "evt_1"
    repo = BillingRepository(pool=None)

    assert await repo.process_stripe_event_in_tx(conn, "evt_1", uuid.uuid4()) is True
    query = conn.fetchval.await_args.args[0]
    assert "ON CONFLICT (event_id, organization_id)" in query


@pytest.mark.asyncio
async def test_governance_drain_persists_usage_before_unblocking_tenant():
    org_id = uuid.uuid4()
    event_id = uuid.uuid4()

    class Conn:
        def __init__(self):
            self.executed = []

        def transaction(self):
            return _AsyncContext()

        async def fetch(self, query, *args):
            if "SELECT DISTINCT organization_id" in query:
                return [{"organization_id": org_id}]
            assert "FOR UPDATE SKIP LOCKED" in query
            return [{
                "id": event_id,
                "organization_id": org_id,
                "event_kind": "usage",
                "payload": {
                    "event_type": "ai_response",
                    "quantity": 1,
                    "metadata": {"model": "test-model"},
                },
            }]

        async def execute(self, query, *args):
            self.executed.append((" ".join(query.split()), args))

    conn = Conn()
    repo = BillingRepository(_Pool(conn))
    assert await repo.drain_governance_outbox(limit=10) == 1

    statements = [query for query, _ in conn.executed]
    usage_index = next(i for i, query in enumerate(statements) if "INSERT INTO usage_events" in query)
    delete_index = next(i for i, query in enumerate(statements) if "DELETE FROM governance_outbox" in query)
    unblock_index = next(i for i, query in enumerate(statements) if "ai_accounting_blocked = (" in query)
    assert usage_index < delete_index < unblock_index
    assert "event_kind = 'usage'" in statements[unblock_index]
    assert "metadata->>'accounting_status' = 'unresolved'" in statements[unblock_index]


@pytest.mark.parametrize("limit", [0, -1, 1001, True, 1.5])
@pytest.mark.asyncio
async def test_governance_drain_rejects_unsafe_limits(limit):
    with pytest.raises(ValueError):
        await BillingRepository(pool=None).drain_governance_outbox(limit=limit)


@pytest.mark.parametrize("kind,payload", [
    ("usage", {"quantity": 1, "metadata": {}}),
    ("usage", {"event_type": "ai_response", "quantity": 0, "metadata": {}}),
    ("audit", {"action": "billing.changed", "target_id": "not-a-uuid"}),
])
@pytest.mark.asyncio
async def test_governance_enqueue_rejects_poison_payloads_before_storage(kind, payload):
    with pytest.raises(ValueError):
        await BillingRepository(pool=None).enqueue_governance(uuid.uuid4(), kind, payload)


@pytest.mark.asyncio
async def test_checkout_replay_reuses_persisted_session_without_network():
    org_id = uuid.uuid4()
    payload = {
        "price_id": "price_test",
        "plan": "starter",
        "interval": "monthly",
        "success_url": "https://app.example.test/success",
        "cancel_url": "https://app.example.test/cancel",
    }
    import hashlib
    import json
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    class Conn:
        def __init__(self):
            self.calls = 0

        def transaction(self):
            return _AsyncContext()

        async def fetchrow(self, query, *args):
            self.calls += 1
            if "FROM organizations" in query:
                return {"id": org_id, "stripe_customer_id": "cus_test", "trial_end": None}
            return {
                "id": uuid.uuid4(),
                "payload_hash": digest,
                "expires_at": datetime.now(timezone.utc) + timedelta(minutes=30),
                "payload": json.dumps({**payload, "trial_end_unix": None}),
                "session_url": "https://checkout.stripe.test/session",
            }

    stripe_call = AsyncMock()
    result = await create_durable_checkout(
        type("Repo", (), {"pool": _Pool(Conn())})(), org_id, "checkout-key-001",
        payload, object(), stripe_call,
    )
    assert result == {"url": "https://checkout.stripe.test/session"}
    stripe_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_checkout_does_not_reuse_legacy_session_without_exact_trial_snapshot():
    org_id = uuid.uuid4()
    payload = {
        "price_id": "price_test",
        "plan": "starter",
        "interval": "monthly",
        "success_url": "https://app.example.test/success",
        "cancel_url": "https://app.example.test/cancel",
    }
    import hashlib
    import json
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    class Conn:
        def transaction(self):
            return _AsyncContext()

        async def fetchrow(self, query, *args):
            if "FROM organizations" in query:
                return {"id": org_id, "stripe_customer_id": "cus_test", "trial_end": None}
            return {
                "id": uuid.uuid4(),
                "payload_hash": digest,
                "expires_at": datetime.now(timezone.utc) + timedelta(minutes=30),
                "payload": json.dumps({**payload, "trial_days_remaining": 0}),
                "session_url": "https://checkout.stripe.test/legacy-session",
            }

    stripe_call = AsyncMock()
    with pytest.raises(HTTPException, match="predates exact trial snapshot") as exc:
        await create_durable_checkout(
            type("Repo", (), {"pool": _Pool(Conn())})(), org_id, "checkout-legacy-001",
            payload, object(), stripe_call,
        )
    assert exc.value.status_code == 409
    stripe_call.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_session_url", [None, "https://checkout.stripe.test/legacy-session"])
async def test_checkout_blocks_new_intent_while_legacy_result_may_still_be_active(legacy_session_url):
    org_id = uuid.uuid4()
    payload = {
        "price_id": "price_test",
        "plan": "starter",
        "interval": "monthly",
        "success_url": "https://app.example.test/success",
        "cancel_url": "https://app.example.test/cancel",
    }

    class Conn:
        def __init__(self):
            self.legacy_intent_query = None

        def transaction(self):
            return _AsyncContext()

        async def fetchrow(self, query, *args):
            if "FROM organizations" in query:
                return {"id": org_id, "stripe_customer_id": "cus_test", "trial_end": None}
            if "AND NOT (payload ? 'trial_end_unix')" in query:
                self.legacy_intent_query = query
                if "session_url IS NOT NULL" in query:
                    return None
                return {
                    "id": uuid.uuid4(),
                    "expires_at": datetime.now(timezone.utc) + timedelta(minutes=20),
                    "session_url": legacy_session_url,
                }
            return None

    stripe_call = AsyncMock()
    conn = Conn()
    with pytest.raises(HTTPException, match="legacy checkout intent is still valid or its result is uncertain") as exc:
        await create_durable_checkout(
            type("Repo", (), {"pool": _Pool(conn)})(), org_id, "checkout-new-key-001",
            payload, object(), stripe_call,
        )
    assert exc.value.status_code == 409
    assert "session_url IS NOT NULL" not in conn.legacy_intent_query
    stripe_call.assert_not_awaited()


@pytest.mark.parametrize(
    ("trial_label", "trial_offset", "status", "has_trial"),
    [
        ("23h_remaining", timedelta(hours=23), "trialing", True),
        ("expired", timedelta(hours=-1), "trialing", False),
        ("full_7_day_trial", timedelta(days=7), "trialing", True),
        ("paid_org_stale_trial_end", timedelta(days=7), "active", False),
    ],
)
@pytest.mark.asyncio
async def test_checkout_preserves_exact_trial_deadline_and_payment_collection(
    trial_label, trial_offset, status, has_trial,
):
    org_id = uuid.uuid4()
    original_trial_end = datetime.now(timezone.utc) + trial_offset

    class Conn:
        def __init__(self):
            self.execute = AsyncMock()

        def transaction(self):
            return _AsyncContext()

        async def fetchrow(self, query, *args):
            if "FROM organizations" in query:
                return {
                    "id": org_id,
                    "stripe_customer_id": "cus_test",
                    "trial_end": original_trial_end,
                    "subscription_status": status,
                }
            return None

    stripe_call = AsyncMock(return_value=SimpleNamespace(id="cs_test", url="https://checkout.test/cs"))
    payload = {
        "price_id": "price_test_monthly",
        "plan": "starter",
        "interval": "monthly",
        "success_url": "https://app.example.test/success",
        "cancel_url": "https://app.example.test/cancel",
    }
    stripe = SimpleNamespace(checkout=SimpleNamespace(Session=SimpleNamespace(create=object())))

    result = await create_durable_checkout(
        type("Repo", (), {"pool": _Pool(Conn())})(),
        org_id,
        f"checkout-{trial_label}",
        payload,
        stripe,
        stripe_call,
    )

    assert result["url"] == "https://checkout.test/cs"
    kwargs = stripe_call.await_args.kwargs
    assert kwargs["payment_method_collection"] == ("if_required" if has_trial else "required")
    assert ("subscription_data" in kwargs) is has_trial
    if has_trial:
        expected_trial_end = int(original_trial_end.timestamp())
        assert kwargs["subscription_data"]["trial_end"] == expected_trial_end
        assert kwargs["expires_at"] <= expected_trial_end
        assert kwargs["metadata"]["trial_end_unix"] == str(expected_trial_end)
        assert stripe_call.await_args.kwargs["subscription_data"]["trial_settings"] == {
            "end_behavior": {"missing_payment_method": "cancel"}
        }
    else:
        assert kwargs["metadata"]["trial_end_unix"] == "none"


@pytest.mark.asyncio
async def test_checkout_does_not_create_session_when_trial_deadline_is_inside_stripe_minimum():
    org_id = uuid.uuid4()
    class Conn:
        def transaction(self):
            return _AsyncContext()

        async def fetchrow(self, query, *args):
            if "FROM organizations" in query:
                return {
                    "id": org_id,
                    "stripe_customer_id": "cus_test",
                    "trial_end": datetime.now(timezone.utc) + timedelta(minutes=20),
                    "subscription_status": "trialing",
                }
            return None

    stripe_call = AsyncMock()
    payload = {
        "price_id": "price_test_monthly",
        "plan": "starter",
        "interval": "monthly",
        "success_url": "https://app.example.test/success",
        "cancel_url": "https://app.example.test/cancel",
    }
    with pytest.raises(HTTPException) as exc:
        await create_durable_checkout(
            type("Repo", (), {"pool": _Pool(Conn())})(),
            org_id,
            "checkout-too-short-001",
            payload,
            object(),
            stripe_call,
        )
    assert exc.value.status_code == 409
    stripe_call.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("include_checkout_metadata", [True, False])
async def test_checkout_webhook_preserves_absolute_trial_deadline(include_checkout_metadata):
    org_id = uuid.uuid4()
    original_trial_end = datetime.now(timezone.utc) + timedelta(hours=23)
    expected_trial_end = (
        datetime.fromtimestamp(int(original_trial_end.timestamp()), tz=timezone.utc)
        if include_checkout_metadata
        else original_trial_end
    )

    class Conn:
        def __init__(self):
            self.execute = AsyncMock()
            self.fetchrow = AsyncMock(return_value={"trial_end": original_trial_end})

    conn = Conn()
    repo = SimpleNamespace(process_stripe_event_in_tx=AsyncMock(return_value=True))
    metadata = (
        {"trial_end_unix": str(int(original_trial_end.timestamp()))}
        if include_checkout_metadata
        else {}
    )
    result = await _handle_checkout_completed(
        conn,
        repo,
        {
            "mode": "subscription",
            "client_reference_id": str(org_id),
            "subscription": "sub_test",
            "customer": "cus_test",
            "metadata": metadata,
        },
        "evt_exact_trial_end",
        0,
    )

    assert result["action"] == "subscription_created"
    assert conn.execute.await_args.args[4] == expected_trial_end
    assert conn.fetchrow.await_count == 1


@pytest.mark.asyncio
async def test_checkout_webhook_clamps_future_metadata_to_original_trial_deadline():
    org_id = uuid.uuid4()
    original_trial_end = datetime.now(timezone.utc) + timedelta(hours=23)

    class Conn:
        def __init__(self):
            self.execute = AsyncMock()
            self.fetchrow = AsyncMock(return_value={"trial_end": original_trial_end})

    conn = Conn()
    repo = SimpleNamespace(process_stripe_event_in_tx=AsyncMock(return_value=True))
    await _handle_checkout_completed(
        conn,
        repo,
        {
            "mode": "subscription",
            "client_reference_id": str(org_id),
            "subscription": "sub_test",
            "customer": "cus_test",
            "metadata": {"trial_end_unix": str(int((original_trial_end + timedelta(days=30)).timestamp()))},
        },
        "evt_future_trial_metadata",
        0,
    )

    expected_deadline = datetime.fromtimestamp(int(original_trial_end.timestamp()), tz=timezone.utc)
    assert conn.execute.await_args.args[4] == expected_deadline
    conn.fetchrow.assert_awaited_once()


@pytest.mark.asyncio
async def test_checkout_webhook_explicit_no_trial_marker_does_not_reuse_stale_db_deadline():
    org_id = uuid.uuid4()
    stale_trial_end = datetime.now(timezone.utc) + timedelta(days=30)

    class Conn:
        def __init__(self):
            self.execute = AsyncMock()
            self.fetchrow = AsyncMock(return_value={"trial_end": stale_trial_end})

    conn = Conn()
    repo = SimpleNamespace(process_stripe_event_in_tx=AsyncMock(return_value=True))
    before = datetime.now(timezone.utc)
    await _handle_checkout_completed(
        conn,
        repo,
        {
            "mode": "subscription",
            "client_reference_id": str(org_id),
            "subscription": "sub_test",
            "customer": "cus_test",
            "metadata": {"trial_end_unix": "none"},
        },
        "evt_no_trial_marker",
        0,
    )
    after = datetime.now(timezone.utc)

    assert before <= conn.execute.await_args.args[4] <= after
    conn.fetchrow.assert_not_awaited()


@pytest.mark.asyncio
async def test_checkout_retry_reuses_frozen_trial_deadline_after_organization_changes():
    org_id = uuid.uuid4()
    created_at = datetime.now(timezone.utc)
    original_trial_end = created_at + timedelta(hours=23)

    class Conn:
        def __init__(self):
            self.trial_end = original_trial_end
            self.intent = None

        def transaction(self):
            return _AsyncContext()

        async def fetchrow(self, query, *args):
            if "FROM organizations" in query:
                return {
                    "id": org_id,
                    "stripe_customer_id": "cus_test",
                    "trial_end": self.trial_end,
                    "subscription_status": "trialing",
                }
            return self.intent

        async def execute(self, query, *args):
            if "INSERT INTO billing_checkout_intents" in query:
                self.intent = {
                    "id": args[0],
                    "payload_hash": args[3],
                    "payload": json.loads(args[4]),
                    "expires_at": args[5],
                    "session_url": None,
                }
            elif "UPDATE billing_checkout_intents" in query and self.intent:
                self.intent["session_url"] = args[3]

    conn = Conn()
    repo = SimpleNamespace(pool=_Pool(conn))
    payload = {
        "price_id": "price_test_monthly",
        "plan": "starter",
        "interval": "monthly",
        "success_url": "https://app.example.test/success",
        "cancel_url": "https://app.example.test/cancel",
    }
    stripe = SimpleNamespace(checkout=SimpleNamespace(Session=SimpleNamespace(create=object())))
    first_attempt = AsyncMock(side_effect=RuntimeError("simulated network failure"))
    with pytest.raises(RuntimeError, match="simulated network failure"):
        await create_durable_checkout(repo, org_id, "checkout-retry-trial-001", payload, stripe, first_attempt)

    conn.trial_end = created_at + timedelta(days=7)
    retry = AsyncMock(return_value=SimpleNamespace(id="cs_retry", url="https://checkout.test/retry"))
    result = await create_durable_checkout(repo, org_id, "checkout-retry-trial-001", payload, stripe, retry)

    assert result["url"] == "https://checkout.test/retry"
    assert retry.await_args.kwargs["subscription_data"]["trial_end"] == int(original_trial_end.timestamp())


def test_checkout_redirect_is_restricted_to_configured_origin():
    assert validate_redirect_url(
        "https://app.example.test/billing/success?session=1",
        "https://app.example.test",
    ).endswith("session=1")
    with pytest.raises(HTTPException) as exc:
        validate_redirect_url("https://evil.example/success", "https://app.example.test")
    assert exc.value.status_code == 400


def test_live_stripe_requires_explicit_commercial_profile(monkeypatch):
    monkeypatch.setenv("LLM_COST_POLICY", "free_only")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_forbidden")
    with pytest.raises(HTTPException) as exc:
        _get_stripe()
    assert exc.value.status_code == 503


def test_commercial_profile_accepts_live_stripe_with_free_only_ai(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("LAUNCH_PROFILE", "commercial_bootstrap")
    monkeypatch.setenv("SANDBOX_ONLY", "false")
    monkeypatch.setenv("LLM_COST_POLICY", "free_only")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_unit_safe_value")
    stripe = _get_stripe()
    assert stripe.api_key == "sk_live_unit_safe_value"


@pytest.mark.asyncio
async def test_paid_deposit_adopts_session_after_send_then_mark_crash():
    org_id = uuid.uuid4()
    booking_id = uuid.uuid4()

    class Conn:
        def __init__(self):
            self.execute = AsyncMock()

        async def fetchrow(self, query, *args):
            return {
                "deposit_amount_minor": 2500,
                "deposit_currency": "eur",
                "deposit_session_id": None,
            }

    conn = Conn()
    repo = type("Repo", (), {"process_stripe_event_in_tx": AsyncMock(return_value=True)})()
    result = await _handle_checkout_completed(conn, repo, {
        "id": "cs_test_deposit",
        "mode": "payment",
        "payment_status": "paid",
        "amount_total": 2500,
        "currency": "eur",
        "metadata": {"booking_id": str(booking_id), "organization_id": str(org_id)},
    }, "evt_deposit", 0)

    assert result["action"] == "deposit_paid"
    update_query = conn.execute.await_args.args[0]
    assert "deposit_session_id = COALESCE(deposit_session_id, $1)" in update_query
    assert "payment_link =" not in update_query
    assert conn.execute.await_args.args[1] == "cs_test_deposit"


@pytest.mark.asyncio
async def test_async_checkout_tie_cannot_relax_existing_state():
    org_id = uuid.uuid4()

    class Conn:
        async def fetchrow(self, query, *args):
            return {
                "id": org_id,
                "stripe_customer_id": "cus_test",
                "subscription_id": "sub_test",
                "subscription_status": "trialing",
                "stripe_event_created": 100,
                "stripe_event_id": "evt_previous",
            }

        execute = AsyncMock()

    gate = await _lock_event_scope(Conn(), {
        "id": "evt_async",
        "created": 100,
        "type": "checkout.session.async_payment_succeeded",
    }, {
        "mode": "subscription",
        "client_reference_id": str(org_id),
        "customer": "cus_test",
        "subscription": "sub_test",
    })
    assert gate["ignored"] == "ambiguous_event_order"
