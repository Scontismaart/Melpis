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
