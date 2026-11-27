const capSection = document.querySelector("[data-mv2-capabilities]");
const setupSection = document.querySelector("#mv2-setup");
const pricingCurve = document.querySelector("[data-mv2-pricing-curve]");
const header = document.querySelector(".mv2-page .mv2-header");

if (capSection instanceof HTMLElement) {
  // Il contenuto del capitolo dark è staticamente visibile dal primo pixel
  capSection.classList.add("mv2-capabilities--visible");
}

if ((capSection instanceof HTMLElement || setupSection instanceof HTMLElement) && header instanceof HTMLElement) {
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  if (!reducedMotion.matches) {
    let headerDark = null;

    const applyHeader = (isDark) => {
      if (!(header instanceof HTMLElement)) return;
      if (isDark === headerDark) return;
      headerDark = isDark;

      if (isDark) {
        header.style.setProperty("--mv2-header-bg", "rgba(7, 9, 10, 0.9)");
        header.style.setProperty("--mv2-header-border", "rgba(255, 255, 255, 0.1)");
        header.style.setProperty("--mv2-header-ink", "rgb(247, 248, 250)");
        header.style.setProperty("--mv2-header-muted", "rgb(214, 214, 214)");
        header.style.setProperty("--mv2-header-action-bg", "rgb(247, 248, 250)");
        header.style.setProperty("--mv2-header-action-ink", "rgb(7, 9, 10)");
        header.style.setProperty("--mv2-header-action-hover", "rgb(255, 255, 255)");
        header.style.setProperty("--mv2-header-shadow", "0 10px 30px rgba(0, 0, 0, 0.24)");
      } else {
        [
          "--mv2-header-bg",
          "--mv2-header-border",
          "--mv2-header-ink",
          "--mv2-header-muted",
          "--mv2-header-action-bg",
          "--mv2-header-action-ink",
          "--mv2-header-action-hover",
          "--mv2-header-shadow",
        ].forEach((property) => header.style.removeProperty(property));
      }
    };

    const update = () => {
      const headerHeight = header instanceof HTMLElement ? header.offsetHeight : 72;

      let darkStart = Infinity;
      let darkEnd = -Infinity;

      if (capSection instanceof HTMLElement) {
        const rect = capSection.getBoundingClientRect();
        darkStart = Math.min(darkStart, rect.top);
        darkEnd = Math.max(darkEnd, rect.bottom);
      }

      if (setupSection instanceof HTMLElement) {
        const rect = setupSection.getBoundingClientRect();
        darkStart = Math.min(darkStart, rect.top);
        darkEnd = Math.max(darkEnd, rect.bottom);
      }

      if (pricingCurve instanceof HTMLElement) {
        const rect = pricingCurve.getBoundingClientRect();
        // The curve transitions from dark to light around its midpoint
        darkEnd = Math.max(darkEnd, rect.top + rect.height * 0.5);
      }

      // L'header passa al tema dark quando la zona dark si trova sotto di esso
      const inDarkSection = darkStart <= headerHeight && darkEnd >= headerHeight * 0.5;
      applyHeader(inDarkSection);
    };

    window.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update, { passive: true });
    update();

    reducedMotion.addEventListener("change", (e) => {
      if (e.matches) {
        window.removeEventListener("scroll", update);
        window.removeEventListener("resize", update);
        applyHeader(false);
      } else {
        window.addEventListener("scroll", update, { passive: true });
        window.addEventListener("resize", update, { passive: true });
        update();
      }
    });
  }
}
