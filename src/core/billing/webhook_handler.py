import logging
import json
import os
import uuid
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


def _valid_org_uuid(value) -> bool:
    """True solo se value e' un UUID valido (fail-closed per lo scope org)."""
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True

def _configured_price_to_plan() -> dict[str, str]:
    from src.core.billing.plans import PLANS
    mapping = {}
    annual_enabled = os.getenv("STRIPE_ANNUAL_PRICING_ENABLED", "").strip().lower() == "true"
    for slug, plan in PLANS.items():
        if plan.stripe_price_id:
            mapping[plan.stripe_price_id] = slug
        if annual_enabled and plan.stripe_price_id_yearly:
            mapping[plan.stripe_price_id_yearly] = slug
    return mapping


PRICE_TO_PLAN: dict[str, str] = {}
PRODUCT_TO_PLAN: dict[str, str] = {}


def _init_plan_maps() -> dict[str, str]:
    PRICE_TO_PLAN.clear()
    PRICE_TO_PLAN.update(_configured_price_to_plan())
    return PRICE_TO_PLAN


def _resolve_plan_from_subscription(subscription_data: dict) -> str | None:
    price_to_plan = _configured_price_to_plan()
    for item in subscription_data.get("items", {}).get("data", []):
        price = item.get("price") or {}
        if not isinstance(price, dict):
            continue
        price_id = price.get("id", "")
        if isinstance(price_id, str) and price_id in price_to_plan:
            return price_to_plan[price_id]
    return None


async def handle_stripe_webhook(event: dict, repo, trial_days: int) -> dict | None:
    """Tutto il processing avviene in un'unica transazione DB: dedup INSERT
    e effetti billing sono atomici. Se il processo crasha a meta', la
    transazione fa rollback di tutto — l'evento NON risulta processato."""
    event_type = event.get("type")
    event_id = event.get("id", "")
    # Never mutate the caller's event while applying conservative tie handling.
    import copy
    data_obj = copy.deepcopy(event.get("data", {}).get("object", {}))
    created = event.get("created")
    if not event_id or not isinstance(created, int) or isinstance(created, bool) or created <= 0:
        raise ValueError("Stripe event requires a stable event id and created timestamp")

    async with repo.pool.acquire() as conn:
        async with conn.transaction():
            gate = await _lock_event_scope(conn, event, data_obj)
            if gate is None:
                return None
            if gate.get("ignored"):
                return {"action": "ignored", "reason": gate["ignored"], "organization_id": gate["id"]}
            if event_type in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
                result = await _handle_checkout_completed(conn, repo, data_obj, event_id, trial_days)
            elif event_type == "invoice.paid":
                result = await _handle_invoice_paid(conn, repo, data_obj, event_id)
            elif event_type == "invoice.payment_failed":
                result = await _handle_payment_failed(conn, repo, data_obj, event_id)
            elif event_type == "customer.subscription.updated":
                result = await _handle_subscription_updated(conn, repo, data_obj, event_id)
            elif event_type in ("customer.subscription.deleted", "subscription.deleted"):
                result = await _handle_subscription_deleted(conn, repo, data_obj, event_id)
            else:
                result = None
            if result and result.get("action") != "duplicate":
                org_id = result["organization_id"]
                if data_obj.get("mode") != "payment":
                    await conn.execute("""
                        UPDATE organizations SET stripe_event_created = $2, stripe_event_id = $3
                        WHERE id = $1::uuid
                    """, org_id, created, event_id)
                await conn.execute("""
                    INSERT INTO audit_log (id, organization_id, action, details)
                    VALUES ($1, $2::uuid, $3, $4::jsonb)
                """, uuid.uuid4(), org_id, "billing.webhook." + result["action"],
                    json.dumps({"stripe_event_id": event_id, "stripe_event_type": event_type}))

    if result and result.get("suspension_notice"):
        from src.core.notifications.email_service import enqueue_suspension_notice
        enqueue_suspension_notice(str(result["organization_id"]), repo.pool)

    if result:
        return result

    logger.info("Unhandled event type: %s", event_type)
    return None


