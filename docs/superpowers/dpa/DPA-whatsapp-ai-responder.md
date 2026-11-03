> **BOZZA — NON PUBBLICARE SENZA REVIEW LEGALE.** Questo documento descrive
> l'implementazione osservata nel codice; provider, regioni, contratti,
> misure operative e retention effettiva vanno riconfermati al momento del lancio. Non sostituisce la
> consulenza di un avvocato o DPO. Prima di farlo accettare a clienti reali,
> va fatto rivedere da un professionista qualificato in materia di protezione
> dati, in particolare sulle clausole di trasferimento extra-UE, sulla base
> giuridica del legittimo interesse (§4.2) e sugli obblighi AI Act (§10).

# Accordo sul Trattamento dei Dati (DPA)
## Allegato ai Termini di Servizio — Melpis

**Ultimo aggiornamento:** {{LEGAL_DOCUMENT_EFFECTIVE_DATE}} — versione bozza 0.1
**Titolare del trattamento (ai fini del presente Accordo):** il Cliente B2B
che sottoscrive i Termini di Servizio ("Cliente" o "Titolare")
**Responsabile del trattamento:** {{LEGAL_ENTITY_NAME}} ("noi",
"Fornitore" o "Responsabile"), con sede in {{LEGAL_ENTITY_REGISTERED_OFFICE}},
P.IVA {{LEGAL_ENTITY_VAT_NUMBER}}

---

## 1. Definizioni

Ai fini del presente Accordo si applicano le definizioni di cui al
Regolamento (UE) 2016/679 ("GDPR"), tra cui in particolare:

- **Dati Personali**: qualsiasi informazione riguardante una persona fisica
  identificata o identificabile, trattata nell'ambito del Servizio.
- **Trattamento**: qualsiasi operazione compiuta sui Dati Personali.
- **Titolare**: il Cliente, che determina finalità e mezzi del trattamento
  dei dati dei propri contatti/clienti finali.
- **Responsabile**: il Fornitore, che tratta i Dati Personali per conto del
  Titolare secondo le istruzioni documentate di quest'ultimo.
- **Sub-responsabile**: soggetto terzo autorizzato dal Fornitore a trattare
  Dati Personali per l'esecuzione del Servizio (Allegato B).
- **Servizio**: la piattaforma SaaS "Melpis", che assiste nella gestione di
  conversazioni e attività collegate ai canali effettivamente attivati dal
  Titolare. Le funzioni AI e le integrazioni opzionali sono soggette a
  configurazione, disponibilità e consenso ove richiesto.

## 2. Oggetto e ruoli

2.1 Il presente Accordo disciplina il trattamento di Dati Personali
effettuato dal Responsabile per conto del Titolare nell'ambito dell'uso del
Servizio, ai sensi dell'art. 28 GDPR.

2.2 **Ruoli.** Con riferimento ai dati dei contatti/clienti finali del
Titolare trattati per erogare le funzioni attivate (ad esempio numeri
WhatsApp, messaggi, prenotazioni, recensioni e documenti forniti dal
Titolare), il Titolare determina finalità e istruzioni; il Fornitore opera
quale Responsabile nei limiti delle attività effettivamente svolte per suo
conto.

Il Fornitore opera quale Titolare autonomo limitatamente alle proprie
finalità di gestione degli account e dei rapporti con i clienti, fatturazione,
sicurezza del servizio e adempimento dei propri obblighi aziendali. La
presente ripartizione non estende il ruolo di Responsabile ai trattamenti
che il Fornitore determina per finalità proprie, né esclude obblighi
inderogabili previsti dalla legge. I trattamenti e i dati effettivamente
coinvolti per ciascuna finalità devono risultare dall'Informativa Privacy e
dall'inventario aggiornato dei trattamenti.

2.3 Il Responsabile tratta i Dati Personali esclusivamente per l'erogazione
del Servizio e secondo le istruzioni documentate del Titolare, che si
considerano fornite tramite: (a) la configurazione del Servizio da parte
del Titolare (es. profilo attività, regole di escalation); (b) il presente
Accordo; (c) i Termini di Servizio. Qualora il Responsabile ritenga che
un'istruzione violi il GDPR o altra normativa applicabile, ne informa
prontamente il Titolare.

