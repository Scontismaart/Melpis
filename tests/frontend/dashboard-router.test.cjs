const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");
const routerApi = require("../../web/dashboard-router.js");

const root = path.resolve(__dirname, "../..");
const primaryRoutes = {
  panoramica: "/app/overview",
  inbox: "/app/inbox",
  prenotazioni: "/app/bookings",
  recensioni: "/app/reviews",
  team: "/app/team",
  assistente: "/app/ai-simulator",
  conoscenza: "/app/knowledge",
  "configurazione-ai": "/app/ai-settings",
};
const routes = { ...primaryRoutes, impostazioni: "/app/settings" };

function createDom(url = "https://melpis.test/app/overview") {
  return new JSDOM(`<!doctype html><html><body><nav>
    ${Object.entries(routes).map(([view, href]) => `<a class="nav-item" data-app-view="${view}" href="${href}">${view}</a>`).join("")}
    <a id="priority-inbox-link" href="/app/inbox">inbox CTA</a>
    <a id="activity-view-all-btn" href="/app/inbox">activity CTA</a>
  </nav></body></html>`, { url, pretendToBeVisual: true });
}

function createIntegratedApp(url) {
  const html = fs.readFileSync(path.join(root, "web/index.html"), "utf8");
  const dom = new JSDOM(html, { url, pretendToBeVisual: true, runScripts: "outside-only" });
  const { window } = dom;
  window.MelpisDashboardRouter = routerApi;
  window.eval(fs.readFileSync(path.join(root, "web/dashboard-settings.js"), "utf8"));
  let currentView = null;
  let router = null;
  const settings = window.MelpisDashboardSettings.create({
    API_BASE: "",
    apiFetch: async () => ({ ok: true, json: async () => ({}) }),
    getContext: () => ({ view: currentView, userId: "qa-user", sessionOrganizationId: "qa-org", selectedOrganizationId: "qa-org" }),
    getRole: () => "owner",
    t: (key) => key,
    _tDash: (_key, fallback) => fallback,
    sanitize: (value) => String(value ?? ""),
    toast() {},
    securityStatus() {},
    localeCorrente: () => "it-IT",
    navigateTo: (...args) => router.navigateTo(...args),
    normalizeSettingsTab: (tab) => routerApi.normalizeSettingsTab(tab),
    settingsSearchForTab: (search, tab) => routerApi.settingsSearchForTab(search, tab),
    closeAccountMenu() {},
    loadMfaStatus() {},
    getIntegrationsApi: () => null,
  });
  const context = {
    settings,
    apriVistaImpostazioni: (category) => settings.openCategory(category),
    navigaTabImpostazioni: (category) => settings.openCategory(category),
  };
  router = routerApi.createDashboardRouter({
    window,
    render: (view) => {
      if (currentView === "impostazioni" && view !== "impostazioni") settings.onExit();
      currentView = view;
      window.document.querySelectorAll("[data-view-panel]").forEach((panel) => {
        panel.classList.toggle("view-hidden", panel.dataset.viewPanel !== view);
      });
      if (view === "impostazioni") {
        const tab = routerApi.settingsTabFromSearch(window.location.search);
        const search = routerApi.settingsSearchForTab(window.location.search, tab);
        if (search !== window.location.search) {
          window.history.replaceState(window.history.state, "", `${window.location.pathname}${search}${window.location.hash}`);
        }
        settings.onEnter(tab);
      }
    },
  });
  return { dom, router, context };
}

