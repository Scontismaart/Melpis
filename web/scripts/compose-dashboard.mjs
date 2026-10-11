import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const repositoryRoot = path.resolve(scriptDirectory, "../..");
const webRoot = path.join(repositoryRoot, "web");
const dashboardSourceRoot = path.join(webRoot, "dashboard-src");
const committedOutputPath = path.join(webRoot, "index.html");
const manifestName = "manifest.json";
const includeMarker = "@dashboard-include";
const includeLine = /^[\t ]*<!-- @dashboard-include ([a-z0-9][a-z0-9_./-]*\.html) -->[\t ]*$/;

function fail(message) {
  throw new Error(`Dashboard composition failed: ${message}`);
}

function validateRelativeHtmlPath(relativePath, label) {
  if (typeof relativePath !== "string" || relativePath.length === 0) {
    fail(`${label} must be a non-empty relative HTML path`);
  }
  if (
    relativePath.includes("\\") ||
    relativePath.includes("\0") ||
    path.posix.isAbsolute(relativePath) ||
    path.win32.isAbsolute(relativePath) ||
    relativePath.split("/").some((part) => part === "" || part === "." || part === "..") ||
    !/^[a-z0-9][a-z0-9_./-]*\.html$/.test(relativePath)
  ) {
    fail(`${label} is not an allowed relative HTML path: ${JSON.stringify(relativePath)}`);
  }
  if (path.posix.normalize(relativePath) !== relativePath) {
    fail(`${label} is not normalized: ${JSON.stringify(relativePath)}`);
  }
  return relativePath;
}

function readUtf8Buffer(filePath, relativePath) {
  let content;
  try {
    content = fs.readFileSync(filePath);
  } catch (error) {
    fail(`cannot read ${relativePath}: ${error.message}`);
  }
  const decoded = content.toString("utf8");
  if (!Buffer.from(decoded, "utf8").equals(content)) {
    fail(`${relativePath} is not valid UTF-8`);
  }
  if (content.includes(13)) {
    fail(`${relativePath} must use LF line endings`);
  }
  return content;
}

function findIncludes(content, relativePath) {
  const includes = [];
  let lineStart = 0;

  while (lineStart < content.length) {
    const newline = content.indexOf(10, lineStart);
    const lineEnd = newline < 0 ? content.length : newline + 1;
    const lineContentEnd = newline < 0 ? content.length : newline;
    const line = content.subarray(lineStart, lineContentEnd).toString("utf8");

    if (line.includes(includeMarker)) {
      if (newline < 0) {
        fail(`${relativePath} has an include marker without a terminating LF`);
      }
      const match = includeLine.exec(line);
      if (!match) {
        fail(`${relativePath} has a malformed include marker on line ${includes.length + 1}`);
      }
      includes.push({ path: match[1], start: lineStart, end: lineEnd });
    }
    lineStart = lineEnd;
  }

  return includes;
}

function sourceFilesUnder(rootPath) {
  const files = new Set();
  const visit = (directoryPath, prefix = "") => {
    let entries;
    try {
      entries = fs.readdirSync(directoryPath, { withFileTypes: true });
    } catch (error) {
      fail(`cannot inspect source directory ${prefix || "."}: ${error.message}`);
    }

    for (const entry of entries) {
      const relativePath = prefix ? `${prefix}/${entry.name}` : entry.name;
      const absolutePath = path.join(directoryPath, entry.name);
      if (entry.isSymbolicLink()) {
        fail(`symbolic links are not allowed in dashboard sources: ${relativePath}`);
      }
      if (entry.isDirectory()) {
        visit(absolutePath, relativePath);
      } else if (entry.isFile()) {
        files.add(relativePath);
      } else {
        fail(`unsupported source entry: ${relativePath}`);
      }
    }
  };

  visit(rootPath);
  return files;
}

function assertInsideRoot(rootRealPath, absolutePath, relativePath) {
  let realPath;
  try {
    realPath = fs.realpathSync(absolutePath);
  } catch (error) {
    fail(`missing source ${relativePath}: ${error.message}`);
  }
  const relativeToRoot = path.relative(rootRealPath, realPath);
  if (
    relativeToRoot === "" ||
    relativeToRoot === ".." ||
    relativeToRoot.startsWith(`..${path.sep}`) ||
    path.isAbsolute(relativeToRoot)
  ) {
    fail(`source resolves outside the dashboard source root: ${relativePath}`);
  }
  return realPath;
}