## 3. Categorie di interessati e di Dati Personali (Allegato A)

Il dettaglio delle categorie di dati trattati, delle finalità e della base
giuridica è riportato nell'**Allegato A**. In sintesi, il Servizio tratta:
identità e contatti (numero WhatsApp, nome), contenuto delle comunicazioni,
dati di prenotazione (incluse eventuali note libere che l'interessato può
inserire), recensioni pubbliche, documenti caricati dal Titolare per
l'assistenza automatica, e dati tecnici/di consenso.

## 4. Basi giuridiche e nota sul consenso WhatsApp

4.1 Spetta al Titolare individuare e documentare la base giuridica adeguata
per il trattamento dei dati dei propri contatti finali (tipicamente
esecuzione di un contratto/misure precontrattuali, o consenso, a seconda
del contesto d'uso).

4.2 **Nota specifica sul canale WhatsApp.** Il Servizio registra lo stato di
consenso di ciascun contatto (`consent_status`: sconosciuto/opt-in/opt-out),
aggiornato tramite parole chiave, risposta a bottone o intervento manuale
dello staff del Titolare. In assenza di un opt-in esplicito, il Servizio può
comunque rispondere a messaggi in entrata iniziati dal contatto stesso,
sulla base del legittimo interesse a fornire un riscontro a una richiesta
volontariamente avviata dall'interessato. **Questa impostazione va validata
da un legale**, in particolare per l'invio di messaggi proattivi (promemoria
prenotazione, richieste di recensione), per cui la base giuridica adeguata
potrebbe richiedere consenso esplicito ai sensi della normativa ePrivacy
applicabile. Il Titolare è responsabile di raccogliere e documentare i
consensi necessari presso i propri contatti secondo la normativa a esso
applicabile.

## 5. Obblighi del Responsabile

Il Responsabile si impegna a:

a) trattare i Dati Personali unicamente su istruzione documentata del
   Titolare, salvo obbligo contrario previsto dal diritto UE o nazionale;
b) garantire che il personale autorizzato al trattamento sia vincolato da
   obbligo di riservatezza;
c) adottare le misure tecniche e organizzative di cui all'art. 32 GDPR,
   descritte nell'**Allegato C**;
d) rispettare le condizioni di cui all'art. 28(2) e (4) GDPR per il ricorso
   a sub-responsabili (§6);
e) assistere il Titolare, tenuto conto della natura del trattamento e delle
   informazioni a disposizione del Responsabile, nel garantire il riscontro
   alle richieste di esercizio dei diritti degli interessati (§8);
f) assistere il Titolare nel garantire il rispetto degli obblighi di cui
   agli artt. 32-36 GDPR (sicurezza, notifica violazioni, valutazione
   d'impatto, consultazione preventiva), tenuto conto della natura del
   trattamento e delle informazioni a disposizione del Responsabile;
g) su scelta del Titolare, cancellare o restituire tutti i Dati Personali
   al termine della fornitura del Servizio, salvo obbligo di conservazione
   previsto dal diritto UE o nazionale (§9);
h) mettere a disposizione del Titolare le informazioni necessarie a
   dimostrare il rispetto degli obblighi di cui all'art. 28 GDPR e
   consentire/contribuire ad attività di audit (§11);
i) informare tempestivamente il Titolare qualora, a proprio giudizio,
   un'istruzione ricevuta violi il GDPR o altra disposizione applicabile.

## 6. Sub-responsabili e trasferimenti extra-UE

6.1 L'**Allegato B** descrive le categorie e i fornitori candidati o
effettivamente utilizzati per le funzioni attivate. La sua inclusione in
questa bozza non costituisce attestazione che un fornitore sia già abilitato,
contrattualizzato o verificato, né autorizzazione a trasferimenti non
documentati. Prima di attivare un fornitore quale Sub-responsabile devono
essere verificati il trattamento effettivo, il ruolo, l'accordo applicabile,
le misure, le condizioni d'uso, la conservazione, gli eventuali upstream e
la localizzazione dei dati. Solo dopo tale verifica si applicano la previa
autorizzazione generale e la procedura di informazione e opposizione qui
previste; i dettagli operativi della procedura vanno completati prima della
sottoscrizione.