function assertSettingsDom(dom, tab) {
  const settingsView = dom.window.document.querySelector('[data-view-panel="impostazioni"]');
  const activeTab = settingsView.querySelector(`[data-settings-cat-btn="${tab}"]`);
  const visiblePanels = [...settingsView.querySelectorAll("[data-settings-panel]")].filter((panel) => !panel.hidden);
  const topbarTitle = dom.window.document.getElementById("topbar-title");
  const titleKey = `dashboard:topbar.${{
    piano: "title_account",
    fatturazione: "title_account",
    whatsapp: "title_whatsapp",
    instagram: "title_instagram",
    calendar: "title_calendar",
    reviews: "title_reviews_settings",
    "booking-pms": "title_pms",
    airtable: "title_airtable",
  }[tab] || "title_impostazioni"}`;

  assert.equal(settingsView.classList.contains("view-hidden"), false, "Settings view is visible");
  assert.ok(activeTab, `Settings tab ${tab} exists`);
  assert.equal(activeTab.classList.contains("active"), true, `${tab} tab is active`);
  assert.equal(activeTab.getAttribute("aria-selected"), "true", `${tab} tab is selected for assistive technology`);
  assert.deepEqual(visiblePanels.map((panel) => panel.dataset.settingsPanel), [tab]);
  assert.equal(topbarTitle.dataset.i18n, titleKey);
  assert.equal(topbarTitle.textContent, titleKey, "Settings heading is synchronized to the active tab");
}

test("route table maps the eight primary views and auxiliary settings view to canonical paths", () => {
  assert.deepEqual(routerApi.ROUTES, routes);
  for (const [view, route] of Object.entries(routes)) {
    assert.equal(routerApi.pathForView(view), route);
    assert.equal(routerApi.resolveRoute(route).view, view);
  }
});

test("legacy, trailing slash, and unknown app paths resolve canonically", () => {
  assert.deepEqual(routerApi.resolveRoute("/app"), { view: "panoramica", path: "/app/overview", canonical: false });
  assert.deepEqual(routerApi.resolveRoute("/app/"), { view: "panoramica", path: "/app/overview", canonical: false });
  assert.deepEqual(routerApi.resolveRoute("/app/inbox/"), { view: "inbox", path: "/app/inbox", canonical: false });
  assert.deepEqual(routerApi.resolveRoute("/app/settings/"), { view: "impostazioni", path: "/app/settings", canonical: false });
  assert.deepEqual(routerApi.resolveRoute("/app/not-real"), { view: "panoramica", path: "/app/overview", canonical: false });
});

test("settings category URLs are whitelisted and preserve unrelated query parameters", () => {
  assert.equal(routerApi.settingsTabFromSearch("?tab=reviews&source=calendar"), "reviews");
  assert.equal(routerApi.settingsTabFromSearch("?tab=not-real&source=calendar"), "generale");
  assert.equal(routerApi.settingsTabFromSearch("?tab=integrazioni"), "whatsapp");
  assert.equal(routerApi.settingsTabFromSearch("?tab=profilo"), "generale");
  for (const inheritedKey of ["constructor", "toString", "__proto__"]) {
    assert.equal(routerApi.settingsTabFromSearch(`?tab=${inheritedKey}`), "generale");
  }
  assert.equal(routerApi.settingsTabFromSearch("?source=calendar"), "generale");
  assert.equal(routerApi.settingsSearchForTab("?source=calendar&tab=not-real&x=1", "reviews"), "?source=calendar&x=1&tab=reviews");
  assert.equal(routerApi.settingsSearchForTab("?source=search", "integrazioni"), "?source=search&tab=whatsapp");
  assert.equal(routerApi.settingsSearchForTab("?source=calendar&tab=reviews", "generale"), "?source=calendar");
  assert.equal(routerApi.settingsSearchForTab("?source=calendar&tab=reviews", "not-real"), "?source=calendar");
});

test("ordinary-click predicate preserves modified and non-primary clicks", () => {
  const dom = createDom();
  const click = (options = {}) => new dom.window.MouseEvent("click", {
    bubbles: true,
    cancelable: true,
    button: 0,
    ...options,
  });
  assert.equal(routerApi.isOrdinaryPrimaryClick(click()), true);
  for (const options of [
    { ctrlKey: true }, { metaKey: true }, { shiftKey: true }, { altKey: true }, { button: 1 },
  ]) assert.equal(routerApi.isOrdinaryPrimaryClick(click(options)), false);
  const prevented = click();
  prevented.preventDefault();
  assert.equal(routerApi.isOrdinaryPrimaryClick(prevented), false);
  dom.window.close();
});

