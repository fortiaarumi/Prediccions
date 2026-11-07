"""
scraper/winamax_scraper.py
==========================
Scraper oficial en directe de Winamax Espanya (100% fidel a la web real).

Extracció ultraràpida i matemàticament exacta dels mercats del partit complet:
- 1X2 i Doble Oportunitat
- Número total de gols (Slider principal: Més/Menys de 2.5 gols)
- Ambdós equips marquen (Sí / No)
- Número total de targetes (Més/Menys de 4.5 i 5.5 targetes, Expulsió Sí)
- Número total de córners (Més/Menys de 8.5, 9.5 i 10.5 córners)
"""

import sys
import os
import re
import time
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

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
    Avalua la frescor d'una captura de quotes segons la política oficial:
    - FRESH: Quota vàlida i recent (age <= max_age_hours).
    - STALE: Quota amb timestamp vàlid però que supera el llindar màxim d'antiguitat.
    - UNKNOWN: Quota sense timestamp fiable, mal format o amb timestamp futur incoherent.

    Tant STALE com UNKNOWN tenen is_stale = True i han de ser exclosos de les value bets públiques.
    """
    now_utc = now if (now is not None and now.tzinfo is not None) else (now.replace(tzinfo=timezone.utc) if now else datetime.now(timezone.utc))
    max_h = float(max_age_hours if max_age_hours is not None else float(os.environ.get("WINAMAX_MAX_ODDS_AGE_HOURS", DEFAULT_MAX_ODDS_AGE_HOURS)))
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
        # Desajust de rellotge cap al futur que supera la tolerància
        return {
            "freshness": "UNKNOWN",
            "is_stale": True,
            "captured_at": dt.isoformat(),
            "age_seconds": round(age_s, 2),
            "rejection_reason": "future_clock_skew"
        }

    if age_s < 0:
        # Dins de la tolerància de desajust lleuger de rellotge (<= 5 minuts)
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

WINAMAX_TOURNAMENTS = {
    "LALIGA": "32",
    "PREMIER": "1",
    "HYPERMOTION": "32",
    "CHAMPIONSHIP": "1",
    "BUNDESLIGA": "30",
    "LIGUE1": "7",
    "SERIEA": "31",
}

WINAMAX_TOURNAMENT_IDS = {
    "LALIGA": 36,
    "PREMIER": 1,
    "HYPERMOTION": 37,
    "CHAMPIONSHIP": 2,
    "BUNDESLIGA": 42,
    "LIGUE1": 4,
    "SERIEA": 33,
}

class WinamaxScraper:
    def __init__(
        self,
        headless: bool = True,
        db: Optional[Any] = None,
        cache_dir: Optional[Path] = None,
        max_odds_age_hours: Optional[float] = None,
        offline: bool = False,
        force_fresh: bool = False
    ):
        self.headless = headless
        if db is not None:
            self.db = db
        else:
            try:
                from database.db_manager import DatabaseManager
                self.db = DatabaseManager()
            except Exception:
                self.db = None
        self._tournament_cache: Dict[str, Dict[str, Any]] = {}
        self._match_cache: Dict[str, Dict[str, Any]] = {}
        self.cache_dir = Path(cache_dir) if cache_dir else CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_odds_age_hours = float(
            max_odds_age_hours if max_odds_age_hours is not None
            else float(os.environ.get("WINAMAX_MAX_ODDS_AGE_HOURS", DEFAULT_MAX_ODDS_AGE_HOURS))
        )
        self.offline = offline
        self.force_fresh = force_fresh

    def save_cache(self, tid: str, state: Dict[str, Any], captured_at: Optional[str] = None) -> Path:
        """Desa la memòria cau amb l'embolcall oficial de metadades UTC."""
        cache_file = self.cache_dir / f"winamax_cache_tid_{tid}.json"
        now_utc = captured_at or (match_data.get("captured_at") if match_data else None) or datetime.now(timezone.utc).isoformat()
        metadata = {
            "source": "winamax",
            "tournament_id": str(tid),
            "captured_at": now_utc,
            "format_version": "1.0"
        }
        state["_metadata"] = metadata
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(state, f)
        self._tournament_cache[str(tid)] = state
        return cache_file

    @staticmethod
    def classify_event_status(m: Dict[str, Any], now_epoch: float) -> str:
        """
        Classifica l'estat real d'un esdeveniment a partir de les metadades de Winamax:
        - 'LIVE': El partit està en directe (status 'LIVE', 'IN_PLAY', o té bandera de live activa).
        - 'FINISHED': El partit ha acabat (status 'ENDED', 'FINISHED', 'CLOSED', o començat fa > 3.5h sense senyal de live).
        - 'SUSPENDED': El partit està suspès (status 'SUSPENDED', 'INTERRUPTED').
        - 'POSTPONED': El partit està ajornat o cancel·lat (status 'POSTPONED', 'CANCELLED', 'DELAYED').
        - 'PREMATCH': El partit és futur i pendent de jugar (status 'PREMATCH' o matchStart > now_epoch).
        - 'UNKNOWN': Estat no determinable.
        """
        if not isinstance(m, dict):
            return "UNKNOWN"

        raw_status = str(m.get("status", "")).upper()
        is_live_flag = bool(m.get("live") or m.get("reallyLive"))

        # 1. LIVE explícit
        if raw_status in ("LIVE", "IN_PLAY", "1H", "2H", "HT", "EXTRA_TIME", "PENALTIES") or is_live_flag:
            return "LIVE"

        # 2. SUSPÈS / AJORNAT
        if raw_status in ("SUSPENDED", "INTERRUPTED"):
            return "SUSPENDED"
        if raw_status in ("POSTPONED", "CANCELLED", "DELAYED", "ABANDONED"):
            return "POSTPONED"

        # 3. FINALITZAT explícit
        if raw_status in ("ENDED", "FINISHED", "CLOSED", "RESULTED"):
            return "FINISHED"

        # 4. Avaluar temps d'inici (matchStart)
        m_start = m.get("matchStart")
        if m_start is not None:
            try:
                m_start_val = float(m_start)
                if m_start_val > 1e11:
                    m_start_val /= 1000.0

                # Futur
                if m_start_val > now_epoch:
                    return "PREMATCH"

                # Ha començat en el passat
                elapsed_seconds = now_epoch - m_start_val
                # Si han passat més de 3.5 hores (12600s) i no és LIVE, es considera FINISHED
                if elapsed_seconds > 12600:
                    return "FINISHED"
                else:
                    # Si ha començat fa poc però el status encara posa PREMATCH (<30 min), retard en inici
                    if raw_status == "PREMATCH" and elapsed_seconds < 1800:
                        return "PREMATCH"
                    # En finestra de partit (0 a 3.5h), continua actiu com a LIVE
                    return "LIVE"
            except (ValueError, TypeError):
                pass

        if raw_status == "PREMATCH":
            return "PREMATCH"

        return "UNKNOWN"

    @staticmethod
    def validate_tournament_payload(
        parsed_state: Any,
        existing_state: Optional[Dict[str, Any]] = None,
        min_coverage_ratio: float = 0.50,
        min_valid_matches_ratio: float = 0.80,
        now: Optional[datetime] = None
    ) -> Tuple[bool, str]:
        """
        Valida que una resposta de torneig sigui estructuralment completa i suficient:
        1. Ha de ser un diccionari amb matches, bets i odds no buits.
        2. Les relacions entre matches -> mainBetId -> bets -> outcomes -> odds han de ser vàlides:
           - Cada partit ha de tenir un mainBetId existent a 'bets'.
           - L'aposta ha de tenir 'outcomes' (llista o diccionari amb almenys 2 opcions).
           - Totes les opcions de l'aposta han de tenir una quota vàlida a 'odds'.
           - Totes les quotes han de ser numèriques i estrictament > 1.0.
        3. Proporció de partits vàlids:
           - Almenys min_valid_matches_ratio (80%) dels partits retornats a 'matches' han d'estar
             completament resolts amb totes les seves relacions i quotes vàlides.
        4. Validació de cobertura robusta respecte a la memòria cau preexistent:
           - Si existia una memòria cau anterior consolidada (>= 15 partits), es computa
             la línia base de partits que encara no han finalitzat (matchStart > now).
             Els partits ja finalitzats (matchStart <= now) es descompten de la línia base
             per acceptar reduccions legítimes del calendari en finalitzar partits de la jornada.
           - El nombre de partits vàlids de la nova captura ha de ser >= min_coverage_ratio (50%)
             de la línia base esperada.
        """
        import math

        if not isinstance(parsed_state, dict):
            return False, "not_a_dict"

        matches = parsed_state.get("matches")
        bets = parsed_state.get("bets")
        odds = parsed_state.get("odds")

        if not isinstance(matches, dict) or len(matches) == 0:
            return False, "missing_or_empty_matches"
        if not isinstance(bets, dict) or len(bets) == 0:
            return False, "missing_or_empty_bets"
        if not isinstance(odds, dict) or len(odds) == 0:
            return False, "missing_or_empty_odds"

        now_dt = now if (now is not None and now.tzinfo is not None) else (now.replace(tzinfo=timezone.utc) if now else datetime.now(timezone.utc))
        now_epoch = now_dt.timestamp()

        valid_matches_count = 0
        total_matches = len(matches)

        for mid, m in matches.items():
            if not isinstance(m, dict):
                continue
            mb_id = m.get("mainBetId")
            if mb_id is None:
                continue

            bet = bets.get(str(mb_id))
            if bet is None:
                bet = bets.get(mb_id)
            if not isinstance(bet, dict):
                continue

            outcomes = bet.get("outcomes")
            if not outcomes:
                continue

            outcome_ids = outcomes if isinstance(outcomes, list) else list(outcomes.keys())
            if len(outcome_ids) < 2:
                continue

            match_odds_valid = True
            for oid in outcome_ids:
                odd_val = odds.get(str(oid))
                if odd_val is None:
                    odd_val = odds.get(oid)

                if odd_val is None:
                    match_odds_valid = False
                    break
                try:
                    val_float = float(odd_val)
                    if math.isnan(val_float) or val_float <= 1.0:
                        match_odds_valid = False
                        break
                except (ValueError, TypeError):
                    match_odds_valid = False
                    break

            if match_odds_valid:
                valid_matches_count += 1

        if total_matches == 0:
            return False, "empty_matches"

        valid_ratio = valid_matches_count / total_matches
        if valid_ratio < min_valid_matches_ratio:
            return False, f"insufficient_valid_matches_ratio: {valid_matches_count}/{total_matches} ({valid_ratio:.1%}) < {min_valid_matches_ratio:.1%}"

        # Validació de cobertura respecte a la memòria cau preexistent
        if existing_state and isinstance(existing_state, dict):
            existing_matches = existing_state.get("matches")
            if isinstance(existing_matches, dict) and len(existing_matches) >= 15:
                # Filtrar partits existents que encara estan actius (futurs PREMATCH o en joc LIVE)
                active_existing = 0
                for em in existing_matches.values():
                    if not isinstance(em, dict):
                        continue
                    ev_status = WinamaxScraper.classify_event_status(em, now_epoch)
                    # No excloure partits en directe (LIVE); només descomptar els finalitzats, suspesos o ajornats
                    if ev_status in ("PREMATCH", "LIVE", "UNKNOWN"):
                        active_existing += 1

                baseline = active_existing if active_existing >= 10 else len(existing_matches)
                min_threshold = int(baseline * min_coverage_ratio)
                if valid_matches_count < min_threshold:
                    return False, f"insufficient_coverage: {valid_matches_count} valid matches < threshold {min_threshold} (baseline: {baseline})"

        return True, "ok"

    def update_match_in_cache(
        self,
        tid: str,
        match_id: str,
        match_data: Optional[Dict[str, Any]] = None,
        captured_at: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Actualitza exclusivament un partit individual dins de la memòria cau del torneig,
        assignant-li un timestamp de partit específic sense renovar artificialment el
        timestamp global del torneig ni el de la resta de partits.
        """
        tid_str = str(tid)
        state = self._tournament_cache.get(tid_str)
        if not state:
            cache_file = self.cache_dir / f"winamax_cache_tid_{tid_str}.json"
            if cache_file.exists():
                try:
                    with open(cache_file, "r", encoding="utf-8") as f:
                        state = json.load(f)
                except Exception:
                    state = {}
            else:
                state = {}
        if not state:
            state = {"matches": {}, "_metadata": {"source": "winamax", "tournament_id": tid_str, "captured_at": None}}

        now_utc = captured_at or (match_data.get("captured_at") if match_data else None) or datetime.now(timezone.utc).isoformat()
        matches = state.setdefault("matches", {})
        m = matches.setdefault(str(match_id), {})
        if match_data:
            m.update(match_data)
        m["captured_at"] = now_utc

        self._tournament_cache[tid_str] = state
        cache_file = self.cache_dir / f"winamax_cache_tid_{tid_str}.json"
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(state, f)
        except Exception:
            pass

        return state

    def get_match_odds(self, home_team: str, away_team: str, competition_id: str = "LALIGA", now: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Descobreix dinàmicament l'ID del partit a Winamax i n'extreu exactament les cuotes reals.
        """
        comp_id = competition_id.upper()
        tid = str(WINAMAX_TOURNAMENTS.get(comp_id, "32"))
        base_tournament_url = f"https://www.winamax.es/apuestas-deportivas/sports/1/{tid}"
        print(f"\n[*] Cercant dinàmicament a Winamax ({comp_id} | Categoria {tid}): {home_team} vs {away_team}...")

        odds_data = {
            "source": f"Winamax Espanya ({comp_id})",
            "url": base_tournament_url,
            "match_id": None,
            "matched_title": None,
            "matched": False,
            "tournament_id": tid,
            "captured_at": None,
            "freshness": "UNKNOWN",
            "is_stale": True,
            "freshness_reason": "not_processed",
            "age_seconds": None,
            "provider_updated_at": None,
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

        def normalize(name: Any) -> str:
            if not name or not isinstance(name, str):
                return ""
            s = name.lower()
            for c, rep in [("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ñ", "n"), ("ç", "c"), ("ü", "u")]:
                s = s.replace(c, rep)
            return re.sub(r"[^a-z0-9]", "", s)

        # 0. Comprovar si hi ha quotes manuals definides per l'usuari
        manual_override_file = CACHE_DIR / "manual_odds_override.json"
        if manual_override_file.exists():
            try:
                with open(manual_override_file, "r", encoding="utf-8") as f:
                    overrides = json.load(f)
                    cache_key = f"{comp_id}_{normalize(home_team)}_{normalize(away_team)}"
                    m_entry = overrides.get(cache_key) or overrides.get(f"{normalize(home_team)}_{normalize(away_team)}")
                    if m_entry and isinstance(m_entry, dict):
                        w_manual = m_entry.get("winamax") or m_entry.get("odds_winamax")
                        if w_manual and isinstance(w_manual, dict) and w_manual.get("1"):
                            odds_data["1X2"] = {
                                "1": float(w_manual.get("1")),
                                "X": float(w_manual.get("X")),
                                "2": float(w_manual.get("2"))
                            }
                            odds_data["matched"] = True
                            odds_data["is_manual_override"] = True
                            odds_data["freshness"] = "FRESH"
                            odds_data["is_stale"] = False
                            odds_data["freshness_reason"] = "manual_override"
                            odds_data["captured_at"] = datetime.now(timezone.utc).isoformat()
                            return odds_data
            except Exception:
                pass

        team_keywords = {
            # LaLiga EA Sports
            "madrid": "madrid", "sociedad": "sociedad", "barcelona": "barcelona",
            "athletic": "athletic", "atletico": "atletico", "villarreal": "villarreal",
            "sevilla": "sevilla", "betis": "betis", "valencia": "valencia",
            "celta": "celta", "osasuna": "osasuna", "rayo": "rayo",
            "getafe": "getafe", "alaves": "alaves", "espanyol": "espanyol",
            "racing": "racing", "deportivo": "deportiv", "malaga": "malaga",
            "levante": "levante", "elche": "elche",
            # Premier League
            "arsenal": "arsenal", "manchester city": "city", "man city": "city",
            "liverpool": "liverpool", "aston villa": "villa", "villa": "villa",
            "tottenham": "tottenham", "spurs": "tottenham", "chelsea": "chelsea",
            "newcastle": "newcastle", "manchester united": "united", "man united": "united",
            "west ham": "west", "brighton": "brighton", "bournemouth": "bournemouth",
            "fulham": "fulham", "wolves": "wolverhampton", "wolverhampton": "wolverhampton",
            "crystal palace": "palace", "palace": "palace", "brentford": "brentford",
            "everton": "everton", "nottingham": "forest", "forest": "forest",
            "ipswich": "ipswich", "leicester": "leicester", "southampton": "southampton",
            "leeds": "leeds", "sunderland": "sunderland",
            # LaLiga Hypermotion
            "zaragoza": "zaragoza", "oviedo": "oviedo", "sporting": "sporting",
            "gijon": "sporting", "eibar": "eibar", "castellon": "castellon", "burgos": "burgos",
            "albacete": "albacete", "huesca": "huesca", "granada": "granada",
            "almeria": "almeria", "cadiz": "cadiz", "cordoba": "cordoba",
            "mirandes": "mirandes", "eldense": "eldense", "ferrol": "ferrol",
            "tenerife": "tenerife", "cartagena": "cartagena", "girona": "girona",
            "sabadell": "sabadell", "ceuta": "ceuta", "palmas": "palmas",
            "mallorca": "mallorca", "fortuna": "celta", "valladolid": "valladolid",
            "leganes": "leganes", "andorra": "andorra",
            # EFL Championship
            "birmingham": "birmingham", "blackburn": "blackburn", "bolton": "bolton",
            "bristol": "bristol", "burnley": "burnley", "cardiff": "cardiff",
            "charlton": "charlton", "derby": "derby", "lincoln": "lincoln",
            "middlesbrough": "middlesbrough", "millwall": "millwall", "norwich": "norwich",
            "portsmouth": "portsmouth", "preston": "preston", "qpr": "qpr", "queens": "qpr",
            "sheffield": "sheffield", "sheff": "sheffield", "stoke": "stoke",
            "swansea": "swansea", "watford": "watford", "west brom": "bromwich",
            "wrexham": "wrexham", "coventry": "coventry", "hull": "hull",
            # Bundesliga
            "bayern": "bayern", "munich": "bayern", "munchen": "bayern",
            "leverkusen": "leverkusen", "bayer": "leverkusen",
            "dortmund": "dortmund", "leipzig": "leipzig",
            "frankfurt": "frankfurt", "francfort": "frankfurt",
            "stuttgart": "stuttgart", "friburgo": "friburgo", "freiburg": "friburgo",
            "gladbach": "gladbach", "monchengladbach": "gladbach",
            "bremen": "bremen", "augsburgo": "augsburg", "augsburg": "augsburg",
            "hoffenheim": "hoffenheim", "mainz": "mainz",
            "union berlin": "union", "colonia": "colonia", "koln": "colonia",
            "hamburgo": "hamburgo", "hamburg": "hamburgo",
            "schalke": "schalke", "paderborn": "paderborn", "elversberg": "elversberg",
            # Ligue 1
            "paris": "paris", "psg": "paris",
            "monaco": "monaco", "lille": "lille",
            "marseille": "marsella", "marsella": "marsella",
            "lyon": "lyon", "lens": "lens",
            "niza": "niza", "nice": "niza",
            "rennes": "rennes", "brest": "brest",
            "toulouse": "toulouse", "strasbourg": "estrasburgo", "estrasburgo": "estrasburgo",
            "le havre": "havre", "havre": "havre",
            "auxerre": "auxerre", "angers": "angers",
            "lorient": "lorient", "troyes": "troyes",
            "le mans": "mans",
            # Serie A
            "inter": "inter", "internazionale": "inter",
            "milan": "milan", "juventus": "juventus", "juve": "juventus",
            "atalanta": "atalanta", "napoli": "napoles", "napoles": "napoles",
            "roma": "roma", "lazio": "lazio",
            "fiorentina": "fiorentina", "bologna": "bolonia", "bolonia": "bolonia",
            "torino": "torino", "monza": "monza",
            "genoa": "genoa", "genova": "genoa", "udinese": "udinese",
            "cagliari": "cagliari", "lecce": "lecce",
            "parma": "parma", "como": "como",
            "venezia": "venecia", "venecia": "venecia",
            "sassuolo": "sassuolo", "frosinone": "frosinone"
        }

        sorted_keywords = sorted(team_keywords.items(), key=lambda x: len(x[0]), reverse=True)
        GENERIC_ROOTS = {"city", "united", "athletic", "town", "villa", "palace", "west", "sporting", "celta", "racing", "forest"}

        def get_keyword(name_str: str) -> str:
            n = normalize(name_str)
            for k, root in sorted_keywords:
                if k in n:
                    return root
                if root not in GENERIC_ROOTS and root in n:
                    return root
            return n[:5]

        h_id = self.db.find_team_id(home_team) if self.db else None
        a_id = self.db.find_team_id(away_team) if self.db else None
        h_key = get_keyword(home_team)
        a_key = get_keyword(away_team)

        # 1. Carregar l'índex del torneig de Winamax mitjançant HTTP directe (amb memòria cau per no saturar)
        cache_file = self.cache_dir / f"winamax_cache_tid_{tid}.json"
        try:
            if tid in self._tournament_cache:
                state = self._tournament_cache[tid]
            else:
                state = {}
                fetch_success = False

                if not self.offline:
                    cmd = ['curl.exe' if os.name == 'nt' else 'curl', '-s', '--connect-timeout', '5', '--max-time', '15', '-A', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36', base_tournament_url]
                    try:
                        res = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=20)
                    except subprocess.TimeoutExpired:
                        print(f"   [!] Timeout (20s) consultant Winamax per a {tournament_id} ({base_tournament_url})")
                        res = None
                    except Exception as e:
                        print(f"   [!] Error consultant Winamax per a {tournament_id}: {e}")
                        res = None

                    start = res.stdout.find('var PRELOADED_STATE = ') if (res and res.stdout) else -1
                    if start == -1 and res and res.stdout:
                        start = res.stdout.find('PRELOADED_STATE = ')

                    if start != -1:
                        state_str = res.stdout[start + len('var PRELOADED_STATE = '):] if 'var PRELOADED_STATE = ' in res.stdout[start:start+30] else res.stdout[start + len('PRELOADED_STATE = '):]
                        try:
                            parsed_state, _ = json.JSONDecoder().raw_decode(state_str)
                            existing_for_val = None
                            if cache_file.exists():
                                try:
                                    with open(cache_file, "r", encoding="utf-8") as exf:
                                        existing_for_val = json.load(exf)
                                except Exception:
                                    pass

                            is_valid, val_reason = self.validate_tournament_payload(parsed_state, existing_state=existing_for_val, now=now)
                            if is_valid:
                                state = parsed_state
                                fetch_success = True
                            else:
                                print(f"[!] WinamaxScraper: Resposta de torneig rebutjada per cobertura ({val_reason}).")
                        except Exception:
                            pass

                if fetch_success:
                    now_utc = datetime.now(timezone.utc).isoformat()
                    metadata = {
                        "source": "winamax",
                        "tournament_id": str(tid),
                        "captured_at": now_utc,
                        "format_version": "1.0"
                    }
                    state["_metadata"] = metadata
                    try:
                        with open(cache_file, "w", encoding="utf-8") as cf:
                            json.dump(state, cf)
                    except Exception:
                        pass
                else:
                    # Fallback al fitxer de cau si no s'ha obtingut resposta (ex: bloqueig cloud / GitHub Actions / offline)
                    # IMPORTANTÍSSIM: No sobreescriure mai el fitxer ni actualitzar el timestamp en cas de fallada!
                    if cache_file.exists():
                        try:
                            with open(cache_file, "r", encoding="utf-8") as cf:
                                state = json.load(cf)
                        except Exception:
                            pass

                self._tournament_cache[tid] = state

            # Resolució de metadades de frescor oficials
            meta = state.get("_metadata") if isinstance(state, dict) else None
            if not meta and isinstance(state, dict) and "metadata" in state:
                meta = state.get("metadata")
            captured_at_val = meta.get("captured_at") if isinstance(meta, dict) else None

            if self.force_fresh:
                freshness_info = {
                    "freshness": "FRESH",
                    "is_stale": False,
                    "captured_at": captured_at_val or datetime.now(timezone.utc).isoformat(),
                    "age_seconds": 0.0,
                    "rejection_reason": "ok"
                }
            else:
                freshness_info = evaluate_freshness(captured_at_val, now=now, max_age_hours=self.max_odds_age_hours)

            odds_data["captured_at"] = freshness_info.get("captured_at")
            odds_data["freshness"] = freshness_info.get("freshness")
            odds_data["is_stale"] = freshness_info.get("is_stale")
            odds_data["freshness_reason"] = freshness_info.get("rejection_reason")
            odds_data["age_seconds"] = freshness_info.get("age_seconds")
            odds_data["tournament_id"] = str(tid)

            matches = state.get("matches") or {}

            target_match = None
            target_mid = None
            target_tourn_id = WINAMAX_TOURNAMENT_IDS.get(comp_id)

            for mid, m in matches.items():
                if not isinstance(m, dict):
                    continue
                if target_tourn_id and m.get("tournamentId") != target_tourn_id:
                    continue
                c1 = m.get("competitor1Name")
                c2 = m.get("competitor2Name")
                if not c1 or not c2:
                    continue

                is_matched = False
                c1_id = self.db.find_team_id(c1) if self.db else None
                c2_id = self.db.find_team_id(c2) if self.db else None

                if h_id and a_id and c1_id and c2_id:
                    if h_id == c1_id and a_id == c2_id:
                        is_matched = True

                if not is_matched:
                    c1_key = get_keyword(c1)
                    c2_key = get_keyword(c2)
                    if (h_key == c1_key or (len(h_key) >= 4 and h_key in normalize(c1)) or (len(c1_key) >= 4 and c1_key in normalize(home_team))) and \
                       (a_key == c2_key or (len(a_key) >= 4 and a_key in normalize(c2)) or (len(c2_key) >= 4 and c2_key in normalize(away_team))):
                        is_matched = True

                if is_matched:
                    target_match = m
                    target_mid = str(mid)
                    break

            if target_match and target_mid:
                match_url = f"https://www.winamax.es/apuestas-deportivas/match/{target_mid}"
                odds_data["matched"] = True
                odds_data["match_id"] = target_mid
                odds_data["matched_title"] = target_match.get("title")
                odds_data["url"] = match_url

                print(f"[+] Partit descobert autònomament: '{target_match.get('title')}' (ID Winamax: {target_mid})")
                print(f"[*] Accedint a tots els mercats: {match_url}")

                # Granularitat de partit: si el partit individual té un captured_at propi específic,
                # té prioritat sobre el timestamp global del torneig
                m_cap = (
                    target_match.get("captured_at")
                    or (target_match.get("_metadata") or {}).get("captured_at")
                )
                if m_cap and not self.force_fresh:
                    freshness_info = evaluate_freshness(m_cap, now=now, max_age_hours=self.max_odds_age_hours)
                    odds_data["captured_at"] = freshness_info.get("captured_at")
                    odds_data["freshness"] = freshness_info.get("freshness")
                    odds_data["is_stale"] = freshness_info.get("is_stale")
                    odds_data["freshness_reason"] = freshness_info.get("rejection_reason")
                    odds_data["age_seconds"] = freshness_info.get("age_seconds")

                if target_mid in self._match_cache:
                    match_state = self._match_cache[target_mid]
                else:
                    match_state = None
                    # Comprovar primer memòria cau local de partit o fixture aïllada
                    match_cache_file = self.cache_dir / f"winamax_match_{target_mid}.json"
                    fixture_file = Path(__file__).parent.parent / "tests" / "fixtures" / f"winamax_match_{target_mid}.json"
                    if match_cache_file.exists():
                        try:
                            with open(match_cache_file, "r", encoding="utf-8") as mf:
                                match_state = json.load(mf)
                        except Exception:
                            pass
                    elif fixture_file.exists():
                        try:
                            with open(fixture_file, "r", encoding="utf-8") as mf:
                                match_state = json.load(mf)
                        except Exception:
                            pass

                    if not match_state and not self.offline:
                        cmd_m = ['curl.exe' if os.name == 'nt' else 'curl', '-s', '--connect-timeout', '5', '--max-time', '15', '-A', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36', match_url]
                        try:
                            res_m = subprocess.run(cmd_m, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=20)
                        except subprocess.TimeoutExpired:
                            print(f"   [!] Timeout (20s) consultant partit Winamax {match_id} ({match_url})")
                            res_m = None
                        except Exception as e:
                            print(f"   [!] Error consultant partit Winamax {match_id}: {e}")
                            res_m = None

                        start_m = res_m.stdout.find('var PRELOADED_STATE = ') if (res_m and res_m.stdout) else -1
                        if start_m != -1:
                            state_m_str = res_m.stdout[start_m + len('var PRELOADED_STATE = '):]
                            try:
                                match_state, _ = json.JSONDecoder().raw_decode(state_m_str)
                                if match_state:
                                    try:
                                        with open(match_cache_file, "w", encoding="utf-8") as mf:
                                            json.dump(match_state, mf)
                                    except Exception:
                                        pass
                            except Exception:
                                pass

                    if not match_state:
                        match_state = state
                    self._match_cache[target_mid] = match_state

                bets = match_state.get("bets") or {}
                odds = match_state.get("odds") or {}
                outcomes = match_state.get("outcomes") or {}

                # 1X2 del PARTIT COMPLET
                main_bet_id = target_match.get("mainBetId")
                if main_bet_id:
                    b_info = bets.get(str(main_bet_id)) or {}
                    out_ids = b_info.get("outcomes") or []
                    for oid in out_ids:
                        o_obj = outcomes.get(str(oid)) or {}
                        code = str(o_obj.get("code") or "").lower()
                        val = odds.get(str(oid))
                        if val is not None:
                            if code == "1":
                                odds_data["1X2"]["1"] = float(val)
                            elif code == "x":
                                odds_data["1X2"]["X"] = float(val)
                            elif code == "2":
                                odds_data["1X2"]["2"] = float(val)

                # Mercats addicionals
                for bid, b_info in bets.items():
                    if not isinstance(b_info, dict):
                        continue
                    m_id_val = str(b_info.get("matchId") or "")
                    if m_id_val == target_mid or m_id_val == str(target_mid):
                        b_title = str(b_info.get("betTitle") or b_info.get("betTypeName") or "Mercat").strip()
                        b_cat = str(b_info.get("betTypeCategory") or "").strip()
                        b_title_lower = b_title.lower()
                        b_cat_lower = b_cat.lower()
                        out_ids = b_info.get("outcomes") or []

                        if any(x in b_title_lower for x in ["1ª mitad", "1a mitad", "2ª mitad", "2a mitad", "descanso", "mitad con más goles"]):
                            continue

                        # Doble Oportunitat
                        if b_title_lower == "doble oportunidad":
                            for oid in out_ids:
                                o_obj = outcomes.get(str(oid)) or {}
                                lbl = str(o_obj.get("label") or o_obj.get("code") or "").strip()
                                val = odds.get(str(oid))
                                if val is not None:
                                    lbl_lower = lbl.lower()
                                    if "o empate" in lbl_lower:
                                        if normalize(target_match.get("competitor1Name", "")) in normalize(lbl):
                                            odds_data["double_chance"]["1X"] = float(val)
                                        else:
                                            odds_data["double_chance"]["X2"] = float(val)
                                    elif "o" in lbl_lower and "empate" not in lbl_lower:
                                        odds_data["double_chance"]["12"] = float(val)

                        # Total de Gols (Over / Under 1.5, 2.5, 3.5)
                        elif b_cat_lower == "total de goles" and b_title_lower == "número total de goles":
                            for oid in out_ids:
                                o_obj = outcomes.get(str(oid)) or {}
                                lbl = str(o_obj.get("label") or o_obj.get("code") or "").strip()
                                val = odds.get(str(oid))
                                if val is not None:
                                    lbl_lower = lbl.lower()
                                    if "más de 1,5" in lbl_lower or "más de 1.5" in lbl_lower:
                                        odds_data["goals"]["Over 1.5"] = float(val)
                                    elif "menos de 1,5" in lbl_lower or "menos de 1.5" in lbl_lower:
                                        odds_data["goals"]["Under 1.5"] = float(val)
                                    elif "más de 2,5" in lbl_lower or "más de 2.5" in lbl_lower:
                                        odds_data["goals"]["Over 2.5"] = float(val)
                                        odds_data["over_under_2_5"]["Over 2.5"] = float(val)
                                        odds_data["over_under_2_5"]["over"] = float(val)
                                    elif "menos de 2,5" in lbl_lower or "menos de 2.5" in lbl_lower:
                                        odds_data["goals"]["Under 2.5"] = float(val)
                                        odds_data["over_under_2_5"]["Under 2.5"] = float(val)
                                        odds_data["over_under_2_5"]["under"] = float(val)
                                    elif "más de 3,5" in lbl_lower or "más de 3.5" in lbl_lower:
                                        odds_data["goals"]["Over 3.5"] = float(val)
                                    elif "menos de 3,5" in lbl_lower or "menos de 3.5" in lbl_lower:
                                        odds_data["goals"]["Under 3.5"] = float(val)

                        # Gols per equip (si Winamax ho ofereix de forma real)
                        elif b_cat_lower == "goles por equipo" and "número total de goles marcados por" in b_title_lower:
                            c1_norm = normalize(target_match.get("competitor1Name", ""))
                            c2_norm = normalize(target_match.get("competitor2Name", ""))
                            title_norm = normalize(b_title)
                            team_key = "home" if c1_norm and c1_norm in title_norm else ("away" if c2_norm and c2_norm in title_norm else None)
                            if team_key:
                                if team_key not in odds_data["team_goals"]:
                                    odds_data["team_goals"][team_key] = {}
                                for oid in out_ids:
                                    o_obj = outcomes.get(str(oid)) or {}
                                    lbl = str(o_obj.get("label") or o_obj.get("code") or "").strip()
                                    val = odds.get(str(oid))
                                    if val is not None:
                                        lbl_lower = lbl.lower()
                                        if "más de 0,5" in lbl_lower or "más de 0.5" in lbl_lower:
                                            odds_data["team_goals"][team_key]["Over 0.5"] = float(val)
                                        elif "más de 1,5" in lbl_lower or "más de 1.5" in lbl_lower:
                                            odds_data["team_goals"][team_key]["Over 1.5"] = float(val)

                        # Ambdós marquen
                        elif b_title_lower in ["ambos equipos marcan", "¿ambos equipos marcarán?", "ambos equipos marcarán"]:
                            for oid in out_ids:
                                o_obj = outcomes.get(str(oid)) or {}
                                lbl = str(o_obj.get("label") or o_obj.get("code") or "").strip()
                                val = odds.get(str(oid))
                                if val is not None:
                                    if lbl.lower() in ["sí", "si", "yes"]:
                                        odds_data["btts"]["Sí"] = float(val)
                                        odds_data["btts"]["yes"] = float(val)
                                    elif lbl.lower() in ["no"]:
                                        odds_data["btts"]["No"] = float(val)
                                        odds_data["btts"]["no"] = float(val)

                        # Targetes
                        elif "tarjeta" in b_cat_lower and b_title_lower == "número total de tarjetas":
                            for oid in out_ids:
                                o_obj = outcomes.get(str(oid)) or {}
                                lbl = str(o_obj.get("label") or o_obj.get("code") or "").strip()
                                val = odds.get(str(oid))
                                if val is not None:
                                    lbl_lower = lbl.lower()
                                    if "más de 4,5" in lbl_lower or "más de 4.5" in lbl_lower:
                                        odds_data["cards"]["Over 4.5 Targetes"] = float(val)
                                    elif "menos de 4,5" in lbl_lower or "menos de 4.5" in lbl_lower:
                                        odds_data["cards"]["Under 4.5 Targetes"] = float(val)
                                    elif "más de 5,5" in lbl_lower or "más de 5.5" in lbl_lower:
                                        odds_data["cards"]["Over 5.5 Targetes"] = float(val)

                        # Targeta Vermella
                        elif "tarjeta" in b_cat_lower and b_title_lower == "expulsión":
                            for oid in out_ids:
                                o_obj = outcomes.get(str(oid)) or {}
                                lbl = str(o_obj.get("label") or o_obj.get("code") or "").strip()
                                val = odds.get(str(oid))
                                if val is not None and lbl.lower() in ["sí", "si"]:
                                    odds_data["cards"]["Targeta Vermella (Sí)"] = float(val)

                        # Córners (Extracció dinàmica de totes les línies Over/Under del partit complet)
                        elif ("córner" in b_cat_lower or "corner" in b_cat_lower):
                            # Evitar meitats, descans o córners individuals d'un sol equip
                            if any(sw in b_title_lower for sw in ["mitad", "descanso", "tiempo", "prórroga"]):
                                continue

                            clean_title = re.sub(r'\s+', ' ', b_title_lower).strip()
                            valid_match_titles = [
                                "número total córneres",
                                "número total de córneres",
                                "total córneres",
                                "total de córneres"
                            ]
                            if clean_title not in valid_match_titles:
                                continue

                            title_norm = normalize(b_title)
                            if (h_key and h_key in title_norm) or (a_key and a_key in title_norm):
                                continue  # Ignorar córners per equip

                            for oid in out_ids:
                                o_obj = outcomes.get(str(oid)) or {}
                                lbl = str(o_obj.get("label") or o_obj.get("code") or "").strip()
                                val = odds.get(str(oid))
                                if val is not None:
                                    lbl_clean = lbl.lower().replace(",", ".")
                                    m_c = re.search(r'(m[aá]s|menos)\s+de\s+(\d+(?:\.\d+)?)', lbl_clean)
                                    if m_c:
                                        dir_str = m_c.group(1).lower()
                                        prefix = "Under" if "menos" in dir_str else "Over"
                                        c_line = m_c.group(2)
                                        try:
                                            c_line_flt = float(c_line)
                                            if 5.5 <= c_line_flt <= 15.5:
                                                odds_data["corners"][f"{prefix} {c_line} Córners"] = float(val)
                                        except ValueError:
                                            pass

            else:
                print(f"[!] No s'ha trobat cap partit actiu a Winamax per a '{home_team}' vs '{away_team}'.")

        except Exception as e:
            print(f"[!] Error durant l'extracció de Winamax: {e}")

        return odds_data