6.2 Per ciascun Sub-responsabile effettivamente autorizzato, il Responsabile
si impegna a imporre mediante accordo scritto obblighi di protezione dei
dati coerenti con l'art. 28(4) GDPR e non meno stringenti di quelli
applicabili al trattamento affidato.

6.3 **Trasferimenti internazionali.** La localizzazione dei trattamenti e
gli eventuali trasferimenti al di fuori dello Spazio Economico Europeo,
incluse le catene di subfornitura, non sono ancora verificati per tutti i
servizi e le configurazioni. Prima di ogni attivazione devono essere
identificati i paesi interessati e verificati gli strumenti e le garanzie
applicabili, ove necessari. Questa bozza non afferma che un trasferimento o
un relativo meccanismo sia già autorizzato, disponibile o perfezionato.

## 7. Violazioni di Dati Personali (Data Breach)

7.1 Il Responsabile notifica al Titolare senza ingiustificato ritardo,
secondo un termine operativo da definire e validare prima della firma,
qualsiasi violazione di Dati Personali che lo riguardi, fornendo le
informazioni disponibili necessarie al Titolare per adempiere ai propri
obblighi di notifica all'Autorità di controllo (art. 33 GDPR) ed eventuale
comunicazione agli interessati (art. 34 GDPR).

7.2 La notifica include, per quanto disponibile: natura della violazione,
categorie e numero approssimativo di interessati e di record coinvolti,
conseguenze probabili, misure adottate o proposte per porvi rimedio.

## 8. Assistenza per l'esercizio dei diritti degli interessati

8.1 Il backend include endpoint riservati al titolare (ruolo owner, con
autenticazione a due fattori) per le seguenti operazioni. L'interfaccia
dashboard self-service e la completezza della propagazione verso tutti i
fornitori non sono ancora verificate; fino ad allora è necessario un processo
assistito e documentato:

- **Esportazione dati** di un contatto (`GET /api/gdpr/export`);
- **Richiesta di cancellazione** dell'organizzazione
  (`POST /api/gdpr/delete`); perimetro, effetti sulle relazioni e completezza
  dell'esecuzione devono essere verificati prima di dichiarare l'erasure
  conclusa. Fornitori esterni e backup richiedono verifica e intervento
  operativo;
- **Gestione delle preferenze di consenso** dei contatti
  (`GET/PUT /api/gdpr/consent-prefs`).

8.2 Per richieste che non possano essere soddisfatte tramite le
funzionalità self-service, il Responsabile fornisce assistenza ragionevole
al Titolare su richiesta scritta, entro tempi concordati in base alla
complessità della richiesta.

## 9. Conservazione e cancellazione dei dati

9.1 Le durate seguenti sono la policy standard di prodotto V1. Si applicano
alle categorie operative quando compatibili con le istruzioni documentate
del Titolare e con gli obblighi di legge applicabili. Non attestano che i
job di cancellazione, la propagazione ai fornitori o la rotazione dei backup
siano già stati eseguiti o verificati. Eventuali policy specifiche per
Titolare devono essere versionate e non possono estendere arbitrariamente
queste durate.

| Categoria | Durata standard | Ambito e cancellazione |
|---|---|---|
| Contenuto dei messaggi conversazionali | Disponibilità ordinaria massima di 60 giorni; rimozione dal database primario entro 90 giorni | La finestra fino a 90 giorni è un termine massimo di rimozione dal primario, non una promessa di cancellazione da copie di terzi. Non prolungare indiscriminatamente o indefinitamente il contenuto personale usando un'etichetta di audit. Eventuali documenti soggetti a uno specifico obbligo restano separati, limitati e motivati. |
| Stato conversazionale strutturato C2.4 | Massimo 30 giorni dall'ultima attività pertinente | Eliminazione anticipata quando termina la finalità o quando è dovuta l'erasure. Non deve essere ricostruito automaticamente da copie obsolete. |
| Ricevute tecniche e dati di idempotenza | 90 giorni dalla conclusione della richiesta | Conservare solo identificativi e prove strettamente necessari; distinguere eventuali dati personali da contabilità soggetta a un obbligo separato. Nessuna conservazione indefinita di contenuti personali come audit. |
| Prenotazioni e soggiorni conclusi | Massimo 12 mesi dalla conclusione | L'archivio operativo non contiene testo conversazionale grezzo. Documenti soggetti a obblighi legali restano in categorie separate, limitate e motivate. Nessun periodo operativo autorizza conservazione indiscriminata di dati sanitari. |
| Dati finanziari e documentazione obbligatoria | Durata prevista per la specifica categoria e il relativo obbligo documentato | Separazione tecnica dai dati conversazionali. Nessuna estensione generale dei termini; contatori e audit non autorizzano la conservazione indiscriminata di testo grezzo. Eventuali documenti obbligatori richiedono categoria, necessità e periodo specifici. |

