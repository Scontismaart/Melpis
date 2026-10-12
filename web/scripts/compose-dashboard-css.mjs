import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const repositoryRoot = path.resolve(scriptDirectory, "../..");
const sourceDirectory = path.join(repositoryRoot, "web", "dashboard-css");
const committedOutputPath = path.join(repositoryRoot, "web", "style.css");
const manifestName = "manifest.json";
const partPattern = /^[0-9]{2}\.css$/;

function fail(message) {
  throw new Error(`Dashboard CSS composition failed: ${message}`);
}

function validatePartPath(relativePath, label) {
  if (
    typeof relativePath !== "string" ||
    !partPattern.test(relativePath) ||
    relativePath.includes("\\") ||
    relativePath.includes("\0") ||
    path.posix.isAbsolute(relativePath) ||
    path.win32.isAbsolute(relativePath) ||
    path.posix.normalize(relativePath) !== relativePath
  ) {
    fail(`${label} is not an allowed relative CSS path: ${JSON.stringify(relativePath)}`);
  }
  return relativePath;
}

function readManifestBuffer(filePath) {
  let content;
  try {
    content = fs.readFileSync(filePath);
  } catch (error) {
    fail(`cannot read ${manifestName}: ${error.message}`);
  }
  const decoded = content.toString("utf8");
  if (!Buffer.from(decoded, "utf8").equals(content)) {
    fail(`${manifestName} is not valid UTF-8`);
  }
  if (content.includes(13) || content.subarray(0, 3).equals(Buffer.from([0xef, 0xbb, 0xbf]))) {
    fail(`${manifestName} must use UTF-8 without a BOM and LF line endings`);
  }
  return decoded;
}

function sourceFilesUnder(rootPath) {
  const files = new Set();
  let entries;
  try {
    entries = fs.readdirSync(rootPath, { withFileTypes: true });
  } catch (error) {
    fail(`cannot inspect source directory: ${error.message}`);
  }

  for (const entry of entries) {
    if (entry.isSymbolicLink()) {
      fail(`symbolic links are not allowed in CSS sources: ${entry.name}`);
    }
    if (!entry.isFile()) {
      fail(`unsupported CSS source entry: ${entry.name}`);
    }
    files.add(entry.name);
  }
  return files;
}

function resolveRegularFile(rootRealPath, absolutePath, relativePath) {
  let stat;
  let realPath;
  try {
    stat = fs.lstatSync(absolutePath);
    realPath = fs.realpathSync(absolutePath);
  } catch (error) {
    fail(`missing CSS source ${relativePath}: ${error.message}`);
  }
  const relativeToRoot = path.relative(rootRealPath, realPath);
  if (
    !stat.isFile() ||
    stat.isSymbolicLink() ||
    relativeToRoot === "" ||
    relativeToRoot === ".." ||
    relativeToRoot.startsWith(`..${path.sep}`) ||
    path.isAbsolute(relativeToRoot)
  ) {
    fail(`CSS source must be a regular file inside its source root: ${relativePath}`);
  }
  return realPath;
}

export function composeDashboardCss({ sourceRoot = sourceDirectory } = {}) {
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
  const manifestRealPath = resolveRegularFile(rootRealPath, manifestPath, manifestName);
  let manifest;
  try {
    manifest = JSON.parse(readManifestBuffer(manifestRealPath));
  } catch (error) {
    if (error.message.startsWith("Dashboard CSS composition failed:")) throw error;
    fail(`${manifestName} is invalid JSON: ${error.message}`);
  }
  if (
    !manifest ||
    Array.isArray(manifest) ||
    Object.keys(manifest).sort().join(",") !== "parts,schema_version" ||
    manifest.schema_version !== 1 ||
    !Array.isArray(manifest.parts) ||
    manifest.parts.length === 0
  ) {
    fail(`${manifestName} must declare schema_version 1 and a non-empty ordered parts array`);
  }

  const partPaths = manifest.parts.map((partPath, index) =>
    validatePartPath(partPath, `parts[${index}]`),
  );
  const partSet = new Set(partPaths);
  if (partSet.size !== partPaths.length) {
    fail("manifest part paths must be unique");
  }

  const expectedFiles = new Set([manifestName, ...partPaths]);
  const actualFiles = sourceFilesUnder(absoluteRoot);
  const unexpectedFiles = [...actualFiles].filter((file) => !expectedFiles.has(file));
  const missingFiles = [...expectedFiles].filter((file) => !actualFiles.has(file));
  if (unexpectedFiles.length || missingFiles.length) {
    fail(
      `source tree does not match ${manifestName}; unexpected: ${unexpectedFiles.join(", ") || "none"}; missing: ${missingFiles.join(", ") || "none"}`,
    );
  }

  const chunks = partPaths.map((relativePath) => {
    const validatedPath = validatePartPath(relativePath, "part");
    const absolutePath = path.resolve(absoluteRoot, validatedPath);
    const realPath = resolveRegularFile(rootRealPath, absolutePath, validatedPath);
    let content;
    try {
      content = fs.readFileSync(realPath);
    } catch (error) {
      fail(`cannot read CSS source ${validatedPath}: ${error.message}`);
    }
    if (content.length === 0) {
      fail(`CSS source ${validatedPath} is empty`);
    }
    return content;
  });

  const output = Buffer.concat(chunks);
  if (output.length === 0) {
    fail("composition produced an empty output");
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
    "Usage: node web/scripts/compose-dashboard-css.mjs [--check | --write | --output PATH]\n",
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

  const result = composeDashboardCss();
  if (modes[0] === "--check") {
    const expected = fs.readFileSync(committedOutputPath);
    if (!result.output.equals(expected)) {
      fail("generated CSS differs byte-for-byte from the committed web/style.css");
    }
    process.stdout.write(
      `Dashboard CSS is up to date (${result.output.length} bytes, sha256 ${result.sha256}, ${result.partCount} parts).\n`,
    );
    return;
  }

  const outputPath = modes[0] === "--output" ? argumentsList[outputIndex + 1] : committedOutputPath;
  if (modes[0] === "--output" && (!outputPath || outputPath.startsWith("--"))) {
    fail("--output requires a destination path");
  }
  writeAtomically(outputPath, result.output);
  const label = modes[0] === "--output" ? path.resolve(outputPath) : "web/style.css";
  process.stdout.write(
    `Generated ${label} (${result.output.length} bytes, sha256 ${result.sha256}, ${result.partCount} parts).\n`,
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
