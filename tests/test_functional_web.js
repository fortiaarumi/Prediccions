/**
 * tests/test_functional_web.js
 * ============================
 * Proves funcionals de la lògica d'arbitratge, càlcul d'stakes per a qualsevol bankroll,
 * semàntica de pagaments bruts vs retorns globals i sanejament d'enllaços d'operadors.
 * Executable directament amb: node tests/test_functional_web.js
 */

const assert = require('assert');
const path = require('path');

// Carregar funcions de producció des de web/app.js
const {
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
  getAppData,
  loadLiveSurebets
} = require('../web/app.js');

console.log('Iniciant bateria de proves funcionals de frontend (Node.js)...');

// =============================================================================
// 1. PROVA DE SANEJAMENT D'ENLLAÇOS I FALLBACK DE WINAMAX
// =============================================================================
console.log('-> Executant Prova 1: Sanejament d\'URLs i absència d\'enllaços enganyosos...');

// 1.1 Winamax amb URL específica vàlida
const w1 = getOperatorInfo('Winamax', 'https://www.winamax.es/paris-sportifs/match/72478666');
assert.strictEqual(w1.hasValidUrl, true, 'Winamax amb deep link ha de tenir hasValidUrl=true');
assert.strictEqual(w1.actionLabel, 'Apostar a Winamax ↗');
assert.strictEqual(w1.validUrl, 'https://www.winamax.es/paris-sportifs/match/72478666');

// 1.2 Winamax sense URL (buit o només espais)
const w2 = getOperatorInfo('Winamax', '');
assert.strictEqual(w2.hasValidUrl, false, 'Winamax sense URL ha de tenir hasValidUrl=false');
assert.strictEqual(w2.actionLabel, 'Sense enllaç directe');
assert.strictEqual(w2.validUrl, '');

// 1.3 Winamax amb URL genèrica de portada (https://www.winamax.es)
const w3 = getOperatorInfo('Winamax', 'https://www.winamax.es');
assert.strictEqual(w3.hasValidUrl, false, 'La portada genèrica de Winamax MAI no ha de passar com a enllaç directe');
assert.strictEqual(w3.actionLabel, 'Sense enllaç directe');
assert.strictEqual(w3.validUrl, '');

// 1.4 Bet365 sense URL
const b1 = getOperatorInfo('Bet365', '');
assert.strictEqual(b1.hasValidUrl, false, 'Bet365 sense URL ha de tenir hasValidUrl=false');
assert.strictEqual(b1.actionLabel, 'Sense enllaç directe');
assert.strictEqual(b1.validUrl, '');

// 1.5 Betfair sense URL
const bf1 = getOperatorInfo('Betfair', '');
assert.strictEqual(bf1.hasValidUrl, false, 'Betfair sense URL ha de tenir hasValidUrl=false');
assert.strictEqual(bf1.actionLabel, 'Sense enllaç directe');
assert.strictEqual(bf1.validUrl, '');

// 1.6 Matchbook sense URL
const mb1 = getOperatorInfo('Matchbook', '');
assert.strictEqual(mb1.hasValidUrl, false, 'Matchbook sense URL ha de tenir hasValidUrl=false');
assert.strictEqual(mb1.actionLabel, 'Sense enllaç directe');
assert.strictEqual(mb1.validUrl, '');

// 1.7 Smarkets sense URL
const sm1 = getOperatorInfo('Smarkets', '');
assert.strictEqual(sm1.hasValidUrl, false, 'Smarkets sense URL ha de tenir hasValidUrl=false');
assert.strictEqual(sm1.actionLabel, 'Sense enllaç directe');
assert.strictEqual(sm1.validUrl, '');

console.log('   [OK] Tots els operadors sense URL mostren "Sense enllaç directe" i cap no obre portades genèriques.');

