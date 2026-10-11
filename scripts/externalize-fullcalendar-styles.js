/* Extract FullCalendar's fixed CSS registration calls into a same-origin file.
 * The vendor bundle otherwise creates an empty <style> and inserts rules through
 * CSSOM at runtime, which cannot be safely covered by a static CSP hash.
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const root = path.resolve(__dirname, "..");
const sourcePath = path.join(root, "web", "vendor", "fullcalendar-source.min.js");
const bundlePath = path.join(root, "web", "vendor", "fullcalendar.min.js");
const cssPath = path.join(root, "web", "vendor", "fullcalendar.css");

function parseStringAt(source, start) {
  const quote = source[start];
  if (quote !== "'" && quote !== '"') throw new Error("FullCalendar CSS call is not a string literal");
  let index = start + 1;
  while (index < source.length) {
    if (source[index] === "\\") index += 2;
    else if (source[index] === quote) {
      index += 1;
      break;
    } else index += 1;
  }
  if (source[index - 1] !== quote) throw new Error("Unterminated FullCalendar CSS literal");
  return { literal: source.slice(start, index), end: index };
}

function extractStyles(source) {
  const styles = [];
  let offset = source.indexOf("function xe(e){");
  if (offset < 0) throw new Error("Unsupported FullCalendar bundle: injectStyles not found");
  while ((offset = source.indexOf("xe(", offset + 3)) >= 0) {
    if (source.slice(offset - 9, offset) === "function ") continue;
    const { literal } = parseStringAt(source, offset + 3);
    // This evaluates only string literals parsed from the reviewed vendor file.
    styles.push(vm.runInNewContext(literal, Object.create(null), { timeout: 1000 }));
  }
  if (styles.length !== 5) throw new Error(`Expected 5 static FullCalendar CSS blocks, got ${styles.length}`);
  return styles;
}

function generate(source) {
  const styles = extractStyles(source);
  const start = source.indexOf("function _e(e){");
  const end = source.indexOf("function Te(e,t)", start);
  if (start < 0 || end < 0) throw new Error("Unsupported FullCalendar bundle: style element helper not found");
  let bundle = source.slice(0, start) + "function _e(e){}" + source.slice(end);
  bundle = bundle.replace(
    "function xe(e){Ce.push(e),Re.forEach(t=>{Te(t,e)})}",
    "function xe(e){}"
  );
  if (bundle === source || bundle.includes("function _e(e){let t=Re.get(e)")) {
    throw new Error("FullCalendar CSS externalization patch did not apply");
  }
  return {
    bundle,
    css: `/* Generated from fullcalendar-source.min.js; do not edit. */\n${styles.join("\n")}\n`,
  };
}

function normalizeCssEol(css) {
  return css.replace(/\r\n/g, "\n");
}

function main() {
  const check = process.argv.includes("--check");
  const source = fs.readFileSync(sourcePath, "utf8");
  const generated = generate(source);
  if (check) {
    if (fs.readFileSync(bundlePath, "utf8") !== generated.bundle
      || normalizeCssEol(fs.readFileSync(cssPath, "utf8")) !== normalizeCssEol(generated.css)) {
      throw new Error("FullCalendar externalized artifacts are stale; run node scripts/externalize-fullcalendar-styles.js");
    }
    console.log("FullCalendar static CSS externalization is current (5 blocks, no CSSOM injection).");
    return;
  }
  fs.writeFileSync(bundlePath, generated.bundle);
  fs.writeFileSync(cssPath, generated.css);
  console.log("Externalized 5 fixed FullCalendar CSS blocks to web/vendor/fullcalendar.css.");
}

if (require.main === module) main();

module.exports = { extractStyles, generate, normalizeCssEol };
