// ==UserScript==
// @name         Prediccions Lliga - Winamax Auto Betslip (1-Clic)
// @namespace    https://github.com/fortiaarumi/Prediccions
// @version      1.2.0
// @description  Afegeix automàticament les combinades de Prediccions Lliga al cupó de Winamax en 1 sol clic (Navegació automàtica pas a pas entre partits).
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

    console.log("[Prediccions Lliga] Winamax Auto-Betslip v1.2.0 inicialitzat.");

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

    // Obtenir la posició del partit actual dins la combinada
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

        // Si no hi ha leg_idx, deduir-ho pel pathname del partit actual
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

    console.log(`[Prediccions Lliga] Partit actual: ${currentLegIdx + 1}/${totalLegs}`, currentLeg);

    let hasSelectedCurrent = false;

    // Crear widget flotant ultra-visible i interactiu
    function createOverlayWidget() {
        if (document.getElementById('prediccions-winamax-dock')) return;

        const dock = document.createElement('div');
        dock.id = 'prediccions-winamax-dock';
        dock.style.cssText = `
            position: fixed;
            bottom: 24px;
            right: 24px;
            z-index: 999999999;
            background: linear-gradient(135deg, #090d16, #131b2e);
            border: 2px solid #10b981;
            border-radius: 14px;
            box-shadow: 0 15px 45px rgba(0,0,0,0.9);
            color: #ffffff;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            padding: 16px 18px;
            width: 350px;
            font-size: 13px;
            line-height: 1.4;
        `;

        const progressPercent = Math.round(((currentLegIdx) / totalLegs) * 100);

        dock.innerHTML = `
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                <div style="font-weight: 800; font-size: 14px; color: #34d399; display: flex; align-items: center; gap: 6px;">
                    <span>⚡ Prediccions Lliga</span>
                    <span style="font-size: 11px; background: rgba(16,185,129,0.2); color: #34d399; padding: 2px 6px; border-radius: 4px; font-weight: 700;">Pas ${currentLegIdx + 1} de ${totalLegs}</span>
                </div>
                <button id="close-dock-btn" style="background: none; border: none; color: #94a3b8; cursor: pointer; font-size: 16px; padding: 2px 6px;">✕</button>
            </div>

            <!-- Barra de progrés -->
            <div style="background: rgba(255,255,255,0.1); border-radius: 999px; height: 6px; overflow: hidden; margin-bottom: 12px;">
                <div id="dock-progress-bar" style="background: linear-gradient(90deg, #10b981, #06b6d4); height: 100%; width: ${progressPercent}%; transition: width 0.3s ease;"></div>
            </div>

            <div style="font-size: 12px; color: #94a3b8; margin-bottom: 4px;">
                Combinada: <strong style="color: #ffffff;">${comboData.profile || 'Recomanada'}</strong> (@${comboData.odd || comboData.boosted_odd || '-'})
            </div>

            <!-- Targeta del partit actual -->
            <div style="background: rgba(255,255,255,0.06); border: 1px solid rgba(16,185,129,0.35); border-radius: 8px; padding: 10px 12px; margin-bottom: 12px;">
                <div style="font-size: 11px; color: #a5b4fc; font-weight: 700; text-transform: uppercase;">Partit Actual (${currentLegIdx + 1}/${totalLegs}):</div>
                <div style="font-size: 13.5px; font-weight: 700; color: #ffffff; margin-top: 2px;">${currentLeg.matchup}</div>
                
                <div style="margin-top: 8px; padding: 6px 8px; background: rgba(16,185,129,0.12); border-left: 3px solid #10b981; border-radius: 4px;">
                    <div style="font-size: 10.5px; color: #94a3b8;">Selecció a marcar:</div>
                    <div style="font-size: 13px; font-weight: 800; color: #34d399;">
                        👉 ${currentLeg.selection || currentLeg.selection_name} 
                        <span style="color: #ffffff; font-family: monospace; margin-left: 4px;">@${currentLeg.odd || currentLeg.bookie_odd}</span>
                    </div>
                </div>

                <div id="dock-status-msg" style="margin-top: 8px; font-size: 12px; font-weight: 700; color: #f59e0b; display: flex; align-items: center; gap: 6px;">
                    <span>⏳ Cercant i clicant la casella...</span>
                </div>
            </div>

            <!-- Botons d'acció -->
            <div style="display: flex; gap: 8px;">
                <button id="btn-next-leg" style="flex: 1.2; background: #059669; color: #ffffff; border: none; border-radius: 6px; padding: 9px; font-weight: 700; cursor: pointer; font-size: 12px; transition: background 0.2s;">
                    ${isLastLeg ? '🏁 Finalitzar Cupó' : '➡️ Següent Partit'}
                </button>
                <button id="btn-open-all-tabs" style="flex: 0.8; background: rgba(255,255,255,0.08); color: #cbd5e1; border: 1px solid rgba(255,255,255,0.15); border-radius: 6px; padding: 9px; font-weight: 600; cursor: pointer; font-size: 11.5px;">
                    📑 Obrir Tots
                </button>
            </div>
        `;

        document.body.appendChild(dock);

        document.getElementById('close-dock-btn').onclick = () => dock.remove();
        document.getElementById('btn-next-leg').onclick = () => goToNextLeg();
        document.getElementById('btn-open-all-tabs').onclick = () => {
            comboData.legs.forEach(leg => {
                if (leg.url) window.open(leg.url, '_blank');
            });
        };
    }

    // Navegar automàticament al següent partit de la combinada en la mateixa pestanya
    function goToNextLeg() {
        if (isLastLeg) {
            const statusEl = document.getElementById('dock-status-msg');
            if (statusEl) {
                statusEl.innerHTML = '🎉 <span style="color: #34d399;">COMBINADA COMPLETADA! Cupó llest amb totes les seleccions!</span>';
            }
            const btnNext = document.getElementById('btn-next-leg');
            if (btnNext) {
                btnNext.style.background = '#10b981';
                btnNext.textContent = '✅ Fet! Introdueix el teu import';
            }
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
        console.log(`[Prediccions Lliga] Navegant al partit ${nextIdx + 1}: ${nextUrl}`);

        const statusEl = document.getElementById('dock-status-msg');
        if (statusEl) {
            statusEl.innerHTML = `🚀 <span style="color: #34d399;">Obrint partit ${nextIdx + 1} de ${totalLegs}...</span>`;
        }

        setTimeout(() => {
            window.location.href = nextUrl;
        }, 500);
    }

    // Intentar canviar de pestanya a Winamax si cal (ex: Goles, Resultado, etc.)
    function tryOpenMarketTab() {
        const cat = (currentLeg.category || '').toLowerCase();
        const sel = (currentLeg.selection || currentLeg.selection_name || '').toLowerCase();

        const tabs = Array.from(document.querySelectorAll('button, div[role="tab"], span'));
        for (const t of tabs) {
            const txt = (t.innerText || t.textContent || '').trim().toLowerCase();
            if (cat.includes('gol') || sel.includes('gol') || sel.includes('marca') || sel.includes('+') || sel.includes('-')) {
                if (txt.startsWith('goles') || txt === 'goles') {
                    t.click();
                    break;
                }
            } else if (cat.includes('target') || sel.includes('target')) {
                if (txt.includes('tarjetas')) {
                    t.click();
                    break;
                }
            } else if (cat.includes('córner') || sel.includes('corner')) {
                if (txt.includes('córners') || txt.includes('corners')) {
                    t.click();
                    break;
                }
            }
        }
    }

    // Algoritme intel·ligent de selecció i clic de la quota
    function attemptSelectCurrentLeg() {
        if (hasSelectedCurrent) return;

        const targetOdd = parseFloat(currentLeg.odd || currentLeg.bookie_odd);
        const oddStrDot = targetOdd.toFixed(2);
        const oddStrComma = targetOdd.toFixed(2).replace('.', ',');
        const targetName = (currentLeg.selection || currentLeg.selection_name || '').toLowerCase();

        const buttons = Array.from(document.querySelectorAll('button, div[role="button"], a[role="button"]'));
        if (buttons.length === 0) return;

        let matchedBtn = null;

        for (const btn of buttons) {
            const text = (btn.innerText || btn.textContent || '').trim().toLowerCase();
            if (!text) continue;

            const hasOdd = text.includes(oddStrDot) || text.includes(oddStrComma) || text.includes(`@${oddStrDot}`) || text.includes(`@${oddStrComma}`);
            if (hasOdd) {
                if (targetName.includes('1x') && text.includes('1x')) { matchedBtn = btn; break; }
                if (targetName.includes('x2') && text.includes('x2')) { matchedBtn = btn; break; }
                if (targetName.includes('12') && text.includes('12')) { matchedBtn = btn; break; }
                if ((targetName.includes('local') || targetName.startsWith('1')) && text.includes('1') && !text.includes('1x') && !text.includes('+1')) { matchedBtn = btn; break; }
                if ((targetName.includes('visitant') || targetName.startsWith('2')) && text.includes('2') && !text.includes('x2') && !text.includes('+2')) { matchedBtn = btn; break; }
                if (targetName.includes('empat') && (text.includes('x') || text.includes('empate') || text.includes('nul'))) { matchedBtn = btn; break; }
                if ((targetName.includes('+0.5') || targetName.includes('marca')) && (text.includes('+0.5') || text.includes('sí') || text.includes('si'))) { matchedBtn = btn; break; }
                if (targetName.includes('+1.5') && text.includes('+1.5')) { matchedBtn = btn; break; }
                if (targetName.includes('-3.5') && text.includes('-3.5')) { matchedBtn = btn; break; }
                if (targetName.includes('+2.5') && text.includes('+2.5')) { matchedBtn = btn; break; }
                if (targetName.includes('btts') || targetName.includes('ambdós')) {
                    if (text.includes('sí') || text.includes('si') || text.includes('ambos')) { matchedBtn = btn; break; }
                }
                if (!matchedBtn) matchedBtn = btn;
            }
        }

        if (matchedBtn) {
            hasSelectedCurrent = true;
            try {
                matchedBtn.click();
                matchedBtn.style.outline = '3px solid #10b981';
                matchedBtn.style.boxShadow = '0 0 15px #10b981';

                const statusEl = document.getElementById('dock-status-msg');
                const pBar = document.getElementById('dock-progress-bar');
                const newPct = Math.round(((currentLegIdx + 1) / totalLegs) * 100);
                if (pBar) pBar.style.width = `${newPct}%`;

                if (isLastLeg) {
                    if (statusEl) {
                        statusEl.innerHTML = '🎉 <span style="color: #34d399;">COMBINADA COMPLETADA! Ja tens les ' + totalLegs + ' seleccions al cupó!</span>';
                    }
                    const btnNext = document.getElementById('btn-next-leg');
                    if (btnNext) {
                        btnNext.style.background = '#10b981';
                        btnNext.textContent = '✅ Fet! Introdueix el teu import';
                    }
                } else {
                    if (statusEl) {
                        statusEl.innerHTML = `✅ <span style="color: #34d399;">Afegida! Passant al partit ${currentLegIdx + 2} en 1.5s...</span>`;
                    }
                    setTimeout(goToNextLeg, 1500);
                }
            } catch (err) {
                console.error("[Prediccions Lliga] Error fent clic:", err);
            }
        } else {
            // Si encara no l'ha trobada, intenta obrir pestanyes de mercat
            tryOpenMarketTab();
        }
    }

    // Escolta de clics manuals: si l'usuari clica la quota manualment, avança també
    document.addEventListener('click', (e) => {
        if (hasSelectedCurrent) return;
        const target = e.target.closest('button, div[role="button"]');
        if (!target || target.id === 'btn-next-leg' || target.id === 'btn-open-all-tabs') return;

        const text = (target.innerText || target.textContent || '').trim();
        const targetOdd = parseFloat(currentLeg.odd || currentLeg.bookie_odd);
        if (text.includes(targetOdd.toFixed(2)) || text.includes(targetOdd.toFixed(2).replace('.', ','))) {
            hasSelectedCurrent = true;
            const statusEl = document.getElementById('dock-status-msg');
            if (statusEl) {
                statusEl.innerHTML = `✅ <span style="color: #34d399;">Detectat manualment! Passant al següent...</span>`;
            }
            if (!isLastLeg) {
                setTimeout(goToNextLeg, 1000);
            }
        }
    });

    // Cicle d'execució progressiu
    setTimeout(createOverlayWidget, 400);
    setTimeout(attemptSelectCurrentLeg, 1000);
    setTimeout(attemptSelectCurrentLeg, 2200);
    setTimeout(attemptSelectCurrentLeg, 3800);

    // Observador continu de canvis al DOM
    const observer = new MutationObserver(() => {
        if (!hasSelectedCurrent) {
            attemptSelectCurrentLeg();
        }
    });

    setTimeout(() => {
        observer.observe(document.body, { childList: true, subtree: true });
    }, 1200);

})();
