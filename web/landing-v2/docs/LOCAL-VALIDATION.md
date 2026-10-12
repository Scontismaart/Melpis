# Fase 1 — verifiche locali e approvazione tecnica Sol

**PASS locale · blueprint utilizzato YES · design Fase 1 approvato da Sol.** La PR e la CI identificano il commit finale; questo documento registra prove locali e non anticipa il risultato remoto.

| Voce | Stato / evidenza |
|---|---|
| Base | origin/main aggiornato `2994fae7ea54094482a40374ea46c08aae8d0407` |
| Branch integrazione | `codex/landing-v2` sperimentale, creato al base |
| Branch fase | `codex/landing-v2-phase-1` |
| Landing V1 | UNCHANGED: 650 file protetti del worktree byte-identici al base, inclusi tutto web, backend, traduzioni e configurazione produzione |
| Checkout originale | 1.036 file tracciati SHA256 identici allo snapshot iniziale; branch originale codex/phase-4 preservato con modifiche locali |
| Landing V2 | ISOLATED: solo web/landing-v2 e nuovo workflow dedicato nella PR |
| Stack | HTML/CSS/SVG e JS vanilla; nessuna dipendenza runtime |
| Build / controlli / test HTTP | PASS: build allowlist, check statici e test mirato del server |
| Browser | PASS: 97 controlli, desktop/mobile, 0 axe/overflow/errori |
| Sicurezza | PASS nel perimetro statico: nessun finding residuo; nessuna API, auth, provider, tracking o dato reale |
| Preview | READY: loopback 127.0.0.1:4175, `/landing-v2/`, distinta da V1 |
| Generated assets | NONE; una favicon SVG manuale originale, font locali copiati con OFL, niente immagini generate |
| Dipendenze QA | Playwright 1.62.1, axe-core 4.14.0, dev-only, lock isolato; npm audit 0 vulnerabilità |
| Routing/Docker/homepage/dashboard | UNCHANGED |
| Sitemap V1 | UNCHANGED; nessuna V2 inserita |
| Main | UNCHANGED da questa fase |
| Merge / deploy | NOT EXECUTED |
| Next | Fase 2 — hero cinematografica, SOLO dopo nuova approvazione utente |

JavaScript circa 0,5 KB gzip e CSS circa 7 KB gzip nel build finale locale, font circa 95 KB totali: dimensioni di trasferimento locali, non misure CWV. Nessuna immagine hero o dipendenza di rete. Le librerie candidate restano escluse; nessun componente aggiunto solo perché nominato.

Il modulo JS opera soltanto se il documento opta nel namespace V2; CSS confined e font family proprie non alterano un documento V1 sentinella anche se importati accidentalmente. Il tool `tools/isolation.mjs` usa byte di checkout, compresi gli attributi/line endings Windows; diff di commit limitato a V2 e nuovo workflow. Il grafo è stato aggiornato localmente con graphify, senza includere file generati nella PR.

Prezzi 29/69/149 riconciliati esclusivamente come listino mensile di riferimento. Trial, quote, sconti/annuali, imposte e inclusioni non pubblicati. Nessun account o checkout attivo: dominio ufficiale non verificabile via DNS in questa sessione, quindi CTA soltanto ad ancore reali della V2. Nessuna telefonia AI.

La V1 resta ufficiale. L'approvazione di Fase 1 o di una fase futura non autorizza sostituzione, merge o deploy. La decisione di sostituire/scartare resta dell'utente e richiede nuova autorizzazione.
