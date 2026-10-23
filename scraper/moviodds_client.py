"""
scraper/moviodds_client.py
==========================
Client autònom i autoritzat per a MoviOdds (https://moviodds.com/).
Recupera quotes oficials prematch en viu de Bet365 per a les 7 competicions
del projecte a cost zero, sense necessitat de clau d'API ni registre.

Requisits de robustesa i frescor:
1. Una única descàrrega global comprimida (gzip) per execució del pipeline:
   - Endpoint: https://moviodds.com/odds?sport=soccer
   - Minimitza la latència i no satura l'API ni fa consultes repetitives.
2. Control estricte de frescor per mercat:
   - Processa 'group_status' de MoviOdds i descarta qualsevol mercat marcat com a 'stale: true'.
   - Descarta seleccions amb 'suspended: true' o quotes invàlides (<= 1.0).
   - Utilitza marques temporals de confirmació de la font ('polled_at', 'ts'), MAI 'cached_at' com a substitut.
   - Conserva la traçabilitat completa per mercat a 'market_status'.
3. Associació estricta i inequívoca de partits:
   - Exigeix coincidència canònica de TOTS DOS equips (local i visitant) i lliga compatible.
   - Comprova la data/hora d'inici contra 'kickoff_utc' amb tolerància explícita documentada (28.0 hores).
   - Elimina alternatives insegures (coincidències parcials de paraules o per un sol equip).
   - Descarta correspondències ambigües o múltiples.
"""

from __future__ import annotations

import os
import time
import json
import gzip
import re
import unicodedata
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
CACHE_FILE = ROOT_DIR / "data" / "bet365_odds_cache.json"

# Tolerància temporal explícita documentada entre SQLite i MoviOdds (28.0 hores):
# Permet cobrir fins a +/- 24h per reprogramacions dins de la mateixa jornada i fins a 2h de desfasament horari UTC vs CET/CEST.
MAX_KICKOFF_TOLERANCE_HOURS = 28.0

# Antiguitat màxima permeses per considerar vigent una quota de la font (24 hores)
MAX_ODDS_AGE_SECONDS = 86400.0

COMPETITION_FEED_MAP = {
    "LALIGA": "Spain La Liga",
    "PREMIER": "England Premier League",
    "HYPERMOTION": "Spain Segunda",
    "CHAMPIONSHIP": "England Championship",
    "BUNDESLIGA": "Germany Bundesliga I",
    "LIGUE1": "France Ligue 1",
    "SERIEA": "Italy Serie A",
}

REVERSE_COMPETITION_MAP = {v: k for k, v in COMPETITION_FEED_MAP.items()}

