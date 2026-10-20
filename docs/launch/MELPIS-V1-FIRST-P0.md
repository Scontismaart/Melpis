# V1-A01 — prima implementazione verificata

Issue: [#86](https://github.com/Scontismaart/Melpis/issues/86). PR: [#103](https://github.com/Scontismaart/Melpis/pull/103), aperta/in review; CI da seguire sul commit esatto. Branch: codex/v1-preview-rate-limit. Base: main2994fae7. Commit: f6e88da92f5fdb4c5a46f07266489df10aab735a. Responsabili: Sol architettura/security/review; Luna implementazione; QA indipendente.

## Comportamento

/api/onboarding/preview entra nel limite aggregato LLM e ha limite10richieste/60secondi condiviso dall'organizzazione autenticata, prima di repository, billing, retrieval o provider. Undicesima richiesta429. Errore limiter blocca le operazioni successive. Nessun cambio a dashboard/manuale, prezzi, quote mensili, Calendar/RAG o autenticazione.

Tre file: src/api/main.py, src/api/routes/organization.py, tests/unit/api/test_onboarding_preview_rate_limit.py. Identità simulate come membership già verificata nei nuovi test: non è una certificazione Supabase o Redis production.

## Prove

- Baseline iniziale: 4test passati e5errori setup PostgreSQL non disponibile sul target predefinito.
- RED prima del codice: cinque regressioni fallite per assenza limiti tenant/globale; due test auth/scope già passati.
- GREEN Luna:14test passati su preview, org limiter e route organizzazione.
- QA indipendente:11test passati su preview e org limiter; nessuna osservazione bloccante.
- Sol: suite con PostgreSQL16/pgvector temporaneo, nuovo container senza volumi esistenti e porta loopback assegnata automaticamente:19passati,0falliti. Comprende tutti i14unit e5route onboarding con schema/migrazioni reali. Container fermato e rimosso a fine test; nessun DB preesistente usato.
- Comando finale: python -m pytest tests/unit/api/test_onboarding_preview_rate_limit.py tests/unit/api/test_org_rate_limit.py tests/unit/api/test_organization_routes.py tests/core/test_onboarding_routes.py -q
- Ruff selettori CI E9,F63,F7,F82:PASS su src/tests/scripts. Scanner tenant:PASS. git diff --check:PASS.
- Graphify update eseguito nel worktree codice:11614nodi/26383archi/545community; parserSQL opzionale assente, avviso registrato; output ignorati non committati.
- CI GitHub e merge restano gate separati: lo stato iniziale è in review, non merged.

## Limiti residui

Questa patch limita l'abuso della preview. Non risolve billing indisponibile, accounting effettivo o quota mensile atomica: A00/A03/A04. Il limite aggregato pre-auth eredita il compromesso già esistente di consumo globale da richieste negate. Nessun provider live, addebito, deploy o credenziale live modificato.
