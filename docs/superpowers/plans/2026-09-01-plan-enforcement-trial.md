# Plan Limits Enforcement & Double Trial Removal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** (1) Eliminare il doppio trial (7 giorni al signup + 7 al checkout = fino a 14 gratis): il periodo gratuito resta solo quello del signup, e il checkout Stripe eredita i giorni rimanenti. (2) Applicare davvero i flag dei piani (`has_rag`, `has_reviews`): oggi sono definiti in `PLANS` ma nessun endpoint li verifica.

**Architecture:** Nuova funzione pura `giorni_trial_rimanenti(trial_end)` in `src/core/billing/suspension.py` (modulo dello stato derivato di billing): calcola i giorni di trial ancora davanti alla data di fine trial dell'org. `routes.py` (create-checkout-session) la usa per impostare `trial_period_days` solo se >0 e la comunica alla sessione via `metadata["trial_days_remaining"]`; `webhook_handler` (checkout.session.completed) legge quel valore (fallback al default configurato) e scrive su DB un `trial_end` che NON estende mai il trial del signup. Per l'enforcement: helper `_piano_blocca_feature(repo, org_id, feature)` in `src/api/main.py` che legge il billing snapshot (plan + status già su `organizations`) e restituisce un messaggio di blocco se il piano non include la feature; applicato agli endpoint RAG e recensioni. Org in trial senza piano (`plan IS NULL`) = accesso completo (la prova è del massimo).

**Tech Stack:** FastAPI dependencies inline, Stripe Checkout API, pytest con fake repo (nessuna rete).

**Spec:** Audit billing 2026-09-01 — gap #1 (nessuna enforcement di has_reviews/has_rag, ALTA) e #3 (doppio trial, MEDIA). Scelta di prodotto documentata: trial = funzionalità complete; il piano viene applicato solo da `invoice.paid` in poi. NOTA: `users_limit`/`whatsapp_numbers_limit` restano non applicate perché NON ESISTONO endpoint di creazione utenti/numeri (YAGNI); andranno collegate quando quegli endpoint nasceranno.

## Global Constraints

- Tenant scoping invariato: ogni lookup billing usa l'org_id del JWT.
- Trial senza piano = funzionalità complete (decisione esplicita, non un gap).
- Nessuna modifica ai piani/prezzi esistenti (`PLANS` invariato).
- Il webhook resta idempotente (`process_stripe_event_in_tx` invariato).
- Messaggi di blocco in italiano, con riferimento all'upgrade.

---

### Task 1: `giorni_trial_rimanenti` + checkout senza doppio trial

**Files:**
- Modify: `src/core/billing/suspension.py` (funzione pura), `src/core/billing/routes.py` (create-checkout-session ~riga 60-95), `src/core/billing/webhook_handler.py` (checkout block ~riga 112-128)
- Test: `tests/unit/billing/test_trial.py` (nuovo)

**Interfaces:**
- Produces: `giorni_trial_rimanenti(trial_end, now=None) -> int` (pura; aware o naive UTC accettati; 0 se passato/None).
- Consumes: `get_organization_billing(org_id)` (esiste già, ritorna `trial_end`).

- [ ] **Step 1: Scrivere il test che fallisce**

`tests/unit/billing/test_trial.py` (creare `__init__.py` se il pacchetto non esiste):

```python
from datetime import datetime, timedelta, timezone

from src.core.billing.suspension import giorni_trial_rimanenti


def test_trial_futuro_ritorna_giorni_interi():
    now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    trial_end = now + timedelta(days=5, hours=2)
    assert giorni_trial_rimanenti(trial_end, now=now) == 5


def test_trial_passato_ritorna_zero():
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    assert giorni_trial_rimanenti(now - timedelta(days=1), now=now) == 0


def test_trial_none_ritorna_zero():
    assert giorni_trial_rimanenti(None) == 0


def test_trial_naive_accettato():
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    naive = datetime(2026, 9, 4)  # naive UTC
    assert giorni_trial_rimanenti(naive, now=now) == 3
```

