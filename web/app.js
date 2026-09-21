/**
 * web/app.js
 * ==========
 * Lògica dinàmica de l'aplicació web de Prediccions de Futbol.
 * Carrega 'data/data.json', gestiona pestanyes, filtres de lligues,
 * cerques en temps real i renderitzat reactiu de totes les seccions:
 * - Power Rànquings Elo
 * - Prediccions de Jornada
 * - Apostes de Valor (+EV)
 * - Combinades Recomanades (6 per Lliga)
 * - Recompte de Diners (Simulador Financer)
 */

let appData = null;
let currentTab = 'rankings';
let currentLeague = 'ALL';
let currentSearch = '';
let activeModalComboKey = null;
window.combosRegistry = {};

document.addEventListener('DOMContentLoaded', () => {
  initEventListeners();
  loadData();
});

function initEventListeners() {
  // Navigation Tabs
  const tabButtons = document.querySelectorAll('.nav-tab');
  tabButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      const tabId = btn.getAttribute('data-tab');
      switchTab(tabId);
    });
  });

  // League Selector Pills
  const leagueButtons = document.querySelectorAll('.pill-btn');
  leagueButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      leagueButtons.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentLeague = btn.getAttribute('data-league');
      applyFilters();
    });
  });

  // Search Box
  const searchInput = document.getElementById('global-search');
  if (searchInput) {
    searchInput.addEventListener('input', (e) => {
      currentSearch = e.target.value.toLowerCase().trim();
      applyFilters();
    });
  }

  // Quick Assistant Modal Listeners
  const modalClose = document.getElementById('qa-modal-close');
  if (modalClose) {
    modalClose.addEventListener('click', closeQuickAssistant);
  }
  const modalBackdrop = document.getElementById('modal-quick-assistant');
  if (modalBackdrop) {
    modalBackdrop.addEventListener('click', (e) => {
      if (e.target === modalBackdrop) {
        closeQuickAssistant();
      }
    });
  }
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      closeQuickAssistant();
    }
  });

  const btnCopy = document.getElementById('qa-btn-copy');
  if (btnCopy) {
    btnCopy.addEventListener('click', copyModalComboSummary);
  }
  const btnOpenAll = document.getElementById('qa-btn-open-all');
  if (btnOpenAll) {
    btnOpenAll.addEventListener('click', openModalAllMatches);
  }
  const btnAutoPc = document.getElementById('qa-btn-auto-pc');
  if (btnAutoPc) {
    btnAutoPc.addEventListener('click', () => {
      if (activeModalComboKey) {
        openWinamaxAutofill(activeModalComboKey);
      }
    });
  }
}

function switchTab(tabId) {
  currentTab = tabId;

  // Actualitzar botons
  document.querySelectorAll('.nav-tab').forEach(b => {
    const isTarget = b.getAttribute('data-tab') === tabId;
    b.classList.toggle('active', isTarget);
    b.setAttribute('aria-selected', isTarget ? 'true' : 'false');
  });

  // Actualitzar seccions
  document.querySelectorAll('.tab-content').forEach(sec => {
    sec.classList.remove('active');
  });

  const activeSec = document.getElementById(`section-${tabId}`);
  if (activeSec) {
    activeSec.classList.add('active');
  }

  window.scrollTo({ top: 0, behavior: 'smooth' });
}

async function loadData() {
  try {
    const res = await fetch('./data/data.json');
    if (!res.ok) {
      throw new Error(`HTTP error ${res.status}`);
    }
    appData = await res.json();
    renderAll();
  } catch (err) {
    console.error("Error carregant data.json:", err);
    document.getElementById('last-updated-badge').textContent = "Avís: Carregant dades...";
  }
}

function renderAll() {
  if (!appData) return;

  renderHeaderMeta();
  renderPowerRankings();
  renderPredictions();
  renderValueBets();
  renderCombos();
  renderBankroll();
  renderChangelog();
}

function applyFilters() {
  renderPowerRankings();
  renderPredictions();
  renderValueBets();
  renderCombos();
}

// -----------------------------------------------------------------------------
// METADADES I CAPÇALERA
// -----------------------------------------------------------------------------
function renderHeaderMeta() {
  const badge = document.getElementById('last-updated-badge');
  if (!badge || !appData.metadata) return;

  const fmt = appData.metadata.formatted_date || "Actualitzat avui";
  badge.textContent = `Actualitzat: ${fmt}`;
}

