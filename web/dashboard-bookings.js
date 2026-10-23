(function attachDashboardBookings(root, factory) {
  const api = factory(root);
  if (root) root.MelpisDashboardBookings = api;
})(typeof window !== "undefined" ? window : globalThis, function createDashboardBookings(root) {
  "use strict";
  const SAFE_CALENDAR_SYNC_ERROR_CODES = new Set(["sync_failed", "retry_pending"]);
  return Object.freeze({
    create(deps) {
      const { API_BASE, apiFetch, _escapeHtml, _toDateKey, _tDash,
        localeCorrente, toast, confermaDestructiva, getContext, getSession,
        getVertical, statoNormalizzatoPrenotazione, exportCsv, updatePending } = deps;
      const document = root.document;
      let active = false;
      let epoch = 0;
      let entryScope = null;
      let pendingConfirmation = null;
      const mutationLocks = new Set();
      function capture() { return { context: getContext(), epoch, role: getSession()?.ruolo }; }
      function current(token) {
        const now = getContext();
        return Boolean(token) && active && token.epoch === epoch && token.role === getSession()?.ruolo && now?.view === "prenotazioni"
          && now?.userId && now?.sessionOrganizationId
          && JSON.stringify(now) === JSON.stringify(token.context);
      }
      function scopeKey() {
        const c = getContext();
        return JSON.stringify([c?.userId, c?.sessionOrganizationId, c?.selectedOrganizationId]);
      }
      function canManage() { return ["owner", "manager"].includes(getSession()?.ruolo); }
      function ensureScope() {
        if (active && entryScope !== null && entryScope !== scopeKey()) {
          invalidate();
          entryScope = scopeKey();
          active = getContext()?.view === "prenotazioni";
          sincronizzaPollingPrenotazioni();
        }
      }
      function beginMutation(key) {
        ensureScope();
        const token = capture();
        if (!current(token) || !canManage() || mutationLocks.has(key)) return null;
        mutationLocks.add(key);
        return token;
      }
      function closeDialogs() {
        const modal = document.getElementById("confirm-modal");
        if (pendingConfirmation && modal && !modal.hidden
          && document.getElementById("confirm-title")?.textContent === pendingConfirmation.titolo
          && document.getElementById("confirm-desc")?.textContent === pendingConfirmation.descrizione) {
          document.getElementById("confirm-cancel-btn")?.click();
          if (modal.hidden) {
            document.getElementById("confirm-title").textContent = "";
            document.getElementById("confirm-desc").textContent = "";
          }
        }
        for (const dialog of document.querySelectorAll(".booking-modal")) {
          if (window.MelpisDialogFocus) window.MelpisDialogFocus.close(dialog, { restoreFocus: false });
          else dialog.hidden = true;
        }
        document.body.classList.remove("booking-modal-open");
      }
      let bookingListRequest = 0;
      let bookingAvailabilityRequest = 0;
      let listFlight = null;
      let availabilityFlight = null;
      let loadFlight = null;
      let calendarInitializing = false;
      const window = root;

      const bookingCalendarEl = document.getElementById("booking-calendar");
      const bookingCount = document.getElementById("booking-count");
      const availabilityList = document.getElementById("availability-list");
      const availabilityDate = document.getElementById("availability-date");
      const bookingForm = document.getElementById("booking-form");
      const bookingStatusText = document.getElementById("booking-status-text");
      const bookingSettingsGrid = document.getElementById("booking-settings-grid");
      const capacitySave = document.getElementById("capacity-save");
      const capacityStatus = document.getElementById("capacity-status");
      let bookingCalendar = null;
      let bookingOpenHours = {};
      let bookingAvailability = new Map();
      let bookingRecords = [];
      let bookingPendingOnly = false;
      let bookingEditingId = null;
      let bookingFormTransition = 0;
      let bookingDetailTransition = 0;
      let bookingPollTimer = null;
      let bookingSnapshot = new Map();
      const bookingModal = document.getElementById("booking-modal");
      const bookingSummary = document.getElementById("booking-summary");
      const bookingPendingValue = document.getElementById("booking-pending-value");
      const bookingDetail = {
        title: document.getElementById("booking-modal-title"),
        date: document.getElementById("booking-detail-date"),
        time: document.getElementById("booking-detail-time"),
        seats: document.getElementById("booking-detail-seats"),
        status: document.getElementById("booking-detail-status"),
        phone: document.getElementById("booking-detail-phone"),
        origin: document.getElementById("booking-detail-origin"),
        note: document.getElementById("booking-detail-note"),
      };

      function oggiIso() {
        return _toDateKey(new Date());
      }

      function colorePrenotazione(stato) {
        const normalized = (stato || "").toLowerCase();
        if (normalized.includes("intervento")) return "#C63F52";
        if (normalized.includes("attesa")) return "#C68A2E";
        return "#1F9D74";
      }

      const STATI_FINALI_PRENOTAZIONE = ["cancellata", "cancellato", "rifiutata", "no_show", "completata"];
      let prenotazioneCorrente = null;

      function aggiornaAzioniPrenotazione(p) {
        const wrap = document.getElementById("booking-detail-actions");
        if (!wrap || !p) {
          if (wrap) wrap.hidden = true;
          return;
        }
        const staff = Boolean(getSession() && getSession().ruolo === "staff");
        const confermaBtn = document.getElementById("booking-confirm-btn");
        const rifiutaBtn = document.getElementById("booking-reject-btn");
        const annullaBtn = document.getElementById("booking-cancel-btn");
        const noShowBtn = document.getElementById("booking-no-show-btn");
        const completedBtn = document.getElementById("booking-completed-btn");
        const editBtn = document.getElementById("booking-edit-btn");
        if (!confermaBtn || !rifiutaBtn || !annullaBtn || !noShowBtn || !completedBtn || !editBtn) return;
        [confermaBtn, rifiutaBtn, annullaBtn, noShowBtn, completedBtn, editBtn].forEach((button) => { button.disabled = false; });
        const stato = statoNormalizzatoPrenotazione(p);
        const finale = STATI_FINALI_PRENOTAZIONE.includes(stato);
        confermaBtn.hidden = staff || finale || stato === "confermata";
        rifiutaBtn.hidden = staff || finale || stato === "rifiutata";
        annullaBtn.hidden = staff || finale;
        noShowBtn.hidden = staff || finale || stato !== "confermata";
        completedBtn.hidden = staff || finale || stato !== "confermata";
        editBtn.hidden = staff || finale;
        wrap.hidden = [confermaBtn, rifiutaBtn, annullaBtn, noShowBtn, completedBtn, editBtn].every((button) => button.hidden);
      }

      async function eseguiAzionePrenotazione(azione, { chiediConferma = false, titolo = "", descrizione = "", label = "Conferma" } = {}) {
        const p = prenotazioneCorrente;
        if (!p?.id) return;
        const key = `action:${p.id}`;
        const token = beginMutation(key);
        if (!token) return;
        const transition = getContext()?.transition;
        const formTransition = bookingFormTransition;
        let detailTransition = ++bookingDetailTransition;
        const selectedDate = bookingCalendar ? _toDateKey(bookingCalendar.getDate()) : oggiIso();
        const isCurrent = () => current(token) && transition === getContext()?.transition && formTransition === bookingFormTransition && detailTransition === bookingDetailTransition && prenotazioneCorrente?.id === p.id && selectedDate === (bookingCalendar ? _toDateKey(bookingCalendar.getDate()) : oggiIso());
        const bottoni = ["booking-confirm-btn", "booking-reject-btn", "booking-cancel-btn", "booking-no-show-btn", "booking-completed-btn", "booking-edit-btn"]
          .map((id) => document.getElementById(id))
          .filter(Boolean);
        try {
          if (chiediConferma) {
            if (pendingConfirmation) return;
            const owned = { titolo, descrizione };
            pendingConfirmation = owned;
            let ok;
            try { ok = await confermaDestructiva({ titolo, descrizione, label }); }
            finally { if (pendingConfirmation === owned) pendingConfirmation = null; }
            if (!ok || !isCurrent() || !canManage()) return;
          }
          if (!isCurrent() || !canManage()) return;
          bottoni.forEach((b) => { b.disabled = true; });
          const res = await apiFetch(`${API_BASE}/api/bookings/${encodeURIComponent(p.id)}/${azione}`, { method: "POST" });
          if (!isCurrent()) return;
          if (!res.ok) {
            const errData = await res.json().catch(() => ({}));
            if (!isCurrent()) return;
            throw new Error(errData.detail || "Operazione non riuscita.");
          }
          const updated = await res.json();
          if (!isCurrent()) return;
          prenotazioneCorrente = updated;
          chiudiDettaglioPrenotazione();
          detailTransition = bookingDetailTransition;
          toast(
            azione === "confirm" ? "Prenotazione confermata."
              : azione === "reject" ? "Prenotazione rifiutata."
                : azione === "mark-no-show" ? "Prenotazione segnata come no-show."
                  : azione === "mark-completed" ? "Prenotazione completata."
                    : "Prenotazione annullata.",
            azione === "confirm" ? "success" : "info",
          );
          await Promise.all([
            aggiornaPrenotazioni(),
            p.data ? aggiornaSemaforo(p.data) : Promise.resolve(),
          ]);
        } catch (err) {
          if (!isCurrent()) return;
          toast(err.message || "Errore di connessione.", "error");
        } finally {
          mutationLocks.delete(key);
          if (isCurrent()) bottoni.forEach((b) => { b.disabled = false; });
        }
      }

      document.getElementById("booking-confirm-btn")?.addEventListener("click", () => {
        eseguiAzionePrenotazione("confirm");
      });

      document.getElementById("booking-reject-btn")?.addEventListener("click", () => {
        eseguiAzionePrenotazione("reject", {
          chiediConferma: true,
          titolo: "Rifiutare la prenotazione?",
          descrizione: `La richiesta di ${prenotazioneCorrente?.nome_cliente || "questo cliente"} verrà contrassegnata come rifiutata e il cliente non avrà il tavolo riservato.`,
          label: "Rifiuta",
        });
      });

      document.getElementById("booking-cancel-btn")?.addEventListener("click", () => {
        eseguiAzionePrenotazione("cancel", {
          chiediConferma: true,
          titolo: "Annullare la prenotazione?",
          descrizione: `La prenotazione di ${prenotazioneCorrente?.nome_cliente || "questo cliente"} verrà annullata e i posti torneranno disponibili.`,
          label: "Annulla prenotazione",
        });
      });

      document.getElementById("booking-no-show-btn")?.addEventListener("click", () => {
        eseguiAzionePrenotazione("mark-no-show", {
          chiediConferma: true,
          titolo: "Segnare come no-show?",
          descrizione: "La prenotazione verrà chiusa come no-show.",
          label: "Segna no-show",
        });
      });

      document.getElementById("booking-completed-btn")?.addEventListener("click", () => {
        eseguiAzionePrenotazione("mark-completed", {
          chiediConferma: true,
          titolo: "Segnare come completata?",
          descrizione: "La prenotazione verrà registrata come completata.",
          label: "Completa",
        });
      });

      function formattaUnitaVerticale(num, singolare = false) {
        const v = getVertical();
        const n = Number(num) || 0;
        if (v === "parrucchiere" || v === "centro_estetico") {
          return singolare || n === 1 ? `${n} persona` : `${n} persone`;
        }
        if (v === "studio_medico_dentista") {
          return singolare || n === 1 ? `${n} paziente` : `${n} pazienti`;
        }
        if (v === "hotel_bnb") {
          return singolare || n === 1 ? `${n} ospite` : `${n} ospiti`;
        }
        return singolare || n === 1 ? `${n} coperto` : `${n} coperti`;
      }

      function apriDettaglioPrenotazione(prenotazione) {
        if (entryScope !== scopeKey()) { ensureScope(); return; }
        if (!current(capture()) || !bookingModal || !prenotazione) return;
        bookingDetailTransition++;
        const valore = (dato, fallback = "Non indicato") => dato || fallback;
        const data = prenotazione.data
          ? new Date(`${prenotazione.data}T12:00:00`).toLocaleDateString(localeCorrente(), {
            weekday: "long", day: "2-digit", month: "long", year: "numeric",
          })
          : "Non indicata";
        prenotazioneCorrente = prenotazione;
        bookingDetail.title.textContent = valore(prenotazione.nome_cliente, "Cliente");
        bookingDetail.date.textContent = data;
        bookingDetail.time.textContent = valore(prenotazione.ora);
        bookingDetail.seats.textContent = prenotazione.coperti ? formattaUnitaVerticale(prenotazione.coperti) : "Non indicati";
        bookingDetail.status.textContent = valore(prenotazione.stato);
        bookingDetail.phone.textContent = valore(prenotazione.telefono);
        bookingDetail.origin.textContent = valore(prenotazione.origine);
        bookingDetail.note.textContent = valore(prenotazione.note, "Nessuna nota");
        aggiornaAzioniPrenotazione(prenotazione);
        if (window.MelpisDialogFocus) window.MelpisDialogFocus.open(bookingModal);
        else bookingModal.hidden = false;
        document.body.classList.add("booking-modal-open");
      }

      function chiudiDettaglioPrenotazione() {
        if (!bookingModal) return;
        bookingDetailTransition++;
        if (window.MelpisDialogFocus) window.MelpisDialogFocus.close(bookingModal);
        else bookingModal.hidden = true;
        document.body.classList.remove("booking-modal-open");
      }

      function apriBookingModal(id, options = {}) {
        const modal = document.getElementById(id);
        if (!modal) return;
        if (id === "booking-create-modal") bookingFormTransition++;
        if (window.MelpisDialogFocus) window.MelpisDialogFocus.open(modal, options);
        else modal.hidden = false;
        document.body.classList.add("booking-modal-open");
      }

      function chiudiBookingModal(id, options = {}) {
        const modal = document.getElementById(id);
        if (!modal) return;
        if (id === "booking-create-modal") bookingFormTransition++;
        if (window.MelpisDialogFocus) window.MelpisDialogFocus.close(modal, options);
        else modal.hidden = true;
        if (![...document.querySelectorAll(".booking-modal")].some((element) => !element.hidden)) {
          document.body.classList.remove("booking-modal-open");
        }
      }

      function apriFormPrenotazione(prenotazione = null) {
        if (entryScope !== scopeKey()) { ensureScope(); return; }
        if (!current(capture())) return;
        bookingEditingId = prenotazione?.id || null;
        bookingForm?.reset();
        document.getElementById("booking-name").value = prenotazione?.nome_cliente || "";
        document.getElementById("booking-phone").value = prenotazione?.telefono || "";
        document.getElementById("booking-date").value = prenotazione?.data || (bookingCalendar ? _toDateKey(bookingCalendar.getDate()) : "") || oggiIso();
        document.getElementById("booking-time").value = String(prenotazione?.ora || "20:00").slice(0, 5);
        document.getElementById("booking-seats").value = prenotazione?.coperti || "";
        document.getElementById("booking-note").value = prenotazione?.note || "";
        bookingStatusText.textContent = "";
        document.getElementById("booking-create-title").textContent = bookingEditingId ? "Modifica prenotazione" : "Nuova prenotazione";
        apriBookingModal("booking-create-modal");
      }

      document.querySelectorAll("[data-booking-close]").forEach((element) => {
        element.addEventListener("click", chiudiDettaglioPrenotazione);
      });
      [
        ["[data-booking-create-close]", "booking-create-modal"],
        ["[data-booking-availability-close]", "booking-availability-modal"],
        ["[data-booking-export-close]", "booking-export-modal"],
        ["[data-booking-actions-close]", "booking-actions-modal"],
      ].forEach(([selector, id]) => {
        document.querySelectorAll(selector).forEach((element) => element.addEventListener("click", () => chiudiBookingModal(id)));
      });
      document.addEventListener("keydown", (event) => {
        const modal = [...document.querySelectorAll(".booking-modal")].reverse().find((element) => !element.hidden);
        if (!modal || !window.MelpisDialogFocus) return;
        window.MelpisDialogFocus.handleKeydown(modal, event, () => {
          if (modal === bookingModal) chiudiDettaglioPrenotazione();
          else chiudiBookingModal(modal.id);
        });
      });

      document.getElementById("booking-new-trigger")?.addEventListener("click", () => apriFormPrenotazione());
      document.getElementById("booking-new-fab")?.addEventListener("click", () => apriFormPrenotazione());
      document.getElementById("booking-edit-btn")?.addEventListener("click", () => {
        if (!prenotazioneCorrente) return;
        chiudiDettaglioPrenotazione();
        apriFormPrenotazione(prenotazioneCorrente);
      });
      document.getElementById("booking-actions-trigger")?.addEventListener("click", () => apriBookingModal("booking-actions-modal"));
      document.getElementById("booking-open-availability")?.addEventListener("click", () => {
        chiudiBookingModal("booking-actions-modal", { restoreFocus: false });
        apriBookingModal("booking-availability-modal", { restoreTarget: document.getElementById("booking-actions-trigger") });
      });
      document.getElementById("booking-open-export")?.addEventListener("click", () => {
        chiudiBookingModal("booking-actions-modal", { restoreFocus: false });
        apriBookingModal("booking-export-modal", { restoreTarget: document.getElementById("booking-actions-trigger") });
      });

      function inizializzaCalendarioPrenotazioni() {
        if (!bookingCalendarEl || !window.FullCalendar) return;
        const slotRange = intervalloSlotPrenotazioni();
        if (bookingCalendar) {
          bookingCalendar.setOption("slotMinTime", slotRange.min);
          bookingCalendar.setOption("slotMaxTime", slotRange.max);
          bookingCalendar.updateSize();
          return;
        }
        bookingCalendar = new window.FullCalendar.Calendar(bookingCalendarEl, {
          initialView: "timeGridDay",
          timeZone: "local",
          locale: "it",
          height: "auto",
          allDaySlot: false,
          nowIndicator: true,
          slotDuration: "00:15:00",
          snapDuration: "00:15:00",
          slotLabelInterval: "01:00:00",
          slotMinTime: slotRange.min,
          slotMaxTime: slotRange.max,
          slotLabelContent(info) {
            const ora = `${String(info.date.getHours()).padStart(2, "0")}:${String(info.date.getMinutes()).padStart(2, "0")}`;
            const slot = bookingAvailability.get(ora.slice(0, 5));
            const stato = slot?.stato || "verde";
            return { html: `<span class="booking-slot-label booking-slot-${_escapeHtml(stato)}"><span class="booking-slot-dot"></span>${_escapeHtml(ora)}</span>` };
          },
          eventClick(info) { apriDettaglioPrenotazione(info.event.extendedProps); },
          selectable: true,
          headerToolbar: false,
          select(info) {
            apriFormPrenotazione({
              data: _toDateKey(info.start),
              ora: `${String(info.start.getHours()).padStart(2, "0")}:${String(info.start.getMinutes()).padStart(2, "0")}`,
            });
            bookingCalendar.unselect();
          },
          datesSet() {
            if (!current(capture())) return;
            const dateKey = _toDateKey(bookingCalendar.getDate());
            if (!calendarInitializing) aggiornaSemaforo(dateKey, { reuse: true });
            renderTabellaPrenotazioniGiorno(dateKey);
            aggiornaToolbarCalendario();
          },
        });
        calendarInitializing = true;
        try { bookingCalendar.render(); }
        finally { calendarInitializing = false; }
        aggiornaToolbarCalendario();
      }

      function renderTabellaPrenotazioniGiorno(data = null) {
        if (!current(capture())) return;
        const renderToken = capture();
        const tableBody = document.getElementById("booking-table-body");
        const countEl = document.getElementById("booking-table-day-count");
        const titleEl = document.getElementById("booking-table-day-title");
        if (!tableBody) return;

        const targetDate = data ? _toDateKey(data) : (bookingCalendar ? _toDateKey(bookingCalendar.getDate()) : oggiIso());

        if (titleEl) {
          try {
            const dObj = new Date(`${targetDate}T12:00:00`);
            const dateLabel = typeof MelpisI18n !== "undefined"
              ? MelpisI18n.formatDate(dObj, { weekday: "long", day: "numeric", month: "long" })
              : dObj.toLocaleDateString(localeCorrente(), { weekday: "long", day: "numeric", month: "long" });
            titleEl.textContent = _tDash("bookings.runtime.day_title", "Prenotazioni di {{date}}", { date: dateLabel });
          } catch {
            titleEl.textContent = _tDash("bookings.runtime.day_title", "Prenotazioni di {{date}}", { date: targetDate });
          }
        }

        const prenotazioniGiorno = (bookingRecords || [])
          .filter((p) => _toDateKey(p.data) === targetDate)
          .sort((a, b) => String(a.ora || "").localeCompare(String(b.ora || "")));

        if (countEl) {
          countEl.textContent = _tDash("bookings.runtime.count", "{{count}} prenotazioni", { count: prenotazioniGiorno.length });
        }

        if (!prenotazioniGiorno.length) {
          const emptyRow = document.createElement("tr");
          const emptyCell = document.createElement("td");
          emptyCell.colSpan = 8;
          emptyCell.className = "booking-table-empty";
          emptyCell.textContent = _tDash(
            "bookings.runtime.empty_day",
            'Nessuna prenotazione per {{date}}. Clicca "+ Nuova prenotazione" per aggiungerne una.',
            { date: targetDate }
          );
          emptyRow.appendChild(emptyCell);
          tableBody.replaceChildren(emptyRow);
          return;
        }

        tableBody.innerHTML = prenotazioniGiorno.map((p) => {
          const ora = String(p.ora || "").slice(0, 5);
          const stato = statoNormalizzatoPrenotazione(p);
          const syncStatus = p.external_sync_provider === "google"
            ? String(p.external_sync_status || "").toLowerCase()
            : "";
          const syncCode = typeof p.external_sync_error_code === "string" && SAFE_CALENDAR_SYNC_ERROR_CODES.has(p.external_sync_error_code)
            ? p.external_sync_error_code
            : "";
          let syncLabel = "";
          if (p.calendar_confirm_pending === true) {
            if (syncStatus === "uncertain") {
              syncLabel = "Prenotazione non confermata: esito Google incerto, in attesa di nuova verifica";
            } else if (["blocked", "failed"].includes(syncStatus)) {
              syncLabel = "Prenotazione non confermata: verifica Google bloccata";
            } else {
              syncLabel = "Prenotazione non confermata: in attesa di nuova verifica Google";
            }
            if (syncCode) syncLabel += ` (${syncCode})`;
          } else if (stato === "confermata") {
            if (["pending", "pending_retry", "processing"].includes(syncStatus)) {
              syncLabel = "Confermata in Melpis, sincronizzazione Google in attesa";
              if (syncCode) syncLabel += ` (${syncCode})`;
            } else if (syncStatus === "failed") {
              syncLabel = "Confermata in Melpis, sincronizzazione Google non riuscita";
              if (syncCode) syncLabel += ` (${syncCode})`;
            } else if (syncStatus === "blocked") {
              syncLabel = "Confermata in Melpis, sincronizzazione Google bloccata";
              if (syncCode) syncLabel += ` (${syncCode})`;
            } else if (syncStatus === "uncertain") {
              syncLabel = "Confermata in Melpis, esito Google incerto";
              if (syncCode) syncLabel += ` (${syncCode})`;
            } else if (["succeeded", "synced"].includes(syncStatus)) {
              syncLabel = "Sincronizzata con Google";
            }
          }
          let badgeClass = "booking-badge-confermata";
          if (stato.includes("attesa")) badgeClass = "booking-badge-in_attesa";
          else if (stato.includes("intervento")) badgeClass = "booking-badge-richiede_intervento";
          else if (stato.includes("cancell") || stato.includes("rifiut")) badgeClass = "booking-badge-cancellata";

          return `
            <tr data-booking-id="${_escapeHtml(p.id)}">
              <td class="booking-row-time">${_escapeHtml(ora)}</td>
              <td class="booking-row-client">${_escapeHtml(p.nome_cliente || "Cliente")}</td>
              <td>${_escapeHtml(p.telefono || "—")}</td>
              <td>${_escapeHtml(formattaUnitaVerticale(p.coperti || 1))}</td>
              <td><span class="booking-origin-tag">${_escapeHtml(p.origine || "WhatsApp")}</span></td>
              <td>${_escapeHtml(p.note || "—")}</td>
              <td><span class="booking-badge ${badgeClass}">${_escapeHtml(p.stato || "confermata")}</span>${syncLabel ? `<small class="booking-sync-status">${_escapeHtml(syncLabel)}</small>` : ""}</td>
              <td style="text-align: right;">
                <button type="button" class="report-refresh" data-open-booking-id="${_escapeHtml(p.id)}" style="padding: 4px 10px; font-size: 0.75rem;">
                  ${_escapeHtml(_tDash("bookings.runtime.details", "Dettagli"))}
                </button>
              </td>
            </tr>
          `;
        }).join("");

        tableBody.querySelectorAll("[data-open-booking-id]").forEach((btn) => {
          btn.addEventListener("click", (e) => {
            e.stopPropagation();
            if (!current(renderToken)) return;
            const bId = btn.dataset.openBookingId;
            const item = bookingRecords.find((b) => String(b.id) === String(bId));
            if (item) apriDettaglioPrenotazione(item);
          });
        });

        tableBody.querySelectorAll("tr[data-booking-id]").forEach((tr) => {
          tr.style.cursor = "pointer";
          tr.addEventListener("click", () => {
            if (!current(renderToken)) return;
            const bId = tr.dataset.bookingId;
            const item = bookingRecords.find((b) => String(b.id) === String(bId));
            if (item) apriDettaglioPrenotazione(item);
          });
        });
      }

      function aggiornaPrenotazioni({ reuse = false } = {}) {
        ensureScope();
        if (reuse && listFlight && current(listFlight.token)) return listFlight.promise;
        const flight = { token: capture(), promise: loadBookings() };
        listFlight = flight;
        flight.promise.finally(() => { if (listFlight === flight) listFlight = null; }).catch(() => {});
        return flight.promise;
      }

      async function loadBookings() {
        const token = capture();
        if (!bookingCalendarEl || !current(token)) return;
        const transition = getContext()?.transition;
        const request = ++bookingListRequest;
        const isCurrent = () => current(token) && request === bookingListRequest && transition === getContext()?.transition;
        try {
          const res = await apiFetch(`${API_BASE}/api/bookings`);
          if (!isCurrent()) return;
          if (!res.ok) { if ([401, 403].includes(res.status)) clearPrivateDOM(); return; }
          const raw = await res.json().catch(() => []);
          if (!isCurrent()) return;
          const prenotazioni = Array.isArray(raw) ? raw : [];
          bookingRecords = prenotazioni;
          const pending = prenotazioni.filter((p) => statoNormalizzatoPrenotazione(p) === "in_attesa");
          bookingCount.textContent = _tDash("bookings.runtime.count", "{{count}} prenotazioni", { count: prenotazioni.length });
          updatePending(pending.length);
          if (bookingPendingValue) bookingPendingValue.textContent = String(pending.length);
          if (!bookingCalendar) inizializzaCalendarioPrenotazioni();
          if (!bookingCalendar) return;
          bookingCalendar.removeAllEvents();
          const mostrabili = bookingPendingOnly ? pending : prenotazioni;
          mostrabili.forEach((p) => {
            if (!p.data || !p.ora) return;
            const dKey = _toDateKey(p.data);
            const ora = String(p.ora).slice(0, 5);
            const [h, m] = ora.split(":").map(Number);
            const totalEndMinutes = (isNaN(m) ? 0 : m) + 15;
            const endH = totalEndMinutes >= 60 ? (h + 1) : h;
            const endM = totalEndMinutes >= 60 ? (totalEndMinutes - 60) : totalEndMinutes;
            const oraFine = `${String(endH).padStart(2, "0")}:${String(endM).padStart(2, "0")}`;
            bookingCalendar.addEvent({
              id: p.id,
              title: `${ora} · ${p.nome_cliente || "Cliente"} · ${formattaUnitaVerticale(p.coperti || 1)}`,
              start: `${dKey}T${ora}:00`,
              end: `${dKey}T${oraFine}:00`,
              backgroundColor: colorePrenotazione(p.stato),
              borderColor: colorePrenotazione(p.stato),
              classNames: statoNormalizzatoPrenotazione(p) === "in_attesa" ? ["booking-event-pending"] : [],
              extendedProps: p,
            });
          });
          const slotRange = intervalloSlotPrenotazioni();
          bookingCalendar.setOption("slotMinTime", slotRange.min);
          bookingCalendar.setOption("slotMaxTime", slotRange.max);
          bookingCalendar.updateSize();
          bookingCalendar.render();

          const currentDate = bookingCalendar ? _toDateKey(bookingCalendar.getDate()) : oggiIso();
          renderTabellaPrenotazioniGiorno(currentDate);

          verificaPrenotazioneAggiornata(prenotazioni);
          bookingSnapshot = new Map(prenotazioni.map((p) => [String(p.id), JSON.stringify(p)]));
        } catch (err) {
          if (!isCurrent()) return;
          // Preserve the quiet read failure without logging sensitive response data.
        }
      }

      function intervalloSlotPrenotazioni() {
        const aperte = Object.entries(bookingOpenHours || {})
          .filter(([, capienza]) => Number(capienza) > 0)
          .map(([ora]) => Number(ora.slice(0, 2)));

        const orePrenotazioni = (bookingRecords || [])
          .filter((p) => p.ora)
          .map((p) => Number(String(p.ora).slice(0, 2)));

        const tutte = [...aperte, ...orePrenotazioni].filter((h) => !isNaN(h));
        if (!tutte.length) return { min: "06:00:00", max: "24:00:00" };

        const minH = Math.min(...tutte);
        const maxH = Math.max(...tutte);
        const min = `${String(Math.max(0, Math.min(minH, 6))).padStart(2, "0")}:00:00`;
        const max = maxH >= 23 ? "24:00:00" : `${String(Math.max(maxH + 1, 23)).padStart(2, "0")}:00:00`;
        return { min, max };
      }

      function aggiornaToolbarCalendario() {
        if (!bookingCalendar) return;
        const vista = bookingCalendar.view.type;
        document.querySelectorAll("[data-booking-calendar-view]").forEach((button) => {
          button.setAttribute("aria-pressed", String(button.dataset.bookingCalendarView === vista));
        });
        const dateKey = _toDateKey(bookingCalendar.getDate());
        const picker = document.getElementById("booking-date-picker");
        const label = document.getElementById("booking-selected-date-label");
        const trigger = document.getElementById("booking-date-picker-trigger");
        if (picker) picker.value = dateKey;
        if (label) label.textContent = bookingCalendar.getDate().toLocaleDateString(localeCorrente(), { day: "numeric", month: "short", year: "numeric" });
        if (trigger) {
          trigger.classList.toggle("is-selected", dateKey !== oggiIso());
          trigger.setAttribute("aria-label", `Seleziona la data delle prenotazioni. Giorno selezionato: ${label?.textContent || dateKey}`);
        }
      }

      function verificaPrenotazioneAggiornata(prenotazioni) {
        if (!prenotazioneCorrente?.id || bookingModal?.hidden) return;
        const next = prenotazioni.find((p) => String(p.id) === String(prenotazioneCorrente.id));
        const warning = document.getElementById("booking-detail-stale-warning");
        if (!next || bookingSnapshot.get(String(next.id)) !== JSON.stringify(next)) {
          warning.textContent = next
            ? "Questa prenotazione è stata modificata. Ricarica per vedere lo stato aggiornato."
            : "Questa prenotazione è stata cancellata. Ricarica per vedere lo stato aggiornato.";
          warning.hidden = false;
        }
      }

      function aggiornaSemaforo(data = null, { reuse = false } = {}) {
        ensureScope();
        const day = data ? _toDateKey(data) : (bookingCalendar ? _toDateKey(bookingCalendar.getDate()) : oggiIso());
        if (reuse && availabilityFlight?.day === day && current(availabilityFlight.token)) return availabilityFlight.promise;
        const flight = { day, token: capture(), promise: loadAvailability(data) };
        availabilityFlight = flight;
        flight.promise.finally(() => { if (availabilityFlight === flight) availabilityFlight = null; }).catch(() => {});
        return flight.promise;
      }

      async function loadAvailability(data = null) {
        const token = capture();
        if (!availabilityList || !current(token)) return;
        const transition = getContext()?.transition;
        const selectedDate = () => bookingCalendar ? _toDateKey(bookingCalendar.getDate()) : oggiIso();
        const targetDate = data ? _toDateKey(data) : selectedDate();
        // Mutation refreshes can reference a day that is no longer selected.
        if (targetDate !== selectedDate()) return;
        const request = ++bookingAvailabilityRequest;
        const isCurrent = () => current(token) && request === bookingAvailabilityRequest && transition === getContext()?.transition && targetDate === selectedDate();
        availabilityDate.textContent = new Date(`${targetDate}T12:00:00`).toLocaleDateString(localeCorrente(), {
          weekday: "short",
          day: "2-digit",
          month: "2-digit",
        });
        try {
          const res = await apiFetch(`${API_BASE}/api/bookings/semaforo?data=${targetDate}`);
          if (!isCurrent()) return;
          if (!res.ok) return;
          const raw = await res.json().catch(() => []);
          if (!isCurrent()) return;
          const slots = Array.isArray(raw) ? raw : [];
          bookingAvailability = new Map(slots.map((slot) => [String(slot.ora).slice(0, 5), slot]));
          availabilityList.innerHTML = "";
          slots.forEach((slot) => {
            const item = document.createElement("div");
            item.classList.add("availability-item");
            if (/^[a-z-]+$/.test(slot.stato)) item.classList.add(`availability-${slot.stato}`);
            item.innerHTML = `
              <span class="availability-dot"></span>
              <span class="availability-hour">${_escapeHtml(slot.ora)}</span>
              <span class="availability-seats">${_escapeHtml(slot.coperti_liberi)}/${_escapeHtml(slot.coperti_massimi)} liberi</span>
            `;
            availabilityList.appendChild(item);
          });
          aggiornaRiepilogoPrenotazioni(targetDate, slots);
          renderTabellaPrenotazioniGiorno(targetDate);
          bookingCalendar?.render();
        } catch (err) {
          if (!isCurrent()) return;
          // Quiet failure, no response contents in client logs.
        }
      }

      function aggiornaRiepilogoPrenotazioni(data, slots) {
        const prenotazioniGiorno = (bookingRecords || []).filter((p) => p.data === data && !STATI_FINALI_PRENOTAZIONE.includes(statoNormalizzatoPrenotazione(p)));
        const coperti = prenotazioniGiorno.reduce((totale, p) => totale + (Number(p.coperti) || 0), 0);
        const liberi = (slots || []).reduce((totale, slot) => totale + (Number(slot.coperti_liberi) || 0), 0);
        if (bookingSummary) bookingSummary.textContent = `${prenotazioniGiorno.length} prenotazioni · ${formattaUnitaVerticale(coperti)} · ${liberi} posti liberi`;

        const emptyNotice = document.getElementById("booking-day-empty");
        if (emptyNotice) {
          emptyNotice.hidden = prenotazioniGiorno.length > 0;
          const emptyBtn = document.getElementById("booking-empty-new-btn");
          if (emptyBtn) {
            emptyBtn.onclick = () => {
              apriFormPrenotazione({ data: data ? _toDateKey(data) : oggiIso(), ora: "20:00" });
            };
          }
        }
      }

      async function aggiornaImpostazioniPrenotazioni() {
        const token = capture();
        if (!bookingSettingsGrid || bookingSettingsGrid.children.length || !current(token)) return;
        const transition = getContext()?.transition;
        try {
          const res = await apiFetch(`${API_BASE}/api/bookings/settings`);
          if (!current(token) || transition !== getContext()?.transition) return;
          if (!res.ok) return;
          const data = await res.json() || {};
          if (!current(token) || transition !== getContext()?.transition) return;
          const capienze = data.capienze_orarie || {};
          const validHour = (hour) => /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(hour);
          const configuredHours = Array.isArray(data.fasce_orarie) ? data.fasce_orarie.filter(validHour) : [];
          const hours = [...new Set([
            ...(configuredHours.length ? configuredHours : Array.from({ length: 24 }, (_, hour) => `${String(hour).padStart(2, "0")}:00`)),
            ...Object.keys(capienze).filter(validHour),
          ])].sort();
          bookingOpenHours = capienze;
          const standard = document.getElementById("booking-standard-capacity");
          if (standard) {
            const valori = Object.values(capienze).filter((value) => Number(value) > 0);
            standard.value = valori.length ? Math.max(...valori) : Object.keys(capienze).length ? 0 : 40;
          }
          bookingSettingsGrid.innerHTML = hours.map((ora) => `
            <label class="booking-setting"><span>${_escapeHtml(ora)}</span><input type="number" min="0" max="500" required data-capacity-hour="${_escapeHtml(ora)}" value="${_escapeHtml(capienze[ora] ?? 40)}"></label>
          `).join("");
        } catch {
          // Staff settings denial must not prevent authorized booking reads.
        }
      }

      bookingForm?.addEventListener("submit", async (e) => {
        e.preventDefault();
        const token = beginMutation("save");
        if (!token) return;
        bookingStatusText.textContent = "";
        const transition = getContext()?.transition;
        const submittedEditingId = bookingEditingId;
        let expectedEditingId = submittedEditingId;
        let formTransition = ++bookingFormTransition;
        const selectedDate = () => bookingCalendar ? _toDateKey(bookingCalendar.getDate()) : oggiIso();
        let expectedDate = selectedDate();
        const isCurrent = () => current(token) && transition === getContext()?.transition && formTransition === bookingFormTransition && expectedEditingId === bookingEditingId && expectedDate === selectedDate();
        const payload = {
          nome_cliente: document.getElementById("booking-name").value.trim(),
          telefono: document.getElementById("booking-phone").value.trim(),
          data: document.getElementById("booking-date").value,
          ora: document.getElementById("booking-time").value,
          coperti: parseInt(document.getElementById("booking-seats").value, 10),
          note: document.getElementById("booking-note").value.trim(),
        };
        try {
          const res = await apiFetch(`${API_BASE}/api/bookings${submittedEditingId ? `/${encodeURIComponent(submittedEditingId)}` : ""}`, {
            method: submittedEditingId ? "PUT" : "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          });
          if (!isCurrent()) return;
          if (!res.ok) {
            const err = await res.json().catch(() => null);
            if (!isCurrent()) return;
            throw new Error(err?.detail?.messaggio || err?.detail || "Errore salvataggio");
          }
          const wasEditing = Boolean(submittedEditingId);
          bookingForm.reset();
          document.getElementById("booking-date").value = payload.data;
          bookingStatusText.textContent = wasEditing ? "Modifica salvata." : "Prenotazione aggiunta.";
          bookingStatusText.style.color = "var(--sage)";
          bookingEditingId = null;
          chiudiBookingModal("booking-create-modal");
          if (bookingCalendar) {
            bookingCalendar.gotoDate(payload.data);
          }
          // The successful save closed its own form and selected its saved day.
          // Subsequent refreshes still belong to this exact UI context.
          formTransition = bookingFormTransition;
          expectedEditingId = bookingEditingId;
          expectedDate = selectedDate();
          await aggiornaPrenotazioni();
          if (!isCurrent()) return;
          await aggiornaSemaforo(payload.data);
          if (!isCurrent()) return;
          renderTabellaPrenotazioniGiorno(payload.data);
        } catch (err) {
          if (!isCurrent()) return;
          bookingStatusText.textContent = submittedEditingId ? "Modifica non salvata, riprova." : err.message;
          bookingStatusText.style.color = "var(--red)";
        } finally {
          mutationLocks.delete("save");
        }
      });

      capacitySave?.addEventListener("click", async () => {
        const token = beginMutation("capacity");
        if (!token) return;
        capacityStatus.textContent = "";
        try {
          const inputs = [...bookingSettingsGrid.querySelectorAll("[data-capacity-hour]")];
          if (!inputs.length) throw new Error("Impostazioni capienza non disponibili. Ricarica e riprova.");
          const invalid = inputs.find((input) => !input.checkValidity() || !Number.isInteger(Number(input.value)));
          if (invalid) {
            invalid.reportValidity();
            throw new Error("Inserisci una capienza intera da 0 a 500 per ogni fascia.");
          }
          const res = await apiFetch(`${API_BASE}/api/bookings/settings`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              capienze_orarie: Object.fromEntries(inputs.map((input) => [input.dataset.capacityHour, Number(input.value)])),
            }),
          });
          if (!current(token)) return;
          if (!res.ok) throw new Error("Errore salvataggio capienza");
          const savedSettings = await res.json();
          if (!current(token)) return;
          bookingOpenHours = savedSettings.capienze_orarie || {};
          if (bookingCalendar) {
            bookingCalendar.destroy();
            bookingCalendar = null;
            inizializzaCalendarioPrenotazioni();
          }
          capacityStatus.textContent = "Capienza aggiornata.";
          capacityStatus.style.color = "var(--sage)";
          await aggiornaSemaforo();
          if (!current(token)) return;
          await aggiornaPrenotazioni();
          if (!current(token)) return;
          capacityStatus.textContent += " Le modifiche sono attive subito.";
        } catch (err) {
          if (!current(token)) return;
          capacityStatus.textContent = err.message;
          capacityStatus.style.color = "var(--red)";
        } finally {
          mutationLocks.delete("capacity");
        }
      });

      document.getElementById("booking-standard-capacity")?.addEventListener("change", (event) => {
        if (!event.target.value || !event.target.checkValidity()) return;
        const value = Number(event.target.value);
        bookingSettingsGrid.querySelectorAll("[data-capacity-hour]").forEach((input) => {
          if (parseInt(input.value, 10) > 0) input.value = value;
        });
      });

      document.getElementById("booking-pending-count")?.addEventListener("click", () => {
        bookingPendingOnly = !bookingPendingOnly;
        document.getElementById("booking-pending-count").setAttribute("aria-pressed", String(bookingPendingOnly));
        aggiornaPrenotazioni();
      });

      document.querySelectorAll("[data-booking-calendar-command]").forEach((button) => {
        button.addEventListener("click", () => {
          if (!bookingCalendar) return;
          const command = button.dataset.bookingCalendarCommand;
          if (command === "prev") bookingCalendar.prev();
          if (command === "next") bookingCalendar.next();
          if (command === "today") bookingCalendar.today();
        });
      });

      const bookingDatePicker = document.getElementById("booking-date-picker");
      document.getElementById("booking-date-picker-trigger")?.addEventListener("click", () => {
        if (!bookingDatePicker) return;
        try {
          bookingDatePicker.showPicker();
        } catch {
          bookingDatePicker.classList.add("booking-date-picker-input--fallback");
          bookingDatePicker.tabIndex = 0;
          bookingDatePicker.focus();
        }
      });
      bookingDatePicker?.addEventListener("change", () => {
        if (!bookingCalendar || !/^\d{4}-\d{2}-\d{2}$/.test(bookingDatePicker.value)) return;
        bookingCalendar.gotoDate(bookingDatePicker.value);
      });

      document.querySelectorAll("[data-booking-calendar-view]").forEach((button) => {
        button.addEventListener("click", () => bookingCalendar?.changeView(button.dataset.bookingCalendarView));
      });

      function prenotazioniVisibili() {
        const panel = document.querySelector('[data-view-panel="prenotazioni"]');
        return Boolean(panel && !panel.classList.contains("view-hidden") && !document.hidden);
      }

      function sincronizzaPollingPrenotazioni() {
        window.clearInterval(bookingPollTimer);
        bookingPollTimer = null;
        if (!active || !current(capture()) || !prenotazioniVisibili()) return;
        bookingPollTimer = window.setInterval(() => {
          aggiornaPrenotazioni({ reuse: true });
          aggiornaSemaforo(null, { reuse: true });
        }, 30000);
      }

      document.getElementById("booking-export-csv")?.addEventListener("click", async () => {
        const da = document.getElementById("booking-export-da")?.value || "";
        const a = document.getElementById("booking-export-a")?.value || "";
        if (da && a && da > a) {
          toast("La data inizio è dopo la data fine.", "error");
          return;
        }
        const token = beginMutation("export");
        if (!token) return;
        try { await exportCsv(da, a, () => current(token)); }
        catch { if (current(token)) toast("Errore di connessione durante l'export.", "error"); }
        finally { mutationLocks.delete("export"); }
      });

      document.addEventListener("visibilitychange", sincronizzaPollingPrenotazioni);

      document.getElementById("booking-date").value = oggiIso();
      function onEnter() {
        if (entryScope !== null && entryScope !== scopeKey()) invalidate();
        entryScope = scopeKey();
        active = true;
        sincronizzaPollingPrenotazioni();
      }
      function onExit() {
        active = false;
        epoch += 1;
        bookingFormTransition += 1;
        bookingDetailTransition += 1;
        closeDialogs();
        loadFlight = null;
        window.clearInterval(bookingPollTimer);
        bookingPollTimer = null;
      }
      function invalidate() {
        onExit();
        clearPrivateDOM();
      }
      function clearPrivateDOM() {
        bookingListRequest += 1;
        bookingAvailabilityRequest += 1;
        bookingFormTransition += 1;
        bookingDetailTransition += 1;
        prenotazioneCorrente = null;
        bookingRecords = [];
        bookingAvailability = new Map();
        bookingSnapshot = new Map();
        bookingOpenHours = {};
        bookingPendingOnly = false;
        bookingEditingId = null;
        bookingForm?.reset();
        bookingCalendar?.destroy();
        bookingCalendar = null;
        bookingSettingsGrid?.replaceChildren();
        for (const id of ["booking-standard-capacity", "booking-export-da", "booking-export-a"]) {
          const input = document.getElementById(id);
          if (input) input.value = "";
        }
        availabilityList?.replaceChildren();
        document.getElementById("booking-table-body")?.replaceChildren();
        for (const el of Object.values(bookingDetail)) if (el) el.textContent = "";
        for (const el of [bookingCount, bookingSummary, bookingStatusText, capacityStatus, bookingPendingValue]) if (el) el.textContent = "";
        document.getElementById("booking-pending-count")?.setAttribute("aria-pressed", "false");
        updatePending(0);
        closeDialogs();
      }
      function load() {
        if (loadFlight && current(loadFlight.token)) return loadFlight.promise;
        const flight = { token: capture(), promise: loadView() };
        loadFlight = flight;
        flight.promise.finally(() => { if (loadFlight === flight) loadFlight = null; }).catch(() => {});
        return flight.promise;
      }
      async function loadView() {
        const token = capture();
        const transition = getContext()?.transition;
        const isCurrent = () => current(token) && getContext()?.transition === transition;
        await aggiornaImpostazioniPrenotazioni();
        if (!isCurrent()) return;
        inizializzaCalendarioPrenotazioni();
        await aggiornaPrenotazioni({ reuse: true });
        if (!isCurrent()) return;
        await aggiornaSemaforo(null, { reuse: true });
        if (!isCurrent()) return;
        if (bookingCalendar) {
          bookingCalendar.updateSize();
          bookingCalendar.render();
        }
        renderTabellaPrenotazioniGiorno();
      }
      return Object.freeze({
        onEnter, onExit, invalidate, load,
        aggiornaPrenotazioni, aggiornaSemaforo, apriBookingModal,
      });
    },
  });
});
