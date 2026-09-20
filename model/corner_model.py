"""
model/corner_model.py
=====================
Model de Predicció de Córners per a La Lliga.

Calcula la distribució esperada de córners locals, visitants i totals a partir de:
- Pressió d'atac per bandes de l'equip local i visitant.
- Córners concedits per partit.
- Biaix de favor de camp.
"""

from typing import Dict, Any
import numpy as np
from scipy.stats import poisson

class CornerModel:
    def __init__(self, league_avg_corners: float = 9.40):
        self.league_avg_corners = league_avg_corners

    def estimate_corners(
        self,
        home_team: Dict[str, Any],
        away_team: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Calcula la previsió de córners del partit."""
        home_for = float(home_team.get("avg_corners_for_5", 5.2))
        home_against = float(home_team.get("avg_corners_against_5", 4.3))
        away_for = float(away_team.get("avg_corners_for_5", 4.2))
        away_against = float(away_team.get("avg_corners_against_5", 5.1))

        # Lambda local i visitant
        lambda_home_corners = (home_for + away_against) / 2.0 * 1.05  # Lleuger avantatge local
        lambda_away_corners = (away_for + home_against) / 2.0 * 0.95

        lambda_total = lambda_home_corners + lambda_away_corners

        # Probabilitats Over/Under
        k_values = np.arange(25)
        pmf = poisson.pmf(k_values, lambda_total)

        over_8_5 = float(pmf[9:].sum()) * 100
        over_9_5 = float(pmf[10:].sum()) * 100
        over_10_5 = float(pmf[11:].sum()) * 100

        return {
            "expected_total_corners": round(lambda_total, 2),
            "expected_home_corners": round(lambda_home_corners, 2),
            "expected_away_corners": round(lambda_away_corners, 2),
            "prob_over_corners": {
                "over_8_5": round(over_8_5, 1),
                "over_9_5": round(over_9_5, 1),
                "over_10_5": round(over_10_5, 1),
            }
        }
