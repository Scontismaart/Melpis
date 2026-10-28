// Synthetic HTTP fixtures only: never connect this server to an application DB.
const ORG_A = "11111111-1111-4111-8111-111111111111";
const ORG_B = "22222222-2222-4222-8222-222222222222";
const FIXED_TIME = "2026-10-08T10:00:00.000Z";
const profile = { nome_attivita: "Melpis QA sintetica", nome: "Melpis QA sintetica", verticale: "ristorante", timezone: "Europe/Rome", lingua: "it", regole: [], dati_attivita: {} };
const readFixtures = {
  "/api/auth/me": { user_id: "33333333-3333-4333-8333-333333333333", organization_id: ORG_A, email: "owner@dashboard-qa.invalid", ruolo: "owner", aal: "aal2" },
  "/api/auth/mfa": { aal: "aal2", factors: [] },
  "/api/ui/summary": { messaggi_totali: 0, gestiti_da_ai: 0, girati_a_umano: 0, prenotazioni: 0 },
  "/api/dashboard": [],
  "/api/dashboard/prioritari": [],
  "/api/report": {},
  "/api/bookings": [],
  "/api/bookings/semaforo": [],
  "/api/bookings/catalog": { revision: 0, draft: null, published: null, published_revision: null, runtime_enabled: false, organization_sector: "ristorante", capabilities: { can_save: true, can_publish: true } },
  "/api/bookings/catalog/audit": { events: [] },
  "/api/bookings/settings": { capienza: 40, timezone: "Europe/Rome", durata_slot_minuti: 30 },
  "/api/onboarding/profilo": profile,
  "/api/onboarding/verticali": [],
  "/api/impostazioni/profilo": profile,
  "/api/impostazioni/organizzazione": { name: "Melpis QA sintetica", timezone: "Europe/Rome" },
  "/api/documenti/conteggio": { totale: 0, count: 0 },
  "/api/documenti/elenco": [],
  "/api/conoscenza/summary": { totale: 0, faq: 0, documenti: 0, web: 0 },
  "/api/conoscenza/conflitti": { conflitti: [] },
  "/api/conoscenza/business": profile,
  "/api/conoscenza/dati-struttura": {},
  "/api/inbox": { tickets: [], total: 0 },
  "/api/inbox/tickets": { tickets: [], total: 0 },
  "/api/inbox/team": { members: [] },
  "/api/team/members": { members: [], total: 0, max_users: 3, plan: "pro" },
  "/api/team/organizations": { organizations: [{ id: ORG_A, name: "QA A" }, { id: ORG_B, name: "QA B" }] },
  "/api/team/invitations": { invitations: [] },
  "/api/audit": { eventi: [], has_more: false },
  "/api/whatsapp/settings": { connected: false, webhook_active: false },
  "/api/instagram/account": { connected: false },
  "/api/calendar/status": { connected: false },
  "/api/reviews/google/status": { connected: false, operational: false },
  "/api/recensioni": [],
  "/api/v1/integrations/booking/status": { connected: false, mode: "internal" },
  "/api/v1/integrations/airtable/status": { connected: false },
  "/api/v1/integrations/airtable/webhooks/events": { events: [] },
  "/api/billing/subscription": { plan: "pro", status: "trialing", price_monthly_eur: 69 },
};
function reply(method, pathname) {
  if (method === "GET" && Object.hasOwn(readFixtures, pathname)) {
    return { status: 200, body: readFixtures[pathname] };
  }
  if (method === "POST" && pathname === "/api/messaggio") {
    return { status: 200, body: { risposta: "Risposta sintetica QA: nessun modello o servizio esterno invocato.", richiede_umano: false, categoria: "informazioni" } };
  }
  // Unknown endpoints and all other writes fail closed, not an empty-success fallback.
  return { status: 501, body: { detail: "Endpoint non previsto dalla fixture QA" } };
}
module.exports = { ORG_A, ORG_B, FIXED_TIME, reply, readFixtures };
