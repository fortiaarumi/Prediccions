"""
engine/arbitrage_engine.py
==========================
Motor matemàtic d'Arbitratge Esportiu (Surebets / "Diners Infinits").
Detecta discrepàncies de quotes entre Winamax i Bet365 on la suma de probabilitats
implícites és inferior a 1.0 (Margin < 100%), garantint un retorn net 100% positiu
sense risc de mercat independentment del resultat final.

Mercats avaluats:
1. 2-way: Doble Oportunitat vs Guanyador Pur (1X vs 2, X2 vs 1, 12 vs X)
2. 2-way: Més / Menys de 2.5 Gols (Over / Under)
3. 2-way: Ambdós Equips Marquen (BTTS Sí vs No)
4. 3-way: 1X2 Cross-Bookmaker (millor quota d'1, X i 2 creuant Winamax i Bet365)
5. 2-way: Còrners i Targetes (línies coincidents)

Models de comissió d'exchange:
- NET_MARKET_WINNINGS: Comissió sobre el guany net del mercat (max(0, PnL) * rate).
  S'aplica a Betfair (2% ES/DGOJ), Matchbook (4% ES/ROW, 2% UK/IE), Smarkets Standard (2%).
- PER_BET_SETTLEMENT: Comissió per aposta individual (turnover fee).
  S'aplica a Smarkets Pro (1%) i Smarkets Select (3%).
"""

import os
import math
import hashlib
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Tuple

# ----- Constants dels models de comissió d'exchange -----
COMMISSION_MODEL_NET_MARKET_WINNINGS = "NET_MARKET_WINNINGS"
COMMISSION_MODEL_PER_BET_SETTLEMENT = "PER_BET_SETTLEMENT"
COMMISSION_MODEL_NONE = "NONE"  # Bookmakers tradicionals (comissió 0%)

# Configuració territorial per defecte: perfils de comissió per proveïdor d'exchange.
# Es construeixen dinàmicament en temps d'instanciació per respectar variables d'entorn
# modificades en temps d'execució.
def build_default_exchange_profiles() -> Dict[str, Dict[str, Any]]:
    """Construeix dinàmicament els perfils per defecte d'exchanges avaluant variables d'entorn en temps d'execució."""
    mb_territory = os.environ.get("MATCHBOOK_TERRITORY", "ES").strip().upper()
    mb_rate_env = os.environ.get("MATCHBOOK_COMMISSION_RATE")
    if mb_rate_env is not None and str(mb_rate_env).strip():
        mb_rate = float(str(mb_rate_env).strip())
    elif mb_territory in ("UK", "IE", "UK_IE"):
        mb_rate = 0.02
    else:
        mb_rate = 0.04

    bf_rate_env = os.environ.get("BETFAIR_COMMISSION_RATE")
    bf_rate = float(str(bf_rate_env).strip()) if bf_rate_env and str(bf_rate_env).strip() else 0.02
    bf_territory = os.environ.get("BETFAIR_TERRITORY", "ES").strip().upper()

    sm_rate_env = os.environ.get("SMARKETS_COMMISSION_RATE")
    sm_rate = float(str(sm_rate_env).strip()) if sm_rate_env and str(sm_rate_env).strip() else 0.02
    sm_territory = os.environ.get("SMARKETS_TERRITORY", "ES").strip().upper()

    return {
        "betfair": {
            "commission_model": COMMISSION_MODEL_NET_MARKET_WINNINGS,
            "rate": bf_rate,
            "territory": bf_territory,
            "notes": "Betfair Espanya (DGOJ): 2% sobre guanys nets del mercat"
        },
        "matchbook": {
            "commission_model": COMMISSION_MODEL_NET_MARKET_WINNINGS,
            "rate": mb_rate,
            "territory": mb_territory,
            "notes": "Matchbook: 2% UK/IE, 4% resta del món (ES inclòs)"
        },
        "smarkets": {
            "commission_model": COMMISSION_MODEL_NET_MARKET_WINNINGS,
            "rate": sm_rate,
            "territory": sm_territory,
            "notes": "Smarkets Standard: 2% sobre guanys nets del mercat"
        },
        "smarkets_pro": {
            "commission_model": COMMISSION_MODEL_PER_BET_SETTLEMENT,
            "rate": 0.01,
            "territory": "ALL",
            "notes": "Smarkets Pro: 1% per aposta liquidada (guanys i pèrdues)"
        },
        "smarkets_select": {
            "commission_model": COMMISSION_MODEL_PER_BET_SETTLEMENT,
            "rate": 0.03,
            "territory": "ALL",
            "notes": "Smarkets Select: 3% per aposta liquidada (guanys i pèrdues)"
        },
        "winamax": {
            "commission_model": COMMISSION_MODEL_NONE,
            "rate": 0.0,
            "territory": "ES",
            "notes": "Winamax: bookmaker tradicional (0% comissió)"
        },
        "bet365": {
            "commission_model": COMMISSION_MODEL_NONE,
            "rate": 0.0,
            "territory": "ES",
            "notes": "Bet365: bookmaker tradicional (0% comissió)"
        },
        "betway": {
            "commission_model": COMMISSION_MODEL_NONE,
            "rate": 0.0,
            "territory": "ES",
            "notes": "Betway: bookmaker tradicional (0% comissió)"
        }
    }


# Constant per compatibilitat amb imports directes de mòdul
DEFAULT_EXCHANGE_PROFILES: Dict[str, Dict[str, Any]] = build_default_exchange_profiles()


def round_commercial(val: Any) -> int:
    """
    Arrodoniment comercial estàndard (round half-up per a bankrolls positius).
    Els empats (.5) s'arrodoneixen sempre cap amunt cap a +infinit.
    Garanteix una política matemàticament idèntica a JavaScript (Math.floor(x + 0.5)).
    Exemples:
      100.49 -> 100
      100.50 -> 101
      100.51 -> 101
      101.50 -> 102
      102.50 -> 103
      99.40  -> 99
    """
    if val is None:
        return 100
    try:
        f = float(val)
    except (ValueError, TypeError):
        return 100
    return int(math.floor(f + 0.5))


def normalize_bankroll(bankroll: Any, min_legs: int = 2) -> int:
    """Normalitza el bankroll a un enter positiu >= min_legs usant arrodoniment comercial."""
    rounded = round_commercial(bankroll if bankroll is not None else 100.0)
    return max(min_legs, rounded)


