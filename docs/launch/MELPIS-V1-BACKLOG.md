# Melpis V1 — backlog e roadmap quattro settimane

Aggiornamento: 18 ottobre 2026. [Specifica corrente](MELPIS-V1-SPEC.md). Primo ciclo autorizzato #103/#116/#113/#114/#115 concluso, con CI post-merge verificata prima del merge successivo. Anche #109 è stata poi integrata separatamente: main corrente `c3f432ef4d3085cc1fdb6e0491840bb0b40bafef`, CI39134693561 in corso. #115 ha CI/GHCR post-merge verdi. Sol mantiene architettura, sicurezza, interfacce e review; Luna interviene sui frammenti circoscritti. Nessun deploy, modifica Production, nuova migrazione remota o eliminazione backup eseguita nello sprint.

## Stato operativo verificato — 18 ottobre 2026

18 task ufficiali: **2 completati, 4 in lavorazione, 7 bloccati, 5 da fare**. A00 #85 è chiusa per recupero/inventario `33a7e94` e assegnazione completa dei 51 file; i residui applicativi hanno issue esplicite e non sono dichiarati certificati. A01 #86 è chiusa dopo merge #103 e accettazione sul commit main esatto. A02/A03/B02/C01 restano in lavorazione. Il batch schema Staging 058/059/UNIQUE era già stato applicato con approvazione e backup/restore verificati; non è stato ripetuto. Il backup cifrato è conservato. I 33 test live esclusi, gli E2E remoti e i gate della beta restano distinti dalle CI verdi.

| Task / issue | Owner | Stato | PR / dipendenze | Prova e blocco | Prossima azione |
|---|---|---|---|---|---|
| A00 / #85 | Sol | Completato | inventario 33a7e94; assegnazione 49+2 file | issue chiusa; originali/snapshot preservati; residui assegnati | eseguire i residui nelle issue competenti, senza import massivo #82 |
| A01 / #86 | Luna, review Sol | Completato | #103, main a25930e | CI39082774889 verde; 11 test di accettazione sul main esatto | nessun gate beta rimosso; accounting/quota restano A03/A04 |
| A02 / #87 | Sol; Luna su guard | In lavorazione | #115 integrata b82ca78; consolidamento locale #108 | #115 post-merge: 2896 passati, 33 live esclusi, GHCR verificata; 172 test billing locali | validatore subscription in lavorazione locale; poi rinnovi/fallimenti, riconciliazione e A04 |
| A03 / #88 | Sol | In lavorazione | #116 e #109 integrate; main c3f432e | #109 candidato: 2854 passati/33 esclusi, 93 test locali; CI39134693561 post-merge in corso | verificare CI integrata, metriche raw provider e tutti gli ingressi |
| A04 / #89 | Sol | Bloccato | A02/A03; prerequisito Q&A | proposta Architect e prescrizioni Security; nessuna quota atomica finale certificata | Sol: chiudere prerequisiti A02/A03; nel frattempo contratti e fixture locali di crash/concorrenza |
| A05 / #90 | Sol | Da fare | A00 | capacità/idempotenza presenti, autorizzazione da completare | policy deterministica auto/manuale |
| A06 / #91 | Sol | Da fare | #114 integrata d526001 | protezione download verificata; export completo/delete/retention restano aperti | completare gli altri criteri privacy senza riscrivere #114 |
| B01 / #92 | Sol | Bloccato | #113 integrata 0e1c60b; A05 | redirect rigoroso e loopback non-Production; Calendar non certificato | FreeBusy/sync/booking e Google Cloud/OAuth reale restano gate |
| B02 / #93 | Sol, Luna su componenti | In lavorazione | A03/A04; PR111/#112 | #112: 48 test locali nuovamente passati; trasporto isolato, runtime non attivato | metriche raw, gate/accounting/quota e limiti per piano prima dell'attivazione |
| B03 / #94 | Luna, review Sol | Da fare | B01/B02 | wizard 7 passi presente | verificare persistenza, disclosure, errori |
| B04 / #95 | Sol, Luna su UI | Bloccato | A02/A04/B02 | prezzi confermati; metrica/entitlement incompleti | Sol/Luna: attendere contratto A04 e limiti B02; nel frattempo inventario dei punti UI/copy |
| C01 / #96 | Sol + fondatore | In lavorazione | A04/A05/A06 per schema finale | 058/059/UNIQUE applicate; backup/restore80tabelle PASS; 42RLS | certificare applicazione/provider e schema finale |
| C02 / #97 | Sol + fondatore | Bloccato | C01 | #83 runbook; nessun host applicativo esterno creato (fondatore), inventario locale completato | Fondatore: risorse/account e budget; Sol: piano di attivazione. Nel frattempo runbook e prove release/backup |
| C03 / #98 | Sol + fondatore | Bloccato | B01/B03/C02 | E2E Meta/Google mancante | Fondatore: host/account autorizzati dopo B01/B03/C02; nel frattempo contract test locali |
| C04 / #99 | Sol + fondatore | Bloccato | A02/A04/C02 | parametri Checkout accettati da Stripe Test; fattura/cancel/replay su handler e PG locale | completare UI Checkout, consegna webhook pubblica, rinnovi/fallimenti ed E2E integrati |
| C05 / #100 | Sol + fondatore | Da fare | A06/B03 | policy AI e testi da approvare | inventario provider e costo rappresentativo |
| D01 / #101 | Sol, Luna su QA | Bloccato | tutti i gate A/B/C | release integrata non pronta | Sol: chiudere gate A/B/C; nel frattempo matrice prove e CI sui commit precisi |
| D02 / #102 | Fondatore + Sol | Da fare | D01 | non avviato, subordinato al go/no-go | preparare coorte 3–5 attività |

