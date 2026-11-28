const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const ORIGIN = process.env.QA_ORIGIN || 'http://127.0.0.1:4180';
const PAGE = new URL('/piani/', ORIGIN).href;
if (!['127.0.0.1', 'localhost', '[::1]'].includes(new URL(PAGE).hostname)) throw new Error('Pricing QA requires a loopback server.');
const OUT = path.join(__dirname, 'artifacts', 'pricing-qa');

const viewports = [
  { name: '1440x900', width: 1440, height: 900 },
  { name: '1024x768', width: 1024, height: 768 },
  { name: '768x1024', width: 768, height: 1024 },
  { name: '390x844', width: 390, height: 844 },
  { name: '360x844', width: 360, height: 844 },
  { name: '320x844', width: 320, height: 844 },
];

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  const problems = [];
  const note = (message) => console.log(message);

  for (const vp of viewports) {
    const context = await browser.newContext({ viewport: { width: vp.width, height: vp.height } });
    const page = await context.newPage();
    const consoleErrors = [];
    page.on('console', (msg) => { if (msg.type() === 'error') consoleErrors.push(msg.text()); });
    page.on('pageerror', (err) => consoleErrors.push(`pageerror: ${err.message}`));

    page.on('response', response => { if (!response.ok()) consoleErrors.push(`HTTP ${response.status()} ${response.url()}`); });
    await page.route('**/*', (route) => {
      const request = route.request();
      const target = new URL(request.url());
      if (target.origin === new URL(PAGE).origin && ['GET', 'HEAD'].includes(request.method()) && !target.pathname.startsWith('/api/')) return route.continue();
      consoleErrors.push(`Unexpected network request: ${request.method()} ${target.pathname}`);
      return route.abort();
    });
    const response = await page.goto(PAGE, { waitUntil: 'networkidle' });
    const headers = response.headers();
    if (!headers['content-security-policy']?.includes("connect-src 'none'") || !headers['x-robots-tag']?.includes('noindex')) {
      problems.push(`${vp.name}: restrictive CSP or noindex missing`);
    }
    await page.waitForTimeout(250);

    const metrics = await page.evaluate(() => {
      const header = document.querySelector('.mv2-header');
      const subnav = document.querySelector('.mv2-pp-subnav');
      const cards = [...document.querySelectorAll('.mv2-pp-plan')];
      const tops = (selector) => [...document.querySelectorAll(selector)].map((el) => Math.round(el.getBoundingClientRect().top));
      return {
        scrollWidth: document.documentElement.scrollWidth,
        innerWidth: window.innerWidth,
        headerPosition: getComputedStyle(header).position,
        headerHeight: Math.round(header.getBoundingClientRect().height),
        subnavPosition: getComputedStyle(subnav).position,
        subnavHeight: Math.round(subnav.getBoundingClientRect().height),
        planTops: cards.map((el) => Math.round(el.getBoundingClientRect().top)),
        priceTops: tops('.mv2-pp-price'),
        ctaTops: tops('.mv2-pp-plan-cta'),
        featureTops: tops('.mv2-pp-plan-features'),
        headerBg: getComputedStyle(header).backgroundColor,
        bodyBg: getComputedStyle(document.body).backgroundColor,
      };
    });

    if (metrics.scrollWidth > metrics.innerWidth + 1) problems.push(`${vp.name}: horizontal overflow ${metrics.scrollWidth} > ${metrics.innerWidth}`);
    if (metrics.headerPosition !== 'sticky') problems.push(`${vp.name}: header is ${metrics.headerPosition}, expected sticky`);
    if (metrics.subnavPosition !== 'sticky') problems.push(`${vp.name}: subnav is ${metrics.subnavPosition}`);

    if (vp.width >= 1100 && new Set(metrics.priceTops).size !== 1) problems.push(`${vp.name}: price rows not aligned ${metrics.priceTops}`);
    if (vp.width >= 1100 && new Set(metrics.ctaTops).size !== 1) problems.push(`${vp.name}: CTA rows not aligned ${metrics.ctaTops}`);
    if (vp.width >= 1100 && new Set(metrics.featureTops).size !== 1) problems.push(`${vp.name}: feature lists not aligned ${metrics.featureTops}`);

    await page.screenshot({ path: path.join(OUT, `piani-${vp.width}.png`) });

    /* sticky header + subnav + comparison thead */
    await page.evaluate(() => {
      const row = document.querySelector('#mv2-pp-confronto');
      window.scrollTo({ top: row.getBoundingClientRect().top + window.scrollY + 520, behavior: 'instant' });
    });
    await page.waitForTimeout(200);
    const sticky = await page.evaluate(() => {
      const head = document.querySelector('.mv2-header').getBoundingClientRect();
      const sub = document.querySelector('.mv2-pp-subnav').getBoundingClientRect();
      const th = document.querySelector('.mv2-pp-table thead th').getBoundingClientRect();
      return {
        headTop: Math.round(head.top),
        headBottom: Math.round(head.bottom),
        subTop: Math.round(sub.top),
        subBottom: Math.round(sub.bottom),
        thTop: Math.round(th.top),
      };
    });
    if (sticky.headTop !== 0) problems.push(`${vp.name}: header not pinned at 0 (${sticky.headTop})`);
    if (Math.abs(sticky.subTop - sticky.headBottom) > 2) problems.push(`${vp.name}: subnav not under header ${JSON.stringify(sticky)}`);
    if (Math.abs(sticky.thTop - sticky.subBottom) > 3) problems.push(`${vp.name}: thead not below subnav ${JSON.stringify(sticky)}`);

    await page.screenshot({ path: path.join(OUT, `${vp.name}-sticky.png`) });

    /* table release at end */
    await page.evaluate(() => document.querySelector('#mv2-pp-faq').scrollIntoView({ block: 'start', behavior: 'instant' }));
    await page.waitForTimeout(150);
    const released = await page.evaluate(() => Math.round(document.querySelector('.mv2-pp-table thead th').getBoundingClientRect().top));
    if (released >= sticky.thTop - 2) problems.push(`${vp.name}: thead still stuck after table (${released})`);

    if (consoleErrors.length) problems.push(`${vp.name}: console errors ${consoleErrors.join(' | ')}`);
    note(`${vp.name}: ${JSON.stringify({ ...metrics, sticky, released })}`);

    await context.close();
  }

  /* Interaction checks on desktop */
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const errors = [];
  page.on('console', (msg) => { if (msg.type() === 'error') errors.push(msg.text()); });
  page.on('pageerror', (err) => errors.push(`pageerror: ${err.message}`));
  await page.goto(PAGE, { waitUntil: 'networkidle' });

  /* accordion collapse, search inside collapsed group, restore */
  const canali = page.locator('[data-pp-group="canali"]');
  await canali.locator('[data-pp-group-toggle]').click();
  const collapsed = await canali.locator('[data-pp-row][hidden]').count();
  if (collapsed !== 2) problems.push(`accordion: expected 2 hidden rows in Canali, got ${collapsed}`);

  await page.fill('[data-pp-search]', 'documenti');
  await page.waitForTimeout(120);
  const search = await page.evaluate(() => {
    const rows = [...document.querySelectorAll('[data-pp-row]')];
    const visible = rows.filter((r) => !r.hidden).map((r) => r.querySelector('th').textContent.trim());
    const groups = [...document.querySelectorAll('[data-pp-group-row]')].filter((r) => !r.hidden);
    const toggles = [...document.querySelectorAll('[data-pp-group-toggle]')].map((b) => b.disabled);
    const canaliRows = [...document.querySelectorAll('[data-pp-group="canali"] [data-pp-row]')].filter((r) => !r.hidden).length;
    return {
      visible,
      groupCount: groups.length,
      allTogglesDisabled: toggles.every(Boolean),
      canaliRows,
      status: document.querySelector('[data-pp-search-status]').textContent,
      emptyHidden: document.querySelector('[data-pp-empty]').hidden,
    };
  });
  if (search.visible.length !== 1 || !search.visible[0].includes('documenti')) problems.push(`search: unexpected results ${JSON.stringify(search)}`);
  if (!search.allTogglesDisabled) problems.push('search: group toggles must be inert during search');
  if (search.canaliRows !== 0) problems.push('search: non matching group rows must be hidden');
  if (!search.emptyHidden) problems.push('search: empty state visible with results');

  await page.fill('[data-pp-search]', 'disponibilita');
  await page.waitForTimeout(100);
  const accentCount = await page.locator('[data-pp-row]:not([hidden])').count();
  if (accentCount < 1) problems.push('search: accent-insensitive query found nothing');

  await page.click('[data-pp-search-clear]');
  await page.waitForTimeout(120);
  const restored = await page.evaluate(() => {
    const canali = document.querySelector('[data-pp-group="canali"]');
    return {
      expanded: canali.querySelector('[data-pp-group-toggle]').getAttribute('aria-expanded'),
      hiddenRows: [...canali.querySelectorAll('[data-pp-row]')].filter((r) => r.hidden).length,
      status: document.querySelector('[data-pp-search-status]').textContent,
      clearVisible: !document.querySelector('[data-pp-search-clear]').hidden,
      headRows: [...document.querySelectorAll('[data-pp-group-row]')].filter((r) => !r.hidden).length,
    };
  });
  if (restored.expanded !== 'false' || restored.hiddenRows !== 2) problems.push(`search clear did not restore accordion ${JSON.stringify(restored)}`);
  if (restored.clearVisible || restored.status !== '') problems.push(`search clear left UI state ${JSON.stringify(restored)}`);

  /* empty search state */
  await page.fill('[data-pp-search]', 'zzzz');
  await page.waitForTimeout(100);
  const emptyState = await page.evaluate(() => ({
    emptyVisible: !document.querySelector('[data-pp-empty]').hidden,
    visibleRows: document.querySelectorAll('[data-pp-row]:not([hidden])').length,
  }));
  if (!emptyState.emptyVisible || emptyState.visibleRows !== 0) problems.push(`search: empty state wrong ${JSON.stringify(emptyState)}`);
  await page.fill('[data-pp-search]', '');

  /* FAQ tabs: click and keyboard */
  await page.click('#mv2-pp-tab-fatturazione');
  const tabState = await page.evaluate(() => ({
    selected: document.querySelector('#mv2-pp-tab-fatturazione').getAttribute('aria-selected'),
    panelHidden: document.querySelector('#mv2-pp-panel-fatturazione').hidden,
    firstHidden: document.querySelector('#mv2-pp-panel-piani').hidden,
  }));
  if (tabState.selected !== 'true' || tabState.panelHidden || !tabState.firstHidden) problems.push(`FAQ tab click failed ${JSON.stringify(tabState)}`);

  await page.focus('#mv2-pp-tab-fatturazione');
  await page.keyboard.press('ArrowRight');
  const keyboardTab = await page.evaluate(() => ({
    focused: document.activeElement.id,
    selected: document.querySelector('#mv2-pp-tab-configurazione').getAttribute('aria-selected'),
    panelHidden: document.querySelector('#mv2-pp-panel-configurazione').hidden,
  }));
  if (keyboardTab.focused !== 'mv2-pp-tab-configurazione' || keyboardTab.selected !== 'true' || keyboardTab.panelHidden) problems.push(`FAQ keyboard failed ${JSON.stringify(keyboardTab)}`);

  /* subnav: open, Escape closes and restores focus */
  await page.click('[data-pp-subnav-menu] summary');
  const openState = await page.evaluate(() => document.querySelector('[data-pp-subnav-menu]').open);
  await page.keyboard.press('Escape');
  const closedState = await page.evaluate(() => ({
    open: document.querySelector('[data-pp-subnav-menu]').open,
    focused: document.activeElement.tagName,
  }));
  if (!openState || closedState.open || closedState.focused !== 'SUMMARY') problems.push(`subnav escape failed ${JSON.stringify({ openState, closedState })}`);

  /* FAQ details open */
  await page.click('#mv2-pp-panel-configurazione details:first-child summary');
  const detailsOpen = await page.evaluate(() => document.querySelector('#mv2-pp-panel-configurazione details').open);
  if (!detailsOpen) problems.push('FAQ detail did not open');

  if (errors.length) problems.push(`interaction console errors: ${errors.join(' | ')}`);

  /* axe */
  await page.evaluate(require('axe-core').source);
  const axe = await page.evaluate(async () => {
    const result = await window.axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] } });
    return result.violations.map((v) => ({ id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.slice(0, 3).map((n) => n.target.join(' ')) }));
  });
  if (axe.length) problems.push(`axe: ${JSON.stringify(axe)}`);

  /* duplicate ids */
  const dupes = await page.evaluate(() => {
    const ids = [...document.querySelectorAll('[id]')].map((el) => el.id);
    return ids.filter((id, i) => ids.indexOf(id) !== i);
  });
  if (dupes.length) problems.push(`duplicate ids: ${dupes.join(', ')}`);

  await page.screenshot({ path: path.join(OUT, '1440x900-faq.png') });

  await context.close();
  await browser.close();

  console.log('\n=== PROBLEMS ===');
  console.log(problems.length ? problems.join('\n') : 'none');
  process.exitCode = problems.length ? 1 : 0;
})();
