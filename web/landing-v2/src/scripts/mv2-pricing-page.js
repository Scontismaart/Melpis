/* /landing-v2/piani/ — subnav dropdown, comparison search, FAQ tabs. */

const pricingRoot = document.querySelector('.mv2-pricing-page');

if (pricingRoot) {
  /* ---------------------------------------------------------------- */
  /* Sub-navigation dropdown                                          */
  /* ---------------------------------------------------------------- */

  const subnavMenu = pricingRoot.querySelector('[data-pp-subnav-menu]');

  if (subnavMenu) {
    const summary = subnavMenu.querySelector('summary');

    const closeMenu = (restoreFocus) => {
      if (!subnavMenu.open) return;
      subnavMenu.open = false;
      if (restoreFocus) summary?.focus();
    };

    document.addEventListener('click', (event) => {
      if (!subnavMenu.open) return;
      if (event.target instanceof Node && !subnavMenu.contains(event.target)) closeMenu(false);
    });

    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') closeMenu(true);
    });

    subnavMenu.addEventListener('click', (event) => {
      if (event.target instanceof Element && event.target.closest('a')) closeMenu(false);
    });
  }

  /* ---------------------------------------------------------------- */
  /* Comparison search and collapsible groups                         */
  /* ---------------------------------------------------------------- */

  const table = pricingRoot.querySelector('[data-pp-table]');

  if (table) {
    const input = pricingRoot.querySelector('[data-pp-search]');
    const clearButton = pricingRoot.querySelector('[data-pp-search-clear]');
    const status = pricingRoot.querySelector('[data-pp-search-status]');
    const empty = pricingRoot.querySelector('[data-pp-empty]');
    const groups = Array.from(table.querySelectorAll('[data-pp-group]'));

    const normalize = (value) =>
      value
        .normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '')
        .toLowerCase()
        .replace(/\s+/g, ' ')
        .trim();

    const rowsOf = (group) => Array.from(group.querySelectorAll('[data-pp-row]'));
    const headRowOf = (group) => group.querySelector('[data-pp-group-row]');
    const toggleOf = (group) => group.querySelector('[data-pp-group-toggle]');

    /* Remembers each group's state outside of search, so clearing the
       search restores exactly what the visitor had open before. */
    const openState = new Map();
    groups.forEach((group) => {
      openState.set(group.dataset.ppGroup, toggleOf(group)?.getAttribute('aria-expanded') !== 'false');
    });

    const applyOpenState = (group) => {
      const open = openState.get(group.dataset.ppGroup) === true;
      rowsOf(group).forEach((row) => {
        row.hidden = !open;
      });
      toggleOf(group)?.setAttribute('aria-expanded', String(open));
      group.dataset.ppOpen = String(open);
    };

    groups.forEach((group) => {
      applyOpenState(group);
      toggleOf(group)?.addEventListener('click', () => {
        if (input && input.value.trim()) return;
        const key = group.dataset.ppGroup;
        openState.set(key, openState.get(key) !== true);
        applyOpenState(group);
      });
    });

    const runSearch = () => {
      if (!input) return;
      const terms = normalize(input.value).split(' ').filter(Boolean);
      const searching = terms.length > 0;
      let matches = 0;

      groups.forEach((group) => {
        const rows = rowsOf(group);
        let groupMatches = 0;

        rows.forEach((row) => {
          if (!searching) return;
          const haystack = normalize(row.dataset.ppKeywords || '');
          const hit = terms.every((term) => haystack.includes(term));
          row.hidden = !hit;
          if (hit) groupMatches += 1;
        });

        if (searching) {
          matches += groupMatches;
          const headRow = headRowOf(group);
          if (headRow) headRow.hidden = groupMatches === 0;
          const toggle = toggleOf(group);
          if (toggle) {
            toggle.disabled = true;
            toggle.setAttribute('aria-expanded', 'true');
          }
          return;
        }

        const headRow = headRowOf(group);
        if (headRow) headRow.hidden = false;
        const toggle = toggleOf(group);
        if (toggle) toggle.disabled = false;
        applyOpenState(group);
      });

      if (empty) empty.hidden = !(searching && matches === 0);
      if (clearButton) clearButton.hidden = !searching;
      if (status) {
        if (!searching) status.textContent = '';
        else if (matches === 1) status.textContent = '1 funzionalità trovata';
        else status.textContent = `${matches} funzionalità trovate`;
      }
    };

    input?.addEventListener('input', runSearch);

    input?.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape' || !input.value) return;
      event.preventDefault();
      input.value = '';
      runSearch();
    });

    clearButton?.addEventListener('click', () => {
      if (!input) return;
      input.value = '';
      runSearch();
      input.focus();
    });
  }

  /* ---------------------------------------------------------------- */
  /* FAQ tabs                                                         */
  /* ---------------------------------------------------------------- */

  const tablist = pricingRoot.querySelector('[data-pp-tabs]');

  if (tablist) {
    const tabs = Array.from(tablist.querySelectorAll('[role="tab"]'));
    const panels = Array.from(pricingRoot.querySelectorAll('[data-pp-panel]'));

    const selectTab = (tab, focus) => {
      tabs.forEach((item) => {
        const active = item === tab;
        item.setAttribute('aria-selected', String(active));
        item.tabIndex = active ? 0 : -1;
      });

      const panelId = tab.getAttribute('aria-controls');
      panels.forEach((panel) => {
        const active = panel.id === panelId;
        panel.hidden = !active;
        panel.dataset.active = String(active);
      });

      if (focus) tab.focus();
    };

    tabs.forEach((tab, index) => {
      tab.addEventListener('click', () => selectTab(tab, false));

      tab.addEventListener('keydown', (event) => {
        let nextIndex = null;

        if (event.key === 'ArrowRight') nextIndex = (index + 1) % tabs.length;
        else if (event.key === 'ArrowLeft') nextIndex = (index - 1 + tabs.length) % tabs.length;
        else if (event.key === 'Home') nextIndex = 0;
        else if (event.key === 'End') nextIndex = tabs.length - 1;

        if (nextIndex === null) return;
        event.preventDefault();
        selectTab(tabs[nextIndex], true);
      });
    });
  }
}
