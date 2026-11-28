import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const node = process.execPath;

test('V2 symbol and favicon preserve the canonical Melpis SVG', async () => {
  const canonical = await readFile(path.resolve(root, '../landing/brand/melpis-symbol-dark.svg'), 'utf8');
  const normalize = (svg) => svg.replace(/\r\n/g, '\n');
  for (const file of ['public/assets/mv2-symbol.svg', 'public/assets/favicon.svg']) {
    const svg = await readFile(path.join(root, file), 'utf8');
    assert.equal(normalize(svg), normalize(canonical));
    assert.match(svg, /viewBox="0 0 200 144"/);
    assert.equal([...svg.matchAll(/<path\b/g)].length, 3);
  }
});

test('build allowlist is self-contained and preview is isolated', async (t) => {
  const html = await readFile(path.join(root, 'dist/index.html'), 'utf8');
  assert.doesNotMatch(html, /id="mv2-contenuto">\s*---/, 'page does not leak an Astro frontmatter delimiter into main');
  assert.match(html, /href="#mv2-percorso"/);
  assert.match(html, /id="mv2-piani"/);
  assert.match(html, /Meno tempo al telefono\./);
  assert.match(html, /Più tempo per la tua attività\./);
  assert.match(html, /Configura una volta\./);
  assert.match(html, /Tutto sotto controllo,/);
  assert.match(html, /client="load"/);
  assert.match(html, /client="visible"/);
  assert.match(html, /Tu invii un solo messaggio\./);
  assert.match(html, /Demo interattiva · Dati simulati/);
  assert.match(html, /Orario occupato/);
  assert.match(html, /Allergeni e menu/);
  assert.match(html, /Anteprima dimostrativa a scopo illustrativo/);
  for (const id of ['mv2-flow-shell', 'mv2-setup', 'mv2-faq', 'mv2-contatto']) assert.match(html, new RegExp(`id="${id}"`));
  for (const sector of ['Ristoranti', 'Hotel', 'Barbieri', 'Centri estetici']) assert.match(html, new RegExp(`>${sector}<`), `SSR sectors include ${sector}`);
  for (const label of ['Panoramica','Inbox','Prenotazioni','Recensioni']) assert.match(html, new RegExp(`>${label}<`), `SSR product navigation includes ${label}`);
  assert.match(html, /Team &amp; Collaboratori|Team & Collaboratori/, 'SSR product navigation includes Team & Collaboratori');
  assert.match(html, /disabled="" aria-pressed="true"/);
  assert.match(html, /content="noindex, nofollow, noarchive"/);

  assert.match(html, /L’assistente aiuta\./);
  assert.match(html, /Il team decide\./);
  assert.match(html, /Richiesta da verificare/);
  assert.match(html, /Team in conversazione/);
  assert.match(html, /Approvazione manuale richiesta/);
  assert.match(html, /Prenotazione #24891/);
  assert.match(html, /Venerdì · 20:30 · 2 persone/);
  assert.match(html, /attività\.demo@melpis\.it/);
  assert.match(html, /Luca · Operatore/);
  assert.match(html, /Configurazione AI/);
  assert.match(html, /Esempio illustrativo/);
  assert.match(html, /mv2-control-canvas/);
  assert.match(html, /mv2-control-aurora-layer/);
  assert.equal([...html.matchAll(/mv2-control-card-inner/g)].length, 3);
  assert.doesNotMatch(html, /class="mv2-control-panel/);
  assert.doesNotMatch(html, /mv2-control-window|mv2-window-dark/);

  assert.match(html, /id="mv2-capacita"/);
  assert.match(html, /id="mv2-capabilities-title"/);
  assert.match(html, /Rispondi 24\/7,/);
  assert.match(html, /Il team interviene solo/);
  assert.match(html, /Base di conoscenza/);
  assert.match(html, /Dati struttura/);
  assert.match(html, /Politiche di cancellazione/);
  assert.match(html, /Regole sulle prenotazioni/);
  assert.match(html, /class="mv2-capabilities-future" aria-hidden="true"><\/div>/);
  assert.equal([...html.matchAll(/mv2-capabilities-future/g)].length, 1);
  assert.doesNotMatch(html, /<canvas[^>]*mv2-capabilities/);
  assert.match(html, /<div class="mv2-theme-transition" data-mv2-theme-transition>/);
  assert.doesNotMatch(html, /mv2-capabilities-curtain|mv2-preloader/);
  assert.doesNotMatch(html, /mv2-capabilities-film|mv2-cap-chart-gradient/);
  assert.match(html, /class="mv2-cap-chart-line" pathLength="1"/);

  // Section 05 (pricing): pearl surface attached to the real Setup end by the silver SVG curve.
  const pricingMarker = html.indexOf('mv2-pricing-section');
  const curveMarker = html.indexOf('data-mv2-pricing-curve');
  assert.ok(pricingMarker > 0 && curveMarker > 0 && curveMarker < pricingMarker, 'pricing curve and section are rendered in order');
  const pricingHtml = html.slice(html.lastIndexOf('<div', curveMarker), html.indexOf('<section', pricingMarker));
  assert.match(pricingHtml, /id="mv2-piani"/);
  assert.match(pricingHtml, /id="mv2-pricing-heading"/);
  assert.match(pricingHtml, /aria-labelledby="mv2-pricing-heading"/);
  assert.match(pricingHtml, /<p class="mv2-pricing-eyebrow">[\s\S]*?UN RIFERIMENTO SEMPLICE[\s\S]*?<\/p>/);
  assert.doesNotMatch(pricingHtml, /<p class="mv2-pricing-eyebrow">[\s\S]*?05[\s\S]*?<\/p>/);
  assert.match(pricingHtml, /Scegli il passo[\s\S]*?adatto a te\./);
  assert.match(pricingHtml, /Tre piani a pagamento con prezzi mensili\./);
  assert.match(pricingHtml, /Costi di abbonamento mensili\. Nessuna attivazione o acquisto da questa pagina\./);
  assert.equal([...pricingHtml.matchAll(/class="mv2-pricing-card(?:"|\s)/g)].length, 4);
  assert.equal([...pricingHtml.matchAll(/data-card-index="[0-3]"/g)].length, 4);
  assert.equal([...pricingHtml.matchAll(/>\s*Prova Melpis\s*</g)].length, 4);
  assert.equal([...pricingHtml.matchAll(/class="mv2-pricing-feature-check"/g)].length, 20, 'every plan feature uses the graphite check icon');
  assert.doesNotMatch(pricingHtml, /mv2-pricing-feature-dot/, 'legacy feature dots are fully removed');
  const planNames = ['Prova gratuita', 'Essenziale', 'Crescita', 'Scala'];
  const planPositions = planNames.map(name => pricingHtml.indexOf(`>${name}<`));
  assert.ok(planPositions.every(position => position > 0), 'all four pricing cards are present');
  assert.deepEqual([...planPositions].sort((a, b) => a - b), planPositions, 'Trial, Essenziale, Crescita and Scala render in order');
  for (const [name, amount] of [['Prova gratuita', '0'], ['Essenziale', '29'], ['Crescita', '69'], ['Scala', '149']]) {
    assert.match(pricingHtml, new RegExp(`>${name}<`), `pricing card ${name} is present`);
    const priceLabel = name === 'Prova gratuita' ? '0 euro fino a 7 giorni o 150 risposte AI, al primo limite raggiunto' : `${amount} euro al mese`;
    assert.match(pricingHtml, new RegExp(`<span class="mv2-pricing-sr">${priceLabel}</span>`), `pricing card ${name} exposes its price to assistive tech`);
  }
  assert.match(pricingHtml, /Fino a 7 giorni o 150 risposte AI, al primo limite raggiunto\./);
  assert.match(pricingHtml, /<span class="mv2-pricing-period" aria-hidden="true">\/ prova<\/span>/);
  const homepageQuotas = [...pricingHtml.matchAll(/<strong class="mv2-pricing-feature-strong">([\d.]+)<\/strong> risposte AI/g)].map(match => match[1]);
  assert.deepEqual(homepageQuotas, ['150', '500', '2.000', '10.000']);

  const plansHtml = await readFile(path.join(root, 'dist/piani.html'), 'utf8');
  for (const quota of homepageQuotas) assert.ok(plansHtml.includes(quota), `Pricing page preserves quota ${quota}`);
  assert.doesNotMatch(plansHtml, /data-annual|id="btnAnnual"/);

  assert.equal([...pricingHtml.matchAll(/class="mv2-pricing-card-cta"/g)].length, 4);
  assert.equal([...pricingHtml.matchAll(/href="\/registrati\/"/g)].length, 4, 'pricing CTAs point at real registration');
  assert.doesNotMatch(pricingHtml, /href="#configura-/, 'pricing CTAs must not link to nonexistent configuration anchors');
  for (const offer of [/\bsconto\b/i, /30 giorni/i, /risposte AI illimitate/i]) {
    assert.doesNotMatch(pricingHtml, offer, `pricing section must not invent an offer: ${offer}`);
  }
  assert.equal([...pricingHtml.matchAll(/data-mv2-pricing-curve(?=[\s>])/g)].length, 1);
  assert.match(pricingHtml, /data-mv2-pricing-curve-motion/);
  assert.match(pricingHtml, /viewBox="0 0 1440 215"/);
  assert.match(pricingHtml, /preserveAspectRatio="none"/);
  for (const id of ['rim-ambient-halo', 'rim-main-line', 'rim-catchlight-bloom', 'rim-catchlight-core', 'lip-drop-shadow', 'pearl-main-surface', 'left-wing-surface', 'left-wing-crease']) {
    assert.match(pricingHtml, new RegExp(`id="mv2-pricing-${id}"`), `curve keeps the Argon ${id} layer`);
  }
  assert.equal([...pricingHtml.matchAll(/data-mv2-pricing-sheen/g)].length, 1, 'only the featured card carries the sheen layer');
  const pricingIds = [...pricingHtml.matchAll(/id="(mv2-pricing-[^"]+)"/g)].map(match => match[1]);
  assert.ok(pricingIds.length >= 12, 'curve ids are namespaced with the mv2-pricing prefix');
  assert.equal(new Set(pricingIds).size, pricingIds.length, 'pricing and curve ids are unique');

  assert.match(html, /id="mv2-contatto"[\s\S]*?aria-labelledby="mv2-final-cta-title"/);
  assert.match(html, /class="mv2-final-cta-title"[\s\S]*?Porta Melpis[\s\S]*?nella tua attività\./);
  assert.equal([...html.matchAll(/class="mv2-final-cta-button"/g)].length, 1, 'the closing section carries one CTA only');
  assert.match(html, /class="mv2-final-cta-button"[\s\S]*?href="\/registrati\/"|href="\/registrati\/"[\s\S]*?class="mv2-final-cta-button"/);
  assert.match(html, /Prova Melpis gratis/);
  assert.doesNotMatch(html, /Rivedi il percorso|mv2-final-link|UN PASSO ALLA VOLTA/, 'the previous closing layout is fully replaced');
  assert.match(html, /data-mv2-final-cta/);
  assert.match(html, /class="mv2-final-cta-curve"[\s\S]*?viewBox="0 0 1440 260"/);
  assert.match(html, /assets\/mv2-symbol\.svg/, 'footer includes the official Melpis symbol');
  assert.doesNotMatch(html, /L'assistente AI per WhatsApp e Instagram/, 'footer brand description is removed in minimal layout');
  assert.match(html, /© 2026 Melpis\. Tutti i diritti riservati\./, 'footer legal copyright line is updated');
  for (const col of ['Prodotto', 'Risorse', 'Azienda']) assert.match(html, new RegExp(col), `footer includes column ${col}`);
  assert.doesNotMatch(html, /Resta aggiornato/, 'Resta aggiornato column is completely removed');

  const proc = spawn(node, ['tools/preview.mjs'], {
    cwd: root,
    env: { ...process.env, MV2_PORT: '0' },
    stdio: ['ignore', 'pipe', 'inherit'],
  });
  let startup = '';
  proc.stdout.setEncoding('utf8');
  for await (const chunk of proc.stdout) {
    startup += chunk;
    if (startup.includes('http://127.0.0.1:')) break;
  }
  t.after(() => proc.kill());
  const port = Number(startup.match(/:(\d+)\/landing-v2\//)?.[1]);
  assert.ok(port > 0, `preview non avviato: ${startup}`);
  const origin = `http://127.0.0.1:${port}`;
  const home = await fetch(`${origin}/landing-v2/`);
  assert.equal(home.status, 200);
  assert.match(home.headers.get('content-security-policy'), /connect-src 'none'/);
  assert.equal(home.headers.get('x-robots-tag'), 'noindex, nofollow, noarchive');
  const homeHtml = await home.text();
  assert.match(homeHtml, /Melpis/);

  const homeCssMatch = homeHtml.match(/href="\/landing-v2\/_astro\/([^"]+\.css)"/)?.[1];
  const css = await readFile(path.join(root, 'dist', '_astro', homeCssMatch || (await (await import('node:fs/promises')).readdir(path.join(root, 'dist', '_astro'))).find(f => f.endsWith('.css'))), 'utf8');
  assert.match(css, /\.mv2-capabilities\{[^}]*--mv2-cap-bg:#000[^}]*background:#000/, 'capabilities must render on a pure-black surface');
  assert.match(css, /\.mv2-theme-transition\{[^}]*--mv2-shift-bg:#fff/, 'theme transition ground variables are missing');
  assert.match(css, /\.mv2-theme-shift-active \.mv2-capabilities\.mv2-capabilities--visible\{background:(?:transparent|0 0)\}/, 'the revealed chapter must expose one uniform wrapper surface');
  assert.doesNotMatch(css, /linear-gradient\(to bottom,\s*var\(--mv2-shift-bg/, 'the capabilities leading edge must not use a spatial gradient');
  assert.match(css, /@media\s?\(prefers-reduced-motion:reduce\)\{[^@]*\.mv2-theme-transition\{background:#fff/, 'reduced motion keeps the static light ground');
  assert.doesNotMatch(css, /mv2-capabilities-curtain|mv2-preloader/, 'legacy curtain and preloader styles are gone');
  assert.doesNotMatch(css, /mv2-capabilities-film|mv2-cap-chart-gradient/, 'legacy film and gradient are gone');
  assert.doesNotMatch(css, /#34d399|16,\s*185,\s*129|52,\s*211,\s*153/i, 'runtime CSS must use the monochrome silver palette outside the scoped dashboard');
  const emerald = [...css.matchAll(/[^;{}]*#10b981[^;{}]*/gi)].map(match => match[0]);
  assert.ok(emerald.length > 0 && emerald.every(declaration => /^--mv2-dash-brand(?:-soft|-line)?:/.test(declaration)), 'emerald stays inside the scoped dashboard variables');
  const fontPath = css.match(/url\((\/landing-v2\/assets\/fonts\/[^)]+\.woff2)\)/)?.[1];
  assert.ok(fontPath, 'built CSS must reference a self-hosted font');
  const font = await fetch(`${origin}${fontPath}`);
  assert.equal(font.status, 200);
  assert.equal(font.headers.get('content-type'), 'font/woff2');
  assert.ok(Number(font.headers.get('content-length')) > 1000);

  for (const url of ['/landing-v2/../index.html', '/landing-v2/%2e%2e/index.html', '/landing-v2/%5c..%5cindex.html']) {
    const response = await fetch(`${origin}${url}`, { redirect: 'manual' });
    assert.ok([400, 403, 404].includes(response.status), `${url} ha risposto ${response.status}`);
  }
  assert.equal((await fetch(`${origin}/landing-v2/tools/build.mjs`)).status, 404);
  assert.equal((await fetch(`${origin}/`)).status, 404);
  assert.match(await (await fetch(`${origin}/robots.txt`)).text(), /Disallow: \/\s/);
  assert.equal((await fetch(`${origin}/landing-v2/`, { method: 'POST' })).status, 405);
  const scripts = await (await import('node:fs/promises')).readdir(path.join(root, 'dist', '_astro'));
  const bundledJs = await Promise.all(scripts.filter(file => file.endsWith('.js')).map(file => readFile(path.join(root, 'dist', '_astro', file), 'utf8')));
  assert.ok(bundledJs.length > 0);
  assert.ok(bundledJs.some(source => source.includes('tavolo') && source.includes('flow')), 'flow demo ships as a bundled script');
  assert.match(html, /data-mv2-control-stage/);
  assert.match(html, /is-settled/);
  assert.ok(bundledJs.some(source => source.includes('gsap')) || (await readFile(path.join(root, 'package.json'),'utf8')).includes('"gsap"'));
});
