"""
scraper/betway_scraper.py
=========================
Scraper oficial de quotes de Betway Espanya (betway.es).
Extracció i càlcul de mercats reals prepartit:
- 1X2 (Guanyador del partit estàndard, diferenciat de promocions 1up/2up)
- Doble Oportunitat (1X, 12, X2)
- Més/Menys de 2.5 gols (i línies addicionals de gols)
- Ambdós equips marquen (BTTS: Sí / No)
- Total de targetes i córners (quan disponibles)

Arquitectura:
- Obté el feed de la competició directament de betway.es mitjançant RSC (React Server Components).
- Resol detalls i mercats secundaris mitjançant l'API oficial de serveis de Betway:
  https://betway.es/es/services/api/events/v2/GetEventDetails
  https://betway.es/es/services/api/events/v2/GetEventMarkets
- Normalitza els noms dels equips usant la base de dades local i diccionari d'àlies canònics.
- Gestiona memòria cau independent (data/betway_odds_cache.json), aïllada de Winamax i Bet365.
- Control estricte de frescor amb timestamps UTC independents.
"""

import os
import re
import json
import copy
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

CACHE_DIR = Path(__file__).parent.parent / "data"
DEFAULT_CACHE_FILE = CACHE_DIR / "betway_odds_cache.json"

DEFAULT_MAX_ODDS_AGE_HOURS: float = 2.0
DEFAULT_MAX_FUTURE_SKEW_SECONDS: float = 300.0  # 5 minuts de tolerància per desajust de rellotge

# Configuració de context oficial per a l'API de serveis de Betway Espanya (DGOJ)
BETWAY_API_CONFIG = {
    "ScoreboardRequest": {"IncidentRequest": {}, "ScoreboardType": 3},
    "LanguageId": 11,       # Espanyol (es)
    "ClientTypeId": 2,      # Web / Desktop
    "BrandId": 3,           # Betway
    "JurisdictionId": 5,    # Espanya (DGOJ)
    "ClientIntegratorId": 1
}

BETWAY_SERVICES_BASE_URL = "https://betway.es/es/services/api"

BETWAY_COMPETITIONS: Dict[str, Dict[str, str]] = {
    "LALIGA": {
        "url": "https://betway.es/es/es/sports/grp/soccer/spain/la-liga",
        "group_cname": "la-liga",
        "category_cname": "soccer",
        "subcategory_cname": "spain",
        "name": "España - LaLiga"
    },
    "HYPERMOTION": {
        "url": "https://betway.es/es/es/sports/grp/soccer/spain/la-liga-2",
        "group_cname": "la-liga-2",
        "category_cname": "soccer",
        "subcategory_cname": "spain",
        "name": "España - LaLiga Hypermotion"
    },
    "PREMIER": {
        "url": "https://betway.es/es/es/sports/grp/soccer/england/premier-league",
        "group_cname": "premier-league",
        "category_cname": "soccer",
        "subcategory_cname": "england",
        "name": "Inglaterra - Premier League"
    },
    "CHAMPIONSHIP": {
        "url": "https://betway.es/es/es/sports/grp/soccer/england/championship",
        "group_cname": "championship",
        "category_cname": "soccer",
        "subcategory_cname": "england",
        "name": "Inglaterra - Championship"
    },
    "BUNDESLIGA": {
        "url": "https://betway.es/es/es/sports/grp/soccer/germany/bundesliga",
        "group_cname": "bundesliga",
        "category_cname": "soccer",
        "subcategory_cname": "germany",
        "name": "Alemania - Bundesliga"
    },
    "LIGUE1": {
        "url": "https://betway.es/es/es/sports/grp/soccer/france/ligue-1",
        "group_cname": "ligue-1",
        "category_cname": "soccer",
        "subcategory_cname": "france",
        "name": "Francia - Ligue 1"
    },
    "SERIEA": {
        "url": "https://betway.es/es/es/sports/grp/soccer/italy/serie-a",
        "group_cname": "serie-a",
        "category_cname": "soccer",
        "subcategory_cname": "italy",
        "name": "Italia - Serie A"
    }
}

