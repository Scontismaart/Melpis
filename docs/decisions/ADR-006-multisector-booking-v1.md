# ADR-006 — Prenotazioni configurabili nei cinque settori

Stato: **decisione architetturale C2.1; soli contratti implementati**, 28 ottobre 2026.
Nessuna estensione runtime, migrazione o attivazione operativa in questa PR.
Baseline dopo S3 #145: `4fb37a95d4e07f77cb48b052d52d0adce56efdc4`.

## Evidenze e componenti da conservare

| Componente | Comportamento effettivo sulla baseline | Riuso / gap |
| --- | --- | --- |
| `src/core/bookings/auto_policy.py`, `validate_auto_booking_configuration`, `parse_and_validate_auto_booking` | Opt-in esplicito v1, ristorante/tavolo, 60 minuti, ore intere, timezone/DST e capacità validate | Conservare v1; aggiungere un percorso v2 separato dopo approvazione |
| `src/core/inbound/service.py`, `_apply_committed_booking` | Solo dopo risposta canonica A04 persistita; verifica messaggi cliente, freschezza, nome, quantità e tenant; gli altri verticali richiedono staff | Conservare il confine post-commit, senza usare l'output LLM come permesso |
| `src/core/db/repositories/booking_repo.py`, `auto_booking_lock`, `insert_auto_confirmed`, `enqueue_calendar_confirmation` | Lock organizzazione/conversazione/capacità/ora; booking e notifica durevoli; deduplica della sorgente | Estendere il lock orario a intervalli e risorse, conservando ordine dei lock e outbox |
| `src/core/bookings/service.py`, `verifica_disponibilita`, `_get_capienze` | Quantità `coperti` e fasce HH:MM; alcuni percorsi manuali hanno fallback a 40 | Non usare fallback o semaforo legacy come prova di disponibilità v2 |
| `src/core/calendar/availability.py`, `CalendarAvailability`, `local_instant`; `calendar/lifecycle.py` | Stato verificato libero/occupato/sconosciuto/non configurato, timezone/DST, worker con fencing | Riutilizzare; verificare tutte le risorse e l'intero nuovo intervallo |
| `src/core/bookings/router.py`, `BookingAdapterRouter` | Router e minimizzazione per adapter esterni già presenti | Nessuna certificazione aggiuntiva; autorità esterna senza prova resta bloccata |
| `src/core/verticals/*`, `DatiPrenotazione` in `src/models/schemas.py` | Cinque strategie linguistiche; DTO comune data/ora/coperti/note; alcuni arricchimenti inferiscono quantità | Non usare default/inferenze delle strategie come evidenza cliente; mancano servizio, durata, operatore, risorsa e soggiorno |
| `web/dashboard-bookings.js`, `dashboard-ai-settings.js`, `src/core/bookings/routes.py` | Dialoghi, calendario, capacità, profilo e regole testuali riutilizzabili; settings owner/manager, nessun catalogo strutturato completo | Nuovo editor sul catalogo canonico; non fingere che orari liberi o regole prompt siano policy deterministiche |

Le route settings attuali non dichiarano un gate MFA generale. Il gate di
pubblicazione/attivazione descritto sotto è **da implementare e testare**, non una
proprietà già certificata.

## Decisione architetturale

Un solo motore deterministico, con due semantiche temporali e un catalogo tenant:

1. **Appuntamento**: servizio con durata e buffer espliciti in minuti, insieme
   alle risorse necessarie. Nessun valore ricavato dalla sola risposta AI.
2. **Soggiorno**: date di arrivo/partenza e orari locali di check-in/check-out;
   notti come date civili, non multipli di 24 ore. Le camere sono occupate per
   l'intero intervallo, incluse le date intermedie.
3. `booking_settings.config` resta il contenitore canonico del catalogo. Le
   stringhe `onboarding_profiles.servizi` restano contenuto informativo, non un
   secondo catalogo autorizzativo. Nessuna sincronizzazione automatica implicita.
4. Operatori e beni sono risorse dello stesso catalogo. Un operatore è una
   risorsa di tipo `operator`; la dashboard può mostrarlo in una sezione dedicata.
   Non creare due contatori di disponibilità per la stessa persona.
5. Il contratto implementato è un **draft di versione 2**, non il body accettato oggi
   dalle API. Il draft non espone alcun flag di autorizzazione automatica.
   Non contiene token, tenant scelto dal client, grant di conferma o controlli di
   sicurezza disattivabili.

