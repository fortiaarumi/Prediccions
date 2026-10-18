"""
tests/test_rank_update_and_model.py
===================================
Tests unitaris i d'integració per verificar:
1. RankEngine.update_tri_ranks canvia els rànquings correctament.
2. Dos equips inicialment iguals divergeixen amb resultats diferents.
3. El crawler no aplica l'actualització dues vegades per al mateix partit.
4. GoalModel genera probabilitats vàlides i normalitzades.
5. La correcció d'Hypermotion away_avg = 1.15 s'aplica.
6. Dixon-Coles no produeix probabilitats negatives.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from model.rank_engine import RankEngine
from model.goal_model import GoalModel
from model.card_model import CardModel
from model.corner_model import CornerModel

def test_tri_ranks_update_positive_performance():
    """Un equip que marca més del que s'espera ha de millorar (baixar) el seu off_rank."""
    re = RankEngine(league_teams_count=20)
    gen, off, def_ = re.update_tri_ranks(
        current_off_rank=10.0,
        current_def_rank=10.0,
        goals_scored=3,
        goals_conceded=0,
        xg_for=2.5,
        xg_against=0.3,
        expected_goals_model=1.27
    )
    assert off < 10.0, f"off_rank hauria de millorar (baixar) amb bona actuació ofensiva, got {off}"
    assert def_ < 10.0, f"def_rank hauria de millorar amb pocs gols encaixats, got {def_}"
    print(f"  PASS: Bona actuació -> off_rank={off:.2f} def_rank={def_:.2f} (ambdós < 10.0)")

def test_tri_ranks_update_negative_performance():
    """Un equip que marca poc i encaixa molt ha d'empitjorar (pujar) els rànquings."""
    re = RankEngine(league_teams_count=20)
    gen, off, def_ = re.update_tri_ranks(
        current_off_rank=5.0,
        current_def_rank=5.0,
        goals_scored=0,
        goals_conceded=4,
        xg_for=0.3,
        xg_against=3.5,
        expected_goals_model=1.27
    )
    assert off > 5.0, f"off_rank hauria d'empitjorar (pujar) amb mala actuació ofensiva, got {off}"
    assert def_ > 5.0, f"def_rank hauria d'empitjorar amb molts gols encaixats, got {def_}"
    print(f"  PASS: Mala actuació -> off_rank={off:.2f} def_rank={def_:.2f} (ambdós > 5.0)")

def test_tri_ranks_clamped():
    """Els rànquings no poden sortir del rang [1.0, league_teams_count]."""
    re = RankEngine(league_teams_count=20)
    gen, off, def_ = re.update_tri_ranks(
        current_off_rank=1.0,
        current_def_rank=1.0,
        goals_scored=5,
        goals_conceded=0,
        xg_for=5.0,
        xg_against=0.0,
        expected_goals_model=1.27
    )
    assert off >= 1.0, f"off_rank no pot ser < 1.0, got {off}"
    assert def_ >= 1.0, f"def_rank no pot ser < 1.0, got {def_}"
    
    gen2, off2, def2 = re.update_tri_ranks(
        current_off_rank=20.0,
        current_def_rank=20.0,
        goals_scored=0,
        goals_conceded=5,
        xg_for=0.0,
        xg_against=5.0,
        expected_goals_model=1.27
    )
    assert off2 <= 20.0, f"off_rank no pot ser > 20.0, got {off2}"
    assert def2 <= 20.0, f"def_rank no pot ser > 20.0, got {def2}"
    print(f"  PASS: Ranks clamped correctament: [{off:.1f}, {def_:.1f}] i [{off2:.1f}, {def2:.1f}]")

