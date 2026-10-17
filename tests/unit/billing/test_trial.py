from datetime import datetime, timedelta, timezone

from src.core.billing.suspension import giorni_trial_rimanenti


def test_trial_futuro_ritorna_giorni_interi():
    now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    trial_end = now + timedelta(days=5, hours=2)
    assert giorni_trial_rimanenti(trial_end, now=now) == 5


def test_trial_frazione_inferiore_a_un_giorno_non_viene_estesa():
    now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    assert giorni_trial_rimanenti(now + timedelta(hours=23), now=now) == 0


def test_trial_passato_ritorna_zero():
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    assert giorni_trial_rimanenti(now - timedelta(days=1), now=now) == 0


def test_trial_none_ritorna_zero():
    assert giorni_trial_rimanenti(None) == 0


def test_trial_naive_accettato():
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    naive = datetime(2026, 9, 4)  # naive UTC
    assert giorni_trial_rimanenti(naive, now=now) == 3
