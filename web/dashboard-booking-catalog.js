(function (root) {
  "use strict";
  // The host must call invalidate synchronously when session, role or organization changes.
  function create(deps) {
    const { container, API_BASE, apiFetch, getContext, getSession } = deps;
    const doc = container.ownerDocument;
    const tr = typeof deps.translate === "function" ? deps.translate : (s) => s;
    let epoch = 0, identity = null, active = false, controller = null, form = null;
    let state = null, pending = null, conflictCopy = null, receipt = null, busy = false, statusKey = "";
    let status, editor, save, publish, reload, restore, revision, security, audit;
    function signature() {
      const context = getContext(), session = getSession();
      if (!context || context.view !== "prenotazioni" || !context.userId || !context.sessionOrganizationId || !session || !["owner", "manager", "staff"].includes(session.ruolo)) return null;
      return JSON.stringify([context, session]);
    }
    function valid(token) {
      if (token !== epoch) return false;
      if (!active || !identity || signature() !== identity) { if (active) invalidate(); return false; }
      return true;
    }
    function node(tag, text, parent) { const element = doc.createElement(tag); if (text != null) element.textContent = tr(text); if (parent) parent.appendChild(element); return element; }
    function addButton(label, handler) { const element = node("button", label, container); element.type = "button"; element.className = "booking-icon-text-button"; element.addEventListener("click", handler); return element; }
    function setMessage(message) { statusKey = message; if (status) status.textContent = tr(message); }
    function permissions() {
      const role = getSession()?.ruolo;
      const knownSector = ["ristorante", "parrucchiere", "centro_estetico", "hotel_bnb", "studio_medico_dentista"].includes(state?.organization_sector);
      return { save: knownSector && role === "owner" && state?.capabilities?.can_save === true, publish: knownSector && role === "owner" && state?.capabilities?.can_publish === true };
    }
    function controls() {
      if (!save) return;
      const p = permissions();
      save.hidden = !p.save; publish.hidden = !p.publish;
      save.disabled = busy || !p.save; publish.disabled = busy || !p.publish || !state?.draft;
      if (form) form.setEditable(!busy && p.save);
    }
    function clear() {
      controller?.abort(); controller = null;
      form?.clear(); form = null; state = null; pending = null; conflictCopy = null; receipt = null; busy = false; statusKey = "";
      container.replaceChildren(); status = editor = save = publish = reload = restore = revision = security = audit = null;
    }
    function invalidate() { active = false; identity = null; epoch += 1; clear(); }
    function leave() { invalidate(); }
    function setup() {
      node("h2", "Catalogo prenotazioni", container);
      node("p", "Il motore automatico non è attivo per questo catalogo.", container);
      revision = node("p", "", container);
      status = node("p", "", container); status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
      security = addButton("Apri impostazioni di sicurezza", () => { if (valid(epoch) && typeof deps.onOpenSecurity === "function") deps.onOpenSecurity(); }); security.hidden = true;
      editor = node("div", null, container);
      save = addButton("Salva bozza", saveDraft);
      publish = addButton("Pubblica", publishDraft);
      reload = addButton("Ricarica catalogo", () => load(true)); reload.hidden = true;
      restore = addButton("Ripristina modifiche non salvate", () => { if (!valid(epoch) || !conflictCopy || !form || !permissions().save) return; form.load(conflictCopy); conflictCopy = null; restore.hidden = true; setMessage("Modifiche ripristinate: verifica il catalogo prima di salvare."); }); restore.hidden = true;
      addButton("Mostra cronologia", loadAudit);
      audit = node("div", null, container);
      save.hidden = publish.hidden = true;
    }
    function validState(data) {
      return data && Number.isSafeInteger(data.revision) && data.revision >= 0 && data.runtime_enabled === false && data.capabilities && typeof data.capabilities.can_save === "boolean" && typeof data.capabilities.can_publish === "boolean" && (data.published_revision === null || Number.isSafeInteger(data.published_revision));
    }
    function apply(data) {
      state = data;
      revision.textContent = `${tr("Revisione corrente")}: ${data.revision} · ${tr("Pubblicata")}: ${data.published_revision == null ? tr("nessuna") : data.published_revision}`;
      form?.clear();
      form = root.MelpisBookingCatalogForm.create(editor, { sector: data.organization_sector, canEdit: permissions().save, translate: tr, onChange: () => { /* Pending request is compared with the complete current payload. */ } });
      form.load(data.draft || data.published || null);
      controls();
    }
    function fail(response) {
      if (response.status === 409) {
        try { conflictCopy = form?.read(state.revision + 1) || null; } catch (_) { conflictCopy = null; }
        state = null; pending = null; controls(); reload.hidden = false;
        setMessage("Il catalogo è cambiato. Ricarica e verifica le modifiche non salvate.");
      } else if (response.status === 403) {
        state = null; pending = null; controls(); reload.hidden = false; security.hidden = typeof deps.onOpenSecurity !== "function";
        setMessage("Permesso mancante o verifica a due fattori necessaria. Controlla le impostazioni di sicurezza.");
      } else if (response.status === 503) {
        reload.hidden = false; setMessage("Catalogo temporaneamente non disponibile. Riprova più tardi.");
      } else { setMessage("Operazione non riuscita. Controlla i dati e riprova."); }
    }
    async function load(preserveConflict = false) {
      const token = epoch; if (!valid(token) || busy) return;
      busy = true; controls(); setMessage("Caricamento catalogo…");
      const localController = new root.AbortController(); controller = localController;
      try {
        const response = await apiFetch(`${API_BASE}/api/bookings/catalog`, { signal: localController.signal });
        if (!valid(token)) return;
        if (!response.ok) { if (receipt) { reload.hidden = false; setMessage("Scrittura riuscita, ma il catalogo aggiornato non è disponibile. Ricarica prima di continuare."); } else fail(response); return; }
        const data = await response.json(); if (!valid(token)) return;
        if (!validState(data)) { state = null; controls(); setMessage("Risposta del catalogo non valida."); reload.hidden = false; return; }
        if (!preserveConflict) conflictCopy = null;
        apply(data); pending = null; receipt = null; reload.hidden = true; security.hidden = !(getSession()?.ruolo === "owner" && !data.capabilities.can_publish && typeof deps.onOpenSecurity === "function");
        restore.hidden = !(preserveConflict && conflictCopy && permissions().save);
        setMessage(!data.organization_sector ? "Settore non disponibile: aggiorna il profilo prima di modificare il catalogo." : !restore.hidden ? "Catalogo aggiornato. Puoi ripristinare le tue modifiche e verificarle." : getSession()?.ruolo === "owner" && !data.capabilities.can_publish ? "Per pubblicare verifica i permessi e la verifica a due fattori nelle impostazioni di sicurezza." : "Catalogo caricato.");
      } catch (_) { if (valid(token)) { setMessage(receipt ? "Scrittura riuscita, ma il catalogo aggiornato non è disponibile. Ricarica prima di continuare." : "Connessione non disponibile. Riprova."); reload.hidden = false; } }
      finally { if (valid(token)) { if (controller === localController) controller = null; busy = false; controls(); } }
    }
    async function write(kind, payload) {
      const token = epoch; if (!valid(token) || busy) return;
      const key = JSON.stringify([kind, payload]);
      if (!pending || pending.key !== key) pending = { key, requestId: root.crypto.randomUUID() };
      const requestId = pending.requestId;
      const body = JSON.stringify({ ...payload, request_id: requestId });
      busy = true; controls(); setMessage(kind === "draft" ? "Salvataggio bozza…" : "Pubblicazione…");
      const localController = new root.AbortController(); controller = localController;
      let succeeded = false;
      try {
        const response = await apiFetch(`${API_BASE}/api/bookings/catalog/${kind}`, { method: kind === "draft" ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body, signal: localController.signal });
        if (!valid(token)) return;
        if (!response.ok) { if (![503, 409].includes(response.status)) pending = null; fail(response); return; }
        const data = await response.json(); if (!valid(token)) return;
        if (!validState(data) || !Number.isSafeInteger(data.applied_revision)) { setMessage("Risposta non valida. Ricarica prima di continuare."); state = null; controls(); reload.hidden = false; return; }
        receipt = { requestId, appliedRevision: data.applied_revision }; state = null; controls(); succeeded = true;
      } catch (_) { if (valid(token)) setMessage("Esito incerto per un problema di connessione. Riprova senza modificare i dati."); }
      finally { if (valid(token)) { if (controller === localController) controller = null; busy = false; controls(); } }
      if (succeeded && valid(token)) await load();
    }
    async function loadAudit() {
      const token = epoch; if (!valid(token) || busy || !state) return;
      busy = true; controls(); audit.textContent = tr("Caricamento cronologia…");
      const localController = new root.AbortController(); controller = localController;
      try {
        const response = await apiFetch(`${API_BASE}/api/bookings/catalog/audit`, { signal: localController.signal });
        if (!valid(token)) return;
        if (!response.ok) { audit.textContent = tr("Cronologia non disponibile."); return; }
        const data = await response.json(); if (!valid(token)) return;
        audit.replaceChildren();
        if (!Array.isArray(data.events)) { audit.textContent = tr("Cronologia non disponibile."); return; }
        if (!data.events.length) { audit.textContent = tr("Nessun evento."); return; }
        const list = node("ul", null, audit);
        data.events.slice(0, 100).forEach((event) => {
          if (!event || typeof event.action !== "string" || !/^[a-z_]{1,40}$/.test(event.action) || !Number.isSafeInteger(event.revision) || event.revision < 0 || typeof event.created_at !== "string" || !/^\d{4}-\d{2}-\d{2}T[0-9:.+Z-]{1,35}$/.test(event.created_at) || typeof event.request_id !== "string" || !/^[0-9a-f-]{36}$/i.test(event.request_id)) return;
          node("li", `${event.action} · ${event.revision} · ${event.created_at} · ${event.request_id}`, list);
        });
      } catch (_) { if (valid(token)) audit.textContent = tr("Cronologia non disponibile."); }
      finally { if (valid(token)) { if (controller === localController) controller = null; busy = false; controls(); } }
    }
    async function saveDraft() {
      const token = epoch; if (!valid(token) || busy || !permissions().save || !form) return;
      let catalog; try { catalog = form.read(state.revision + 1); } catch (_) { setMessage("Seleziona un catalogo prima di salvare."); return; }
      if (state.organization_sector && catalog.sector !== state.organization_sector) { setMessage("L'esempio scelto non corrisponde al settore dell'organizzazione."); return; }
      await write("draft", { expected_revision: state.revision, catalog });
    }
    async function publishDraft() {
      const token = epoch; if (!valid(token) || busy || !permissions().publish || !state?.draft) return;
      const expected = state.revision;
      if (typeof deps.confirm !== "function") { setMessage("La conferma di pubblicazione non è disponibile."); return; }
      busy = true; controls(); let confirmed = false;
      try { confirmed = await deps.confirm({ title: tr("Pubblicare il catalogo?"), description: tr("Verifica la bozza. Per pubblicare serve la verifica a due fattori."), label: tr("Pubblica") }); }
      catch (_) { confirmed = false; }
      if (!valid(token)) return;
      busy = false; controls();
      if (!confirmed || !permissions().publish || state.revision !== expected) return;
      await write("publish", { expected_revision: expected });
    }
    function refreshLanguage() {
      const token = epoch; if (!valid(token) || busy || !state || !form) return;
      let draft = null; try { draft = form.read(state.revision + 1); } catch (_) { /* Empty catalog. */ }
      const message = statusKey, securityVisible = !security.hidden, reloadVisible = !reload.hidden, restoreVisible = !restore.hidden;
      container.replaceChildren(); setup(); apply(state);
      if (draft) form.load(draft);
      security.hidden = !securityVisible; reload.hidden = !reloadVisible; restore.hidden = !restoreVisible;
      controls(); setMessage(message);
    }
    function enter() {
      invalidate(); identity = signature();
      if (!identity || getSession()?.ruolo === "staff") return;
      active = true; setup(); load();
    }
    return Object.freeze({ enter, leave, invalidate, refreshLanguage });
  }
  root.MelpisBookingCatalog = Object.freeze({ create });
})(typeof window !== "undefined" ? window : globalThis);
