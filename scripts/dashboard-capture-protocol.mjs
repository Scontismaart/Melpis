// QA only. Call once on a NEW tab, close that tab after capture; never reuse a
// WebView capture surface across routes. This is not copied into the web image.
export async function captureReadyViewport(tab, { width, height }) {
  await tab.playwright.locator('body[data-qa-ready="true"]').waitFor({ state: 'visible' });
  const metadata = JSON.parse(await tab.playwright.locator('body').getAttribute('data-qa-environment'));
  const actual = await tab.playwright.evaluate(() => ({ width: innerWidth, height: innerHeight }));
  if (actual.width !== width || actual.height !== height || metadata.viewport.width !== width || metadata.viewport.height !== height ||
      metadata.capture?.pendingRequests !== 0 || metadata.capture?.stableFrames < 3 || metadata.capture?.mode !== 'viewport-only') {
    throw new Error('Capture environment is not stable or viewport does not match');
  }
  // Full-page + clipping takes a different scrollbar/compositing path in the
  // desktop WebView; capture the actual viewport without temporary page resizing.
  const bytes = await tab.screenshot({ fullPage: false });
  return { bytes, metadata };
}