# Àlies canònics complets per a totes les competicions oficials
TEAM_ALIASES = {
    # England (Premier & Championship)
    "sheff utd": "sheffield united",
    "sheffield utd": "sheffield united",
    "sheff wed": "sheffield wednesday",
    "qpr": "queens park rangers",
    "man city": "manchester city",
    "man utd": "manchester united",
    "manchester utd": "manchester united",
    "spurs": "tottenham hotspur",
    "tottenham": "tottenham hotspur",
    "wolves": "wolverhampton wanderers",
    "wolverhampton": "wolverhampton wanderers",
    "bournemouth": "afc bournemouth",
    "brighton": "brighton & hove albion",
    "brighton and hove albion": "brighton & hove albion",
    "newcastle": "newcastle united",
    "leeds": "leeds united",
    "leeds utd": "leeds united",
    "west ham": "west ham united",
    "west brom": "west bromwich albion",
    "preston": "preston north end",
    "blackburn": "blackburn rovers",
    "norwich": "norwich city",
    "cardiff": "cardiff city",
    "birmingham": "birmingham city",
    "stoke": "stoke city",
    "swansea": "swansea city",
    "derby": "derby county",
    "bristol city": "bristol city",
    "charlton": "charlton athletic",
    "oxford": "oxford united",
    "coventry": "coventry city",
    "luton": "luton town",
    "plymouth": "plymouth argyle",
    "portsmouth": "portsmouth",
    "watford": "watford",
    "southampton": "southampton",

    # Spain (LaLiga & Hypermotion)
    "atletico madrid": "atletico de madrid",
    "athletic club": "athletic bilbao",
    "athletic": "athletic bilbao",
    "racing santander": "racing de santander",
    "deportivo a coruna": "deportivo de la coruna",
    "rc deportivo de la coruna": "deportivo de la coruna",
    "celta vigo": "rc celta de vigo",
    "celta de vigo": "rc celta de vigo",
    "celta": "rc celta de vigo",
    "elche": "elche cf",
    "espanyol": "rcd espanyol",
    "mallorca": "rcd mallorca",
    "eibar": "sd eibar",
    "alaves": "deportivo alaves",
    "cd alaves": "deportivo alaves",
    "cordoba": "cordoba cf",
    "leganes": "cd leganes",
    "rayo": "rayo vallecano",
    "valencia": "valencia cf",
    "villarreal": "villarreal cf",
    "betis": "real betis",
    "sociedad": "real sociedad",
    "sevilla": "sevilla fc",
    "osasuna": "ca osasuna",
    "getafe": "getafe cf",
    "girona": "girona fc",
    "valladolid": "real valladolid",
    "zaragoza": "real zaragoza",
    "sporting gijon": "real sporting",
    "sporting": "real sporting",
    "castellon": "cd castellon",
    "sabadell": "ce sabadell",
    "tenerife": "cd tenerife",
    "oviedo": "real oviedo",
    "racing ferrol": "racing de ferrol",
    "cartagena": "fc cartagena",
    "granada": "granada cf",
    "almeria": "ud almeria",
    "cadiz": "cadiz cf",
    "mirandes": "cd mirandes",
    "burgos": "burgos cf",
    "huesca": "sd huesca",
    "albacete": "albacete balompie",
    "levante": "levante ud",
    "malaga": "malaga cf",

    # Germany (Bundesliga)
    "cologne": "colonia",
    "1 fc koln": "colonia",
    "1 fc colonia": "colonia",
    "koln": "colonia",
    "schalke 04": "schalke",
    "fc schalke 04": "schalke",
    "schalke": "schalke",
    "freiburg": "friburgo",
    "sc freiburg": "friburgo",
    "paderborn": "sc paderborn",
    "sc paderborn 07": "sc paderborn",
    "hamburg": "hamburger sv",
    "elversberg": "sv elversberg",
    "augsburg": "augsburgo",
    "fc augsburgo": "augsburgo",
    "union berlin": "1 fc union berlin",
    "mainz": "mainz 05",
    "1 fsv mainz 05": "mainz 05",
    "werder bremen": "werder bremen",
    "bayern munich": "bayern munich",
    "bayern munich": "bayern munich",
    "bayern munich": "bayern munich",
    "bayern munich": "bayern munich",
    "eintracht frankfurt": "eintracht frankfurt",
    "eintracht francfort": "eintracht frankfurt",
    "leipzig": "rb leipzig",
    "dortmund": "borussia dortmund",
    "leverkusen": "bayer leverkusen",
    "hoffenheim": "tsg hoffenheim",
    "stuttgart": "vfb stuttgart",
    "gladbach": "borussia monchengladbach",
    "borussia mgladbach": "borussia monchengladbach",

    # France (Ligue 1)
    "stade rennais": "rennes",
    "stade brestois": "brest",
    "brest": "brest",
    "psg": "paris saint germain",
    "paris sg": "paris saint germain",
    "lyon": "olympique lyonnais",
    "st etienne": "saint etienne",
    "paris fc": "paris fc",
    "troyes": "es troyes ac",
    "lorient": "fc lorient",
    "strasbourg": "rc strasbourg",
    "nice": "ogc nice",
    "monaco": "as monaco",
    "lille": "lille osc",
    "marseille": "olympique de marseille",
    "toulouse": "toulouse fc",
    "le mans": "le mans fc",
    "le havre": "le havre ac",
    "auxerre": "aj auxerre",
    "angers": "angers sco",
    "lens": "rc lens",

    # Italy (Serie A)
    "sassuolo": "us sassuolo",
    "bologna": "bologna fc",
    "roma": "as roma",
    "lazio": "ss lazio",
    "parma": "parma calcio",
    "napoli": "ssc napoli",
    "monza": "ac monza",
    "inter milan": "inter de milan",
    "inter": "inter de milan",
    "ac milan": "milan",
    "fiorentina": "acf fiorentina",
    "atalanta": "atalanta bc",
    "genoa": "genoa cfc",
    "venezia": "venezia fc",
    "cagliari": "cagliari calcio",
    "como": "como 1907",
    "como 1907": "como 1907",
    "udinese": "udinese calcio",
    "frosinone": "frosinone calcio",
    "juventus": "juventus turin",
    "lecce": "us lecce",
    "torino": "torino fc",
    "verona": "hellas verona",
    "empoli": "empoli fc",
}


