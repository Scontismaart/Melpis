const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const root = path.resolve(__dirname, "../..");
const source = fs.readFileSync(path.join(root, "web/dashboard-inbox.js"), "utf8");
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function response(data, ok = true) { return { ok, json: async () => data }; }
function ticket(id, status = "CLAIMED") {
  return { id, phone_number: id, ticket_status: status, canale: "whatsapp", version: 1 };
}
function harness() {
  const dom = new JSDOM(fs.readFileSync(path.join(root, "web/index.html"), "utf8"), {
    url: "https://melpis.test/app/inbox", runScripts: "outside-only", pretendToBeVisual: true,
  });
  const win = dom.window;
  win.innerWidth = 500;
  win.eval(source);
  const requests = [], toasts = [], errors = [], intervals = new Map(), callbacks = [];
  const context = { userId: "user-1", sessionOrganizationId: "org-1", selectedOrganizationId: "org-1", view: "inbox", transition: 1, epoch: 0 };
  let intervalId = 0;
  win.setInterval = (callback, delay) => { const id = ++intervalId; intervals.set(id, { callback, delay }); return id; };
  win.clearInterval = (id) => intervals.delete(id);
  win.requestAnimationFrame = (callback) => { callbacks.push(callback); return callbacks.length; };
  win.setTimeout = (callback) => { callbacks.push(callback); return callbacks.length; };
  const module = win.MelpisDashboardInbox.create({
    API_BASE: "", getContext: () => ({ ...context }),
    apiFetch(url, options = {}) { const item = { url, options, ...deferred() }; requests.push(item); return item.promise; },
    t: (key, vars) => `${win.qaLanguage || "it"}:${key}${vars?.name ? ` ${vars.name}` : ""}`,
    localeCorrente: () => "it-IT", toast: (...args) => toasts.push(args),
    _skeletonList: () => "<div class='skeleton'>loading</div>",
    _emptyState: (_icon, title) => { const el = win.document.createElement("p"); el.textContent = title; return el; },
    _errorState: (title) => { const el = win.document.createElement("p"); el.textContent = title; return el; },
    ICONS: { inbox: "" }, apriVistaImpostazioni() {},
  });
  async function loadTickets(items, team = []) {
    const pending = module.load();
    const pair = requests.slice(-2);
    assert.equal(pair[0].url, "/api/inbox/tickets?limit=100");
    assert.equal(pair[1].url, "/api/inbox/team");
    pair[0].resolve(response({ tickets: items }));
    pair[1].resolve(response({ members: team }));
    await pending;
  }
  async function select(id, messages = []) {
    const row = [...win.document.querySelectorAll(".inbox-conv-row")].find((el) => el.dataset.ticketId === id);
    assert.ok(row, `ticket ${id} rendered`);
    row.click();
    const request = requests.at(-1);
    assert.match(request.url, /\/messages\?limit=200$/);
    request.resolve(response({ messages }));
    for (let index = 0; index < 12; index++) await Promise.resolve();
  }
  return { dom, win, document: win.document, module, context, requests, toasts, errors, intervals, callbacks,
    loadTickets, select, close: () => dom.window.close() };
}

test("Inbox factory has the narrow public API and one polling timer", async (t) => {
  const h = harness(); t.after(h.close);
  assert.deepEqual(Object.keys(h.module).sort(), ["invalidate", "load", "onEnter", "onExit", "refreshLanguage"].sort());
  assert.ok(Object.isFrozen(h.module));
  h.module.onEnter(); h.module.onEnter();
  assert.deepEqual([...h.intervals.values()].map((item) => item.delay), [3000]);
  const first = h.module.load(), second = h.module.load();
  assert.equal(h.requests.length, 2, "same-scope loads share one flight");
  h.requests[0].resolve(response({ tickets: [] })); h.requests[1].resolve(response({ members: [] }));
  await Promise.all([first, second]);
  h.module.onExit(); assert.equal(h.intervals.size, 0);
});