test("initial deep link renders the view and canonicalizes while preserving query, hash, and state", () => {
  const dom = createDom("https://melpis.test/app/inbox/?filter=open#latest");
  const state = { marker: "existing" };
  dom.window.history.replaceState(state, "", dom.window.location.href);
  const rendered = [];
  const router = routerApi.createDashboardRouter({ window: dom.window, render: (view) => rendered.push(view) });
  router.start();
  assert.equal(dom.window.location.pathname, "/app/inbox");
  assert.equal(dom.window.location.search, "?filter=open");
  assert.equal(dom.window.location.hash, "#latest");
  assert.deepEqual(dom.window.history.state, state);
  assert.deepEqual(rendered, ["inbox"]);
  dom.window.close();
});

test("settings and AI settings remain separate routes and a settings deep link keeps its suffix", () => {
  const dom = createDom("https://melpis.test/app/settings/?tab=calendar&source=oauth#panel");
  const state = { marker: "settings-deep-link" };
  dom.window.history.replaceState(state, "", dom.window.location.href);
  const rendered = [];
  const router = routerApi.createDashboardRouter({ window: dom.window, render: (view) => rendered.push(view) });
  router.start();

  assert.equal(dom.window.location.pathname, "/app/settings");
  assert.equal(dom.window.location.search, "?tab=calendar&source=oauth");
  assert.equal(dom.window.location.hash, "#panel");
  assert.deepEqual(dom.window.history.state, state);
  assert.deepEqual(rendered, ["impostazioni"]);
  assert.equal(routerApi.resolveRoute("/app/ai-settings").view, "configurazione-ai");
  assert.notEqual(routerApi.pathForView("impostazioni"), routerApi.pathForView("configurazione-ai"));
  dom.window.close();
});

test("settings navigation preserves query, hash, and history state", () => {
  const dom = createDom();
  const state = { marker: "preserved" };
  dom.window.history.replaceState(state, "", dom.window.location.href);
  const rendered = [];
  const router = routerApi.createDashboardRouter({ window: dom.window, render: (view) => rendered.push(view) });
  router.start();
  assert.equal(router.navigateTo("impostazioni", { search: "?tab=piano&source=menu", hash: "#billing" }), true);

  assert.equal(dom.window.location.pathname, "/app/settings");
  assert.equal(dom.window.location.search, "?tab=piano&source=menu");
  assert.equal(dom.window.location.hash, "#billing");
  assert.deepEqual(dom.window.history.state, state);
  assert.deepEqual(rendered, ["panoramica", "impostazioni"]);
  assert.equal(router.navigateTo("impostazioni", { search: "?source=menu", hash: "#billing" }), true);
  assert.equal(router.navigateTo("impostazioni", { search: "", hash: "#billing" }), true);
  assert.equal(router.navigateTo("impostazioni", { search: "", hash: "", replaceSuffix: true }), true);
  assert.equal(dom.window.location.search, "");
  assert.equal(dom.window.location.hash, "");
  assert.equal(dom.window.history.length, 2);
  assert.deepEqual(dom.window.history.state, state);
  dom.window.close();
});

test("initial Settings default query renders the real General tab and panel", () => {
  const { dom, router } = createIntegratedApp("https://melpis.test/app/settings?tab=generale");
  router.start();
  assert.equal(router.getCurrentView(), "impostazioni");
  assertSettingsDom(dom, "generale");
  dom.window.close();
});

test("direct open and hard initialization render the requested Calendar tab", () => {
  const { dom, router } = createIntegratedApp("https://melpis.test/app/settings?tab=calendar");
  router.start();
  assert.equal(router.getCurrentView(), "impostazioni");
  assertSettingsDom(dom, "calendar");
  dom.window.close();
});

