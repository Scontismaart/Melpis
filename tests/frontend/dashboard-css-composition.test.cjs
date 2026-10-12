const assert = require("node:assert/strict");
const childProcess = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const repositoryRoot = path.resolve(__dirname, "../..");
const sourceRoot = path.join(repositoryRoot, "web/dashboard-css");
const outputPath = path.join(repositoryRoot, "web/style.css");
const expectedBytes = 364962;
const expectedSha256 = "cb1c181a4e617c43998b62ebde1b2c57ca77f56b4c297fe1ccf44a9fb5ae1fa1";
const scriptPath = path.join(repositoryRoot, "web/scripts/compose-dashboard-css.mjs");

let composeDashboardCss;
test("load the dashboard CSS composer", async () => {
  ({ composeDashboardCss } = await import("../../web/scripts/compose-dashboard-css.mjs"));
});

test("the committed dashboard CSS is byte-identical to its ordered source parts", () => {
  assert.ok(composeDashboardCss, "CSS composer import must succeed before contract assertions run");

  const first = composeDashboardCss({ sourceRoot });
  const second = composeDashboardCss({ sourceRoot });
  const committed = fs.readFileSync(outputPath);
  assert.deepEqual(first.output, committed);
  assert.deepEqual(second.output, first.output, "composition is deterministic across repeated runs");
  assert.equal(first.partCount, 11);
  assert.equal(first.output.length, expectedBytes);
  assert.equal(first.sha256, expectedSha256);
  assert.equal(crypto.createHash("sha256").update(committed).digest("hex"), expectedSha256);
  assert.equal(committed.includes(13), false, "the composed stylesheet preserves its canonical LF bytes");
  assert.equal(committed.subarray(0, 3).equals(Buffer.from([0xef, 0xbb, 0xbf])), false);
});

