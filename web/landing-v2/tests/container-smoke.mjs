import assert from 'node:assert/strict';

const origin = new URL(process.env.QA_ORIGIN || 'http://127.0.0.1:4180');
assert.ok(['127.0.0.1', 'localhost', '[::1]'].includes(origin.hostname), 'Container QA requires loopback');

const get = (route, options) => fetch(new URL(route, origin), { redirect: 'manual', ...options });
const v2Pages = ['/', '/piani/', ...['ristoranti', 'hotel', 'barbieri', 'saloni'].map(sector => `/landing-v2/${sector}/`)];
const legacyPages = [
  '/accedi/', '/registrati/', '/documentazione/', '/documentazione/account-e-piani/piani-e-fatturazione/',
  '/settori/ristoranti/', '/settori/hotel/', '/settori/saloni-bellezza/', '/settori/studi-medici/',
  '/en/', '/en/login/', '/en/signup/', '/en/pricing/', '/en/documentation/', '/en/industries/restaurants/',
  '/es/', '/es/iniciar-sesion/', '/es/registro/', '/es/precios/', '/es/documentacion/',
  '/fr/', '/fr/connexion/', '/fr/inscription/', '/fr/tarifs/', '/fr/documentation/',
  '/de/', '/de/anmelden/', '/de/registrieren/', '/de/preise/', '/de/dokumentation/',
];
const legalPages = ['/privacy/', '/termini/', '/cookie/', '/en/privacy/', '/en/terms/', '/en/cookies/',
  '/es/privacidad/', '/es/terminos/', '/es/cookies/', '/fr/confidentialite/', '/fr/conditions/', '/fr/cookies/',
  '/de/datenschutz/', '/de/agb/', '/de/cookies/'];
const assets = new Set();
const pages = new Map();
for (const route of [...v2Pages, ...legacyPages, ...legalPages]) {
  const response = await get(route);
  assert.equal(response.status, 200, route);
  assert.match(response.headers.get('content-type') ?? '', /^text\/html/, route);
  const html = await response.text();
  if (legalPages.includes(route)) {
    assert.doesNotMatch(html, /\{\{LEGAL_/, route);
    if (route === '/privacy/') assert.ok(html.includes('Melpis Synthetic QA'), route);
  }
  if (!v2Pages.includes(route)) continue;
  pages.set(route, html);
  assert.equal(response.headers.get('x-robots-tag'), 'noindex, nofollow, noarchive', route);
  const policy = response.headers.get('content-security-policy') ?? '';
  assert.match(policy, /script-src 'self' 'sha256-/);
  assert.match(policy, /connect-src 'none'/);
  assert.match(policy, /frame-ancestors 'none'/);
  assert.match(policy, /form-action 'none'/);
  assert.doesNotMatch(policy, /unsafe-/);
  assert.ok(html.includes(`rel="canonical" href="https://melpis.it${route}"`), route);
  assert.doesNotMatch(html, /<(?:form|iframe)\b|(?:src|action)="https?:/);
  for (const [, url] of html.matchAll(/(?:src|href)="(\/landing-v2\/(?:_astro|assets|images)\/[^" ]+)"/g)) assets.add(url);
}
assert.match(pages.get('/'), /Meno tempo al telefono\./);
assert.match(pages.get('/piani/'), /Confronta le funzionalità dei piani/);
for (const [route, html] of pages) {
  for (const [, value] of html.matchAll(/<a\b[^>]*\bhref="([^"]+)"/g)) {
    if (value.startsWith('mailto:')) continue;
    const link = new URL(value.replaceAll('&amp;', '&'), new URL(route, origin));
    assert.equal(link.origin, origin.origin, `${route}: ${value}`);
    if (link.hash && pages.has(link.pathname)) {
      assert.ok(pages.get(link.pathname).includes(`id="${decodeURIComponent(link.hash.slice(1))}"`), `${route}: ${value}`);
    } else if (!pages.has(link.pathname) && !legacyPages.includes(link.pathname) && !legalPages.includes(link.pathname)) {
      assert.equal((await get(link.pathname)).status, 200, `${route}: ${value}`);
    }
  }
}
for (const url of assets) assert.equal((await get(url, { method: 'HEAD' })).status, 200, url);
for (const name of ['Geist-LICENSE.txt', 'Inter-OFL.txt', 'Newsreader-OFL.txt', 'Outfit-OFL.txt']) {
  const response = await get(`/landing-v2/assets/fonts/licenses/${name}`);
  assert.equal(response.status, 200);
  assert.match(await response.text(), /SIL OPEN FONT LICENSE/i);
}
for (const [src, target] of [
  ['/prezzi', '/piani/'], ['/prezzi/', '/piani/'], ['/prezzi/index.html', '/piani/'],
  ['/landing-v2', '/'], ['/landing-v2/', '/'], ['/index.html', '/'], ['/piani', '/piani/'],
  ['/landing-v2/piani/', '/piani/'], ['/landing-v2/login/', '/accedi/'], ['/landing-v2/registrazione/', '/registrati/'],
]) {
  const response = await get(`${src}?ref=qa`);
  assert.equal(response.status, 301, src);
  assert.equal(response.headers.get('location'), `${target}?ref=qa`, src);
}
for (const route of ['/piani.html', '/privacy.html', '/en/privacy/index.html', '/landing-v2/src/pages/index.astro',
  '/landing-v2/piani/index.html', '/landing-v2/package-lock.json', '/landing-v2/images/dashboard-qa/tab-1440-inbox.png']) {
  assert.equal((await get(route)).status, 404, route);
}
const guest = await get('/app/impostazioni', { headers: { cookie: 'melpis_lang=en' } });
assert.equal(guest.status, 302);
assert.equal(guest.headers.get('location'), '/en/login/?next=/app/impostazioni#next=/app/impostazioni');
const shell = await get('/app/impostazioni', { headers: { cookie: 'wa_at=synthetic-shell-routing-only' } });
assert.equal(shell.status, 200);
assert.match(shell.headers.get('content-security-policy') ?? '', /script-src 'self';/);
assert.doesNotMatch(shell.headers.get('content-security-policy') ?? '', /sha256-/);
assert.match(await shell.text(), /<script[^>]*src="(?:\/app\/)?app\.js/);
const robots = await get('/robots.txt');
assert.equal(robots.status, 200);
assert.match(await robots.text(), /Disallow: \/app\//);
const sitemap = await get('/sitemap.xml');
assert.equal(sitemap.status, 200);
const xml = await sitemap.text();
assert.doesNotMatch(xml, /<loc>https:\/\/melpis\.it\/(?:piani\/|prezzi\/|landing-v2\/[^<]*)?<\/loc>/);
assert.match(xml, /<loc>https:\/\/melpis\.it\/en\/pricing\/<\/loc>/);
console.log(`Nginx smoke passed: ${pages.size} V2 pages, ${legacyPages.length} legacy routes, ${legalPages.length} rendered legal pages, ${assets.size} assets, aliases, dashboard and SEO.`);
