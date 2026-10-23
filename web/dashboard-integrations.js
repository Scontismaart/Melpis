(function attachDashboardIntegrations(root, factory) {
  const api = factory(root);
  if (root) root.MelpisDashboardIntegrations = api;
})(typeof window !== "undefined" ? window : globalThis, function createDashboardIntegrations(root) {
  return Object.freeze({
    create(deps) {
      const document = root.document;
      const window = root;
      const { API_BASE, apiFetch, toast, confermaDestructiva, localeCorrente, getContext,
        apriVistaImpostazioni, _tDash, _sanitize, aggiornaRecensioni } = deps;
      let active = false;
      let currentScope = null;
      let activeTab = null;
      let lifecycleEpoch = 0;
      const providerFlights = new Map();
      const actionFlights = new Set();
      const oauthTimers = new Set();

      function claimAction(token, actionName) {
        const original = JSON.parse(token?.context || "{}");
        const key = `${JSON.stringify([original.userId, original.sessionOrganizationId, original.selectedOrganizationId])}:${actionName}`;
        if (actionFlights.has(key)) return null;
        actionFlights.add(key);
        return () => actionFlights.delete(key);
      }

      function sameIntegrationScope(token) {
        const original = JSON.parse(token?.context || "{}");
        const current = getContext?.() || {};
        return Boolean(original.userId && original.sessionOrganizationId
          && original.userId === current.userId
          && original.sessionOrganizationId === current.sessionOrganizationId
          && original.selectedOrganizationId === current.selectedOrganizationId);
      }

      function scheduleOAuthFeedback(message, context, type = "success") {
        const captured = JSON.stringify(context || {});
        const timer = setTimeout(() => {
          oauthTimers.delete(timer);
          const now = getContext?.() || {};
          if (now.view === "impostazioni" && JSON.stringify([now.userId, now.sessionOrganizationId, now.selectedOrganizationId])
            === JSON.stringify([context?.userId, context?.sessionOrganizationId, context?.selectedOrganizationId])
            && captured) toast(message, type);
        }, 400);
        oauthTimers.add(timer);
      }

      function captureIntegrationContext() {
        return { context: JSON.stringify(getContext?.() || {}), epoch: lifecycleEpoch };
      }

      function isIntegrationContextCurrent(token) {
        const expectedView = activeTab === "reviews-overview" ? "recensioni" : "impostazioni";
        const context = getContext?.();
        return Boolean(active && token && token.epoch === lifecycleEpoch
          && context?.userId && context?.sessionOrganizationId
          && context?.view === expectedView
          && JSON.stringify(context) === token.context);
      }

      async function readActionResponse(response, token) {
        const currentAfterRequest = isIntegrationContextCurrent(token);
        const body = await response.json().catch(() => ({}));
        return { body, current: currentAfterRequest && isIntegrationContextCurrent(token) };
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
  const actionContext = captureIntegrationContext();
  const releaseAction = claimAction(actionContext, `integration-test:${canale}`);
  if (!releaseAction || !isIntegrationContextCurrent(actionContext)) { releaseAction?.(); return; }
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
    const { body: data, current } = await readActionResponse(res, actionContext);
    if (!current) return;

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
    if (!isIntegrationContextCurrent(actionContext)) return;
    if (feedbackEl) {
      feedbackEl.hidden = false;
      feedbackEl.className = "integ-test-feedback error";
      feedbackEl.textContent = "Errore di rete o server non raggiungibile.";
    }
    toast("Impossibile eseguire il test di connessione.", "error");
  } finally {
    releaseAction();
    if (sameIntegrationScope(actionContext)) {
      btn.classList.remove("loading");
      btn.disabled = false;
      btn.innerHTML = originalHtml;
    }
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
  const contextToken = captureIntegrationContext();
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
    if (!isIntegrationContextCurrent(contextToken)) return;
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      if (!isIntegrationContextCurrent(contextToken)) return;
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
    if (!isIntegrationContextCurrent(contextToken)) return;

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
    if (!isIntegrationContextCurrent(contextToken)) return;
    console.warn("[WhatsApp] Impossibile recuperare lo stato della connessione.");
    if (connectedCard) connectedCard.hidden = false;
    if (wizardCard) wizardCard.hidden = true;
    if (connectedSub) connectedSub.textContent = "Errore di connessione con il server.";
    if (statoEl) _aggiornaBadgeStato(statoEl, "error", "Errore di rete");
  }
}

async function caricaStatoInstagram() {
  const contextToken = captureIntegrationContext();
  const connectedCard = document.getElementById("integ-instagram-connected-card");
  const wizardCard = document.getElementById("integ-instagram-wizard-card");
  const igIdVal = document.getElementById("integ-ig-id");
  const statoEl = document.getElementById("integ-instagram-stato");

  try {
    const res = await apiFetch(`${API_BASE}/api/instagram/account`);
    if (!isIntegrationContextCurrent(contextToken)) return;
    if (res.ok) {
      const d = await res.json();
      if (!isIntegrationContextCurrent(contextToken)) return;
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
      if (!isIntegrationContextCurrent(contextToken)) return;
      const errMsg = _estraiMessaggioErroreApi(res, d, "Permessi insufficienti per Instagram.");
      if (connectedCard) connectedCard.hidden = false;
      if (wizardCard) wizardCard.hidden = true;
      if (igIdVal) igIdVal.textContent = errMsg;
      if (statoEl) _aggiornaBadgeStato(statoEl, res.mfaRequired ? "mfa_required" : "unauthorized");
    } else {
      const d = await res.json().catch(() => ({}));
      if (!isIntegrationContextCurrent(contextToken)) return;
      const errMsg = _estraiMessaggioErroreApi(res, d, "Impossibile recuperare lo stato di Instagram.");
      if (connectedCard) connectedCard.hidden = false;
      if (wizardCard) wizardCard.hidden = true;
      if (igIdVal) igIdVal.textContent = errMsg;
      if (statoEl) _aggiornaBadgeStato(statoEl, "error");
    }
  } catch (err) {
    if (!isIntegrationContextCurrent(contextToken)) return;
    if (connectedCard) connectedCard.hidden = true;
    if (wizardCard) wizardCard.hidden = false;
    if (statoEl) _aggiornaBadgeStato(statoEl, "error", "Errore di rete");
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
  const actionContext = captureIntegrationContext();
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
  const releaseAction = claimAction(actionContext, "wa-connect");
  if (!releaseAction) return;

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
    const contextAfterRequest = isIntegrationContextCurrent(actionContext);
    const data = await res.json().catch(() => ({}));
    if (!contextAfterRequest || !isIntegrationContextCurrent(actionContext)) return;
    if (res.ok) {
      const tokenInput = document.getElementById("wa-access-token");
      if (tokenInput?.value.trim() === token) tokenInput.value = "";
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
    if (!isIntegrationContextCurrent(actionContext)) return;
    if (statusEl) {
      statusEl.textContent = "Errore di connessione con il server.";
      statusEl.className = "security-status err";
    }
  } finally {
    releaseAction();
    if (sameIntegrationScope(actionContext)) {
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = "Verifica e Salva Collegamento";
      }
      if (!isIntegrationContextCurrent(actionContext) && statusEl?.textContent === "Verifica credenziali con Meta Cloud API…") {
        statusEl.textContent = "";
        statusEl.className = "security-status";
      }
    }
  }
});

document.getElementById("wa-wz-send-test-btn")?.addEventListener("click", async () => {
  const actionContext = captureIntegrationContext();
  const testPhone = document.getElementById("wa-wz-test-phone")?.value.trim();
  const idempotencyKey = _getWhatsAppTestIdempotencyKey(testPhone);
  const statusEl = document.getElementById("wa-wz-test-status");
  const btn = document.getElementById("wa-wz-send-test-btn");
  const releaseAction = claimAction(actionContext, "wa-test-wizard");
  if (!releaseAction || !isIntegrationContextCurrent(actionContext)) { releaseAction?.(); return; }

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
    const { body: data, current } = await readActionResponse(res, actionContext);
    if (!current) return;
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
    if (!isIntegrationContextCurrent(actionContext)) return;
    if (statusEl) {
      statusEl.textContent = "Errore di comunicazione col server.";
      statusEl.className = "security-status err";
    }
  } finally {
    releaseAction();
    if (sameIntegrationScope(actionContext) && btn) {
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
  const actionContext = captureIntegrationContext();
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
  const releaseAction = claimAction(actionContext, "wa-test-connected");
  if (!releaseAction || !isIntegrationContextCurrent(actionContext)) { releaseAction?.(); return; }
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
    const { body: d, current } = await readActionResponse(res, actionContext);
    if (!current) return;
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
    if (!isIntegrationContextCurrent(actionContext)) return;
    if (feedback) {
      feedback.hidden = false;
      feedback.className = "integ-test-feedback error";
      feedback.textContent = "Errore di connessione.";
    }
  } finally {
    releaseAction();
    if (sameIntegrationScope(actionContext) && btn) {
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
  const actionContext = captureIntegrationContext();
  const releaseAction = claimAction(actionContext, "wa-disconnect");
  if (!releaseAction || !isIntegrationContextCurrent(actionContext)) { releaseAction?.(); return; }
  const ok = await confermaDestructiva({
    titolo: "Disconnettere WhatsApp Business?",
    descrizione: "L'assistente AI smetterà di rispondere automaticamente ai messaggi in arrivo su questo numero WhatsApp.",
    label: "Disconnetti WhatsApp",
  });
  if (!isIntegrationContextCurrent(actionContext) || !ok) { releaseAction(); return; }

  try {
    const res = await apiFetch(`${API_BASE}/api/whatsapp/disconnect`, { method: "POST" });
    const { body: d, current } = await readActionResponse(res, actionContext);
    if (!current) return;
    if (res.ok) {
      toast("WhatsApp disconnesso con successo.");
      await caricaStatoWhatsApp();
    } else if (res.status !== 403) {
      toast(_estraiMessaggioErroreApi(res, d, "Errore durante la disconnessione."), "error");
    }
  } catch {
    if (!isIntegrationContextCurrent(actionContext)) return;
    toast("Errore di rete.", "error");
  } finally {
    releaseAction();
  }
});

// Instagram Handlers
document.getElementById("ig-connect-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const actionContext = captureIntegrationContext();
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
  const releaseAction = claimAction(actionContext, "instagram-connect");
  if (!releaseAction || !isIntegrationContextCurrent(actionContext)) { releaseAction?.(); return; }

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
    const { body: d, current } = await readActionResponse(res, actionContext);
    if (!current) return;
    if (res.ok) {
      const tokenInput = document.getElementById("ig-token-input");
      if (tokenInput?.value.trim() === igToken) tokenInput.value = "";
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
    if (!isIntegrationContextCurrent(actionContext)) return;
    if (statusEl) {
      statusEl.textContent = "Errore di connessione.";
      statusEl.className = "security-status err";
    }
  } finally {
    releaseAction();
    if (sameIntegrationScope(actionContext) && submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = "Collega Instagram Direct";
    }
  }
});

document.getElementById("integ-instagram-disconnect")?.addEventListener("click", async () => {
  const actionContext = captureIntegrationContext();
  const releaseAction = claimAction(actionContext, "instagram-disconnect");
  if (!releaseAction || !isIntegrationContextCurrent(actionContext)) { releaseAction?.(); return; }
  const ok = await confermaDestructiva({
    titolo: "Disconnettere Instagram Direct?",
    descrizione: "L'assistente AI non risponderà più ai messaggi diretti su questo account Instagram.",
    label: "Disconnetti Instagram",
  });
  if (!isIntegrationContextCurrent(actionContext) || !ok) { releaseAction(); return; }

  try {
    const res = await apiFetch(`${API_BASE}/api/instagram/account`, { method: "DELETE" });
    const { body: d, current } = await readActionResponse(res, actionContext);
    if (!current) return;
    if (res.ok) {
      toast("Instagram Direct disconnesso con successo.");
      const feedbackEl = document.getElementById("integ-instagram-feedback");
      if (feedbackEl) feedbackEl.hidden = true;
      await caricaStatoInstagram();
    } else if (res.status !== 403) {
      toast(_estraiMessaggioErroreApi(res, d, "Errore durante la disconnessione."), "error");
    }
  } catch {
    if (!isIntegrationContextCurrent(actionContext)) return;
    toast("Errore di rete.", "error");
  } finally {
    releaseAction();
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
  const contextToken = captureIntegrationContext();
  const stato = document.getElementById("integ-calendar-stato");
  const sub = document.getElementById("integ-calendar-sub");
  const help = document.getElementById("integ-calendar-help");
  const calIdMeta = document.getElementById("integ-calendar-id");
  const syncModeMeta = document.getElementById("integ-calendar-sync-mode");
  const btnConnect = document.getElementById("integ-calendar-connect");
  const btnCheck = document.getElementById("integ-calendar-check");
  const btnDisconnect = document.getElementById("integ-calendar-disconnect");
  const btnSave = document.getElementById("integ-calendar-save");
  const modeInput = document.getElementById("integ-calendar-mode");
  const calendarIdInput = document.getElementById("integ-calendar-primary-id");
  const resourceIdsInput = document.getElementById("integ-calendar-resource-ids");
  const syncInput = document.getElementById("integ-calendar-sync-enabled");
  const counts = document.getElementById("integ-calendar-counts");

  if (!stato || !sub) return;

  try {
    const res = await apiFetch(`${API_BASE}/api/calendar/status`);
    if (!isIntegrationContextCurrent(contextToken)) return;
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      if (!isIntegrationContextCurrent(contextToken)) return;
      const errMsg = _estraiMessaggioErroreApi(res, d, "Impossibile verificare lo stato di Google Calendar.");
      if (res.status === 403) {
        _aggiornaBadgeStato(stato, res.mfaRequired ? "mfa_required" : "unauthorized");
        sub.textContent = errMsg;
      } else {
        _aggiornaBadgeStato(stato, "error");
        sub.textContent = errMsg;
      }
      if (help) help.textContent = "Lo stato del calendario non è disponibile. Riprova o contatta un amministratore.";
      return;
    }
    const d = await res.json();
    if (!isIntegrationContextCurrent(contextToken)) return;
    mostraStatoCalendar(d, { stato, sub, help, calIdMeta, syncModeMeta, btnConnect, btnCheck, btnDisconnect, counts });
    // Legacy rows predate explicit mode selection. Treat them as internal
    // until an owner deliberately saves a different mode.
    if (modeInput) modeInput.value = ["internal", "internal_calendar_sync", "google_authoritative"].includes(d.mode) ? d.mode : "internal";
    if (calendarIdInput) calendarIdInput.value = typeof d.calendar_id === "string" ? d.calendar_id : "primary";
    if (resourceIdsInput) resourceIdsInput.value = Array.isArray(d.resource_calendar_ids) ? d.resource_calendar_ids.join("\n") : "";
    if (syncInput) syncInput.checked = d.sync_enabled === true;
    if (btnSave) btnSave.disabled = d.enabled === false;
  } catch {
    if (!isIntegrationContextCurrent(contextToken)) return;
    _aggiornaBadgeStato(stato, "error", "Errore di rete");
    sub.textContent = "Errore di connessione con il server";
    if (help) help.textContent = "Lo stato del calendario non è disponibile. Riprova più tardi.";
  }
}

const CALENDAR_MODE_LABELS = Object.freeze({
  internal: "Solo Melpis",
  internal_calendar_sync: "Melpis con Google opzionale",
  google_authoritative: "Google autorevole",
});

const CALENDAR_STATUS_LABELS = Object.freeze({
  not_connected: ["not_connected", "Non collegato", "Google Calendar non è collegato."],
  connected: ["connected", "Connesso", "Google Calendar è collegato."],
  syncing: ["connecting", "Sincronizzazione in corso", "La verifica è in corso."],
  sync_failed: ["error", "Sincronizzazione non riuscita", "La sincronizzazione non è riuscita. I dati Melpis restano disponibili."],
  revoked: ["requires_reauth", "Accesso revocato", "L'accesso Google è stato revocato. Ricollega l'account per continuare."],
  authoritative_unavailable: ["error", "Google non disponibile", "Google è configurato come fonte autorevole ma non è raggiungibile."],
  reconnect_required: ["requires_reauth", "Riconnessione necessaria", "Ricollega Google Calendar per ripristinare l'integrazione."],
  disabled: ["disabled", "Disabilitato", "Google Calendar è disabilitato per questa organizzazione."],
});

const CALENDAR_ERROR_MESSAGES = Object.freeze({
  credentials_missing: "Credenziali Google non disponibili.",
  token_revoked: "L'accesso Google è stato revocato. Ricollega l'account.",
  provider_unavailable: "Google Calendar non è raggiungibile.",
  invalid_calendar: "Il calendario selezionato non è accessibile.",
  availability_unverified: "Non è stato possibile verificare la disponibilità.",
  configuration_invalid: "La configurazione del calendario non è valida.",
});

function mostraStatoCalendar(data, elements) {
  const status = CALENDAR_STATUS_LABELS[data.integration_status]
    || (data.connected ? CALENDAR_STATUS_LABELS.connected : CALENDAR_STATUS_LABELS.not_connected);
  _aggiornaBadgeStato(elements.stato, status[0], status[1]);
  const mode = CALENDAR_MODE_LABELS[data.mode] || CALENDAR_MODE_LABELS.internal;
  const calendarName = typeof data.calendar_id === "string" && data.calendar_id ? data.calendar_id : "primary";
  elements.sub.textContent = `${status[2]} Modalità: ${mode}.`;
  if (elements.calIdMeta) elements.calIdMeta.textContent = calendarName;
  if (elements.syncModeMeta) elements.syncModeMeta.textContent = mode;
  if (elements.counts) {
    elements.counts.textContent = `In attesa: ${Number.isFinite(data.pending_count) ? data.pending_count : 0} · Non riuscite: ${Number.isFinite(data.failed_count) ? data.failed_count : 0} · Esito incerto: ${Number.isFinite(data.uncertain_count) ? data.uncertain_count : 0}`;
  }
  if (elements.help) {
    const lastSync = data.last_sync_at ? new Date(data.last_sync_at) : null;
    const lastSyncText = lastSync && !Number.isNaN(lastSync.getTime())
      ? `Ultimo aggiornamento: ${lastSync.toLocaleString(localeCorrente())}. ` : "";
    const errorText = CALENDAR_ERROR_MESSAGES[data.error_code] || "";
    elements.help.textContent = `${lastSyncText}${errorText || status[2]}`;
  }
  const shouldConnect = data.integration_status === "not_connected"
    || data.integration_status === "revoked" || data.integration_status === "reconnect_required";
  if (elements.btnConnect) elements.btnConnect.hidden = !shouldConnect;
  if (elements.btnCheck) elements.btnCheck.hidden = data.enabled === false || data.connected !== true;
  if (elements.btnDisconnect) elements.btnDisconnect.hidden = !data.connected;
}

function calendarResourceIds() {
  const value = document.getElementById("integ-calendar-resource-ids")?.value || "";
  return [...new Set(value.split(/\r?\n/).map((id) => id.trim()).filter(Boolean))];
}

document.getElementById("integ-calendar-save")?.addEventListener("click", async () => {
  const actionContext = captureIntegrationContext();
  const releaseAction = claimAction(actionContext, "calendar-settings-save");
  if (!releaseAction || !isIntegrationContextCurrent(actionContext)) { releaseAction?.(); return; }
  const button = document.getElementById("integ-calendar-save");
  button.disabled = true;
  try {
    const res = await apiFetch(`${API_BASE}/api/calendar/settings`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        mode: document.getElementById("integ-calendar-mode")?.value || "internal",
        calendar_id: document.getElementById("integ-calendar-primary-id")?.value.trim() || "primary",
        resource_calendar_ids: calendarResourceIds(),
        sync_enabled: document.getElementById("integ-calendar-sync-enabled")?.checked === true,
      }),
    });
    const { body, current } = await readActionResponse(res, actionContext);
    if (!current) return;
    if (!res.ok) {
      const safeMessage = CALENDAR_ERROR_MESSAGES[body?.error_code] || "Impossibile salvare le impostazioni del calendario.";
      toast(safeMessage, "error");
      return;
    }
    toast("Impostazioni del calendario salvate.", "success");
    await caricaStatoCalendar();
  } catch {
    if (isIntegrationContextCurrent(actionContext)) toast("Errore di connessione durante il salvataggio.", "error");
  } finally {
    button.disabled = false;
    releaseAction();
  }
});

document.getElementById("integ-calendar-check")?.addEventListener("click", async () => {
  const actionContext = captureIntegrationContext();
  const releaseAction = claimAction(actionContext, "calendar-check");
  if (!releaseAction || !isIntegrationContextCurrent(actionContext)) { releaseAction?.(); return; }
  const button = document.getElementById("integ-calendar-check");
  button.disabled = true;
  _aggiornaBadgeStato(document.getElementById("integ-calendar-stato"), "connecting", "Sincronizzazione in corso");
  const statusText = document.getElementById("integ-calendar-sub");
  if (statusText) statusText.textContent = "Verifica della disponibilità Google in corso…";
  try {
    const res = await apiFetch(`${API_BASE}/api/calendar/check`, { method: "POST" });
    const { body, current } = await readActionResponse(res, actionContext);
    if (!current) return;
    if (body && typeof body.integration_status === "string") {
      mostraStatoCalendar(body, {
        stato: document.getElementById("integ-calendar-stato"),
        sub: document.getElementById("integ-calendar-sub"),
        help: document.getElementById("integ-calendar-help"),
        calIdMeta: document.getElementById("integ-calendar-id"),
        syncModeMeta: document.getElementById("integ-calendar-sync-mode"),
        btnConnect: document.getElementById("integ-calendar-connect"),
        btnCheck: button,
        btnDisconnect: document.getElementById("integ-calendar-disconnect"),
        counts: document.getElementById("integ-calendar-counts"),
      });
    }
    if (!res.ok) toast(CALENDAR_ERROR_MESSAGES[body?.error_code] || "La verifica del calendario non è riuscita.", "error");
  } catch {
    if (isIntegrationContextCurrent(actionContext)) {
      toast("Errore di connessione durante la verifica.", "error");
      await caricaStatoCalendar();
    }
  } finally {
    button.disabled = false;
    releaseAction();
  }
});

document.getElementById("integ-calendar-connect")?.addEventListener("click", async () => {
  const actionContext = captureIntegrationContext();
  const releaseAction = claimAction(actionContext, "calendar-oauth");
  if (!releaseAction || !isIntegrationContextCurrent(actionContext)) { releaseAction?.(); return; }
  const url = new URL("/api/calendar/auth", window.location.origin);
  const selectedOrg = localStorage.getItem("melpis_selected_organization");
  if (selectedOrg) url.searchParams.set("organization_id", selectedOrg);
  try {
    const checkRes = await apiFetch(url.href, { method: "GET", redirect: "manual" });
    if (!isIntegrationContextCurrent(actionContext)) return;
    if (checkRes.status >= 400) return;
    if (isIntegrationContextCurrent(actionContext)) window.location.href = url.href;
  } catch (_) { return; }
  finally { releaseAction(); }
});

document.getElementById("integ-calendar-disconnect")?.addEventListener("click", async () => {
  const actionContext = captureIntegrationContext();
  const releaseAction = claimAction(actionContext, "calendar-disconnect");
  if (!releaseAction || !isIntegrationContextCurrent(actionContext)) { releaseAction?.(); return; }
  const ok = await confermaDestructiva({
    titolo: "Disconnettere Google Calendar?",
    descrizione: "Le prenotazioni esistenti restano memorizzate in Melpis, ma non verranno più sincronizzate con Google Calendar.",
    label: "Disconnetti",
  });
  if (!isIntegrationContextCurrent(actionContext) || !ok) { releaseAction(); return; }
  try {
    const res = await apiFetch(`${API_BASE}/api/calendar/disconnect`, { method: "DELETE" });
    const { body: d, current } = await readActionResponse(res, actionContext);
    if (!current) return;
    if (res.ok) {
      toast("Google Calendar disconnesso con successo.");
      await caricaStatoCalendar();
    } else if (res.status !== 403) {
      toast(_estraiMessaggioErroreApi(res, d, "Errore durante la disconnessione."), "error");
    }
  } catch {
    if (!isIntegrationContextCurrent(actionContext)) return;
    toast("Errore di connessione.", "error");
  } finally {
    releaseAction();
  }
});

/* Gestione redirect OAuth callback, eseguita dopo Auth e router. */
function gestisciCalendarRedirect() {
  const capturedContext = getContext?.();
  if (!capturedContext?.userId || !capturedContext?.sessionOrganizationId) return;
  const params = new URLSearchParams(window.location.search);
  const cal = params.get("calendar");
  if (cal === "connected") {
    const url = new URL(window.location);
    url.searchParams.delete("calendar");
    window.history.replaceState(window.history.state, "", url);
    apriVistaImpostazioni("calendar");
    scheduleOAuthFeedback("Google Calendar connesso con successo!", capturedContext);
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
    scheduleOAuthFeedback(msgs[reason] || `Errore: ${reason}`, capturedContext, "error");
  }
}

/* ============================================================
   GOOGLE RECENSIONI (BUSINESS PROFILE) — stato, connect, sync, disconnect
   ============================================================ */

async function caricaStatoReviews() {
  const contextToken = captureIntegrationContext();
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
    if (!isIntegrationContextCurrent(contextToken)) return;
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      if (!isIntegrationContextCurrent(contextToken)) return;
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
    if (!isIntegrationContextCurrent(contextToken)) return;
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
    if (!isIntegrationContextCurrent(contextToken)) return;
    setSummaryStatus(null);
    _aggiornaBadgeStato(stato, "error", "Errore di rete");
    sub.textContent = "Errore di connessione con il server";
  }
}

// The Reviews overview badge reflects the same tenant-scoped status endpoint as
// the integration settings page; it must never claim a static connection.
document.getElementById("integ-reviews-connect")?.addEventListener("click", async () => {
  const actionContext = captureIntegrationContext();
  const releaseAction = claimAction(actionContext, "reviews-oauth");
  if (!releaseAction || !isIntegrationContextCurrent(actionContext)) { releaseAction?.(); return; }
  const url = new URL(`${API_BASE}/api/reviews/google/auth`, window.location.origin);
  const selectedOrg = localStorage.getItem("melpis_selected_organization");
  if (selectedOrg) url.searchParams.set("organization_id", selectedOrg);
  try {
    const checkRes = await apiFetch(url.href, { method: "GET", redirect: "manual" });
    if (!isIntegrationContextCurrent(actionContext)) return;
    if (checkRes.status >= 400) return;
    if (isIntegrationContextCurrent(actionContext)) window.location.href = url.href;
  } catch (_) { return; }
  finally { releaseAction(); }
});

document.getElementById("integ-reviews-sync")?.addEventListener("click", async () => {
  const btn = document.getElementById("integ-reviews-sync");
  const feedbackEl = document.getElementById("integ-reviews-feedback");
  if (!btn || btn.classList.contains("loading")) return;
  const actionContext = captureIntegrationContext();
  const releaseAction = claimAction(actionContext, "reviews-sync");
  if (!releaseAction || !isIntegrationContextCurrent(actionContext)) { releaseAction?.(); return; }

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
    const { body: d, current } = await readActionResponse(res, actionContext);
    if (!current) return;
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
    if (!isIntegrationContextCurrent(actionContext)) return;
    if (feedbackEl) {
      feedbackEl.hidden = false;
      feedbackEl.className = "integ-test-feedback error";
      feedbackEl.textContent = "Errore di connessione durante la sincronizzazione.";
    }
    toast("Impossibile sincronizzare le recensioni.", "error");
  } finally {
    releaseAction();
    if (sameIntegrationScope(actionContext)) {
      btn.classList.remove("loading");
      btn.disabled = false;
      btn.innerHTML = originalHtml;
    }
  }
});

