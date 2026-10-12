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
must(/client="visible"/.test(html) && /aria-label="Simulazione del passaggio al team"/.test(html), 'React SSR island or its accessible group is missing.');
const controlGroup = html.match(/<div class="mv2-control-options"[\s\S]*?<\/div>/)?.[0] ?? '';
must(/aria-pressed="false"[^>]*disabled=""/.test(controlGroup) && /aria-pressed="true"[^>]*disabled=""/.test(controlGroup) && /Passaggio visibile e gestito dal pannello/.test(html), 'React island SSR fallback state is missing.');
must(/Esempio dimostrativo con dati sintetici/.test(html), 'Synthetic example disclosure missing.');
must(/Listino mensile di riferimento/.test(html), 'Monthly reference price qualifier missing.');
must(!/\/landing-v2\/(?:styles\.css|app\.js)/.test(html), 'Legacy direct CSS or JS resource remains.');
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
