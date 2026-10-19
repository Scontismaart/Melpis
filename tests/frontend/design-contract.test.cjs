const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { JSDOM } = require('jsdom');

const root = path.resolve(__dirname, '../..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');

test('shared visual tokens keep the primary palette monochrome', () => {
  const css = read('web/design-tokens.css');
  assert.match(css, /--melpis-canvas:\s*#090909/);
  assert.match(css, /--melpis-accent:\s*#ffffff/);
  assert.match(read('web/style.css'), /@import url\('\/app\/design-tokens\.css'\)/);
  assert.match(read('web/auth.css'), /@import url\('\/design-tokens\.css'\)/);
  assert.match(read('web/landing/style.css'), /@import url\('\/design-tokens\.css'\)/);
});

test('landing plan cards match confirmed prices and monthly message quotas', () => {
  const dom = new JSDOM(read('web/landing/index.html'));
  const cards = [...dom.window.document.querySelectorAll('.price-card-shell')];
  assert.equal(cards.length, 3);
  const expected = [
    ['29', '500'],
    ['69', '2.000'],
    ['149', '10.000'],
  ];
  cards.forEach((card, index) => {
    const price = card.querySelector('.p-amount');
    assert.equal(price?.dataset.monthly, expected[index][0]);
    assert.equal(price?.dataset.annual, undefined);
    assert.ok(card.textContent.includes(`Fino a ${expected[index][1]} messaggi/mese`));
  });
});

test('all launch landing home pages show monthly-only pricing', () => {
  const pages = [
    ['web/landing/index.html', 'Fatturazione Mensile'],
    ['web/landing/en/index.html', 'Monthly'],
    ['web/landing/es/index.html', 'Mensual'],
    ['web/landing/fr/index.html', 'Mensuel'],
    ['web/landing/de/index.html', 'Monatlich'],
  ];
  for (const [file, monthlyLabel] of pages) {
    const document = new JSDOM(read(file)).window.document;
    const billing = document.querySelector('.billing-toggle-wrap');
    assert.ok(billing?.querySelector('.toggle-btn[aria-current="true"]'), `${file} has an accessible monthly label`);
    assert.equal(billing?.textContent.trim(), monthlyLabel, `${file} has a localized monthly label`);
    assert.equal(billing?.querySelector('button'), null, `${file} has no billing toggle`);
    assert.equal(document.querySelector('#btnAnnual'), null, `${file} has no annual option`);
    const prices = [...document.querySelectorAll('.p-amount')].map((price) => price.textContent.trim());
    assert.deepEqual(prices, ['29', '69', '149'], `${file} shows approved monthly prices`);
    assert.equal(document.querySelectorAll('.p-amount[data-annual]').length, 0, `${file} exposes no annual display prices`);
  }
  const app = read('web/landing/app.js');
  assert.doesNotMatch(app, /btnAnnual|data-annual|setBilling\s*\(/);
});

test('localized signup success and account link do not fall back to Italian', () => {
  const pages = [
    'web/en/signup/index.html',
    'web/es/registro/index.html',
    'web/fr/inscription/index.html',
    'web/de/registrieren/index.html',
  ];
  pages.forEach((file) => {
    const dom = new JSDOM(read(file));
    const text = dom.window.document.body.textContent;
    assert.doesNotMatch(text, /Hai già un account\?|Account creato\.|Ti abbiamo inviato/);
    const success = dom.window.document.querySelector('#register-success');
    assert.equal(success?.getAttribute('role'), 'status');
    assert.ok(success?.querySelector('#success-login-link'));
  });
});

test('all pricing locales use confirmed limits and canonical monthly prices', () => {
  const limits = [500, 2000, 10000];
  const monthly = [29, 69, 149];
  for (const lang of ['it', 'en', 'es', 'fr', 'de']) {
    for (const file of [`locales/${lang}/pricing.json`, `web/locales/${lang}/pricing.json`, `web/landing/locales/${lang}/pricing.json`]) {
      const pricing = JSON.parse(read(file));
      const plans = [pricing.plans.essential, pricing.plans.growth, pricing.plans.scale];
      assert.equal(pricing.hero.annual_toggle, undefined, `${file} has no annual toggle metadata`);
      assert.equal(pricing.hero.annual_discount, undefined, `${file} has no annual billing metadata`);
      plans.forEach((plan, index) => {
        assert.equal(plan.price_monthly, monthly[index], `${file} monthly price ${index}`);
        assert.equal(plan.price_annual, undefined, `${file} has no annual price ${index}`);
        assert.ok(plan.features.some((feature) => feature.replace(/\D/g, '') === String(limits[index])), `${file} message limit ${limits[index]}`);
        assert.ok(plan.features.every((feature) => !/unlimited ai conversations|conversazioni ai illimitate|conversaciones con ia ilimitadas|conversations ia illimitées|unbegrenzte ki-konversationen/i.test(feature)));
      });
    }
  }
});

test('landing FAQ states that the trial ends at the first 7-day or 150-message limit', () => {
  for (const lang of ['it', 'en', 'es', 'fr', 'de']) {
    const landing = JSON.parse(read(`locales/${lang}/landing.json`));
    const trial = landing.faq.items[4].a;
    assert.match(trial, /150/);
    assert.match(trial, /7/);
  }
});

test('release hero uses the supplied artwork and keeps the primary action visible', () => {
  const landing = new JSDOM(read('web/landing/index.html'));
  const hero = landing.window.document.querySelector('.hero-section');
  assert.ok(hero);
  assert.match(hero.textContent, /Meno messaggi da gestire\./);
  assert.match(hero.textContent, /Più tempo per far crescere la tua attività\./);
  assert.ok(hero.querySelector('.hero-cta-row .btn-cta-primary'));
  assert.match(read('web/landing/style.css'), /sfondohero-release\.webp/);
  assert.ok(fs.statSync(path.join(root, 'web/landing/sfondohero-release.webp')).size > 1000);
});

test('dashboard keeps a real date input and no overview welcome block', () => {
  const dashboard = new JSDOM(read('web/index.html'));
  const document = dashboard.window.document;
  assert.equal(document.querySelector('#booking-date-picker')?.getAttribute('type'), 'date');
  assert.ok(document.querySelector('#booking-date-picker-trigger'));
  assert.ok(document.querySelector('#booking-selected-date-label'));
  assert.equal(document.querySelector('#panoramica-business-name'), null);
  assert.equal(document.querySelectorAll('.ai-config-group-title').length, 2);
});

test('dashboard theme controls inherit colors and Google Reviews has no static connected claim', () => {
  const document = new JSDOM(read('web/index.html')).window.document;
  const css = read('web/style.css');
  assert.equal(document.querySelector('.sidebar-footer-note'), null);
  assert.ok(document.querySelector('#sidebar-lang-select.sidebar-lang-select'));
  const status = document.querySelector('#reviews-google-summary-status');
  assert.ok(status);
  assert.equal(status.classList.contains('ready'), false);
  assert.equal(status.dataset.state, 'loading');
  const integrations = read('web/dashboard-integrations.js');
  assert.match(integrations, /\/api\/reviews\/google\/status/);
  assert.match(integrations, /const operational = d\.operational === true/);
  assert.match(integrations, /setSummaryStatus\(operational \? true : null\)/);
  assert.match(integrations, /setSummaryStatus\(false\)/);
  assert.match(css, /\.nav-item \.nav-icon[\s\S]*?color:\s*currentColor/);
  assert.match(css, /\.sidebar-lang-select[\s\S]*?appearance:\s*none/);
  assert.match(css, /\.priority-item-badge[\s\S]*?background:\s*var\(--dashboard-surface-subtle\)/);
  assert.match(css, /\.topbar\s*\{\s*position:\s*relative;\s*top:\s*auto;/);
});

test('theme prepaint honors saved light, dark, system and safe fallback', () => {
  const script = read('web/theme-init.js');
  const resolve = (saved, systemDark, brokenStorage = false) => {
    const document = { documentElement: { dataset: {} } };
    const localStorage = { getItem: () => {
      if (brokenStorage) throw new Error('storage blocked');
      return saved;
    } };
    vm.runInNewContext(script, { document, localStorage, window: { matchMedia: () => ({ matches: systemDark }) } });
    return document.documentElement.dataset.theme;
  };
  assert.equal(resolve('light', true), 'light');
  assert.equal(resolve('dark', false), 'dark');
  assert.equal(resolve('system', false), 'light');
  assert.equal(resolve('system', true), 'dark');
  assert.equal(resolve(null, false, true), 'dark');
});
