# C01 — Piano verificabile di allineamento Staging

Stato: **backup reale cifrato e restore verificati; batch 058/059/UNIQUE
applicato con successo esclusivamente a Staging. C01 resta in lavorazione per
lo schema della release finale e la certificazione applicativa**.
Evidenza raccolta il 16 ottobre 2026. Target esclusivo: Melpis Staging,
project ref `ucncxwnhcdrqonknkvau`, riattivato e verificato ACTIVE_HEALTHY.
Production è fuori perimetro. Il fondatore ha approvato il batch esatto
058/059/UNIQUE descritto qui, subordinandolo a backup e restore reali riusciti.
L'approvazione del batch è acquisita e non va richiesta nuovamente a parità di
scope e hash. Il consenso distinto alla copia locale è ora acquisito per dump
cifrato e restore temporaneo su questo PC, alle condizioni riportate sotto.

## Aggiornamento operativo — backup, restore e batch completati

Nel ciclo successivo Sol ha verificato nell'interfaccia che Staging è sul piano
**Free e non dispone di backup del provider**. Non risultano host né bucket R2
configurati per il backup. Il fondatore ha successivamente autorizzato un dump
cifrato e restore temporaneo su PC, subordinati a PC/disco protetti, spazio
adeguato e assenza di segreti o dati personali nei log. Il backup cifrato va
conservato almeno fino alla verifica delle migrazioni e non può essere eliminato
senza conferma umana distinta. Se una condizione non è verificata, fermarsi.

Controlli locali di Sol: circa 353 GB liberi, database Staging circa 15 MB,
Defender con protezione in tempo reale attiva e firewall attivo su tutti i
profili. Il fondatore ha confermato **BitLocker C: cifrato al 100%, XTS-AES 128,
Protection On con TPM**. La conferma risolve il precedente controllo bloccato
da AccessDenied; i comandi non avevano provato lo stato del disco.

La directory artefatti storica concede lettura a `CodexSandboxUsers` e non va
usata per dati reali o chiavi. Le directory dedicate backup e chiavi sono state
create e verificate con ereditarietà disabilitata e sole tre ACE: proprietario,
SYSTEM e amministratori; nessun accesso `CodexSandboxUsers`.
Chiave e dump non devono comparire in Git, PR, chat o output dei processi.

TLS verso Staging verificato con CA pubblica Supabase, fingerprint SHA-256
`700723581420dd1ac98fd7e9ac529f0ef210eadcaf87fc868a3ad7d114c2f3b7` e
`verify-full` (hostname e certificato verificati). Il vecchio `sslmode=require`
nel DSN viene sovrascritto nel client senza cambiare il file ambiente.

Il wrapper privato di Sol `c01_real_restore.py` verifica immagine per digest,
log Docker disabilitati, rete assente, nessuna porta host e PGDATA realmente
su tmpfs (`statfs`), con psql silenzioso. Il confronto previsto include schema,
ACL, privilegi predefiniti, viste, sequenze, trigger e definizioni auth/public,
oltre ai dati. Query di catalogo e fingerprint righe hanno superato prima il
test sintetico e poi il restore reale. Backup reale `20261016T185355Z`, SHA-256
dell'archivio prima della cifratura:
`bead16565924045ae0d2411256ec46fc77cac0104f5a5094ae8d6b9620f453f8`.
Archivio e manifest `-verified` sono cifrati; le chiavi sono protette da DPAPI
utente e conservate separatamente nelle directory con sole tre ACE verificate.
Nessun dato personale o segreto è inserito in questo documento.

**Restore reale PASS:** catalogo e impronte di tutte le 80 tabelle confrontate.
Il ripristino su clone isolato include correzioni ripetibili da manifest per
owner di due viste `pg_stat` e otto grant USAGE di schema. Sono correzioni di
fedeltà del clone, non cambiamenti al database sorgente. La normalizzazione
del confronto riguarda soltanto ordine delle tuple ACL e rango ordinale;
tipi completi e collation delle colonne sono stati verificati. Nessun campo
semantico mancante viene coperto dal solo conteggio righe.

Sol ha quindi applicato il batch approvato a Staging, con esito positivo:

| Registrazione migrazione | Operazione |
|---|---|
| `20261016190107` | 058, RLS e revoca ACL `outbound_dedup` |
| `20261016190116` | 059, lifecycle, backfill e gate autorizzativo |
| `20261016190124` | UNIQUE dedicata `whatsapp_accounts.phone_number_id` |

Postflight: **42/42 tabelle public con RLS attivo**, 27 lifecycle e 27 subject
provisioned; invariati 27 auth user, 27 profili, 24 organizzazioni e 20 membership,
con sette subject senza membership. ACL outbound anon/authenticated negate;
EXECUTE del helper consentito ad authenticated e negato ad anon; UNIQUE esatta
e trigger auth/membership presenti. Le tre FK CASCADE preesistenti sono conservate.

