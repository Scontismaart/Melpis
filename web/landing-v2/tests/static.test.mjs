import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const node = process.execPath;

test('V2 symbol and favicon preserve the canonical Melpis SVG', async () => {
  const canonical = await readFile(path.resolve(root, '../landing/brand/melpis-symbol-dark.svg'), 'utf8');
  const normalize = (svg) => svg.replace(/\r\n/g, '\n');
  for (const file of ['public/assets/mv2-symbol.svg', 'public/assets/favicon.svg']) {
    const svg = await readFile(path.join(root, file), 'utf8');
    assert.equal(normalize(svg), normalize(canonical));
    assert.match(svg, /viewBox="0 0 200 144"/);
    assert.equal([...svg.matchAll(/<path\b/g)].length, 3);
  }
});

test('build allowlist is self-contained and preview is isolated', async (t) => {
  const build = spawn(node, ['tools/build.mjs'], { cwd: root, stdio: 'inherit' });
  const [buildCode] = await once(build, 'exit');
  assert.equal(buildCode, 0);
  const html = await readFile(path.join(root, 'dist/index.html'), 'utf8');
  assert.match(html, /href="#mv2-percorso"/);
  assert.match(html, /id="mv2-piani"/);
  assert.match(html, /Ogni conversazione\./);
  assert.match(html, /Sotto controllo\./);
  assert.match(html, /client="load"/);
  assert.match(html, /client="visible"/);
  assert.match(html, /Da una domanda,/);
  assert.match(html, /Demo illustrativa · dati sintetici/);
  assert.match(html, /0 posti disponibili/);
  assert.match(html, /informazioni non bastano/);
  assert.match(html, /Anteprima dimostrativa · dati sintetici/);
  for (const label of ['Panoramica','Inbox','Prenotazioni','Recensioni','Knowledge Base','Team']) assert.match(html, new RegExp(`>${label}<`), `SSR product navigation includes ${label}`);
  assert.match(html, /disabled="" aria-pressed="true"/);
  assert.match(html, /content="noindex, nofollow, noarchive"/);

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
  assert.ok(port > 0, `preview non avviato: ${startup}`);
  const origin = `http://127.0.0.1:${port}`;
  const home = await fetch(`${origin}/landing-v2/`);
  assert.equal(home.status, 200);
  assert.match(home.headers.get('content-security-policy'), /connect-src 'none'/);
  assert.equal(home.headers.get('x-robots-tag'), 'noindex, nofollow, noarchive');
  assert.match(await home.text(), /Melpis/);

  const css = await readFile(path.join(root, 'dist', '_astro', (await (await import('node:fs/promises')).readdir(path.join(root, 'dist', '_astro'))).find(f => f.endsWith('.css'))), 'utf8');
  assert.doesNotMatch(css, /#10b981|#34d399|16,\s*185,\s*129|52,\s*211,\s*153/i, 'runtime CSS must use the monochrome silver palette');
  const fontPath = css.match(/url\((\/landing-v2\/assets\/fonts\/[^)]+\.woff2)\)/)?.[1];
  assert.ok(fontPath, 'built CSS must reference a self-hosted font');
  const font = await fetch(`${origin}${fontPath}`);
  assert.equal(font.status, 200);
  assert.equal(font.headers.get('content-type'), 'font/woff2');
  assert.ok(Number(font.headers.get('content-length')) > 1000);

  for (const url of ['/landing-v2/../index.html', '/landing-v2/%2e%2e/index.html', '/landing-v2/%5c..%5cindex.html']) {
    const response = await fetch(`${origin}${url}`, { redirect: 'manual' });
    assert.ok([400, 403, 404].includes(response.status), `${url} ha risposto ${response.status}`);
  }
  assert.equal((await fetch(`${origin}/landing-v2/tools/build.mjs`)).status, 404);
  assert.equal((await fetch(`${origin}/`)).status, 404);
  assert.match(await (await fetch(`${origin}/robots.txt`)).text(), /Disallow: \/\s/);
  assert.equal((await fetch(`${origin}/landing-v2/`, { method: 'POST' })).status, 405);
  const scripts = await (await import('node:fs/promises')).readdir(path.join(root, 'dist', '_astro'));
  const bundledJs = await Promise.all(scripts.filter(file => file.endsWith('.js')).map(file => readFile(path.join(root, 'dist', '_astro', file), 'utf8')));
  assert.ok(bundledJs.length > 0);
  assert.ok(bundledJs.some(source => source.includes('Panoramica') && source.includes('Prenotazioni') && source.includes('Knowledge Base')));
  assert.ok(bundledJs.some(source => source.includes('gsap')) || (await readFile(path.join(root, 'package.json'),'utf8')).includes('"gsap"'));
});
