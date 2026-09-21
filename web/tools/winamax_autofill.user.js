// ==UserScript==
// @name         Prediccions Lliga - Winamax Auto Betslip (1-Clic Turbo)
// @namespace    https://github.com/fortiaarumi/Prediccions
// @version      2.0.0
// @description  Omple automàticament la combinada sencera a Winamax a màxima velocitat en 1 sol clic (Mode Turbo amb HUD integrat).
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

    console.log("[Prediccions Lliga] Winamax Auto-Betslip v2.0.0 Turbo iniciat.");

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

    let hasSelectedCurrent = false;

    // Crear el HUD Turbo centrat i d'alt rendiment
    function renderTurboHud() {
        if (document.getElementById('prediccions-turbo-hud')) return;

        const hud = document.createElement('div');
        hud.id = 'prediccions-turbo-hud';
        hud.style.cssText = `
            position: fixed;
            top: 20px;
            left: 50%;
            transform: translateX(-50%);
            z-index: 9999999999;
            background: rgba(11, 17, 30, 0.95);
            border: 2px solid #10b981;
            border-radius: 16px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.9), 0 0 30px rgba(16, 185, 129, 0.2);
            color: #ffffff;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            padding: 18px 24px;
            width: 440px;
            max-width: 90vw;
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            box-sizing: border-box;
            transition: opacity 0.4s ease, transform 0.4s ease;
        `;

        const pct = Math.round(((currentLegIdx) / totalLegs) * 100);

        hud.innerHTML = `
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                <div style="display: flex; align-items: center; gap: 8px;">
                    <span style="font-size: 18px;">⚡</span>
                    <strong style="font-size: 15px; color: #34d399; letter-spacing: -0.3px;">Turbo Auto-Betslip</strong>
                </div>
                <div style="font-size: 11.5px; background: rgba(16,185,129,0.2); color: #34d399; padding: 2px 8px; border-radius: 999px; font-weight: 800; font-family: monospace;">
                    PAS ${currentLegIdx + 1} DE ${totalLegs}
                </div>
            </div>

            <!-- Progrés -->
            <div style="background: rgba(255,255,255,0.1); border-radius: 999px; height: 7px; overflow: hidden; margin-bottom: 12px;">
                <div id="turbo-progress-fill" style="background: linear-gradient(90deg, #10b981, #06b6d4); height: 100%; width: ${pct}%; transition: width 0.25s ease;"></div>
            </div>

            <!-- Llista de seleccions -->
            <div style="display: flex; flex-direction: column; gap: 6px; margin-bottom: 14px;">
                ${comboData.legs.map((leg, idx) => {
                    let icon = '⏳';
                    let textCol = '#94a3b8';
                    let bg = 'rgba(255,255,255,0.03)';
                    let border = 'rgba(255,255,255,0.08)';

                    if (idx < currentLegIdx) {
                        icon = '✅';
                        textCol = '#34d399';
                        bg = 'rgba(16,185,129,0.08)';
                        border = 'rgba(16,185,129,0.3)';
                    } else if (idx === currentLegIdx) {
                        icon = '🎯';
                        textCol = '#ffffff';
                        bg = 'rgba(99,102,241,0.15)';
                        border = 'rgba(99,102,241,0.5)';
                    }

                    return `
                        <div style="background: ${bg}; border: 1px solid ${border}; border-radius: 8px; padding: 6px 10px; display: flex; justify-content: space-between; align-items: center; font-size: 12px;">
                            <div style="flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-right: 8px;">
                                <span style="margin-right: 4px;">${icon}</span>
                                <span style="font-weight: 600; color: ${textCol};">${leg.matchup.split(' vs ')[0]} vs ${leg.matchup.split(' vs ')[1] || ''}:</span>
                                <span style="color: ${textCol};">${leg.selection || leg.selection_name}</span>
                            </div>
                            <div style="font-weight: 800; color: #a5b4fc; font-family: monospace;">@${leg.odd || leg.bookie_odd}</div>
                        </div>
                    `;
                }).join('')}
            </div>

            <div id="turbo-hud-status" style="font-size: 12.5px; font-weight: 700; color: #f59e0b; text-align: center; display: flex; align-items: center; justify-content: center; gap: 6px;">
                <span>⚡ Clicant quota de ${currentLeg.matchup}...</span>
            </div>

            <!-- Botons ràpids per si l'usuari vol controlar -->
            <div style="display: flex; gap: 8px; margin-top: 10px;">
                <button id="turbo-btn-next" style="flex: 1; background: rgba(255,255,255,0.08); color: #cbd5e1; border: 1px solid rgba(255,255,255,0.15); border-radius: 6px; padding: 7px; font-size: 11.5px; font-weight: 600; cursor: pointer;">
                    ${isLastLeg ? '🏁 Finalitzar' : '➡️ Saltar Partit'}
                </button>
                <button id="turbo-btn-close" style="background: none; color: #64748b; border: none; padding: 7px 10px; font-size: 11.5px; cursor: pointer;">
                    Ocultar
                </button>
            </div>
        `;

        document.body.appendChild(hud);

        document.getElementById('turbo-btn-close').onclick = () => hud.remove();
        document.getElementById('turbo-btn-next').onclick = () => fastHopToNextLeg();
    }

    // Salt instantani al següent partit
    function fastHopToNextLeg() {
        if (isLastLeg) {
            const hudStatus = document.getElementById('turbo-hud-status');
            if (hudStatus) {
                hudStatus.innerHTML = '🎉 <span style="color: #34d399; font-size: 13.5px;">COMBINADA COMPLETADA AL 100%! Cupó llest a la teva cistella!</span>';
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

    // Canvi instantani a pestanya de mercats si cal (Goles, Tarjetas, etc.)
    function instantOpenMarketTab() {
        const cat = (currentLeg.category || '').toLowerCase();
        const sel = (currentLeg.selection || currentLeg.selection_name || '').toLowerCase();

        let keyword = null;
        if (cat.includes('gol') || sel.includes('gol') || sel.includes('marca') || sel.includes('+') || sel.includes('-')) {
            keyword = 'goles';
        } else if (cat.includes('target') || sel.includes('target') || sel.includes('amarill')) {
            keyword = 'tarjetas';
        } else if (cat.includes('córner') || sel.includes('corner')) {
            keyword = 'córners';
        }

        if (!keyword) return;

        const tabs = Array.from(document.querySelectorAll('button, div[role="tab"], span'));
        for (const t of tabs) {
            const txt = (t.innerText || t.textContent || '').trim().toLowerCase();
            if (txt.startsWith(keyword) && txt.length < 20) {
                t.click();
                break;
            }
        }
    }

    // Matcher ràpid i precís
    function findTargetButton() {
        const targetOdd = parseFloat(currentLeg.odd || currentLeg.bookie_odd);
        const oddDot = targetOdd.toFixed(2);
        const oddComma = targetOdd.toFixed(2).replace('.', ',');
        const targetName = (currentLeg.selection || currentLeg.selection_name || '').toLowerCase();

        const buttons = Array.from(document.querySelectorAll('button, div[role="button"]'));
        if (buttons.length === 0) return null;

        for (const btn of buttons) {
            const txt = (btn.innerText || btn.textContent || '').trim().toLowerCase();
            if (!txt) continue;

            const hasOdd = txt.includes(oddDot) || txt.includes(oddComma) || txt.includes(`@${oddDot}`) || txt.includes(`@${oddComma}`);
            if (hasOdd) {
                if (targetName.includes('1x') && (txt.includes('1x') || txt.includes('1 o x'))) return btn;
                if (targetName.includes('x2') && (txt.includes('x2') || txt.includes('x o 2'))) return btn;
                if (targetName.includes('12') && (txt.includes('12') || txt.includes('1 o 2'))) return btn;
                if (targetName.includes('local') || targetName.startsWith('1')) {
                    if (txt.includes('1') && !txt.includes('1x') && !txt.includes('+1')) return btn;
                }
                if (targetName.includes('visitant') || targetName.startsWith('2')) {
                    if (txt.includes('2') && !txt.includes('x2') && !txt.includes('+2')) return btn;
                }
                if (targetName.includes('empat') && (txt.includes('x') || txt.includes('empate') || txt.includes('nul'))) return btn;
                if ((targetName.includes('+0.5') || targetName.includes('marca')) && (txt.includes('+0.5') || txt.includes('sí') || txt.includes('si') || txt.includes('marca'))) return btn;
                if (targetName.includes('+1.5') && txt.includes('+1.5')) return btn;
                if (targetName.includes('-3.5') && txt.includes('-3.5')) return btn;
                if (targetName.includes('+2.5') && txt.includes('+2.5')) return btn;
                if (targetName.includes('btts') || targetName.includes('ambdós')) {
                    if (txt.includes('sí') || txt.includes('si') || txt.includes('ambos')) return btn;
                }
                return btn;
            }
        }
        return null;
    }

    // Execució Turbo: cerca cada 120ms
    function turboRun() {
        if (hasSelectedCurrent) return;

        instantOpenMarketTab();

        const btn = findTargetButton();
        if (btn) {
            hasSelectedCurrent = true;
            try {
                btn.click();
                btn.style.outline = '3px solid #10b981';
                btn.style.boxShadow = '0 0 20px #10b981';

                const status = document.getElementById('turbo-hud-status');
                if (status) {
                    status.innerHTML = `✅ <span style="color: #34d399;">Afegida! ${isLastLeg ? 'Finalitzant...' : 'Saltant al següent partit...'}</span>`;
                }

                // Espera mínima de 250ms només per deixar que React actualitzi l'estat intern
                setTimeout(fastHopToNextLeg, 280);
            } catch (err) {
                console.error("[Prediccions Lliga] Error:", err);
            }
        }
    }

    // Escolta de clics manuals com a pla B
    document.addEventListener('click', (e) => {
        if (hasSelectedCurrent) return;
        const target = e.target.closest('button, div[role="button"]');
        if (!target || target.id === 'turbo-btn-next' || target.id === 'turbo-btn-close') return;

        const text = (target.innerText || target.textContent || '').trim();
        const targetOdd = parseFloat(currentLeg.odd || currentLeg.bookie_odd);
        if (text.includes(targetOdd.toFixed(2)) || text.includes(targetOdd.toFixed(2).replace('.', ','))) {
            hasSelectedCurrent = true;
            setTimeout(fastHopToNextLeg, 200);
        }
    });

    // Iniciar HUD a l'instant
    renderTurboHud();

    // Polling d'alta freqüència (cada 150ms durant un màxim de 2 segons)
    let checks = 0;
    const turboInterval = setInterval(() => {
        checks++;
        if (hasSelectedCurrent || checks > 15) {
            clearInterval(turboInterval);
            if (!hasSelectedCurrent) {
                const status = document.getElementById('turbo-hud-status');
                if (status) {
                    status.innerHTML = `⚠️ <span style="color: #fde047;">Clica la quota del partit o prem 'Saltar Partit'</span>`;
                }
            }
            return;
        }
        turboRun();
    }, 150);

})();
