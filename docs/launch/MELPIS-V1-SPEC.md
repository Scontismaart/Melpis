# Melpis V1 — specifica tecnica corrente

Data: 16 ottobre 2026. Fonte: direzione esplicita del fondatore allegata a questa sessione. Canonico: Scontismaart/Melpis. Questa specifica sostituisce la precedente proposta di escludere Calendar o PDF/RAG. Stato: requisiti confermati nel brief, implementazione e provider ancora da certificare.

## Perimetro obbligatorio

Beta chiusa per 3–5 attività italiane; outreach e supporto del fondatore. WhatsApp H24, informazioni aziendali autorizzate, FAQ/dati strutturati/PDF-RAG, Google Calendar disponibilità/eventi, prenotazioni automatiche o a conferma manuale configurabile, escalation, dashboard, wizard esistente, multilingua, disclosure AI, Stripe mensile, GDPR, backup e recovery sono requisiti V1. Nessun requisito può essere rimosso per abbreviare la roadmap.

Instagram, Google Reviews, multi-location, white label e analytics avanzate sono esclusi dal primo rilascio. Non cancellare il codice senza necessità: mantenerlo disattivato e fuori dalle promesse V1. Airtable resta un adapter opzionale solo se sostenibile, sicuro e certificato, senza rendere la beta dipendente da esso.

## Prezzi e quote confermati

| Piano | Prezzo mensile | Risposte AI per periodo |
| --- | ---: | ---: |
| Essenziale | EUR29 | 500 |
| Crescita | EUR69 | 2.000 |
| Scala | EUR149 | 10.000 |

Trial: 7 giorni o 150 risposte, al primo limite, senza carta e senza rinnovo a pagamento implicito. Solo mensile al lancio. Nessun prezzo modificato. Quota esaurita blocca nuove generazioni AI, preservando dashboard e intervento manuale. Rinnovo valido o upgrade riabilitano secondo il periodo; replay ed eventi Stripe fuori ordine non duplicano consumo o reset. PR82 è candidata esistente, ancora aperta.

## Definizione tecnica delle risposte — requisito confermato, da certificare in A04

Una unità prodotto è una risposta AI finale valida, passata dai guardrail e persistita come disponibile per cliente/operatore. Chiave operazione server-side unica per tenant e messaggio/richiesta: replay e retry contano una volta. La risposta salvata conta anche se la consegna Meta fallisce; il retry di consegna non genera né conta un'altra risposta. Preview e Q&A che generano una risposta finale concorrono alla stessa quota, evitando percorsi alternativi di generazione gratuita. Una rigenerazione esplicita è una nuova operazione.

Classificazioni interne, embeddings, retrieval, tentativi scartati/falliti e messaggi deterministici non consumano risposte prodotto. Tutte le chiamate provider, anche fallite, richiedono però accounting costi/token/modello/tenant/latenza/motivo. Riserva atomica prima della generazione, completamento idempotente dopo persistenza, rilascio dopo fallimento verificato. Riutilizzare claim/ledger esistenti senza un secondo contatore discordante. Questo contratto non è dichiarato già implementato. In main, message_repo.claim_message_and_check_quota incrementa messages_used_this_period e marca billed_at durante il claim, prima della generazione: A04 deve distinguere riserva e completamento, senza addebitare come risposta generata un semplice claim.

## Confine AI e prenotazioni

LLM propone testo e dati; il servizio applicativo autorizza. Messaggio cliente, cronologia, PDF, pagina web e RAG restano dati non attendibili: non cambiano tenant, consenso, budget o diritti.

Auto: confermare solo con intento e dettagli verificati, policy attività e abilitazione owner valide, tenant/contatto corretti, capacità e Calendar verificati, lock/idempotenza acquisiti e side effect persistiti/riconciliati. Il modello non autorizza mutazioni. Manuale: richiesta in attesa, conferma staff separata. Su incertezza o conflitto, handoff senza confermare. Main crea oggi richieste in_attesa: non è certificazione dell'auto-conferma richiesta in V1.

Calendar e PostgreSQL non condividono transazioni: definire rilettura/riconciliazione e regole sulle modifiche esterne. Non promettere assenza assoluta di race con altri writer Google.

## Proposta Calendar/PDF per piano — non applicata

