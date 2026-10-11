const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const childProcess = require("node:child_process");
const { JSDOM } = require("jsdom");

const { ROUTE_MAP, SUPPORTED_LANGS, generateHreflangs, generateLanguageSelector } = require("../../scripts/build-i18n.js");

test("dashboard overview omits the topbar account avatar and decorative KPI controls", () => {
  const root = path.resolve(__dirname, "../..");
  const html = fs.readFileSync(path.join(root, "web/index.html"), "utf8");
  const appCode = ["app.js", "dashboard-overview.js", "dashboard-reviews.js", "dashboard-bookings.js", "dashboard-knowledge.js", "dashboard-team.js", "dashboard-ai-simulator.js"]
    .map((file) => fs.readFileSync(path.join(root, "web", file), "utf8"))
    .join("\n");
  const css = fs.readFileSync(path.join(root, "web/style.css"), "utf8");

  assert.doesNotMatch(html, /topbar-avatar-(?:btn|initial)/);
  assert.doesNotMatch(html, /class="kpi-action-btn"/);
  assert.doesNotMatch(appCode, /topbar-avatar-initial|kpi-action-btn/);
  assert.doesNotMatch(css, /\.topbar-avatar-(?:btn|initial)|\.kpi-action-btn|\.kpi-card::before/);
  assert.match(html, /class="sidebar-account-btn"/, "sidebar profile access remains available");
});

test("i18n: mandatory namespaces exist in source locale (it)", () => {
  const required = [
    "common.json", "landing.json", "pricing.json", "sectors.json",
    "docs.json", "auth.json", "dashboard.json", "inbox.json",
    "settings.json", "errors.json"
  ];
  const itDir = path.resolve(__dirname, "../../locales/it");
  assert.ok(fs.existsSync(itDir), "locales/it directory must exist");
  for (const ns of required) {
    const filePath = path.join(itDir, ns);
    assert.ok(fs.existsSync(filePath), `Namespace ${ns} must exist in locales/it`);
    const data = JSON.parse(fs.readFileSync(filePath, "utf8"));
    assert.ok(Object.keys(data).length > 0, `Namespace ${ns} must not be empty`);
  }
});

test("i18n: glossary.json defines approved brand terminology", () => {
  const glossaryPath = path.resolve(__dirname, "../../locales/glossary.json");
  assert.ok(fs.existsSync(glossaryPath), "glossary.json must exist");
  const glossary = JSON.parse(fs.readFileSync(glossaryPath, "utf8"));
  assert.equal(glossary.terms.Melpis.en, "Melpis");
  assert.equal(glossary.terms.WhatsApp.en, "WhatsApp");
  assert.equal(glossary.terms["Google Calendar"].en, "Google Calendar");
  assert.equal(glossary.terms["Meta Cloud API"].en, "Meta Cloud API");
});

test("i18n: route map has valid absolute paths with trailing slashes", () => {
  for (const [key, routes] of Object.entries(ROUTE_MAP)) {
    for (const lang of SUPPORTED_LANGS) {
      const url = routes[lang];
      assert.ok(url, `Route ${key} must define URL for ${lang}`);
      assert.ok(url.startsWith("/"), `Route ${key}.${lang} must start with /`);
      assert.ok(url.endsWith("/"), `Route ${key}.${lang} must end with /`);
    }
  }
});

test("i18n: generateHreflangs outputs canonical and x-default", () => {
  const tags = generateHreflangs("pricing", "it");
  assert.match(tags, /rel="canonical" href="https:\/\/melpis\.it\/prezzi\/"/);
  assert.match(tags, /rel="alternate" hreflang="it" href="https:\/\/melpis\.it\/prezzi\/"/);
  assert.match(tags, /rel="alternate" hreflang="en" href="https:\/\/melpis\.it\/en\/pricing\/"/);
  assert.match(tags, /rel="alternate" hreflang="es" href="https:\/\/melpis\.it\/es\/precios\/"/);
  assert.match(tags, /rel="alternate" hreflang="fr" href="https:\/\/melpis\.it\/fr\/tarifs\/"/);
  assert.match(tags, /rel="alternate" hreflang="de" href="https:\/\/melpis\.it\/de\/preise\/"/);
  assert.match(tags, /rel="alternate" hreflang="x-default" href="https:\/\/melpis\.it\/en\/pricing\/"/);
});