test("same-view Settings query replacement synchronizes URL, active tab, title, and panel", () => {
  const { dom, router } = createIntegratedApp("https://melpis.test/app/settings?tab=generale");
  router.start();
  const entriesBefore = dom.window.history.length;

  router.navigateTo("impostazioni", { search: "?tab=calendar", replaceSuffix: true });

  assert.equal(dom.window.location.search, "?tab=calendar");
  assert.equal(dom.window.history.length, entriesBefore, "replaceSuffix must not create a history entry");
  assertSettingsDom(dom, "calendar");
  dom.window.close();
});

test("same-view Settings query navigation preserves replace semantics and synchronizes a second tab", () => {
  const { dom, router } = createIntegratedApp("https://melpis.test/app/settings?tab=calendar");
  router.start();
  const entriesBefore = dom.window.history.length;

  router.navigateTo("impostazioni", { search: "?tab=piano" });

  assert.equal(dom.window.location.search, "?tab=piano");
  assert.equal(dom.window.history.length, entriesBefore, "same-view Settings tab changes keep the current replace semantics");
  assertSettingsDom(dom, "piano");
  dom.window.close();
});

test("programmatic Settings entry and tab actions rely on the location-driven renderer", () => {
  const { dom, router, context } = createIntegratedApp("https://melpis.test/app/overview?source=callback#status");
  router.start();

  assert.equal(context.apriVistaImpostazioni("calendar"), true);
  assert.equal(dom.window.location.pathname, "/app/settings");
  assert.equal(dom.window.location.search, "?source=callback&tab=calendar");
  assert.equal(dom.window.location.hash, "#status");
  assertSettingsDom(dom, "calendar");

  assert.equal(context.navigaTabImpostazioni("piano"), true);
  assert.equal(dom.window.location.search, "?source=callback&tab=piano");
  assertSettingsDom(dom, "piano");

  assert.equal(context.apriVistaImpostazioni("reviews"), true);
  assert.equal(dom.window.location.search, "?source=callback&tab=reviews");
  assertSettingsDom(dom, "reviews");
  dom.window.close();
});

test("Back and Forward synchronize Settings query history with the real DOM", async () => {
  const { dom, router } = createIntegratedApp("https://melpis.test/app/settings?tab=generale");
  const { window } = dom;
  router.start();
  window.history.pushState({}, "", "/app/settings?tab=calendar");
  window.history.pushState({}, "", "/app/settings?tab=piano");

  const travel = async (direction, search, tab) => {
    const popstate = new Promise((resolve) => window.addEventListener("popstate", resolve, { once: true }));
    window.history[direction]();
    await popstate;
    assert.equal(window.location.search, search);
    assertSettingsDom(dom, tab);
  };
  await travel("back", "?tab=calendar", "calendar");
  await travel("back", "", "generale");
  await travel("forward", "?tab=calendar", "calendar");
  await travel("forward", "?tab=piano", "piano");
  dom.window.close();
});

test("every allowlisted Settings tab opens directly with its matching panel", () => {
  for (const tab of routerApi.SETTINGS_TABS) {
    const { dom, router } = createIntegratedApp(`https://melpis.test/app/settings?tab=${tab}`);
    router.start();
    assert.equal(
      dom.window.location.search,
      routerApi.settingsSearchForTab(`?tab=${tab}`, tab),
      `${tab} query follows the existing canonical form`,
    );
    assertSettingsDom(dom, tab);
    dom.window.close();
  }
});

test("invalid Settings tab falls back safely to General and canonicalizes the query", () => {
  const { dom, router } = createIntegratedApp("https://melpis.test/app/settings?tab=unknown&source=qa");
  router.start();
  assert.equal(dom.window.location.search, "?source=qa");
  assertSettingsDom(dom, "generale");
  dom.window.close();
});

test("AI Settings remains a separate view and does not expose the general Settings panel", () => {
  const { dom, router } = createIntegratedApp("https://melpis.test/app/ai-settings");
  router.start();
  assert.equal(router.getCurrentView(), "configurazione-ai");
  assert.equal(dom.window.document.querySelector('[data-view-panel="configurazione-ai"]').classList.contains("view-hidden"), false);
  assert.equal(dom.window.document.querySelector('[data-view-panel="impostazioni"]').classList.contains("view-hidden"), true);
  dom.window.close();
});

