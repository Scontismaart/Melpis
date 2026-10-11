(function attachDashboardAiSettings(root, factory) {
  const api = factory(root);
  if (root) root.MelpisDashboardAiSettings = api;
})(typeof window !== "undefined" ? window : globalThis, function createDashboardAiSettings(root) {
  "use strict";

  return Object.freeze({
    create(deps) {
      const {
        API_BASE, apiFetch, getContext, setProfile, getRole = () => null,
        sanitize, escapeHtml, toast, translate, securityStatus,
      } = deps;
      const document = root.document;
      const window = root;
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
      const validVerticals = ["ristorante", "parrucchiere", "hotel_bnb", "centro_estetico", "studio_medico_dentista"];
      const saveFlights = new Map();
      let currentRules = [];
      let active = false;
      let epoch = 0;
      let scope = null;
      let loaded = false;
      let dirty = false;
      let loadedProfile = null;
      let draftRevision = 0;
      let loadFlight = null;

      function scopeKey(context = getContext()) {
        return JSON.stringify([context?.userId, context?.sessionOrganizationId, context?.selectedOrganizationId]);
      }

      function capture() { return { context: getContext(), epoch }; }

      function current(token) {
        const now = getContext();
        return active && token?.epoch === epoch && now?.view === "configurazione-ai"
          && Boolean(now?.userId && now?.sessionOrganizationId)
          && JSON.stringify(now) === JSON.stringify(token.context);
      }

      function clearFeedback() {
        if (saveStatus) {
          saveStatus.textContent = "";
          saveStatus.style.color = "";
        }
      }

      function resetDraft() {
        dirty = false;
        loaded = false;
        loadedProfile = null;
        draftRevision = 0;
        currentRules = [];
        if (nomeInput) nomeInput.value = "";
        if (vertSelect) vertSelect.selectedIndex = 0;
        if (descTextarea) descTextarea.value = "";
        if (orariTextarea) orariTextarea.value = "";
        if (tonoSelect) tonoSelect.selectedIndex = 0;
        if (tonoCustom) tonoCustom.value = "";
        document.querySelectorAll(".ai-cfg-lang-opt").forEach((checkbox) => { checkbox.checked = false; });
        updateDefaultLanguage("it");
        renderRules();
        clearFeedback();
        if (saveBtn) saveBtn.disabled = true;
      }

      function renderRules(checkedByIndex = null) {
        if (!rulesContainer) return;
        rulesContainer.replaceChildren();
        if (!currentRules.length) {
          const empty = document.createElement("p");
          empty.className = "settings-help-sm";
          empty.textContent = translate("settings:ai_configuration.rules_empty");
          rulesContainer.appendChild(empty);
          return;
        }
        currentRules.forEach((rule, index) => {
          const item = document.createElement("div");
          item.className = "settings-rule-item";
          const label = document.createElement("label");
          label.className = "settings-rule-label";
          const checked = checkedByIndex ? checkedByIndex.get(index) !== false : true;
          label.innerHTML = `<input type="checkbox" class="ai-cfg-rule-checkbox" data-rule-index="${index}" ${checked ? "checked" : ""}> <span>${sanitize(rule)}</span>`;
          const deleteButton = document.createElement("button");
          deleteButton.type = "button";
          deleteButton.className = "settings-rule-remove-btn";
          const removeLabel = translate("settings:ai_configuration.remove_rule");
          deleteButton.title = removeLabel;
          deleteButton.setAttribute("aria-label", removeLabel);
          deleteButton.innerHTML = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>';
          deleteButton.addEventListener("click", () => {
            if (!active) return;
            currentRules.splice(index, 1);
            dirty = true;
            renderRules();
          });
          item.append(label, deleteButton);
          rulesContainer.appendChild(item);
        });
      }

      function updateDefaultLanguage(chosen = null) {
        if (!defaultLinguaSelect) return;
        const currentValue = chosen || defaultLinguaSelect.value || "it";
        const selectedLanguages = ["it", ...Array.from(document.querySelectorAll(".ai-cfg-lang-opt:checked"), (checkbox) => checkbox.value)];
        const labels = { it: "Italiano (it)", en: "English (en)", es: "Español (es)", fr: "Français (fr)", de: "Deutsch (de)" };
        defaultLinguaSelect.replaceChildren();
        selectedLanguages.forEach((code) => {
          const option = document.createElement("option");
          option.value = code;
          option.textContent = labels[code] || code.toUpperCase();
          option.selected = code === currentValue;
          defaultLinguaSelect.appendChild(option);
        });
        if (!selectedLanguages.includes(currentValue)) defaultLinguaSelect.value = "it";
      }

      function populateProfile(profile) {
        const businessName = profile.nome_attivita || "La tua attività";
        const businessNameEl = document.getElementById("business-name");
        const chatBusinessNameEl = document.getElementById("chat-business-name");
        if (businessNameEl) businessNameEl.textContent = businessName;
        if (chatBusinessNameEl) chatBusinessNameEl.textContent = businessName;
        if (nomeInput) nomeInput.value = profile.nome_attivita || "";
        if (vertSelect && validVerticals.includes(profile.verticale)) vertSelect.value = profile.verticale;
        if (descTextarea) descTextarea.value = profile.descrizione || "";
        if (orariTextarea && profile.orari) {
          orariTextarea.value = profile.orari;
          const kbHours = document.getElementById("kb-orari-input");
          if (kbHours && !kbHours.value) kbHours.value = profile.orari;
        }
        if (tonoSelect && profile.tono) {
          const presets = ["professionale_caloroso", "informale_amichevole", "formale_elegante"];
          if (presets.includes(profile.tono)) {
            tonoSelect.value = profile.tono;
            if (tonoCustom) tonoCustom.value = "";
          } else {
            tonoSelect.value = "professionale_caloroso";
            if (tonoCustom) tonoCustom.value = profile.tono;
          }
        }
        const supportedLanguages = Array.isArray(profile.lingue_supportate) ? profile.lingue_supportate : [];
        document.querySelectorAll(".ai-cfg-lang-opt").forEach((checkbox) => {
          checkbox.checked = supportedLanguages.includes(checkbox.value);
        });
        updateDefaultLanguage(profile.lingua_default || "it");
        currentRules = Array.isArray(profile.regole_escalation) ? [...profile.regole_escalation] : [];
        renderRules();
      }

      async function load() {
        const context = getContext();
        if (!active || context?.view !== "configurazione-ai" || !context?.userId || !context?.sessionOrganizationId) return null;
        const key = scopeKey(context);
        if (loadFlight?.key === key) return loadFlight.promise;
        const token = capture();
        if (saveStatus) {
          saveStatus.textContent = translate("settings:ai_configuration.load_loading");
          saveStatus.style.color = "";
        }
        if (saveBtn) saveBtn.disabled = true;
        resetDraft();
        if (saveStatus) saveStatus.textContent = translate("settings:ai_configuration.load_loading");

        const promise = (async () => {
          try {
            const response = await apiFetch(`${API_BASE}/api/onboarding/profilo`);
            if (!current(token)) return null;
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            if (!current(token)) return null;
            const profile = data?.profilo && typeof data.profilo === "object" ? data.profilo : {};
            loadedProfile = profile;
            setProfile(profile);
            populateProfile(profile);
            loaded = true;
            dirty = false;
            if (saveStatus) saveStatus.textContent = "";
            if (saveBtn) saveBtn.disabled = false;
            return profile;
          } catch (error) {
            if (!current(token)) return null;
            root.console?.error?.("Impossibile caricare la configurazione AI:", error);
            if (saveStatus) {
              saveStatus.textContent = translate("settings:ai_configuration.load_error");
              saveStatus.style.color = "var(--red)";
            }
            return null;
          } finally {
            if (loadFlight?.promise === promise) loadFlight = null;
          }
        })();
        loadFlight = { key, promise };
        return promise;
      }

      function collectPayload(profile) {
        const name = (nomeInput?.value || "").trim();
        const vertical = validVerticals.includes(vertSelect?.value)
          ? vertSelect.value
          : (validVerticals.includes(profile?.verticale) ? profile.verticale : "ristorante");
        const description = (descTextarea?.value || "").trim();
        const hours = (orariTextarea?.value || "").trim() || profile?.orari || "";
        let tone = tonoSelect ? tonoSelect.value : (profile?.tono || "professionale_caloroso");
        if (tonoCustom && tonoCustom.value.trim()) tone = tonoCustom.value.trim();
        const supportedLanguages = ["it", ...Array.from(document.querySelectorAll(".ai-cfg-lang-opt:checked"), (checkbox) => checkbox.value)];
        const defaultLanguage = defaultLinguaSelect?.value || "it";
        const selectedRules = [];
        document.querySelectorAll(".ai-cfg-rule-checkbox").forEach((checkbox) => {
          if (checkbox.checked) {
            const index = Number(checkbox.dataset.ruleIndex);
            if (currentRules[index]) selectedRules.push(currentRules[index]);
          }
        });
        return {
          verticale: vertical,
          nome_attivita: name,
          orari: hours,
          descrizione: description,
          tono: tone,
          servizi: Array.isArray(profile?.servizi) ? profile.servizi : [],
          regole_escalation: selectedRules.length ? selectedRules : currentRules,
          whatsapp_collegato: Boolean(profile?.whatsapp_collegato),
          documenti_importati: Boolean(profile?.documenti_importati),
          lingue_supportate: supportedLanguages,
          lingua_default: supportedLanguages.includes(defaultLanguage) ? defaultLanguage : "it",
        };
      }

      async function save() {
        const token = capture();
        if (!current(token) || !loaded || !["owner", "manager"].includes(getRole())) return null;
        const key = scopeKey(token.context);
        const existing = saveFlights.get(key);
        if (existing) return existing;
        const profile = loadedProfile || {};
        const revisionAtSave = draftRevision;
        if (saveBtn) saveBtn.disabled = true;
        if (saveStatus) {
          saveStatus.textContent = translate("settings:ai_configuration.save_loading");
          saveStatus.style.color = "";
        }
        const operation = (async () => {
          try {
            const payload = collectPayload(profile);
            const response = await apiFetch(`${API_BASE}/api/onboarding/profilo`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(payload),
            });
            const currentAfterRequest = current(token);
            const data = await response.json().catch(() => ({}));
            if (!currentAfterRequest || !current(token)) return null;
            if (!response.ok) {
              if (saveStatus) {
                saveStatus.textContent = data.detail || translate("settings:ai_configuration.save_error");
                saveStatus.style.color = "var(--red)";
              }
              return null;
            }
            if (!data?.profilo || typeof data.profilo !== "object") {
              if (saveStatus) {
                saveStatus.textContent = translate("settings:ai_configuration.save_error");
                saveStatus.style.color = "var(--red)";
              }
              return null;
            }
            setProfile(data.profilo);
            loadedProfile = data.profilo;
            const savedName = payload.nome_attivita;
            if (savedName) {
              const businessNameEl = document.getElementById("business-name");
              if (businessNameEl) businessNameEl.textContent = savedName;
              const chatBusinessNameEl = document.getElementById("chat-business-name");
              if (chatBusinessNameEl) chatBusinessNameEl.textContent = savedName;
            }
            const kbHours = document.getElementById("kb-orari-input");
            if (kbHours && payload.orari) kbHours.value = payload.orari;
            if (draftRevision === revisionAtSave) dirty = false;
            if (saveStatus) securityStatus(saveStatus, translate("settings:ai_configuration.save_success"));
            toast(translate("settings:ai_configuration.save_toast"), "success");
            return data.profilo;
          } catch {
            if (!current(token)) return null;
            if (saveStatus) {
              saveStatus.textContent = translate("settings:ai_configuration.connection_error");
              saveStatus.style.color = "var(--red)";
            }
            return null;
          } finally {
            if (saveFlights.get(key) === operation) saveFlights.delete(key);
            const now = getContext();
            if (saveBtn && scope === key && active && scopeKey(now) === key) {
              saveBtn.disabled = false;
            }
          }
        })();
        saveFlights.set(key, operation);
        return operation;
      }

      function markDirty() {
        if (active && loaded) {
          dirty = true;
          draftRevision += 1;
        }
      }

      rulesContainer?.addEventListener("change", markDirty);

      document.querySelectorAll(".ai-cfg-lang-opt").forEach((checkbox) => {
        checkbox.addEventListener("change", () => {
          const checked = new Map([...document.querySelectorAll(".ai-cfg-rule-checkbox")]
            .map((input) => [Number(input.dataset.ruleIndex), input.checked]));
          updateDefaultLanguage();
          renderRules(checked);
          markDirty();
        });
      });
      addRuleBtn?.addEventListener("click", () => {
        if (!active || !addRuleInput) return;
        const text = addRuleInput.value.trim();
        if (!text) return;
        currentRules.push(text);
        addRuleInput.value = "";
        dirty = true;
        draftRevision += 1;
        renderRules();
      });
      addRuleInput?.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          addRuleBtn?.click();
        }
      });
      form?.querySelectorAll("input, select, textarea").forEach((input) => {
        input.addEventListener("input", markDirty);
        input.addEventListener("change", markDirty);
      });
      form?.addEventListener("submit", (event) => {
        event.preventDefault();
        void save();
      });

      function onEnter() {
        const context = getContext();
        if (context?.view !== "configurazione-ai" || !context?.userId || !context?.sessionOrganizationId) return;
        const nextScope = scopeKey(context);
        if (scope !== nextScope) {
          epoch += 1;
          active = false;
          loadFlight = null;
          if (scope !== null) setProfile(null);
          resetDraft();
          scope = nextScope;
        }
        active = true;
        if (loaded) {
          clearFeedback();
          if (saveBtn) saveBtn.disabled = saveFlights.has(scope);
          return;
        }
        void load();
      }

      function onExit() {
        active = false;
        epoch += 1;
        loadFlight = null;
        if (saveStatus?.textContent === translate("settings:ai_configuration.load_loading")
          || saveStatus?.textContent === translate("settings:ai_configuration.save_loading")) clearFeedback();
        if (saveBtn && loaded) saveBtn.disabled = false;
      }

      function invalidate() {
        active = false;
        epoch += 1;
        loadFlight = null;
        scope = null;
        loadedProfile = null;
        setProfile(null);
        resetDraft();
      }

      function refreshLanguage() {
        if (!active) return;
        const checked = new Map([...document.querySelectorAll(".ai-cfg-rule-checkbox")]
          .map((input) => [Number(input.dataset.ruleIndex), input.checked]));
        renderRules(checked);
      }

      return Object.freeze({ onEnter, onExit, invalidate, refreshLanguage });
    },
  });
});
