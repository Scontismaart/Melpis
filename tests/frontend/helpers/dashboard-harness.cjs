const fs = require("node:fs");
const path = require("node:path");
const { JSDOM, VirtualConsole } = require("jsdom");

const root = path.resolve(__dirname, "../../..");
const scriptFiles = [
  "web/config.js",
  "web/i18n-client.js",
  "web/dialog-focus.js",
  "web/dashboard-router.js",
  "web/dashboard-shared.js",
  "web/dashboard-inbox.js",
  "web/dashboard-ai-settings.js",
  "web/dashboard-settings.js",
  "web/dashboard-integrations.js",
  "web/dashboard-overview.js",
  "web/dashboard-reviews.js",
  "web/dashboard-knowledge.js",
  "web/dashboard-team.js",
  "web/dashboard-ai-simulator.js",
  "web/dashboard-bookings.js",
  "web/app.js",
  "web/mfa.js",
];

function jsonResponse(body, { status = 200, headers = {} } = {}) {
  const headerMap = new Map(Object.entries(headers).map(([key, value]) => [key.toLowerCase(), value]));
  const response = {
    status,
    ok: status >= 200 && status < 300,
    headers: { get: (name) => headerMap.get(String(name).toLowerCase()) || null },
    async json() { return body; },
    async text() { return JSON.stringify(body); },
    clone() { return jsonResponse(body, { status, headers }); },
  };
  return response;
}

