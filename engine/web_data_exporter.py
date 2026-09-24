"""
engine/web_data_exporter.py
===========================
Generador integral de dades per a l'aplicació web estàtica allotjada a Vercel.
Recull, processa i exporta tota la informació a 'web/data/data.json':
1. Metadades i estat de les competicions
2. Power Rànquings Elo amb estadístiques clàssiques (Punts, PJ, PG, PE, PP, GF, GC, DG, Forma W/D/L)
3. Prediccions oficials de cada partit de la jornada (xG, 1X2, marcadors, O/U 2.5, BTTS, Àrbitres)
4. Seleccions de valor matemàtic (+EV%)
5. Les 6 Combinades recomanades per lliga (2 Segures @25€, 2 Semi @10€, 2 Arriscades @5€) + Multi-Lliga
6. Tauler financer 'Què hagués passat si...' amb KPIs de rendibilitat, ROI i historial jornada a jornada.
"""

import sys
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import os
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
WEB_DATA_DIR = ROOT / "web" / "data"
WEB_DATA_FILE = WEB_DATA_DIR / "data.json"

from database.db_manager import DatabaseManager
from scraper.jornada_resolver import JornadaResolver
from scraper.referee_resolver import RefereeResolver
from scraper.winamax_scraper import WinamaxScraper
from engine.match_predictor import MatchPredictor
from engine.value_bet_engine import ValueBetEngine
from engine.combo_bet_engine import ComboBetEngine
from engine.combo_tracker import ComboTracker
from engine.changelog_manager import ChangelogManager

COMPETITIONS_META = [
    {
        "id": "LALIGA",
        "name": "LaLiga EA Sports",
        "country": "Espanya",
        "flag": "🇪🇸",
        "flashscore_code": "la-liga",
        "winamax_sport_id": 1,
        "winamax_cat_id": 1,
    },
    {
        "id": "PREMIER",
        "name": "Premier League",
        "country": "Anglaterra",
        "flag": "🏴󠁧󠁢󠁥󠁮󠁧󠁿",
        "flashscore_code": "premier-league",
        "winamax_sport_id": 1,
        "winamax_cat_id": 32,
    },
    {
        "id": "HYPERMOTION",
        "name": "LaLiga Hypermotion",
        "country": "Espanya (2a)",
        "flag": "🇪🇸",
        "flashscore_code": "laliga2",
        "winamax_sport_id": 1,
        "winamax_cat_id": 2,
    },
    {
        "id": "CHAMPIONSHIP",
        "name": "EFL Championship",
        "country": "Anglaterra (2a)",
        "flag": "🏴󠁧󠁢󠁥󠁮󠁧󠁿",
        "flashscore_code": "championship",
        "winamax_sport_id": 1,
        "winamax_cat_id": 1,
    }
]

