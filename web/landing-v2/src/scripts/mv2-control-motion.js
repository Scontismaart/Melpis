const STAGE_SELECTOR = '[data-mv2-control-stage]';
const EASE = 'cubic-bezier(0.22, 1, 0.36, 1)';

const DASH_DURATION = 900;
const DASH_DELAY = 100;
const CARD_DURATION = 780;
const CARD_DELAYS = [520, 700, 890];

const ENTRIES = [
  { x: -8, y: 58, z: -60, rotation: -5, scale: 0.94 },
  { x: 34, y: 64, z: -30, rotation: 4, scale: 0.95 },
  { x: 58, y: 74, z: 20, rotation: -3, scale: 0.92 },
];

function prefersReducedMotion() {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch (error) {
    return false;
  }
}

function frame({ x, y, z, rotation, scale }) {
  return `translate3d(${x}px, ${y}px, ${z}px) rotate(${rotation}deg) scale(${scale})`;
}

function reveal(element, from, to, duration, delay) {
  return element.animate(
    [
      { opacity: 0, transform: from },
      { opacity: 1, transform: to },
    ],
    { duration, delay, easing: EASE, fill: 'both' },
  );
}

function play(stage) {
  const dash = stage.querySelector('.mv2-ctl-dash');
  const cards = Array.from(stage.querySelectorAll('.mv2-control-card'));
  if (!dash || cards.length === 0) return;

  const animations = [
    reveal(dash, 'translateY(35px) scale(0.96)', 'translateY(0) scale(1)', DASH_DURATION, DASH_DELAY),
  ];

  cards.forEach((card, index) => {
    const entry = ENTRIES[index] || ENTRIES[ENTRIES.length - 1];
    const delay = CARD_DELAYS[index] ?? CARD_DELAYS[CARD_DELAYS.length - 1];
    animations.push(
      reveal(card, frame(entry), 'translate3d(0, 0, 0) rotate(0deg) scale(1)', CARD_DURATION, delay),
    );
  });

  let settled = false;
  const settle = () => {
    if (settled) return;
    settled = true;
    stage.classList.add('is-settled');
    for (const animation of animations) animation.cancel();
  };

  Promise.all(animations.map((animation) => animation.finished)).then(settle, settle);

  const total = Math.max(
    DASH_DELAY + DASH_DURATION,
    CARD_DELAYS[cards.length - 1] + CARD_DURATION,
  );
  window.setTimeout(settle, total + 250);
}

function init() {
  const stage = document.querySelector(STAGE_SELECTOR);
  if (!stage || prefersReducedMotion()) return;

  if (typeof Element.prototype.animate !== 'function') {
    stage.classList.add('is-settled');
    return;
  }

  let played = false;
  const trigger = () => {
    if (played) return;
    played = true;
    play(stage);
  };

  if (typeof IntersectionObserver !== 'function') {
    trigger();
    return;
  }

  const observer = new IntersectionObserver(
    (entries) => {
      if (!entries.some((entry) => entry.isIntersecting)) return;
      observer.disconnect();
      trigger();
    },
    { threshold: 0.2, rootMargin: '0px 0px -12% 0px' },
  );

  observer.observe(stage);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init, { once: true });
} else {
  init();
}
