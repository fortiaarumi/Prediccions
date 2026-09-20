"""
engine/match_predictor.py
=========================
Coordinador unificat de predicció per a un partit de La Lliga.

Integra:
- Model de Resultat i Gols (Poisson + Dixon-Coles)
- Model de Targetes i Àrbitre (CardModel)
- Model de Córners (CornerModel)
- Factors de descans i fatiga multicompetició (MulticompTracker)
"""

from typing import Dict, Any
from model.goal_model import GoalModel
from model.card_model import CardModel
from model.corner_model import CornerModel
from scraper.multicomp_tracker import MulticompTracker

class MatchPredictor:
    def __init__(self):
        self.goal_model = GoalModel()
        self.card_model = CardModel()
        self.corner_model = CornerModel()
        self.multicomp_tracker = MulticompTracker()

    def predict_single_match(
        self,
        home_team: Dict[str, Any],
        away_team: Dict[str, Any],
        referee: Dict[str, Any],
        match_context: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Executa l'anàlisi predictiu complet per a un partit.
        """
        if match_context is None:
            match_context = {}

        # 1. Factors de descans
        home_rest = float(home_team.get("rest_days", 7.0))
        away_rest = float(away_team.get("rest_days", 7.0))
        
        home_rest_factor = self.multicomp_tracker.get_fatigue_multiplier(
            home_rest, match_context.get("home_played_europe", False)
        )
        away_rest_factor = self.multicomp_tracker.get_fatigue_multiplier(
            away_rest, match_context.get("away_played_europe", False)
        )

        # 2. Predicció de Gols i 1X2
        goal_preds = self.goal_model.predict_match(
            home_team=home_team,
            away_team=away_team,
            home_rest_factor=home_rest_factor,
            away_rest_factor=away_rest_factor,
            top_n=5
        )

        # 3. Predicció de Targetes i Àrbitre
        card_preds = self.card_model.estimate_card_expectations(
            referee=referee,
            home_team=home_team,
            away_team=away_team,
            is_knockout=match_context.get("is_knockout", False)
        )

        # 4. Predicció de Córners
        corner_preds = self.corner_model.estimate_corners(
            home_team=home_team,
            away_team=away_team
        )

        # 5. Indicadors Clars i Sense Ambigüitat
        # Pronòstic 1X2 principal
        p_1 = goal_preds["prob_1X2"]["1"]
        p_x = goal_preds["prob_1X2"]["X"]
        p_2 = goal_preds["prob_1X2"]["2"]
        
        if p_1 >= 50.0:
            main_1x2_verdict = f"Victòria Local ({home_team.get('name', 'Local')})"
        elif p_2 >= 45.0:
            main_1x2_verdict = f"Victòria Visitant ({away_team.get('name', 'Visitant')})"
        elif p_1 >= p_2 and p_1 >= p_x:
            main_1x2_verdict = "1X (Favorit Local / Risc d'Empat)"
        elif p_2 >= p_1 and p_2 >= p_x:
            main_1x2_verdict = "X2 (Favorit Visitant / Risc d'Empat)"
        else:
            main_1x2_verdict = "Partit molt obert / Empat probable"

        # Pronòstic de targetes principal
        exp_cards = card_preds["expected_total_cards"]
        if exp_cards >= 5.5:
            card_verdict = f"Línia ALTA de targetes (+5.5 esperades: {exp_cards})"
        elif exp_cards <= 4.0:
            card_verdict = f"Línia BAIXA de targetes (-4.5 esperades: {exp_cards})"
        else:
            card_verdict = f"Línia MODERADA (~4.5 - 5.0 targetes esperades: {exp_cards})"

        return {
            "matchup": f"{home_team.get('name', 'Local')} vs {away_team.get('name', 'Visitant')}",
            "home_team": home_team.get("name", "Local"),
            "away_team": away_team.get("name", "Visitant"),
            "home_team_id": home_team.get("id"),
            "away_team_id": away_team.get("id"),
            "verdict_1x2": main_1x2_verdict,
            "verdict_cards": card_verdict,
            "goals": goal_preds,
            "cards": card_preds,
            "corners": corner_preds,
            "rest_info": {
                "home_rest_days": home_rest,
                "away_rest_days": away_rest,
                "home_fatigue_penalty": round((1.0 - home_rest_factor) * 100, 1),
                "away_fatigue_penalty": round((1.0 - away_rest_factor) * 100, 1),
            }
        }
