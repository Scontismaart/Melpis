import logging
import json
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException

logger = logging.getLogger(__name__)
_NON_SUBSCRIPTION_INVOICE = object()


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
    """Accept one complete, uniquely configured monthly EUR service item."""
    from src.core.billing.plans import PLANS

    items = subscription_data.get("items")
    if not isinstance(items, dict) or items.get("has_more") is not False:
        return None
    data = items.get("data")
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        return None
    item = data[0]
    if type(item.get("quantity")) is not int or item["quantity"] != 1:
        return None
    price = item.get("price")
    if not isinstance(price, dict) or not price.get("id"):
        return None
    matches = [(slug, plan) for slug, plan in PLANS.items()
               if plan.stripe_price_id and plan.stripe_price_id == price["id"]]
    if len(matches) != 1:
        return None
    slug, plan = matches[0]
    recurring = price.get("recurring")
    if (not isinstance(recurring, dict) or recurring.get("interval") != "month"
            or type(recurring.get("interval_count")) is not int
            or recurring["interval_count"] != 1 or price.get("currency") != "eur"
            or type(price.get("unit_amount")) is not int
            or price["unit_amount"] != plan.price_monthly_eur * 100):
        return None
    return slug


def _subscription_period(data):
    values = [data.get("current_period_start"), data.get("current_period_end")]
    if any(type(value) is not int or value <= 0 for value in values) or values[1] <= values[0]:
        return None
    try:
        return tuple(datetime.fromtimestamp(value, tz=timezone.utc) for value in values)
    except (ValueError, OverflowError, OSError):
        return None


def _invoice_alias(paths):
    """Return one invoice alias, rejecting snapshots that disagree.

    ``paths`` is a sequence of (container, key) pairs. Presence matters: an
    explicit null alongside a populated alias is ambiguous and must be
    reconciled by Stripe rather than guessed locally.
    """
    present = [container[key] for container, key in paths
               if isinstance(container, dict) and key in container]
    if len(present) > 1 and any(value != present[0] for value in present[1:]):
        raise HTTPException(503, "Stripe invoice aliases require reconciliation")
    return present[0] if present else None


