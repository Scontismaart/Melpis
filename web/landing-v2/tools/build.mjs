import { cp, mkdir, rm } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const dist = path.join(root, 'dist');
const sourceFonts = path.join(root, 'assets', 'fonts');
const files = ['index.html', 'styles.css', 'app.js'];
const fontFiles = ['mv2-outfit-latin.woff2', 'mv2-outfit-latin-ext.woff2', 'mv2-inter-latin.woff2'];
const fontLicenses = ['Inter-OFL.txt', 'Outfit-OFL.txt'];

await rm(dist, { recursive: true, force: true });
for (const file of files) await cp(path.join(root, file), path.join(dist, file));
const distFonts = path.join(dist, 'assets', 'fonts');
await mkdir(path.join(distFonts, 'licenses'), { recursive: true });
for (const file of fontFiles) await cp(path.join(sourceFonts, file), path.join(distFonts, file));
for (const file of fontLicenses) await cp(path.join(sourceFonts, 'licenses', file), path.join(distFonts, 'licenses', file));
await cp(path.join(root, 'robots.txt'), path.join(dist, 'robots.txt'));
await cp(path.join(root, 'assets', 'favicon.svg'), path.join(dist, 'assets', 'favicon.svg'));
console.log(`Landing V2 pronta: ${path.relative(root, dist)} (${files.length} pagine/app + ${fontFiles.length} font)`);
