const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { createDashboardHarness } = require("./helpers/dashboard-harness.cjs");

const html = fs.readFileSync(path.resolve(__dirname, "../../web/index.html"), "utf8");

for (const configured of [false, true]) {
  test(`saved credentials do not certify WhatsApp reception (webhook configured=${configured})`, async (t) => {
    const harness = createDashboardHarness({
      url: "https://melpis.test/app/settings?tab=whatsapp",
      overrides: {
        "/api/whatsapp/settings": (_request, jsonResponse) => jsonResponse({
          connesso: true, webhook_active: configured, phone_number_id: "123",
        }),
      },
    });
    t.after(harness.dispose);
    await harness.ready();
    await harness.settle();
    assert.match(harness.document.getElementById("integ-wa-connected-sub").textContent, /Credenziali collegate/);
    const reception = harness.document.getElementById("integ-wa-reception-status").textContent;
    assert.match(reception, configured ? /Da verificare/ : /Non attiva/);
    assert.doesNotMatch(harness.document.body.textContent, /pronto a rispondere|Attiva in tempo reale/);
  });
}

test("initial WhatsApp markup does not claim unverified operation", () => {
  const card = html.slice(html.indexOf('id="integ-whatsapp-connected-card"'), html.indexOf('id="integ-whatsapp-wizard-card"'));
  assert.doesNotMatch(card, /Connesso e pronto a rispondere|Attiva in tempo reale/);
});
