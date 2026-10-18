from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import json
import hashlib
import uuid

import pytest
from fastapi import HTTPException

from src.core.billing.checkout import create_durable_checkout, validate_redirect_url
from src.core.billing.routes import _get_stripe
from src.core.billing.webhook_handler import (
    _checkout_intent_for_session,
    _handle_checkout_completed,
    _handle_checkout_expired,
    _handle_subscription_updated,
    _lock_event_scope,
)
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
@pytest.mark.parametrize("intent_age", [timedelta(minutes=-20), timedelta(days=10)])
async def test_checkout_blocks_new_intent_until_unresolved_provider_result_is_resolved(
    legacy_session_url, intent_age,
):
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
            if "SELECT id FROM billing_checkout_intents" in query:
                self.legacy_intent_query = query
                return {
                    "id": uuid.uuid4(),
                    "expires_at": datetime.now(timezone.utc) - intent_age,
                    "session_url": legacy_session_url,
                }
            return None

        execute = AsyncMock()

    stripe_call = AsyncMock()
    conn = Conn()
    with pytest.raises(HTTPException, match="Another checkout is still active or awaiting webhook resolution") as exc:
        await create_durable_checkout(
            type("Repo", (), {"pool": _Pool(conn)})(), org_id, "checkout-new-key-001",
            payload, object(), stripe_call,
        )
    assert exc.value.status_code == 409
    assert "COALESCE((payload->>'_resolved')::boolean, FALSE) = FALSE" in conn.legacy_intent_query
    assert "session_url IS NOT NULL" not in conn.legacy_intent_query
    assert "expires_at >" not in conn.legacy_intent_query
    stripe_call.assert_not_awaited()