test("Inbox polling skips unsettled load, pauses hidden, and resumes once", async (t) => {
  const h = harness(); t.after(h.close);
  h.module.onEnter();
  const pending = h.module.load();
  h.intervals.values().next().value.callback();
  assert.equal(h.requests.length, 2, "poll does not overlap unsettled list flight");
  Object.defineProperty(h.document, "hidden", { configurable: true, value: true });
  h.document.dispatchEvent(new h.win.Event("visibilitychange"));
  assert.equal(h.intervals.size, 0);
  h.requests[0].resolve(response({ tickets: [] }));
  h.requests[1].resolve(response({ members: [] }));
  await pending;
  Object.defineProperty(h.document, "hidden", { configurable: true, value: false });
  h.document.dispatchEvent(new h.win.Event("visibilitychange"));
  assert.equal(h.intervals.size, 1);
  assert.equal(h.requests.length, 4, "visibility resume refreshes once");
  h.requests[2].resolve(response({ tickets: [] }));
  h.requests[3].resolve(response({ members: [] }));
});

test("Inbox scope change clears private DOM and rejects old list completion", async (t) => {
  const h = harness(); t.after(h.close);
  await h.loadTickets([ticket("old")]);
  assert.match(h.document.getElementById("inbox-list").textContent, /old/);
  h.context.selectedOrganizationId = "org-2";
  const pending = h.module.load();
  assert.doesNotMatch(h.document.getElementById("inbox-list").textContent, /old/);
  h.requests.at(-2).resolve(response({ tickets: [ticket("new")] }));
  h.requests.at(-1).resolve(response({ members: [] }));
  await pending;
  assert.match(h.document.getElementById("inbox-list").textContent, /new/);
  h.module.invalidate();
  assert.equal(h.document.getElementById("inbox-list").textContent, "");
  assert.equal(h.document.getElementById("inbox-thread-messages").textContent, "");
});

test("Inbox exit then organization change clears old rows and drafts before new JSON", async (t) => {
  const h = harness(); t.after(h.close);
  await h.loadTickets([ticket("private A")]);
  await h.select("private A");
  const input = h.document.getElementById("inbox-message-input");
  input.value = "tenant A draft";
  input.dispatchEvent(new h.win.Event("input", { bubbles: true }));
  h.module.onExit();
  h.context.selectedOrganizationId = "org-2";
  h.context.transition++;
  h.module.onEnter();
  assert.equal(h.document.getElementById("inbox-list").textContent, "");
  assert.equal(h.document.getElementById("inbox-thread-messages").textContent, "");
  assert.equal(input.value, "");
  const pending = h.module.load();
  assert.equal(h.document.getElementById("inbox-list").textContent.includes("private A"), false);
  h.requests.at(-2).resolve(response({ tickets: [ticket("tenant B")] }));
  h.requests.at(-1).resolve(response({ members: [] }));
  await pending;
  assert.match(h.document.getElementById("inbox-list").textContent, /tenant B/);
});

test("Inbox old list JSON after leave and same-scope return cannot replace new visit", async (t) => {
  const h = harness(); t.after(h.close);
  h.module.onEnter();
  const old = h.module.load();
  const oldTickets = h.requests[0], oldTeam = h.requests[1];
  h.module.onExit(); h.context.transition++;
  h.module.onEnter();
  const fresh = h.module.load();
  h.requests[2].resolve(response({ tickets: [ticket("fresh")] }));
  h.requests[3].resolve(response({ members: [] }));
  await fresh;
  oldTickets.resolve(response({ tickets: [ticket("stale")] }));
  oldTeam.resolve(response({ members: [] }));
  await old;
  assert.match(h.document.getElementById("inbox-list").textContent, /fresh/);
  assert.doesNotMatch(h.document.getElementById("inbox-list").textContent, /stale/);
});

test("Inbox rapid A to B selection rejects late A thread and failure", async (t) => {
  const h = harness(); t.after(h.close);
  await h.loadTickets([ticket("A"), ticket("B")]);
  const rows = [...h.document.querySelectorAll(".inbox-conv-row")];
  rows[0].click();
  const old = h.requests.at(-1);
  rows[1].click();
  const fresh = h.requests.at(-1);
  fresh.resolve(response({ messages: [{ id: "b", direction: "inbound", content_text: "B thread" }] }));
  for (let index = 0; index < 12; index++) await Promise.resolve();
  old.reject(new Error("obsolete A"));
  await new Promise((resolve) => setImmediate(resolve));
  assert.match(h.document.getElementById("inbox-thread-messages").textContent, /B thread/);
  assert.equal(h.document.getElementById("inbox-detail-name").textContent, "B");
});

