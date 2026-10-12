# Commercial sources — Landing V2 Fase 1

Verifica 12 ottobre 2026. Baseline `origin/main` = `2994fae7ea54094482a40374ea46c08aae8d0407`. Fonti soltanto lette; nessun runtime Python/backend eseguito e nessuna modifica commerciale V1.

## Listino di riferimento autorizzato

| Piano | Mensile di riferimento | Fonte codice canonico | Fonte pagina pubblica locale |
|---|---:|---|---|
| Essenziale | €29/mese | src/core/billing/plans.py:22–32, price_monthly_eur=29 | web/landing/prezzi/index.html:400, confronto :609 |
| Crescita | €69/mese | src/core/billing/plans.py:35–45, price_monthly_eur=69 | web/landing/prezzi/index.html:433, confronto :614 |
| Scala | €149/mese | src/core/billing/plans.py:48–58, price_monthly_eur=149 | web/landing/prezzi/index.html:466, confronto :618 |

La richiesta utente 29/69/149 e il baseline applicativo concordano; è riconciliazione documentale del solo importo mensile. Copy ammesso: “Listino mensile di riferimento”. Non equivale a conferma di prezzo effettivo del checkout Stripe né a offerta pubblicata dalla V2. Nessuna credenziale Stripe consultata, nessuna checkout session aperta, nessun acquisto o abbonamento creato.

Escludere in Fase 1 IVA inclusa/esclusa, tasse, costi Meta, quote, trial, annuali, risparmi e sconti, inclusioni di piano e badge “più scelto”: quei dati non sono stati approvati dal presente controllo ristretto. Il codice contiene ulteriori valori ma la loro presenza non autorizza ripubblicarli. Il documento storico `melpis-piano-pricing-definitivo.md` (€49/99/199) non prevale su richiesta attuale + origin/main. Nessuna modifica a billing/plans.py.

## Destinazioni: esistenza locale distinta da disponibilità live

| Destinazione futura | Riscontro locale nel baseline | Stato pubblicazione |
|---|---|---|
| /registrati/ | src/api/main.py:638–641 → web/register.html presente | Non verificata live; non attivare CTA V2 |
| /accedi/ | src/api/main.py:633–636 → web/login.html presente | Non verificata live; non attivare CTA V2 |
| /documentazione/ | web/landing/documentazione/index.html presente; root StaticFiles html=True src/api/main.py:689–690 | Non verificata live; non attivare link V2 |
| /prezzi/ | web/landing/prezzi/index.html presente; stesso root static mount | Non verificata live; non attivare link V2 |
| /privacy/, /termini/, /cookie/ | route e file response src/api/main.py:643–666 | Non trasferire policy V1 automaticamente nella preview |

Il 12 ottobre 2026 `Resolve-DnsName melpis.it` restituisce “Nome DNS inesistente”; il controllo web di https://melpis.it/ risulta non accessibile. Questo è stato della verifica in sessione, non diagnosi dell'ambiente di produzione. Il codice locale non dimostra route live, permessi, migrazioni o contenuti del dominio.

CTA Fase 1: ancore interne realmente presenti, ad esempio `#mv2-percorso`, `#mv2-piani`, `#mv2-setup`. Nessun account/trial/checkout esterno, href vuoto, pulsante finto o auth servita dal server V2. Il worker UI deve verificare gli ID finali e il QA deve provarne destinazioni mouse/touch/tastiera. Futuro gate: verificare dominio raggiungibile + destinazione finale autentica + copy commerciale e privacy prima di abilitare CTA esterne.

## Security review dell'architettura proposta

`PHASE-1-ARCHITECTURE.md` propone preview Node loopback 127.0.0.1, docroot limitata al dist V2, allowlist build, nessun proxy V1/backend, traversal respinto, documenti/tool non serviti. Strategia appropriata all'ambito statico; efficacia da verificare sui tool implementati dal Developer, non dichiarata conclusa qui.

CSP richiesta: risorse script/style/font same-origin, `connect-src 'none'`, object/frame/form/base disabilitati; nessuno script/style inline nella pagina distribuibile, eval o handler inline. `noindex,nofollow` e header robots disallow non sono confini di sicurezza, ma riducono indicizzazione accidentale. Nessun listener su tutte le interfacce, cookie auth, localStorage, service worker, tracking, API o chiamata LLM.

Dati soltanto sintetici marcati; nessuna prenotazione/invio reale. Gli SVG originali sono decorativi e non riproducono dashboard/account sensibili. Le fonti benchmark e i documenti reference non entrano nell'allowlist distribuibile. Nessun segreto/account/telefono reale negli asset. Zero privilegi riduce la superficie: nessun cambio a tenant scope, consenso, billing o auth.

Verifiche richieste prima approvazione tecnica: network con soli asset V2, CSP senza violazioni, path traversal e file sviluppo negati, diff/hash file protetti invariati, assenza API/forms/third-party, ancore e pricing labels coerenti. Questa revisione non autorizza merge/deploy o nuova Fase 2.
