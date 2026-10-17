# whatsapp-ai-responder

Risponditore WhatsApp AI multi-tenant: un SaaS che risponde automaticamente ai messaggi dei clienti via LLM, con guardrails, inbox con intervento umano (HITL), automazione delle recensioni Google, prenotazioni, wizard di onboarding, funzioni GDPR e supporto multilingua (it/en/fr/de/es).

Il prodotto permette a ogni tenant di collegare i propri canali WhatsApp e Instagram e di far gestire le conversazioni a un team di agenti LLM, configurato sulla base delle conoscenze aziendali (documenti indicizzati in RAG), con classificazione dell'intento, limiti di lunghezza della risposta, cache semantica delle FAQ e possibilità di escalation al gestore umano. Il tutto è gestito da una dashboard web (FastAPI + vanilla JS) con login, fatturazione Stripe, sync Google Calendar e revisione delle conversazioni.

## Architettura

Il sistema è composto da 4 servizi Docker, tutti dalla stessa immagine:

- **api** — FastAPI: REST API, dashboard, webhook per WhatsApp/Instagram (Meta Cloud API) e Google OAuth. Unico servizio che espone una porta pubblica.
- **worker-inbound** — processa i messaggi in ingresso usando claim transazionali sul database (ogni messaggio è "preso in carico" da un solo worker, evitando doppie risposte).
- **worker-retry** — ritenta le consegne fallite dei messaggi in uscita.
- **supervisor** — rete di sicurezza indipendente: libera i claim stale (reaper) anche quando i worker che li avevano presi in carico sono morti.

Il database è **Supabase Postgres** con estensione **pgvector**. Il profilo di lancio usa solo modelli ammessi sul tier **Groq FREE**, con attestazione esplicita dell'account e nessun fallback a pagamento. Meta opera esclusivamente in sandbox. L'immagine Docker gira con utente **non-root**.

## Setup sviluppo

Prerequisiti: **Python 3.12**, **Docker Desktop** (per PostgreSQL locale) e **Node.js** (lint e test frontend).

Prima di configurare i provider, creare e attivare un ambiente virtuale Python
3.12 e installare con `python scripts/reviewed_runtime.py install`.
Il comando genera una derivata interna della wheel ufficiale CrewAI 1.15.4,
verifica gli hash e preserva l'extra LiteLLM, usando MCP 1.30.0. Non usare
direttamente `pip install -r requirements.txt`: il vincolo MCP upstream non
ammette il fix. La derivata non è una release o approvazione di compatibilità
di CrewAI. [Provenienza, audit e aggiornamento](docs/REVIEWED-RUNTIME.md).

1. Copiare `.env.example` in `.env` e compilare le variabili richieste. In particolare:
   - `ENCRYPTION_KEY`: generare una chiave Fernet con `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
   - `META_APP_SECRET` e `META_VERIFY_TOKEN`: obbligatorie per il webhook WhatsApp; senza di esse l'app parte comunque ma il webhook non viene montato e Meta non può raggiungerti.
2. Migrazioni database: i file SQL in `src/core/db/migrations/` si applicano in ordine via `psql`; il workflow CI `.github/workflows/migrations.yml` mostra il pattern esatto. Vengono applicati anche `src/whatsapp/schema.sql`, `src/core/db/schema.sql` e `src/core/db/triggers.sql`.
3. Esecuzione locale: `uvicorn src.api.main:app --reload --no-proxy-headers --no-access-log`.
4. Stack completo con Docker: `docker compose --profile dev up -d` (il profilo `dev` aggiunge `postgres-dev` per lo sviluppo locale).

## Test

Suite completa su Windows:

```
$env:PYTHONUTF8=1; python -m pytest -q
```

Su Unix: `PYTHONUTF8=1 python -m pytest -q`. Alcuni test richiedono Docker (Postgres via testcontainers). Check di sintassi JS della dashboard: `node --check web/app.js`.

## Deploy

- `docs/DEPLOY.md` — deploy sandbox a costo zero, preflight, rollback e verifiche esterne.
- `docs/CHECKLIST-PRE-LANCIO.md` — cosa verificare prima di andare in produzione.

## Documentazione

- `docs/DEPLOY.md` — deploy a costo zero con immagini immutabili e TLS.
- `docs/CHECKLIST-PRE-LANCIO.md` — checklist viva delle verifiche pre-lancio.
- `docs/GUARDRAILS.md` — controlli di qualità e sicurezza sulla risposta (intent classifier, validatore post-LLM, cache FAQ, A/B test prompt).
- `docs/SETUP-INSTAGRAM.md` — setup del canale Instagram DM riusando la stessa app Meta del WhatsApp Cloud API.
- `docs/SMOKE-ONBOARDING.md` — smoke test manuale del wizard di onboarding (org-scoped, RLS, preview con LLM + RAG).

## Note sicurezza

I segreti vanno solo nel file `.env`, che è in `.gitignore` ed escluso dall'immagine. Non committare chiavi reali. Il profilo di lancio ammette soltanto i modelli Groq elencati dal controllo costo; l'operatore deve verificare account FREE e condizioni dei dati prima di abilitarlo.
