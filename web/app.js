const API_BASE =
  typeof window !== "undefined" && typeof window.MELPIS_API_BASE === "string"
    ? window.MELPIS_API_BASE
    : "";

if (typeof window !== "undefined" && window.MELPIS_API_BASE === undefined) {
  console.warn("[App] window.MELPIS_API_BASE non definita, fallback sicuro su same-origin ('')");
}
const PROFILO_ID = "trattoria_da_mario";
const { escapeHtml: _escapeHtml, sanitize: _sanitize, toast, confirmDestructive: confermaDestructiva, toDateKey: _toDateKey } = window.MelpisDashboardShared;

function localeCorrente() {
  return window.MelpisI18n?.getLocale?.() || "it-IT";
}

/* ============================================================
   AUTENTICAZIONE — BFF (task18)
   ============================================================
   Il frontend NON gestisce token: invia email+password a
   /api/auth/login, il backend (BFF) scambia le credenziali con
   Supabase Auth e salva la sessione in cookie HttpOnly+Secure+
   SameSite=Strict. Il JS manda solo fetch con credentials: i
   cookie viaggiano da soli, mai token in localStorage o header.
   Su 401 si tenta un refresh (/api/auth/refresh) e si riprova
   una volta; se anche il refresh fallisce si torna al login.
   ============================================================ */

let sessione = null; // { email, organization_id, ruolo } | null
let dashboardSessionEpoch = 0;
let dashboardOverviewModule = null;
let dashboardReviewsModule = null;
let dashboardKnowledgeModule = null;
let aggiornaConoscenzaCompleta;
let dashboardTeamModule = null;
let caricaTeam;
let dashboardAiSimulatorModule = null;
let dashboardBookingsModule = null;
let dashboardInboxModule = null;
let aggiornaPrenotazioni;
let aggiornaSemaforo;
let apriBookingModal;

function leggiCookie(nome) {
  return document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(`${nome}=`))
    ?.slice(nome.length + 1) || "";
}

function csrfToken() {
  return decodeURIComponent(leggiCookie("__Host-wa_csrf") || leggiCookie("wa_csrf"));
}

/* ============================================================
   STATO RETE — banner "connessione persa / ripristinata"
   ============================================================ */

let reteInErrore = false;

function mostraBannerRete(testo, online = false, autoHideMs = 0) {
  const netBanner = document.getElementById("net-banner");
  if (!netBanner) return;
  netBanner.textContent = testo;
  netBanner.classList.toggle("online", Boolean(online));
  netBanner.hidden = false;
  if (mostraBannerRete._timer) {
    clearTimeout(mostraBannerRete._timer);
    mostraBannerRete._timer = null;
  }
  if (autoHideMs) {
    mostraBannerRete._timer = setTimeout(() => {
      netBanner.hidden = true;
      mostraBannerRete._timer = null;
    }, autoHideMs);
  }
}

function segnalaErroreRete() {
  reteInErrore = true;
  if (typeof navigator !== "undefined" && !navigator.onLine) {
    mostraBannerRete("Connessione persa — i dati non si aggiornano. Controlla la rete.");
  } else {
    mostraBannerRete("Server irraggiungibile — riprova tra poco.");
  }
}

function segnaReteOk() {
  if (!reteInErrore) return;
  reteInErrore = false;
  mostraBannerRete("Connessione ripristinata.", true, 3000);
}

window.addEventListener("offline", () => {
  mostraBannerRete("Connessione persa — i dati non si aggiornano. Controlla la rete.");
});

window.addEventListener("online", () => {
  if (reteInErrore) segnaReteOk();
  else mostraBannerRete("Connessione ripristinata.", true, 3000);
});

async function tentaRefresh() {
  try {
    return await fetch(`${API_BASE}/api/auth/refresh`, {
      method: "POST",
      headers: csrfToken() ? { "X-CSRF-Token": csrfToken() } : {},
      credentials: "include",
    });
  } catch (err) {
    segnalaErroreRete();
    throw err;
  }
}

async function apiFetch(url, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const safeMethod = ["GET", "HEAD", "OPTIONS"].includes(method);
  const headers = { ...(options.headers || {}) };
  const selectedOrg = localStorage.getItem("melpis_selected_organization");
  if (selectedOrg) headers["X-Organization-Id"] = selectedOrg;
  async function tenta() {
    if (!safeMethod) {
      const token = csrfToken();
      if (token) headers["X-CSRF-Token"] = token;
      else delete headers["X-CSRF-Token"];
    }
    try {
      return await fetch(url, { ...options, headers, credentials: "include" });
    } catch (err) {
      segnalaErroreRete();
      throw err;
    }
  }
  let res = await tenta();
  if (res.status === 401 && !url.includes("/api/auth/")) {
    try {
      const refreshRes = await tentaRefresh();
      if (refreshRes.ok) res = await tenta();
    } catch (err) {
      invalidaSessione();
      vaiAdAccesso();
      throw err;
    }
    if (res.status === 401) {
      invalidaSessione();
      vaiAdAccesso();
    }
  }
  if (res.status === 403) {
    const mfaHeader = res.headers.get("X-MFA-Required");
    let isMfa = mfaHeader === "true";
    if (!isMfa) {
      try {
        const cloned = res.clone();
        const data = await cloned.json();
        if (
          data &&
          typeof data.detail === "string" &&
          (data.detail.includes("MFA") || data.detail.includes("due fattori"))
        ) {
          isMfa = true;
        }
      } catch (_) {}
    }
    if (isMfa) {
      res.mfaRequired = true;
      const mfaMessage = typeof t === "function"
        ? t("settings:mfa.required_toast")
        : "Questa operazione richiede una verifica in due passaggi.";
      toast(
        mfaMessage,
        "warning",
        8000,
        {
          testo: typeof t === "function" ? t("settings:mfa.open_security") : "Apri Sicurezza",
          onClick: () => {
            if (window.MelpisMfa?.openForStepUp) window.MelpisMfa.openForStepUp();
            else apriVistaImpostazioni("sicurezza");
          },
        }
      );
    }
  }
  segnaReteOk();
  return res;
}

// Small bridge for the isolated MFA UI module; session tokens remain in cookies.
window.MelpisAPI = window.MelpisAPI || {};
window.MelpisAPI.fetch = apiFetch;
window.MelpisAPI.base = API_BASE;

const RUOLO_LABEL = {
  owner: "Proprietario",
  manager: "Manager",
  staff: "Staff",
};
const RUOLI_LABELS = RUOLO_LABEL;

function aggiornaBottoneAccesso() {
  const btn = document.getElementById("accesso-btn");
  if (btn) btn.hidden = Boolean(sessione);

  if (!sessione) {
    chiudiSidebarAccountMenu();
    return;
  }
  const email = sessione.email || "utente";
  const iniziale = (email[0] || "U").toUpperCase();

  // Sidebar account trigger
  const sbAvatar = document.getElementById("sidebar-avatar-initial");
  const sbEmail = document.getElementById("sidebar-account-email");
  const sbPlan = document.getElementById("sidebar-account-plan");
  if (sbAvatar) sbAvatar.textContent = iniziale;
  if (sbEmail) sbEmail.textContent = email;
  if (sbPlan && sessione.ruolo) {
    const rLabel = RUOLI_LABELS[sessione.ruolo] || sessione.ruolo;
    sbPlan.textContent = `Ruolo: ${rLabel}`;
  }

  // Restrizioni UI per staff: nascondi gestione billing e Stripe
  const isStaff = sessione.ruolo === "staff";
  const pianoMenuBtn = document.getElementById("sidebar-menu-piano");
  if (pianoMenuBtn) pianoMenuBtn.hidden = isStaff;

  const pianoCatBtns = document.querySelectorAll('[data-settings-cat-btn="piano"], [data-settings-cat-btn="fatturazione"]');
  pianoCatBtns.forEach(b => {
    b.style.display = isStaff ? "none" : "";
  });
}

function toggleSidebarAccountMenu(force) {
  const dropdown = document.getElementById("sidebar-account-dropdown");
  const btn = document.getElementById("sidebar-account-trigger");
  if (!dropdown || !btn) return;
  const apri = typeof force === "boolean" ? force : dropdown.hidden;
  dropdown.hidden = !apri;
  btn.setAttribute("aria-expanded", String(apri));
}

function chiudiSidebarAccountMenu() {
  toggleSidebarAccountMenu(false);
}

document.getElementById("sidebar-account-trigger")?.addEventListener("click", (e) => {
  e.stopPropagation();
  toggleSidebarAccountMenu();
});

document.addEventListener("click", (e) => {
  const trigger = document.getElementById("sidebar-account-trigger");
  const dropdown = document.getElementById("sidebar-account-dropdown");
  if (!dropdown || dropdown.hidden) return;
  if (!trigger?.contains(e.target) && !dropdown.contains(e.target)) {
    chiudiSidebarAccountMenu();
  }
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    chiudiSidebarAccountMenu();
    chiudiPannelloNotifiche();
  }
});

document.getElementById("sidebar-logout-btn")?.addEventListener("click", async () => {
  chiudiSidebarAccountMenu();
  if (await faiLogout()) window.location.href = "/accedi/";
});

document.getElementById("sidebar-menu-piano")?.addEventListener("click", () => {
  chiudiSidebarAccountMenu();
  apriVistaImpostazioni("piano");
});

document.getElementById("sidebar-menu-impostazioni")?.addEventListener("click", (event) => {
  if (!window.MelpisDashboardRouter?.isOrdinaryPrimaryClick(event)) return;
  chiudiSidebarAccountMenu();
});

/* Login su pagina dedicata /accedi/, registrazione su /registrati/
   (pagine standalone). Il modal è stato rimosso. */
function vaiAdAccesso() {
  const next = encodeURIComponent(window.location.pathname + window.location.search);
  window.location.href = `/accedi/?next=${next}`;
}

async function caricaSessione() {
  try {
    const orgHeaders = () => {
      const id = localStorage.getItem("melpis_selected_organization");
      return id ? { "X-Organization-Id": id } : {};
    };
    let res = await fetch(`${API_BASE}/api/auth/me`, { credentials: "include", headers: orgHeaders() });
    if (res.status === 401) {
      try {
        const refRes = await tentaRefresh();
        if (refRes && refRes.ok) {
          res = await fetch(`${API_BASE}/api/auth/me`, { credentials: "include", headers: orgHeaders() });
        }
      } catch { /* ignora */ }
    }
    if (res.status === 403) {
      const choices = await fetch(`${API_BASE}/api/team/organizations`, { credentials: "include" });
      if (choices.ok) {
        const data = await choices.json();
        const first = data.organizations?.[0]?.id;
        if (first) {
          localStorage.setItem("melpis_selected_organization", first);
          res = await fetch(`${API_BASE}/api/auth/me`, { credentials: "include", headers: orgHeaders() });
        }
      }
    }
    if (!res.ok) {
      sessione = null;
      aggiornaBottoneAccesso();
      return false;
    }
    const data = await res.json();
    if (!data || data.source === "anonymous" || !data.user_id || !data.organization_id) {
      sessione = null;
      aggiornaBottoneAccesso();
      return false;
    }
    sessione = data;
    aggiornaBottoneAccesso();
    return true;
  } catch {
    sessione = null;
    aggiornaBottoneAccesso();
    return false;
  }
}

function invalidaSessione() {
  dashboardSessionEpoch += 1;
  dashboardOverviewModule?.invalidate();
  dashboardReviewsModule?.invalidate();
  dashboardKnowledgeModule?.invalidate();
  dashboardTeamModule?.invalidate();
  dashboardAiSimulatorModule?.invalidate();
  dashboardBookingsModule?.invalidate();
  dashboardInboxModule?.invalidate();
  sessione = null;
  document.body.classList.remove("authenticated");
  aggiornaBottoneAccesso();
}

async function faiLogout() {
  try {
    const response = await apiFetch(`${API_BASE}/api/auth/logout`, {
      method: "POST",
    });
    if (!response.ok) throw new Error("Logout rejected");
    invalidaSessione();
    // Notify same-origin tabs without persisting credentials or private data.
    // Storage being unavailable must not turn a completed logout into failure.
    try {
      localStorage.setItem("melpis_auth_logout", `${Date.now()}:${Math.random()}`);
    } catch { /* the current tab is already signed out */ }
    return true;
  } catch {
    toast("Uscita non completata. Riprova: la sessione potrebbe essere ancora attiva.", "error");
    return false;
  }
}

document.getElementById("accesso-btn")?.addEventListener("click", () => {
  vaiAdAccesso();
});

/* ============================================================
   ACCOUNT — Piano e abbonamento (vista dedicata, ex CTA topbar)
   ============================================================ */

const ACCOUNT_PLANS = [
  {
    slug: "starter",
    nome: "Essenziale",
    prezzo: "€29",
    cadenza: "/mese",
    limite: "500 messaggi / mese",
    features: [
      "1 numero WhatsApp Business",
      "1 utente staff (operatore)",
      "Gestione orari e listino servizi",
      "Supporto standard"
    ],
    popolare: false,
  },
  {
    slug: "pro",
    nome: "Crescita",
    prezzo: "€69",
    cadenza: "/mese",
    limite: "2.000 messaggi / mese",
    features: [
      "WhatsApp + Instagram Direct",
      "Fino a 3 utenti staff (operatori)",
      "Recensioni Google, previa verifica di disponibilità dell'account",
      "Sincronizzazione Google Calendar",
      "Supporto prioritario"
    ],
    popolare: true,
  },
  {
    slug: "business",
    nome: "Scala",
    prezzo: "€149",
    cadenza: "/mese",
    limite: "10.000 messaggi / mese",
    features: [
      "Conoscenza AI con documenti",
      "Operatori configurabili",
      "Canali disponibili in base alle integrazioni verificate",
      "Supporto dedicato 1-to-1"
    ],
    popolare: false,
  },
];

function accountStatoPill(stato) {
  const pill = document.getElementById("account-stato");
  if (!pill) return;
  if (stato === "active") { pill.textContent = "Attivo"; pill.className = "account-stato-pill"; }
  else if (stato === "trialing") { pill.textContent = "Prova gratuita"; pill.className = "account-stato-pill stato-trial"; }
  else if (stato === "canceled" || stato === "past_due") { pill.textContent = "In pausa"; pill.className = "account-stato-pill stato-pausa"; }
  else { pill.textContent = "Nessun abbonamento"; pill.className = "account-stato-pill stato-pausa"; }
}

async function caricaAccount() {
  const status = document.getElementById("account-status");
  try {
    const res = await apiFetch(`${API_BASE}/api/billing/subscription`);
    if (!res.ok) throw new Error("stato non disponibile");
    const sub = await res.json();
    const stato = sub.subscription_status || "none";
    accountStatoPill(stato);

    const corrente = ACCOUNT_PLANS.find((p) => p.slug === sub.plan);
    const planName = corrente ? corrente.nome : "Nessun piano";
    const planPrice = corrente ? `${corrente.prezzo}${corrente.cadenza || "/mese"}` : "";

    const nameEl = document.getElementById("account-plan-nome");
    const priceEl = document.getElementById("account-plan-prezzo");
    const sbPlanEl = document.getElementById("sidebar-account-plan");

    if (nameEl) nameEl.textContent = planName;
    if (priceEl) priceEl.textContent = planPrice;
    if (sbPlanEl) sbPlanEl.textContent = corrente ? "Piano " + corrente.nome : "Piano e abbonamento";

    const rinnovo = document.getElementById("account-rinnovo");
    const dataIt = (iso) => {
      const d = new Date(iso);
      if (isNaN(d.getTime())) return "";
      return (typeof MelpisI18n !== "undefined" && typeof MelpisI18n.formatDate === "function")
        ? MelpisI18n.formatDate(d)
        : d.toLocaleDateString(localeCorrente());
    };
    if (rinnovo) {
      if (sub.trial_end) rinnovo.textContent = `Prova gratuita attiva fino al ${dataIt(sub.trial_end)}.`;
      else if (stato === "canceled") rinnovo.textContent = "Abbonamento disattivato: il servizio resta attivo fino al termine del periodo pagato.";
      else if (sub.current_period_end) rinnovo.textContent = `Prossimo rinnovo programmato: ${dataIt(sub.current_period_end)}.`;
      else rinnovo.textContent = "Nessun rinnovo programmato.";
    }

    // Card cambio piano: quella attiva è evidenziata e non cliccabile.
    const wrap = document.getElementById("account-plans");
    if (wrap) {
      wrap.innerHTML = "";
      ACCOUNT_PLANS.forEach((p) => {
        const attuale = p.slug === sub.plan;
        const card = document.createElement("div");
        card.className = "account-plan-card" + (attuale ? " account-plan-attuale" : "") + (p.popolare ? " popolare" : "");

        const popBadge = p.popolare ? '<span class="account-badge-popolare">Consigliato</span>' : "";
        const statusBadge = attuale ? '<span class="account-stato-pill">Piano Attivo</span>' : "";

        const featHtml = p.features.map(f => `
          <li class="account-plan-feat-item">
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" class="account-feat-check"><polyline points="20 6 9 17 4 12"/></svg>
            <span>${_sanitize(f)}</span>
          </li>
        `).join("");

        card.innerHTML = `
          ${popBadge}
          <div class="dash-card-header" style="margin-bottom: 0;">
            <span class="dash-card-title">${_sanitize(p.nome)}</span>
            ${statusBadge}
          </div>
          <div class="account-plan-price-row">
            <span class="account-plan-price-amount">${_sanitize(p.prezzo)}</span>
            <span class="account-plan-price-period">${_sanitize(p.cadenza || "/mese")}</span>
          </div>
          <p class="account-plan-limit">${_sanitize(p.limite)}</p>
          <ul class="account-plan-feat-list">${featHtml}</ul>
        `;

        const actions = document.createElement("div");
        actions.className = "settings-actions";
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = attuale ? "report-refresh" : (p.popolare ? "review-analyze" : "report-refresh");
        btn.textContent = attuale ? "Piano in uso" : (stato === "canceled" ? "Riattiva " + p.nome : "Passa a " + p.nome);
        if (!attuale) {
          btn.addEventListener("click", () => cambiaPiano(p.slug));
        } else {
          btn.disabled = true;
        }
        actions.appendChild(btn);
        card.appendChild(actions);
        wrap.appendChild(card);
      });
    }
    if (status) status.hidden = true;
  } catch (err) {
    console.error("Impossibile caricare l'abbonamento:", err);
    status.hidden = false;
    status.textContent = "Impossibile caricare lo stato dell'abbonamento: torna su questa sezione per riprovare.";
    status.style.color = "var(--red)";
  }
}

const checkoutAttempts = new Map();
async function cambiaPiano(slug) {
  const status = document.getElementById("account-status");
  const previous = checkoutAttempts.get(slug);
  const attempt = previous && Date.now() - previous.created < 25 * 60 * 1000
    ? previous : { key: crypto.randomUUID(), created: Date.now() };
  checkoutAttempts.set(slug, attempt);
  try {
    const res = await apiFetch(`${API_BASE}/api/billing/create-checkout-session`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": attempt.key },
      body: JSON.stringify({
        plan: slug,
        interval: "monthly",
        success_url: window.location.origin + "/app/",
        cancel_url: window.location.origin + "/app/",
      }),
    });
    if (!res.ok) {
      if (res.status === 403) return;
      throw new Error("checkout non disponibile");
    }
    const data = await res.json();
    if (data.url) window.location.href = data.url;
  } catch (e) {
    console.error("Errore cambio piano:", e);
    status.hidden = false;
    status.textContent = "Impossibile avviare il cambio piano. Riprova più tardi.";
    status.style.color = "var(--red)";
  }
}

async function apriPortaleBilling() {
  const status = document.getElementById("account-status");
  try {
    const res = await apiFetch(`${API_BASE}/api/billing/create-portal-session`, { method: "POST" });
    if (!res.ok) {
      if (res.status === 403) return;
      throw new Error("portale non disponibile");
    }
    const data = await res.json();
    if (data.url) window.location.href = data.url;
  } catch (e) {
    console.error("Errore apertura portale billing:", e);
    status.hidden = false;
    status.textContent = "Impossibile aprire il portale Stripe. Riprova più tardi.";
    status.style.color = "var(--red)";
  }
}

document.getElementById("account-portal-btn")?.addEventListener("click", apriPortaleBilling);
document.getElementById("account-cancel-btn")?.addEventListener("click", apriPortaleBilling);

/* ============================================================
   SIDEBAR / NAVIGATION
   ============================================================ */

const navItems = document.querySelectorAll(".nav-item");
const topbarTitle = document.getElementById("topbar-title");
const topbarDate = document.getElementById("topbar-date");
const views = document.querySelectorAll(".view");
let activeDashboardView = null;
let dashboardViewTransition = 0;
let overviewReportRequest = 0;
let dashboardRouter = null;
let aggiornaRiepilogo;
let aggiornaPrioritari;
let avviaPanoramicaPolling;
let fermaPanoramicaPolling;
let aggiornaRecensioni;
function resetDashboardScroll() {
  const scrollingElement = document.scrollingElement || document.documentElement;
  scrollingElement.scrollTop = 0;
  document.body.scrollTop = 0;
}
const NOTIFICATION_STORAGE_KEY = "restaurant-dashboard-notifications-v1";
const notificationBadges = document.querySelectorAll("[data-notification-badge]");
let notificationItems = {
  inbox: 0,
  prenotazioni: 0,
  recensioni: 0,
  conoscenza: 0,
};
let prenotazioniInAttesaCount = 0;

/* Chiavi delle notifiche nate prima della riorganizzazione della sidebar:
   la migrazione preserva lo stato "già letto" (timestamp) di chi le aveva
   viste, altrimenti le notifiche ricomparirebbero come nuove. */
const NOTIF_KEY_MIGRATION = {
  documenti: "conoscenza",
  report: null, // badge rimosso: la voce non esiste più
  panoramica: null, // badge aggregato rimosso (ora Inbox e Recensioni hanno i propri)
  assistente: null,
};

function migraStatoNotifiche(stato) {
  if (!stato || !stato.viste) return stato;
  let migrato = false;
  for (const [vecchia, nuova] of Object.entries(NOTIF_KEY_MIGRATION)) {
    if (!(vecchia in stato.viste)) continue;
    migrato = true;
    if (nuova && !(nuova in stato.viste)) {
      stato.viste[nuova] = stato.viste[vecchia];
    }
    delete stato.viste[vecchia];
  }
  if (migrato) salvaStatoNotifiche(stato);
  return stato;
}

function leggiStatoNotifiche() {
  try {
    const stato = JSON.parse(localStorage.getItem(NOTIFICATION_STORAGE_KEY) || "null") || { inizializzato: false, viste: {} };
    return migraStatoNotifiche(stato);
  } catch {
    return { inizializzato: false, viste: {} };
  }
}

function salvaStatoNotifiche(stato) {
  try { localStorage.setItem(NOTIFICATION_STORAGE_KEY, JSON.stringify(stato)); } catch { /* storage non disponibile */ }
}

