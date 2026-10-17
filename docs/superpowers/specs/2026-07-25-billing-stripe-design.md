# Billing & Stripe Integration — Design Document

> **SUPERSEDED — historical proposal, not current pricing.** The current monthly
> prices are Essenziale €29, Crescita €69, and Scala €149. See
> [`src/core/billing/plans.py`](../../../../src/core/billing/plans.py) and
> [`docs/CHECKLIST-LANCIO-DEFINITIVA.md`](../../../CHECKLIST-LANCIO-DEFINITIVA.md).
> The €49/€99/€199 figures below are retained only as historical evidence.

## Stato attuale

- `usage_events` table exists with `billing_month` generated column — tested, unused
- `organizations` table lacks any billing columns
- No Stripe dependency, no webhook handler, no billing routes
- Plans in codice non esistono — tutto da costruire

## Piani e prezzi

| Piano | Prezzo | Numeri WA | Messaggi/mese | Utenti | Recensioni auto | RAG avanzato | Trial |
|-------|--------|-----------|---------------|--------|-----------------|-------------|-------|
| Starter | 49€/mese | 1 | 500 | 1 | no | no | 7gg |
| Pro | 99€/mese | 1 | 2.000 | 3 | sì (assistita) | no | 7gg |
| Business | 199€/mese | 2+ | illimitati | illimitati | sì (assistita) | sì | 7gg |

**Nota:** Recensioni "auto" e "RAG avanzato" non sono ancora implementati nel codice. Rimossi dalla differenziazione tecnica finché non esistono. I limiti reali oggi sono: numeri WA, messaggi/mese, utenti per organizzazione.

## Architettura

```
Cliente → Stripe Checkout Session (trial 7gg, carta richiesta)
            → checkout.session.completed → attiva subscription_status = 'trialing'
            → invoice.paid → subscription_status = 'active', reset contatore
            → invoice.payment_failed → subscription_status = 'past_due'
            → customer.subscription.updated → aggiorna piano/limiti
            → subscription.deleted → subscription_status = 'canceled'

Cliente → Stripe Customer Portal (autogestione carta, upgrade, downgrade, cancellazione)
            → webhook customer.subscription.updated → riflette cambiamenti

Middleware limiti (prima di ogni messaggio in/out):
  1. subscription_status IN ('active','trialing')? → no → 402
  2. messages_used_this_period < plan.limits? → no → 429
  3. passa

Audit: ogni modifica a organizations.subscription_status loggata su audit_log
```

## Webhook Stripe — eventi gestiti

| Evento | Azione | Idempotenza |
|--------|--------|-------------|
| `checkout.session.completed` | Crea/aggiorna Stripe customer locale, setta `subscription_status='trialing'`, salva `current_period_start` e `current_period_end` dal `subscription.current_period_start/end` nel payload | Dedup su `event_id` |
| `invoice.paid` | Setta `subscription_status='active'`, resetta `messages_used_this_period=0`, aggiorna `current_period_start/end` | Dedup su `event_id` |
| `invoice.payment_failed` | Setta `subscription_status='past_due'`, logga su `audit_log` | Dedup su `event_id` |
| `customer.subscription.updated` | Aggiorna `plan`, resetta contatore se cambio ciclo, aggiorna `current_period_start/end` | Dedup su `event_id` |
| `subscription.deleted` | Setta `subscription_status='canceled'` | Dedup su `event_id` |

**Firma:** Ogni webhook verificato con `Stripe-Signature` header e endpoint secret.
**Idempotenza:** Tabella `processed_stripe_events(event_id TEXT PRIMARY KEY, processed_at TIMESTAMPTZ)`.

## Schema DB — migrazione

```sql
-- Nuove colonne su organizations
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS
    stripe_customer_id TEXT UNIQUE,
    subscription_id TEXT,
    subscription_status TEXT NOT NULL DEFAULT 'incomplete'
        CHECK (subscription_status IN ('incomplete','trialing','active','past_due','canceled')),
    plan TEXT CHECK (plan IN ('starter','pro','business')),
    messages_used_this_period INT NOT NULL DEFAULT 0,
    messages_limit INT CHECK (messages_limit > 0),  -- NULL = illimitato
    users_limit INT CHECK (users_limit > 0),          -- NULL = illimitato
    whatsapp_numbers_limit INT CHECK (whatsapp_numbers_limit > 0), -- NULL = illimitato
    current_period_start TIMESTAMPTZ,
    current_period_end TIMESTAMPTZ,
    trial_start TIMESTAMPTZ,
    trial_end TIMESTAMPTZ;

-- Tabella dedup webhook
CREATE TABLE IF NOT EXISTS processed_stripe_events (
    event_id TEXT PRIMARY KEY,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indice per lookup rapido customer
CREATE INDEX IF NOT EXISTS idx_org_stripe_customer ON organizations(stripe_customer_id);
```

