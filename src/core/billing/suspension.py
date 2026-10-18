"""Stato di sospensione org — derivato, mai memorizzato.

is_org_suspended() calcola lo stato dalla sola verita' canonica
(subscription_status + trial_end su organizations). Non c'e' colonna
dedicata: aggiungerne una significherebbe un secondo posto dove lo stato
puo' disallinearsi dai dati di billing.

La colonna suspension_notified_at NON fa parte di questo: e' un fatto
storico ("ho gia' mandato la mail?"), non derivabile, e viene gestita
separatamente dal webhook e dal job trial.
"""

from datetime import datetime, timezone


class AIGenerationBlocked(RuntimeError):
    """The verified tenant state does not authorize a new AI generation."""


def assert_ai_generation_allowed(billing: dict | None) -> None:
    """Require verified entitlement and numerical quota before new AI work."""
    if not isinstance(billing, dict) or not billing:
        raise AIGenerationBlocked("billing state unavailable")
    if billing.get("ai_accounting_blocked"):
        raise AIGenerationBlocked("AI accounting unavailable")
    status = billing.get("subscription_status")
    if status not in {"active", "trialing"} or is_org_suspended(status, billing.get("trial_end")):
        raise AIGenerationBlocked("subscription is not active")
    limit = billing.get("messages_limit")
    used = billing.get("messages_used_this_period")
    if (not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0
            or not isinstance(used, int) or isinstance(used, bool) or used < 0):
        raise AIGenerationBlocked("AI quota unavailable")
    if used >= limit:
        raise AIGenerationBlocked("AI quota exhausted")


def is_org_suspended(subscription_status: str | None, trial_end=None) -> bool:
    """True se l'org non puo' usare il pipeline conversazionale.

    - canceled: sempre sospesa.
    - trial_end scaduto: sospesa solo se lo status NON e' active/past_due.
      Un'org pagante puo' avere un trial_end residuo nel passato: trattarla
      come sospesa sarebbe un falso positivo che bloccherebbe il servizio
      che sta effettivamente pagando.
    """
    # Existing commercial policy: past_due remains usable while Stripe retries
    # collection. unpaid/canceled terminate that grace period. Unknown states
    # never grant access; a trial requires a verifiable future end date.
    if subscription_status in ("active", "past_due"):
        return False
    if subscription_status != "trialing" or not isinstance(trial_end, datetime):
        return True
    if trial_end.tzinfo is None:
        trial_end = trial_end.replace(tzinfo=timezone.utc)
    return trial_end <= datetime.now(timezone.utc)


def giorni_trial_rimanenti(trial_end, now=None) -> int:
    """Giorni interi di trial ancora davanti (floor), 0 se scaduto/assente.

    Serve al checkout per ereditare il trial del signup invece di regalarne
    un secondo: 7 giorni al signup + 7 al checkout = fino a 14 gratis.
    Floor, non ceil: il periodo gratuito non si estende mai oltre la
    trial_end originale del signup."""
    if trial_end is None:
        return 0
    if not isinstance(trial_end, datetime):
        return 0
    now = now or datetime.now(timezone.utc)
    if not isinstance(now, datetime):
        return 0
    if isinstance(trial_end, datetime) and trial_end.tzinfo is None:
        trial_end = trial_end.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    residuo = (trial_end - now).total_seconds()
    if residuo <= 0:
        return 0
    return int(residuo // 86400)


def trial_end_unix_timestamp(trial_end, now=None) -> int | None:
    """Return the original UTC trial deadline as a Unix timestamp, if active.

    Flooring to seconds can end a trial fractionally early, never later. The
    checkout must not replace this deadline with a rounded number of days.
    """
    if not isinstance(trial_end, datetime):
        return None
    now = now or datetime.now(timezone.utc)
    if not isinstance(now, datetime):
        return None
    if trial_end.tzinfo is None:
        trial_end = trial_end.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if trial_end <= now:
        return None
    timestamp = int(trial_end.timestamp())
    return timestamp if timestamp > int(now.timestamp()) else None
