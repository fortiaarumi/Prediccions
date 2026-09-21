// ==UserScript==
// @name         Prediccions Lliga - Winamax Auto Betslip (1-Clic)
// @namespace    https://github.com/fortiaarumi/Prediccions
// @version      1.3.0
// @description  Afegeix automàticament les combinades de Prediccions Lliga al cupó de Winamax en 1 sol clic (Navegació pas a pas fluida i sense bloquejos).
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

    console.log("[Prediccions Lliga] Winamax Auto-Betslip v1.3.0 iniciat.");

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
    let switchedTabOnce = false;

    // Crear widget flotant net, ràpid i sense càrrega de CPU
    function createOverlayWidget() {
        if (document.getElementById('prediccions-winamax-dock')) return;

        const dock = document.createElement('div');
        dock.id = 'prediccions-winamax-dock';
        dock.style.cssText = `
            position: fixed;
            bottom: 20px;
            right: 20px;
            z-index: 999999999;
            background: linear-gradient(135deg, #0b111e, #131c2e);
            border: 2px solid #10b981;
            border-radius: 14px;
            box-shadow: 0 16px 40px rgba(0,0,0,0.9);
            color: #ffffff;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            padding: 16px 18px;
            width: 360px;
            font-size: 13px;
            line-height: 1.4;
            box-sizing: border-box;
        `;

        const progressPercent = Math.round(((currentLegIdx) / totalLegs) * 100);

        dock.innerHTML = `
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                <div style="font-weight: 800; font-size: 14px; color: #34d399; display: flex; align-items: center; gap: 6px;">
                    <span>⚡ Prediccions Lliga</span>
                    <span style="font-size: 11px; background: rgba(16,185,129,0.2); color: #34d399; padding: 2px 6px; border-radius: 4px; font-weight: 700;">Pas ${currentLegIdx + 1} de ${totalLegs}</span>
                </div>
                <button id="close-dock-btn" style="background: none; border: none; color: #94a3b8; cursor: pointer; font-size: 18px; line-height: 1;">✕</button>
            </div>

            <!-- Progrés -->
            <div style="background: rgba(255,255,255,0.1); border-radius: 999px; height: 6px; overflow: hidden; margin-bottom: 10px;">
                <div id="dock-progress-bar" style="background: linear-gradient(90deg, #10b981, #06b6d4); height: 100%; width: ${progressPercent}%; transition: width 0.3s ease;"></div>
            </div>

            <div style="font-size: 11.5px; color: #94a3b8; margin-bottom: 8px;">
                Combinada: <strong style="color: #ffffff;">${comboData.profile || 'Recomanada'}</strong> 
                <span style="color: #34d399; font-weight: 700;">@${comboData.odd || comboData.boosted_odd || '-'}</span>
            </div>

            <!-- Partit actual -->
            <div style="background: rgba(255,255,255,0.05); border: 1px solid rgba(16,185,129,0.3); border-radius: 8px; padding: 10px; margin-bottom: 12px;">
                <div style="font-size: 10.5px; color: #a5b4fc; font-weight: 700; text-transform: uppercase;">Partit ${currentLegIdx + 1} de ${totalLegs}:</div>
                <div style="font-size: 13.5px; font-weight: 700; color: #ffffff; margin-top: 2px;">${currentLeg.matchup}</div>
                
                <div style="margin-top: 8px; padding: 8px 10px; background: rgba(16,185,129,0.12); border-left: 3px solid #10b981; border-radius: 4px;">
                    <div style="font-size: 10.5px; color: #94a3b8;">Casella a marcar:</div>
                    <div style="font-size: 13px; font-weight: 800; color: #34d399;">
                        👉 ${currentLeg.selection || currentLeg.selection_name}
                        <span style="color: #ffffff; font-family: monospace; margin-left: 4px;">@${currentLeg.odd || currentLeg.bookie_odd}</span>
                    </div>
                </div>

                <div id="dock-status-msg" style="margin-top: 8px; font-size: 12px; font-weight: 700; color: #f59e0b;">
                    ⏳ Cercant quota...
                </div>
            </div>

            <!-- Botons -->
            <div style="display: flex; gap: 8px;">
                <button id="btn-next-leg" style="flex: 1.2; background: #059669; color: #ffffff; border: none; border-radius: 6px; padding: 9px; font-weight: 700; cursor: pointer; font-size: 12px;">
                    ${isLastLeg ? '🏁 Finalitzar Cupó' : '➡️ Següent Partit'}
                </button>
                <button id="btn-switch-tab" style="flex: 1; background: rgba(59,130,246,0.2); color: #93c5fd; border: 1px solid rgba(59,130,246,0.4); border-radius: 6px; padding: 9px; font-weight: 600; cursor: pointer; font-size: 11.5px;">
                    📂 Obrir Goles / Mercat
                </button>
            </div>
        `;

        document.body.appendChild(dock);

        document.getElementById('close-dock-btn').onclick = () => dock.remove();
        document.getElementById('btn-next-leg').onclick = () => goToNextLeg();
        document.getElementById('btn-switch-tab').onclick = () => trySwitchToMarketTab(true);
    }

    // Saltar al següent partit
    function goToNextLeg() {
        if (isLastLeg) {
            const statusEl = document.getElementById('dock-status-msg');
            if (statusEl) {
                statusEl.innerHTML = '🎉 <span style="color: #34d399;">COMBINADA COMPLETADA! Cupó llest!</span>';
            }
            const btnNext = document.getElementById('btn-next-leg');
            if (btnNext) {
                btnNext.style.background = '#10b981';
                btnNext.textContent = '✅ Fet! Aposta llesta';
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
            statusEl.innerHTML = `🚀 <span style="color: #34d399;">Carregant partit ${nextIdx + 1} de ${totalLegs}...</span>`;
        }

        setTimeout(() => {
            window.location.href = nextUrl;
        }, 400);
    }

    // Canviar automàticament a la pestanya de mercat adequada (ex: Goles, Tarjetas)
    function trySwitchToMarketTab(force) {
        if (switchedTabOnce && !force) return;

        const cat = (currentLeg.category || '').toLowerCase();
        const sel = (currentLeg.selection || currentLeg.selection_name || '').toLowerCase();

        // Buscar botons de pestanya de Winamax
        const buttons = Array.from(document.querySelectorAll('button, div[role="tab"], div[role="button"], span'));
        
        let targetKeyword = null;
        if (cat.includes('gol') || sel.includes('gol') || sel.includes('marca') || sel.includes('+') || sel.includes('-')) {
            targetKeyword = 'goles';
        } else if (cat.includes('target') || sel.includes('target') || sel.includes('amarill')) {
            targetKeyword = 'tarjetas';
        } else if (cat.includes('córner') || sel.includes('corner')) {
            targetKeyword = 'córners';
        }

        if (targetKeyword) {
            for (const b of buttons) {
                const txt = (b.innerText || b.textContent || '').trim().toLowerCase();
                if (txt.includes(targetKeyword) && txt.length < 25) {
                    console.log(`[Prediccions Lliga] Clicant pestanya de mercat '${targetKeyword}':`, b);
                    b.click();
                    switchedTabOnce = true;
                    break;
                }
            }
        }
    }

    // Cercar i clicar la quota del partit actual
    function attemptSelectCurrentLeg() {
        if (hasSelectedCurrent) return;

        const targetOdd = parseFloat(currentLeg.odd || currentLeg.bookie_odd);
        const oddStrDot = targetOdd.toFixed(2);
        const oddStrComma = targetOdd.toFixed(2).replace('.', ',');
        const targetName = (currentLeg.selection || currentLeg.selection_name || '').toLowerCase();

        const buttons = Array.from(document.querySelectorAll('button, div[role="button"]'));
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
                if ((targetName.includes('+0.5') || targetName.includes('marca')) && (text.includes('+0.5') || text.includes('sí') || text.includes('si') || text.includes('marca'))) { matchedBtn = btn; break; }
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
                        statusEl.innerHTML = '🎉 <span style="color: #34d399;">COMBINADA COMPLETADA! Cupó llest!</span>';
                    }
                    const btnNext = document.getElementById('btn-next-leg');
                    if (btnNext) {
                        btnNext.style.background = '#10b981';
                        btnNext.textContent = '✅ Fet! Introdueix l\'import';
                    }
                } else {
                    if (statusEl) {
                        statusEl.innerHTML = `✅ <span style="color: #34d399;">Afegida! Passant al partit ${currentLegIdx + 2} en 1.5s...</span>`;
                    }
                    setTimeout(goToNextLeg, 1500);
                }
            } catch (err) {
                console.error("[Prediccions Lliga] Error clicant:", err);
            }
        } else {
            // Si no l'ha trobada encara, intentar obrir pestanya de mercat
            trySwitchToMarketTab(false);
            const statusEl = document.getElementById('dock-status-msg');
            if (statusEl && !hasSelectedCurrent) {
                statusEl.innerHTML = `⚠️ <span style="color: #cbd5e1;">Clica la casella al partit o prem 'Següent'</span>`;
            }
        }
    }

    // Escolta de clics manuals: si l'usuari clica la quota manualment, el script l'ajuda i avança
    document.addEventListener('click', (e) => {
        if (hasSelectedCurrent) return;
        const target = e.target.closest('button, div[role="button"]');
        if (!target || target.id === 'btn-next-leg' || target.id === 'btn-switch-tab' || target.id === 'close-dock-btn') return;

        const text = (target.innerText || target.textContent || '').trim();
        const targetOdd = parseFloat(currentLeg.odd || currentLeg.bookie_odd);
        if (text.includes(targetOdd.toFixed(2)) || text.includes(targetOdd.toFixed(2).replace('.', ','))) {
            hasSelectedCurrent = true;
            const statusEl = document.getElementById('dock-status-msg');
            if (statusEl) {
                statusEl.innerHTML = `✅ <span style="color: #34d399;">Seleccionat! Passant al següent partit...</span>`;
            }
            if (!isLastLeg) {
                setTimeout(goToNextLeg, 1200);
            }
        }
    });

    // Cicle d'execució lleuger i segur: SENSE cap MutationObserver per evitar bucles infinits
    setTimeout(createOverlayWidget, 400);

    // 4 intents espaiats (0% de consum de CPU)
    setTimeout(attemptSelectCurrentLeg, 1000);
    setTimeout(attemptSelectCurrentLeg, 2200);
    setTimeout(attemptSelectCurrentLeg, 3500);
    setTimeout(attemptSelectCurrentLeg, 5000);

})();