document.getElementById("integ-reviews-disconnect")?.addEventListener("click", async () => {
  const actionContext = captureIntegrationContext();
  const releaseAction = claimAction(actionContext, "reviews-disconnect");
  if (!releaseAction || !isIntegrationContextCurrent(actionContext)) { releaseAction?.(); return; }
  const ok = await confermaDestructiva({
    titolo: "Disconnettere Google Recensioni?",
    descrizione: "Le recensioni già importate rimarranno salvate in Melpis, ma non verranno più scaricate nuove recensioni né pubblicate risposte automatiche su Google Business.",
    label: "Disconnetti profilo",
  });
  if (!isIntegrationContextCurrent(actionContext) || !ok) { releaseAction(); return; }

  try {
    const res = await apiFetch(`${API_BASE}/api/reviews/google/disconnect`, { method: "DELETE" });
    const { body: d, current } = await readActionResponse(res, actionContext);
    if (!current) return;
    if (res.ok) {
      toast("Google Business disconnesso con successo.");
      const feedbackEl = document.getElementById("integ-reviews-feedback");
      if (feedbackEl) feedbackEl.hidden = true;
      await caricaStatoReviews();
    } else if (res.status !== 403) {
      toast(_estraiMessaggioErroreApi(res, d, "Errore durante la disconnessione."), "error");
    }
  } catch {
    if (!isIntegrationContextCurrent(actionContext)) return;
    toast("Errore di connessione.", "error");
  } finally {
    releaseAction();
  }
});

