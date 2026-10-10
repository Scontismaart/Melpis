(function attachDashboardInbox(root, factory) {
  const api = factory(root);
  if (root) root.MelpisDashboardInbox = api;
})(typeof window !== "undefined" ? window : globalThis, function createDashboardInbox(root) {
  "use strict";
  return Object.freeze({
    create(deps) {
      const { API_BASE, apiFetch, t, localeCorrente, toast, _emptyState, _errorState,
        _skeletonList, ICONS, apriVistaImpostazioni, getContext } = deps;
      const document = root.document;
      const window = root;
      let active = false;
      let epoch = 0;
      let entryScope = null;
      let listSequence = 0;
      let detailSequence = 0;
      let listFlight = null;
      let detailFlight = null;
      let listenersReady = false;
      const mutationLocks = new Set();
      const drafts = new Map();
      const replyAttempts = new Map();

      function scopeKey(context = getContext()) {
        return JSON.stringify([context?.userId, context?.sessionOrganizationId, context?.selectedOrganizationId]);
      }
      function capture() { return { context: getContext(), epoch }; }
      function current(token, ticketId = null) {
        const now = getContext();
        return active && token?.epoch === epoch && now?.view === "inbox"
          && Boolean(now?.userId && now?.sessionOrganizationId)
          && JSON.stringify(now) === JSON.stringify(token.context)
          && (ticketId === null || String(inboxState.selectedTicketId) === String(ticketId));
      }
      function clearPrivateDom() {
        for (const id of ["inbox-list", "inbox-thread-messages", "inbox-detail-actions"]) {
          const el = document.getElementById(id);
          if (el) { el.replaceChildren(); delete el.dataset.ticketId; delete el.dataset.ticketStatus; }
        }
        const input = document.getElementById("inbox-message-input");
        if (input) { input.value = ""; input.disabled = false; }
        const send = document.getElementById("inbox-send-btn");
        if (send) send.disabled = false;
        const search = document.getElementById("inbox-search-input");
        if (search) search.value = "";
        document.querySelectorAll("[data-inbox-channel]").forEach((chip) => {
          chip.classList.toggle("active", chip.dataset.inboxChannel === "all");
        });
        document.querySelectorAll("[data-inbox-status]").forEach((chip) => {
          chip.classList.toggle("active", chip.dataset.inboxStatus === "all");
        });
        document.querySelector(".inbox-view")?.classList.remove("show-detail");
        for (const id of ["inbox-detail-name", "inbox-detail-phone", "inbox-context-number", "inbox-context-channel", "inbox-context-status", "inbox-context-priority", "inbox-context-assigned", "inbox-context-last-message", "inbox-detail-status"]) {
          const el = document.getElementById(id);
          if (el) el.textContent = "";
        }
        const badge = document.getElementById("inbox-detail-channel");
        if (badge) badge.replaceChildren();
        const avatar = document.getElementById("inbox-detail-avatar");
        if (avatar) { avatar.textContent = ""; avatar.removeAttribute("style"); }
        const strip = document.getElementById("inbox-status-strip");
        if (strip) { strip.hidden = true; strip.replaceChildren(); }
        const aiBadge = document.getElementById("inbox-ai-badge");
        if (aiBadge) aiBadge.replaceChildren();
        const reply = document.getElementById("inbox-reply-form");
        if (reply) reply.hidden = true;
        aggiornaContatoriFiltri();
        mostraPlaceholderDettaglio();
      }
      function invalidate() {
        fermaInboxPolling();
        active = false;
        epoch++;
        listSequence++;
        detailSequence++;
        listFlight = null;
        detailFlight = null;
        entryScope = null;
        drafts.clear();
        replyAttempts.clear();
        inboxState = { channelFilter: "all", statusFilter: "all", search: "", selectedTicketId: null,
          tickets: [], team: [], pendingClaims: new Set(), isLoading: false };
        clearPrivateDom();
      }
      function ensureScope() {
        const context = getContext();
        if (entryScope !== null && entryScope !== scopeKey(context)) {
          invalidate();
          if (context?.view === "inbox" && context.userId && context.sessionOrganizationId) onEnter();
        }
      }
      function onEnter() {
        const context = getContext();
        if (context?.view !== "inbox" || !context.userId || !context.sessionOrganizationId) return;
        if (active && entryScope === scopeKey(context)) return;
        if (entryScope !== null && entryScope !== scopeKey(context)) invalidate();
        active = true;
        epoch++;
        entryScope = scopeKey(context);
        if (!listenersReady) { inizializzaEventiInbox(); listenersReady = true; }
        avviaInboxPolling();
      }
      function onExit() {
        active = false;
        epoch++;
        listSequence++;
        detailSequence++;
        listFlight = null;
        detailFlight = null;
        fermaInboxPolling();
      }
      function load() { onEnter(); return caricaInbox(false); }
      function refreshLanguage() {
        ensureScope();
        if (!active) return;
        renderInboxConversazioni();
        if (inboxState.selectedTicketId) void caricaDettaglioTicket(inboxState.selectedTicketId, true, {
          refreshMessages: false, forceRender: true,
        });
      }
      function lockKey(token, ticketId, action) { return JSON.stringify([scopeKey(token.context), String(ticketId), action]); }
      async function mutate(ticket, action, request, success, failure, button) {
        ensureScope();
        const token = capture();
        if (!current(token, ticket.id)) return;
        const key = lockKey(token, ticket.id, action);
        if (mutationLocks.has(key)) return;
        mutationLocks.add(key);
        if (button) button.disabled = true;
        try {
          const res = await request();
          if (!current(token, ticket.id)) return;
          if (res.status === 403) { invalidate(); return; }
          if (!res.ok) throw new Error(failure);
          toast(success, "success");
          await caricaInbox(false, true);
        } catch (err) {
          if (current(token, ticket.id)) toast(failure, "error");
        } finally {
          mutationLocks.delete(key);
          if (current(token, ticket.id)) {
            void caricaDettaglioTicket(ticket.id, true, { refreshMessages: false, forceRender: true });
          }
        }
      }
      function claimTicket(ticket, button) {
        return mutate(ticket, "claim", () => apiFetch(`${API_BASE}/api/inbox/claim/${encodeURIComponent(ticket.id)}`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ expected_version: ticket.version }),
        }), "Hai preso in carico la conversazione", "Impossibile fare il claim del ticket.", button);
      }
/* ============================================================
   INBOX (HITL) — Layout a 2 colonne
   ============================================================ */

let inboxState = {
  channelFilter: "all",
  statusFilter: "all",
  search: "",
  selectedTicketId: null,
  tickets: [],
  team: [],
  pendingClaims: new Set(),
  isLoading: false,
};

const TICKET_STATUS_LABEL = {
  AI_ACTIVE: "AI",
  PENDING_STAFF: "Richiede operatore",
  CLAIMED: "Preso in carico",
  RESOLVED: "Risolto",
};

function labelStatoTicketInbox(status) {
  const translationKey = {
    AI_ACTIVE: "inbox:filters.status_ai",
    PENDING_STAFF: "inbox:filters.status_pending",
    CLAIMED: "inbox:filters.status_claimed",
    RESOLVED: "inbox:filters.status_resolved",
  }[status];
  return translationKey ? t(translationKey) : TICKET_STATUS_LABEL[status] || status || "Aperta";
}

const MESSAGE_STATUS_LABEL = {
  received_pending_ai: "received_pending_ai",
  processing: "processing",
  handled: "handled",
  queued: "queued",
  sending_ambiguous: "sending_ambiguous",
  sent: "sent",
  delivered: "delivered",
  read: "read",
  failed: "failed",
};

function labelStatoMessaggioInbox(status) {
  const key = MESSAGE_STATUS_LABEL[status];
  return key ? t(`inbox:chat.message_status.${key}`) : status;
}

function aggiornaStatoPulsanteClaimInbox(button, ticketId) {
  const isPending = mutationLocks.has(lockKey(capture(), ticketId, "claim"));
  button.disabled = isPending;
  button.textContent = t(isPending ? "inbox:chat.claim_loading" : "inbox:chat.claim_btn");
}

const AVATAR_COLOR_PALETTES = [
  { bg: "#EBF5FF", text: "#1E40AF" },
  { bg: "#F0FDF4", text: "#166534" },
  { bg: "#FEF3C7", text: "#92400E" },
  { bg: "#FEE2E2", text: "#991B1B" },
  { bg: "#F3E8FF", text: "#6B21A8" },
  { bg: "#ECFDF5", text: "#065F46" },
  { bg: "#FFF1F2", text: "#9F1239" },
  { bg: "#F5F3FF", text: "#5B21B6" },
];

function _getAvatarColors(identifier) {
  let hash = 0;
  const str = String(identifier || "");
  for (let i = 0; i < str.length; i++) {
    hash = (hash << 5) - hash + str.charCodeAt(i);
    hash |= 0;
  }
  return AVATAR_COLOR_PALETTES[Math.abs(hash) % AVATAR_COLOR_PALETTES.length];
}

function _getAvatarInitial(nameOrPhone) {
  if (!nameOrPhone) return "C";
  const trimmed = nameOrPhone.trim();
  if (trimmed.startsWith("+")) {
    return trimmed.slice(-2);
  }
  const parts = trimmed.split(" ").filter(Boolean);
  if (parts.length >= 2) {
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }
  return trimmed.slice(0, 2).toUpperCase();
}

function formatInboxDate(value) {
  if (!value) return "";
  const d = new Date(value);
  return d.toLocaleString(localeCorrente(), { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function formatRelativeTime(value) {
  if (!value) return "";
  const d = new Date(value);
  const now = new Date();
  const diffSec = Math.floor((now - d) / 1000);
  if (diffSec < 60) return "Adesso";
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin} min fa`;
  const diffOre = Math.floor(diffMin / 60);
  if (diffOre < 24 && d.getDate() === now.getDate()) return `${diffOre} ore fa`;
  if (diffOre < 48 && (now.getDate() - d.getDate() === 1 || diffOre < 24)) return "Ieri";
  return d.toLocaleDateString(localeCorrente(), { day: "2-digit", month: "short" });
}

function formatSla(sla_due_at, is_overdue) {
  if (!sla_due_at) return null;
  const due = new Date(sla_due_at);
  const now = new Date();
  const minutes = Math.max(0, Math.round((due - now) / 60000));
  if (is_overdue) return { text: "SLA superato", overdue: true };
  if (minutes <= 0) return { text: "SLA scaduto", overdue: true };
  return { text: `SLA ${minutes} min`, overdue: false };
}

function formatMessageContent(rawText) {
  const fragment = document.createDocumentFragment();
  const source = String(rawText || "");
  const token = /\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)|((?:https?:\/\/|www\.)[^\s<)]+)/gi;
  let offset = 0;
  function appendText(value) {
    const lines = value.split("\n");
    lines.forEach((line, index) => {
      if (index) fragment.appendChild(document.createElement("br"));
      if (line) fragment.appendChild(document.createTextNode(line));
    });
  }
  for (const match of source.matchAll(token)) {
    appendText(source.slice(offset, match.index));
    const label = match[1] || match[3];
    const candidate = match[2] || match[3];
    try {
      const url = new URL(candidate.startsWith("www.") ? `https://${candidate}` : candidate);
      if (!["http:", "https:"].includes(url.protocol)) throw new Error("Unsafe URL");
      const link = document.createElement("a");
      link.href = url.href;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.className = "thread-link";
      link.textContent = label;
      fragment.appendChild(link);
    } catch {
      appendText(match[0]);
    }
    offset = match.index + match[0].length;
  }
  appendText(source.slice(offset));
  return fragment;
}

/* ---------- Polling & Caricamento Inbox ---------- */

let inboxPollingTimer = null;

function avviaInboxPolling() {
  fermaInboxPolling();
  if (!active || document.hidden) return;
  inboxPollingTimer = root.setInterval(() => {
    ensureScope();
    if (!active || document.hidden || getContext()?.view !== "inbox") {
      fermaInboxPolling();
      return;
    }
    if (!listFlight) void caricaInbox(true);
  }, 3000);
}

function fermaInboxPolling() {
  if (inboxPollingTimer) {
    root.clearInterval(inboxPollingTimer);
    inboxPollingTimer = null;
  }
}

function caricaInbox(silent = false, force = false) {
  ensureScope();
  if (!active || getContext()?.view !== "inbox") return Promise.resolve();
  if (!force && listFlight && listFlight.scope === entryScope) return listFlight.promise;
  const sequence = ++listSequence;
  const token = capture();
  const promise = runInboxLoad(silent, sequence, token);
  const flight = { scope: entryScope, promise };
  listFlight = flight;
  void promise.then(() => { if (listFlight === flight) listFlight = null; }, () => { if (listFlight === flight) listFlight = null; });
  return promise;
}

async function runInboxLoad(silent, sequence, token) {
  const container = document.getElementById("inbox-list");
  if (!container) return;
  const isCurrent = () => sequence === listSequence && current(token);

  if (!silent && !inboxState.tickets.length) {
    container.innerHTML = _skeletonList(4);
  }

  try {
    const [ticketsRes, teamRes] = await Promise.all([
      apiFetch(`${API_BASE}/api/inbox/tickets?limit=100`),
      apiFetch(`${API_BASE}/api/inbox/team`).catch(() => ({ ok: false })),
    ]);
    if (!isCurrent()) return;
    if (teamRes.status === 403) { invalidate(); return; }

    if (ticketsRes.status === 403) { invalidate(); return; }
    if (!ticketsRes.ok) {
      if (!silent) {
        container.innerHTML = "";
        container.appendChild(_errorState("Impossibile caricare le conversazioni.", () => caricaInbox()));
      }
      return;
    }

    const data = await ticketsRes.json();
    if (!isCurrent()) return;
    const teamData = teamRes.ok ? await teamRes.json().catch(() => ({ members: [] })) : null;
    if (!isCurrent()) return;
    const newTickets = data.tickets || [];

    const prevSig = (inboxState.tickets || []).map(t => `${t.id}:${t.last_message_at}:${t.ticket_status}:${t.escalation_failed}:${t.unread_count || 0}:${t.last_message_preview}`).join("|");
    const newSig = newTickets.map(t => `${t.id}:${t.last_message_at}:${t.ticket_status}:${t.escalation_failed}:${t.unread_count || 0}:${t.last_message_preview}`).join("|");

    inboxState.tickets = newTickets;
    if (teamData) inboxState.team = teamData.members || [];

    aggiornaContatoriFiltri();

    if (prevSig !== newSig || !silent) {
      renderInboxConversazioni();
    }

    // Se c'era un ticket selezionato o siamo su desktop e nessun ticket è selezionato, seleziona il primo
    if (inboxState.selectedTicketId) {
      const exists = inboxState.tickets.some((t) => String(t.id) === String(inboxState.selectedTicketId));
      if (exists) {
        await caricaDettaglioTicket(inboxState.selectedTicketId, silent);
        if (!isCurrent()) return;
      } else {
        const firstVisible = getFilteredTickets()[0];
        if (firstVisible && window.innerWidth >= 1024) {
          selezionaTicket(firstVisible.id);
        } else {
          inboxState.selectedTicketId = null;
          mostraPlaceholderDettaglio();
        }
      }
    } else if (window.innerWidth >= 768) {
      const firstVisible = getFilteredTickets()[0];
      if (firstVisible) {
        selezionaTicket(firstVisible.id);
      } else {
        mostraPlaceholderDettaglio();
      }
    }
  } catch (err) {
    if (!isCurrent()) return;
    console.error("Errore caricamento inbox");
    if (!silent) {
      container.innerHTML = "";
      container.appendChild(_errorState("Impossibile caricare l'inbox.", () => caricaInbox()));
    }
  }
}

function getFilteredTickets() {
  let list = [...inboxState.tickets];

  // 1. Filtro Canale
  const ch = inboxState.channelFilter;
  if (ch === "whatsapp") {
    list = list.filter((t) => (t.canale || "whatsapp").toLowerCase() === "whatsapp");
  } else if (ch === "instagram") {
    list = list.filter((t) => (t.canale || "").toLowerCase() === "instagram");
  }

  // 2. Filtro Stato & Gestione
  const st = inboxState.statusFilter;
  if (st === "ai") {
    list = list.filter((t) => t.ticket_status === "AI_ACTIVE");
  } else if (st === "human") {
    list = list.filter((t) => t.ticket_status === "CLAIMED" || t.ticket_status === "PENDING_STAFF");
  } else if (st === "escalated") {
    list = list.filter((t) => t.escalation_failed || t.ticket_status === "PENDING_STAFF" || t.priorita === "alta" || t.is_overdue);
  } else if (st === "resolved") {
    list = list.filter((t) => t.ticket_status === "RESOLVED");
  }

  // 3. Ricerca veloce per numero, testo o operatore
  if (inboxState.search) {
    const q = inboxState.search.toLowerCase().trim();
    list = list.filter((t) => {
      const phone = (t.phone_number || "").toLowerCase();
      const preview = (t.last_message_preview || "").toLowerCase();
      const name = (t.assigned_nome || "").toLowerCase();
      return phone.includes(q) || preview.includes(q) || name.includes(q);
    });
  }

  return list;
}

function aggiornaContatoriFiltri() {
  const all = inboxState.tickets;
  const countAll = all.length;
  const countWa = all.filter((t) => (t.canale || "whatsapp").toLowerCase() === "whatsapp").length;
  const countIg = all.filter((t) => (t.canale || "").toLowerCase() === "instagram").length;
  const countEscalated = all.filter((t) => t.escalation_failed || t.ticket_status === "PENDING_STAFF" || t.priorita === "alta" || t.is_overdue).length;

  const elAll = document.getElementById("chip-cnt-all");
  const elWa = document.getElementById("chip-cnt-wa");
  const elIg = document.getElementById("chip-cnt-ig");
  const elEsc = document.getElementById("chip-cnt-escalated");
  const elTotal = document.getElementById("inbox-total-count");

  if (elAll) elAll.textContent = countAll;
  if (elWa) elWa.textContent = countWa;
  if (elIg) elIg.textContent = countIg;
  if (elEsc) elEsc.textContent = countEscalated;
  if (elTotal) elTotal.textContent = countAll;
}

function renderInboxConversazioni() {
  const container = document.getElementById("inbox-list");
  if (!container) return;

  const tickets = getFilteredTickets();
  container.innerHTML = "";

  if (!tickets.length) {
    if (!inboxState.tickets.length) {
      const waConfigured = Boolean(window._waConnected || false);
      if (!waConfigured) {
        container.appendChild(
          _emptyState(
            ICONS.inbox,
            t("inbox:filters.empty_no_conversations"),
            t("inbox:filters.empty_connect_channels"),
            t("inbox:filters.connect_channels"),
            () => {
              apriVistaImpostazioni("whatsapp");
            }
          )
        );
      } else {
        container.appendChild(
          _emptyState(
            ICONS.inbox,
            t("inbox:filters.empty_waiting")
          )
        );
      }
    } else {
      container.appendChild(
        _emptyState(
          ICONS.inbox,
          t("inbox:filters.empty_no_results"),
          t("inbox:filters.empty_filter_results"),
          t("inbox:filters.show_all"),
          () => {
            inboxState.channelFilter = "all";
            inboxState.statusFilter = "all";
            inboxState.search = "";
            const searchInput = document.getElementById("inbox-search-input");
            if (searchInput) searchInput.value = "";
            document.querySelectorAll("[data-inbox-channel]").forEach((b) => {
              b.classList.toggle("active", b.dataset.inboxChannel === "all");
            });
            document.querySelectorAll("[data-inbox-status]").forEach((b) => {
              b.classList.toggle("active", b.dataset.inboxStatus === "all");
            });
            renderInboxConversazioni();
          }
        )
      );
    }
    return;
  }

  tickets.forEach((ticket) => {
    const row = document.createElement("div");
    row.className = "inbox-conv-row";
    if (ticket.id === inboxState.selectedTicketId) {
      row.classList.add("active");
    }
    row.dataset.ticketId = ticket.id;

    // Avatar
    const colors = _getAvatarColors(ticket.phone_number || ticket.id);
    const initial = _getAvatarInitial(ticket.phone_number || "Cliente");
    const avatarEl = document.createElement("div");
    avatarEl.className = "inbox-conv-avatar";
    avatarEl.style.backgroundColor = colors.bg;
    avatarEl.style.color = colors.text;
    avatarEl.textContent = initial;

    // Body
    const bodyEl = document.createElement("div");
    bodyEl.className = "inbox-conv-body";

    // Top Line: Nome + Tempo relativo
    const topLine = document.createElement("div");
    topLine.className = "inbox-conv-top-line";

    const nameEl = document.createElement("span");
    nameEl.className = "inbox-conv-name";
    nameEl.textContent = ticket.phone_number || "Cliente";

    const timeEl = document.createElement("span");
    timeEl.className = "inbox-conv-time";
    timeEl.textContent = formatRelativeTime(ticket.last_message_at || ticket.created_at);

    topLine.appendChild(nameEl);
    topLine.appendChild(timeEl);
    // Meta Line: Canale + Stato
    const metaLine = document.createElement("div");
    metaLine.className = "inbox-conv-meta-line";

    const isIg = (ticket.canale || "").toLowerCase() === "instagram";
    const channelIcon = document.createElement("span");
    channelIcon.className = `conv-channel-icon ${isIg ? "icon-ig" : "icon-wa"}`;
    channelIcon.innerHTML = isIg
      ? '<svg viewBox="0 0 24 24" fill="none"><rect x="3" y="3" width="18" height="18" rx="5" stroke="#E1306C" stroke-width="2"/><circle cx="12" cy="12" r="4" stroke="#E1306C" stroke-width="2"/><circle cx="17.5" cy="6.5" r="1" fill="#E1306C"/></svg>'
      : '<svg viewBox="0 0 24 24" fill="none"><path d="M12 21a9 9 0 1 0-4.5-1.2L3 21l1.2-4.5A9 9 0 0 0 12 21Z" stroke="#25D366" stroke-width="2" stroke-linejoin="round"/></svg>';

    const channelName = document.createElement("span");
    channelName.textContent = isIg ? "Instagram" : "WhatsApp";

    const sep = document.createElement("span");
    sep.className = "conv-meta-sep";
    sep.textContent = "·";

    const statusPill = document.createElement("span");
    statusPill.className = `conv-status-pill status-${(ticket.ticket_status || "").toLowerCase()}`;

    let statusText = labelStatoTicketInbox(ticket.ticket_status);
    let statusIcon = "";
    if (ticket.ticket_status === "AI_ACTIVE") {
      statusIcon = '<svg viewBox="0 0 24 24" fill="none" width="13" height="13"><rect x="3" y="6" width="18" height="14" rx="3" stroke="currentColor" stroke-width="1.8"/><circle cx="8.5" cy="12" r="1.5" fill="currentColor"/><circle cx="15.5" cy="12" r="1.5" fill="currentColor"/><path d="M12 2v4" stroke="currentColor" stroke-width="1.8"/></svg>';
    } else if (ticket.ticket_status === "PENDING_STAFF") {
      statusIcon = '<span class="inbox-dot-red"></span>';
    } else if (ticket.ticket_status === "CLAIMED") {
      statusIcon = '<svg viewBox="0 0 24 24" fill="none" width="13" height="13"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" stroke="currentColor" stroke-width="1.8"/><circle cx="12" cy="7" r="4" stroke="currentColor" stroke-width="1.8"/></svg>';
      if (ticket.assigned_nome) statusText = t("inbox:chat.claimed_by_other", { name: ticket.assigned_nome });
    } else if (ticket.ticket_status === "RESOLVED") {
      statusIcon = '<svg viewBox="0 0 24 24" fill="none" width="13" height="13"><path d="M5 12l5 5L20 7" stroke="#10b981" stroke-width="2.2" stroke-linecap="round"/></svg>';
    }

    statusPill.innerHTML = statusIcon;
    const statusLabel = document.createElement("span");
    statusLabel.textContent = statusText;
    statusPill.append(" ", statusLabel);

    metaLine.appendChild(channelIcon);
    metaLine.appendChild(channelName);
    metaLine.appendChild(sep);
    metaLine.appendChild(statusPill);
    if (ticket.escalation_failed) {
      const alert = document.createElement("span");
      alert.className = "conv-status-pill status-pending_staff";
      alert.textContent = t("inbox:filters.escalation_failed");
      alert.setAttribute("role", "alert");
      metaLine.appendChild(alert);
    }

    // Preview
    const previewEl = document.createElement("p");
    previewEl.className = "inbox-conv-preview";
    previewEl.textContent = ticket.last_message_preview || "(Nessun messaggio recente)";

    bodyEl.appendChild(topLine);
    bodyEl.appendChild(metaLine);
    bodyEl.appendChild(previewEl);

    row.appendChild(avatarEl);
    row.appendChild(bodyEl);

    row.addEventListener("click", () => {
      selezionaTicket(ticket.id);
      // Su mobile switcha a vista dettaglio
      if (window.innerWidth < 768) {
        document.querySelector(".inbox-view")?.classList.add("show-detail");
      }
    });

    container.appendChild(row);
  });
}

function selezionaTicket(ticketId) {
  ensureScope();
  if (!active || !inboxState.tickets.some((ticket) => String(ticket.id) === String(ticketId))) return;
  const input = document.getElementById("inbox-message-input");
  if (input && inboxState.selectedTicketId !== null) drafts.set(String(inboxState.selectedTicketId), input.value);
  inboxState.selectedTicketId = ticketId;
  detailSequence++;
  detailFlight = null;
  if (input) input.value = drafts.get(String(ticketId)) || "";

  // Evidenzia riga attiva
  document.querySelectorAll(".inbox-conv-row").forEach((row) => {
    row.classList.toggle("active", row.dataset.ticketId === String(ticketId));
  });

  caricaDettaglioTicket(ticketId);
}

function mostraPlaceholderDettaglio() {
  const emptyEl = document.getElementById("inbox-detail-empty");
  const contentEl = document.getElementById("inbox-detail-content");
  if (emptyEl) emptyEl.hidden = false;
  if (contentEl) contentEl.hidden = true;
}

function creaControlliFeedback(m) {
  const container = document.createElement("div");
  container.className = "thread-feedback";
  const ticketId = inboxState.selectedTicketId;
  const upBtn = document.createElement("button");
  upBtn.type = "button";
  upBtn.className = "thread-feedback-btn";
  upBtn.innerHTML = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg> <span></span>';
  upBtn.querySelector("span").textContent = m.feedback_staff_up || 0;
  upBtn.title = "Risposta appropriata";
  const downBtn = document.createElement("button");
  downBtn.type = "button";
  downBtn.className = "thread-feedback-btn";
  downBtn.innerHTML = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zm7-13h3a2 2 0 0 1 2-2v7a2 2 0 0 1-2 2h-3"/></svg> <span></span>';
  downBtn.querySelector("span").textContent = m.feedback_staff_down || 0;
  downBtn.title = "Risposta da migliorare";
  function liveButtons() {
    const rows = document.getElementById("inbox-thread-messages")?.querySelectorAll(".inbox-msg-row[data-msg-id]") || [];
    const row = Array.from(rows).find((item) => item.dataset.msgId === String(m.id));
    return row?.querySelectorAll(".thread-feedback-btn") || [];
  }
  async function sendFeedback(value, button) {
    ensureScope();
    const token = capture();
    if (!current(token, ticketId)) return;
    const key = lockKey(token, m.id, "feedback");
    if (mutationLocks.has(key)) return;
    mutationLocks.add(key);
    upBtn.disabled = downBtn.disabled = true;
    try {
      const res = await apiFetch(`${API_BASE}/api/inbox/messages/${encodeURIComponent(m.id)}/feedback`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value }),
      });
      if (!current(token, ticketId)) return;
      if (res.status === 403) { invalidate(); return; }
      if (!res.ok) throw new Error("Feedback non registrato");
      button.classList.add("selected");
      liveButtons()[value === "up" ? 0 : 1]?.classList.add("selected");
      toast("Feedback registrato", "success");
    } catch {
      if (current(token, ticketId)) toast("Feedback non registrato", "error");
    } finally {
      mutationLocks.delete(key);
      if (current(token, ticketId)) {
        upBtn.disabled = downBtn.disabled = false;
        liveButtons().forEach((control) => { control.disabled = false; });
      }
    }
  }
  upBtn.disabled = downBtn.disabled = mutationLocks.has(lockKey(capture(), m.id, "feedback"));
  upBtn.addEventListener("click", (e) => { e.stopPropagation(); void sendFeedback("up", upBtn); });
  downBtn.addEventListener("click", (e) => { e.stopPropagation(); void sendFeedback("down", downBtn); });
  container.appendChild(upBtn);
  container.appendChild(downBtn);
  return container;
}

