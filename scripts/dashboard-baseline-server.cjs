// QA-only loopback server. Real dashboard assets + synthetic API, no credentials/DB.
const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
const { reply, FIXED_TIME } = require("../tests/frontend/fixtures/dashboard-visual-api.cjs");
const { SCENARIOS, viewReply } = require("../tests/frontend/fixtures/dashboard-views-qa.cjs");
const { safeRequests: inboxRequests } = require("../tests/frontend/fixtures/dashboard-inbox-qa.cjs");
const defaultRoot = path.resolve(__dirname, "..", "web");
const interactions = process.env.DASHBOARD_QA_INTERACTIONS === "1";
const viewStates = interactions && process.env.DASHBOARD_QA_VIEW_STATES === "1";
function resolveAssetRoot(override, enabled, fallback = defaultRoot) {
  if (!override || !enabled) return fallback;
  if (!path.isAbsolute(override) || override.startsWith("\\\\") || override.startsWith("//")) {
    throw new Error("DASHBOARD_QA_ASSET_ROOT must be an absolute local directory path");
  }
  const resolved = path.resolve(override);
  let stat;
  try { stat = fs.statSync(resolved); } catch { throw new Error("DASHBOARD_QA_ASSET_ROOT must name an existing local directory"); }
  if (!stat.isDirectory()) throw new Error("DASHBOARD_QA_ASSET_ROOT must name an existing local directory");
  return resolved;
}
const root = resolveAssetRoot(process.env.DASHBOARD_QA_ASSET_ROOT, interactions && viewStates);
const port = Number(process.env.DASHBOARD_QA_PORT || 4190);
const csp = fs.readFileSync(path.join(root, "security-headers.conf"), "utf8").match(/Content-Security-Policy "([^"]+)"/)[1];
const requests = [];
// Explicit opt-in for synthetic interaction smoke; baseline mode stays identical.
function interactionReply(method, pathname, cookie = "", enabled = interactions) {
  if (!enabled) return reply(method, pathname);
  if (/(?:^|;\s*)dashboard_qa_signed_out=1(?:;|$)/.test(cookie)) {
    return { status: 401, body: { detail: "Sessione QA terminata" } };
  }
  if (method === "POST" && pathname === "/api/auth/logout") {
    return { status: 200, body: { ok: true }, signedOut: true };
  }
  if (method === "GET" && pathname === "/api/documenti/elenco") {
    return { status: 200, body: { documenti: [{ id: "synthetic-document-only", nome: "Documento QA sintetico", tipo: "documento", stato: "pronto", is_active: true, chunk: 1, caricato_il: FIXED_TIME }] } };
  }
  return reply(method, pathname);
}
const types = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".json": "application/json", ".svg": "image/svg+xml", ".woff2": "font/woff2", ".png": "image/png", ".webp": "image/webp", ".webmanifest": "application/manifest+json" };
const server = http.createServer((req, res) => {
  if (![`127.0.0.1:${port}`, `localhost:${port}`].includes(req.headers.host)) { res.writeHead(403).end(); return; }
  res.setHeader("Content-Security-Policy", csp);
  res.setHeader("X-Content-Type-Options", "nosniff");
  res.setHeader("Referrer-Policy", "no-referrer");
  res.setHeader("Cache-Control", "no-store");
  const url = new URL(req.url, `http://127.0.0.1:${port}`);
  if (url.pathname.startsWith("/api/")) {
    const cookie = req.headers.cookie || "";
    const scenario = cookie.match(/(?:^|;\s*)dashboard_qa_case=([a-z-]+)(?:;|$)/)?.[1];
    const loggedOut = /(?:^|;\s*)dashboard_qa_signed_out=1(?:;|$)/.test(cookie);
    const isInboxRoute = url.pathname.startsWith("/api/inbox/");
    const finishApiRequest = (rawBody = null) => {
    const result = !loggedOut && viewStates && viewReply(req.method, url.pathname + url.search, scenario, req.headers["x-organization-id"] || undefined, isInboxRoute ? rawBody : null)
      || interactionReply(req.method, url.pathname, cookie);
    if (result.signedOut) res.setHeader("Set-Cookie", "dashboard_qa_signed_out=1; Path=/; HttpOnly; SameSite=Strict");
    // Inbox QA stores only allowlisted safe fields; other requests omit bodies.
    requests.push({ method: req.method, path: url.pathname, status: result.status });
    const respond = () => res.writeHead(result.status, { "Content-Type": "application/json" }).end(JSON.stringify(result.body));
    if (result.delayMs) setTimeout(respond, result.delayMs);
    else respond();
    };
    if (viewStates && isInboxRoute && !loggedOut) {
      let rawBody = "";
      req.on("data", (chunk) => { if (rawBody.length < 16384) rawBody += chunk.toString("utf8"); });
      req.on("end", () => finishApiRequest(rawBody));
    } else {
      req.resume();
      finishApiRequest();
    }
    return;
  }
  if (req.method !== "GET" && req.method !== "HEAD") { res.writeHead(405).end(); return; }
  if (viewStates && url.pathname === "/__qa/start") {
    const scenario = url.searchParams.get("case");
    const view = url.searchParams.get("view");
    if (!SCENARIOS.has(scenario) || !["overview", "inbox", "reviews", "knowledge", "team", "ai-simulator", "bookings"].includes(view)) { res.writeHead(400).end(); return; }
    res.setHeader("Set-Cookie", [`dashboard_qa_case=${scenario}; Path=/; HttpOnly; SameSite=Strict`, "dashboard_qa_signed_out=; Max-Age=0; Path=/; HttpOnly; SameSite=Strict"]);
    res.writeHead(302, { Location: `/app/${view}` }).end(); return;
  }
  if (url.pathname === "/__qa/requests") { res.writeHead(200, { "Content-Type": "application/json" }).end(JSON.stringify(requests)); return; }
  if (url.pathname === "/__qa/inbox-requests") { res.writeHead(200, { "Content-Type": "application/json" }).end(JSON.stringify(inboxRequests)); return; }
  if (url.pathname === "/__qa/bootstrap.js") {
    res.writeHead(200, { "Content-Type": "text/javascript" }).end(`
      // This test-only script is never shipped in web/ or its Docker image.
      const RealDate = Date;
      window.Date = class extends RealDate { constructor(...args) { super(...(args.length ? args : [${JSON.stringify(FIXED_TIME)}])); } static now() { return new RealDate(${JSON.stringify(FIXED_TIME)}).getTime(); } };
      localStorage.setItem('melpis_lang', 'it');
      localStorage.setItem('melpis-theme', 'dark');
      document.cookie = 'melpis_lang=it; Path=/; SameSite=Strict';
      document.cookie = 'wa_csrf=synthetic-qa-only; Path=/; SameSite=Strict';
      const nativeFetch = window.fetch.bind(window);
      let pending = 0, revision = 0;
      async function ready() {
        const visit = ++revision;
        await document.fonts.ready;
        // Wait for production entrance transitions; do not alter their CSS/rendering.
        await new Promise(resolve => requestAnimationFrame(resolve));
        // Capture the live-status pulse at frame zero, rather than a random instant.
        // This exists only in the isolated screenshot server, not shipped assets.
        document.getAnimations().filter(animation => !Number.isFinite(animation.effect.getComputedTiming().endTime))
          .forEach(animation => { animation.pause(); animation.currentTime = 0; });
        await Promise.all(document.getAnimations()
          .filter(animation => Number.isFinite(animation.effect.getComputedTiming().endTime))
          .map(animation => animation.finished.catch(() => {})));
        requestAnimationFrame(() => requestAnimationFrame(() => {
          if (!pending && revision === visit && document.body) document.body.dataset.qaReady = 'true';
        }));
      }
      window.fetch = async (...args) => {
        pending++; revision++;
        if (document.body) document.body.dataset.qaReady = 'false';
        try { return await nativeFetch(...args); }
        finally { pending--; if (!pending) ready(); }
      };
      document.addEventListener('DOMContentLoaded', ready, {once:true});
    `);
    return;
  }
  if (url.pathname === "/config.js") { res.writeHead(200, { "Content-Type": "text/javascript" }).end("window.MELPIS_API_BASE = '';\n"); return; }
  let relative;
  let dashboard = false;
  if (url.pathname === "/app" || /^\/app\/(overview|inbox|bookings|reviews|team|ai-simulator|knowledge|ai-settings|settings)\/?$/.test(url.pathname)) {
    relative = "index.html"; dashboard = true;
    const scenario = url.searchParams.get("qa_case");
    if (viewStates && SCENARIOS.has(scenario)) res.setHeader("Set-Cookie", `dashboard_qa_case=${scenario}; Path=/; HttpOnly; SameSite=Strict`);
  } else if (url.pathname.startsWith("/app/")) { relative = url.pathname.slice(5); }
  else if (interactions && url.pathname === "/accedi/") { relative = "accedi/index.html"; }
  else if (interactions && ["/auth.css", "/auth.js", "/login.js"].includes(url.pathname)) { relative = url.pathname.slice(1); }
  else if (/^\/(brand|fonts|locales)\//.test(url.pathname) || url.pathname === "/i18n-client.js") { relative = url.pathname.slice(1); }
  else { res.writeHead(404).end(); return; }
  const target = path.resolve(root, relative);
  if (!target.startsWith(root + path.sep) || !fs.existsSync(target) || !fs.statSync(target).isFile()) { res.writeHead(404).end(); return; }
  res.setHeader("Content-Type", types[path.extname(target)] || "application/octet-stream");
  let content = fs.readFileSync(target);
  if (dashboard) content = content.toString().replace('<script src="/app/theme-init.js">', '<script src="/__qa/bootstrap.js"></script>\n<script src="/app/theme-init.js">');
  res.end(req.method === "HEAD" ? undefined : content);
});
if (require.main === module) server.listen(port, "127.0.0.1", () => console.log(`Synthetic dashboard QA: http://127.0.0.1:${port}/app/overview`));
module.exports = { server, interactionReply, resolveAssetRoot, defaultRoot };
