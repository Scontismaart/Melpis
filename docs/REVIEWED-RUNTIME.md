# Runtime CrewAI/MCP con derivata interna verificata

La derivata interna è un backport metadata del wheel CrewAI 1.15.4, non una
release né un'approvazione upstream. Aggiorna il vincolo MCP upstream a
`mcp>=1.30.0,<1.31` (con pin applicativo `mcp==1.30.0`) e rimuove la dipendenza
obbligatoria `crewai-cli==1.15.4`: l'applicazione usa le API CrewAI, ma non il CLI.
Il CLI trascina `uv~=0.11.6`; `uv` non è invocato dal servizio, non è compatibile
con il fix minimo `uv>=0.13.4` e non deve essere distribuito nell'immagine runtime.
Il builder verifica l'assenza di entrambi nel runtime e `pip check` verifica il
grafo risolto; il codice CrewAI resta byte-identico alla wheel ufficiale.

Il 22 ottobre 2026 l'audit ha segnalato `CVE-2026-114603` /
`GHSA-xcq7-p3pf-p88h` in `opentelemetry-api==1.42.1`. La
[advisory ufficiale](https://github.com/open-telemetry/opentelemetry-python/security/advisories/GHSA-xcq7-p3pf-p88h)
indica `1.45.1` come prima versione corretta. I tre vincoli CrewAI
`opentelemetry-api`, `opentelemetry-sdk` ed exporter OTLP HTTP `~=1.42.0`
impediscono la risoluzione corretta: la stessa derivata modifica esclusivamente
quei tre vincoli in `>=1.45.1,<1.46`, verificandone la presenza esattamente una
volta. Anche il wheel ufficiale `crewai-core==1.15.4` ripete i tre vincoli:
viene ricostruito separatamente con le sole tre sostituzioni in METADATA e
RECORD rigenerato. Il manifest include sorgente, derivata e hash dei file di
entrambi; l'installer verifica versione e payload core effettivamente installati.
`requirements.txt` fissa core a `1.15.4` e i tre pacchetti OTel a `1.45.1`; SDK/exporter comuni,
gRPC e semantic conventions vengono risolti e controllati nell'inventario
completo. Nessuna eccezione audit è stata aggiunta, nessun codice CrewAI
modificato. Questi hash sostituiscono quelli della precedente derivata A05.

## Installazione e provenienza

Usare un ambiente virtuale Python 3.12 pulito, poi:

```text
python scripts/bootstrap_reviewed_pip.py
python scripts/reviewed_runtime.py install
```

Il bootstrap esplicito porta pip a 26.2.1: il pip 25.0.1 nell'immagine base
locale ha advisory OSV e rimane incluso nell'inventario, non escluso dall'audit.
Il helper verifica prima il digest della wheel ufficiale, poi installa solo il
file locale con `--upgrade --no-index`, senza dipendenze aggiuntive, e verifica
la versione in un processo Python nuovo. Usa una directory temporanea propria.
Su host rifiuta Python globale: attivare un virtualenv. `--isolated-build` è
riservato ai runner CI usa-e-getta e agli stage Docker; non usarlo sul PC globale.
La chiamata non avviene implicitamente dal runtime installer. Anche il virtualenv
separato del tool audit riceve il bootstrap, prima di installare pip-audit.

CI, workflow migrazioni e build Docker usano lo stesso comando. Docker passa
`--user` nel builder e verifica nuovamente i file copiati nel runtime non-root.
Il file `requirements.txt` conserva la versione ed extra `crewai[litellm]` come
input; il builder genera un riferimento diretto alla wheel interna sotto
`.runtime/reviewed-dependencies`, senza perdere gli altri requisiti o l'extra.
Un'installazione diretta con pip è intenzionalmente incompatibile con il vecchio
vincolo upstream e non va forzata con `--no-deps`, override o eccezioni audit.

Il builder standard-library scarica esclusivamente la wheel ufficiale PyPI
verificata con SHA-256:

| Artefatto | SHA-256 |
|---|---|
| CrewAI 1.15.4 ufficiale | `bb7210ccbac3c75c231966fb5dd8d77797945c5ddb220ca3b54b50bf0a31a711` |
| Derivata interna canonica | `94030a7648346a9abd60213cf7964cc82f6afd1d9c6d098c13a9e1642989396c` |
| CrewAI Core 1.15.4 ufficiale | `3af1675c6f0a5fe394edafd227d2a43ded25997c246aacbe682ee745b889a07e` |
| Derivata Core canonica | `75112b4195665f57f19f3841f17d0dbd4d042d74230412e96dd27f224a9604a7` |
| Manifest di provenienza combinato | `3f1df5aa3885af30864ea39f4a9c22197b8a68d9583517519ebad8475da5bdfa` |
| pip 26.2.1 ufficiale | `71138adf1f4ca900cdb7d289c21b7494329f2332b6d85f0e1c42108c0384ed3e` |

Fonti primarie: [CrewAI 1.15.4 su PyPI](https://pypi.org/pypi/crewai/1.15.4/json),
[CrewAI Core 1.15.4 su PyPI](https://pypi.org/pypi/crewai-core/1.15.4/json),
[pip 26.2.1 su PyPI](https://pypi.org/pypi/pip/26.2.1/json) e
[wheel ufficiale pip](https://files.pythonhosted.org/packages/f3/6e/1736e5b4ae2b778ef2f81c47d797de9f891d4d8acb047a24ca37a60294dd/pip-26.2.1-py3-none-any.whl).

Cambiano soltanto il vincolo MCP, tre vincoli OpenTelemetry e la dipendenza CLI in METADATA, più RECORD
rigenerato. I restanti file, inclusi codice, versione e licenza MIT, restano
identici byte per byte.
ZIP_STORED, ordine dei file e metadati ZIP fissi evitano dipendenze da zlib e
timestamp; il digest è stato riprodotto su Windows e Linux con Python 3.12.
Il manifest verificato include gli hash dei 527 file CrewAI e 28 file Core,
esclusi i rispettivi RECORD che pip riscrive. Il runtime deve avere esattamente CrewAI/Core 1.15.4 e MCP
1.30.0, i tre pacchetti OpenTelemetry `1.45.1`, senza `crewai-cli` e `uv`; ogni file atteso viene ricontrollato, senza
importare CrewAI o provider.

Nessuna wheel viene caricata su un registro: ogni build la ricostruisce dalla
sorgente ufficiale verificata. Generati e wheel rimangono ignorati da Git e dal
contesto Docker. L'immagine finale conserva `runtime-provenance.json` e il
report audit della propria build, non la wheel o il virtualenv del tool audit.

## Audit del runtime realmente installato

Creare un virtualenv separato per `pip-audit==2.10.1`. Con il virtualenv runtime
ancora attivo, eseguire:

```text
python scripts/reviewed_runtime.py audit --audit-python /percorso/tool-venv/bin/python
```

Prima di installare pip-audit nel tool-venv usare il suo interprete con
`scripts/bootstrap_reviewed_pip.py`, senza il flag globale. CI e Docker lo fanno
esplicitamente. Il cache mount BuildKit contiene soltanto download pubblici e
non è copiato nell'immagine finale.

Il wrapper usa OSV (`https://api.osv.dev/v1/query`), `--strict` e tutti i percorsi
site-packages del runtime. Confronta identità e versioni esatte del report con
quelle realmente importabili: il sito utente dell'host è incluso soltanto se
`site.ENABLE_USER_SITE` lo abilita. In un virtualenv isolato non viene incluso
un percorso utente estraneo; in un runtime con installazione `--user` resta
incluso e ogni duplicato continua a bloccare l'audit. Confronta quindi
l'inventario installato prima e dopo l'audit. Nessun pacchetto omesso, aggiunto,
duplicato o saltato è ammesso; `torch==2.14.0+cpu` non è normalizzato a una
versione diversa. Controlla poi il manifest e la policy esistente senza nuove
eccezioni. Il tool audit resta fuori dall'inventario applicativo.

Il servizio PyPI di pip-audit saltava il suffisso `+cpu`; il primo report di
laboratorio è conservato come prova del limite. Un secondo audit OSV completo
ha riportato 204 distribuzioni, incluso Torch con versione esatta, senza skip.
Gli advisory residui Chroma/json-repair sono gli stessi già presenti nella
policy, con razionale limitato e versioni esatte; gli alias non vengono riscritti
per superarla. La policy resta invariata. Report e inventario
vengono prodotti dai controlli effettivi di ciascuna build, non dal vecchio lab.

La scansione di ciascuna immagine avviene nel builder prima di copiare i file;
la verifica finale controlla gli stessi payload e confronta anche l'intero
inventario del runtime non-root con il report realmente generato dal builder,
riapplicando la policy. Pacchetti aggiunti o omessi fra gli stage bloccano la build. Servono
ancora risultati sul commit candidato e su entrambe le architetture amd64/arm64;
non dedurli dal solo laboratorio. I test includono stdio reale e casi reali HTTP
streamable/SSE su server FastMCP locale, porta dinamica su 127.0.0.1, senza
provider o segreti, con timeout e cleanup del subprocess. Aggiungere i casi non
equivale a dichiararli già passati sull'immagine finale. Nessun E2E
Meta/Google/Stripe è certificato.

## Aggiornamento o rimozione della derivata

Quando upstream pubblicherà un vincolo compatibile, preferire la release
ufficiale dopo review. Ogni aggiornamento qui richiede fonte ufficiale e digest,
diff byte-per-byte, nuovo digest canonico/manifest, test di compatibilità MCP e
regressioni Melpis, audit completo e review di sicurezza. Non cambiare hash o
rilassare verifiche per far passare una build. I report devono essere nuovi;
un errore di rete/auditor non può riutilizzare un report precedente.


## Correzione filelock — candidata da artifact ufficiali, 29 ottobre 2026

Le advisory [CVE-2026-112909](https://github.com/tox-dev/filelock/security/advisories/GHSA-69hj-f68j-2879)
e [CVE-2026-112910](https://github.com/tox-dev/filelock/security/advisories/GHSA-8mx5-7m5m-r77g)
indicano filelock 4.2.1 come prima versione corretta. Il precedente pin 3.20.3
non le risolve. LiteLLM 1.106.2 e le release ufficiali fino alla 1.106.5
richiedono filelock >=3.16.1,<4.0: pip standard rifiuta il pin corretto.

La candidata minima usa [LiteLLM 1.103.10 ufficiale](https://pypi.org/pypi/litellm/1.103.10/json)
e filelock 4.2.1. Quella release non dichiara ne usa direttamente filelock:
la verifica indipendente dello sdist ufficiale (SHA-256
`b1bf12810b6565156c6b23bda2ccaf115c6edafbbf92e98220ecbc73f89d6972`)
non trova riferimenti nei 3.697 file. Non e una dichiarazione di certificazione
upstream di LiteLLM con filelock 4: la compatibilita va verificata nel grafo
e nei consumatori reali. CrewAI 1.15.4 ammette LiteLLM >=1.84,<2; Python 3.12
soddisfa i vincoli delle due distribuzioni. Nessuna wheel LiteLLM modificata.

Il pin esplicito huggingface-hub 1.33.0 conserva il componente embedding gia
revisionato: la rimozione del vincolo LiteLLM <2 avrebbe altrimenti consentito
una major 2.x non necessaria. Torch e Hugging Face devono accettare il nuovo
filelock tramite i propri metadata ufficiali e superare le prove di lock.
Non vengono modificati CrewAI/Core, MCP, OTel, relativi backport, hash o builder.

La risoluzione usa dipendenze normali, senza --no-deps, ignore-installed,
falsificazioni metadata o nuove eccezioni audit. Installazione effettiva,
pip check, verifica provenance, audit completo e regressioni sono gate prima
del merge, insieme alle quattro build API/web e alla review indipendente.
Il virtualenv pip-audit resta separato: la sua versione filelock non costituisce
prova della versione del runtime; il confronto inventario gia esistente resta
obbligatorio. Il run storico C2.2 40769914701 rimane fallito e non va riscritto.
Nessun provider reale, deploy o migrazione remota viene autorizzato dal fix.

Il fondatore ha autorizzato separatamente test e PR di questa candidata:
l'assenza di una dipendenza LiteLLM non equivale al supporto esplicito upstream
richiesto dal primo mandato. Il merge richiede una nuova approvazione.

La verifica locale Python 3.12 ha installato il grafo con pip standard:
`pip check` e verifica dei file CrewAI/Core tramite il manifest esistente
superati. L'audit del runtime effettivo copre 204 distribuzioni: nessuna nuova
vulnerabilita, con le sole eccezioni gia revisionate ChromaDB/json-repair.
Le nove voci del report rappresentano cinque ID distinti, quattro duplicati.
Rispetto all'ambiente precedente non cambiano i pacchetti presenti: oltre ai
due pin mirati cambiano otto versioni transitive (google-api-core, httplib2,
hf-xet, boto3, botocore, tzdata, fonttools, pydantic-settings).

Le regressioni mirate locali passano 144 test, inclusi PostgreSQL/pgvector,
accounting A03, quote A04, recovery offline, guardrail, retry e tre prove di
lock cross-process con filelock, Hugging Face e Torch. Il frontend passa
472 test con tre saltati. Questi risultati Windows non certificano i lock
POSIX: suite completa, audit e build Linux sui due target restano gate CI.
La prima prova PostgreSQL locale senza estensione vector e fallita nel setup;
il successivo run usa l'immagine pgvector prevista dalla CI, senza cambiare
test o migrazioni per nascondere il problema. Nessun E2E provider reale.
