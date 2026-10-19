#!/usr/bin/env node
/**
 * Melpis — Static i18n Site Generator
 * Generates fully translated, pre-rendered localized HTML for public marketing pages,
 * sectors, pricing, docs hub, legal pages, and auth templates.
 * Injects self-canonicals, reciprocal hreflangs, and dynamic routeMap-aware language switchers.
 */

const fs = require('fs');
const path = require('path');
const { extractInlineAssets } = require('./extract-inline-assets');
const {
  LANDING_EXTRAS,
  PRICING_EXTRAS,
  SECTOR_SHARED,
  SECTOR_EXTRAS,
  DOCS_EXTRAS
} = require('./i18n-extra-copy');

const ROOT_DIR = path.resolve(__dirname, '..');
const LOCALES_DIR = path.join(ROOT_DIR, 'locales');
const WEB_DIR = path.join(ROOT_DIR, 'web');
const DOMAIN = 'https://melpis.it';

const SUPPORTED_LANGS = ['it', 'en', 'es', 'fr', 'de'];

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
  { key: 'home', sourceFile: path.join(WEB_DIR, 'landing', 'index.html') },
  { key: 'pricing', sourceFile: path.join(WEB_DIR, 'landing', 'prezzi', 'index.html') },
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
  if (routeKey === 'home' && bundle.landing) {
    return { title: bundle.landing.page_title, description: bundle.landing.meta_description };
  }
  if (routeKey === 'pricing' && bundle.pricing) {
    return { title: bundle.pricing.page_title, description: bundle.pricing.meta_description };
  }
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

