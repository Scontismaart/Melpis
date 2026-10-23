const { test } = require("node:test");
const assert = require("node:assert/strict");
const { createDashboardHarness } = require("./helpers/dashboard-harness.cjs");

const BOOKING_DATE = "2026-10-08";
const pending = (overrides = {}) => ({
  id: "qa-pending-1", nome_cliente: "Cliente QA", telefono: "+39000123",
  data: BOOKING_DATE, ora: "20:00", coperti: 2, note: "Nota QA",
  stato: "in_attesa", origine: "WhatsApp", ...overrides,
});
const confirmed = (overrides = {}) => pending({ id: "qa-confirmed-1", nome_cliente: "Confermato QA", stato: "confermata", ...overrides });
const json = (body, status = 200) => ({ status, ok: status >= 200 && status < 300,
  headers: { get: () => null }, text: async () => JSON.stringify(body),
  json: async () => body, clone() { return json(body, status); } });

function fixture({ bookings = [pending(), confirmed()], settings = { capienze_orarie: { "20:00": 12 }, fasce_orarie: ["20:00"] }, slots = [{ ora: "20:00", coperti_liberi: 8, coperti_massimi: 12, stato: "verde" }], role = "owner", overrides = {} } = {}) {
  const h = createDashboardHarness({
    url: "https://melpis.test/app/overview",
    overrides: {
      "/api/auth/me": { user_id: "user-1", organization_id: "org-1", email: "qa@example.test", ruolo: role },
      "/api/bookings": () => json(bookings),
      "/api/bookings/settings": () => json(settings),
      "/api/bookings/semaforo": (request) => request.search.includes(`data=${BOOKING_DATE}`) ? json(slots) : json([]),
      ...overrides,
    },
  });
  return h;
}

async function openBookings(h) {
  await h.ready();
  await h.navigate("prenotazioni");
  await h.waitFor(() => h.document.querySelector("#booking-table-body tr"), "bookings view did not render");
  await h.settle();
}

function requests(h, method, pathname) {
  return h.requests.filter((request) => request.method === method && request.pathname === pathname);
}

function fillForm(h, values = {}) {
  const data = { name: "Nuovo QA", phone: "+39000999", date: BOOKING_DATE, time: "20:15", seats: "3", note: "Nota sintetica", ...values };
  h.document.getElementById("booking-name").value = data.name;
  h.document.getElementById("booking-phone").value = data.phone;
  h.document.getElementById("booking-date").value = data.date;
  h.document.getElementById("booking-time").value = data.time;
  h.document.getElementById("booking-seats").value = data.seats;
  h.document.getElementById("booking-note").value = data.note;
}

async function settleRequests(h) {
  await Promise.all(h.requests.map((request) => request.promise));
  await h.settle();
}

test("Bookings module bootstraps through the real dashboard and requests the three owned read routes", async (t) => {
  const h = fixture(); t.after(() => h.dispose());
  await openBookings(h);
  assert.ok(h.bookings);
  assert.ok(requests(h, "GET", "/api/bookings").length >= 1);
  assert.ok(requests(h, "GET", "/api/bookings/settings").length >= 1);
  assert.ok(requests(h, "GET", "/api/bookings/semaforo").length >= 1);
  assert.equal(h.unexpectedRequests.length, 0);
});

test("Bookings renders synthetic rows, pending count, summary and calendar-backed table", async (t) => {
  const h = fixture(); t.after(() => h.dispose()); await openBookings(h);
  assert.equal(h.document.querySelectorAll("#booking-table-body tr[data-booking-id]").length, 2);
  assert.match(h.document.getElementById("booking-count").textContent, /2/);
  assert.match(h.document.getElementById("booking-summary").textContent, /2 prenotazioni/);
  assert.match(h.document.getElementById("booking-summary").textContent, /4 coperti/);
  assert.match(h.document.getElementById("booking-summary").textContent, /8 posti liberi/);
});

test("Calendar starts in day view and day, week and month controls update their pressed state", async (t) => {
  const h = fixture(); t.after(() => h.dispose()); await openBookings(h);
  const button = (view) => h.document.querySelector(`[data-booking-calendar-view="${view}"]`);
  assert.equal(button("timeGridDay").getAttribute("aria-pressed"), "true");
  for (const view of ["timeGridWeek", "dayGridMonth", "timeGridDay"]) {
    button(view).click(); await h.settle();
    assert.equal(button(view).getAttribute("aria-pressed"), "true");
    assert.equal([...h.document.querySelectorAll("[data-booking-calendar-view]")].filter((item) => item.getAttribute("aria-pressed") === "true").length, 1);
  }
});

test("Calendar previous, next and today commands keep the date picker synchronized", async (t) => {
  const h = fixture(); t.after(() => h.dispose()); await openBookings(h);
  const picker = h.document.getElementById("booking-date-picker");
  h.document.querySelector('[data-booking-calendar-command="prev"]').click(); await h.settle();
  assert.equal(picker.value, "2026-10-07");
  h.document.querySelector('[data-booking-calendar-command="next"]').click(); await h.settle();
  assert.equal(picker.value, BOOKING_DATE);
  h.calendars[0].today(); await h.settle();
  assert.equal(picker.value, BOOKING_DATE);
});