/* Gestione redirect OAuth callback per Google Reviews, dopo Auth e router. */
function gestisciReviewsRedirect() {
  const capturedContext = getContext?.();
  if (!capturedContext?.userId || !capturedContext?.sessionOrganizationId) return;
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
    scheduleOAuthFeedback("Google Recensioni connesso con successo!", capturedContext);
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
    scheduleOAuthFeedback(msgs[reason] || `Errore autorizzazione Google: ${reason}`, capturedContext, "error");
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
  const contextToken = captureIntegrationContext();
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
    if (!isIntegrationContextCurrent(contextToken)) return;
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      if (!isIntegrationContextCurrent(contextToken)) return;
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
    if (!isIntegrationContextCurrent(contextToken)) return;
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

  const contextToken = captureIntegrationContext();
  const releaseAction = claimAction(contextToken, "booking-mode");
  if (!releaseAction || !isIntegrationContextCurrent(contextToken)) { releaseAction?.(); return; }
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
    const { body: d, current } = await readActionResponse(res, contextToken);
    if (!current) return;
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
    if (!isIntegrationContextCurrent(contextToken)) return;
    toast("Errore di rete durante l'aggiornamento della modalità.", "error");
  } finally {
    releaseAction();
    if (sameIntegrationScope(contextToken)) {
      btn.disabled = false;
      btn.textContent = originalText;
    }
  }
});