function responseBody(pathname) {
  if (/^\/locales\//.test(pathname)) return {};
  if (pathname === "/api/auth/me") {
    return { user_id: "user-1", organization_id: "org-1", email: "owner@example.test", ruolo: "owner" };
  }
  if (pathname === "/api/auth/mfa") return { aal: "aal1", factors: [] };
  if (pathname === "/api/onboarding/profilo") return { profilo: {} };
  if (pathname === "/api/onboarding/verticali") return { verticali: [], lingue_disponibili: ["it", "en", "es", "fr", "de"] };
  if (pathname === "/api/team/organizations") return { organizations: [{ id: "org-1", name: "Org One" }] };
  if (pathname === "/api/team/invitations") return { invitations: [] };
  if (pathname === "/api/team/members") return { members: [], total: 1, users_limit: null, can_add_more: true };
  if (pathname === "/api/team/inbox") return { members: [] };
  if (pathname === "/api/inbox/team") return { members: [] };
  if (pathname === "/api/inbox/tickets") return { tickets: [] };
  if (pathname === "/api/recensioni") return { recensioni: [] };
  if (pathname === "/api/report") return {};
  if (pathname === "/api/bookings/settings") return { capienze_orarie: {}, fasce_orarie: [] };
  if (pathname === "/api/bookings/semaforo") return [];
  if (pathname === "/api/bookings") return [];
  if (pathname === "/api/dashboard/prioritari" || pathname === "/api/dashboard") return [];
  if (pathname === "/api/ui/summary") return {};
  if (pathname === "/api/documenti/conteggio") return { chunk_indicizzati: 0 };
  if (pathname === "/api/documenti/elenco") return { documenti: [] };
  if (pathname === "/api/conoscenza/summary") {
    return { faq: {}, documenti: {}, web: {}, dati_struttura: {}, conflitti_totali: 0 };
  }
  if (pathname === "/api/conoscenza/dati-struttura") return { servizi: [], orari: "" };
  if (pathname === "/api/impostazioni/organizzazione") return { timezone: "UTC", timezone_disponibili: ["UTC"] };
  if (pathname === "/api/whatsapp/settings" || pathname === "/api/instagram/account") return {};
  if (pathname === "/api/billing/subscription") return {};
  if (/^\/api\/(calendar|reviews\/google|v1\/integrations\/booking|v1\/integrations\/airtable)\//.test(pathname)) return {};
  if (/^\/api\/audit(?:\/|$)/.test(pathname)) return { items: [], events: [], total: 0 };
  return undefined;
}

function createDashboardHarness({
  url = "https://melpis.test/app/overview",
  overrides = {},
  selectedOrganization = "org-1",
} = {}) {
  const html = fs.readFileSync(path.join(root, "web/index.html"), "utf8");
  const jsdomErrors = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on("jsdomError", (error) => jsdomErrors.push(error));
  const dom = new JSDOM(html, { url, runScripts: "outside-only", pretendToBeVisual: true, virtualConsole });
  const { window: win } = dom;
  const requests = [];
  const unexpectedRequests = [];
  const listenerRegistrations = [];
  const intervals = new Map();
  const timeouts = new Map();
  const errors = [];
  const windowErrors = [];
  let routerConstructions = 0;
  let routerInstance = null;
  let bookingsInstance = null;
  const calendars = [];
  let domContentLoadedEvents = 0;
  let intervalId = 0;
  let timeoutId = 0;

  if (selectedOrganization !== null) {
    win.localStorage.setItem("melpis_selected_organization", selectedOrganization);
  }
  win.document.cookie = "wa_csrf=csrf-test-token; Path=/; SameSite=Lax";
  win.document.addEventListener("DOMContentLoaded", () => { domContentLoadedEvents += 1; }, { once: true });
  win.addEventListener("error", (event) => windowErrors.push(event.error || event.message));
  win.console.error = (...args) => errors.push(args);
  win.console.warn = (...args) => errors.push(args);
  win.alert = () => {};
  win.confirm = () => true;
  win.matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
  win.requestAnimationFrame = (callback) => {
    callback(win.performance.now());
    return 1;
  };

  const originalAddEventListener = win.EventTarget.prototype.addEventListener;
  win.EventTarget.prototype.addEventListener = function trackedAddEventListener(type, listener, options) {
    listenerRegistrations.push({ target: this, type: String(type), listener });
    return originalAddEventListener.call(this, type, listener, options);
  };

  win.setInterval = (callback, delay = 0) => {
    const id = ++intervalId;
    intervals.set(id, { callback, delay });
    return id;
  };
  win.clearInterval = (id) => intervals.delete(id);
  win.setTimeout = (callback, delay = 0) => {
    const id = ++timeoutId;
    timeouts.set(id, { callback, delay });
    return id;
  };
  win.clearTimeout = (id) => timeouts.delete(id);

  win.fetch = async (input, options = {}) => {
    const urlValue = typeof input === "string" ? input : input.url;
    const parsed = new URL(urlValue, win.location.href);
    const method = String(options.method || "GET").toUpperCase();
    const request = { url: parsed.href, pathname: parsed.pathname, search: parsed.search, method, options };
    requests.push(request);
    const methodOverrideKey = `${method} ${parsed.pathname}`;
    const hasOverride = Object.hasOwn(overrides, methodOverrideKey) || Object.hasOwn(overrides, parsed.pathname);
    const override = Object.hasOwn(overrides, methodOverrideKey)
      ? overrides[methodOverrideKey]
      : overrides[parsed.pathname];
    if (!hasOverride && !["GET", "HEAD", "OPTIONS"].includes(method)) {
      unexpectedRequests.push(request);
      return jsonResponse({ detail: `No mutation mock for ${method} ${parsed.pathname}` }, { status: 599 });
    }
    const body = hasOverride ? override : responseBody(parsed.pathname);
    if (body === undefined) {
      unexpectedRequests.push(request);
      return jsonResponse({ detail: `No strict mock for ${method} ${parsed.pathname}` }, { status: 599 });
    }
    if (typeof body === "function") return body(request, jsonResponse);
    if (body && body.__response) return jsonResponse(body.body, body.__response);
    return jsonResponse(body);
  };

  // Keep calendar behavior deterministic while executing the actual app code.
  win.FullCalendar = {
    Calendar: class Calendar {
      constructor(element, options) {
        calendars.push(this);
        this.el = element;
        this.options = options;
        this.currentDate = new Date("2026-10-08T12:00:00");
        this.view = { type: options.initialView };
        this.events = [];
        this.hasRendered = false;
      }
      updateSize() {}
      removeAllEvents() { this.events = []; }
      addEvent(event) { this.events.push(event); }
      getDate() { return this.currentDate; }
      _notifyDatesSet() {
        this.options.datesSet?.({ start: this.currentDate, end: this.currentDate, view: this.view });
      }
      render() {
        if (!this.hasRendered) {
          this.hasRendered = true;
          this._notifyDatesSet();
        }
      }
      gotoDate(value) {
        const nextDate = new Date(`${value}T12:00:00`);
        if (nextDate.getTime() === this.currentDate.getTime()) return;
        this.currentDate = nextDate;
        this._notifyDatesSet();
      }
      prev() {
        this.currentDate.setDate(this.currentDate.getDate() - (this.view.type === "timeGridDay" ? 1 : 7));
        this._notifyDatesSet();
      }
      next() {
        this.currentDate.setDate(this.currentDate.getDate() + (this.view.type === "timeGridDay" ? 1 : 7));
        this._notifyDatesSet();
      }
      today() { this.currentDate = new Date("2026-10-08T12:00:00"); this._notifyDatesSet(); }
      changeView(type) { this.view.type = type; this._notifyDatesSet(); }
      setOption(name, value) { this.options[name] = value; }
      unselect() {}
      destroy() { this.events = []; }
    },
  };
  win.DOMPurify = { sanitize: (value) => String(value ?? "") };
  win.i18next = {
    isInitialized: false,
    addResourceBundle() {},
    async init() { this.isInitialized = true; },
    t(key, options = {}) {
      return (options.defaultValue || String(key).split(":").pop())
        .replace(/{{(\w+)}}/g, (match, name) => options[name] ?? match);
    },
  };

  for (const file of scriptFiles) {
    if (file === "web/app.js") {
      const bookingsApi = win.MelpisDashboardBookings;
      win.MelpisDashboardBookings = Object.freeze({
        create(deps) { bookingsInstance = bookingsApi.create(deps); return bookingsInstance; },
      });
      const routerApi = win.MelpisDashboardRouter;
      win.MelpisDashboardRouter = Object.freeze({
        ...routerApi,
        createDashboardRouter(options) {
          routerConstructions += 1;
          routerInstance = routerApi.createDashboardRouter(options);
          return routerInstance;
        },
      });
    }
    win.eval(fs.readFileSync(path.join(root, file), "utf8"));
  }

  const scriptOrder = [...win.document.querySelectorAll("script[src]")]
    .map((script) => new URL(script.src, win.location.href).pathname);
  const domReady = new Promise((resolve) => {
    if (win.document.readyState === "interactive" || win.document.readyState === "complete") resolve();
    else win.document.addEventListener("DOMContentLoaded", resolve, { once: true });
  });

  async function settle() {
    for (let index = 0; index < 16; index += 1) await Promise.resolve();
  }

  async function waitFor(predicate, message = "dashboard startup did not settle") {
    for (let attempt = 0; attempt < 100; attempt += 1) {
      await settle();
      if (predicate()) return;
      await new Promise((resolve) => setTimeout(resolve, 1));
    }
    throw new Error(message);
  }

  async function ready() {
    await domReady;
    await waitFor(
      () => win.document.body.classList.contains("authenticated")
        && [...win.document.querySelectorAll("[data-view-panel]")].filter((panel) => !panel.classList.contains("view-hidden")).length === 1,
    );
    await settle();
  }

  function navigate(view, suffix = "") {
    const anchor = win.document.querySelector(`[data-app-view="${view}"]`);
    if (!anchor) throw new Error(`missing semantic route anchor: ${view}`);
    const url = new URL(anchor.href, win.location.href);
    if (suffix) url.search = suffix;
    anchor.href = `${url.pathname}${url.search}${url.hash}`;
    anchor.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true, button: 0 }));
    return settle();
  }

  function close() {
    dom.window.close();
  }

  async function dispose() {
    await settle();
    await new Promise((resolve) => setImmediate(resolve));
    close();
    await new Promise((resolve) => setImmediate(resolve));
  }

  return {
    dom,
    window: win,
    document: win.document,
    requests,
    unexpectedRequests,
    listenerRegistrations,
    intervals,
    timeouts,
    errors,
    windowErrors,
    jsdomErrors,
    domReady,
    get domContentLoadedEvents() { return domContentLoadedEvents; },
    get routerConstructions() { return routerConstructions; },
    get router() { return routerInstance; },
    get bookings() { return bookingsInstance; },
    calendars,
    scriptOrder,
    settle,
    waitFor,
    ready,
    navigate,
    close,
    dispose,
  };
}

module.exports = { createDashboardHarness };