test("unknown paths canonicalize with replaceState and preserve the initial URL suffix", () => {
  const dom = createDom("https://melpis.test/app/not-real?source=legacy#section");
  let replaces = 0;
  const originalReplace = dom.window.history.replaceState.bind(dom.window.history);
  dom.window.history.replaceState = (...args) => { replaces += 1; return originalReplace(...args); };
  const router = routerApi.createDashboardRouter({ window: dom.window });
  router.start();
  assert.equal(dom.window.location.pathname, "/app/overview");
  assert.equal(dom.window.location.search, "?source=legacy");
  assert.equal(dom.window.location.hash, "#section");
  assert.equal(replaces, 1);
  dom.window.close();
});

test("user navigation pushes once; same-view clicks do not add an entry", () => {
  const dom = createDom();
  const router = routerApi.createDashboardRouter({ window: dom.window });
  router.start();
  router.navigateTo("inbox");
  assert.equal(dom.window.location.pathname, "/app/inbox");
  assert.equal(dom.window.history.length, 2);
  router.navigateTo("inbox");
  assert.equal(dom.window.history.length, 2);
  dom.window.close();
});

test("semantic route anchors preserve their own query and fragment", () => {
  const dom = createDom();
  const router = routerApi.createDashboardRouter({ window: dom.window });
  router.start();
  const anchor = dom.window.document.querySelector('[data-app-view="inbox"]');
  anchor.href = "/app/inbox?source=shortcut#latest";
  anchor.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true, cancelable: true, button: 0 }));
  assert.equal(dom.window.location.pathname, "/app/inbox");
  assert.equal(dom.window.location.search, "?source=shortcut");
  assert.equal(dom.window.location.hash, "#latest");
  dom.window.close();
});

test("same-view anchor suffix canonicalizes in place without adding history", () => {
  const dom = createDom("https://melpis.test/app/inbox");
  const router = routerApi.createDashboardRouter({ window: dom.window });
  router.start();
  const entries = dom.window.history.length;
  const anchor = dom.window.document.querySelector('[data-app-view="inbox"]');
  anchor.href = "/app/inbox?source=shortcut#latest";
  anchor.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true, cancelable: true, button: 0 }));
  assert.equal(dom.window.location.search, "?source=shortcut");
  assert.equal(dom.window.location.hash, "#latest");
  assert.equal(dom.window.history.length, entries);
  dom.window.close();
});

test("same-view anchor without a suffix preserves the current route suffix", () => {
  const dom = createDom("https://melpis.test/app/inbox?filter=open#latest");
  const router = routerApi.createDashboardRouter({ window: dom.window });
  router.start();
  const anchor = dom.window.document.querySelector('[data-app-view="inbox"]');
  anchor.href = "/app/inbox";
  anchor.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true, cancelable: true, button: 0 }));
  assert.equal(dom.window.location.search, "?filter=open");
  assert.equal(dom.window.location.hash, "#latest");
  dom.window.close();
});

test("popstate renders Back and Forward destinations without adding entries", () => {
  const dom = createDom();
  const rendered = [];
  const router = routerApi.createDashboardRouter({ window: dom.window, render: (view) => rendered.push(view) });
  router.start();
  router.navigateTo("inbox");
  router.navigateTo("recensioni");
  router.navigateTo("impostazioni", { search: "?tab=reviews" });
  const historyLength = dom.window.history.length;
  dom.window.history.back();
  return new Promise((resolve) => {
    dom.window.addEventListener("popstate", () => {
      assert.equal(dom.window.location.pathname, "/app/reviews");
      assert.equal(dom.window.history.length, historyLength);
      dom.window.history.back();
      dom.window.addEventListener("popstate", () => {
        assert.equal(dom.window.location.pathname, "/app/inbox");
        dom.window.history.forward();
        dom.window.addEventListener("popstate", () => {
          assert.equal(dom.window.location.pathname, "/app/reviews");
          dom.window.history.forward();
          dom.window.addEventListener("popstate", () => {
            assert.equal(dom.window.location.pathname, "/app/settings");
            assert.equal(dom.window.location.search, "?tab=reviews");
            assert.deepEqual(rendered, [
              "panoramica", "inbox", "recensioni", "impostazioni",
              "recensioni", "inbox", "recensioni", "impostazioni",
            ]);
            dom.window.close();
            resolve();
          }, { once: true });
        }, { once: true });
      }, { once: true });
    }, { once: true });
  });
});

