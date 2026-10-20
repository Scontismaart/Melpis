# Melpis V1 — allineamento Git e stato locale

Aggiornamento: 18 ottobre 2026. Canonico Scontismaart/Melpis. Main corrente `ca2459ab16b05268c06d7f4a547b7fd271682f97`, integrata #112; CI full post-merge in corso. Nessun esito full/GHCR è attribuito al nuovo head. Il precedente main `d56929a205656c797ce397bafda814839dab18a6` e CI39137587518 sono verdi: 2.987 passati/33 saltati/58 warning, quattro immagini e due manifest GHCR immutabili verificati. Questo inventario distingue commit pubblicati, checkout locali, modifiche non committate e runtime; non attribuisce la CI remota a modifiche locali.

## Avanzamento corrente — sprint autorizzato del 18 ottobre 2026

Il fondatore ha autorizzato esplicitamente le integrazioni separate #103/#116/#113/#114/#115 e la pubblicazione GHCR prevista dai workflow. Ogni merge successivo ha atteso la CI post-merge precedente. Nessun deploy, riavvio dei servizi Production, migrazione remota, modifica live o eliminazione backup eseguita nello sprint.

| PR | Commit main | Prova post-merge |
|---|---|---|
| #103 | `a25930ecd8ace14e4bf5ffa1492f56824e807793` | [CI39082774889](https://github.com/Scontismaart/Melpis/actions/runs/39082774889) verde; 11 test di accettazione sul main esatto, #86 chiusa. |
| #116 | `2d26baf02204c81bb68b77cb06468b2b7907f922` | [CI39113224120](https://github.com/Scontismaart/Melpis/actions/runs/39113224120) verde, 2.752 passati e 33 live esclusi. Soli test, A03 aperta. |
| #113 | `0e1c60b13ed8b78d4dcdfaab7fe54f9d3326b020` | [CI39114444996](https://github.com/Scontismaart/Melpis/actions/runs/39114444996) verde. Google Cloud Production e OAuth reale non certificati. |
| #114 | `d526001fbcafdf275387531384eea9ef9194d791` | [CI39115843721](https://github.com/Scontismaart/Melpis/actions/runs/39115843721) verde completa, GHCR riuscita. Delete/retention restano aperti. |
| #115 | `b82ca786364ebbcd17daaeebbed173ced09b3ff3` | [CI39117170849](https://github.com/Scontismaart/Melpis/actions/runs/39117170849) verde: 2.896 passati/33 live esclusi; quattro immagini e due manifest immutabili verificati. |
| #109 | `c3f432ef4d3085cc1fdb6e0491840bb0b40bafef` | [CI39134693561](https://github.com/Scontismaart/Melpis/actions/runs/39134693561) verde, 2.933 passati/33 saltati; A03 resta aperta. |
| #108 | `d56929a205656c797ce397bafda814839dab18a6` | [CI39137587518](https://github.com/Scontismaart/Melpis/actions/runs/39137587518) verde, 2.987 passati/33 saltati/58 warning; 4 immagini GHCR e 2 manifest immutabili API/web verificati. Nessun deploy o scrittura DB; A02 resta aperta. |
| #112 | `ca2459ab16b05268c06d7f4a547b7fd271682f97` | CI full post-merge in corso; 48 test mirati locali passati. Due file di trasporto isolato; nessuna attivazione Q&A o certificazione B02. |

Il primo ciclo #103/#116/#113/#114/#115 è concluso. Per #115 sono verificate quattro immagini e due manifest API/web immutabili con tag `sha-b82ca786364ebbcd17daaeebbed173ced09b3ff3`, senza deploy dei servizi. #109 è stata integrata e verificata verde sul commit `c3f432e`; #108 è poi stata verificata verde con pubblicazione GHCR sul commit `d56929a`. #112 è stata successivamente integrata su `ca2459a`; la sua CI full post-merge è pending.

