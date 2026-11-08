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

if (typeof window === 'undefined') {
  global.window = {};
}
if (typeof localStorage === 'undefined') {
  global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
}

let appData = null;
let currentTab = 'novetats';
let currentLeague = 'ALL'; // Deprecated global fallback
let selectedLeagueRankings = 'LALIGA';
let selectedLeaguePredictions = 'ALL';
let selectedLeagueValue = 'ALL';
let selectedLeagueCombos = 'DAILY';
let currentSearch = '';
let currentMatchFilter = 'ALL';
let activeModalComboKey = null;
window.combosRegistry = {};

try {
  const lr = localStorage.getItem('prediccions_league_rankings');
  if (lr && lr !== 'ALL' && lr !== 'DAILY') selectedLeagueRankings = lr;
  const lp = localStorage.getItem('prediccions_league_predictions');
  if (lp) selectedLeaguePredictions = lp;
  const lv = localStorage.getItem('prediccions_league_value');
  if (lv) selectedLeagueValue = lv;
  const lc = localStorage.getItem('prediccions_league_combos');
  if (lc) selectedLeagueCombos = lc;
} catch (e) { }

// Estat de la casa d'apostes seleccionada (WINAMAX vs BET365)
let currentBookie = 'WINAMAX';
try {
  const savedBookie = localStorage.getItem('prediccions_bookie');
  if (savedBookie === 'BET365' || savedBookie === 'WINAMAX') {
    currentBookie = savedBookie;
  }
} catch (e) { }

function setBookie(bookie) {
  currentBookie = (bookie === 'BET365') ? 'BET365' : 'WINAMAX';
  try {
    localStorage.setItem('prediccions_bookie', currentBookie);
  } catch (e) { }

  const btnW = document.getElementById('btn-bookie-winamax');
  const btnB = document.getElementById('btn-bookie-bet365');
  if (btnW && btnB) {
    if (currentBookie === 'BET365') {
      btnW.classList.remove('active');
      btnB.classList.add('active');
    } else {
      btnW.classList.add('active');
      btnB.classList.remove('active');
    }
  }

  // Re-renderitzar seccions afectades
  if (appData) {
    renderPredictions();
    renderValueBets();
    renderCombos();
  }
  showToast(`Casa d'apostes activa: ${currentBookie === 'BET365' ? '🟢 Bet365' : '🔴 Winamax'}`, 'info');
}
window.setBookie = setBookie;

function getValidBet365Url(url) {
  if (!url || typeof url !== 'string' || url.includes('/D1002/')) {
    return 'https://www.bet365.es/#/AS/B1/';
  }
  return url;
}
window.getValidBet365Url = getValidBet365Url;

