import { readFile, readdir, stat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const dist = path.join(root, 'dist');
const html = await readFile(path.join(dist, 'index.html'), 'utf8');
const css = await readFile(path.join(dist, 'styles.css'), 'utf8');
const js = await readFile(path.join(dist, 'app.js'), 'utf8');
const fail = (message) => { console.error(`CHECK FAILED: ${message}`); process.exitCode = 1; };
const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map((match) => match[1]);
const idSet = new Set(ids);
if (ids.some((id) => !id.startsWith('mv2-'))) fail('Tutti gli id devono avere il prefisso mv2-.');
if (new Set(ids).size !== ids.length) fail('Trovati id duplicati.');
for (const [, href] of html.matchAll(/\bhref="#([^"]+)"/g)) if (!idSet.has(href)) fail(`Ancora senza destinazione: #${href}`);
if (!/<html lang="it" class="mv2-document">/.test(html)) fail('Lingua o namespace documento assente.');
if (!/<meta name="robots" content="noindex, nofollow, noarchive">/.test(html)) fail('Meta robots richiesto assente.');
if (/https?:\/\/|<form\b|\b(fetch|XMLHttpRequest|WebSocket)\b/i.test(`${html}\n${js}`)) fail('La pagina deve restare isolata, senza form o richieste di rete.');
if (/\b(?:testimonial|clienti serviti|% in più|aumento del|garantit[oa])\b/i.test(html)) fail('Rimuovere claim commerciali non verificati.');
for (const [, classes] of html.matchAll(/\bclass="([^"]+)"/g)) {
  if (classes.split(/\s+/).some((name) => !name.startsWith('mv2-'))) fail(`Classe senza prefisso mv2-: ${classes}`);
}
if (/--(?!mv2-)[a-z]/i.test(css)) fail('Variabile CSS senza prefisso mv2-.');
const entries = await readdir(dist, { withFileTypes: true });
for (const entry of entries) if (!['index.html', 'styles.css', 'app.js', 'assets', 'robots.txt'].includes(entry.name)) fail(`File fuori allowlist in dist: ${entry.name}`);
if (!(await readdir(path.join(dist, 'assets'))).includes('favicon.svg')) fail('Favicon V2 assente.');
const assets = await readdir(path.join(dist, 'assets', 'fonts'));
const expected = ['mv2-inter-latin.woff2', 'mv2-outfit-latin.woff2', 'mv2-outfit-latin-ext.woff2'];
if (expected.some((name) => !assets.includes(name))) fail('Font V2 allowlist incompleta.');
for (const asset of expected) if ((await stat(path.join(dist, 'assets', 'fonts', asset))).size < 1000) fail(`Font vuoto o troncato: ${asset}`);
const licenses = await readdir(path.join(dist, 'assets', 'fonts', 'licenses'));
if (!['Inter-OFL.txt', 'Outfit-OFL.txt'].every((name) => licenses.includes(name))) fail('Licenze font assenti dalla build.');
if (!/Disallow: \/\s/.test(await readFile(path.join(dist, 'robots.txt'), 'utf8'))) fail('Robots disallow all assente.');
if (!js.includes("event.key === 'Escape'") || !js.includes('mv2Toggle.focus()') || !js.includes("'(max-width: 680px)'")) fail('Interazioni menu incomplete.');
if (!html.includes('Esempio dimostrativo con dati sintetici')) fail('Etichetta demo sintetica assente.');
if (!html.includes('Listino mensile di riferimento')) fail('Qualifica del listino mensile assente.');
if (process.exitCode) process.exit(process.exitCode);
console.log('Controlli statici V2 superati.');
