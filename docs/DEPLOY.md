# Deploy — profilo Commercial Bootstrap, costi da verificare

Questa procedura non crea risorse e non autorizza addebiti. Il target scelto è
una VM Oracle Cloud Always Free, perché l'app richiede API e worker sempre
attivi; un piano con sleep o senza background worker non basta. La VM Oracle
Cloud Always Free è un candidato, non una garanzia di disponibilità o costo zero.
La quota A1 attuale equivale a 2 OCPU e 12 GiB di RAM complessivi per tenancy
Always Free: non usare la vecchia stima di 4 OCPU/24 GiB. Disponibilità della
shape, quote, condizioni e capienza sotto carico vanno verificate nel portale
e sulla VM prima del GO.

## Architettura

- Caddy è l'unico servizio esposto e gestisce TLS e proxy same-origin.
- Nginx serve il frontend; FastAPI non espone porte direttamente.
- I worker gestiscono inbox webhook durabile, risposte, retry e lease stale.
- Valkey mantiene rate limiting con AOF.
- Il database PostgreSQL/pgvector e il piano Supabase di destinazione sono da
  identificare e verificare; non assumere che un progetto esistente sia Melpis
  o che un piano gratuito garantisca continuità o costo zero.

Il manifest `compose.production.yml` non monta il sorgente, richiede riferimenti
OCI `repository@sha256:<64-hex>` emessi dal workflow (i tag, inclusi i tag SHA,
sono rifiutati dal preflight), usa processi non privilegiati, limiti risorse e rotazione
log. Solo Caddy pubblica 80/443.

## Preparazione

1. Dopo aver identificato e approvato il progetto Supabase di staging, verifica
   piano, costi, regione e supporto pgvector; non creare o migrare il progetto
   di produzione durante la preparazione dello staging.
2. Confronta schema e storico migrazioni del progetto di staging selezionato,
   poi applica in ordine le migrazioni mancanti da una release revisionata.
   Una verifica del 26 settembre 2026 indicava che le migrazioni locali
   `055_simulation_requests.sql` e `056_google_reviews_cursor.sql` non
   risultavano applicate al progetto candidato `Selecta SaaS`; quel progetto
   non è stato verificato come target Melpis. Verifica lo stato attuale solo
   sul target confermato e non applicare migrazioni a produzione senza backup
   verificato e autorizzazione.
3. Scegli un solo provider AI per il profilo `free_only`. Per Groq verifica
   nel pannello il piano FREE e ZDR prima di confermare i flag dedicati. Per
   OpenRouter imposta `AI_PROVIDER=openrouter`, una chiave dell'account free e
   un `AI_MODEL=openrouter/<vendor>/<model>:free`; il codice ricontrolla il
   catalogo gratuito/ZDR e si ferma se non può verificarlo. La sola presenza
   nel catalogo non garantisce che un endpoint sia disponibile o che non sia
   temporaneamente rate-limited: esegui un smoke test senza fallback a pagamento.
4. Collauda i flussi in Stripe Test Mode e Meta sandbox/test number. Per il
   go-live commerciale configura poi Stripe Live con i tre Price ID mensili
   (oppure sei se l'offerta annuale è approvata) e webhook firmato, mantenendo
   `LLM_COST_POLICY=free_only`. Prima del cutover compila
   `WHATSAPP_TEST_RECIPIENTS` e, se usato, `INSTAGRAM_TEST_RECIPIENTS` solo con
   destinatari autorizzati.
5. Copia `.env.production.example` in `.env.production` solo sulla VM, genera
   una nuova chiave Fernet e inserisci i segreti. Non commettere il file.
6. Esegui il controllo read-only:

   ```powershell
   python scripts/release_preflight.py --env-file .env.production
   ```

7. Crea il file runtime del frontend. Contiene solo i sei valori legali già
   approvati (nessun segreto API/DB) ed è scritto con permessi owner-only:

   ```powershell
   python scripts/write_legal_runtime_env.py --env-file .env.production --output .runtime/legal.env
   ```

8. Copia dal GitHub Actions summary i due riferimenti digest completi in
   `MELPIS_API_IMAGE_REF` e `MELPIS_WEB_IMAGE_REF`. Imposta inoltre
   `CADDY_SITE_MODE=temporary` e `PUBLIC_HOST` sul DNS temporaneo. Il compose
   carica solo `Caddyfile.temporary`, quindi non richiede certificati per
   `melpis.it` o `app.melpis.it` prima del DNS finale. Poi valida e avvia:

   ```powershell
   docker compose -f compose.production.yml config
   docker compose -f compose.production.yml up -d
   ```

## Go/no-go obbligatorio

- Health live/ready 200 via HTTPS; database con TLS e RLS attiva.
- Registrazione, login, MFA e isolamento tra due organizzazioni sandbox.
- Webhook Meta firmato: ACK rapido, riga inbox durabile, worker completato.
- Solo un destinatario in allowlist riceve il messaggio di prova.
- STOP persiste il consenso, blocca invii e produce audit event.
- Stripe Test: checkout, duplicati, eventi fuori ordine, cancellazione e
  deposito con carta fittizia; Stripe Live: chiavi, Price ID configurati (tre
  mensili o sei se offerta annuale abilitata) e firma webhook
  validati senza un addebito reale di prova.
- Fallimento escalation visibile in Inbox e intervento umano disponibile.
- Riavvio durante webhook/retry senza doppio effetto osservabile.
- Prima di clienti reali: `pg_dump` cifrato verso storage separato, schedulato
  e monitorato, e restore drill su un database di verifica. Non presumere backup
  nel tier Supabase Free né gratuità dello storage oltre le sue quote.

## Rollback

Mantieni i due riferimenti digest precedenti: ripristina
`MELPIS_API_IMAGE_REF` e `MELPIS_WEB_IMAGE_REF` e rilancia il compose.
Le migrazioni applicate sono additive e non vanno eliminate nel rollback. Se una
migrazione fallisce, ferma il traffico e ripristina un dump verificato.

## Evidenze ancora esterne

Codice e manifest non provano disponibilità Oracle, quote Supabase/Groq, DNS,
consegna Meta, webhook Stripe o backup separato. Non segnare queste voci complete
senza gli account sandbox e l'host effettivo.
