import { createServer } from 'node:http';
import { access, readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'dist');
const port = Number(process.env.MV2_PORT ?? 4175);
const mime = new Map([
  ['.html', 'text/html; charset=utf-8'], ['.css', 'text/css; charset=utf-8'],
  ['.js', 'text/javascript; charset=utf-8'], ['.woff2', 'font/woff2'], ['.svg', 'image/svg+xml'],
]);
await access(path.join(root, 'index.html'));

const server = createServer(async (req, res) => {
  const headers = {
    'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self' data:; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    'X-Robots-Tag': 'noindex, nofollow, noarchive',
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer',
    'Cache-Control': 'no-store',
  };
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    res.writeHead(405, { ...headers, Allow: 'GET, HEAD' }).end();
    return;
  }
  let pathname;
  try {
    pathname = decodeURIComponent((req.url ?? '/').split('?')[0]);
  } catch {
    res.writeHead(400, headers).end();
    return;
  }
  if (pathname.includes('\\') || pathname.includes('\0') || pathname.split('/').some((part) => part === '..')) {
    res.writeHead(403, headers).end();
    return;
  }
  if (pathname === '/robots.txt') pathname = '/landing-v2/robots.txt';
  if (pathname === '/landing-v2' || pathname === '/landing-v2/') pathname = '/landing-v2/index.html';
  if (!pathname.startsWith('/landing-v2/')) {
    res.writeHead(404, headers).end();
    return;
  }
  const relative = pathname.slice('/landing-v2/'.length);
  if (!relative || relative.endsWith('/')) {
    res.writeHead(404, headers).end();
    return;
  }
  const target = path.resolve(root, relative);
  if (!target.startsWith(`${root}${path.sep}`)) {
    res.writeHead(403, headers).end();
    return;
  }
  try {
    const body = await readFile(target);
    res.writeHead(200, { ...headers, 'Content-Type': mime.get(path.extname(target)) ?? 'application/octet-stream', 'Content-Length': body.length });
    res.end(req.method === 'HEAD' ? undefined : body);
  } catch {
    res.writeHead(404, headers).end();
  }
});

server.listen(port, '127.0.0.1', () => console.log(`Preview V2: http://127.0.0.1:${server.address().port}/landing-v2/`));