function aggiornaBadgeNotifiche(stato) {
  notificationBadges.forEach((badge) => {
    const key = badge.dataset.notificationBadge;
    if (key === "prenotazioni") {
      // Badge persistente per prenotazioni in_attesa: visibile da qualsiasi vista finché non confermate/rifiutate
      const inAttesa = Number(prenotazioniInAttesaCount || 0);
      badge.textContent = inAttesa > 99 ? "99+" : String(inAttesa);
      badge.hidden = inAttesa === 0;
      badge.title = inAttesa > 0 ? `Da confermare: ${inAttesa}` : "";
      return;
    }
    const totale = Number(notificationItems[key] || 0);
    const viste = Number(stato.viste?.[key] || 0);
    const nonViste = Math.max(0, totale - viste);
    badge.textContent = nonViste > 99 ? "99+" : String(nonViste);
    badge.hidden = nonViste === 0;
  });
}

function segnaNotificheViste(viewName) {
  const stato = leggiStatoNotifiche();
  stato.viste = stato.viste || {};
  stato.viste[viewName] = notificationItems[viewName] || 0;
  salvaStatoNotifiche(stato);
  aggiornaBadgeNotifiche(stato);
  aggiornaCampana();
}

async function renderDashboardView(viewName) {
  if (activeDashboardView === "panoramica") dashboardOverviewModule?.onExit();
  if (activeDashboardView === "recensioni") dashboardReviewsModule?.onExit();
  if (activeDashboardView === "conoscenza" || activeDashboardView === "documenti") dashboardKnowledgeModule?.onExit();
  if (activeDashboardView === "team") dashboardTeamModule?.onExit();
  if (activeDashboardView === "assistente") dashboardAiSimulatorModule?.onExit();
  if (activeDashboardView === "prenotazioni") dashboardBookingsModule?.onExit();
  if (activeDashboardView === "inbox") dashboardInboxModule?.onExit();
  const transition = ++dashboardViewTransition;
  activeDashboardView = viewName;
  segnaNotificheViste(viewName);
  chiudiMenuMobile();
  chiudiSidebarAccountMenu();

  navItems.forEach((item) => {
    const active = item.dataset.view === viewName;
    item.classList.toggle("active", active);
    if (active) item.setAttribute("aria-current", "page");
    else item.removeAttribute("aria-current");
  });
  views.forEach((view) => {
    view.classList.toggle("view-hidden", view.dataset.viewPanel !== viewName);
  });
  resetDashboardScroll();

  const titleKeys = {
    panoramica: "title_panoramica",
    inbox: "title_inbox",
    prenotazioni: "title_prenotazioni",
    recensioni: "title_recensioni",
    team: "title_team",
    assistente: "title_assistente",
    conoscenza: "title_conoscenza",
    "configurazione-ai": "title_configurazione_ai",
    impostazioni: "title_impostazioni",
    account: "title_account",
    onboarding: "title_configurazione_ai",
  };
  topbarTitle.dataset.i18n = `dashboard:topbar.${titleKeys[viewName] || "title_panoramica"}`;
  topbarTitle.textContent = t(topbarTitle.dataset.i18n);

  if (viewName === "impostazioni") {
    const routerApi = window.MelpisDashboardRouter;
    const tab = routerApi?.settingsTabFromSearch(window.location.search) || "generale";
    const search = routerApi?.settingsSearchForTab(window.location.search, tab);
    if (search !== undefined && search !== window.location.search) {
      window.history.replaceState(
        window.history.state,
        "",
        `${window.location.pathname}${search}${window.location.hash}`,
      );
    }
    attivaCategoriaImpostazioni(tab);
    aggiornaTitoloImpostazioni(tab);
  }
  if (viewName === "account") caricaAccount();

  if (viewName === "panoramica") {
    dashboardOverviewModule?.onEnter();
    avviaPanoramicaPolling();
    aggiornaRiepilogo();
    aggiornaPrioritari();
    aggiornaReport();
  } else {
    fermaPanoramicaPolling();
  }
  if (viewName === "team") {
    dashboardTeamModule?.onEnter();
    caricaTeam();
  }
  if (viewName === "assistente") dashboardAiSimulatorModule?.onEnter();
  if (viewName === "recensioni") {
    dashboardReviewsModule?.onEnter();
    aggiornaRecensioni();
  }
  if (viewName === "documenti" || viewName === "conoscenza") {
    dashboardKnowledgeModule?.onEnter();
    aggiornaConoscenzaCompleta();
  }
  if (viewName === "configurazione-ai" && typeof caricaConfigurazioneAI === "function") {
    caricaConfigurazioneAI();
  }

  if (viewName === "inbox") {
    dashboardInboxModule?.onEnter();
    dashboardInboxModule?.load();
  }

  if (viewName !== "prenotazioni") return;
  dashboardBookingsModule?.onEnter();
  await dashboardBookingsModule?.load();
}

/* Destinazioni di fallback per chiavi di viste non più presenti nella nav
   (notifiche salvate prima della riorganizzazione, link memorizzati):
   apre la vista contenitore e, se prevista, il tab giusto dentro
   Impostazioni — mai un no-op silenzioso. */
const VIEW_FALLBACK = {
  audit: { view: "impostazioni", tab: "audit" },
  integrazioni: { view: "impostazioni", tab: "whatsapp" },
  account: { view: "impostazioni", tab: "piano" },
  piano: { view: "impostazioni", tab: "piano" },
  fatturazione: { view: "impostazioni", tab: "fatturazione" },
  sicurezza: { view: "impostazioni", tab: "sicurezza" },
  profilo: { view: "impostazioni", tab: "profilo" },
  reviews: { view: "impostazioni", tab: "reviews" },
  documenti: { view: "conoscenza" },
  report: { view: "panoramica" },
  onboarding: { view: "configurazione-ai" },
};

function apriVistaImpostazioni(cat = "generale") {
  const routerApi = window.MelpisDashboardRouter;
  if (!routerApi || !dashboardRouter) return false;
  const tab = routerApi.normalizeSettingsTab(cat);
  const search = routerApi.settingsSearchForTab(window.location.search, tab);
  const navigated = dashboardRouter.navigateTo("impostazioni", {
    search,
    hash: window.location.hash,
    replaceSuffix: true,
  });
  if (!navigated) return false;

  chiudiSidebarAccountMenu();
  return true;
}

function apriView(key) {
  if (key === "impostazioni") {
    apriVistaImpostazioni("generale");
    return true;
  }
  if (window.MelpisDashboardRouter?.pathForView(key)) {
    return dashboardRouter?.navigateTo(key) || false;
  }
  if (key === "account") {
    apriVistaImpostazioni("piano");
    return true;
  }
  const dest = VIEW_FALLBACK[key];
  if (!dest) return false;
  if (dest.view === "impostazioni") {
    apriVistaImpostazioni(dest.tab || "generale");
    return true;
  }
  const container = document.querySelector(`.nav-item[data-view="${dest.view}"]`);
  if (container) apriView(dest.view);
  if (dest.tab) attivaCategoriaImpostazioni(dest.tab);
  return true;
}

/* --- Sotto-sidebar Impostazioni & Account (Pattern a due riquadri) --- */

function attivaCategoriaImpostazioni(cat) {
  const MAPPATURA_LEGACY = {
    generale: "generale",
    integrazioni: "whatsapp",
    audit: "audit",
    account: "piano",
    piano: "piano",
    fatturazione: "fatturazione",
    profilo: "profilo",
    "assistente-regole": "assistente-regole",
    assistente: "assistente-regole",
    sicurezza: "sicurezza",
    whatsapp: "whatsapp",
    instagram: "instagram",
    calendar: "calendar",
    reviews: "reviews",
    webhook: "webhook",
    "booking-pms": "booking-pms",
    booking: "booking-pms",
    pms: "booking-pms",
    airtable: "airtable",
  };
  const targetCat = MAPPATURA_LEGACY[cat] || cat || "generale";

  document.querySelectorAll("[data-settings-cat-btn]").forEach((b) => {
    const on = b.dataset.settingsCatBtn === targetCat;
    b.classList.toggle("active", on);
    b.setAttribute("aria-selected", String(on));
  });

  document.querySelectorAll("[data-settings-panel]").forEach((p) => {
    const on = p.dataset.settingsPanel === targetCat;
    p.hidden = !on;
  });

  // Carica i dati specifici della categoria attiva
  if (targetCat === "generale") {
    if (typeof caricaTimezone === "function") caricaTimezone();
  } else if (["whatsapp", "instagram", "calendar", "reviews", "webhook", "booking-pms", "airtable"].includes(targetCat)) {
    if (typeof caricaIntegrazioni === "function") caricaIntegrazioni();
  } else if (targetCat === "piano" || targetCat === "fatturazione") {
    if (typeof caricaAccount === "function") caricaAccount();
  } else if (targetCat === "audit") {
    if (typeof caricaAudit === "function") caricaAudit();
  } else if (targetCat === "sicurezza") {
    window.MelpisMfa?.loadStatus?.();
  }
}

function aggiornaTitoloImpostazioni(cat) {
  const titleKeys = {
    piano: "title_account",
    fatturazione: "title_account",
    whatsapp: "title_whatsapp",
    instagram: "title_instagram",
    calendar: "title_calendar",
    reviews: "title_reviews_settings",
    "booking-pms": "title_pms",
    airtable: "title_airtable",
  };
  topbarTitle.dataset.i18n = `dashboard:topbar.${titleKeys[cat] || "title_impostazioni"}`;
  topbarTitle.textContent = t(topbarTitle.dataset.i18n);
}

function navigaTabImpostazioni(cat) {
  const routerApi = window.MelpisDashboardRouter;
  const tab = routerApi?.normalizeSettingsTab(cat) || "generale";
  if (!routerApi || !dashboardRouter) return false;
  return dashboardRouter.navigateTo("impostazioni", {
    search: routerApi.settingsSearchForTab(window.location.search, tab),
    hash: window.location.hash,
    replaceSuffix: true,
  });
}

function attivaTabImpostazioni(tab) {
  navigaTabImpostazioni(tab);
}

document.querySelectorAll("[data-settings-cat-btn]").forEach((btn) => {
  btn.addEventListener("click", () => navigaTabImpostazioni(btn.dataset.settingsCatBtn));
});

// Ricerca live nelle impostazioni
const settingsSearchInput = document.getElementById("settings-search-input");
if (settingsSearchInput) {
  settingsSearchInput.addEventListener("input", () => {
    const q = settingsSearchInput.value.trim().toLowerCase();
    const catBtns = document.querySelectorAll("[data-settings-cat-btn]");
    const groups = document.querySelectorAll(".settings-nav-group");
    const emptyMsg = document.getElementById("settings-search-empty");
    let matchCount = 0;

    catBtns.forEach((btn) => {
      const text = (btn.textContent + " " + (btn.dataset.keywords || "")).toLowerCase();
      const match = !q || text.includes(q);
      btn.style.display = match ? "" : "none";
      if (match) matchCount++;
    });

    groups.forEach((grp) => {
      const visibleBtns = grp.querySelectorAll('[data-settings-cat-btn]:not([style*="display: none"])');
      grp.style.display = visibleBtns.length ? "" : "none";
    });

    if (emptyMsg) {
      emptyMsg.hidden = matchCount > 0;
    }
  });
}

async function aggiornaNotifiche() {
  try {
    const summaryResponse = await apiFetch(`${API_BASE}/api/ui/summary`);
    const summary = summaryResponse.ok ? await summaryResponse.json() : {};
    notificationItems = {
      inbox: summary.inbox_attivi || 0,
      prenotazioni: summary.prenotazioni || 0,
      recensioni: summary.recensioni_da_approvare || 0,
      conoscenza: summary.documenti || 0,
    };
    try {
      const bRes = await apiFetch(`${API_BASE}/api/bookings`);
      if (bRes.ok) {
        const list = await bRes.json();
        prenotazioniInAttesaCount = list.filter((p) => statoNormalizzatoPrenotazione(p) === "in_attesa").length;
        const pendingValue = document.getElementById("booking-pending-value");
        if (pendingValue) pendingValue.textContent = String(prenotazioniInAttesaCount);
      }
    } catch { /* ignora errore fetch secondario */ }
    const stato = leggiStatoNotifiche();
    if (!stato.inizializzato) {
      stato.inizializzato = true;
      stato.viste = { ...notificationItems };
      salvaStatoNotifiche(stato);
    }
    aggiornaBadgeNotifiche(stato);
    aggiornaCampana();
  } catch (err) {
    console.error("Impossibile aggiornare le notifiche:", err);
  }
}

function aggiornaDataTopbar() {
  if (!topbarDate) return;
  topbarDate.textContent = (typeof MelpisI18n !== "undefined" && typeof MelpisI18n.formatDate === "function")
    ? MelpisI18n.formatDate(new Date(), { day: "numeric", month: "long", year: "numeric" })
    : new Date().toLocaleDateString(localeCorrente(), { day: "numeric", month: "long", year: "numeric" });
  const topbarWeekday = document.getElementById("topbar-weekday");
  if (topbarWeekday) {
    const rawDay = (typeof MelpisI18n !== "undefined" && typeof MelpisI18n.formatDate === "function")
      ? MelpisI18n.formatDate(new Date(), { weekday: "long" })
      : new Date().toLocaleDateString(localeCorrente(), { weekday: "long" });
    topbarWeekday.textContent = rawDay.charAt(0).toUpperCase() + rawDay.slice(1);
  }
}
aggiornaDataTopbar();

// The authenticated router owns semantic navigation; view rendering remains here.

/* ============================================================
   ONBOARDING
   ============================================================ */

const onboardingState = {
  loaded: false,
  step: 0,
  verticals: [],
  selectedVertical: "ristorante",
  extraRules: [],
  lingue: ["it"],
  linguaDefault: "it",
  lingueDisponibili: ["it", "en", "fr", "de", "es"],
  profileLoaded: false,
};

const LINGUE_DEFAULT_PER_VERTICALE = {
  hotel_bnb: ["it", "en", "fr", "de", "es"],
  ristorante: ["it", "en", "fr", "de", "es"],
  centro_estetico: ["it", "en", "fr", "de"],
  parrucchiere: ["it", "en", "fr"],
  studio_medico_dentista: ["it", "en"],
};

const onboardingEls = {
  steps: document.querySelectorAll("[data-onboarding-step]"),
  pages: document.querySelectorAll("[data-onboarding-page]"),
  progress: document.getElementById("onboarding-progress-bar"),
  verticalGrid: document.getElementById("vertical-grid"),
  name: document.getElementById("onboarding-name"),
  hours: document.getElementById("onboarding-hours"),
  tone: document.getElementById("onboarding-tone"),
  services: document.getElementById("onboarding-services"),
  lingueGrid: document.getElementById("lingue-grid"),
  linguaDefault: document.getElementById("lingua-default"),
  escalationList: document.getElementById("escalation-list"),
  extraRule: document.getElementById("onboarding-extra-rule"),
  addRule: document.getElementById("onboarding-add-rule"),
  previewBtn: document.getElementById("onboarding-preview-btn"),
  previewText: document.getElementById("onboarding-preview-text"),
  whatsapp: document.getElementById("onboarding-whatsapp"),
  docs: document.getElementById("onboarding-docs"),
  docFile: document.getElementById("onboarding-doc-file"),
  uploadDoc: document.getElementById("onboarding-doc-upload"),
  docStatus: document.getElementById("onboarding-doc-status"),
  openDocs: document.getElementById("onboarding-open-docs"),
  testMessage: document.getElementById("onboarding-test-message"),
  testBtn: document.getElementById("onboarding-test-btn"),
  testOutput: document.getElementById("onboarding-test-output"),
  status: document.getElementById("onboarding-status"),
  prev: document.getElementById("onboarding-prev"),
  next: document.getElementById("onboarding-next"),
};

function verticaleCorrente() {
  return onboardingState.verticals.find((v) => v.id === onboardingState.selectedVertical) || onboardingState.verticals[0];
}

function righeDaTextarea(value) {
  return value.split("\n").map((r) => r.trim()).filter(Boolean);
}

function lingueSelezionate() {
  const inputs = [...document.querySelectorAll(".onboarding-lang:checked")];
  return inputs.length ? inputs.map((i) => i.value) : onboardingState.lingue;
}

function renderLingue() {
  if (!onboardingEls.lingueGrid) return;
  onboardingEls.lingueGrid.innerHTML = "";
  onboardingState.lingueDisponibili.forEach((lang) => {
    const label = document.createElement("label");
    label.className = "wizard-check";
    const checked = onboardingState.lingue.includes(lang);
    const locked = lang === "it";
    label.innerHTML = `<input class="onboarding-lang" type="checkbox" value="${_sanitize(lang)}" ${checked ? "checked" : ""} ${locked ? "disabled" : ""}> ${_sanitize(lang.toUpperCase())}`;
    label.querySelector("input").addEventListener("change", () => {
      onboardingState.lingue = lingueSelezionate();
      aggiornaDefaultLingua();
    });
    onboardingEls.lingueGrid.appendChild(label);
  });
  aggiornaDefaultLingua();
}

function aggiornaDefaultLingua() {
  if (!onboardingEls.linguaDefault) return;
  const selezionate = lingueSelezionate();
  onboardingEls.linguaDefault.innerHTML = "";
  selezionate.forEach((lang) => {
    const opt = document.createElement("option");
    opt.value = lang;
    opt.textContent = lang.toUpperCase();
    opt.selected = lang === onboardingState.linguaDefault;
    onboardingEls.linguaDefault.appendChild(opt);
  });
  if (!selezionate.includes(onboardingState.linguaDefault)) {
    onboardingState.linguaDefault = "it";
    [...onboardingEls.linguaDefault.options].forEach((o) => {
      o.selected = o.value === "it";
    });
  }
}

const ONBOARDING_DRAFT_KEY = "melpis_onboarding_bozza";
let dbProfileRecord = null;

function profiloOnboarding() {
  const vertical = verticaleCorrente();
  return {
    verticale: onboardingState.selectedVertical,
    nome_attivita: onboardingEls.name.value.trim(),
    orari: onboardingEls.hours.value.trim(),
    tono: onboardingEls.tone.value.trim() || vertical?.tono || "",
    servizi: righeDaTextarea(onboardingEls.services.value),
    regole_escalation: [...document.querySelectorAll(".onboarding-rule:checked")].map((input) => input.value),
    whatsapp_collegato: Boolean(onboardingEls.whatsapp?.checked),
    documenti_importati: Boolean(onboardingEls.docs?.checked),
    lingue_supportate: lingueSelezionate(),
    lingua_default: onboardingEls.linguaDefault?.value || onboardingState.linguaDefault,
  };
}

function salvaBozzaOnboarding() {
  /* Bozza in localStorage: il wizard resta riprendibile anche se il
     browser si chiude a metà. I dati sono non-sensibili (profilo attività). */
  try {
    const bozza = {
      step: onboardingState.step,
      salvata_at: new Date().toISOString(),
      profilo: profiloOnboarding(),
      extraRules: onboardingState.extraRules || [],
    };
    localStorage.setItem(ONBOARDING_DRAFT_KEY, JSON.stringify(bozza));
    const badge = document.getElementById("onboarding-autosave");
    if (badge) {
      const ora = new Date().toLocaleTimeString(localeCorrente(), { hour: "2-digit", minute: "2-digit" });
      badge.textContent = `Bozza salvata · ${ora}`;
      badge.hidden = false;
    }
  } catch { /* localStorage pieno/bloccato: non blocca il wizard */ }
}

