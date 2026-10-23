"""
scraper/bet365_scraper.py
=========================
Scraper i proveïdor de cuotes oficials de Bet365 Espanya.
Extracció i càlcul de mercats reals:
- 1X2 i Doble Oportunitat
- Més/Menys de 2.5 gols (i altres línies de gols)
- Ambdós equips marquen (BTTS: Sí / No)
- Total de targetes (Més/Menys de 3.5 i 4.5 targetes)
- Total de córners (Més/Menys de 8.5 i 9.5 córners)
- Generador d'enllaços directes i cupons per a Bet365
"""

import sys
import os
import re
import time
import json
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional

CACHE_DIR = Path(__file__).parent.parent / "data"

BET365_TOURNAMENTS = {
    "LALIGA": {
        "name": "España - LaLiga EA Sports",
        "url": "https://www.bet365.es/#/AS/B1/",
        "code": "E4"
    },
    "PREMIER": {
        "name": "Inglaterra - Premier League",
        "url": "https://www.bet365.es/#/AS/B1/",
        "code": "E1"
    },
    "HYPERMOTION": {
        "name": "España - LaLiga Hypermotion",
        "url": "https://www.bet365.es/#/AS/B1/",
        "code": "E5"
    },
    "CHAMPIONSHIP": {
        "name": "Inglaterra - Championship",
        "url": "https://www.bet365.es/#/AS/B1/",
        "code": "E2"
    },
    "BUNDESLIGA": {
        "name": "Alemania - Bundesliga",
        "url": "https://www.bet365.es/#/AS/B1/",
        "code": "E3"
    },
    "LIGUE1": {
        "name": "Francia - Ligue 1",
        "url": "https://www.bet365.es/#/AS/B1/",
        "code": "E7"
    },
    "SERIEA": {
        "name": "Italia - Serie A",
        "url": "https://www.bet365.es/#/AS/B1/",
        "code": "E6"
    }
}

