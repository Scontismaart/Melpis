/* Loopback-only V2 browser QA. Captures are local review artifacts, not shipped assets. */
const { chromium } = require('playwright');
const axe = require('axe-core');
const fs = require('node:fs/promises');
const path = require('node:path');
const { spawn } = require('node:child_process');
const root = path.resolve(__dirname, '..');
const out = path.join(root, 'artifacts', 'phase3-review');
const url = process.env.V2_QA_URL || 'http://127.0.0.1:4175/landing-v2/';
const origin = new URL(url).origin;
if (!['127.0.0.1', 'localhost', '[::1]'].includes(new URL(url).hostname)) throw new Error('Browser QA accepts loopback previews only.');
const results = { generatedAt: new Date().toISOString(), url, browser: null, viewports: [], views: [], errors: [], externalRequests: [], unexpectedRequests: [], failures: [] };
const labels = ['Panoramica', 'Inbox', 'Prenotazioni', 'Recensioni', 'Knowledge Base', 'Team'];
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
      {
        await page.screenshot({ path: path.join(out, `phase3-followup-${width}-viewport.png`) });
        await page.locator('.mv2-hero-stage').screenshot({ path: path.join(out, `phase3-followup-${width}-hero-stage.png`) });
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
      if (['Panoramica', 'Inbox', 'Prenotazioni', 'Recensioni', 'Knowledge Base', 'Team'].includes(label)) await desktop.locator('.mv2-hero-stage').screenshot({ path: path.join(out, `phase3-followup-view-${label.toLowerCase().replaceAll(' ','-')}.png`) });
      results.views.push(label);
    }
    await desktop.getByRole('button', { name: /Inbox/ }).focus(); await desktop.keyboard.press('Enter');
    check(await desktop.getByRole('button', { name: /Inbox/ }).getAttribute('aria-pressed') === 'true', 'Keyboard activation selects a view');
    check(await desktop.evaluate(() => document.activeElement?.classList.contains('mv2-ps-nav-button')), 'Keyboard focus remains on navigation');
    const beforeStory = await desktop.evaluate(() => ({ storage: localStorage.length, session: sessionStorage.length }));
    const story = desktop.locator('.mv2-story-demo');
    await story.scrollIntoViewIfNeeded();
    await desktop.waitForFunction(() => !document.querySelector('#mv2-story-scenario')?.disabled);
    const scenarioSelect = desktop.locator('#mv2-story-scenario');
    check(await scenarioSelect.boundingBox().then(box => box.height >= 44), 'Story scenario selector touch target is at least 44px');
    const storyButtons = story.getByRole('button');
    check(await storyButtons.count() === 3, 'Story has only the three necessary manual controls');
    check(await story.locator('.mv2-story-chapters [aria-current="step"]').count() === 1, 'Story chapter rail marks exactly one current step');
    await desktop.locator('#mv2-percorso').screenshot({ path: path.join(out, 'phase3-followup-story-available-initial-1440-full.png') });
    await story.getByRole('button', { name: 'Avvia la demo' }).click();
    await story.getByText('Informazioni consultate', { exact: true }).waitFor();
    check(await story.locator('.mv2-story-chapters [data-complete="true"]').count() === 1, 'Completed story chapter is reflected in the non-interactive rail');
    await story.getByRole('button', { name: 'Passaggio successivo' }).click();
    await story.getByText('Disponibilità verificata', { exact: true }).waitFor();
    check(await story.locator('.mv2-story-availability').getByText(/2 disponibili/).isVisible(), 'Positive scenario verifies capacity before confirmation');
    check(/Martedì 13 ottobre 2026[\s\S]*19:30[\s\S]*2 disponibili/.test(await story.locator('.mv2-story-availability').innerText()), 'Availability panel exposes day, time and synthetic capacity');
    check(await story.locator('.mv2-story-response-bubble').getByText(/Vuoi confermare per due/).isVisible(), 'Positive assistant asks for confirmation before customer confirmation');
    await desktop.locator('#mv2-percorso').screenshot({ path: path.join(out, 'phase3-followup-story-available-intermediate-1440-full.png') });
    await story.getByRole('button', { name: 'Passaggio successivo' }).click();
    await story.getByText('Conferma della cliente', { exact: true }).waitFor();
    check(await story.locator('.mv2-story-confirmation-bubble').getByText('«Confermo, grazie.»').isVisible(), 'Positive scenario requires an explicit customer confirmation');
    check(await story.locator('.mv2-story-response-bubble').getByText(/Conferma ricevuta/).isVisible(), 'Assistant responds to confirmation in chronological order');
    check(!(await story.locator('.mv2-story-record').count()), 'No booking appears before the application guard passes');
    await story.getByRole('button', { name: 'Passaggio successivo' }).click();
    await story.getByText('Prenotazione verificata', { exact: true }).waitFor();
    check(!(await story.locator('.mv2-story-record').count()), 'Application validation precedes the illustrative panel record');
    await story.getByRole('button', { name: 'Passaggio successivo' }).click();
    check(await story.locator('.mv2-story-record').getByText('Confermata nell’esempio').isVisible(), 'Positive scenario records the illustrative booking only after confirmation');
    check(!(await story.locator('.mv2-story-availability').count()), 'Final positive state replaces the temporary availability card with the booking row');
    check(/Cliente demo · 2 persone[\s\S]*Martedì 13 ottobre 2026 · 19:30[\s\S]*Conversazione in Inbox/.test(await story.locator('.mv2-story-record').innerText()), 'Illustrative booking row includes customer, date, time and inbox context');
    await story.screenshot({ path: path.join(out, 'phase3-followup-story-positive-viewport.png') });
    await scenarioSelect.selectOption('unavailable');
    await story.getByText('Richiesta ricevuta', { exact: true }).waitFor();
    await story.getByRole('button', { name: 'Avvia la demo' }).click();
    await story.getByText('Disponibilità verificata', { exact: true }).waitFor();
    check(await story.locator('.mv2-story-availability').getByText(/0 disponibili/).isVisible(), 'Unavailable scenario reports zero capacity');
    await story.getByRole('button', { name: 'Passaggio successivo' }).click();
    check(await story.getByText(/Prenotazione non creata/).isVisible(), 'Unavailable scenario never creates a booking');
    check(/0 disponibili/.test(await story.locator('.mv2-story-availability').innerText()), 'Unavailable capacity panel shows zero slots');
    check(!(await story.locator('.mv2-story-record').count()), 'Unavailable scenario has no booking row');
    await story.screenshot({ path: path.join(out, 'phase3-followup-story-unavailable-viewport.png') });
    await scenarioSelect.selectOption('team');
    await story.getByRole('button', { name: 'Avvia la demo' }).click();
    await story.getByText('Informazioni non sufficienti', { exact: true }).waitFor();
    await story.getByRole('button', { name: 'Passaggio successivo' }).click();
    check(await story.locator('.mv2-story-team-record').getByText('Da prendere in carico').isVisible(), 'Missing knowledge routes the conversation to team follow-up');
    check(!(await story.locator('.mv2-story-record').count()), 'Team scenario has no booking row');
    await story.screenshot({ path: path.join(out, 'phase3-followup-story-team-viewport.png') });
    await story.getByRole('button', { name: 'Indietro' }).click();
    await story.getByRole('button', { name: 'Ricomincia' }).click();
    check(await story.getByText('Passaggio 1 di 3').isVisible(), 'Story resets and can be replayed');
    await scenarioSelect.focus(); await desktop.keyboard.press('Tab');
    check(await desktop.evaluate(() => document.activeElement?.tagName === 'BUTTON'), 'Story controls remain keyboard reachable');
    const storyAxe = await axeRun(desktop);
    check(storyAxe.violations.length === 0, 'Story states: Axe WCAG A/AA', storyAxe.violations.map(item => ({ id: item.id, nodes: item.nodes.map(node => node.target) })));
    const afterStory = await desktop.evaluate(() => ({ storage: localStorage.length, session: sessionStorage.length }));
    check(afterStory.storage === beforeStory.storage && afterStory.session === beforeStory.session, 'Story uses no browser persistence');
    check(!results.errors.some(error => /4175/.test(error)), 'Story scenario replay has no browser errors');
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
    check(await noJs.locator('.mv2-story-fallback').innerText().then(text => /2 posti disponibili[\s\S]*Nessun posto disponibile[\s\S]*0 posti disponibili[\s\S]*Informazione da verificare/.test(text)), 'No-JS story fallback explains all three outcomes');
    check(!(await noJs.locator('.mv2-story-demo').isVisible()), 'No-JS view uses only the complete static story fallback');
    check(await noJs.locator('.mv2-ps-nav-button').first().isDisabled() && await noJs.locator('.mv2-ps-content noscript').isVisible(), 'No-JS navigation is disabled with an explanation');
    check(await noJs.locator('#mv2-nav').isVisible() && !(await noJs.locator('.mv2-menu-toggle').isVisible()), 'No-JS mobile navigation remains expanded without layout-dependent enhancement');
    await noJs.close();

    const reduced = await browser.newPage({ viewport: { width: 390, height: 900 }, reducedMotion: 'reduce' }); await open(reduced, 'reduced-motion');
    check(await reduced.locator('.mv2-ps-view').evaluate(el => getComputedStyle(el).animationName === 'none'), 'Reduced-motion preference disables view entrance');
    await reduced.locator('.mv2-story-demo').scrollIntoViewIfNeeded();
    await reduced.waitForFunction(() => !document.querySelector('#mv2-story-scenario')?.disabled);
    await reduced.locator('#mv2-story-scenario').selectOption('available');
    check(await reduced.locator('.mv2-story-chapters li').first().evaluate(el => getComputedStyle(el).transitionDuration === '0s'), 'Reduced motion keeps chapter progress static');
    await reduced.getByRole('button', { name: 'Avvia la demo' }).click();
    await wait(350);
    check(await reduced.locator('.mv2-story-response-bubble').evaluate(el => getComputedStyle(el).transform === 'none' && getComputedStyle(el).opacity === '1'), 'Reduced motion leaves story content static and fully visible');
    await reduced.emulateMedia({ reducedMotion: 'no-preference' });
    await reduced.getByRole('button', { name: 'Passaggio successivo' }).click();
    await reduced.emulateMedia({ reducedMotion: 'reduce' });
    await wait(350);
    check(await reduced.locator('.mv2-story-operation-content').evaluate(el => getComputedStyle(el).transform === 'none' && getComputedStyle(el).opacity === '1'), 'Changing reduced motion clears story animation styles');
    check(await reduced.evaluate(() => window.__mv2Csp.length === 0), 'Reduced-motion state has no CSP violation');
    await reduced.close();
  } catch (error) { results.errors.push(String(error.stack || error)); }
  finally {
    if (browser) await browser.close(); if (server) server.kill();
    results.passed = results.failures.length === 0 && results.errors.length === 0 && results.externalRequests.length === 0 && results.unexpectedRequests.length === 0;
    await fs.writeFile(path.join(out, 'browser-qa.json'), JSON.stringify(results, null, 2));
    console.log(JSON.stringify({ passed: results.passed, viewports: results.viewports.length, views: results.views, failures: results.failures, errors: results.errors, externalRequests: results.externalRequests, unexpectedRequests: results.unexpectedRequests, artifacts: out }, null, 2));
    if (!results.passed) process.exitCode = 1;
  }
})();
