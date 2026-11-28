import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

import * as saloniScenarios from '../src/components/melpis-saloni-flow-scenarios.mjs';
import * as hotelScenarios from '../src/components/melpis-hotel-flow-scenarios.mjs';
import * as barbieriScenarios from '../src/components/melpis-barbieri-flow-scenarios.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const node = process.execPath;

test('saloni, hotel, and barbieri scenario modules provide valid and coherent flow definitions', () => {
  const suites = [
    { name: 'Saloni', mod: saloniScenarios },
    { name: 'Hotel', mod: hotelScenarios },
    { name: 'Barbieri', mod: barbieriScenarios },
  ];

  for (const { name, mod } of suites) {
    assert.equal(mod.steps.length, 6, `${name} must have 6 flow steps`);
    assert.equal(mod.LAST_STEP, 5, `${name} LAST_STEP must be 5`);
    assert.equal(mod.scenarioOrder.length, 4, `${name} must offer exactly 4 selectable scenarios in scenarioOrder`);
    assert.equal(Object.keys(mod.scenarios).length, 4, `${name} must offer exactly 4 selectable scenarios in scenarios map`);
    assert.equal(mod.stepDurations.length, 6, `${name} must define durations for each step`);

    for (const scId of mod.scenarioOrder) {
      const sc = mod.scenarios[scId];
      assert.ok(sc, `${name}: scenario missing for id ${scId}`);
      assert.ok(sc.nav, `${name}: scenario missing nav`);
      assert.ok(sc.title, `${name}: scenario missing title`);
      assert.ok(sc.client && sc.client.name, `${name}: scenario missing client name`);
      assert.ok(sc.message, `${name}: scenario missing first message`);
      assert.ok(sc.reply, `${name}: scenario missing reply`);
      assert.ok(sc.outcome && sc.outcome.title, `${name}: scenario missing outcome title`);
      assert.ok(Array.isArray(sc.entities), `${name}: scenario entities must be array`);
    }
  }
});

test('saloni, hotel, and barbieri pages are built following the official Ristoranti template', async (t) => {
  const sectors = [
    {
      slug: 'hotel',
      htmlFile: 'dist/hotel.html',
      badge: 'Hotel',
      h1Match: /quando la reception/i,
      expectedHeadings: [
        /Rispondi a ogni ospite\./,
        /Tutte le informazioni della struttura/,
        /L'assistente accoglie\./,
        /Migliora la reputazione online e/,
        /Scegli il passo/,
        /Prima di attivare/,
        /Melpis in hotel\./,
        /Porta Melpis/,
      ],
      themeClass: 'mv2-sector--hotel',
    },
    {
      slug: 'barbieri',
      htmlFile: 'dist/barbieri.html',
      badge: 'Barbieri',
      h1Match: /Tu pensa ai clienti sulla poltrona/i,
      expectedHeadings: [
        /Tu pensa al taglio\./,
        /Smetti di interrompere il taglio/,
        /L'assistente organizza\./,
        /Recensioni sempre curate e/,
        /Scegli il passo/,
        /Prima di attivare/,
        /Melpis in salone\./,
        /Porta Melpis/,
      ],
      themeClass: 'mv2-sector--barbieri',
    },
    {
      slug: 'saloni',
      htmlFile: 'dist/saloni.html',
      badge: 'Centri estetici',
      h1Match: /Dedica più tempo ai trattamenti/i,
      expectedHeadings: [
        /Più tempo per le clienti\./,
        /Tutti i trattamenti del centro spiegati con cura/,
        /L'assistente accoglie\./,
        /Fai brillare la reputazione del tuo centro/,
        /Scegli il passo/,
        /Prima di attivare/,
        /Melpis nel centro\./,
        /Porta Melpis/,
      ],
      themeClass: 'mv2-sector--saloni',
    },
  ];

  for (const sector of sectors) {
    const html = await readFile(path.join(root, sector.htmlFile), 'utf8');

    // 1. Single H1 check
    const h1Matches = [...html.matchAll(/<h1\b[^>]*>([\s\S]*?)<\/h1>/gi)];
    assert.equal(h1Matches.length, 1, `Only one H1 must exist on /${sector.slug}/`);
    assert.match(h1Matches[0][1], sector.h1Match, `H1 on /${sector.slug}/ must match pattern`);

    // 2. Badge check
    assert.match(html, new RegExp(sector.badge), `Badge ${sector.badge} must be present`);

    // 3. Theme class and base layout classes on main
    assert.match(html, /class="[^"]*mv2-restaurants[^"]*"/, `Base .mv2-restaurants class must be present on /${sector.slug}/`);
    assert.match(html, new RegExp(`class="[^"]*${sector.themeClass}[^"]*"`), `Theme class ${sector.themeClass} must be applied`);

    // 4. Section headings check
    for (const pattern of sector.expectedHeadings) {
      assert.match(html, pattern, `Expected section heading matching ${pattern} on /${sector.slug}/`);
    }

    // 5. Structure parity: Scenarios track, Flow console, Knowledge showcase, Reviews showcase, Pricing, FAQ
    assert.match(html, /mv2-rest-scenarios-scroll-track/, `Scroll-driven scenarios track must be present on /${sector.slug}/`);
    assert.match(html, /mv2-flow-stage/, `Flow demo stage must be present on /${sector.slug}/`);
    assert.match(html, /mv2-rest-kb-stage/, `Knowledge base showcase must be present on /${sector.slug}/`);
    assert.match(html, /mv2-rest-reviews-stage/, `Reviews showcase must be present on /${sector.slug}/`);
    assert.match(html, /mv2-pricing-section/, `Pricing section must be present on /${sector.slug}/`);
    assert.match(html, /mv2-rest-faq-section/, `FAQ section must be present on /${sector.slug}/`);

    // 6. Navigation links
    assert.match(html, /href="#mv2-rest-piani"/, `CTA links must target #mv2-rest-piani on /${sector.slug}/`);
  }

  // Preview server live check
  const proc = spawn(node, ['tools/preview.mjs'], {
    cwd: root,
    env: { ...process.env, MV2_PORT: '0' },
    stdio: ['ignore', 'pipe', 'inherit'],
  });
  let startup = '';
  proc.stdout.setEncoding('utf8');
  for await (const chunk of proc.stdout) {
    startup += chunk;
    if (startup.includes('http://127.0.0.1:')) break;
  }
  t.after(() => proc.kill());
  const port = Number(startup.match(/:(\d+)\/landing-v2\//)?.[1]);
  assert.ok(port > 0, `Preview server failed to start: ${startup}`);
  const origin = `http://127.0.0.1:${port}`;

  for (const sector of sectors) {
    const res = await fetch(`${origin}/landing-v2/${sector.slug}/`);
    assert.equal(res.status, 200, `/${sector.slug}/ returned status ${res.status}`);
    assert.equal(res.headers.get('content-type'), 'text/html; charset=utf-8');
    assert.match(res.headers.get('content-security-policy'), /connect-src 'none'/);

    const body = await res.text();
    assert.match(body, new RegExp(sector.badge));
    assert.match(body, sector.h1Match);
  }
});