Propongo Calendar e PDF/RAG base su tutti i piani, differenziando il volume e lasciando invariati i prezzi. Ipotesi beta: un calendario operativo per attività; 10/30/100 documenti e 50/150/500 pagine indicizzate. Sono valori proposti da approvare e misurare, non limiti già commercialmente confermati. Aggiungere tetti effettivi a byte totali/chunk/storage, oltre al numero file. Il limite file attuale è 20MB.

Nessuna promessa OCR: PDF senza testo deve dare errore esplicito, senza risultare indicizzato. Embedding locale una volta per indicizzazione e contesto recuperato limitato aiutano i costi; pgvector e modelli replicati fra processi richiedono benchmark RAM/storage. Main ha RAG solo Scala; il diff locale abilita Calendar per tutti ma non cambia RAG. Non modificare entitlement fino alla review della matrice.

## Riutilizzare i componenti presenti

- Wizard: web/dashboard-src/partials/pane-onboarding.html e src/core/onboarding.py. Sette passi: Attività, Voce/servizi, Lingue, Escalation, WhatsApp, Documenti, Test. Nome, orari, servizi, tono, lingue, escalation e preview esistono.
- Dashboard modulare: Inbox, booking, knowledge, AI, team, consumi e abbonamenti. Nessun nuovo framework richiesto.
- PDF: src/api/routes/knowledge.py e documenti/*. Estrazione, chunking, MiniLM multilingua locale, pgvector e retrieval tenant-scoped esistono. Completare stato atomico indicizzazione, limiti/autorizzazioni e gestione fonti.
- Calendar: OAuth/freebusy/sync/scheduler esistenti. Main/PR82 lo vietano in production; correzioni locali di startup e freebusy sono candidate da recuperare e certificare.
- Billing: piani, checkout, portale Stripe e webhook idempotente esistono. PR82 aggiunge lancio mensile e rifiuto prezzi sconosciuti; non è main.

## Costi e dipendenze esterne

Obiettivo hosting circa EUR10/mese esclusi dominio, AI e variabili; nessun servizio pagato attivato automaticamente. Non è un preventivo garantito. Oracle Always Free indica oggi 2 OCPU/12GiB per tenancy Always Free e possibile reclamazione delle istanze inattive: account/capienza/recovery da verificare. Supabase Free limita il DB a 500MB: i vettori richiedono budget storage e monitoraggio. Backup off-site e restore obbligatori.

Production impone LLM_COST_POLICY=free_only e conferme account/ZDR: impedisce semplicemente di scegliere un provider pagato per stabilizzare capacità. Eventuale policy paid_bounded richiede analisi costo per risposta e approvazione, senza fallback pagati impliciti. Account/quote/condizioni provider non verificati qui; piani free non provano capacità/SLA per SaaS. Calcolare token, contesto RAG, tentativi e margine con traffico rappresentativo.

Fonti ufficiali consultate il 16 ottobre 2026:
- [Oracle Always Free](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
- [Supabase billing](https://supabase.com/docs/guides/platform/billing-on-supabase)
- [Supabase backup](https://supabase.com/docs/guides/platform/backups)
- [Groq rate limits](https://console.groq.com/docs/rate-limits)

## Gate di certificazione

Nessun deploy production, operazione distruttiva DB, cambio credenziali live o merge sensibile senza autorizzazione esplicita. CI sul commit esatto, E2E Meta/Google/Stripe, isolamento due tenant, backup/restore, monitoraggio e review legale restano obbligatori. Credenziali salvate o vecchie suite verdi non attestano operatività. Metadati Supabase verificati in sola lettura: Melpis Production ACTIVE_HEALTHY, Melpis Staging riattivato ACTIVE_HEALTHY, entrambi eu-central-1. Il [piano C01](MELPIS-V1-STAGING-PLAN.md) registra il batch 058/059/UNIQUE approvato e applicato su Staging dopo backup cifrato reale e restore isolato verificato; controlli schema e dati passati, E2E applicazione/provider ancora aperti. Il backup resta conservato fino a esplicita conferma di eliminazione. Nella precedente verifica storica su Production mancavano auth_access_lifecycle e current_auth_access_allowed(); outbound_dedup ha RLS attiva, vector è0.8.2. Lo storico arriva alla057, ma outbound RLS mostra perché la sola history non prova lo schema. Main richiede059: Staging ora la include; Production non è stato interrogato né modificato in questo ciclo e richiede un gate separato prima di una release autorizzata. Un mese è obiettivo condizionato ai gate, non data garantita.