### Integrazioni autorizzate — 18 ottobre 2026

| PR | Commit main integrato | CI post-merge | Esito verificato |
|---|---|---|---|
| #103 | `a25930ecd8ace14e4bf5ffa1492f56824e807793` | [39082774889](https://github.com/Scontismaart/Melpis/actions/runs/39082774889) | Verde; accettazione #86: 11 test sul main esatto. |
| #116 | `2d26baf02204c81bb68b77cb06468b2b7907f922` | [39113224120](https://github.com/Scontismaart/Melpis/actions/runs/39113224120) | Verde; 2.752 test passati, 33 live esclusi. Soli test, A03 aperta. |
| #113 | `0e1c60b13ed8b78d4dcdfaab7fe54f9d3326b020` | [39114444996](https://github.com/Scontismaart/Melpis/actions/runs/39114444996) | Verde; callback verificato nel codice, Google Calendar non certificato. |
| #114 | `d526001fbcafdf275387531384eea9ef9194d791` | [39115843721](https://github.com/Scontismaart/Melpis/actions/runs/39115843721) | Verde completa, pubblicazione GHCR riuscita. Privacy completa ancora aperta. |
| #115 | `b82ca786364ebbcd17daaeebbed173ced09b3ff3` | [39117170849](https://github.com/Scontismaart/Melpis/actions/runs/39117170849) | Verde; 2.896 passati, 33 live esclusi, quattro immagini e due manifest immutabili verificati. |
| #109 | `c3f432ef4d3085cc1fdb6e0491840bb0b40bafef` | [39134693561](https://github.com/Scontismaart/Melpis/actions/runs/39134693561) | In corso; consolidamento successivo al primo ciclo, A03 resta aperta. |

Primo ciclo di cinque PR concluso. #115 ha pubblicato quattro immagini e due manifest API/web immutabili con tag verificato `sha-b82ca786364ebbcd17daaeebbed173ced09b3ff3`. Questa pubblicazione non ha eseguito deploy. L'esito del candidato #109 non sostituisce la verifica ancora in corso sul commit main `c3f432e`.