Candidato #115 `de4c3f`: [CI39115288952](https://github.com/Scontismaart/Melpis/actions/runs/39115288952) verde, 2.861 passati e 33 live esclusi; 172 test billing locali passati. Stripe Test, SDK/API `2024-06-20`: tre prezzi mensili 29/69/149 EUR, nove Checkout (tre piani × 7 giorni/49 ore/scaduto), parametri accettati e retry idempotente. Tentativi 47 ore/23 ore/30 minuti rifiutati da Stripe; il trial applicativo originale prosegue senza carta e senza estensione. Tutte le sessioni sintetiche fatte scadere, customer sintetico rimosso; nessun addebito reale o modifica alla configurazione live. Fattura/cancellazione/replay da subscription Test diretta trattati dall'handler de4c3f su PostgreSQL locale: non certificano consegna webhook pubblica o intera UI Checkout. A02 resta aperta per validatore subscription, rinnovi/fallimenti, riconciliazione e A04.

A00 #85 chiusa: inventario/recupero `33a7e94` e [attribuzione completa di 49+2 file](MELPIS-V1-A00-RESIDUAL-ASSIGNMENT.md); originali e snapshot preservati. La chiusura riguarda recupero/attribuzione; i residui applicativi hanno issue proprie, non certificazione implicita. A01 #86 chiusa. Totale: **2 completati, 4 in lavorazione, 7 bloccati, 5 da fare**; quattro attività ancora in lavorazione A02/A03/B02/C01.

#109 integrata main `c3f432ef4d3085cc1fdb6e0491840bb0b40bafef`, [CI39134693561](https://github.com/Scontismaart/Melpis/actions/runs/39134693561) verde: 2.933 passati/33 saltati. #108 poi integrata su `d56929a205656c797ce397bafda814839dab18a6`; [CI39137587518](https://github.com/Scontismaart/Melpis/actions/runs/39137587518) verde: 2.987 passati/33 saltati/58 warning e quattro immagini/due manifest immutabili API/web verificati. Digest: API `sha256:706e533ebd981215493d34d978f3e57cc80a8a9ef271af03131cb63bf29e0e55`, web `sha256:c31807b555956f30e57a844fd8ab59e3ac41613d1cfd284c57e1ef8ca50ebca4`. Nessun deploy o scrittura DB. #112 è stata integrata successivamente su `ca2459ab16b05268c06d7f4a547b7fd271682f97`; 48 test mirati locali passati, CI full post-merge pending. La PR tocca due file di trasporto isolato; Q&A non attivato e B02 non certificata. A03 resta aperta per metriche raw provider e copertura di tutti gli ingressi; B02 attende A03/A04 e la CI full. C01 attende prove applicative e schema finale. I 33 test live esclusi restano gate beta.

Consolidamento A02 nella #108 integrato e verificato nella CI post-merge sul commit `d56929a`. Restano rinnovi/fallimenti, trial, limite 150, public checkout, riconciliazione webhook e A04. La #112 è separata e integrata su `ca2459a`; la CI full non è ancora conclusa e non attiva il percorso Q&A.

Il batch Staging 058/059/UNIQUE era già applicato con backup/restore verificati; non ripetuto nello sprint. Backup cifrato e chiavi conservati, disponibili per rollback: eliminazione soltanto dopo conferma umana. Nessun deploy o modifica Production autorizzato da questa ricognizione. Le prove provider circoscritte non completano i gate esterni.

## Inventario storico — 16 ottobre 2026

Da questo punto le sezioni registrano la fotografia del 16 ottobre, baseline origin/main `2994fae7`: stato runtime/Supabase, PR aperte e assenza di merge appartengono a quella data. Lo stato corrente è sopra e nel [backlog aggiornato](MELPIS-V1-BACKLOG.md). In particolare Staging è stata poi riattivata e il batch autorizzato verificato; #103 non è più aperta e non è più sul vecchio commit f6e88da.

## Azioni eseguite

