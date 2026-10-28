# C2.2 — catalogo amministrativo, confini e writer

Baseline: `314c4773c4b5f546ff35b24c8c4e0f3e34670efc`. Inventario eseguito prima
dell'implementazione. Nessuna estensione delle conferme automatiche V2.

## Matrice degli accessi

| Percorso | Owner | Owner MFA | Manager | Staff/client diretto |
| --- | --- | --- | --- | --- |
| Catalogo/stato/audit API | lettura | lettura | lettura | negato |
| Salva bozza API | sì, CAS/idempotenza | sì | no | no |
| Pubblica API | no | sì, CAS/idempotenza | no | no |
| Settings booking generici | comportamento esistente | comportamento esistente | comportamento esistente | nessun nuovo permesso |
| Modifica namespace catalogo via settings | no | no | no | no |
| Nuove tabelle snapshot/ricevute | solo backend | solo backend | solo backend | nessun grant client |

L'API non conferisce riservatezza owner-only alla configurazione: la SELECT SQL
di booking_settings per membri del tenant esiste già dalla 062 e non viene
ampliata o ristretta qui. Non contiene dati cliente o campi clinici raccolti.
L'autorizzazione applicativa resta obbligatoria anche con service_role.

## Writer e ordine dei lock

| Writer esistente | Lock/semantica sulla baseline | Controllo C2.2 necessario |
| --- | --- | --- |
| PUT /api/bookings/settings, BookingService.aggiorna_impostazioni | owner/manager; merge superficiale config | rifiutare namespace protetto anche nullo/uguale; non accettare tenant/revisioni come permessi |
| BookingRepository.save_booking_settings_locked | org FOR SHARE, barriera tenant 606 condivisa, capacity esclusivo; UPSERT intero config | conservare il catalogo corrente atomicamente, non copiarlo da input obsoleto |
| BookingRepository.upsert_booking_settings_config e facade | sostituzione config senza lock | serializzare capacity e preservare namespace corrente nel SQL |
| BookingRepository.upsert_booking_settings | capacity esclusivo; aggiorna solo fasce/capienze/slot | mantenere config invariato; testare riga assente e concorrenza |
| Calendar.save_calendar_settings | barriera 606, lock credenziali 604, credentials row, org KEY SHARE, capacity, settings row | sequenza invariata; legge config dopo capacity e preserva catalogo |
| InMemoryBookingRepo e fallback demo | sostituzione config, nessuna transazione durevole | nessuna pubblicazione; stesso rifiuto del namespace protetto |
| inbound/manuali | lettura settings o booking CRUD | nessuna attivazione/import del catalogo nel gate A05 |
| fixture SQL e migrazioni | setup/test, non endpoint applicativi | non considerarli bypass remoti provati |

Il nuovo writer riusa booking_settings_lock, senza acquisire lock Calendar o
chiamare provider. Non riordinare i lock: il lock organizzazione precedente
alla barriera serializza già l'erasure; un'inversione introdurrebbe un rischio.

## Stato e transazione progettati

`booking_settings.config.catalog_v2` è canonico: revisione dello stato, bozza,
pubblicato e revisione pubblicata. Ogni scrittura richiede revisione attesa e UUID
idempotente. Il salvataggio della bozza non pubblica. La pubblicazione incrementa
la revisione, valida nuovamente, crea snapshot immutabile e svuota la bozza.

Ricevute minimali: tenant, attore, richiesta, azione, hash e revisione applicata;
nessuna copia del payload o della risposta nelle ricevute. Replay autorizzato
solo dopo controllo attuale di membership/ruolo/MFA e restituisce stato corrente
più revisione della ricevuta, senza ripristinare vecchie configurazioni.

CAS, config, snapshot, ricevuta e INSERT nel preesistente audit_log sono nella
stessa transazione; un audit fallito annulla tutto. Non usare audit best-effort.
Il namespace aggiornato non modifica altri campi o autorità Calendar/A05.

## Privacy e certificazioni separate

L'export A06 attuale esclude esplicitamente il JSON di configurazione; l'erasure
non elimina booking_settings né organizations. Una FK CASCADE non prova quindi
export o cancellazione dei nuovi snapshot. Nessuna modifica alle policy/codice
GDPR viene introdotta per mascherare questo limite. Snapshot amministrativi e
audit sono nel perimetro C2.2 autorizzato; classificazione/retention/export ed
erasure effettiva rimangono gate C05/A06 prima della beta. Nessun dato paziente,
prompt o campo clinico arbitrario fa parte del catalogo. Nomi servizi sono dati
non attendibili da visualizzare con escaping, non istruzioni.

Migrazione soltanto locale; ACL/RLS/DSN remoti non letti o certificati. Nessun
deploy o feature flag operativo. La race preesistente nel refresh credenziali
resta separata e blocca la certificazione degli adapter interessati.
