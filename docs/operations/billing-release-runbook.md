# Billing release runbook

## Profilo mensile-only

L'offerta annuale (€288 Starter, €708 Pro, €1.548 Business) è in attesa di approvazione. Il profilo production `commercial_bootstrap` richiede i tre Price ID mensili e `STRIPE_ANNUAL_PRICING_ENABLED=false`; i Price ID annuali non sono richiesti finché il flag resta disattivato. Il pricing V1 nasconde il selettore annuale quando l'endpoint same-origin `/api/billing/public-config` non conferma esplicitamente `true`. Un errore di rete mantiene il pricing mensile.

Il checkout `yearly` risponde con errore di configurazione quando il flag non è esattamente `true`. Il preflight e lo startup applicano la stessa regola fail-closed.

## Approvazione dell'annuale

Dopo l'approvazione commerciale, configurare tutti e tre i Price ID Stripe `price_...` verificati per Starter, Pro e Business, impostare `STRIPE_ANNUAL_PRICING_ENABLED=true`, quindi eseguire preflight e startup guard prima del rilascio. Non riutilizzare ID di prodotto (`prod_...`) al posto dei Price ID. In caso di rollback, disattivare prima il flag; i checkout annuali vengono negati e l'interfaccia torna mensile-only.

## Webhook e checkout

Webhook `invoice.paid` e `customer.subscription.updated` possono concedere piano, stato attivo o reset quota solo con un Price ID presente nella configurazione consentita. Prezzi sconosciuti vengono deduplicati e auditati senza estendere entitlement o azzerare quota. Gli stati restrittivi, come `past_due` e `canceled`, restano applicati anche se il prezzo non è riconosciuto.

Un checkout può avviare il trial solo quando la sessione Stripe corrisponde a un `billing_checkout_intent` persistito per la stessa organizzazione e il suo Price ID è configurato. Checkout senza intent durevole o con session_id non corrispondente vengono ignorati fail-closed. Se il webhook arriva prima che il chiamante API abbia persistito `session_id` (`session_id IS NULL`), l'handler valida l'intent non scaduto, associa atomicamente il `session_id` all'intent ed esegue l'attivazione senza perdere l'evento.
