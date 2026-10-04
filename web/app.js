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
let currentTab = 'novetats';
let currentLeague = 'ALL';
let currentSearch = '';
let currentMatchFilter = 'ALL';
let activeModalComboKey = null;
window.combosRegistry = {};

// Memòria persistent resilient amb IndexedDB (resistent a neteja de memòria cau bàsica)
const IDB_NAME = 'PrediccionsResilientDB';
const IDB_STORE = 'app_data';

function openIDB() {
  return new Promise((resolve) => {
    if (!window.indexedDB) return resolve(null);
    try {
      const req = indexedDB.open(IDB_NAME, 1);
      req.onupgradeneeded = (e) => {
        const db = e.target.result;
        if (!db.objectStoreNames.contains(IDB_STORE)) {
          db.createObjectStore(IDB_STORE);
        }
      };
      req.onsuccess = (e) => resolve(e.target.result);
      req.onerror = () => resolve(null);
    } catch (e) {
      resolve(null);
    }
  });
}

async function idbGet(key) {
  const db = await openIDB();
  if (!db) return null;
  return new Promise((resolve) => {
    try {
      const tx = db.transaction(IDB_STORE, 'readonly');
      const req = tx.objectStore(IDB_STORE).get(key);
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => resolve(null);
    } catch (e) {
      resolve(null);
    }
  });
}

async function idbSet(key, val) {
  const db = await openIDB();
  if (!db) return;
  try {
    const tx = db.transaction(IDB_STORE, 'readwrite');
    tx.objectStore(IDB_STORE).put(val, key);
  } catch (e) {}
}

let selectedJornadaPred = {};
let selectedJornadaCombo = {};

window.onJornadaChange = function(type, val) {
  const jNum = val === 'ACTIVE' ? 'ACTIVE' : parseInt(val, 10);
  if (type === 'predictions') {
    selectedJornadaPred[currentLeague] = jNum;
    UserAuth.saveCurrentPreference(`jornada_pred_${currentLeague}`, jNum);
    renderPredictions();
  } else if (type === 'combos') {
    selectedJornadaCombo[currentLeague] = jNum;
    UserAuth.saveCurrentPreference(`jornada_combo_${currentLeague}`, jNum);
    renderCombos();
  }
};

// Memòria cau local resilient per a combinades (evita que desapareguin durant canvis de jornada o càrrega)
window._cachedCombos = {};
try {
  const sc = localStorage.getItem('prediccions_valid_combos');
  if (sc) window._cachedCombos = JSON.parse(sc);
} catch (e) {}

window.setMatchesFilter = function(status) {
  currentMatchFilter = status;
  UserAuth.saveCurrentPreference('matchFilter', status);
  document.querySelectorAll('.match-filter-pill').forEach(btn => {
    btn.classList.toggle('active', btn.getAttribute('data-match-filter') === status);
  });
  renderPredictions();
};

document.addEventListener('DOMContentLoaded', async () => {
  await UserAuth.init();
  initEventListeners();
  loadData();
});

