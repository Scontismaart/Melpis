# Melpis Incident Response & Monitoring Runbook

Questo runbook operativo definisce le procedure di monitoraggio, escalation e risposta agli incidenti per l'infrastruttura di produzione Melpis.

Tutti gli scenari distinguono tra **rilevamento proattivo** e **procedura di mitigazione/recupero**.

---

## 1. Mappa dei Controlli di Monitoraggio

| Componente | Endpoint / Check | Soglia Critica | Canale Alert Primario | Risposta Operativa |
|---|---|---|---|---|
| **API Web/BFF** | `GET /api/health` | HTTP != 200 per 2 controlli consecutivi (intervallo 60s) | Uptime Monitoring (BetterStack / UptimeRobot) + Sentry | Riavvio container `api` o analisi log Caddy |
| **Database Supabase** | `GET /api/health/ready` (query `SELECT 1`) | Latenza query > 3000ms o errore connessione | Sentry SDK (`asyncpg.PostgresError`) + Uptime monitor | Verifica dashboard Supabase, pooler connections |
| **Worker Inbound** | Docker container health & metriche inbox queue | Messaggi in stato `pending` con età > 5 minuti | Sentry Alert + Docker health check | Riavvio container `worker-inbound`, verifica Valkey |
| **Worker Retry** | Docker container health & DLQ backlog | Messaggi in DLQ > 10 in 1 ora | Sentry Alert (`dead_letter_queue_threshold`) | Analisi cause errori outbound (Meta token o rate limit) |
| **AI Provider** | Rifiuti Groq/OpenRouter (429, 503) | Percentuale errori AI > 10% in 15 minuti | Sentry SDK (`AIProviderError`) | Attivazione messaggio di cortesia / escalation forzata |
| **Webhook Meta** | Latenza ACK HTTP & error rate su `/webhooks/whatsapp` | Risposta > 2500ms o status 5xx | Sentry Alert + Meta App Dashboard Webhook Alerts | Verifica rate limit e saturazione connessioni DB |
| **Webhook Stripe** | Fallimento signature o 5xx su `/api/billing/webhook` (hostname pubblico da verificare prima della configurazione provider) | Status 5xx o dropped webhook events | Sentry Alert + Stripe Dashboard Webhook Failures | Verifica secret `whsec_...` e integrità database |
| **Backup Giornaliero** | Cron output `/usr/local/sbin/melpis-backup` | Nessun backup riuscito in R2 da > 26 ore | Sentry capture in `drill.py` + Syslog/Cron failure email | Esecuzione manuale `scripts/backup_supabase_r2.sh` |
| **Spazio Disco VM** | Utiilizzo filesystem root `/` | Spazio occupato > 85% | VM Agent / Cron disk-check script | Purge log Docker (`truncate`) e vecchi dump locali |
| **Valkey Memory** | Memoria utilizzata da Valkey | Raggiunto 90% di 96 MB (86 MB) | Sentry / Log warning | Verifica anomalie rate limiting / flush chiavi scadute |

---

## 2. Procedure Operative di Risposta per Scenario

### Scenario 1: API DOWN (Servizio non risponde)
1. **Rilevamento**: Uptime monitor segnala HTTP 502/503/timeout su `https://melpis.it/api/health`.
2. **Diagnosi rapida**:
   ```bash
   ssh melpis@VM_IP
   docker compose -f /srv/melpis/app/compose.production.yml ps
   docker compose -f /srv/melpis/app/compose.production.yml logs --tail 100 api
   docker compose -f /srv/melpis/app/compose.production.yml logs --tail 50 caddy
   ```
3. **Azioni di Recupero**:
   - Se il container `api` è crashato o in loop di riavvio (`restarting`):
     ```bash
     docker compose -f /srv/melpis/app/compose.production.yml restart api
     ```
   - Se Caddy restituisce 502 Bad Gateway: verificare che la rete interna `app_internal` non abbia conflitti IP e che il container `api` sia healthy (`docker inspect --format='{{.State.Health.Status}}' melpis_api_1`).
4. **Verifica del Recupero**:
   ```bash
   curl -I https://melpis.it/api/health
   # Deve restituire HTTP/2 200 OK
   ```

---

### Scenario 2: DB DOWN (Database Supabase Irraggiungibile)
1. **Rilevamento**: Sentry segnala `CannotConnectNowError` o `ConnectionDoesNotExistError` a raffica; `/api/health/ready` restituisce 503.
2. **Diagnosi**:
   - Verificare stato della piattaforma su `status.supabase.com`.
   - Accedere alla dashboard Supabase del progetto `qfxwqfavnuufdfpkhxtj`.
   - Verificare se il pooler (pgBouncer / Supavisor) ha esaurito i client consentiti.