## Counting messaggi

- **Conta:** messaggi in + out (via `whatsapp_service.send_message` e `inbound_processor`)
- **Incluso:** fast_path (saluti, orari, ringraziamenti) — nessuna "scappatoia free"
- **Escluso:** status webhook (delivered/read/failed), claim/retry interni
- **Metodo:** incremento atomico `organizations.messages_used_this_period += 1` a ogni messaggio
- **Reset:** a 0 su ogni `invoice.paid` usando `current_period_start` Stripe
- **Periodo:** ciclo fatturazione Stripe (`current_period_start/end`), non mese calendario

## Middleware limiti

Middleware FastAPI applicato a route messaggio (in/out). Non applicato a webhook Stripe (`/api/billing/webhook`), health (`/api/health`), webhooks WhatsApp (`/webhooks/whatsapp`), routes pubbliche.

La whitelist del rate limit middleware viene estesa con `/api/billing/webhook` per evitare che IP condivisi Stripe vengano bloccati sotto carico.

```
Funzione check_plan_limits(organization_id):
    1. Leggi organizations row (cached breve, es. 10s)
    2. subscription_status:
         'active'/'trialing' → ok
         'past_due' → 402 Payment Required, body: {"error": "payment_failed", "action": "update_payment"}
         'canceled'/'incomplete' → 402 Payment Required, body: {"error": "subscription_inactive"}
    3. messages_limit IS NOT NULL AND messages_used_this_period >= messages_limit:
         → 429 Too Many Requests, body: {"error": "quota_exceeded", "limit": X, "current": Y, "resets_at": current_period_end}
         headers: X-RateLimit-Limit, X-RateLimit-Remaining, X-RateLimit-Reset
    4. Invalida cache su ogni cambiamento subscription_status (webhook)
```

**Downgrade:** Il cambio piano via webhook `customer.subscription.updated` aggiorna `plan` e `messages_limit` immediatamente nel DB. Se l'uso corrente supera già il nuovo limite, il blocco scatta subito al prossimo messaggio — nessun grace period. Documentato: il cliente deve verificare l'uso prima del downgrade.

**Avvisi soft block:** a 80%, 90%, 100% del limite, risposta 429 include header `X-Quota-Warning: 80|90|100`. Dashboard (frontend futuro) può leggere header e mostrare warning.

## Stripe API — endpoint

### `POST /api/billing/create-checkout-session`

Crea Stripe Checkout Session in modalità `subscription`, con `line_items` basate sul `price_id` mensile e `client_reference_id = organization_id`. `subscription_data.trial_end` deve restare almeno 48 ore nel futuro secondo Stripe; l'applicazione aggiunge un margine tecnico di cinque minuti per la latenza e la precisione al secondo. Se il trial applicativo è attivo ma resta meno di questa soglia, non creare intent o sessioni Stripe e non chiedere una carta: il trial applicativo conserva la scadenza originale e il checkout a pagamento sarà disponibile dopo la sua scadenza. Con almeno 48 ore e cinque minuti residui, usa la scadenza assoluta originale del signup, `payment_method_collection=if_required` e `subscription_data.trial_settings.end_behavior.missing_payment_method=cancel`; non calcolare mai un nuovo trial di sette giorni dal checkout. Dopo la scadenza del trial, il checkout richiede un metodo di pagamento (`payment_method_collection=required`) e non invia `subscription_data.trial_end`. La Checkout Session scade al massimo dopo un'ora e comunque non oltre la scadenza del trial. Un intent non risolto senza URL, dopo un timeout ambiguo, può essere ripetuto solo con customer, parametri congelati e la stessa chiave Stripe: Stripe restituisce l'esito memorizzato oppure rivalida la richiesta, senza avviare un secondo intent o modificare la scadenza del trial. L'intent rimane bloccato senza limite di tempo finché un webhook firmato non ne conferma il completamento o la scadenza; in questo modo un evento perso non riapre un secondo checkout e la risoluzione manuale resta necessaria se Stripe non conferma l'esito. L'intervallo annuale resta disabilitato nel profilo `commercial_bootstrap`. `success_url` e `cancel_url` devono restare sul dominio dashboard configurato.

