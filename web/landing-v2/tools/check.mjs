import { access, readFile, stat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const dist = path.join(root, 'dist');
const html = await readFile(path.join(dist, 'index.html'), 'utf8');
const fail = (message) => { console.error(`CHECK FAILED: ${message}`); process.exitCode = 1; };
const must = (condition, message) => { if (!condition) fail(message); };
const policyTag = html.match(/<meta\b[^>]*http-equiv=["']content-security-policy["'][^>]*>/i)?.[0] ?? '';
const policy = policyTag.match(/\bcontent=(['"])(.*?)\1/i)?.[2]?.replaceAll('&amp;', '&').replaceAll('&quot;', '"').replaceAll('&#39;', "'") ?? '';
const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(m => m[1]);
const idSet = new Set(ids);
must(ids.every(id => id.startsWith('mv2-')), 'Every document id must keep the V2 prefix.');
must(new Set(ids).size === ids.length, 'Document ids must be unique.');
for (const [, href] of html.matchAll(/\bhref="#([^"]+)"/g)) must(idSet.has(href), `Missing anchor target #${href}.`);
must(/<html lang="it" class="mv2-document">/.test(html), 'Document language and namespace missing.');
must(/<meta name="robots" content="noindex, nofollow, noarchive"/.test(html), 'Noindex metadata missing.');
must(/default-src 'self'/.test(policy) && /connect-src 'none'/.test(policy) && /form-action 'none'/.test(policy), 'Restrictive Astro CSP directives missing.');
must(/script-src[^;]*'sha256-[A-Za-z0-9+/=]+'/.test(policy), 'Astro CSP does not contain a generated script hash.');
must(!/unsafe-(?:inline|eval)|strict-dynamic/.test(policy), 'CSP contains a prohibited relaxation.');
must(!/<form\b|https?:\/\//i.test(html), 'Static page must not add forms or external URLs.');
must(/client="load"/.test(html) && /Anteprima interattiva del pannello Melpis/.test(html), 'Hero React SSR island is missing.');
const productNav = html.match(/<div class="mv2-ps-nav-list"[\s\S]*?<\/div><\/div>/)?.[0] ?? '';
for (const label of ['Panoramica','Inbox','Prenotazioni','Recensioni','Knowledge Base','Team']) must(productNav.includes(label), `SSR product navigation is missing ${label}.`);
must(/disabled="" aria-pressed="true"/.test(productNav) && /Sezione selezionata:[\s\S]*?Panoramica/.test(html), 'SSR selected navigation or no-JavaScript state is missing.');
must(/Anteprima dimostrativa · dati sintetici/.test(html), 'Synthetic example disclosure missing.');
must(/Ogni conversazione\.<br\s*\/?><em>Sotto controllo\.<\/em>/.test(html), 'Phase 2 hero headline is missing.');
must(/Listino mensile di riferimento/.test(html), 'Monthly reference price qualifier missing.');
must(!/\/landing-v2\/(?:styles\.css|app\.js)/.test(html), 'Legacy direct CSS or JS resource remains.');
const sourceFiles = ['src/styles/landing.css','src/components/ProductShowcase.jsx','src/components/Journey.astro','src/components/Hero.astro'];
for (const file of sourceFiles) {
  const source = await readFile(path.join(root,file),'utf8');
  must(!/10b981|34d399|16,\s*185,\s*129|52,\s*211,\s*153|emerald/i.test(source), `Emerald palette reference remains in ${file}.`);
  must(!/class(?:Name)?="[^"]*(?:^|\s)ps-[a-z-]+/.test(source), `Unnamespaced showcase styles remain in ${file}.`);
}
for (const match of html.matchAll(/(?:src|href)="([^"]+)"/g)) {
  const value = match[1];
  if (value.startsWith('/')) must(value.startsWith('/landing-v2/'), `Asset escapes base path: ${value}`);
}
const entries = await (await import('node:fs/promises')).readdir(dist, { withFileTypes: true });
must(entries.every(e => ['index.html','robots.txt','assets','_astro'].includes(e.name)), 'Unexpected file in isolated output.');
must(/Disallow: \/\s/.test(await readFile(path.join(dist, 'robots.txt'), 'utf8')), 'Robots must disallow indexing.');
const expectedFonts = ['mv2-inter-latin.woff2','mv2-outfit-latin.woff2','mv2-outfit-latin-ext.woff2'];
for (const name of expectedFonts) { const font = path.join(root,'public','assets','fonts',name); await access(font); must((await stat(font)).size > 1000, `Font is missing or too small: ${name}`); await access(path.join(dist,'assets','fonts',name)); }
for (const name of ['Inter-OFL.txt','Outfit-OFL.txt']) await access(path.join(dist,'assets','fonts','licenses',name));
for (const file of ['assets/favicon.svg','assets/mv2-symbol.svg']) must((await readFile(path.join(root,'public',file))).equals(await readFile(path.join(root,'../landing/brand/melpis-symbol-dark.svg'))), `Brand asset differs from canonical logo: ${file}`);
if (process.exitCode) process.exit(process.exitCode);
console.log('Astro output, React SSR, base path, CSP, noindex, brand assets and static content checks passed.');