- Remote origin aggiornato da Scontismaart/SaaS al canonico Scontismaart/Melpis; fetch senza cancellazione di ref.
- Cartella principale: passaggio da codex/phase-4 21e5fd9 a main e fast-forward del precedente main locale 15926ed verso origin/main 2994fae7; nessun reset o cancellazione.
- I tre percorsi non tracciati della cartella principale sono conservati: docs/marketing/melpis-phase0-2026-10-12, docs/mirofish-context-melpis.md, web/landing/library-test.
- Branch locale codex/landing-v2 avanzato in fast-forward di otto commit verso il proprio remoto fc6457b; non integrato in main.
- Snapshot privato delle modifiche tracciate dei worktree sporchi e copia dei due file Python nuovi di m1; report locali conservati. Il percorso dei backup viene comunicato al fondatore, non pubblicato qui.
- Nessun branch eliminato; nessun worktree storico pulito o sporco cancellato; nessun merge/deploy effettuato.

## Coda integrazione

| Candidato | Commit | Stato | Azione |
| --- | --- | --- | --- |
| PR82 billing mensile | a3d40ca | OPEN, mergeable; validazione/build verdi sul remoto | Revisionare e integrare separatamente dopo autorizzazione; non include i 52 elementi locali |
| PR83 runbook | fe47d14 | OPEN, mergeable; validazione/build verdi sul remoto | Riutilizzare runbook aggiornandoli alla direzione V1; non dimostra stato provider |
| landing-v2 | fc6457b | Otto commit oltre main, nuovo albero web/landing-v2 | Conservare separata, allineare capacità/prezzi/disclosure prima pubblicazione; nessun redesign necessario per backend |
| m1 locale | HEAD a3d40ca + diff | 49 file tracciati modificati; due nuovi file applicativi/test e directory report | Recuperare in piccole PR dopo revisione e test; non riscrivere preview gate/Calendar/GDPR già presenti |
| task16/debito-tecnico locale | fa8588e | Un commit locale oltre il proprio remoto | Modifica il tooling agent; tenere separato dalla V1, nessun push automatico |

git merge-tree ha simulato PR82+PR83 e PR82+landing-v2 senza conflitti testuali. Nessuna esecuzione integrata è stata certificata: assenza di conflitti non prova compatibilità funzionale.

## Differenze locali da recuperare

| Area | Main/PR82 pubblicati | Solo locale m1 | Resto da fare |
| --- | --- | --- | --- |
| Billing/AI | PR82 mensile, prezzi sconosciuti, checkout intent | assert_ai_generation_allowed, preview e runner | Quota atomica, accounting provider effettivo, rate preview nuovo A01 |
| Calendar | Disabilitato in production | Abilitazione condizionata, capability, callback rigoroso, freebusy/sync fail-closed | Review/piccole PR, test reale OAuth/eventi e booking auto/manuale |
| GDPR | Endpoint e store irrigidito PR82 | Cookie HttpOnly, token hashato e payload cifrato | Export completo, delete durevole, retention, verifica Redis |
| Dashboard/report | Moduli presenti | Ulteriori gate/route e relativi test | Entitlement coerente, nessun blocco operazioni manuali a quota zero |

## Runtime locale osservato

- whatsapp-ai-responder-api-1: unhealthy; immagine whatsapp-ai-responder:latest creata il 26 settembre 2026, source bind dalla cartella principale e porta8000. Controlli read-only: /api/health/live=200; /api/health/ready=503, database non configurato (DATABASE_URL assente). Processo senza --reload. Cambiare checkout non certifica o riavvia il processo già avviato: code/image/config devono essere riallineati e collaudati prima di usare questo ambiente.
- phase7-runtime-api-1: healthy, ma sorgente montato dal worktree melpis-v1-phase7-8, quindi non è prova del commit main né della nuova release.
- Nessun riavvio dei runtime esistenti o accesso alle loro credenziali. QA della nuova patch usa un PostgreSQL temporaneo isolato, senza volumi o database esistenti.

## Inventario worktree (snapshot prima dei nuovi interventi)

