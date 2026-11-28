#!/usr/bin/env node
/**
 * Melpis — Static i18n Site Generator
 * Builds localized sector, docs and auth pages. Reviewed legal pages and the
 * retained localized home and pricing HTML are maintained in place.
 * Injects self-canonicals, reciprocal hreflangs, and dynamic routeMap-aware language switchers.
 */

const fs = require('fs');
const path = require('path');
const { extractInlineAssets } = require('./extract-inline-assets');
const {
  SECTOR_SHARED,
  SECTOR_EXTRAS,
  DOCS_EXTRAS
} = require('./i18n-extra-copy');

const ROOT_DIR = path.resolve(__dirname, '..');
const LOCALES_DIR = path.join(ROOT_DIR, 'locales');
const WEB_DIR = path.join(ROOT_DIR, 'web');
const DOMAIN = 'https://melpis.it';

const SUPPORTED_LANGS = ['it', 'en', 'es', 'fr', 'de'];
const SITEMAP_EXCLUDED_PATHS = new Set(['/', '/prezzi/', '/piani/']);

// Central Canonical Route Map
const ROUTE_MAP = {
  home: {
    it: '/',
    en: '/en/',
    es: '/es/',
    fr: '/fr/',
    de: '/de/'
  },
  pricing: {
    it: '/prezzi/',
    en: '/en/pricing/',
    es: '/es/precios/',
    fr: '/fr/tarifs/',
    de: '/de/preise/'
  },
  restaurants: {
    it: '/settori/ristoranti/',
    en: '/en/industries/restaurants/',
    es: '/es/sectores/restaurantes/',
    fr: '/fr/secteurs/restaurants/',
    de: '/de/branchen/restaurants/'
  },
  beauty: {
    it: '/settori/saloni-bellezza/',
    en: '/en/industries/beauty-salons/',
    es: '/es/sectores/salones-belleza/',
    fr: '/fr/secteurs/salons-beaute/',
    de: '/de/branchen/kosmetik-friseure/'
  },
  medical: {
    it: '/settori/studi-medici/',
    en: '/en/industries/medical-clinics/',
    es: '/es/sectores/clinicas-medicas/',
    fr: '/fr/secteurs/cabinets-medicaux/',
    de: '/de/branchen/arztpraxen/'
  },
  hotels: {
    it: '/settori/hotel/',
    en: '/en/industries/hotels/',
    es: '/es/sectores/hoteles/',
    fr: '/fr/secteurs/hotels/',
    de: '/de/branchen/hotels/'
  },
  docs: {
    it: '/documentazione/',
    en: '/en/documentation/',
    es: '/es/documentacion/',
    fr: '/fr/documentation/',
    de: '/de/dokumentation/'
  },
  login: {
    it: '/accedi/',
    en: '/en/login/',
    es: '/es/iniciar-sesion/',
    fr: '/fr/connexion/',
    de: '/de/anmelden/'
  },
  register: {
    it: '/registrati/',
    en: '/en/signup/',
    es: '/es/registro/',
    fr: '/fr/inscription/',
    de: '/de/registrieren/'
  },
  privacy: {
    it: '/privacy/',
    en: '/en/privacy/',
    es: '/es/privacidad/',
    fr: '/fr/confidentialite/',
    de: '/de/datenschutz/'
  },
  terms: {
    it: '/termini/',
    en: '/en/terms/',
    es: '/es/terminos/',
    fr: '/fr/conditions/',
    de: '/de/agb/'
  },
  cookies: {
    it: '/cookie/',
    en: '/en/cookies/',
    es: '/es/cookies/',
    fr: '/fr/cookies/',
    de: '/de/cookies/'
  }
};

const LANG_LABELS = {
  it: { name: 'Italiano', code: 'IT', locale: 'it_IT' },
  en: { name: 'English', code: 'EN', locale: 'en_GB' },
  es: { name: 'Español', code: 'ES', locale: 'es_ES' },
  fr: { name: 'Français', code: 'FR', locale: 'fr_FR' },
  de: { name: 'Deutsch', code: 'DE', locale: 'de_DE' }
};

const PAGES_CONFIG = [
  { key: 'restaurants', sourceFile: path.join(WEB_DIR, 'landing', 'settori', 'ristoranti', 'index.html') },
  { key: 'beauty', sourceFile: path.join(WEB_DIR, 'landing', 'settori', 'saloni-bellezza', 'index.html') },
  { key: 'medical', sourceFile: path.join(WEB_DIR, 'landing', 'settori', 'studi-medici', 'index.html') },
  { key: 'hotels', sourceFile: path.join(WEB_DIR, 'landing', 'settori', 'hotel', 'index.html') },
  { key: 'docs', sourceFile: path.join(WEB_DIR, 'landing', 'documentazione', 'index.html') },
  { key: 'login', sourceFile: path.join(WEB_DIR, 'login.html') },
  { key: 'register', sourceFile: path.join(WEB_DIR, 'register.html') },
  { key: 'privacy', sourceFile: path.join(WEB_DIR, 'landing', 'privacy.html') },
  { key: 'terms', sourceFile: path.join(WEB_DIR, 'landing', 'termini.html') },
  { key: 'cookies', sourceFile: path.join(WEB_DIR, 'landing', 'cookie.html') }
];

function loadLocales(lang) {
  const dir = path.join(LOCALES_DIR, lang);
  if (!fs.existsSync(dir)) return null;
  const bundle = {};
  fs.readdirSync(dir).forEach(file => {
    if (file.endsWith('.json') && file !== 'glossary.json') {
      const ns = file.replace('.json', '');
      try {
        bundle[ns] = JSON.parse(fs.readFileSync(path.join(dir, file), 'utf8'));
      } catch (e) {
        console.error(`Error parsing ${dir}/${file}`, e);
      }
    }
  });
  return bundle;
}

function generateHreflangs(routeKey, currentLang) {
  const route = ROUTE_MAP[routeKey];
  if (!route) return '';

  const selfCanonical = `${DOMAIN}${route[currentLang]}`;
  let html = `    <link rel="canonical" href="${selfCanonical}">\n`;

  for (const lang of SUPPORTED_LANGS) {
    if (route[lang]) {
      html += `    <link rel="alternate" hreflang="${lang}" href="${DOMAIN}${route[lang]}">\n`;
    }
  }
  // x-default points to en
  const defaultUrl = route['en'] || route['it'];
  html += `    <link rel="alternate" hreflang="x-default" href="${DOMAIN}${defaultUrl}">\n`;
  return html;
}

function generateLanguageSelector(routeKey, currentLang) {
  const route = ROUTE_MAP[routeKey] || ROUTE_MAP.home;
  const current = LANG_LABELS[currentLang] || LANG_LABELS.it;

  let desktopOptions = '';
  let mobileOptions = '';

  for (const lang of SUPPORTED_LANGS) {
    const targetUrl = route[lang] || `/${lang}/`;
    const isActive = lang === currentLang;
    const label = LANG_LABELS[lang];

    desktopOptions += `                    <a href="${targetUrl}" class="lang-option-link${isActive ? ' is-active' : ''}" hreflang="${lang}" role="menuitem">${label.name}</a>\n`;
    mobileOptions += `                <a href="${targetUrl}" class="mobile-lang-chip${isActive ? ' is-active' : ''}" hreflang="${lang}">${label.name}</a>\n`;
  }

  const desktopHtml = `
            <div class="lang-selector-wrap" id="langSelectorWrap">
                <button type="button" class="lang-selector-btn" id="langDropdownTrigger" aria-haspopup="menu" aria-expanded="false" aria-label="Seleziona lingua / Select language">
                    <span class="lang-code-text">${current.code}</span>
                    <svg class="dropdown-chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>
                </button>
                <div class="lang-dropdown-menu" id="langDropdownMenu" role="menu" aria-labelledby="langDropdownTrigger" hidden>
${desktopOptions}                </div>
            </div>`;

  const mobileHtml = `
            <div class="mobile-lang-section">
                <span class="mobile-lang-title">Lingua / Language</span>
                <div class="mobile-lang-grid">
${mobileOptions}                </div>
            </div>`;

  return { desktopHtml, mobileHtml };
}

function ensureDir(dirPath) {
  if (!fs.existsSync(dirPath)) {
    fs.mkdirSync(dirPath, { recursive: true });
  }
}

