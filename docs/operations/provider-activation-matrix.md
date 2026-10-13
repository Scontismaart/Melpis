# Melpis Provider Activation Matrix & Readiness

Questo documento analizza lo stato dei quattro provider esterni critici per Melpis (Stripe, Meta WhatsApp, AI Provider, Google Calendar & Reviews) e specifica le condizioni necessarie prima di qualsiasi attivazione in ambiente reale.

Nessun provider Live deve essere attivato o fatturato senza previa approvazione del proprietario.

---

## 1. Classificazione Livelli di Verifica

Per evitare affermazioni non dimostrate, ogni controllo è classificato secondo tre livelli rigorosi:

- **MOCK PASS**: il codice gestisce il contratto, le firme, i payload e gli errori tramite fixture sintetiche e mock di libreria in CI/test locali.
- **LOCAL SANDBOX PASS**: il flusso è stato eseguito end-to-end con un database PostgreSQL locale disposable, container Valkey e credenziali di test/sandbox.
- **REAL PROVIDER E2E PASS**: il flusso è stato eseguito con eventi scambiati realmente con le API del fornitore esterno (Stripe Test Mode, Meta WhatsApp Cloud API Sandbox, account Groq reale, Google OAuth consent screen).

---

## 2. Matrice Generale Provider

| Provider | Codice Pronto | Simulazioni Passate (MOCK) | Sandbox Locale Passata | Live Configurato | Credenziali Mancanti | Azioni Manuali Necessarie | Owner Dipendenza | Criterio Esatto per GO |
|---|---|---|---|---|---|---|---|---|
| **Stripe Billing** | **SÌ** (fix PR #82 incluso) | **SÌ** (64 PASS in `tests/core/billing/`) | **SÌ** (Postgres + Valkey concurrency) | **NO** (fail-closed, solo Test autorizzato) | `sk_live_...`, `pk_live_...`, `whsec_...`, Price ID Live | 1. Creare 3 prodotti mensili su Stripe Dashboard.<br>2. Configurare webhook endpoint Live con secret firmato.<br>3. Configurare Customer Portal su Dashboard Stripe. | OWNER | Webhook firmato funzionante, 3 Price ID mensili verificati in `.env.production`, nessun addebito reale di test. |
| **Meta WhatsApp Cloud API** | **SÌ** | **SÌ** (175 PASS in `tests/whatsapp/`) | **SÌ** (inbox durabile, dedup, worker, outbox) | **NO** (fail-closed sandbox) | `WHATSAPP_API_TOKEN` Live, Phone Number ID Live, WABA ID | 1. Completare verifica aziendale su Meta Business Manager.<br>2. Associare numero telefonico di produzione.<br>3. Verificare webhook su Meta Developer Portal. | OWNER | Webhook registrato con verify token, callback HTTP 200 rapido, messaggio di test ricevuto solo su numero allowlist. |
| **AI Provider (Groq / OpenRouter)** | **SÌ** | **SÌ** (65 PASS in `tests/unit/test_cost_policy.py`, guardrails) | **SÌ** (ZDR check, allowlist, fallback a costo €0) | **NO** (chiavi API non inserite) | `GROQ_API_KEY` (oppure `OPENROUTER_API_KEY`) | 1. Creare account Groq gratuito.<br>2. Verificare che non sia associata carta di credito.<br>3. Impostare `GROQ_FREE_ACCOUNT_CONFIRMED=true` e `GROQ_ZDR_CONFIRMED=true`. | OWNER | `release_preflight.py` valida la chiave, zero fallback a pagamento, risposte sicure con guardrail attivi. |
| **Google Calendar** | **SÌ** (OAuth e sync) | **SÌ** (`tests/unit/test_google_integration_flags.py`) | **SÌ** (mock OAuth, conflict handling) | **NO** (volutamente disabilitato) | Google OAuth Client ID / Secret di produzione | Lasciare `GOOGLE_CALENDAR_ENABLED=false` al lancio pre-certificazione. | OWNER | Attivazione post-lancio solo dopo approvazione esplicita e verifica quote Google Console. |
| **Google Reviews (GBP)** | **PARZIALE** | **SÌ** (`tests/core/reviews/`) | **SÌ** (client API mock) | **BLOCCATO ESTERNAMENTE** | Google Business Profile API Access | **BLOCCANTE ESTERNO**: Google ha assegnato quota 0 (`RATE_LIMIT_EXCEEDED`). Lasciare `GOOGLE_BUSINESS_ENABLED=false`. | GOOGLE / OWNER | Richiesta aumento quota approvata da Google. Non pubblicizzare Reviews come operativo. |

---

## 3. Dettaglio Provider e Controlli di Sicurezza

### 3.1 Stripe
- **Funzionalità verificate**:
  - Checkout session durevole e idempotente.
  - Risoluzione race condition webhook `checkout.session.completed` prima del commit di `session_id` (PR #82).
  - Stripe Customer Portal per aggiornamento pagamento e disdetta.
  - Sospensione immediata del bot su eventi `past_due`, `unpaid`, `canceled`.
  - Ripristino automatico dell'entitlement su `invoice.paid` solo per Price ID configurati.
  - Prevenzione ampliamento quote da eventi con Price ID non autorizzati.
- **Opzione di Lancio Mensile-Only**:
  - Abilitata con `STRIPE_ANNUAL_PRICING_ENABLED=false`.
  - Richiede solo 3 Price ID:
    - Essenziale (€29/mese): `STRIPE_PRICE_STARTER`
    - Crescita (€69/mese): `STRIPE_PRICE_PRO`
    - Scala (€149/mese): `STRIPE_PRICE_BUSINESS`
  - Toggle annuale nascosto su frontend e landing. Nessun Price ID annuale inventato.
- **Gate di Rilascio**:
  - Test Mode collaudato con carta di test Stripe (`4242...`).
  - Creazione credenziali Live nel secret store host solo al momento del cutover.

### 3.2 Meta WhatsApp Cloud API
- **Funzionalità verificate**:
  - Verifica webhook endpoint (`hub.mode=subscribe`, `hub.verify_token`, `hub.challenge`).
  - Firma crittografica HMAC-SHA256 (`X-Hub-Signature-256`) verificata con secret applicativo.
  - Ingestione inbox durabile prima dell'invio HTTP 200 a Meta (latenza < 150ms).
  - Worker asincrono separato per elaborazione inbox e retry esponenziale.
  - Gestione consenso e comando `STOP` in transazione atomica (disiscrizione immediata).
  - Finestra di conversazione 24h Meta rispettata.
  - Escalation verso operatore umano con visibilità immediata nella dashboard staff.
- **Gate di Rilascio**:
  - Configurazione `WHATSAPP_TEST_RECIPIENTS` ristretta a numeri interni del team fino all'approvazione del lancio pubblico.
  - Nessun invio a contatti esterni o clienti reali durante il bootstrap.

### 3.3 AI Provider (Groq / OpenRouter)
- **Funzionalità verificate**:
  - Politica di costo rigida `LLM_COST_POLICY=free_only` enforced a livello di `startup_guard.py`.
  - Zero Paid Fallback: se il provider free fallisce o supera il rate limit, l'app scala a un messaggio di attesa o all'operatore umano. Non chiama mai endpoint a pagamento.
  - Accounting dei token e attribuzione dei costi stimati per organizzazione e conversazione.
  - Guardrail pipeline: sanitizzazione prompt, blocco tentativi di prompt injection, validazione disponibilità appuntamenti (nessuna prenotazione su slot non disponibili).
- **Gate di Rilascio**:
  - Conferma manuale account Groq Free (`GROQ_FREE_ACCOUNT_CONFIRMED=true`) e Zero Data Retention (`GROQ_ZDR_CONFIRMED=true`).

### 3.4 Google Calendar & Google Reviews
- **Stato al Lancio Commerciale**:
  - Entrambi i flag sono impostati a `false`:
    ```ini
    GOOGLE_CALENDAR_ENABLED=false
    GOOGLE_BUSINESS_ENABLED=false
    ```
  - Lo `startup_guard.py` e lo script `release_preflight.py` bloccano l'avvio in produzione se uno di questi flag è impostato a `true` senza autorizzazione.
  - **Google Reviews è un bloccante esterno**: l'API Google Business Profile ha quota 0 su console Google. Non deve essere promossa o commercializzata come attiva al Day 1.
