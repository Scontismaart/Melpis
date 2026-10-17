"""Durable checkout intent: retries reuse the exact provider request."""

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from fastapi import HTTPException

STRIPE_TRIAL_MINIMUM_LEAD = timedelta(hours=48, minutes=5)
CHECKOUT_SESSION_LIFETIME = timedelta(hours=1)
CHECKOUT_SESSION_MINIMUM_LEAD = timedelta(minutes=30)


def _checkout_now(now_provider=None) -> datetime:
    return now_provider() if now_provider is not None else datetime.now(timezone.utc)


def _trial_lead_is_safe(trial_end_unix: int, now: datetime) -> bool:
    """Leave a five-minute margin beyond Stripe's 48-hour minimum."""
    return trial_end_unix - now.timestamp() >= STRIPE_TRIAL_MINIMUM_LEAD.total_seconds()


def _active_trial_error(trial_end_unix: int) -> HTTPException:
    trial_end = datetime.fromtimestamp(trial_end_unix, tz=timezone.utc)
    return HTTPException(
        409,
        detail={
            "code": "trial_active_checkout_deferred",
            "trial_end": trial_end.isoformat(),
        },
    )


def validate_redirect_url(value: str, base_url: str) -> str:
    target, base = urlsplit(value), urlsplit(base_url)
    if (target.username or target.password or target.fragment or
            (target.scheme, target.netloc) != (base.scheme, base.netloc) or
            target.scheme not in {"https", "http"} or
            (target.scheme == "http" and target.hostname not in {"localhost", "127.0.0.1"})):
        raise HTTPException(400, "Checkout redirect must use the configured application origin")
    return value