// -----------------------------------------------------------------------------
// 1. POWER RÀNQUINGS ELO
// -----------------------------------------------------------------------------
function renderPowerRankings() {
  const container = document.getElementById('rankings-tables-container');
  if (!container || !appData.power_rankings) return;

  const comps = appData.metadata.competitions || [];
  let html = '';

  comps.forEach(c => {
    if (currentLeague !== 'ALL' && currentLeague !== c.id) return;

    let teams = appData.power_rankings[c.id] || [];
    if (currentSearch) {
      teams = teams.filter(t => t.name.toLowerCase().includes(currentSearch));
    }

    if (teams.length === 0) return;

    html += `
      <div class="league-ranking-block">
        <div class="league-block-header">
          <div class="league-name-heading">
            <span>${c.flag || '⚽'}</span>
            <span>${c.name}</span>
            <span style="font-size: 13px; color: var(--text-muted); font-weight: normal;">(Jornada ${c.active_jornada})</span>
          </div>
          <span style="font-size: 12px; color: var(--text-secondary); font-family: var(--font-mono);">${teams.length} Equips</span>
        </div>

        <div class="table-responsive">
          <table class="elo-table">
            <thead>
              <tr>
                <th style="width: 45px;">#</th>
                <th>Equip</th>
                <th>Elo Rating</th>
                <th>Rànq. Atac</th>
                <th>Rànq. Def</th>
                <th>PJ</th>
                <th>PG</th>
                <th>PE</th>
                <th>PP</th>
                <th>GF:GC</th>
                <th>DG</th>
                <th>Punts</th>
                <th>Forma (Últims 5)</th>
              </tr>
            </thead>
            <tbody>
              ${teams.map((t, idx) => {
                const isTop = idx < 4;
                const formPills = (t.form || []).map(f => {
                  let cls = 'form-d';
                  if (f === 'W') cls = 'form-w';
                  else if (f === 'L') cls = 'form-l';
                  return `<span class="form-pill ${cls}">${f}</span>`;
                }).join('');

                const gdCls = t.gd > 0 ? 'color: var(--accent-emerald); font-weight: bold;' : (t.gd < 0 ? 'color: var(--accent-rose);' : '');

                return `
                  <tr>
                    <td class="rank-cell ${isTop ? 'rank-top' : ''}">${t.rank}</td>
                    <td class="team-cell">
                      <span>${t.name}</span>
                    </td>
                    <td>
                      <span class="elo-badge">${t.elo_rating.toFixed(1)}</span>
                    </td>
                    <td style="font-family: var(--font-mono);">${t.off_rank.toFixed(2)}</td>
                    <td style="font-family: var(--font-mono);">${t.def_rank.toFixed(2)}</td>
                    <td style="font-family: var(--font-mono);">${t.played}</td>
                    <td style="font-family: var(--font-mono); color: var(--accent-emerald);">${t.won}</td>
                    <td style="font-family: var(--font-mono); color: var(--accent-amber);">${t.drawn}</td>
                    <td style="font-family: var(--font-mono); color: var(--accent-rose);">${t.lost}</td>
                    <td style="font-family: var(--font-mono); font-size: 12px;">${t.gf}:${t.ga}</td>
                    <td style="font-family: var(--font-mono); ${gdCls}">${t.gd > 0 ? '+' : ''}${t.gd}</td>
                    <td style="font-family: var(--font-mono); font-weight: 800; font-size: 14px; color: #ffffff;">${t.points}</td>
                    <td><div class="form-pill-group">${formPills}</div></td>
                  </tr>
                `;
              }).join('')}
            </tbody>
          </table>
        </div>
      </div>
    `;
  });

  container.innerHTML = html || `<p style="text-align: center; padding: 40px; color: var(--text-muted);">No s'han trobat equips coincidents amb la cerca.</p>`;
}

// -----------------------------------------------------------------------------
// 2. PREDICCIONS DE JORNADA
// -----------------------------------------------------------------------------
function renderPredictions() {
  const grid = document.getElementById('matches-grid');
  if (!grid || !appData.predictions) return;

  let allMatches = [];
  const comps = appData.metadata.competitions || [];

  comps.forEach(c => {
    if (currentLeague !== 'ALL' && currentLeague !== c.id) return;
    const pData = appData.predictions[c.id];
    if (pData && pData.matches) {
      pData.matches.forEach(m => {
        m._compName = c.name;
        m._compFlag = c.flag;
        allMatches.push(m);
      });
    }
  });

  if (currentSearch) {
    allMatches = allMatches.filter(m => 
      m.home_team.name.toLowerCase().includes(currentSearch) ||
      m.away_team.name.toLowerCase().includes(currentSearch)
    );
  }

  if (allMatches.length === 0) {
    grid.innerHTML = `<p style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--text-muted);">No hi ha partits que coincideixin amb el filtre seleccionat.</p>`;
    return;
  }

  grid.innerHTML = allMatches.map(m => {
    const o1 = m.odds_1x2 ? (m.odds_1x2['1'] || '-') : '-';
    const ox = m.odds_1x2 ? (m.odds_1x2['X'] || '-') : '-';
    const o2 = m.odds_1x2 ? (m.odds_1x2['2'] || '-') : '-';

    const p1 = m.prob_1 || 33.3;
    const px = m.prob_x || 33.3;
    const p2 = m.prob_2 || 33.4;

    const ref = m.referee || { name: 'Pendent Oficial', yellow_avg: 4.2, fouls_avg: 24.5 };
    const refBadgeCls = ref.is_official ? 'color: #c084fc;' : 'color: var(--text-muted);';

    return `
      <div class="match-card">
        <div class="match-card-header">
          <span class="match-league-tag">${m._compFlag || '⚽'} ${m._compName} · J${m.jornada}</span>
          <span>📅 ${m.date || 'Pendent'}</span>
        </div>

        <div class="matchup-row">
          <div class="team-box">
            <div class="team-name">${m.home_team.name}</div>
            <div class="team-elo-sub">Elo: ${m.home_team.elo}</div>
          </div>
          <div class="vs-badge">VS</div>
          <div class="team-box away">
            <div class="team-name">${m.away_team.name}</div>
            <div class="team-elo-sub">Elo: ${m.away_team.elo}</div>
          </div>
        </div>

        <!-- 1X2 Probabilities Bar -->
        <div class="prob-bar-container">
          <div class="prob-labels-row">
            <span class="prob-1">1: ${p1}% (@${o1})</span>
            <span class="prob-x">X: ${px}% (@${ox})</span>
            <span class="prob-2">2: ${p2}% (@${o2})</span>
          </div>
          <div class="prob-bar">
            <div class="bar-segment-1" style="width: ${p1}%;"></div>
            <div class="bar-segment-x" style="width: ${px}%;"></div>
            <div class="bar-segment-2" style="width: ${p2}%;"></div>
          </div>
        </div>

        <!-- Advanced Metrics Grid -->
        <div class="match-stats-grid">
          <div>
            <div class="stat-item-label">xG Esperat</div>
            <div class="stat-item-val" style="color: var(--accent-cyan);">${m.xg_home} - ${m.xg_away}</div>
          </div>
          <div>
            <div class="stat-item-label">Marcador Top</div>
            <div class="stat-item-val">${m.most_likely_score}</div>
          </div>
          <div>
            <div class="stat-item-label">+2.5 Gols</div>
            <div class="stat-item-val" style="color: ${m.prob_over_25 > 50 ? 'var(--accent-emerald)' : 'var(--accent-rose)'};">${m.prob_over_25}%</div>
          </div>
        </div>

        <!-- Verdict & Referee -->
        <div class="verdict-pill">
          <span>🎯 Pronòstic: <strong>${m.verdict}</strong></span>
        </div>

        <div class="referee-tag">
          <span style="${refBadgeCls}">⚖️ ${ref.name}</span>
          <span style="font-family: var(--font-mono);">(${ref.yellow_avg} 🟨/p · ${ref.fouls_avg} faltes)</span>
        </div>
      </div>
    `;
  }).join('');
}

