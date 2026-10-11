const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { createHash } = require("node:crypto");

const baselineRoot = path.join(__dirname, "baselines", "dashboard-01");
const manifestPath = path.join(baselineRoot, "manifest.json");
const activeBaselineRoot = path.join(__dirname, "baselines", "dashboard-02");
const activeManifestPath = path.join(activeBaselineRoot, "manifest.json");

function jpegDimensions(buffer) {
  assert.equal(buffer[0], 0xff, "JPEG starts with SOI marker prefix");
  assert.equal(buffer[1], 0xd8, "JPEG starts with SOI marker");
  const startOfFrameMarkers = new Set([
    0xc0, 0xc1, 0xc2, 0xc3,
    0xc5, 0xc6, 0xc7,
    0xc9, 0xca, 0xcb,
    0xcd, 0xce, 0xcf,
  ]);

  let offset = 2;
  while (offset < buffer.length) {
    assert.equal(buffer[offset], 0xff, `expected JPEG marker at byte ${offset}`);
    while (buffer[offset] === 0xff) offset += 1;
    const marker = buffer[offset];
    offset += 1;

    if (marker === 0xd9 || marker === 0xda) break;
    if (marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) continue;

    assert.ok(offset + 1 < buffer.length, "JPEG segment has a length field");
    const length = buffer.readUInt16BE(offset);
    assert.ok(length >= 2 && offset + length <= buffer.length, "JPEG segment length is valid");
    if (startOfFrameMarkers.has(marker)) {
      assert.ok(length >= 7, "JPEG frame header includes dimensions");
      const data = offset + 2;
      return {
        width: buffer.readUInt16BE(data + 3),
        height: buffer.readUInt16BE(data + 1),
      };
    }
    offset += length;
  }
  throw new Error("JPEG has no supported Start Of Frame marker");
}

test("dashboard visual baseline manifest maps twelve correctly sized JPEGs", () => {
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  assert.equal(manifest.schema_version, 1);
  assert.equal(manifest.baseline_set, "dashboard-01");
  assert.equal(manifest.capture.locale, "it");
  assert.equal(manifest.capture.theme, "dark");
  assert.equal(manifest.capture.fixed_time, "2026-10-08T10:00:00.000Z");
  assert.equal(manifest.capture.identity.role, "owner");
  assert.match(manifest.capture.identity.email, /@dashboard-qa\.invalid$/);
  assert.equal(manifest.capture.dataset_profile, "empty");
  assert.deepEqual(manifest.capture.viewports, {
    desktop: { width: 1440, height: 900 },
    mobile: { width: 375, height: 812 },
  });

  assert.deepEqual(manifest.cases.map(({ id }) => id), [
    "overview", "inbox", "bookings", "settings", "simulator", "sidebar",
  ]);
  const entries = manifest.cases.flatMap((captureCase) => Object.entries(captureCase.images)
    .map(([viewport, filename]) => ({ captureCase, viewport, filename })));
  assert.equal(entries.length, 12);
  assert.equal(new Set(entries.map(({ filename }) => filename)).size, 12);

  for (const { captureCase, viewport, filename } of entries) {
    assert.ok(["desktop", "mobile"].includes(viewport));
    assert.match(filename, new RegExp(`^${viewport}-${captureCase.id}\\.jpg$`));
    const target = path.resolve(baselineRoot, filename);
    assert.ok(target.startsWith(`${baselineRoot}${path.sep}`), "manifest file stays inside its baseline directory");
    assert.ok(fs.existsSync(target), `${filename} exists`);
    assert.equal(createHash("sha256").update(fs.readFileSync(target)).digest("hex"), manifest.image_sha256[filename],
      `${filename} matches the reviewed immutable artifact`);
    const expected = manifest.capture.viewports[viewport];
    assert.deepEqual(jpegDimensions(fs.readFileSync(target)), {
      width: expected.width,
      height: expected.height,
    }, `${filename} dimensions match ${viewport} capture metadata`);
  }
});

