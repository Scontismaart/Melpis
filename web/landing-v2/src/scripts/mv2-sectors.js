(function () {
  const root = document.querySelector('.mv2-sector-page');
  if (!root) return;

  const prefersReducedMotion = (() => {
    try {
      return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    } catch {
      return false;
    }
  })();

  root.classList.add('mv2-sector-js-active');

  /* 1. Scroll Reveal with IntersectionObserver + WAAPI */
  const revealElements = root.querySelectorAll('[data-sector-reveal]');

  if (!prefersReducedMotion && typeof IntersectionObserver === 'function' && typeof Element.prototype.animate === 'function') {
    root.classList.add('mv2-sector-animated');

    const revealObserver = new IntersectionObserver(
      (entries, observer) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          const el = entry.target;
          const siblings = Array.from(el.parentElement ? el.parentElement.children : []);
          const staggerIndex = Math.max(0, siblings.indexOf(el));

          const animation = el.animate(
            [
              { opacity: 0, transform: 'translateY(14px)' },
              { opacity: 1, transform: 'translateY(0px)' },
            ],
            {
              duration: 500,
              delay: staggerIndex * 60,
              easing: 'cubic-bezier(0.22, 1, 0.36, 1)',
              fill: 'forwards',
            }
          );

          animation.onfinish = () => {
            el.classList.add('is-revealed');
          };

          observer.unobserve(el);
        });
      },
      { threshold: 0.1 }
    );

    revealElements.forEach((el) => revealObserver.observe(el));
  } else {
    revealElements.forEach((el) => {
      el.classList.add('is-revealed');
    });
  }

  /* 2. Interactive FAQ Accordion with WAAPI */
  const faqItems = root.querySelectorAll('.mv2-sector-faq__item');

  faqItems.forEach((item) => {
    const trigger = item.querySelector('.mv2-sector-faq__trigger');
    const content = item.querySelector('.mv2-sector-faq__pane');
    if (!trigger || !content) return;

    // Start closed
    content.style.display = 'none';

    trigger.addEventListener('click', () => {
      const isOpen = item.getAttribute('data-open') === 'true';

      // Close other open items
      faqItems.forEach((otherItem) => {
        if (otherItem !== item && otherItem.getAttribute('data-open') === 'true') {
          const otherTrigger = otherItem.querySelector('.mv2-sector-faq__trigger');
          const otherContent = otherItem.querySelector('.mv2-sector-faq__pane');
          otherItem.setAttribute('data-open', 'false');
          if (otherTrigger) otherTrigger.setAttribute('aria-expanded', 'false');

          if (otherContent) {
            if (!prefersReducedMotion && typeof otherContent.animate === 'function') {
              const startH = otherContent.scrollHeight;
              const closeAnim = otherContent.animate(
                [
                  { height: `${startH}px`, opacity: 1 },
                  { height: '0px', opacity: 0 },
                ],
                { duration: 200, easing: 'cubic-bezier(0.22, 1, 0.36, 1)' }
              );
              closeAnim.onfinish = () => {
                otherContent.style.display = 'none';
              };
            } else {
              otherContent.style.display = 'none';
            }
          }
        }
      });

      // Toggle current item
      if (!isOpen) {
        item.setAttribute('data-open', 'true');
        trigger.setAttribute('aria-expanded', 'true');
        content.style.display = 'block';

        if (!prefersReducedMotion && typeof content.animate === 'function') {
          const targetH = content.scrollHeight;
          content.animate(
            [
              { height: '0px', opacity: 0 },
              { height: `${targetH}px`, opacity: 1 },
            ],
            { duration: 240, easing: 'cubic-bezier(0.22, 1, 0.36, 1)' }
          );
        }
      } else {
        item.setAttribute('data-open', 'false');
        trigger.setAttribute('aria-expanded', 'false');

        if (!prefersReducedMotion && typeof content.animate === 'function') {
          const startH = content.scrollHeight;
          const shrinkAnim = content.animate(
            [
              { height: `${startH}px`, opacity: 1 },
              { height: '0px', opacity: 0 },
            ],
            { duration: 200, easing: 'cubic-bezier(0.22, 1, 0.36, 1)' }
          );
          shrinkAnim.onfinish = () => {
            content.style.display = 'none';
          };
        } else {
          content.style.display = 'none';
        }
      }
    });
  });
})();
