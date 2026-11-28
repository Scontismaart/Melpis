// Deterministically move static style attributes into same-origin CSS.
// Idempotent: generated blocks are replaced, and already extracted pages stay unchanged.
import crypto from "node:crypto";
import fs from "node:fs";

const groups = [
  { css: "web/style.css", html: ["web/index.html"] },
  { css: "web/auth.css", html: ["web/login.html", "web/register.html"] },
  { css: "web/landing/style.css", html: [
    "web/landing/privacy.html", "web/landing/termini.html",
    "web/landing/cookie.html", "web/landing/404.html",
    "web/landing/documentazione/index.html",
    "web/landing/settori/ristoranti/index.html", "web/landing/settori/saloni-bellezza/index.html",
    "web/landing/settori/studi-medici/index.html", "web/landing/settori/hotel/index.html",
  ] },
];
const begin = "/* generated:inline-styles:start */";
const end = "/* generated:inline-styles:end */";

for (const group of groups) {
  const rules = new Map();
  for (const path of group.html.filter(fs.existsSync)) {
    let html = fs.readFileSync(path, "utf8");
    html = html.replace(/<([a-z][\w:-]*)([^<>]*?)\sstyle="([^"]*)"([^<>]*?)>/gi,
      (match, tag, before, style, after) => {
        const normalized = style.trim().replace(/;?\s*$/, ";");
        const name = `is-${crypto.createHash("sha256").update(normalized).digest("hex").slice(0, 12)}`;
        rules.set(name, normalized);
        const attrs = `${before}${after}`;
        if (/\sclass="/.test(attrs)) {
          return `<${tag}${attrs.replace(/\sclass="([^"]*)"/, ` class="$1 ${name}"`)}>`;
        }
        return `<${tag}${before} class="${name}"${after}>`;
      });
    fs.writeFileSync(path, html);
  }
  let css = fs.readFileSync(group.css, "utf8");
  css = css.replace(new RegExp(`${begin.replace(/[/*]/g, "\\$&")}[\\s\\S]*?${end.replace(/[/*]/g, "\\$&")}\\s*`, "g"), "");
  if (rules.size) {
    css += `\n${begin}\n${[...rules].map(([name, value]) => `.${name}{${value}}`).join("\n")}\n${end}\n`;
  }
  fs.writeFileSync(group.css, css);
}