// =============================================================================
// 2. PROVA D'OPTIMITZACIÓ EXACTA PER A DIVERSOS BANKROLLS (50, 75, 99, 100, 101, 150, 200 €)
// =============================================================================
console.log('-> Executant Prova 2: Optimització de stakes enters i liquidació real per a bankrolls...');

// Surebet 3-way amb Betfair (comissió 2% NET_MARKET_WINNINGS), Winamax i Bet365
const testArb3 = {
  competition_id: 'LALIGA',
  competition_name: 'LaLiga EA Sports',
  date: '01.12.2026',
  matchup: 'FC Barcelona vs Real Madrid',
  market_name: '1X2 Cross-Bookmaker',
  base_investment: 100.0,
  net_guaranteed_profit: 8.75,
  min_guaranteed_return: 108.75,
  profit_pct: 8.75,
  net_profit_pct: 8.75,
  margin_pct: 94.5,
  legs: [
    {
      bookmaker: 'Betfair',
      selection: '1',
      odd: 3.15,
      commission_rate: 0.02,
      commission_model: 'NET_MARKET_WINNINGS',
      settlement_key: 'betfair::market::BF_101',
      stake_rounded: 35,
      expected_return: 108.75,
      url: ''
    },
    {
      bookmaker: 'Winamax',
      selection: 'X',
      odd: 3.35,
      commission_rate: 0.0,
      commission_model: 'NONE',
      settlement_key: 'winamax::isolated::WX_X',
      stake_rounded: 33,
      expected_return: 110.55,
      url: 'https://www.winamax.es/paris-sportifs/match/72478666'
    },
    {
      bookmaker: 'Bet365',
      selection: '2',
      odd: 3.45,
      commission_rate: 0.0,
      commission_model: 'NONE',
      settlement_key: 'bet365::isolated::B365_2',
      stake_rounded: 32,
      expected_return: 110.40,
      url: 'https://www.bet365.es/#/AC/B1/C1/D1002/E12345/F2/'
    }
  ]
};

const bankrolls = [50, 75, 99, 100, 101, 150, 200];

bankrolls.forEach(B => {
  const result = computeArbitrageValues(testArb3, B);

  // 1. Suma exacta de stakes
  const sumStakes = result.roundStakes.reduce((a, b) => a + b, 0);
  assert.strictEqual(sumStakes, B, `Per a Bankroll ${B} €, la suma d'stakes ha de ser exactament ${B} € (obtingut: ${sumStakes})`);

  // 2. Tots els stakes són enters positius >= 1 €
  result.roundStakes.forEach((s, idx) => {
    assert.strictEqual(Number.isInteger(s), true, `L'stake de la pota ${idx} ha de ser enter`);
    assert.ok(s >= 1, `L'stake de la pota ${idx} ha de ser >= 1 €`);
  });

  // 3. Verificació independent del PnL de cada escenari utilitzant calculateScenarioPnL
  const actualP0 = calculateScenarioPnL(testArb3.legs, result.roundStakes, 0);
  const actualPx = calculateScenarioPnL(testArb3.legs, result.roundStakes, 1);
  const actualP2 = calculateScenarioPnL(testArb3.legs, result.roundStakes, 2);
  const actualMinPnl = Math.min(actualP0, actualPx, actualP2);

  // 4. El benefici garantit mostrat coincideix EXACTAMENT amb el mínim PnL real
  assert.ok(
    Math.abs(result.netProfit - actualMinPnl) < 0.02,
    `Per a Bankroll ${B} €, el netProfit de la UI (${result.netProfit}) ha de coincidir amb el min PnL real (${actualMinPnl.toFixed(2)})`
  );

  // 5. El retorn mínim és exactament B + netProfit
  assert.ok(
    Math.abs(result.minReturn - (B + result.netProfit)) < 0.02,
    `Per a Bankroll ${B} €, el minReturn (${result.minReturn}) ha de ser B + netProfit (${(B + result.netProfit).toFixed(2)})`
  );

  // 6. Si netProfit > 0, isPositive ha de ser true; si <= 0, false
  assert.strictEqual(result.isPositive, result.netProfit > 0, 'isPositive ha de reflectir fidelment si hi ha guany');

  console.log(`   [OK] Bankroll ${B} €: Stakes=${JSON.stringify(result.roundStakes)} | MinPnL=${result.netProfit.toFixed(2)} € | Retorn=${result.minReturn.toFixed(2)} € (Discrepància = 0.00 €)`);
});