test("i18n: client adapter loads and provides Intl formatters", () => {
  const clientCode = fs.readFileSync(path.resolve(__dirname, "../../web/i18n-client.js"), "utf8");
  const dom = new JSDOM("<!DOCTYPE html><html><head></head><body></body></html>", {
    url: "https://melpis.it/app/",
    runScripts: "dangerously"
  });

  dom.window.eval(clientCode);
  const MelpisI18n = dom.window.MelpisI18n;

  assert.ok(MelpisI18n, "MelpisI18n must be exposed on window");
  assert.equal(typeof MelpisI18n.init, "function");
  assert.equal(typeof MelpisI18n.t, "function");
  assert.equal(typeof MelpisI18n.formatDate, "function");
  assert.equal(typeof MelpisI18n.formatCurrency, "function");

  // Format currency with IT locale
  const formattedIt = MelpisI18n.formatCurrency(69, "EUR");
  assert.match(formattedIt, /69/);

  dom.window.close();
});

test("i18n: Settings language selector updates the dashboard shell and persists", async () => {
  const clientCode = fs.readFileSync(path.resolve(__dirname, "../../web/i18n-client.js"), "utf8");
  const localeDir = path.resolve(__dirname, "../../web/locales");
  const dom = new JSDOM(`<!doctype html><html><head></head><body>
    <select id="sidebar-lang-select"><option value="it">Italiano</option><option value="en">English</option></select>
    <select id="settings-lang-select"><option value="it">Italiano</option><option value="en">English</option></select>
    <nav data-i18n-aria="dashboard:sidebar.nav_aria"><span data-i18n="dashboard:sidebar.nav_panoramica">Panoramica</span></nav>
    <h1 id="topbar-title" data-i18n="dashboard:topbar.title_panoramica">Panoramica</h1>
    <span data-i18n="dashboard:overview.attention_title">Richiede la tua attenzione</span>
  </body></html>`, { url: "https://melpis.it/app/", runScripts: "outside-only" });

  dom.window.localStorage.setItem("melpis_lang", "it");
  // Some i18next configurations return the unresolved path without its namespace.
  // The client must fall back to its loaded JSON bundle rather than showing that path.
  dom.window.i18next = {
    isInitialized: false,
    init: async function () { this.isInitialized = true; },
    addResourceBundle: function () {},
    changeLanguage: async function () {},
    t: function (key) { return String(key).split(":").pop(); },
  };
  dom.window.fetch = async (url) => {
    const relative = String(url).replace(/^\//, "").split("?")[0];
    const filePath = path.join(path.resolve(__dirname, "../../web"), relative);
    return {
      ok: fs.existsSync(filePath),
      status: fs.existsSync(filePath) ? 200 : 404,
      json: async () => JSON.parse(fs.readFileSync(filePath, "utf8")),
    };
  };
  dom.window.eval(clientCode);
  const i18n = dom.window.MelpisI18n;
  await i18n.init();
  assert.equal(dom.window.document.querySelector("[data-i18n='dashboard:sidebar.nav_panoramica']").textContent, "Panoramica");

  const changed = new Promise((resolve) => dom.window.addEventListener("melpis:lang-changed", resolve, { once: true }));
  const settingsSelect = dom.window.document.getElementById("settings-lang-select");
  settingsSelect.value = "en";
  settingsSelect.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  await changed;

  assert.equal(i18n.getLanguage(), "en");
  assert.equal(dom.window.document.documentElement.lang, "en");
  assert.equal(dom.window.document.getElementById("sidebar-lang-select").value, "en");
  assert.equal(settingsSelect.value, "en");
  assert.equal(dom.window.document.querySelector("[data-i18n='dashboard:sidebar.nav_panoramica']").textContent, "Overview");
  assert.equal(dom.window.document.getElementById("topbar-title").textContent, "Overview");
  assert.equal(dom.window.document.querySelector("[data-i18n='dashboard:overview.attention_title']").textContent, "Needs your attention");
  assert.equal(dom.window.localStorage.getItem("melpis_lang"), "en");
  assert.match(dom.window.document.cookie, /melpis_lang=en/);
  dom.window.close();
});

test("i18n: dashboard bundles stay synchronized and cover visible Settings, AI, and Inbox copy", () => {
  const root = path.resolve(__dirname, "../..");
  const localesRoot = path.join(root, "locales");
  const dashboard = fs.readFileSync(path.join(root, "web/index.html"), "utf8");
  const appCode = ["app.js", "dashboard-inbox.js", "dashboard-ai-settings.js", "dashboard-settings.js", "dashboard-integrations.js"]
    .map((file) => fs.readFileSync(path.join(root, "web", file), "utf8")).join("\n");
  const keys = new Set([
    ...Array.from(dashboard.matchAll(/data-i18n(?:-[a-z]+)?="([^"]+)"/g), (match) => match[1]),
    ...Array.from(appCode.matchAll(/t\("((?:settings|inbox|dashboard):[^\"]+)"\)/g), (match) => match[1]),
  ]);
  [
    "inbox:filters.empty_no_conversations",
    "inbox:filters.empty_connect_channels",
    "inbox:filters.connect_channels",
    "inbox:filters.empty_waiting",
    "inbox:filters.empty_no_results",
    "inbox:filters.empty_filter_results",
    "inbox:filters.show_all",
    "inbox:filters.escalation_failed",
    "inbox:filters.status_strip_ai",
    "inbox:filters.status_strip_pending",
    "inbox:filters.status_strip_resolved",
    "inbox:filters.empty_title",
    "inbox:filters.empty_thread_text",
    "inbox:filters.ticket_resolved",
    "inbox:filters.manual_reply_claim",
    "inbox:chat.claim_loading",
    "inbox:chat.claim_btn",
    "inbox:chat.release_btn",
    "inbox:chat.close_ticket",
    "inbox:chat.type_message",
    "inbox:chat.claimed_by_you",
    "inbox:chat.claimed_by_other",
    ...[
      "received_pending_ai", "processing", "handled", "queued", "sending_ambiguous",
      "sent", "delivered", "read", "failed",
    ].map((status) => `inbox:chat.message_status.${status}`),
  ].forEach((key) => keys.add(key));
  const resolveKey = (bundle, key) => {
    const separator = key.indexOf(":");
    let namespace = separator >= 0 ? key.slice(0, separator) : "common";
    let keyPath = separator >= 0 ? key.slice(separator + 1) : key;
    if (separator < 0) {
      const dot = key.indexOf(".");
      const possibleNamespace = dot >= 0 ? key.slice(0, dot) : "";
      if (["dashboard", "inbox", "settings"].includes(possibleNamespace)) {
        namespace = possibleNamespace;
        keyPath = key.slice(dot + 1);
      }
    }
    return keyPath.split(".").reduce((value, part) => value && value[part], bundle[namespace]);
  };

  for (const lang of SUPPORTED_LANGS) {
    const namespaces = {};
    for (const ns of ["dashboard", "settings", "inbox"]) {
      const sourcePath = path.join(localesRoot, lang, `${ns}.json`);
      const source = JSON.parse(fs.readFileSync(sourcePath, "utf8"));
      namespaces[ns] = source;
      for (const targetRoot of [path.join(root, "web/locales"), path.join(root, "web/landing/locales")]) {
        const served = JSON.parse(fs.readFileSync(path.join(targetRoot, lang, `${ns}.json`), "utf8"));
        assert.deepEqual(served, source, `${targetRoot} ${lang}/${ns}.json is synchronized with source`);
      }
    }
    for (const key of keys) {
      assert.equal(typeof resolveKey(namespaces, key), "string", `${lang} translation exists for ${key}`);
    }
  }
  for (const key of [
    "settings:ai_configuration.rules_empty",
    "settings:ai_configuration.remove_rule",
    "settings:ai_configuration.save_loading",
    "settings:ai_configuration.save_success",
    "settings:ai_configuration.save_error",
    "settings:ai_configuration.connection_error",
    "settings:ai_configuration.load_loading",
    "settings:ai_configuration.load_error",
  ]) {
    assert.ok(appCode.includes(`translate("${key}")`), `dynamic AI copy uses ${key}`);
  }
});

test("i18n: Inbox renders resolved translations in every supported language", async () => {
  const root = path.resolve(__dirname, "../..");
  const html = fs.readFileSync(path.join(root, "web/index.html"), "utf8");
  const clientCode = fs.readFileSync(path.join(root, "web/i18n-client.js"), "utf8");
  const dom = new JSDOM(html, { url: "http://localhost:4174/app/", runScripts: "outside-only" });
  const inbox = dom.window.document.querySelector('[data-view-panel="inbox"]');
  assert.ok(inbox, "Inbox panel exists in the dashboard shell");

  dom.window.localStorage.setItem("melpis_lang", "it");
  dom.window.fetch = async (url) => {
    const relative = new URL(String(url), dom.window.location.href).pathname.replace(/^\/+/, "");
    const filePath = path.join(root, "web", relative);
    const exists = fs.existsSync(filePath);
    return {
      ok: exists,
      status: exists ? 200 : 404,
      json: async () => JSON.parse(fs.readFileSync(filePath, "utf8")),
    };
  };
  dom.window.eval(clientCode);
  const i18n = dom.window.MelpisI18n;
  await i18n.init({ namespaces: ["inbox"] });

  for (const language of SUPPORTED_LANGS) {
    if (language !== "it") await i18n.setLanguage(language);
    for (const el of inbox.querySelectorAll("[data-i18n]")) {
      assert.doesNotMatch(el.textContent.trim(), /^(?:inbox:)?filters\./, `${language} resolves ${el.dataset.i18n}`);
    }
    for (const attribute of ["data-i18n-placeholder", "data-i18n-aria", "data-i18n-title"]) {
      for (const el of inbox.querySelectorAll(`[${attribute}]`)) {
        const targetAttribute = attribute === "data-i18n-placeholder" ? "placeholder" : attribute === "data-i18n-aria" ? "aria-label" : "title";
        assert.doesNotMatch(el.getAttribute(targetAttribute) || "", /^(?:inbox:)?filters\./, `${language} resolves ${el.getAttribute(attribute)}`);
      }
    }
  }

  dom.window.close();
});

test("dashboard navigation hrefs stay canonical in IT, EN, FR, ES, and DE", () => {
  const root = path.resolve(__dirname, "../..");
  const html = fs.readFileSync(path.join(root, "web/index.html"), "utf8");
  const routes = [
    "/app/overview", "/app/inbox", "/app/bookings", "/app/reviews",
    "/app/team", "/app/ai-simulator", "/app/knowledge", "/app/ai-settings",
  ];
  const dom = new JSDOM(html, { url: "https://melpis.test/app/overview" });
  const anchors = [...dom.window.document.querySelectorAll(".nav-item[data-app-view]")];
  assert.equal(anchors.length, routes.length);
  for (const lang of ["it", "en", "fr", "es", "de"]) {
    dom.window.document.documentElement.lang = lang;
    assert.deepEqual(anchors.map((anchor) => anchor.getAttribute("href")), routes);
  }
  dom.window.close();
});

test("i18n: dynamic Inbox copy resolves in every supported language", async () => {
  const clientCode = fs.readFileSync(path.resolve(__dirname, "../../web/i18n-client.js"), "utf8");
  const dom = new JSDOM("<!doctype html><html><head></head><body></body></html>", {
    url: "https://melpis.it/app/",
    runScripts: "outside-only",
  });
  dom.window.fetch = async (url) => {
    const relative = String(url).replace(/^\//, "").split("?")[0];
    const filePath = path.join(path.resolve(__dirname, "../../web"), relative);
    return {
      ok: fs.existsSync(filePath),
      status: fs.existsSync(filePath) ? 200 : 404,
      json: async () => JSON.parse(fs.readFileSync(filePath, "utf8")),
    };
  };
  dom.window.eval(clientCode);
  const i18n = dom.window.MelpisI18n;
  await i18n.init();

  const keys = [
    "inbox:filters.empty_no_conversations",
    "inbox:filters.empty_connect_channels",
    "inbox:filters.connect_channels",
    "inbox:filters.empty_waiting",
    "inbox:filters.empty_no_results",
    "inbox:filters.empty_filter_results",
    "inbox:filters.show_all",
    "inbox:filters.escalation_failed",
    "inbox:filters.status_strip_ai",
    "inbox:filters.status_strip_pending",
    "inbox:filters.status_strip_resolved",
    "inbox:filters.empty_title",
    "inbox:filters.empty_thread_text",
    "inbox:filters.ticket_resolved",
    "inbox:filters.manual_reply_claim",
    "inbox:chat.claim_loading",
    "inbox:chat.claim_btn",
    "inbox:chat.release_btn",
    "inbox:chat.close_ticket",
    "inbox:chat.type_message",
    "inbox:chat.claimed_by_you",
    "inbox:chat.claimed_by_other",
    ...[
      "received_pending_ai", "processing", "handled", "queued", "sending_ambiguous",
      "sent", "delivered", "read", "failed",
    ].map((status) => `inbox:chat.message_status.${status}`),
  ];

  for (const language of SUPPORTED_LANGS) {
    await i18n.setLanguage(language);
    for (const key of keys) {
      const value = i18n.t(key);
      assert.equal(typeof value, "string", `${language} has a string for ${key}`);
      assert.notEqual(value, key, `${language} does not expose raw key ${key}`);
      assert.ok(value.trim(), `${language} has non-empty copy for ${key}`);
    }
    const claimedByName = i18n.t("inbox:chat.claimed_by_other", { name: "Alex" });
    assert.ok(claimedByName.includes("Alex"), `${language} interpolates the assigned operator`);
    assert.doesNotMatch(claimedByName, /\{\{\s*name\s*\}\}/, `${language} resolves the operator placeholder`);
  }
  dom.window.close();
});



test("i18n: every dashboard runtime _tDash key is translated in all dashboard bundles", () => {
  const root = path.resolve(__dirname, "../..");
  const appCode = ["app.js", "dashboard-overview.js", "dashboard-reviews.js", "dashboard-bookings.js", "dashboard-knowledge.js", "dashboard-team.js", "dashboard-ai-simulator.js"]
    .map((file) => fs.readFileSync(path.join(root, "web", file), "utf8")).join("\n");
  const runtimeKeys = new Set(
    Array.from(appCode.matchAll(/_tDash\(\s*["']([^"']+\.runtime\.[^"']+)/gs), (match) => match[1]),
  );
  const resolveKey = (bundle, key) => key.split(".").reduce((value, part) => value && value[part], bundle);

  assert.ok(runtimeKeys.size > 0, "dashboard must expose runtime translation keys");
  assert.ok([...runtimeKeys].some((key) => key.startsWith("overview.runtime.")));
  assert.ok([...runtimeKeys].some((key) => key.startsWith("reviews.runtime.")));
  for (const lang of SUPPORTED_LANGS) {
    const source = JSON.parse(fs.readFileSync(path.join(root, "locales", lang, "dashboard.json"), "utf8"));
    const served = JSON.parse(fs.readFileSync(path.join(root, "web/locales", lang, "dashboard.json"), "utf8"));
    const landing = JSON.parse(fs.readFileSync(path.join(root, "web/landing/locales", lang, "dashboard.json"), "utf8"));
    assert.deepEqual(served, source, `web dashboard bundle is synchronized for ${lang}`);
    assert.deepEqual(landing, source, `landing dashboard bundle is synchronized for ${lang}`);
    for (const key of runtimeKeys) {
      assert.equal(typeof resolveKey(source, key), "string", `${lang} translation exists for ${key}`);
    }
    assert.equal(typeof source.knowledge.ask_placeholder, "string", `${lang} translation exists for knowledge.ask_placeholder`);
  }
});

test("production landing CSP has no inline-code exception", () => {
  const headers = fs.readFileSync(path.resolve(__dirname, "../../web/security-headers.conf"), "utf8");
  assert.match(headers, /script-src 'self';/);
  assert.match(headers, /style-src 'self';/);
  assert.doesNotMatch(headers, /(?:script-src|style-src) [^;]*'unsafe-inline'/);
  assert.match(headers, /style-src-attr 'unsafe-inline'/);
  childProcess.execFileSync(process.execPath, ["scripts/audit-csp.js"], {
    cwd: path.resolve(__dirname, "../.."),
    stdio: "pipe"
  });
});

test("FullCalendar uses only the deployed same-origin stylesheet under CSP", () => {
  const root = path.resolve(__dirname, "../..");
  const headers = fs.readFileSync(path.join(root, "web/security-headers.conf"), "utf8");
  const dashboard = fs.readFileSync(path.join(root, "web/index.html"), "utf8");
  const vendor = fs.readFileSync(path.join(root, "web/vendor/fullcalendar.min.js"), "utf8");
  assert.match(headers, /style-src 'self';/);
  assert.match(dashboard, /<link rel="stylesheet" href="\/app\/vendor\/fullcalendar\.css">/);
  assert.ok(fs.statSync(path.join(root, "web/vendor/fullcalendar.css")).size > 1000);
  assert.doesNotMatch(vendor, /function _e\(e\)\{let t=Re\.get\(e\)/, "runtime style creation is removed");
  childProcess.execFileSync(process.execPath, ["scripts/externalize-fullcalendar-styles.js", "--check"], {
    cwd: root,
    stdio: "pipe"
  });
  const dom = new JSDOM("<!doctype html><head></head><body></body>", { runScripts: "outside-only" });
  dom.window.eval(vendor);
  assert.equal(dom.window.document.querySelectorAll("style[data-fullcalendar]").length, 0);
  dom.window.close();
});

test("pricing copy never promises automatic electronic invoicing", () => {
  const pricing = fs.readFileSync(path.resolve(__dirname, "../../web/landing/prezzi/index.html"), "utf8");
  const extraCopy = fs.readFileSync(path.resolve(__dirname, "../../scripts/i18n-extra-copy.js"), "utf8");
  assert.match(pricing, /Ricevuta Stripe immediata; fattura fiscale su richiesta\./);
  assert.doesNotMatch(pricing, /Sistema di Interscambio|codice SDI/i);
  assert.doesNotMatch(extraCopy, /standard compliant tax invoices are automatically generated|facturas oficiales se generan y envían automáticamente|factures avec mentions fiscales légales sont automatiquement générées|Rechnungen mit ausgewiesener Mehrwertsteuer automatisch erstellt/i);
});

test("localized pricing has one native invoice answer and no Italian residue", () => {
  const routes = {
    en: ["pricing", "Your Stripe receipt is available immediately; request a fiscal invoice from us when needed."],
    es: ["precios", "Recibes el recibo de Stripe de inmediato; solicita la factura fiscal cuando la necesites."],
    fr: ["tarifs", "Votre reçu Stripe est disponible immédiatement ; demandez une facture fiscale si nécessaire."],
    de: ["preise", "Ihre Stripe-Quittung ist sofort verfügbar; fordern Sie bei Bedarf eine steuerliche Rechnung an."],
  };
  for (const [lang, [route, invoiceCopy]] of Object.entries(routes)) {
    const page = fs.readFileSync(path.resolve(__dirname, `../../web/landing/${lang}/${route}/index.html`), "utf8");
    const faq = page.match(/<section class="faq-section" id="faq">([\s\S]*?)<\/section>/);
    assert.ok(faq, `${lang} commercial FAQ section exists`);
    assert.equal(faq[1].split(invoiceCopy).length - 1, 1, `${lang} has exactly one native invoice answer`);
    assert.doesNotMatch(page, /Ricevuta Stripe immediata; fattura fiscale su richiesta\./, `${lang} has no Italian invoice copy`);
  }
});
