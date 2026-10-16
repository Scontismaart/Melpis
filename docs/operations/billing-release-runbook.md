# Billing release runbook

## Profilo mensile-only

Il profilo production V1 `commercial_bootstrap` offre solo i piani mensili e richiede `STRIPE_ANNUAL_PRICING_ENABLED=false`; i Price ID annuali non sono richiesti. Il pricing V1 nasconde il selettore annuale quando l'endpoint same-origin `/api/billing/public-config` non conferma esplicitamente `true`. Un errore di rete mantiene il pricing mensile. Impostare il flag a `true` da solo non abilita l'annuale: lo startup guard e il preflight bloccano questa configurazione nel profilo V1.

Il checkout `yearly` risponde con errore di configurazione quando il flag non è esattamente `true`. Il preflight e lo startup applicano la stessa regola fail-closed.

## Eventuale offerta annuale futura

Un'eventuale offerta annuale richiede una modifica futura ed esplicita del profilo, una nuova revisione di sicurezza e test aggiornati. Non basta impostare `STRIPE_ANNUAL_PRICING_ENABLED=true`. Fino a quella modifica, lasciare il flag a `false`; il catalogo e il checkout V1 restano mensili.

## Webhook e checkout

Webhook `invoice.paid` e `customer.subscription.updated` possono concedere piano, stato attivo o reset quota solo con un Price ID presente nella configurazione consentita. Prezzi sconosciuti vengono deduplicati e auditati senza estendere entitlement o azzerare quota. Gli stati restrittivi, come `past_due` e `canceled`, restano applicati anche se il prezzo non è riconosciuto.

Un checkout può avviare il trial solo quando la sessione Stripe corrisponde a un `billing_checkout_intent` persistito per la stessa organizzazione e il suo Price ID è configurato. Checkout senza intent durevole o con session_id non corrispondente vengono ignorati fail-closed. Se il webhook arriva prima che il chiamante API abbia persistito `session_id` (`session_id IS NULL`), l'handler valida l'intent non scaduto, associa atomicamente il `session_id` all'intent ed esegue l'attivazione senza perdere l'evento.