function updateInternalLinks(html, currentLang) {
  let updated = html;

  // Process longer URLs first so specific routes match before general ones
  const entries = Object.entries(ROUTE_MAP).sort((a, b) => b[1].it.length - a[1].it.length);

  for (const [key, routes] of entries) {
    const itUrl = routes.it;
    const targetUrl = routes[currentLang];
    if (itUrl && targetUrl && itUrl !== targetUrl) {
      if (itUrl === '/') {
        // Only match root slash or root with hash/query: href="/" or href="/#foo"
        const rootRegex = new RegExp(`href=["']/((?:[?#][^"']*)?)["']`, 'g');
        updated = updated.replace(rootRegex, (match, query) => {
          return `href="${targetUrl}${query || ''}"`;
        });
      } else {
        const escapedItUrl = itUrl.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        const regex = new RegExp(`href=["']${escapedItUrl}([^"']*)["']`, 'g');
        updated = updated.replace(regex, (match, query) => {
          let q = query || '';
          if (q && /\.(css|js|webp|png|jpg|jpeg|svg|pdf|ico|json|xml)(\?.*)?$/i.test(q)) {
            return match;
          }
          if (currentLang !== 'it' && q.includes('piano=')) {
            q = q.replace('piano=', 'plan=');
          }
          return `href="${targetUrl}${q}"`;
        });
      }
    }
  }

  return updated;
}

function injectLanguageSelector(html, routeKey, currentLang) {
  let result = html;
  const { desktopHtml, mobileHtml } = generateLanguageSelector(routeKey, currentLang);

  // 1. Desktop selector: replace existing if present, otherwise inject before .nav-hamburger or into .hero-nav-actions
  if (result.includes('id="langSelectorWrap"') || result.includes('class="lang-selector-wrap"')) {
    result = result.replace(
      /<div[^>]*\b(?:id="langSelectorWrap"|class="lang-selector-wrap")[^>]*>[\s\S]*?id="langDropdownMenu"[\s\S]*?<\/div>\s*<\/div>/i,
      desktopHtml.trim()
    );
  } else if (result.includes('<button type="button" class="nav-hamburger"')) {
    result = result.replace(
      '<button type="button" class="nav-hamburger"',
      `${desktopHtml}\n                <button type="button" class="nav-hamburger"`
    );
  } else if (result.includes('<div class="hero-nav-actions">')) {
    result = result.replace(
      '<div class="hero-nav-actions">',
      `<div class="hero-nav-actions">\n${desktopHtml}`
    );
  }

  // 2. Mobile drawer: replace existing if present, otherwise append before closing </div> of mobile-menu-inner
  if (result.includes('class="mobile-lang-section"')) {
    result = result.replace(
      /<div[^>]*\bclass="mobile-lang-section"[^>]*>[\s\S]*?class="mobile-lang-grid"[\s\S]*?<\/div>\s*<\/div>/i,
      mobileHtml.trim()
    );
  } else if (result.includes('class="mobile-menu-inner"')) {
    result = result.replace(
      /(\s*<\/div>\s*<\/div>\s*(?:<!--[^\n]*-->\s*)*<main)/i,
      `\n${mobileHtml}$1`
    );
  }

  return result;
}