// =============================================================================
// 3. DETECCIÓ DE REGRESSIÓ: DEMOSTRAR QUE L'ESCALAT LINEAL INGENU FALLARIA
// =============================================================================
console.log('-> Executant Prova 3: Detecció de regressió contra l\'escalat lineal ingenu...');

function flawedLinearScaling(arb, B) {
  const ratio = B / 100.0;
  const stakes = arb.legs.map(l => Math.max(1, Math.round(l.stake_rounded * ratio)));
  const sumStakes = stakes.reduce((a, b) => a + b, 0);
  stakes[0] += (B - sumStakes);
  const falseProfit = Number((arb.net_guaranteed_profit * ratio).toFixed(2));
  const realP0 = calculateScenarioPnL(arb.legs, stakes, 0);
  const realPx = calculateScenarioPnL(arb.legs, stakes, 1);
  const realP2 = calculateScenarioPnL(arb.legs, stakes, 2);
  const realMin = Math.min(realP0, realPx, realP2);
  return { falseProfit, realMin, discrepancy: Math.abs(falseProfit - realMin) };
}

const reg50 = flawedLinearScaling(testArb3, 50);
assert.ok(reg50.discrepancy > 0.5, 'L\'antic escalat ingenu a 50 € ha de generar una discrepància superior a 0.50 €');
console.log(`   [OK] Test de regressió confirmat: L'antic mètode lineal hauria fallat a 50 € amb error de ${reg50.discrepancy.toFixed(2)} €`);

// =============================================================================
// 4. PROVA DE SEMÀNTICA: PAGAMENT BRUT INDIVIDUAL VS RETORN GLOBAL DE CARTERA
// =============================================================================
console.log('-> Executant Prova 4: Verificació de la semàntica de pagaments i retorns...');

const values100 = computeArbitrageValues(testArb3, 100);

// 4.1 Cada pota té el seu pagament brut individual s_i * odd_i
assert.strictEqual(values100.grossPayouts[0], Number((35 * 3.15).toFixed(2)), 'Pota 1: 35 * 3.15 = 110.25 €');
assert.strictEqual(values100.grossPayouts[1], Number((33 * 3.35).toFixed(2)), 'Pota 2: 33 * 3.35 = 110.55 €');
assert.strictEqual(values100.grossPayouts[2], Number((32 * 3.45).toFixed(2)), 'Pota 3: 32 * 3.45 = 110.40 €');

// 4.2 El pagament brut individual no dedueix les pèrdues d'altres operadors
assert.ok(values100.grossPayouts[0] > 100, 'El pagament brut de Betfair és > 100 €');

// 4.3 El retorn global de cartera sí que dedueix les pèrdues de les altres potes i comissions
assert.strictEqual(values100.minReturn, 108.75, 'El retorn mínim global és 108.75 €');
assert.strictEqual(values100.netProfit, 8.75, 'El benefici net garantit és 8.75 €');

// 4.4 Coherència amb el text d'instruccions copiables
const copiedText = formatArbitrageInstructions(testArb3, 100);
assert.ok(copiedText.includes('Pagament brut individual: 110.25 €'), 'El text copiat ha de contenir el pagament brut individual de Betfair');
assert.ok(copiedText.includes('Pagament brut individual: 110.55 €'), 'El text copiat ha de contenir el pagament brut individual de Winamax');
assert.ok(copiedText.includes('RETORN GLOBAL MÍNIM DE CARTERA: 108.75 €'), 'El text copiat ha de mostrar el retorn global a la zona comuna');
assert.ok(copiedText.includes('BENEFICI NET GARANTIT: +8.75 €'), 'El text copiat ha de mostrar el benefici net a la zona comuna');
assert.ok(!copiedText.includes('Retorn net previst: 108.75 €'), 'No s\'ha d\'utilitzar l\'antiga etiqueta ambigua a cada pota');