/* ---------- Dettaglio Conversazione & Thread ---------- */

function _renderMsgRow(m, ticket) {
  const row = document.createElement("div");
  const isInbound = m.direction === "inbound";
  row.className = `inbox-msg-row ${isInbound ? "inbound" : "outbound"}`;
  row.dataset.msgId = m.id;

  // Avatar
  const avatar = document.createElement("div");
  avatar.className = `inbox-msg-avatar ${!isInbound ? "melpis-avatar" : ""}`;
  if (isInbound) {
    const colors = _getAvatarColors(ticket.phone_number || ticket.id);
    avatar.style.backgroundColor = colors.bg;
    avatar.style.color = colors.text;
    avatar.textContent = _getAvatarInitial(ticket.phone_number || "Cliente");
  } else {
    avatar.innerHTML = '<brand-logo variant="symbol" theme="auto" size="md"></brand-logo>';
  }

  // Bubble
  const bubble = document.createElement("div");
  bubble.className = "inbox-msg-bubble";

  // Autore per outbound
  if (!isInbound) {
    const author = document.createElement("span");
    author.className = "inbox-msg-author";
    author.textContent = m.handling_type === "ai_handled" ? "Melpis AI" : (ticket.assigned_nome || "Operatore");
    bubble.appendChild(author);
  }

  // Testo formattato con link cliccabili
  const textEl = document.createElement("div");
  textEl.className = "inbox-msg-text";
  textEl.appendChild(formatMessageContent(m.content_text || `(messaggio ${m.message_type})`));
  bubble.appendChild(textEl);

  // Feedback staff per messaggi AI
  if (!isInbound && m.handling_type === "ai_handled") {
    bubble.appendChild(creaControlliFeedback(m));
  }

  // Meta / Timestamp
  const meta = document.createElement("div");
  meta.className = "inbox-msg-meta";
  const quando = formatInboxDate(m.created_at);
  const status = labelStatoMessaggioInbox(m.status);
  meta.dataset.createdAt = m.created_at || "";
  meta.dataset.status = m.status || "";
  meta.dataset.direction = m.direction || "";
  meta.textContent = isInbound ? quando : `${quando} · ${status}`;
  bubble.appendChild(meta);

  row.appendChild(avatar);
  row.appendChild(bubble);
  return row;
}