def parse_datetime_utc(dt_val: Any) -> Optional[datetime]:
    """
    Normalitza de manera segura qualsevol format de data/hora a datetime amb zona horària UTC.
    Suporta formats 'DD.MM.YYYY HH:MM', 'YYYY-MM-DDTHH:MM:SSZ', i variants amb o sense decalatge.
    """
    if not dt_val:
        return None
    if isinstance(dt_val, datetime):
        return dt_val if dt_val.tzinfo else dt_val.replace(tzinfo=timezone.utc)

    s = str(dt_val).strip()
    if not s:
        return None

    # 1. Format 'DD.MM.YYYY HH:MM' típic de Flashscore / SQLite
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})(?:\s+(\d{1,2}):(\d{2}))?$", s)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hour = int(m.group(4) or 0)
        minute = int(m.group(5) or 0)
        return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)

    # 2. Format ISO 8601 estàndard (ex: 2026-10-24T12:00:00Z o 2026-10-24T12:00:00.123456Z)
    try:
        # Eliminar fracció de segons si és massa llarga per a strptime
        clean_iso = re.sub(r"(\.\d{6})\d+Z$", r"\1Z", s)
        if clean_iso.endswith("Z"):
            clean_iso = clean_iso[:-1] + "+00:00"
        dt = datetime.fromisoformat(clean_iso)
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass

    return None