function leggiBozzaOnboarding() {
  try {
    const raw = localStorage.getItem(ONBOARDING_DRAFT_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function pulisciBozzaOnboarding() {
  try {
    localStorage.removeItem(ONBOARDING_DRAFT_KEY);
  } catch { /* storage non disponibile */ }
  const banner = document.getElementById("onboarding-draft-banner");
  if (banner) banner.hidden = true;
  const badge = document.getElementById("onboarding-autosave");
  if (badge) badge.hidden = true;
}

function applicaProfiloAForm(record, extraRules = null) {
  if (!record) return;
  if (record.verticale) onboardingState.selectedVertical = record.verticale;
  if (onboardingEls.name) onboardingEls.name.value = record.nome_attivita || "";
  if (onboardingEls.hours) onboardingEls.hours.value = record.orari || "";
  if (onboardingEls.tone) onboardingEls.tone.value = record.tono || "";
  if (onboardingEls.services) {
    onboardingEls.services.value = Array.isArray(record.servizi)
      ? record.servizi.join("\n")
      : (record.servizi || "");
  }
  if (onboardingEls.whatsapp) onboardingEls.whatsapp.checked = Boolean(record.whatsapp_collegato);
  if (onboardingEls.docs) onboardingEls.docs.checked = Boolean(record.documenti_importati);
  onboardingState.profileLoaded = true;
  if (record.lingue_supportate?.length) onboardingState.lingue = record.lingue_supportate;
  if (record.lingua_default) onboardingState.linguaDefault = record.lingua_default;
  if (extraRules && Array.isArray(extraRules)) {
    onboardingState.extraRules = extraRules;
  }
  if (record.nome_attivita) {
    const bn = document.getElementById("business-name");
    const cbn = document.getElementById("chat-business-name");
    if (bn) bn.textContent = record.nome_attivita;
    if (cbn) cbn.textContent = record.nome_attivita;
  }
}

function renderOnboardingStep() {
  onboardingEls.steps.forEach((step) => {
    step.classList.toggle("active", Number(step.dataset.onboardingStep) === onboardingState.step);
  });
  onboardingEls.pages.forEach((page) => {
    page.hidden = Number(page.dataset.onboardingPage) !== onboardingState.step;
  });
  if (onboardingEls.progress) {
    onboardingEls.progress.style.width = `${((onboardingState.step + 1) / 7) * 100}%`;
  }
  onboardingEls.prev.disabled = onboardingState.step === 0;
  onboardingEls.next.textContent = onboardingState.step === 6 ? "Completa" : "Avanti";
  salvaBozzaOnboarding();
}

function renderVerticals() {
  onboardingEls.verticalGrid.innerHTML = "";
  onboardingState.verticals.forEach((vertical) => {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "vertical-card";
    card.classList.toggle("active", vertical.id === onboardingState.selectedVertical);
    card.innerHTML = `<strong>${_sanitize(vertical.label)}</strong><span>${_sanitize(vertical.servizi.slice(0, 3).join(", "))}</span>`;
    card.addEventListener("click", () => {
      onboardingState.selectedVertical = vertical.id;
      onboardingEls.tone.value = vertical.tono;
      onboardingEls.services.value = vertical.servizi.join("\n");
      onboardingEls.testMessage.value = vertical.esempio;
      onboardingState.extraRules = [];
      if (!onboardingState.profileLoaded) {
        onboardingState.lingue = LINGUE_DEFAULT_PER_VERTICALE[vertical.id] || ["it"];
        renderLingue();
      }
      renderVerticals();
      renderEscalationRules();
      salvaBozzaOnboarding();
    });
    onboardingEls.verticalGrid.appendChild(card);
  });
}

function renderEscalationRules() {
  const vertical = verticaleCorrente();
  const rules = [...(vertical?.escalation || []), ...onboardingState.extraRules];
  onboardingEls.escalationList.innerHTML = "";
  rules.forEach((rule) => {
    const label = document.createElement("label");
    label.className = "wizard-check";
    label.innerHTML = `<input class="onboarding-rule" type="checkbox" value="${_sanitize(rule.replaceAll('"', "&quot;"))}" checked> ${_sanitize(rule)}`;
    const input = label.querySelector("input");
    input?.addEventListener("change", () => salvaBozzaOnboarding());
    onboardingEls.escalationList.appendChild(label);
  });
}

async function caricaProfiloOnboarding() {
  try {
    const res = await apiFetch(`${API_BASE}/api/onboarding/profilo`);
    if (res.status === 401) {
      onboardingEls.status.textContent = 'Serve la config di accesso: clicca "Configura accesso" in alto.';
      onboardingEls.status.style.color = "var(--red)";
      return null;
    }
    if (!res.ok) return null;
    const data = await res.json();
    dbProfileRecord = data.profilo || null;
    return dbProfileRecord;
  } catch {
    return null;
  }
}

async function inizializzaOnboarding() {
  if (onboardingState.loaded) {
    renderOnboardingStep();
    return;
  }
  if (!sessione) {
    vaiAdAccesso();
    return;
  }
  try {
    const res = await apiFetch(`${API_BASE}/api/onboarding/verticali`);
    if (res.status === 401) {
      onboardingEls.status.textContent = "Sessione scaduta: effettua di nuovo il login.";
      onboardingEls.status.style.color = "var(--red)";
      return;
    }
    if (!res.ok) throw new Error("Template verticali non disponibili");
    const data = await res.json();
    onboardingState.verticals = data.verticali || [];
    onboardingState.lingueDisponibili = data.lingue_disponibili || ["it", "en", "fr", "de", "es"];
    const first = onboardingState.verticals[0];
    if (first) {
      onboardingState.selectedVertical = first.id;
      onboardingEls.tone.value = first.tono;
      onboardingEls.services.value = first.servizi.join("\n");
      onboardingEls.testMessage.value = first.esempio;
    }
    const savedProfile = await caricaProfiloOnboarding();
    const bozzaLocale = leggiBozzaOnboarding();

    let usaBozza = false;
    if (bozzaLocale && bozzaLocale.profilo) {
      if (!savedProfile || !savedProfile.updated_at) {
        usaBozza = true;
      } else {
        const draftDate = new Date(bozzaLocale.salvata_at || 0);
        const dbDate = new Date(savedProfile.updated_at || savedProfile.created_at || 0);
        usaBozza = draftDate >= dbDate;
      }
    }

    const banner = document.getElementById("onboarding-draft-banner");
    const timeHint = document.getElementById("onboarding-draft-time-hint");

    if (usaBozza && bozzaLocale.profilo) {
      applicaProfiloAForm(bozzaLocale.profilo, bozzaLocale.extraRules);
      if (typeof bozzaLocale.step === "number" && bozzaLocale.step >= 0 && bozzaLocale.step <= 6) {
        onboardingState.step = bozzaLocale.step;
      }
      if (banner) {
        banner.hidden = false;
        if (timeHint && bozzaLocale.salvata_at) {
          try {
            const d = new Date(bozzaLocale.salvata_at);
            timeHint.textContent = `Bozza salvata il ${d.toLocaleDateString(localeCorrente())} alle ${d.toLocaleTimeString(localeCorrente(), { hour: "2-digit", minute: "2-digit" })}.`;
          } catch {
            timeHint.textContent = "Bozza recuperata in locale.";
          }
        }
      }
    } else if (savedProfile) {
      applicaProfiloAForm(savedProfile);
      if (banner) banner.hidden = true;
    }

    renderVerticals();
    renderEscalationRules();
    renderLingue();
    renderOnboardingStep();
    onboardingState.loaded = true;
  } catch (err) {
    onboardingEls.status.textContent = err.message || "Errore caricamento onboarding.";
    onboardingEls.status.style.color = "var(--red)";
  }
}

async function salvaProfiloOnboarding() {
  const payload = profiloOnboarding();
  const res = await apiFetch(`${API_BASE}/api/onboarding/profilo`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => null);
    throw new Error(err?.detail || "Errore salvataggio profilo");
  }
  const data = await res.json();
  const record = data.profilo;
  dbProfileRecord = record;
  pulisciBozzaOnboarding();
  document.getElementById("business-name").textContent = record.nome_attivita;
  document.getElementById("chat-business-name").textContent = record.nome_attivita;
  return record;
}

function chiudiOnboarding() {
  apriView("configurazione-ai");
}

function _tDash(key, fallback, options = {}) {
  if (typeof t !== "function") return fallback || key;
  const translated = t(`dashboard:${key}`, { ...options, defaultValue: fallback || key });
  return translated === `dashboard:${key}` ? (fallback || key) : translated;
}

document.getElementById("onboarding-chiudi")?.addEventListener("click", chiudiOnboarding);
document.getElementById("onboarding-draft-discard")?.addEventListener("click", () => {
  pulisciBozzaOnboarding();
  onboardingState.step = 0;
  onboardingState.extraRules = [];
  if (dbProfileRecord) {
    applicaProfiloAForm(dbProfileRecord);
  } else {
    const first = onboardingState.verticals[0];
    if (first) {
      onboardingState.selectedVertical = first.id;
      onboardingEls.tone.value = first.tono;
      onboardingEls.services.value = first.servizi.join("\n");
      onboardingEls.testMessage.value = first.esempio;
      onboardingEls.name.value = "";
      onboardingEls.hours.value = "";
      onboardingState.lingue = LINGUE_DEFAULT_PER_VERTICALE[first.id] || ["it"];
    }
  }
  renderVerticals();
  renderEscalationRules();
  renderLingue();
  renderOnboardingStep();
  toast("Bozza locale scartata. Ripartito dal profilo iniziale.", "info");
});

[onboardingEls.name, onboardingEls.hours, onboardingEls.tone, onboardingEls.services].forEach((el) => {
  el?.addEventListener("input", () => salvaBozzaOnboarding());
});
[onboardingEls.whatsapp, onboardingEls.docs].forEach((el) => {
  el?.addEventListener("change", () => salvaBozzaOnboarding());
});


/* Sicurezza account: cambio password/email */

const SECURITY_PASSWORD_MIN = 10;

function securityStatus(el, testo, errore = false) {
  if (!el) return;
  el.textContent = testo;
  el.style.color = errore ? "var(--red)" : "var(--green-deep)";
}

document.getElementById("security-password-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const currentPwd = document.getElementById("security-password-current")?.value || "";
  const pwd = document.getElementById("security-password")?.value || "";
  const conferma = document.getElementById("security-password-confirm")?.value || "";
  const status = document.getElementById("security-password-status");
  const submit = e.target.querySelector(".security-submit");
  if (!currentPwd) {
    securityStatus(status, "Inserisci la password attuale", true);
    return;
  }
  if (pwd.length < SECURITY_PASSWORD_MIN || !/[^A-Za-z0-9]/.test(pwd)) {
    securityStatus(status, `Min ${SECURITY_PASSWORD_MIN} caratteri e almeno un simbolo (es. ! @ #)`, true);
    return;
  }
  if (pwd !== conferma) {
    securityStatus(status, "Le due password non coincidono", true);
    return;
  }
  submit.disabled = true;
  securityStatus(status, "Aggiorno…");
  try {
    const res = await apiFetch(`${API_BASE}/api/auth/password`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: pwd, current_password: currentPwd }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      securityStatus(status, data.detail || "Aggiornamento non riuscito", true);
      return;
    }
    securityStatus(status, data.message || "Password aggiornata");
    const currentInput = document.getElementById("security-password-current");
    if (currentInput) currentInput.value = "";
    document.getElementById("security-password").value = "";
    document.getElementById("security-password-confirm").value = "";
  } catch {
    securityStatus(status, "Errore di connessione", true);
  } finally {
    submit.disabled = false;
  }
});

document.getElementById("security-send-reset-btn")?.addEventListener("click", async () => {
  const resetBtn = document.getElementById("security-send-reset-btn");
  const resetStatus = document.getElementById("security-reset-email-status");
  if (!resetStatus || !resetBtn) return;
  resetBtn.disabled = true;
  securityStatus(resetStatus, "Invio link sicuro in corso…");
  try {
    const res = await apiFetch(`${API_BASE}/api/auth/send-password-reset`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      securityStatus(resetStatus, data.detail || "Impossibile inviare il link di reset", true);
      return;
    }
    securityStatus(resetStatus, data.message || "Link inviato con successo.");
  } catch {
    securityStatus(resetStatus, "Errore di connessione", true);
  } finally {
    resetBtn.disabled = false;
  }
});

document.getElementById("security-email-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const email = document.getElementById("security-email")?.value.trim() || "";
  const status = document.getElementById("security-email-status");
  const submit = e.target.querySelector(".security-submit");
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    securityStatus(status, "Inserisci un indirizzo email valido", true);
    return;
  }
  submit.disabled = true;
  securityStatus(status, "Aggiorno…");
  try {
    const res = await apiFetch(`${API_BASE}/api/auth/email`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      securityStatus(status, data.detail || "Aggiornamento non riuscito", true);
      return;
    }
    securityStatus(status, data.message || "Email aggiornata");
    document.getElementById("security-email").value = "";
  } catch {
    securityStatus(status, "Errore di connessione", true);
  } finally {
    submit.disabled = false;
  }
});