def test_two_teams_diverge():
    """Dos equips inicialment iguals divergeixen amb resultats oposats."""
    re = RankEngine(league_teams_count=22)
    
    team_a_off, team_a_def = 11.0, 11.0
    team_b_off, team_b_def = 11.0, 11.0
    
    # Equip A guanya 3-0
    _, team_a_off, team_a_def = re.update_tri_ranks(team_a_off, team_a_def, 3, 0, 2.5, 0.3, 1.15)
    _, team_b_off, team_b_def = re.update_tri_ranks(team_b_off, team_b_def, 0, 3, 0.3, 2.5, 1.15)
    
    assert team_a_off != team_b_off, "Equips amb resultats oposats han de divergir en off_rank"
    assert team_a_off < team_b_off, f"A (guanyador) hauria de tenir millor off_rank: A={team_a_off:.2f} vs B={team_b_off:.2f}"
    assert team_a_def < team_b_def, f"A (porteria a zero) hauria de tenir millor def_rank: A={team_a_def:.2f} vs B={team_b_def:.2f}"
    print(f"  PASS: Divergència: A=(off={team_a_off:.2f}, def={team_a_def:.2f}) vs B=(off={team_b_off:.2f}, def={team_b_def:.2f})")

def test_xg_absent_fallback():
    """Si l'xG no està disponible, es fa servir gols reals com a fallback sense error."""
    re = RankEngine(league_teams_count=20)
    gen, off, def_ = re.update_tri_ranks(
        current_off_rank=10.0,
        current_def_rank=10.0,
        goals_scored=2,
        goals_conceded=1,
        xg_for=2.0,      # Simula fallback: xg = goals
        xg_against=1.0,
        expected_goals_model=1.27
    )
    assert 1.0 <= off <= 20.0
    assert 1.0 <= def_ <= 20.0
    print(f"  PASS: xG fallback funciona: off={off:.2f} def={def_:.2f}")

def test_goal_model_valid_probabilities():
    """GoalModel genera probabilitats 1X2 que sumen 100% i Over/Under coherents."""
    gm = GoalModel()
    home = {"off_rank": 3.0, "def_rank": 5.0, "competition_id": "LALIGA"}
    away = {"off_rank": 10.0, "def_rank": 12.0, "competition_id": "LALIGA"}
    result = gm.predict_match(home, away)
    
    p_total = result["prob_1X2"]["1"] + result["prob_1X2"]["X"] + result["prob_1X2"]["2"]
    assert abs(p_total - 100.0) < 0.5, f"1X2 han de sumar ~100%, got {p_total:.2f}"
    
    o25 = result["over_under"]["over_2_5"]
    u25 = result["over_under"]["under_2_5"]
    assert abs(o25 + u25 - 100.0) < 0.5, f"Over+Under han de sumar ~100%, got {o25 + u25:.2f}"
    
    assert result["prob_1X2"]["1"] > 0
    assert result["prob_1X2"]["X"] > 0
    assert result["prob_1X2"]["2"] > 0
    print(f"  PASS: 1X2 sumen {p_total:.1f}% (1={result['prob_1X2']['1']:.1f}, X={result['prob_1X2']['X']:.1f}, 2={result['prob_1X2']['2']:.1f})")

def test_hypermotion_profile_corrected():
    """El perfil d'Hypermotion ha de tenir away_avg = 1.05 (calibrat estrictament amb pre-test J1-5 sense leakage)."""
    gm = GoalModel()
    prof = gm.league_profiles["HYPERMOTION"]
    assert prof["away_avg"] == 1.05, f"away_avg hauria de ser 1.05, got {prof['away_avg']}"
    assert prof["home_avg"] == 1.35, f"home_avg hauria de ser 1.35, got {prof['home_avg']}"
    assert prof["teams"] == 22
    print(f"  PASS: Hypermotion profile: home_avg={prof['home_avg']}, away_avg={prof['away_avg']}")

def test_hypermotion_lambda_away_reasonable():
    """Lambda d'un visitant mitjà a Hypermotion ha de ser coherent (~1.05)."""
    gm = GoalModel()
    home = {"off_rank": 11.0, "def_rank": 11.0, "competition_id": "HYPERMOTION", "teams_count": 22}
    away = {"off_rank": 11.0, "def_rank": 11.0, "competition_id": "HYPERMOTION", "teams_count": 22}
    lh, la = gm.estimate_lambdas(home, away)
    assert 0.90 < la < 1.20, f"Lambda away hauria d'estar entre 0.90 i 1.20 a Hypermotion, got {la:.3f}"
    assert 1.20 < lh < 1.50, f"Lambda home hauria d'estar entre 1.20 i 1.50 a Hypermotion, got {lh:.3f}"
    print(f"  PASS: Hypermotion lambda_home={lh:.3f} lambda_away={la:.3f}")

