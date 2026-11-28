/* Melpis Landing V2 — Section 05 pricing motion.
   One dedicated IntersectionObserver + WAAPI reveal, a scroll-linked curve
   micro-move, and a throttled specular glint on the featured card.
   Every animation is cancelled on finish so no residual transform/opacity
   can compete with the CSS hover states. Reduced motion stays instant. */

const pricingSection = document.querySelector('[data-mv2-pricing-section]');
const pricingDoc = document.documentElement;

if (pricingSection instanceof HTMLElement) {
  const reduceQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
  const finePointerQuery = window.matchMedia('(hover: hover) and (pointer: fine)');

  const header = pricingSection.querySelector('[data-mv2-pricing-header]');
  const cards = Array.from(pricingSection.querySelectorAll('[data-mv2-pricing-card]'));
  const footnote = pricingSection.querySelector('[data-mv2-pricing-footnote]');
  const featuredCard = pricingSection.querySelector('[data-mv2-pricing-featured]');
  const sheen = pricingSection.querySelector('[data-mv2-pricing-sheen]');
  const curveStage = document.querySelector('[data-mv2-pricing-curve]');
  const curveMotion = document.querySelector('[data-mv2-pricing-curve-motion]');

  const easing = 'cubic-bezier(0.22, 1, 0.36, 1)';
  const running = new Set();
  let observer = null;
  let scrollFrame = 0;
  let pointerFrame = 0;
  let sheenAnimation = null;
  let hoverPoint = null;
  let revealed = false;

  const cancelRunning = () => {
    running.forEach((animation) => {
      try {
        animation.cancel();
      } catch (error) {
        /* Already cancelled. */
      }
    });
    running.clear();
  };

  const cancelSheen = () => {
    if (!sheenAnimation) return;
    try {
      sheenAnimation.cancel();
    } catch (error) {
      /* Already cancelled. */
    }
    sheenAnimation = null;
  };

  const animateOnce = (element, keyframes, options) => {
    if (!(element instanceof HTMLElement)) return;
    const animation = element.animate(keyframes, options);
    running.add(animation);
    animation.finished
      .then(() => {
        running.delete(animation);
        animation.cancel();
      })
      .catch(() => {
        running.delete(animation);
      });
  };

  const reveal = () => {
    if (revealed) return;
    revealed = true;
    pricingSection.classList.add('mv2-pricing-inview');

    if (reduceQuery.matches) {
      cancelRunning();
      cancelSheen();
      return;
    }

    animateOnce(
      header,
      [
        { opacity: 0, transform: 'translate3d(0, 12px, 0)' },
        { opacity: 1, transform: 'translate3d(0, 0, 0)' }
      ],
      { duration: 580, easing, fill: 'both' }
    );

    cards.forEach((card, index) => {
      animateOnce(
        card,
        [
          { opacity: 0, transform: 'translate3d(0, 18px, 0)' },
          { opacity: 1, transform: 'translate3d(0, 0, 0)' }
        ],
        { duration: 640, delay: 75 + index * 80, easing, fill: 'both' }
      );
    });

    animateOnce(
      sheen,
      [
        { transform: 'translate3d(-12%, -8%, 0)', opacity: 0.55 },
        { transform: 'translate3d(0%, 0%, 0)', opacity: 1 }
      ],
      { duration: 1050, delay: 160, easing: 'cubic-bezier(0.16, 1, 0.3, 1)', fill: 'both' }
    );

    animateOnce(
      footnote,
      [
        { opacity: 0, transform: 'translate3d(0, 8px, 0)' },
        { opacity: 1, transform: 'translate3d(0, 0, 0)' }
      ],
      { duration: 500, delay: 310, easing, fill: 'both' }
    );
  };

  /* ---------------------------------------------------------------- */
  /* Reveal                                                           */
  /* ---------------------------------------------------------------- */

  if (!reduceQuery.matches) {
    pricingDoc.classList.add('mv2-pricing-motion');

    if ('IntersectionObserver' in window) {
      observer = new IntersectionObserver(
        (entries, instance) => {
          entries.forEach((entry) => {
            if (!entry.isIntersecting) return;
            reveal();
            instance.disconnect();
            observer = null;
          });
        },
        { threshold: 0.1 }
      );
      observer.observe(pricingSection);
    } else {
      reveal();
    }
  }

  /* ---------------------------------------------------------------- */
  /* Scroll-linked curve micro-move                                   */
  /* ---------------------------------------------------------------- */

  const updateCurve = () => {
    scrollFrame = 0;
    if (!(curveStage instanceof HTMLElement) || !(curveMotion instanceof HTMLElement)) return;
    if (reduceQuery.matches) {
      curveMotion.style.transform = '';
      return;
    }
    const rect = curveStage.getBoundingClientRect();
    const viewport = window.innerHeight || 800;
    const progress = Math.max(-1, Math.min(1, (viewport * 0.45 - rect.top) / viewport));
    const shiftY = (1 - Math.min(1, Math.max(0, progress + 0.6))) * 6;
    curveMotion.style.transform = `translate3d(0, ${shiftY.toFixed(2)}px, 0)`;
  };

  const scheduleCurve = () => {
    if (scrollFrame) return;
    scrollFrame = window.requestAnimationFrame(updateCurve);
  };

  /* ---------------------------------------------------------------- */
  /* Featured card specular glint                                     */
  /* ---------------------------------------------------------------- */

  const moveSheen = () => {
    pointerFrame = 0;
    if (!hoverPoint || !(featuredCard instanceof HTMLElement) || !(sheen instanceof HTMLElement)) return;
    if (reduceQuery.matches) return;
    const rect = featuredCard.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const relX = (hoverPoint.x - rect.left) / rect.width - 0.5;
    const relY = (hoverPoint.y - rect.top) / rect.height - 0.5;
    cancelSheen();
    sheenAnimation = sheen.animate(
      [
        {
          transform: `translate3d(${(relX * 12).toFixed(2)}%, ${(relY * 8).toFixed(2)}%, 0)`
        }
      ],
      { duration: 300, easing: 'ease-out', fill: 'forwards' }
    );
  };

  const onPointerMove = (event) => {
    if (reduceQuery.matches) return;
    hoverPoint = { x: event.clientX, y: event.clientY };
    if (pointerFrame) return;
    pointerFrame = window.requestAnimationFrame(moveSheen);
  };

  const onPointerLeave = () => {
    hoverPoint = null;
    if (pointerFrame) {
      window.cancelAnimationFrame(pointerFrame);
      pointerFrame = 0;
    }
    cancelSheen();
    if (reduceQuery.matches || !(sheen instanceof HTMLElement)) return;
    sheenAnimation = sheen.animate(
      [{ transform: 'translate3d(0%, 0%, 0)' }],
      { duration: 480, easing, fill: 'forwards' }
    );
  };

  if (finePointerQuery.matches && featuredCard instanceof HTMLElement) {
    featuredCard.addEventListener('pointermove', onPointerMove, { passive: true });
    featuredCard.addEventListener('pointerleave', onPointerLeave, { passive: true });
  }

  /* ---------------------------------------------------------------- */
  /* Lifecycle                                                        */
  /* ---------------------------------------------------------------- */

  const onReduceChange = (event) => {
    if (!event.matches) return;
    cancelRunning();
    cancelSheen();
    revealed = true;
    pricingSection.classList.add('mv2-pricing-inview');
    if (curveMotion instanceof HTMLElement) curveMotion.style.transform = '';
    if (observer) {
      observer.disconnect();
      observer = null;
    }
  };

  if (typeof reduceQuery.addEventListener === 'function') {
    reduceQuery.addEventListener('change', onReduceChange);
  }

  const cleanup = () => {
    if (observer) {
      observer.disconnect();
      observer = null;
    }
    if (scrollFrame) {
      window.cancelAnimationFrame(scrollFrame);
      scrollFrame = 0;
    }
    if (pointerFrame) {
      window.cancelAnimationFrame(pointerFrame);
      pointerFrame = 0;
    }
    cancelRunning();
    cancelSheen();
    window.removeEventListener('scroll', scheduleCurve);
    window.removeEventListener('resize', scheduleCurve);
    if (featuredCard instanceof HTMLElement) {
      featuredCard.removeEventListener('pointermove', onPointerMove);
      featuredCard.removeEventListener('pointerleave', onPointerLeave);
    }
    if (typeof reduceQuery.removeEventListener === 'function') {
      reduceQuery.removeEventListener('change', onReduceChange);
    }
  };

  window.addEventListener('pagehide', cleanup, { once: true });
  window.addEventListener('scroll', scheduleCurve, { passive: true });
  window.addEventListener('resize', scheduleCurve, { passive: true });
  updateCurve();
}
