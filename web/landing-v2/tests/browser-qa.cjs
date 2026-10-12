/* Loopback-only V2 browser QA. Captures are local review artifacts, not shipped assets. */
const { chromium } = require('playwright');
const axe = require('axe-core');
const fs = require('node:fs/promises');
const path = require('node:path');
const { spawn } = require('node:child_process');
const root = path.resolve(__dirname, '..');
const out = path.join(root, 'artifacts', 'phase2-review');
const url = process.env.V2_QA_URL || 'http://127.0.0.1:4176/landing-v2/';
const origin = new URL(url).origin;
if (!['127.0.0.1', 'localhost', '[::1]'].includes(new URL(url).hostname)) throw new Error('Browser QA accepts loopback previews only.');
const results = { generatedAt: new Date().toISOString(), url, browser: null, viewports: [], views: [], errors: [], externalRequests: [], failures: [] };
const labels = ['Panoramica', 'Inbox', 'Prenotazioni', 'Recensioni', 'Knowledge Base', 'Team'];
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
function check(pass, name, evidence) { if (!pass) results.failures.push({ name, evidence }); }
async function startPreview() {
  if (process.env.V2_QA_URL) return null;
  const child = spawn(process.execPath, ['tools/preview.mjs'], { cwd: root, env: { ...process.env, MV2_PORT: '4176' }, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  let log = ''; child.stdout.on('data', c => log += c); child.stderr.on('data', c => log += c);
  for (let i = 0; i < 100; i++) { if (child.exitCode !== null) throw new Error(`Preview exited: ${log}`); if (log.includes('Preview V2:')) return child; await wait(100); }
  child.kill(); throw new Error(`Preview startup timeout: ${log}`);
}
async function monitor(page, width) {
  page.on('console', msg => { if (msg.type() === 'error') results.errors.push(`${width}: ${msg.text()}`); });
  page.on('pageerror', error => results.errors.push(`${width}: ${String(error)}`));
  page.on('requestfailed', request => results.errors.push(`${width}: ${request.url()} ${request.failure()?.errorText}`));
  page.on('response', response => { if (response.status() >= 400) results.errors.push(`${width}: HTTP ${response.status()} ${response.url()}`); });
  page.on('request', request => { if (new URL(request.url()).origin !== origin) results.externalRequests.push(request.url()); });
  await page.addInitScript(() => { window.__mv2Csp = []; document.addEventListener('securitypolicyviolation', e => window.__mv2Csp.push({ directive: e.violatedDirective, blockedURI: e.blockedURI })); });
}
async function open(page, width) {
  await monitor(page, width);
  const response = await page.goto(url, { waitUntil: 'networkidle' });
  await page.evaluate(() => document.fonts.ready);
  await wait(750);
  return response;
}
async function axeRun(page) {
  await page.evaluate(axe.source);
  return page.evaluate(async () => window.axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'] } }));
}
(async () => {
  await fs.mkdir(out, { recursive: true });
  let server, browser;
  try {
    server = await startPreview();
    browser = await chromium.launch({ headless: true }); results.browser = browser.version();
    for (const width of [1440, 1024, 768, 390, 360, 320]) {
      const height = width <= 390 ? 1000 : 900;
      const context = await browser.newContext({ viewport: { width, height }, reducedMotion: 'no-preference' });
      const page = await context.newPage(); const response = await open(page, width); const headers = response.headers();
      const initial = await page.evaluate(() => ({
        width: innerWidth, pageWidth: document.documentElement.scrollWidth, pageHeight: document.documentElement.scrollHeight,
        hero: document.querySelector('#mv2-inizio').getBoundingClientRect().toJSON(), frame: document.querySelector('.mv2-ps-frame').getBoundingClientRect().toJSON(),
        logoLoaded: [...document.images].every(image => image.complete && image.naturalWidth > 0),
        anchors: [...document.querySelectorAll('a[href^="#"]')].every(a => Boolean(document.getElementById(a.getAttribute('href').slice(1)))),
        navCount: document.querySelectorAll('.mv2-ps-nav-button').length,
        heroFont: getComputedStyle(document.querySelector('h1')).fontSize,
        mainTextFont: getComputedStyle(document.querySelector('.mv2-ps-view-heading p:not(.mv2-ps-overline)')).fontSize,
        requests: performance.getEntriesByType('resource').map(r => r.name),
      }));
      check(initial.pageWidth <= width, 'No horizontal overflow', initial.pageWidth);
      check(headers['x-robots-tag']?.includes('noindex') && await page.locator('meta[name="robots"]').getAttribute('content') === 'noindex, nofollow, noarchive', `${width}: noindex`, headers['x-robots-tag']);
      check(headers['content-security-policy']?.includes("connect-src 'none'") && headers['content-security-policy']?.includes("form-action 'none'") && !/unsafe-(?:inline|eval)/.test(headers['content-security-policy']), `${width}: restrictive CSP`, headers['content-security-policy']);
      check(initial.navCount === 6 && initial.anchors && initial.logoLoaded, `${width}: SSR sections, anchors and logo`, initial);
      check(await page.evaluate(() => document.documentElement.classList.contains('mv2-js')), `${width}: early progressive-enhancement bootstrap`, await page.evaluate(() => document.documentElement.className));
      check(!initial.requests.some(request => /^https?:/.test(request) && new URL(request).origin !== origin), `${width}: no external network requests`, initial.requests);
      const accessibility = await axeRun(page);
      check(accessibility.violations.length === 0, `${width}: Axe WCAG A/AA`, accessibility.violations.map(item => ({ id: item.id, impact: item.impact, targets: item.nodes.map(node => node.target) })));
      check((await page.evaluate(() => window.__mv2Csp)).length === 0, `${width}: no CSP violations`, await page.evaluate(() => window.__mv2Csp));
      if (width === 1440 || width === 768 || width === 390) {
        await page.screenshot({ path: path.join(out, `phase2-${width}-viewport.png`) });
        await page.locator('#mv2-inizio').screenshot({ path: path.join(out, `phase2-${width}-hero.png`) });
      }
      results.viewports.push({ width, height, initial, axeViolations: accessibility.violations.length });
      await context.close();
    }

    const desktop = await browser.newPage({ viewport: { width: 1440, height: 1000 } }); await open(desktop, 'views');
    let mainFrameCount = 0; desktop.on('framenavigated', frame => { if (frame === desktop.mainFrame()) mainFrameCount++; });
    for (const label of labels) {
      const button = desktop.getByRole('button', { name: new RegExp(label) });
      await button.click();
      await desktop.getByRole('heading', { name: label, exact: true }).waitFor();
      check(await button.getAttribute('aria-pressed') === 'true', `${label}: aria-pressed selected`);
      check(mainFrameCount === 0, `${label}: view switch does not navigate`, desktop.url());
      if (['Inbox', 'Prenotazioni', 'Recensioni', 'Knowledge Base', 'Team'].includes(label)) await desktop.locator('#mv2-inizio').screenshot({ path: path.join(out, `phase2-view-${label.toLowerCase().replaceAll(' ','-')}.png`) });
      results.views.push(label);
    }
    await desktop.getByRole('button', { name: /Inbox/ }).focus(); await desktop.keyboard.press('Enter');
    check(await desktop.getByRole('button', { name: /Inbox/ }).getAttribute('aria-pressed') === 'true', 'Keyboard activation selects a view');
    check(await desktop.evaluate(() => document.activeElement?.classList.contains('mv2-ps-nav-button')), 'Keyboard focus remains on navigation');
    await desktop.close();

    const mobile = await browser.newPage({ viewport: { width: 390, height: 900 } }); await open(mobile, 'mobile');
    const selector = mobile.locator('#mv2-ps-view-select');
    check(await selector.locator('option').count() === 6, 'Mobile selector exposes all six views');
    check((await selector.boundingBox()).height >= 44, 'Mobile selector touch target is at least 44px');
    await selector.selectOption('inbox'); await mobile.getByRole('heading', { name: 'Inbox', exact: true }).waitFor();
    await mobile.locator('#mv2-inizio').screenshot({ path: path.join(out, 'phase2-mobile-inbox.png') });
    await selector.selectOption('bookings'); await mobile.getByRole('heading', { name: 'Prenotazioni', exact: true }).waitFor();
    await mobile.locator('#mv2-inizio').screenshot({ path: path.join(out, 'phase2-mobile-prenotazioni.png') });
    await selector.selectOption('knowledge'); await mobile.getByRole('heading', { name: 'Knowledge Base', exact: true }).waitFor();
    await mobile.locator('#mv2-inizio').screenshot({ path: path.join(out, 'phase2-mobile-knowledge-base.png') });
    await mobile.close();

    const noJs = await browser.newPage({ viewport: { width: 390, height: 900 }, javaScriptEnabled: false }); await open(noJs, 'no-js');
    check(await noJs.locator('h1').isVisible() && await noJs.locator('.mv2-ps-overview').isVisible(), 'No-JS fallback shows readable hero and overview');
    check(await noJs.locator('.mv2-ps-nav-button').first().isDisabled() && await noJs.locator('noscript').isVisible(), 'No-JS navigation is disabled with an explanation');
    check(await noJs.locator('#mv2-nav').isVisible() && !(await noJs.locator('.mv2-menu-toggle').isVisible()), 'No-JS mobile navigation remains expanded without layout-dependent enhancement');
    await noJs.close();

    const reduced = await browser.newPage({ viewport: { width: 390, height: 900 }, reducedMotion: 'reduce' }); await open(reduced, 'reduced-motion');
    check(await reduced.locator('.mv2-ps-view').evaluate(el => getComputedStyle(el).animationName === 'none'), 'Reduced-motion preference disables view entrance');
    check(await reduced.evaluate(() => window.__mv2Csp.length === 0), 'Reduced-motion state has no CSP violation');
    await reduced.close();
  } catch (error) { results.errors.push(String(error.stack || error)); }
  finally {
    if (browser) await browser.close(); if (server) server.kill();
    results.passed = results.failures.length === 0 && results.errors.length === 0 && results.externalRequests.length === 0;
    await fs.writeFile(path.join(out, 'browser-qa.json'), JSON.stringify(results, null, 2));
    console.log(JSON.stringify({ passed: results.passed, viewports: results.viewports.length, views: results.views, failures: results.failures, errors: results.errors, externalRequests: results.externalRequests, artifacts: out }, null, 2));
    if (!results.passed) process.exitCode = 1;
  }
})();