9.2 **Richieste di cancellazione e cessazione.** Le richieste sono gestite
secondo le funzionalità e le relazioni dati effettivamente verificate. La
presenza di un endpoint non attesta cancellazione completa o propagazione
automatica. Prima di dichiarare conclusa un'erasure occorre verificare le
tabelle e le copie pertinenti, le ricevute C2.4, le barriere contro replay o
ricreazione, e gli eventuali passaggi esterni ancora pendenti. Alla
cessazione del Servizio il Responsabile restituisce o cancella i dati
secondo la scelta del Titolare e le condizioni dell'art. 28(3)(g) GDPR,
salvi gli obblighi di conservazione effettivamente applicabili e documentati.

9.3 **Obblighi separati e trattenimenti eccezionali.** Dati finanziari o
documenti obbligatori possono essere conservati solo nella categoria
pertinente, per il periodo richiesto e con accessi limitati. Non è prevista
alcuna eccezione generica o permanente. Un eventuale legal hold richiede
motivo e autorità documentati, ambito minimo, accessi limitati e una data di
scadenza o revisione. Non autorizza una conservazione generica o indefinita
di contenuti personali come audit o contabilità. Eventuali documenti
legalmente necessari restano in categorie separate, limitate e motivate.

9.4 **Backup.** La policy standard prevede backup cifrati, accesso
amministrativo ristretto e rotazione con conservazione massima di 30 giorni,
salvo obbligo specifico documentato. Il ripristino deve avvenire in ambiente
isolato, con riconciliazione delle cancellazioni intervenute dopo il backup
e verifica di barriere indipendenti contro replay e ricreazione dei dati
cancellati prima di riaprire il traffico. Le evidenze necessarie a preservare
tali barriere devono essere custodite indipendentemente dal backup. Questa
descrizione definisce la policy e non attesta configurazione, restore o
verifiche già eseguiti. I job distruttivi richiedono una policy registrata,
verifica dell'ambiente e GO operativo.

## 10. Nota su intelligenza artificiale e trasparenza (AI Act)

10.1 Il Servizio genera risposte automatiche tramite un sistema di
intelligenza artificiale e, in conformità all'art. 50(1) del Regolamento
(UE) 2024/1689 ("AI Act"), informa gli utenti finali che stanno interagendo
con un sistema di IA al momento del primo contatto automatico: la prima
risposta generata dal sistema verso ciascun contatto inizia con la dicitura
"Ciao! Sono l'assistente automatico di [attività], un sistema di
intelligenza artificiale. Scrivi OPERATORE se vuoi parlare con una persona."
e offre la possibilità di richiedere di parlare con una persona
("OPERATORE"), che determina l'instradamento della conversazione a un
operatore umano del Titolare (HITL).

10.2 Resta a carico del Titolare ogni obbligo ulteriore previsto dall'AI Act
che non sia specificamente adempiuto dal Servizio (es. nei confronti di
utenti istituzionali o in relazione ad altri canali), da verificare con
l'assistenza di un legale.

## 11. Audit

Il Responsabile mette a disposizione del Titolare, su richiesta scritta e
con ragionevole preavviso, le informazioni necessarie a dimostrare il
rispetto degli obblighi di cui al presente Accordo, e consente lo
svolgimento di attività di verifica/audit, anche tramite un terzo
incaricato dal Titolare, con modalità e tempistiche concordate in modo da
non compromettere la sicurezza del Servizio o i dati di altri Titolari.

## 12. Responsabilità

Ciascuna parte risponde dei danni causati da un trattamento che violi il
GDPR nella misura prevista dall'art. 82 GDPR e dalla normativa applicabile.
Restano ferme le eventuali limitazioni di responsabilità previste nei
Termini di Servizio, nei limiti in cui compatibili con norme inderogabili
in materia di protezione dei dati.

