# MELPIS V1 — APPROVAZIONE FONDATORE: POLICY PRIVACY E DATA GOVERNANCE V1.0

**DECISIONE FONDATORE DEFINITIVA PER LA V1 — APPROVATA**

Repository: `Scontismaart/Melpis`

Autorizzo formalmente l'adozione della **Melpis V1 Privacy & Data Governance Policy 1.0** quale riferimento vincolante per lo sviluppo del prodotto, C2.4, C05 e A06.

Le cinque decisioni di prodotto sottostanti sono **CHIUSE E APPROVATE**. Non devono essere rimesse in discussione durante l'implementazione ordinaria.

L'obiettivo è minimizzare dati, utilizzi, accessi, esposizione giuridica evitabile e rischi operativi, garantendo coerenza fra funzionamento del software, contratti e informazioni presentate ai clienti.

## DECISIONE 1 — RUOLI E RESPONSABILITÀ

Per i dati dei clienti finali trattati nell'erogazione del servizio:

- L'attività cliente determina finalità e istruzioni del trattamento.
- Melpis opera come responsabile del trattamento, nei limiti delle attività realmente svolte per conto del cliente.
- Melpis agisce invece come titolare autonomo per le proprie finalità di gestione account, fatturazione, sicurezza e obblighi aziendali.
- Gli accessi dello staff Melpis devono essere limitati, motivati, autorizzati e tracciati.
- Nessuna attività può accedere ai dati di un'altra attività.
- I contratti e il runtime devono rappresentare i ruoli effettivi, senza clausole che dichiarino immunità da obblighi inderogabili.

Adotta questa ripartizione come modello contrattuale e architetturale della V1. Segnala soltanto eventuali trattamenti effettivi incompatibili con essa.

## DECISIONE 2 — FINALITÀ E LIMITI DELL'AI

I dati possono essere utilizzati esclusivamente per:

- Assistenza conversazionale richiesta.
- FAQ e informazioni dell'attività.
- Raccolta delle informazioni necessarie alla prenotazione.
- Gestione, modifica e cancellazione delle prenotazioni.
- Prevenzione degli errori e dei doppi addebiti.
- Sicurezza, assistenza tecnica e obblighi documentati.

Sono vietati:

- Addestramento di modelli sui dati dei clienti da parte di Melpis.
- Marketing autonomo.
- Profilazione commerciale non autorizzata.
- Vendita o cessione dei dati per finalità indipendenti.
- Raccolta di dati non necessari.
- Utilizzo clinico dell'AI.
- Decisioni mediche automatiche.

Il settore medico/dentistico rimane limitato alle funzioni amministrative.

Nessuna diagnosi, anamnesi, prescrizione o gestione di referti.

Messaggi clinici o urgenti devono attivare il percorso di escalation appropriato.

**L'attivazione reale del settore sanitario resta bloccata finché non siano stati verificati i requisiti specifici di trattamento dei dati sanitari e il perimetro autorizzato.**

## DECISIONE 3 — CONSERVAZIONE DEI DATI

Approvo le seguenti durate come policy standard di prodotto V1, da applicare alle categorie operative quando compatibili con le istruzioni contrattuali del titolare e con gli obblighi normativi applicabili.

**Messaggi conversazionali**
- Disponibilità ordinaria: massimo 60 giorni.
- Eliminazione del contenuto dal database primario: entro 90 giorni.
- Cancellazione anticipata quando richiesta e dovuta.

**Stato conversazionale strutturato C2.4**
- Conservazione massima ordinaria: 30 giorni dall'ultima attività pertinente.
- Eliminazione anticipata quando la finalità termina o interviene un'erasure applicabile.
- Nessuna ricostruzione automatica da copie obsolete.

**Ricevute tecniche e idempotenza**
- Conservazione ordinaria: 90 giorni dalla conclusione della richiesta.
- Distinzione fra ricevute contenenti dati personali e contabilità necessaria a obblighi separati.
- Conservare soltanto identificativi e prove strettamente necessari.
- Nessuna conservazione indefinita di contenuti personali sotto l'etichetta di audit.

**Prenotazioni e soggiorni conclusi**
- Archivio operativo standard: massimo 12 mesi dalla conclusione.
- Nessun testo conversazionale grezzo nell'archivio.
- Eventuali documenti soggetti a obblighi legali di conservazione rimangono in categorie separate, limitate e motivate.
- Nessun periodo operativo autorizza la conservazione indiscriminata di dati sanitari.

**Contabilità e documentazione obbligatoria**
- Periodi applicabili alla specifica categoria e ai relativi obblighi.
- Separazione tecnica dai dati conversazionali.
- Nessuna cancellazione di contatori necessaria a nascondere consumi o ricreare quote.

