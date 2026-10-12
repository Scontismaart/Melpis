/* Local, read-only browser audit. Runtime code and protected V1 files are never written. */
const { chromium } = require('playwright');
const axe = require('axe-core');
const fs = require('node:fs/promises');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { createHash } = require('node:crypto');
const root = path.resolve(__dirname, '..');
const out = path.join(root, 'artifacts', 'browser-qa');
const url = process.env.V2_QA_URL || 'http://127.0.0.1:4176/landing-v2/';
const allowedOrigin = new URL(url).origin;
if (!['127.0.0.1', 'localhost', '[::1]'].includes(new URL(url).hostname)) throw new Error('Browser QA only accepts a loopback preview URL.');
const report = { generatedAt: new Date().toISOString(), url, browserChannel: process.env.V2_BROWSER_CHANNEL || 'bundled chromium', checks: [], viewports: [], errors: [], limitations: ['Mobile viewport emulation, not physical-device validation.', 'Axe and focus checks do not replace a screen-reader audit.', 'No trusted V1 pixel baseline: visual regression comparison is inconclusive.', 'No field performance or Core Web Vitals claims.'] };
function check(name, pass, evidence) { report.checks.push({ name, pass: Boolean(pass), evidence }); }
async function sourceHashes() { const files = []; async function walk(dir) { for (const entry of await fs.readdir(dir, { withFileTypes: true })) { const file = path.join(dir, entry.name); if (entry.isDirectory()) await walk(file); else files.push(file); } } await walk(path.join(root, 'dist')); return Object.fromEntries(await Promise.all(files.map(async file => [path.relative(path.join(root, 'dist'), file).replaceAll('\\', '/'), createHash('sha256').update(await fs.readFile(file)).digest('hex')]))); }
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function startPreview() {
  if (process.env.V2_QA_URL) return null;
  const child = spawn(process.execPath, ['tools/preview.mjs'], { cwd: root, env: { ...process.env, MV2_PORT: '4176' }, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  let logs = ''; child.stdout.on('data', c => logs += c); child.stderr.on('data', c => logs += c);
  for (let i = 0; i < 80; i++) {
    if (child.exitCode !== null) throw new Error(`Preview startup failed: ${logs}`);
    if (logs.includes('Preview V2:')) return child;
    await delay(100);
  }
  child.kill(); throw new Error(`Preview readiness timeout: ${logs}`);
}
async function watch(page, label) {
  const events = { consoleErrors: [], pageErrors: [], badResponses: [], failedRequests: [], externalRequests: [], csp: [] };
  page.on('console', msg => { if (msg.type() === 'error') events.consoleErrors.push(msg.text()); });
  page.on('pageerror', err => events.pageErrors.push(String(err)));
  page.on('response', res => { if (res.status() >= 400) events.badResponses.push({ url: res.url(), status: res.status() }); });
  page.on('requestfailed', req => events.failedRequests.push({ url: req.url(), error: req.failure()?.errorText }));
  await page.route('**/*', route => {
    const requested = new URL(route.request().url());
    if (requested.protocol === 'http:' || requested.protocol === 'https:') {
      if (requested.origin !== allowedOrigin) { events.externalRequests.push(requested.href); return route.abort(); }
    }
    return route.continue();
  });
  await page.addInitScript(() => {
    window.__mv2QaCsp = [];
    document.addEventListener('securitypolicyviolation', e => window.__mv2QaCsp.push({ directive: e.violatedDirective, blockedURI: e.blockedURI }));
  });
  return { events, label };
}
async function geometry(page) {
  return page.evaluate(() => ({ width: innerWidth, documentWidth: document.documentElement.scrollWidth, overflow: document.documentElement.scrollWidth - innerWidth, height: document.documentElement.scrollHeight, images: [...document.images].map(i => ({ src: i.getAttribute('src'), broken: i.complete && !i.naturalWidth })), anchors: [...document.querySelectorAll('a')].map(a => ({ href: a.getAttribute('href'), text: a.textContent.trim(), valid: a.getAttribute('href')?.startsWith('#') && Boolean(document.getElementById(a.getAttribute('href').slice(1))) })), fonts: [...document.fonts].map(f => ({ family: f.family, status: f.status })), resources: performance.getEntriesByType('resource').map(r => ({ name: r.name, type: r.initiatorType })) }));
}
async function keyboard(page, label) {
  await page.goto(url); await page.evaluate(() => document.fonts.ready); const seen = []; let first;
  for (let i = 0; i < 100; i++) {
    await page.keyboard.press('Tab');
    const item = await page.evaluate(() => {
      const el = document.activeElement, s = getComputedStyle(el), r = el.getBoundingClientRect();
      return { tag: el.tagName, text: el.textContent.trim().slice(0, 70), href: el.getAttribute('href'), id: el.id, outlineStyle: s.outlineStyle, outlineWidth: parseFloat(s.outlineWidth), focusVisible: el.matches(':focus-visible'), visible: r.width > 0 && r.height > 0 && s.visibility !== 'hidden', signature: `${el.tagName}|${el.id}|${el.getAttribute('href')}|${el.textContent.trim()}` };
    });
    if (item.tag === 'BODY' && seen.length > 0) break; // Tab may leave page for browser chrome at the document boundary.
    if (!first) first = item.signature; else if (item.signature === first) break;
    seen.push(item);
  }
  check(`${label}: keyboard focus indicator on all tab stops`, seen.length > 4 && seen.every(i => i.tag !== 'BODY' && i.visible && i.focusVisible && i.outlineWidth > 0 && i.outlineStyle !== 'none'), seen);
  return seen;
}
(async () => {
  await fs.mkdir(out, { recursive: true }); let server, browser;
  try {
    report.sourceHashesBefore = await sourceHashes();
    server = await startPreview();
    browser = await chromium.launch({ headless: true, ...(process.env.V2_BROWSER_CHANNEL ? { channel: process.env.V2_BROWSER_CHANNEL } : {}) });
    for (const width of [1440, 1024, 768, 390, 360, 320]) {
      const context = await browser.newContext({ viewport: { width, height: 900 }, reducedMotion: 'no-preference' });
      const page = await context.newPage(), watched = await watch(page, `${width}`);
      const response = await page.goto(url, { waitUntil: 'networkidle' }); await page.evaluate(() => document.fonts.ready);
      const meta = await page.locator('meta[name="robots"]').getAttribute('content'), headers = response.headers();
      check(`${width}: noindex meta + HTTP header`, meta?.includes('noindex') && headers['x-robots-tag']?.includes('noindex'), { meta, header: headers['x-robots-tag'] });
      check(`${width}: restrictive preview CSP`, headers['content-security-policy']?.includes("connect-src 'none'") && headers['content-security-policy']?.includes("form-action 'none'") && !headers['content-security-policy']?.includes('unsafe-'), headers['content-security-policy']);
      const initial = await geometry(page);
      const island = page.locator('.mv2-control-options');
      check(`${width}: SSR renders initial team state without eager hydration`, await island.locator('button').nth(0).getAttribute('aria-pressed') === 'false' && await island.locator('button').nth(0).isDisabled() && await island.locator('button').nth(1).getAttribute('aria-pressed') === 'true', 'Buttons are server-rendered with the original active team state and remain disabled before visibility.');
      await island.scrollIntoViewIfNeeded();
      await page.waitForFunction(() => { const b = document.querySelector('.mv2-control-options button'); return b && !b.disabled; });
      await island.getByRole('button', { name: /Richiesta fuori dalle informazioni/ }).click();
      check(`${width}: visible island hydrates and changes state`, await island.locator('button').nth(0).getAttribute('aria-pressed') === 'true' && /Richiesta in attesa/.test(await page.locator('.mv2-control-footer').innerText()), 'Visible client island enables interaction and updates its live status.');
      await island.getByRole('button', { name: /Team in conversazione/ }).click();
      check(`${width}: state returns to the original team selection`, await island.locator('button').nth(1).getAttribute('aria-pressed') === 'true' && /Passaggio visibile/.test(await page.locator('.mv2-control-footer').innerText()), 'State can be returned to the initial selection.');
      const pricing = await page.locator('#mv2-piani').innerText();
      check(`${width}: reference monthly prices and internal configuration CTAs`, ['29', '69', '149'].every(price => pricing.includes(price)) && /mensile|mese/i.test(pricing) && await page.locator('#mv2-piani a[href="#mv2-setup"]').count() === 3, pricing);
      check(`${width}: no document overflow`, initial.overflow <= 0, initial);
      await page.screenshot({ path: path.join(out, `v2-${width}-hero.png`) });
      await page.screenshot({ path: path.join(out, `v2-${width}-full.png`), fullPage: true });
      check(`${width}: all anchors have existing internal targets`, initial.anchors.length > 5 && initial.anchors.every(a => a.valid), initial.anchors);
      check(`${width}: only V2 same-origin browser assets`, initial.resources.length > 2 && initial.resources.every(r => new URL(r.name).origin === allowedOrigin && new URL(r.name).pathname.startsWith('/landing-v2/')), initial.resources);
      check(`${width}: two self-hosted fonts loaded`, new Set(initial.fonts.filter(f => f.status === 'loaded').map(f => f.family)).size >= 2 && initial.resources.filter(r => r.name.endsWith('.woff2')).length >= 2, initial.fonts);
      check(`${width}: demo is labelled synthetic, no form`, await page.locator('body').innerText().then(t => /Esempio sintetico|dati fittizi/.test(t)) && await page.locator('form').count() === 0, 'Static illustrative data; no form submission.');
      const toggle = page.locator('.mv2-menu-toggle'), nav = page.locator('#mv2-nav');
      if (width <= 680) {
        check(`${width}: mobile menu starts closed`, await toggle.getAttribute('aria-expanded') === 'false' && !await nav.isVisible());
        await toggle.focus(); await page.keyboard.press('Enter');
        check(`${width}: keyboard opens mobile menu`, await toggle.getAttribute('aria-expanded') === 'true' && await nav.isVisible());
        const menuFocus = [];
        for (let i = 0; i < await nav.locator('a').count(); i++) {
          await page.keyboard.press('Tab');
          menuFocus.push(await page.evaluate(() => ({ text: document.activeElement.textContent.trim(), inNav: document.activeElement.closest('#mv2-nav') !== null, focusVisible: document.activeElement.matches(':focus-visible'), outline: parseFloat(getComputedStyle(document.activeElement).outlineWidth) })));
        }
        check(`${width}: keyboard reaches all menu links with visible focus`, menuFocus.every(f => f.inNav && f.focusVisible && f.outline > 0), menuFocus);
        await page.keyboard.press('Escape');
        check(`${width}: Escape closes and restores toggle focus`, await toggle.getAttribute('aria-expanded') === 'false' && await toggle.evaluate(el => document.activeElement === el));
        await toggle.click(); await nav.locator('a').first().click();
        check(`${width}: menu link click navigates and closes`, new URL(page.url()).hash === '#mv2-percorso' && await toggle.getAttribute('aria-expanded') === 'false');
        await page.evaluate(() => scrollTo(0, 0)); await toggle.click(); await page.setViewportSize({ width: 1024, height: 900 });
        // Viewport acknowledgement precedes matchMedia change delivery on some runners.
        await page.waitForFunction(() => document.querySelector('.mv2-menu-toggle').getAttribute('aria-expanded') === 'false', null, { timeout: 2000 });
        check(`${width}: resize resets open menu`, await toggle.getAttribute('aria-expanded') === 'false' && await nav.isVisible());
        await page.setViewportSize({ width, height: 900 });
        await nav.waitFor({ state: 'hidden', timeout: 2000 });
        check(`${width}: menu remains closed on return to mobile`, !await nav.isVisible());
      } else check(`${width}: desktop navigation reachable`, await nav.isVisible() && !await toggle.isVisible());
      await page.evaluate(axe.source);
      const a11y = await page.evaluate(async () => window.axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'] } }));
      check(`${width}: axe WCAG A/AA violations`, a11y.violations.length === 0, a11y.violations.map(v => ({ id: v.id, impact: v.impact, description: v.description, nodes: v.nodes.map(n => ({ target: n.target, summary: n.failureSummary })) })));
      const focus = await keyboard(page, `${width}`);
      watched.events.csp = await page.evaluate(() => window.__mv2QaCsp || []);
      check(`${width}: clean console/pageerror/network/CSP`, Object.values(watched.events).every(v => v.length === 0), watched.events);
      report.viewports.push({ width, geometry: initial, axeViolations: a11y.violations.length, keyboardStops: focus.length, events: watched.events });
      await context.close();
    }
    for (const width of [1440, 390, 360]) {
      const context = await browser.newContext({ viewport: { width, height: 900 }, javaScriptEnabled: false }); const page = await context.newPage(); await page.goto(url); await page.evaluate(() => document.fonts.ready);
      const links = page.locator('#mv2-nav a'), count = await links.count(); let visible = true;
      for (let i = 0; i < count; i++) { await page.evaluate(() => scrollTo({ top: 0, behavior: 'instant' })); await page.waitForTimeout(100); visible = visible && await links.nth(i).isVisible(); await links.nth(i).click(); const href = await links.nth(i).getAttribute('href'); check(`${width}: JS-off nav target ${i}`, new URL(page.url()).hash === href); }
      check(`${width}: JS-off navigation exposed and valid`, visible && count >= 4 && (await geometry(page)).overflow <= 0);
      const noJsPositions = await page.evaluate(() => ({ navBottom: document.querySelector('#mv2-nav').getBoundingClientRect().bottom, headingTop: document.querySelector('h1').getBoundingClientRect().top }));
      check(`${width}: JS-off navigation does not obscure hero`, noJsPositions.navBottom <= noJsPositions.headingTop, noJsPositions);
      await page.evaluate(() => scrollTo({ top: 0, behavior: 'instant' })); await page.waitForTimeout(100); await page.screenshot({ path: path.join(out, `v2-${width}-no-js.png`) }); await context.close();
    }
    const context = await browser.newContext({ viewport: { width: 390, height: 900 }, reducedMotion: 'reduce' }), page = await context.newPage(); await page.goto(url);
    const motion = await page.evaluate(() => ({ animations: document.getAnimations().length, elements: [...document.querySelectorAll('body,body *')].flatMap(el => ['', '::before', '::after'].map(pseudo => { const s = getComputedStyle(el, pseudo || null); return { tag: el.tagName, pseudo, transition: s.transitionDuration, animation: s.animationDuration, animationName: s.animationName }; })).filter(e => e.transition.split(',').some(v => parseFloat(v) !== 0) || e.animation.split(',').some(v => parseFloat(v) !== 0)) }));
    check('reduced motion: zero duration and no active animations', motion.animations === 0 && motion.elements.length === 0, motion); await context.close();
    // 1280 CSS-pixel desktop at 400% browser zoom corresponds to a 320px layout viewport.
    const reflow = await browser.newPage({ viewport: { width: 320, height: 900 } }); await reflow.goto(url); const reflowGeometry = await geometry(reflow); check('320px reflow equivalent: no horizontal document overflow', reflowGeometry.overflow <= 0, reflowGeometry); await reflow.close();
    // Adversarial coexistence: even an accidental import must not mutate or restyle
    // a document that does not opt into the V2 namespace. No protected V1 file is edited.
    const isolatedContext = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const isolatedPage = await isolatedContext.newPage();
    await isolatedPage.route('**/landing-v2/isolation-fixture', route => route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><html lang="it"><head><style>body{margin:11px;color:rgb(40,50,60);background:rgb(235,236,237);font:18px/1.4 Arial}button{padding:8px;border:2px solid rgb(40,50,60)}</style></head><body class="v1-sentinel"><main><h1>V1 isolation fixture</h1><button type="button">Existing control</button></main></body></html>' }));
    await isolatedPage.goto(`${allowedOrigin}/landing-v2/isolation-fixture`);
    const sentinel = () => isolatedPage.evaluate(() => {
      const body = getComputedStyle(document.body), button = getComputedStyle(document.querySelector('button'));
      return { html: document.body.outerHTML, margin: body.margin, color: body.color, background: body.backgroundColor, font: body.fontFamily, size: body.fontSize, boxSizing: body.boxSizing, buttonPadding: button.padding, buttonBorder: button.borderWidth };
    });
    const beforeImport = await sentinel();
    const v2Css = (await fs.readdir(path.join(root, 'dist', '_astro'))).find(f => f.endsWith('.css'));
    if (v2Css) await isolatedPage.addStyleTag({ url: `${allowedOrigin}/landing-v2/_astro/${v2Css}` });
    await isolatedPage.keyboard.press('Escape');
    await isolatedPage.keyboard.press('Escape');
    check('V2 CSS and module do not affect an unopted V1 document', JSON.stringify(beforeImport) === JSON.stringify(await sentinel()), beforeImport);
    await isolatedContext.close();
  } catch (error) { report.errors.push(String(error.stack || error)); }
  finally { if (browser) await browser.close(); if (server) server.kill(); report.sourceHashes = await sourceHashes(); check('Stable build throughout audit', JSON.stringify(report.sourceHashesBefore) === JSON.stringify(report.sourceHashes), report.sourceHashes); report.passed = report.errors.length === 0 && report.checks.every(c => c.pass); await fs.writeFile(path.join(out, 'results.json'), JSON.stringify(report, null, 2)); console.log(JSON.stringify({ passed: report.passed, checks: report.checks.length, failed: report.checks.filter(c => !c.pass).map(c => c.name), errors: report.errors, artifactDirectory: out }, null, 2)); if (!report.passed) process.exitCode = 1; }
})();
