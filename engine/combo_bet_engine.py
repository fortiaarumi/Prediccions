"""
engine/combo_bet_engine.py
==========================
Motor d'anàlisi i generació d'apostes combinades matemàtiques a Winamax Espanya.

Calcula amb rigor científic:
1. Probabilitats conjuntes per a esdeveniments estadísticament independents:
   P_comb = P_1 * P_2 * ... * P_k
2. Cuotes combinades oficials de Winamax:
   Odd_comb = Odd_1 * Odd_2 * ... * Odd_k
3. Cuota justa teòrica del model:
   Fair_comb = 1.0 / P_comb
4. Valor esperat conjunt (+EV%):
   EV_comb = (P_comb * Odd_comb - 1.0) * 100.0

Genera dos perfils clau d'apostes combinades per jornada:
- 🛡️ Combinada Segura: Seleccions amb probabilitat individual >= 90% (o màxima seguretat disponible >= 85%)
  acumulant seleccions de partits diferents fins a assolir una cuota objectiu d'entre 2.0 i 3.0.
- 🚀 Combinada Arriscada / Cuota Alta: Combinació d'alt multiplicador (Cuota >= 30.0) optimitzant
  la probabilitat real segons el model i el valor esperat positiu (+EV%).
"""

import itertools
import re
import unicodedata
from collections import Counter
from typing import List, Dict, Any, Optional, Tuple, Set

CANONICAL_TEAM_ALIASES: Dict[str, str] = {
    # LaLiga EA Sports
    "atletico de madrid": "ATM", "atletico madrid": "ATM", "atletico": "ATM", "atleti": "ATM", "atm": "ATM",
    "real madrid": "RMA", "madrid": "RMA", "rma": "RMA",
    "fc barcelona": "FCB", "barcelona": "FCB", "barca": "FCB", "barça": "FCB", "fcb": "FCB",
    "sevilla": "SEV", "sevilla fc": "SEV", "sev": "SEV",
    "villarreal": "VIL", "villarreal cf": "VIL", "vil": "VIL",
    "athletic club": "ATH", "athletic bilbao": "ATH", "athletic": "ATH", "ath": "ATH",
    "real sociedad": "RSO", "la real": "RSO", "rso": "RSO",
    "real betis": "BET", "betis": "BET", "bet": "BET",
    "valencia": "VAL", "valencia cf": "VAL", "val": "VAL",
    "osasuna": "OSA", "ca osasuna": "OSA", "osa": "OSA",
    "celta de vigo": "CEL", "celta": "CEL", "rc celta": "CEL", "rc celta de vigo": "CEL", "cel": "CEL",
    "rayo vallecano": "RAY", "rayo": "RAY", "ray": "RAY",
    "getafe": "GET", "getafe cf": "GET", "get": "GET",
    "alaves": "ALA", "deportivo alaves": "ALA", "ala": "ALA",
    "espanyol": "ESP", "rcd espanyol": "ESP", "esp": "ESP",
    "girona": "GIR", "girona fc": "GIR", "gir": "GIR",
    "mallorca": "MLL", "rcd mallorca": "MLL", "mll": "MLL",
    "las palmas": "LAS", "ud las palmas": "LAS", "las": "LAS",
    "leganes": "LEG", "cd leganes": "LEG", "leg": "LEG",
    "valladolid": "VLL", "real valladolid": "VLL", "vll": "VLL",

    # Hypermotion
    "racing de santander": "RDS", "racing santander": "RDS", "racing": "RDS", "rds": "RDS",
    "deportivo de la coruna": "DEP", "deportivo la coruna": "DEP", "deportivo": "DEP", "depor": "DEP", "dep": "DEP",
    "malaga": "MAL", "malaga cf": "MAL", "mal": "MAL",
    "levante": "LEV", "levante ud": "LEV", "lev": "LEV",
    "elche": "ELC", "elche cf": "ELC", "elc": "ELC",
    "cadiz": "CAD", "cadiz cf": "CAD", "cad": "CAD",
    "cordoba": "COR", "cordoba cf": "COR", "cor": "COR",
    "eibar": "EIB", "sd eibar": "EIB", "eib": "EIB",
    "granada": "GRA", "granada cf": "GRA", "gra": "GRA",
    "oviedo": "ROV", "real oviedo": "ROV", "rov": "ROV",
    "sporting de gijon": "SPG", "sporting gijon": "SPG", "sporting": "SPG", "spg": "SPG",
    "tenerife": "TEN", "cd tenerife": "TEN", "ten": "TEN",
    "zaragoza": "ZAR", "real zaragoza": "ZAR", "zar": "ZAR",

    # Premier League
    "manchester city": "MCI", "man city": "MCI", "mci": "MCI",
    "manchester united": "MUN", "man utd": "MUN", "manchester utd": "MUN", "mun": "MUN",
    "arsenal": "ARS", "arsenal fc": "ARS", "ars": "ARS",
    "liverpool": "LIV", "liverpool fc": "LIV", "liv": "LIV",
    "aston villa": "AVL", "villa": "AVL", "avl": "AVL",
    "tottenham": "TOT", "tottenham hotspur": "TOT", "spurs": "TOT", "tot": "TOT",
    "chelsea": "CHE", "chelsea fc": "CHE", "che": "CHE",
    "newcastle": "NEW", "newcastle united": "NEW", "new": "NEW",
    "brighton": "BHA", "brighton and hove albion": "BHA", "bha": "BHA",
    "west ham": "WHU", "west ham united": "WHU", "whu": "WHU",
    "crystal palace": "CRY", "palace": "CRY", "cry": "CRY",
    "fulham": "FUL", "ful": "FUL",
    "brentford": "BRE", "bre": "BRE",
    "everton": "EVE", "eve": "EVE",
    "wolverhampton": "WOL", "wolves": "WOL", "wol": "WOL",

    # Bundesliga
    "bayern munich": "BAY", "bayern munchen": "BAY", "bayern": "BAY", "bay": "BAY",
    "bayer leverkusen": "B04", "leverkusen": "B04", "b04": "B04",
    "borussia dortmund": "BVB", "dortmund": "BVB", "bvb": "BVB",
    "rb leipzig": "RBL", "leipzig": "RBL", "rbl": "RBL",
    "eintracht frankfurt": "SGE", "frankfurt": "SGE", "sge": "SGE",
    "vfb stuttgart": "VFB", "stuttgart": "VFB", "vfb": "VFB",
    "vfl wolfsburg": "WOB", "wolfsburg": "WOB", "wob": "WOB",

    # Serie A
    "inter": "INT", "inter milan": "INT", "internazionale": "INT", "int": "INT",
    "milan": "ACM", "ac milan": "ACM", "acm": "ACM",
    "juventus": "JUV", "juve": "JUV", "juv": "JUV",
    "atalanta": "ATA", "ata": "ATA",
    "napoli": "NAP", "nap": "NAP",
    "roma": "ASR", "as roma": "ASR", "asr": "ASR",
    "lazio": "LAZ", "laz": "LAZ",
    "fiorentina": "FIO", "fio": "FIO",
    "bologna": "BFC", "bfc": "BFC",
    "torino": "TOR", "tor": "TOR",
    "genoa": "GEN", "gen": "GEN",

    # Ligue 1
    "paris saint-germain": "PSG", "paris saint germain": "PSG", "paris sg": "PSG", "psg": "PSG",
    "monaco": "ASM", "as monaco": "ASM", "asm": "ASM",
    "marseille": "OM", "olympique marseille": "OM", "om": "OM",
    "lyon": "OL", "olympique lyon": "OL", "ol": "OL",
    "lille": "LIL", "losc": "LIL", "lil": "LIL"
}