3. **Azioni di Recupero**:
   - Se Supabase è in manutenzione o degradato: monitorare lo status page fino a risoluzione.
   - Se le connessioni sono sature per leak applicativo:
     ```bash
     docker compose -f /srv/melpis/app/compose.production.yml restart api worker-inbound worker-retry
     ```
4. **Verifica del Recupero**:
   ```bash
   curl -I https://melpis.it/api/health/ready
   # Deve restituire HTTP/2 200 OK con latency < 100ms
   ```

---

### Scenario 3: AI Unavailable (Groq / OpenRouter degradato o Rate-Limited)
1. **Rilevamento**: Log di sistema indicano `AIProviderUnavailable` o `RateLimitExceeded`; Sentry segnala fallback su escalation.
2. **Impatto sul Servizio**:
   - I guardrail di sicurezza e la policy `free_only` **impediscono** qualsiasi chiamata a pagamento non autorizzata.
   - Nessun appuntamento o promessa ingannevole viene generata.
   - Il risponditore invia il messaggio di cortesia standard: *"Al momento non riesco a rispondere, ti metto in contatto con un operatore dello staff."* e crea un ticket escalated in Inbox.
3. **Azioni di Recupero**:
   - Se il provider primario è temporaneamente indisponibile: valutare lo switch a fornitore alternativo gratuito (es. OpenRouter Free) aggiornando `AI_PROVIDER` e `AI_MODEL` in `.env.production` e riavviando:
     ```bash
     docker compose -f /srv/melpis/app/compose.production.yml restart worker-inbound
     ```
4. **Verifica**: Inviare un messaggio di test dal numero allowlist e verificare risposta nei tempi previsti.

---

### Scenario 4: Meta WhatsApp Webhook Failure
1. **Rilevamento**: Notifica email da Meta for Developers ("Webhook delivery failing for Melpis") o calo del traffico inbound.
2. **Diagnosi**:
   ```bash
   docker compose -f /srv/melpis/app/compose.production.yml logs --tail 100 api | grep "webhook"
   ```
   - Verificare se l'errore è dovuto a:
     - Firma HMAC non valida (`X-Hub-Signature-256` mismatch: secret non sincronizzato).
     - Timeout di rete (> 5 secondi: Caddy o API saturi).
     - Certificato TLS scaduto (Caddy non rinnova certificato).
3. **Azioni di Recupero**:
   - Verificare che Caddy stia rinnovando TLS regolarmente (`docker logs caddy`).
   - Se la firma fallisce: verificare `WHATSAPP_WEBHOOK_VERIFY_TOKEN` e `META_APP_SECRET` in `.env.production`.
4. **Verifica**: Eseguire un ping di test dal portale sviluppatori Meta (**WhatsApp → Configuration → Webhook → Test**).

---

### Scenario 5: Stripe Webhook Failure
1. **Rilevamento**: alert via email da Stripe Dashboard relativo all'endpoint configurato. Il path applicativo è `/api/billing/webhook`; l'hostname pubblico va verificato con DNS, TLS e routing reverse-proxy prima di registrare o usare l'URL completo. Non configurare URL provider copiandolo da questo runbook.
2. **Diagnosi**:
   - Controllare lo stato degli eventi nella Dashboard Stripe (**Developers → Webhooks → Tentativi di recapito falliti**).
   - Ispezionare la risposta HTTP registrata da Stripe (es. 400, 500, timeout).
3. **Azioni di Recupero**:
   - Se errore 400 `Invalid signature`: aggiornare `STRIPE_WEBHOOK_SECRET` con il valore esatto dell'endpoint live.
   - Se errore 500: verificare i log del container `api` per identificare l'eccezione SQL o di lock transazionale.
   - Poiché Stripe ritenta automaticamente i webhook per 72 ore con backoff esponenziale, una volta risolto il problema gli eventi pendenti verranno processati senza perdita di dati.
4. **Verifica**: Eseguire *"Invia evento di prova"* da Stripe Dashboard e verificare codice 200 OK.

---

### Scenario 6: Worker Stuck (Worker Asincrono Bloccato)
1. **Rilevamento**: I messaggi in arrivo vengono salvati in `whatsapp_inbox` ma non avanzano allo stato `processed` o non generano risposte outbound.
2. **Diagnosi**:
   ```bash
   docker compose -f /srv/melpis/app/compose.production.yml logs --tail 100 worker-inbound
   ```
   - Verificare se il worker ha perso la connessione a Valkey o PostgreSQL.
3. **Azioni di Recupero**:
   ```bash
   docker compose -f /srv/melpis/app/compose.production.yml restart worker-inbound worker-retry
   ```
4. **Verifica**: I messaggi non elaborati con lease scaduto vengono automaticamente ripresi in carico grazie al meccanismo di locking distribuito su database.

---

