# MELPIS V1 — A00: attribuzione del diff residuo

Data: 18 ottobre 2026. Inventario circoscritto; nessuna modifica applicativa, test, commit, push, merge o chiamata ai provider eseguita per questa ricognizione. Attribuzione revisionata da Sol prima della pubblicazione; nessuna equivalenza runtime implicita.

## Sorgenti e conteggio

Repository corrente: `Scontismaart/Melpis`. Worktree di recupero preservato: `C:\Users\muzzo\Desktop\whatsapp-ai-responder\.worktrees\melpis-prelaunch-m1`, HEAD `a3d40ca8b88baced54e0e95a4243a11082f158db`.

`git status --short` e `git diff --stat` confermano **49 file tracciati modificati, due file applicativi/test non tracciati, un report storico non tracciato**. Diff tracciato: 1.300 inserimenti, 254 rimozioni. Le 51 righe numerate sotto coprono esattamente i 49+2 percorsi; il report è registrato separatamente. Nessun file sorgente o snapshot originale è stato modificato o eliminato.

Evidenze lette: inventario storico `docs/launch/MELPIS-STEP0-DIFF-SECURITY-REVIEW.md`, diff locale per hunk, file/diff remoti delle PR indicate, titoli delle issue #85–#102, backlog esistente. La sovrapposizione di percorso non prova equivalenza del codice o copertura dei test.

## Stato corrente delle integrazioni — snapshot 18 ottobre 2026

`main` remoto ora punta a `ca2459ab16b05268c06d7f4a547b7fd271682f97`, dopo l'integrazione della PR #112. La CI full post-merge è in corso; nessun risultato o pubblicazione GHCR è ancora attribuito a questo nuovo commit. Il precedente main `d56929a205656c797ce397bafda814839dab18a6` ha superato [CI39137587518](https://github.com/Scontismaart/Melpis/actions/runs/39137587518): 2.987 passati, 33 saltati, 58 warning, quattro immagini e due manifest multiarch immutabili API/web verificati. Nessun deploy o scrittura database.

- #103: integrata in `main` al commit `a25930ecd8ace14e4bf5ffa1492f56824e807793`, CI post-merge verde; issue #86 chiusa dopo verifica circoscritta. Il limiter non recupera il gate billing locale della stessa route.
- #116: integrata in `main` `2d26baf02204c81bb68b77cb06468b2b7907f922`; CI39113224120 verde, 2.752 passati/33 live esclusi. Solo test, A03 resta aperta. Non recupera gli hunk locali di autorizzazione del runner.
- #113: integrata in `main` `0e1c60b13ed8b78d4dcdfaab7fe54f9d3326b020`; CI39114444996 verde. Redirect OAuth e test non certificano Calendar o Google Cloud Production.
- #114: integrata in `main` `d526001fbcafdf275387531384eea9ef9194d791`; CI39115843721 verde completa e GHCR riuscita. Route/store GDPR, autorizzazione runtime e consumo atomico non certificano delete/retention né l'intera baseline globale delle route.
- #115: integrata in `main` `b82ca786364ebbcd17daaeebbed173ced09b3ff3`; CI39117170849 verde, 2.896 passati/33 live esclusi, quattro immagini e due manifest immutabili verificati con tag `sha-b82ca786364ebbcd17daaeebbed173ced09b3ff3`. Primo ciclo di cinque PR concluso, nessun deploy. Candidato `de4c3f`: CI39115288952 verde, 2.861 passati/33 live esclusi e 172 test billing locali; i vecchi head 74f0652 sono storici.
- #109: integrata in `main` `c3f432ef4d3085cc1fdb6e0491840bb0b40bafef`; [CI39134693561](https://github.com/Scontismaart/Melpis/actions/runs/39134693561) verde, 2.933 passati/33 saltati. Candidato `d6a9d114c1046f74e2bee2a9a77d7245b86f2a61`: CI39116119080 verde, 2.854 passati/33 live esclusi e 93 test locali. A03 resta aperta per metriche raw/tutti gli ingressi.
- #108: integrata in `main` `d56929a205656c797ce397bafda814839dab18a6`; [CI39137587518](https://github.com/Scontismaart/Melpis/actions/runs/39137587518) verde, 2.987 passati/33 saltati/58 warning; quattro immagini e due manifest immutabili API/web pubblicati e verificati. Digest API `sha256:706e533ebd981215493d34d978f3e57cc80a8a9ef271af03131cb63bf29e0e55`; web `sha256:c31807b555956f30e57a844fd8ab59e3ac41613d1cfd284c57e1ef8ca50ebca4`. Nessun deploy o scrittura database. A02 resta aperta per trial, limite 150, public checkout e riconciliazione webhook; A04 e quota atomica restano aperte.
- #112: integrata successivamente nel main corrente `ca2459ab16b05268c06d7f4a547b7fd271682f97`; trasporto isolato su due file, 48 test mirati locali passati. CI full post-merge pending; nessuna attivazione runtime Q&A, B02 non certificata.

