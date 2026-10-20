# Runtime CrewAI/MCP con derivata interna verificata

## Stato su Melpis main - 17 ottobre 2026

La correzione e stata integrata tramite PR #105 nel commit `7988f32f419019c650d77ebe9fe4a16366f5c374`. La CI post-merge [39022106060](https://github.com/Scontismaart/Melpis/actions/runs/39022106060) e verde: 2.744 test passati, 33 test live opt-in esclusi, audit OSV di 204 dipendenze senza pacchetti saltati e build API/web amd64/arm64 riuscite. Le immagini SHA del commit sono state lette direttamente da GHCR; i manifest riportano entrambe le architetture e i digest di runtime corrispondono ai tag specifici per architettura.

Questo certifica il commit e la pubblicazione immagini, non un deploy o un'approvazione upstream della derivata. Nessun deploy dei servizi e stato eseguito; il fondatore ha confermato che non esistono host applicativi esterni. I 33 test live restano esclusi e da certificare prima della beta.


La correzione e integrata in main tramite PR #105. Questo aggiornamento documenta lo stato verificato della build; non autorizza deploy o approvazione upstream della derivata. L'upstream CrewAI 1.15.4 richiede `mcp~=1.28.1`, mentre la
correzione di sicurezza richiede MCP 1.30.0. La derivata interna rilassa soltanto
quel vincolo a `mcp>=1.30.0,<1.31`, mantenendo `mcp==1.30.0` nel contratto Melpis.
Non implica approvazione o firma upstream sul nuovo abbinamento.

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
| Derivata interna canonica | `74baca11ee420f0cde6c76c421092a78174c963b4fa646830df33be52d053455` |
| Manifest di provenienza | `11537e7bd6d0f3dd12cf26beb6e39db7503ff1f44e61a261c60cc8e0b4645def` |
| pip 26.2.1 ufficiale | `71138adf1f4ca900cdb7d289c21b7494329f2332b6d85f0e1c42108c0384ed3e` |

Fonti primarie: [CrewAI 1.15.4 su PyPI](https://pypi.org/pypi/crewai/1.15.4/json),
[pip 26.2.1 su PyPI](https://pypi.org/pypi/pip/26.2.1/json) e
[wheel ufficiale pip](https://files.pythonhosted.org/packages/f3/6e/1736e5b4ae2b778ef2f81c47d797de9f891d4d8acb047a24ca37a60294dd/pip-26.2.1-py3-none-any.whl).

Cambiano soltanto il vincolo in METADATA e RECORD rigenerato. I restanti 526
file, inclusi codice, versione e licenza MIT, restano identici byte per byte.
ZIP_STORED, ordine dei file e metadati ZIP fissi evitano dipendenze da zlib e
timestamp; il digest è stato riprodotto su Windows e Linux con Python 3.12.
Il manifest verificato include gli hash dei 527 file installabili escluso il
RECORD che pip riscrive. Il runtime deve avere esattamente CrewAI 1.15.4 e MCP
1.30.0; ogni file atteso viene ricontrollato, senza importare CrewAI o provider.

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
l'inventario installato prima e dopo l'audit. Nessun pacchetto omesso, aggiunto,
duplicato o saltato è ammesso; `torch==2.14.0+cpu` non è normalizzato a una
versione diversa. Controlla poi il manifest e la policy esistente senza nuove
eccezioni. Il tool audit resta fuori dall'inventario applicativo.

Il servizio PyPI di pip-audit saltava il suffisso `+cpu`; il primo report di
laboratorio è conservato come prova del limite. Un secondo audit OSV completo
ha riportato 204 distribuzioni, incluso Torch con versione esatta, senza skip.
Gli advisory residui Chroma/json-repair sono gli stessi già presenti nella
policy; gli alias non vengono riscritti per superarla. Report e inventario
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