@pytest.mark.parametrize(
    ("trial_label", "trial_offset", "status", "checkout_allowed", "has_trial"),
    [
        ("full_7_day_trial", timedelta(days=7), "trialing", True, True),
        ("49h_remaining", timedelta(hours=49), "trialing", True, True),
        ("48h_boundary", timedelta(hours=48), "trialing", False, True),
        ("47h_remaining", timedelta(hours=47), "trialing", False, True),
        ("23h_remaining", timedelta(hours=23), "trialing", False, True),
        ("30m_remaining", timedelta(minutes=30), "trialing", False, True),
        ("expired", timedelta(hours=-1), "trialing", True, False),
        ("paid_org_stale_trial_end", timedelta(days=7), "active", True, False),
    ],
)
@pytest.mark.asyncio
async def test_checkout_preserves_exact_trial_deadline_and_payment_collection(
    trial_label, trial_offset, status, checkout_allowed, has_trial,
):
    org_id = uuid.uuid4()
    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    original_trial_end = now + trial_offset

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

    conn = Conn()
    stripe_call = AsyncMock(return_value=SimpleNamespace(id="cs_test", url="https://checkout.test/cs"))
    payload = {
        "price_id": "price_test_monthly",
        "plan": "starter",
        "interval": "monthly",
        "success_url": "https://app.example.test/success",
        "cancel_url": "https://app.example.test/cancel",
    }
    stripe = SimpleNamespace(checkout=SimpleNamespace(Session=SimpleNamespace(create=object())))

    if not checkout_allowed:
        with pytest.raises(HTTPException) as exc:
            await create_durable_checkout(
                type("Repo", (), {"pool": _Pool(conn)})(), org_id,
                f"checkout-{trial_label}", payload, stripe, stripe_call,
                now_provider=lambda: now,
            )
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "trial_active_checkout_deferred"
        stripe_call.assert_not_awaited()
        conn.execute.assert_not_awaited()
        return

    result = await create_durable_checkout(
        type("Repo", (), {"pool": _Pool(conn)})(),
        org_id,
        f"checkout-{trial_label}",
        payload,
        stripe,
        stripe_call,
        now_provider=lambda: now,
    )

    assert result["url"] == "https://checkout.test/cs"
    kwargs = stripe_call.await_args.kwargs
    assert kwargs["payment_method_collection"] == ("if_required" if has_trial else "always")
    assert ("trial_end" in kwargs["subscription_data"]) is has_trial
    intent_payload = json.loads(conn.execute.await_args_list[0].args[5])
    assert intent_payload["trial_end_unix"] == (
        int(original_trial_end.timestamp()) if has_trial else None
    )
    assert intent_payload["checkout_intent_id"] == kwargs["metadata"]["checkout_intent_id"]
    assert intent_payload["checkout_session_expires_at_unix"] == kwargs["expires_at"]
    assert intent_payload["stripe_customer_id"] == "cus_test"
    if has_trial:
        expected_trial_end = int(original_trial_end.timestamp())
        assert kwargs["subscription_data"]["trial_end"] == expected_trial_end
        assert kwargs["expires_at"] <= expected_trial_end
        assert kwargs["metadata"]["trial_end_unix"] == str(expected_trial_end)
        assert kwargs["subscription_data"]["metadata"]["checkout_intent_id"] == kwargs["metadata"]["checkout_intent_id"]
        assert stripe_call.await_args.kwargs["subscription_data"]["trial_settings"] == {
            "end_behavior": {"missing_payment_method": "cancel"}
        }
    else:
        assert kwargs["metadata"]["trial_end_unix"] == "none"
        assert "trial_settings" not in kwargs["subscription_data"]


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
async def test_checkout_session_is_not_created_with_less_than_30_minutes_before_expiry():
    org_id = uuid.uuid4()
    created_at = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    current_time = [created_at, created_at + timedelta(minutes=31)]

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
                    "trial_end": None,
                    "subscription_status": "active",
                }
            return None

    conn = Conn()
    stripe_call = AsyncMock()
    payload = {
        "price_id": "price_test_monthly",
        "plan": "starter",
        "interval": "monthly",
        "success_url": "https://app.example.test/success",
        "cancel_url": "https://app.example.test/cancel",
    }
    stripe = SimpleNamespace(checkout=SimpleNamespace(Session=SimpleNamespace(create=object())))

    with pytest.raises(HTTPException, match="too close to its frozen session expiry") as exc:
        await create_durable_checkout(
            type("Repo", (), {"pool": _Pool(conn)})(), org_id,
            "checkout-session-expiry-001", payload, stripe, stripe_call,
            now_provider=lambda: current_time.pop(0) if current_time else created_at + timedelta(minutes=31),
        )

    assert exc.value.status_code == 409
    stripe_call.assert_not_awaited()
    assert "_resolved" in conn.execute.await_args_list[1].args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("include_checkout_metadata", [True, False])
