/* Capture a short local-only walkthrough for design review. */
const { chromium } = require('playwright');
const fs = require('node:fs/promises');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const out = path.join(root, 'artifacts', 'phase2-review');
const url = process.env.V2_QA_URL || 'http://127.0.0.1:4178/landing-v2/';
if (!['127.0.0.1', 'localhost', '[::1]'].includes(new URL(url).hostname)) throw new Error('Video capture accepts loopback previews only.');
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

(async () => {
  await fs.mkdir(out, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce', recordVideo: { dir: out, size: { width: 1440, height: 900 } } });
  const page = await context.newPage();
  await page.goto(url, { waitUntil: 'networkidle' });
  await page.evaluate(() => document.fonts.ready);
  await wait(900);
  await page.locator('.mv2-ps-frame').evaluate(el => el.scrollIntoView({ behavior: 'instant', block: 'start' }));
  await wait(400);
  for (const label of ['Inbox', 'Prenotazioni', 'Recensioni', 'Knowledge Base', 'Team', 'Panoramica']) {
    await page.getByRole('button', { name: new RegExp(label) }).click();
    await page.getByRole('heading', { name: label, exact: true }).waitFor();
    await wait(900);
  }
  const video = page.video();
  await context.close();
  await browser.close();
  const temporaryPath = await video.path();
  const finalPath = path.join(out, 'showcase-demo.webm');
  await fs.rm(finalPath, { force: true });
  await fs.rename(temporaryPath, finalPath);
  const images=['phase2-1440-viewport.png','phase2-768-viewport.png','phase2-390-viewport.png','phase2-view-inbox.png','phase2-view-prenotazioni.png','phase2-view-recensioni.png','phase2-view-knowledge-base.png','phase2-view-team.png','phase2-mobile-inbox.png','phase2-mobile-prenotazioni.png','phase2-mobile-knowledge-base.png'];
  const gallery=`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Melpis V2 · review</title><style>body{margin:0;padding:32px;background:#07090a;color:#f7f7f5;font:16px system-ui}main{max-width:1280px;margin:auto}h1{font-size:28px}figure{margin:24px 0}img,video{display:block;width:100%;height:auto;border:1px solid #74777b;border-radius:8px}figcaption{margin:8px 0;color:#aab2c0}</style><main><h1>Melpis V2 · review captures</h1><p>Preview illustrativa · dati sintetici. Captures generated locally for design and QA review.</p><video controls src="showcase-demo.webm"></video>${images.map(name=>`<figure><img loading="lazy" src="${name}" alt="${name.replaceAll('-',' ').replace('.png','')}"><figcaption>${name}</figcaption></figure>`).join('')}</main>`;
  await fs.writeFile(path.join(out,'gallery.html'),gallery);
  console.log(JSON.stringify({ video: finalPath, bytes: (await fs.stat(finalPath)).size, gallery: path.join(out,'gallery.html') }));
})().catch(error => { console.error(error); process.exit(1); });
