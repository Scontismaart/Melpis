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
let dashboardAiSettingsModule = null;
let dashboardSettingsModule = null;
let dashboardIntegrationsModule = null;
let _estraiMessaggioErroreApi;
let gestisciCalendarRedirect;
let gestisciReviewsRedirect;
let dashboardBookingsModule = null;
let dashboardInboxModule = null;
let aggiornaPrenotazioni;
let aggiornaSemaforo;
let apriBookingModal;

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

const dashboardAuthTransport = window.MelpisDashboardAuthTransport.create({
  API_BASE,
  fetch: window.fetch.bind(window),
  document,
  storage: window.localStorage,
  onNetworkError: () => segnalaErroreRete(),
  onNetworkOk: () => segnaReteOk(),
  invalidateSession: () => invalidaSessione(),
  redirectToLogin: () => vaiAdAccesso(),
  showMfaRequired: (openSecurity) => {
    const mfaMessage = typeof t === "function"
      ? t("settings:mfa.required_toast")
      : "Questa operazione richiede una verifica in due passaggi.";
    toast(
      mfaMessage,
      "warning",
      8000,
      {
        testo: typeof t === "function" ? t("settings:mfa.open_security") : "Apri Sicurezza",
        onClick: openSecurity,
      }
    );
  },
  openSecurity: () => {
    if (window.MelpisMfa?.openForStepUp) window.MelpisMfa.openForStepUp();
    else apriVistaImpostazioni("sicurezza");
  },
});
const { apiFetch, tentaRefresh } = dashboardAuthTransport;

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
  dashboardAiSettingsModule?.invalidate();
  dashboardSettingsModule?.invalidate();
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
  if (activeDashboardView === "recensioni") {
    dashboardReviewsModule?.onExit();
    dashboardIntegrationsModule?.onOverviewExit();
  }
  if (activeDashboardView === "conoscenza" || activeDashboardView === "documenti") dashboardKnowledgeModule?.onExit();
  if (activeDashboardView === "team") dashboardTeamModule?.onExit();
  if (activeDashboardView === "assistente") dashboardAiSimulatorModule?.onExit();
  if (activeDashboardView === "prenotazioni") dashboardBookingsModule?.onExit();
  if (activeDashboardView === "inbox") dashboardInboxModule?.onExit();
  if (activeDashboardView === "configurazione-ai") dashboardAiSettingsModule?.onExit();
  if (activeDashboardView === "impostazioni" || activeDashboardView === "account") dashboardSettingsModule?.onExit();
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
    dashboardSettingsModule?.onEnter(tab);
  }
  if (viewName === "account") dashboardSettingsModule?.loadAccount();

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
    dashboardIntegrationsModule?.onOverviewEnter();
    aggiornaRecensioni();
  }
  if (viewName === "documenti" || viewName === "conoscenza") {
    dashboardKnowledgeModule?.onEnter();
    aggiornaConoscenzaCompleta();
  }
  if (viewName === "configurazione-ai") dashboardAiSettingsModule?.onEnter();

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
  return dashboardSettingsModule?.openCategory(cat) || false;
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
  if (dest.tab) apriVistaImpostazioni(dest.tab);
  return true;
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

dashboardAiSettingsModule = window.MelpisDashboardAiSettings.create({
  API_BASE, apiFetch, getContext: dashboardContextSnapshot,
  getRole: () => sessione?.ruolo || null,
  setProfile: (profile) => { dbProfileRecord = profile; },
  sanitize: _sanitize, escapeHtml: _escapeHtml, toast, translate: t, securityStatus,
});

dashboardIntegrationsModule = window.MelpisDashboardIntegrations.create({
  API_BASE, apiFetch, getContext: dashboardContextSnapshot,
  toast, confermaDestructiva, localeCorrente, apriVistaImpostazioni,
  _tDash, _sanitize, aggiornaRecensioni,
});
({ _estraiMessaggioErroreApi, gestisciCalendarRedirect, gestisciReviewsRedirect } = dashboardIntegrationsModule);

dashboardSettingsModule = window.MelpisDashboardSettings.create({
  API_BASE, apiFetch, getContext: dashboardContextSnapshot,
  getRole: () => sessione?.ruolo || null, t, _tDash, sanitize: _sanitize, toast, securityStatus,
  localeCorrente, navigateTo: (...args) => dashboardRouter?.navigateTo(...args) || false,
  normalizeSettingsTab: (tab) => window.MelpisDashboardRouter?.normalizeSettingsTab(tab) || "generale",
  settingsSearchForTab: (...args) => window.MelpisDashboardRouter?.settingsSearchForTab(...args) || "",
  closeAccountMenu: chiudiSidebarAccountMenu,
  refreshSidebarIdentity: aggiornaBottoneAccesso,
  loadMfaStatus: () => window.MelpisMfa?.loadStatus?.(),
  getIntegrationsApi: () => dashboardIntegrationsModule,
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
  dashboardAiSettingsModule?.invalidate();
  dashboardSettingsModule?.invalidate();
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
      if (row.dataset.tab) apriVistaImpostazioni(row.dataset.tab);
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
    dashboardAiSettingsModule?.refreshLanguage();
    dashboardSettingsModule?.refreshLanguage();
    if (typeof toast === "function") {
      toast(typeof t === "function" ? t("dashboard.toast.saved") : "Lingua aggiornata", "success");
    }
  });
})();



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

