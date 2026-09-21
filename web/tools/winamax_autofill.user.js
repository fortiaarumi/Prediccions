// ==UserScript==
// @name         Prediccions Lliga - Winamax Auto Betslip (1-Clic Definitiu)
// @namespace    https://github.com/fortiaarumi/Prediccions
// @version      3.2.0
// @description  Omple la combinada sencera de forma 100% autònoma, ràpida i amb quotes reals a Winamax.
// @author       Prediccions Lliga
// @match        https://winamax.es/*
// @match        https://*.winamax.es/*
// @match        https://winamax.fr/*
// @match        https://*.winamax.fr/*
// @include      *winamax.es/*
// @include      *winamax.fr/*
// @icon         https://www.winamax.es/favicon.ico
// @grant        none
// @run-at       document-idle
// ==/UserScript==

(function() {
    'use strict';

    console.log("[Prediccions Lliga] Winamax Auto-Betslip v3.2.0 iniciat.");

    function parseComboPayload() {
        const fullUrl = window.location.href;
        let raw = null;

        if (fullUrl.includes('combo_autofill=')) {
            try {
                raw = fullUrl.split('combo_autofill=')[1].split('&')[0].split('#')[0];
            } catch (e) {}
        }

        if (!raw) {
            try {
                const stored = sessionStorage.getItem('prediccions_active_combo');
                if (stored) return JSON.parse(stored);
            } catch (e) {}
            return null;
        }

        try {
            const decoded = decodeURIComponent(raw);
            const parsed = JSON.parse(decoded);
            sessionStorage.setItem('prediccions_active_combo', JSON.stringify(parsed));
            sessionStorage.setItem('prediccions_raw_payload', raw);
            return parsed;
        } catch (e) {
            console.error("[Prediccions Lliga] Error analitzant el payload:", e);
            return null;
        }
    }

    const comboData = parseComboPayload();
    if (!comboData || !comboData.legs || comboData.legs.length === 0) {
        return;
    }

    function getCurrentLegIndex() {
        const fullUrl = window.location.href;
        if (fullUrl.includes('leg_idx=')) {
            try {
                const idxStr = fullUrl.split('leg_idx=')[1].split('&')[0].split('#')[0];
                const idx = parseInt(idxStr, 10);
                if (!isNaN(idx) && idx >= 0 && idx < comboData.legs.length) {
                    return idx;
                }
            } catch (e) {}
        }

        const currentPath = window.location.pathname;
        const found = comboData.legs.findIndex(l => {
            if (!l.url) return false;
            const matchId = l.url.split('/').pop();
            return matchId && currentPath.includes(matchId);
        });

        return found !== -1 ? found : 0;
    }

    const currentLegIdx = getCurrentLegIndex();
    const currentLeg = comboData.legs[currentLegIdx];
    const totalLegs = comboData.legs.length;
    const isLastLeg = currentLegIdx >= totalLegs - 1;

    let hasClickedCurrent = false;

    // Crear HUD net i modern a la part superior
    function renderTopHud() {
        if (document.getElementById('prediccions-turbo-hud')) return;

        const hud = document.createElement('div');
        hud.id = 'prediccions-turbo-hud';
        hud.style.cssText = `
            position: fixed;
            top: 20px;
            left: 50%;
            transform: translateX(-50%);
            z-index: 9999999999;
            background: linear-gradient(135deg, #090e1a, #141e30);
            border: 2px solid #10b981;
            border-radius: 14px;
            box-shadow: 0 16px 50px rgba(0, 0, 0, 0.95), 0 0 30px rgba(16, 185, 129, 0.35);
            color: #ffffff;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            padding: 14px 20px;
            width: 450px;
            max-width: 92vw;
            box-sizing: border-box;
            transition: opacity 0.35s ease, transform 0.35s ease;
        `;

        const pct = Math.round(((currentLegIdx) / totalLegs) * 100);

        hud.innerHTML = `
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                <div style="display: flex; align-items: center; gap: 6px;">
                    <span style="font-size: 16px;">⚡</span>
                    <strong style="font-size: 14px; color: #34d399;">Turbo Auto-Betslip v3.2.0</strong>
                </div>
                <div style="font-size: 11px; background: rgba(16,185,129,0.2); color: #34d399; padding: 2px 8px; border-radius: 999px; font-weight: 800; letter-spacing: 0.5px;">
                    PARTIT ${currentLegIdx + 1} DE ${totalLegs}
                </div>
            </div>

            <!-- Barra de Progrés -->
            <div style="background: rgba(255,255,255,0.1); border-radius: 999px; height: 6px; overflow: hidden; margin-bottom: 10px;">
                <div id="turbo-progress-fill" style="background: linear-gradient(90deg, #10b981, #06b6d4); height: 100%; width: ${pct}%; transition: width 0.3s ease;"></div>
            </div>

            <!-- Info del partit actual -->
            <div style="background: rgba(255,255,255,0.05); border: 1px solid rgba(16,185,129,0.25); border-radius: 8px; padding: 8px 12px; margin-bottom: 8px;">
                <div style="font-size: 13px; font-weight: 700; color: #ffffff;">${currentLeg.matchup}</div>
                <div style="font-size: 12px; color: #34d399; font-weight: 700; margin-top: 2px;">
                    👉 Selecció: ${currentLeg.selection || currentLeg.selection_name} (@${currentLeg.odd || currentLeg.bookie_odd || '-'})
                </div>
            </div>

            <div id="turbo-hud-status" style="font-size: 12px; font-weight: 700; color: #f59e0b; text-align: center;">
                ⏳ Buscant i clicant la quota oficial a Winamax...
            </div>
        `;

        document.body.appendChild(hud);
    }

    // Saltar al següent partit o finalitzar
    function goToNextLeg() {
        if (isLastLeg) {
            const hudStatus = document.getElementById('turbo-hud-status');
            if (hudStatus) {
                hudStatus.innerHTML = '🎉 <span style="color: #34d399; font-size: 13px;">COMBINADA COMPLETADA AL 100%! Cupó llest a la cistella!</span>';
            }
            const pFill = document.getElementById('turbo-progress-fill');
            if (pFill) pFill.style.width = '100%';

            setTimeout(() => {
                const hud = document.getElementById('prediccions-turbo-hud');
                if (hud) {
                    hud.style.opacity = '0';
                    hud.style.transform = 'translateX(-50%) translateY(-20px)';
                    setTimeout(() => hud.remove(), 400);
                }
            }, 3000);
            return;
        }

        const nextIdx = currentLegIdx + 1;
        const nextLeg = comboData.legs[nextIdx];
        if (!nextLeg || !nextLeg.url) return;

        let rawPayload = sessionStorage.getItem('prediccions_raw_payload');
        if (!rawPayload) {
            rawPayload = encodeURIComponent(JSON.stringify(comboData));
        }

        const nextUrl = `${nextLeg.url}#combo_autofill=${rawPayload}&leg_idx=${nextIdx}`;
        window.location.href = nextUrl;
    }

    // Helper per normalitzar text (sense accents, minúscules)
    function cleanStr(s) {
        return (s || '')
            .toLowerCase()
            .normalize('NFD')
            .replace(/[\u0300-\u036f]/g, '')
            .trim();
    }

    // Helper per netejar noms d'equips
    function cleanTeam(name) {
        return cleanStr(name)
            .replace(/\b(fc|cf|cd|ud|ca|rcd|sd|ad)\b/g, '')
            .replace(/\b(de|del|la|las|los|el)\b/g, '')
            .trim();
    }

    // Obtenir tots els botons/elements clicables de quota a la pàgina
    function getCandidateButtons() {
        const queryCandidates = Array.from(document.querySelectorAll('button, [role="button"], a[role="button"], div[tabindex]'));
        const filtered = queryCandidates.filter(el => {
            if (el.closest('header, nav, [class*="menu"], [class*="navbar"]')) return false;
            const t = (el.innerText || el.textContent || '').trim();
            return /\b\d+[,.]\d{2}\b/.test(t) && t.length < 80;
        });

        if (filtered.length > 0) return filtered;

        // Fallback genèric
        return Array.from(document.querySelectorAll('div, span, button')).filter(el => {
            if (el.children.length > 3) return false;
            const t = (el.innerText || '').trim();
            return /\b\d+[,.]\d{2}\b/.test(t) && t.length < 40;
        });
    }

    // Matcher intel·ligent per trobar el botó exacte segons mercat
    function findTargetOddButton() {
        const buttons = getCandidateButtons();
        if (buttons.length === 0) return null;

        const legOdd = parseFloat(currentLeg.odd || currentLeg.bookie_odd || 0);
        const oddDot = legOdd > 0 ? legOdd.toFixed(2) : '---';
        const oddComma = legOdd > 0 ? legOdd.toFixed(2).replace('.', ',') : '---';
        
        const selName = cleanStr(currentLeg.selection || currentLeg.selection_name);
        const cat = cleanStr(currentLeg.category);
        const matchup = cleanStr(currentLeg.matchup);

        const parts = matchup.split(' vs ');
        const homeTeam = parts[0] ? parts[0].trim() : '';
        const awayTeam = parts[1] ? parts[1].trim() : '';
        const homeClean = cleanTeam(homeTeam);
        const awayClean = cleanTeam(awayTeam);

        // Helper: comprova si un element conté la quota buscada
        function hasOdd(text) {
            if (legOdd <= 0) return true;
            return text.includes(oddDot) || text.includes(oddComma);
        }

        // =========================================================================
        // 1. MERCAT: DOBLE OPORTUNITAT (1X, X2, 12)
        // =========================================================================
        if (cat.includes('doble') || selName.includes('1x') || selName.includes('x2') || selName.includes('12')) {
            const is1X = selName.includes('1x') || (selName.includes('empat') && homeClean && selName.includes(homeClean));
            const isX2 = selName.includes('x2') || (selName.includes('empat') && awayClean && selName.includes(awayClean));
            const is12 = selName.includes('12');

            // Pass 1: Text de selecció + Quota exacta
            for (const b of buttons) {
                const txt = cleanStr(b.innerText || b.textContent);
                if (is1X && (txt.includes('1x') || (txt.includes('1') && txt.includes('x')) || (homeClean && txt.includes(homeClean) && txt.includes('empat')))) {
                    if (hasOdd(txt)) return b;
                }
                if (isX2 && (txt.includes('x2') || (txt.includes('x') && txt.includes('2')) || (awayClean && txt.includes(awayClean) && txt.includes('empat')))) {
                    if (hasOdd(txt)) return b;
                }
                if (is12 && txt.includes('12')) {
                    if (hasOdd(txt)) return b;
                }
            }

            // Pass 2: Text de selecció (sense exigir quota si ha fluctuat lleugerament)
            for (const b of buttons) {
                const txt = cleanStr(b.innerText || b.textContent);
                if (is1X && (txt.includes('1x') || (txt.includes('1') && txt.includes('x')))) return b;
                if (isX2 && (txt.includes('x2') || (txt.includes('x') && txt.includes('2')))) return b;
                if (is12 && txt.includes('12')) return b;
            }
        }

        // =========================================================================
        // 2. MERCAT: GOLS PER EQUIP (e.g. "Arsenal marca (+0.5 gols)")
        // =========================================================================
        if (selName.includes('marca') || (cat.includes('gol') && (selName.includes('+0.5') || selName.includes('+1.5')))) {
            const line = selName.includes('1.5') ? '1,5' : '0,5';
            const lineAlt = selName.includes('1.5') ? '1.5' : '0.5';
            const targetTeam = (awayClean && selName.includes(awayClean)) ? awayClean : homeClean;

            // Buscar secció específica de l'equip a Winamax
            // Format Winamax: "Número total de goles marcados por [Equipo]"
            const allSections = Array.from(document.querySelectorAll('div, section')).filter(el => {
                const t = cleanStr(el.innerText || '');
                return (t.includes('goles marcados por') || t.includes('total de goles')) && targetTeam && t.includes(targetTeam);
            });

            for (const sec of allSections) {
                const secButtons = Array.from(sec.querySelectorAll('button, [role="button"]'));
                for (const b of secButtons) {
                    const txt = cleanStr(b.innerText || b.textContent);
                    if ((txt.includes('mas de ' + line) || txt.includes('mas de ' + lineAlt) || txt.includes('+' + line) || txt.includes('+' + lineAlt)) && hasOdd(txt)) {
                        return b;
                    }
                }
            }

            // Fallback: Qualsevol botó amb "Más de 0,5" i la quota exacta
            for (const b of buttons) {
                const txt = cleanStr(b.innerText || b.textContent);
                if ((txt.includes('mas de ' + line) || txt.includes('mas de ' + lineAlt) || txt.includes('+' + line)) && hasOdd(txt)) {
                    return b;
                }
            }
        }

        // =========================================================================
        // 3. MERCAT: GOLS TOTALS (e.g. "Menys de 3.5 Gols (Under 3.5)")
        // =========================================================================
        if (selName.includes('under') || selName.includes('over') || selName.includes('menys de') || selName.includes('mes de') || (cat.includes('gol') && selName.includes('3.5'))) {
            const isUnder = selName.includes('under') || selName.includes('menys') || selName.includes('menos');
            let line = '2,5';
            let lineAlt = '2.5';
            if (selName.includes('3.5') || selName.includes('3,5')) { line = '3,5'; lineAlt = '3.5'; }
            else if (selName.includes('1.5') || selName.includes('1,5')) { line = '1,5'; lineAlt = '1.5'; }

            const targetPrefix = isUnder ? 'menos de' : 'mas de';

            for (const b of buttons) {
                const txt = cleanStr(b.innerText || b.textContent);
                if ((txt.includes(targetPrefix + ' ' + line) || txt.includes(targetPrefix + ' ' + lineAlt)) && hasOdd(txt)) {
                    return b;
                }
            }
            // Fallback sense odd exacta
            for (const b of buttons) {
                const txt = cleanStr(b.innerText || b.textContent);
                if (txt.includes(targetPrefix + ' ' + line) || txt.includes(targetPrefix + ' ' + lineAlt)) {
                    return b;
                }
            }
        }

        // =========================================================================
        // 4. MERCAT: 1X2 (RESULTAT DEL PARTIT)
        // =========================================================================
        if (cat === '1x2' || selName.includes('victoria local') || selName.includes('victoria visitant') || selName.startsWith('1 -') || selName.startsWith('2 -')) {
            const is1 = selName.startsWith('1') || selName.includes('local');
            const is2 = selName.startsWith('2') || selName.includes('visitant');
            const isX = selName.startsWith('x') || selName.includes('empat');

            for (const b of buttons) {
                const txt = cleanStr(b.innerText || b.textContent);
                if (!hasOdd(txt)) continue;

                if (is1 && !txt.includes('empate') && !txt.includes('visitante') && !txt.includes('o empate') && !txt.includes('x2')) {
                    if (txt.includes('1') || (homeClean && txt.includes(homeClean))) return b;
                }
                if (is2 && !txt.includes('empate') && !txt.includes('local') && !txt.includes('o empate') && !txt.includes('1x')) {
                    if (txt.includes('2') || (awayClean && txt.includes(awayClean))) return b;
                }
                if (isX && (txt.includes('empate') || txt === 'x')) {
                    return b;
                }
            }
        }

        // =========================================================================
        // 5. MERCAT: AMBÓS EQUIPS MARQUEN (BTTS)
        // =========================================================================
        if (cat.includes('btts') || selName.includes('marquen') || selName.includes('marcan')) {
            const isNo = selName.includes('no');
            const isYes = selName.includes('si') || selName.includes('sí') || selName.includes('yes');

            for (const b of buttons) {
                const txt = cleanStr(b.innerText || b.textContent);
                if (!hasOdd(txt)) continue;

                if (isNo && (txt === 'no' || txt.startsWith('no\n') || txt.includes('no'))) {
                    return b;
                }
                if (isYes && (txt === 'si' || txt.startsWith('si\n') || txt.includes('si'))) {
                    return b;
                }
            }
        }

        // =========================================================================
        // 6. FALLBACK: Quota exacta sempre que no contradigui el mercat
        // =========================================================================
        if (legOdd > 0) {
            for (const b of buttons) {
                const txt = cleanStr(b.innerText || b.textContent);
                if (txt.includes(oddDot) || txt.includes(oddComma)) {
                    return b;
                }
            }
        }

        return null;
    }

    // Execució robusta del clic
    function executeAutoclick() {
        if (hasClickedCurrent) return;

        const btn = findTargetOddButton();
        if (btn) {
            hasClickedCurrent = true;
            try {
                const target = btn.closest('button, [role="button"]') || btn;
                target.scrollIntoView({ behavior: 'smooth', block: 'center' });

                // Comprovar si ja està seleccionat prèviament
                const isAlreadySelected = target.getAttribute('aria-pressed') === 'true' || 
                                          target.classList.contains('selected') || 
                                          target.classList.contains('active');

                if (isAlreadySelected) {
                    console.log("[Prediccions Lliga] La selecció ja estava a la cistella!");
                    const hudStatus = document.getElementById('turbo-hud-status');
                    if (hudStatus) {
                        hudStatus.innerHTML = `✅ <span style="color: #34d399;">Ja seleccionat: ${currentLeg.matchup}! ${isLastLeg ? 'Finalitzant...' : 'Passant al següent partit...'}</span>`;
                    }
                    setTimeout(goToNextLeg, 500);
                    return;
                }

                // Disparar la seqüència completa d'esdeveniments que espera React/Web Components
                const opts = { bubbles: true, cancelable: true, view: window };
                target.dispatchEvent(new PointerEvent('pointerdown', opts));
                target.dispatchEvent(new MouseEvent('mousedown', opts));
                target.dispatchEvent(new PointerEvent('pointerup', opts));
                target.dispatchEvent(new MouseEvent('mouseup', opts));
                target.click();

                // Ressaltar visualment amb verd maragda brillant
                target.style.outline = '4px solid #10b981';
                target.style.boxShadow = '0 0 25px #10b981';

                const hudStatus = document.getElementById('turbo-hud-status');
                if (hudStatus) {
                    hudStatus.innerHTML = `✅ <span style="color: #34d399;">Afegit: ${currentLeg.matchup}! ${isLastLeg ? 'Finalitzant...' : 'Passant al següent partit...'}</span>`;
                }

                const pFill = document.getElementById('turbo-progress-fill');
                if (pFill) {
                    pFill.style.width = `${Math.round(((currentLegIdx + 1) / totalLegs) * 100)}%`;
                }

                // Deixar 750ms perquè Winamax registri la selecció a la cistella i saltar
                setTimeout(goToNextLeg, 750);
            } catch (err) {
                console.error("[Prediccions Lliga] Error clicant:", err);
            }
        }
    }

    // Clic manual de suport com a salvavides (només si fa clic a una quota real)
    document.addEventListener('click', (e) => {
        if (hasClickedCurrent) return;
        const target = e.target.closest('button, [role="button"], div[tabindex]');
        if (!target) return;
        if (target.closest('header, nav, [class*="menu"], [class*="navbar"], [class*="tab"]')) return;
        
        const text = (target.innerText || target.textContent || '').trim();
        if (/\b\d+[,.]\d{2}\b/.test(text) && text.length < 35) {
            console.log("[Prediccions Lliga] Clic manual detectat en quota:", text);
            hasClickedCurrent = true;
            setTimeout(goToNextLeg, 600);
        }
    });

    renderTopHud();

    // Comprovar de manera ultra lleugera cada 200ms durant un màxim d'10 segons (50 intents)
    let attempts = 0;
    const clickInterval = setInterval(() => {
        attempts++;
        if (hasClickedCurrent || attempts > 50) {
            clearInterval(clickInterval);
            if (!hasClickedCurrent && attempts > 50) {
                const hudStatus = document.getElementById('turbo-hud-status');
                if (hudStatus) {
                    hudStatus.innerHTML = `⚠️ <span style="color: #f59e0b;">No s'ha trobat la quota automàticament. Fes clic manualment a la teva selecció per continuar!</span>`;
                }
            }
            return;
        }

        // Si porta 4 intents sense trobar el botó, desplaçar lleugerament per carregar components lazy
        if (attempts === 5 || attempts === 12) {
            window.scrollBy({ top: 300, behavior: 'smooth' });
        }

        executeAutoclick();
    }, 200);

})();