class ArbitrageEngine:
    build_default_exchange_profiles = staticmethod(build_default_exchange_profiles)
    round_commercial = staticmethod(round_commercial)
    normalize_bankroll = staticmethod(normalize_bankroll)

    def __init__(
        self,
        min_profit_threshold: float = 0.0,
        near_arb_threshold: float = -2.5,
        max_profit_threshold: float = 15.0,
        max_odds_age_seconds: Optional[float] = None,
        max_skew_seconds: Optional[float] = None,
        enforce_strict_freshness: bool = False,
        commission_rate: float = 0.0,
        bookmaker_commissions: Optional[Dict[str, float]] = None,
        exchange_profiles: Optional[Dict[str, Dict[str, Any]]] = None
    ):
        """
        :param min_profit_threshold: % mínim de benefici net garantit per considerar-ho Surebet (ex: 0.0% o 1.0%).
        :param near_arb_threshold: % límit per classificar com a 'Near-Arb' (marge estret en monitorització).
        :param max_profit_threshold: % límit màxim de seguretat (palpable error filter). Si supera el 15%,
                                     és un error de feed o quota errònia que la casa cancel·larà.
        :param max_odds_age_seconds: Edat màxima en segons d'una quota (defecte: 60s, configurable via SUREBET_MAX_ODDS_AGE_SECONDS).
        :param max_skew_seconds: Decalatge màxim admès entre timestamps de quotes (defecte: 30s, configurable via SUREBET_MAX_SKEW_SECONDS).
        :param enforce_strict_freshness: Si és True, descarta terminantment qualsevol oportunitat que no compleixi
                                         el criteri estricte de frescor i màxim decalatge temporal.
        :param commission_rate: Taxa de comissió per defecte sobre guanys nets si no s'especifica per casa.
        :param bookmaker_commissions: Diccionari de taxes de comissió per casa (ex: Winamax: 0.0%, Bet365: 0.0%, Betfair: 2.0%).
        :param exchange_profiles: Diccionari de perfils d'exchange personalitzats.
                                  Cada entrada ha de tenir 'commission_model' i 'rate'.
        """
        self.min_profit = min_profit_threshold
        self.near_arb_limit = near_arb_threshold
        self.max_profit = max_profit_threshold
        self.max_odds_age_seconds = float(
            max_odds_age_seconds if max_odds_age_seconds is not None
            else os.environ.get("SUREBET_MAX_ODDS_AGE_SECONDS", 60.0)
        )
        self.max_skew_seconds = float(
            max_skew_seconds if max_skew_seconds is not None
            else os.environ.get("SUREBET_MAX_SKEW_SECONDS", 30.0)
        )
        self.enforce_strict_freshness = enforce_strict_freshness
        self.commission_rate = commission_rate

        # 1. Construcció dinàmica de perfils d'exchange avaluant les variables d'entorn actuals
        self.exchange_profiles = build_default_exchange_profiles()

        # 2. Diccionari base de taxes de comissió sincronitzat amb els perfils
        self.bookmaker_commissions: Dict[str, float] = {
            k: float(v.get("rate", 0.0)) for k, v in self.exchange_profiles.items()
        }
        self.bookmaker_commissions["default"] = commission_rate

        # 3. Prevalença: paràmetre bookmaker_commissions passat al constructor
        if bookmaker_commissions:
            for bk_raw, rate_val in bookmaker_commissions.items():
                bk_key = bk_raw.lower().strip().replace(" ", "_")
                r_val = float(rate_val)
                self.bookmaker_commissions[bk_key] = r_val
                if bk_key in self.exchange_profiles:
                    self.exchange_profiles[bk_key]["rate"] = r_val
                else:
                    self.exchange_profiles[bk_key] = {
                        "commission_model": COMMISSION_MODEL_NONE if r_val == 0.0 else COMMISSION_MODEL_NET_MARKET_WINNINGS,
                        "rate": r_val,
                        "territory": "N/A",
                        "notes": f"Personalitzat per bookmaker ({bk_raw})"
                    }

        # 4. Prevalença màxima: exchange_profiles explícit passat al constructor
        if exchange_profiles:
            for ex_raw, prof in exchange_profiles.items():
                ex_key = ex_raw.lower().strip().replace(" ", "_")
                self.exchange_profiles[ex_key] = dict(prof)
                if "rate" in prof:
                    self.bookmaker_commissions[ex_key] = float(prof["rate"])

        # 5. Compatibilitat retroactiva: commission_rate global si no hi ha config específica per casa
        if commission_rate > 0.0 and not bookmaker_commissions and not exchange_profiles:
            self.bookmaker_commissions["default"] = commission_rate
            self.exchange_profiles["winamax"]["rate"] = commission_rate
            self.exchange_profiles["winamax"]["commission_model"] = COMMISSION_MODEL_NET_MARKET_WINNINGS
            self.exchange_profiles["bet365"]["rate"] = commission_rate
            self.exchange_profiles["bet365"]["commission_model"] = COMMISSION_MODEL_NET_MARKET_WINNINGS
            if "betway" in self.exchange_profiles:
                self.exchange_profiles["betway"]["rate"] = commission_rate
                self.exchange_profiles["betway"]["commission_model"] = COMMISSION_MODEL_NET_MARKET_WINNINGS

    # ----- Resolució del perfil de comissió d'una pota -----

    def get_exchange_profile(self, leg: Dict[str, Any]) -> Dict[str, Any]:
        """
        Retorna el perfil complet de comissió per a la casa d'apostes de la pota.
        Jerarquia: exchange_profiles > bookmaker_commissions > defecte NONE/0%.
        """
        bk_raw = str(leg.get("bookmaker") or "").strip()
        bk = bk_raw.lower().replace(" ", "_")
        # Cerca directa al perfil d'exchange
        if bk in self.exchange_profiles:
            return dict(self.exchange_profiles[bk])
        # Cerca per substring (ex: "smarkets_pro" dins "smarkets pro tier")
        for name, prof in self.exchange_profiles.items():
            if name in bk or bk in name:
                return dict(prof)
        # Bookmaker tradicional o no registrat
        rate = self.get_leg_commission(leg)
        return {
            "commission_model": COMMISSION_MODEL_NONE if rate == 0.0 else COMMISSION_MODEL_NET_MARKET_WINNINGS,
            "rate": rate,
            "territory": "N/A",
            "notes": f"Bookmaker {bk_raw}"
        }

    def get_leg_commission(self, leg: Dict[str, Any]) -> float:
        """Determina la taxa de comissió exacta aplicable a la casa d'apostes de la pota."""
        bk_raw = str(leg.get("bookmaker") or "").strip()
        bk = bk_raw.lower().replace(" ", "_")
        if bk in self.exchange_profiles:
            return float(self.exchange_profiles[bk].get("rate", 0.0))
        for name, prof in self.exchange_profiles.items():
            if name in bk or bk in name:
                return float(prof.get("rate", 0.0))
        if bk in self.bookmaker_commissions:
            return float(self.bookmaker_commissions[bk])
        for name, comm in self.bookmaker_commissions.items():
            if name in bk:
                return float(comm)
        return float(self.bookmaker_commissions.get("default", self.commission_rate))

    # ----- Generació d'identificadors deterministes per a potes -----

    @staticmethod
    def generate_leg_id(leg_index: int, bookmaker: str, selection: str, odd: float, unique_tag: str = "") -> str:
        """
        Genera un identificador determinista i estable per a cada pota dins d'una oportunitat.
        No utilitza id() de Python (no persistent). Usa un hash SHA-256 truncat de les
        propietats immutables de la pota dins del context de l'oportunitat.
        """
        raw = f"leg_{leg_index}:{bookmaker}:{selection}:{odd}"
        if unique_tag:
            raw += f":{unique_tag}"
        return f"leg_{leg_index}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:12]}"

    @staticmethod
    def get_settlement_key(leg: Dict[str, Any]) -> str:
        """
        Determina la clau de liquidació d'una pota per a l'agrupació de comissions
        de mercat (NET_MARKET_WINNINGS).

        Supòsit operatiu (Single-Account Assumption):
        En exchanges esportives, la compensació de guanys i pèrdues només opera
        entre ordres liquidades sota el MATEIX compte d'usuari i al MATEIX mercat natiu.
        Si la pota defineix un 'account_id', s'inclou com a dimensió d'aïllament;
        si no s'especifica, s'assumeix el compte per defecte de l'usuari.

        Jerarquia estricta i sense ambigüitat:
        1. native_market_id: identificador real natiu del mercat de l'exchange (ex: '1.205839210').
           S'agrupa com f"{provider}::market::{native_market_id}" (o amb account_id si s'especifica).
        2. proven_market_key: correspondència canònica explícita i provada.
           Ha de contenir metadades estructurades que evitin qualsevol risc de col·lisió
           entre partits diferents:
           - Si és un dict: ha de contenir almenys 'event_id' (o 'match_id') i 'market_type'.
           - Si és un tuple/list: len >= 2 amb elements no buits (ex: (event_id, market_type)).
           - Si és un string: ha de contenir almenys un delimitador '::' amb identificador d'esdeveniment i mercat.
           Si no compleix aquests criteris d'inequivocitat, es rebutja l'agrupació i es deriva a l'aïllament estricte.
        3. Aïllament estricte per defecte:
           Cada pota s'aïlla individualment amb el seu 'leg_id' determinista unívoc.
           MAI s'utilitza un bet_id per compensar apostes diferents.
        """
        provider = str(leg.get("bookmaker") or "unknown").lower().strip()
        account_id = str(leg.get("account_id") or "").lower().strip()
        acc_prefix = f"::{account_id}" if account_id else ""
        native_mid = leg.get("native_market_id")

        if native_mid is not None and str(native_mid).strip():
            # Cas 1: ID natiu del mercat de l'exchange present i vàlid
            return f"{provider}{acc_prefix}::market::{str(native_mid).strip()}"

        proven_key = leg.get("proven_market_key")
        if proven_key is not None:
            # Validació rigorosa que la clau canònica és inequívoca i lligada a l'esdeveniment
            is_valid_canonical = False
            canonical_str = ""

            if isinstance(proven_key, dict):
                ev = proven_key.get("event_id") or proven_key.get("match_id")
                mtype = proven_key.get("market_type")
                if ev and mtype:
                    period = proven_key.get("period", "full_time")
                    line = proven_key.get("line", "")
                    canonical_str = f"{ev}::{mtype}::{period}::{line}"
                    is_valid_canonical = True
            elif isinstance(proven_key, (tuple, list)) and len(proven_key) >= 2:
                if all(str(p).strip() for p in proven_key[:2]):
                    canonical_str = "::".join(str(p).strip() for p in proven_key)
                    is_valid_canonical = True
            elif isinstance(proven_key, str) and "::" in proven_key:
                parts = [p.strip() for p in proven_key.split("::") if p.strip()]
                if len(parts) >= 2:
                    canonical_str = "::".join(parts)
                    is_valid_canonical = True

            if is_valid_canonical:
                # Cas 2: Clau canònica explícitament documentada i demostrada
                return f"{provider}{acc_prefix}::canonical::{canonical_str}"

        # Cas 3: Aïllament estricte per defecte
        leg_id = leg.get("leg_id") or ArbitrageEngine.generate_leg_id(
            int(leg.get("_leg_index", 0)),
            provider,
            str(leg.get("selection") or ""),
            float(leg.get("odd") or 0.0),
            unique_tag=str(leg.get("_unique_tag") or "")
        )
        return f"{provider}{acc_prefix}::isolated::{leg_id}"

    # ----- Liquidació per escenaris amb models de comissió -----

    def calculate_scenario_pnl(
        self,
        legs: List[Dict[str, Any]],
        stakes: List[float],
        scenario_index: int,
        leg_outcomes: Optional[List[str]] = None,
        profiles: Optional[List[Dict[str, Any]]] = None,
        settlement_keys: Optional[List[str]] = None
    ) -> float:
        """
        Calcula el PnL net d'un escenari concret tenint en compte els models de comissió de cada proveïdor.

        :param legs: Llista de potes de l'oportunitat.
        :param stakes: Llista d'imports apostats (stakes) corresponents a cada pota.
        :param scenario_index: Índex de la pota guanyadora en escenaris estàndard (exclosos altres).
        :param leg_outcomes: Opcional. Llista explícita d'estats per pota ('WIN', 'LOSS', 'PUSH'/'VOID').
                             Permet avaluar escenaris amb devolució (push/void).
        :param profiles: Opcional. Llista precomputada de perfils d'exchange (evita recomputacions en bucles exhaustius).
        :param settlement_keys: Opcional. Llista precomputada de claus de liquidació (evita recomputacions en bucles exhaustius).
        """
        # Pas 1: Calcular el gross PnL de cada pota en aquest escenari
        leg_pnls = []
        for i, (leg, stake) in enumerate(zip(legs, stakes)):
            odd = float(leg.get("odd") or 0.0)
            if leg_outcomes is not None and i < len(leg_outcomes):
                outcome = str(leg_outcomes[i]).upper()
                if outcome == "WIN":
                    gross_pnl = stake * (odd - 1.0)
                elif outcome in ("PUSH", "VOID"):
                    gross_pnl = 0.0  # Devolució de l'aposta
                else:
                    gross_pnl = -stake
            else:
                if i == scenario_index:
                    # Pota guanyadora: retorn = stake * odd; gross_pnl = stake * (odd - 1)
                    gross_pnl = stake * (odd - 1.0)
                else:
                    # Pota perdedora: gross_pnl = -stake
                    gross_pnl = -stake
            leg_pnls.append(gross_pnl)

        # Pas 2: Obtenir perfils i settlement keys amb índex de posició garantit
        if profiles is None:
            profiles = [self.get_exchange_profile(leg) for leg in legs]
        if settlement_keys is None:
            settlement_keys = []
            for i, leg in enumerate(legs):
                leg_indexed = dict(leg, _leg_index=leg.get("_leg_index", i))
                settlement_keys.append(self.get_settlement_key(leg_indexed))

        # Pas 3: Agrupar per settlement_key i calcular comissions
        groups: Dict[str, Dict[str, Any]] = {}
        for i, (leg, pnl, profile, skey) in enumerate(zip(legs, leg_pnls, profiles, settlement_keys)):
            model = profile["commission_model"]
            rate = profile["rate"]

            if model == COMMISSION_MODEL_PER_BET_SETTLEMENT:
                # PER_BET_SETTLEMENT: comissió individual per aposta, mai agrupada
                unique_key = f"per_bet_{i}_{skey}"
                groups[unique_key] = {
                    "model": model,
                    "rate": rate,
                    "entries": [(i, pnl, stakes[i])]
                }
            else:
                # NET_MARKET_WINNINGS o NONE: agrupació per settlement_key
                if skey not in groups:
                    groups[skey] = {
                        "model": model,
                        "rate": rate,
                        "entries": []
                    }
                groups[skey]["entries"].append((i, pnl, stakes[i]))

        # Pas 4: Calcular el PnL net total deduint comissions per grup
        total_net_pnl = 0.0
        for gkey, group in groups.items():
            model = group["model"]
            rate = group["rate"]
            entries = group["entries"]

            if model == COMMISSION_MODEL_NONE:
                # Bookmaker tradicional: sense comissió
                total_net_pnl += sum(pnl for _, pnl, _ in entries)

            elif model == COMMISSION_MODEL_NET_MARKET_WINNINGS:
                # Comissió sobre el guany net agregat positiu del mercat
                market_gross_pnl = sum(pnl for _, pnl, _ in entries)
                commission = max(0.0, market_gross_pnl) * rate
                total_net_pnl += market_gross_pnl - commission

            elif model == COMMISSION_MODEL_PER_BET_SETTLEMENT:
                # Comissió individual per aposta liquidada
                for _, pnl, stake in entries:
                    if pnl > 0.0:
                        # Aposta guanyadora: comissió sobre el guany brut
                        commission = pnl * rate
                    elif pnl < 0.0:
                        # Aposta perdedora: comissió sobre el stake jugat
                        commission = stake * rate
                    else:
                        # Aposta amb push/void (PnL == 0.0): sense comissió a l'exchange
                        commission = 0.0
                    total_net_pnl += pnl - commission

        return total_net_pnl

    @staticmethod
    def _parse_ts(ts_val: Any) -> Optional[datetime]:
        """Converteix un timestamp ISO o enter a datetime UTC."""
        if not ts_val:
            return None
        if isinstance(ts_val, datetime):
            return ts_val if ts_val.tzinfo else ts_val.replace(tzinfo=timezone.utc)
        s = str(ts_val).strip()
        try:
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None

    def validate_legs_freshness(self, legs: List[Dict[str, Any]], now: Optional[datetime] = None) -> Tuple[bool, str, float]:
        """
        Valida que totes les quotes compleixin els criteris d'edat màxima i de decalatge temporal (skew).
        Retorna (is_valid, reason, max_skew_seconds_found).
        """
        ref_now = now or datetime.now(timezone.utc)
        timestamps = []

        for idx, leg in enumerate(legs):
            ts_str = leg.get("captured_at") or leg.get("polled_at") or leg.get("timestamp")
            dt = self._parse_ts(ts_str)
            if not dt:
                if self.enforce_strict_freshness:
                    return False, f"missing_timestamp_leg_{idx+1}", 0.0
            else:
                age_s = (ref_now - dt).total_seconds()
                if self.enforce_strict_freshness and age_s > self.max_odds_age_seconds:
                    return False, f"stale_odds_leg_{idx+1}_age_{int(age_s)}s", 0.0
                timestamps.append(dt)

        if len(timestamps) >= 2:
            epochs = [t.timestamp() for t in timestamps]
            skew_s = max(epochs) - min(epochs)
            if self.enforce_strict_freshness and skew_s > self.max_skew_seconds:
                return False, f"clock_skew_exceeded_{int(skew_s)}s", skew_s
            return True, "ok", skew_s

        return True, "ok", 0.0

    def calculate_2way_arbitrage(
        self,
        leg1: Dict[str, Any],
        leg2: Dict[str, Any],
        market_name: str,
        match_info: Dict[str, Any],
        now: Optional[datetime] = None,
        bankroll: float = 100.0
    ) -> Optional[Dict[str, Any]]:
        """Calcula l'arbitratge entre dos resultats mútuament excloents i exhaustius."""
        o1 = float(leg1.get("odd") or 0.0)
        o2 = float(leg2.get("odd") or 0.0)

        if o1 <= 1.01 or o2 <= 1.01 or o1 > 15.0 or o2 > 15.0:
            return None

        # Validació de frescor estricta i decalatge temporal
        is_fresh, fresh_reason, skew_found = self.validate_legs_freshness([leg1, leg2], now=now)
        if not is_fresh and self.enforce_strict_freshness:
            return None

        # Suma de probabilitats implícites (Invers de les quotes)
        sum_inv = (1.0 / o1) + (1.0 / o2)
        if sum_inv <= 0.0:
            return None

        gross_profit_pct = round(((1.0 / sum_inv) - 1.0) * 100.0, 2)
        margin_pct = round(sum_inv * 100.0, 2)

        # Filtre anti errors palpables o quotes corruptes (>15% és irreal pre-partit)
        if gross_profit_pct < self.near_arb_limit or gross_profit_pct > self.max_profit:
            return None

        # Assignar leg_id deterministes
        leg1_id = self.generate_leg_id(0, str(leg1.get("bookmaker", "")), str(leg1.get("selection", "")), o1)
        leg2_id = self.generate_leg_id(1, str(leg2.get("bookmaker", "")), str(leg2.get("selection", "")), o2)
        leg1 = dict(leg1, leg_id=leg1_id, _leg_index=0)
        leg2 = dict(leg2, leg_id=leg2_id, _leg_index=1)

        base_bankroll = float(self.normalize_bankroll(bankroll, min_legs=2))

        # Optimització exhaustiva global de stakes enters sobre el bankroll seleccionat
        # L'espai és 1-D amb exactament base_bankroll - 1 punts candidats (s1 in [1, B-1], s2 = B - s1).
        # Avaluem directament el min PnL net de cada punt amb calculate_scenario_pnl.
        # Això garanteix el màxim absolut de min(PnL_net) sense biaix heurístic d'inversos bruts.
        legs_2way = [leg1, leg2]
        profiles_2way = [self.get_exchange_profile(l) for l in legs_2way]
        keys_2way = [
            self.get_settlement_key(dict(l, _leg_index=l.get("_leg_index", i)))
            for i, l in enumerate(legs_2way)
        ]

        best_round_s1 = max(1, int(base_bankroll) // 2)
        best_round_s2 = int(base_bankroll) - best_round_s1
        best_min_pnl = -float("inf")
        best_avg_pnl = -float("inf")
        best_var_pnl = float("inf")

        for test_s1 in range(1, int(base_bankroll)):
            test_s2 = int(base_bankroll) - test_s1
            stakes_test = [float(test_s1), float(test_s2)]
            pnl_s0 = self.calculate_scenario_pnl(legs_2way, stakes_test, 0, profiles=profiles_2way, settlement_keys=keys_2way)
            pnl_s1 = self.calculate_scenario_pnl(legs_2way, stakes_test, 1, profiles=profiles_2way, settlement_keys=keys_2way)
            min_pnl = min(pnl_s0, pnl_s1)
            avg_pnl = (pnl_s0 + pnl_s1) / 2.0
            var_pnl = (pnl_s0 - avg_pnl) ** 2 + (pnl_s1 - avg_pnl) ** 2

            if min_pnl > best_min_pnl + 1e-6:
                best_min_pnl = min_pnl
                best_avg_pnl = avg_pnl
                best_var_pnl = var_pnl
                best_round_s1 = test_s1
                best_round_s2 = test_s2
            elif abs(min_pnl - best_min_pnl) <= 1e-6:
                if avg_pnl > best_avg_pnl + 1e-6:
                    best_min_pnl = min_pnl
                    best_avg_pnl = avg_pnl
                    best_var_pnl = var_pnl
                    best_round_s1 = test_s1
                    best_round_s2 = test_s2
                elif abs(avg_pnl - best_avg_pnl) <= 1e-6:
                    if var_pnl < best_var_pnl - 1e-6:
                        best_min_pnl = min_pnl
                        best_avg_pnl = avg_pnl
                        best_var_pnl = var_pnl
                        best_round_s1 = test_s1
                        best_round_s2 = test_s2

        # Calcular retorns nets definitius per escenari amb els stakes arrodonits
        final_stakes_2way = [float(best_round_s1), float(best_round_s2)]
        pnl_scenario_0 = self.calculate_scenario_pnl(legs_2way, final_stakes_2way, 0, profiles=profiles_2way, settlement_keys=keys_2way)
        pnl_scenario_1 = self.calculate_scenario_pnl(legs_2way, final_stakes_2way, 1, profiles=profiles_2way, settlement_keys=keys_2way)

        ret1 = round(base_bankroll + pnl_scenario_0, 2)
        ret2 = round(base_bankroll + pnl_scenario_1, 2)

        min_return = min(ret1, ret2)
        actual_net_profit = round(min_return - base_bankroll, 2)
        net_profit_pct = round((actual_net_profit / base_bankroll) * 100.0, 2)
        is_pure_surebet = (actual_net_profit > self.min_profit) and is_fresh

        comm1 = self.get_leg_commission(leg1)
        comm2 = self.get_leg_commission(leg2)

        gross_payout_1 = round(best_round_s1 * o1, 2)
        gross_payout_2 = round(best_round_s2 * o2, 2)

        return {
            "type": "2-WAY",
            "market_name": market_name,
            "matchup": match_info.get("matchup", "Partit"),
            "date": match_info.get("date", "Pendent"),
            "competition_id": match_info.get("competition_id", ""),
            "competition_name": match_info.get("competition_name", ""),
            "is_surebet": is_pure_surebet,
            "status": "SUREBET" if is_pure_surebet else "NEAR_ARB",
            "profit_pct": net_profit_pct,
            "gross_profit_pct": gross_profit_pct,
            "net_profit_pct": net_profit_pct,
            "margin_pct": margin_pct,
            "base_investment": base_bankroll,
            "net_guaranteed_profit": actual_net_profit,
            "min_guaranteed_return": min_return,
            "scenario_total_returns": [ret1, ret2],
            "scenario_net_pnls": [round(pnl_scenario_0, 2), round(pnl_scenario_1, 2)],
            "skew_seconds": round(skew_found, 1),
            "freshness_valid": is_fresh,
            "freshness_reason": fresh_reason,
            "legs": [
                {
                    "bookmaker": leg1["bookmaker"],
                    "selection": leg1.get("selection", ""),
                    "odd": o1,
                    "leg_id": leg1_id,
                    "commission_rate": comm1,
                    "commission_model": self.get_exchange_profile(leg1)["commission_model"],
                    "settlement_key": self.get_settlement_key(leg1),
                    "stake_pct": float(best_round_s1),
                    "stake_rounded": int(best_round_s1),
                    "gross_payout": gross_payout_1,
                    "expected_return": ret1,  # Retorn global de cartera en escenari 1 (compatibilitat)
                    "captured_at": leg1.get("captured_at"),
                    "url": leg1.get("url", "")
                },
                {
                    "bookmaker": leg2["bookmaker"],
                    "selection": leg2.get("selection", ""),
                    "odd": o2,
                    "leg_id": leg2_id,
                    "commission_rate": comm2,
                    "commission_model": self.get_exchange_profile(leg2)["commission_model"],
                    "settlement_key": self.get_settlement_key(leg2),
                    "stake_pct": float(best_round_s2),
                    "stake_rounded": int(best_round_s2),
                    "gross_payout": gross_payout_2,
                    "expected_return": ret2,  # Retorn global de cartera en escenari 2 (compatibilitat)
                    "captured_at": leg2.get("captured_at"),
                    "url": leg2.get("url", "")
                }
            ]
        }

    def calculate_3way_arbitrage(
        self,
        leg1: Dict[str, Any],
        legX: Dict[str, Any],
        leg2: Dict[str, Any],
        match_info: Dict[str, Any],
        now: Optional[datetime] = None,
        bankroll: float = 100.0
    ) -> Optional[Dict[str, Any]]:
        """Calcula l'arbitratge de 3 vies (1X2) creuant Winamax i Bet365."""
        o1 = float(leg1.get("odd") or 0.0)
        ox = float(legX.get("odd") or 0.0)
        o2 = float(leg2.get("odd") or 0.0)

        if o1 <= 1.01 or ox <= 1.01 or o2 <= 1.01 or o1 > 15.0 or ox > 15.0 or o2 > 15.0:
            return None

        # Si les 3 quotes provenen exactament de la mateixa casa, mai no hi haurà arbitratge (overround de la casa)
        bookies = {leg1["bookmaker"], legX["bookmaker"], leg2["bookmaker"]}
        if len(bookies) < 2:
            return None

        # Validació de frescor estricta i decalatge temporal
        is_fresh, fresh_reason, skew_found = self.validate_legs_freshness([leg1, legX, leg2], now=now)
        if not is_fresh and self.enforce_strict_freshness:
            return None

        sum_inv = (1.0 / o1) + (1.0 / ox) + (1.0 / o2)
        if sum_inv <= 0.0:
            return None

        gross_profit_pct = round(((1.0 / sum_inv) - 1.0) * 100.0, 2)
        margin_pct = round(sum_inv * 100.0, 2)

        # Filtre anti errors palpables o quotes corruptes (>15% és irreal pre-partit)
        if gross_profit_pct < self.near_arb_limit or gross_profit_pct > self.max_profit:
            return None

        # Assignar leg_id deterministes
        leg1_id = self.generate_leg_id(0, str(leg1.get("bookmaker", "")), str(leg1.get("selection", "")), o1)
        legX_id = self.generate_leg_id(1, str(legX.get("bookmaker", "")), str(legX.get("selection", "")), ox)
        leg2_id = self.generate_leg_id(2, str(leg2.get("bookmaker", "")), str(leg2.get("selection", "")), o2)
        leg1 = dict(leg1, leg_id=leg1_id, _leg_index=0)
        legX = dict(legX, leg_id=legX_id, _leg_index=1)
        leg2 = dict(leg2, leg_id=leg2_id, _leg_index=2)
        all_legs = [leg1, legX, leg2]

        comm1 = self.get_leg_commission(leg1)
        commx = self.get_leg_commission(legX)
        comm2 = self.get_leg_commission(leg2)

        base_bankroll = float(self.normalize_bankroll(bankroll, min_legs=3))

        # Optimització exhaustiva global de stakes enters sobre el bankroll seleccionat
        # Cerca exhaustiva dels punts de la xarxa d'enters positius per a bankroll B:
        # s1 >= 1, sx >= 1, s2 >= 1, s1 + sx + s2 = B.
        # Això garanteix matemàticament l'òptim global dins l'espai discret simplex S_3
        # sense cap aproximació heurística ni filtre previ de radi.
        #
        # Jerarquia determinista d'objectius per a la selecció:
        # 1. Objectiu primari: Maximitzar el PnL mínim garantit entre els 3 escenaris (min(p0, px, p2)).
        # 2. Desempat 1 (secundari): Maximitzar el PnL mitjà dels 3 escenaris ((p0 + px + p2) / 3.0).
        # 3. Desempat 2 (terciari): Minimitzar la variància de PnL entre escenaris per a màxim equilibri de retorns.
        # 4. Desempat 3 (quaternari): Ordre determinista lexicogràfic sobre la distribució (t1, tx, t2).
        profiles = [self.get_exchange_profile(l) for l in all_legs]
        settlement_keys = [
            self.get_settlement_key(dict(l, _leg_index=l.get("_leg_index", i)))
            for i, l in enumerate(all_legs)
        ]

        b_int = int(base_bankroll)
        best_round_s1 = max(1, b_int // 3)
        best_round_sx = max(1, b_int // 3)
        best_round_s2 = max(1, b_int - best_round_s1 - best_round_sx)
        best_min_pnl = -float("inf")
        best_avg_pnl = -float("inf")
        best_var_pnl = float("inf")

        for t1 in range(1, b_int - 1):
            max_tx = b_int - t1
            for tx in range(1, max_tx):
                t2 = max_tx - tx
                stakes_test = [float(t1), float(tx), float(t2)]
                p0 = self.calculate_scenario_pnl(all_legs, stakes_test, 0, profiles=profiles, settlement_keys=settlement_keys)
                px = self.calculate_scenario_pnl(all_legs, stakes_test, 1, profiles=profiles, settlement_keys=settlement_keys)
                p2 = self.calculate_scenario_pnl(all_legs, stakes_test, 2, profiles=profiles, settlement_keys=settlement_keys)

                min_pnl = min(p0, px, p2)
                avg_pnl = (p0 + px + p2) / 3.0
                var_pnl = (p0 - avg_pnl) ** 2 + (px - avg_pnl) ** 2 + (p2 - avg_pnl) ** 2

                if min_pnl > best_min_pnl + 1e-6:
                    best_min_pnl = min_pnl
                    best_avg_pnl = avg_pnl
                    best_var_pnl = var_pnl
                    best_round_s1 = t1
                    best_round_sx = tx
                    best_round_s2 = t2
                elif abs(min_pnl - best_min_pnl) <= 1e-6:
                    if avg_pnl > best_avg_pnl + 1e-6:
                        best_min_pnl = min_pnl
                        best_avg_pnl = avg_pnl
                        best_var_pnl = var_pnl
                        best_round_s1 = t1
                        best_round_sx = tx
                        best_round_s2 = t2
                    elif abs(avg_pnl - best_avg_pnl) <= 1e-6:
                        if var_pnl < best_var_pnl - 1e-6:
                            best_min_pnl = min_pnl
                            best_avg_pnl = avg_pnl
                            best_var_pnl = var_pnl
                            best_round_s1 = t1
                            best_round_sx = tx
                            best_round_s2 = t2

        final_stakes = [float(best_round_s1), float(best_round_sx), float(best_round_s2)]
        pnl_s0 = self.calculate_scenario_pnl(all_legs, final_stakes, 0, profiles=profiles, settlement_keys=settlement_keys)
        pnl_sx = self.calculate_scenario_pnl(all_legs, final_stakes, 1, profiles=profiles, settlement_keys=settlement_keys)
        pnl_s2 = self.calculate_scenario_pnl(all_legs, final_stakes, 2, profiles=profiles, settlement_keys=settlement_keys)

        ret1 = round(base_bankroll + pnl_s0, 2)
        retx = round(base_bankroll + pnl_sx, 2)
        ret2 = round(base_bankroll + pnl_s2, 2)

        min_return = min(ret1, retx, ret2)
        actual_net_profit = round(min_return - base_bankroll, 2)
        net_profit_pct = round((actual_net_profit / base_bankroll) * 100.0, 2)
        is_pure_surebet = (actual_net_profit > self.min_profit) and is_fresh

        gross_payout_1 = round(best_round_s1 * o1, 2)
        gross_payout_x = round(best_round_sx * ox, 2)
        gross_payout_2 = round(best_round_s2 * o2, 2)

        return {
            "type": "3-WAY",
            "market_name": "1X2 Cross-Bookmaker",
            "matchup": match_info.get("matchup", "Partit"),
            "date": match_info.get("date", "Pendent"),
            "competition_id": match_info.get("competition_id", ""),
            "competition_name": match_info.get("competition_name", ""),
            "is_surebet": is_pure_surebet,
            "status": "SUREBET" if is_pure_surebet else "NEAR_ARB",
            "profit_pct": net_profit_pct,
            "gross_profit_pct": gross_profit_pct,
            "net_profit_pct": net_profit_pct,
            "margin_pct": margin_pct,
            "base_investment": base_bankroll,
            "net_guaranteed_profit": actual_net_profit,
            "min_guaranteed_return": min_return,
            "scenario_total_returns": [ret1, retx, ret2],
            "scenario_net_pnls": [round(pnl_s0, 2), round(pnl_sx, 2), round(pnl_s2, 2)],
            "skew_seconds": round(skew_found, 1),
            "freshness_valid": is_fresh,
            "freshness_reason": fresh_reason,
            "legs": [
                {
                    "bookmaker": leg1["bookmaker"],
                    "selection": leg1.get("selection", ""),
                    "odd": o1,
                    "leg_id": leg1_id,
                    "commission_rate": comm1,
                    "commission_model": self.get_exchange_profile(leg1)["commission_model"],
                    "settlement_key": self.get_settlement_key(leg1),
                    "stake_pct": float(best_round_s1),
                    "stake_rounded": int(best_round_s1),
                    "gross_payout": gross_payout_1,
                    "expected_return": ret1,  # Retorn global de cartera en escenari 1 (compatibilitat)
                    "captured_at": leg1.get("captured_at"),
                    "url": leg1.get("url", "")
                },
                {
                    "bookmaker": legX["bookmaker"],
                    "selection": legX.get("selection", ""),
                    "odd": ox,
                    "leg_id": legX_id,
                    "commission_rate": commx,
                    "commission_model": self.get_exchange_profile(legX)["commission_model"],
                    "settlement_key": self.get_settlement_key(legX),
                    "stake_pct": float(best_round_sx),
                    "stake_rounded": int(best_round_sx),
                    "gross_payout": gross_payout_x,
                    "expected_return": retx,  # Retorn global de cartera en escenari 2 (compatibilitat)
                    "captured_at": legX.get("captured_at"),
                    "url": legX.get("url", "")
                },
                {
                    "bookmaker": leg2["bookmaker"],
                    "selection": leg2.get("selection", ""),
                    "odd": o2,
                    "leg_id": leg2_id,
                    "commission_rate": comm2,
                    "commission_model": self.get_exchange_profile(leg2)["commission_model"],
                    "settlement_key": self.get_settlement_key(leg2),
                    "stake_pct": float(best_round_s2),
                    "stake_rounded": int(best_round_s2),
                    "gross_payout": gross_payout_2,
                    "expected_return": ret2,  # Retorn global de cartera en escenari 3 (compatibilitat)
                    "captured_at": leg2.get("captured_at"),
                    "url": leg2.get("url", "")
                }
            ]
        }

    def optimize_stakes_for_bankroll(self, legs: List[Dict[str, Any]], bankroll: float = 100.0) -> Dict[str, Any]:
        """
        Calcula els stakes enters exactes òptims per a qualsevol bankroll i avalua
        el PnL net real de cada escenari aplicant la liquidació i comissions d'exchange reals.
        Garanteix que:
        1. Els stakes recomanats són enters positius (>= 1 €) que sumen exactament el bankroll.
        2. El benefici garantit és exactament el mínim dels PnL nets de cada escenari.
        3. El retorn global és coherent amb el capital invertit (Bankroll + Min PnL).
        """
        k = len(legs)
        if k < 2 or k > 3:
            raise ValueError("Només s'admeten oportunitats de 2 o 3 potes")

        B = self.normalize_bankroll(bankroll, min_legs=k)
        legs_indexed = [
            dict(
                l,
                _leg_index=l.get("_leg_index", i),
                leg_id=l.get("leg_id") or self.generate_leg_id(
                    i,
                    str(l.get("bookmaker", "")),
                    str(l.get("selection", "")),
                    float(l.get("odd") or 0.0)
                )
            )
            for i, l in enumerate(legs)
        ]
        profiles = [self.get_exchange_profile(l) for l in legs_indexed]
        settlement_keys = [self.get_settlement_key(l) for l in legs_indexed]

        best_stakes = []
        best_min_pnl = -float("inf")
        best_avg_pnl = -float("inf")
        best_var_pnl = float("inf")

        if k == 2:
            for s1 in range(1, B):
                s2 = B - s1
                st = [float(s1), float(s2)]
                p0 = self.calculate_scenario_pnl(legs_indexed, st, 0, profiles=profiles, settlement_keys=settlement_keys)
                p1 = self.calculate_scenario_pnl(legs_indexed, st, 1, profiles=profiles, settlement_keys=settlement_keys)
                m = min(p0, p1)
                avg = (p0 + p1) / 2.0
                var = (p0 - avg) ** 2 + (p1 - avg) ** 2
                if m > best_min_pnl + 1e-6:
                    best_min_pnl, best_avg_pnl, best_var_pnl, best_stakes = m, avg, var, [s1, s2]
                elif abs(m - best_min_pnl) <= 1e-6:
                    if avg > best_avg_pnl + 1e-6:
                        best_min_pnl, best_avg_pnl, best_var_pnl, best_stakes = m, avg, var, [s1, s2]
                    elif abs(avg - best_avg_pnl) <= 1e-6:
                        if var < best_var_pnl - 1e-6:
                            best_min_pnl, best_avg_pnl, best_var_pnl, best_stakes = m, avg, var, [s1, s2]
        else:  # k == 3
            for s1 in range(1, B - 1):
                max_s2 = B - s1
                for s2 in range(1, max_s2):
                    s3 = max_s2 - s2
                    st = [float(s1), float(s2), float(s3)]
                    p0 = self.calculate_scenario_pnl(legs_indexed, st, 0, profiles=profiles, settlement_keys=settlement_keys)
                    px = self.calculate_scenario_pnl(legs_indexed, st, 1, profiles=profiles, settlement_keys=settlement_keys)
                    p2 = self.calculate_scenario_pnl(legs_indexed, st, 2, profiles=profiles, settlement_keys=settlement_keys)
                    m = min(p0, px, p2)
                    avg = (p0 + px + p2) / 3.0
                    var = (p0 - avg) ** 2 + (px - avg) ** 2 + (p2 - avg) ** 2
                    if m > best_min_pnl + 1e-6:
                        best_min_pnl, best_avg_pnl, best_var_pnl, best_stakes = m, avg, var, [s1, s2, s3]
                    elif abs(m - best_min_pnl) <= 1e-6:
                        if avg > best_avg_pnl + 1e-6:
                            best_min_pnl, best_avg_pnl, best_var_pnl, best_stakes = m, avg, var, [s1, s2, s3]
                        elif abs(avg - best_avg_pnl) <= 1e-6:
                            if var < best_var_pnl - 1e-6:
                                best_min_pnl, best_avg_pnl, best_var_pnl, best_stakes = m, avg, var, [s1, s2, s3]

        final_st = [float(s) for s in best_stakes]
        scenario_pnls = [
            round(self.calculate_scenario_pnl(legs_indexed, final_st, s, profiles=profiles, settlement_keys=settlement_keys), 2)
            for s in range(k)
        ]
        scenario_total_returns = [round(B + p, 2) for p in scenario_pnls]
        min_return = min(scenario_total_returns)
        net_profit = round(min(scenario_pnls), 2)
        profit_pct = round((net_profit / float(B)) * 100.0, 2)
        gross_payouts = [round(best_stakes[i] * float(legs_indexed[i].get("odd") or 0.0), 2) for i in range(k)]

        return {
            "bankroll": float(B),
            "round_stakes": best_stakes,
            "gross_payouts": gross_payouts,
            "scenario_pnls": scenario_pnls,
            "scenario_total_returns": scenario_total_returns,
            "min_guaranteed_return": min_return,
            "net_guaranteed_profit": net_profit,
            "profit_pct": profit_pct,
            "is_positive": net_profit > 0
        }

    def scan_match(self, match: Dict[str, Any], now: Optional[datetime] = None, bankroll: float = 100.0) -> List[Dict[str, Any]]:
        """Analitza tots els mercats d'un partit i retorna totes les oportunitats d'arbitratge."""
        arbs = []

        # No avaluar partits ja jugats o suspesos
        if match.get("status") in ["FINISHED", "SUSPENDED"]:
            return []

        h_name = match.get("home_team", {}).get("name", "Local")
        a_name = match.get("away_team", {}).get("name", "Visitant")
        match_info = {
            "matchup": f"{h_name} vs {a_name}",
            "date": match.get("date", "Pendent"),
            "competition_id": match.get("competition_id", ""),
            "competition_name": match.get("competition_name", "")
        }

        # Helper per extreure timestamp i URL de qualsevol proveïdor
        def _get_provider_info(p_name: str) -> Tuple[Optional[str], str]:
            p = p_name.lower().strip()
            url = match.get(f"url_{p}")
            if not url:
                if "winamax" in p: url = "https://www.winamax.es"
                elif "bet365" in p: url = "https://www.bet365.es/#/AS/B1/"
                elif "betway" in p: url = "https://betway.es"
                elif "betfair" in p: url = "https://www.betfair.es/exchange/"
                elif "matchbook" in p: url = "https://www.matchbook.com"
                elif "smarkets" in p: url = "https://www.smarkets.com"
                else: url = f"https://www.{p}.com"

            sub = match.get(f"odds_{p}") or {}
            cap = (
                sub.get("captured_at") or sub.get("polled_at") or
                match.get(f"odds_{p}_captured_at") or match.get("captured_at")
            )
            return cap, url

        # 1. 1X2: Descobriment de tots els proveïdors disponibles (Winamax, Bet365, Betway, Exchanges)
        providers_1x2: Dict[str, Dict[str, float]] = {}
        for k, v in match.items():
            if k.startswith("odds_1x2_") and isinstance(v, dict):
                p_name = k[len("odds_1x2_"):].strip()
                p_label = "Winamax" if p_name == "winamax" else ("Bet365" if p_name == "bet365" else ("Betway" if p_name == "betway" else p_name.capitalize()))
                providers_1x2[p_label] = {
                    "1": float(v.get("1") or 0.0),
                    "X": float(v.get("X") or 0.0),
                    "2": float(v.get("2") or 0.0)
                }

        # Trobar les millors quotes per a 1, X, 2 creuant tots els proveïdors
        best_leg_1: Optional[Dict[str, Any]] = None
        best_leg_X: Optional[Dict[str, Any]] = None
        best_leg_2: Optional[Dict[str, Any]] = None

        for bk, odds_dict in providers_1x2.items():
            cap, url = _get_provider_info(bk)
            mid = match.get(f"market_id_1x2_{bk.lower()}")

            o1 = odds_dict.get("1", 0.0)
            if o1 > 1.0 and (best_leg_1 is None or o1 > best_leg_1["odd"]):
                best_leg_1 = {
                    "bookmaker": bk,
                    "selection": f"1 - Victòria {h_name}",
                    "odd": o1,
                    "captured_at": cap,
                    "url": url,
                    "native_market_id": mid
                }

            ox = odds_dict.get("X", 0.0)
            if ox > 1.0 and (best_leg_X is None or ox > best_leg_X["odd"]):
                best_leg_X = {
                    "bookmaker": bk,
                    "selection": "X - Empat",
                    "odd": ox,
                    "captured_at": cap,
                    "url": url,
                    "native_market_id": mid
                }

            o2 = odds_dict.get("2", 0.0)
            if o2 > 1.0 and (best_leg_2 is None or o2 > best_leg_2["odd"]):
                best_leg_2 = {
                    "bookmaker": bk,
                    "selection": f"2 - Victòria {a_name}",
                    "odd": o2,
                    "captured_at": cap,
                    "url": url,
                    "native_market_id": mid
                }

        if best_leg_1 and best_leg_X and best_leg_2:
            arb_1x2 = self.calculate_3way_arbitrage(best_leg_1, best_leg_X, best_leg_2, match_info, now=now, bankroll=bankroll)
            if arb_1x2:
                arbs.append(arb_1x2)

        # 2. Doble Oportunitat vs Guanyador Simple (2-way)
        providers_dc: Dict[str, Dict[str, float]] = {}
        for k, v in match.items():
            if k.startswith("odds_dc_") and isinstance(v, dict):
                p_name = k[len("odds_dc_"):].strip()
                p_label = "Winamax" if p_name == "winamax" else ("Bet365" if p_name == "bet365" else ("Betway" if p_name == "betway" else p_name.capitalize()))
                providers_dc[p_label] = {
                    "1X": float(v.get("1X") or 0.0),
                    "X2": float(v.get("X2") or 0.0),
                    "12": float(v.get("12") or 0.0)
                }

        # Avaluem totes les combinacions creuades entre cases diferents
        for bk_dc, dc_odds in providers_dc.items():
            cap_dc, url_dc = _get_provider_info(bk_dc)
            mid_dc = match.get(f"market_id_dc_{bk_dc.lower()}")

            for bk_1x2, single_odds in providers_1x2.items():
                if bk_dc == bk_1x2:
                    continue  # Mateixa casa mai no constitueix arbitratge
                cap_single, url_single = _get_provider_info(bk_1x2)
                mid_single = match.get(f"market_id_1x2_{bk_1x2.lower()}")

                # 1X vs 2
                o_1x = dc_odds.get("1X", 0.0)
                o_2 = single_odds.get("2", 0.0)
                if o_1x > 1.0 and o_2 > 1.0:
                    arb = self.calculate_2way_arbitrage(
                        {"bookmaker": bk_dc, "selection": f"1X - {h_name} o Empat", "odd": o_1x, "captured_at": cap_dc, "url": url_dc, "native_market_id": mid_dc},
                        {"bookmaker": bk_1x2, "selection": f"2 - Victòria {a_name}", "odd": o_2, "captured_at": cap_single, "url": url_single, "native_market_id": mid_single},
                        "Doble Oportunitat (1X vs 2)",
                        match_info,
                        now=now,
                        bankroll=bankroll
                    )
                    if arb: arbs.append(arb)

                # X2 vs 1
                o_x2 = dc_odds.get("X2", 0.0)
                o_1 = single_odds.get("1", 0.0)
                if o_x2 > 1.0 and o_1 > 1.0:
                    arb = self.calculate_2way_arbitrage(
                        {"bookmaker": bk_dc, "selection": f"X2 - Empat o {a_name}", "odd": o_x2, "captured_at": cap_dc, "url": url_dc, "native_market_id": mid_dc},
                        {"bookmaker": bk_1x2, "selection": f"1 - Victòria {h_name}", "odd": o_1, "captured_at": cap_single, "url": url_single, "native_market_id": mid_single},
                        "Doble Oportunitat (X2 vs 1)",
                        match_info,
                        now=now,
                        bankroll=bankroll
                    )
                    if arb: arbs.append(arb)

                # 12 vs X
                o_12 = dc_odds.get("12", 0.0)
                o_x = single_odds.get("X", 0.0)
                if o_12 > 1.0 and o_x > 1.0:
                    arb = self.calculate_2way_arbitrage(
                        {"bookmaker": bk_dc, "selection": f"12 - {h_name} o {a_name}", "odd": o_12, "captured_at": cap_dc, "url": url_dc, "native_market_id": mid_dc},
                        {"bookmaker": bk_1x2, "selection": "X - Empat", "odd": o_x, "captured_at": cap_single, "url": url_single, "native_market_id": mid_single},
                        "Doble Oportunitat (12 vs X)",
                        match_info,
                        now=now,
                        bankroll=bankroll
                    )
                    if arb: arbs.append(arb)

        # 3. Over / Under 2.5 Gols
        providers_ou: Dict[str, Dict[str, float]] = {}
        for k, v in match.items():
            if (k.startswith("odds_ou25_") or k.startswith("odds_ou_")) and isinstance(v, dict):
                p_name = k.replace("odds_ou25_", "").replace("odds_ou_", "").strip()
                p_label = "Winamax" if p_name == "winamax" else ("Bet365" if p_name == "bet365" else ("Betway" if p_name == "betway" else p_name.capitalize()))
                providers_ou[p_label] = {
                    "over": float(v.get("over") or 0.0),
                    "under": float(v.get("under") or 0.0)
                }

        ou_bookies = list(providers_ou.keys())
        for i in range(len(ou_bookies)):
            for j in range(len(ou_bookies)):
                if i == j:
                    continue
                bk_a = ou_bookies[i]
                bk_b = ou_bookies[j]
                cap_a, url_a = _get_provider_info(bk_a)
                cap_b, url_b = _get_provider_info(bk_b)
                mid_a = match.get(f"market_id_ou25_{bk_a.lower()}") or match.get(f"market_id_ou_{bk_a.lower()}")
                mid_b = match.get(f"market_id_ou25_{bk_b.lower()}") or match.get(f"market_id_ou_{bk_b.lower()}")

                o_over = providers_ou[bk_a].get("over", 0.0)
                o_under = providers_ou[bk_b].get("under", 0.0)

                if o_over > 1.0 and o_under > 1.0:
                    arb = self.calculate_2way_arbitrage(
                        {"bookmaker": bk_a, "selection": "Més de 2.5 Gols (Over)", "odd": o_over, "captured_at": cap_a, "url": url_a, "native_market_id": mid_a},
                        {"bookmaker": bk_b, "selection": "Menys de 2.5 Gols (Under)", "odd": o_under, "captured_at": cap_b, "url": url_b, "native_market_id": mid_b},
                        "Total Gols (Més/Menys 2.5)",
                        match_info,
                        now=now,
                        bankroll=bankroll
                    )
                    if arb: arbs.append(arb)

        # 4. Ambdós Equips Marquen (BTTS)
        providers_btts: Dict[str, Dict[str, float]] = {}
        for k, v in match.items():
            if k.startswith("odds_btts_") and isinstance(v, dict):
                p_name = k[len("odds_btts_"):].strip()
                p_label = "Winamax" if p_name == "winamax" else ("Bet365" if p_name == "bet365" else ("Betway" if p_name == "betway" else p_name.capitalize()))
                providers_btts[p_label] = {
                    "yes": float(v.get("yes") or 0.0),
                    "no": float(v.get("no") or 0.0)
                }

        btts_bookies = list(providers_btts.keys())
        for i in range(len(btts_bookies)):
            for j in range(len(btts_bookies)):
                if i == j:
                    continue
                bk_a = btts_bookies[i]
                bk_b = btts_bookies[j]
                cap_a, url_a = _get_provider_info(bk_a)
                cap_b, url_b = _get_provider_info(bk_b)
                mid_a = match.get(f"market_id_btts_{bk_a.lower()}")
                mid_b = match.get(f"market_id_btts_{bk_b.lower()}")

                o_yes = providers_btts[bk_a].get("yes", 0.0)
                o_no = providers_btts[bk_b].get("no", 0.0)

                if o_yes > 1.0 and o_no > 1.0:
                    arb = self.calculate_2way_arbitrage(
                        {"bookmaker": bk_a, "selection": "Ambdós Marquen: SÍ", "odd": o_yes, "captured_at": cap_a, "url": url_a, "native_market_id": mid_a},
                        {"bookmaker": bk_b, "selection": "Ambdós Marquen: NO", "odd": o_no, "captured_at": cap_b, "url": url_b, "native_market_id": mid_b},
                        "Ambdós Marquen (BTTS)",
                        match_info,
                        now=now,
                        bankroll=bankroll
                    )
                    if arb: arbs.append(arb)

        return arbs

    def scan_all_matches(self, matches: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Escaneja una llista de partits i retorna l'informe complet d'arbitratge."""
        all_arbs = []
        for m in matches:
            found = self.scan_match(m)
            all_arbs.extend(found)

        # Ordenar de major a menor benefici garantit (% Profit)
        all_arbs.sort(key=lambda x: x.get("profit_pct", -999.0), reverse=True)

        surebets = [a for a in all_arbs if a["is_surebet"]]
        near_arbs = [a for a in all_arbs if not a["is_surebet"]]

        # Estadístiques globals
        max_p = surebets[0]["profit_pct"] if surebets else (near_arbs[0]["profit_pct"] if near_arbs else 0.0)
        avg_p = round(sum(a["profit_pct"] for a in surebets) / len(surebets), 2) if surebets else 0.0

        return {
            "summary": {
                "total_surebets": len(surebets),
                "total_near_arbs": len(near_arbs),
                "max_profit_pct": max_p,
                "avg_profit_pct": avg_p,
                "evaluated_matches": len(matches)
            },
            "surebets": surebets,
            "near_arbs": near_arbs[:10], # Mostrar les 10 oportunitats més properes a arbitratge
            "all_opportunities": all_arbs
        }