def _normalize_invoice_snapshot(data):
    """Normalize supported Stripe invoice API versions before any DB checks.

    Stripe's older invoice snapshot puts subscription and price details at
    the top level of each invoice line. Newer snapshots nest the subscription
    under ``parent.subscription_details`` and line price/subscription details
    under ``pricing.price_details`` and ``parent.subscription_item_details``.
    Only signed subscription metadata is copied into the canonical intent
    field; invoice-level metadata is never an authorization source.
    """
    if not isinstance(data, dict):
        raise HTTPException(503, "Stripe invoice requires reconciliation")

    subscription_details = data.get("subscription_details")
    parent = data.get("parent")
    parent_subscription = parent.get("subscription_details") if isinstance(parent, dict) else None
    parent_type = parent.get("type") if isinstance(parent, dict) else None
    if not isinstance(subscription_details, dict):
        subscription_details = {}
    if not isinstance(parent_subscription, dict):
        parent_subscription = {}
    legacy_metadata = subscription_details.get("metadata")
    legacy_subscription_present = (
        (isinstance(data.get("subscription"), str) and bool(data["subscription"].strip()))
        or (isinstance(subscription_details.get("subscription"), str)
            and bool(subscription_details["subscription"].strip()))
        or (isinstance(legacy_metadata, dict) and bool(legacy_metadata.get("checkout_intent_id")))
    )
    if parent_type == "subscription_details":
        nested_subscription = parent_subscription.get("subscription")
        if not isinstance(nested_subscription, str) or not nested_subscription.strip():
            raise HTTPException(503, "Stripe invoice subscription requires reconciliation")
    elif parent_type is not None and (
        "subscription_details" in (parent if isinstance(parent, dict) else {})
        or legacy_subscription_present
    ):
        raise HTTPException(503, "Stripe invoice parent type requires reconciliation")
    subscription_id = _invoice_alias((
        (data, "subscription"),
        (subscription_details, "subscription"),
        (parent_subscription, "subscription"),
    ))
    metadata_paths = [
        (subscription_details, "metadata"),
        (parent_subscription, "metadata"),
    ]
    supplied_metadata = [(container[key]) for container, key in metadata_paths
                         if key in container]
    if len(supplied_metadata) > 1:
        intent_values = [
            metadata.get("checkout_intent_id") if isinstance(metadata, dict) else None
            for metadata in supplied_metadata
        ]
        if any(value != intent_values[0] for value in intent_values[1:]):
            raise HTTPException(503, "Stripe invoice intent metadata requires reconciliation")
    metadata = supplied_metadata[0] if supplied_metadata else None
    if metadata is not None and not isinstance(metadata, dict):
        raise HTTPException(503, "Stripe invoice intent metadata requires reconciliation")
    intent_marker = metadata.get("checkout_intent_id") if isinstance(metadata, dict) else None
    subscription_declared = (
        parent_type == "subscription_details"
        or (isinstance(subscription_id, str) and bool(subscription_id.strip()))
        or bool(intent_marker)
    )
    if subscription_declared and (
        not isinstance(subscription_id, str) or not subscription_id.strip()
    ):
        raise HTTPException(503, "Stripe invoice subscription requires reconciliation")

    # Store the canonical subscription and metadata only after checking every
    # supplied alias. These are the values consumed by ordering and binding.
    data["subscription"] = subscription_id
    data["subscription_details"] = {"metadata": metadata} if metadata is not None else {}

    lines = data.get("lines")
    if not isinstance(lines, dict) or not isinstance(lines.get("data"), list):
        return data
    normalized_lines = []
    saw_subscription_line = False
    all_lines_are_invoice_items = bool(lines["data"])
    for line in lines["data"]:
        if not isinstance(line, dict):
            normalized_lines.append(line)
            all_lines_are_invoice_items = False
            continue
        line_parent = line.get("parent")
        item_details = (line_parent.get("subscription_item_details")
                        if isinstance(line_parent, dict) else None)
        if not isinstance(item_details, dict):
            item_details = {}

        line_subscription = _invoice_alias((
            (line, "subscription"),
            (item_details, "subscription"),
        ))
        legacy_price = line.get("price") if "price" in line else None
        if legacy_price is not None and not isinstance(legacy_price, dict):
            raise HTTPException(503, "Stripe invoice price requires reconciliation")
        legacy_price_id = legacy_price.get("id") if isinstance(legacy_price, dict) else None
        pricing = line.get("pricing")
        price_details = pricing.get("price_details") if isinstance(pricing, dict) else None
        if (isinstance(pricing, dict) and "type" in pricing
                and pricing.get("type") != "price_details"):
            raise HTTPException(503, "Stripe invoice pricing type requires reconciliation")
        current_price = price_details.get("price") if isinstance(price_details, dict) else None
        if isinstance(current_price, dict):
            current_price = current_price.get("id")
        price_aliases = []
        if "price" in line:
            if isinstance(legacy_price, dict) and "id" in legacy_price:
                price_aliases.append((legacy_price, "id"))
            else:
                price_aliases.append(({"price": legacy_price_id}, "price"))
        if isinstance(price_details, dict) and "price" in price_details:
            price_aliases.append(({"price": current_price}, "price"))
        price_id = _invoice_alias(price_aliases)

        legacy_type_present = "type" in line
        current_type_present = isinstance(line_parent, dict) and "type" in line_parent
        legacy_type = line.get("type")
        current_type = line_parent.get("type") if isinstance(line_parent, dict) else None
        legacy_kind = {"subscription": "subscription", "invoiceitem": "invoice_item"}.get(legacy_type)
        current_kind = {
            "subscription_item_details": "subscription",
            "invoice_item_details": "invoice_item",
        }.get(current_type)
        if legacy_type_present and legacy_kind is None:
            raise HTTPException(503, "Stripe invoice line type requires reconciliation")
        if current_type_present and current_kind is None:
            raise HTTPException(503, "Stripe invoice line type requires reconciliation")
        if legacy_kind and current_kind and legacy_kind != current_kind:
            raise HTTPException(503, "Stripe invoice line aliases require reconciliation")
        line_kind = legacy_kind or current_kind
        if line_kind == "invoice_item" and line_subscription:
            raise HTTPException(503, "Stripe invoice line subscription requires reconciliation")
        is_subscription_line = line_kind == "subscription" or bool(line_subscription)
        if is_subscription_line:
            if line_subscription != subscription_id:
                raise HTTPException(503, "Stripe invoice line subscription requires reconciliation")
            saw_subscription_line = True
            all_lines_are_invoice_items = False
        elif line_kind != "invoice_item":
            all_lines_are_invoice_items = False

        legacy_proration = line.get("proration") if "proration" in line else None
        current_proration = item_details.get("proration") if "proration" in item_details else None
        proration = _invoice_alias((
            ({"value": legacy_proration} if "proration" in line else {}, "value"),
            ({"value": current_proration} if "proration" in item_details else {}, "value"),
        ))
        current_format = isinstance(price_details, dict) or bool(item_details)
        if isinstance(legacy_price, dict):
            canonical_price = dict(legacy_price)
            if price_id is not None:
                canonical_price["id"] = price_id
        elif price_id is not None:
            canonical_price = {"id": price_id}
            if current_format:
                canonical_price["_catalog_monthly_snapshot"] = True
        else:
            canonical_price = {}

        normalized = dict(line)
        normalized["type"] = (
            "subscription" if is_subscription_line
            else "invoiceitem" if line_kind == "invoice_item" else None
        )
        normalized["subscription"] = line_subscription
        normalized["price"] = canonical_price
        normalized["proration"] = proration
        normalized_lines.append(normalized)
    data["lines"] = {**lines, "data": normalized_lines}
    if not subscription_declared and not saw_subscription_line and all_lines_are_invoice_items:
        return _NON_SUBSCRIPTION_INVOICE
    return data