test("Calendar date selection reloads semaforo with the local selected date", async (t) => {
  const h = fixture(); t.after(() => h.dispose()); await openBookings(h);
  const picker = h.document.getElementById("booking-date-picker");
  picker.value = "2026-10-09"; picker.dispatchEvent(new h.window.Event("change", { bubbles: true }));
  await h.settle();
  assert.ok(requests(h, "GET", "/api/bookings/semaforo").some((request) => request.search.includes("data=2026-10-09")));
  assert.equal(picker.value, "2026-10-09");
});

test("Empty bookings show one empty row and an empty-day prompt", async (t) => {
  const h = fixture({ bookings: [], slots: [] }); t.after(() => h.dispose()); await openBookings(h);
  assert.equal(h.document.querySelectorAll("#booking-table-body tr").length, 1);
  assert.match(h.document.querySelector("#booking-table-body").textContent, /Nessuna prenotazione/);
  assert.equal(h.document.getElementById("booking-day-empty").hidden, false);
});

test("Confirmed Google bookings show safe external sync status", async (t) => {
  const h = fixture({ bookings: [
    confirmed({ id: "google-pending", external_sync_provider: "google", external_sync_status: "pending_retry", external_sync_error_code: "retry_pending" }),
    confirmed({ id: "google-synced", external_sync_provider: "google", external_sync_status: "succeeded" }),
    confirmed({ id: "google-failed", external_sync_provider: "google", external_sync_status: "failed", external_sync_error_code: "sync_failed" }),
    confirmed({ id: "google-blocked", external_sync_provider: "google", external_sync_status: "blocked" }),
    confirmed({ id: "google-uncertain", external_sync_provider: "google", external_sync_status: "uncertain" }),
    confirmed({ id: "unsafe-code", external_sync_provider: "google", external_sync_status: "failed", external_sync_error_code: "token=secret-value" }),
    confirmed({ id: "other-provider", external_sync_provider: "apaleo", external_sync_status: "failed" }),
  ] });
  t.after(() => h.dispose()); await openBookings(h);
  const pendingRow = h.document.querySelector('[data-booking-id="google-pending"]');
  assert.match(pendingRow.textContent, /Confermata in Melpis, sincronizzazione Google in attesa/);
  assert.match(pendingRow.textContent, /retry_pending/);
  assert.match(h.document.querySelector('[data-booking-id="google-synced"]').textContent, /Sincronizzata con Google/);
  assert.match(h.document.querySelector('[data-booking-id="google-failed"]').textContent, /Confermata in Melpis, sincronizzazione Google non riuscita/);
  assert.match(h.document.querySelector('[data-booking-id="google-blocked"]').textContent, /Confermata in Melpis, sincronizzazione Google bloccata/);
  assert.match(h.document.querySelector('[data-booking-id="google-uncertain"]').textContent, /Confermata in Melpis, esito Google incerto/);
  assert.doesNotMatch(h.document.querySelector('[data-booking-id="unsafe-code"]').textContent, /token=secret-value/);
  assert.doesNotMatch(h.document.querySelector('[data-booking-id="other-provider"]').textContent, /sincronizzazione Google|Sincronizzata con Google/);
});

test("Authoritative Calendar verification stays explicitly pending and never claims confirmed or synced", async (t) => {
  const h = fixture({ bookings: [
    pending({ id: "calendar-confirm-pending", calendar_confirm_pending: true, external_sync_provider: "google", external_sync_status: "processing" }),
    pending({ id: "calendar-confirm-blocked", calendar_confirm_pending: true, external_sync_provider: "google", external_sync_status: "blocked" }),
    pending({ id: "calendar-confirm-uncertain", calendar_confirm_pending: true, external_sync_provider: "google", external_sync_status: "uncertain" }),
  ] });
  t.after(() => h.dispose()); await openBookings(h);
  const pendingRow = h.document.querySelector('[data-booking-id="calendar-confirm-pending"]');
  assert.match(pendingRow.querySelector(".booking-badge").textContent, /in_attesa/);
  assert.match(pendingRow.textContent, /Prenotazione non confermata: in attesa di nuova verifica Google/);
  assert.doesNotMatch(pendingRow.textContent, /Sincronizzata con Google|Confermata in Melpis/);
  assert.match(h.document.querySelector('[data-booking-id="calendar-confirm-blocked"]').textContent, /Prenotazione non confermata: verifica Google bloccata/);
  assert.match(h.document.querySelector('[data-booking-id="calendar-confirm-uncertain"]').textContent, /esito Google incerto, in attesa di nuova verifica/);
});

