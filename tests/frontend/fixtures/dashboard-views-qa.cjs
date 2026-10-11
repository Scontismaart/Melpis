// Opt-in, loopback-only view smoke data. No DB, provider, model or credentials.
const { ORG_A, ORG_B, FIXED_TIME, readFixtures } = require("./dashboard-visual-api.cjs");
const { INBOX_CASES, inboxReply } = require("./dashboard-inbox-qa.cjs");
const SCENARIOS = new Set([
  "populated", "empty", "error", "loading", "denied",
  "staff", "mfa-required", "simulator-hostile", "simulator-error", "simulator-timeout",
  "booking-error", "booking-timeout", "settings-populated", "settings-error", "settings-denied",
  "settings-staff", "settings-org-b", "settings-org-switch", "settings-mfa-required", "settings-connected", "ai-settings-populated",
  "ai-settings-error", "ai-settings-denied", "ai-settings-staff", "ai-settings-org-b",
  "ai-settings-mfa-required", "auth-success", "auth-expired", ...INBOX_CASES,
]);
const FAQ_ID = "synthetic-faq-qa";
const DOCUMENT_ID = "synthetic-document-qa";
const WEB_ID = "synthetic-web-qa";
const MEMBER_ID = "synthetic-member-qa";
const INVITATION_ID = "synthetic-invitation-qa";
const TEAM_EMAIL = "collaboratore@dashboard-qa.invalid";
const BOOKING_DATE = "2026-10-08";
const BOOKING_IDS = { pending: "synthetic-booking-pending", confirmed: "synthetic-booking-confirmed" };
const booking = (label, kind, stato = kind === "pending" ? "in_attesa" : "confermata") => ({
  id: `${BOOKING_IDS[kind]}-${label}`,
  organization_id: `synthetic-org-${label}`,
  nome_cliente: `Cliente QA ${label} ${kind === "pending" ? "in attesa" : "confermato"}`,
  telefono: `+39000000000${label === "A" ? "1" : "2"}`,
  data: BOOKING_DATE,
  ora: "20:00",
  coperti: 2,
  note: "Prenotazione sintetica QA",
  stato,
});
const KNOWLEDGE_PATHS = new Set([
  "/api/conoscenza/summary", "/api/conoscenza/conflitti", "/api/conoscenza/dati-struttura",
  "/api/documenti/elenco", "/api/documenti/conteggio",
]);
const faq = (label) => ({ id: FAQ_ID, nome: `FAQ QA ${label}`, tipo: "faq", stato: "pronto", is_active: true, chunk: 1, domanda: `Domanda sintetica ${label}`, risposta: `Risposta sintetica ${label}`, caricato_il: FIXED_TIME });
const documentItem = (label) => ({ id: DOCUMENT_ID, nome: `Documento QA ${label}.txt`, tipo: "documento", stato: "pronto", is_active: true, chunk: 2, caricato_il: FIXED_TIME });
const webItem = (label) => ({ id: WEB_ID, nome: `Pagina QA ${label}`, fonte: "https://example.invalid/qa", tipo: "web", stato: "pronto", is_active: true, chunk: 1, caricato_il: FIXED_TIME });
function forbidden(reason, mfaRequired = false) {
  return { status: 403, body: mfaRequired ? { detail: "MFA required", code: "mfa_required" } : { detail: reason } };
}
function viewReply(method, pathname, scenario, organization = ORG_A, rawBody = null) {
  const [route, query = ""] = pathname.split("?");
  pathname = route;
  if (INBOX_CASES.has(scenario) && pathname.startsWith("/api/inbox/")) return inboxReply(method, pathname, scenario, organization, rawBody);
  if (!SCENARIOS.has(scenario)) return null;
  if (scenario === "auth-expired" && ((method === "GET" && pathname === "/api/auth/me")
    || (method === "POST" && pathname === "/api/auth/refresh"))) {
    return { status: 401, body: { detail: "Sessione QA scaduta" } };
  }
  if (![ORG_A, ORG_B].includes(organization)) return forbidden("Organizzazione QA non autorizzata");
  const label = organization === ORG_B ? "B" : "A";
  const denied = scenario === "denied" || scenario === "staff";
  const mfaRequired = scenario === "mfa-required";
  const member = { user_id: MEMBER_ID, nome: `Membro QA ${label}`, email: TEAM_EMAIL, ruolo: "staff", joined_at: FIXED_TIME };
  const invitation = { id: INVITATION_ID, email: TEAM_EMAIL, ruolo: "staff", stato: "pending", created_at: FIXED_TIME };
  const review = { id: `synthetic-review-${label}`, autore: `Cliente sintetico ${label}`, testo: `Recensione QA ${label}`, bozza_risposta: "Grazie per il feedback sintetico.", fonte: "google", valutazione_stelle: 4, sentiment: "positiva", categoria: "servizio", stato: "bozza_generata", created_at: FIXED_TIME };

  if (method === "GET" && pathname === "/api/auth/me") {
    const auth = { ...readFixtures[pathname], organization_id: scenario.endsWith("org-b") ? ORG_B : organization,
      ruolo: scenario === "staff" || scenario.endsWith("-staff") ? "staff" : "owner" };
    if (mfaRequired || scenario.endsWith("mfa-required")) auth.aal = "aal1";
    return { status: 200, body: auth };
  }
  if (pathname === "/api/impostazioni/organizzazione") {
    const isSettings = scenario.startsWith("settings-");
    if (!isSettings) return null;
    if (scenario.endsWith("denied")) return forbidden("Accesso alle impostazioni QA negato");
    if (scenario.endsWith("error")) return { status: 500, body: { detail: "Errore impostazioni QA sintetico" } };
    if (method === "GET") return { status: 200, body: {
      timezone: "Europe/Rome", timezone_disponibili: ["Europe/Rome", "Europe/London", "America/New_York"],
    } };
    if (method === "PUT") {
      if (scenario.endsWith("staff") || scenario.endsWith("mfa-required")) return forbidden("Permesso impostazioni QA richiesto", scenario.endsWith("mfa-required"));
      return { status: 200, body: { timezone: "Europe/Rome", ok: true } };
    }
    return null;
  }
  if (scenario === "settings-connected" && method === "GET" && pathname === "/api/calendar/status") {
    return { status: 200, body: { connected: true, calendar_id: "synthetic-calendar-qa@dashboard-qa.invalid", sync_enabled: true } };
  }
  if (scenario === "settings-connected" && method === "DELETE" && pathname === "/api/calendar/disconnect") {
    return { status: 200, body: { ok: true, disconnected: true } };
  }
  if (pathname === "/api/billing/subscription") {
    if (!scenario.startsWith("settings-")) return null;
    if (scenario.endsWith("denied") || scenario.endsWith("staff")) return forbidden("Billing QA non disponibile per questo ruolo");
    if (scenario.endsWith("error")) return { status: 500, body: { detail: "Errore billing QA sintetico" } };
    return { status: 200, body: { plan: "pro", subscription_status: "active", current_period_end: FIXED_TIME } };
  }
  if (pathname === "/api/audit") {
    if (!scenario.startsWith("settings-")) return null;
    if (scenario.endsWith("denied") || scenario.endsWith("staff")) return forbidden("Audit QA non disponibile per questo ruolo");
    if (scenario.endsWith("error")) return { status: 500, body: { detail: "Errore audit QA sintetico" } };
    const offset = Number(new URLSearchParams(query).get("offset") || 0);
    return { status: 200, body: { eventi: offset ? [] : [{ id: "synthetic-audit-qa", action: "profile.updated", created_at: FIXED_TIME, details: { name: "Attività QA sintetica" } }], has_more: false } };
  }
  if (pathname === "/api/onboarding/profilo") {
    if (!scenario.startsWith("ai-settings-")) return null;
    if (scenario.endsWith("denied")) return forbidden("Profilo AI QA non disponibile", scenario.endsWith("mfa-required"));
    if (scenario.endsWith("error")) return { status: 500, body: { detail: "Errore profilo AI QA sintetico" } };
    if (method !== "GET" && (scenario.endsWith("staff") || scenario.endsWith("mfa-required"))) {
      return forbidden("Modifica profilo AI QA riservata a owner o manager", scenario.endsWith("mfa-required"));
    }
    const label = scenario.endsWith("org-b") || organization === ORG_B ? "B" : "A";
    const profilo = { verticale: "ristorante", nome_attivita: `Ristorante QA ${label}`, orari: "Lun-Ven 09:00-18:00",
      descrizione: `Attività sintetica QA ${label}`, tono: "professionale_caloroso", servizi: [{ nome: "Servizio QA", prezzo: 10 }],
      regole_escalation: ["Passa a un operatore per richieste complesse"], lingue_supportate: ["it"], lingua_default: "it",
      whatsapp_collegato: false, documenti_importati: false };
    return { status: 200, body: { profilo } };
  }
  if (pathname === "/api/bookings" || pathname.startsWith("/api/bookings/")) {
    if (scenario === "denied") return forbidden("Accesso prenotazioni QA negato");
    if (scenario === "staff" && method !== "GET") return forbidden("Le azioni prenotazioni QA richiedono ruolo owner o manager");
    if (scenario === "staff" && method === "GET" && pathname === "/api/bookings/settings") return forbidden("Impostazioni prenotazioni QA riservate a owner o manager");
    if (scenario === "mfa-required" && method !== "GET") return forbidden("MFA richiesta per questa azione QA", true);
    if (scenario === "booking-error" || scenario === "error") return { status: 500, body: { detail: "Errore prenotazioni QA sintetico" } };
    if (scenario === "booking-timeout") return { status: 504, delayMs: 3000, body: { detail: "Timeout prenotazioni QA sintetico" } };
    const bookings = [booking(label, "pending"), booking(label, "confirmed")];
    if (method === "GET" && pathname === "/api/bookings") {
      return { status: 200, body: scenario === "empty" ? [] : bookings, delayMs: scenario === "loading" ? 3000 : 0 };
    }
    if (method === "GET" && pathname === "/api/bookings/settings") {
      return { status: 200, body: { capienze_orarie: { "20:00": 12 }, fasce_orarie: ["20:00"] } };
    }
    if (method === "GET" && pathname === "/api/bookings/semaforo") {
      const requestedDate = new URLSearchParams(query).get("data");
      if (requestedDate !== BOOKING_DATE) return { status: 200, body: [] };
      return { status: 200, body: scenario === "empty" ? [] : [{ ora: "20:00", coperti_liberi: 8, coperti_massimi: 12, stato: "verde" }] };
    }
    if (method === "PUT" && pathname === "/api/bookings/settings") {
      return { status: 200, body: { capienze_orarie: { "20:00": 12 }, fasce_orarie: ["20:00"] } };
    }
    const actionMatch = pathname.match(/^\/api\/bookings\/(synthetic-booking-(?:pending|confirmed)-[AB])\/(confirm|reject|cancel|mark-no-show|mark-completed)$/);
    if (method === "POST" && actionMatch) {
      const current = bookings.find((item) => item.id === actionMatch[1]);
      if (!current) return null;
      const nextStatus = { confirm: "confermata", reject: "rifiutata", cancel: "cancellata", "mark-no-show": "no_show", "mark-completed": "completata" }[actionMatch[2]];
      return { status: 200, body: { ...current, stato: nextStatus } };
    }
    if ((method === "POST" && pathname === "/api/bookings") || (method === "PUT" && bookings.some((item) => pathname === `/api/bookings/${item.id}`))) {
      return { status: method === "POST" ? 201 : 200, body: { ...bookings[0], nome_cliente: `Cliente QA ${label} salvato` } };
    }
    return null;
  }
  if (method === "GET" && pathname === "/api/reviews/google/status") return { status: 200, body: { connected: true, operational: false, account_name: "Google QA sintetico", location_name: "Sede QA sintetica" } };
  if (method === "GET" && ["/api/dashboard", "/api/dashboard/prioritari", "/api/recensioni"].includes(pathname)) {
    if (scenario === "denied" && pathname === "/api/recensioni") return forbidden("Accesso alle recensioni QA negato");
    if (scenario === "error") return { status: 500, body: { detail: "Errore sintetico QA" } };
    const empty = scenario === "empty";
    const body = pathname === "/api/recensioni" ? { recensioni: empty ? [] : [review] } : empty ? [] : [{ id: review.id, testo_originale: `Attività QA ${label}`, tipo_evento: "recensione", priorita: "alta", gestito_da_ai: false, timestamp: FIXED_TIME, dettagli: { stelle: 4, autore: review.autore, fonte: "google", sentiment: "positiva" } }];
    return { status: 200, body, delayMs: scenario === "loading" ? 3000 : 0 };
  }
  // Only fixed synthetic review action is accepted.
  if (method === "POST" && pathname === `/api/recensioni/${review.id}/approva`) return denied ? forbidden("Azione QA non autorizzata") : { status: 200, body: { stato: "approvata" } };

  if (method === "GET" && KNOWLEDGE_PATHS.has(pathname)) {
    if (scenario === "denied") return forbidden("Accesso Knowledge QA negato");
    if (scenario === "error") return { status: 500, body: { detail: "Errore Knowledge QA sintetico" } };
    if (pathname === "/api/conoscenza/summary") {
      const empty = scenario === "empty";
      const body = empty ? { faq: { totale: 0, attive: 0, errori: 0 }, documenti: { totale: 0, attive: 0, errori: 0 }, web: { totale: 0, attive: 0, errori: 0 }, dati_struttura: { totale: 0 }, conflitti_totali: 0 } : { faq: { totale: 1, attive: 1, errori: 0 }, documenti: { totale: 1, attive: 1, errori: 0 }, web: { totale: 1, attive: 1, errori: 0 }, dati_struttura: { totale: 2 }, conflitti_totali: 0 };
      return { status: 200, body, delayMs: scenario === "loading" ? 3000 : 0 };
    }
    if (pathname === "/api/conoscenza/conflitti") return { status: 200, body: { conflitti: [] } };
    if (pathname === "/api/conoscenza/dati-struttura") return { status: 200, body: { orari: "Lun-Ven 09:00-18:00", servizi: [{ nome: `Servizio QA ${label}`, prezzo: 10 }], updated_at: FIXED_TIME } };
    if (pathname === "/api/documenti/conteggio") return { status: 200, body: { totale: scenario === "empty" ? 0 : 3, count: scenario === "empty" ? 0 : 3 } };
    if (scenario === "empty") return { status: 200, body: { documenti: [] } };
    if (query === "tipo=faq") return { status: 200, body: { documenti: [faq(label)] } };
    if (query === "tipo=web") return { status: 200, body: { documenti: [webItem(label)] } };
    return { status: 200, body: { documenti: [faq(label), documentItem(label), webItem(label)] }, delayMs: scenario === "loading" ? 3000 : 0 };
  }
  // Knowledge writes are exact synthetic actions and IDs. No generic write fallback.
  const knowledgeWrites = new Map([
    [`POST /api/conoscenza/faq`, { status: 201, body: { id: FAQ_ID, stato: "pronto" } }],
    [`PUT /api/conoscenza/faq/${FAQ_ID}`, { status: 200, body: { id: FAQ_ID, stato: "pronto" } }],
    [`DELETE /api/conoscenza/faq/${FAQ_ID}`, { status: 200, body: { ok: true } }],
    ["POST /api/documenti/carica", { status: 201, body: { id: DOCUMENT_ID, detail: "Testo sintetico indicizzato." } }],
    ["POST /api/documenti/carica-file", { status: 201, body: { id: DOCUMENT_ID, detail: "File sintetico indicizzato." } }],
    ["POST /api/conoscenza/web", { status: 201, body: { id: WEB_ID, detail: "Pagina sintetica indicizzata." } }],
    ["PUT /api/conoscenza/dati-struttura", { status: 200, body: { ok: true, updated_at: FIXED_TIME } }],
    [`PATCH /api/documenti/${FAQ_ID}/toggle`, { status: 200, body: { is_active: false } }],
    [`PATCH /api/documenti/${DOCUMENT_ID}/toggle`, { status: 200, body: { is_active: false } }],
    [`PATCH /api/documenti/${WEB_ID}/toggle`, { status: 200, body: { is_active: false } }],
    [`DELETE /api/documenti/${DOCUMENT_ID}`, { status: 200, body: { ok: true } }],
    [`DELETE /api/documenti/${WEB_ID}`, { status: 200, body: { ok: true } }],
    ["POST /api/documenti/chiedi", { status: 200, body: { risposta: `Risposta documentale sintetica QA ${label}.`, fonti: [{ documento: `Documento QA ${label}.txt`, score: 0.98, tipo: "documento", priorita: 3, stato: "indicizzata" }] } }],
  ]);
  if (knowledgeWrites.has(`${method} ${pathname}`)) {
    if (denied) return forbidden("Azione Knowledge QA non autorizzata");
    if (mfaRequired) return forbidden("MFA richiesta per questa azione QA", true);
    return knowledgeWrites.get(`${method} ${pathname}`);
  }

  if (method === "GET" && pathname === "/api/team/organizations") return { status: 200, body: { organizations: [{ id: ORG_A, name: "QA A" }, { id: ORG_B, name: "QA B" }] } };
  if (method === "GET" && pathname === "/api/team/members") {
    if (scenario === "denied") return forbidden("Accesso Team QA negato");
    if (scenario === "error") return { status: 500, body: { detail: "Errore team QA sintetico" } };
    const empty = scenario === "empty";
    return { status: 200, body: { members: empty ? [] : [{ ...member, user_id: `synthetic-owner-${label}`, nome: `Proprietario QA ${label}`, email: `owner-${label.toLowerCase()}@dashboard-qa.invalid`, ruolo: "owner" }, member], total: empty ? 0 : 2, users_limit: 3, can_add_more: true, plan: "growth" }, delayMs: scenario === "loading" ? 3000 : 0 };
  }
  if (method === "GET" && pathname === "/api/team/invitations") return scenario === "empty" ? { status: 200, body: { invitations: [] } } : { status: 200, body: { invitations: [invitation] }, delayMs: scenario === "loading" ? 3000 : 0 };
  const teamWrites = new Map([
    ["POST /api/team/members", { status: 201, body: { id: INVITATION_ID, email: TEAM_EMAIL } }],
    [`PATCH /api/team/members/${MEMBER_ID}`, { status: 200, body: { user_id: MEMBER_ID, ruolo: "manager" } }],
    [`DELETE /api/team/members/${MEMBER_ID}`, { status: 200, body: { ok: true } }],
    [`POST /api/team/invitations/${INVITATION_ID}/resend`, { status: 200, body: { ok: true } }],
    [`DELETE /api/team/invitations/${INVITATION_ID}`, { status: 200, body: { ok: true } }],
  ]);
  if (teamWrites.has(`${method} ${pathname}`)) {
    if (denied) return forbidden("Azione Team QA non autorizzata per staff");
    if (mfaRequired) return forbidden("MFA richiesta per questa azione QA", true);
    return teamWrites.get(`${method} ${pathname}`);
  }

  if (method === "POST" && pathname === "/api/messaggio") {
    if (scenario === "denied") return forbidden("Simulatore QA non autorizzato");
    if (scenario === "simulator-error") return { status: 503, body: { detail: "Simulatore QA temporaneamente non disponibile" } };
    if (scenario === "simulator-timeout") return { status: 504, delayMs: 3000, body: { detail: "Timeout sintetico QA del simulatore" } };
    if (scenario === "simulator-hostile") return { status: 200, body: { risposta: "<img src=x onerror=alert(1)><script>QA_HOSTILE</script>", richiede_umano: true, categoria: "sicurezza" } };
    return { status: 200, delayMs: scenario === "loading" ? 3000 : 0, body: { risposta: "Risposta sintetica QA: nessun modello o servizio esterno invocato.", richiede_umano: false, categoria: "informazioni" } };
  }
  return null;
}
module.exports = { SCENARIOS, viewReply };
