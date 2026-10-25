(function attachDashboardOverview(root, factory) {
  const api = factory(root);
  if (root) root.MelpisDashboardOverview = api;
})(typeof window !== "undefined" ? window : globalThis, function createDashboardOverview(root) {
  "use strict";

  return Object.freeze({
    create(deps) {
      const {
        API_BASE, apiFetch, _sanitize, toast, _tDash, t, localeCorrente,
        _toDateKey, _emptyState, _errorState, _skeletonList, ICONS,
        getContext, apriView, apriVistaImpostazioni, apriBookingModal,
        getReviewsApi, loadOnboarding,
      } = deps;
      const document = root.document;
      let epoch = 0;
      let active = false;
      let destroyed = false;
      let entryScope = null;
      let prioritySequence = 0;
      let summarySequence = 0;
      const pendingTimers = new Set();
      function capture(view = "panoramica") {
        return { snapshot: getContext(), epoch, view };
      }
      function current(token) {
        const now = getContext();
        return active && !destroyed && epoch === token.epoch && token.view === "panoramica"
          && now?.view === token.view && now?.userId && now?.sessionOrganizationId
          && JSON.stringify(now) === JSON.stringify(token.snapshot);
      }
      function guard(token) { return current(token); }
      function sameContext(expected) {
        const now = getContext();
        return Boolean(now?.userId && now?.sessionOrganizationId)
          && JSON.stringify(now) === JSON.stringify(expected);
      }
      function clearWork() {
        epoch += 1;
        for (const timer of pendingTimers) root.clearTimeout(timer);
        pendingTimers.clear();
        fermaPanoramicaPolling();
      }
      function scopeKey() {
        const context = getContext();
        return JSON.stringify([context?.userId, context?.sessionOrganizationId, context?.selectedOrganizationId]);
      }
      function clearScopeData() {
        priorityList?.replaceChildren();
        ticketList?.replaceChildren();
        [statTotale, statAi, statUmano].forEach((node) => { if (node) node.textContent = "0"; });
        const badge = document.getElementById("priority-count-badge");
        if (badge) { badge.textContent = ""; badge.hidden = true; }
        ["totale", "ai", "umano"].forEach((metric) => {
          document.getElementById(`sparkline-${metric}`)?.replaceChildren();
          document.getElementById(`sparkline-${metric}-val`)?.replaceChildren();
          document.getElementById(`trend-${metric}`)?.replaceChildren();
        });
      }

      const prioritySection = document.getElementById("priority-section");
      const priorityList = document.getElementById("priority-list");
      const ticketList = document.getElementById("ticket-list");
      const statTotale = document.getElementById("stat-totale");
      const statAi = document.getElementById("stat-ai");
      const statUmano = document.getElementById("stat-umano");
      const loadOnboardingState = loadOnboarding || caricaStatoOnboarding;

async function caricaStatoOnboarding(token = capture()) {
  try {
    const [resDoc, resBook, resWa] = await Promise.allSettled([
      apiFetch(`${API_BASE}/api/documenti/conteggio`),
      apiFetch(`${API_BASE}/api/bookings/settings`),
      apiFetch(`${API_BASE}/api/whatsapp/settings`)
    ]);
    if (!guard(token)) return null;

    if ([resDoc, resBook, resWa].some((result) => result.status !== "fulfilled" || !result.value?.ok)) {
      return { unavailable: true };
    }

    const [d, b, w] = await Promise.all([
      resDoc.value.json().catch(() => null),
      resBook.value.json().catch(() => null),
      resWa.value.json().catch(() => null),
    ]);
    if (!guard(token)) return null;
    if (!d || typeof d !== "object" || !b || typeof b !== "object" || !w || typeof w !== "object"
      || typeof d.chunk_indicizzati !== "number" || !Number.isFinite(d.chunk_indicizzati)
      || !b.capienze_orarie || typeof b.capienze_orarie !== "object" || Array.isArray(b.capienze_orarie)) {
      return { unavailable: true };
    }

    const hasDocs = Number(d.chunk_indicizzati) > 0;
    const hasBookingHours = Object.values(b.capienze_orarie).some((value) => Number.isFinite(Number(value)) && Number(value) > 0);
    const hasWa = Boolean(
      (typeof w.phone_number_id === "string" && w.phone_number_id.trim())
      || w.status === "connected"
      || w.configured === true
    );

    return {
      hasDocs,
      hasBookingHours,
      hasWa,
      isFullyConfigured: hasDocs && hasBookingHours && hasWa,
    };
  } catch {
    if (!guard(token)) return null;
    return { unavailable: true };
  }
}

async function aggiornaPrioritari(silent = false) {
  const token = capture();
  if (!guard(token)) return;
  const request = ++prioritySequence;
  const isCurrent = () => request === prioritySequence && guard(token);
  if (!silent && !priorityList.children.length) {
    priorityList.innerHTML = _skeletonList(3);
  }
  try {
    const res = await apiFetch(`${API_BASE}/api/dashboard/prioritari`);
    if (!isCurrent()) return;
    if (!res.ok) {
      if (!silent) {
        priorityList.innerHTML = "";
        priorityList.appendChild(_errorState(_tDash("overview.runtime.priority_error", "Impossibile caricare le richieste urgenti."), () => aggiornaPrioritari()));
      }
      return;
    }
    const rawEventi = await res.json().catch(() => []);
    if (!isCurrent()) return;
    const eventi = Array.isArray(rawEventi) ? rawEventi : [];
    const cfg = eventi.length === 0 ? await loadOnboardingState(token) : null;
    if (!isCurrent()) return;
    if (eventi.length === 0 && (!cfg || cfg.unavailable)) {
      priorityList.innerHTML = "";
      priorityList.appendChild(_errorState(
        _tDash("overview.runtime.setup_unavailable", "Impossibile verificare la configurazione. Riprova."),
        () => aggiornaPrioritari()
      ));
      return;
    }
    const countBadge = document.getElementById("priority-count-badge");
    if (countBadge) {
      countBadge.textContent = eventi.length ? _tDash("overview.runtime.priority_count", "{{count}} elementi", { count: eventi.length }) : "";
      countBadge.hidden = !eventi.length;
    }
    priorityList.innerHTML = "";
    if (eventi.length === 0) {
      const li = document.createElement("li");
      if (cfg && !cfg.isFullyConfigured) {
        li.className = "onboarding-checklist-item";
        li.innerHTML = `
          <div class="onboarding-checklist-card">
            <div class="onboarding-checklist-header">
              <div class="onboarding-checklist-icon-wrap" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="22" height="22"><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" stroke-linecap="round" stroke-linejoin="round"/></svg>
              </div>
              <div>
                <h3 class="onboarding-checklist-title">Completa la configurazione iniziale</h3>
                <p class="onboarding-checklist-sub">Segui questi 3 passaggi per attivare l'assistente con i tuoi clienti.</p>
              </div>
              <button type="button" class="onboarding-step-btn" data-action="setup-wizard">${_tDash("onboarding.full_guide", "Guida completa · 7 passaggi")}</button>
            </div>
            <div class="onboarding-checklist-steps">
              <div class="onboarding-step-row">
                <div class="onboarding-step-left">
                  <span class="onboarding-step-indicator ${cfg.hasWa ? "done" : "todo"}">${cfg.hasWa ? "✓" : "1"}</span>
                  <span class="onboarding-step-text ${cfg.hasWa ? "done" : ""}">Collega il numero WhatsApp Business</span>
                </div>
                ${cfg.hasWa
                  ? '<span class="onboarding-step-done-badge">Completato</span>'
                  : '<button type="button" class="onboarding-step-btn" data-action="setup-wa">Collega →</button>'}
              </div>
              <div class="onboarding-step-row">
                <div class="onboarding-step-left">
                  <span class="onboarding-step-indicator ${cfg.hasDocs ? "done" : "todo"}">${cfg.hasDocs ? "✓" : "2"}</span>
                  <span class="onboarding-step-text ${cfg.hasDocs ? "done" : ""}">Carica menu o listino (Knowledge Base)</span>
                </div>
                ${cfg.hasDocs
                  ? '<span class="onboarding-step-done-badge">Completato</span>'
                  : '<button type="button" class="onboarding-step-btn" data-action="setup-docs">Carica PDF →</button>'}
              </div>
              <div class="onboarding-step-row">
                <div class="onboarding-step-left">
                  <span class="onboarding-step-indicator ${cfg.hasBookingHours ? "done" : "todo"}">${cfg.hasBookingHours ? "✓" : "3"}</span>
                  <span class="onboarding-step-text ${cfg.hasBookingHours ? "done" : ""}">Configura orari e capienza tavoli</span>
                </div>
                ${cfg.hasBookingHours
                  ? '<span class="onboarding-step-done-badge">Completato</span>'
                  : '<button type="button" class="onboarding-step-btn" data-action="setup-booking">Configura →</button>'}
              </div>
            </div>
          </div>
        `;
        li.querySelector('[data-action="setup-wa"]')?.addEventListener("click", () => {
          apriVistaImpostazioni("whatsapp");
        });
        li.querySelector('[data-action="setup-wizard"]')?.addEventListener("click", () => {
          apriView("onboarding");
        });
        li.querySelector('[data-action="setup-docs"]')?.addEventListener("click", () => {
          apriView("conoscenza");
        });
        li.querySelector('[data-action="setup-booking"]')?.addEventListener("click", () => {
          apriView("prenotazioni");
          const targetContext = getContext();
          const timer = root.setTimeout(() => {
            pendingTimers.delete(timer);
            if (sameContext(targetContext)) apriBookingModal("booking-availability-modal");
          }, 100);
          pendingTimers.add(timer);
        });
      } else {
        li.appendChild(_emptyState(
          ICONS.check,
          _tDash("overview.runtime.no_messages_title", "Nessun messaggio oggi"),
          _tDash("overview.runtime.no_messages_desc", "L'assistente è configurato e pronto a rispondere automaticamente ai tuoi clienti.")
        ));
      }
      priorityList.appendChild(li);
      return;
    }
    eventi.forEach((e) => {
      const li = document.createElement("li");
      li.classList.add("priority-item", `prio-${e.priorita}`);
      const badge = document.createElement("span");
      badge.classList.add("priority-item-badge", `badge-${e.tipo_evento}`);
      badge.textContent = e.tipo_evento === "recensione" ? "REC" : "MSG";
      const msg = document.createElement("span");
      msg.classList.add("priority-item-msg");
      msg.textContent = e.testo_originale;
      const cat = document.createElement("span");
      cat.classList.add("priority-item-cat");
      cat.textContent = (e.dettagli?.categoria || e.dettagli?.sentiment || "generico");
      li.appendChild(badge);
      li.appendChild(msg);
      li.appendChild(cat);

      if (e.tipo_evento === "recensione") {
        li.style.cursor = "pointer";
        li.title = "Clicca per aprire e gestire questa recensione";
        li.addEventListener("click", () => {
          apriView("recensioni");
          const transitionContext = getContext();
          const timer = root.setTimeout(() => {
            pendingTimers.delete(timer);
            if (sameContext(transitionContext) && transitionContext.view === "recensioni" && e.id) {
              getReviewsApi()?.apriDettaglioRecensione(e.id);
            }
          }, 80);
          pendingTimers.add(timer);
        });
      }

      priorityList.appendChild(li);
    });
  } catch (err) {
    if (!isCurrent()) return;
    console.error("Impossibile aggiornare gli eventi prioritari:", err);
    priorityList.innerHTML = "";
    priorityList.appendChild(_errorState(_tDash("overview.runtime.priority_error", "Impossibile caricare le richieste urgenti."), aggiornaPrioritari));
  }
}

/* ============================================================
   KPI & SPARKLINE 7 GIORNI (PANORAMICA)
   ============================================================ */

function generaSparklineSvg(dataPoints, strokeColor, gradientId) {
  const pts = Array.isArray(dataPoints) && dataPoints.length === 7 ? dataPoints : [0, 0, 0, 0, 0, 0, 0];
  const maxVal = Math.max(...pts, 1);
  const width = 84;
  const height = 24;
  const barWidth = 3;
  const gap = 5;
  const allZero = pts.every((v) => v === 0);

  let barsHtml = "";
  pts.forEach((val, i) => {
    const x = i * (barWidth + gap) + 4;
    const h = allZero ? (3 + (i % 3) * 4) : Math.max(3, (val / maxVal) * (height - 4));
    const y = height - h;
    const opacity = allZero ? (0.22 + (i / 7) * 0.32) : (0.4 + (val / maxVal) * 0.6);
    barsHtml += `<rect x="${x}" y="${y}" width="${barWidth}" height="${h}" rx="1.5" fill="${strokeColor}" fill-opacity="${opacity.toFixed(2)}" />`;
  });

  return `
    <svg viewBox="0 0 ${width} ${height}" class="kpi-sparkline-bars" style="width:100%;height:100%;">
      ${barsHtml}
    </svg>
  `;
}

function calcolaTrendKpi(oggi, ieri, tipoMetrica) {
  // tipoMetrica: 'totale' | 'ai' | 'umano'
  if (ieri === 0 && oggi === 0) {
    return {
      testo: tipoMetrica === "umano" ? "Nessuna escalation" : "In linea con ieri",
      icona: "—",
      classe: "trend-neutral",
    };
  }
  if (ieri === 0 && oggi > 0) {
    const isGood = tipoMetrica !== "umano";
    return {
      testo: `+${oggi} rispetto a ieri`,
      icona: "↑",
      classe: isGood ? "trend-positive" : "trend-warning",
    };
  }
  const diff = oggi - ieri;
  const pct = Math.round((diff / ieri) * 100);
  if (pct === 0) {
    return {
      testo: "In linea con ieri",
      icona: "—",
      classe: "trend-neutral",
    };
  }
  if (pct > 0) {
    if (tipoMetrica === "ai") {
      return { testo: `+${pct}% rispetto a ieri`, icona: "↑", classe: "trend-positive" };
    } else if (tipoMetrica === "umano") {
      return { testo: `+${pct}% rispetto a ieri`, icona: "↑", classe: "trend-warning" };
    } else {
      return { testo: `+${pct}% rispetto a ieri`, icona: "↑", classe: "trend-info" };
    }
  } else {
    // Calo
    if (tipoMetrica === "ai") {
      return { testo: `${pct}% rispetto a ieri`, icona: "↓", classe: "trend-neutral" };
    } else if (tipoMetrica === "umano") {
      return { testo: `${pct}% rispetto a ieri`, icona: "↓", classe: "trend-positive" };
    } else {
      return { testo: `${pct}% rispetto a ieri`, icona: "↓", classe: "trend-neutral" };
    }
  }
}

function applicaBadgeTrend(elId, trendObj) {
  const el = document.getElementById(elId);
  if (!el) return;
  el.className = `kpi-trend-badge ${trendObj.classe}`;
  el.innerHTML = `
    <span class="trend-icon">${trendObj.icona}</span>
    <span class="trend-text">${_sanitize(trendObj.testo)}</span>
  `;
}

let isPanoramicaPollingActive = false;
let panoramicaPollingTimer = null;

function avviaPanoramicaPolling() {
  if (isPanoramicaPollingActive) return;
  isPanoramicaPollingActive = true;
  panoramicaPollingTimer = setInterval(async () => {
    if (!isPanoramicaPollingActive) return;
    if (document.hidden) return;
    const currentView = document.querySelector(".nav-item.active")?.dataset?.view || "panoramica";
    if (currentView !== "panoramica") {
      fermaPanoramicaPolling();
      return;
    }
    await Promise.all([
      aggiornaRiepilogo(true),
      aggiornaPrioritari(true),
    ]);
  }, 5000);
}

function fermaPanoramicaPolling() {
  isPanoramicaPollingActive = false;
  if (panoramicaPollingTimer) {
    clearInterval(panoramicaPollingTimer);
    panoramicaPollingTimer = null;
  }
}

async function aggiornaRiepilogo(silent = false) {
  const token = capture();
  if (!guard(token)) return;
  const request = ++summarySequence;
  const isCurrent = () => request === summarySequence && guard(token);
  if (!silent && !ticketList.children.length) {
    ticketList.innerHTML = _skeletonList(3);
  }
  try {
    const res = await apiFetch(`${API_BASE}/api/dashboard`);
    if (!isCurrent()) return;
    if (!res.ok) {
      if (!silent) {
        ticketList.innerHTML = "";
        ticketList.appendChild(_errorState(_tDash("overview.runtime.activity_error", "Impossibile caricare l'attività recente."), () => aggiornaRiepilogo()));
      }
      return;
    }
    const rawStorico = await res.json().catch(() => []);
    if (!isCurrent()) return;
    const storico = Array.isArray(rawStorico) ? rawStorico : [];

    // Calcolo 7 giorni storici
    const oggiDate = new Date();
    const giorni7 = [];
    for (let i = 6; i >= 0; i--) {
      const d = new Date(oggiDate);
      d.setDate(d.getDate() - i);
      const key = _toDateKey(d);
      giorni7.push({ dateKey: key, totale: 0, ai: 0, umano: 0 });
    }

    const oggiKey = _toDateKey(oggiDate);
    const eventiOggi = [];

    storico.forEach((e) => {
      if (!e.timestamp) return;
      const evDate = _toDateKey(e.timestamp);
      if (evDate === oggiKey) {
        eventiOggi.push(e);
      }
      const giornoObj = giorni7.find((g) => g.dateKey === evDate);
      if (giornoObj) {
        giornoObj.totale++;
        if (e.gestito_da_ai) giornoObj.ai++;
        else giornoObj.umano++;
      }
    });

    const totaleOggi = eventiOggi.length;
    const gestitiAiOggi = eventiOggi.filter((e) => e.gestito_da_ai).length;
    const giratiOggi = totaleOggi - gestitiAiOggi;

    if (statTotale) statTotale.textContent = totaleOggi;
    if (statAi) statAi.textContent = gestitiAiOggi;
    if (statUmano) statUmano.textContent = giratiOggi;

    // Calcolo trend e sparklines
    const totalePts = giorni7.map((g) => g.totale);
    const aiPts = giorni7.map((g) => g.ai);
    const umanoPts = giorni7.map((g) => g.umano);

    const ieriTotale = totalePts[5] || 0;
    const ieriAi = aiPts[5] || 0;
    const ieriUmano = umanoPts[5] || 0;

    // Badges
    const trendTotale = calcolaTrendKpi(totaleOggi, ieriTotale, "totale");
    const trendAi = calcolaTrendKpi(gestitiAiOggi, ieriAi, "ai");
    const trendUmano = calcolaTrendKpi(giratiOggi, ieriUmano, "umano");

    applicaBadgeTrend("trend-totale", trendTotale);
    applicaBadgeTrend("trend-ai", trendAi);
    applicaBadgeTrend("trend-umano", trendUmano);

    // Sparklines SVG
    const sparkTotaleEl = document.getElementById("sparkline-totale");
    const sparkAiEl = document.getElementById("sparkline-ai");
    const sparkUmanoEl = document.getElementById("sparkline-umano");

    if (sparkTotaleEl) {
      sparkTotaleEl.innerHTML = generaSparklineSvg(totalePts, "var(--accent)", "grad-kpi-totale");
    }
    if (sparkAiEl) {
      sparkAiEl.innerHTML = generaSparklineSvg(aiPts, "var(--sage)", "grad-kpi-ai");
    }
    if (sparkUmanoEl) {
      sparkUmanoEl.innerHTML = generaSparklineSvg(umanoPts, "var(--amber)", "grad-kpi-umano");
    }

    const totSettimana = totalePts.reduce((a, b) => a + b, 0);
    const aiSettimana = aiPts.reduce((a, b) => a + b, 0);
    const umanoSettimana = umanoPts.reduce((a, b) => a + b, 0);

    const valTotEl = document.getElementById("sparkline-totale-val");
    const valAiEl = document.getElementById("sparkline-ai-val");
    const valUmanoEl = document.getElementById("sparkline-umano-val");

    const sparklineUnit = t("dashboard:kpi.sparkline_unit");
    if (valTotEl) valTotEl.textContent = `${totSettimana} ${sparklineUnit}`;
    if (valAiEl) valAiEl.textContent = `${aiSettimana} ${sparklineUnit}`;
    if (valUmanoEl) valUmanoEl.textContent = `${umanoSettimana} ${sparklineUnit}`;

    ticketList.innerHTML = "";
    const eventiDaMostrare = eventiOggi.length > 0 ? eventiOggi.slice().reverse() : storico.slice(0, 15);

    if (eventiDaMostrare.length === 0) {
      ticketList.appendChild(_emptyState(
        ICONS.chat,
        _tDash("overview.runtime.waiting_title", "In attesa di conversazioni"),
        _tDash("overview.runtime.waiting_desc", "I messaggi dei clienti e le risposte dell'assistente compariranno qui in tempo reale."),
        _tDash("overview.runtime.try_simulator", "Prova nel simulatore"),
        () => apriView("assistente")
      ));
      return;
    }

    eventiDaMostrare.forEach((e) => {
      const li = document.createElement("li");
      li.classList.add("ticket-item", `prio-${e.priorita}`);
      const isPositive = (e.gestito_da_ai === true) || (e.tipo_evento === "recensione" && Number(e.dettagli?.stelle) >= 4);
      if (isPositive) {
        li.classList.add("item-positive");
      }
      const testoWrap = document.createElement("div");
      testoWrap.classList.add("ticket-item-text");
      const msg = document.createElement("p");
      msg.classList.add("ticket-item-msg");
      msg.textContent = e.testo_originale;
      const time = document.createElement("span");
      time.classList.add("ticket-item-time");
      const isOggi = _toDateKey(e.timestamp) === oggiKey;
      time.textContent = isOggi
        ? new Date(e.timestamp).toLocaleTimeString(localeCorrente(), { hour: "2-digit", minute: "2-digit" })
        : new Date(e.timestamp).toLocaleDateString(localeCorrente(), { day: "2-digit", month: "2-digit" }) + " " + new Date(e.timestamp).toLocaleTimeString(localeCorrente(), { hour: "2-digit", minute: "2-digit" });
      testoWrap.appendChild(msg);
      testoWrap.appendChild(time);
      const tags = document.createElement("div");
      tags.classList.add("ticket-item-tags");
      const tipoBadge = document.createElement("span");
      tipoBadge.classList.add("ticket-tag", `ticket-tag-${e.tipo_evento}`);
      tipoBadge.textContent = e.tipo_evento === "recensione"
        ? _tDash("overview.runtime.review_kind", "Recensione")
        : _tDash("overview.runtime.message_kind", "Messaggio");
      tags.appendChild(tipoBadge);
      if (e.tipo_evento === "recensione" && e.dettagli?.stelle) {
        const stelleTag = document.createElement("span");
        stelleTag.classList.add("ticket-tag", "ticket-tag-stelle");
        if (Number(e.dettagli.stelle) >= 4) {
          stelleTag.classList.add("tag-positive");
        }
        stelleTag.textContent = "\u2605".repeat(e.dettagli.stelle) + "\u2606".repeat(5 - e.dettagli.stelle);
        tags.appendChild(stelleTag);
      }
      if (e.tipo_evento === "recensione") {
        const viewBtn = document.createElement("button");
        viewBtn.classList.add("ticket-copy-btn");
        viewBtn.textContent = _tDash("overview.runtime.open_review", "Visualizza");
        viewBtn.addEventListener("click", () => {
          apriView("recensioni");
          const transitionContext = getContext();
          const timer = root.setTimeout(() => {
            pendingTimers.delete(timer);
            if (sameContext(transitionContext) && transitionContext.view === "recensioni" && e.id) {
              getReviewsApi()?.apriDettaglioRecensione(e.id);
            }
          }, 80);
          pendingTimers.add(timer);
        });
        tags.appendChild(viewBtn);
      } else {
        const statusTag = document.createElement("span");
        statusTag.classList.add("ticket-tag");
        statusTag.classList.add(e.gestito_da_ai ? "ticket-tag-ai" : "ticket-tag-umano");
        statusTag.textContent = e.gestito_da_ai ? "Assistente" : "Umano";
        tags.appendChild(statusTag);
      }
      li.appendChild(testoWrap);
      li.appendChild(tags);
      ticketList.appendChild(li);
    });
  } catch (err) {
    if (!isCurrent()) return;
    console.error("Impossibile aggiornare il riepilogo:", err);
    if (!silent) {
      ticketList.innerHTML = "";
      ticketList.appendChild(_errorState("Impossibile caricare l'attività recente.", () => aggiornaRiepilogo()));
    }
  }
}



      return Object.freeze({
        onEnter() {
          const scope = scopeKey();
          if (entryScope !== null && scope !== entryScope) { clearWork(); clearScopeData(); }
          entryScope = scope;
          active = true;
        },
        onExit() { active = false; clearWork(); },
        invalidate() { active = false; destroyed = true; clearWork(); clearScopeData(); },
        aggiornaRiepilogo, aggiornaPrioritari, avviaPanoramicaPolling, fermaPanoramicaPolling,
      });
    },
  });
});