class ComboBetEngine:
    def __init__(self, db=None):
        self.winamax_base_url = "https://www.winamax.es/apuestas-deportivas/sports/1/32"
        self.db = db

    def calculate_combined_probability(self, probabilities: List[float]) -> float:
        """
        Calcula la probabilitat conjunta (decimal 0.0 a 1.0) d'esdeveniments independents.
        Ex: [0.90, 0.85, 0.92] -> 0.90 * 0.85 * 0.92 = 0.7038 (70.4%)
        """
        if not probabilities:
            return 0.0
        prod = 1.0
        for p in probabilities:
            p_dec = p / 100.0 if p > 1.0 else p
            prod *= max(0.0, min(1.0, p_dec))
        return prod

    def calculate_combined_odds(self, odds: List[float]) -> float:
        """Calcula la multiplicació exacta de cuotes decimals de Winamax."""
        if not odds:
            return 1.0
        prod = 1.0
        for o in odds:
            if o and o > 1.0:
                prod *= o
        return round(prod, 2)

    def calculate_winamax_booster(self, num_legs: int) -> float:
        """
        Retorna el percentatge de bonificació oficial del Combo Booster de Winamax:
        4 seleccions: +5.0%
        5 seleccions: +7.5%
        6 seleccions: +10.0%
        7 seleccions: +12.5%
        8 seleccions: +15.0%
        9 seleccions: +20.0%
        10+ seleccions: +25.0%
        """
        booster_table = {4: 5.0, 5: 7.5, 6: 10.0, 7: 12.5, 8: 15.0, 9: 20.0, 10: 25.0}
        if num_legs >= 10:
            return 25.0
        return booster_table.get(num_legs, 0.0)

    def calculate_combo_summary(self, legs: List[Dict[str, Any]], profile_name: str) -> Dict[str, Any]:
        """Calcula totes les mètriques d'una combinada a partir de les seves seleccions (legs)."""
        if not legs:
            return {
                "profile": profile_name,
                "legs": [],
                "combined_odd": 1.0,
                "combined_prob_pct": 0.0,
                "fair_odd": 99.0,
                "ev_pct": -100.0,
                "is_value": False,
                "booster_pct": 0.0,
                "boosted_odd": 1.0,
                "winamax_url": self.winamax_base_url
            }

        # Deduplicació defensiva de seleccions canòniques mantenint ordre
        unique_legs = []
        seen_keys = set()
        for leg in legs:
            k = self._get_leg_key(leg)
            if k not in seen_keys:
                seen_keys.add(k)
                unique_legs.append(leg)
        legs = unique_legs

        odds_list = [leg["bookie_odd"] for leg in legs]
        probs_list = [leg["model_prob"] for leg in legs]

        combined_odd = self.calculate_combined_odds(odds_list)
        p_dec = self.calculate_combined_probability(probs_list)
        combined_prob_pct = round(p_dec * 100.0, 1)

        fair_odd = round(1.0 / p_dec, 2) if p_dec > 0.0 else 999.0
        ev_pct = round((p_dec * combined_odd - 1.0) * 100.0, 1)

        booster_pct = self.calculate_winamax_booster(len(legs))
        boosted_odd = round(combined_odd * (1.0 + booster_pct / 100.0), 2) if booster_pct > 0 else combined_odd

        return {
            "profile": profile_name,
            "legs": legs,
            "legs_count": len(legs),
            "combined_odd": combined_odd,
            "combined_prob_pct": combined_prob_pct,
            "fair_odd": fair_odd,
            "ev_pct": ev_pct,
            "is_value": ev_pct > 0,
            "booster_pct": booster_pct,
            "boosted_odd": boosted_odd,
            "winamax_url": self.winamax_base_url
        }

    def _get_leg_key(self, leg: Dict[str, Any]) -> str:
        """Identificador únic normalitzat d'una selecció exacta (matchup + categoria + mercat)."""
        matchup = (leg.get("matchup") or "").strip().lower()
        name = (leg.get("name") or leg.get("selection_name") or "").strip().lower()
        cat = (leg.get("category") or "").strip().lower()
        return f"{matchup}::{cat}::{name}"

    def _normalize_team_token(self, name: Any) -> Optional[str]:
        """Normalitza el nom o ID d'un equip al seu codi canònic o representació unívoca."""
        if not name:
            return None
        if isinstance(name, dict):
            name = name.get("id") or name.get("short_name") or name.get("name") or ""
        s = str(name).strip().lower()
        if not s or s in ("none", "null", "local", "visitant", "equip 1", "equip 2", "partit", "partit sense nom"):
            return None

        # Normalització d'accents
        s_norm = unicodedata.normalize('NFD', s).encode('ascii', 'ignore').decode('utf-8')
        s_clean = re.sub(r'[^a-z0-9\s]', ' ', s_norm)
        s_clean = re.sub(r'\s+', ' ', s_clean).strip()

        if s_clean in CANONICAL_TEAM_ALIASES:
            return CANONICAL_TEAM_ALIASES[s_clean]
        if s_norm in CANONICAL_TEAM_ALIASES:
            return CANONICAL_TEAM_ALIASES[s_norm]

        # Si tenim db disponible i no s'ha resolt, provar db.find_team_id
        if self.db and hasattr(self.db, "find_team_id"):
            try:
                tid = self.db.find_team_id(str(name))
                if tid:
                    return tid.upper()
            except Exception:
                pass

        # Netejar prefixos i sufixos habituals
        tokens = [w for w in s_clean.split() if w not in ("fc", "cf", "cd", "sd", "rcd", "rc", "ud", "ca", "de", "del", "la", "el", "los", "las", "club", "afc", "bsc", "sc")]
        if tokens:
            candidate = " ".join(tokens)
            if candidate in CANONICAL_TEAM_ALIASES:
                return CANONICAL_TEAM_ALIASES[candidate]
            return candidate.replace(" ", "")

        return s_clean.replace(" ", "") if s_clean else None

    def _extract_match_identity(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extreu i normalitza de forma exhaustiva la identitat d'un partit:
        - IDs explícits (match_id, id, event_id, etc.)
        - Equips canònics (home_token, away_token)
        - Determina si el registre té identitat suficient per poder demostrar que és un partit distint.
        """
        explicit_ids = set()
        raw_mid = item.get("match_id") or item.get("id") or item.get("event_id")
        if raw_mid is not None and str(raw_mid).strip() not in ("", "0", "-1", "none", "null", "partit", "partit sense nom"):
            mid_str = str(raw_mid).strip().lower()
            explicit_ids.add(mid_str)

        # Buscar equips a partir dels camps estructurats
        h_token = self._normalize_team_token(item.get("home_team_id") or item.get("home_team"))
        a_token = self._normalize_team_token(item.get("away_team_id") or item.get("away_team"))

        # Si falten equips, extreure del matchup mitjançant múltiples separadors
        matchup = (item.get("matchup") or "").strip()
        if (not h_token or not a_token) and matchup:
            parts = re.split(r'\s+(?:vs\.?|v\.?|-|/|contra|@)\s+', matchup, flags=re.IGNORECASE)
            if len(parts) == 2:
                if not h_token:
                    h_token = self._normalize_team_token(parts[0])
                if not a_token:
                    a_token = self._normalize_team_token(parts[1])

        # Si encara falten, extreure codis d'equips de l'ID si segueix el patró SEASON_LEAGUE_HOM_AWA o HOM_AWA
        if (not h_token or not a_token) and raw_mid:
            m_code = re.search(r'([A-Za-z0-9]{2,5})_([A-Za-z0-9]{2,5})$', str(raw_mid).strip())
            if m_code:
                if not h_token:
                    h_token = self._normalize_team_token(m_code.group(1))
                if not a_token:
                    a_token = self._normalize_team_token(m_code.group(2))

        # Competició
        comp_id = str(item.get("competition_id") or "").strip().upper() or None

        # Data
        raw_date = item.get("date_time") or item.get("match_date") or item.get("date")
        date_str = None
        if raw_date:
            m_d = re.search(r'(\d{4}-\d{2}-\d{2})', str(raw_date))
            if m_d:
                date_str = m_d.group(1)

        # Jornada
        jornada = item.get("jornada")
        if jornada is not None:
            try:
                jornada = int(jornada)
            except (ValueError, TypeError):
                jornada = str(jornada).strip()

        # Determinar si té identitat suficient
        # Cal com a mínim: un ID explícit vàlid O una parella d'equips vàlids (h_token i a_token)
        has_sufficient = bool(explicit_ids) or bool(h_token and a_token)
        canonical_pair = (h_token, a_token) if (h_token and a_token) else None

        return {
            "explicit_ids": explicit_ids,
            "home": h_token,
            "away": a_token,
            "canonical_pair": canonical_pair,
            "competition_id": comp_id,
            "date": date_str,
            "jornada": jornada,
            "has_sufficient_identity": has_sufficient,
            "raw_matchup": matchup
        }

    def _are_same_match(self, item1: Dict[str, Any], item2: Dict[str, Any]) -> bool:
        """
        Comprova si dues seleccions o registres pertanyen al mateix partit:
        1. Si comparteixen un ID explícit -> Mateix partit.
        2. Si tenen competicions, dates o jornades provadament diferents -> Partits diferents
           (no es bloqueja combinar duels diferents entre els mateixos equips en competicions o dates diferents).
        3. Si comparteixen els mateixos equips (home, away) i no es pot demostrar que són enfrontaments diferents -> Mateix partit.
        4. Comprovació creuada d'ID que contingui els codis d'equip de l'altre partit (en el mateix context de data/competició).
        """
        id1 = self._extract_match_identity(item1)
        id2 = self._extract_match_identity(item2)

        # 1. IDs explícits compartits
        if id1["explicit_ids"] and id2["explicit_ids"]:
            if id1["explicit_ids"] & id2["explicit_ids"]:
                return True

        # 2. Comprovació de diferències provables (competició, data o jornada diferents)
        c1, c2 = id1.get("competition_id"), id2.get("competition_id")
        d1, d2 = id1.get("date"), id2.get("date")
        j1, j2 = id1.get("jornada"), id2.get("jornada")

        if c1 and c2 and c1 != c2:
            return False
        if d1 and d2 and d1 != d2:
            return False
        if c1 and c2 and c1 == c2 and j1 is not None and j2 is not None and j1 != j2:
            return False

        # 3. Parelles canòniques d'equips
        p1 = id1["canonical_pair"]
        p2 = id2["canonical_pair"]
        if p1 and p2:
            h1, a1 = p1
            h2, a2 = p2
            # Mateixos equips en la mateixa posició
            if h1 == h2 and a1 == a2:
                return True

        # 4. Comprovació creuada ID vs equips
        for mid in id1["explicit_ids"]:
            if p2:
                h2_low, a2_low = p2[0].lower(), p2[1].lower()
                if h2_low in mid and a2_low in mid:
                    return True
        for mid in id2["explicit_ids"]:
            if p1:
                h1_low, a1_low = p1[0].lower(), p1[1].lower()
                if h1_low in mid and a1_low in mid:
                    return True

        return False

    def _get_match_canonical_key(self, m: Dict[str, Any]) -> str:
        """Retorna una clau canònica del partit independentment del format (id, matchup o equips)."""
        ident = self._extract_match_identity(m)
        if ident["canonical_pair"]:
            return f"{ident['home']}::{ident['away']}"
        if ident["explicit_ids"]:
            return sorted(list(ident["explicit_ids"]))[0]
        return (m.get("matchup") or "partit_sense_nom").strip().lower()

    def has_logical_contradiction(self, legs: List[Dict[str, Any]]) -> bool:
        """
        Detecta i rebutja contradiccions lògiques entre seleccions:
        - BTTS No i marcador exacte on marquen tots dos (ex: 1-1, 2-1, 1-2, 2-2, 3-1...).
        - BTTS Sí i marcador exacte on algun equip no marca (ex: 0-0, 1-0, 0-1, 2-0, 0-2...).
        - BTTS Sí i BTTS No.
        - Over X.5 i Under Y.5 on X >= Y (ex: Over 2.5 i Under 1.5, Over 2.5 i Under 2.5).
        - Under 1.5 i marcadors amb >= 2 gols (ex: 1-1, 2-0, etc.).
        - Under 2.5 i marcadors amb >= 3 gols (ex: 2-1, 3-0, etc.).
        - Over 2.5 i marcadors amb <= 2 gols (ex: 0-0, 1-0, 1-1, etc.).
        - 1X2 contradictoris (ex: Victòria local i Victòria visitant o Empat).
        - Doble oportunitat incompatible amb 1X2 oposat (ex: 1X i 2 - Victòria visitant).
        """
        if not legs or len(legs) < 2:
            return False

        btts_yes = False
        btts_no = False
        exact_scores = []
        under_lines = []
        over_lines = []
        outcomes_1x2 = set()
        double_chances = set()

        for leg in legs:
            name = (leg.get("name") or leg.get("selection_name") or "").lower()
            cat = (leg.get("category") or "").lower()

            # BTTS
            if "btts" in cat or "ambdós marquen" in name or "ambos equipos marcan" in name or "btts" in name:
                if "no" in name:
                    btts_no = True
                elif "sí" in name or "si" in name or "yes" in name:
                    btts_yes = True

            # Marcador exacte
            if "marcador exacte" in cat or "marcador exacte" in name or "resultat exacte" in name or "resultado exacto" in name:
                m = re.search(r'(\d+)\s*[-:]\s*(\d+)', name)
                if m:
                    exact_scores.append((int(m.group(1)), int(m.group(2))))

            # Over / Under
            m_ou = re.search(r'(?:més de|over|\+)\s*(\d+(?:\.\d+)?)', name)
            if m_ou and "córner" not in name and "corner" not in name and "target" not in name and "card" not in name:
                try:
                    over_lines.append(float(m_ou.group(1)))
                except ValueError:
                    pass

            m_u = re.search(r'(?:menys de|under|-)\s*(\d+(?:\.\d+)?)', name)
            if m_u and "córner" not in name and "corner" not in name and "target" not in name and "card" not in name:
                try:
                    under_lines.append(float(m_u.group(1)))
                except ValueError:
                    pass

            # 1X2
            if "1x2" in cat or name.startswith("1 -") or name.startswith("x -") or name.startswith("2 -"):
                if name.startswith("1 -") or "victòria local" in name:
                    outcomes_1x2.add("1")
                elif name.startswith("x -") or "empat" in name:
                    outcomes_1x2.add("X")
                elif name.startswith("2 -") or "victòria visitant" in name:
                    outcomes_1x2.add("2")

            # Doble oportunitat
            if "doble oportunitat" in cat or "doble oportunidad" in cat or "double chance" in cat:
                if "1x" in name:
                    double_chances.add("1X")
                elif "x2" in name:
                    double_chances.add("X2")
                elif "12" in name:
                    double_chances.add("12")

        if btts_yes and btts_no:
            return True

        for h, a in exact_scores:
            if btts_no and (h > 0 and a > 0):
                return True
            if btts_yes and (h == 0 or a == 0):
                return True
            tot = h + a
            for u in under_lines:
                if tot >= u:
                    return True
            for o in over_lines:
                if tot <= o:
                    return True
            if outcomes_1x2:
                if h > a and any(oc in ["X", "2"] for oc in outcomes_1x2):
                    return True
                if h == a and any(oc in ["1", "2"] for oc in outcomes_1x2):
                    return True
                if h < a and any(oc in ["1", "X"] for oc in outcomes_1x2):
                    return True

        for o in over_lines:
            for u in under_lines:
                if o >= u:
                    return True

        if len(outcomes_1x2) > 1:
            return True

        if "1" in outcomes_1x2 and "X2" in double_chances:
            return True
        if "2" in outcomes_1x2 and "1X" in double_chances:
            return True
        if "X" in outcomes_1x2 and "12" in double_chances:
            return True

        return False

    def is_valid_combo_legs(self, legs: List[Dict[str, Any]], allow_duos: bool = False) -> bool:
        """
        Valida que les seleccions d'una combinada siguin estrictament vàlides per a combinades ordinàries:
        - Mínim 2 seleccions.
        - Seleccions úniques (sense seleccions repetides).
        - Cada selecció ha de provenir d'un partit DIFERENT demostrable.
        - Rebutja qualsevol combinada amb identitat insuficient (no es pot provar que siguin partits diferents).
        - Rebutja qualsevol combinada amb dues seleccions del mateix partit per a combinades ordinàries.
        - Detecta i rebutja explícitament contradiccions lògiques (com BTTS No i marcador 1-1).
        """
        if not legs or len(legs) < 2:
            return False

        leg_keys = [self._get_leg_key(l) for l in legs]
        if len(set(leg_keys)) != len(legs):
            return False

        # 1. Comprovar que cada selecció tingui identitat de partit suficient
        for l in legs:
            ident = self._extract_match_identity(l)
            if not ident["has_sufficient_identity"]:
                return False

        # 2. Exigència estricta de partits diferents (comparació creuada de totes les parelles)
        n = len(legs)
        for i in range(n):
            for j in range(i + 1, n):
                if self._are_same_match(legs[i], legs[j]):
                    return False

        # 3. Detectar i rebutjar contradiccions lògiques
        if self.has_logical_contradiction(legs):
            return False

        return True

    def _get_combo_sig(self, legs: List[Dict[str, Any]]) -> frozenset:
        """Signatura única del conjunt de seleccions d'una combinada."""
        return frozenset(self._get_leg_key(l) for l in legs)

    def _extract_all_match_markets(self, predicted_matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Extreu tots els mercats individuals disponibles per a cada partit,
        assegurant la informació del partit i l'enllaç de Winamax,
        deduplicant partits per clau canònica.
        """
        match_markets = []
        seen_matches = set()
        for p in predicted_matches:
            m_key = self._get_match_canonical_key(p)
            if m_key in seen_matches:
                continue
            seen_matches.add(m_key)
            matchup = p.get("matchup")
            if not matchup:
                h_name = p.get("home_team", {}).get("name") if isinstance(p.get("home_team"), dict) else str(p.get("home_team") or p.get("home") or "Local")
                a_name = p.get("away_team", {}).get("name") if isinstance(p.get("away_team"), dict) else str(p.get("away_team") or p.get("away") or "Visitant")
                matchup = f"{h_name} vs {a_name}"
            else:
                parts = matchup.split(" vs ", 1)
                h_name = parts[0] if len(parts) == 2 else "Local"
                a_name = parts[1] if len(parts) == 2 else "Visitant"

            comp_id = p.get("competition_id", "")
            date_str = p.get("date", "Pendent")
            odds_info = p.get("odds", {})
            match_url = odds_info.get("url") or p.get("url") or self.winamax_base_url

            bet_analysis = p.get("bet_analysis", {})
            raw_pred = p.get("raw_pred", {})
            if not bet_analysis and raw_pred:
                bet_analysis = raw_pred.get("bet_analysis", {})

            single_markets = bet_analysis.get("single_markets") or p.get("value_bets", [])
            valid_markets = []
            for m in single_markets:
                odd = m.get("bookie_odd")
                prob = m.get("model_prob")
                if odd and odd > 1.05 and prob and prob > 0:
                    item = dict(m)
                    item["matchup"] = matchup
                    item["competition_id"] = comp_id
                    item["date"] = date_str
                    item["url"] = match_url
                    valid_markets.append(item)

            # Només afegim el partit si té mercats reals publicats i verificats a Winamax
            if valid_markets:
                match_markets.append({
                    "matchup": matchup,
                    "competition_id": comp_id,
                    "date": date_str,
                    "url": match_url,
                    "markets": valid_markets
                })

        return match_markets

    def _prepare_balanced_pools(
        self,
        match_markets: List[Dict[str, Any]],
        is_multi: bool,
        min_prob: float,
        min_odd: float,
        max_per_comp: int = 5,
        forbidden_exact: Optional[Set[str]] = None,
        counts: Optional[Dict[str, int]] = None,
        max_usage: int = 2
    ) -> List[Dict[str, Any]]:
        """
        Prepara el pool de partits seleccionant mercats complementaris de diferents categories
        (Doble Oportunitat, 1X2, Gols, Córners, Targetes, BTTS) per garantir varietat,
        filtrant seleccions prohibides o que ja hagin assolit el límit d'ús (màxim 2 combinades).
        """
        forbidden_exact = forbidden_exact or set()
        counts = counts or {}

        def extract_diverse_cands(mm):
            valid = []
            for m in mm.get("markets", []):
                k = self._get_leg_key(m)
                if k in forbidden_exact:
                    continue
                if counts.get(k, 0) >= max_usage:
                    continue
                if m["model_prob"] >= min_prob and m["bookie_odd"] >= min_odd:
                    valid.append(m)

            if not valid:
                for m in mm.get("markets", []):
                    k = self._get_leg_key(m)
                    if k in forbidden_exact or counts.get(k, 0) >= max_usage:
                        continue
                    if m["model_prob"] >= (min_prob * 0.70) and m["bookie_odd"] >= 1.05:
                        valid.append(m)

            if not valid:
                return []

            # Agrupar el millor mercat per a cada categoria per permetre triar Córners, Targetes, Doble Oportunitat i Gols
            by_category = {}
            for m in valid:
                cat = m.get("category", "Altres")
                if cat not in by_category or m["model_prob"] > by_category[cat]["model_prob"]:
                    by_category[cat] = m
            sorted_cands = sorted(by_category.values(), key=lambda x: x["model_prob"], reverse=True)
            return sorted_cands[:4]

        pools = []
        seen_pool_matchups = set()
        if is_multi:
            by_comp = {}
            for mm in match_markets:
                c_id = mm.get("competition_id", "OTHER")
                by_comp.setdefault(c_id, []).append(mm)

            # Intercalar partits de manera round-robin (LaLiga #1, Premier #1, Hypermotion #1, LaLiga #2...)
            for i in range(max_per_comp):
                for c_id, comp_mms in by_comp.items():
                    if i < len(comp_mms):
                        m_up = (comp_mms[i].get("matchup") or "").strip().lower()
                        if m_up in seen_pool_matchups:
                            continue
                        cands = extract_diverse_cands(comp_mms[i])
                        if cands:
                            seen_pool_matchups.add(m_up)
                            pools.append({
                                "matchup": comp_mms[i]["matchup"],
                                "competition_id": c_id,
                                "markets": cands
                            })
        else:
            for mm in match_markets:
                m_up = (mm.get("matchup") or "").strip().lower()
                if m_up in seen_pool_matchups:
                    continue
                cands = extract_diverse_cands(mm)
                if cands:
                    seen_pool_matchups.add(m_up)
                    pools.append({
                        "matchup": mm["matchup"],
                        "competition_id": mm.get("competition_id", ""),
                        "markets": cands
                    })
        return pools

    def _build_match_choices(
        self,
        markets: List[Dict[str, Any]],
        allow_duos: bool = False,
        max_singles: int = 4,
        max_duos: int = 3
    ) -> List[List[Dict[str, Any]]]:
        """
        Construeix les opcions seleccionables per a un partit.
        Per a combinades ordinàries, cada partit aporta exclusivament seleccions individuals
        (màxim 1 selecció per partit per evitar dependències no modelades i quotes fictícies).
        """
        return [[m] for m in markets[:max_singles]]

    def _pick_diverse_c2(
        self,
        candidates: List[Dict[str, Any]],
        c1_cand: Dict[str, Any],
        pools: List[Dict[str, Any]],
        temp_counts: Dict[str, int],
        max_usage: int = 2,
        forbidden_exact: Optional[Set[str]] = None,
        max_overlap_ratio: float = 0.5,
        target_k: int = 2
    ) -> Dict[str, Any]:
        """
        Selecciona un candidat c2 que sigui significativament diferent de c1:
        - Rebutja candidats amb la mateixa signatura de combinada.
        - Limita l'encavalcament de partits (matchups) a un màxim de la meitat dels partits (o 0/1 si k<=2).
        - Penalitza fortament l'encavalcament i dóna prioritat a combinacions de partits i mercats nous.
        - Mai clona c1_cand. Si cap candidat existent compleix el llindar estricte, tria el de menor
          encavalcament o construeix una alternativa a partir dels partits no utilitzats del pool.
        """
        forbidden_exact = forbidden_exact or set()
        c1_legs = c1_cand.get("legs", [])
        c1_matchups = c1_cand.get("matchups", set())
        c1_sig = c1_cand.get("sig", frozenset())
        c1_leg_keys = {self._get_leg_key(l) for l in c1_legs}

        k_len = max(len(c1_legs), target_k)
        if k_len <= 2:
            max_allowed_overlap = 0 if len(pools) >= 4 else 1
        elif k_len <= 4:
            max_allowed_overlap = 1
        else:
            max_allowed_overlap = max(1, int(k_len * max_overlap_ratio))

        scored_cands = []
        for cand in candidates:
            if cand.get("sig") == c1_sig:
                continue
            cand_legs = cand.get("legs", [])
            if any(temp_counts.get(self._get_leg_key(l), 0) >= max_usage for l in cand_legs):
                continue
            if any(self._get_leg_key(l) in forbidden_exact for l in cand_legs):
                continue

            cand_matchups = cand.get("matchups", set())
            overlap_matches = len(cand_matchups & c1_matchups)
            overlap_exact_legs = sum(1 for l in cand_legs if self._get_leg_key(l) in c1_leg_keys)

            penalty = (overlap_matches * 0.15) + (overlap_exact_legs * 0.20)
            adjusted_score = cand["score"] - penalty

            scored_cands.append({
                "cand": cand,
                "overlap_matches": overlap_matches,
                "overlap_exact_legs": overlap_exact_legs,
                "adj_score": adjusted_score,
                "is_within_limit": overlap_matches <= max_allowed_overlap
            })

        # Fase 1: Candidats que respecten el màxim d'encavalcament permès
        within_limit = [x for x in scored_cands if x["is_within_limit"]]
        if within_limit:
            within_limit.sort(key=lambda x: (x["overlap_matches"], -x["adj_score"]))
            return within_limit[0]["cand"]

        # Fase 2: Si cap no compleix el màxim estricte, agafar el que tingui MENOR encavalcament possible
        if scored_cands:
            scored_cands.sort(key=lambda x: (x["overlap_matches"], x["overlap_exact_legs"], -x["adj_score"]))
            return scored_cands[0]["cand"]

        # Fase 3: Relaxar temporalment temp_counts si era per saturació de quotes
        for cand in candidates:
            if cand.get("sig") == c1_sig:
                continue
            cand_legs = cand.get("legs", [])
            overlap_exact_legs = sum(1 for l in cand_legs if self._get_leg_key(l) in c1_leg_keys)
            if overlap_exact_legs < len(c1_legs):
                return cand

        # Fase 4: Construir combinada a partir dels partits del pool no utilitzats a c1
        alt_legs = []
        seen_alt_matchups = set()
        for p in pools:
            p_up = (p.get("matchup") or "").strip().lower()
            if p["matchup"] not in c1_matchups and p_up not in seen_alt_matchups:
                for m in p["markets"]:
                    k = self._get_leg_key(m)
                    if k not in forbidden_exact and k not in c1_leg_keys:
                        alt_legs.append(m)
                        seen_alt_matchups.add(p_up)
                        break
                if len(alt_legs) >= k_len:
                    break

        if len(alt_legs) >= 2 and self.is_valid_combo_legs(alt_legs, allow_duos=False):
            return {"legs": alt_legs, "sig": self._get_combo_sig(alt_legs)}

        return {"legs": [], "sig": frozenset()}

    def find_pair_safe(
        self,
        match_markets: List[Dict[str, Any]],
        is_multi: bool = False,
        global_counts: Optional[Dict[str, int]] = None,
        forbidden_exact: Optional[Set[str]] = None,
        max_usage: int = 2,
        allow_duos: bool = False
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Genera dues combinades segures (cuota 1.95 - 3.50) optimitzant la màxima probabilitat conjunta."""
        prefix = "Mega-Combinada Multi-Lliga" if is_multi else "Combinada"
        global_counts = global_counts if global_counts is not None else Counter()
        forbidden_exact = forbidden_exact or set()

        pools = self._prepare_balanced_pools(
            match_markets,
            is_multi=is_multi,
            min_prob=60.0,
            min_odd=1.07,
            max_per_comp=4,
            forbidden_exact=forbidden_exact,
            counts=global_counts,
            max_usage=max_usage
        )

        if len(pools) < 2:
            return self.calculate_combo_summary([], f"{prefix} Segura #1 (Cuota 2-3)"), self.calculate_combo_summary([], f"{prefix} Segura #2 (Cuota 2-3 Alternativa)")

        candidates = []
        n_pools = min(len(pools), 14)
        for k in range(2, min(n_pools, 4) + 1):
            for match_combo in itertools.combinations(pools[:n_pools], k):
                comps = {s["competition_id"] for s in match_combo if s.get("competition_id")}
                if is_multi and len(comps) < 2:
                    continue
                league_bonus = 0.06 if is_multi and len(comps) >= 3 else (0.02 if is_multi and len(comps) >= 2 else 0.0)

                cand_lists = [self._build_match_choices(m["markets"], allow_duos=allow_duos, max_singles=3, max_duos=2) for m in match_combo]
                for choice in itertools.product(*cand_lists):
                    leg_choice = [leg for item in choice for leg in (item if isinstance(item, list) else [item])]
                    if not self.is_valid_combo_legs(leg_choice, allow_duos=allow_duos):
                        continue
                    if any(self._get_leg_key(l) in forbidden_exact or global_counts.get(self._get_leg_key(l), 0) >= max_usage for l in leg_choice):
                        continue
                    odds = [l["bookie_odd"] for l in leg_choice]
                    c_odd = round(self.calculate_combined_odds(odds), 2)
                    if 1.90 <= c_odd <= 3.60:
                        cats = {l.get("category") for l in leg_choice}
                        p_dec = self.calculate_combined_probability([l["model_prob"] for l in leg_choice])
                        non_win = cats - {"1X2", "Doble Oportunitat"}
                        div_bonus = 0.04 if non_win else 0.0
                        score = p_dec * (1.0 + div_bonus + league_bonus) - abs(c_odd - 2.25) * 0.01
                        candidates.append({
                            "legs": list(leg_choice),
                            "c_odd": c_odd,
                            "p_dec": p_dec,
                            "score": score,
                            "matchups": {l["matchup"] for l in leg_choice},
                            "sig": self._get_combo_sig(leg_choice)
                        })
                        if len(candidates) >= 100:
                            break
                if len(candidates) >= 100:
                    break
            if len(candidates) >= 100:
                break

        if not candidates:
            for k in range(2, min(n_pools, 5) + 1):
                for match_combo in itertools.combinations(pools[:n_pools], k):
                    comps = {s["competition_id"] for s in match_combo if s.get("competition_id")}
                    if is_multi and len(comps) < 2 and len(set(m.get("competition_id") for m in pools if m.get("competition_id"))) >= 2:
                        continue
                    cand_lists = [self._build_match_choices(m["markets"], allow_duos=allow_duos, max_singles=3, max_duos=2) for m in match_combo]
                    for choice in itertools.product(*cand_lists):
                        leg_choice = [leg for item in choice for leg in (item if isinstance(item, list) else [item])]
                        if not self.is_valid_combo_legs(leg_choice, allow_duos=allow_duos):
                            continue
                        if any(self._get_leg_key(l) in forbidden_exact or global_counts.get(self._get_leg_key(l), 0) >= max_usage for l in leg_choice):
                            continue
                        odds = [l["bookie_odd"] for l in leg_choice]
                        c_odd = round(self.calculate_combined_odds(odds), 2)
                        if c_odd >= 1.70:
                            p_dec = self.calculate_combined_probability([l["model_prob"] for l in leg_choice])
                            candidates.append({
                                "legs": list(leg_choice), "c_odd": c_odd, "p_dec": p_dec,
                                "score": p_dec - abs(c_odd - 2.2) * 0.01,
                                "matchups": {l["matchup"] for l in leg_choice},
                                "sig": self._get_combo_sig(leg_choice)
                            })
                            if len(candidates) >= 100:
                                break
                    if len(candidates) >= 100:
                        break
                if len(candidates) >= 100:
                    break

        candidates.sort(key=lambda x: x["score"], reverse=True)
        c1_cand = candidates[0] if candidates else {"legs": [], "sig": frozenset()}

        # Recompte temporal per a c2
        temp_counts = dict(global_counts)
        for l in c1_cand.get("legs", []):
            k = self._get_leg_key(l)
            temp_counts[k] = temp_counts.get(k, 0) + 1

        c2_cand = self._pick_diverse_c2(
            candidates=candidates,
            c1_cand=c1_cand,
            pools=pools,
            temp_counts=temp_counts,
            max_usage=max_usage,
            forbidden_exact=forbidden_exact,
            max_overlap_ratio=0.5,
            target_k=2
        )

        # Actualitzar el registre global d'usos
        for l in c1_cand.get("legs", []):
            global_counts[self._get_leg_key(l)] = global_counts.get(self._get_leg_key(l), 0) + 1
        for l in c2_cand.get("legs", []):
            global_counts[self._get_leg_key(l)] = global_counts.get(self._get_leg_key(l), 0) + 1

        c1 = self.calculate_combo_summary(c1_cand.get("legs", []), f"{prefix} Segura #1 (Cuota 2-3)")
        c2 = self.calculate_combo_summary(c2_cand.get("legs", []), f"{prefix} Segura #2 (Cuota 2-3 Alternativa)")
        c1["category_code"] = "SAFE"
        c2["category_code"] = "SAFE"
        c1["recommended_stake"] = 25.0
        c2["recommended_stake"] = 25.0
        return c1, c2

    def find_pair_semi(
        self,
        match_markets: List[Dict[str, Any]],
        is_multi: bool = False,
        global_counts: Optional[Dict[str, int]] = None,
        forbidden_exact: Optional[Set[str]] = None,
        max_usage: int = 2,
        allow_duos: bool = False
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Genera dues combinades semi-arriscades (cuota ~8.5 - 16.5) optimitzant probabilitat i varietat."""
        prefix = "Mega-Combinada Multi-Lliga" if is_multi else "Combinada"
        global_counts = global_counts if global_counts is not None else Counter()
        forbidden_exact = forbidden_exact or set()

        pools = self._prepare_balanced_pools(
            match_markets,
            is_multi=is_multi,
            min_prob=45.0,
            min_odd=1.15,
            max_per_comp=4,
            forbidden_exact=forbidden_exact,
            counts=global_counts,
            max_usage=max_usage
        )

        if len(pools) < 2:
            return self.calculate_combo_summary([], f"{prefix} Semi-Arriscada #1 (Cuota ~10.0)"), self.calculate_combo_summary([], f"{prefix} Semi-Arriscada #2 (Cuota ~10.0 Alternativa)")

        candidates = []
        n_pools = min(len(pools), 14)
        for k in range(2, min(n_pools, 6) + 1):
            for match_combo in itertools.combinations(pools[:n_pools], k):
                comps = {s["competition_id"] for s in match_combo if s.get("competition_id")}
                if is_multi and len(comps) < 2 and len(set(m.get("competition_id") for m in pools if m.get("competition_id"))) >= 2:
                    continue
                league_bonus = 0.08 if is_multi and len(comps) >= 3 else (0.03 if is_multi and len(comps) >= 2 else 0.0)

                cand_lists = [self._build_match_choices(m["markets"], allow_duos=allow_duos, max_singles=3, max_duos=2) for m in match_combo]
                for choice in itertools.product(*cand_lists):
                    leg_choice = [leg for item in choice for leg in (item if isinstance(item, list) else [item])]
                    if not self.is_valid_combo_legs(leg_choice, allow_duos=allow_duos):
                        continue
                    if any(self._get_leg_key(l) in forbidden_exact or global_counts.get(self._get_leg_key(l), 0) >= max_usage for l in leg_choice):
                        continue
                    odds = [l["bookie_odd"] for l in leg_choice]
                    c_odd = round(self.calculate_combined_odds(odds), 2)
                    if 8.50 <= c_odd <= 16.50:
                        cats = {l.get("category") for l in leg_choice}
                        p_dec = self.calculate_combined_probability([l["model_prob"] for l in leg_choice])

                        div_bonus = 0.0
                        if "Córners" in cats: div_bonus += 0.05
                        if "Targetes" in cats: div_bonus += 0.05
                        if "BTTS" in cats: div_bonus += 0.04
                        if "Gols" in cats: div_bonus += 0.03

                        fresh_bonus = sum(0.04 for l in leg_choice if global_counts.get(self._get_leg_key(l), 0) == 0)
                        score = p_dec * (1.0 + div_bonus + league_bonus + fresh_bonus) - abs(c_odd - 10.0) * 0.002
                        candidates.append({
                            "legs": list(leg_choice),
                            "c_odd": c_odd,
                            "p_dec": p_dec,
                            "score": score,
                            "matchups": {l["matchup"] for l in leg_choice},
                            "sig": self._get_combo_sig(leg_choice)
                        })
                        if len(candidates) >= 100:
                            break
                if len(candidates) >= 100:
                    break
            if len(candidates) >= 100:
                break

        if not candidates:
            for k in range(2, min(n_pools, 6) + 1):
                for match_combo in itertools.combinations(pools[:n_pools], k):
                    comps = {s["competition_id"] for s in match_combo if s.get("competition_id")}
                    if is_multi and len(comps) < 2 and len(set(m.get("competition_id") for m in pools if m.get("competition_id"))) >= 2:
                        continue
                    cand_lists = [self._build_match_choices(m["markets"], allow_duos=allow_duos, max_singles=3, max_duos=2) for m in match_combo]
                    for choice in itertools.product(*cand_lists):
                        leg_choice = [leg for item in choice for leg in (item if isinstance(item, list) else [item])]
                        if not self.is_valid_combo_legs(leg_choice, allow_duos=allow_duos):
                            continue
                        if any(self._get_leg_key(l) in forbidden_exact or global_counts.get(self._get_leg_key(l), 0) >= max_usage for l in leg_choice):
                            continue
                        odds = [l["bookie_odd"] for l in leg_choice]
                        c_odd = round(self.calculate_combined_odds(odds), 2)
                        if c_odd >= 6.0:
                            p_dec = self.calculate_combined_probability([l["model_prob"] for l in leg_choice])
                            candidates.append({
                                "legs": list(leg_choice), "c_odd": c_odd, "p_dec": p_dec,
                                "score": p_dec - abs(c_odd - 10.0) * 0.003,
                                "matchups": {l["matchup"] for l in leg_choice},
                                "sig": self._get_combo_sig(leg_choice)
                            })
                            if len(candidates) >= 100:
                                break
                    if len(candidates) >= 100:
                        break
                if len(candidates) >= 100:
                    break

        candidates.sort(key=lambda x: x["score"], reverse=True)
        if candidates:
            c1_cand = candidates[0]
        else:
            c1_legs = []
            for p in pools:
                for m in p["markets"]:
                    k = self._get_leg_key(m)
                    if k not in forbidden_exact and global_counts.get(k, 0) < max_usage:
                        c1_legs.append(m)
                        break
                if len(c1_legs) >= 3:
                    break
            if len(c1_legs) >= 2 and self.is_valid_combo_legs(c1_legs, allow_duos=False):
                c1_cand = {"legs": c1_legs, "matchups": {l["matchup"] for l in c1_legs}, "sig": self._get_combo_sig(c1_legs), "score": 0.0}
            else:
                c1_cand = {"legs": [], "matchups": set(), "sig": frozenset(), "score": 0.0}

        temp_counts = dict(global_counts)
        for l in c1_cand.get("legs", []):
            k = self._get_leg_key(l)
            temp_counts[k] = temp_counts.get(k, 0) + 1

        c2_cand = self._pick_diverse_c2(
            candidates=candidates,
            c1_cand=c1_cand,
            pools=pools,
            temp_counts=temp_counts,
            max_usage=max_usage,
            forbidden_exact=forbidden_exact,
            max_overlap_ratio=0.5,
            target_k=3
        ) if (candidates or c1_cand.get("legs")) else {"legs": [], "sig": frozenset()}

        for l in c1_cand.get("legs", []):
            global_counts[self._get_leg_key(l)] = global_counts.get(self._get_leg_key(l), 0) + 1
        for l in c2_cand.get("legs", []):
            global_counts[self._get_leg_key(l)] = global_counts.get(self._get_leg_key(l), 0) + 1

        c1 = self.calculate_combo_summary(c1_cand.get("legs", []), f"{prefix} Semi-Arriscada #1 (Cuota ~10.0)")
        c2 = self.calculate_combo_summary(c2_cand.get("legs", []), f"{prefix} Semi-Arriscada #2 (Cuota ~10.0 Alternativa)")
        c1["category_code"] = "SEMI"
        c2["category_code"] = "SEMI"
        c1["recommended_stake"] = 10.0
        c2["recommended_stake"] = 10.0
        return c1, c2

    def find_pair_risky(
        self,
        match_markets: List[Dict[str, Any]],
        is_multi: bool = False,
        global_counts: Optional[Dict[str, int]] = None,
        forbidden_exact: Optional[Set[str]] = None,
        max_usage: int = 2,
        allow_duos: bool = False
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Genera dues combinades arriscades (cuota >= 28.0) multimerkat amb activació de Combo Booster Winamax."""
        prefix = "Mega-Combinada Multi-Lliga" if is_multi else "Combinada"
        global_counts = global_counts if global_counts is not None else Counter()
        forbidden_exact = forbidden_exact or set()

        pools = self._prepare_balanced_pools(
            match_markets,
            is_multi=is_multi,
            min_prob=20.0,
            min_odd=1.20,
            max_per_comp=4,
            forbidden_exact=forbidden_exact,
            counts=global_counts,
            max_usage=max_usage
        )

        if len(pools) < 2:
            return self.calculate_combo_summary([], f"{prefix} Arriscada #1 (Cuota >= 30.0)"), self.calculate_combo_summary([], f"{prefix} Arriscada #2 (Cuota >= 30.0 Alternativa)")

        candidates = []
        n_pools = min(len(pools), 14)
        for k in range(3, min(n_pools, 8) + 1):
            for match_combo in itertools.combinations(pools[:n_pools], k):
                comps = {s["competition_id"] for s in match_combo if s.get("competition_id")}
                if is_multi and len(comps) < 2 and len(set(m.get("competition_id") for m in pools if m.get("competition_id"))) >= 2:
                    continue
                league_bonus = 0.10 if is_multi and len(comps) >= 3 else (0.04 if is_multi and len(comps) >= 2 else 0.0)

                cand_lists = [self._build_match_choices(m["markets"], allow_duos=allow_duos, max_singles=3, max_duos=3) for m in match_combo]
                for choice in itertools.product(*cand_lists):
                    leg_choice = [leg for item in choice for leg in (item if isinstance(item, list) else [item])]
                    if not self.is_valid_combo_legs(leg_choice, allow_duos=allow_duos):
                        continue
                    if any(self._get_leg_key(l) in forbidden_exact or global_counts.get(self._get_leg_key(l), 0) >= max_usage for l in leg_choice):
                        continue
                    odds = [l["bookie_odd"] for l in leg_choice]
                    c_odd = round(self.calculate_combined_odds(odds), 2)
                    if 28.0 <= c_odd <= 65.0:
                        cats = {l.get("category") for l in leg_choice}
                        p_dec = self.calculate_combined_probability([l["model_prob"] for l in leg_choice])

                        div_bonus = 0.0
                        if "Córners" in cats: div_bonus += 0.08
                        if "Targetes" in cats: div_bonus += 0.08
                        if "BTTS" in cats: div_bonus += 0.06
                        if "Gols" in cats: div_bonus += 0.05

                        fresh_bonus = sum(0.05 for l in leg_choice if global_counts.get(self._get_leg_key(l), 0) == 0)
                        score = p_dec * (1.0 + div_bonus + league_bonus + fresh_bonus) - abs(c_odd - 35.0) * 0.0005
                        candidates.append({
                            "legs": list(leg_choice),
                            "c_odd": c_odd,
                            "p_dec": p_dec,
                            "score": score,
                            "matchups": {l["matchup"] for l in leg_choice},
                            "sig": self._get_combo_sig(leg_choice)
                        })
                        if len(candidates) >= 100:
                            break
                if len(candidates) >= 100:
                    break
            if len(candidates) >= 100:
                break

        if not candidates:
            # Fallback 1: si no troba en rang estricte, buscar >= 28.0
            for k in range(3, min(n_pools, 8) + 1):
                for match_combo in itertools.combinations(pools[:n_pools], k):
                    comps = {s["competition_id"] for s in match_combo if s.get("competition_id")}
                    if is_multi and len(comps) < 2 and len(set(m.get("competition_id") for m in pools if m.get("competition_id"))) >= 2:
                        continue
                    cand_lists = [self._build_match_choices(m["markets"], allow_duos=allow_duos, max_singles=3, max_duos=3) for m in match_combo]
                    for choice in itertools.product(*cand_lists):
                        leg_choice = [leg for item in choice for leg in (item if isinstance(item, list) else [item])]
                        if not self.is_valid_combo_legs(leg_choice, allow_duos=allow_duos):
                            continue
                        if any(self._get_leg_key(l) in forbidden_exact or global_counts.get(self._get_leg_key(l), 0) >= max_usage for l in leg_choice):
                            continue
                        odds = [l["bookie_odd"] for l in leg_choice]
                        c_odd = round(self.calculate_combined_odds(odds), 2)
                        if c_odd >= 28.0:
                            p_dec = self.calculate_combined_probability([l["model_prob"] for l in leg_choice])
                            candidates.append({
                                "legs": list(leg_choice), "c_odd": c_odd, "p_dec": p_dec,
                                "score": p_dec, "matchups": {l["matchup"] for l in leg_choice},
                                "sig": self._get_combo_sig(leg_choice)
                            })
                            if len(candidates) >= 100:
                                break
                    if len(candidates) >= 100:
                        break
                if len(candidates) >= 100:
                    break

        if not candidates:
            # Fallback 2: si cap combinació assoleix 28.0, permetre >= 22.0
            for k in range(3, min(n_pools, 8) + 1):
                for match_combo in itertools.combinations(pools[:n_pools], k):
                    comps = {s["competition_id"] for s in match_combo if s.get("competition_id")}
                    if is_multi and len(comps) < 2 and len(set(m.get("competition_id") for m in pools if m.get("competition_id"))) >= 2:
                        continue
                    cand_lists = [self._build_match_choices(m["markets"], allow_duos=allow_duos, max_singles=3, max_duos=3) for m in match_combo]
                    for choice in itertools.product(*cand_lists):
                        leg_choice = [leg for item in choice for leg in (item if isinstance(item, list) else [item])]
                        if not self.is_valid_combo_legs(leg_choice, allow_duos=allow_duos):
                            continue
                        if any(self._get_leg_key(l) in forbidden_exact or global_counts.get(self._get_leg_key(l), 0) >= max_usage for l in leg_choice):
                            continue
                        odds = [l["bookie_odd"] for l in leg_choice]
                        c_odd = round(self.calculate_combined_odds(odds), 2)
                        if c_odd >= 22.0:
                            p_dec = self.calculate_combined_probability([l["model_prob"] for l in leg_choice])
                            candidates.append({
                                "legs": list(leg_choice), "c_odd": c_odd, "p_dec": p_dec,
                                "score": p_dec, "matchups": {l["matchup"] for l in leg_choice},
                                "sig": self._get_combo_sig(leg_choice)
                            })
                            if len(candidates) >= 100:
                                break
                    if len(candidates) >= 100:
                        break
                if len(candidates) >= 100:
                    break

        candidates.sort(key=lambda x: x["score"], reverse=True)
        if candidates:
            c1_cand = candidates[0]
        else:
            c1_legs = []
            for p in pools:
                for m in p["markets"]:
                    k = self._get_leg_key(m)
                    if k not in forbidden_exact and global_counts.get(k, 0) < max_usage:
                        c1_legs.append(m)
                        break
                if len(c1_legs) >= 4:
                    break
            if len(c1_legs) >= 2 and self.is_valid_combo_legs(c1_legs, allow_duos=False):
                c1_cand = {"legs": c1_legs, "matchups": {l["matchup"] for l in c1_legs}, "sig": self._get_combo_sig(c1_legs), "score": 0.0}
            else:
                c1_cand = {"legs": [], "matchups": set(), "sig": frozenset(), "score": 0.0}

        temp_counts = dict(global_counts)
        for l in c1_cand.get("legs", []):
            k = self._get_leg_key(l)
            temp_counts[k] = temp_counts.get(k, 0) + 1

        c2_cand = self._pick_diverse_c2(
            candidates=candidates,
            c1_cand=c1_cand,
            pools=pools,
            temp_counts=temp_counts,
            max_usage=max_usage,
            forbidden_exact=forbidden_exact,
            max_overlap_ratio=0.5,
            target_k=5
        ) if (candidates or c1_cand.get("legs")) else {"legs": [], "sig": frozenset()}

        for l in c1_cand.get("legs", []):
            global_counts[self._get_leg_key(l)] = global_counts.get(self._get_leg_key(l), 0) + 1
        for l in c2_cand.get("legs", []):
            global_counts[self._get_leg_key(l)] = global_counts.get(self._get_leg_key(l), 0) + 1

        c1 = self.calculate_combo_summary(c1_cand.get("legs", []), f"{prefix} Arriscada #1 (Cuota >= 30.0)")
        c2 = self.calculate_combo_summary(c2_cand.get("legs", []), f"{prefix} Arriscada #2 (Cuota >= 30.0 Alternativa)")
        c1["category_code"] = "RISKY"
        c2["category_code"] = "RISKY"
        c1["recommended_stake"] = 5.0
        c2["recommended_stake"] = 5.0
        return c1, c2

    def find_safe_combination(self, predicted_matches: List[Dict[str, Any]], target_odd_min: float = 2.0, target_odd_max: float = 3.0) -> Dict[str, Any]:
        """Troba la combinada segura (compatibilitat)."""
        mms = self._extract_all_match_markets(predicted_matches)
        c1, _ = self.find_pair_safe(mms, is_multi=False)
        return c1

    def find_semi_combination(self, predicted_matches: List[Dict[str, Any]], **kwargs) -> Dict[str, Any]:
        """Troba la combinada semi-arriscada (compatibilitat)."""
        mms = self._extract_all_match_markets(predicted_matches)
        c1, _ = self.find_pair_semi(mms, is_multi=False)
        return c1

    def find_high_odd_combination(self, predicted_matches: List[Dict[str, Any]], **kwargs) -> Dict[str, Any]:
        """Troba la combinada arriscada (compatibilitat)."""
        mms = self._extract_all_match_markets(predicted_matches)
        c1, _ = self.find_pair_risky(mms, is_multi=False)
        return c1

    def _extract_single_match_markets(self, match: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Extreu i prepara tots els mercats individuals i multimerkat d'un ÚNIC PARTIT,
        incloent 1X2, Doble Oportunitat, Gols, BTTS, Targetes, Córners i Marcadors Exactes.
        """
        matchup = match.get("matchup") or ""
        comp_id = match.get("competition_id", "")
        date_str = match.get("date", "")
        url = match.get("url") or match.get("winamax_url") or self.winamax_base_url

        db = getattr(self, "db", None)
        if db is None:
            try:
                from database.db_manager import DatabaseManager
                db = DatabaseManager()
            except Exception:
                db = None

        if " vs " in matchup and matchup.strip().lower() not in ["partit vs partit", "partit", "partit sense nom"]:
            parts = matchup.split(" vs ", 1)
            h_name = parts[0].strip()
            a_name = parts[1].strip()
        else:
            h_name = match.get("home_team", {}).get("name") if isinstance(match.get("home_team"), dict) else str(match.get("home_team") or match.get("home_name") or "")
            a_name = match.get("away_team", {}).get("name") if isinstance(match.get("away_team"), dict) else str(match.get("away_team") or match.get("away_name") or "")
            if not h_name and match.get("home_team_id") and db:
                try:
                    h_team = db.get_team(match.get("home_team_id"))
                    if h_team:
                        h_name = h_team.get("name", match.get("home_team_id"))
                except Exception:
                    pass
            if not a_name and match.get("away_team_id") and db:
                try:
                    a_team = db.get_team(match.get("away_team_id"))
                    if a_team:
                        a_name = a_team.get("name", match.get("away_team_id"))
                except Exception:
                    pass
            h_name = h_name or "Local"
            a_name = a_name or "Visitant"
            if h_name != "Local" and a_name != "Visitant":
                matchup = f"{h_name} vs {a_name}"
            else:
                matchup = matchup if (matchup and matchup.strip().lower() not in ["partit", "partit sense nom"]) else "Partit"

        h_id = match.get("home_team_id") or (db.find_team_id(h_name) if db else None)
        a_id = match.get("away_team_id") or (db.find_team_id(a_name) if db else None)

        raw_pred = match.get("raw_pred", match)
        bet_analysis = match.get("bet_analysis") or raw_pred.get("bet_analysis", {})
        existing_single = bet_analysis.get("single_markets") or match.get("value_bets", [])

        markets = []
        seen_keys = set()
        for sm in existing_single:
            odd = sm.get("bookie_odd")
            prob = sm.get("model_prob")
            if odd and odd > 1.05 and prob and prob > 0:
                item = dict(sm)
                item["matchup"] = matchup
                item["competition_id"] = comp_id
                item["date"] = date_str
                item["url"] = url
                item["home_team_id"] = h_id
                item["away_team_id"] = a_id
                k = self._get_leg_key(item)
                if k not in seen_keys:
                    seen_keys.add(k)
                    markets.append(item)

        goals = raw_pred.get("goals", {})
        cards = raw_pred.get("cards", {})
        corners = raw_pred.get("corners", {})

        p_1x2 = goals.get("prob_1X2", {})
        p1 = float(p_1x2.get("1", 45.0))
        px = float(p_1x2.get("X", 25.0))
        p2 = float(p_1x2.get("2", 30.0))

        # Doble Oportunitat
        p_1x = round(p1 + px, 1)
        odd_1x = round(max(1.10, (100.0 / p_1x) * 0.93), 2)
        item_1x = {"matchup": matchup, "name": f"1X - {h_name} o Empat", "category": "Doble Oportunitat", "bookie_odd": odd_1x, "model_prob": p_1x, "competition_id": comp_id, "date": date_str, "url": url}
        if self._get_leg_key(item_1x) not in seen_keys:
            markets.append(item_1x)
            seen_keys.add(self._get_leg_key(item_1x))

        p_x2 = round(p2 + px, 1)
        odd_x2 = round(max(1.10, (100.0 / p_x2) * 0.93), 2)
        item_x2 = {"matchup": matchup, "name": f"X2 - Empat o {a_name}", "category": "Doble Oportunitat", "bookie_odd": odd_x2, "model_prob": p_x2, "competition_id": comp_id, "date": date_str, "url": url}
        if self._get_leg_key(item_x2) not in seen_keys:
            markets.append(item_x2)
            seen_keys.add(self._get_leg_key(item_x2))

        # 1X2 Directe
        fav_name = h_name if p1 >= p2 else a_name
        fav_prob = max(p1, p2)
        odd_fav = round(max(1.20, (100.0 / fav_prob) * 0.92), 2)
        fav_prefix = "1 - Victòria" if p1 >= p2 else "2 - Victòria"
        item_fav = {"matchup": matchup, "name": f"{fav_prefix} {fav_name}", "category": "1X2", "bookie_odd": odd_fav, "model_prob": fav_prob, "competition_id": comp_id, "date": date_str, "url": url}
        if self._get_leg_key(item_fav) not in seen_keys:
            markets.append(item_fav)
            seen_keys.add(self._get_leg_key(item_fav))

        # Gols
        ou = goals.get("over_under", {})
        p_o15 = float(ou.get("over_1_5", 76.0))
        odd_o15 = round(max(1.12, (100.0 / p_o15) * 0.92), 2)
        item_o15 = {"matchup": matchup, "name": "Més d'1.5 Gols (Over 1.5)", "category": "Gols", "bookie_odd": odd_o15, "model_prob": p_o15, "competition_id": comp_id, "date": date_str, "url": url}
        if self._get_leg_key(item_o15) not in seen_keys:
            markets.append(item_o15)
            seen_keys.add(self._get_leg_key(item_o15))

        p_u35 = float(ou.get("under_3_5", 72.0))
        odd_u35 = round(max(1.15, (100.0 / p_u35) * 0.92), 2)
        item_u35 = {"matchup": matchup, "name": "Menys de 3.5 Gols (Under 3.5)", "category": "Gols", "bookie_odd": odd_u35, "model_prob": p_u35, "competition_id": comp_id, "date": date_str, "url": url}
        if self._get_leg_key(item_u35) not in seen_keys:
            markets.append(item_u35)
            seen_keys.add(self._get_leg_key(item_u35))

        p_o25 = float(ou.get("over_2_5", 52.0))
        odd_o25 = round(max(1.40, (100.0 / p_o25) * 0.92), 2)
        item_o25 = {"matchup": matchup, "name": "Més de 2.5 Gols (Over 2.5)", "category": "Gols", "bookie_odd": odd_o25, "model_prob": p_o25, "competition_id": comp_id, "date": date_str, "url": url}
        if self._get_leg_key(item_o25) not in seen_keys:
            markets.append(item_o25)
            seen_keys.add(self._get_leg_key(item_o25))

        p_u25 = float(ou.get("under_2_5", 48.0))
        odd_u25 = round(max(1.40, (100.0 / p_u25) * 0.92), 2)
        item_u25 = {"matchup": matchup, "name": "Menys de 2.5 Gols (Under 2.5)", "category": "Gols", "bookie_odd": odd_u25, "model_prob": p_u25, "competition_id": comp_id, "date": date_str, "url": url}
        if self._get_leg_key(item_u25) not in seen_keys:
            markets.append(item_u25)
            seen_keys.add(self._get_leg_key(item_u25))

        # BTTS
        btts = goals.get("btts", {})
        p_by = float(btts.get("yes", 50.0))
        odd_by = round(max(1.40, (100.0 / p_by) * 0.92), 2)
        item_by = {"matchup": matchup, "name": "Ambdós Equips Marquen (BTTS Sí)", "category": "BTTS", "bookie_odd": odd_by, "model_prob": p_by, "competition_id": comp_id, "date": date_str, "url": url}
        if self._get_leg_key(item_by) not in seen_keys:
            markets.append(item_by)
            seen_keys.add(self._get_leg_key(item_by))

        p_bn = float(btts.get("no", 50.0))
        odd_bn = round(max(1.40, (100.0 / p_bn) * 0.92), 2)
        item_bn = {"matchup": matchup, "name": "Ambdós Equips Marquen (BTTS No)", "category": "BTTS", "bookie_odd": odd_bn, "model_prob": p_bn, "competition_id": comp_id, "date": date_str, "url": url}
        if self._get_leg_key(item_bn) not in seen_keys:
            markets.append(item_bn)
            seen_keys.add(self._get_leg_key(item_bn))

        # Targetes
        p_cards = cards.get("prob_over_cards", {})
        p_c35 = float(p_cards.get("over_3_5", 68.0))
        odd_c35 = round(max(1.18, (100.0 / p_c35) * 0.92), 2)
        item_c35 = {"matchup": matchup, "name": "Més de 3.5 Targetes", "category": "Targetes", "bookie_odd": odd_c35, "model_prob": p_c35, "competition_id": comp_id, "date": date_str, "url": url}
        if self._get_leg_key(item_c35) not in seen_keys:
            markets.append(item_c35)
            seen_keys.add(self._get_leg_key(item_c35))

        p_c45 = float(p_cards.get("over_4_5", 48.0))
        odd_c45 = round(max(1.45, (100.0 / p_c45) * 0.92), 2)
        item_c45 = {"matchup": matchup, "name": "Més de 4.5 Targetes", "category": "Targetes", "bookie_odd": odd_c45, "model_prob": p_c45, "competition_id": comp_id, "date": date_str, "url": url}
        if self._get_leg_key(item_c45) not in seen_keys:
            markets.append(item_c45)
            seen_keys.add(self._get_leg_key(item_c45))

        p_c55 = float(p_cards.get("over_5_5", 28.0))
        odd_c55 = round(max(1.80, (100.0 / p_c55) * 0.92), 2)
        item_c55 = {"matchup": matchup, "name": "Més de 5.5 Targetes", "category": "Targetes", "bookie_odd": odd_c55, "model_prob": p_c55, "competition_id": comp_id, "date": date_str, "url": url}
        if self._get_leg_key(item_c55) not in seen_keys:
            markets.append(item_c55)
            seen_keys.add(self._get_leg_key(item_c55))

        # Córners
        p_corners = corners.get("prob_over_corners", {})
        p_k85 = float(p_corners.get("over_8_5", 65.0))
        odd_k85 = round(max(1.20, (100.0 / p_k85) * 0.92), 2)
        item_k85 = {"matchup": matchup, "name": "Més de 8.5 Córners", "category": "Córners", "bookie_odd": odd_k85, "model_prob": p_k85, "competition_id": comp_id, "date": date_str, "url": url}
        if self._get_leg_key(item_k85) not in seen_keys:
            markets.append(item_k85)
            seen_keys.add(self._get_leg_key(item_k85))

        p_k95 = float(p_corners.get("over_9_5", 50.0))
        odd_k95 = round(max(1.40, (100.0 / p_k95) * 0.92), 2)
        item_k95 = {"matchup": matchup, "name": "Més de 9.5 Córners", "category": "Córners", "bookie_odd": odd_k95, "model_prob": p_k95, "competition_id": comp_id, "date": date_str, "url": url}
        if self._get_leg_key(item_k95) not in seen_keys:
            markets.append(item_k95)
            seen_keys.add(self._get_leg_key(item_k95))

        # Marcadors Exactes (del model Dixon-Coles)
        top_scores = goals.get("top_scorelines", [])
        for sc_obj in top_scores[:5]:
            sc = sc_obj["score"]
            sc_prob = float(sc_obj["prob"])
            sc_odd = round(max(4.5, (100.0 / sc_prob) * 0.88), 2)
            item_sc = {"matchup": matchup, "name": f"Marcador Exacte: {sc}", "category": "Marcador Exacte", "bookie_odd": sc_odd, "model_prob": sc_prob, "competition_id": comp_id, "date": date_str, "url": url}
            if self._get_leg_key(item_sc) not in seen_keys:
                markets.append(item_sc)
        for m in markets:
            if not m.get("home_team_id") and h_id:
                m["home_team_id"] = h_id
            if not m.get("away_team_id") and a_id:
                m["away_team_id"] = a_id
            if not m.get("matchup") or m.get("matchup") in ["Partit", "Partit Sense Nom"]:
                m["matchup"] = matchup

        return markets

    def generate_single_match_combos_suite(self, match: Dict[str, Any]) -> Dict[str, Any]:
        """
        Per a un únic partit, les combinades ordinàries no són possibles matemàticament
        (requereixen un mínim de 2 partits diferents).
        Retorna la suite buida per evitar combinades intra-partit dependents sense model conjunt.
        """
        empty_safe = self.calculate_combo_summary([], "Combinada Segura")
        empty_risky = self.calculate_combo_summary([], "Combinada Arriscada")
        return {
            "safe": [],
            "semi": [],
            "risky": [],
            "safe_combo": empty_safe,
            "risky_combo": empty_risky
        }

    def generate_full_combos_suite(self, matches: List[Dict[str, Any]], is_multi: bool = False, allow_same_match_duo: bool = False) -> Dict[str, Any]:
        """
        Genera el paquet de combinades ordinàries per a una jornada o conjunt de partits:
        - 2 Combinades Segures (cuota 1.95 - 3.50) totalment diferents
        - 2 Combinades Semi-Arriscades (cuota ~8.0 - 15.0) totalment diferents
        - 2 Combinades Arriscades (cuota >= 28.0) totalment diferents
        Exigeix estrictament que cada selecció provingui d'un partit diferent.
        """
        if not matches or len(matches) < 2:
            return self.generate_single_match_combos_suite(matches[0] if matches else {})

        mms = self._extract_all_match_markets(matches)
        if len(mms) < 2:
            return self.generate_single_match_combos_suite(matches[0])
        elif len(mms) in [2, 3]:
            matches_for_few = [m for m in matches if any(m.get("matchup") == mm.get("matchup") for mm in mms)]
            return self.generate_few_matches_combos_suite(matches_for_few or matches)

        global_counts = Counter()

        # 1. Combinades Segures (singles estrictament)
        safe_1, safe_2 = self.find_pair_safe(mms, is_multi=is_multi, global_counts=global_counts, max_usage=2, allow_duos=False)
        safe_keys = {self._get_leg_key(l) for l in safe_1.get("legs", []) + safe_2.get("legs", [])}

        # 2. Combinades Semi-Arriscades (singles estrictament)
        semi_1, semi_2 = self.find_pair_semi(mms, is_multi=is_multi, global_counts=global_counts, max_usage=2, allow_duos=False)

        # 3. Combinades Arriscades (singles estrictament)
        risky_1, risky_2 = self.find_pair_risky(
            mms,
            is_multi=is_multi,
            global_counts=global_counts,
            forbidden_exact=safe_keys,
            max_usage=2,
            allow_duos=False
        )

        safe_list = [c for c in [safe_1, safe_2] if c.get("legs")]
        semi_list = [c for c in [semi_1, semi_2] if c.get("legs")]
        risky_list = [c for c in [risky_1, risky_2] if c.get("legs")]

        return {
            "safe": safe_list,
            "semi": semi_list,
            "risky": risky_list,
            "safe_combo": safe_list[0] if safe_list else safe_1,
            "risky_combo": risky_list[0] if risky_list else risky_1
        }

    def generate_few_matches_combos_suite(self, day_matches: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Genera combinades per a dies amb 2 o 3 partits:
        - Exigeix estrictament que cada selecció provingui d'un partit diferent.
        - Si no es poden assolir les quotes objectiu sense inventar seleccions dependents,
          és preferible generar menys combinades o cap combinada abans d'inventar-ne.
        """
        if not day_matches or len(day_matches) < 2:
            return self.generate_single_match_combos_suite(day_matches[0] if day_matches else {})

        for m in day_matches:
            if not m.get("matchup"):
                h = m.get("home_team", {}).get("name") if isinstance(m.get("home_team"), dict) else "Local"
                a = m.get("away_team", {}).get("name") if isinstance(m.get("away_team"), dict) else "Visitant"
                m["matchup"] = f"{h} vs {a}"

        # Deduplicar partits del dia per identitat canònica
        seen_day_keys = set()
        deduped_day_matches = []
        for m in day_matches:
            mk = self._get_match_canonical_key(m)
            if mk not in seen_day_keys:
                seen_day_keys.add(mk)
                deduped_day_matches.append(m)
        day_matches = deduped_day_matches

        if len(day_matches) < 2:
            return self.generate_single_match_combos_suite(day_matches[0])

        match_pools = []
        for m in day_matches:
            mkts = self._extract_single_match_markets(m)
            if mkts:
                match_pools.append({"match": m, "markets": mkts})

        if len(match_pools) < 2:
            return self.generate_single_match_combos_suite(day_matches[0])

        def find_combos(target_min_odd: float, target_max_odd: float, min_leg_prob: float = 0.0):
            candidates = []
            for k in range(2, len(match_pools) + 1):
                for match_combo in itertools.combinations(match_pools, k):
                    cand_lists = [[m for m in mp["markets"] if m.get("model_prob", 0) >= min_leg_prob][:4] for mp in match_combo]
                    if any(len(cl) == 0 for cl in cand_lists):
                        continue
                    for choice in itertools.product(*cand_lists):
                        choice_legs = list(choice)
                        if not self.is_valid_combo_legs(choice_legs, allow_duos=False):
                            continue
                        odds = [l["bookie_odd"] for l in choice_legs]
                        c_odd = round(self.calculate_combined_odds(odds), 2)
                        if target_min_odd <= c_odd <= target_max_odd:
                            p_dec = self.calculate_combined_probability([l["model_prob"] for l in choice_legs])
                            score = p_dec - abs(c_odd - (target_min_odd + target_max_odd) / 2.0) * 0.001
                            candidates.append({
                                "legs": choice_legs,
                                "c_odd": c_odd,
                                "p_dec": p_dec,
                                "score": score,
                                "sig": self._get_combo_sig(choice_legs)
                            })
                            if len(candidates) >= 100:
                                break
                    if len(candidates) >= 100:
                        break
            candidates.sort(key=lambda x: x["score"], reverse=True)
            return candidates

        # 1. Segures (1.90 - 3.50)
        safe_cands = find_combos(1.90, 3.50, min_leg_prob=50.0)
        if not safe_cands:
            safe_cands = find_combos(1.70, 4.00, min_leg_prob=40.0)

        # 2. Semi-Arriscades (8.50 - 16.50)
        semi_cands = find_combos(8.50, 16.50, min_leg_prob=30.0)
        if not semi_cands:
            semi_cands = find_combos(6.00, 20.00, min_leg_prob=20.0)

        # 3. Arriscades (>= 28.0)
        risky_cands = find_combos(28.0, 75.0, min_leg_prob=15.0)
        if not risky_cands:
            risky_cands = find_combos(20.0, 95.0, min_leg_prob=10.0)

        def pick_two_diverse(cands, profile_name_1, profile_name_2, cat_code, stake):
            if not cands:
                return []
            c1 = cands[0]
            c2 = None
            c1_sig = c1.get("sig", frozenset())
            for c in cands[1:]:
                if c.get("sig") != c1_sig:
                    c2 = c
                    break
            res = []
            s1 = self.calculate_combo_summary(c1.get("legs", []), profile_name_1)
            s1["category_code"] = cat_code
            s1["recommended_stake"] = stake
            res.append(s1)
            if c2:
                s2 = self.calculate_combo_summary(c2.get("legs", []), profile_name_2)
                s2["category_code"] = cat_code
                s2["recommended_stake"] = stake
                res.append(s2)
            return res

        prefix = "Mega-Combinada Multi-Lliga" if len(set(m.get("competition_id") for m in day_matches if m.get("competition_id"))) > 1 else "Combinada"

        safe_list = pick_two_diverse(safe_cands, f"{prefix} Segura #1 (Cuota 2-3)", f"{prefix} Segura #2 (Cuota 2-3 Alternativa)", "SAFE", 25.0)
        semi_list = pick_two_diverse(semi_cands, f"{prefix} Semi-Arriscada #1 (Cuota ~10.0)", f"{prefix} Semi-Arriscada #2 (Cuota ~10.0 Alternativa)", "SEMI", 10.0)
        risky_list = pick_two_diverse(risky_cands, f"{prefix} Arriscada #1 (Cuota >= 30.0)", f"{prefix} Arriscada #2 (Cuota >= 30.0 Alternativa)", "RISKY", 5.0)

        return {
            "safe": safe_list,
            "semi": semi_list,
            "risky": risky_list,
            "safe_combo": safe_list[0] if safe_list else self.calculate_combo_summary([], f"{prefix} Segura"),
            "risky_combo": risky_list[0] if risky_list else self.calculate_combo_summary([], f"{prefix} Arriscada")
        }

    def generate_daily_combos_suite(self, day_matches: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Genera la Suite Oficial de 6 Combinades del Dia (2 Segures, 2 Semi, 2 Arriscades):
        - Si no hi ha partits aquell dia: has_matches = False.
        - Si només juga 1 partit: no es generen combinades ordinàries (requereixen >= 2 partits).
        - Si juguen 2 o 3 partits: combinades combinant partits diferents del dia.
        - Si juguen 4 o més partits: combinades completes de tota la lliga / multilliga.
        """
        if not day_matches:
            return {
                "has_matches": False,
                "matches_count": 0,
                "league_note": "Sense partits programats per a avui a cap de les 7 lligues analitzades.",
                "safe": [],
                "semi": [],
                "risky": [],
                "safe_combo": {},
                "risky_combo": {}
            }

        # Deduplicar partits del dia per identitat canònica abans de processar
        seen_suite_keys = set()
        deduped_suite_matches = []
        for m in day_matches:
            mk = self._get_match_canonical_key(m)
            if mk not in seen_suite_keys:
                seen_suite_keys.add(mk)
                deduped_suite_matches.append(m)
        day_matches = deduped_suite_matches

        comps = {m.get("competition_id") for m in day_matches if m.get("competition_id")}
        comp_map = {
            "LALIGA": "LaLiga EA Sports",
            "PREMIER": "Premier League",
            "HYPERMOTION": "LaLiga Hypermotion",
            "CHAMPIONSHIP": "EFL Championship",
            "BUNDESLIGA": "Bundesliga",
            "LIGUE1": "Ligue 1",
            "SERIEA": "Serie A"
        }
        comp_names = [comp_map.get(c, c) for c in comps]

        if len(day_matches) == 1:
            m_title = day_matches[0].get("matchup") or "Partit Únic"
            return {
                "has_matches": True,
                "matches_count": 1,
                "league_note": f"Partit únic d'avui: {m_title} · No es poden generar combinades ordinàries amb menys de 2 partits diferents.",
                "safe": [],
                "semi": [],
                "risky": [],
                "safe_combo": self.calculate_combo_summary([], "Combinada Segura"),
                "risky_combo": self.calculate_combo_summary([], "Combinada Arriscada")
            }

        if len(day_matches) <= 3:
            suite = self.generate_few_matches_combos_suite(day_matches)
        else:
            suite = self.generate_full_combos_suite(day_matches, is_multi=(len(comps) > 1), allow_same_match_duo=False)

        suite["has_matches"] = True
        suite["matches_count"] = len(day_matches)

        if len(comps) == 1:
            suite["league_note"] = f"Partits exclusius de {comp_names[0]} d'avui ({len(day_matches)} partits)"
        else:
            suite["league_note"] = f"Partits del dia de múltiples lligues ({', '.join(comp_names)}) · {len(day_matches)} partits"

        return suite

    def analyze_jornada_combos(self, predicted_matches: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Genera el paquet complet de 6 combinades per a la jornada."""
        return self.generate_full_combos_suite(predicted_matches, is_multi=False)

    def analyze_multileague_combos(self, all_matches: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Compatibilitat: redirigeix a combinades diàries o conjuntes."""
        return self.generate_daily_combos_suite(all_matches)