test("Inbox ticket and detail 403 clear private data", async (t) => {
  const h = harness(); t.after(h.close);
  await h.loadTickets([ticket("private")]);
  const row = h.document.querySelector(".inbox-conv-row");
  row.click();
  h.requests.at(-1).resolve({ ok: false, status: 403 });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(h.document.getElementById("inbox-list").textContent, "");
  assert.equal(h.document.getElementById("inbox-detail-name").textContent, "");
  assert.equal(h.intervals.size, 0);
});

test("Inbox empty, API error, and session invalidation show no stale private state", async (t) => {
  const h = harness(); t.after(h.close);
  const empty = h.module.load();
  h.requests[0].resolve(response({ tickets: [] })); h.requests[1].resolve(response({ members: [] })); await empty;
  assert.match(h.document.getElementById("inbox-list").textContent, /empty_waiting|empty_no_conversations/);
  const failing = h.module.load();
  h.requests.at(-2).resolve(response({}, false)); h.requests.at(-1).resolve(response({ members: [] })); await failing;
  assert.match(h.document.getElementById("inbox-list").textContent, /Impossibile caricare le conversazioni/);
  h.module.invalidate();
  assert.equal(h.document.getElementById("inbox-list").textContent, "");
  assert.equal(h.document.getElementById("inbox-search-input").value, "");
});

test("Inbox logout invalidation blocks pending 401 read and reply completions", async (t) => {
  const h = harness(); t.after(h.close);
  await h.loadTickets([ticket("A")]); await h.select("A");
  const pendingLoad = h.module.load();
  const pendingTickets = h.requests.at(-2), pendingTeam = h.requests.at(-1);
  const input = h.document.getElementById("inbox-message-input"); input.value = "private reply";
  h.document.getElementById("inbox-reply-form").dispatchEvent(new h.win.Event("submit", { bubbles: true, cancelable: true }));
  const pendingReply = h.requests.at(-1);
  h.module.invalidate(); h.context.userId = null;
  const readsBefore = h.requests.length;
  pendingTickets.resolve({ ok: false, status: 401 }); pendingTeam.resolve(response({ members: [] }));
  pendingReply.resolve({ ok: false, status: 401 });
  await pendingLoad; await new Promise((resolve) => setImmediate(resolve));
  assert.equal(h.requests.length, readsBefore);
  assert.equal(h.document.getElementById("inbox-list").textContent, "");
  assert.equal(h.document.getElementById("inbox-thread-messages").textContent, "");
  assert.equal(h.document.getElementById("inbox-message-input").value, "");
  assert.equal(h.toasts.length, 0);
});

test("Inbox assign defers authority to backend and a 403 clears private UI", async (t) => {
  const h = harness(); t.after(h.close);
  await h.loadTickets([ticket("private")], [{ user_id: "staff-2", nome: "Staff 2" }]);
  await h.select("private");
  const assign = h.document.querySelector("#inbox-detail-actions select");
  assign.value = "staff-2";
  assign.dispatchEvent(new h.win.Event("change", { bubbles: true }));
  const request = h.requests.at(-1);
  assert.equal(request.url, "/api/inbox/assign/private");
  assert.equal(JSON.parse(request.options.body).assigned_to, "staff-2");
  request.resolve({ ok: false, status: 403 });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(h.document.getElementById("inbox-list").textContent, "");
  assert.equal(h.document.getElementById("inbox-detail-name").textContent, "");
});