Candidato #115 `de4c3f`: [CI39115288952](https://github.com/Scontismaart/Melpis/actions/runs/39115288952) verde, **2.861 test passati e 33 live esclusi**, più 172 test billing locali. Trial originale conservato, nessuna conversione implicita, solo mensile. Stripe Test ha verificato tre prezzi 29/69/149 EUR mensili e nove Checkout (tre piani × trial 7 giorni/49 ore/scaduto): parametri SDK/API `2024-06-20` accettati e retry idempotente. Tentativi con residuo 47 ore/23 ore/30 minuti rifiutati da Stripe, coerentemente con il vincolo provider; il flusso applicativo conserva il trial senza richiedere carta per mantenerlo attivo. Tutte le sessioni sintetiche sono state fatte scadere e il customer sintetico rimosso. Nessun addebito reale né modifica alla configurazione live.

Una subscription Test diretta ha prodotto fattura e cancellazione; eventi e replay sono stati trattati dall'handler `de4c3f` su PostgreSQL locale. Queste prove **non certificano consegna webhook pubblica né intero percorso UI Checkout**. Restano validatore rigoroso della subscription, rinnovi/fallimenti, riconciliazione e contratto A04.

#109 integrata in main `c3f432ef4d3085cc1fdb6e0491840bb0b40bafef`, [CI39134693561](https://github.com/Scontismaart/Melpis/actions/runs/39134693561) in corso. Il candidato `d6a9d114c1046f74e2bee2a9a77d7245b86f2a61` modifica tre file runtime/quattro test: 93 test locali e [CI39116119080](https://github.com/Scontismaart/Melpis/actions/runs/39116119080) verde, 2.854 passati/33 live esclusi/quattro build riuscite. #112 ha nuovamente passato 48 test locali; nessun percorso Q&A attivato. La certificazione A03 richiede ancora metriche della risposta originale e copertura di tutti gli ingressi; B02 attende A03/A04. C01 attende prove applicative e schema finale.

Ulteriore consolidamento A02 nella #108 esistente: validatore rigoroso dello snapshot subscription in lavorazione locale, non ancora pubblicato né dichiarato certificato. Il contratto proposto non concede nuove quote, upgrade o periodi; usa la scadenza trial minima e consente soltanto transizioni restrittive senza rilassarle. Rinnovi/fallimenti, riconciliazione e A04 restano aperti; nessuna duplicazione del vecchio stack.

### TASK 0 — dipendenze CI (storico verificato del 17 ottobre 2026)

**Merge autorizzato completato.** La #110 era stata integrata nella #105 al commit c832869. Dopo verifica della CI esatta e conferma del fondatore che non esistono host applicativi esterni, la [PR105](https://github.com/Scontismaart/Melpis/pull/105) e stata integrata in main al commit `7988f32f419019c650d77ebe9fe4a16366f5c374`. Albero identico al candidato c832869/f322359 gia revisionato. [CI post-merge](https://github.com/Scontismaart/Melpis/actions/runs/39022106060) completata verde: 2.744 test passati, 33 test live opt-in esclusi, audit 204 dipendenze e build API/web amd64/arm64 riuscite. Immagini multiarch lette da GHCR con tag SHA e digest verificati. Nessun deploy dei servizi e stato eseguito; il fondatore conferma che non esiste ancora un host applicativo esterno.

**Correzione.** La wheel ufficiale CrewAI 1.15.4 imponeva MCP 1.28.x, interessato da CVE-2026-59951. Il builder riproducibile modifica soltanto METADATA/RECORD: 526 file, codice/versione/licenza inclusi, restano identici. Il runtime usa MCP 1.30.0, installer condiviso CI/migrazioni/Docker e bootstrap pip 26.2.1 verificato con SHA negli ambienti isolati. La policy respinge audit incompleti, pacchetti saltati e duplicati; confronta inventario e versioni esatte anche nell'immagine finale senza privilegi. Le cinque eccezioni preesistenti Chroma/json-repair restano invariate; nessuna nuova eccezione. Provenienza e procedura sono documentate in `docs/REVIEWED-RUNTIME.md` su main.

