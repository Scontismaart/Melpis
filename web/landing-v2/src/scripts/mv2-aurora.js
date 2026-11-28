/**
 * melpis landing v2 — hero aurora gate.
 *
 * Decides whether the hero panel gets a WebGL aurora, mounts it through a lazy import so the
 * shader chunk is only fetched when it will actually be used, and keeps the static CSS mesh
 * fallback in place on every device that cannot run it (no JavaScript, no WebGL, reduced motion,
 * context loss). Never throws: the page must render identically without this file.
 */

const MV2_AURORA_REVEAL_CAP = 5200;

function mv2AuroraReduce() {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch (error) {
    return false;
  }
}

function mv2AuroraAfterReveal(run) {
  const root = document.documentElement;
  try {
    if (!root.classList.contains('mv2-preload') || root.classList.contains('mv2-ready')) {
      window.requestAnimationFrame(run);
      return;
    }
    let settled = false;
    let observer = null;
    const finish = () => {
      if (settled) return;
      settled = true;
      window.removeEventListener('mv2:reveal', finish);
      if (observer) observer.disconnect();
      window.requestAnimationFrame(run);
    };
    window.addEventListener('mv2:reveal', finish, { once: true });
    if (typeof window.MutationObserver === 'function') {
      observer = new window.MutationObserver(() => {
        if (root.classList.contains('mv2-ready')) finish();
      });
      observer.observe(root, { attributes: true, attributeFilter: ['class'] });
    }
    window.setTimeout(finish, MV2_AURORA_REVEAL_CAP);
  } catch (error) {
    try {
      run();
    } catch (inner) {
      /* the aurora is optional */
    }
  }
}

function mv2AuroraMount(panel) {
  const canvas = document.createElement('canvas');
  canvas.className = 'mv2-aurora-canvas';
  canvas.setAttribute('aria-hidden', 'true');
  panel.insertBefore(canvas, panel.firstChild);

  const remove = () => {
    if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
  };

  import('./mv2-aurora-webgl.js')
    .then((mod) => {
      let view = null;
      try {
        view = mod.mountAurora(canvas, { observe: panel });
      } catch (error) {
        view = null;
      }
      if (!view) {
        remove();
        return;
      }
      view.onLost(remove);
      view.start();
      window.requestAnimationFrame(() => canvas.classList.add('is-visible'));
    })
    .catch(remove);
}

try {
  const panel = document.querySelector('[data-mv2-aurora-panel]');
  if (panel && !mv2AuroraReduce()) mv2AuroraAfterReveal(() => mv2AuroraMount(panel));
} catch (error) {
  /* the static CSS mesh fallback stays as the hero backdrop */
}
