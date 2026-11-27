const mv2Doc = document.documentElement;
const mv2Reduce = (() => {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch (error) {
    return false;
  }
})();
const mv2FinePointer = (() => {
  try {
    return window.matchMedia('(hover: hover) and (pointer: fine)').matches;
  } catch (error) {
    return false;
  }
})();

let mv2MotionDispatched = false;
function mv2SignalMotion() {
  if (mv2MotionDispatched) return;
  mv2MotionDispatched = true;
  window.__mv2MotionReady = true;
  window.dispatchEvent(new CustomEvent('mv2:motionready'));
}

function mv2RevealAll() {
  mv2Doc.classList.add('mv2-motion');
  mv2Doc.classList.add('mv2-ready');
  const nodes = document.querySelectorAll('[data-mv2-reveal]');
  for (let i = 0; i < nodes.length; i += 1) nodes[i].classList.add('is-visible');
  const heroNodes = document.querySelectorAll('[data-mv2-hero]');
  for (let i = 0; i < heroNodes.length; i += 1) heroNodes[i].classList.add('is-visible');
}

function mv2SplitHeading(heading) {
  if (heading.dataset.mv2Split === 'done') return;
  heading.dataset.mv2Split = 'done';

  const groups = [];
  let current = [];
  const children = Array.prototype.slice.call(heading.childNodes);
  for (let i = 0; i < children.length; i += 1) {
    const node = children[i];
    if (node.nodeType === 1 && node.tagName === 'BR') {
      groups.push(current);
      current = [];
    } else {
      current.push(node);
    }
  }
  groups.push(current);

  while (heading.firstChild) heading.removeChild(heading.firstChild);

  let wordIndex = 0;
  let lineIndex = 0;
  for (let g = 0; g < groups.length; g += 1) {
    const group = groups[g];
    if (!group.length) continue;
    const line = document.createElement('span');
    line.className = 'mv2-hline';
    const inner = document.createElement('span');
    inner.className = 'mv2-hline-in';
    inner.style.setProperty('--line', String(lineIndex));
    lineIndex += 1;

    for (let n = 0; n < group.length; n += 1) inner.appendChild(group[n]);

    const walker = document.createTreeWalker(inner, NodeFilter.SHOW_TEXT);
    const textNodes = [];
    let textNode;
    while ((textNode = walker.nextNode())) {
      if (textNode.nodeValue && textNode.nodeValue.trim()) textNodes.push(textNode);
    }
    for (let t = 0; t < textNodes.length; t += 1) {
      const node = textNodes[t];
      const parts = node.nodeValue.split(/(\s+)/);
      const fragment = document.createDocumentFragment();
      for (let p = 0; p < parts.length; p += 1) {
        const part = parts[p];
        if (!part) continue;
        if (/^\s+$/.test(part)) {
          fragment.appendChild(document.createTextNode(part));
          continue;
        }
        const word = document.createElement('span');
        word.className = 'mv2-word';
        const wordInner = document.createElement('span');
        wordInner.className = 'mv2-word-in';
        wordInner.style.setProperty('--word', String(wordIndex));
        wordIndex += 1;
        wordInner.textContent = part;
        word.appendChild(wordInner);
        fragment.appendChild(word);
      }
      if (node.parentNode) node.parentNode.replaceChild(fragment, node);
    }

    line.appendChild(inner);
    heading.appendChild(line);
  }
}

function mv2SetupReveals() {
  const headings = document.querySelectorAll('[data-mv2-reveal="title"]');
  for (let i = 0; i < headings.length; i += 1) mv2SplitHeading(headings[i]);

  const targets = document.querySelectorAll('[data-mv2-reveal]:not([data-mv2-hero])');

  if (typeof IntersectionObserver === 'undefined') {
    for (let i = 0; i < targets.length; i += 1) targets[i].classList.add('is-visible');
  } else {
    const observer = new IntersectionObserver((entries) => {
      for (let i = 0; i < entries.length; i += 1) {
        const entry = entries[i];
        if (entry.isIntersecting) {
          entry.target.classList.add('is-visible');
          observer.unobserve(entry.target);
        }
      }
    }, { rootMargin: '0px 0px -10% 0px', threshold: 0.12 });

    for (let i = 0; i < targets.length; i += 1) observer.observe(targets[i]);
  }

  // Trigger hero entrance immediately without waiting for scroll
  window.requestAnimationFrame(() => {
    mv2Doc.classList.add('mv2-ready');
    const heroNodes = document.querySelectorAll('[data-mv2-hero]');
    for (let i = 0; i < heroNodes.length; i += 1) {
      heroNodes[i].classList.add('is-visible');
    }
  });
}

