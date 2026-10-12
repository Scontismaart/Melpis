import { defineConfig } from 'astro/config';
import react from '@astrojs/react';

export default defineConfig({
  base: '/landing-v2/',
  trailingSlash: 'always',
  output: 'static',
  srcDir: './src',
  publicDir: './public',
  outDir: './dist',
  compressHTML: true,
  build: { format: 'file', inlineStylesheets: 'never' },
  markdown: { syntaxHighlight: false },
  devToolbar: { enabled: false },
  integrations: [react()],
  security: {
    csp: {
      algorithm: 'SHA-256',
      directives: ["default-src 'self'", "base-uri 'none'", "form-action 'none'", "object-src 'none'", "img-src 'self' data:", "font-src 'self'", "connect-src 'none'", "frame-src 'none'"],
    },
  },
});