async def _lock_event_scope(conn, event, data):
    """Serialize every event for an organization before dedup and state writes.

    Events have second-resolution timestamps, not a reliable total order. Ties
    may only move access toward a more restrictive state; they never reset quota.
    """
    kind = event.get("type", "")
    if kind not in {"checkout.session.completed", "checkout.session.async_payment_succeeded",
                    "invoice.paid", "invoice.payment_failed", "customer.subscription.updated",
                    "customer.subscription.deleted", "subscription.deleted"}:
        return None
    if kind.startswith("checkout.session."):
        if data.get("mode") == "payment":
            org_id = (data.get("metadata") or {}).get("organization_id")
        elif data.get("mode") == "subscription" and data.get("subscription"):
            org_id = data.get("client_reference_id")
        else:
            return None
        if not _valid_org_uuid(org_id):
            return None
        row = await conn.fetchrow("SELECT * FROM organizations WHERE id = $1::uuid FOR UPDATE", org_id)
    else:
        row = await conn.fetchrow("SELECT * FROM organizations WHERE stripe_customer_id = $1 FOR UPDATE", data.get("customer"))
    if row is None:
        return None
    org = dict(row)
    if data.get("mode") == "payment":
        return org
    incoming_sub = data.get("subscription") if kind.startswith(("checkout.", "invoice.")) else data.get("id")
    if not incoming_sub:
        return {**org, "ignored": "subscription_missing"}
    current_sub = org.get("subscription_id")
    if data.get("customer") != org.get("stripe_customer_id") and org.get("stripe_customer_id"):
        return {**org, "ignored": "customer_mismatch"}
    if current_sub and current_sub != incoming_sub:
        if not (kind == "checkout.session.completed" and org.get("subscription_status") == "canceled"):
            return {**org, "ignored": "subscription_mismatch"}
    # A canceled Stripe subscription is terminal; no later snapshot of that
    # same subscription can grant access again.
    # not evidence of a new subscription.
    if current_sub == incoming_sub and org.get("subscription_status") == "canceled" and kind not in {"customer.subscription.deleted", "subscription.deleted"}:
        return {**org, "ignored": "canceled_subscription"}
    previous = org.get("stripe_event_created") or 0
    created = event["created"]
    if created < previous:
        return {**org, "ignored": "stale_event"}
    if created == previous and event["id"] != org.get("stripe_event_id"):
        status = data.get("status") if kind == "customer.subscription.updated" else {
            "invoice.paid": "active", "invoice.payment_failed": "past_due",
            "customer.subscription.deleted": "canceled", "subscription.deleted": "canceled",
            "checkout.session.completed": "trialing",
            "checkout.session.async_payment_succeeded": "trialing",
        }.get(kind)
        severity = {"active": 0, "trialing": 0, "past_due": 1, "incomplete": 2,
                    "incomplete_expired": 3, "unpaid": 3, "paused": 3, "canceled": 4}
        if severity.get(status, 5) <= severity.get(org.get("subscription_status"), 5):
            return {**org, "ignored": "ambiguous_event_order"}
        # Permit only a restrictive status transition, not plan/period changes.
        data.pop("items", None)
        data.pop("current_period_start", None)
        data.pop("current_period_end", None)
    return org