function mv2SetupMagnetic() {
  if (!mv2FinePointer) return;
  const items = document.querySelectorAll('[data-mv2-magnetic]');
  for (let i = 0; i < items.length; i += 1) {
    const item = items[i];
    let frame = 0;
    item.addEventListener('pointermove', (event) => {
      if (frame) return;
      frame = window.requestAnimationFrame(() => {
        frame = 0;
        const rect = item.getBoundingClientRect();
        const dx = (event.clientX - (rect.left + rect.width / 2)) / rect.width;
        const dy = (event.clientY - (rect.top + rect.height / 2)) / rect.height;
        item.style.setProperty('--mx', `${(dx * 7).toFixed(2)}px`);
        item.style.setProperty('--my', `${(dy * 5).toFixed(2)}px`);
      });
    });
    item.addEventListener('pointerleave', () => {
      item.style.setProperty('--mx', '0px');
      item.style.setProperty('--my', '0px');
    });
  }
}

function mv2SetupSpotlight() {
  if (!mv2FinePointer) return;
  let frame = 0;
  let pending = null;
  document.addEventListener('pointermove', (event) => {
    pending = event;
    if (frame) return;
    frame = window.requestAnimationFrame(() => {
      frame = 0;
      const target = pending.target;
      if (!(target instanceof Element)) return;
      const card = target.closest('.mv2-sector-card, .mv2-plan');
      if (!card) return;
      const rect = card.getBoundingClientRect();
      card.style.setProperty('--mx', `${(((pending.clientX - rect.left) / rect.width) * 100).toFixed(2)}%`);
      card.style.setProperty('--my', `${(((pending.clientY - rect.top) / rect.height) * 100).toFixed(2)}%`);
    });
  }, { passive: true });
}

function mv2SetupHeader() {
  const header = document.querySelector('.mv2-header');
  if (!header) return;
  let ticking = 0;
  const apply = () => {
    ticking = 0;
    mv2Doc.classList.toggle('mv2-scrolled', window.scrollY > 12);
  };
  window.addEventListener('scroll', () => {
    if (ticking) return;
    ticking = window.requestAnimationFrame(apply);
  }, { passive: true });
  apply();
}

function mv2SetupAuroraPause() {
  const hosts = document.querySelectorAll('[data-mv2-aurora-pause]');
  if (!hosts.length || typeof IntersectionObserver === 'undefined') return;
  const observer = new IntersectionObserver((entries) => {
    for (let i = 0; i < entries.length; i += 1) {
      const entry = entries[i];
      entry.target.dataset.inview = entry.isIntersecting ? 'true' : 'false';
    }
  }, { rootMargin: '200px 0px' });
  for (let i = 0; i < hosts.length; i += 1) observer.observe(hosts[i]);
}

function mv2Init() {
  try {
    mv2SetupHeader();
  } catch (error) {
    /* the sticky navbar keeps its resting state if the sentinel fails */
  }
  if (mv2Reduce) {
    mv2Doc.classList.add('mv2-ready');
    mv2SignalMotion();
    return;
  }
  mv2Doc.classList.add('mv2-motion');
  try {
    mv2SetupReveals();
  } catch (error) {
    mv2RevealAll();
  }
  try {
    mv2SetupAuroraPause();
  } catch (error) {
    /* the aurora keeps drifting if the viewport observer fails */
  }
  try {
    mv2SetupMagnetic();
    mv2SetupSpotlight();
  } catch (error) {
    /* microinteractions are optional enhancements */
  }
  mv2SignalMotion();
}

try {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mv2Init, { once: true });
  } else {
    mv2Init();
  }
} catch (error) {
  mv2RevealAll();
  mv2SignalMotion();
}

window.setTimeout(() => {
  if (!mv2MotionDispatched || !mv2Doc.classList.contains('mv2-ready')) {
    mv2RevealAll();
    mv2SignalMotion();
  }
}, 1000);
