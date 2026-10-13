# Melpis pre-launch mission 1 readiness audit

Audit baseline: `origin/main` at `2994fae7ea54094482a40374ea46c08aae8d0407` (2026-10-12).
This document records repository evidence, not a claim that production systems
or external provider configuration have been inspected. No remote database,
production secret store, Stripe Live account, or live provider was accessed.

`TESTATO` names repository tests unless it explicitly says the test was run in
this workstream. `.env.production.example` is a template; it does not establish
the state of a deployment. `CONFIGURATO` therefore remains **non verificato**
for real environments unless a documented provider checkpoint below says
otherwise.

## Readiness matrix

| Requisito | Implementato | Testato | Configurato | Blocco | Evidenza |
|---|---|---|---|---|---|
| Prezzi Stripe mensili e annuali | I piani e i limiti mensili sono definiti; prima del fix il profilo commerciale richiedeva sei Price ID. L'annuale non è approvato. | `tests/core/billing/test_plans_pricing.py`, `tests/core/billing/test_price_to_plan.py`, release preflight tests. | Valori reali non verificati; il template non prova che i Price ID esistano. | Usare i tre Price ID mensili configurati; l'annuale resta spento finché non approvato. | `src/core/billing/plans.py`, `src/core/startup_guard.py`, `scripts/release_preflight.py`, `docs/CHECKLIST-LANCIO-DEFINITIVA.md`, `docs/operations/billing-release-runbook.md`. |
| Checkout, webhook, Portal e cancellazione | Checkout/Portal richiedono owner e MFA; firma webhook e ledger eventi; gli stati di cancellazione sospendono l'organizzazione. Baseline aveva mismatch dell'origine di ritorno e poteva estendere l'entitlement con Price ID sconosciuto; fix e test in questa PR. | `tests/core/billing/test_routes.py`, `test_routes_p0.py`, `test_webhook_handler.py`, `tests/concurrency/test_stripe_cancellation_integration.py`; esecuzione finale da riportare sotto. | Nessun account Stripe verificato. | Stripe Test e configurazione effettiva di webhook/Portal restano gate manuali. Nessuna attivazione Live in questo workstream. | `src/core/billing/routes.py`, `src/core/billing/webhook_handler.py`, `web/dashboard-settings.js`, runbook billing. |
| Registrazione | Signup anti-enumerazione, rate limit, password temporanea privata, PKCE e provisioning dopo conferma email. | `tests/core/auth/test_register_policy.py`, `tests/unit/auth/test_email_confirmation_callback.py`. | Supabase Auth e recapito reali non verificati. | Provare email e callback con l'URL esatto prima della beta chiusa. | `src/core/auth/register.py`, `docs/operations/supabase_signup_email_confirmation.md`. |
| Conferma email | Callback GET/POST nello stesso browser; PKCE monouso, identità confermata e provisioning idempotente. | `tests/unit/auth/test_email_confirmation_callback.py` copre callback, replay, concorrenza e retry. | Template email, SMTP e redirect URL effettivi non verificati. | Uno scanner email può consumare il link hosted; il runbook richiede una prova reale prima della beta chiusa. | `src/core/auth/register.py`, `docs/operations/supabase_signup_email_confirmation.md`. |
| Login e logout | BFF con cookie HttpOnly/Secure, CSRF, controlli lifecycle e revoca locale al logout. | `tests/core/auth/test_bff.py`, `tests/core/auth/test_access_revocation.py`. | Dominio, TLS e sessioni reali non verificati. | Smoke HTTPS multi-worker e restart manuale. | `src/core/auth/routes.py`, `src/core/auth/bff.py`, `.env.production.example`. |
| Recupero password | Risposta uniforme a `/recover`; token verificato, policy password e account lifecycle a `/reset`. | `tests/core/auth/test_password_recovery.py`. | Email di recupero e redirect reali non verificati. | Provare consegna e link con il provider di posta effettivo. | `src/core/auth/routes.py`, test password recovery. |
| Refresh e revoca sessioni | Rotazione cookie, identità invariata, denylist condivisa e revoca fail-closed su Redis indisponibile. | `tests/core/auth/test_bff.py`, `tests/unit/auth/test_token_denylist.py`, `tests/core/auth/test_access_revocation.py`. | Valkey/Redis reale non verificato. | Verificare rotazione e revoca dopo restart e su ogni replica. | `src/core/auth/routes.py`, `src/core/auth/denylist.py`, `.env.production.example`. |
| MFA | TOTP enrollment/challenge/verify e step-up `aal2`; richiesto per billing, GDPR e credenziali sensibili. | `tests/core/auth/test_mfa_routes.py`, `tests/unit/auth/test_require_mfa.py`. | Enrollment reale non verificato. | Provare enrollment e step-up sul target di rilascio. | `src/core/auth/routes.py`, `src/core/auth/dependencies.py`. |
| Isolamento multi-tenant | JWT verificato più membership server-side; query scoping applicativo e RLS aggiuntiva. | `tests/core/test_tenant_scoping.py`, `tests/core/auth/test_security_hardening.py`, `tests/core/auth/test_access_revocation.py`. | Stato del database di produzione non verificato. | Applicare/revieware migrazioni autorizzate e provare due tenant sul DB target; questo audit non ha contattato DB remoti. | `src/core/auth/dependencies.py`, `src/core/db/scoping.py`, `docs/DEPLOY.md`. |
| Inviti Team | L'invito non concede membership; token hash, email verificata, limiti piano, ruolo inviter e accettazione atomica. | `tests/unit/api/test_team_invitations.py`, `tests/unit/api/test_team_routes.py`, test frontend Team. | SMTP e dominio mittente reali non verificati. | Consegna e accettazione end-to-end restano manuali; senza SMTP l'endpoint fallisce chiuso. | `src/core/team_invitations.py`, `src/api/routes/team.py`, `.env.production.example`. |
| Export GDPR | Owner+MFA, dati scoping per organizzazione, link monouso TTL 15 minuti. In questa PR il token store di produzione diventa fail-closed se Redis non è disponibile; dev/test conserva il fallback in memoria. | `tests/core/gdpr/test_routes.py`, `tests/core/gdpr/test_token_store.py`; test token store eseguito in questo workstream: 7 PASS. | Redis reale non verificato. | Preflight/startup richiedono Redis; disponibilità Redis resta gate operativo. | `src/core/gdpr/routes.py`, `src/core/gdpr/token_store.py`, `.env.production.example`. |
| Cancellazione organizzazione | Owner+MFA, audit e propagazione ai connettori Airtable/Softr prima del delete locale. La risposta ora descrive separatamente il completamento locale e gli esiti per servizio; non dichiara che ogni servizio esterno abbia cancellato i dati. | `tests/core/gdpr/test_routes.py`, `tests/core/gdpr/test_propagation.py`. Test route mirato da eseguire nel pass finale. | Non è stata verificata la presenza di copie o credenziali Airtable/Softr nel deployment. | Se esistono copie esterne attive, manca un ledger durevole di retry/alert; confermare i sistemi effettivamente usati e trattare i risultati `false` come cancellazione esterna pendente. | `src/core/gdpr/routes.py`, `src/core/gdpr/propagation.py`, `docs/operations/pre-release-secret-rotation.md`. |
| Retention dati | Soft delete messaggi a 60 giorni, purge dopo 30 giorni; simulazioni 30 giorni, history inattiva 24h, dedup webhook 7 giorni. | `tests/whatsapp/test_retention.py`, inclusi job e purge. | Scheduler e policy backup reali non verificati. | Verificare scheduler, backup, retention e copie provider; la colonna storica `message_retention_days` non è applicata dal job. | `src/core/retention_job.py`, `src/core/db/repositories/message_repo.py`, `src/core/gdpr/routes.py`, DPA in `src/core/gdpr/routes.py`. |
| Consenso e STOP | Consenso effettivo, evento e audit sono salvati in transazione; gli invii ricontrollano opt-out prima dell'invio. | `tests/whatsapp/test_consent.py`, `tests/unit/test_inbound_processing_service.py`, `tests/concurrency/test_inbound_secondary_flows_p0.py`. | Stato Meta reale e destinatari autorizzati non verificati. | Smoke sandbox Meta: STOP, replay e successivo invio negato. | `src/core/db/repositories/contact_repo.py`, `src/core/inbound/service.py`, `src/whatsapp/service.py`. |
| ACK webhook Meta e idempotenza ingest | Il webhook persiste l'inbox prima dell'ACK; worker e dedup gestiscono retry separatamente dalla generazione AI. | Copertura in `tests/whatsapp/`, `tests/core/` e test concurrency; scenario live non eseguito qui. | Callback e segreti Meta effettivi non verificati. | Testare firma reale, ACK rapido e worker con sandbox quando autorizzato. | `src/whatsapp/router.py`, `src/whatsapp/inbox.py`, `docs/CHECKLIST-LANCIO-DEFINITIVA.md`. |
| Google Calendar | OAuth, sync/eventi e idempotenza sono implementati. Il guard di produzione richiede la feature disattivata al lancio. | `tests/unit/test_google_integration_flags.py`, `tests/core/calendar/`, oltre alle prove provider descritte nel checkpoint Phase 7. | Flag reale volutamente disabilitato; credenziali reali non ispezionate. | Prima di attivare servono approvazione esplicita e gate di certificazione; non abilitato da questa missione. | `src/core/startup_guard.py`, `scripts/release_preflight.py`, `.env.production.example`, `docs/phase7-integrations-qa.md`. |
| Google Reviews | OAuth e client API esistono; lo stato operativo richiede una sincronizzazione riuscita. | Suite `tests/core/reviews/` e regressioni Reviews Phase 7. | OAuth QA completato storicamente; Google Business Profile API ha risposto 429 `RATE_LIMIT_EXCEEDED`, quota 0. | Provider/API quota è bloccante: non leggere recensioni e non pubblicizzare Reviews come pienamente operativo. | `docs/melpis-v1-phase78-readiness.md`, `docs/phase7-integrations-qa.md`, `src/core/startup_guard.py`. |

