const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { JSDOM } = require("jsdom");

test("pages have unique IDs", () => {
  for (const path of ["web/index.html", "web/login.html", "web/register.html", "web/landing/index.html"]) {
    const dom = new JSDOM(fs.readFileSync(path, "utf8"));
    const ids = [...dom.window.document.querySelectorAll("[id]")].map(el => el.id);
    assert.equal(new Set(ids).size, ids.length, path);
    dom.window.close();
  }
});

test("static pages contain no inline style attributes", () => {
  const files = ["web/index.html", "web/login.html", "web/register.html",
    "web/landing/index.html", "web/landing/privacy.html", "web/landing/termini.html",
    "web/landing/cookie.html"];
  for (const path of files) {
    assert.doesNotMatch(fs.readFileSync(path, "utf8"), /\sstyle=/i, path);
  }
});

test("assigned name is rendered as text, not executable markup", () => {
  const source = fs.readFileSync("web/dashboard-inbox.js", "utf8");
  const fragment = source.match(/statusPill\.innerHTML = statusIcon;[\s\S]*?statusPill\.append\(" ", statusLabel\);/);
  assert.ok(fragment, "safe status rendering must exist");
  const dom = new JSDOM("<div id='pill'></div>");
  const statusPill = dom.window.document.getElementById("pill");
  vm.runInNewContext(fragment[0], {
    statusPill, document: dom.window.document, statusIcon: "",
    statusText: '<img src=x onerror="alert(1)">',
  });
  assert.equal(statusPill.querySelector("img"), null);
  assert.match(statusPill.textContent, /<img/);

  const detailStatusFragment = source.match(/const assignedLabelEl = document\.createElement\("span"\);\s*assignedLabelEl\.textContent = assignedLabel;\s*aiBadge\.appendChild\(assignedLabelEl\);/);
  assert.ok(detailStatusFragment, "detail status must append the assigned name as text");
  const aiBadge = dom.window.document.createElement("div");
  vm.runInNewContext(detailStatusFragment[0], {
    aiBadge,
    document: dom.window.document,
    assignedLabel: '<img src=x onerror="alert(1)">',
  });
  assert.equal(aiBadge.querySelector("img"), null);
  assert.match(aiBadge.textContent, /<img/);
  dom.window.close();
});
