# Melpis V1 — backlog e roadmap quattro settimane

Data: 17 ottobre 2026. [Specifica corrente](MELPIS-V1-SPEC.md). Baseline main 2994fae7; fetch del 17 ottobre conferma main locale e origin/main senza divergenze. Sol mantiene architettura, sicurezza, interfacce e review. Luna sviluppa task circoscritti con test. Nessun merge/deploy implicito.

## Stato operativo verificato — 17 ottobre 2026

18 task ufficiali: **4 in lavorazione, 8 bloccati, 6 da fare, 0 completati**. Un frammento sviluppato non completa il task: servono accettazione, CI verde e merge; per le integrazioni anche E2E. Il batch schema 058/059/UNIQUE è applicato su Staging con approvazione e backup/restore reali verificati. Nessun nuovo frammento codice è integrato in main; E2E remoto e approvazione produzione restano aperti.

| Task / issue | Owner | Stato | PR / dipendenze | Prova e blocco | Prossima azione |
|---|---|---|---|---|---|
| A00 / #85 | Sol | In lavorazione | #106 su #82 | snapshot preservato; preview gate 20+8 test e review sicurezza; CI stacked attivata | attendere CI/review #106 e recuperare successivi |
| A01 / #86 | Luna, review Sol | Bloccato | #103; indipendente | sviluppato, 19 test; CI rossa MCP | risolvere dipendenza compatibile, CI, merge |
| A02 / #87 | Sol; Luna su guard | In lavorazione | #82/#107/#108 | guard mensile 107 test; validazione eventi Stripe 60 test indipendenti | CI, review e riconciliazione eventi concorrenti prima di merge |
| A03 / #88 | Sol | In lavorazione | #109 su #106; A00/A01 | accounting preview 37 test e QA indipendente; altri ingressi da certificare | CI e verifica integrata limiter, poi altri ingressi |
| A04 / #89 | Sol | Bloccato | A02/A03; prerequisito Q&A | proposta Architect e prescrizioni Security; nessuna quota atomica finale certificata | prima Q&A, poi protocollo disattivato e test di concorrenza |
| A05 / #90 | Sol | Da fare | A00 | capacità/idempotenza presenti, autorizzazione da completare | policy deterministica auto/manuale |
| A06 / #91 | Sol | Da fare | A00 | candidato #82 non certifica privacy completa | recupero sicuro export/delete/retention |
| B01 / #92 | Sol | Bloccato | A00/A05 | Calendar non certificato | completare policy poi test provider |
| B02 / #93 | Sol, Luna su test | Da fare | A03/A04 | PDF/RAG presente; capacità per piano non approvata | benchmark RAM/storage e limiti |
| B03 / #94 | Luna, review Sol | Da fare | B01/B02 | wizard 7 passi presente | verificare persistenza, disclosure, errori |
| B04 / #95 | Sol, Luna su UI | Bloccato | A02/A04/B02 | prezzi confermati; metrica/entitlement incompleti | allineare API, dashboard e copy |
| C01 / #96 | Sol + fondatore | In lavorazione | A04/A05/A06 per schema finale | 058/059/UNIQUE applicate; backup/restore80tabelle PASS; 42RLS | certificare applicazione/provider e schema finale |
| C02 / #97 | Sol + fondatore | Bloccato | C01 | #83 runbook; host/email/restore reale non certificati | identificare risorse e preventivo |
| C03 / #98 | Sol + fondatore | Bloccato | B01/B03/C02 | E2E Meta/Google mancante | callback e account autorizzati |
| C04 / #99 | Sol + fondatore | Bloccato | A02/A04/C02 | Stripe Test E2E mancante | catalogo mensile e scenari provider |
| C05 / #100 | Sol + fondatore | Da fare | A06/B03 | policy AI e testi da approvare | inventario provider e costo rappresentativo |
| D01 / #101 | Sol, Luna su QA | Bloccato | tutti i gate A/B/C | release integrata non pronta | chiudere P0 e certificare commit combinato |
| D02 / #102 | Fondatore + Sol | Da fare | D01 | non avviato, subordinato al go/no-go | preparare coorte 3–5 attività |