Le tabelle di attribuzione sotto conservano la fotografia delle fonti all'inventario: gli SHA stacked non sono gli stati correnti delle PR. Prevale lo stato corrente sopra. Il consolidamento A02 #108 è integrato e ha CI post-merge verde sul commit `d56929a`; i gate di prodotto (trial, limite 150, public checkout, riconciliazione webhook) e A04 restano aperti. #112 è integrata sul successivo `ca2459a`, ma la CI full post-merge è pending. Nessuna certificazione runtime completa discende da queste CI; backup conservati, nessun deploy o modifica Production.

## Mappa completa dei 49 file modificati e dei due nuovi file

Ogni riga assegna un responsabile operativo già presente nel backlog. “Recuperato in PR” significa candidato pubblicato, salvo stato integrato esplicitato sopra; non significa merge o equivalenza integrale del file.

| N. | Percorso nel worktree originale | Issue e trattamento del frammento |
|---|---|---|
| 1 | `.env.production.example` | A02 #87: commento solo mensile già presente nel perimetro #115; confrontare il testo finale, evitare duplicazione. |
| 2 | `docs/CHECKLIST-LANCIO-DEFINITIVA.md` | B04 #95, con gate C04 #99: riconciliare copy mensile. Correggere anche la frase residua “sei Price ID mensili”; tre prezzi mensili previsti, nessuna configurazione live certificata. |
| 3 | `docs/operations/billing-release-runbook.md` | A02 #87: fonte già recuperata in #107, da riallineare con #115; nessuna seconda procedura annuale. |
| 4 | `src/core/billing/routes.py` | A02 #87: annuale negato in commercial_bootstrap, già trattato in #115; #108 tratta validazioni eventi diverse. Consolidare una sola versione. |
| 5 | `src/core/billing/webhook_handler.py` | A02 #87: hunk originale limita la mappa annuale; #108/#115 contengono hardening più ampio. Comparare il gate catalogo finale, non copiare il vecchio handler sopra quello nuovo. |
| 6 | `tests/core/billing/test_routes.py` | A02 #87: regressione public-config mensile; conservare il caso esplicito flag annuale true/commercial_bootstrap, evitando duplicati equivalenti nella suite #115. |
| 7 | `scripts/release_preflight.py` | A02 #87: hunk mensile recuperato #107/#115. B01 #92: hunk separato abilita Calendar condizionato a credenziali/callback, non incluso nelle PR #113/#115. Tenere readiness chiusa fino alle prove richieste. |
| 8 | `src/core/startup_guard.py` | A02 #87 per guard mensile #107/#115. B01 #92 per nuova policy Calendar true/false e credenziali; #113 fornisce validatore, non questo cambio di readiness. |
| 9 | `tests/core/auth/test_demo_mode_guard.py` | A02 #87 per casi annuale già #107/#115; B01 #92 per test distinti di Calendar abilitato, credenziali assenti e flag invalido. Non allentare la guard per far passare fixture. |
| 10 | `tests/unit/test_release_preflight.py` | A02 #87 per guard mensile già #107/#115; B01 #92 per casi Calendar abilitato/credenziali/callback. Fingerprint storico Gitleaks va riconciliato sul file finale senza eccezioni generiche. |
| 11 | `src/core/billing/suspension.py` | A03 #88: helper assert_ai_generation_allowed recuperato in #106. A02 #87: hunk is_org_suspended che nega past_due NON recuperato da #106; #115 pubblicata aggiunge soltanto trial_end_unix_timestamp. Decidere e uniformare policy centrale e chiamanti. |
| 12 | `tests/core/billing/test_suspension.py` | A02 #87/A03 #88: regressioni originali past_due/unpaid/incomplete_expired/unknown/None da recuperare insieme alla policy, non coperte dalla sola presenza del file suspension.py in #115. |
| 13 | `src/api/routes/organization.py` | A03 #88: gate prima della preview in #106, accounting in #109. #103 integrata aggiunge il limiter, non questo gate. Consolidare #106/#109 su main, preservando limiter #103. |
| 14 | `tests/unit/api/test_organization_routes.py` | A03 #88: vecchi test gate preview sostituiti/rafforzati dai casi dedicati #106/#109; confrontare matrice negazioni e caso positivo, evitare ricopia integrale. |
| 15 | `src/core/onboarding.py` | A03 #88: hunk gate recuperato/rafforzato #109 con accounting; consolidare quella fonte. |
| 16 | `tests/core/test_onboarding.py` | A03 #88: adeguamento billing presente nella fonte #109; mantenere test significativi senza duplicarli. |
| 17 | `src/core/crew_runner.py` | A03 #88: gate prima del provider e allow_unbilled_demo limitato al simulatore senza organizzazione. Frammento residuo; #116 modifica solo test. Richiede verifica dei chiamanti e metriche provider originali. |
| 18 | `src/core/crew_runner_report.py` | A03 #88: parametro billing e gate del report prima del provider. Residuo, con compatibilità chiamanti e accounting da certificare. |
| 19 | `src/core/crew_runner_review.py` | A03 #88: gate recensioni prima del provider. Recuperare protezione senza attivare Reviews, fuori perimetro V1. |
| 20 | `src/core/inbound/service.py` | A03 #88: propagazione billing_state già verificato al request orchestrator. Residuo, evitare fetch concorrenti incoerenti e perdita attribuzione tenant. |
| 21 | `src/core/receptionist/conversation_orchestrator.py` | A03 #88: gate spostato prima di embedding/cache; eccezione demo solo simulation senza org. Residuo, richiede metriche anche per embedding e fail-closed. |
| 22 | `src/core/reviews/ai_governance.py` | A03 #88: riuso gate centrale; Reviews resta disabilitato. Residuo, nessun nuovo fronte funzionale. |
| 23 | `src/api/routes/dashboard.py` | A03 #88: gate billing/report e passaggio snapshot al runner. Residuo, distinguere lettura cache da nuova generazione e conservare accesso dashboard/manuale. |
| 24 | `tests/core/test_report_agent.py` | A03 #88: fixture billing e regressioni collegate al runner report residuo. |
| 25 | `tests/unit/api/test_dashboard_routes.py` | A03 #88: matrice negazioni/caso positivo route report; recuperare insieme al relativo codice. |
| 26 | `tests/unit/test_crew_runner_review.py` | A03 #88: fixture billing per guard recensioni; non certifica attivazione Reviews. |
| 27 | `tests/unit/test_phase5_runner_safety.py` | A03 #88: fixture active_billing e negazioni runner locali restano residui. B01 #92: distinto hunk adatta helper booking a _google_busy_intervals. #116 integrata aggiunge solo prova modello/accounting, NON questi hunk. |
| 28 | `src/api/routes/knowledge.py` | B02 #93 con dipendenza A03 #88/A04 #89: gate prima di Q&A; residuo. #111 policy client e #112 trasporto non modificano questa route né attivano Q&A. |
| 29 | `src/core/documenti/qa_agent.py` | B02 #93/A03 #88: gate prima di embedding. Residuo da integrare con trasporto/accounting già preparati #111/#112, senza percorso AI duplicato. |
| 30 | `tests/core/test_qa_agent.py` | B02 #93: fixture/regressioni gate coerenti con il runner finale; non provano metriche provider originali o quota atomica. |
| 31 | `tests/core/test_conoscenza_e2e.py` | B02 #93: passaggio billing sintetico al Q&A; test locale, non E2E provider reale. |
| 32 | `src/core/calendar/routes.py` | B01 #92: recuperato in #113 validatore e redirect_uri esplicito Flow. Configurazione Google Cloud e OAuth reale restano C03 #98. |
| 33 | `src/core/calendar/service.py` | B01 #92: residuo FreeBusy fail-closed su credenziali/response/errori; sync restituisce esito. Verificare timezone/DST e distinzione lista busy vuota valida vs stato sconosciuto. |
| 34 | `src/core/scheduler.py` | B01 #92: residuo riconciliazione create/update/delete e last_sync_at solo su esito reale. A03 #88: hunk separato rimuove job report serale; validare assenza chiamate AI senza billing, evitare cancellazione funzionale implicita. |
| 35 | `tests/core/calendar/test_freebusy.py` | B01 #92: test residui errore/response malformata/credenziali, da recuperare insieme al servizio. Non inclusi #113. |
| 36 | `tests/unit/test_google_integration_flags.py` | B01 #92: casi Flow/redirect recuperati #113; non scambiare questi test per certificazione FreeBusy/scheduler/startup. |
| 37 | `src/core/bookings/__init__.py` | B01 #92 con A05 #90: esporta errore Calendar non disponibile; recuperare nello stesso frammento service/routes per evitare import rotto. |
| 38 | `src/core/bookings/routes.py` | B01 #92/A05 #90: traduce stato Calendar sconosciuto in 503 su availability/create/update. Residuo, confermare assenza mutazioni prima del rifiuto. |
| 39 | `src/core/bookings/service.py` | B01 #92/A05 #90: residuo disponibilità Calendar fail-closed e riuso snapshot busy. Non completa autorizzazione deterministica AI, auto/manuale o atomicità rispetto a cambi esterni. |
| 40 | `tests/core/bookings/test_booking_service.py` | B01 #92/A05 #90: casi residui Calendar sconosciuto/busy e capacità; aggiungere prove di nessuna mutazione e replay nel frammento finale. |
| 41 | `src/core/billing/plans.py` | B04 #95 con B01 #92: nuovo has_calendar=True per tre piani; residuo. Richiede matrice prodotto approvata ed enforcement coerente, non incluso #115. |
| 42 | `src/api/routes/common.py` | B04 #95/B01 #92: capability calendar e relativa negazione. Residuo legato a plans.py, non gate di readiness provider. |
| 43 | `tests/unit/api/test_mock_compatibility.py` | B04 #95/B01 #92: test Calendar nei tre piani, recuperare con capability finale. |
| 44 | `tests/core/billing/test_plans_pricing.py` | B04 #95/B01 #92: originale asserisce has_calendar in tutti i piani. #115 modifica lo stesso file per intervallo annuale/mensile; NON recupera il test Calendar. Conservare entrambi gli ambiti senza duplicarli. |
| 45 | `src/core/gdpr/routes.py` | A06 #91: migrazione cookie/download recuperata e rafforzata #114 con binding autorizzato. Usare #114, non reintrodurre il vecchio bearer locale non vincolato. |
| 46 | `src/core/gdpr/token_store.py` | A06 #91: cifratura/hash recuperati e rafforzati #114 con consumo atomico. Non sovrascrivere con vecchio snapshot. |
| 47 | `tests/core/gdpr/test_routes.py` | A06 #91: aggiornamenti download recuperati/estesi #114 (logout, tenant, account, autorizzazione). Restano delete/retention fuori da questo frammento. |
| 48 | `tests/core/gdpr/test_token_store.py` | A06 #91: cifratura/monouso recuperati/estesi #114. Verificare solo eventuali casi non equivalenti, evitando duplicati. |
| 49 | `docs/route_contracts_baseline.json` | A06 #91: soltanto nuovo contratto GDPR recuperato #114. D01 #101, tracciato anche in A00 #85: residuo globale MFA/auth/ruoli/route non equivale a GDPR. Riconciliare contro controlli runtime, NON importare la baseline intera per azzerare drift preesistente. |
| 50 | `src/core/calendar/redirect_uri.py` (nuovo non tracciato) | B01 #92: recuperato e rafforzato #113 (loopback non-Production, public HTTPS). Conservare originale finché il recupero è verificato; non aggiungere una seconda implementazione. |
| 51 | `tests/unit/test_calendar_redirect_validation.py` (nuovo non tracciato) | B01 #92: recuperato ed esteso #113; casi positivi/negativi ambienti/authority/path. Non certifica OAuth reale. |

