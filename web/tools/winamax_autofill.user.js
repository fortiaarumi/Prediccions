// ==UserScript==
// @name         Prediccions Lliga - Winamax Auto Betslip (1-Clic)
// @namespace    https://github.com/fortiaarumi/Prediccions
// @version      1.1.0
// @description  Afegeix automàticament les combinades de Prediccions Lliga al cupó de Winamax en 1 sol clic.
// @author       Prediccions Lliga
// @match        https://*.winamax.es/*
// @match        https://*.winamax.fr/*
// @icon         https://www.winamax.es/favicon.ico
// @grant        none
// @run-at       document-idle
// ==/UserScript==

(function() {
    'use strict';

    console.log("[Prediccions Lliga] Winamax Auto-Betslip inicialitzat v1.1.0.");

    function parseComboPayload() {
        const fullUrl = window.location.href;
        if (!fullUrl.includes('combo_autofill=')) {
            return null;
        }

        try {
            const raw = fullUrl.split('combo_autofill=')[1].split('&')[0].split('#')[0];
            const decoded = decodeURIComponent(raw);
            return JSON.parse(decoded);
        } catch (e) {
            console.error("[Prediccions Lliga] Error analitzant el payload de la combinada:", e);
            return null;
        }
    }

    const comboData = parseComboPayload();
    if (!comboData || !comboData.legs || comboData.legs.length === 0) {
        return;
    }

    console.log("[Prediccions Lliga] Combinada detectada per auto-omplir:", comboData);

    // Guardar la combinada a sessionStorage per si Winamax navega o refresca
    try {
        sessionStorage.setItem('prediccions_active_combo', JSON.stringify(comboData));
    } catch (e) {}

    // Crear widget flotant a la interfície de Winamax
    function createOverlayWidget() {
        if (document.getElementById('prediccions-winamax-dock')) return;

        const dock = document.createElement('div');
        dock.id = 'prediccions-winamax-dock';
        dock.style.cssText = `
            position: fixed;
            bottom: 24px;
            right: 24px;
            z-index: 9999999;
            background: linear-gradient(135deg, #0f172a, #1e293b);
            border: 2px solid #10b981;
            border-radius: 12px;
            box-shadow: 0 12px 35px rgba(0,0,0,0.85);
            color: #ffffff;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            padding: 16px;
            width: 340px;
            font-size: 13px;
        `;

        dock.innerHTML = `
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                <div style="font-weight: 800; font-size: 14px; color: #34d399; display: flex; align-items: center; gap: 6px;">
                    <span>⚡ Prediccions Lliga (1-Clic)</span>
                </div>
                <button id="close-dock-btn" style="background: none; border: none; color: #94a3b8; cursor: pointer; font-size: 16px; padding: 2px 6px;">✕</button>
            </div>
            <div style="font-size: 12px; color: #cbd5e1; margin-bottom: 12px; line-height: 1.4;">
                Combinada: <strong style="color: #ffffff;">${comboData.profile || 'Recomanada'}</strong>
                <span style="display: inline-block; background: #059669; color: #ffffff; padding: 1px 6px; border-radius: 4px; font-weight: 700; margin-left: 4px;">@${comboData.odd || comboData.boosted_odd || '-'}</span>
            </div>
            <div id="dock-legs-container" style="display: flex; flex-direction: column; gap: 8px; max-height: 240px; overflow-y: auto; margin-bottom: 14px;">
                ${comboData.legs.map((leg, idx) => `
                    <div class="dock-leg-item" id="dock-leg-${idx}" style="background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.12); border-radius: 6px; padding: 8px 10px; display: flex; justify-content: space-between; align-items: center;">
                        <div style="flex: 1; overflow: hidden; margin-right: 8px;">
                            <div style="font-size: 10px; color: #94a3b8; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${leg.matchup}</div>
                            <div style="font-size: 12px; font-weight: 600; color: #f8fafc; margin-top: 1px;">${leg.selection || leg.selection_name}</div>
                        </div>
                        <div style="text-align: right; flex-shrink: 0;">
                            <span style="font-size: 12px; font-weight: 800; color: #a5b4fc; font-family: monospace;">@${leg.odd || leg.bookie_odd}</span>
                            <div class="dock-status" style="font-size: 9.5px; color: #f59e0b; margin-top: 2px;">⏳ Buscant...</div>
                        </div>
                    </div>
                `).join('')}
            </div>
            <div style="display: flex; gap: 8px;">
                <button id="btn-reapply" style="flex: 1.2; background: #059669; color: #ffffff; border: none; border-radius: 6px; padding: 8px; font-weight: 700; cursor: pointer; font-size: 12px;">
                    🔄 Reintentar Clics
                </button>
                <button id="btn-open-matches" style="flex: 1; background: #3b82f6; color: #ffffff; border: none; border-radius: 6px; padding: 8px; font-weight: 700; cursor: pointer; font-size: 12px;">
                    📑 Obrir Restants
                </button>
            </div>
        `;

        document.body.appendChild(dock);

        document.getElementById('close-dock-btn').onclick = () => dock.remove();
        document.getElementById('btn-reapply').onclick = () => attemptAutoSelect();
        document.getElementById('btn-open-matches').onclick = () => {
            comboData.legs.forEach(leg => {
                const targetUrl = leg.url;
                if (targetUrl && !window.location.href.includes(targetUrl.split('/').pop())) {
                    window.open(targetUrl, '_blank');
                }
            });
        };
    }

    // Algoritme intel·ligent de cerca i selecció de quotes
    function attemptAutoSelect() {
        console.log("[Prediccions Lliga] Cercant botons de quotes al DOM de Winamax...");
        const buttons = Array.from(document.querySelectorAll('button, div[role="button"], a[role="button"]'));
        if (buttons.length === 0) return;

        comboData.legs.forEach((leg, idx) => {
            const statusEl = document.querySelector(`#dock-leg-${idx} .dock-status`);
            const targetOdd = parseFloat(leg.odd || leg.bookie_odd);
            const oddStr1 = targetOdd.toFixed(2);
            const oddStr2 = targetOdd.toFixed(2).replace('.', ',');
            const targetName = (leg.selection || leg.selection_name || '').toLowerCase();

            let matchedBtn = null;

            for (const btn of buttons) {
                const text = (btn.innerText || btn.textContent || '').trim().toLowerCase();
                if (!text) continue;

                // Comprova si el botó conté la cuota (ex: "1.10" o "1,10")
                const hasOdd = text.includes(oddStr1) || text.includes(oddStr2);
                if (hasOdd) {
                    // Matcher heurístic per tipus de selecció
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
                    if (targetName.includes('-2.5') && text.includes('-2.5')) { matchedBtn = btn; break; }
                    if (targetName.includes('btts') || targetName.includes('ambdós')) {
                        if (text.includes('sí') || text.includes('si') || text.includes('ambos')) { matchedBtn = btn; break; }
                    }
                    if (!matchedBtn) matchedBtn = btn;
                }
            }

            if (matchedBtn) {
                try {
                    matchedBtn.click();
                    matchedBtn.style.outline = '3px solid #10b981';
                    if (statusEl) {
                        statusEl.innerHTML = '✅ Afegida!';
                        statusEl.style.color = '#10b981';
                    }
                } catch (err) {
                    console.error("[Prediccions Lliga] Error clicant botó:", err);
                }
            } else {
                if (statusEl && statusEl.innerHTML !== '✅ Afegida!') {
                    const currentMatchId = window.location.pathname.split('/').pop();
                    const legMatchId = (leg.url || '').split('/').pop();
                    if (legMatchId && currentMatchId !== legMatchId) {
                        statusEl.innerHTML = `<a href="${leg.url}" style="color: #60a5fa; text-decoration: underline;">Obrir partit ↗</a>`;
                    } else {
                        statusEl.innerHTML = '⏳ No trobada';
                        statusEl.style.color = '#f59e0b';
                    }
                }
            }
        });
    }

    // Inicialització amb reintents progressius per a la SPA de Winamax
    setTimeout(createOverlayWidget, 500);
    setTimeout(attemptAutoSelect, 1200);
    setTimeout(attemptAutoSelect, 2500);
    setTimeout(attemptAutoSelect, 4000);

    // Observador de canvis al DOM per si es despleguen mercats
    const observer = new MutationObserver(() => {
        const hasPending = Array.from(document.querySelectorAll('.dock-status')).some(el => el.innerHTML.includes('⏳'));
        if (hasPending) {
            attemptAutoSelect();
        }
    });

    setTimeout(() => {
        observer.observe(document.body, { childList: true, subtree: true });
    }, 1500);

})();