# Diccionari d'àlies canònics per a Betway
BETWAY_TEAM_ALIASES: Dict[str, str] = {
    # LaLiga EA Sports & Hypermotion
    "athletic de bilbao": "Athletic Club",
    "athletic club bilbao": "Athletic Club",
    "atletico de madrid": "Atlético de Madrid",
    "atletico madrid": "Atlético de Madrid",
    "at madrid": "Atlético de Madrid",
    "atletico": "Atlético de Madrid",
    "celta de vigo": "Celta de Vigo",
    "celta vigo": "Celta de Vigo",
    "deportivo de la coruna": "Deportivo de La Coruña",
    "deportivo la coruna": "Deportivo de La Coruña",
    "racing santander": "Racing de Santander",
    "racing de santander": "Racing de Santander",
    "real sociedad de futbol": "Real Sociedad",
    "rayo vallecano de madrid": "Rayo Vallecano",
    "osasuna": "Osasuna",
    "alaves": "Alavés",
    "leganes": "Leganés",
    "malaga": "Málaga",
    "sporting gijon": "Sporting de Gijón",
    "real oviedo": "Real Oviedo",
    "castellon": "Castellón",
    "cadiz": "Cádiz",
    "almeria": "Almería",
    "granada": "Granada",
    "cordoba": "Córdoba",
    "ferrol": "Racing Ferrol",
    "racing ferrol": "Racing Ferrol",
    "racing de ferrol": "Racing Ferrol",
    "cartagena": "Cartagena",
    "mirandes": "Mirandés",
    "burgos": "Burgos",
    "huesca": "Huesca",
    "zaragoza": "Real Zaragoza",
    "real zaragoza": "Real Zaragoza",
    "tenerife": "Tenerife",
    "levante": "Levante",
    "elche": "Elche",
    "valencia": "Valencia",
    "sevilla": "Sevilla",
    "real betis": "Real Betis",
    "betis": "Real Betis",
    "villarreal": "Villarreal",
    "getafe": "Getafe",
    "mallorca": "Mallorca",
    "espanyol": "Espanyol",
    "girona": "Girona",
    "las palmas": "Las Palmas",
    "valladolid": "Real Valladolid",
    "real valladolid": "Real Valladolid",
    "barcelona": "Barcelona",
    "real madrid": "Real Madrid",
    
    # Premier League & Championship
    "manchester city": "Manchester City",
    "man city": "Manchester City",
    "manchester united": "Manchester United",
    "man united": "Manchester United",
    "tottenham hotspur": "Tottenham",
    "tottenham": "Tottenham",
    "newcastle united": "Newcastle",
    "newcastle": "Newcastle",
    "west ham united": "West Ham",
    "west ham": "West Ham",
    "wolverhampton wanderers": "Wolves",
    "wolves": "Wolves",
    "brighton & hove albion": "Brighton",
    "brighton": "Brighton",
    "nottingham forest": "Nottingham Forest",
    "sheffield united": "Sheffield United",
    "sheffield wednesday": "Sheffield Wednesday",
    "leicester city": "Leicester",
    "leicester": "Leicester",
    "ipswich town": "Ipswich",
    "ipswich": "Ipswich",
    "luton town": "Luton",
    "luton": "Luton",
    "southampton": "Southampton",
    "norwich city": "Norwich",
    "west bromwich albion": "West Brom",
    "west brom": "West Brom",
    "queens park rangers": "QPR",
    "qpr": "QPR",
    "preston north end": "Preston",
    "blackburn rovers": "Blackburn",
    "stoke city": "Stoke",
    "coventry city": "Coventry",
    "bristol city": "Bristol City",
    "hull city": "Hull",
    "plymouth argyle": "Plymouth",
    "cardiff city": "Cardiff",
    "swansea city": "Swansea",
    "watford": "Watford",
    "middlesbrough": "Middlesbrough",
    "millwall": "Millwall",
    "sunderland": "Sunderland",
    "leeds united": "Leeds",
    "leeds": "Leeds",
    "burnley": "Burnley",
    "portsmouth": "Portsmouth",
    "derby county": "Derby",
    "oxford united": "Oxford United",
    
    # Bundesliga
    "bayern munich": "Bayern Munich",
    "bayern munchen": "Bayern Munich",
    "bayer leverkusen": "Bayer Leverkusen",
    "borussia dortmund": "Borussia Dortmund",
    "rb leipzig": "RB Leipzig",
    "eintracht frankfurt": "Eintracht Frankfurt",
    "vfb stuttgart": "VfB Stuttgart",
    "sc freiburg": "SC Freiburg",
    "friburgo": "SC Freiburg",
    "borussia monchengladbach": "Borussia Mönchengladbach",
    "monchengladbach": "Borussia Mönchengladbach",
    "werder bremen": "Werder Bremen",
    "augsburg": "FC Augsburg",
    "augsburgo": "FC Augsburg",
    "tsg hoffenheim": "Hoffenheim",
    "hoffenheim": "Hoffenheim",
    "mainz 05": "Mainz 05",
    "mainz": "Mainz 05",
    "union berlin": "Union Berlin",
    "fc koln": "1. FC Köln",
    "colonia": "1. FC Köln",
    "vfl wolfsburg": "VfL Wolfsburg",
    "wolfsburg": "VfL Wolfsburg",
    "vfl bochum": "VfL Bochum",
    "bochum": "VfL Bochum",
    "fc st. pauli": "FC St. Pauli",
    "st. pauli": "FC St. Pauli",
    "holstein kiel": "Holstein Kiel",
    "heidenheim": "1. FC Heidenheim",
    
    # Ligue 1
    "paris saint-germain": "Paris Saint-Germain",
    "psg": "Paris Saint-Germain",
    "as monaco": "Monaco",
    "monaco": "Monaco",
    "olympique marseille": "Marseille",
    "marseille": "Marseille",
    "marsella": "Marseille",
    "olympique lyonnais": "Lyon",
    "lyon": "Lyon",
    "lille osc": "Lille",
    "lille": "Lille",
    "rc lens": "Lens",
    "lens": "Lens",
    "ogc nice": "Nice",
    "nice": "Nice",
    "niza": "Nice",
    "stade rennais": "Rennes",
    "rennes": "Rennes",
    "stade brestois": "Brest",
    "brest": "Brest",
    "toulouse fc": "Toulouse",
    "toulouse": "Toulouse",
    "rc strasbourg": "Strasbourg",
    "strasbourg": "Strasbourg",
    "estrasburgo": "Strasbourg",
    "le havre": "Le Havre",
    "havre": "Le Havre",
    "aj auxerre": "Auxerre",
    "auxerre": "Auxerre",
    "angers sco": "Angers",
    "angers": "Angers",
    "as saint-etienne": "Saint-Étienne",
    "saint-etienne": "Saint-Étienne",
    "fc nantes": "Nantes",
    "nantes": "Nantes",
    "stade de reims": "Reims",
    "reims": "Reims",
    "montpellier hsc": "Montpellier",
    "montpellier": "Montpellier",
    
    # Serie A
    "inter milan": "Inter",
    "internazionale": "Inter",
    "inter": "Inter",
    "ac milan": "AC Milan",
    "milan": "AC Milan",
    "juventus": "Juventus",
    "juve": "Juventus",
    "atalanta": "Atalanta",
    "ssc napoli": "Napoli",
    "napoli": "Napoli",
    "napoles": "Napoli",
    "as roma": "AS Roma",
    "roma": "AS Roma",
    "ss lazio": "Lazio",
    "lazio": "Lazio",
    "acf fiorentina": "Fiorentina",
    "fiorentina": "Fiorentina",
    "bologna fc": "Bologna",
    "bologna": "Bologna",
    "bolonia": "Bologna",
    "torino fc": "Torino",
    "torino": "Torino",
    "ac monza": "Monza",
    "monza": "Monza",
    "genoa cfc": "Genoa",
    "genoa": "Genoa",
    "genova": "Genoa",
    "udinese calcio": "Udinese",
    "udinese": "Udinese",
    "cagliari calcio": "Cagliari",
    "cagliari": "Cagliari",
    "us lecce": "Lecce",
    "lecce": "Lecce",
    "parma calcio": "Parma",
    "parma": "Parma",
    "como 1907": "Como",
    "como": "Como",
    "venezia fc": "Venezia",
    "venezia": "Venezia",
    "venecia": "Venezia",
    "hellas verona": "Verona",
    "verona": "Verona",
    "empoli fc": "Empoli",
    "empoli": "Empoli"
}


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
            # Si ve en mil·lisegons (> 1e11)
            val = float(dt_val)
            if val > 1e11:
                val /= 1000.0
            return datetime.fromtimestamp(val, tz=timezone.utc)
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
        for fmt in ("%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y %H:%M"):
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
    Avalua la frescor d'una quota Betway segons la política oficial:
    - FRESH: Quota vàlida i recent (age <= max_age_hours).
    - STALE: Quota amb timestamp vàlid però que supera el llindar màxim d'antiguitat.
    - UNKNOWN: Quota sense timestamp fiable o amb desajust futur incoherent.
    """
    now_utc = now if (now is not None and now.tzinfo is not None) else (now.replace(tzinfo=timezone.utc) if now else datetime.now(timezone.utc))
    max_h = float(max_age_hours if max_age_hours is not None else float(os.environ.get("BETWAY_MAX_ODDS_AGE_HOURS", DEFAULT_MAX_ODDS_AGE_HOURS)))
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


class BetwayScraper:
    """
    Scraper oficial per a Betway Espanya.
    Ingesta quotes 100% reals, normalitza equips i mercats i manté una memòria cau dedicada.
    """

    def __init__(
        self,
        cache_file: Optional[Path] = None,
        cache_path: Optional[Path] = None,
        cache_dir: Optional[Path] = None,
        max_odds_age_hours: Optional[float] = None,
        offline: Optional[bool] = None,
        force_fresh: bool = False,
        db: Optional[Any] = None
    ):
        chosen_file = cache_path or cache_file
        if chosen_file:
            self._cache_file = Path(chosen_file)
        elif cache_dir:
            self._cache_file = Path(cache_dir) / "betway_odds_cache.json"
        else:
            self._cache_file = DEFAULT_CACHE_FILE
        self._cache_file.parent.mkdir(parents=True, exist_ok=True)

        self.max_odds_age_hours = float(
            max_odds_age_hours if max_odds_age_hours is not None
            else float(os.environ.get("BETWAY_MAX_ODDS_AGE_HOURS", DEFAULT_MAX_ODDS_AGE_HOURS))
        )
        if offline is not None:
            self.offline = bool(offline)
        else:
            self.offline = bool(os.environ.get("PREDDICIONS_TEST_OFFLINE"))
        self.force_fresh = force_fresh

        if db is not None:
            self.db = db
        else:
            try:
                from database.db_manager import DatabaseManager
                self.db = DatabaseManager()
            except Exception:
                self.db = None

        self._cache: Dict[str, Any] = {}
        self._load_cache()

    @property
    def cache_path(self) -> Path:
        """Ruta al fitxer de memòria cau dedicat."""
        return self._cache_file

    def get_cached_odds(self) -> Dict[str, Any]:
        """Retorna una còpia de tota la memòria cau carregada."""
        return copy.deepcopy(self._cache)

    # ----- Gestió de memòria cau independent -----

    def _load_cache(self) -> None:
        """Carrega la memòria cau des de disc si existeix."""
        if self._cache_file.exists():
            try:
                with open(self._cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self._cache = data
            except Exception:
                self._cache = {}

    def _save_cache(self) -> None:
        """Desa la memòria cau a disc de manera atòmica."""
        try:
            tmp_path = self._cache_file.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, indent=2, ensure_ascii=False)
            tmp_path.replace(self._cache_file)
        except Exception:
            pass

    def get_cache_entry(self, cache_key: str) -> Optional[Dict[str, Any]]:
        return copy.deepcopy(self._cache.get(cache_key))

    def save_cache_entry(self, cache_key: str, entry: Dict[str, Any], captured_at: Optional[str] = None) -> None:
        now_utc = captured_at or entry.get("captured_at") or datetime.now(timezone.utc).isoformat()
        entry["captured_at"] = now_utc
        entry["polled_at"] = entry.get("polled_at") or now_utc
        entry["freshness"] = "FRESH"
        entry["is_stale"] = False
        entry["bookmaker"] = "BETWAY"
        self._cache[cache_key] = entry
        self._save_cache()

    # ----- Normalització de noms i equips -----

    @staticmethod
    def normalize_name(name: Any) -> str:
        """Normalitza una cadena eliminant accents, caràcters no alfanumèrics i espais extres."""
        if not name or not isinstance(name, str):
            return ""
        s = name.lower()
        replacements = (
            ("á", "a"), ("à", "a"), ("é", "e"), ("è", "e"),
            ("í", "i"), ("ï", "i"), ("ó", "o"), ("ò", "o"),
            ("ú", "u"), ("ü", "u"), ("ñ", "n"), ("ç", "c")
        )
        for orig, rep in replacements:
            s = s.replace(orig, rep)
        s = re.sub(r"[^a-z0-9\s]", " ", s)
        return " ".join(s.split())

    def resolve_canonical_team(self, name_str: str) -> str:
        """Resol el nom canònic d'un equip de Betway contra la base de dades i diccionari d'àlies."""
        if not name_str:
            return ""
        norm = self.normalize_name(name_str)
        if norm in BETWAY_TEAM_ALIASES:
            return BETWAY_TEAM_ALIASES[norm]

        if self.db:
            tid = self.db.find_team_id(name_str) or self.db.find_team_id(norm)
            if tid:
                try:
                    conn = self.db.get_connection()
                    row = conn.execute("SELECT name FROM teams WHERE id = ?", (tid,)).fetchone()
                    if row and row[0]:
                        return row[0]
                except Exception:
                    pass

        return name_str.strip()

    # ----- Peticions HTTP legítimes a Betway -----

    @staticmethod
    def _fetch_url(url: str, timeout_seconds: float = 12.0) -> Tuple[int, str, Dict[str, str]]:
        """Realitza una petició GET legítima amb capçaleres de navegador estàndard."""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8"
        }
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            content = resp.read().decode("utf-8", errors="ignore")
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, content, resp_headers

    @staticmethod
    def _call_services_api(endpoint: str, payload: Dict[str, Any], timeout_seconds: float = 12.0) -> Dict[str, Any]:
        """Realitza una crida POST JSON a l'API de serveis oficial de Betway."""
        url = f"{BETWAY_SERVICES_BASE_URL}/{endpoint.lstrip('/')}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Accept-Language": "es-ES,es;q=0.9"
        }
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            res_text = resp.read().decode("utf-8", errors="ignore")
            return json.loads(res_text)

    # ----- Extracció de dades de competició (RSC) -----

    def fetch_competition_events(self, competition_id: str) -> Dict[str, Any]:
        """
        Extreu tots els partits i quotes 1X2 del torneig a partir de la pàgina de grup de Betway.
        Retorna un diccionari estructurat indexat per ID de partit i per parella d'equips.
        """
        comp_id = competition_id.upper()
        if comp_id not in BETWAY_COMPETITIONS:
            return {"events": {}, "matches": [], "error": f"Competició {competition_id} no suportada per Betway"}

        comp_info = BETWAY_COMPETITIONS[comp_id]
        url = comp_info["url"]

        if self.offline:
            # En mode offline, retorna dades buides o de memòria cau
            return {"events": {}, "matches": [], "source": "offline"}

        try:
            status, html_content, headers = self._fetch_url(url)
            captured_at = headers.get("date")
            dt_captured = parse_datetime_utc(captured_at) or datetime.now(timezone.utc)
            captured_at_iso = dt_captured.isoformat()

            # Extracció de l'arbre RSC
            scripts = re.findall(r'<script([^>]*)>(.*?)</script>', html_content, re.DOTALL)
            match_emos = None

            for _, body in scripts:
                if 'matchEmos' in body:
                    pushes = re.findall(r'self\.__next_f\.push\(\[(\d+),"(.*?)"\]\)', body, re.DOTALL)
                    for _, raw_str in pushes:
                        if 'matchEmos' in raw_str:
                            try:
                                decoded = json.loads('"' + raw_str + '"')
                                lines = decoded.split("\n")
                                for line in lines:
                                    if "matchEmos" in line:
                                        colon = line.find(":")
                                        if colon != -1:
                                            chunk_json = json.loads(line[colon + 1:])
                                            def find_emos(obj):
                                                if isinstance(obj, dict):
                                                    if "matchEmos" in obj and isinstance(obj["matchEmos"], list) and len(obj["matchEmos"]) > 0:
                                                        return obj["matchEmos"][0]
                                                    for v in obj.values():
                                                        found = find_emos(v)
                                                        if found: return found
                                                elif isinstance(obj, list):
                                                    for it in obj:
                                                        found = find_emos(it)
                                                        if found: return found
                                                return None
                                            found_emos = find_emos(chunk_json)
                                            if found_emos:
                                                match_emos = found_emos
                                                break
                            except Exception:
                                pass
                        if match_emos:
                            break
                if match_emos:
                    break

            if not match_emos:
                return {"events": {}, "matches": [], "error": "No s'ha pogut localitzar matchEmos en el contingut RSC de Betway"}

            events_dict = match_emos.get("events", {})
            markets_dict = match_emos.get("markets", {})
            outcomes_dict = match_emos.get("outcomes", {})

            parsed_events: Dict[str, Any] = {}
            parsed_matches: List[Dict[str, Any]] = []

            for ev_id_str, ev in events_dict.items():
                if not isinstance(ev, dict):
                    continue
                ev_name = ev.get("name", "")
                starts_at = ev.get("startsAt")
                feed_id = ev.get("feedId") or ev.get("sportsRadarId") or ev.get("SportsRadarId") or ev.get("FeedId")
                trading = ev.get("trading", "")
                started = bool(ev.get("started"))
                suspended = bool(ev.get("suspended"))

                # Noms d'equips
                parts = [p.strip() for p in ev_name.split(" - ") if p.strip()]
                if len(parts) != 2:
                    continue
                raw_home, raw_away = parts[0], parts[1]
                canonical_home = self.resolve_canonical_team(raw_home)
                canonical_away = self.resolve_canonical_team(raw_away)

                # Cerca del mercat 1X2 estàndard ("win-draw-win")
                odds_1x2: Dict[str, float] = {}
                market_1x2_id = None

                for mid_str, m in markets_dict.items():
                    if str(m.get("eventId")) == str(ev_id_str):
                        cname = m.get("marketCName")
                        # Estrictament win-draw-win estàndard, descartant 1up/2up
                        if cname == "win-draw-win":
                            market_1x2_id = m.get("id") or mid_str
                            outcomes_ids = m.get("outcomes", [])
                            for oid in outcomes_ids:
                                oc = outcomes_dict.get(str(oid)) or outcomes_dict.get(oid)
                                if oc:
                                    coupon = str(oc.get("name", {}).get("coupon") or "").lower()
                                    odd_val = oc.get("oddsDecimal")
                                    if odd_val is not None:
                                        try:
                                            odd_float = float(odd_val)
                                            if odd_float > 1.0:
                                                if coupon in ("local", "1"):
                                                    odds_1x2["1"] = odd_float
                                                elif coupon in ("empate", "x", "draw"):
                                                    odds_1x2["X"] = odd_float
                                                elif coupon in ("visitante", "2", "away"):
                                                    odds_1x2["2"] = odd_float
                                        except (ValueError, TypeError):
                                            pass
                            break

                event_data = {
                    "event_id": ev_id_str,
                    "event_name": ev_name,
                    "raw_home": raw_home,
                    "raw_away": raw_away,
                    "canonical_home": canonical_home,
                    "canonical_away": canonical_away,
                    "starts_at": starts_at,
                    "feed_id": feed_id,
                    "trading": trading,
                    "started": started,
                    "suspended": suspended,
                    "odds_1x2": odds_1x2,
                    "market_id_1x2": market_1x2_id,
                    "captured_at": captured_at_iso
                }
                parsed_events[str(ev_id_str)] = event_data
                parsed_matches.append(event_data)

            return {
                "events": parsed_events,
                "matches": parsed_matches,
                "captured_at": captured_at_iso,
                "competition_id": comp_id
            }

        except Exception as e:
            return {"events": {}, "matches": [], "error": f"Error consultant Betway {comp_id}: {str(e)}", "status": "error"}

    # ----- Resolució de mercats secundaris detallats (GetEventDetails + GetEventMarkets) -----

    def fetch_event_full_markets(self, event_id: int) -> Dict[str, Any]:
        """
        Consulta l'API de serveis de Betway per obtenir tots els mercats d'un partit:
        1X2, Doble Oportunitat, Over/Under 2.5 i BTTS.
        """
        if self.offline:
            return {"error": "offline_mode"}

        try:
            # 1. GetEventDetails per obtenir la llista de MarketIds
            details_payload = dict(BETWAY_API_CONFIG)
            details_payload["EventId"] = int(event_id)
            details_res = self._call_services_api("events/v2/GetEventDetails", details_payload)

            if not details_res.get("Success"):
                return {"error": f"GetEventDetails fallat: {details_res.get('Errors')}"}

            ev_info = details_res.get("Event") or {}
            market_ids = ev_info.get("Markets") or []
            if not market_ids:
                return {"error": "No hi ha MarketIds a l'esdeveniment"}

            # 2. GetEventMarkets amb tots els MarketIds
            markets_payload = dict(BETWAY_API_CONFIG)
            markets_payload["MarketIds"] = [int(mid) for mid in market_ids]
            markets_res = self._call_services_api("events/v2/GetEventMarkets", markets_payload)

            if not markets_res.get("Success"):
                return {"error": f"GetEventMarkets fallat: {markets_res.get('Errors')}"}

            now_utc = datetime.now(timezone.utc).isoformat()
            raw_markets = markets_res.get("Markets", [])
            raw_outcomes = markets_res.get("Outcomes", [])

            # Agrupar seleccions per MarketId
            outcomes_by_mid: Dict[int, List[Dict[str, Any]]] = {}
            for oc in raw_outcomes:
                mid = oc.get("MarketId")
                if mid is not None:
                    outcomes_by_mid.setdefault(int(mid), []).append(oc)

            result = {
                "event_id": event_id,
                "event_name": ev_info.get("EventName"),
                "captured_at": now_utc,
                "1X2": {},
                "market_id_1x2": None,
                "double_chance": {},
                "market_id_dc": None,
                "over_under_2_5": {},
                "market_id_ou25": None,
                "btts": {},
                "market_id_btts": None
            }

            for m in raw_markets:
                mid = m.get("Id")
                title = (m.get("Title") or m.get("Name") or "").strip()
                cname = (m.get("MarketCName") or "").strip()
                groups = m.get("OutcomeGroups") or {}
                hcap = float(m.get("Handicap") or 0.0)

                # 1. 1X2 estàndard (estricte win-draw-win, sense promocions de gol d'avantatge)
                if cname == "win-draw-win" and not result["1X2"]:
                    result["market_id_1x2"] = mid
                    h_oids = groups.get("home", {}).get("outcomes", [])
                    x_oids = groups.get("draw", {}).get("outcomes", [])
                    a_oids = groups.get("away", {}).get("outcomes", [])
                    for oc in raw_outcomes:
                        oid = oc.get("Id")
                        odd = float(oc.get("OddsDecimal") or 0.0)
                        if odd > 1.0:
                            if oid in h_oids: result["1X2"]["1"] = odd
                            elif oid in x_oids: result["1X2"]["X"] = odd
                            elif oid in a_oids: result["1X2"]["2"] = odd

                # 2. Doble Oportunitat
                elif cname == "double-chance" and not result["double_chance"]:
                    result["market_id_dc"] = mid
                    h_oids = groups.get("home", {}).get("outcomes", [])
                    d_oids = groups.get("draw", {}).get("outcomes", [])
                    a_oids = groups.get("away", {}).get("outcomes", [])
                    for oc in raw_outcomes:
                        oid = oc.get("Id")
                        odd = float(oc.get("OddsDecimal") or 0.0)
                        if odd > 1.0:
                            if oid in h_oids: result["double_chance"]["1X"] = odd
                            elif oid in d_oids: result["double_chance"]["12"] = odd
                            elif oid in a_oids: result["double_chance"]["X2"] = odd

                # 3. Over / Under 2.5 Gols
                elif (cname == "-total-goals---2-5" or (hcap == 2.5 and "total-goals" in cname and "3-way" not in cname and "1st-half" not in cname)) and not result["over_under_2_5"]:
                    result["market_id_ou25"] = mid
                    yes_oids = groups.get("yes", {}).get("outcomes", [])
                    no_oids = groups.get("no", {}).get("outcomes", [])
                    for oc in raw_outcomes:
                        oid = oc.get("Id")
                        odd = float(oc.get("OddsDecimal") or 0.0)
                        if odd > 1.0:
                            if oid in yes_oids: result["over_under_2_5"]["over"] = odd
                            elif oid in no_oids: result["over_under_2_5"]["under"] = odd

                # 4. Ambdós Equips Marquen (BTTS)
                elif cname == "both-teams-to-score" and "1st-half" not in cname and not result["btts"]:
                    result["market_id_btts"] = mid
                    yes_oids = groups.get("yes", {}).get("outcomes", [])
                    no_oids = groups.get("no", {}).get("outcomes", [])
                    for oc in raw_outcomes:
                        oid = oc.get("Id")
                        odd = float(oc.get("OddsDecimal") or 0.0)
                        if odd > 1.0:
                            if oid in yes_oids: result["btts"]["yes"] = odd
                            elif oid in no_oids: result["btts"]["no"] = odd

            return result

        except Exception as e:
            return {"error": f"Error consultant mercats complets de l'esdeveniment {event_id}: {str(e)}"}

    # ----- Interfície unificada per partit (get_match_odds) -----

    def get_match_odds(
        self,
        home_team: str,
        away_team: str,
        competition_id: str = "LALIGA",
        match_date_time: Optional[str] = None,
        now: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Descobreix i extreu les quotes reals de Betway per al partit indicat.
        Retorna l'estructura canònica de quotes utilitzada pel motor de surebets.
        """
        comp_id = competition_id.upper()
        h_norm = self.normalize_name(home_team)
        a_norm = self.normalize_name(away_team)
        cache_key = f"{comp_id}_{h_norm}_{a_norm}"

        base_url = BETWAY_COMPETITIONS.get(comp_id, {}).get("url", "https://betway.es")

        odds_data: Dict[str, Any] = {
            "source": f"Betway Espanya ({comp_id})",
            "bookmaker": "BETWAY",
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
            "provenance": "BETWAY",
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

        # 1. Comprovar la memòria cau si no estem forçant quotes fresques
        if not self.force_fresh and cache_key in self._cache:
            cached = copy.deepcopy(self._cache[cache_key])
            fresh_eval = evaluate_freshness(cached.get("captured_at"), now=now, max_age_hours=self.max_odds_age_hours)
            if not fresh_eval["is_stale"] and cached.get("matched"):
                cached.update(fresh_eval)
                return cached

        # 2. Si estem offline i tenim memòria cau (encara que sigui stale per fallback)
        if self.offline:
            if cache_key in self._cache:
                cached = copy.deepcopy(self._cache[cache_key])
                fresh_eval = evaluate_freshness(cached.get("captured_at"), now=now, max_age_hours=self.max_odds_age_hours)
                cached.update(fresh_eval)
                return cached
            odds_data["freshness_reason"] = "offline_no_cache"
            return odds_data

        # 3. Consulta en viu de la competició a Betway
        comp_data = self.fetch_competition_events(comp_id)
        if comp_data.get("error"):
            # Si falla la connexió en viu, fallback a la memòria cau si existeix
            if cache_key in self._cache:
                cached = copy.deepcopy(self._cache[cache_key])
                fresh_eval = evaluate_freshness(cached.get("captured_at"), now=now, max_age_hours=self.max_odds_age_hours)
                cached.update(fresh_eval)
                return cached
            odds_data["freshness_reason"] = comp_data.get("error")
            return odds_data

        # Cerca del partit per noms d'equips
        matched_ev = None
        for ev in comp_data.get("matches", []):
            ev_h_norm = self.normalize_name(ev.get("canonical_home") or ev.get("raw_home") or "")
            ev_a_norm = self.normalize_name(ev.get("canonical_away") or ev.get("raw_away") or "")

            # Coincidència canònica o per substring rellevant
            if (h_norm == ev_h_norm or h_norm in ev_h_norm or ev_h_norm in h_norm) and \
               (a_norm == ev_a_norm or a_norm in ev_a_norm or ev_a_norm in a_norm):
                matched_ev = ev
                break

        if not matched_ev:
            odds_data["freshness_reason"] = "match_not_found_in_betway"
            return odds_data

        ev_id = matched_ev.get("event_id")
        captured_at = matched_ev.get("captured_at")
        odds_data["match_id"] = str(ev_id)
        odds_data["matched"] = True
        odds_data["captured_at"] = captured_at
        odds_data["1X2"] = matched_ev.get("odds_1x2", {})
        odds_data["market_id_1x2"] = matched_ev.get("market_id_1x2")

        # 4. Obtenir mercats secundaris complets (Doble Oportunitat, Over/Under 2.5, BTTS)
        if ev_id:
            try:
                full_markets = self.fetch_event_full_markets(int(ev_id))
                if not full_markets.get("error"):
                    if full_markets.get("1X2"):
                        odds_data["1X2"] = full_markets["1X2"]
                    if full_markets.get("market_id_1x2"):
                        odds_data["market_id_1x2"] = full_markets["market_id_1x2"]
                    if full_markets.get("double_chance"):
                        odds_data["double_chance"] = full_markets["double_chance"]
                        odds_data["market_id_dc"] = full_markets.get("market_id_dc")
                    if full_markets.get("over_under_2_5"):
                        odds_data["over_under_2_5"] = full_markets["over_under_2_5"]
                        odds_data["market_id_ou25"] = full_markets.get("market_id_ou25")
                    if full_markets.get("btts"):
                        odds_data["btts"] = full_markets["btts"]
                        odds_data["market_id_btts"] = full_markets.get("market_id_btts")
            except Exception:
                pass

        # Avaluar frescor
        fresh_eval = evaluate_freshness(captured_at, now=now, max_age_hours=self.max_odds_age_hours)
        odds_data.update(fresh_eval)

        # Desar a la memòria cau independent
        self.save_cache_entry(cache_key, odds_data, captured_at=captured_at)

        return odds_data
