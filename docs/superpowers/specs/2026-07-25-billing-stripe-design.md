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
            → invoice.paid verificata → aggiorna piano/limiti
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
| `checkout.session.completed` | Risolve e associa l'intent durevole al Customer/subscription; conserva soltanto il trial applicativo originale. Non concede accesso pagato e non sovrascrive una subscription già nota. | Dedup su `event_id`; lifecycle separato dal cursore entitlement |
| `invoice.paid` | Dopo verifica del pagamento, servizio mensile e binding: attiva il piano e aggiorna il periodo dalla linea; reset solo per prima inizializzazione verificata o nuovo periodo. | Dedup e audit nella stessa transazione del lock organizzazione |
| `invoice.payment_failed` | Setta `subscription_status='past_due'`, logga su `audit_log` | Dedup su `event_id` |
| `customer.subscription.updated` | Conserva diritti già verificati sullo stesso piano/periodo oppure restringe lo stato; non cambia piano, limiti, periodo o consumo. Trial solo originario, mai esteso. Catalogo positivo invalido o pagamento mancante: 503 prima di dedup/cursore. | Dedup su `event_id`; lock organizzazione |
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

**Cambio piano:** uno snapshot `customer.subscription.updated` non concede né modifica il piano pagato. Una fattura pagata e verificata può assegnare il piano del servizio mensile e il relativo periodo; prorazioni e cambi non riconciliabili rimangono da certificare prima del rilascio. Non dichiarare upgrade/downgrade completo sulla sola ricezione dello snapshot.

**Avvisi soft block:** a 80%, 90%, 100% del limite, risposta 429 include header `X-Quota-Warning: 80|90|100`. Dashboard (frontend futuro) può leggere header e mostrare warning.

## Stripe API — endpoint

### `POST /api/billing/create-checkout-session`

Crea Stripe Checkout Session in modalità `subscription`, con `line_items` basate sul `price_id` mensile e `client_reference_id = organization_id`. `subscription_data.trial_end` deve restare almeno 48 ore nel futuro secondo Stripe; l'applicazione aggiunge un margine tecnico di cinque minuti per la latenza e la precisione al secondo. Se il trial applicativo è attivo ma resta meno di questa soglia, non creare intent o sessioni Stripe e non chiedere una carta: il trial applicativo conserva la scadenza originale e il checkout a pagamento sarà disponibile dopo la sua scadenza. Con almeno 48 ore e cinque minuti residui, usa la scadenza assoluta originale del signup, `payment_method_collection=if_required` e `subscription_data.trial_settings.end_behavior.missing_payment_method=cancel`; non calcolare mai un nuovo trial di sette giorni dal checkout. Dopo la scadenza del trial, il checkout richiede un metodo di pagamento (`payment_method_collection=always`) e non invia `subscription_data.trial_end`. Il valore `required` non appartiene al contratto di questo parametro in Stripe 10.12.0 / API 2024-06-20. La Checkout Session scade al massimo dopo un'ora e comunque non oltre la scadenza del trial. Un intent non risolto senza URL, dopo un timeout ambiguo, può essere ripetuto solo con customer, parametri congelati e la stessa chiave Stripe: Stripe restituisce l'esito memorizzato oppure rivalida la richiesta, senza avviare un secondo intent o modificare la scadenza del trial. L'intent rimane bloccato senza limite di tempo finché un webhook firmato non ne conferma il completamento o la scadenza; in questo modo un evento perso non riapre un secondo checkout e la risoluzione manuale resta necessaria se Stripe non conferma l'esito. L'intervallo annuale resta disabilitato nel profilo `commercial_bootstrap`. `success_url` e `cancel_url` devono restare sul dominio dashboard configurato.

**Runbook per intent unresolved:** il sistema non sblocca automaticamente un'organizzazione quando la Checkout Session scade localmente. Prima di una risoluzione manuale, un operatore autorizzato deve verificare in Stripe, usando `checkout_intent_id` e il Customer congelato, lo stato della Session e l'eventuale subscription; deve poi riconciliare l'evento firmato o registrare una riparazione auditata. Non cancellare né marcare resolved l'intent basandosi soltanto su timeout, assenza del webhook o scadenza locale: l'organizzazione resta in fail-closed finché l'esito provider è verificato.

La quota trial resta limitata a 150 risposte. La relazione fra esaurimento della quota e disponibilità del checkout appartiene al contratto A04; finché il contratto non è approvato, non dedurre né implementare una conversione automatica o un'estensione del trial.

**Auth:** `require_ruolo("owner")`

### `POST /api/billing/create-portal-session`

Crea Stripe Customer Portal session per self-service (cambio carta, upgrade/downgrade, cancellazione).

**Auth:** `require_ruolo("owner")`

### `POST /api/billing/webhook`

