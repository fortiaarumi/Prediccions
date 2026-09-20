"""
model/goal_model.py
===================
Model Probabilístic de Gols i Resultats 1X2 per a La Lliga.

Integra:
- Rànquings ofensius i defensius dinàmics.
- Mitjanes mòbils d'xG generat i concedit per cada equip.
- Factor de descans suau (impacte màxim 2.5%).
- Correcció bivariant de Dixon-Coles (rho = -0.10) per a marcadors baixos.
"""

import numpy as np
from scipy.stats import poisson
from typing import Dict, Any, List, Tuple

class GoalModel:
    def __init__(self, rho: float = -0.10, home_advantage: float = 0.25, max_goals: int = 8):
        self.rho = rho
        self.home_advantage = home_advantage
        self.max_goals = max_goals
        self.league_avg_home_goals = 1.42
        self.league_avg_away_goals = 1.12

    def estimate_lambdas(
        self,
        home_team: Dict[str, Any],
        away_team: Dict[str, Any],
        home_rest_factor: float = 1.0,
        away_rest_factor: float = 1.0
    ) -> Tuple[float, float]:
        """
        Calcula lambda_home i lambda_away combinant el rànquing d'equips i l'xG mòbil.
        """
        home_off = float(home_team.get("off_rank", 10.0))
        home_def = float(home_team.get("def_rank", 10.0))
        away_off = float(away_team.get("off_rank", 10.0))
        away_def = float(away_team.get("def_rank", 10.0))

        # 1. Força d'atac i feblesa defensiva teòrica
        off_strength_home = max(0.4, (21.0 - home_off) / 10.5)
        def_weakness_away = max(0.4, away_def / 10.5)

        off_strength_away = max(0.4, (21.0 - away_off) / 10.5)
        def_weakness_home = max(0.4, home_def / 10.5)

        lambda_rank_home = self.league_avg_home_goals * off_strength_home * def_weakness_away
        lambda_rank_away = self.league_avg_away_goals * off_strength_away * def_weakness_home

        # 2. xG mòbil si està disponible (rolling xG)
        home_xg_for = float(home_team.get("rolling_xg_for", lambda_rank_home))
        away_xg_against = float(away_team.get("rolling_xg_against", lambda_rank_home))
        empirical_lambda_home = (home_xg_for + away_xg_against) / 2.0

        away_xg_for = float(away_team.get("rolling_xg_for", lambda_rank_away))
        home_xg_against = float(home_team.get("rolling_xg_against", lambda_rank_away))
        empirical_lambda_away = (away_xg_for + home_xg_against) / 2.0

        # 3. Combinació ponderada (60% Model de Ranks + 40% xG mòbil directe)
        lambda_home = (0.60 * lambda_rank_home + 0.40 * empirical_lambda_home) * home_rest_factor + (self.home_advantage * 0.5)
        lambda_away = (0.60 * lambda_rank_away + 0.40 * empirical_lambda_away) * away_rest_factor - (self.home_advantage * 0.2)

        return float(max(0.20, lambda_home)), float(max(0.15, lambda_away))

    def build_bivariate_dixon_coles_matrix(self, lambda_home: float, lambda_away: float) -> np.ndarray:
        """Genera la matriu bivariant de probabilitats corregida amb Dixon-Coles."""
        goals = np.arange(self.max_goals)
        home_pmf = poisson.pmf(goals, lambda_home)
        away_pmf = poisson.pmf(goals, lambda_away)

        matrix = np.outer(home_pmf, away_pmf)

        # Ajust de Dixon-Coles per a marcadors 0-0, 1-0, 0-1 i 1-1
        matrix[0, 0] *= max(0.0001, 1.0 - lambda_home * lambda_away * self.rho)
        matrix[0, 1] *= max(0.0001, 1.0 + lambda_home * self.rho)
        matrix[1, 0] *= max(0.0001, 1.0 + lambda_away * self.rho)
        matrix[1, 1] *= max(0.0001, 1.0 - self.rho)

        matrix_sum = matrix.sum()
        if matrix_sum > 0:
            matrix /= matrix_sum

        return matrix

    def predict_match(
        self,
        home_team: Dict[str, Any],
        away_team: Dict[str, Any],
        home_rest_factor: float = 1.0,
        away_rest_factor: float = 1.0,
        top_n: int = 5
    ) -> Dict[str, Any]:
        """Calcula 1X2, marcadors exactes, Over/Under i BTTS."""
        lambda_home, lambda_away = self.estimate_lambdas(
            home_team=home_team,
            away_team=away_team,
            home_rest_factor=home_rest_factor,
            away_rest_factor=away_rest_factor
        )

        matrix = self.build_bivariate_dixon_coles_matrix(lambda_home, lambda_away)

        # 1X2
        prob_1 = float(np.tril(matrix, -1).sum()) * 100
        prob_x = float(np.diag(matrix).sum()) * 100
        prob_2 = float(np.triu(matrix, 1).sum()) * 100

        # Top Scores
        scores = []
        for i in range(self.max_goals):
            for j in range(self.max_goals):
                scores.append(((i, j), matrix[i, j]))
        scores.sort(key=lambda x: x[1], reverse=True)
        top_scores = [
            {"score": f"{s[0][0]}-{s[0][1]}", "prob": round(float(s[1]) * 100, 2)}
            for s in scores[:top_n]
        ]

        # Over / Under
        total_goals_prob = np.zeros(self.max_goals * 2)
        for i in range(self.max_goals):
            for j in range(self.max_goals):
                total_goals_prob[i + j] += matrix[i, j]

        over_2_5 = float(total_goals_prob[3:].sum()) * 100
        btts_yes = float((1.0 - matrix[0, :].sum() - matrix[:, 0].sum() + matrix[0, 0]) * 100)

        return {
            "expected_goals_home": round(lambda_home, 2),
            "expected_goals_away": round(lambda_away, 2),
            "prob_1X2": {
                "1": round(prob_1, 1),
                "X": round(prob_x, 1),
                "2": round(prob_2, 1),
            },
            "top_scorelines": top_scores,
            "over_under": {
                "over_2_5": round(over_2_5, 1),
                "under_2_5": round(100.0 - over_2_5, 1)
            },
            "btts": {
                "yes": round(btts_yes, 1),
                "no": round(100.0 - btts_yes, 1)
            }
        }
