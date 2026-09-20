"""
model/card_model.py
===================
Model d'Esdeveniments per a Targetes Grogues i Vermelles a La Lliga.

Integra de forma central:
1. Mètriques històriques de l'àrbitre assignat (mitjana de targetes i índex de severitat).
2. Tendència disciplinària recent dels dos equips (mitjana d'amonestacions dels últims partits).
3. Factor Derbi / Rivalitat Històrica (El Clàssic, Derbi de Sevilla, Derbi Basc, Derbi Madrileny).
4. Tensió de la classificació (diferencial de punts i proximitat a la zona de descens).
"""

from typing import Dict, Any, Tuple
import numpy as np
from scipy.stats import poisson

class CardModel:
    def __init__(self):
        # Llista de derbis d'alta tensió a La Lliga
        self.known_derbies = {
            ("RMA", "FCB"), ("FCB", "RMA"), # El Clásico
            ("RMA", "ATM"), ("ATM", "RMA"), # Derbi Madrileño
            ("BET", "SEV"), ("SEV", "BET"), # Gran Derbi Sevillano
            ("ATH", "RSO"), ("RSO", "ATH"), # Derbi Vasco
            ("FCB", "ESP"), ("ESP", "FCB"), # Derbi Barcelonés
            ("VAL", "VIL"), ("VIL", "VAL"), # Derbi de la Comunitat
        }

    def is_derby(self, home_team_id: str, away_team_id: str) -> bool:
        return (home_team_id, away_team_id) in self.known_derbies

    def estimate_card_expectations(
        self,
        referee: Dict[str, Any],
        home_team: Dict[str, Any],
        away_team: Dict[str, Any],
        is_knockout: bool = False
    ) -> Dict[str, Any]:
        """
        Calcula la distribució probabilística de targetes per al partit.
        """
        # 1. Base arbitral
        ref_yellow_avg = float(referee.get("yellow_cards_avg", 4.5))
        ref_red_avg = float(referee.get("red_cards_avg", 0.25))
        ref_strictness = float(referee.get("strictness_index", 1.0))
        ref_name = referee.get("name", "Àrbitre Assignat")

        # 2. Base d'equips (mitjana recent d'amonestacions)
        home_cards_avg = float(home_team.get("avg_cards_for_5", 2.2))
        away_cards_avg = float(away_team.get("avg_cards_for_5", 2.3))
        team_combined_avg = home_cards_avg + away_cards_avg

        # 3. Factor Rivalitat i Tensió
        derby = self.is_derby(home_team.get("id", ""), away_team.get("id", ""))
        derby_boost = 0.90 if derby else 0.0
        knockout_boost = 0.50 if is_knockout else 0.0

        # Tensió per proximitat de rànquings
        rank_home = float(home_team.get("general_rank", 10.0))
        rank_away = float(away_team.get("general_rank", 10.0))
        rank_diff = abs(rank_home - rank_away)
        tension_boost = max(0.0, (10.0 - rank_diff) * 0.04) # Si estan molt empatats, més tensió

        # 4. Càlcul combinat de la lambda total de targetes (ponderant 55% àrbitre + 45% equips)
        lambda_cards = (
            (0.55 * (ref_yellow_avg * ref_strictness) + 0.45 * team_combined_avg)
            + derby_boost
            + knockout_boost
            + tension_boost
        )

        # Distribució entre local i visitant
        prop_home = home_cards_avg / max(0.1, team_combined_avg)
        lambda_home_cards = lambda_cards * prop_home
        lambda_away_cards = lambda_cards * (1.0 - prop_home)

        # 5. Probabilitats Over/Under
        k_values = np.arange(15)
        pmf = poisson.pmf(k_values, lambda_cards)

        over_3_5 = float(pmf[4:].sum()) * 100
        over_4_5 = float(pmf[5:].sum()) * 100
        over_5_5 = float(pmf[6:].sum()) * 100
        over_6_5 = float(pmf[7:].sum()) * 100

        # Probabilitat de targeta vermella al partit
        prob_red_card = (1.0 - np.exp(-ref_red_avg * (1.0 + (0.3 if derby else 0.0)))) * 100

        # Diagnòstic clar
        if ref_strictness >= 1.15:
            ref_profile = "Àrbitre MOLT SEVER (Línia alta de targetes)"
        elif ref_strictness <= 0.88:
            ref_profile = "Àrbitre PERMISSIU (Deixa jugar / Pocs avisos)"
        else:
            ref_profile = "Àrbitre ESTÀNDARD / EQUILIBRAT"

        return {
            "referee_name": ref_name,
            "referee_profile": ref_profile,
            "referee_yellow_avg": round(ref_yellow_avg, 2),
            "is_derby": derby,
            "expected_total_cards": round(lambda_cards, 2),
            "expected_home_cards": round(lambda_home_cards, 2),
            "expected_away_cards": round(lambda_away_cards, 2),
            "prob_over_cards": {
                "over_3_5": round(over_3_5, 1),
                "over_4_5": round(over_4_5, 1),
                "over_5_5": round(over_5_5, 1),
                "over_6_5": round(over_6_5, 1),
            },
            "prob_red_card": round(prob_red_card, 1)
        }