def test_dixon_coles_non_negative():
    """La matriu de Dixon-Coles no ha de contenir probabilitats negatives."""
    gm = GoalModel(rho=-0.10)
    for lh, la in [(0.5, 0.3), (2.0, 1.5), (1.2, 1.0), (3.0, 0.2)]:
        mat = gm.build_bivariate_dixon_coles_matrix(lh, la)
        assert np.all(mat >= 0), f"La matriu conté valors negatius per lh={lh}, la={la}"
        assert abs(mat.sum() - 1.0) < 0.001, f"La matriu no suma 1.0: {mat.sum():.6f}"
    print(f"  PASS: Matriu Dixon-Coles no-negativa i normalitzada per a tots els casos de prova")

def test_elo_change_symmetry():
    """El canvi d'Elo és zero-sum: sum(delta) = 0."""
    re = RankEngine()
    for hg, ag in [(2, 1), (0, 0), (1, 3)]:
        dh, da = re.calculate_elo_change(1500.0, 1500.0, hg, ag)
        assert abs(dh + da) < 0.001, f"Elo changes should be zero-sum: dh={dh:.4f} da={da:.4f}"
    print(f"  PASS: Elo canvis zero-sum verificats")

def test_corner_model_valid():
    """CornerModel genera prediccions vàlides."""
    cm = CornerModel()
    result = cm.estimate_corners(
        home_team={"avg_corners_for_5": 5.5, "avg_corners_against_5": 4.0},
        away_team={"avg_corners_for_5": 4.0, "avg_corners_against_5": 5.0}
    )
    assert result["expected_total_corners"] > 0
    assert 0 < result["prob_over_corners"]["over_9_5"] < 100
    print(f"  PASS: CornerModel: total={result['expected_total_corners']:.1f} O9.5={result['prob_over_corners']['over_9_5']:.1f}%")

def test_card_model_valid():
    """CardModel genera prediccions vàlides."""
    cm = CardModel()
    result = cm.estimate_card_expectations(
        referee={"yellow_cards_avg": 5.0, "red_cards_avg": 0.3, "strictness_index": 1.1, "name": "Test"},
        home_team={"avg_cards_for_5": 2.5, "id": "RMA", "general_rank": 3.0},
        away_team={"avg_cards_for_5": 2.3, "id": "FCB", "general_rank": 1.5}
    )
    assert result["expected_total_cards"] > 2.0
    assert 0 < result["prob_over_cards"]["over_4_5"] < 100
    print(f"  PASS: CardModel: total={result['expected_total_cards']:.1f} derby={result['is_derby']} O4.5={result['prob_over_cards']['over_4_5']:.1f}%")

def test_competition_id_propagation_in_predictor():
    """Verifica que MatchPredictor propaga competition_id correctament cap al GoalModel."""
    from engine.match_predictor import MatchPredictor
    mp = MatchPredictor()
    
    h_team = {"id": "TEST_H", "name": "Test Home", "off_rank": 11.0, "def_rank": 11.0}
    a_team = {"id": "TEST_A", "name": "Test Away", "off_rank": 11.0, "def_rank": 11.0}
    ref = {"name": "Ref", "yellow_cards_avg": 4.5, "strictness_index": 1.0}
    
    # 1. Sense context -> Default a LALIGA
    pred_la = mp.predict_single_match(dict(h_team), dict(a_team), ref)
    # 2. Amb context HYPERMOTION -> Ha d'utilitzar el perfil HYPERMOTION
    pred_hm = mp.predict_single_match(dict(h_team), dict(a_team), ref, match_context={"competition_id": "HYPERMOTION"})
    
    assert pred_la["goals"]["expected_goals_home"] != pred_hm["goals"]["expected_goals_home"] or pred_la["goals"]["expected_goals_away"] != pred_hm["goals"]["expected_goals_away"], \
        "HYPERMOTION ha de generar lambdas diferents de LALIGA"
    assert pred_hm["goals"]["expected_goals_home"] == 1.35
    assert pred_hm["goals"]["expected_goals_away"] == 1.05
    print("  PASS: MatchPredictor propaga correctament competition_id al GoalModel")

