(function (root) {
  "use strict";
  const sectors = ["ristorante", "parrucchiere", "centro_estetico", "hotel_bnb", "studio_medico_dentista"];
  const types = { customer_name: "text", contact_phone: "phone", contact_email: "email", appointment_date: "date", appointment_time: "time", party_size: "positive_integer", service_id: "reference", resource_id: "reference", arrival_date: "date", departure_date: "date", guest_count: "positive_integer", room_count: "positive_integer" };
  const labels = { customer_name: "Nome cliente", contact_phone: "Telefono", contact_email: "Email", appointment_date: "Data appuntamento", appointment_time: "Ora appuntamento", party_size: "Numero persone", service_id: "Servizio", resource_id: "Risorsa", arrival_date: "Data arrivo", departure_date: "Data partenza", guest_count: "Numero ospiti", room_count: "Numero camere" };
  const sectorNames = { ristorante: "Ristorante", parrucchiere: "Parrucchiere", centro_estetico: "Centro estetico", hotel_bnb: "Hotel / B&B", studio_medico_dentista: "Studio medico / dentista" };
  const kinds = ["operator", "space", "table", "seat_pool", "room", "equipment"];
  const kindNames = { operator: "Operatore", space: "Spazio", table: "Tavolo", seat_pool: "Posti", room: "Camera", equipment: "Attrezzatura" };
  const weekdays = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"];
  const clone = (x) => JSON.parse(JSON.stringify(x));
  function allowed(sector) {
    return ["customer_name", "contact_phone", "contact_email", ...(sector === "hotel_bnb" ? ["arrival_date", "departure_date", "guest_count", "room_count"] : ["appointment_date", "appointment_time", ...(sector === "ristorante" ? ["party_size"] : [])]), "service_id", "resource_id"];
  }
  function required(sector) { return sector === "hotel_bnb" ? ["customer_name", "arrival_date", "departure_date"] : ["customer_name", "appointment_date", "appointment_time"]; }
  function fields(sector) { return required(sector).map((key) => ({ key, value_type: types[key], required: true })); }
  function example(sector, timezone) {
    if (!sectors.includes(sector)) throw new Error("Settore non supportato");
    const kind = { ristorante: "seat_pool", parrucchiere: "operator", centro_estetico: "space", hotel_bnb: "room", studio_medico_dentista: "operator" }[sector];
    return { version: 2, revision: 1, sector, timezone: timezone || "Europe/Rome", availability: { days: Array.from({ length: 7 }, (_, weekday) => ({ weekday, windows: weekday === 6 ? [] : [{ start_minute: 540, end_minute: 1080 }] })), closed_dates: ["2027-12-25"] }, resources: [{ resource_id: "primary", kind, capacity: sector === "ristorante" ? 12 : 1, exclusive: sector !== "ristorante" }], services: [{ service_id: "standard", name: "Standard", duration: sector === "hotel_bnb" ? { kind: "civil_nights", minimum_nights: 1, check_in: "14:00", check_out: "14:00" } : { kind: "minutes", minutes: sector === "ristorante" ? 60 : 45, buffer_before: 0, buffer_after: 5 }, resources: [{ resource_id: "primary", quantity: 1 }], fields: sector === "ristorante" ? [...fields(sector), { key: "party_size", value_type: "positive_integer", required: true }] : fields(sector), handoff: { staff_review_required: false, party_size_above: null } }] };
  }
  function create(container, options) {
    const doc = container.ownerDocument;
    const opts = options || {};
    const translate = typeof opts.translate === "function" ? opts.translate : (s) => s;
    let model = null;
    let editable = Boolean(opts.canEdit);
    const changed = () => { if (typeof opts.onChange === "function") opts.onChange(); };
    function el(tag, text, parent) { const n = doc.createElement(tag); if (text != null) n.textContent = translate(text); if (parent) parent.appendChild(n); return n; }
    function button(parent, text, fn) { const b = el("button", text, parent); b.type = "button"; b.className = "booking-icon-text-button"; b.disabled = !editable; b.addEventListener("click", () => { if (!editable) return; fn(); changed(); render(); }); return b; }
    function control(parent, label, value, type, update, choices) {
      const wrap = el("label", null, parent); el("span", label, wrap);
      const input = el(choices ? "select" : "input", null, wrap);
      if (choices) choices.forEach(([v, name, raw]) => { const o = el("option", raw ? null : name, input); if (raw) o.textContent = name; o.value = v; });
      else input.type = type;
      if (type === "checkbox") input.checked = Boolean(value); else input.value = value == null ? "" : String(value);
      if (type === "number") { input.step = "1"; input.min = "0"; }
      input.disabled = !editable;
      input.addEventListener("change", () => { if (!editable) return; update(type === "checkbox" ? input.checked : type === "number" ? (input.value === "" ? null : Number(input.value)) : input.value); changed(); });
      return input;
    }
    function group(parent, title) { const f = el("fieldset", null, parent); el("legend", title, f); return f; }
    function render() {
      container.replaceChildren();
      if (!model) {
        el("p", "Nessun catalogo. Scegli un esempio sintetico e verifica tutti i valori prima di salvarlo.", container);
        sectors.forEach((sector) => button(container, `${translate("Usa esempio")}: ${translate(sectorNames[sector])}`, () => { model = example(sector, opts.timezone); }));
        return;
      }
      el("p", `${translate("Settore")}: ${sectorNames[model.sector] ? translate(sectorNames[model.sector]) : model.sector}`, container);
      control(container, "Fuso orario IANA", model.timezone, "text", (v) => { model.timezone = v; });
      const resources = group(container, "Risorse");
      model.resources.forEach((r, index) => {
        const box = group(resources, `${translate("Risorsa")} ${index + 1}`);
        control(box, "Identificativo", r.resource_id, "text", (v) => { r.resource_id = v; });
        control(box, "Tipo", r.kind, "text", (v) => { r.kind = v; }, kinds.map((k) => [k, kindNames[k]]));
        control(box, "Capacità", r.capacity, "number", (v) => { r.capacity = v; });
        control(box, "Esclusiva (capacità 1)", r.exclusive, "checkbox", (v) => { r.exclusive = v; });
        button(box, "Rimuovi risorsa", () => { model.resources.splice(index, 1); });
      });
      button(resources, "Aggiungi risorsa", () => { model.resources.push({ resource_id: "", kind: "operator", capacity: 1, exclusive: true }); });
      const services = group(container, "Servizi");
      model.services.forEach((s, index) => {
        const box = group(services, `${translate("Servizio")} ${index + 1}`);
        control(box, "Identificativo servizio", s.service_id, "text", (v) => { s.service_id = v; });
        control(box, "Nome servizio", s.name, "text", (v) => { s.name = v; });
        if (s.duration.kind === "civil_nights") {
          control(box, "Notti minime", s.duration.minimum_nights, "number", (v) => { s.duration.minimum_nights = v; });
          control(box, "Check-in", s.duration.check_in, "time", (v) => { s.duration.check_in = v; });
          control(box, "Check-out", s.duration.check_out, "time", (v) => { s.duration.check_out = v; });
        } else {
          [["minutes", "Durata in minuti"], ["buffer_before", "Intervallo prima (minuti)"], ["buffer_after", "Intervallo dopo (minuti)"]].forEach(([key, label]) => control(box, label, s.duration[key], "number", (v) => { s.duration[key] = v; }));
        }
        const refs = group(box, "Risorse richieste");
        s.resources.forEach((r, i) => {
          const row = el("div", null, refs);
          const choices = [["", "Seleziona una risorsa"], ...model.resources.map((resource) => [resource.resource_id, resource.resource_id, true])];
          if (r.resource_id && !choices.some(([id]) => id === r.resource_id)) choices.push([r.resource_id, r.resource_id, true]);
          control(row, "Risorsa", r.resource_id, "text", (v) => { r.resource_id = v; }, choices);
          control(row, "Quantità", r.quantity, "number", (v) => { r.quantity = v; });
          button(row, "Rimuovi requisito", () => { s.resources.splice(i, 1); });
        });
        button(refs, "Aggiungi requisito", () => { s.resources.push({ resource_id: "", quantity: 1 }); });
        const fieldset = group(box, "Informazioni amministrative in ordine di raccolta");
        s.fields.forEach((f, i) => {
          const row = el("div", null, fieldset); el("span", labels[f.key] || f.key, row);
          const mandatory = required(model.sector).includes(f.key);
          const check = control(row, "Obbligatorio", f.required, "checkbox", (v) => { f.required = v; });
          if (mandatory) check.disabled = true;
          if (i > 0) button(row, "Sposta su", () => { [s.fields[i - 1], s.fields[i]] = [s.fields[i], s.fields[i - 1]]; });
          if (i < s.fields.length - 1) button(row, "Sposta giù", () => { [s.fields[i + 1], s.fields[i]] = [s.fields[i], s.fields[i + 1]]; });
          if (!mandatory) button(row, "Rimuovi campo", () => { s.fields.splice(i, 1); });
        });
        const remaining = allowed(model.sector).filter((key) => !s.fields.some((f) => f.key === key));
        if (remaining.length) {
          let selected = remaining[0];
          control(fieldset, "Campo da aggiungere", selected, "text", (v) => { selected = v; }, remaining.map((k) => [k, labels[k]]));
          button(fieldset, "Aggiungi campo", () => { s.fields.push({ key: selected, value_type: types[selected], required: false }); });
        }
        const handoff = group(box, "Ulteriore intervento umano");
        s.handoff = s.handoff || { staff_review_required: false, party_size_above: null };
        control(handoff, "Richiedi revisione del personale", s.handoff.staff_review_required, "checkbox", (v) => { s.handoff.staff_review_required = v; });
        control(handoff, "Intervento sopra il numero di persone (facoltativo)", s.handoff.party_size_above, "number", (v) => { s.handoff.party_size_above = v; });
        button(box, "Rimuovi servizio", () => { model.services.splice(index, 1); });
      });
      button(services, "Aggiungi servizio", () => { model.services.push({ service_id: "", name: "", duration: model.sector === "hotel_bnb" ? { kind: "civil_nights", minimum_nights: 1, check_in: "", check_out: "" } : { kind: "minutes", minutes: 1, buffer_before: 0, buffer_after: 0 }, resources: [], fields: fields(model.sector), handoff: { staff_review_required: false, party_size_above: null } }); });
      const availability = group(container, "Disponibilità settimanale");
      weekdays.forEach((name, weekday) => {
        const box = group(availability, name);
        let day = model.availability.days.find((d) => d.weekday === weekday);
        const mode = !day ? "unknown" : day.windows.length ? "open" : "closed";
        const select = control(box, "Stato", mode, "text", (v) => {
          model.availability.days = model.availability.days.filter((d) => d.weekday !== weekday);
          if (v !== "unknown") model.availability.days.push({ weekday, windows: v === "open" ? [{ start_minute: 0, end_minute: 1 }] : [] });
          render();
        }, [["unknown", "Non definito"], ["closed", "Chiuso"], ["open", "Aperto"]]);
        select.setAttribute("aria-label", `${translate(name)}: ${translate("Stato")}`);
        if (!day || !day.windows.length) return;
        day.windows.forEach((w, i) => {
          const row = el("div", null, box);
          const start = control(row, "Inizio (minuto 0–1439)", w.start_minute, "number", (v) => { w.start_minute = v; }); start.max = "1439";
          const end = control(row, "Fine (minuto 1–1440)", w.end_minute, "number", (v) => { w.end_minute = v; }); end.max = "1440";
          button(row, "Rimuovi fascia", () => { day.windows.splice(i, 1); });
        });
        button(box, "Aggiungi fascia", () => { day.windows.push({ start_minute: 0, end_minute: 1 }); });
      });
      const closures = group(container, "Date di chiusura");
      model.availability.closed_dates.forEach((date, i) => { const row = el("div", null, closures); control(row, "Data", date, "date", (v) => { model.availability.closed_dates[i] = v; }); button(row, "Rimuovi data", () => { model.availability.closed_dates.splice(i, 1); }); });
      button(closures, "Aggiungi data", () => { model.availability.closed_dates.push(""); });
    }
    function load(catalog) { model = catalog == null ? null : clone(catalog); render(); }
    function read(revision) { if (!model) throw new Error("Seleziona o carica un catalogo"); const result = clone(model); result.revision = revision; return result; }
    function clear() { model = null; container.replaceChildren(); }
    function setEditable(value) { editable = Boolean(value); render(); }
    render();
    return Object.freeze({ load, read, clear, setEditable });
  }
  root.MelpisBookingCatalogForm = Object.freeze({ create, example });
})(typeof window !== "undefined" ? window : globalThis);