Confronto dei dati LIVE su tutte le 80 tabelle preesistenti: l'unica variazione
è `supabase_migrations.schema_migrations`, con tre registrazioni aggiunte;
nessun'altra variazione di dati rilevata. La nuova tabella lifecycle è verificata
separatamente con i conteggi sopra. Sul clone reale dopo le migrazioni, cinque
assert sintetici sono PASS: accesso membro, separazione tenant A/B, revoca con
claim di JWT precedente, profilo revocato nascosto e account disabilitato.
I test del clone sono transazionali con rollback; nessuna riga sintetica è
stata inserita nel database remoto per eseguirli.

Il container restore temporaneo è stato rimosso da Sol dopo le verifiche; i dati del clone erano su tmpfs. Una copia della procedura di recupero, del manifest di esecuzione e del certificato pubblico è conservata nella directory privata del backup, senza credenziali sorgente.
**Backup cifrato e chiavi restano conservati: nessuna cancellazione automatica,
serve conferma umana esplicita prima dell'eliminazione**, anche dopo postflight. Questo backup locale dipende dal profilo Windows per DPAPI e non sostituisce il backup off-site ancora previsto in C02.

Limiti: schema Staging allineato al batch corrente, non certificazione E2E
dell'applicazione o dei provider remoti. C01 resta in lavorazione per lo schema
della release finale. Nessun accesso Production, merge o deploy è parte di
questo batch. Il warning Advisor 0029 sul helper SECURITY DEFINER è stato
revisionato e accettato da Sol/Security come intenzionale: helper booleano
basato soltanto su `auth.uid()`, senza argomenti arbitrari né side effect,
`search_path` vuoto ed EXECUTE concesso soltanto ad authenticated. Il warning
password preesistente resta separato e non è risolto da questa accettazione.

Preflight prima del batch, eseguito da Sol in sola lettura: 27 auth user, 20 subject
con membership (join tramite `user_profiles`) e 7 senza; 24 organizzazioni e
20 membership; zero gruppi duplicati non-NULL di `phone_number_id`.
Prima dell'applicazione `auth_access_lifecycle` era assente e `anon` conservava
INSERT su `outbound_dedup`. Questi sono dati storici di preflight, sostituiti
per lo stato corrente dal postflight sopra. Le precondizioni locali di protezione
disco, spazio, sicurezza e ACL e la condizione backup/restore reale sono state
soddisfatte prima di applicare il batch.

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

| Oggetti public | Staging prima del batch | Ricostruzione repo fino a 059 |
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

## Diff storico prima del batch e decisioni

L'elenco seguente descrive i delta rilevati prima dell'applicazione, non gli
oggetti ancora mancanti dopo il postflight.

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

## Batch approvato e applicato — identità immutabile

| Ordine | File esatto | SHA-256 dei byte collaudati |
|---|---|---|
| 1 | `src/core/db/migrations/058_outbound_dedup_rls.sql` | `bc1c2304da567389e4bf1e9a80ea4e8bd06799803efd672c5579d6a83785a715` |
| 2 | `src/core/db/migrations/059_auth_access_lifecycle.sql` | `49f2e6e1f7898d3c120d29cb70a426f8a8985a7a8bb27242e741e28cc3398610` |
| 3, delta dedicato incluso nell'approvazione condizionata | artefatto privato `c01_unique_phone_delta.sql` | `21d5a4484e208d192a30c2e71a965c8855f5addfa81696b3a6cbfaa58e0b9ed3` |

058/059 esistenti non sono stati modificati. Gli hash si riferiscono ai byte
locali collaudati: se un checkout cambia terminatori di riga, fermarsi e
riconciliare gli hash con l'approvazione ricevuta. Il terzo file è un artefatto
privato non pubblicato come migrazione; la sua approvazione è condizionata allo
stesso backup/restore reale e non permette un'applicazione anticipata.

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
4. Condizione soddisfatta per questo batch, da ripetere prima di nuovi batch:
   prima di ogni scrittura ottenere backup dati cifrato di auth+public e
   dipendenze necessarie, sul PC autorizzato in directory privata dedicata;
   verificare prima cifratura/protezione disco e ACL ristrette; niente dump o
   chiavi in Git/PR/chat. Usare un canale database diretto autorizzato, accesso
   minimo e strumenti compatibili con la versione remota. Chiave separata dal
   backup, accesso limitato. Conservare il backup cifrato fino a migrazioni
   verificate e poi fino a conferma umana esplicita della cancellazione;
   nessuna eliminazione automatica per scadenza o cleanup. Niente PII o segreti
   nei log, e STOP se una condizione non è verificata.
5. Verificare restore temporaneo in ambiente locale isolato prima del batch,
   entro il consenso ricevuto e dopo i controlli sopra. Il drill sintetico
   sotto non sostituisce il backup Staging né il restore dei dati reali.
6. Registrare l'approvazione condizionata già ricevuta per 058/059 e delta UNIQUE
   dedicato; verificare che scope, hash, effetti sui sette subject e piano
   incidenti corrispondano al presente piano. Soddisfare prima la condizione
   backup/restore reali e la destinazione privata approvata, poi eseguire il
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
