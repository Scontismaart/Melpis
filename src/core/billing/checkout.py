"""Durable checkout intent: retries reuse the exact provider request."""

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from fastapi import HTTPException


def validate_redirect_url(value: str, base_url: str) -> str:
    target, base = urlsplit(value), urlsplit(base_url)
    if (target.username or target.password or target.fragment or
            (target.scheme, target.netloc) != (base.scheme, base.netloc) or
            target.scheme not in {"https", "http"} or
            (target.scheme == "http" and target.hostname not in {"localhost", "127.0.0.1"})):
        raise HTTPException(400, "Checkout redirect must use the configured application origin")
    return value


async def create_durable_checkout(repo, org_id, request_key, payload, stripe, stripe_call):
    if not request_key or not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", request_key):
        raise HTTPException(400, "A stable Idempotency-Key header (8-128 characters) is required")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    async with repo.pool.acquire() as conn:
        async with conn.transaction():
            org = await conn.fetchrow("SELECT * FROM organizations WHERE id = $1::uuid FOR UPDATE", str(org_id))
            if not org:
                raise HTTPException(404, "Organization not found")
            existing = await conn.fetchrow("""
                SELECT * FROM billing_checkout_intents WHERE organization_id = $1::uuid AND request_key = $2
                FOR UPDATE
            """, str(org_id), request_key)
            if existing:
                if existing["payload_hash"] != digest:
                    raise HTTPException(409, "Idempotency key already used for a different checkout")
                if existing["expires_at"] <= datetime.now(timezone.utc):
                    raise HTTPException(409, "Checkout expired; create a new request key")
                intent = dict(existing)
                frozen = intent["payload"]
                if isinstance(frozen, str):
                    frozen = json.loads(frozen)
                if "trial_end_unix" not in frozen:
                    raise HTTPException(
                        409,
                        "Checkout intent predates exact trial snapshot; wait for the legacy session to expire",
                    )
                if existing["session_url"]:
                    return {"url": existing["session_url"]}
            else:
                now = datetime.now(timezone.utc)
                legacy_session = await conn.fetchrow("""
                    SELECT id, expires_at FROM billing_checkout_intents
                    WHERE organization_id = $1::uuid
                      AND expires_at > $2
                      AND NOT (payload ? 'trial_end_unix')
                    ORDER BY created_at DESC
                    LIMIT 1
                    FOR UPDATE
                """, str(org_id), now)
                if legacy_session:
                    raise HTTPException(
                        409,
                        "A legacy checkout intent is still valid or its result is uncertain; wait for it to expire before starting a new checkout",
                    )
                expires_at = now + timedelta(hours=1)
                from src.core.billing.suspension import trial_end_unix_timestamp
                frozen = dict(payload)
                trial_end_unix = None
                if org.get("subscription_status") == "trialing":
                    trial_end_unix = trial_end_unix_timestamp(org.get("trial_end"), now=now)
                if trial_end_unix is not None:
                    exact_trial_end = datetime.fromtimestamp(trial_end_unix, tz=timezone.utc)
                    if exact_trial_end - now < timedelta(minutes=30):
                        raise HTTPException(
                            409,
                            "The remaining trial is too short for a safe Stripe Checkout session; retry after it ends",
                        )
                    expires_at = min(expires_at, exact_trial_end)
                frozen["trial_end_unix"] = trial_end_unix
                intent = {"id": uuid.uuid4(), "expires_at": expires_at}
                intent["payload"] = frozen
                await conn.execute("""
                    INSERT INTO billing_checkout_intents(id, organization_id, request_key, payload_hash, payload, expires_at)
                    VALUES ($1, $2::uuid, $3, $4, $5::jsonb, $6)
                """, intent["id"], str(org_id), request_key, digest, json.dumps(frozen), intent["expires_at"])
            customer_id = org["stripe_customer_id"]
    # Intent is committed BEFORE the network call. Both Stripe requests have
    # stable keys, including recovery after a crash before persisting a response.
    if not customer_id:
        customer = await stripe_call(stripe.Customer.create, metadata={"organization_id": str(org_id)},
                                     idempotency_key=f"customer:{org_id}")
        customer_id = customer.id
        async with repo.pool.acquire() as conn:
            result = await conn.fetchval("""
                UPDATE organizations SET stripe_customer_id = COALESCE(stripe_customer_id, $2)
                WHERE id = $1::uuid RETURNING stripe_customer_id
            """, str(org_id), customer_id)
            if result != customer_id:
                raise HTTPException(409, "Stripe customer changed; checkout must be retried")
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
    # The explicit sentinel prevents a stale future DB deadline from being
    # reused when a paid or expired organization starts a new checkout.
    session_metadata = {"trial_end_unix": "none"}
    if trial_end_unix is not None:
        session_metadata["trial_end_unix"] = str(trial_end_unix)
    kwargs = dict(customer=customer_id, line_items=[{"price": frozen["price_id"], "quantity": 1}],
                  mode="subscription", success_url=frozen["success_url"], cancel_url=frozen["cancel_url"],
                  client_reference_id=str(org_id), metadata=session_metadata,
                  payment_method_collection="if_required" if trial_end_unix is not None else "required",
                  expires_at=int(intent["expires_at"].timestamp()),
                  idempotency_key=f"checkout:{intent['id']}")
    if trial_end_unix is not None:
        kwargs["subscription_data"] = {
            "trial_end": trial_end_unix,
            "trial_settings": {"end_behavior": {"missing_payment_method": "cancel"}},
        }
    session = await stripe_call(stripe.checkout.Session.create, **kwargs)
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
