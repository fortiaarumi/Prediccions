"""
engine/jornada_predictor.py
===========================
Execució de les prediccions completes per a qualsevol lliga (LaLiga EA Sports, Premier League, Hypermotion).
- Separa clarament partits jugats i pendents de disputar.
- Assigna àrbitres oficials o estàndard.
- Extreu cuotes en directe de Winamax Espanya per a la competició corresponent.
- Desa automàticament les combinades al sistema de seguiment ('Què hagués passat si...').
- Genera automàticament l'informe en PDF oficial ('reports/informe_jornada_{N}_{comp}.pdf').
"""

import sys
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional
from database.db_manager import DatabaseManager
from scraper.jornada_resolver import JornadaResolver
from scraper.referee_resolver import RefereeResolver
from scraper.winamax_scraper import WinamaxScraper
from scraper.match_scraper import MatchScraper
from engine.match_predictor import MatchPredictor
from engine.value_bet_engine import ValueBetEngine
from engine.combo_bet_engine import ComboBetEngine
from engine.pdf_report_generator import PDFReportGenerator

COMPETITION_NAMES = {
    "LALIGA": "LaLiga EA Sports",
    "PREMIER": "Premier League",
    "HYPERMOTION": "LaLiga Hypermotion",
}

class JornadaPredictor:
    def __init__(self, db: DatabaseManager = None):
        self.db = db if db else DatabaseManager()
        self.referee_resolver = RefereeResolver(db=self.db)
        self.match_scraper = MatchScraper(headless=True)
        self.predictor = MatchPredictor()
        self.winamax = WinamaxScraper(headless=True)
        self.value_engine = ValueBetEngine(kelly_fraction=0.25)
        self.combo_engine = ComboBetEngine()
        self.pdf_generator = PDFReportGenerator()

    def predict_jornada(
        self,
        jornada: int,
        season: str = "2026-2027",
        competition_id: str = "LALIGA",
        include_odds: bool = True,
        generate_pdf: bool = True
    ) -> Dict[str, Any]:
        """
        Descobreix els partits de la jornada, separa jugats de pendents,
        calcula les prediccions, guarda les recomanacions de combinades
        i genera l'informe PDF complet.
        """
        comp_id = competition_id.upper()
        comp_name = COMPETITION_NAMES.get(comp_id, comp_id)

        print(f"\n" + "=" * 75)
        print(f"   ANÀLISI I PREDICCIÓ OFICIAL: {comp_name.upper()} - JORNADA {jornada}")
        print("=" * 75)

        resolver = JornadaResolver(season=season, competition_id=comp_id)
        fixtures = resolver.get_jornada_fixtures(jornada)

        # 1. Recuperar partits ja jugats des de la base de dades
        played_matches, upcoming_fixtures = self._classify_fixtures(jornada, fixtures, competition_id=comp_id)

        print(f"[*] Total partits Jornada {jornada} ({comp_id}): {len(fixtures)}")
        print(f"    • Partits ja jugats: {len(played_matches)}")
        print(f"    • Partits pendents de disputar: {len(upcoming_fixtures)}")

        # 2. Predir cadascun dels partits pendents
        predicted_matches = []
        for idx, fix in enumerate(upcoming_fixtures, start=1):
            home_name = fix["home_team"]
            away_name = fix["away_team"]
            match_date = fix.get("date", "Pendent")

            home_id = self.db.find_team_id(home_name) or self.db.get_or_create_team(home_name)
            away_id = self.db.find_team_id(away_name) or self.db.get_or_create_team(away_name)

            home_data = self.db.get_team_rating(home_id)
            away_data = self.db.get_team_rating(away_id)

            # Resolució d'àrbitre
            if comp_id == "LALIGA":
                ref_resolution = self.referee_resolver.resolve_referee(
                    home_name=home_name,
                    away_name=away_name,
                    match_code=fix.get("match_code"),
                    match_date=match_date
                )
                ref_data = ref_resolution["ref_data"]
            else:
                ref_data = self.db.get_referee("REF_DEFAULT")
                ref_resolution = {
                    "id": "REF_DEFAULT",
                    "name": "Àrbitre Oficial",
                    "is_generic": True,
                    "ref_data": ref_data
                }

            ref_status_str = f"{ref_resolution['name']}" if not ref_resolution["is_generic"] else "Pendent Oficial (Àrbitre Mitjà Standard)"
            print(f"\n[{idx}/{len(upcoming_fixtures)}] Analitzant: {home_name} vs {away_name} ({match_date})")
            print(f"    • Àrbitre: {ref_status_str}")

            # Predicció estadística
            match_pred = self.predictor.predict_single_match(
                home_team=home_data,
                away_team=away_data,
                referee=ref_data,
                match_context={"date": match_date}
            )
            match_pred["competition_id"] = comp_id
            match_pred["date"] = match_date
            match_pred["referee_resolution"] = ref_resolution

            # Extracció de cuotes Winamax i valor esperat
            bet_analysis = {"value_bets": [], "single_markets": []}
            odds = {}
            if include_odds:
                odds = self.winamax.get_match_odds(home_data["name"], away_data["name"], competition_id=comp_id)
                bet_analysis = self.value_engine.analyze_match_betting(match_pred, odds)

            match_pred["odds"] = odds
            match_pred["bet_analysis"] = bet_analysis
            predicted_matches.append(match_pred)

            n_vbs = len(bet_analysis.get("value_bets", []))
            o_1x2 = odds.get('1X2') or odds.get('odds_1x2') or {}
            print(f"    • Pronòstic: {match_pred['verdict_1x2']} | Cuotes Winamax: 1({o_1x2.get('1', '-')}) X({o_1x2.get('X', '-')}) 2({o_1x2.get('2', '-')}) | Oportunitats +EV%: {n_vbs}")

        # 3. Anàlisi i generació d'apostes combinades matemàtiques
        combo_bets = self.combo_engine.analyze_jornada_combos(predicted_matches)
        safe_c = combo_bets.get("safe_combo", {})
        risky_c = combo_bets.get("risky_combo", {})

        # Registrar automàticament les combinades al sistema de seguiment ('Què hagués passat si...')
        for idx, sc in enumerate(combo_bets.get("safe", []), 1):
            if sc and sc.get("legs"):
                self.db.save_combo_recommendation(
                    competition_id=comp_id,
                    season=season,
                    jornada=jornada,
                    profile=f"SAFE_{idx}",
                    stake=25.0,
                    combo_summary=sc
                )
        for idx, sm in enumerate(combo_bets.get("semi", []), 1):
            if sm and sm.get("legs"):
                self.db.save_combo_recommendation(
                    competition_id=comp_id,
                    season=season,
                    jornada=jornada,
                    profile=f"SEMI_{idx}",
                    stake=10.0,
                    combo_summary=sm
                )
        for idx, rc in enumerate(combo_bets.get("risky", []), 1):
            if rc and rc.get("legs"):
                self.db.save_combo_recommendation(
                    competition_id=comp_id,
                    season=season,
                    jornada=jornada,
                    profile=f"RISKY_{idx}",
                    stake=5.0,
                    combo_summary=rc
                )

        print("\n" + "-" * 75)
        print(f"   🎯 APOSTES COMBINADES RECOMANADES ({comp_name.upper()} - WINAMAX ESPANYA)")
        print("-" * 75)
        if safe_c and safe_c.get("legs"):
            print(f"   🛡️ COMBINADA SEGURA:   Cuota: {safe_c['combined_odd']:.2f} (Target 2-3) | Prob. Model: {safe_c['combined_prob_pct']:.1f}% | EV: +{safe_c['ev_pct']:.1f}%")
            for leg in safe_c["legs"]:
                print(f"      • {leg['matchup']}: {leg['name']} @ {leg['bookie_odd']:.2f} ({leg['model_prob']:.1f}%)")
        if risky_c and risky_c.get("legs"):
            print(f"   🚀 COMBINADA ARRISCADA: Cuota: {risky_c['combined_odd']:.2f} (Target >= 30) | Prob. Model: {risky_c['combined_prob_pct']:.1f}% | EV: +{risky_c['ev_pct']:.1f}%")
            for leg in risky_c["legs"]:
                print(f"      • {leg['matchup']}: {leg['name']} @ {leg['bookie_odd']:.2f} ({leg['model_prob']:.1f}%)")
        print("-" * 75)

        # 4. Generació de l'informe PDF oficial
        pdf_path = None
        if generate_pdf:
            print(f"\n[*] Maquetant i generant l'informe en PDF per a la Jornada {jornada} de {comp_name}...")
            pdf_path = self.pdf_generator.generate_jornada_report(
                jornada=jornada,
                played_matches=played_matches,
                predicted_matches=predicted_matches,
                combo_bets=combo_bets,
                competition_id=comp_id
            )

            print("\n" + "=" * 75)
            print(f"   [ÈXIT] INFORME DE LA JORNADA {jornada} ({comp_name}) GENERAT CORRECTAMENT!")
            print(f"   📄 Fitxer PDF desat a:")
            print(f"      {pdf_path.resolve()}")
            print("=" * 75 + "\n")

        return {
            "competition_id": comp_id,
            "competition_name": comp_name,
            "jornada": jornada,
            "played_matches": played_matches,
            "predicted_matches": predicted_matches,
            "combo_bets": combo_bets,
            "pdf_path": str(pdf_path.resolve()) if pdf_path else None
        }

    def _classify_fixtures(
        self,
        jornada: int,
        fixtures: List[Dict[str, Any]],
        competition_id: str = "LALIGA"
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Classifica els partits entre ja jugats i pendents de disputar per a la competició."""
        played = []
        upcoming = []

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT m.id, m.home_team_id, m.away_team_id, m.home_goals, m.away_goals,
                       m.home_xg, m.away_xg, m.home_corners, m.away_corners,
                       m.home_yellow_cards, m.away_yellow_cards, m.referee_id, m.date_time,
                       ht.name as home_name, at.name as away_name, r.name as ref_name
                FROM matches m
                LEFT JOIN teams ht ON m.home_team_id = ht.id
                LEFT JOIN teams at ON m.away_team_id = at.id
                LEFT JOIN referees r ON m.referee_id = r.id
                WHERE m.jornada = ? AND m.competition_id = ? AND m.status = 'FINISHED' AND m.home_goals IS NOT NULL
            """, (jornada, competition_id))
            finished_db = {f"{row['home_team_id']}_{row['away_team_id']}": dict(row) for row in cursor.fetchall()}

        for fix in fixtures:
            home = fix["home"]
            away = fix["away"]
            h_id = self.db.find_team_id(home)
            a_id = self.db.find_team_id(away)
            match_key = f"{h_id}_{a_id}"

            # Comprovar si el partit ja s'ha disputat i té dades completes a la BD
            is_in_db = match_key in finished_db
            db_data = finished_db.get(match_key, {})
            has_full_db_data = is_in_db and db_data.get("home_xg") is not None

            has_flashscore_score = fix.get("score_h") is not None and str(fix.get("score_h")).isdigit()

            if has_full_db_data:
                played.append({
                    "home": home,
                    "away": away,
                    "date": db_data.get("date_time") or fix.get("date"),
                    "home_goals": db_data.get("home_goals"),
                    "away_goals": db_data.get("away_goals"),
                    "home_xg": db_data.get("home_xg"),
                    "away_xg": db_data.get("away_xg"),
                    "home_corners": db_data.get("home_corners"),
                    "away_corners": db_data.get("away_corners"),
                    "home_yellow_cards": db_data.get("home_yellow_cards"),
                    "away_yellow_cards": db_data.get("away_yellow_cards"),
                    "referee_name": db_data.get("ref_name") or "Oficial"
                })
            elif has_flashscore_score or is_in_db:
                # El partit ja s'ha jugat: Scrapejar autònomament en directe les estadístiques
                print(f"[*] Recuperant autònomament en directe estadístiques: {home} vs {away}...")
                match_code = fix.get("match_code")
                url = fix.get("url")
                scraped = self.match_scraper.scrape_match_feed(match_code, url) or {}

                ref_res = self.referee_resolver.resolve_referee(home, away, match_code=match_code, match_date=fix.get("date"))
                ref_id = ref_res.get("id") or "REF_DEFAULT"
                ref_name = ref_res.get("name") or "Oficial"

                home_goals = int(fix.get("score_h")) if has_flashscore_score else db_data.get("home_goals", 0)
                away_goals = int(fix.get("score_a")) if has_flashscore_score else db_data.get("away_goals", 0)
                if scraped.get("home_goals") is not None:
                    home_goals = scraped.get("home_goals")
                    away_goals = scraped.get("away_goals")

                home_xg = scraped.get("home_xg") if scraped.get("home_xg") is not None else db_data.get("home_xg")
                away_xg = scraped.get("away_xg") if scraped.get("away_xg") is not None else db_data.get("away_xg")
                home_corners = scraped.get("home_corners") if scraped.get("home_corners") is not None else db_data.get("home_corners")
                away_corners = scraped.get("away_corners") if scraped.get("away_corners") is not None else db_data.get("away_corners")
                home_yellow_cards = scraped.get("home_yellow_cards") if scraped.get("home_yellow_cards") is not None else db_data.get("home_yellow_cards")
                away_yellow_cards = scraped.get("away_yellow_cards") if scraped.get("away_yellow_cards") is not None else db_data.get("away_yellow_cards")

                # Guardar o actualitzar a la base de dades SQLite
                if h_id and a_id:
                    match_id = f"{competition_id}_J{jornada}_{h_id}_{a_id}"
                    with self.db.get_connection() as conn:
                        c = conn.cursor()
                        c.execute("""
                            INSERT OR REPLACE INTO matches 
                            (id, competition_id, season, jornada, date_time, home_team_id, away_team_id, referee_id,
                             home_goals, away_goals, home_xg, away_xg, home_corners, away_corners,
                             home_yellow_cards, away_yellow_cards, home_red_cards, away_red_cards, status, url)
                            VALUES (?, ?, '2026-2027', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 'FINISHED', ?)
                        """, (
                            match_id, competition_id, jornada, fix.get("date") or "2026-09-15", h_id, a_id, ref_id,
                            home_goals, away_goals, home_xg, away_xg, home_corners, away_corners,
                            home_yellow_cards, away_yellow_cards, url
                        ))
                        conn.commit()

                played.append({
                    "home": home,
                    "away": away,
                    "date": fix.get("date"),
                    "home_goals": home_goals,
                    "away_goals": away_goals,
                    "home_xg": home_xg,
                    "away_xg": away_xg,
                    "home_corners": home_corners,
                    "away_corners": away_corners,
                    "home_yellow_cards": home_yellow_cards,
                    "away_yellow_cards": away_yellow_cards,
                    "referee_name": ref_name
                })
            else:
                upcoming.append(fix)

        return played, upcoming
