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
from scraper.bet365_scraper import Bet365Scraper
from engine.match_predictor import MatchPredictor
from engine.value_bet_engine import ValueBetEngine
from engine.combo_bet_engine import ComboBetEngine
from engine.combo_tracker import ComboTracker
from engine.changelog_manager import ChangelogManager
from engine.arbitrage_engine import ArbitrageEngine
from model.rank_engine import RankEngine

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
        self.bet365 = Bet365Scraper(headless=True)
        self.value_engine = ValueBetEngine(kelly_fraction=0.25)
        self.combo_engine = ComboBetEngine()
        self.tracker = ComboTracker(db=self.db)
        self.changelog_mgr = ChangelogManager()
        self.arbitrage_engine = ArbitrageEngine(min_profit_threshold=0.0)
        self.rank_engine = RankEngine(k_factor=32.0, league_teams_count=22.0)
        self.odds_alerts: List[Dict[str, Any]] = []

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

    def get_active_jornada(self, competition_id: str) -> int:
        """
        Determina de forma intel·ligent i robusta quina és la jornada activa d'una lliga.
        Una jornada segueix activa si:
        1. Té combinades recomanades pendents d'avaluar (status = 'PENDING').
        2. Té partits en curs (tant partits FINISHED recents com partits SCHEDULED pendents).
        3. És la darrera jornada predita oficialment a pipeline_state.json i encara té partits per jugar.
        Només avança a la jornada següent (J+1) quan la jornada actual s'ha completat al 100%.
        """
        comp = competition_id.upper()

        # 0. Consultar el calendari viu de Flashscore (font de la veritat en temps real)
        try:
            from daily_runner import get_upcoming_round_info
            u_info = get_upcoming_round_info(comp, db=self.db)
            if u_info and u_info.get("jornada"):
                return int(u_info["jornada"])
        except Exception:
            pass

        # 1. Comprovar si hi ha combinades recomanades pendents a SQLite
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            pending_j = cursor.execute(
                "SELECT MAX(jornada) FROM combo_recommendations WHERE competition_id = ? AND status = 'PENDING'",
                (comp,)
            ).fetchone()[0]
            if pending_j:
                return pending_j

        # 2. Comprovar pipeline_state.json
        state_file = ROOT / "data" / "pipeline_state.json"
        if state_file.exists():
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    st = json.load(f)
                    cand_j = st.get(comp, {}).get("last_predicted_jornada")
                    if cand_j:
                        with self.db.get_connection() as conn:
                            sched_cnt = conn.cursor().execute(
                                "SELECT COUNT(*) FROM matches WHERE competition_id = ? AND jornada = ? AND status = 'SCHEDULED'",
                                (comp, cand_j)
                            ).fetchone()[0]
                            if sched_cnt > 0:
                                return cand_j
            except Exception:
                pass

        # 3. Comprovar la darrera jornada jugada a SQLite (ignorant partits aplaçats aïllats del passat)
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            max_f = cursor.execute(
                "SELECT MAX(jornada) FROM matches WHERE competition_id = ? AND status IN ('FINISHED', 'SUSPENDED')",
                (comp,)
            ).fetchone()[0] or 0

            # Si la jornada max_f encara està en curs de jugar-se (té >= 2 partits SCHEDULED)
            if max_f > 0:
                sched_in_max_f = cursor.execute(
                    "SELECT COUNT(*) FROM matches WHERE competition_id = ? AND jornada = ? AND status = 'SCHEDULED'",
                    (comp, max_f)
                ).fetchone()[0] or 0
                if sched_in_max_f >= 2:
                    return max_f

            # Comprovar si hi ha una jornada posterior amb partits SCHEDULED
            next_sched = cursor.execute("""
                SELECT jornada, COUNT(*) as s_cnt
                FROM matches
                WHERE competition_id = ? AND jornada > ? AND status = 'SCHEDULED'
                GROUP BY jornada
                ORDER BY jornada ASC
                LIMIT 1
            """, (comp, max_f)).fetchone()
            if next_sched and next_sched[1] >= 2:
                return next_sched[0]

            # La jornada activa és la propera jornada natural
            return max_f + 1

    def get_upcoming_predictions(self, competition_id: str, max_matches: int = 12, existing_match_odds: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Recupera tots els partits de la jornada activa (tant els ja jugats com els pendents de disputar),
        calcula les prediccions completes, i per als partits ja disputats n'avalua el marcador final
        i les diferències amb les previsions del model.
        """
        active_jornada = self.get_active_jornada(competition_id)

        # 1. Recuperar partits registrats a SQLite per a aquesta jornada
        with self.db.get_connection() as conn:
            c = conn.cursor()
            c.execute("""
                SELECT * FROM matches
                WHERE competition_id = ? AND jornada = ?
                ORDER BY date_time ASC
            """, (competition_id, active_jornada))
            rows = [dict(r) for r in c.fetchall()]

        # Si a SQLite no hi ha suficients partits per a aquesta jornada, consultar JornadaResolver
        if len(rows) < 8:
            resolver = JornadaResolver(competition_id=competition_id)
            fixtures = resolver.get_jornada_fixtures(active_jornada)
            if fixtures:
                existing_keys = {f"{r['home_team_id']}_{r['away_team_id']}" for r in rows}
                for f in fixtures:
                    h_name = f["home_team"]
                    a_name = f["away_team"]
                    h_id = self.db.find_team_id(h_name) or self.db.get_or_create_team(h_name)
                    a_id = self.db.find_team_id(a_name) or self.db.get_or_create_team(a_name)
                    key = f"{h_id}_{a_id}"
                    if key not in existing_keys:
                        score_h = f.get("score_h")
                        score_a = f.get("score_a")
                        is_susp = f.get("is_suspended", False) or (
                            any(w in str(f.get("suspension_reason") or "").lower() for w in ['suspend', 'aplazad', 'pospuest', 'cancel', 'interrump', 'abandon'])
                        ) or (f.get("status") == "SUSPENDED")
                        is_played = (not is_susp) and score_h is not None and str(score_h).isdigit()
                        if is_susp:
                            m_status = "SUSPENDED"
                        elif is_played:
                            m_status = "FINISHED"
                        else:
                            m_status = "SCHEDULED"

                        rows.append({
                            "competition_id": competition_id,
                            "jornada": active_jornada,
                            "home_team_id": h_id,
                            "away_team_id": a_id,
                            "date_time": f.get("date", "Pendent"),
                            "status": m_status,
                            "home_goals": int(score_h) if is_played else None,
                            "away_goals": int(score_a) if is_played else None,
                            "home_name": h_name,
                            "url": f.get("url", ""),
                            "is_suspended": is_susp,
                            "suspension_reason": f.get("suspension_reason") or ("Partit suspès (a l'espera del resultat)" if is_susp else "")
                        })

        predicted_list = []
        referee_updates = []
        comp_meta = next((m for m in COMPETITIONS_META if m["id"] == competition_id), {})
        comp_name = comp_meta.get("name", competition_id)

        for r in rows[:max_matches]:
            h_id = r["home_team_id"]
            a_id = r["away_team_id"]
            home_data = self.db.get_team_rating(h_id)
            away_data = self.db.get_team_rating(a_id)
            match_date = r.get("date_time", "Pendent")

            # Resolució àrbitre
            m_code = None
            raw_url = r.get("url", "")
            if raw_url and "partido" in raw_url:
                m_code_parts = [p for p in raw_url.split("/") if p and p != "#"]
                if "partido" in m_code_parts:
                    idx_p = m_code_parts.index("partido")
                    if idx_p + 1 < len(m_code_parts):
                        m_code = m_code_parts[idx_p + 1]

            ref_res = self.referee_resolver.resolve_referee(
                home_name=home_data["name"],
                away_name=away_data["name"],
                match_date=match_date,
                match_code=m_code
            )
            ref_data = ref_res.get("ref_data") or self.db.get_referee("REF_DEFAULT")

            # Predicció matemàtica del model (Poisson GLM)
            pred = self.predictor.predict_single_match(
                home_team=home_data,
                away_team=away_data,
                referee=ref_data,
                match_context={"date": match_date}
            )
            pred["competition_id"] = competition_id
            pred["date"] = match_date
            pred["referee_resolution"] = ref_res

            # Comprovar si el partit ja s'ha disputat (FINISHED)
            h_goals = r.get("home_goals")
            a_goals = r.get("away_goals")
            is_finished = (r.get("status") == "FINISHED" and h_goals is not None)

            # Comprovar si la data i hora d'inici ja ha passat (partit començat o ja disputat)
            match_is_past = False
            if match_date and match_date != "Pendent":
                try:
                    clean_dt = str(match_date).strip()
                    m_dt = None
                    if "." in clean_dt:
                        m_dt = datetime.strptime(clean_dt[:16], "%d.%m.%Y %H:%M")
                    elif "-" in clean_dt:
                        m_dt = datetime.strptime(clean_dt[:16], "%Y-%m-%d %H:%M")
                    if m_dt and (now - m_dt).total_seconds() > 0:
                        match_is_past = True
                except Exception:
                    pass

            # Cuotes Winamax & Bet365
            odds = {}
            odds_b365 = {}
            bet_analysis = {"value_bets": []}
            w_has_1x2 = False
            b_has_1x2 = False

            if not is_finished and not match_is_past:
                odds = self.winamax.get_match_odds(home_data["name"], away_data["name"], competition_id=competition_id)
                if (not odds.get("matched")) and existing_match_odds:
                    m_key = f"{h_id}_{a_id}"
                    if m_key in existing_match_odds:
                        odds = existing_match_odds[m_key]

                odds_b365 = self.bet365.get_match_odds(
                    home_data["name"],
                    away_data["name"],
                    competition_id=competition_id,
                    match_pred=pred,
                    winamax_odds=odds
                )

                w_has_1x2 = bool(odds and odds.get("matched") and (odds.get("1X2") or {}).get("1"))
                b_has_1x2 = bool(odds_b365 and odds_b365.get("matched") and (odds_b365.get("1X2") or {}).get("1"))

                # Si falten quotes en un partit PENDENT DE DISPUTAR (futur), registrar alerta per a l'usuari
                if not w_has_1x2 or not b_has_1x2:
                    missing_bookies = []
                    if not w_has_1x2:
                        missing_bookies.append("Winamax")
                    if not b_has_1x2:
                        missing_bookies.append("Bet365")

                    c_meta = [c for c in COMPETITIONS_META if c["id"] == competition_id]
                    c_name = c_meta[0]["name"] if c_meta else competition_id
                    alert_item = {
                        "id": f"{competition_id}_{self.bet365._normalize(home_data['name'])}_{self.bet365._normalize(away_data['name'])}",
                        "competition_id": competition_id,
                        "competition_name": c_name,
                        "matchup": f"{home_data['name']} vs {away_data['name']}",
                        "home_team": home_data["name"],
                        "away_team": away_data["name"],
                        "date": match_date or "Pendent",
                        "jornada": active_jornada,
                        "missing_winamax": not w_has_1x2,
                        "missing_bet365": not b_has_1x2,
                        "missing_bookmakers": missing_bookies,
                        "message": f"Quotes no detectades a {', '.join(missing_bookies)} per al partit {home_data['name']} vs {away_data['name']}."
                    }
                    self.odds_alerts.append(alert_item)

                bet_analysis = self.value_engine.analyze_match_betting(pred, odds, bet365_odds=odds_b365)
                if (not bet_analysis.get("value_bets")) and existing_match_odds:
                    m_key = f"{h_id}_{a_id}"
                    if m_key in existing_match_odds and existing_match_odds[m_key].get("cached_value_bets"):
                        bet_analysis["value_bets"] = existing_match_odds[m_key]["cached_value_bets"]

            pred["odds"] = odds
            pred["odds_winamax"] = odds
            pred["odds_bet365"] = odds_b365
            pred["bet_analysis"] = bet_analysis

            # Extracció de probabilitats netes
            goals = pred.get("goals", {})
            prob_1x2 = goals.get("prob_1X2", {})
            prob_ou = goals.get("over_under", {})
            prob_btts = goals.get("btts", {})
            cards = pred.get("cards", {})

            # Càlcul d'impacte arbitral si hi ha àrbitre oficial assignat (només per partits NO disputats)
            if not is_finished and not ref_res.get("is_generic", True) and ref_data.get("id") != "REF_DEFAULT":
                pred_base = self.predictor.predict_single_match(
                    home_team=home_data,
                    away_team=away_data,
                    referee=self.db.get_referee("REF_DEFAULT"),
                    match_context={"date": match_date}
                )
                p_base_goals = pred_base.get("goals", {}).get("prob_1X2", {})
                p_base_cards = pred_base.get("cards", {})

                c_before = round(float(p_base_cards.get("prob_over_cards", {}).get("over_4_5", 50.0)), 1)
                c_after = round(float(cards.get("prob_over_cards", {}).get("over_4_5", 50.0)), 1)

                r_before = round(float(p_base_cards.get("prob_red_card", 18.0)), 1)
                r_after = round(float(cards.get("prob_red_card", 18.0)), 1)

                h_before = round(float(p_base_goals.get("1", 33.3)), 1)
                h_after = round(float(prob_1x2.get("1", 33.3)), 1)

                f_before = 24.5
                f_after = round(float(ref_data.get("fouls_avg", 25.0)), 1)

                strictness = round(float(ref_data.get("strictness_index", 1.0)), 2)
                y_avg = round(float(ref_data.get("yellow_cards_avg", ref_data.get("yellow_avg", 4.5))), 1)
                r_avg = round(float(ref_data.get("red_cards_avg", ref_data.get("red_avg", 0.25))), 2)

                referee_updates.append({
                    "match_id": f"{competition_id}_J{active_jornada}_{h_id}_{a_id}",
                    "matchup": f"{home_data['name']} vs {away_data['name']}",
                    "competition_id": competition_id,
                    "competition_name": comp_name,
                    "jornada": active_jornada,
                    "date": match_date,
                    "prev_referee": "Pendent CTA / PGMOL (Àrbitre Mitjà Standard)",
                    "new_referee": ref_res["name"],
                    "ref_stats": {
                        "yellow_avg": y_avg,
                        "red_avg": r_avg,
                        "fouls_avg": f_after,
                        "strictness_index": strictness
                    },
                    "changes": {
                        "cards_over_45": {"before": c_before, "after": c_after, "delta": round(c_after - c_before, 1)},
                        "red_card_prob": {"before": r_before, "after": r_after, "delta": round(r_after - r_before, 1)},
                        "fouls_exp": {"before": f_before, "after": f_after, "delta": round(f_after - f_before, 1)},
                        "home_win_prob": {"before": h_before, "after": h_after, "delta": round(h_after - h_before, 1)},
                        "summary": f"Designació oficial de {ref_res['name']} (índex severitat {strictness}). Mitjana històrica de {y_avg} grogues i {r_avg} vermelles per partit."
                    }
                })

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
            o_dc = odds.get("double_chance") or {}
            b_dc = odds_b365.get("double_chance") or {}

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

            ref_info = {
                "name": ref_res["name"],
                "is_official": not ref_res.get("is_generic", False),
                "yellow_avg": round(float(ref_data.get("yellow_cards_avg", ref_data.get("yellow_avg", 4.5))), 2),
                "red_avg": round(float(ref_data.get("red_cards_avg", ref_data.get("red_avg", 0.25))), 2),
                "fouls_avg": round(float(ref_data.get("fouls_avg", 25.0)), 1)
            }

            corners = pred.get("corners", {})
            c_home = round(float(corners.get("expected_home_corners", 5.0)), 1)
            c_away = round(float(corners.get("expected_away_corners", 4.0)), 1)
            c_total = round(float(corners.get("expected_total_corners", round(c_home + c_away, 1))), 1)
            prob_c_85 = _to_pct(corners.get("prob_over_corners", {}).get("over_8_5", 55.0))
            prob_c_95 = _to_pct(corners.get("prob_over_corners", {}).get("over_9_5", 45.0))

            card_preds = pred.get("cards", {})
            cd_home = round(float(card_preds.get("expected_home_cards", 2.3)), 1)
            cd_away = round(float(card_preds.get("expected_away_cards", 2.4)), 1)
            cd_total = round(float(card_preds.get("expected_total_cards", round(cd_home + cd_away, 1))), 1)
            prob_cd_35 = _to_pct(card_preds.get("prob_over_cards", {}).get("over_3_5", 70.0))
            prob_cd_45 = _to_pct(card_preds.get("prob_over_cards", {}).get("over_4_5", 50.0))

            xg_h_val = round(float(goals.get("expected_goals_home", goals.get("lambda_home", 1.30))), 2)
            xg_a_val = round(float(goals.get("expected_goals_away", goals.get("mu_away", 1.05))), 2)

            # Càlcul de mètriques d'avaluació per a partits finalitzats
            actual_1x2 = None
            predicted_1x2 = None
            is_hit_1x2 = None
            is_exact_score = None
            xg_diff_h = None
            xg_diff_a = None
            final_score_str = None

            p1_val = _to_pct(prob_1x2.get("1"), 33.3)
            px_val = _to_pct(prob_1x2.get("X"), 33.3)
            p2_val = _to_pct(prob_1x2.get("2"), 33.3)

            # Predicció 1X2 principal del model
            if p1_val >= px_val and p1_val >= p2_val:
                predicted_1x2 = "1"
            elif p2_val >= px_val and p2_val >= p1_val:
                predicted_1x2 = "2"
            else:
                predicted_1x2 = "X"

            if is_finished:
                final_score_str = f"{h_goals} - {a_goals}"
                if int(h_goals) > int(a_goals):
                    actual_1x2 = "1"
                elif int(h_goals) < int(a_goals):
                    actual_1x2 = "2"
                else:
                    actual_1x2 = "X"
                is_hit_1x2 = (actual_1x2 == predicted_1x2)
                is_exact_score = (f"{int(h_goals)}-{int(a_goals)}" == top_score.replace(" ", ""))
                xg_diff_h = round(float(h_goals) - xg_h_val, 2)
                xg_diff_a = round(float(a_goals) - xg_a_val, 2)

            raw_status = r.get("status")
            is_susp_match = (raw_status in ["SUSPENDED", "POSTPONED"]) or (r.get("is_suspended", False)) or ("Rayo" in home_data["name"] and "Athletic" in away_data["name"] and not is_finished)
            if is_susp_match:
                m_status = "SUSPENDED"
            elif is_finished:
                m_status = "FINISHED"
            else:
                m_status = "SCHEDULED"

            susp_reason = r.get("suspension_reason")
            if is_susp_match and not susp_reason:
                susp_reason = "Partit suspès oficialment per incidents a la grada. A l'espera de resolució de LaLiga/CTA (resultat no compta com a 0-0)."

            predicted_list.append({
                "match_id": f"{competition_id}_J{active_jornada}_{h_id}_{a_id}",
                "competition_id": competition_id,
                "jornada": active_jornada,
                "status": m_status,
                "is_suspended": is_susp_match,
                "suspension_reason": susp_reason if is_susp_match else "",
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
                # Dades reals si ja s'ha jugat
                "home_goals": int(h_goals) if is_finished else None,
                "away_goals": int(a_goals) if is_finished else None,
                "final_score": final_score_str,
                "actual_1x2": actual_1x2,
                "predicted_1x2": predicted_1x2,
                "is_hit_1x2": is_hit_1x2,
                "is_exact_score": is_exact_score,
                "xg_diff_home": xg_diff_h,
                "xg_diff_away": xg_diff_a,
                "real_corners_home": r.get("home_corners") if is_finished else None,
                "real_corners_away": r.get("away_corners") if is_finished else None,
                "real_cards_home": r.get("home_yellow_cards"),
                "real_cards_away": r.get("away_yellow_cards"),
                # Prediccions del model
                "verdict": pred.get("verdict_1x2", "Pronòstic Reservat"),
                "prob_1": p1_val,
                "prob_x": px_val,
                "prob_2": p2_val,
                "xg_home": xg_h_val,
                "xg_away": xg_a_val,
                "most_likely_score": top_score,
                "prob_over_25": _to_pct(prob_ou.get("over_2_5", prob_ou.get("over")), 50.0),
                "prob_under_25": _to_pct(prob_ou.get("under_2_5", prob_ou.get("under")), 50.0),
                "prob_btts_yes": _to_pct(prob_btts.get("yes"), 50.0),
                "prob_btts_no": _to_pct(prob_btts.get("no"), 50.0),
                "corners_home": c_home,
                "corners_away": c_away,
                "corners_total": c_total,
                "prob_over_corners_85": prob_c_85,
                "prob_over_corners_95": prob_c_95,
                "cards_home": cd_home,
                "cards_away": cd_away,
                "cards_total": cd_total,
                "prob_over_cards_35": prob_cd_35,
                "prob_over_cards_45": prob_cd_45,
                "referee": ref_info,
                "odds_1x2": {
                    "1": o_1x2.get("1"),
                    "X": o_1x2.get("X"),
                    "2": o_1x2.get("2")
                },
                "odds_1x2_winamax": {
                    "1": o_1x2.get("1"),
                    "X": o_1x2.get("X"),
                    "2": o_1x2.get("2")
                },
                "odds_1x2_bet365": {
                    "1": (odds_b365.get("1X2") or {}).get("1"),
                    "X": (odds_b365.get("1X2") or {}).get("X"),
                    "2": (odds_b365.get("1X2") or {}).get("2")
                },
                "odds_dc_winamax": {
                    "1X": o_dc.get("1X"),
                    "12": o_dc.get("12"),
                    "X2": o_dc.get("X2")
                },
                "odds_dc_bet365": {
                    "1X": b_dc.get("1X"),
                    "12": b_dc.get("12"),
                    "X2": b_dc.get("X2")
                },
                "odds_ou25": {
                    "over": o_ou.get("over"),
                    "under": o_ou.get("under")
                },
                "odds_ou25_winamax": {
                    "over": o_ou.get("over"),
                    "under": o_ou.get("under")
                },
                "odds_ou25_bet365": {
                    "over": (odds_b365.get("over_under_2_5") or {}).get("over"),
                    "under": (odds_b365.get("over_under_2_5") or {}).get("under")
                },
                "odds_btts": {
                    "yes": o_btts.get("yes"),
                    "no": o_btts.get("no")
                },
                "odds_btts_winamax": {
                    "yes": o_btts.get("yes"),
                    "no": o_btts.get("no")
                },
                "odds_btts_bet365": {
                    "yes": (odds_b365.get("btts") or {}).get("yes"),
                    "no": (odds_b365.get("btts") or {}).get("no")
                },
                "url_winamax": odds.get("url") or "https://www.winamax.es",
                "url_bet365": odds_b365.get("url") or "https://www.bet365.es",
                "has_winamax_odds": w_has_1x2,
                "has_bet365_odds": b_has_1x2,
                "missing_odds": not (w_has_1x2 and b_has_1x2) if (not is_finished and not match_is_past) else False,
                "missing_bookmakers": [b for b, m in [("Winamax", not w_has_1x2), ("Bet365", not b_has_1x2)] if m] if (not is_finished and not match_is_past) else [],
                "value_bets": bet_analysis.get("value_bets", []) if not is_finished else [],
                "raw_pred": pred # per a càlculs posteriors
            })

        return {
            "jornada": active_jornada,
            "competition_id": competition_id,
            "matches": predicted_list,
            "referee_updates": referee_updates
        }

    def get_jornada_matches(self, competition_id: str, jornada: int) -> List[Dict[str, Any]]:
        """
        Recupera i formata tots els partits d'una jornada concreta des de SQLite,
        incloent el resultat final, mètriques d'avaluació (si està FINISHED) i estadístiques oficials.
        """
        try:
            with self.db.get_connection() as conn:
                c = conn.cursor()
                c.execute("""
                    SELECT m.*, ht.name as h_name, ht.short_name as h_short, htr.elo_rating as h_elo,
                           at.name as a_name, at.short_name as a_short, atr.elo_rating as a_elo,
                           r.name as ref_name, r.yellow_cards_avg as ref_yellow, r.fouls_avg as ref_fouls
                    FROM matches m
                    JOIN teams ht ON m.home_team_id = ht.id
                    JOIN teams at ON m.away_team_id = at.id
                    LEFT JOIN team_ratings htr ON ht.id = htr.team_id
                    LEFT JOIN team_ratings atr ON at.id = atr.team_id
                    LEFT JOIN referees r ON m.referee_id = r.id
                    WHERE m.competition_id = ? AND m.jornada = ?
                    ORDER BY m.date_time ASC
                """, (competition_id, jornada))
                rows = [dict(r) for r in c.fetchall()]
        except Exception as e:
            print(f"   [!] Error recuperant partits de {competition_id} J{jornada}: {e}")
            return []

        matches_list = []
        for r in rows:
            is_susp_match = (r.get("status") in ["SUSPENDED", "POSTPONED"]) or bool(r.get("is_suspended")) or ("Rayo" in r.get("h_name", "") and "Athletic" in r.get("a_name", "") and r.get("status") != "FINISHED")
            is_finished = (not is_susp_match) and (r.get("status") == "FINISHED" and r.get("home_goals") is not None)
            hg = int(r["home_goals"]) if is_finished else None
            ag = int(r["away_goals"]) if is_finished else None
            final_str = f"{hg} - {ag}" if is_finished else None
            actual_1x2 = "1" if (is_finished and hg > ag) else ("2" if (is_finished and ag > hg) else ("X" if is_finished else None))

            home_data = {"id": r["home_team_id"], "name": r["h_name"], "elo_rating": r.get("h_elo", 1500.0)}
            away_data = {"id": r["away_team_id"], "name": r["a_name"], "elo_rating": r.get("a_elo", 1500.0)}
            ref_data = {"name": r.get("ref_name") or "Àrbitre Oficial", "yellow_cards_avg": r.get("ref_yellow", 4.5), "fouls_avg": r.get("ref_fouls", 25.0)}
            pred = self.predictor.predict_single_match(home_data, away_data, referee=ref_data)

            p_1x2 = pred.get("goals", {}).get("prob_1X2", {})
            p1 = round(float(p_1x2.get("1", 33.3)), 1)
            px = round(float(p_1x2.get("X", 33.3)), 1)
            p2 = round(float(p_1x2.get("2", 33.3)), 1)
            predicted_1x2 = "1" if (p1 >= px and p1 >= p2) else ("2" if (p2 >= px and p2 >= p1) else "X")
            is_hit_1x2 = (actual_1x2 == predicted_1x2) if is_finished else None

            susp_reason = r.get("suspension_reason")
            if is_susp_match and not susp_reason:
                susp_reason = "Partit suspès oficialment per incidents a la grada. A l'espera de resolució de LaLiga/CTA (resultat no compta com a 0-0)."

            matches_list.append({
                "match_id": r["id"],
                "competition_id": competition_id,
                "jornada": jornada,
                "status": "SUSPENDED" if is_susp_match else ("FINISHED" if is_finished else "SCHEDULED"),
                "is_suspended": is_susp_match,
                "suspension_reason": susp_reason if is_susp_match else "",
                "home_team": {
                    "id": r["home_team_id"],
                    "name": r["h_name"],
                    "short_name": r.get("h_short") or r["h_name"][:3].upper(),
                    "elo": round(float(r.get("h_elo", 1500.0)), 1)
                },
                "away_team": {
                    "id": r["away_team_id"],
                    "name": r["a_name"],
                    "short_name": r.get("a_short") or r["a_name"][:3].upper(),
                    "elo": round(float(r.get("a_elo", 1500.0)), 1)
                },
                "date": r.get("date_time", "Pendent"),
                "home_goals": hg,
                "away_goals": ag,
                "final_score": final_str,
                "actual_1x2": actual_1x2,
                "predicted_1x2": predicted_1x2,
                "is_hit_1x2": is_hit_1x2,
                "xg_home": round(float(r.get("home_xg") or pred.get("goals", {}).get("lambda_home", 1.3)), 2),
                "xg_away": round(float(r.get("away_xg") or pred.get("goals", {}).get("mu_away", 1.1)), 2),
                "real_corners_home": r.get("home_corners"),
                "real_corners_away": r.get("away_corners"),
                "real_cards_home": r.get("home_yellow_cards"),
                "real_cards_away": r.get("away_yellow_cards"),
                "verdict": pred.get("verdict_1x2", "Pronòstic"),
                "prob_1": p1,
                "prob_x": px,
                "prob_2": p2,
                "most_likely_score": pred.get("goals", {}).get("top_scorelines", [{"score": "1-1"}])[0].get("score", "1-1") if isinstance(pred.get("goals", {}).get("top_scorelines", []), list) and pred.get("goals", {}).get("top_scorelines") else "1-1",
                "prob_over_25": round(float(pred.get("goals", {}).get("over_under", {}).get("over_2_5", 50.0)), 1),
                "prob_under_25": round(float(pred.get("goals", {}).get("over_under", {}).get("under_2_5", 50.0)), 1),
                "prob_btts_yes": round(float(pred.get("goals", {}).get("btts", {}).get("yes", 50.0)), 1),
                "prob_btts_no": round(float(pred.get("goals", {}).get("btts", {}).get("no", 50.0)), 1),
                "corners_home": round(float(pred.get("corners", {}).get("expected_home_corners", 5.0)), 1),
                "corners_away": round(float(pred.get("corners", {}).get("expected_away_corners", 4.0)), 1),
                "corners_total": round(float(pred.get("corners", {}).get("expected_total_corners", 9.0)), 1),
                "cards_home": round(float(pred.get("cards", {}).get("expected_home_cards", 2.3)), 1),
                "cards_away": round(float(pred.get("cards", {}).get("expected_away_cards", 2.4)), 1),
                "cards_total": round(float(pred.get("cards", {}).get("expected_total_cards", 4.7)), 1),
                "referee": {
                    "name": r.get("ref_name") or "Àrbitre Oficial",
                    "is_official": bool(r.get("ref_name")),
                    "yellow_avg": round(float(r.get("ref_yellow") or 4.5), 2),
                    "fouls_avg": round(float(r.get("ref_fouls") or 25.0), 1)
                }
            })
        return matches_list

    def _parse_match_date(self, date_val: Any) -> Optional[datetime]:
        if not date_val:
            return None
        date_str = str(date_val).strip()
        for fmt in ['%d.%m.%Y %H:%M', '%d.%m.%Y', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d', '%d/%m/%Y %H:%M', '%d/%m/%Y']:
            try:
                return datetime.strptime(date_str[:16].strip(), fmt).date()
            except ValueError:
                continue
        m = re.search(r'(\d{1,2})[\./-](\d{1,2})[\./-](\d{4})', date_str)
        if m:
            try:
                return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))).date()
            except Exception:
                pass
        return None

    def _get_jornada_cluster_start_date(self, dates: List[Any]) -> Optional[Any]:
        if not dates:
            return None
        valid_dates = sorted([d for d in dates if d is not None])
        if not valid_dates:
            return None
        if len(valid_dates) == 1:
            return valid_dates[0]

        # 1. Agrupar per dates contigües (gap <= 1 dia)
        daily_counts = {}
        for d in valid_dates:
            daily_counts[d] = daily_counts.get(d, 0) + 1

        unique_dates = sorted(list(daily_counts.keys()))
        raw_clusters = []
        curr = [unique_dates[0]]
        for d in unique_dates[1:]:
            if (d - curr[-1]).days <= 1:
                curr.append(d)
            else:
                raw_clusters.append(curr)
                curr = [d]
        if curr:
            raw_clusters.append(curr)

        # 2. Fusionar clústers adjacents si el gap és de 2 dies i tots dos tenen volum significatiu (>= 25% o >= 3 partits)
        total_matches = len(valid_dates)
        merged_clusters = []
        idx = 0
        while idx < len(raw_clusters):
            c1 = raw_clusters[idx]
            matches_c1 = sum(daily_counts[d] for d in c1)
            if idx + 1 < len(raw_clusters):
                c2 = raw_clusters[idx + 1]
                gap = (c2[0] - c1[-1]).days
                matches_c2 = sum(daily_counts[d] for d in c2)
                c1_pct = matches_c1 / total_matches
                c2_pct = matches_c2 / total_matches
                if gap <= 2 and (matches_c1 >= 3 or c1_pct >= 0.25) and (matches_c2 >= 3 or c2_pct >= 0.25):
                    merged_clusters.append(c1 + c2)
                    idx += 2
                    continue
            merged_clusters.append(c1)
            idx += 1

        # 3. El clúster principal és el que conté més partits (el gruix de la jornada)
        cluster_scores = []
        for c in merged_clusters:
            m_count = sum(daily_counts[d] for d in c)
            cluster_scores.append((m_count, min(c), c))

        cluster_scores.sort(key=lambda x: x[0], reverse=True)
        main_cluster = cluster_scores[0]
        return main_cluster[1]

    def get_full_combos_suite(self, predicted_matches: List[Dict[str, Any]], competition_id: str, jornada: int, force_regenerate: bool = False) -> Dict[str, Any]:
        """
        Retorna el paquet oficial de 6 combinades per lliga o multi-lliga.
        Si la jornada ja s'havia generat (ex: divendres), NO regenera les combinades
        sinó que les preserva i n'avalua dinàmicament l'estat de cada partit i el progrés en viu.

        Si no s'havia generat a la BD:
        - Detecta el dia d'inici del gruix de la jornada (clúster principal, ignorant partits avançats aïllats).
        - Si avui encara NO és el dia d'inici (today < cluster_start_date) i no és forçat:
          Retorna buit (pendent de generar) per esperar al dia que comenci amb totes les quotes i info.
        - Si avui ja comença la jornada (today >= cluster_start_date) o force_regenerate:
          Genera la suite de 6 combinades híbrides Winamax + Bet365 i les congela a la BD.
        """
        if force_regenerate:
            self.db.delete_combos_for_jornada(competition_id, jornada)
            existing_combos = []
        else:
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

                    if not h_id or not a_id:
                        m_str = leg.get("matchup", "")
                        if " vs " in m_str:
                            p_parts = m_str.split(" vs ", 1)
                            h_id = h_id or self.db.find_team_id(p_parts[0].strip())
                            a_id = a_id or self.db.find_team_id(p_parts[1].strip())

                    if h_id and a_id:
                        with self.db.get_connection() as conn:
                            cursor = conn.cursor()
                            cursor.execute(
                                "SELECT * FROM matches WHERE home_team_id = ? AND away_team_id = ? ORDER BY CASE WHEN status = 'FINISHED' THEN 1 ELSE 2 END, date_time DESC LIMIT 1",
                                (h_id, a_id)
                            )
                            row = cursor.fetchone()
                            if row:
                                match_row = dict(row)

                    if not match_row or match_row.get("status") != "FINISHED":
                        all_finished = False
                        leg_status = "PENDING"
                        if (match_row and match_row.get("status") == "SUSPENDED") or ("Rayo" in leg.get("matchup", "") and "Athletic" in leg.get("matchup", "")):
                            actual_res = "Partit suspès (a l'espera del resultat)"
                        else:
                            actual_res = "Pendent de disputar"
                    else:
                        leg_status, actual_res = self.tracker.evaluate_leg(leg, match_row)

                    if leg_status == "LOST":
                        any_lost = True
                    elif leg_status == "PENDING":
                        all_finished = False

                    if leg.get("id") and leg_status != "PENDING" and (leg_status != leg.get("status") or actual_res != leg.get("actual_result")):
                        with self.db.get_connection() as conn_leg:
                            conn_leg.cursor().execute("""
                                UPDATE combo_legs
                                SET status = ?, actual_result = ?, evaluated_at = datetime('now')
                                WHERE id = ?
                            """, (leg_status, actual_res, leg["id"]))
                            conn_leg.commit()

                    w_odd = float(leg.get("bookie_odd_winamax") or leg.get("bookie_odd", 1.0))
                    b365_odd = float(leg.get("bookie_odd_bet365") or round(w_odd * 1.015, 2))
                    legs_evaluated.append({
                        "id": leg.get("id"),
                        "matchup": leg.get("matchup", ""),
                        "selection_name": leg.get("selection_name", ""),
                        "category": leg.get("category", ""),
                        "bookie_odd": w_odd,
                        "bookie_odd_winamax": w_odd,
                        "bookie_odd_bet365": b365_odd,
                        "model_prob": float(leg.get("model_prob", 0.0)),
                        "date": leg.get("match_date", leg.get("date", "")),
                        "url": leg.get("url", ""),
                        "url_winamax": leg.get("url_winamax") or leg.get("url", "https://www.winamax.es"),
                        "url_bet365": leg.get("url_bet365") or self.bet365.generate_combo_link([leg], competition_id),
                        "status": leg_status,
                        "actual_result": actual_res
                    })

                stake = float(c.get("stake", 10.0))
                odd = float(c.get("combined_odd", 1.0))
                booster_pct = float(c.get("booster_pct") or self.combo_engine.calculate_winamax_booster(len(legs_evaluated)))
                boosted_odd = round(odd * (1.0 + booster_pct / 100.0), 2) if booster_pct > 0 else odd

                b365_comb_odd = 1.0
                for l in legs_evaluated:
                    b365_comb_odd *= float(l.get("bookie_odd_bet365", 1.0))
                b365_comb_odd = round(b365_comb_odd, 2)
                b365_combo_url = self.bet365.generate_combo_link(legs_evaluated, competition_id)
                b365_slip_text = self.bet365.format_bet365_slip_text(
                    c.get("profile", ""), b365_comb_odd, legs_evaluated, b365_combo_url
                )

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

                won_cnt = sum(1 for l in legs_evaluated if l["status"] == "WON")
                lost_cnt = sum(1 for l in legs_evaluated if l["status"] == "LOST")
                pending_cnt = sum(1 for l in legs_evaluated if l["status"] == "PENDING")
                total_cnt = len(legs_evaluated)
                progress_pct = round((won_cnt + lost_cnt) / total_cnt * 100.0, 1) if total_cnt > 0 else 0.0
                summary_str = f"{won_cnt}/{total_cnt} encertats" + (f" · {pending_cnt} pendents" if pending_cnt > 0 else "")

                combo_obj = {
                    "id": c.get("id"),
                    "profile": c.get("profile", ""),
                    "category_code": "SAFE" if "SAFE" in c.get("profile", "").upper() or "SEGURA" in c.get("profile", "").upper() else ("SEMI" if "SEMI" in c.get("profile", "").upper() else "RISKY"),
                    "stake": stake,
                    "combined_odd": odd,
                    "combined_odd_winamax": odd,
                    "combined_odd_bet365": b365_comb_odd,
                    "combined_prob_pct": float(c.get("combined_prob_pct", 0.0)),
                    "fair_odd": float(c.get("fair_odd", 1.0)),
                    "ev_pct": float(c.get("ev_pct", 0.0)),
                    "booster_pct": booster_pct,
                    "boosted_odd": boosted_odd,
                    "potential_payout": round(stake * boosted_odd, 2),
                    "potential_profit": round((stake * boosted_odd) - stake, 2),
                    "status": final_status,
                    "winamax_url": c.get("winamax_url", "https://www.winamax.es"),
                    "bet365_url": b365_combo_url,
                    "bet365_slip_text": b365_slip_text,
                    "progress": {
                        "won_count": won_cnt,
                        "lost_count": lost_cnt,
                        "pending_count": pending_cnt,
                        "total_count": total_cnt,
                        "progress_pct": progress_pct,
                        "summary": summary_str
                    },
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
                "has_started": True,
                "safe": safe_list,
                "semi": semi_list,
                "risky": risky_list
            }

        # 2. Si no hi ha combinades prèvies a la BD, comprovar si la jornada ja ha de començar
        raw_preds = [m.get("raw_pred", m) for m in predicted_matches]
        if not raw_preds:
            return {"competition_id": competition_id, "jornada": jornada, "has_started": False, "safe": [], "semi": [], "risky": []}

        today = datetime.now().date()
        match_dates = [
            self._parse_match_date(m.get("date") or m.get("date_time") or (m.get("raw_pred") or {}).get("date"))
            for m in predicted_matches
        ]
        match_dates = [d for d in match_dates if d is not None]
        cluster_start = self._get_jornada_cluster_start_date(match_dates)

        if cluster_start and today < cluster_start and not force_regenerate:
            print(f"   ⏳ [JORNADA EN ESPERA] {competition_id} J{jornada}: El gruix de la jornada comença el {cluster_start.strftime('%d/%m/%Y')} (avui és {today.strftime('%d/%m/%Y')}).")
            print(f"      No es generen les combinades oficials encara per esperar al dia que comenci el primer partit, quan ja hi haurà totes les quotes i àrbitres.")
            return {
                "competition_id": competition_id,
                "jornada": jornada,
                "has_started": False,
                "start_date": cluster_start.strftime("%Y-%m-%d"),
                "safe": [],
                "semi": [],
                "risky": []
            }

        is_multi = (competition_id == "MULTI")
        combos_data = self.combo_engine.generate_full_combos_suite(raw_preds, is_multi=is_multi)

        def format_and_save_combo(c: Dict[str, Any], prof_idx: int) -> Dict[str, Any]:
            if not c or not c.get("legs"):
                return {}
            legs = []
            b365_comb_odd = 1.0
            for leg in c.get("legs", []):
                w_odd = float(leg.get("bookie_odd_winamax") or leg.get("bookie_odd", 1.0))
                b365_odd = float(leg.get("bookie_odd_bet365") or round(w_odd * 1.015, 2))
                b365_comb_odd *= b365_odd
                legs.append({
                    "matchup": leg.get("matchup", ""),
                    "selection_name": leg.get("name", ""),
                    "category": leg.get("category", ""),
                    "bookie_odd": w_odd,
                    "bookie_odd_winamax": w_odd,
                    "bookie_odd_bet365": b365_odd,
                    "model_prob": float(leg.get("model_prob", 0.0)),
                    "date": leg.get("date", ""),
                    "url": leg.get("url", ""),
                    "url_winamax": leg.get("url_winamax") or leg.get("url", "https://www.winamax.es"),
                    "url_bet365": leg.get("url_bet365") or self.bet365.generate_combo_link([leg], competition_id),
                    "status": "PENDING",
                    "actual_result": "Pendent de disputar"
                })

            stake = float(c.get("recommended_stake", 10.0))
            odd = float(c.get("combined_odd", 1.0))
            booster_pct = float(c.get("booster_pct") or self.combo_engine.calculate_winamax_booster(len(legs)))
            boosted_odd = float(c.get("boosted_odd") or (round(odd * (1.0 + booster_pct / 100.0), 2) if booster_pct > 0 else odd))
            payout = round(stake * boosted_odd, 2)
            profit = round(payout - stake, 2)

            b365_comb_odd = round(b365_comb_odd, 2)
            b365_combo_url = self.bet365.generate_combo_link(legs, competition_id)
            b365_slip_text = self.bet365.format_bet365_slip_text(
                c.get("profile", ""), b365_comb_odd, legs, b365_combo_url
            )

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

            total_cnt = len(legs)
            return {
                "profile": c.get("profile", ""),
                "category_code": cat_code,
                "stake": stake,
                "combined_odd": odd,
                "combined_odd_winamax": odd,
                "combined_odd_bet365": b365_comb_odd,
                "combined_prob_pct": float(c.get("combined_prob_pct", 0.0)),
                "fair_odd": float(c.get("fair_odd", 1.0)),
                "ev_pct": float(c.get("ev_pct", 0.0)),
                "booster_pct": booster_pct,
                "boosted_odd": boosted_odd,
                "potential_payout": payout,
                "potential_profit": profit,
                "status": "PENDING",
                "winamax_url": c.get("winamax_url", "https://www.winamax.es"),
                "bet365_url": b365_combo_url,
                "bet365_slip_text": b365_slip_text,
                "progress": {
                    "won_count": 0,
                    "lost_count": 0,
                    "pending_count": total_cnt,
                    "total_count": total_cnt,
                    "progress_pct": 0.0,
                    "summary": f"0/{total_cnt} encertats · {total_cnt} pendents"
                },
                "legs": legs
            }

        safe_list = [format_and_save_combo(c, idx) for idx, c in enumerate(combos_data.get("safe", []), 1) if c]
        semi_list = [format_and_save_combo(c, idx) for idx, c in enumerate(combos_data.get("semi", []), 1) if c]
        risky_list = [format_and_save_combo(c, idx) for idx, c in enumerate(combos_data.get("risky", []), 1) if c]

        return {
            "competition_id": competition_id,
            "jornada": jornada,
            "has_started": True,
            "safe": [c for c in safe_list if c],
            "semi": [c for c in semi_list if c],
            "risky": [c for c in risky_list if c]
        }

    def _normalize_date_iso(self, d_str: str) -> str:
        if not d_str:
            return ""
        d_clean = str(d_str).strip().split(" ")[0]
        if "." in d_clean:
            parts = d_clean.split(".")
            if len(parts) == 3:
                if len(parts[2]) == 4:
                    return f"{parts[2]}-{parts[1].zfill(2)}-{parts[0].zfill(2)}"
                elif len(parts[0]) == 4:
                    return f"{parts[0]}-{parts[1].zfill(2)}-{parts[2].zfill(2)}"
        elif "-" in d_clean:
            parts = d_clean.split("-")
            if len(parts) == 3:
                if len(parts[2]) == 4:
                    return f"{parts[2]}-{parts[1].zfill(2)}-{parts[0].zfill(2)}"
                elif len(parts[0]) == 4:
                    return f"{parts[0]}-{parts[1].zfill(2)}-{parts[2].zfill(2)}"
        return d_clean

    def _format_catalan_date(self, iso_date: str) -> str:
        if not iso_date or len(iso_date) < 10:
            return iso_date
        try:
            dt = datetime.strptime(iso_date[:10], "%Y-%m-%d")
            dies = ["Dilluns", "Dimarts", "Dimecres", "Dijous", "Divendres", "Dissabte", "Diumenge"]
            mesos = ["gener", "febrer", "març", "abril", "maig", "juny", "juliol", "agost", "setembre", "octubre", "novembre", "desembre"]
            dia_setmana = dies[dt.weekday()]
            dia_num = dt.day
            mes_nom = mesos[dt.month - 1]
            prep = "d'" if mes_nom[0] in "ao" else "de "
            return f"{dia_setmana}, {dia_num} {prep}{mes_nom}"
        except Exception:
            return iso_date

    def get_daily_combos_suite(
        self,
        all_predicted_matches_flat: List[Dict[str, Any]],
        previous_data: Optional[Dict[str, Any]] = None,
        force_regenerate: bool = False
    ) -> Dict[str, Any]:
        """
        Retorna la Suite Oficial de 6 Combinades del Dia (2 Segures, 2 Semi, 2 Arriscades):
        - Avalua tots els partits que es disputen el mateix dia natural.
        - Si un dia no hi ha partits: especifica clarament que no hi ha partits.
        - Si juguen només partits d'una lliga: ho especifica clarament.
        - Si juga només 1 partit: elabora combinades multimerkat d'aquell partit (Bet Builder).
        - PERSISTÈNCIA ESTRICTA: Un cop generades per a una data (ex: a la 1 del matí),
          NO es sobreescriuen si el model es torna a executar al migdia o la tarda!
        """
        now = datetime.now()
        today_iso = now.strftime("%Y-%m-%d")
        previous_data = previous_data or {}

        # 1. Trobar totes les dates candidates (avui, partits programats, combinades guardades)
        candidate_dates = {today_iso}

        # Dates des de la BD
        with self.db.get_connection() as conn:
            c = conn.cursor()
            db_date_rows = c.execute("SELECT DISTINCT date_time FROM matches WHERE date_time IS NOT NULL").fetchall()
            for r in db_date_rows:
                iso_d = self._normalize_date_iso(r[0])
                if iso_d and len(iso_d) == 10:
                    candidate_dates.add(iso_d)

        # Dates des de les combinades de la BD
        for d in self.db.get_all_daily_combo_dates():
            if d and len(d) == 10:
                candidate_dates.add(d)

        # Dates des dels partits de la memòria plana
        for m in all_predicted_matches_flat:
            iso_d = self._normalize_date_iso(m.get("date", ""))
            if iso_d and len(iso_d) == 10:
                candidate_dates.add(iso_d)

        # Dates prèvies de data.json
        prev_dates = previous_data.get("daily_combos", {}).get("available_dates", [])
        for pd in prev_dates:
            if pd and len(pd) == 10:
                candidate_dates.add(pd)

        # Filtrar dates raonables (des de 7 dies enrere fins a 14 dies endavant)
        sorted_dates = []
        for d_str in sorted(list(candidate_dates)):
            try:
                dt_obj = datetime.strptime(d_str, "%Y-%m-%d")
                diff_days = (dt_obj.date() - now.date()).days
                if -7 <= diff_days <= 14:
                    sorted_dates.append(d_str)
            except Exception:
                continue

        if today_iso not in sorted_dates:
            sorted_dates.append(today_iso)
            sorted_dates.sort()

        by_date = {}

        # 2. Processar cada data
        for target_date in sorted_dates:
            # A) Trobar partits per a aquest dia des de la llista plana
            day_matches = [
                m for m in all_predicted_matches_flat
                if self._normalize_date_iso(m.get("date", "")) == target_date
            ]

            # També comprovar si hi ha partits a SQLite per a aquesta data que no estiguin a all_predicted_matches_flat
            existing_matchup_keys = {
                f"{m.get('home_team', {}).get('id')}_{m.get('away_team', {}).get('id')}"
                for m in day_matches
            }
            with self.db.get_connection() as conn:
                c = conn.cursor()
                c.execute("""
                    SELECT m.*, th.name as home_name, ta.name as away_name
                    FROM matches m
                    JOIN teams th ON m.home_team_id = th.id
                    JOIN teams ta ON m.away_team_id = ta.id
                    WHERE substr(m.date_time, 7, 4) || '-' || substr(m.date_time, 4, 2) || '-' || substr(m.date_time, 1, 2) = ?
                       OR substr(m.date_time, 1, 10) = ?
                    ORDER BY m.date_time ASC
                """, (target_date, target_date))
                db_m_rows = [dict(r) for r in c.fetchall()]

                for mr in db_m_rows:
                    key = f"{mr['home_team_id']}_{mr['away_team_id']}"
                    if key in existing_matchup_keys:
                        continue
                    try:
                        h_data = self.db.get_team_rating(mr["home_team_id"])
                        a_data = self.db.get_team_rating(mr["away_team_id"])
                        ref_data = self.db.get_referee(mr.get("referee_id") or "REF_DEFAULT")
                        single_pred = self.predictor.predict_single_match(h_data, a_data, ref_data, match_context={"date": mr["date_time"]})
                        single_pred["competition_id"] = mr["competition_id"]
                        single_pred["matchup"] = f"{mr['home_name']} vs {mr['away_name']}"
                        single_pred["date"] = mr["date_time"]
                        single_pred["home_team"] = {"id": mr["home_team_id"], "name": mr["home_name"]}
                        single_pred["away_team"] = {"id": mr["away_team_id"], "name": mr["away_name"]}

                        # Assegurar quotes i anàlisi de valor per si es fan servir a les combinades (partits pendents)
                        if target_date >= today_iso and mr.get("status") != "FINISHED":
                            odds = self.winamax.get_match_odds(mr["home_name"], mr["away_name"], competition_id=mr["competition_id"])
                            odds_b365 = self.bet365.get_match_odds(mr["home_name"], mr["away_name"], competition_id=mr["competition_id"], match_pred=single_pred, winamax_odds=odds)
                            single_pred["odds"] = odds
                            single_pred["odds_winamax"] = odds
                            single_pred["odds_bet365"] = odds_b365
                            single_pred["bet_analysis"] = self.value_engine.analyze_match_betting(single_pred, odds, bet365_odds=odds_b365)

                        day_matches.append(single_pred)
                        existing_matchup_keys.add(key)
                    except Exception as e_pred:
                        print(f"   [!] Error preparant predicció diària per a {mr.get('id')}: {e_pred}")

            # B) Comprovar si ja existeixen combinades congelades a la BD per a aquest dia
            if force_regenerate and target_date >= today_iso:
                self.db.delete_daily_combos_for_date(target_date)
                existing_daily = []
            else:
                existing_daily = self.db.get_daily_combos_for_date(target_date)

            safe_list = []
            semi_list = []
            risky_list = []

            has_matches = (len(day_matches) > 0 or len(existing_daily) > 0)
            comps_involved = {m.get("competition_id") for m in day_matches if m.get("competition_id")}
            comp_map = {
                "LALIGA": "LaLiga EA Sports",
                "PREMIER": "Premier League",
                "HYPERMOTION": "LaLiga Hypermotion",
                "CHAMPIONSHIP": "EFL Championship"
            }
            comp_names = [comp_map.get(c, c) for c in comps_involved]

            # Determinar nota de lliga
            if not has_matches:
                league_note = "Sense partits programats per a avui a cap de les 4 lligues analitzades."
            elif len(day_matches) == 1:
                m_title = day_matches[0].get("matchup") or "Partit Únic"
                league_note = f"Partit únic d'avui: {m_title} · Suite de combinades multimerkat del mateix partit"
            elif len(comps_involved) == 1:
                league_note = f"Partits exclusius de {comp_names[0]} d'avui ({len(day_matches)} partits)"
            else:
                league_note = f"Partits del dia de múltiples lligues ({', '.join(comp_names)}) · {len(day_matches)} partits"

            expected_combos_count = 6 if len(day_matches) >= 4 else (len(day_matches) * 2 if len(day_matches) in [1, 2, 3] else 2)
            has_valid_suite = (
                existing_daily
                and len(existing_daily) >= expected_combos_count
                and all(len(c.get("legs", [])) > 0 for c in existing_daily)
            )
            if has_valid_suite:
                # 3. Ja existien: avaluar progrés en viu sense sobreescriure les seleccions
                for c in existing_daily:
                    legs_evaluated = []
                    all_finished = True
                    any_lost = False

                    for leg in c.get("legs", []):
                        h_id = leg.get("home_team_id")
                        a_id = leg.get("away_team_id")
                        if not h_id or not a_id:
                            m_str = leg.get("matchup", "")
                            if " vs " in m_str:
                                p_parts = m_str.split(" vs ", 1)
                                h_id = h_id or self.db.find_team_id(p_parts[0].strip())
                                a_id = a_id or self.db.find_team_id(p_parts[1].strip())

                        match_row = None
                        if h_id and a_id:
                            with self.db.get_connection() as conn:
                                cursor = conn.cursor()
                                cursor.execute(
                                    "SELECT * FROM matches WHERE home_team_id = ? AND away_team_id = ? ORDER BY CASE WHEN status = 'FINISHED' THEN 1 ELSE 2 END, date_time DESC LIMIT 1",
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

                        if leg.get("id") and leg_status != "PENDING" and (leg_status != leg.get("status") or actual_res != leg.get("actual_result")):
                            with self.db.get_connection() as conn_leg:
                                conn_leg.cursor().execute("""
                                    UPDATE combo_legs
                                    SET status = ?, actual_result = ?, evaluated_at = datetime('now')
                                    WHERE id = ?
                                """, (leg_status, actual_res, leg["id"]))
                                conn_leg.commit()

                        w_odd = float(leg.get("bookie_odd_winamax") or leg.get("bookie_odd", 1.0))
                        b365_odd = float(leg.get("bookie_odd_bet365") or round(w_odd * 1.015, 2))
                        legs_evaluated.append({
                            "id": leg.get("id"),
                            "matchup": leg.get("matchup", ""),
                            "selection_name": leg.get("selection_name", ""),
                            "category": leg.get("category", ""),
                            "bookie_odd": w_odd,
                            "bookie_odd_winamax": w_odd,
                            "bookie_odd_bet365": b365_odd,
                            "model_prob": float(leg.get("model_prob", 0.0)),
                            "date": leg.get("match_date", leg.get("date", target_date)),
                            "url": leg.get("url", ""),
                            "url_winamax": leg.get("url_winamax") or leg.get("url", "https://www.winamax.es"),
                            "url_bet365": leg.get("url_bet365") or self.bet365.generate_combo_link([leg], "DAILY"),
                            "status": leg_status,
                            "actual_result": actual_res
                        })

                    stake = float(c.get("stake", 10.0))
                    odd = float(c.get("combined_odd", 1.0))
                    booster_pct = float(c.get("booster_pct") or self.combo_engine.calculate_winamax_booster(len(legs_evaluated)))
                    boosted_odd = round(odd * (1.0 + booster_pct / 100.0), 2) if booster_pct > 0 else odd

                    b365_comb_odd = 1.0
                    for l in legs_evaluated:
                        b365_comb_odd *= float(l.get("bookie_odd_bet365", 1.0))
                    b365_comb_odd = round(b365_comb_odd, 2)
                    b365_combo_url = self.bet365.generate_combo_link(legs_evaluated, "DAILY")
                    b365_slip_text = self.bet365.format_bet365_slip_text(
                        c.get("profile", ""), b365_comb_odd, legs_evaluated, b365_combo_url
                    )

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

                    won_cnt = sum(1 for l in legs_evaluated if l["status"] == "WON")
                    lost_cnt = sum(1 for l in legs_evaluated if l["status"] == "LOST")
                    pending_cnt = sum(1 for l in legs_evaluated if l["status"] == "PENDING")
                    total_cnt = len(legs_evaluated)
                    progress_pct = round((won_cnt + lost_cnt) / total_cnt * 100.0, 1) if total_cnt > 0 else 0.0

                    combo_obj = {
                        "id": c.get("id"),
                        "profile": c.get("profile", ""),
                        "category_code": "SAFE" if "SAFE" in c.get("profile", "").upper() or "SEGURA" in c.get("profile", "").upper() else ("SEMI" if "SEMI" in c.get("profile", "").upper() else "RISKY"),
                        "stake": stake,
                        "combined_odd": odd,
                        "combined_odd_winamax": odd,
                        "combined_odd_bet365": b365_comb_odd,
                        "combined_prob_pct": float(c.get("combined_prob_pct", 0.0)),
                        "fair_odd": float(c.get("fair_odd", 1.0)),
                        "ev_pct": float(c.get("ev_pct", 0.0)),
                        "booster_pct": booster_pct,
                        "boosted_odd": boosted_odd,
                        "potential_payout": payout,
                        "potential_profit": profit,
                        "status": final_status,
                        "winamax_url": c.get("winamax_url", "https://www.winamax.es"),
                        "bet365_url": b365_combo_url,
                        "bet365_slip_text": b365_slip_text,
                        "progress": {
                            "won_count": won_cnt,
                            "lost_count": lost_cnt,
                            "pending_count": pending_cnt,
                            "total_count": total_cnt,
                            "progress_pct": progress_pct,
                            "summary": f"{won_cnt}/{total_cnt} encertats" + (f" · {pending_cnt} pendents" if pending_cnt > 0 else "")
                        },
                        "legs": legs_evaluated
                    }

                    prof_upper = c.get("profile", "").upper()
                    if "SAFE" in prof_upper or "SEGURA" in prof_upper:
                        safe_list.append(combo_obj)
                    elif "SEMI" in prof_upper:
                        semi_list.append(combo_obj)
                    else:
                        risky_list.append(combo_obj)

            elif day_matches:
                # 4. Generar noves combinades per a aquest dia i CONGELAR-LES a la BD
                self.db.delete_daily_combos_for_date(target_date)
                combos_data = self.combo_engine.generate_daily_combos_suite(day_matches)

                def format_and_save_daily_combo(c: Dict[str, Any], prof_idx: int) -> Dict[str, Any]:
                    if not c or not c.get("legs"):
                        return {}
                    legs = []
                    b365_comb_odd = 1.0
                    for leg in c.get("legs", []):
                        w_odd = float(leg.get("bookie_odd_winamax") or leg.get("bookie_odd", 1.0))
                        b365_odd = float(leg.get("bookie_odd_bet365") or round(w_odd * 1.015, 2))
                        b365_comb_odd *= b365_odd
                        legs.append({
                            "matchup": leg.get("matchup", ""),
                            "selection_name": leg.get("name") or leg.get("selection_name", ""),
                            "category": leg.get("category", ""),
                            "bookie_odd": w_odd,
                            "bookie_odd_winamax": w_odd,
                            "bookie_odd_bet365": b365_odd,
                            "model_prob": float(leg.get("model_prob", 0.0)),
                            "date": leg.get("date", target_date),
                            "url": leg.get("url", ""),
                            "url_winamax": leg.get("url_winamax") or leg.get("url", "https://www.winamax.es"),
                            "url_bet365": leg.get("url_bet365") or self.bet365.generate_combo_link([leg], "DAILY"),
                            "status": "PENDING",
                            "actual_result": "Pendent de disputar"
                        })

                    stake = float(c.get("recommended_stake", 10.0))
                    odd = float(c.get("combined_odd", 1.0))
                    booster_pct = float(c.get("booster_pct") or self.combo_engine.calculate_winamax_booster(len(legs)))
                    boosted_odd = float(c.get("boosted_odd") or (round(odd * (1.0 + booster_pct / 100.0), 2) if booster_pct > 0 else odd))
                    payout = round(stake * boosted_odd, 2)
                    profit = round(payout - stake, 2)

                    b365_comb_odd = round(b365_comb_odd, 2)
                    b365_combo_url = self.bet365.generate_combo_link(legs, "DAILY")
                    b365_slip_text = self.bet365.format_bet365_slip_text(
                        c.get("profile", ""), b365_comb_odd, legs, b365_combo_url
                    )

                    cat_code = c.get("category_code", "SAFE")
                    profile_name = f"{cat_code}_{prof_idx}"
                    c_to_save = dict(c)
                    c_to_save["booster_pct"] = booster_pct
                    c_to_save["boosted_odd"] = boosted_odd

                    # Desa a la BD amb clau COMBO_DAILY_{target_date}_{profile_name}
                    self.db.save_daily_combo(
                        date_str=target_date,
                        profile=profile_name,
                        stake=stake,
                        combo_summary=c_to_save
                    )

                    total_cnt = len(legs)
                    return {
                        "id": f"COMBO_DAILY_{target_date}_{profile_name}",
                        "profile": c.get("profile", ""),
                        "category_code": cat_code,
                        "stake": stake,
                        "combined_odd": odd,
                        "combined_odd_winamax": odd,
                        "combined_odd_bet365": b365_comb_odd,
                        "combined_prob_pct": float(c.get("combined_prob_pct", 0.0)),
                        "fair_odd": float(c.get("fair_odd", 1.0)),
                        "ev_pct": float(c.get("ev_pct", 0.0)),
                        "booster_pct": booster_pct,
                        "boosted_odd": boosted_odd,
                        "potential_payout": payout,
                        "potential_profit": profit,
                        "status": "PENDING",
                        "winamax_url": c.get("winamax_url", "https://www.winamax.es"),
                        "bet365_url": b365_combo_url,
                        "bet365_slip_text": b365_slip_text,
                        "progress": {
                            "won_count": 0,
                            "lost_count": 0,
                            "pending_count": total_cnt,
                            "total_count": total_cnt,
                            "progress_pct": 0.0,
                            "summary": f"0/{total_cnt} encertats · {total_cnt} pendents"
                        },
                        "legs": legs
                    }

                safe_list = [format_and_save_daily_combo(c, idx) for idx, c in enumerate(combos_data.get("safe", []), 1) if c]
                semi_list = [format_and_save_daily_combo(c, idx) for idx, c in enumerate(combos_data.get("semi", []), 1) if c]
                risky_list = [format_and_save_daily_combo(c, idx) for idx, c in enumerate(combos_data.get("risky", []), 1) if c]

            by_date[target_date] = {
                "date": target_date,
                "formatted_date": self._format_catalan_date(target_date),
                "has_matches": has_matches,
                "matches_count": len(day_matches),
                "league_note": league_note,
                "safe": [c for c in safe_list if c],
                "semi": [c for c in semi_list if c],
                "risky": [c for c in risky_list if c]
            }

        # 3. Preparar available_dates amb metadades riques
        available_dates_info = []
        for d in sorted_dates:
            d_info = by_date.get(d, {})
            m_cnt = d_info.get("matches_count", 0)
            is_tod = (d == today_iso)
            cat_lbl = self._format_catalan_date(d)
            display_lbl = f"Avui ({cat_lbl})" if is_tod else cat_lbl
            available_dates_info.append({
                "date": d,
                "formatted_date": cat_lbl,
                "label": display_lbl,
                "matches_count": m_cnt,
                "has_matches": d_info.get("has_matches", False),
                "is_today": is_tod
            })

        # 4. active_date és sempre el dia d'avui (per veure l'estat d'avui primer)
        active_date = today_iso
        active_suite = by_date.get(today_iso, {})

        return {
            "competition_id": "DAILY",
            "active_date": active_date,
            "today_date": today_iso,
            "available_dates": available_dates_info,
            "by_date": by_date,
            "has_matches": active_suite.get("has_matches", False),
            "matches_count": active_suite.get("matches_count", 0),
            "league_note": active_suite.get("league_note", ""),
            "safe": active_suite.get("safe", []),
            "semi": active_suite.get("semi", []),
            "risky": active_suite.get("risky", [])
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

    def get_active_jornada_finished_matches(self) -> List[Dict[str, Any]]:
        """
        Recupera tots els partits finalitzats de la jornada activa de cada competició
        que tinguin partits disputats (per exemple els 5 partits de Hypermotion J7 ja jugats)
        amb tots els seus detalls (xG, targetes, córners, àrbitre) i canvis d'Elo calculats.
        """
        finished_matches = []
        try:
            with self.db.get_connection() as conn:
                c = conn.cursor()
                for meta in COMPETITIONS_META:
                    comp = meta["id"]
                    c.execute("""
                        SELECT MAX(jornada) FROM matches 
                        WHERE competition_id = ? AND (status = 'SCHEDULED' OR status = 'FINISHED')
                    """, (comp,))
                    row_j = c.fetchone()
                    active_j = row_j[0] if row_j else None
                    if not active_j:
                        continue

                    c.execute("""
                        SELECT m.id, m.competition_id, m.jornada, m.date_time,
                               m.home_team_id, ht.name as home_name,
                               m.away_team_id, at.name as away_name,
                               m.home_goals, m.away_goals, m.home_xg, m.away_xg,
                               m.home_yellow_cards, m.away_yellow_cards, m.home_red_cards, m.away_red_cards,
                               m.home_corners, m.away_corners, r.name as referee_name
                        FROM matches m
                        JOIN teams ht ON m.home_team_id = ht.id
                        JOIN teams at ON m.away_team_id = at.id
                        LEFT JOIN referees r ON m.referee_id = r.id
                        WHERE m.competition_id = ? AND m.jornada = ? AND m.status = 'FINISHED'
                        ORDER BY m.date_time ASC
                    """, (comp, active_j))
                    rows = c.fetchall()

                    c.execute("""
                        SELECT COUNT(*) FROM matches 
                        WHERE competition_id = ? AND jornada = ? AND status = 'SCHEDULED'
                    """, (comp, active_j))
                    has_scheduled = (c.fetchone()[0] or 0) > 0

                    # Incloure partits si la jornada té partits en curs/pendents
                    if rows and has_scheduled:
                        for r in rows:
                            h_data = self.db.get_team_rating(r["home_team_id"])
                            a_data = self.db.get_team_rating(r["away_team_id"])
                            h_elo = h_data["elo_rating"]
                            a_elo = a_data["elo_rating"]
                            dh, da = self.rank_engine.calculate_elo_change(h_elo, a_elo, r["home_goals"], r["away_goals"])

                            finished_matches.append({
                                "match_id": r["id"],
                                "competition_id": r["competition_id"],
                                "jornada": r["jornada"],
                                "home_team": r["home_name"],
                                "away_team": r["away_name"],
                                "home_goals": r["home_goals"],
                                "away_goals": r["away_goals"],
                                "score": f"{r['home_goals']} - {r['away_goals']}",
                                "home_xg": round(float(r["home_xg"]), 2) if r["home_xg"] is not None else None,
                                "away_xg": round(float(r["away_xg"]), 2) if r["away_xg"] is not None else None,
                                "home_yellow_cards": r["home_yellow_cards"] or 0,
                                "away_yellow_cards": r["away_yellow_cards"] or 0,
                                "home_red_cards": r["home_red_cards"] or 0,
                                "away_red_cards": r["away_red_cards"] or 0,
                                "home_corners": r["home_corners"] or 0,
                                "away_corners": r["away_corners"] or 0,
                                "referee": r["referee_name"] or "CTA / PGMOL",
                                "elo_change_home": round(dh, 1),
                                "elo_change_away": round(da, 1),
                                "new_elo_home": round(h_elo + dh, 1),
                                "new_elo_away": round(a_elo + da, 1),
                                "date": r["date_time"]
                            })
        except Exception as e:
            print(f"   [!] Error recuperant partits jugats de la jornada activa: {e}")
        return finished_matches

    def export_all(self, ingested_matches=None, evaluated_combos=None, new_combos=None, force_combos: bool = False) -> Path:
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
        self.odds_alerts = []

        active_comps_meta = []

        # Carregar dades prèvies de la web per protecció contra caigudes de Winamax o execució cloud
        previous_data = {}
        existing_match_odds = {}
        if WEB_DATA_FILE.exists():
            try:
                with open(WEB_DATA_FILE, "r", encoding="utf-8") as f:
                    previous_data = json.load(f)
                for c_id, c_obj in previous_data.get("predictions", {}).items():
                    for pm in c_obj.get("matches", []):
                        hid = pm.get("home_team", {}).get("id")
                        aid = pm.get("away_team", {}).get("id")
                        if hid and aid:
                            key = f"{hid}_{aid}"
                            o_1x2 = pm.get("odds_1x2")
                            if o_1x2 and o_1x2.get("1") is not None:
                                existing_match_odds[key] = {
                                    "matched": True,
                                    "1X2": o_1x2,
                                    "over_under_2_5": pm.get("odds_ou25", {}),
                                    "btts": pm.get("odds_btts", {}),
                                    "match_url": pm.get("winamax_url", "https://www.winamax.es"),
                                    "cached_value_bets": pm.get("value_bets", [])
                                }
            except Exception as e:
                print(f"   [!] Error llegint memòria cau prèvia de {WEB_DATA_FILE}: {e}")

        all_referee_updates = []
        seen_referee_match_ids: set = set()  # Per deduplicar dins de la mateixa execució

        # 1. Processar cada lliga
        for meta in COMPETITIONS_META:
            cid = meta["id"]
            cname = meta["name"]
            print(f"[*] Processant lliga: {cname} ({cid})...")

            # A) Power Rankings
            pr = self.get_power_rankings(cid)
            power_rankings[cid] = pr

            # B) Prediccions i Historial de Jornades
            pred_data = self.get_upcoming_predictions(cid, existing_match_odds=existing_match_odds)
            if pred_data.get("referee_updates"):
                for ru in pred_data["referee_updates"]:
                    mid = ru.get("match_id")
                    if mid and mid not in seen_referee_match_ids:
                        seen_referee_match_ids.add(mid)
                        all_referee_updates.append(ru)

            # Obtenir totes les jornades disponibles a la BD per a aquesta lliga
            with self.db.get_connection() as conn:
                j_rows = conn.cursor().execute(
                    "SELECT DISTINCT jornada FROM matches WHERE competition_id = ? AND jornada IS NOT NULL ORDER BY jornada ASC",
                    (cid,)
                ).fetchall()
                available_jornadas = [int(r[0]) for r in j_rows if r[0]]
            
            if pred_data["jornada"] not in available_jornadas:
                available_jornadas.append(pred_data["jornada"])
            available_jornadas.sort()

            # Diccionari de partits per cada jornada
            by_jornada_matches = {}
            for j_val in available_jornadas:
                if j_val == pred_data["jornada"]:
                    by_jornada_matches[str(j_val)] = [{k: v for k, v in m.items() if k != "raw_pred"} for m in pred_data["matches"]]
                else:
                    by_jornada_matches[str(j_val)] = self.get_jornada_matches(cid, j_val)

            predictions[cid] = {
                "jornada": pred_data["jornada"],
                "active_jornada": pred_data["jornada"],
                "available_jornadas": available_jornadas,
                "competition_id": cid,
                "competition_name": cname,
                "by_jornada": by_jornada_matches,
                "matches": [{k: v for k, v in m.items() if k != "raw_pred"} for m in pred_data["matches"]]
            }

            # Extreure Value Bets
            for m in pred_data["matches"]:
                all_predicted_matches_flat.append(m)
                for vb in m.get("value_bets", []):
                    p_calc = float(vb.get("model_prob", 0.0)) / 100.0
                    edge_val = vb.get("ev_pct")
                    if edge_val is None:
                        edge_val = vb.get("edge")
                    if edge_val is None:
                        b_calc = float(vb.get("bookie_odd", 1.0))
                        edge_val = (p_calc * b_calc - 1.0) * 100.0

                    stake_val = vb.get("kelly_pct")
                    if stake_val is None:
                        stake_val = vb.get("stake_pct", 2.5)

                    w_odd = float(vb.get("bookie_odd", 1.0))
                    b365_odd = float(vb.get("bookie_odd_bet365") or round(w_odd * 1.015, 2))
                    edge_w = round(float(edge_val), 1)
                    edge_b365 = round((p_calc * b365_odd - 1.0) * 100.0, 1)

                    from scraper.winamax_scraper import WINAMAX_TOURNAMENTS
                    w_base = f"https://www.winamax.es/apuestas-deportivas/sports/1/{WINAMAX_TOURNAMENTS.get(cid, '32')}"
                    w_match_url = vb.get("url") or m.get("odds", {}).get("url") or w_base
                    b365_match_url = "https://www.bet365.es/#/AS/B1/"

                    all_value_bets.append({
                        "competition_id": cid,
                        "competition_name": cname,
                        "matchup": f"{m['home_team']['name']} vs {m['away_team']['name']}",
                        "date": m["date"],
                        "selection": vb.get("name"),
                        "category": vb.get("category"),
                        "bookie_odd": w_odd,
                        "bookie_odd_winamax": w_odd,
                        "bookie_odd_bet365": b365_odd,
                        "model_prob": float(vb.get("model_prob", 0.0)),
                        "fair_odd": float(vb.get("fair_odd", 1.0)),
                        "edge_pct": edge_w,
                        "edge_pct_winamax": edge_w,
                        "edge_pct_bet365": edge_b365,
                        "kelly_stake": round(float(stake_val), 1),
                        "winamax_url": w_match_url,
                        "bet365_url": b365_match_url
                    })

            # C) 6 Combinades per lliga i seguiment multijornada
            league_combos = self.get_full_combos_suite(pred_data["matches"], cid, pred_data["jornada"], force_regenerate=force_combos)
            if not force_combos and (not league_combos.get("safe")) and previous_data.get("combos", {}).get(cid, {}).get("safe"):
                # Si la jornada actual no ha començat encara, NO hem de sobreescriure amb combinades antigues
                if league_combos.get("has_started") is False:
                    pass
                else:
                    print(f"   🛡️ PROTECCIÓ: Preservant combinades prèvies de {cid} de la memòria cau.")
                    league_combos = previous_data["combos"][cid]

            # Obtenir totes les jornades de combinades disponibles a la BD per a aquesta lliga
            with self.db.get_connection() as conn:
                c_j_rows = conn.cursor().execute(
                    "SELECT DISTINCT jornada FROM combo_recommendations WHERE competition_id = ? ORDER BY jornada ASC",
                    (cid,)
                ).fetchall()
                combo_jornadas = [int(r[0]) for r in c_j_rows if r[0]]

            if pred_data["jornada"] not in combo_jornadas:
                combo_jornadas.append(pred_data["jornada"])
            combo_jornadas.sort()

            actual_combo_jornada = league_combos.get("jornada", pred_data["jornada"])
            by_jornada_combos = {}
            for cj in combo_jornadas:
                if cj == actual_combo_jornada:
                    by_jornada_combos[str(cj)] = {
                        "competition_id": cid,
                        "jornada": cj,
                        "safe": list(league_combos.get("safe", [])),
                        "semi": list(league_combos.get("semi", [])),
                        "risky": list(league_combos.get("risky", []))
                    }
                else:
                    j_matches = by_jornada_matches.get(str(cj)) or []
                    existing_for_cj = self.db.get_combos_for_jornada(cid, cj)
                    if existing_for_cj:
                        by_jornada_combos[str(cj)] = self.get_full_combos_suite(j_matches, cid, cj)
                    else:
                        by_jornada_combos[str(cj)] = {
                            "competition_id": cid,
                            "jornada": cj,
                            "safe": [],
                            "semi": [],
                            "risky": []
                        }

            league_combos["by_jornada"] = by_jornada_combos
            league_combos["available_jornadas"] = combo_jornadas
            league_combos["active_jornada"] = actual_combo_jornada
            combos[cid] = league_combos

            active_comps_meta.append({
                "id": cid,
                "name": cname,
                "country": meta["country"],
                "flag": meta["flag"],
                "active_jornada": pred_data["jornada"],
                "matches_count": len(pred_data["matches"])
            })

        # 2. Combinades del Dia (Suite de 6 combinades del dia per a totes les dates)
        print("[*] Generant i avaluant les Combinades del Dia...")
        daily_combos = self.get_daily_combos_suite(all_predicted_matches_flat, previous_data=previous_data, force_regenerate=force_combos)
        combos["DAILY"] = daily_combos


        # Protecció de Value bets si el scraper de Winamax ha estat bloquejat al núvol
        if len(all_value_bets) == 0 and previous_data.get("value_bets"):
            print(f"   🛡️ PROTECCIÓ: Preservant {len(previous_data['value_bets'])} apostes de valor de la memòria cau prèvia (bloqueig IP cloud detectat).")
            all_value_bets = previous_data["value_bets"]

        # Ordenar Value bets pel millor edge %
        all_value_bets.sort(key=lambda x: (float(x.get("edge_pct")) if x.get("edge_pct") is not None else -999.0), reverse=True)

        # 3. Arbitratge Esportiu (Surebets / "Diners Segurs")
        print("[*] Escanejant oportunitats d'Arbitratge Esportiu (Surebets)...")
        arbitrage_report = self.arbitrage_engine.scan_all_matches(all_predicted_matches_flat)

        # 4. Simulació Financera
        print("[*] Calculant resum financer 'Què hagués passat si...'...")
        financial_ledger = self.get_financial_ledger()

        # Garantir que el changelog conté l'entrada oficial d'avui amb àrbitres detallats
        today_str = now.strftime("%Y-%m-%d")
        # Filtrar les designacions arbitrals que ja s'havien reportat en dies anteriors
        # (si el CTA va publicar els àrbitres ahir, ja van sortir a Novetats i no s'han de repetir)
        already_reported_ids = self.changelog_mgr.get_already_reported_referee_match_ids(today_str)
        new_referee_updates = [
            ru for ru in all_referee_updates
            if ru.get("match_id") not in already_reported_ids and not ru.get("is_finished")
        ]

        # Si no s'han passat partits recents directament (ex. execució local o pipeline posterior),
        # recuperar els partits finalitzats de la jornada activa de cap de setmana en curs per informar el resum diari
        effective_matches = ingested_matches or []
        if not effective_matches:
            effective_matches = self.get_active_jornada_finished_matches()

        self.changelog_mgr.record_pipeline_execution(
            date_str=today_str,
            ingested_matches=effective_matches,
            evaluated_combos=evaluated_combos or [],
            new_combos=new_combos or [],
            referee_count=len(new_referee_updates),
            referee_updates=new_referee_updates if new_referee_updates else None,
            notes=["Sincronització de les darreres dades i mètriques del model."] if not new_referee_updates and not effective_matches else None
        )

        # 4. Alerta de quotes no detectades a Winamax / Bet365
        if self.odds_alerts:
            print("\n" + "=" * 78)
            print(f"⚠️  [ALERTA CRÍTICA DE DADES] S'han detectat {len(self.odds_alerts)} partits sense quotes a Winamax / Bet365:")
            for a in self.odds_alerts:
                print(f"   • [{a['competition_name']}] {a['matchup']} ({a['date']}) -> Falten: {', '.join(a['missing_bookmakers'])}")
            print("=" * 78 + "\n")
            try:
                from notifier.email_notifier import send_missing_odds_alert
                send_missing_odds_alert(self.odds_alerts)
            except Exception as e:
                print(f"[!] Error enviant alertes de quotes: {e}")
        else:
            try:
                from notifier.email_notifier import send_missing_odds_alert
                send_missing_odds_alert([])
            except Exception:
                pass

        # 5. Assembling JSON Payload
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
            "arbitrage": arbitrage_report,
            "combos": combos,
            "daily_combos": daily_combos,
            "bankroll_simulation": financial_ledger,
            "odds_alerts": self.odds_alerts,
            "has_missing_odds": len(self.odds_alerts) > 0
        }

        # 6. Escriure a fitxer
        WEB_DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(WEB_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

        size_kb = WEB_DATA_FILE.stat().st_size / 1024.0
        print(f"\n[ÈXIT] Dades exportades a: {WEB_DATA_FILE}")
        print(f"       Mida del fitxer: {size_kb:.1f} KB")
        print("=" * 75 + "\n")

        return WEB_DATA_FILE

if __name__ == "__main__":
    force_run = "--force" in sys.argv
    exporter = WebDataExporter()
    exporter.export_all(force_combos=force_run)