function createFixture({ manifest, files = {} }) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "dashboard-css-composition-"));
  fs.writeFileSync(path.join(root, "manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`);
  for (const [relativePath, content] of Object.entries(files)) {
    const filePath = path.join(root, relativePath);
    fs.mkdirSync(path.dirname(filePath), { recursive: true });
    fs.writeFileSync(filePath, content);
  }
  return root;
}

function withFixture(options, callback) {
  const root = createFixture(options);
  try {
    return callback(root);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
}

function createTemporaryWebRoot({ parts, css = Buffer.from(""), composer = true }) {
  const temporaryRoot = fs.mkdtempSync(path.join(os.tmpdir(), "dashboard-css-cli-"));
  const webRoot = path.join(temporaryRoot, "web");
  fs.mkdirSync(path.join(webRoot, "scripts"), { recursive: true });
  fs.mkdirSync(path.join(webRoot, "dashboard-css"), { recursive: true });
  fs.writeFileSync(path.join(webRoot, "style.css"), css);
  if (composer) fs.copyFileSync(scriptPath, path.join(webRoot, "scripts/compose-dashboard-css.mjs"));
  fs.writeFileSync(
    path.join(webRoot, "dashboard-css/manifest.json"),
    `${JSON.stringify({ schema_version: 1, parts: Object.keys(parts) }, null, 2)}\n`,
  );
  for (const [name, bytes] of Object.entries(parts)) {
    fs.writeFileSync(path.join(webRoot, "dashboard-css", name), bytes);
  }
  return { temporaryRoot, webRoot, cliPath: path.join(webRoot, "scripts/compose-dashboard-css.mjs") };
}

test("manifest order determines the exact concatenation without separators", () => {
  withFixture(
    {
      manifest: { schema_version: 1, parts: ["02.css", "01.css"] },
      files: { "01.css": Buffer.from("first"), "02.css": Buffer.from("second") },
    },
    (root) => {
      const { output, partCount } = composeDashboardCss({ sourceRoot: root });
      assert.deepEqual(output, Buffer.from("secondfirst"));
      assert.equal(partCount, 2);
    },
  );
});

test("the composer rejects empty manifests and empty segments", () => {
  withFixture(
    { manifest: { schema_version: 1, parts: [] } },
    (root) => assert.throws(() => composeDashboardCss({ sourceRoot: root }), /non-empty ordered parts array/),
  );
  withFixture(
    { manifest: { schema_version: 1, parts: ["01.css"] }, files: { "01.css": Buffer.alloc(0) } },
    (root) => assert.throws(() => composeDashboardCss({ sourceRoot: root }), /is empty/),
  );
});

test("the composer rejects missing, duplicate, and unreferenced source files", () => {
  withFixture(
    { manifest: { schema_version: 1, parts: ["01.css", "02.css"] }, files: { "01.css": "one" } },
    (root) => assert.throws(() => composeDashboardCss({ sourceRoot: root }), /source tree does not match/),
  );
  withFixture(
    { manifest: { schema_version: 1, parts: ["01.css", "01.css"] }, files: { "01.css": "one" } },
    (root) => assert.throws(() => composeDashboardCss({ sourceRoot: root }), /must be unique/),
  );
  withFixture(
    {
      manifest: { schema_version: 1, parts: ["01.css"] },
      files: { "01.css": "one", "unused.css": "unused" },
    },
    (root) => assert.throws(() => composeDashboardCss({ sourceRoot: root }), /source tree does not match/),
  );
});

test("the composer rejects traversal and malformed manifests", () => {
  withFixture(
    { manifest: { schema_version: 1, parts: ["../outside.css"] } },
    (root) => assert.throws(() => composeDashboardCss({ sourceRoot: root }), /not an allowed relative CSS path/),
  );
  withFixture(
    { manifest: { schema_version: 2, parts: ["01.css"] }, files: { "01.css": "one" } },
    (root) => assert.throws(() => composeDashboardCss({ sourceRoot: root }), /schema_version 1/),
  );
  withFixture(
    { manifest: { schema_version: 1, parts: ["01.css"] }, files: { "01.css": "one" } },
    (root) => {
      fs.mkdirSync(path.join(root, "nested"));
      assert.throws(() => composeDashboardCss({ sourceRoot: root }), /unsupported CSS source entry: nested/);
    },
  );
});

test(
  "the composer rejects file symlinks and symlinked source roots",
  { skip: process.platform === "win32" ? "Windows file symlinks may require elevated privileges" : false },
  () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "dashboard-css-symlink-"));
    const outside = path.join(root, "outside.css");
    const source = path.join(root, "source");
    fs.mkdirSync(source);
    fs.writeFileSync(outside, "outside");
    fs.writeFileSync(
      path.join(source, "manifest.json"),
      `${JSON.stringify({ schema_version: 1, parts: ["01.css"] })}\n`,
    );
    fs.symlinkSync(outside, path.join(source, "01.css"));
    try {
      assert.throws(() => composeDashboardCss({ sourceRoot: source }), /symbolic links are not allowed/);
      assert.throws(() => composeDashboardCss({ sourceRoot: path.join(root, "source-link") }), /cannot inspect source root/);
      fs.symlinkSync(source, path.join(root, "source-link"), "junction");
      assert.throws(() => composeDashboardCss({ sourceRoot: path.join(root, "source-link") }), /real directory/);
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  },
);