function cleanSelectionName(str) {
  if (!str) return '';
  if (typeof str !== 'string') return String(str);
  return str.replace(/\{['"]id['"]:\s*['"][^'"]*['"],\s*['"]name['"]:\s*['"]([^'"]*)['"]\}/g, '$1');
}
window.cleanSelectionName = cleanSelectionName;

function cleanMatchup(matchup, leg) {
  if (!matchup || typeof matchup !== 'string' || ['partit', 'partit sense nom', 'partit únic'].includes(matchup.trim().toLowerCase())) {
    if (leg) {
      if (leg.home_team && leg.away_team) {
        return `${leg.home_team} vs ${leg.away_team}`;
      }
      if (leg.home_team_id && leg.away_team_id) {
        return `${leg.home_team_id} vs ${leg.away_team_id}`;
      }
      const sel = (leg.selection_name || leg.name || '').toLowerCase();
      if (sel.includes('levante') || sel.includes('sevilla')) {
        return 'Levante UD vs Sevilla FC';
      }
    }
    return 'Pendent de resoldre';
  }
  return matchup.replace(/\{['"]id['"]:\s*['"][^'"]*['"],\s*['"]name['"]:\s*['"]([^'"]*)['"]\}/g, '$1');
}
window.cleanMatchup = cleanMatchup;

function parseMatchDateToMs(dateStr) {
  if (!dateStr || typeof dateStr !== 'string') return 9999999999999;
  const s = dateStr.trim();
  // Format DD.MM.YYYY HH:mm o DD/MM/YYYY HH:mm
  const dmyMatch = s.match(/^(\d{1,2})[./-](\d{1,2})[./-](\d{4})(?:\s+(\d{1,2}):(\d{2}))?/);
  if (dmyMatch) {
    const day = parseInt(dmyMatch[1], 10);
    const month = parseInt(dmyMatch[2], 10) - 1;
    const year = parseInt(dmyMatch[3], 10);
    const hour = dmyMatch[4] ? parseInt(dmyMatch[4], 10) : 0;
    const minute = dmyMatch[5] ? parseInt(dmyMatch[5], 10) : 0;
    return new Date(year, month, day, hour, minute).getTime();
  }
  // Format YYYY-MM-DD HH:mm o YYYY-MM-DDTHH:mm
  const ymdMatch = s.match(/^(\d{4})[./-](\d{1,2})[./-](\d{1,2})(?:[T\s](\d{1,2}):(\d{2}))?/);
  if (ymdMatch) {
    const year = parseInt(ymdMatch[1], 10);
    const month = parseInt(ymdMatch[2], 10) - 1;
    const day = parseInt(ymdMatch[3], 10);
    const hour = ymdMatch[4] ? parseInt(ymdMatch[4], 10) : 0;
    const minute = ymdMatch[5] ? parseInt(ymdMatch[5], 10) : 0;
    return new Date(year, month, day, hour, minute).getTime();
  }
  const parsed = Date.parse(s);
  return isNaN(parsed) ? 9999999999999 : parsed;
}
window.parseMatchDateToMs = parseMatchDateToMs;

function copyBet365Slip(comboKey) {
  const combo = window.combosRegistry[comboKey];
  if (!combo) return;
  const odd = combo.combined_odd_bet365 || combo.boosted_odd || combo.combined_odd || 1.0;
  const url = getValidBet365Url(combo.bet365_url);
  const legs = combo.legs || [];

  let text = `🟢 BET365 · ${combo.profile}\n`;
  text += `Cuota Total Bet365: @${Number(odd).toFixed(2)}\n`;
  text += `Enllaç Directe Bet365: ${url}\n\n`;
  text += `SELECCIONS DEL CUPÓ:\n`;
  legs.forEach((leg, i) => {
    const o = leg.bookie_odd_bet365 != null ? leg.bookie_odd_bet365.toFixed(2) : (leg.bookie_odd != null ? leg.bookie_odd.toFixed(2) : '-');
    text += `${i + 1}. [${cleanMatchup(leg.matchup, leg)}] 👉 ${cleanSelectionName(leg.selection_name)}\n`;
  });

  navigator.clipboard.writeText(text).then(() => {
    showToast("📋 Enllaç i cupó de Bet365 copiats al porta-retalls!", "success");
  }).catch(() => {
    showToast("No s'ha pogut copiar automàticament.", "warning");
  });
}
window.copyBet365Slip = copyBet365Slip;

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
  } catch (e) { }
}

let selectedJornadaPred = {};
let selectedJornadaCombo = {};

window.onJornadaChange = function (type, val) {
  let jVal = val;
  if (val !== 'ACTIVE' && typeof val === 'string' && !val.includes('-')) {
    const parsed = parseInt(val, 10);
    if (!isNaN(parsed)) jVal = parsed;
  }
  if (type === 'predictions') {
    selectedJornadaPred[selectedLeaguePredictions] = jVal;
    UserAuth.saveCurrentPreference(`jornada_pred_${selectedLeaguePredictions}`, jVal);
    renderPredictions();
  } else if (type === 'combos') {
    selectedJornadaCombo[selectedLeagueCombos] = jVal;
    UserAuth.saveCurrentPreference(`jornada_combo_${selectedLeagueCombos}`, jVal);
    renderCombos();
  }
};

function onLeagueChange(section, league) {
  if (section === 'rankings') {
    selectedLeagueRankings = league;
    try { localStorage.setItem('prediccions_league_rankings', league); } catch (e) { }
    renderPowerRankings();
  } else if (section === 'predictions') {
    selectedLeaguePredictions = league;
    try { localStorage.setItem('prediccions_league_predictions', league); } catch (e) { }
    renderPredictions();
  } else if (section === 'value') {
    selectedLeagueValue = league;
    try { localStorage.setItem('prediccions_league_value', league); } catch (e) { }
    renderValueBets();
  } else if (section === 'combos') {
    selectedLeagueCombos = league;
    try { localStorage.setItem('prediccions_league_combos', league); } catch (e) { }
    renderCombos();
  }
}
window.onLeagueChange = onLeagueChange;

// Memòria cau local resilient per a combinades (evita que desapareguin durant canvis de jornada o càrrega)
window._cachedCombos = {};
try {
  const sc = localStorage.getItem('prediccions_valid_combos');
  if (sc) window._cachedCombos = JSON.parse(sc);
} catch (e) { }

window.setMatchesFilter = function (status) {
  currentMatchFilter = status;
  UserAuth.saveCurrentPreference('matchFilter', status);
  document.querySelectorAll('.match-filter-pill').forEach(btn => {
    btn.classList.toggle('active', btn.getAttribute('data-match-filter') === status);
  });
  renderPredictions();
};

if (typeof document !== 'undefined' && document.addEventListener) {
  document.addEventListener('DOMContentLoaded', async () => {
    await UserAuth.init();
    initEventListeners();
    loadData();
  });
}

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

  // League Selector Pills (Legacy / Safe fallback)
  const leagueButtons = document.querySelectorAll('.pill-btn');
  if (leagueButtons && leagueButtons.length > 0) {
    leagueButtons.forEach(btn => {
      btn.addEventListener('click', () => {
        leagueButtons.forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        currentLeague = btn.getAttribute('data-league');
        UserAuth.saveCurrentPreference('favoriteLeague', currentLeague);
        if (currentLeague === 'DAILY' && currentTab !== 'combos') {
          switchTab('combos');
        }
        applyFilters();
      });
    });
  }

  // Search Box
  const searchInput = document.getElementById('global-search');
  if (searchInput) {
    searchInput.addEventListener('input', (e) => {
      currentSearch = e.target.value.toLowerCase().trim();
      applyFilters();
    });
  }

  // Bookie Selector Toggle Sync
  const btnBookieW = document.getElementById('btn-bookie-winamax');
  const btnBookieB = document.getElementById('btn-bookie-bet365');
  if (btnBookieW && btnBookieB) {
    if (currentBookie === 'BET365') {
      btnBookieW.classList.remove('active');
      btnBookieB.classList.add('active');
    } else {
      btnBookieW.classList.add('active');
      btnBookieB.classList.remove('active');
    }
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

  // Arbitrage Stake Calculator Listeners
  const arbStakeInput = document.getElementById('arb-total-stake');
  const arbBtnRecalc = document.getElementById('arb-btn-recalc');
  if (arbStakeInput) {
    arbStakeInput.addEventListener('input', (e) => {
      const val = parseFloat(e.target.value);
      if (val && val > 0) {
        currentArbTotalStake = normalizeBankroll(val);
        renderArbitrage();
      }
    });
    arbStakeInput.addEventListener('change', (e) => {
      const val = parseFloat(e.target.value);
      if (val && val > 0) {
        currentArbTotalStake = normalizeBankroll(val);
        arbStakeInput.value = currentArbTotalStake;
        renderArbitrage();
      }
    });
    arbStakeInput.addEventListener('blur', (e) => {
      const val = parseFloat(e.target.value);
      if (val && val > 0) {
        currentArbTotalStake = normalizeBankroll(val);
        arbStakeInput.value = currentArbTotalStake;
      }
    });
  }
  if (arbBtnRecalc) {
    arbBtnRecalc.addEventListener('click', () => {
      if (arbStakeInput) {
        const val = parseFloat(arbStakeInput.value);
        if (val && val > 0) {
          currentArbTotalStake = normalizeBankroll(val);
          arbStakeInput.value = currentArbTotalStake;
        }
      }
      renderArbitrage();
      showToast(`Apostes recalculades per a ${currentArbTotalStake}€ d'inversió total!`, 'info');
    });
  }

  // Surebet Agent Controls (Local Windows Monitor)
  setupSurebetAgentControls();
}

// Gestió de l'Agent Windows per a Surebets (Mode Supabase Remot vs Mode Desenvolupament Local Explícit)
let _activeSurebetSession = null;
let _surebetSessionTimerInterval = null;
let _surebetPollInterval = null;
const LOCAL_BRIDGE_URL = 'http://127.0.0.1:8777';

// El pont local només és admissible si es compleixen TOTES les condicions de seguretat:
// 1. Hostname és estrictament loopback local ('localhost' o '127.0.0.1') sota protocol HTTP (bloqueig total a Vercel/producció).
// 2. Prohibició absoluta de vectors controlats per visitants: ni paràmetres URL (?local_bridge, ?dev) ni localStorage.
// 3. Requereix una configuració de compilació/test explícitament immutable (Object.defineProperty amb writable: false i configurable: false).
function isDevBridgeExplicitlyEnabled() {
  if (typeof window === 'undefined' || !window.location) return false;

  // 1. Bloqueig d'origen: mai permès fora de loopback local (rebuig immediat de dominis públics o Vercel)
  const host = window.location.hostname;
  if (host !== 'localhost' && host !== '127.0.0.1') return false;

  // 2. Bloqueig de protocol: en HTTPS (producció) mai s'admet connectivitat loopback
  if (window.location.protocol !== 'http:') return false;

  // 3. Verificació de configuració de compilació/test immutable (no modificable per visitants)
  try {
    const desc = Object.getOwnPropertyDescriptor(window, '__PREDICCIONS_DEV_BUILD__');
    if (desc && desc.configurable === false && desc.writable === false && desc.value === true) {
      return true;
    }
  } catch (e) { }

  return false;
}

function setupSurebetAgentControls() {
  const btnStart = document.getElementById('btn-start-surebet-search');
  const btnStop = document.getElementById('btn-stop-surebet-search');
  const durationSelect = document.getElementById('surebet-duration-select');
  const stateVal = document.getElementById('surebet-job-state');
  const timerVal = document.getElementById('surebet-job-timer');
  const oppsVal = document.getElementById('surebet-job-opps');
  const idVal = document.getElementById('surebet-job-id');
  const agentBadgeText = document.getElementById('agent-connection-status-text');
  const pulseDot = document.getElementById('agent-pulse-dot');

  // Elements Auth Operador
  const operatorLoginForm = document.getElementById('operator-login-form');
  const operatorUserInfo = document.getElementById('operator-user-info');
  const operatorActiveEmail = document.getElementById('operator-active-email');
  const operatorEmailInput = document.getElementById('operator-email-input');
  const operatorPasswordInput = document.getElementById('operator-password-input');
  const btnOperatorLogin = document.getElementById('btn-operator-login');
  const btnOperatorLogout = document.getElementById('btn-operator-logout');
  const operatorLoginError = document.getElementById('operator-login-error');

  if (!btnStart || !btnStop) return;

  let isAgentOnline = false;

  // -------------------------------------------------------------
  // Gestió de Sessió d'Operador (Supabase Auth)
  // -------------------------------------------------------------
  function updateOperatorAuthUI(userEmail) {
    if (userEmail) {
      if (operatorLoginForm) operatorLoginForm.style.display = 'none';
      if (operatorUserInfo) operatorUserInfo.style.display = 'flex';
      if (operatorActiveEmail) operatorActiveEmail.textContent = userEmail;
      if (operatorLoginError) operatorLoginError.style.display = 'none';
    } else {
      if (operatorLoginForm) operatorLoginForm.style.display = 'block';
      if (operatorUserInfo) operatorUserInfo.style.display = 'none';
      if (operatorActiveEmail) operatorActiveEmail.textContent = '--';
    }
  }

  function initOperatorAuth() {
    try {
      const stored = sessionStorage.getItem('supabase_operator_session');
      if (stored) {
        const sess = JSON.parse(stored);
        if (sess && sess.token && sess.email) {
          if (sess.expires_at && Date.now() > sess.expires_at) {
            sessionStorage.removeItem('supabase_operator_session');
            updateOperatorAuthUI(null);
            return;
          }
          if (window.SUPABASE_CONFIG) {
            window.SUPABASE_CONFIG.userJwt = sess.token;
          }
          updateOperatorAuthUI(sess.email);
          checkAgentHeartbeat();
        }
      }
    } catch (e) {
      console.warn("[Auth] Error restaurant sessió:", e);
    }
  }

  if (btnOperatorLogin) {
    btnOperatorLogin.addEventListener('click', async () => {
      const email = operatorEmailInput ? operatorEmailInput.value.trim() : '';
      const password = operatorPasswordInput ? operatorPasswordInput.value : '';

      if (!email || !password) {
        if (operatorLoginError) {
          operatorLoginError.textContent = "Introdueix correu i contrasenya d'operador.";
          operatorLoginError.style.display = 'block';
        }
        return;
      }

      if (!window.SUPABASE_CONFIG || !window.SUPABASE_CONFIG.url) {
        showToast("Configuració de Supabase no disponible", "error");
        return;
      }

      btnOperatorLogin.disabled = true;
      btnOperatorLogin.textContent = "Autenticant...";
      if (operatorLoginError) operatorLoginError.style.display = 'none';

      try {
        const authUrl = `${window.SUPABASE_CONFIG.url.replace(/\/$/, '')}/auth/v1/token?grant_type=password`;
        const resp = await fetch(authUrl, {
          method: 'POST',
          headers: {
            'apikey': window.SUPABASE_CONFIG.publishableKey,
            'Content-Type': 'application/json'
          },
          body: JSON.stringify({ email, password })
        });

        if (!resp.ok) {
          const errData = await resp.json().catch(() => ({}));
          throw new Error(errData.error_description || errData.message || `Error d'autenticació (${resp.status})`);
        }

        const data = await resp.json();
        const jwt = data.access_token;
        const userEmail = (data.user && data.user.email) ? data.user.email : email;
        const expiresAt = Date.now() + ((data.expires_in || 3600) * 1000);

        window.SUPABASE_CONFIG.userJwt = jwt;
        sessionStorage.setItem('supabase_operator_session', JSON.stringify({
          token: jwt,
          email: userEmail,
          expires_at: expiresAt
        }));

        updateOperatorAuthUI(userEmail);
        showToast(`Operador autenticat: ${userEmail}`, "success");
        checkAgentHeartbeat();

      } catch (err) {
        if (operatorLoginError) {
          operatorLoginError.textContent = err.message || "Error en iniciar sessió.";
          operatorLoginError.style.display = 'block';
        }
        showToast(`Error de login: ${err.message}`, "error");
      } finally {
        btnOperatorLogin.disabled = false;
        btnOperatorLogin.innerHTML = '<span>🔑</span> Iniciar Sessió';
      }
    });
  }

  if (btnOperatorLogout) {
    btnOperatorLogout.addEventListener('click', () => {
      sessionStorage.removeItem('supabase_operator_session');
      if (window.SUPABASE_CONFIG) {
        window.SUPABASE_CONFIG.userJwt = null;
      }
      updateOperatorAuthUI(null);
      isAgentOnline = false;
      if (agentBadgeText) agentBadgeText.textContent = "PC Windows Desconnectat (pendent de configuració / autenticació)";
      if (pulseDot) {
        pulseDot.style.background = '#94a3b8';
        pulseDot.style.boxShadow = 'none';
      }
      updateJobUI(null);
      showToast("Sessió d'operador tancada", "info");
    });
  }

  initOperatorAuth();

  // -------------------------------------------------------------
  // Monitoratge de Batec del PC (Heartbeat)
  // -------------------------------------------------------------
  async function checkAgentHeartbeat() {
    // 1. Mode Supabase Remot (només si NO hi ha un dev bridge local explícitament habilitat)
    if (!isDevBridgeExplicitlyEnabled() && window.SUPABASE_CONFIG && window.SUPABASE_CONFIG.url) {
      if (!window.SUPABASE_CONFIG.userJwt) {
        isAgentOnline = false;
        if (agentBadgeText) agentBadgeText.textContent = "PC Windows Desconnectat (pendent de configuració / autenticació)";
        if (pulseDot) {
          pulseDot.style.background = '#94a3b8';
          pulseDot.style.boxShadow = 'none';
        }
        return;
      }

      try {
        const sbUrl = `${window.SUPABASE_CONFIG.url.replace(/\/$/, '')}/rest/v1/daemon_agents?select=agent_name,is_active,last_heartbeat&order=last_heartbeat.desc.nullslast&limit=1`;
        const headers = {
          'apikey': window.SUPABASE_CONFIG.publishableKey,
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${window.SUPABASE_CONFIG.userJwt}`
        };
        const resp = await fetch(sbUrl, { headers, cache: 'no-store' });
        if (resp.ok) {
          const data = await resp.json();
          if (data && data.length > 0) {
            const agent = data[0];
            const lastHb = agent.last_heartbeat ? new Date(agent.last_heartbeat).getTime() : 0;
            const ageSec = Math.max(0, (Date.now() - lastHb) / 1000);
            if (agent.is_active && ageSec <= 60) {
              isAgentOnline = true;
              if (agentBadgeText) agentBadgeText.textContent = `PC Windows: 🟢 En línia (${agent.agent_name} · fa ${Math.round(ageSec)}s)`;
              if (pulseDot) {
                pulseDot.style.background = '#34d399';
                pulseDot.style.boxShadow = '0 0 8px #34d399';
              }
              if (!_activeSurebetSession || ['COMPLETED', 'CANCELLED', 'FAILED'].includes(_activeSurebetSession.status)) {
                btnStart.disabled = false;
              }
              return;
            } else {
              isAgentOnline = false;
              const ageStr = ageSec > 3600 ? `${Math.round(ageSec/3600)}h` : `${Math.round(ageSec)}s`;
              if (agentBadgeText) agentBadgeText.textContent = `PC Windows: 🔴 Desconnectat (Últim batec fa ${ageStr})`;
              if (pulseDot) {
                pulseDot.style.background = '#ef4444';
                pulseDot.style.boxShadow = 'none';
              }
              return;
            }
          }
        }
      } catch (err) {
        console.warn("[Surebet Agent] Error comprovant batec a Supabase:", err);
      }

      isAgentOnline = false;
      if (agentBadgeText) agentBadgeText.textContent = 'PC Windows: 🔴 Desconnectat (Sense connexió)';
      if (pulseDot) {
        pulseDot.style.background = '#ef4444';
        pulseDot.style.boxShadow = 'none';
      }
      return;
    }

    // 2. Mode Desenvolupament Local: només si està explícitament habilitat
    if (isDevBridgeExplicitlyEnabled()) {
      try {
        const resp = await fetch(`${LOCAL_BRIDGE_URL}/api/agent/status`, { cache: 'no-store' });
        if (resp.ok) {
          const data = await resp.json();
          if (data.connected && data.last_heartbeat) {
            isAgentOnline = true;
            if (agentBadgeText) agentBadgeText.textContent = `PC Windows: 🟢 En línia (${data.status})`;
            if (pulseDot) {
              pulseDot.style.background = '#34d399';
              pulseDot.style.boxShadow = '0 0 8px #34d399';
            }
            return;
          }
        }
      } catch (e) {
        // Servidor pont local no disponible
      }

      isAgentOnline = false;
      if (agentBadgeText) agentBadgeText.textContent = 'PC Windows: 🔴 Desconnectat (Pont local no detectat)';
      if (pulseDot) {
        pulseDot.style.background = '#94a3b8';
        pulseDot.style.boxShadow = 'none';
      }
      return;
    }

    // 3. Sense configuració
    isAgentOnline = false;
    if (agentBadgeText) agentBadgeText.textContent = 'PC Windows: Desconnectat (Servei remot pendent)';
    if (pulseDot) {
      pulseDot.style.background = '#94a3b8';
      pulseDot.style.boxShadow = 'none';
    }
  }

  // Comprovar batec inicial i periòdicament
  checkAgentHeartbeat();
  if ((window.SUPABASE_CONFIG && window.SUPABASE_CONFIG.url) || isDevBridgeExplicitlyEnabled()) {
    setInterval(checkAgentHeartbeat, 5000);
  }

  // -------------------------------------------------------------
  // Actualització visual de l'Estat de la Feina
  // -------------------------------------------------------------
  function updateJobUI(session) {
    if (!session) {
      if (stateVal) stateVal.textContent = 'En repòs (Sense cerca activa)';
      if (timerVal) timerVal.textContent = '--:--';
      if (idVal) idVal.textContent = 'Cap';
      if (oppsVal) oppsVal.textContent = '0 actives';
      btnStart.disabled = false;
      btnStop.disabled = true;
      return;
    }

    if (idVal) idVal.textContent = session.id ? session.id.slice(0, 13) + '...' : 'Cap';

    const statusDescriptions = {
      'PENDING': '⏳ En cua (PENDING · Esperant agent Windows...)',
      'RUNNING': '⚡ Cercant (RUNNING · Agent actiu executant cerca)',
      'CANCEL_REQUESTED': '🛑 Sol·licitant aturada cooperativa...',
      'CANCELLED': '🛑 Cerca aturada (CANCELLED)',
      'COMPLETED': '✅ Cerca finalitzada (COMPLETED)',
      'FAILED': '❌ Error durant la cerca (FAILED)',
      'TIMEOUT': '⏰ Temps esgotat (TIMEOUT)'
    };

    if (stateVal) stateVal.textContent = statusDescriptions[session.status] || session.status;

    if (session.status === 'RUNNING' || session.status === 'PENDING') {
      btnStart.disabled = true;
      btnStop.disabled = false;
    } else if (session.status === 'CANCEL_REQUESTED') {
      btnStart.disabled = true;
      btnStop.disabled = true;
    } else {
      btnStart.disabled = false;
      btnStop.disabled = true;
    }
  }

  // -------------------------------------------------------------
  // Botó «Buscar surebets»
  // -------------------------------------------------------------
  btnStart.addEventListener('click', async () => {
    if (!isDevBridgeExplicitlyEnabled() && window.SUPABASE_CONFIG && window.SUPABASE_CONFIG.url && !window.SUPABASE_CONFIG.userJwt) {
      showToast("Cal iniciar sessió com a operador abans de llançar la cerca.", "warning");
      if (operatorEmailInput) operatorEmailInput.focus();
      return;
    }

    if (!isAgentOnline) {
      showToast("Avís: El PC Windows no sembla en línia. Assegura't d'executar 'start_surebet_agent.bat'.", "warning");
    }

    const duration = parseInt(durationSelect ? durationSelect.value : '120', 10);
    const durationMins = Math.round(duration / 60);

    try {
      let sessionData;
      if (window.SUPABASE_CONFIG && window.SUPABASE_CONFIG.url && window.SUPABASE_CONFIG.userJwt) {
        // Mode Supabase remot: crida a l'RPC protegida create_search_session
        const rpcUrl = `${window.SUPABASE_CONFIG.url.replace(/\/$/, '')}/rest/v1/rpc/create_search_session`;
        const rpcHeaders = {
          'apikey': window.SUPABASE_CONFIG.publishableKey,
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${window.SUPABASE_CONFIG.userJwt}`
        };

        const rpcResp = await fetch(rpcUrl, {
          method: 'POST',
          headers: rpcHeaders,
          body: JSON.stringify({ p_duration_seconds: duration })
        });

        if (!rpcResp.ok) {
          const errText = await rpcResp.text();
          if (errText.includes("23505") || errText.includes("Ja existeix una sessió")) {
            throw new Error("Ja hi ha una cerca activa o en cua. Espera que finalitzi o atura-la abans d'iniciar-ne una de nova.");
          }
          throw new Error(`Error creant sessió a Supabase (${rpcResp.status}): ${errText}`);
        }

        const createdId = await rpcResp.json();
        sessionData = { session_id: createdId, status: 'PENDING' };
      } else if (isDevBridgeExplicitlyEnabled()) {
        const resp = await fetch(`${LOCAL_BRIDGE_URL}/api/sessions`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Requested-With': 'XMLHttpRequest'
          },
          body: JSON.stringify({ duration_minutes: durationMins, user_id: 'user_local_web' })
        });

        if (!resp.ok) throw new Error("No s'ha pogut crear la sessió al coordinador");
        sessionData = await resp.json();
      } else {
        throw new Error("El servei de Surebets no està configurat.");
      }

      _activeSurebetSession = {
        id: sessionData.session_id,
        status: sessionData.status,
        durationSeconds: duration,
        startTime: Date.now()
      };

      updateJobUI(_activeSurebetSession);
      showToast(`Cerca de Surebets iniciada (${durationMins} min) a Supabase`, 'success');

      // Temporitzador de seguiment
      if (_surebetSessionTimerInterval) clearInterval(_surebetSessionTimerInterval);
      _surebetSessionTimerInterval = setInterval(() => {
        if (!_activeSurebetSession) {
          clearInterval(_surebetSessionTimerInterval);
          return;
        }
        const elapsed = Math.floor((Date.now() - _activeSurebetSession.startTime) / 1000);
        const mins = String(Math.floor(elapsed / 60)).padStart(2, '0');
        const secs = String(elapsed % 60).padStart(2, '0');
        if (timerVal) timerVal.textContent = `${mins}:${secs}`;
      }, 1000);

      // Polling de resultats i estat
      if (_surebetPollInterval) clearInterval(_surebetPollInterval);
      _surebetPollInterval = setInterval(async () => {
        if (!_activeSurebetSession) {
          clearInterval(_surebetPollInterval);
          return;
        }

        try {
          if (window.SUPABASE_CONFIG && window.SUPABASE_CONFIG.url && window.SUPABASE_CONFIG.userJwt) {
            const headers = {
              'apikey': window.SUPABASE_CONFIG.publishableKey,
              'Content-Type': 'application/json',
              'Authorization': `Bearer ${window.SUPABASE_CONFIG.userJwt}`
            };

            // 1. Estat de la sessió
            const sUrl = `${window.SUPABASE_CONFIG.url.replace(/\/$/, '')}/rest/v1/surebet_sessions?id=eq.${_activeSurebetSession.id}&select=*`;
            const sResp = await fetch(sUrl, { headers, cache: 'no-store' });
            if (sResp.ok) {
              const sData = await sResp.json();
              if (sData && sData.length > 0) {
                _activeSurebetSession.status = sData[0].status;
                updateJobUI(_activeSurebetSession);

                if (['COMPLETED', 'CANCELLED', 'FAILED', 'TIMEOUT'].includes(sData[0].status)) {
                  clearInterval(_surebetPollInterval);
                  clearInterval(_surebetSessionTimerInterval);
                  showToast(`Sessió finalitzada: ${sData[0].status}`, 'info');
                }
              }
            }

            // 2. Consulta de snapshots per a la sessió
            const snapUrl = `${window.SUPABASE_CONFIG.url.replace(/\/$/, '')}/rest/v1/surebet_snapshots?session_id=eq.${_activeSurebetSession.id}&select=id,opportunities_count`;
            const snapResp = await fetch(snapUrl, { headers, cache: 'no-store' });
            if (snapResp.ok) {
              const snapData = await snapResp.json();
              if (snapData && oppsVal) {
                const totalOpps = snapData.reduce((acc, s) => acc + (s.opportunities_count || 0), 0);
                oppsVal.textContent = `${snapData.length} snap · ${totalOpps} opps`;
              }
            }

            // 3. Oportunitats actives des de la vista v_active_surebets amb FILTRATGE SERVIDOR
            const vUrl = `${window.SUPABASE_CONFIG.url.replace(/\/$/, '')}/rest/v1/v_active_surebets?matchup=not.ilike.*TEST*&matchup=not.ilike.*SIMULAC*&select=*`;
            const vResp = await fetch(vUrl, { headers, cache: 'no-store' });
            if (vResp.ok) {
              const vData = await vResp.json();
              if (appData && appData.arbitrage) {
                // Filtratge estructurat de seguretat (booleans de simulació i metadades)
                const realData = (vData || []).filter(item => {
                  if (item.is_synthetic === true || item.is_synthetic === 'true') return false;
                  if (item.bookmakers && Array.isArray(item.bookmakers) && item.bookmakers.some(b => b && b.is_synthetic)) return false;
                  return true;
                });
                appData.arbitrage.surebets = realData;
                renderArbitrage();
              }
            }
          } else if (isDevBridgeExplicitlyEnabled()) {
            // Mode Local: Pont HTTP 127.0.0.1
            const sResp = await fetch(`${LOCAL_BRIDGE_URL}/api/sessions/${_activeSurebetSession.id}`, { cache: 'no-store' });
            if (sResp.ok) {
              const sData = await sResp.json();
              _activeSurebetSession.status = sData.status;
              updateJobUI(_activeSurebetSession);

              if (['COMPLETED', 'CANCELLED', 'FAILED'].includes(sData.status)) {
                clearInterval(_surebetPollInterval);
                clearInterval(_surebetSessionTimerInterval);
                showToast(`Sessió de cerca finalitzada: ${sData.status}`, 'info');
              }
            }

            const oppResp = await fetch(`${LOCAL_BRIDGE_URL}/api/opportunities`, { cache: 'no-store' });
            if (oppResp.ok) {
              const oppData = await oppResp.json();
              if (appData && appData.arbitrage) {
                const nowSec = Date.now() / 1000;
                const validOpps = (oppData.opportunities || []).filter(o => {
                  const age = o.detected_at_epoch ? (nowSec - o.detected_at_epoch) : 0;
                  return age <= 1800.0 && (!o.matchup || !o.matchup.includes('[TEST'));
                });

                appData.arbitrage.surebets = validOpps.map(o => o.opportunity_payload || o);
                renderArbitrage();
              }
            }
          }
        } catch (err) {
          console.warn("[Surebet Polling] Error consultant estat:", err);
        }
      }, 2000);

    } catch (err) {
      showToast(`Error iniciant cerca: ${err.message}`, 'error');
    }
  });

  // -------------------------------------------------------------
  // Botó «Aturar cerca»
  // -------------------------------------------------------------
  btnStop.addEventListener('click', async () => {
    if (_activeSurebetSession && _activeSurebetSession.id) {
      try {
        _activeSurebetSession.status = 'CANCEL_REQUESTED';
        updateJobUI(_activeSurebetSession);
        showToast("Sol·licitant aturada cooperativa a l'Agent...", "warning");

        if (window.SUPABASE_CONFIG && window.SUPABASE_CONFIG.url && window.SUPABASE_CONFIG.userJwt) {
          // Mode Supabase remot: crida a l'RPC request_session_cancellation
          const cancelUrl = `${window.SUPABASE_CONFIG.url.replace(/\/$/, '')}/rest/v1/rpc/request_session_cancellation`;
          const cancelHeaders = {
            'apikey': window.SUPABASE_CONFIG.publishableKey,
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${window.SUPABASE_CONFIG.userJwt}`
          };
          const cancelResp = await fetch(cancelUrl, {
            method: 'POST',
            headers: cancelHeaders,
            body: JSON.stringify({ p_session_id: _activeSurebetSession.id })
          });
          if (cancelResp.ok) {
            _activeSurebetSession.status = 'CANCEL_REQUESTED';
            updateJobUI(_activeSurebetSession);
            showToast("Aturada de cerca sol·licitada a Supabase", "info");
          } else {
            const errText = await cancelResp.text();
            throw new Error(`Error en sol·licitud de cancel·lació: ${errText}`);
          }
        } else if (isDevBridgeExplicitlyEnabled()) {
          const resp = await fetch(`${LOCAL_BRIDGE_URL}/api/sessions/${_activeSurebetSession.id}/cancel`, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-Requested-With': 'XMLHttpRequest'
            },
            body: JSON.stringify({ user_id: 'user_local_web' })
          });

          if (resp.ok) {
            const resData = await resp.json();
            _activeSurebetSession.status = resData.status || 'CANCELLED';
            updateJobUI(_activeSurebetSession);
            showToast("Cerca aturada correctament", "info");
          }
        }
      } catch (e) {
        showToast("Error comunicant aturada a l'Agent", "error");
      }
    }
  });
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
      } catch (e) { }
    }

    renderAll();
  } catch (err) {
    console.error("Error carregant data.json:", err);
    document.getElementById('last-updated-badge').textContent = "Avís: Carregant dades...";
  }
}

async function loadLiveSurebets() {
  const g = (typeof window !== 'undefined') ? window : (typeof global !== 'undefined' ? global : {});
  const liveUrl = g.SUREBETS_LIVE_URL || './data/surebets_live.json';
  try {
    const res = await fetch(liveUrl, { cache: 'no-cache' });
    if (!res.ok) {
      throw new Error(`HTTP error ${res.status}`);
    }
    const liveData = await res.json();
    if (liveData && liveData.arbitrage && appData) {
      appData.arbitrage = liveData.arbitrage;
      if (liveData.cost_guard) {
        appData.cost_guard = liveData.cost_guard;
      }
      g._surebetsLiveConnected = true;
      if (typeof global !== 'undefined') global._surebetsLiveConnected = true;
      renderArbitrage();
    }
  } catch (err) {
    g._surebetsLiveConnected = false;
    if (typeof global !== 'undefined') global._surebetsLiveConnected = false;
    // Fallback transparent: manté les dades prèvies de data.json
    if (appData && appData.arbitrage) {
      renderArbitrage();
    }
  }
}

function renderAll() {
  if (!appData) return;

  applyLocalOddsOverrides();
  loadBankrollSimState();
  UserAuth.updateUI();
  PersonalBets.render();
  renderHeaderMeta();
  renderPowerRankings();
  renderPredictions();
  renderValueBets();
  renderArbitrage();
  renderCombos();
  renderBankroll();
  renderChangelog();
  checkOddsAlerts();
}

function applyFilters() {
  renderPowerRankings();
  renderPredictions();
  renderValueBets();
  renderArbitrage();
  renderCombos();
}

// -----------------------------------------------------------------------------
// METADADES I CAPÇALERA
// -----------------------------------------------------------------------------
function renderHeaderMeta() {
  const badge = document.getElementById('last-updated-badge');
  if (!badge || !appData.metadata) return;

  const fmt = appData.metadata.formatted_date || "Actualitzat avui";
  let shortFmt = fmt;
  const match = fmt.match(/(\d{1,2}\/\d{1,2})(?:\/\d{4})?\s*(?:a les)?\s*(\d{1,2}:\d{2})/i);
  if (match) {
    shortFmt = `${match[1]} · ${match[2]}`;
  } else {
    shortFmt = fmt.replace(/\/?\d{4}/, '').replace('a les', '').trim();
  }

  badge.innerHTML = `
    <span class="timestamp-full">Actualitzat: ${fmt}</span>
    <span class="timestamp-mobile">🕒 ${shortFmt}</span>
  `;
  badge.setAttribute('title', `Darrera actualització del model: ${fmt}`);
}

// -----------------------------------------------------------------------------
// 1. POWER RÀNQUINGS ELO
// -----------------------------------------------------------------------------
function renderPowerRankings() {
  const container = document.getElementById('rankings-tables-container');
  if (!container || !appData.power_rankings) return;

  const sel = document.getElementById('select-league-rankings');
  if (sel && sel.value !== selectedLeagueRankings) {
    sel.value = selectedLeagueRankings;
  }

  const comps = appData.metadata.competitions || [];
  let html = '';

  comps.forEach(c => {
    if (selectedLeagueRankings !== c.id) return;

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

  const selLeague = document.getElementById('select-league-pred');
  if (selLeague && selLeague.value !== selectedLeaguePredictions) {
    selLeague.value = selectedLeaguePredictions;
  }

  // Actualitzar Desplegable de Jornades
  const selPred = document.getElementById('select-jornada-pred');
  const badgePred = document.getElementById('badge-jornada-pred');

  if (selPred) {
    if (selectedLeaguePredictions === 'ALL') {
      selPred.innerHTML = `<option value="ACTIVE" selected>Jornada en Curs de Cada Lliga</option>`;
      if (badgePred) {
        badgePred.textContent = 'Totes les Lligues';
        badgePred.className = 'jornada-badge badge-active';
      }
    } else {
      const pData = appData.predictions[selectedLeaguePredictions];
      if (pData) {
        const activeJ = pData.active_jornada || pData.jornada || 8;
        const available = pData.available_jornadas || [activeJ];
        const maxJ = Math.max(...available, activeJ + 2);
        const minJ = 1;

        let targetJ = selectedJornadaPred[selectedLeaguePredictions];
        if (!targetJ || targetJ === 'ACTIVE') {
          targetJ = activeJ;
          selectedJornadaPred[selectedLeaguePredictions] = activeJ;
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
    if (selectedLeaguePredictions !== 'ALL' && selectedLeaguePredictions !== c.id) return;
    const pData = appData.predictions[c.id];
    if (!pData) return;

    const activeJ = pData.active_jornada || pData.jornada || 8;
    const targetJ = (selectedLeaguePredictions === 'ALL') ? activeJ : (selectedJornadaPred[c.id] || activeJ);

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

    const seenMatchKeys = new Set();
    jMatches.forEach(m => {
      const hId = (m.home_team?.id || m.home_team_id || (typeof m.home_team === 'string' ? m.home_team : '') || '').toLowerCase();
      const aId = (m.away_team?.id || m.away_team_id || (typeof m.away_team === 'string' ? m.away_team : '') || '').toLowerCase();
      const mId = m.match_id || m.id;
      const canonicalKey = `${m.competition_id || c.id}_J${targetJ}_${hId}_${aId}`;
      if (seenMatchKeys.has(canonicalKey) || (mId && seenMatchKeys.has(mId))) {
        return;
      }
      seenMatchKeys.add(canonicalKey);
      if (mId) seenMatchKeys.add(mId);
      m._compName = c.name;
      m._compFlag = c.flag;
      m._targetJornada = targetJ;
      allMatches.push(m);
    });
  });

  // Ordenar sempre de forma cronològica ascendent pel començament del partit
  // (Ordenació global quan es selecciona "Totes" i consistent quan es selecciona una lliga individual)
  allMatches.sort((a, b) => {
    const timeA = parseMatchDateToMs(a.date || a.date_time);
    const timeB = parseMatchDateToMs(b.date || b.date_time);
    if (timeA !== timeB) return timeA - timeB;
    const compA = a.competition_id || a._compName || '';
    const compB = b.competition_id || b._compName || '';
    if (compA !== compB) return compA.localeCompare(compB);
    const idA = a.match_id || a.id || '';
    const idB = b.match_id || b.id || '';
    return idA.localeCompare(idB);
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
    const isB365 = (currentBookie === 'BET365');
    const oddsSource = isB365 ? (m.odds_1x2_bet365 || m.odds_1x2) : (m.odds_1x2_winamax || m.odds_1x2);
    const o1 = oddsSource ? (oddsSource['1'] || '-') : '-';
    const ox = oddsSource ? (oddsSource['X'] || '-') : '-';
    const o2 = oddsSource ? (oddsSource['2'] || '-') : '-';

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
              <div class="stat-item-val" style="color: var(--accent-cyan); font-size: 13.5px;" title="${(m.top_scorelines || []).map(s => `${s.score} (${s.prob}%)`).join(' · ')}">${m.most_likely_score_formatted || (m.most_likely_score_prob ? `${m.most_likely_score} (${String(m.most_likely_score_prob).replace('.', ',')}%)` : (m.most_likely_score || '-'))}</div>
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

          <div class="match-card-actions">
            <button class="btn-match-details" onclick="openMatchDetailsModal('${m.match_id || m.id}')">
              ℹ️ MÉS INFORMACIÓ
            </button>
          </div>
        </div>
      `;
    }

    // -------------------------------------------------------------------------
    // B) TARGETA PER A PARTITS SUSPESOS / AJORNATS (SUSPENDED)
    // -------------------------------------------------------------------------
    if (m.status === 'SUSPENDED') {
      const suspReason = m.suspension_reason || "Aquest partit ha estat suspès per incidents o causa de força major. El pronòstic i les combinades romanen congelats i a l'espera de resolució oficial, sense comptar-se com a empat a 0.";
      return `
        <div class="match-card match-card-suspended">
          <div class="match-card-header">
            <span class="match-league-tag">${m._compFlag || '⚽'} ${m._compName} · J${m.jornada}</span>
            <span class="status-suspended-pill">⏸️ SUSPÈS · A l'espera del resultat</span>
          </div>

          <div class="matchup-row matchup-suspended-row">
            <div class="team-box">
              <div class="team-name">${m.home_team.name}</div>
              <div class="team-elo-sub">Elo: ${m.home_team.elo}</div>
            </div>

            <div class="suspended-score-box">
              <div class="suspended-icon-val">⏸️</div>
              <span class="suspended-score-lbl">Resultat Congelat</span>
            </div>

            <div class="team-box away">
              <div class="team-name">${m.away_team.name}</div>
              <div class="team-elo-sub">Elo: ${m.away_team.elo}</div>
            </div>
          </div>

          <!-- Targeta Informativa de Suspensió -->
          <div class="prediction-evaluation-card suspended">
            <div class="eval-header-line">
              <span class="eval-title">⚠️ <strong>Partit Suspès Oficialment</strong></span>
              <span class="suspended-res-badge">Pendent de Resolució</span>
            </div>
            <div class="eval-body-line" style="margin-top: 6px; line-height: 1.5; font-size: 12.5px; color: var(--text-secondary);">
              ${suspReason}
            </div>
          </div>

          <!-- Mètriques de referència del model -->
          <div class="match-stats-grid finished-grid" style="border-top: 1px dashed rgba(245, 158, 11, 0.25);">
            <div>
              <div class="stat-item-label">xG Model</div>
              <div class="stat-item-val" style="color: var(--accent-cyan); font-size: 13.5px;">${m.xg_home} - ${m.xg_away}</div>
            </div>
            <div>
              <div class="stat-item-label">Marcador Predit</div>
              <div class="stat-item-val" style="color: #ffffff; font-size: 13px;" title="${(m.top_scorelines || []).map(s => `${s.score} (${s.prob}%)`).join(' · ')}">${m.most_likely_score_formatted || (m.most_likely_score_prob ? `${m.most_likely_score} (${String(m.most_likely_score_prob).replace('.', ',')}%)` : (m.most_likely_score || '-'))}</div>
            </div>
            <div>
              <div class="stat-item-label">Prob. Model 1X2</div>
              <div class="stat-item-val" style="color: var(--text-muted); font-size: 11px; font-family: var(--font-mono);">
                1: ${p1}% · X: ${px}% · 2: ${p2}%
              </div>
            </div>
          </div>

          <div class="referee-tag">
            <span style="${refBadgeCls}">⚖️ ${ref.name}</span>
            <span style="font-family: var(--font-mono);">${ref.yellow_avg ? `(${ref.yellow_avg} 🟨 · ${ref.fouls_avg} faltes)` : ''}</span>
          </div>

          <div class="match-card-actions">
            <button class="btn-match-details" onclick="openMatchDetailsModal('${m.match_id || m.id}')">
              ℹ️ MÉS INFORMACIÓ
            </button>
          </div>
        </div>
      `;
    }

    // -------------------------------------------------------------------------
    // C) TARGETA PER A PARTITS PENDENTS DE JUGAR (SCHEDULED)
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
            ${(o1 === '-' || !o1) ? `
              <span class="missing-odds-inline-warn" onclick="openOddsModalForMatch('${m.competition_id || ''}', '${(m.home_team.name || '').replace(/'/g, "\\'")}', '${(m.away_team.name || '').replace(/'/g, "\\'")}')">
                ⚠️ Quota ${isB365 ? 'Bet365' : 'Winamax'} no detectada ✏️
              </span>
            ` : `
              <span class="prob-1">1: ${p1}% (@${o1})</span>
              <span class="prob-x">X: ${px}% (@${ox})</span>
              <span class="prob-2">2: ${p2}% (@${o2})</span>
            `}
            <span class="prob-bookie-tag ${isB365 ? 'bet365' : 'winamax'}">${isB365 ? '🟢 Bet365' : '🔴 Winamax'}</span>
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
            <div class="stat-item-val" title="${(m.top_scorelines || []).map(s => `${s.score} (${s.prob}%)`).join(' · ')}">${m.most_likely_score_formatted || (m.most_likely_score_prob ? `${m.most_likely_score} (${String(m.most_likely_score_prob).replace('.', ',')}%)` : (m.most_likely_score || '-'))}</div>
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

        <div class="match-card-actions">
          <button class="btn-match-details" onclick="openMatchDetailsModal('${m.match_id || m.id}')">
            ℹ️ MÉS INFORMACIÓ
          </button>
        </div>
      </div>
    `;
  }).join('');
}

// -----------------------------------------------------------------------------
// 3. APOSTES DE VALOR (+EV%)
// -----------------------------------------------------------------------------
function formatBetMatchDate(dateStr) {
  if (!dateStr) return '';
  try {
    const parts = dateStr.trim().split(' ');
    const dParts = parts[0].includes('.') ? parts[0].split('.') : parts[0].split('-');
    let day, month, year;
    if (parts[0].includes('.')) {
      day = parseInt(dParts[0], 10);
      month = parseInt(dParts[1], 10) - 1;
      year = parseInt(dParts[2], 10);
    } else {
      year = parseInt(dParts[0], 10);
      month = parseInt(dParts[1], 10) - 1;
      day = parseInt(dParts[2], 10);
    }
    const d = new Date(year, month, day);
    const dayNames = ['Dg.', 'Dl.', 'Dt.', 'Dc.', 'Dj.', 'Dv.', 'Ds.'];
    const dayName = dayNames[d.getDay()] || '';
    const timeStr = parts[1] ? ` · ${parts[1]}` : '';
    return `${dayName} ${day.toString().padStart(2, '0')}/${(month + 1).toString().padStart(2, '0')}${timeStr}`;
  } catch (e) {
    return dateStr;
  }
}

function renderValueBets() {
  const container = document.getElementById('value-bets-container');
  if (!container || !appData.value_bets) return;

  const selValLeague = document.getElementById('select-league-value');
  if (selValLeague && selValLeague.value !== selectedLeagueValue) {
    selValLeague.value = selectedLeagueValue;
  }

  const isB365 = (currentBookie === 'BET365');
  let rawBets = (appData.value_bets || []).filter(b => !b.is_stale && b.status !== 'OBSOLETE');

  // Mapejar i calcular exactament l'edge real per a la casa d'apostes activa
  let bets = rawBets.map(b => {
    const odd = isB365
      ? (b.bookie_odd_bet365 != null ? b.bookie_odd_bet365 : null)
      : (b.bookie_odd_winamax != null ? b.bookie_odd_winamax : b.bookie_odd);

    const prob = (b.model_prob != null ? b.model_prob : 0) / 100.0;

    // EV percentual real: (Probabilitat * Cuota - 1) * 100
    let calcEdge = 0.0;
    if (odd && odd > 1.0 && prob > 0) {
      calcEdge = +((prob * odd - 1.0) * 100.0).toFixed(1);
    } else if (isB365) {
      calcEdge = b.edge_pct_bet365 != null ? b.edge_pct_bet365 : (b.edge_pct || 0.0);
    } else {
      calcEdge = b.edge_pct_winamax != null ? b.edge_pct_winamax : (b.edge_pct || 0.0);
    }

    // Càlcul dinàmic de Kelly Stake per a la cuota activa
    let kellyStake = b.kelly_stake != null ? b.kelly_stake : 2.5;
    if (odd && odd > 1.0 && prob > 0) {
      const bRatio = odd - 1.0;
      const q = 1.0 - prob;
      const rawK = ((bRatio * prob) - q) / bRatio;
      kellyStake = rawK > 0 ? +(Math.min(10.0, Math.max(0.5, rawK * 100 * 0.25))).toFixed(1) : 0.0;
    }

    const bUrl = isB365
      ? (b.bet365_url && !b.bet365_url.includes('/D1002/') ? b.bet365_url : 'https://www.bet365.es/#/AS/B1/')
      : (b.winamax_url || 'https://www.winamax.es/apuestas-deportivas');

    return {
      ...b,
      _activeOdd: odd,
      _activeEdge: calcEdge,
      _activeKelly: kellyStake,
      _activeUrl: bUrl
    };
  });

  // Filtre 1: Només apostes amb VALOR POSITIU (+EV > 0%) per a la casa seleccionada!
  bets = bets.filter(b => b._activeEdge > 0.0);

  // Filtre 2: Lliga
  if (selectedLeagueValue !== 'ALL') {
    bets = bets.filter(b => b.competition_id === selectedLeagueValue);
  }

  // Filtre 3: Cerca de text
  if (currentSearch) {
    bets = bets.filter(b =>
      b.matchup.toLowerCase().includes(currentSearch) ||
      b.selection.toLowerCase().includes(currentSearch)
    );
  }

  // Ordenar de major a menor valor (+EV%)
  bets.sort((a, b) => b._activeEdge - a._activeEdge);

  if (bets.length === 0) {
    const bookieName = isB365 ? 'Bet365' : 'Winamax';
    container.innerHTML = `<p style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--text-muted);">No hi ha apostes de valor (+EV) per a ${bookieName} amb el filtre seleccionat.</p>`;
    return;
  }

  container.innerHTML = bets.map(b => {
    const bookieOddStr = b._activeOdd != null ? b._activeOdd.toFixed(2) : '-';
    const edgeStr = `+${b._activeEdge.toFixed(1)}% EV`;
    const kellyStr = `${b._activeKelly.toFixed(1)}%`;
    const fairStr = b.fair_odd != null ? b.fair_odd.toFixed(2) : '-';
    const dateFormatted = formatBetMatchDate(b.date);

    const bookieLabel = isB365 ? 'Cuota Bet365' : 'Cuota Winamax';
    const actionLabel = isB365 ? 'Apostar a Bet365 ↗' : 'Apostar a Winamax España ↗';
    const btnCls = isB365 ? 'btn-winamax combo-direct-link bet365-link' : 'btn-winamax';

    return `
      <div class="value-card">
        <div class="value-edge-glow">${edgeStr}</div>
        <div class="value-matchup">${b.matchup}</div>
        ${dateFormatted ? `<div class="value-card-date">📅 ${dateFormatted}</div>` : ''}
        <div class="value-selection">👉 ${b.selection}</div>

        <div class="value-metrics-row">
          <div>
            <div class="stat-item-label">${bookieLabel}</div>
            <div class="stat-item-val" style="color: #ffffff;">@${bookieOddStr}</div>
          </div>
          <div>
            <div class="stat-item-label">Prob. Model</div>
            <div class="stat-item-val" style="color: var(--accent-cyan); font-family: var(--font-mono);">${b.model_prob != null ? b.model_prob.toFixed(1) : '-'}%</div>
          </div>
          <div>
            <div class="stat-item-label">Cuota Justa</div>
            <div class="stat-item-val" style="color: var(--text-muted);">@${fairStr}</div>
          </div>
          <div>
            <div class="stat-item-label">Stake Kelly</div>
            <div class="stat-item-val" style="color: var(--accent-emerald);">${kellyStr}</div>
          </div>
        </div>

        <div class="value-card-actions">
          <button class="btn-track-bet-card" onclick="quickTrackBetFromCard('${encodeURIComponent(JSON.stringify({
      matchup: b.matchup,
      selection: b.selection,
      odd: parseFloat(bookieOddStr) || b.bookie_odd,
      stake: 10,
      category: 'Valor (+EV)',
      competition_id: b.competition_id
    }))}')">
            ➕ Afegir al meu compte
          </button>
          <a href="${b._activeUrl}" target="_blank" rel="noopener noreferrer" class="${btnCls}">
            ${actionLabel}
          </a>
        </div>
      </div>
    `;
  }).join('');
}

// -----------------------------------------------------------------------------
// 3.5. SUREBETS & ARBITRATGE ESPORTIU ("DINERS SEGURS")
// -----------------------------------------------------------------------------
// ARBITRAGE (SUREBETS & VALUE BETS)
// -----------------------------------------------------------------------------
let currentArbTotalStake = 100;
window.arbitrageRegistry = {};

function roundCommercial(val) {
  if (val == null || isNaN(val)) return 100;
  return Math.floor(Number(val) + 0.5);
}
window.roundCommercial = roundCommercial;

function normalizeBankroll(totalBankroll, minLegs = 2) {
  const rounded = roundCommercial(totalBankroll != null ? totalBankroll : 100);
  return Math.max(minLegs, rounded);
}
window.normalizeBankroll = normalizeBankroll;

function getOperatorInfo(bookmaker, rawUrl) {
  const b = (bookmaker || '').toLowerCase().trim();
  let tagClass = 'arb-tag-generic';
  let icon = '🏷️';
  let name = bookmaker || 'Desconegut';
  let btnClass = 'btn-arb-leg-generic';
  let validUrl = (rawUrl || '').trim();
  let hasValidUrl = false;
  let actionLabel = 'Sense enllaç directe';

  function isGenericHomepage(u) {
    if (!u) return true;
    const clean = u.replace(/^https?:\/\//i, '').replace(/^www\./i, '').replace(/\/+$/, '').toLowerCase();
    return [
      'winamax.es', 'winamax.com', 'winamax.fr',
      'bet365.es', 'bet365.com',
      'betway.es', 'betway.com',
      'betfair.es', 'betfair.com',
      'matchbook.com',
      'smarkets.com'
    ].includes(clean);
  }

  if (b.includes('winamax')) {
    tagClass = 'arb-tag-winamax';
    icon = '🔴';
    name = 'Winamax';
    btnClass = 'btn-arb-leg-winamax';
    if (validUrl && !isGenericHomepage(validUrl)) {
      hasValidUrl = true;
      actionLabel = 'Apostar a Winamax ↗';
    } else {
      validUrl = '';
      hasValidUrl = false;
      actionLabel = 'Sense enllaç directe';
    }
  } else if (b.includes('bet365')) {
    tagClass = 'arb-tag-bet365';
    icon = '🟢';
    name = 'Bet365';
    btnClass = 'btn-arb-leg-bet365';
    if (validUrl && !isGenericHomepage(validUrl)) {
      validUrl = typeof getValidBet365Url === 'function' ? getValidBet365Url(validUrl) : validUrl;
      hasValidUrl = true;
      actionLabel = 'Apostar a Bet365 ↗';
    } else {
      validUrl = '';
      hasValidUrl = false;
      actionLabel = 'Sense enllaç directe';
    }
  } else if (b.includes('betway')) {
    tagClass = 'arb-tag-betway';
    icon = '⚫';
    name = 'Betway';
    btnClass = 'btn-arb-leg-betway';
    if (validUrl && !isGenericHomepage(validUrl)) {
      hasValidUrl = true;
      actionLabel = 'Apostar a Betway ↗';
    } else {
      validUrl = '';
      hasValidUrl = false;
      actionLabel = 'Sense enllaç directe';
    }
  } else if (b.includes('betfair')) {
    tagClass = 'arb-tag-betfair';
    icon = '🟨';
    name = 'Betfair Exchange';
    btnClass = 'btn-arb-leg-betfair';
    if (validUrl && !isGenericHomepage(validUrl)) {
      hasValidUrl = true;
      actionLabel = 'Apostar a Betfair ↗';
    } else {
      validUrl = '';
      hasValidUrl = false;
      actionLabel = 'Sense enllaç directe';
    }
  } else if (b.includes('matchbook')) {
    tagClass = 'arb-tag-matchbook';
    icon = '🔴';
    name = 'Matchbook';
    btnClass = 'btn-arb-leg-matchbook';
    if (validUrl && !isGenericHomepage(validUrl)) {
      hasValidUrl = true;
      actionLabel = 'Apostar a Matchbook ↗';
    } else {
      validUrl = '';
      hasValidUrl = false;
      actionLabel = 'Sense enllaç directe';
    }
  } else if (b.includes('smarkets')) {
    tagClass = 'arb-tag-smarkets';
    icon = '🟦';
    name = 'Smarkets';
    btnClass = 'btn-arb-leg-smarkets';
    if (validUrl && !isGenericHomepage(validUrl)) {
      hasValidUrl = true;
      actionLabel = 'Apostar a Smarkets ↗';
    } else {
      validUrl = '';
      hasValidUrl = false;
      actionLabel = 'Sense enllaç directe';
    }
  } else {
    if (validUrl && !isGenericHomepage(validUrl)) {
      hasValidUrl = true;
      actionLabel = `Apostar a ${name} ↗`;
    } else {
      validUrl = '';
      hasValidUrl = false;
      actionLabel = 'Sense enllaç directe';
    }
  }

  return { tagClass, icon, name, btnClass, actionLabel, validUrl, hasValidUrl };
}
window.getOperatorInfo = getOperatorInfo;

function calculateScenarioPnL(legs, stakes, scenarioIndex) {
  const legPnls = [];
  for (let i = 0; i < legs.length; i++) {
    const leg = legs[i];
    const stake = stakes[i];
    const odd = Number(leg.odd || 0.0);
    if (i === scenarioIndex) {
      legPnls.push(stake * (odd - 1.0));
    } else {
      legPnls.push(-stake);
    }
  }

  const groups = {};
  for (let i = 0; i < legs.length; i++) {
    const leg = legs[i];
    const skey = leg.settlement_key || `isolated_${i}_${leg.bookmaker}`;
    const model = leg.commission_model || 'NONE';
    const rate = Number(leg.commission_rate || 0.0);

    if (model === 'PER_BET_SETTLEMENT') {
      const uniqueKey = `per_bet_${i}_${skey}`;
      groups[uniqueKey] = { model, rate, entries: [{ pnl: legPnls[i], stake: stakes[i] }] };
    } else {
      if (!groups[skey]) {
        groups[skey] = { model, rate, entries: [] };
      }
      groups[skey].entries.push({ pnl: legPnls[i], stake: stakes[i] });
    }
  }

  let totalNetPnl = 0.0;
  for (const gkey in groups) {
    const group = groups[gkey];
    const model = group.model;
    const rate = group.rate;
    const entries = group.entries;

    if (model === 'NONE') {
      totalNetPnl += entries.reduce((acc, e) => acc + e.pnl, 0.0);
    } else if (model === 'NET_MARKET_WINNINGS') {
      const marketGrossPnl = entries.reduce((acc, e) => acc + e.pnl, 0.0);
      const commission = Math.max(0.0, marketGrossPnl) * rate;
      totalNetPnl += marketGrossPnl - commission;
    } else if (model === 'PER_BET_SETTLEMENT') {
      for (let j = 0; j < entries.length; j++) {
        const e = entries[j];
        if (e.pnl > 0.0) {
          totalNetPnl += e.pnl * (1.0 - rate);
        } else if (e.pnl < 0.0) {
          totalNetPnl += e.pnl - (e.stake * rate);
        }
      }
    }
  }
  return totalNetPnl;
}
window.calculateScenarioPnL = calculateScenarioPnL;

function optimizeArbitrageStakes(arb, totalBankroll) {
  const legs = arb.legs || [];
  const k = legs.length;
  const B = normalizeBankroll(totalBankroll, k);

  // Si B == 100 i arb.base_investment == 100 i els stakes del backend ja estan precalculats exactament
  if (B === 100 && normalizeBankroll(arb.base_investment || 100, k) === 100 && legs.every(l => l.stake_rounded != null)) {
    const stakes = legs.map(l => l.stake_rounded);
    const scenarioPnls = [0, 1, ...(k === 3 ? [2] : [])].map(s => calculateScenarioPnL(legs, stakes, s));
    const scenarioTotalReturns = scenarioPnls.map(p => Number((B + p).toFixed(2)));
    const minReturn = Math.min(...scenarioTotalReturns);
    const netProfit = Number(Math.min(...scenarioPnls).toFixed(2));
    const profitPct = Number(((netProfit / B) * 100.0).toFixed(2));
    const grossPayouts = legs.map((l, i) => Number((stakes[i] * Number(l.odd || 0.0)).toFixed(2)));
    return {
      bankroll: B,
      effectiveBankroll: B,
      roundStakes: stakes,
      grossPayouts,
      scenarioPnls,
      scenarioTotalReturns,
      minReturn,
      netProfit,
      profitPct,
      isPositive: netProfit > 0
    };
  }

  // Optimització exhaustiva global de stakes enters per al bankroll B
  let bestStakes = [];
  let bestMinPnl = -Infinity;
  let bestAvgPnl = -Infinity;
  let bestVarPnl = Infinity;

  if (k === 2) {
    for (let s1 = 1; s1 < B; s1++) {
      const s2 = B - s1;
      const testStakes = [s1, s2];
      const p0 = calculateScenarioPnL(legs, testStakes, 0);
      const p1 = calculateScenarioPnL(legs, testStakes, 1);
      const minP = Math.min(p0, p1);
      const avgP = (p0 + p1) / 2.0;
      const varP = Math.pow(p0 - avgP, 2) + Math.pow(p1 - avgP, 2);

      if (minP > bestMinPnl + 1e-6) {
        bestMinPnl = minP; bestAvgPnl = avgP; bestVarPnl = varP; bestStakes = testStakes;
      } else if (Math.abs(minP - bestMinPnl) <= 1e-6) {
        if (avgP > bestAvgPnl + 1e-6) {
          bestMinPnl = minP; bestAvgPnl = avgP; bestVarPnl = varP; bestStakes = testStakes;
        } else if (Math.abs(avgP - bestAvgPnl) <= 1e-6) {
          if (varP < bestVarPnl - 1e-6) {
            bestMinPnl = minP; bestAvgPnl = avgP; bestVarPnl = varP; bestStakes = testStakes;
          }
        }
      }
    }
  } else if (k === 3) {
    for (let s1 = 1; s1 <= B - 2; s1++) {
      const maxS2 = B - s1;
      for (let s2 = 1; s2 < maxS2; s2++) {
        const s3 = maxS2 - s2;
        const testStakes = [s1, s2, s3];
        const p0 = calculateScenarioPnL(legs, testStakes, 0);
        const p1 = calculateScenarioPnL(legs, testStakes, 1);
        const p2 = calculateScenarioPnL(legs, testStakes, 2);
        const minP = Math.min(p0, p1, p2);
        const avgP = (p0 + p1 + p2) / 3.0;
        const varP = Math.pow(p0 - avgP, 2) + Math.pow(p1 - avgP, 2) + Math.pow(p2 - avgP, 2);

        if (minP > bestMinPnl + 1e-6) {
          bestMinPnl = minP; bestAvgPnl = avgP; bestVarPnl = varP; bestStakes = testStakes;
        } else if (Math.abs(minP - bestMinPnl) <= 1e-6) {
          if (avgP > bestAvgPnl + 1e-6) {
            bestMinPnl = minP; bestAvgPnl = avgP; bestVarPnl = varP; bestStakes = testStakes;
          } else if (Math.abs(avgP - bestAvgPnl) <= 1e-6) {
            if (varP < bestVarPnl - 1e-6) {
              bestMinPnl = minP; bestAvgPnl = avgP; bestVarPnl = varP; bestStakes = testStakes;
            }
          }
        }
      }
    }
  }

  const scenarioPnls = [0, 1, ...(k === 3 ? [2] : [])].map(s => calculateScenarioPnL(legs, bestStakes, s));
  const scenarioTotalReturns = scenarioPnls.map(p => Number((B + p).toFixed(2)));
  const minReturn = Math.min(...scenarioTotalReturns);
  const netProfit = Number(Math.min(...scenarioPnls).toFixed(2));
  const profitPct = Number(((netProfit / B) * 100.0).toFixed(2));
  const grossPayouts = legs.map((l, i) => Number((bestStakes[i] * Number(l.odd || 0.0)).toFixed(2)));

  return {
    bankroll: B,
    effectiveBankroll: B,
    roundStakes: bestStakes,
    grossPayouts,
    scenarioPnls,
    scenarioTotalReturns,
    minReturn,
    netProfit,
    profitPct,
    isPositive: netProfit > 0
  };
}
window.optimizeArbitrageStakes = optimizeArbitrageStakes;

function computeArbitrageValues(arb, totalBankroll) {
  return optimizeArbitrageStakes(arb, totalBankroll);
}
window.computeArbitrageValues = computeArbitrageValues;

function formatArbitrageInstructions(arb, totalBankroll) {
  const total = totalBankroll || currentArbTotalStake || 100;
  const legs = arb.legs || [];
  const { bankroll: effectiveBankroll, roundStakes, grossPayouts, minReturn, netProfit, profitPct, isPositive } = computeArbitrageValues(arb, total);

  let text = `⚡ INSTRUCCIONS D'ARBITRATGE MATEMÀTIC (+${profitPct.toFixed(1)}% Teòric)\n`;
  text += `Partit: ${arb.matchup} (${arb.competition_name || arb.competition_id})\n`;
  text += `Mercat: ${arb.market_name}\n`;
  text += `Data: ${arb.date}\n`;
  text += `Inversió Total: ${effectiveBankroll} €\n\n`;
  text += `PAS A PAS D'EXECUCIÓ:\n`;

  legs.forEach((leg, i) => {
    const op = getOperatorInfo(leg.bookmaker, leg.url);
    const linkText = op.hasValidUrl ? op.validUrl : 'Sense enllaç directe';
    text += `${i + 1}. ${op.name.toUpperCase()}:\n`;
    text += `   👉 Aposta ${roundStakes[i]} € a '${leg.selection}' @${Number(leg.odd).toFixed(2)}\n`;
    text += `   💵 Pagament brut individual: ${grossPayouts[i].toFixed(2)} €\n`;
    text += `   🔗 Link: ${linkText}\n\n`;
  });

  text += `--------------------------------------------------\n`;
  text += `💼 RETORN GLOBAL MÍNIM DE CARTERA: ${minReturn.toFixed(2)} € (després de cobrir la resta d'apostes i comissions)\n`;
  text += `💰 BENEFICI NET GARANTIT: ${netProfit >= 0 ? '+' : ''}${netProfit.toFixed(2)} € (Teòric matemàtic: subjecte a reconfirmació de quotes i liquiditat a cada casa)\n`;

  return text;
}
window.formatArbitrageInstructions = formatArbitrageInstructions;

function copyArbitrageInstructions(arbKey) {
  const arb = window.arbitrageRegistry[arbKey];
  if (!arb) return;

  const total = currentArbTotalStake || 100;
  const text = formatArbitrageInstructions(arb, total);

  navigator.clipboard.writeText(text).then(() => {
    showToast(`Instruccions d'arbitratge copiades al porta-retalls!`, 'success');
  }).catch(() => {
    showToast(`Error copiant instruccions.`, 'error');
  });
}
window.copyArbitrageInstructions = copyArbitrageInstructions;

function escapeHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function formatRelativeTime(isoString, referenceNow = Date.now()) {
  if (!isoString) return 'Sense dades';
  const ts = new Date(isoString).getTime();
  if (isNaN(ts)) return 'Data invàlida';
  const diffSeconds = Math.max(0, Math.floor((referenceNow - ts) / 1000));
  if (diffSeconds < 10) return 'ara mateix (<10s)';
  if (diffSeconds < 60) return `fa ${diffSeconds}s`;
  const diffMinutes = Math.floor(diffSeconds / 60);
  if (diffMinutes < 60) return `fa ${diffMinutes} min`;
  const diffHours = Math.floor(diffMinutes / 60);
  if (diffHours < 24) return `fa ${diffHours} h`;
  const diffDays = Math.floor(diffHours / 24);
  return `fa ${diffDays} d`;
}

function updateLiveTimestamps() {
  if (typeof document === 'undefined') return;
  const now = Date.now();
  const timeElements = document.querySelectorAll('[data-live-timestamp]');
  timeElements.forEach(el => {
    const iso = el.getAttribute('data-live-timestamp');
    if (!iso) return;
    const prefix = el.getAttribute('data-time-prefix') || '';
    const text = formatRelativeTime(iso, now);
    el.textContent = prefix ? `${prefix} ${text}` : text;

    if (el.getAttribute('data-freshness-thresholds') === 'true') {
      const ts = new Date(iso).getTime();
      const ageSeconds = Math.max(0, Math.floor((now - ts) / 1000));
      const warnThreshold = parseInt(el.getAttribute('data-warn-threshold') || '60', 10);
      const errThreshold = parseInt(el.getAttribute('data-err-threshold') || '300', 10);

      el.classList.remove('fresh-green', 'stale-yellow', 'stale-red');
      if (ageSeconds <= warnThreshold) {
        el.classList.add('fresh-green');
      } else if (ageSeconds <= errThreshold) {
        el.classList.add('stale-yellow');
      } else {
        el.classList.add('stale-red');
      }
    }
  });
}

// Iniciar temporitzador periòdic en viu (sense recàrrega de pàgina al navegador)
if (typeof window !== 'undefined' && typeof document !== 'undefined') {
  if (window._liveTimerInterval) clearInterval(window._liveTimerInterval);
  window._liveTimerInterval = setInterval(updateLiveTimestamps, 1000);
  if (window._liveTimerInterval && window._liveTimerInterval.unref) {
    window._liveTimerInterval.unref();
  }
}

function renderPipelineStatus() {
  const badgeEl = document.getElementById('pipeline-overall-badge');
  const summaryEl = document.getElementById('pipeline-summary-bar');
  const tasksGridEl = document.getElementById('pipeline-tasks-grid');

  if (!badgeEl || !summaryEl || !tasksGridEl) return;

  const ps = (appData && (appData.pipeline_status || (appData.metadata && appData.metadata.pipeline_status))) || null;

  if (!ps) {
    badgeEl.innerHTML = `<span class="arb-status-pill arb-status-inactive">Desconegut</span>`;
    summaryEl.innerHTML = `<div class="pipeline-kpi-card"><span class="pipeline-kpi-body">Sense metadades de pipeline.</span></div>`;
    return;
  }

  const statusRaw = (ps.status || 'COMPLETED').toUpperCase();
  let statusBadgeHtml = '';
  if (statusRaw === 'COMPLETED' || statusRaw === 'SUCCESS') {
    statusBadgeHtml = `<span class="arb-status-pill arb-status-active">✅ Execució Correcta</span>`;
  } else if (statusRaw === 'PARTIAL') {
    statusBadgeHtml = `<span class="arb-status-pill arb-status-suspended">⚠️ Execució Parcial</span>`;
  } else if (statusRaw === 'RUNNING') {
    statusBadgeHtml = `<span class="arb-status-pill arb-status-updating">🔄 En Curs</span>`;
  } else {
    statusBadgeHtml = `<span class="arb-status-pill arb-status-error">❌ Fallida</span>`;
  }
  badgeEl.innerHTML = statusBadgeHtml;

  const lastSuccessIso = ps.last_success_timestamp || ps.last_run_timestamp || '';
  const lastAttemptIso = ps.last_attempt_timestamp || ps.last_run_timestamp || '';
  const durationSec = ps.duration_seconds != null ? `${Number(ps.duration_seconds).toFixed(1)}s` : '--';
  const scheduleText = ps.schedule || '08:23, 15:23 i 00:23 BCN';

  const lastSuccessDateStr = lastSuccessIso
    ? new Date(lastSuccessIso).toLocaleString('ca-ES', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })
    : 'Pendent';
  const lastAttemptDateStr = lastAttemptIso
    ? new Date(lastAttemptIso).toLocaleString('ca-ES', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })
    : 'Pendent';

  const attemptStatusLabel = (ps.last_attempt_status || ps.status || 'COMPLETED').toUpperCase();
  const attemptStatusTag = attemptStatusLabel === 'COMPLETED' || attemptStatusLabel === 'SUCCESS'
    ? '<span class="status-tag tag-success">Correcte</span>'
    : (attemptStatusLabel === 'PARTIAL' ? '<span class="status-tag tag-warning">Parcial</span>' : '<span class="status-tag tag-danger">Fallit</span>');

  summaryEl.innerHTML = `
    <div class="pipeline-kpi-card">
      <span class="pipeline-kpi-icon">🕒</span>
      <div class="pipeline-kpi-body">
        <div class="pipeline-kpi-lbl">Última Execució Correcta</div>
        <div class="pipeline-kpi-val">${lastSuccessDateStr}</div>
        <div class="pipeline-kpi-sub" data-live-timestamp="${lastSuccessIso}" data-time-prefix="fa">
          ${formatRelativeTime(lastSuccessIso)}
        </div>
      </div>
    </div>

    <div class="pipeline-kpi-card">
      <span class="pipeline-kpi-icon">🎯</span>
      <div class="pipeline-kpi-body">
        <div class="pipeline-kpi-lbl">Últim Intent de Pipeline</div>
        <div class="pipeline-kpi-val">${lastAttemptDateStr} (${attemptStatusTag})</div>
        <div class="pipeline-kpi-sub" data-live-timestamp="${lastAttemptIso}" data-time-prefix="fa">
          ${formatRelativeTime(lastAttemptIso)}
        </div>
      </div>
    </div>

    <div class="pipeline-kpi-card">
      <span class="pipeline-kpi-icon">⏱️</span>
      <div class="pipeline-kpi-body">
        <div class="pipeline-kpi-lbl">Durada de l'Execució</div>
        <div class="pipeline-kpi-val font-mono">${durationSec}</div>
        <div class="pipeline-kpi-sub">${escapeHtml(ps.runner_env || 'GitHub Actions Runner')}</div>
      </div>
    </div>

    <div class="pipeline-kpi-card">
      <span class="pipeline-kpi-icon">📅</span>
      <div class="pipeline-kpi-body">
        <div class="pipeline-kpi-lbl">Horari Programat (Actions)</div>
        <div class="pipeline-kpi-val">${scheduleText}</div>
        <div class="pipeline-kpi-sub">3 cicles diaris automàtics</div>
      </div>
    </div>
  `;

  const tasks = ps.tasks || {};
  const taskOrder = [
    { id: 'results_and_scores', icon: '⚽', defaultName: 'Resultats i marcadors (Flashscore)' },
    { id: 'elo_and_stats', icon: '📈', defaultName: 'Rànquings Elo i mètriques' },
    { id: 'referee_designations', icon: '🟨', defaultName: 'Designacions arbitrals (CTA / PGMOL)' },
    { id: 'predictions', icon: '🔮', defaultName: 'Prediccions Poisson GLM i Valor' },
    { id: 'combos', icon: '🎯', defaultName: 'Combinades oficials (Diàries i Lliga)' },
    { id: 'web_export', icon: '💾', defaultName: 'Exportació i desplegament web' }
  ];

  const taskCardsHtml = taskOrder.map(item => {
    const t = tasks[item.id] || {};
    const taskName = t.name || item.defaultName;
    const taskStatus = (t.status || 'COMPLETED').toUpperCase();

    let taskBadge = '<span class="status-tag tag-success">Completat</span>';
    let cardClass = 'task-card-success';
    if (taskStatus === 'FAILED' || taskStatus === 'ERROR') {
      taskBadge = '<span class="status-tag tag-danger">Fallit</span>';
      cardClass = 'task-card-failed';
    } else if (taskStatus === 'PARTIAL' || taskStatus === 'WARNING') {
      taskBadge = '<span class="status-tag tag-warning">Parcial</span>';
      cardClass = 'task-card-warning';
    } else if (taskStatus === 'SKIPPED') {
      taskBadge = '<span class="status-tag tag-neutral">Omesa</span>';
      cardClass = 'task-card-skipped';
    }

    const tLastSuccessIso = t.last_success || lastSuccessIso;
    const tTimeStr = tLastSuccessIso
      ? new Date(tLastSuccessIso).toLocaleTimeString('ca-ES', { hour: '2-digit', minute: '2-digit' })
      : '--';
    const tDetails = t.details || 'Tasca operativa sincronitzada.';

    return `
      <div class="pipeline-task-card ${cardClass}" data-task-id="${item.id}">
        <div class="pipeline-task-top">
          <span class="pipeline-task-name">${item.icon} ${taskName}</span>
          ${taskBadge}
        </div>
        <div class="pipeline-task-desc">${escapeHtml(tDetails)}</div>
        <div class="pipeline-task-bottom">
          <span>Últim èxit: <strong>${tTimeStr}</strong></span>
          <span class="pipeline-task-age" data-live-timestamp="${tLastSuccessIso}" data-time-prefix="">
            ${formatRelativeTime(tLastSuccessIso)}
          </span>
        </div>
      </div>
    `;
  }).join('');

  tasksGridEl.innerHTML = taskCardsHtml;
}
window.renderPipelineStatus = renderPipelineStatus;

function renderArbitrage() {
  const containerSurebets = document.getElementById('surebets-container');
  const containerStale = document.getElementById('stale-arbs-container');
  const staleHeader = document.getElementById('stale-arbs-header');
  const containerHistorical = document.getElementById('historical-arbs-container');
  const historicalHeader = document.getElementById('historical-arbs-header');
  const containerNear = document.getElementById('near-arbs-container');
  const heroBanner = document.getElementById('arbitrage-hero-banner');
  const bookmakersBar = document.getElementById('arbitrage-bookmakers-bar');
  const countBadge = document.getElementById('surebets-count-badge');
  const countStaleBadge = document.getElementById('stale-arbs-count-badge');
  const countHistoricalBadge = document.getElementById('historical-arbs-count-badge');
  const countNearBadge = document.getElementById('near-arbs-count-badge');

  if (!containerSurebets || !appData || !appData.arbitrage) return;

  const arbData = appData.arbitrage;
  const summary = arbData.summary || {};
  const bStatus = arbData.bookmakers_status || {};
  const nowMs = Date.now();

  // Funció auxiliar per formatar edat de quotes
  function formatRelativeAge(seconds, isoDate) {
    let s = seconds;
    if (s == null && isoDate) {
      const diffMs = Date.now() - new Date(isoDate).getTime();
      s = Math.max(0, Math.floor(diffMs / 1000));
    }
    if (s == null || isNaN(s)) return 'Sense dades';
    if (s < 10) return 'Ara mateix (<10s)';
    if (s < 60) return `fa ${Math.round(s)}s`;
    if (s < 3600) return `fa ${Math.round(s / 60)} min`;
    return `fa ${Math.round(s / 3600)} h`;
  }

  // Renderitzar Panell d'Estat dels Scrapers (Winamax, Bet365, Betway - FASE 1 & FASE 4)
  if (bookmakersBar) {
    const bookies = [
      { key: 'winamax', name: 'Winamax', icon: '🔴', tagClass: 'arb-tag-winamax' },
      { key: 'bet365', name: 'Bet365', icon: '🟢', tagClass: 'arb-tag-bet365' },
      { key: 'betway', name: 'Betway', icon: '⚫', tagClass: 'arb-tag-betway' }
    ];

    const cardsHtml = bookies.map(b => {
      const info = bStatus[b.key] || {};
      const statusRaw = (info.status || 'active').toLowerCase();
      let statusLabel = 'Actiu';
      let statusClass = 'arb-status-active';

      if (statusRaw === 'suspended') {
        statusLabel = 'Suspès (Circuit Breaker)';
        statusClass = 'arb-status-suspended';
      } else if (statusRaw === 'error') {
        statusLabel = 'Error de connexió';
        statusClass = 'arb-status-error';
      } else if (statusRaw === 'updating') {
        statusLabel = 'Actualitzant...';
        statusClass = 'arb-status-updating';
      }

      // Resultat de l'últim intent (èxit, error o parcial)
      const attemptStatus = (info.last_attempt_status || (statusRaw === 'error' ? 'error' : 'success')).toLowerCase();
      let attemptHtml = '';
      if (attemptStatus === 'success') {
        attemptHtml = '<span class="arb-attempt-tag attempt-success">✅ Últim intent: Èxit</span>';
      } else if (attemptStatus === 'partial') {
        attemptHtml = `<span class="arb-attempt-tag attempt-partial" title="${escapeHtml(info.partial_details || 'Dades parcials')}">⚠️ Últim intent: Parcial</span>`;
      } else {
        const errTitle = info.last_error ? `title="${escapeHtml(info.last_error)}"` : '';
        attemptHtml = `<span class="arb-attempt-tag attempt-error" ${errTitle}>❌ Últim intent: Fallit</span>`;
      }

      const lastSuccessIso = info.last_success || '';
      const successTimeStr = lastSuccessIso
        ? new Date(lastSuccessIso).toLocaleTimeString('ca-ES', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
        : 'Sense registre previ';

      const eventsCount = info.events_count != null ? info.events_count : 0;
      const oddsCount = info.odds_count != null ? info.odds_count : 0;

      // Timestamps distingits: petició, resposta HTTP, generació
      const qStartStr = info.query_start ? new Date(info.query_start).toLocaleTimeString('ca-ES', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '--';
      const qEndStr = info.query_end ? new Date(info.query_end).toLocaleTimeString('ca-ES', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '--';
      const httpCapStr = info.http_captured_at ? new Date(info.http_captured_at).toLocaleTimeString('ca-ES', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '--';

      const failureNote = (attemptStatus === 'error' && info.last_error)
        ? `<div class="arb-card-error-note">⚠️ Últim intent fallit: ${escapeHtml(info.last_error)}</div>`
        : '';

      return `
        <div class="arb-bookie-status-card" data-bookie="${b.key}">
          <div class="arb-bookie-header">
            <span class="arb-bookie-name">${b.icon} ${b.name}</span>
            <span class="arb-status-pill ${statusClass}">${statusLabel}</span>
          </div>

          <div class="arb-bookie-attempt-row">
            ${attemptHtml}
          </div>

          <div class="arb-bookie-metrics-grid">
            <div class="arb-bookie-detail">
              <span class="detail-label">Últim monitoratge correcte:</span>
              <span class="arb-bookie-detail-val font-mono">${successTimeStr}</span>
            </div>
            <div class="arb-bookie-detail">
              <span class="detail-label">Temps transcorregut:</span>
              <span class="arb-bookie-detail-val live-freshness-val font-mono"
                    data-live-timestamp="${lastSuccessIso}"
                    data-time-prefix=""
                    data-freshness-thresholds="true"
                    data-warn-threshold="60"
                    data-err-threshold="300">
                ${formatRelativeAge(info.age_seconds, lastSuccessIso)}
              </span>
            </div>
            <div class="arb-bookie-detail">
              <span class="detail-label">Volum capturat:</span>
              <span class="arb-bookie-detail-val font-mono">${eventsCount} partits · ${oddsCount} quotes</span>
            </div>
            <div class="arb-bookie-detail">
              <span class="detail-label" title="Hora de recepció de la resposta del servidor">Resposta HTTP:</span>
              <span class="arb-bookie-detail-val font-mono" title="Hora de captura de dades">${httpCapStr}</span>
            </div>
          </div>

          <div class="arb-card-query-window">
            <span>⏱️ Finestra consulta: ${qStartStr} ➔ ${qEndStr}</span>
          </div>

          ${failureNote}
        </div>
      `;
    }).join('');

    const lastGen = arbData.last_generation || (appData.metadata && appData.metadata.generated_at);
    const lastGenDate = lastGen ? new Date(lastGen) : null;
    const isOldSnapshot = lastGenDate ? (Date.now() - lastGenDate.getTime() > 24 * 3600 * 1000) : false;
    const lastGenStr = lastGenDate
      ? (isOldSnapshot
          ? `${lastGenDate.toLocaleDateString('ca-ES')} a les ${lastGenDate.toLocaleTimeString('ca-ES', { hour: '2-digit', minute: '2-digit' })}`
          : lastGenDate.toLocaleTimeString('ca-ES', { hour: '2-digit', minute: '2-digit', second: '2-digit' }))
      : 'N/A';

    bookmakersBar.innerHTML = `
      ${cardsHtml}
      <div class="arb-daemon-meta">
        <span>📁 <strong>Surebet Snapshot:</strong> Darrera comprovació ${isOldSnapshot ? 'd\'arxiu ' : ''}${lastGenStr}</span>
        <span>📡 Monitoratge sota demanda des del web: Pendent d'activació</span>
      </div>
    `;

    // Actualitzar timestamp a la llegenda
    const legendDataJson = document.getElementById('legend-data-json-timestamp');
    if (legendDataJson && lastGen) {
      legendDataJson.setAttribute('data-live-timestamp', lastGen);
      legendDataJson.textContent = `${lastGenStr} (${formatRelativeTime(lastGen)})`;
    }
  }

  // Renderitzar Bloc B: Pipeline de GitHub Actions
  renderPipelineStatus();

  // Avaluació dinàmica de frescor i classificació en 4 nivells de la política de 30 minuts
  function computeDynamicFreshness(arb) {
    const legs = arb.legs || [];
    if (!legs.length) return { tier: 'EXCLUDED', ageMax: 99999, skew: 0, legAges: [] };
    let minTs = Infinity;
    let maxTs = -Infinity;
    let hasMissing = false;
    let hasFuture = false;
    const legAges = [];

    for (const l of legs) {
      const tsStr = l.captured_at || l.timestamp || l.polled_at;
      if (!tsStr) {
        hasMissing = true;
        break;
      }
      const t = new Date(tsStr).getTime();
      if (isNaN(t)) {
        hasMissing = true;
        break;
      }
      // Tolerància de 5s per timestamps futurs
      if (t > nowMs + 5000) {
        hasFuture = true;
        break;
      }
      if (t < minTs) minTs = t;
      if (t > maxTs) maxTs = t;
      const legAge = Math.max(0, (nowMs - t) / 1000);
      legAges.push(legAge);
    }

    if (hasMissing || hasFuture) {
      return { tier: 'EXCLUDED', ageMax: 99999, skew: 0, legAges: [] };
    }

    // Exclusió de partits ja començats o finalitzats
    if (arb.date) {
      const mMs = parseMatchDateToMs(arb.date);
      if (mMs && mMs < nowMs) {
        return { tier: 'EXCLUDED', ageMax: 99999, skew: 0, legAges: [], isPastMatch: true };
      }
    }

    const ageMax = Math.max(0, (nowMs - minTs) / 1000);
    const skew = Math.max(0, (maxTs - minTs) / 1000);

    // Màxim absolut 1800s (30 minuts)
    if (ageMax > 1800) {
      return { tier: 'EXCLUDED', ageMax, skew, legAges };
    }
    if (ageMax <= 60 && skew <= 30) {
      return { tier: 'VERIFIED_FRESH', ageMax, skew, legAges };
    }
    if (ageMax <= 300) {
      return { tier: 'POTENTIALLY_STALE', ageMax, skew, legAges };
    }
    return { tier: 'RECENT_HISTORICAL', ageMax, skew, legAges };
  }

  // Agrupació de totes les fonts de surebets per re-avaluar l'envelliment dinàmic
  const allSurebetSources = [
    ...(arbData.surebets || []),
    ...(arbData.verified_fresh_surebets || []),
    ...(arbData.potentially_stale_surebets || []),
    ...(arbData.recent_historical_surebets || []),
    ...(arbData.stale_surebets || [])
  ];

  const seenKeys = new Set();
  const rawUniqueSurebets = [];
  for (const s of allSurebetSources) {
    const k = `${s.matchup}::${s.market_name}::${(s.legs || []).map(l => `${l.bookmaker}@${l.odd}`).join('|')}`;
    if (!seenKeys.has(k)) {
      seenKeys.add(k);
      rawUniqueSurebets.push(s);
    }
  }

  let verifiedFreshSurebets = [];
  let potentiallyStaleSurebets = [];
  let recentHistoricalSurebets = [];

  for (const arb of rawUniqueSurebets) {
    const freshMeta = computeDynamicFreshness(arb);
    arb._dynamicFreshness = freshMeta;
    if (freshMeta.tier === 'VERIFIED_FRESH') {
      verifiedFreshSurebets.push(arb);
    } else if (freshMeta.tier === 'POTENTIALLY_STALE') {
      potentiallyStaleSurebets.push(arb);
    } else if (freshMeta.tier === 'RECENT_HISTORICAL') {
      recentHistoricalSurebets.push(arb);
    }
  }

  let nearArbs = (arbData.near_arbs || []).filter(a => {
    const freshMeta = computeDynamicFreshness(a);
    a._dynamicFreshness = freshMeta;
    return freshMeta.tier !== 'EXCLUDED';
  });

  // Filtre per cerca de text
  if (currentSearch) {
    const q = currentSearch.toLowerCase();
    const filterFn = a =>
      a.matchup.toLowerCase().includes(q) ||
      a.market_name.toLowerCase().includes(q) ||
      (a.legs && a.legs.some(l => (l.selection || '').toLowerCase().includes(q)));

    verifiedFreshSurebets = verifiedFreshSurebets.filter(filterFn);
    potentiallyStaleSurebets = potentiallyStaleSurebets.filter(filterFn);
    recentHistoricalSurebets = recentHistoricalSurebets.filter(filterFn);
    nearArbs = nearArbs.filter(filterFn);
  }

  // Render Hero KPI Banner
  if (heroBanner) {
    const maxP = verifiedFreshSurebets.length > 0
      ? Math.max(...verifiedFreshSurebets.map(s => s.profit_pct))
      : (summary.max_profit_pct || 0);
    const avgP = verifiedFreshSurebets.length > 0
      ? (verifiedFreshSurebets.reduce((acc, s) => acc + s.profit_pct, 0) / verifiedFreshSurebets.length)
      : (summary.avg_profit_pct || 0);

    heroBanner.innerHTML = `
      <div class="arb-hero-card">
        <span class="arb-hero-icon">⚡</span>
        <div class="arb-hero-val">${verifiedFreshSurebets.length}</div>
        <div class="arb-hero-lbl">Verificades Recentment (≤60s)</div>
      </div>
      <div class="arb-hero-card glow-green">
        <span class="arb-hero-icon">📈</span>
        <div class="arb-hero-val">+${Number(maxP).toFixed(1)}%</div>
        <div class="arb-hero-lbl">Diferencial Màxim Teòric</div>
      </div>
      <div class="arb-hero-card">
        <span class="arb-hero-icon">💰</span>
        <div class="arb-hero-val">+${Number(avgP).toFixed(1)}%</div>
        <div class="arb-hero-lbl">Retorn Mitjà (Fresc)</div>
      </div>
      <div class="arb-hero-card">
        <span class="arb-hero-icon">🛡️</span>
        <div class="arb-hero-val">30 min</div>
        <div class="arb-hero-lbl">Finestra de Monitoratge</div>
      </div>
    `;
  }

  if (countBadge) countBadge.textContent = `${verifiedFreshSurebets.length} oportunitats`;
  if (countStaleBadge) countStaleBadge.textContent = `${potentiallyStaleSurebets.length} oportunitats`;
  if (countHistoricalBadge) countHistoricalBadge.textContent = `${recentHistoricalSurebets.length} oportunitats`;
  if (countNearBadge) countNearBadge.textContent = `${nearArbs.length} partits`;

  const totalActiveWindowOpps = verifiedFreshSurebets.length + potentiallyStaleSurebets.length + recentHistoricalSurebets.length;
  const oppsVal = document.getElementById('surebet-job-opps');
  if (oppsVal) oppsVal.textContent = `${totalActiveWindowOpps} actives (≤30m)`;

  window.arbitrageRegistry = {};
  const totalBankroll = currentArbTotalStake || 100;

  function renderCard(arb, idx, tierType) {
    const arbKey = `arb_${arb.competition_id || 'match'}_${idx}_${tierType}`;
    window.arbitrageRegistry[arbKey] = arb;

    const legs = arb.legs || [];
    const { bankroll: effectiveBankroll, roundStakes, grossPayouts, minReturn, netProfit, profitPct, isPositive } = computeArbitrageValues(arb, totalBankroll);

    const freshMeta = arb._dynamicFreshness || computeDynamicFreshness(arb);
    const ageMax = freshMeta.ageMax || 0;
    const legAges = freshMeta.legAges || [];

    let badgeClass = 'arb-badge-near';
    let badgeText = `Marge: ${Number(arb.margin_pct || 100).toFixed(1)}%`;
    let cardGlowClass = 'arb-card-near';
    let noticeHtml = '';
    let profitLabel = 'Diferencial Net Teòric:';

    if (tierType === 'fresh') {
      badgeClass = isPositive ? 'arb-badge-profit' : 'arb-badge-near';
      badgeText = isPositive ? `+${profitPct.toFixed(1)}% Teòric (≤60s)` : `Marge: ${Number(arb.margin_pct || 100).toFixed(1)}%`;
      cardGlowClass = 'arb-card-surebet';
      profitLabel = 'Diferencial Net Teòric:';
      noticeHtml = `
        <div class="arb-fresh-notice" style="font-size: 11.5px; color: #10b981; margin-top: 4px; display: flex; align-items: center; gap: 4px; font-weight: 500;">
          <span>⚡</span>
          <span><strong>Captura Recent (≤60s):</strong> Discrepància detectada fa menys d'un minut. Requereix reconfirmació immediata de quotes i liquiditat a les cases abans d'operar.</span>
        </div>
      `;
    } else if (tierType === 'stale') {
      badgeClass = 'arb-badge-stale';
      badgeText = `⚠️ +${profitPct.toFixed(1)}% (1-5 min)`;
      cardGlowClass = 'arb-card-stale';
      profitLabel = 'Benefici Teòric Estimat:';
      noticeHtml = `
        <div class="arb-stale-notice">
          <span>⚠️</span>
          <span><strong>Oportunitat Potencialment Desactualitzada:</strong> Quotes capturades fa entre 1 i 5 minuts (${Math.round(ageMax)}s). Les quotes de les cases poden haver canviat; comprova els preus actuals abans d'apostar.</span>
        </div>
      `;
    } else if (tierType === 'historical') {
      badgeClass = 'arb-badge-historical';
      badgeText = `📜 +${profitPct.toFixed(1)}% (5-30 min)`;
      cardGlowClass = 'arb-card-historical';
      profitLabel = 'Benefici Teòric d\'Arxiu:';
      noticeHtml = `
        <div class="arb-historical-notice">
          <span>📜</span>
          <span><strong>Registre Històric Recent:</strong> Quotes capturades fa entre 5 i 30 minuts (${Math.round(ageMax / 60)} min). Caràcter purament informatiu d'arxiu; benefici NO garantit ja que el mercat sol corregir la discrepància.</span>
        </div>
      `;
    } else if (tierType === 'near') {
      badgeClass = 'arb-badge-near';
      badgeText = `Marge: ${Number(arb.margin_pct || 100).toFixed(1)}%`;
      cardGlowClass = 'arb-card-near';
      profitLabel = 'Dèficit de Marge:';
    }

    const legsHtml = legs.map((leg, i) => {
      const op = getOperatorInfo(leg.bookmaker, leg.url);
      const legAge = legAges[i] != null ? legAges[i] : (leg.age_seconds != null ? leg.age_seconds : null);
      const legCapTimeStr = leg.captured_at
        ? new Date(leg.captured_at).toLocaleTimeString('ca-ES', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
        : '--';
      const legAgeStr = formatRelativeAge(legAge, leg.captured_at);

      const actionBtnHtml = op.hasValidUrl
        ? `<a href="${op.validUrl}" target="_blank" rel="noopener noreferrer" class="btn-arb-leg ${op.btnClass}">${op.actionLabel}</a>`
        : `<span class="btn-arb-leg btn-arb-leg-disabled" title="No hi ha enllaç directe disponible">Sense enllaç directe</span>`;

      return `
        <div class="arb-leg-box">
          <div class="arb-leg-top">
            <span class="arb-bookie-tag ${op.tagClass}">${op.icon} ${op.name}</span>
            <span class="arb-leg-odd">@${Number(leg.odd).toFixed(2)}</span>
          </div>
          <div class="arb-leg-selection">${leg.selection}</div>
          <div class="arb-leg-metrics">
            <div class="arb-leg-mitem">
              <span class="arb-m-label">Aposta:</span>
              <span class="arb-m-val stake-val">${roundStakes[i]} €</span>
            </div>
            <div class="arb-leg-mitem">
              <span class="arb-m-label">Pagament brut:</span>
              <span class="arb-m-val return-val">${grossPayouts[i].toFixed(2)} €</span>
            </div>
          </div>
          <div class="arb-leg-time-footer" style="font-size: 10.5px; color: var(--text-muted); margin-top: 4px; display: flex; justify-content: space-between;">
            <span>🕒 Captura: ${legCapTimeStr}</span>
            <span>(${legAgeStr})</span>
          </div>
          ${actionBtnHtml}
        </div>
      `;
    }).join('');

    return `
      <div class="arb-card ${cardGlowClass}">
        <div class="arb-card-header">
          <div>
            <div class="arb-league-badge">${arb.competition_name || arb.competition_id} · 📅 ${arb.date}</div>
            <h3 class="arb-matchup-title">${arb.matchup}</h3>
            <div class="arb-market-name">🎯 ${arb.market_name}</div>
            ${noticeHtml}
          </div>
          <div class="arb-badge-container">
            <span class="arb-badge ${badgeClass}">${badgeText}</span>
          </div>
        </div>

        <div class="arb-legs-container">
          ${legsHtml}
        </div>

        <div class="arb-card-footer">
          <div class="arb-summary-stats">
            <div class="arb-summary-item">
              <span class="arb-sum-lbl">Inversió Total:</span>
              <span class="arb-sum-val">${effectiveBankroll} €</span>
            </div>
            <div class="arb-summary-item">
              <span class="arb-sum-lbl">Retorn Global Mínim:</span>
              <span class="arb-sum-val" style="color: #6ee7b7;">${minReturn.toFixed(2)} €</span>
            </div>
            <div class="arb-summary-item">
              <span class="arb-sum-lbl">${profitLabel}</span>
              <span class="arb-sum-val ${isPositive ? 'profit-green' : 'loss-orange'}">${netProfit >= 0 ? '+' : ''}${netProfit.toFixed(2)} €</span>
            </div>
          </div>
          <div class="arb-card-actions">
            <button class="btn-copy-arb" onclick="copyArbitrageInstructions('${arbKey}')" title="Copia instruccions detallades per executar l'arbitratge pas a pas">
              📋 Copiar Instruccions
            </button>
          </div>
        </div>
      </div>
    `;
  }

  // Nivell 1: Surebets Verificades Recentment (≤60s)
  if (verifiedFreshSurebets.length === 0) {
    containerSurebets.innerHTML = `
      <div class="empty-jornada-state" style="grid-column: 1/-1; text-align: center; padding: 36px 20px; background: rgba(17, 24, 39, 0.45); border: 1px dashed rgba(255,255,255,0.12); border-radius: var(--radius-md);">
        <span style="font-size: 36px; display: block; margin-bottom: 8px;">⚡</span>
        <h3 style="font-size: 17px; color: var(--text-primary); margin-bottom: 6px;">No hi ha surebets verificades recentment (≤60s)</h3>
        <p style="color: var(--text-muted); max-width: 520px; margin: 0 auto; font-size: 13px;">
          El motor només mostra com a verificades les oportunitats amb quotes capturades en els darrers 60 segons i amb decalatge ≤30s entre les cases.
        </p>
      </div>
    `;
  } else {
    containerSurebets.innerHTML = verifiedFreshSurebets.map((arb, i) => renderCard(arb, i, 'fresh')).join('');
  }

  // Nivell 2: Surebets Potencialment Desactualitzades (1–5 min)
  if (containerStale) {
    if (potentiallyStaleSurebets.length === 0) {
      if (staleHeader) staleHeader.style.display = 'none';
      containerStale.style.display = 'none';
      containerStale.innerHTML = '';
    } else {
      if (staleHeader) staleHeader.style.display = 'flex';
      containerStale.style.display = 'grid';
      containerStale.innerHTML = potentiallyStaleSurebets.map((arb, i) => renderCard(arb, i, 'stale')).join('');
    }
  }

  // Nivell 3: Surebets Històriques Recents (5–30 min)
  if (containerHistorical) {
    if (recentHistoricalSurebets.length === 0) {
      if (historicalHeader) historicalHeader.style.display = 'none';
      containerHistorical.style.display = 'none';
      containerHistorical.innerHTML = '';
    } else {
      if (historicalHeader) historicalHeader.style.display = 'flex';
      containerHistorical.style.display = 'grid';
      containerHistorical.innerHTML = recentHistoricalSurebets.map((arb, i) => renderCard(arb, i, 'historical')).join('');
    }
  }

  // Nivell 4: Near-Arbs (Marge Estret)
  if (containerNear) {
    if (nearArbs.length === 0) {
      containerNear.innerHTML = `<p style="grid-column: 1/-1; text-align: center; color: var(--text-muted); padding: 24px;">No hi ha partits en monitorització d'estretor de marge.</p>`;
    } else {
      containerNear.innerHTML = nearArbs.map((arb, i) => renderCard(arb, i, 'near')).join('');
    }
  }
}
window.renderArbitrage = renderArbitrage;

// Temporitzador per a l'envelliment dinàmic en temps real (cada 10s al navegador)
if (typeof window !== 'undefined' && typeof document !== 'undefined' && !window._arbitrageAgingInterval) {
  const agingTimer = setInterval(() => {
    if (typeof document === 'undefined') return;
    const containerSurebets = document.getElementById('surebets-container');
    if (containerSurebets && typeof appData !== 'undefined' && appData && appData.arbitrage) {
      renderArbitrage();
    }
  }, 10000);
  if (agingTimer && typeof agingTimer.unref === 'function') {
    agingTimer.unref();
  }
  window._arbitrageAgingInterval = agingTimer;
}

// -----------------------------------------------------------------------------
// 4. COMBINADES RECOMANADES (6 PER LLIGA + COMBINADES DEL DIA)
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

  const selComboLeague = document.getElementById('select-league-combos');
  if (selComboLeague && selComboLeague.value !== selectedLeagueCombos) {
    selComboLeague.value = selectedLeagueCombos;
  }

  // Actualitzar Desplegable de Jornades de Combinades
  const selCombo = document.getElementById('select-jornada-combo');
  const badgeCombo = document.getElementById('badge-jornada-combo');
  const lblCombo = document.querySelector('label[for="select-jornada-combo"]');

  if (selCombo) {
    if (lblCombo) {
      lblCombo.textContent = (selectedLeagueCombos === 'DAILY') ? '📅 Data de les Combinades:' : '📅 Jornada Combinades:';
    }

    if (selectedLeagueCombos === 'DAILY') {
      const dailySuite = appData.combos?.DAILY || appData.daily_combos;
      if (dailySuite) {
        const availableDates = dailySuite.available_dates || [];
        const activeDate = dailySuite.active_date || dailySuite.date || '';
        let targetDate = selectedJornadaCombo['DAILY'] || activeDate;
        if (!targetDate || targetDate === 'ACTIVE') {
          targetDate = activeDate;
          selectedJornadaCombo['DAILY'] = activeDate;
        }

        let optionsHtml = '';
        availableDates.forEach(dItem => {
          const dDate = (typeof dItem === 'object' && dItem.date) ? dItem.date : dItem;
          const isSelected = (dDate === targetDate) ? 'selected' : '';
          const dayData = dailySuite.by_date ? dailySuite.by_date[dDate] : null;
          const count = (typeof dItem === 'object' && dItem.matches_count != null) ? dItem.matches_count : (dayData ? dayData.matches_count : 0);
          const countStr = count === 1 ? '1 partit' : `${count} partits`;
          let label = (typeof dItem === 'object' && dItem.label) ? dItem.label : (dayData?.formatted_date || dDate);
          optionsHtml += `<option value="${dDate}" ${isSelected}>${label} (${countStr})</option>`;
        });
        selCombo.innerHTML = optionsHtml || `<option value="${targetDate}" selected>Avui (${targetDate})</option>`;

        if (badgeCombo) {
          const isToday = targetDate === (dailySuite.today_date || dailySuite.active_date || '');
          badgeCombo.textContent = isToday ? '🟢 Avui' : `📅 ${targetDate}`;
          badgeCombo.className = isToday ? 'jornada-badge badge-active' : 'jornada-badge badge-future';
        }
      }
    } else {
      const cData = appData.combos[selectedLeagueCombos];
      if (cData) {
        const activeJ = cData.active_jornada || (appData.predictions[selectedLeagueCombos]?.active_jornada) || 8;
        const available = cData.available_jornadas || [activeJ];
        const maxJ = Math.max(...available, activeJ + 2);
        const minJ = 1;

        let targetJ = selectedJornadaCombo[selectedLeagueCombos];
        if (!targetJ || targetJ === 'ACTIVE') {
          targetJ = activeJ;
          selectedJornadaCombo[selectedLeagueCombos] = activeJ;
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
  const leagueKeys = ['DAILY', 'LALIGA', 'PREMIER', 'HYPERMOTION', 'CHAMPIONSHIP', 'BUNDESLIGA', 'LIGUE1', 'SERIEA'];
  let html = '';
  let totalCombosFound = 0;
  let isFutureCombos = false;
  let futureComboJ = 0;

  leagueKeys.forEach(lKey => {
    if (selectedLeagueCombos !== lKey) return;

    if (lKey === 'DAILY') {
      const dailySuite = appData.combos?.DAILY || appData.daily_combos;
      if (!dailySuite) return;

      const activeDate = dailySuite.active_date || dailySuite.date || '';
      const targetDate = selectedJornadaCombo['DAILY'] || activeDate;

      let dayData = dailySuite;
      if (dailySuite.by_date && dailySuite.by_date[targetDate]) {
        dayData = dailySuite.by_date[targetDate];
      }

      const hasMatches = dayData.has_matches !== false && (dayData.matches_count == null || dayData.matches_count > 0);
      const dateFormatted = dayData.date_formatted || targetDate;
      const leagueNote = dayData.league_note || '';

      if (!hasMatches) {
        html += `
          <div class="combos-league-wrapper">
            <div class="league-block-header" style="background: transparent; padding: 0 0 16px 0;">
              <h2 class="league-name-heading" style="font-size: 20px;">📅 Combinades del Dia · ${dateFormatted}</h2>
              <span style="font-size: 13px; color: var(--text-muted); font-family: var(--font-mono);">Sense partits programats</span>
            </div>
            <div class="empty-jornada-state" style="text-align: center; padding: 36px 20px; background: rgba(17, 24, 39, 0.45); border: 1px dashed rgba(255,255,255,0.12); border-radius: var(--radius-md); margin: 6px 0 24px 0;">
              <span style="font-size: 36px; display: block; margin-bottom: 8px;">📅</span>
              <h3 style="font-size: 17px; color: var(--text-primary); margin-bottom: 6px;">No hi ha partits programats per al dia ${dateFormatted}</h3>
              <p style="color: var(--text-muted); max-width: 520px; margin: 0 auto; font-size: 13px; line-height: 1.5;">
                Avui no es disputa cap partit a cap de les 7 lligues analitzades (LaLiga EA Sports, Premier League, LaLiga Hypermotion, EFL Championship, Bundesliga, Ligue 1, Serie A). Pots consultar les combinades d'altres dies amb el selector de dates superior.
              </p>
            </div>
          </div>
        `;
        totalCombosFound += 1;
        return;
      }

      let allCards = [
        ...(dayData.safe || []).map(c => ({ ...c, type: 'safe' })),
        ...(dayData.semi || []).map(c => ({ ...c, type: 'semi' })),
        ...(dayData.risky || []).map(c => ({ ...c, type: 'risky' }))
      ];

      if (allCards.length === 0) return;
      totalCombosFound += allCards.length;

      let noteHtml = '';
      if (leagueNote) {
        noteHtml = `
          <div style="margin-top: 6px;">
            <span style="display: inline-flex; align-items: center; gap: 6px; background: rgba(59, 130, 246, 0.15); border: 1px solid rgba(59, 130, 246, 0.35); color: #93c5fd; padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: 600;">
              ℹ️ ${leagueNote}
            </span>
          </div>
        `;
      }

      const leagueTitle = `📅 Combinades del Dia (${dateFormatted})`;
      html += `
        <div class="combos-league-wrapper">
          <div class="league-block-header" style="background: transparent; padding: 0 0 16px 0;">
            <div style="display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 10px;">
              <div>
                <h2 class="league-name-heading" style="font-size: 20px;">📅 Combinades del Dia · ${dateFormatted}</h2>
                <span style="font-size: 13px; color: var(--accent-emerald); font-family: var(--font-mono);">2 Segures · 2 Semi · 2 Arriscades</span>
                ${noteHtml}
              </div>
              <div style="font-size: 12px; color: var(--text-muted); background: rgba(255,255,255,0.04); padding: 4px 10px; border-radius: 6px; border: 1px solid var(--border-glass);">
                ⚽ Partits analitzats: <strong>${dayData.matches_count || allCards[0]?.legs?.length || 0}</strong>
              </div>
            </div>
          </div>

          <div class="combos-grid">
            ${renderComboCardsHtml(allCards, 'DAILY', leagueTitle)}
          </div>
        </div>
      `;
      return;
    }

    const cData = appData.combos[lKey];
    if (!cData) return;

    const activeJ = cData.active_jornada || (appData.predictions[lKey]?.active_jornada) || 8;
    const targetJ = selectedJornadaCombo[lKey] || activeJ;

    if (targetJ > activeJ || (targetJ === activeJ && cData.has_started === false)) {
      isFutureCombos = true;
      futureComboJ = targetJ;
    }

    let leagueCombos = null;
    if (targetJ === activeJ) {
      leagueCombos = cData;
      if (cData.has_started !== false && (!leagueCombos || (!leagueCombos.safe?.length && !leagueCombos.semi?.length && !leagueCombos.risky?.length))) {
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

    if (cData.has_started !== false && allCards.length === 0 && targetJ === activeJ && window._cachedCombos && window._cachedCombos[lKey]) {
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
    else if (lKey === 'BUNDESLIGA') leagueTitle = '🇩🇪 Bundesliga';
    else if (lKey === 'LIGUE1') leagueTitle = '🇫🇷 Ligue 1';
    else if (lKey === 'SERIEA') leagueTitle = '🇮🇹 Serie A';

    html += `
      <div class="combos-league-wrapper">
        <div class="league-block-header" style="background: transparent; padding: 0 0 16px 0;">
          <h2 class="league-name-heading" style="font-size: 20px;">${leagueTitle} · Suite de 6 Combinades</h2>
          <span style="font-size: 13px; color: var(--accent-emerald); font-family: var(--font-mono);">2 Segures · 2 Semi · 2 Arriscades</span>
        </div>

        <div class="combos-grid">
          ${renderComboCardsHtml(allCards, lKey, leagueTitle)}
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

function renderComboCardsHtml(allCards, lKey, leagueTitle) {
  return allCards.map((c, cardIdx) => {
    const comboKey = `${lKey}_${c.type}_${cardIdx}`;
    const effectiveStake = getComboEffectiveStake(c);
    const isB365 = (currentBookie === 'BET365');

    const baseOdd = isB365
      ? (c.combined_odd_bet365 != null ? c.combined_odd_bet365 : null)
      : (c.combined_odd_winamax || c.combined_odd || 1.0);
    const boosterPct = isB365 ? 0 : (c.booster_pct || 0);
    const boostedOdd = (!isB365 && boosterPct > 0) ? (c.boosted_odd || (baseOdd ? baseOdd * (1.0 + boosterPct / 100.0) : null)) : baseOdd;
    const effectivePayout = (effectiveStake && boostedOdd) ? effectiveStake * boostedOdd : 0;

    window.combosRegistry[comboKey] = {
      ...c,
      stake: effectiveStake,
      boosted_odd: boostedOdd,
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
            <div class="combo-odd-pill ${oddCls}">${boostedOdd != null ? `@ ${boostedOdd.toFixed(2)}` : 'Pendent de quotes'}</div>
            ${hasBooster ? `<div style="font-size: 10px; color: var(--text-muted); margin-top: 3px; font-family: var(--font-mono);">base @${baseOdd != null ? baseOdd.toFixed(2) : '-'}</div>` : `<div style="font-size: 10px; color: var(--text-muted); margin-top: 3px; font-family: var(--font-mono);">${isB365 ? '🟢 Bet365' : '🔴 Winamax'}</div>`}
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
      const mName = cleanMatchup(l.matchup, l);
      let segTitle = `Partit ${lIdx + 1} (${mName}): Pendent`;
      const isSuspended = l.status === 'SUSPENDED' || (l.actual_result && l.actual_result.toLowerCase().includes('suspès'));
      if (l.status === 'WON') {
        segCls = 'seg-won';
        segTitle = `Partit ${lIdx + 1} (${mName}): Encertat! (${l.actual_result || ''})`;
      } else if (l.status === 'LOST') {
        segCls = 'seg-lost';
        segTitle = `Partit ${lIdx + 1} (${mName}): Fallat (${l.actual_result || ''})`;
      } else if (isSuspended) {
        segCls = 'seg-suspended';
        segTitle = `Partit ${lIdx + 1} (${mName}): ⏸️ Suspès (${l.actual_result || ''})`;
      }
      return `<div class="combo-progress-segment ${segCls}" title="${segTitle}"></div>`;
    }).join('')}
          </div>
        </div>

        <ul class="combo-legs-list">
          ${legs.map(l => {
      const isSusp = l.status === 'SUSPENDED' || (l.actual_result && l.actual_result.toLowerCase().includes('suspès'));
      let legBadge = `<span class="leg-pill pill-pending">⏳ Pendent (${l.date || 'Properament'})</span>`;
      let legItemCls = 'leg-pending';
      if (l.status === 'WON') {
        legBadge = `<span class="leg-pill pill-won">✅ ${l.actual_result || 'Encertat'}</span>`;
        legItemCls = 'leg-won';
      } else if (l.status === 'LOST') {
        legBadge = `<span class="leg-pill pill-lost">❌ ${l.actual_result || 'Fallat'}</span>`;
        legItemCls = 'leg-lost';
      } else if (isSusp) {
        legBadge = `<span class="leg-pill pill-suspended" style="background: rgba(245, 158, 11, 0.18); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.4);">⏸️ ${l.actual_result || 'Partit suspès'}</span>`;
        legItemCls = 'leg-suspended';
      }

      let catIcon = '🏷️';
      const cat = l.category || '';
      if (cat === 'Gols') catIcon = '⚽';
      else if (cat === 'Córners') catIcon = '🚩';
      else if (cat === 'Targetes') catIcon = '🟨';
      else if (cat === 'BTTS') catIcon = '🤝';
      else if (cat === 'Doble Oportunitat') catIcon = '🛡️';
      else if (cat === '1X2') catIcon = '🎯';
      else if (cat === 'Marcador Exacte') catIcon = '🔢';

      const catTag = cat ? `<span class="leg-category-tag">${catIcon} ${cat}</span>` : '';
      const modelProb = l.model_prob != null ? l.model_prob.toFixed(1) : '-';
      const legOdd = isB365
        ? (l.bookie_odd_bet365 != null ? l.bookie_odd_bet365.toFixed(2) : '-')
        : (l.bookie_odd_winamax != null ? l.bookie_odd_winamax.toFixed(2) : (l.bookie_odd != null ? l.bookie_odd.toFixed(2) : '-'));

      return `
              <li class="combo-leg-item ${legItemCls}">
                <div class="leg-desc">
                  <div style="display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-bottom: 2px;">
                    <span class="leg-matchup">${cleanMatchup(l.matchup, l)}</span>
                    ${catTag}
                  </div>
                  <div class="leg-name">${cleanSelectionName(l.selection_name)}</div>
                  <div style="margin-top: 4px;">${legBadge}</div>
                </div>
                <div class="leg-odds-box">
                  <div class="leg-odd" title="${isB365 ? 'Cuota Bet365' : 'Cuota Winamax'}">@${legOdd}</div>
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
          ${isB365 ? `
            <div class="combo-btn-row">
              <button type="button" class="btn-track-combo" onclick="trackComboBet('${comboKey}')" title="Afegir aquesta combinada al teu compte">
                ➕ Apostar al meu compte
              </button>
              <button type="button" class="btn-copy-b365" onclick="copyBet365Slip('${comboKey}')" title="Copiar seleccions i enllaç de la combinada Bet365">
                📋 Copiar Link Bet365
              </button>
            </div>
            <div style="display: flex; gap: 8px; align-items: center; width: 100%;">
              <a href="${getValidBet365Url(c.bet365_url)}" target="_blank" rel="noopener noreferrer" class="combo-direct-link bet365-link" style="flex: 1;">
                🟢 Apostar a Bet365 ↗
              </a>
              <button type="button" class="btn-assistant" onclick="openQuickAssistant('${comboKey}')" title="Obre assistent interactiu pas a pas" style="padding: 10px 14px;">
                📲 Assistent
              </button>
            </div>
            <div class="bet365-direct-url-box">
              <span class="bet365-url-lbl">🔗 Link Bet:</span>
              <a href="${getValidBet365Url(c.bet365_url)}" target="_blank" rel="noopener noreferrer" class="bet365-url-text">${getValidBet365Url(c.bet365_url)}</a>
            </div>
          ` : `
            <div class="combo-btn-row">
              <button type="button" class="btn-track-combo" onclick="trackComboBet('${comboKey}')" title="Afegir aquesta combinada al teu compte">
                ➕ Apostar al meu compte
              </button>
              <button type="button" class="btn-assistant" onclick="openQuickAssistant('${comboKey}')" title="Obre assistent interactiu pas a pas (Mòbil i PC)">
                📲 Assistent Ràpid
              </button>
            </div>
            <a href="${c.winamax_url || 'https://www.winamax.es'}" target="_blank" rel="noopener noreferrer" class="combo-direct-link">
              Obrir a Winamax ↗
            </a>
          `}
        </div>
      </div>
    `;
  }).join('');
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
    } catch (e) { }
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
      if (p.leagueRankings) selectedLeagueRankings = p.leagueRankings;
      if (p.leaguePredictions) selectedLeaguePredictions = p.leaguePredictions;
      if (p.leagueValue) selectedLeagueValue = p.leagueValue;
      if (p.leagueCombos) selectedLeagueCombos = p.leagueCombos;
      if (p.favoriteLeague) {
        currentLeague = p.favoriteLeague;
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
          leagueRankings: selectedLeagueRankings,
          leaguePredictions: selectedLeaguePredictions,
          leagueValue: selectedLeagueValue,
          leagueCombos: selectedLeagueCombos,
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
            match = combosList.find(c => (c.profile || '').toUpperCase() === prof && (!b.competition_id || b.competition_id === 'MULTI' || b.competition_id === 'DAILY' || c.leagueKey === b.competition_id));
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
            const mName = cleanMatchup(l.matchup, l);
            let segTitle = `Partit ${lIdx + 1} (${mName}): Pendent`;
            if (l.status === 'WON') {
              segCls = 'seg-won';
              segTitle = `Partit ${lIdx + 1} (${mName}): Encertat! (${l.actual_result || ''})`;
            } else if (l.status === 'LOST') {
              segCls = 'seg-lost';
              segTitle = `Partit ${lIdx + 1} (${mName}): Fallat (${l.actual_result || ''})`;
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
            const cMatch = cleanMatchup(l.matchup, l);
            const homeShort = cMatch.includes(' vs ') ? cMatch.split(' vs ')[0] : cMatch;
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
    competition_id: compKey || 'DAILY',
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
window.onComboStakeChange = function (comboId, val) {
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
window.onComboStatusChange = function (comboId, status) {
  if (!bankrollSimState.comboOverrides[comboId]) {
    bankrollSimState.comboOverrides[comboId] = {};
  }
  bankrollSimState.comboOverrides[comboId].simStatus = status;

  saveBankrollSimState();
  const calc = calculateBankrollSimulation();
  updateBankrollDom(calc);
};

// Handler per aplicar imports específics a tota una jornada concreta
window.applyRoundUnits = function (roundIdx) {
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
  } catch (e) { }

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
window.toggleLedgerDetails = function (idx) {
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
    } catch (e) { }

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

      const isB365 = (currentBookie === 'BET365');
      const legOdd = isB365
        ? (leg.bookie_odd_bet365 != null ? leg.bookie_odd_bet365.toFixed(2) : '-')
        : (leg.bookie_odd_winamax != null ? leg.bookie_odd_winamax.toFixed(2) : (leg.bookie_odd != null ? leg.bookie_odd.toFixed(2) : '-'));
      const modelProb = leg.model_prob != null ? leg.model_prob.toFixed(1) : '-';
      const legUrl = isB365
        ? getValidBet365Url(leg.url_bet365 || combo.bet365_url)
        : (leg.url || combo.winamax_url || 'https://www.winamax.es');

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
            <div class="qa-instruction-label">${isB365 ? '👉 Casella exacta a marcar a Bet365:' : '👉 Casella exacta a marcar a Winamax:'}</div>
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
  // Quan l'usuari clica per obrir el partit, marquem automàticament la casella
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

  const isB365 = (currentBookie === 'BET365');
  const boosterPct = isB365 ? 0 : (combo.booster_pct || 0);
  const odd = isB365
    ? (combo.combined_odd_bet365 || combo.combined_odd || 1.0)
    : (combo.boosted_odd || combo.combined_odd || 1.0);
  const probConjunta = combo.combined_prob_pct != null ? combo.combined_prob_pct.toFixed(1) : '-';
  const bookieName = isB365 ? 'BET365' : 'WINAMAX';
  const directLink = isB365 ? getValidBet365Url(combo.bet365_url) : (combo.winamax_url || 'https://www.winamax.es');

  let text = `⚽ PREDICCIONS FUTBOL · ${bookieName} · ${combo.profile}\n`;
  text += `Lliga: ${combo.leagueTitle || ''}\n`;
  text += `Cuota Total: @${Number(odd).toFixed(2)}${boosterPct > 0 ? ` (+${boosterPct}% Booster)` : ''}\n`;
  text += `Enllaç Directe ${bookieName}: ${directLink}\n`;
  text += `Probabilitat Conjunta Model: ${probConjunta}%\n`;
  text += `Inversió Recomanada: ${combo.stake.toFixed(2)} € (Retorn: +${combo.potential_payout.toFixed(2)} €)\n\n`;
  text += `SELECCIONS DEL CUPÓ:\n`;

  (combo.legs || []).forEach((leg, i) => {
    const lOdd = isB365
      ? (leg.bookie_odd_bet365 != null ? leg.bookie_odd_bet365.toFixed(2) : '-')
      : (leg.bookie_odd_winamax != null ? leg.bookie_odd_winamax.toFixed(2) : (leg.bookie_odd != null ? leg.bookie_odd.toFixed(2) : '-'));
    text += `${i + 1}. [${leg.matchup}] 👉 ${leg.selection_name} (@${lOdd}) [Model: ${leg.model_prob}%]\n`;
    const lUrl = isB365 ? (leg.url_bet365 || directLink) : (leg.url || directLink);
    if (lUrl) {
      text += `   Enllaç: ${lUrl}\n`;
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
window.onJornadaChange = typeof onJornadaChange !== 'undefined' ? onJornadaChange : (window.onJornadaChange || null);
window.UserAuth = UserAuth;
window.setBookie = setBookie;
window.copyBet365Slip = copyBet365Slip;
window.openOddsModal = openOddsModal;
window.openOddsModalForMatch = openOddsModalForMatch;
window.closeOddsModal = closeOddsModal;
window.saveManualOdds = saveManualOdds;
window.copyOverridesJson = copyOverridesJson;
window.checkOddsAlerts = checkOddsAlerts;

// =============================================================================
// GESTIÓ D'ALERTES DE QUOTES MANCANTS I EDICIÓ MANUAL
// =============================================================================

function applyLocalOddsOverrides() {
  if (!appData || !appData.predictions) return;
  try {
    const raw = localStorage.getItem('manual_odds_overrides');
    if (!raw) return;
    const overrides = JSON.parse(raw);
    for (const [compId, compObj] of Object.entries(appData.predictions)) {
      const matches = compObj.matches || [];
      matches.forEach(m => {
        const hName = m.home_team ? m.home_team.name : '';
        const aName = m.away_team ? m.away_team.name : '';
        const normH = hName.toLowerCase().replace(/[^a-z0-9]/g, '');
        const normA = aName.toLowerCase().replace(/[^a-z0-9]/g, '');
        const key1 = `${compId}_${normH}_${normA}`;
        const key2 = `${normH}_${normA}`;
        const ov = overrides[key1] || overrides[key2];
        if (ov) {
          if (ov.winamax && ov.winamax['1']) {
            m.odds_1x2_winamax = { ...m.odds_1x2_winamax, ...ov.winamax };
            m.odds_1x2 = { ...m.odds_1x2, ...ov.winamax };
            m.has_winamax_odds = true;
          }
          if (ov.bet365 && ov.bet365['1']) {
            m.odds_1x2_bet365 = { ...m.odds_1x2_bet365, ...ov.bet365 };
            m.has_bet365_odds = true;
          }
          if (m.has_winamax_odds && m.has_bet365_odds) {
            m.missing_odds = false;
            m.missing_bookmakers = [];
          }
        }
      });
    }
  } catch (e) {
    console.warn("Error aplicant overrides locals:", e);
  }
}

function getActiveOddsAlerts() {
  if (!appData) return [];
  const baseAlerts = appData.odds_alerts || [];
  let overrides = {};
  try {
    overrides = JSON.parse(localStorage.getItem('manual_odds_overrides') || '{}');
  } catch (e) { }

  const now = new Date();

  return baseAlerts.filter(a => {
    // 1. Si el partit ja té quotes introduïdes manualment
    const ov = overrides[a.id];
    if (ov) {
      const hasW = !a.missing_winamax || (ov.winamax && ov.winamax['1']);
      const hasB = !a.missing_bet365 || (ov.bet365 && ov.bet365['1']);
      if (hasW && hasB) return false;
    }

    // 2. Comprovar si el partit ja està finalitzat a appData
    if (appData.predictions && appData.predictions[a.competition_id]) {
      const compMatches = appData.predictions[a.competition_id].matches || [];
      const mMatch = compMatches.find(m => {
        const hName = (m.home_team ? m.home_team.name : '').toLowerCase();
        const aName = (m.away_team ? m.away_team.name : '').toLowerCase();
        const targetH = (a.home_team || '').toLowerCase();
        const targetA = (a.away_team || '').toLowerCase();
        return (
          (hName.includes(targetH) || targetH.includes(hName)) &&
          (aName.includes(targetA) || targetA.includes(aName))
        );
      });
      if (mMatch) {
        if (mMatch.status === 'FINISHED' || mMatch.home_goals !== null || mMatch.final_score) {
          return false;
        }
        if (mMatch.has_winamax_odds && mMatch.has_bet365_odds) {
          return false;
        }
      }
    }

    // 3. Comprovar si la data i hora d'inici ja ha passat (partit passat o començat)
    if (a.date && a.date !== 'Pendent') {
      try {
        let pDate = null;
        if (a.date.includes('.')) {
          const parts = a.date.trim().split(' ');
          const dParts = parts[0].split('.');
          const tParts = (parts[1] || '00:00').split(':');
          pDate = new Date(parseInt(dParts[2]), parseInt(dParts[1]) - 1, parseInt(dParts[0]), parseInt(tParts[0]), parseInt(tParts[1]));
        } else if (a.date.includes('-')) {
          pDate = new Date(a.date.replace(' ', 'T'));
        }
        if (pDate && !isNaN(pDate.getTime()) && pDate < now) {
          return false;
        }
      } catch (err) { }
    }

    return true;
  });
}

function checkOddsAlerts() {
  const banner = document.getElementById('odds-alert-banner');
  const msgEl = document.getElementById('odds-alert-msg');
  if (!banner) return;

  const activeAlerts = getActiveOddsAlerts();
  if (activeAlerts && activeAlerts.length > 0) {
    banner.style.display = 'block';
    if (msgEl) {
      msgEl.innerHTML = `S'han detectat <strong>${activeAlerts.length} partits</strong> sense quotes oficials a Winamax o Bet365. El model no inventa dades falses. Introdueix-les manualment:`;
    }
  } else {
    banner.style.display = 'none';
  }
}

function openOddsModal() {
  const modal = document.getElementById('odds-modal-overlay');
  const listEl = document.getElementById('odds-modal-list');
  if (!modal || !listEl) return;

  const alerts = getActiveOddsAlerts();
  let overrides = {};
  try {
    overrides = JSON.parse(localStorage.getItem('manual_odds_overrides') || '{}');
  } catch (e) { }

  if (!alerts || alerts.length === 0) {
    listEl.innerHTML = `
      <div style="text-align: center; padding: 30px; color: var(--accent-emerald);">
        <span style="font-size: 32px;">✅</span>
        <h4 style="margin: 10px 0 5px 0;">Tots els partits tenen quotes oficials!</h4>
        <p style="color: var(--text-muted); font-size: 13px;">No hi ha cap partit actiu pendent de quotes a Winamax ni a Bet365.</p>
      </div>
    `;
  } else {
    listEl.innerHTML = alerts.map(a => {
      const ov = overrides[a.id] || {};
      const wOv = ov.winamax || {};
      const bOv = ov.bet365 || {};

      return `
        <div class="odds-edit-card" data-alert-id="${a.id}">
          <div class="odds-edit-card-header">
            <div>
              <div class="odds-match-name">${a.matchup}</div>
              <div class="odds-match-meta">
                <span>🏆 ${a.competition_name || a.competition_id}</span>
                <span>📅 ${a.date || 'Pendent'}</span>
              </div>
            </div>
            <div style="display: flex; gap: 6px;">
              ${a.missing_winamax ? `<span class="missing-badge missing-badge-winamax">Falta Winamax</span>` : ''}
              ${a.missing_bet365 ? `<span class="missing-badge missing-badge-bet365">Falta Bet365</span>` : ''}
            </div>
          </div>

          <div class="odds-inputs-grid">
            <!-- Winamax Inputs -->
            <div class="odds-bookie-column">
              <span class="odds-bookie-label">🔴 Winamax (1 - X - 2):</span>
              <div class="odds-inputs-row">
                <div class="odds-input-group">
                  <label>1</label>
                  <input type="number" step="0.01" min="1.01" class="odds-input-field input-w1" placeholder="ex: 2.15" value="${wOv['1'] || ''}">
                </div>
                <div class="odds-input-group">
                  <label>X</label>
                  <input type="number" step="0.01" min="1.01" class="odds-input-field input-wx" placeholder="ex: 3.30" value="${wOv['X'] || ''}">
                </div>
                <div class="odds-input-group">
                  <label>2</label>
                  <input type="number" step="0.01" min="1.01" class="odds-input-field input-w2" placeholder="ex: 3.50" value="${wOv['2'] || ''}">
                </div>
              </div>
            </div>

            <!-- Bet365 Inputs -->
            <div class="odds-bookie-column">
              <span class="odds-bookie-label">🟢 Bet365 (1 - X - 2):</span>
              <div class="odds-inputs-row">
                <div class="odds-input-group">
                  <label>1</label>
                  <input type="number" step="0.01" min="1.01" class="odds-input-field input-b1" placeholder="ex: 2.20" value="${bOv['1'] || ''}">
                </div>
                <div class="odds-input-group">
                  <label>X</label>
                  <input type="number" step="0.01" min="1.01" class="odds-input-field input-bx" placeholder="ex: 3.35" value="${bOv['X'] || ''}">
                </div>
                <div class="odds-input-group">
                  <label>2</label>
                  <input type="number" step="0.01" min="1.01" class="odds-input-field input-b2" placeholder="ex: 3.45" value="${bOv['2'] || ''}">
                </div>
              </div>
            </div>
          </div>
        </div>
      `;
    }).join('');
  }

  modal.style.display = 'flex';
}

function openOddsModalForMatch(compId, home, away) {
  openOddsModal();
}

function closeOddsModal() {
  const modal = document.getElementById('odds-modal-overlay');
  if (modal) modal.style.display = 'none';
}

function saveManualOdds() {
  const cards = document.querySelectorAll('.odds-edit-card');
  let overrides = {};
  try {
    overrides = JSON.parse(localStorage.getItem('manual_odds_overrides') || '{}');
  } catch (e) { }

  let savedCount = 0;
  cards.forEach(card => {
    const aid = card.getAttribute('data-alert-id');
    const w1 = parseFloat(card.querySelector('.input-w1')?.value || 0);
    const wx = parseFloat(card.querySelector('.input-wx')?.value || 0);
    const w2 = parseFloat(card.querySelector('.input-w2')?.value || 0);
    const b1 = parseFloat(card.querySelector('.input-b1')?.value || 0);
    const bx = parseFloat(card.querySelector('.input-bx')?.value || 0);
    const b2 = parseFloat(card.querySelector('.input-b2')?.value || 0);

    if ((w1 && wx && w2) || (b1 && bx && b2)) {
      overrides[aid] = overrides[aid] || {};
      if (w1 && wx && w2) {
        overrides[aid].winamax = { '1': w1, 'X': wx, '2': w2 };
      }
      if (b1 && bx && b2) {
        overrides[aid].bet365 = { '1': b1, 'X': bx, '2': b2 };
      }
      savedCount++;
    }
  });

  localStorage.setItem('manual_odds_overrides', JSON.stringify(overrides));
  applyLocalOddsOverrides();
  renderPredictions();
  renderValueBets();
  renderArbitrage();
  checkOddsAlerts();
  closeOddsModal();

  alert(`✅ S'han desat correctament les quotes de ${savedCount} partits! S'han actualitzat les prediccions i combinades a la pantalla.`);
}

function copyOverridesJson() {
  let overrides = {};
  try {
    overrides = JSON.parse(localStorage.getItem('manual_odds_overrides') || '{}');
  } catch (e) { }

  const jsonStr = JSON.stringify(overrides, null, 2);
  if (navigator.clipboard) {
    navigator.clipboard.writeText(jsonStr).then(() => {
      alert("📋 JSON copiat al porta-retalls!\nPots enganxar-lo al fitxer data/manual_odds_override.json del servidor si vols persistir-lo al núvol.");
    }).catch(() => {
      prompt("Copia el JSON manualment per a data/manual_odds_override.json:", jsonStr);
    });
  } else {
    prompt("Copia el JSON manualment per a data/manual_odds_override.json:", jsonStr);
  }
}

// =============================================================================
// FITXA DETALLADA DE PARTIT (FASE 2)
// =============================================================================
let currentDetailsMatch = null;
let currentLineCategory = 'corners';
let currentLineThreshold = 8.5;

function openMatchDetailsModal(matchId) {
  if (!appData || !appData.predictions) return;

  let foundMatch = null;
  const comps = Object.keys(appData.predictions);
  for (const c of comps) {
    const pData = appData.predictions[c];
    if (!pData) continue;
    const matches = pData.matches || [];
    for (const m of matches) {
      if ((m.match_id && m.match_id === matchId) || (m.id && m.id === matchId)) {
        foundMatch = m;
        break;
      }
    }
    if (foundMatch) break;
  }

  if (!foundMatch) {
    alert("No s'han pogut carregar les dades detallades d'aquest partit.");
    return;
  }

  currentDetailsMatch = foundMatch;
  renderMatchDetailsContent(foundMatch);

  const modal = document.getElementById('modal-match-details');
  if (modal) {
    modal.style.display = 'flex';
    document.body.style.overflow = 'hidden';
  }
}

function closeMatchDetailsModal() {
  const modal = document.getElementById('modal-match-details');
  if (modal) {
    modal.style.display = 'none';
    document.body.style.overflow = '';
  }
}

function switchMatchTab(tabName) {
  const tabs = document.querySelectorAll('.md-tab-btn');
  tabs.forEach(t => {
    t.classList.toggle('active', t.getAttribute('data-tab') === tabName);
  });

  const panes = ['summary', 'comparison', 'stats', 'markets', 'lines', 'recommendations'];
  panes.forEach(p => {
    const el = document.getElementById(`md-pane-${p}`);
    if (el) {
      el.style.display = (p === tabName) ? 'block' : 'none';
    }
  });

  if (tabName === 'lines' && currentDetailsMatch) {
    renderLinesTab(currentDetailsMatch.match_details || {});
  }
}

function renderMatchDetailsContent(match) {
  const dt = match.match_details || {};
  const summary = dt.summary || {
    competition_name: match._compName || 'Competició',
    jornada: match.jornada || '-',
    date_time: match.date || 'Pendent',
    status: match.status || 'SCHEDULED',
    home_team: match.home_team || { name: 'Local', elo: '-' },
    away_team: match.away_team || { name: 'Visitant', elo: '-' }
  };

  // 1. Capçalera del Modal
  const compBadge = document.getElementById('md-comp-badge');
  if (compBadge) compBadge.textContent = summary.competition_name || 'Lliga';
  const jBadge = document.getElementById('md-jornada-badge');
  if (jBadge) jBadge.textContent = `Jornada ${summary.jornada}`;
  const dateBadge = document.getElementById('md-date-badge');
  if (dateBadge) dateBadge.textContent = `📅 ${summary.date_time || 'Pendent'}`;
  const statusBadge = document.getElementById('md-status-badge');
  if (statusBadge) statusBadge.textContent = summary.status === 'FINISHED' ? '🏁 FINALITZAT' : (summary.status === 'SUSPENDED' ? '⏸️ SUSPÈS' : '⏳ PENDENT');

  const homeName = document.getElementById('md-home-name');
  if (homeName) homeName.textContent = summary.home_team.name;
  const homeSub = document.getElementById('md-home-sub');
  if (homeSub) homeSub.textContent = `Elo: ${summary.home_team.elo || '-'}`;

  const awayName = document.getElementById('md-away-name');
  if (awayName) awayName.textContent = summary.away_team.name;
  const awaySub = document.getElementById('md-away-sub');
  if (awaySub) awaySub.textContent = `Elo: ${summary.away_team.elo || '-'}`;

  const vsLabel = document.getElementById('md-vs-label');
  const scoreDisp = document.getElementById('md-score-display');
  if (match.status === 'FINISHED' && match.final_score) {
    if (vsLabel) vsLabel.style.display = 'none';
    if (scoreDisp) {
      scoreDisp.style.display = 'block';
      scoreDisp.textContent = match.final_score;
    }
  } else {
    if (vsLabel) vsLabel.style.display = 'inline-block';
    if (scoreDisp) scoreDisp.style.display = 'none';
  }

  // 2. Renderitzar Panells
  renderSummaryTab(dt, match);
  renderComparisonTab(dt);
  renderStatsTab(dt, match);
  renderMarketsTab(dt);
  renderLinesTab(dt);
  renderRecommendationsTab(dt);

  // Inicialitzar a la pestanya de resum
  switchMatchTab('summary');
}

function renderSummaryTab(dt, match) {
  const container = document.getElementById('md-pane-summary');
  if (!container) return;

  const comp = dt.team_comparison || {};
  const hSt = comp.home_standings || {};
  const aSt = comp.away_standings || {};
  const ref = comp.referee || { name: 'Pendent de designació', strictness_index: null };
  const sum = dt.summary || {};

  container.innerHTML = `
    <!-- Targeta Gols Esperats pel Model (λ Poisson) -->
    <div class="recom-card-box highlight" style="margin-bottom: 16px;">
      <div class="recom-title">⚽ Gols Esperats pel Model Matemàtic (λ Poisson)</div>
      <div class="recom-desc">
        Taxa total estimada pel model: <strong>${sum.lambda_goals_total != null ? sum.lambda_goals_total : '-'} gols esperats</strong>
        (Local: <strong>${sum.lambda_goals_home != null ? sum.lambda_goals_home : '-'}</strong> · Visitant: <strong>${sum.lambda_goals_away != null ? sum.lambda_goals_away : '-'}</strong>).
        ${sum.lambda_corners_total ? ` Mitjana de córners prevista: <strong>${sum.lambda_corners_total}</strong>.` : ''}
        ${sum.lambda_cards_total ? ` Mitjana de targetes prevista: <strong>${sum.lambda_cards_total}</strong>.` : ''}
        <div style="font-size: 11px; color: var(--text-muted); margin-top: 4px;">* Aquestes dades representen les mitjanes (λ) de les distribucions de Poisson del model, diferenciades de les mètriques d'xG històric basades en tirs.</div>
      </div>
    </div>

    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px;">
      <div class="recom-card-box">
        <div class="recom-title">🏠 ${match.home_team?.name || 'Local'}</div>
        <div style="font-size: 13px; color: var(--text-secondary); line-height: 1.6;">
          ${hSt.available ? `
            <div><strong>Posició:</strong> ${hSt.rank}è de la lliga (${hSt.points} pts)</div>
            <div><strong>Balanç:</strong> ${hSt.won}V · ${hSt.drawn}E · ${hSt.lost}D (${hSt.played} PJ)</div>
            <div><strong>Gols:</strong> ${hSt.gf} a favor, ${hSt.ga} en contra (DG: ${hSt.gd >= 0 ? '+' : ''}${hSt.gd})</div>
            <div style="margin-top: 6px;"><strong>Últims 5:</strong> ${(hSt.form_5 || []).map(f => `<span class="form-badge-pill form-badge-${f.toLowerCase()}">${f}</span>`).join('')}</div>
          ` : `
            <div style="color: var(--text-muted);">Classificació no disponible per a aquesta lliga.</div>
          `}
        </div>
      </div>

      <div class="recom-card-box">
        <div class="recom-title">✈️ ${match.away_team?.name || 'Visitant'}</div>
        <div style="font-size: 13px; color: var(--text-secondary); line-height: 1.6;">
          ${aSt.available ? `
            <div><strong>Posició:</strong> ${aSt.rank}è de la lliga (${aSt.points} pts)</div>
            <div><strong>Balanç:</strong> ${aSt.won}V · ${aSt.drawn}E · ${aSt.lost}D (${aSt.played} PJ)</div>
            <div><strong>Gols:</strong> ${aSt.gf} a favor, ${aSt.ga} en contra (DG: ${aSt.gd >= 0 ? '+' : ''}${aSt.gd})</div>
            <div style="margin-top: 6px;"><strong>Últims 5:</strong> ${(aSt.form_5 || []).map(f => `<span class="form-badge-pill form-badge-${f.toLowerCase()}">${f}</span>`).join('')}</div>
          ` : `
            <div style="color: var(--text-muted);">Classificació no disponible per a aquesta lliga.</div>
          `}
        </div>
      </div>
    </div>

    <!-- Àrbitre oficial -->
    <div class="recom-card-box highlight">
      <div class="recom-title">⚖️ Designació Arbitral Oficial: ${ref.name}</div>
      <div class="recom-desc">
        ${ref.is_official ? `
          Designació confirmada pel CTA/PGMOL. Estadístiques històriques reals:
          <strong>${ref.yellow_avg != null ? ref.yellow_avg : 'N/D'}</strong> targetes grogues per partit,
          <strong>${ref.red_avg != null ? ref.red_avg : 'N/D'}</strong> targetes vermelles i
          <strong>${ref.fouls_avg != null ? ref.fouls_avg : 'N/D'}</strong> faltes de mitjana.
          Índex de severitat: <strong>${ref.strictness_index != null ? ref.strictness_index : '1.0'}</strong>.
        ` : `
          Àrbitre oficial pendent de designació per la federació. S'utilitzen valors neutres de mitjana de la competició.
        `}
      </div>
    </div>
  `;
}

function renderComparisonTab(dt) {
  const container = document.getElementById('md-pane-comparison');
  if (!container) return;

  const comp = dt.team_comparison || {};
  const hSt = comp.home_standings || {};
  const aSt = comp.away_standings || {};
  const hAdv = comp.home_advanced || {};
  const aAdv = comp.away_advanced || {};

  container.innerHTML = `
    <div class="md-table-wrap">
      <table class="md-table">
        <thead>
          <tr>
            <th>Mètrica Real Històrica</th>
            <th>${dt.summary?.home_team?.name || 'Local'} (Local)</th>
            <th>${dt.summary?.away_team?.name || 'Visitant'} (Visitant)</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><strong>Posició / Punts</strong></td>
            <td>${hSt.available ? `${hSt.rank}è · ${hSt.points} pts` : 'N/D'}</td>
            <td>${aSt.available ? `${aSt.rank}è · ${aSt.points} pts` : 'N/D'}</td>
          </tr>
          <tr>
            <td><strong>Partits Jugats (V/E/D)</strong></td>
            <td>${hSt.available ? `${hSt.played} (${hSt.won}-${hSt.drawn}-${hSt.lost})` : 'N/D'}</td>
            <td>${aSt.available ? `${aSt.played} (${aSt.won}-${aSt.drawn}-${aSt.lost})` : 'N/D'}</td>
          </tr>
          <tr>
            <td><strong>Gols (Marcats - Concedits)</strong></td>
            <td>${hSt.available ? `${hSt.gf} - ${hSt.ga} (DG: ${hSt.gd})` : 'N/D'}</td>
            <td>${aSt.available ? `${aSt.gf} - ${aSt.ga} (DG: ${aSt.gd})` : 'N/D'}</td>
          </tr>
          <tr>
            <td><strong>Rècord Local / Visitant</strong></td>
            <td>${hAdv.home_record ? `${hAdv.home_record.played} PJ (${hAdv.home_record.won}V-${hAdv.home_record.drawn}E-${hAdv.home_record.lost}D)` : 'N/D'}</td>
            <td>${aAdv.away_record ? `${aAdv.away_record.played} PJ (${aAdv.away_record.won}V-${aAdv.away_record.drawn}E-${aAdv.away_record.lost}D)` : 'N/D'}</td>
          </tr>
          <tr>
            <td><strong>xG Real Històric de Tirs (Flashscore)</strong></td>
            <td>${hAdv.xg_for_per_game != null ? `${hAdv.xg_for_per_game} a favor / ${hAdv.xg_against_per_game} en contra` : 'Dada no disponible'}</td>
            <td>${aAdv.xg_for_per_game != null ? `${aAdv.xg_for_per_game} a favor / ${aAdv.xg_against_per_game} en contra` : 'Dada no disponible'}</td>
          </tr>
          <tr>
            <td><strong>Córners Mitjans (Favor / Contra)</strong></td>
            <td>${hAdv.corners_for_per_game != null ? `${hAdv.corners_for_per_game} / ${hAdv.corners_against_per_game}` : 'Dada no disponible'}</td>
            <td>${aAdv.corners_for_per_game != null ? `${aAdv.corners_for_per_game} / ${aAdv.corners_against_per_game}` : 'Dada no disponible'}</td>
          </tr>
          <tr>
            <td><strong>Targetes Grogues / Partit</strong></td>
            <td>${hAdv.yellow_cards_per_game != null ? `${hAdv.yellow_cards_per_game} 🟨/p` : 'Dada no disponible'}</td>
            <td>${aAdv.yellow_cards_per_game != null ? `${aAdv.yellow_cards_per_game} 🟨/p` : 'Dada no disponible'}</td>
          </tr>
        </tbody>
      </table>
    </div>
  `;
}

function renderStatsTab(dt, match) {
  const container = document.getElementById('md-pane-stats');
  if (!container) return;

  const hSt = dt.team_comparison?.home_standings || {};
  const aSt = dt.team_comparison?.away_standings || {};

  container.innerHTML = `
    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">
      <div class="recom-card-box">
        <div class="recom-title">Forma Recent ${match.home_team?.name}</div>
        <div style="margin: 10px 0;">
          ${(hSt.form_5 || []).map(f => `<span class="form-badge-pill form-badge-${f.toLowerCase()}">${f}</span>`).join('') || '<span style="color: var(--text-muted);">N/D</span>'}
        </div>
        <div style="font-size: 12.5px; color: var(--text-secondary); line-height: 1.5;">
          ${hSt.available ? `Balanç de punts recents i estabilitat competitiva segons el model Elo (${match.home_team?.elo}).` : 'Dades de forma no disponibles.'}
        </div>
      </div>

      <div class="recom-card-box">
        <div class="recom-title">Forma Recent ${match.away_team?.name}</div>
        <div style="margin: 10px 0;">
          ${(aSt.form_5 || []).map(f => `<span class="form-badge-pill form-badge-${f.toLowerCase()}">${f}</span>`).join('') || '<span style="color: var(--text-muted);">N/D</span>'}
        </div>
        <div style="font-size: 12.5px; color: var(--text-secondary); line-height: 1.5;">
          ${aSt.available ? `Balanç de punts recents i estabilitat competitiva segons el model Elo (${match.away_team?.elo}).` : 'Dades de forma no disponibles.'}
        </div>
      </div>
    </div>
  `;
}

function renderMarketsTab(dt) {
  const container = document.getElementById('md-pane-markets');
  if (!container) return;

  const markets = dt.markets_analysis || [];
  if (markets.length === 0) {
    container.innerHTML = `<p style="text-align: center; color: var(--text-muted); padding: 20px;">No hi ha dades de mercats disponibles per a aquest partit.</p>`;
    return;
  }

  container.innerHTML = `
    <div class="md-table-wrap">
      <table class="md-table">
        <thead>
          <tr>
            <th>Mercat & Selecció</th>
            <th>Prob. Model</th>
            <th>Quota Justa</th>
            <th>Winamax</th>
            <th>Bet365</th>
            <th>Millor EV</th>
          </tr>
        </thead>
        <tbody>
          ${markets.map(m => {
    const evColor = (m.best_ev && m.best_ev > 0) ? 'color: var(--accent-emerald); font-weight: 700;' : 'color: var(--text-muted);';
    const fBadgeW = m.freshness_winamax === 'FRESH' ? 'fresh' : (m.freshness_winamax === 'STALE' ? 'stale' : 'unknown');
    const fBadgeB = m.freshness_bet365 === 'FRESH' ? 'fresh' : (m.freshness_bet365 === 'STALE' ? 'stale' : 'unknown');

    return `
              <tr>
                <td>
                  <strong>${m.selection}</strong>
                  <div style="font-size: 10.5px; color: var(--text-muted);">${m.category}</div>
                </td>
                <td style="font-family: var(--font-mono);">${m.prob}%</td>
                <td style="font-family: var(--font-mono); color: var(--accent-cyan);">${m.fair_odd ? `@${m.fair_odd}` : '-'}</td>
                <td>
                  ${m.odd_winamax ? `
                    <span style="font-family: var(--font-mono); font-weight: 700;">@${m.odd_winamax}</span>
                    <span class="freshness-badge ${fBadgeW}">${m.freshness_winamax}</span>
                  ` : '<span style="color: var(--text-muted); font-size: 11px;">N/D</span>'}
                </td>
                <td>
                  ${m.odd_bet365 ? `
                    <span style="font-family: var(--font-mono); font-weight: 700;">@${m.odd_bet365}</span>
                    <span class="freshness-badge ${fBadgeB}">${m.freshness_bet365}</span>
                  ` : '<span style="color: var(--text-muted); font-size: 11px;">N/D</span>'}
                </td>
                <td style="${evColor}; font-family: var(--font-mono);">
                  ${m.best_ev != null ? `${m.best_ev >= 0 ? '+' : ''}${m.best_ev}%` : '-'}
                  ${m.best_bookie ? `<span style="font-size: 10px; color: var(--text-muted);"> (${m.best_bookie})</span>` : ''}
                </td>
              </tr>
            `;
  }).join('')}
        </tbody>
      </table>
    </div>
  `;
}

// Utilitats matemàtiques universals per a mercats amb i sense devolució (push)
function calculateEV(pWin, pPush, odd) {
  if (!odd || odd <= 1.0 || pWin <= 0) return null;
  const pW = pWin / 100.0;
  const pV = (pPush || 0.0) / 100.0;
  return parseFloat(((pW * odd + pV - 1.0) * 100.0).toFixed(2));
}

function calculateFairOdd(pWin, pPush) {
  if (pWin <= 0) return null;
  const pW = pWin / 100.0;
  const pV = (pPush || 0.0) / 100.0;
  return parseFloat(((1.0 - pV) / pW).toFixed(2));
}

function renderLinesTab(dt) {
  const container = document.getElementById('md-pane-lines');
  if (!container) return;

  const linesData = dt.interactive_lines || {};
  const currentLines = linesData[currentLineCategory] || [];

  if (currentLines.length === 0) {
    container.innerHTML = `<p style="text-align: center; color: var(--text-muted); padding: 20px;">No hi ha dades de línies interactives disponibles.</p>`;
    return;
  }

  // Comprovar si el threshold actual està dins dels disponibles
  const thresholds = currentLines.map(l => l.threshold);
  if (!thresholds.includes(currentLineThreshold)) {
    currentLineThreshold = thresholds[Math.floor(thresholds.length / 2)] || thresholds[0];
  }

  const selectedLine = currentLines.find(l => l.threshold === currentLineThreshold) || currentLines[0];
  const isPushLine = selectedLine.prob_push && selectedLine.prob_push > 0;

  container.innerHTML = `
    <div class="line-selector-container">
      <div class="line-category-nav">
        <button class="btn-line-cat ${currentLineCategory === 'corners' ? 'active' : ''}" onclick="onLineCategoryChange('corners')">🚩 Córners</button>
        <button class="btn-line-cat ${currentLineCategory === 'goals' ? 'active' : ''}" onclick="onLineCategoryChange('goals')">⚽ Gols</button>
        <button class="btn-line-cat ${currentLineCategory === 'cards' ? 'active' : ''}" onclick="onLineCategoryChange('cards')">🟨 Targetes</button>
      </div>

      <div class="slider-controls-row">
        <div class="slider-value-display">
          <span style="font-size: 13px; font-weight: 600; color: var(--text-secondary);">Línia seleccionada:</span>
          <span class="slider-threshold-badge">${selectedLine.threshold}</span>
          ${selectedLine.is_integer ? '<span style="font-size: 11px; background: rgba(59, 130, 246, 0.15); color: #60a5fa; padding: 2px 8px; border-radius: 4px; font-weight: 600;">Línia Asiàtica Entera (amb Devolució/Push)</span>' : '<span style="font-size: 11px; background: rgba(16, 185, 129, 0.15); color: #34d399; padding: 2px 8px; border-radius: 4px; font-weight: 600;">Línia Estàndard Binària (Sense Push)</span>'}
        </div>
        <input type="range" class="interactive-slider"
               min="0" max="${thresholds.length - 1}" step="1"
               value="${thresholds.indexOf(selectedLine.threshold)}"
               oninput="onLineSliderChange(this.value)">
        <div style="display: flex; justify-content: space-between; font-size: 11px; color: var(--text-muted); font-family: var(--font-mono);">
          <span>${thresholds[0]}</span>
          <span>${thresholds[thresholds.length - 1]}</span>
        </div>
      </div>

      <div class="line-cards-grid">
        <!-- Targeta OVER -->
        <div class="line-outcome-card">
          <div class="line-outcome-title" style="color: var(--accent-emerald);">Més de ${selectedLine.threshold} (Over)</div>
          <div class="line-stat-line"><span class="lbl">Probabilitat Guany:</span><span class="val">${selectedLine.prob_over}%</span></div>
          ${isPushLine ? `<div class="line-stat-line"><span class="lbl">Prob. Devolució (Push):</span><span class="val" style="color: #f59e0b; font-weight: 700;">${selectedLine.prob_push}%</span></div>` : ''}
          <div class="line-stat-line"><span class="lbl">Quota Justa (amb Push):</span><span class="val">@${selectedLine.fair_odd_over || '-'}</span></div>
          <div class="line-stat-line"><span class="lbl">Quota Winamax:</span><span class="val">${selectedLine.winamax_over ? `@${selectedLine.winamax_over}` : '<span style="color: var(--text-muted);">No disponible</span>'}</span></div>
          <div class="line-stat-line"><span class="lbl">Quota Bet365:</span><span class="val">${selectedLine.bet365_over ? `@${selectedLine.bet365_over}` : '<span style="color: var(--text-muted);">No disponible</span>'}</span></div>
          <div class="line-stat-line"><span class="lbl">Valor Winamax:</span><span class="val" style="${selectedLine.ev_winamax_over && selectedLine.ev_winamax_over > 0 ? 'color: var(--accent-emerald); font-weight: 700;' : ''}">${selectedLine.ev_winamax_over != null ? `${selectedLine.ev_winamax_over >= 0 ? '+' : ''}${selectedLine.ev_winamax_over}%` : '<span style="color: var(--text-muted);">No disponible</span>'}</span></div>
          <div class="line-stat-line"><span class="lbl">Valor Bet365:</span><span class="val" style="${selectedLine.ev_bet365_over && selectedLine.ev_bet365_over > 0 ? 'color: var(--accent-emerald); font-weight: 700;' : ''}">${selectedLine.ev_bet365_over != null ? `${selectedLine.ev_bet365_over >= 0 ? '+' : ''}${selectedLine.ev_bet365_over}%` : '<span style="color: var(--text-muted);">No disponible</span>'}</span></div>
        </div>

        <!-- Targeta UNDER -->
        <div class="line-outcome-card">
          <div class="line-outcome-title" style="color: var(--accent-cyan);">Menys de ${selectedLine.threshold} (Under)</div>
          <div class="line-stat-line"><span class="lbl">Probabilitat Guany:</span><span class="val">${selectedLine.prob_under}%</span></div>
          ${isPushLine ? `<div class="line-stat-line"><span class="lbl">Prob. Devolució (Push):</span><span class="val" style="color: #f59e0b; font-weight: 700;">${selectedLine.prob_push}%</span></div>` : ''}
          <div class="line-stat-line"><span class="lbl">Quota Justa (amb Push):</span><span class="val">@${selectedLine.fair_odd_under || '-'}</span></div>
          <div class="line-stat-line"><span class="lbl">Quota Winamax:</span><span class="val">${selectedLine.winamax_under ? `@${selectedLine.winamax_under}` : '<span style="color: var(--text-muted);">No disponible</span>'}</span></div>
          <div class="line-stat-line"><span class="lbl">Quota Bet365:</span><span class="val">${selectedLine.bet365_under ? `@${selectedLine.bet365_under}` : '<span style="color: var(--text-muted);">No disponible</span>'}</span></div>
          <div class="line-stat-line"><span class="lbl">Valor Winamax:</span><span class="val" style="${selectedLine.ev_winamax_under && selectedLine.ev_winamax_under > 0 ? 'color: var(--accent-emerald); font-weight: 700;' : ''}">${selectedLine.ev_winamax_under != null ? `${selectedLine.ev_winamax_under >= 0 ? '+' : ''}${selectedLine.ev_winamax_under}%` : '<span style="color: var(--text-muted);">No disponible</span>'}</span></div>
          <div class="line-stat-line"><span class="lbl">Valor Bet365:</span><span class="val" style="${selectedLine.ev_bet365_under && selectedLine.ev_bet365_under > 0 ? 'color: var(--accent-emerald); font-weight: 700;' : ''}">${selectedLine.ev_bet365_under != null ? `${selectedLine.ev_bet365_under >= 0 ? '+' : ''}${selectedLine.ev_bet365_under}%` : '<span style="color: var(--text-muted);">No disponible</span>'}</span></div>
        </div>
      </div>
    </div>
  `;
}

function onLineCategoryChange(category) {
  currentLineCategory = category;
  const linesData = currentDetailsMatch?.match_details?.interactive_lines || {};
  const lines = linesData[category] || [];
  if (lines.length > 0) {
    currentLineThreshold = lines[Math.floor(lines.length / 2)].threshold;
  }
  renderLinesTab(currentDetailsMatch?.match_details || {});
}

function onLineSliderChange(idx) {
  const linesData = currentDetailsMatch?.match_details?.interactive_lines || {};
  const lines = linesData[currentLineCategory] || [];
  const selected = lines[parseInt(idx, 10)];
  if (selected) {
    currentLineThreshold = selected.threshold;
    renderLinesTab(currentDetailsMatch?.match_details || {});
  }
}

function renderRecommendationsTab(dt) {
  const container = document.getElementById('md-pane-recommendations');
  if (!container) return;

  const rec = dt.recommendations || {};
  const h1x2 = rec.highest_1x2;
  const hStd = rec.highest_probability_standard || rec.highest_probability;
  const bv = rec.best_value;
  const adv = rec.advised_against || [];

  container.innerHTML = `
    <!-- (A) RESULTAT 1X2 MÉS PROBABLE -->
    <div class="recom-card-box highlight">
      <div class="recom-title" style="color: var(--accent-cyan);">
        🎯 Resultat 1X2 Més Probable: ${h1x2 ? h1x2.selection : 'N/D'}
      </div>
      <div style="font-size: 13.5px; margin: 8px 0;">
        Probabilitat estimada pel model: <strong>${h1x2 ? h1x2.probability : '-'}%</strong>
        ${h1x2 && h1x2.fair_odd ? `· Quota Justa: <strong>@${h1x2.fair_odd}</strong>` : ''}
        ${h1x2 && h1x2.best_odd ? `· Quota Mercat: <strong>@${h1x2.best_odd}</strong> (${h1x2.best_bookie})` : ''}
        ${h1x2 && h1x2.ev != null ? `· Edge: <strong style="${h1x2.ev >= 0 ? 'color: var(--accent-emerald);' : 'color: var(--accent-rose);'}">${h1x2.ev >= 0 ? '+' : ''}${h1x2.ev}%</strong>` : ''}
      </div>
      <div class="recom-desc">
        ${h1x2 ? h1x2.disclaimer : 'No disponible.'}
      </div>
    </div>

    <!-- (B) SELECCIÓ MÉS PROBABLE ENTRE MERCATS ESTÀNDARD -->
    <div class="recom-card-box highlight">
      <div class="recom-title" style="color: var(--accent-cyan);">
        ⭐ Màxima Probabilitat (Mercats Estàndard): ${hStd ? hStd.selection : 'N/D'}
      </div>
      <div style="font-size: 13.5px; margin: 8px 0;">
        Probabilitat estimada: <strong>${hStd ? hStd.probability : '-'}%</strong>
        ${hStd && hStd.fair_odd ? `· Quota Justa: <strong>@${hStd.fair_odd}</strong>` : ''}
        ${hStd && hStd.best_odd ? `· Quota Mercat: <strong>@${hStd.best_odd}</strong> (${hStd.best_bookie})` : ''}
        ${hStd && hStd.ev != null ? `· Edge: <strong style="${hStd.ev >= 0 ? 'color: var(--accent-emerald);' : 'color: var(--accent-rose);'}">${hStd.ev >= 0 ? '+' : ''}${hStd.ev}%</strong>` : ''}
      </div>
      <div class="recom-desc">
        ${hStd ? hStd.disclaimer : 'No disponible.'}
      </div>
    </div>

    <!-- (C) MILLOR VALOR (+EV%) -->
    <div class="recom-card-box">
      <div class="recom-title" style="color: var(--accent-emerald);">
        💎 Millor Aposta amb Valor (+EV)
      </div>
      ${bv ? `
        <div style="font-size: 13.5px; margin: 8px 0;">
          Selecció: <strong>${bv.selection}</strong> (@${bv.odd} a ${bv.bookmaker}) ·
          Edge: <strong style="color: var(--accent-emerald);">+${bv.ev_pct}%</strong>
        </div>
        <div class="recom-desc">
          ${bv.disclaimer} Recomanem gestionar el risc mitjançant criteri Kelly fraccionat (aposta suggerida: <strong>${bv.recommended_stake_eur} €</strong>).
        </div>
      ` : `
        <div class="recom-desc" style="color: var(--text-muted);">
          Cap selecció d'aquest partit presenta valor matemàtic positiu (+EV) confirmat amb quotes fresques en aquest moment. Recomanem prudència i no forçar entrades sense edge estadístic.
        </div>
      `}
    </div>

    <!-- (D) MERCATS DESACONSELLATS -->
    <div class="recom-card-box warning">
      <div class="recom-title" style="color: var(--accent-rose);">
        ⚠️ Mercats Desaconsellats pel Model
      </div>
      ${adv.length > 0 ? `
        <div style="display: flex; flex-direction: column; gap: 8px; margin-top: 8px;">
          ${adv.map(a => {
    const isNegativeEv = a.reason_type === 'NEGATIVE_EV';
    const isHighVar = a.reason_type === 'HIGH_VARIANCE';
    const badgeColor = isNegativeEv ? '#ef4444' : (isHighVar ? '#f59e0b' : '#94a3b8');
    const badgeLabel = isNegativeEv ? 'EDGE NEGATIU' : (isHighVar ? 'ALTA VARIÀNCIA' : 'NO AVALUABLE · CADUCAT');

    return `
              <div style="font-size: 12.5px; border-left: 2px solid ${badgeColor}; padding-left: 8px;">
                <span style="font-size: 10px; background: rgba(255,255,255,0.06); color: ${badgeColor}; padding: 1px 5px; border-radius: 3px; font-weight: 700; margin-right: 5px;">${badgeLabel}</span>
                <strong>${a.market}</strong> (${a.bookmaker}):
                <span style="color: var(--text-secondary);">${a.reason}</span>
              </div>
            `;
  }).join('')}
        </div>
      ` : `
        <div class="recom-desc" style="color: var(--text-muted);">
          No s'han identificat anomalies extremes ni edges negatius crítics per a aquest partit.
        </div>
      `}
    </div>
  `;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    roundCommercial,
    normalizeBankroll,
    getOperatorInfo,
    calculateScenarioPnL,
    optimizeArbitrageStakes,
    computeArbitrageValues,
    formatArbitrageInstructions,
    formatRelativeTime,
    escapeHtml,
    renderPipelineStatus,
    getAppData: () => appData,
    setAppData: (d) => { appData = d; },
    loadLiveSurebets
  };
}