| Settore beta | Modello | Informazioni amministrative / risorse |
| --- | --- | --- |
| `ristorante` | Appuntamento; conservare il caso v1 60 minuti | Cliente, contatto, data/ora, persone; pool coperti o tavoli espliciti |
| `parrucchiere` | Appuntamento | Servizio e durata configurati; operatore e postazione |
| `centro_estetico` | Appuntamento | Servizio e durata configurati; operatore, cabina/attrezzatura |
| `hotel_bnb` | Soggiorno | Arrivo/partenza, ospiti/camere, contatto; camere e disponibilità sull'intero soggiorno |
| `studio_medico_dentista` | Appuntamento amministrativo | Servizio amministrativo, contatto, slot e professionista; nessun campo clinico libero |

Le durate e quantità nelle fixture sono sintetiche, non default commerciali.
I servizi della beta possono usare configurazioni diverse. Nessun nuovo limite
commerciale, requisito di carta o requisito Google viene introdotto.

## Configurazione e raccolta conversazionale

- Catalogo unico: servizi, risorse, disponibilità settimanale e chiusure. Le
  disponibilità di organizzazione, servizio e risorse si intersecano; un override
  non amplia implicitamente gli orari degli altri vincoli. Chiavi valide su tutti
  i sette giorni; un giorno vuoto è chiuso, un dato mancante è sconosciuto.
- Campi richiesti selezionabili per servizio da un catalogo amministrativo tipato;
  ordine delle domande configurabile. Le etichette non sono istruzioni al modello.
  Il draft non ammette prompt o codice arbitrario come regole di autorizzazione.
- Stato raccolta durevole, tenant/conversazione/intento/revisione configurazione:
  valore, provenienza del messaggio cliente, conferma/correzione e stato mancanti.
  Un campo già verificato non viene richiesto di nuovo, salvo correzione del
  cliente, dato scaduto o requisito cambiato. Un riepilogo LLM non è una prova.
- Riutilizzare contatti verificati con minimizzazione; non trasformare default
  come `coperti=1/2` delle strategie verticali in un dato confermato dal cliente.
- Per il sanitario il catalogo campi è ristretto: niente sintomi, diagnosi,
  terapie, documenti clinici o note libere. Le regole esistenti di escalation
  clinica restano obbligatorie. Anche i metadati degli appuntamenti richiedono
  controllo accessi e minimizzazione; C05 deve approvare il perimetro effettivo.
- Le attuali regole testuali di escalation restano preferenze aggiuntive, non
  deroghe. Dati incerti, disponibilità ignota, perdita di autorizzazione, richiesta
  umana e condizioni di sicurezza non possono essere disattivate dal cliente.

## Autorizzazione e protocollo di commit

1. Il backend risolve utente, membership e organizzazione effettiva. Draft e
   catalogo non accettano `organization_id`, ruoli o prove dal client/LLM come grant.
2. Pubblicazione e attivazione: owner autorizzato e MFA server-side, revisione
   attesa e audit durevole. Le modifiche dei manager e i permessi di delega sono
   una decisione esplicita da verificare prima di estendere l'API; le route
   manuali attuali non vengono modificate dalla proposta.
3. L'AI raccoglie una proposta. A03 continua ad autorizzare e contabilizzare i
   tentativi provider; A04 continua a contare solo la risposta AI finale canonica.
   Un errore A04 non autorizza booking. Nessuna conferma fondata sul testo LLM.
4. Dopo il commit canonico A04, riusare il passaggio deterministico inbound:
   riconfermare intento più recente, dati cliente, catalogo/revisione e scope.
   La risposta AI resta neutra finché manca il risultato deterministico.
5. Riusare l'ordine corrente: barriera organizzazione, riga conversazione,
   configurazione/capacità, quindi lock risorse in ordine stabile. V2 deve usare
   lo stesso ordine in creazione, modifica, cancellazione e writer manuali.
   Dentro la transazione nessuna chiamata provider.
6. Lock tenant/risorsa indipendenti dall'ora di inizio; sommare le allocazioni
   sovrapposte sull'intero intervallo `[inizio,fine)`. Per risorse esclusive la
   capacità è uno; per pool verificare tutte le quantità, senza fallback.
7. Nella stessa transazione scrivere booking, allocazioni, snapshot servizio/
   configurazione e outbox. La chiave è tenant più intento cliente verificato;
   replay stesso payload restituisce lo stesso risultato, payload diverso è
   conflitto. Dopo un crash non ripetere POST generative o side effect esterni.
8. Riusare il worker di conferma/outbox. La notifica deterministica non aggiunge
   un consumo A04. Un guasto della consegna non rimborsa una risposta già
   persistita. Un booking fallito dopo il commit AI richiede staff, senza falso
   messaggio di conferma e senza cancellare il ledger commerciale.

