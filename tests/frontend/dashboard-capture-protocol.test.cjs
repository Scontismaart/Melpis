const { test } = require('node:test');
const assert = require('node:assert/strict');

test('viewport capture rejects stale readiness and never invokes full-page clipping', async () => {
  const { captureReadyViewport } = await import('../../scripts/dashboard-capture-protocol.mjs');
  const metadata = { viewport: { width: 1440, height: 900 }, capture: { pendingRequests: 0, stableFrames: 3, mode: 'viewport-only' } };
  let options;
  const tab = {
    playwright: { locator: () => ({ waitFor: async () => {}, getAttribute: async () => JSON.stringify(metadata) }),
      evaluate: async () => ({ width: 1440, height: 900 }) },
    screenshot: async value => { options = value; return Buffer.from('fixture'); },
  };
  await captureReadyViewport(tab, { width: 1440, height: 900 });
  assert.deepEqual(options, { fullPage: false });
  metadata.capture.pendingRequests = 1;
  options = null;
  await assert.rejects(captureReadyViewport(tab, { width: 1440, height: 900 }), /not stable/);
  assert.equal(options, null);
});