test("Failed list, availability and settings responses do not invent success content", async (t) => {
  const h = fixture({ overrides: {
    "/api/bookings": () => json({ detail: "Errore sintetico" }, 500),
    "/api/bookings/settings": () => json({ detail: "Errore sintetico" }, 500),
    "/api/bookings/semaforo": () => json({ detail: "Errore sintetico" }, 504),
  } });
  t.after(() => h.dispose()); await openBookings(h);
  assert.equal(h.document.querySelectorAll("#booking-table-body tr[data-booking-id]").length, 0);
  assert.doesNotMatch(h.document.getElementById("booking-status-text").textContent, /aggiunta|salvata/i);
  assert.doesNotMatch(h.document.getElementById("capacity-status").textContent, /aggiornata/i);
});

test("Create form keeps required HTML validation in front of the POST", async (t) => {
  const h = fixture(); t.after(() => h.dispose()); await openBookings(h);
  h.document.getElementById("booking-new-trigger").click();
  const form = h.document.getElementById("booking-form");
  assert.equal(form.checkValidity(), false);
  form.requestSubmit(); await h.settle();
  assert.equal(requests(h, "POST", "/api/bookings").length, 0);
});

test("Create submits the exact form payload and adds no idempotency header", async (t) => {
  let submitted;
  const h = fixture({ overrides: { "POST /api/bookings": (request) => { submitted = request; return json(pending({ id: "created-qa" }), 201); } } });
  t.after(() => h.dispose()); await openBookings(h);
  h.document.getElementById("booking-new-trigger").click(); fillForm(h);
  h.document.getElementById("booking-form").dispatchEvent(new h.window.Event("submit", { bubbles: true, cancelable: true }));
  await settleRequests(h);
  assert.deepEqual(JSON.parse(submitted.options.body), {
    nome_cliente: "Nuovo QA", telefono: "+39000999", data: BOOKING_DATE,
    ora: "20:15", coperti: 3, note: "Nota sintetica",
  });
  assert.equal(Object.keys(submitted.options.headers || {}).some((key) => /idempotency/i.test(key)), false);
  assert.equal(h.document.getElementById("booking-status-text").textContent, "Prenotazione aggiunta.");
});

test("Create failure keeps the dialog open and reports failure without false success", async (t) => {
  const h = fixture({ overrides: { "POST /api/bookings": () => json({ detail: "Server QA down" }, 500) } });
  t.after(() => h.dispose()); await openBookings(h);
  h.document.getElementById("booking-new-trigger").click(); fillForm(h);
  h.document.getElementById("booking-form").dispatchEvent(new h.window.Event("submit", { bubbles: true, cancelable: true }));
  await settleRequests(h);
  assert.equal(h.document.getElementById("booking-create-modal").hidden, false);
  assert.match(h.document.getElementById("booking-status-text").textContent, /Server QA down/);
  assert.notEqual(h.document.getElementById("booking-status-text").textContent, "Prenotazione aggiunta.");
});

test("Rapid duplicate create submits are serialized to one POST", async (t) => {
  const h = fixture(); t.after(() => h.dispose()); await openBookings(h);
  h.document.getElementById("booking-new-trigger").click(); fillForm(h);
  const form = h.document.getElementById("booking-form");
  form.dispatchEvent(new h.window.Event("submit", { bubbles: true, cancelable: true }));
  form.dispatchEvent(new h.window.Event("submit", { bubbles: true, cancelable: true }));
  await settleRequests(h);
  assert.equal(requests(h, "POST", "/api/bookings").length, 1);
});

test("Detail edit submits PUT to the selected booking ID with the form payload", async (t) => {
  let edited;
  const h = fixture({ overrides: { "PUT /api/bookings/qa-pending-1": (request) => { edited = request; return json(pending()); } } });
  t.after(() => h.dispose()); await openBookings(h);
  h.document.querySelector('[data-open-booking-id="qa-pending-1"]').click();
  h.document.getElementById("booking-edit-btn").click();
  assert.equal(h.document.getElementById("booking-name").value, "Cliente QA");
  fillForm(h, { name: "Aggiornato QA" });
  h.document.getElementById("booking-form").dispatchEvent(new h.window.Event("submit", { bubbles: true, cancelable: true }));
  await settleRequests(h);
  assert.equal(edited.pathname, "/api/bookings/qa-pending-1");
  assert.equal(edited.method, "PUT");
  assert.equal(JSON.parse(edited.options.body).nome_cliente, "Aggiornato QA");
  assert.equal(h.document.getElementById("booking-status-text").textContent, "Modifica salvata.");
});

test("Confirm uses the fixed action endpoint and closes detail after success", async (t) => {
  const h = fixture({ overrides: { "POST /api/bookings/qa-pending-1/confirm": () => json(pending({ stato: "confermata" })) } });
  t.after(() => h.dispose()); await openBookings(h);
  h.document.querySelector('[data-open-booking-id="qa-pending-1"]').click();
  h.document.getElementById("booking-confirm-btn").click(); await settleRequests(h);
  assert.equal(requests(h, "POST", "/api/bookings/qa-pending-1/confirm").length, 1);
  assert.equal(h.document.getElementById("booking-modal").hidden, true);
});

