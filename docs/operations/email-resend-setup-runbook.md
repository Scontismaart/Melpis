# Melpis Email & Resend Readiness Runbook

Questo documento definisce l'architettura, la configurazione, i requisiti DNS e le procedure operative del sistema email di Melpis in vista del lancio commerciale.

Nessuna email reale deve essere inviata a destinatari non autorizzati prima del cutover.

---

## 1. Separazione dei Canali Email

Melpis impiega due sistemi email distinti con scopi, configurazioni e credenziali separati:

```
                      +---------------------------------------+
                      |         MELPIS EMAIL SYSTEMS          |
                      +-------------------+-------------------+
                                          |
             +----------------------------+----------------------------+
             |                                                         |
+----------------------------+                            +----------------------------+
|    SUPABASE AUTH SMTP      |                            |  MELPIS APPLICATION SMTP   |
+----------------------------+                            +----------------------------+
| Gestito: Supabase Hosted   |                            | Gestito: Melpis Backend    |
| Config: Pannello Supabase  |                            | Config: .env.production    |
| Port: 587 (TLS/STARTTLS)   |                            | Port: 587 (STARTTLS)       |
+----------------------------+                            +----------------------------+
| Flussi:                    |                            | Flussi:                    |
| - Registrazione (Signup)   |                            | - Inviti Team              |
| - Conferma email           |                            | - Notifiche Escalation     |
| - Password recovery        |                            | - Preavvisi Sospensione    |
| - Magic Link / Auth Token  |                            |                            |
+----------------------------+                            +----------------------------+
```

### Canale A: Supabase Auth SMTP
- **Dove si configura**: Dashboard Supabase → progetto verificato come target Auth dell'ambiente interessato → **Authentication** → **Email Settings** → **SMTP Settings**. Il progetto di produzione Melpis non è ancora identificato: non usare `Selecta SaaS` come target senza verifica e approvazione esplicita.
- **Flussi gestiti**:
  - `confirm_email`: invio token monouso e link di attivazione account.
  - `reset_password`: invio link monouso per reimpostazione credenziali `/auth/v1/recover`.
  - Notifiche cambio email.
- **Sicurezza e Protezione**:
  - PKCE obbligatorio: il link hosted reindirizza al BFF Melpis con codice crittografico monouso.
  - Anti-enumerazione: Supabase restituisce 200 anche se l'email non esiste nel DB.
  - Rate limiting applicativo: integrato in `src/core/auth/register.py` e `src/core/auth/routes.py`.
  - Callback URL separati per ambiente, da sostituire con host verificati solo dopo DNS, TLS e reverse-proxy QA; i segnaposto non vanno incollati nei pannelli provider:
    - **Staging**: `https://<host-staging-verificato>/api/auth/confirm`
    - **Produzione**: `https://<host-produzione-verificato>/api/auth/confirm`

### Canale B: Melpis Application SMTP
- **Dove si configura**: file `.env.production` sulla VM (variabili `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`).
- **Codice sorgente**:
  - `src/core/notifications/email_service.py`: code e invio notifiche operative.
  - `src/core/team_invitations.py`: generazione token e invio inviti collaboratori.
- **Flussi gestiti**:
  - **Inviti Team**: `send_invitation(email, invite_url)` invia token di invito validi 7 giorni con permessi atomici e verifica identità Supabase Auth.
  - **Escalation Umana**: `enqueue_escalation(org_id, ...)` notifica i titolari dell'organizzazione quando un cliente WhatsApp richiede un operatore umano o quando l'AI non può procedere.
  - **Sospensione Servizio**: `enqueue_suspension_notice(org_id, ...)` notifica il blocco del bot WhatsApp a seguito di mancato pagamento Stripe (`past_due`, `canceled`).

---

## 2. Valutazione e Configurazione Resend SMTP

Melpis supporta nativamente qualsiasi server SMTP compatibile con standard RFC 5321 / STARTTLS.

### Valutazione: Resend SMTP vs Resend REST API
- **SMTP standard** (Raccomandato e adottato):
  - **Zero dipendenze esterne**: utilizza il modulo standard Python `smtplib` e `email.message.EmailMessage`.
  - **Portabilità completa**: permette di cambiare provider (Resend, Amazon SES, Postmark, Mailgun) modificando unicamente le variabili d'ambiente in `.env.production`, senza modificare una sola riga di codice né ricostruire le immagini Docker.
  - **Nessuna API key nel codice sorgente**.
- **REST API Resend** (Non necessaria):
  - Richiederebbe l'aggiunta di una libreria SDK o client HTTP dedicato, introducendo dipendenze e rischio di lock-in ingiustificato.

### Parametri di Configurazione Resend per `.env.production`

```ini
# Configurazione Melpis Application SMTP (tramite Resend SMTP)
SMTP_HOST=smtp.resend.com
SMTP_PORT=587
SMTP_USER=resend
SMTP_PASSWORD=<RESEND_API_KEY>
SMTP_FROM=Melpis <notifiche@melpis.it>
```

### Controlli di Sicurezza Applicativi
1. **STARTTLS**: sia `email_service.py` che `team_invitations.py` eseguono `server.starttls()` obbligatorio prima del comando `server.login()`, garantendo la cifratura in transito delle credenziali e del messaggio.
2. **Timeout Rigido**: timeout esplicito impostato a `10` secondi su `smtplib.SMTP(..., timeout=10)` per prevenire blocco dei thread/worker.
3. **Sanitizzazione Errori**: le eccezioni nei log (`logger.critical`) registrano solo `org_id` e `subject`. Nessuna password, token o credenziale SMTP viene mai riflessa nei log applicativi.
4. **Retry con Backoff**: le notifiche di `email_service.py` usano tenacity con `stop_after_attempt(3)` e `wait_exponential(multiplier=5, min=5, max=120)`.