async def _handle_checkout_completed(conn, repo, data, event_id, trial_days):
    mode = data.get("mode")

    if mode == "payment":
        metadata = data.get("metadata") or {}
        booking_id = metadata.get("booking_id")
        org_id = metadata.get("organization_id")
        if booking_id:
            # Fail-closed: senza uno scope organization_id valido NON si
            # tocca il booking (Invariante #1). Skip completo: nemmeno la
            # dedup, cosi' l'evento non risulta consumato.
            if not _valid_org_uuid(org_id):
                logger.warning(
                    "stripe_webhook=checkout_org_scope_invalid event_id=%s "
                    "booking_id=%s", event_id, booking_id,
                )
                return None
            if data.get("payment_status") != "paid" or not _valid_org_uuid(booking_id):
                return None
            booking = await conn.fetchrow("""
                SELECT deposit_amount_minor, deposit_currency, deposit_session_id
                FROM bookings WHERE id = $1::uuid AND organization_id = $2::uuid FOR UPDATE
            """, booking_id, org_id)
            session_id = data.get("id")
            if (not booking or not isinstance(session_id, str) or not session_id
                    or (booking["deposit_session_id"] is not None
                        and booking["deposit_session_id"] != session_id)
                    or booking["deposit_amount_minor"] is None
                    or booking["deposit_amount_minor"] != data.get("amount_total")
                    or booking["deposit_currency"] != str(data.get("currency", "")).lower()):
                logger.error("stripe_deposit_mismatch organization_id=%s event_id=%s", org_id, event_id)
                return None
            if not await repo.process_stripe_event_in_tx(conn, event_id, org_id):
                return {"action": "duplicate", "status": "skipped", "organization_id": org_id}
            await conn.execute("""
                UPDATE bookings SET payment_status = 'paid',
                    deposit_session_id = COALESCE(deposit_session_id, $1), updated_at = NOW()
                WHERE id = $2 AND organization_id = $3
            """, data.get("id"), booking_id, org_id)
            return {"action": "deposit_paid", "booking_id": booking_id, "organization_id": org_id}
        return None

    org_id = data.get("client_reference_id")
    subscription_id = data.get("subscription")
    customer_id = data.get("customer")

    if not org_id or not subscription_id or mode != "subscription":
        return None

    # A signed Checkout event is not sufficient by itself to start a trial:
    # the exact session must be the one persisted from our durable checkout
    # intent, and that intent must contain a currently configured Price ID.
    session_id = data.get("id")
    intent_id = (data.get("metadata") or {}).get("billing_checkout_intent_id")
    intent_row = await conn.fetchrow("""
        SELECT payload FROM billing_checkout_intents
        WHERE id = $1::uuid AND organization_id = $2::uuid AND session_id = $3
        FOR UPDATE
    """, intent_id, org_id, session_id) if _valid_org_uuid(intent_id) and session_id else None
    if not intent_row:
        return None
    payload = intent_row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    expected_price_id = payload.get("price_id") if isinstance(payload, dict) else None
    if not expected_price_id or expected_price_id not in _configured_price_to_plan():
        return None
    if data.get("customer") != (await conn.fetchval(
        "SELECT stripe_customer_id FROM organizations WHERE id = $1::uuid", org_id
    )):
        return None

    if not await repo.process_stripe_event_in_tx(conn, event_id, org_id):
        return {"action": "duplicate", "status": "skipped", "organization_id": org_id}

    now = datetime.now(timezone.utc)
    # Il trial NON si rinnova al checkout: si eredita quello del signup.
    # Le sessioni senza intent durevole sono ignorate sopra, senza fallback.
    from src.core.billing.suspension import giorni_trial_rimanenti
    riga_org = await conn.fetchrow("SELECT trial_end FROM organizations WHERE id = $1", org_id)
    trial_days_residui = giorni_trial_rimanenti(riga_org["trial_end"] if riga_org else None, now=now)
    nuovo_trial_end = now + timedelta(days=trial_days_residui)
    # Nota: trial_days_residui = 0 (trial signup esaurito) scrive
    # trial_end = now: l'org resta brevemente "trialing scaduto" (sospesa)
    # fino all'invoice.paid immediatamente a seguire. Finestra accettata.
    await conn.execute("""
        UPDATE organizations SET
            stripe_customer_id = $1, subscription_id = $2,
            subscription_status = 'trialing',
            trial_start = $3, trial_end = $4,
            current_period_start = $3, current_period_end = $4,
            suspension_notified_at = NULL
        WHERE id = $5
    """, customer_id, subscription_id, now, nuovo_trial_end, org_id)
    return {"action": "subscription_created", "status": "trialing", "organization_id": org_id}


async def _lookup_org_by_customer(customer_id: str, repo, conn) -> dict | None:
    if not customer_id:
        return None
    row = await conn.fetchrow(
        "SELECT id, stripe_customer_id, subscription_status, plan, "
        "current_period_start, messages_used_this_period "
        "FROM organizations WHERE stripe_customer_id = $1",
        customer_id,
    )
    return dict(row) if row else None


async def _handle_invoice_paid(conn, repo, data, event_id):
    if data.get("status") != "paid":
        return None
    customer_id = data.get("customer")
    org = await _lookup_org_by_customer(customer_id, repo, conn)
    if not org:
        return None

    if not await repo.process_stripe_event_in_tx(conn, event_id, org["id"]):
        return {"action": "duplicate", "status": "skipped", "organization_id": org["id"]}

    period_start = datetime.fromtimestamp(data.get("period_start", 0), tz=timezone.utc)
    period_end = datetime.fromtimestamp(data.get("period_end", 0), tz=timezone.utc)

    plan_slug = _resolve_plan_from_subscription({"items": data.get("lines") or {}})
    if not plan_slug:
        # Paid invoice without an allowlisted configured Price ID is audited
        # below but cannot activate access, change plan, or reset quota.
        return {"action": "invoice_ignored_unconfigured_price", "organization_id": org["id"]}

    await conn.execute(
        "UPDATE organizations SET subscription_id = COALESCE(subscription_id, $2), subscription_status = 'active', suspension_notified_at = NULL WHERE id = $1",
        org["id"], data.get("subscription"),
    )
    if period_end > period_start and (org.get("current_period_start") is None or period_start > org["current_period_start"]):
        await conn.execute("""
            UPDATE organizations SET
                messages_used_this_period = 0,
                current_period_start = $1,
                current_period_end = $2
            WHERE id = $3
        """, period_start, period_end, org["id"])

    if plan_slug:
        from src.core.billing.plans import get_plan
        plan = get_plan(plan_slug)
        await conn.execute("""
            UPDATE organizations SET
                plan = $1, messages_limit = $2, users_limit = $3,
                whatsapp_numbers_limit = $4
            WHERE id = $5
        """, plan_slug, plan.messages_limit, plan.users_limit, plan.whatsapp_numbers_limit, org["id"])

    return {"action": "subscription_activated", "status": "active", "organization_id": org["id"]}


