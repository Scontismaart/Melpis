import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const node = process.execPath;

test('ristoranti page is generated, linked from homepage, and serves all assets cleanly', async (t) => {
  // 1. Verify build output files
  const htmlIndex = await readFile(path.join(root, 'dist/index.html'), 'utf8');
  const htmlRest = await readFile(path.join(root, 'dist/ristoranti.html'), 'utf8');

  // Homepage card link verification
  assert.match(
    htmlIndex,
    /href="\/landing-v2\/ristoranti\/"[^>]*>[\s\S]*?Ristoranti/,
    'Homepage Ristoranti card links to /landing-v2/ristoranti/'
  );
  assert.match(
    htmlIndex,
    /href="\/landing-v2\/hotel\/"[^>]*>[\s\S]*?Hotel/,
    'Homepage Hotel card links to /landing-v2/hotel/'
  );
  assert.match(
    htmlIndex,
    /href="\/landing-v2\/barbieri\/"[^>]*>[\s\S]*?Barbieri/,
    'Homepage Barbieri card links to /landing-v2/barbieri/'
  );
  assert.match(
    htmlIndex,
    /href="\/landing-v2\/saloni\/"[^>]*>[\s\S]*?Centri estetici/,
    'Homepage Centri estetici card links to /landing-v2/saloni/'
  );

  // Semantic structure & Single H1
  const h1Matches = [...htmlRest.matchAll(/<h1\b[^>]*>([\s\S]*?)<\/h1>/gi)];
  assert.equal(h1Matches.length, 1, 'Only one H1 must exist on /ristoranti/');
  assert.match(h1Matches[0][1], /L'intelligenza artificiale che/i);
  assert.match(h1Matches[0][1], /gestisce i tuoi tavoli/i);

  // Check H2 section headings matching the restaurant page structure
  assert.match(htmlRest, /Configura una volta\./);
  assert.match(htmlRest, /Melpis gestisce il servizio\./);
  assert.match(htmlRest, /Smetti di rispondere alle stesse domande/);
  assert.match(htmlRest, /L'assistente aiuta\./);
  assert.match(htmlRest, /Organizza i tavoli e/);
  assert.doesNotMatch(htmlRest, /Tutto sotto controllo,/);
  assert.doesNotMatch(htmlRest, /Prima si configura\./);
  assert.match(htmlRest, /Scegli il passo/);
  assert.match(htmlRest, /Prima di attivare/);
  assert.match(htmlRest, /Porta Melpis/);

  // Navigation targets
  assert.match(htmlRest, /class="mv2-rest-brand"[^>]*href="\/"/);
  assert.match(htmlRest, /href="#mv2-rest-piani"/);

  // Isolated preview verification
  const proc = spawn(node, ['tools/preview.mjs'], {
    cwd: root,
    env: { ...process.env, MV2_PORT: '0' },
    stdio: ['ignore', 'pipe', 'inherit'],
  });
  let startup = '';
  proc.stdout.setEncoding('utf8');
  for await (const chunk of proc.stdout) {
    startup += chunk;
    if (startup.includes('http://127.0.0.1:')) break;
  }
  t.after(() => proc.kill());
  const port = Number(startup.match(/:(\d+)\/landing-v2\//)?.[1]);
  assert.ok(port > 0, `Preview did not start: ${startup}`);
  const origin = `http://127.0.0.1:${port}`;

  // Fetch /landing-v2/ristoranti/
  const resRest = await fetch(`${origin}/landing-v2/ristoranti/`);
  assert.equal(resRest.status, 200);
  assert.equal(resRest.headers.get('content-type'), 'text/html; charset=utf-8');
  assert.match(resRest.headers.get('content-security-policy'), /connect-src 'none'/);
  const restText = await resRest.text();
  assert.match(restText, /L'intelligenza artificiale che/i);
  assert.match(restText, /gestisce i tuoi tavoli/i);
});