test("router intercepts only ordinary same-origin route clicks", () => {
  const dom = createDom();
  const rendered = [];
  const router = routerApi.createDashboardRouter({ window: dom.window, render: (view) => rendered.push(view) });
  router.start();
  const anchor = dom.window.document.querySelector('[data-app-view="inbox"]');
  const click = (options = {}) => {
    const event = new dom.window.MouseEvent("click", { bubbles: true, cancelable: true, button: 0, ...options });
    anchor.dispatchEvent(event);
    return event.defaultPrevented;
  };
  assert.equal(click(), true);
  for (const options of [
    { ctrlKey: true }, { metaKey: true }, { shiftKey: true }, { altKey: true }, { button: 1 },
  ]) assert.equal(click(options), false);
  anchor.target = "_blank";
  assert.equal(click(), false);
  anchor.removeAttribute("target");
  anchor.download = "dashboard.html";
  assert.equal(click(), false);
  anchor.removeAttribute("download");
  const prevented = new dom.window.MouseEvent("click", { bubbles: true, cancelable: true });
  prevented.preventDefault();
  anchor.dispatchEvent(prevented);
  assert.equal(rendered.filter((view) => view === "inbox").length, 1);
  dom.window.close();
});

test("start is idempotent and semantic links keep canonical paths", () => {
  const dom = createDom();
  const rendered = [];
  const router = routerApi.createDashboardRouter({ window: dom.window, render: (view) => rendered.push(view) });
  router.start();
  router.start();
  dom.window.document.querySelector('[data-app-view="team"]').dispatchEvent(
    new dom.window.MouseEvent("click", { bubbles: true, cancelable: true, button: 0 }),
  );
  assert.deepEqual(rendered, ["panoramica", "team"]);
  dom.window.close();
});