def _validated_subscription_state(org, data):
    """Snapshots may preserve verified rights or restrict them, never grant."""
    status = data.get("status")
    severity = {"active": 0, "trialing": 0, "past_due": 1, "incomplete": 2,
                "incomplete_expired": 3, "unpaid": 3, "paused": 3, "canceled": 4}
    if status not in severity:
        status = "incomplete"
    previous = org.get("subscription_status")
    if status not in {"active", "trialing"}:
        # In particular, unpaid/paused must not relax into legacy past_due.
        if severity.get(previous, 5) > severity[status]:
            status = previous if previous in severity else "incomplete"
        return status, None
    plan = _resolve_plan_from_subscription(data)
    period = _subscription_period(data)
    if plan is None or period is None:
        raise HTTPException(503, "Stripe subscription catalog requires reconciliation")
    if status == "active":
        if (previous != "active" or org.get("plan") != plan
                or period != (org.get("current_period_start"), org.get("current_period_end"))):
            raise HTTPException(503, "Stripe paid period requires invoice reconciliation")
        return status, None
    canonical_end = org.get("trial_end")
    if previous != "trialing" or not isinstance(canonical_end, datetime):
        raise HTTPException(503, "Stripe trial requires original application entitlement")
    if canonical_end.tzinfo is None:
        canonical_end = canonical_end.replace(tzinfo=timezone.utc)
    provider_end = data.get("trial_end")
    if type(provider_end) is not int or provider_end <= 0:
        raise HTTPException(503, "Stripe trial deadline requires reconciliation")
    try:
        provider_end = datetime.fromtimestamp(provider_end, tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        raise HTTPException(503, "Stripe trial deadline requires reconciliation") from None
    return status, min(canonical_end, provider_end)


async def handle_stripe_webhook(event: dict, repo, trial_days: int) -> dict | None:
    """Tutto il processing avviene in un'unica transazione DB: dedup INSERT
    e effetti billing sono atomici. Se il processo crasha a meta', la
    transazione fa rollback di tutto — l'evento NON risulta processato."""
    event_type = event.get("type")
    event_id = event.get("id", "")
    # Never mutate the caller's event while applying conservative tie handling.
    import copy
    data_obj = copy.deepcopy(event.get("data", {}).get("object", {}))
    if event_type in {"invoice.paid", "invoice.payment_failed"}:
        data_obj = _normalize_invoice_snapshot(data_obj)
    created = event.get("created")
    if not event_id or not isinstance(created, int) or isinstance(created, bool) or created <= 0:
        raise ValueError("Stripe event requires a stable event id and created timestamp")
    if data_obj is _NON_SUBSCRIPTION_INVOICE:
        return {"action": "ignored", "reason": "non_subscription_invoice"}

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
                result = await _handle_invoice_paid(conn, repo, data_obj, event_id, scope=gate)
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
                if result["action"] in {
                    "subscription_activated", "payment_failed", "subscription_updated", "subscription_deleted",
                }:
                    await conn.execute("""
                        UPDATE organizations SET stripe_event_created = $2, stripe_event_id = $3
                        WHERE id = $1::uuid
                    """, org_id, created, event_id)
                await conn.execute("""
                    INSERT INTO audit_log (id, organization_id, action, details)
                    VALUES ($1, $2::uuid, $3, $4::jsonb)
                """, uuid.uuid4(), org_id, "billing.webhook." + result["action"],
                    json.dumps({
                        "stripe_event_id": event_id,
                        "stripe_event_type": event_type,
                        "stripe_object_id": data_obj.get("id"),
                        "subscription_id": (
                            data_obj.get("subscription")
                            if event_type.startswith(("checkout.", "invoice.")) else data_obj.get("id")
                        ),
                    }))

    if result and result.get("suspension_notice"):
        from src.core.notifications.email_service import enqueue_suspension_notice
        enqueue_suspension_notice(str(result["organization_id"]), repo.pool)

    if result:
        return result

    logger.info("Unhandled event type: %s", event_type)
    return None


async def _lock_event_scope(conn, event, data):
    """Serialize every event for an organization before dedup and state writes.

    Checkout lifecycle is independent of entitlement event ordering. Equal
    timestamps need object-specific evidence, not event-ID lexical ordering.
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
    if kind.startswith("checkout.session."):
        if current_sub and current_sub != incoming_sub:
            if (org.get("subscription_status") != "canceled"
                    or event["created"] <= (org.get("stripe_event_created") or 0)):
                return {**org, "ignored": "subscription_mismatch"}
        # A delayed completion still resolves the intent, but cannot change an
        # already bound subscription's entitlement (including a canceled one).
        return org
    if kind == "invoice.paid" and data.get("status") != "paid":
        return {**org, "ignored": "invoice_not_paid"}
    if kind == "invoice.payment_failed" and _invoice_service_line(data) is None:
        raise HTTPException(503, "Stripe failed invoice service line requires reconciliation")
    if current_sub and current_sub != incoming_sub:
        if not (kind == "invoice.paid" and org.get("subscription_status") == "canceled"):
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
    if kind == "invoice.paid":
        intent = await _invoice_intent_for_subscription(conn, org, data)
        if intent is not None:
            org["_invoice_intent"] = intent
            if (current_sub in {None, incoming_sub}
                    and org.get("subscription_status") in {"trialing", "incomplete"}
                    and intent["payload"].get("_paid_period_initialized") is not True
                    and not await _subscription_has_paid_history(conn, org, incoming_sub)):
                org["_initial_paid_period"] = True
    if kind.startswith("invoice."):
        period = _invoice_period(data)
        current_start = org.get("current_period_start")
        if (period and isinstance(current_start, datetime) and period[0] < current_start
                and not org.get("_initial_paid_period")):
            return {**org, "ignored": "old_billing_period"}
    if not current_sub or current_sub != incoming_sub:
        if kind == "invoice.paid":
            intent = await _invoice_intent_for_subscription(conn, org, data)
            if intent is None or (current_sub and created <= previous):
                raise HTTPException(503, "Stripe subscription binding requires reconciliation")
            org["_invoice_intent"] = intent
        elif kind in {"customer.subscription.deleted", "subscription.deleted"}:
            intent = await _invoice_intent_for_subscription(conn, org, {
                "customer": data.get("customer"),
                "subscription": incoming_sub,
                "subscription_details": {"metadata": data.get("metadata")},
                "lines": data.get("items"),
            })
            if intent is None:
                raise HTTPException(503, "Stripe cancellation binding requires reconciliation")
        else:
            # Never adopt an unknown subscription before its checkout or a
            # paid invoice proves a durable authorized intent. Retry delivery.
            raise HTTPException(503, "Stripe subscription binding pending checkout")
    if kind == "customer.subscription.updated":
        # Validate before dedup/cursor changes and classify ties by effective state.
        status, trial_end = _validated_subscription_state(org, data)
        data["status"] = status
        org["_trial_deadline_restricted"] = (
            status == "trialing" and trial_end < org["trial_end"]
        )
    if kind == "invoice.payment_failed" and await _invoice_payment_recorded(conn, org, data):
        return {**org, "ignored": "invoice_already_paid"}
    if created == previous and event["id"] != org.get("stripe_event_id"):
        if kind == "invoice.paid":
            if org.get("subscription_status") in {"active", "trialing", "incomplete"}:
                return org
            if (org.get("subscription_status") == "past_due"
                    and await _same_invoice_failed_at_cursor(conn, org, data)):
                return org
            raise HTTPException(503, "Stripe payment ordering requires reconciliation")
        status = data.get("status") if kind == "customer.subscription.updated" else {
            "invoice.paid": "active", "invoice.payment_failed": "past_due",
            "customer.subscription.deleted": "canceled", "subscription.deleted": "canceled",
            "checkout.session.completed": "trialing",
            "checkout.session.async_payment_succeeded": "trialing",
        }.get(kind)
        severity = {"active": 0, "trialing": 0, "past_due": 1, "incomplete": 2,
                    "incomplete_expired": 3, "unpaid": 3, "paused": 3, "canceled": 4}
        if (severity.get(status, 5) <= severity.get(org.get("subscription_status"), 5)
                and not org.get("_trial_deadline_restricted")):
            return {**org, "ignored": "ambiguous_event_order"}
        # Permit only a restrictive status transition, not plan/period changes.
    return org


async def _subscription_has_paid_history(conn, org, subscription_id):
    # Legacy audit entries without a subscription cannot prove initialization
    # is safe. Reconcile rather than resetting a previously paid quota again.
    return bool(await conn.fetchval("""
        SELECT 1 FROM audit_log
        WHERE organization_id = $1::uuid AND action = 'billing.webhook.subscription_activated'
          AND (details->>'subscription_id' = $2 OR details->>'subscription_id' IS NULL)
        LIMIT 1
    """, str(org["id"]), subscription_id))


async def _invoice_payment_recorded(conn, org, data):
    """A paid invoice is terminal for that invoice, not for another renewal."""
    if not data.get("id"):
        return False
    return bool(await conn.fetchval("""
        SELECT 1 FROM audit_log
        WHERE organization_id = $1::uuid AND action = 'billing.webhook.subscription_activated'
          AND details->>'stripe_object_id' = $2 AND details->>'subscription_id' = $3
        LIMIT 1
    """, str(org["id"]), data["id"], data.get("subscription")))


async def _same_invoice_failed_at_cursor(conn, org, data):
    if not data.get("id"):
        return False
    return bool(await conn.fetchval("""
        SELECT 1 FROM audit_log
        WHERE organization_id = $1::uuid AND action = 'billing.webhook.payment_failed'
          AND details->>'stripe_event_id' = $2
          AND details->>'stripe_object_id' = $3 AND details->>'subscription_id' = $4
        LIMIT 1
    """, str(org["id"]), org.get("stripe_event_id"), data["id"], data.get("subscription")))


async def _invoice_intent_for_subscription(conn, org, data):
    """Stripe's signed subscription metadata binds an early invoice to checkout.

    The API 2024-06-20 invoice snapshots subscription_data.metadata in
    subscription_details.metadata. Customer alone is never authorization.
    """
    details = data.get("subscription_details") or {}
    metadata = details.get("metadata") if isinstance(details, dict) else None
    intent_id = metadata.get("checkout_intent_id") if isinstance(metadata, dict) else None
    if not _valid_org_uuid(intent_id):
        return None
    intent = await conn.fetchrow("""
        SELECT id, organization_id, session_id, payload FROM billing_checkout_intents
        WHERE id = $1::uuid AND organization_id = $2::uuid FOR UPDATE
    """, intent_id, str(org["id"]))
    if not intent:
        return None
    frozen = intent["payload"]
    if isinstance(frozen, str):
        frozen = json.loads(frozen)
    if not isinstance(frozen, dict):
        return None
    price_ids = {
        (line.get("price") or {}).get("id")
        for line in (data.get("lines") or {}).get("data", [])
        if isinstance(line, dict) and isinstance(line.get("price"), dict)
    }
    if (frozen.get("checkout_intent_id") != intent_id
            or frozen.get("stripe_customer_id") != data.get("customer")
            or not frozen.get("price_id") or frozen["price_id"] not in price_ids
            or frozen.get("_subscription_id") not in {None, data.get("subscription")}
            or frozen.get("_resolution") == "expired"):
        return None
    return {**dict(intent), "payload": frozen}


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
    if (data.get("subscription")
            and frozen.get("_subscription_id") not in {None, data["subscription"]}):
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
    if intent["payload"].get("_resolution") == "expired":
        raise HTTPException(503, "Conflicting Stripe checkout lifecycle requires reconciliation")

    riga_org = await conn.fetchrow(
        "SELECT subscription_id, subscription_status, trial_end FROM organizations WHERE id = $1", org_id
    )

    if not await repo.process_stripe_event_in_tx(conn, event_id, org_id):
        return {"action": "duplicate", "status": "skipped", "organization_id": org_id}

    already_bound = riga_org and riga_org.get("subscription_id") == subscription_id
    if already_bound:
        await _complete_checkout_intent(conn, org_id, intent["id"], data["id"], subscription_id)
        return {"action": "checkout_confirmed", "organization_id": org_id}

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
            trial_start = COALESCE(trial_start, $3), trial_end = $4,
            current_period_start = $3, current_period_end = $4,
            suspension_notified_at = NULL
        WHERE id = $5
    """, customer_id, subscription_id, now, nuovo_trial_end, org_id)
    await _complete_checkout_intent(conn, org_id, intent["id"], data["id"], subscription_id)
    return {"action": "subscription_created", "status": "trialing", "organization_id": org_id}