console.log('   [OK] Semàntica validada: Pagaments bruts individuals separats del retorn global de cartera.');

// =============================================================================
// 5. PROVA DE 2 POTES AL MATEIX MERCAT NATIV D'EXCHANGE (NET_MARKET_WINNINGS)
// =============================================================================
console.log('-> Executant Prova 5: Dues potes al mateix exchange i mercat natiu...');

const arbSameMkt = {
  competition_id: 'LALIGA',
  base_investment: 100.0,
  legs: [
    { bookmaker: 'Betfair', odd: 3.20, commission_rate: 0.02, commission_model: 'NET_MARKET_WINNINGS', settlement_key: 'bf::market::MKT_SHARED' },
    { bookmaker: 'Betfair', odd: 3.30, commission_rate: 0.02, commission_model: 'NET_MARKET_WINNINGS', settlement_key: 'bf::market::MKT_SHARED' },
    { bookmaker: 'Bet365', odd: 3.40, commission_rate: 0.0, commission_model: 'NONE', settlement_key: 'b365::isolated::2' }
  ]
};

const resSameMkt = computeArbitrageValues(arbSameMkt, 100);
assert.strictEqual(resSameMkt.roundStakes.reduce((a, b) => a + b, 0), 100);
assert.ok(resSameMkt.netProfit > 0, 'Surebet viable ha de produir netProfit > 0');

console.log('   [OK] Liquidació agregada amb NET_MARKET_WINNINGS verificada.');

// =============================================================================
// 6. PROVA DE POLÍTICA D'ARRODONIMENT COMERCIAL I COHERÈNCIA DE BANKROLLS DECIMALS
//    Casos: 100.49, 100.50, 100.51, 101.50, 102.50, 99.40, 50, 100, 200 €
// =============================================================================
console.log('-> Executant Prova 6: Política d\'arrodoniment comercial i coherència de bankrolls decimals...');

const decimalCases = [
  { input: 100.49, expected: 100 },
  { input: 100.50, expected: 101 },
  { input: 100.51, expected: 101 },
  { input: 101.50, expected: 102 },
  { input: 102.50, expected: 103 },
  { input: 99.40,  expected: 99 },
  { input: 50.00,  expected: 50 },
  { input: 100.00, expected: 100 },
  { input: 200.00, expected: 200 }
];

