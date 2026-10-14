# Melpis Pre-Launch Readiness & Consolidation Audit (V1)

**Data di consolidamento**: 13 Ottobre 2026  
**Baseline Repository**: `origin/main` @ `2994fae7ea54094482a40374ea46c08aae8d0407`  
**PR di Riferimento Missione 1**: [#82](https://github.com/Scontismaart/SaaS/pull/82) (`feat(billing): support monthly-only launch and fail-closed readiness controls`)  
**Profilo di Lancio**: Commercial Bootstrap con costi da verificare; eventuali tier gratuiti sono stime condizionali, non garanzie (`commercial_bootstrap`)
**Politica di Rilascio**: Nessun merge automatico su `main`, nessun deploy non autorizzato, nessun addebito reale, nessun provider Live attivato senza autorizzazione esplicita dell'Owner.

---

## 1. Executive Summary

- **STATO**: PARTIAL — scope commerciale V1 definito; gate tecnici/operativi aperti
- **PRIORITÀ**: P0
- **EVIDENZA**: il perimetro commerciale V1 include WhatsApp e Google Calendar in tutti e tre i piani. Il fix webhook Stripe è testato localmente e la CI va verificata sul nuovo head; Google Calendar resta un gate obbligatorio ancora bloccato.
- **AZIONE SUCCESSIVA**: Revisione finale da parte dell'Owner, approvazione delle decisioni commerciali/legali ed esecuzione guidata dei setup manuali nei pannelli web dei provider.
- **RESPONSABILE**: SOL / OWNER

Melpis V1 non è autorizzato al Go-Live finché non sono superati i gate operativi e di provider elencati qui. WhatsApp e Google Calendar sono entrambi requisiti obbligatori del perimetro V1; Calendar resta disabilitato fino alla certificazione end-to-end reale. Google Reviews è escluso dal perimetro V1. Le verifiche del repository non certificano da sole la disponibilità o l'operatività dei servizi esterni.

La CI del repository non certifica da sola il Go-Live. I punti aperti comprendono verifiche del codice, certificazioni dei provider e **gate operativi esterni/azioni manuali dell'Owner**:
1. Approvazione commerciale del lancio **solo-mensile** (senza obbligo di creare listini annuali).
2. Configurazione manuale dei 3 prodotti mensili su Stripe Dashboard e webhook secret Live.
3. Configurazione e test end-to-end reale del numero WhatsApp di produzione.
4. Certificazione end-to-end reale di Google Calendar (OAuth, callback, lettura/scrittura autorizzata e gestione errori); senza esito PASS non è consentito il Go-Live.
5. Creazione account Groq e conferma ZDR, subordinata a disponibilità, quote e condizioni correnti.
6. Inserimento e verifica dei record DNS (`melpis.it`) su Cloudflare.
7. Verifica di disponibilità e costi della VM, quindi staging e deploy solo dopo i gate e le autorizzazioni previsti.

---

## 2. Stato Prodotto V1

- **STATO**: BLOCKED — GO-LIVE GATE (Google Calendar obbligatorio)
- **PRIORITÀ**: P0
- **EVIDENZA**:
  - Piani definiti in `src/core/billing/plans.py`: Essenziale (500 msg/mese, €29), Crescita (2.000 msg/mese, €69), Scala (10.000 msg/mese, €149).
  - Google Calendar è incluso in tutti e tre i piani V1 (Essenziale €29, Crescita €69, Scala €149 al mese). Il copy prezzi viene allineato; l'accesso per piano non deve limitarlo. L'integrazione resta disabilitata in produzione fino alla certificazione end-to-end.
  - I piani V1 non includono l'integrazione Google Reviews; l'entitlement è disattivato in tutti e tre i piani. Il copy promozionale delle recensioni è stato rimosso dal materiale landing aggiornato.
  - Limiti di messaggi e quote rigorosamente tracciati e protetti da sospensione automatica.
  - Dashboard modulare funzionante senza inline styles (`tests/frontend/security.test.cjs` 393 PASS).
  - Landing V1 allineata, con selettore annuale nascosto `[hidden]` fail-closed quando annuale disabilitato.
  - **Landing V2 sospesa** come richiesto: nessun branch o file toccato in `web/landing-v2/`.
- **AZIONE SUCCESSIVA**: Mantenere i piani mensili e il perimetro commerciale documentato; nessun Go-Live finché WhatsApp e Google Calendar non superano i rispettivi test reali e gli altri gate obbligatori.
- **RESPONSABILE**: SOL

---

## 3. Stato Repository / Main

- **STATO**: DONE
- **PRIORITÀ**: P0
- **EVIDENZA**: `origin/main` pulito e allineato al commit `2994fae` (Merge PR #78 `codex/security-replace-python-jose`). Tutti i check e security audit passano su baseline.
- **AZIONE SUCCESSIVA**: Mantenere `main` intonso; integrare le modifiche solo tramite PR esplicitamente approvate dall'Owner.
- **RESPONSABILE**: OWNER / SOL

---

## 4. PR e Fix da Integrare

- **STATO**: BLOCKED — correzioni Step 0 in corso; PR aperte, non autorizzate al merge
- **PRIORITÀ**: P0
- **EVIDENZA**:
  - **PR #82**: `feat(billing): support monthly-only launch and fail-closed readiness controls`
    - Commit `daf3d964c47aa99d4d0106038f76e7535ba8b2a6`.
    - Risolve la race condition nel webhook `checkout.session.completed` quando arriva prima del commit del `session_id`.
    - Supporta `STRIPE_ANNUAL_PRICING_ENABLED=false` richiedendo solo 3 Price ID mensili anziché 6.
    - GDPR token store fail-closed con Redis in produzione.
    - CI GitHub sul commit esaminato: verde; dopo il fix al webhook è necessaria una nuova CI sull'head aggiornato.
    - La consegna tardiva di un evento Stripe valido era scartata in base all'ora di ricezione; correzione e regressione locale aggiunte, `tests/core/billing/test_webhook_handler.py` 27/27 PASS. CI sul nuovo head ancora da eseguire.
  - **PR Documentale / Readiness #83**:
    - Runbook operativi: `docs/operations/email-resend-setup-runbook.md`, `docs/operations/provider-activation-matrix.md`, `docs/operations/incident-response-runbook.md`, `docs/launch/MELPIS-LAUNCH-READINESS.md`.
    - Le correzioni di Step 0 devono passare la CI sull'head aggiornato prima di ogni autorizzazione al merge.
- **AZIONE SUCCESSIVA**: L'Owner esamina e approva il merge delle PR quando desidera procedere al rilascio.
- **RESPONSABILE**: OWNER

---

## 5. Database Supabase

- **STATO**: PARTIAL
- **PRIORITÀ**: P0
- **EVIDENZA**:
  - `qfxwqfavnuufdfpkhxtj` (`Selecta SaaS`) è soltanto un progetto candidato, **non verificato** come database Melpis di produzione.
  - Il progetto Supabase di staging corretto non è ancora identificato/verificato.
  - Migrazioni 001–054 censite nel repository; script di verifica `verify_supabase_restore_fidelity.py` attivo.
  - TLS `verify-full` obbligatorio con certificato di root `BACKUP_PGSSLROOTCERT`.
- **AZIONE SUCCESSIVA**:
  - In Step 1 identificare e confermare esplicitamente i progetti Supabase di staging e produzione, distinguendoli; non eseguire migrazioni, restore o altre operazioni sul progetto candidato prima della conferma del target.
  - Solo dopo l'identificazione, verificare stato e `pgvector`, poi pianificare dump e verifica migrazioni sul database corretto.
- **RESPONSABILE**: OWNER / LUNA

---

## 6. Oracle VM

- **STATO**: PARTIAL (Script e configurazioni pronti; istanza da avviare)
- **PRIORITÀ**: P0
- **EVIDENZA**:
  - `scripts/bootstrap_oracle_vm.sh`: supporta Ubuntu 24.04 sia `amd64` che `arm64` (Ampere A1).
  - Test statici firewall e bootstrap: `tests/scripts/test_oracle_vm_bootstrap.sh` **PASS**.
  - UFW default-deny incoming, porte pubbliche solo 80/443; porta 22 ristretta all'IP/CIDR dell'amministratore.
  - Utente dedicato non-root `melpis` nel gruppo `docker`. Hardening SSH (`PermitRootLogin no`, `PasswordAuthentication no`).
  - Swapfile 2 GiB automatico.
- **AZIONE SUCCESSIVA**: L'Owner crea l'istanza su Oracle Cloud Console (shape Ampere A1 2 OCPU / 12 GB RAM Always Free oppure E2.1.Micro) e lancia `bootstrap_oracle_vm.sh`.
- **RESPONSABILE**: OWNER

---

## 7. Docker / GHCR

- **STATO**: DONE
- **PRIORITÀ**: P0
- **EVIDENZA**:
  - `compose.production.yml`: specifica immagini immutabili pinning via digest `repository@sha256:<hash>`.
  - Servizi isolati: solo Caddy espone porte 80 e 443 sull'host.
  - Processi container in sola lettura (`read_only: true`), tmpfs su `/tmp`, `no-new-privileges: true`, drop delle capability `ALL`.
  - Limiti risorse rigidi: API (1.5 GB RAM, 0.75 CPU), worker-inbound (1 GB RAM, 0.5 CPU), worker-retry (512 MB, 0.25 CPU), supervisor (256 MB, 0.1 CPU), valkey (128 MB, 0.1 CPU).
  - Log rotation configurata su ogni container (`json-file`, max-size 10m, max-file 3).
  - `test_release_containers.py`: 65 PASS.
- **AZIONE SUCCESSIVA**: Estrarre i digest immutabili generati dal workflow CI su `main` al momento del merge.
- **RESPONSABILE**: LUNA / OWNER

---

## 8. Cloudflare DNS / R2

- **STATO**: PARTIAL
- **PRIORITÀ**: P0
- **EVIDENZA**:
  - Caddyfile configurato con `CADDY_SITE_MODE=temporary` per bootstrap pre-DNS e `CADDY_SITE_MODE=final` per il cutover `melpis.it` e `app.melpis.it`.
  - `scripts/backup_supabase_r2.sh` supporta Cloudflare R2 con endpoint S3-compatibile HTTPS, credenziali dedicate a minimi privilegi e verifica digest SHA-256 post-upload.
  - `tests/scripts/test_backup_restore_scripts.sh`: **PASS**.
- **AZIONE SUCCESSIVA**:
  - Creare bucket R2 dedicato (es. `melpis-backups`) su Cloudflare con API token scoped.
  - Creare record DNS A/AAAA temporaneo (es. `melpis-vm.example.net`) per la fase di test, quindi puntare `melpis.it` e `app.melpis.it` alla VM prima del cutover.
- **RESPONSABILE**: OWNER

---

## 9. Resend / Email

- **STATO**: DONE (Architettura e test pronti; configurazione credenziali richiesta)
- **PRIORITÀ**: P0
- **EVIDENZA**:
  - `docs/operations/email-resend-setup-runbook.md` definisce la separazione netta tra Supabase Auth SMTP e Melpis Application SMTP.
  - Resend SMTP via `smtp.resend.com:587` con STARTTLS obbligatorio e timeout 10s.
  - Test suite email: `tests/core/test_email_service.py` (3 PASS), `tests/unit/api/test_team_invitations.py` (18 PASS), `tests/unit/auth/test_email_confirmation_callback.py` (51 PASS).
  - Nessuna credenziale esposta nei log o nei file versionati.
- **AZIONE SUCCESSIVA**:
  - Verificare il dominio `melpis.it` su Resend aggiungendo i 3 record CNAME per DKIM e TXT per SPF.
  - Generare API key Resend e inserirla in `.env.production` come `SMTP_PASSWORD` e nella dashboard Supabase per le email di Auth.
- **RESPONSABILE**: OWNER

---

## 10. Stripe

- **STATO**: DONE (Codice e opzione mensile pronti; configurazione Live richiesta)
- **PRIORITÀ**: P0
- **EVIDENZA**:
  - Supporto lancio mensile-only (`STRIPE_ANNUAL_PRICING_ENABLED=false`) implementato e testato.
  - Race condition pre-commit `session_id` risolta atomicamente con lock `FOR UPDATE` (PR #82).
  - 64 test di billing passati con successo (`tests/core/billing/`).
  - Sospensione immediata su insoluto e Customer Portal con MFA obbligatoria.
- **AZIONE SUCCESSIVA**:
  - Creare i tre Price ID mensili su Stripe Dashboard (€29, €69, €149).
  - Configurare l'endpoint webhook Live e il Customer Portal su Dashboard Stripe.
  - Inserire le chiavi `sk_live_...`, `pk_live_...`, `whsec_...` e i 3 `price_...` in `.env.production`.
- **RESPONSABILE**: OWNER

---

## 11. Meta WhatsApp / Instagram

- **STATO**: DONE (Integrazione pronta; configurazione numero reale richiesta)
- **PRIORITÀ**: P0
- **EVIDENZA**:
  - Ingestione inbox durabile prima dell'ACK HTTP 200; worker asincrono separato.
  - Gestione atomica del consenso e comando `STOP` in transazione.
  - Allowlist di sicurezza `WHATSAPP_TEST_RECIPIENTS` attiva e fail-closed.
  - 175 test WhatsApp passati con successo (`tests/whatsapp/`).
- **AZIONE SUCCESSIVA**:
  - Registrare il numero di telefono aziendale sull'App Meta WhatsApp Cloud.
  - Prima di configurare provider, verificare hostname pubblico scelto, DNS, TLS e routing reverse-proxy. Path applicativo WhatsApp: `/webhooks/whatsapp`; non copiare o configurare URL provider finché l'host pubblico definitivo non è stato verificato.
  - Eseguire smoke test scambiando un messaggio dal numero di test presente in allowlist.
- **RESPONSABILE**: OWNER / LUNA

---

## 12. Groq / OpenRouter

- **STATO**: DONE (Fail-fast free-only pronto; account da confermare)
- **PRIORITÀ**: P0
- **EVIDENZA**:
  - `LLM_COST_POLICY=free_only` enforced da `startup_guard.py` e `release_preflight.py`.
  - Zero Paid Fallback garantito: nessun costo a consumo o fallback a pagamento non autorizzato.
  - Guardrail e validazione intent testati (65 PASS).
- **AZIONE SUCCESSIVA**:
  - Creare account gratuito su console Groq senza carta di credito associata.
  - Generare API key (`gsk_...`) e impostare `GROQ_FREE_ACCOUNT_CONFIRMED=true` e `GROQ_ZDR_CONFIRMED=true` in `.env.production`.
- **RESPONSABILE**: OWNER

---

## 13. Google Calendar obbligatorio / Google Reviews escluso

- **STATO**: BLOCKED — GO-LIVE GATE V1
- **PRIORITÀ**: P0
- **EVIDENZA**:
  - Google Calendar è requisito obbligatorio V1, ma `GOOGLE_CALENDAR_ENABLED=false` finché OAuth, callback, autorizzazioni, sincronizzazione e gestione degli errori non superano QA end-to-end reale e review security.
  - Decisione commerciale dell'Owner: Calendar incluso in tutti e tre i piani mensili (€29/69/149); nessun entitlement per piano deve escludere Essenziale. L'inclusione commerciale non equivale a certificazione operativa.
  - Nessun Go-Live V1 è consentito senza un test reale Google Calendar con esito PASS. La disponibilità delle API/quote e ogni eventuale verifica Google restano da accertare.
  - Google Reviews non è requisito V1 e resta escluso/bloccato; la quota Google Business Profile è 0 (`RATE_LIMIT_EXCEEDED`). Non venderlo né abilitarlo come operativo.
- **AZIONE SUCCESSIVA**:
  - Mantenere Calendar fail-closed/disabilitato fino al superamento dei gate; abilitarlo soltanto nell'ambiente verificato dopo il test reale e l'approvazione prevista.
  - Mantenere Google Reviews fuori da offerta e materiali commerciali V1.
- **RESPONSABILE**: SOL / OWNER / GOOGLE

---

## 14. Backup / Restore

- **STATO**: DONE (Procedure, cifratura e drill automatico pronti)
- **PRIORITÀ**: P0
- **EVIDENZA**:
  - `scripts/backup_supabase_r2.sh`: dump `pg_dump` TLS verify-full, cifratura simmetrica/asimmetrica con `age`, verifica locale decrittografia prima dell'upload, verifica SHA-256 su R2 e retention 7 giorni.
  - `scripts/restore_supabase_drill.sh`: blocco su target non approvati, tripwire anti-produzione, verifica tabelle, vincoli e record minimi ripristinati.
  - `scripts/verify_supabase_restore_fidelity.py`: controllo trigger `trg_sync_auth_user` e permessi funzioni Auth.
  - Test bash statici: `tests/scripts/test_backup_restore_scripts.sh` **PASS**.
  - Test python: `tests/core/test_backup_drill.py` (3 PASS con integrazione Sentry).
- **AZIONE SUCCESSIVA**: Configurare `backup.env` su VM con le credenziali R2 e la chiave `age`, testando un'esecuzione manuale su un database sandbox isolato prima del go-live.
- **RESPONSABILE**: OWNER / LUNA

---

## 15. Monitoring / Sentry

- **STATO**: DONE (Runbook e SDK pronti; DSN da iniettare)
- **PRIORITÀ**: P0
- **EVIDENZA**:
  - `docs/operations/incident-response-runbook.md` redatto per tutti gli 11 scenari critici (API down, DB down, AI down, webhook failures, worker stuck, backup missing, disk full, credenziale compromessa, rollback, recovery).
  - Sentry SDK integrato nel backend FastAPI e nei worker (`SENTRY_DSN`).
  - Health checks `/api/health` e `/api/health/ready` attivi.
- **AZIONE SUCCESSIVA**: Creare un progetto gratuito su Sentry.io e inserire `SENTRY_DSN` in `.env.production`. Configurare un monitor esterno gratuito (BetterStack o UptimeRobot) sull'endpoint `/api/health`.
- **RESPONSABILE**: OWNER

---

## 16. Sicurezza

- **STATO**: PARTIAL — gate pre-lancio ancora aperti
- **PRIORITÀ**: P0
- **EVIDENZA**:
  - Tenant isolation applicativa forzata su ogni query (`src/core/db/scoping.py`).
  - Nessun segreto hardcoded; audit dipendenze pulito.
  - Cookie HttpOnly, Secure, SameSite, CSRF protection su BFF.
  - MFA obbligatoria (`aal2`) per operazioni privilegiate (billing, export GDPR, eliminazione organizzazione).
  - Scanner tenant e guard dog eseguiti in CI sulla baseline: **PASS**; nessuna nuova certificazione implicata da questo stato.
  - Il payload dell'export GDPR viene serializzato in chiaro a livello applicativo nel token store persistente Valkey/AOF. La cifratura/isolamento del volume a riposo non è stata verificata: confermare protezioni o cifrare il payload prima di conservare export reali.
- **AZIONE SUCCESSIVA**: Generare una nuova chiave Fernet casuale a 32-byte in `.env.production` (`ENCRYPTION_KEY`) sulla VM.
- **RESPONSABILE**: OWNER / LUNA

---

## 17. GDPR e Documenti Legali

- **STATO**: PARTIAL — protezione export a riposo e approvazione testi da completare
- **PRIORITÀ**: P0
- **EVIDENZA**:
  - Export dati GDPR e cancellazione organizzazione atomici con audit trail.
  - Token store per download GDPR reso fail-closed con Redis in produzione (PR #82); il payload JSON resta in chiaro a livello applicativo e la protezione a riposo del volume non è verificata.
  - Script `scripts/write_legal_runtime_env.py` isola i soli parametri legali pubblici per il web container read-only.
  - 33 test GDPR passati (`tests/core/gdpr/`).
- **AZIONE SUCCESSIVA**: Verificare/cifrare il payload export a riposo; l'Owner verifica che i dati aziendali (P.IVA, ragione sociale, PEC, indirizzo) nei file legali corrispondano ai dati reali e imposta i flag `LEGAL_*_REVIEW_APPROVED=true`.
- **RESPONSABILE**: OWNER

---

## 18. Dominio e TLS

- **STATO**: PARTIAL
- **PRIORITÀ**: P0
- **EVIDENZA**:
  - Caddyfile predisposto per rilascio automatico certificati Let's Encrypt / ZeroSSL su porte 80 e 443.
  - Due modalità isolate: `CADDY_SITE_MODE=temporary` (usa solo `PUBLIC_HOST` temporaneo senza richiedere certificati prematuri per il dominio principale) e `CADDY_SITE_MODE=final` (per `melpis.it` e redirect 308 `app.melpis.it`).
- **AZIONE SUCCESSIVA**: Configurare il puntamento DNS su Cloudflare (record A con proxy o DNS-only verso l'IP pubblico della VM).
- **RESPONSABILE**: OWNER

---

## 19. Costi Previsti e Rischi

- **STATO**: PARTIAL — preventivo da verificare
- **PRIORITÀ**: P0
- **EVIDENZA**:
  - I prezzi/tier gratuiti elencati qui sono stime indicative e condizionali a disponibilità, idoneità, quote, uso, termini e prezzi correnti del provider; non costituiscono garanzia di costo €0 né di continuità.
  - Oracle Cloud, Supabase, Groq, Cloudflare/R2, Resend e Sentry possono applicare limiti, cambiare condizioni o richiedere capacità non disponibile nel tier gratuito. Verificare costi ed eventuali overage prima dell'adozione.
  - Stripe può applicare commissioni variabili sul transato; verificare il tariffario applicabile. Budget mensile totale non ancora certificato.
- **Rischi Operativi Identificati**:
  - *Rischio Sleep Supabase Free*: condizioni e sospensione per inattività dipendono dalle policy correnti del provider; verificarle sul progetto effettivamente selezionato, senza assumere che traffico/heartbeat garantiscano l'assenza di sospensione.
  - *Rischio Rate Limit Groq Free*: se più tenant generano traffico simultaneo, Groq Free può restituire 429. Il sistema gestisce il caso fail-closed instradando verso operatore umano senza inventare risposte.
- **RESPONSABILE**: SOL / OWNER

---

## 20. Cose che può fare Sol / Luna

- Implementazione micro-fix, hardening del codice, risoluzione race condition e test deterministici (completato in PR #82).
- Validazione sintattica compose, script di bootstrap e test di conformità su host disposable (completato).
- Redazione runbook operativi e checklist di deployment (completato).
- Assistenza remota e review architetturale passo-passo durante le sessioni congiunte.

---

## 21. Cose che DEVE fare il Proprietario (Azioni Manuali)

Le seguenti operazioni richiedono credenziali, carte o accessi proprietari che gli agenti non possiedono e non devono manipolare:
1. **Google Calendar**: completare la certificazione end-to-end reale e di sicurezza; gate obbligatorio prima del Go-Live V1.
2. **Oracle Cloud**: verificare disponibilità e costi, poi eventualmente creare la VM Ubuntu 24.04 e recuperare l'IP pubblico.
3. **Cloudflare**: verificare prima dominio, DNS e costi; poi configurare i record e il bucket R2.
4. **Stripe**: Creare i 3 prodotti mensili (€29, €69, €149) in modalità Live e recuperare i Price ID e la webhook secret solo dopo l'autorizzazione di rilascio.
5. **Meta WhatsApp**: completare configurazione e test reale autorizzato.
6. **Groq**: verificare disponibilità/condizioni e poi creare l'account e la API key `gsk_...`.
7. **Resend**: verificare quote/prezzi, poi aggiungere il dominio `melpis.it`, configurare DKIM/SPF e SMTP.
8. Configurare gli URL webhook dei provider soltanto dopo aver verificato hostname pubblico, DNS, TLS e routing. Path applicativi: Stripe `/api/billing/webhook`; WhatsApp `/webhooks/whatsapp`. L'hostname resta da selezionare/verificare: non copiare ora URL completi nei pannelli.
9. **Compilazione `.env.production` sulla VM**: inserire i valori segreti reali sulla VM, senza commetterli su Git, soltanto dopo i gate e le autorizzazioni previsti.

---

## 22. Sequenza Operativa di Lancio (Step-by-Step)

```
[1. Configurazione Account Web]
  │- Stripe: 3 prodotti mensili + Webhook Live
  │- Meta: Numero WhatsApp + Webhook
  │- Groq: API Key Free
  │- Resend: Dominio verificato + API Key SMTP
  │- Cloudflare: Bucket R2 + DNS temporaneo
  ▼
[2. Bootstrap VM Oracle]
  │- Esegui scripts/bootstrap_oracle_vm.sh con --admin-cidr e --temp-host
  │- Prova secondo login SSH come utente melpis
  │- Esegui hardening (--apply-firewall --apply-ssh-hardening --confirm-ssh-access)
  ▼
[3. Configurazione Segreti sulla VM]
  │- Copia .env.production.example in /srv/melpis/app/.env.production
  │- Genera ENCRYPTION_KEY con cryptography.fernet
  │- Inserisci chiavi reali (Stripe, Meta, Groq, Resend, Supabase, R2)
  │- Esegui scripts/release_preflight.py per validazione fail-closed
  │- Genera runtime legale: scripts/write_legal_runtime_env.py
  ▼
[4. Avvio Staging / Host Temporaneo]
  │- CADDY_SITE_MODE=temporary su host temporaneo
  │- docker compose -f compose.production.yml up -d
  │- Esegui collaudo sandbox con numero di test allowlist
  ▼
[5. Cutover DNS e Lancio Finale]
  │- Punta melpis.it e app.melpis.it all'IP pubblico della VM su Cloudflare
  │- Imposta CADDY_SITE_MODE=final
  │- docker compose up -d caddy
  │- Esegui collaudo transazione reale Stripe Test / Live
  │- APERTURA UFFICIALE
```

---

## 23. Final GO / NO-GO

| Condizione | Stato Attuale | Criterio per GO |
|---|---|---|
| **Codice Melpis V1 & Test** | **NO-GO** | PR #82/#83 ancora aperte; fix webhook locale 27/27 PASS ma CI sul nuovo head da eseguire; Calendar non è certificato e ha gap fail-open/sync. |
| **Profilo Solo-Mensile** | **PARTIAL** | Supportato nel codice; prodotti/checkout/rinnovo e gestione eventi devono superare i gate Stripe Test prima del Live. |
| **Webhook Stripe** | **PARTIAL** | Race del binding sessione risolta; fix di consegna tardiva testato localmente, CI aggiornata ancora pendente. |
| **Google Calendar obbligatorio V1** | **NO-GO** | Incluso in tutti e tre i piani; flag disabilitato in produzione, fail-open FreeBusy e stato sync non affidabile da correggere e certificare con E2E reale. |
| **Configurazione Account Esterni** | **NO-GO (Attesa Owner)** | In attesa che l'Owner compili credenziali e dashboard dei provider. |
| **Infrastruttura VM & DNS** | **NO-GO (Attesa Owner)** | In attesa di creazione istanza Oracle e puntamento DNS Cloudflare. |
| **Decisioni Legali / Fiscali** | **NO-GO (Attesa Owner)** | In attesa di approvazione dei testi legali e ragione sociale. |

**Verdetto Finale**:  
V1 è **NO-GO / NON READY TO DEPLOY**. Le PR non sono mergiate e i gate su Stripe, Google Calendar, database corretto, costi e infrastruttura restano aperti. Nessun setup provider, merge o deploy è autorizzato da questo documento.