test("dashboard HTML uses canonical semantic links, accessible active state, and absolute app assets", () => {
  const html = fs.readFileSync(path.join(root, "web/index.html"), "utf8");
  const links = [...html.matchAll(/<a\b[^>]*class="nav-item[^>]*data-view="([^"]+)"[^>]*>/g)];
  assert.equal(links.length, 8);
  for (const match of links) {
    const route = primaryRoutes[match[1]];
    assert.ok(route, `unexpected nav view ${match[1]}`);
    assert.match(match[0], new RegExp(`href="${route}"`));
    assert.match(match[0], /data-app-view=/);
  }
  assert.match(html, /<a\b(?=[^>]*id="sidebar-menu-impostazioni")(?=[^>]*class="sidebar-dropdown-item")(?=[^>]*href="\/app\/settings")(?=[^>]*data-app-view="impostazioni")(?=[^>]*role="menuitem")[^>]*>/);
  assert.doesNotMatch(html, /<a\b[^>]*class="nav-item[^>]*data-view="impostazioni"/);
  assert.doesNotMatch(html, /href="\/app\/account"/);
  assert.match(html, /id="priority-inbox-link"[^>]*href="\/app\/inbox"[^>]*data-app-view="inbox"/);
  assert.match(html, /id="activity-view-all-btn"[^>]*href="\/app\/inbox"[^>]*data-app-view="inbox"/);
  assert.ok(html.indexOf('src="/app/dashboard-router.js') < html.indexOf('src="/app/app.js'));
  assert.doesNotMatch(html, /<base\b/i);
  assert.doesNotMatch(html, /(?:src|href)="(?:theme-init\.js|style\.css|vendor\/|dialog-focus\.js|app\.js|mfa\.js)/);
  assert.match(fs.readFileSync(path.join(root, "web/app.js"), "utf8"), /aria-current/);
});

test("changing dashboard language does not rewrite canonical route hrefs", () => {
  const html = fs.readFileSync(path.join(root, "web/index.html"), "utf8");
  for (const href of Object.values(primaryRoutes)) {
    assert.ok(html.includes(`href="${href}"`));
  }
  assert.ok(html.includes('href="/app/settings"'));
  const i18n = fs.readFileSync(path.join(root, "web/i18n-client.js"), "utf8");
  assert.doesNotMatch(i18n, /(?:data-app-view|\.nav-item)[\s\S]{0,100}href\s*=/);
});

test("settings route rendering and category actions are wired to the allowlisted tab helpers", () => {
  const app = fs.readFileSync(path.join(root, "web/app.js"), "utf8");
  const settings = fs.readFileSync(path.join(root, "web/dashboard-settings.js"), "utf8");
  assert.match(app, /settingsTabFromSearch\(window\.location\.search\)/);
  assert.match(app, /settingsSearchForTab\(window\.location\.search, tab\)/);
  assert.match(settings, /button\.addEventListener\("click", \(\) => navigateTab\(button\.dataset\.settingsCatBtn\)\)/);
  assert.match(settings, /function navigateTab\(category\)/);
  assert.match(settings, /settingsSearchForTab\(root\.location\.search, tab\)/);
  assert.match(app, /if \(key === "account"\) \{\s*apriVistaImpostazioni\("piano"\)/);
  assert.match(app, /getElementById\("sidebar-menu-impostazioni"\)\?\.addEventListener\("click", \(event\) => \{\s*if \(!window\.MelpisDashboardRouter\?\.isOrdinaryPrimaryClick\(event\)\) return;\s*chiudiSidebarAccountMenu\(\);\s*\}\);/);
  assert.doesNotMatch(app.match(/getElementById\("sidebar-menu-impostazioni"\)\?\.addEventListener\("click",[\s\S]*?\);/)?.[0] || "", /apriVistaImpostazioni/);
});

test("Calendar and Reviews OAuth callbacks preserve unrelated URL state and route into Settings", () => {
  const integrations = fs.readFileSync(path.join(root, "web/dashboard-integrations.js"), "utf8");
  const app = fs.readFileSync(path.join(root, "web/app.js"), "utf8");
  const callbackBody = (name) => {
    const start = integrations.indexOf(`function ${name}(`);
    assert.notEqual(start, -1, `${name} exists`);
    const end = integrations.indexOf("\nfunction ", start + 1);
    return integrations.slice(start, end === -1 ? undefined : end);
  };
  const deletedParams = (body) => [...new Set(
    [...body.matchAll(/url\.searchParams\.delete\("([^"]+)"\)/g)].map((match) => match[1]),
  )].sort();
  const preservesStateAndHash = /window\.history\.replaceState\(window\.history\.state,\s*"",\s*url\)/;

  const calendar = callbackBody("gestisciCalendarRedirect");
  assert.deepEqual(deletedParams(calendar), ["calendar", "reason"]);
  assert.match(calendar, /apriVistaImpostazioni\("calendar"\)/);
  assert.match(calendar, preservesStateAndHash);

  const reviews = callbackBody("gestisciReviewsRedirect");
  assert.deepEqual(deletedParams(reviews), ["reason", "reviews_google"]);
  assert.match(reviews, /apriVistaImpostazioni\("reviews"\)/);
  assert.match(reviews, preservesStateAndHash);

  const settingsEntryStart = app.indexOf("function apriVistaImpostazioni(");
  assert.notEqual(settingsEntryStart, -1, "app shell exposes Settings routing");
  const settingsEntryEnd = app.indexOf("\nfunction ", settingsEntryStart + 1);
  const settingsEntry = app.slice(settingsEntryStart, settingsEntryEnd);
  assert.match(settingsEntry, /dashboardSettingsModule\?\.openCategory\(cat\)/);
  assert.match(integrations, /apriVistaImpostazioni\("calendar"\)/);
});