// Disconnessione Gestionale
document.getElementById("integ-booking-disconnect-btn")?.addEventListener("click", async () => {
  const actionContext = captureIntegrationContext();
  const releaseAction = claimAction(actionContext, "booking-disconnect");
  if (!releaseAction || !isIntegrationContextCurrent(actionContext)) { releaseAction?.(); return; }
  const ok = await confermaDestructiva({
    titolo: "Disconnettere il gestionale di prenotazioni?",
    descrizione: "Le credenziali salvate verranno rimosse e Melpis tornerà a gestire le prenotazioni esclusivamente con il database locale integrato.",
    label: "Disconnetti gestionale",
  });
  if (!isIntegrationContextCurrent(actionContext) || !ok) { releaseAction(); return; }

  try {
    const res = await apiFetch(`${API_BASE}/api/v1/integrations/booking`, { method: "DELETE" });
    const { body: d, current } = await readActionResponse(res, actionContext);
    if (!current) return;
    if (res.ok) {
      toast("Gestionale disconnesso con successo. Motore interno ripristinato.", "success");
      const formCard = document.getElementById("integ-booking-form-card");
      if (formCard) formCard.hidden = true;
      await caricaStatoBooking();
    } else if (res.status !== 403) {
      toast(_estraiMessaggioErroreApi(res, d, "Errore durante la rimozione del gestionale."), "error");
    }
  } catch {
    if (!isIntegrationContextCurrent(actionContext)) return;
    toast("Errore di connessione.", "error");
  } finally {
    releaseAction();
  }
});