| Branch/checkout | SHA | Elementi locali modificati/non tracciati |
| --- | --- | ---: |
| main | 2994fae7 | 3 |
| codex/dashboard-modularization-01 | be368fd7 | 0 |
| codex/dashboard-modularization-02 | 867d4666 | 0 |
| codex/dashboard-modularization-03a | 82ddf6f5 | 0 |
| codex/dashboard-modularization-03b | e7653390 | 0 |
| codex/dashboard-modularization-03c | 4a1fecb2 | 0 |
| codex/dashboard-modularization-03d | 194fa766 | 1 |
| codex/dashboard-modularization-04a | 01051712 | 1 |
| (detached: worktrees) | 3060374c | 0 |
| codex/dashboard-modularization-04b | d46905ed | 1 |
| codex/dashboard-modularization-05a | c92058f9 | 59 |
| (detached: dashboard-modularization-05a-main) | bae7abe3 | 1 |
| codex/dashboard-modularization-05b | e4b2a84d | 1 |
| codex/landing-v2-phase-2 | 70027c2a | 0 |
| codex/landing-v2-phase-3 | a0cc9673 | 0 |
| codex/landing-v2-phase-1 | e61ac650 | 0 |
| codex/landing-v2-phase-1b | dc3d51b9 | 0 |
| (detached: melpis-prelaunch) | 2994fae7 | 0 |
| codex/v1-roadmap-alignment | 2994fae7 | 0 |
| codex/melpis-v1-phase7-8 | 6d179ede | 58 |
| codex/v1-preview-rate-limit | 2994fae7 | 0 |
| (detached: phase4-tenant-isolation) | 794f74de | 0 |
| (detached: phase5-ai-hardening) | 90a6472a | 0 |
| codex/phase8-closed-beta-gate | 278e4ab6 | 0 |
| codex/security-replace-python-jose | ed19760e | 0 |
| codex/melpis-prelaunch-m1 | a3d40ca8 | 52 |
| codex/melpis-prelaunch-readiness | fe47d14c | 1 |
| codex/phase4-tenant-isolation | 85947601 | 0 |
| codex/phase5-ai-hardening | 1e8de4d1 | 0 |
| codex/phase6-authenticated-qa | 7b3c2094 | 0 |
| codex/phase7-integrations | 3e36917c | 48 |
| codex/ui-linear-dashboard | 42952d64 | 0 |
| task10/instagram-dm | 3df94b21 | 0 |
| task6/hitl-storico-badge | 740bbd93 | 0 |

Due registrazioni worktree puntano a directory mancanti (ci-trigger-all-branches e hitl): sono solo metadata prunabili, lasciati intatti. Le modifiche generate HTML/CSS e le directory QA dei worktree storici non sono state pubblicate.

## Catalogo branch locali

Dietro/avanti sono rispetto a origin/main; non indicano una proposta di rebase. Un branch storico già contenuto in main non va fast-forwardato verso main per far apparire tutto uguale.