function initEventListeners() {
  // Navigation Tabs (Top)
  const tabButtons = document.querySelectorAll('.nav-tab');
  tabButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      const tabId = btn.getAttribute('data-tab');
      switchTab(tabId);
    });
  });

  // Mobile Bottom Navigation Dock
  const mobileNavBtns = document.querySelectorAll('.mobile-nav-btn');
  mobileNavBtns.forEach(btn => {
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
      UserAuth.saveCurrentPreference('favoriteLeague', currentLeague);
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
      closeAuthModal();
      closeAddBetModal();
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

  // Auth Modal Listeners
  const btnAuthClose = document.getElementById('auth-modal-close');
  if (btnAuthClose) btnAuthClose.onclick = closeAuthModal;
  const modalAuth = document.getElementById('modal-auth');
  if (modalAuth) {
    modalAuth.onclick = (e) => { if (e.target === modalAuth) closeAuthModal(); };
  }
  const btnEnterGuest = document.getElementById('btn-enter-guest');
  if (btnEnterGuest) {
    btnEnterGuest.onclick = () => {
      UserAuth.loginAsGuest();
      closeAuthModal();
    };
  }
  const authForm = document.getElementById('auth-form');
  if (authForm) {
    authForm.onsubmit = (e) => {
      e.preventDefault();
      const u = document.getElementById('auth-username').value;
      const p = document.getElementById('auth-password').value;
      const errEl = document.getElementById('auth-error-msg');
      try {
        UserAuth.login(u, p);
        closeAuthModal();
      } catch (err) {
        if (errEl) {
          errEl.textContent = err.message;
          errEl.style.display = 'block';
        }
      }
    };
  }

  // Add Bet Modal Listeners
  const btnOpenAddBet = document.getElementById('btn-open-add-bet');
  if (btnOpenAddBet) btnOpenAddBet.onclick = () => openAddBetModal();
  const btnAddBetClose = document.getElementById('add-bet-modal-close');
  if (btnAddBetClose) btnAddBetClose.onclick = closeAddBetModal;
  const btnCancelAddBet = document.getElementById('btn-cancel-add-bet');
  if (btnCancelAddBet) btnCancelAddBet.onclick = closeAddBetModal;
  const modalAddBet = document.getElementById('modal-add-bet');
  if (modalAddBet) {
    modalAddBet.onclick = (e) => { if (e.target === modalAddBet) closeAddBetModal(); };
  }
  const addBetForm = document.getElementById('add-bet-form');
  if (addBetForm) {
    addBetForm.onsubmit = (e) => {
      e.preventDefault();
      const matchup = document.getElementById('bet-matchup').value;
      const comp = document.getElementById('bet-competition').value;
      const cat = document.getElementById('bet-category').value;
      const sel = document.getElementById('bet-selection').value;
      const odd = parseFloat(document.getElementById('bet-odd').value) || 2.0;
      const stake = parseFloat(document.getElementById('bet-stake').value) || 10.0;
      const status = document.getElementById('bet-status').value || 'PENDING';

      PersonalBets.add({
        matchup,
        competition_id: comp,
        category: cat,
        selection: sel,
        odd,
        stake,
        status
      });
      closeAddBetModal();
    };
  }

  // Clear Personal Bets
  const btnClearBets = document.getElementById('btn-clear-my-bets');
  if (btnClearBets) btnClearBets.onclick = () => PersonalBets.clearAll();

  // Status Filter Pills for Personal Bets
  const pFilterBtns = document.querySelectorAll('.personal-pill-btn');
  pFilterBtns.forEach(btn => {
    btn.onclick = () => {
      pFilterBtns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      PersonalBets.currentFilter = btn.getAttribute('data-status');
      PersonalBets.render();
    };
  });

  // Backup & Restore Account Listeners
  const btnExportAcc = document.getElementById('btn-export-account');
  if (btnExportAcc) btnExportAcc.onclick = () => UserAuth.exportAccountBackup();
  const inputImportAcc = document.getElementById('input-import-account');
  if (inputImportAcc) inputImportAcc.onchange = (e) => UserAuth.importAccountBackup(e);
}

function switchTab(tabId) {
  currentTab = tabId;
  UserAuth.saveCurrentPreference('preferredTab', tabId);

  // Actualitzar botons superiors
  document.querySelectorAll('.nav-tab').forEach(b => {
    const isTarget = b.getAttribute('data-tab') === tabId;
    b.classList.toggle('active', isTarget);
    b.setAttribute('aria-selected', isTarget ? 'true' : 'false');
  });

  // Actualitzar botons de la barra inferior mòbil
  document.querySelectorAll('.mobile-nav-btn').forEach(b => {
    b.classList.toggle('active', b.getAttribute('data-tab') === tabId);
  });

  // Actualitzar seccions
  document.querySelectorAll('.tab-content').forEach(sec => {
    sec.classList.remove('active');
  });

  const activeSec = document.getElementById(`section-${tabId}`);
  if (activeSec) {
    activeSec.classList.add('active');
  }

  if (tabId === 'bankroll') {
    PersonalBets.render();
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

    // Sincronitzar combinades amb la memòria cau resilient
    if (appData.combos) {
      window._cachedCombos = window._cachedCombos || {};
      for (const [k, v] of Object.entries(appData.combos)) {
        if ((v.safe && v.safe.length > 0) || (v.semi && v.semi.length > 0) || (v.risky && v.risky.length > 0)) {
          window._cachedCombos[k] = v;
        } else if (window._cachedCombos[k]) {
          appData.combos[k] = window._cachedCombos[k];
        }
      }
      try {
        localStorage.setItem('prediccions_valid_combos', JSON.stringify(window._cachedCombos));
      } catch (e) {}
    }

    renderAll();
  } catch (err) {
    console.error("Error carregant data.json:", err);
    document.getElementById('last-updated-badge').textContent = "Avís: Carregant dades...";
  }
}

function renderAll() {
  if (!appData) return;

  loadBankrollSimState();
  UserAuth.updateUI();
  PersonalBets.render();
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

  // Actualitzar Desplegable de Jornades
  const selPred = document.getElementById('select-jornada-pred');
  const badgePred = document.getElementById('badge-jornada-pred');

  if (selPred) {
    if (currentLeague === 'ALL') {
      selPred.innerHTML = `<option value="ACTIVE" selected>Jornada en Curs de Cada Lliga</option>`;
      if (badgePred) {
        badgePred.textContent = 'Multi-Lliga';
        badgePred.className = 'jornada-badge badge-active';
      }
    } else {
      const pData = appData.predictions[currentLeague];
      if (pData) {
        const activeJ = pData.active_jornada || pData.jornada || 8;
        const available = pData.available_jornadas || [activeJ];
        const maxJ = Math.max(...available, activeJ + 2);
        const minJ = 1;

        let targetJ = selectedJornadaPred[currentLeague];
        if (!targetJ || targetJ === 'ACTIVE') {
          targetJ = activeJ;
          selectedJornadaPred[currentLeague] = activeJ;
        }

        let optionsHtml = '';
        for (let j = minJ; j <= maxJ; j++) {
          let statusText = '';
          if (j < activeJ) statusText = ' (🏁 Finalitzada)';
          else if (j === activeJ) statusText = ' (🟢 En Curs)';
          else statusText = ' (⏳ Properament)';

          const isSelected = (j === targetJ) ? 'selected' : '';
          optionsHtml += `<option value="${j}" ${isSelected}>Jornada ${j}${statusText}</option>`;
        }
        selPred.innerHTML = optionsHtml;

        if (badgePred) {
          if (targetJ < activeJ) {
            badgePred.textContent = '🏁 Jornada Finalitzada';
            badgePred.className = 'jornada-badge badge-finished';
          } else if (targetJ === activeJ) {
            badgePred.textContent = '🟢 Jornada en Curs';
            badgePred.className = 'jornada-badge badge-active';
          } else {
            badgePred.textContent = '⏳ Properament';
            badgePred.className = 'jornada-badge badge-future';
          }
        }
      }
    }
  }

  let allMatches = [];
  const comps = appData.metadata.competitions || [];
  let isFutureSelected = false;
  let futureJornadaNum = 0;

  comps.forEach(c => {
    if (currentLeague !== 'ALL' && currentLeague !== c.id) return;
    const pData = appData.predictions[c.id];
    if (!pData) return;

    const activeJ = pData.active_jornada || pData.jornada || 8;
    const targetJ = (currentLeague === 'ALL') ? activeJ : (selectedJornadaPred[c.id] || activeJ);

    if (targetJ > activeJ) {
      isFutureSelected = true;
      futureJornadaNum = targetJ;
    }

    let jMatches = [];
    if (targetJ === activeJ && pData.matches && pData.matches.length > 0) {
      jMatches = pData.matches;
    } else if (pData.by_jornada && pData.by_jornada[String(targetJ)]) {
      jMatches = pData.by_jornada[String(targetJ)];
    }

    jMatches.forEach(m => {
      m._compName = c.name;
      m._compFlag = c.flag;
      m._targetJornada = targetJ;
      allMatches.push(m);
    });
  });

  if (currentSearch) {
    allMatches = allMatches.filter(m => 
      m.home_team.name.toLowerCase().includes(currentSearch) ||
      m.away_team.name.toLowerCase().includes(currentSearch)
    );
  }

  // Actualitzar comptadors a la barra de filtres
  const totalCount = allMatches.length;
  const finishedMatchesList = allMatches.filter(m => m.status === 'FINISHED');
  const pendingMatchesList = allMatches.filter(m => m.status !== 'FINISHED');

  const countAllEl = document.getElementById('count-matches-all');
  if (countAllEl) countAllEl.textContent = totalCount;
  const countPendEl = document.getElementById('count-matches-pending');
  if (countPendEl) countPendEl.textContent = pendingMatchesList.length;
  const countFinEl = document.getElementById('count-matches-finished');
  if (countFinEl) countFinEl.textContent = finishedMatchesList.length;

  // Filtrar per estat si s'ha seleccionat Pendents o Finalitzats
  if (currentMatchFilter === 'SCHEDULED') {
    allMatches = pendingMatchesList;
  } else if (currentMatchFilter === 'FINISHED') {
    allMatches = finishedMatchesList;
  }

  if (allMatches.length === 0) {
    if (isFutureSelected) {
      grid.innerHTML = `
        <div class="empty-jornada-state" style="grid-column: 1/-1; text-align: center; padding: 48px 20px; background: rgba(17, 24, 39, 0.5); border: 1px dashed rgba(255,255,255,0.12); border-radius: var(--radius-md);">
          <span style="font-size: 38px; display: block; margin-bottom: 12px;">⏳</span>
          <h3 style="font-size: 18px; color: var(--text-primary); margin-bottom: 8px;">Encara no hi ha partits disponibles per a la Jornada ${futureJornadaNum}</h3>
          <p style="color: var(--text-muted); max-width: 480px; margin: 0 auto; font-size: 13px;">
            Els calendaris, designacions arbitrals del CTA/PGMOL i les cuotes oficials de Winamax s'incorporaran automàticament tan bon punt la competició iniciï aquesta jornada.
          </p>
        </div>
      `;
    } else {
      grid.innerHTML = `<p style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--text-muted);">No hi ha partits que coincideixin amb el filtre seleccionat.</p>`;
    }
    return;
  }

  grid.innerHTML = allMatches.map(m => {
    const o1 = m.odds_1x2 ? (m.odds_1x2['1'] || '-') : '-';
    const ox = m.odds_1x2 ? (m.odds_1x2['X'] || '-') : '-';
    const o2 = m.odds_1x2 ? (m.odds_1x2['2'] || '-') : '-';

    const p1 = m.prob_1 || 33.3;
    const px = m.prob_x || 33.3;
    const p2 = m.prob_2 || 33.4;

    const ref = m.referee || { name: 'Pendent Oficial', yellow_avg: null, fouls_avg: null };
    const refBadgeCls = ref.is_official ? 'color: #c084fc;' : 'color: var(--text-muted);';

    // -------------------------------------------------------------------------
    // A) TARGETA PER A PARTITS JA JUGATS (FINISHED) AMB COMPARATIVA DE RESULTAT
    // -------------------------------------------------------------------------
    if (m.status === 'FINISHED') {
      const isHit = m.is_hit_1x2;
      const hitCls = isHit ? 'hit' : 'miss';
      const hitIcon = isHit ? '✅' : '❌';
      const hitText = isHit
        ? `Pronòstic encertat (${m.actual_1x2})`
        : `Pronòstic fallat (Predit: ${m.predicted_1x2 || '-'} · Real: ${m.actual_1x2 || '-'})`;

      const exactScoreBadge = m.is_exact_score
        ? `<span class="exact-hit-pill">✨ Marcador Exacte!</span>`
        : '';

      const xgHDiff = m.xg_diff_home != null ? `${m.xg_diff_home >= 0 ? '+' : ''}${m.xg_diff_home}` : '-';
      const xgADiff = m.xg_diff_away != null ? `${m.xg_diff_away >= 0 ? '+' : ''}${m.xg_diff_away}` : '-';

      return `
        <div class="match-card match-card-finished">
          <div class="match-card-header">
            <span class="match-league-tag">${m._compFlag || '⚽'} ${m._compName} · J${m.jornada}</span>
            <span class="status-finished-pill">🏁 FINALITZAT · ${m.date || ''}</span>
          </div>

          <div class="matchup-row matchup-finished-row">
            <div class="team-box">
              <div class="team-name">${m.home_team.name}</div>
              <div class="team-elo-sub">Elo: ${m.home_team.elo}</div>
            </div>
            
            <div class="final-score-box">
              <div class="final-score-val">${m.final_score || `${m.home_goals} - ${m.away_goals}`}</div>
              <span class="final-score-lbl">Marcador Final</span>
            </div>

            <div class="team-box away">
              <div class="team-name">${m.away_team.name}</div>
              <div class="team-elo-sub">Elo: ${m.away_team.elo}</div>
            </div>
          </div>

          <!-- Avaluació Pronòstic vs Realitat -->
          <div class="prediction-evaluation-card ${hitCls}">
            <div class="eval-header-line">
              <span class="eval-title">${hitIcon} <strong>${hitText}</strong></span>
              ${exactScoreBadge}
            </div>
            <div class="eval-body-line">
              Pronòstic inicial del model: <strong>${m.verdict}</strong>
            </div>
          </div>

          <!-- Diferències Marcador i xG -->
          <div class="match-stats-grid finished-grid">
            <div>
              <div class="stat-item-label">Marcador Predit</div>
              <div class="stat-item-val" style="color: var(--accent-cyan); font-size: 13.5px;">${m.most_likely_score || '-'}</div>
            </div>
            <div>
              <div class="stat-item-label">xG Model vs Real</div>
              <div class="stat-item-val" style="color: #ffffff; font-size: 12.5px;">
                ${m.xg_home} - ${m.xg_away} ➔ <strong style="color: var(--accent-emerald);">${m.home_goals}-${m.away_goals}</strong>
              </div>
              <div class="stat-item-sub">Dif xG: ${xgHDiff} / ${xgADiff}</div>
            </div>
            <div>
              <div class="stat-item-label">Prob. Model 1X2</div>
              <div class="stat-item-val" style="color: var(--text-muted); font-size: 11px; font-family: var(--font-mono);">
                1: ${p1}% · X: ${px}% · 2: ${p2}%
              </div>
            </div>
          </div>

          <!-- Mètriques reals vs predites (Córners, Targetes, BTTS) -->
          <div class="match-stats-grid secondary-stats-row finished-secondary-row">
            <div>
              <div class="stat-item-label">🚩 Córners Reals</div>
              <div class="stat-item-val" style="font-size: 12px; color: var(--accent-cyan);">
                ${m.real_corners_home != null ? `${m.real_corners_home + m.real_corners_away} totals` : `Predits ~${m.corners_total}`}
              </div>
            </div>
            <div>
              <div class="stat-item-label">⚽ Ambdós Marquen</div>
              <div class="stat-item-val" style="font-size: 12px; color: ${(m.home_goals > 0 && m.away_goals > 0) ? 'var(--accent-emerald)' : 'var(--text-muted)'};">
                ${(m.home_goals > 0 && m.away_goals > 0) ? 'Sí (Han marcat ambdós)' : 'No'}
              </div>
            </div>
            <div>
              <div class="stat-item-label">🟨 Targetes Reals</div>
              <div class="stat-item-val" style="font-size: 12px; color: var(--accent-amber);">
                ${m.real_cards_home != null ? `${m.real_cards_home + m.real_cards_away} totals` : `Predites ~${m.cards_total}`}
              </div>
            </div>
          </div>

          <div class="referee-tag">
            <span style="${refBadgeCls}">⚖️ ${ref.name}</span>
            <span style="font-family: var(--font-mono);">${ref.yellow_avg ? `(${ref.yellow_avg} 🟨 · ${ref.fouls_avg} faltes)` : ''}</span>
          </div>
        </div>
      `;
    }

    // -------------------------------------------------------------------------
    // B) TARGETA PER A PARTITS PENDENTS DE JUGAR (SCHEDULED)
    // -------------------------------------------------------------------------
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

        <!-- Advanced Metrics Grid (Fila 1: xG, Marcador Top, +2.5 Gols) -->
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

        <!-- Fila 2 (Sol·licitada): Córners esperats per equip, Ambdós Marquen BTTS, Targetes per equip +3.5 -->
        <div class="match-stats-grid secondary-stats-row">
          <div>
            <div class="stat-item-label">🚩 Córners (H - A)</div>
            <div class="stat-item-val" style="color: var(--accent-cyan); font-size: 13px;">
              ${m.corners_home != null ? m.corners_home : '5.0'} - ${m.corners_away != null ? m.corners_away : '4.5'}
              <span class="stat-item-sub">(${m.corners_total != null ? m.corners_total : '9.5'})</span>
            </div>
          </div>
          <div>
            <div class="stat-item-label">⚽ Ambdós Marquen (BTTS)</div>
            <div class="stat-item-val" style="color: ${(m.prob_btts_yes != null ? m.prob_btts_yes : 50) >= 50 ? 'var(--accent-emerald)' : 'var(--accent-amber)'}; font-size: 13px;">
              ${m.prob_btts_yes != null ? m.prob_btts_yes : '50.0'}%
            </div>
          </div>
          <div>
            <div class="stat-item-label">🟨 Targetes (H - A)</div>
            <div class="stat-item-val" style="color: var(--accent-amber); font-size: 13px;">
              ${m.cards_home != null ? m.cards_home : '2.5'} - ${m.cards_away != null ? m.cards_away : '2.5'}
              <span class="stat-item-sub">(+3.5: ${m.prob_over_cards_35 != null ? m.prob_over_cards_35 : '75.0'}%)</span>
            </div>
          </div>
        </div>

        <!-- Verdict & Referee -->
        <div class="verdict-pill">
          <span>🎯 Pronòstic: <strong>${m.verdict}</strong></span>
        </div>

        <div class="referee-tag">
          <span style="${refBadgeCls}">⚖️ ${ref.name}</span>
          <span style="font-family: var(--font-mono);">${ref.yellow_avg ? `(${ref.yellow_avg} 🟨/p · ${ref.fouls_avg || '-'} faltes)` : '<span style="color: var(--text-muted); font-size: 11px;">(Pendent de designació oficial)</span>'}</span>
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

        <div class="value-card-actions">
          <button class="btn-track-bet-card" onclick="quickTrackBetFromCard('${encodeURIComponent(JSON.stringify({
            matchup: b.matchup,
            selection: b.selection,
            odd: b.bookie_odd,
            stake: 10,
            category: 'Valor (+EV)',
            competition_id: b.competition_id
          }))}')">
            ➕ Afegir al meu compte
          </button>
          <a href="${b.winamax_url || 'https://www.winamax.es'}" target="_blank" rel="noopener noreferrer" class="btn-winamax">
            Apostar a Winamax España ↗
          </a>
        </div>
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

  loadBankrollSimState();

  const uSafe = bankrollSimState.globalUnits.safe;
  const uSemi = bankrollSimState.globalUnits.semi;
  const uRisky = bankrollSimState.globalUnits.risky;

  const safeFmt = uSafe % 1 === 0 ? `${uSafe}€` : `${uSafe.toFixed(2)}€`;
  const semiFmt = uSemi % 1 === 0 ? `${uSemi}€` : `${uSemi.toFixed(2)}€`;
  const riskyFmt = uRisky % 1 === 0 ? `${uRisky}€` : `${uRisky.toFixed(2)}€`;

  // Actualitzar dinàmicament el subtítol i el badge de la capçalera de combinades
  const subtitleEl = document.getElementById('combos-section-subtitle');
  if (subtitleEl) {
    subtitleEl.innerHTML = `Estratègia diversificada de bankroll: 2 Segures (${safeFmt} · Cuota 2-3.5), 2 Semi-Arriscades (${semiFmt} · Cuota ~10.0) i 2 Arriscades (${riskyFmt} · Cuota &ge;30.0).`;
  }
  const badgeEl = document.getElementById('combos-policy-badge');
  if (badgeEl) {
    badgeEl.innerHTML = `<span>Inversió per jornada: <strong>${safeFmt}</strong> Segura · <strong>${semiFmt}</strong> Semi · <strong>${riskyFmt}</strong> Arriscada</span>`;
  }

  // Actualitzar Desplegable de Jornades de Combinades
  const selCombo = document.getElementById('select-jornada-combo');
  const badgeCombo = document.getElementById('badge-jornada-combo');

  if (selCombo) {
    if (currentLeague === 'ALL') {
      selCombo.innerHTML = `<option value="ACTIVE" selected>Jornada en Curs de Cada Lliga</option>`;
      if (badgeCombo) {
        badgeCombo.textContent = 'Multi-Lliga';
        badgeCombo.className = 'jornada-badge badge-active';
      }
    } else {
      const cData = appData.combos[currentLeague];
      if (cData) {
        const activeJ = cData.active_jornada || (appData.predictions[currentLeague]?.active_jornada) || 8;
        const available = cData.available_jornadas || [activeJ];
        const maxJ = Math.max(...available, activeJ + 2);
        const minJ = 1;

        let targetJ = selectedJornadaCombo[currentLeague];
        if (!targetJ || targetJ === 'ACTIVE') {
          targetJ = activeJ;
          selectedJornadaCombo[currentLeague] = activeJ;
        }

        let optionsHtml = '';
        for (let j = minJ; j <= maxJ; j++) {
          let statusText = '';
          if (j < activeJ) statusText = ' (🏁 Finalitzada)';
          else if (j === activeJ) statusText = ' (🟢 En Curs)';
          else statusText = ' (⏳ Properament)';

          const isSelected = (j === targetJ) ? 'selected' : '';
          optionsHtml += `<option value="${j}" ${isSelected}>Jornada ${j}${statusText}</option>`;
        }
        selCombo.innerHTML = optionsHtml;

        if (badgeCombo) {
          if (targetJ < activeJ) {
            badgeCombo.textContent = '🏁 Jornada Finalitzada';
            badgeCombo.className = 'jornada-badge badge-finished';
          } else if (targetJ === activeJ) {
            badgeCombo.textContent = '🟢 Jornada en Curs';
            badgeCombo.className = 'jornada-badge badge-active';
          } else {
            badgeCombo.textContent = '⏳ Properament';
            badgeCombo.className = 'jornada-badge badge-future';
          }
        }
      }
    }
  }

  window.combosRegistry = {};
  const leagueKeys = ['LALIGA', 'PREMIER', 'HYPERMOTION', 'CHAMPIONSHIP', 'MULTI'];
  let html = '';
  let totalCombosFound = 0;
  let isFutureCombos = false;
  let futureComboJ = 0;

  leagueKeys.forEach(lKey => {
    if (currentLeague !== 'ALL' && currentLeague !== lKey) return;

    const cData = appData.combos[lKey];
    if (!cData) return;

    const activeJ = cData.active_jornada || (appData.predictions[lKey]?.active_jornada) || 8;
    const targetJ = (currentLeague === 'ALL') ? activeJ : (selectedJornadaCombo[lKey] || activeJ);

    if (targetJ > activeJ) {
      isFutureCombos = true;
      futureComboJ = targetJ;
    }

    let leagueCombos = null;
    if (targetJ === activeJ) {
      leagueCombos = cData;
      if (!leagueCombos || (!leagueCombos.safe?.length && !leagueCombos.semi?.length && !leagueCombos.risky?.length)) {
        if (window._cachedCombos && window._cachedCombos[lKey]) {
          leagueCombos = window._cachedCombos[lKey];
        }
      }
    } else if (cData.by_jornada && cData.by_jornada[String(targetJ)]) {
      leagueCombos = cData.by_jornada[String(targetJ)];
    }

    if (!leagueCombos) return;

    let allCards = [
      ...(leagueCombos.safe || []).map(c => ({ ...c, type: 'safe' })),
      ...(leagueCombos.semi || []).map(c => ({ ...c, type: 'semi' })),
      ...(leagueCombos.risky || []).map(c => ({ ...c, type: 'risky' }))
    ];

    if (allCards.length === 0 && targetJ === activeJ && window._cachedCombos && window._cachedCombos[lKey]) {
      const cached = window._cachedCombos[lKey];
      allCards = [
        ...(cached.safe || []).map(c => ({ ...c, type: 'safe' })),
        ...(cached.semi || []).map(c => ({ ...c, type: 'semi' })),
        ...(cached.risky || []).map(c => ({ ...c, type: 'risky' }))
      ];
    }

    if (allCards.length === 0) return;
    totalCombosFound += allCards.length;

    let leagueTitle = lKey;
    if (lKey === 'LALIGA') leagueTitle = '🇪🇸 LaLiga EA Sports';
    else if (lKey === 'PREMIER') leagueTitle = '🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premier League';
    else if (lKey === 'HYPERMOTION') leagueTitle = '🇪🇸 LaLiga Hypermotion';
    else if (lKey === 'CHAMPIONSHIP') leagueTitle = '🏴󠁧󠁢󠁥󠁮󠁧󠁿 EFL Championship';
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
            const effectiveStake = getComboEffectiveStake(c);
            const boostedOdd = c.boosted_odd || c.combined_odd || 1.0;
            const effectivePayout = effectiveStake * boostedOdd;

            window.combosRegistry[comboKey] = {
              ...c,
              stake: effectiveStake,
              potential_payout: effectivePayout,
              leagueKey: lKey,
              leagueTitle: leagueTitle
            };

            const legs = c.legs || [];
            const totalLegs = legs.length;
            const wonLegs = legs.filter(l => l.status === 'WON').length;
            const lostLegs = legs.filter(l => l.status === 'LOST').length;
            const pendingLegs = legs.filter(l => l.status === 'PENDING' || !l.status).length;

            let cardStatus = c.status || 'PENDING';
            if (lostLegs > 0) cardStatus = 'LOST';
            else if (totalLegs > 0 && wonLegs === totalLegs) cardStatus = 'WON';

            let oddCls = 'odd-safe';
            if (c.type === 'semi') oddCls = 'odd-semi';
            else if (c.type === 'risky') oddCls = 'odd-risky';

            let statusTag = '<span class="status-pending-tag" style="font-size: 10px; padding: 2px 6px;">⏳ EN CURS</span>';
            if (cardStatus === 'WON') {
              statusTag = '<span class="status-won-tag" style="font-size: 10px; padding: 2px 6px;">🏆 GUANYADA</span>';
            } else if (cardStatus === 'LOST') {
              statusTag = '<span class="status-lost-tag" style="font-size: 10px; padding: 2px 6px;">❌ FALLADA</span>';
            }

            let progressSummary = `${wonLegs}/${totalLegs} encertats`;
            if (pendingLegs > 0) {
              progressSummary += ` · ${pendingLegs} pendents`;
            }
            if (lostLegs > 0) {
              progressSummary += ` (${lostLegs} fallat${lostLegs > 1 ? 's' : ''})`;
            }

            const boosterPct = c.booster_pct || 0;
            const hasBooster = boosterPct > 0;
            const boosterBadge = hasBooster ? `<span class="booster-pill">🚀 +${boosterPct}% Booster</span>` : '';
            const probConjunta = c.combined_prob_pct != null ? c.combined_prob_pct.toFixed(1) : '-';

            const autofillUrl = getAutofillUrl(c);

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

                <!-- Progrés en directe de la combinada -->
                <div class="combo-progress-bar-container">
                  <div class="combo-progress-label-row">
                    <span class="combo-progress-title">Progrés en directe</span>
                    <span class="combo-progress-stats"><strong>${progressSummary}</strong></span>
                  </div>
                  <div class="combo-progress-bar">
                    ${legs.map((l, lIdx) => {
                      let segCls = 'seg-pending';
                      let segTitle = `Partit ${lIdx + 1} (${l.matchup}): Pendent`;
                      if (l.status === 'WON') {
                        segCls = 'seg-won';
                        segTitle = `Partit ${lIdx + 1} (${l.matchup}): Encertat! (${l.actual_result || ''})`;
                      } else if (l.status === 'LOST') {
                        segCls = 'seg-lost';
                        segTitle = `Partit ${lIdx + 1} (${l.matchup}): Fallat (${l.actual_result || ''})`;
                      }
                      return `<div class="combo-progress-segment ${segCls}" title="${segTitle}"></div>`;
                    }).join('')}
                  </div>
                </div>

                <ul class="combo-legs-list">
                  ${legs.map(l => {
                    let legBadge = `<span class="leg-pill pill-pending">⏳ Pendent (${l.date || 'Properament'})</span>`;
                    let legItemCls = 'leg-pending';
                    if (l.status === 'WON') {
                      legBadge = `<span class="leg-pill pill-won">✅ ${l.actual_result || 'Encertat'}</span>`;
                      legItemCls = 'leg-won';
                    } else if (l.status === 'LOST') {
                      legBadge = `<span class="leg-pill pill-lost">❌ ${l.actual_result || 'Fallat'}</span>`;
                      legItemCls = 'leg-lost';
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
                      <li class="combo-leg-item ${legItemCls}">
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
                    Inversió: <strong>${effectiveStake.toFixed(2)} €</strong>
                  </div>
                  <div style="font-family: var(--font-mono); font-size: 11.5px; color: ${cardStatus === 'LOST' ? 'var(--accent-rose)' : 'var(--accent-emerald)'};">
                    ${cardStatus === 'LOST' ? '❌ Fallada' : (cardStatus === 'WON' ? '🏆 Encertada!' : `⏳ En joc (${wonLegs}/${totalLegs})`)}
                  </div>
                  <div class="payout-info">
                    ${cardStatus === 'LOST' ? `<span style="color: var(--text-muted); text-decoration: line-through;">+${effectivePayout.toFixed(2)} €</span>` : `Retorn: +${effectivePayout.toFixed(2)} €`}
                  </div>
                </div>

                <div class="combo-actions-wrapper">
                  <div class="combo-btn-row">
                    <button type="button" class="btn-track-combo" onclick="trackComboBet('${comboKey}')" title="Afegir aquesta combinada al teu compte">
                      ➕ Apostar al meu compte
                    </button>
                    <button type="button" class="btn-assistant" onclick="openQuickAssistant('${comboKey}')" title="Obre assistent interactiu pas a pas (Mòbil i PC)">
                      📲 Assistent Ràpid
                    </button>
                    <a href="${autofillUrl}" target="_blank" rel="noopener noreferrer" class="btn-auto-pc" title="Obre Winamax i omple el cupó automàticament (Tampermonkey)">
                      ⚡ 1-Clic Auto
                    </a>
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

  if (totalCombosFound === 0) {
    if (isFutureCombos) {
      container.innerHTML = `
        <div class="empty-jornada-state" style="text-align: center; padding: 48px 20px; background: rgba(17, 24, 39, 0.5); border: 1px dashed rgba(255,255,255,0.12); border-radius: var(--radius-md); margin: 20px 0;">
          <span style="font-size: 38px; display: block; margin-bottom: 12px;">🎯</span>
          <h3 style="font-size: 18px; color: var(--text-primary); margin-bottom: 8px;">Combinades pendents de generar per a la Jornada ${futureComboJ}</h3>
          <p style="color: var(--text-muted); max-width: 480px; margin: 0 auto; font-size: 13px;">
            Les combinades es congelen un cop comença la jornada per garantir la màxima transparència. La nova suite de 6 combinades de la Jornada ${futureComboJ} es calcularà i congelarà automàticament abans de l'inici del primer partit amb les millors cuotes de Winamax.
          </p>
        </div>
      `;
    } else {
      container.innerHTML = `<p style="text-align: center; padding: 40px; color: var(--text-muted);">No s'han trobat combinades disponibles per al filtre actual.</p>`;
    }
  } else {
    container.innerHTML = html;
  }
}

// =============================================================================
// GESTIÓ D'USUARIS I AUTENTICACIÓ (UserAuth)
// =============================================================================
const UserAuth = {
  SESSION_KEY: 'prediccions_user_session',
  USERS_KEY: 'prediccions_user_accounts',

  async init() {
    try {
      let rawUsers = localStorage.getItem(this.USERS_KEY);
      if (!rawUsers) {
        const idbUsers = await idbGet(this.USERS_KEY);
        if (idbUsers) {
          localStorage.setItem(this.USERS_KEY, JSON.stringify(idbUsers));
          console.log("🛡️ Comptes restaurats des d'IndexedDB.");
        }
      }
      let rawSession = localStorage.getItem(this.SESSION_KEY);
      if (!rawSession) {
        const idbSession = await idbGet(this.SESSION_KEY);
        if (idbSession) {
          localStorage.setItem(this.SESSION_KEY, JSON.stringify(idbSession));
        }
      }
    } catch (e) {
      console.warn("Error inicialitzant emmagatzematge resilient:", e);
    }
    this.restoreUserPreferences();
    this.updateUI();
  },

  getCurrentUser() {
    try {
      const raw = localStorage.getItem(this.SESSION_KEY);
      if (raw) return JSON.parse(raw);
    } catch (e) {
      console.warn("Error llegint sessió:", e);
    }
    return {
      username: 'Convidat',
      isGuest: true,
      isAuthenticated: true,
      avatar: '👤'
    };
  },

  setCurrentUser(userObj) {
    try {
      localStorage.setItem(this.SESSION_KEY, JSON.stringify(userObj));
      idbSet(this.SESSION_KEY, userObj);
    } catch (e) {
      console.warn("Error desant sessió:", e);
    }
    this.restoreUserPreferences();
    this.updateUI();
    PersonalBets.render();
  },

  getAllUsers() {
    try {
      const raw = localStorage.getItem(this.USERS_KEY);
      return raw ? JSON.parse(raw) : {};
    } catch (e) {
      return {};
    }
  },

  saveUsers(users) {
    try {
      localStorage.setItem(this.USERS_KEY, JSON.stringify(users));
      idbSet(this.USERS_KEY, users);
    } catch (e) {}
  },

  saveCurrentPreference(key, value) {
    const user = this.getCurrentUser();
    if (!user || user.isGuest) return;
    const users = this.getAllUsers();
    if (!users[user.username]) users[user.username] = {};
    if (!users[user.username].preferences) users[user.username].preferences = {};
    users[user.username].preferences[key] = value;
    this.saveUsers(users);
  },

  restoreUserPreferences() {
    const user = this.getCurrentUser();
    if (!user || user.isGuest) return;
    const users = this.getAllUsers();
    const account = users[user.username];
    if (account && account.preferences) {
      const p = account.preferences;
      if (p.favoriteLeague) {
        currentLeague = p.favoriteLeague;
        document.querySelectorAll('.pill-btn').forEach(b => {
          b.classList.toggle('active', b.getAttribute('data-league') === currentLeague);
        });
      }
      if (p.matchFilter) {
        currentMatchFilter = p.matchFilter;
        document.querySelectorAll('.match-filter-pill').forEach(btn => {
          btn.classList.toggle('active', btn.getAttribute('data-match-filter') === currentMatchFilter);
        });
      }
      if (p.preferredTab && p.preferredTab !== currentTab) {
        setTimeout(() => switchTab(p.preferredTab), 60);
      }
    }
  },

  exportAccountBackup() {
    const users = this.getAllUsers();
    const currentUser = this.getCurrentUser();
    const allStorage = {};
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && k.startsWith('prediccions_')) {
        allStorage[k] = localStorage.getItem(k);
      }
    }
    const backupData = {
      app: 'Prediccions Futbol AI',
      exportedAt: new Date().toISOString(),
      currentUser: currentUser.username,
      users: users,
      storage: allStorage
    };

    const blob = new Blob([JSON.stringify(backupData, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `prediccions_compte_${currentUser.username}_${new Date().toISOString().slice(0, 10)}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    showToast("💾 Còpia de seguretat descarregada amb èxit!", "success");
  },

  importAccountBackup(event) {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = async (e) => {
      try {
        const data = JSON.parse(e.target.result);
        if (!data || !data.users) {
          throw new Error("El fitxer no té un format de còpia vàlid.");
        }
        this.saveUsers(data.users);
        if (data.storage) {
          for (const [k, v] of Object.entries(data.storage)) {
            localStorage.setItem(k, v);
            await idbSet(k, typeof v === 'string' ? JSON.parse(v) : v);
          }
        }
        if (data.currentUser && data.currentUser !== 'Convidat' && data.users[data.currentUser]) {
          this.setCurrentUser({
            username: data.currentUser,
            isGuest: false,
            isAuthenticated: true,
            avatar: data.currentUser[0].toUpperCase()
          });
        }
        showToast("📥 Compte i dades restaurades correctament!", "success");
        closeAuthModal();
        this.restoreUserPreferences();
        PersonalBets.render();
        applyFilters();
      } catch (err) {
        alert("Error en importar el fitxer: " + err.message);
      }
    };
    reader.readAsText(file);
  },

  login(username, password) {
    const cleanUser = (username || '').trim();
    if (!cleanUser) throw new Error("Has d'introduir un nom d'usuari.");
    if (!password || password.length < 3) throw new Error("La contrasenya ha de tenir com a mínim 3 caràcters.");

    const users = this.getAllUsers();
    if (!users[cleanUser]) {
      users[cleanUser] = {
        password: btoa(password),
        createdAt: new Date().toISOString(),
        preferences: {
          favoriteLeague: currentLeague,
          preferredTab: currentTab,
          matchFilter: currentMatchFilter
        },
        bets: []
      };
      this.saveUsers(users);
      showToast(`🎉 Compte creat amb èxit per a ${cleanUser}!`, 'success');
    } else {
      if (users[cleanUser].password !== btoa(password)) {
        throw new Error("Contrasenya incorrecta per a aquest usuari.");
      }
      showToast(`👋 Benvingut de nou, ${cleanUser}!`, 'success');
    }

    this.setCurrentUser({
      username: cleanUser,
      isGuest: false,
      isAuthenticated: true,
      avatar: cleanUser[0].toUpperCase()
    });
  },

  loginAsGuest() {
    this.setCurrentUser({
      username: 'Convidat',
      isGuest: true,
      isAuthenticated: true,
      avatar: '👤'
    });
    showToast("⚡ Has entrat en mode Convidat.", 'info');
  },

  logout() {
    this.setCurrentUser({
      username: 'Convidat',
      isGuest: true,
      isAuthenticated: true,
      avatar: '👤'
    });
    showToast("🚪 Sessió tancada. Ara ets en mode Convidat.", 'info');
  },

  updateUI() {
    const user = this.getCurrentUser();
    const widget = document.getElementById('user-auth-widget');
    const heroName = document.getElementById('hero-user-name');

    if (heroName) {
      heroName.textContent = user.isGuest ? "Convidat (Mode Local)" : `${user.username} (El Teu Compte)`;
    }

    if (widget) {
      if (user.isGuest) {
        widget.innerHTML = `
          <button class="btn-auth-header" id="btn-header-auth" title="Iniciar sessió o canviar usuari">
            <span class="auth-icon">👤</span>
            <span class="auth-name">Convidat</span>
            <span class="auth-action-lbl">Entrar</span>
          </button>
        `;
      } else {
        widget.innerHTML = `
          <div class="user-header-pill">
            <span class="user-avatar-mini">${user.avatar}</span>
            <span class="user-name-mini">${user.username}</span>
            <button class="btn-logout-mini" id="btn-header-logout" title="Tancar sessió">✕</button>
          </div>
        `;
      }

      const btnAuth = document.getElementById('btn-header-auth');
      if (btnAuth) btnAuth.onclick = openAuthModal;
      const btnLogout = document.getElementById('btn-header-logout');
      if (btnLogout) btnLogout.onclick = () => UserAuth.logout();
    }
  }
};

// =============================================================================
// GESTIÓ D'APOSTES PERSONALS (PersonalBets)
// =============================================================================
const PersonalBets = {
  currentFilter: 'ALL',

  getStorageKey() {
    const user = UserAuth.getCurrentUser();
    const uname = (user.username || 'guest').toLowerCase().replace(/[^a-z0-9]/g, '_');
    return `prediccions_bets_${uname}`;
  },

  getAll() {
    try {
      const raw = localStorage.getItem(this.getStorageKey());
      if (raw) return JSON.parse(raw);
    } catch (e) {
      console.warn("Error carregant apostes:", e);
    }
    // Fallback: comprovar directament al perfil de l'usuari
    const user = UserAuth.getCurrentUser();
    if (!user.isGuest) {
      const users = UserAuth.getAllUsers();
      if (users[user.username] && Array.isArray(users[user.username].bets) && users[user.username].bets.length > 0) {
        return users[user.username].bets;
      }
    }
    return [];
  },

  saveAll(bets) {
    try {
      localStorage.setItem(this.getStorageKey(), JSON.stringify(bets));
      idbSet(this.getStorageKey(), bets);
      // Sincronitzar directament dins de l'objecte de compte
      const user = UserAuth.getCurrentUser();
      if (!user.isGuest) {
        const users = UserAuth.getAllUsers();
        if (users[user.username]) {
          users[user.username].bets = bets;
          UserAuth.saveUsers(users);
        }
      }
    } catch (e) {
      console.warn("Error desant apostes:", e);
    }
    this.render();
  },

  syncWithLiveData() {
    try {
      const raw = localStorage.getItem(this.getStorageKey());
      if (!raw) return;
      let bets = JSON.parse(raw);
      if (!Array.isArray(bets) || bets.length === 0) return;

      if (!appData || !appData.combos) return;

      // Crear mapa i llista de totes les combinades disponibles a appData
      const combosList = [];
      Object.entries(appData.combos).forEach(([ck, clist]) => {
        ['safe', 'semi', 'risky'].forEach(tier => {
          (clist[tier] || []).forEach((c, idx) => {
            combosList.push({ ...c, leagueKey: ck, tier: tier, comboKey: `${ck}_${tier}_${idx}` });
          });
        });
      });

      let changed = false;

      bets.forEach(b => {
        const isCombo = b.category === 'Combinada' || (b.matchup && b.matchup.toLowerCase().includes('combinada'));
        if (!isCombo) return;

        // Intentar trobar la combinada corresponent
        let match = null;
        if (b.combo_id) {
          match = combosList.find(c => c.id === b.combo_id || `${c.leagueKey}_${c.id}` === b.combo_id || c.comboKey === b.combo_id);
        }
        if (!match && b.matchup) {
          // Cercar per perfil (SAFE_1, SAFE_2, SEMI_1, etc.) i competició
          const profMatch = b.matchup.match(/(SAFE_\d|SEMI_\d|RISKY_\d)/i);
          if (profMatch) {
            const prof = profMatch[1].toUpperCase();
            match = combosList.find(c => (c.profile || '').toUpperCase() === prof && (!b.competition_id || b.competition_id === 'MULTI' || c.leagueKey === b.competition_id));
            if (!match) {
              match = combosList.find(c => (c.profile || '').toUpperCase() === prof);
            }
          }
        }
        if (!match && b.selection) {
          // Cercar per coincidència d'algun equip o selecció
          match = combosList.find(c => {
            return (c.legs || []).some(l => b.selection.includes(l.selection_name) || (l.matchup && b.selection.includes(l.matchup.split(' vs ')[0])));
          });
        }

        if (match) {
          if (!b.combo_id) { b.combo_id = match.id || match.comboKey; changed = true; }
          if (!b.profile) { b.profile = match.profile; changed = true; }
          if (match.legs && match.legs.length > 0) {
            b.legs = JSON.parse(JSON.stringify(match.legs));
            changed = true;

            // Recalcular l'estat automàticament a partir dels resultats de les cames
            const totalLegs = b.legs.length;
            const wonLegs = b.legs.filter(l => l.status === 'WON').length;
            const lostLegs = b.legs.filter(l => l.status === 'LOST').length;

            let autoStatus = 'PENDING';
            if (lostLegs > 0) autoStatus = 'LOST';
            else if (totalLegs > 0 && wonLegs === totalLegs) autoStatus = 'WON';

            if (b.status !== autoStatus) {
              b.status = autoStatus;
              b.auto_evaluated = true;
              changed = true;
            } else {
              b.auto_evaluated = true;
            }
          }
        }
      });

      if (changed) {
        localStorage.setItem(this.getStorageKey(), JSON.stringify(bets));
      }
    } catch (e) {
      console.warn("Error sincronitzant apostes amb dades en directe:", e);
    }
  },

  add(bet) {
    const bets = this.getAll();
    const newBet = {
      id: 'bet_' + Date.now() + '_' + Math.random().toString(36).substr(2, 5),
      date: new Date().toLocaleDateString('ca-ES', { day: '2-digit', month: '2-digit', year: 'numeric' }) + ' ' +
            new Date().toLocaleTimeString('ca-ES', { hour: '2-digit', minute: '2-digit' }),
      matchup: bet.matchup || 'Partit Sense Nom',
      competition_id: bet.competition_id || 'HYPERMOTION',
      category: bet.category || 'Personalitzada',
      selection: bet.selection || 'Selecció',
      odd: parseFloat(bet.odd) || 2.0,
      stake: parseFloat(bet.stake) || 10.0,
      status: bet.status || 'PENDING',
      notes: bet.notes || '',
      combo_id: bet.combo_id || null,
      profile: bet.profile || null,
      legs: bet.legs ? JSON.parse(JSON.stringify(bet.legs)) : null,
      auto_evaluated: bet.auto_evaluated || false
    };
    bets.unshift(newBet);
    this.saveAll(bets);
    showToast(`✅ Aposta afegida al teu recompte personal: ${newBet.selection} (@${newBet.odd.toFixed(2)})`, 'success');
    return newBet;
  },

  updateStatus(betId, newStatus) {
    const bets = this.getAll();
    const target = bets.find(b => b.id === betId);
    if (target) {
      target.status = newStatus;
      target.auto_evaluated = false; // El canvi manual té prioritat si l'usuari ho força
      this.saveAll(bets);
      const icon = newStatus === 'WON' ? '✅' : newStatus === 'LOST' ? '❌' : '⏳';
      const label = newStatus === 'WON' ? 'Guanyada' : newStatus === 'LOST' ? 'Perduda' : 'Pendent';
      showToast(`${icon} Aposta marcada com a ${label}`, 'info');
    }
  },

  delete(betId) {
    let bets = this.getAll();
    bets = bets.filter(b => b.id !== betId);
    this.saveAll(bets);
    showToast("🗑️ Aposta eliminada del teu registre.", 'info');
  },

  clearAll() {
    if (confirm("Estàs segur que vols buidar TOT el teu registre d'apostes personal?")) {
      this.saveAll([]);
      showToast("🗑️ S'ha buidat tot el teu registre.", 'info');
    }
  },

  calculateKPIs() {
    const bets = this.getAll();
    let totalInvested = 0;
    let closedStake = 0;
    let totalPayout = 0;
    let pendingStake = 0;
    let won = 0;
    let lost = 0;
    let pending = 0;

    bets.forEach(b => {
      const stake = parseFloat(b.stake) || 0;
      const odd = parseFloat(b.odd) || 1.0;

      if (b.status === 'WON') {
        won++;
        closedStake += stake;
        totalPayout += stake * odd;
      } else if (b.status === 'LOST') {
        lost++;
        closedStake += stake;
      } else {
        pending++;
        pendingStake += stake;
      }
      totalInvested += stake;
    });

    const netPnl = totalPayout - closedStake;
    const closedCount = won + lost;
    const roiPct = closedStake > 0 ? (netPnl / closedStake * 100.0) : 0.0;
    const winRatePct = closedCount > 0 ? (won / closedCount * 100.0) : 0.0;

    return {
      totalInvested,
      closedStake,
      totalPayout,
      netPnl,
      pendingStake,
      roiPct,
      winRatePct,
      won,
      lost,
      pending,
      totalCount: bets.length
    };
  },

  render() {
    this.syncWithLiveData();
    const kpis = this.calculateKPIs();
    const kpiContainer = document.getElementById('personal-kpi-grid');
    const listContainer = document.getElementById('personal-bets-list');

    const elAll = document.getElementById('count-all');
    const elPending = document.getElementById('count-pending');
    const elWon = document.getElementById('count-won');
    const elLost = document.getElementById('count-lost');
    if (elAll) elAll.textContent = kpis.totalCount;
    if (elPending) elPending.textContent = kpis.pending;
    if (elWon) elWon.textContent = kpis.won;
    if (elLost) elLost.textContent = kpis.lost;

    if (kpiContainer) {
      const isProfit = kpis.netPnl >= 0;
      const pnlSign = isProfit ? '+' : '';
      const roiSign = kpis.roiPct >= 0 ? '+' : '';

      kpiContainer.innerHTML = `
        <div class="kpi-card">
          <div class="kpi-icon">💳</div>
          <div class="kpi-label">Capital Apostat</div>
          <div class="kpi-value">${kpis.totalInvested.toFixed(2)} €</div>
          <div class="kpi-sub">${kpis.closedStake.toFixed(2)} € resolts · ${kpis.pendingStake.toFixed(2)} € en joc</div>
        </div>

        <div class="kpi-card">
          <div class="kpi-icon">💵</div>
          <div class="kpi-label">Retorn Brut Cobrat</div>
          <div class="kpi-value">${kpis.totalPayout.toFixed(2)} €</div>
          <div class="kpi-sub">${kpis.won} apostes encertades</div>
        </div>

        <div class="kpi-card highlight ${isProfit ? '' : 'loss'}">
          <div class="kpi-icon">📈</div>
          <div class="kpi-label">El Teu Balanç Net (PnL)</div>
          <div class="kpi-value ${isProfit ? 'positive' : 'negative'}">${pnlSign}${kpis.netPnl.toFixed(2)} €</div>
          <div class="kpi-sub">${kpis.won + kpis.lost > 0 ? "Rendiment net consolidat" : "Cap aposta resolta encara"}</div>
        </div>

        <div class="kpi-card">
          <div class="kpi-icon">🎯</div>
          <div class="kpi-label">El Teu ROI</div>
          <div class="kpi-value ${isProfit ? 'positive' : 'negative'}">${roiSign}${kpis.roiPct.toFixed(1)}%</div>
          <div class="kpi-sub">Retorn sobre capital resolt</div>
        </div>

        <div class="kpi-card">
          <div class="kpi-icon">🏆</div>
          <div class="kpi-label">Taxa d'Encert</div>
          <div class="kpi-value" style="color: var(--accent-cyan);">${kpis.winRatePct.toFixed(1)}%</div>
          <div class="kpi-sub">${kpis.won}W / ${kpis.lost}L (${kpis.pending} pendents)</div>
        </div>
      `;
    }

    if (listContainer) {
      let bets = this.getAll();
      if (this.currentFilter !== 'ALL') {
        bets = bets.filter(b => b.status === this.currentFilter);
      }

      if (bets.length === 0) {
        listContainer.innerHTML = `
          <div class="personal-empty-state">
            <span class="empty-icon">📭</span>
            <h3>No hi ha cap aposta en aquest filtre</h3>
            <p>Afegeix una aposta fàcilment des d'<strong>Apostes de Valor (+EV%)</strong>, des de les <strong>Combinades</strong>, o clica el botó superior <em>"➕ Nova Aposta Personalitzada"</em>.</p>
            <button class="btn-primary-action" onclick="openAddBetModal()" style="margin-top: 14px;">
              <span>➕ Afegir Aposta Ara</span>
            </button>
          </div>
        `;
        return;
      }

      listContainer.innerHTML = bets.map(b => {
        const isWon = b.status === 'WON';
        const isLost = b.status === 'LOST';
        const isPending = b.status === 'PENDING';

        const payout = isWon ? (b.stake * b.odd) : 0;
        const netProfit = isWon ? (payout - b.stake) : isLost ? (-b.stake) : 0;
        const profitSign = netProfit > 0 ? '+' : '';

        let statusBadge = '<span class="status-badge-bet pending">⏳ PENDENT</span>';
        if (isWon) statusBadge = `<span class="status-badge-bet won">✅ GUANYADA${b.auto_evaluated ? ' <small style="font-size:10px; opacity:0.8;">(Auto)</small>' : ''}</span>`;
        if (isLost) statusBadge = `<span class="status-badge-bet lost">❌ PERDUDA${b.auto_evaluated ? ' <small style="font-size:10px; opacity:0.8;">(Auto)</small>' : ''}</span>`;
        if (isPending && b.auto_evaluated) statusBadge = '<span class="status-badge-bet pending">⏳ EN CURS <small style="font-size:10px; opacity:0.8;">(Auto)</small></span>';

        let comboProgressHtml = '';
        if (b.category === 'Combinada' && b.legs && b.legs.length > 0) {
          const legs = b.legs;
          const totalLegs = legs.length;
          const wonLegs = legs.filter(l => l.status === 'WON').length;
          const lostLegs = legs.filter(l => l.status === 'LOST').length;
          const pendingLegs = legs.filter(l => l.status === 'PENDING' || !l.status).length;

          let progressSummary = `${wonLegs}/${totalLegs} encertats`;
          if (pendingLegs > 0) progressSummary += ` · ${pendingLegs} pendents`;
          if (lostLegs > 0) progressSummary += ` (${lostLegs} fallat${lostLegs > 1 ? 's' : ''})`;

          let statusAccent = 'var(--accent-amber)';
          if (lostLegs > 0) statusAccent = 'var(--accent-rose)';
          else if (totalLegs > 0 && wonLegs === totalLegs) statusAccent = 'var(--accent-emerald)';

          comboProgressHtml = `
            <div class="combo-progress-bar-container" style="margin: 12px 0 6px 0; background: rgba(0, 0, 0, 0.25); border: 1px solid rgba(255, 255, 255, 0.06); padding: 10px 12px; border-radius: 8px;">
              <div class="combo-progress-label-row" style="margin-bottom: 6px; display: flex; justify-content: space-between; align-items: center;">
                <span class="combo-progress-title" style="font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-muted);">⚡ Progrés en directe</span>
                <span class="combo-progress-stats" style="font-size: 11px; color: ${statusAccent};"><strong>${progressSummary}</strong></span>
              </div>
              <div class="combo-progress-bar" style="height: 6px; border-radius: 3px; background: rgba(255, 255, 255, 0.08); overflow: hidden; display: flex; gap: 2px;">
                ${legs.map((l, lIdx) => {
                  let segCls = 'seg-pending';
                  let segTitle = `Partit ${lIdx + 1} (${l.matchup}): Pendent`;
                  if (l.status === 'WON') {
                    segCls = 'seg-won';
                    segTitle = `Partit ${lIdx + 1} (${l.matchup}): Encertat! (${l.actual_result || ''})`;
                  } else if (l.status === 'LOST') {
                    segCls = 'seg-lost';
                    segTitle = `Partit ${lIdx + 1} (${l.matchup}): Fallat (${l.actual_result || ''})`;
                  }
                  return `<div class="combo-progress-segment ${segCls}" style="flex: 1;" title="${segTitle}"></div>`;
                }).join('')}
              </div>

              <div class="personal-combo-legs" style="margin-top: 10px; display: flex; flex-direction: column; gap: 4px;">
                ${legs.map(l => {
                  let legIcon = '⏳';
                  let legColor = 'var(--accent-amber)';
                  let legBg = 'rgba(245, 158, 11, 0.06)';
                  if (l.status === 'WON') {
                    legIcon = '✅';
                    legColor = 'var(--accent-emerald)';
                    legBg = 'rgba(16, 185, 129, 0.08)';
                  } else if (l.status === 'LOST') {
                    legIcon = '❌';
                    legColor = 'var(--accent-rose)';
                    legBg = 'rgba(244, 63, 94, 0.08)';
                  }
                  const homeShort = l.matchup ? l.matchup.split(' vs ')[0] : '';
                  return `
                    <div style="display: flex; justify-content: space-between; align-items: center; font-size: 11.5px; padding: 4px 8px; background: ${legBg}; border-radius: 4px; border-left: 2px solid ${legColor};">
                      <div style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 82%;">
                        <span style="color: ${legColor}; margin-right: 4px;">${legIcon}</span>
                        <strong style="color: var(--text-primary);">${homeShort}:</strong>
                        <span style="color: var(--text-secondary); margin-left: 3px;">${l.selection_name}</span>
                        ${l.actual_result ? `<span style="color: ${legColor}; font-size: 10.5px; margin-left: 6px; font-weight: 500;">(${l.actual_result})</span>` : ''}
                      </div>
                      <span style="font-family: var(--font-mono); font-size: 11px; color: var(--accent-cyan); white-space: nowrap; margin-left: 8px;">@${l.bookie_odd ? Number(l.bookie_odd).toFixed(2) : '-'}</span>
                    </div>
                  `;
                }).join('')}
              </div>
            </div>
          `;
        }

        return `
          <div class="personal-bet-card ${b.status.toLowerCase()}">
            <div class="bet-card-main">
              <div class="bet-card-meta">
                <span class="bet-date">🕒 ${b.date}</span>
                <span class="bet-category-pill">${b.category}</span>
                <span class="bet-comp-pill">${b.competition_id}</span>
                ${b.auto_evaluated ? '<span class="bet-comp-pill" style="background: rgba(16, 185, 129, 0.15); color: var(--accent-emerald);">🤖 Auto-sync</span>' : ''}
              </div>
              <div class="bet-card-matchup">${b.matchup}</div>
              <div class="bet-card-selection">👉 <strong>${b.selection}</strong></div>
              ${comboProgressHtml}
            </div>

            <div class="bet-card-financials">
              <div class="bet-fin-item">
                <span class="fin-label">Quota</span>
                <span class="fin-val font-mono">@${b.odd.toFixed(2)}</span>
              </div>
              <div class="bet-fin-item">
                <span class="fin-label">Apostat</span>
                <span class="fin-val font-mono">${b.stake.toFixed(2)} €</span>
              </div>
              <div class="bet-fin-item">
                <span class="fin-label">Balanç Net</span>
                <span class="fin-val font-mono ${netProfit > 0 ? 'color-emerald' : netProfit < 0 ? 'color-rose' : 'color-muted'}">
                  ${isPending ? `(Pot: +${((b.stake * b.odd) - b.stake).toFixed(2)} €)` : `${profitSign}${netProfit.toFixed(2)} €`}
                </span>
              </div>
              <div class="bet-fin-item">
                <span class="fin-label">Estat</span>
                ${statusBadge}
              </div>
            </div>

            <div class="bet-card-actions">
              ${b.auto_evaluated ? '<div style="font-size: 10px; color: var(--text-muted); margin-bottom: 4px; text-align: right;">🤖 Sincronitzat pel model</div>' : ''}
              <div class="quick-status-group">
                <button class="btn-status-toggle ${isWon ? 'active won' : ''}" onclick="PersonalBets.updateStatus('${b.id}', 'WON')" title="Marcar com a Guanyada">
                  ✅ Guanyada
                </button>
                <button class="btn-status-toggle ${isLost ? 'active lost' : ''}" onclick="PersonalBets.updateStatus('${b.id}', 'LOST')" title="Marcar com a Perduda">
                  ❌ Perduda
                </button>
                <button class="btn-status-toggle ${isPending ? 'active pending' : ''}" onclick="PersonalBets.updateStatus('${b.id}', 'PENDING')" title="Tornar a Pendent">
                  ⏳ Pendent
                </button>
              </div>
              <button class="btn-delete-bet" onclick="PersonalBets.delete('${b.id}')" title="Eliminar aposta">
                🗑️
              </button>
            </div>
          </div>
        `;
      }).join('');
    }
  }
};

// =============================================================================
// MODALS I UTILITATS D'APOSTES
// =============================================================================
function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = `toast-item toast-${type}`;
  toast.innerHTML = `<span>${message}</span>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.classList.add('visible');
  }, 10);
  setTimeout(() => {
    toast.classList.remove('visible');
    setTimeout(() => toast.remove(), 300);
  }, 3500);
}

function openAuthModal() {
  const modal = document.getElementById('modal-auth');
  if (modal) {
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    const uInput = document.getElementById('auth-username');
    if (uInput) setTimeout(() => uInput.focus(), 100);
  }
}

function closeAuthModal() {
  const modal = document.getElementById('modal-auth');
  if (modal) {
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
    const err = document.getElementById('auth-error-msg');
    if (err) err.style.display = 'none';
  }
}

function openAddBetModal(prefill = null) {
  const modal = document.getElementById('modal-add-bet');
  if (!modal) return;
  if (prefill) {
    if (prefill.matchup) document.getElementById('bet-matchup').value = prefill.matchup;
    if (prefill.selection) document.getElementById('bet-selection').value = prefill.selection;
    if (prefill.odd) document.getElementById('bet-odd').value = prefill.odd;
    if (prefill.stake) document.getElementById('bet-stake').value = prefill.stake;
    if (prefill.category) document.getElementById('bet-category').value = prefill.category;
    if (prefill.competition_id) document.getElementById('bet-competition').value = prefill.competition_id;
  }
  modal.classList.add('open');
  modal.setAttribute('aria-hidden', 'false');
}

function closeAddBetModal() {
  const modal = document.getElementById('modal-add-bet');
  if (modal) {
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
  }
}

function quickTrackBetFromCard(encoded) {
  try {
    const bet = JSON.parse(decodeURIComponent(encoded));
    PersonalBets.add({
      matchup: bet.matchup,
      selection: bet.selection,
      odd: bet.odd,
      stake: 10,
      category: bet.category || 'Valor (+EV)',
      competition_id: bet.competition_id,
      status: 'PENDING'
    });
  } catch (e) {
    console.error("Error afegint aposta:", e);
  }
}

function trackComboBet(comboKey) {
  let found = (window.combosRegistry && window.combosRegistry[comboKey]) || null;
  let compKey = (found && (found.leagueKey || found.competition_id)) || '';

  if (!found && appData && appData.combos) {
    for (const [ck, clist] of Object.entries(appData.combos)) {
      const allC = [
        ...(clist.safe || []),
        ...(clist.semi || []),
        ...(clist.risky || [])
      ];
      const c = allC.find(item => item.id === comboKey || `${ck}_${item.id}` === comboKey);
      if (c) {
        found = c;
        compKey = ck;
        break;
      }
    }
  }

  if (!found) {
    showToast("No s'ha pogut trobar la combinada seleccionada.", 'error');
    return;
  }

  const boostedOdd = found.boosted_odd || found.combined_odd || 2.0;
  const stake = typeof getComboEffectiveStake === 'function' ? getComboEffectiveStake(found) : (parseFloat(found.stake) || 10.0);

  const legsSummary = (found.legs || []).map(l => {
    const teamShort = l.matchup ? l.matchup.split(' vs ')[0] : '';
    return `${teamShort ? teamShort + ': ' : ''}${l.selection_name}`;
  }).join(' · ');

  const legs = (found.legs || []).map(l => ({ ...l }));
  const totalLegs = legs.length;
  const wonLegs = legs.filter(l => l.status === 'WON').length;
  const lostLegs = legs.filter(l => l.status === 'LOST').length;
  let initStatus = 'PENDING';
  if (lostLegs > 0) initStatus = 'LOST';
  else if (totalLegs > 0 && wonLegs === totalLegs) initStatus = 'WON';

  PersonalBets.add({
    combo_id: found.id || comboKey,
    profile: found.profile || 'Recomanada',
    matchup: `Combinada ${found.profile || 'Recomanada'} (${totalLegs} partits)`,
    selection: legsSummary || legs.map(l => l.selection_name).join(' + ') || 'Combinada',
    odd: parseFloat(boostedOdd) || 2.0,
    stake: parseFloat(stake) || 10.0,
    category: 'Combinada',
    competition_id: compKey || 'MULTI',
    status: initStatus,
    legs: legs,
    auto_evaluated: true
  });
}

// Expose handlers globally on window for inline onclick handlers
window.trackComboBet = trackComboBet;
window.quickTrackBetFromCard = quickTrackBetFromCard;
window.trackMatchForecast = trackMatchForecast;
window.openAuthModal = openAuthModal;
window.closeAuthModal = closeAuthModal;
window.openAddBetModal = openAddBetModal;
window.closeAddBetModal = closeAddBetModal;
window.PersonalBets = PersonalBets;
window.UserAuth = UserAuth;

function trackMatchForecast(matchId) {
  if (!appData || !appData.predictions) return;
  let foundMatch = null;
  for (const [cid, cData] of Object.entries(appData.predictions)) {
    const m = (cData.matches || []).find(item => item.match_id === matchId);
    if (m) {
      foundMatch = m;
      break;
    }
  }
  if (!foundMatch) {
    openAddBetModal();
    return;
  }

  openAddBetModal({
    matchup: `${foundMatch.home_team.name} vs ${foundMatch.away_team.name}`,
    selection: `Pronòstic: ${foundMatch.verdict}`,
    odd: foundMatch.odds_1x2 ? (foundMatch.odds_1x2['1'] || 2.0) : 2.0,
    stake: 10,
    category: '1X2',
    competition_id: foundMatch.competition_id
  });
}

// -----------------------------------------------------------------------------
// 5. RECOMPTE DE DINERS ("QUÈ HAGUÉS PASSAT SI...") · SIMULADOR INTERACTIU
// -----------------------------------------------------------------------------

const bankrollSimState = {
  globalUnits: {
    safe: 25.0,
    semi: 10.0,
    risky: 5.0
  },
  comboOverrides: {}, // comboId -> { stake?: number, simStatus?: 'REAL'|'WON'|'LOST'|'PENDING' }
  loadedFromStorage: false
};

function loadBankrollSimState() {
  if (bankrollSimState.loadedFromStorage) return;
  try {
    const raw = localStorage.getItem('prediccions_bankroll_sim');
    if (raw) {
      const parsed = JSON.parse(raw);
      if (parsed.globalUnits) {
        bankrollSimState.globalUnits = {
          safe: parseFloat(parsed.globalUnits.safe) || 25.0,
          semi: parseFloat(parsed.globalUnits.semi) || 10.0,
          risky: parseFloat(parsed.globalUnits.risky) || 5.0
        };
      }
      if (parsed.comboOverrides && typeof parsed.comboOverrides === 'object') {
        bankrollSimState.comboOverrides = parsed.comboOverrides;
      }
    }
  } catch (err) {
    console.warn("No s'ha pogut carregar l'estat del simulador de bankroll des de localStorage:", err);
  }
  bankrollSimState.loadedFromStorage = true;
}

function saveBankrollSimState() {
  try {
    localStorage.setItem('prediccions_bankroll_sim', JSON.stringify({
      globalUnits: bankrollSimState.globalUnits,
      comboOverrides: bankrollSimState.comboOverrides
    }));
  } catch (err) {
    console.warn("No s'ha pogut guardar l'estat del simulador a localStorage:", err);
  }
}

function getComboCategory(combo) {
  const prof = ((combo && combo.profile) || '').toUpperCase();
  if (prof.includes('SAFE') || prof.includes('SEGURA')) return 'safe';
  if (prof.includes('SEMI')) return 'semi';
  return 'risky';
}

function getComboEffectiveStake(combo) {
  const override = bankrollSimState.comboOverrides[combo.id];
  if (override && typeof override.stake === 'number' && !isNaN(override.stake)) {
    return Math.max(0, override.stake);
  }
  const cat = getComboCategory(combo);
  const globalUnit = bankrollSimState.globalUnits[cat];
  if (typeof globalUnit === 'number' && !isNaN(globalUnit)) {
    return Math.max(0, globalUnit);
  }
  return parseFloat(combo.stake) || 10.0;
}

function getComboUserStatus(combo) {
  const override = bankrollSimState.comboOverrides[combo.id];
  if (override && override.simStatus) {
    return override.simStatus;
  }
  return 'REAL';
}

function getComboEffectiveStatus(combo) {
  const userStatus = getComboUserStatus(combo);
  if (userStatus && userStatus !== 'REAL') {
    return userStatus;
  }
  return combo.status || 'PENDING';
}

function calculateComboMetrics(combo) {
  const stake = getComboEffectiveStake(combo);
  const status = getComboEffectiveStatus(combo);
  const odd = parseFloat(combo.combined_odd) || 1.0;

  let payout = 0;
  let profit = 0;
  let isEvaluated = false;

  if (status === 'WON') {
    payout = stake * odd;
    profit = payout - stake;
    isEvaluated = true;
  } else if (status === 'LOST') {
    payout = 0;
    profit = -stake;
    isEvaluated = true;
  } else {
    // PENDING
    payout = 0;
    profit = 0;
    isEvaluated = false;
  }

  const potentialPayout = stake * odd;
  const potentialProfit = potentialPayout - stake;

  return {
    comboId: combo.id,
    category: getComboCategory(combo),
    stake,
    status,
    odd,
    payout,
    profit,
    isEvaluated,
    potentialPayout,
    potentialProfit
  };
}

function calculateBankrollSimulation() {
  const bData = appData && appData.bankroll_simulation;
  if (!bData) return null;

  const rounds = bData.rounds_history || [];

  let totalEvaluatedStake = 0;
  let totalEvaluatedPayout = 0;
  let totalNetProfit = 0;
  let totalPendingStake = 0;
  let totalPotentialProfit = 0;

  let wonBets = 0;
  let lostBets = 0;
  let pendingBets = 0;

  const profilesCalc = {
    safe: { unit: bankrollSimState.globalUnits.safe, invested: 0, pendingStake: 0, payout: 0, netPnl: 0, won: 0, lost: 0, pending: 0 },
    semi: { unit: bankrollSimState.globalUnits.semi, invested: 0, pendingStake: 0, payout: 0, netPnl: 0, won: 0, lost: 0, pending: 0 },
    risky: { unit: bankrollSimState.globalUnits.risky, invested: 0, pendingStake: 0, payout: 0, netPnl: 0, won: 0, lost: 0, pending: 0 }
  };

  const roundsCalc = rounds.map((r, rIdx) => {
    let rEvalStake = 0;
    let rTotalStake = 0;
    let rPayout = 0;
    let rProfit = 0;
    let rWon = 0;
    let rLost = 0;
    let rPending = 0;

    const combosCalc = (r.combos || []).map(cb => {
      const cm = calculateComboMetrics(cb);
      rTotalStake += cm.stake;

      const pProf = profilesCalc[cm.category];
      if (cm.isEvaluated) {
        rEvalStake += cm.stake;
        rPayout += cm.payout;
        rProfit += cm.profit;

        totalEvaluatedStake += cm.stake;
        totalEvaluatedPayout += cm.payout;
        totalNetProfit += cm.profit;

        if (cm.status === 'WON') {
          rWon++;
          wonBets++;
          if (pProf) pProf.won++;
        } else {
          rLost++;
          lostBets++;
          if (pProf) pProf.lost++;
        }

        if (pProf) {
          pProf.invested += cm.stake;
          pProf.payout += cm.payout;
          pProf.netPnl += cm.profit;
        }
      } else {
        rPending++;
        pendingBets++;
        totalPendingStake += cm.stake;
        totalPotentialProfit += cm.potentialProfit;
        if (pProf) {
          pProf.pending++;
          pProf.pendingStake += cm.stake;
        }
      }
      return cm;
    });

    let statusSummary = 'PENDING';
    if (rPending === 0) {
      statusSummary = rProfit >= 0 ? 'WON' : 'LOST';
    } else if (rWon > 0 || rLost > 0) {
      statusSummary = rProfit >= 0 ? 'EN CURS (+)' : 'EN CURS (-)';
    }

    return {
      roundIndex: rIdx,
      competitionId: r.competition_id,
      jornada: r.jornada,
      evaluatedStake: rEvalStake,
      totalStake: rTotalStake,
      payout: rPayout,
      netProfit: rProfit,
      wonCount: rWon,
      lostCount: rLost,
      pendingCount: rPending,
      statusSummary,
      combosCalc
    };
  });

  const totalClosed = wonBets + lostBets;
  const roiPct = totalEvaluatedStake > 0 ? (totalNetProfit / totalEvaluatedStake * 100.0) : 0.0;
  const winRatePct = totalClosed > 0 ? (wonBets / totalClosed * 100.0) : 0.0;

  // Check if simulation differs from defaults
  const isCustomUnits = (
    bankrollSimState.globalUnits.safe !== 25.0 ||
    bankrollSimState.globalUnits.semi !== 10.0 ||
    bankrollSimState.globalUnits.risky !== 5.0
  );
  const isCustomCombos = Object.keys(bankrollSimState.comboOverrides).length > 0;
  const isCustomized = isCustomUnits || isCustomCombos;

  return {
    kpis: {
      totalInvested: totalEvaluatedStake,
      totalPayout: totalEvaluatedPayout,
      netPnl: totalNetProfit,
      roiPct,
      winRatePct,
      totalBets: totalClosed,
      wonBets,
      lostBets,
      pendingBets,
      totalPendingStake,
      totalPotentialProfit
    },
    byProfile: profilesCalc,
    roundsCalc,
    isCustomized
  };
}

function renderBankrollKpiGrid(kpis) {
  const isProfit = kpis.netPnl >= 0;
  const pnlSign = isProfit ? '+' : '';
  const roiSign = kpis.roiPct >= 0 ? '+' : '';

  const closedSubtitle = kpis.totalBets > 0
    ? `${kpis.totalBets} concloses · ${kpis.totalPendingStake.toFixed(2)} € en joc`
    : `0 tancades (${kpis.totalPendingStake.toFixed(2)} € pendents en joc)`;

  return `
    <div class="kpi-card">
      <div class="kpi-icon">💳</div>
      <div class="kpi-label">Total Invertit</div>
      <div class="kpi-value" id="kpi-val-invested">${kpis.totalInvested.toFixed(2)} €</div>
      <div class="kpi-sub" id="kpi-sub-invested">${closedSubtitle}</div>
    </div>

    <div class="kpi-card">
      <div class="kpi-icon">💵</div>
      <div class="kpi-label">Retorn Brut</div>
      <div class="kpi-value" id="kpi-val-payout">${kpis.totalPayout.toFixed(2)} €</div>
      <div class="kpi-sub">Pagaments totals rebuts</div>
    </div>

    <div class="kpi-card highlight ${isProfit ? '' : 'loss'}">
      <div class="kpi-icon">📈</div>
      <div class="kpi-label">Balanç Net (PnL)</div>
      <div class="kpi-value ${isProfit ? 'positive' : 'negative'}" id="kpi-val-pnl">${pnlSign}${kpis.netPnl.toFixed(2)} €</div>
      <div class="kpi-sub">${kpis.totalBets > 0 ? "Rendiment net consolidat" : "Cap aposta finalitzada encara"}</div>
    </div>

    <div class="kpi-card">
      <div class="kpi-icon">🎯</div>
      <div class="kpi-label">Rendibilitat (ROI)</div>
      <div class="kpi-value ${isProfit ? 'positive' : 'negative'}" id="kpi-val-roi">${roiSign}${kpis.roiPct.toFixed(1)}%</div>
      <div class="kpi-sub">Retorn sobre capital tancat</div>
    </div>

    <div class="kpi-card">
      <div class="kpi-icon">🏆</div>
      <div class="kpi-label">Taxa d'Encert</div>
      <div class="kpi-value" style="color: var(--accent-cyan);" id="kpi-val-winrate">${kpis.winRatePct.toFixed(1)}%</div>
      <div class="kpi-sub" id="kpi-sub-winrate">${kpis.wonBets}W / ${kpis.lostBets}L (${kpis.pendingBets} pendents)</div>
    </div>
  `;
}

function renderBankrollProfilesGrid(byProfile) {
  const cards = [
    { key: 'safe', label: 'Combinades Segures', icon: '🛡️', d: byProfile.safe },
    { key: 'semi', label: 'Combinades Semi-Arriscades', icon: '⚖️', d: byProfile.semi },
    { key: 'risky', label: 'Combinades Arriscades', icon: '🚀', d: byProfile.risky }
  ];

  return cards.map(c => {
    const d = c.d || {};
    const isProf = (d.netPnl || 0) >= 0;
    const totalClosed = (d.won || 0) + (d.lost || 0);
    const winRate = totalClosed > 0 ? (d.won / totalClosed * 100.0) : 0.0;
    const unitStake = typeof d.unit === 'number' ? d.unit.toFixed(2) : '0.00';

    return `
      <div class="profile-card">
        <div class="profile-card-header">
          <div class="profile-card-title">${c.icon} ${c.label}</div>
          <span class="profile-unit-badge" id="profile-badge-unit-${c.key}">Unitat activa: <strong>${unitStake} €</strong></span>
        </div>

        <div class="profile-stats-row">
          <div>
            <div class="stat-item-label">Invertit (Tancat)</div>
            <div class="stat-item-val" id="profile-stat-invested-${c.key}">${(d.invested || 0).toFixed(2)} €</div>
          </div>
          <div>
            <div class="stat-item-label">Balanç Net</div>
            <div class="stat-item-val" id="profile-stat-pnl-${c.key}" style="color: ${isProf ? 'var(--accent-emerald)' : 'var(--accent-rose)'};">
              ${isProf ? '+' : ''}${(d.netPnl || 0).toFixed(2)} €
            </div>
          </div>
          <div>
            <div class="stat-item-label">Taxa Encert</div>
            <div class="stat-item-val" id="profile-stat-winrate-${c.key}" style="color: var(--accent-cyan);">${winRate.toFixed(1)}%</div>
          </div>
        </div>
      </div>
    `;
  }).join('');
}

function renderLedgerRoundMetricsHtml(rc) {
  const isProf = rc.netProfit >= 0;
  const pnlSign = isProf ? '+' : '';
  const displayStake = rc.evaluatedStake > 0 ? rc.evaluatedStake : rc.totalStake;
  const stakeNote = rc.evaluatedStake === 0 && rc.pendingCount > 0 ? ' (en joc)' : '';

  let statusClass = 'status-pending-tag';
  if (rc.statusSummary === 'WON') statusClass = 'status-won-tag';
  else if (rc.statusSummary === 'LOST') statusClass = 'status-lost-tag';

  return `
    <span>Apostat: <strong>${displayStake.toFixed(2)} €</strong>${stakeNote}</span>
    <span style="color: ${isProf ? 'var(--accent-emerald)' : 'var(--accent-rose)'};">
      Balanç: <strong>${pnlSign}${rc.netProfit.toFixed(2)} €</strong>
    </span>
    <span class="${statusClass}">${rc.statusSummary}</span>
    <span style="font-size: 11px; color: var(--text-muted);">▼</span>
  `;
}

function renderLedgerComboBadgeHtml(cm) {
  if (cm.status === 'WON') {
    return `<span class="status-won-tag">GUANYADA (+${cm.profit.toFixed(2)} € · Retorn: ${cm.payout.toFixed(2)} €)</span>`;
  } else if (cm.status === 'LOST') {
    return `<span class="status-lost-tag">PERDUDA (-${cm.stake.toFixed(2)} €)</span>`;
  } else {
    return `<span class="status-pending-tag">PENDENT (Potencial: +${cm.potentialProfit.toFixed(2)} €)</span>`;
  }
}

// Actualització ultra-ràpida del DOM sense destruir inputs ni perdre el cursor
function updateBankrollDom(calc) {
  if (!calc) return;

  // 1. KPIs
  const kpiInvested = document.getElementById('kpi-val-invested');
  if (kpiInvested) kpiInvested.textContent = `${calc.kpis.totalInvested.toFixed(2)} €`;

  const kpiSubInvested = document.getElementById('kpi-sub-invested');
  if (kpiSubInvested) {
    kpiSubInvested.textContent = calc.kpis.totalBets > 0
      ? `${calc.kpis.totalBets} concloses · ${calc.kpis.totalPendingStake.toFixed(2)} € en joc`
      : `0 tancades (${calc.kpis.totalPendingStake.toFixed(2)} € pendents en joc)`;
  }

  const kpiPayout = document.getElementById('kpi-val-payout');
  if (kpiPayout) kpiPayout.textContent = `${calc.kpis.totalPayout.toFixed(2)} €`;

  const kpiPnl = document.getElementById('kpi-val-pnl');
  if (kpiPnl) {
    const isProf = calc.kpis.netPnl >= 0;
    kpiPnl.textContent = `${isProf ? '+' : ''}${calc.kpis.netPnl.toFixed(2)} €`;
    kpiPnl.className = `kpi-value ${isProf ? 'positive' : 'negative'}`;
  }

  const kpiRoi = document.getElementById('kpi-val-roi');
  if (kpiRoi) {
    const isProf = calc.kpis.roiPct >= 0;
    kpiRoi.textContent = `${isProf ? '+' : ''}${calc.kpis.roiPct.toFixed(1)}%`;
    kpiRoi.className = `kpi-value ${isProf ? 'positive' : 'negative'}`;
  }

  const kpiWinrate = document.getElementById('kpi-val-winrate');
  if (kpiWinrate) kpiWinrate.textContent = `${calc.kpis.winRatePct.toFixed(1)}%`;

  const kpiSubWinrate = document.getElementById('kpi-sub-winrate');
  if (kpiSubWinrate) kpiSubWinrate.textContent = `${calc.kpis.wonBets}W / ${calc.kpis.lostBets}L (${calc.kpis.pendingBets} pendents)`;

  // 2. Profiles
  ['safe', 'semi', 'risky'].forEach(k => {
    const prof = calc.byProfile[k];
    if (!prof) return;

    const unitBadge = document.getElementById(`profile-badge-unit-${k}`);
    if (unitBadge) unitBadge.innerHTML = `Unitat activa: <strong>${prof.unit.toFixed(2)} €</strong>`;

    const invEl = document.getElementById(`profile-stat-invested-${k}`);
    if (invEl) invEl.textContent = `${prof.invested.toFixed(2)} €`;

    const pnlEl = document.getElementById(`profile-stat-pnl-${k}`);
    if (pnlEl) {
      const isP = prof.netPnl >= 0;
      pnlEl.textContent = `${isP ? '+' : ''}${prof.netPnl.toFixed(2)} €`;
      pnlEl.style.color = isP ? 'var(--accent-emerald)' : 'var(--accent-rose)';
    }

    const wrEl = document.getElementById(`profile-stat-winrate-${k}`);
    if (wrEl) {
      const totalC = prof.won + prof.lost;
      const rate = totalC > 0 ? (prof.won / totalC * 100.0) : 0.0;
      wrEl.textContent = `${rate.toFixed(1)}%`;
    }
  });

  // 3. Round headers and combo badges
  calc.roundsCalc.forEach(rc => {
    const rMetrics = document.getElementById(`ledger-round-metrics-${rc.roundIndex}`);
    if (rMetrics) {
      rMetrics.innerHTML = renderLedgerRoundMetricsHtml(rc);
    }

    rc.combosCalc.forEach(cm => {
      const badgeEl = document.getElementById(`combo-badge-${cm.comboId}`);
      if (badgeEl) {
        badgeEl.innerHTML = renderLedgerComboBadgeHtml(cm);
      }
    });
  });

  // 4. Banner de simulació personalitzada
  const banner = document.getElementById('sim-status-banner');
  const bannerText = document.getElementById('sim-status-banner-text');
  if (banner && bannerText) {
    if (calc.isCustomized) {
      banner.style.display = 'flex';
      bannerText.innerHTML = `<strong>Simulació activa amb imports personalitzats:</strong> Balanç projectat: <strong>${calc.kpis.netPnl >= 0 ? '+' : ''}${calc.kpis.netPnl.toFixed(2)} €</strong> (${calc.kpis.roiPct >= 0 ? '+' : ''}${calc.kpis.roiPct.toFixed(1)}% ROI).`;
    } else {
      banner.style.display = 'none';
    }
  }
}

function renderBankroll() {
  const bData = appData && appData.bankroll_simulation;
  if (!bData) return;

  loadBankrollSimState();

  // 1. Sincronitzar inputs de la barra superior del simulador
  const inputSafe = document.getElementById('sim-unit-safe');
  const inputSemi = document.getElementById('sim-unit-semi');
  const inputRisky = document.getElementById('sim-unit-risky');
  if (inputSafe) inputSafe.value = bankrollSimState.globalUnits.safe;
  if (inputSemi) inputSemi.value = bankrollSimState.globalUnits.semi;
  if (inputRisky) inputRisky.value = bankrollSimState.globalUnits.risky;

  // 2. Executar càlcul inicial
  const calc = calculateBankrollSimulation();
  if (!calc) return;

  // 3. Renderitzar KPI Hero Grid
  const kpiGrid = document.getElementById('bankroll-kpi-grid');
  if (kpiGrid) {
    kpiGrid.innerHTML = renderBankrollKpiGrid(calc.kpis);
  }

  // 4. Renderitzar Breakdown per Perfil
  const profilesGrid = document.getElementById('profiles-breakdown-grid');
  if (profilesGrid) {
    profilesGrid.innerHTML = renderBankrollProfilesGrid(calc.byProfile);
  }

  // 5. Preservar quins acordinons estaven oberts
  const openAccordionIndices = new Set();
  document.querySelectorAll('.ledger-round-details.open').forEach(el => {
    const id = el.id;
    const match = id && id.match(/ledger-details-(\d+)/);
    if (match) openAccordionIndices.add(parseInt(match[1], 10));
  });

  // 6. Renderitzar Historial Detallat Jornada a Jornada (Ledger)
  const ledgerList = document.getElementById('ledger-rounds-list');
  if (ledgerList) {
    const rounds = bData.rounds_history || [];
    if (rounds.length === 0) {
      ledgerList.innerHTML = `<p style="text-align: center; padding: 20px; color: var(--text-muted);">Encara no hi ha jornades històriques registrades al simulador.</p>`;
      return;
    }

    ledgerList.innerHTML = calc.roundsCalc.map(rc => {
      const origRound = rounds[rc.roundIndex];
      const isOpen = openAccordionIndices.has(rc.roundIndex);

      // Calcular imports actuals de la jornada per als inputs del xip ràpid
      const roundSafeStake = (origRound.combos || []).find(c => getComboCategory(c) === 'safe');
      const roundSemiStake = (origRound.combos || []).find(c => getComboCategory(c) === 'semi');
      const roundRiskyStake = (origRound.combos || []).find(c => getComboCategory(c) === 'risky');

      const curSafe = roundSafeStake ? getComboEffectiveStake(roundSafeStake) : bankrollSimState.globalUnits.safe;
      const curSemi = roundSemiStake ? getComboEffectiveStake(roundSemiStake) : bankrollSimState.globalUnits.semi;
      const curRisky = roundRiskyStake ? getComboEffectiveStake(roundRiskyStake) : bankrollSimState.globalUnits.risky;

      return `
        <div class="ledger-round-card" id="ledger-round-card-${rc.roundIndex}">
          <div class="ledger-round-header" onclick="toggleLedgerDetails(${rc.roundIndex})">
            <div class="ledger-round-title">
              <span>⚽</span>
              <span>${rc.competitionId} · Jornada ${rc.jornada}</span>
            </div>
            <div class="ledger-round-metrics" id="ledger-round-metrics-${rc.roundIndex}">
              ${renderLedgerRoundMetricsHtml(rc)}
            </div>
          </div>

          <div class="ledger-round-details ${isOpen ? 'open' : ''}" id="ledger-details-${rc.roundIndex}">
            <!-- Barra d'ajust ràpid d'aquesta jornada -->
            <div class="round-customizer-toolbar">
              <div class="round-customizer-info">
                <span class="round-customizer-tag">🎯 PERSONALITZAR AQUESTA JORNADA (${rc.competitionId} J${rc.jornada})</span>
                <span class="round-customizer-desc">Ajusta els imports exclusivament per a aquesta jornada i simula què hagués passat:</span>
              </div>
              <div class="round-customizer-controls">
                <div class="round-input-chip">
                  <span>🛡️ Segura:</span>
                  <input type="number" id="round-input-safe-${rc.roundIndex}" value="${curSafe}" min="0" step="1" />
                  <span>€</span>
                </div>
                <div class="round-input-chip">
                  <span>⚖️ Semi:</span>
                  <input type="number" id="round-input-semi-${rc.roundIndex}" value="${curSemi}" min="0" step="1" />
                  <span>€</span>
                </div>
                <div class="round-input-chip">
                  <span>🚀 Arriscada:</span>
                  <input type="number" id="round-input-risky-${rc.roundIndex}" value="${curRisky}" min="0" step="1" />
                  <span>€</span>
                </div>
                <button type="button" class="btn-round-apply" onclick="applyRoundUnits(${rc.roundIndex})" title="Aplica aquests imports només a aquesta jornada">
                  ⚡ Aplicar a la Jornada
                </button>
              </div>
            </div>

            <!-- Llista de combinades de la jornada -->
            <div class="round-combos-ledger-list">
              ${(origRound.combos || []).map(cb => {
                const cm = rc.combosCalc.find(c => c.comboId === cb.id) || calculateComboMetrics(cb);
                const cat = cm.category;
                const pillClass = cat === 'safe' ? 'pill-safe' : (cat === 'semi' ? 'pill-semi' : 'pill-risky');
                const catLabel = cat === 'safe' ? '🛡️ Segura' : (cat === 'semi' ? '⚖️ Semi-Arriscada' : '🚀 Arriscada');
                const userStatus = getComboUserStatus(cb);

                return `
                  <div class="ledger-combo-card" id="combo-card-${cb.id}">
                    <div class="ledger-combo-header">
                      <div class="ledger-combo-main-info">
                        <span class="combo-profile-pill ${pillClass}">${catLabel} (${cb.profile})</span>
                        <span class="combo-odd-tag">Cuota @${cm.odd.toFixed(2)}</span>
                      </div>

                      <div class="ledger-combo-inputs-bar">
                        <div class="combo-stake-field">
                          <label for="stake-input-${cb.id}">Aposta:</label>
                          <div class="combo-num-input-wrap">
                            <input type="number"
                                   id="stake-input-${cb.id}"
                                   class="combo-stake-input"
                                   value="${cm.stake}"
                                   min="0"
                                   step="1"
                                   oninput="onComboStakeChange('${cb.id}', this.value)" />
                            <span>€</span>
                          </div>
                        </div>

                        <div class="combo-sim-field">
                          <label for="sim-select-${cb.id}">Simular:</label>
                          <select id="sim-select-${cb.id}"
                                  class="combo-sim-select"
                                  onchange="onComboStatusChange('${cb.id}', this.value)">
                            <option value="REAL" ${userStatus === 'REAL' ? 'selected' : ''}>Real (${cb.status})</option>
                            <option value="WON" ${userStatus === 'WON' ? 'selected' : ''}>🟢 Guanyada</option>
                            <option value="LOST" ${userStatus === 'LOST' ? 'selected' : ''}>🔴 Perduda</option>
                            <option value="PENDING" ${userStatus === 'PENDING' ? 'selected' : ''}>🟡 Pendent</option>
                          </select>
                        </div>

                        <div class="combo-badge-container" id="combo-badge-${cb.id}">
                          ${renderLedgerComboBadgeHtml(cm)}
                        </div>
                      </div>
                    </div>

                    <!-- Legs de la combinada -->
                    <ul class="combo-legs-mini-list">
                      ${(cb.legs || []).map(l => {
                        let resClass = 'res-pending';
                        if (l.actual_result === 'ENCERTADA') resClass = 'res-won';
                        else if (l.actual_result === 'FALLADA') resClass = 'res-lost';

                        return `
                          <li class="combo-leg-mini-item">
                            <span class="leg-mini-matchup">${l.matchup}: <strong>${l.selection_name}</strong> (@${l.bookie_odd ? l.bookie_odd.toFixed(2) : '-'})</span>
                            <span class="leg-mini-result ${resClass}">${l.actual_result || 'Pendent'}</span>
                          </li>
                        `;
                      }).join('')}
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

  // 7. Enllaçar botons del panell superior (només un cop)
  const btnApplyAll = document.getElementById('btn-apply-all-stakes');
  if (btnApplyAll && !btnApplyAll.dataset.bound) {
    btnApplyAll.dataset.bound = 'true';
    btnApplyAll.addEventListener('click', applyAllStakes);
  }

  const btnReset = document.getElementById('btn-reset-default-stakes');
  if (btnReset && !btnReset.dataset.bound) {
    btnReset.dataset.bound = 'true';
    btnReset.addEventListener('click', resetDefaultStakes);
  }

  const btnBannerReset = document.getElementById('btn-banner-reset');
  if (btnBannerReset && !btnBannerReset.dataset.bound) {
    btnBannerReset.dataset.bound = 'true';
    btnBannerReset.addEventListener('click', resetDefaultStakes);
  }

  // Enllaçar inputs d'unitats globals perquè actualitzin reactivament el simulador i les combinades
  ['sim-unit-safe', 'sim-unit-semi', 'sim-unit-risky'].forEach(id => {
    const inp = document.getElementById(id);
    if (inp && !inp.dataset.bound) {
      inp.dataset.bound = 'true';
      const handleLiveInput = () => {
        const valSafe = Math.max(0, parseFloat(document.getElementById('sim-unit-safe')?.value || 25) || 0);
        const valSemi = Math.max(0, parseFloat(document.getElementById('sim-unit-semi')?.value || 10) || 0);
        const valRisky = Math.max(0, parseFloat(document.getElementById('sim-unit-risky')?.value || 5) || 0);

        bankrollSimState.globalUnits = {
          safe: valSafe,
          semi: valSemi,
          risky: valRisky
        };
        saveBankrollSimState();
        const curCalc = calculateBankrollSimulation();
        updateBankrollDom(curCalc);
        renderCombos();
      };
      inp.addEventListener('input', handleLiveInput);
      inp.addEventListener('change', handleLiveInput);
    }
  });

  // 8. Actualitzar banner d'estat
  const banner = document.getElementById('sim-status-banner');
  const bannerText = document.getElementById('sim-status-banner-text');
  if (banner && bannerText) {
    if (calc.isCustomized) {
      banner.style.display = 'flex';
      bannerText.innerHTML = `<strong>Simulació activa amb imports personalitzats:</strong> Balanç projectat: <strong>${calc.kpis.netPnl >= 0 ? '+' : ''}${calc.kpis.netPnl.toFixed(2)} €</strong> (${calc.kpis.roiPct >= 0 ? '+' : ''}${calc.kpis.roiPct.toFixed(1)}% ROI).`;
    } else {
      banner.style.display = 'none';
    }
  }
}

// Handler per canviar l'aposta d'una combinada individual
window.onComboStakeChange = function(comboId, val) {
  const numericVal = parseFloat(val);
  const stake = !isNaN(numericVal) && numericVal >= 0 ? numericVal : 0;

  if (!bankrollSimState.comboOverrides[comboId]) {
    bankrollSimState.comboOverrides[comboId] = {};
  }
  bankrollSimState.comboOverrides[comboId].stake = stake;

  saveBankrollSimState();
  const calc = calculateBankrollSimulation();
  updateBankrollDom(calc);
  renderCombos();
};

// Handler per canviar l'estat d'una combinada (Real / Guanyada / Perduda / Pendent)
window.onComboStatusChange = function(comboId, status) {
  if (!bankrollSimState.comboOverrides[comboId]) {
    bankrollSimState.comboOverrides[comboId] = {};
  }
  bankrollSimState.comboOverrides[comboId].simStatus = status;

  saveBankrollSimState();
  const calc = calculateBankrollSimulation();
  updateBankrollDom(calc);
};

// Handler per aplicar imports específics a tota una jornada concreta
window.applyRoundUnits = function(roundIdx) {
  const bData = appData && appData.bankroll_simulation;
  if (!bData || !bData.rounds_history || !bData.rounds_history[roundIdx]) return;

  const round = bData.rounds_history[roundIdx];
  const inputSafe = document.getElementById(`round-input-safe-${roundIdx}`);
  const inputSemi = document.getElementById(`round-input-semi-${roundIdx}`);
  const inputRisky = document.getElementById(`round-input-risky-${roundIdx}`);

  const valSafe = Math.max(0, parseFloat(inputSafe ? inputSafe.value : 25) || 0);
  const valSemi = Math.max(0, parseFloat(inputSemi ? inputSemi.value : 10) || 0);
  const valRisky = Math.max(0, parseFloat(inputRisky ? inputRisky.value : 5) || 0);

  (round.combos || []).forEach(cb => {
    const cat = getComboCategory(cb);
    let chosenVal = valRisky;
    if (cat === 'safe') chosenVal = valSafe;
    else if (cat === 'semi') chosenVal = valSemi;

    if (!bankrollSimState.comboOverrides[cb.id]) {
      bankrollSimState.comboOverrides[cb.id] = {};
    }
    bankrollSimState.comboOverrides[cb.id].stake = chosenVal;

    // Actualitzar l'input de la combinada directament al DOM
    const comboInput = document.getElementById(`stake-input-${cb.id}`);
    if (comboInput) comboInput.value = chosenVal;
  });

  saveBankrollSimState();
  const calc = calculateBankrollSimulation();
  updateBankrollDom(calc);
  renderCombos();

  // Animació visual breu de confirmació a la targeta de la jornada
  const card = document.getElementById(`ledger-round-card-${roundIdx}`);
  if (card) {
    card.style.outline = '2px solid var(--accent-amber)';
    setTimeout(() => { card.style.outline = 'none'; }, 800);
  }
};

// Handler global: Aplicar imports base a TOTES les jornades
function applyAllStakes() {
  const inputSafe = document.getElementById('sim-unit-safe');
  const inputSemi = document.getElementById('sim-unit-semi');
  const inputRisky = document.getElementById('sim-unit-risky');

  const valSafe = Math.max(0, parseFloat(inputSafe ? inputSafe.value : 25) || 0);
  const valSemi = Math.max(0, parseFloat(inputSemi ? inputSemi.value : 10) || 0);
  const valRisky = Math.max(0, parseFloat(inputRisky ? inputRisky.value : 5) || 0);

  bankrollSimState.globalUnits = {
    safe: valSafe,
    semi: valSemi,
    risky: valRisky
  };

  // Netejar sobreescriptures individuals de stake perquè adoptin la unitat global
  Object.keys(bankrollSimState.comboOverrides).forEach(id => {
    delete bankrollSimState.comboOverrides[id].stake;
    if (Object.keys(bankrollSimState.comboOverrides[id]).length === 0) {
      delete bankrollSimState.comboOverrides[id];
    }
  });

  saveBankrollSimState();
  renderBankroll();
  renderCombos();

  // Animació / feedback visual
  const btn = document.getElementById('btn-apply-all-stakes');
  if (btn) {
    const originalText = btn.innerHTML;
    btn.innerHTML = `<span>✅ Aplicat a totes les jornades!</span>`;
    btn.style.background = 'linear-gradient(135deg, #059669, #10b981)';
    setTimeout(() => {
      btn.innerHTML = originalText;
      btn.style.background = '';
    }, 1600);
  }
}

// Handler global: Restablir imports originals (25€ / 10€ / 5€)
function resetDefaultStakes() {
  bankrollSimState.globalUnits = {
    safe: 25.0,
    semi: 10.0,
    risky: 5.0
  };
  bankrollSimState.comboOverrides = {};

  try {
    localStorage.removeItem('prediccions_bankroll_sim');
  } catch (e) {}

  renderBankroll();
  renderCombos();

  const btn = document.getElementById('btn-reset-default-stakes');
  if (btn) {
    const orig = btn.innerHTML;
    btn.innerHTML = `<span>✅ Valors restablerts!</span>`;
    setTimeout(() => { btn.innerHTML = orig; }, 1400);
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

  const allEntries = appData.changelog || [];
  if (allEntries.length === 0) {
    container.innerHTML = `<p style="text-align: center; padding: 40px; color: var(--text-muted);">No hi ha novetats registrades.</p>`;
    return;
  }

  // Filtrar estrictament els darrers 7 dies per no col·lapsar la pestanya de novetats
  const latestDateStr = allEntries[0]?.date || new Date().toISOString().split('T')[0];
  const refDate = new Date(latestDateStr);
  const cutoffDate = new Date(refDate.getTime() - 7 * 24 * 60 * 60 * 1000);
  const cutoffDateStr = cutoffDate.toISOString().split('T')[0];

  let entries = allEntries.filter(e => {
    if (!e.date) return false;
    return e.date >= cutoffDateStr;
  });

  if (entries.length === 0) {
    entries = allEntries.slice(0, 3);
  }

  const html = `
    <div class="timeline-container">
      <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; flex-wrap: wrap; gap: 10px;">
        <div style="display: inline-flex; align-items: center; gap: 8px; padding: 6px 14px; background: rgba(56, 189, 248, 0.08); border: 1px solid rgba(56, 189, 248, 0.2); border-radius: 20px; font-size: 12px; color: var(--accent-cyan);">
          <span>📅</span> <strong>Finestra activa:</strong> Darrers 7 dies (${entries.length} actualitzacions)
        </div>
        <span style="font-size: 11.5px; color: var(--text-muted); font-family: var(--font-mono);">Historial antic arxivat per no sobrecarregar</span>
      </div>
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

        const matchesList = entry.matches || [];
        const combosList = entry.combos_evaluated || [];

        let matchesHtml = '';
        if (matchesList.length > 0) {
          matchesHtml = `
            <div class="timeline-section-title">⚽ Partits Ingestats amb Detall (${matchesList.length})</div>
            <div class="timeline-matches-grid">
              ${matchesList.map(m => {
                const hGoals = m.home_goals != null ? m.home_goals : '-';
                const aGoals = m.away_goals != null ? m.away_goals : '-';
                const xgH = m.home_xg != null ? Number(m.home_xg).toFixed(2) : '-';
                const xgA = m.away_xg != null ? Number(m.away_xg).toFixed(2) : '-';
                const cards = (m.home_yellow_cards || 0) + (m.away_yellow_cards || 0) + (m.home_red_cards || 0) + (m.away_red_cards || 0);
                const corners = (m.home_corners || 0) + (m.away_corners || 0);
                const eloDiffH = m.elo_change_home != null ? m.elo_change_home : 0;
                const eloDiffA = m.elo_change_away != null ? m.elo_change_away : 0;

                return `
                  <div class="timeline-match-card">
                    <div class="tm-top">
                      <span class="tm-league-badge">${m.competition_id || ''} · J${m.jornada || ''}</span>
                      <span>${m.date || ''}</span>
                    </div>
                    <div class="tm-score-row">
                      <span class="tm-team home" title="${m.home_team}">${m.home_team}</span>
                      <span class="tm-score-badge">${m.score || `${hGoals} - ${aGoals}`}</span>
                      <span class="tm-team away" title="${m.away_team}">${m.away_team}</span>
                    </div>
                    <div class="tm-stats-row">
                      <span class="tm-stat-pill">📊 xG: ${xgH} - ${xgA}</span>
                      <span class="tm-stat-pill">🟨 ${cards}</span>
                      <span class="tm-stat-pill">🚩 ${corners}</span>
                      <span class="tm-stat-pill">⚖️ ${m.referee || 'CTA/PGMOL'}</span>
                    </div>
                    <div class="tm-elo-row">
                      <span class="tm-elo-pill ${eloDiffH >= 0 ? 'tm-elo-pos' : 'tm-elo-neg'}">
                        ${m.home_team}: ${eloDiffH >= 0 ? '+' : ''}${eloDiffH} ${m.new_elo_home ? `(${m.new_elo_home})` : ''}
                      </span>
                      <span class="tm-elo-pill ${eloDiffA >= 0 ? 'tm-elo-pos' : 'tm-elo-neg'}">
                        ${m.away_team}: ${eloDiffA >= 0 ? '+' : ''}${eloDiffA} ${m.new_elo_away ? `(${m.new_elo_away})` : ''}
                      </span>
                    </div>
                  </div>
                `;
              }).join('')}
            </div>
          `;
        }

        let combosHtml = '';
        if (combosList.length > 0) {
          combosHtml = `
            <div class="timeline-section-title">📈 Combinades Avaluades (${combosList.length})</div>
            <div class="timeline-combos-grid">
              ${combosList.map(c => {
                const isWon = c.status === 'WON';
                const pnl = c.profit != null ? c.profit : (isWon ? (c.payout - c.stake) : -c.stake);
                const pnlSign = pnl >= 0 ? '+' : '';
                return `
                  <div class="timeline-combo-pill ${isWon ? 'won' : 'lost'}">
                    <span>${isWon ? '🏆' : '❌'}</span>
                    <strong>[${c.competition_id || ''} J${c.jornada || ''}] ${c.profile || 'Combinada'}</strong>
                    <span>@${c.odd ? Number(c.odd).toFixed(2) : '-'}</span>
                    <span>(${pnlSign}${Number(pnl).toFixed(2)} €)</span>
                  </div>
                `;
              }).join('')}
            </div>
          `;
        }

        const refUpdates = entry.referee_updates || [];
        let refereesHtml = '';
        if (refUpdates.length > 0) {
          refereesHtml = `
            <div class="timeline-section-title">⚖️ Designacions Arbitrals Confirmades (${refUpdates.length})</div>
            <div class="timeline-referees-grid">
              ${refUpdates.map(ru => {
                const cName = ru.competition_name || ru.competition_id || '';
                const jNum = ru.jornada ? ` · J${ru.jornada}` : '';
                const pRef = ru.prev_referee || 'Pendent CTA';
                const nRef = ru.new_referee || 'Oficial';
                const ch = ru.changes || {};
                const cOver = ch.cards_over_45 || {};
                const rProb = ch.red_card_prob || {};
                const fExp = ch.fouls_exp || {};
                const hProb = ch.home_win_prob || {};
                const note = ch.summary || '';

                const dCards = cOver.delta != null ? (cOver.delta >= 0 ? `+${cOver.delta.toFixed(1)}% 📈` : `${cOver.delta.toFixed(1)}% 📉`) : '-';
                const dRed = rProb.delta != null ? (rProb.delta >= 0 ? `+${rProb.delta.toFixed(1)}% 📈` : `${rProb.delta.toFixed(1)}% 📉`) : '-';
                const dHome = hProb.delta != null ? (hProb.delta >= 0 ? `+${hProb.delta.toFixed(1)}% 📈` : `${hProb.delta.toFixed(1)}% 📉`) : '-';

                return `
                  <div class="timeline-referee-card">
                    <div class="tr-top">
                      <span class="tr-badge">⚖️ ${cName}${jNum}</span>
                      <span>${ru.date || ''}</span>
                    </div>
                    <div class="tr-matchup">${ru.matchup || 'Partit'}</div>
                    <div class="tr-ref-row">
                      <span class="tr-ref-old">${pRef}</span>
                      <span class="tr-ref-arrow">➔</span>
                      <span class="tr-ref-new">${nRef}</span>
                    </div>
                    <div class="tr-metrics-grid">
                      <div class="tr-metric-box">
                        <div class="tr-metric-label">Targetes &gt; 4.5</div>
                        <div class="tr-metric-val ${cOver.delta >= 0 ? 'up' : 'down'}">
                          <span>${cOver.before || 50}% ➔ ${cOver.after || 50}%</span>
                          <span>${dCards}</span>
                        </div>
                      </div>
                      <div class="tr-metric-box">
                        <div class="tr-metric-label">Expulsió / Vermella</div>
                        <div class="tr-metric-val ${rProb.delta >= 0 ? 'up' : 'down'}">
                          <span>${rProb.before || 20}% ➔ ${rProb.after || 20}%</span>
                          <span>${dRed}</span>
                        </div>
                      </div>
                      <div class="tr-metric-box">
                        <div class="tr-metric-label">Faltes Esperades</div>
                        <div class="tr-metric-val">
                          <span>${fExp.before || 24.5} ➔ ${fExp.after || 26.0}</span>
                          <span style="color: var(--accent-amber);">${fExp.delta >= 0 ? '+' : ''}${fExp.delta || 0}</span>
                        </div>
                      </div>
                      <div class="tr-metric-box">
                        <div class="tr-metric-label">Biaix Victòria Local</div>
                        <div class="tr-metric-val ${hProb.delta >= 0 ? 'up' : 'down'}">
                          <span>${hProb.before || 33}% ➔ ${hProb.after || 33}%</span>
                          <span>${dHome}</span>
                        </div>
                      </div>
                    </div>
                    ${note ? `<div class="tr-note">${note}</div>` : ''}
                  </div>
                `;
              }).join('')}
            </div>
          `;
        }

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
              ${matchesHtml}
              ${refereesHtml}
              ${combosHtml}
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

  const btnAutoPc = document.getElementById('qa-btn-auto-pc');
  if (btnAutoPc) {
    btnAutoPc.href = getAutofillUrl(combo);
  }

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

function getAutofillUrl(combo) {
  if (!combo) return 'https://www.winamax.es/apuestas-deportivas';

  const payload = {
    profile: combo.profile || 'Combinada',
    odd: (combo.boosted_odd || combo.combined_odd || 0).toFixed(2),
    boosted_odd: combo.boosted_odd || combo.combined_odd,
    booster_pct: combo.booster_pct || 0,
    legs: (combo.legs || []).map(l => ({
      matchup: l.matchup,
      selection: l.selection_name,
      selection_name: l.selection_name,
      category: l.category || '',
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

  return `${initialUrl}#combo_autofill=${hash}&leg_idx=0`;
}

function openWinamaxAutofill(comboKey) {
  const combo = window.combosRegistry[comboKey];
  if (!combo) return;
  const targetUrl = getAutofillUrl(combo);
  window.open(targetUrl, '_blank');
}

function launchTestComboAutofill() {
  const keys = Object.keys(window.combosRegistry || {});
  if (keys.length > 0) {
    openWinamaxAutofill(keys[0]);
  } else {
    switchTab('combos');
  }
}

// Exportar globals per als controladors en línia
window.getAutofillUrl = getAutofillUrl;
window.openQuickAssistant = openQuickAssistant;
window.closeQuickAssistant = closeQuickAssistant;
window.handleLegClick = handleLegClick;
window.toggleLegChecked = toggleLegChecked;
window.copyModalComboSummary = copyModalComboSummary;
window.openModalAllMatches = openModalAllMatches;
window.openWinamaxAutofill = openWinamaxAutofill;
window.launchTestComboAutofill = launchTestComboAutofill;
window.onJornadaChange = onJornadaChange;
window.UserAuth = UserAuth;