// Salvataggio Configurazione Provider
document.getElementById("booking-config-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const actionContext = captureIntegrationContext();
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

  const credentialInputIds = ["calcom-api-key", "simplybook-api-key", "apaleo-client-secret", "beds24-invite-code", "zak-api-key"];
  const credentialSnapshot = new Map(credentialInputIds.map((id) => [id, document.getElementById(id)?.value || ""]));
  const credentialsUnchanged = () => credentialInputIds.every((id) => (document.getElementById(id)?.value || "") === credentialSnapshot.get(id));
  const releaseAction = claimAction(actionContext, "booking-config");
  if (!releaseAction || !isIntegrationContextCurrent(actionContext)) { releaseAction?.(); return; }

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
    const { body: d, current } = await readActionResponse(res, actionContext);
    if (!current) return;
    if (res.ok) {
      toast(d.message || "Integrazione gestionale salvata con successo!", "success");
      const formCard = document.getElementById("integ-booking-form-card");
      const shouldClearCredentials = credentialsUnchanged();
      if (formCard && shouldClearCredentials) formCard.hidden = true;
      credentialInputIds.forEach((id) => {
        const el = document.getElementById(id);
        if (el && shouldClearCredentials) el.value = "";
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
    if (!isIntegrationContextCurrent(actionContext)) return;
    if (feedbackEl) {
      feedbackEl.hidden = false;
      feedbackEl.className = "integ-test-feedback error";
      feedbackEl.textContent = "Errore di connessione durante il salvataggio.";
    }
    toast("Impossibile salvare la configurazione del gestionale.", "error");
  } finally {
    releaseAction();
    if (sameIntegrationScope(actionContext) && submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = "Salva Configurazione";
    }
  }
});

/* ============================================================
   INTEGRAZIONE AIRTABLE (FASE 3)
   Invarianti: 1 (Tenant Isolation), 6 (GDPR Sanità), 10 (Zero Secrets)
   ============================================================ */

let _airtableBasesCache = [];

async function caricaStatoAirtable() {
  const contextToken = captureIntegrationContext();
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
    if (!isIntegrationContextCurrent(contextToken)) return;
    if (!res.ok) {
      if (basesListEl) basesListEl.innerHTML = "";
      if (basesEmptyEl) basesEmptyEl.hidden = true;
      const d = await res.json().catch(() => ({}));
      if (!isIntegrationContextCurrent(contextToken)) return;
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
    if (!isIntegrationContextCurrent(contextToken)) return;
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

  const actionContext = captureIntegrationContext();
  const releaseAction = claimAction(actionContext, `airtable-disconnect:${baseId}`);
  if (!releaseAction || !isIntegrationContextCurrent(actionContext)) { releaseAction?.(); return; }
  const confermato = await confermaDestructiva({
    titolo: "Disconnettere la Base Airtable?",
    descrizione: `Stai per rimuovere la connessione alla Base "${baseName}" (${baseId}). Le credenziali salvate verranno revocate e i workflow collegati a questa Base non riceveranno più dati.`,
    label: "Disconnetti Base",
  });

  if (!isIntegrationContextCurrent(actionContext) || !confermato) { releaseAction(); return; }

  try {
    const res = await apiFetch(`${API_BASE}/api/v1/integrations/airtable?base_id=${encodeURIComponent(baseId)}`, {
      method: "DELETE",
    });
    const { body: d, current } = await readActionResponse(res, actionContext);
    if (!current) return;
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
    if (!isIntegrationContextCurrent(actionContext)) return;
    toast("Errore di connessione durante la rimozione della Base.", "error");
  } finally {
    releaseAction();
  }
}

async function caricaEventiWebhookAirtable() {
  const contextToken = captureIntegrationContext();
  const tbody = document.getElementById("airtable-events-tbody");
  const btn = document.getElementById("airtable-events-refresh-btn");
  if (!tbody) return;

  if (btn) {
    btn.disabled = true;
    btn.textContent = "Caricamento…";
  }

  try {
    const res = await apiFetch(`${API_BASE}/api/v1/integrations/airtable/webhooks/events?limit=20`);
    if (!isIntegrationContextCurrent(contextToken)) return;
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      if (!isIntegrationContextCurrent(contextToken)) return;
      const errMsg = _estraiMessaggioErroreApi(res, d, "Impossibile caricare gli eventi webhook.");
      tbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--text-muted); padding: 14px;">${DOMPurify.sanitize(errMsg)}</td></tr>`;
      return;
    }

    const events = await res.json().catch(() => []);
    if (!isIntegrationContextCurrent(contextToken)) return;
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
    if (!isIntegrationContextCurrent(contextToken)) return;
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
  const actionContext = captureIntegrationContext();
  const token = document.getElementById("airtable-token")?.value.trim();
  const baseId = document.getElementById("airtable-base-id")?.value.trim();
  const baseName = document.getElementById("airtable-base-name")?.value.trim() || "";
  const submitBtn = document.getElementById("airtable-connect-submit-btn");
  const feedbackEl = document.getElementById("airtable-connect-feedback");

  if (!token || !baseId) {
    toast("Inserisci Personal Access Token e Base ID.", "error");
    return;
  }
  const releaseAction = claimAction(actionContext, "airtable-connect");
  if (!releaseAction || !isIntegrationContextCurrent(actionContext)) { releaseAction?.(); return; }

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
    const { body: d, current } = await readActionResponse(res, actionContext);
    if (!current) return;
    if (res.ok) {
      toast(d.message || "Base Airtable collegata con successo!", "success");
      const formCard = document.getElementById("integ-airtable-connect-card");
      const tokInput = document.getElementById("airtable-token");
      const baseInput = document.getElementById("airtable-base-id");
      const nameInput = document.getElementById("airtable-base-name");
      const tokenUnchanged = tokInput?.value.trim() === token;
      const baseUnchanged = baseInput?.value.trim() === baseId;
      const nameUnchanged = nameInput?.value.trim() === baseName;
      if (formCard && tokenUnchanged && baseUnchanged && nameUnchanged) formCard.hidden = true;
      if (tokInput && tokenUnchanged) tokInput.value = "";
      if (baseInput && baseUnchanged) baseInput.value = "";
      if (nameInput && nameUnchanged) nameInput.value = "";
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
    if (!isIntegrationContextCurrent(actionContext)) return;
    if (feedbackEl) {
      feedbackEl.hidden = false;
      feedbackEl.className = "integ-test-feedback error";
      feedbackEl.textContent = "Errore di connessione durante la verifica del token.";
    }
    toast("Impossibile contattare il server per la verifica del PAT.", "error");
  } finally {
    releaseAction();
    if (sameIntegrationScope(actionContext) && submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = "Verifica e Connetti Base";
    }
  }
});