| Branch locale | SHA | Dietro main | Avanti main | Upstream |
| --- | --- | ---: | ---: | --- |
| ai/aggiungi-una-docstring-in-italiano-alla-funzione-create-book | 8d486bc | 254 | 6 | origin/ai/aggiungi-una-docstring-in-italiano-alla-funzione-create-book |
| ai/rendi-timezone-aware-il-calcolo-del-cutoff-in-get-review-ana | fa8588e | 254 | 6 | locale soltanto |
| ai/test-dry-run-meccanica-pipeline | ec1b36c | 254 | 3 | locale soltanto |
| archive/p1-1-brand | 98cbc17 | 236 | 1 | locale soltanto |
| archive/p1-2-gdpr | 98cbc17 | 236 | 1 | locale soltanto |
| archive/p1-3-faq-cache | 98cbc17 | 236 | 1 | locale soltanto |
| archive/p1-p2-wip | 98cbc17 | 236 | 1 | locale soltanto |
| archive/p2-1-dlq-ui | 98cbc17 | 236 | 1 | locale soltanto |
| archive/p2-2-trial-banner | 98cbc17 | 236 | 1 | locale soltanto |
| archive/p2-3-competitor-validation | 98cbc17 | 236 | 1 | locale soltanto |
| backup-before-landing-recovery | 50a2263 | 108 | 2 | locale soltanto |
| backup-before-landing-rollback | 762c09b | 108 | 2 | locale soltanto |
| chore/cleanup-repo-tooling | 1dcab93 | 341 | 0 | origin/chore/cleanup-repo-tooling |
| codex/ci-trigger-all-branches | 57f6544 | 331 | 0 | origin/main |
| codex/dashboard-modularization-01 | be368fd | 27 | 0 | origin/codex/dashboard-modularization-01 |
| codex/dashboard-modularization-02 | 867d466 | 25 | 0 | origin/codex/dashboard-modularization-02 |
| codex/dashboard-modularization-03a | 82ddf6f | 23 | 0 | origin/codex/dashboard-modularization-03a |
| codex/dashboard-modularization-03b | e765339 | 21 | 0 | origin/codex/dashboard-modularization-03b |
| codex/dashboard-modularization-03c | 4a1fecb | 16 | 0 | origin/codex/dashboard-modularization-03c |
| codex/dashboard-modularization-03d | 194fa76 | 14 | 0 | origin/codex/dashboard-modularization-03d |
| codex/dashboard-modularization-04a | 0105171 | 10 | 0 | origin/codex/dashboard-modularization-04a |
| codex/dashboard-modularization-04b | d46905e | 8 | 0 | origin/codex/dashboard-modularization-04b |
| codex/dashboard-modularization-05a | c92058f | 5 | 0 | origin/codex/dashboard-modularization-05a |
| codex/dashboard-modularization-05b | e4b2a84 | 3 | 0 | origin/codex/dashboard-modularization-05b |
| codex/landing-v2 | fc6457b | 0 | 8 | origin/codex/landing-v2 |
| codex/landing-v2-phase-1 | e61ac65 | 0 | 3 | origin/codex/landing-v2-phase-1 |
| codex/landing-v2-phase-1b | dc3d51b | 0 | 5 | origin/codex/landing-v2-phase-1b |
| codex/landing-v2-phase-2 | 70027c2 | 0 | 7 | origin/codex/landing-v2-phase-2 |
| codex/landing-v2-phase-3 | a0cc967 | 0 | 10 | origin/codex/landing-v2-phase-3 |
| codex/melpis-prelaunch-m1 | a3d40ca | 0 | 6 | origin/codex/melpis-prelaunch-m1 |
| codex/melpis-prelaunch-readiness | fe47d14 | 0 | 5 | origin/codex/melpis-prelaunch-readiness |
| codex/melpis-v1-phase7-8 | 6d179ed | 29 | 0 | locale soltanto |
| codex/phase-4 | 21e5fd9 | 55 | 0 | locale soltanto |
| codex/phase4-tenant-isolation | 8594760 | 46 | 0 | origin/codex/phase4-tenant-isolation |
| codex/phase5-ai-hardening | 1e8de4d | 43 | 0 | origin/codex/phase5-ai-hardening |
| codex/phase6-authenticated-qa | 7b3c209 | 41 | 0 | origin/codex/phase6-authenticated-qa |
| codex/phase7-integrations | 3e36917 | 37 | 0 | origin/codex/phase7-integrations |
| codex/phase8-closed-beta-gate | 278e4ab | 38 | 0 | origin/codex/phase8-closed-beta-gate |
| codex/prelaunch-hardening | a1f6095 | 67 | 0 | origin/codex/prelaunch-hardening |
| codex/security-hardening | c60776b | 247 | 0 | locale soltanto |
| codex/security-replace-python-jose | ed19760 | 1 | 0 | origin/codex/security-replace-python-jose |
| codex/task-13-model-routing | 6a3fba1 | 327 | 0 | origin/codex/task-13-model-routing |
| codex/ui-linear-dashboard | 42952d6 | 49 | 0 | origin/codex/ui-linear-dashboard |
| codex/v1-preview-rate-limit | 2994fae | 0 | 0 | locale soltanto |
| codex/v1-roadmap-alignment | 2994fae | 0 | 0 | locale soltanto |
| deepseek/roadmap-wip | 7e678a5 | 345 | 0 | locale soltanto |
| feat/auth-authorization | f9104c6 | 391 | 0 | locale soltanto |
| feat/booking-airtable-hardening | c7c2443 | 130 | 0 | origin/feat/booking-airtable-hardening |
| feat/p1-resilience-airtable-ai | d25ffea | 138 | 0 | locale soltanto |
| feature/architecture-consolidation-phases-1-5 | d25ffea | 138 | 0 | origin/feature/architecture-consolidation-phases-1-5 |
| feature/bookings-redesign | 2154610 | 182 | 0 | origin/feature/bookings-redesign |
| feature/landing-page-and-web-assets | 4d5b4a1 | 152 | 0 | origin/feature/landing-page-and-web-assets |
| feature/launch-readiness | aecd4f6 | 198 | 0 | origin/feature/launch-readiness |
| feature/site-restyle | e782a1f | 223 | 0 | locale soltanto |
| fix/P1-5-blocker | 483dd5d | 225 | 0 | origin/fix/P1-5-blocker |
| fix/google-oauth-login | 99f14e1 | 220 | 0 | origin/fix/google-oauth-login |
| fix/migration-idempotency | c2ad6db | 154 | 0 | origin/fix/migration-idempotency |
| fix/migrations-yml | fb636a6 | 291 | 0 | origin/fix/migrations-yml |
| fix/p0-blockers | 75b163e | 242 | 0 | locale soltanto |
| fix/p0-blockers-v2 | 483dd5d | 225 | 0 | origin/fix/p0-blockers-v2 |
| fix/security-audit-and-prompt-hardening | 12f2e3b | 165 | 0 | origin/fix/security-audit-and-prompt-hardening |
| fixing-dashboard | 40081c2 | 175 | 0 | locale soltanto |
| main | 2994fae | 0 | 0 | origin/main |
| master | ea0da99 | 375 | 0 | locale soltanto |
| task10/instagram-dm | 3df94b2 | 291 | 0 | origin/task10/instagram-dm |
| task10/quick-wins-b1b3b4 | f68bf48 | 347 | 0 | origin/task10/quick-wins-b1b3b4 |
| task11/google-reviews-integration | a0bd4eb | 343 | 0 | origin/task11/google-reviews-integration |
| task12/guardrails | 3197604 | 272 | 0 | origin/task12/guardrails |
| task12/pgvector-documenti | 8ba6fdd | 332 | 0 | origin/task12/pgvector-documenti |
| task13/hitl-sla-inbox | 891ff32 | 318 | 0 | origin/task13/hitl-sla-inbox |
| task14/multilingua | 89525f5 | 264 | 0 | origin/task14/multilingua |
| task14/onboarding-production-ready | af3ccab | 314 | 0 | origin/task14/onboarding-production-ready |
| task15/rag-whatsapp-hitl | 50be9f0 | 308 | 1 | origin/task15/rag-whatsapp-hitl |
| task16/debito-tecnico | fa8588e | 254 | 6 | origin/task16/debito-tecnico |
| task17/analytics-report-vendibili | 5a49fd9 | 249 | 0 | origin/task17/analytics-report-vendibili |
| task18/auth-bff | 9301128 | 244 | 0 | origin/task18/auth-bff |
| task6/hitl-storico-badge | 740bbd9 | 291 | 0 | origin/task6/hitl-storico-badge |
| task9/reviews-fixes | 057a9d0 | 349 | 0 | origin/task9/reviews-fixes |

