/* Melpis Landing Page — Lemni Architecture Controller (Vanilla JS, Zero Dependencies) */
(function () {
    'use strict';

    // Segnala JS attivo: il CSS nasconde .reveal/.stagger-* solo sotto html.js,
    // così il contenuto resta visibile se app.js non si carica o JS è disabilitato.
    document.documentElement.classList.add('js');

    // Preview only: three approved visual treatments share the same content and CTA.
    var heroPreview = new URLSearchParams(window.location.search).get('hero');
    if (['halo', 'horizon', 'contour'].includes(heroPreview)) {
        document.getElementById('hero')?.setAttribute('data-hero-variant', heroPreview);
    }

    var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    /* ---------- Unified Navbar Controller (Sticky, Dropdown & Mobile Accordion) ---------- */
    var header = document.getElementById('mainHeader');
    var dropdown = document.getElementById('navDropdownSettori');
    var trigger = document.getElementById('settoriDropdownTrigger');
    var hamburger = document.getElementById('navHamburger');
    var overlay = document.getElementById('mobileOverlay');
    var accordionBtn = document.getElementById('mobileSettoriBtn');
    var accordionWrap = document.getElementById('mobileSettoriAccordion');
    var langTrigger = document.getElementById('langDropdownTrigger');
    var langMenu = document.getElementById('langDropdownMenu');

    /* 1. Persistent Sticky Scroll Blur & Padding */
    if (header) {
        var lightSections = document.querySelectorAll('[data-navbar-theme="light"]');
        var updateScrollState = function () {
            var scrollY = window.pageYOffset || document.documentElement.scrollTop || 0;
            var isCurrentlyScrolled = header.classList.contains('is-scrolled');
            var shouldBeScrolled = isCurrentlyScrolled ? scrollY > 10 : scrollY > 20;
            header.classList.toggle('is-scrolled', shouldBeScrolled);

            if (lightSections.length > 0) {
                var headerRect = header.getBoundingClientRect();
                var headerMidY = headerRect.top + (headerRect.height / 2);
                var isOverLight = false;

                if (document.elementsFromPoint) {
                    var els = document.elementsFromPoint(window.innerWidth / 2, headerMidY);
                    var topEl = null;
                    for (var e = 0; e < els.length; e++) {
                        var candidate = els[e];
                        if (candidate !== header && !header.contains(candidate) && !candidate.classList.contains('mobile-overlay')) {
                            topEl = candidate;
                            break;
                        }
                    }
                    if (topEl) {
                        isOverLight = !!topEl.closest('[data-navbar-theme="light"]');
                    }
                } else {
                    for (var i = 0; i < lightSections.length; i++) {
                        var secRect = lightSections[i].getBoundingClientRect();
                        if (secRect.top <= headerMidY && secRect.bottom >= headerMidY) {
                            isOverLight = true;
                            break;
                        }
                    }
                }
                header.classList.toggle('nav-theme-light', isOverLight);
            }
        };
        window.addEventListener('scroll', updateScrollState, { passive: true });
        window.addEventListener('resize', updateScrollState, { passive: true });
        updateScrollState();
    }

    /* 2. Desktop Dropdown Accessibility & Click Fallback */
    if (dropdown && trigger) {
        var setDropdown = function (open) {
            dropdown.classList.toggle('is-open', open);
            trigger.setAttribute('aria-expanded', String(open));
        };

        trigger.addEventListener('click', function (e) {
            e.preventDefault();
            e.stopPropagation();
            setDropdown(!dropdown.classList.contains('is-open'));
        });

        document.addEventListener('click', function (e) {
            if (!dropdown.contains(e.target)) {
                setDropdown(false);
            }
        });

        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && dropdown.classList.contains('is-open')) {
                setDropdown(false);
                trigger.focus();
            }
        });

        dropdown.addEventListener('focusout', function () {
            setTimeout(function () {
                if (!dropdown.contains(document.activeElement)) {
                    setDropdown(false);
                }
            }, 10);
        });
    }
 
    /* 2b. Language Dropdown Controller */
    if (langTrigger && langMenu && !langTrigger.dataset.langInit) {
        langTrigger.dataset.langInit = 'true';
        var setLangDropdown = function (open) {
            langMenu.hidden = !open;
            langMenu.classList.toggle('is-open', open);
            langTrigger.setAttribute('aria-expanded', String(open));
        };

        langTrigger.addEventListener('click', function (e) {
            e.preventDefault();
            e.stopPropagation();
            var isOpen = langTrigger.getAttribute('aria-expanded') === 'true';
            setLangDropdown(!isOpen);
        });

        document.addEventListener('click', function (e) {
            if (!langMenu.contains(e.target) && !langTrigger.contains(e.target)) {
                setLangDropdown(false);
            }
        });

        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && langTrigger.getAttribute('aria-expanded') === 'true') {
                setLangDropdown(false);
                langTrigger.focus();
            }
        });
    }

    /* 3. Mobile Navigation Overlay */
    function setNav(open) {
        if (!overlay || !hamburger) return;
        overlay.classList.toggle('open', open);
        overlay.setAttribute('aria-hidden', String(!open));
        hamburger.setAttribute('aria-expanded', String(open));
        document.body.style.overflow = open ? 'hidden' : '';
    }
    function closeNav() { setNav(false); }

    if (hamburger && overlay) {
        hamburger.addEventListener('click', function (e) {
            e.preventDefault();
            setNav(!overlay.classList.contains('open'));
        });
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') closeNav();
        });
        overlay.querySelectorAll('.mobile-link:not(.mobile-accordion-btn), .mobile-sublink, .btn-pill-cta, .btn-cta-primary').forEach(function (el) {
            el.addEventListener('click', closeNav);
        });
    }

    /* 4. Mobile Accordion for Settori */
    if (accordionBtn && accordionWrap) {
        accordionBtn.addEventListener('click', function (e) {
            e.preventDefault();
            e.stopPropagation();
            var isExpanded = accordionWrap.classList.toggle('is-expanded');
            accordionBtn.setAttribute('aria-expanded', String(isExpanded));
        });
    }

    /* ---------- Continuous Assembling & Dissolving Real Melpis Dashboard Loop ---------- */
    (function initDashboardAssemblyLoop() {
        var win = document.getElementById('heroDashWindow');
        var viewport = document.getElementById('heroDashViewport');
        var sidebar = document.getElementById('dashPieceSidebar');
        var topbar = document.getElementById('dashPieceTopbar');
        var stat1 = document.getElementById('dashStat1');
        var stat2 = document.getElementById('dashStat2');
        var stat3 = document.getElementById('dashStat3');
        var panels = document.getElementById('dashPiecePanels');
        var statVal1 = document.getElementById('statVal1');
        var statVal2 = document.getElementById('statVal2');
        var statVal3 = document.getElementById('statVal3');

        if (!win) return;

        function setFloatState(active) {
            if (viewport) viewport.classList.toggle('is-floating', active);
            win.classList.toggle('is-assembled', active);
            if (stat1) stat1.classList.toggle('float-a', active);
            if (stat2) stat2.classList.toggle('float-b', active);
            if (stat3) stat3.classList.toggle('float-c', active);
        }

        if (reduceMotion) {
            win.classList.remove('is-dissolving');
            [sidebar, topbar, stat1, stat2, stat3, panels].forEach(function (el) {
                if (el) el.classList.add('is-mounted');
            });
            if (statVal1) statVal1.textContent = '12';
            if (statVal2) statVal2.textContent = '9';
            if (statVal3) statVal3.textContent = '5';
            return;
        }

        var timelineTimeouts = [];

        function clearTimeline() {
            timelineTimeouts.forEach(function (t) { clearTimeout(t); });
            timelineTimeouts = [];
        }

        function countUp(el, target, duration) {
            if (!el) return;
            var startTime = null;
            function step(timestamp) {
                if (!startTime) startTime = timestamp;
                var progress = Math.min((timestamp - startTime) / duration, 1);
                var easeProgress = 1 - Math.pow(1 - progress, 3);
                el.textContent = Math.floor(easeProgress * target);
                if (progress < 1) {
                    window.requestAnimationFrame(step);
                } else {
                    el.textContent = target;
                }
            }
            window.requestAnimationFrame(step);
        }

        function runCycle() {
            clearTimeline();
            setFloatState(false);

            // 1. Reset all pieces instantly
            win.classList.remove('is-dissolving');
            [sidebar, topbar, stat1, stat2, stat3, panels].forEach(function (el) {
                if (el) el.classList.remove('is-mounted');
            });
            if (statVal1) statVal1.textContent = '0';
            if (statVal2) statVal2.textContent = '0';
            if (statVal3) statVal3.textContent = '0';

            // Step 1: Sidebar at 120ms
            timelineTimeouts.push(setTimeout(function () {
                if (sidebar) sidebar.classList.add('is-mounted');
            }, 120));

            // Step 2: Topbar at 420ms
            timelineTimeouts.push(setTimeout(function () {
                if (topbar) topbar.classList.add('is-mounted');
            }, 420));

            // Step 3: Stat Card 1 at 720ms
            timelineTimeouts.push(setTimeout(function () {
                if (stat1) stat1.classList.add('is-mounted');
                countUp(statVal1, 12, 900);
            }, 720));

            // Step 4: Stat Card 2 at 960ms
            timelineTimeouts.push(setTimeout(function () {
                if (stat2) stat2.classList.add('is-mounted');
                countUp(statVal2, 9, 900);
            }, 960));

            // Step 5: Stat Card 3 at 1200ms
            timelineTimeouts.push(setTimeout(function () {
                if (stat3) stat3.classList.add('is-mounted');
                countUp(statVal3, 5, 900);
            }, 1200));

            // Step 6: Bottom Panels at 1500ms + start float loop
            timelineTimeouts.push(setTimeout(function () {
                if (panels) panels.classList.add('is-mounted');
                setFloatState(true);
            }, 1500));

            // Step 7: Live micro-event at 3600ms (Stat 1 increments to 13 + subtle bump)
            timelineTimeouts.push(setTimeout(function () {
                if (statVal1 && stat1 && stat1.classList.contains('is-mounted')) {
                    statVal1.textContent = '13';
                    stat1.style.transform = 'translateY(-3px) scale(1.02)';
                    setTimeout(function () {
                        if (stat1) stat1.style.transform = '';
                    }, 350);
                }
            }, 3600));

            // Step 8: Dissolve out whole dashboard at 5800ms
            timelineTimeouts.push(setTimeout(function () {
                setFloatState(false);
                if (win) win.classList.add('is-dissolving');
            }, 5800));

            // Step 9: Loop back at 6700ms (solo se ancora visibile)
            timelineTimeouts.push(setTimeout(function () {
                if (shouldAnimate()) runCycle();
            }, 6700));
        }

        var isHeroVisible = true;
        var isDocVisible = !document.hidden;

        function shouldAnimate() {
            return isHeroVisible && isDocVisible && !reduceMotion;
        }

        function safeRunCycle() {
            if (!shouldAnimate()) {
                clearTimeline();
                return;
            }
            runCycle();
        }

        var heroStage = viewport || win;
        if ('IntersectionObserver' in window && heroStage) {
            var heroObs = new IntersectionObserver(function (entries) {
                entries.forEach(function (entry) {
                    var prev = isHeroVisible;
                    isHeroVisible = entry.isIntersecting;
                    if (isHeroVisible && !prev && isDocVisible) {
                        safeRunCycle();
                    } else if (!isHeroVisible) {
                        clearTimeline();
                        setFloatState(false);
                    }
                });
            }, { threshold: 0.1 });
            heroObs.observe(heroStage);
        }

        document.addEventListener('visibilitychange', function () {
            isDocVisible = !document.hidden;
            if (isDocVisible && isHeroVisible) {
                safeRunCycle();
            } else if (!isDocVisible) {
                clearTimeline();
                setFloatState(false);
            }
        });

        if (document.readyState === 'complete' || document.readyState === 'interactive') {
            setTimeout(safeRunCycle, 150);
        } else {
            window.addEventListener('DOMContentLoaded', function () {
                setTimeout(safeRunCycle, 150);
            });
        }
    })();

    /* ---------- Human-in-the-Loop Takeover Interactive Animation Demo ---------- */
    (function initHitlTakeoverDemo() {
        var windowEl = document.getElementById('hitlMockupWindow');
        var statusPill = document.getElementById('hitlStatusPill');
        var takeoverRibbon = document.getElementById('hitlTakeoverRibbon');
        var takeoverBtn = document.getElementById('hitlTakeoverBtn');
        var btnTxt = document.getElementById('hitlBtnTxt');
        var ribbonLabel = document.getElementById('hitlRibbonLabel');
        var threadTag = document.getElementById('hitlThreadTag');
        var virtualCursor = document.getElementById('hitlVirtualCursor');
        var cursorRipple = document.getElementById('hitlCursorRipple');
        var inputPlaceholder = document.getElementById('hitlInputPlaceholder');

        if (!windowEl || !takeoverBtn) return;

        if (reduceMotion) {
            windowEl.classList.add('is-takeover-active');
            if (statusPill) {
                statusPill.className = 'hitl-status-pill pill-human';
                statusPill.innerHTML = '<span class="pill-dot"></span><span class="pill-text">Ripreso da te · Staff in linea</span>';
            }
            if (takeoverRibbon) takeoverRibbon.classList.add('is-taken');
            if (takeoverBtn) {
                takeoverBtn.classList.add('is-active');
                if (btnTxt) btnTxt.textContent = 'Controllo attivo';
            }
            if (ribbonLabel) ribbonLabel.textContent = 'Controllo Umano attivo';
            if (threadTag) threadTag.textContent = 'Preso in carico';
            if (inputPlaceholder) inputPlaceholder.textContent = 'Rispondi come Melpis Barbershop...';
            if (virtualCursor) virtualCursor.style.display = 'none';
            return;
        }

        var hitlTimeouts = [];

        function clearHitlTimeline() {
            hitlTimeouts.forEach(function (t) { clearTimeout(t); });
            hitlTimeouts = [];
        }

        function runHitlLoop() {
            clearHitlTimeline();

            // State 1: Reset to AI Mode
            windowEl.classList.remove('is-takeover-active');
            if (statusPill) {
                statusPill.className = 'hitl-status-pill pill-ai';
                statusPill.innerHTML = '<span class="pill-dot"></span><span class="pill-text">Gestito dall\'AI</span>';
            }
            if (takeoverRibbon) takeoverRibbon.classList.remove('is-taken');
            if (takeoverBtn) {
                takeoverBtn.classList.remove('is-active');
                if (btnTxt) btnTxt.textContent = 'Prendi controllo';
            }
            if (ribbonLabel) ribbonLabel.textContent = 'Controllo AI attivo';
            if (threadTag) threadTag.textContent = '— in corso';
            if (inputPlaceholder) inputPlaceholder.textContent = 'AI in ascolto attivo...';
            
            if (virtualCursor) {
                virtualCursor.style.opacity = '0';
                virtualCursor.style.transform = 'translate(180px, 200px)';
            }
            if (cursorRipple) cursorRipple.classList.remove('pulse');

            // State 2: Cursor appears (at 1.2s)
            hitlTimeouts.push(setTimeout(function () {
                if (virtualCursor) {
                    virtualCursor.style.opacity = '0.95';
                }
            }, 1200));

            // State 3: Cursor moves to Takeover Button (at 1.7s)
            hitlTimeouts.push(setTimeout(function () {
                if (virtualCursor && takeoverBtn) {
                    var btnRect = takeoverBtn.getBoundingClientRect();
                    var winRect = windowEl.getBoundingClientRect();
                    var relX = btnRect.left - winRect.left + btnRect.width / 2;
                    var relY = btnRect.top - winRect.top + btnRect.height / 2;
                    virtualCursor.style.transform = 'translate(' + relX + 'px, ' + relY + 'px)';
                }
            }, 1700));

            // State 4: Simulated Click (at 2.8s)
            hitlTimeouts.push(setTimeout(function () {
                if (cursorRipple) {
                    cursorRipple.classList.remove('pulse');
                    void cursorRipple.offsetWidth; // trigger reflow
                    cursorRipple.classList.add('pulse');
                }
                if (takeoverBtn) {
                    takeoverBtn.style.transform = 'scale(0.92)';
                    setTimeout(function () {
                        if (takeoverBtn) takeoverBtn.style.transform = '';
                    }, 200);
                }
            }, 2800));

            // State 5: Takeover Active Transition (at 3.1s)
            hitlTimeouts.push(setTimeout(function () {
                windowEl.classList.add('is-takeover-active');
                if (statusPill) {
                    statusPill.className = 'hitl-status-pill pill-human';
                    statusPill.innerHTML = '<span class="pill-dot"></span><span class="pill-text">Ripreso da te · Staff in linea</span>';
                }
                if (takeoverRibbon) takeoverRibbon.classList.add('is-taken');
                if (takeoverBtn) {
                    takeoverBtn.classList.add('is-active');
                    if (btnTxt) btnTxt.textContent = 'Controllo attivo';
                }
                if (ribbonLabel) ribbonLabel.textContent = 'Controllo Umano attivo';
                if (threadTag) threadTag.textContent = '— preso in carico';
                if (inputPlaceholder) inputPlaceholder.textContent = 'Rispondi come Melpis Barbershop...';
                if (virtualCursor) virtualCursor.style.opacity = '0';
            }, 3100));

            // State 6: Loop back after hold (at 7.8s)
            hitlTimeouts.push(setTimeout(runHitlLoop, 7800));
        }

        var isHitlVisible = false;

        function safeRunHitlLoop() {
            if (!isHitlVisible || document.hidden || reduceMotion) {
                clearHitlTimeline();
                return;
            }
            runHitlLoop();
        }

        // Trigger when section comes into view (IntersectionObserver)
        if ('IntersectionObserver' in window) {
            var hitlObserver = new IntersectionObserver(function (entries) {
                entries.forEach(function (entry) {
                    var wasVisible = isHitlVisible;
                    isHitlVisible = entry.isIntersecting;
                    if (isHitlVisible && !wasVisible && !document.hidden) {
                        safeRunHitlLoop();
                    } else if (!isHitlVisible) {
                        clearHitlTimeline();
                    }
                });
            }, { threshold: 0.15 });
            hitlObserver.observe(windowEl);
        } else {
            isHitlVisible = true;
            setTimeout(safeRunHitlLoop, 1000);
        }

        document.addEventListener('visibilitychange', function () {
            if (!document.hidden && isHitlVisible) {
                safeRunHitlLoop();
            } else if (document.hidden) {
                clearHitlTimeline();
            }
        });
    })();

    /* ---------- Scroll Reveal (IntersectionObserver) ---------- */
    if (reduceMotion) {
        document.querySelectorAll('.reveal').forEach(function (el) {
            el.classList.add('visible');
        });
    } else if ('IntersectionObserver' in window) {
        var revObs = new IntersectionObserver(function (entries) {
            entries.forEach(function (entry) {
                if (entry.isIntersecting) {
                    entry.target.classList.add('visible');
                    revObs.unobserve(entry.target);
                }
            });
        }, { threshold: 0.08, rootMargin: '0px 0px -40px 0px' });

        document.querySelectorAll('.reveal:not(.visible)').forEach(function (el) {
            revObs.observe(el);
        });
    }

    /* ---------- Sticky Slide-Over Sync (pin offsets & hook heights) ---------- */
    /* Il vincolo sticky è il content box del genitore: lo spazio di aggancio è
       uno spacer ::after dentro .pin-wrap (vedi style.css), non un padding.
       Qui si misurano le altezze reali per (a) far pinnare le sezioni solo quando
       il loro bordo inferiore raggiunge il fondo della viewport — così nessun
       contenuto viene coperto prima di essere letto — e (b) limitare l'hook di
       pin-wrap-2 all'altezza di pricing, così nessuna banda resta scoperta.
       Nessuno scroll listener, solo resize/ResizeObserver. */
    (function initSlideOverSync() {
        var features = document.querySelector('.features-split-section');
        var faq = document.querySelector('.faq-section');
        var finalCta = document.querySelector('.final-cta-section');
        if (!features || !faq || !finalCta || !('ResizeObserver' in window)) return;

        var desktop = window.matchMedia('(min-width: 961px)');

        function sync() {
            if (!desktop.matches) return;
            var vh = window.innerHeight;
            document.documentElement.style.setProperty('--slide-pin-1', (vh - features.offsetHeight) + 'px');
            document.documentElement.style.setProperty('--slide-pin-3', (vh - faq.offsetHeight) + 'px');
            // hook-3 non deve superare l'altezza della CTA: lo spacer resta tutto
            // coperto e il footer non viene mai sovrapposto
            document.documentElement.style.setProperty('--slide-hook-3', Math.min(vh + 120, finalCta.offsetHeight) + 'px');
        }

        var ro = new ResizeObserver(function () { sync(); });
        ro.observe(features);
        ro.observe(faq);
        ro.observe(finalCta);
        window.addEventListener('resize', sync, { passive: true });
        sync();
    })();

    /* ---------- High-Performance Progressive Narrative Spine Drawing ---------- */
    (function initNarrativeSpine() {
        var section = document.querySelector('.narrative-journey-section');
        var container = document.querySelector('.narrative-container');
        var activePath = document.querySelector('.narrative-path-active');
        var glowPath = document.querySelector('.narrative-path-glow');
        var terminalDot = document.querySelector('.narrative-terminal-dot');
        var nodes = document.querySelectorAll('.narrative-node');
        if (!section || !container || !activePath) return;

        // 1. Calculate path length dynamically
        var pathLength = 0;
        try {
            pathLength = activePath.getTotalLength();
        } catch (e) {
            pathLength = 1435;
        }
        if (!pathLength || isNaN(pathLength) || pathLength <= 0) {
            pathLength = 1435;
        }

        // Set initial stroke-dasharray and stroke-dashoffset (unvisited / invisible)
        activePath.style.strokeDasharray = pathLength;
        activePath.style.strokeDashoffset = pathLength;
        if (glowPath) {
            glowPath.style.strokeDasharray = pathLength;
            glowPath.style.strokeDashoffset = pathLength;
        }
        if (terminalDot) {
            terminalDot.style.opacity = '0';
            terminalDot.style.transition = 'opacity 0.4s ease';
        }

        if (reduceMotion) {
            activePath.style.strokeDashoffset = '0';
            if (glowPath) glowPath.style.strokeDashoffset = '0';
            if (terminalDot) terminalDot.style.opacity = '1';
            nodes.forEach(function (n) { n.classList.add('node-active'); });
            return;
        }

        var currentProgress = 0;
        var targetProgress = 0;
        var rafId = null;

        function tick() {
            var diff = targetProgress - currentProgress;
            if (Math.abs(diff) < 0.001) {
                currentProgress = targetProgress;
                rafId = null;
            } else {
                currentProgress += diff * 0.18;
                rafId = requestAnimationFrame(tick);
            }
            var offset = pathLength * (1 - currentProgress);
            activePath.style.strokeDashoffset = offset;
            if (glowPath) glowPath.style.strokeDashoffset = offset;

            if (terminalDot) {
                terminalDot.style.opacity = currentProgress >= 0.96 ? '1' : '0';
            }

            nodes.forEach(function (n, idx) {
                var thresh = [0.06, 0.28, 0.52, 0.75, 0.94][idx] || 0.5;
                if (currentProgress >= thresh) {
                    n.classList.add('node-active');
                }
            });
        }

        function onScroll() {
            var cRect = container.getBoundingClientRect();
            var vhMid = window.innerHeight * 0.5;
            var start = vhMid;
            var end = vhMid - 1435;
            var p = (start - cRect.top) / (start - end);
            if (p < 0) p = 0;
            if (p > 1) p = 1;
            targetProgress = p;

            if (!rafId) {
                rafId = requestAnimationFrame(tick);
            }
        }

        // Keep IntersectionObserver for reliable fallback activation of nodes
        if ('IntersectionObserver' in window) {
            var nodeObs = new IntersectionObserver(function (entries) {
                entries.forEach(function (entry) {
                    if (entry.isIntersecting) {
                        entry.target.classList.add('node-active');
                    }
                });
            }, { threshold: 0.15, rootMargin: '0px 0px -20px 0px' });

            nodes.forEach(function (node) {
                nodeObs.observe(node);
            });
        }

        window.addEventListener('scroll', onScroll, { passive: true });
        onScroll();

        window.addEventListener('resize', function () {
            try {
                var len = activePath.getTotalLength();
                if (len && !isNaN(len) && len > 0) {
                    pathLength = len;
                    activePath.style.strokeDasharray = pathLength;
                    if (glowPath) glowPath.style.strokeDasharray = pathLength;
                    onScroll();
                }
            } catch (e) {}
        }, { passive: true });
    })();

    /* ---------- Interactive Sector Rules Switcher (Satellite 3) ---------- */
    (function initSectorRulesSwitcher() {
        var tabs = document.querySelectorAll('.r-tab[data-sector]');
        var container = document.getElementById('rulesMiniItems');
        if (!tabs.length || !container) return;

        var sectorData = {
            ristoranti: [
                { label: 'Gestione coperti e intolleranze', on: true },
                { label: 'Anticipo prenotazione e slot', on: true },
                { label: 'Promemoria automatico no-show', on: true }
            ],
            saloni: [
                { label: 'Durata trattamenti e pieghe', on: true },
                { label: 'Preferenza operatore/stilista', on: true },
                { label: 'Richiesta acconto per no-show', on: true }
            ],
            medici: [
                { label: 'Passaggio operatore per urgenze', on: true },
                { label: 'Triage prima visita / controllo', on: true },
                { label: 'Orari reperibilità segreteria', on: true }
            ],
            hotel: [
                { label: 'Check-in/out e orari colazione', on: true },
                { label: 'Politica cancellazione diretta', on: true },
                { label: 'Richieste extra (culla, animali)', on: true }
            ]
        };

        tabs.forEach(function (tab) {
            tab.addEventListener('click', function () {
                var sector = tab.getAttribute('data-sector');
                if (!sector || !sectorData[sector]) return;

                tabs.forEach(function (t) { t.classList.remove('active'); });
                tab.classList.add('active');

                // Smooth micro cross-fade
                container.style.opacity = '0';
                setTimeout(function () {
                    var items = sectorData[sector];
                    var html = '';
                    items.forEach(function (item) {
                        html += '<div class="r-item"><span>' + item.label + '</span> <span class="r-toggle ' + (item.on ? 'on' : '') + '"></span></div>';
                    });
                    container.innerHTML = html;
                    container.style.opacity = '1';
                }, 150);
            });
        });
    })();

    /* ---------- Signup Modal / Quick Start ---------- */
    var modal = document.getElementById('signupModal');
    var modalClose = document.getElementById('modalClose');

    function openModal() {
        if (!modal) return;
        modal.classList.add('open');
        modal.setAttribute('aria-hidden', 'false');
        document.body.style.overflow = 'hidden';
        var firstInput = document.getElementById('regEmail');
        if (firstInput) setTimeout(function () { firstInput.focus(); }, 100);
    }

    function closeModal() {
        if (!modal) return;
        modal.classList.remove('open');
        modal.setAttribute('aria-hidden', 'true');
        document.body.style.overflow = '';
    }

    if (modalClose) modalClose.addEventListener('click', closeModal);
    if (modal) {
        modal.addEventListener('click', function (e) {
            if (e.target === modal) closeModal();
        });
    }

    document.querySelectorAll('[data-signup]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var location = btn.getAttribute('data-track-location') || 'general';
            var plan = btn.getAttribute('data-plan') || '';
            openModal();
        });
    });

    function handleSignupSubmit(e) {
        e.preventDefault();
        var form = document.getElementById('signupForm');
        if (!form) return;
        var email = form.email.value.trim();
        var vertical = form.vertical.value;

        // Redirect to onboarding with parameters
        window.location.href = '/registrati/?email=' + encodeURIComponent(email) + '&settore=' + encodeURIComponent(vertical);
    }

    var signupForm = document.getElementById('signupForm');
    if (signupForm) signupForm.addEventListener('submit', handleSignupSubmit);

    /* ---------- Lemni Quality Split Blocks: Staggered Entrance & Living Motion Loops ---------- */
    (function initLemniSplitBlocks() {
        var splitBlocks = document.querySelectorAll('.split-block-wrap, .integrations-micro-cta');
        if (!splitBlocks.length) return;

        // 1. Staggered Scroll Entrance Observer
        if ('IntersectionObserver' in window) {
            var splitObs = new IntersectionObserver(function (entries) {
                entries.forEach(function (entry) {
                    if (entry.isIntersecting) {
                        entry.target.classList.add('is-visible');
                        splitObs.unobserve(entry.target);
                    }
                });
            }, { threshold: 0.10, rootMargin: '0px 0px -30px 0px' });

            splitBlocks.forEach(function (block) {
                splitObs.observe(block);
            });
        } else {
            splitBlocks.forEach(function (b) { b.classList.add('is-visible'); });
        }

        // 2. Knowledge Base Upload Loop
        var kbBlock = document.querySelector('.kb-block-wrap');
        var kbProgressBar = document.getElementById('kbProgressBar');
        var kbFileName = document.getElementById('kbFileName');
        var kbFileBadge = document.getElementById('kbFileBadge');
        var kbProgressStatus = document.getElementById('kbProgressStatus');
        var kbProgressPercent = document.getElementById('kbProgressPercent');

        var kbFiles = [
            'Menu_e_Allergeni_2026.pdf',
            'Listino_Trattamenti_e_Prezzi.pdf',
            'Regolamento_Prenotazioni_e_Orari.pdf'
        ];
        var kbFileIndex = 0;
        var kbTimeouts = [];
        var isKbVisible = false;

        function clearKbTimeouts() {
            while (kbTimeouts.length > 0) {
                clearTimeout(kbTimeouts.pop());
            }
        }

        function runKbUploadCycle() {
            if (!kbProgressBar || reduceMotion || !isKbVisible || document.hidden) return;
            clearKbTimeouts();
            var currentFile = kbFiles[kbFileIndex % kbFiles.length];
            kbFileIndex++;

            if (kbFileName) kbFileName.textContent = currentFile;
            if (kbFileBadge) {
                kbFileBadge.textContent = 'in caricamento';
                kbFileBadge.style.background = 'transparent';
                kbFileBadge.style.color = '#64748B';
            }
            if (kbProgressStatus) kbProgressStatus.textContent = 'Caricamento in corso...';
            if (kbProgressPercent) kbProgressPercent.textContent = '15%';
            kbProgressBar.style.width = '15%';

            // Step 1: Upload progress to 55%
            kbTimeouts.push(setTimeout(function () {
                if (!kbProgressBar || !isKbVisible || document.hidden) return;
                kbProgressBar.style.width = '55%';
                if (kbProgressPercent) kbProgressPercent.textContent = '55%';
                if (kbProgressStatus) kbProgressStatus.textContent = 'Lettura orari, servizi e listino...';
                if (kbFileBadge) kbFileBadge.textContent = 'elaborazione';
            }, 1200));

            // Step 2: Progress to 88%
            kbTimeouts.push(setTimeout(function () {
                if (!kbProgressBar || !isKbVisible || document.hidden) return;
                kbProgressBar.style.width = '88%';
                if (kbProgressPercent) kbProgressPercent.textContent = '88%';
                if (kbProgressStatus) kbProgressStatus.textContent = 'Applicazione regole dell\'attività...';
                if (kbFileBadge) kbFileBadge.textContent = 'regole';
            }, 2400));

            // Step 3: Complete 100%
            kbTimeouts.push(setTimeout(function () {
                if (!kbProgressBar || !isKbVisible || document.hidden) return;
                kbProgressBar.style.width = '100%';
                if (kbProgressPercent) kbProgressPercent.textContent = '100%';
                if (kbProgressStatus) kbProgressStatus.textContent = 'Pronto per rispondere ai clienti';
                if (kbFileBadge) {
                    kbFileBadge.textContent = 'attivo';
                    kbFileBadge.style.background = 'transparent';
                    kbFileBadge.style.color = '#15803D';
                }
            }, 3600));

            // Reset and loop next file
            kbTimeouts.push(setTimeout(function () {
                if (isKbVisible && !document.hidden) runKbUploadCycle();
            }, 7500));
        }

        if ('IntersectionObserver' in window && kbBlock && !reduceMotion) {
            var kbObs = new IntersectionObserver(function (entries) {
                entries.forEach(function (entry) {
                    var wasVisible = isKbVisible;
                    isKbVisible = entry.isIntersecting;
                    if (isKbVisible && !wasVisible && !document.hidden) {
                        runKbUploadCycle();
                    } else if (!isKbVisible) {
                        clearKbTimeouts();
                    }
                });
            }, { threshold: 0.15 });
            kbObs.observe(kbBlock);
        } else if (!reduceMotion) {
            isKbVisible = true;
            setTimeout(runKbUploadCycle, 800);
        }

        // 3. Automazioni: Live Calendar Insertion & Review Typing Loop
        var syncBlock = document.querySelector('.sync-block-wrap');
        var toastBanner = document.getElementById('syncToastBanner');
        var incomingSlot = document.getElementById('incomingSlotCard');
        var typingParagraph = document.getElementById('aiTypingParagraph');
        var typingCursor = document.getElementById('typingCursor');

        var reviewReplyText = "Grazie Laura! Siamo felicissimi che abbiate trascorso una bellissima serata. Vi aspettiamo presto!";
        var typingTimer = null;
        var syncTimeouts = [];
        var isSyncVisible = false;

        function clearSyncTimeouts() {
            while (syncTimeouts.length > 0) {
                clearTimeout(syncTimeouts.pop());
            }
            if (typingTimer) {
                clearTimeout(typingTimer);
                typingTimer = null;
            }
        }

        function typeWriter(text, i, callback) {
            if (!typingParagraph || !isSyncVisible || document.hidden) return;
            if (i < text.length) {
                typingParagraph.textContent += text.charAt(i);
                typingTimer = setTimeout(function () {
                    typeWriter(text, i + 1, callback);
                }, 30 + Math.random() * 20);
            } else if (callback) {
                syncTimeouts.push(setTimeout(callback, 3500));
            }
        }

        function runSyncSequence() {
            if (!syncBlock || reduceMotion || !isSyncVisible || document.hidden) return;
            clearSyncTimeouts();

            // Step 1: Toast & Slot Animation
            if (toastBanner) {
                toastBanner.style.opacity = '0';
                toastBanner.style.transform = 'translateY(-6px)';
                syncTimeouts.push(setTimeout(function () {
                    if (!isSyncVisible || document.hidden) return;
                    toastBanner.style.opacity = '1';
                    toastBanner.style.transform = 'translateY(0)';
                }, 400));
            }

            if (incomingSlot) {
                incomingSlot.style.opacity = '0';
                incomingSlot.style.transform = 'translateY(-8px)';
                syncTimeouts.push(setTimeout(function () {
                    if (!isSyncVisible || document.hidden) return;
                    incomingSlot.style.opacity = '1';
                    incomingSlot.style.transform = 'translateY(0)';
                }, 900));
            }

            // Step 2: Review typing stream
            if (typingParagraph) {
                typingParagraph.textContent = '';
                if (typingCursor) typingCursor.style.display = 'inline-block';
                syncTimeouts.push(setTimeout(function () {
                    if (!isSyncVisible || document.hidden) return;
                    typeWriter(reviewReplyText, 0, function () {
                        syncTimeouts.push(setTimeout(function () {
                            if (isSyncVisible && !document.hidden) runSyncSequence();
                        }, 4000));
                    });
                }, 1400));
            }
        }

        // Trigger sync sequence when block enters viewport
        if ('IntersectionObserver' in window && syncBlock && !reduceMotion) {
            var syncObs = new IntersectionObserver(function (entries) {
                entries.forEach(function (entry) {
                    var wasVisible = isSyncVisible;
                    isSyncVisible = entry.isIntersecting;
                    if (isSyncVisible && !wasVisible && !document.hidden) {
                        runSyncSequence();
                    } else if (!isSyncVisible) {
                        clearSyncTimeouts();
                    }
                });
            }, { threshold: 0.15 });
            syncObs.observe(syncBlock);
        } else if (!reduceMotion) {
            isSyncVisible = true;
            runSyncSequence();
        }

        document.addEventListener('visibilitychange', function () {
            if (document.hidden) {
                clearKbTimeouts();
                clearSyncTimeouts();
            } else {
                if (isKbVisible) runKbUploadCycle();
                if (isSyncVisible) runSyncSequence();
            }
        });
    })();

})();
