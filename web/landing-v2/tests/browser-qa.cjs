/* Loopback-only V2 browser QA. Captures are local review artifacts, not shipped assets. */
const { chromium } = require('playwright');
const axe = require('axe-core');
const fs = require('node:fs/promises');
const path = require('node:path');
const { spawn } = require('node:child_process');
const root = path.resolve(__dirname, '..');
const out = path.join(root, 'artifacts', 'integrated-home');
const url = process.env.V2_QA_URL || 'http://127.0.0.1:4175/landing-v2/';
const origin = new URL(url).origin;
if (!['127.0.0.1', 'localhost', '[::1]'].includes(new URL(url).hostname)) throw new Error('Browser QA accepts loopback previews only.');
const results = { generatedAt: new Date().toISOString(), url, browser: null, viewports: [], views: [], errors: [], externalRequests: [], unexpectedRequests: [], failures: [] };
const labels = ['Panoramica', 'Inbox', 'Prenotazioni', 'Recensioni', 'Simulatore AI'];
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
function check(pass, name, evidence) { if (!pass) results.failures.push({ name, evidence }); }
async function startPreview() {
  if (process.env.V2_QA_URL) return null;
  const child = spawn(process.execPath, ['tools/preview.mjs'], { cwd: root, env: { ...process.env, MV2_PORT: '4175' }, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  let log = ''; child.stdout.on('data', c => log += c); child.stderr.on('data', c => log += c);
  for (let i = 0; i < 100; i++) { if (child.exitCode !== null) throw new Error(`Preview exited: ${log}`); if (log.includes('Preview V2:')) return child; await wait(100); }
  child.kill(); throw new Error(`Preview startup timeout: ${log}`);
}
async function monitor(page, width) {
  page.on('console', msg => { if (msg.type() === 'error') results.errors.push(`${width}: ${msg.text()}`); });
  page.on('pageerror', error => results.errors.push(`${width}: ${String(error)}`));
  page.on('requestfailed', request => results.errors.push(`${width}: ${request.url()} ${request.failure()?.errorText}`));
  page.on('response', response => { if (response.status() >= 400) results.errors.push(`${width}: HTTP ${response.status()} ${response.url()}`); });
  page.on('request', request => {
    if (new URL(request.url()).origin !== origin) results.externalRequests.push(request.url());
    if (['fetch','xhr'].includes(request.resourceType()) || !['GET','HEAD'].includes(request.method())) results.unexpectedRequests.push({ url: request.url(), method: request.method(), type: request.resourceType() });
  });
  await page.route('**/*', (route) => {
    const request = route.request();
    const target = new URL(request.url());
    return target.origin === origin && ['GET', 'HEAD'].includes(request.method()) && !target.pathname.startsWith('/api/')
      ? route.continue()
      : route.abort();
  });
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
      const height = width <= 390 ? 844 : 900;
      const context = await browser.newContext({ viewport: { width, height }, reducedMotion: 'no-preference' });
      const page = await context.newPage(); const response = await open(page, width); const headers = response.headers();
      const initial = await page.evaluate(() => ({
        width: innerWidth, pageWidth: document.documentElement.scrollWidth, pageHeight: document.documentElement.scrollHeight,
        hero: document.querySelector('#mv2-inizio').getBoundingClientRect().toJSON(), frame: document.querySelector('.mv2-ps-frame').getBoundingClientRect().toJSON(),
        logoLoaded: [...document.querySelectorAll('.mv2-mark')].every(image => image.complete && image.naturalWidth > 0),
        anchors: [...document.querySelectorAll('a[href^="#"]')].every(a => Boolean(document.getElementById(a.getAttribute('href').slice(1)))),
        navCount: document.querySelectorAll('.mv2-ps-nav-button').length,
        heroFont: getComputedStyle(document.querySelector('h1')).fontSize,
        requests: performance.getEntriesByType('resource').map(r => r.name),
      }));
      check(initial.pageWidth <= width, 'No horizontal overflow', initial.pageWidth);
      check(headers['x-robots-tag']?.includes('noindex') && await page.locator('meta[name="robots"]').getAttribute('content') === 'noindex, nofollow, noarchive', `${width}: noindex`, headers['x-robots-tag']);
      check(headers['content-security-policy']?.includes("connect-src 'none'") && headers['content-security-policy']?.includes("form-action 'none'") && !/unsafe-(?:inline|eval)/.test(headers['content-security-policy']), `${width}: restrictive CSP`, headers['content-security-policy']);
      check(initial.navCount === 8 && initial.anchors && initial.logoLoaded, `${width}: SSR sections, anchors and logo`, initial);
      check(await page.evaluate(() => document.documentElement.classList.contains('mv2-js')), `${width}: early progressive-enhancement bootstrap`, await page.evaluate(() => document.documentElement.className));
      check(!initial.requests.some(request => /^https?:/.test(request) && new URL(request).origin !== origin), `${width}: no external network requests`, initial.requests);
      const accessibility = await axeRun(page);
      check(accessibility.violations.length === 0, `${width}: Axe WCAG A/AA`, accessibility.violations.map(item => ({ id: item.id, impact: item.impact, targets: item.nodes.map(node => node.target) })));
      check((await page.evaluate(() => window.__mv2Csp)).length === 0, `${width}: no CSP violations`, await page.evaluate(() => window.__mv2Csp));
      {
        await page.screenshot({ path: path.join(out, `home-${width}.png`) });
        if (width === 1440 || width === 390) await page.locator('.mv2-ps-frame').screenshot({ path: path.join(out, `dashboard-demo-${width}.png`) });
      }
      results.viewports.push({ width, height, initial, axeViolations: accessibility.violations.length });
      await context.close();
    }

    const desktop = await browser.newPage({ viewport: { width: 1440, height: 1000 } }); await open(desktop, 'views');
    let mainFrameCount = 0; desktop.on('framenavigated', frame => { if (frame === desktop.mainFrame()) mainFrameCount++; });
    for (const label of labels) {
      const button = desktop.locator('.mv2-ps-sidebar').getByRole('button', { name: label, exact: true });
      await button.click();
      await desktop.locator('.mv2-ps-frame [aria-live]').filter({ hasText: label === 'Simulatore AI' ? 'Simulatore Assistente AI' : label }).waitFor();
      check(await button.getAttribute('aria-pressed') === 'true', `${label}: aria-pressed selected`);
      check(mainFrameCount === 0, `${label}: view switch does not navigate`, desktop.url());
      if (['Panoramica', 'Inbox', 'Prenotazioni', 'Recensioni', 'Knowledge Base', 'Team'].includes(label)) await desktop.locator('.mv2-hero-stage').screenshot({ path: path.join(out, `phase3-followup-view-${label.toLowerCase().replaceAll(' ','-')}.png`) });
      results.views.push(label);
    }
    await desktop.locator('.mv2-ps-sidebar').getByRole('button', { name: 'Inbox', exact: true }).focus(); await desktop.keyboard.press('Enter');
    check(await desktop.locator('.mv2-ps-sidebar').getByRole('button', { name: 'Inbox', exact: true }).getAttribute('aria-pressed') === 'true', 'Keyboard activation selects a view');
    check(await desktop.evaluate(() => document.activeElement?.classList.contains('mv2-ps-nav-button')), 'Keyboard focus remains on navigation');
    await desktop.close();

    const mobile = await browser.newPage({ viewport: { width: 390, height: 900 }, reducedMotion: 'reduce' });
    await open(mobile, 'mobile');
    const menu = mobile.locator('.mv2-menu-toggle');
    await menu.focus();
    await mobile.keyboard.press('Enter');
    check(await menu.getAttribute('aria-expanded') === 'true', 'Keyboard opens the mobile menu');
    await mobile.keyboard.press('Escape');
    check(await menu.getAttribute('aria-expanded') === 'false' && await menu.evaluate(el => el === document.activeElement), 'Escape restores menu focus');

    const flow = mobile.locator('.mv2-flow-console');
    await flow.scrollIntoViewIfNeeded();
    await mobile.waitForFunction(() => !document.querySelector('.mv2-flow-console')?.closest('astro-island')?.hasAttribute('ssr'));
    await mobile.waitForFunction(() => document.querySelector('.mv2-flow-console')?.dataset.reduced === 'true', null, { timeout: 5000 });
    const beforeFlow = await mobile.evaluate(() => [localStorage.length, sessionStorage.length]);
    const scenarios = flow.locator('.mv2-flow-scenario');
    const outcomes = ['proposta', 'informativo', 'alternativa', 'escalation'];
    for (const [index, outcome] of outcomes.entries()) {
      await scenarios.nth(index).click();
      await flow.locator('.mv2-flow-send').click();
      await flow.locator(`.mv2-flow-outcome[data-kind="${outcome}"]`).waitFor({ timeout: 15000 });
      check(await flow.locator('.mv2-flow-outcome[data-kind="confermata"]').count() === 0, `${outcome}: no confirmed booking`);
      await flow.locator('.mv2-flow-replay').click();
      check(await flow.locator('.mv2-flow-outcome').count() === 0, `${outcome}: reset clears outcome`);
    }
    const afterFlow = await mobile.evaluate(() => [localStorage.length, sessionStorage.length]);
    check(String(beforeFlow) === String(afterFlow), 'Demo uses no browser persistence');
    check((await mobile.evaluate(() => window.__mv2Csp)).length === 0, 'Demo has no CSP violation');
    await mobile.close();

    const noJs = await browser.newPage({ viewport: { width: 390, height: 900 }, javaScriptEnabled: false });
    await open(noJs, 'no-js');
    check(await noJs.locator('h1').isVisible() && await noJs.locator('.mv2-ps-frame').isVisible(), 'No-JS fallback shows hero and dashboard');
    check(await noJs.locator('.mv2-flow-fallback li').count() === 4 && await noJs.locator('.mv2-flow-fallback').isVisible(), 'No-JS fallback explains all four scenarios');
    check(!await noJs.locator('.mv2-flow-stage').isVisible(), 'No-JS fallback does not show inert interactive controls');
    check(await noJs.locator('.mv2-ps-nav-button').first().isDisabled(), 'No-JS dashboard controls are disabled');
    await noJs.close();

    if (process.env.V2_QA_URL) {
      for (const sector of ['ristoranti', 'hotel', 'barbieri', 'saloni']) {
        const page = await browser.newPage({ viewport: { width: 390, height: 844 }, reducedMotion: 'reduce' });
        await monitor(page, sector);
        await page.goto(new URL(`/landing-v2/${sector}/`, origin).href, { waitUntil: 'networkidle' });
        const accessibility = await axeRun(page);
        check(accessibility.violations.length === 0, `${sector}: Axe WCAG A/AA`, accessibility.violations.map(item => ({ id: item.id, targets: item.nodes.map(node => node.target) })));
        check((await page.evaluate(() => window.__mv2Csp)).length === 0, `${sector}: no CSP violations`);
        await page.close();
      }
    }
  } catch (error) { results.errors.push(String(error.stack || error)); }
  finally {
    if (browser) await browser.close(); if (server) server.kill();
    results.passed = results.failures.length === 0 && results.errors.length === 0 && results.externalRequests.length === 0 && results.unexpectedRequests.length === 0;
    await fs.writeFile(path.join(out, 'browser-qa.json'), JSON.stringify(results, null, 2));
    console.log(JSON.stringify({ passed: results.passed, viewports: results.viewports.length, views: results.views, failures: results.failures, errors: results.errors, externalRequests: results.externalRequests, unexpectedRequests: results.unexpectedRequests, artifacts: out }, null, 2));
    if (!results.passed) process.exitCode = 1;
  }
})();
