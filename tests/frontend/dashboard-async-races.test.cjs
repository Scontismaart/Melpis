const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { spawnSync } = require("node:child_process");
const { JSDOM } = require("jsdom");

const root = path.resolve(__dirname, "../..");
const app = fs.readFileSync(path.join(root, "web/app.js"), "utf8").replace(/\r\n/g, "\n");
function source(name) {
  const start = app.search(new RegExp(`(?:async )?function ${name}\\(`));
  assert.notEqual(start, -1, `missing ${name}`);
  const end = app.indexOf("\n}", start);
  assert.notEqual(end, -1, `missing ${name} end`);
  return app.slice(start, end + 2);
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function response(data) { return { ok: true, json: () => Promise.resolve(data) }; }
function harness(view) {
  const dom = new JSDOM(fs.readFileSync(path.join(root, "web/index.html"), "utf8"), {
    url: "https://melpis.test/app/overview", pretendToBeVisual: true,
    runScripts: "outside-only",
  });
  const document = dom.window.document;
  dom.window.eval(fs.readFileSync(path.join(root, "web/dashboard-shared.js"), "utf8"));
  // Test-only observation seam: execute the production factory, expose no
  // additional API in the shipped script and copy no implementations.
  dom.window.eval(fs.readFileSync(path.join(root, "web/dashboard-bookings.js"), "utf8")
    .replace("let bookingCalendar = null;", "let bookingCalendar = deps.testCalendar;")
    .replace("onEnter, onExit, invalidate, load,", "onEnter, onExit, invalidate, load, apriFormPrenotazione, apriDettaglioPrenotazione, eseguiAzionePrenotazione, currentBooking: () => prenotazioneCorrente,"));
  const requests = [], errors = [], toasts = [], intervals = new Map(), gotoDates = [];
  let intervalId = 0;
  let date = "2026-10-02";
  const bookingCalendar = {
    getDate: () => new Date(`${date}T12:00:00`), removeAllEvents() {}, addEvent() {},
    gotoDate(value) { gotoDates.push(value); date = value; },
    view: { type: "timeGridDay" }, setOption() {}, updateSize() {}, render() {}, destroy() {}, unselect() {},
  };
  dom.window.FullCalendar = { Calendar: function () { return bookingCalendar; } };
  const context = vm.createContext({
    document, window: dom.window, console: { error: (...args) => errors.push(args) },
    API_BASE: "", bookingCalendar, Date, Map, Set,
    _toDateKey: dom.window.MelpisDashboardShared.toDateKey,
    crypto: { randomUUID: () => "test-idempotency-key" },
    apiFetch(url, options = {}) { const pending = deferred(); requests.push({ url, options, ...pending }); return pending.promise; },
    _skeletonList: () => "<div class='skeleton'>loading</div>",
    _sanitize: (value) => String(value), _tDash: (_key, fallback, vars = {}) => fallback.replace(/{{(\w+)}}/g, (_, key) => vars[key]),
    t: (key) => key, localeCorrente: () => "it-IT",
    _errorState(message) { const el = document.createElement("p"); el.className = "error-state"; el.textContent = message; return el; },
    _emptyState(_icon, title) { const el = document.createElement("p"); el.textContent = title; return el; },
    ICONS: { chat: "", check: "" },
    calcolaTrendKpi: () => ({}), applicaBadgeTrend() {}, generaSparklineSvg: () => "",
    caricaStatoOnboarding: () => Promise.resolve({ isFullyConfigured: true }),
    setInterval: (fn) => { const id = ++intervalId; intervals.set(id, fn); return id; }, clearInterval: (id) => intervals.delete(id),
    toast: (...args) => toasts.push(args),
    bookingCalendarEl: document.getElementById("booking-calendar"),
    bookingForm: document.getElementById("booking-form"), bookingStatusText: document.getElementById("booking-status-text"),
    bookingModal: document.getElementById("booking-modal"),
    bookingDetail: Object.fromEntries(["title", "date", "time", "seats", "status", "phone", "origin", "note"].map(key => [key,
      document.getElementById(key === "title" ? "booking-modal-title" : `booking-detail-${key}`),
    ])),
    sessione: { user_id: "user-1", organization_id: "org-1", ruolo: "owner" }, STATI_FINALI_PRENOTAZIONE: ["annullata", "rifiutata", "completata", "no_show"],
    confermaDestructiva: () => Promise.resolve(true),
    bookingCount: document.getElementById("booking-count"), bookingPendingValue: document.getElementById("booking-pending-value"),
    availabilityList: document.getElementById("availability-list"), availabilityDate: document.getElementById("availability-date"),
    priorityList: document.getElementById("priority-list"), ticketList: document.getElementById("ticket-list"),
    statTotale: document.getElementById("stat-totale"), statAi: document.getElementById("stat-ai"), statUmano: document.getElementById("stat-umano"),
    statoNormalizzatoPrenotazione: (p) => p.stato || "confermata",
    leggiStatoNotifiche: () => ({}), aggiornaBadgeNotifiche() {}, aggiornaCampana() {},
    intervalloSlotPrenotazioni: () => ({ min: "06:00:00", max: "24:00:00" }),
    verificaPrenotazioneAggiornata() {}, formattaUnitaVerticale: (value) => value, colorePrenotazione: () => "green",
    aggiornaRiepilogoPrenotazioni(_date, slots) { document.getElementById("booking-summary").textContent = slots.map(s => s.coperti_liberi).join(","); },
    renderTabellaPrenotazioniGiorno(day) { document.getElementById("booking-table-day-title").textContent = day; },
    oggiIso: () => date,
    ...Object.fromEntries(["Section", "Date", "Totale", "Ai", "Umano", "Analisi", "Suggestions", "SuggestionsList", "Timestamp", "Refresh", "EmptyHint"].map(suffix => [
      `report${suffix}`, document.getElementById(`report-${suffix.replace(/[A-Z]/g, (char, i) => `${i ? "-" : ""}${char.toLowerCase()}`)}`),
    ])),
    reportRefreshHtml: document.getElementById("report-refresh").innerHTML,
  });
  vm.runInContext(fs.readFileSync(path.join(root, "web/dashboard-overview.js"), "utf8"), context);
  const form = document.getElementById("booking-form");
  const addListener = form.addEventListener.bind(form);
  let submission;
  form.addEventListener = (name, listener) => addListener(name, event => { submission = listener(event); });
  vm.runInContext(`
    let activeDashboardView = ${JSON.stringify(view)};
    let dashboardViewTransition = 1;
    let overviewSummaryRequest = 0, overviewPriorityRequest = 0;
    let bookingListRequest = 0, bookingAvailabilityRequest = 0;
    let bookingEditingId = null, bookingFormTransition = 0;
    let prenotazioneCorrente = null, bookingDetailTransition = 0;
    let overviewReportRequest = 0;
    let isPanoramicaPollingActive = false, panoramicaPollingTimer = null;
    let bookingRecords = [], bookingAvailability = new Map(), bookingSnapshot = new Map();
    let prenotazioniInAttesaCount = 0, bookingPendingOnly = false;
    const bookingsModule = window.MelpisDashboardBookings.create({
      testCalendar: bookingCalendar,
      API_BASE, apiFetch: (...args) => apiFetch(...args),
      _escapeHtml: window.MelpisDashboardShared.escapeHtml, _toDateKey, _tDash, localeCorrente, toast,
      confermaDestructiva: (...args) => confermaDestructiva(...args),
      getContext: () => ({ userId: sessione?.user_id, sessionOrganizationId: sessione?.organization_id, selectedOrganizationId: sessione?.organization_id, view: activeDashboardView, transition: dashboardViewTransition }),
      getSession: () => sessione, getVertical: () => "ristorante", statoNormalizzatoPrenotazione,
      exportCsv() {}, updatePending() {},
    });
    if (activeDashboardView === "prenotazioni") bookingsModule.onEnter();
    globalThis.navigate = (view) => {
      if (activeDashboardView === "prenotazioni") bookingsModule.onExit();
      activeDashboardView = view; dashboardViewTransition++;
      if (view === "prenotazioni") bookingsModule.onEnter();
    };
    for (const name of ["aggiornaPrenotazioni", "aggiornaSemaforo", "apriFormPrenotazione", "apriDettaglioPrenotazione", "eseguiAzionePrenotazione", "currentBooking"]) globalThis[name] = bookingsModule[name];
    globalThis.apriBookingModal = bookingsModule.apriBookingModal;
    ${source("aggiornaReport")}
    const overviewModule = window.MelpisDashboardOverview.create({
      API_BASE, apiFetch, _sanitize, toast, _tDash, t, localeCorrente, _toDateKey,
      _emptyState, _errorState, _skeletonList, ICONS,
      getContext: () => ({ userId: "user-1", sessionOrganizationId: "org-1", selectedOrganizationId: "org-1", view: activeDashboardView, transition: dashboardViewTransition, epoch: 0 }),
      apriView: (view) => navigate(view), apriBookingModal, getReviewsApi: () => null,
      loadOnboarding: () => caricaStatoOnboarding(),
    });
    globalThis.aggiornaRiepilogo = overviewModule.aggiornaRiepilogo;
    globalThis.aggiornaPrioritari = overviewModule.aggiornaPrioritari;
    globalThis.avviaPanoramicaPolling = overviewModule.avviaPanoramicaPolling;
    globalThis.fermaPanoramicaPolling = overviewModule.fermaPanoramicaPolling;
    overviewModule.onEnter();
  `, context);
  return { dom, document, context, requests, errors, toasts, intervals, gotoDates,
    setDate(value) { date = value; }, getDate() { return date; },
    submitBooking() { form.dispatchEvent(new dom.window.Event("submit", { cancelable: true })); return submission; },
  };
}

const event = (text) => ({ testo_originale: text, timestamp: new Date().toISOString(), tipo_evento: "messaggio", priorita: "alta", gestito_da_ai: true });
const ticket = (id, status = "RESOLVED") => ({ id, phone_number: id, ticket_status: status, canale: "whatsapp" });
const slot = (free) => ({ ora: "20:00", stato: "verde", coperti_liberi: free, coperti_massimi: 10 });

for (const [loader, listId] of [["aggiornaRiepilogo", "ticket-list"], ["aggiornaPrioritari", "priority-list"]]) {
  test(`${loader}: newest request wins when older JSON finishes last`, async (t) => {
    const h = harness("panoramica"); t.after(() => h.dom.window.close());
    const jsonA = deferred();
    const a = h.context[loader](); h.requests[0].resolve({ ok: true, json: () => jsonA.promise });
    await Promise.resolve();
    const b = h.context[loader](); h.requests[1].resolve(response([event("fresh B")])); await b;
    jsonA.resolve([event("obsolete A")]); await a;
    assert.match(h.document.getElementById(listId).textContent, /fresh B/);
    assert.doesNotMatch(h.document.getElementById(listId).textContent, /obsolete A/);
  });
  test(`${loader}: leave and return rejects old response and stale failure`, async (t) => {
    const h = harness("panoramica"); t.after(() => h.dom.window.close());
    const a = h.context[loader]();
    h.context.navigate("inbox"); h.context.navigate("panoramica");
    const b = h.context[loader](); h.requests[1].resolve(response([event("current visit")])); await b;
    h.requests[0].reject(new Error("stale error")); await a;
    assert.match(h.document.getElementById(listId).textContent, /current visit/);
    assert.equal(h.document.querySelectorAll(".error-state").length, 0);
    assert.equal(h.errors.length, 0);
  });
}

test("priority onboarding await cannot append an obsolete empty state", async (t) => {
  const h = harness("panoramica"); t.after(() => h.dom.window.close());
  const onboarding = deferred(), onboardingStarted = deferred();
  h.context.caricaStatoOnboarding = () => { onboardingStarted.resolve(); return onboarding.promise; };
  const a = h.context.aggiornaPrioritari(); h.requests[0].resolve(response([])); await onboardingStarted.promise;
  const b = h.context.aggiornaPrioritari(); h.requests[1].resolve(response([event("new priority")])); await b;
  onboarding.resolve({ isFullyConfigured: true }); await a;
  assert.equal(h.document.getElementById("priority-list").children.length, 1);
  assert.match(h.document.getElementById("priority-list").textContent, /new priority/);
});

test("Overview repeated polling activation keeps one interval and stop clears it", (t) => {
  const h = harness("panoramica"); t.after(() => h.dom.window.close());
  h.context.fermaPanoramicaPolling();
  for (let visit = 0; visit < 3; visit++) {
    h.context.avviaPanoramicaPolling(); h.context.avviaPanoramicaPolling();
    assert.equal(h.intervals.size, 1);
    h.context.fermaPanoramicaPolling(); assert.equal(h.intervals.size, 0);
  }
});



test("Bookings overlapping refreshes keep newest records after delayed JSON", async (t) => {
  const h = harness("prenotazioni"); t.after(() => h.dom.window.close()); const oldJson = deferred();
  const a = h.context.aggiornaPrenotazioni(); h.requests[0].resolve({ ok: true, json: () => oldJson.promise }); await Promise.resolve();
  const b = h.context.aggiornaPrenotazioni(); h.requests[1].resolve(response([{}, {}])); await b;
  oldJson.resolve([{}]); await a;
  assert.equal(h.document.getElementById("booking-count").textContent, "2 prenotazioni");
});

test("Bookings availability selected date B survives late A including delayed JSON", async (t) => {
  const h = harness("prenotazioni"); t.after(() => h.dom.window.close()); const oldJson = deferred();
  const a = h.context.aggiornaSemaforo(); h.requests[0].resolve({ ok: true, json: () => oldJson.promise }); await Promise.resolve();
  h.setDate("2026-10-03"); const b = h.context.aggiornaSemaforo(); h.requests[1].resolve(response([slot(8)])); await b;
  oldJson.resolve([slot(1)]); await a;
  assert.match(h.document.getElementById("booking-summary").textContent, /8 posti liberi/);
  assert.match(h.document.getElementById("booking-table-day-title").textContent, /3 ottobre/);
});

test("Bookings explicit refresh for old mutation date cannot override current selection", async (t) => {
  const h = harness("prenotazioni"); t.after(() => h.dom.window.close());
  h.setDate("2026-10-03");
  const b = h.context.aggiornaSemaforo(); h.requests[0].resolve(response([slot(8)])); await b;
  const before = h.document.getElementById("availability-date").textContent;
  const a = h.context.aggiornaSemaforo("2026-10-02");
  if (h.requests[1]) h.requests[1].resolve(response([slot(1)])); await a;
  assert.equal(h.document.getElementById("availability-date").textContent, before);
  assert.match(h.document.getElementById("booking-summary").textContent, /8 posti liberi/);
});

test("Overview report newest JSON wins and old forced error cannot toast or reset newer button", async (t) => {
  const h = harness("panoramica"); t.after(() => h.dom.window.close());
  const a = h.context.aggiornaReport(true);
  const b = h.context.aggiornaReport(true);
  h.requests[0].reject(new Error("stale report failure")); await a;
  assert.equal(h.toasts.length, 0);
  assert.equal(h.document.getElementById("report-refresh").disabled, true);
  h.requests[1].resolve(response({ statistiche: { totale_messaggi: 9 }, analisi_testuale: "current report" })); await b;
  assert.equal(h.document.getElementById("report-analisi").textContent, "current report");
  assert.equal(h.document.getElementById("report-refresh").disabled, false);
  assert.notEqual(h.document.getElementById("report-refresh").textContent, "Generazione in corso...");
});

test("Report JSON completing after leave cannot commit and next visit resets loading button", async (t) => {
  const h = harness("panoramica"); t.after(() => h.dom.window.close()); const oldJson = deferred();
  const a = h.context.aggiornaReport(true); h.requests[0].resolve({ ok: true, json: () => oldJson.promise }); await Promise.resolve();
  h.context.navigate("inbox");
  oldJson.resolve({ statistiche: {}, analisi_testuale: "obsolete report" }); await a;
  assert.notEqual(h.document.getElementById("report-analisi").textContent, "obsolete report");
  assert.equal(h.toasts.length, 0);
  h.context.navigate("panoramica");
  const b = h.context.aggiornaReport(); h.requests[1].resolve(response({ statistiche: {}, analisi_testuale: "current visit" })); await b;
  assert.equal(h.document.getElementById("report-refresh").disabled, false);
  assert.notEqual(h.document.getElementById("report-refresh").textContent, "Generazione in corso...");
});



test("Bookings same-date availability overlapping refreshes preserve latest response", async (t) => {
  const h = harness("prenotazioni"); t.after(() => h.dom.window.close());
  const a = h.context.aggiornaSemaforo(); const b = h.context.aggiornaSemaforo();
  h.requests[1].resolve(response([slot(8)])); await b;
  h.requests[0].resolve(response([slot(1)])); await a;
  assert.match(h.document.getElementById("booking-summary").textContent, /8 posti liberi/);
});

for (const loader of ["aggiornaPrenotazioni", "aggiornaSemaforo"]) {
  test(`${loader}: stale rejection after leave/re-enter is silent`, async (t) => {
    const h = harness("prenotazioni"); t.after(() => h.dom.window.close());
    const a = h.context[loader](); h.context.navigate("inbox"); h.context.navigate("prenotazioni");
    const b = h.context[loader](); h.requests[1].resolve(response(loader === "aggiornaSemaforo" ? [slot(8)] : [{}, {}])); await b;
    h.requests[0].reject(new Error("old visit")); await a;
    assert.equal(h.errors.length, 0);
  });
}



for (const scenario of ["date B", "leave and return", "new form", "stale error"]) {
  test(`Booking save completion ignores obsolete UI context after ${scenario}`, async (t) => {
    const h = harness("prenotazioni"); t.after(() => h.dom.window.close());
    h.context.apriFormPrenotazione({ id: "A", data: "2026-10-02", nome_cliente: "Customer A", coperti: 2 });
    const saving = h.submitBooking();
    if (scenario === "date B") h.setDate("2026-10-03");
    else if (scenario === "leave and return") { h.context.navigate("inbox"); h.context.navigate("prenotazioni"); h.context.apriFormPrenotazione({ id: "A", data: "2026-10-02", nome_cliente: "Customer A", coperti: 2 }); }
    else h.context.apriFormPrenotazione({ id: "B", data: "2026-10-03", nome_cliente: "Customer B", coperti: 3 });
    const name = h.document.getElementById("booking-name"), modal = h.document.getElementById("booking-create-modal");
    const expectedName = name.value, expectedDate = h.getDate();
    h.document.getElementById("booking-status-text").textContent = "current context";
    const unwantedFetches = [];
    h.context.apiFetch = async url => { unwantedFetches.push(url); return response([]); };
    if (scenario === "stale error") h.requests[0].reject(new Error("late save failure"));
    else h.requests[0].resolve(response({}));
    await saving;
    assert.equal(h.getDate(), expectedDate);
    assert.equal(name.value, expectedName);
    assert.equal(modal.hidden, false);
    assert.equal(h.document.getElementById("booking-status-text").textContent, "current context");
    assert.equal(h.gotoDates.length, 0);
    assert.equal(unwantedFetches.length, 0);
  });
}

test("Booking save cannot render date A after selected date changes during refresh", async (t) => {
  const h = harness("prenotazioni"); t.after(() => h.dom.window.close());
  h.context.apriFormPrenotazione({ data: "2026-10-02", nome_cliente: "Customer A", coperti: 2 });
  const refreshing = deferred(), refreshStarted = deferred();
  h.context.apiFetch = (url, options = {}) => {
    if (url === "/api/bookings" && !options.method) { refreshStarted.resolve(); return refreshing.promise; }
    const request = deferred(); h.requests.push({ url, ...request }); return request.promise;
  };
  const saving = h.submitBooking(); h.requests[0].resolve(response({})); await refreshStarted.promise;
  h.setDate("2026-10-03");
  h.document.getElementById("booking-table-day-title").textContent = "2026-10-03";
  refreshing.resolve(response([])); await saving;
  assert.match(h.document.getElementById("booking-table-day-title").textContent, /3 ottobre/);
  assert.doesNotMatch(h.document.getElementById("booking-table-day-title").textContent, /2 ottobre/);
});

test("Booking save in unchanged context closes its own form and refreshes the saved day", async (t) => {
  const h = harness("prenotazioni"); t.after(() => h.dom.window.close());
  h.context.apriFormPrenotazione({ id: "A", data: "2026-10-03", nome_cliente: "Customer A", coperti: 2 });
  const saving = h.submitBooking();
  assert.equal(h.requests[0].url, "/api/bookings/A");
  assert.equal(h.requests[0].options.method, "PUT");
  assert.equal(JSON.parse(h.requests[0].options.body).data, "2026-10-03");
  const refreshes = [];
  h.context.apiFetch = async url => { refreshes.push(url); return response([]); };
  h.requests[0].resolve(response({})); await saving;
  assert.equal(h.document.getElementById("booking-create-modal").hidden, true);
  assert.equal(h.document.getElementById("booking-name").value, "");
  assert.equal(h.document.getElementById("booking-status-text").textContent, "Modifica salvata.");
  assert.equal(h.getDate(), "2026-10-03");
  assert.deepEqual(refreshes, ["/api/bookings", "/api/bookings/semaforo?data=2026-10-03"]);
  assert.match(h.document.getElementById("booking-table-day-title").textContent, /3 ottobre/);
});

test("Booking availability explicit date uses selected local calendar day", async (t) => {
  if (!process.env.MELPIS_TZ_CHILD) {
    const result = spawnSync(process.execPath, ["--test", "--test-name-pattern=Booking availability explicit date uses selected local calendar day", __filename], {
      env: { ...process.env, NODE_TEST_CONTEXT: undefined, TZ: "America/New_York", MELPIS_TZ_CHILD: "1" }, encoding: "utf8", timeout: 10000,
    });
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert.match(result.stdout, /tests 1/, "the child executed the timezone regression test");
    return;
  }
  const h = harness("prenotazioni"); t.after(() => h.dom.window.close());
  const availability = h.context.aggiornaSemaforo("2026-10-02");
  assert.equal(h.requests.length, 1, "explicit local date must match the calendar day in negative UTC offsets");
  assert.match(h.requests[0].url, /data=2026-10-02$/);
  h.requests[0].resolve(response([slot(8)])); await availability;
  assert.match(h.document.getElementById("booking-summary").textContent, /8 posti liberi/);
});

for (const scenario of ["new detail", "leave and return", "stale error", "delayed JSON"]) {
  test(`Booking action A ignores stale completion after ${scenario}`, async (t) => {
    const h = harness("prenotazioni"); t.after(() => h.dom.window.close());
    h.context.apriDettaglioPrenotazione({ id: "A", data: "2026-10-02", nome_cliente: "Customer A", stato: "in_attesa" });
    const pending = h.context.eseguiAzionePrenotazione("confirm");
    const oldJson = deferred(), jsonStarted = deferred();
    if (scenario === "delayed JSON") {
      h.requests[0].resolve({ ok: true, json: () => { jsonStarted.resolve(); return oldJson.promise; } });
      await jsonStarted.promise;
    }
    if (scenario === "leave and return") { h.context.navigate("inbox"); h.context.navigate("prenotazioni"); h.context.apriDettaglioPrenotazione({ id: "A", data: "2026-10-02", nome_cliente: "Customer A", stato: "in_attesa" }); }
    else h.context.apriDettaglioPrenotazione({ id: "B", data: "2026-10-03", nome_cliente: "Customer B", stato: "in_attesa" });
    const button = h.document.getElementById("booking-confirm-btn");
    const unwanted = [];
    h.context.apiFetch = async url => { unwanted.push(url); return response([]); };
    // B has its own pending interaction; A must not enable its control.
    button.disabled = true;
    if (scenario === "stale error") h.requests[0].reject(new Error("late A error"));
    else if (scenario === "delayed JSON") oldJson.resolve({ id: "A", stato: "confermata" });
    else h.requests[0].resolve(response({ id: "A", stato: "confermata" }));
    await pending;
    assert.equal(h.context.currentBooking().id, scenario === "leave and return" ? "A" : "B");
    assert.equal(h.context.currentBooking().stato, "in_attesa");
    assert.equal(h.document.getElementById("booking-modal").hidden, false);
    assert.equal(h.document.getElementById("booking-modal-title").textContent, scenario === "leave and return" ? "Customer A" : "Customer B");
    assert.equal(button.disabled, true);
    assert.equal(h.toasts.length, 0); assert.equal(unwanted.length, 0);
  });
}

test("New booking detail opens with its own enabled actions while older action is pending", async (t) => {
  const h = harness("prenotazioni"); t.after(() => h.dom.window.close());
  h.context.apriDettaglioPrenotazione({ id: "A", data: "2026-10-02", stato: "in_attesa" });
  const pending = h.context.eseguiAzionePrenotazione("confirm");
  h.context.apriDettaglioPrenotazione({ id: "B", data: "2026-10-03", stato: "in_attesa" });
  assert.equal(h.document.getElementById("booking-confirm-btn").disabled, false);
  h.requests[0].resolve(response({ id: "A", stato: "confermata" })); await pending;
});

test("Booking action in current context closes its own detail and refreshes bookings", async (t) => {
  const h = harness("prenotazioni"); t.after(() => h.dom.window.close());
  h.context.apriDettaglioPrenotazione({ id: "A", data: "2026-10-02", stato: "in_attesa" });
  const pending = h.context.eseguiAzionePrenotazione("confirm");
  assert.equal(h.requests[0].url, "/api/bookings/A/confirm"); assert.equal(h.requests[0].options.method, "POST");
  const refreshes = [];
  h.context.apiFetch = async url => { refreshes.push(url); return response([]); };
  h.requests[0].resolve(response({ id: "A", stato: "confermata" })); await pending;
  assert.equal(h.context.currentBooking().stato, "confermata");
  assert.equal(h.document.getElementById("booking-modal").hidden, true);
  assert.equal(h.document.getElementById("booking-confirm-btn").disabled, false);
  assert.equal(h.toasts.length, 1); assert.match(h.toasts[0][0], /confermata/);
  assert.deepEqual(refreshes, ["/api/bookings", "/api/bookings/semaforo?data=2026-10-02"]);
});