async def _handle_payment_failed(conn, repo, data, event_id):
    customer_id = data.get("customer")
    org = await _lookup_org_by_customer(customer_id, repo, conn)
    if not org:
        return None

    if not await repo.process_stripe_event_in_tx(conn, event_id, org["id"]):
        return {"action": "duplicate", "status": "skipped", "organization_id": org["id"]}

    await conn.execute(
        "UPDATE organizations SET subscription_status = 'past_due' WHERE id = $1",
        org["id"],
    )
    return {"action": "payment_failed", "status": "past_due", "organization_id": org["id"]}


async def _handle_subscription_updated(conn, repo, data, event_id):
    customer_id = data.get("customer")
    org = await _lookup_org_by_customer(customer_id, repo, conn)
    if not org:
        return None

    if not await repo.process_stripe_event_in_tx(conn, event_id, org["id"]):
        return {"action": "duplicate", "status": "skipped", "organization_id": org["id"]}

    plan_slug = _resolve_plan_from_subscription(data)
    if plan_slug:
        from src.core.billing.plans import get_plan
        plan = get_plan(plan_slug)
        await conn.execute("""
            UPDATE organizations SET
                plan = $1, messages_limit = $2, users_limit = $3,
                whatsapp_numbers_limit = $4
            WHERE id = $5
        """, plan_slug, plan.messages_limit, plan.users_limit, plan.whatsapp_numbers_limit, org["id"])

    canonical = {"active", "trialing", "past_due", "unpaid", "incomplete", "incomplete_expired", "canceled", "paused"}
    status = data.get("status")
    if status not in canonical:
        status = "incomplete"  # Unknown upstream state is never entitlement.
    # An unknown/unconfigured Price ID can never grant access or reset quota.
    # Restrictive lifecycle statuses are still applied immediately.
    if not plan_slug and status in {"active", "trialing"}:
        return {"action": "subscription_ignored_unconfigured_price", "organization_id": org["id"]}
    trial_end = data.get("trial_end")
    await conn.execute("""
        UPDATE organizations SET subscription_status = $1,
            trial_end = CASE WHEN $1 = 'trialing' THEN $3 ELSE trial_end END,
            suspension_notified_at = CASE WHEN $1 IN ('active','trialing') THEN NULL ELSE suspension_notified_at END
        WHERE id = $2
    """, status, org["id"], datetime.fromtimestamp(trial_end, tz=timezone.utc) if trial_end else None)

    period_start = data.get("current_period_start")
    period_end = data.get("current_period_end")
    if plan_slug and period_start and period_end:
        new_start = datetime.fromtimestamp(period_start, tz=timezone.utc)
        if period_end > period_start and (org.get("current_period_start") is None or new_start > org["current_period_start"]):
            await conn.execute("""
                UPDATE organizations SET
                    messages_used_this_period = 0,
                    current_period_start = $1,
                    current_period_end = $2
                WHERE id = $3
            """, new_start, datetime.fromtimestamp(period_end, tz=timezone.utc), org["id"])

    return {"action": "subscription_updated", "plan": plan_slug, "organization_id": org["id"]}


async def _handle_subscription_deleted(conn, repo, data, event_id):
    customer_id = data.get("customer")
    org = await _lookup_org_by_customer(customer_id, repo, conn)
    if not org:
        return None

    if not await repo.process_stripe_event_in_tx(conn, event_id, org["id"]):
        return {"action": "duplicate", "status": "skipped", "organization_id": org["id"]}

    # Claim atomico della notifica: solo chi trova suspension_notified_at IS
    # NULL la imposta e segnala la mail. Se un altro trigger (job trial) e'
    # passato prima, RETURNING non restituisce nulla e non inviamo doppioni.
    await conn.execute("UPDATE organizations SET subscription_status = 'canceled' WHERE id = $1", org["id"])
    claimed = await conn.fetchrow("""
        UPDATE organizations SET
            subscription_status = 'canceled',
            suspension_notified_at = NOW()
        WHERE id = $1 AND suspension_notified_at IS NULL
        RETURNING id
    """, org["id"])
    return {
        "action": "subscription_deleted",
        "status": "canceled",
        "organization_id": org["id"],
        "suspension_notice": claimed is not None,
    }