test("the composer rejects Windows junction source roots", { skip: process.platform !== "win32" }, () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "dashboard-css-junction-"));
  const source = path.join(root, "source");
  fs.mkdirSync(source);
  fs.writeFileSync(
    path.join(source, "manifest.json"),
    `${JSON.stringify({ schema_version: 1, parts: ["01.css"] })}\n`,
  );
  fs.writeFileSync(path.join(source, "01.css"), "part");
  const junction = path.join(root, "source-junction");
  try {
    fs.symlinkSync(source, junction, "junction");
    assert.throws(() => composeDashboardCss({ sourceRoot: junction }), /real directory/);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("--check detects byte drift without changing the output and CLI errors have no side effects", () => {
  const fixture = createTemporaryWebRoot({ parts: { "01.css": Buffer.from("part") }, css: Buffer.from("drift") });
  const original = fs.readFileSync(path.join(fixture.webRoot, "style.css"));
  try {
    const check = childProcess.spawnSync(process.execPath, [fixture.cliPath, "--check"], {
      cwd: fixture.temporaryRoot,
      encoding: "utf8",
    });
    assert.notEqual(check.status, 0);
    assert.match(check.stderr, /differs byte-for-byte/);
    assert.deepEqual(fs.readFileSync(path.join(fixture.webRoot, "style.css")), original);

    const unknown = childProcess.spawnSync(
      process.execPath,
      [fixture.cliPath, "--output", path.join(fixture.temporaryRoot, "sentinel.css"), "--unknown"],
      { cwd: fixture.temporaryRoot, encoding: "utf8" },
    );
    assert.notEqual(unknown.status, 0);
    assert.match(unknown.stderr, /unknown or trailing arguments/);
    assert.equal(fs.existsSync(path.join(fixture.temporaryRoot, "sentinel.css")), false);
  } finally {
    fs.rmSync(fixture.temporaryRoot, { recursive: true, force: true });
  }
});

test("--output writes the composed bytes atomically to the requested destination", () => {
  const fixture = createTemporaryWebRoot({
    parts: { "01.css": Buffer.from("one"), "02.css": Buffer.from("two") },
  });
  const destination = path.join(fixture.temporaryRoot, "generated.css");
  try {
    const result = childProcess.spawnSync(process.execPath, [fixture.cliPath, "--output", destination], {
      cwd: fixture.temporaryRoot,
      encoding: "utf8",
    });
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(fs.readFileSync(destination), Buffer.from("onetwo"));
    assert.deepEqual(
      fs.readdirSync(fixture.temporaryRoot).filter((name) => name.endsWith(".tmp")),
      [],
      "successful writes leave no temporary file behind",
    );
  } finally {
    fs.rmSync(fixture.temporaryRoot, { recursive: true, force: true });
  }
});

test("--check on the repository is read-only and byte-exact", () => {
  const before = fs.readFileSync(outputPath);
  const result = childProcess.spawnSync(process.execPath, [scriptPath, "--check"], {
    cwd: repositoryRoot,
    encoding: "utf8",
  });
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, new RegExp(`${expectedBytes} bytes`));
  assert.deepEqual(fs.readFileSync(outputPath), before);
});

test("asset audit resolves listed CSS segment URLs against the final stylesheet and remains fail-closed", () => {
  const temporaryRoot = fs.mkdtempSync(path.join(os.tmpdir(), "dashboard-css-assets-"));
  const webRoot = path.join(temporaryRoot, "web");
  const segmentRoot = path.join(webRoot, "dashboard-css");
  const fontPath = path.join(webRoot, "fonts", "inter-latin.woff2");
  const auditPath = path.join(temporaryRoot, "scripts", "audit-assets.js");
  fs.mkdirSync(segmentRoot, { recursive: true });
  fs.mkdirSync(path.dirname(fontPath), { recursive: true });
  fs.mkdirSync(path.dirname(auditPath), { recursive: true });
  fs.writeFileSync(
    path.join(segmentRoot, "manifest.json"),
    `${JSON.stringify({ schema_version: 1, parts: ["01.css"] })}\n`,
  );
  fs.writeFileSync(path.join(segmentRoot, "01.css"), "@font-face{src:url('fonts/inter-latin.woff2')}\n");
  fs.writeFileSync(fontPath, "synthetic-font-asset");
  fs.copyFileSync(path.join(repositoryRoot, "scripts/audit-assets.js"), auditPath);
  try {
    const valid = childProcess.spawnSync(process.execPath, [auditPath], {
      cwd: temporaryRoot,
      encoding: "utf8",
    });
    assert.equal(valid.status, 0, valid.stderr);
    assert.match(valid.stdout, /Referenced local asset audit passed/);

    fs.rmSync(fontPath);
    const missing = childProcess.spawnSync(process.execPath, [auditPath], {
      cwd: temporaryRoot,
      encoding: "utf8",
    });
    assert.notEqual(missing.status, 0);
    assert.match(missing.stderr, /dashboard-css[\\/]01\.css -> fonts\/inter-latin\.woff2/);
  } finally {
    fs.rmSync(temporaryRoot, { recursive: true, force: true });
  }
});