Endpoint pubblico (senza auth API) che riceve eventi Stripe. Verifica firma. Dedup su `event_id`. Processa eventi e aggiorna DB.

Il lifecycle Checkout (`completed`, `async_payment_succeeded`, `expired`) risolve l'intent senza avanzare il cursore degli eventi di entitlement. Un completamento ritardato per una subscription già associata conserva stato, trial, periodo e consumo. Gli eventi di pagamento e subscription sono serializzati per organizzazione; una subscription cancellata resta terminale.

Una fattura pagata arrivata prima del completamento Checkout deve provare il binding tramite `subscription_details.metadata.checkout_intent_id`, intent persistito, Customer e prezzo mensile congelati. Il solo Customer non autorizza una subscription nuova. Il pagamento deve avere importo positivo in EUR, periodo valido e piano riconosciuto: la fattura a zero del trial non attiva l'accesso pagato. I pareggi temporali paid/failed dello stesso invoice sono risolti con le prove durevoli dell'audit; un invoice già pagato non torna fallito. Conflitti tra invoice differenti o binding non verificabili restituiscono 503 senza dedup o mutazioni, per consentire retry e riconciliazione. Non risolvere tali conflitti basandosi sull'ordine lessicografico degli event ID. La riconciliazione provider reale resta un gate A02 con credenziali test autorizzate; la simulazione locale non la certifica.

Il periodo del servizio deriva dall'unica riga ricorrente mensile non prorata, con prezzo configurato, quantità uno e subscription corrispondente. I campi top-level `Invoice.period_start/end` descrivono il periodo di utilizzo precedente e non la copertura acquistata ([API Stripe 2024-06-20](https://docs.stripe.com/api/invoices/object?api-version=2024-06-20)). Righe annuali, sconosciute, ambigue o soltanto di prorata non autorizzano un nuovo entitlement; richiedono riconciliazione. Fatture paginate, righe di servizio gratuite con commissioni separate e combinazioni di righe prorata richiedono riconciliazione. Il primo pagamento può sostituire un periodo locale provvisorio soltanto con intent verificato, marker persistito di inizializzazione unica e assenza di pagamenti precedenti della subscription; uno stato trialing/incomplete da solo non autorizza un nuovo azzeramento. Una fattura di un periodo precedente non riattiva una sottoscrizione sospesa per il periodo attuale.

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
- `test_webhook_subscription_updated`: nessun upgrade/reset da snapshot; diritti esistenti preservati, restrizioni applicate, catalogo mensile rigoroso, trial originario e pareggi sulla scadenza
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

### Verifica circoscritta Stripe Test — 18 ottobre 2026

SDK effettivo `stripe==10.12.0`, richieste con API `2024-06-20`. Catalogo Test letto e verificato: tre prezzi attivi EUR 29/69/149, intervallo mese, quantità ricorrente uno. Nove Checkout Session sintetiche accettate (tre piani, trial originale di sette giorni, 49 ore residue e checkout esplicito senza trial); replay della stessa chiave idempotente restituisce la stessa Session. Il provider rifiuta `subscription_data.trial_end` con 47 ore, 23 ore e 30 minuti residui. Il confine locale di 48 ore resta differito per il margine tecnico documentato, senza estensione del trial.

Nessuna Session completata, carta, subscription o addebito. Sessioni sintetiche scadute e Customer sintetico rimosso. Nessuna modifica al catalogo o alle impostazioni dell'account; nessun endpoint webhook Test configurato. Credenziali non salvate nel repository o nelle evidenze. Questa prova certifica i parametri Checkout, non rinnovi/cancellazioni reali, consegna webhook, riconciliazione completa o quota atomica A04. Suite locale finale: 172 test passati, una deprecazione SDK; include PostgreSQL sintetico, permutazioni/duplicati/concorrenza e firma webhook reale.


## Consolidamento A02 — 18 ottobre 2026

Gli snapshot subscription positivi richiedono una sola riga completa (`has_more=false`), quantità intera uno, Price mensile configurato univocamente, EUR, importo nominale corretto e periodo intero ordinato. Nessun fallback Product o Price annuale. `active` conserva soltanto piano e periodo già attivi; `trialing` conserva solo il trial applicativo esistente senza alterare quota o limiti. Pareggi temporali possono accorciare una scadenza trial verificata, mai estenderla. Gli stati restrittivi valgono anche con catalogo ignoto, senza allentare stati più severi. Gli aggiornamenti di piano/limiti/periodo e reset rimangono di competenza della fattura verificata. Nuovi diritti incerti rispondono 503 prima di dedup e avanzamento del cursore.

Il [runbook operativo](../../operations/billing-release-runbook.md) distingue parametri Checkout verificati, consegna locale con oggetti Stripe Test, gate pubblici e quota atomica A04 ancora aperti.