async def create_durable_checkout(repo, org_id, request_key, payload, stripe, stripe_call, now_provider=None):
    if not request_key or not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", request_key):
        raise HTTPException(400, "A stable Idempotency-Key header (8-128 characters) is required")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    created_intent = False
    recovery_replay = False
    async with repo.pool.acquire() as conn:
        async with conn.transaction():
            org = await conn.fetchrow("SELECT * FROM organizations WHERE id = $1::uuid FOR UPDATE", str(org_id))
            if not org:
                raise HTTPException(404, "Organization not found")
            now = _checkout_now(now_provider)
            if org.get("subscription_id") and org.get("subscription_status") not in {
                "canceled", "incomplete_expired"
            }:
                raise HTTPException(409, "A subscription already exists; manage it from the billing portal")
            existing = await conn.fetchrow("""
                SELECT * FROM billing_checkout_intents WHERE organization_id = $1::uuid AND request_key = $2
                FOR UPDATE
            """, str(org_id), request_key)
            if existing:
                if existing["payload_hash"] != digest:
                    raise HTTPException(409, "Idempotency key already used for a different checkout")
                if existing["expires_at"] <= now:
                    raise HTTPException(409, "Checkout expired; create a new request key")
                intent = dict(existing)
                frozen = intent["payload"]
                if isinstance(frozen, str):
                    frozen = json.loads(frozen)
                if frozen.get("_resolved") is True:
                    raise HTTPException(409, "Checkout intent already finalized; create a new request key")
                if "trial_end_unix" not in frozen:
                    raise HTTPException(
                        409,
                        "Checkout intent predates exact trial snapshot; wait for the legacy session to expire",
                    )
                provider_expires_at = frozen.get("checkout_session_expires_at_unix")
                if provider_expires_at is not None and int(provider_expires_at) <= int(now.timestamp()):
                    raise HTTPException(409, "Checkout expired; create a new request key")
                if existing["session_url"]:
                    return {"url": existing["session_url"]}
                if "stripe_customer_id" not in frozen:
                    raise HTTPException(
                        409,
                        "Checkout intent predates frozen customer binding; resolve the existing provider session before retrying",
                    )
                # A previous Session.create may have succeeded while its
                # response was lost. Replay only this frozen request with the
                # same key; Stripe returns its cached result or revalidates
                # the original parameters without granting a new trial.
                recovery_replay = True
                frozen_trial_end = frozen.get("trial_end_unix")
                if frozen_trial_end is not None:
                    frozen_trial_end = int(frozen_trial_end)
                    if frozen_trial_end <= int(now.timestamp()):
                        raise HTTPException(
                            409,
                            "The frozen trial checkout expired; wait for the original trial to end and create a new request key",
                        )
            else:
                unresolved_intent = await conn.fetchrow("""
                    SELECT id FROM billing_checkout_intents
                    WHERE organization_id = $1::uuid
                      AND COALESCE((payload->>'_resolved')::boolean, FALSE) = FALSE
                    ORDER BY created_at DESC
                    LIMIT 1
                    FOR UPDATE
                """, str(org_id))
                if unresolved_intent:
                    raise HTTPException(
                        409,
                        "Another checkout is still active or awaiting webhook resolution; wait before starting a new checkout",
                    )
                checkout_expires_at = now + CHECKOUT_SESSION_LIFETIME
                from src.core.billing.suspension import trial_end_unix_timestamp
                frozen = dict(payload)
                trial_end_unix = None
                if org.get("subscription_status") == "trialing":
                    trial_end_unix = trial_end_unix_timestamp(org.get("trial_end"), now=now)
                if trial_end_unix is not None:
                    if not _trial_lead_is_safe(trial_end_unix, now):
                        raise _active_trial_error(trial_end_unix)
                    checkout_expires_at = min(checkout_expires_at, datetime.fromtimestamp(trial_end_unix, tz=timezone.utc))
                intent_id = uuid.uuid4()
                frozen["trial_end_unix"] = trial_end_unix
                frozen["checkout_intent_id"] = str(intent_id)
                frozen["checkout_session_expires_at_unix"] = int(checkout_expires_at.timestamp())
                # Keep the provider customer fixed for every replay. A missing
                # customer is created with its own stable idempotency key and
                # persisted into this intent before creating a Checkout Session.
                frozen["stripe_customer_id"] = org.get("stripe_customer_id")
                intent = {"id": intent_id, "expires_at": checkout_expires_at}
                intent["payload"] = frozen
                await conn.execute("""
                    INSERT INTO billing_checkout_intents(id, organization_id, request_key, payload_hash, payload, expires_at)
                    VALUES ($1, $2::uuid, $3, $4, $5::jsonb, $6)
                """, intent["id"], str(org_id), request_key, digest, json.dumps(frozen), intent["expires_at"])
                created_intent = True
            customer_id = frozen.get("stripe_customer_id")
            if customer_id and org.get("stripe_customer_id") not in {None, customer_id}:
                raise HTTPException(409, "Checkout intent customer changed; resolve the existing provider session")

    frozen = intent["payload"]
    if isinstance(frozen, str):
        frozen = json.loads(frozen)
    trial_end_unix = frozen.get("trial_end_unix")
    if trial_end_unix is not None:
        try:
            trial_end_unix = int(trial_end_unix)
        except (TypeError, ValueError, OverflowError) as exc:
            raise HTTPException(409, "Checkout intent has an invalid trial snapshot") from exc
        if trial_end_unix <= 0:
            raise HTTPException(409, "Checkout intent has an invalid trial snapshot")
    provider_expires_at = frozen.get("checkout_session_expires_at_unix")
    if provider_expires_at is None:
        provider_expires_at = int(intent["expires_at"].timestamp())
    try:
        provider_expires_at = int(provider_expires_at)
    except (TypeError, ValueError, OverflowError) as exc:
        raise HTTPException(409, "Checkout intent has an invalid session expiry") from exc

    now = _checkout_now(now_provider)
    if provider_expires_at <= now.timestamp():
        if created_intent:
            await _resolve_intent_without_session(repo, org_id, intent["id"])
        raise HTTPException(409, "Checkout expired; create a new request key")
    if (not recovery_replay and
            provider_expires_at - now.timestamp() < CHECKOUT_SESSION_MINIMUM_LEAD.total_seconds()):
        if created_intent:
            await _resolve_intent_without_session(repo, org_id, intent["id"])
        raise HTTPException(409, "Checkout intent is too close to its frozen session expiry; create a new request key")
    if trial_end_unix is not None:
        if trial_end_unix <= int(now.timestamp()):
            if created_intent:
                await _resolve_intent_without_session(repo, org_id, intent["id"])
            raise HTTPException(409, "The original trial expired; create a new request key for paid checkout")
        if not recovery_replay and not _trial_lead_is_safe(trial_end_unix, now):
            if created_intent:
                await _resolve_intent_without_session(repo, org_id, intent["id"])
            raise _active_trial_error(trial_end_unix)

    # Intent is committed BEFORE the network call. Both Stripe requests have
    # stable keys, including recovery after a crash before persisting a response.
    if not customer_id:
        customer = await stripe_call(stripe.Customer.create, metadata={"organization_id": str(org_id)},
                                     idempotency_key=f"customer:{org_id}")
        customer_id = customer.id
        async with repo.pool.acquire() as conn:
            async with conn.transaction():
                result = await conn.fetchval("""
                    UPDATE organizations SET stripe_customer_id = COALESCE(stripe_customer_id, $2)
                    WHERE id = $1::uuid RETURNING stripe_customer_id
                """, str(org_id), customer_id)
                if result != customer_id:
                    raise HTTPException(409, "Stripe customer changed; checkout must be retried")
                bound_customer = await conn.fetchval("""
                    UPDATE billing_checkout_intents
                    SET payload = jsonb_set(payload, '{stripe_customer_id}', to_jsonb($3::text), true)
                    WHERE id = $1::uuid AND organization_id = $2::uuid
                      AND COALESCE((payload->>'_resolved')::boolean, FALSE) = FALSE
                      AND (payload->>'stripe_customer_id' IS NULL OR payload->>'stripe_customer_id' = $3)
                    RETURNING payload->>'stripe_customer_id'
                """, intent["id"], str(org_id), customer_id)
                if bound_customer != customer_id:
                    raise HTTPException(409, "Checkout intent customer binding could not be persisted")
    now = _checkout_now(now_provider)
    if provider_expires_at <= now.timestamp():
        if created_intent:
            await _resolve_intent_without_session(repo, org_id, intent["id"])
        raise HTTPException(409, "Checkout expired; create a new request key")
    if (not recovery_replay and
            provider_expires_at - now.timestamp() < CHECKOUT_SESSION_MINIMUM_LEAD.total_seconds()):
        if created_intent:
            await _resolve_intent_without_session(repo, org_id, intent["id"])
        raise HTTPException(409, "Checkout intent is too close to its frozen session expiry; create a new request key")
    if trial_end_unix is not None:
        if trial_end_unix <= int(now.timestamp()):
            if created_intent:
                await _resolve_intent_without_session(repo, org_id, intent["id"])
            raise HTTPException(409, "The original trial expired; create a new request key for paid checkout")
        if not recovery_replay and not _trial_lead_is_safe(trial_end_unix, now):
            if created_intent:
                await _resolve_intent_without_session(repo, org_id, intent["id"])
            raise _active_trial_error(trial_end_unix)
    # The explicit sentinel prevents a stale future DB deadline from being
    # reused when a paid or expired organization starts a new checkout.
    session_metadata = {
        "trial_end_unix": "none",
        "checkout_intent_id": frozen["checkout_intent_id"],
    }
    if trial_end_unix is not None:
        session_metadata["trial_end_unix"] = str(trial_end_unix)
    subscription_data = {"metadata": {"checkout_intent_id": frozen["checkout_intent_id"]}}
    kwargs = dict(customer=customer_id, line_items=[{"price": frozen["price_id"], "quantity": 1}],
                  mode="subscription", success_url=frozen["success_url"], cancel_url=frozen["cancel_url"],
                  client_reference_id=str(org_id), metadata=session_metadata,
                  payment_method_collection="if_required" if trial_end_unix is not None else "required",
                  expires_at=int(frozen["checkout_session_expires_at_unix"]),
                  idempotency_key=f"checkout:{intent['id']}")
    if trial_end_unix is not None:
        subscription_data.update({
            "trial_end": trial_end_unix,
            "trial_settings": {"end_behavior": {"missing_payment_method": "cancel"}},
        })
    kwargs["subscription_data"] = subscription_data
    try:
        session = await stripe_call(stripe.checkout.Session.create, **kwargs)
    except Exception as exc:
        invalid_request_error = getattr(getattr(stripe, "error", None), "InvalidRequestError", None)
        if isinstance(invalid_request_error, type) and isinstance(exc, invalid_request_error):
            # Stripe did not execute the endpoint (for example, the frozen
            # trial_end now fails the provider's 48-hour validation), so this
            # intent cannot later grant a subscription.
            await _resolve_intent_without_session(repo, org_id, intent["id"])
        raise
    async with repo.pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("""
                UPDATE billing_checkout_intents SET session_id = $3, session_url = $4
                WHERE id = $1 AND organization_id = $2::uuid
            """, intent["id"], str(org_id), session.id, session.url)
            await conn.execute("""
                INSERT INTO audit_log(id, organization_id, action, details)
                VALUES ($1, $2::uuid, 'billing.checkout_session_created', $3::jsonb)
                ON CONFLICT (id) DO NOTHING
            """, intent["id"], str(org_id), json.dumps({"session_id": session.id, "plan": frozen["plan"]}))
    return {"url": session.url}


async def _resolve_intent_without_session(repo, org_id, intent_id):
    """Release an intent if this request proves no Checkout Session was sent."""
    async with repo.pool.acquire() as conn:
        await conn.execute("""
            UPDATE billing_checkout_intents
            SET expires_at = NOW(),
                payload = jsonb_set(payload, '{_resolved}', 'true'::jsonb, true)
            WHERE id = $1::uuid AND organization_id = $2::uuid AND session_id IS NULL
        """, intent_id, str(org_id))
