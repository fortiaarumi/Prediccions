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
import json
import time
import subprocess
import copy
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional

CACHE_DIR = Path(__file__).parent.parent / "data"

DEFAULT_MAX_ODDS_AGE_HOURS: float = 2.0
DEFAULT_MAX_FUTURE_SKEW_SECONDS: float = 300.0  # 5 minuts de tolerància per desajust de rellotge

def parse_datetime_utc(dt_val: Any) -> Optional[datetime]:
    """Parseja de forma robusta un timestamp a datetime amb timezone UTC."""
    if not dt_val:
        return None
    if isinstance(dt_val, datetime):
        if dt_val.tzinfo is None:
            return dt_val.replace(tzinfo=timezone.utc)
        return dt_val.astimezone(timezone.utc)
    if isinstance(dt_val, (int, float)):
        try:
            return datetime.fromtimestamp(dt_val, tz=timezone.utc)
        except Exception:
            return None
    if isinstance(dt_val, str):
        s = dt_val.strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            pass
        for fmt in ("%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M", "%Y-%m-%dT%H:%M:%S"):
            try:
                dt = datetime.strptime(s, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except Exception:
                pass
    return None

def evaluate_freshness(
    captured_at: Any,
    now: Optional[datetime] = None,
    max_age_hours: Optional[float] = None,
    max_future_skew_seconds: float = DEFAULT_MAX_FUTURE_SKEW_SECONDS
) -> Dict[str, Any]:
    """
    Avalua la frescor d'una quota Bet365 segons la política oficial:
    - FRESH: Quota vàlida i recent (age <= max_age_hours).
    - STALE: Quota amb timestamp vàlid però que supera el llindar d'antiguitat.
    - UNKNOWN: Quota sense timestamp fiable, mal format o amb timestamp futur incoherent.

    Tant STALE com UNKNOWN tenen is_stale = True i han de ser exclosos de les value bets públiques.
    """
    now_utc = now if (now is not None and now.tzinfo is not None) else (now.replace(tzinfo=timezone.utc) if now else datetime.now(timezone.utc))
    max_h = float(max_age_hours if max_age_hours is not None else float(os.environ.get("BET365_MAX_ODDS_AGE_HOURS", DEFAULT_MAX_ODDS_AGE_HOURS)))
    max_age_seconds = max_h * 3600.0

    if not captured_at or (isinstance(captured_at, str) and not captured_at.strip()):
        return {
            "freshness": "UNKNOWN",
            "is_stale": True,
            "captured_at": None,
            "age_seconds": None,
            "rejection_reason": "missing_timestamp"
        }

    dt = parse_datetime_utc(captured_at)
    if not dt:
        return {
            "freshness": "UNKNOWN",
            "is_stale": True,
            "captured_at": str(captured_at),
            "age_seconds": None,
            "rejection_reason": "malformed_timestamp"
        }

    age_s = (now_utc - dt).total_seconds()

    if age_s < -max_future_skew_seconds:
        # Desajust cap al futur per sobre de la tolerància
        return {
            "freshness": "UNKNOWN",
            "is_stale": True,
            "captured_at": dt.isoformat(),
            "age_seconds": round(age_s, 2),
            "rejection_reason": "future_clock_skew"
        }

    if age_s < 0:
        age_s = 0.0

    if age_s > max_age_seconds:
        return {
            "freshness": "STALE",
            "is_stale": True,
            "captured_at": dt.isoformat(),
            "age_seconds": round(age_s, 2),
            "rejection_reason": "exceeded_max_age"
        }

    return {
        "freshness": "FRESH",
        "is_stale": False,
        "captured_at": dt.isoformat(),
        "age_seconds": round(age_s, 2),
        "rejection_reason": "ok"
    }

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
    def __init__(
        self,
        headless: bool = True,
        cache_dir: Optional[Path] = None,
        cache_file: Optional[Path] = None,
        max_odds_age_hours: Optional[float] = None,
        offline: bool = False,
        force_fresh: bool = False
    ):
        self.headless = headless
        if cache_file:
            self._cache_file = Path(cache_file)
        elif cache_dir:
            self._cache_file = Path(cache_dir) / "bet365_odds_cache.json"
        else:
            self._cache_file = CACHE_DIR / "bet365_odds_cache.json"
        self._cache_file.parent.mkdir(parents=True, exist_ok=True)
        self.max_odds_age_hours = float(
            max_odds_age_hours if max_odds_age_hours is not None
            else float(os.environ.get("BET365_MAX_ODDS_AGE_HOURS", DEFAULT_MAX_ODDS_AGE_HOURS))
        )
        self.offline = offline or bool(os.environ.get("PREDDICIONS_TEST_OFFLINE"))
        self.force_fresh = force_fresh
        self._cache: Dict[str, Any] = {}
        self._load_cache()

    def get_historical_cache_entry(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """Retorna l'entrada històrica crua emmagatzemada a la memòria cau sense aplicar exclusions."""
        return copy.deepcopy(self._cache.get(cache_key))

    def save_cache_entry(self, cache_key: str, entry: Dict[str, Any], captured_at: Optional[str] = None) -> None:
        """Desa una entrada a la memòria cau amb l'embolcall de metadades UTC oficials."""
        now_utc = captured_at or entry.get("captured_at") or datetime.now(timezone.utc).isoformat()
        existing = self._cache.get(cache_key)
        if existing and "historical_odds" in existing and "historical_odds" not in entry:
            entry["historical_odds"] = copy.deepcopy(existing["historical_odds"])
        entry["captured_at"] = now_utc
        entry["polled_at"] = entry.get("polled_at") or now_utc
        entry["freshness"] = "FRESH"
        entry["is_stale"] = False
        entry["provenance"] = entry.get("provenance", "BET365")
        if "provider_updated_at" not in entry:
            entry["provider_updated_at"] = None
        self._cache[cache_key] = entry
        self._save_cache()

    def update_match_markets(
        self,
        cache_key: str,
        updated_markets: Dict[str, Any],
        captured_at: Optional[str] = None,
        market_metadata: Optional[Dict[str, Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Actualitza exclusivament els mercats indicats per a un partit a la memòria cau,
        garantint que els mercats no actualitzats NO reben un timestamp nou artificialment
        i conserven la seva pròpia marca temporal d'origen.
        """
        entry = self._cache.get(cache_key)
        now_utc = captured_at or (entry.get("captured_at") if entry else None) or datetime.now(timezone.utc).isoformat()
        if not entry:
            entry = {
                "source": "Bet365 Espanya",
                "bookmaker": "BET365",
                "matched": True,
                "captured_at": now_utc,
                "polled_at": now_utc,
                "is_stale": False,
                "provenance": "BET365",
                "market_status": {},
                "1X2": {},
                "double_chance": {},
                "over_under_2_5": {},
                "btts": {},
                "corners": {},
                "cards": {},
            }
            self._cache[cache_key] = entry

        m_stat = entry.setdefault("market_status", {})

        # Actualitzar NOMÉS els mercats indicats a updated_markets
        for m_name, m_odds in updated_markets.items():
            prev_odds = entry.get(m_name)
            meta = (market_metadata or {}).get(m_name, {})
            is_composite = (
                m_name in ("corners", "cards") or
                m_name.startswith("corners_") or
                m_name.startswith("cards_") or
                m_name in ("home_corners", "away_corners", "corners_1st_half", "corners_2nd_half")
            )

            if isinstance(m_odds, dict) and isinstance(prev_odds, dict) and is_composite:
                # Actualització parcial de línies/seleccions dins del mateix mercat compost
                existing_selections = m_stat.setdefault(m_name, {}).setdefault("selections", {})
                prev_market_ts = m_stat.get(m_name, {}).get("captured_at", now_utc)

                # Inicialitzar seleccions prèvies si no tenien metadades individuals
                for p_sel in prev_odds:
                    if p_sel not in existing_selections:
                        existing_selections[p_sel] = {
                            "valid": True,
                            "is_stale": False,
                            "captured_at": prev_market_ts,
                            "polled_at": prev_market_ts,
                            "ts": "",
                            "provider_updated_at": None,
                        }

                # Combinar mercat actiu i arxivar seleccions substituïdes a historical_odds
                merged_odds = copy.deepcopy(prev_odds)
                for s_key, s_val in m_odds.items():
                    if s_key in merged_odds:
                        entry.setdefault("historical_odds", {}).setdefault(m_name, {})[s_key] = copy.deepcopy(merged_odds[s_key])
                    merged_odds[s_key] = s_val
                    existing_selections[s_key] = {
                        "valid": bool(s_val),
                        "is_stale": False,
                        "captured_at": now_utc,
                        "polled_at": meta.get("polled_at", now_utc),
                        "ts": meta.get("ts", ""),
                        "provider_updated_at": meta.get("provider_updated_at", None),
                    }
                entry[m_name] = merged_odds
                m_stat[m_name] = {
                    "valid": bool(merged_odds),
                    "stale": False,
                    "suspended": False,
                    "rejection_reason": "ok" if merged_odds else "absent",
                    "captured_at": now_utc,
                    "polled_at": meta.get("polled_at", now_utc),
                    "ts": meta.get("ts", ""),
                    "provider_updated_at": meta.get("provider_updated_at", None),
                    "selections": existing_selections,
                }
            else:
                if prev_odds:
                    entry.setdefault("historical_odds", {})[m_name] = copy.deepcopy(prev_odds)
                entry[m_name] = m_odds
                m_stat[m_name] = {
                    "valid": bool(m_odds),
                    "stale": False,
                    "suspended": False,
                    "rejection_reason": "ok" if m_odds else "absent",
                    "captured_at": now_utc,
                    "polled_at": meta.get("polled_at", now_utc),
                    "ts": meta.get("ts", ""),
                    "provider_updated_at": meta.get("provider_updated_at", None),
                }

        entry["captured_at"] = now_utc
        self._save_cache()
        return entry

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
        winamax_odds: Optional[Dict[str, Any]] = None,
        match_date_time: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Descobreix i extreu les quotes reals de Bet365 per al partit indicat.
        Prioritza sempre les dades fresques i confirmades del feed oficial en viu (MoviOdds).
        La memòria cau s'utilitza exclusivament com a alternativa/fallback si no hi ha dades actuals,
        verificant sempre la data de confirmació de la font (polled_at o ts), mai cached_at.
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
            "matched": False,
            "captured_at": None,
            "freshness": "UNKNOWN",
            "is_stale": True,
            "freshness_reason": "not_processed",
            "age_seconds": None,
            "provider_updated_at": None,
            "provenance": "BET365",
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
                            now_utc_str = datetime.now(timezone.utc).isoformat()
                            odds_data["matched"] = True
                            odds_data["is_manual_override"] = True
                            odds_data["source"] = f"Override Manual Bet365 ({comp_id})"
                            odds_data["provenance"] = "MANUAL_OVERRIDE"
                            odds_data["captured_at"] = now_utc_str
                            odds_data["polled_at"] = now_utc_str
                            odds_data["freshness"] = "FRESH"
                            odds_data["is_stale"] = False
                            odds_data["freshness_reason"] = "manual_override"
                            return odds_data
            except Exception:
                pass

        # 1. PRIORITAT 1: Comprovar font oficial Bet365 en viu via MoviOdds (feed actualitzat té prioritat sobre memòria cau antiga)
        try:
            from scraper.moviodds_client import MoviOddsClient
            movi_client = MoviOddsClient.get_instance()
            if movi_client.has_feed():
                api_odds = movi_client.get_match_odds(home_team, away_team, comp_id, match_date_time=match_date_time)
                if api_odds and api_odds.get("matched") and not api_odds.get("is_stale"):
                    h_norm = self._normalize(home_team)
                    a_norm = self._normalize(away_team)
                    cache_key = f"{comp_id}_{h_norm}_{a_norm}"
                    self._cache[cache_key] = api_odds
                    self._save_cache()
                    return api_odds
        except Exception:
            pass

        # 2. ALTERNATIVA / FALLBACK: Memòria cau pròpia de Bet365 revalidant cadascun dels mercats individualment
        if cache_key in self._cache:
            cached_entry = self._cache[cache_key]
            revalidated = self.revalidate_cached_entry(cached_entry, now=now)
            if revalidated and revalidated.get("matched"):
                return revalidated

        # 3. Comprovar font especialitzada Bet365 autoritzada (Odds Data API) si hi ha token configurat
        token = os.environ.get("ODDS_DATA_API_TOKEN")
        if token:
            try:
                from scraper.odds_data_api_client import OddsDataApiClient
                client = OddsDataApiClient(token=token)
                api_odds = client.get_match_odds(home_team, away_team, comp_id)
                if api_odds and api_odds.get("matched"):
                    h_norm = self._normalize(home_team)
                    a_norm = self._normalize(away_team)
                    cache_key = f"{comp_id}_{h_norm}_{a_norm}"
                    self._cache[cache_key] = api_odds
                    self._save_cache()
                    return api_odds
            except Exception as e:
                pass

        # 4. Comprovar font autoritzada secundària (The Odds API) si hi ha clau de servei B2B configurada
        api_key = os.environ.get("THE_ODDS_API_KEY") or os.environ.get("ODDS_API_KEY")
        if api_key:
            api_odds = self._fetch_odds_api(home_team, away_team, comp_id, api_key)
            if api_odds and api_odds.get("matched"):
                return api_odds

        # 5. NO DEPENDRE DE WINAMAX NI INVENTAR QUOTES:
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
        odds_data["access_status"] = "REQUIRES_ODDS_DATA_API_TOKEN"
        odds_data["access_diagnostics"] = "Bloqueig per Cloudflare Enterprise Challenge a nivell de xarxa per accés directe a bet365.es. Requereix ODDS_DATA_API_TOKEN per a dades prematch oficials (https://api.odds-data-api.com/)."
        return odds_data

    def revalidate_cached_entry(self, cached_entry: Dict[str, Any], now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
        """
        Revalida cadascun dels mercats individualment abans de retornar una entrada de memòria cau.
        - Elimina qualsevol mercat sense confirmació positiva i suficient (marques polled_at / ts de la font).
        - Elimina mercats marcats com a obsolets (stale), suspesos o invàlids.
        - Conserva només els mercats estrictament vàlids.
        - Si el mercat principal 1X2 no és vàlid o no té confirmació suficient, es descarta tota l'entrada.
        """
        if not isinstance(cached_entry, dict):
            return None

        # Rebuig si prové de Winamax
        if cached_entry.get("is_derived_from_winamax"):
            return None

        now_utc = now if (now is not None and now.tzinfo is not None) else (now.replace(tzinfo=timezone.utc) if now else datetime.now(timezone.utc))
        entry = copy.deepcopy(cached_entry)
        m_stat = entry.setdefault("market_status", {})

        # Resolució de frescor global Bet365
        if self.force_fresh:
            freshness_info = {
                "freshness": "FRESH",
                "is_stale": False,
                "captured_at": entry.get("captured_at") or entry.get("polled_at") or now_utc.isoformat(),
                "age_seconds": 0.0,
                "rejection_reason": "ok"
            }
        else:
            cand_ts = entry.get("captured_at") or entry.get("polled_at") or entry.get("ts")
            freshness_info = evaluate_freshness(cand_ts, now=now_utc, max_age_hours=self.max_odds_age_hours)

        entry["captured_at"] = freshness_info.get("captured_at")
        entry["polled_at"] = freshness_info.get("captured_at")
        entry["freshness"] = freshness_info.get("freshness")
        entry["is_stale"] = freshness_info.get("is_stale")
        entry["freshness_reason"] = freshness_info.get("rejection_reason")
        entry["age_seconds"] = freshness_info.get("age_seconds")
        entry["provenance"] = entry.get("provenance", "BET365")

        base_markets = ["1X2", "double_chance", "over_under_2_5", "btts", "corners", "cards"]
        ignored_meta_keys = {
            "source", "bookmaker", "url", "tournament_url", "match_id", "matched_title",
            "matched", "captured_at", "polled_at", "freshness", "is_stale", "freshness_reason",
            "age_seconds", "provider_updated_at", "provenance", "status", "error", "access_status",
            "access_diagnostics", "historical_odds", "market_status", "is_manual_override",
            "kickoff_utc", "match_start", "start_time", "is_derived_from_winamax"
        }
        extra_markets = [k for k in list(entry.keys()) + list(m_stat.keys()) if k not in ignored_meta_keys and not k.startswith("_")]
        markets = list(dict.fromkeys(base_markets + extra_markets))

        for m in markets:
            # 1. Comprovació de plantilles sintètiques conegudes
            if m == "over_under_2_5" and entry.get("over_under_2_5") == {"over": 1.98, "under": 1.87}:
                entry["over_under_2_5"] = {}
                m_stat[m] = {"valid": False, "stale": False, "suspended": False, "rejection_reason": "unverified"}
                continue
            if m == "btts" and entry.get("btts") == {"yes": 1.93, "no": 1.92}:
                entry["btts"] = {}
                m_stat[m] = {"valid": False, "stale": False, "suspended": False, "rejection_reason": "unverified"}
                continue

            # 2. Si el mercat és buit a l'entrada
            m_val = entry.get(m)
            if not m_val:
                entry[m] = {}
                m_stat.setdefault(m, {"valid": False, "stale": False, "suspended": False, "rejection_reason": "absent"})
                continue

            # 3. Comprovació d'estat a market_status
            st = m_stat.get(m)
            if not isinstance(st, dict):
                # No hi ha confirmació positiva d'aquest mercat
                entry[m] = {}
                m_stat[m] = {"valid": False, "stale": False, "suspended": False, "rejection_reason": "unverified"}
                continue

            # Si està marcat com a invalid, stale o suspès
            if st.get("valid") is not True or st.get("stale") is True or st.get("suspended") is True:
                reason = "suspended" if st.get("suspended") else ("stale" if st.get("stale") else st.get("rejection_reason", "unverified"))
                entry.setdefault("historical_odds", {})[m] = copy.deepcopy(m_val)
                entry[m] = {}
                m_stat[m] = {
                    "valid": False,
                    "stale": bool(st.get("stale")),
                    "suspended": bool(st.get("suspended")),
                    "rejection_reason": reason,
                    "polled_at": st.get("polled_at"),
                    "ts": st.get("ts"),
                    "captured_at": st.get("captured_at"),
                    "historical_odds": copy.deepcopy(m_val),
                }
                continue

            # 4. Validació de frescor temporal individual del mercat
            m_time = st.get("captured_at") or st.get("polled_at") or st.get("ts")
            selections_stat = st.get("selections")
            is_composite = (
                m in ("corners", "cards") or
                m.startswith("corners_") or
                m.startswith("cards_") or
                m in ("home_corners", "away_corners", "corners_1st_half", "corners_2nd_half")
            )
            if isinstance(m_val, dict) and isinstance(selections_stat, dict) and len(selections_stat) > 0 and is_composite:
                active_selections = {}
                max_s = self.max_odds_age_hours * 3600.0
                for sel_name, sel_odd in list(m_val.items()):
                    sel_meta = selections_stat.get(sel_name, {})
                    sel_time = sel_meta.get("captured_at") or sel_meta.get("polled_at") or sel_meta.get("ts") or m_time
                    sel_dt = parse_datetime_utc(sel_time)
                    if not sel_dt:
                        entry.setdefault("historical_odds", {}).setdefault(m, {})[sel_name] = sel_odd
                        sel_meta["valid"] = False
                        sel_meta["is_stale"] = True
                        sel_meta["rejection_reason"] = "unverified"
                        continue

                    sel_age = (now_utc - sel_dt).total_seconds()
                    if sel_age < -DEFAULT_MAX_FUTURE_SKEW_SECONDS or sel_age > max_s:
                        entry.setdefault("historical_odds", {}).setdefault(m, {})[sel_name] = sel_odd
                        sel_meta["valid"] = False
                        sel_meta["is_stale"] = True
                        sel_meta["rejection_reason"] = "future_clock_skew" if sel_age < -DEFAULT_MAX_FUTURE_SKEW_SECONDS else "stale"
                    else:
                        active_selections[sel_name] = sel_odd
                        sel_meta["valid"] = True
                        sel_meta["is_stale"] = False
                        sel_meta["rejection_reason"] = "ok"

                entry[m] = active_selections
                if not active_selections:
                    m_stat[m] = {
                        "valid": False,
                        "stale": True,
                        "suspended": False,
                        "rejection_reason": "stale",
                        "polled_at": st.get("polled_at"),
                        "ts": st.get("ts"),
                        "captured_at": st.get("captured_at"),
                        "selections": selections_stat,
                        "historical_odds": copy.deepcopy(m_val),
                    }
                else:
                    m_stat[m]["valid"] = True
                    m_stat[m]["stale"] = False
                    m_stat[m]["selections"] = selections_stat
                continue
            if not m_time:
                entry[m] = {}
                m_stat[m] = {
                    "valid": False,
                    "stale": False,
                    "suspended": False,
                    "rejection_reason": "unverified",
                }
                continue

            s_dt = parse_datetime_utc(m_time)
            if not s_dt:
                entry[m] = {}
                m_stat[m] = {
                    "valid": False,
                    "stale": False,
                    "suspended": False,
                    "rejection_reason": "unverified",
                    "polled_at": st.get("polled_at"),
                    "ts": st.get("ts"),
                }
                continue

            if self.force_fresh:
                m_stat[m] = {
                    "valid": True,
                    "stale": False,
                    "suspended": False,
                    "rejection_reason": "ok",
                    "polled_at": st.get("polled_at") or entry.get("captured_at"),
                    "ts": st.get("ts"),
                }
                continue

            max_s = self.max_odds_age_hours * 3600.0
            age_s = (now_utc - s_dt).total_seconds()
            if age_s < -DEFAULT_MAX_FUTURE_SKEW_SECONDS or age_s > max_s:
                # Mercat caducat/obsolet individualment o amb clock skew:
                # La quota històrica i el timestamp es conserven a nivell d'auditoria,
                # però s'esborren del diccionari actiu per excloure'l d'all_value_bets
                entry.setdefault("historical_odds", {})[m] = copy.deepcopy(m_val)
                entry[m] = {}
                m_stat[m] = {
                    "valid": False,
                    "stale": True,
                    "suspended": False,
                    "rejection_reason": "future_clock_skew" if age_s < -DEFAULT_MAX_FUTURE_SKEW_SECONDS else "stale",
                    "polled_at": st.get("polled_at"),
                    "ts": st.get("ts"),
                    "captured_at": st.get("captured_at"),
                    "historical_odds": copy.deepcopy(m_val),
                }
                continue

            # Mercat vàlid i confirmat
            m_stat[m] = {
                "valid": True,
                "stale": False,
                "suspended": False,
                "rejection_reason": "ok",
                "polled_at": st.get("polled_at"),
                "ts": st.get("ts"),
            }

        # 5. El mercat principal 1X2 ha de ser vàlid i complet
        is_1x2_valid = (
            bool(entry.get("1X2"))
            and len(entry["1X2"]) == 3
            and m_stat.get("1X2", {}).get("valid") is True
            and not m_stat.get("1X2", {}).get("stale")
        )

        if not is_1x2_valid or entry.get("freshness") in ("STALE", "UNKNOWN"):
            entry["matched"] = False
            entry["is_stale"] = True
            if entry.get("freshness") not in ("STALE", "UNKNOWN"):
                entry["freshness"] = "STALE" if m_stat.get("1X2", {}).get("stale") else "UNKNOWN"
            return None

        entry["matched"] = True
        entry["is_stale"] = False
        entry["freshness"] = "FRESH"
        entry["provenance"] = entry.get("provenance", "BET365_MOVIODDS")
        return entry

    def get_odds_data_api_info(self) -> Dict[str, Any]:
        """Retorna la informació del catàleg, plans i guia de configuració d'Odds Data API."""
        from scraper.odds_data_api_client import OddsDataApiClient
        return OddsDataApiClient.get_service_info()

    def check_direct_access(self) -> Dict[str, Any]:
        """
        Executa una comprovació de connectivitat a bet365.es i retorna el diagnòstic tècnic empíric.
        """
        cmd = ['curl.exe' if os.name == 'nt' else 'curl', '-s', '-I', '-A', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36', 'https://www.bet365.es/#/AS/B1/']
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=5)
            headers = res.stdout
            is_cf_403 = ('403' in headers) and ('cloudflare' in headers.lower())
            is_cf_mitigated = 'cf-mitigated: challenge' in headers.lower()
            return {
                "url": "https://www.bet365.es/#/AS/B1/",
                "is_blocked": is_cf_403 or is_cf_mitigated,
                "status_code": 403 if is_cf_403 else 200,
                "server": "cloudflare",
                "challenge": "Turnstile Managed Challenge" if is_cf_mitigated else "None",
                "message": "Bloqueig per Cloudflare Enterprise Challenge a nivell de xarxa per a peticions automatitzades directes."
            }
        except Exception as e:
            return {
                "url": "https://www.bet365.es/#/AS/B1/",
                "is_blocked": True,
                "error": str(e),
                "message": "Error de xarxa en contactar amb bet365.es."
            }

    def _fetch_odds_api(self, home_team: str, away_team: str, comp_id: str, api_key: str) -> Optional[Dict[str, Any]]:
        """
        Consulta legalment The Odds API (the-odds-api.com) per obtenir quotes oficials de Bet365.
        """
        sport_map = {
            "LALIGA": "soccer_spain_la_liga",
            "HYPERMOTION": "soccer_spain_segunda_division",
            "PREMIER": "soccer_epl",
            "CHAMPIONSHIP": "soccer_england_championship",
            "BUNDESLIGA": "soccer_germany_bundesliga",
            "LIGUE1": "soccer_france_ligue_one",
            "SERIEA": "soccer_italy_serie_a"
        }
        sport_key = sport_map.get(comp_id)
        if not sport_key:
            return None

        if not hasattr(self, "_api_cache"):
            self._api_cache: Dict[str, list] = {}

        if sport_key not in self._api_cache:
            try:
                import urllib.request
                import ssl
                url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds?regions=eu&bookmakers=bet365&markets=h2h,totals&apiKey={api_key}"
                ctx = ssl.create_default_context()
                req = urllib.request.Request(url, headers={"User-Agent": "PrediccionsModel/1.0"})
                with urllib.request.urlopen(req, context=ctx, timeout=8) as resp:
                    if resp.status == 200:
                        self._api_cache[sport_key] = json.loads(resp.read().decode("utf-8"))
                    else:
                        self._api_cache[sport_key] = []
            except Exception:
                self._api_cache[sport_key] = []

        events = self._api_cache.get(sport_key, [])
        h_norm = self._normalize(home_team)
        a_norm = self._normalize(away_team)

        for ev in events:
            ev_h = self._normalize(ev.get("home_team", ""))
            ev_a = self._normalize(ev.get("away_team", ""))
            if (h_norm in ev_h or ev_h in h_norm) and (a_norm in ev_a or ev_a in a_norm):
                for bm in ev.get("bookmakers", []):
                    if bm.get("key") == "bet365":
                        odds_data = {
                            "source": f"Bet365 via The-Odds-API ({comp_id})",
                            "bookmaker": "BET365",
                            "url": "https://www.bet365.es/#/AS/B1/",
                            "tournament_url": "https://www.bet365.es/#/AS/B1/",
                            "match_id": ev.get("id"),
                            "matched_title": f"{ev.get('home_team')} vs {ev.get('away_team')}",
                            "matched": True,
                            "is_manual_override": False,
                            "provenance": "BET365_AUTHORIZED_API",
                            "cached_at": time.time(),
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
                        for m in bm.get("markets", []):
                            if m.get("key") == "h2h":
                                for o in m.get("outcomes", []):
                                    if o.get("name") == ev.get("home_team"):
                                        odds_data["1X2"]["1"] = float(o.get("price"))
                                    elif o.get("name") == "Draw":
                                        odds_data["1X2"]["X"] = float(o.get("price"))
                                    elif o.get("name") == ev.get("away_team"):
                                        odds_data["1X2"]["2"] = float(o.get("price"))
                            elif m.get("key") == "totals":
                                for o in m.get("outcomes", []):
                                    if o.get("point") == 2.5:
                                        if o.get("name") == "Over":
                                            odds_data["over_under_2_5"]["over"] = float(o.get("price"))
                                        elif o.get("name") == "Under":
                                            odds_data["over_under_2_5"]["under"] = float(o.get("price"))
                        if odds_data["1X2"].get("1"):
                            cache_key = f"{comp_id}_{h_norm}_{a_norm}"
                            self._cache[cache_key] = odds_data
                            self._save_cache()
                            return odds_data
        return None

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
