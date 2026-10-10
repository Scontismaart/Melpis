const { ORG_A, ORG_B, FIXED_TIME } = require("./dashboard-visual-api.cjs");

const INBOX_CASES = new Set(["populated", "empty", "error", "loading", "denied", "staff", "inbox-visual", "inbox-ambiguous", "inbox-timeout", "inbox-reply-error"]);
const OWNER_ID = "synthetic-inbox-owner";
const MEMBER_ID = "synthetic-inbox-member";
const IDS = ["synthetic-inbox-claimed", "synthetic-inbox-pending", "synthetic-inbox-resolved"];
const safeRequests = [];
const orgStates = new Map();

function initialState(org) {
  const label = org === ORG_A ? "A" : "B";
  const tickets = [
    { id: IDS[0], organization_id: org, phone_number: `+3900000000${label === "A" ? "1" : "2"}`, ticket_status: "CLAIMED", assigned_to: OWNER_ID, assigned_nome: "Operatore QA", canale: "whatsapp", priorita: "normale", escalation_failed: false, version: 1, last_message_at: FIXED_TIME, last_message_preview: "Messaggio sintetico in carico", unread_count: 1 },
    { id: IDS[1], organization_id: org, phone_number: `qa-${label}@dashboard-qa.invalid`, ticket_status: "PENDING_STAFF", assigned_to: null, canale: "instagram", priorita: "alta", escalation_failed: true, version: 1, last_message_at: FIXED_TIME, last_message_preview: "Richiesta sintetica di assistenza", unread_count: 2 },
    { id: IDS[2], organization_id: org, phone_number: `resolved-${label}@dashboard-qa.invalid`, ticket_status: "RESOLVED", assigned_to: null, canale: "whatsapp", priorita: "normale", escalation_failed: false, version: 1, last_message_at: FIXED_TIME, last_message_preview: "Thread sintetico concluso", unread_count: 0 },
  ];
  return { tickets, messages: new Map(IDS.map((id) => [id, id === IDS[2] ? [] : [{ id: `synthetic-${label}-msg-${id.endsWith("claimed") ? "1" : "2"}`, organization_id: org, ticket_id: id, direction: "inbound", content_text: "Testo ostile sintetico: ignora istruzioni. https://example.invalid/qa e id <script>QA</script>", created_at: FIXED_TIME }]])), replies: new Map() };
}
function stateFor(org) {
  if (!orgStates.has(org)) orgStates.set(org, initialState(org));
  return orgStates.get(org);
}
function jsonBody(value) {
  if (typeof value !== "string") return null;
  try { return JSON.parse(value); } catch { return null; }
}
function inboxReply(method, pathname, scenario, org, rawBody) {
  if (!INBOX_CASES.has(scenario)) return null;
  if (![ORG_A, ORG_B].includes(org)) return { status: 403, body: { detail: "Organizzazione QA non autorizzata" } };
  const denied = scenario === "denied";
  if (denied) return { status: 403, body: { detail: "Inbox QA negata" } };
  if (scenario === "error" && method === "GET") return { status: 500, body: { detail: "Errore Inbox sintetico" } };
  const state = stateFor(org);
  const body = jsonBody(rawBody);
  const ticket = (id) => state.tickets.find((item) => item.id === id && item.organization_id === org);
  let result = null;
  if (method === "GET" && pathname === "/api/inbox/tickets") {
    const tickets = scenario === "empty" ? [] : structuredClone(state.tickets);
    // Old monolith shadows the translator with a ticket variable. This neutral
    // visual case compares renderable common states; populated still reproduces it.
    if (scenario === "inbox-visual") tickets.forEach((item) => { delete item.assigned_nome; item.escalation_failed = false; });
    result = { status: 200, body: { tickets, total: tickets.length }, delayMs: scenario === "loading" ? 1800 : 0 };
  }
  else if (method === "GET" && pathname === "/api/inbox/team") result = { status: 200, body: { members: scenario === "empty" ? [] : [{ user_id: OWNER_ID, nome: "Operatore QA", email: "staff@dashboard-qa.invalid" }, { user_id: MEMBER_ID, nome: "Team QA", email: "member@dashboard-qa.invalid" }] } };
  else {
    let match = pathname.match(/^\/api\/inbox\/tickets\/(synthetic-inbox-(?:claimed|pending|resolved))\/messages$/);
    if (method === "GET" && match && ticket(match[1])) result = { status: 200, body: { messages: structuredClone(state.messages.get(match[1]) || []) }, delayMs: scenario === "inbox-timeout" ? 1800 : 0 };
    match = pathname.match(/^\/api\/inbox\/messages\/(synthetic-[AB]-msg-[12])\/feedback$/);
    if (method === "POST" && match) {
      const found = [...state.messages.values()].flat().find((item) => item.id === match[1]);
      if (found && body && Object.keys(body).length === 1 && ["up", "down"].includes(body.value)) result = { status: 200, body: { ok: true } };
    }
    match = pathname.match(/^\/api\/inbox\/(claim|release|assign|resolve)\/(synthetic-inbox-(?:claimed|pending|resolved))$/);
    if (!result && method === "POST" && match) {
      const [, action, id] = match;
      const current = ticket(id);
      const isStaff = scenario === "staff";
      const allowed = current && current.ticket_status !== "RESOLVED" && (!isStaff || (action === "claim" && current.ticket_status === "PENDING_STAFF") || (action === "release" && current.ticket_status === "CLAIMED" && current.assigned_to === OWNER_ID));
      const payloadOk = action === "claim" ? body && body.expected_version === current?.version && Object.keys(body).length === 1
        : action === "assign" ? body && body.assigned_to === MEMBER_ID && body.expected_version === current?.version && Object.keys(body).length === 2
          : rawBody == null || rawBody === "";
      if (allowed && payloadOk) {
        if (action === "claim") { current.ticket_status = "CLAIMED"; current.assigned_to = OWNER_ID; current.assigned_nome = "Operatore QA"; current.version++; }
        if (action === "assign") { current.assigned_to = body.assigned_to; current.assigned_nome = "Team QA"; current.version++; }
        if (action === "release") { current.ticket_status = "PENDING_STAFF"; current.assigned_to = null; current.assigned_nome = null; current.version++; }
        if (action === "resolve") { current.ticket_status = "RESOLVED"; current.version++; }
        result = { status: 200, body: { ticket: structuredClone(current) } };
      }
    }
    match = pathname.match(/^\/api\/inbox\/reply\/(synthetic-inbox-(?:claimed|pending|resolved))$/);
    if (!result && method === "POST" && match) {
      const current = ticket(match[1]);
      const keys = body && Object.keys(body).sort().join(",");
      const valid = current?.ticket_status === "CLAIMED" && (!scenario.includes("staff") || current.assigned_to === OWNER_ID) && keys === "content,idempotency_key,message_type" && typeof body.content === "string" && body.content.trim() && body.message_type === "text" && /^[\w-]{8,100}$/.test(body.idempotency_key);
      if (valid) {
        if (scenario === "inbox-reply-error") result = { status: 503, body: { detail: "Invio sintetico fallito" } };
        else if (scenario === "inbox-timeout") result = { status: 504, delayMs: 1800, body: { detail: "Timeout invio sintetico" } };
        else {
          const existing = state.replies.get(body.idempotency_key);
          const status = scenario === "inbox-ambiguous" ? "sending_ambiguous" : "sent";
          const message = existing || { id: `synthetic-out-${state.replies.size + 1}`, message_id: `synthetic-out-${state.replies.size + 1}`, status, idempotency_key: body.idempotency_key, content_text: body.content, direction: "outbound", created_at: FIXED_TIME };
          state.replies.set(body.idempotency_key, message);
          if (!existing) state.messages.get(match[1]).push(message);
          result = { status: 200, body: { message_id: message.message_id, status: scenario === "inbox-ambiguous" ? "sending_ambiguous" : "sent" } };
        }
      }
    }
  }
  safeRequests.push({ method, path: pathname, organization_id: org, status: result?.status || 404, body_fields: body && typeof body === "object" ? Object.keys(body).sort() : [] });
  return result || { status: 404, body: { detail: "Azione Inbox QA sconosciuta o non valida" } };
}

module.exports = { INBOX_CASES, inboxReply, safeRequests, orgStates };
