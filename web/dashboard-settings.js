(function attachDashboardSettings(root, factory) {
  const api = factory(root);
  if (root) root.MelpisDashboardSettings = api;
})(typeof window !== "undefined" ? window : globalThis, function createDashboardSettings(root) {
  "use strict";

  return Object.freeze({
    create(deps) {
      const {
        API_BASE, apiFetch, getContext, getRole, t, _tDash, sanitize, toast, securityStatus,
        localeCorrente, navigateTo, normalizeSettingsTab, settingsSearchForTab,
        closeAccountMenu, loadMfaStatus, getIntegrationsApi, refreshSidebarIdentity = () => {},
        redirect = (url) => { root.location.href = url; },
      } = deps;
      const document = root.document;
      const topbarTitle = document.getElementById("topbar-title");
      const scopeFlights = new Map();
      const checkoutAttempts = new Map();
      const titleKeys = {
        piano: "title_account", fatturazione: "title_account", whatsapp: "title_whatsapp",
        instagram: "title_instagram", calendar: "title_calendar", reviews: "title_reviews_settings",
        "booking-pms": "title_pms", airtable: "title_airtable",
      };
      const legacyTabs = {
        generale: "generale", integrazioni: "whatsapp", audit: "audit", account: "piano",
        piano: "piano", fatturazione: "fatturazione", profilo: "profilo",
        "assistente-regole": "assistente-regole", assistente: "assistente-regole",
        sicurezza: "sicurezza", whatsapp: "whatsapp", instagram: "instagram",
        calendar: "calendar", reviews: "reviews", webhook: "webhook",
        "booking-pms": "booking-pms", booking: "booking-pms", pms: "booking-pms", airtable: "airtable",
      };
      const integrationTabs = new Set(["whatsapp", "instagram", "calendar", "reviews", "webhook", "booking-pms", "airtable"]);
      const accountPlans = [
        { slug: "starter", nome: "Essenziale", prezzo: "€29", cadenza: "/mese", limite: "500 messaggi / mese", features: [
          "1 numero WhatsApp Business", "1 utente staff (operatore)", "Gestione orari e listino servizi", "Supporto standard",
        ], popolare: false },
        { slug: "pro", nome: "Crescita", prezzo: "€69", cadenza: "/mese", limite: "2.000 messaggi / mese", features: [
          "WhatsApp + Instagram Direct", "Fino a 3 utenti staff (operatori)",
          "Recensioni Google, previa verifica di disponibilità dell'account", "Sincronizzazione Google Calendar", "Supporto prioritario",
        ], popolare: true },
        { slug: "business", nome: "Scala", prezzo: "€149", cadenza: "/mese", limite: "10.000 messaggi / mese", features: [
          "Conoscenza AI con documenti", "Operatori configurabili",
          "Canali disponibili in base alle integrazioni verificate", "Supporto dedicato 1-to-1",
        ], popolare: false },
      ];
      let active = false;
      let epoch = 0;
      let scope = null;
      let activeTab = "generale";
      let timezoneLoaded = false;
      let timezoneFlight = null;
      let auditOffset = 0;
      let auditHasMore = false;
      let auditEvents = [];

      function scopeKey(context = getContext()) {
        return JSON.stringify([context?.userId, context?.sessionOrganizationId, context?.selectedOrganizationId]);
      }

      function capture() { return { context: getContext(), epoch }; }

      function current(token) {
        const now = getContext();
        return active && token?.epoch === epoch
          && ["impostazioni", "account"].includes(now?.view)
          && Boolean(now?.userId && now?.sessionOrganizationId)
          && JSON.stringify(now) === JSON.stringify(token.context);
      }

      function settingsText(key, fallback) {
        const translated = t(key, { defaultValue: fallback });
        return translated === key ? fallback : translated;
      }

      function canManageBilling() { return ["owner", "manager"].includes(getRole()); }

      function setTitle(tab) {
        if (!topbarTitle) return;
        topbarTitle.dataset.i18n = `dashboard:topbar.${titleKeys[tab] || "title_impostazioni"}`;
        topbarTitle.textContent = t(topbarTitle.dataset.i18n);
      }

      function openCategory(category = "generale") {
        const tab = normalizeSettingsTab(category);
        const search = settingsSearchForTab(root.location.search, tab);
        const navigated = navigateTo("impostazioni", { search, hash: root.location.hash, replaceSuffix: true });
        if (navigated) closeAccountMenu();
        return navigated;
      }

      function activateCategory(category) {
        const tab = legacyTabs[category] || category || "generale";
        if (integrationTabs.has(activeTab) && !integrationTabs.has(tab)) getIntegrationsApi()?.onExit();
        document.querySelectorAll("[data-settings-cat-btn]").forEach((button) => {
          const selected = button.dataset.settingsCatBtn === tab;
          button.classList.toggle("active", selected);
          button.setAttribute("aria-selected", String(selected));
        });
        document.querySelectorAll("[data-settings-panel]").forEach((panel) => {
          panel.hidden = panel.dataset.settingsPanel !== tab;
        });
        activeTab = tab;
        setTitle(tab);
        if (tab === "generale") void loadTimezone();
        else if (integrationTabs.has(tab)) getIntegrationsApi()?.onEnter(tab);
        else if (tab === "piano" || tab === "fatturazione") void loadAccount();
        else if (tab === "audit") void loadAudit();
        else if (tab === "sicurezza") loadMfaStatus();
      }

      function navigateTab(category) {
        const tab = normalizeSettingsTab(category);
        const search = settingsSearchForTab(root.location.search, tab);
        return navigateTo("impostazioni", { search, hash: root.location.hash, replaceSuffix: true });
      }

      function filterSettings() {
        const input = document.getElementById("settings-search-input");
        if (!input) return;
        const query = input.value.trim().toLowerCase();
        let matchCount = 0;
        document.querySelectorAll("[data-settings-cat-btn]").forEach((button) => {
          const text = `${button.textContent} ${button.dataset.keywords || ""}`.toLowerCase();
          const deniedBilling = !canManageBilling() && ["piano", "fatturazione"].includes(button.dataset.settingsCatBtn);
          const match = !deniedBilling && (!query || text.includes(query));
          button.style.display = match ? "" : "none";
          if (match) matchCount += 1;
        });
        document.querySelectorAll(".settings-nav-group").forEach((group) => {
          const visible = group.querySelectorAll('[data-settings-cat-btn]:not([style*="display: none"])');
          group.style.display = visible.length ? "" : "none";
        });
        const emptyMessage = document.getElementById("settings-search-empty");
        if (emptyMessage) emptyMessage.hidden = matchCount > 0;
      }

      function clearFeedback() {
        for (const id of ["settings-timezone-status", "account-status"]) {
          const status = document.getElementById(id);
          if (status) { status.textContent = ""; status.style.color = ""; status.hidden = id === "account-status"; }
        }
      }

      function clearScopeData() {
        timezoneLoaded = false;
        timezoneFlight = null;
        auditOffset = 0;
        auditHasMore = false;
        auditEvents = [];
        const timezone = document.getElementById("settings-timezone");
        if (timezone) timezone.replaceChildren();
        const timezoneSave = document.getElementById("settings-timezone-save");
        if (timezoneSave) timezoneSave.disabled = true;
        const auditList = document.getElementById("audit-list");
        if (auditList) auditList.replaceChildren();
        const auditCount = document.getElementById("audit-count");
        if (auditCount) auditCount.textContent = "";
        for (const [id, value] of Object.entries({
          "account-stato": "…", "account-plan-nome": "…", "account-plan-prezzo": "",
          "account-rinnovo": "",
        })) {
          const element = document.getElementById(id);
          if (element) element.textContent = value;
        }
        const sidebarPlan = document.getElementById("sidebar-account-plan");
        if (getContext()?.userId) refreshSidebarIdentity();
        else if (sidebarPlan) sidebarPlan.textContent = _tDash("sidebar.menu_piano", "Piano e abbonamento");
        document.getElementById("account-plans")?.replaceChildren();
        clearFeedback();
      }

      async function loadTimezone() {
        const token = capture();
        if (!current(token) || activeTab !== "generale") return null;
        if (timezoneLoaded) return true;
        const flightKey = `${scope}:${token.epoch}`;
        if (timezoneFlight?.key === flightKey) return timezoneFlight.promise;
        const select = document.getElementById("settings-timezone");
        const saveButton = document.getElementById("settings-timezone-save");
        const status = document.getElementById("settings-timezone-status");
        if (!select || !saveButton) return null;
        if (status) { status.textContent = t("settings:timezone_section.load_loading"); status.style.color = ""; }
        saveButton.disabled = true;
        const promise = (async () => {
          try {
            const response = await apiFetch(`${API_BASE}/api/impostazioni/organizzazione`);
            if (!current(token)) return null;
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            if (!current(token)) return null;
            select.replaceChildren();
            (data.timezone_disponibili || []).forEach((timezone) => {
              const option = document.createElement("option");
              option.value = timezone;
              option.textContent = timezone;
              option.selected = timezone === data.timezone;
              select.appendChild(option);
            });
            if (![...select.options].some((option) => option.value === data.timezone)) {
              const option = document.createElement("option");
              option.value = data.timezone;
              option.textContent = data.timezone;
              option.selected = true;
              select.appendChild(option);
            }
            timezoneLoaded = true;
            saveButton.disabled = false;
            if (status) status.textContent = "";
            return true;
          } catch {
            if (!current(token)) return null;
            saveButton.disabled = true;
            if (status) { status.textContent = settingsText("settings:timezone_section.load_error", "Impossibile caricare il fuso orario. Riprova."); status.style.color = "var(--red)"; }
            return null;
          } finally {
            if (timezoneFlight?.promise === promise) timezoneFlight = null;
          }
        })();
        timezoneFlight = { key: flightKey, promise };
        return promise;
      }

      async function saveTimezone() {
        const token = capture();
        if (!current(token) || !timezoneLoaded || activeTab !== "generale") return null;
        const key = `${scope}:timezone`;
        if (scopeFlights.has(key)) return scopeFlights.get(key);
        const saveButton = document.getElementById("settings-timezone-save");
        const status = document.getElementById("settings-timezone-status");
        const select = document.getElementById("settings-timezone");
        if (saveButton) saveButton.disabled = true;
        if (status) { status.textContent = t("settings:timezone_section.save_loading"); status.style.color = ""; }
        const operation = (async () => {
          try {
            const response = await apiFetch(`${API_BASE}/api/impostazioni/organizzazione`, {
              method: "PUT", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ timezone: select?.value }),
            });
            const currentAfterRequest = current(token);
            await response.json().catch(() => ({}));
            if (!currentAfterRequest || !current(token)) return null;
            if (!response.ok) {
              if (status) { status.textContent = settingsText("settings:timezone_section.save_error", "Impossibile salvare il fuso orario."); status.style.color = "var(--red)"; }
              return null;
            }
            if (status) securityStatus(status, t("settings:timezone_section.save_success"));
            return true;
          } catch {
            if (!current(token)) return null;
            if (status) { status.textContent = settingsText("settings:timezone_section.connection_error", "Errore di connessione."); status.style.color = "var(--red)"; }
            return null;
          } finally {
            if (scopeFlights.get(key) === operation) scopeFlights.delete(key);
            if (saveButton && scope === scopeKey(getContext()) && active) saveButton.disabled = !timezoneLoaded;
          }
        })();
        scopeFlights.set(key, operation);
        return operation;
      }

      function accountStatus(message) {
        const status = document.getElementById("account-status");
        if (status) { status.hidden = false; status.textContent = message; status.style.color = "var(--red)"; }
      }

      function accountStatePill(state) {
        const pill = document.getElementById("account-stato");
        if (!pill) return;
        if (state === "active") { pill.textContent = "Attivo"; pill.className = "account-stato-pill"; }
        else if (state === "trialing") { pill.textContent = "Prova gratuita"; pill.className = "account-stato-pill stato-trial"; }
        else if (state === "canceled" || state === "past_due") { pill.textContent = "In pausa"; pill.className = "account-stato-pill stato-pausa"; }
        else { pill.textContent = "Nessun abbonamento"; pill.className = "account-stato-pill stato-pausa"; }
      }

      async function loadAccount() {
        const token = capture();
        if (!current(token) || !canManageBilling()) return null;
        const status = document.getElementById("account-status");
        try {
          const response = await apiFetch(`${API_BASE}/api/billing/subscription`);
          if (!current(token)) return null;
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const subscription = await response.json();
          if (!current(token)) return null;
          const state = subscription.subscription_status || "none";
          accountStatePill(state);
          const currentPlan = accountPlans.find((plan) => plan.slug === subscription.plan);
          const name = currentPlan ? currentPlan.nome : "Nessun piano";
          const price = currentPlan ? `${currentPlan.prezzo}${currentPlan.cadenza || "/mese"}` : "";
          const nameEl = document.getElementById("account-plan-nome");
          const priceEl = document.getElementById("account-plan-prezzo");
          const sidebarPlan = document.getElementById("sidebar-account-plan");
          if (nameEl) nameEl.textContent = name;
          if (priceEl) priceEl.textContent = price;
          if (sidebarPlan) sidebarPlan.textContent = currentPlan ? `Piano ${currentPlan.nome}` : "Piano e abbonamento";
          const renewal = document.getElementById("account-rinnovo");
          const formatDate = (iso) => {
            const date = new Date(iso);
            if (Number.isNaN(date.getTime())) return "";
            return root.MelpisI18n?.formatDate?.(date) || date.toLocaleDateString(localeCorrente());
          };
          if (renewal) {
            if (subscription.trial_end) renewal.textContent = `Prova gratuita attiva fino al ${formatDate(subscription.trial_end)}.`;
            else if (state === "canceled") renewal.textContent = "Abbonamento disattivato: il servizio resta attivo fino al termine del periodo pagato.";
            else if (subscription.current_period_end) renewal.textContent = `Prossimo rinnovo programmato: ${formatDate(subscription.current_period_end)}.`;
            else renewal.textContent = "Nessun rinnovo programmato.";
          }
          const wrap = document.getElementById("account-plans");
          if (wrap) {
            wrap.replaceChildren();
            accountPlans.forEach((plan) => {
              const isCurrent = plan.slug === subscription.plan;
              const card = document.createElement("div");
              card.className = `account-plan-card${isCurrent ? " account-plan-attuale" : ""}${plan.popolare ? " popolare" : ""}`;
              const popularBadge = plan.popolare ? '<span class="account-badge-popolare">Consigliato</span>' : "";
              const activeBadge = isCurrent ? '<span class="account-stato-pill">Piano Attivo</span>' : "";
              const features = plan.features.map((feature) => `<li class="account-plan-feat-item"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" class="account-feat-check"><polyline points="20 6 9 17 4 12"/></svg><span>${sanitize(feature)}</span></li>`).join("");
              card.innerHTML = `${popularBadge}<div class="dash-card-header" style="margin-bottom: 0;"><span class="dash-card-title">${sanitize(plan.nome)}</span>${activeBadge}</div><div class="account-plan-price-row"><span class="account-plan-price-amount">${sanitize(plan.prezzo)}</span><span class="account-plan-price-period">${sanitize(plan.cadenza || "/mese")}</span></div><p class="account-plan-limit">${sanitize(plan.limite)}</p><ul class="account-plan-feat-list">${features}</ul>`;
              const actions = document.createElement("div");
              actions.className = "settings-actions";
              const button = document.createElement("button");
              button.type = "button";
              button.className = isCurrent ? "report-refresh" : (plan.popolare ? "review-analyze" : "report-refresh");
              button.textContent = isCurrent ? "Piano in uso" : (state === "canceled" ? `Riattiva ${plan.nome}` : `Passa a ${plan.nome}`);
              if (isCurrent) button.disabled = true;
              else button.addEventListener("click", () => { void changePlan(plan.slug); });
              actions.appendChild(button);
              card.appendChild(actions);
              wrap.appendChild(card);
            });
          }
          if (status) status.hidden = true;
          return subscription;
        } catch {
          if (!current(token)) return null;
          accountStatus("Impossibile caricare lo stato dell'abbonamento: torna su questa sezione per riprovare.");
          return null;
        }
      }

      async function changePlan(slug) {
        const token = capture();
        if (!current(token) || !canManageBilling()) return null;
        const key = `${scope}:checkout:${slug}`;
        if (scopeFlights.has(key)) return scopeFlights.get(key);
        const attemptKey = `${scope}:${slug}`;
        const previous = checkoutAttempts.get(attemptKey);
        const attempt = previous && Date.now() - previous.created < 25 * 60 * 1000
          ? previous : { key: root.crypto.randomUUID(), created: Date.now() };
        checkoutAttempts.set(attemptKey, attempt);
        const operation = (async () => {
          try {
            const response = await apiFetch(`${API_BASE}/api/billing/create-checkout-session`, {
              method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": attempt.key },
              body: JSON.stringify({ plan: slug, interval: "monthly", success_url: root.location.origin + "/app/", cancel_url: root.location.origin + "/app/" }),
            });
            const currentAfterRequest = current(token);
            const data = await response.json().catch(() => ({}));
            if (!currentAfterRequest || !current(token)) return null;
            if (!response.ok) {
              if (response.status === 403) return null;
              if (response.status === 409 && data.detail?.code === "trial_active_checkout_deferred") {
                const deadline = new Date(data.detail.trial_end);
                const formatted = Number.isNaN(deadline.getTime())
                  ? "la scadenza della prova gratuita"
                  : (root.MelpisI18n?.formatDate?.(deadline) || deadline.toLocaleDateString(localeCorrente()));
                accountStatus(`La prova gratuita resta attiva fino a ${formatted}. Non è richiesta una carta; potrai avviare l’abbonamento dopo la scadenza.`);
                const status = document.getElementById("account-status");
                if (status) status.style.color = "var(--green-deep)";
                return null;
              }
              if (response.status === 409 && typeof data.detail === "string" && data.detail.includes("subscription already exists")) {
                accountStatus("Per modificare un abbonamento esistente, usa il portale di fatturazione.");
                return null;
              }
              throw new Error("checkout non disponibile");
            }
            if (data.url) redirect(data.url);
            return data;
          } catch {
            if (!current(token)) return null;
            accountStatus("Impossibile avviare il cambio piano. Riprova più tardi.");
            return null;
          } finally {
            if (scopeFlights.get(key) === operation) scopeFlights.delete(key);
          }
        })();
        scopeFlights.set(key, operation);
        return operation;
      }

      async function openBillingPortal() {
        const token = capture();
        if (!current(token) || !canManageBilling()) return null;
        const key = `${scope}:billing-portal`;
        if (scopeFlights.has(key)) return scopeFlights.get(key);
        const operation = (async () => {
          try {
            const response = await apiFetch(`${API_BASE}/api/billing/create-portal-session`, { method: "POST" });
            const currentAfterRequest = current(token);
            const data = await response.json().catch(() => ({}));
            if (!currentAfterRequest || !current(token)) return null;
            if (!response.ok) { if (response.status === 403) return null; throw new Error("portale non disponibile"); }
            if (data.url) redirect(data.url);
            return data;
          } catch {
            if (!current(token)) return null;
            accountStatus("Impossibile aprire il portale Stripe. Riprova più tardi.");
            return null;
          } finally {
            if (scopeFlights.get(key) === operation) scopeFlights.delete(key);
          }
        })();
        scopeFlights.set(key, operation);
        return operation;
      }

      function renderAudit() {
        const list = document.getElementById("audit-list");
        const count = document.getElementById("audit-count");
        if (!list) return;
        list.replaceChildren();
        if (!auditEvents.length) {
          const empty = document.createElement("p");
          empty.className = "inbox-empty";
          empty.textContent = "Nessuna azione registrata: le modifiche a impostazioni, prenotazioni e documenti compariranno qui.";
          list.appendChild(empty);
          if (count) count.textContent = "";
          return;
        }
        auditEvents.forEach((event) => {
          const item = document.createElement("div");
          item.className = "audit-item";
          const date = new Date(event.created_at).toLocaleString(localeCorrente(), { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
          const details = event.details && Object.keys(event.details).length
            ? Object.entries(event.details).map(([key, value]) => `${key}: ${value}`).join(" · ") : "";
          item.innerHTML = `<div class="audit-main"><strong>${sanitize(auditActionLabel[event.action] || event.action)}</strong>${details ? `<span class="audit-details">${sanitize(details)}</span>` : ""}</div><div class="audit-meta"><span>${sanitize(event.user_email || "sistema")}</span><time>${sanitize(date)}</time></div>`;
          list.appendChild(item);
        });
        if (count) count.textContent = `${auditOffset} eventi`;
        if (auditHasMore) {
          const button = document.createElement("button");
          button.type = "button";
          button.id = "audit-load-more";
          button.className = "inbox-load-more";
          button.textContent = "Carica altri eventi";
          button.addEventListener("click", () => { button.disabled = true; button.textContent = "Carico…"; void loadAudit({ append: true }); });
          list.appendChild(button);
        }
      }

      const auditActionLabel = {
        "profilo.aggiornato": "Profilo aggiornato", "org.timezone_updated": "Fuso orario aggiornato",
        "prenotazione.confermata": "Prenotazione confermata", "prenotazione.rifiutata": "Prenotazione rifiutata",
        "prenotazione.annullata": "Prenotazione annullata", documento_eliminato: "Documento eliminato",
      };

      async function loadAudit({ append = false } = {}) {
        const token = capture();
        if (!current(token) || activeTab !== "audit") return null;
        const list = document.getElementById("audit-list");
        if (!list) return null;
        if (!append) {
          auditOffset = 0;
          auditHasMore = false;
          auditEvents = [];
          list.innerHTML = '<div class="skeleton skeleton-line-lg"></div><div class="skeleton skeleton-line-lg"></div><div class="skeleton skeleton-line-lg"></div>';
        }
        const key = `${scope}:audit:${append ? auditOffset : 0}`;
        if (scopeFlights.has(key)) return scopeFlights.get(key);
        const operation = (async () => {
          try {
            const response = await apiFetch(`${API_BASE}/api/audit?limit=20&offset=${auditOffset}`);
            if (!current(token)) return null;
            if (!response.ok) {
              list.textContent = "";
              const empty = document.createElement("p");
              empty.className = "inbox-empty";
              empty.textContent = "Registro non disponibile.";
              list.appendChild(empty);
              return null;
            }
            const data = await response.json();
            if (!current(token)) return null;
            auditHasMore = Boolean(data.has_more);
            auditEvents = append ? [...auditEvents, ...(data.eventi || [])] : (data.eventi || []);
            auditOffset = auditEvents.length;
            renderAudit();
            return data;
          } catch {
            if (!current(token)) return null;
            list.textContent = "";
            const empty = document.createElement("p");
            empty.className = "inbox-empty";
            empty.textContent = "Registro non disponibile.";
            list.appendChild(empty);
            return null;
          } finally {
            if (scopeFlights.get(key) === operation) scopeFlights.delete(key);
          }
        })();
        scopeFlights.set(key, operation);
        return operation;
      }

      async function loadAccountForLegacyRoute() {
        return loadAccount();
      }

      document.querySelectorAll("[data-settings-cat-btn]").forEach((button) => {
        button.addEventListener("click", () => navigateTab(button.dataset.settingsCatBtn));
      });
      document.getElementById("settings-search-input")?.addEventListener("input", filterSettings);
      document.getElementById("settings-timezone-save")?.addEventListener("click", () => { void saveTimezone(); });
      document.getElementById("account-portal-btn")?.addEventListener("click", () => { void openBillingPortal(); });
      document.getElementById("account-cancel-btn")?.addEventListener("click", () => { void openBillingPortal(); });

      function onEnter(category) {
        const context = getContext();
        if (context?.view !== "impostazioni" || !context?.userId || !context?.sessionOrganizationId) return;
        const nextScope = scopeKey(context);
        if (scope !== nextScope) {
          epoch += 1;
          active = false;
          getIntegrationsApi()?.invalidate();
          clearScopeData();
          scope = nextScope;
        } else {
          epoch += 1;
        }
        active = true;
        const tab = normalizeSettingsTab(category);
        activateCategory(tab);
      }

      function onExit() {
        active = false;
        epoch += 1;
        getIntegrationsApi()?.onExit();
        if (document.getElementById("settings-timezone-status")?.textContent === t("settings:timezone_section.load_loading")
          || document.getElementById("settings-timezone-status")?.textContent === t("settings:timezone_section.save_loading")) {
          const status = document.getElementById("settings-timezone-status");
          if (status) status.textContent = "";
        }
      }

      function invalidate() {
        active = false;
        epoch += 1;
        scope = null;
        getIntegrationsApi()?.invalidate();
        clearScopeData();
      }

      function refreshLanguage() {
        if (!active) return;
        setTitle(activeTab);
        getIntegrationsApi()?.refreshLanguage();
        if (activeTab === "audit" && auditEvents.length) renderAudit();
      }

      return Object.freeze({
        openCategory, onEnter, onExit, invalidate, refreshLanguage,
        loadAccount: loadAccountForLegacyRoute,
      });
    },
  });
});
