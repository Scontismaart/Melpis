/*
 * Makes static HTML compatible with the production CSP.  The generated names
 * are content-addressed, so repeating the localization build is stable.
 */
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const WEB_DIR = path.join(process.cwd(), "web");
// Astro emits its own hash-based policy for V2. Leave that build intact.
const LANDING_V2 = path.join(WEB_DIR, "landing-v2");
const ASSET_DIR = path.join(WEB_DIR, "inline-assets");
const EXCLUDED_HTML = new Set([
  path.join(WEB_DIR, "landing", "hero-glassmorphism.html"),
  path.join(WEB_DIR, "landing", "knowledge-hero.html"),
]);
const DATA_SCRIPT_TYPES = new Set(["application/json", "application/ld+json"]);

function walkHtml(dir, files = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const candidate = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (candidate !== LANDING_V2) walkHtml(candidate, files);
    }
    else if (entry.name.endsWith(".html") && !EXCLUDED_HTML.has(candidate)) files.push(candidate);
  }
  return files.sort();
}

function normalized(content) {
  return `${content.replace(/\r\n/g, "\n").trim()}\n`;
}

function writeAsset(kind, extension, content, manifest) {
  const digest = crypto.createHash("sha256").update(content).digest("hex");
  const filename = `${kind}-${digest}.${extension}`;
  const output = path.join(ASSET_DIR, filename);
  if (!fs.existsSync(output) || fs.readFileSync(output, "utf8") !== content) {
    fs.writeFileSync(output, content, "utf8");
  }
  manifest.add(filename);
  return `/inline-assets/${filename}`;
}

function scriptType(attributes) {
  const match = attributes.match(/\btype\s*=\s*(["'])(.*?)\1/i);
  return (match ? match[2] : "").trim().toLowerCase();
}

function isExecutableScript(attributes) {
  if (/\bsrc\s*=/i.test(attributes)) return false;
  const type = scriptType(attributes);
  return !DATA_SCRIPT_TYPES.has(type) && (
    !type || type === "text/javascript" || type === "application/javascript" || type === "module"
  );
}

function removeRemoteFontLinks(html) {
  return html
    .replace(/^\s*<link\b[^>]*href=["']https:\/\/fonts\.googleapis\.com[^>]*>\s*\n?/gim, "")
    .replace(/^\s*<link\b[^>]*href=["']https:\/\/fonts\.gstatic\.com[^>]*>\s*\n?/gim, "")
    .replace(/^\s*<link\b[^>]*rel=["']preconnect["'][^>]*href=["']https:\/\/fonts\.(?:googleapis|gstatic)\.com[^>]*>\s*\n?/gim, "");
}

function extractInlineAssets() {
  fs.mkdirSync(ASSET_DIR, { recursive: true });
  const manifest = new Set();
  let transformed = 0;

  for (const file of walkHtml(WEB_DIR)) {
    const before = fs.readFileSync(file, "utf8");
    let html = removeRemoteFontLinks(before);
    html = html.replace(/<style\b[^>]*>([\s\S]*?)<\/style>/gi, (_match, body) => {
      const href = writeAsset("style", "css", normalized(body), manifest);
      return `<link rel="stylesheet" href="${href}">`;
    });
    html = html.replace(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi, (match, attributes, body) => {
      if (!isExecutableScript(attributes)) return match;
      const src = writeAsset("script", "js", normalized(body), manifest);
      return `<script src="${src}" defer></script>`;
    });
    if (html !== before) {
      fs.writeFileSync(file, html, "utf8");
      transformed++;
    }
    for (const match of html.matchAll(/\/inline-assets\/([^"'?#\s>]+)/g)) {
      manifest.add(match[1]);
    }
  }

  const assets = [...manifest].sort();
  fs.writeFileSync(
    path.join(ASSET_DIR, "manifest.json"),
    `${JSON.stringify({ version: 1, assets }, null, 2)}\n`,
    "utf8"
  );
  console.log(`  ✓ Externalized CSP assets in ${transformed} HTML files (${assets.length} unique files)`);
}

module.exports = { extractInlineAssets, isExecutableScript, removeRemoteFontLinks };

if (require.main === module) extractInlineAssets();