const LANDING_COPY = {
  en: {
    heroH1: '<span class="hero-h1-lead">Fewer messages to manage.</span><span class="hero-h1-tail">More time to grow your business.</span>',
    heroSub: "Melpis answers customers, organizes bookings, and automates repetitive work on WhatsApp, Instagram, and other channels.",
    ctaPrimary: "Start free",
    ctaSecondary: "See how it works",
    badge7Days: "7-day free trial",
    badgeNoCard: "No card required",
    badgeMeta: "Official Meta Cloud API",
    ambLeft: ["AUTOMATION", "FOR SMART", "BUSINESS"],
    ambRight: ["MORE", "TIME", "FOR WHAT", "MATTERS"],
    ambBottom: "TURN CONVERSATIONS INTO REVENUE",
    narrativeTitle: "How Melpis automates your business conversations",
    sat1Wa: "Manage every inbound inquiry, without hiring extra staff",
    sat2Avail: "No inquiries lost at 2 AM — instant answers, around the clock",
    sat3Rules: "Trained on your industry rules — not a generic chatbot",
    sat4Lang: "Welcomes every customer in their native language with automatic detection",
    climaxGrandTitle: "From chat inquiry to calendar booking<br>in seconds, without touching your phone",
    climaxLead: "Melpis reads the inquiry, checks Google Calendar, and confirms with the customer — you simply receive a notification with the booked event.",
    climaxSatLabel: "Availability check · Google Calendar",
    climaxDomHead: "Booking confirmed · replied in 3s",
    climaxSyncNote: "Event created on calendar · notification sent to team",
    portalHeadline: "Your customers deserve answers in 30 seconds.<br>Not hours waiting on hold.",
    portalSub: "Customers reach out on WhatsApp expecting instant replies. Melpis answers using strictly your business data — hours, catalogues, policies — without making anything up.",
    coworkerPill: "For restaurants, hotels, salons, clinics, and appointment-based businesses",
    coworkerHeadline: "Automate inquiries,<br>\n                        <span class=\"headline-nowrap\">bookings, and reviews.</span><br>\n                        <span class=\"headline-muted\">Without losing control.</span>",
    coworkerDesc: "Melpis handles customer inquiries on WhatsApp and Instagram, schedules appointments on your calendar, and automates repetitive tasks. Your team steps in only when needed.",
    coworkerCta: "Try Melpis for free",
    coworkerMicro: "Setup in under 10 minutes · No credit card required",
    indPills: { r: "Restaurants", h: "Hotels", b: "Barber shops", s: "Beauty salons", o: "Other services" },
    benefit1Title: "Ready in 10 minutes",
    benefit1Sub: "Connect your channels and start without<br>altering your existing workflow.",
    benefit2Title: "Tailored to your industry",
    benefit2Sub: "Restaurants, hotels, salons, clinics,<br>and appointment-based businesses.",
    benefit3Title: "You stay in full control",
    benefit3Sub: "Melpis automates repetitive tasks,<br>but you can jump in at any time.",
    kbEyebrow: "BUSINESS KNOWLEDGE &amp; RULES",
    kbTitle: "Answers only using your verified data.<br><span class=\"nowrap\">And when unsure, consults your team.</span>",
    kbDesc: "Melpis never invents facts or improvises: it references your PDFs, catalogues, pricing, and business rules. When an inquiry falls outside your guidelines, it politely informs the customer and alerts your staff immediately.",
    kbBullets: [
      "Support for PDFs, daily specials, price catalogues, and operating hours",
      "Recognition of your specific services, shifts, and booking policies",
      "Courteous notice and instant staff alert when custom arrangements are required"
    ],
    kbCta1: "Try free for 7 days",
    kbCta2: "How it works (60 sec)",
    kbMicro1: "No credit card",
    kbMicro2: "Setup in minutes",
    kbMicro3: "Your data stays yours",
    syncEyebrow: "Automation &amp; Calendar Sync",
    syncTitle: "Avoid scheduling clashes.<br>No more juggling multiple calendars.",
    syncDesc: "Every appointment arranged via WhatsApp instantly syncs to Google Calendar after checking open slots in real time. Minimize clashes and human mistakes.",
    syncBullets: [
      "Instant two-way synchronization with Google Calendar",
      "Automated availability checks: reduces the risk of double-booked tables or time slots",
      "Ready-to-send reply drafts for post-service Google Reviews matching your brand voice"
    ],
    syncMore: "Explore all native integrations",
    hitlTitle: "Your team always stays in the driver's seat",
    hitlLead: "AI never operates as an opaque black box. Monitor chats in real time from the Unified Inbox, step in with one click, and reclaim full control whenever you want.",
    hitlPillarTitle: "Instant human takeover",
    hitlBadge: "Featured",
    hitlPillarDesc: "If a customer submits a complex request, asks for a human, or needs custom terms, the assistant pauses automation and alerts your staff right away.",
    hitlMeta: "Instant switch: 0s",
    hitlMeta2: "Real-time message tracking",
    hitlSub1Title: "Custom Tone &amp; Guidelines",
    hitlSub1Desc: "Define confidence levels, operational boundaries, and escalation protocols.",
    hitlSub2Title: "Privacy &amp; Data Security",
    hitlSub2Desc: "Strict tenant isolation, data encryption at rest, and zero third-party AI training.",
    pricingEyebrow: "Simple, Transparent Pricing",
    pricingTitle: "Choose the ideal plan for your business",
    pricingLead: "Get started with a 7-day free trial. No credit card required, cancel anytime in a single click.",
    monthlyToggle: "Monthly Billing",
    annualToggle: "Annual",
    planEssentialTitle: "Essential",
    planEssentialDesc: "For small businesses and independent professionals taking their first steps with automation.",
    planGrowthTitle: "Growth",
    planGrowthDesc: "Ideal for restaurants, salons, and medical practices with steady customer flow.",
    planGrowthPopular: "Most Popular",
    planScaleTitle: "Scale",
    planScaleDesc: "For hotels, large clinics, and high-volume businesses with bespoke requirements.",
    finalCtaTitle: "Try Melpis on your business for 7 days.",
    finalCtaSub: "Connect WhatsApp, set your business rules, and watch it handle customers seamlessly. No card required.",
    finalCtaBtn: "Try Melpis free for 7 days",
    finalCtaMicro: "10-min setup · No credit card required · Always stay in control",
    modalBadge: "7-Day Free Trial",
    modalTitle: "Activate your Melpis assistant",
    modalDesc: "No credit card required. Enter your email to begin the guided setup in just a few steps.",
    modalEmailLabel: "Business email",
    modalVerticalLabel: "Industry",
    modalOptions: {
      ristorante_pizzeria: "Restaurant / Pizzeria",
      salone_parrucchiere_beauty: "Beauty Salon / Hairdresser / Spa",
      studio_medico_dentista: "Medical Clinic / Dentist / Healthcare",
      hotel_bnb: "Hotel / B&amp;B / Hospitality",
      palestra_fitness: "Gym / Fitness / Sports Center",
      altro: "Other service business"
    },
    modalSubmit: "Start free trial",
    modalFootnote: "Already have an account?",
    modalFootnoteLink: "Log in to dashboard",
    footerTagline: "The AI WhatsApp assistant for service businesses.",
    footerPlatform: "Platform",
    footerDashboard: "Dashboard",
    wfShowcaseTag: "Melpis in action",
    wfCardTitle1: "1. Customer reaches out",
    wfMsgClient: "Hi! Do you have a table<br>for 4 tonight at 8:30 PM?",
    wfCardTitle2: "2. Melpis responds",
    wfCardSub2: "Instant automated reply",
    wfMsgAi: "Of course! Table confirmed<br>for 4 tonight at 8:30 PM.<br>See you soon! 🎉",
    wfCardTitle3: "3. Booking scheduled",
    wfCalTitle: "Table for 4 - Mark Smith",
    wfCalDate: "Tonight, 8:30 PM",
    wfCardTitle4: "4. Review request",
    wfCardSub4: "Post-service followup",
    wfReviewText: "How was your experience?<br>Leave us a review 🙏",
    wfReviewBtn: "Leave a review",
    wfQuote: "More customers. Less effort.<br>Sustainable growth.",
    wfProofText: "Happier customers"
  },
  es: {
    heroH1: '<span class="hero-h1-lead">Menos mensajes que gestionar.</span><span class="hero-h1-tail">Más tiempo para hacer crecer tu negocio.</span>',
    heroSub: "Melpis responde a tus clientes, organiza las reservas y automatiza el trabajo repetitivo en WhatsApp, Instagram y otros canales.",
    ctaPrimary: "Empieza gratis",
    ctaSecondary: "Cómo funciona",
    badge7Days: "7 días gratis",
    badgeNoCard: "Sin tarjeta requerida",
    badgeMeta: "API oficial Meta Cloud",
    ambLeft: ["AUTOMATIZACIÓN", "PARA QUIEN", "EMPRENDE"],
    ambRight: ["MÁS", "TIEMPO", "PARA LO QUE", "IMPORTA"],
    ambBottom: "DE CONVERSACIONES A OPORTUNIDADES",
    narrativeTitle: "Cómo Melpis automatiza las conversaciones de tu negocio",
    sat1Wa: "Gestiona cada mensaje entrante, sin contratar personal extra",
    sat2Avail: "Ninguna consulta perdida a las 2 de la madrugada — respuestas inmediatas, siempre",
    sat3Rules: "Conoce las reglas de tu sector — no responde como un bot genérico",
    sat4Lang: "Atiende a cada cliente en su idioma, sin configuraciones complejas",
    climaxGrandTitle: "Del mensaje de chat a la cita confirmada<br>en segundos, sin tocar el teléfono",
    climaxLead: "Melpis lee la consulta, revisa Google Calendar y responde al cliente — tú solo recibes la notificación con el evento creado.",
    climaxSatLabel: "Control de disponibilidad · Google Calendar",
    climaxDomHead: "Reserva confirmada · respuesta en 3s",
    climaxSyncNote: "Evento creado en agenda · notificación enviada al equipo",
    portalHeadline: "Tus clientes merecen respuestas en 30 segundos.<br>No horas de espera al teléfono.",
    portalSub: "Los clientes escriben por WhatsApp esperando respuestas al instante. Melpis responde usando solo los datos de tu negocio — horarios, tarifas, reglas — sin inventar nada.",
    coworkerPill: "Para restaurantes, hoteles, salones, clínicas y negocios con cita previa",
    coworkerHeadline: "Automatiza consultas,<br>\n                        <span class=\"headline-nowrap\">reservas y reseñas.</span><br>\n                        <span class=\"headline-muted\">Sin perder el control.</span>",
    coworkerDesc: "Melpis atiende a los clientes en WhatsApp e Instagram, organiza citas en el calendario y automatiza tareas repetitivas. Tu equipo solo interviene cuando es necesario.",
    coworkerCta: "Prueba Melpis gratis",
    coworkerMicro: "Configuración en menos de 10 minutos · Sin tarjeta requerida",
    indPills: { r: "Restaurantes", h: "Hoteles", b: "Barberías", s: "Salones de belleza", o: "Otros servicios" },
    benefit1Title: "Listo en 10 minutos",
    benefit1Sub: "Conecta tus canales y empieza sin<br>cambiar tu forma de trabajar.",
    benefit2Title: "Adaptado a tu sector",
    benefit2Sub: "Restaurantes, hoteles, salones, clínicas<br>y servicios con cita previa.",
    benefit3Title: "Mantienes el control total",
    benefit3Sub: "Melpis automatiza tareas repetitivas,<br>pero puedes intervenir cuando quieras.",
    kbEyebrow: "CONOCIMIENTO EMPRESARIAL &amp; REGLAS",
    kbTitle: "Responde solo con tus datos verificados.<br><span class=\"nowrap\">Y cuando no sabe, consulta a tu equipo.</span>",
    kbDesc: "Melpis nunca improvisa ni inventa respuestas: consulta tus PDF, cartas, listas de precios y normativas. Cuando una solicitud supera tus reglas, avisa al cliente y transfiere la consulta a tu equipo.",
    kbBullets: [
      "Soporte para PDF, menús diarios, listas de precios y horarios",
      "Reconocimiento de servicios, turnos y condiciones de tu negocio",
      "Aviso cortés y notificación inmediata al personal ante solicitudes especiales"
    ],
    kbCta1: "Probar gratis 7 días",
    kbCta2: "Cómo funciona (60 seg)",
    kbMicro1: "Sin tarjeta de crédito",
    kbMicro2: "Activación en minutos",
    kbMicro3: "Tus datos son siempre tuyos",
    syncEyebrow: "Automatización &amp; Sincronización",
    syncTitle: "Evita reservas duplicadas.<br>Olvídate de mirar dos calendarios.",
    syncDesc: "Cada cita acordada por WhatsApp se sincroniza al instante con Google Calendar tras verificar los huecos libres en tiempo real. Reduce solapamientos y errores manuales.",
    syncBullets: [
      "Sincronización bidireccional instantánea con Google Calendar",
      "Control automático de disponibilidad: reduce el riesgo de citas o mesas duplicadas",
      "Borradores listos para responder reseñas de Google con el tono de tu marca"
    ],
    syncMore: "Descubre todas las integraciones nativas",
    hitlTitle: "Tu equipo mantiene siempre el control",
    hitlLead: "La IA nunca actúa como una caja negra cerrada. Supervisa las conversaciones en tiempo real desde la bandeja unificada, interviene con un solo clic y retoma el control cuando quieras.",
    hitlPillarTitle: "Pase inmediato a un operador humano",
    hitlBadge: "Destacado",
    hitlPillarDesc: "Si un cliente plantea una consulta compleja, pide hablar con una persona o necesita condiciones especiales, el asistente pausa la automatización y avisa a tu equipo al instante.",
    hitlMeta: "Cambio instantáneo: 0s",
    hitlMeta2: "Seguimiento de mensajes en tiempo real",
    hitlSub1Title: "Tono y Reglas a Medida",
    hitlSub1Desc: "Define niveles de confianza, límites de acción y protocolos operativos.",
    hitlSub2Title: "Privacidad y Seguridad de Datos",
    hitlSub2Desc: "Aislamiento estricto de clientes, cifrado de datos y sin entrenamiento de modelos externos.",
    pricingEyebrow: "Precios Simples y Transparentes",
    pricingTitle: "Elige el plan ideal para tu negocio",
    pricingLead: "Empieza con una prueba gratuita de 7 días. Sin tarjeta requerida, cancela cuando quieras con un solo clic.",
    monthlyToggle: "Facturación Mensual",
    annualToggle: "Anual",
    planEssentialTitle: "Esencial",
    planEssentialDesc: "Para pequeños negocios y autónomos que buscan automatizar sus primeros mensajes.",
    planGrowthTitle: "Crecimiento",
    planGrowthDesc: "Ideal para restaurantes, salones y clínicas con flujo continuo de clientes.",
    planGrowthPopular: "Más Elegido",
    planScaleTitle: "Escala",
    planScaleDesc: "Para hoteles, clínicas y empresas con alto volumen de solicitudes complejas.",
    finalCtaTitle: "Prueba Melpis en tu negocio durante 7 días.",
    finalCtaSub: "Conecta WhatsApp, define tus reglas y comprueba cómo atiende a tus clientes. Sin tarjeta requerida.",
    finalCtaBtn: "Prueba Melpis gratis durante 7 días",
    finalCtaMicro: "Configuración en 10 min · Sin tarjeta requerida · Control total siempre",
    modalBadge: "Prueba Gratuita de 7 Días",
    modalTitle: "Activa tu asistente Melpis",
    modalDesc: "Sin tarjeta requerida. Introduce tu email para comenzar la configuración guiada en pocos pasos.",
    modalEmailLabel: "Email corporativo",
    modalVerticalLabel: "Sector del negocio",
    modalOptions: {
      ristorante_pizzeria: "Restaurante / Pizzería",
      salone_parrucchiere_beauty: "Salón de Belleza / Peluquería / Spa",
      studio_medico_dentista: "Clínica Médica / Dentista / Salud",
      hotel_bnb: "Hotel / B&amp;B / Alojamiento",
      palestra_fitness: "Gimnasio / Fitness / Club Deportivo",
      altro: "Otro negocio de servicios"
    },
    modalSubmit: "Iniciar prueba gratuita",
    modalFootnote: "¿Ya tienes una cuenta?",
    modalFootnoteLink: "Entrar al panel",
    footerTagline: "El asistente de WhatsApp con IA para empresas de servicios.",
    footerPlatform: "Plataforma",
    footerDashboard: "Panel de control",
    wfShowcaseTag: "Melpis en acción",
    wfCardTitle1: "1. El cliente escribe",
    wfMsgClient: "¡Hola! ¿Tienen mesa<br>para 4 esta noche a las 20:30?",
    wfCardTitle2: "2. Melpis responde",
    wfCardSub2: "Respuesta automática",
    wfMsgAi: "¡Claro! Mesa confirmada<br>para 4 esta noche a las 20:30.<br>¡Hasta pronto! 🎉",
    wfCardTitle3: "3. Cita creada",
    wfCalTitle: "Mesa 4P - Marcos Ruiz",
    wfCalDate: "Esta noche, 20:30",
    wfCardTitle4: "4. Solicitud de reseña",
    wfCardSub4: "Tras el servicio",
    wfReviewText: "¿Qué tal ha sido tu experiencia?<br>Déjanos una reseña 🙏",
    wfReviewBtn: "Dejar una reseña",
    wfQuote: "Más clientes. Menos trabajo.<br>Crecimiento continuo.",
    wfProofText: "Clientes más satisfechos"
  },
  fr: {
    heroH1: '<span class="hero-h1-lead">Moins de messages à gérer.</span><span class="hero-h1-tail">Plus de temps pour développer votre activité.</span>',
    heroSub: "Melpis répond aux clients, organise les réservations et automatise le travail répétitif sur WhatsApp, Instagram et d'autres canaux.",
    ctaPrimary: "Commencer gratuitement",
    ctaSecondary: "Comment ça marche",
    badge7Days: "7 jours gratuits",
    badgeNoCard: "Aucune carte requise",
    badgeMeta: "API officielle Meta Cloud",
    ambLeft: ["AUTOMATISATION", "POUR LES", "ENTREPRISES"],
    ambRight: ["PLUS DE", "TEMPS", "POUR L'", "ESSENTIEL"],
    ambBottom: "DES CONVERSATIONS AUX OPPORTUNITÉS",
    narrativeTitle: "Comment Melpis automatise les conversations de votre entreprise",
    sat1Wa: "Gérez chaque message entrant, sans embaucher de personnel",
    sat2Avail: "Aucune demande perdue à 2h du matin — réponses immédiates, en continu",
    sat3Rules: "Maîtrise les règles de votre secteur — bien plus qu'un bot générique",
    sat4Lang: "Accueille chaque client dans sa langue, sans configuration complexe",
    climaxGrandTitle: "Du message dans le chat au rendez-vous dans l'agenda<br>en quelques secondes, sans toucher votre téléphone",
    climaxLead: "Melpis analyse la demande, vérifie Google Calendar et répond au client — vous recevez simplement la notification du rendez-vous.",
    climaxSatLabel: "Vérification des disponibilités · Google Calendar",
    climaxDomHead: "Réservation confirmée · réponse en 3s",
    climaxSyncNote: "Événement créé dans l'agenda · notification envoyée à l'équipe",
    portalHeadline: "Vos clients méritent une réponse en 30 secondes.<br>Pas des heures d'attente au téléphone.",
    portalSub: "Les clients vous écrivent sur WhatsApp et attendent une réponse rapide. Melpis répond en s'appuyant uniquement sur vos données — horaires, tarifs, règles — sans rien inventer.",
    coworkerPill: "Pour restaurants, hôtels, salons de beauté et activités sur rendez-vous",
    coworkerHeadline: "Automatisez demandes,<br>\n                        <span class=\"headline-nowrap\">réservations et avis.</span><br>\n                        <span class=\"headline-muted\">Sans perdre le contrôle.</span>",
    coworkerDesc: "Melpis répond à vos clients sur WhatsApp et Instagram, organise vos rendez-vous dans le calendrier et automatise les tâches répétitives. Vous n'intervenez que si nécessaire.",
    coworkerCta: "Essayer Melpis gratuitement",
    coworkerMicro: "Configuration en moins de 10 minutes · Aucune carte requise",
    indPills: { r: "Restaurants", h: "Hôtels", b: "Salons de coiffure", s: "Instituts de beauté", o: "Autres activités" },
    benefit1Title: "Prêt en 10 minutes",
    benefit1Sub: "Connectez vos canaux et démarrez sans<br>changer vos habitudes de travail.",
    benefit2Title: "Adapté à votre secteur",
    benefit2Sub: "Restaurants, hôtels, salons, cabinets<br>médicaux et services sur rendez-vous.",
    benefit3Title: "Vous gardez le contrôle",
    benefit3Sub: "Melpis automatise les tâches répétitives,<br>mais vous intervenez quand vous le souhaitez.",
    kbEyebrow: "BASE DE CONNAISSANCES &amp; RÈGLES",
    kbTitle: "Répond uniquement avec vos données vérifiées.<br><span class=\"nowrap\">Et en cas de doute, consulte votre équipe.</span>",
    kbDesc: "Melpis n'invente rien et n'improvise jamais : il consulte vos PDF, menus, tarifs et règles d'établissement. Lorsqu'une demande sort de vos critères, il prévient le client et alerte aussitôt votre équipe.",
    kbBullets: [
      "Prise en charge des PDF, menus du jour, tarifs et horaires d'ouverture",
      "Reconnaissance des services, créneaux et conditions de l'établissement",
      "Message courtois et notification immédiate au personnel pour les demandes sur mesure"
    ],
    kbCta1: "Essayer gratuitement 7 jours",
    kbCta2: "Comment ça marche (60 sec)",
    kbMicro1: "Sans carte de crédit",
    kbMicro2: "Activation en quelques minutes",
    kbMicro3: "Vos données restent vôtres",
    syncEyebrow: "Automatisation &amp; Synchronisation",
    syncTitle: "Évitez les surréservations.<br>Ne jonglez plus entre plusieurs agendas.",
    syncDesc: "Chaque rendez-vous convenu sur WhatsApp se synchronise instantanément dans Google Calendar après vérification des créneaux en direct. Limite les conflits d'agenda et les erreurs de saisie.",
    syncBullets: [
      "Synchronisation bidirectionnelle instantanée avec Google Calendar",
      "Contrôle automatique de disponibilité en temps réel pour limiter les conflits de créneaux",
      "Brouillons de réponses aux avis Google post-visite adaptés à votre ton"
    ],
    syncMore: "Découvrir toutes les intégrations natives",
    hitlTitle: "Votre équipe garde toujours la main",
    hitlLead: "L'IA n'opère jamais comme une boîte noire fermée. Suivez les conversations en direct depuis la boîte de réception unifiée, intervenez en un clic et reprenez la main à tout moment.",
    hitlPillarTitle: "Transfert instantané à un conseiller",
    hitlBadge: "En vedette",
    hitlPillarDesc: "Si le client pose une question complexe, demande un humain ou exige des conditions particulières, l'assistant suspend l'automatisation et prévient votre personnel.",
    hitlMeta: "Bascule instantanée : 0s",
    hitlMeta2: "Suivi des messages en temps réel",
    hitlSub1Title: "Ton et Règles sur Mesure",
    hitlSub1Desc: "Définissez le degré d'autonomie, les seuils d'escalade et vos protocoles métier.",
    hitlSub2Title: "Confidentialité &amp; Sécurité des Données",
    hitlSub2Desc: "Isolation stricte des données, chiffrement complet et aucun entraînement de modèles tiers.",
    pricingEyebrow: "Tarifs Clairs et Transparents",
    pricingTitle: "Choisissez le forfait adapté à votre activité",
    pricingLead: "Commencez par un essai gratuit de 7 jours. Sans carte bancaire requise, résiliez à tout moment en un clic.",
    monthlyToggle: "Facturation Mensuelle",
    annualToggle: "Annuelle",
    planEssentialTitle: "Essentiel",
    planEssentialDesc: "Pour les indépendants et petites entreprises souhaitant automatiser leurs premiers messages.",
    planGrowthTitle: "Croissance",
    planGrowthDesc: "Idéal pour restaurants, salons et cabinets médicaux avec un flux continu de clients.",
    planGrowthPopular: "Le Plus Populaire",
    planScaleTitle: "Échelle",
    planScaleDesc: "Pour hôtels, grandes structures et entreprises à fort volume avec des besoins avancés.",
    finalCtaTitle: "Testez Melpis pour votre activité pendant 7 jours.",
    finalCtaSub: "Connectez WhatsApp, définissez vos consignes et découvrez ses réponses aux clients. Aucune carte requise.",
    finalCtaBtn: "Essayer Melpis gratuitement pendant 7 jours",
    finalCtaMicro: "Configuration en 10 min · Aucune carte requise · Contrôle total permanent",
    modalBadge: "Essai Gratuit de 7 Jours",
    modalTitle: "Activez votre assistant Melpis",
    modalDesc: "Aucune carte requise. Saisissez votre e-mail pour démarrer la configuration guidée en quelques étapes.",
    modalEmailLabel: "E-mail professionnel",
    modalVerticalLabel: "Secteur d'activité",
    modalOptions: {
      ristorante_pizzeria: "Restaurant / Pizzeria",
      salone_parrucchiere_beauty: "Salon de Beauté / Coiffure / Spa",
      studio_medico_dentista: "Cabinet Médical / Dentiste / Santé",
      hotel_bnb: "Hôtel / Chambre d'hôtes / Hébergement",
      palestra_fitness: "Salle de Sport / Fitness / Centre Sportif",
      altro: "Autre activité de service"
    },
    modalSubmit: "Démarrer l'essai gratuit",
    modalFootnote: "Vous avez déjà un compte ?",
    modalFootnoteLink: "Accéder au tableau de bord",
    footerTagline: "L'assistant WhatsApp IA pour les entreprises de services.",
    footerPlatform: "Plateforme",
    footerDashboard: "Tableau de bord",
    wfShowcaseTag: "Melpis en action",
    wfCardTitle1: "1. Le client écrit",
    wfMsgClient: "Bonjour ! Avez-vous une table<br>pour 4 ce soir à 20h30 ?",
    wfCardTitle2: "2. Melpis répond",
    wfCardSub2: "Réponse instantanée",
    wfMsgAi: "Bien sûr ! Table confirmée<br>pour 4 ce soir à 20h30.<br>À très vite ! 🎉",
    wfCardTitle3: "3. Rendez-vous créé",
    wfCalTitle: "Table 4P - Marc Dupont",
    wfCalDate: "Ce soir, 20h30",
    wfCardTitle4: "4. Demande d'avis",
    wfCardSub4: "Après la prestation",
    wfReviewText: "Comment s'est passée votre visite ?<br>Donnez-nous votre avis 🙏",
    wfReviewBtn: "Donner un avis",
    wfQuote: "Plus de clients. Moins d'effort.<br>Croissance continue.",
    wfProofText: "Clients plus satisfaits"
  },
  de: {
    heroH1: '<span class="hero-h1-lead">Weniger Nachrichten verwalten.</span><span class="hero-h1-tail">Mehr Zeit, Ihr Unternehmen voranzubringen.</span>',
    heroSub: "Melpis beantwortet Kundenanfragen, organisiert Buchungen und automatisiert Routinearbeit auf WhatsApp, Instagram und weiteren Kanälen.",
    ctaPrimary: "Kostenlos starten",
    ctaSecondary: "So funktioniert's",
    badge7Days: "7 Tage kostenlos",
    badgeNoCard: "Keine Kreditkarte nötig",
    badgeMeta: "Offizielle Meta Cloud API",
    ambLeft: ["AUTOMATION", "FÜR", "UNTERNEHMER"],
    ambRight: ["MEHR", "ZEIT", "FÜR DAS", "WESENTLICHE"],
    ambBottom: "VON NACHRICHTEN ZU ERFOLG",
    narrativeTitle: "Wie Melpis die Kommunikation Ihres Unternehmens automatisiert",
    sat1Wa: "Verwalten Sie jede Nachricht, ohne zusätzliches Personal einzustellen",
    sat2Avail: "Keine verpasste Anfrage um 2 Uhr nachts — sofortige Antworten, rund um die Uhr",
    sat3Rules: "Kennt Ihre Branchenregeln — antwortet nicht wie ein generischer Bot",
    sat4Lang: "Begrüßt jeden Kunden in seiner Sprache, ohne Mehraufwand",
    climaxGrandTitle: "Von der Chat-Nachricht zum Kalendertermin<br>in Sekunden, ohne das Smartphone in die Hand zu nehmen",
    climaxLead: "Melpis erfasst die Anfrage, prüft Google Calendar und antwortet dem Kunden — Sie erhalten lediglich die Benachrichtigung über den gebuchten Termin.",
    climaxSatLabel: "Verfügbarkeitsprüfung · Google Calendar",
    climaxDomHead: "Buchung bestätigt · Antwort in 3s",
    climaxSyncNote: "Termin im Kalender erstellt · Benachrichtigung an das Team gesendet",
    portalHeadline: "Ihre Kunden verdienen Antworten in 30 Sekunden.<br>Keine stundenlange Warteschleife am Telefon.",
    portalSub: "Kunden schreiben über WhatsApp und erwarten sofortige Antworten. Melpis antwortet ausschließlich auf Basis Ihrer Unternehmensdaten — Öffnungszeiten, Preislisten, Richtlinien — ohne zu halluzinieren.",
    coworkerPill: "Für Restaurants, Hotels, Studios, Praxen und terminbasierte Betriebe",
    coworkerHeadline: "Automatisieren Sie Anfragen,<br>\n                        <span class=\"headline-nowrap\">Buchungen und Bewertungen.</span><br>\n                        <span class=\"headline-muted\">Ohne Kontrollverlust.</span>",
    coworkerDesc: "Melpis antwortet Kunden auf WhatsApp und Instagram, trägt Termine im Kalender ein und automatisiert Routineaufgaben. Sie greifen nur ein, wenn es nötig ist.",
    coworkerCta: "Melpis kostenlos testen",
    coworkerMicro: "Einrichtung in unter 10 Minuten · Keine Kreditkarte nötig",
    indPills: { r: "Restaurants", h: "Hotels", b: "Barbershops", s: "Kosmetikstudios", o: "Weitere Dienstleister" },
    benefit1Title: "In 10 Minuten startklar",
    benefit1Sub: "Verbinden Sie Ihre Kanäle und starten Sie,<br>ohne Ihre Arbeitsweise umzustellen.",
    benefit2Title: "Auf Ihre Branche zugeschnitten",
    benefit2Sub: "Restaurants, Hotels, Friseure, Praxen<br>und terminbasierte Unternehmen.",
    benefit3Title: "Sie behalten die Kontrolle",
    benefit3Sub: "Melpis automatisiert Routineaufgaben,<br>doch Sie können jederzeit eingreifen.",
    kbEyebrow: "UNTERNEHMENSWISSEN &amp; REGELN",
    kbTitle: "Antwortet nur mit Ihren verifizierten Daten.<br><span class=\"nowrap\">Und wenn unklar, fragt es Ihr Team.</span>",
    kbDesc: "Melpis halluziniert nicht und improvisiert nie: Es stützt sich strikt auf Ihre PDFs, Menüs, Preislisten und Vorgaben. Überschreitet eine Anfrage die definierten Grenzen, informiert es den Kunden und leitet direkt an Ihr Team weiter.",
    kbBullets: [
      "Unterstützung für PDFs, Tageskarten, Preislisten und Öffnungszeiten",
      "Erkennung Ihrer Dienstleistungen, Schichten und Buchungsbedingungen",
      "Höfliche Rückmeldung und sofortige Benachrichtigung des Personals bei Sonderwünschen"
    ],
    kbCta1: "7 Tage kostenlos testen",
    kbCta2: "So funktioniert's (60 Sek.)",
    kbMicro1: "Keine Kreditkarte nötig",
    kbMicro2: "Aktivierung in Minuten",
    kbMicro3: "Ihre Daten bleiben Ihre Daten",
    syncEyebrow: "Automation &amp; Kalender-Sync",
    syncTitle: "Terminüberschneidungen vermeiden.<br>Kein lästiges Prüfen zweier Kalender mehr.",
    syncDesc: "Jeder per WhatsApp vereinbarte Termin wird sofort in Google Calendar synchronisiert, nachdem freie Zeitfenster in Echtzeit geprüft wurden. Minimiert Überschneidungen und manuelle Übertragungsfehler.",
    syncBullets: [
      "Sofortige bidirektionale Synchronisierung mit Google Calendar",
      "Automatische Verfügbarkeitsprüfung zur Vermeidung von Terminkollisionen",
      "Entwürfe für Google-Bewertungen nach dem Besuch im passenden Unternehmenston"
    ],
    syncMore: "Alle nativen Integrationen entdecken",
    hitlTitle: "Ihr Team behält stets die Regie",
    hitlLead: "Die KI agiert niemals als undurchsichtige Blackbox. Verfolgen Sie Unterhaltungen live im gemeinsamen Posteingang, schalten Sie sich mit einem Klick ein und übernehmen Sie jederzeit die Führung.",
    hitlPillarTitle: "Sofortige Weiterleitung an Mitarbeiter",
    hitlBadge: "Hervorgehoben",
    hitlPillarDesc: "Stellt ein Kunde eine komplexe Anfrage, wünscht einen menschlichen Ansprechpartner oder Sonderkonditionen, pausiert der Assistent und benachrichtigt Ihr Team sofort.",
    hitlMeta: "Sofortige Umschaltung: 0s",
    hitlMeta2: "Lückenlose Nachrichtenübersicht",
    hitlSub1Title: "Maßgeschneiderter Ton &amp; Regeln",
    hitlSub1Desc: "Legen Sie Vertrauensschwellen, Aktionsgrenzen und Protokolle für jeden Fall fest.",
    hitlSub2Title: "Datenschutz &amp; Datensicherheit",
    hitlSub2Desc: "Strikte Mandantentrennung, Verschlüsselung und kein Training externer KI-Modelle.",
    pricingEyebrow: "Einfache &amp; transparente Preise",
    pricingTitle: "Wählen Sie den passenden Tarif für Ihren Betrieb",
    pricingLead: "Starten Sie mit 7 Tagen kostenloser Testphase. Keine Kreditkarte nötig, jederzeit mit einem Klick kündbar.",
    monthlyToggle: "Monatliche Abrechnung",
    annualToggle: "Jährlich",
    planEssentialTitle: "Basis",
    planEssentialDesc: "Für kleinere Betriebe und Selbstständige, die erste Kundenanfragen automatisieren möchten.",
    planGrowthTitle: "Wachstum",
    planGrowthDesc: "Ideal für Restaurants, Salons und Arztpraxen mit kontinuierlichem Kundenaufkommen.",
    planGrowthPopular: "Meistgewählt",
    planScaleTitle: "Scale",
    planScaleDesc: "Für Hotels, Kliniken und stark ausgelastete Betriebe mit anspruchsvollen Anforderungen.",
    finalCtaTitle: "Testen Sie Melpis 7 Tage lang unverbindlich in Ihrem Betrieb.",
    finalCtaSub: "Verbinden Sie WhatsApp, hinterlegen Sie Ihre Regeln und erleben Sie den Kundenservice live. Keine Kreditkarte nötig.",
    finalCtaBtn: "Melpis 7 Tage kostenlos testen",
    finalCtaMicro: "Einrichtung in 10 Min. · Keine Kreditkarte nötig · Volle Kontrolle jederzeit",
    modalBadge: "7 Tage kostenlose Testphase",
    modalTitle: "Aktivieren Sie Ihren Melpis-Assistenten",
    modalDesc: "Keine Kreditkarte nötig. Geben Sie Ihre E-Mail ein, um die geführte Einrichtung in wenigen Schritten zu starten.",
    modalEmailLabel: "Geschäftliche E-Mail",
    modalVerticalLabel: "Branche",
    modalOptions: {
      ristorante_pizzeria: "Restaurant / Pizzeria",
      salone_parrucchiere_beauty: "Kosmetiksalon / Friseur / Spa",
      studio_medico_dentista: "Arztpraxis / Zahnarzt / Gesundheit",
      hotel_bnb: "Hotel / Pension / Beherbergung",
      palestra_fitness: "Fitnessstudio / Sportzentrum",
      altro: "Sonstiges Dienstleistungsunternehmen"
    },
    modalSubmit: "Kostenlose Testphase starten",
    modalFootnote: "Bereits registriert?",
    modalFootnoteLink: "Zur Anmeldeseite",
    footerTagline: "Der WhatsApp-KI-Assistent für Dienstleistungsbetriebe.",
    footerPlatform: "Plattform",
    footerDashboard: "Dashboard",
    wfShowcaseTag: "Melpis im Einsatz",
    wfCardTitle1: "1. Der Kunde schreibt",
    wfMsgClient: "Hallo! Haben Sie heute<br>einen Tisch für 4 um 20:30 Uhr?",
    wfCardTitle2: "2. Melpis antwortet",
    wfCardSub2: "Automatische Antwort",
    wfMsgAi: "Sehr gerne! Tisch für 4<br>heute um 20:30 Uhr bestätigt.<br>Bis gleich! 🎉",
    wfCardTitle3: "3. Termin eingetragen",
    wfCalTitle: "Tisch 4P - Markus Bauer",
    wfCalDate: "Heute, 20:30 Uhr",
    wfCardTitle4: "4. Bewertungsanfrage",
    wfCardSub4: "Nach dem Besuch",
    wfReviewText: "Wie war Ihr Besuch bei uns?<br>Hinterlassen Sie eine Bewertung 🙏",
    wfReviewBtn: "Bewertung abgeben",
    wfQuote: "Mehr Gäste. Weniger Aufwand.<br>Stetiges Wachstum.",
    wfProofText: "Zufriedenere Kunden"
  }
};