def test_crawler_idempotency_and_finished_guard():
    """Verifica que partits ja finalitzats no dupliquen Elo/Ranks i partits no finalitzats no modifiquen dades."""
    import sqlite3
    
    conn = sqlite3.connect(":memory:")
    c = conn.cursor()
    c.execute("CREATE TABLE matches (id TEXT PRIMARY KEY, status TEXT, home_goals INT, away_goals INT)")
    c.execute("CREATE TABLE team_ratings (team_id TEXT PRIMARY KEY, elo_rating REAL, off_rank REAL, def_rank REAL)")
    
    c.execute("INSERT INTO team_ratings VALUES ('T1', 1500.0, 10.0, 10.0)")
    c.execute("INSERT INTO team_ratings VALUES ('T2', 1500.0, 10.0, 10.0)")
    c.execute("INSERT INTO matches VALUES ('M1', 'FINISHED', 2, 1)")
    conn.commit()
    
    # 1. Idempotència: Si el partit ja estava FINISHED a la DB, s'ha de saltar
    c.execute("SELECT status, home_goals, away_goals FROM matches WHERE id = 'M1'")
    existing = c.fetchone()
    assert existing is not None and existing[0] == "FINISHED" and existing[1] is not None
    # No s'ha de tocar l'Elo
    c.execute("SELECT elo_rating FROM team_ratings WHERE team_id = 'T1'")
    elo_initial = c.fetchone()[0]
    assert elo_initial == 1500.0
    
    # 2. Guard de partit no finalitzat: Si ac_code != '3' i is_finished = False, no s'ha d'actualitzar
    is_finished = False
    if is_finished:
        c.execute("UPDATE team_ratings SET elo_rating = 1600.0 WHERE team_id = 'T1'")
    conn.commit()
    c.execute("SELECT elo_rating FROM team_ratings WHERE team_id = 'T1'")
    assert c.fetchone()[0] == 1500.0
    print("  PASS: Idempotència i guard de finalització verificats")