// -----------------------------------------------------------------------------
// 3. APOSTES DE VALOR (+EV%)
// -----------------------------------------------------------------------------
function renderValueBets() {
  const container = document.getElementById('value-bets-container');
  if (!container || !appData.value_bets) return;

  let bets = appData.value_bets || [];
  if (currentLeague !== 'ALL') {
    bets = bets.filter(b => b.competition_id === currentLeague);
  }

  if (currentSearch) {
    bets = bets.filter(b => 
      b.matchup.toLowerCase().includes(currentSearch) ||
      b.selection.toLowerCase().includes(currentSearch)
    );
  }

  if (bets.length === 0) {
    container.innerHTML = `<p style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--text-muted);">No hi ha apostes de valor (+EV) per al filtre seleccionat.</p>`;
    return;
  }

  container.innerHTML = bets.map(b => {
    const edge = b.edge_pct != null ? b.edge_pct.toFixed(1) : '0.0';
    const kelly = b.kelly_stake != null ? b.kelly_stake.toFixed(1) : '2.5';
    const bookie = b.bookie_odd != null ? b.bookie_odd.toFixed(2) : '-';
    const fair = b.fair_odd != null ? b.fair_odd.toFixed(2) : '-';

    return `
      <div class="value-card">
        <div class="value-edge-glow">+${edge}% EV</div>
        <div class="value-matchup">${b.matchup}</div>
        <div class="value-selection">👉 ${b.selection}</div>

        <div class="value-metrics-row">
          <div>
            <div class="stat-item-label">Cuota Winamax</div>
            <div class="stat-item-val" style="color: #ffffff;">@${bookie}</div>
          </div>
          <div>
            <div class="stat-item-label">Prob. Model</div>
            <div class="stat-item-val" style="color: var(--accent-cyan); font-family: var(--font-mono);">${b.model_prob != null ? b.model_prob.toFixed(1) : '-'}%</div>
          </div>
          <div>
            <div class="stat-item-label">Cuota Justa</div>
            <div class="stat-item-val" style="color: var(--text-muted);">@${fair}</div>
          </div>
          <div>
            <div class="stat-item-label">Stake Kelly</div>
            <div class="stat-item-val" style="color: var(--accent-emerald);">${kelly}%</div>
          </div>
        </div>

        <a href="${b.winamax_url || 'https://www.winamax.es'}" target="_blank" rel="noopener noreferrer" class="btn-winamax">
          Apostar a Winamax España ↗
        </a>
      </div>
    `;
  }).join('');
}

