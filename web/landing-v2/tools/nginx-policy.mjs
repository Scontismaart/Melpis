import assert from 'node:assert/strict';
import { readFileSync, writeFileSync } from 'node:fs';

const pages = ['index', 'piani', 'ristoranti', 'hotel', 'barbieri', 'saloni'];
const policies = pages.map((page) => {
  const html = readFileSync(new URL(`../dist/${page}.html`, import.meta.url), 'utf8');
  const policy = html.match(/<meta http-equiv="content-security-policy" content="([^"]+)"/i)?.[1];
  assert.ok(policy && /^[a-zA-Z0-9:;'=+/ _-]+$/.test(policy), `Invalid Astro CSP on ${page}`);
  for (const rule of ["connect-src 'none'", "form-action 'none'", "object-src 'none'", "base-uri 'none'"]) {
    assert.ok(policy.split(';').map((directive) => directive.trim()).includes(rule), `Missing ${rule} on ${page}`);
  }
  assert.match(policy, /script-src 'self' 'sha256-/);
  assert.doesNotMatch(policy, /unsafe-|strict-dynamic/);
  assert.match(html, /<meta name="robots" content="noindex, nofollow, noarchive"/);
  return policy;
});
const [policy] = policies;
assert.ok(policies.every((value) => value === policy), 'Public pages must share the same generated Astro CSP');

writeFileSync(new URL('../nginx-policy.conf', import.meta.url), `root /opt/melpis/landing-v2;
add_header Content-Security-Policy "${policy.trim().replace(/;$/, '')}; frame-ancestors 'none'" always;
add_header X-Robots-Tag "noindex, nofollow, noarchive" always;
add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "DENY" always;
add_header Referrer-Policy "no-referrer" always;
add_header Cache-Control "no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0" always;
`);