## PR sovrapposte: sorgenti da consolidare, non duplicare

| PR | SHA letto | Funzione rispetto all'originale |
|---|---|---|
| #106 | `d5a82ecffb67ca46e9fad5b2e54be90ef3a3d3b7` | Gate preview e helper billing; stacked su #82, non ancora integrazione main. Non cambia la legacy policy is_org_suspended per past_due. |
| #107 | `c220d6a2b6f42fa1b7f47fef7a093b5ad783059a` | Guard/preflight/runbook mensili; #115 recupera parte runtime su main. Conservare il contenuto documentale non duplicato. |
| #108 | `226392f783422023c84e7d808ac798050098b3b2` | Head finale integrato in `d56929a`: validazione eventi, catalogo e binding. A02 resta aperta per i gate operativi; nessun merge automatico dello stack #82. |
| #109 | `e36fa7a46ef286288b7316b75826c7a172a0ee88` | Accounting preview su #106. Riutilizzare sink/propagazione testati; metriche originali provider e altri ingressi restano A03. |
| #111 | `c513d2bab2f2da5f0ed2dd4f564240c86014e362` | Policy client estratta; nessuno dei 51 percorsi originali modificato. Dipendenza preparatoria B02, non recupero del gate Q&A. |
| #112 | `598c66e882d8c487754e8f3f1cfecc8f4415742b` | Head integrato nel commit main `ca2459a`; trasporto raw isolato su due file, nessuno dei 51 percorsi originali modificato. 48 test mirati locali passati; CI full post-merge pending. Dipendenza preparatoria B02, non attivazione runtime né certificazione Q&A. |