test("Cancel asks for confirmation before calling its action endpoint", async (t) => {
  const h = fixture({ overrides: { "POST /api/bookings/qa-pending-1/cancel": () => json(pending({ stato: "cancellata" })) } });
  t.after(() => h.dispose()); await openBookings(h);
  h.document.querySelector('[data-open-booking-id="qa-pending-1"]').click();
  h.document.getElementById("booking-cancel-btn").click(); await h.settle();
  assert.equal(h.document.getElementById("confirm-modal").hidden, false);
  assert.equal(requests(h, "POST", "/api/bookings/qa-pending-1/cancel").length, 0);
  h.document.getElementById("confirm-ok-btn").click();
  await settleRequests(h);
  assert.equal(requests(h, "POST", "/api/bookings/qa-pending-1/cancel").length, 1);
});

test("Action failures keep detail open and do not emit success toasts", async (t) => {
  const h = fixture({ overrides: { "POST /api/bookings/qa-pending-1/confirm": () => json({ detail: "Azione negata QA" }, 403) } });
  t.after(() => h.dispose()); await openBookings(h);
  h.document.querySelector('[data-open-booking-id="qa-pending-1"]').click();
  h.document.getElementById("booking-confirm-btn").click(); await settleRequests(h);
  assert.equal(h.document.getElementById("booking-modal").hidden, false);
  assert.doesNotMatch(h.document.getElementById("toast-container").textContent, /Prenotazione confermata/);
  assert.match(h.document.getElementById("toast-container").textContent, /Azione negata QA/);
});

test("Capacity save uses the exact settings PUT payload and reflects success only afterward", async (t) => {
  let submitted;
  const h = fixture({ overrides: { "PUT /api/bookings/settings": (request) => { submitted = request; return json({ capienze_orarie: { "20:00": 14 }, fasce_orarie: ["20:00"] }); } } });
  t.after(() => h.dispose()); await openBookings(h);
  h.document.getElementById("booking-actions-trigger").click(); h.document.getElementById("booking-open-availability").click(); await settleRequests(h);
  h.document.querySelector('[data-capacity-hour="20:00"]').value = "14";
  h.document.getElementById("capacity-save").click(); await settleRequests(h);
  assert.deepEqual(JSON.parse(submitted.options.body), { capienze_orarie: { "20:00": 14 } });
  assert.match(h.document.getElementById("capacity-status").textContent, /Capienza aggiornata/);
});

test("Staff can read bookings but cannot see detail mutation actions or load capacity settings", async (t) => {
  const h = fixture({ role: "staff", overrides: { "/api/bookings/settings": () => json({ detail: "Forbidden" }, 403) } });
  t.after(() => h.dispose()); await openBookings(h);
  assert.equal(h.document.querySelectorAll("#booking-table-body tr[data-booking-id]").length, 2);
  h.document.querySelector('[data-open-booking-id="qa-pending-1"]').click();
  assert.equal(h.document.getElementById("booking-detail-actions").hidden, true);
  assert.ok(requests(h, "GET", "/api/bookings/settings").length >= 1);
});

test("Stale list JSON from an old organization does not replace the current view", async (t) => {
  const old = deferred(); let hold = false;
  const h = fixture({ overrides: { "/api/bookings": () => {
    if (hold) return { ...json([]), json: () => old.promise };
    return json([pending({ id: "org-b-booking", nome_cliente: "Org B" })]);
  } } });
  t.after(() => h.dispose()); await openBookings(h);
  hold = true;
  const refresh = h.bookings.aggiornaPrenotazioni(); await h.settle();
  h.window.localStorage.setItem("melpis_selected_organization", "org-b");
  const module = h.bookings;
  module.invalidate();
  hold = false;
  await h.navigate("panoramica");
  await h.navigate("prenotazioni");
  await h.settle();
  old.resolve([pending({ id: "org-a-booking", nome_cliente: "Org A stale" })]);
  await refresh; await h.settle();
  assert.doesNotMatch(h.document.getElementById("booking-table-body").textContent, /Org A stale/);
});

test("Logout invalidation closes booking dialogs and clears sensitive details", async (t) => {
  const h = fixture(); t.after(() => h.dispose()); await openBookings(h);
  h.document.querySelector('[data-open-booking-id="qa-pending-1"]').click();
  assert.match(h.document.getElementById("booking-modal-title").textContent, /Cliente QA/);
  h.bookings.invalidate();
  assert.equal(h.document.getElementById("booking-modal-title").textContent, "");
  assert.equal(h.document.getElementById("booking-modal").hidden, true);
  assert.equal(h.document.getElementById("booking-create-modal").hidden, true);
});