class WebDataExporter:
    def __init__(self, db: Optional[DatabaseManager] = None):
        self.db = db if db else DatabaseManager()
        self.predictor = MatchPredictor()
        self.referee_resolver = RefereeResolver(db=self.db)
        self.winamax = WinamaxScraper(headless=True)
        self.value_engine = ValueBetEngine(kelly_fraction=0.25)
        self.combo_engine = ComboBetEngine()
        self.tracker = ComboTracker(db=self.db)
        self.changelog_mgr = ChangelogManager()

    def get_power_rankings(self, competition_id: str) -> List[Dict[str, Any]]:
        """Calcula el rànquing Elo complet amb taula clàssica de classificació i forma recent."""
        elo_standings = self.db.get_league_standings_elo(competition_id)
        if not elo_standings:
            return []

        # Recuperar tots els partits finalitzats per calcular punts i forma
        with self.db.get_connection() as conn:
            c = conn.cursor()
            c.execute("""
                SELECT home_team_id, away_team_id, home_goals, away_goals, date_time, jornada
                FROM matches
                WHERE competition_id = ? AND status = 'FINISHED'
            """, (competition_id,))
            matches = [dict(r) for r in c.fetchall()]

        # Ordenar estrictament per ordre cronològic real (no alfabètic de text DD.MM.YYYY)
        def parse_match_date(m):
            dt_str = m.get("date_time") or ""
            try:
                return datetime.strptime(dt_str.strip(), "%d.%m.%Y %H:%M")
            except Exception:
                try:
                    return datetime.strptime(dt_str.strip(), "%Y-%m-%d %H:%M")
                except Exception:
                    return datetime(2026, 1, 1) + timedelta(days=int(m.get("jornada") or 0))

        matches.sort(key=parse_match_date)

        # Estructures per equip
        stats = {}
        for t in elo_standings:
            tid = t["id"]
            stats[tid] = {
                "played": 0, "won": 0, "drawn": 0, "lost": 0,
                "gf": 0, "ga": 0, "gd": 0, "points": 0,
                "form": [] # Llista de 'W', 'D', 'L'
            }

        for m in matches:
            hid = m["home_team_id"]
            aid = m["away_team_id"]
            if m["home_goals"] is None or m["away_goals"] is None:
                continue
            hg = int(m["home_goals"])
            ag = int(m["away_goals"])

            if hid in stats:
                st = stats[hid]
                st["played"] += 1
                st["gf"] += hg
                st["ga"] += ag
                if hg > ag:
                    st["won"] += 1
                    st["points"] += 3
                    st["form"].append("W")
                elif hg == ag:
                    st["drawn"] += 1
                    st["points"] += 1
                    st["form"].append("D")
                else:
                    st["lost"] += 1
                    st["form"].append("L")

            if aid in stats:
                st = stats[aid]
                st["played"] += 1
                st["gf"] += ag
                st["ga"] += hg
                if ag > hg:
                    st["won"] += 1
                    st["points"] += 3
                    st["form"].append("W")
                elif ag == hg:
                    st["drawn"] += 1
                    st["points"] += 1
                    st["form"].append("D")
                else:
                    st["lost"] += 1
                    st["form"].append("L")

        # Calcular rànquings ofensius i defensius dinàmics per a cada equip segons gols reals
        team_stats_list = []
        for team in elo_standings:
            tid = team["id"]
            st = stats.get(tid, {"played": 0, "won": 0, "drawn": 0, "lost": 0, "gf": 0, "ga": 0, "points": 0, "form": []})
            pj = max(1, st["played"])
            gf_per_game = round(st["gf"] / pj, 2)
            ga_per_game = round(st["ga"] / pj, 2)
            team_stats_list.append({
                "team": team,
                "st": st,
                "gf_per_game": gf_per_game,
                "ga_per_game": ga_per_game
            })

        # Ordenar per millor atac (més gols per partit) -> rànquing 1 a N
        sorted_by_off = sorted(team_stats_list, key=lambda x: (x["gf_per_game"], x["st"]["gf"]), reverse=True)
        off_ranks = {item["team"]["id"]: idx for idx, item in enumerate(sorted_by_off, 1)}

        # Ordenar per millor defensa (menys gols encaixats per partit) -> rànquing 1 a N
        sorted_by_def = sorted(team_stats_list, key=lambda x: (x["ga_per_game"], x["st"]["ga"]))
        def_ranks = {item["team"]["id"]: idx for idx, item in enumerate(sorted_by_def, 1)}

        # Fusionar amb dades d'Elo
        result = []
        for rank_idx, item in enumerate(team_stats_list, 1):
            team = item["team"]
            tid = team["id"]
            st = item["st"]
            gd = st["gf"] - st["ga"]
            recent_form = st["form"][-5:] if st["form"] else ["-"]

            result.append({
                "rank": rank_idx,
                "team_id": tid,
                "name": team["name"],
                "short_name": team.get("short_name") or team["name"][:3].upper(),
                "elo_rating": round(float(team.get("elo_rating", 1500.0)), 1),
                "off_rank": float(off_ranks.get(tid, rank_idx)),
                "def_rank": float(def_ranks.get(tid, rank_idx)),
                "played": st["played"],
                "won": st["won"],
                "drawn": st["drawn"],
                "lost": st["lost"],
                "gf": st["gf"],
                "ga": st["ga"],
                "gd": gd,
                "points": st["points"],
                "form": recent_form,
                "avg_cards": round(float(team.get("avg_cards_for_5", 2.0)), 1),
                "avg_corners": round(float(team.get("avg_corners_for_5", 4.5)), 1)
            })

        # Ordenar per rànquing Elo descendent
        result.sort(key=lambda x: x["elo_rating"], reverse=True)
        for idx, r in enumerate(result, 1):
            r["rank"] = idx

        return result

    def get_upcoming_predictions(self, competition_id: str, max_matches: int = 11) -> Dict[str, Any]:
        """Recupera els partits pendents de la propera jornada i genera les prediccions completes."""
        # Buscar partits SCHEDULED
        with self.db.get_connection() as conn:
            max_f = conn.cursor().execute("SELECT MAX(jornada) FROM matches WHERE competition_id = ? AND status = 'FINISHED'", (competition_id,)).fetchone()[0] or 0
        active_jornada = max_f + 1

        with self.db.get_connection() as conn:
            c = conn.cursor()
            c.execute("""
                SELECT * FROM matches
                WHERE competition_id = ? AND status = 'SCHEDULED' AND jornada >= ?
                ORDER BY jornada ASC, date_time ASC
            """, (competition_id, active_jornada))
            rows = [dict(r) for r in c.fetchall()]

        # Si a SQLite no hi ha suficients partits scheduled per a aquesta jornada, consultar JornadaResolver
        if len(rows) < 8:
            resolver = JornadaResolver(competition_id=competition_id)
            fixtures = resolver.get_jornada_fixtures(active_jornada)
            if fixtures:
                rows = []
                for f in fixtures:
                    h_name = f["home_team"]
                    a_name = f["away_team"]
                    h_id = self.db.find_team_id(h_name) or self.db.get_or_create_team(h_name)
                    a_id = self.db.find_team_id(a_name) or self.db.get_or_create_team(a_name)
                    rows.append({
                        "competition_id": competition_id,
                        "jornada": active_jornada,
                        "home_team_id": h_id,
                        "away_team_id": a_id,
                        "date_time": f.get("date", "Pendent"),
                        "status": "SCHEDULED",
                        "home_name": h_name,
                    })

        predicted_list = []
        for r in rows[:max_matches]:
            h_id = r["home_team_id"]
            a_id = r["away_team_id"]
            home_data = self.db.get_team_rating(h_id)
            away_data = self.db.get_team_rating(a_id)
            match_date = r.get("date_time", "Pendent")

            # Resolució àrbitre
            if competition_id == "LALIGA":
                ref_res = self.referee_resolver.resolve_referee(
                    home_name=home_data["name"],
                    away_name=away_data["name"],
                    match_date=match_date
                )
                ref_data = ref_res["ref_data"]
            else:
                ref_data = self.db.get_referee("REF_DEFAULT")
                ref_res = {"name": "Àrbitre Oficial", "is_generic": True, "ref_data": ref_data}

            # Predicció
            pred = self.predictor.predict_single_match(
                home_team=home_data,
                away_team=away_data,
                referee=ref_data,
                match_context={"date": match_date}
            )
            pred["competition_id"] = competition_id
            pred["date"] = match_date
            pred["referee_resolution"] = ref_res

            # Cuotes Winamax
            odds = self.winamax.get_match_odds(home_data["name"], away_data["name"], competition_id=competition_id)
            bet_analysis = self.value_engine.analyze_match_betting(pred, odds)
            pred["odds"] = odds
            pred["bet_analysis"] = bet_analysis

            # Extracció de probabilitats netes
            goals = pred.get("goals", {})
            prob_1x2 = goals.get("prob_1X2", {})
            prob_ou = goals.get("over_under", {})
            prob_btts = goals.get("btts", {})
            scores = goals.get("top_scorelines", [])
            if scores and isinstance(scores[0], dict):
                top_score = scores[0].get("score", "1-1")
            elif scores and isinstance(scores[0], (list, tuple)):
                top_score = str(scores[0][0])
            else:
                top_score = "1-1"

            o_1x2 = odds.get("1X2") or odds.get("odds_1x2") or {}
            o_ou = odds.get("over_under_2_5") or {}
            o_btts = odds.get("btts") or {}

            # Helper per percentatges segurs (de 0..100)
            def _to_pct(val, default=50.0):
                if val is None:
                    return default
                try:
                    f = float(val)
                    if 0 < f <= 1.0:
                        return round(f * 100.0, 1)
                    return round(f, 1)
                except Exception:
                    return default

            # Àrbitre info neta
            ref_info = {
                "name": ref_res["name"],
                "is_official": not ref_res.get("is_generic", False),
                "yellow_avg": round(float(ref_data.get("yellow_avg", 4.2)), 2),
                "red_avg": round(float(ref_data.get("red_avg", 0.2)), 2),
                "fouls_avg": round(float(ref_data.get("fouls_avg", 24.5)), 1)
            }

            predicted_list.append({
                "match_id": f"{competition_id}_J{active_jornada}_{h_id}_{a_id}",
                "competition_id": competition_id,
                "jornada": active_jornada,
                "home_team": {
                    "id": h_id,
                    "name": home_data["name"],
                    "short_name": home_data.get("short_name", home_data["name"][:3].upper()),
                    "elo": round(float(home_data.get("elo_rating", 1500.0)), 1)
                },
                "away_team": {
                    "id": a_id,
                    "name": away_data["name"],
                    "short_name": away_data.get("short_name", away_data["name"][:3].upper()),
                    "elo": round(float(away_data.get("elo_rating", 1500.0)), 1)
                },
                "date": match_date,
                "verdict": pred.get("verdict_1x2", "Pronòstic Reservat"),
                "prob_1": _to_pct(prob_1x2.get("1"), 33.3),
                "prob_x": _to_pct(prob_1x2.get("X"), 33.3),
                "prob_2": _to_pct(prob_1x2.get("2"), 33.3),
                "xg_home": round(float(goals.get("expected_goals_home", goals.get("lambda_home", 1.30))), 2),
                "xg_away": round(float(goals.get("expected_goals_away", goals.get("mu_away", 1.05))), 2),
                "most_likely_score": top_score,
                "prob_over_25": _to_pct(prob_ou.get("over_2_5", prob_ou.get("over")), 50.0),
                "prob_under_25": _to_pct(prob_ou.get("under_2_5", prob_ou.get("under")), 50.0),
                "prob_btts_yes": _to_pct(prob_btts.get("yes"), 50.0),
                "prob_btts_no": _to_pct(prob_btts.get("no"), 50.0),
                "referee": ref_info,
                "odds_1x2": {
                    "1": o_1x2.get("1"),
                    "X": o_1x2.get("X"),
                    "2": o_1x2.get("2")
                },
                "odds_ou25": {
                    "over": o_ou.get("over"),
                    "under": o_ou.get("under")
                },
                "odds_btts": {
                    "yes": o_btts.get("yes"),
                    "no": o_btts.get("no")
                },
                "value_bets": bet_analysis.get("value_bets", []),
                "raw_pred": pred # per a càlculs posteriors
            })

        return {
            "jornada": active_jornada,
            "competition_id": competition_id,
            "matches": predicted_list
        }

    def get_full_combos_suite(self, predicted_matches: List[Dict[str, Any]], competition_id: str, jornada: int) -> Dict[str, Any]:
        """
        Retorna el paquet oficial de 6 combinades per lliga o multi-lliga.
        Si la jornada ja s'havia generat (ex: divendres), NO regenera les combinades
        sinó que les preserva i n'avalua dinàmicament l'estat de cada partit (dissabte/diumenge).
        """
        existing_combos = self.db.get_combos_for_jornada(competition_id, jornada)

        if existing_combos and len(existing_combos) >= 2:
            safe_list = []
            semi_list = []
            risky_list = []

            for c in existing_combos:
                legs_evaluated = []
                all_finished = True
                any_lost = False

                for leg in c.get("legs", []):
                    h_id = leg.get("home_team_id")
                    a_id = leg.get("away_team_id")
                    match_row = None

                    if h_id and a_id:
                        with self.db.get_connection() as conn:
                            cursor = conn.cursor()
                            cursor.execute(
                                "SELECT * FROM matches WHERE home_team_id = ? AND away_team_id = ? ORDER BY date_time DESC LIMIT 1",
                                (h_id, a_id)
                            )
                            row = cursor.fetchone()
                            if row:
                                match_row = dict(row)

                    if not match_row or match_row.get("status") != "FINISHED":
                        all_finished = False
                        leg_status = "PENDING"
                        actual_res = "Pendent de disputar"
                    else:
                        leg_status, actual_res = self.tracker.evaluate_leg(leg, match_row)

                    if leg_status == "LOST":
                        any_lost = True
                    elif leg_status == "PENDING":
                        all_finished = False

                    legs_evaluated.append({
                        "matchup": leg.get("matchup", ""),
                        "selection_name": leg.get("selection_name", ""),
                        "category": leg.get("category", ""),
                        "bookie_odd": float(leg.get("bookie_odd", 1.0)),
                        "model_prob": float(leg.get("model_prob", 0.0)),
                        "date": leg.get("match_date", leg.get("date", "")),
                        "url": leg.get("url", ""),
                        "status": leg_status,
                        "actual_result": actual_res
                    })

                stake = float(c.get("stake", 10.0))
                odd = float(c.get("combined_odd", 1.0))
                booster_pct = float(c.get("booster_pct") or self.combo_engine.calculate_winamax_booster(len(legs_evaluated)))
                boosted_odd = round(odd * (1.0 + booster_pct / 100.0), 2) if booster_pct > 0 else odd

                if any_lost:
                    final_status = "LOST"
                    payout = 0.0
                    profit = -stake
                elif all_finished:
                    final_status = "WON"
                    payout = round(stake * boosted_odd, 2)
                    profit = round(payout - stake, 2)
                else:
                    final_status = "PENDING"
                    payout = round(stake * boosted_odd, 2)
                    profit = round(payout - stake, 2)

                if final_status != c.get("status") and final_status in ["WON", "LOST"]:
                    self.db.update_combo_evaluation(
                        combo_id=c["id"],
                        status=final_status,
                        payout=payout,
                        profit=profit,
                        legs_evaluation=legs_evaluated
                    )

                combo_obj = {
                    "id": c.get("id"),
                    "profile": c.get("profile", ""),
                    "category_code": "SAFE" if "SAFE" in c.get("profile", "").upper() or "SEGURA" in c.get("profile", "").upper() else ("SEMI" if "SEMI" in c.get("profile", "").upper() else "RISKY"),
                    "stake": stake,
                    "combined_odd": odd,
                    "combined_prob_pct": float(c.get("combined_prob_pct", 0.0)),
                    "fair_odd": float(c.get("fair_odd", 1.0)),
                    "ev_pct": float(c.get("ev_pct", 0.0)),
                    "booster_pct": booster_pct,
                    "boosted_odd": boosted_odd,
                    "potential_payout": round(stake * boosted_odd, 2),
                    "potential_profit": round((stake * boosted_odd) - stake, 2),
                    "status": final_status,
                    "winamax_url": c.get("winamax_url", "https://www.winamax.es"),
                    "legs": legs_evaluated
                }

                prof_upper = c.get("profile", "").upper()
                if "SAFE" in prof_upper or "SEGURA" in prof_upper:
                    safe_list.append(combo_obj)
                elif "SEMI" in prof_upper:
                    semi_list.append(combo_obj)
                else:
                    risky_list.append(combo_obj)

            return {
                "competition_id": competition_id,
                "jornada": jornada,
                "safe": safe_list,
                "semi": semi_list,
                "risky": risky_list
            }

        # 2. Si no hi ha combinades prèvies, generar-les amb el motor de combinades
        raw_preds = [m.get("raw_pred", m) for m in predicted_matches]
        if not raw_preds:
            return {"competition_id": competition_id, "jornada": jornada, "safe": [], "semi": [], "risky": []}

        is_multi = (competition_id == "MULTI")
        combos_data = self.combo_engine.generate_full_combos_suite(raw_preds, is_multi=is_multi)

        def format_and_save_combo(c: Dict[str, Any], prof_idx: int) -> Dict[str, Any]:
            if not c or not c.get("legs"):
                return {}
            legs = []
            for leg in c.get("legs", []):
                legs.append({
                    "matchup": leg.get("matchup", ""),
                    "selection_name": leg.get("name", ""),
                    "category": leg.get("category", ""),
                    "bookie_odd": float(leg.get("bookie_odd", 1.0)),
                    "model_prob": float(leg.get("model_prob", 0.0)),
                    "date": leg.get("date", ""),
                    "url": leg.get("url", ""),
                    "status": "PENDING",
                    "actual_result": "Pendent de disputar"
                })

            stake = float(c.get("recommended_stake", 10.0))
            odd = float(c.get("combined_odd", 1.0))
            booster_pct = float(c.get("booster_pct") or self.combo_engine.calculate_winamax_booster(len(legs)))
            boosted_odd = float(c.get("boosted_odd") or (round(odd * (1.0 + booster_pct / 100.0), 2) if booster_pct > 0 else odd))
            payout = round(stake * boosted_odd, 2)
            profit = round(payout - stake, 2)

            # Desar a la BD per garantir persistència durant tot el cap de setmana
            cat_code = c.get("category_code", "SAFE")
            profile_name = f"{cat_code}_{prof_idx}"
            c_to_save = dict(c)
            c_to_save["booster_pct"] = booster_pct
            c_to_save["boosted_odd"] = boosted_odd
            self.db.save_combo_recommendation(
                competition_id=competition_id,
                season="2026-2027",
                jornada=jornada,
                profile=profile_name,
                stake=stake,
                combo_summary=c_to_save
            )

            return {
                "profile": c.get("profile", ""),
                "category_code": cat_code,
                "stake": stake,
                "combined_odd": odd,
                "combined_prob_pct": float(c.get("combined_prob_pct", 0.0)),
                "fair_odd": float(c.get("fair_odd", 1.0)),
                "ev_pct": float(c.get("ev_pct", 0.0)),
                "booster_pct": booster_pct,
                "boosted_odd": boosted_odd,
                "potential_payout": payout,
                "potential_profit": profit,
                "status": "PENDING",
                "winamax_url": c.get("winamax_url", "https://www.winamax.es"),
                "legs": legs
            }

        safe_list = [format_and_save_combo(c, idx) for idx, c in enumerate(combos_data.get("safe", []), 1) if c]
        semi_list = [format_and_save_combo(c, idx) for idx, c in enumerate(combos_data.get("semi", []), 1) if c]
        risky_list = [format_and_save_combo(c, idx) for idx, c in enumerate(combos_data.get("risky", []), 1) if c]

        return {
            "competition_id": competition_id,
            "jornada": jornada,
            "safe": [c for c in safe_list if c],
            "semi": [c for c in semi_list if c],
            "risky": [c for c in risky_list if c]
        }

    def get_financial_ledger(self) -> Dict[str, Any]:
        """Calcula el resum financer 'Què hagués passat si...' amb historial complet."""
        # 1. Avalua pendents primer
        self.tracker.evaluate_all_pending_combos()

        # 2. Obtenir balanç complet de la BD
        history = self.db.get_bankroll_history()
        combos = history.get("combos", [])

        # Agrupar per jornada i competició per crear l'historial
        rounds_dict = {}
        for c in combos:
            key = f"{c['competition_id']}_J{c['jornada']}"
            if key not in rounds_dict:
                rounds_dict[key] = {
                    "competition_id": c["competition_id"],
                    "jornada": c["jornada"],
                    "season": c.get("season", "2026-2027"),
                    "created_at": c.get("created_at", ""),
                    "total_stake": 0.0,
                    "total_payout": 0.0,
                    "net_profit": 0.0,
                    "status_summary": "PENDING",
                    "combos": []
                }
            r = rounds_dict[key]
            r["combos"].append(c)
            if c["status"] in ["WON", "LOST"]:
                r["total_stake"] += float(c["stake"])
                r["total_payout"] += float(c["payout"])
                r["net_profit"] += float(c["profit"])

        for key, item in rounds_dict.items():
            if any(c["status"] == "PENDING" for c in item["combos"]):
                item["status_summary"] = "PENDING"
            elif item["net_profit"] > 0:
                item["status_summary"] = "WON"
            else:
                item["status_summary"] = "LOST"

        # Ordenar rondes
        rounds_history = []
        for key in sorted(rounds_dict.keys(), reverse=True):
            item = rounds_dict[key]
            item["total_stake"] = round(item["total_stake"], 2)
            item["total_payout"] = round(item["total_payout"], 2)
            item["net_profit"] = round(item["net_profit"], 2)
            item["roi_pct"] = round((item["net_profit"] / item["total_stake"] * 100.0), 1) if item["total_stake"] > 0 else 0.0
            rounds_history.append(item)

        # Càlcul de perfils
        safe_info = history.get("safe", {"total": 0, "won": 0, "hit_rate_pct": 0.0})
        semi_info = history.get("semi", {"total": 0, "won": 0, "hit_rate_pct": 0.0})
        risky_info = history.get("risky", {"total": 0, "won": 0, "hit_rate_pct": 0.0})

        # Càlcul de beneficis per perfil
        safe_stake = sum(c["stake"] for c in combos if "SAFE" in c["profile"].upper() and c["status"] in ["WON", "LOST"])
        safe_payout = sum(c["payout"] for c in combos if "SAFE" in c["profile"].upper() and c["status"] in ["WON", "LOST"])
        safe_profit = safe_payout - safe_stake

        semi_stake = sum(c["stake"] for c in combos if "SEMI" in c["profile"].upper() and c["status"] in ["WON", "LOST"])
        semi_payout = sum(c["payout"] for c in combos if "SEMI" in c["profile"].upper() and c["status"] in ["WON", "LOST"])
        semi_profit = semi_payout - semi_stake

        risky_stake = sum(c["stake"] for c in combos if "RISKY" in c["profile"].upper() and c["status"] in ["WON", "LOST"])
        risky_payout = sum(c["payout"] for c in combos if "RISKY" in c["profile"].upper() and c["status"] in ["WON", "LOST"])
        risky_profit = risky_payout - risky_stake

        total_stake = history.get("total_stake", 0.0)
        total_payout = history.get("total_payout", 0.0)
        net_profit = history.get("net_profit", 0.0)
        roi_pct = history.get("roi_pct", 0.0)

        won_count = safe_info["won"] + semi_info["won"] + risky_info["won"]
        total_eval = history.get("evaluated_combos", 0)
        win_rate = round((won_count / total_eval * 100.0), 1) if total_eval > 0 else 0.0

        return {
            "kpis": {
                "total_invested": total_stake,
                "total_payout": total_payout,
                "net_pnl": net_profit,
                "roi_pct": roi_pct,
                "total_bets": total_eval,
                "won_bets": won_count,
                "lost_bets": total_eval - won_count,
                "pending_bets": len([c for c in combos if c["status"] == "PENDING"]),
                "win_rate_pct": win_rate
            },
            "by_profile": {
                "safe": {
                    "label": "Combinades Segures",
                    "unit_stake": 25.0,
                    "total_invested": round(safe_stake, 2),
                    "total_payout": round(safe_payout, 2),
                    "net_pnl": round(safe_profit, 2),
                    "total": safe_info["total"],
                    "won": safe_info["won"],
                    "win_rate_pct": safe_info["hit_rate_pct"]
                },
                "semi": {
                    "label": "Combinades Semi-Arriscades",
                    "unit_stake": 10.0,
                    "total_invested": round(semi_stake, 2),
                    "total_payout": round(semi_payout, 2),
                    "net_pnl": round(semi_profit, 2),
                    "total": semi_info["total"],
                    "won": semi_info["won"],
                    "win_rate_pct": semi_info["hit_rate_pct"]
                },
                "risky": {
                    "label": "Combinades Arriscades",
                    "unit_stake": 5.0,
                    "total_invested": round(risky_stake, 2),
                    "total_payout": round(risky_payout, 2),
                    "net_pnl": round(risky_profit, 2),
                    "total": risky_info["total"],
                    "won": risky_info["won"],
                    "win_rate_pct": risky_info["hit_rate_pct"]
                }
            },
            "rounds_history": rounds_history,
            "all_combos": combos
        }

    def export_all(self) -> Path:
        """Executa la canalització completa i genera 'web/data/data.json'."""
        print("\n" + "=" * 75)
        print("   🌐 EXPORTANT DADES PER A L'APP WEB (VERCEL) · data.json")
        print("=" * 75)

        now = datetime.now()
        now_iso = now.isoformat()
        now_fmt = now.strftime("%d/%m/%Y a les %H:%M")

        power_rankings = {}
        predictions = {}
        combos = {}
        all_predicted_matches_flat = []
        all_value_bets = []

        active_comps_meta = []

        # 1. Processar cada lliga
        for meta in COMPETITIONS_META:
            cid = meta["id"]
            cname = meta["name"]
            print(f"[*] Processant lliga: {cname} ({cid})...")

            # A) Power Rankings
            pr = self.get_power_rankings(cid)
            power_rankings[cid] = pr

            # B) Prediccions
            pred_data = self.get_upcoming_predictions(cid)
            predictions[cid] = {
                "jornada": pred_data["jornada"],
                "competition_id": cid,
                "competition_name": cname,
                "matches": [{k: v for k, v in m.items() if k != "raw_pred"} for m in pred_data["matches"]]
            }

            # Extreure Value Bets
            for m in pred_data["matches"]:
                all_predicted_matches_flat.append(m)
                for vb in m.get("value_bets", []):
                    # Extreure el percentatge d'avantatge real (ev_pct)
                    edge_val = vb.get("ev_pct")
                    if edge_val is None:
                        edge_val = vb.get("edge")
                    if edge_val is None:
                        p_calc = float(vb.get("model_prob", 0.0)) / 100.0
                        b_calc = float(vb.get("bookie_odd", 1.0))
                        edge_val = (p_calc * b_calc - 1.0) * 100.0

                    stake_val = vb.get("kelly_pct")
                    if stake_val is None:
                        stake_val = vb.get("stake_pct", 2.5)

                    all_value_bets.append({
                        "competition_id": cid,
                        "competition_name": cname,
                        "matchup": f"{m['home_team']['name']} vs {m['away_team']['name']}",
                        "date": m["date"],
                        "selection": vb.get("name"),
                        "category": vb.get("category"),
                        "bookie_odd": float(vb.get("bookie_odd", 1.0)),
                        "model_prob": float(vb.get("model_prob", 0.0)),
                        "fair_odd": float(vb.get("fair_odd", 1.0)),
                        "edge_pct": round(float(edge_val), 1),
                        "kelly_stake": round(float(stake_val), 1),
                        "winamax_url": vb.get("url", m.get("odds", {}).get("match_url", "https://www.winamax.es"))
                    })

            # C) 6 Combinades per lliga
            league_combos = self.get_full_combos_suite(pred_data["matches"], cid, pred_data["jornada"])
            combos[cid] = league_combos

            active_comps_meta.append({
                "id": cid,
                "name": cname,
                "country": meta["country"],
                "flag": meta["flag"],
                "active_jornada": pred_data["jornada"],
                "matches_count": len(pred_data["matches"])
            })

        # 2. Mega-Combinades Multi-Lliga (6 combinades)
        print("[*] Generant les 6 combinades Multi-Lliga...")
        multi_combos = self.get_full_combos_suite(all_predicted_matches_flat, "MULTI", max(c["active_jornada"] for c in active_comps_meta))
        combos["MULTI"] = multi_combos

        # Ordenar Value bets pel millor edge %
        all_value_bets.sort(key=lambda x: (float(x.get("edge_pct")) if x.get("edge_pct") is not None else -999.0), reverse=True)

        # 3. Simulació Financera
        print("[*] Calculant resum financer 'Què hagués passat si...'...")
        financial_ledger = self.get_financial_ledger()

        # Garantir que el changelog conté l'entrada oficial d'avui
        entries = self.changelog_mgr.load_entries()
        today_str = now.strftime("%Y-%m-%d")
        if not any(e.get("date") == today_str for e in entries):
            self.changelog_mgr.record_pipeline_execution(
                date_str=today_str,
                notes=["Sincronització de les darreres dades i mètriques del model."]
            )

        # 4. Assembling JSON Payload
        payload = {
            "metadata": {
                "generated_at": now_iso,
                "formatted_date": now_fmt,
                "season": "2026-2027",
                "version": "2.0.0",
                "app_title": "Prediccions de Futbol · Poisson GLM & Winamax",
                "competitions": active_comps_meta
            },
            "changelog": self.changelog_mgr.get_feed(),
            "power_rankings": power_rankings,
            "predictions": predictions,
            "value_bets": all_value_bets,
            "combos": combos,
            "bankroll_simulation": financial_ledger
        }

        # 5. Escriure a fitxer
        WEB_DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(WEB_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

        size_kb = WEB_DATA_FILE.stat().st_size / 1024.0
        print(f"\n[ÈXIT] Dades exportades a: {WEB_DATA_FILE}")
        print(f"       Mida del fitxer: {size_kb:.1f} KB")
        print("=" * 75 + "\n")

        return WEB_DATA_FILE

if __name__ == "__main__":
    exporter = WebDataExporter()
    exporter.export_all()
