# Checklist lancio definitiva — commercial bootstrap a costi fissi €0

Ultimo aggiornamento: 22 settembre 2026. Le voci sono basate su evidenza
verificabile; nessuna risorsa cloud è considerata pronta solo perché configurata
nel codice.

## Contratti di prodotto confermati

- [x] Essenziale: 500 messaggi/mese, €29 mensile.
- [x] Crescita: 2.000 messaggi/mese, €69 mensile.
- [x] Scala: 10.000 messaggi/mese, €149 mensile.
- [ ] Approvare l'offerta annuale (€288/€708/€1.548); fino all'approvazione mantenere `STRIPE_ANNUAL_PRICING_ENABLED=false` e non compilare/attivare i Price ID annuali
  commerciale prima di pubblicare equivalenti mensili o promesse di sconto.
- [x] Nessun cliente/dato reale nel progetto pre-lancio.
- [x] Budget LLM €0; nessun fallback a pagamento.
- [x] Stripe Live per monetizzare dal Day 1; Groq resta `free_only` senza
  fallback a pagamento. Stripe Test e Meta sandbox restano obbligatori per le
  prove pre-cutover, senza invii a terzi.

## Correzioni implementate e verificate in CI

- [x] API utente: JWT Supabase + membership server-side; API key statica non
  rappresenta più un utente/tenant.
- [x] Credenziali integrazioni: owner + MFA; callback OAuth con nonce atomico,
  redirect same-origin ed errori non riflessi.
- [x] Rate limiting pre-auth per IP risolto solo da proxy esplicitamente fidati;
  credenziali hashate nelle chiavi e CORS include PATCH.
- [x] Webhook Meta: firma/body validati, persistenza inbox prima dell'ACK, lease,
  retry e deduplica transazionale.
- [x] Outbound WhatsApp/Instagram: stato ambiguo non reinviato alla cieca,
  consenso ricontrollato e destinatari sandbox fail-closed.
- [x] Escalation fallita visibile nell'Inbox; intervento umano preservato.
- [x] Billing: checkout e depositi idempotenti persistiti; webhook firmati,
  duplicati/fuori ordine e importo/valuta verificati; stati sconosciuti negati.
- [x] Accounting AI: attribuzione e outbox durabile con blocco fail-closed.
- [x] Frontend: linter `no-undef`, test DOM/ID/XSS, credenziali pulite nei `finally`,
  stili statici estratti e CSP senza inline script/style elements.
- [x] Immagini Python 3.12 non-root; compose separato con Caddy TLS, API privata,
  Valkey persistente e tag immutabili.

## Bloccanti operativi — non ancora verificabili localmente

- [ ] Account Oracle Always Free creato, capacità disponibile e nessun metodo di
  pagamento/addebito automatico attivo; IP pubblico assegnato.
- [x] Progetto Supabase `Selecta SaaS` attivo, pgvector attivo, migrazioni
  052–054 applicate e isolamento RLS verificato sul database remoto con due
  identità di tenant distinti in una transazione annullata. L'unico advisor
  security residuo è la protezione password compromesse, disponibile soltanto
  su Supabase Pro e quindi esclusa per rispettare il vincolo €0; MFA e policy
  password applicative restano obbligatorie.
- [ ] Account Groq FREE verificato senza billing; limiti annotati; chiave inserita;
  `GROQ_FREE_ACCOUNT_CONFIRMED=true` solo dopo il controllo.
- [ ] Stripe Live configurato con chiavi coerenti, i sei Price ID
  mensili/annuali e il webhook firmato; il flusso è stato prima collaudato in
  Test Mode senza addebiti reali.
- [ ] App/numero Meta configurati e approvati per il go-live; fino al cutover la
  sandbox/allowlist contiene solo numeri di test autorizzati dall'utente.
- [ ] DNS `melpis.it`/`app.melpis.it` oppure hostname temporaneo e HTTPS validi.
- [ ] Caselle support/privacy e base giuridica/DPA/sub-responsabili convalidati da
  un professionista; la revisione tecnica non è consulenza legale.
- [ ] Valori `LEGAL_*` verificati e flag `LEGAL_PUBLIC_DOCUMENTS_REVIEWED`,
  `LEGAL_PRIVACY_REVIEW_APPROVED`, `LEGAL_TERMS_REVIEW_APPROVED` e
  `LEGAL_DPA_REVIEW_APPROVED` impostati a `true` dopo review legale; nessun
  token `{{LEGAL_*}}` residuo nell'output pubblico renderizzato.
- [ ] Strategia backup gratuita separata dall'host, dump cifrato e restore drill.
- [ ] Alerting: Sentry Free configurato e testato oppure monitoraggio equivalente;
  i log strutturati da soli non sono un alert proattivo.

## Go/no-go su ambiente reale sandbox

- [ ] `python scripts/release_preflight.py --env-file .env.production` verde.
- [ ] Renderer legale eseguito nel container read-only con output in tmpfs e
  controllo dell'HTML pubblico senza token non risolti.
- [x] Suite backend completa, lint/test frontend, tenant scanner, migrazioni e
  immagini API/web amd64+arm64 verdi sul commit di release (`1850 passed, 33
  skipped`; 21 settembre 2026).
- [ ] Registrazione → verifica email → login → MFA → dashboard.
- [ ] Webhook firmato → ACK rapido → worker → Inbox → risposta al solo numero test.
- [ ] STOP/opt-out persistito e invii successivi bloccati.
- [ ] Escalation normale e simulazione fallimento visibile allo staff.
- [ ] Checkout mensile/annuale, replay/fuori ordine/cancellazione/deposito
  collaudati in Stripe Test; prodotti/prezzi/webhook Live verificati senza
  creare addebiti di prova su carte reali.
- [ ] Riavvio API/worker durante ingress/retry senza perdita o duplicazione effetto.
- [ ] Smoke desktop/mobile e controllo accessibilità tastiera/screen reader.
- [ ] Rollback al tag immagine precedente e restore drill documentati.

Il rilascio pubblico è **NO-GO** finché una voce della sezione “Bloccanti
operativi” o “Go/no-go” resta non verificata. Vedi `docs/DEPLOY.md`.
