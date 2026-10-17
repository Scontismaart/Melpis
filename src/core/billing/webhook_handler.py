import logging
import json
import uuid
from datetime import datetime, timezone

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

PRICE_TO_PLAN: dict[str, str] = {}
PRODUCT_TO_PLAN: dict[str, str] = {}


def _init_plan_maps():
    from src.core.billing.plans import PLANS
    for slug, plan in PLANS.items():
        if plan.stripe_price_id:
            PRICE_TO_PLAN[plan.stripe_price_id] = slug
        if plan.stripe_price_id_yearly:
            PRICE_TO_PLAN[plan.stripe_price_id_yearly] = slug
        PRODUCT_TO_PLAN[f"prod_{slug}"] = slug


def _resolve_plan_from_subscription(subscription_data: dict) -> str | None:
    if not PRICE_TO_PLAN and not PRODUCT_TO_PLAN:
        _init_plan_maps()
    for item in subscription_data.get("items", {}).get("data", []):
        price = item.get("price") or {}
        if not isinstance(price, dict):
            continue
        price_id = price.get("id", "")
        if price_id in PRICE_TO_PLAN:
            return PRICE_TO_PLAN[price_id]
        product_id = price.get("product") or (item.get("plan") or {}).get("product", "")
        if product_id in PRODUCT_TO_PLAN:
            return PRODUCT_TO_PLAN[product_id]
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
            elif event_type == "checkout.session.expired":
                result = await _handle_checkout_expired(conn, repo, data_obj, event_id)
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
    if kind not in {"checkout.session.completed", "checkout.session.async_payment_succeeded", "checkout.session.expired",
                    "invoice.paid", "invoice.payment_failed", "customer.subscription.updated",
                    "customer.subscription.deleted", "subscription.deleted"}:
        return None
    if kind.startswith("checkout.session."):
        if data.get("mode") == "payment":
            org_id = (data.get("metadata") or {}).get("organization_id")
        elif data.get("mode") == "subscription" and (
            data.get("subscription") or kind == "checkout.session.expired"
        ):
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
    if kind == "checkout.session.expired":
        if data.get("customer") and data.get("customer") != org.get("stripe_customer_id"):
            return {**org, "ignored": "customer_mismatch"}
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
    if not current_sub and not kind.startswith("checkout.session."):
        await conn.execute("UPDATE organizations SET subscription_id = $2 WHERE id = $1", org["id"], incoming_sub)
    return org


async def _checkout_intent_for_session(conn, org_id, data):
    """Resolve a signed Checkout event to the durable intent that created it."""
    session_id = data.get("id")
    metadata = data.get("metadata") or {}
    if not isinstance(session_id, str) or not session_id or not isinstance(metadata, dict):
        return None

    intent_id = metadata.get("checkout_intent_id")
    if intent_id:
        if not _valid_org_uuid(str(org_id)) or not _valid_org_uuid(intent_id):
            return None
        intent = await conn.fetchrow("""
            SELECT id, organization_id, session_id, payload
            FROM billing_checkout_intents
            WHERE id = $1::uuid AND organization_id = $2::uuid
            FOR UPDATE
        """, intent_id, str(org_id))
    else:
        # Pre-binding sessions are accepted only when their provider ID was
        # already persisted against this organization.
        intent = await conn.fetchrow("""
            SELECT id, organization_id, session_id, payload
            FROM billing_checkout_intents
            WHERE organization_id = $1::uuid AND session_id = $2
            ORDER BY created_at DESC LIMIT 1 FOR UPDATE
        """, str(org_id), session_id)
    if not intent:
        return None

    frozen = intent["payload"]
    if isinstance(frozen, str):
        try:
            frozen = json.loads(frozen)
        except (TypeError, ValueError):
            return None
    if not isinstance(frozen, dict):
        return None
    if intent["session_id"] and intent["session_id"] != session_id:
        return None
    if ("stripe_customer_id" in frozen
            and frozen["stripe_customer_id"] != data.get("customer")):
        return None
    if intent_id and frozen.get("checkout_intent_id") != intent_id:
        return None

    if "trial_end_unix" in frozen:
        expected_trial_end = frozen.get("trial_end_unix")
        expected_metadata = "none" if expected_trial_end is None else str(int(expected_trial_end))
        if metadata.get("trial_end_unix") != expected_metadata:
            return None
    elif intent_id or "trial_end_unix" in metadata:
        return None
    return {**dict(intent), "payload": frozen}


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
    if not _valid_org_uuid(org_id):
        return None
    intent = await _checkout_intent_for_session(conn, org_id, data)
    if not intent:
        logger.warning("stripe_checkout_intent_mismatch organization_id=%s event_id=%s", org_id, event_id)
        return None

    if not await repo.process_stripe_event_in_tx(conn, event_id, org_id):
        return {"action": "duplicate", "status": "skipped", "organization_id": org_id}

    now = datetime.now(timezone.utc)
    # Preserve the signup deadline at second precision. Session metadata is
    # only a snapshot and must be bounded by the canonical DB deadline; it
    # cannot create or extend a trial on its own. Legacy sessions fall back to
    # the exact DB deadline without reconstructing rounded whole days.
    metadata = data.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    trial_end_unix = metadata.get("trial_end_unix")
    if trial_end_unix == "none":
        nuovo_trial_end = now
    else:
        riga_org = await conn.fetchrow("SELECT trial_end FROM organizations WHERE id = $1", org_id)
        original_trial_end = riga_org["trial_end"] if riga_org else None
        if isinstance(original_trial_end, datetime) and original_trial_end.tzinfo is None:
            original_trial_end = original_trial_end.replace(tzinfo=timezone.utc)

        if isinstance(trial_end_unix, str) and trial_end_unix.isdigit() and original_trial_end:
            try:
                requested_trial_end = int(trial_end_unix)
                original_trial_end_unix = int(original_trial_end.timestamp())
                bounded_trial_end = min(requested_trial_end, original_trial_end_unix)
                nuovo_trial_end = (
                    datetime.fromtimestamp(bounded_trial_end, tz=timezone.utc)
                    if bounded_trial_end > 0
                    else now
                )
            except (OverflowError, OSError, ValueError):
                nuovo_trial_end = original_trial_end
        else:
            # Missing/malformed metadata is treated as a legacy checkout.
            nuovo_trial_end = original_trial_end or now
    await conn.execute("""
        UPDATE organizations SET
            stripe_customer_id = $1, subscription_id = $2,
            subscription_status = 'trialing',
            trial_start = $3, trial_end = $4,
            current_period_start = $3, current_period_end = $4,
            suspension_notified_at = NULL
        WHERE id = $5
    """, customer_id, subscription_id, now, nuovo_trial_end, org_id)
    await conn.execute("""
        UPDATE billing_checkout_intents
        SET session_id = COALESCE(session_id, $2), expires_at = NOW(),
            payload = jsonb_set(payload, '{_resolved}', 'true'::jsonb, true)
        WHERE id = $1::uuid AND organization_id = $3::uuid
          AND (session_id IS NULL OR session_id = $2)
    """, intent["id"], data["id"], str(org_id))
    return {"action": "subscription_created", "status": "trialing", "organization_id": org_id}