**Runbook per intent unresolved:** il sistema non sblocca automaticamente un'organizzazione quando la Checkout Session scade localmente. Prima di una risoluzione manuale, un operatore autorizzato deve verificare in Stripe, usando `checkout_intent_id` e il Customer congelato, lo stato della Session e l'eventuale subscription; deve poi riconciliare l'evento firmato o registrare una riparazione auditata. Non cancellare né marcare resolved l'intent basandosi soltanto su timeout, assenza del webhook o scadenza locale: l'organizzazione resta in fail-closed finché l'esito provider è verificato.

La quota trial resta limitata a 150 risposte. La relazione fra esaurimento della quota e disponibilità del checkout appartiene al contratto A04; finché il contratto non è approvato, non dedurre né implementare una conversione automatica o un'estensione del trial.

**Auth:** `require_ruolo("owner")`

### `POST /api/billing/create-portal-session`

Crea Stripe Customer Portal session per self-service (cambio carta, upgrade/downgrade, cancellazione).

**Auth:** `require_ruolo("owner")`

### `POST /api/billing/webhook`

Endpoint pubblico (senza auth API) che riceve eventi Stripe. Verifica firma. Dedup su `event_id`. Processa eventi e aggiorna DB.

**Auth:** Nessuna — verifica firma Stripe-Signature.
**Rate limit:** Escluso dal rate limit middleware (whitelist).

### `GET /api/billing/usage`

Ritorna usage corrente: `{ messages_used, messages_limit, period_start, period_end, plan }`.

**Auth:** `require_ruolo("owner", "manager", "staff")`

### `GET /api/billing/subscription`

Ritorna stato abbonamento: `{ plan, status, current_period_end, trial_end }`.

**Auth:** `require_ruolo("owner", "manager", "staff")`

## Env

```
STRIPE_SECRET_KEY=sk_test_...
STRIPE_PUBLISHABLE_KEY=pk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PRICE_STARTER=price_starter_xxx
STRIPE_PRICE_PRO=price_pro_xxx
STRIPE_PRICE_BUSINESS=price_business_xxx
STRIPE_TRIAL_DAYS=7
```

## Repository — nuovi metodi

```python
class CoreRepository:
    async def get_organization_billing(org_id: UUID) -> dict
    async def update_organization_billing(org_id: UUID, billing_data: dict)
    async def set_subscription_status(org_id: UUID, status: str)
    async def increment_message_usage(org_id: UUID) -> int
    async def reset_message_usage(org_id: UUID, period_start: datetime, period_end: datetime)
    async def process_stripe_event(event_id: str, org_id: UUID) -> bool  # returns True if new
    async def update_plan_limits(org_id: UUID, plan: str)
```

## Test

- Testcontainers PostgreSQL + Stripe mock (test mode + fixture eventi)
- `test_create_checkout_session`: verifica creazione session con trial
- `test_create_portal_session`: verifica creazione portal
- `test_webhook_checkout_completed`: evento mockato → org passa a trialing
- `test_webhook_invoice_paid`: evento mockato → reset contatore
- `test_webhook_subscription_updated`: upgrade/downgrade → limiti aggiornati
- `test_webhook_subscription_deleted`: cancellazione → status canceled
- `test_webhook_signature`: richiesta senza firma → 400 (payload non verificabile)
- `test_webhook_idempotency`: evento duplicato → ignore
- `test_check_plan_limits_ok`: org active, sotto limite → passa
- `test_check_plan_limits_exceeded`: org active, sopra limite → 429
- `test_check_plan_limits_past_due`: org past_due → 402
- `test_check_plan_limits_trialing`: org in trial → passa
- `test_increment_message_usage`: atomico, cross-tenant isolation
- `test_business_unlimited`: org business, usage alto → passa
- `test_reset_on_invoice_paid`: contatore azzerato al rinnovo
- `test_usage_endpoint`: GET /api/billing/usage → dati corretti

## Non in scope

- Frontend pricing page / dashboard billing UI (solo API backend)
- Stripe metered billing API (troppo complesso per piani flat)
- Report periodici di fatturazione (Stripe dashboard)
- Multi-currency (solo EUR)
- Coupon / sconti