## 13. Durata

Il presente Accordo ha efficacia per la durata del contratto di Servizio
tra le parti ed è automaticamente risolto alla cessazione dello stesso,
fermo quanto previsto al §9.

## 14. Legge applicabile e foro competente

Il presente Accordo è disciplinato dalla legge italiana. Per qualsiasi
controversia è competente in via esclusiva il Foro di {{LEGAL_FORUM}}, salve le
disposizioni inderogabili in materia di protezione dei consumatori ove
applicabili.

## 15. Accettazione

Il presente Accordo è allegato ai Termini di Servizio del Fornitore e si
intende accettato dal Titolare al momento della sottoscrizione dei
medesimi, salvo diverso accordo scritto tra le parti (es. per clienti che
richiedano una sottoscrizione separata).

---

## Allegato A — Categorie di interessati, dati e finalità

**Interessati:** contatti/clienti finali del Titolare che comunicano via
WhatsApp; utenti dell'organizzazione del Titolare (staff).

| Categoria | Esempi | Finalità |
|---|---|---|
| Identità e contatto | numero WhatsApp, nome contatto, nome/email staff | Erogazione del Servizio per conto del Titolare; gestione dell'account per le finalità proprie del Fornitore |
| Contenuto comunicazioni | testo messaggi, cronologia conversazione | Risposta alla richiesta e gestione della conversazione; disponibilità ordinaria massima 60 giorni e rimozione dal database primario entro 90 giorni |
| Stato conversazionale C2.4 | dati strutturati strettamente necessari allo stato del flusso | Continuità operativa; massimo 30 giorni dall'ultima attività pertinente |
| Prenotazioni e soggiorni | dati necessari alla prenotazione; evitare note libere non necessarie | Gestione della prenotazione; massimo 12 mesi dalla conclusione per l'archivio operativo |
| Ricevute tecniche/idempotenza | identificativi e prove minime della richiesta | Affidabilità e prevenzione di duplicati; 90 giorni dalla conclusione della richiesta |
| Recensioni | autore, testo recensione, bozza di risposta AI | Gestione della reputazione, se la funzione è attivata; conservazione limitata alla finalità e alle policy applicabili |
| Documenti caricati dal Titolare | contenuti forniti e rappresentazioni derivate, se utilizzate | Risposte basate sulle informazioni dell'attività; necessità, durata e cancellazione da definire nelle istruzioni e nel registro della funzione attivata |
| Consensi e preferenze | stato e prove minime dell'evento | Applicazione delle preferenze e prova degli adempimenti; termini specifici da documentare senza trattenere contenuti conversazionali grezzi |
| Trasparenza AI | eventuale timestamp della disclosure | Gestione della funzione e prova minima ove necessaria; durata e accessi da documentare |
| Credenziali di integrazione | token OAuth o credenziali equivalenti, se attivati | Funzionamento dell'integrazione; protezione e cancellazione al termine della finalità |
| Dati tecnici, account e finanziari | timestamp, stati, contatori, dati di fatturazione pertinenti | Sicurezza, gestione account e obblighi propri del Fornitore; durate specifiche per categoria e obbligo documentato, separati dal testo dei messaggi |

**Dati relativi alla salute e altre categorie particolari.** L'uso sanitario
è limitato alle funzioni amministrative. Il Servizio non è destinato a
diagnosi, anamnesi, prescrizioni, gestione di referti o decisioni mediche
automatiche. L'attivazione reale del settore medico o dentistico resta
bloccata finché non siano stati verificati i requisiti specifici e il
perimetro autorizzato. Messaggi clinici o urgenti devono essere indirizzati
al percorso di escalation appropriato. Il Titolare deve istruire il
Responsabile sul trattamento effettivamente necessario e sulla relativa
condizione di liceità; la presente bozza non autorizza una raccolta
indiscriminata di dati sanitari.

## Allegato B — Sub-responsabili