async def _complete_checkout_intent(conn, org_id, intent_id, session_id, subscription_id):
    await conn.execute("""
        UPDATE billing_checkout_intents
        SET session_id = COALESCE(session_id, $2), expires_at = NOW(),
            payload = payload || jsonb_build_object(
                '_resolved', true, '_resolution', 'completed', '_subscription_id', $4::text
            )
        WHERE id = $1::uuid AND organization_id = $3::uuid
          AND (session_id IS NULL OR session_id = $2)
    """, intent_id, session_id, str(org_id), subscription_id)


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
    if intent["payload"].get("_resolution") == "completed":
        return {"action": "checkout_already_completed", "organization_id": org_id}
    await conn.execute("""
        UPDATE billing_checkout_intents
        SET session_id = COALESCE(session_id, $2), expires_at = NOW(),
            payload = payload || '{"_resolved": true, "_resolution": "expired"}'::jsonb
        WHERE id = $1::uuid AND organization_id = $3::uuid
          AND (session_id IS NULL OR session_id = $2)
    """, intent["id"], data["id"], str(org_id))
    return {"action": "checkout_expired", "organization_id": org_id}


async def _lookup_org_by_customer(customer_id: str, repo, conn) -> dict | None:
    if not customer_id:
        return None
    row = await conn.fetchrow(
        "SELECT id, stripe_customer_id, subscription_id, subscription_status, trial_end, plan, "
        "current_period_start, current_period_end, messages_used_this_period "
        "FROM organizations WHERE stripe_customer_id = $1",
        customer_id,
    )
    return dict(row) if row else None