// -----------------------------------------------------------------------------
// 4. COMBINADES RECOMANADES (6 PER LLIGA + MULTI)
// -----------------------------------------------------------------------------
function renderCombos() {
  const container = document.getElementById('combos-container');
  if (!container || !appData.combos) return;

  window.combosRegistry = {};
  const leagueKeys = ['LALIGA', 'PREMIER', 'HYPERMOTION', 'MULTI'];
  let html = '';

  leagueKeys.forEach(lKey => {
    if (currentLeague !== 'ALL' && currentLeague !== lKey) return;

    const leagueCombos = appData.combos[lKey];
    if (!leagueCombos) return;

    const allCards = [
      ...(leagueCombos.safe || []).map(c => ({ ...c, type: 'safe' })),
      ...(leagueCombos.semi || []).map(c => ({ ...c, type: 'semi' })),
      ...(leagueCombos.risky || []).map(c => ({ ...c, type: 'risky' }))
    ];

    if (allCards.length === 0) return;

    let leagueTitle = lKey;
    if (lKey === 'LALIGA') leagueTitle = '🇪🇸 LaLiga EA Sports';
    else if (lKey === 'PREMIER') leagueTitle = '🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premier League';
    else if (lKey === 'HYPERMOTION') leagueTitle = '🇪🇸 LaLiga Hypermotion';
    else if (lKey === 'MULTI') leagueTitle = '🌍 Mega-Combinades Multi-Lliga';

    html += `
      <div class="combos-league-wrapper">
        <div class="league-block-header" style="background: transparent; padding: 0 0 16px 0;">
          <h2 class="league-name-heading" style="font-size: 20px;">${leagueTitle} · Suite de 6 Combinades</h2>
          <span style="font-size: 13px; color: var(--accent-emerald); font-family: var(--font-mono);">2 Segures · 2 Semi · 2 Arriscades</span>
        </div>

        <div class="combos-grid">
          ${allCards.map((c, cardIdx) => {
            const comboKey = `${lKey}_${c.type}_${cardIdx}`;
            window.combosRegistry[comboKey] = {
              ...c,
              leagueKey: lKey,
              leagueTitle: leagueTitle
            };

            let oddCls = 'odd-safe';
            if (c.type === 'semi') oddCls = 'odd-semi';
            else if (c.type === 'risky') oddCls = 'odd-risky';

            let statusTag = '<span class="status-pending-tag" style="font-size: 10px; padding: 2px 6px;">⏳ EN CURS</span>';
            if (c.status === 'WON') {
              statusTag = '<span class="status-won-tag" style="font-size: 10px; padding: 2px 6px;">🏆 GUANYADA</span>';
            } else if (c.status === 'LOST') {
              statusTag = '<span class="status-lost-tag" style="font-size: 10px; padding: 2px 6px;">❌ FALLADA</span>';
            }

            const boosterPct = c.booster_pct || 0;
            const boostedOdd = c.boosted_odd || c.combined_odd;
            const hasBooster = boosterPct > 0;
            const boosterBadge = hasBooster ? `<span class="booster-pill">🚀 +${boosterPct}% Booster</span>` : '';
            const probConjunta = c.combined_prob_pct != null ? c.combined_prob_pct.toFixed(1) : '-';

            return `
              <div class="combo-card ${c.type}">
                <div class="combo-header">
                  <div>
                    <div class="combo-title">${c.profile}</div>
                    <div style="display: flex; gap: 6px; align-items: center; margin-top: 5px; flex-wrap: wrap;">
                      ${statusTag}
                      ${boosterBadge}
                      <span class="combo-joint-prob-pill" title="Probabilitat conjunta estimada pel model">
                        🎯 Prob. Conjunta: <strong>${probConjunta}%</strong>
                      </span>
                    </div>
                  </div>
                  <div style="text-align: right;">
                    <div class="combo-odd-pill ${oddCls}">@ ${boostedOdd.toFixed(2)}</div>
                    ${hasBooster ? `<div style="font-size: 10px; color: var(--text-muted); margin-top: 3px; font-family: var(--font-mono);">base @${c.combined_odd.toFixed(2)}</div>` : ''}
                  </div>
                </div>

                <ul class="combo-legs-list">
                  ${(c.legs || []).map(l => {
                    let legBadge = '<span class="leg-pill pill-pending">⏳ Pendent</span>';
                    if (l.status === 'WON') {
                      legBadge = `<span class="leg-pill pill-won">✅ ${l.actual_result || 'Encertat'}</span>`;
                    } else if (l.status === 'LOST') {
                      legBadge = `<span class="leg-pill pill-lost">❌ ${l.actual_result || 'Fallat'}</span>`;
                    }

                    let catIcon = '🏷️';
                    const cat = l.category || '';
                    if (cat === 'Gols') catIcon = '⚽';
                    else if (cat === 'Córners') catIcon = '🚩';
                    else if (cat === 'Targetes') catIcon = '🟨';
                    else if (cat === 'BTTS') catIcon = '🤝';
                    else if (cat === 'Doble Oportunitat') catIcon = '🛡️';
                    else if (cat === '1X2') catIcon = '🎯';

                    const catTag = cat ? `<span class="leg-category-tag">${catIcon} ${cat}</span>` : '';
                    const modelProb = l.model_prob != null ? l.model_prob.toFixed(1) : '-';

                    return `
                      <li class="combo-leg-item">
                        <div class="leg-desc">
                          <div style="display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-bottom: 2px;">
                            <span class="leg-matchup">${l.matchup}</span>
                            ${catTag}
                          </div>
                          <div class="leg-name">${l.selection_name}</div>
                          <div style="margin-top: 4px;">${legBadge}</div>
                        </div>
                        <div class="leg-odds-box">
                          <div class="leg-odd" title="Cuota Winamax">@${l.bookie_odd.toFixed(2)}</div>
                          <div class="leg-prob-pill" title="Probabilitat del model per a aquesta selecció">
                            ${modelProb}%
                          </div>
                        </div>
                      </li>
                    `;
                  }).join('')}
                </ul>

                <div class="combo-footer">
                  <div class="stake-info">
                    Inversió: <strong>${c.stake.toFixed(2)} €</strong>
                  </div>
                  <div style="font-family: var(--font-mono); font-size: 11.5px; color: var(--accent-emerald);">
                    Prob. Conjunta: <strong>${probConjunta}%</strong>
                  </div>
                  <div class="payout-info">
                    Retorn: +${c.potential_payout.toFixed(2)} €
                  </div>
                </div>

                <div class="combo-actions-wrapper">
                  <div class="combo-btn-row">
                    <button type="button" class="btn-assistant" onclick="openQuickAssistant('${comboKey}')" title="Obre assistent interactiu pas a pas (Mòbil i PC)">
                      📲 Assistent Ràpid
                    </button>
                    <button type="button" class="btn-auto-pc" onclick="openWinamaxAutofill('${comboKey}')" title="Omple el cupó automàticament a Winamax amb l'script de Tampermonkey (PC)">
                      ⚡ 1-Clic Auto
                    </button>
                  </div>
                  <a href="${c.winamax_url || 'https://www.winamax.es'}" target="_blank" rel="noopener noreferrer" class="combo-direct-link">
                    Obrir lliga a Winamax ↗
                  </a>
                </div>
              </div>
            `;
          }).join('')}
        </div>
      </div>
    `;
  });

  container.innerHTML = html || `<p style="text-align: center; padding: 40px; color: var(--text-muted);">No hi ha combinades disponibles per al filtre actual.</p>`;
}

