const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { generate, normalizeCssEol } = require("../../scripts/externalize-fullcalendar-styles.js");

test("FullCalendar CSS check ignores only Windows line-ending conversion", () => {
  const root = path.resolve(__dirname, "../..");
  const source = fs.readFileSync(path.join(root, "web/vendor/fullcalendar-source.min.js"), "utf8");
  const actual = fs.readFileSync(path.join(root, "web/vendor/fullcalendar.css"), "utf8");
  const expected = generate(source).css;
  assert.equal(normalizeCssEol(actual), normalizeCssEol(expected));
  assert.notEqual(normalizeCssEol(actual), normalizeCssEol(expected.replace("--fc-small-font-size", "--fc-modified-font-size")));
});