## Stato test di questa missione

Esecuzioni verificate con successo nel workstream:

- `python -m pytest tests/core/billing/`: **58 PASS** (piani, price-to-plan mensile/annuale, route checkout/portal, sospensione, quote trial, deposito webhook, webhook handler con intent durevole).
- `python -m pytest tests/core/gdpr/`: **33 PASS** (propagazione cancellazione esterna, route export/download/delete/dpa, token store fail-closed con Redis in produzione).
- `python -m pytest tests/unit/test_release_preflight.py tests/unit/billing/test_reliability_controls.py tests/core/auth/test_demo_mode_guard.py tests/unit/test_google_integration_flags.py`: **141 PASS** (preflight mensile-only/annuale, guardie di avvio, controlli affidabilità billing, flag Google integrati).
- `python -m pytest tests/core/auth/test_register_policy.py tests/unit/auth/test_email_confirmation_callback.py`: **69 PASS** (signup, callback email, anti-enumerazione, PKCE).
- `python -m pytest tests/core/auth/test_bff.py tests/core/auth/test_access_revocation.py tests/core/auth/test_password_recovery.py tests/core/auth/test_mfa_routes.py tests/unit/auth/test_require_mfa.py tests/unit/auth/test_token_denylist.py`: **95 PASS** (sessioni BFF, revoca immediata, recovery password, enrollment/challenge TOTP MFA, denylist token).
- `python -m pytest tests/core/test_tenant_scoping.py tests/core/auth/test_security_hardening.py tests/unit/api/test_team_invitations.py tests/unit/api/test_team_routes.py tests/whatsapp/test_retention.py tests/whatsapp/test_consent.py tests/unit/test_inbound_processing_service.py tests/concurrency/test_stripe_cancellation_integration.py`: **81 PASS** (scoping multi-tenant, CSRF/cookie security, inviti team atomici, retention job, opt-out STOP in transazione, cancellazione Stripe concurrency).
- `npm run lint:web`: **PASS** (0 errori ESLint).
- `npm run test:web`: **393 PASS / 0 FAIL** (zero inline style attributes su landing/statiche, selettore annuale nascosto fail-closed, auth/MFA/Team, sanitizzazione DOM, integrazione calendari/recensioni).
- `npm run test:i18n`, `npm run audit:assets`, `npm run audit:images`, `npm run audit:links`, `npm run audit:seo`: **TUTTI PASS**.