| Fornitore | Funzione | Dati trattati | Sede / trasferimento extra-UE |
|---|---|---|---|
| Meta Platforms (WhatsApp Business API) | Candidato per invio/ricezione WhatsApp, se abilitato | Da confermare in base ai flussi effettivi; potenzialmente numero, contenuto e identificativi messaggio | Entità, paesi, condizioni, retention e trasferimenti da verificare |
| Provider AI e upstream effettivamente selezionati | Candidati per generazione AI, se abilitati | Da confermare; potenzialmente prompt, testo messaggio, profilo attività ed estratti documentali | Provider, upstream, endpoint, retention, condizioni e trasferimenti da verificare |
| Google (Business Profile, Calendar) | Integrazioni opzionali, se abilitate | Da confermare in base alle funzioni e ai dati sincronizzati effettivi | Entità, paesi, condizioni e trasferimenti da verificare |
| Stripe | Pagamenti o abbonamenti, se abilitati | Flussi effettivi, categorie e dati della transazione da confermare; non presumere che un dato non transiti senza verifica | Entità, paesi, condizioni e trasferimenti da verificare |
| Supabase e infrastruttura | Hosting/database/autenticazione, se effettivamente utilizzati | Da confermare per database, backup, log e servizi collegati | Progetto, regione, contratti, backup, subfornitori e trasferimenti da verificare |
| Sentry | Monitoraggio errori, solo se configurato | Da confermare; verificare contenuto di tracce e regole di redazione | Attivazione, configurazione, paesi e trasferimenti da verificare |
| Provider email, monitoraggio, backup e altri servizi tecnici | Solo se effettivamente configurati | Da censire secondo il flusso reale e il principio di minimizzazione | Identità, contratto, localizzazione, retention e trasferimenti da verificare |
| Airtable, Softr e altre integrazioni opzionali | Solo se effettivamente attivate | Da censire per i dati realmente sincronizzati | Verifica completa prima dell'attivazione |

L'Allegato B è un inventario da completare, non una dichiarazione che i
fornitori elencati siano tutti attivi o già autorizzati. Nessuna regione,
ubicazione o condizione contrattuale è considerata verificata sulla sola
base di questa bozza. Un fornitore non verificato resta disabilitato o deve
produrre un esito UNKNOWN/fail-closed quando necessario.

## Allegato C — Misure tecniche e organizzative (art. 32 GDPR)

**Misure attive:**

- Cifratura a riposo (AES-128/Fernet) delle credenziali e dei token di
  integrazione (WhatsApp, Google OAuth).
- Controllo accessi basato su ruoli (owner/manager/staff).
- Autenticazione a più fattori obbligatoria per le operazioni più sensibili
  (esportazione/cancellazione dati, fatturazione, collegamento account
  Google).
- Isolamento dei dati tra organizzazioni a livello applicativo (ogni
  interrogazione ai dati è vincolata all'organizzazione dell'utente
  autenticato).
- Verifica crittografica della firma delle richieste in arrivo da
  WhatsApp/Meta e da Stripe.
- Limitazione della frequenza delle richieste (rate limiting) e controllo
  delle origini consentite (CORS).
- Calcolo delle rappresentazioni vettoriali dei documenti caricati
  interamente in locale, senza invio a servizi terzi.
- Redazione automatica dei dati personali riconoscibili (numeri di
  telefono in formato internazionale, indirizzi email) nei log applicativi,
  attiva su tutti i processi del Servizio (API e worker).

**Limitazioni note, dichiarate in trasparenza:**

- La redazione dei log si basa su riconoscimento di pattern (regex) ed è
  pertanto un mitigamento ragionevole, non una garanzia assoluta: non
  copre, ad esempio, identificativi numerici privi di prefisso
  internazionale, né dati personali presenti in campi di testo libero che
  non seguono un formato riconoscibile.
- Il servizio di monitoraggio errori (Sentry), se attivato, può ricevere
  incidentalmente frammenti di testo dei messaggi tramite i messaggi di
  eccezione.
- La sicurezza a livello di database (RLS) è affiancata, ma non sostituita,
  dal controllo applicativo per organizzazione; l'accesso con credenziali
  di servizio del database bypassa la RLS, per cui l'isolamento effettivo
  tra clienti si basa sul corretto funzionamento del livello applicativo.

Il Responsabile si impegna ad aggiornare il presente Allegato in caso di
modifiche sostanziali alle misure di sicurezza adottate.