async function generaPreviewOnboarding(targetEl, message) {
  const res = await apiFetch(`${API_BASE}/api/onboarding/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ profilo: profiloOnboarding(), messaggio: message }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => null);
    throw new Error(err?.detail || "Errore preview");
  }
  const data = await res.json();
  targetEl.textContent = data.risposta;
  return data;
}

onboardingEls.steps.forEach((step) => {
  step.addEventListener("click", () => {
    onboardingState.step = Number(step.dataset.onboardingStep);
    renderOnboardingStep();
  });
});

onboardingEls.prev?.addEventListener("click", () => {
  onboardingState.step = Math.max(0, onboardingState.step - 1);
  renderOnboardingStep();
});

onboardingEls.next?.addEventListener("click", async () => {
  if (onboardingState.step < 6) {
    onboardingState.step += 1;
    renderOnboardingStep();
    return;
  }
  onboardingEls.next.disabled = true;
  onboardingEls.status.textContent = "Salvataggio profilo...";
  try {
    await salvaProfiloOnboarding();
    onboardingEls.status.textContent = "Profilo salvato. La chat ora usa questo assistente.";
    onboardingEls.status.style.color = "var(--sage)";
  } catch (err) {
    onboardingEls.status.textContent = err.message;
    onboardingEls.status.style.color = "var(--red)";
  } finally {
    onboardingEls.next.disabled = false;
  }
});

onboardingEls.addRule?.addEventListener("click", () => {
  const rule = onboardingEls.extraRule.value.trim();
  if (!rule) return;
  onboardingState.extraRules.push(rule);
  onboardingEls.extraRule.value = "";
  renderEscalationRules();
  salvaBozzaOnboarding();
});

onboardingEls.previewBtn?.addEventListener("click", async () => {
  onboardingEls.previewBtn.disabled = true;
  onboardingEls.previewText.textContent = "Genero anteprima...";
  try {
    await generaPreviewOnboarding(onboardingEls.previewText, verticaleCorrente()?.esempio || "Siete aperti domani?");
  } catch (err) {
    onboardingEls.previewText.textContent = err.message;
  } finally {
    onboardingEls.previewBtn.disabled = false;
  }
});

onboardingEls.testBtn?.addEventListener("click", async () => {
  onboardingEls.testBtn.disabled = true;
  onboardingEls.testOutput.textContent = "Salvo profilo e provo risposta...";
  try {
    await salvaProfiloOnboarding();
    await generaPreviewOnboarding(
      onboardingEls.testOutput,
      onboardingEls.testMessage.value.trim() || verticaleCorrente()?.esempio || "Siete aperti?"
    );
    onboardingEls.status.textContent = "Wizard completato end-to-end.";
    onboardingEls.status.style.color = "var(--sage)";
  } catch (err) {
    onboardingEls.testOutput.textContent = err.message;
  } finally {
    onboardingEls.testBtn.disabled = false;
  }
});

onboardingEls.openDocs?.addEventListener("click", () => {
  apriView("conoscenza");
});

onboardingEls.uploadDoc?.addEventListener("click", async () => {
  const file = onboardingEls.docFile.files[0];
  if (!file) {
    onboardingEls.docStatus.textContent = "Scegli un file prima di caricare.";
    onboardingEls.docStatus.style.color = "var(--red)";
    return;
  }
  onboardingEls.uploadDoc.disabled = true;
  onboardingEls.docStatus.textContent = "Caricamento e indicizzazione...";
  onboardingEls.docStatus.style.color = "";
  try {
    const form = new FormData();
    form.append("file", file);
    const res = await apiFetch(`${API_BASE}/api/documenti/carica-file`, { method: "POST", body: form });
    if (!res.ok) {
      const err = await res.json().catch(() => null);
      throw new Error(err?.detail || "Errore caricamento");
    }
    const data = await res.json();
    onboardingEls.docStatus.textContent = `Indicizzati ${data.indicizzati} chunk da '${data.nome}'.`;
    onboardingEls.docStatus.style.color = "var(--sage)";
    onboardingEls.docs.checked = true;
  } catch (err) {
    onboardingEls.docStatus.textContent = err.message;
    onboardingEls.docStatus.style.color = "var(--red)";
  } finally {
    onboardingEls.uploadDoc.disabled = false;
  }
});

/* ============================================================
   CHAT
   ============================================================ */


/* ============================================================
   PRENOTAZIONI
   ============================================================ */

function statoNormalizzatoPrenotazione(p) {
  return String(p?.stato || "").toLowerCase().trim().replace(/\s+/g, "_");
}

dashboardBookingsModule = window.MelpisDashboardBookings.create({
  API_BASE, apiFetch, _sanitize, _escapeHtml, _toDateKey, _tDash, localeCorrente,
  toast, confermaDestructiva, getContext: dashboardContextSnapshot, getSession: () => sessione,
  getVertical: () => dbProfileRecord?.verticale || onboardingState?.selectedVertical || "ristorante",
  statoNormalizzatoPrenotazione, exportCsv: scaricaCsvPrenotazioni,
  updatePending: (count) => {
    prenotazioniInAttesaCount = count;
    aggiornaBadgeNotifiche(leggiStatoNotifiche());
    aggiornaCampana();
  },
});
({ aggiornaPrenotazioni, aggiornaSemaforo, apriBookingModal } = dashboardBookingsModule);


/* ============================================================
   REPORT
   ============================================================ */

const reportSection = document.getElementById("report-section");
const reportDate = document.getElementById("report-date");
const reportTotale = document.getElementById("report-totale");
const reportAi = document.getElementById("report-ai");
const reportUmano = document.getElementById("report-umano");
const reportAnalisi = document.getElementById("report-analisi");
const reportSuggestions = document.getElementById("report-suggestions");
const reportSuggestionsList = document.getElementById("report-suggestions-list");
const reportTimestamp = document.getElementById("report-timestamp");
const reportRefresh = document.getElementById("report-refresh");
let reportRefreshHtml = reportRefresh ? reportRefresh.innerHTML : "";
const reportEmptyHint = document.getElementById("report-empty-hint");

/* Export CSV prenotazioni (endpoint /api/report/csv) */

async function scaricaCsvPrenotazioni(da, a, isCurrent = () => true) {
  const params = new URLSearchParams();
  if (da) params.set("da", da);
  if (a) params.set("a", a);
  let res;
  try {
    res = await apiFetch(`${API_BASE}/api/report/csv?${params.toString()}`);
  } catch {
    if (!isCurrent()) return;
    toast("Errore di connessione durante l'export.", "error");
    return;
  }
  if (!isCurrent()) return;
  if (res.status === 403) {
    toast("Export disponibile per proprietario e manager.", "error");
    return;
  }
  if (!res.ok) {
    toast("Export non riuscito. Riprova.", "error");
    return;
  }
  const blob = await res.blob();
  if (!isCurrent()) return;
  const dispo = res.headers.get("Content-Disposition") || "";
  const match = dispo.match(/filename="?([^"]+)"?/);
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = match ? match[1] : "prenotazioni.csv";
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(link.href);
  toast("Export scaricato.", "success");
}

document.getElementById("report-export-csv")?.addEventListener("click", () => scaricaCsvPrenotazioni());

async function aggiornaReport(forza = false) {
  const transition = dashboardViewTransition;
  const view = activeDashboardView;
  const request = ++overviewReportRequest;
  const isCurrent = () => request === overviewReportRequest && transition === dashboardViewTransition && activeDashboardView === view;
  if (reportRefresh && !reportRefresh.disabled) reportRefreshHtml = reportRefresh.innerHTML;
  const origHtml = reportRefreshHtml;
  if (!forza && reportRefresh?.disabled) {
    reportRefresh.disabled = false;
    reportRefresh.innerHTML = origHtml;
  }
  if (forza && reportRefresh) {
    reportRefresh.disabled = true;
    reportRefresh.textContent = "Generazione in corso...";
  }
  try {
    const url = `${API_BASE}/api/report${forza ? "?forza=true" : ""}`;
    const res = await apiFetch(url);
    if (!isCurrent()) return;
    if (!res.ok) {
      if (forza) toast("Impossibile aggiornare il report in questo momento. Riprova più tardi.", "error");
      return;
    }
    const report = await res.json().catch(() => ({}));
    if (!isCurrent()) return;
    if (!report || !report.statistiche) return;
    reportSection.hidden = false;
    if (reportEmptyHint) reportEmptyHint.hidden = true;
    reportDate.textContent = report.statistiche?.periodo || "";
    reportTotale.textContent = report.statistiche?.totale_messaggi ?? "0";
    reportAi.textContent = report.statistiche?.gestiti_da_ai ?? "0";
    reportUmano.textContent = report.statistiche?.girati_a_umano ?? "0";
    reportAnalisi.textContent = report.analisi_testuale || "";
    reportTimestamp.textContent = report.generato_il ? "Generato: " + new Date(report.generato_il).toLocaleTimeString(localeCorrente(), {
      hour: "2-digit", minute: "2-digit",
    }) : "";
    if (report.suggerimenti && report.suggerimenti.length > 0) {
      reportSuggestions.hidden = false;
      reportSuggestionsList.innerHTML = "";
      report.suggerimenti.forEach((s) => {
        const li = document.createElement("li");
        li.classList.add("report-suggestions-item");
        li.textContent = s;
        reportSuggestionsList.appendChild(li);
      });
    } else {
      reportSuggestions.hidden = true;
    }
    if (forza) toast("Report aggiornato con successo!", "success");
  } catch (err) {
    if (!isCurrent()) return;
    console.error("Impossibile caricare il report:", err);
    if (forza) toast("Errore di connessione durante l'aggiornamento del report.", "error");
  } finally {
    if (forza && reportRefresh && isCurrent()) {
      reportRefresh.disabled = false;
      reportRefresh.innerHTML = origHtml;
    }
  }
}

reportRefresh.addEventListener("click", () => aggiornaReport(true));

/* ============================================================
   PANORAMICA — KPI + priorità + attività
   ============================================================ */

function _skeletonList(n = 3) {
  return (
    '<div class="skeleton-list">' +
    '<div class="skeleton skeleton-line-lg"></div>'.repeat(n) +
    "</div>"
  );
}

const ICONS = {
  inbox: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M4 6.5h16v11H4v-11Z"/><path d="M4 8l8 6 8-6" stroke-linecap="round"/></svg>',
  doc: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M7 4h10v16H7V4Z"/><path d="M10 9h4M10 13h4" stroke-linecap="round"/></svg>',
  trend: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M5 20V10M12 20V4M19 20v-7"/></svg>',
  chat: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M4 5.5h16v10H9l-4 4v-4H4v-10Z"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>',
  alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4L2.5 20h19L12 4Z"/><path d="M12 10v4M12 17.5v.01"/></svg>',
};

function _emptyState(icon, titolo, sottotitolo, ctaLabel, ctaAction) {
  const wrap = document.createElement("div");
  wrap.className = "empty-state";
  wrap.innerHTML =
    '<div class="empty-state-icon" aria-hidden="true">' + icon + "</div>" +
    '<span class="empty-state-title">' + _sanitize(titolo) + "</span>" +
    '<span class="empty-state-sub">' + _sanitize(sottotitolo) + "</span>";
  if (ctaLabel) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "review-analyze";
    btn.textContent = ctaLabel;
    btn.addEventListener("click", ctaAction);
    wrap.appendChild(btn);
  }
  return wrap;
}

/* Stato errore con retry: usato al posto del skeleton infinito quando una
   chiamata API fallisce (l'utente deve capire che può riprovare, non che
   la sezione sia vuota). */
function _errorState(messaggio, retryFn) {
  const wrap = document.createElement("div");
  wrap.className = "error-state";
  wrap.setAttribute("role", "alert");
  wrap.innerHTML =
    '<div class="empty-state-icon error" aria-hidden="true">' + ICONS.alert + "</div>" +
    '<span class="empty-state-title">' + _sanitize(_tDash("overview.runtime.error_title", "Qualcosa è andato storto")) + "</span>" +
    '<span class="empty-state-sub">' + _sanitize(messaggio) + "</span>";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "review-analyze";
  btn.textContent = _tDash("overview.runtime.retry", "Riprova");
  btn.addEventListener("click", retryFn);
  wrap.appendChild(btn);
  return wrap;
}

function dashboardContextSnapshot() {
  return {
    userId: sessione?.user_id || null,
    sessionOrganizationId: sessione?.organization_id || null,
    selectedOrganizationId: localStorage.getItem("melpis_selected_organization") || null,
    view: activeDashboardView,
    transition: dashboardViewTransition,
    epoch: dashboardSessionEpoch,
  };
}

dashboardOverviewModule = window.MelpisDashboardOverview.create({
  API_BASE, apiFetch, _sanitize, toast, _tDash, t, localeCorrente,
  _toDateKey, _emptyState, _errorState, _skeletonList, ICONS,
  getContext: dashboardContextSnapshot,
  apriView, apriVistaImpostazioni, apriBookingModal,
  getReviewsApi: () => dashboardReviewsModule,
});
({
  aggiornaRiepilogo, aggiornaPrioritari, avviaPanoramicaPolling,
  fermaPanoramicaPolling,
} = dashboardOverviewModule);

dashboardReviewsModule = window.MelpisDashboardReviews.create({
  API_BASE, apiFetch, _escapeHtml, _sanitize, toast, _tDash, localeCorrente,
  _emptyState, _errorState, _skeletonList, ICONS,
  getContext: dashboardContextSnapshot,
  getOverviewApi: () => dashboardOverviewModule,
  aggiornaNotifiche,
});
({ aggiornaRecensioni } = dashboardReviewsModule);

dashboardAiSimulatorModule = window.MelpisDashboardAiSimulator.create({
  API_BASE, apiFetch, PROFILO_ID,
  getContext: dashboardContextSnapshot,
  aggiornaRiepilogo, aggiornaPrioritari, aggiornaReport,
  aggiornaPrenotazioni, aggiornaSemaforo, aggiornaNotifiche,
});

dashboardKnowledgeModule = window.MelpisDashboardKnowledge.create({
  API_BASE, apiFetch, _escapeHtml, _sanitize, _tDash, localeCorrente,
  confermaDestructiva, getContext: dashboardContextSnapshot,
});
({ aggiornaConoscenzaCompleta } = dashboardKnowledgeModule);

dashboardInboxModule = window.MelpisDashboardInbox.create({
  API_BASE, apiFetch, t, localeCorrente, toast, _emptyState, _errorState,
  _skeletonList, ICONS, apriVistaImpostazioni, getContext: dashboardContextSnapshot,
});




/* ============================================================
   MENU MOBILE — sidebar off-canvas sotto 1100px
   ============================================================ */

const navToggle = document.getElementById("nav-toggle");
const sidebarOverlay = document.getElementById("sidebar-overlay");

function chiudiMenuMobile() {
  const eraAperto = document.body.classList.contains("nav-open");
  document.body.classList.remove("nav-open");
  if (navToggle) navToggle.setAttribute("aria-expanded", "false");
  if (sidebarOverlay) sidebarOverlay.hidden = true;
  // Se il focus era dentro il drawer APERTO, torna al toggle (a11y: niente
  // focus perso su elemento invisibile). Su desktop (drawer mai aperto) il
  // focus non viene toccato.
  if (eraAperto && navToggle) {
    const sidebarAperta = document.querySelector(".sidebar");
    if (sidebarAperta && sidebarAperta.contains(document.activeElement)) {
      navToggle.focus();
    }
  }
}

function apriMenuMobile() {
  document.body.classList.add("nav-open");
  if (navToggle) navToggle.setAttribute("aria-expanded", "true");
  if (sidebarOverlay) sidebarOverlay.hidden = false;
  // Il focus parte dentro il drawer, sul primo elemento navigabile.
  const primo = document.querySelector(".sidebar .nav-item");
  if (primo) primo.focus();
}

navToggle?.addEventListener("click", () => {
  if (document.body.classList.contains("nav-open")) chiudiMenuMobile();
  else apriMenuMobile();
});

sidebarOverlay?.addEventListener("click", chiudiMenuMobile);

/* Focus-trap del drawer (a11y 2.4.3): con il menu mobile aperto il Tab cicla
   solo dentro la sidebar; Tab oltre l'ultimo elemento chiude il drawer e
   riporta il focus al toggle. */
document.addEventListener("keydown", (event) => {
  if (event.key !== "Tab" || !document.body.classList.contains("nav-open")) return;
  const sidebar = document.querySelector(".sidebar");
  if (!sidebar) return;
  const focusabili = sidebar.querySelectorAll(
    "button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])"
  );
  if (!focusabili.length) return;
  const primo = focusabili[0];
  const ultimo = focusabili[focusabili.length - 1];
  if (event.shiftKey && document.activeElement === primo) {
    event.preventDefault();
    ultimo.focus();
  } else if (!event.shiftKey && document.activeElement === ultimo) {
    event.preventDefault();
    chiudiMenuMobile();
  }
});

/* Chiusura dropdown su click fuori (menu utente + notifiche) */

function chiudiMenuUtente() {
  chiudiSidebarAccountMenu();
}

document.addEventListener("click", (event) => {
  if (!(event.target instanceof Element)) return;
  if (!event.target.closest(".notif-wrap")) chiudiPannelloNotifiche();
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  chiudiMenuMobile();
  chiudiSidebarAccountMenu();
  chiudiPannelloNotifiche();
});

/* ============================================================
   CENTRO NOTIFICHE — campana in topbar
   Aggrega i conteggi già calcolati in notificationItems
   (aggiornati ogni 30s da aggiornaNotifiche).
   ============================================================ */

const NOTIF_LABELS = {
  inbox: "Inbox",
  prenotazioni: "Prenotazioni",
  recensioni: "Recensioni",
  conoscenza: "Conoscenza",
};

const notifBell = document.getElementById("notif-bell");
const notifPanel = document.getElementById("notif-panel");
const notifList = document.getElementById("notif-list");
const bellBadge = document.getElementById("bell-badge");

function conteggioNonViste() {
  const stato = leggiStatoNotifiche();
  return Object.keys(NOTIF_LABELS).map((key) => {
    if (key === "prenotazioni") {
      // Nel pannello campana mostra sempre il count reale di in_attesa (azione richiesta)
      return { key, nonViste: Number(prenotazioniInAttesaCount || 0) };
    }
    const totale = Number(notificationItems[key] || 0);
    const viste = Number(stato.viste?.[key] || 0);
    return { key, nonViste: Math.max(0, totale - viste) };
  });
}

function aggiornaCampana() {
  if (!bellBadge) return;
  const totale = conteggioNonViste().reduce((somma, riga) => somma + riga.nonViste, 0);
  bellBadge.textContent = totale > 99 ? "99+" : String(totale);
  bellBadge.hidden = totale === 0;
}

function renderPannelloNotifiche() {
  if (!notifList) return;
  const righe = conteggioNonViste();
  notifList.innerHTML = "";
  if (!righe.some((riga) => riga.nonViste > 0)) {
    const p = document.createElement("p");
    p.className = "notif-empty";
    p.textContent = "Tutto aggiornato: nessuna novità.";
    notifList.appendChild(p);
    return;
  }
  righe.forEach(({ key, nonViste }) => {
    const row = document.createElement("button");
    row.type = "button";
    row.className = `notif-row${nonViste > 0 ? " has-new" : ""}`;
    row.setAttribute("role", "menuitem");
    const label = document.createElement("span");
    label.className = "notif-row-label";
    label.textContent = NOTIF_LABELS[key] || key;
    const count = document.createElement("span");
    count.className = "notif-count";
    count.textContent = String(nonViste);
    row.append(label, count);
    row.addEventListener("click", () => {
      chiudiPannelloNotifiche();
      apriView(key);
    });
    notifList.appendChild(row);
  });
}

function togglePannelloNotifiche(force) {
  if (!notifPanel || !notifBell) return;
  const apri = typeof force === "boolean" ? force : notifPanel.hidden;
  if (apri) renderPannelloNotifiche();
  notifPanel.hidden = !apri;
  notifBell.setAttribute("aria-expanded", String(apri));
}

function chiudiPannelloNotifiche() {
  togglePannelloNotifiche(false);
}

notifBell?.addEventListener("click", () => {
  chiudiMenuUtente();
  togglePannelloNotifiche();
});

/* ============================================================
   AVVIO
   ============================================================ */

// Una pagina /app/ ripristinata dalla back-forward cache potrebbe contenere
// DOM autenticato precedente al logout. Ricarica per riverificare la sessione.
window.addEventListener("storage", (event) => {
  if (event.key === "melpis_auth_logout" && event.newValue && window.location.pathname.startsWith("/app/")) {
    invalidaSessione();
    vaiAdAccesso();
  }
});
window.addEventListener("pagehide", () => {
  dashboardOverviewModule?.onExit();
  dashboardReviewsModule?.onExit();
  dashboardKnowledgeModule?.onExit();
  dashboardTeamModule?.onExit();
  dashboardAiSimulatorModule?.onExit();
  dashboardBookingsModule?.onExit();
  dashboardInboxModule?.onExit();
  document.body.classList.remove("authenticated");
});
window.addEventListener("pageshow", (event) => {
  document.body.dataset.authPageshowPersisted = String(event.persisted);
  if (event.persisted && window.location.pathname.startsWith("/app/")) {
    document.body.classList.remove("authenticated");
    console.debug("[Auth] BFCache restore: private shell hidden; session revalidation required");
    window.location.reload();
  }
});

(async function avvia() {
  const pendingInvite = new URLSearchParams(window.location.hash.slice(1)).get("team-invite")
    || sessionStorage.getItem("melpis_pending_team_invite");
  if (pendingInvite) {
    try {
      // This endpoint authenticates the session without creating a trial organization.
      const inviteSession = await apiFetch(`${API_BASE}/api/team/organizations`);
      if (inviteSession.ok && await accettaInvitoDaLink()) return;
      if (inviteSession.status === 401) {
        sessionStorage.setItem("melpis_pending_team_invite", pendingInvite);
        history.replaceState(null, "", window.location.pathname + window.location.search);
        vaiAdAccesso();
        return;
      }
    } catch { /* Il normale caricamento mostrerà l'errore di sessione. */ }
  }
  const loggato = await caricaSessione();
  if (!loggato) {
    const pendingToken = new URLSearchParams(window.location.hash.slice(1)).get("team-invite");
    if (pendingToken) {
      sessionStorage.setItem("melpis_pending_team_invite", pendingToken);
      history.replaceState(null, "", window.location.pathname + window.location.search);
    }
    vaiAdAccesso();
    return;
  }
  if (await accettaInvitoDaLink()) return;

  dashboardRouter = window.MelpisDashboardRouter.createDashboardRouter({
    window,
    render: renderDashboardView,
  });
  dashboardRouter.start();
  gestisciCalendarRedirect();
  gestisciReviewsRedirect();

  document.body.classList.add("authenticated");
  document.querySelectorAll(".app-shell").forEach((el) => {
    el.style.visibility = "";
  });
  if (sessionStorage.getItem("melpis_benvenuto")) {
    sessionStorage.removeItem("melpis_benvenuto");
    toast("Benvenuto in Melpis: il tuo periodo di prova è attivo.", "success");
  }
  if (typeof window.caricaProfiloImpostazioni === "function") {
    await window.caricaProfiloImpostazioni();
  }
  if (typeof window.aggiornaConteggio === "function") {
    window.aggiornaConteggio();
  }
  aggiornaNotifiche();
  aggiornaCampana();
  setInterval(aggiornaNotifiche, 30000);
  if (typeof navigator !== "undefined" && !navigator.onLine) {
    mostraBannerRete("Connessione persa — i dati non si aggiornano. Controlla la rete.");
  }
})();

/* ============================================================
   RICERCA GLOBALE (client-side su ticket, prenotazioni, documenti)
   ============================================================ */

(function inizializzaRicercaGlobale() {
  const wrap = document.getElementById("global-search");
  const input = document.getElementById("global-search-input");
  const results = document.getElementById("global-search-results");
  const toggle = document.getElementById("global-search-toggle");
  if (!wrap || !input || !results) return;

  let debounceTimer = null;
  let searchToken = 0;

  function chiudi() {
    results.hidden = true;
    results.innerHTML = "";
  }

  function chiudiRicerca() {
    chiudi();
    wrap.classList.remove("is-open");
    input.hidden = true;
    input.blur();
    toggle?.setAttribute("aria-expanded", "false");
  }

  function apriRicerca() {
    wrap.classList.add("is-open");
    input.hidden = false;
    toggle?.setAttribute("aria-expanded", "true");
    input.focus();
  }

  toggle?.addEventListener("click", () => {
    if (wrap.classList.contains("is-open")) {
      chiudiRicerca();
    } else {
      apriRicerca();
    }
  });

  function apriCon(html) {
    results.innerHTML = html;
    results.hidden = false;
  }

  function riga(gruppo, titolo, sub, view, tab) {
    return `<button type="button" class="gs-row" data-view="${view}"${tab ? ` data-tab="${tab}"` : ""}>
      <span class="gs-gruppo">${gruppo}</span>
      <span class="gs-main"><strong>${_sanitize(titolo)}</strong><span>${_sanitize(sub || "")}</span></span>
    </button>`;
  }

  /* Navigazione statica "Vai a": la ricerca non indicizza solo dati, deve
     risolvere anche le destinazioni del menu (es. "audit" → Impostazioni ›
     Audit). DEBITO TECNICO NOTO: la lista è statica — se aggiungi un tab o
     una vista, aggiorna questa mappa (nessun modo automatico per rilevarlo). */
  const VAI_A = [
    { q: ["audit", "log", "registro", "storico azioni"], gruppo: "Gestione", titolo: "Audit", sub: "Impostazioni › Audit", view: "impostazioni", tab: "audit" },
    { q: ["integrazioni", "whatsapp", "instagram", "webhook", "collega", "canali"], gruppo: "Gestione", titolo: "Integrazioni", sub: "Impostazioni › Integrazioni", view: "impostazioni", tab: "integrazioni" },
    { q: ["fuso", "timezone", "password", "email account"], gruppo: "Gestione", titolo: "Impostazioni generali", sub: "Gestione › Generale", view: "impostazioni", tab: "generale" },
    { q: ["fattur", "abbonament", "piano", "rinnovo", "pagament", "upgrade", "downgrade", "cancellazion", "prezz"], gruppo: "Account", titolo: "Piano e abbonamento", sub: "Account", view: "account" },
    { q: ["menu", "conoscenza", "allergeni", "carta dei vini", "documenti", "pdf", "knowledge"], gruppo: "Assistente", titolo: "Conoscenza", sub: "Assistente › Conoscenza", view: "conoscenza" },
    { q: ["configurazione", "personalità", "regole", "tono", "lingue", "istruzioni", "identità", "orari", "escalation"], gruppo: "Assistente", titolo: "Configurazione AI", sub: "Assistente › Configurazione AI", view: "configurazione-ai" },
  ];

  async function eseguiRicerca(q) {
    const token = ++searchToken;
    const ql = q.toLowerCase();

    const [tickRes, bookRes, docRes] = await Promise.allSettled([
      apiFetch(`${API_BASE}/api/inbox/tickets?limit=100`),
      apiFetch(`${API_BASE}/api/bookings`),
      apiFetch(`${API_BASE}/api/documenti/elenco`),
    ]);

    if (token !== searchToken) return; // richiesta superata

    const blocchi = [];

    if (tickRes.status === "fulfilled" && tickRes.value.ok) {
      const tickets = (await tickRes.value.json()).tickets || [];
      const hits = tickets.filter((t) =>
        [t.phone_number, t.last_message_preview, t.ticket_status]
          .some((v) => (v || "").toLowerCase().includes(ql))
      ).slice(0, 5);
      if (hits.length) {
        blocchi.push(`<span class="gs-gruppo-titolo">Ticket</span>` + hits.map((t) =>
          riga("Inbox", t.phone_number || "Cliente", t.last_message_preview || t.ticket_status, "inbox")
        ).join(""));
      }
    }

    if (bookRes.status === "fulfilled" && bookRes.value.ok) {
      const prens = await bookRes.value.json();
      const hits = (Array.isArray(prens) ? prens : []).filter((p) =>
        [p.nome_cliente, p.data, p.stato, p.telefono]
          .some((v) => (v || "").toLowerCase().includes(ql))
      ).slice(0, 5);
      if (hits.length) {
        blocchi.push(`<span class="gs-gruppo-titolo">Prenotazioni</span>` + hits.map((p) =>
          riga("Prenotazioni", p.nome_cliente || "Cliente", `${p.data || ""} ${String(p.ora || "").slice(0, 5)} · ${p.stato || ""}`, "prenotazioni")
        ).join(""));
      }
    }

    if (docRes.status === "fulfilled" && docRes.value.ok) {
      const docs = (await docRes.value.json()).documenti || [];
      const hits = docs.filter((d) => (d.nome || "").toLowerCase().includes(ql)).slice(0, 5);
      if (hits.length) {
        blocchi.push(`<span class="gs-gruppo-titolo">Documenti</span>` + hits.map((d) =>
          riga("Documenti", d.nome, `${d.chunk} parti`, "documenti")
        ).join(""));
      }
    }

    if (token !== searchToken) return;

    // Navigazione "Vai a": le viste si suggeriscono sempre (in fondo),
    // anche quando i risultati dati sono vuoti.
    const vaiHits = VAI_A.filter((v) => v.q.some((k) => ql.includes(k) || k.includes(ql)) || ql.length >= 3 && v.titolo.toLowerCase().includes(ql));
    if (vaiHits.length) {
      blocchi.push(
        `<span class="gs-gruppo-titolo">Vai a</span>` +
        vaiHits.slice(0, 4).map((v) => riga(v.gruppo, v.titolo, v.sub, v.view, v.tab)).join("")
      );
    }

    if (!blocchi.length) {
      apriCon(`<p class="gs-vuoto">Nessun risultato per "${_sanitize(q)}"</p>`);
      return;
    }
    apriCon(blocchi.join(""));
  }

  input.addEventListener("input", () => {
    const q = input.value.trim();
    clearTimeout(debounceTimer);
    if (q.length < 2) { chiudi(); return; }
    debounceTimer = setTimeout(() => eseguiRicerca(q), 250);
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") chiudiRicerca();
  });

  results.addEventListener("click", (e) => {
    const row = e.target.closest(".gs-row");
    if (!row) return;
    const view = row.dataset.view;
    chiudiRicerca();
    input.value = "";
    if (view === "impostazioni") {
      apriVistaImpostazioni(row.dataset.tab || "generale");
    } else {
      apriView(view);
      if (row.dataset.tab) attivaCategoriaImpostazioni(row.dataset.tab);
    }
  });

  document.addEventListener("click", (e) => {
    if (!wrap.contains(e.target)) chiudiRicerca();
  });

  document.addEventListener("keydown", (e) => {
    if (e.key !== "/" || e.defaultPrevented) return;
    const t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
    e.preventDefault();
    apriRicerca();
  });
})();

/* ============================================================
   TEMA CHIARO/SCURO (grigio antracite, mai nero puro)
   ============================================================ */

(function inizializzaTema() {
  const KEY = "melpis_theme";
  const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");

  function risolviTema(preferenza) {
    if (preferenza === "light") return "light";
    if (preferenza === "dark") return "dark";
    return mediaQuery.matches ? "dark" : "light";
  }

  function applica(preferenza) {
    const effettivo = risolviTema(preferenza);
    document.documentElement.dataset.theme = effettivo;

    const btns = document.querySelectorAll("#theme-segmented-control .theme-seg-btn");
    btns.forEach((btn) => {
      const val = btn.getAttribute("data-theme-val");
      const isActive = val === preferenza;
      btn.classList.toggle("active", isActive);
      btn.setAttribute("aria-checked", isActive ? "true" : "false");
    });
  }

  function impostaTema(nuovaPreferenza) {
    localStorage.setItem(KEY, nuovaPreferenza);
    applica(nuovaPreferenza);
  }

  const salvato = localStorage.getItem(KEY) || "dark";
  applica(salvato);

  mediaQuery.addEventListener("change", () => {
    if ((localStorage.getItem(KEY) || "dark") === "system") {
      applica("system");
    }
  });

  document.querySelectorAll("#theme-segmented-control .theme-seg-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const val = btn.getAttribute("data-theme-val");
      if (val) impostaTema(val);
    });
  });
})();

/* ============================================================
   IMPOSTAZIONI — fuso orario organizzazione
   ============================================================ */

let caricaTimezone;

(function inizializzaTimezone() {
  const select = document.getElementById("settings-timezone");
  const saveBtn = document.getElementById("settings-timezone-save");
  const status = document.getElementById("settings-timezone-status");
  if (!select || !saveBtn) return;
  let caricato = false;
  saveBtn.disabled = true;

  async function carica() {
    if (status) {
      status.textContent = t("settings:timezone_section.load_loading");
      status.style.color = "";
    }
    try {
      const res = await apiFetch(`${API_BASE}/api/impostazioni/organizzazione`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (caricato) return;
      caricato = true;
      select.innerHTML = "";
      (data.timezone_disponibili || []).forEach((tz) => {
        const opt = document.createElement("option");
        opt.value = tz;
        opt.textContent = tz;
        if (tz === data.timezone) opt.selected = true;
        select.appendChild(opt);
      });
      if (![...select.options].some((o) => o.value === data.timezone)) {
        const opt = document.createElement("option");
        opt.value = data.timezone;
        opt.textContent = data.timezone;
        opt.selected = true;
        select.appendChild(opt);
      }
      saveBtn.disabled = false;
      if (status) status.textContent = "";
    } catch {
      saveBtn.disabled = true;
      if (status) {
        status.textContent = t("settings:timezone_section.load_error");
        status.style.color = "var(--red)";
      }
    }
  }

  saveBtn.addEventListener("click", async () => {
    saveBtn.disabled = true;
    if (status) { status.textContent = t("settings:timezone_section.save_loading"); status.style.color = ""; }
    try {
      const res = await apiFetch(`${API_BASE}/api/impostazioni/organizzazione`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ timezone: select.value }),
      });
      if (!res.ok) {
        if (status) { status.textContent = t("settings:timezone_section.save_error"); status.style.color = "var(--red)"; }
        return;
      }
      if (status) securityStatus(status, t("settings:timezone_section.save_success"));
    } catch {
      if (status) { status.textContent = t("settings:timezone_section.connection_error"); status.style.color = "var(--red)"; }
    } finally {
      saveBtn.disabled = !caricato;
    }
  });

  caricaTimezone = carica;
})();

/* ============================================================
   INTERNAZIONALIZZAZIONE — Selettori lingua Dashboard
   ============================================================ */

(function inizializzaLinguaDashboard() {
  const sidebarSelect = document.getElementById("sidebar-lang-select");
  const settingsSelect = document.getElementById("settings-lang-select");

  function syncSelects(lang) {
    if (sidebarSelect && sidebarSelect.value !== lang) sidebarSelect.value = lang;
    if (settingsSelect && settingsSelect.value !== lang) settingsSelect.value = lang;
  }

  // Inizializza lingua corrente
  if (typeof MelpisI18n !== "undefined" && typeof MelpisI18n.getLanguage === "function") {
    syncSelects(MelpisI18n.getLanguage());
  }

  window.addEventListener("melpis:lang-changed", (e) => {
    if (e && e.detail && e.detail.language) {
      syncSelects(e.detail.language);
    }
    if (typeof aggiornaDataTopbar === "function") {
      aggiornaDataTopbar();
    }
    dashboardInboxModule?.refreshLanguage();
    if (typeof toast === "function") {
      toast(typeof t === "function" ? t("dashboard.toast.saved") : "Lingua aggiornata", "success");
    }
  });
})();

/* ============================================================
   CONFIGURAZIONE AI — Identità, Tono, Multilingua e Regole
   ============================================================ */

let caricaConfigurazioneAI;

(function inizializzaConfigurazioneAI() {
  const form = document.getElementById("ai-config-form");
  const saveBtn = document.getElementById("ai-cfg-save-btn");
  const saveStatus = document.getElementById("ai-cfg-save-status");

  const nomeInput = document.getElementById("ai-cfg-nome");
  const vertSelect = document.getElementById("ai-cfg-verticale");
  const descTextarea = document.getElementById("ai-cfg-descrizione");
  const orariTextarea = document.getElementById("ai-cfg-orari");

  const tonoSelect = document.getElementById("ai-cfg-tono-select");
  const tonoCustom = document.getElementById("ai-cfg-tono-custom");
  const defaultLinguaSelect = document.getElementById("ai-cfg-lingua-default");
  const addRuleInput = document.getElementById("ai-cfg-new-rule-input");
  const addRuleBtn = document.getElementById("ai-cfg-add-rule-btn");
  const rulesContainer = document.getElementById("ai-cfg-rules-list");

  let currentRules = [];

  const VERTICALI_VALIDI = ["ristorante", "parrucchiere", "hotel_bnb", "centro_estetico", "studio_medico_dentista"];

  function renderRules() {
    if (!rulesContainer) return;
    rulesContainer.innerHTML = "";
    if (!currentRules.length) {
      const empty = document.createElement("p");
      empty.className = "settings-help-sm";
      empty.textContent = t("settings:ai_configuration.rules_empty");
      rulesContainer.appendChild(empty);
      return;
    }
    currentRules.forEach((rule, idx) => {
      const item = document.createElement("div");
      item.className = "settings-rule-item";

      const label = document.createElement("label");
      label.className = "settings-rule-label";
      label.innerHTML = `<input type="checkbox" class="ai-cfg-rule-checkbox" data-rule-index="${idx}" checked> <span>${_sanitize(rule)}</span>`;

      const delBtn = document.createElement("button");
      delBtn.type = "button";
      delBtn.className = "settings-rule-remove-btn";
      delBtn.title = t("settings:ai_configuration.remove_rule");
      delBtn.innerHTML = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg> <span>' + _escapeHtml(t("settings:ai_configuration.remove_rule")) + '</span>';
      delBtn.addEventListener("click", () => {
        currentRules.splice(idx, 1);
        renderRules();
      });

      item.appendChild(label);
      item.appendChild(delBtn);
      rulesContainer.appendChild(item);
    });
  }

  function aggiornaSelectLinguaDefault(linguaScelta = null) {
    if (!defaultLinguaSelect) return;
    const currentVal = linguaScelta || defaultLinguaSelect.value || "it";
    const selezionate = ["it", ...Array.from(document.querySelectorAll(".ai-cfg-lang-opt:checked")).map((cb) => cb.value)];

    const LABELS = {
      it: "Italiano (it)",
      en: "English (en)",
      es: "Español (es)",
      fr: "Français (fr)",
      de: "Deutsch (de)",
    };

    defaultLinguaSelect.innerHTML = "";
    selezionate.forEach((code) => {
      const opt = document.createElement("option");
      opt.value = code;
      opt.textContent = LABELS[code] || code.toUpperCase();
      opt.selected = code === currentVal;
      defaultLinguaSelect.appendChild(opt);
    });

    if (!selezionate.includes(currentVal)) {
      defaultLinguaSelect.value = "it";
    }
  }

  document.querySelectorAll(".ai-cfg-lang-opt").forEach((cb) => {
    cb.addEventListener("change", () => aggiornaSelectLinguaDefault());
  });

  addRuleBtn?.addEventListener("click", () => {
    if (!addRuleInput) return;
    const text = addRuleInput.value.trim();
    if (!text) return;
    currentRules.push(text);
    addRuleInput.value = "";
    renderRules();
  });

  addRuleInput?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      addRuleBtn?.click();
    }
  });

  async function carica() {
    let caricamentoRiuscito = false;
    // A new visit always starts with an empty form so a failed or partial response
    // cannot leave values from a previously loaded profile visible.
    if (saveStatus) {
      saveStatus.textContent = t("settings:ai_configuration.load_loading");
      saveStatus.style.color = "";
    }
    if (saveBtn) saveBtn.disabled = true;
    dbProfileRecord = {};
    if (nomeInput) nomeInput.value = "";
    if (vertSelect) vertSelect.selectedIndex = 0;
    if (descTextarea) descTextarea.value = "";
    if (orariTextarea) orariTextarea.value = "";
    if (tonoSelect) tonoSelect.selectedIndex = 0;
    if (tonoCustom) tonoCustom.value = "";
    document.querySelectorAll(".ai-cfg-lang-opt").forEach((cb) => { cb.checked = false; });
    aggiornaSelectLinguaDefault("it");
    currentRules = [];
    renderRules();

    try {
      const res = await apiFetch(`${API_BASE}/api/onboarding/profilo`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const prof = data.profilo || {};
      dbProfileRecord = prof;

      const businessName = prof.nome_attivita || "La tua attività";
      const bName = document.getElementById("business-name");
      if (bName) bName.textContent = businessName;
      const cName = document.getElementById("chat-business-name");
      if (cName) cName.textContent = businessName;

      // Identità
      if (nomeInput) nomeInput.value = prof.nome_attivita || "";
      if (vertSelect && VERTICALI_VALIDI.includes(prof.verticale)) vertSelect.value = prof.verticale;
      if (descTextarea) descTextarea.value = prof.descrizione || "";
      if (orariTextarea && prof.orari) {
        orariTextarea.value = prof.orari;
        const kbOrari = document.getElementById("kb-orari-input");
        if (kbOrari && !kbOrari.value) kbOrari.value = prof.orari;
      }

      // Tono
      if (tonoSelect && prof.tono) {
        const presets = ["professionale_caloroso", "informale_amichevole", "formale_elegante"];
        if (presets.includes(prof.tono)) {
          tonoSelect.value = prof.tono;
          if (tonoCustom) tonoCustom.value = "";
        } else {
          tonoSelect.value = "professionale_caloroso";
          if (tonoCustom) tonoCustom.value = prof.tono;
        }
      }

      // Multilingua
      const supportate = Array.isArray(prof.lingue_supportate) ? prof.lingue_supportate : [];
      document.querySelectorAll(".ai-cfg-lang-opt").forEach((cb) => {
        cb.checked = supportate.includes(cb.value);
      });
      aggiornaSelectLinguaDefault(prof.lingua_default || "it");

      // Regole escalation
      if (Array.isArray(prof.regole_escalation)) {
        currentRules = [...prof.regole_escalation];
      } else {
        currentRules = [];
      }
      renderRules();
      caricamentoRiuscito = true;
      if (saveStatus) saveStatus.textContent = "";
    } catch (err) {
      console.error("Impossibile caricare la configurazione AI:", err);
      if (saveStatus) {
        saveStatus.textContent = t("settings:ai_configuration.load_error");
        saveStatus.style.color = "var(--red)";
      }
    } finally {
      if (saveBtn) saveBtn.disabled = !caricamentoRiuscito;
    }
  }

  function raccogliPayload() {
    const nome = (nomeInput?.value || "").trim();
    const verticaleSelezionato = VERTICALI_VALIDI.includes(vertSelect?.value)
      ? vertSelect.value
      : (VERTICALI_VALIDI.includes(dbProfileRecord?.verticale) ? dbProfileRecord.verticale : "ristorante");
    const descrizione = (descTextarea?.value || "").trim();
    const orari = (orariTextarea?.value || "").trim() || dbProfileRecord?.orari || "";

    let tono = tonoSelect ? tonoSelect.value : (dbProfileRecord?.tono || "professionale_caloroso");
    if (tonoCustom && tonoCustom.value.trim()) {
      tono = tonoCustom.value.trim();
    }

    const lingueSelezionate = ["it", ...Array.from(document.querySelectorAll(".ai-cfg-lang-opt:checked")).map((cb) => cb.value)];
    const linguaDefault = defaultLinguaSelect?.value || "it";

    const regoleSelezionate = [];
    document.querySelectorAll(".ai-cfg-rule-checkbox").forEach((cb) => {
      if (cb.checked) {
        const idx = Number(cb.dataset.ruleIndex);
        if (currentRules[idx]) regoleSelezionate.push(currentRules[idx]);
      }
    });

    return {
      verticale: verticaleSelezionato,
      nome_attivita: nome,
      orari,
      descrizione,
      tono,
      servizi: Array.isArray(dbProfileRecord?.servizi) ? dbProfileRecord.servizi : [],
      regole_escalation: regoleSelezionate.length ? regoleSelezionate : currentRules,
      whatsapp_collegato: Boolean(dbProfileRecord?.whatsapp_collegato),
      documenti_importati: Boolean(dbProfileRecord?.documenti_importati),
      lingue_supportate: lingueSelezionate,
      lingua_default: lingueSelezionate.includes(linguaDefault) ? linguaDefault : "it",
    };
  }

  async function eseguiSalvataggio() {
    if (saveBtn) saveBtn.disabled = true;
    if (saveStatus) { saveStatus.textContent = t("settings:ai_configuration.save_loading"); saveStatus.style.color = ""; }

    try {
      const payload = raccogliPayload();
      const res = await apiFetch(`${API_BASE}/api/onboarding/profilo`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        if (saveStatus) {
          saveStatus.textContent = err.detail || t("settings:ai_configuration.save_error");
          saveStatus.style.color = "var(--red)";
        }
        return;
      }
      const data = await res.json();
      dbProfileRecord = data.profilo;

      if (payload.nome_attivita) {
        const bName = document.getElementById("business-name");
        if (bName) bName.textContent = payload.nome_attivita;
        const cName = document.getElementById("chat-business-name");
        if (cName) cName.textContent = payload.nome_attivita;
      }

      // Sincronizza campo orari anche nella Knowledge Base (Dati Struttura)
      const kbOrari = document.getElementById("kb-orari-input");
      if (kbOrari && payload.orari) {
        kbOrari.value = payload.orari;
      }

      if (saveStatus) securityStatus(saveStatus, t("settings:ai_configuration.save_success"));
      toast(t("settings:ai_configuration.save_toast"), "success");
    } catch {
      if (saveStatus) {
        saveStatus.textContent = t("settings:ai_configuration.connection_error");
        saveStatus.style.color = "var(--red)";
      }
    } finally {
      if (saveBtn) saveBtn.disabled = false;
    }
  }

  form?.addEventListener("submit", (e) => {
    e.preventDefault();
    eseguiSalvataggio();
  });

  window.addEventListener("melpis:lang-changed", renderRules);

  caricaConfigurazioneAI = carica;
})();

/* ============================================================
   AUDIT — registro attività
   ============================================================ */

const AUDIT_ACTION_LABEL = {
  "profilo.aggiornato": "Profilo aggiornato",
  "org.timezone_updated": "Fuso orario aggiornato",
  "prenotazione.confermata": "Prenotazione confermata",
  "prenotazione.rifiutata": "Prenotazione rifiutata",
  "prenotazione.annullata": "Prenotazione annullata",
  "documento_eliminato": "Documento eliminato",
};

let auditOffset = 0;
let auditHasMore = false;

async function caricaAudit({ append = false } = {}) {
  const list = document.getElementById("audit-list");
  const count = document.getElementById("audit-count");
  if (!list) return;
  if (!append) {
    auditOffset = 0;
    auditHasMore = false;
    list.innerHTML = '<div class="skeleton skeleton-line-lg"></div><div class="skeleton skeleton-line-lg"></div><div class="skeleton skeleton-line-lg"></div>';
  }
  try {
    const res = await apiFetch(`${API_BASE}/api/audit?limit=20&offset=${auditOffset}`);
    if (!res.ok) {
      list.innerHTML = '<p class="inbox-empty">Registro non disponibile.</p>';
      return;
    }
    const data = await res.json();
    const eventi = data.eventi || [];
    auditHasMore = Boolean(data.has_more);
    if (!append) {
      list.innerHTML = "";
      document.getElementById("inbox-load-more")?.remove();
    } else {
      document.getElementById("audit-load-more")?.remove();
    }
    if (!eventi.length && !append) {
      list.innerHTML = '<p class="inbox-empty">Nessuna azione registrata: le modifiche a impostazioni, prenotazioni e documenti compariranno qui.</p>';
      if (count) count.textContent = "";
      return;
    }
    eventi.forEach((ev) => {
      const item = document.createElement("div");
      item.className = "audit-item";
      const quando = new Date(ev.created_at).toLocaleString(localeCorrente(), {
        day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
      });
      const dettagli = ev.details && Object.keys(ev.details).length
        ? Object.entries(ev.details).map(([k, v]) => `${k}: ${v}`).join(" · ")
        : "";
      item.innerHTML = `
        <div class="audit-main">
          <strong>${_sanitize(AUDIT_ACTION_LABEL[ev.action] || ev.action)}</strong>
          ${dettagli ? `<span class="audit-details">${_sanitize(dettagli)}</span>` : ""}
        </div>
        <div class="audit-meta">
          <span>${_sanitize(ev.user_email || "sistema")}</span>
          <time>${_sanitize(quando)}</time>
        </div>`;
      list.appendChild(item);
    });
    auditOffset += eventi.length;
    if (count) count.textContent = `${auditOffset} eventi`;
    if (auditHasMore) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.id = "audit-load-more";
      btn.className = "inbox-load-more";
      btn.textContent = "Carica altri eventi";
      btn.addEventListener("click", () => {
        btn.disabled = true;
        btn.textContent = "Carico…";
        caricaAudit({ append: true });
      });
      list.appendChild(btn);
    }
  } catch {
    list.innerHTML = '<p class="inbox-empty">Registro non disponibile.</p>';
  }
}

/* ============================================================
   INTEGRAZIONI — stato canali e webhook
   ============================================================ */

/* ============================================================
   INTEGRAZIONI — stato canali e test di connessione reale
   ============================================================ */

/* ============================================================
   INTEGRAZIONI — HELPER STATI UNIFICATI (§8) ED ERROR MAPPING (§9)
   ============================================================ */

/**
 * Estrae e normalizza i messaggi di errore restituiti dalle API (Invarianti 9 e 10).
 * Mappa i codici HTTP standard (§9 audit) e previene l'esposizione di dettagli tecnici grezzi
 * (stack trace, frammenti SQL o oggetti Pydantic serializzati come [object Object]).
 */
function _estraiMessaggioErroreApi(res, bodyData, fallbackMsg = "Operazione non riuscita.") {
  const status = res ? res.status : null;

  // 1. Mappatura prioritaria su codici di rete e infrastruttura (§9 audit)
  if (status === 429) {
    return "Troppe richieste inviate. Attendi qualche istante prima di riprovare.";
  }
  if (status === 502 || status === 503 || status === 504) {
    return "Il servizio esterno è temporaneamente non raggiungibile. Riprova tra poco.";
  }
  if (status === 500) {
    return "Si è verificato un errore interno del server. Riprova più tardi.";
  }
  if (status === 401) {
    return "Sessione scaduta o credenziali non valide. Effettua nuovamente l'accesso.";
  }

  // 2. Estrazione e sanitizzazione del payload 'detail'
  let rawDetail = bodyData?.detail || bodyData?.message || bodyData?.error;

  // Se FastAPI / Pydantic ha restituito un array di errori di validazione (422)
  if (Array.isArray(rawDetail)) {
    const fieldErrors = rawDetail
      .map((item) => {
        if (!item || typeof item !== "object") return null;
        const loc = Array.isArray(item.loc) ? item.loc[item.loc.length - 1] : "";
        const msg = item.msg || "non valido";
        return loc ? `Campo "${loc}": ${msg}` : msg;
      })
      .filter(Boolean);
    if (fieldErrors.length > 0) {
      return `Dati non validi: ${fieldErrors.slice(0, 3).join("; ")}.`;
    }
    rawDetail = null;
  } else if (rawDetail && typeof rawDetail === "object") {
    rawDetail = rawDetail.message || rawDetail.detail || JSON.stringify(rawDetail);
  }

  if (typeof rawDetail === "string" && rawDetail.trim()) {
    const trimmed = rawDetail.trim();
    // Filtro di sicurezza (Invariante 10): blocca leak di SQL, traceback o eccezioni interne grezze
    const isTechnicalLeak =
      /SELECT\s+|INSERT\s+|UPDATE\s+|DELETE\s+|Traceback|psycopg2|sqlalchemy|asyncpg|fastapi\.exceptions|pydantic|\/src\/|File\s+["'].*?["']|line\s+\d+|Internal Server Error/i.test(trimmed);
    if (!isTechnicalLeak) {
      return trimmed;
    }
  }

  // 3. Fallback contestuale su codici standard se detail non disponibile o non sicuro
  if (status === 403) {
    return res?.mfaRequired
      ? "Operazione bloccata: autenticazione a due fattori (MFA) richiesta."
      : "Non disponi dei permessi necessari per questa operazione.";
  }
  if (status === 404) {
    return "Risorsa o configurazione richiesta non trovata.";
  }
  if (status === 400) {
    return "I dati inviati non sono validi. Controlla i campi inseriti.";
  }

  return fallbackMsg;
}

/**
 * Aggiorna il badge di stato conformemente agli stati canonici di §8:
 * NOT_CONNECTED, CONNECTING, CONNECTED, ERROR, REQUIRES_REAUTH, DISABLED, UNAUTHORIZED, MFA_REQUIRED
 */
function _aggiornaBadgeStato(el, statoKey, customLabel) {
  if (!el) return;
  const canonicalMap = {
    connected: "connected",
    not_connected: "disconnected",
    disconnected: "disconnected",
    connecting: "connecting",
    pending: "connecting",
    pending_verification: "connecting",
    error: "error",
    requires_reauth: "requires_reauth",
    expired_token: "requires_reauth",
    disabled: "disabled",
    unauthorized: "unauthorized",
    mfa_required: "mfa_required",
  };

  const safeClass = canonicalMap[statoKey] || (statoKey === "connected" ? "connected" : "error");
  el.className = `integrazione-stato badge-status ${safeClass}`;
  const dot = '<span class="badge-status-dot"></span>';

  let label = customLabel;
  if (!label) {
    if (safeClass === "connected") label = "Connesso";
    else if (safeClass === "disconnected") label = "Non connesso";
    else if (safeClass === "connecting") label = "In attesa";
    else if (safeClass === "requires_reauth") label = "Riconnessione necessaria";
    else if (safeClass === "disabled") label = "Disabilitato";
    else if (safeClass === "unauthorized") label = "Non autorizzato";
    else if (safeClass === "mfa_required") label = "MFA richiesta";
    else label = "Errore";
  }

  el.innerHTML = `${dot}<span class="badge-status-label">${typeof DOMPurify !== "undefined" ? DOMPurify.sanitize(label) : _sanitize(label)}</span>`;
}

async function eseguiTestIntegrazione(canale, btn, feedbackEl) {
  if (!btn) return;
  if (btn.classList.contains("loading")) return;
  const originalHtml = btn.innerHTML;
  btn.disabled = true;
  btn.classList.add("loading");
  btn.innerHTML = `
    <svg viewBox="0 0 24 24" fill="none" width="15" height="15"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.19" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
    Verifico…
  `;
  if (feedbackEl) feedbackEl.hidden = true;

  try {
    const res = await apiFetch(`${API_BASE}/api/integrazioni/test/${encodeURIComponent(canale)}`, {
      method: "POST",
    });
    const data = await res.json().catch(() => ({}));

    const badgeEl = document.getElementById(`integ-${canale === "calendar" ? "calendar" : canale}-stato`);
    if (badgeEl && data.status) {
      _aggiornaBadgeStato(badgeEl, data.status);
    }

    if (feedbackEl) {
      feedbackEl.hidden = false;
      feedbackEl.className = `integ-test-feedback ${data.success ? "success" : data.status === "expired_token" ? "error" : "warning"}`;
      feedbackEl.textContent = data.message || (data.success ? "Test completato con successo." : "Errore durante la verifica.");
    }

    const verifiedUser = data.details?.username || (data.message && data.message.match(/@([a-zA-Z0-9._]+)/)?.[1]);
    if (canale === "instagram" && verifiedUser) {
      const igIdVal = document.getElementById("integ-ig-id");
      if (igIdVal) igIdVal.textContent = `@${verifiedUser}`;
    }

    if (data.success) {
      toast(data.message || "Connessione verificata con successo!", "success");
    } else {
      toast(data.message || "Verifica fallita: controlla le impostazioni.", "error");
    }
  } catch (err) {
    if (feedbackEl) {
      feedbackEl.hidden = false;
      feedbackEl.className = "integ-test-feedback error";
      feedbackEl.textContent = "Errore di rete o server non raggiungibile.";
    }
    toast("Impossibile eseguire il test di connessione.", "error");
  } finally {
    btn.classList.remove("loading");
    btn.disabled = false;
    btn.innerHTML = originalHtml;
  }
}

/* ============================================================
   INTEGRAZIONI: WHATSAPP GUIDED SETUP, INSTAGRAM, CALENDAR
   ============================================================ */

function _setWaWizardStep(stepNum) {
  [1, 2, 3].forEach((s) => {
    const tab = document.getElementById(`wa-step-tab-${s}`);
    const panel = document.getElementById(`wa-wz-panel-${s}`);
    if (tab) {
      tab.classList.toggle("active", s === stepNum);
      tab.classList.toggle("done", s < stepNum);
    }
    if (panel) {
      panel.hidden = (s !== stepNum);
    }
  });
}

async function caricaStatoWhatsApp() {
  const connectedCard = document.getElementById("integ-whatsapp-connected-card");
  const wizardCard = document.getElementById("integ-whatsapp-wizard-card");
  const phoneDisplay = document.getElementById("integ-wa-phone-number-display");
  const connectedTitle = document.getElementById("integ-wa-connected-title");
  const connectedSub = document.getElementById("integ-wa-connected-sub");
  const receptionStatus = document.getElementById("integ-wa-reception-status");
  const statoEl = document.getElementById("integ-whatsapp-stato");
  if (receptionStatus) receptionStatus.textContent = "Non verificata";

  try {
    const res = await apiFetch(`${API_BASE}/api/whatsapp/settings`);
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      const errMsg = _estraiMessaggioErroreApi(res, d, "Impossibile recuperare lo stato di WhatsApp.");
      if (res.status === 403) {
        if (connectedCard) connectedCard.hidden = false;
        if (wizardCard) wizardCard.hidden = true;
        if (connectedTitle) connectedTitle.textContent = "WhatsApp Business";
        if (connectedSub) connectedSub.textContent = errMsg;
        if (phoneDisplay) phoneDisplay.textContent = "—";
        if (statoEl) _aggiornaBadgeStato(statoEl, res.mfaRequired ? "mfa_required" : "unauthorized");
      } else {
        if (connectedCard) connectedCard.hidden = false;
        if (wizardCard) wizardCard.hidden = true;
        if (connectedTitle) connectedTitle.textContent = "WhatsApp Business";
        if (connectedSub) connectedSub.textContent = errMsg;
        if (phoneDisplay) phoneDisplay.textContent = "—";
        if (statoEl) _aggiornaBadgeStato(statoEl, "error");
      }
      return;
    }
    const d = await res.json();

    if (d.connesso) {
      if (connectedCard) connectedCard.hidden = false;
      if (wizardCard) wizardCard.hidden = true;
      if (phoneDisplay) phoneDisplay.textContent = d.display_phone_number || d.phone_number_id || "Numero collegato";
      if (connectedTitle) connectedTitle.textContent = d.verified_name || "WhatsApp Business";
      if (connectedSub) connectedSub.textContent = "Credenziali collegate; ricezione e consegna da verificare.";
      if (receptionStatus) receptionStatus.textContent = d.webhook_active === true
        ? "Da verificare con un messaggio reale"
        : "Non attiva: configurazione webhook incompleta";
      if (statoEl) _aggiornaBadgeStato(statoEl, "connected");
    } else {
      if (connectedCard) connectedCard.hidden = true;
      if (wizardCard) wizardCard.hidden = false;
      _setWaWizardStep(1);
    }
  } catch {
    console.warn("[WhatsApp] Impossibile recuperare lo stato della connessione.");
    if (connectedCard) connectedCard.hidden = false;
    if (wizardCard) wizardCard.hidden = true;
    if (connectedSub) connectedSub.textContent = "Errore di connessione con il server.";
    if (statoEl) _aggiornaBadgeStato(statoEl, "error", "Errore di rete");
  }
}

async function caricaStatoInstagram() {
  const connectedCard = document.getElementById("integ-instagram-connected-card");
  const wizardCard = document.getElementById("integ-instagram-wizard-card");
  const igIdVal = document.getElementById("integ-ig-id");
  const statoEl = document.getElementById("integ-instagram-stato");

  try {
    const res = await apiFetch(`${API_BASE}/api/instagram/account`);
    if (res.ok) {
      const d = await res.json();
      if (connectedCard) connectedCard.hidden = false;
      if (wizardCard) wizardCard.hidden = true;
      if (igIdVal) igIdVal.textContent = d.ig_user_id ? `@${d.ig_user_id}` : "Account collegato";
      if (statoEl) _aggiornaBadgeStato(statoEl, "connected");
    } else if (res.status === 404) {
      // 404: nessun account Instagram collegato per questo tenant
      if (connectedCard) connectedCard.hidden = true;
      if (wizardCard) wizardCard.hidden = false;
      if (statoEl) _aggiornaBadgeStato(statoEl, "disconnected");
    } else if (res.status === 403) {
      const d = await res.json().catch(() => ({}));
      const errMsg = _estraiMessaggioErroreApi(res, d, "Permessi insufficienti per Instagram.");
      if (connectedCard) connectedCard.hidden = false;
      if (wizardCard) wizardCard.hidden = true;
      if (igIdVal) igIdVal.textContent = errMsg;
      if (statoEl) _aggiornaBadgeStato(statoEl, res.mfaRequired ? "mfa_required" : "unauthorized");
    } else {
      const d = await res.json().catch(() => ({}));
      const errMsg = _estraiMessaggioErroreApi(res, d, "Impossibile recuperare lo stato di Instagram.");
      if (connectedCard) connectedCard.hidden = false;
      if (wizardCard) wizardCard.hidden = true;
      if (igIdVal) igIdVal.textContent = errMsg;
      if (statoEl) _aggiornaBadgeStato(statoEl, "error");
    }
  } catch (err) {
    if (connectedCard) connectedCard.hidden = true;
    if (wizardCard) wizardCard.hidden = false;
    if (statoEl) _aggiornaBadgeStato(statoEl, "error", "Errore di rete");
  }
}

async function caricaIntegrazioni() {
  const status = document.getElementById("integrazioni-status");
  try {
    await Promise.allSettled([
      caricaStatoWhatsApp(),
      caricaStatoInstagram(),
      caricaStatoCalendar(),
      caricaStatoReviews(),
      caricaStatoBooking(),
      caricaStatoAirtable()
    ]);
  } catch (err) {
    if (status) { status.textContent = "Errore durante il caricamento integrazioni."; status.style.color = "var(--red)"; }
  }
}

// WhatsApp Wizard Event Listeners
const whatsappTestIdempotencyKeys = new Map();

function _getWhatsAppTestIdempotencyKey(recipient) {
  if (!recipient || !String(recipient).trim()) return null;
  const value = String(recipient).trim();
  const normalizedRecipient = value.replace(/\D/g, "") || value.toLowerCase();
  if (!whatsappTestIdempotencyKeys.has(normalizedRecipient)) {
    whatsappTestIdempotencyKeys.set(normalizedRecipient, crypto.randomUUID());
  }
  return whatsappTestIdempotencyKeys.get(normalizedRecipient);
}

function _completeWhatsAppTestIntent(recipient, key, delivered) {
  if (!recipient || !key || delivered !== true) return;
  const value = String(recipient).trim();
  const normalizedRecipient = value.replace(/\D/g, "") || value.toLowerCase();
  if (whatsappTestIdempotencyKeys.get(normalizedRecipient) === key) {
    whatsappTestIdempotencyKeys.delete(normalizedRecipient);
  }
}
document.getElementById("wa-wz-goto-step2")?.addEventListener("click", () => {
  _setWaWizardStep(2);
});

document.getElementById("wa-wz-back-step1")?.addEventListener("click", () => {
  _setWaWizardStep(1);
});

document.getElementById("wa-connect-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const phoneId = document.getElementById("wa-phone-number-id")?.value.trim();
  const wabaId = document.getElementById("wa-waba-id")?.value.trim();
  const token = document.getElementById("wa-access-token")?.value.trim();
  const statusEl = document.getElementById("wa-connect-status");
  const submitBtn = document.getElementById("wa-connect-submit");

  if (!phoneId || !wabaId || !token) {
    if (statusEl) {
      statusEl.textContent = "Compila tutti i campi obbligatori.";
      statusEl.className = "security-status err";
    }
    return;
  }

  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.textContent = "Verifica con Meta in corso…";
  }
  if (statusEl) {
    statusEl.textContent = "Verifica credenziali con Meta Cloud API…";
    statusEl.className = "security-status";
  }

  try {
    const res = await apiFetch(`${API_BASE}/api/whatsapp/connect`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ phone_number_id: phoneId, waba_id: wabaId, access_token: token }),
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      const warning = data.webhook_subscription_warning;
      if (statusEl) statusEl.textContent = warning || "";
      toast(warning || data.message || "Credenziali WhatsApp salvate.", warning ? "info" : "success");
      _setWaWizardStep(3);
    } else {
      const errMsg = _estraiMessaggioErroreApi(res, data, "Errore durante la connessione con Meta.");
      if (statusEl) {
        statusEl.textContent = errMsg;
        statusEl.className = "security-status err";
      }
      toast(errMsg, "error");
    }
  } catch (err) {
    if (statusEl) {
      statusEl.textContent = "Errore di connessione con il server.";
      statusEl.className = "security-status err";
    }
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = "Verifica e Salva Collegamento";
    }
    document.getElementById("wa-access-token").value = "";
  }
});

document.getElementById("wa-wz-send-test-btn")?.addEventListener("click", async () => {
  const testPhone = document.getElementById("wa-wz-test-phone")?.value.trim();
  const idempotencyKey = _getWhatsAppTestIdempotencyKey(testPhone);
  const statusEl = document.getElementById("wa-wz-test-status");
  const btn = document.getElementById("wa-wz-send-test-btn");

  if (btn) {
    btn.disabled = true;
    btn.textContent = "Invio in corso…";
  }
  if (statusEl) {
    statusEl.textContent = "Invio del messaggio di prova a Meta…";
    statusEl.className = "security-status";
  }

  try {
    const res = await apiFetch(`${API_BASE}/api/whatsapp/send-test`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        to_phone: testPhone || null,
        ...(idempotencyKey ? { idempotency_key: idempotencyKey } : {}),
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok && data.success) {
      _completeWhatsAppTestIntent(testPhone, idempotencyKey, true);
      if (statusEl) {
        statusEl.textContent = data.message || "Messaggio di test inviato con successo!";
        statusEl.className = "security-status ok";
      }
      toast(data.message || "Messaggio inviato!", "success");
    } else {
      const errMsg = _estraiMessaggioErroreApi(res, data, data.message || "Errore durante l'invio del messaggio di test.");
      if (statusEl) {
        statusEl.textContent = errMsg;
        statusEl.className = "security-status err";
      }
      toast(errMsg, "error");
    }
  } catch {
    if (statusEl) {
      statusEl.textContent = "Errore di comunicazione col server.";
      statusEl.className = "security-status err";
    }
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Invia messaggio di test";
    }
  }
});

document.getElementById("wa-wz-finish-btn")?.addEventListener("click", async () => {
  await caricaStatoWhatsApp();
  toast("Configurazione salvata; verifica lo stato del collegamento.", "info");
});

// WhatsApp Connected Card Handlers
document.getElementById("integ-wa-open-test-btn")?.addEventListener("click", () => {
  const box = document.getElementById("integ-wa-test-box");
  if (box) box.hidden = !box.hidden;
});

document.getElementById("integ-wa-send-test-submit")?.addEventListener("click", async () => {
  const phone = document.getElementById("integ-wa-test-phone-input")?.value.trim();
  const feedback = document.getElementById("integ-wa-test-feedback");
  const btn = document.getElementById("integ-wa-send-test-submit");

  if (!phone) {
    if (feedback) {
      feedback.hidden = false;
      feedback.className = "integ-test-feedback error";
      feedback.textContent = "Inserisci un numero di cellulare per ricevere la prova.";
    }
    return;
  }
  const idempotencyKey = _getWhatsAppTestIdempotencyKey(phone);

  if (btn) {
    btn.disabled = true;
    btn.textContent = "Invio…";
  }

  try {
    const res = await apiFetch(`${API_BASE}/api/whatsapp/send-test`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ to_phone: phone, idempotency_key: idempotencyKey }),
    });
    const d = await res.json().catch(() => ({}));
    if (res.ok && d.success) {
      _completeWhatsAppTestIntent(phone, idempotencyKey, true);
      if (feedback) {
        feedback.hidden = false;
        feedback.className = "integ-test-feedback success";
        feedback.textContent = d.message || "Messaggio inviato!";
      }
      toast(d.message || "Messaggio inviato!", "success");
    } else {
      const errMsg = _estraiMessaggioErroreApi(res, d, d.message || "Errore durante l'invio della prova.");
      if (feedback) {
        feedback.hidden = false;
        feedback.className = "integ-test-feedback error";
        feedback.textContent = errMsg;
      }
      toast(errMsg, "error");
    }
  } catch {
    if (feedback) {
      feedback.hidden = false;
      feedback.className = "integ-test-feedback error";
      feedback.textContent = "Errore di connessione.";
    }
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Invia prova";
    }
  }
});

document.getElementById("integ-wa-reconfig-btn")?.addEventListener("click", () => {
  const connectedCard = document.getElementById("integ-whatsapp-connected-card");
  const wizardCard = document.getElementById("integ-whatsapp-wizard-card");
  if (connectedCard) connectedCard.hidden = true;
  if (wizardCard) wizardCard.hidden = false;
  _setWaWizardStep(2);
});

document.getElementById("integ-wa-disconnect-btn")?.addEventListener("click", async () => {
  const ok = await confermaDestructiva({
    titolo: "Disconnettere WhatsApp Business?",
    descrizione: "L'assistente AI smetterà di rispondere automaticamente ai messaggi in arrivo su questo numero WhatsApp.",
    label: "Disconnetti WhatsApp",
  });
  if (!ok) return;

  try {
    const res = await apiFetch(`${API_BASE}/api/whatsapp/disconnect`, { method: "POST" });
    const d = await res.json().catch(() => ({}));
    if (res.ok) {
      toast("WhatsApp disconnesso con successo.");
      await caricaStatoWhatsApp();
    } else if (res.status !== 403) {
      toast(_estraiMessaggioErroreApi(res, d, "Errore durante la disconnessione."), "error");
    }
  } catch {
    toast("Errore di rete.", "error");
  }
});

// Instagram Handlers
document.getElementById("ig-connect-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const igUserId = document.getElementById("ig-user-id-input")?.value.trim();
  const igToken = document.getElementById("ig-token-input")?.value.trim();
  const statusEl = document.getElementById("ig-connect-status");
  const submitBtn = document.getElementById("ig-connect-submit");

  if (!igUserId || !igToken) {
    if (statusEl) {
      statusEl.textContent = "Compila tutti i campi obbligatori.";
      statusEl.className = "security-status err";
    }
    return;
  }

  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.textContent = "Collegamento in corso…";
  }

  try {
    const res = await apiFetch(`${API_BASE}/api/instagram/account`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ig_user_id: igUserId, access_token: igToken }),
    });
    const d = await res.json().catch(() => ({}));
    if (res.ok) {
      toast("Instagram Direct collegato con successo!", "success");
      await caricaStatoInstagram();
    } else {
      const errMsg = _estraiMessaggioErroreApi(res, d, "Errore durante il collegamento di Instagram.");
      if (statusEl) {
        statusEl.textContent = errMsg;
        statusEl.className = "security-status err";
      }
      toast(errMsg, "error");
    }
  } catch {
    if (statusEl) {
      statusEl.textContent = "Errore di connessione.";
      statusEl.className = "security-status err";
    }
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = "Collega Instagram Direct";
    }
    document.getElementById("ig-token-input").value = "";
  }
});

document.getElementById("integ-instagram-disconnect")?.addEventListener("click", async () => {
  const ok = await confermaDestructiva({
    titolo: "Disconnettere Instagram Direct?",
    descrizione: "L'assistente AI non risponderà più ai messaggi diretti su questo account Instagram.",
    label: "Disconnetti Instagram",
  });
  if (!ok) return;

  try {
    const res = await apiFetch(`${API_BASE}/api/instagram/account`, { method: "DELETE" });
    const d = await res.json().catch(() => ({}));
    if (res.ok) {
      toast("Instagram Direct disconnesso con successo.");
      const feedbackEl = document.getElementById("integ-instagram-feedback");
      if (feedbackEl) feedbackEl.hidden = true;
      await caricaStatoInstagram();
    } else if (res.status !== 403) {
      toast(_estraiMessaggioErroreApi(res, d, "Errore durante la disconnessione."), "error");
    }
  } catch {
    toast("Errore di rete.", "error");
  }
});

document.querySelectorAll(".btn-test-conn[data-canale]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const canale = btn.dataset.canale;
    if (!canale) return;
    const feedbackEl = document.getElementById(`integ-${canale}-feedback`);
    eseguiTestIntegrazione(canale, btn, feedbackEl);
  });
});

/* ============================================================
   GOOGLE CALENDAR — stato, connect, disconnect
   ============================================================ */

async function caricaStatoCalendar() {
  const stato = document.getElementById("integ-calendar-stato");
  const sub = document.getElementById("integ-calendar-sub");
  const help = document.getElementById("integ-calendar-help");
  const calIdMeta = document.getElementById("integ-calendar-id");
  const syncModeMeta = document.getElementById("integ-calendar-sync-mode");
  const btnConnect = document.getElementById("integ-calendar-connect");
  const btnTest = document.getElementById("integ-calendar-test");
  const btnDisconnect = document.getElementById("integ-calendar-disconnect");

  if (!stato || !sub) return;

  try {
    const res = await apiFetch(`${API_BASE}/api/calendar/status`);
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      const errMsg = _estraiMessaggioErroreApi(res, d, "Impossibile verificare lo stato di Google Calendar.");
      if (res.status === 403) {
        _aggiornaBadgeStato(stato, res.mfaRequired ? "mfa_required" : "unauthorized");
        sub.textContent = errMsg;
      } else {
        _aggiornaBadgeStato(stato, "error");
        sub.textContent = errMsg;
      }
      return;
    }
    const d = await res.json();
    if (d.connected) {
      _aggiornaBadgeStato(stato, "connected");
      const calId = d.calendar_id || "Primary (Predefinito)";
      const sync = d.sync_enabled ? "Sincronizzazione attiva" : "Sincronizzazione in pausa";
      sub.textContent = `${calId} · ${sync}`;
      if (calIdMeta) calIdMeta.textContent = calId;
      if (syncModeMeta) syncModeMeta.textContent = d.sync_enabled ? "Bidirezionale automatica" : "In pausa";
      if (help) help.textContent = d.last_sync_at
        ? `Ultima sincronizzazione: ${new Date(d.last_sync_at).toLocaleString(localeCorrente())}`
        : "Sincronizzazione attiva: le prenotazioni confermate vengono sincronizzate con Google Calendar.";
      if (btnConnect) btnConnect.hidden = true;
      if (btnTest) btnTest.hidden = false;
      if (btnDisconnect) btnDisconnect.hidden = false;
    } else {
      _aggiornaBadgeStato(stato, "disconnected");
      sub.textContent = "Nessun account Google collegato";
      if (calIdMeta) calIdMeta.textContent = "Nessun calendario";
      if (syncModeMeta) syncModeMeta.textContent = "Disattivata";
      if (btnConnect) btnConnect.hidden = false;
      if (btnTest) btnTest.hidden = true;
      if (btnDisconnect) btnDisconnect.hidden = true;
    }
  } catch {
    _aggiornaBadgeStato(stato, "error", "Errore di rete");
    sub.textContent = "Errore di connessione con il server";
  }
}

document.getElementById("integ-calendar-connect")?.addEventListener("click", async () => {
  const url = new URL(`${API_BASE}/api/calendar/auth`, window.location.origin);
  const selectedOrg = localStorage.getItem("melpis_selected_organization");
  if (selectedOrg) url.searchParams.set("organization_id", selectedOrg);
  try {
    const checkRes = await apiFetch(url.href, { method: "GET", redirect: "manual" });
    if (checkRes.status >= 400) return;
  } catch (_) { return; }
  window.location.href = url.href;
});

document.getElementById("integ-calendar-disconnect")?.addEventListener("click", async () => {
  const ok = await confermaDestructiva({
    titolo: "Disconnettere Google Calendar?",
    descrizione: "Le prenotazioni esistenti restano memorizzate in Melpis, ma non verranno più sincronizzate con Google Calendar.",
    label: "Disconnetti",
  });
  if (!ok) return;
  try {
    const res = await apiFetch(`${API_BASE}/api/calendar/disconnect`, { method: "DELETE" });
    const d = await res.json().catch(() => ({}));
    if (res.ok) {
      toast("Google Calendar disconnesso con successo.");
      await caricaStatoCalendar();
    } else if (res.status !== 403) {
      toast(_estraiMessaggioErroreApi(res, d, "Errore durante la disconnessione."), "error");
    }
  } catch {
    toast("Errore di connessione.", "error");
  }
});

/* Gestione redirect OAuth callback, eseguita dopo Auth e router. */
function gestisciCalendarRedirect() {
  const params = new URLSearchParams(window.location.search);
  const cal = params.get("calendar");
  if (cal === "connected") {
    const url = new URL(window.location);
    url.searchParams.delete("calendar");
    window.history.replaceState(window.history.state, "", url);
    apriVistaImpostazioni("calendar");
    setTimeout(() => toast("Google Calendar connesso con successo!", "success"), 400);
  } else if (cal === "error") {
    const reason = params.get("reason") || "errore_sconosciuto";
    const url = new URL(window.location);
    url.searchParams.delete("calendar");
    url.searchParams.delete("reason");
    window.history.replaceState(window.history.state, "", url);
    apriVistaImpostazioni("calendar");
    const msgs = {
      no_refresh_token: "Autorizzazione negata: la tua app Google non è in produzione. Aggiungi il tuo account come test user nella Google Cloud Console.",
      invalid_state: "Sessione scaduta: riprova la connessione.",
      invalid_nonce: "Sessione scaduta: riprova la connessione.",
      nonce_expired: "Timeout: la richiesta è scaduta, riprova.",
      missing_code: "Autorizzazione annullata.",
      server_error: "Errore del server: riprova più tardi.",
    };
    setTimeout(() => toast(msgs[reason] || `Errore: ${reason}`, "error"), 400);
  }
}

/* ============================================================
   GOOGLE RECENSIONI (BUSINESS PROFILE) — stato, connect, sync, disconnect
   ============================================================ */

async function caricaStatoReviews() {
  const stato = document.getElementById("integ-reviews-stato");
  const sub = document.getElementById("integ-reviews-sub");
  const help = document.getElementById("integ-reviews-help");
  const accountMeta = document.getElementById("integ-reviews-account");
  const locationMeta = document.getElementById("integ-reviews-location");
  const btnConnect = document.getElementById("integ-reviews-connect");
  const btnSync = document.getElementById("integ-reviews-sync");
  const btnDisconnect = document.getElementById("integ-reviews-disconnect");
  const summaryStatus = document.getElementById("reviews-google-summary-status");

  const setSummaryStatus = (connected) => {
    if (!summaryStatus) return;
    const state = connected === true ? "connected" : connected === false ? "disconnected" : "unavailable";
    summaryStatus.dataset.state = state;
    summaryStatus.classList.toggle("ready", state === "connected");
    summaryStatus.classList.toggle("manual", state !== "connected");
    summaryStatus.textContent = state === "connected"
      ? _tDash("reviews.google_connected", "Connesso")
      : state === "disconnected"
        ? _tDash("reviews.google_disconnected", "Non collegato")
        : _tDash("reviews.google_status_unavailable", "Stato non disponibile");
  };

  if (!stato || !sub) return;

  try {
    const res = await apiFetch(`${API_BASE}/api/reviews/google/status`);
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      const errMsg = _estraiMessaggioErroreApi(res, d, "Impossibile verificare lo stato di Google Recensioni.");
      if (res.status === 403) {
        _aggiornaBadgeStato(stato, res.mfaRequired ? "mfa_required" : "unauthorized");
        sub.textContent = errMsg;
      } else {
        _aggiornaBadgeStato(stato, "error");
        sub.textContent = errMsg;
      }
      setSummaryStatus(null);
      return;
    }
    const d = await res.json();
    if (d.connected) {
      const operational = d.operational === true;
      setSummaryStatus(operational ? true : null);
      _aggiornaBadgeStato(stato, operational ? "connected" : "pending", operational ? "Attiva" : "OAuth collegato; integrazione non attiva");
      const acc = d.account_name || "Account Google collegato";
      const loc = d.location_name || "Sede predefinita";
      sub.textContent = operational ? `${loc} · Attiva` : "Accesso alle recensioni non ancora verificato dal provider";
      if (accountMeta) accountMeta.textContent = acc;
      if (locationMeta) locationMeta.textContent = loc;
      if (help) {
        help.textContent = d.last_sync_at
          ? `Ultima sincronizzazione: ${new Date(d.last_sync_at).toLocaleString(localeCorrente())}`
          : "Consenso OAuth salvato. Disponibilità Account/Location/Reviews da verificare; quota Google zero richiede approvazione del progetto.";
      }
      if (btnConnect) btnConnect.hidden = true;
      if (btnSync) btnSync.hidden = false;
      if (btnDisconnect) btnDisconnect.hidden = false;
    } else {
      setSummaryStatus(false);
      _aggiornaBadgeStato(stato, "disconnected");
      sub.textContent = "Nessun account Google collegato";
      if (accountMeta) accountMeta.textContent = "Nessun account";
      if (locationMeta) locationMeta.textContent = "—";
      if (help) {
        help.textContent = "Collega Google Business Profile: importazione e funzioni sulle recensioni dipendono dalla disponibilità e dai permessi verificati del provider.";
      }
      if (btnConnect) btnConnect.hidden = false;
      if (btnSync) btnSync.hidden = true;
      if (btnDisconnect) btnDisconnect.hidden = true;
    }
  } catch {
    setSummaryStatus(null);
    _aggiornaBadgeStato(stato, "error", "Errore di rete");
    sub.textContent = "Errore di connessione con il server";
  }
}

// The Reviews overview badge reflects the same tenant-scoped status endpoint as
// the integration settings page; it must never claim a static connection.
if (document.getElementById("reviews-google-summary-status")) {
  void caricaStatoReviews();
}

document.getElementById("integ-reviews-connect")?.addEventListener("click", async () => {
  const url = new URL(`${API_BASE}/api/reviews/google/auth`, window.location.origin);
  const selectedOrg = localStorage.getItem("melpis_selected_organization");
  if (selectedOrg) url.searchParams.set("organization_id", selectedOrg);
  try {
    const checkRes = await apiFetch(url.href, { method: "GET", redirect: "manual" });
    if (checkRes.status >= 400) return;
  } catch (_) { return; }
  window.location.href = url.href;
});

document.getElementById("integ-reviews-sync")?.addEventListener("click", async () => {
  const btn = document.getElementById("integ-reviews-sync");
  const feedbackEl = document.getElementById("integ-reviews-feedback");
  if (!btn || btn.classList.contains("loading")) return;

  const originalHtml = btn.innerHTML;
  btn.disabled = true;
  btn.classList.add("loading");
  btn.innerHTML = `
    <svg viewBox="0 0 24 24" fill="none" width="15" height="15"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.19" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
    Sincronizzo…
  `;
  if (feedbackEl) feedbackEl.hidden = true;

  try {
    const res = await apiFetch(`${API_BASE}/api/reviews/google/sync`, { method: "POST" });
    const d = await res.json().catch(() => ({}));
    if (res.ok) {
      const msg = typeof d.nuove === "number"
        ? (d.nuove > 0 ? `Sincronizzazione completata: ${d.nuove} nuove recensioni importate.` : "Nessuna nuova recensione da importare.")
        : "Sincronizzazione completata con successo!";
      if (feedbackEl) {
        feedbackEl.hidden = false;
        feedbackEl.className = "integ-test-feedback success";
        feedbackEl.textContent = msg;
      }
      toast(msg, "success");
      await caricaStatoReviews();
      if (typeof aggiornaRecensioni === "function") {
        aggiornaRecensioni();
      }
    } else if (res.status !== 403) {
      const errMsg = _estraiMessaggioErroreApi(res, d, "Errore durante la sincronizzazione delle recensioni.");
      if (feedbackEl) {
        feedbackEl.hidden = false;
        feedbackEl.className = "integ-test-feedback error";
        feedbackEl.textContent = errMsg;
      }
      toast(errMsg, "error");
    }
  } catch {
    if (feedbackEl) {
      feedbackEl.hidden = false;
      feedbackEl.className = "integ-test-feedback error";
      feedbackEl.textContent = "Errore di connessione durante la sincronizzazione.";
    }
    toast("Impossibile sincronizzare le recensioni.", "error");
  } finally {
    btn.classList.remove("loading");
    btn.disabled = false;
    btn.innerHTML = originalHtml;
  }
});

document.getElementById("integ-reviews-disconnect")?.addEventListener("click", async () => {
  const ok = await confermaDestructiva({
    titolo: "Disconnettere Google Recensioni?",
    descrizione: "Le recensioni già importate rimarranno salvate in Melpis, ma non verranno più scaricate nuove recensioni né pubblicate risposte automatiche su Google Business.",
    label: "Disconnetti profilo",
  });
  if (!ok) return;

  try {
    const res = await apiFetch(`${API_BASE}/api/reviews/google/disconnect`, { method: "DELETE" });
    const d = await res.json().catch(() => ({}));
    if (res.ok) {
      toast("Google Business disconnesso con successo.");
      const feedbackEl = document.getElementById("integ-reviews-feedback");
      if (feedbackEl) feedbackEl.hidden = true;
      await caricaStatoReviews();
    } else if (res.status !== 403) {
      toast(_estraiMessaggioErroreApi(res, d, "Errore durante la disconnessione."), "error");
    }
  } catch {
    toast("Errore di connessione.", "error");
  }
});

/* Gestione redirect OAuth callback per Google Reviews, dopo Auth e router. */
function gestisciReviewsRedirect() {
  const params = new URLSearchParams(window.location.search);
  const rev = params.get("reviews_google");
  if (!rev) return;

  if (rev === "connected") {
    const url = new URL(window.location);
    url.searchParams.delete("reviews_google");
    window.history.replaceState(window.history.state, "", url);
    if (typeof apriVistaImpostazioni === "function") {
      apriVistaImpostazioni("reviews");
    }
    setTimeout(() => toast("Google Recensioni connesso con successo!", "success"), 400);
  } else if (rev === "error") {
    const reason = params.get("reason") || "errore_sconosciuto";
    const url = new URL(window.location);
    url.searchParams.delete("reviews_google");
    url.searchParams.delete("reason");
    window.history.replaceState(window.history.state, "", url);
    if (typeof apriVistaImpostazioni === "function") {
      apriVistaImpostazioni("reviews");
    }
    const msgs = {
      no_refresh_token: "Autorizzazione negata: account non autorizzato o refresh token assente.",
      invalid_state: "Sessione scaduta: riprova la connessione.",
      invalid_nonce: "Sessione scaduta: riprova la connessione.",
      nonce_expired: "Timeout: la richiesta di autorizzazione è scaduta, riprova.",
      missing_code: "Autorizzazione Google annullata o codice mancante.",
      access_denied: "Autorizzazione rifiutata su Google.",
    };
    setTimeout(() => toast(msgs[reason] || `Errore autorizzazione Google: ${reason}`, "error"), 400);
  }
}

/* ============================================================
   INTEGRAZIONE GESTIONALE PRENOTAZIONI (PMS / BOOKING FRAMEWORK - FASE 2)
   ============================================================ */

const PMS_PROVIDER_LABELS = {
  internal: "Melpis Interno (Built-in)",
  calcom: "Cal.com",
  simplybook: "SimplyBook.me",
  apaleo: "Apaleo PMS",
  beds24: "Beds24 PMS",
  zak: "WuBook ZaK",
};

const PMS_MODE_LABELS = {
  authoritative: "Authoritative (Primario)",
  shadow: "Shadow (Monitoraggio)",
  local_only: "Local Only (Solo DB Locale)",
  mirror: "Mirror (Sincronizzazione)",
};

async function caricaStatoBooking() {
  const statoEl = document.getElementById("integ-booking-stato");
  const titleEl = document.getElementById("integ-booking-title");
  const subEl = document.getElementById("integ-booking-sub");
  const helpEl = document.getElementById("integ-booking-help");
  const metaProviderEl = document.getElementById("integ-booking-meta-provider");
  const metaModeEl = document.getElementById("integ-booking-meta-mode");
  const metaDpaEl = document.getElementById("integ-booking-meta-dpa");
  const metaSyncEl = document.getElementById("integ-booking-meta-sync");
  const honestContainer = document.getElementById("integ-booking-honest-badge-container");
  const warningBanner = document.getElementById("integ-booking-warning-banner");
  const quickModeBox = document.getElementById("integ-booking-quick-mode");
  const quickModeSelect = document.getElementById("integ-booking-quick-mode-select");
  const optQuickAuth = document.getElementById("opt-quick-authoritative");
  const toggleBtn = document.getElementById("integ-booking-toggle-form-btn");
  const disconnectBtn = document.getElementById("integ-booking-disconnect-btn");

  if (!statoEl || !titleEl) return;

  try {
    const res = await apiFetch(`${API_BASE}/api/v1/integrations/booking/status`);
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      const errMsg = _estraiMessaggioErroreApi(res, d, "Impossibile verificare lo stato del gestionale.");
      if (res.status === 403) {
        _aggiornaBadgeStato(statoEl, res.mfaRequired ? "mfa_required" : "unauthorized");
        if (subEl) subEl.textContent = errMsg;
      } else {
        _aggiornaBadgeStato(statoEl, "error");
        if (subEl) subEl.textContent = errMsg;
      }
      if (quickModeBox) quickModeBox.hidden = true;
      if (disconnectBtn) disconnectBtn.hidden = true;
      return;
    }

    const d = await res.json();
    const isConfigured = Boolean(d.is_configured && d.provider && d.provider !== "internal" && d.is_active);

    if (!isConfigured) {
      // Motore Interno Built-in
      _aggiornaBadgeStato(statoEl, "connected", "Built-in / Attivo");
      titleEl.textContent = "Melpis Interno";
      if (subEl) subEl.textContent = "Motore integrato (Local Only)";
      if (metaProviderEl) metaProviderEl.textContent = "Melpis Interno (Built-in)";
      if (metaModeEl) metaModeEl.textContent = "Local Only";
      if (metaDpaEl) metaDpaEl.textContent = "Non applicabile";
      if (metaSyncEl) metaSyncEl.textContent = "—";
      if (helpEl) {
        helpEl.textContent = "Il motore interno gestisce disponibilità, fasce orarie e capienze direttamente su database sicuro PostgreSQL senza software terzi.";
      }
      if (honestContainer) honestContainer.hidden = true;
      if (warningBanner) warningBanner.hidden = true;
      if (quickModeBox) quickModeBox.hidden = true;
      if (disconnectBtn) disconnectBtn.hidden = true;
      if (toggleBtn) toggleBtn.textContent = "Configura gestionale esterno";
    } else {
      // Gestionale Esterno Configurato
      const provName = PMS_PROVIDER_LABELS[d.provider] || d.provider;
      _aggiornaBadgeStato(statoEl, "connected", "Connesso");
      titleEl.textContent = provName;
      if (subEl) subEl.textContent = `Modalità ${d.mode || "authoritative"} · Attivo`;
      if (metaProviderEl) metaProviderEl.textContent = provName;
      if (metaModeEl) metaModeEl.textContent = PMS_MODE_LABELS[d.mode] || d.mode;
      if (metaDpaEl) metaDpaEl.textContent = d.medical_dpa_signed ? "Firmato (Art. 9 GDPR)" : "Non attivo (Note filtrate)";

      if (metaSyncEl) {
        if (d.last_sync) {
          const sDate = d.last_sync.updated_at ? new Date(d.last_sync.updated_at).toLocaleString(localeCorrente()) : "";
          if (d.last_sync.status === "synced") {
            metaSyncEl.textContent = `Sincronizzato (${d.last_sync.external_booking_id || "OK"}) ${sDate ? "· " + sDate : ""}`;
          } else if (d.last_sync.status === "failed") {
            metaSyncEl.textContent = `Errore: ${d.last_sync.sync_error || "fallito"} ${sDate ? "· " + sDate : ""}`;
          } else {
            metaSyncEl.textContent = `${d.last_sync.status} ${sDate ? "· " + sDate : ""}`;
          }
        } else {
          metaSyncEl.textContent = "Nessuna transazione recente";
        }
      }

      if (helpEl) {
        helpEl.textContent = `Integrazione con ${provName} attiva. Le prenotazioni inviate dai clienti WhatsApp vengono gestite in modalità ${d.mode}.`;
      }

      // Honest badge: "Verifica live consigliata" per Cal.com, Apaleo, Beds24 (§12 audit)
      if (honestContainer) {
        honestContainer.hidden = !["calcom", "apaleo", "beds24"].includes(d.provider);
      }

      // Warning banner per SimplyBook (aggiornamento non supportato) e ZaK (solo shadow/local_only)
      if (warningBanner) {
        if (d.provider === "simplybook") {
          warningBanner.hidden = false;
          warningBanner.innerHTML = "<strong>Attenzione SimplyBook:</strong> L'aggiornamento e la modifica delle prenotazioni non sono supportati via API da SimplyBook.me (solo creazione e cancellazione via bot).";
        } else if (d.provider === "zak") {
          warningBanner.hidden = false;
          warningBanner.innerHTML = "<strong>Gating Governance:</strong> WuBook ZaK è attivo in modalità protetta (Shadow / Local Only). La modalità authoritative è disabilitata per policy di governance.";
        } else {
          warningBanner.hidden = true;
        }
      }

      // Quick Mode Box
      if (quickModeBox) {
        quickModeBox.hidden = false;
        if (quickModeSelect) {
          quickModeSelect.value = d.mode || "authoritative";
          if (optQuickAuth) {
            if (d.provider === "zak") {
              optQuickAuth.disabled = true;
              optQuickAuth.textContent = "Authoritative (Disabilitato per ZaK)";
            } else {
              optQuickAuth.disabled = false;
              optQuickAuth.textContent = "Authoritative (Primario)";
            }
          }
        }
      }

      if (disconnectBtn) disconnectBtn.hidden = false;
      if (toggleBtn) toggleBtn.textContent = "Riconfigura credenziali";
    }
  } catch (err) {
    _aggiornaBadgeStato(statoEl, "error", "Errore");
    if (subEl) subEl.textContent = "Errore di connessione con il server";
  }
}

function _aggiornaDescrizioneModalitaBooking() {
  const modeSelect = document.getElementById("booking-mode-select");
  const descEl = document.getElementById("booking-mode-description");
  if (!modeSelect || !descEl) return;

  const m = modeSelect.value;
  if (m === "authoritative") {
    descEl.textContent = "Il gestionale esterno fa fede assoluta per disponibilità e prenotazioni. Nessun fallback fittizio locale; se il gestionale fallisce o è occupato, scatta l'escalation con operatore umano.";
  } else if (m === "shadow") {
    descEl.textContent = "La prenotazione viene salvata e confermata primariamente sul database locale di Melpis; la chiamata al gestionale esterno avviene asincronamente per audit e monitoraggio.";
  } else {
    descEl.textContent = "Tutte le transazioni avvengono esclusivamente sul database PostgreSQL locale di Melpis. Nessun dato inviato al gestionale esterno.";
  }
}

function _aggiornaGatingProviderBooking() {
  const providerSelect = document.getElementById("booking-provider-select");
  const modeSelect = document.getElementById("booking-mode-select");
  const optAuth = document.getElementById("opt-form-authoritative");
  const optShadow = document.getElementById("opt-form-shadow");
  const honestBox = document.getElementById("booking-form-honest-box");
  const honestText = document.getElementById("booking-form-honest-text");
  const warningBox = document.getElementById("booking-form-warning-box");
  const warningText = document.getElementById("booking-form-warning-text");

  if (!providerSelect || !modeSelect) return;
  const p = providerSelect.value;

  // 1. Mostra/Nascondi container campi per provider
  const allFieldDivs = document.querySelectorAll(".booking-provider-fields");
  allFieldDivs.forEach((div) => {
    div.hidden = div.id !== `fields-provider-${p}`;
  });

  // 2. Gating §12 su modalità e provider
  if (p === "zak") {
    if (optAuth) {
      optAuth.disabled = true;
      optAuth.textContent = "Authoritative (Non consentito per WuBook ZaK)";
    }
    if (optShadow) {
      optShadow.disabled = false;
      optShadow.textContent = "Shadow (Monitoraggio / Test — Fa fede Melpis locale)";
    }
    if (modeSelect.value === "authoritative") {
      modeSelect.value = "shadow";
    }
    if (warningBox && warningText) {
      warningBox.hidden = false;
      warningText.innerHTML = "<strong>Gating ZaK:</strong> WuBook ZaK è limitato alla modalità <em>Shadow</em> o <em>Local Only</em>. Le modalità Authoritative e Mirror sono bloccate perché non ancora certificate per la produzione.";
    }
    if (honestBox) honestBox.hidden = true;
  } else if (p === "internal") {
    if (optAuth) {
      optAuth.disabled = true;
      optAuth.textContent = "Authoritative (Non applicabile a motore interno)";
    }
    if (optShadow) {
      optShadow.disabled = true;
      optShadow.textContent = "Shadow (Non applicabile a motore interno)";
    }
    modeSelect.value = "local_only";
    if (warningBox) warningBox.hidden = true;
    if (honestBox) honestBox.hidden = true;
  } else {
    if (optAuth) {
      optAuth.disabled = false;
      optAuth.textContent = "Authoritative (Primario — Fa fede il gestionale)";
    }
    if (optShadow) {
      optShadow.disabled = false;
      optShadow.textContent = "Shadow (Monitoraggio / Test — Fa fede Melpis locale)";
    }

    if (p === "simplybook") {
      if (warningBox && warningText) {
        warningBox.hidden = false;
        warningText.innerHTML = "<strong>Attenzione SimplyBook:</strong> L'aggiornamento/modifica delle prenotazioni non è supportato dall'API di SimplyBook (solo creazione e cancellazione). Eventuali cambi orario richiedono una nuova prenotazione o gestione manuale.";
      }
      if (honestBox) honestBox.hidden = true;
    } else if (["calcom", "apaleo", "beds24"].includes(p)) {
      if (warningBox) warningBox.hidden = true;
      if (honestBox && honestText) {
        honestBox.hidden = false;
        const nome = PMS_PROVIDER_LABELS[p] || p;
        honestText.innerHTML = `<strong>Verifica live consigliata (${nome}):</strong> Tutti i contratti e le API sono validati tramite test automatici. Si consiglia comunque di effettuare una prenotazione reale di test prima dell'apertura completa al pubblico.`;
      }
    } else {
      if (warningBox) warningBox.hidden = true;
      if (honestBox) honestBox.hidden = true;
    }
  }

  _aggiornaDescrizioneModalitaBooking();
}

