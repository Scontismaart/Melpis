"""Minimize restricted inbound payloads before storage and AI processing."""

import json
import uuid

MEDICAL_RESTRICTED = frozenset(
    {
        "studio_medico_dentista",
        "studio_medico",
        "medico",
        "clinica",
        "sanitario",
        "odontoiatra",
        "dentista",
        "fisioterapia",
        "dottore",
        "poliambulatorio",
        "sanita",
        "medical_disabled",
    }
)


def medical_restricted(profile, classification=None):
    """Positive signals only restrict processing; neither grants permission."""
    if isinstance(profile, str):
        try:
            profile = json.loads(profile)
        except (TypeError, ValueError):
            profile = {}
    sector = profile.get("verticale") if isinstance(profile, dict) else None
    trusted = classification.get("sector") if classification else None
    return any(
        isinstance(v, str) and v.strip().lower() in MEDICAL_RESTRICTED
        for v in (sector, trusted)
    )


def medical_administrative_text_allowed(text):
    """Allow only the bounded C2.4 administrative vocabulary for medical tenants.

    This never classifies clinical text as safe. Unknown words, clinical terms,
    oversized input, and malformed confirmation commands remain handoff-only.
    """
    if not isinstance(text, str) or not text.strip() or len(text) > 512:
        return False
    import re
    if re.fullmatch(r"CONFIRM [0-9a-f-]{36}", text.strip()):
        return True
    from types import SimpleNamespace
    from src.core.bookings.conversation_state import medical_source_requires_handoff
    try:
        return not medical_source_requires_handoff(
            text, SimpleNamespace(sector="studio_medico_dentista"),
        )
    except (TypeError, ValueError, AttributeError):
        return False


async def inbound_classification_status(repo, organization_id):
    """Return restricted, unknown, or error without interpreting unknown as medical."""
    try:
        profile = await repo.get_org_business_profile(organization_id)
    except Exception:
        return "error"
    if medical_restricted(profile):
        return "restricted"
    pool = getattr(repo, "pool", None)
    if pool is None:
        return "error"
    try:
        async with pool.acquire() as conn:
            if not await conn.fetchval(
                "SELECT to_regclass('public.booking_privacy_classification_reader')"
            ):
                return "unknown"
            row = await conn.fetchrow(
                "SELECT sector FROM booking_privacy_classification_reader WHERE organization_id=$1",
                uuid.UUID(str(organization_id)),
            )
            return "restricted" if medical_restricted(profile, row) else "unknown"
    except Exception:
        return "error"


async def medical_restricted_for_inbound(repo, organization_id, business_profile,
                                         *, text="", allow_administrative=False):
    status = await inbound_classification_status(repo, organization_id)
    if status == "unknown":
        # Preserve a positive medical signal supplied by the caller even when
        # an older schema cannot attest the classification. Unknown stays
        # permissive only for profiles with no medical signal, matching the
        # pre-existing fail-closed boundary.
        return medical_restricted(business_profile)
    # Administrative messages pass only after the database has affirmatively
    # classified this tenant and only when the separately gated private worker
    # is enabled. Provider errors, old schemas and ambiguous profiles fail closed.
    if status == "restricted" and allow_administrative and medical_administrative_text_allowed(text):
        return False
    return True


QUARANTINE_TEXT = "Classificazione non disponibile: intervento umano"


def minimized_inbound_text(text, *, quarantine=False, allow_medical_admin=False):
    """Preserve opt-out semantics as a canonical command, never source text."""
    from src.whatsapp.service import OPT_OUT_KEYWORDS, _normalize_text

    normalized = _normalize_text(text if isinstance(text, str) else "")
    if any(word in normalized for word in OPT_OUT_KEYWORDS["it"]):
        return "stop"
    if allow_medical_admin and medical_administrative_text_allowed(text):
        return text.strip()
    return QUARANTINE_TEXT if quarantine else "Richiesta riservata: intervento umano"


def _identifier(value, *, digits=False):
    """Restrict retained routing identifiers to bounded provider-safe characters."""
    import re

    pattern = r"[0-9]{1,32}" if digits else r"[A-Za-z0-9_.:$=+/-]{1,256}"
    return value if isinstance(value, str) and re.fullmatch(pattern, value) else None


def minimize_whatsapp_change(change, *, quarantine=False, allow_medical_admin=False):
    """Build a fresh routing envelope, never copying unknown provider fields."""
    value = change.get("value") if isinstance(change, dict) else None
    value = value if isinstance(value, dict) else {}
    result = {"field": "messages", "value": {"messaging_product": "whatsapp"}}
    clean = result["value"]
    metadata = value.get("metadata")
    if isinstance(metadata, dict):
        phone_id = _identifier(metadata.get("phone_number_id"), digits=True)
        if phone_id:
            clean["metadata"] = {"phone_number_id": phone_id}
    messages = []
    for msg in value.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        mid, sender = (
            _identifier(msg.get("id")),
            _identifier(msg.get("from"), digits=True),
        )
        if not mid or not sender:
            continue
        source = msg.get("text") if isinstance(msg.get("text"), dict) else {}
        item = {
            "id": mid,
            "from": sender,
            "type": "text",
            "text": {
                "body": minimized_inbound_text(
                    source.get("body", ""), quarantine=quarantine,
                    allow_medical_admin=allow_medical_admin and not quarantine,
                )
            },
        }
        timestamp = _identifier(msg.get("timestamp"), digits=True)
        if timestamp:
            item["timestamp"] = timestamp
        messages.append(item)
    if messages:
        clean["messages"] = messages
    statuses = []
    for status in value.get("statuses") or []:
        if not isinstance(status, dict):
            continue
        sid, timestamp = (
            _identifier(status.get("id")),
            _identifier(status.get("timestamp"), digits=True),
        )
        state = status.get("status")
        if (
            not sid
            or not timestamp
            or state not in {"sent", "delivered", "read", "failed", "deleted"}
        ):
            continue
        item = {"id": sid, "timestamp": timestamp, "status": state}
        recipient = _identifier(status.get("recipient_id"), digits=True)
        if recipient:
            item["recipient_id"] = recipient
        try:
            callback = str(uuid.UUID(status.get("biz_opaque_callback_data")))
        except (TypeError, ValueError, AttributeError):
            callback = None
        if callback:
            item["biz_opaque_callback_data"] = callback
        statuses.append(item)
    if statuses:
        clean["statuses"] = statuses
    return result


def minimize_instagram_event(event, *, quarantine=False):
    """Retain only bounded identifiers and a canonical text marker."""
    event = event if isinstance(event, dict) else {}
    sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
    recipient = (
        event.get("recipient") if isinstance(event.get("recipient"), dict) else {}
    )
    msg = event.get("message") if isinstance(event.get("message"), dict) else {}
    result = {
        "sender": {"id": _identifier(sender.get("id"), digits=True) or ""},
        "recipient": {"id": _identifier(recipient.get("id"), digits=True) or ""},
        "message": {
            "mid": _identifier(msg.get("mid")) or "",
            "text": minimized_inbound_text(msg.get("text"), quarantine=quarantine),
        },
    }
    timestamp = event.get("timestamp")
    if type(timestamp) is int and 0 <= timestamp <= 9999999999999:
        result["timestamp"] = timestamp
    return result