export function composeDashboard({ sourceRoot = dashboardSourceRoot } = {}) {
  const absoluteRoot = path.resolve(sourceRoot);
  let rootStat;
  try {
    rootStat = fs.lstatSync(absoluteRoot);
  } catch (error) {
    fail(`cannot inspect source root: ${error.message}`);
  }
  if (!rootStat.isDirectory() || rootStat.isSymbolicLink()) {
    fail("source root must be a real directory, not a symbolic link");
  }

  const rootRealPath = fs.realpathSync(absoluteRoot);
  const manifestPath = path.join(absoluteRoot, manifestName);
  let manifestStat;
  try {
    manifestStat = fs.lstatSync(manifestPath);
  } catch (error) {
    fail(`missing ${manifestName}: ${error.message}`);
  }
  if (!manifestStat.isFile() || manifestStat.isSymbolicLink()) {
    fail(`${manifestName} must be a regular file`);
  }

  let manifest;
  try {
    manifest = JSON.parse(readUtf8Buffer(manifestPath, manifestName).toString("utf8"));
  } catch (error) {
    if (error.message.startsWith("Dashboard composition failed:")) throw error;
    fail(`${manifestName} is invalid JSON: ${error.message}`);
  }
  if (
    !manifest ||
    Array.isArray(manifest) ||
    Object.keys(manifest).sort().join(",") !== "entry,envelope,parts" ||
    !Array.isArray(manifest.parts) ||
    !manifest.envelope ||
    Array.isArray(manifest.envelope) ||
    Object.keys(manifest.envelope).sort().join(",") !== "prefix,suffix" ||
    typeof manifest.envelope.prefix !== "string" ||
    manifest.envelope.prefix.length === 0 ||
    typeof manifest.envelope.suffix !== "string" ||
    manifest.envelope.suffix.length === 0
  ) {
    fail(`${manifestName} must declare an entry, exact document envelope, and parts array`);
  }

  const entryPath = validateRelativeHtmlPath(manifest.entry, "entry");
  const partPaths = manifest.parts.map((partPath, index) =>
    validateRelativeHtmlPath(partPath, `parts[${index}]`),
  );
  const partSet = new Set(partPaths);
  if (partSet.size !== partPaths.length || partSet.has(entryPath)) {
    fail("manifest paths must be unique and parts cannot include the entry file");
  }

  const expectedFiles = new Set([manifestName, entryPath, ...partPaths]);
  const actualFiles = sourceFilesUnder(absoluteRoot);
  const unexpectedFiles = [...actualFiles].filter((file) => !expectedFiles.has(file));
  const missingFiles = [...expectedFiles].filter((file) => !actualFiles.has(file));
  if (unexpectedFiles.length || missingFiles.length) {
    fail(
      `source tree does not match ${manifestName}; unexpected: ${unexpectedFiles.join(", ") || "none"}; missing: ${missingFiles.join(", ") || "none"}`,
    );
  }

  const references = new Map(partPaths.map((partPath) => [partPath, 0]));
  const render = (relativePath, stack = []) => {
    if (stack.includes(relativePath)) {
      fail(`include cycle detected: ${[...stack, relativePath].join(" -> ")}`);
    }
    if (relativePath !== entryPath) {
      if (!partSet.has(relativePath)) {
        fail(`include is not declared in ${manifestName}: ${relativePath}`);
      }
      const referenceCount = references.get(relativePath);
      if (referenceCount !== 0) {
        fail(`duplicate include reference: ${relativePath}`);
      }
      references.set(relativePath, referenceCount + 1);
    }

    const validatedPath = validateRelativeHtmlPath(relativePath, "include");
    const absolutePath = path.resolve(absoluteRoot, ...validatedPath.split("/"));
    const realPath = assertInsideRoot(rootRealPath, absolutePath, validatedPath);
    const stat = fs.lstatSync(absolutePath);
    if (!stat.isFile() || stat.isSymbolicLink()) {
      fail(`source must be a regular non-symlink file: ${validatedPath}`);
    }

    const content = readUtf8Buffer(realPath, validatedPath);
    const includes = findIncludes(content, validatedPath);
    const chunks = [];
    let cursor = 0;
    for (const include of includes) {
      chunks.push(content.subarray(cursor, include.start));
      const childPath = validateRelativeHtmlPath(include.path, "include");
      chunks.push(render(childPath, [...stack, relativePath]));
      cursor = include.end;
    }
    chunks.push(content.subarray(cursor));
    return Buffer.concat(chunks);
  };

  const output = render(entryPath);
  const unreferencedParts = [...references].filter(([, count]) => count !== 1).map(([part]) => part);
  if (unreferencedParts.length) {
    fail(`each manifest part must be referenced exactly once; missing or repeated: ${unreferencedParts.join(", ")}`);
  }
  if (output.length === 0 || output.includes(Buffer.from(includeMarker))) {
    fail("composition produced an empty or incomplete output");
  }
  const prefix = Buffer.from(manifest.envelope.prefix, "utf8");
  const suffix = Buffer.from(manifest.envelope.suffix, "utf8");
  if (
    output.length < prefix.length + suffix.length ||
    !output.subarray(0, prefix.length).equals(prefix) ||
    !output.subarray(output.length - suffix.length).equals(suffix)
  ) {
    fail("composition does not match the declared document envelope");
  }

  return {
    output,
    sha256: createHash("sha256").update(output).digest("hex"),
    partCount: partPaths.length,
  };
}