function caricaDettaglioTicket(ticketId, silent = false, options = {}) {
  ensureScope();
  if (!active || String(inboxState.selectedTicketId) !== String(ticketId)) return Promise.resolve();
  const refreshMessages = options.refreshMessages !== false;
  if (refreshMessages && silent && detailFlight?.ticketId === String(ticketId) && detailFlight.epoch === epoch) {
    return detailFlight.promise;
  }
  const token = capture();
  const request = refreshMessages ? ++detailSequence : detailSequence;
  const promise = runDettaglioTicket(ticketId, silent, options, token, request);
  if (refreshMessages) {
    const flight = { ticketId: String(ticketId), epoch, promise };
    detailFlight = flight;
    void promise.then(() => { if (detailFlight === flight) detailFlight = null; }, () => { if (detailFlight === flight) detailFlight = null; });
  }
  return promise;
}

async function runDettaglioTicket(ticketId, silent, { refreshMessages = true, forceRender = false }, token, request) {
  const isCurrent = () => request === detailSequence && current(token, ticketId);
  const emptyEl = document.getElementById("inbox-detail-empty");
  const contentEl = document.getElementById("inbox-detail-content");
  if (!contentEl) return;

  const ticket = inboxState.tickets.find((t) => String(t.id) === String(ticketId));
  if (!ticket) {
    mostraPlaceholderDettaglio();
    return;
  }

  if (emptyEl) emptyEl.hidden = true;
  contentEl.hidden = false;

  // 1. Header Dettaglio
  const nameEl = document.getElementById("inbox-detail-name");
  const phoneEl = document.getElementById("inbox-detail-phone");
  const avatarEl = document.getElementById("inbox-detail-avatar");
  const channelBadge = document.getElementById("inbox-detail-channel");
  const actionsEl = document.getElementById("inbox-detail-actions");
  const contextNumber = document.getElementById("inbox-context-number");
  const contextChannel = document.getElementById("inbox-context-channel");
  const contextStatus = document.getElementById("inbox-context-status");
  const contextPriority = document.getElementById("inbox-context-priority");
  const contextAssigned = document.getElementById("inbox-context-assigned");
  const contextLastMessage = document.getElementById("inbox-context-last-message");
  const detailStatus = document.getElementById("inbox-detail-status");

  if (nameEl) nameEl.textContent = ticket.phone_number || "Cliente";
  if (phoneEl) phoneEl.textContent = ticket.phone_number || "";

  if (avatarEl) {
    const colors = _getAvatarColors(ticket.phone_number || ticket.id);
    avatarEl.style.backgroundColor = colors.bg;
    avatarEl.style.color = colors.text;
    avatarEl.textContent = _getAvatarInitial(ticket.phone_number || "Cliente");
  }

  const isIg = (ticket.canale || "").toLowerCase() === "instagram";
  const channelName = isIg ? "Instagram" : "WhatsApp";
  const statusName = labelStatoTicketInbox(ticket.ticket_status);
  const priorityName = t(ticket.priorita === "alta" ? "inbox:filters.priority_high" : "inbox:filters.priority_normal");
  const assignedMember = inboxState.team.find((member) => String(member.user_id) === String(ticket.assigned_to));
  const assignedName = ticket.assigned_nome || assignedMember?.nome || assignedMember?.email || t(ticket.assigned_to ? "inbox:filters.assigned_generic" : "inbox:filters.unassigned");
  if (contextNumber) contextNumber.textContent = ticket.phone_number || "—";
  if (contextChannel) contextChannel.textContent = channelName;
  if (contextStatus) contextStatus.textContent = statusName;
  if (contextPriority) contextPriority.textContent = priorityName;
  if (contextAssigned) contextAssigned.textContent = assignedName;
  if (contextLastMessage) contextLastMessage.textContent = formatInboxDate(ticket.last_message_at || ticket.created_at);
  if (detailStatus) {
    detailStatus.textContent = statusName;
    detailStatus.dataset.status = (ticket.ticket_status || "").toLowerCase();
  }
  if (channelBadge) {
    channelBadge.className = `inbox-channel-badge ${isIg ? "ig" : "wa"}`;
    channelBadge.innerHTML = isIg
      ? '<svg viewBox="0 0 24 24" fill="none" width="14" height="14"><rect x="3" y="3" width="18" height="18" rx="5" stroke="currentColor" stroke-width="2"/><circle cx="12" cy="12" r="4" stroke="currentColor" stroke-width="2"/><circle cx="17.5" cy="6.5" r="1" fill="currentColor"/></svg><span>Instagram</span>'
      : '<svg viewBox="0 0 24 24" fill="none" width="14" height="14"><path d="M12 21a9 9 0 1 0-4.5-1.2L3 21l1.2-4.5A9 9 0 0 0 12 21Z" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg><span>WhatsApp</span>';
  }

  // Pulsanti Azione (Claim / Resolve / Release / Assign)
  if (actionsEl && (forceRender || !silent || actionsEl.dataset.ticketId !== String(ticket.id) || actionsEl.dataset.ticketStatus !== ticket.ticket_status)) {
    actionsEl.dataset.ticketId = ticket.id;
    actionsEl.dataset.ticketStatus = ticket.ticket_status;
    actionsEl.innerHTML = "";

    if (ticket.ticket_status !== "RESOLVED" && inboxState.team.length > 0) {
      const assignSel = document.createElement("select");
      assignSel.className = "inbox-action-select";
      const placeholderOpt = document.createElement("option");
      placeholderOpt.value = "";
      placeholderOpt.textContent = t("inbox:filters.assign_placeholder");
      placeholderOpt.disabled = true;
      placeholderOpt.selected = !ticket.assigned_to;
      assignSel.appendChild(placeholderOpt);
      inboxState.team.forEach((m) => {
        const opt = document.createElement("option");
        opt.value = m.user_id;
        opt.textContent = `${m.nome || m.email}${m.user_id === ticket.assigned_to ? t("inbox:filters.assigned_suffix") : ""}`;
        if (m.user_id === ticket.assigned_to) opt.selected = true;
        assignSel.appendChild(opt);
      });
      assignSel.disabled = mutationLocks.has(lockKey(capture(), ticket.id, "assign"));
      assignSel.addEventListener("change", () => {
        if (!assignSel.value) return;
        const assignedTo = assignSel.value;
        void mutate(ticket, "assign", () => apiFetch(`${API_BASE}/api/inbox/assign/${encodeURIComponent(ticket.id)}`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ assigned_to: assignedTo, expected_version: ticket.version }),
        }), "Ticket assegnato con successo", "Impossibile assegnare.", assignSel);
      });
      actionsEl.appendChild(assignSel);
    }

    if (ticket.ticket_status === "PENDING_STAFF" || ticket.ticket_status === "AI_ACTIVE") {
      const claimBtn = document.createElement("button");
      claimBtn.type = "button";
      claimBtn.className = "inbox-action-btn primary";
      aggiornaStatoPulsanteClaimInbox(claimBtn, ticket.id);
      claimBtn.title = t("inbox:chat.claim_btn");
      claimBtn.addEventListener("click", () => { void claimTicket(ticket, claimBtn); });
      actionsEl.appendChild(claimBtn);
    }

    if (ticket.ticket_status === "CLAIMED") {
      const releaseBtn = document.createElement("button");
      releaseBtn.type = "button";
      releaseBtn.className = "inbox-action-btn";
      releaseBtn.textContent = t("inbox:chat.release_btn");
      releaseBtn.title = t("inbox:chat.release_btn");
      releaseBtn.disabled = mutationLocks.has(lockKey(capture(), ticket.id, "release"));
      releaseBtn.addEventListener("click", () => {
        void mutate(ticket, "release", () => apiFetch(`${API_BASE}/api/inbox/release/${encodeURIComponent(ticket.id)}`, { method: "POST" }),
          "Conversazione rilasciata", "Impossibile rilasciare il ticket.", releaseBtn);
      });
      actionsEl.appendChild(releaseBtn);

      const resolveBtn = document.createElement("button");
      resolveBtn.type = "button";
      resolveBtn.className = "inbox-action-btn primary";
      resolveBtn.textContent = t("inbox:chat.close_ticket");
      resolveBtn.title = t("inbox:chat.close_ticket");
      resolveBtn.disabled = mutationLocks.has(lockKey(capture(), ticket.id, "resolve"));
      resolveBtn.addEventListener("click", () => {
        void mutate(ticket, "resolve", () => apiFetch(`${API_BASE}/api/inbox/resolve/${encodeURIComponent(ticket.id)}`, { method: "POST" }),
          "Ticket segnato come risolto", "Impossibile risolvere il ticket.", resolveBtn);
      });
      actionsEl.appendChild(resolveBtn);
    }
  }

  // 2. Banner di Stato Discreto (AI / Richiesta Staff)
  const statusStrip = document.getElementById("inbox-status-strip");
  const aiBadge = document.getElementById("inbox-ai-badge");
  const aiBadgeText = document.getElementById("inbox-ai-badge-text");

  if (statusStrip && aiBadge && aiBadgeText) {
    statusStrip.className = "inbox-status-strip";
    if (ticket.ticket_status === "AI_ACTIVE") {
      statusStrip.hidden = false;
      aiBadge.innerHTML = `<svg viewBox="0 0 24 24" fill="none" width="16" height="16"><rect x="3" y="6" width="18" height="14" rx="3" stroke="currentColor" stroke-width="1.8"/><circle cx="8.5" cy="12" r="1.5" fill="currentColor"/><circle cx="15.5" cy="12" r="1.5" fill="currentColor"/><path d="M12 2v4" stroke="currentColor" stroke-width="1.8"/></svg><span>${t("inbox:filters.status_strip_ai")}</span>`;
    } else if (ticket.ticket_status === "PENDING_STAFF") {
      statusStrip.hidden = false;
      statusStrip.classList.add("pending");
      aiBadge.innerHTML = `<span class="inbox-dot-red"></span><span>${t("inbox:filters.status_strip_pending")}</span>`;
    } else if (ticket.ticket_status === "CLAIMED") {
      statusStrip.hidden = false;
      const assignedLabel = ticket.assigned_nome
        ? t("inbox:chat.claimed_by_other", { name: ticket.assigned_nome })
        : t("inbox:chat.claimed_by_you");
      aiBadge.innerHTML = '<svg viewBox="0 0 24 24" fill="none" width="16" height="16"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" stroke="currentColor" stroke-width="1.8"/><circle cx="12" cy="7" r="4" stroke="currentColor" stroke-width="1.8"/></svg>';
      const assignedLabelEl = document.createElement("span");
      assignedLabelEl.textContent = assignedLabel;
      aiBadge.appendChild(assignedLabelEl);
    } else {
      statusStrip.hidden = false;
      aiBadge.innerHTML = `<svg viewBox="0 0 24 24" fill="none" width="16" height="16"><path d="M5 12l5 5L20 7" stroke="#10b981" stroke-width="2.2" stroke-linecap="round"/></svg><span>${t("inbox:filters.status_strip_resolved")}</span>`;
    }
  }

  // 3. Thread Messaggi (Zero-Flicker)
  const threadContainer = document.getElementById("inbox-thread-messages");
  if (threadContainer && refreshMessages) {
    const isNewTicket = threadContainer.dataset.ticketId !== ticket.id;
    threadContainer.dataset.ticketId = ticket.id;

    if (!silent && isNewTicket) {
      threadContainer.innerHTML = _skeletonList(3);
    }

    try {
      const res = await apiFetch(`${API_BASE}/api/inbox/tickets/${encodeURIComponent(ticket.id)}/messages?limit=200`);
      if (!isCurrent()) return;
      if (res.status === 403) { invalidate(); return; }
      if (!res.ok) throw new Error("Errore recupero messaggi");
      const msgData = await res.json();
      if (!isCurrent()) return;
      const seenMessageIds = new Set();
      const messages = (msgData.messages || []).filter((message) => {
        const id = String(message.id);
        if (seenMessageIds.has(id)) return false;
        seenMessageIds.add(id);
        return true;
      });

      const renderedMsgRows = threadContainer.querySelectorAll(".inbox-msg-row[data-msg-id]");
      const renderedIds = Array.from(renderedMsgRows).map((r) => r.dataset.msgId);
      const newIds = messages.map((m) => String(m.id));

      const isSameList =
        !isNewTicket &&
        renderedIds.length === newIds.length &&
        renderedIds.every((id, idx) => id === newIds[idx]);

      if (isSameList && renderedIds.length > 0) {
        // Aggiorna solo stato metadati in-place
        messages.forEach((m) => {
          const row = Array.from(renderedMsgRows).find((item) => item.dataset.msgId === String(m.id));
          if (row) {
            const meta = row.querySelector(".inbox-msg-meta");
            if (meta) {
              const isInbound = m.direction === "inbound";
              const quando = formatInboxDate(m.created_at);
              const status = labelStatoMessaggioInbox(m.status);
              const expectedText = isInbound ? quando : `${quando} · ${status}`;
              if (meta.textContent !== expectedText) {
                meta.textContent = expectedText;
              }
            }
          }
        });
      } else {
        const isNearBottom =
          threadContainer.scrollHeight - threadContainer.scrollTop - threadContainer.clientHeight < 150;

        threadContainer.innerHTML = "";
        if (!messages.length) {
          threadContainer.innerHTML = '<div class="inbox-empty-thread"><div class="inbox-empty-thread-icon"><svg viewBox="0 0 24 24" fill="none" width="28" height="28" stroke="currentColor" stroke-width="1.8"><rect x="3" y="5" width="18" height="14" rx="3"/><path d="m3 7 9 6 9-6"/></svg></div><p class="inbox-empty-text"></p><p class="inbox-empty-subtext"></p></div>';
          const emptyTitle = threadContainer.querySelector(".inbox-empty-text");
          const emptySubtext = threadContainer.querySelector(".inbox-empty-subtext");
          if (emptyTitle) emptyTitle.textContent = t("inbox:filters.empty_title");
          if (emptySubtext) emptySubtext.textContent = t("inbox:filters.empty_thread_text");
        } else {
          messages.forEach((m) => {
            const row = _renderMsgRow(m, ticket);
            threadContainer.appendChild(row);
          });
        }

        const scrollToBottom = () => {
          if (threadContainer && isCurrent()) {
            threadContainer.scrollTop = threadContainer.scrollHeight;
          }
        };

        if (isNearBottom || isNewTicket || !silent) {
          scrollToBottom();
          root.requestAnimationFrame(scrollToBottom);
          root.setTimeout(scrollToBottom, 40);
          root.setTimeout(scrollToBottom, 120);
        }
      }
    } catch (err) {
      if (!isCurrent()) return;
      console.error("Errore caricamento thread");
      if (!silent) {
        threadContainer.innerHTML = '<p class="inbox-empty error" style="text-align:center; padding:20px;">Impossibile caricare lo storico dei messaggi.</p>';
      }
    }
  }

  if (!isCurrent()) return;
  if (threadContainer && !refreshMessages) {
    const emptyTitle = threadContainer.querySelector(".inbox-empty-text");
    const emptySubtext = threadContainer.querySelector(".inbox-empty-subtext");
    if (emptyTitle) emptyTitle.textContent = t("inbox:filters.empty_title");
    if (emptySubtext) emptySubtext.textContent = t("inbox:filters.empty_thread_text");
    threadContainer.querySelectorAll(".inbox-msg-meta[data-created-at]").forEach((meta) => {
      const timestamp = formatInboxDate(meta.dataset.createdAt);
      meta.textContent = meta.dataset.direction === "inbound"
        ? timestamp
        : `${timestamp} · ${labelStatoMessaggioInbox(meta.dataset.status)}`;
    });
  }

  // 4. Input Box Risposta
  const replyForm = document.getElementById("inbox-reply-form");
  const disabledBanner = document.getElementById("inbox-reply-disabled-banner");
  const claimInlineBtn = document.getElementById("inbox-claim-inline-btn");
  const msgInput = document.getElementById("inbox-message-input");
  const sendBtn = document.getElementById("inbox-send-btn");

  if (ticket.ticket_status === "CLAIMED") {
    if (replyForm) replyForm.hidden = false;
    if (disabledBanner) disabledBanner.hidden = true;
    if (msgInput) {
      msgInput.disabled = mutationLocks.has(lockKey(token, ticket.id, "reply"));
      msgInput.placeholder = t("inbox:chat.type_message");
      if (!silent) msgInput.focus();
    }
    if (sendBtn) sendBtn.disabled = mutationLocks.has(lockKey(token, ticket.id, "reply"));
  } else {
    if (replyForm) replyForm.hidden = true;
    if (disabledBanner) disabledBanner.hidden = false;
    const disabledText = document.getElementById("inbox-reply-disabled-text");
    if (disabledText) {
      disabledText.textContent = t(ticket.ticket_status === "RESOLVED"
        ? "inbox:filters.ticket_resolved"
        : "inbox:filters.manual_reply_claim");
    }
    if (claimInlineBtn) {
      aggiornaStatoPulsanteClaimInbox(claimInlineBtn, ticket.id);
      claimInlineBtn.style.display = ticket.ticket_status === "RESOLVED" ? "none" : "inline-block";
      claimInlineBtn.onclick = () => { void claimTicket(ticket, claimInlineBtn); };
    }
  }
}

