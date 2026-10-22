const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const test = require("node:test");
const { JSDOM } = require("jsdom");

const root = path.resolve(__dirname, "../..");
const web = path.join(root, "web");
const origin = "http://localhost";

function scripts(html) {
  const document = new JSDOM(html).window.document;
  return [...document.querySelectorAll("script[src]")]
    .map((script) => new URL(script.getAttribute("src"), `${origin}/app/`))
    .filter((url) => url.origin === origin)
    .map((url) => url.pathname + url.search);
}

// Resolve COPY destinations rather than merely searching for basenames: a
// script copied to the landing root cannot satisfy a dashboard reference.
function packagedScripts(dockerfile) {
  const result = new Map();
  for (const line of dockerfile.replace(/\\\r?\n/g, " ").split(/\r?\n/)) {
    if (!/^COPY\s/i.test(line) || /--from=/.test(line)) continue;
    const tokens = line.trim().split(/\s+/).slice(1);
    assert.ok(!tokens.some((token) => token.startsWith("--")), "unsupported COPY option");
    const destination = tokens.pop();
    const prefix = "/usr/share/nginx/html/";
    if (!destination.startsWith(prefix)) continue;
    for (const source of tokens) {
      const absolute = path.join(web, source);
      // Globs in this Dockerfile currently cover images only. Do not silently
      // treat a future JavaScript glob as proof that its files were packaged.
      if (/[*?[]/.test(source)) {
        assert.ok(!/\.m?js$/.test(source), "resolve JavaScript COPY globs explicitly");
        continue;
      }
      const publicPath = "/" + destination.slice(prefix.length);
      if (fs.statSync(absolute).isDirectory()) {
        for (const file of fs.readdirSync(absolute, { recursive: true })) {
          if (fs.statSync(path.join(absolute, file)).isFile()) {
            result.set(path.posix.join(publicPath, file.replaceAll("\\", "/")), path.join(absolute, file));
          }
        }
      } else {
        result.set(destination.endsWith("/") ? path.posix.join(publicPath, path.basename(source)) : publicPath, absolute);
      }
    }
  }
  return result;
}

test("every generated dashboard script exists and is packaged at its public URL", async () => {
  const { composeDashboard } = await import(pathToFileURL(path.join(web, "scripts/compose-dashboard.mjs")));
  const html = composeDashboard().output.toString();
  const packaged = packagedScripts(fs.readFileSync(path.join(web, "Dockerfile"), "utf8"));
  const required = scripts(html);
  assert.ok(required.length > 10, "dashboard script inventory must not be empty");
  for (const reference of required) {
    const pathname = new URL(reference, origin).pathname;
    // config.js is generated from this template by envsubst at image startup.
    if (pathname === "/config.js") {
      assert.ok(packaged.has("/config.template.js"));
      assert.match(fs.readFileSync(path.join(web, "Dockerfile"), "utf8"), />\s*\/usr\/share\/nginx\/html\/config\.js/);
      continue;
    }
    assert.ok(packaged.has(pathname), `missing Docker asset: ${pathname}`);
    assert.ok(fs.statSync(packaged.get(pathname)).isFile(), `missing source: ${pathname}`);
  }
});

test("packaging resolver handles directory copies and rejects wrong destination", () => {
  const packaged = packagedScripts("COPY vendor/ /usr/share/nginx/html/app/vendor/\nCOPY app.js /usr/share/nginx/html/");
  assert.ok(packaged.has("/app/vendor/fullcalendar.min.js"));
  assert.ok(!packaged.has("/app/app.js"));
});

test("the regression detects each omitted module and an unknown HTML asset", async () => {
  const { composeDashboard } = await import(pathToFileURL(path.join(web, "scripts/compose-dashboard.mjs")));
  const required = scripts(composeDashboard().output.toString()).map((reference) => new URL(reference, origin).pathname);
  const dockerfile = fs.readFileSync(path.join(web, "Dockerfile"), "utf8");
  for (const name of ["dashboard-auth-transport.js", "dashboard-ai-settings.js", "dashboard-settings.js", "dashboard-integrations.js"]) {
    assert.ok(required.includes(`/app/${name}`));
    const omitted = packagedScripts(dockerfile.replace(`${name} `, ""));
    assert.ok(!omitted.has(`/app/${name}`), `omission must be detected: ${name}`);
  }
  const unknown = scripts('<script src="/app/nonexistent-module.js?v=1"></script>');
  assert.ok(!packagedScripts(dockerfile).has(new URL(unknown[0], origin).pathname));
});

// Opt-in against a built image connected to a real test backend. Supply an
// already-authorized session; never create a fake cookie or mock auth routes.
test("built image serves all dashboard scripts as JavaScript with a legitimate session", {
  skip: !process.env.MELPIS_WEB_SMOKE_URL || !process.env.MELPIS_WEB_SMOKE_COOKIE,
}, async () => {
  const base = new URL(process.env.MELPIS_WEB_SMOKE_URL);
  assert.ok(["localhost", "127.0.0.1", "[::1]"].includes(base.hostname), "smoke is local only");
  const headers = { Cookie: process.env.MELPIS_WEB_SMOKE_COOKIE };
  const me = await fetch(new URL("/api/auth/me", base), { headers, redirect: "manual" });
  assert.equal(me.status, 200, "existing session must be authorized by the backend");
  const identity = await me.json();
  assert.ok(identity.user_id && identity.organization_id, "session must have user and organization scope");
  const page = await fetch(new URL("/app/", base), { headers, redirect: "manual" });
  assert.equal(page.status, 200);
  assert.match(page.headers.get("content-type"), /text\/html/i);
  const html = await page.text();
  assert.ok(new JSDOM(html).window.document.querySelector(".app-shell"), "response must be the dashboard");
  for (const reference of scripts(html)) {
    const response = await fetch(new URL(reference, base), { headers, redirect: "manual" });
    assert.equal(response.status, 200, `asset status: ${reference}`);
    assert.match(response.headers.get("content-type"), /^(?:application|text)\/(?:x-)?javascript\b/i, `asset MIME: ${reference}`);
    assert.doesNotMatch(await response.text(), /^\s*(?:<!doctype\s+html|<html)/i, `HTML fallback: ${reference}`);
  }
});

test("dashboard boots visibly without JavaScript errors using existing browser storage", {
  skip: !process.env.MELPIS_WEB_SMOKE_URL || !process.env.MELPIS_WEB_SMOKE_STORAGE_STATE,
}, async () => {
  // Playwright is an external smoke-runner dependency, not an application
  // dependency. The state must come from a previously authorized manual login.
  const { chromium } = require(process.env.MELPIS_PLAYWRIGHT_MODULE || "playwright");
  const base = new URL(process.env.MELPIS_WEB_SMOKE_URL);
  assert.ok(["localhost", "127.0.0.1", "[::1]"].includes(base.hostname), "smoke is local only");
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({ storageState: process.env.MELPIS_WEB_SMOKE_STORAGE_STATE });
    const me = await context.request.get(new URL("/api/auth/me", base).href);
    assert.equal(me.status(), 200, "existing session must be authorized by the backend");
    const identity = await me.json();
    assert.ok(identity.user_id && identity.organization_id);
    const page = await context.newPage();
    let pageErrors = 0;
    page.on("pageerror", () => { pageErrors += 1; });
    const response = await page.goto(new URL("/app/", base).href);
    assert.equal(response.status(), 200);
    await page.waitForFunction(() => {
      const shell = document.querySelector(".app-shell");
      return document.body.classList.contains("authenticated") && shell &&
        getComputedStyle(shell).visibility === "visible" && Number(getComputedStyle(shell).opacity) > 0;
    });
    assert.equal(await page.locator(".sidebar").isVisible(), true);
    assert.equal(pageErrors, 0, "dashboard startup raised JavaScript exceptions");
  } finally {
    await browser.close();
  }
});