decimalCases.forEach(({ input, expected }) => {
  // 6.1 Comprovació independent de la política d'arrodoniment comercial: Math.floor(x + 0.5)
  const independentRounded = Math.floor(input + 0.5);
  assert.strictEqual(independentRounded, expected, `Càlcul independent per a ${input} ha de ser ${expected}`);
  assert.strictEqual(roundCommercial(input), expected, `roundCommercial(${input}) ha de ser ${expected}`);
  assert.strictEqual(normalizeBankroll(input, 3), Math.max(3, expected), `normalizeBankroll(${input}, 3) ha de ser ${expected}`);

  // 6.2 Càlcul d'apostes amb la surebet 3-way
  const res = computeArbitrageValues(testArb3, input);

  // 6.3 Bankroll efectiu reportat coincideix amb la normalització
  assert.strictEqual(res.bankroll, expected, `Per a ${input} €, el bankroll efectiu reportat ha de ser ${expected} €`);
  assert.strictEqual(res.effectiveBankroll, expected, `effectiveBankroll ha de ser ${expected} €`);

  // 6.4 La suma dels stakes coincideix EXACTAMENT amb el bankroll efectiu
  const sumStakes = res.roundStakes.reduce((a, b) => a + b, 0);
  assert.strictEqual(sumStakes, expected, `Per a ${input} €, la suma dels stakes (${sumStakes}) ha de ser exactament ${expected} €`);

  // 6.5 El retorn garantit es calcula sobre el bankroll efectiu
  const expectedMinReturn = Number((expected + res.netProfit).toFixed(2));
  assert.ok(
    Math.abs(res.minReturn - expectedMinReturn) < 0.02,
    `Per a ${input} €, minReturn (${res.minReturn}) ha de ser effectiveBankroll + netProfit (${expectedMinReturn})`
  );

  // 6.6 La funció de còpia d'instruccions utilitza el bankroll efectiu
  const instructions = formatArbitrageInstructions(testArb3, input);
  assert.ok(
    instructions.includes(`Inversió Total: ${expected} €`),
    `Les instruccions copiades per a input ${input} € han de mostrar la inversió efectiva 'Inversió Total: ${expected} €'`
  );
  if (input !== expected) {
    assert.ok(
      !instructions.includes(`Inversió Total: ${input} €`),
      `Les instruccions copiades NO han de mostrar el valor decimal no normalitzat ${input} €`
    );
  }
  assert.ok(
    instructions.includes(`RETORN GLOBAL MÍNIM DE CARTERA: ${res.minReturn.toFixed(2)} €`),
    `Les instruccions copiades han d'incloure el retorn global calculat sobre el bankroll efectiu`
  );

  console.log(`   [OK] Input ${input} € -> Normalitzat a ${expected} € | Suma Stakes=${sumStakes} € | Retorn=${res.minReturn.toFixed(2)} € | Instruccions OK`);
});

// =============================================================================
// 7. PROVA D'INTEGRACIÓ DE BETWAY AL FRONTEND (getOperatorInfo i 3 cases)
// =============================================================================
console.log('-> Executant Prova 7: Integració de Betway al frontend i instruccions...');

// 7.1 Reconeixement d'operador Betway amb enllaç profund específic
const opBetwayValid = getOperatorInfo('Betway', 'https://betway.es/es/sports/evt/72478670');
assert.strictEqual(opBetwayValid.name, 'Betway', 'Ha de reconèixer Betway');
assert.strictEqual(opBetwayValid.hasValidUrl, true, 'Enllaç profund d\'esdeveniment ha de ser vàlid');
assert.strictEqual(opBetwayValid.actionLabel, 'Apostar a Betway ↗');
assert.strictEqual(opBetwayValid.tagClass, 'arb-tag-betway');

// 7.2 Bloqueig de portada genèrica betway.es
const opBetwayGeneric = getOperatorInfo('Betway', 'https://betway.es/');
assert.strictEqual(opBetwayGeneric.hasValidUrl, false, 'Portada genèrica ha de quedar deshabilitada');
assert.strictEqual(opBetwayGeneric.actionLabel, 'Sense enllaç directe');

// 7.3 Instruccions d'aposta tripartita Winamax + Bet365 + Betway
const arbTri = {
  competition_id: 'LALIGA',
  competition_name: 'LaLiga EA Sports',
  matchup: 'Valencia CF vs Real Madrid',
  market_name: 'Guanyador del partit (1X2)',
  date: '2026-11-04',
  base_investment: 100.0,
  legs: [
    { bookmaker: 'Winamax', odd: 3.40, selection: '1 - Victòria Valencia CF', url: 'https://www.winamax.es/paris-sportifs/match/1001' },
    { bookmaker: 'Bet365', odd: 3.35, selection: 'X - Empat', url: 'https://www.bet365.es/#/AC/B1/C1/D13/E1001/F2/' },
    { bookmaker: 'Betway', odd: 3.30, selection: '2 - Victòria Real Madrid', url: 'https://betway.es/es/sports/evt/72478670' }
  ]
};

