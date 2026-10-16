# C01 — Piano verificabile di allineamento Staging

Stato: **preparato e collaudato offline; nessuna migrazione remota eseguita**.
Evidenza raccolta il 16 ottobre 2026. Target esclusivo: Melpis Staging,
project ref `ucncxwnhcdrqonknkvau`, riattivato e verificato ACTIVE_HEALTHY.
Production è fuori perimetro. Prima dell'esecuzione serve approvazione umana
del batch esatto, del backup e degli effetti sull'accesso descritti qui.

## Baseline ed evidenza

Il confronto usa lo schema effettivo del catalogo, non la cronologia migrazioni.
La cronologia Staging riporta due voci 057: questo non prova quali operazioni
precedenti siano applicate. Snapshot di soli metadati e aggregati, senza righe
personali esportate: `staging-catalog-cycle2.json` e integrazioni read-only
raccolte da Sol. Directory privata locale:
`C:\Users\muzzo\.codex\artifacts\melpis-v1-alignment-20261016-162734`.

Schema atteso ricostruito usando `src/whatsapp/schema.sql`,
`src/core/db/schema.sql`, `src/core/db/triggers.sql` e tutti i 56 file presenti
che corrispondono a `src/core/db/migrations/0*.sql`, ordinati per nome fino a
`059_auth_access_lifecycle.sql`. Sono 56 file, non una sequenza contigua di 59
migrazioni: lo scaffolding iniziale è nei tre file schema/trigger citati.
Baseline documentale verificata con `git rev-parse HEAD`:
`f7745cfae2c09500c799488e296c78b00df71748`.
Manifest SHA-256 di ogni input in `offline-schema-report.json` privato.

| Oggetti public | Staging | Ricostruzione repo fino a 059 |
|---|---:|---:|
| Tabelle | 41 | 42 |
| Colonne | 424 | 427 |
| Policy | 43 | 45 |
| Trigger public | 6 | 7 |
| Funzioni public non-extension | 8 | 10 |
| Constraint | 145 | 148 |

Tutte le 41 tabelle Staging hanno RLS attivo. Questi conteggi escludono
trigger `auth`, indici e funzioni appartenenti a extension; tali oggetti sono
controllati separatamente nel preflight.

## Diff esatto e decisioni

1. Manca `auth_access_lifecycle` con colonne `auth_user_id uuid NOT NULL`,
   `provisioned_at timestamptz NULL`, `disabled_at timestamptz NULL`, PK e FK
   verso `auth.users(id) ON DELETE CASCADE`.
2. Mancano policy `auth_access_lifecycle_deny_clients` e policy RESTRICTIVE
   `user_profiles_active_access`; mancano funzioni `current_auth_access_allowed`,
   `initialize_auth_access_lifecycle`, `mark_auth_access_provisioned`; manca
   trigger public `trg_mark_auth_access_provisioned`. Su `auth.users` è presente
   soltanto `trg_sync_auth_user`: manca quindi anche
   `trg_initialize_auth_access_lifecycle`. Tutti questi delta appartengono alla 059.
3. `outbound_dedup` ha già RLS attivo e la policy, ma anon/authenticated hanno
   ancora SELECT/INSERT/UPDATE/DELETE. **058 non è un no-op**: revoca queste ACL
   e ricrea la deny policy. Il backend privilegiato mantiene il proprio percorso.
4. Manca `whatsapp_accounts_phone_number_id_key UNIQUE(phone_number_id)`.
   L'ispezione separata degli indici conferma soltanto PK(id) e indice NON UNIQUE
   su organization_id. Aggregato rilevato: zero gruppi duplicati non-NULL.
   Serve una correzione dedicata e approvata, non la modifica di una migrazione
   già pubblicata. La UNIQUE permette più NULL secondo la semantica PostgreSQL;
   la colonna prevista dal repo è NOT NULL.
5. Tre FK organization_id sono ON DELETE CASCADE in Staging ma NO ACTION nella
   ricostruzione repo: `faq_cache_organization_id_fkey`,
   `onboarding_profiles_organization_id_fkey`,
   `message_feedback_organization_id_fkey`. **Conservare nel batch C01** e
   revisionare separatamente: non riscrivere automaticamente la semantica delete.
6. Due CHECK differiscono soltanto nella rappresentazione dei cast di array
   varchar/text: `airtable_connections_token_type_check` e
   `airtable_webhook_events_status_check`. Gli stessi cast vengono riscritti
   da pg_dump/restore sul medesimo PostgreSQL 17. Valori ammessi invariati;
   nessuna correzione proposta.
7. `check_chunk_org_consistency()` differisce soltanto CRLF/LF.
   Staging ha in più `rls_auto_enable()` del provider: conservare; non cancellare.