## Catalogo branch remoti (fetch corrente)

| Branch remoto | SHA | Dietro main | Avanti main |
| --- | --- | ---: | ---: |
| origin | 2994fae | 0 | 0 |
| origin/ai/aggiungi-una-docstring-in-italiano-alla-funzione-create-book | 8d486bc | 254 | 6 |
| origin/codex/dashboard-modularization-01 | be368fd | 27 | 0 |
| origin/codex/dashboard-modularization-02 | 867d466 | 25 | 0 |
| origin/codex/dashboard-modularization-03a | 82ddf6f | 23 | 0 |
| origin/codex/dashboard-modularization-03b | e765339 | 21 | 0 |
| origin/codex/dashboard-modularization-03c | 4a1fecb | 16 | 0 |
| origin/codex/dashboard-modularization-03d | 194fa76 | 14 | 0 |
| origin/codex/dashboard-modularization-04a | 0105171 | 10 | 0 |
| origin/codex/dashboard-modularization-04b | d46905e | 8 | 0 |
| origin/codex/dashboard-modularization-05a | c92058f | 5 | 0 |
| origin/codex/dashboard-modularization-05b | e4b2a84 | 3 | 0 |
| origin/codex/landing-v2 | fc6457b | 0 | 8 |
| origin/codex/landing-v2-phase-1 | e61ac65 | 0 | 3 |
| origin/codex/landing-v2-phase-1b | dc3d51b | 0 | 5 |
| origin/codex/landing-v2-phase-2 | 70027c2 | 0 | 7 |
| origin/codex/landing-v2-phase-3 | a0cc967 | 0 | 10 |
| origin/codex/melpis-prelaunch-m1 | a3d40ca | 0 | 6 |
| origin/codex/melpis-prelaunch-readiness | fe47d14 | 0 | 5 |
| origin/codex/melpis-v1-phase7-8 | 6d179ed | 29 | 0 |
| origin/codex/phase3-auth | 0dfa639 | 56 | 0 |
| origin/codex/phase4-tenant-isolation | 8594760 | 46 | 0 |
| origin/codex/phase5-ai-hardening | 1e8de4d | 43 | 0 |
| origin/codex/phase6-authenticated-qa | 7b3c209 | 41 | 0 |
| origin/codex/phase7-integrations | 3e36917 | 37 | 0 |
| origin/codex/phase8-closed-beta-gate | 278e4ab | 38 | 0 |
| origin/codex/prelaunch-hardening | a1f6095 | 67 | 0 |
| origin/codex/release-prelaunch | e326982 | 79 | 0 |
| origin/codex/security-replace-python-jose | ed19760 | 1 | 0 |
| origin/codex/task-13-model-routing | 6a3fba1 | 327 | 0 |
| origin/codex/ui-linear-dashboard | 42952d6 | 49 | 0 |
| origin/dependabot/docker/python-3.14-slim-bookworm | f6955a8 | 66 | 1 |
| origin/dependabot/docker/web/nginx-1.31-alpine | ce05be7 | 2 | 1 |
| origin/dependabot/docker/web/node-26-alpine | 75443ea | 0 | 1 |
| origin/dependabot/github_actions/actions/cache-6 | 744bde0 | 66 | 1 |
| origin/dependabot/github_actions/actions/checkout-7 | d71db37 | 66 | 1 |
| origin/dependabot/github_actions/actions/setup-node-7 | dce84a8 | 66 | 1 |
| origin/dependabot/github_actions/actions/setup-python-7 | babc686 | 66 | 1 |
| origin/dependabot/npm_and_yarn/eslint-10.12.0 | 74c4a14 | 2 | 1 |
| origin/dependabot/npm_and_yarn/globals-17.13.0 | 9fd7c93 | 40 | 1 |
| origin/dependabot/npm_and_yarn/jsdom-30.1.2 | eb2b582 | 40 | 1 |
| origin/dependabot/pip/alembic-1.20.0 | bcf5173 | 66 | 1 |
| origin/dependabot/pip/google-api-python-client-2.201.0 | c5ed648 | 40 | 1 |
| origin/dependabot/pip/google-auth-httplib2-0.4.4 | 1cdaca3 | 40 | 1 |
| origin/dependabot/pip/pyairtable-3.5.0.post1 | 8dbcd33 | 48 | 1 |
| origin/dependabot/pip/redis-8.1.0 | 19e0316 | 0 | 1 |
| origin/feat/booking-airtable-hardening | c7c2443 | 130 | 0 |
| origin/feat/hitl-shared-inbox | 42780b0 | 356 | 0 |
| origin/feature/architecture-consolidation-phases-1-5 | d25ffea | 138 | 0 |
| origin/feature/bookings-redesign | 2154610 | 182 | 0 |
| origin/feature/landing-page-and-web-assets | 4d5b4a1 | 152 | 0 |
| origin/fix/migration-idempotency | c2ad6db | 154 | 0 |
| origin/fix/migrations-yml | fb636a6 | 291 | 0 |
| origin/main | 2994fae | 0 | 0 |
| origin/task10/instagram-dm | 3df94b2 | 291 | 0 |
| origin/task12/guardrails | 3197604 | 272 | 0 |
| origin/task12/pgvector-documenti | 8ba6fdd | 332 | 0 |
| origin/task13/hitl-sla-inbox | 891ff32 | 318 | 0 |
| origin/task14/onboarding-production-ready | af3ccab | 314 | 0 |
| origin/task15/rag-whatsapp-hitl | 50be9f0 | 308 | 1 |
| origin/task16/debito-tecnico | ff08f63 | 254 | 5 |
| origin/task6/hitl-storico-badge | 740bbd9 | 291 | 0 |