const PRICING_PAGE_COPY = {
  en: {
    heroBadge: "Transparent pricing, no hidden fees",
    heroTitle: "Simple plans for businesses of any size",
    heroSub: "Start with a 7-day free trial: no credit card required, cancel anytime.",
    trustChip1: "7-day free trial",
    trustChip2: "No card required",
    trustChip3: "Official Meta Cloud API",
    volumeLabel: "Monthly volume",
    volEss: "Up to 500 managed messages / month",
    volGro: "Up to 2,000 managed messages / month",
    volSca: "Up to 10,000 managed messages / month",
    featEss: [
      "1 official WhatsApp Business number",
      "Standard knowledge base",
      "Google Calendar sync",
      "Manual staff takeover",
      "Standard email support"
    ],
    featGro: [
      "WhatsApp + Instagram DM",
      "Extended knowledge base",
      "Anti no-show reminders",
      "Google Reviews integration",
      "Up to 3 staff operators",
      "Priority WhatsApp support"
    ],
    featSca: [
      "Multi-channel support",
      "Multi-location support",
      "Location-specific rules",
      "Custom conversational flows",
      "Unlimited staff accounts",
      "Dedicated phone support"
    ],
    customNeeds: "Custom requirements? <a href=\"mailto:info@melpis.it\">Contact us</a>",
    popularLead: "The best balance for most businesses",
    manualEyebrow: "The Value of Automation",
    manualTitle: "How much does doing everything by hand cost you today?",
    manualLead: "No hypothetical formulas or arbitrary guesses: here are the three real problems absorbing time and energy every day in your business.",
    mCard1Title: "Repetitive messages",
    mCard1Desc: "Constant inquiries about hours, pricing, availability, and services continuously interrupting your workday.",
    mCard2Title: "Lost bookings",
    mCard2Desc: "After-hours inquiries or delayed replies when the potential client has already contacted a competitor.",
    mCard3Title: "Staff time",
    mCard3Desc: "Hours spent every week by staff on routine front-desk tasks that Melpis can automate effortlessly.",
    mConclusion: "With Melpis you automate repetitive requests and leave your staff free to focus on what truly requires a human touch.",
    metaEyebrow: "Transparent Rates",
    metaTitle: "Melpis price + WhatsApp fees",
    metaLead: "A clear and fair distinction between the software platform and the actual network provider messaging costs.",
    metaBadge1: "Software Fee",
    metaCard1Title: "Melpis",
    metaCard1High: "Fixed monthly subscription based on chosen plan.",
    metaCard1Desc: "Includes a dedicated AI assistant, calendar and channel integrations, a company knowledge base, staff inbox, and message handling up to your plan limit.",
    metaBadge2: "Official Infrastructure",
    metaCard2Title: "Meta WhatsApp Business Platform",
    metaCard2High: "Variable message fees applied directly by Meta.",
    metaCard2Desc: "Message costs via the official WhatsApp Business API are billed by Meta based on message category (utility, marketing, service) and recipient country.",
    metaBadge3: "Zero Surprises",
    metaCard3Title: "No hidden Melpis markups",
    metaCard3High: "You always know what you pay for software; Meta charges are separate and transparent.",
    metaCard3Desc: "Melpis never adds percentage surcharges to your WhatsApp messages: you pay our clear SaaS subscription to us, and only official rates directly to Meta.",
    compEyebrow: "PLAN COMPARISON",
    compTitle: "Detailed feature breakdown",
    compLead: "Compare channels, volumes, automations, and support to select the perfect plan for your business.",
    compFeatCol: "Feature",
    compSubEss: "Everything you need to get started",
    compSubGro: "More capabilities, greater results",
    compSubSca: "Maximum power for your enterprise"
  },
  es: {
    heroBadge: "Precios claros, sin costes ocultos",
    heroTitle: "Planes simples para negocios de cualquier tamaño",
    heroSub: "Empieza con 7 días de prueba gratuita: sin tarjeta de crédito requerida, cancela cuando quieras.",
    trustChip1: "7 días de prueba gratis",
    trustChip2: "Sin tarjeta requerida",
    trustChip3: "API oficial Meta Cloud",
    volumeLabel: "Volumen mensual",
    volEss: "Hasta 500 mensajes gestionados / mes",
    volGro: "Hasta 2.000 mensajes gestionados / mes",
    volSca: "Hasta 10.000 mensajes gestionados / mes",
    featEss: [
      "1 número oficial WhatsApp Business",
      "Base de conocimiento estándar",
      "Sincronización con Google Calendar",
      "Pase manual al personal",
      "Soporte estándar por email"
    ],
    featGro: [
      "WhatsApp + Instagram DM",
      "Base de conocimiento ampliada",
      "Recordatorios anti no-show",
      "Gestión de reseñas de Google",
      "Hasta 3 operadores de equipo",
      "Soporte prioritario por WhatsApp"
    ],
    featSca: [
      "Soporte multicanal",
      "Soporte multisede",
      "Reglas por sede",
      "Flujos conversacionales a medida",
      "Cuentas de personal ilimitadas",
      "Soporte telefónico dedicado"
    ],
    customNeeds: "¿Necesidades a medida? <a href=\"mailto:info@melpis.it\">Habla con nosotros</a>",
    popularLead: "El mejor equilibrio para la mayoría de negocios",
    manualEyebrow: "El Valor de la Automatización",
    manualTitle: "¿Cuánto te cuesta hoy gestionar todo a mano?",
    manualLead: "Sin fórmulas hipotéticas ni estimaciones arbitrarias: aquí están los tres problemas reales que absorben tiempo y energía a diario.",
    mCard1Title: "Mensajes repetitivos",
    mCard1Desc: "Consultas continuas sobre horarios, precios, disponibilidad y servicios que interrumpen constantemente tu trabajo.",
    mCard2Title: "Reservas perdidas",
    mCard2Desc: "Consultas fuera de horario o respuestas tardías cuando el cliente potencial ya ha reservado en otro lugar.",
    mCard3Title: "Tiempo del personal",
    mCard3Desc: "Horas dedicadas cada semana por el equipo a tareas rutinarias de secretaría que Melpis puede gestionar automáticamente.",
    mConclusion: "Con Melpis automatizas las solicitudes repetitivas y reservas a tu equipo solo para lo que realmente exige una persona.",
    metaEyebrow: "Tarifas Transparentes",
    metaTitle: "Precio Melpis + costes de WhatsApp",
    metaLead: "Una distinción clara y transparente entre la plataforma de software y los costes directos del proveedor de mensajería.",
    metaBadge1: "Cuota de Software",
    metaCard1Title: "Melpis",
    metaCard1High: "Suscripción mensual fija según el plan contratado.",
    metaCard1Desc: "Incluye asistente de IA dedicado, integración con calendarios y canales, base de conocimientos, panel para el personal y gestión de mensajes hasta el límite del plan.",
    metaBadge2: "Infraestructura Oficial",
    metaCard2Title: "Meta WhatsApp Business Platform",
    metaCard2High: "Costes variables por mensaje aplicados por Meta.",
    metaCard2Desc: "Las tarifas por mensaje enviado a través de la API oficial de WhatsApp Business las factura Meta según tipo (utilidad, marketing, servicio) y país.",
    metaBadge3: "Cero Sorpresas",
    metaCard3Title: "Sin sobrecostes ocultos en Melpis",
    metaCard3High: "Siempre sabes cuánto pagas por el software; los costes de Meta son independientes y transparentes.",
    metaCard3Desc: "Melpis no aplica ningún recargo porcentual sobre tus mensajes de WhatsApp: nos pagas tu tarifa SaaS y abonas las tarifas oficiales directamente a Meta.",
    compEyebrow: "COMPARATIVA DE PLANES",
    compTitle: "Comparativa detallada de funciones",
    compLead: "Compara canales, volúmenes, automatizaciones y soporte para elegir el plan perfecto para tu negocio.",
    compFeatCol: "Característica",
    compSubEss: "Lo esencial para empezar",
    compSubGro: "Más posibilidades, mayores resultados",
    compSubSca: "Máxima potencia para tu negocio"
  },
  fr: {
    heroBadge: "Tarifs clairs, aucun frais caché",
    heroTitle: "Des forfaits simples pour toute taille d'activité",
    heroSub: "Commencez par un essai gratuit de 7 jours : aucune carte bancaire requise, résiliez à tout moment.",
    trustChip1: "Essai gratuit de 7 jours",
    trustChip2: "Aucune carte requise",
    trustChip3: "API officielle Meta Cloud",
    volumeLabel: "Volume mensuel",
    volEss: "Jusqu'à 500 messages gérés / mois",
    volGro: "Jusqu'à 2 000 messages gérés / mois",
    volSca: "Jusqu'à 10 000 messages gérés / mois",
    featEss: [
      "1 numéro officiel WhatsApp Business",
      "Base de connaissances standard",
      "Synchronisation Google Calendar",
      "Prise en main manuelle par le personnel",
      "Support standard par e-mail"
    ],
    featGro: [
      "WhatsApp + Instagram DM",
      "Base de connaissances étendue",
      "Rappels anti rendez-vous manqués",
      "Gestion des avis Google",
      "Jusqu'à 3 opérateurs d'équipe",
      "Support prioritaire par WhatsApp"
    ],
    featSca: [
      "Support multicanal",
      "Support multi-établissements",
      "Règles par établissement",
      "Parcours conversationnels sur mesure",
      "Comptes d'équipe illimités",
      "Support téléphonique dédié"
    ],
    customNeeds: "Besoins sur mesure ? <a href=\"mailto:info@melpis.it\">Contactez-nous</a>",
    popularLead: "Le meilleur équilibre pour la majorité des entreprises",
    manualEyebrow: "La Valeur de l'Automatisation",
    manualTitle: "Combien vous coûte la gestion manuelle aujourd'hui ?",
    manualLead: "Aucune formule arbitraire : voici les trois problèmes réels qui consomment votre temps et votre énergie chaque jour.",
    mCard1Title: "Messages répétitifs",
    mCard1Desc: "Demandes incessantes sur les horaires, prix, disponibilités et services qui interrompent continuellement vos journées.",
    mCard2Title: "Réservations perdues",
    mCard2Desc: "Demandes reçues en dehors des heures d'ouverture ou réponses tardives quand le client s'est déjà tourné vers un confrère.",
    mCard3Title: "Temps du personnel",
    mCard3Desc: "Des heures passées chaque semaine par vos équipes sur des tâches administratives que Melpis gère sans effort.",
    mConclusion: "Avec Melpis, automatisez les demandes récurrentes et réservez l'attention de vos équipes aux échanges à forte valeur.",
    metaEyebrow: "Transparence Tarifaire",
    metaTitle: "Prix Melpis + coûts WhatsApp",
    metaLead: "Une distinction claire et loyale entre la plateforme logicielle et les coûts réels des opérateurs de messagerie.",
    metaBadge1: "Abonnement Logiciel",
    metaCard1Title: "Melpis",
    metaCard1High: "Forfait mensuel fixe selon l'offre choisie.",
    metaCard1Desc: "Comprend l'assistant IA dédié, l'intégration aux agendas et canaux, la base documentaire, le tableau de bord d'équipe et la gestion des messages.",
    metaBadge2: "Infrastructure Officielle",
    metaCard2Title: "Meta WhatsApp Business Platform",
    metaCard2High: "Frais de message variables facturés par Meta.",
    metaCard2Desc: "Les coûts des messages envoyés via l'API officielle WhatsApp Business sont facturés par Meta selon la catégorie (utilité, marketing, service) et le pays.",
    metaBadge3: "Zéro Surprise",
    metaCard3Title: "Aucune marge cachée Melpis",
    metaCard3High: "Vous savez exactement ce que vous payez pour le logiciel ; les frais Meta restent séparés et transparents.",
    metaCard3Desc: "Melpis n'applique aucune surtaxe ni pourcentage sur vos messages WhatsApp : vous réglez votre abonnement SaaS et payez les tarifs officiels à Meta.",
    compEyebrow: "COMPARATIF DES OFFRES",
    compTitle: "Tableau comparatif détaillé",
    compLead: "Comparez les canaux, volumes, fonctionnalités et niveaux de support pour trouver la formule idéale.",
    compFeatCol: "Fonctionnalité",
    compSubEss: "L'essentiel pour démarrer",
    compSubGro: "Plus de puissance, de meilleurs résultats",
    compSubSca: "La solution intégrale pour votre entreprise"
  },
  de: {
    heroBadge: "Klare Preise, keine versteckten Kosten",
    heroTitle: "Transparente Tarife für Betriebe jeder Größe",
    heroSub: "Starten Sie mit 7 Tagen kostenloser Testphase: keine Kreditkarte erforderlich, jederzeit kündbar.",
    trustChip1: "7 Tage kostenlos testen",
    trustChip2: "Keine Kreditkarte nötig",
    trustChip3: "Offizielle Meta Cloud API",
    volumeLabel: "Monatliches Volumen",
    volEss: "Bis zu 500 verwaltete Nachrichten / Monat",
    volGro: "Bis zu 2.000 verwaltete Nachrichten / Monat",
    volSca: "Bis zu 10.000 verwaltete Nachrichten / Monat",
    featEss: [
      "1 offizielle WhatsApp Business-Nummer",
      "Standard-Wissensdatenbank",
      "Google Calendar-Synchronisation",
      "Manuelle Mitarbeiterübernahme",
      "Standard-Support per E-Mail"
    ],
    featGro: [
      "WhatsApp + Instagram DM",
      "Erweiterte Wissensdatenbank",
      "Automatische Terminerinnerungen",
      "Google-Bewertungsmanagement",
      "Bis zu 3 Teammitglieder im Posteingang",
      "Prioritärer WhatsApp-Support"
    ],
    featSca: [
      "Multi-Kanal-Unterstützung",
      "Multi-Standort-Verwaltung",
      "Standortspezifische Regeln",
      "Individuelle Dialogabläufe",
      "Unbegrenzte Mitarbeiterkonten",
      "Dedizierter Telefonsupport"
    ],
    customNeeds: "Individuelle Wünsche? <a href=\"mailto:info@melpis.it\">Sprechen Sie mit uns</a>",
    popularLead: "Die ideale Balance für die meisten Betriebe",
    manualEyebrow: "Der Wert von Automation",
    manualTitle: "Was kostet Sie die manuelle Betreuung heute?",
    manualLead: "Keine hypothetischen Rechenmodelle: Das sind die drei realen Probleme, die täglich wertvolle Arbeitszeit in Ihrem Betrieb binden.",
    mCard1Title: "Wiederkehrende Nachrichten",
    mCard1Desc: "Ständige Fragen zu Öffnungszeiten, Preisen, Terminen und Leistungen, die Ihren Arbeitsalltag fortlaufend unterbrechen.",
    mCard2Title: "Verpasste Buchungen",
    mCard2Desc: "Anfragen außerhalb der Geschäftszeiten oder verspätete Rückmeldungen, wenn der Kunde bereits beim Wettbewerber gebucht hat.",
    mCard3Title: "Wertvolle Mitarbeiterzeit",
    mCard3Desc: "Stunden, die Ihr Personal wöchentlich mit Routine-Telefondienst verbringt, die Melpis vollautomatisch abwickeln kann.",
    mConclusion: "Mit Melpis automatisieren Sie Routineanfragen und halten Ihrem Team den Rücken frei für persönliche Kundengespräche.",
    metaEyebrow: "Tarif-Transparenz",
    metaTitle: "Melpis-Software + WhatsApp-Kosten",
    metaLead: "Eine saubere Trennung zwischen unserer Softwareplattform und den tatsächlichen Gebühren des Messaging-Anbieters.",
    metaBadge1: "Software-Gebühr",
    metaCard1Title: "Melpis",
    metaCard1High: "Feste monatliche Pauschale je nach gewähltem Tarif.",
    metaCard1Desc: "Enthält den dedizierten KI-Assistenten, Kalender- und Kanal-Synchronisation, Wissensdatenbank, Team-Dashboard und Konversationsvolumen.",
    metaBadge2: "Offizielle Infrastruktur",
    metaCard2Title: "Meta WhatsApp Business Platform",
    metaCard2High: "Variable Nachrichtengebühren von Meta.",
    metaCard2Desc: "Kosten für über die offizielle WhatsApp Business API versendete Nachrichten rechnet Meta nach Kategorie (Utility, Marketing, Service) und Land ab.",
    metaBadge3: "Keine Überraschungen",
    metaCard3Title: "Keine versteckten Melpis-Aufschläge",
    metaCard3High: "Volle Preistransparenz: Softwaregebühr an uns, Meta-Gebühren direkt an Meta.",
    metaCard3Desc: "Melpis erhebt keinerlei prozentuale Margen auf Ihre WhatsApp-Nachrichten: Sie zahlen Ihr klares Software-Abonnement an uns und die offiziellen Gebühren an Meta.",
    compEyebrow: "TARIFVERGLEICH",
    compTitle: "Alle Details im direkten Vergleich",
    compLead: "Vergleichen Sie Kanäle, Volumina, Automationen und Support, um das optimale Paket für Ihr Unternehmen zu wählen.",
    compFeatCol: "Funktion",
    compSubEss: "Das Wesentliche für den Einstieg",
    compSubGro: "Mehr Möglichkeiten, bessere Ergebnisse",
    compSubSca: "Maximale Leistung für Ihr Unternehmen"
  }
};

