# Billing release runbook

## V1 mensile e trial

Piani Essenziale/Crescita/Scala: 29/69/149 EUR al mese. `STRIPE_ANNUAL_PRICING_ENABLED=false` è richiesto nel profilo `commercial_bootstrap`; startup guard e preflight bloccano l'annuale. Il client mostra solo il mensile se la configurazione pubblica non conferma il flag. Un'offerta annuale futura richiede una modifica esplicita revisionata.

Il trial applicativo termina dopo sette giorni o 150 risposte, al primo limite. Non richiede carta per iniziare, non viene ricreato dai webhook e non si converte implicitamente a pagamento. La quota commerciale delle risposte è distinta dai tentativi provider; la prenotazione atomica resta A04.

Stripe SDK 10.12.0, API 2024-06-20: Checkout `subscription_data.trial_end` richiede almeno 48 ore nel futuro. Il server usa un margine di cinque minuti: con meno di 48 ore e cinque minuti il checkout viene differito e il trial applicativo residuo continua senza carta. Non si arrotonda né si estende la scadenza. A trial scaduto un checkout esplicito usa `payment_method_collection=always`, senza `trial_end`; con trial eleggibile usa `if_required`. Il minimo di trenta minuti della Checkout Session è un vincolo separato.

## Identità, intent e ordinamento

Gli intent persistono organizzazione, Customer, piano, Price mensile e scadenza originale. Retry riusa payload e idempotency key; un risultato ambiguo non autorizza un nuovo trial o checkout. La subscription deve corrispondere a quella associata oppure a una prova provider firmata e all'intent durevole.

La risoluzione `checkout.session.completed/expired` è indipendente dal cursore globale entitlement. Completion ritardata non sovrascrive una subscription già nota. Gli eventi entitlement sono serializzati per organizzazione e deduplicati nella stessa transazione delle mutazioni e dell'audit. Eventi vecchi e cancellazione terminale non riattivano diritti. I pareggi richiedono prove sullo stesso oggetto; pagato/fallito della stessa fattura non equivalgono a eventi di fatture differenti.

## Fatture e snapshot subscription

Solo `invoice.paid` verificata può assegnare piano, limiti, nuovo periodo pagato o reset quota. Sono richiesti pagamento positivo EUR e un solo servizio subscription mensile non prorato, quantità intera uno, Price mensile configurato univocamente, subscription coerente e periodo valido della linea. Non usare il periodo globale della fattura, che può descrivere il ciclo precedente. La prima inizializzazione è protetta da marker intent e storico audit, anche con consegna ritardata.

`customer.subscription.updated` non concede nuovi diritti. Lo stato `active` può soltanto conservare un'organizzazione già attiva sullo stesso piano e identico periodo verificato. Lo stato `trialing` conserva soltanto il trial applicativo già esistente e ne può accorciare la scadenza; non assegna i limiti del piano pagato né resetta il consumo. Un pareggio temporale permette soltanto un accorciamento verificato del trial.

Per gli snapshot positivi il catalogo richiede un solo item, `has_more=false`, quantità intera uno, Price mensile univoco, EUR, importo nominale esatto e periodo ordinato. Nessun fallback da Product o Price annuale. Catalogo invalido o nuovo pagamento non dimostrato produce 503 prima della deduplica e del cursore, così una fattura ritardata può essere riconciliata. Stati restrittivi si applicano anche con catalogo ignoto, senza allentare uno stato più severo.

## Riconciliazione e rilascio

Una risposta 503 è un evento da riprovare/riconciliare, non un evento riuscito. Conservare evento e stato provider per la verifica, senza registrare segreti o payload personali. Non alterare manualmente il cursore per concedere accesso. Il riconciliatore operativo automatico e i flussi di prorazione restano da certificare; nessuna prova locale li sostituisce.

Prima della beta verificare consegna pubblica firmata, completion Checkout completa, rinnovi, upgrade, fallimenti, cancellazioni e quota atomica A04. I 33 test live opt-in esclusi dalla CI non sono test superati.

## Prove Stripe Test del 18 ottobre 2026

Catalogo autorizzato verificato: tre Price mensili EUR 29/69/149. Nove Sessioni sintetiche (tre piani per sette giorni, 49 ore e trial scaduto) hanno confermato i parametri serializzati e il retry idempotente; Stripe ha rifiutato `trial_end` con 47 ore, 23 ore e trenta minuti. Sessioni scadute e soli Customer sintetici rimossi.

Una subscription Test diretta con carta ufficiale simulata ha prodotto una fattura pagata elaborata dall'handler locale: periodo della linea, limiti, reset iniziale, replay senza doppio reset e cancellazione terminale verificati. Nessun addebito reale, nessuna modifica al catalogo/configurazione live. Il consolidamento subscription è stato inoltre provato con lo snapshot API 2024-06-20 della stessa subscription Test: stato attivo, identico periodo pagato e consumo già incrementato restano invariati. La prima esecuzione aggiuntiva ha trovato lo schema locale minimale lasciato dal test di cancellazione; dopo ripristino dello schema completo tramite fixture autorizzata, la prova è passata. Questa prova usa oggetti provider reali in Test mode e consegna locale simulata: non certifica il webhook pubblico né l'intera UI Checkout.

Nessun deploy o migrazione remota fa parte di questo runbook. Backup cifrato Staging e originali restano conservati; eliminazione solo dopo conferma del fondatore.