/* ---------- Invio Risposta Manuale ---------- */

async function inviaRispostaInbox() {
  ensureScope();
  const ticket = inboxState.tickets.find((item) => String(item.id) === String(inboxState.selectedTicketId));
  if (!active || !ticket || ticket.ticket_status !== "CLAIMED") return;
  const token = capture();
  if (!current(token, ticket.id)) return;
  const msgInput = document.getElementById("inbox-message-input");
  const sendBtn = document.getElementById("inbox-send-btn");
  if (!msgInput) return;
  const content = msgInput.value.trim();
  if (!content) return;
  const key = lockKey(token, ticket.id, "reply");
  if (mutationLocks.has(key)) return;
  mutationLocks.add(key);
  drafts.set(String(ticket.id), msgInput.value);
  const attemptKey = JSON.stringify([scopeKey(token.context), String(ticket.id), content]);
  let idempotencyKey = replyAttempts.get(attemptKey);
  if (!idempotencyKey) {
    idempotencyKey = root.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
    replyAttempts.set(attemptKey, idempotencyKey);
  }
  if (sendBtn) sendBtn.disabled = true;
  msgInput.disabled = true;
  try {
    const res = await apiFetch(`${API_BASE}/api/inbox/reply/${encodeURIComponent(ticket.id)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content, message_type: "text", idempotency_key: idempotencyKey }),
    });
    if (!current(token, ticket.id)) return;
    if (res.status === 403) { invalidate(); return; }
    if (!res.ok) throw new Error("Invio risposta fallito.");
    const data = await res.json();
    if (!current(token, ticket.id)) return;
    if (!data?.message_id || !["sent", "delivered", "read"].includes(data.status)) {
      toast(t("inbox:chat.message_status.sending_ambiguous"), "info");
      return;
    }
    replyAttempts.delete(attemptKey);
    if (msgInput.value.trim() === content) {
      msgInput.value = "";
      drafts.delete(String(ticket.id));
    }
    toast("Messaggio inviato", "success");
    await caricaDettaglioTicket(ticket.id);
    if (!current(token, ticket.id)) return;
    await caricaInbox(false, true);
  } catch {
    if (current(token, ticket.id)) {
      toast(t("inbox:chat.message_status.sending_ambiguous"), "error");
    }
  } finally {
    mutationLocks.delete(key);
    if (current(token, ticket.id)) {
      if (sendBtn) sendBtn.disabled = false;
      msgInput.disabled = false;
      msgInput.focus();
    }
  }
}

/* ---------- Setup Event Listeners Inbox ---------- */

function inizializzaEventiInbox() {
  // 1. Filtri Canale
  document.querySelectorAll("[data-inbox-channel]").forEach((chip) => {
    chip.addEventListener("click", () => {
      inboxState.channelFilter = chip.dataset.inboxChannel;
      document.querySelectorAll("[data-inbox-channel]").forEach((c) => c.classList.toggle("active", c === chip));
      renderInboxConversazioni();
    });
  });

  // 2. Filtri Stato & Gestione
  document.querySelectorAll("[data-inbox-status]").forEach((chip) => {
    chip.addEventListener("click", () => {
      inboxState.statusFilter = chip.dataset.inboxStatus;
      document.querySelectorAll("[data-inbox-status]").forEach((c) => c.classList.toggle("active", c === chip));
      renderInboxConversazioni();
    });
  });

  // 3. Ricerca Conversazioni
  const searchInput = document.getElementById("inbox-search-input");
  if (searchInput) {
    searchInput.addEventListener("input", () => {
      inboxState.search = searchInput.value;
      renderInboxConversazioni();
    });
  }

  // 4. Form Invio Risposta
  const replyForm = document.getElementById("inbox-reply-form");
  if (replyForm) {
    replyForm.addEventListener("submit", (e) => {
      e.preventDefault();
      inviaRispostaInbox();
    });
  }

  const sendBtn = document.getElementById("inbox-send-btn");
  if (sendBtn) {
    sendBtn.addEventListener("click", (e) => {
      e.preventDefault();
      inviaRispostaInbox();
    });
  }

  const msgInput = document.getElementById("inbox-message-input");
  if (msgInput) {
    msgInput.addEventListener("input", () => {
      if (active && inboxState.selectedTicketId !== null) drafts.set(String(inboxState.selectedTicketId), msgInput.value);
    });
    msgInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        inviaRispostaInbox();
      }
    });
  }

  // 5. Mobile Controls (Torna alla lista dalla chat)
  const backBtn = document.getElementById("inbox-back-to-list");
  if (backBtn) {
    backBtn.addEventListener("click", () => {
      document.querySelector(".inbox-view")?.classList.remove("show-detail");
    });
  }
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) fermaInboxPolling();
    else if (active && getContext()?.view === "inbox") {
      ensureScope();
      if (active) { avviaInboxPolling(); void caricaInbox(true); }
    }
  });
}



      return Object.freeze({ onEnter, onExit, invalidate, load, refreshLanguage });
    },
  });
});
