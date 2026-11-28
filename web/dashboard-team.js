(function attachDashboardTeam(root, factory) {
  const api = factory(root);
  if (root) root.MelpisDashboardTeam = api;
})(typeof window !== "undefined" ? window : globalThis, function createDashboardTeam(root) {
  "use strict";
  return Object.freeze({
    create(deps) {
      const { API_BASE, apiFetch, _escapeHtml, _tDash, localeCorrente,
        toast, confermaDestructiva, _estraiMessaggioErroreApi,
        getContext, getSession, apriView } = deps;
      const document = root.document;
      const Option = root.Option;
      let active = false;
      let destroyed = false;
      let epoch = 0;
      let entryScope = null;
      let loadSequence = 0;
      let invitationsSequence = 0;
      let organizationsSequence = 0;
      let pendingTeamPromise = null;
      let pendingTeamToken = null;
      let membersRenderToken = null;
      let invitationsRenderToken = null;
      let invitationsCache = [];
      let pendingConfirmation = null;
      const mutationLocks = new Set();
      const busyButtons = new Map();
      function capture() { return { context: getContext(), epoch }; }
      function current(token) {
        const now = getContext();
        return Boolean(token) && active && !destroyed && token.epoch === epoch && now?.view === "team"
          && now?.userId && now?.sessionOrganizationId
          && JSON.stringify(now) === JSON.stringify(token.context);
      }
      function scopeKey() {
        const c = getContext();
        return JSON.stringify([c?.userId, c?.sessionOrganizationId, c?.selectedOrganizationId]);
      }
      function canManage() { return ["owner", "manager"].includes(getSession()?.ruolo); }
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
      async function askTeam(options) {
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
      function clearWork() {
        cancelOwnedConfirmation();
        epoch += 1;
        loadSequence += 1;
        invitationsSequence += 1;
        organizationsSequence += 1;
        pendingTeamPromise = null;
        pendingTeamToken = null;
        for (const [button, initial] of busyButtons) {
          button.innerHTML = initial.html;
          button.disabled = initial.disabled;
        }
        busyButtons.clear();
      }
      function clearPrivateDOM() {
        teamDataCache = null;
        membersRenderToken = null;
        invitationsRenderToken = null;
        invitationsCache = [];
        ["team-members-tbody", "team-invitations-list", "team-org-selector"]
          .forEach((id) => document.getElementById(id)?.replaceChildren());
        ["team-stat-limit", "team-stat-plan", "team-count-badge", "team-limit-title", "team-limit-desc"]
          .forEach((id) => { const node = document.getElementById(id); if (node) node.textContent = ""; });
        const email = document.getElementById("team-add-email");
        if (email) email.value = "";
        const banner = document.getElementById("team-limit-banner");
        if (banner) banner.hidden = true;
        const card = document.getElementById("team-add-card");
        if (card) card.style.display = "none";
        const submit = document.getElementById("team-submit-btn");
        if (submit) submit.disabled = true;
      }
      function denied(res, token) {
        if (res.status !== 401 && res.status !== 403) return false;
        if (current(token)) { clearWork(); clearPrivateDOM(); }
        return true;
      }
      let teamDataCache = null;

      async function caricaInvitiTeam() {
        const list = document.getElementById("team-invitations-list");
        const token = capture();
        const sequence = ++invitationsSequence;
        if (!current(token) || !list || getSession()?.ruolo === "staff") return;
        try {
          const res = await apiFetch(`${API_BASE}/api/team/invitations`);
          if (!current(token) || sequence !== invitationsSequence) return;
          if (denied(res, token)) return;
          if (!res.ok) return;
          const data = await res.json();
          if (!current(token) || sequence !== invitationsSequence) return;
          const invitations = data.invitations || [];
          invitationsCache = invitations;
          invitationsRenderToken = token;
          list.innerHTML = invitations.length ? invitations.map((inv) => `
            <li data-invitation-id="${_escapeHtml(inv.id)}">
              <span>${_escapeHtml(inv.email)} · ${_escapeHtml(inv.ruolo)}</span>
              ${getSession()?.ruolo === "owner" || inv.ruolo === "staff" ? `
                <button type="button" class="team-invite-resend" data-id="${_escapeHtml(inv.id)}">${_escapeHtml(_tDash("team.resend", "Reinvia"))}</button>
                <button type="button" class="team-invite-revoke" data-id="${_escapeHtml(inv.id)}">${_escapeHtml(_tDash("team.revoke", "Revoca"))}</button>
              ` : ""}
            </li>`).join("") : `<li>${_escapeHtml(_tDash("team.pending_empty", "Nessun invito in attesa."))}</li>`;
        } catch { if (current(token) && sequence === invitationsSequence) list.textContent = _tDash("team.pending_unavailable", "Inviti temporaneamente non disponibili."); }
      }

      async function caricaOrganizzazioniTeam() {
        const token = capture();
        const sequence = ++organizationsSequence;
        const selector = document.getElementById("team-org-selector");
        if (!current(token) || !selector) return;
        const res = await apiFetch(`${API_BASE}/api/team/organizations`);
        if (!current(token) || sequence !== organizationsSequence) return;
        if (denied(res, token)) return;
        if (!res.ok) return;
        const data = await res.json();
        if (!current(token) || sequence !== organizationsSequence) return;
        selector.replaceChildren();
        for (const org of data.organizations || []) {
          const option = new Option(org.name, org.id);
          selector.add(option);
        }
        selector.value = getSession()?.organization_id || "";
        selector.hidden = selector.options.length < 2;
      }

      function caricaTeam() {
        if (!active || destroyed) return Promise.resolve();
        if (entryScope !== null && entryScope !== scopeKey()) {
          clearWork();
          clearPrivateDOM();
          entryScope = scopeKey();
        }
        if (pendingTeamPromise && current(pendingTeamToken)) return pendingTeamPromise;
        const task = loadTeam();
        pendingTeamPromise = task;
        pendingTeamToken = capture();
        task.finally(() => {
          if (pendingTeamPromise === task) {
            pendingTeamPromise = null;
            pendingTeamToken = null;
          }
        }).catch(() => {});
        return task;
      }
      function refreshTeam() {
        loadSequence += 1;
        pendingTeamPromise = null;
        pendingTeamToken = null;
        return caricaTeam();
      }
      async function loadTeam() {
        const token = capture();
        const sequence = ++loadSequence;
        if (!current(token)) return;
        const tbody = document.getElementById("team-members-tbody");
        const statLimit = document.getElementById("team-stat-limit");
        const statPlan = document.getElementById("team-stat-plan");
        const countBadge = document.getElementById("team-count-badge");
        const limitBanner = document.getElementById("team-limit-banner");
        const limitTitle = document.getElementById("team-limit-title");
        const limitDesc = document.getElementById("team-limit-desc");
        const submitBtn = document.getElementById("team-submit-btn");
        const addCard = document.getElementById("team-add-card");

        if (!tbody) return;
        try {
        await Promise.all([caricaInvitiTeam(), caricaOrganizzazioniTeam()]);
        if (!current(token) || sequence !== loadSequence) return;

        const mioRuolo = getSession()?.ruolo || "staff";

        // Se l'utente è staff, nascondi la card di aggiunta
        if (addCard) {
          addCard.style.display = mioRuolo === "staff" ? "none" : "";
        }

        // Se l'utente è manager, può aggiungere solo staff
        const ruoloSelect = document.getElementById("team-add-ruolo");
        if (ruoloSelect && mioRuolo === "manager") {
          const mgrOpt = ruoloSelect.querySelector('option[value="manager"]');
          if (mgrOpt) mgrOpt.disabled = true;
          ruoloSelect.value = "staff";
        } else if (ruoloSelect) {
          const mgrOpt = ruoloSelect.querySelector('option[value="manager"]');
          if (mgrOpt) mgrOpt.disabled = false;
        }

          const res = await apiFetch(`${API_BASE}/api/team/members`);
          if (!current(token) || sequence !== loadSequence) return;
          if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            if (!current(token) || sequence !== loadSequence) return;
            const detail = _estraiMessaggioErroreApi(res, err, "Errore");
            if (denied(res, token)) {
              toast(_tDash("team.runtime.load_error", "Impossibile caricare i membri del team: {{detail}}", { detail }), "error");
              return;
            }
            const message = _tDash("team.runtime.load_error", "Impossibile caricare i membri del team: {{detail}}", { detail });
            tbody.innerHTML = `<tr><td colspan="5" style="padding: 24px; text-align: center; color: var(--red);">${_escapeHtml(message)}</td></tr>`;
            return;
          }
          const data = await res.json();
          if (!current(token) || sequence !== loadSequence) return;
          teamDataCache = data;
          membersRenderToken = token;

          const { members, total, users_limit, can_add_more } = data;
          const seatEntitlementUnavailable = users_limit === null && !can_add_more;
          const unavailable = _tDash("team.runtime.seat_entitlement_unavailable", "Disponibilità del piano non verificata.");

          // Aggiorna contatori e badge
          if (countBadge) countBadge.textContent = _tDash("team.runtime.member_count", "{{count}} membri", { count: total });

          if (statLimit) {
            if (seatEntitlementUnavailable) {
              statLimit.textContent = unavailable;
            } else if (users_limit === null) {
              statLimit.textContent = _tDash("team.runtime.unlimited_accounts", "{{count}} account attivi, senza limite numerico di piano", { count: total });
            } else if (users_limit === 1) {
              statLimit.textContent = _tDash("team.runtime.essential_account", "1 / 1 account (Piano Essenziale - Solo titolare)");
            } else {
              const operatori = Math.max(0, total - 1);
              const maxOperatori = users_limit - 1;
              statLimit.textContent = _tDash("team.runtime.account_capacity", "{{used}} / {{limit}} account ({{operators}} di {{max}} collaboratori)", {
                used: total, limit: users_limit, operators: operatori, max: maxOperatori
              });
            }
          }

          // Aggiorna piano visualizzato
          if (statPlan) {
            let nomePiano = _tDash("team.runtime.plan_essential", "Essenziale");
            if (users_limit === 3) nomePiano = _tDash("team.runtime.plan_growth", "Crescita (Pro)");
            else if (users_limit === null) nomePiano = _tDash("team.runtime.plan_scale", "Scala");
            statPlan.textContent = seatEntitlementUnavailable ? unavailable : nomePiano;
          }

          // Gestione banner limite raggiunto
          if (limitBanner && submitBtn) {
            if (!can_add_more) {
              limitBanner.hidden = false;
              submitBtn.disabled = true;
              submitBtn.title = seatEntitlementUnavailable ? unavailable : users_limit === 1
                ? _tDash("team.runtime.essential_limit_title", "Il piano Essenziale non include collaboratori aggiuntivi")
                : _tDash("team.runtime.limit_title", "Limite massimo collaboratori raggiunto per il piano corrente");
              if (limitTitle) {
                limitTitle.textContent = seatEntitlementUnavailable ? unavailable : users_limit === 1
                  ? _tDash("team.runtime.essential_limit_banner", "Il piano Essenziale include solo il titolare")
                  : _tDash("team.runtime.limit_banner", "Limite account raggiunto ({{used}}/{{limit}})", { used: total, limit: users_limit });
              }
              if (limitDesc) {
                if (seatEntitlementUnavailable) {
                  limitDesc.textContent = "";
                } else if (users_limit === 1) {
                  limitDesc.textContent = "Il piano Essenziale è riservato al solo account titolare. Effettua l'upgrade al piano Crescita per abilitare fino a 2 collaboratori operativi.";
                } else {
                  limitDesc.textContent = "Hai occupato tutti i posti inclusi nel tuo piano. Il piano Scala non prevede un limite numerico di posti.";
                }
              }
            } else {
              limitBanner.hidden = true;
            submitBtn.disabled = !canManage();
              submitBtn.title = "";
            }
          }

          if (!members || members.length === 0) {
            tbody.innerHTML = `<tr><td colspan="5" style="padding: 30px; text-align: center; color: var(--ink-soft);">Nessun collaboratore trovato.</td></tr>`;
            return;
          }

          tbody.innerHTML = members.map((m) => {
            const isOwner = m.ruolo === "owner";
            const isManager = m.ruolo === "manager";
            const isStaff = m.ruolo === "staff";
            const initials = (m.nome || m.email || "U").substring(0, 2).toUpperCase();

            let rolePillClass = "team-role-staff";
            let roleLabel = "Operatore Staff";
            if (isOwner) {
              rolePillClass = "team-role-owner";
              roleLabel = "Proprietario";
            } else if (isManager) {
              rolePillClass = "team-role-manager";
              roleLabel = "Manager";
            }

            let dateFormatted = "—";
            if (m.joined_at) {
              try {
                dateFormatted = new Date(m.joined_at).toLocaleDateString(localeCorrente(), {
                  day: "2-digit",
                  month: "2-digit",
                  year: "numeric"
                });
              } catch { /* ignora */ }
            }

            // Permessi azioni
            let actionsHtml = "";
            if (isOwner) {
              actionsHtml = `<span style="font-size:0.8rem; color:var(--ink-soft); font-style:italic;">Proprietario account</span>`;
            } else if (mioRuolo === "staff") {
              actionsHtml = `<span style="font-size:0.8rem; color:var(--ink-soft); font-style:italic;">Nessuna azione</span>`;
            } else if (mioRuolo === "manager" && isManager) {
              actionsHtml = `<span style="font-size:0.8rem; color:var(--ink-soft); font-style:italic;">Manager</span>`;
            } else {
              // Owner o Manager su Staff
              const canChangeRole = mioRuolo === "owner";
              actionsHtml = `
                <div style="display:inline-flex; align-items:center; gap:8px;">
                  ${canChangeRole ? `
                  <select class="team-action-btn team-change-role" data-user-id="${_escapeHtml(m.user_id)}" style="padding:4px 8px; font-size:0.78rem;">
                      <option value="staff" ${isStaff ? "selected" : ""}>Staff</option>
                      <option value="manager" ${isManager ? "selected" : ""}>Manager</option>
                    </select>
                  ` : ""}
                <button type="button" class="team-action-btn delete team-delete-btn" data-user-id="${_escapeHtml(m.user_id)}" data-email="${_escapeHtml(m.email)}" title="Rimuovi dal team">
                    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                    Rimuovi
                  </button>
                </div>
              `;
            }

            return `
            <tr data-user-id="${_escapeHtml(m.user_id)}">
                <td>
                  <div class="team-member-cell">
                  <div class="team-avatar-circle" aria-hidden="true">${_escapeHtml(initials)}</div>
                    <div>
                      <strong style="color:var(--ink); display:block;">${_escapeHtml(m.nome)}</strong>
                    </div>
                  </div>
                </td>
                <td><span style="color:var(--ink-soft); font-family:monospace; font-size:0.85rem;">${_escapeHtml(m.email)}</span></td>
                <td><span class="team-role-pill ${rolePillClass}">${roleLabel}</span></td>
                <td style="color:var(--ink-soft); font-size:0.85rem;">${dateFormatted}</td>
                <td style="text-align: right;">${actionsHtml}</td>
              </tr>
            `;
          }).join("");

        } catch (err) {
          if (!current(token) || sequence !== loadSequence) return;
          console.debug("Team member request failed");
          tbody.innerHTML = `<tr><td colspan="5" style="padding: 24px; text-align: center; color: var(--red);">Errore di connessione durante il recupero dei collaboratori.</td></tr>`;
        }
      }

      async function aggiungiMembroTeam(e) {
        e?.preventDefault?.();
        const emailInput = document.getElementById("team-add-email");
        const ruoloSelect = document.getElementById("team-add-ruolo");
        const submitBtn = document.getElementById("team-submit-btn");
        if (!canManage() || teamDataCache?.can_add_more !== true) return;
        const token = beginMutation("invite-submit", submitBtn);
        if (!token) return;

        const email = (emailInput?.value || "").trim();
        const ruolo = ruoloSelect?.value || "staff";

        if (!email) {
          toast("Inserisci l'indirizzo email del collaboratore.", "warning");
          endMutation("invite-submit", submitBtn);
          return;
        }
        if (getSession()?.ruolo === "manager" && ruolo !== "staff") {
          endMutation("invite-submit", submitBtn);
          return;
        }

        if (submitBtn) {
          submitBtn.disabled = true;
          submitBtn.textContent = "Aggiunta in corso...";
        }

        try {
          const res = await apiFetch(`${API_BASE}/api/team/members`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email, ruolo }),
          });
          if (!current(token)) return;

          const data = await res.json().catch(() => ({}));
          if (!current(token)) return;
          if (!res.ok) {
            const errMsg = _estraiMessaggioErroreApi(res, data, "Impossibile aggiungere il collaboratore.");
            toast(errMsg, "error", 6000);
            denied(res, token);
            return;
          }

          toast(_tDash("team.invite_sent", "Invito preparato. L'accesso al team richiede l'accettazione del destinatario."), "success");
          // A test link is returned only in explicitly enabled non-production mode.
          if (data.test_link) toast(`Link di test: ${data.test_link}`, "success", 12000);
          if (emailInput) emailInput.value = "";
          await caricaInvitiTeam();
        } catch (err) {
          if (!current(token)) return;
          console.debug("Team invitation request failed");
          toast("Errore di connessione durante l'aggiunta.", "error");
        } finally {
          endMutation("invite-submit", submitBtn);
          if (submitBtn && current(token)) submitBtn.disabled = teamDataCache?.can_add_more === false || !canManage();
        }
      }

      function canManageMember(userId, action) {
        const role = getSession()?.ruolo;
        const member = teamDataCache?.members?.find((candidate) => String(candidate.user_id) === String(userId));
        if (!member || member.ruolo === "owner") return false;
        if (action === "role") return role === "owner";
        return role === "owner" || (role === "manager" && member.ruolo === "staff");
      }

      async function gestisciAzioniTeam(e) {
        const actionElement = e.target.closest(".team-delete-btn, .team-change-role");
        const row = actionElement?.closest("tr[data-user-id]");
        const tbody = document.getElementById("team-members-tbody");
        if (!actionElement || !row || !tbody?.contains(row) || !current(membersRenderToken)) return;
        const userId = actionElement.dataset.userId;
        const isDelete = actionElement.classList.contains("team-delete-btn");
        const action = isDelete ? "delete" : "role";
        if (!canManageMember(userId, action)) return;
        const key = `member-${action}:${userId}`;
        const token = beginMutation(key, actionElement);
        if (!token) return;
        try {
          if (isDelete) {
            const email = actionElement.dataset.email;
            const ok = await askTeam({
              titolo: "Rimuovi collaboratore",
              descrizione: `Sei sicuro di voler rimuovere ${email} dal team? L'utente non potrà più accedere alle chat e al pannello di questa attività.`,
              label: "Rimuovi collaboratore"
            });
            if (!current(token) || !ok || !canManageMember(userId, "delete")) return;
          }
          const newRole = isDelete ? null : actionElement.value;
          if (!isDelete && newRole !== "staff" && newRole !== "manager") return;
          const res = await apiFetch(`${API_BASE}/api/team/members/${encodeURIComponent(userId)}`, isDelete
            ? { method: "DELETE" }
            : { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ruolo: newRole }) });
          if (!current(token)) return;
          const data = await res.json().catch(() => ({}));
          if (!current(token)) return;
          if (!res.ok) {
            toast(_estraiMessaggioErroreApi(res, data, isDelete ? "Errore rimozione." : "Errore aggiornamento ruolo."), "error");
            if (denied(res, token)) return;
            if (!isDelete) await refreshTeam();
            return;
          }
          toast(isDelete ? "Collaboratore rimosso dal team."
            : `Ruolo aggiornato a ${newRole === "manager" ? "Manager" : "Staff"}.`, "success");
          await refreshTeam();
        } catch {
          if (!current(token)) return;
          toast(isDelete ? "Errore di connessione durante la rimozione." : "Errore di connessione.", "error");
          if (!isDelete) await refreshTeam();
        } finally {
          endMutation(key, actionElement);
        }
      }

      document.getElementById("team-add-form")?.addEventListener("submit", aggiungiMembroTeam);

      document.getElementById("team-invitations-list")?.addEventListener("click", async (event) => {
        const button = event.target.closest("button[data-id]");
        const list = document.getElementById("team-invitations-list");
        if (!button || !list?.contains(button) || !current(invitationsRenderToken) || !canManage()) return;
        const invitation = invitationsCache.find((item) => String(item.id) === button.dataset.id);
        if (!invitation || (getSession()?.ruolo === "manager" && invitation.ruolo !== "staff")) return;
        const action = button.classList.contains("team-invite-revoke") ? "revoke" : "resend";
        const id = encodeURIComponent(button.dataset.id);
        const key = `invitation-${action}:${button.dataset.id}`;
        const token = beginMutation(key, button);
        if (!token) return;
        try {
          const res = await apiFetch(`${API_BASE}/api/team/invitations/${id}${action === "resend" ? "/resend" : ""}`, {
            method: action === "resend" ? "POST" : "DELETE"
          });
          if (!current(token)) return;
          if (!res.ok) {
            toast(_tDash("team.invite_error", "Impossibile aggiornare l'invito."), "error");
            denied(res, token);
            return;
          }
          toast(action === "resend"
            ? _tDash("team.resend_success", "Invito reinviato.")
            : _tDash("team.revoke_success", "Invito revocato."), "success");
          await caricaInvitiTeam();
        } catch {
          if (current(token)) toast(_tDash("team.invite_error", "Impossibile aggiornare l'invito."), "error");
        } finally { endMutation(key, button); }
      });

      document.getElementById("btn-refresh-team")?.addEventListener("click", () => { if (current(capture())) caricaTeam(); });
      document.getElementById("team-limit-upgrade-btn")?.addEventListener("click", () => {
        if (current(capture())) apriView("account");
      });
      document.getElementById("team-members-tbody")?.addEventListener("click", (e) => {
        if (e.target.closest(".team-delete-btn")) gestisciAzioniTeam(e);
      });
      document.getElementById("team-members-tbody")?.addEventListener("change", (e) => {
        if (e.target.closest(".team-change-role")) gestisciAzioniTeam(e);
      });

      return Object.freeze({
        onEnter() {
          const scope = scopeKey();
          if (entryScope !== null && scope !== entryScope) { clearWork(); clearPrivateDOM(); }
          entryScope = scope;
          active = true;
        },
        onExit() { active = false; clearWork(); },
        invalidate() { active = false; destroyed = true; clearWork(); clearPrivateDOM(); },
        caricaTeam,
      });
    },
  });
});
