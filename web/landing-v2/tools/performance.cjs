const { chromium } = require('playwright');
const { execFileSync, spawn } = require('node:child_process');
const fs = require('node:fs/promises');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const repositoryRoot = path.resolve(root, '../..');
const out = path.join(root, 'artifacts', 'phase1b-after');
const url = 'http://127.0.0.1:4176/landing-v2/';
const widths = (process.env.V2_PERF_WIDTHS || '1440,1024,768,390,360,320').split(',').map(Number);
const sampleCount = Number(process.env.V2_PERF_SAMPLES || 3);
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const median = xs => [...xs].sort((a, b) => a - b)[Math.floor(xs.length / 2)];
const inlineScriptBytes = html => [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script\s*>/gi)]
  .filter(([, attrs]) => !/\bsrc\s*=/.test(attrs))
  .reduce((sum, [, , script]) => sum + Buffer.byteLength(script, 'utf8'), 0);

(async () => {
  await fs.mkdir(out, { recursive: true });
  const server = spawn(process.execPath, ['tools/preview.mjs'], { cwd: root, env: { ...process.env, MV2_PORT: '4176' }, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  let logs = ''; server.stdout.on('data', chunk => logs += chunk); server.stderr.on('data', chunk => logs += chunk);
  let browser;
  try {
    for (let i = 0; i < 100 && !logs.includes('Preview V2:'); i++) { if (server.exitCode !== null) throw Error(logs); await wait(100); }
    if (!logs.includes('Preview V2:')) throw Error(`Preview readiness timeout: ${logs}`);
    browser = await chromium.launch({ headless: true });
    const sourceHead = execFileSync('git', ['-C', repositoryRoot, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
    const dirty = Boolean(execFileSync('git', ['-C', repositoryRoot, 'status', '--porcelain', '--', 'web/landing-v2', '.github/workflows/landing-v2.yml'], { encoding: 'utf8' }).trim());
    const report = { capturedAt: new Date().toISOString(), source: { head: sourceHead, dirty }, url, engine: browser.version(), conditions: { headless: true, viewportHeight: 900, deviceScaleFactor: 1, cpuThrottleRate: 4, cache: 'fresh context per sample', encoding: 'uncompressed loopback; Content-Length and Resource Timing body sizes, not network wire estimates', samplesPerWidth: sampleCount, phases: 'initial before scrolling; then visible-to-enabled measured with MutationObserver after scroll to client:visible island' }, widths: {} };
    for (const width of widths) {
      const runs = [];
      for (let sample = 0; sample < sampleCount; sample++) {
        const context = await browser.newContext({ viewport: { width, height: 900 }, deviceScaleFactor: 1, reducedMotion: 'no-preference' });
        const page = await context.newPage();
        const cdp = await context.newCDPSession(page); await cdp.send('Emulation.setCPUThrottlingRate', { rate: 4 });
        const sizes = [];
        page.on('response', async response => { try { const parsed = new URL(response.url()); if (parsed.origin !== new URL(url).origin) return; const headers = await response.allHeaders(); sizes.push({ path: parsed.pathname, type: response.request().resourceType(), status: response.status(), bodyBytes: Number(headers['content-length'] || 0) }); } catch {} });
        await page.addInitScript(() => { window.__lcp = 0; window.__cls = 0; window.__shifts = []; new PerformanceObserver(list => { for (const entry of list.getEntries()) window.__lcp = entry.startTime; }).observe({ type: 'largest-contentful-paint', buffered: true }); new PerformanceObserver(list => { for (const entry of list.getEntries()) if (!entry.hadRecentInput) { window.__cls += entry.value; const nav = document.querySelector('#mv2-nav'); window.__shifts.push({ value: entry.value, startTime: entry.startTime, scrollY: window.scrollY, navPosition: nav && getComputedStyle(nav).position, navRect: nav && nav.getBoundingClientRect().toJSON(), sources: entry.sources.map(source => ({ node: source.node?.outerHTML?.slice(0,120), previousRect: source.previousRect, currentRect: source.currentRect })) }); } }).observe({ type: 'layout-shift', buffered: true }); });
        const response = await page.goto(url, { waitUntil: 'networkidle' }); await page.evaluate(() => document.fonts.ready); await wait(750);
        const initial = await page.evaluate(() => { const nav = performance.getEntriesByType('navigation')[0]; const entries = performance.getEntriesByType('resource').map(r => ({ path: new URL(r.name).pathname, type: r.initiatorType, bodyBytes: r.encodedBodySize })); return { lcp: window.__lcp, cls: window.__cls, shifts: window.__shifts, domContentLoaded: nav?.domContentLoadedEventEnd || 0, load: nav?.loadEventEnd || 0, resources: entries }; });
        const initialHeader = Number((await response.allHeaders())['content-length'] || 0);
        const totals = resources => ({ cssBodyBytes: resources.filter(r => r.path.endsWith('.css')).reduce((n, r) => n + r.bodyBytes, 0), jsBodyBytes: resources.filter(r => /\.(?:js|mjs)$/.test(r.path)).reduce((n, r) => n + r.bodyBytes, 0), assetBodyBytes: resources.reduce((n, r) => n + r.bodyBytes, 0), requestCount: resources.length + 1 });
        const html = await response.text();
        const inlineJsBytes = inlineScriptBytes(html);
        const before = { htmlBytes: initialHeader, ...totals(initial.resources), externalJsBytes: totals(initial.resources).jsBodyBytes, inlineJsBytes, resources: initial.resources, shifts: initial.shifts, lcp: initial.lcp, cls: initial.cls, domContentLoaded: initial.domContentLoaded, load: initial.load };
        await page.evaluate(() => {
          const button = document.querySelector('.mv2-control-options button');
          const startedAt = performance.now();
          window.__visibleToEnabled = new Promise(resolve => {
            const observer = new MutationObserver(() => {
              if (!button.disabled) { observer.disconnect(); resolve(performance.now() - startedAt); }
            });
            observer.observe(button, { attributes: true, attributeFilter: ['disabled'] });
            if (!button.disabled) { observer.disconnect(); resolve(0); }
          });
        });
        await page.locator('.mv2-control-options').scrollIntoViewIfNeeded();
        await page.waitForFunction(() => { const control = document.querySelector('.mv2-control-options button'); return control && !control.disabled; });
        const visibleToEnabledMs = await page.evaluate(() => window.__visibleToEnabled);
        const hydrated = await page.evaluate(() => performance.getEntriesByType('resource').map(r => ({ path: new URL(r.name).pathname, type: r.initiatorType, bodyBytes: r.encodedBodySize })));
        const after = totals(hydrated);
        runs.push({ initial: before, afterVisibleHydration: after, visibleToEnabledMs, hydratedExtraJsBytes: Math.max(0, after.jsBodyBytes - before.jsBodyBytes), hydratedExtraRequests: Math.max(0, after.requestCount - before.requestCount) });
        if (sample === 0) { await page.evaluate(() => scrollTo({ top: 0, behavior: 'instant' })); await wait(250); await page.screenshot({ path: path.join(out, `v2-${width}-hero.png`) }); await page.screenshot({ path: path.join(out, `v2-${width}-full.png`), fullPage: true }); }
        await context.close();
      }
      const initialKeys = ['lcp', 'cls', 'htmlBytes', 'cssBodyBytes', 'externalJsBytes', 'inlineJsBytes', 'assetBodyBytes', 'requestCount'];
      const medians = Object.fromEntries(initialKeys.map(key => [key, median(runs.map(run => run.initial[key]))]));
      medians.hydratedJsBytes = median(runs.map(run => run.afterVisibleHydration.jsBodyBytes));
      medians.hydratedRequestCount = median(runs.map(run => run.afterVisibleHydration.requestCount));
      medians.visibleToEnabledMs = median(runs.map(run => run.visibleToEnabledMs));
      medians.extraJsBytesAfterScroll = median(runs.map(run => run.hydratedExtraJsBytes));
      medians.extraRequestsAfterScroll = median(runs.map(run => run.hydratedExtraRequests));
      report.widths[width] = { runs, median: medians };
    }
    const baseline = JSON.parse(await fs.readFile(path.join(root, 'docs', 'PHASE-1B-PERFORMANCE-BASELINE.json'), 'utf8'));
    report.baseline = baseline;
    report.baselineComparison = Object.fromEntries(widths.map(width => { const old = baseline.widths[width], current = report.widths[width].median; return [width, { baseline: { ...old, inlineJsBytes: baseline.capture.inlineJsBytes }, phase1bInitial: current, initialExternalJsDelta: current.externalJsBytes - old.externalJsBytes, inlineJsDelta: current.inlineJsBytes - baseline.capture.inlineJsBytes, initialRequestDelta: current.requestCount - old.requestCount, lcpDeltaMs: current.lcp - old.lcpMs }]; }));
    await fs.writeFile(path.join(out, 'performance.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify({ engine: report.engine, conditions: report.conditions, comparison: report.baselineComparison, screenshotsDirectory: out, report: path.join(out, 'performance.json') }, null, 2));
  } finally { if (browser) await browser.close(); server.kill(); }
})().catch(error => { console.error(error); process.exit(1); });