---

## 3. Analisi della Durabilità delle Notifiche Email

### Inviti Team (`team_invitations.py`)
- **Comportamento**: gli inviti team vengono inviati in modo **sincrono/diretto** (`await asyncio.to_thread(_send)`).
- **Durabilità**: **NESSUN RISCHIO DI PERDITA SU RESTART**. L'invito viene registrato nel database `team_invitations` e l'email viene inviata contestualmente. Se l'invio SMTP fallisce, la route restituisce un errore e il record può essere reinviato dall'utente dalla Dashboard Team.

### Notifiche Operative (`email_service.py`)
- **Comportamento**: le notifiche di escalation e sospensione utilizzano una coda in-memory `asyncio.Queue`.
- **Comportamento al Riavvio della VM**:
  - Se la VM o il container API vengono riavviati mentre un evento è in coda, la notifica email in memoria può essere persa.
  - **Mitigazione di Sicurezza già attiva**:
    - Per l'**Escalation**: la conversazione nel database PostgreSQL viene marcata atomicamente con `status = 'escalated'`. L'operatore visualizza il ticket direttamente nell'Inbox della Dashboard web anche se l'email di notifica non è stata recapitata.
    - Per la **Sospensione**: l'organizzazione viene sospesa atomicamente nel database (`subscription_status = 'past_due'`). Il blocco del bot WhatsApp è deterministico e indipendente dall'avviso email.
- **Evoluzione post-lancio**: se il volume crescerà, sarà possibile passare a un transactional outbox persistito su PostgreSQL. Per la V1 l'architettura attuale è sufficiente e conforme.

---

## 4. Requisiti DNS e Deliverability per `melpis.it`

Per garantire che le email non finiscano nello spam e rispettino i requisiti 2026 di Google Workspace e Yahoo Mail, il dominio `melpis.it` deve essere configurato su Cloudflare DNS con i seguenti record:

### A. Record SPF (Sender Policy Framework)
Definisce quali server sono autorizzati a inviare email per `@melpis.it`.
- **Tipo**: `TXT`
- **Nome**: `@` (oppure `melpis.it`)
- **Valore**: `v=spf1 include:resend.com ~all`
*(Nota: se il dominio ricevente usa Google Workspace per le caselle umane, unificare: `v=spf1 include:_spf.google.com include:resend.com ~all`)*

### B. Record DKIM (DomainKeys Identified Mail)
Resend genera tre chiavi crittografiche univoche durante la verifica del dominio.
- **Tipo**: `CNAME` (3 record)
- **Nome / Valore**: forniti dal pannello di controllo Resend (sezione **Domains → Add Domain → melpis.it**).
- **Stato Proxy Cloudflare**: **DNS ONLY** (grigio, non proxied).

### C. Record DMARC
- **Tipo**: `TXT`
- **Nome**: `_dmarc`
- **Valore iniziale (monitoraggio)**:
  ```text
  v=DMARC1; p=none; rua=mailto:dmarc-reports@melpis.it; pct=100; adkim=r; aspf=r
  ```
- **Valore target (pre-lancio commerciale)**:
  ```text
  v=DMARC1; p=reject; rua=mailto:dmarc-reports@melpis.it; pct=100; adkim=s; aspf=s
  ```

### D. Separazione Netta delle Caselle Riceventi
Il servizio Resend SMTP è **esclusivamente transazionale in uscita**. Non gestisce la posta in arrivo.
- Le caselle istituzionali:
  - `support@melpis.it`
  - `privacy@melpis.it`
  - `info@melpis.it`
  devono essere ospitate su un provider di posta dedicato (es. Google Workspace, Zoho Mail, Proton, o provider IMAP/POP3).
- I record `MX` del dominio `melpis.it` devono puntare **esclusivamente** ai server del provider di posta ricevente, **MAI** a Resend.

---

## 5. Procedura di Collaudo Operativo

### Test con Server SMTP Fake/Local (Test deterministico)
1. Durante i test unitari e CI, `tests/core/test_email_service.py` e `tests/unit/api/test_team_invitations.py` simulano il socket SMTP intercettando le chiamate `smtplib.SMTP`, validando payload, destinatari, STARTTLS e gestione errori.
2. In ambiente locale/disposable è possibile avviare un container `mailpit` o `smtp4dev`:
   ```bash
   docker run -d --name mailpit -p 1025:1025 -p 8025:8025 axllent/mailpit
   ```
   e verificare la ricezione delle email tramite dashboard web su `http://localhost:8025` senza invii all'esterno.

### Test su Ambiente Reale (Sandbox Pre-Lancio)
Prima del Go-Live commerciale:
1. Inserire una chiave API Resend di test in `.env.production` sulla VM.
2. Creare un invito team per un indirizzo email appartenente all'operatore (`owner@...`).
3. Verificare ricezione, allineamento DKIM/SPF negli header dell'email ricevuta (`Authentication-Results: spf=pass, dkim=pass`).
4. Verificare che nessun messaggio venga inviato a utenti terzi non registrati.