test("Duplicate enter and visibility changes keep at most one bookings poller", async (t) => {
  const h = fixture(); t.after(() => h.dispose()); await openBookings(h);
  const module = h.bookings;
  module.onEnter(); module.onEnter();
  const pollers = () => [...h.intervals.values()].filter((timer) => timer.delay === 30000 && String(timer.callback).includes("aggiornaPrenotazioni"));
  assert.equal(pollers().length, 1);
  Object.defineProperty(h.document, "hidden", { configurable: true, value: true });
  h.document.dispatchEvent(new h.window.Event("visibilitychange"));
  assert.equal(pollers().length, 0);
  Object.defineProperty(h.document, "hidden", { configurable: true, value: false });
  h.document.dispatchEvent(new h.window.Event("visibilitychange"));
  assert.equal(pollers().length, 1);
  module.onExit(); assert.equal(pollers().length, 0);
});

test("Detail dialog is owned by dialog-focus, closes on Escape and restores focus", async (t) => {
  const h = fixture(); t.after(() => h.dispose()); await openBookings(h);
  const trigger = h.document.querySelector('[data-open-booking-id="qa-pending-1"]'); trigger.focus(); trigger.click();
  assert.equal(h.document.getElementById("booking-modal").hidden, false);
  h.document.dispatchEvent(new h.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  assert.equal(h.document.getElementById("booking-modal").hidden, true);
  assert.equal(h.document.activeElement, trigger);
});

test("Untrusted booking names, notes and IDs remain inert in rows and details", async (t) => {
  const hostile = pending({ id: 'qa" onmouseover="alert(1)', nome_cliente: "<img src=x onerror=alert(1)>", note: "<script>QA</script>" });
  const h = fixture({ bookings: [hostile] }); t.after(() => h.dispose()); await openBookings(h);
  assert.equal(h.document.querySelectorAll("#booking-table-body img, #booking-table-body script").length, 0);
  assert.equal(h.document.querySelectorAll('#booking-table-body [onmouseover="alert(1)"]').length, 0);
  assert.match(h.document.getElementById("booking-table-body").textContent, /<img src=x/);
  h.document.querySelector("#booking-table-body [data-open-booking-id]").click();
  assert.equal(h.document.querySelectorAll("#booking-modal img, #booking-modal script").length, 0);
  assert.match(h.document.getElementById("booking-detail-note").textContent, /<script>QA/);
});

test("15-minute event ending at midnight rolls to the next local date", async (t) => {
  const h = fixture({ bookings: [pending({ ora: "23:45" })] }); t.after(() => h.dispose()); await openBookings(h);
  const module = h.bookings;
  await module.aggiornaPrenotazioni?.();
  assert.equal(h.calendars[0].events[0].start, `${BOOKING_DATE}T23:45:00`);
  assert.equal(h.calendars[0].events[0].end, `${BOOKING_DATE}T24:00:00`);
  assert.ok(requests(h, "GET", "/api/bookings/semaforo").some((request) => request.search.includes(`data=${BOOKING_DATE}`)));
  assert.equal(h.document.querySelectorAll("#booking-table-body tr[data-booking-id]").length, 1);
});

test("Booking flows do not request Google Calendar OAuth or provider endpoints", async (t) => {
  const h = fixture({ overrides: { "POST /api/bookings": () => json(pending(), 201) } });
  t.after(() => h.dispose()); await openBookings(h);
  const start = h.requests.length;
  h.document.getElementById("booking-new-trigger").click(); fillForm(h);
  h.document.getElementById("booking-form").dispatchEvent(new h.window.Event("submit", { bubbles: true, cancelable: true }));
  await settleRequests(h);
  assert.equal(h.requests.slice(start).some((request) => /google|oauth|calendar\/connect/i.test(request.pathname)), false);
  assert.equal(h.unexpectedRequests.length, 0);
});

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

test("Staff forged create, capacity and action events issue no reserved writes", async (t) => {
  const h = fixture({ role: "staff" }); t.after(() => h.dispose()); await openBookings(h);
  h.document.getElementById("booking-new-trigger").click(); fillForm(h);
  h.document.getElementById("booking-form").dispatchEvent(new h.window.Event("submit", { cancelable: true }));
  h.document.getElementById("capacity-save").dispatchEvent(new h.window.Event("click"));
  h.document.querySelector('[data-open-booking-id="qa-pending-1"]').click();
  h.document.getElementById("booking-confirm-btn").dispatchEvent(new h.window.Event("click"));
  await h.settle();
  assert.equal(h.requests.filter(r => ["POST", "PUT"].includes(r.method) && r.pathname.startsWith("/api/bookings")).length, 0);
});

test("Route exit cancels its confirmation before any obsolete mutation", async (t) => {
  const h = fixture(); t.after(() => h.dispose()); await openBookings(h);
  h.document.querySelector('[data-open-booking-id="qa-pending-1"]').click();
  h.document.getElementById("booking-cancel-btn").click(); await h.settle();
  assert.equal(h.document.getElementById("confirm-modal").hidden, false);
  await h.navigate("panoramica");
  h.document.getElementById("confirm-ok-btn").click(); await h.settle();
  assert.equal(h.document.getElementById("confirm-modal").hidden, true);
  assert.equal(h.document.getElementById("booking-modal").hidden, true);
  assert.equal(requests(h, "POST", "/api/bookings/qa-pending-1/cancel").length, 0);
});

test("Single-flight load and polling reuse pending reads, explicit refresh still supersedes", async (t) => {
  const read = deferred(); let hold = false;
  const h = fixture({ overrides: { "/api/bookings": () => hold ? read.promise : json([pending()]) } });
  t.after(() => h.dispose()); await openBookings(h); hold = true;
  const mark = requests(h, "GET", "/api/bookings").length;
  const first = h.bookings.aggiornaPrenotazioni({ reuse: true });
  const second = h.bookings.aggiornaPrenotazioni({ reuse: true });
  assert.equal(first, second);
  assert.equal(requests(h, "GET", "/api/bookings").length, mark + 1);
  read.resolve(json([pending()])); await first;
});

test("Pending write lock survives route exit and reopening until the request settles", async (t) => {
  const write = deferred();
  const h = fixture({ overrides: { "POST /api/bookings": () => write.promise } });
  t.after(() => h.dispose()); await openBookings(h);
  const submit = () => { h.document.getElementById("booking-new-trigger").click(); fillForm(h); h.document.getElementById("booking-form").dispatchEvent(new h.window.Event("submit", { cancelable: true })); };
  submit(); await h.navigate("panoramica"); await h.navigate("prenotazioni"); submit();
  assert.equal(requests(h, "POST", "/api/bookings").length, 1);
  write.resolve(json(pending(), 201)); await h.settle();
  assert.equal(h.document.getElementById("booking-create-modal").hidden, false);
  assert.equal(h.document.getElementById("booking-name").value, "Nuovo QA");
  assert.doesNotMatch(h.document.getElementById("booking-status-text").textContent, /aggiunta/);
});

test("Capacity delayed JSON after logout cannot repopulate private DOM or show success", async (t) => {
  const body = deferred();
  const h = fixture({ overrides: { "PUT /api/bookings/settings": () => ({ ok: true, json: () => body.promise }) } });
  t.after(() => h.dispose()); await openBookings(h);
  h.document.getElementById("capacity-save").click(); await h.settle();
  h.bookings.invalidate(); body.resolve({ capienze_orarie: { "20:00": 99 } }); await h.settle();
  assert.equal(h.document.getElementById("capacity-status").textContent, "");
  assert.equal(h.document.getElementById("booking-settings-grid").children.length, 0);
  assert.equal(h.document.getElementById("booking-table-body").children.length, 0);
});

test("Network timeout on create stays controlled, without false success or sensitive logs", async (t) => {
  const h = fixture({ overrides: { "POST /api/bookings": () => Promise.reject(new Error("Errore di connessione.")) } });
  t.after(() => h.dispose()); await openBookings(h);
  h.document.getElementById("booking-new-trigger").click(); fillForm(h);
  h.document.getElementById("booking-form").dispatchEvent(new h.window.Event("submit", { cancelable: true }));
  await h.settle();
  assert.equal(h.document.getElementById("booking-create-modal").hidden, false);
  assert.match(h.document.getElementById("booking-status-text").textContent, /Errore di connessione/);
  assert.equal(h.errors.length, 0);
});

test("Scope switch immediately purges old calendar, rows, settings and detail state", async (t) => {
  const h = fixture(); t.after(() => h.dispose()); await openBookings(h);
  const old = h.calendars[0];
  h.document.querySelector('[data-open-booking-id="qa-pending-1"]').click();
  h.window.localStorage.setItem("melpis_selected_organization", "org-b");
  h.bookings.onEnter();
  assert.equal(h.document.getElementById("booking-table-body").children.length, 0);
  assert.equal(h.document.getElementById("booking-modal-title").textContent, "");
  assert.equal(h.document.getElementById("booking-settings-grid").children.length, 0);
  assert.equal(old.events.length, 0);
});

test("API permission denial purges existing bookings instead of retaining sensitive records", async (t) => {
  let denied = false;
  const h = fixture({ overrides: { "/api/bookings": () => denied ? json({ detail: "Forbidden" }, 403) : json([pending()]) } });
  t.after(() => h.dispose()); await openBookings(h); denied = true;
  await h.bookings.aggiornaPrenotazioni();
  assert.equal(h.document.getElementById("booking-table-body").children.length, 0);
  assert.equal(h.document.getElementById("booking-settings-grid").children.length, 0);
});

test("Repeated route entries never duplicate submit listeners or calendar instances", async (t) => {
  const h = fixture(); t.after(() => h.dispose()); await openBookings(h);
  const form = h.document.getElementById("booking-form");
  const count = h.listenerRegistrations.filter(r => r.target === form && r.type === "submit").length;
  for (let i = 0; i < 3; i++) { await h.navigate("panoramica"); await h.navigate("prenotazioni"); }
  assert.equal(h.listenerRegistrations.filter(r => r.target === form && r.type === "submit").length, count);
  assert.equal(h.calendars.length, 1);
});

for (const date of ["2026-03-29", "2026-10-25"]) {
  test(`DST boundary ${date} preserves local date and fifteen-minute slot`, async (t) => {
    const h = fixture({ bookings: [pending({ data: date, ora: "01:45" })] });
    t.after(() => h.dispose()); await openBookings(h);
    const calendar = h.calendars[0];
    calendar.gotoDate(date); await h.settle();
    assert.equal(calendar.events[0].start, `${date}T01:45:00`);
    assert.equal(calendar.events[0].end, `${date}T02:00:00`);
    assert.equal(h.document.getElementById("booking-date-picker").value, date);
    assert.match(requests(h, "GET", "/api/bookings/semaforo").at(-1).search, new RegExp(date));
  });
}

test("Capacity double click shares one pending PUT, no late success after route exit", async (t) => {
  const write = deferred();
  const h = fixture({ overrides: { "PUT /api/bookings/settings": () => write.promise } });
  t.after(() => h.dispose()); await openBookings(h);
  h.document.getElementById("capacity-save").click(); h.document.getElementById("capacity-save").click();
  assert.equal(requests(h, "PUT", "/api/bookings/settings").length, 1);
  await h.navigate("panoramica");
  write.resolve(json({ capienze_orarie: { "20:00": 99 } })); await h.settle();
  assert.doesNotMatch(h.document.getElementById("capacity-status").textContent, /aggiornata/);
});

test("Revoked role during confirmation cannot authorize the pending action", async (t) => {
  const identity = { user_id: "user-1", organization_id: "org-1", email: "qa@example.test", ruolo: "owner" };
  const h = fixture({ overrides: { "/api/auth/me": identity } });
  t.after(() => h.dispose()); await openBookings(h);
  h.document.querySelector('[data-open-booking-id="qa-pending-1"]').click();
  h.document.getElementById("booking-cancel-btn").click(); await h.settle();
  identity.ruolo = "staff";
  h.document.getElementById("confirm-ok-btn").click(); await h.settle();
  assert.equal(requests(h, "POST", "/api/bookings/qa-pending-1/cancel").length, 0);
});

for (const stale of [false, true]) {
  test(`CSV export ${stale ? "discards a stale blob after logout" : "preserves range query and one download"}`, async (t) => {
    const body = deferred(); let downloads = 0;
    const h = fixture({ overrides: { "/api/report/csv": () => ({ ...json(null), blob: () => body.promise }) } });
    t.after(() => h.dispose()); await openBookings(h);
    h.window.URL.createObjectURL = () => { downloads++; return "blob:synthetic"; };
    h.window.URL.revokeObjectURL = () => {};
    h.window.HTMLAnchorElement.prototype.click = () => {};
    h.document.getElementById("booking-export-da").value = "2026-10-08";
    h.document.getElementById("booking-export-a").value = "2026-10-09";
    h.document.getElementById("booking-export-csv").click(); await h.settle();
    assert.equal(requests(h, "GET", "/api/report/csv")[0].search, "?da=2026-10-08&a=2026-10-09");
    if (stale) h.bookings.invalidate();
    body.resolve(new h.window.Blob(["synthetic CSV"])); await h.settle();
    assert.equal(downloads, stale ? 0 : 1);
    if (stale) assert.doesNotMatch(h.document.getElementById("toast-container")?.textContent || "", /Export scaricato/);
    else assert.match(h.document.getElementById("toast-container").textContent, /Export scaricato/);
  });
}

test("Overview booking setup opens the existing availability dialog through shell wiring", async (t) => {
  const h = fixture({ settings: { capienze_orarie: {}, fasce_orarie: [] } });
  t.after(() => h.dispose()); await h.ready();
  const setup = h.document.querySelector('[data-action="setup-booking"]');
  assert.ok(setup);
  setup.click(); await h.settle();
  for (const timer of [...h.timeouts.values()]) {
    if (timer.delay === 100) timer.callback();
  }
  await h.settle();
  assert.equal(h.document.getElementById("booking-availability-modal").hidden, false);
  assert.equal(h.windowErrors.length, 0);
});

test("Simulator success retains the wired Bookings callbacks and completes notification refresh", async (t) => {
  const h = fixture({ overrides: { "POST /api/messaggio": () => json({ risposta: "Risposta QA", richiede_umano: false }) } });
  t.after(() => h.dispose()); await h.ready(); await h.navigate("assistente");
  const before = requests(h, "GET", "/api/ui/summary").length;
  h.document.getElementById("chat-input").value = "Messaggio sintetico";
  h.document.getElementById("chat-form").dispatchEvent(new h.window.Event("submit", { bubbles: true, cancelable: true }));
  await h.waitFor(() => requests(h, "POST", "/api/messaggio").length === 1);
  await h.settle();
  assert.match(h.document.getElementById("chat-body").textContent, /Risposta QA/);
  assert.doesNotMatch(h.document.getElementById("chat-body").textContent, /Non riesco a contattare/);
  assert.ok(requests(h, "GET", "/api/ui/summary").length > before);
  assert.equal(h.windowErrors.length, 0);
});

for (const settings of [null, {}, { fasce_orarie: [], capienze_orarie: {} }]) {
  test(`Unconfigured capacity settings ${JSON.stringify(settings)} expose all 24 editable hours`, async (t) => {
    const h = fixture({ settings }); t.after(() => h.dispose()); await openBookings(h);
    const inputs = [...h.document.querySelectorAll('[data-capacity-hour]')];
    assert.equal(inputs.length, 24);
    assert.equal(inputs[0].dataset.capacityHour, "00:00");
    assert.equal(inputs.at(-1).dataset.capacityHour, "23:00");
    assert.ok(inputs.every(input => input.value === "40"));
    assert.equal(h.document.getElementById("booking-standard-capacity").value, "40");
  });
}

test("Capacity editor includes saved hours missing from the list and preserves explicit zero", async (t) => {
  const h = fixture({ settings: { fasce_orarie: ["20:00"], capienze_orarie: { "20:00": 10, "21:00": 0, "21:30": 5 } } });
  t.after(() => h.dispose()); await openBookings(h);
  assert.equal(h.document.querySelector('[data-capacity-hour="21:00"]').value, "0");
  assert.equal(h.document.querySelector('[data-capacity-hour="21:30"]').value, "5");
  assert.equal(h.document.querySelectorAll('[data-capacity-hour]').length, 3);
});

test("Zero capacity is saved per hour and survives reload, standard changes preserve closed slots", async (t) => {
  let settings = null; let submitted;
  const h = fixture({ overrides: {
    "/api/bookings/settings": () => json(settings),
    "PUT /api/bookings/settings": request => {
      submitted = JSON.parse(request.options.body);
      settings = { fasce_orarie: Object.keys(submitted.capienze_orarie), ...submitted };
      return json(settings);
    },
  } });
  t.after(() => h.dispose()); await openBookings(h);
  const standard = h.document.getElementById("booking-standard-capacity");
  standard.value = "30"; standard.dispatchEvent(new h.window.Event("change"));
  h.document.querySelector('[data-capacity-hour="20:00"]').value = "12";
  h.document.querySelector('[data-capacity-hour="21:00"]').value = "0";
  h.document.getElementById("capacity-save").click(); await settleRequests(h);
  assert.equal(Object.keys(submitted.capienze_orarie).length, 24);
  assert.equal(submitted.capienze_orarie["19:00"], 30);
  assert.equal(submitted.capienze_orarie["20:00"], 12);
  assert.equal(submitted.capienze_orarie["21:00"], 0);
  h.bookings.invalidate(); await h.navigate("panoramica"); await h.navigate("prenotazioni"); await h.settle();
  assert.equal(h.document.querySelector('[data-capacity-hour="20:00"]').value, "12");
  assert.equal(h.document.querySelector('[data-capacity-hour="21:00"]').value, "0");
  standard.value = "25"; standard.dispatchEvent(new h.window.Event("change"));
  assert.equal(h.document.querySelector('[data-capacity-hour="20:00"]').value, "25");
  assert.equal(h.document.querySelector('[data-capacity-hour="21:00"]').value, "0");
});

test("All closed capacity hours display standard zero without reopening them", async (t) => {
  const h = fixture({ settings: { fasce_orarie: ["20:00", "21:00"], capienze_orarie: { "20:00": 0, "21:00": 0 } } });
  t.after(() => h.dispose()); await openBookings(h);
  assert.equal(h.document.getElementById("booking-standard-capacity").value, "0");
});

for (const value of ["", "-1", "501", "1.5"]) {
  test(`Invalid hourly capacity ${JSON.stringify(value)} cannot issue a settings write`, async (t) => {
    const h = fixture(); t.after(() => h.dispose()); await openBookings(h);
    h.document.querySelector('[data-capacity-hour="20:00"]').value = value;
    h.document.getElementById("capacity-save").click(); await h.settle();
    assert.equal(requests(h, "PUT", "/api/bookings/settings").length, 0);
  });
}

test("Failed capacity settings load cannot save an empty map or invent editable defaults", async (t) => {
  const h = fixture({ overrides: { "/api/bookings/settings": () => json({ detail: "Forbidden" }, 403) } });
  t.after(() => h.dispose()); await openBookings(h);
  assert.equal(h.document.querySelectorAll('[data-capacity-hour]').length, 0);
  h.document.getElementById("capacity-save").click(); await h.settle();
  assert.equal(requests(h, "PUT", "/api/bookings/settings").length, 0);
});