function writeAtomically(outputPath, content) {
  const absoluteOutputPath = path.resolve(outputPath);
  const directoryPath = path.dirname(absoluteOutputPath);
  const temporaryPath = path.join(
    directoryPath,
    `.${path.basename(absoluteOutputPath)}.${process.pid}.${Date.now()}.tmp`,
  );

  try {
    if (fs.existsSync(absoluteOutputPath) && fs.lstatSync(absoluteOutputPath).isSymbolicLink()) {
      fail(`output path cannot be a symbolic link: ${absoluteOutputPath}`);
    }
    fs.writeFileSync(temporaryPath, content, { flag: "wx" });
    fs.renameSync(temporaryPath, absoluteOutputPath);
  } catch (error) {
    try {
      fs.rmSync(temporaryPath, { force: true });
    } catch {
      // Preserve the original error; the destination is never touched before rename.
    }
    throw error;
  }
}

function printHelp() {
  process.stdout.write(
    "Usage: node web/scripts/compose-dashboard.mjs [--check | --write | --output PATH]\n",
  );
}

function runCli(argumentsList) {
  const knownArguments = new Set(["--check", "--write", "--output", "--help"]);
  const outputIndex = argumentsList.indexOf("--output");
  const unknownArguments = argumentsList.filter((argument, index) =>
    !knownArguments.has(argument) && !(outputIndex >= 0 && index === outputIndex + 1),
  );
  if (unknownArguments.length) {
    fail(`unknown or trailing arguments: ${unknownArguments.join(", ")}`);
  }
  if (argumentsList.includes("--help")) {
    if (argumentsList.length !== 1) {
      fail("--help cannot be combined with other arguments");
    }
    printHelp();
    return;
  }
  const modes = argumentsList.filter((argument) => ["--check", "--write", "--output"].includes(argument));
  if (modes.length > 1) {
    fail("choose only one of --check, --write, or --output");
  }
  if (
    (modes[0] === "--output" && argumentsList.length !== 2) ||
    (modes[0] !== "--output" && argumentsList.length > 1)
  ) {
    fail("unexpected trailing arguments");
  }

  const result = composeDashboard();
  if (modes[0] === "--check") {
    const expected = fs.readFileSync(committedOutputPath);
    if (!result.output.equals(expected)) {
      fail("generated HTML differs from the committed web/index.html; regenerate and commit it");
    }
    process.stdout.write(
      `Dashboard HTML is up to date (${result.output.length} bytes, sha256 ${result.sha256}, ${result.partCount} parts).\n`,
    );
    return;
  }

  if (modes[0] === "--output") {
    const outputPath = argumentsList[outputIndex + 1];
    if (!outputPath || outputPath.startsWith("--")) {
      fail("--output requires a destination path");
    }
    writeAtomically(outputPath, result.output);
    process.stdout.write(`Composed dashboard HTML at ${path.resolve(outputPath)} (${result.output.length} bytes, sha256 ${result.sha256}).\n`);
    return;
  }

  writeAtomically(committedOutputPath, result.output);
  process.stdout.write(
    `Generated web/index.html (${result.output.length} bytes, sha256 ${result.sha256}, ${result.partCount} parts).\n`,
  );
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    runCli(process.argv.slice(2));
  } catch (error) {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  }
}