Nessuna altra differenza di tabella/colonna/policy/trigger public è emersa
dal confronto delle definizioni catturate. Questo non certifica ruoli,
configurazione Auth, grant di ogni oggetto o prestazioni del servizio remoto.

## Batch proposto e identità immutabile

| Ordine | File esatto | SHA-256 dei byte collaudati |
|---|---|---|
| 1 | `src/core/db/migrations/058_outbound_dedup_rls.sql` | `bc1c2304da567389e4bf1e9a80ea4e8bd06799803efd672c5579d6a83785a715` |
| 2 | `src/core/db/migrations/059_auth_access_lifecycle.sql` | `49f2e6e1f7898d3c120d29cb70a426f8a8985a7a8bb27242e741e28cc3398610` |
| 3, separatamente approvato | artefatto privato `c01_unique_phone_delta.sql` | `21d5a4484e208d192a30c2e71a965c8855f5addfa81696b3a6cbfaa58e0b9ed3` |

058/059 esistenti non sono stati modificati. Gli hash si riferiscono ai byte
locali collaudati: se un checkout cambia terminatori di riga, fermarsi e
riconciliare gli hash prima di approvare. Il terzo file è una proposta privata
non pubblicato come migrazione e non va applicato implicitamente.

La correzione proposta è un blocco DO che aggiunge la UNIQUE nominata soltanto
se il constraint omonimo non esiste. Preflight obbligatorio: se un constraint
omonimo esiste deve avere esattamente la definizione attesa; altrimenti STOP.
L'aggiunta effettua una scansione e prende un lock: concordare finestra,
`lock_timeout` e `statement_timeout`; in caso di timeout abortire senza retry cieco.
Non inserire dati di test nel database remoto come verifica della UNIQUE.

Contenuto SQL esatto dell'artefatto privato collaudato, incluso il commento;
il digest in tabella identifica il file originale, non un copia/incolla dal
renderer Markdown (che potrebbe cambiare terminatori di riga):

```sql
-- C01 separate staging schema correction; NOT part of migrations 058/059.
-- Run only after rechecking zero duplicate non-NULL phone_number_id groups.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'public.whatsapp_accounts'::regclass
      AND conname = 'whatsapp_accounts_phone_number_id_key'
  ) THEN
    ALTER TABLE public.whatsapp_accounts
      ADD CONSTRAINT whatsapp_accounts_phone_number_id_key UNIQUE (phone_number_id);
  END IF;
END;
$$;
```

## Effetti della 059 su utenti esistenti

**Non è una migrazione puramente additiva dal punto di vista autorizzativo.**
Backfill di tutti i 27 subject presenti in `auth.users`: `provisioned_at=NOW()`
se non esiste già la riga lifecycle. Venti subject hanno almeno una membership;
sette subject storici senza membership vengono quindi negati e non possono
essere interpretati come nuovi signup per ottenere JIT un'altra organizzazione.
I sette subject richiedono verifica privata del responsabile, senza pubblicare
identità o ripristinare accesso automaticamente.

Aggregati Staging di riferimento: 27 auth user, 24 organizzazioni, 20 membership,
27 profili. Il batch non deve modificare/eliminare tali righe o dati tenant.
I nuovi auth user creati dopo 059 ricevono lifecycle non provisioned; una grant
membership marca il provisioning. `disabled_at` blocca accesso applicativo e
RLS con JWT già emessi. La perdita dell'ultima membership nega accesso; la
perdita di una tra più membership conserva l'accesso alle altre organizzazioni.
Il replay non riabilita utenti disabilitati e non trasforma nuovi utenti in
storici. Nessun processo deve fare backfill ripetuti manuali con UPDATE.

## Preflight, backup e approvazione

1. Confermare ref Staging nell'interfaccia e nella connessione; bloccare ogni
   sessione puntata a Production. Identificare proprietario dell'esecuzione.
2. Ripetere solo metadati/aggregati: salute, versione PostgreSQL, schema/grant,
   auth trigger, zero gruppi phone duplicati, conteggi e subject con/senza grant.
   Se i dati cambiano, aggiornare l'impatto prima dell'approvazione.
3. Raccogliere snapshot schema completo incluse ACL, policy, trigger auth,
   funzioni e indici. Verificare disponibilità reale di backup del provider;
   non presumere PITR o restore disponibile su quel piano.
4. Prima di ogni scrittura ottenere backup dati cifrato di auth+public e
   dipendenze necessarie, in destinazione privata fuori host; niente dump o
   chiavi in Git/PR/chat. Usare un canale database diretto autorizzato, accesso
   minimo e strumenti compatibili con la versione remota. Chiave separata dal
   backup, accesso limitato, retention privata concordata (proposta: 7 giorni
   dopo accettazione, salvo policy vigente), cancellazione verificata.
