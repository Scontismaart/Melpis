const { test } = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');
const { buildQaBootstrap } = require('../../scripts/dashboard-baseline-server.cjs');

test('capture readiness is revoked after late DOM rendering and pointer transitions', async () => {
  const dom = new JSDOM('<body><main>Loading</main></body>', {
    url: 'http://127.0.0.1:4190/app/bookings', runScripts: 'outside-only', pretendToBeVisual: true,
  });
  const { window } = dom;
  window.fetch = async () => ({ ok: true });
  window.document.fonts = Object.assign([], { ready: Promise.resolve(), status: 'loaded' });
  window.document.getAnimations = () => [];
  window.eval(buildQaBootstrap());
  const untilReady = async () => {
    for (let frame = 0; frame < 30 && window.document.body.dataset.qaReady !== 'true'; frame++) {
      await new Promise(resolve => window.requestAnimationFrame(resolve));
    }
    assert.equal(window.document.body.dataset.qaReady, 'true');
  };
  try {
    await untilReady();
    window.document.querySelector('main').textContent = 'Calendar rendered after fetch';
    await Promise.resolve();
    assert.equal(window.document.body.dataset.qaReady, 'false', 'late render must revoke stale readiness');
    await untilReady();
    window.document.dispatchEvent(new window.Event('pointermove', { bubbles: true }));
    assert.equal(window.document.body.dataset.qaReady, 'false', 'hover transitions must revoke readiness');
    await untilReady();
    assert.equal(JSON.parse(window.document.body.dataset.qaEnvironment).capture.pendingRequests, 0);
  } finally { window.close(); }
});
