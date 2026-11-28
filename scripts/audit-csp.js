const fs = require("fs");
const path = require("path");
const { isExecutableScript } = require("./extract-inline-assets");

const root = path.join(process.cwd(), "web");
// The Astro build validates its separately served, hash-based policy.
const landingV2Root = path.join(root, "landing-v2");
const excluded = new Set([
  path.join(root, "landing", "hero-glassmorphism.html"),
  path.join(root, "landing", "knowledge-hero.html"),
]);
const findings = [];

function walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const file = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (file !== landingV2Root) walk(file);
    }
    else if (entry.name.endsWith(".html") && !excluded.has(file)) {
      const content = fs.readFileSync(file, "utf8");
      if (/<style\b[^>]*>[\s\S]*?<\/style>/i.test(content)) findings.push(`${file}: inline style block`);
      for (const match of content.matchAll(/<script\b([^>]*)>[\s\S]*?<\/script>/gi)) {
        if (isExecutableScript(match[1])) findings.push(`${file}: executable inline script`);
      }
      if (/https:\/\/(?:images\.unsplash\.com|source\.unsplash\.com|picsum\.photos)/i.test(content)) {
        findings.push(`${file}: remote demo image`);
      }
      if (/https:\/\/fonts\.(?:googleapis|gstatic)\.com/i.test(content)) {
        findings.push(`${file}: remote font dependency`);
      }
    }
  }
}

walk(root);
const headers = fs.readFileSync(path.join(root, "security-headers.conf"), "utf8");
for (const directive of ["script-src", "style-src"]) {
  const match = headers.match(new RegExp(`${directive} ([^;]+)`));
  if (!match || match[1].includes("'unsafe-inline'")) findings.push(`security-headers.conf: unsafe ${directive}`);
}
const fullCalendarCss = path.join(root, "vendor", "fullcalendar.css");
const fullCalendarBundle = path.join(root, "vendor", "fullcalendar.min.js");
const dashboard = fs.readFileSync(path.join(root, "index.html"), "utf8");
if (!fs.existsSync(fullCalendarCss) || !/href="\/?(?:app\/)?vendor\/fullcalendar\.css"/.test(dashboard)) {
  findings.push("FullCalendar CSS must be an explicit same-origin stylesheet");
}
if (fs.existsSync(fullCalendarBundle) && /function _e\(e\)\{let t=Re\.get\(e\)/.test(fs.readFileSync(fullCalendarBundle, "utf8"))) {
  findings.push("FullCalendar bundle still creates runtime style elements");
}
if (findings.length) {
  console.error(findings.join("\n"));
  process.exit(1);
}
console.log("CSP audit passed: no executable inline code, style blocks, remote demo images, or broad unsafe-inline directives.");