// Event Listeners Booking Provider Setup
document.getElementById("booking-provider-select")?.addEventListener("change", _aggiornaGatingProviderBooking);
document.getElementById("booking-mode-select")?.addEventListener("change", _aggiornaDescrizioneModalitaBooking);

document.getElementById("integ-booking-toggle-form-btn")?.addEventListener("click", () => {
  const formCard = document.getElementById("integ-booking-form-card");
  if (!formCard) return;
  const isHidden = formCard.hidden;
  formCard.hidden = !isHidden;
  if (isHidden) {
    _aggiornaGatingProviderBooking();
    formCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
});

document.getElementById("booking-form-cancel-btn")?.addEventListener("click", () => {
  const formCard = document.getElementById("integ-booking-form-card");
  if (formCard) formCard.hidden = true;
});

// Quick Mode Switcher
document.getElementById("integ-booking-quick-mode-btn")?.addEventListener("click", async () => {
  const select = document.getElementById("integ-booking-quick-mode-select");
  const btn = document.getElementById("integ-booking-quick-mode-btn");
  const feedbackEl = document.getElementById("integ-booking-feedback");
  if (!select || !btn) return;

  const mode = select.value;
  btn.disabled = true;
  const originalText = btn.textContent;
  btn.textContent = "Aggiorno…";
  if (feedbackEl) feedbackEl.hidden = true;

  try {
    const res = await apiFetch(`${API_BASE}/api/v1/integrations/booking/mode`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    const d = await res.json().catch(() => ({}));
    if (res.ok) {
      toast(d.message || `Modalità aggiornata a ${mode}`, "success");
      await caricaStatoBooking();
    } else if (res.status !== 403) {
      const errMsg = _estraiMessaggioErroreApi(res, d, "Errore durante l'aggiornamento della modalità.");
      if (feedbackEl) {
        feedbackEl.hidden = false;
        feedbackEl.className = "integ-test-feedback error";
        feedbackEl.textContent = errMsg;
      }
      toast(errMsg, "error");
    }
  } catch {
    toast("Errore di rete durante l'aggiornamento della modalità.", "error");
  } finally {
    btn.disabled = false;
    btn.textContent = originalText;
  }
});

// Disconnessione Gestionale
document.getElementById("integ-booking-disconnect-btn")?.addEventListener("click", async () => {
  const ok = await confermaDestructiva({
    titolo: "Disconnettere il gestionale di prenotazioni?",
    descrizione: "Le credenziali salvate verranno rimosse e Melpis tornerà a gestire le prenotazioni esclusivamente con il database locale integrato.",
    label: "Disconnetti gestionale",
  });
  if (!ok) return;

  try {
    const res = await apiFetch(`${API_BASE}/api/v1/integrations/booking`, { method: "DELETE" });
    const d = await res.json().catch(() => ({}));
    if (res.ok) {
      toast("Gestionale disconnesso con successo. Motore interno ripristinato.", "success");
      const formCard = document.getElementById("integ-booking-form-card");
      if (formCard) formCard.hidden = true;
      await caricaStatoBooking();
    } else if (res.status !== 403) {
      toast(_estraiMessaggioErroreApi(res, d, "Errore durante la rimozione del gestionale."), "error");
    }
  } catch {
    toast("Errore di connessione.", "error");
  }
});

// Salvataggio Configurazione Provider
document.getElementById("booking-config-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const provider = document.getElementById("booking-provider-select")?.value || "internal";
  const mode = document.getElementById("booking-mode-select")?.value || "local_only";
  const medicalDpa = Boolean(document.getElementById("booking-medical-dpa-check")?.checked);
  const submitBtn = document.getElementById("booking-form-submit-btn");
  const feedbackEl = document.getElementById("booking-form-feedback");

  // Controllo di sicurezza: WuBook ZaK non supporta authoritative
  if (provider === "zak" && mode === "authoritative") {
    toast("WuBook ZaK non supporta la modalità authoritative in produzione.", "error");
    return;
  }

  let credentials = {};
  let config = { medical_dpa_signed: medicalDpa };

  if (provider === "calcom") {
    const apiKey = document.getElementById("calcom-api-key")?.value.trim();
    const eventTypeId = document.getElementById("calcom-event-type-id")?.value.trim();
    const timezone = document.getElementById("calcom-timezone")?.value.trim() || "Europe/Rome";
    if (!apiKey) {
      toast("Inserisci l'API Key per Cal.com.", "error");
      return;
    }
    credentials = { api_key: apiKey };
    if (eventTypeId) config.event_type_id = eventTypeId;
    config.timezone = timezone;
  } else if (provider === "simplybook") {
    const companyLogin = document.getElementById("simplybook-company-login")?.value.trim();
    const apiKey = document.getElementById("simplybook-api-key")?.value.trim();
    if (!companyLogin || !apiKey) {
      toast("Inserisci Company Login e API Key per SimplyBook.me.", "error");
      return;
    }
    credentials = { company_login: companyLogin, api_key: apiKey };
  } else if (provider === "apaleo") {
    const clientId = document.getElementById("apaleo-client-id")?.value.trim();
    const clientSecret = document.getElementById("apaleo-client-secret")?.value.trim();
    const propertyId = document.getElementById("apaleo-property-id")?.value.trim();
    if (!clientId || !clientSecret) {
      toast("Inserisci Client ID e Client Secret per Apaleo.", "error");
      return;
    }
    credentials = { client_id: clientId, client_secret: clientSecret };
    if (propertyId) credentials.property_id = propertyId;
    config.channel_code = "Direct";
    config.timezone = "Europe/Rome";
  } else if (provider === "beds24") {
    const inviteCode = document.getElementById("beds24-invite-code")?.value.trim();
    const propertyId = document.getElementById("beds24-property-id")?.value.trim();
    if (!inviteCode || !propertyId) {
      toast("Inserisci Invite Code e ID Proprietà per Beds24.", "error");
      return;
    }
    credentials = { invite_code: inviteCode, property_id: propertyId };
    config.timezone = "Europe/Rome";
  } else if (provider === "zak") {
    const propertyId = document.getElementById("zak-property-id")?.value.trim();
    const apiKey = document.getElementById("zak-api-key")?.value.trim();
    if (!propertyId || !apiKey) {
      toast("Inserisci Property ID e Token API per WuBook ZaK.", "error");
      return;
    }
    credentials = { property_id: propertyId, api_key: apiKey };
  } else if (provider === "internal") {
    credentials = {};
    config.medical_dpa_signed = false;
  }

  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.textContent = "Salvataggio in corso…";
  }
  if (feedbackEl) feedbackEl.hidden = true;

  try {
    const res = await apiFetch(`${API_BASE}/api/v1/integrations/booking`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        provider,
        credentials,
        mode,
        config,
        is_active: true,
      }),
    });

    const d = await res.json().catch(() => ({}));
    if (res.ok) {
      toast(d.message || "Integrazione gestionale salvata con successo!", "success");
      const formCard = document.getElementById("integ-booking-form-card");
      if (formCard) formCard.hidden = true;
      ["calcom-api-key", "simplybook-api-key", "apaleo-client-secret", "beds24-invite-code", "zak-api-key"].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.value = "";
      });
      await caricaStatoBooking();
    } else if (res.status !== 403) {
      const errMsg = _estraiMessaggioErroreApi(res, d, "Errore durante il salvataggio dell'integrazione.");
      if (feedbackEl) {
        feedbackEl.hidden = false;
        feedbackEl.className = "integ-test-feedback error";
        feedbackEl.textContent = errMsg;
      }
      toast(errMsg, "error");
    }
  } catch {
    if (feedbackEl) {
      feedbackEl.hidden = false;
      feedbackEl.className = "integ-test-feedback error";
      feedbackEl.textContent = "Errore di connessione durante il salvataggio.";
    }
    toast("Impossibile salvare la configurazione del gestionale.", "error");
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = "Salva Configurazione";
    }
    for (const id of ["calcom-api-key", "simplybook-api-key", "apaleo-client-secret", "beds24-invite-code", "zak-api-key"]) {
      const input = document.getElementById(id);
      if (input) input.value = "";
    }
  }
});

