const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");
const source = fs.readFileSync(path.join(__dirname, "../../web/dashboard-booking-catalog-form.js"), "utf8");
const fixtures = require("../core/bookings/fixtures/catalogs_v2.json");
function setup(options = {}) {
  const dom = new JSDOM("<main></main>", { runScripts: "outside-only" });
  dom.window.eval(source);
  const container = dom.window.document.querySelector("main");
  const form = dom.window.MelpisBookingCatalogForm.create(container, { canEdit: true, ...options });
  return { dom, container, form };
}
function button(container, text) { return [...container.querySelectorAll("button")].find((b) => b.textContent === text); }
function field(container, label) { return [...container.querySelectorAll("label")].find((n) => n.querySelector("span")?.textContent === label)?.querySelector("input,select"); }
function change(dom, input, value) { input.value = value; input.dispatchEvent(new dom.window.Event("change", { bubbles: true })); }
function plain(value) { return JSON.parse(JSON.stringify(value)); }

test("all five examples require explicit selection and preserve the contract fixture shape", () => {
  for (const [sector, fixture] of Object.entries(fixtures)) {
    const { container, form, dom } = setup();
    assert.match(container.textContent, /esempio sintetico/i);
    assert.throws(() => form.read(1));
    const example = dom.window.MelpisBookingCatalogForm.example(sector, fixture.timezone);
    delete example.services[0].handoff;
    assert.deepEqual(plain(example), fixture);
    const buttons = [...container.querySelectorAll("button")];
    assert.equal(buttons.length, 5);
    const selected = buttons.find((b) => b.textContent.includes(({ ristorante: "Ristorante", parrucchiere: "Parrucchiere", centro_estetico: "Centro estetico", hotel_bnb: "Hotel / B&B", studio_medico_dentista: "Studio medico / dentista" })[sector]));
    selected.click();
    assert.equal(form.read(3).sector, sector);
    assert.equal(form.read(3).revision, 3);
    dom.window.close();
  }
});

test("load and read preserve unknown versus closed days, inputs, and fixed fields", () => {
  const { container, form, dom } = setup();
  const fixture = structuredClone(fixtures.parrucchiere);
  fixture.availability.days = [{ weekday: 0, windows: [] }, { weekday: 1, windows: [{ start_minute: 0, end_minute: 1440 }] }];
  form.load(fixture);
  assert.deepEqual(plain(form.read(9).availability), fixture.availability);
  change(dom, field(container, "Nome servizio"), "<img src=x onerror=alert(1)>");
  assert.equal(container.querySelector("img"), null);
  assert.equal(form.read(9).services[0].name, "<img src=x onerror=alert(1)>");
  const monday = container.querySelector('select[aria-label="Lunedì: Stato"]');
  change(dom, monday, "unknown");
  assert.equal(form.read(9).availability.days.some((d) => d.weekday === 0), false);
  change(dom, container.querySelector('select[aria-label="Lunedì: Stato"]'), "closed");
  assert.deepEqual(plain(form.read(9).availability.days.find((d) => d.weekday === 0).windows), []);
  assert.equal(container.textContent.includes("Campo clinico"), false);
  dom.window.close();
});

test("guided dynamic controls add resources, services, fields, windows and closures", () => {
  const { container, form, dom } = setup(); form.load(fixtures.ristorante);
  button(container, "Aggiungi risorsa").click();
  button(container, "Aggiungi servizio").click();
  button(container, "Aggiungi data").click();
  button(container, "Aggiungi fascia").click();
  button(container, "Aggiungi campo").click();
  const result = form.read(4);
  assert.equal(result.resources.length, 2);
  assert.equal(result.services.length, 2);
  assert.equal(result.availability.closed_dates.length, 2);
  assert.equal(result.availability.days[0].windows.length, 2);
  assert.equal(result.services[0].fields[4].value_type, "phone");
  dom.window.close();
});

test("read-only state disables controls, clear removes private data and load copies input", () => {
  const { container, form, dom } = setup(); const fixture = structuredClone(fixtures.hotel_bnb);
  form.load(fixture); fixture.services[0].name = "changed outside";
  assert.equal(form.read(2).services[0].name, "Standard");
  form.setEditable(false);
  assert.ok([...container.querySelectorAll("input, select, button")].every((n) => n.disabled));
  button(container, "Aggiungi servizio").click();
  assert.equal(form.read(2).services.length, 1);
  form.clear(); assert.equal(container.textContent, ""); assert.throws(() => form.read(2));
  dom.window.close();
});

test("translated fixed field labels remain associated with their controls", () => {
  for (const [lang, expected] of [["en", "Customer name"], ["de", "Kundenname"]]) {
    const messages = require(`../../locales/${lang}/dashboard.json`).booking_catalog;
    const keys = { "Nome cliente": "customer_name", "Telefono": "phone", "Email": "email" };
    const { container, form, dom } = setup({ translate: (text) => messages[keys[text]] || text });
    form.load(fixtures.parrucchiere);
    assert.match(container.textContent, new RegExp(expected));
    assert.ok([...container.querySelectorAll("input, select")].every((input) => input.closest("label")));
    dom.window.close();
  }
});