// -----------------------------------------------------------------------------
// 5. RECOMPTE DE DINERS ("QUÈ HAGUÉS PASSAT SI...")
// -----------------------------------------------------------------------------
function renderBankroll() {
  const bData = appData.bankroll_simulation;
  if (!bData) return;

  const kpis = bData.kpis || {};

  // KPI Grid
  const kpiGrid = document.getElementById('bankroll-kpi-grid');
  if (kpiGrid) {
    const isProfit = (kpis.net_pnl || 0) >= 0;
    const pnlSign = isProfit ? '+' : '';
    const roiSign = (kpis.roi_pct || 0) >= 0 ? '+' : '';

    kpiGrid.innerHTML = `
      <div class="kpi-card">
        <div class="kpi-icon">💳</div>
        <div class="kpi-label">Total Invertit</div>
        <div class="kpi-value">${(kpis.total_invested || 0).toFixed(2)} €</div>
        <div class="kpi-sub">${kpis.total_bets || 0} apostes concloses</div>
      </div>

      <div class="kpi-card">
        <div class="kpi-icon">💵</div>
        <div class="kpi-label">Retorn Brut</div>
        <div class="kpi-value">${(kpis.total_payout || 0).toFixed(2)} €</div>
        <div class="kpi-sub">Pagaments totals rebuts</div>
      </div>

      <div class="kpi-card highlight ${isProfit ? '' : 'loss'}">
        <div class="kpi-icon">📈</div>
        <div class="kpi-label">Balanç Net (PnL)</div>
        <div class="kpi-value ${isProfit ? 'positive' : 'negative'}">${pnlSign}${(kpis.net_pnl || 0).toFixed(2)} €</div>
        <div class="kpi-sub">Guany net real d'avui</div>
      </div>

      <div class="kpi-card">
        <div class="kpi-icon">🎯</div>
        <div class="kpi-label">Rendibilitat (ROI)</div>
        <div class="kpi-value ${isProfit ? 'positive' : 'negative'}">${roiSign}${(kpis.roi_pct || 0).toFixed(1)}%</div>
        <div class="kpi-sub">Retorn sobre capital</div>
      </div>

      <div class="kpi-card">
        <div class="kpi-icon">🏆</div>
        <div class="kpi-label">Taxa d'Encert</div>
        <div class="kpi-value" style="color: var(--accent-cyan);">${(kpis.win_rate_pct || 0).toFixed(1)}%</div>
        <div class="kpi-sub">${kpis.won_bets || 0}W / ${kpis.lost_bets || 0}L (${kpis.pending_bets || 0} pendents)</div>
      </div>
    `;
  }

  // Breakdown per Perfil
  const profilesGrid = document.getElementById('profiles-breakdown-grid');
  if (profilesGrid && bData.by_profile) {
    const profs = bData.by_profile;
    const cards = [
      { key: 'safe', label: 'Combinades Segures', icon: '🛡️', stake: '25.00 €', data: profs.safe },
      { key: 'semi', label: 'Combinades Semi-Arriscades', icon: '⚖️', stake: '10.00 €', data: profs.semi },
      { key: 'risky', label: 'Combinades Arriscades', icon: '🚀', stake: '5.00 €', data: profs.risky }
    ];

    profilesGrid.innerHTML = cards.map(c => {
      const d = c.data || {};
      const isProf = (d.net_pnl || 0) >= 0;
      return `
        <div class="profile-card">
          <div class="profile-card-header">
            <div class="profile-card-title">${c.icon} ${c.label}</div>
            <span class="profile-unit-badge">Unitat: ${c.stake}</span>
          </div>

          <div class="profile-stats-row">
            <div>
              <div class="stat-item-label">Invertit</div>
              <div class="stat-item-val">${(d.total_invested || 0).toFixed(2)} €</div>
            </div>
            <div>
              <div class="stat-item-label">Balanç Net</div>
              <div class="stat-item-val" style="color: ${isProf ? 'var(--accent-emerald)' : 'var(--accent-rose)'};">${isProf ? '+' : ''}${(d.net_pnl || 0).toFixed(2)} €</div>
            </div>
            <div>
              <div class="stat-item-label">Taxa Encert</div>
              <div class="stat-item-val" style="color: var(--accent-cyan);">${(d.win_rate_pct || 0).toFixed(1)}%</div>
            </div>
          </div>
        </div>
      `;
    }).join('');
  }

  // Historial Jornada a Jornada (Ledger)
  const ledgerList = document.getElementById('ledger-rounds-list');
  if (ledgerList) {
    const rounds = bData.rounds_history || [];
    if (rounds.length === 0) {
      ledgerList.innerHTML = `<p style="text-align: center; padding: 20px; color: var(--text-muted);">Encara no hi ha jornades històriques registrades al simulador.</p>`;
      return;
    }

    ledgerList.innerHTML = rounds.map((r, idx) => {
      const isProf = r.net_profit >= 0;
      return `
        <div class="ledger-round-card">
          <div class="ledger-round-header" onclick="toggleLedgerDetails(${idx})">
            <div class="ledger-round-title">
              <span>⚽</span>
              <span>${r.competition_id} · Jornada ${r.jornada}</span>
            </div>
            <div class="ledger-round-metrics">
              <span>Apostat: <strong>${r.total_stake.toFixed(2)} €</strong></span>
              <span style="color: ${isProf ? 'var(--accent-emerald)' : 'var(--accent-rose)'};">
                Balanç: <strong>${isProf ? '+' : ''}${r.net_profit.toFixed(2)} €</strong>
              </span>
              <span class="${r.status_summary === 'WON' ? 'status-won-tag' : 'status-lost-tag'}">${r.status_summary}</span>
              <span style="font-size: 11px; color: var(--text-muted);">▼</span>
            </div>
          </div>

          <div class="ledger-round-details" id="ledger-details-${idx}">
            <div style="display: flex; flex-direction: column; gap: 12px; margin-top: 8px;">
              ${(r.combos || []).map(cb => {
                let badge = '<span class="status-pending-tag">PENDENT</span>';
                if (cb.status === 'WON') badge = '<span class="status-won-tag">GUANYADA (+' + cb.profit.toFixed(2) + ' €)</span>';
                else if (cb.status === 'LOST') badge = '<span class="status-lost-tag">PERDUDA (-' + cb.stake.toFixed(2) + ' €)</span>';

                return `
                  <div style="background: rgba(255,255,255,0.02); border: 1px solid var(--border-glass); border-radius: 8px; padding: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                      <strong>${cb.profile}</strong>
                      <div>Cuota: @${cb.combined_odd.toFixed(2)} · Stake: ${cb.stake.toFixed(2)} € · ${badge}</div>
                    </div>
                    <ul style="list-style: none; padding-left: 0; font-size: 12px; color: var(--text-secondary);">
                      ${(cb.legs || []).map(l => `
                        <li style="padding: 4px 0; border-bottom: 1px solid rgba(255,255,255,0.03); display: flex; justify-content: space-between;">
                          <span>${l.matchup}: <strong>${l.selection_name}</strong></span>
                          <span>${l.actual_result || 'Pendent'}</span>
                        </li>
                      `).join('')}
                    </ul>
                  </div>
                `;
              }).join('')}
            </div>
          </div>
        </div>
      `;
    }).join('');
  }
}