/* ============================================================
   INTEGRAZIONE AIRTABLE (FASE 3)
   Invarianti: 1 (Tenant Isolation), 6 (GDPR Sanità), 10 (Zero Secrets)
   ============================================================ */

let _airtableBasesCache = [];

async function caricaStatoAirtable() {
  const statoEl = document.getElementById("integ-airtable-stato");
  const subEl = document.getElementById("integ-airtable-sub");
  const basesCountEl = document.getElementById("integ-airtable-bases-count");
  const lastUpdateEl = document.getElementById("integ-airtable-last-update");
  const basesListEl = document.getElementById("airtable-bases-list");
  const basesEmptyEl = document.getElementById("airtable-bases-empty");
  const valBaseSelect = document.getElementById("airtable-val-base-select");
  const subBaseSelect = document.getElementById("airtable-sub-base-select");
  const endpointUrlEl = document.getElementById("airtable-webhook-endpoint-url");

  if (endpointUrlEl) {
    const baseOrigin = window.location.origin || "";
    endpointUrlEl.textContent = `${baseOrigin}/api/v1/integrations/airtable/webhook`;
  }

  try {
    const res = await apiFetch(`${API_BASE}/api/v1/integrations/airtable/status`);
    if (!res.ok) {
      if (basesListEl) basesListEl.innerHTML = "";
      if (basesEmptyEl) basesEmptyEl.hidden = true;
      const d = await res.json().catch(() => ({}));
      const errMsg = _estraiMessaggioErroreApi(res, d, "Impossibile recuperare lo stato di Airtable.");
      if (res.status === 403) {
        _aggiornaBadgeStato(statoEl, res.mfaRequired ? "mfa_required" : "unauthorized");
        if (subEl) subEl.textContent = errMsg;
      } else {
        _aggiornaBadgeStato(statoEl, "error");
        if (subEl) subEl.textContent = errMsg;
      }
      return;
    }

    const d = await res.json().catch(() => ({}));
    const connections = Array.isArray(d.connections) ? d.connections : [];
    _airtableBasesCache = connections;

    const count = connections.length;
    if (basesCountEl) basesCountEl.textContent = String(count);

    if (d.is_configured && count > 0) {
      _aggiornaBadgeStato(statoEl, "connected");
      if (subEl) {
        subEl.textContent = `${count} ${count === 1 ? "Base collegata" : "Basi collegate"}`;
      }

      const lastUpdated = connections[0]?.updated_at;
      if (lastUpdateEl) {
        lastUpdateEl.textContent = lastUpdated
          ? new Date(lastUpdated).toLocaleDateString(localeCorrente(), { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" })
          : "—";
      }

      if (basesEmptyEl) basesEmptyEl.hidden = true;

      if (basesListEl) {
        basesListEl.innerHTML = connections
          .map((c) => {
            const safeName = DOMPurify.sanitize(c.base_name || "Base Airtable");
            const safeId = DOMPurify.sanitize(c.base_id);
            const safeVerticale = c.verticale ? `<span class="badge-status-honest" style="margin-left: 6px;">${DOMPurify.sanitize(c.verticale)}</span>` : "";
            const formattedDate = c.updated_at
              ? new Date(c.updated_at).toLocaleDateString(localeCorrente(), { day: "2-digit", month: "2-digit", year: "numeric" })
              : "";

            return `
              <div class="airtable-base-card" data-base-id="${safeId}">
                <div class="airtable-base-head">
                  <div>
                    <div class="airtable-base-name">${safeName} ${safeVerticale}</div>
                    <code class="airtable-base-id-badge">${safeId}</code>
                  </div>
                  <button type="button" class="btn-danger-outline btn-disconnect-airtable-base" data-base-id="${safeId}" data-base-name="${safeName}" style="padding: 4px 8px; font-size: 0.78rem;">
                    Disconnetti
                  </button>
                </div>
                <div class="airtable-base-meta">
                  <span>Aggiornato: ${formattedDate || "—"}</span>
                  <span class="badge-status connected" style="padding: 2px 6px; font-size: 0.74rem;">
                    <span class="badge-status-dot"></span> Attiva
                  </span>
                </div>
              </div>
            `;
          })
          .join("");
      }

      const selectOptions = '<option value="">Seleziona una Base connessa…</option>' +
        connections.map((c) => `<option value="${DOMPurify.sanitize(c.base_id)}">${DOMPurify.sanitize(c.base_name || c.base_id)} (${DOMPurify.sanitize(c.base_id)})</option>`).join("");

      if (valBaseSelect) valBaseSelect.innerHTML = selectOptions;
      if (subBaseSelect) subBaseSelect.innerHTML = selectOptions;

    } else {
      _aggiornaBadgeStato(statoEl, "disconnected");
      if (subEl) subEl.textContent = "Nessuna Base connessa";
      if (lastUpdateEl) lastUpdateEl.textContent = "—";
      if (basesListEl) basesListEl.innerHTML = "";
      if (basesEmptyEl) basesEmptyEl.hidden = false;
      if (valBaseSelect) valBaseSelect.innerHTML = '<option value="">Nessuna Base connessa</option>';
      if (subBaseSelect) subBaseSelect.innerHTML = '<option value="">Nessuna Base connessa</option>';
    }

    await caricaEventiWebhookAirtable();

  } catch {
    _aggiornaBadgeStato(statoEl, "error", "Errore di rete");
    if (subEl) subEl.textContent = "Errore di connessione con il server";
  }
}

async function disconnettiBaseAirtable(baseId, baseName) {
  if (!baseId) return;

  const confermato = await confermaDestructiva({
    titolo: "Disconnettere la Base Airtable?",
    descrizione: `Stai per rimuovere la connessione alla Base "${baseName}" (${baseId}). Le credenziali salvate verranno revocate e i workflow collegati a questa Base non riceveranno più dati.`,
    label: "Disconnetti Base",
  });

  if (!confermato) return;

  try {
    const res = await apiFetch(`${API_BASE}/api/v1/integrations/airtable?base_id=${encodeURIComponent(baseId)}`, {
      method: "DELETE",
    });

    const d = await res.json().catch(() => ({}));
    if (res.ok) {
      toast(d.message || `Base ${baseName} disconnessa con successo.`, "success");
      await caricaStatoAirtable();
    } else if (res.status === 403) {
      if (!res.mfaRequired) {
        toast(_estraiMessaggioErroreApi(res, d, "Permessi insufficienti per rimuovere la Base."), "error");
      }
    } else {
      toast(_estraiMessaggioErroreApi(res, d, "Errore durante la disconnessione della Base."), "error");
    }
  } catch {
    toast("Errore di connessione durante la rimozione della Base.", "error");
  }
}

async function caricaEventiWebhookAirtable() {
  const tbody = document.getElementById("airtable-events-tbody");
  const btn = document.getElementById("airtable-events-refresh-btn");
  if (!tbody) return;

  if (btn) {
    btn.disabled = true;
    btn.textContent = "Caricamento…";
  }

  try {
    const res = await apiFetch(`${API_BASE}/api/v1/integrations/airtable/webhooks/events?limit=20`);
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      const errMsg = _estraiMessaggioErroreApi(res, d, "Impossibile caricare gli eventi webhook.");
      tbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--text-muted); padding: 14px;">${DOMPurify.sanitize(errMsg)}</td></tr>`;
      return;
    }

    const events = await res.json().catch(() => []);
    if (!Array.isArray(events) || events.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--text-muted); padding: 14px;">Nessun evento webhook registrato di recente.</td></tr>';
      return;
    }

    tbody.innerHTML = events
      .map((ev) => {
        const safeBase = DOMPurify.sanitize(ev.base_id || "—");
        const safeWebhook = DOMPurify.sanitize(ev.webhook_id || "—");
        const safeEventId = DOMPurify.sanitize(ev.external_event_id || ev.id || "—");
        const safeStatus = DOMPurify.sanitize(ev.status || "pending");
        const dt = ev.created_at || ev.event_timestamp;
        const formattedDate = dt
          ? new Date(dt).toLocaleDateString(localeCorrente(), { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" })
          : "—";

        const statusClass = safeStatus === "processed" ? "connected" : safeStatus === "failed" ? "disconnected" : "pending";
        const statusLabel = safeStatus === "processed" ? "Elaborato" : safeStatus === "failed" ? "Fallito" : "In attesa";

        return `
          <tr>
            <td style="padding: 8px 10px; font-size: 0.78rem;">${formattedDate}</td>
            <td style="padding: 8px 10px;"><code style="font-size: 0.76rem;">${safeBase}</code></td>
            <td style="padding: 8px 10px;"><code style="font-size: 0.76rem;">${safeWebhook}</code></td>
            <td style="padding: 8px 10px; font-size: 0.76rem; color: var(--text-muted);">${safeEventId}</td>
            <td style="padding: 8px 10px;">
              <span class="badge-status ${statusClass}" style="padding: 2px 6px; font-size: 0.74rem;">
                <span class="badge-status-dot"></span> ${statusLabel}
              </span>
            </td>
          </tr>
        `;
      })
      .join("");

  } catch {
    tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--red); padding: 12px;">Errore di rete nel caricamento eventi.</td></tr>';
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Aggiorna eventi";
    }
  }
}

