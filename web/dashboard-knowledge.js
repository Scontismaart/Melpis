(function attachDashboardKnowledge(root, factory) {
  const api = factory(root);
  if (root) root.MelpisDashboardKnowledge = api;
})(typeof window !== "undefined" ? window : globalThis, function createDashboardKnowledge(root) {
  "use strict";
  return Object.freeze({
    create(deps) {
      const { API_BASE, apiFetch, _escapeHtml, _sanitize, _tDash,
        localeCorrente, confermaDestructiva, getContext } = deps;
      const document = root.document;
      const alert = root.alert.bind(root);
      let active = false;
      let destroyed = false;
      let epoch = 0;
      let entryScope = null;
      let tabGeneration = 0;
      const readGenerations = { summary: 0, faq: 0, documenti: 0, web: 0, dati: 0 };
      const pendingReads = new Map();
      const mutationLocks = new Set();
      const busyButtons = new Map();
      let pendingConfirmation = null;
      async function askKnowledge(options) {
        if (pendingConfirmation) return false;
        const promise = confermaDestructiva(options);
        const owned = { promise, titolo: options.titolo, descrizione: options.descrizione };
        pendingConfirmation = owned;
        try { return await promise; }
        finally { if (pendingConfirmation === owned) pendingConfirmation = null; }
      }
      function cancelOwnedConfirmation() {
        const modal = document.getElementById("confirm-modal");
        if (!pendingConfirmation || !modal || modal.hidden) return;
        if (document.getElementById("confirm-title")?.textContent !== pendingConfirmation.titolo
          || document.getElementById("confirm-desc")?.textContent !== pendingConfirmation.descrizione) return;
        document.getElementById("confirm-cancel-btn")?.click();
        if (modal.hidden) {
          document.getElementById("confirm-title").textContent = "";
          document.getElementById("confirm-desc").textContent = "";
        }
      }
      function capture() { return { context: getContext(), epoch, tab: tabGeneration }; }
      function current(token, tab = false) {
        const now = getContext();
        return active && !destroyed && token.epoch === epoch
          && (now?.view === "conoscenza" || now?.view === "documenti")
          && now?.userId && now?.sessionOrganizationId
          && JSON.stringify(now) === JSON.stringify(token.context)
          && (!tab || token.tab === tabGeneration);
      }
      function scopeKey() {
        const c = getContext();
        return JSON.stringify([c?.userId, c?.sessionOrganizationId, c?.selectedOrganizationId]);
      }
      function startRead(key) {
        const token = capture();
        const generation = ++readGenerations[key];
        return () => current(token, key !== "summary") && readGenerations[key] === generation;
      }
      function singleFlight(key, read) {
        if (pendingReads.has(key)) return pendingReads.get(key);
        const task = Promise.resolve().then(read);
        pendingReads.set(key, task);
        task.finally(() => { if (pendingReads.get(key) === task) pendingReads.delete(key); }).catch(() => {});
        return task;
      }
      function refresh(key, load) {
        readGenerations[key] += 1;
        pendingReads.delete(key);
        return load();
      }
      function authorized(res, valid) {
        if (res.status === 401 || res.status === 403) {
          if (valid()) { clearWork(); clearPrivateDOM(); }
          return false;
        }
        return true;
      }
      function beginMutation(key, button) {
        const token = capture();
        if (!current(token) || mutationLocks.has(key)) return null;
        mutationLocks.add(key);
        if (button) {
          busyButtons.set(button, { html: button.innerHTML, disabled: button.disabled,
            restoreFocus: key.includes("delete"), token });
          button.disabled = true;
        }
        return token;
      }
      function endMutation(key, button) {
        mutationLocks.delete(key);
        if (button && busyButtons.has(button)) {
          const initial = busyButtons.get(button);
          button.innerHTML = initial.html;
          button.disabled = initial.disabled;
          busyButtons.delete(button);
          if (initial.restoreFocus && current(initial.token) && button.isConnected
            && (document.activeElement === document.body
              || document.activeElement?.closest("#confirm-modal[hidden]"))) button.focus();
        }
      }
      function clearWork() {
        cancelOwnedConfirmation();
        epoch += 1;
        tabGeneration += 1;
        Object.keys(readGenerations).forEach((key) => { readGenerations[key] += 1; });
        pendingReads.clear();
        for (const [button, initial] of busyButtons) {
          button.innerHTML = initial.html;
          button.disabled = initial.disabled;
        }
        busyButtons.clear();
        kbDocDropzone?.classList.remove("dragover");
      }
      function clearPrivateDOM() {
        [kbFAQList, docLibrary, kbWebList, kbServiziTbody, docFontiList,
          document.getElementById("kb-conflitti-dettagli")].forEach((node) => node?.replaceChildren());
        [kbFAQDomanda, kbFAQRisposta, kbFAQEditId, docCaricaTesto, docCaricaNome,
          kbWebUrlInput, kbOrariInput, docQuery, docFile].forEach((node) => { if (node) node.value = ""; });
        [kbFAQFormStatus, docCaricaStatus, kbWebImportStatus, kbDatiSaveStatus,
          docRispostaText].forEach((node) => { if (node) node.textContent = ""; });
        [docRisposta, docFonti, document.getElementById("kb-conflitti-banner"),
          document.getElementById("kb-conflitti-parziali")]
          .forEach((node) => { if (node) node.hidden = true; });
        if (kbFAQFormCard) kbFAQFormCard.style.display = "none";
        document.querySelectorAll("[id^='kb-badge-'], [id^='kb-stat-'], [id^='kb-'][id$='-status-text'], [id^='kb-'][id$='-updated-text']")
          .forEach((node) => { node.textContent = ""; });
      }
      let kbActiveTab = "faq";

      // Navigazione Tab della Knowledge Base
      document.querySelectorAll(".kb-tab-btn[data-kb-tab]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const tabName = btn.dataset.kbTab;
          impostaTabConoscenza(tabName);
        });
        btn.addEventListener("keydown", gestisciNavigazioneTabConoscenza);
      });

      function gestisciNavigazioneTabConoscenza(event) {
        const tabs = [...document.querySelectorAll(".kb-tab-btn[data-kb-tab]")];
        const currentIndex = tabs.indexOf(event.currentTarget);
        if (currentIndex < 0 || !tabs.length) return;

        let nextIndex;
        if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % tabs.length;
        else if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
        else if (event.key === "Home") nextIndex = 0;
        else if (event.key === "End") nextIndex = tabs.length - 1;
        else return;

        event.preventDefault();
        const nextTab = tabs[nextIndex];
        impostaTabConoscenza(nextTab.dataset.kbTab);
        nextTab.focus();
      }

      function impostaTabConoscenza(tabName) {
        if (!current(capture())) return;
        if (kbActiveTab !== tabName) {
          kbActiveTab = tabName;
          tabGeneration += 1;
          for (const key of pendingReads.keys()) {
            if (key !== "summary") pendingReads.delete(key);
          }
        }
        document.querySelectorAll(".kb-tab-btn[data-kb-tab]").forEach((b) => {
          const active = b.dataset.kbTab === tabName;
          b.classList.toggle("active", active);
          b.setAttribute("aria-selected", String(active));
          b.tabIndex = active ? 0 : -1;
        });

        const panels = {
          faq: document.getElementById("kb-panel-faq"),
          documenti: document.getElementById("kb-panel-documenti"),
          web: document.getElementById("kb-panel-web"),
          "dati-struttura": document.getElementById("kb-panel-dati-struttura"),
        };

        Object.entries(panels).forEach(([key, panel]) => {
          if (panel) panel.hidden = key !== tabName;
        });

        // Carica i dati specifici del tab
        if (tabName === "faq") caricaFAQ();
        else if (tabName === "documenti") { loadDocumentLimits(); caricaDocumenti(); }
        else if (tabName === "web") caricaPagineWeb();
        else if (tabName === "dati-struttura") caricaDatiStruttura();
      }

      function _formatDataOra(isoStr) {
        if (!isoStr) return "N/D";
        try {
          const d = new Date(isoStr);
          return d.toLocaleDateString(localeCorrente(), { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
        } catch {
          return isoStr;
        }
      }

      // ── Summary & Conflitti Aggregati ────────────────────────────

      function caricaKBSummary() { return singleFlight("summary", loadKBSummary); }
      async function loadKBSummary() {
        const valid = startRead("summary");
        if (!valid()) return;
        try {
          const res = await apiFetch(`${API_BASE}/api/conoscenza/summary`);
          if (!valid()) return;
          if (!authorized(res, valid)) return;
          if (!res.ok) return;
          const data = await res.json();
          if (!valid()) return;

          // Aggiorna contatori tab
          const badgeFAQ = document.getElementById("kb-badge-faq");
          const badgeDoc = document.getElementById("kb-badge-documenti");
          const badgeWeb = document.getElementById("kb-badge-web");
          const badgeDati = document.getElementById("kb-badge-dati-struttura");
          if (badgeFAQ) badgeFAQ.textContent = data.faq?.totale || 0;
          if (badgeDoc) badgeDoc.textContent = data.documenti?.totale || 0;
          if (badgeWeb) badgeWeb.textContent = data.web?.totale || 0;
          if (badgeDati) badgeDati.textContent = data.dati_struttura?.totale || 0;

          // Aggiorna card statistiche
          const statFAQ = document.getElementById("kb-stat-faq");
          const statFAQMeta = document.getElementById("kb-stat-faq-meta");
          if (statFAQ) statFAQ.textContent = `${data.faq?.attive || 0}/${data.faq?.totale || 0}`;
          if (statFAQMeta) {
            statFAQMeta.textContent = data.faq?.errori ? `${data.faq.errori} con errore` : "Tutte indicizzate";
          }

          const statDoc = document.getElementById("kb-stat-doc");
          const statDocMeta = document.getElementById("kb-stat-doc-meta");
          if (statDoc) statDoc.textContent = `${data.documenti?.attive || 0}/${data.documenti?.totale || 0}`;
          if (statDocMeta) {
            statDocMeta.textContent = data.documenti?.errori ? `${data.documenti.errori} con errore` : "Tutti indicizzati";
          }

          const statWeb = document.getElementById("kb-stat-web");
          const statWebMeta = document.getElementById("kb-stat-web-meta");
          if (statWeb) statWeb.textContent = `${data.web?.attive || 0}/${data.web?.totale || 0}`;
          if (statWebMeta) {
            statWebMeta.textContent = data.web?.errori ? `${data.web.errori} non raggiungibili` : "Tutte indicizzate";
          }

          const statDati = document.getElementById("kb-stat-dati");
          if (statDati) statDati.textContent = `${data.dati_struttura?.totale || 0} configurati`;

          // Verifica e visualizzazione banner conflitti
          const bannerConflitti = document.getElementById("kb-conflitti-banner");
          const avvisoConflittiParziali = document.getElementById("kb-conflitti-parziali");
          const dettagliConflitti = document.getElementById("kb-conflitti-dettagli");
          if (avvisoConflittiParziali) {
            avvisoConflittiParziali.hidden = data.conflitti_completi !== false;
          }
          if (data.conflitti_totali > 0) {
            const confRes = await apiFetch(`${API_BASE}/api/conoscenza/conflitti`);
            if (!valid()) return;
            if (!authorized(confRes, valid)) return;
            if (confRes.ok) {
              const confData = await confRes.json();
              if (!valid()) return;
              if (avvisoConflittiParziali && confData.complete === false) {
                avvisoConflittiParziali.hidden = false;
              }
              if (confData.conflitti?.length && bannerConflitti && dettagliConflitti) {
                dettagliConflitti.innerHTML = "";
                confData.conflitti.forEach((c) => {
                  const item = document.createElement("div");
                  item.className = "kb-conflict-item";
                  item.textContent = `Servizio "${c.servizio}": prezzo ufficiale ${c.prezzo_ufficiale.toFixed(2)}€ vs ${c.prezzo_conflitto.toFixed(2)}€ menzionato in "${c.fonte_conflitto}"`;
                  dettagliConflitti.appendChild(item);
                });
                bannerConflitti.hidden = false;
              }
            }
          } else if (bannerConflitti) {
            bannerConflitti.hidden = true;
          }
        } catch (err) {
          console.debug("Knowledge summary request failed");
        }
      }

      // ── Tab 1: FAQ Manager ───────────────────────────────────────

      const kbFAQList = document.getElementById("kb-faq-list");
      const kbFAQFormCard = document.getElementById("kb-faq-form-card");
      const kbFAQOpenFormBtn = document.getElementById("kb-faq-open-form-btn");
      const kbFAQCancelBtn = document.getElementById("kb-faq-cancel-btn");
      const kbFAQSaveBtn = document.getElementById("kb-faq-save-btn");
      const kbFAQDomanda = document.getElementById("kb-faq-domanda");
      const kbFAQRisposta = document.getElementById("kb-faq-risposta");
      const kbFAQEditId = document.getElementById("kb-faq-edit-id");
      const kbFAQFormStatus = document.getElementById("kb-faq-form-status");

      kbFAQOpenFormBtn?.addEventListener("click", () => {
        if (!current(capture())) return;
        if (!kbFAQFormCard) return;
        kbFAQEditId.value = "";
        kbFAQDomanda.value = "";
        kbFAQRisposta.value = "";
        kbFAQFormStatus.textContent = "";
        kbFAQFormCard.style.display = "block";
        kbFAQDomanda.focus();
      });

      kbFAQCancelBtn?.addEventListener("click", () => {
        if (!current(capture())) return;
        if (kbFAQFormCard) kbFAQFormCard.style.display = "none";
      });

      kbFAQSaveBtn?.addEventListener("click", async () => {
        const token = beginMutation("faq-save", kbFAQSaveBtn);
        if (!token) return;
        const domanda = kbFAQDomanda.value.trim();
        const risposta = kbFAQRisposta.value.trim();
        const editId = kbFAQEditId.value.trim();

        if (!domanda || !risposta) {
          kbFAQFormStatus.textContent = "Inserisci sia la domanda che la risposta.";
          kbFAQFormStatus.style.color = "var(--red)";
          endMutation("faq-save", kbFAQSaveBtn);
          return;
        }

        kbFAQSaveBtn.disabled = true;
        kbFAQSaveBtn.textContent = "Salvataggio…";
        kbFAQFormStatus.textContent = "";

        try {
          const url = editId ? `${API_BASE}/api/conoscenza/faq/${encodeURIComponent(editId)}` : `${API_BASE}/api/conoscenza/faq`;
          const method = editId ? "PUT" : "POST";
          const res = await apiFetch(url, {
            method,
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ domanda, risposta }),
          });
          if (!current(token)) return;
          if (!authorized(res, () => current(token))) return;

          if (!res.ok) {
            const err = await res.json().catch(() => null);
            if (!current(token)) return;
            throw new Error(err?.detail || "Errore durante il salvataggio della FAQ");
          }

          kbFAQFormCard.style.display = "none";
          kbFAQDomanda.value = "";
          kbFAQRisposta.value = "";
          kbFAQEditId.value = "";
          await refresh("faq", caricaFAQ);
          if (!current(token)) return;
          await refresh("summary", caricaKBSummary);
        } catch (err) {
          if (!current(token)) return;
          kbFAQFormStatus.textContent = err.message || "Errore di connessione.";
          kbFAQFormStatus.style.color = "var(--red)";
        } finally {
          endMutation("faq-save", kbFAQSaveBtn);
        }
      });

      function caricaFAQ() { return singleFlight("faq", loadcaricaFAQ); }
      async function loadcaricaFAQ() {
        const valid = startRead("faq");
        if (!valid()) return;
        if (!kbFAQList) return;
        try {
          const res = await apiFetch(`${API_BASE}/api/documenti/elenco?tipo=faq`);
          if (!valid()) return;
          if (!authorized(res, valid)) return;
          if (!res.ok) throw new Error("Errore nel caricamento delle FAQ");
          const data = await res.json();
          if (!valid()) return;
          const items = data.documenti || [];
          const renderToken = capture();

          // Aggiorna status bar tab
          const statusBarText = document.getElementById("kb-faq-status-text");
          const statusUpdatedText = document.getElementById("kb-faq-updated-text");
          const attive = items.filter((d) => d.is_active).length;
          if (statusBarText) {
            statusBarText.textContent = _tDash("knowledge.runtime.faq_count", "{{active}} FAQ attive su {{total}} totali (tutte indicizzate)", { active: attive, total: items.length });
          }
          if (statusUpdatedText && items.length > 0) {
            const lastUpdate = items[0].updated_at || items[0].caricato_il;
            statusUpdatedText.textContent = _tDash("knowledge.runtime.last_update", "Ultimo aggiornamento: {{date}}", { date: _formatDataOra(lastUpdate) });
          }

          kbFAQList.innerHTML = "";
          if (items.length === 0) {
            kbFAQList.innerHTML = '<p class="doc-library-empty">' + _escapeHtml(_tDash("knowledge.runtime.empty_faq", 'Nessuna FAQ configurata. Clicca su "+ Nuova FAQ" per aggiungerne una.')) + "</p>";
            return;
          }

          items.forEach((faq) => {
            const meta = faq.metadata || {};
            const domanda = meta.domanda || faq.nome;
            const risposta = meta.risposta || "";
            const isActive = faq.is_active !== false;

            const card = document.createElement("div");
            card.className = `kb-item-card ${isActive ? "" : "is-inactive"}`;

            // Contenuto principale
            const main = document.createElement("div");
            main.className = "kb-item-main";

            const title = document.createElement("div");
            title.className = "kb-item-title";
            title.textContent = domanda;

            const sub = document.createElement("div");
            sub.className = "kb-item-subtitle";
            sub.textContent = risposta;

            const metaRow = document.createElement("div");
            metaRow.className = "kb-item-meta";

            const badgeStato = document.createElement("span");
            if (faq.stato === "errore") {
              badgeStato.className = "kb-badge kb-badge-danger";
              badgeStato.textContent = "Errore";
            } else if (isActive) {
              badgeStato.className = "kb-badge kb-badge-success";
              badgeStato.textContent = "Indicizzata";
            } else {
              badgeStato.className = "kb-badge kb-badge-neutral";
              badgeStato.textContent = "Disattivata";
            }

            const dateMeta = document.createElement("span");
            dateMeta.textContent = `Aggiornata: ${_formatDataOra(faq.updated_at || faq.caricato_il)}`;

            metaRow.append(badgeStato, dateMeta);
            main.append(title, sub, metaRow);

            // Azioni: switch toggle, edit, delete
            const actions = document.createElement("div");
            actions.className = "kb-item-actions";

            // Toggle switch attivo/disattivo
            const switchLabel = document.createElement("label");
            switchLabel.className = "kb-switch";
            switchLabel.title = isActive ? "Disattiva fonte" : "Attiva fonte";

            const switchInput = document.createElement("input");
            switchInput.type = "checkbox";
            switchInput.checked = isActive;
            switchInput.addEventListener("change", async () => {
              if (!card.isConnected || !current(renderToken, true)) return;
              const token = beginMutation(`faq-toggle:${faq.id}`, switchInput);
              if (!token) return;
              try {
                const togRes = await apiFetch(`${API_BASE}/api/documenti/${encodeURIComponent(faq.id)}/toggle`, { method: "PATCH" });
                if (!current(token)) return;
                if (!authorized(togRes, () => current(token))) return;
                if (!togRes.ok) throw new Error("Errore durante l'aggiornamento dello stato");
                await refresh("faq", caricaFAQ);
                if (!current(token)) return;
                await refresh("summary", caricaKBSummary);
              } catch (err) {
                if (!current(token)) return;
                switchInput.checked = !switchInput.checked;
                alert(err.message || "Impossibile aggiornare lo stato.");
              } finally {
                endMutation(`faq-toggle:${faq.id}`, switchInput);
              }
            });

            const switchSlider = document.createElement("span");
            switchSlider.className = "kb-switch-slider";
            switchLabel.append(switchInput, switchSlider);

            // Edit button
            const editBtn = document.createElement("button");
            editBtn.type = "button";
            editBtn.className = "inbox-quick-action";
            editBtn.style.padding = "4px 8px";
            editBtn.textContent = "Modifica";
            editBtn.addEventListener("click", () => {
              if (!card.isConnected || !current(renderToken, true)) return;
              kbFAQEditId.value = faq.id;
              kbFAQDomanda.value = domanda;
              kbFAQRisposta.value = risposta;
              kbFAQFormCard.style.display = "block";
              kbFAQDomanda.focus();
            });

            // Delete button
            const delBtn = document.createElement("button");
            delBtn.type = "button";
            delBtn.className = "kb-btn-delete";
            delBtn.innerHTML = `
              <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M10 11v6M14 11v6"/></svg>
              <span>Elimina</span>
            `;
            delBtn.addEventListener("click", async () => {
              if (!card.isConnected || !current(renderToken, true)) return;
              const token = beginMutation(`faq-delete:${faq.id}`, delBtn);
              if (!token) return;
              try {
              const ok = await askKnowledge({
                titolo: "Eliminare questa FAQ?",
                descrizione: `L'assistente non potrà più usare la risposta a: "${domanda}"`,
                label: "Elimina FAQ",
              });
              if (!current(token)) return;
              if (!ok) return;
              try {
                const delRes = await apiFetch(`${API_BASE}/api/conoscenza/faq/${encodeURIComponent(faq.id)}`, { method: "DELETE" });
                if (!current(token)) return;
                if (!authorized(delRes, () => current(token))) return;
                if (!delRes.ok) throw new Error("Errore durante l'eliminazione");
                await refresh("faq", caricaFAQ);
                if (!current(token)) return;
                await refresh("summary", caricaKBSummary);
              } catch (err) {
                if (!current(token)) return;
                alert(err.message || "Impossibile eliminare la FAQ.");
              }
              } finally {
                endMutation(`faq-delete:${faq.id}`, delBtn);
              }
            });

            actions.append(switchLabel, editBtn, delBtn);
            card.append(main, actions);
            kbFAQList.appendChild(card);
          });
        } catch (err) {
          console.debug("Knowledge FAQ request failed");
          if (valid()) kbFAQList.innerHTML = '<p class="doc-library-empty" style="color:var(--red);">Errore nel caricamento delle FAQ.</p>';
        }
      }

      // ── Tab 2: Documenti Manager & Drag-and-Drop ─────────────────

      const docLibrary = document.getElementById("doc-library");
      const docFile = document.getElementById("doc-file");
      const kbDocDropzone = document.getElementById("kb-doc-dropzone");
      const docCaricaTesto = document.getElementById("doc-carica-testo");
      const docCaricaNome = document.getElementById("doc-carica-nome");
      const docCaricaBtn = document.getElementById("doc-carica-btn");
      const docCaricaStatus = document.getElementById("doc-carica-status");
      const docLimits = document.getElementById("kb-doc-limits");
      let pendingFileAction = null;
      let documentPollTimer = null;
      let lastDocumentList = [];
      let ragEnabled = null;

      function newIdempotencyKey() {
        if (root.crypto?.randomUUID) return root.crypto.randomUUID();
        const bytes = new Uint8Array(16);
        if (root.crypto?.getRandomValues) root.crypto.getRandomValues(bytes);
        else bytes.forEach((_, index) => { bytes[index] = Math.random() * 256; });
        bytes[6] = (bytes[6] & 0x0f) | 0x40;
        bytes[8] = (bytes[8] & 0x3f) | 0x80;
        const hex = [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
        return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
      }
      function formatLimitBytes(value) {
        if (!Number.isFinite(value) || value <= 0) return null;
        return value >= 1024 * 1024 ? `${(value / (1024 * 1024)).toLocaleString(localeCorrente())} MB`
          : `${Math.ceil(value / 1024).toLocaleString(localeCorrente())} KB`;
      }
      function displayDocumentLimits(data) {
        if (!docLimits) return;
        const technical = data?.technical_limits;
        const usage = data?.usage;
        const values = [technical?.upload_bytes, technical?.pages_per_document, technical?.chunks_per_document];
        const usageValues = [usage?.documents, usage?.pages, usage?.chunks];
        const supported = values.every(Number.isFinite);
        const usageKnown = usageValues.every(Number.isFinite);
        const plan = typeof data?.plan === "string" && data.plan ? `Piano ${data.plan}` : "Piano non disponibile";
        const commercial = data?.commercial_limits;
        const commercialConfigured = commercial?.status !== "not_configured" && commercial
          && [commercial.documents, commercial.pages].some(Number.isFinite);
        const parts = [plan, data?.rag_enabled === true ? "Knowledge Base attiva" : data?.rag_enabled === false
          ? "Knowledge Base non inclusa nel piano" : "Disponibilità Knowledge Base non disponibile"];
        if (supported) parts.push(`Limiti tecnici per file: ${formatLimitBytes(values[0]) || "non disponibili"}, ${values[1]} pagine, ${values[2]} segmenti`);
        else parts.push("Limiti tecnici non disponibili");
        if (usageKnown) parts.push(`Utilizzo: ${usageValues[0]} documenti, ${usageValues[1]} pagine, ${usageValues[2]} segmenti`);
        else parts.push("Utilizzo non disponibile");
        parts.push(commercialConfigured ? "Limiti del piano disponibili" : "Limiti commerciali non configurati");
        docLimits.textContent = parts.join(" · ");
        ragEnabled = data?.rag_enabled === true ? true : data?.rag_enabled === false ? false : null;
        if (docFile) docFile.disabled = data?.rag_enabled !== true;
        if (docCaricaBtn) docCaricaBtn.disabled = data?.rag_enabled !== true;
        if (kbDocDropzone) kbDocDropzone.setAttribute("aria-disabled", String(data?.rag_enabled !== true));
      }
      async function loadDocumentLimits() {
        if (!docLimits) return;
        const token = capture();
        if (!current(token)) return;
        docLimits.textContent = "Caricamento limiti…";
        try {
          const res = await apiFetch(`${API_BASE}/api/documenti/limiti`);
          if (!current(token)) return;
          if (res.status === 401 || res.status === 403) { authorized(res, () => current(token)); return; }
          if (!res.ok) throw new Error("Limiti non disponibili");
          const data = await res.json();
          if (current(token)) displayDocumentLimits(data);
        } catch {
          if (current(token)) displayDocumentLimits(null);
        }
      }
      function setUploadStatus(message, kind = "neutral") {
        if (!docCaricaStatus) return;
        docCaricaStatus.textContent = message;
        docCaricaStatus.style.color = kind === "error" ? "var(--red)" : kind === "success" ? "var(--green)" : "var(--ink)";
      }
      function stopDocumentPolling() {
        if (documentPollTimer) root.clearTimeout(documentPollTimer);
        documentPollTimer = null;
      }
      function hasPendingDocuments(items) {
        return items.some((doc) => doc.stato === "caricato" || doc.stato === "in_elaborazione");
      }
      function scheduleDocumentPoll() {
        stopDocumentPolling();
        if (!active || !hasPendingDocuments(lastDocumentList)) return;
        documentPollTimer = root.setTimeout(async () => {
          documentPollTimer = null;
          if (!current(capture(), true)) return;
          await refresh("documenti", caricaDocumenti);
          if (pendingFileAction) {
            const matching = lastDocumentList.find((item) => item.id && pendingFileAction.documentId === item.id)
              || lastDocumentList.find((item) => item.nome === pendingFileAction.file.name);
            if (matching?.index_verified === true && matching.stato === "indicizzata") {
              setUploadStatus(`File "${pendingFileAction.file.name}" indicizzato con successo.`, "success");
              pendingFileAction = null;
            } else if (matching?.stato === "errore") {
              setUploadStatus("Non è stato possibile indicizzare il file. Controlla i requisiti del formato e riprova.", "error");
              pendingFileAction = null;
            } else if (matching?.index_verified === false) {
              setUploadStatus("Documento salvato; l'indice deve essere verificato e reindicizzato.", "error");
              pendingFileAction = null;
            }
          }
          scheduleDocumentPoll();
        }, 2500);
      }
      async function submitFile(file, url, key, label) {
        const form = new FormData();
        form.append("file", file);
        const res = await apiFetch(url, { method: "POST", body: form, headers: { "Idempotency-Key": key } });
        if (res.status === 401 || res.status === 403) authorized(res, () => current(capture()));
        if (!res.ok) {
          const err = await res.json().catch(() => null);
          throw new Error(err?.detail || `Errore durante ${label}`);
        }
        return res.json();
      }

      // Setup drag and drop
      if (kbDocDropzone && docFile) {
        kbDocDropzone.addEventListener("click", () => { if (current(capture())) docFile.click(); });
        kbDocDropzone.addEventListener("dragover", (e) => {
          e.preventDefault();
          if (!current(capture())) return;
          kbDocDropzone.classList.add("dragover");
        });
        kbDocDropzone.addEventListener("dragleave", () => {
          if (!current(capture())) return;
          kbDocDropzone.classList.remove("dragover");
        });
        kbDocDropzone.addEventListener("drop", async (e) => {
          e.preventDefault();
          if (!current(capture())) return;
          kbDocDropzone.classList.remove("dragover");
          const file = e.dataTransfer?.files?.[0];
          if (file) await eseguiUploadFile(file);
        });
        docFile.addEventListener("change", async () => {
          if (!current(capture())) return;
          const file = docFile.files?.[0];
          if (file) await eseguiUploadFile(file);
        });
      }

      async function eseguiUploadFile(file, action = null) {
        const token = beginMutation("file-upload");
        if (!token) return;
        if (!docCaricaStatus) { endMutation("file-upload"); return; }
        if (ragEnabled === false) {
          setUploadStatus("La Knowledge Base non è inclusa nel piano.", "error");
          endMutation("file-upload");
          return;
        }
        const currentAction = action || { file, key: newIdempotencyKey(), endpoint: `${API_BASE}/api/documenti/carica-file` };
        pendingFileAction = currentAction;
        setUploadStatus(`Caricamento di "${file.name}" e indicizzazione in corso…`);
        const retryButton = document.getElementById("kb-doc-upload-retry");
        if (retryButton) retryButton.hidden = true;
        try {
          const result = await submitFile(file, currentAction.endpoint, currentAction.key, "il caricamento del file");
          if (!current(token)) return;
          setUploadStatus(`File "${file.name}" ricevuto; verifica dell'indicizzazione in corso…`);
          if (docFile) docFile.value = "";
          await refresh("documenti", caricaDocumenti);
          if (!current(token)) return;
          await refresh("summary", caricaKBSummary);
          if (!current(token)) return;
          const matching = lastDocumentList.find((item) => result?.id && item.id === result.id)
            || lastDocumentList.find((item) => item.nome === file.name);
          if (result?.id) currentAction.documentId = result.id;
          if (matching?.stato === "indicizzata" && matching.index_verified === true) {
            setUploadStatus(`File "${file.name}" indicizzato con successo.`, "success");
            pendingFileAction = null;
          } else if (matching?.index_verified === false) {
            setUploadStatus("Documento salvato; l'indice deve essere verificato e reindicizzato.", "error");
          } else if (matching?.stato === "indicizzata") {
            setUploadStatus("Documento salvato; stato di verifica dell'indice non disponibile.");
          } else if (matching?.stato === "errore") {
            setUploadStatus("Non è stato possibile indicizzare il file. Controlla i requisiti del formato e riprova.", "error");
          } else {
            setUploadStatus(`File "${file.name}" ricevuto; indicizzazione in corso.`);
          }
        } catch (err) {
          if (!current(token)) return;
          setUploadStatus(err.message || "Errore durante il caricamento del file.", "error");
          if (retryButton) retryButton.hidden = false;
        } finally {
          endMutation("file-upload");
        }
      }
      document.getElementById("kb-doc-upload-retry")?.addEventListener("click", () => {
        if (pendingFileAction?.file) eseguiUploadFile(pendingFileAction.file, pendingFileAction);
      });

      // Upload testo libero
      docCaricaBtn?.addEventListener("click", async () => {
        const token = beginMutation("text-upload", docCaricaBtn);
        if (!token) return;
        if (ragEnabled === false) {
          setUploadStatus("La Knowledge Base non è inclusa nel piano.", "error");
          endMutation("text-upload", docCaricaBtn);
          return;
        }
        const testo = docCaricaTesto.value.trim();
        const nome = docCaricaNome.value.trim() || "documento.txt";
        if (!testo) {
          docCaricaStatus.textContent = "Incolla il testo del documento prima di salvare.";
          docCaricaStatus.style.color = "var(--red)";
          endMutation("text-upload", docCaricaBtn);
          return;
        }
        docCaricaBtn.disabled = true;
        docCaricaBtn.textContent = "Indicizzazione…";
        try {
          const res = await apiFetch(`${API_BASE}/api/documenti/carica`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ testo, nome }),
          });
          if (!current(token)) return;
          if (!authorized(res, () => current(token))) return;
          if (!res.ok) {
            const err = await res.json().catch(() => null);
            if (!current(token)) return;
            throw new Error(err?.detail || "Errore durante l'indicizzazione");
          }
          docCaricaStatus.textContent = `Testo "${nome}" indicizzato con successo.`;
          docCaricaStatus.style.color = "var(--green)";
          docCaricaTesto.value = "";
          docCaricaNome.value = "";
          await refresh("documenti", caricaDocumenti);
          if (!current(token)) return;
          await refresh("summary", caricaKBSummary);
        } catch (err) {
          if (!current(token)) return;
          docCaricaStatus.textContent = err.message || "Errore durante il salvataggio.";
          docCaricaStatus.style.color = "var(--red)";
        } finally {
          endMutation("text-upload", docCaricaBtn);
        }
      });

      function caricaDocumenti() { return singleFlight("documenti", loadcaricaDocumenti); }
      async function loadcaricaDocumenti() {
        const valid = startRead("documenti");
        if (!valid()) return;
        if (!docLibrary) return;
        try {
          const res = await apiFetch(`${API_BASE}/api/documenti/elenco`);
          if (!valid()) return;
          if (!authorized(res, valid)) return;
          if (!res.ok) throw new Error("Errore nel recupero documenti");
          const data = await res.json();
          if (!valid()) return;
          const items = (data.documenti || []).filter((d) => d.tipo === "documento" || d.tipo === "upload");
          lastDocumentList = items;
          scheduleDocumentPoll();
          const renderToken = capture();

          const statusBarText = document.getElementById("kb-doc-status-text");
          const statusUpdatedText = document.getElementById("kb-doc-updated-text");
          const attivi = items.filter((d) => d.is_active && d.stato === "indicizzata" && d.index_verified === true).length;
          if (statusBarText) {
            statusBarText.textContent = _tDash("knowledge.runtime.documents_count", "{{active}} documenti attivi su {{total}} totali", { active: attivi, total: items.length });
          }
          if (statusUpdatedText && items.length > 0) {
            statusUpdatedText.textContent = _tDash("knowledge.runtime.last_update", "Ultimo aggiornamento: {{date}}", { date: _formatDataOra(items[0].updated_at || items[0].caricato_il) });
          }

          docLibrary.innerHTML = "";
          if (items.length === 0) {
            docLibrary.innerHTML = '<p class="doc-library-empty">' + _escapeHtml(_tDash("knowledge.runtime.empty_documents", "Nessun file caricato. Usa l'area tratteggiata sopra per caricare PDF, DOCX o immagini.")) + "</p>";
            return;
          }

          items.forEach((doc) => {
            const isActive = doc.is_active !== false && doc.stato === "indicizzata" && doc.index_verified === true;
            const card = document.createElement("div");
            card.className = `kb-item-card ${isActive ? "" : "is-inactive"}`;

            const main = document.createElement("div");
            main.className = "kb-item-main";

            const title = document.createElement("div");
            title.className = "kb-item-title";
            title.textContent = doc.nome;

            const metaRow = document.createElement("div");
            metaRow.className = "kb-item-meta";

            const badgeStato = document.createElement("span");
            if (doc.index_verified === false) {
              badgeStato.className = "kb-badge kb-badge-warning";
              badgeStato.textContent = "Da reindicizzare";
            } else if (doc.stato === "caricato") {
              badgeStato.className = "kb-badge kb-badge-warning";
              badgeStato.textContent = "Caricato";
            } else if (doc.stato === "in_elaborazione") {
              badgeStato.className = "kb-badge kb-badge-warning";
              badgeStato.textContent = "In elaborazione";
            } else if (doc.stato === "errore") {
              badgeStato.className = "kb-badge kb-badge-danger";
              badgeStato.textContent = "Errore";
            } else if (doc.stato === "indicizzata" && doc.index_verified === true && isActive) {
              badgeStato.className = "kb-badge kb-badge-success";
              badgeStato.textContent = "Indicizzato";
            } else if (doc.stato === "indicizzata" && doc.index_verified === true) {
              badgeStato.className = "kb-badge kb-badge-neutral";
              badgeStato.textContent = "Disattivato";
            } else {
              badgeStato.className = "kb-badge kb-badge-neutral";
              badgeStato.textContent = "Stato non disponibile";
            }

            const chunkMeta = document.createElement("span");
            chunkMeta.textContent = Number.isFinite(doc.chunk) ? `${doc.chunk} segmenti` : "Segmenti n/d";

            const pageMeta = document.createElement("span");
            const pageCount = doc.pages ?? doc.metadata?.pages;
            pageMeta.textContent = Number.isFinite(pageCount) ? `${pageCount} pagine` : "Pagine n/d";

            const dateMeta = document.createElement("span");
            dateMeta.textContent = `Caricato: ${_formatDataOra(doc.caricato_il)}`;

            metaRow.append(badgeStato, chunkMeta, pageMeta, dateMeta);
            main.append(title, metaRow);

            const actions = document.createElement("div");
            actions.className = "kb-item-actions";

            // Toggle switch
            if (doc.stato === "indicizzata" && doc.index_verified === true) {
              const switchLabel = document.createElement("label");
              switchLabel.className = "kb-switch";
              switchLabel.title = isActive ? "Disattiva documento" : "Attiva documento";

              const switchInput = document.createElement("input");
              switchInput.type = "checkbox";
              switchInput.checked = isActive;
              switchInput.addEventListener("change", async () => {
                if (!card.isConnected || !current(renderToken, true)) return;
                const token = beginMutation(`document-toggle:${doc.id}`, switchInput);
                if (!token) return;
                try {
                  const togRes = await apiFetch(`${API_BASE}/api/documenti/${encodeURIComponent(doc.id)}/toggle`, { method: "PATCH" });
                  if (!current(token)) return;
                  if (!authorized(togRes, () => current(token))) return;
                  if (!togRes.ok) throw new Error("Errore switch");
                  await refresh("documenti", caricaDocumenti);
                  if (!current(token)) return;
                  await refresh("summary", caricaKBSummary);
                } catch (err) {
                  if (!current(token)) return;
                  switchInput.checked = !switchInput.checked;
                  alert(err.message || "Impossibile aggiornare lo stato.");
                } finally {
                  endMutation(`document-toggle:${doc.id}`, switchInput);
                }
              });

              const switchSlider = document.createElement("span");
              switchSlider.className = "kb-switch-slider";
              switchLabel.append(switchInput, switchSlider);
              actions.appendChild(switchLabel);
            }

            if (doc.stato === "errore" || doc.index_verified === false) {
              const reindexButton = document.createElement("button");
              reindexButton.type = "button";
              reindexButton.className = "report-refresh";
              reindexButton.textContent = "Reindicizza documento";
              reindexButton.addEventListener("click", () => {
                if (!card.isConnected || !current(renderToken, true)) return;
                const picker = document.createElement("input");
                picker.type = "file";
                picker.dataset.reindexFilePicker = "true";
                picker.accept = docFile?.accept || ".pdf,.docx,.txt,.md,.csv,.json,.png,.jpg,.jpeg,.webp";
                picker.hidden = true;
                document.body.appendChild(picker);
                picker.addEventListener("change", async () => {
                  const original = picker.files?.[0];
                  picker.remove();
                  if (!original || !current(renderToken, true)) return;
                  const action = { file: original, key: newIdempotencyKey(), endpoint:
                    `${API_BASE}/api/documenti/${encodeURIComponent(doc.id)}/reindicizza` };
                  await eseguiUploadFile(original, action);
                }, { once: true });
                picker.click();
              });
              actions.appendChild(reindexButton);
            }

            // Delete button
            const delBtn = document.createElement("button");
            delBtn.type = "button";
            delBtn.className = "kb-btn-delete";
            delBtn.innerHTML = `
              <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M10 11v6M14 11v6"/></svg>
              <span>Elimina</span>
            `;
            delBtn.addEventListener("click", async () => {
              if (!card.isConnected || !current(renderToken, true)) return;
              const token = beginMutation(`document-delete:${doc.id}`, delBtn);
              if (!token) return;
              try {
              const ok = await askKnowledge({
                titolo: "Rimuovere questo documento?",
                descrizione: `"${doc.nome}" verrà eliminato definitivamente dalla knowledge base.`,
                label: "Elimina documento",
              });
              if (!current(token)) return;
              if (!ok) return;
              try {
                const delRes = await apiFetch(`${API_BASE}/api/documenti/${encodeURIComponent(doc.id)}`, { method: "DELETE" });
                if (!current(token)) return;
                if (!authorized(delRes, () => current(token))) return;
                if (!delRes.ok) throw new Error("Errore eliminazione");
                await refresh("documenti", caricaDocumenti);
                if (!current(token)) return;
                await refresh("summary", caricaKBSummary);
              } catch (err) {
                if (!current(token)) return;
                alert(err.message || "Impossibile rimuovere il documento.");
              }
              } finally {
                endMutation(`document-delete:${doc.id}`, delBtn);
              }
            });
            actions.appendChild(delBtn);

            card.append(main, actions);
            docLibrary.appendChild(card);
          });
        } catch (err) {
          console.debug("Knowledge document request failed");
        }
      }

      // ── Tab 3: Pagine Web Manager ────────────────────────────────

      const kbWebList = document.getElementById("kb-web-list");
      const kbWebUrlInput = document.getElementById("kb-web-url-input");
      const kbWebImportBtn = document.getElementById("kb-web-import-btn");
      const kbWebImportStatus = document.getElementById("kb-web-import-status");

      kbWebImportBtn?.addEventListener("click", async () => {
        const token = beginMutation("web-import", kbWebImportBtn);
        if (!token) return;
        const url = (kbWebUrlInput?.value || "").trim();
        if (!url || (!url.startsWith("http://") && !url.startsWith("https://"))) {
          kbWebImportStatus.textContent = "Inserisci un URL valido che inizi con http:// o https://";
          kbWebImportStatus.style.color = "var(--red)";
          endMutation("web-import", kbWebImportBtn);
          return;
        }

        kbWebImportBtn.disabled = true;
        kbWebImportBtn.textContent = "Recupero in corso…";
        kbWebImportStatus.textContent = `Scaricamento e indicizzazione dei contenuti da ${url}…`;
        kbWebImportStatus.style.color = "var(--ink)";

        try {
          const res = await apiFetch(`${API_BASE}/api/conoscenza/web`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url }),
          });
          if (!current(token)) return;
          if (!authorized(res, () => current(token))) return;

          if (!res.ok) {
            const err = await res.json().catch(() => null);
            if (!current(token)) return;
            throw new Error(err?.detail || "Impossibile estrarre la pagina web");
          }

          const data = await res.json();
          if (!current(token)) return;
          kbWebImportStatus.textContent = data.detail || "Pagina web importata con successo!";
          kbWebImportStatus.style.color = "var(--green)";
          kbWebUrlInput.value = "";
          await refresh("web", caricaPagineWeb);
          if (!current(token)) return;
          await refresh("summary", caricaKBSummary);
        } catch (err) {
          if (!current(token)) return;
          kbWebImportStatus.textContent = err.message || "Errore durante l'importazione.";
          kbWebImportStatus.style.color = "var(--red)";
          await refresh("web", caricaPagineWeb);
          if (!current(token)) return;
          await refresh("summary", caricaKBSummary);
        } finally {
          endMutation("web-import", kbWebImportBtn);
        }
      });

      function caricaPagineWeb() { return singleFlight("web", loadcaricaPagineWeb); }
      async function loadcaricaPagineWeb() {
        const valid = startRead("web");
        if (!valid()) return;
        if (!kbWebList) return;
        try {
          const res = await apiFetch(`${API_BASE}/api/documenti/elenco?tipo=web`);
          if (!valid()) return;
          if (!authorized(res, valid)) return;
          if (!res.ok) throw new Error("Errore recupero pagine web");
          const data = await res.json();
          if (!valid()) return;
          const items = data.documenti || [];
          const renderToken = capture();

          const statusBarText = document.getElementById("kb-web-status-text");
          const statusUpdatedText = document.getElementById("kb-web-updated-text");
          const attive = items.filter((d) => d.is_active && d.stato !== "errore").length;
          if (statusBarText) {
            statusBarText.textContent = _tDash("knowledge.runtime.web_count", "{{active}} pagine web attive su {{total}} totali", { active: attive, total: items.length });
          }
          if (statusUpdatedText && items.length > 0) {
            statusUpdatedText.textContent = _tDash("knowledge.runtime.last_update", "Ultimo aggiornamento: {{date}}", { date: _formatDataOra(items[0].updated_at || items[0].caricato_il) });
          }

          kbWebList.innerHTML = "";
          if (items.length === 0) {
            kbWebList.innerHTML = '<p class="doc-library-empty">' + _escapeHtml(_tDash("knowledge.runtime.empty_web", "Nessuna pagina web importata. Inserisci un link sopra per importare listino o info dal tuo sito.")) + "</p>";
            return;
          }

          items.forEach((item) => {
            const isActive = item.is_active !== false && item.stato !== "errore";
            const card = document.createElement("div");
            card.className = `kb-item-card ${isActive ? "" : "is-inactive"}`;

            const main = document.createElement("div");
            main.className = "kb-item-main";

            const title = document.createElement("div");
            title.className = "kb-item-title";
            title.textContent = item.nome;

            const sub = document.createElement("div");
            sub.className = "kb-item-subtitle";
          const sourceUrl = String(item.fonte || "");
          if (/^https?:\/\//i.test(sourceUrl)) {
            const link = document.createElement("a");
            link.href = sourceUrl;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            link.style.color = "var(--accent)";
            link.textContent = sourceUrl;
            sub.appendChild(link);
          } else {
            sub.textContent = sourceUrl;
          }

            const metaRow = document.createElement("div");
            metaRow.className = "kb-item-meta";

            const badgeStato = document.createElement("span");
            if (item.stato === "errore") {
              badgeStato.className = "kb-badge kb-badge-danger";
              badgeStato.textContent = "Errore import";
              badgeStato.title = item.errore || "URL non raggiungibile";
            } else if (isActive) {
              badgeStato.className = "kb-badge kb-badge-success";
              badgeStato.textContent = "Indicizzata";
            } else {
              badgeStato.className = "kb-badge kb-badge-neutral";
              badgeStato.textContent = "Disattivata";
            }

            const chunkMeta = document.createElement("span");
            chunkMeta.textContent = `${item.chunk || 0} chunk`;

            const dateMeta = document.createElement("span");
            dateMeta.textContent = `Importata: ${_formatDataOra(item.caricato_il)}`;

            metaRow.append(badgeStato, chunkMeta, dateMeta);
            main.append(title, sub, metaRow);

            const actions = document.createElement("div");
            actions.className = "kb-item-actions";

            if (item.stato !== "errore") {
              const switchLabel = document.createElement("label");
              switchLabel.className = "kb-switch";
              switchLabel.title = isActive ? "Disattiva pagina" : "Attiva pagina";

              const switchInput = document.createElement("input");
              switchInput.type = "checkbox";
              switchInput.checked = isActive;
              switchInput.addEventListener("change", async () => {
                if (!card.isConnected || !current(renderToken, true)) return;
                const token = beginMutation(`web-toggle:${item.id}`, switchInput);
                if (!token) return;
                try {
                  const togRes = await apiFetch(`${API_BASE}/api/documenti/${encodeURIComponent(item.id)}/toggle`, { method: "PATCH" });
                  if (!current(token)) return;
                  if (!authorized(togRes, () => current(token))) return;
                  if (!togRes.ok) throw new Error("Errore switch");
                  await refresh("web", caricaPagineWeb);
                  if (!current(token)) return;
                  await refresh("summary", caricaKBSummary);
                } catch (err) {
                  if (!current(token)) return;
                  switchInput.checked = !switchInput.checked;
                  alert(err.message || "Impossibile aggiornare lo stato.");
                } finally {
                  endMutation(`web-toggle:${item.id}`, switchInput);
                }
              });

              const switchSlider = document.createElement("span");
              switchSlider.className = "kb-switch-slider";
              switchLabel.append(switchInput, switchSlider);
              actions.appendChild(switchLabel);
            }

            const delBtn = document.createElement("button");
            delBtn.type = "button";
            delBtn.className = "kb-btn-delete";
            delBtn.innerHTML = `
              <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M10 11v6M14 11v6"/></svg>
              <span>Elimina</span>
            `;
            delBtn.addEventListener("click", async () => {
              if (!card.isConnected || !current(renderToken, true)) return;
              const token = beginMutation(`web-delete:${item.id}`, delBtn);
              if (!token) return;
              try {
              const ok = await askKnowledge({
                titolo: "Rimuovere questa pagina web?",
                descrizione: `L'assistente non userà più i contenuti importati da "${item.fonte}".`,
                label: "Elimina pagina",
              });
              if (!current(token)) return;
              if (!ok) return;
              try {
                const delRes = await apiFetch(`${API_BASE}/api/documenti/${encodeURIComponent(item.id)}`, { method: "DELETE" });
                if (!current(token)) return;
                if (!authorized(delRes, () => current(token))) return;
                if (!delRes.ok) throw new Error("Errore eliminazione");
                await refresh("web", caricaPagineWeb);
                if (!current(token)) return;
                await refresh("summary", caricaKBSummary);
              } catch (err) {
                if (!current(token)) return;
                alert(err.message || "Impossibile rimuovere la pagina.");
              }
              } finally {
                endMutation(`web-delete:${item.id}`, delBtn);
              }
            });
            actions.appendChild(delBtn);

            card.append(main, actions);
            kbWebList.appendChild(card);
          });
        } catch (err) {
          console.debug("Knowledge web request failed");
        }
      }

      // ── Tab 4: Dati Struttura Manager ────────────────────────────

      const kbServiziTbody = document.getElementById("kb-servizi-tbody");
      const kbAddServizioBtn = document.getElementById("kb-add-servizio-btn");
      const kbOrariInput = document.getElementById("kb-orari-input");
      const kbSalvaDatiBtn = document.getElementById("kb-salva-dati-btn");
      const kbDatiSaveStatus = document.getElementById("kb-dati-save-status");

      function _creaRigaServizio(s = {}) {
        const rowToken = capture();
        const tr = document.createElement("tr");
        tr.dataset.servizioId = s.id || "";

        // Nome
        const tdNome = document.createElement("td");
        const inNome = document.createElement("input");
        inNome.type = "text";
        inNome.className = "kb-table-input kb-servizio-nome";
        inNome.placeholder = "Es. Taglio Uomo";
        inNome.value = s.nome || "";
        tdNome.appendChild(inNome);

        // Prezzo
        const tdPrezzo = document.createElement("td");
        const inPrezzo = document.createElement("input");
        inPrezzo.type = "number";
        inPrezzo.className = "kb-table-input kb-servizio-prezzo";
        inPrezzo.placeholder = "25.00";
        inPrezzo.step = "0.5";
        inPrezzo.min = "0";
        inPrezzo.value = s.prezzo != null ? s.prezzo : "";
        tdPrezzo.appendChild(inPrezzo);

        // Durata
        const tdDurata = document.createElement("td");
        const inDurata = document.createElement("input");
        inDurata.type = "number";
        inDurata.className = "kb-table-input kb-servizio-durata";
        inDurata.placeholder = "30";
        inDurata.step = "5";
        inDurata.min = "5";
        inDurata.value = s.durata_minuti || 30;
        tdDurata.appendChild(inDurata);

        // Operatore
        const tdOp = document.createElement("td");
        const inOp = document.createElement("input");
        inOp.type = "text";
        inOp.className = "kb-table-input kb-servizio-operatore";
        inOp.placeholder = "Es. Marco o Qualsiasi";
        inOp.value = s.operatore || "";
        tdOp.appendChild(inOp);

        // Azioni (elimina riga)
        const tdAzioni = document.createElement("td");
        tdAzioni.style.textAlign = "center";
        const removeBtn = document.createElement("button");
        removeBtn.type = "button";
        removeBtn.className = "kb-btn-delete";
        removeBtn.innerHTML = `
          <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M10 11v6M14 11v6"/></svg>
          <span>Elimina</span>
        `;
        removeBtn.title = "Elimina questo servizio";
        removeBtn.addEventListener("click", () => { if (tr.isConnected && current(rowToken, true)) tr.remove(); });
        tdAzioni.appendChild(removeBtn);

        tr.append(tdNome, tdPrezzo, tdDurata, tdOp, tdAzioni);
        return tr;
      }

      kbAddServizioBtn?.addEventListener("click", () => {
        if (!current(capture())) return;
        if (!kbServiziTbody) return;
        const newRow = _creaRigaServizio();
        kbServiziTbody.appendChild(newRow);
        newRow.querySelector(".kb-servizio-nome")?.focus();
      });

      kbSalvaDatiBtn?.addEventListener("click", async () => {
        const token = beginMutation("data-save", kbSalvaDatiBtn);
        if (!token) return;
        if (!kbServiziTbody) { endMutation("data-save", kbSalvaDatiBtn); return; }
        const rows = kbServiziTbody.querySelectorAll("tr");
        const servizi = [];

        rows.forEach((tr) => {
          const nome = tr.querySelector(".kb-servizio-nome")?.value.trim();
          const prezzoRaw = tr.querySelector(".kb-servizio-prezzo")?.value.trim();
          const durataRaw = tr.querySelector(".kb-servizio-durata")?.value.trim();
          const operatore = tr.querySelector(".kb-servizio-operatore")?.value.trim() || "";

          if (nome) {
            servizi.push({
              id: tr.dataset.servizioId || null,
              nome,
              prezzo: parseFloat(prezzoRaw) || 0.0,
              durata_minuti: parseInt(durataRaw, 10) || 30,
              operatore,
            });
          }
        });

        const orari = (kbOrariInput?.value || "").trim();

        kbSalvaDatiBtn.disabled = true;
        kbSalvaDatiBtn.textContent = "Salvataggio e indicizzazione…";
        if (kbDatiSaveStatus) kbDatiSaveStatus.textContent = "";

        try {
          const res = await apiFetch(`${API_BASE}/api/conoscenza/dati-struttura`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ servizi, orari }),
          });
          if (!current(token)) return;
          if (!authorized(res, () => current(token))) return;

          if (!res.ok) {
            const err = await res.json().catch(() => null);
            if (!current(token)) return;
            throw new Error(err?.detail || "Errore durante il salvataggio dei Dati struttura");
          }

          if (kbDatiSaveStatus) {
            kbDatiSaveStatus.textContent = "Dati struttura e listino sincronizzati con successo (Priorità 1 applicata).";
            kbDatiSaveStatus.style.color = "var(--green)";
          }
          const aiCfgOrari = document.getElementById("ai-cfg-orari");
          if (aiCfgOrari && orari) {
            aiCfgOrari.value = orari;
          }
          await refresh("dati", caricaDatiStruttura);
          if (!current(token)) return;
          await refresh("summary", caricaKBSummary);
        } catch (err) {
          if (!current(token)) return;
          if (kbDatiSaveStatus) {
            kbDatiSaveStatus.textContent = err.message || "Errore durante il salvataggio.";
            kbDatiSaveStatus.style.color = "var(--red)";
          }
        } finally {
          endMutation("data-save", kbSalvaDatiBtn);
        }
      });

      function caricaDatiStruttura() { return singleFlight("dati", loadcaricaDatiStruttura); }
      async function loadcaricaDatiStruttura() {
        const valid = startRead("dati");
        if (!valid()) return;
        if (!kbServiziTbody) return;
        try {
          const res = await apiFetch(`${API_BASE}/api/conoscenza/dati-struttura`);
          if (!valid()) return;
          if (!authorized(res, valid)) return;
          if (!res.ok) throw new Error("Errore recupero dati struttura");
          const data = await res.json();
          if (!valid()) return;

          const statusBarText = document.getElementById("kb-dati-status-text");
          const statusUpdatedText = document.getElementById("kb-dati-updated-text");
          if (statusBarText) {
            statusBarText.textContent = `${(data.servizi || []).length} servizi configurati — Priorità 1 (Massima autorevolezza)`;
          }
          if (statusUpdatedText) {
            statusUpdatedText.textContent = data.updated_at ? `Ultimo aggiornamento: ${_formatDataOra(data.updated_at)}` : "Pronto per la configurazione";
          }

          if (kbOrariInput) {
            kbOrariInput.value = data.orari || "";
          }

          kbServiziTbody.innerHTML = "";
          if (data.servizi && data.servizi.length > 0) {
            data.servizi.forEach((s) => {
              kbServiziTbody.appendChild(_creaRigaServizio(s));
            });
          } else {
            // Inserisci riga iniziale vuota pronta all'uso
            kbServiziTbody.appendChild(_creaRigaServizio());
          }
        } catch (err) {
          console.debug("Knowledge business-data request failed");
        }
      }

      // ── Tester "Chiedi alla Knowledge Base" ───────────────────────

      const docQuery = document.getElementById("doc-query");
      const docChiediBtn = document.getElementById("doc-chiedi-btn");
      const docRisposta = document.getElementById("doc-risposta");
      const docRispostaText = document.getElementById("doc-risposta-text");
      const docFonti = document.getElementById("doc-fonti");
      const docFontiList = document.getElementById("doc-fonti-list");
      let qaIdempotencyKey = null;
      let qaIdempotencyQuestion = null;

      docChiediBtn?.addEventListener("click", async () => {
        const token = beginMutation("query", docChiediBtn);
        if (!token) return;
        const domanda = (docQuery?.value || "").trim();
        if (!domanda) { endMutation("query", docChiediBtn); return; }
        if (qaIdempotencyQuestion !== domanda || !qaIdempotencyKey) {
          qaIdempotencyKey = crypto.randomUUID();
          qaIdempotencyQuestion = domanda;
        }
        docChiediBtn.disabled = true;
        docChiediBtn.textContent = "Interrogazione AI in corso…";
        docRisposta.hidden = true;
        try {
          const res = await apiFetch(`${API_BASE}/api/documenti/chiedi`, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "Idempotency-Key": qaIdempotencyKey,
            },
            body: JSON.stringify({ domanda, k: 5 }),
          });
          if (!current(token)) return;
          if (!authorized(res, () => current(token))) return;
          if (!res.ok) throw new Error("Errore durante l'elaborazione della domanda");
          const data = await res.json();
          if (!current(token)) return;
          docRispostaText.textContent = data.risposta;
          docRisposta.hidden = false;
          qaIdempotencyKey = null;
          qaIdempotencyQuestion = null;

          if (data.fonti && data.fonti.length > 0) {
            docFonti.hidden = false;
            docFontiList.innerHTML = "";
            data.fonti.forEach((f) => {
              const li = document.createElement("li");
              li.className = "doc-fonti-item";

              // Label priorità
              let priorityLabel = "";
              let priorityClass = "kb-badge-neutral";
              if (f.priorita === 1 || f.tipo === "dati_struttura") {
                priorityLabel = "Priorità 1 (Dati struttura)";
                priorityClass = "kb-badge-success";
              } else if (f.priorita === 2 || f.tipo === "faq") {
                priorityLabel = "Priorità 2 (FAQ)";
                priorityClass = "kb-badge-priority";
              } else if (f.priorita === 3 || f.tipo === "documento" || f.tipo === "upload") {
                priorityLabel = "Priorità 3 (Documento)";
                priorityClass = "kb-badge-warning";
              } else if (f.priorita === 4 || f.tipo === "web") {
                priorityLabel = "Priorità 4 (Web)";
                priorityClass = "kb-badge-neutral";
              }

              const statoFonte = f.stato === "indicizzata" ? '<span class="kb-badge kb-badge-success">Indicizzata</span>' : '<span class="kb-badge kb-badge-warning">In elaborazione</span>';

              li.innerHTML = `
                <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;">
                  <strong>${_sanitize(f.documento)}</strong>
                  <div style="display:flex;gap:6px;">
                    <span class="kb-badge ${priorityClass}">${priorityLabel}</span>
                    ${statoFonte}
                  </div>
                </div>
                <div style="font-size:0.75rem;color:var(--ink-soft);">Score rilevanza semantica: <span class="doc-fonti-score">${_sanitize(f.score)}</span></div>
              `;
              docFontiList.appendChild(li);
            });
          } else {
            docFonti.hidden = true;
          }
        } catch (err) {
          if (!current(token)) return;
          docRispostaText.textContent = err.message || "Errore durante la ricerca.";
          docRisposta.hidden = false;
          docFonti.hidden = true;
        } finally {
          endMutation("query", docChiediBtn);
        }
      });

      // Funzione principale richiamata dal router della vista "conoscenza"
      async function aggiornaConoscenzaCompleta() {
        const token = capture();
        if (!current(token)) return;
        await caricaKBSummary();
        if (!current(token)) return;
        if (kbActiveTab === "faq") await caricaFAQ();
        else if (kbActiveTab === "documenti") await caricaDocumenti();
        else if (kbActiveTab === "web") await caricaPagineWeb();
        else if (kbActiveTab === "dati-struttura") await caricaDatiStruttura();
      }

      return Object.freeze({
        onEnter() {
          const scope = scopeKey();
          if (entryScope !== null && entryScope !== scope) { clearWork(); clearPrivateDOM(); }
          entryScope = scope;
          active = true;
        },
        onExit() { active = false; stopDocumentPolling(); clearWork(); },
        invalidate() { active = false; destroyed = true; clearWork(); clearPrivateDOM(); },
        aggiornaConoscenzaCompleta,
      });
    },
  });
});
