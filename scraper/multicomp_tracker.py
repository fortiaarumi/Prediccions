"""
scraper/multicomp_tracker.py
============================
Seguiment de dies de descans i fatiga exclusivament per a La Lliga.

Aplica una ponderació suau (poc impacte, 1-3% màxim) perquè el descans
sigui un factor secundari i no distorsioni la qualitat real de l'equip.
"""

from datetime import datetime
from typing import Dict, Any

class MulticompTracker:
    def __init__(self):
        pass

    def calculate_rest_days(self, last_match_date_str: str, next_match_date_str: str) -> float:
        """Calcula els dies naturals de descans entre partits de lliga."""
        try:
            d1 = datetime.strptime(last_match_date_str.split(" ")[0], "%Y-%m-%d")
            d2 = datetime.strptime(next_match_date_str.split(" ")[0], "%Y-%m-%d")
            diff = (d2 - d1).days
            return max(1.0, float(diff))
        except Exception:
            return 7.0  # 1 setmana per defecte (jornada estàndard de cap de setmana)

    def get_fatigue_multiplier(self, rest_days: float, *args, **kwargs) -> float:
        """
        Factor suau de descans:
        - >= 5 dies: 1.00 (100% rendiment)
        - 4 dies: 0.99 (99% rendiment)
        - 3 dies o menys: 0.975 (97.5% rendiment, màxima penalització suau del 2.5%)
        """
        if rest_days <= 3:
            return 0.975
        elif rest_days == 4:
            return 0.99
        else:
            return 1.00
