"""
scraper/moviodds_client.py
==========================
Client autònom i autoritzat per a MoviOdds (https://moviodds.com/).
Recupera quotes oficials prematch en viu de Bet365 per a les 7 competicions
del projecte a cost zero, sense necessitat de clau d'API ni registre.

Característiques clau:
1. Una única descàrrega global comprimida (gzip) per execució del pipeline:
   - Endpoint: https://moviodds.com/odds?sport=soccer
   - Minimitza la latència i no satura l'API ni fa consultes repetitives.
2. Suport de resiliència:
   - Gestió d'errors HTTP, timeouts i respecte per la capçalera 'Retry-After' en cas de codi 429.
3. Associació inequívoca de partits:
   - Exigeix coincidència de TOTS DOS equips (local i visitant) i lliga compatible.
   - Resolució d'àlies canònics (ex. Sheff Utd -> Sheffield United, QPR -> Queens Park Rangers, etc.).
   - Descarta correspondències ambigües o múltiples.
4. Extracció exhaustiva de mercats oficials Bet365:
   - 1X2 (Full Time Result)
   - Doble Oportunitat
   - Gols Over/Under 2.5 (línia estricta de 2.5 gols)
   - Ambdós Equips Marquen (BTTS: Sí / No)
   - Córners (totals asiàtics, línies 2-way, línies alternatives)
   - Targetes (targetes totals del partit, handicap, targetes per equip)
5. Traçabilitat i frescor de dades:
   - Provenance: 'BET365_MOVIODDS'
   - Registre de 'ts' (variació de quota), 'polled_at' (confirmació) i exclusió de quotes suspeses.
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
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
CACHE_FILE = ROOT_DIR / "data" / "bet365_odds_cache.json"

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

# Àlies canònics per a noms d'equips entre les diferents lligues i la nomenclatura de Bet365
TEAM_ALIASES = {
    "sheff utd": "sheffield united",
    "sheff wed": "sheffield wednesday",
    "qpr": "queens park rangers",
    "cologne": "colonia",
    "freiburg": "friburgo",
    "stade rennais": "rennais",
    "rennes": "rennais",
    "stade brestois": "brest",
    "brest": "brest",
    "psg": "paris saint germain",
    "paris sg": "paris saint germain",
    "lyon": "olympique lyonnais",
    "st etienne": "saint etienne",
    "celta fortuna": "celta de vigo b",
    "celta b": "celta de vigo b",
    "man city": "manchester city",
    "man utd": "manchester united",
    "spurs": "tottenham",
    "wolves": "wolverhampton",
    "atletico madrid": "atletico de madrid",
    "athletic club": "athletic bilbao",
    "racing santander": "racing de santander",
    "deportivo a coruna": "deportivo de la coruna",
    "elche": "elche cf",
    "espanyol": "rcd espanyol",
    "mallorca": "rcd mallorca",
    "eibar": "sd eibar",
    "alaves": "deportivo alaves",
    "cordoba": "cordoba cf",
    "leganes": "cd leganes",
    "paderborn": "sc paderborn",
    "hamburg": "hamburger sv",
    "elversberg": "sv elversberg",
    "augsburg": "augsburgo",
    "union berlin": "1 fc union berlin",
    "paris fc": "paris fc",
    "troyes": "es troyes ac",
    "lorient": "fc lorient",
    "strasbourg": "rc strasbourg",
    "nice": "ogc nice",
    "monaco": "as monaco",
    "lille": "lille osc",
    "sassuolo": "us sassuolo",
    "bologna": "bologna fc",
    "roma": "as roma",
    "lazio": "ss lazio",
    "parma": "parma calcio",
    "napoli": "ssc napoli",
    "monza": "ac monza",
}


class MoviOddsClient:
    """Client per descarregar i analitzar quotes de futbol de Bet365 via MoviOdds."""

    FEED_URL = "https://moviodds.com/odds?sport=soccer"
    _instance: Optional[MoviOddsClient] = None

    def __init__(self, cache_ttl_seconds: float = 600.0):
        self._cache_ttl = cache_ttl_seconds
        self._raw_matches: List[Dict[str, Any]] = []
        self._last_fetch_time: float = 0.0
        self._indexed_matches: Dict[str, Dict[str, Any]] = {}
        self._parsed_odds_cache: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def get_instance(cls) -> MoviOddsClient:
        """Retorna la instància singleton per compartir el feed en la mateixa execució."""
        if cls._instance is None:
            cls._instance = MoviOddsClient()
        return cls._instance

    @staticmethod
    def normalize_name(text: str) -> str:
        """Normalitza cadenes eliminant accents, prefixos de club i aplicant àlies."""
        if not text:
            return ""
        nfkd = unicodedata.normalize("NFKD", str(text))
        clean = "".join([c for c in nfkd if not unicodedata.combining(c)]).lower()
        # Eliminar prefixos/sufixos comuns de club (com 1. FC, CF, etc.)
        clean = re.sub(r"(^|\s)1\.\s*", " ", clean)
        clean = re.sub(
            r"\b(cf|fc|cd|rcd|ud|sd|ca|afc|bsc|sc|vfb|rb|tsg|ogc|rc|us|ac|ss|as)\b",
            " ",
            clean,
        )
        clean = re.sub(r"[^a-z0-9]", " ", clean).strip()
        clean = re.sub(r"\s+", " ", clean)

        # Resolució d'àlies amb límits de paraula estrictes
        for alias, target in TEAM_ALIASES.items():
            pattern = rf"\b{re.escape(alias)}\b"
            if re.search(pattern, clean):
                clean = re.sub(pattern, target, clean)

        clean = clean.replace(" ", "")
        return clean

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
        """Pre-indexa els partits descarregats per competició i clau d'enfrontament."""
        self._indexed_matches.clear()
        self._parsed_odds_cache.clear()

        for m in self._raw_matches:
            feed_league = m.get("league", "")
            if feed_league not in REVERSE_COMPETITION_MAP:
                continue

            comp_id = REVERSE_COMPETITION_MAP[feed_league]
            m_h = m.get("home", "")
            m_a = m.get("away", "")
            h_norm = self.normalize_name(m_h)
            a_norm = self.normalize_name(m_a)

            if h_norm and a_norm:
                key = f"{comp_id}_{h_norm}_{a_norm}"
                self._indexed_matches[key] = m

    def has_feed(self) -> bool:
        """Indica si el feed global està carregat en memòria i és vigent."""
        now = time.time()
        return bool(self._raw_matches and (now - self._last_fetch_time < self._cache_ttl))

    def match_fixture(
        self, home_team: str, away_team: str, competition_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Cerca a l'índex de MoviOdds la correspondència exacta per a ambdós equips.
        Exigeix coincidència inequívoca d'ambdós equips i de la competició.
        """
        if not self.has_feed():
            return None

        comp_id = competition_id.upper()
        feed_league = COMPETITION_FEED_MAP.get(comp_id)
        if not feed_league:
            return None

        h_norm = self.normalize_name(home_team)
        a_norm = self.normalize_name(away_team)

        # 1. Cerca directa per clau composta
        direct_key = f"{comp_id}_{h_norm}_{a_norm}"
        if direct_key in self._indexed_matches:
            return self._indexed_matches[direct_key]

        # 2. Cerca flexible dins de la mateixa competició
        league_matches = [
            m for m in self._raw_matches if m.get("league") == feed_league
        ]

        candidates = []
        for m in league_matches:
            m_h = self.normalize_name(m.get("home", ""))
            m_a = self.normalize_name(m.get("away", ""))

            # Coincidència bidireccional estricta per a ambdós equips
            h_matches = (
                (h_norm in m_h or m_h in h_norm)
                and len(h_norm) >= 3
                and len(m_h) >= 3
            )
            a_matches = (
                (a_norm in m_a or m_a in a_norm)
                and len(a_norm) >= 3
                and len(m_a) >= 3
            )

            if not h_matches:
                h_words = set(re.findall(r"[a-z]{3,}", home_team.lower()))
                mh_words = set(re.findall(r"[a-z]{3,}", str(m.get("home", "")).lower()))
                h_matches = bool(h_words.intersection(mh_words))

            if not a_matches:
                a_words = set(re.findall(r"[a-z]{3,}", away_team.lower()))
                ma_words = set(re.findall(r"[a-z]{3,}", str(m.get("away", "")).lower()))
                a_matches = bool(a_words.intersection(ma_words))

            if h_matches and a_matches:
                candidates.append(m)

        # Exigir una ÚNICA correspondència vàlida per evitar associacions ambigües
        if len(candidates) == 1:
            return candidates[0]
        elif len(candidates) > 1:
            print(f"[!] MoviOddsClient: Associació ambigua ({len(candidates)} candidats) per a {home_team} vs {away_team} a {comp_id}. Descartat.")
            return None

        return None

    def get_match_odds(
        self, home_team: str, away_team: str, competition_id: str = "LALIGA"
    ) -> Optional[Dict[str, Any]]:
        """
        Retorna les quotes completes oficials de Bet365 per al partit sol·licitat.
        Retorna None si el feed no està carregat o no hi ha correspondència real.
        """
        if not self.has_feed():
            return None

        comp_id = competition_id.upper()
        h_norm = self.normalize_name(home_team)
        a_norm = self.normalize_name(away_team)
        cache_key = f"{comp_id}_{h_norm}_{a_norm}"

        if cache_key in self._parsed_odds_cache:
            return self._parsed_odds_cache[cache_key]

        raw_match = self.match_fixture(home_team, away_team, comp_id)
        if not raw_match:
            return None

        parsed = self.parse_match_odds(raw_match, comp_id)
        if parsed and parsed.get("matched"):
            self._parsed_odds_cache[cache_key] = parsed
            return parsed

        return None

    def parse_match_odds(self, raw_match: Dict[str, Any], competition_id: str) -> Dict[str, Any]:
        """
        Interpreta l'estructura de quotes de MoviOdds i construeix el perfil de quotes oficials.
        """
        fi_id = str(raw_match.get("id", ""))
        home_name = raw_match.get("home", "")
        away_name = raw_match.get("away", "")
        kickoff_utc = raw_match.get("kickoff_utc")
        polled_at = raw_match.get("polled_at")

        result = {
            "source": f"Bet365 via MoviOdds (FI: {fi_id})",
            "bookmaker": "BET365",
            "url": "https://www.bet365.es/#/AS/B1/",
            "match_id": fi_id,
            "matched_title": f"{home_name} vs {away_name}",
            "matched": False,
            "is_manual_override": False,
            "provenance": "BET365_MOVIODDS",
            "cached_at": time.time(),
            "kickoff_utc": kickoff_utc,
            "polled_at": polled_at,
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

        # Agrupar quotes per nom de grup
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for q in quotes:
            # Ignorar quotes suspeses o amb preu nul/invàlid
            if q.get("suspended") is True:
                continue
            gname = q.get("group_name", "")
            groups.setdefault(gname, []).append(q)

        # 1. Mercat 1X2 (Full Time Result)
        ft_quotes = groups.get("Full Time Result", [])
        h_norm = self.normalize_name(home_name)
        a_norm = self.normalize_name(away_name)

        latest_ts = ""
        for q in ft_quotes:
            label = str(q.get("label", ""))
            price = self._safe_float(q.get("odds_dec"))
            if not price:
                continue
            q_ts = q.get("ts", "")
            if q_ts > latest_ts:
                latest_ts = q_ts

            l_norm = self.normalize_name(label)
            if l_norm == h_norm or (l_norm and l_norm in h_norm) or (h_norm and h_norm in l_norm) or label in ("1", "Home"):
                result["1X2"]["1"] = price
            elif label.lower() in ("draw", "empat", "x", "tie") or l_norm in ("draw", "empat"):
                result["1X2"]["X"] = price
            elif l_norm == a_norm or (l_norm and l_norm in a_norm) or (a_norm and a_norm in l_norm) or label in ("2", "Away"):
                result["1X2"]["2"] = price

        if latest_ts:
            result["ts"] = latest_ts

        # 2. Doble Oportunitat
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

        # 3. Gols Over / Under 2.5 (línia estricta de 2.5)
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

        # 4. Ambdós Equips Marquen (BTTS)
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

        # 5. Córners
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

        # 6. Targetes
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

        # S'aprova la correspondència si el mercat 1X2 té totes 3 opcions
        if len(result["1X2"]) == 3:
            result["matched"] = True

        return result

    @staticmethod
    def _safe_float(val: Any) -> Optional[float]:
        try:
            f = float(val)
            return round(f, 2) if f > 1.0 else None
        except (ValueError, TypeError):
            return None

    def sync_to_cache(
        self,
        target_matches: List[Dict[str, Any]],
        cache_file_path: Optional[Path] = None,
    ) -> int:
        """
        Sincronitza les quotes extretes de MoviOdds a bet365_odds_cache.json,
        assegurant que no sobreescriu overrides manuals ni injecta dades sintètiques.
        """
        file_path = cache_file_path or CACHE_FILE
        existing_cache: Dict[str, Any] = {}
        if file_path.exists():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    existing_cache = json.load(f)
            except Exception:
                existing_cache = {}

        synced_count = 0
        for m in target_matches:
            cid = m.get("competition_id", "LALIGA").upper()
            h_team = m.get("home_team", "")
            a_team = m.get("away_team", "")

            # Obtenir quotes
            odds = self.get_match_odds(h_team, a_team, cid)
            if odds and odds.get("matched"):
                h_norm = self.normalize_name(h_team)
                a_norm = self.normalize_name(a_team)
                cache_key = f"{cid}_{h_norm}_{a_norm}"

                # Mai sobreescriure un override manual
                if existing_cache.get(cache_key, {}).get("is_manual_override"):
                    continue

                existing_cache[cache_key] = odds
                synced_count += 1

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(existing_cache, f, ensure_ascii=False, indent=2)
            print(f"[MoviOddsClient] Sincronitzades {synced_count} quotes a {file_path.name}.")
        except Exception as e:
            print(f"[!] Error desant la memòria cau a {file_path}: {e}")

        return synced_count
