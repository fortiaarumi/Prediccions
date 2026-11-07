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
from __future__ import annotations

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
from typing import Dict, List, Any, Optional, Tuple

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
    },
    {
        "id": "BUNDESLIGA",
        "name": "Bundesliga",
        "country": "Alemanya",
        "flag": "🇩🇪",
        "flashscore_code": "bundesliga",
        "winamax_sport_id": 1,
        "winamax_cat_id": 30,
    },
    {
        "id": "LIGUE1",
        "name": "Ligue 1",
        "country": "França",
        "flag": "🇫🇷",
        "flashscore_code": "ligue-1",
        "winamax_sport_id": 1,
        "winamax_cat_id": 7,
    },
    {
        "id": "SERIEA",
        "name": "Serie A",
        "country": "Itàlia",
        "flag": "🇮🇹",
        "flashscore_code": "serie-a",
        "winamax_sport_id": 1,
        "winamax_cat_id": 31,
    }
]

class WebDataExporter:
    def __init__(self, db: Optional[Any] = None):
        if isinstance(db, (str, Path)):
            self.db = DatabaseManager(db_path=str(db))
        elif db is not None:
            self.db = db
        else:
            self.db = DatabaseManager()
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
                "SELECT MAX(jornada) FROM matches WHERE competition_id = ? AND status IN ('FINISHED', 'SUSPENDED', 'LIVE')",
                (comp,)
            ).fetchone()[0] or 0

            # Si la jornada max_f encara està en curs de jugar-se (té >= 2 partits SCHEDULED o LIVE)
            if max_f > 0:
                sched_in_max_f = cursor.execute(
                    "SELECT COUNT(*) FROM matches WHERE competition_id = ? AND jornada = ? AND status IN ('SCHEDULED', 'LIVE')",
                    (comp, max_f)
                ).fetchone()[0] or 0
                if sched_in_max_f >= 1:
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

    @staticmethod
    def _poisson_cdf(k: int, lmbda: float) -> float:
        """Funcio de distribucio acumulada de Poisson P(X <= k) amb mitjana lmbda."""
        import math
        if lmbda <= 0.0:
            return 1.0 if k >= 0 else 0.0
        prob_sum = 0.0
        for i in range(max(0, k) + 1):
            prob_sum += (lmbda ** i) * math.exp(-lmbda) / math.factorial(i)
        return min(1.0, max(0.0, prob_sum))

    @staticmethod
    def _poisson_pmf(k: int, lmbda: float) -> float:
        """Funció de probabilitat puntual de Poisson P(X = k) amb mitjana lmbda."""
        import math
        if lmbda <= 0.0:
            return 1.0 if k == 0 else 0.0
        if k < 0:
            return 0.0
        return float((lmbda ** k) * math.exp(-lmbda) / math.factorial(k))

    @staticmethod
    def _calc_line_probabilities(threshold: float, lmbda: float) -> Dict[str, float]:
        """
        Calcula rigorosament P_over, P_under i P_push per a qualsevol línia:
        - Si threshold és enter (ex: 2.0, 9.0):
          P_push = P(X = k) = pmf(k)
          P_over = P(X > k) = 1 - cdf(k)
          P_under = P(X < k) = cdf(k - 1)
          P_over + P_push + P_under = 1.0
        - Si threshold és mig punt (ex: 2.5, 9.5):
          P_push = 0.0
          P_over = 1 - cdf(floor(threshold))
          P_under = cdf(floor(threshold))
          P_over + P_under = 1.0
        """
        import math
        is_int = (float(threshold) == float(int(threshold)))
        k = int(threshold)

        if not is_int:
            floor_k = int(math.floor(threshold))
            p_under = WebDataExporter._poisson_cdf(floor_k, lmbda)
            p_over = max(0.0, 1.0 - p_under)
            return {
                "p_over": round(p_over * 100.0, 2),
                "p_under": round(p_under * 100.0, 2),
                "p_push": 0.0,
                "is_integer": False
            }
        else:
            p_push = WebDataExporter._poisson_pmf(k, lmbda)
            p_over = max(0.0, 1.0 - WebDataExporter._poisson_cdf(k, lmbda))
            p_under = WebDataExporter._poisson_cdf(k - 1, lmbda) if k > 0 else 0.0
            return {
                "p_over": round(p_over * 100.0, 2),
                "p_under": round(p_under * 100.0, 2),
                "p_push": round(p_push * 100.0, 2),
                "is_integer": True
            }

    @staticmethod
    def _calc_distribution_line_probabilities(threshold: float, distribution: List[float]) -> Dict[str, float]:
        """
        Calcula rigorosament P_over, P_under i P_push a partir d'una distribució
        discreta completa (ex: matriu Dixon-Coles normalitzada per a gols totals).
        - distribution[k] = P(X = k) per a k in [0, len(distribution)-1]
        - Si threshold és enter (ex: 2.0):
          P_push = P(X = k) = distribution[k]
          P_under = sum_{i=0}^{k-1} distribution[i]
          P_over = sum_{i=k+1}^{N-1} distribution[i]
          P_over + P_push + P_under = 1.0 (exactament 100%)
        - Si threshold és mig punt (ex: 1.5, 2.5, 3.5):
          P_push = 0.0
          P_under = sum_{i=0}^{floor(threshold)} distribution[i]
          P_over = sum_{i=floor(threshold)+1}^{N-1} distribution[i]
          P_over + P_under = 1.0
        """
        import math
        is_int = (float(threshold) == float(int(threshold)))
        n = len(distribution)

        if not is_int:
            floor_k = int(math.floor(threshold))
            p_under = sum(distribution[:floor_k + 1]) if floor_k >= 0 else 0.0
            p_over = sum(distribution[floor_k + 1:]) if floor_k + 1 < n else 0.0
            tot = p_under + p_over
            if tot > 0:
                p_under /= tot
                p_over /= tot
            return {
                "p_over": round(p_over * 100.0, 2),
                "p_under": round(p_under * 100.0, 2),
                "p_push": 0.0,
                "is_integer": False
            }
        else:
            k = int(threshold)
            p_push = distribution[k] if 0 <= k < n else 0.0
            p_under = sum(distribution[:k]) if k > 0 else 0.0
            p_over = sum(distribution[k + 1:]) if k + 1 < n else 0.0
            tot = p_under + p_push + p_over
            if tot > 0:
                p_under /= tot
                p_push /= tot
                p_over /= tot
            return {
                "p_over": round(p_over * 100.0, 2),
                "p_under": round(p_under * 100.0, 2),
                "p_push": round(p_push * 100.0, 2),
                "is_integer": True
            }

    @staticmethod
    def _calc_ev_and_fair_odd(p_win_pct: float, p_push_pct: float, odd: Optional[float]) -> Tuple[Optional[float], Optional[float]]:
        """
        Fórmules matemàtiques universals per a mercats amb i sense devolució (push):
        - Quota justa amb push: Q_fair = (1 - P_push) / P_win
        - EV amb push: EV = (P_win * Q + P_push - 1) * 100.0
        """
        p_w = p_win_pct / 100.0
        p_v = p_push_pct / 100.0
        if p_w <= 0.0:
            return None, None
        fair_odd = round((1.0 - p_v) / p_w, 2)
        if odd and odd > 1.0:
            ev = round((p_w * odd + p_v - 1.0) * 100.0, 2)
        else:
            ev = None
        return fair_odd, ev

    def build_match_details(
        self,
        match_row: Dict[str, Any],
        pred: Dict[str, Any],
        home_data: Dict[str, Any],
        away_data: Dict[str, Any],
        ref_data: Dict[str, Any],
        ref_res: Dict[str, Any],
        odds_winamax: Dict[str, Any],
        odds_bet365: Dict[str, Any],
        bet_analysis: Dict[str, Any],
        competition_id: str,
        active_jornada: int,
        is_finished: bool = False,
        is_live_match: bool = False,
        is_susp_match: bool = False,
        susp_reason: str = ""
    ) -> Dict[str, Any]:
        """
        Construeix l'estructura exhaustiva de la fitxa detallada de partit per a la web:
        1. Capçalera & Resum (equips, escuts, lliga, jornada, data/hora, estats).
        2. Comparativa d'equips (classificació clàssica, forma recent 5 partits, dades d'àrbitre,
           estadístiques reals acumulades a casa/fora: xG, córners, targetes. MAI zeros inventats).
        3. Anàlisi de mercats amb quotes, probabilitats justes, frescor i procedència.
        4. Selector interactiu de línies (slider Córners, Gols, Targetes amb EV en temps real).
        5. Recomanacions (Màxima probabilitat separada de valor, millor +EV i mercats desaconsellats).
        """
        h_id = home_data["id"]
        a_id = away_data["id"]
        c_meta = next((m for m in COMPETITIONS_META if m["id"] == competition_id), {})
        comp_name = c_meta.get("name", competition_id)
        match_date = match_row.get("date_time", "Pendent")

        # 1. Recuperar classificació clàssica i forma recent
        pr_list = self.get_power_rankings(competition_id)
        h_pr = next((p for p in pr_list if p.get("team_id") == h_id or p.get("name") == home_data.get("name")), None)
        a_pr = next((p for p in pr_list if p.get("team_id") == a_id or p.get("name") == away_data.get("name")), None)

        def _fmt_standing(pr_entry):
            if not pr_entry:
                return {
                    "available": False, "rank": None, "points": None, "played": None,
                    "won": None, "drawn": None, "lost": None, "gf": None, "ga": None,
                    "gd": None, "form_5": []
                }
            return {
                "available": True,
                "rank": pr_entry.get("rank"),
                "points": pr_entry.get("points"),
                "played": pr_entry.get("played"),
                "won": pr_entry.get("won"),
                "drawn": pr_entry.get("drawn"),
                "lost": pr_entry.get("lost"),
                "gf": pr_entry.get("gf"),
                "ga": pr_entry.get("ga"),
                "gd": pr_entry.get("gd"),
                "form_5": pr_entry.get("form_5", [])
            }

        h_standings = _fmt_standing(h_pr)
        a_standings = _fmt_standing(a_pr)

        # 2. Mètriques reals acumulades des de la base de dades SQLite (sense inventar zeros)
        home_adv = {
            "corners_for_per_game": None,
            "corners_against_per_game": None,
            "yellow_cards_per_game": None,
            "xg_for_per_game": None,
            "xg_against_per_game": None,
            "home_record": {"played": 0, "won": 0, "drawn": 0, "lost": 0, "gf": 0, "ga": 0}
        }
        away_adv = {
            "corners_for_per_game": None,
            "corners_against_per_game": None,
            "yellow_cards_per_game": None,
            "xg_for_per_game": None,
            "xg_against_per_game": None,
            "away_record": {"played": 0, "won": 0, "drawn": 0, "lost": 0, "gf": 0, "ga": 0}
        }

        try:
            with self.db.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT home_goals, away_goals, home_xg, away_xg, home_corners, away_corners, home_yellow_cards
                    FROM matches
                    WHERE competition_id = ? AND status = 'FINISHED' AND home_team_id = ?
                """, (competition_id, h_id))
                h_rows = cur.fetchall()

                cur.execute("""
                    SELECT away_goals, home_goals, away_xg, home_xg, away_corners, home_corners, away_yellow_cards
                    FROM matches
                    WHERE competition_id = ? AND status = 'FINISHED' AND away_team_id = ?
                """, (competition_id, a_id))
                a_rows = cur.fetchall()

            if h_rows:
                h_rec = home_adv["home_record"]
                h_rec["played"] = len(h_rows)
                for r in h_rows:
                    hg, ag = r[0], r[1]
                    if hg is not None and ag is not None:
                        h_rec["gf"] += int(hg)
                        h_rec["ga"] += int(ag)
                        if int(hg) > int(ag): h_rec["won"] += 1
                        elif int(hg) == int(ag): h_rec["drawn"] += 1
                        else: h_rec["lost"] += 1
                c_for = [r[4] for r in h_rows if r[4] is not None]
                c_against = [r[5] for r in h_rows if r[5] is not None]
                cards = [r[6] for r in h_rows if r[6] is not None]
                xg_f = [r[2] for r in h_rows if r[2] is not None]
                xg_a = [r[3] for r in h_rows if r[3] is not None]
                if c_for: home_adv["corners_for_per_game"] = round(sum(c_for) / len(c_for), 1)
                if c_against: home_adv["corners_against_per_game"] = round(sum(c_against) / len(c_against), 1)
                if cards: home_adv["yellow_cards_per_game"] = round(sum(cards) / len(cards), 1)
                if xg_f: home_adv["xg_for_per_game"] = round(sum(xg_f) / len(xg_f), 2)
                if xg_a: home_adv["xg_against_per_game"] = round(sum(xg_a) / len(xg_a), 2)

            if a_rows:
                a_rec = away_adv["away_record"]
                a_rec["played"] = len(a_rows)
                for r in a_rows:
                    ag, hg = r[0], r[1]
                    if ag is not None and hg is not None:
                        a_rec["gf"] += int(ag)
                        a_rec["ga"] += int(hg)
                        if int(ag) > int(hg): a_rec["won"] += 1
                        elif int(ag) == int(hg): a_rec["drawn"] += 1
                        else: a_rec["lost"] += 1
                c_for = [r[4] for r in a_rows if r[4] is not None]
                c_against = [r[5] for r in a_rows if r[5] is not None]
                cards = [r[6] for r in a_rows if r[6] is not None]
                xg_f = [r[2] for r in a_rows if r[2] is not None]
                xg_a = [r[3] for r in a_rows if r[3] is not None]
                if c_for: away_adv["corners_for_per_game"] = round(sum(c_for) / len(c_for), 1)
                if c_against: away_adv["corners_against_per_game"] = round(sum(c_against) / len(c_against), 1)
                if cards: away_adv["yellow_cards_per_game"] = round(sum(cards) / len(cards), 1)
                if xg_f: away_adv["xg_for_per_game"] = round(sum(xg_f) / len(xg_f), 2)
                if xg_a: away_adv["xg_against_per_game"] = round(sum(xg_a) / len(xg_a), 2)
        except Exception:
            pass

        # 3. Dades de l'àrbitre
        ref_info = {
            "name": ref_res.get("name", "Pendent CTA / PGMOL"),
            "is_official": not ref_res.get("is_generic", True),
            "strictness_index": round(float(ref_data.get("strictness_index", 1.0)), 2) if ref_data.get("strictness_index") is not None else None,
            "yellow_avg": round(float(ref_data.get("yellow_cards_avg", ref_data.get("yellow_avg", 4.5))), 2) if (ref_data.get("yellow_cards_avg") or ref_data.get("yellow_avg")) else None,
            "red_avg": round(float(ref_data.get("red_cards_avg", ref_data.get("red_avg", 0.25))), 2) if (ref_data.get("red_cards_avg") or ref_data.get("red_avg")) else None,
            "fouls_avg": round(float(ref_data.get("fouls_avg", 25.0)), 1) if ref_data.get("fouls_avg") else None
        }

        # 4. Selector interactiu de línies (Córners, Gols, Targetes)
        def _find_odd(odds_dict, sel_pattern):
            if not isinstance(odds_dict, dict):
                return None
            for k, v in odds_dict.items():
                if sel_pattern.lower() in str(k).lower():
                    try:
                        fv = float(v)
                        if fv > 1.0:
                            return fv
                    except (ValueError, TypeError):
                        pass
            return None

        # A) Córners (inclou línies asiàtiques enteres amb devolució i mitges línies sense devolució)
        corners_pred = pred.get("corners", {})
        lmbda_corners = float(corners_pred.get("expected_total_corners") or 9.5)
        w_corners_odds = odds_winamax.get("corners", {}) if isinstance(odds_winamax.get("corners"), dict) else {}
        b_corners_odds = odds_bet365.get("corners", {}) if isinstance(odds_bet365.get("corners"), dict) else {}

        corner_thresholds = [7.5, 8.5, 9.0, 9.5, 10.0, 10.5, 11.5]
        corner_lines = []
        for t in corner_thresholds:
            p_dict = self._calc_line_probabilities(t, lmbda_corners)
            p_ov = p_dict["p_over"]
            p_un = p_dict["p_under"]
            p_psh = p_dict["p_push"]
            is_int = p_dict["is_integer"]

            t_str = f"{int(t)}" if is_int else f"{t}"
            w_ov = _find_odd(w_corners_odds, f"Over {t_str}") or _find_odd(w_corners_odds, f">{t_str}")
            w_un = _find_odd(w_corners_odds, f"Under {t_str}") or _find_odd(w_corners_odds, f"<{t_str}")
            b_ov = _find_odd(b_corners_odds, f"Over {t_str}") or _find_odd(b_corners_odds, f">{t_str}")
            b_un = _find_odd(b_corners_odds, f"Under {t_str}") or _find_odd(b_corners_odds, f"<{t_str}")

            fair_ov, ev_w_ov = self._calc_ev_and_fair_odd(p_ov, p_psh, w_ov)
            _, ev_b_ov = self._calc_ev_and_fair_odd(p_ov, p_psh, b_ov)
            fair_un, ev_w_un = self._calc_ev_and_fair_odd(p_un, p_psh, w_un)
            _, ev_b_un = self._calc_ev_and_fair_odd(p_un, p_psh, b_un)

            corner_lines.append({
                "threshold": t,
                "is_integer": is_int,
                "prob_over": p_ov,
                "prob_under": p_un,
                "prob_push": p_psh,
                "fair_odd_over": fair_ov,
                "fair_odd_under": fair_un,
                "winamax_over": w_ov,
                "winamax_under": w_un,
                "bet365_over": b_ov,
                "bet365_under": b_un,
                "ev_winamax_over": ev_w_ov,
                "ev_winamax_under": ev_w_un,
                "ev_bet365_over": ev_b_ov,
                "ev_bet365_under": ev_b_un,
                "has_real_odds": bool(w_ov or w_un or b_ov or b_un)
            })

        # B) Gols
        goals_pred = pred.get("goals", {})
        lmbda_h = float(goals_pred.get("expected_goals_home", goals_pred.get("lambda_home", 1.3)))
        lmbda_a = float(goals_pred.get("expected_goals_away", goals_pred.get("mu_away", 1.1)))
        lmbda_goals = round(lmbda_h + lmbda_a, 2)
        w_goals_odds = odds_winamax.get("goals", {}) if isinstance(odds_winamax.get("goals"), dict) else {}
        b_goals_odds = odds_bet365.get("goals", {}) if isinstance(odds_bet365.get("goals"), dict) else {}

        goal_thresholds = [1.5, 2.0, 2.5, 3.0, 3.5]
        total_goals_dist = goals_pred.get("total_goals_distribution")
        goal_lines = []
        for t in goal_thresholds:
            if total_goals_dist and len(total_goals_dist) > 0:
                p_dict = self._calc_distribution_line_probabilities(t, total_goals_dist)
            else:
                p_dict = self._calc_line_probabilities(t, lmbda_goals)
            p_ov = p_dict["p_over"]
            p_un = p_dict["p_under"]
            p_psh = p_dict["p_push"]
            is_int = p_dict["is_integer"]

            t_str = f"{int(t)}" if is_int else f"{t}"
            w_ov = None
            w_un = None
            if t == 2.5:
                w_ou25 = odds_winamax.get("over_under_2_5", {})
                w_ov = float(w_ou25.get("over") or w_ou25.get("Over 2.5") or 0.0) or None
                w_un = float(w_ou25.get("under") or w_ou25.get("Under 2.5") or 0.0) or None
            if not w_ov:
                w_ov = _find_odd(w_goals_odds, f"Over {t_str}") or _find_odd(w_goals_odds, f">{t_str}")
                w_un = _find_odd(w_goals_odds, f"Under {t_str}") or _find_odd(w_goals_odds, f"<{t_str}")

            b_ov = None
            b_un = None
            if t == 2.5:
                b_ou25 = odds_bet365.get("over_under_2_5", {})
                b_ov = float(b_ou25.get("over") or b_ou25.get("Over 2.5") or 0.0) or None
                b_un = float(b_ou25.get("under") or b_ou25.get("Under 2.5") or 0.0) or None
            if not b_ov:
                b_ov = _find_odd(b_goals_odds, f"Over {t_str}") or _find_odd(b_goals_odds, f">{t_str}")
                b_un = _find_odd(b_goals_odds, f"Under {t_str}") or _find_odd(b_goals_odds, f"<{t_str}")

            fair_ov, ev_w_ov = self._calc_ev_and_fair_odd(p_ov, p_psh, w_ov)
            _, ev_b_ov = self._calc_ev_and_fair_odd(p_ov, p_psh, b_ov)
            fair_un, ev_w_un = self._calc_ev_and_fair_odd(p_un, p_psh, w_un)
            _, ev_b_un = self._calc_ev_and_fair_odd(p_un, p_psh, b_un)

            goal_lines.append({
                "threshold": t,
                "is_integer": is_int,
                "prob_over": p_ov,
                "prob_under": p_un,
                "prob_push": p_psh,
                "fair_odd_over": fair_ov,
                "fair_odd_under": fair_un,
                "winamax_over": w_ov,
                "winamax_under": w_un,
                "bet365_over": b_ov,
                "bet365_under": b_un,
                "ev_winamax_over": ev_w_ov,
                "ev_winamax_under": ev_w_un,
                "ev_bet365_over": ev_b_ov,
                "ev_bet365_under": ev_b_un,
                "has_real_odds": bool(w_ov or w_un or b_ov or b_un)
            })

        # C) Targetes
        cards_pred = pred.get("cards", {})
        lmbda_cards = float(cards_pred.get("expected_total_cards") or 4.5)
        w_cards_odds = odds_winamax.get("cards", {}) if isinstance(odds_winamax.get("cards"), dict) else {}
        b_cards_odds = odds_bet365.get("cards", {}) if isinstance(odds_bet365.get("cards"), dict) else {}

        card_thresholds = [3.5, 4.0, 4.5, 5.0, 5.5]
        card_lines = []
        for t in card_thresholds:
            p_dict = self._calc_line_probabilities(t, lmbda_cards)
            p_ov = p_dict["p_over"]
            p_un = p_dict["p_under"]
            p_psh = p_dict["p_push"]
            is_int = p_dict["is_integer"]

            t_str = f"{int(t)}" if is_int else f"{t}"
            w_ov = _find_odd(w_cards_odds, f"Over {t_str}") or _find_odd(w_cards_odds, f">{t_str}")
            w_un = _find_odd(w_cards_odds, f"Under {t_str}") or _find_odd(w_cards_odds, f"<{t_str}")
            b_ov = _find_odd(b_cards_odds, f"Over {t_str}") or _find_odd(b_cards_odds, f">{t_str}")
            b_un = _find_odd(b_cards_odds, f"Under {t_str}") or _find_odd(b_cards_odds, f"<{t_str}")

            fair_ov, ev_w_ov = self._calc_ev_and_fair_odd(p_ov, p_psh, w_ov)
            _, ev_b_ov = self._calc_ev_and_fair_odd(p_ov, p_psh, b_ov)
            fair_un, ev_w_un = self._calc_ev_and_fair_odd(p_un, p_psh, w_un)
            _, ev_b_un = self._calc_ev_and_fair_odd(p_un, p_psh, b_un)

            card_lines.append({
                "threshold": t,
                "is_integer": is_int,
                "prob_over": p_ov,
                "prob_under": p_un,
                "prob_push": p_psh,
                "fair_odd_over": fair_ov,
                "fair_odd_under": fair_un,
                "winamax_over": w_ov,
                "winamax_under": w_un,
                "bet365_over": b_ov,
                "bet365_under": b_un,
                "ev_winamax_over": ev_w_ov,
                "ev_winamax_under": ev_w_un,
                "ev_bet365_over": ev_b_ov,
                "ev_bet365_under": ev_b_un,
                "has_real_odds": bool(w_ov or w_un or b_ov or b_un)
            })

        # 5. Taula completa de mercats i quotes
        p1 = round(float(goals_pred.get("prob_1X2", {}).get("1", 33.3)), 1)
        px = round(float(goals_pred.get("prob_1X2", {}).get("X", 33.3)), 1)
        p2 = round(float(goals_pred.get("prob_1X2", {}).get("2", 33.4)), 1)
        w_1x2 = odds_winamax.get("1X2", {}) or odds_winamax.get("odds_1x2", {})
        b_1x2 = odds_bet365.get("1X2", {}) if isinstance(odds_bet365.get("1X2"), dict) else {}
        w_dc = odds_winamax.get("double_chance", {}) if isinstance(odds_winamax.get("double_chance"), dict) else {}
        b_dc = odds_bet365.get("double_chance", {}) if isinstance(odds_bet365.get("double_chance"), dict) else {}
        w_btts = odds_winamax.get("btts", {}) if isinstance(odds_winamax.get("btts"), dict) else {}
        b_btts = odds_bet365.get("btts", {}) if isinstance(odds_bet365.get("btts"), dict) else {}
        w_dnb = odds_winamax.get("dnb", {}) if isinstance(odds_winamax.get("dnb"), dict) else {}
        b_dnb = odds_bet365.get("dnb", {}) if isinstance(odds_bet365.get("dnb"), dict) else {}

        freshness_w = odds_winamax.get("freshness", "UNKNOWN")
        freshness_b = odds_bet365.get("freshness", "UNKNOWN")
        prov_w = odds_winamax.get("provenance", "WINAMAX")
        prov_b = odds_bet365.get("provenance", "BET365")

        def _calc_market_item(m_cat, sel_name, prob_win, odd_w_raw, odd_b_raw, prob_push=0.0, is_standard=True):
            ow = float(odd_w_raw) if odd_w_raw and float(odd_w_raw) > 1.0 else None
            ob = float(odd_b_raw) if odd_b_raw and float(odd_b_raw) > 1.0 else None
            fair, ev_w = self._calc_ev_and_fair_odd(prob_win, prob_push, ow)
            _, ev_b = self._calc_ev_and_fair_odd(prob_win, prob_push, ob)
            best_odd = max([o for o in [ow, ob] if o is not None], default=None)
            best_bk = "Winamax" if (best_odd == ow and ow is not None) else ("Bet365" if (best_odd == ob and ob is not None) else None)
            best_ev = max([e for e in [ev_w, ev_b] if e is not None], default=None)
            return {
                "category": m_cat,
                "selection": sel_name,
                "prob": prob_win,
                "prob_push": prob_push,
                "fair_odd": fair,
                "odd_winamax": ow,
                "odd_bet365": ob,
                "best_odd": best_odd,
                "best_bookie": best_bk,
                "ev_winamax": ev_w,
                "ev_bet365": ev_b,
                "best_ev": best_ev,
                "freshness_winamax": freshness_w if ow else "N/A",
                "freshness_bet365": freshness_b if ob else "N/A",
                "provenance_winamax": prov_w if ow else "N/A",
                "provenance_bet365": prov_b if ob else "N/A",
                "is_standard": is_standard
            }

        markets_analysis = [
            _calc_market_item("1X2", f"1 - Victòria {home_data['name']}", p1, w_1x2.get("1"), b_1x2.get("1"), prob_push=0.0, is_standard=True),
            _calc_market_item("1X2", "X - Empat", px, w_1x2.get("X"), b_1x2.get("X"), prob_push=0.0, is_standard=True),
            _calc_market_item("1X2", f"2 - Victòria {away_data['name']}", p2, w_1x2.get("2"), b_1x2.get("2"), prob_push=0.0, is_standard=True),
            _calc_market_item("Doble Oportunitat", f"1X - {home_data['name']} o Empat", round(p1 + px, 1), w_dc.get("1X"), b_dc.get("1X"), prob_push=0.0, is_standard=True),
            _calc_market_item("Doble Oportunitat", f"12 - {home_data['name']} o {away_data['name']}", round(p1 + p2, 1), w_dc.get("12"), b_dc.get("12"), prob_push=0.0, is_standard=True),
            _calc_market_item("Doble Oportunitat", f"X2 - Empat o {away_data['name']}", round(px + p2, 1), w_dc.get("X2"), b_dc.get("X2"), prob_push=0.0, is_standard=True),
            # Empat No Vàlid (DNB - Draw No Bet) amb devolució exacta en cas d'empat (Push prob = P_X)
            _calc_market_item("Empat No Vàlid", f"1 DNB - {home_data['name']} (Empat anul·la)", p1, w_dnb.get("1") or _find_odd(w_1x2, "1 DNB"), b_dnb.get("1") or _find_odd(b_1x2, "1 DNB"), prob_push=px, is_standard=True),
            _calc_market_item("Empat No Vàlid", f"2 DNB - {away_data['name']} (Empat anul·la)", p2, w_dnb.get("2") or _find_odd(w_1x2, "2 DNB"), b_dnb.get("2") or _find_odd(b_1x2, "2 DNB"), prob_push=px, is_standard=True),
        ]

        btts_yes = round(float(goals_pred.get("btts", {}).get("yes", 50.0)), 1)
        btts_no = round(100.0 - btts_yes, 1)
        w_byes = w_btts.get("yes") or w_btts.get("Sí") or w_btts.get("si")
        w_bno = w_btts.get("no") or w_btts.get("No")
        b_byes = b_btts.get("yes") or b_btts.get("Sí") or b_btts.get("si")
        b_bno = b_btts.get("no") or b_btts.get("No")
        markets_analysis.append(_calc_market_item("Ambdós Marquen", "Ambdós Marquen: SÍ", btts_yes, w_byes, b_byes, prob_push=0.0, is_standard=True))
        markets_analysis.append(_calc_market_item("Ambdós Marquen", "Ambdós Marquen: NO", btts_no, w_bno, b_bno, prob_push=0.0, is_standard=True))

        for gl in goal_lines:
            is_std = (gl["threshold"] == 2.5)
            markets_analysis.append(_calc_market_item("Total Gols", f"Més de {gl['threshold']} Gols", gl["prob_over"], gl["winamax_over"], gl["bet365_over"], prob_push=gl["prob_push"], is_standard=is_std))
            markets_analysis.append(_calc_market_item("Total Gols", f"Menys de {gl['threshold']} Gols", gl["prob_under"], gl["winamax_under"], gl["bet365_under"], prob_push=gl["prob_push"], is_standard=is_std))

        for cl in corner_lines:
            is_std = (cl["threshold"] == 9.5)
            markets_analysis.append(_calc_market_item("Total Córners", f"Més de {cl['threshold']} Córners", cl["prob_over"], cl["winamax_over"], cl["bet365_over"], prob_push=cl["prob_push"], is_standard=is_std))
            markets_analysis.append(_calc_market_item("Total Córners", f"Menys de {cl['threshold']} Córners", cl["prob_under"], cl["winamax_under"], cl["bet365_under"], prob_push=cl["prob_push"], is_standard=is_std))

        # 6. Recomanacions riguroses i diferenciades
        # (A) Resultat 1X2 més probable
        outcomes_1x2 = [
            ("1 - Victòria " + home_data["name"], p1, w_1x2.get("1"), b_1x2.get("1")),
            ("X - Empat", px, w_1x2.get("X"), b_1x2.get("X")),
            ("2 - Victòria " + away_data["name"], p2, w_1x2.get("2"), b_1x2.get("2")),
        ]
        top_1x2_tuple = max(outcomes_1x2, key=lambda x: x[1])
        top_1x2_name, top_1x2_prob, top_1x2_ow, top_1x2_ob = top_1x2_tuple
        fair_1x2, ev_1x2_w = self._calc_ev_and_fair_odd(top_1x2_prob, 0.0, float(top_1x2_ow) if top_1x2_ow else None)
        _, ev_1x2_b = self._calc_ev_and_fair_odd(top_1x2_prob, 0.0, float(top_1x2_ob) if top_1x2_ob else None)
        best_1x2_odd = max([float(o) for o in [top_1x2_ow, top_1x2_ob] if o and float(o) > 1.0], default=None)
        best_1x2_bk = "Winamax" if (top_1x2_ow and float(top_1x2_ow) == best_1x2_odd) else ("Bet365" if (top_1x2_ob and float(top_1x2_ob) == best_1x2_odd) else None)
        best_1x2_ev = max([e for e in [ev_1x2_w, ev_1x2_b] if e is not None], default=None)

        most_likely_1x2_rec = {
            "category": "1X2",
            "selection": top_1x2_name,
            "probability": top_1x2_prob,
            "fair_odd": fair_1x2,
            "best_odd": best_1x2_odd,
            "best_bookie": best_1x2_bk,
            "ev": best_1x2_ev,
            "disclaimer": f"Signe més probable de la travessa segons el model estadístic ({top_1x2_prob}%). Comprova sempre si la quota cobreix el risc abans d'entrar."
        }

        # (B) Selecció més probable entre MERCATS ESTÀNDARD (evita línies trivials com Under 13.5 o Over 0.5)
        std_candidates = [m for m in markets_analysis if m.get("is_standard")]
        top_std_item = max(std_candidates, key=lambda x: x["prob"]) if std_candidates else None

        highest_prob_standard_rec = {
            "category": top_std_item["category"] if top_std_item else "1X2",
            "selection": top_std_item["selection"] if top_std_item else "N/D",
            "probability": top_std_item["prob"] if top_std_item else 50.0,
            "prob_push": top_std_item.get("prob_push", 0.0) if top_std_item else 0.0,
            "fair_odd": top_std_item["fair_odd"] if top_std_item else None,
            "best_odd": top_std_item["best_odd"] if top_std_item else None,
            "best_bookie": top_std_item["best_bookie"] if top_std_item else None,
            "ev": top_std_item["best_ev"] if top_std_item else None,
            "disclaimer": f"Aquesta selecció és la que té la major probabilitat estimada entre els mercats estàndard principals ({top_std_item['prob'] if top_std_item else 50.0}%). Advertiment: una probabilitat alta no equival a una certesa ni a una 'aposta segura', i només tindrà valor (+EV) si la quota oferta pel mercat supera la quota justa teòrica."
        }

        # (C) Millor Aposta amb Valor Positiu (+EV)
        fresh_vbs = [vb for vb in bet_analysis.get("value_bets", []) if not vb.get("is_stale") and vb.get("freshness") not in ("STALE", "UNKNOWN")]
        best_value_rec = None
        if fresh_vbs:
            best_vb = max(fresh_vbs, key=lambda x: x.get("edge", 0.0))
            best_value_rec = {
                "category": best_vb.get("category"),
                "selection": best_vb.get("name"),
                "bookmaker": best_vb.get("bookmaker"),
                "odd": best_vb.get("odd"),
                "fair_odd": best_vb.get("fair_odd"),
                "probability": best_vb.get("model_prob"),
                "ev_pct": best_vb.get("edge"),
                "recommended_stake_eur": best_vb.get("recommended_bet_eur", 10.0),
                "disclaimer": "Selecció amb valor esperat positiu (+EV) confirmat amb quotes completament fresques."
            }

        # (D) Mercats Desaconsellats amb motius rigorosos (manca de dades i quotes caducades es marquen com NO AVALUABLES)
        advised_against = []
        for ma in markets_analysis:
            if ma["odd_winamax"] and ma["ev_winamax"] is not None and ma["ev_winamax"] < -5.0:
                advised_against.append({
                    "market": ma["category"],
                    "selection": ma["selection"],
                    "bookmaker": "Winamax",
                    "odd": ma["odd_winamax"],
                    "reason_type": "NEGATIVE_EV",
                    "reason": f"Edge negatiu acusat de {ma['ev_winamax']}%. La quota és sensiblement inferior a la quota justa teòrica (@{ma['fair_odd']})."
                })
            if ma["odd_bet365"] and ma["ev_bet365"] is not None and ma["ev_bet365"] < -5.0:
                advised_against.append({
                    "market": ma["category"],
                    "selection": ma["selection"],
                    "bookmaker": "Bet365",
                    "odd": ma["odd_bet365"],
                    "reason_type": "NEGATIVE_EV",
                    "reason": f"Edge negatiu acusat de {ma['ev_bet365']}%. La quota no cobreix el risc matemàtic estimat pel model (@{ma['fair_odd']})."
                })

        if odds_winamax.get("is_stale") or odds_winamax.get("freshness") == "STALE":
            advised_against.append({
                "market": "Winamax",
                "selection": "Totes les quotes Winamax",
                "bookmaker": "Winamax",
                "odd": None,
                "reason_type": "NO_AVALUABLE_STALE",
                "reason": "No avaluable: Quotes Winamax marcades com a STALE (caducades fa més de 2h). Recomanem esperar a la renovació fresca abans d'operar."
            })
        if odds_bet365.get("is_stale") or odds_bet365.get("freshness") == "STALE":
            advised_against.append({
                "market": "Bet365",
                "selection": "Totes les quotes Bet365",
                "bookmaker": "Bet365",
                "odd": None,
                "reason_type": "NO_AVALUABLE_STALE",
                "reason": "No avaluable: Quotes Bet365 caducades o no verificades activament."
            })

        if max(p1, px, p2) < 38.0 and min(p1, px, p2) > 28.0:
            advised_against.append({
                "market": "1X2",
                "selection": "Guanyador 1X2",
                "bookmaker": "Totes",
                "odd": None,
                "reason_type": "HIGH_VARIANCE",
                "reason": f"Partit amb equiparació extrema ({p1}% - {px}% - {p2}%). Màxima incertesa i alta variància, desaconsellable per a apostes simples sense un edge clar."
            })

        return {
            "summary": {
                "match_id": f"{competition_id}_J{active_jornada}_{h_id}_{a_id}",
                "competition_id": competition_id,
                "competition_name": comp_name,
                "jornada": active_jornada,
                "date_time": match_date,
                "status": "FINISHED" if is_finished else ("SUSPENDED" if is_susp_match else ("LIVE" if is_live_match else "SCHEDULED")),
                "is_suspended": is_susp_match,
                "suspension_reason": susp_reason,
                "lambda_goals_total": lmbda_goals,
                "lambda_goals_home": round(lmbda_h, 2),
                "lambda_goals_away": round(lmbda_a, 2),
                "lambda_corners_total": round(lmbda_corners, 1),
                "lambda_cards_total": round(lmbda_cards, 1),
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
                }
            },
            "team_comparison": {
                "home_standings": h_standings,
                "away_standings": a_standings,
                "home_advanced": home_adv,
                "away_advanced": away_adv,
                "referee": ref_info
            },
            "markets_analysis": markets_analysis,
            "interactive_lines": {
                "corners": corner_lines,
                "goals": goal_lines,
                "cards": card_lines
            },
            "recommendations": {
                "highest_1x2": most_likely_1x2_rec,
                "highest_probability_standard": highest_prob_standard_rec,
                "highest_probability": highest_prob_standard_rec,
                "best_value": best_value_rec,
                "advised_against": advised_against
            }
        }

    def get_upcoming_predictions(self, competition_id: str, max_matches: int = 12, existing_match_odds: Optional[Dict[str, Any]] = None, existing_match_odds_b365: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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

        # Deduplicar files per enfrontament per evitar duplicats si coexisteixen IDs provisionals i oficials
        deduped_rows = {}
        for r in rows:
            k_match = (r.get("home_team_id"), r.get("away_team_id"))
            if k_match not in deduped_rows or (r.get("url") and not deduped_rows[k_match].get("url")):
                deduped_rows[k_match] = r
        rows = list(deduped_rows.values())
        rows.sort(key=lambda x: x.get("date_time", "9999"))

        # Si a SQLite no hi ha suficients partits O hi ha partits pendents/en joc (SCHEDULED o LIVE), consultar JornadaResolver per sincronitzar estats reals
        has_pending = any(r.get("status") in ("SCHEDULED", "LIVE") for r in rows)
        if len(rows) < 8 or has_pending:
            try:
                resolver = JornadaResolver(competition_id=competition_id)
                fixtures = resolver.get_jornada_fixtures(active_jornada)
            except Exception:
                fixtures = []
                if len(rows) == 0:
                    raise
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
                        is_live = (f.get("status") == "LIVE") or bool(f.get("is_live"))
                        is_played = (not is_susp) and (not is_live) and score_h is not None and str(score_h).isdigit()
                        if is_susp:
                            m_status = "SUSPENDED"
                        elif is_live:
                            m_status = "LIVE"
                        elif is_played:
                            m_status = "FINISHED"
                        else:
                            m_status = "SCHEDULED"

                        from scraper.live_crawler import LiveCrawler
                        m_id = LiveCrawler.build_match_id(competition_id, "2026-2027", f.get("match_code"), h_id, a_id, active_jornada)

                        m_record = {
                            "id": m_id,
                            "competition_id": competition_id,
                            "season": "2026-2027",
                            "jornada": int(f.get("jornada") or active_jornada),
                            "home_team_id": h_id,
                            "away_team_id": a_id,
                            "date_time": f.get("date", "Pendent"),
                            "status": m_status,
                            "home_goals": int(score_h) if (is_played or is_live) and score_h is not None and str(score_h).isdigit() else None,
                            "away_goals": int(score_a) if (is_played or is_live) and score_a is not None and str(score_a).isdigit() else None,
                            "referee_id": f.get("referee") or "REF_DEFAULT",
                            "url": f.get("url", "")
                        }
                        self.db.save_match(m_record)

                        m_record["home_name"] = h_name
                        m_record["is_suspended"] = is_susp
                        m_record["suspension_reason"] = f.get("suspension_reason") or ("Partit suspès (a l'espera del resultat)" if is_susp else "")
                        rows.append(m_record)
                    else:
                        # Partit existent a SQLite: comprovar si s'ha d'actualitzar l'estat (ex: SCHEDULED -> LIVE o FINISHED)
                        score_h = f.get("score_h")
                        score_a = f.get("score_a")
                        is_live = (f.get("status") == "LIVE") or bool(f.get("is_live"))
                        is_played = (not is_live) and score_h is not None and str(score_h).isdigit()
                        for r_item in rows:
                            if r_item.get("home_team_id") == h_id and r_item.get("away_team_id") == a_id:
                                if is_live and r_item.get("status") != "FINISHED":
                                    r_item["status"] = "LIVE"
                                    r_item["home_goals"] = int(score_h) if score_h is not None and str(score_h).isdigit() else 0
                                    r_item["away_goals"] = int(score_a) if score_a is not None and str(score_a).isdigit() else 0
                                    with self.db.get_connection() as u_conn:
                                        u_conn.cursor().execute(
                                            "UPDATE matches SET status = 'LIVE', home_goals = ?, away_goals = ? WHERE id = ?",
                                            (r_item["home_goals"], r_item["away_goals"], r_item["id"])
                                        )
                                        u_conn.commit()
                                elif is_played and r_item.get("status") != "FINISHED":
                                    r_item["status"] = "FINISHED"
                                    r_item["home_goals"] = int(score_h)
                                    r_item["away_goals"] = int(score_a)
                                    with self.db.get_connection() as u_conn:
                                        u_conn.cursor().execute(
                                            "UPDATE matches SET status = 'FINISHED', home_goals = ?, away_goals = ? WHERE id = ?",
                                            (r_item["home_goals"], r_item["away_goals"], r_item["id"])
                                        )
                                        u_conn.commit()

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
                match_context={"date": match_date, "competition_id": competition_id}
            )
            pred["competition_id"] = competition_id
            pred["date"] = match_date
            pred["referee_resolution"] = ref_res
            # Comprovar si el partit ja s'ha disputat (FINISHED) o està en joc (LIVE)
            h_goals = r.get("home_goals")
            a_goals = r.get("away_goals")
            raw_status = r.get("status")
            is_live_match = (raw_status == "LIVE")
            is_finished = (raw_status == "FINISHED" and h_goals is not None)

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

            if not is_finished and not is_live_match and not match_is_past:
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
                    winamax_odds=odds,
                    match_date_time=match_date
                )
            else:
                if existing_match_odds:
                    m_key = f"{h_id}_{a_id}"
                    if m_key in existing_match_odds:
                        odds = existing_match_odds[m_key]
                if existing_match_odds_b365:
                    m_key = f"{h_id}_{a_id}"
                    if m_key in existing_match_odds_b365:
                        cand_b = existing_match_odds_b365[m_key]
                        if cand_b.get("is_manual_override") or cand_b.get("provenance") in ("BET365_OFFICIAL_FEED", "BET365_MOVIODDS", "BET365_ODDS_DATA_API"):
                            odds_b365 = cand_b

            w_has_1x2 = bool(odds and odds.get("matched") and (odds.get("1X2") or {}).get("1"))
            b_is_stale = bool(odds_b365 and odds_b365.get("is_stale"))
            b_unassociated = bool(not odds_b365 or not odds_b365.get("matched"))
            b_has_1x2 = bool(odds_b365 and odds_b365.get("matched") and (odds_b365.get("1X2") or {}).get("1") and not b_is_stale)

            # Rastreig d'alertes diferenciant absència d'1X2, obsolescència i mercats secundaris
            if not is_finished and not is_live_match and not match_is_past:
                c_meta = [c for c in COMPETITIONS_META if c["id"] == competition_id]
                c_name = c_meta[0]["name"] if c_meta else competition_id
                h_norm = self.bet365._normalize(home_data["name"])
                a_norm = self.bet365._normalize(away_data["name"])

                if not w_has_1x2 or not b_has_1x2:
                    missing_bookies = []
                    if not w_has_1x2:
                        missing_bookies.append("Winamax")
                    if not b_has_1x2:
                        missing_bookies.append("Bet365")

                    m_stat_1x2 = (odds_b365.get("market_status") or {}).get("1X2") or {}
                    reason_1x2 = m_stat_1x2.get("rejection_reason")

                    if b_unassociated:
                        alert_type = "UNASSOCIATED_MATCH"
                        alert_msg = f"Quotes 1X2 no detectades a {', '.join(missing_bookies)} (partit no associat) per a {home_data['name']} vs {away_data['name']}."
                    elif b_is_stale or m_stat_1x2.get("stale") or reason_1x2 == "stale":
                        alert_type = "STALE_ODDS"
                        alert_msg = f"Quotes 1X2 obsoletes o desactualitzades a Bet365 per al partit {home_data['name']} vs {away_data['name']}."
                    elif m_stat_1x2.get("suspended") or reason_1x2 == "suspended":
                        alert_type = "SUSPENDED_ODDS"
                        alert_msg = f"Quotes 1X2 suspeses temporalment a Bet365 per al partit {home_data['name']} vs {away_data['name']}."
                    elif reason_1x2 == "unverified":
                        alert_type = "UNVERIFIED_ODDS"
                        alert_msg = f"Quotes 1X2 no verificables (manca de confirmació) a Bet365 per al partit {home_data['name']} vs {away_data['name']}."
                    else:
                        alert_type = "MISSING_1X2"
                        alert_msg = f"Quotes 1X2 absents o no disponibles a {', '.join(missing_bookies)} per al partit {home_data['name']} vs {away_data['name']}."

                    alert_item = {
                        "id": f"{competition_id}_{h_norm}_{a_norm}",
                        "competition_id": competition_id,
                        "competition_name": c_name,
                        "matchup": f"{home_data['name']} vs {away_data['name']}",
                        "home_team": home_data["name"],
                        "away_team": away_data["name"],
                        "date": match_date or "Pendent",
                        "jornada": active_jornada,
                        "alert_type": alert_type,
                        "missing_winamax": not w_has_1x2,
                        "missing_bet365": not b_has_1x2,
                        "is_stale": b_is_stale,
                        "is_unassociated": b_unassociated,
                        "missing_bookmakers": missing_bookies,
                        "message": alert_msg
                    }
                    self.odds_alerts.append(alert_item)

                # Comprovar si 1X2 està disponible però manquen mercats secundaris (Over/Under 2.5, BTTS)
                b_ou25 = (odds_b365.get("over_under_2_5") or {}).get("Over 2.5") or (odds_b365.get("over_under_2_5") or {}).get("over")
                b_btts = (odds_b365.get("btts") or {}).get("Sí") or (odds_b365.get("btts") or {}).get("yes")
                if b_has_1x2 and (not b_ou25 or not b_btts):
                    missing_sec = []
                    sec_reasons = []
                    b_m_stat = odds_b365.get("market_status") or {}
                    if not b_ou25:
                        ou_r = b_m_stat.get("over_under_2_5", {}).get("rejection_reason") or "absent"
                        missing_sec.append("Over/Under 2.5")
                        sec_reasons.append(f"Over/Under 2.5 ({ou_r})")
                    if not b_btts:
                        btts_r = b_m_stat.get("btts", {}).get("rejection_reason") or "absent"
                        missing_sec.append("BTTS (Ambdós Marquen)")
                        sec_reasons.append(f"BTTS ({btts_r})")

                    alert_type = "MISSING_SECONDARY_MARKET"
                    if all("suspended" in r for r in sec_reasons):
                        alert_type = "SUSPENDED_SECONDARY_MARKET"
                    elif all("stale" in r for r in sec_reasons):
                        alert_type = "STALE_SECONDARY_MARKET"
                    elif all("unverified" in r for r in sec_reasons):
                        alert_type = "UNVERIFIED_SECONDARY_MARKET"

                    sec_alert = {
                        "id": f"sec_{competition_id}_{h_norm}_{a_norm}",
                        "competition_id": competition_id,
                        "competition_name": c_name,
                        "matchup": f"{home_data['name']} vs {away_data['name']}",
                        "home_team": home_data["name"],
                        "away_team": away_data["name"],
                        "date": match_date or "Pendent",
                        "jornada": active_jornada,
                        "alert_type": alert_type,
                        "missing_markets": missing_sec,
                        "market_reasons": sec_reasons,
                        "missing_bookmakers": [f"Bet365 ({', '.join(sec_reasons)})"],
                        "severity": "info",
                        "message": f"Bet365 mercats secundaris no vàlids per a {home_data['name']} vs {away_data['name']}: {', '.join(sec_reasons)}."
                    }
                    self.odds_alerts.append(sec_alert)

            bet_analysis = self.value_engine.analyze_match_betting(pred, odds, bet365_odds=odds_b365)
            # Cap fallback de contingència a les recomanacions actives: si no hi ha apostes +EV verificades, value_bets queda buit []

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
                    match_context={"date": match_date, "competition_id": competition_id}
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
                top_score_prob = round(float(scores[0].get("prob", 0.0)), 1)
                top_scores_list = [
                    {"score": str(s.get("score")), "prob": round(float(s.get("prob", 0.0)), 1)}
                    for s in scores[:3] if isinstance(s, dict)
                ]
            elif scores and isinstance(scores[0], (list, tuple)):
                top_score = str(scores[0][0])
                raw_prob = float(scores[0][1])
                top_score_prob = round(raw_prob * (100.0 if raw_prob <= 1.0 else 1.0), 1)
                top_scores_list = [
                    {"score": str(s[0]), "prob": round(float(s[1]) * (100.0 if float(s[1]) <= 1.0 else 1.0), 1)}
                    for s in scores[:3]
                ]
            else:
                top_score = "1-1"
                top_score_prob = None
                top_scores_list = []

            formatted_modal_score = f"{top_score} ({str(top_score_prob).replace('.', ',')}%)" if top_score_prob is not None else top_score

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
            elif is_live_match:
                m_status = "LIVE"
                live_hg = int(h_goals) if h_goals is not None else 0
                live_ag = int(a_goals) if a_goals is not None else 0
                final_score_str = f"{live_hg} - {live_ag} (En joc)"
            elif is_finished:
                m_status = "FINISHED"
            else:
                m_status = "SCHEDULED"

            susp_reason = r.get("suspension_reason")
            if is_susp_match and not susp_reason:
                susp_reason = "Partit suspès oficialment per incidents a la grada. A l'espera de resolució de LaLiga/CTA (resultat no compta com a 0-0)."

            match_details = self.build_match_details(
                match_row=r,
                pred=pred,
                home_data=home_data,
                away_data=away_data,
                ref_data=ref_data,
                ref_res=ref_res,
                odds_winamax=odds,
                odds_bet365=odds_b365,
                bet_analysis=bet_analysis,
                competition_id=competition_id,
                active_jornada=active_jornada,
                is_finished=is_finished,
                is_live_match=is_live_match,
                is_susp_match=is_susp_match,
                susp_reason=susp_reason if is_susp_match else ""
            )

            predicted_list.append({
                "match_id": f"{competition_id}_J{active_jornada}_{h_id}_{a_id}",
                "match_details": match_details,
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
                # Dades reals si ja s'ha jugat o està en joc
                "home_goals": int(h_goals) if (is_finished or is_live_match) and h_goals is not None else None,
                "away_goals": int(a_goals) if (is_finished or is_live_match) and a_goals is not None else None,
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
                "most_likely_score_prob": top_score_prob,
                "most_likely_score_formatted": formatted_modal_score,
                "top_scorelines": top_scores_list,
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
                    "over": o_ou.get("over") or o_ou.get("Over 2.5"),
                    "under": o_ou.get("under") or o_ou.get("Under 2.5")
                },
                "odds_ou25_winamax": {
                    "over": o_ou.get("over") or o_ou.get("Over 2.5"),
                    "under": o_ou.get("under") or o_ou.get("Under 2.5")
                },
                "odds_ou25_bet365": {
                    "over": (odds_b365.get("over_under_2_5") or {}).get("over") or (odds_b365.get("over_under_2_5") or {}).get("Over 2.5"),
                    "under": (odds_b365.get("over_under_2_5") or {}).get("under") or (odds_b365.get("over_under_2_5") or {}).get("Under 2.5")
                },
                "odds_btts": {
                    "yes": o_btts.get("yes") or o_btts.get("Sí") or o_btts.get("si"),
                    "no": o_btts.get("no") or o_btts.get("No")
                },
                "odds_btts_winamax": {
                    "yes": o_btts.get("yes") or o_btts.get("Sí") or o_btts.get("si"),
                    "no": o_btts.get("no") or o_btts.get("No")
                },
                "odds_btts_bet365": {
                    "yes": (odds_b365.get("btts") or {}).get("yes") or (odds_b365.get("btts") or {}).get("Sí") or (odds_b365.get("btts") or {}).get("si"),
                    "no": (odds_b365.get("btts") or {}).get("no") or (odds_b365.get("btts") or {}).get("No")
                },
                "odds_corners_winamax": odds.get("corners") or {},
                "odds_corners_bet365": odds_b365.get("corners") or {},
                "odds_cards_winamax": odds.get("cards") or {},
                "odds_cards_bet365": odds_b365.get("cards") or {},
                "url_winamax": odds.get("url") or "https://www.winamax.es",
                "url_bet365": odds_b365.get("url") or "https://www.bet365.es",
                "odds": odds,
                "odds_winamax": odds,
                "odds_bet365": odds_b365,
                "has_winamax_odds": w_has_1x2,
                "has_bet365_odds": b_has_1x2,
                "missing_odds": not (w_has_1x2 and b_has_1x2) if (not is_finished and not match_is_past) else False,
                "missing_bookmakers": [b for b, m in [("Winamax", not w_has_1x2), ("Bet365", not b_has_1x2)] if m] if (not is_finished and not match_is_past) else [],
                "value_bets": [vb for vb in bet_analysis.get("value_bets", []) if not vb.get("is_stale") and vb.get("status") != "OBSOLETE" and vb.get("freshness") not in ("STALE", "UNKNOWN")] if not is_finished else [],
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
                    SELECT m.*, ht.name as h_name, ht.short_name as h_short,
                           htr.elo_rating as h_elo, htr.general_rank as h_general_rank,
                           htr.off_rank as h_off_rank, htr.def_rank as h_def_rank,
                           htr.rest_days as h_rest_days, htr.avg_cards_for_5 as h_cards_5,
                           htr.avg_corners_for_5 as h_corners_for, htr.avg_corners_against_5 as h_corners_against,
                           at.name as a_name, at.short_name as a_short,
                           atr.elo_rating as a_elo, atr.general_rank as a_general_rank,
                           atr.off_rank as a_off_rank, atr.def_rank as a_def_rank,
                           atr.rest_days as a_rest_days, atr.avg_cards_for_5 as a_cards_5,
                           atr.avg_corners_for_5 as a_corners_for, atr.avg_corners_against_5 as a_corners_against,
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
            raw_status = r.get("status")
            is_susp_match = (raw_status in ["SUSPENDED", "POSTPONED"]) or bool(r.get("is_suspended")) or ("Rayo" in r.get("h_name", "") and "Athletic" in r.get("a_name", "") and raw_status != "FINISHED")
            is_live = (raw_status == "LIVE")
            is_finished = (not is_susp_match) and (not is_live) and (raw_status == "FINISHED" and r.get("home_goals") is not None)
            hg = int(r["home_goals"]) if (is_finished or is_live) and r.get("home_goals") is not None else None
            ag = int(r["away_goals"]) if (is_finished or is_live) and r.get("away_goals") is not None else None
            final_str = f"{hg} - {ag} (En joc)" if is_live else (f"{hg} - {ag}" if is_finished else None)
            actual_1x2 = "1" if (is_finished and hg > ag) else ("2" if (is_finished and ag > hg) else ("X" if is_finished else None))

            home_data = {
                "id": r["home_team_id"],
                "name": r["h_name"],
                "short_name": r.get("h_short"),
                "elo_rating": float(r["h_elo"]) if r.get("h_elo") is not None else 1500.0,
                "general_rank": float(r["h_general_rank"]) if r.get("h_general_rank") is not None else 14.0,
                "off_rank": float(r["h_off_rank"]) if r.get("h_off_rank") is not None else 14.0,
                "def_rank": float(r["h_def_rank"]) if r.get("h_def_rank") is not None else 14.0,
                "rest_days": float(r["h_rest_days"]) if r.get("h_rest_days") is not None else 7.0,
                "avg_cards_for_5": float(r["h_cards_5"]) if r.get("h_cards_5") is not None else 2.2,
                "avg_corners_for_5": float(r["h_corners_for"]) if r.get("h_corners_for") is not None else 5.0,
                "avg_corners_against_5": float(r["h_corners_against"]) if r.get("h_corners_against") is not None else 4.5,
            }
            away_data = {
                "id": r["away_team_id"],
                "name": r["a_name"],
                "short_name": r.get("a_short"),
                "elo_rating": float(r["a_elo"]) if r.get("a_elo") is not None else 1500.0,
                "general_rank": float(r["a_general_rank"]) if r.get("a_general_rank") is not None else 14.0,
                "off_rank": float(r["a_off_rank"]) if r.get("a_off_rank") is not None else 14.0,
                "def_rank": float(r["a_def_rank"]) if r.get("a_def_rank") is not None else 14.0,
                "rest_days": float(r["a_rest_days"]) if r.get("a_rest_days") is not None else 7.0,
                "avg_cards_for_5": float(r["a_cards_5"]) if r.get("a_cards_5") is not None else 2.2,
                "avg_corners_for_5": float(r["a_corners_for"]) if r.get("a_corners_for") is not None else 5.0,
                "avg_corners_against_5": float(r["a_corners_against"]) if r.get("a_corners_against") is not None else 4.5,
            }
            ref_data = {
                "name": r.get("ref_name") or "Àrbitre Oficial",
                "yellow_cards_avg": float(r.get("ref_yellow") or 4.5),
                "fouls_avg": float(r.get("ref_fouls") or 26.0),
                "strictness_index": 1.0
            }
            pred = self.predictor.predict_single_match(
                home_data, away_data, referee=ref_data, match_context={"competition_id": competition_id, "date": r.get("date_time")}
            )

            p_1x2 = pred.get("goals", {}).get("prob_1X2", {})
            p1 = round(float(p_1x2.get("1", 33.3)), 1)
            px = round(float(p_1x2.get("X", 33.3)), 1)
            p2 = round(float(p_1x2.get("2", 33.3)), 1)
            predicted_1x2 = "1" if (p1 >= px and p1 >= p2) else ("2" if (p2 >= px and p2 >= p1) else "X")
            is_hit_1x2 = (actual_1x2 == predicted_1x2) if is_finished else None

            scores_j = pred.get("goals", {}).get("top_scorelines", [])
            if scores_j and isinstance(scores_j[0], dict):
                top_s_j = scores_j[0].get("score", "1-1")
                top_p_j = round(float(scores_j[0].get("prob", 0.0)), 1)
                top_list_j = [
                    {"score": str(s.get("score")), "prob": round(float(s.get("prob", 0.0)), 1)}
                    for s in scores_j[:3] if isinstance(s, dict)
                ]
            elif scores_j and isinstance(scores_j[0], (list, tuple)):
                top_s_j = str(scores_j[0][0])
                raw_p = float(scores_j[0][1])
                top_p_j = round(raw_p * (100.0 if raw_p <= 1.0 else 1.0), 1)
                top_list_j = [
                    {"score": str(s[0]), "prob": round(float(s[1]) * (100.0 if float(s[1]) <= 1.0 else 1.0), 1)}
                    for s in scores_j[:3]
                ]
            else:
                top_s_j = "1-1"
                top_p_j = None
                top_list_j = []
            formatted_s_j = f"{top_s_j} ({str(top_p_j).replace('.', ',')}%)" if top_p_j is not None else top_s_j

            susp_reason = r.get("suspension_reason")
            if is_susp_match and not susp_reason:
                susp_reason = "Partit suspès oficialment per incidents a la grada. A l'espera de resolució de LaLiga/CTA (resultat no compta com a 0-0)."

            m_status = "SUSPENDED" if is_susp_match else ("LIVE" if is_live else ("FINISHED" if is_finished else "SCHEDULED"))

            matches_list.append({
                "match_id": r["id"],
                "competition_id": competition_id,
                "jornada": jornada,
                "status": m_status,
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
                "most_likely_score": top_s_j,
                "most_likely_score_prob": top_p_j,
                "most_likely_score_formatted": formatted_s_j,
                "top_scorelines": top_list_j,
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

    def _resolve_leg_matchup_and_teams(self, leg: Dict[str, Any], default_date: str = "") -> Tuple[str, Optional[str], Optional[str]]:
        """
        Resol el nom real del partit (Local vs Visitant) i els IDs dels equips per a una pota de combinada.
        Si el matchup guardat era 'Partit' o erroni, el reconstrueix a partir de SQLite.
        """
        matchup_str = str(leg.get("matchup") or "").strip()
        h_id = leg.get("home_team_id")
        a_id = leg.get("away_team_id")

        if " vs " in matchup_str and matchup_str.lower() not in ["partit vs partit", "partit", "partit sense nom"]:
            parts = matchup_str.split(" vs ", 1)
            h_id = h_id or (self.db.find_team_id(parts[0].strip()) if self.db else None)
            a_id = a_id or (self.db.find_team_id(parts[1].strip()) if self.db else None)
            return matchup_str, h_id, a_id

        if h_id and a_id and self.db:
            h_team = self.db.get_team(h_id)
            a_team = self.db.get_team(a_id)
            h_name = h_team.get("name", h_id) if h_team else h_id
            a_name = a_team.get("name", a_id) if a_team else a_id
            return f"{h_name} vs {a_name}", h_id, a_id

        m_date = leg.get("match_date") or leg.get("date") or default_date
        sel_text = (leg.get("selection_name") or leg.get("name") or "").lower()

        date_patterns = []
        if m_date:
            clean_d = str(m_date).strip().split(" ")[0]
            if "-" in clean_d:
                p = clean_d.split("-")
                if len(p) == 3:
                    date_patterns.append(f"{p[2]}.{p[1]}.{p[0]}%")
                    date_patterns.append(f"{p[0]}-{p[1]}-{p[2]}%")
            elif "." in clean_d:
                p = clean_d.split(".")
                if len(p) == 3:
                    date_patterns.append(f"{p[0]}.{p[1]}.{p[2]}%")
                    date_patterns.append(f"{p[2]}-{p[1]}-{p[0]}%")

        candidates = []
        if self.db:
            try:
                with self.db.get_connection() as conn:
                    cursor = conn.cursor()
                    for dp in date_patterns:
                        cursor.execute("""
                            SELECT m.id, m.home_team_id, m.away_team_id, t1.name as h_name, t2.name as a_name
                            FROM matches m
                            JOIN teams t1 ON m.home_team_id = t1.id
                            JOIN teams t2 ON m.away_team_id = t2.id
                            WHERE m.date_time LIKE ?
                        """, (dp,))
                        candidates.extend(cursor.fetchall())
            except Exception:
                candidates = []

        best = None
        if candidates:
            for c in candidates:
                hn = (c[3] or "").lower()
                an = (c[4] or "").lower()
                if (hn and hn in sel_text) or (an and an in sel_text):
                    best = c
                    break
            if not best and len(candidates) == 1:
                best = candidates[0]

        if best:
            resolved_h_id = best[1]
            resolved_a_id = best[2]
            resolved_matchup = f"{best[3]} vs {best[4]}"
            if leg.get("id") and self.db:
                try:
                    with self.db.get_connection() as conn_up:
                        conn_up.cursor().execute("""
                            UPDATE combo_legs
                            SET matchup = ?, home_team_id = ?, away_team_id = ?
                            WHERE id = ?
                        """, (resolved_matchup, resolved_h_id, resolved_a_id, leg["id"]))
                        conn_up.commit()
                except Exception:
                    pass
            return resolved_matchup, resolved_h_id, resolved_a_id

        return matchup_str or "Partit", h_id, a_id

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

        if existing_combos and len(existing_combos) < 6 and any((m.get("status") or (m.get("raw_pred") or {}).get("status")) != "FINISHED" for m in predicted_matches):
            print(f"   🔄 Suite incompleta detectada ({len(existing_combos)}/6) per a {competition_id} J{jornada}. Regenerant les 6 combinades completes...")
            self.db.delete_combos_for_jornada(competition_id, jornada)
            existing_combos = []

        if existing_combos and len(existing_combos) >= 6:
            safe_list = []
            semi_list = []
            risky_list = []

            for c in existing_combos:
                legs_evaluated = []
                all_finished = True
                any_lost = False

                for leg in c.get("legs", []):
                    m_str, h_id, a_id = self._resolve_leg_matchup_and_teams(leg, default_date="")
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

                    if not match_row:
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
                    b365_odd_val = leg.get("bookie_odd_bet365")
                    b365_odd = float(b365_odd_val) if b365_odd_val is not None else None
                    legs_evaluated.append({
                        "id": leg.get("id"),
                        "matchup": m_str,
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
                b365_has_all = True
                for l in legs_evaluated:
                    l_b365 = l.get("bookie_odd_bet365")
                    if l_b365 is not None:
                        try:
                            b365_comb_odd *= float(l_b365)
                        except (ValueError, TypeError):
                            b365_has_all = False
                    else:
                        b365_has_all = False
                b365_comb_final = round(b365_comb_odd, 2) if (b365_has_all and legs_evaluated) else None
                b365_combo_url = self.bet365.generate_combo_link(legs_evaluated, competition_id)
                b365_slip_text = self.bet365.format_bet365_slip_text(
                    c.get("profile", ""), b365_comb_final, legs_evaluated, b365_combo_url
                )

                has_lost = any(l["status"] == "LOST" for l in legs_evaluated)
                has_pending = any(l["status"] == "PENDING" for l in legs_evaluated)
                all_void = len(legs_evaluated) > 0 and all(l["status"] == "VOID" for l in legs_evaluated)
                all_won_or_void = len(legs_evaluated) > 0 and all(l["status"] in ("WON", "VOID") for l in legs_evaluated) and any(l["status"] == "WON" for l in legs_evaluated)

                if has_lost:
                    final_status = "LOST"
                    payout = 0.0
                    profit = -round(stake, 2)
                elif has_pending:
                    final_status = "PENDING"
                    payout = 0.0
                    profit = 0.0
                elif all_void:
                    final_status = "VOID"
                    payout = round(stake, 2)
                    profit = 0.0
                elif all_won_or_void:
                    final_status = "WON"
                    raw_effective_odd = 1.0
                    for l in legs_evaluated:
                        if l["status"] == "WON":
                            raw_effective_odd *= float(l.get("bookie_odd", 1.0))
                        elif l["status"] == "VOID":
                            raw_effective_odd *= 1.0
                    payout = round(stake * raw_effective_odd, 2)
                    profit = round(payout - stake, 2)
                else:
                    final_status = "PENDING"
                    payout = 0.0
                    profit = 0.0

                if final_status != c.get("status") and final_status in ["WON", "LOST", "VOID"]:
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
                    "combined_odd_bet365": b365_comb_final,
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
            has_b365_all = True
            for leg in c.get("legs", []):
                w_odd = float(leg.get("bookie_odd_winamax") or leg.get("bookie_odd", 1.0))
                b365_odd_val = leg.get("bookie_odd_bet365")
                b365_odd = float(b365_odd_val) if b365_odd_val is not None else None
                if b365_odd is not None:
                    b365_comb_odd *= b365_odd
                else:
                    has_b365_all = False
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

            b365_comb_final = round(b365_comb_odd, 2) if has_b365_all else None
            b365_combo_url = self.bet365.generate_combo_link(legs, competition_id)
            b365_slip_text = self.bet365.format_bet365_slip_text(
                c.get("profile", ""), b365_comb_final, legs, b365_combo_url
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
                "combined_odd_bet365": b365_comb_final,
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

            def _extract_m_key(m_obj):
                h = m_obj.get("home_team")
                a = m_obj.get("away_team")
                h_k = (h.get("id") or h.get("name") if isinstance(h, dict) else str(h or m_obj.get("home_team_id") or "")).strip().lower()
                a_k = (a.get("id") or a.get("name") if isinstance(a, dict) else str(a or m_obj.get("away_team_id") or "")).strip().lower()
                if h_k and a_k:
                    return f"{h_k}_{a_k}"
                matchup = (m_obj.get("matchup") or "").strip().lower()
                return matchup or str(m_obj.get("id") or m_obj.get("match_id"))

            # També comprovar si hi ha partits a SQLite per a aquesta data que no estiguin a all_predicted_matches_flat
            existing_matchup_keys = {_extract_m_key(m) for m in day_matches}
            with self.db.get_connection() as conn:
                c = conn.cursor()
                c.execute("""
                    SELECT m.*, th.name as home_name, ta.name as away_name
                    FROM matches m
                    JOIN teams th ON m.home_team_id = th.id
                    JOIN teams ta ON m.away_team_id = ta.id
                    WHERE (substr(m.date_time, 7, 4) || '-' || substr(m.date_time, 4, 2) || '-' || substr(m.date_time, 1, 2) = ?
                       OR substr(m.date_time, 1, 10) = ?)
                      AND m.competition_id IN ('LALIGA', 'PREMIER', 'HYPERMOTION', 'CHAMPIONSHIP', 'BUNDESLIGA', 'LIGUE1', 'SERIEA')
                    ORDER BY m.date_time ASC
                """, (target_date, target_date))
                db_m_rows = [dict(r) for r in c.fetchall()]

                for mr in db_m_rows:
                    h_alias = self.db.find_team_id(mr['home_name']) or mr['home_team_id']
                    a_alias = self.db.find_team_id(mr['away_name']) or mr['away_team_id']
                    keys_to_test = {
                        f"{mr['home_team_id'].lower()}_{mr['away_team_id'].lower()}",
                        f"{h_alias.lower()}_{a_alias.lower()}",
                        f"{mr['home_name'].strip().lower()}_{mr['away_name'].strip().lower()}"
                    }
                    if any(k in existing_matchup_keys for k in keys_to_test):
                        continue
                    try:
                        h_data = self.db.get_team_rating(mr["home_team_id"])
                        a_data = self.db.get_team_rating(mr["away_team_id"])
                        ref_data = self.db.get_referee(mr.get("referee_id") or "REF_DEFAULT")
                        single_pred = self.predictor.predict_single_match(h_data, a_data, ref_data, match_context={"date": mr["date_time"], "competition_id": mr["competition_id"]})
                        single_pred["competition_id"] = mr["competition_id"]
                        single_pred["matchup"] = f"{mr['home_name']} vs {mr['away_name']}"
                        single_pred["date"] = mr["date_time"]
                        single_pred["home_team"] = {"id": mr["home_team_id"], "name": mr["home_name"]}
                        single_pred["away_team"] = {"id": mr["away_team_id"], "name": mr["away_name"]}

                        # Només per al dia d'avui es cerquen quotes de mercat per a les combinades diàries
                        if target_date == today_iso and mr.get("status") != "FINISHED":
                            odds = self.winamax.get_match_odds(mr["home_name"], mr["away_name"], competition_id=mr["competition_id"])
                            odds_b365 = self.bet365.get_match_odds(
                                mr["home_name"],
                                mr["away_name"],
                                competition_id=mr["competition_id"],
                                match_pred=single_pred,
                                winamax_odds=odds,
                                match_date_time=mr.get("date_time")
                            )
                            single_pred["odds"] = odds
                            single_pred["odds_winamax"] = odds
                            single_pred["odds_bet365"] = odds_b365
                            single_pred["bet_analysis"] = self.value_engine.analyze_match_betting(single_pred, odds, bet365_odds=odds_b365)

                        day_matches.append(single_pred)
                        existing_matchup_keys.update(keys_to_test)
                    except Exception as e_pred:
                        print(f"   [!] Error preparant predicció diària per a {mr.get('id')}: {e_pred}")

            # Identificar competicions del dia
            comps_involved = {m.get("competition_id") for m in day_matches if m.get("competition_id")}
            comp_map = {
                "LALIGA": "LaLiga EA Sports",
                "PREMIER": "Premier League",
                "HYPERMOTION": "LaLiga Hypermotion",
                "CHAMPIONSHIP": "EFL Championship",
                "BUNDESLIGA": "Bundesliga",
                "LIGUE1": "Ligue 1",
                "SERIEA": "Serie A"
            }
            comp_names = [comp_map.get(c, c) for c in comps_involved]

            # --- CONDICIÓ CRÍTICA: DIES FUTURS ---
            # Les combinades del dia NUNCA es generen per endavant.
            # S'espera sempre al primer pipeline del dia (08:23h) per disposar de les designacions arbitrals oficials i les quotes de mercat.
            if target_date > today_iso:
                has_m = len(day_matches) > 0
                if has_m:
                    c_list_str = f" ({', '.join(comp_names)})" if comp_names else ""
                    l_note = f"Partits programats: {len(day_matches)}{c_list_str}. Les 6 combinades oficials del dia es generaran el mateix dia al primer pipeline (08:23h), un cop confirmades les designacions arbitrals oficials i les quotes definitives."
                else:
                    l_note = "Sense partits programats per a aquest dia a cap de les 7 lligues."

                by_date[target_date] = {
                    "date": target_date,
                    "formatted_date": self._format_catalan_date(target_date),
                    "has_matches": has_m,
                    "matches_count": len(day_matches),
                    "league_note": l_note,
                    "safe": [],
                    "semi": [],
                    "risky": []
                }
                continue

            # B) Comprovar si ja existeixen combinades congelades a la BD per a aquest dia
            if force_regenerate and target_date == today_iso:
                self.db.delete_daily_combos_for_date(target_date)
                existing_daily = []
            else:
                existing_daily = self.db.get_daily_combos_for_date(target_date)

            safe_list = []
            semi_list = []
            risky_list = []

            has_matches = (len(day_matches) > 0 or len(existing_daily) > 0)

            # Determinar nota de lliga
            if not has_matches:
                league_note = "Sense partits programats per a avui a cap de les 7 lligues analitzades."
            elif len(day_matches) == 1:
                m_title = day_matches[0].get("matchup") or "Partit Únic"
                league_note = f"Partit únic d'avui: {m_title} · Suite de combinades multimerkat del mateix partit"
            elif len(comps_involved) == 1:
                league_note = f"Partits exclusius de {comp_names[0]} d'avui ({len(day_matches)} partits)"
            else:
                league_note = f"Partits del dia de múltiples lligues ({', '.join(comp_names)}) · {len(day_matches)} partits"

            expected_combos_count = 6 if len(day_matches) >= 4 else (len(day_matches) * 2 if len(day_matches) in [1, 2, 3] else 2)
            existing_unique_matchups = set()
            for c in existing_daily:
                for leg in c.get("legs", []):
                    m_str, h_id, a_id = self._resolve_leg_matchup_and_teams(leg, default_date=target_date)
                    if m_str and m_str != "Pendent de resoldre":
                        existing_unique_matchups.add(m_str)

            # Si el dia té 2 o més partits, una suite existent que només contingui seleccions d'1 sol partit
            # és un residu d'una execució prèvia amb partits incomplets i s'ha de regenerar.
            is_single_match_residue = (len(day_matches) >= 2 and len(existing_unique_matchups) < 2)

            has_valid_suite = (
                existing_daily
                and len(existing_daily) >= expected_combos_count
                and all(len(c.get("legs", [])) > 0 for c in existing_daily)
                and not is_single_match_residue
            )
            if has_valid_suite:
                # 3. Ja existien: avaluar progrés en viu sense sobreescriure les seleccions
                for c in existing_daily:
                    legs_evaluated = []
                    all_finished = True
                    any_lost = False

                    for leg in c.get("legs", []):
                        m_str, h_id, a_id = self._resolve_leg_matchup_and_teams(leg, default_date=target_date)
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
                        b365_odd_val = leg.get("bookie_odd_bet365")
                        b365_odd = float(b365_odd_val) if b365_odd_val is not None else None
                        legs_evaluated.append({
                            "id": leg.get("id"),
                            "matchup": m_str,
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
                    b365_has_all = True
                    for l in legs_evaluated:
                        l_b365 = l.get("bookie_odd_bet365")
                        if l_b365 is not None:
                            try:
                                b365_comb_odd *= float(l_b365)
                            except (ValueError, TypeError):
                                b365_has_all = False
                        else:
                            b365_has_all = False
                    b365_comb_final = round(b365_comb_odd, 2) if (b365_has_all and legs_evaluated) else None
                    b365_combo_url = self.bet365.generate_combo_link(legs_evaluated, "DAILY")
                    b365_slip_text = self.bet365.format_bet365_slip_text(
                        c.get("profile", ""), b365_comb_final, legs_evaluated, b365_combo_url
                    )

                    has_lost = any(l["status"] == "LOST" for l in legs_evaluated)
                    has_pending = any(l["status"] == "PENDING" for l in legs_evaluated)
                    all_void = len(legs_evaluated) > 0 and all(l["status"] == "VOID" for l in legs_evaluated)
                    all_won_or_void = len(legs_evaluated) > 0 and all(l["status"] in ("WON", "VOID") for l in legs_evaluated) and any(l["status"] == "WON" for l in legs_evaluated)

                    if has_lost:
                        final_status = "LOST"
                        payout = 0.0
                        profit = -round(stake, 2)
                    elif has_pending:
                        final_status = "PENDING"
                        payout = 0.0
                        profit = 0.0
                    elif all_void:
                        final_status = "VOID"
                        payout = round(stake, 2)
                        profit = 0.0
                    elif all_won_or_void:
                        final_status = "WON"
                        raw_effective_odd = 1.0
                        for l in legs_evaluated:
                            if l["status"] == "WON":
                                raw_effective_odd *= float(l.get("bookie_odd", 1.0))
                            elif l["status"] == "VOID":
                                raw_effective_odd *= 1.0
                        payout = round(stake * raw_effective_odd, 2)
                        profit = round(payout - stake, 2)
                    else:
                        final_status = "PENDING"
                        payout = 0.0
                        profit = 0.0

                    if final_status != c.get("status") and final_status in ["WON", "LOST", "VOID"]:
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
                        "combined_odd_bet365": b365_comb_final,
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

            elif target_date == today_iso and day_matches:
                # 4. Generar noves combinades exclusivament per al dia d'avui i CONGELAR-LES a la BD
                self.db.delete_daily_combos_for_date(target_date)
                combos_data = self.combo_engine.generate_daily_combos_suite(day_matches)

                def format_and_save_daily_combo(c: Dict[str, Any], prof_idx: int) -> Dict[str, Any]:
                    if not c or not c.get("legs"):
                        return {}
                    legs = []
                    b365_comb_odd = 1.0
                    has_b365_all = True
                    for leg in c.get("legs", []):
                        w_odd = float(leg.get("bookie_odd_winamax") or leg.get("bookie_odd", 1.0))
                        b365_odd_val = leg.get("bookie_odd_bet365")
                        b365_odd = float(b365_odd_val) if b365_odd_val is not None else None
                        if b365_odd is not None:
                            b365_comb_odd *= b365_odd
                        else:
                            has_b365_all = False
                        sel_name = leg.get("name") or leg.get("selection_name", "")
                        if isinstance(sel_name, str) and ("{'id':" in sel_name or '{"id":' in sel_name):
                            import re
                            sel_name = re.sub(r"\{['\"]id['\"]:\s*['\"][^'\"]*['\"],\s*['\"]name['\"]:\s*['\"]([^'\"]*)['\"]\}", r"\1", sel_name)
                        legs.append({
                            "matchup": leg.get("matchup", ""),
                            "selection_name": sel_name,
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

                    b365_comb_final = round(b365_comb_odd, 2) if has_b365_all else None
                    b365_combo_url = self.bet365.generate_combo_link(legs, "DAILY")
                    b365_slip_text = self.bet365.format_bet365_slip_text(
                        c.get("profile", ""), b365_comb_final, legs, b365_combo_url
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
                        "combined_odd_bet365": b365_comb_final,
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
                        WHERE competition_id = ? AND (status = 'SCHEDULED' OR status = 'FINISHED' OR status = 'LIVE')
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
                        WHERE competition_id = ? AND jornada = ? AND (status = 'SCHEDULED' OR status = 'LIVE')
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

    def _sanitize_bet365_odds(self, match_data: Dict[str, Any]) -> Dict[str, Any]:
        """Inutilitza i neteja quotes sintètiques de Bet365 residuals en objectes de partit."""
        ou = match_data.get("odds_ou_bet365") or match_data.get("odds_ou25_bet365") or {}
        btts = match_data.get("odds_btts_bet365") or {}
        is_synth = (ou == {"over": 1.98, "under": 1.87} or btts == {"yes": 1.93, "no": 1.92})
        has_valid_source = match_data.get("is_manual_override") or match_data.get("odds_bet365", {}).get("provenance") in ("BET365_OFFICIAL_FEED", "BET365_MOVIODDS", "BET365_ODDS_DATA_API")
        
        if is_synth or not has_valid_source:
            match_data["odds_1x2_bet365"] = {}
            match_data["odds_ou_bet365"] = {}
            match_data["odds_ou25_bet365"] = {}
            match_data["odds_btts_bet365"] = {}
            match_data["odds_corners_bet365"] = {}
            match_data["odds_cards_bet365"] = {}
            match_data["odds_dc_bet365"] = {}
            if "odds_bet365" in match_data and isinstance(match_data["odds_bet365"], dict):
                match_data["odds_bet365"]["matched"] = False
                match_data["odds_bet365"]["1X2"] = {}
        return match_data

    def compute_audit_stats(self, predictions: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """
        Calcula l'auditoria de cobertura i frescor de quotes de Bet365/MoviOdds per competició.
        Regles d'auditoria:
        1. Una quota automàtica només es comptabilitza com a vàlida si hi ha una validació explícita i positiva
           del mercat corresponent (market_status[market]['valid'] is True). MAI utilitzar valid=True per defecte.
        2. Conserva la causa de rebuig específica per a cada mercat/partit:
           - absent (discarded_absent / absent)
           - stale / obsolet (discarded_stale / stale)
           - suspès (discarded_suspended / suspended)
           - no verificable / sense confirmació suficient (discarded_unverified / unverified)
           - no associat (discarded_unmatched / unmatched)
        3. Exposa comptadors globals i específics compatibles:
           valid, absent, stale, suspended, unverified, unmatched.
        """
        audit_stats = {}
        for meta in COMPETITIONS_META:
            cid = meta["id"]
            stat = {
                "name": meta["name"],
                "eligible": 0,
                "associated": 0,
                "valid": 0,
                "absent": 0,
                "stale": 0,
                "suspended": 0,
                "unverified": 0,
                "unmatched": 0,
                "valid_1x2": 0,
                "valid_ou25": 0,
                "valid_btts": 0,
                "valid_corners": 0,
                "valid_cards": 0,
                "discarded_stale": 0,
                "discarded_unmatched": 0,
                "discarded_suspended": 0,
                "discarded_unverified": 0,
                "discarded_absent": 0,
            }
            c_matches = predictions.get(cid, {}).get("matches", [])
            for m in c_matches:
                if m.get("status") not in ("FINISHED", "LIVE") and m.get("home_goals") is None:
                    stat["eligible"] += 1
                    b_odds = m.get("odds_bet365") or {}
                    m_stat = b_odds.get("market_status") or {}
                    is_match_stale = bool(b_odds.get("is_stale"))
                    is_matched = bool(b_odds.get("matched"))

                    if is_matched:
                        stat["associated"] += 1
                    else:
                        stat["discarded_unmatched"] += 1
                        stat["unmatched"] += 1

                    # Comprovació de cadascun dels mercats amb validació explícita i positiva (sense True per defecte)
                    for m_key, odds_val, stat_key in [
                        ("1X2", (m.get("odds_1x2_bet365") or {}).get("1"), "valid_1x2"),
                        ("over_under_2_5", (m.get("odds_ou25_bet365") or {}).get("over"), "valid_ou25"),
                        ("btts", (m.get("odds_btts_bet365") or {}).get("yes"), "valid_btts"),
                        ("corners", m.get("odds_corners_bet365"), "valid_corners"),
                        ("cards", m.get("odds_cards_bet365"), "valid_cards"),
                    ]:
                        st_m = m_stat.get(m_key)
                        is_valid = (
                            bool(odds_val)
                            and not is_match_stale
                            and isinstance(st_m, dict)
                            and st_m.get("valid") is True
                            and not st_m.get("stale")
                            and not st_m.get("suspended")
                            and st_m.get("rejection_reason") in (None, "ok")
                        )
                        if is_valid:
                            stat[stat_key] += 1
                            stat["valid"] += 1
                        else:
                            # Rebutjat: registrar causa de rebuig
                            if not is_matched:
                                pass  # Ja comptabilitzat a nivell de partit a unmatched / discarded_unmatched
                            elif not isinstance(st_m, dict) or not st_m:
                                # Manca totalment market_status: no es pot verificar
                                stat["discarded_unverified"] += 1
                                stat["unverified"] += 1
                            else:
                                reason = st_m.get("rejection_reason")
                                if st_m.get("stale") or reason == "stale" or is_match_stale:
                                    stat["discarded_stale"] += 1
                                    stat["stale"] += 1
                                elif st_m.get("suspended") or reason == "suspended":
                                    stat["discarded_suspended"] += 1
                                    stat["suspended"] += 1
                                elif reason == "absent":
                                    stat["discarded_absent"] += 1
                                    stat["absent"] += 1
                                elif reason == "unverified":
                                    stat["discarded_unverified"] += 1
                                    stat["unverified"] += 1
                                else:
                                    if not odds_val:
                                        stat["discarded_absent"] += 1
                                        stat["absent"] += 1
                                    else:
                                        stat["discarded_unverified"] += 1
                                        stat["unverified"] += 1

            audit_stats[cid] = stat
        return audit_stats

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
        existing_match_odds_b365 = {}
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
                            o_1x2 = pm.get("odds_1x2_winamax") or pm.get("odds_1x2")
                            if o_1x2 and o_1x2.get("1") is not None:
                                existing_match_odds[key] = {
                                    "matched": True,
                                    "1X2": o_1x2,
                                    "over_under_2_5": pm.get("odds_ou25_winamax", pm.get("odds_ou25", {})),
                                    "btts": pm.get("odds_btts_winamax", pm.get("odds_btts", {})),
                                    "corners": pm.get("odds_corners_winamax", {}),
                                    "cards": pm.get("odds_cards_winamax", {}),
                                    "double_chance": pm.get("odds_dc_winamax", {}),
                                    "url": pm.get("url_winamax", "https://www.winamax.es"),
                                    "cached_value_bets": pm.get("value_bets", [])
                                }
                            o_b365_1x2 = pm.get("odds_1x2_bet365")
                            ou_b365 = pm.get("odds_ou25_bet365", {})
                            btts_b365 = pm.get("odds_btts_bet365", {})
                            is_synth = (ou_b365 == {"over": 1.98, "under": 1.87} or btts_b365 == {"yes": 1.93, "no": 1.92})
                            is_valid_origin = pm.get("is_manual_override") or pm.get("odds_bet365", {}).get("provenance") in ("BET365_OFFICIAL_FEED", "BET365_MOVIODDS", "BET365_ODDS_DATA_API")
                            if o_b365_1x2 and o_b365_1x2.get("1") is not None and not is_synth and is_valid_origin:
                                existing_match_odds_b365[key] = {
                                    "matched": True,
                                    "is_manual_override": pm.get("is_manual_override", False),
                                    "provenance": pm.get("odds_bet365", {}).get("provenance", "BET365_MOVIODDS"),
                                    "1X2": o_b365_1x2,
                                    "over_under_2_5": ou_b365,
                                    "btts": btts_b365,
                                    "corners": pm.get("odds_corners_bet365", {}),
                                    "cards": pm.get("odds_cards_bet365", {}),
                                    "double_chance": pm.get("odds_dc_bet365", {}),
                                    "url": pm.get("url_bet365", "https://www.bet365.es")
                                }
            except Exception as e:
                print(f"   [!] Error llegint memòria cau prèvia de {WEB_DATA_FILE}: {e}")

        all_referee_updates = []
        seen_referee_match_ids: set = set()  # Per deduplicar dins de la mateixa execució

        # Escalfament de quotes oficials Bet365 via MoviOdds (1 sola crida comprimida per a totes les lligues)
        try:
            from scraper.moviodds_client import MoviOddsClient
            MoviOddsClient.get_instance().fetch_feed()
        except Exception as e:
            print(f"   [!] Error inicialitzant MoviOddsClient: {e}")

        # 1. Processar cada lliga
        for meta in COMPETITIONS_META:
            cid = meta["id"]
            cname = meta["name"]
            print(f"[*] Processant lliga: {cname} ({cid})...")

            # A) Power Rankings
            pr = self.get_power_rankings(cid)
            power_rankings[cid] = pr

            # B) Prediccions i Historial de Jornades
            pred_data = self.get_upcoming_predictions(cid, existing_match_odds=existing_match_odds, existing_match_odds_b365=existing_match_odds_b365)
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
                    if vb.get("is_stale") is True or vb.get("status") == "OBSOLETE" or vb.get("freshness") in ("STALE", "UNKNOWN"):
                        continue
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

                    bookie_name = vb.get("bookie") or ("Bet365" if vb.get("provenance") == "BET365" else "Winamax")
                    w_odd_val = vb.get("bookie_odd_winamax")
                    b365_odd_val = vb.get("bookie_odd_bet365")

                    active_odd = float(vb.get("bookie_odd") or (b365_odd_val if bookie_name == "Bet365" else w_odd_val) or 1.0)
                    w_odd = float(w_odd_val) if w_odd_val is not None else (active_odd if bookie_name == "Winamax" else None)
                    b365_odd = float(b365_odd_val) if b365_odd_val is not None else (active_odd if bookie_name == "Bet365" else None)

                    p_push = float(vb.get("push_prob", 0.0)) / 100.0
                    edge_w = round((p_calc * w_odd + p_push - 1.0) * 100.0, 1) if w_odd is not None else None
                    edge_b365 = round((p_calc * b365_odd + p_push - 1.0) * 100.0, 1) if b365_odd is not None else None
                    edge_active = round(float(edge_val), 1)

                    from scraper.winamax_scraper import WINAMAX_TOURNAMENTS
                    w_base = f"https://www.winamax.es/apuestas-deportivas/sports/1/{WINAMAX_TOURNAMENTS.get(cid, '32')}"
                    w_match_url = vb.get("url") or m.get("odds", {}).get("url") or w_base
                    b365_match_url = m.get("odds_bet365", {}).get("url") or "https://www.bet365.es/#/AS/B1/"

                    rejection_reason_b365 = None
                    if b365_odd is None:
                        b365_m = m.get("odds_bet365", {})
                        if not b365_m or not b365_m.get("matched"):
                            rejection_reason_b365 = "Partit no associat a Bet365"
                        elif b365_m.get("is_stale"):
                            rejection_reason_b365 = "Quotes Bet365 obsoletes"
                        else:
                            rejection_reason_b365 = "Línia de mercat no oferta per Bet365"

                    all_value_bets.append({
                        "competition_id": cid,
                        "competition_name": cname,
                        "matchup": f"{m['home_team']['name']} vs {m['away_team']['name']}",
                        "date": m["date"],
                        "selection": vb.get("name"),
                        "category": vb.get("category"),
                        "market_type": vb.get("market_type"),
                        "market_format": vb.get("market_format"),
                        "bookie": bookie_name,
                        "provenance": vb.get("provenance") or ("BET365" if bookie_name == "Bet365" else "WINAMAX"),
                        "bookie_odd": active_odd,
                        "bookie_odd_winamax": w_odd,
                        "bookie_odd_bet365": b365_odd,
                        "rejection_reason_bet365": rejection_reason_b365,
                        "model_prob": float(vb.get("model_prob", 0.0)),
                        "push_prob": float(vb.get("push_prob", 0.0)),
                        "loss_prob": float(vb.get("loss_prob", 0.0)),
                        "fair_odd": float(vb.get("fair_odd", 1.0)),
                        "edge_pct": edge_active,
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


        # Auditoria de cobertura i frescor de quotes per competició
        audit_stats = self.compute_audit_stats(predictions)

        print("\n" + "=" * 116)
        print("📊 AUDITORIA DE COBERTURA I FRESCOR DE QUOTES (BET365 / MOVIODDS)")
        print("=" * 116)
        print(f"{'COMPETICIÓ':<14} | {'ELEG.':<5} | {'ASSOC.':<6} | {'1X2':<5} | {'O/U2.5':<6} | {'BTTS':<5} | {'CÓRN':<5} | {'TARG':<5} | {'ABSENT':<7} | {'OBSOLET':<7} | {'SUSPÈS':<6} | {'NO VERIF':<8} | {'NO ASSOC':<8}")
        print("-" * 116)
        tot_e = tot_a = tot_1 = tot_ou = tot_bt = tot_co = tot_ca = tot_ab = tot_st = tot_su = tot_uv = tot_un = 0
        for cid, st in audit_stats.items():
            tot_e += st["eligible"]
            tot_a += st["associated"]
            tot_1 += st["valid_1x2"]
            tot_ou += st["valid_ou25"]
            tot_bt += st["valid_btts"]
            tot_co += st["valid_corners"]
            tot_ca += st["valid_cards"]
            tot_ab += st.get("discarded_absent", 0)
            tot_st += st["discarded_stale"]
            tot_su += st["discarded_suspended"]
            tot_uv += st["discarded_unverified"]
            tot_un += st["discarded_unmatched"]
            print(f"{cid:<14} | {st['eligible']:<5} | {st['associated']:<6} | {st['valid_1x2']:<5} | {st['valid_ou25']:<6} | {st['valid_btts']:<5} | {st['valid_corners']:<5} | {st['valid_cards']:<5} | {st.get('discarded_absent', 0):<7} | {st['discarded_stale']:<7} | {st['discarded_suspended']:<6} | {st['discarded_unverified']:<8} | {st['discarded_unmatched']:<8}")
        print("-" * 116)
        print(f"{'TOTAL':<14} | {tot_e:<5} | {tot_a:<6} | {tot_1:<5} | {tot_ou:<6} | {tot_bt:<5} | {tot_co:<5} | {tot_ca:<5} | {tot_ab:<7} | {tot_st:<7} | {tot_su:<6} | {tot_uv:<8} | {tot_un:<8}")
        print("=" * 116 + "\n")

        # Eliminat qualsevol fallback a previous_data["value_bets"]:
        # Si no hi ha oportunitats noves i verificades, all_value_bets ha de quedar buit [].
        # Les recomanacions actives mai no poden mostrar quotes obsoletes o caducades com a +EV vigent.

        # Ordenar Value bets pel millor edge %
        all_value_bets.sort(key=lambda x: (float(x.get("edge_pct")) if x.get("edge_pct") is not None else -999.0), reverse=True)

        # 3. Arbitratge Esportiu (Surebets / "Diners Segurs")
        print("[*] Escanejant oportunitats d'Arbitratge Esportiu (Surebets)...")
        arbitrage_report = self.arbitrage_engine.scan_all_matches(all_predicted_matches_flat)
        # Preservar bookmakers_status i timestamps del daemon si ja existien a web/data/data.json (FASE 4)
        if previous_data.get("arbitrage", {}).get("bookmakers_status"):
            arbitrage_report["bookmakers_status"] = previous_data["arbitrage"]["bookmakers_status"]
        if previous_data.get("arbitrage", {}).get("last_generation"):
            arbitrage_report["last_generation"] = previous_data["arbitrage"]["last_generation"]
        if previous_data.get("arbitrage", {}).get("cycle_duration_seconds") is not None:
            arbitrage_report["cycle_duration_seconds"] = previous_data["arbitrage"]["cycle_duration_seconds"]

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

        # Registrar exclusivament els partits que han finalitzat en aquesta execució del pipeline
        effective_matches = ingested_matches or []

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
                missing_str = ', '.join(a.get('missing_bookmakers') or a.get('missing_markets') or [])
                print(f"   • [{a['competition_name']}] {a['matchup']} ({a['date']}) -> Falten: {missing_str}")
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

        # 4.5. Estat del Pipeline General de GitHub Actions (FASE 2)
        pipeline_status = None
        pipeline_state_file = ROOT / "data" / "pipeline_state.json"
        if pipeline_state_file.exists():
            try:
                with open(pipeline_state_file, "r", encoding="utf-8") as ps_f:
                    ps_data = json.load(ps_f)
                    pipeline_status = ps_data.get("pipeline_status")
            except Exception:
                pass
        if not pipeline_status:
            try:
                from daily_runner import get_canonical_pipeline_status
                pipeline_status = get_canonical_pipeline_status()
            except Exception:
                pipeline_status = {
                    "workflow_name": "Pipeline Diari Autònom de Prediccions",
                    "workflow_file": "daily_pipeline.yml",
                    "schedule": "08:23, 15:23 i 00:23 BCN (06:23, 13:23 i 22:23 UTC)",
                    "status": "COMPLETED",
                    "last_run_timestamp": now_iso,
                    "last_success_timestamp": now_iso,
                    "duration_seconds": 120.0,
                    "last_attempt_status": "COMPLETED",
                    "tasks": {}
                }

        # 5. Assembling JSON Payload
        payload = {
            "metadata": {
                "generated_at": now_iso,
                "formatted_date": now_fmt,
                "season": "2026-2027",
                "version": "2.0.0",
                "app_title": "Prediccions de Futbol · Poisson GLM & Winamax",
                "competitions": active_comps_meta,
                "pipeline_status": pipeline_status
            },
            "pipeline_status": pipeline_status,
            "changelog": self.changelog_mgr.get_feed(),
            "power_rankings": power_rankings,
            "predictions": predictions,
            "value_bets": all_value_bets,
            "archived_unverified_bets": previous_data.get("value_bets", []) if len(all_value_bets) == 0 and previous_data.get("value_bets") else [],
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