// Toggle helper per a l'historial
window.toggleLedgerDetails = function(idx) {
  const el = document.getElementById(`ledger-details-${idx}`);
  if (el) {
    el.classList.toggle('open');
  }
};

// -----------------------------------------------------------------------------
// 6. NOVETATS I CANVIS DIARIS DEL MODEL (CHANGELOG FEED)
// -----------------------------------------------------------------------------
function renderChangelog() {
  const container = document.getElementById('changelog-feed');
  if (!container) return;

  const entries = appData.changelog || [];
  if (entries.length === 0) {
    container.innerHTML = `<p style="text-align: center; padding: 40px; color: var(--text-muted);">No hi ha novetats registrades.</p>`;
    return;
  }

  const html = `
    <div class="timeline-container">
      ${entries.map((entry, idx) => {
        let dateFormatted = entry.date;
        try {
          if (entry.date) {
            const parts = entry.date.split('-');
            if (parts.length === 3) {
              dateFormatted = `${parts[2]}/${parts[1]}/${parts[0]}`;
            }
          }
        } catch (e) {}

        const badgeLabel = entry.badge || 'Actualització';
        const badgeClass = entry.badge_type === 'primary' ? 'timeline-badge-primary' : 'timeline-badge-info';

        return `
          <div class="timeline-entry ${idx === 0 ? 'latest-entry' : ''}">
            <div class="timeline-marker">
              <span class="timeline-dot"></span>
            </div>
            <div class="timeline-content">
              <div class="timeline-header">
                <div class="timeline-title-row">
                  <span class="timeline-badge ${badgeClass}">${badgeLabel}</span>
                  <h3 class="timeline-title">${entry.title}</h3>
                </div>
                <div class="timeline-date">📅 ${dateFormatted}</div>
              </div>
              <ul class="timeline-items-list">
                ${(entry.items || []).map(item => `
                  <li class="timeline-item">
                    <span class="timeline-item-icon">✓</span>
                    <span class="timeline-item-text">${item}</span>
                  </li>
                `).join('')}
              </ul>
            </div>
          </div>
        `;
      }).join('')}
    </div>
  `;

  container.innerHTML = html;
}