test("Inbox reply duplicate triggers share one POST and ambiguous 200 retains draft and key", async (t) => {
  const h = harness(); t.after(h.close);
  await h.loadTickets([ticket("A"), ticket("B")]);
  await h.select("A");
  const input = h.document.getElementById("inbox-message-input");
  input.value = "Hello A";
  input.dispatchEvent(new h.win.Event("input", { bubbles: true }));
  const form = h.document.getElementById("inbox-reply-form");
  form.dispatchEvent(new h.win.Event("submit", { bubbles: true, cancelable: true }));
  h.document.getElementById("inbox-send-btn").click();
  input.dispatchEvent(new h.win.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  const posts = () => h.requests.filter((request) => request.url.includes("/api/inbox/reply/"));
  assert.equal(posts().length, 1);
  const firstKey = JSON.parse(posts()[0].options.body).idempotency_key;
  posts()[0].resolve(response({ message_id: "msg-1", status: "sending_ambiguous" }));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(input.value, "Hello A");
  assert.ok(h.toasts.every(([message]) => message !== "Messaggio inviato"));
  form.dispatchEvent(new h.win.Event("submit", { bubbles: true, cancelable: true }));
  assert.equal(posts().length, 2, "explicit retry is possible");
  assert.equal(JSON.parse(posts()[1].options.body).idempotency_key, firstKey, "retry reuses idempotency key");
  posts()[1].resolve(response({ message_id: "msg-1", status: "queued" }));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(input.value, "Hello A");
  assert.ok(h.toasts.every(([message]) => message !== "Messaggio inviato"));
});

test("Inbox draft is ticket-owned and hostile message content or ID cannot create unsafe links", async (t) => {
  const h = harness(); t.after(h.close);
  await h.loadTickets([ticket("A"), ticket("B")]);
  const hostileId = 'x\"] img[src=x]';
  await h.select("A", [{ id: hostileId, direction: "inbound", content_text: '[safe](https://example.com/\"onclick=alert(1)) <img src=x> www.example.org' }]);
  const thread = h.document.getElementById("inbox-thread-messages");
  assert.equal(thread.querySelectorAll("img").length, 0);
  assert.ok([...thread.querySelectorAll("a")].every((link) => ["http:", "https:"].includes(new URL(link.href).protocol)));
  const input = h.document.getElementById("inbox-message-input");
  input.value = "draft A";
  input.dispatchEvent(new h.win.Event("input", { bubbles: true }));
  await h.select("B");
  assert.equal(input.value, "");
  input.value = "draft B";
  input.dispatchEvent(new h.win.Event("input", { bubbles: true }));
  await h.select("A");
  assert.equal(input.value, "draft A");
});

test("Inbox only clears a draft after a confirmed message ID and sent status", async (t) => {
  const h = harness(); t.after(h.close);
  await h.loadTickets([ticket("A")]);
  await h.select("A");
  const input = h.document.getElementById("inbox-message-input");
  input.value = "hello";
  const form = h.document.getElementById("inbox-reply-form");
  form.dispatchEvent(new h.win.Event("submit", { bubbles: true, cancelable: true }));
  h.requests.at(-1).resolve(response({ status: "sent" }));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(input.value, "hello");
  assert.ok(h.toasts.every(([message]) => message !== "Messaggio inviato"));
  form.dispatchEvent(new h.win.Event("submit", { bubbles: true, cancelable: true }));
  h.requests.at(-1).resolve(response({ message_id: "msg-1", status: "sent" }));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(input.value, "");
  assert.ok(h.toasts.some(([message]) => message === "Messaggio inviato"));
  const detail = h.requests.at(-1);
  assert.match(detail.url, /\/messages\?limit=200$/);
  detail.resolve(response({ messages: [] }));
  await new Promise((resolve) => setImmediate(resolve));
  const pair = h.requests.slice(-2);
  pair[0].resolve(response({ tickets: [ticket("A")] }));
  pair[1].resolve(response({ members: [] }));
});

test("Inbox late reply from A cannot clear B draft or announce A in B", async (t) => {
  const h = harness(); t.after(h.close);
  await h.loadTickets([ticket("A"), ticket("B")]);
  await h.select("A");
  const input = h.document.getElementById("inbox-message-input");
  input.value = "A draft";
  h.document.getElementById("inbox-reply-form").dispatchEvent(new h.win.Event("submit", { bubbles: true, cancelable: true }));
  const post = h.requests.at(-1);
  await h.select("B");
  input.value = "B draft";
  input.dispatchEvent(new h.win.Event("input", { bubbles: true }));
  const count = h.requests.length;
  post.resolve(response({ message_id: "a-message", status: "sent" }));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(input.value, "B draft");
  assert.equal(h.requests.length, count, "A reply does not refresh the B thread");
  assert.equal(h.toasts.length, 0);
});

test("Inbox release and feedback locks survive controls rerender", async (t) => {
  const h = harness(); t.after(h.close);
  await h.loadTickets([ticket("A")]);
  await h.select("A", [{ id: "m-1", direction: "outbound", handling_type: "ai_handled", content_text: "reply" }]);
  const up = h.document.querySelector(".thread-feedback-btn");
  up.click();
  const down = h.document.querySelectorAll(".thread-feedback-btn")[1];
  down.click();
  assert.equal(h.requests.filter((request) => request.url.includes("/feedback")).length, 1);
  assert.deepEqual(JSON.parse(h.requests.at(-1).options.body), { value: "up" });
  h.requests.at(-1).resolve(response({}));
  await new Promise((resolve) => setImmediate(resolve));
  const release = [...h.document.querySelectorAll("#inbox-detail-actions button")]
    .find((button) => button.textContent.endsWith("inbox:chat.release_btn"));
  release.click();
  h.module.refreshLanguage();
  const rerendered = [...h.document.querySelectorAll("#inbox-detail-actions button")]
    .find((button) => button.textContent.endsWith("inbox:chat.release_btn"));
  assert.equal(rerendered.disabled, true);
  rerendered.click();
  assert.equal(h.requests.filter((request) => request.url.includes("/api/inbox/release/")).length, 1);
  h.requests.at(-1).resolve(response({}));
});

test("Inbox claim toolbar and inline button share a lock across rerender", async (t) => {
  const h = harness(); t.after(h.close);
  await h.loadTickets([ticket("A", "PENDING_STAFF")]);
  await h.select("A");
  const claim = h.document.querySelector("#inbox-detail-actions .inbox-action-btn");
  claim.click();
  h.module.refreshLanguage();
  const inline = h.document.getElementById("inbox-claim-inline-btn");
  inline.click();
  assert.equal(h.requests.filter((request) => request.url.includes("/api/inbox/claim/")).length, 1);
  assert.equal(inline.disabled, true);
  h.requests.at(-1).resolve(response({}));
  await new Promise((resolve) => setImmediate(resolve));
});

test("Inbox commits ticket and team JSON atomically", async (t) => {
  const h = harness(); t.after(h.close);
  let resolveTeam;
  const teamJson = new Promise((resolve) => { resolveTeam = resolve; });
  const pending = h.module.load();
  h.requests[0].resolve(response({ tickets: [ticket("atomic")] }));
  h.requests[1].resolve({ ok: true, json: () => teamJson });
  await new Promise((resolve) => setImmediate(resolve));
  const rowCountBeforeTeam = h.document.querySelectorAll(".inbox-conv-row").length;
  resolveTeam({ members: [{ user_id: "atomic-team", nome: "Atomic team" }] });
  await pending;
  assert.equal(rowCountBeforeTeam, 0, "partial ticket data stays uncommitted while team JSON is pending");
  assert.match(h.document.getElementById("inbox-list").textContent, /atomic/);
});

test("Inbox old success and failure after exit/re-entry cannot replace current thread", async (t) => {
  for (const fail of [false, true]) {
    const h = harness(); t.after(h.close);
    await h.loadTickets([ticket("A"), ticket("B")]);
    const rowA = [...h.document.querySelectorAll(".inbox-conv-row")].find((row) => row.dataset.ticketId === "A");
    rowA.click(); const stale = h.requests.at(-1);
    h.module.onExit(); h.context.transition++; h.module.onEnter();
    const freshLoad = h.module.load();
    h.requests.at(-2).resolve(response({ tickets: [ticket("A"), ticket("B")] }));
    h.requests.at(-1).resolve(response({ members: [] }));
    await new Promise((resolve) => setImmediate(resolve));
    const reentryDetail = h.requests.at(-1); assert.match(reentryDetail.url, /\/messages\?limit=200$/);
    reentryDetail.resolve(response({ messages: [{ id: "a-reentry", direction: "inbound", content_text: "reentry A" }] }));
    await freshLoad;
    await h.select("B", [{ id: "b-new", direction: "inbound", content_text: "current B thread" }]);
    if (fail) stale.reject(new Error("obsolete A")); else stale.resolve(response({ messages: [{ id: "a-old", direction: "inbound", content_text: "obsolete A" }] }));
    await new Promise((resolve) => setImmediate(resolve));
    assert.match(h.document.getElementById("inbox-thread-messages").textContent, /current B thread/);
    assert.doesNotMatch(h.document.getElementById("inbox-thread-messages").textContent, /obsolete A/);
  }
});

test("Inbox delayed scroll callbacks for A cannot move B thread", async (t) => {
  const h = harness(); t.after(h.close);
  await h.loadTickets([ticket("A"), ticket("B")]);
  const rowA = [...h.document.querySelectorAll(".inbox-conv-row")].find((row) => row.dataset.ticketId === "A");
  rowA.click(); h.requests.at(-1).resolve(response({ messages: [{ id: "a1", direction: "inbound", content_text: "A" }] }));
  await new Promise((resolve) => setImmediate(resolve));
  const callbacks = h.callbacks.splice(0);
  const rowB = [...h.document.querySelectorAll(".inbox-conv-row")].find((row) => row.dataset.ticketId === "B");
  rowB.click(); h.requests.at(-1).resolve(response({ messages: [{ id: "b1", direction: "inbound", content_text: "B" }] }));
  await new Promise((resolve) => setImmediate(resolve));
  const thread = h.document.getElementById("inbox-thread-messages");
  Object.defineProperty(thread, "scrollHeight", { configurable: true, value: 1000 }); thread.scrollTop = 25;
  callbacks.forEach((callback) => callback());
  assert.equal(thread.scrollTop, 25);
});

test("Inbox late reply success for B and failure after exit leave draft, controls, and focus untouched", async (t) => {
  for (const fail of [false, true]) {
    const h = harness(); t.after(h.close);
    await h.loadTickets([ticket("A"), ticket("B")]); await h.select("A");
    const input = h.document.getElementById("inbox-message-input"), send = h.document.getElementById("inbox-send-btn");
    input.value = "send A";
    h.document.getElementById("inbox-reply-form").dispatchEvent(new h.win.Event("submit", { bubbles: true, cancelable: true }));
    const oldReply = h.requests.at(-1);
    if (fail) {
      h.module.onExit(); h.context.view = "overview"; h.context.transition++;
    } else await h.select("B");
    input.value = "draft B"; input.disabled = true; send.disabled = true;
    if (!fail) { input.disabled = false; send.disabled = false; input.focus(); }
    const count = h.requests.length;
    if (fail) oldReply.reject(new Error("late POST failure"));
    else oldReply.resolve(response({ message_id: "a-sent", status: "sent" }));
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(input.value, "draft B");
    assert.equal(input.disabled, fail); assert.equal(send.disabled, fail);
    if (!fail) assert.equal(h.document.activeElement, input);
    assert.equal(h.requests.length, count, "stale reply does not trigger detail or list reads");
    assert.equal(h.toasts.length, 0);
  }
});

test("Inbox language refresh rerenders current copy without fetching and translates pending claim", async (t) => {
  const h = harness(); t.after(h.close);
  await h.loadTickets([ticket("pending", "PENDING_STAFF")], [{ user_id: "staff", nome: "Staff" }]);
  await h.select("pending");
  const before = h.requests.length;
  h.win.qaLanguage = "en"; h.module.refreshLanguage();
  assert.equal(h.requests.length, before, "language rerender does not fetch list or thread");
  assert.match(h.document.getElementById("inbox-detail-actions").textContent, /en:inbox:chat.claim_btn/);
  const claim = h.document.querySelector("#inbox-detail-actions .inbox-action-btn"); claim.click();
  h.win.qaLanguage = "it"; h.module.refreshLanguage();
  const translated = h.document.getElementById("inbox-detail-actions").querySelector(".inbox-action-btn");
  assert.equal(translated.disabled, true); assert.match(translated.textContent, /it:inbox:chat.claim_loading/);
  assert.equal(h.requests.filter((request) => request.url.includes("/api/inbox/claim/")).length, 1);
  h.requests.at(-1).resolve(response({ detail: "claim conflict" }, false));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(h.requests.length, before + 1, "failed claim does not trigger a refresh");
  assert.equal(h.document.querySelector("#inbox-detail-actions .inbox-action-btn").disabled, false);
});

test("Inbox filters, search, counters and escalation warning reflect ticket fields", async (t) => {
  const h = harness(); t.after(h.close);
  const wa = { ...ticket("wa", "CLAIMED"), canale: "whatsapp", last_message_preview: "alpha" };
  const ig = { ...ticket("ig", "PENDING_STAFF"), canale: "instagram", priorita: "alta", escalation_failed: true, last_message_preview: "beta" };
  const resolved = { ...ticket("done", "RESOLVED"), canale: "whatsapp", last_message_preview: "gamma" };
  await h.loadTickets([wa, ig, resolved]);
  assert.equal(h.document.getElementById("inbox-total-count").textContent, "3");
  assert.equal(h.document.getElementById("chip-cnt-all").textContent, "3");
  assert.equal(h.document.getElementById("chip-cnt-wa").textContent, "2");
  assert.equal(h.document.getElementById("chip-cnt-ig").textContent, "1");
  assert.equal(h.document.getElementById("chip-cnt-escalated").textContent, "1");
  assert.match(h.document.getElementById("inbox-list").textContent, /escalation_failed/);
  h.document.querySelector('[data-inbox-channel="instagram"]').click();
  assert.deepEqual([...h.document.querySelectorAll(".inbox-conv-row")].map((row) => row.dataset.ticketId), ["ig"]);
  h.document.querySelector('[data-inbox-channel="all"]').click();
  h.document.querySelector('[data-inbox-status="resolved"]').click();
  assert.deepEqual([...h.document.querySelectorAll(".inbox-conv-row")].map((row) => row.dataset.ticketId), ["done"]);
  h.document.querySelector('[data-inbox-status="all"]').click();
  const search = h.document.getElementById("inbox-search-input"); search.value = "beta"; search.dispatchEvent(new h.win.Event("input", { bubbles: true }));
  assert.deepEqual([...h.document.querySelectorAll(".inbox-conv-row")].map((row) => row.dataset.ticketId), ["ig"]);
});

test("Inbox assignment and resolution use exact action methods and payloads", async (t) => {
  const h = harness(); t.after(h.close);
  await h.loadTickets([ticket("A"), ticket("B")], [{ user_id: "member-1", nome: "Member" }]);
  await h.select("A");
  const assign = h.document.querySelector("#inbox-detail-actions select"); assign.value = "member-1"; assign.dispatchEvent(new h.win.Event("change", { bubbles: true }));
  let request = h.requests.at(-1);
  assert.equal(request.options.method, "POST"); assert.equal(request.url, "/api/inbox/assign/A");
  assert.deepEqual(JSON.parse(request.options.body), { assigned_to: "member-1", expected_version: 1 });
  request.resolve(response({})); await new Promise((resolve) => setImmediate(resolve));
  const resolveButton = [...h.document.querySelectorAll("#inbox-detail-actions button")].find((button) => button.textContent.endsWith("inbox:chat.close_ticket"));
  resolveButton.click(); request = h.requests.at(-1);
  assert.equal(request.options.method, "POST"); assert.equal(request.url, "/api/inbox/resolve/A"); assert.equal(request.options.body, undefined);
  request.resolve(response({})); await new Promise((resolve) => setImmediate(resolve));
});

test("Inbox newest explicit mutation refresh wins over older delayed team JSON", async (t) => {
  const h = harness(); t.after(h.close);
  await h.loadTickets([ticket("A")], [{ user_id: "member-1", nome: "Member" }]); await h.select("A");
  const assign = h.document.querySelector("#inbox-detail-actions select"); assign.value = "member-1"; assign.dispatchEvent(new h.win.Event("change", { bubbles: true }));
  const assignPost = h.requests.at(-1);
  const resolveButton = [...h.document.querySelectorAll("#inbox-detail-actions button")].find((button) => button.textContent.endsWith("inbox:chat.close_ticket"));
  resolveButton.click(); const resolvePost = h.requests.at(-1);
  resolvePost.resolve(response({})); await new Promise((resolve) => setImmediate(resolve));
  const older = h.requests.slice(-2);
  assert.match(older[0].url, /\/tickets\?limit=100$/);
  let finishOldTeam;
  older[0].resolve(response({ tickets: [ticket("stale mutation result")] }));
  older[1].resolve({ ok: true, json: () => new Promise((resolve) => { finishOldTeam = resolve; }) });
  await new Promise((resolve) => setImmediate(resolve));
  assignPost.resolve(response({})); await new Promise((resolve) => setImmediate(resolve));
  const newer = h.requests.slice(-2);
  newer[0].resolve(response({ tickets: [ticket("A", "RESOLVED")] }));
  newer[1].resolve(response({ members: [{ user_id: "member-1", nome: "Member" }] }));
  await new Promise((resolve) => setImmediate(resolve));
  const detail = h.requests.at(-1); assert.match(detail.url, /\/messages\?limit=200$/); detail.resolve(response({ messages: [] }));
  await new Promise((resolve) => setImmediate(resolve));
  finishOldTeam({ members: [{ user_id: "member-1", nome: "Member" }] });
  await new Promise((resolve) => setImmediate(resolve));
  assert.match(h.document.getElementById("inbox-list").textContent, /A/);
  assert.doesNotMatch(h.document.getElementById("inbox-list").textContent, /stale mutation result/);
});

test("Inbox loaded thread retains same-ticket draft across a status-only poll refresh", async (t) => {
  const h = harness(); t.after(h.close);
  await h.loadTickets([ticket("A")]); await h.select("A");
  const input = h.document.getElementById("inbox-message-input"); input.value = "keep while polling"; input.dispatchEvent(new h.win.Event("input", { bubbles: true }));
  h.module.onEnter(); const poll = h.module.load();
  h.requests.at(-2).resolve(response({ tickets: [{ ...ticket("A"), unread_count: 1 }] }));
  h.requests.at(-1).resolve(response({ members: [] }));
  await new Promise((resolve) => setImmediate(resolve));
  const detail = h.requests.at(-1); assert.match(detail.url, /\/messages\?limit=200$/); detail.resolve(response({ messages: [] }));
  await poll;
  assert.equal(input.value, "keep while polling");
});

test("Inbox scope invalidation resets visible search and channel/status chips", async (t) => {
  const h = harness(); t.after(h.close);
  await h.loadTickets([ticket("A"), ticket("B")]);
  const search = h.document.getElementById("inbox-search-input");
  search.value = "private term"; search.dispatchEvent(new h.win.Event("input", { bubbles: true }));
  h.document.querySelector('[data-inbox-channel="whatsapp"]').click();
  h.document.querySelector('[data-inbox-status="human"]').click();
  h.module.invalidate();
  assert.equal(search.value, "");
  assert.ok(h.document.querySelector('[data-inbox-channel="all"]').classList.contains("active"));
  assert.ok(h.document.querySelector('[data-inbox-status="all"]').classList.contains("active"));
  assert.equal(h.document.querySelectorAll("[data-inbox-channel].active").length, 1);
  assert.equal(h.document.querySelectorAll("[data-inbox-status].active").length, 1);
});

test("Inbox ambiguous reply copy follows the active locale", async (t) => {
  const h = harness(); t.after(h.close);
  h.win.qaLanguage = "en";
  await h.loadTickets([ticket("A")]); await h.select("A");
  const input = h.document.getElementById("inbox-message-input"); input.value = "uncertain";
  h.document.getElementById("inbox-reply-form").dispatchEvent(new h.win.Event("submit", { bubbles: true, cancelable: true }));
  h.requests.at(-1).resolve(response({ message_id: "msg-uncertain", status: "sending_ambiguous" }));
  await new Promise((resolve) => setImmediate(resolve));
  assert.ok(h.toasts.some(([message]) => message === "en:inbox:chat.message_status.sending_ambiguous"), "uncertain delivery feedback must be translated");
});