**Prove su f322359.** [CI completa verde](https://github.com/Scontismaart/Melpis/actions/runs/38991228923): **2.744 test passati, 33 test live opt-in esclusi**. Audit OSV: 204 dipendenze, nessun pacchetto escluso, inventario esatto; build API e web native amd64/arm64 riuscite. Sette test MCP passati, inclusi handshake/tool/error HTTP e SSE su server locali reali. Ripetizione locale sull'immagine finale non-root, senza rete esterna: inventario verificato e sette test passati. I 33 esclusi riguardano provider live; non certificano gli E2E esterni.

**Nuova verifica completata.** [CI esatta sul candidato c832869](https://github.com/Scontismaart/Melpis/actions/runs/39018034399) interamente verde: 2.744 test passati, 33 test live opt-in esclusi, audit 204 dipendenze senza pacchetti esclusi e quattro build API/web amd64/arm64 riuscite. In quel controllo main era invariato e auto-merge disabilitato; la pubblicazione e avvenuta solo sul successivo merge autorizzato. Le prove non certificano provider live né il completamento billing/Q&A/quota. PR103 riallineata a main al commit 5b7da92, con 23 test unit e CI completa verde. PR104 ha base aggiornata localmente e documentazione in chiusura; #106-#109 restano da riallineare secondo dipendenze e autorizzazioni.

**Pubblicazione e deploy.** I workflow pubblicano immagini soltanto su push a main; i job PR saltano la pubblicazione. I tag sono SHA e i digest multiarch API/web del commit 7988f32 sono verificati direttamente su GHCR; Compose richiede riferimenti per digest. Nessun deploy dei servizi e stato eseguito. Non risultano workflow di deploy, webhook o deployment GitHub. Inventario locale: due contesti Docker locali, 38 container senza updater/socket Docker montato, 220 azioni pianificate senza riferimenti a deploy Melpis e 302 servizi senza updater Melpis. SSH/OCI senza host configurati; il fondatore conferma che gli host applicativi esterni devono ancora essere creati. I due progetti cloud confermati sono i database Supabase, letti solo come metadati. Nel monitoraggio post-merge (snapshot 09:42 e controllo 09:44 UTC), 38/38 container del campione mantengono immagine/avvio/restart invariati; lo snapshot pre-merge esteso non e conservato separatamente. Nessuna operazione su database o servizi Production. La futura creazione di host richiede un nuovo controllo della configurazione di aggiornamento.

### Certificazioni distinte — fotografia storica del 17 ottobre 2026

Le due righe seguenti descrivono il ciclo precedente; lo stato corrente delle PR e dei task è riportato sopra. Non interpretare “aperte/non integrate” come stato successivo allo sprint del 18 ottobre.

- Sviluppato localmente: limiter preview (#103), gate billing preview A00 (#106), isolamento tool audit (#105), guard mensile startup/preflight (#107, 107 test), validazione eventi Stripe (#108, 60 test indipendenti), accounting preview (#109, 37 test anche in QA indipendente), piano C01. Accounting completo e quota atomica restano aperti.
- Integrato in main: fondazione CI/runtime #105, commit 7988f32; #105/#110 chiuse. #82/#83/#103/#104/#106/#107/#108/#109/#111/#112 aperte. #111 e basata su main, head c513d2b invariato, CI completa verde. PR112 e draft su main, head 598c66e, CI avviata; test locali mirati 48/48, review Sol completata. QA indipendente finale senza verdetto per limite di utilizzo. Nessun merge o deploy di PR112 autorizzato.
- Staging: batch approvato applicato, backup cifrato reale e restore PG17.6 isolato verificati su 80 tabelle, catalogo/ACL/trigger e dati. Solo ledger migrazioni varia fra le tabelle preesistenti. **Nessuna certificazione E2E applicativa/provider remota**. Backup e chiavi DPAPI conservati fino a conferma umana per eliminazione.
- Produzione: nessun nuovo rilascio o modifica autorizzata.

[Batch C01, hash, impatto accessi e backup/rollback](MELPIS-V1-STAGING-PLAN.md). Il backfill 059 riguarda 27 subject storici: 7 senza membership restano negati. Approvazione del batch e del restore locale ricevute; BitLocker, sicurezza PC, spazio, ACL e TLS verificati. Container temporaneo rimosso; backup cifrato conservato.

## Dipendenze

A00 ha completato recupero/inventario e attribuzione: [mappa 49+2 file](MELPIS-V1-A00-RESIDUAL-ASSIGNMENT.md), issue #85 chiusa. I residui restano nelle issue competenti; non autorizza un merge automatico #82. A01 è completata. A02 consolida #115 con i contratti ancora mancanti; A03 accounting e A04 quota atomica precedono certificazione funzionale. A05/A06 chiudono booking/privacy. Calendar/PDF obbligatori in B. Le preparazioni esterne C restano distinte dalle certificazioni.

## [V1-A00](https://github.com/Scontismaart/Melpis/issues/85) — Recuperare e separare l'hardening locale esistente

- Priorità/fase: P0/A. Responsabile: Sol.
- Dipendenze: nessuna.
- Componenti: Worktree melpis-prelaunch-m1; 49 file tracciati modificati e due nuovi moduli/test; PR #82/#83.
- Test: Revisione diff e segreti; suite per ciascun frammento; CI sul nuovo commit.
- Accettazione originaria: Ogni frammento è attribuito, verificato e pubblicato in una piccola PR; nessuna modifica locale viene persa o dichiarata coperta dalla vecchia CI. La chiusura operativa #85 registra inventario/recupero e assegnazione completa; l'implementazione residua è esplicitamente trasferita alle issue competenti, non dichiarata completata.
- Rischi: Diff ampio e interdipendente; possibili cambi di policy non approvati.
- Stato/prove corrente: **Completato, issue #85 chiusa**. Inventario/recupero `33a7e94`, assegnazione esatta 49 file modificati + due nuovi, zero omissioni/duplicati; report storico classificato separatamente. Snapshot e worktree originali conservati. [Mappa dei residui](MELPIS-V1-A00-RESIDUAL-ASSIGNMENT.md): copertura per hunk e issue #87/#88/#90/#91/#92/#93/#95/#101. Le fonti #106/#107/#108/#109 non vanno duplicate né considerate già tutte integrate.

## [V1-A01](https://github.com/Scontismaart/Melpis/issues/86) — Limitare la preview onboarding prima di retrieval e provider

- Priorità/fase: P0/A. Responsabile: Luna; review Sol.
- Dipendenze: nessuna.
- Componenti: src/api/main.py; src/api/routes/organization.py; test route preview.
- Test: 10 richieste ammesse/11a 429; stesso tenant condiviso; tenant separati; header falsificato; limiter guasto; limite globale.
- Accettazione: Limite 10/60 secondi per organizzazione autenticata applicato prima di qualsiasi lavoro AI; route inclusa nel limiter globale; nessuna chiamata successiva quando negata.
- Rischi: Riduce abuso; non certifica quota mensile o accounting.
- Stato/prove corrente: **Completato, issue #86 chiusa**. #103 integrata con autorizzazione al commit main `a25930ecd8ace14e4bf5ffa1492f56824e807793`; CI39082774889 verde. Undici test di accettazione sul main esatto verificano limite tenant/aggregato, ordine prima di AI e rifiuto senza lavoro successivo. Le precedenti prove sul candidato `5b7da92` restano storiche; i 33 test live non sono stati considerati eseguiti e restano gate beta. Il limiter non certifica A03/A04.

## [V1-A02](https://github.com/Scontismaart/Melpis/issues/87) — Integrare in sicurezza il billing mensile della PR #82

- Priorità/fase: P0/A. Responsabile: Sol.
- Dipendenze: nessuna.
- Componenti: PR #82; billing/webhook_handler.py, checkout.py, routes.py; startup_guard.py; preflight.
- Test: Prezzi sconosciuti/mancanti, tutti i line item, checkout intent, duplicati, eventi fuori ordine e tre piani mensili.
- Accettazione: Solo catalogo approvato concede entitlement; annuale non proposto nel lancio; tutti i test sul commit revisionato. Merge separato autorizzato dal fondatore.
- Rischi: PR82 aperta; CI verde vale solo per a3d40ca, non per diff locale.
- Stato/prove corrente: **In lavorazione**. #115 integrata in main `b82ca786364ebbcd17daaeebbed173ced09b3ff3`, CI39117170849 verde con 2.896 passati/33 live esclusi e GHCR immutabile verificata. Candidato `de4c3f` verde (2.861 passati, 33 live esclusi), 172 test billing locali e prove Stripe Test circoscritte descritte sopra. Consolidamento del validatore subscription nella #108 esistente in lavorazione locale, non ancora pubblicato: nessun nuovo entitlement/quota/upgrade/periodo dallo snapshot, trial minimo e transizioni restrittive. A02 resta aperta per questa verifica, rinnovi/fallimenti, riconciliazione e A04; nessun merge automatico #82 né certificazione UI/consegna pubblica.

## [V1-A03](https://github.com/Scontismaart/Melpis/issues/88) — Certificare gate AI e accounting dei tentativi effettivi

- Priorità/fase: P0/A. Responsabile: Sol.
- Dipendenze: V1-A00, V1-A01.
- Componenti: crew_runner*.py; onboarding.py; routes/common.py; reviews/ai_governance.py; documenti/qa_agent.py.
- Test: Billing assente/errore, trial scaduto, quota zero, accounting indisponibile; modello effettivo, token e latenza per ogni tentativo.
- Accettazione: Nessuna chiamata senza tenant e stato verificato; ogni tentativo anche fallito è attribuito; metriche derivano dal provider; accounting irrisolto blocca nuove generazioni.
- Rischi: La preview attuale ricostruisce il modello invece di propagare le metriche effettive.
- Stato/prove corrente: **In lavorazione**. #116 integrata main `2d26baf02204c81bb68b77cb06468b2b7907f922`, CI39113224120 verde con 2.752 passati/33 live esclusi: soli test. #109 integrata in main `c3f432ef4d3085cc1fdb6e0491840bb0b40bafef`, CI39134693561 post-merge in corso; candidato `d6a9d114c1046f74e2bee2a9a77d7245b86f2a61` con 93 test locali e CI39116119080 verde (2.854 passati/33 live esclusi). #111 resta policy condivisa isolata; #112 trasporto isolato, 48 test locali nuovamente passati e runtime non attivato. Metriche raw provider e tutti gli ingressi restano da certificare; A04 resta separata.

Ulteriore gate A03: CrewAI 1.15.4 normalizza alcuni campi token mancanti a zero prima degli eventi; i test #109 certificano il collegamento al runner e al ledger, non la presenza dei campi nella risposta originale del provider. Dopo l'integrazione circoscritta #109, il contratto raw delle metriche e l'isolamento dei collector restano obbligatori prima della certificazione A03 e dell'attivazione dei percorsi dipendenti. Non interpretare metriche ignote come consumo zero. Q&A documentale richiede ancora gate, guardrail e accounting rigoroso.

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
- Stato/prove corrente: #114 integrata in main `d526001fbcafdf275387531384eea9ef9194d791`, CI39115843721 verde completa e GHCR riuscita: autorizzazione runtime download, binding e consumo atomico verificati. Export completo, delete/retention e propagazione restano da completare; A06 non chiusa. Drift globale preesistente distinto dal contratto GDPR, nessun import massivo della vecchia baseline.

## [V1-B01](https://github.com/Scontismaart/Melpis/issues/92) — Completare Google Calendar mantenendo il gate di readiness

- Priorità/fase: P0/B. Responsabile: Sol.
- Dipendenze: V1-A00, V1-A05.
- Componenti: calendar/service.py, routes.py, redirect_uri.py locale; scheduler.py; startup_guard.py; preflight.
- Test: OAuth nonce/MFA/tenant; freebusy errore/risposta vuota/malformata; timezone/DST; create/update/delete; retry e revoca.
- Accettazione: Disponibilità sconosciuta non equivale a libera; abilitazione condizionata a credenziali e callback valide; eventi e booking riconciliati senza duplicati; prova reale in C03.
- Rischi: Main vieta Calendar in production; locale è candidato non certificato.
- Stato/prove corrente: #113 integrata in main `0e1c60b13ed8b78d4dcdfaab7fe54f9d3326b020`, CI39114444996 verde. Callback pubblico HTTPS e loopback HTTP strettamente non-Production verificati nel codice. FreeBusy/sync/booking, configurazione Google Cloud Production e OAuth reale restano gate; non dichiarare Calendar certificato.

## [V1-B02](https://github.com/Scontismaart/Melpis/issues/93) — Completare PDF/RAG tenant-scoped e coerente con i piani

- Priorità/fase: P0/B. Responsabile: Sol; Luna su upload/UI/test.
- Dipendenze: V1-A03, V1-A04.
- Componenti: routes/knowledge.py; documenti/*; document_repo.py; dashboard-knowledge.js; billing/plans.py.
- Test: PDF valido/corrotto/scansione senza testo; limiti byte/pagine/chunk; tenant diversi; documenti inattivi; injection, conflitti fonti e rollback indicizzazione.
- Accettazione: PDF caricato è pronto solo dopo indicizzazione completa; retrieval usa solo fonti autorizzate e attive del tenant; nessuna istruzione documento autorizza azioni; entitlement e limiti visibili coerenti.
- Rischi: MiniLM locale in processi multipli e vettori PostgreSQL consumano memoria/storage; RAG attuale solo Scala.
- Stato/prove corrente: pipeline PDF/estrazione/chunk/embedding/retrieval esistente. #112: 48 test locali nuovamente passati sul trasporto isolato, runtime non attivato; #111 prepara policy client condivisa. B02 attende metriche raw/gate/accounting A03 e quota A04, oltre a limiti per piano e matrice approvata. Nessuna duplicazione dei frammenti già pubblicati.

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
- Stato/prove corrente: Stripe Test ha accettato tre prezzi mensili 29/69/149 EUR e nove Checkout (tre piani × 7 giorni/49 ore/scaduto) con retry idempotente; 47 ore/23 ore/30 minuti rifiutati dal provider. Sessioni sintetiche scadute, customer sintetico rimosso; nessun addebito reale o configurazione live modificata. Fattura/cancellazione/replay di subscription Test diretta trattati dall'handler de4c3f su PostgreSQL locale. Consegna webhook pubblica, percorso UI Checkout, rinnovi/fallimenti, A04 e Live restano da certificare.

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

1. Primo ciclo di cinque PR concluso, inclusa CI/GHCR verde #115. Seguire CI39134693561 di main `c3f432ef4d3085cc1fdb6e0491840bb0b40bafef` dopo #109; mantenere distinto il candidato verde dal risultato post-merge ancora in corso.
2. A03: certificare metriche raw provider e tutti gli ingressi dopo l'integrazione #109. #116 è integrata come soli test; A03 resta aperta. Aggiornare #104 con la documentazione corrente.
3. A02/A04: consolidare validatore subscription, rinnovi/fallimenti e riconciliazione senza duplicare #107/#108; preparare riserva atomica sul ledger esistente, con concorrenza/crash/replay e nessun secondo contatore.
4. B02/A05/A06/B01: usare #111/#112 senza attivazione Q&A prima di A03/A04; eseguire i residui esplicitamente assegnati, preservando le protezioni già integrate #113/#114. Nessuna certificazione Calendar/privacy completa implicita.
5. C01/C02/C04: conservare backup e chiavi per rollback; definire host e prove applicative Staging. Le prove Stripe Test circoscritte non completano UI/webhook pubblico. Nuove migrazioni, deploy, Production, eliminazione backup e spese richiedono autorizzazioni proprie.

## Integrazione

Piccole issue/PR; non pubblicare il diff locale di 49 file come unico intervento. Review Sol, CI sul commit e controlli tenant/segreti; aggiornare Graphify dopo modifiche al codice. Lo sprint ha autorizzato e integrato separatamente #103/#116/#113/#114/#115, non un merge automatico #82/#83 o dei vecchi stack. Confrontare locale e remoto prima di estrarre residui già assegnati; nessun refactor estraneo o cancellazione di worktree con lavoro conservato. Backup cifrato disponibile per rollback fino a conferma umana della sua eliminazione.