- [ ] **Step 2: Eseguire il test e verificare che fallisca**

Run: `python -m pytest tests/unit/billing/test_trial.py -v`
Expected: FAIL con `ImportError: cannot import name 'giorni_trial_rimanenti'`

- [ ] **Step 3: Implementare la funzione pura**

In `src/core/billing/suspension.py` aggiungere:

```python
import math


def giorni_trial_rimanenti(trial_end, now=None) -> int:
    """Giorni interi di trial ancora davanti (ceil), 0 se scaduto/assente.

    Serve al checkout per ereditare il trial del signup invece di regalarne
    un secondo: 7 giorni al signup + 7 al checkout = fino a 14 gratis."""
    if trial_end is None:
        return 0
    now = now or datetime.now(timezone.utc)
    if isinstance(trial_end, datetime) and trial_end.tzinfo is None:
        trial_end = trial_end.replace(tzinfo=timezone.utc)
    if not isinstance(now, datetime):
        return 0
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    residuo = (trial_end - now).total_seconds()
    if residuo <= 0:
        return 0
    return math.ceil(residuo / 86400)
```

- [ ] **Step 4: Eseguire i test e verificare che passino**

Run: `python -m pytest tests/unit/billing/test_trial.py -v`
Expected: 4 PASS

- [ ] **Step 5: Usarla nel checkout (routes.py)**

In `create-checkout-session`, sostituire il blocco trial:

```python
    # Un solo trial: il checkout eredita i giorni rimanenti del trial del
    # signup invece di regalarne un secondo periodo (audit billing #3).
    from src.core.billing.suspension import giorni_trial_rimanenti
    try:
        billing = await repo.get_organization_billing(org_id)
    except ValueError:
        billing = None
    trial_rimanenti = giorni_trial_rimanenti(billing.get("trial_end")) if billing else 0

    subscription_data = {}
    if trial_rimanenti > 0:
        subscription_data["trial_period_days"] = trial_rimanenti
    session = await _stripe_call(
        st.checkout.Session.create,
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        mode="subscription",
        success_url=req.success_url,
        cancel_url=req.cancel_url,
        client_reference_id=str(org_id),
        metadata={"trial_days_remaining": str(trial_rimanenti)},
        subscription_data=subscription_data or None,
        payment_method_collection="required",
    )
```

(Nota: `subscription_data=None` è accettato da Stripe come assente; in alternativa omettere il kwarg condizionalmente.)

- [ ] **Step 6: Allineare il webhook (webhook_handler.py)**

Nel blocco `checkout.session.completed`, sostituire `now + timedelta(days=trial_days)`:

```python
    # Il trial NON si rinnova al checkout: si eredita quello del signup
    # (metadata["trial_days_remaining"] scritto da create-checkout-session).
    meta = data.get("metadata") or {}
    try:
        trial_days_residui = int(meta.get("trial_days_remaining", trial_days))
    except (TypeError, ValueError):
        trial_days_residui = trial_days
    nuovo_trial_end = now + timedelta(days=trial_days_residui)
    await conn.execute("""
        UPDATE organizations SET
            stripe_customer_id = $1, subscription_id = $2,
            subscription_status = 'trialing',
            trial_start = $3, trial_end = $4,
            current_period_start = $3, current_period_end = $4,
            suspension_notified_at = NULL
        WHERE id = $5
    """, customer_id, subscription_id, now, nuovo_trial_end, org_id)
```

- [ ] **Step 7: Eseguire i test**

Run: `python -m pytest tests/unit/billing/test_trial.py tests/concurrency/test_stripe_cancellation_integration.py -q`
Expected: test_trial 4 PASS; il file concurrency richiede DB (errore pg_pool preesistente in locale, verde in CI).

- [ ] **Step 8: Commit**

```bash
git add src/core/billing/ tests/unit/billing/
git commit -m "fix(billing): un solo trial, checkout eredita i giorni rimanenti del signup"
```