async def _handle_checkout_expired(conn, repo, data, event_id):
    org_id = data.get("client_reference_id")
    if data.get("mode") != "subscription" or not _valid_org_uuid(org_id):
        return None
    intent = await _checkout_intent_for_session(conn, org_id, data)
    if not intent:
        logger.warning("stripe_checkout_expired_intent_mismatch organization_id=%s event_id=%s", org_id, event_id)
        return None
    if not await repo.process_stripe_event_in_tx(conn, event_id, org_id):
        return {"action": "duplicate", "status": "skipped", "organization_id": org_id}
    await conn.execute("""
        UPDATE billing_checkout_intents
        SET session_id = COALESCE(session_id, $2), expires_at = NOW(),
            payload = jsonb_set(payload, '{_resolved}', 'true'::jsonb, true)
        WHERE id = $1::uuid AND organization_id = $3::uuid
          AND (session_id IS NULL OR session_id = $2)
    """, intent["id"], data["id"], str(org_id))
    return {"action": "checkout_expired", "organization_id": org_id}


async def _lookup_org_by_customer(customer_id: str, repo, conn) -> dict | None:
    if not customer_id:
        return None
    row = await conn.fetchrow(
        "SELECT id, stripe_customer_id, subscription_id, subscription_status, trial_end, plan, "
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

    await conn.execute(
        "UPDATE organizations SET subscription_status = 'active', suspension_notified_at = NULL WHERE id = $1",
        org["id"],
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
    trial_end = data.get("trial_end")
    trial_end_to_store = None
    if status == "trialing":
        canonical_trial_end = org.get("trial_end")
        if isinstance(canonical_trial_end, datetime) and canonical_trial_end.tzinfo is None:
            canonical_trial_end = canonical_trial_end.replace(tzinfo=timezone.utc)
        if not isinstance(canonical_trial_end, datetime):
            # A provider event alone cannot create an application trial.
            status = "incomplete"
        else:
            provider_trial_end = None
            if isinstance(trial_end, int) and not isinstance(trial_end, bool) and trial_end > 0:
                try:
                    provider_trial_end = datetime.fromtimestamp(trial_end, tz=timezone.utc)
                except (OverflowError, OSError, ValueError):
                    provider_trial_end = None
            trial_end_to_store = (
                min(provider_trial_end, canonical_trial_end)
                if provider_trial_end is not None
                else canonical_trial_end
            )
    await conn.execute("""
        UPDATE organizations SET subscription_status = $1,
            trial_end = CASE WHEN $1 = 'trialing' THEN $3 ELSE trial_end END,
            suspension_notified_at = CASE WHEN $1 IN ('active','trialing') THEN NULL ELSE suspension_notified_at END
        WHERE id = $2
    """, status, org["id"], trial_end_to_store)

    period_start = data.get("current_period_start")
    period_end = data.get("current_period_end")
    if period_start and period_end:
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