// Submit Form Validazione Schema
document.getElementById("airtable-validate-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const actionContext = captureIntegrationContext();
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
  const releaseAction = claimAction(actionContext, `airtable-validate:${baseId}`);
  if (!releaseAction || !isIntegrationContextCurrent(actionContext)) { releaseAction?.(); return; }

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

    const { body: d, current } = await readActionResponse(res, actionContext);
    if (!current) return;
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
    if (!isIntegrationContextCurrent(actionContext)) return;
    if (feedbackEl) {
      feedbackEl.hidden = false;
      feedbackEl.className = "integ-test-feedback error";
      feedbackEl.textContent = "Errore di connessione durante la validazione dello schema.";
    }
    toast("Impossibile convalidare lo schema su Airtable.", "error");
  } finally {
    releaseAction();
    if (sameIntegrationScope(actionContext) && submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = "Valida Schema Tabella";
    }
  }
});

// Submit Form Sottoscrizione Webhook
document.getElementById("airtable-webhook-sub-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const actionContext = captureIntegrationContext();
  const baseId = document.getElementById("airtable-sub-base-select")?.value.trim();
  const webhookId = document.getElementById("airtable-sub-webhook-id")?.value.trim();
  const macSecret = document.getElementById("airtable-sub-mac-secret")?.value.trim();
  const submitBtn = document.getElementById("airtable-sub-submit-btn");
  const feedbackEl = document.getElementById("airtable-sub-feedback");

  if (!baseId || !webhookId || !macSecret) {
    toast("Compila tutti i campi richiesti per la registrazione del webhook.", "error");
    return;
  }
  const releaseAction = claimAction(actionContext, `airtable-webhook:${baseId}:${webhookId}`);
  if (!releaseAction || !isIntegrationContextCurrent(actionContext)) { releaseAction?.(); return; }

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

    const { body: d, current } = await readActionResponse(res, actionContext);
    if (!current) return;
    if (res.ok) {
      const whInput = document.getElementById("airtable-sub-webhook-id");
      const macInput = document.getElementById("airtable-sub-mac-secret");
      if (whInput?.value.trim() === webhookId) whInput.value = "";
      if (macInput?.value === macSecret) macInput.value = "";
      if (feedbackEl) {
        feedbackEl.hidden = false;
        feedbackEl.className = "integ-test-feedback success";
        feedbackEl.textContent = `Webhook registrato con successo! ID: ${d.webhook_id} (Base: ${d.base_id}).`;
      }
      toast("Sottoscrizione webhook registrata con successo!", "success");
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
    if (!isIntegrationContextCurrent(actionContext)) return;
    if (feedbackEl) {
      feedbackEl.hidden = false;
      feedbackEl.className = "integ-test-feedback error";
      feedbackEl.textContent = "Errore di rete durante la registrazione del webhook.";
    }
    toast("Impossibile registrare la sottoscrizione webhook.", "error");
  } finally {
    releaseAction();
    if (sameIntegrationScope(actionContext) && submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = "Registra Sottoscrizione Webhook";
    }
  }
});

