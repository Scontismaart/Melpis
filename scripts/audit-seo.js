const fs = require('fs');
const path = require('path');

const DOMAIN = 'https://melpis.it';
const { ROUTE_MAP, SITEMAP_EXCLUDED_PATHS, SUPPORTED_LANGS } = require('./build-i18n.js');
const WEB_DIR = path.join(process.cwd(), 'web');

const LOCALE_OG = {
  it: 'it_IT',
  en: 'en_GB',
  es: 'es_ES',
  fr: 'fr_FR',
  de: 'de_DE'
};

let errors = [];

function getExpectedFilePath(routeKey, lang) {
  const url = ROUTE_MAP[routeKey][lang];
  if (lang === 'it') {
    if (routeKey === 'restaurants') return path.join(WEB_DIR, 'landing', 'settori', 'ristoranti', 'index.html');
    if (routeKey === 'beauty') return path.join(WEB_DIR, 'landing', 'settori', 'saloni-bellezza', 'index.html');
    if (routeKey === 'medical') return path.join(WEB_DIR, 'landing', 'settori', 'studi-medici', 'index.html');
    if (routeKey === 'hotels') return path.join(WEB_DIR, 'landing', 'settori', 'hotel', 'index.html');
    if (routeKey === 'docs') return path.join(WEB_DIR, 'landing', 'documentazione', 'index.html');
    if (routeKey === 'login') return path.join(WEB_DIR, 'login.html');
    if (routeKey === 'register') return path.join(WEB_DIR, 'register.html');
    if (routeKey === 'privacy') return path.join(WEB_DIR, 'landing', 'privacy.html');
    if (routeKey === 'terms') return path.join(WEB_DIR, 'landing', 'termini.html');
    if (routeKey === 'cookies') return path.join(WEB_DIR, 'landing', 'cookie.html');
  } else {
    const clean = url.replace(/^\/|\/$/g, '');
    return path.join(WEB_DIR, 'landing', clean, 'index.html');
  }
}

// 1. Audit HTML files
for (const [routeKey, routes] of Object.entries(ROUTE_MAP)) {
  for (const lang of SUPPORTED_LANGS) {
    // The Nginx integration tests audit the V2 canonical, noindex and aliases.
    if (SITEMAP_EXCLUDED_PATHS.has(routes[lang])) continue;
    const filePath = getExpectedFilePath(routeKey, lang);
    if (!fs.existsSync(filePath)) {
      errors.push({ type: 'MISSING_FILE', routeKey, lang, filePath });
      continue;
    }

    const html = fs.readFileSync(filePath, 'utf8');
    const expectedUrl = `${DOMAIN}${routes[lang]}`;

    // A. Check <html lang="...">
    const langMatch = html.match(/<html[^>]*lang=["']([^"']+)["']/i);
    if (!langMatch || langMatch[1] !== lang) {
      errors.push({ type: 'WRONG_HTML_LANG', routeKey, lang, found: langMatch ? langMatch[1] : null, expected: lang });
    }

    // B. Check self-canonical
    const canonMatch = html.match(/<link\s+rel=["']canonical["']\s+href=["']([^"']+)["']/i);
    if (!canonMatch) {
      errors.push({ type: 'MISSING_CANONICAL', routeKey, lang });
    } else if (canonMatch[1] !== expectedUrl) {
      errors.push({ type: 'MISMATCH_CANONICAL', routeKey, lang, found: canonMatch[1], expected: expectedUrl });
    }

    // C. Check hreflangs
    for (const altLang of SUPPORTED_LANGS) {
      const altUrl = `${DOMAIN}${routes[altLang]}`;
      const hreflangRegex = new RegExp(`<link\\s+rel=["']alternate["']\\s+hreflang=["']${altLang}["']\\s+href=["']${altUrl.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}["']`, 'i');
      if (!hreflangRegex.test(html)) {
        errors.push({ type: 'MISSING_HREFLANG', routeKey, lang, altLang, expectedUrl: altUrl });
      }
    }

    // D. Check x-default
    const expectedXDefault = `${DOMAIN}${routes.en || routes.it}`;
    const xDefaultRegex = new RegExp(`<link\\s+rel=["']alternate["']\\s+hreflang=["']x-default["']\\s+href=["']${expectedXDefault.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}["']`, 'i');
    if (!xDefaultRegex.test(html)) {
      errors.push({ type: 'MISSING_X_DEFAULT', routeKey, lang, expectedUrl: expectedXDefault });
    }

    // E. Check og:locale
    const ogLocaleMatch = html.match(/<meta\s+property=["']og:locale["']\s+content=["']([^"']+)["']/i);
    if (ogLocaleMatch && ogLocaleMatch[1] !== LOCALE_OG[lang]) {
      errors.push({ type: 'WRONG_OG_LOCALE', routeKey, lang, found: ogLocaleMatch[1], expected: LOCALE_OG[lang] });
    }

    // F. Check title
    const titleMatch = html.match(/<title>(.*?)<\/title>/i);
    if (!titleMatch || !titleMatch[1].trim()) {
      errors.push({ type: 'MISSING_TITLE', routeKey, lang });
    }
  }
}

// 2. Audit Sitemap.xml
const sitemapPath = path.join(WEB_DIR, 'landing', 'sitemap.xml');
if (!fs.existsSync(sitemapPath)) {
  errors.push({ type: 'MISSING_SITEMAP', path: sitemapPath });
} else {
  const sitemapXml = fs.readFileSync(sitemapPath, 'utf8');
  for (const [routeKey, routes] of Object.entries(ROUTE_MAP)) {
    for (const lang of SUPPORTED_LANGS) {
      const url = `${DOMAIN}${routes[lang]}`;
      const inSitemap = sitemapXml.includes(`<loc>${url}</loc>`);
      if (inSitemap && SITEMAP_EXCLUDED_PATHS.has(routes[lang])) {
        errors.push({ type: 'SITEMAP_UNEXPECTED_LOC', url });
      } else if (!inSitemap && !SITEMAP_EXCLUDED_PATHS.has(routes[lang])) {
        errors.push({ type: 'SITEMAP_MISSING_LOC', url });
      }
    }
  }
}

console.log('--- SEO Audit Report ---');
console.log(`Total errors found: ${errors.length}`);
if (errors.length > 0) {
  console.log(JSON.stringify(errors.slice(0, 30), null, 2));
} else {
  console.log('SEO audit passed for retained public pages and sitemap entries.');
}