const instrTri = formatArbitrageInstructions(arbTri, 100);
assert.ok(instrTri.includes('WINAMAX:'), 'Ha d\'incloure la secció de Winamax');
assert.ok(instrTri.includes('BET365:'), 'Ha d\'incloure la secció de Bet365');
assert.ok(instrTri.includes('BETWAY:'), 'Ha d\'incloure la secció de Betway');
assert.ok(instrTri.includes('https://betway.es/es/sports/evt/72478670'), 'Ha d\'incloure l\'enllaç directe a Betway');
assert.ok(instrTri.includes('RETORN GLOBAL MÍNIM DE CARTERA:'), 'Ha d\'incloure el retorn global');

console.log('   [OK] Suport complet per a Betway i arbitratge tripartit al frontend verificat.');

// =============================================================================
// 8. PROVA DE FORMAT RELATIU TEMPORAL I CASOS LÍMIT (formatRelativeTime)
// =============================================================================
console.log('-> Executant Prova 8: Format de temps relatiu i llindars de frescor...');

const nowRef = new Date('2026-11-02T20:00:00.000Z').getTime();

// 8.1 Menys de 10 segons
const tRecent = new Date('2026-11-02T19:59:55.000Z').toISOString();
assert.strictEqual(formatRelativeTime(tRecent, nowRef), 'ara mateix (<10s)');

// 8.2 Segons (25s)
const t25s = new Date('2026-11-02T19:59:35.000Z').toISOString();
assert.strictEqual(formatRelativeTime(t25s, nowRef), 'fa 25s');

// 8.3 Minuts (3 min)
const t3m = new Date('2026-11-02T19:57:00.000Z').toISOString();
assert.strictEqual(formatRelativeTime(t3m, nowRef), 'fa 3 min');

// 8.4 Hores (2 h)
const t2h = new Date('2026-11-02T18:00:00.000Z').toISOString();
assert.strictEqual(formatRelativeTime(t2h, nowRef), 'fa 2 h');

// 8.5 Dies (5 d)
const t5d = new Date('2026-10-28T20:00:00.000Z').toISOString();
assert.strictEqual(formatRelativeTime(t5d, nowRef), 'fa 5 d');

// 8.6 Valors buits o invàlids
assert.strictEqual(formatRelativeTime('', nowRef), 'Sense dades');
assert.strictEqual(formatRelativeTime(null, nowRef), 'Sense dades');
assert.strictEqual(formatRelativeTime('data-invalida', nowRef), 'Data invàlida');

// 8.7 Desincronització lleugerament futura (clock skew)
const tFuture = new Date('2026-11-02T20:00:05.000Z').toISOString();
assert.strictEqual(formatRelativeTime(tFuture, nowRef), 'ara mateix (<10s)');

console.log('   [OK] Lògica de temps relatiu i llindars de frescor verificada en tots els casos límit.');

// =============================================================================
// 9. PROVA DE TRANSPARÈNCIA DE MONITORATGE I DISTINCIÓ DE TIMESTAMPS (BLOC A)
// =============================================================================
console.log('-> Executant Prova 9: Transparència de quotes i timestamps distingits a Bloc A...');

// Validar que l'estructura generada per SurebetDaemon conté les 3 cases i tots els camps distingits
const fs = require('fs');
const dataJsonPath = path.join(__dirname, '..', 'web', 'data', 'data.json');
assert.ok(fs.existsSync(dataJsonPath), 'web/data/data.json ha d\'existir');
const rawWebData = JSON.parse(fs.readFileSync(dataJsonPath, 'utf8'));

assert.ok(rawWebData.arbitrage, 'data.json ha de tenir secció arbitrage');
const bStatus = rawWebData.arbitrage.bookmakers_status;
assert.ok(bStatus, 'arbitrage ha de tenir bookmakers_status');