function updateSeoHead(html, routeKey, currentLang, bundle) {
  let updated = html;

  // Set html lang
  updated = updated.replace(/<html(\s+[^>]*?)lang="[^"]*"/i, `<html$1lang="${currentLang}"`);
  if (!updated.match(/<html[^>]*lang=/i)) {
    updated = updated.replace(/<html/i, `<html lang="${currentLang}"`);
  }

  // Replace og:locale
  const locale = LANG_LABELS[currentLang].locale;
  updated = updated.replace(/<meta property="og:locale" content="[^"]*">/i, `<meta property="og:locale" content="${locale}">`);

  // Remove existing canonical and hreflang tags to prevent duplicates
  updated = updated.replace(/\s*<link rel="canonical"[^>]*>/gi, '');
  updated = updated.replace(/\s*<link rel="alternate"\s+hreflang[^>]*>/gi, '');

  // Generate new SEO canonical + reciprocal hreflangs
  const hreflangTags = generateHreflangs(routeKey, currentLang);
  updated = updated.replace('</head>', `${hreflangTags}</head>`);

  // Localize title and meta description if present in bundle
  const meta = getPageMetadata(routeKey, bundle);
  if (meta) {
    if (meta.title) {
      updated = updated.replace(/<title>.*?<\/title>/i, `<title>${meta.title}</title>`);
      updated = updated.replace(/<meta property="og:title" content=".*?">/i, `<meta property="og:title" content="${meta.title}">`);
      updated = updated.replace(/<meta name="twitter:title" content=".*?">/i, `<meta name="twitter:title" content="${meta.title}">`);
    }
    if (meta.description) {
      updated = updated.replace(/<meta name="description" content=".*?">/i, `<meta name="description" content="${meta.description}">`);
      updated = updated.replace(/<meta property="og:description" content=".*?">/i, `<meta property="og:description" content="${meta.description}">`);
      updated = updated.replace(/<meta name="twitter:description" content=".*?">/i, `<meta name="twitter:description" content="${meta.description}">`);
    }
  }

  // Universal textless OG image for foreign locales
  if (currentLang !== 'it') {
    updated = updated.replace(/content="https:\/\/melpis\.it\/og-image\.jpg"/g, 'content="https://melpis.it/og-image-universal.jpg"');
    updated = updated.replace(/content="\/og-image\.jpg"/g, 'content="https://melpis.it/og-image-universal.jpg"');
  }

  // Localize JSON-LD structured data
  if (currentLang !== 'it') {
    const localeTag = LANG_LABELS[currentLang].locale.replace('_', '-');
    updated = updated.replace(/"inLanguage":\s*"it-IT"/g, `"inLanguage": "${localeTag}"`);
    if (bundle && bundle.landing && bundle.landing.meta_description) {
      const cleanDesc = bundle.landing.meta_description.replace(/"/g, '\\"');
      updated = updated.replace(/"description":\s*"(?:Risponditore|Assistente) WhatsApp AI e gestione prenotazioni per attività locali"/g, `"description": "${cleanDesc}"`);
      updated = updated.replace(/"description":\s*"Assistente WhatsApp AI e automazione prenotazioni su Google Calendar per attività locali e PMI\."/g, `"description": "${cleanDesc}"`);
      updated = updated.replace(/"description":\s*"Assistente AI su WhatsApp Business ufficiale per attività di servizio: risposte automatiche, prenotazioni sincronizzate su Google Calendar ed elaborazione recensioni\."/g, `"description": "${cleanDesc}"`);
    }
    if (bundle && bundle.pricing && bundle.pricing.plans) {
      if (bundle.pricing.plans.essential) {
        updated = updated.replace(/"name":\s*"Essenziale"/g, `"name": "${bundle.pricing.plans.essential.name}"`);
      }
      if (bundle.pricing.plans.growth) {
        updated = updated.replace(/"name":\s*"Crescita"/g, `"name": "${bundle.pricing.plans.growth.name}"`);
      }
      if (bundle.pricing.plans.scale) {
        updated = updated.replace(/"name":\s*"Scala"/g, `"name": "${bundle.pricing.plans.scale.name}"`);
      }
    }
  }

  return updated;
}

function getPageMetadata(routeKey, bundle) {
  if (!bundle) return null;
  if (routeKey === 'restaurants' && bundle.sectors && bundle.sectors.restaurants) {
    return { title: bundle.sectors.restaurants.page_title, description: bundle.sectors.restaurants.meta_description };
  }
  if (routeKey === 'beauty' && bundle.sectors && bundle.sectors.beauty) {
    return { title: bundle.sectors.beauty.page_title, description: bundle.sectors.beauty.meta_description };
  }
  if (routeKey === 'medical' && bundle.sectors && bundle.sectors.medical) {
    return { title: bundle.sectors.medical.page_title, description: bundle.sectors.medical.meta_description };
  }
  if (routeKey === 'hotels' && bundle.sectors && bundle.sectors.hotels) {
    return { title: bundle.sectors.hotels.page_title, description: bundle.sectors.hotels.meta_description };
  }
  if (routeKey === 'docs' && bundle.docs) {
    return { title: bundle.docs.page_title, description: bundle.docs.meta_description };
  }
  if (routeKey === 'login' && bundle.auth && bundle.auth.login) {
    return { title: bundle.auth.login.page_title, description: bundle.auth.login.meta_description };
  }
  if (routeKey === 'register' && bundle.auth && bundle.auth.register) {
    return { title: bundle.auth.register.page_title, description: bundle.auth.register.meta_description };
  }
  return null;
}

/* ─────────────────────────────────────────────────────────────
   BODY CONTENT LOCALIZATION ENGINE
─────────────────────────────────────────────────────────────── */

function localizeNav(html, lang, bundle) {
  if (lang === 'it' || !bundle) return html;
  const c = bundle.common || {};
  const nav = c.nav || {};
  const sec = c.sectors || {};
  let res = html;

  const skipMap = {
    en: 'Skip to main content',
    es: 'Saltar al contenido principal',
    fr: 'Passer au contenu principal',
    de: 'Zum Hauptinhalt springen'
  };
  if (skipMap[lang]) {
    res = res.replace('Salta al contenuto principale', skipMap[lang]);
  }

  if (nav.sectors) {
    res = res.replace(/<span>Settori<\/span>/g, `<span>${nav.sectors}</span>`);
  }

  if (sec.restaurants) {
    res = res.replace(/<span class="dropdown-sector-title">Ristoranti e Pizzerie<\/span>/g, `<span class="dropdown-sector-title">${sec.restaurants}</span>`);
    res = res.replace(/<span class="mobile-sublink-title">Ristoranti e Pizzerie<\/span>/g, `<span class="mobile-sublink-title">${sec.restaurants}</span>`);
  }
  if (sec.beauty) {
    res = res.replace(/<span class="dropdown-sector-title">Saloni &amp; Centri Estetici<\/span>/g, `<span class="dropdown-sector-title">${sec.beauty}</span>`);
    res = res.replace(/<span class="mobile-sublink-title">Saloni &amp; Bellezza<\/span>/g, `<span class="mobile-sublink-title">${sec.beauty}</span>`);
  }
  if (sec.medical) {
    res = res.replace(/<span class="dropdown-sector-title">Studi Medici &amp; Dentistici<\/span>/g, `<span class="dropdown-sector-title">${sec.medical}</span>`);
    res = res.replace(/<span class="mobile-sublink-title">Studi Medici &amp; Dentisti<\/span>/g, `<span class="mobile-sublink-title">${sec.medical}</span>`);
  }
  if (sec.hotels) {
    res = res.replace(/<span class="dropdown-sector-title">Hotel &amp; Ricettivo<\/span>/g, `<span class="dropdown-sector-title">${sec.hotels}</span>`);
    res = res.replace(/<span class="mobile-sublink-title">Hotel &amp; Ricettivo<\/span>/g, `<span class="mobile-sublink-title">${sec.hotels}</span>`);
  }

  const secSubMap = {
    en: {
      restaurants: "Table reservations, shift control, calendar sync",
      beauty: "Stylist booking, combined treatments, reminders",
      medical: "Patient triage, patient privacy, 24/7 support",
      hotels: "Room availability, multilingual WhatsApp concierge, check-in"
    },
    es: {
      restaurants: "Reservas de mesas, turnos y recordatorios anti no-show",
      beauty: "Citas por estilista, tratamientos combinados, avisos",
      medical: "Triaje preliminar, privacidad de pacientes, soporte 24/7",
      hotels: "Disponibilidad de habitaciones, conserje multilingüe, check-in"
    },
    fr: {
      restaurants: "Réservations de tables, gestion des services et no-show",
      beauty: "Rendez-vous par praticien, soins combinés, rappels",
      medical: "Triage préliminaire, confidentialité patient, support 24/7",
      hotels: "Disponibilité des chambres, conciergerie multilingue, check-in"
    },
    de: {
      restaurants: "Tischreservierungen, Schichtverwaltung, Kalendersync",
      beauty: "Termine nach Mitarbeiter, Kombi-Behandlungen, Erinnerungen",
      medical: "Patiententriage, vertrauliche Datenverarbeitung, 24/7-Support",
      hotels: "Zimmerverfügbarkeit, mehrsprachiger WhatsApp-Concierge, Check-in"
    }
  };
  if (secSubMap[lang]) {
    res = res.replace('Prenotazioni tavoli, gestione no-show, sincronizzazione calendario', secSubMap[lang].restaurants);
    res = res.replace('Appuntamenti per operatore, trattamenti combinati, promemoria', secSubMap[lang].beauty);
    res = res.replace('Triage preliminare, privacy rigorosa, supporto segreteria 24/7', secSubMap[lang].medical);
    res = res.replace('Disponibilità camere, concierge WhatsApp multilingue, check-in rapido', secSubMap[lang].hotels);
  }

  if (nav.documentation) {
    res = res.replace(/>Documentazione<\/a>/g, `>${nav.documentation}</a>`);
  }
  if (nav.pricing) {
    res = res.replace(/>Prezzi<\/a>/g, `>${nav.pricing}</a>`);
  }
  if (nav.login) {
    res = res.replace(/>Accedi<\/a>/g, `>${nav.login}</a>`);
    res = res.replace(/>Accedi al pannello<\/a>/g, `>${nav.login}</a>`);
  }
  if (nav.signup) {
    res = res.replace(/<span>Inizia gratis<\/span>/g, `<span>${nav.signup}</span>`);
  }
  if (bundle.landing?.hero?.cta_primary) {
    res = res.replace(/<span>Inizia la prova di 7 giorni<\/span>/g, `<span>${bundle.landing.hero.cta_primary}</span>`);
  }

  return res;
}

function localizeFooter(html, lang, bundle) {
  if (lang === 'it' || !bundle) return html;
  const f = bundle.common?.footer || {};
  let res = html;

  if (f.rights) {
    res = res.replace(/Tutti i diritti riservati\./g, f.rights);
  }
  if (f.privacy) {
    res = res.replace(/Informativa sulla Privacy/g, f.privacy);
  }
  if (f.terms) {
    res = res.replace(/Termini di Servizio/g, f.terms);
    res = res.replace(/Termini e Condizioni/g, f.terms);
  }
  if (f.cookies) {
    res = res.replace(/Cookie Policy/g, f.cookies);
    res = res.replace(/Informativa Cookie/g, f.cookies);
  }
  if (f.legal_note) {
    res = res.replace(/Melpis utilizza le API Cloud ufficiali di WhatsApp Business\..*?WhatsApp LLC\/Meta Platforms Inc\./g, f.legal_note);
  }

  const colMap = {
    en: { prod: "Product", sec: "Industries", res: "Resources", leg: "Legal", feat: "Features", integ: "Integrations", guide: "Onboarding Guide", check: "Pre-Launch Checklist", secGdpr: "Security & GDPR" },
    es: { prod: "Producto", sec: "Sectores", res: "Recursos", leg: "Legal", feat: "Funcionalidades", integ: "Integraciones", guide: "Guía de inicio", check: "Lista pre-lanzamiento", secGdpr: "Seguridad y RGPD" },
    fr: { prod: "Produit", sec: "Secteurs", res: "Ressources", leg: "Mentions légales", feat: "Fonctionnalités", integ: "Intégrations", guide: "Guide de démarrage", check: "Checklist pré-lancement", secGdpr: "Sécurité & RGPD" },
    de: { prod: "Produkt", sec: "Branchen", res: "Ressourcen", leg: "Rechtliches", feat: "Funktionen", integ: "Integrationen", guide: "Onboarding-Leitfaden", check: "Checkliste vor dem Start", secGdpr: "Sicherheit & DSGVO" }
  };

  const cm = colMap[lang];
  if (cm) {
    res = res.replace(/<h4>Prodotto<\/h4>/g, `<h4>${cm.prod}</h4>`);
    res = res.replace(/<h4>Settori<\/h4>/g, `<h4>${cm.sec}</h4>`);
    res = res.replace(/<h4>Risorse<\/h4>/g, `<h4>${cm.res}</h4>`);
    res = res.replace(/<h4>Legale<\/h4>/g, `<h4>${cm.leg}</h4>`);
    res = res.replace(/>Funzionalità<\/a>/g, `>${cm.feat}</a>`);
    res = res.replace(/>Integrazioni<\/a>/g, `>${cm.integ}</a>`);
    res = res.replace(/>Guida Onboarding<\/a>/g, `>${cm.guide}</a>`);
    res = res.replace(/>Checklist Pre-Lancio<\/a>/g, `>${cm.check}</a>`);
    res = res.replace(/>Sicurezza e GDPR<\/a>/g, `>${cm.secGdpr}</a>`);
  }

  return res;
}

function localizeSector(html, routeKey, lang, bundle) {
  if (lang === 'it' || !bundle || !bundle.sectors) return html;
  const s = bundle.sectors[routeKey];
  if (!s) return html;
  let res = html;

  if (s.hero) {
    if (s.hero.badge) {
      res = res.replace(/<span class="badge-text">.*?<\/span>/, `<span class="badge-text">${s.hero.badge}</span>`);
    }
    if (s.hero.title) {
      res = res.replace(/<h1 class="vertical-hero-title[^"]*">[\s\S]*?<\/h1>/, `<h1 class="vertical-hero-title hero-animate-1">${s.hero.title}</h1>`);
    }
    if (s.hero.subtitle) {
      res = res.replace(/<p class="vertical-hero-sub[^"]*">[\s\S]*?<\/p>/, `<p class="vertical-hero-sub hero-animate-3">${s.hero.subtitle}</p>`);
    }
    if (s.hero.cta) {
      res = res.replace(/<span>Inizia la prova di 7 giorni<\/span>/g, `<span>${s.hero.cta}</span>`);
    }
  }

  // 1. Shared Sector Copy
  const shared = SECTOR_SHARED[lang];
  if (shared) {
    res = res.replace(/Nessuna carta di credito richiesta/g, shared.trustCard);
    res = res.replace(/Fino a 7 giorni o 150 risposte AI/g, shared.trustTrial);
    res = res.replace(/Meta Cloud API ufficiale/g, shared.trustApi);
    res = res.replace(/<span>Scopri come funziona<\/span>/g, `<span>${shared.ctaSecondary}</span>`);
    res = res.replace(/<span class="eyebrow-pill">Semplicità di attivazione<\/span>/g, `<span class="eyebrow-pill">${shared.stepsEyebrow}</span>`);
    res = res.replace(/<span class="eyebrow-pill">Domande Frequenti<\/span>/g, `<span class="eyebrow-pill">${shared.faqEyebrow}</span>`);
  }

  const skipLabels = {
    en: 'Skip to main content',
    es: 'Saltar al contenido principal',
    fr: 'Aller au contenu principal',
    de: 'Zum Hauptinhalt springen'
  };
  if (skipLabels[lang]) {
    res = res.replace(/Salta al contenuto principale/, skipLabels[lang]);
  }

  // 2. Sector Extras (Vantaggi, Steps, FAQs, CTA)
  const secExt = SECTOR_EXTRAS[routeKey]?.[lang];
  if (secExt) {
    if (secExt.vantaggiEyebrow) {
      res = res.replace(/<span class="eyebrow-pill">Pensato per.*?<\/span>/, `<span class="eyebrow-pill">${secExt.vantaggiEyebrow}</span>`);
    }
    if (secExt.vantaggiTitle) {
      res = res.replace(/<h2 class="display-md">I vantaggi concreti per.*?<\/h2>/, `<h2 class="display-md">${secExt.vantaggiTitle}</h2>`);
    }
    if (secExt.stepsTitle) {
      res = res.replace(/<h2 class="display-md">Pronto per il tuo servizio in 3 passaggi<\/h2>/, `<h2 class="display-md">${secExt.stepsTitle}</h2>`);
    }
    if (secExt.step1Title) {
      res = res.replace(/<h3>Collega il tuo WhatsApp<\/h3>/, `<h3>${secExt.step1Title}</h3>`);
      res = res.replace(/<p>Connetti il (tuo )?numero.*?<\/p>/, `<p>${secExt.step1Desc}</p>`);
    }
    if (secExt.step2Title) {
      res = res.replace(/<h3>(Carica menu, orari e turni|Carica servizi, durate e collaboratori|Definisci orari, prestazioni e regole|Carica guida, regole e servizi)<\/h3>/, `<h3>${secExt.step2Title}</h3>`);
      res = res.replace(/<p>(Carica le informazioni del menu|Imposta i servizi|Configura orari|Carica le istruzioni).*?<\/p>/, `<p>${secExt.step2Desc}</p>`);
    }
    if (secExt.step3Title) {
      res = res.replace(/<h3>(I tavoli si confermano in automatico|Le poltrone si riempiono da sole|Visite e richieste gestite in sicurezza|Il concierge risponde 24\/7)<\/h3>/, `<h3>${secExt.step3Title}</h3>`);
      res = res.replace(/<p>(Ogni tavolo confermato|Ogni appuntamento confermato|I pazienti ricevono risposte|I tuoi ospiti ricevono risposte).*?<\/p>/, `<p>${secExt.step3Desc}</p>`);
    }

    // FAQ list dynamic replacement
    if (secExt.faqTitle) {
      res = res.replace(/<h2 class="display-md">Tutto quello che serve sapere.*?<\/h2>/, `<h2 class="display-md">${secExt.faqTitle}</h2>`);
    }
    if (Array.isArray(secExt.faqItems) && secExt.faqItems.length > 0) {
      const faqItemsHtml = secExt.faqItems.map(item => `                        <details class="faq-item reveal">
                            <summary class="faq-quest"><span>${item.q}</span> <span class="faq-icon" aria-hidden="true">+</span></summary>
                            <div class="faq-ans"><div class="faq-ans-inner"><p>${item.a}</p></div></div>
                        </details>`).join('\n');
      res = res.replace(/<div class="faq-list">[\s\S]*?<\/div>/, `<div class="faq-list">\n${faqItemsHtml}\n                    </div>`);
    }

    // Final CTA
    if (secExt.finalTitle) {
      res = res.replace(/<h2 class="final-cta-title">[\s\S]*?<\/h2>/, `<h2 class="final-cta-title">${secExt.finalTitle}</h2>`);
    }
    if (secExt.finalSub) {
      res = res.replace(/<p class="final-cta-sub">[\s\S]*?<\/p>/, `<p class="final-cta-sub">${secExt.finalSub}</p>`);
    }
    if (secExt.finalBtn) {
      res = res.replace(/<span>Attiva la prova per.*?<\/span>/, `<span>${secExt.finalBtn}</span>`);
    }

    // Specific for restaurants mockups
    if (routeKey === 'restaurants') {
      if (secExt.row1Eyebrow) res = res.replace(/<span class="vantaggi-eyebrow">Automazione WhatsApp<\/span>/, `<span class="vantaggi-eyebrow">${secExt.row1Eyebrow}</span>`);
      if (secExt.row1Title) res = res.replace(/<h3>Risposte e prenotazioni istantanee su WhatsApp<\/h3>/, `<h3>${secExt.row1Title}</h3>`);
      if (secExt.row1Desc) res = res.replace(/<p>Mentre la sala è piena e la brigata è ai fornelli.*?<\/p>/, `<p>${secExt.row1Desc}</p>`);
      if (secExt.chatInAuthor) res = res.replace(/Cliente · 19:12/, secExt.chatInAuthor);
      if (secExt.chatInQuote) res = res.replace(/“Buonasera! Avete un tavolo per 4 stasera verso le 20:30\?”/, secExt.chatInQuote);
      if (secExt.chatOutAuthor) res = res.replace(/Melpis per il tuo locale/, secExt.chatOutAuthor);
      if (secExt.chatOutQuote) res = res.replace(/“Tavolo per 4 confermato alle 20:30\. Ho inserito la preferenza per la sala interna\.”/, secExt.chatOutQuote);
      if (secExt.chatSyncPill) res = res.replace(/<span>Google Calendar opzionale, previa certificazione operativa<\/span>/, '<span>' + secExt.chatSyncPill + '</span>');

      if (secExt.row2Eyebrow) res = res.replace(/<span class="vantaggi-eyebrow">Rotazione Sala<\/span>/, `<span class="vantaggi-eyebrow">${secExt.row2Eyebrow}</span>`);
      if (secExt.row2Title) res = res.replace(/<h3>Doppi turni e capienza ottimizzata<\/h3>/, `<h3>${secExt.row2Title}</h3>`);
      if (secExt.row2Desc) res = res.replace(/<p>Configura i turni serali.*?<\/p>/, `<p>${secExt.row2Desc}</p>`);

      if (secExt.row3Eyebrow) res = res.replace(/<span class="vantaggi-eyebrow">Intelligenza Contestuale<\/span>/, `<span class="vantaggi-eyebrow">${secExt.row3Eyebrow}</span>`);
      if (secExt.row3Title) res = res.replace(/<h3>Menu, allergeni e carta dei vini da PDF<\/h3>/, `<h3>${secExt.row3Title}</h3>`);
      if (secExt.row3Desc) res = res.replace(/<p>Configura menu e allergeni per le risposte.*?<\/p>/, `<p>${secExt.row3Desc}</p>`);

      if (secExt.row4Eyebrow) res = res.replace(/<span class="vantaggi-eyebrow">Zero No-Show<\/span>/, `<span class="vantaggi-eyebrow">${secExt.row4Eyebrow}</span>`);
      if (secExt.row4Title) res = res.replace(/<h3>Promemoria interattivi e sincronizzazione calendario<\/h3>/, `<h3>${secExt.row4Title}</h3>`);
      if (secExt.row4Desc) res = res.replace(/<p>Invia un promemoria WhatsApp a ridosso del servizio.*?<\/p>/, `<p>${secExt.row4Desc}</p>`);

      if (secExt.noshowQuote) res = res.replace(/“Gentile Marco, ti ricordiamo il tavolo per 4 stasera alle 20:30 da Osteria Bella Vista\.”/, secExt.noshowQuote);
      if (secExt.noshowConfirm) res = res.replace(/<span>Confermo la presenza<\/span>/, `<span>${secExt.noshowConfirm}</span>`);
      if (secExt.noshowCancel) res = res.replace(/<span>Devo disdire<\/span>/, `<span>${secExt.noshowCancel}</span>`);
      if (secExt.noshowFeedback) res = res.replace(/<span id="noshowFeedbackText">Presenza confermata dall'ospite · Sincronizzato con Google Calendar<\/span>/, `<span id="noshowFeedbackText">${secExt.noshowFeedback}</span>`);
    }

    // Row 4 button fallbacks for other sectors
    const confirmMap = { en: "Confirm appointment", es: "Confirmo cita", fr: "Confirmer rdv", de: "Termin bestätigen" };
    const cancelMap = { en: "Reschedule / cancel", es: "Reprogramar / cancelar", fr: "Reporter / annuler", de: "Verschieben / absagen" };
    const feedbackMap = { en: "Appointment confirmed · Calendar slot protected", es: "Cita confirmada · Espacio protegido en agenda", fr: "Rendez-vous confirmé · Créneau réservé", de: "Termin bestätigt · Zeitfenster reserviert" };

    if (confirmMap[lang]) res = res.replace(/<span>Confermo la presenza<\/span>/g, `<span>${confirmMap[lang]}</span>`);
    if (cancelMap[lang]) res = res.replace(/<span>(Devo disdire|Devo spostare|Devo rimandare)<\/span>/g, `<span>${cancelMap[lang]}</span>`);
    if (feedbackMap[lang]) res = res.replace(/<span id="noshowFeedbackText">.*?<\/span>/g, `<span id="noshowFeedbackText">${feedbackMap[lang]}</span>`);
  }

  if (Array.isArray(s.features)) {
    const itSec = loadLocales('it')?.sectors?.[routeKey]?.features || [];
    s.features.forEach((feat, idx) => {
      const itFeat = itSec[idx];
      if (itFeat) {
        if (itFeat.title && feat.title) res = res.replace(itFeat.title, feat.title);
        if (itFeat.desc && feat.desc) res = res.replace(itFeat.desc, feat.desc);
      }
    });
  }

  return res;
}

function localizeAuth(html, routeKey, lang, bundle) {
  if (!bundle || !bundle.auth) return html;
  const a = bundle.auth;
  let res = html;

  // Inietta il bundle dei messaggi per auth client
  if (a.messages) {
    const jsonSnippet = `<script id="auth-translations" type="application/json">${JSON.stringify(a.messages)}</script>`;
    if (res.includes('id="auth-translations"')) {
      res = res.replace(/<script id="auth-translations"[^>]*>[\s\S]*?<\/script>/, jsonSnippet);
    } else {
      res = res.replace(/<\/head>/, `    ${jsonSnippet}\n</head>`);
    }
  }

  if (lang === 'it') return res;

  if (routeKey === 'login' && a.login) {
    const l = a.login;
    res = res.replace(/<h1 id="accesso-title">.*?<\/h1>/, `<h1 id="accesso-title">${l.heading}</h1>`);
    res = res.replace(/<p class="accesso-help" id="accesso-help">.*?<\/p>/, `<p class="accesso-help" id="accesso-help">${l.subtitle}</p>`);
    res = res.replace(/<\/svg>\s*Accedi con Google/i, `</svg>\n      ${l.google_btn}`);
    res = res.replace(/<div class="accedi-divider">oppure con email<\/div>/, `<div class="accedi-divider">${l.divider}</div>`);
    res = res.replace(/<label for="accesso-email">Email<\/label>/, `<label for="accesso-email">${l.email_label}</label>`);
    res = res.replace(/<label for="accesso-password">Password<\/label>/, `<label for="accesso-password">${l.password_label}</label>`);
    res = res.replace(/placeholder="nome@attivita\.it"/g, `placeholder="${l.email_placeholder || 'name@business.com'}"`);
    res = res.replace(/<button type="submit" class="review-analyze" id="accesso-save">Accedi<\/button>/, `<button type="submit" class="review-analyze" id="accesso-save">${l.submit_btn}</button>`);
    res = res.replace(/<a href="#" id="forgot-link">Password dimenticata\?<\/a>/, `<a href="#" id="forgot-link">${l.forgot_link}</a>`);
    res = res.replace(/Non hai un account\?\s*<a id="link-registrati"[^>]*>Registrati<\/a>/, `${l.no_account} <a id="link-registrati" href="${ROUTE_MAP.register[lang]}">${l.signup_link}</a>`);
    res = res.replace(/← Torna alla home/, l.back_home);

    if (a.recover) {
      res = res.replace(/Inserisci l'email con cui ti sei registrato.*?nuova password\./, a.recover.instructions);
      res = res.replace(/<button type="submit" class="review-analyze" id="recover-save">Invia link di recupero<\/button>/, `<button type="submit" class="review-analyze" id="recover-save">${a.recover.submit_btn}</button>`);
      res = res.replace(/← Torna al login/, a.recover.back_to_login);
    }
    if (a.reset) {
      res = res.replace(/Imposta una nuova password: almeno 10 caratteri e un carattere speciale\./, a.reset.instructions);
      res = res.replace(/<label for="reset-password">Nuova password<\/label>/, `<label for="reset-password">${a.reset.new_password_label}</label>`);
      res = res.replace(/<button type="submit" class="review-analyze" id="reset-save">Aggiorna password<\/button>/, `<button type="submit" class="review-analyze" id="reset-save">${a.reset.submit_btn}</button>`);
    }
  }

  if (routeKey === 'register' && a.register) {
    const r = a.register;
    res = res.replace(/<h1 id="register-title">.*?<\/h1>/, `<h1 id="register-title">${r.heading}</h1>`);
    res = res.replace(/<p class="accesso-help" id="register-help">.*?<\/p>/, `<p class="accesso-help" id="register-help">${r.subtitle}</p>`);
    res = res.replace(/<\/svg>\s*Continua con Google/i, `</svg>\n      ${r.google_btn}`);
    res = res.replace(/<div class="accedi-divider">oppure con email<\/div>/, `<div class="accedi-divider">${r.divider}</div>`);
    res = res.replace(/<label for="reg-nome">Nome dell'attività<\/label>/, `<label for="reg-nome">${r.business_label}</label>`);
    res = res.replace(/placeholder="Es\. Trattoria Da Mario"/, `placeholder="${r.business_placeholder}"`);
    res = res.replace(/<label for="reg-email">Email di lavoro<\/label>/, `<label for="reg-email">${r.email_label}</label>`);
    res = res.replace(/placeholder="tu@attivita\.it"/, `placeholder="${r.email_placeholder}"`);
    res = res.replace(/<label for="reg-password">Password<\/label>/, `<label for="reg-password">${r.password_label}</label>`);
    res = res.replace(/placeholder="Minimo 10 caratteri con un simbolo"/, `placeholder="${r.password_placeholder}"`);

    if (r.checks) {
      res = res.replace(/<li data-check="len">Almeno 10 caratteri<\/li>/, `<li data-check="len">${r.checks.len}</li>`);
      res = res.replace(/<li data-check="special">Un simbolo \(! @ # \$ % …\)<\/li>/, `<li data-check="special">${r.checks.special}</li>`);
      res = res.replace(/<li data-check="upper">Una maiuscola<\/li>/, `<li data-check="upper">${r.checks.upper}</li>`);
      res = res.replace(/<li data-check="num">Un numero<\/li>/, `<li data-check="num">${r.checks.num}</li>`);
    }

    if (r.terms_agree) {
      res = res.replace(/<label for="reg-termini">[\s\S]*?<\/label>/, `<label for="reg-termini">${r.terms_agree}</label>`);
    }

    res = res.replace(/<button type="submit" class="review-analyze" id="register-save">Crea il mio assistente<\/button>/, `<button type="submit" class="review-analyze" id="register-save">${r.submit_btn}</button>`);
    res = res.replace(/Hai già un account\?\s*<a id="link-accedi"[^>]*>[^<]*<\/a>/, `${r.have_account || "Already have an account?"} <a id="link-accedi" href="${ROUTE_MAP.login[lang]}">${r.login_link || "Log in"}</a>`);
    res = res.replace(/(<p class="accesso-success" id="register-success"[^>]*>)[\s\S]*?(<\/p>)/, `$1\n        <strong>${r.success_title}</strong> ${r.success_body} <a id="success-login-link" href="${ROUTE_MAP.login[lang]}">${r.success_login_link}</a>\n      $2`);
    res = res.replace(/← Torna alla home/, r.back_home || "← Back to home");
  }

  return res;
}

function localizeLegal(html, routeKey, lang) {
  if (lang === 'it') return html;
  let res = html;

  const itTarget = (routeKey === 'privacy') ? 'privacy' : (routeKey === 'terms' ? 'termini' : 'cookie');
  const disclaimers = {
    en: `<div class="legal-disclaimer-callout" style="background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.15); border-radius: 8px; padding: 14px 18px; margin: 20px 0 28px; font-size: 0.90rem; color: #cbd5e1; line-height: 1.5;"><strong style="color: #ffffff;">Notice:</strong> This document is provided in English as a courtesy translation. The legally binding version is the official <a href="https://melpis.it/${itTarget}/" style="color: #60a5fa; text-decoration: underline;">Italian text</a>.</div>`,
    es: `<div class="legal-disclaimer-callout" style="background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.15); border-radius: 8px; padding: 14px 18px; margin: 20px 0 28px; font-size: 0.90rem; color: #cbd5e1; line-height: 1.5;"><strong style="color: #ffffff;">Aviso:</strong> Este documento se proporciona como una traducción de cortesía. La versión legalmente vinculante es el <a href="https://melpis.it/${itTarget}/" style="color: #60a5fa; text-decoration: underline;">texto oficial en italiano</a>.</div>`,
    fr: `<div class="legal-disclaimer-callout" style="background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.15); border-radius: 8px; padding: 14px 18px; margin: 20px 0 28px; font-size: 0.90rem; color: #cbd5e1; line-height: 1.5;"><strong style="color: #ffffff;">Avis :</strong> Ce document est fourni à titre de traduction de courtoisie. La version juridiquement contraignante est le <a href="https://melpis.it/${itTarget}/" style="color: #60a5fa; text-decoration: underline;">texte officiel en italien</a>.</div>`,
    de: `<div class="legal-disclaimer-callout" style="background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.15); border-radius: 8px; padding: 14px 18px; margin: 20px 0 28px; font-size: 0.90rem; color: #cbd5e1; line-height: 1.5;"><strong style="color: #ffffff;">Hinweis:</strong> Dieses Dokument dient als unverbindliche Übersetzung. Rechtlich bindend ist ausschließlich die offizielle <a href="https://melpis.it/${itTarget}/" style="color: #60a5fa; text-decoration: underline;">italienische Fassung</a>.</div>`
  };

  const titleMap = {
    privacy: { en: "Privacy Policy", es: "Política de Privacidad", fr: "Politique de Confidentialité", de: "Datenschutzerklärung" },
    terms: { en: "Terms of Service", es: "Términos del Servicio", fr: "Conditions d'Utilisation", de: "Allgemeine Geschäftsbedingungen" },
    cookies: { en: "Cookie Policy", es: "Política de Cookies", fr: "Politique relative aux Cookies", de: "Cookie-Richtlinie" }
  };

  if (titleMap[routeKey] && titleMap[routeKey][lang]) {
    res = res.replace(/<h1[^>]*>.*?<\/h1>/, `<h1>${titleMap[routeKey][lang]}</h1>`);
  }

  if (disclaimers[lang] && !res.includes('class="legal-disclaimer-callout"')) {
    res = res.replace(/(<p class="legal-updated">.*?<\/p>)/, `$1\n    ${disclaimers[lang]}`);
  }

  return res;
}

function localizeDocs(html, lang, bundle) {
  if (lang === 'it' || !bundle || !bundle.docs) return html;
  const d = bundle.docs;
  let res = html;

  const de = DOCS_EXTRAS[lang];

  if (d.hero) {
    if (d.hero.badge) res = res.replace(/Guide &amp; Documentazione Ufficiale/g, d.hero.badge);
    if (d.hero.title) res = res.replace(/Documentazione &amp; Guida Ufficiale/g, d.hero.title);
    if (d.hero.subtitle) res = res.replace(/Tutto ciò che serve per configurare, integrare e sfruttare al massimo il tuo assistente Melpis\./g, d.hero.subtitle);
  }

  // Search input placeholder
  const searchPl = de?.searchPlaceholder || d.hero?.search_placeholder || "Search guides (Ctrl+K)...";
  res = res.replace(/placeholder="Cerca nelle guide \(Ctrl\+K\)\.\.\."/g, `placeholder="${searchPl}"`);
  res = res.replace(/placeholder="Cerca guide, integrazioni, configurazione \(Ctrl\+K\)\.\.\."/g, `placeholder="${searchPl}"`);

  // Sidebar group titles
  const cats = d.categories || {};
  if (cats.start?.title) res = res.replace(/<span class="docs-sidebar-group-title">Inizia da qui<\/span>/g, `<span class="docs-sidebar-group-title">${cats.start.title}</span>`);
  if (cats.integrations?.title) res = res.replace(/<span class="docs-sidebar-group-title">Canali e Integrazioni<\/span>/g, `<span class="docs-sidebar-group-title">${cats.integrations.title}</span>`);
  if (cats.ai_config?.title) res = res.replace(/<span class="docs-sidebar-group-title">Configurazione AI<\/span>/g, `<span class="docs-sidebar-group-title">${cats.ai_config.title}</span>`);
  if (cats.inbox?.title) res = res.replace(/<span class="docs-sidebar-group-title">Shared Inbox &amp; Umano<\/span>/g, `<span class="docs-sidebar-group-title">${cats.inbox.title}</span>`);
  if (cats.bookings?.title) res = res.replace(/<span class="docs-sidebar-group-title">Prenotazioni<\/span>/g, `<span class="docs-sidebar-group-title">${cats.bookings.title}</span>`);
  if (cats.accounts?.title) res = res.replace(/<span class="docs-sidebar-group-title">Account &amp; Piani<\/span>/g, `<span class="docs-sidebar-group-title">${cats.accounts.title}</span>`);
  if (cats.security?.title) res = res.replace(/<span class="docs-sidebar-group-title">Sicurezza &amp; GDPR<\/span>/g, `<span class="docs-sidebar-group-title">${cats.security.title}</span>`);
  if (cats.troubleshooting?.title) res = res.replace(/<span class="docs-sidebar-group-title">Troubleshooting<\/span>/g, `<span class="docs-sidebar-group-title">${cats.troubleshooting.title}</span>`);

  // Breadcrumbs & Navigation
  const bcDocs = bundle.common?.nav?.docs || "Documentation";
  res = res.replace(/<a href="\/documentazione\/" class="docs-bc-link">Documentazione<\/a>/g, `<a href="${ROUTE_MAP.docs[lang]}" class="docs-bc-link">${bcDocs}</a>`);
  if (cats.start?.title) {
    res = res.replace(/<a href="\/documentazione\/" class="docs-bc-link">Inizia da qui<\/a>/g, `<a href="${ROUTE_MAP.docs[lang]}" class="docs-bc-link">${cats.start.title}</a>`);
  }

  // Article title, Headings & Table of Contents
  const articleHeadings = {
    en: {
      title: "What is Melpis and how the AI assistant works",
      sub: "A complete, verifiable overview of how Melpis independently manages WhatsApp and Instagram messages, respects price lists, and involves staff when needed.",
      h2_what: "What it enables you to do",
      h2_pillars: "The 4 operational pillars of Melpis",
      h2_pipeline: "Message lifecycle (Pipeline)",
      h2_limits: "Real system limitations",
      h2_troubleshooting: "Troubleshooting",
      h2_tech: "Technical deep dive (for developers)",
      h2_related: "Related guides",
      mobile_toc: "Show article table of contents",
      toc_title: "On this page",
      toc_what: "What it enables you to do",
      toc_pillars: "The 4 operational pillars",
      toc_pipeline: "Message lifecycle",
      toc_limits: "Real limitations",
      toc_troubleshooting: "Troubleshooting",
      toc_tech: "Technical deep dive",
      toc_related: "Related guides",
      feedback_label: "Was this guide helpful?",
      feedback_yes: "👍 Yes, very clear",
      feedback_no: "👎 No, needs clarification",
      feedback_thanks: "Thank you for your feedback! It helps improve our documentation."
    },
    es: {
      title: "Qué es Melpis y cómo funciona el asistente de IA",
      sub: "Una visión completa y verificable de cómo Melpis gestiona con total autonomía los mensajes de WhatsApp e Instagram, respeta tus tarifas y deriva al equipo.",
      h2_what: "Qué permite hacer",
      h2_pillars: "Los 4 pilares operativos de Melpis",
      h2_pipeline: "El ciclo de vida del mensaje (Pipeline)",
      h2_limits: "Limitaciones reales del sistema",
      h2_troubleshooting: "Resolución de problemas",
      h2_tech: "Profundización técnica (para desarrolladores)",
      h2_related: "Guías relacionadas",
      mobile_toc: "Mostrar índice del artículo",
      toc_title: "En esta página",
      toc_what: "Qué permite hacer",
      toc_pillars: "Los 4 pilares operativos",
      toc_pipeline: "Ciclo de vida del mensaje",
      toc_limits: "Limitaciones reales",
      toc_troubleshooting: "Resolución de problemas",
      toc_tech: "Profundización técnica",
      toc_related: "Guías relacionadas",
      feedback_label: "¿Te resultó útil esta guía?",
      feedback_yes: "👍 Sí, muy clara",
      feedback_no: "👎 No, necesita aclaración",
      feedback_thanks: "¡Gracias por tus comentarios! Ayudan a mejorar la documentación."
    },
    fr: {
      title: "Qu'est-ce que Melpis et comment fonctionne l'assistant IA",
      sub: "Une vue d'ensemble complète sur la façon dont Melpis gère en toute autonomie les messages WhatsApp et Instagram, respecte vos tarifs et mobilise votre équipe au besoin.",
      h2_what: "Ce que permet la solution",
      h2_pillars: "Les 4 piliers opérationnels de Melpis",
      h2_pipeline: "Le cycle de vie d'un message (Pipeline)",
      h2_limits: "Limites réelles du système",
      h2_troubleshooting: "Résolution des problèmes",
      h2_tech: "Approfondissement technique (pour développeurs)",
      h2_related: "Guides associés",
      mobile_toc: "Afficher le sommaire des articles",
      toc_title: "Sur cette page",
      toc_what: "Ce que permet la solution",
      toc_pillars: "Les 4 piliers opérationnels",
      toc_pipeline: "Cycle de vie d'un message",
      toc_limits: "Limites réelles",
      toc_troubleshooting: "Résolution des problèmes",
      toc_tech: "Approfondissement technique",
      toc_related: "Guides associés",
      feedback_label: "Ce guide vous a-t-il été utile ?",
      feedback_yes: "👍 Oui, très clair",
      feedback_no: "👎 Non, nécessite des clarifications",
      feedback_thanks: "Merci pour votre retour ! Cela nous aide à améliorer la documentation."
    },
    de: {
      title: "Was ist Melpis und wie funktioniert der KI-Assistent",
      sub: "Ein vollständiger und überprüfbarer Überblick darüber, wie Melpis WhatsApp- und Instagram-Nachrichten selbstständig beantwortet, Preislisten beachtet und bei Bedarf Mitarbeiter einbindet.",
      h2_what: "Funktionsumfang und Möglichkeiten",
      h2_pillars: "Die 4 operativen Säulen von Melpis",
      h2_pipeline: "Lebenszyklus einer Nachricht (Pipeline)",
      h2_limits: "Tatsächliche Systemgrenzen",
      h2_troubleshooting: "Fehlerbehebung",
      h2_tech: "Technische Vertiefung (für Entwickler)",
      h2_related: "Verwandte Anleitungen",
      mobile_toc: "Inhaltsverzeichnis anzeigen",
      toc_title: "Auf dieser Seite",
      toc_what: "Funktionsumfang und Möglichkeiten",
      toc_pillars: "Die 4 operativen Säulen",
      toc_pipeline: "Lebenszyklus Nachricht",
      toc_limits: "Tatsächliche Systemgrenzen",
      toc_troubleshooting: "Fehlerbehebung",
      toc_tech: "Technische Vertiefung",
      toc_related: "Verwandte Anleitungen",
      feedback_label: "War diese Anleitung hilfreich?",
      feedback_yes: "👍 Ja, sehr verständlich",
      feedback_no: "👎 Nein, benötigt Klärung",
      feedback_thanks: "Vielen Dank für Ihre Rückmeldung! Sie hilft uns, die Dokumentation zu verbessern."
    }
  };

  const ah = articleHeadings[lang];
  if (ah) {
    res = res.replace(/<h1 class="docs-article-title">Cos'è Melpis e come funziona l'assistente AI<\/h1>/g, `<h1 class="docs-article-title">${ah.title}</h1>`);
    res = res.replace(/<p class="docs-article-subtitle">Una panoramica completa e verificabile su come Melpis gestisce in autonomia i messaggi WhatsApp e Instagram dei clienti, rispetta i listini aziendali e coinvolge il personale quando necessario\.<\/p>/g, `<p class="docs-article-subtitle">${ah.sub}</p>`);
    res = res.replace(/<h2 id="cosa-permette-di-fare">Cosa permette di fare<\/h2>/g, `<h2 id="cosa-permette-di-fare">${ah.h2_what}</h2>`);
    res = res.replace(/<h2 id="i-4-pilastri-operativi">I 4 pilastri operativi di Melpis<\/h2>/g, `<h2 id="i-4-pilastri-operativi">${ah.h2_pillars}</h2>`);
    res = res.replace(/<h2 id="il-ciclo-di-vita-del-messaggio">Il ciclo di vita di un messaggio \(Pipeline\)<\/h2>/g, `<h2 id="il-ciclo-di-vita-del-messaggio">${ah.h2_pipeline}</h2>`);
    res = res.replace(/<h2 id="limitazioni-reali">Limitazioni reali del sistema<\/h2>/g, `<h2 id="limitazioni-reali">${ah.h2_limits}</h2>`);
    res = res.replace(/<h2 id="risoluzione-dei-problemi">Risoluzione dei problemi<\/h2>/g, `<h2 id="risoluzione-dei-problemi">${ah.h2_troubleshooting}</h2>`);
    res = res.replace(/<h2 id="approfondimento-tecnico">Approfondimento tecnico \(per sviluppatori\)<\/h2>/g, `<h2 id="approfondimento-tecnico">${ah.h2_tech}</h2>`);
    res = res.replace(/<h2 id="guide-correlate">Guide correlate<\/h2>/g, `<h2 id="guide-correlate">${ah.h2_related}</h2>`);
    res = res.replace(/<span>Mostra indice articoli<\/span>/g, `<span>${ah.mobile_toc}</span>`);

    // Desktop Table of Contents (TOC)
    res = res.replace(/<div class="docs-toc-title">In questa pagina<\/div>/g, `<div class="docs-toc-title">${ah.toc_title}</div>`);
    res = res.replace(/<a href="#cosa-permette-di-fare" class="docs-toc-link">Cosa permette di fare<\/a>/g, `<a href="#cosa-permette-di-fare" class="docs-toc-link">${ah.toc_what}</a>`);
    res = res.replace(/<a href="#i-4-pilastri-operativi" class="docs-toc-link">I 4 pilastri operativi<\/a>/g, `<a href="#i-4-pilastri-operativi" class="docs-toc-link">${ah.toc_pillars}</a>`);
    res = res.replace(/<a href="#il-ciclo-di-vita-del-messaggio" class="docs-toc-link">Ciclo di vita messaggio<\/a>/g, `<a href="#il-ciclo-di-vita-del-messaggio" class="docs-toc-link">${ah.toc_pipeline}</a>`);
    res = res.replace(/<a href="#limitazioni-reali" class="docs-toc-link">Limitazioni reali<\/a>/g, `<a href="#limitazioni-reali" class="docs-toc-link">${ah.toc_limits}</a>`);
    res = res.replace(/<a href="#risoluzione-dei-problemi" class="docs-toc-link">Risoluzione dei problemi<\/a>/g, `<a href="#risoluzione-dei-problemi" class="docs-toc-link">${ah.toc_troubleshooting}</a>`);
    res = res.replace(/<a href="#approfondimento-tecnico" class="docs-toc-link">Approfondimento tecnico<\/a>/g, `<a href="#approfondimento-tecnico" class="docs-toc-link">${ah.toc_tech}</a>`);
    res = res.replace(/<a href="#guide-correlate" class="docs-toc-link">Guide correlate<\/a>/g, `<a href="#guide-correlate" class="docs-toc-link">${ah.toc_related}</a>`);

    // Feedback box
    res = res.replace(/<span class="docs-feedback-label">Questa guida ti è stata utile\?<\/span>/g, `<span class="docs-feedback-label">${ah.feedback_label}</span>`);
    res = res.replace(/👍 Sì, molto chiara/g, ah.feedback_yes);
    res = res.replace(/👎 No, serve chiarimento/g, ah.feedback_no);
    res = res.replace(/Grazie per il tuo riscontro! Aiuta a migliorare la documentazione\./g, ah.feedback_thanks);
  }

  return res;
}

function copyLocalesToWeb(namespaces = null) {
  const destWeb = path.join(WEB_DIR, 'locales');
  const destLanding = path.join(WEB_DIR, 'landing', 'locales');
  ensureDir(destWeb);
  ensureDir(destLanding);

  if (Array.isArray(namespaces)) {
    for (const lang of SUPPORTED_LANGS) {
      for (const namespace of namespaces) {
        const filename = `${namespace}.json`;
        const srcPath = path.join(LOCALES_DIR, lang, filename);
        if (!fs.existsSync(srcPath)) {
          throw new Error(`Missing locale source: ${srcPath}`);
        }
        for (const destinationRoot of [destWeb, destLanding]) {
          const destination = path.join(destinationRoot, lang);
          ensureDir(destination);
          fs.copyFileSync(srcPath, path.join(destination, filename));
        }
      }
    }
    console.log(`  ✓ Synced ${namespaces.join(', ')} locale bundle(s) to web and web/landing`);
    return;
  }

  function copyRecursive(src, dest) {
    ensureDir(dest);
    const entries = fs.readdirSync(src, { withFileTypes: true });
    for (const entry of entries) {
      const srcPath = path.join(src, entry.name);
      const destPath = path.join(dest, entry.name);
      if (entry.isDirectory()) {
        copyRecursive(srcPath, destPath);
      } else {
        fs.copyFileSync(srcPath, destPath);
      }
    }
  }

  copyRecursive(LOCALES_DIR, destWeb);
  copyRecursive(LOCALES_DIR, destLanding);
  console.log('  ✓ Synced /locales to web/locales and web/landing/locales');
}

function getTargetOutputPath(routeKey, lang) {
  const route = ROUTE_MAP[routeKey];
  const urlPath = route[lang];
  
  if (lang === 'it') {
    const config = PAGES_CONFIG.find(p => p.key === routeKey);
    return config ? config.sourceFile : null;
  }

  const cleanPath = urlPath.replace(/^\/|\/$/g, '');
  return path.join(WEB_DIR, 'landing', cleanPath, 'index.html');
}

function buildAll() {
  console.log('🚀 [Melpis Static i18n Builder] Starting compilation of localized pages...\n');

  // 1. Sync translation bundles into web/locales
  copyLocalesToWeb();

  // These four locales retain their published V1 pages. They are maintained
  // directly so the Italian V1 templates can be removed.
  for (const key of ['home', 'pricing']) {
    for (const lang of SUPPORTED_LANGS.filter(lang => lang !== 'it')) {
      const page = getTargetOutputPath(key, lang);
      if (!fs.existsSync(page)) throw new Error(`Missing maintained page: ${key}/${lang}`);
    }
  }

  let totalPagesBuilt = 0;

  for (const page of PAGES_CONFIG) {
    if (!fs.existsSync(page.sourceFile)) {
      console.warn(`⚠️ Source file not found: ${page.sourceFile}`);
      continue;
    }

    // Legal translations are reviewed documents, not mechanically translated
    // landing pages. Keep their locale-specific HTML intact on every build.
    if (['privacy', 'terms', 'cookies'].includes(page.key)) {
      for (const lang of SUPPORTED_LANGS) {
        const legalPath = getTargetOutputPath(page.key, lang);
        if (!legalPath || !fs.existsSync(legalPath)) {
          throw new Error(`Missing reviewed legal page: ${page.key}/${lang}`);
        }
      }
      console.log(`  ✓ Preserved 5 reviewed locales for [${page.key}]`);
      continue;
    }

    const sourceHtml = fs.readFileSync(page.sourceFile, 'utf8');

    for (const lang of SUPPORTED_LANGS) {
      const bundle = loadLocales(lang);
      let localizedHtml = sourceHtml;

      // 1. Update SEO headers (canonical, hreflang, lang attr, meta tags)
      localizedHtml = updateSeoHead(localizedHtml, page.key, lang, bundle);

      // 2. Localize navigation and footer
      localizedHtml = localizeNav(localizedHtml, lang, bundle);
      localizedHtml = localizeFooter(localizedHtml, lang, bundle);

      // 3. Page-specific body content localization
      if (page.key === 'restaurants' || page.key === 'beauty' || page.key === 'medical' || page.key === 'hotels') {
        localizedHtml = localizeSector(localizedHtml, page.key, lang, bundle);
      } else if (page.key === 'login' || page.key === 'register') {
        localizedHtml = localizeAuth(localizedHtml, page.key, lang, bundle);
      } else if (page.key === 'privacy' || page.key === 'terms' || page.key === 'cookies') {
        localizedHtml = localizeLegal(localizedHtml, page.key, lang);
      } else if (page.key === 'docs') {
        localizedHtml = localizeDocs(localizedHtml, lang, bundle);
      }

      // 4. Update internal relative links
      localizedHtml = updateInternalLinks(localizedHtml, lang);

      // 5. Inject Language Selector (desktop and mobile) - placed after updateInternalLinks so language links retain exact target routes
      localizedHtml = injectLanguageSelector(localizedHtml, page.key, lang);

      // 6. Compute output paths
      const outPath = getTargetOutputPath(page.key, lang);
      if (outPath) {
        ensureDir(path.dirname(outPath));
        fs.writeFileSync(outPath, localizedHtml, 'utf8');
        totalPagesBuilt++;

        // For auth pages, mirror also directly to web/{cleanPath}/index.html
        if (page.key === 'login' || page.key === 'register') {
          const route = ROUTE_MAP[page.key];
          const cleanPath = route[lang].replace(/^\/|\/$/g, '');
          const altAuthPath = path.join(WEB_DIR, cleanPath, 'index.html');
          ensureDir(path.dirname(altAuthPath));
          fs.writeFileSync(altAuthPath, localizedHtml, 'utf8');
        }
      }
    }
    console.log(`  ✓ Built 5 locales for [${page.key}]`);
  }

  console.log(`\n✅ Finished generating ${totalPagesBuilt} localized HTML files successfully!\n`);
  generateMultilingualSitemap();
  extractInlineAssets();
}

function generateMultilingualSitemap() {
  const sitemapPath = path.join(WEB_DIR, 'landing', 'sitemap.xml');
  // SOURCE_DATE_EPOCH is the reproducible-build standard.  The tracked
  // fallback only changes with an intentional source edit, never at runtime.
  const sourceDateEpoch = Number(process.env.SOURCE_DATE_EPOCH || 0);
  const today = sourceDateEpoch > 0
    ? new Date(sourceDateEpoch * 1000).toISOString().split('T')[0]
    : '2026-09-15';

  let xml = `<?xml version="1.0" encoding="UTF-8"?>\n`;
  xml += `<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"\n`;
  xml += `        xmlns:xhtml="http://www.w3.org/1999/xhtml">\n`;

  // 1. Marketing and Sector pages with all localized variants
  for (const [routeKey, routes] of Object.entries(ROUTE_MAP)) {
    const priority = (routeKey === 'home') ? '1.0' : (routeKey === 'pricing' ? '0.9' : (routeKey.startsWith('settori') || routeKey === 'restaurants' || routeKey === 'beauty' || routeKey === 'medical' || routeKey === 'hotels' ? '0.8' : '0.7'));
    const changefreq = (routeKey === 'home' || routeKey === 'pricing') ? 'weekly' : 'monthly';

    const indexedLanguages = SUPPORTED_LANGS.filter(lang => !SITEMAP_EXCLUDED_PATHS.has(routes[lang]));
    for (const lang of indexedLanguages) {
      const locUrl = `${DOMAIN}${routes[lang]}`;
      xml += `  <url>\n`;
      xml += `    <loc>${locUrl}</loc>\n`;
      for (const altLang of indexedLanguages) {
        xml += `    <xhtml:link rel="alternate" hreflang="${altLang}" href="${DOMAIN}${routes[altLang]}" />\n`;
      }
      xml += `    <xhtml:link rel="alternate" hreflang="x-default" href="${DOMAIN}${routes.en || routes.it}" />\n`;
      xml += `    <lastmod>${today}</lastmod>\n`;
      xml += `    <changefreq>${changefreq}</changefreq>\n`;
      xml += `    <priority>${priority}</priority>\n`;
      xml += `  </url>\n`;
    }
  }

  // 2. Documentation articles (source paths)
  const docsDir = path.join(WEB_DIR, 'landing', 'documentazione');
  function scanDocs(dir, relativePath = '') {
    const entries = fs.readdirSync(dir, { withFileTypes: true });
    for (const entry of entries) {
      if (entry.isDirectory()) {
        scanDocs(path.join(dir, entry.name), path.join(relativePath, entry.name));
      } else if (entry.name === 'index.html' && relativePath) {
        const cleanRel = relativePath.replace(/\\/g, '/');
        const docUrl = `${DOMAIN}/documentazione/${cleanRel}/`;
        xml += `  <url>\n`;
        xml += `    <loc>${docUrl}</loc>\n`;
        xml += `    <xhtml:link rel="alternate" hreflang="it" href="${docUrl}" />\n`;
        xml += `    <xhtml:link rel="alternate" hreflang="x-default" href="${docUrl}" />\n`;
        xml += `    <lastmod>${today}</lastmod>\n`;
        xml += `    <changefreq>monthly</changefreq>\n`;
        xml += `    <priority>0.7</priority>\n`;
        xml += `  </url>\n`;
      }
    }
  }
  if (fs.existsSync(docsDir)) {
    scanDocs(docsDir);
  }

  xml += `</urlset>\n`;
  fs.writeFileSync(sitemapPath, xml, 'utf8');
  console.log(`  ✓ Successfully updated multilingual sitemap at web/landing/sitemap.xml`);
}

// Export functions for test suite and runner
module.exports = {
  ROUTE_MAP,
  SUPPORTED_LANGS,
  SITEMAP_EXCLUDED_PATHS,
  generateHreflangs,
  generateLanguageSelector,
  generateMultilingualSitemap,
  copyLocalesToWeb,
  loadLocales,
  buildAll
};

if (require.main === module) {
  buildAll();
}
