// ==UserScript==
// @name         Prediccions Lliga - Winamax Auto Betslip (1-Clic Definitiu)
// @namespace    https://github.com/fortiaarumi/Prediccions
// @version      3.0.0
// @description  Omple la combinada sencera de forma 100% autònoma, sense parpellejos i a màxima velocitat.
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

    console.log("[Prediccions Lliga] Winamax Auto-Betslip v3.0.0 iniciat.");

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

    // Crear HUD net a la part superior
    function renderTopHud() {
        if (document.getElementById('prediccions-turbo-hud')) return;

        const hud = document.createElement('div');
        hud.id = 'prediccions-turbo-hud';
        hud.style.cssText = `
            position: fixed;
            top: 18px;
            left: 50%;
            transform: translateX(-50%);
            z-index: 9999999999;
            background: linear-gradient(135deg, #090e1a, #141e30);
            border: 2px solid #10b981;
            border-radius: 14px;
            box-shadow: 0 16px 50px rgba(0, 0, 0, 0.9), 0 0 25px rgba(16, 185, 129, 0.25);
            color: #ffffff;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            padding: 14px 20px;
            width: 420px;
            max-width: 92vw;
            box-sizing: border-box;
            transition: opacity 0.35s ease, transform 0.35s ease;
        `;

        const pct = Math.round(((currentLegIdx) / totalLegs) * 100);

        hud.innerHTML = `
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                <div style="display: flex; align-items: center; gap: 6px;">
                    <span style="font-size: 16px;">⚡</span>
                    <strong style="font-size: 14px; color: #34d399;">Creant Combinada Automàticament</strong>
                </div>
                <div style="font-size: 11px; background: rgba(16,185,129,0.2); color: #34d399; padding: 2px 7px; border-radius: 999px; font-weight: 800;">
                    PARTIT ${currentLegIdx + 1} DE ${totalLegs}
                </div>
            </div>

            <!-- Progrés -->
            <div style="background: rgba(255,255,255,0.1); border-radius: 999px; height: 6px; overflow: hidden; margin-bottom: 10px;">
                <div id="turbo-progress-fill" style="background: linear-gradient(90deg, #10b981, #06b6d4); height: 100%; width: ${pct}%; transition: width 0.3s ease;"></div>
            </div>

            <!-- Info del partit actual -->
            <div style="background: rgba(255,255,255,0.05); border: 1px solid rgba(16,185,129,0.25); border-radius: 8px; padding: 8px 12px; margin-bottom: 8px;">
                <div style="font-size: 13px; font-weight: 700; color: #ffffff;">${currentLeg.matchup}</div>
                <div style="font-size: 12px; color: #34d399; font-weight: 700; margin-top: 2px;">
                    👉 Aposta: ${currentLeg.selection || currentLeg.selection_name}
                </div>
            </div>

            <div id="turbo-hud-status" style="font-size: 12px; font-weight: 700; color: #f59e0b; text-align: center;">
                ⏳ Clicant la quota oficial...
            </div>
        `;

        document.body.appendChild(hud);
    }

    // Saltar al següent partit
    function goToNextLeg() {
        if (isLastLeg) {
            const hudStatus = document.getElementById('turbo-hud-status');
            if (hudStatus) {
                hudStatus.innerHTML = '🎉 <span style="color: #34d399; font-size: 13.5px;">COMBINADA COMPLETADA AL 100%! Cupó llest a la cistella!</span>';
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
            }, 2500);
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

    // Matcher infal·lible per trobar el botó adequat a la pantalla
    function findTargetOddButton() {
        const buttons = Array.from(document.querySelectorAll('button, div[role="button"]'));
        if (buttons.length === 0) return null;

        const targetOdd = parseFloat(currentLeg.odd || currentLeg.bookie_odd || 0);
        const oddDot = targetOdd > 0 ? targetOdd.toFixed(2) : '---';
        const oddComma = targetOdd > 0 ? targetOdd.toFixed(2).replace('.', ',') : '---';
        const selName = (currentLeg.selection || currentLeg.selection_name || '').toLowerCase();
        const matchup = (currentLeg.matchup || '').toLowerCase();
        
        const homeTeam = matchup.includes(' vs ') ? matchup.split(' vs ')[0].trim() : '';
        const awayTeam = matchup.includes(' vs ') ? matchup.split(' vs ')[1].trim() : '';

        const homeCore = homeTeam.replace(/^(fc|ca|ud|rcd|cd|cf)\s+/i, '').replace(/\s+(cf|fc|sad)$/i, '').trim();
        const awayCore = awayTeam.replace(/^(fc|ca|ud|rcd|cd|cf)\s+/i, '').replace(/\s+(cf|fc|sad)$/i, '').trim();

        // 1. Prioritat 1: Trobar la quota exacta (ex: 1.10 o 1,10)
        if (targetOdd > 0) {
            for (const b of buttons) {
                const txt = (b.innerText || b.textContent || '').trim().toLowerCase();
                if (txt.includes(oddDot) || txt.includes(oddComma)) {
                    return b;
                }
            }
        }

        // 2. Prioritat 2: Doble oportunitat (1X / X2)
        if (selName.includes('1x') || selName.includes('o empat') || selName.includes('o empate')) {
            for (const b of buttons) {
                const txt = (b.innerText || b.textContent || '').trim().toLowerCase();
                if (txt.includes('1x') || (txt.includes('1') && txt.includes('x'))) {
                    return b;
                }
            }
        }
        if (selName.includes('x2')) {
            for (const b of buttons) {
                const txt = (b.innerText || b.textContent || '').trim().toLowerCase();
                if (txt.includes('x2') || (txt.includes('x') && txt.includes('2'))) {
                    return b;
                }
            }
        }

        // 3. Prioritat 3: Equip local (1 / Local / Marca / Guanya)
        if (selName.includes('local') || selName.startsWith('1') || selName.includes(homeCore) || selName.includes('marca')) {
            for (const b of buttons) {
                const txt = (b.innerText || b.textContent || '').trim().toLowerCase();
                if (homeCore && txt.includes(homeCore) && !txt.includes('empate') && !txt.includes('visitante')) {
                    return b;
                }
            }
        }

        // 4. Prioritat 4: Equip visitant (2 / Visitant)
        if (selName.includes('visitant') || selName.startsWith('2') || selName.includes(awayCore)) {
            for (const b of buttons) {
                const txt = (b.innerText || b.textContent || '').trim().toLowerCase();
                if (awayCore && txt.includes(awayCore) && !txt.includes('empate') && !txt.includes('local')) {
                    return b;
                }
            }
        }

        // 5. Prioritat 5: El botó principal del partit (equip de casa o primera quota)
        const primaryCandidates = buttons.filter(b => {
            const txt = (b.innerText || b.textContent || '').trim().toLowerCase();
            return (homeCore && txt.includes(homeCore)) || /\d+[,.]\d{2}/.test(txt);
        });

        if (primaryCandidates.length > 0) {
            return primaryCandidates[0];
        }

        return null;
    }

    // Execució del clic fiable
    function executeAutoclick() {
        if (hasClickedCurrent) return;

        const btn = findTargetOddButton();
        if (btn) {
            hasClickedCurrent = true;
            try {
                // Assegurar esdeveniments natius de ratolí
                btn.scrollIntoView({ behavior: 'smooth', block: 'center' });
                btn.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
                btn.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
                btn.click();
                btn.style.outline = '4px solid #10b981';
                btn.style.boxShadow = '0 0 25px #10b981';

                const hudStatus = document.getElementById('turbo-hud-status');
                if (hudStatus) {
                    hudStatus.innerHTML = `✅ <span style="color: #34d399;">Afegit: ${currentLeg.matchup}! ${isLastLeg ? 'Finalitzant...' : 'Passant al següent partit...'}</span>`;
                }

                const pFill = document.getElementById('turbo-progress-fill');
                if (pFill) {
                    pFill.style.width = `${Math.round(((currentLegIdx + 1) / totalLegs) * 100)}%`;
                }

                // Deixar 600ms perquè Winamax desi la selecció a la cistella i saltar
                setTimeout(goToNextLeg, 600);
            } catch (err) {
                console.error("[Prediccions Lliga] Error clicant:", err);
            }
        }
    }

    // Clic manual de suport
    document.addEventListener('click', (e) => {
        if (hasClickedCurrent) return;
        const target = e.target.closest('button, div[role="button"]');
        if (!target) return;
        const text = (target.innerText || target.textContent || '').trim();
        if (/\d+[,.]\d{2}/.test(text)) {
            hasClickedCurrent = true;
            setTimeout(goToNextLeg, 500);
        }
    });

    renderTopHud();

    // Intentar clicar als 300ms, 600ms, 1000ms i 1500ms
    setTimeout(executeAutoclick, 300);
    setTimeout(executeAutoclick, 650);
    setTimeout(executeAutoclick, 1100);
    setTimeout(executeAutoclick, 1600);

})();