def _period_from_timestamps(start, end):
    if (isinstance(start, bool) or isinstance(end, bool)
            or not isinstance(start, (int, float)) or not isinstance(end, (int, float))
            or start <= 0 or end <= start):
        return None
    try:
        return datetime.fromtimestamp(start, tz=timezone.utc), datetime.fromtimestamp(end, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _invoice_service_line(data):
    """Use the purchased service period, not Invoice's retrospective usage period.

    V1 sells one monthly subscription. Prorations, unknown/annual prices and
    multiple service lines require provider reconciliation rather than guessing.
    """
    from src.core.billing.plans import PLANS

    line_collection = data.get("lines")
    if not isinstance(line_collection, dict) or line_collection.get("has_more") is not False:
        return None
    lines = line_collection.get("data", [])
    if not isinstance(lines, list) or len(lines) != 1 or not isinstance(lines[0], dict):
        return None
    line = lines[0]
    if line.get("type") != "subscription":
        return None
    price = line.get("price") or {}
    if not isinstance(price, dict):
        return None
    recurring = price.get("recurring") or {}
    if (line.get("proration") is not False
            or line.get("currency") != "eur"
            or line.get("subscription") != data.get("subscription")
            or type(line.get("quantity")) is not int or line["quantity"] != 1):
        return None
    plans = [(slug, plan) for slug, plan in PLANS.items()
             if plan.stripe_price_id and plan.stripe_price_id == price.get("id")]
    if len(plans) != 1:
        return None
    slug, plan = plans[0]
    current_catalog_snapshot = price.get("_catalog_monthly_snapshot") is True
    if current_catalog_snapshot:
        # Current Stripe invoice lines reference the Price ID without copying
        # recurring facts. The configured monthly catalog ID is authoritative.
        if recurring and (not isinstance(recurring, dict)
                          or recurring.get("interval") != "month"
                          or type(recurring.get("interval_count")) is not int
                          or recurring["interval_count"] != 1):
            return None
    elif (not isinstance(recurring, dict) or recurring.get("interval") != "month"
          or type(recurring.get("interval_count")) is not int
          or recurring["interval_count"] != 1):
        return None
    expected_amount = plan.price_monthly_eur * 100
    if type(line.get("amount")) is not int or line["amount"] != expected_amount:
        return None
    period = line.get("period") or {}
    if not isinstance(period, dict):
        return None
    dates = _period_from_timestamps(period.get("start"), period.get("end"))
    return (slug, price["id"], *dates) if dates is not None else None


def _invoice_period(data):
    service_line = _invoice_service_line(data)
    return service_line[2:] if service_line else None


def _paid_invoice_period(data):
    if (data.get("status") != "paid"
            or type(data.get("amount_paid")) is not int or data["amount_paid"] <= 0
            or data.get("currency") != "eur"):
        return None
    return _invoice_period(data)


async def _handle_invoice_paid(conn, repo, data, event_id, *, scope=None):
    period = _paid_invoice_period(data)
    if period is None:
        if (data.get("status") == "paid" and type(data.get("amount_paid")) is int
                and data["amount_paid"] > 0 and data.get("currency") == "eur"):
            raise HTTPException(503, "Stripe invoice monthly service period requires reconciliation")
        return None
    customer_id = data.get("customer")
    org = await _lookup_org_by_customer(customer_id, repo, conn)
    if not org:
        return None

    intent = (scope or {}).get("_invoice_intent")
    service_line = _invoice_service_line(data)
    plan_slug = service_line[0]
    if intent is not None:
        frozen = intent["payload"]
        from src.core.billing.plans import get_plan
        try:
            intent_plan = get_plan(frozen.get("plan"))
        except (KeyError, ValueError):
            intent_plan = None
        if (not intent_plan or frozen.get("interval") != "monthly"
                or intent_plan.stripe_price_id != frozen.get("price_id")
                or service_line[1] != frozen.get("price_id") or plan_slug != frozen.get("plan")):
            raise HTTPException(503, "Stripe checkout price requires reconciliation")
        plan_slug = frozen["plan"]
    elif org.get("subscription_id") != data.get("subscription"):
        raise HTTPException(503, "Stripe subscription binding requires reconciliation")
    if not plan_slug:
        raise HTTPException(503, "Stripe invoice price requires reconciliation")

    if not await repo.process_stripe_event_in_tx(conn, event_id, org["id"]):
        return {"action": "duplicate", "status": "skipped", "organization_id": org["id"]}

    period_start, period_end = period
    if intent is not None:
        await conn.execute("UPDATE organizations SET subscription_id = $2 WHERE id = $1", org["id"], data["subscription"])
        await conn.execute("""
            UPDATE billing_checkout_intents
            SET payload = jsonb_set(payload, '{_subscription_id}', to_jsonb($3::text), true)
                          || '{"_paid_period_initialized": true}'::jsonb
            WHERE id = $1::uuid AND organization_id = $2::uuid
        """, intent["id"], org["id"], data["subscription"])

    await conn.execute(
        "UPDATE organizations SET subscription_status = 'active', suspension_notified_at = NULL WHERE id = $1",
        org["id"],
    )
    if ((scope or {}).get("_initial_paid_period")
            or (period_end > period_start and (org.get("current_period_start") is None
                                             or period_start > org["current_period_start"]))):
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

    status, trial_end_to_store = _validated_subscription_state(org, data)
    if not await repo.process_stripe_event_in_tx(conn, event_id, org["id"]):
        return {"action": "duplicate", "status": "skipped", "organization_id": org["id"]}

    # Paid invoices alone own plan, limits, service periods and quota resets.
    await conn.execute("""
        UPDATE organizations SET subscription_status = $1,
            trial_end = CASE WHEN $1 = 'trialing' THEN $3 ELSE trial_end END,
            suspension_notified_at = CASE WHEN $1 IN ('active','trialing') THEN NULL ELSE suspension_notified_at END
        WHERE id = $2
    """, status, org["id"], trial_end_to_store)
    return {"action": "subscription_updated", "plan": org.get("plan"), "organization_id": org["id"]}



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
    await conn.execute(
        "UPDATE organizations SET subscription_status = 'canceled', subscription_id = $2 WHERE id = $1",
        org["id"], data["id"],
    )
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
