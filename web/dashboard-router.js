/* Lightweight same-origin router for the authenticated dashboard shell. */
/* global module */
(function attachDashboardRouter(global, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (global) global.MelpisDashboardRouter = api;
})(typeof window !== "undefined" ? window : globalThis, function createDashboardRouterApi() {
  "use strict";

  const ROUTES = Object.freeze({
    panoramica: "/app/overview",
    inbox: "/app/inbox",
    prenotazioni: "/app/bookings",
    recensioni: "/app/reviews",
    team: "/app/team",
    assistente: "/app/ai-simulator",
    conoscenza: "/app/knowledge",
    "configurazione-ai": "/app/ai-settings",
    onboarding: "/app/onboarding",
    impostazioni: "/app/settings",
  });
  const SETTINGS_TABS = Object.freeze([
    "generale",
    "sicurezza",
    "whatsapp",
    "instagram",
    "calendar",
    "reviews",
    "booking-pms",
    "airtable",
    "piano",
    "fatturazione",
    "audit",
  ]);
  const SETTINGS_TAB_SET = new Set(SETTINGS_TABS);
  const SETTINGS_TAB_ALIASES = Object.freeze({
    integrazioni: "whatsapp",
    account: "piano",
    booking: "booking-pms",
    pms: "booking-pms",
  });
  const VIEWS_BY_PATH = Object.freeze(
    Object.fromEntries(Object.entries(ROUTES).map(([view, route]) => [route, view])),
  );

  function normalizeAppPath(pathname) {
    if (typeof pathname !== "string") return ROUTES.panoramica;
    const normalized = pathname.replace(/\/+$/, "") || "/";
    return normalized === "/app" ? ROUTES.panoramica : normalized;
  }

  function resolveRoute(pathname) {
    const normalized = normalizeAppPath(pathname);
    const view = VIEWS_BY_PATH[normalized];
    const path = view ? normalized : ROUTES.panoramica;
    return { view: view || "panoramica", path, canonical: pathname === path };
  }

  function pathForView(viewKey) {
    return Object.hasOwn(ROUTES, viewKey) ? ROUTES[viewKey] : null;
  }

  function normalizeSettingsTab(tab) {
    if (SETTINGS_TAB_SET.has(tab)) return tab;
    return Object.hasOwn(SETTINGS_TAB_ALIASES, tab) ? SETTINGS_TAB_ALIASES[tab] : "generale";
  }

  function settingsTabFromSearch(search) {
    return normalizeSettingsTab(new URLSearchParams(search || "").get("tab"));
  }

  function settingsSearchForTab(search, tab) {
    const params = new URLSearchParams(search || "");
    params.delete("tab");
    const normalizedTab = normalizeSettingsTab(tab);
    if (normalizedTab !== "generale") params.set("tab", normalizedTab);
    const query = params.toString();
    return query ? `?${query}` : "";
  }

  function isOrdinaryPrimaryClick(event) {
    return Boolean(event)
      && !event.defaultPrevented
      && event.button === 0
      && !event.metaKey
      && !event.ctrlKey
      && !event.shiftKey
      && !event.altKey;
  }

  function createDashboardRouter({ window: win = globalThis, render = () => {} } = {}) {
    if (!win?.location || !win?.history || !win?.document) {
      throw new TypeError("Dashboard router requires a browser window");
    }

    let started = false;
    let currentView = null;
    let currentLocation = null;

    function locationKey() {
      return `${win.location.pathname}${win.location.search}${win.location.hash}`;
    }

    function renderLocation() {
      const resolved = resolveRoute(win.location.pathname);
      if (win.location.pathname !== resolved.path) {
        win.history.replaceState(
          win.history.state,
          "",
          `${resolved.path}${win.location.search}${win.location.hash}`,
        );
      }
      const location = locationKey();
      const settingsLocationChanged = resolved.view === "impostazioni" && currentLocation !== location;
      if (currentView !== resolved.view || settingsLocationChanged) {
        currentView = resolved.view;
        render(resolved.view);
        // The renderer may canonicalize location.search (for example an invalid Settings tab).
        // Capture the final location so this normalization does not trigger a redundant render.
      }
      currentLocation = locationKey();
      return resolved.view;
    }

    function navigateTo(viewKey, options = {}) {
      const { search = "", hash = "", replaceSuffix = false } = options;
      const path = pathForView(viewKey);
      if (!path) return false;
      if (win.location.pathname === path && currentView === viewKey) {
        if ((search || hash || replaceSuffix)
          && `${win.location.search}${win.location.hash}` !== `${search}${hash}`) {
          win.history.replaceState(win.history.state, "", `${path}${search}${hash}`);
        }
        renderLocation();
        return true;
      }

      if (win.location.pathname !== path) {
        win.history.pushState(win.history.state, "", `${path}${search}${hash}`);
      } else if (`${win.location.search}${win.location.hash}` !== `${search}${hash}`) {
        win.history.pushState(win.history.state, "", `${path}${search}${hash}`);
      }
      renderLocation();
      return true;
    }

    function handlePopState() {
      renderLocation();
    }

    function handleDocumentClick(event) {
      if (!isOrdinaryPrimaryClick(event)) return;

      const anchor = event.target instanceof win.Element
        ? event.target.closest("a[data-app-view]")
        : null;
      if (!anchor || anchor.hasAttribute("download")) return;
      const target = anchor.getAttribute("target");
      if (target && target.toLowerCase() !== "_self") return;

      const viewKey = anchor.dataset.appView;
      if (!pathForView(viewKey)) return;
      let url;
      try {
        url = new win.URL(anchor.href, win.location.href);
      } catch {
        return;
      }
      if (url.origin !== win.location.origin || url.username || url.password) return;
      if (url.pathname !== pathForView(viewKey)) return;

      event.preventDefault();
      navigateTo(viewKey, { search: url.search, hash: url.hash });
    }

    function start() {
      if (started) return currentView;
      started = true;
      win.document.addEventListener("click", handleDocumentClick);
      win.addEventListener("popstate", handlePopState);
      return renderLocation();
    }

    function invalidateCurrentView() {
      currentView = null;
    }

    return Object.freeze({
      start,
      navigateTo,
      handlePopState,
      invalidateCurrentView,
      getCurrentView: () => currentView,
    });
  }

  return Object.freeze({
    ROUTES,
    SETTINGS_TABS,
    normalizeAppPath,
    normalizeSettingsTab,
    settingsTabFromSearch,
    settingsSearchForTab,
    isOrdinaryPrimaryClick,
    resolveRoute,
    pathForView,
    createDashboardRouter,
  });
});