async def test_checkout_webhook_preserves_absolute_trial_deadline(include_checkout_metadata):
    org_id = uuid.uuid4()
    intent_id = uuid.uuid4()
    session_id = "cs_trial_binding"
    original_trial_end = datetime.now(timezone.utc) + timedelta(hours=23)
    expected_trial_end = (
        datetime.fromtimestamp(int(original_trial_end.timestamp()), tz=timezone.utc)
        if include_checkout_metadata
        else original_trial_end
    )

    class Conn:
        def __init__(self):
            self.execute = AsyncMock()
            self.fetch_queries = []

        async def fetchrow(self, query, *args):
            self.fetch_queries.append(query)
            if "FROM billing_checkout_intents" in query:
                return {
                    "id": intent_id,
                    "organization_id": org_id,
                    "session_id": session_id,
                    "payload": (
                        {
                            "checkout_intent_id": str(intent_id),
                            "trial_end_unix": int(original_trial_end.timestamp()),
                        }
                        if include_checkout_metadata else {}
                    ),
                }
            return {"trial_end": original_trial_end}

    conn = Conn()
    repo = SimpleNamespace(process_stripe_event_in_tx=AsyncMock(return_value=True))
    metadata = (
        {"checkout_intent_id": str(intent_id), "trial_end_unix": str(int(original_trial_end.timestamp()))}
        if include_checkout_metadata else {}
    )
    result = await _handle_checkout_completed(
        conn,
        repo,
        {
            "id": session_id,
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
    assert conn.execute.await_args_list[0].args[4] == expected_trial_end
    assert len(conn.fetch_queries) == 2


@pytest.mark.asyncio
async def test_checkout_webhook_clamps_future_metadata_to_original_trial_deadline():
    org_id = uuid.uuid4()
    intent_id = uuid.uuid4()
    session_id = "cs_future_trial_metadata"
    original_trial_end = datetime.now(timezone.utc) + timedelta(hours=23)
    future_trial_end = original_trial_end + timedelta(days=30)

    class Conn:
        def __init__(self):
            self.execute = AsyncMock()

        async def fetchrow(self, query, *args):
            if "FROM billing_checkout_intents" in query:
                return {
                    "id": intent_id,
                    "organization_id": org_id,
                    "session_id": session_id,
                    "payload": {
                        "checkout_intent_id": str(intent_id),
                        "trial_end_unix": int(future_trial_end.timestamp()),
                    },
                }
            return {"trial_end": original_trial_end}

    conn = Conn()
    repo = SimpleNamespace(process_stripe_event_in_tx=AsyncMock(return_value=True))
    await _handle_checkout_completed(
        conn,
        repo,
        {
            "id": session_id,
            "mode": "subscription",
            "client_reference_id": str(org_id),
            "subscription": "sub_test",
            "customer": "cus_test",
            "metadata": {
                "checkout_intent_id": str(intent_id),
                "trial_end_unix": str(int(future_trial_end.timestamp())),
            },
        },
        "evt_future_trial_metadata",
        0,
    )

    expected_deadline = datetime.fromtimestamp(int(original_trial_end.timestamp()), tz=timezone.utc)
    assert conn.execute.await_args_list[0].args[4] == expected_deadline


@pytest.mark.asyncio
async def test_checkout_webhook_explicit_no_trial_marker_does_not_reuse_stale_db_deadline():
    org_id = uuid.uuid4()
    intent_id = uuid.uuid4()
    session_id = "cs_no_trial_marker"
    stale_trial_end = datetime.now(timezone.utc) + timedelta(days=30)

    class Conn:
        def __init__(self):
            self.execute = AsyncMock()

        async def fetchrow(self, query, *args):
            return {
                "id": intent_id,
                "organization_id": org_id,
                "session_id": session_id,
                "payload": {
                    "checkout_intent_id": str(intent_id),
                    "trial_end_unix": None,
                },
            }

    conn = Conn()
    repo = SimpleNamespace(process_stripe_event_in_tx=AsyncMock(return_value=True))
    before = datetime.now(timezone.utc)
    await _handle_checkout_completed(
        conn,
        repo,
        {
            "id": session_id,
            "mode": "subscription",
            "client_reference_id": str(org_id),
            "subscription": "sub_test",
            "customer": "cus_test",
            "metadata": {"checkout_intent_id": str(intent_id), "trial_end_unix": "none"},
        },
        "evt_no_trial_marker",
        0,
    )
    after = datetime.now(timezone.utc)

    assert before <= conn.execute.await_args_list[0].args[4] <= after


@pytest.mark.asyncio
async def test_checkout_webhook_binds_to_intent_even_when_session_id_persistence_races():
    org_id = uuid.uuid4()
    intent_id = uuid.uuid4()
    trial_end = datetime.now(timezone.utc) + timedelta(days=7)
    session_id = "cs_webhook_before_response_save"

    class Conn:
        def __init__(self):
            self.execute = AsyncMock()

        async def fetchrow(self, query, *args):
            if "FROM billing_checkout_intents" in query:
                return {
                    "id": intent_id,
                    "organization_id": org_id,
                    "session_id": None,
                    "payload": {
                        "checkout_intent_id": str(intent_id),
                        "trial_end_unix": int(trial_end.timestamp()),
                    },
                }
            return {"trial_end": trial_end}

    conn = Conn()
    repo = SimpleNamespace(process_stripe_event_in_tx=AsyncMock(return_value=True))
    await _handle_checkout_completed(conn, repo, {
        "id": session_id,
        "mode": "subscription",
        "client_reference_id": str(org_id),
        "subscription": "sub_race",
        "customer": "cus_test",
        "metadata": {
            "checkout_intent_id": str(intent_id),
            "trial_end_unix": str(int(trial_end.timestamp())),
        },
    }, "evt_webhook_before_session_save", 7)

    resolve_query, intent_id_arg, session_id_arg, org_id_arg, subscription_id_arg = (
        conn.execute.await_args_list[1].args
    )
    assert "session_id = COALESCE(session_id, $2)" in resolve_query
    assert intent_id_arg == intent_id
    assert session_id_arg == session_id
    assert org_id_arg == str(org_id)
    assert subscription_id_arg == "sub_race"
    assert "_resolved" in resolve_query


@pytest.mark.asyncio
async def test_checkout_completed_rejects_session_from_another_intent_without_consuming_event():
    org_id = uuid.uuid4()
    intent_id = uuid.uuid4()

    class Conn:
        execute = AsyncMock()

        async def fetchrow(self, query, *args):
            return {
                "id": intent_id,
                "organization_id": org_id,
                "session_id": "cs_expected",
                "payload": {
                    "checkout_intent_id": str(intent_id),
                    "trial_end_unix": None,
                },
            }

    conn = Conn()
    repo = SimpleNamespace(process_stripe_event_in_tx=AsyncMock(return_value=True))
    result = await _handle_checkout_completed(conn, repo, {
        "id": "cs_unrelated",
        "mode": "subscription",
        "client_reference_id": str(org_id),
        "subscription": "sub_unrelated",
        "customer": "cus_test",
        "metadata": {"checkout_intent_id": str(intent_id), "trial_end_unix": "none"},
    }, "evt_unrelated_session", 7)

    assert result is None
    repo.process_stripe_event_in_tx.assert_not_awaited()
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_checkout_webhook_rejects_customer_mismatch_with_frozen_intent():
    org_id = uuid.uuid4()
    intent_id = uuid.uuid4()

    class Conn:
        async def fetchrow(self, query, *args):
            return {
                "id": intent_id,
                "organization_id": org_id,
                "session_id": None,
                "payload": {
                    "checkout_intent_id": str(intent_id),
                    "trial_end_unix": None,
                    "stripe_customer_id": "cus_expected",
                },
            }

    result = await _checkout_intent_for_session(Conn(), str(org_id), {
        "id": "cs_bound",
        "customer": "cus_other",
        "metadata": {
            "checkout_intent_id": str(intent_id),
            "trial_end_unix": "none",
        },
    })

    assert result is None


@pytest.mark.asyncio
async def test_checkout_expired_webhook_resolves_intent_for_next_explicit_checkout():
    org_id = uuid.uuid4()
    intent_id = uuid.uuid4()
    session_id = "cs_expired"

    class Conn:
        execute = AsyncMock()

        async def fetchrow(self, query, *args):
            return {
                "id": intent_id,
                "organization_id": org_id,
                "session_id": session_id,
                "payload": {"checkout_intent_id": str(intent_id), "trial_end_unix": None},
            }

    conn = Conn()
    repo = SimpleNamespace(process_stripe_event_in_tx=AsyncMock(return_value=True))
    result = await _handle_checkout_expired(conn, repo, {
        "id": session_id,
        "mode": "subscription",
        "client_reference_id": str(org_id),
        "customer": "cus_test",
        "metadata": {"checkout_intent_id": str(intent_id), "trial_end_unix": "none"},
    }, "evt_checkout_expired",)

    assert result == {"action": "checkout_expired", "organization_id": str(org_id)}
    assert "_resolved" in conn.execute.await_args.args[0]
    repo.process_stripe_event_in_tx.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_delta", [timedelta(days=30), timedelta(hours=-2)])
async def test_subscription_updated_never_extends_signup_trial_deadline(provider_delta):
    org_id = uuid.uuid4()
    canonical_trial_end = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
    provider_trial_end = canonical_trial_end + provider_delta

    class Conn:
        execute = AsyncMock()

        async def fetchrow(self, query, *args):
            return {
                "id": org_id,
                "stripe_customer_id": "cus_test",
                "subscription_id": "sub_test",
                "subscription_status": "trialing",
                "trial_end": canonical_trial_end,
                "plan": "starter",
                "current_period_start": None,
                "messages_used_this_period": 0,
            }

    conn = Conn()
    repo = SimpleNamespace(process_stripe_event_in_tx=AsyncMock(return_value=True))
    await _handle_subscription_updated(conn, repo, {
        "id": "sub_test",
        "customer": "cus_test",
        "status": "trialing",
        "trial_end": int(provider_trial_end.timestamp()),
    }, "evt_sub_update_trial",)

    assert conn.execute.await_args_list[0].args[0].find("UPDATE organizations SET subscription_status") >= 0
    stored_trial_end = conn.execute.await_args_list[0].args[3]
    assert stored_trial_end == min(provider_trial_end, canonical_trial_end)


@pytest.mark.asyncio
@pytest.mark.parametrize("stripe_created_session", [True, False])
async def test_checkout_retry_reuses_frozen_trial_deadline_after_organization_changes(stripe_created_session):
    org_id = uuid.uuid4()
    created_at = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    current_time = [created_at]
    original_trial_end = created_at + timedelta(hours=48, minutes=5)

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
            if "request_key = $2" in query:
                return self.intent if self.intent and self.intent["request_key"] == args[1] else None
            if "SELECT id FROM billing_checkout_intents" in query:
                return self.intent
            return None

        async def execute(self, query, *args):
            if "INSERT INTO billing_checkout_intents" in query:
                self.intent = {
                    "id": args[0],
                    "payload_hash": args[3],
                    "payload": json.loads(args[4]),
                    "expires_at": args[5],
                    "session_url": None,
                    "session_id": None,
                    "request_key": args[2],
                }
            elif "SET expires_at = NOW()" in query and self.intent:
                self.intent["payload"]["_resolved"] = True
            elif "SET session_id = $3" in query and self.intent:
                self.intent["session_id"] = args[2]
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
    cached_session = SimpleNamespace(id="cs_original", url="https://checkout.test/original")
    provider_idempotency_cache = {}

    async def first_request(_method, **kwargs):
        if stripe_created_session:
            provider_idempotency_cache[kwargs["idempotency_key"]] = cached_session
        raise RuntimeError("simulated lost response")

    first_attempt = AsyncMock(side_effect=first_request)
    class FakeStripeInvalidRequest(Exception):
        pass

    with pytest.raises(RuntimeError, match="simulated lost response"):
        await create_durable_checkout(
            repo, org_id, "checkout-retry-trial-001", payload, stripe, first_attempt,
            now_provider=lambda: current_time[0],
        )

    assert conn.intent["payload"]["trial_end_unix"] == int(original_trial_end.timestamp())
    current_time[0] = created_at + timedelta(minutes=6)
    conn.trial_end = created_at + timedelta(days=7)

    async def replay_request(_method, **kwargs):
        if kwargs["idempotency_key"] in provider_idempotency_cache:
            return provider_idempotency_cache[kwargs["idempotency_key"]]
        raise FakeStripeInvalidRequest("Stripe rejected trial_end below its 48-hour minimum")

    retry = AsyncMock(side_effect=replay_request)
    if stripe_created_session:
        result = await create_durable_checkout(
            repo, org_id, "checkout-retry-trial-001", payload, stripe, retry,
            now_provider=lambda: current_time[0],
        )
        assert result == {"url": cached_session.url}
        assert conn.intent["session_id"] == cached_session.id
    else:
        stripe.error = SimpleNamespace(InvalidRequestError=FakeStripeInvalidRequest)
        with pytest.raises(FakeStripeInvalidRequest, match="48-hour minimum"):
            await create_durable_checkout(
                repo, org_id, "checkout-retry-trial-001", payload, stripe, retry,
                now_provider=lambda: current_time[0],
            )
        assert conn.intent["session_id"] is None
        assert conn.intent["payload"]["_resolved"] is True

    retry.assert_awaited_once()
    assert retry.await_args.kwargs == first_attempt.await_args.kwargs
    assert retry.await_args.kwargs["idempotency_key"] == f"checkout:{conn.intent['id']}"
    assert conn.intent["payload"]["trial_end_unix"] == int(original_trial_end.timestamp())


@pytest.mark.asyncio
async def test_checkout_freezes_customer_before_creating_provider_session():
    org_id = uuid.uuid4()
    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)

    class Conn:
        def __init__(self):
            self.stripe_customer_id = None
            self.intent = None

        def transaction(self):
            return _AsyncContext()

        async def fetchrow(self, query, *args):
            if "FROM organizations" in query:
                return {
                    "id": org_id,
                    "stripe_customer_id": self.stripe_customer_id,
                    "trial_end": None,
                    "subscription_status": "incomplete",
                }
            if "request_key = $2" in query:
                return None
            if "SELECT id FROM billing_checkout_intents" in query:
                return None
            return None

        async def execute(self, query, *args):
            if "INSERT INTO billing_checkout_intents" in query:
                self.intent = {
                    "id": args[0],
                    "payload": json.loads(args[4]),
                    "expires_at": args[5],
                    "session_id": None,
                    "session_url": None,
                }
            elif "SET session_id = $3" in query:
                self.intent["session_id"] = args[2]
                self.intent["session_url"] = args[3]

        async def fetchval(self, query, *args):
            if "UPDATE organizations SET stripe_customer_id" in query:
                if self.stripe_customer_id is None:
                    self.stripe_customer_id = args[1]
                return self.stripe_customer_id
            if "UPDATE billing_checkout_intents" in query and "jsonb_set" in query:
                if self.intent["payload"].get("stripe_customer_id") not in {None, args[2]}:
                    return None
                self.intent["payload"]["stripe_customer_id"] = args[2]
                return args[2]
            return None

    conn = Conn()
    repo = SimpleNamespace(pool=_Pool(conn))
    stripe = SimpleNamespace(
        Customer=SimpleNamespace(create=object()),
        checkout=SimpleNamespace(Session=SimpleNamespace(create=object())),
    )
    session = SimpleNamespace(id="cs_customer_bound", url="https://checkout.test/customer-bound")

    async def stripe_call(method, **kwargs):
        if method is stripe.Customer.create:
            return SimpleNamespace(id="cus_bound")
        if method is stripe.checkout.Session.create:
            return session
        raise AssertionError("unexpected Stripe method")

    await create_durable_checkout(
        repo,
        org_id,
        "checkout-customer-bind-001",
        {
            "price_id": "price_test_monthly",
            "plan": "starter",
            "interval": "monthly",
            "success_url": "https://app.example.test/success",
            "cancel_url": "https://app.example.test/cancel",
        },
        stripe,
        stripe_call,
        now_provider=lambda: now,
    )

    assert conn.stripe_customer_id == "cus_bound"
    assert conn.intent["payload"]["stripe_customer_id"] == "cus_bound"


@pytest.mark.asyncio
async def test_checkout_fails_closed_if_organization_customer_changed_after_intent_creation():
    org_id = uuid.uuid4()
    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    intent_id = uuid.uuid4()
    payload = {
        "price_id": "price_test_monthly",
        "plan": "starter",
        "interval": "monthly",
        "success_url": "https://app.example.test/success",
        "cancel_url": "https://app.example.test/cancel",
    }
    frozen = {
        **payload,
        "trial_end_unix": int((now + timedelta(days=7)).timestamp()),
        "checkout_intent_id": str(intent_id),
        "checkout_session_expires_at_unix": int((now + timedelta(hours=1)).timestamp()),
        "stripe_customer_id": "cus_original",
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    class Conn:
        def transaction(self):
            return _AsyncContext()

        async def fetchrow(self, query, *args):
            if "FROM organizations" in query:
                return {
                    "id": org_id,
                    "stripe_customer_id": "cus_replaced",
                    "trial_end": now + timedelta(days=7),
                    "subscription_status": "trialing",
                }
            if "request_key = $2" in query:
                return {
                    "id": intent_id,
                    "payload_hash": digest,
                    "payload": frozen,
                    "expires_at": now + timedelta(hours=1),
                    "session_id": None,
                    "session_url": None,
                }
            return None

        execute = AsyncMock()

    stripe_call = AsyncMock()
    with pytest.raises(HTTPException, match="Checkout intent customer changed"):
        await create_durable_checkout(
            type("Repo", (), {"pool": _Pool(Conn())})(),
            org_id,
            "checkout-customer-change-001",
            payload,
            object(),
            stripe_call,
            now_provider=lambda: now,
        )

    stripe_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_checkout_rejects_a_second_idempotency_key_while_first_intent_is_unresolved():
    org_id = uuid.uuid4()
    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    trial_end = now + timedelta(days=7)

    class Conn:
        def __init__(self):
            self.intent = None

        def transaction(self):
            return _AsyncContext()

        async def fetchrow(self, query, *args):
            if "FROM organizations" in query:
                return {
                    "id": org_id,
                    "stripe_customer_id": "cus_test",
                    "trial_end": trial_end,
                    "subscription_status": "trialing",
                }
            if "request_key = $2" in query:
                return self.intent if self.intent and self.intent["request_key"] == args[1] else None
            if "SELECT id FROM billing_checkout_intents" in query:
                return self.intent
            return None

        async def execute(self, query, *args):
            if "INSERT INTO billing_checkout_intents" in query:
                self.intent = {
                    "id": args[0],
                    "payload_hash": args[3],
                    "payload": json.loads(args[4]),
                    "expires_at": args[5],
                    "session_url": None,
                    "session_id": None,
                    "request_key": args[2],
                }
            elif "UPDATE billing_checkout_intents" in query and self.intent:
                self.intent["session_id"] = args[2]
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
    first = AsyncMock(return_value=SimpleNamespace(id="cs_first", url="https://checkout.test/first"))
    await create_durable_checkout(
        repo, org_id, "checkout-intent-first-001", payload, stripe, first,
        now_provider=lambda: now,
    )

    second = AsyncMock(return_value=SimpleNamespace(id="cs_second", url="https://checkout.test/second"))
    with pytest.raises(HTTPException) as exc:
        await create_durable_checkout(
            repo, org_id, "checkout-intent-second-001", payload, stripe, second,
            now_provider=lambda: now,
        )

    assert exc.value.status_code == 409
    assert "Another checkout" in exc.value.detail
    second.assert_not_awaited()


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
async def test_async_checkout_tie_scope_does_not_mutate_entitlement():
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

    conn = Conn()
    gate = await _lock_event_scope(conn, {
        "id": "evt_async",
        "created": 100,
        "type": "checkout.session.async_payment_succeeded",
    }, {
        "mode": "subscription",
        "client_reference_id": str(org_id),
        "customer": "cus_test",
        "subscription": "sub_test",
    })
    assert gate["subscription_id"] == "sub_test"
    assert "ignored" not in gate
    conn.execute.assert_not_awaited()