// -----------------------------------------------------------------------------
// 7. ASSISTENT RÀPID DE CUPÓ WINAMAX (MÒBIL & PC) & 1-CLIC AUTO
// -----------------------------------------------------------------------------
function openQuickAssistant(comboKey) {
  const combo = window.combosRegistry[comboKey];
  if (!combo) return;

  activeModalComboKey = comboKey;
  const modal = document.getElementById('modal-quick-assistant');
  if (!modal) return;

  const titleEl = document.getElementById('qa-modal-title');
  const subEl = document.getElementById('qa-modal-subtitle');
  const sumEl = document.getElementById('qa-modal-summary');
  const legsEl = document.getElementById('qa-modal-legs-list');

  const boosterPct = combo.booster_pct || 0;
  const boostedOdd = combo.boosted_odd || combo.combined_odd;
  const probConjunta = combo.combined_prob_pct != null ? combo.combined_prob_pct.toFixed(1) : '-';
  const legsCount = (combo.legs || []).length;

  if (titleEl) titleEl.textContent = combo.profile;
  if (subEl) subEl.textContent = `${combo.leagueTitle || 'Lliga'} · Assistent Interactiu de Selecció`;

  if (sumEl) {
    sumEl.innerHTML = `
      <div class="qa-sum-col">
        <span class="qa-sum-lbl">Cuota Total</span>
        <span class="qa-sum-val" style="color: var(--accent-emerald);">@${boostedOdd.toFixed(2)}</span>
        ${boosterPct > 0 ? `<span class="qa-sum-sub">🚀 +${boosterPct}% Booster inclòs</span>` : ''}
      </div>
      <div class="qa-sum-col">
        <span class="qa-sum-lbl">Probabilitat Model</span>
        <span class="qa-sum-val" style="color: var(--accent-cyan);">${probConjunta}%</span>
        <span class="qa-sum-sub">Estimació conjunta</span>
      </div>
      <div class="qa-sum-col">
        <span class="qa-sum-lbl">Progrés Cupó</span>
        <span class="qa-sum-val" id="qa-progress-text" style="color: var(--accent-amber);">0 / ${legsCount}</span>
        <span class="qa-sum-sub">Seleccions afegides</span>
      </div>
      <div class="qa-sum-col">
        <span class="qa-sum-lbl">Inversió Recomanada</span>
        <span class="qa-sum-val">${combo.stake.toFixed(2)} €</span>
        <span class="qa-sum-sub">Retorn: +${combo.potential_payout.toFixed(2)} €</span>
      </div>
    `;
  }

  if (legsEl) {
    legsEl.innerHTML = (combo.legs || []).map((leg, idx) => {
      let catIcon = '🏷️';
      const cat = leg.category || '';
      if (cat === 'Gols') catIcon = '⚽';
      else if (cat === 'Córners') catIcon = '🚩';
      else if (cat === 'Targetes') catIcon = '🟨';
      else if (cat === 'BTTS') catIcon = '🤝';
      else if (cat === 'Doble Oportunitat') catIcon = '🛡️';
      else if (cat === '1X2') catIcon = '🎯';

      const legOdd = leg.bookie_odd != null ? leg.bookie_odd.toFixed(2) : '-';
      const modelProb = leg.model_prob != null ? leg.model_prob.toFixed(1) : '-';
      const legUrl = leg.url || combo.winamax_url || 'https://www.winamax.es';

      return `
        <div class="qa-leg-card" id="qa-leg-card-${idx}">
          <div class="qa-leg-top">
            <div class="qa-leg-matchup">
              <span class="qa-leg-num">#${idx + 1}</span>
              <strong>${leg.matchup}</strong>
              <span class="leg-category-tag" style="margin-left: 6px;">${catIcon} ${cat}</span>
            </div>
            <div class="qa-leg-meta">
              <span class="qa-leg-odd">@${legOdd}</span>
              <span class="qa-leg-prob">${modelProb}% model</span>
            </div>
          </div>

          <div class="qa-selection-instruction">
            <div class="qa-instruction-label">👉 Casella exacta a marcar a Winamax:</div>
            <div class="qa-instruction-value">
              <strong>${leg.selection_name}</strong>
            </div>
          </div>

          <div class="qa-leg-bottom">
            <a href="${legUrl}" target="_blank" rel="noopener noreferrer" class="btn-leg-open" onclick="handleLegClick(${idx})">
              ⚽ Obrir Partit (Web / App) ↗
            </a>
            <label class="qa-check-label" for="qa-check-${idx}">
              <input type="checkbox" class="qa-leg-checkbox" id="qa-check-${idx}" onchange="toggleLegChecked(${idx})">
              <span>Afegit al cupó</span>
            </label>
          </div>
        </div>
      `;
    }).join('');
  }

  modal.classList.add('active');
  modal.setAttribute('aria-hidden', 'false');
  document.body.style.overflow = 'hidden';
}