## Findings storici riconciliati

- Il limite Scala/Business corrente è 10.000 messaggi mensili; non riportare il
  vecchio limite errato come aperto.
- Checkout Portal, cancellazione, GDPR export e cancellazione organizzazione
  esistono nel codice attuale; i vecchi report che li descrivevano assenti sono
  superati.
- Consenso STOP e ACK webhook durabile sono implementati e hanno test; la prova
  sul provider reale resta manuale.
- Annuale non approvato: prezzi/Price ID annuali non vanno dedotti dai vecchi
  importi o dai mock di test.

## Gate manuali prima della beta commerciale

1. Verificare i soli tre prodotti/Price ID mensili in Stripe Test, webhook
   firmato, Portal e cancellazione; configurare eventuali valori Live solo nel
   secret store dopo l'approvazione del titolare. Nessun addebito di prova.
2. Eseguire i flussi reali email signup, login/MFA, recovery, team invitation,
   tenant isolation, export/delete e STOP in ambiente sandbox autorizzato.
3. Verificare Redis/Valkey condiviso, backup cifrato e restore drill, SMTP,
   database/migrazioni e alerting sul target operativo senza usare credenziali
   reali in questa missione.
4. Mantenere Calendar e Reviews disabilitati finché la certificazione e le
   condizioni del provider non sono approvate; Reviews è bloccato da quota API
   Google zero.
5. Confermare se Airtable/Softr conservano dati tenant. Se sì, definire owner,
   retry durevole e alert per cancellazione esterna prima di dichiarare il
   flusso completo.
