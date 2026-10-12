# Asset provenance — Landing V2 Fase 1

Verifica 12 ottobre 2026. Baseline origin/main `2994fae7ea54094482a40374ea46c08aae8d0407`. Nessuna generazione immagini necessaria in Fase 1. Font V1 letti soltanto. Copie V2 presenti: tutti tre SHA256 byte-identici alle sorgenti, con i nomi della tabella. Anche le due licenze source/dist hanno hash identici; Outfit-OFL recuperata dalla preview con HTTP 200.

## Font locali

| Sorgente baseline | Nome V2 verificato | SHA256 sorgente |
|---|---|---|
| web/landing/fonts/outfit-latin.woff2 | assets/fonts/mv2-outfit-latin.woff2 | 6C18D579FD87C3776BE068B762CBC83FDE3ACB543D49EABD3ADE842EB987E887 |
| web/landing/fonts/outfit-latin-ext.woff2 | assets/fonts/mv2-outfit-latin-ext.woff2 | 0F53D1C03B3918D744A843B5039001EE31695CA1E255E3914188DF81BEB461E9 |
| web/landing/fonts/inter-latin.woff2 | assets/fonts/mv2-inter-latin.woff2 | 3100E775E8616CD2611BEECFA23A4263D7037586789B43F035236A2E6FBD4C62 |

Rename del file e namespace CSS non implicano modifica del Font Software; nessun subsetting/conversione/modifica binaria richiesto. Famiglie Outfit/Inter e range pesi provengono dalle dichiarazioni del baseline `web/landing/style.css:9–34`; non sono stati indipendentemente estratti i name tables WOFF2 in questa revisione. Gli hash dimostrano identità con baseline quando corrispondono, non identificazione di una release upstream precisa. Nessun font scaricato da un competitor, nessuna richiesta Google Fonts nel browser; font-display swap e fallback locali.

## Licenze upstream conservate

Testi completi scaricati dagli URL ufficiali Google Fonts; entrambi SIL Open Font License 1.1. Conservare copyright e licenza insieme alla distribuzione dei font, non soltanto nel repository sviluppo. L'allowlist build deve includere assets/fonts/licenses/*.txt.

| File locale | Fonte ufficiale | Copyright upstream | SHA256 testo acquisito |
|---|---|---|---|
| assets/fonts/licenses/Outfit-OFL.txt | https://raw.githubusercontent.com/google/fonts/main/ofl/outfit/OFL.txt | 2021 The Outfit Project Authors (Outfitio/Outfit-Fonts) | C676351BF8576B9ABA743CD5EAA8C0E7EE0D51F805D720447B4DF4DDB6A2E416 |
| assets/fonts/licenses/Inter-OFL.txt | https://raw.githubusercontent.com/google/fonts/main/ofl/inter/OFL.txt | 2020 The Inter Project Authors (rsms/inter) | 5B9321A4298CFEB6B34354164A1C3AFC3DB114569984C502B9B35D988FD58C57 |

[Outfit licenza ufficiale](https://github.com/google/fonts/blob/main/ofl/outfit/OFL.txt), [Inter licenza ufficiale](https://github.com/google/fonts/blob/main/ofl/inter/OFL.txt). Scaricati il 12 ottobre 2026; URL main mutabile, hash locale rende identificabile il testo effettivamente conservato. Autorizzano embedding e ridistribuzione con condizioni della licenza; non vendere font separatamente, non attribuire endorsement degli autori. Questa nota non sostituisce il testo completo.

## Altri asset

- Immagini generate: NONE. Nessun tool generativo chiamato in questa fase.
- Screenshot competitor: solo pacchetto ricerca precedente, fuori bundle e allowlist V2; nessuna immagine/identità copiata nella landing.
- Fotografie, video, audio, shader, canvas, texture di terzi: NONE.
- SVG correnti dopo revisione brand: `assets/mv2-symbol.svg` e `assets/favicon.svg` sono copie del simbolo autentico `web/landing/brand/melpis-symbol-dark.svg`. Geometria, viewBox 200×144, tre path e gradienti preservati; SHA256 locale di entrambi uguale all'originale: 714F9EB037919E51681BF6AC094140C0591B851E9F84C5E21E4DB377758B5BDE. La favicon manuale a lettera M della prima bozza è stata sostituita. Header/footer caricano l'asset copiato nella V2, senza dipendenza runtime dalla V1. Il test confronta i testi SVG normalizzando soltanto i line endings di checkout; non altera geometria o colori. Nessun script/external href/foreignObject, nessun tool generativo chiamato. Asset Melpis preesistente fornito come riferimento dall'utente; nessun asset concorrente o nuovo marchio.
- Logo/wordmark V2: composizione testuale Melpis originale; nessun marchio cliente/partner/certificazione implicito.
- Icone correnti: CSS/simboli Unicode, senza librerie/font icon/asset remote. Simboli decorativi con aria-hidden nel markup pertinente.

## Checklist di verifica, responsabilità UI/QA

1. VERIFICATO: font V2 ai nomi della tabella, tutti SHA256 uguali al baseline.
2. Nessun URL browser al font/percorso V1: tutti asset serviti da `/landing-v2/`.
3. Licenze presenti nel dist vicino ai font e recuperabili dall'allowlist asset.
4. Nessun benchmark o file docs/reference nel dist.
5. Network same-origin soltanto; CSP font-src self, connect-src none.
6. Hash sorgenti V1 rimangono invariati. Nessun commit/deploy effettuato dal revisore.