Le PR Dependabot e la vecchia PR22 restano candidate separate: nessun upgrade major aggregato durante i P0. Le due PR nuove di questo lavoro non inglobano branch storici o codice locale non revisionato.

## Supabase — metadati verificati in sola lettura

- Melpis Production: ACTIVE_HEALTHY, eu-central-1, PostgreSQL17. Storico migrazioni registrato fino057. Query di catalogo read-only: auth_access_lifecycle e current_auth_access_allowed() assenti; outbound_dedup ha RLS attiva; pgvector0.8.2. Mancanza059 è un blocco di compatibilità Auth con main. La sola history non permette di dichiarare058 assente: il controllo RLS conferma che quel requisito è attivo.
- Melpis Staging: INACTIVE, eu-central-1. Non riattivato automaticamente; nessun test dati o migrazione eseguito.
- Project ID esatti conservati nell’evidenza privata per distinguere gli ambienti; i vecchi nomi Selecta/Selectaa non sono stati usati come prova. Nessuna credenziale letta o configurazione modificata.
- Advisor security: unico avviso restituito, leaked password protection disabilitata. Verificare policy/password/MFA e disponibilità del controllo senza upgrade di piano automatici: [documentazione Supabase](https://supabase.com/docs/guides/auth/password-security#password-strength-and-leaked-password-protection). Non equivale a certificazione completa dell’isolamento applicativo.

## Nuovi interventi verificati

- codex/v1-preview-rate-limit, commitf6e88da, [PR103](https://github.com/Scontismaart/Melpis/pull/103): limite preview condiviso per tenant e aggregato,19test passati con PostgreSQL isolato. Non integrato.
- codex/v1-roadmap-alignment: specifica V1,18issue85–102, roadmap e catalogo branch. Nessuna modifica di policy commerciale applicata automaticamente.
- Snapshot privato tracciato m1 conservato nel ref locale refs/melpis-recovery/m1-20261016; i due file Python nuovi sono salvati nel backup esterno. Nessuna modifica del worktree originale.
- Merge-tree fra patch preview e PR82 e fra patch preview e snapshot tracciato locale: nessun conflitto testuale. Il semplice git apply della patch main fallisce sul contesto modificato di organization.py; usare la normale integrazione a tre vie o estrazione revisionata, non sovrascrivere il file locale. Il risultato combinato non è ancora certificato dai test.
