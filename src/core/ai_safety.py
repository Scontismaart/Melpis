"""Server-owned Phase 5 policy. Never loaded from client/business instructions."""

# Re-enabling requires a code review and caller/record/action authorization.
# Intentionally no environment or client override while that model is absent.
CRM_AI_TOOLS_ENABLED = False
AI_INPUT_MAX_CHARS = 12000
BOOKING_FAILURE_REPLY = "Non posso confermare la prenotazione. Chiedo allo staff di verificare."


def validate_ai_input(text):
    # Validate before Pydantic, whose errors may include the rejected input.
    if not isinstance(text, str) or len(text) > AI_INPUT_MAX_CHARS:
        raise RuntimeError("AI input rejected")


async def record_ai_attempts(repo, org_id, usage, message_id, conversation_id, *, task_type="customer_message"):
    """Legacy sink disabled; only the per-dispatch provider adapter is authoritative."""
    raise RuntimeError("AI usage must be recorded by the provider accounting adapter")


def apply_booking_result(response, booking):
    """Only application-confirmed persisted state may determine success wording."""
    if not isinstance(booking, dict) or not booking.get("id"):
        raise RuntimeError("Booking persistence not confirmed")
    if booking.get("external_sync_status") == "failed" or booking.get("richiede_intervento"):
        response.risposta = BOOKING_FAILURE_REPLY
        response.richiede_umano = True
        response.motivo = "booking_requires_verification"
    elif str(booking.get("stato", "")).lower() in {"confermata", "confermato", "confirmed"}:
        response.risposta = "Prenotazione confermata."
    else:
        response.risposta = "Richiesta di prenotazione registrata, in attesa di conferma."


def booking_failed(response):
    response.risposta = BOOKING_FAILURE_REPLY
    response.richiede_umano = True
    response.motivo = "booking_failed"


def replay_requires_intervention(booking):
    # A crash may precede persistence of the external sync failure flag.
    # Pending/unknown state must therefore never suppress staff escalation.
    return (not isinstance(booking, dict)
            or bool(booking.get("richiede_intervento"))
            or str(booking.get("stato", "")).lower() not in {"confermata", "confermato", "confirmed"})