**Legal hold**
- Nessuna eccezione generica o permanente.
- Eventuali trattenimenti devono richiedere motivo documentato, ambito minimo, autorità competente, scadenza o revisione.

Non trasformare questi termini operativi in promesse assolute riferite a copie di terzi o a dati soggetti a obblighi diversi.

Le policy tenant-specifiche devono essere versionate e non possono estendere arbitrariamente le durate standard.

## DECISIONE 4 — EXPORT, CANCELLAZIONE E BACKUP

Approvo:

- Export per organizzazione e soggetto, secondo relazioni verificate.
- Minimizzazione dei dati esportati.
- Erasure e anonimizzazione quando applicabili.
- Barriere durevoli contro replay e ricreazione dei contenuti eliminati.
- Gestione esplicita delle copie A04 e delle ricevute C2.4.
- Nessuna cancellazione fittizia.
- Nessuna dichiarazione di erasure completata se esistono passaggi esterni ancora pendenti.

**Backup**
- Cifratura obbligatoria.
- Accesso amministrativo ristretto.
- Rotazione ordinaria con conservazione massima di 30 giorni, salvo obbligo specifico documentato.
- Restore in ambiente isolato.
- Riconciliazione con le cancellazioni successive al backup.
- Nessuna riapertura del traffico prima della verifica delle barriere GDPR.

Le evidenze di erasure necessarie al ripristino devono essere custodite indipendentemente dal backup da ripristinare.

Non attivare job distruttivi senza policy approvata nel registro, verifica dell'ambiente e relativo GO operativo.

## DECISIONE 5 — FORNITORI E TRATTAMENTI ESTERNI

Melpis può utilizzare soltanto fornitori necessari al funzionamento del servizio e verificati rispetto al trattamento realmente richiesto.

Rientrano nell'inventario, quando effettivamente abilitati:

- Provider AI e relativi upstream.
- Meta/WhatsApp.
- Supabase e infrastruttura.
- Google Calendar.
- Stripe.
- Email, monitoraggio, backup e servizi tecnici.

Requisiti:

- Accordi applicabili e ruoli contrattuali verificati.
- Ubicazione del trattamento e trasferimenti documentati.
- Misure di sicurezza appropriate.
- Retention e condizioni di utilizzo conosciute.
- Nessun addestramento sui dati dei clienti per finalità indipendenti non autorizzate.
- Nessun passaggio silenzioso a un provider diverso.
- Nessun costo o servizio aggiuntivo attivato automaticamente.

Un provider non verificato rimane disabilitato o produce un esito UNKNOWN/fail-closed quando necessario.

## IMPLEMENTAZIONE E DOCUMENTAZIONE

Sol deve:

1. Registrare questa policy come decisione interna approvata, versionata e rintracciabile.
2. Allineare C2.4, C05, A06, export, retention ed erasure.
3. Eliminare contraddizioni fra durate dichiarate e comportamento software.
4. Versionare le categorie nel registro delle policy.
5. Separare dati operativi, dati finanziari e prove di sicurezza.
6. Adeguare le bozze documentali alle decisioni approvate, senza pubblicare informazioni non verificate.
7. Preparare test su scadenze, cancellazioni, restore e replay.
8. Identificare eventuali requisiti normativi o contrattuali incompatibili e segnalarli come blocchi concreti di attivazione.

Non modificare autonomamente le cinque decisioni.

Non dichiarare eseguite attività che non lo sono: accordi sottoscritti, certificazioni, purge, cancellazioni presso provider o backup/restore effettivi.

Non modificare Production e non avviare retention distruttiva.

## ESITO ATTESO

Restituisci:

- Documento policy V1.0 registrato.
- Matrice categoria → finalità → durata → accessi → export → erasure.
- Elenco dei componenti da adeguare.
- Eventuali incompatibilità normative/contrattuali concrete.
- Gate tecnici necessari per applicare le decisioni.
- Stato C05/A06 dopo l'approvazione interna.

**DECISIONE FONDATORE: LE CINQUE SCELTE PRIVACY E DATA GOVERNANCE V1.0 SONO UFFICIALMENTE APPROVATE E CHIUSE A LIVELLO DI PRODOTTO.**

Usale come fonte di verità per chiudere il lavoro tecnico.

Non richiedere ulteriori decisioni di preferenza su questi cinque punti. Eventuali obblighi inderogabili o condizioni contrattuali non soddisfatte rimangono gate oggettivi, non scelte di prodotto da riaprire.