### Scenario 7: Backup Missing (Mancata Esecuzione Backup Giornaliero)
1. **Rilevamento**: Nessun file caricato nel bucket R2 con timestamp delle ultime 26 ore.
2. **Diagnosi**:
   ```bash
   sudo journalctl -u cron -g melpis-backup --no-pager -n 50
   ```
3. **Azioni di Recupero**:
   - Eseguire manualmente il wrapper come root per verificare l'errore:
     ```bash
     sudo /usr/local/sbin/melpis-backup
     ```
   - Cause frequenti:
     - Token R2 scaduto o permessi bucket revocati.
     - Spazio temporaneo esaurito in `/var/tmp`.
     - File `/etc/whatsapp-ai-responder/backup.env` con permessi errati (deve essere `0:600`).
4. **Verifica**: Esecuzione manuale completata con codice 0 e artifact visibile in R2.

---

### Scenario 8: Disk Full (Spazio Disco Esaurito su VM)
1. **Rilevamento**: Alert utilizzo disco > 85%; errori Docker `no space left on device`.
2. **Diagnosi**:
   ```bash
   df -h /
   docker system df
   ```
3. **Azioni di Recupero**:
   - Rimuovere build layer e container non utilizzati:
     ```bash
     docker system prune -f
     ```
   - Troncamento file di log container anomali:
     ```bash
     truncate -s 0 /var/lib/docker/containers/*/*-json.log
     ```
   - Pulizia temporanei:
     ```bash
     sudo rm -rf /var/tmp/supabase-r2-*
     ```
4. **Verifica**: `df -h /` mostra spazio disponibile > 30%.

---

### Scenario 9: Compromised Credential (Credenziale Esposta)
1. **Rilevamento**: Alert di secret scanning (GitHub, GitGuardian) o rilevamento interno.
2. **Azioni Immediate (Fail-Closed)**:
   - Se chiave **Stripe**: revocare immediatamente la chiave da Stripe Dashboard e generarne una nuova; aggiornare `.env.production` e riavviare `api`.
   - Se token **Meta**: rigenerare il token da Meta for Developers e aggiornare l'ambiente.
   - Se password **Database**: cambiare la password da Supabase Dashboard (**Settings → Database**), aggiornare `.env.production` e riavviare tutti i container.
   - Se chiave **Fernet (`ENCRYPTION_KEY`)**: richiede rotazione programmata dei dati cifrati a riposo (OAuth tokens, credenziali connettori). Seguire `docs/operations/pre-release-secret-rotation.md`.
3. **Audit**: Esaminare i log di accesso per identificare utilizzi anomali o accessi non autorizzati.

---

### Scenario 10: Rollback Release
Se un rilascio applicativo introduce una regressione critica in produzione:
1. **Procedura di Rollback Digest**:
   - Recuperare i due riferimenti digest immutabili della release precedente dal registro delle release (es. `MELPIS_API_IMAGE_REF_PREV` e `MELPIS_WEB_IMAGE_REF_PREV`).
   - Sulla VM, esportare i digest precedenti e riavviare:
     ```bash
     export MELPIS_API_IMAGE_REF="ghcr.io/scontismaart/saas-api@sha256:<PREVIOUS_DIGEST>"
     export MELPIS_WEB_IMAGE_REF="ghcr.io/scontismaart/saas-web@sha256:<PREVIOUS_DIGEST>"
     docker compose -f /srv/melpis/app/compose.production.yml up -d
     ```
2. **Politica sulle Migrazioni Database**:
   - Le migrazioni PostgreSQL in Melpis sono concepite per essere **additive e retrocompatibili** (expand-contract).
   - **NON** eseguire rollback DDL del database se non strettamente necessario, per evitare perdita di dati raccolti durante la finestra di release.

---

### Scenario 11: Disaster Recovery (Ripristino Totale da Zero)
In caso di perdita catastrofica della VM o corruzione del database:
1. **Ricostruzione VM**:
   - Eseguire su una nuova istanza Ubuntu 24.04:
     ```bash
     sudo bash scripts/bootstrap_oracle_vm.sh --admin-cidr '...' --temp-host '...' --ssh-authorized-key-file '...' --apply-firewall --apply-ssh-hardening --confirm-ssh-access
     ```
2. **Ripristino Database**:
   - Utilizzare lo script `scripts/restore_supabase_drill.sh` con target autorizzato o ripristinare il dump decifrato su una nuova istanza Supabase:
     ```bash
     age --decrypt --identity backup.key supabase-latest.dump.age | pg_restore --clean --if-exists --dbname "$NEW_DATABASE_URL"
     ```
3. **Esecuzione Fidelity Gate**:
   - Eseguire `scripts/verify_supabase_restore_fidelity.py` per certificare l'integrità di trigger e permessi prima di instradare traffico utente.
