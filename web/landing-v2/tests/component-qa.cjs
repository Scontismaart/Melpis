const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');

const root = require('node:path').resolve(__dirname, '..');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

(async () => {
  const server = spawn(process.execPath, ['tools/preview.mjs'], { cwd: root, env: { ...process.env, MV2_PORT: '0' }, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  let output = '';
  const port = await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error(`Preview timeout: ${output}`)), 10000);
    server.stdout.on('data', chunk => { output += chunk; const match = output.match(/Preview V2: http:\/\/127\.0\.0\.1:(\d+)/); if (match) { clearTimeout(timeout); resolve(Number(match[1])); } });
    server.stderr.on('data', chunk => { output += chunk; });
    server.on('exit', code => { clearTimeout(timeout); reject(new Error(`Preview exited ${code}: ${output}`)); });
  });
  let browser;
  try {
    browser = await chromium.launch({ headless: true });
    for (const javaScriptEnabled of [false, true]) {
      const page = await browser.newPage({ viewport: { width: 390, height: 844 }, javaScriptEnabled });
      await page.goto(`http://127.0.0.1:${port}/landing-v2/`, { waitUntil: 'networkidle' });
      const buttons = page.locator('.mv2-control-options button');
      assert.equal(await buttons.count(), 2, 'SSR must include both controls');
      assert.equal(await buttons.nth(1).getAttribute('aria-pressed'), 'true', 'SSR preserves the original active team option');
      assert.equal(await buttons.nth(0).getAttribute('aria-pressed'), 'false');
      if (!javaScriptEnabled) {
        assert.equal(await buttons.nth(0).isDisabled(), true, 'without JS, controls are safely disabled');
        assert.match(await page.locator('.mv2-control-label').allInnerTexts().then(xs => xs.join(' ')), /controlli richiedono JavaScript/i);
      } else {
        assert.equal(await buttons.nth(0).isDisabled(), true, 'client:visible island remains unhydrated before scroll');
        await page.locator('.mv2-control-options').scrollIntoViewIfNeeded();
        await page.waitForFunction(() => !document.querySelector('.mv2-control-options button').disabled);
        await buttons.nth(0).click();
        assert.equal(await buttons.nth(0).getAttribute('aria-pressed'), 'true');
        assert.match(await page.locator('.mv2-control-footer').innerText(), /Richiesta in attesa/);
        await buttons.nth(1).click();
        assert.equal(await buttons.nth(1).getAttribute('aria-pressed'), 'true');
        assert.match(await page.locator('.mv2-control-footer').innerText(), /Passaggio visibile/);
      }
      await page.close();
    }
    console.log('Control panel SSR, JS-off fallback, visible hydration and both state transitions passed.');
  } finally { if (browser) await browser.close(); server.kill(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