// Event Delegation su Lista Basi (Listener Hygiene: 1 solo listener permanente)
document.getElementById("airtable-bases-list")?.addEventListener("click", async (e) => {
  const btn = e.target.closest(".btn-disconnect-airtable-base");
  if (!btn) return;
  const baseId = btn.dataset.baseId;
  const baseName = btn.dataset.baseName || baseId;
  await disconnettiBaseAirtable(baseId, baseName);
});

// Toggle Form Connessione
document.getElementById("airtable-toggle-connect-btn")?.addEventListener("click", () => {
  const formCard = document.getElementById("integ-airtable-connect-card");
  if (!formCard) return;
  const isHidden = formCard.hidden;
  formCard.hidden = !isHidden;
  if (isHidden) {
    formCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
});

document.getElementById("airtable-connect-cancel-btn")?.addEventListener("click", () => {
  const formCard = document.getElementById("integ-airtable-connect-card");
  if (formCard) formCard.hidden = true;
});

// Submit Form Connessione PAT
document.getElementById("airtable-connect-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const token = document.getElementById("airtable-token")?.value.trim();
  const baseId = document.getElementById("airtable-base-id")?.value.trim();
  const baseName = document.getElementById("airtable-base-name")?.value.trim() || "";
  const submitBtn = document.getElementById("airtable-connect-submit-btn");
  const feedbackEl = document.getElementById("airtable-connect-feedback");

  if (!token || !baseId) {
    toast("Inserisci Personal Access Token e Base ID.", "error");
    return;
  }

  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.textContent = "Verifica in corso…";
  }
  if (feedbackEl) feedbackEl.hidden = true;

  try {
    const res = await apiFetch(`${API_BASE}/api/v1/integrations/airtable/connect`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        token,
        base_id: baseId,
        base_name: baseName,
      }),
    });

    const d = await res.json().catch(() => ({}));
    if (res.ok) {
      toast(d.message || "Base Airtable collegata con successo!", "success");
      const formCard = document.getElementById("integ-airtable-connect-card");
      if (formCard) formCard.hidden = true;
      const tokInput = document.getElementById("airtable-token");
      const baseInput = document.getElementById("airtable-base-id");
      const nameInput = document.getElementById("airtable-base-name");
      if (tokInput) tokInput.value = "";
      if (baseInput) baseInput.value = "";
      if (nameInput) nameInput.value = "";
      await caricaStatoAirtable();
    } else if (res.status === 403) {
      if (!res.mfaRequired) {
        // Messaggio esatto dal backend (es. AirtableMedicalPolicyError GDPR Art. 9) sanitizzato
        const errMsg = _estraiMessaggioErroreApi(res, d, "Connessione ad Airtable non consentita per policy di sicurezza.");
        if (feedbackEl) {
          feedbackEl.hidden = false;
          feedbackEl.className = "integ-test-feedback error";
          feedbackEl.textContent = errMsg;
        }
        toast(errMsg, "error", 8000);
      }
    } else {
      const errMsg = _estraiMessaggioErroreApi(res, d, "Errore durante la connessione della Base Airtable.");
      if (feedbackEl) {
        feedbackEl.hidden = false;
        feedbackEl.className = "integ-test-feedback error";
        feedbackEl.textContent = errMsg;
      }
      toast(errMsg, "error");
    }
  } catch {
    if (feedbackEl) {
      feedbackEl.hidden = false;
      feedbackEl.className = "integ-test-feedback error";
      feedbackEl.textContent = "Errore di connessione durante la verifica del token.";
    }
    toast("Impossibile contattare il server per la verifica del PAT.", "error");
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = "Verifica e Connetti Base";
    }
    document.getElementById("airtable-token").value = "";
  }
});