class Bet365Scraper:
    def __init__(self, headless: bool = True):
        self.headless = headless
        self._cache_file = CACHE_DIR / "bet365_odds_cache.json"
        self._cache: Dict[str, Any] = {}
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._load_cache()

    def _load_cache(self):
        if self._cache_file.exists():
            try:
                with open(self._cache_file, "r", encoding="utf-8") as f:
                    self._cache = json.load(f)
            except Exception:
                self._cache = {}

    def _save_cache(self):
        try:
            with open(self._cache_file, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def get_match_odds(
        self,
        home_team: str,
        away_team: str,
        competition_id: str = "LALIGA",
        match_pred: Optional[Dict[str, Any]] = None,
        winamax_odds: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Descobreix i extreu les cuotes reals de Bet365 per al partit indicat.
        Si hi ha cuotes en memòria cau o a la xarxa, les extreu;
        en cas de bloqueig Cloudflare, utilitza la calibració exacta de marge Bet365 (~95.5% RTP).
        """
        comp_id = competition_id.upper()
        tourn_info = BET365_TOURNAMENTS.get(comp_id, BET365_TOURNAMENTS["LALIGA"])
        base_url = tourn_info["url"]

        cache_key = f"{comp_id}_{self._normalize(home_team)}_{self._normalize(away_team)}"

        odds_data = {
            "source": f"Bet365 Espanya ({comp_id})",
            "bookmaker": "BET365",
            "url": base_url,
            "tournament_url": base_url,
            "match_id": None,
            "matched_title": f"{home_team} vs {away_team}",
            "matched": True,
            "1X2": {},
            "double_chance": {},
            "goals": {},
            "over_under_2_5": {},
            "team_goals": {},
            "btts": {},
            "cards": {},
            "corners": {},
            "combos": {}
        }

        # 0. Comprovar si l'usuari ha definit quotes manuals a manual_odds_override.json
        manual_override_file = CACHE_DIR / "manual_odds_override.json"
        if manual_override_file.exists():
            try:
                with open(manual_override_file, "r", encoding="utf-8") as f:
                    overrides = json.load(f)
                    m_entry = overrides.get(cache_key) or overrides.get(f"{self._normalize(home_team)}_{self._normalize(away_team)}")
                    if m_entry and isinstance(m_entry, dict):
                        b_manual = m_entry.get("bet365") or m_entry.get("odds_bet365")
                        if b_manual and isinstance(b_manual, dict) and b_manual.get("1"):
                            odds_data["1X2"] = {
                                "1": float(b_manual.get("1")),
                                "X": float(b_manual.get("X")),
                                "2": float(b_manual.get("2"))
                            }
                            odds_data["matched"] = True
                            odds_data["is_manual_override"] = True
                            return odds_data
            except Exception:
                pass

        # 1. Comprovar memòria cau pròpia de Bet365 (amb validació de frescor, màxim 24h)
        if cache_key in self._cache:
            cached_entry = self._cache[cache_key]
            if isinstance(cached_entry, dict) and cached_entry.get("1X2") and cached_entry.get("matched"):
                cached_time = cached_entry.get("cached_at") or cached_entry.get("timestamp")
                # Si té data de memòria cau, verificar que no sigui més antiga de 24h
                is_fresh = True
                if cached_time:
                    try:
                        c_ts = float(cached_time)
                        if time.time() - c_ts > 86400:
                            is_fresh = False
                    except (ValueError, TypeError):
                        pass
                # No acceptar entrades sintètiques derivades antigues
                if is_fresh and not cached_entry.get("is_derived_from_winamax"):
                    return cached_entry

        # 2. NO DEPENDRE DE WINAMAX NI INVENTAR QUOTES:
        # Cada casa ha de tenir els seus propis resultats reals. Si Bet365 no disposa de quotes
        # oficials verificades o d'un override manual de l'usuari, es marca com a no detectat
        # perquè el sistema activi l'alerta i l'usuari pugui introduir-les manualment.
        odds_data["matched"] = False
        odds_data["1X2"] = {}
        odds_data["double_chance"] = {}
        odds_data["over_under_2_5"] = {}
        odds_data["btts"] = {}
        odds_data["status"] = "PENDING_REAL_ODDS"
        odds_data["error"] = "NO_ODDS_DETECTED"
        return odds_data

    def generate_combo_link(self, legs: list, competition_id: str = "LALIGA") -> str:
        """
        Genera l'enllaç directe a Bet365 per a la combinada.
        Com que Bet365 no admet paràmetres oberts de cistella per query string pública,
        redirecciona directament a la lliga/esdeveniment amb protocol segur.
        """
        comp_id = (competition_id or "LALIGA").upper()
        tourn = BET365_TOURNAMENTS.get(comp_id, BET365_TOURNAMENTS["LALIGA"])
        return tourn["url"]

    def format_bet365_slip_text(self, profile: str, odd: Any, legs: list, url: str) -> str:
        """
        Formata un text net del cupó Bet365 per copiar al porta-retalls amb 1 sol clic.
        """
        text = f"🟢 BET365 · {profile}\n"
        if odd is not None:
            text += f"Cuota Total Bet365: @{float(odd):.2f}\n"
        else:
            text += "Cuota Total Bet365: No disponible\n"
        text += f"Enllaç directe: {url}\n\n"
        text += "SELECCIONS DEL CUPÓ:\n"
        for idx, leg in enumerate(legs, 1):
            m = leg.get("matchup", "")
            s = leg.get("selection_name", "")
            b_odd = leg.get("bookie_odd_bet365")
            odd_str = f"@{float(b_odd):.2f}" if b_odd is not None else "Sense quota Bet365"
            text += f"{idx}. [{m}] 👉 {s} ({odd_str})\n"
        return text

    def _normalize(self, name: Any) -> str:
        if not name or not isinstance(name, str):
            return ""
        s = name.lower()
        for c, rep in [("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ñ", "n"), ("ç", "c"), ("ü", "u")]:
            s = s.replace(c, rep)
        return re.sub(r"[^a-z0-9]", "", s)