Le allocazioni misurano risorse fisiche, non risposte AI: nessun contatore
commerciale parallelo. Modifiche e cancellazioni devono usare lo stesso protocollo
e revocare revisioni obsolete, anche quando arrivano dalla dashboard.

## Autorità e sincronizzazione

Conservare i nomi esistenti `internal`, `internal_calendar_sync`,
`google_authoritative`. Google resta facoltativo. Nel secondo modo la conferma
interna non dipende dal sync; errori/retry sono durevoli e visibili. Nel terzo,
stato sconosciuto, credenziali non valide o risposta incompleta impediscono la
conferma. Riusare `CalendarAvailability`, epoch di connessione e fencing revisioni.

`external_authoritative` resta fail-closed senza una prova di disponibilità e
completamento del provider esterno. Nessun cambio automatico di autorità. Il
mapping risorse/calendari e il payload create/update/delete devono usare durata
e intervallo v2, non il vecchio evento fisso di 60 minuti. Le chiamate reali e la
certificazione C03 restano separate; il protocollo non promette atomicità con
modifiche manuali concorrenti su un calendario esterno.

## Piano migrazioni e cutover — non eseguibile in questa PR

1. Revisione immutabile del catalogo nel setting canonico e storico snapshot;
   nuova raccolta strutturata con scope diretto organizzazione/conversazione.
2. Aggiungere ai booking intervallo UTC, timezone originale, servizio/revisione
   e riferimento allo snapshot; aggiungere allocazioni tenant-scoped indicizzate
   per risorsa/intervallo. Riutilizzare booking/outbox esistenti, non copiarli.
3. ACL/RLS defense-in-depth e scritture solo backend autorizzato. Nessun ruolo
   service-role diventa un sostituto delle verifiche applicative.
4. Mapping storico solo con evidenze: il v1 ristorante noto può conservare il
   suo intervallo di 60 minuti. Date, durate o risorse ignote richiedono revisione;
   niente default inventati e nessun opt-in dei tenant storici.
5. Prima del cutover: backup cifrato/restore verificato, certificazione separata
   delle migrazioni 060–066 in C01, drenaggio dei worker vecchi e prova di fencing.
   Scegliere il numero della nuova migrazione solo dopo revisione.
6. Attivazione per tenant dopo pubblicazione esplicita. Un cambio di catalogo non
   modifica i booking esistenti né resetta trial/quota A04. Bloccare nuova
   automazione in caso di configurazione storica incompatibile.
7. Rollback: fermare nuova ammissione v2, drenare/raccordare operazioni e outbox,
   mantenere booking e allocazioni già committati. Non riabilitare writer v1 che
   ignorino occupazioni v2; usare il percorso manuale sicuro finché compatibile.

## Step di implementazione successivi e prove richieste

| Step minimo | Riuso / modifica prevista | Criterio verificabile |
| --- | --- | --- |
| Catalogo e raccolta | Settings + nuovo contratto versionato; stato raccolta con prove cliente | Cinque configurazioni valide; riferimenti tenant errati rifiutati; campi mancanti senza domande duplicate; pubblicazione owner/MFA/CAS |
| Intervalli e risorse | BookingRepository e lock/outbox; ammissione v2 separata da v1 | PostgreSQL concorrente: intervalli parzialmente sovrapposti, ultima risorsa, più operatori, A→B tenant; nessun overbooking |
| Collegamento inbound | Confine post-A04, legacy e orchestrato | Guardrail/A04 falliti: zero booking; replay/crash/correzione/cancellazione: un solo side effect; escalation senza falsa conferma |
| UI editor e manuali | Dialoghi accessibili, invalidazione contesto, settings backend | Stessa disponibilità per azioni manuali/automatiche; impostazioni risorse/orari/servizi e stato pubblicazione visibili |
| Google/esterni | B01 e adapter esistenti | Opzionale irraggiungibile non cambia autorità; autorevole unknown blocca; create/update/delete e crash/retry senza duplicati |
| Cutover beta | C01/C03/C05 | Cinque attività reali autorizzate, una per settore; staging e privacy certificati prima del GO operativo |

I test di questa PR verificano solo schema/fixture e il mantenimento dei rifiuti
del runtime corrente. Non dimostrano concorrenza PostgreSQL v2, raccolta
conversazionale implementata, Google reale o conferme multisettore funzionanti.
Quei gate richiedono codice, regressioni e un GO successivo. #90 resta aperta.


## Perimetro implementato C2.1

`src/core/bookings/catalog_contracts.py` e un modulo nuovo, non importato da
route, worker, repository, strategie o `__init__.py` del runtime. Nessun DTO
esistente viene modificato. I contratti sono Pydantic strict/frozen con extra
vietati ricorsivamente, collezioni tuple e oggetti annidati immutabili.

