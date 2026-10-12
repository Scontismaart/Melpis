import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import assert from 'node:assert/strict';

const repository = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const baseline = process.env.MV2_BASE_REF || '2994fae7ea54094482a40374ea46c08aae8d0407';
const git = (args, options = {}) => execFileSync('git', args, { cwd: repository, maxBuffer: 40 * 1024 * 1024, ...options });
const paths = (buffer) => buffer.toString('utf8').split('\0').filter(Boolean);
const permitted = (name) => name.startsWith('web/landing-v2/') || name === '.github/workflows/landing-v2.yml';
const committed = paths(git(['diff', '--name-only', '-z', baseline, 'HEAD']));
assert.deepEqual(committed.filter((name) => !permitted(name)), [], 'Committed Phase 1 changes must stay within V2 and its new workflow.');
const changed = paths(git(['diff', '--name-only', '-z', baseline]));
const untracked = paths(git(['ls-files', '--others', '--exclude-standard', '-z']));
// Repository instructions require a local graph refresh. Its generated data may remain
// dirty locally, but the preceding committed-scope assertion forbids shipping it in this PR.
assert.deepEqual([...changed, ...untracked].filter((name) => !permitted(name) && !name.startsWith('graphify-out/')), [], 'Phase 1 must not change protected files outside V2 and its new CI workflow.');

// --filters reproduces checkout line endings and attributes on Windows as well as Linux.
// Compare actual bytes, not whitespace-insensitive diffs, for every protected V1 asset/page.
const protectedFiles = paths(git(['ls-tree', '-r', '--name-only', '-z', baseline, '--', 'web', 'src', 'locales', 'Dockerfile', 'docker-compose.yml', 'compose.production.yml', 'package.json', 'package-lock.json', '.github/workflows/ci.yml', '.github/workflows/migrations.yml']));
for (const name of protectedFiles) {
  const expected = git(['cat-file', '--filters', `${baseline}:${name}`]);
  assert.ok(expected.equals(readFileSync(path.join(repository, name))), `Protected file bytes changed: ${name}`);
}
console.log(`V1, dashboard, backend and production config unchanged: ${protectedFiles.length} protected files byte-identical; ${new Set([...changed, ...untracked]).size} phase files within allowlist.`);