---

### Task 2: Enforcement `has_rag` / `has_reviews`

**Files:**
- Modify: `src/api/main.py` (helper nuovo + 4 endpoint: `/api/documenti/carica` ~1528, `/api/documenti/carica-file` ~1554, `/api/documenti/chiedi` (cercare route), `/api/recensione` ~1059)
- Test: `tests/unit/billing/test_feature_enforcement.py` (nuovo)

**Interfaces:**
- Consumes: `_get_billing_snapshot(repo, org_id)` (esiste), `PLANS` di `src/core/billing/plans.py`.
- Produces: `async def _piano_blocca_feature(repo, org_id, feature: str) -> str | None` — messaggio di blocco (403) o None. `feature` ∈ {"rag", "recensioni"}.

- [ ] **Step 1: Scrivere il test che fallisce**

`tests/unit/billing/test_feature_enforcement.py`:

```python
import pytest

import src.api.main as api_main


class FakeRepo:
    def __init__(self, billing):
        self._billing = billing

    async def get_organization_billing(self, org_id):
        if self._billing is None:
            raise ValueError("org inesistente")
        return self._billing


@pytest.mark.asyncio
async def test_blocca_rag_su_piano_starter():
    repo = FakeRepo({"plan": "starter", "subscription_status": "active"})
    msg = await api_main._piano_blocca_feature(repo, "org-1", "rag")
    assert msg and "Knowledge Base" in msg


@pytest.mark.asyncio
async def test_consentire_rag_su_business():
    repo = FakeRepo({"plan": "business", "subscription_status": "active"})
    assert await api_main._piano_blocca_feature(repo, "org-1", "rag") is None


@pytest.mark.asyncio
async def test_blocca_recensioni_su_starter_non_su_pro():
    repo = FakeRepo({"plan": "starter", "subscription_status": "active"})
    assert (await api_main._piano_blocca_feature(repo, "org-1", "recensioni")) is not None
    repo2 = FakeRepo({"plan": "pro", "subscription_status": "active"})
    assert await api_main._piano_blocca_feature(repo2, "org-1", "recensioni") is None


@pytest.mark.asyncio
async def test_trial_senza_piano_accesso_completo():
    repo = FakeRepo({"plan": None, "subscription_status": "trialing"})
    assert await api_main._piano_blocca_feature(repo, "org-1", "rag") is None


@pytest.mark.asyncio
async def test_billing_assente_consentire_failopen():
    repo = FakeRepo(None)
    assert await api_main._piano_blocca_feature(repo, "org-1", "rag") is None
```

- [ ] **Step 2: Eseguire il test e verificare che fallisca**

Run: `python -m pytest tests/unit/billing/test_feature_enforcement.py -v`
Expected: FAIL con `AttributeError: ... has no attribute '_piano_blocca_feature'`

- [ ] **Step 3: Implementare helper + applicazione agli endpoint**

In `src/api/main.py` dopo `_record_ai_usage`:

```python
async def _piano_blocca_feature(repo, org_id, feature: str) -> str | None:
    """Messaggio di blocco se il piano dell'org non include la feature.

    Org in trial senza piano (plan IS NULL) = accesso completo: la prova e'
    del piano massimo; i limiti si applicano da invoice.paid in poi
    (audit billing #1). Fail-open se il billing non e' leggibile."""
    if not repo or not org_id:
        return None
    billing = await _get_billing_snapshot(repo, org_id)
    if not billing:
        return None
    plan_slug = billing.get("plan")
    if not plan_slug:
        return None
    from src.core.billing.plans import PLANS
    plan = PLANS.get(plan_slug)
    if not plan:
        return None
    if feature == "rag" and not plan.has_rag:
        return f"Il piano {plan.name} non include la Knowledge Base AI. Effettua l'upgrade al piano Scala per caricare documenti."
    if feature == "recensioni" and not plan.has_reviews:
        return f"Il piano {plan.name} non include la gestione delle recensioni. Effettua l'upgrade per abilitarla."
    return None
```