test("CI active dashboard baseline V2 integrity and provenance remain pinned to main", () => {
  const manifest = JSON.parse(fs.readFileSync(activeManifestPath, "utf8"));
  assert.equal(manifest.schema_version, 2);
  assert.equal(manifest.baseline_set, "dashboard-02");
  assert.equal(manifest.active_for_ci, true);
  assert.equal(manifest.source_ref, "main");
  assert.equal(manifest.source_commit, "3060374cbf4385f305626bde27dde696445e1143");
  assert.equal(manifest.capture_metadata.file, "capture-metadata.json");
  assert.equal(manifest.capture_metadata.sha256,
    createHash("sha256").update(fs.readFileSync(path.join(activeBaselineRoot, manifest.capture_metadata.file))).digest("hex"));
  assert.equal(manifest.capture.browser.user_agent.includes("Chrome/155.0.0.0"), true);
  assert.equal(manifest.capture.locale, "it");
  assert.equal(manifest.capture.time_zone, "Europe/Rome");
  assert.equal(manifest.capture.fixed_time, "2026-10-08T10:00:00.000Z");
  assert.equal(manifest.capture.theme, "dark");
  assert.equal(manifest.capture.viewports.desktop.device_pixel_ratio, 1.0000000149011612);
  assert.equal(manifest.capture.viewports.mobile.device_pixel_ratio, 1.0000000149011612);
  assert.equal(manifest.certification.main_repeatability.identical_images, 12);
  assert.equal(manifest.certification.main_repeatability.total_images, 12);
  assert.equal(manifest.certification.main_vs_block4a.identical_images, 12);
  assert.equal(manifest.certification.main_vs_block4a.total_images, 12);
  assert.equal(manifest.certification.dynamic_main_vs_block4a.identical_images, 10);
  assert.equal(manifest.certification.dynamic_main_vs_block4a.total_images, 10);
  assert.equal(manifest.ci_gate.active_baseline, "dashboard-02");
  assert.match(manifest.ci_gate.limitation, /does not run a real-browser screenshot capture or RGB comparison/);

  const metadata = JSON.parse(fs.readFileSync(path.join(activeBaselineRoot, manifest.capture_metadata.file), "utf8"));
  const fontAssets = {
    "fonts/outfit-latin.woff2": "fonts/outfit-latin.woff2",
    "fonts/outfit-latin-ext.woff2": "fonts/outfit-latin-ext.woff2",
    "fonts/inter-latin.woff2": "fonts/inter-latin.woff2",
  };
  for (const [manifestPath, webPath] of Object.entries(fontAssets)) {
    const asset = fs.readFileSync(path.join(__dirname, "..", "..", "web", webPath));
    assert.equal(createHash("sha256").update(asset).digest("hex"), manifest.capture.fonts.assets_sha256[manifestPath],
      `${webPath} still matches the font used by active V2`);
  }
  const styleCss = fs.readFileSync(path.join(__dirname, "..", "..", "web", "style.css"));
  assert.equal(createHash("sha256").update(styleCss).digest("hex"), manifest.capture.fonts.style_css_sha256,
    "web/style.css still matches the active V2 capture source");
  const entries = manifest.cases.flatMap((captureCase) => Object.entries(captureCase.images)
    .map(([viewport, filename]) => ({ captureCase, viewport, filename })));
  assert.equal(entries.length, 12);
  assert.equal(new Set(entries.map(({ filename }) => filename)).size, 12);
  assert.equal(Object.keys(metadata).length, 12);

  for (const { captureCase, viewport, filename } of entries) {
    assert.ok(["desktop", "mobile"].includes(viewport));
    assert.match(filename, new RegExp(`^${viewport}-${captureCase.id}\\.jpg$`));
    const target = path.resolve(activeBaselineRoot, filename);
    assert.ok(target.startsWith(`${activeBaselineRoot}${path.sep}`), "active V2 file stays inside its baseline directory");
    assert.ok(fs.existsSync(target), `${filename} exists`);
    assert.equal(createHash("sha256").update(fs.readFileSync(target)).digest("hex"), manifest.image_sha256[filename],
      `${filename} matches the active immutable V2 artifact`);
    assert.deepEqual(jpegDimensions(fs.readFileSync(target)), manifest.capture.viewports[viewport] && {
      width: manifest.capture.viewports[viewport].width,
      height: manifest.capture.viewports[viewport].height,
    }, `${filename} dimensions match V2 capture metadata`);
    const captureMetadata = metadata[`main-a-${viewport}-${captureCase.id}`];
    assert.ok(captureMetadata, `${filename} has runtime capture metadata`);
    assert.equal(captureMetadata.fixture.route, captureCase.route.split("?")[0]);
    assert.equal(captureMetadata.viewport.width, manifest.capture.viewports[viewport].width);
    assert.equal(captureMetadata.viewport.height, manifest.capture.viewports[viewport].height);
    assert.equal(captureMetadata.viewport.devicePixelRatio, manifest.capture.viewports[viewport].device_pixel_ratio);
    assert.equal(captureMetadata.viewport.zoom, manifest.capture.viewports[viewport].zoom);
    assert.equal(captureMetadata.locale, manifest.capture.locale);
    assert.equal(captureMetadata.timeZone, manifest.capture.time_zone);
    assert.equal(captureMetadata.theme, manifest.capture.theme);
    assert.equal(captureMetadata.language, manifest.capture.language);
    assert.equal(captureMetadata.fixedClock, manifest.capture.fixed_time);
    assert.equal(captureMetadata.fixture.scenario, "baseline");
    assert.equal(captureMetadata.fixture.fixture, "synthetic-loopback");
    assert.deepEqual(captureMetadata.fonts.loadedFamilies, manifest.capture.fonts.loaded_faces);
    assert.equal(captureMetadata.fonts.bodyComputed, manifest.capture.fonts.body_computed);
    assert.equal(captureMetadata.fonts.headingComputed, manifest.capture.fonts.heading_computed);
  }
});