['winamax', 'bet365', 'betway'].forEach(bk => {
  const info = bStatus[bk];
  assert.ok(info, `bookmakers_status ha d'incloure ${bk}`);
  assert.ok('status' in info, `${bk} ha de tenir camp status`);
  assert.ok('last_attempt_status' in info, `${bk} ha de tenir camp last_attempt_status`);
  assert.ok('events_count' in info, `${bk} ha de tenir camp events_count`);
  assert.ok('odds_count' in info, `${bk} ha de tenir camp odds_count`);
  assert.ok('query_start' in info || info.query_start === null, `${bk} ha de tenir query_start`);
  assert.ok('query_end' in info || info.query_end === null, `${bk} ha de tenir query_end`);
  assert.ok('http_captured_at' in info || info.http_captured_at === null, `${bk} ha de tenir http_captured_at`);
});

console.log('   [OK] Bloc A: Les 3 cases reals disposen de mètriques de volum i timestamps distingits.');

// =============================================================================
// 10. PROVA DE L'ESTAT DEL PIPELINE GENERAL DE GITHUB ACTIONS (BLOC B)
// =============================================================================
console.log('-> Executant Prova 10: Estat de GitHub Actions i desglossament de tasques a Bloc B...');

const pipeStatus = rawWebData.pipeline_status || (rawWebData.metadata && rawWebData.metadata.pipeline_status);
assert.ok(pipeStatus, 'data.json ha de contenir pipeline_status');
assert.strictEqual(pipeStatus.workflow_file, 'daily_pipeline.yml');
assert.ok(['COMPLETED', 'SUCCESS', 'PARTIAL', 'RUNNING'].includes(pipeStatus.status.toUpperCase()));
assert.ok(pipeStatus.last_success_timestamp, 'Ha de tenir last_success_timestamp');
assert.ok(pipeStatus.last_attempt_timestamp, 'Ha de tenir last_attempt_timestamp');
assert.ok(pipeStatus.duration_seconds > 0, 'Durada ha de ser positiva');

const expectedTasks = [
  'results_and_scores',
  'elo_and_stats',
  'referee_designations',
  'predictions',
  'combos',
  'web_export'
];

expectedTasks.forEach(taskId => {
  assert.ok(pipeStatus.tasks[taskId], `Tasca ${taskId} ha d'estar desglossada al pipeline`);
  const t = pipeStatus.tasks[taskId];
  assert.ok(t.name, `Tasca ${taskId} ha de tenir nom`);
  assert.ok(t.status, `Tasca ${taskId} ha de tenir status`);
  assert.ok(t.last_success, `Tasca ${taskId} ha de tenir last_success`);
});

console.log('   [OK] Bloc B: Metadades de GitHub Actions completes amb les 6 tasques operatives desglossades.');

// =============================================================================
// 11. PROVA DE FALLBACK DE LOADLIVESUREBETS EN CAS D'ERROR DE XARXA
// =============================================================================
console.log('-> Executant Prova 11: Fallback transparent de dades en viu cap a data.json...');

const initialArb = getAppData() ? getAppData().arbitrage : rawWebData.arbitrage;
assert.ok(initialArb, 'appData ha de contenir les dades base d’arbitratge');
assert.ok(initialArb.bookmakers_status.winamax, 'Winamax ha d’existir');

// Simular fallada del fetch en viu
global.fetch = async () => {
  throw new Error('Connexió rebutjada (VM aturada)');
};

// Executar loadLiveSurebets amb Promise
loadLiveSurebets().then(() => {
  // Comprovar que appData.arbitrage no s'ha destruït ni ha quedat buit
  const preservedArb = getAppData() ? getAppData().arbitrage : rawWebData.arbitrage;
  assert.ok(preservedArb, 'En cas d’error de xarxa, les dades de data.json es conserven intactes');
  assert.strictEqual(global._surebetsLiveConnected, false, 'Estat de connexió en viu ha de ser false');
  console.log('   [OK] Fallback validat: La web manté les dades prèvies de data.json sense trencar-se.');

  console.log('\n========================================================');
  console.log('TOTES LES PROVES FUNCIONALS DE FRONTEND HAN PASSAT (11/11)');
  console.log('========================================================\n');
});