class MoviOddsClient:
    """Client per descarregar i analitzar quotes de futbol de Bet365 via MoviOdds."""

    FEED_URL = "https://moviodds.com/odds?sport=soccer"
    _instance: Optional[MoviOddsClient] = None

    def __init__(self, cache_ttl_seconds: float = 600.0):
        self._cache_ttl = cache_ttl_seconds
        self._raw_matches: List[Dict[str, Any]] = []
        self._last_fetch_time: float = 0.0
        self._indexed_matches: Dict[str, List[Dict[str, Any]]] = {}
        self._parsed_odds_cache: Dict[str, Dict[str, Any]] = {}
        self._db_manager = None

    @classmethod
    def get_instance(cls) -> MoviOddsClient:
        """Retorna la instància singleton per compartir el feed en la mateixa execució."""
        if cls._instance is None:
            cls._instance = MoviOddsClient()
        return cls._instance

    def _get_db(self):
        if self._db_manager is None:
            try:
                from database.db_manager import DatabaseManager
                self._db_manager = DatabaseManager()
            except Exception:
                self._db_manager = None
        return self._db_manager

    @staticmethod
    def normalize_name(text: str) -> str:
        """Normalitza cadenes eliminant accents, prefixos de club i aplicant àlies canònics."""
        if not text:
            return ""
        nfkd = unicodedata.normalize("NFKD", str(text))
        clean = "".join([c for c in nfkd if not unicodedata.combining(c)]).lower()
        # Eliminar prefixos de numeració de club (ex: 1. FC, 1. FSV)
        clean = re.sub(r"(^|\s)1\.\s*", " ", clean)
        # Eliminar sufixos o prefixos de denominació social de club
        clean = re.sub(
            r"\b(cf|fc|cd|rcd|ud|sd|ca|afc|bsc|sc|vfb|rb|tsg|ogc|rc|us|ac|ss|as)\b",
            " ",
            clean,
        )
        clean = re.sub(r"[^a-z0-9]", " ", clean).strip()
        clean = re.sub(r"\s+", " ", clean)

        # Aplicar àlies canònics amb límits de paraula estrictes
        for alias, target in TEAM_ALIASES.items():
            pattern = rf"\b{re.escape(alias)}\b"
            if re.search(pattern, clean):
                clean = re.sub(pattern, target, clean)

        clean = clean.replace(" ", "")
        return clean

    def resolve_team_key(self, name: str) -> str:
        """
        Resol el nom d'un equip al seu identificador canònic a través de DatabaseManager
        o via normalització i àlies canònics estrictes.
        """
        if not name:
            return ""
        db = self._get_db()
        if db:
            tid = db.find_team_id(name)
            if tid:
                return tid.upper()
        # Fallback a nom normalitzat canònic
        return self.normalize_name(name)

    def fetch_feed(self, force_refresh: bool = False, retries: int = 3) -> List[Dict[str, Any]]:
        """
        Descarrega el feed global de futbol en una sola crida HTTP amb compressió gzip.
        Gestiona automàticament codis 429 amb backoff respectant Retry-After.
        """
        now = time.time()
        if not force_refresh and (now - self._last_fetch_time < self._cache_ttl) and self._raw_matches:
            return self._raw_matches

        headers = {
            "User-Agent": "PrediccionsLliga/1.0 (Authorized Bet365 Client)",
            "Accept-Encoding": "gzip",
            "Accept": "application/json",
        }

        for attempt in range(retries):
            start_t = time.time()
            req = urllib.request.Request(self.FEED_URL, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=25) as resp:
                    raw = resp.read()
                    if resp.info().get("Content-Encoding") == "gzip":
                        raw = gzip.decompress(raw)
                    data = json.loads(raw.decode("utf-8"))

                matches = data.get("matches", []) if isinstance(data, dict) else []
                self._raw_matches = matches
                self._last_fetch_time = time.time()
                self._index_matches()
                duration = time.time() - start_t
                print(f"[MoviOddsClient] Descarregat feed global: {len(matches)} partits en {duration:.2f}s.")
                return matches

            except urllib.error.HTTPError as e:
                print(f"[!] MoviOddsClient HTTPError {e.code}: {e.reason} (intent {attempt + 1}/{retries})")
                if e.code == 429:
                    retry_after = int(e.headers.get("Retry-After", 6))
                    wait_time = max(retry_after, 5) + 1
                    print(f"    Rate limit detectat. Esperant {wait_time}s abans de reintentar...")
                    time.sleep(wait_time)
                else:
                    time.sleep(2)
                if attempt == retries - 1:
                    print(f"[!] MoviOddsClient: Error definitiu descarregant el feed: {e}")
                    return self._raw_matches

            except Exception as e:
                print(f"[!] MoviOddsClient error de connexió (intent {attempt + 1}/{retries}): {e}")
                time.sleep(2)
                if attempt == retries - 1:
                    return self._raw_matches

        return self._raw_matches

    def _index_matches(self):
        """Pre-indexa els partits descarregats per competició i claus canòniques d'equip."""
        self._indexed_matches.clear()
        self._parsed_odds_cache.clear()

        for m in self._raw_matches:
            feed_league = m.get("league", "")
            if feed_league not in REVERSE_COMPETITION_MAP:
                continue

            comp_id = REVERSE_COMPETITION_MAP[feed_league]
            m_h = m.get("home", "")
            m_a = m.get("away", "")
            h_key = self.resolve_team_key(m_h)
            a_key = self.resolve_team_key(m_a)

            if h_key and a_key:
                key = f"{comp_id}_{h_key}_{a_key}"
                self._indexed_matches.setdefault(key, []).append(m)

    def has_feed(self) -> bool:
        """Indica si el feed global està carregat en memòria i és vigent."""
        now = time.time()
        return bool(self._raw_matches and (now - self._last_fetch_time < self._cache_ttl))

    def match_fixture(
        self,
        home_team: str,
        away_team: str,
        competition_id: str,
        match_date_time: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Cerca a l'índex de MoviOdds la correspondència inequívoca d'un partit.
        Requisits estrictes:
        1. Ambdós equips (local i visitant) han de coincidir canònicament.
        2. La competició ha de ser compatible.
        3. Si es proporciona match_date_time, la data/hora ha d'estar dins de la tolerància de 28.0 hores.
        4. Rebutja coincidències parcials insegures (no admet intersecció de paraules aïllades).
        5. Exigeix una única correspondència vàlida (descarta ambigüitats).
        """
        if not self.has_feed():
            return None

        comp_id = competition_id.upper()
        feed_league = COMPETITION_FEED_MAP.get(comp_id)
        if not feed_league:
            return None

        h_key = self.resolve_team_key(home_team)
        a_key = self.resolve_team_key(away_team)
        h_norm = self.normalize_name(home_team)
        a_norm = self.normalize_name(away_team)

        sqlite_dt = parse_datetime_utc(match_date_time) if match_date_time else None

        # Partits de la mateixa lliga al feed
        league_matches = [
            m for m in self._raw_matches if m.get("league") == feed_league
        ]

        candidates = []
        for m in league_matches:
            m_h = m.get("home", "")
            m_a = m.get("away", "")
            m_h_key = self.resolve_team_key(m_h)
            m_a_key = self.resolve_team_key(m_a)
            m_h_norm = self.normalize_name(m_h)
            m_a_norm = self.normalize_name(m_a)

            # 1. Coincidència canònica estricta de TOTS DOS equips
            h_matches = (m_h_key == h_key) or (m_h_norm == h_norm)
            a_matches = (m_a_key == a_key) or (m_a_norm == a_norm)
            if not (h_matches and a_matches):
                continue

            # 2. Comprovació de coherència temporal amb tolerància documentada
            ko_str = m.get("kickoff_utc")
            ko_dt = parse_datetime_utc(ko_str)
            if sqlite_dt and ko_dt:
                diff_hours = abs((sqlite_dt - ko_dt).total_seconds()) / 3600.0
                if diff_hours > MAX_KICKOFF_TOLERANCE_HOURS:
                    # Descartat per incompatibilitat de calendari / reprogramació llunyana
                    continue

            candidates.append(m)

        # 3. Exigir una ÚNICA correspondència inequívoca
        if len(candidates) == 1:
            return candidates[0]
        elif len(candidates) > 1:
            print(
                f"[!] MoviOddsClient: Associació ambigua ({len(candidates)} partits coincidents) per a {home_team} vs {away_team} a {comp_id}. Descartat."
            )
            return None

        return None

    def get_match_odds(
        self,
        home_team: str,
        away_team: str,
        competition_id: str = "LALIGA",
        match_date_time: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Retorna les quotes completes oficials de Bet365 per al partit sol·licitat.
        Retorna None si el feed no està carregat, no hi ha correspondència real o les quotes són obsoletes.
        """
        if not self.has_feed():
            return None

        comp_id = competition_id.upper()
        h_norm = self.normalize_name(home_team)
        a_norm = self.normalize_name(away_team)
        dt_key = (match_date_time or "").replace(" ", "_")
        cache_key = f"{comp_id}_{h_norm}_{a_norm}_{dt_key}"

        if cache_key in self._parsed_odds_cache:
            return self._parsed_odds_cache[cache_key]

        raw_match = self.match_fixture(
            home_team, away_team, comp_id, match_date_time=match_date_time
        )
        if not raw_match:
            return None

        parsed = self.parse_match_odds(raw_match, comp_id)
        if parsed and parsed.get("matched") and not parsed.get("is_stale"):
            self._parsed_odds_cache[cache_key] = parsed
            return parsed

        return None

    def parse_match_odds(
        self, raw_match: Dict[str, Any], competition_id: str
    ) -> Dict[str, Any]:
        """
        Interpreta l'estructura de quotes de MoviOdds, avaluant la frescor real de cada mercat:
        - Descarta seleccions suspeses ('suspended: true') i preus invàlids (<= 1.0).
        - Avalua 'group_status' i descarta quotes de mercats marcats com a 'stale: true'.
        - Registra marques temporals oficials de confirmació ('polled_at') i de variació ('ts').
        """
        fi_id = str(raw_match.get("id", ""))
        home_name = raw_match.get("home", "")
        away_name = raw_match.get("away", "")
        kickoff_utc = raw_match.get("kickoff_utc")
        raw_polled_at = raw_match.get("polled_at")
        group_status = raw_match.get("group_status") or {}

        now_utc = datetime.now(timezone.utc)

        result: Dict[str, Any] = {
            "source": f"Bet365 via MoviOdds (FI: {fi_id})",
            "bookmaker": "BET365",
            "url": "https://www.bet365.es/#/AS/B1/",
            "match_id": fi_id,
            "matched_title": f"{home_name} vs {away_name}",
            "matched": False,
            "is_manual_override": False,
            "provenance": "BET365_MOVIODDS",
            "kickoff_utc": kickoff_utc,
            "polled_at": raw_polled_at,
            "is_stale": False,
            "market_status": {},
            "1X2": {},
            "double_chance": {},
            "over_under_2_5": {},
            "btts": {},
            "corners": {},
            "cards": {},
        }

        quotes = raw_match.get("quotes", [])
        if not quotes:
            return result

        # 1. Agrupar quotes per nom de grup i avaluar frescor per mercat
        groups: Dict[str, List[Dict[str, Any]]] = {}
        market_freshness: Dict[str, Dict[str, Any]] = {}

        for q in quotes:
            # Excloure seleccions marcades explícitament com a suspeses
            if q.get("suspended") is True:
                continue

            gid = str(q.get("group_id") or "")
            g_info = group_status.get(gid) or {}

            # Descarta mercats marcats com a stale al feed
            if g_info.get("stale") is True:
                continue

            price = self._safe_float(q.get("odds_dec"))
            if not price:
                continue

            gname = q.get("group_name", "")
            groups.setdefault(gname, []).append(q)

            # Rastrejar frescor del grup
            polled_t = g_info.get("polled_at") or raw_polled_at
            q_ts = q.get("ts") or ""
            if gname not in market_freshness:
                market_freshness[gname] = {
                    "polled_at": polled_t,
                    "ts": q_ts,
                    "stale": False,
                }
            else:
                if q_ts and q_ts > (market_freshness[gname].get("ts") or ""):
                    market_freshness[gname]["ts"] = q_ts

        # 2. Mercat 1X2 (Full Time Result)
        ft_quotes = groups.get("Full Time Result", [])
        h_norm = self.normalize_name(home_name)
        a_norm = self.normalize_name(away_name)

        latest_1x2_ts = ""
        for q in ft_quotes:
            label = str(q.get("label", ""))
            price = self._safe_float(q.get("odds_dec"))
            if not price:
                continue
            q_ts = q.get("ts", "")
            if q_ts > latest_1x2_ts:
                latest_1x2_ts = q_ts

            l_norm = self.normalize_name(label)
            if l_norm == h_norm or label in ("1", "Home"):
                result["1X2"]["1"] = price
            elif label.lower() in ("draw", "empat", "x", "tie") or l_norm in ("draw", "empat"):
                result["1X2"]["X"] = price
            elif l_norm == a_norm or label in ("2", "Away"):
                result["1X2"]["2"] = price

        if latest_1x2_ts:
            result["ts"] = latest_1x2_ts

        # 3. Doble Oportunitat
        dc_quotes = groups.get("Double Chance", [])
        for q in dc_quotes:
            label = str(q.get("label", "")).lower()
            price = self._safe_float(q.get("odds_dec"))
            if not price:
                continue
            if "1x" in label or ("home" in label and "draw" in label):
                result["double_chance"]["1X"] = price
            elif "12" in label or ("home" in label and "away" in label):
                result["double_chance"]["12"] = price
            elif "x2" in label or "2x" in label or ("draw" in label and "away" in label):
                result["double_chance"]["X2"] = price

        # 4. Gols Over / Under 2.5 (línia estricta de 2.5)
        for gname in ("Match Goals", "Goals Over/Under", "Alternative Total Goals"):
            for q in groups.get(gname, []):
                line = str(q.get("line") or q.get("handicap") or "")
                if line != "2.5":
                    continue
                price = self._safe_float(q.get("odds_dec"))
                if not price:
                    continue
                label = str(q.get("label") or q.get("side") or "").lower()
                if "over" in label or "mas" in label:
                    result["over_under_2_5"]["Over 2.5"] = price
                    result["over_under_2_5"]["over"] = price
                elif "under" in label or "menos" in label:
                    result["over_under_2_5"]["Under 2.5"] = price
                    result["over_under_2_5"]["under"] = price

        # 5. Ambdós Equips Marquen (BTTS)
        for q in groups.get("Both Teams to Score", groups.get("Both Teams To Score", [])):
            price = self._safe_float(q.get("odds_dec"))
            if not price:
                continue
            label = str(q.get("label", "")).lower()
            if label in ("yes", "si", "sí", "1"):
                result["btts"]["Sí"] = price
                result["btts"]["yes"] = price
            elif label in ("no", "2"):
                result["btts"]["No"] = price
                result["btts"]["no"] = price

        # 6. Córners
        for gname, qlist in groups.items():
            if "corner" in gname.lower():
                for q in qlist:
                    price = self._safe_float(q.get("odds_dec"))
                    if not price:
                        continue
                    lbl = str(q.get("label") or q.get("side") or "").strip()
                    line = str(q.get("line") or q.get("handicap") or "").strip()
                    item_key = f"{gname} {lbl} {line}".strip()
                    result["corners"][item_key] = price

        # 7. Targetes
        for gname, qlist in groups.items():
            if "card" in gname.lower():
                for q in qlist:
                    price = self._safe_float(q.get("odds_dec"))
                    if not price:
                        continue
                    lbl = str(q.get("label") or q.get("side") or "").strip()
                    line = str(q.get("line") or q.get("handicap") or "").strip()
                    item_key = f"{gname} {lbl} {line}".strip()
                    result["cards"][item_key] = price

        # Registrar la traçabilitat de frescor
        result["market_status"] = market_freshness

        # Verificar validesa del mercat 1X2 i confirmació temporal
        if len(result["1X2"]) == 3:
            source_time = result.get("polled_at") or result.get("ts") or raw_match.get("last_seen")
            if source_time:
                source_dt = parse_datetime_utc(source_time)
                if source_dt:
                    age_s = (now_utc - source_dt).total_seconds()
                    if 0 <= age_s <= MAX_ODDS_AGE_SECONDS:
                        result["matched"] = True
                        result["is_stale"] = False
                    else:
                        result["matched"] = False
                        result["is_stale"] = True
                else:
                    result["matched"] = False
                    result["is_stale"] = True
            elif self._last_fetch_time > 0 and (time.time() - self._last_fetch_time <= MAX_ODDS_AGE_SECONDS):
                result["matched"] = True
                result["is_stale"] = False
            else:
                result["matched"] = False
                result["is_stale"] = True
        else:
            result["matched"] = False

        return result

    @staticmethod
    def _safe_float(val: Any) -> Optional[float]:
        try:
            f = float(val)
            return round(f, 2) if f > 1.0 else None
        except (ValueError, TypeError):
            return None

    @staticmethod
    def is_odds_entry_fresh(odds_entry: Dict[str, Any], max_age_seconds: float = MAX_ODDS_AGE_SECONDS) -> bool:
        """
        Valida de forma estricta la frescor d'una entrada a partir del timestamp de la font
        ('polled_at' o 'ts'). MAI utilitza 'cached_at' com a substitut de l'hora de confirmació.
        """
        if not odds_entry or not isinstance(odds_entry, dict):
            return False
        if odds_entry.get("is_stale"):
            return False

        source_time = odds_entry.get("polled_at") or odds_entry.get("ts")
        if not source_time:
            return False

        dt = parse_datetime_utc(source_time)
        if not dt:
            return False

        age_s = (datetime.now(timezone.utc) - dt).total_seconds()
        return 0 <= age_s <= max_age_seconds