// Refresh Eventi Webhook (1 solo listener permanente)
document.getElementById("airtable-events-refresh-btn")?.addEventListener("click", () => {
  caricaEventiWebhookAirtable();
});


      function onEnter(tab) {
        const context = getContext?.();
        if (context?.view !== "impostazioni" || !context?.userId || !context?.sessionOrganizationId) return null;
        const nextScope = JSON.stringify([context.userId, context.sessionOrganizationId, context.selectedOrganizationId]);
        if (currentScope !== nextScope) invalidate();
        currentScope = nextScope;
        active = true;
        activeTab = tab;
        const epoch = ++lifecycleEpoch;
        const loader = ({ whatsapp: caricaStatoWhatsApp, instagram: caricaStatoInstagram,
          calendar: caricaStatoCalendar, reviews: caricaStatoReviews,
          "booking-pms": caricaStatoBooking, airtable: caricaStatoAirtable,
          webhook: caricaStatoAirtable })[tab];
        if (!loader) return null;
        const key = `${currentScope}:${tab}`;
        if (providerFlights.has(key)) return providerFlights.get(key);
        const operation = Promise.resolve().then(() => {
          if (!active || lifecycleEpoch !== epoch || currentScope !== nextScope) return null;
          return loader();
        }).finally(() => { if (providerFlights.get(key) === operation) providerFlights.delete(key); });
        providerFlights.set(key, operation);
        return operation;
      }

      function onExit() {
        active = false;
        activeTab = null;
        lifecycleEpoch += 1;
        providerFlights.clear();
        for (const timer of oauthTimers) clearTimeout(timer);
        oauthTimers.clear();
      }

      function onOverviewEnter() {
        const context = getContext?.();
        if (context?.view !== "recensioni" || !context?.userId || !context?.sessionOrganizationId) return null;
        const nextScope = JSON.stringify([context.userId, context.sessionOrganizationId, context.selectedOrganizationId]);
        if (currentScope !== nextScope) invalidate();
        currentScope = nextScope;
        active = true;
        activeTab = "reviews-overview";
        lifecycleEpoch += 1;
        return caricaStatoReviews();
      }

      function onOverviewExit() {
        if (activeTab !== "reviews-overview") return;
        active = false;
        activeTab = null;
        lifecycleEpoch += 1;
      }

      function invalidate() {
        active = false;
        currentScope = null;
        activeTab = null;
        lifecycleEpoch += 1;
        providerFlights.clear();
        for (const timer of oauthTimers) clearTimeout(timer);
        oauthTimers.clear();
        _airtableBasesCache = [];
        document.querySelectorAll('#wa-connect-form, #ig-connect-form, #booking-config-form, #airtable-connect-form, #airtable-validate-form, #airtable-webhook-sub-form').forEach((form) => form.reset());
        for (const id of ["integ-wa-test-phone-input", "wa-wz-test-phone"]) {
          const input = document.getElementById(id);
          if (input) input.value = "";
        }
        const calendarMode = document.getElementById("integ-calendar-mode");
        if (calendarMode) calendarMode.value = "internal";
        const calendarId = document.getElementById("integ-calendar-primary-id");
        if (calendarId) calendarId.value = "primary";
        const calendarResources = document.getElementById("integ-calendar-resource-ids");
        if (calendarResources) calendarResources.value = "";
        const calendarSync = document.getElementById("integ-calendar-sync-enabled");
        if (calendarSync) calendarSync.checked = false;
        const calendarSave = document.getElementById("integ-calendar-save");
        if (calendarSave) calendarSave.disabled = false;
        const calendarCheck = document.getElementById("integ-calendar-check");
        if (calendarCheck) calendarCheck.disabled = false;
        const defaults = {
          "integ-wa-connected-title": "WhatsApp Business", "integ-wa-connected-sub": "",
          "integ-wa-phone-number-display": "—", "integ-wa-reception-status": "",
          "wa-connect-status": "", "wa-wz-test-status": "",
          "integ-wa-test-feedback": "", "integ-instagram-sub": "",
          "integ-ig-id": "—", "integ-instagram-feedback": "",
          "integ-calendar-sub": "", "integ-calendar-id": "—", "integ-calendar-sync-mode": "Solo Melpis",
          "integ-calendar-counts": "In attesa: 0 · Non riuscite: 0 · Esito incerto: 0",
          "integ-calendar-feedback": "", "integ-reviews-sub": "",
          "integ-reviews-account": "—", "integ-reviews-location": "—",
          "integ-reviews-feedback": "", "integ-booking-title": "Melpis Interno",
          "integ-booking-sub": "", "integ-booking-meta-provider": "Melpis Interno (Built-in)",
          "integ-booking-meta-mode": "", "integ-booking-meta-dpa": "—",
          "integ-booking-meta-sync": "", "integ-booking-feedback": "",
          "integ-airtable-sub": "", "integ-airtable-stato": "",
          "integ-airtable-bases-count": "0", "integ-airtable-auth-type": "—", "integ-airtable-last-update": "—",
          "airtable-bases-list": "", "airtable-events-tbody": "",
        };
        for (const [id, value] of Object.entries(defaults)) {
          const element = document.getElementById(id);
          if (element) element.textContent = value;
        }
        for (const [id, hidden] of Object.entries({
          "integ-whatsapp-connected-card": true, "integ-whatsapp-wizard-card": false, "integ-wa-test-box": true,
          "integ-instagram-connected-card": true, "integ-instagram-wizard-card": false,
          "integ-calendar-connect": true, "integ-calendar-check": true, "integ-calendar-disconnect": true,
          "integ-reviews-connect": true, "integ-reviews-sync": true, "integ-reviews-disconnect": true,
          "integ-booking-honest-badge-container": true, "integ-booking-warning-banner": true,
          "integ-airtable-connect-card": true, "integ-airtable-validate-card": true, "integ-airtable-webhooks-card": true,
        })) {
          const element = document.getElementById(id);
          if (element) element.hidden = hidden;
        }
        for (const [id, defaultLabel] of [
          ["integ-whatsapp-stato", ""], ["integ-instagram-stato", ""],
          ["integ-calendar-stato", ""], ["integ-reviews-stato", ""], ["integ-booking-stato", "Built-in"],
        ]) {
          const element = document.getElementById(id);
          if (!element) continue;
          element.classList.remove("connecting", "error", "requires_reauth", "disabled", "unauthorized", "connected");
          element.classList.add("disconnected");
          const label = element.querySelector(".badge-status-label");
          if (label) label.textContent = defaultLabel;
        }
        document.querySelectorAll('#integ-wa-test-feedback, #integ-instagram-feedback, #integ-calendar-feedback, #integ-reviews-feedback, #integ-booking-feedback, #integ-airtable-feedback, #airtable-connect-feedback, #airtable-validate-feedback, #airtable-sub-feedback, #wa-connect-status, #wa-wz-test-status').forEach((feedback) => {
          feedback.textContent = "";
        });
        document.querySelectorAll('#integ-wa-test-feedback, #integ-instagram-feedback, #integ-calendar-feedback, #integ-reviews-feedback, #integ-booking-feedback, #integ-airtable-feedback').forEach((feedback) => { feedback.hidden = true; });
      }

      function refreshLanguage() {
        if (active && ["reviews", "reviews-overview"].includes(activeTab)) caricaStatoReviews();
      }

      return Object.freeze({
          onEnter, onExit, onOverviewEnter, onOverviewExit, invalidate, refreshLanguage,
          _estraiMessaggioErroreApi: typeof _estraiMessaggioErroreApi === 'function' ? _estraiMessaggioErroreApi : undefined,
          gestisciCalendarRedirect: typeof gestisciCalendarRedirect === 'function' ? gestisciCalendarRedirect : undefined,
          gestisciReviewsRedirect: typeof gestisciReviewsRedirect === 'function' ? gestisciReviewsRedirect : undefined,
      });
    },
  });
});