### TASK 0 — dipendenze CI

**Integrazione autorizzata.** La [PR110](https://github.com/Scontismaart/Melpis/pull/110), head verificato `f322359`, è stata integrata soltanto nella [PR105](https://github.com/Scontismaart/Melpis/pull/105): commit risultante `c832869`, contenuti identici al codice già verificato. #105 resta aperta verso main, senza auto-merge. Per main e relativa pubblicazione GHCR serve autorizzazione separata.

**Correzione.** La wheel ufficiale CrewAI 1.15.4 imponeva MCP 1.28.x, interessato da CVE-2026-59951. Il builder riproducibile modifica soltanto METADATA/RECORD: 526 file, codice/versione/licenza inclusi, restano identici. Il runtime usa MCP 1.30.0, installer condiviso CI/migrazioni/Docker e bootstrap pip 26.2.1 verificato con SHA negli ambienti isolati. La policy respinge audit incompleti, pacchetti saltati e duplicati; confronta inventario e versioni esatte anche nell'immagine finale senza privilegi. Le cinque eccezioni preesistenti Chroma/json-repair restano invariate; nessuna nuova eccezione. Provenienza e procedura sono in `docs/REVIEWED-RUNTIME.md` della PR105.

**Prove su f322359.** [CI completa verde](https://github.com/Scontismaart/Melpis/actions/runs/38991228923): **2.744 test passati, 33 test live opt-in esclusi**. Audit OSV: 204 dipendenze, nessun pacchetto escluso, inventario esatto; build API e web native amd64/arm64 riuscite. Sette test MCP passati, inclusi handshake/tool/error HTTP e SSE su server locali reali. Ripetizione locale sull'immagine finale non-root, senza rete esterna: inventario verificato e sette test passati. I 33 esclusi riguardano provider live; non certificano gli E2E esterni.

**Nuova verifica completata.** [CI esatta su c832869](https://github.com/Scontismaart/Melpis/actions/runs/39018034399) interamente verde dopo il merge: **2.744 test passati, 33 test live opt-in esclusi**, audit di 204 dipendenze senza pacchetti esclusi e tutte e quattro le build API/web amd64/arm64 riuscite. Nessun conflitto; main invariato e auto-merge disabilitato. Pubblicazione immagini saltata. Le prove non certificano i provider live né il completamento delle funzionalita billing/Q&A/quota. Le precedenti PR #103/#104/#106–#109 con la vecchia base restano da riallineare e rieseguire dopo l'integrazione autorizzata della base CI.

**Pubblicazione e deploy.** I workflow pubblicano immagini soltanto su push a main; i job di pubblicazione della PR110 sono stati saltati. I tag sono legati allo SHA e Compose richiede riferimenti per digest. Nei controlli repository non risultano workflow di deploy, webhook o deployment GitHub. Non sono stati interrogati host o automazioni esterne di Production. Il merge nella sola #105 non pubblica immagini e non modifica main.

### Certificazioni distinte

- Sviluppato localmente: limiter preview (#103), gate billing preview A00 (#106), isolamento tool audit (#105), guard mensile startup/preflight (#107, 107 test), validazione eventi Stripe (#108, 60 test indipendenti), accounting preview (#109, 37 test anche in QA indipendente), piano C01. Accounting completo e quota atomica restano aperti.
- Integrato in main: nessuno dei nuovi frammenti; #82/#83/#103/#104/#105/#106/#107/#108/#109/#111 aperte; #110 chiusa e integrata soltanto nella #105.
- Staging: batch approvato applicato, backup cifrato reale e restore PG17.6 isolato verificati su 80 tabelle, catalogo/ACL/trigger e dati. Solo ledger migrazioni varia fra le tabelle preesistenti. **Nessuna certificazione E2E applicativa/provider remota**. Backup e chiavi DPAPI conservati fino a conferma umana per eliminazione.
- Produzione: nessun nuovo rilascio o modifica autorizzata.

[Batch C01, hash, impatto accessi e backup/rollback](MELPIS-V1-STAGING-PLAN.md). Il backfill 059 riguarda 27 subject storici: 7 senza membership restano negati. Approvazione del batch e del restore locale ricevute; BitLocker, sicurezza PC, spazio, ACL e TLS verificati. Container temporaneo rimosso; backup cifrato conservato.

## Dipendenze

A00 recupera il diff locale; A02 integra la candidata PR82 dopo review. A01 è indipendente e non duplica il lavoro locale. A03 accounting e A04 quota atomica precedono certificazione funzionale; A05/A06 chiudono booking/privacy. Calendar/PDF obbligatori in B. Le preparazioni esterne C vanno aperte subito, mentre le certificazioni attendono i task indicati.

## [V1-A00](https://github.com/Scontismaart/Melpis/issues/85) — Recuperare e separare l'hardening locale esistente

- Priorità/fase: P0/A. Responsabile: Sol.
- Dipendenze: nessuna.
- Componenti: Worktree melpis-prelaunch-m1; 49 file tracciati modificati e due nuovi moduli/test; PR #82/#83.
- Test: Revisione diff e segreti; suite per ciascun frammento; CI sul nuovo commit.
- Accettazione: Ogni frammento è attribuito, verificato e pubblicato in una piccola PR; nessuna modifica locale viene persa o dichiarata coperta dalla vecchia CI.
- Rischi: Diff ampio e interdipendente; possibili cambi di policy non approvati.
- Stato/prove: Snapshot locale conservato; primo frammento gate preview sviluppato e verificato (20 test), [PR106](https://github.com/Scontismaart/Melpis/pull/106), commit `60c7a23`, stacked su #82. QA indipendente: 20 test route e 8 sospensione; head `d5a82ec` include dipendenze CI #105. Resto del diff non recuperato.

## [V1-A01](https://github.com/Scontismaart/Melpis/issues/86) — Limitare la preview onboarding prima di retrieval e provider

- Priorità/fase: P0/A. Responsabile: Luna; review Sol.
- Dipendenze: nessuna.
- Componenti: src/api/main.py; src/api/routes/organization.py; test route preview.
- Test: 10 richieste ammesse/11a 429; stesso tenant condiviso; tenant separati; header falsificato; limiter guasto; limite globale.
- Accettazione: Limite 10/60 secondi per organizzazione autenticata applicato prima di qualsiasi lavoro AI; route inclusa nel limiter globale; nessuna chiamata successiva quando negata.
- Rischi: Riduce abuso; non certifica quota mensile o accounting.
- Stato/prove: implementata da Luna, revisionata da Sol e QA;19test passati con PostgreSQL isolato; [PR103](https://github.com/Scontismaart/Melpis/pull/103) aperta, commitf6e88da; CI/merge ancora gate. [Prove](MELPIS-V1-FIRST-P0.md).

## [V1-A02](https://github.com/Scontismaart/Melpis/issues/87) — Integrare in sicurezza il billing mensile della PR #82

- Priorità/fase: P0/A. Responsabile: Sol.
- Dipendenze: nessuna.
- Componenti: PR #82; billing/webhook_handler.py, checkout.py, routes.py; startup_guard.py; preflight.
- Test: Prezzi sconosciuti/mancanti, tutti i line item, checkout intent, duplicati, eventi fuori ordine e tre piani mensili.
- Accettazione: Solo catalogo approvato concede entitlement; annuale non proposto nel lancio; tutti i test sul commit revisionato. Merge separato autorizzato dal fondatore.
- Rischi: PR82 aperta; CI verde vale solo per a3d40ca, non per diff locale.
- Stato/prove: Candidato esistente su GitHub, non integrato. Guard mensile startup/preflight in [PR107](https://github.com/Scontismaart/Melpis/pull/107), sorgente `8db4ad3`, head con dipendenze CI `c220d6a`, approvato dalla review sicurezza; 107 test passati anche in QA indipendente. [PR108](https://github.com/Scontismaart/Melpis/pull/108), head `6a8d8c7`, aggiunge validazione completa di prezzo, singolo line item e quantità, binding customer/subscription, cause fattura e restrizione dello stato per snapshot autorevoli non validi. QA indipendente: 60 test passati, Ruff e diff check passati. Nessun contatore quota modificato da questa correzione. Eventi validi concorrenti nello stesso secondo possono richiedere snapshot successivo/riconciliazione; Stripe Test E2E e quota atomica rimangono aperti. CI e merge ancora necessari.

## [V1-A03](https://github.com/Scontismaart/Melpis/issues/88) — Certificare gate AI e accounting dei tentativi effettivi

- Priorità/fase: P0/A. Responsabile: Sol.
- Dipendenze: V1-A00, V1-A01.
- Componenti: crew_runner*.py; onboarding.py; routes/common.py; reviews/ai_governance.py; documenti/qa_agent.py.
- Test: Billing assente/errore, trial scaduto, quota zero, accounting indisponibile; modello effettivo, token e latenza per ogni tentativo.
- Accettazione: Nessuna chiamata senza tenant e stato verificato; ogni tentativo anche fallito è attribuito; metriche derivano dal provider; accounting irrisolto blocca nuove generazioni.
- Rischi: La preview attuale ricostruisce il modello invece di propagare le metriche effettive.
- Stato/prove: [PR109](https://github.com/Scontismaart/Melpis/pull/109), head `e36fa7a`, stacked su #106: propaga metriche effettive e registra i tentativi in finally anche su errore; rimuove il precedente evento stimato al solo successo. 37 test passati, ripetuti in QA indipendente; review sicurezza approvata, Ruff/diff check passati. Le prove DB coprono tentativi multipli, costo effettivo, errore dopo tentativo, metriche incomplete con blocco persistente e successiva negazione, zero eventi prima del provider, isolamento tenant e contatore commerciale invariato. Non certifica A04, retry interni SDK o crash prima della persistenza; CI e verifica integrata con #103 ancora necessarie. Altri ingressi AI da certificare separatamente. [PR111](https://github.com/Scontismaart/Melpis/pull/111), head `c513d2b`, estrae la sola policy condivisa dei client senza cambiare routing/costi/provider: 48 test passati in sviluppo Luna, root e QA Security con rete bloccata; validazione applicativa CI passata, build API ancora in corso al controllo corrente. Nessun nuovo adapter o percorso Q&A attivato.

Ulteriore gate A03: CrewAI 1.15.4 normalizza alcuni campi token mancanti a zero prima degli eventi; i test #109 certificano il collegamento al runner e al ledger, non la presenza dei campi nella risposta originale del provider. Il contratto raw delle metriche e l'isolamento dei collector devono essere provati prima del merge e della certificazione A03. Non interpretare metriche ignote come consumo zero. Q&A documentale richiede ancora gate, guardrail e accounting rigoroso.

## [V1-A04](https://github.com/Scontismaart/Melpis/issues/89) — Applicare quota risposte AI atomica e idempotente

- Priorità/fase: P0/A. Responsabile: Sol.
- Dipendenze: V1-A02, V1-A03.
- Componenti: billing_repo.py; message_repo.py; inbound/service.py; migrazione additiva se necessaria; dashboard consumi.
- Test: Concorrenza all'ultima risposta; replay stesso operation_id; crash; fallimento generazione; rinnovo e upgrade; accesso manuale a quota zero.
- Accettazione: Una risposta finale valida e persistita conta una volta; riserva prima del provider; fallimenti verificati prima della persistenza rilasciano la riserva; consegna fallita dopo persistenza conta una volta; dashboard e operazioni manuali restano accessibili; rinnovo/upgrade sbloccano senza reset impropri.
- Rischi: Claim inbound esistente e snapshot runner non provano lo stesso contratto su tutti i percorsi.
- Stato/prove: Proposta Architect e prescrizioni Security preparate nel fascicolo privato del ciclo: riuso usage_events e unico contatore, periodo stabile sulle modifiche di sola scadenza, token protetti, tentativi durevoli/idempotenti e autorizzazione finale. Sequenza di frammenti definita, iniziando dal prerequisito Q&A A03. Nessuna quota atomica implementata o certificata; ulteriori migrazioni Staging richiedono un nuovo piano e autorizzazione.

## [V1-A05](https://github.com/Scontismaart/Melpis/issues/90) — Separare proposta AI e autorizzazione deterministica della prenotazione

- Priorità/fase: P0/A. Responsabile: Sol.
- Dipendenze: V1-A00.
- Componenti: conversation_orchestrator.py; legacy_pipeline.py; verticals/base.py; bookings/service.py e routes.py.
- Test: Negazioni, testo iniettato, date passate, timezone, capacità concorrente, conferma automatica/manuale e duplicati.
- Accettazione: LLM estrae dati soltanto; policy server verifica intento, dati confermati, tenant, regole e disponibilità; auto/manuale configurabile; Calendar ambiguo o dati incerti causano handoff senza conferma.
- Rischi: Main crea richiesta in_attesa dall'output LLM; non confonderla con conferma automatica sicura.
- Stato/prove: Idempotenza e capacità native presenti; gate di autorizzazione da completare.

## [V1-A06](https://github.com/Scontismaart/Melpis/issues/91) — Completare export, delete, retention e protezione token GDPR

- Priorità/fase: P0/A. Responsabile: Sol; Luna su test circoscritti.
- Dipendenze: V1-A00.
- Componenti: gdpr/routes.py, propagation.py, token_store.py; retention_job.py; repository; Caddy.
- Test: Inventario dati export; delete parziale/riprova/replay; purge messaggi/conversazioni; token one-time senza URL/log; Redis guasto.
- Accettazione: Export include tutte le categorie definite; cancellazione dichiara stato reale e propagazione richiesta tracciata; retention corrisponde al DPA; Redis richiesto; nessun token bearer nei log.
- Rischi: Diff locale migliora token ma non certifica cancellazione/retention completa; dipende da inventario e review legale.
- Stato/prove: Parzialmente implementato fra main, PR82 e locale; ricomposizione necessaria.

## [V1-B01](https://github.com/Scontismaart/Melpis/issues/92) — Completare Google Calendar mantenendo il gate di readiness

- Priorità/fase: P0/B. Responsabile: Sol.
- Dipendenze: V1-A00, V1-A05.
- Componenti: calendar/service.py, routes.py, redirect_uri.py locale; scheduler.py; startup_guard.py; preflight.
- Test: OAuth nonce/MFA/tenant; freebusy errore/risposta vuota/malformata; timezone/DST; create/update/delete; retry e revoca.
- Accettazione: Disponibilità sconosciuta non equivale a libera; abilitazione condizionata a credenziali e callback valide; eventi e booking riconciliati senza duplicati; prova reale in C03.
- Rischi: Main vieta Calendar in production; locale è candidato non certificato.
- Stato/prove: Implementazione esistente con correzioni locali da recuperare.

## [V1-B02](https://github.com/Scontismaart/Melpis/issues/93) — Completare PDF/RAG tenant-scoped e coerente con i piani

- Priorità/fase: P0/B. Responsabile: Sol; Luna su upload/UI/test.
- Dipendenze: V1-A03, V1-A04.
- Componenti: routes/knowledge.py; documenti/*; document_repo.py; dashboard-knowledge.js; billing/plans.py.
- Test: PDF valido/corrotto/scansione senza testo; limiti byte/pagine/chunk; tenant diversi; documenti inattivi; injection, conflitti fonti e rollback indicizzazione.
- Accettazione: PDF caricato è pronto solo dopo indicizzazione completa; retrieval usa solo fonti autorizzate e attive del tenant; nessuna istruzione documento autorizza azioni; entitlement e limiti visibili coerenti.
- Rischi: MiniLM locale in processi multipli e vettori PostgreSQL consumano memoria/storage; RAG attuale solo Scala.
- Stato/prove: Pipeline PDF/estrazione/chunk/embedding/retrieval già presente; matrice proposta da approvare.

## [V1-B03](https://github.com/Scontismaart/Melpis/issues/94) — Completare wizard, multilingua e trasparenza AI riusando la dashboard

- Priorità/fase: P1/B. Responsabile: Luna; criteri/review Sol.
- Dipendenze: V1-B01, V1-B02.
- Componenti: pane-onboarding.html; dashboard-ai-settings.js; dashboard-integrations.js; onboarding.py; locali e guida Meta.
- Test: Persistenza profilo/lingue/escalation; 7 passi esistenti; errori integrazioni; AI disclosure; cambio tenant e ruoli.
- Accettazione: Wizard esistente riusato; guide Meta manuale/assistita; connessione non mostrata operativa senza prova; lingue e handoff rispettano configurazione; cliente informato che parla con AI.
- Rischi: Salvare credenziali non certifica funzionamento; non duplicare onboarding già esistente.
- Stato/prove: Wizard Attività/Voce/Lingue/Escalation/WhatsApp/Documenti/Test già presente.

## [V1-B04](https://github.com/Scontismaart/Melpis/issues/95) — Allineare piani, dashboard consumi e copy alla specifica V1

- Priorità/fase: P1/B. Responsabile: Sol; Luna su copy/test.
- Dipendenze: V1-A02, V1-A04, V1-B02.
- Componenti: plans.py; dashboard-settings.js; dashboard-overview.js; locales/*; landing esistente e landing-v2.
- Test: Stessa metrica UI/API/billing; solo mensile; zero quota consente dashboard/manuale; entitlement server-side.
- Accettazione: 29/69/149 e 500/2000/10000 risposte; trial7d/150 senza carta; nessuna promessa Instagram/Reviews V1; Calendar e PDF disponibili secondo matrice approvata.
- Rischi: Landing-v2 è branch separato; la proposta dei limiti RAG non è ancora decisione commerciale.
- Stato/prove: Prezzi confermati; contratto quote e capacità ancora da allineare.

## [V1-C01](https://github.com/Scontismaart/Melpis/issues/96) — Identificare Supabase staging/production e certificare migrazioni

- Priorità/fase: P0/C. Responsabile: Sol + fondatore.
- Dipendenze: V1-A04, V1-A05, V1-A06 per lo schema finale; il batch 058/059/UNIQUE corrente puo essere preparato indipendentemente.
- Componenti: Migrazioni SQL; configurazione Auth/RLS; docs/DEPLOY.md.
- Test: Project ID/regione/ruolo; migrazioni da zero/upgrade/replay; due tenant reali; revoca membership; signup beta limitato.
- Accettazione: Progetti identificati senza ambiguità; schema esatto della release; autorizzazione applicativa con service-role verificata; nessuna modifica distruttiva senza approvazione.
- Rischi: history non prova lo schema; 059 cambia accesso per sette subject storici senza membership; backup reale, lock UNIQUE e approvazione batch sono gate obbligatori.
- Stato/prove: Staging ACTIVE_HEALTHY. Batch 058/059/UNIQUE applicato dopo approvazione, backup cifrato reale e restore verificato (80 tabelle, catalogo e dati); replay sul clone reale e cinque assert RLS/vecchi claim passati. Postflight 42/42 tabelle public RLS, 27 lifecycle, dati precedenti invariati salvo ledger migrazioni. [Piano ed evidenze](MELPIS-V1-STAGING-PLAN.md). C01 resta aperto per schema finale, autorizzazione applicativa e prove E2E.

## [V1-C02](https://github.com/Scontismaart/Melpis/issues/97) — Preparare host, HTTPS, email, backup e monitoraggio entro budget approvato

- Priorità/fase: P0/C. Responsabile: Sol + fondatore.
- Dipendenze: V1-C01.
- Componenti: compose.production.yml; Caddy; runbook #83; script backup/restore.
- Test: Capienza CPU/RAM/embedding; TLS; email conferma/reset/inviti; backup cifrato off-host+restore; alert worker/inbox/provider.
- Accettazione: Host verificato, immagini per digest, due canali email distinti configurati, restore isolato riuscito, alert ricevuto e rollback documentato; preventivo <=circa10 EUR/mese esclusi dominio/AI/variabili o nuova decisione budget.
- Rischi: Oracle capacity/reclamation e quote Free; nessun servizio pagato attivato automaticamente.
- Stato/prove: Runbook in PR83; risorse e delivery non certificate.

## [V1-C03](https://github.com/Scontismaart/Melpis/issues/98) — Certificare Meta e Google Calendar con destinatari e account autorizzati

- Priorità/fase: P0/C. Responsabile: Sol + fondatore.
- Dipendenze: V1-B01, V1-B03, V1-C02.
- Componenti: Webhook/worker/outbound; Google OAuth; integrazioni dashboard.
- Test: Meta inbound/ACK/delivery/STOP/replay/restart/handoff; Google busy/free/create/update/delete/revoca/recovery.
- Accettazione: Prova E2E reale con trace_id/organization_id; nessun doppio invio/prenotazione; provider failure non diventa disponibilità o successo.
- Rischi: Numero Meta dichiarato disponibile ma E2E non provato; callback pubblica e OAuth possono ritardare il mese.
- Stato/prove: Blocco esterno verificabile solo dopo host e account.

## [V1-C04](https://github.com/Scontismaart/Melpis/issues/99) — Certificare Stripe Test e preparare tre prezzi mensili Live

- Priorità/fase: P0/C. Responsabile: Sol + fondatore.
- Dipendenze: V1-A02, V1-A04, V1-C02.
- Componenti: Billing checkout/webhook/portal; catalogo Stripe; runbook.
- Test: Tre piani, trial senza carta, scadenza, upgrade, rinnovo, pagamento fallito, cancellazione, duplicati/out-of-order e price sconosciuto.
- Accettazione: Entitlement e contatori esatti; dashboard/manuale accessibili a quota esaurita; Live coerente con Test dopo autorizzazione del fondatore.
- Rischi: Stripe Live da configurare; test non deve causare addebiti reali.
- Stato/prove: Test automatizzati esistenti; provider Test/Live da certificare.

## [V1-C05](https://github.com/Scontismaart/Melpis/issues/100) — Approvare trattamento dati, testi legali e policy provider AI

- Priorità/fase: P0/C. Responsabile: Sol + fondatore/consulente.
- Dipendenze: V1-A06, V1-B03.
- Componenti: Privacy/termini/DPA; AI disclosure; cost_policy.py; startup_guard.py; runbook provider.
- Test: Nessun placeholder pubblico; inventario subprocessori; account/quote/ZDR e condizioni uso; kill switch; nessun fallback di spesa implicito.
- Accettazione: Documenti approvati e runtime coerente; provider/model scelti con budget e condizioni verificati; proposta di piano pagato soggetta ad approvazione esplicita.
- Rischi: free_only obbligatorio può impedire provider sostenibile per SaaS; nessuna promessa di gratuità o SLA non verificata.
- Stato/prove: Verifica esterna necessaria; nessun nuovo servizio a pagamento autorizzato.

## [V1-D01](https://github.com/Scontismaart/Melpis/issues/101) — Certificare release integrata e go/no-go beta

- Priorità/fase: P0/D. Responsabile: Sol; Luna su regressioni.
- Dipendenze: V1-A01, V1-A02, V1-A03, V1-A04, V1-A05, V1-A06, V1-B01, V1-B02, V1-B03, V1-B04, V1-C01, V1-C02, V1-C03, V1-C04, V1-C05.
- Componenti: CI completa; E2E desktop/mobile; release manifest; checklist beta.
- Test: Suite sul commit esatto; migrazioni Postgres; limiti/concorrenza; guasti/recovery; segreti/dipendenze; test reali provider.
- Accettazione: Ogni requisito V1 ha prova; zero P0 aperti; evidence aggiornata al commit integrato; fondatore autorizza deploy/merge sensibili.
- Rischi: CI delle singole PR non certifica combinazione; un gate esterno mancante rinvia beta.
- Stato/prove: Non pronto.

## [V1-D02](https://github.com/Scontismaart/Melpis/issues/102) — Onboardare 3–5 attività e monitorare qualità e costi

- Priorità/fase: P1/D. Responsabile: Fondatore + Sol.
- Dipendenze: V1-D01.
- Componenti: Runbook onboarding/supporto; metriche tenant; registro feedback.
- Test: Configurazione assistita di ogni attività; rehearsal escalation; stop AI; controlli giornalieri backup/consumi.
- Accettazione: 3–5 attività con fonti approvate, consenso e operatore; errore e spesa per tenant visibili; decisione esplicita prima di ampliare coorte.
- Rischi: Supporto affidato al fondatore; volume reale e costo per risposta possono cambiare il dimensionamento.
- Stato/prove: Bloccato dal go/no-go.

## Calendario operativo condizionato ai gate

| Settimana | Sol | Luna | Fondatore/gate esterni | Uscita |
| --- | --- | --- | --- | --- |
| 1: 16–22 ottobre | A00, review A01/A02, progettare A04–A06 | A01 e regressioni circoscritte | Identificare Supabase, Meta/Google/host; decidere capacità piani e policy AI | Lavoro recuperato, primi P0 verificati, account identificati |
| 2: 23–29 ottobre | A03–A06; B01; architettura B02 | Test/upload/UI/wizard B02–B04 | Callback OAuth/Meta, email e privacy; preventivi prima di spesa | Quote e booking deterministici; Calendar/RAG certificati localmente |
| 3: 30 ottobre–5 novembre | Integrazione C01–C05 | E2E desktop/mobile e guide | Stripe Test, HTTPS, backup/restore, review legale/provider | E2E reali, restore/alert, candidate con CI integrata |
| 4: 6–12 novembre | D01 go/no-go e review | Regressioni dai test | Autorizzazione produzione e onboarding D02 | Beta 3–5 attività soltanto con tutti i P0 chiusi |

Finestra operativa dalla verifica, non promessa. Percorso critico: capienza/reclaim Oracle, Meta e callback pubblica, Google OAuth/quote, identità Supabase, modello/condizioni AI, migrazioni, legale e tempo del fondatore. Se mancano gate, rinviare la beta indicando causa e responsabile; non togliere Calendar/PDF/auto-booking per far apparire rispettata la data.

## Prossime cinque azioni

1. TASK0: richiedere autorizzazione separata per #105 c832869 verso main, includendo pubblicazione GHCR senza deploy servizi. Dopo integrazione autorizzata, riallineare le PR dipendenti e rieseguire CI sugli head risultanti.
2. A03/B02: completare review/CI #111; sviluppare il trasporto Q&A con metriche originali, limiti della risposta prima del buffering, gate di accesso e guardrail, senza attivarlo prima delle prove richieste.
3. A02/A04: certificare riconciliazione billing e protocollo durevole dei tentativi; poi riserva atomica sul ledger esistente, con prove concorrenti/crash/replay e nessun secondo contatore.
4. A05/A06: recuperare autorizzazione deterministica booking e privacy; predisporre Google Calendar dopo i rispettivi gate, con test su account autorizzati.
5. C01/C02: conservare backup e chiavi per rollback; definire host/account e release applicativa Staging per E2E. Nuove migrazioni richiedono diff/review e autorizzazione propri. Nessuna spesa, modifica Production o deploy implicito.

## Integrazione

Piccole issue/PR; non pubblicare il diff locale di 49 file come unico intervento. Test prima dei difetti nuovi, review Sol, CI sul commit, controlli tenant/segreti e aggiornamento Graphify dopo codice. PR82/#83 candidate non ancora integrate. Confrontare locale e remote prima di estrarre frammenti. Nessun refactor delle feature escluse o cancellazione di worktree con lavoro non salvato.