## Report storico e limiti delle prove

Il cinquantaduesimo percorso conservato è `docs/launch/MELPIS-STEP0-DIFF-SECURITY-REVIEW.md` nel worktree originale: **A00 #85**, evidenza storica da mantenere. Riporta repository `Scontismaart/SaaS`, vecchia base `2994fae`, risultati locali del 14 ottobre e istruzioni di packaging allora vigenti. Non è autorizzazione corrente a pubblicare tutti i 49 file nella #82.

I suoi giudizi PASS e 367 test locali non certificano gli SHA attuali, la combinazione delle PR o i provider reali. La sua vecchia affermazione di assenza P1 non prevale sulle successive correzioni binding GDPR/Stripe. La discrepanza storica Gitleaks 124 dichiarati/122 osservati è registrata come dato non dimostrato; non inventare due finding, non dichiarare “zero segreti” globale e non aggiungere esclusioni generiche per rendere verde una scansione.

I 33 test live esclusi restano gate della beta. La baseline globale route presenta drift preesistente separato dal delta GDPR #114: aggiornare metadati senza controlli runtime non risolve autorizzazione. Nessun dato Production, backup o credenziale è stato letto o modificato per questa attribuzione.

## Criterio di chiusura A00

Attribuzione completata per **49+2 file e report storico**. La direttiva del fondatore nello sprint di integrazione consente di chiudere A00 quando i frammenti rilevanti sono integrati oppure assegnati esplicitamente ad altre issue, senza perdere codice o dati; questo aggiorna il precedente criterio della issue che richiedeva la pubblicazione di ogni frammento. I residui hanno issue esplicite #87/#88/#90/#91/#92/#93/#95/#101, originali preservati e fonti sovrapposte identificate. Attribuzione pubblicata nel commit `33a7e947486e4c5bb4ca4a448b3497fe800aa469` della PR104, con CI verde e collegamenti alle issue destinatarie; A00 #85 è stata quindi chiusa. Le tabelle restano una fotografia delle fonti all'attribuzione, non una dichiarazione che le relative PR siano ancora aperte oggi. La sua chiusura amministrativa non completa le implementazioni, le prove per frammento o la certificazione runtime delle issue assegnatarie; nessun import massivo del vecchio worktree.
