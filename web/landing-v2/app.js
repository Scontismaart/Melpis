const mv2Root = document.querySelector('.mv2-page');

if (mv2Root) {
  const mv2Toggle = mv2Root.querySelector('.mv2-menu-toggle');
  const mv2Nav = mv2Root.querySelector('#mv2-nav');
  const mv2Mobile = window.matchMedia('(max-width: 680px)');
  mv2Root.dataset.mv2Enhanced = 'true';

  function mv2SetMenu(open) {
    if (!mv2Toggle || !mv2Nav) return;
    mv2Toggle.setAttribute('aria-expanded', String(open));
    mv2Toggle.setAttribute('aria-label', open ? 'Chiudi menu' : 'Apri menu');
    mv2Nav.dataset.mv2Open = String(open);
  }

  mv2Toggle?.addEventListener('click', () => {
    mv2SetMenu(mv2Toggle.getAttribute('aria-expanded') !== 'true');
  });
  mv2SetMenu(false);

  mv2Nav?.addEventListener('click', (event) => {
    if (event.target instanceof Element && event.target.closest('a')) mv2SetMenu(false);
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && mv2Toggle?.getAttribute('aria-expanded') === 'true') {
      mv2SetMenu(false);
      mv2Toggle.focus();
    }
  });

  mv2Mobile.addEventListener('change', (event) => {
    if (!event.matches) mv2SetMenu(false);
  });
}
