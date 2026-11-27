import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const astroCli = path.join(root, 'node_modules', 'astro', 'bin', 'astro.mjs');
const result = spawnSync(process.execPath, [astroCli, 'build'], {
  cwd: root,
  stdio: 'inherit',
  env: { ...process.env, ASTRO_TELEMETRY_DISABLED: '1' },
});
if (result.error) throw result.error;
process.exitCode = result.status ?? 1;