- `CatalogDraft`: versione 2, revisione positiva, uno dei cinque codici canonici,
  timezone IANA, disponibilita, servizi e risorse con riferimenti locali univoci.
  Nessun `organization_id`, flag enabled, token, ruolo o permesso nel payload.
- `validate_catalog_json`: rifiuta chiavi JSON duplicate a ogni livello e lega il
  risultato a un UUID ricevuto dal contesto server. `ScopedCatalog.for_organization`
  rifiuta il riuso in un'altra organizzazione. **Questo binding non autentica ne
  autorizza il chiamante**: l'API futura deve risolvere membership e scope prima
  della validazione, senza prendere il tenant dal body.
- `AdministrativeField`: chiave e tipo fissi, obbligatorieta configurabile e ordine
  rappresentato dalla tuple. La raccolta deve includere nome e date/ora pertinenti.
  Il ristorante puo chiedere persone; l'hotel ospiti e camere; sanitario solo
  identita/contatto, servizio, risorsa e data/ora amministrativi. Nessun campo
  clinico libero, note, etichetta domanda personalizzata, prompt, espressione o
  regex. I nomi dei servizi sono dati visuali non attendibili, mai istruzioni.
  Questa PR valida le definizioni: non raccoglie ne valida ancora valori cliente.
- `MinuteDuration`: minuti positivi e buffer non negativi, senza default di durata.
  `CivilNightDuration`: minimo notti esplicito e orari locali check-in/out; hotel
  soltanto soggiorni, gli altri quattro settori soltanto appuntamenti.
- `Resource` e `ResourceRequirement`: operatori, spazi, tavoli, pool coperti,
  camere, attrezzatura, capacita positiva; risorsa esclusiva con capacita uno;
  quantita richiesta non superiore alla capacita dichiarata. Non sono ancora
  allocazioni o una verifica della disponibilita residua nel database.
- `WeeklyAvailability`: intervalli locali semiaperti in minuti 0..1440, senza
  sovrapposizioni o rollover notturno implicito. Giorno omesso significa unknown;
  giorno con tuple vuota significa chiuso. Chiusure civili univoche. Intersezioni
  tra orari di servizio/risorsa, eccezioni e lock restano lavoro successivo.
- `civil_stay_interval`: sottrazione delle date per contare le notti, quindi riuso
  di `local_instant` del modulo Calendar availability per
  convertire gli estremi; DST ambiguo/inesistente rifiutato, mai notti*24 ore.
- `AvailabilityObservation`: envelope immutabile con organizzazione, revisione,
  intervallo aware e `CalendarAvailability` esistente. Non ha un metodo per
  autorizzare conferme. FREE e un dato del provider, non un permesso: provenienza,
  freschezza, copertura di tutte le risorse e revisione corrente devono ancora
  essere verificate dal futuro gate server-side. UNKNOWN/DISABLED restano distinti.
- `AdditionalHandoff`: puo aggiungere revisione staff e soglia persone; non puo
  rimuovere richiesta umana, escalation clinica o altri gate obbligatori.

Nessuna pubblicazione owner/MFA/CAS, persistenza catalogo, nuova raccolta AI,
allocazione PostgreSQL v2 o sincronizzazione eventi v2 e implementata qui.
A03/A04, BookingRepository, BookingAdapterRouter e outbox rimangono invariati.
Il ristorante continua a usare il gate v1 esistente, tavolo 60 minuti; i quattro
settori aggiuntivi non ottengono nuove conferme. Nessun catalogo retroattivo.

## Prossime tre PR proposte, non avviate

1. **Catalogo amministrativo e raccolta strutturata**: API/dashboard con schema
   canonico, membership, pubblicazione owner+MFA, revisione CAS e audit; dati cliente
   verificabili e invalidazione delle risposte obsolete. Nuova autorizzazione per
   runtime/persistenza, nessuna conferma multisettore implicita.
2. **Allocazioni su intervalli e risorse**: BookingRepository/outbox esistenti,
   lock ordinati e protocollo atomico creazione/modifica/cancellazione; notti
   civili hotel, test PostgreSQL concorrenti e compatibilita writer manuali.
   Migrazione SQL futura da revisionare e autorizzare separatamente.
3. **Ammissione deterministica e certificazione verticale**: collegamento dopo
   commit A04, prove cliente/catalogo, guardrail e idempotenza; authority Google/
   esterni fail-closed, casi clinici verso staff. Fixture e prove separate per
   ciascuno dei cinque settori, prima del cutover C01/C03/C05 e GO beta.
