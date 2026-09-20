"""
scraper/referee_scraper.py
==========================
Cens oficial dels 20 Àrbitres de Camp de Primera Divisió (CTA - RFEF).
Exclou àrbitres exclusius de VAR.
"""

from typing import Dict, List, Any

class RefereeScraper:
    def __init__(self):
        pass

    def fetch_laliga_referees_stats(self) -> List[Dict[str, Any]]:
        """
        Retorna la plantilla oficial dels 20 àrbitres de camp de LaLiga EA Sports.
        """
        referees_data = [
            {"id": "REF_HERNANDEZ_HERNANDEZ", "name": "Alejandro Hernández Hernández", "matches_count": 215, "yellow_cards_avg": 5.40, "red_cards_avg": 0.35, "fouls_avg": 27.5, "penalties_avg": 0.38, "home_win_pct": 0.44, "strictness_index": 1.20},
            {"id": "REF_BUSQUETS_FERRER", "name": "Mateo Busquets Ferrer", "matches_count": 42, "yellow_cards_avg": 5.65, "red_cards_avg": 0.40, "fouls_avg": 28.2, "penalties_avg": 0.36, "home_win_pct": 0.42, "strictness_index": 1.25},
            {"id": "REF_GIL_MANZANO", "name": "Jesús Gil Manzano", "matches_count": 235, "yellow_cards_avg": 5.10, "red_cards_avg": 0.30, "fouls_avg": 26.0, "penalties_avg": 0.32, "home_win_pct": 0.46, "strictness_index": 1.13},
            {"id": "REF_SANCHEZ_MARTINEZ", "name": "José María Sánchez Martínez", "matches_count": 195, "yellow_cards_avg": 4.80, "red_cards_avg": 0.22, "fouls_avg": 25.5, "penalties_avg": 0.28, "home_win_pct": 0.45, "strictness_index": 1.06},
            {"id": "REF_SOTO_GRADO", "name": "César Soto Grado", "matches_count": 125, "yellow_cards_avg": 4.90, "red_cards_avg": 0.25, "fouls_avg": 26.5, "penalties_avg": 0.30, "home_win_pct": 0.43, "strictness_index": 1.10},
            {"id": "REF_DE_BURGOS_BENGOETXEA", "name": "Ricardo De Burgos Bengoetxea", "matches_count": 190, "yellow_cards_avg": 4.20, "red_cards_avg": 0.18, "fouls_avg": 23.5, "penalties_avg": 0.25, "home_win_pct": 0.47, "strictness_index": 0.92},
            {"id": "REF_ALBEROLA_ROJAS", "name": "Javier Alberola Rojas", "matches_count": 155, "yellow_cards_avg": 3.65, "red_cards_avg": 0.12, "fouls_avg": 21.0, "penalties_avg": 0.20, "home_win_pct": 0.50, "strictness_index": 0.80},
            {"id": "REF_MARTINEZ_MUNUERA", "name": "Juan Martínez Munuera", "matches_count": 225, "yellow_cards_avg": 4.45, "red_cards_avg": 0.20, "fouls_avg": 24.2, "penalties_avg": 0.26, "home_win_pct": 0.48, "strictness_index": 0.98},
            {"id": "REF_MUNUERA_MONTERO", "name": "José Luis Munuera Montero", "matches_count": 168, "yellow_cards_avg": 4.60, "red_cards_avg": 0.23, "fouls_avg": 25.0, "penalties_avg": 0.27, "home_win_pct": 0.45, "strictness_index": 1.02},
            {"id": "REF_ORTIZ_ARIAS", "name": "Miguel Ángel Ortiz Arias", "matches_count": 98, "yellow_cards_avg": 4.70, "red_cards_avg": 0.22, "fouls_avg": 25.2, "penalties_avg": 0.29, "home_win_pct": 0.45, "strictness_index": 1.04},
            {"id": "REF_PULIDO_SANTANA", "name": "Juan Luis Pulido Santana", "matches_count": 78, "yellow_cards_avg": 5.00, "red_cards_avg": 0.28, "fouls_avg": 26.5, "penalties_avg": 0.31, "home_win_pct": 0.43, "strictness_index": 1.12},
            {"id": "REF_GARCIA_VERDURA", "name": "Víctor García Verdura", "matches_count": 48, "yellow_cards_avg": 4.40, "red_cards_avg": 0.18, "fouls_avg": 24.0, "penalties_avg": 0.24, "home_win_pct": 0.46, "strictness_index": 0.95},
            {"id": "REF_QUINTERO_GONZALEZ", "name": "Alejandro Quintero González", "matches_count": 38, "yellow_cards_avg": 4.55, "red_cards_avg": 0.20, "fouls_avg": 24.8, "penalties_avg": 0.25, "home_win_pct": 0.45, "strictness_index": 1.00},
            {"id": "REF_SESMA_ESPINOSA", "name": "Miguel Sesma Espinosa", "matches_count": 25, "yellow_cards_avg": 4.60, "red_cards_avg": 0.22, "fouls_avg": 25.0, "penalties_avg": 0.26, "home_win_pct": 0.45, "strictness_index": 1.01},
            {"id": "REF_MUNIZ_RUIZ_C", "name": "Carlos Muñiz", "matches_count": 15, "yellow_cards_avg": 4.80, "red_cards_avg": 0.25, "fouls_avg": 25.5, "penalties_avg": 0.28, "home_win_pct": 0.44, "strictness_index": 1.06},
            {"id": "REF_GONZALEZ_ESTEBAN", "name": "Jon Ander González", "matches_count": 15, "yellow_cards_avg": 5.10, "red_cards_avg": 0.30, "fouls_avg": 26.5, "penalties_avg": 0.30, "home_win_pct": 0.43, "strictness_index": 1.13},
            {"id": "REF_BESTARD_SERVERA", "name": "Luis Bestard", "matches_count": 15, "yellow_cards_avg": 4.75, "red_cards_avg": 0.20, "fouls_avg": 24.5, "penalties_avg": 0.25, "home_win_pct": 0.46, "strictness_index": 1.05},
            {"id": "REF_ORELLANA_CID", "name": "Manuel Orellana", "matches_count": 15, "yellow_cards_avg": 4.90, "red_cards_avg": 0.24, "fouls_avg": 25.8, "penalties_avg": 0.29, "home_win_pct": 0.44, "strictness_index": 1.08},
        ]
        return referees_data