// Submit Form Validazione Schema
document.getElementById("airtable-validate-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const baseId = document.getElementById("airtable-val-base-select")?.value.trim();
  const table = document.getElementById("airtable-val-table")?.value.trim();
  const fieldsRaw = document.getElementById("airtable-val-fields")?.value.trim();
  const submitBtn = document.getElementById("airtable-validate-submit-btn");
  const feedbackEl = document.getElementById("airtable-validate-feedback");

  if (!baseId || !table || !fieldsRaw) {
    toast("Seleziona una Base e compila nome tabella e campi richiesti.", "error");
    return;
  }

  const requiredFields = fieldsRaw.split(",").map((s) => s.trim()).filter(Boolean);
  if (requiredFields.length === 0) {
    toast("Specifica almeno un campo richiesto per la validazione.", "error");
    return;
  }

  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.textContent = "Validazione in corso…";
  }
  if (feedbackEl) feedbackEl.hidden = true;

  try {
    const res = await apiFetch(`${API_BASE}/api/v1/integrations/airtable/validate-schema`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        base_id: baseId,
        table_id_or_name: table,
        required_fields: requiredFields,
      }),
    });

    const d = await res.json().catch(() => ({}));
    if (res.ok) {
      if (d.is_valid) {
        const matched = Array.isArray(d.available_fields) && d.available_fields.length > 0
          ? d.available_fields.join(", ")
          : "Tutti i campi richiesti sono presenti";
        if (feedbackEl) {
          feedbackEl.hidden = false;
          feedbackEl.className = "integ-test-feedback success";
          feedbackEl.innerHTML = `<strong>Schema Valido:</strong> La tabella "${DOMPurify.sanitize(d.table_name || table)}" è conforme. Campi verificati con successo: ${DOMPurify.sanitize(matched)}.`;
        }
        toast("Schema Airtable verificato con successo!", "success");
      } else {
        const missing = Array.isArray(d.missing_fields) && d.missing_fields.length > 0
          ? d.missing_fields.join(", ")
          : "Campi mancanti non specificati";
        const avail = Array.isArray(d.available_fields) && d.available_fields.length > 0
          ? `<br><small style="color: var(--text-muted);">Campi trovati nella tabella: ${DOMPurify.sanitize(d.available_fields.join(", "))}</small>`
          : "";
        if (feedbackEl) {
          feedbackEl.hidden = false;
          feedbackEl.className = "integ-test-feedback error";
          feedbackEl.innerHTML = `<strong>Schema non conforme:</strong> I seguenti campi richiesti non esistono nella tabella: <strong>${DOMPurify.sanitize(missing)}</strong>.${avail}`;
        }
        toast("La tabella specificata non contiene tutti i campi richiesti.", "warning");
      }
    } else if (res.status === 403) {
      if (!res.mfaRequired) {
        const errMsg = _estraiMessaggioErroreApi(res, d, "Permessi insufficienti per validare lo schema.");
        if (feedbackEl) {
          feedbackEl.hidden = false;
          feedbackEl.className = "integ-test-feedback error";
          feedbackEl.textContent = errMsg;
        }
        toast(errMsg, "error");
      }
    } else {
      const errMsg = _estraiMessaggioErroreApi(res, d, "Errore durante la validazione dello schema.");
      if (feedbackEl) {
        feedbackEl.hidden = false;
        feedbackEl.className = "integ-test-feedback error";
        feedbackEl.textContent = errMsg;
      }
      toast(errMsg, "error");
    }
  } catch {
    if (feedbackEl) {
      feedbackEl.hidden = false;
      feedbackEl.className = "integ-test-feedback error";
      feedbackEl.textContent = "Errore di connessione durante la validazione dello schema.";
    }
    toast("Impossibile convalidare lo schema su Airtable.", "error");
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = "Valida Schema Tabella";
    }
  }
});

// Submit Form Sottoscrizione Webhook
document.getElementById("airtable-webhook-sub-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const baseId = document.getElementById("airtable-sub-base-select")?.value.trim();
  const webhookId = document.getElementById("airtable-sub-webhook-id")?.value.trim();
  const macSecret = document.getElementById("airtable-sub-mac-secret")?.value.trim();
  const submitBtn = document.getElementById("airtable-sub-submit-btn");
  const feedbackEl = document.getElementById("airtable-sub-feedback");

  if (!baseId || !webhookId || !macSecret) {
    toast("Compila tutti i campi richiesti per la registrazione del webhook.", "error");
    return;
  }

  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.textContent = "Registrazione in corso…";
  }
  if (feedbackEl) feedbackEl.hidden = true;

  try {
    const res = await apiFetch(`${API_BASE}/api/v1/integrations/airtable/webhooks/subscribe`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        base_id: baseId,
        webhook_id: webhookId,
        mac_secret: macSecret,
        notification_url: `${window.location.origin || ""}/api/v1/integrations/airtable/webhook`,
      }),
    });

    const d = await res.json().catch(() => ({}));
    if (res.ok) {
      if (feedbackEl) {
        feedbackEl.hidden = false;
        feedbackEl.className = "integ-test-feedback success";
        feedbackEl.textContent = `Webhook registrato con successo! ID: ${d.webhook_id} (Base: ${d.base_id}).`;
      }
      toast("Sottoscrizione webhook registrata con successo!", "success");
      const whInput = document.getElementById("airtable-sub-webhook-id");
      const macInput = document.getElementById("airtable-sub-mac-secret");
      if (whInput) whInput.value = "";
      if (macInput) macInput.value = "";
      await caricaEventiWebhookAirtable();
    } else if (res.status === 403) {
      if (!res.mfaRequired) {
        const errMsg = _estraiMessaggioErroreApi(res, d, "Autorizzazione webhook non valida per questa Base.");
        if (feedbackEl) {
          feedbackEl.hidden = false;
          feedbackEl.className = "integ-test-feedback error";
          feedbackEl.textContent = errMsg;
        }
        toast(errMsg, "error");
      }
    } else {
      const errMsg = _estraiMessaggioErroreApi(res, d, "Errore durante la registrazione del webhook.");
      if (feedbackEl) {
        feedbackEl.hidden = false;
        feedbackEl.className = "integ-test-feedback error";
        feedbackEl.textContent = errMsg;
      }
      toast(errMsg, "error");
    }
  } catch {
    if (feedbackEl) {
      feedbackEl.hidden = false;
      feedbackEl.className = "integ-test-feedback error";
      feedbackEl.textContent = "Errore di rete durante la registrazione del webhook.";
    }
    toast("Impossibile registrare la sottoscrizione webhook.", "error");
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = "Registra Sottoscrizione Webhook";
    }
    document.getElementById("airtable-sub-mac-secret").value = "";
  }
});

// Refresh Eventi Webhook (1 solo listener permanente)
document.getElementById("airtable-events-refresh-btn")?.addEventListener("click", () => {
  caricaEventiWebhookAirtable();
});

/* ============================================================
   STAMPA REPORT + SCORCIATOIE TASTIERA
   ============================================================ */

document.getElementById("report-print")?.addEventListener("click", () => window.print());

(function inizializzaScorciatoie() {
  const ordineViste = [
    "panoramica", "onboarding", "assistente", "recensioni",
    "prenotazioni", "report", "documenti", "inbox",
    "impostazioni", "integrazioni", "audit",
  ];

  function pannelloScorciatoie() {
    let overlay = document.getElementById("shortcuts-overlay");
    if (overlay) { overlay.remove(); return; }
    overlay = document.createElement("div");
    overlay.id = "shortcuts-overlay";
    overlay.innerHTML = `
      <div class="shortcuts-panel" role="dialog" aria-modal="true" aria-label="Scorciatoie da tastiera">
        <h3>Scorciatoie da tastiera</h3>
        <dl>
          <dt>/</dt><dd>ricerca globale</dd>
          <dt>1 – 9</dt><dd>vai alle viste in ordine di menu</dd>
          <dt>0</dt><dd>vista Audit</dd>
          <dt>?</dt><dd>questo pannello</dd>
          <dt>Esc</dt><dd>chiudi pannelli e ricerca</dd>
        </dl>
        <p>Premi Esc o clicca fuori per chiudere.</p>
      </div>`;
    overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });
    document.body.appendChild(overlay);
  }

  document.addEventListener("keydown", (e) => {
    if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return;
    const t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;

    if (e.key === "?") { e.preventDefault(); pannelloScorciatoie(); return; }
    if (e.key === "Escape") { document.getElementById("shortcuts-overlay")?.remove(); return; }

    const n = Number(e.key);
    if (!Number.isNaN(n) && e.key !== " ") {
      const view = n === 0 ? "audit" : ordineViste[n - 1];
      if (view) {
        const btn = document.querySelector(`[data-view="${view}"]`);
        if (btn) { e.preventDefault(); btn.click(); }
      }
    }
  });
})();

/* ============================================================
   TEAM & COLLABORATORI — Gestione Membri e Limiti Piano
   ============================================================ */

dashboardTeamModule = window.MelpisDashboardTeam.create({
  API_BASE, apiFetch, _escapeHtml, _tDash, localeCorrente,
  toast, confermaDestructiva, _estraiMessaggioErroreApi,
  getContext: dashboardContextSnapshot, getSession: () => sessione, apriView,
});
({ caricaTeam } = dashboardTeamModule);
document.getElementById("team-org-selector")?.addEventListener("change", (event) => {
  dashboardInboxModule?.invalidate();
  localStorage.setItem("melpis_selected_organization", event.target.value);
  window.location.reload();
});

async function accettaInvitoDaLink() {
  const params = new URLSearchParams(window.location.hash.slice(1));
  const token = params.get("team-invite") || sessionStorage.getItem("melpis_pending_team_invite");
  if (!token) return false;
  sessionStorage.removeItem("melpis_pending_team_invite");
  history.replaceState(null, "", window.location.pathname + window.location.search);
  const res = await apiFetch(`${API_BASE}/api/team/invitations/accept`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token })
  });
  if (!res.ok) {
    toast(_tDash("team.invite_invalid", "Invito non valido o scaduto. Chiedi un nuovo invito."), "error");
    return false;
  }
  const accepted = await res.json();
  dashboardInboxModule?.invalidate();
  localStorage.setItem("melpis_selected_organization", accepted.organization_id);
  window.location.replace("/app/team");
  return true;
}

