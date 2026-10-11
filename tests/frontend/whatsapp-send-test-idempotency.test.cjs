const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const integrations = fs.readFileSync(path.resolve(__dirname, "../../web/dashboard-integrations.js"), "utf8").replace(/\r\n/g, "\n");

function functionSource(name) {
  const start = integrations.search(new RegExp(`function ${name}\\(`));
  assert.notEqual(start, -1, `missing ${name}`);
  const end = integrations.indexOf("\n}", start);
  assert.notEqual(end, -1, `missing ${name} end`);
  return integrations.slice(start, end + 2);
}

test("WhatsApp send-test retries reuse one recipient key until confirmed delivery", (t) => {
  const dom = new JSDOM("<!doctype html><html><body></body></html>", { runScripts: "outside-only" });
  t.after(() => dom.window.close());
  let sequence = 0;
  Object.defineProperty(dom.window.crypto, "randomUUID", { value: () => `uuid-${++sequence}` });

  dom.window.eval(`
    const whatsappTestIdempotencyKeys = new Map();
    ${functionSource("_getWhatsAppTestIdempotencyKey")}
    ${functionSource("_completeWhatsAppTestIntent")}
  `);

  const first = dom.window.eval('_getWhatsAppTestIdempotencyKey("+39 333-123-4567")');
  const retry = dom.window.eval('_getWhatsAppTestIdempotencyKey("+39 (333) 123 4567")');
  assert.equal(first, "uuid-1");
  assert.equal(retry, first);

  dom.window.eval(`_completeWhatsAppTestIntent("+39 333-123-4567", "${first}", false)`);
  assert.equal(dom.window.eval('_getWhatsAppTestIdempotencyKey("+39 333-123-4567")'), first);

  dom.window.eval(`_completeWhatsAppTestIntent("+39 333-123-4567", "${first}", true)`);
  assert.equal(dom.window.eval('_getWhatsAppTestIdempotencyKey("+39 333-123-4567")'), "uuid-2");
});

test("both WhatsApp test-send handlers send their stable key when a recipient is supplied", () => {
  const whatsapp = integrations.split("// WhatsApp Wizard Event Listeners", 2)[1].split("// Instagram", 1)[0];
  assert.equal((whatsapp.match(/const idempotencyKey = _getWhatsAppTestIdempotencyKey\(/g) || []).length, 2);
  assert.equal((whatsapp.match(/idempotency_key:\s*idempotencyKey/g) || []).length, 2);
  assert.equal((whatsapp.match(/_completeWhatsAppTestIntent\([^\n]+true\)/g) || []).length, 2);
});
