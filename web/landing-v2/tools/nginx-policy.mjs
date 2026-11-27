import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
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
  for (const [directive, pattern] of [
    ['script-src', /<script\b([^>]*)>([\s\S]*?)<\/script>/gi],
    ['style-src', /<style\b([^>]*)>([\s\S]*?)<\/style>/gi],
  ]) {
    const sources = policy.split(';').map((value) => value.trim()).find((value) => value.startsWith(`${directive} `));
    for (const [, attributes, body] of html.matchAll(pattern)) {
      if (directive === 'script-src' && /\ssrc=/.test(attributes)) {
        assert.match(attributes, /\ssrc="\/landing-v2\//, `Unexpected script URL on ${page}`);
        continue;
      }
      const hash = createHash('sha256').update(body).digest('base64');
      assert.ok(sources?.split(' ').includes(`'sha256-${hash}'`), `Unapproved inline ${directive} on ${page}`);
    }
  }
  assert.doesNotMatch(html, /<[a-z][^>]*\s(?:style|on[a-z]+)="/i, `Inline style or event attribute on ${page}`);
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
