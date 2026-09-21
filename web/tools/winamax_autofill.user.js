// ==UserScript==
// @name         Prediccions Lliga - Winamax Auto Betslip (1-Clic)
// @namespace    https://github.com/fortiaarumi/Prediccions
// @version      1.0.0
// @description  Afegeix automàticament les combinades de Prediccions Lliga al cupó de Winamax en 1 sol clic.
// @author       Prediccions Lliga
// @match        https://www.winamax.es/apuestas-deportivas*
// @match        https://www.winamax.fr/paris-sportifs*
// @icon         https://www.winamax.es/favicon.ico
// @grant        none
// @run-at       document-idle
// ==/UserScript==

(function() {
    'use strict';

    console.log("[Prediccions Lliga] Winamax Auto-Betslip inicialitzat.");

    function parseComboPayload() {
        const hash = window.location.hash;
        if (!hash || !hash.includes('combo_autofill=')) {
            return null;
        }

        try {
            const raw = hash.split('combo_autofill=')[1].split('&')[0];
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

    console.log("[Prediccions Lliga] Combinada detectada:", comboData);

    // Crear widget flotant a la interfície de Winamax
    function createOverlayWidget() {
        const dock = document.createElement('div');
        dock.id = 'prediccions-winamax-dock';
        dock.style.cssText = `
            position: fixed;
            bottom: 24px;
            right: 24px;
            z-index: 999999;
            background: linear-gradient(135deg, #0f172a, #1e293b);
            border: 2px solid #10b981;
            border-radius: 12px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.8);
            color: #ffffff;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            padding: 16px;
            width: 320px;
            font-size: 13px;
        `;

        dock.innerHTML = `
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                <div style="font-weight: 800; font-size: 14px; color: #34d399; display: flex; align-items: center; gap: 6px;">
                    <span>⚡ Prediccions Lliga</span>
                </div>
                <button id="close-dock-btn" style="background: none; border: none; color: #94a3b8; cursor: pointer; font-size: 16px;">✕</button>
            </div>
            <div style="font-size: 12px; color: #cbd5e1; margin-bottom: 12px;">
                Combinada: <strong>${comboData.profile || 'Recomanada'}</strong> (@${comboData.odd || '-'})
            </div>
            <div id="dock-legs-container" style="display: flex; flex-direction: column; gap: 6px; max-height: 220px; overflow-y: auto; margin-bottom: 12px;">
                ${comboData.legs.map((leg, idx) => `
                    <div class="dock-leg-item" id="dock-leg-${idx}" style="background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); border-radius: 6px; padding: 6px 8px; display: flex; justify-content: space-between; align-items: center;">
                        <div style="flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-right: 6px;">
                            <div style="font-size: 10px; color: #94a3b8;">${leg.matchup}</div>
                            <div style="font-size: 11.5px; font-weight: 600; color: #f8fafc;">${leg.selection}</div>
                        </div>
                        <div style="text-align: right;">
                            <span style="font-size: 11px; font-weight: 700; color: #a5b4fc;">@${leg.odd}</span>
                            <div class="dock-status" style="font-size: 9px; color: #f59e0b;">⏳ Clicant...</div>
                        </div>
                    </div>
                `).join('')}
            </div>
            <div style="display: flex; gap: 8px;">
                <button id="btn-reapply" style="flex: 1; background: #059669; color: #ffffff; border: none; border-radius: 6px; padding: 8px; font-weight: 700; cursor: pointer; font-size: 12px;">
                    🔄 Reintentar Seleccions
                </button>
                <button id="btn-open-matches" style="flex: 1; background: #3b82f6; color: #ffffff; border: none; border-radius: 6px; padding: 8px; font-weight: 700; cursor: pointer; font-size: 12px;">
                    📑 Obrir Partits
                </button>
            </div>
        `;

        document.body.appendChild(dock);

        document.getElementById('close-dock-btn').onclick = () => dock.remove();
        document.getElementById('btn-reapply').onclick = () => attemptAutoSelect();
        document.getElementById('btn-open-matches').onclick = () => {
            comboData.legs.forEach(leg => {
                if (leg.url && leg.url.includes('/match/')) {
                    window.open(leg.url, '_blank');
                }
            });
        };
    }

    // Algoritme intel·ligent de detecció i clic de cuotes al DOM de Winamax
    function attemptAutoSelect() {
        console.log("[Prediccions Lliga] Intentant seleccionar les quotes al DOM de Winamax...");
        const buttons = Array.from(document.querySelectorAll('button, div[role="button"]'));

        comboData.legs.forEach((leg, idx) => {
            const statusEl = document.querySelector(`#dock-leg-${idx} .dock-status`);
            const targetOdd = parseFloat(leg.odd);
            const targetName = (leg.selection || '').toLowerCase();

            // Buscar botons amb la cuota exacta o text de selecció
            let foundBtn = null;
            for (const btn of buttons) {
                const text = btn.innerText.toLowerCase();
                // Comprovació per cuota
                if (text.includes(targetOdd.toFixed(2)) || text.includes(targetOdd.toString())) {
                    if (targetName.includes('1x') && text.includes('1x')) { foundBtn = btn; break; }
                    if (targetName.includes('x2') && text.includes('x2')) { foundBtn = btn; break; }
                    if (targetName.includes('12') && text.includes('12')) { foundBtn = btn; break; }
                    if ((targetName.includes('local') || targetName.startsWith('1')) && text.includes('1')) { foundBtn = btn; break; }
                    if ((targetName.includes('visitant') || targetName.startsWith('2')) && text.includes('2')) { foundBtn = btn; break; }
                    if (targetName.includes('empat') && (text.includes('x') || text.includes('empate'))) { foundBtn = btn; break; }
                    if (targetName.includes('més d\'1.5') || targetName.includes('+1.5')) { foundBtn = btn; break; }
                    if (targetName.includes('més de 2.5') || targetName.includes('+2.5')) { foundBtn = btn; break; }
                    if (targetName.includes('menys de 3.5') || targetName.includes('-3.5')) { foundBtn = btn; break; }
                    if (targetName.includes('btts') || targetName.includes('ambdós')) { foundBtn = btn; break; }
                    if (!foundBtn) foundBtn = btn;
                }
            }

            if (foundBtn) {
                foundBtn.click();
                if (statusEl) {
                    statusEl.innerHTML = '✅ Afegida!';
                    statusEl.style.color = '#10b981';
                }
            } else {
                if (statusEl) {
                    statusEl.innerHTML = '⚠️ Cal obrir el partit';
                    statusEl.style.color = '#f59e0b';
                }
            }
        });
    }

    // Esperar que la SPA de Winamax s'hagi renderitzat
    setTimeout(() => {
        createOverlayWidget();
        setTimeout(attemptAutoSelect, 1500);
    }, 1200);

})();