function localizeLanding(html, lang, bundle) {
  if (lang === 'it' || !bundle || !bundle.landing) return html;
  const l = bundle.landing;
  const c = LANDING_COPY[lang];
  if (!c) return html;
  let res = html;

  // The public landing ships monthly pricing only for V1. Keep the static
  // monthly label localized when this page is regenerated from the Italian source.
  const monthlyBillingLabel = bundle.pricing?.hero?.monthly_toggle;
  if (monthlyBillingLabel) {
    res = res.replace(
      /(<div class="billing-toggle-wrap">\s*<span class="toggle-btn active" aria-current="true">)[^<]*(<\/span>\s*<\/div>)/,
      `$1${monthlyBillingLabel}$2`
    );
  }

  // 1. Hero H1 and Subtitle
  res = res.replace(
    /<h1 class="hero-h1(?: hero-anim-title)?">[\s\S]*?<\/h1>/,
    `<h1 class="hero-h1 hero-anim-title">\n                        ${c.heroH1}\n                    </h1>`
  );
  res = res.replace(
    /<p class="hero-sub(?: hero-anim-sub)?">[\s\S]*?<\/p>/,
    `<p class="hero-sub hero-anim-sub">\n                        ${c.heroSub}\n                    </p>`
  );

  // 2. CTAs and Badges
  res = res.replace(/<span>Inizia gratis<\/span>/g, `<span>${c.ctaPrimary}</span>`);
  res = res.replace(/<p class="hero-eyebrow">AUTOMAZIONE PER CHI FA IMPRESA<\/p>/, `<p class="hero-eyebrow">${c.ambLeft.join(' ')}</p>`);
  res = res.replace(/<span>Come funziona<\/span>/g, `<span>${c.ctaSecondary}</span>`);
  res = res.replace(/(?:<span>)?7 giorni gratis(?:<\/span>)?/g, c.badge7Days);
  res = res.replace(/(?:<span>)?Nessuna carta richiesta(?:<\/span>)?/g, c.badgeNoCard);
  res = res.replace(/(?:<span>)?(?:Ufficiale Meta Cloud API|Infrastruttura ufficiale Meta Cloud API)(?:<\/span>)?/g, c.badgeMeta);

  // Ambient vertical & bottom badges
  res = res.replace(/<span class="ambient-text">AUTOMAZIONE<\/span>\s*<span class="ambient-text">PER CHI<\/span>\s*<span class="ambient-text">FA IMPRESA<\/span>/, `<span class="ambient-text">${c.ambLeft[0]}</span>\n                <span class="ambient-text">${c.ambLeft[1]}</span>\n                <span class="ambient-text">${c.ambLeft[2]}</span>`);
  res = res.replace(/<span class="ambient-text">PIÙ<\/span>\s*<span class="ambient-text">TEMPO<\/span>\s*<span class="ambient-text">PER CIÒ<\/span>\s*<span class="ambient-text">CHE CONTA<\/span>/, `<span class="ambient-text">${c.ambRight[0]}</span>\n                <span class="ambient-text">${c.ambRight[1]}</span>\n                <span class="ambient-text">${c.ambRight[2]}</span>`);
  res = res.replace(/<span class="ambient-bottom-text">DAI CONVERSAZIONI A OPPORTUNITÀ<\/span>/, `<span class="ambient-bottom-text">${c.ambBottom}</span>`);

  // 3. Bento narrative journey
  res = res.replace(/Come Melpis automatizza le conversazioni della tua attività/, c.narrativeTitle);
  res = res.replace(/Gestisci ogni messaggio in ingresso, senza assumere personale/, c.sat1Wa);
  res = res.replace(/Nessuna richiesta persa alle 2 di notte — risposte immediate, sempre/, c.sat2Avail);
  res = res.replace(/Conosce le regole del tuo settore — non risponde come un bot generico/, c.sat3Rules);
  res = res.replace(/Accoglie ogni cliente nella sua lingua, senza configurazioni extra/, c.sat4Lang);
  res = res.replace(/Dal messaggio in chat all'appuntamento in agenda<br>in pochi secondi, senza aprire il telefono/, c.climaxGrandTitle);
  res = res.replace(/Melpis legge la richiesta, controlla Google Calendar e risponde al cliente — tu ricevi solo la notifica con l'evento già creato\./, c.climaxLead);
  res = res.replace(/Controllo disponibilità · Google Calendar/, c.climaxSatLabel);
  res = res.replace(/Prenotazione confermata · risposta in 3s/, c.climaxDomHead);
  res = res.replace(/Evento creato in agenda · notifica inviata al team/, c.climaxSyncNote);

  // Extra copy for Satellite & Climax chat mockups
  const cExtra = LANDING_EXTRAS[lang];
  if (cExtra) {
    res = res.replace(/<span class="chat-time-sm">Adesso<\/span>/, `<span class="chat-time-sm">${cExtra.sat1ChatTime}</span>`);
    res = res.replace(/"Buonasera! Avete un tavolo per 4 domani sera alle 20:30\? Uno di noi è celiaco\."/, cExtra.sat1ChatCust);
    res = res.replace(/"Buonasera Giulia! Tavolo confermato per 4 alle 20:30 con nota per celiachia\. Vi aspettiamo!"/, cExtra.sat1ChatAi);

    res = res.replace(/Tavolo per 6 · 21:00 · <em class="inbox-status-done">Confermato<\/em>/, cExtra.sat2Inb1Sub);
    res = res.replace(/Taglio \+ piega · martedì/, cExtra.sat2Inb2Sub);
    res = res.replace(/<strong>Marco R\. · Ristorante<\/strong>/, `<strong>Marco R. · ${cExtra.sat2Rest}</strong>`);
    res = res.replace(/<strong>Sara B\. · Salone &amp; Spa<\/strong>/, `<strong>Sara B. · ${cExtra.sat2Salon}</strong>`);

    if (cExtra.sat3Tabs) {
      res = res.replace(/data-sector="ristoranti">Ristoranti<\/button>/, `data-sector="ristoranti">${cExtra.sat3Tabs.ristoranti}</button>`);
      res = res.replace(/data-sector="saloni">Saloni<\/button>/, `data-sector="saloni">${cExtra.sat3Tabs.saloni}</button>`);
      res = res.replace(/data-sector="medici">Medici<\/button>/, `data-sector="medici">${cExtra.sat3Tabs.medici}</button>`);
      res = res.replace(/data-sector="hotel">Hotel<\/button>/, `data-sector="hotel">${cExtra.sat3Tabs.hotel}</button>`);
    }
    if (cExtra.sat3Items) {
      res = res.replace(/Gestione coperti e intolleranze/, cExtra.sat3Items[0]);
      res = res.replace(/Anticipo prenotazione e slot/, cExtra.sat3Items[1]);
      res = res.replace(/Fail-Closed clinico per urgenze/, cExtra.sat3Items[2]);
    }

    res = res.replace(/"Buonasera! Avete un tavolo per 4 questo sabato sera alle 20:30\?"/, cExtra.climaxSatQuote);
    res = res.replace(/Sabato · 20:30 – 22:00 · Tavolo 4 persone/, cExtra.climaxCalTitle);
    res = res.replace(/Sala interna · slot libero, nessuna sovrapposizione/, cExtra.climaxCalSub);
    res = res.replace(/"Buonasera Marco! Tavolo per 4 confermato per questo sabato alle 20:30\. A presto!"/, cExtra.climaxAiReply);
    res = res.replace(/Inviato automaticamente/, cExtra.climaxAutoSent);

    res = res.replace(/"Esperienza fantastica! Tavolo pronto all'arrivo, servizio velocissimo e accoglienza super cordiale\."/, cExtra.reviewCardQuote);
    res = res.replace(/Bozza generata dall'AI/, cExtra.reviewCardBadge);
    res = res.replace(/Pronta per approvazione/, cExtra.reviewCardStatus);

    res = res.replace(/Ciao! Vorrei organizzare un evento aziendale per circa 20 persone con menu personalizzato sabato sera\. È possibile\?/, cExtra.hitlCustMsg);
    res = res.replace(/Certamente Matteo! Per gruppi superiori a 12 persone con richieste specifiche ti collego subito al nostro responsabile per concordare il menu\./, cExtra.hitlAiMsg);
    res = res.replace(/\[autonomo\]/, cExtra.hitlBadgeAuto);
    res = res.replace(/Marco \(Staff Melpis\) ha preso il controllo della chat/, cExtra.hitlTakeoverPill);
    res = res.replace(/\[staff in linea\]/, cExtra.hitlBadgeStaff);
    res = res.replace(/Ciao Matteo! Sono Marco del team Melpis\. Ti ho riservato l'area per 20 ospiti e ho inoltrato le proposte menu per sabato!/, cExtra.hitlStaffMsg);
    res = res.replace(/AI in ascolto attivo\.\.\./, cExtra.hitlPlaceholder);
  }

  // 4. Portal bridge
  res = res.replace(/I tuoi clienti meritano risposte in 30 secondi\.<br>Non ore di attesa al telefono\./, c.portalHeadline);
  res = res.replace(/I clienti scrivono su WhatsApp e si aspettano una risposta subito\. Melpis risponde usando solo i dati della tua attività — orari, listini, regole — senza inventare nulla\./, c.portalSub);

  // 5. Coworker showcase (#come-funziona)
  res = res.replace(/Per ristoranti, hotel, barber shop e attività su appuntamento/, c.coworkerPill);
  res = res.replace(
    /<h2 class="hero-headline">[\s\S]*?<\/h2>/,
    `<h2 class="hero-headline">\n                        ${c.coworkerHeadline}\n                    </h2>`
  );
  res = res.replace(/Melpis risponde ai clienti su WhatsApp e Instagram, organizza gli appuntamenti nel calendario e automatizza le attività ripetitive\. Tu intervieni solo quando serve\./, c.coworkerDesc);
  res = res.replace(/<span>Prova Melpis gratis<\/span>/g, `<span>${c.coworkerCta}</span>`);
  res = res.replace(/Configurazione in meno di 10 minuti · Nessuna carta richiesta/g, c.coworkerMicro);

  // Industry pills
  res = res.replace(/<span>Ristoranti<\/span>/g, `<span>${c.indPills.r}</span>`);
  res = res.replace(/<span>Hotel<\/span>/g, `<span>${c.indPills.h}</span>`);
  res = res.replace(/<span>Barber shop<\/span>/g, `<span>${c.indPills.b}</span>`);
  res = res.replace(/<span>Centri estetici<\/span>/g, `<span>${c.indPills.s}</span>`);
  res = res.replace(/<span>Altre attività<\/span>/g, `<span>${c.indPills.o}</span>`);

  // Benefits bar
  res = res.replace(/<h3 class="benefit-title">Pronto in 10 minuti<\/h3>/, `<h3 class="benefit-title">${c.benefit1Title}</h3>`);
  res = res.replace(/Collega i tuoi canali e inizia senza<br>cambiare il modo in cui lavori\./, c.benefit1Sub);
  res = res.replace(/<h3 class="benefit-title">Adattato al tuo settore<\/h3>/, `<h3 class="benefit-title">${c.benefit2Title}</h3>`);
  res = res.replace(/Ristoranti, hotel, barber shop, centri<br>estetici e attività su appuntamento\./, c.benefit2Sub);
  res = res.replace(/<h3 class="benefit-title">Tu mantieni il controllo<\/h3>/, `<h3 class="benefit-title">${c.benefit3Title}</h3>`);
  res = res.replace(/Melpis automatizza le attività ripetitive,<br>ma puoi intervenire quando vuoi\./, c.benefit3Sub);

  // 6. Features split section: Knowledge Base
  res = res.replace(/CONOSCENZA AZIENDALE &amp; REGOLE/, c.kbEyebrow);
  res = res.replace(/Risponde solo con le tue informazioni\.<br><span class="nowrap">E quando non sa, chiede al tuo team\.<\/span>/, c.kbTitle);
  res = res.replace(/Melpis non inventa risposte e non improvvisa: consulta PDF, menu, listini e regole della tua attività\. Quando una richiesta esce dai limiti che hai impostato, avvisa il cliente e passa subito la mano al tuo staff\./, c.kbDesc);
  res = res.replace(/Supporto per PDF, menu del giorno, listini prezzi e orari di apertura/, c.kbBullets[0]);
  res = res.replace(/Riconoscimento di servizi, orari e condizioni impostate dall'attività/, c.kbBullets[1]);
  res = res.replace(/Avviso cortese e notifica allo staff quando serve un accordo personalizzato/, c.kbBullets[2]);
  res = res.replace(/<span>Prova gratis per 7 giorni<\/span>/g, `<span>${c.kbCta1}</span>`);
  res = res.replace(/<span>Come funziona \(60 sec\)<\/span>/g, `<span>${c.kbCta2}</span>`);
  res = res.replace(/Nessuna carta di credito/g, c.kbMicro1);
  res = res.replace(/Attivazione in pochi minuti/g, c.kbMicro2);
  res = res.replace(/Dati sempre tuoi/g, c.kbMicro3);

  // Automations & Sync
  res = res.replace(/Automazioni &amp; Sincronizzazione/, c.syncEyebrow);
  res = res.replace(/Zero doppie prenotazioni\.<br>Non guardi più due calendari\./, c.syncTitle);
  res = res.replace(/Ogni appuntamento concordato su WhatsApp si sincronizza all'istante su Google Calendar, verificando gli slot liberi in tempo reale\. Niente sovrapposizioni, niente errori manuali\./, c.syncDesc);
  res = res.replace(/Sincronizzazione bidirezionale immediata con Google Calendar/, c.syncBullets[0]);
  res = res.replace(/Controllo automatico disponibilità: nessun tavolo o slot prenotato due volte/, c.syncBullets[1]);
  res = res.replace(/Bozze pronte per le recensioni Google post-servizio con il tuo tono di voce/, c.syncBullets[2]);
  res = res.replace(/<span>Scopri tutte le integrazioni native<\/span>/, `<span>${c.syncMore}</span>`);

  // Human in the loop
  res = res.replace(/Il tuo team mantiene sempre la regia/, c.hitlTitle);
  res = res.replace(/L'AI non opera mai come una scatola nera chiusa\. Monitora le conversazioni in tempo reale dall'Inbox unificata, intervieni con un solo click e riprendi il controllo ogni volta che lo desideri\./, c.hitlLead);
  res = res.replace(/<h3 class="hitl-pillar-title">Passaggio all'operatore istantaneo<\/h3>/, `<h3 class="hitl-pillar-title">${c.hitlPillarTitle}</h3>`);
  res = res.replace(/<span class="hitl-featured-badge">In evidenza<\/span>/, `<span class="hitl-featured-badge">${c.hitlBadge}</span>`);
  res = res.replace(/Se il cliente pone una richiesta complessa, vuole parlare con una persona o richiede condizioni personalizzate, l'assistente mette in pausa l'automazione e notifica all'istante il tuo staff\./, c.hitlPillarDesc);
  res = res.replace(/Switch istantaneo: 0s/, c.hitlMeta);
  res = res.replace(/Zero messaggi persi/, c.hitlMeta2);
  res = res.replace(/<h4>Tono e Regole su Misura<\/h4>/, `<h4>${c.hitlSub1Title}</h4>`);
  res = res.replace(/Imposta confidenza, limiti d'azione e protocolli per ogni scenario operativo\./, c.hitlSub1Desc);
  res = res.replace(/<h4>Privacy e Sicurezza dei Dati<\/h4>/, `<h4>${c.hitlSub2Title}</h4>`);
  res = res.replace(/Isolamento tenant rigoroso, crittografia end-to-end e nessun training di modelli terzi\./, c.hitlSub2Desc);

  // 7. Pricing on landing page
  res = res.replace(/<span class="eyebrow-pill">Piani Semplici e Trasparenti<\/span>/, `<span class="eyebrow-pill">${c.pricingEyebrow}</span>`);
  res = res.replace(/Scegli il piano ideale per la tua attività/, c.pricingTitle);
  res = res.replace(/Inizia con 7 giorni di prova gratuita[\s\S]*?disdici quando vuoi con un solo click\./, c.pricingLead);
  res = res.replace(/>Fatturazione Mensile<\/button>/, `>${c.monthlyToggle}</button>`);
  res = res.replace(/>Annuale<\/button>/, `>${c.annualToggle}</button>`);
  const quotaCopy = {
    en: ['Up to 500 messages/month', 'Up to 2,000 messages/month', 'Up to 10,000 messages/month'],
    es: ['Hasta 500 mensajes/mes', 'Hasta 2.000 mensajes/mes', 'Hasta 10.000 mensajes/mes'],
    fr: ["Jusqu'à 500 messages/mois", "Jusqu'à 2 000 messages/mois", "Jusqu'à 10 000 messages/mois"],
    de: ['Bis zu 500 Nachrichten/Monat', 'Bis zu 2.000 Nachrichten/Monat', 'Bis zu 10.000 Nachrichten/Monat'],
  }[lang];
  if (quotaCopy) {
    ['500', '2.000', '10.000'].forEach((amount, index) => {
      res = res.replace(`Fino a ${amount} messaggi/mese`, quotaCopy[index]);
    });
  }
  res = res.replace(/<h3>Essenziale<\/h3>/, `<h3>${c.planEssentialTitle}</h3>`);
  res = res.replace(/Per piccole attività e professionisti che vogliono automatizzare i primi messaggi\./, c.planEssentialDesc);
  res = res.replace(/<h3>Crescita<\/h3>/, `<h3>${c.planGrowthTitle}</h3>`);
  res = res.replace(/Ideale per ristoranti, saloni e studi medici con flusso costante di clienti\./, c.planGrowthDesc);
  res = res.replace(/<div class="popular-ribbon">Più Scelto<\/div>/, `<div class="popular-ribbon">${c.planGrowthPopular}</div>`);  // Showcase glass panel text localization
  if (c.wfShowcaseTag) {
    res = res.replace(/<span class="wf-showcase-tag">.*?<\/span>/, `<span class="wf-showcase-tag">${c.wfShowcaseTag}</span>`);
    res = res.replace(/<h4 class="wf-card-title wf-card-title-1">.*?<\/h4>/, `<h4 class="wf-card-title wf-card-title-1">${c.wfCardTitle1}</h4>`);
    res = res.replace(/<p class="client-bubble-msg wf-msg-client">[\s\S]*?<\/p>/, `<p class="client-bubble-msg wf-msg-client">${c.wfMsgClient}</p>`);
    res = res.replace(/<h4 class="wf-card-title wf-card-title-2">.*?<\/h4>/, `<h4 class="wf-card-title wf-card-title-2">${c.wfCardTitle2}</h4>`);
    res = res.replace(/<span class="wf-card-sub wf-card-sub-2">.*?<\/span>/, `<span class="wf-card-sub wf-card-sub-2">${c.wfCardSub2}</span>`);
    res = res.replace(/<p class="ai-bubble-msg wf-msg-ai">[\s\S]*?<\/p>/, `<p class="ai-bubble-msg wf-msg-ai">${c.wfMsgAi}</p>`);
    res = res.replace(/<h4 class="wf-card-title wf-card-title-3">.*?<\/h4>/, `<h4 class="wf-card-title wf-card-title-3">${c.wfCardTitle3}</h4>`);
    res = res.replace(/<strong class="cal-event-title wf-cal-title">.*?<\/strong>/, `<strong class="cal-event-title wf-cal-title">${c.wfCalTitle}</strong>`);
    res = res.replace(/<span class="cal-event-date wf-cal-date">.*?<\/span>/, `<span class="cal-event-date wf-cal-date">${c.wfCalDate}</span>`);
    res = res.replace(/<h4 class="wf-card-title wf-card-title-4">.*?<\/h4>/, `<h4 class="wf-card-title wf-card-title-4">${c.wfCardTitle4}</h4>`);
    res = res.replace(/<span class="wf-card-sub wf-card-sub-4">.*?<\/span>/, `<span class="wf-card-sub wf-card-sub-4">${c.wfCardSub4}</span>`);
    res = res.replace(/<p class="review-text wf-review-text">[\s\S]*?<\/p>/, `<p class="review-text wf-review-text">${c.wfReviewText}</p>`);
    res = res.replace(/<span class="btn-review-mini wf-review-btn">.*?<\/span>/, `<span class="btn-review-mini wf-review-btn">${c.wfReviewBtn}</span>`);
    res = res.replace(/<p class="handwriting-quote wf-quote">[\s\S]*?<\/p>/, `<p class="handwriting-quote wf-quote">${c.wfQuote}</p>`);
    res = res.replace(/<span class="proof-text wf-proof-text">.*?<\/span>/, `<span class="proof-text wf-proof-text">${c.wfProofText}</span>`);
  }
  res = res.replace(/<h3>Scala<\/h3>/, `<h3>${c.planScaleTitle}</h3>`);
  res = res.replace(/Per hotel, cliniche o attività ad alto volume con esigenze avanzate\./, c.planScaleDesc);
  res = res.replace(/(<button type="button" class="btn-price"[^>]*data-plan="essential"[^>]*>).*?(<\/button>)/, `$1${c.kbCta1}$2`);
  res = res.replace(/(<button type="button" class="btn-price btn-price-featured"[^>]*data-plan="growth"[^>]*>).*?(<\/button>)/, `$1${c.kbCta1}$2`);
  res = res.replace(/(<button type="button" class="btn-price"[^>]*data-plan="scale"[^>]*>).*?(<\/button>)/, `$1${c.kbCta1}$2`);

  // 8. FAQ accordion
  if (cExtra && cExtra.faqEyebrow) {
    res = res.replace(/<span class="eyebrow-pill">Domande Frequenti<\/span>/, `<span class="eyebrow-pill">${cExtra.faqEyebrow}</span>`);
    res = res.replace(/<h2 class="display-md">Tutto quello che c'è da sapere<\/h2>/, `<h2 class="display-md">${cExtra.faqTitle}</h2>`);
  }
  if (l.faq && Array.isArray(l.faq.items)) {
    const faqHtml = l.faq.items.map(item => `                <details class="faq-item">
                    <summary class="faq-quest"><span>${item.q}</span> <span class="faq-icon" aria-hidden="true">+</span></summary>
                    <div class="faq-ans"><div class="faq-ans-inner"><p>${item.a}</p></div></div>
                </details>`).join('\n');
    res = res.replace(/<div class="faq-list reveal">[\s\S]*?<\/div>\s*<\/div>\s*<\/section>/, `<div class="faq-list reveal">\n${faqHtml}\n            </div>\n        </div>\n    </section>`);
  }

  // 9. Final CTA
  res = res.replace(/<h2 class="final-cta-title">Prova Melpis sulla tua attività per 7 giorni\.<\/h2>/, `<h2 class="final-cta-title">${c.finalCtaTitle}</h2>`);
  res = res.replace(/Connetti WhatsApp, imposta le tue regole e guarda come risponde ai clienti\. Nessuna carta richiesta\./, c.finalCtaSub);
  res = res.replace(/<span>Prova Melpis gratis per 7 giorni<\/span>/g, `<span>${c.finalCtaBtn}</span>`);
  res = res.replace(/Setup in 10 min · Nessuna carta richiesta · Mantieni sempre il controllo/, c.finalCtaMicro);

  // 10. Signup modal
  res = res.replace(/<div class="modal-badge-top">Prova Gratuita 7 Giorni<\/div>/, `<div class="modal-badge-top">${c.modalBadge}</div>`);
  res = res.replace(/<h3 id="modalTitle">Attiva il tuo assistente Melpis<\/h3>/, `<h3 id="modalTitle">${c.modalTitle}</h3>`);
  res = res.replace(/Nessuna carta richiesta\. Inserisci la tua email per avviare la configurazione guidata in pochi passaggi\./, c.modalDesc);
  res = res.replace(/<label for="regEmail">Email aziendale<\/label>/, `<label for="regEmail">${c.modalEmailLabel}</label>`);
  res = res.replace(/<label for="regVertical">Settore attività<\/label>/, `<label for="regVertical">${c.modalVerticalLabel}</label>`);
  if (c.modalOptions) {
    for (const [key, val] of Object.entries(c.modalOptions)) {
      res = res.replace(new RegExp(`<option value="${key}">[^<]+<\\/option>`), `<option value="${key}">${val}</option>`);
    }
  }
  res = res.replace(/<span>Avvia la prova gratuita<\/span>/, `<span>${c.modalSubmit}</span>`);
  res = res.replace(/Hai già un account\? <a href="\/app\/">Accedi alla dashboard<\/a>/, `${c.modalFootnote} <a href="/app/">${c.modalFootnoteLink}</a>`);

  // 11. Footer tagline & platform
  res = res.replace(/L'assistente AI su WhatsApp per attività di servizio\./, c.footerTagline);
  res = res.replace(/<h4>Piattaforma<\/h4>/, `<h4>${c.footerPlatform}</h4>`);
  res = res.replace(/>Pannello di controllo<\/a>/, `>${c.footerDashboard}</a>`);

  return res;
}

function localizePricing(html, lang, bundle) {
  if (lang === 'it' || !bundle || !bundle.pricing) return html;
  const p = bundle.pricing;
  const c = PRICING_PAGE_COPY[lang];
  let res = html;

  if (c) {
    // 1. Hero
    res = res.replace(/<span class="eyebrow-pill">(?:Prezzi chiari, nessun costo nascosto|Tariffe Semplici &amp; Trasparenti)<\/span>/, `<span class="eyebrow-pill">${c.heroBadge}</span>`);
    res = res.replace(/<h1[^>]*>[\s\S]*?<\/h1>/, `<h1 class="anim-hero-2">${c.heroTitle}</h1>`);
    res = res.replace(/<p class="pricing-hero-p[^"]*">[\s\S]*?<\/p>/, `<p class="pricing-hero-p anim-hero-3">${c.heroSub}</p>`);

    // Trust chips
    res = res.replace(/7 giorni (?:gratuiti|di prova gratuita)/g, c.trustChip1);
    res = res.replace(/Nessuna carta richiesta/g, c.trustChip2);
    res = res.replace(/Meta Cloud API Ufficiale/g, c.trustChip3);

    // 2. Standalone Cards
    res = res.replace(/<h3 class="price-plan-name">Essenziale<\/h3>/g, `<h3 class="price-plan-name">${p.plans?.essential?.name || 'Essential'}</h3>`);
    res = res.replace(/<h3 class="price-plan-name">Crescita<\/h3>/g, `<h3 class="price-plan-name">${p.plans?.growth?.name || 'Growth'}</h3>`);
    res = res.replace(/<h3 class="price-plan-name">Scala<\/h3>/g, `<h3 class="price-plan-name">${p.plans?.scale?.name || 'Scale'}</h3>`);

    res = res.replace(/Per professionisti e piccole attività che vogliono automatizzare WhatsApp\./, p.plans?.essential?.tagline || c.volEss);
    res = res.replace(/Per locali, saloni e studi con più richieste e prenotazioni\./, p.plans?.growth?.tagline || c.volGro);
    res = res.replace(/Per attività multi-sede o con volumi elevati\./, p.plans?.scale?.tagline || c.volSca);

    res = res.replace(/<span class="price-period">\/mese<\/span>/g, `<span class="price-period">${p.plans?.essential?.period || '/month'}</span>`);
    res = res.replace(/<a href="\/registrati\/\?piano=essenziale" class="btn-price">Inizia gratis<\/a>/, `<a href="/registrati/?piano=essenziale" class="btn-price">${p.plans?.essential?.cta || 'Start free'}</a>`);
    res = res.replace(/<a href="\/registrati\/\?piano=crescita" class="btn-price btn-popular">Prova Crescita gratis<\/a>/, `<a href="/registrati/?piano=crescita" class="btn-price btn-popular">${p.plans?.growth?.cta || 'Try Growth free'}</a>`);
    res = res.replace(/<a href="\/registrati\/\?piano=scala" class="btn-price">Inizia gratis<\/a>/, `<a href="/registrati/?piano=scala" class="btn-price">${p.plans?.scale?.cta || 'Start free'}</a>`);

    res = res.replace(/<div class="popular-badge">Più Scelto<\/div>/, `<div class="popular-badge">${p.plans?.growth?.badge || 'Most Popular'}</div>`);
    res = res.replace(/<div class="popular-lead-line">Il miglior equilibrio per la maggior parte delle attività<\/div>/, `<div class="popular-lead-line">${c.popularLead}</div>`);
    res = res.replace(/<span class="volume-label">Volume mensile<\/span>/g, `<span class="volume-label">${c.volumeLabel}</span>`);
    res = res.replace(/Fino a 500 messaggi gestiti \/ mese/, c.volEss);
    res = res.replace(/Fino a 2\.000 messaggi gestiti \/ mese/, c.volGro);
    res = res.replace(/Fino a 10\.000 messaggi gestiti \/ mese/, c.volSca);
    res = res.replace(/Esigenze su misura\? <a href="mailto:info@melpis\.it">Parla con noi<\/a>/, c.customNeeds);

    // 3. Meta disclaimer
    res = res.replace(/<strong>Costi WhatsApp Meta esclusi\.<\/strong>/, `<strong>${c.metaCard3Title}.</strong>`);
    res = res.replace(/Eventuali costi applicati da Meta per l’invio dei messaggi WhatsApp Business vengono addebitati separatamente in base al tipo di messaggio e al Paese del destinatario\./, c.metaCard2Desc);
    res = res.replace(/Come funzionano i costi WhatsApp\? <span aria-hidden="true">↓<\/span>/, `${c.metaTitle}? <span aria-hidden="true">↓</span>`);

    // 4. Manual cost section
    res = res.replace(/<span class="eyebrow-pill">Il Valore dell'Automazione<\/span>/, `<span class="eyebrow-pill">${c.manualEyebrow}</span>`);
    res = res.replace(/<h2>Quanto ti costa oggi gestire tutto a mano\?<\/h2>/, `<h2>${c.manualTitle}</h2>`);
    res = res.replace(/Nessuna formula ipotetica o stima arbitraria: ecco i tre problemi reali che assorbono tempo ed energie ogni giorno nella tua attività\./, c.manualLead);
    res = res.replace(/<h3>Messaggi ripetitivi<\/h3>/, `<h3>${c.mCard1Title}</h3>`);
    res = res.replace(/Richieste continue su orari, prezzi, disponibilità e servizi che interrompono continuamente il tuo lavoro\./, c.mCard1Desc);
    res = res.replace(/<h3>Prenotazioni perse<\/h3>/, `<h3>${c.mCard2Title}</h3>`);
    res = res.replace(/Richieste fuori orario o risposte che arrivano troppo tardi, quando il potenziale cliente ha già contattato un altro locale o studio\./, c.mCard2Desc);
    res = res.replace(/<h3>Tempo del personale<\/h3>/, `<h3>${c.mCard3Title}</h3>`);
    res = res.replace(/Ore spese ogni settimana dallo staff su attività di segreteria e risposte di routine che Melpis può gestire automaticamente\./, c.mCard3Desc);
    res = res.replace(/Con Melpis automatizzi le richieste ripetitive e lasci allo staff solo ciò che richiede davvero una persona\./, c.mConclusion);

    // 5. Meta Pricing Explainer
    res = res.replace(/<span class="eyebrow-pill">Trasparenza Tariffe<\/span>/, `<span class="eyebrow-pill">${c.metaEyebrow}</span>`);
    res = res.replace(/<h2>Prezzo Melpis \+ costi WhatsApp<\/h2>/, `<h2>${c.metaTitle}</h2>`);
    res = res.replace(/Una distinzione chiara e corretta tra la piattaforma software e i costi vivi dei provider di messaggistica\./, c.metaLead);
    res = res.replace(/<div class="meta-pricing-badge">Canone Software<\/div>/, `<div class="meta-pricing-badge">${c.metaBadge1}</div>`);
    res = res.replace(/<div class="meta-pricing-highlight">Abbonamento mensile fisso in base al piano\.<\/div>/, `<div class="meta-pricing-highlight">${c.metaCard1High}</div>`);
    res = res.replace(/Include l'assistente AI dedicato, l'integrazione con agende e canali, la knowledge base aziendale, il pannello operativo per lo staff e la gestione dei messaggi fino al limite del tuo piano\./, c.metaCard1Desc);
    res = res.replace(/<div class="meta-pricing-badge">Infrastruttura Ufficiale<\/div>/, `<div class="meta-pricing-badge">${c.metaBadge2}</div>`);
    res = res.replace(/<div class="meta-pricing-highlight">Costi messaggio variabili applicati da Meta\.<\/div>/, `<div class="meta-pricing-highlight">${c.metaCard2High}</div>`);
    res = res.replace(/I costi per i messaggi inviati tramite l'API ufficiale WhatsApp Business vengono conteggiati da Meta in base alla categoria del messaggio \(utility, marketing, servizio\) e al Paese del destinatario\./, c.metaCard2Desc);
    res = res.replace(/<div class="meta-pricing-badge">Nessuna Sorpresa<\/div>/, `<div class="meta-pricing-badge">${c.metaBadge3}</div>`);
    res = res.replace(/<h3>Nessun costo nascosto Melpis<\/h3>/, `<h3>${c.metaCard3Title}</h3>`);
    res = res.replace(/<div class="meta-pricing-highlight">Sai sempre quanto paghi per il software; le tariffe Meta sono separate e trasparenti\.<\/div>/, `<div class="meta-pricing-highlight">${c.metaCard3High}</div>`);
    res = res.replace(/Melpis non applica alcuna maggiorazione o ricarico percentuale sui tuoi messaggi WhatsApp: paghi il tuo abbonamento SaaS chiaro a noi e le sole tariffe ufficiali direttamente a Meta\./, c.metaCard3Desc);

    // 6. Comparison Table
    res = res.replace(/<span class="eyebrow-pill">CONFRONTO PIANI<\/span>/, `<span class="eyebrow-pill">${c.compEyebrow}</span>`);
    res = res.replace(/<h2>Tutti i dettagli a confronto<\/h2>/, `<h2>${c.compTitle}</h2>`);
    res = res.replace(/Confronta canali, volumi, automazioni e supporto per scegliere il piano più adatto alla tua attività\./, c.compLead);
    res = res.replace(/<th class="col-feat-th">Caratteristica<\/th>/, `<th class="col-feat-th">${c.compFeatCol}</th>`);
    res = res.replace(/L'essenziale per iniziare/, c.compSubEss);
    res = res.replace(/Più possibilità, più risultati/, c.compSubGro);
    res = res.replace(/Massima potenza per il tuo business/, c.compSubSca);
    res = res.replace(/<span class="badge-piu-scelto">Più scelto<\/span>/, `<span class="badge-piu-scelto">${p.plans?.growth?.badge || 'Most Popular'}</span>`);

    // 7. Comparison Table details & values
    const pex = PRICING_EXTRAS[lang];
    if (pex) {
      if (pex.compCategories) {
        res = res.replace(/<td class="comp-cat-label">CANALI<\/td>/g, `<td class="comp-cat-label">${pex.compCategories.canali}</td>`);
        res = res.replace(/<td class="comp-cat-label">VOLUME<\/td>/g, `<td class="comp-cat-label">${pex.compCategories.volume}</td>`);
        res = res.replace(/<td class="comp-cat-label">AUTOMAZIONI<\/td>/g, `<td class="comp-cat-label">${pex.compCategories.automazioni}</td>`);
        res = res.replace(/<td class="comp-cat-label">SUPPORTO<\/td>/g, `<td class="comp-cat-label">${pex.compCategories.supporto}</td>`);
      }
      if (pex.compFeatures) {
        res = res.replace(/<span class="feat-name">Canali supportati<\/span>/g, `<span class="feat-name">${pex.compFeatures.canaliSupportati}</span>`);
        res = res.replace(/<span class="feat-name">Numeri WhatsApp collegabili<\/span>/g, `<span class="feat-name">${pex.compFeatures.numeriCollegabili}</span>`);
        res = res.replace(/<span class="feat-name">Conversazioni gestite da Melpis \/ mese<\/span>/g, `<span class="feat-name">${pex.compFeatures.convGestite}</span>`);
        res = res.replace(/<span class="feat-name">Collaboratori staff abilitati \(HITL\)<\/span>/g, `<span class="feat-name">${pex.compFeatures.collaboratori}</span>`);
        res = res.replace(/<span class="feat-name">Conoscenza aziendale \(Knowledge Base\)<\/span>/g, `<span class="feat-name">${pex.compFeatures.kb}</span>`);
        res = res.replace(/<span class="feat-name">Sincronizzazione Google Calendar<\/span>/g, `<span class="feat-name">${pex.compFeatures.calendar}</span>`);
        res = res.replace(/<span class="feat-name">Reminder anti no-show automatici<\/span>/g, `<span class="feat-name">${pex.compFeatures.noShow}</span>`);
        res = res.replace(/<span class="feat-name">Assistente risposte recensioni Google<\/span>/g, `<span class="feat-name">${pex.compFeatures.reviews}</span>`);
        res = res.replace(/<span class="feat-name">Canale di assistenza<\/span>/g, `<span class="feat-name">${pex.compFeatures.supporto}</span>`);
        res = res.replace(/<span class="feat-name">Attivazione e Onboarding<\/span>/g, `<span class="feat-name">${pex.compFeatures.onboarding}</span>`);
      }
      if (pex.compValues) {
        res = res.replace(/<td>1 numero ufficiale<\/td>/g, `<td>${pex.compValues.num1}</td>`);
        res = res.replace(/<td>Numeri multipli dedicati<\/td>/g, `<td>${pex.compValues.numMulti}</td>`);
        res = res.replace(/<td>Fino a 500<\/td>/g, `<td>${pex.compValues.vol500}</td>`);
        res = res.replace(/<td class="col-crescita">Fino a 2\.000<\/td>/g, `<td class="col-crescita">${pex.compValues.vol2000}</td>`);
        res = res.replace(/<td>Fino a 10\.000<\/td>/g, `<td>${pex.compValues.vol10000}</td>`);
        res = res.replace(/<td>1 account \(solo titolare\)<\/td>/g, `<td>${pex.compValues.team1}</td>`);
        res = res.replace(/<td class="col-crescita">Fino a 3 account \(Titolare \+ 2\)<\/td>/g, `<td class="col-crescita">${pex.compValues.team3}</td>`);
        res = res.replace(/<td>Account illimitati con ruoli e permessi<\/td>/g, `<td>${pex.compValues.teamUnlimited}</td>`);
        res = res.replace(/<td>Base \(orari, listino, servizi\)<\/td>/g, `<td>${pex.compValues.kbBase}</td>`);
        res = res.replace(/<td class="col-crescita">Estesa \(cataloghi, listini complessi, FAQ\)<\/td>/g, `<td class="col-crescita">${pex.compValues.kbExtended}</td>`);
        res = res.replace(/<td>Personalizzata per sede e flussi complessi<\/td>/g, `<td>${pex.compValues.kbCustom}</td>`);
        res = res.replace(/(<span class="chk-green">✓<\/span> )Inclusa con turni/g, `$1${pex.compValues.calShifts}`);
        res = res.replace(/(<span class="chk-green">✓<\/span> )Inclusa/g, `$1${pex.compValues.calIncluded}`);
        res = res.replace(/(<span class="chk-green">✓<\/span> )Agende multiple per sede/g, `$1${pex.compValues.calMulti}`);
        res = res.replace(/(<span class="chk-green">✓<\/span> )Inclusi avanzati/g, `$1${pex.compValues.includedAdvanced}`);
        res = res.replace(/(<span class="chk-green">✓<\/span> )Inclusi/g, `$1${pex.compValues.included}`);
        res = res.replace(/(<span class="chk-green">✓<\/span> )Incluso/g, `$1${pex.compValues.included}`);
        res = res.replace(/(<span class="chk-green">✓<\/span> )Multi-sede incluso/g, `$1${pex.compValues.multiIncluded}`);
        res = res.replace(/<td>Email entro 24h<\/td>/g, `<td>${pex.compValues.email24h}</td>`);
        res = res.replace(/<td class="col-crescita">WhatsApp prioritario<\/td>/g, `<td class="col-crescita">${pex.compValues.waPriority}</td>`);
        res = res.replace(/<td>WhatsApp prioritario \+ Telefono<\/td>/g, `<td>${pex.compValues.waPhone}</td>`);
        res = res.replace(/<td>Self-service con guida<\/td>/g, `<td>${pex.compValues.selfService}</td>`);
        res = res.replace(/<td class="col-crescita col-crescita-bottom">Setup assistito e test prompt<\/td>/g, `<td class="col-crescita col-crescita-bottom">${pex.compValues.setupAssisted}</td>`);
        res = res.replace(/<td>Onboarding 1-to-1 dedicato<\/td>/g, `<td>${pex.compValues.onboarding1to1}</td>`);
      }
      if (pex.compFootnote) {
        res = res.replace(/<p class="comp-footnote">\* I costi Meta WhatsApp Business Platform sono separati dal piano Melpis\.<\/p>/, `<p class="comp-footnote">${pex.compFootnote}</p>`);
      }
      if (pex.commercialFaq) {
        // Replace the complete section, not the first nested div. The former
        // non-greedy div match left the Italian FAQ tail in every locale.
        // Keep structured-data wording native too, so no Italian invoice copy
        // survives on a non-Italian pricing route.
        const invoiceItem = pex.commercialFaq.items[4];
        res = res.replace(/Come viene emessa la fattura e include la fatturazione elettronica italiana\?/g, invoiceItem.q);
        res = res.replace(/Ricevuta Stripe immediata; fattura fiscale su richiesta\./g, invoiceItem.a);
        const commFaqHtml = pex.commercialFaq.items.map(item => `                    <details class="faq-item">
                        <summary class="faq-quest"><span>${item.q}</span> <span class="faq-icon" aria-hidden="true">+</span></summary>
                        <div class="faq-ans"><p>${item.a}</p></div>
                    </details>`).join('\n');
        const commercialFaqSection = `<section class="faq-section" id="faq">
            <div class="container">
                <div class="center">
                    <span class="eyebrow-pill">${pex.commercialFaq.eyebrow}</span>
                    <h2>${pex.commercialFaq.title}</h2>
                </div>

                <div class="faq-list">
${commFaqHtml}
                </div>
            </div>
        </section>`;
        res = res.replace(/<section class="faq-section" id="faq">[\s\S]*?<\/section>/, commercialFaqSection);
      }
      if (pex.finalTitle) {
        res = res.replace(/<h2 class="final-cta-title">Meno lavoro manuale ogni giorno\.<br>Più tempo per la tua attività\.<\/h2>/, `<h2 class="final-cta-title">${pex.finalTitle}</h2>`);
        res = res.replace(/<p class="final-cta-sub">Configura Melpis in pochi passaggi e provalo per 7 giorni senza carta di credito\.<\/p>/, `<p class="final-cta-sub">${pex.finalSub}</p>`);
        res = res.replace(/<span>Inizia la prova gratuita di 7 giorni<\/span>/g, `<span>${pex.finalBtn}</span>`);
      }
    }
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
    res = res.replace(/7 giorni di prova completa/g, shared.trustTrial);
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
      res = res.replace(/<p>(Carica il PDF|Imposta i servizi|Configura orari|Carica le istruzioni).*?<\/p>/, `<p>${secExt.step2Desc}</p>`);
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
      if (secExt.chatSyncPill) res = res.replace(/Google Calendar sincronizzato/, secExt.chatSyncPill);

      if (secExt.row2Eyebrow) res = res.replace(/<span class="vantaggi-eyebrow">Rotazione Sala<\/span>/, `<span class="vantaggi-eyebrow">${secExt.row2Eyebrow}</span>`);
      if (secExt.row2Title) res = res.replace(/<h3>Doppi turni e capienza ottimizzata<\/h3>/, `<h3>${secExt.row2Title}</h3>`);
      if (secExt.row2Desc) res = res.replace(/<p>Configura i turni serali.*?<\/p>/, `<p>${secExt.row2Desc}</p>`);

      if (secExt.row3Eyebrow) res = res.replace(/<span class="vantaggi-eyebrow">Intelligenza Contestuale<\/span>/, `<span class="vantaggi-eyebrow">${secExt.row3Eyebrow}</span>`);
      if (secExt.row3Title) res = res.replace(/<h3>Menu, allergeni e carta dei vini da PDF<\/h3>/, `<h3>${secExt.row3Title}</h3>`);
      if (secExt.row3Desc) res = res.replace(/<p>Carica il PDF del tuo menu.*?<\/p>/, `<p>${secExt.row3Desc}</p>`);

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
      if (page.key === 'home') {
        localizedHtml = localizeLanding(localizedHtml, lang, bundle);
      } else if (page.key === 'pricing') {
        localizedHtml = localizePricing(localizedHtml, lang, bundle);
      } else if (page.key === 'restaurants' || page.key === 'beauty' || page.key === 'medical' || page.key === 'hotels') {
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

    for (const lang of SUPPORTED_LANGS) {
      const locUrl = `${DOMAIN}${routes[lang]}`;
      xml += `  <url>\n`;
      xml += `    <loc>${locUrl}</loc>\n`;
      for (const altLang of SUPPORTED_LANGS) {
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