5. Verificare restore in ambiente isolato autorizzato prima del batch. Dati
   reali Staging non possono essere copiati su locale senza autorizzazione
   distinta; il drill sintetico sotto non sostituisce il backup Staging.
6. Approvazione esplicita: 058, effetti 059 sui sette subject, delta UNIQUE
   separato, rischio lock, backup/restore e piano incidenti. Poi eseguire il
   batch concordato con transazioni esplicite, ON_ERROR_STOP e timeout finiti;
   registrare file/hash e risultato, senza rieseguire tutto lo storico.

## Postflight e rollback che conserva i deny

Controllare definizioni e ACL 058; tabella/PK/FK/funzioni/entrambe le policy e
trigger public+auth 059; conteggi lifecycle coerenti con snapshot auth; deny
storici senza membership e nessuna variazione inattesa di org/membership;
UNIQUE su phone. Usare account sintetici già autorizzati per i controlli API,
mai account dei clienti. Validare RLS con ruolo authenticated e claim subject;
il risultato di una query service-role da solo non prova isolamento.

Su errore prima del COMMIT eseguire ROLLBACK. Dopo il commit, rollback operativo
significa fermare login/traffico di staging, preservare lifecycle/provisioned_at/
disabled_at, revoche ACL e policy deny; correggere in avanti o scegliere una
versione applicativa compatibile. **Non DROP della 059, non disattivare RLS,
non rimuovere disabled_at e non dare di nuovo grant client come rollback.**
Un eventuale restore completo a prima della 059 richiede indisponibilità,
riconciliazione privata dei deny/revoche avvenuti dopo il backup e riapplicazione
del gate prima di riaprire traffico. Nessun downgrade DB automatico autorizzato.
La UNIQUE va conservata; se la correzione fallisce, la transazione abortisce.

## Collaudo offline eseguito

Container nuovo e dedicato `melpis-c01-offline-20261016`, bind solo loopback
porta 61897, nessun database esistente usato. Immagine PostgreSQL 17 pgvector:
`pgvector/pgvector@sha256:ac08538c6f8b9904c33c8224c5e5706dbe760aca29db1d096972b4052c22a75d`.
Server PostgreSQL 17.11 (Debian 17.11-1.pgdg12+2), x86_64. Scaffolding auth minimale della
suite, non stack Supabase completo; i dati sono esclusivamente sintetici.

- **29 test passati**, 4 warning deprecazione httpx, in 54,96 secondi:
  `tests/core/auth/test_access_revocation.py` e
  `tests/core/auth/test_security_hardening.py`. Coprono API/revoca/JWT vecchi,
  JIT concorrente, client ACL e RLS con `auth.uid()` basato su claim del test.
- **15 controlli sintetici passati**: migrazione da 058 con utenti storici,
  backfill, membro ancora autorizzato, storico senza membership negato,
  nuovo subject non provisioned, membership marca provisioning, ultima revoke,
  disabled, replay preserva stati, cascade auth user, replay UNIQUE, duplicate
  rifiutato, replay 058 revoca ACL, restore preserva disabled e storico senza
  membership ed equivalenza catalogo restore. Il report distingue i 15 assert.
- Dump sintetico cifrato Fernet in memoria, decifrato e ripristinato su database
  separato `c01_restore`; nessun dump dati reali. La chiave effimera non è stata
  conservata. Catalogo equivalente dopo restore: soltanto i due CHECK varchar
  citati sono normalizzati per la rappresentazione dei cast; raw catalog non
  identico, differenza registrata nel report. Stati di deny preservati.
- Prima ricostruzione senza `search_path=public,extensions` fallita sullo HNSW;
  ricostruzione corretta completata senza saltare la migrazione 024. Nessuna
  conclusione positiva deriva dall'esecuzione iniziale fallita.

Evidenze private: `offline_schema_qa.py`, `offline-schema-report.json`,
`expected-catalog-pg17.json`, `c01-auth-tests.xml`, `c01-auth-tests.log`,
`c01_unique_phone_delta.sql`, backup cifrato sintetico. Nessuna scrittura
remota effettuata dal collaudo. Il container dedicato viene rimosso a fine task;
gli artefatti restano locali per review.

## Dipendenze future, fuori da questo batch

A04 (quote/contabilizzazione atomica), A05 (workflow prenotazioni) e A06
(privacy) possono richiedere nuove migrazioni distinte: attendere implementazione e security review,
produrre diff e hash propri, ripetere drill e richiedere approvazione distinta.
Non includerle automaticamente in C01, non applicare nuove migrazioni per sola
presenza nel branch. Questo piano non approva deploy, merge o uso di Production.