function closeQuickAssistant() {
  const modal = document.getElementById('modal-quick-assistant');
  if (modal) {
    modal.classList.remove('active');
    modal.setAttribute('aria-hidden', 'true');
  }
  document.body.style.overflow = '';
}

function handleLegClick(idx) {
  // Quan l'usuari clica per obrir el partit a Winamax, marquem automàticament la casella
  const checkbox = document.getElementById(`qa-check-${idx}`);
  if (checkbox && !checkbox.checked) {
    checkbox.checked = true;
    toggleLegChecked(idx);
  }
}

function toggleLegChecked(idx) {
  const card = document.getElementById(`qa-leg-card-${idx}`);
  const checkbox = document.getElementById(`qa-check-${idx}`);
  if (card && checkbox) {
    card.classList.toggle('is-checked', checkbox.checked);
  }
  updateProgressText();
}

function updateProgressText() {
  const checkboxes = document.querySelectorAll('.qa-leg-checkbox');
  const checked = Array.from(checkboxes).filter(cb => cb.checked).length;
  const total = checkboxes.length;
  const progressText = document.getElementById('qa-progress-text');
  if (progressText) {
    progressText.textContent = `${checked} / ${total}`;
    if (checked === total && total > 0) {
      progressText.style.color = 'var(--accent-emerald)';
    } else {
      progressText.style.color = 'var(--accent-amber)';
    }
  }
}

function copyModalComboSummary() {
  if (!activeModalComboKey) return;
  const combo = window.combosRegistry[activeModalComboKey];
  if (!combo) return;

  const boosterPct = combo.booster_pct || 0;
  const boostedOdd = combo.boosted_odd || combo.combined_odd;
  const probConjunta = combo.combined_prob_pct != null ? combo.combined_prob_pct.toFixed(1) : '-';

  let text = `⚽ PREDICCIONS FUTBOL · ${combo.profile}\n`;
  text += `Lliga: ${combo.leagueTitle || ''}\n`;
  text += `Cuota Total: @${boostedOdd.toFixed(2)}${boosterPct > 0 ? ` (+${boosterPct}% Booster)` : ''}\n`;
  text += `Probabilitat Conjunta Model: ${probConjunta}%\n`;
  text += `Inversió Recomanada: ${combo.stake.toFixed(2)} € (Retorn: +${combo.potential_payout.toFixed(2)} €)\n\n`;
  text += `SELECCIONS DEL CUPÓ:\n`;

  (combo.legs || []).forEach((leg, i) => {
    text += `${i + 1}. [${leg.matchup}] 👉 ${leg.selection_name} (@${leg.bookie_odd != null ? leg.bookie_odd.toFixed(2) : '-'}) [Model: ${leg.model_prob}%]\n`;
    if (leg.url) {
      text += `   Enllaç: ${leg.url}\n`;
    }
  });

  navigator.clipboard.writeText(text).then(() => {
    const btn = document.getElementById('qa-btn-copy');
    if (btn) {
      const original = btn.innerHTML;
      btn.innerHTML = '✅ Copiat!';
      btn.style.borderColor = 'var(--accent-emerald)';
      btn.style.color = 'var(--accent-emerald)';
      setTimeout(() => {
        btn.innerHTML = original;
        btn.style.borderColor = '';
        btn.style.color = '';
      }, 2000);
    }
  }).catch(err => {
    console.error('Error al copiar al porta-retalls:', err);
    alert('No s\'ha pogut copiar automàticament. Pots seleccionar el text manualment.');
  });
}

function openModalAllMatches() {
  if (!activeModalComboKey) return;
  const combo = window.combosRegistry[activeModalComboKey];
  if (!combo || !combo.legs) return;

  combo.legs.forEach((leg, idx) => {
    if (leg.url) {
      setTimeout(() => {
        window.open(leg.url, '_blank');
      }, idx * 180);
    }
  });
}

function openWinamaxAutofill(comboKey) {
  const combo = window.combosRegistry[comboKey];
  if (!combo) return;

  const payload = {
    profile: combo.profile,
    odd: (combo.boosted_odd || combo.combined_odd || 0).toFixed(2),
    boosted_odd: combo.boosted_odd || combo.combined_odd,
    booster_pct: combo.booster_pct || 0,
    legs: (combo.legs || []).map(l => ({
      matchup: l.matchup,
      selection: l.selection_name,
      selection_name: l.selection_name,
      odd: (l.bookie_odd != null) ? l.bookie_odd.toFixed(2) : '',
      bookie_odd: l.bookie_odd,
      model_prob: l.model_prob,
      url: l.url
    }))
  };

  const hash = encodeURIComponent(JSON.stringify(payload));
  const initialUrl = (combo.legs && combo.legs.length > 0 && combo.legs[0].url) 
    ? combo.legs[0].url 
    : (combo.winamax_url || 'https://www.winamax.es/apuestas-deportivas');

  window.open(`${initialUrl}#combo_autofill=${hash}`, '_blank');
}

// Exportar globals per als controladors en línia
window.openQuickAssistant = openQuickAssistant;
window.closeQuickAssistant = closeQuickAssistant;
window.handleLegClick = handleLegClick;
window.toggleLegChecked = toggleLegChecked;
window.copyModalComboSummary = copyModalComboSummary;
window.openModalAllMatches = openModalAllMatches;
window.openWinamaxAutofill = openWinamaxAutofill;