Negli endpoint `/api/documenti/carica`, `/api/documenti/carica-file`, `/api/documenti/chiedi` (dopo il recupero di `user`/`repo`, prima del lavoro):

```python
    blocco = await _piano_blocca_feature(repo, org_id, "rag")
    if blocco:
        raise HTTPException(status_code=403, detail=blocco)
```

In `/api/recensione` (stessa posizione, feature "recensioni"):

```python
    blocco = await _piano_blocca_feature(repo, org_id, "recensioni")
    if blocco:
        raise HTTPException(status_code=403, detail=blocco)
```

- [ ] **Step 4: Eseguire i test e verificare che passino**

Run: `python -m pytest tests/unit/billing/ -v`
Expected: tutti PASS

- [ ] **Step 5: Compilazione**

Run: `python -m py_compile src/api/main.py src/core/billing/routes.py src/core/billing/webhook_handler.py src/core/billing/suspension.py`
Expected: OK

---

### Task 3: Chiusura

- [ ] **Step 1: Suite**

Run: `python -m pytest tests/unit/billing tests/core/test_onboarding.py -q`
Expected: PASS (esclusi pg_pool)

- [ ] **Step 2: Code review cumulativa finale**

Skill requesting-code-review: reviewer su freebusy (piano 2026-09-01-google-calendar-freebusy.md) + questo piano, includendo anche Globale #6 se completato.

---

## Esito (2026-09-01)

Task 1-2 eseguiti inline. Deviazioni dal piano: `giorni_trial_rimanenti` usa **floor** (non ceil) per non estendere mai il periodo gratuito oltre la trial_end del signup; la funzione è in `suspension.py` con `import math` inline. Il checkout usa `get_organization_billing` già presente (nessuna nuova query) e comunica i giorni residui via session `metadata["trial_days_remaining"]`, che il webhook legge con fallback al default configurato.

Test: `tests/unit/billing/` 9 passed. Test pg_pool/concurrency: errore ambiente preesistente (nessun container Postgres in locale, verdi in CI).

## Addendum (2026-10-17) — scadenza esatta e dipendenza A04

Il checkout salva nell'intent la scadenza originale del trial come timestamp Unix UTC e la passa a Stripe con `subscription_data.trial_end`. Il webhook conserva la stessa scadenza anche se arriva in ritardo; non ricostruisce la data sommando giorni arrotondati. La Checkout Session scade entro la fine del trial. Se restano meno di 30 minuti, la creazione viene rifiutata perché Stripe non consente una Checkout Session con durata inferiore; l'utente può riprovare quando il trial è terminato e il checkout richiederà il metodo di pagamento.

Gli intent legacy senza la scadenza esatta vengono rifiutati, anche quando hanno già un URL Stripe. Nuovi intent per la stessa organizzazione restano bloccati fino alla scadenza registrata di qualsiasi intent legacy non scaduto, anche se non è stato salvato un URL Stripe e l'esito della richiesta è incerto. Così un retry non riutilizza una sessione creata con zero giorni residui arrotondati e non avvia una seconda Checkout Session mentre la prima potrebbe essere ancora attiva.

I test coprono 23 ore residue, trial scaduto, trial completo di sette giorni e il limite di 150 risposte. Il test PostgreSQL `test_trial_boundary_is_atomic_idempotent_and_tenant_scoped` verifica che una sola richiesta concorrente possa claimare la risposta 150 e che quella concorrente venga rifiutata. Questo test verifica solo il limite tecnico di quota. La presentazione dell'upgrade e l'eventuale checkout al raggiungimento della soglia dipendono dal contratto A04, da definire e integrare separatamente; questa modifica non inventa quel comportamento. Il lancio resta mensile: il profilo `commercial_bootstrap` continua a rifiutare il prezzo annuale e non viene introdotta alcuna conversione a pagamento senza consenso.
