const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');

const root = require('node:path').resolve(__dirname, '..');

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
    const url = `http://127.0.0.1:${port}/landing-v2/`;
    const noJs = await browser.newPage({ viewport: { width: 390, height: 844 }, javaScriptEnabled: false });
    await noJs.goto(url, { waitUntil: 'networkidle' });
    assert.equal(await noJs.locator('h1').innerText(), 'Ogni conversazione.\nSotto controllo.');
    assert.equal(await noJs.locator('.mv2-ps-nav-button').count(), 6, 'SSR includes all six sections');
    assert.equal(await noJs.locator('.mv2-ps-nav-button[aria-pressed="true"]').innerText(), 'Panoramica');
    assert.equal(await noJs.locator('.mv2-ps-nav-button:not([aria-pressed="true"])').first().isDisabled(), true, 'without JS, navigation stays visibly disabled');
    assert.match(await noJs.locator('.mv2-ps-content noscript').innerText(), /navigazione tra sezioni richiede JavaScript/i);
    assert.match(await noJs.locator('.mv2-story-fallback').innerText(), /nessun posto disponibile[\s\S]*informazioni non bastano/i);
    assert.equal(await noJs.locator('.mv2-story-demo').isVisible(), false, 'no-JS story remains fully in the static fallback');
    assert.match(await noJs.locator('.mv2-ps-overview').innerText(), /Messaggi ricevuti[\s\S]*Gestiti assistente[\s\S]*Girati a umano/);
    await noJs.close();

    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    let navigationCount = 0;
    page.on('framenavigated', frame => { if (frame === page.mainFrame()) navigationCount += 1; });
    await page.goto(url, { waitUntil: 'networkidle' });
    await page.waitForFunction(() => [...document.querySelectorAll('.mv2-ps-nav-button')].every(button => !button.disabled));
    assert.equal(navigationCount, 1, 'the product view interaction does not reload the page');
    const choose = async (label, heading) => {
      await page.getByRole('button', { name: new RegExp(label) }).click();
      await page.getByRole('heading', { name: heading, exact: true }).waitFor();
      assert.equal(await page.locator('.mv2-ps-nav-button[aria-pressed="true"] span').innerText(), label);
      assert.equal(navigationCount, 1, `${heading} selection does not change URL or reload`);
    };
    await choose('Inbox', 'Inbox');
    assert.match(await page.locator('.mv2-ps-chat').innerText(), /Giulia R\.[\s\S]*Team/);
    await page.getByRole('button', { name: /Marco L\./ }).click();
    assert.match(await page.locator('.mv2-ps-chat').innerText(), /Marco L\.[\s\S]*Siamo aperti fino alle 23:00/);
    await choose('Prenotazioni', 'Prenotazioni');
    await page.getByRole('button', { name: /Davide C\./ }).click();
    assert.match(await page.locator('.mv2-ps-booking-detail').innerText(), /Davide C\.[\s\S]*20:30/);
    await choose('Recensioni', 'Recensioni');
    assert.match(await page.locator('.mv2-ps-review-list').innerText(), /Bozza di risposta[\s\S]*Non pubblicata/);
    await choose('Knowledge Base', 'Knowledge Base');
    await page.getByRole('button', { name: /Politica di cancellazione/ }).click();
    assert.match(await page.locator('.mv2-ps-knowledge-detail').innerText(), /almeno 24 ore/);
    await choose('Team', 'Team');
    assert.match(await page.locator('.mv2-ps-team-list').innerText(), /Elena Rossi[\s\S]*Paolo Bianchi[\s\S]*Marta Conti/);

    const inboxNav = page.getByRole('button', { name: /Inbox/ });
    await inboxNav.focus();
    await page.keyboard.press('Enter');
    assert.equal(await inboxNav.getAttribute('aria-pressed'), 'true', 'keyboard activation updates the selected state');
    assert.equal(await page.evaluate(() => document.activeElement?.classList.contains('mv2-ps-nav-button')), true, 'focus remains on the selected navigation control');

    const mobile = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await mobile.goto(url, { waitUntil: 'networkidle' });
    await mobile.waitForFunction(() => !document.querySelector('#mv2-ps-view-select').disabled);
    const selector = mobile.locator('#mv2-ps-view-select');
    assert.equal(await selector.locator('option').count(), 6, 'mobile selector includes all six views');
    assert.ok((await selector.boundingBox()).height >= 44, 'mobile selector meets the touch target minimum');
    await selector.selectOption('inbox');
    await mobile.getByRole('heading', { name: 'Inbox' }).waitFor();
    await selector.selectOption('knowledge');
    await mobile.getByRole('heading', { name: 'Knowledge Base' }).waitFor();
    assert.equal(await mobile.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true, 'mobile page has no horizontal overflow');
    await mobile.close();

    const reduced = await browser.newPage({ viewport: { width: 390, height: 844 }, reducedMotion: 'reduce' });
    await reduced.goto(url, { waitUntil: 'networkidle' });
    assert.equal(await reduced.locator('.mv2-ps-view').evaluate(el => getComputedStyle(el).animationName), 'none', 'reduced motion disables view transitions');
    await reduced.close();
    await page.close();
    console.log('Product showcase SSR/JS-off fallback, all six views, secondary selections, keyboard navigation, mobile select and reduced motion passed.');
  } finally { if (browser) await browser.close(); server.kill(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