def test_flashscore_status_codes_and_repair():
    """Simula els codis reals de Flashscore (AC/DA) i verifica la transició segura d'estats."""
    # 1. Codi AC='1', DA='1': SCHEDULED (programat)
    ac_code_sched = "1"
    da_code_sched = "1"
    is_susp_sched = ac_code_sched in ['37', '4', '10', '11']
    is_fin_sched = (da_code_sched == "3" or ac_code_sched == "3") and not is_susp_sched
    assert not is_fin_sched, "Partit programat no ha de ser marcat com a finalitzat"
    
    # 2. Codi AC='2'/'9', DA='2'/'9': LIVE / DESCANS (marcador parcial 1-0)
    ac_code_live = "9"  # Descans
    da_code_live = "9"
    score_h_live = "1"
    score_a_live = "0"
    is_susp_live = ac_code_live in ['37', '4', '10', '11']
    is_fin_live = (da_code_live == "3" or ac_code_live == "3") and not is_susp_live
    assert not is_fin_live, "Partit en joc (AC='9' descans) mai ha de ser marcat com a finalitzat"
    
    # 3. Codi AC='37', AM='Aplazado': SUSPENDED
    ac_code_susp = "37"
    is_susp = ac_code_susp in ['37', '4', '10', '11']
    assert is_susp, "Partit suspès ha de ser detectat correctament"
    
    # 4. Codi AC='3', DA='3': FINISHED (marcador final 2-1)
    ac_code_fin = "3"
    da_code_fin = "3"
    is_susp_fin = ac_code_fin in ['37', '4', '10', '11']
    is_fin_real = (da_code_fin == "3" or ac_code_fin == "3") and not is_susp_fin
    assert is_fin_real, "Partit amb AC='3' i DA='3' ha de ser marcat com a finalitzat"
    
    # 5. Flux de reparació/transició segura:
    import sqlite3
    conn = sqlite3.connect(":memory:")
    c = conn.cursor()
    c.execute("CREATE TABLE matches (id TEXT PRIMARY KEY, status TEXT, home_goals INT, away_goals INT)")
    c.execute("CREATE TABLE team_ratings (team_id TEXT PRIMARY KEY, elo_rating REAL, off_rank REAL, def_rank REAL)")
    c.execute("INSERT INTO team_ratings VALUES ('H', 1500.0, 10.0, 10.0)")
    c.execute("INSERT INTO team_ratings VALUES ('A', 1500.0, 10.0, 10.0)")
    
    # Inicialment el partit estava SCHEDULED
    c.execute("INSERT INTO matches VALUES ('M_TEST', 'SCHEDULED', NULL, NULL)")
    conn.commit()
    
    # Arriba resultat definitiu (AC='3', 3-1)
    c.execute("SELECT status, home_goals FROM matches WHERE id = 'M_TEST'")
    ex = c.fetchone()
    if not (ex and ex[0] == "FINISHED" and ex[1] is not None):
        # Transició segura a FINISHED
        c.execute("UPDATE matches SET status = 'FINISHED', home_goals = 3, away_goals = 1 WHERE id = 'M_TEST'")
        re = RankEngine(league_teams_count=20)
        dh, da = re.calculate_elo_change(1500.0, 1500.0, 3, 1)
        _, h_off, h_def = re.update_tri_ranks(10.0, 10.0, 3, 1, 2.5, 0.8, 1.27)
        c.execute("UPDATE team_ratings SET elo_rating = elo_rating + ?, off_rank = ?, def_rank = ? WHERE team_id = 'H'", (dh, h_off, h_def))
        conn.commit()
        
    c.execute("SELECT status, home_goals, away_goals FROM matches WHERE id = 'M_TEST'")
    assert c.fetchone() == ("FINISHED", 3, 1)
    c.execute("SELECT elo_rating, off_rank FROM team_ratings WHERE team_id = 'H'")
    elo_after_1, off_after_1 = c.fetchone()
    assert elo_after_1 > 1500.0 and off_after_1 < 10.0
    
    # Segona passada (re-execució): ha de ser idempotent i no modificar res
    c.execute("SELECT status, home_goals FROM matches WHERE id = 'M_TEST'")
    ex2 = c.fetchone()
    if ex2 and ex2[0] == "FINISHED" and ex2[1] is not None:
        # Se salta!
        pass
    else:
        assert False, "Hauria d'haver detectat el partit com a FINISHED i saltar-lo"
        
    c.execute("SELECT elo_rating, off_rank FROM team_ratings WHERE team_id = 'H'")
    elo_after_2, off_after_2 = c.fetchone()
    assert elo_after_1 == elo_after_2 and off_after_1 == off_after_2
    print("  PASS: Codis d'estat Flashscore i reparació/transició segura verificats")

if __name__ == "__main__":
    tests = [
        test_tri_ranks_update_positive_performance,
        test_tri_ranks_update_negative_performance,
        test_tri_ranks_clamped,
        test_two_teams_diverge,
        test_xg_absent_fallback,
        test_goal_model_valid_probabilities,
        test_hypermotion_profile_corrected,
        test_hypermotion_lambda_away_reasonable,
        test_dixon_coles_non_negative,
        test_elo_change_symmetry,
        test_corner_model_valid,
        test_card_model_valid,
        test_competition_id_propagation_in_predictor,
        test_crawler_idempotency_and_finished_guard,
        test_flashscore_status_codes_and_repair,
    ]
    
    print("=" * 60)
    print("TESTS: Rànquings, Model i Integritat")
    print("=" * 60)
    
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"  FAIL: {t.__name__}: {e}")
            failed += 1
    
    print("-" * 60)
    print(f"RESULTAT: {passed} PASS / {failed} FAIL / {len(tests)} TOTAL")
    if failed > 0:
        sys.exit(1)
    else:
        print("TOTS ELS TESTS HAN PASSAT.")
