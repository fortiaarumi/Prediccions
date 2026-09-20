"""
model/rank_engine.py
====================
Motor de Rànquings Dinàmics i Elo centrat exclusivament en La Lliga.

Com s'utilitza l'xG (Gols Esperats) automàticament:
- Els gols reals tenen alta variància per la sort (rebots, pals, gols en pròpia).
- L'xG mesura la qualitat real de creació de joc i perill generat.
- El model utilitza una mètrica combinada (65% xG + 35% Gols Reals) per calcular
  la desviació i actualitzar el rànquing ofensiu i defensiu.
"""

from typing import Dict, Any, Tuple
import math

class RankEngine:
    def __init__(self, k_factor: float = 32.0, league_teams_count: float = 20.0):
        self.k_factor = k_factor
        self.league_teams_count = float(league_teams_count)

    def calculate_elo_change(
        self,
        home_elo: float,
        away_elo: float,
        home_goals: int,
        away_goals: int
    ) -> Tuple[float, float]:
        """Calcula el canvi en la puntuació Elo per a un partit de futbol."""
        # Probabilitat esperada de victòria local (+50 punts avantatge de camp)
        expected_home = 1.0 / (1.0 + 10.0 ** ((away_elo - home_elo - 50.0) / 400.0))
        expected_away = 1.0 - expected_home

        # Resultat real
        if home_goals > away_goals:
            actual_home, actual_away = 1.0, 0.0
        elif home_goals == away_goals:
            actual_home, actual_away = 0.5, 0.5
        else:
            actual_home, actual_away = 0.0, 1.0

        # Multiplicador per diferència de gols
        goal_diff = abs(home_goals - away_goals)
        margin_multiplier = math.log(max(1, goal_diff) + 1.0)

        effective_k = self.k_factor * margin_multiplier

        delta_home = effective_k * (actual_home - expected_home)
        delta_away = effective_k * (actual_away - expected_away)

        return delta_home, delta_away

    def update_tri_ranks(
        self,
        current_off_rank: float,
        current_def_rank: float,
        goals_scored: int,
        goals_conceded: int,
        xg_for: float,
        xg_against: float,
        expected_goals_model: float
    ) -> Tuple[float, float, float]:
        """
        Actualitza els rànquings ofensiu i defensiu combinant gols reals i xG.
        """
        # Perill ofensiu real generat (65% xG + 35% Gols marcats)
        effective_off_output = 0.65 * xg_for + 0.35 * goals_scored
        off_performance = effective_off_output - expected_goals_model
        
        # Ajust del rank ofensiu (baixar el número = millorar)
        shift_off = -0.30 * off_performance
        new_off = max(1.0, min(self.league_teams_count, current_off_rank + shift_off))

        # Solidesa defensiva real (65% xG concedit + 35% Gols encaixats)
        effective_def_conceded = 0.65 * xg_against + 0.35 * goals_conceded
        def_performance = expected_goals_model - effective_def_conceded
        
        # Ajust del rank defensiu
        shift_def = -0.30 * def_performance
        new_def = max(1.0, min(self.league_teams_count, current_def_rank + shift_def))

        new_general = round((new_off + new_def) / 2.0, 2)
        return new_general, round(new_off, 2), round(new_def, 2)

