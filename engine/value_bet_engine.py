"""
engine/value_bet_engine.py
==========================
Motor d'anàlisi de valor (+EV%) i detecció d'apostes rendibles.
Avalua ÚNICAMENT les cuotes que realment estan publicades a Winamax.
"""

from __future__ import annotations

import re
import math
from typing import Dict, Any, List, Optional, Iterable
from scipy.stats import poisson

class ValueBetEngine:
    def __init__(self, kelly_fraction: float = 0.25):
        self.kelly_fraction = kelly_fraction

    def calculate_market_value(
        self,
        model_prob: float,
        bookie_odd: float,
        push_prob: float = 0.0,
        loss_prob: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Calcula el valor esperat (+EV%), quota justa i percentatge Kelly,
        amb suport complet per a mercats binaris, mercats europeus de 3 opcions
        i mercats asiàtics amb probabilitat de devolució (push).
        Fórmula EV amb push: EV = (P_win * odds + P_push - 1.0) * 100.0
        Quota justa amb push: Q_fair = (1.0 - P_push) / P_win
        Kelly amb push: f* = (b * P_win - P_loss) / (b * (P_win + P_loss))
        """
        if bookie_odd is None or bookie_odd <= 1.0 or model_prob <= 0.0:
            return {
                "model_prob": round(model_prob * 100, 1),
                "push_prob": round(push_prob * 100, 1),
                "loss_prob": round((1.0 - model_prob - push_prob) * 100, 1),
                "bookie_odd": None,
                "fair_odd": 99.0,
                "implied_prob": 0.0,
                "ev_pct": -100.0,
                "kelly_pct": 0.0,
                "is_value": False,
                "rating": "SENSE VALOR"
            }

        p_w = float(model_prob)
        p_v = float(push_prob)
        p_l = float(loss_prob) if loss_prob is not None else max(0.0, 1.0 - p_w - p_v)
        b = bookie_odd - 1.0

        # Quota justa considerant el retorn de capital en cas de push
        fair_odd = (1.0 - p_v) / p_w if p_w > 0 else 99.0
        implied_prob = ((1.0 - p_v) / bookie_odd) * 100.0 if bookie_odd > 0 else 0.0
        ev_pct = (p_w * bookie_odd + p_v - 1.0) * 100.0

        # Càlcul de Kelly fraccional adaptat a mercats amb devolució (push)
        non_push = p_w + p_l
        if b > 0 and non_push > 0:
            kelly_full = (b * p_w - p_l) / (b * non_push)
        else:
            kelly_full = 0.0
        kelly_stake = max(0.0, kelly_full * self.kelly_fraction * 100.0)

        # Classificació de rendibilitat
        if ev_pct >= 15.0:
            rating = "🔥 EXCEL·LENT (+15% EV)"
            is_value = True
        elif ev_pct >= 5.0:
            rating = "⭐ MOLT BONA (+5% a +15% EV)"
            is_value = True
        elif ev_pct > 0.0:
            rating = "✓ LLEUGER VALOR (0% a +5% EV)"
            is_value = True
        else:
            rating = "❌ SENSE VALOR (Evita)"
            is_value = False

        return {
            "model_prob": round(p_w * 100, 1),
            "push_prob": round(p_v * 100, 1),
            "loss_prob": round(p_l * 100, 1),
            "bookie_odd": round(bookie_odd, 2),
            "fair_odd": round(fair_odd, 2),
            "implied_prob": round(implied_prob, 1),
            "ev_pct": round(ev_pct, 1),
            "kelly_pct": round(kelly_stake, 1),
            "is_value": is_value,
            "rating": rating
        }

    @staticmethod
    def classify_corner_market(c_key: str, available_keys: Optional[Iterable[str]] = None) -> Dict[str, Any]:
        """
        Classificació explícita del mercat de córners i del seu format:
        - EUROPEAN_3WAY: mercat de tres opcions (Over / Exactly / Under). Si X = K, Over perd i Under perd.
        - ASIAN_2WAY: mercat asiàtic de dues opcions amb línia entera K. Si X = K, hi ha devolució (push).
        - HALF_BALL_2WAY: mercat de dues opcions amb línia decimal (ex: 8.5). Sense possibilitat d'empat.
        - UNKNOWN: mercat parcial, d'equip, rang o sense metadades suficients per determinar el format.
        """
        if not c_key or not isinstance(c_key, str):
            return {"market_type": "UNKNOWN", "market_format": "UNKNOWN", "threshold": None, "raw_key": c_key}

        key_low = c_key.lower().strip()

        # 1. Rebutjar mercats parcials, d'equip o handicaps (no representen el total del partit)
        partial_keywords = (
            "1st half", "2nd half", "half", "team corners", "primera parte",
            "segunda parte", "mitja part", "handicap"
        )
        if any(pk in key_low for pk in partial_keywords):
            return {"market_type": "UNKNOWN", "market_format": "UNKNOWN", "threshold": None, "raw_key": c_key}

        # 2. Rebutjar rangs (ex: "Total Corners 6 - 8")
        if re.search(r'\d+\s*-\s*\d+', key_low):
            return {"market_type": "UNKNOWN", "market_format": "UNKNOWN", "threshold": None, "raw_key": c_key}

        # 3. Determinar selecció explícita
        is_exact = "exact" in key_low
        is_under = ("under" in key_low or "menys" in key_low or "menos" in key_low) and not is_exact
        is_over = ("over" in key_low or "més" in key_low or "mes" in key_low or "más" in key_low) and not is_exact

        # Si hi ha conflicte o cap indicador
        if sum([bool(is_exact), bool(is_under), bool(is_over)]) != 1:
            return {"market_type": "UNKNOWN", "market_format": "UNKNOWN", "threshold": None, "raw_key": c_key}

        # 4. Extreure llindar numèric (ex: 6, 6.5, 8, 8.5, 10.0), ignorant '2-way' o '3-way'
        cleaned_key = re.sub(r'\b[23]-way\b', '', key_low)
        m_thresh = re.search(r'(\d+(?:\.\d+)?)', cleaned_key) or re.search(r'(\d+)_(\d+)', cleaned_key)
        if not m_thresh:
            return {"market_type": "UNKNOWN", "market_format": "UNKNOWN", "threshold": None, "raw_key": c_key}

        thresh = float(f"{m_thresh.group(1)}.{m_thresh.group(2)}") if m_thresh.lastindex == 2 else float(m_thresh.group(1))
        if thresh < 4.5 or thresh > 16.5:
            return {"market_type": "UNKNOWN", "market_format": "UNKNOWN", "threshold": None, "raw_key": c_key}

        is_integer = (thresh == int(thresh))

        # 5. Identificació explícita del format del mercat
        if not is_integer:
            market_format = "HALF_BALL_2WAY"
        else:
            # Línia entera: cal distingir ASIAN_2WAY de EUROPEAN_3WAY o declarar UNKNOWN
            if "asian" in key_low or "asiat" in key_low:
                market_format = "ASIAN_2WAY"
            elif "2-way" in key_low or "2 way" in key_low:
                market_format = "ASIAN_2WAY"
            elif "3-way" in key_low or "3 way" in key_low:
                market_format = "EUROPEAN_3WAY"
            elif "alternative" in key_low:
                market_format = "EUROPEAN_3WAY"
            elif is_exact:
                market_format = "EUROPEAN_3WAY"
            elif available_keys:
                k_int = int(thresh)
                has_companion_exact = any(
                    "exact" in str(k).lower() and re.search(rf'\b{k_int}\b', str(k))
                    for k in available_keys
                )
                if has_companion_exact:
                    market_format = "EUROPEAN_3WAY"
                else:
                    # Si no hi ha opció Exactly ni indicadors explícits, és UNKNOWN per seguretat
                    market_format = "UNKNOWN"
            else:
                # Format desconegut o sense metadades suficients: no deduir a cegues
                market_format = "UNKNOWN"

        if market_format == "UNKNOWN":
            m_type = "UNKNOWN"
        elif is_exact:
            m_type = "EXACT"
        elif is_under:
            m_type = "UNDER"
        elif is_over:
            m_type = "OVER"
        else:
            m_type = "UNKNOWN"

        return {
            "market_type": m_type,
            "market_format": market_format,
            "threshold": thresh,
            "is_integer": is_integer,
            "raw_key": c_key
        }

    @staticmethod
    def calculate_corner_probabilities(
        market_type: str,
        threshold: float,
        lambda_corn: float,
        market_format: str = "EUROPEAN_3WAY"
    ) -> Optional[Dict[str, float]]:
        """
        Calcula exactament P_win, P_push i P_loss segons la semàntica del mercat:
        - HALF_BALL_2WAY (ex: 8.5): P_push = 0.0. P_win = 1 - cdf(8), P_loss = cdf(8).
        - EUROPEAN_3WAY amb enter K (ex: Over 6): Si X = K, Over perd (Exactament guanya).
          P_push = 0.0. P_win = 1 - cdf(K), P_loss = cdf(K) = P(X < K) + P(X = K).
        - ASIAN_2WAY amb enter K (ex: Over 10.0): Si X = K, hi ha devolució (Push).
          P_win = 1 - cdf(K), P_push = pmf(K), P_loss = cdf(K - 1).
          P_win + P_push + P_loss = 1.0 rigorosament.
        """
        if lambda_corn <= 0 or threshold <= 0:
            return None

        is_int = (threshold == int(threshold))
        k = int(threshold)

        if not is_int or market_format == "HALF_BALL_2WAY":
            floor_k = int(math.floor(threshold))
            p_over = float(1.0 - poisson.cdf(floor_k, lambda_corn))
            p_under = float(poisson.cdf(floor_k, lambda_corn))
            if market_type == "OVER":
                return {"p_win": p_over, "p_push": 0.0, "p_loss": p_under}
            elif market_type == "UNDER":
                return {"p_win": p_under, "p_push": 0.0, "p_loss": p_over}
            else:
                return None

        # Llindar enter
        p_over = float(1.0 - poisson.cdf(k, lambda_corn))       # P(X > K)
        p_exact = float(poisson.pmf(k, lambda_corn))            # P(X = K)
        p_under = float(poisson.cdf(k - 1, lambda_corn)) if k > 0 else 0.0  # P(X < K)

        if market_format == "EUROPEAN_3WAY":
            if market_type == "OVER":
                return {"p_win": p_over, "p_push": 0.0, "p_loss": p_exact + p_under}
            elif market_type == "UNDER":
                return {"p_win": p_under, "p_push": 0.0, "p_loss": p_exact + p_over}
            elif market_type == "EXACT":
                return {"p_win": p_exact, "p_push": 0.0, "p_loss": p_over + p_under}
            else:
                return None
        elif market_format == "ASIAN_2WAY":
            if market_type == "OVER":
                return {"p_win": p_over, "p_push": p_exact, "p_loss": p_under}
            elif market_type == "UNDER":
                return {"p_win": p_under, "p_push": p_exact, "p_loss": p_over}
            else:
                return None
        else:
            return None

    @staticmethod
    def calculate_corner_probability(
        market_type: str,
        threshold: float,
        lambda_corn: float,
        market_format: str = "EUROPEAN_3WAY"
    ) -> Optional[float]:
        """Calcula la probabilitat de guanyar per a un mercat de córners (mantenint compatibilitat)."""
        probs = ValueBetEngine.calculate_corner_probabilities(market_type, threshold, lambda_corn, market_format)
        return probs["p_win"] if probs else None

    @classmethod
    def _find_matching_corner_odd(
        cls,
        corner_dict: Dict[str, Any],
        target_type: str,
        target_thresh: float,
        target_format: str = "HALF_BALL_2WAY"
    ) -> Optional[float]:
        """Cerca a un diccionari de córners una quota que coincideixi exactament en tipus, llindar i format semàntic."""
        if not corner_dict or not isinstance(corner_dict, dict):
            return None
        for k, v in corner_dict.items():
            if not v or v <= 1.0:
                continue
            parsed = cls.classify_corner_market(k, available_keys=corner_dict.keys())
            if parsed["market_type"] == target_type and parsed["threshold"] is not None:
                if abs(parsed["threshold"] - target_thresh) < 0.01:
                    # Validació d'integritat semàntica: no emparellar un mercat asiàtic amb devolució
                    # amb un mercat europeu de 3 opcions sense devolució!
                    if parsed["market_format"] == target_format or (target_format == "HALF_BALL_2WAY" and parsed["market_format"] == "HALF_BALL_2WAY"):
                        return float(v)
        return None

    @classmethod
    def _find_matching_card_odd(cls, card_dict: Dict[str, Any], is_under: bool, target_thresh: float) -> Optional[float]:
        """Cerca a un diccionari de targetes una quota que coincideixi en direcció i llindar."""
        if not card_dict or not isinstance(card_dict, dict):
            return None
        for k, v in card_dict.items():
            if not v or v <= 1.0:
                continue
            k_low = k.lower()
            b_is_under = "under" in k_low or "menys" in k_low or "menos" in k_low
            m = re.search(r'(\d+(?:\.\d+)?)', k)
            if m and b_is_under == is_under:
                th = float(m.group(1))
                if abs(th - target_thresh) < 0.01:
                    return float(v)
        return None

    def analyze_match_betting(
        self,
        model_prediction: Dict[str, Any],
        winamax_odds: Dict[str, Any],
        bet365_odds: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Analitza els mercats del partit combinant de forma híbrida les quotes oficials
        disponibles a Winamax i Bet365. Si una casa no té publicat un mercat (o encara no
        n'ha obert la línia), s'utilitza l'altra casa per oferir totes les opcions reals.
        """
        winamax_odds = winamax_odds or {}
        bet365_odds = bet365_odds or {}

        w_is_stale = bool(winamax_odds.get("is_stale")) or (winamax_odds.get("freshness") in ("STALE", "UNKNOWN"))
        w_freshness = winamax_odds.get("freshness") or ("UNKNOWN" if w_is_stale else "FRESH")
        w_reason = winamax_odds.get("freshness_reason") or ("winamax_stale_or_unknown" if w_is_stale else "ok")

        b_is_stale = bool(bet365_odds.get("is_stale")) or (bet365_odds.get("freshness") in ("STALE", "UNKNOWN"))
        b_freshness = bet365_odds.get("freshness") or ("UNKNOWN" if b_is_stale else "FRESH")
        b_reason = bet365_odds.get("freshness_reason") or ("bet365_stale_or_unknown" if b_is_stale else "ok")

        p_goals = model_prediction["goals"]
        p_cards = model_prediction["cards"]
        p_corners = model_prediction["corners"]

        prob_1 = p_goals["prob_1X2"]["1"] / 100.0
        prob_x = p_goals["prob_1X2"]["X"] / 100.0
        prob_2 = p_goals["prob_1X2"]["2"] / 100.0

        prob_over25 = p_goals["over_under"].get("over_2_5", 50.0) / 100.0
        prob_under25 = p_goals["over_under"].get("under_2_5", 100.0 - prob_over25 * 100.0) / 100.0

        prob_btts_yes = p_goals["btts"].get("yes", 50.0) / 100.0
        prob_btts_no = p_goals["btts"].get("no", 50.0) / 100.0

        prob_1x = prob_1 + prob_x
        prob_x2 = prob_x + prob_2
        prob_12 = prob_1 + prob_2

        prob_cards_o45 = p_cards["prob_over_cards"].get("over_4_5", 50.0) / 100.0
        prob_cards_u45 = 1.0 - prob_cards_o45
        prob_cards_o55 = p_cards["prob_over_cards"].get("over_5_5", 30.0) / 100.0
        prob_red_card = p_cards.get("prob_red_card", 20.0) / 100.0

        single_markets = []
        h_raw = model_prediction.get("home_team", "Local")
        a_raw = model_prediction.get("away_team", "Visitant")
        h_name = h_raw.get("name", "Local") if isinstance(h_raw, dict) else str(h_raw or "Local")
        a_name = a_raw.get("name", "Visitant") if isinstance(a_raw, dict) else str(a_raw or "Visitant")

        # 1. Mercat 1X2 (Híbrid: Winamax preferent, fallback Bet365)
        o_1x2 = winamax_odds.get("1X2", {})
        b_1x2 = bet365_odds.get("1X2", {})
        w_1 = o_1x2.get("1")
        b_1 = b_1x2.get("1")
        odd_1 = w_1 or b_1

        w_x = o_1x2.get("X")
        b_x = b_1x2.get("X")
        odd_x = w_x or b_x

        w_2 = o_1x2.get("2")
        b_2 = b_1x2.get("2")
        odd_2 = w_2 or b_2

        if odd_1 and odd_1 > 1.0:
            v = self.calculate_market_value(prob_1, odd_1)
            v["name"] = f"1 - Victòria Local ({h_name})"
            v["category"] = "1X2"
            v["bookie"] = "Winamax" if w_1 else "Bet365"
            v["bookie_odd_winamax"] = w_1
            v["bookie_odd_bet365"] = b_1
            v["provenance"] = "WINAMAX" if w_1 else "BET365"
            single_markets.append(v)
        if odd_x and odd_x > 1.0:
            v = self.calculate_market_value(prob_x, odd_x)
            v["name"] = "X - Empat"
            v["category"] = "1X2"
            v["bookie"] = "Winamax" if w_x else "Bet365"
            v["bookie_odd_winamax"] = w_x
            v["bookie_odd_bet365"] = b_x
            v["provenance"] = "WINAMAX" if w_x else "BET365"
            single_markets.append(v)
        if odd_2 and odd_2 > 1.0:
            v = self.calculate_market_value(prob_2, odd_2)
            v["name"] = f"2 - Victòria Visitant ({a_name})"
            v["category"] = "1X2"
            v["bookie"] = "Winamax" if w_2 else "Bet365"
            v["bookie_odd_winamax"] = w_2
            v["bookie_odd_bet365"] = b_2
            v["provenance"] = "WINAMAX" if w_2 else "BET365"
            single_markets.append(v)

        # 2. Doble Oportunitat (Real de Winamax o Bet365)
        o_dc = winamax_odds.get("double_chance", {})
        b_dc = bet365_odds.get("double_chance", {})
        w_1x = o_dc.get("1X")
        b_1x = b_dc.get("1X")
        odd_1x = w_1x or b_1x

        w_x2 = o_dc.get("X2")
        b_x2 = b_dc.get("X2")
        odd_x2 = w_x2 or b_x2

        w_12 = o_dc.get("12")
        b_12 = b_dc.get("12")
        odd_12 = w_12 or b_12

        if odd_1x and odd_1x > 1.0:
            v = self.calculate_market_value(prob_1x, odd_1x)
            v["name"] = f"1X - {h_name} o Empat"
            v["category"] = "Doble Oportunitat"
            v["bookie"] = "Winamax" if w_1x else "Bet365"
            v["bookie_odd_winamax"] = w_1x
            v["bookie_odd_bet365"] = b_1x
            v["provenance"] = "WINAMAX" if w_1x else "BET365"
            single_markets.append(v)
        if odd_x2 and odd_x2 > 1.0:
            v = self.calculate_market_value(prob_x2, odd_x2)
            v["name"] = f"X2 - Empat o {a_name}"
            v["category"] = "Doble Oportunitat"
            v["bookie"] = "Winamax" if w_x2 else "Bet365"
            v["bookie_odd_winamax"] = w_x2
            v["bookie_odd_bet365"] = b_x2
            v["provenance"] = "WINAMAX" if w_x2 else "BET365"
            single_markets.append(v)
        if odd_12 and odd_12 > 1.0:
            v = self.calculate_market_value(prob_12, odd_12)
            v["name"] = f"12 - {h_name} o {a_name}"
            v["category"] = "Doble Oportunitat"
            v["bookie"] = "Winamax" if w_12 else "Bet365"
            v["bookie_odd_winamax"] = w_12
            v["bookie_odd_bet365"] = b_12
            v["provenance"] = "WINAMAX" if w_12 else "BET365"
            single_markets.append(v)

        # 3. Gols Over / Under (1.5, 2.5, 3.5)
        o_goals = winamax_odds.get("goals", {})
        o_ou25 = winamax_odds.get("over_under_2_5", {})
        b_ou25 = bet365_odds.get("over_under_2_5", {})
        b_goals = bet365_odds.get("goals", {})

        w_o25 = o_goals.get("Over 2.5") or o_ou25.get("Over 2.5") or o_ou25.get("over")
        b_o25 = b_ou25.get("over") or b_ou25.get("Over 2.5") or (b_goals.get("Over 2.5") if b_goals else None)
        odd_o25 = w_o25 or b_o25

        w_u25 = o_goals.get("Under 2.5") or o_ou25.get("Under 2.5") or o_ou25.get("under")
        b_u25 = b_ou25.get("under") or b_ou25.get("Under 2.5") or (b_goals.get("Under 2.5") if b_goals else None)
        odd_u25 = w_u25 or b_u25

        if odd_o25 and odd_o25 > 1.0:
            v = self.calculate_market_value(prob_over25, odd_o25)
            v["name"] = "Més de 2.5 Gols (Over 2.5)"
            v["category"] = "Gols"
            v["bookie"] = "Winamax" if w_o25 else "Bet365"
            v["bookie_odd_winamax"] = w_o25
            v["bookie_odd_bet365"] = b_o25
            v["provenance"] = "WINAMAX" if w_o25 else "BET365"
            single_markets.append(v)

        if odd_u25 and odd_u25 > 1.0:
            v = self.calculate_market_value(prob_under25, odd_u25)
            v["name"] = "Menys de 2.5 Gols (Under 2.5)"
            v["category"] = "Gols"
            v["bookie"] = "Winamax" if w_u25 else "Bet365"
            v["bookie_odd_winamax"] = w_u25
            v["bookie_odd_bet365"] = b_u25
            v["provenance"] = "WINAMAX" if w_u25 else "BET365"
            single_markets.append(v)

        w_o15 = o_goals.get("Over 1.5")
        b_o15 = b_goals.get("Over 1.5")
        odd_o15 = w_o15 or b_o15
        if odd_o15 and odd_o15 > 1.0:
            prob_over15 = p_goals.get("over_under", {}).get("over_1_5", 75.0) / 100.0
            v = self.calculate_market_value(prob_over15, odd_o15)
            v["name"] = "Més d'1.5 Gols (Over 1.5)"
            v["category"] = "Gols"
            v["bookie"] = "Winamax" if w_o15 else "Bet365"
            v["bookie_odd_winamax"] = w_o15
            v["bookie_odd_bet365"] = b_o15
            v["provenance"] = "WINAMAX" if w_o15 else "BET365"
            single_markets.append(v)

        w_u15 = o_goals.get("Under 1.5")
        b_u15 = b_goals.get("Under 1.5")
        odd_u15 = w_u15 or b_u15
        if odd_u15 and odd_u15 > 1.0:
            prob_under15 = 1.0 - (p_goals.get("over_under", {}).get("over_1_5", 75.0) / 100.0)
            v = self.calculate_market_value(prob_under15, odd_u15)
            v["name"] = "Menys d'1.5 Gols (Under 1.5)"
            v["category"] = "Gols"
            v["bookie"] = "Winamax" if w_u15 else "Bet365"
            v["bookie_odd_winamax"] = w_u15
            v["bookie_odd_bet365"] = b_u15
            v["provenance"] = "WINAMAX" if w_u15 else "BET365"
            single_markets.append(v)

        w_o35 = o_goals.get("Over 3.5")
        b_o35 = b_goals.get("Over 3.5")
        odd_o35 = w_o35 or b_o35
        if odd_o35 and odd_o35 > 1.0:
            prob_over35 = p_goals.get("over_under", {}).get("over_3_5", 28.0) / 100.0
            v = self.calculate_market_value(prob_over35, odd_o35)
            v["name"] = "Més de 3.5 Gols (Over 3.5)"
            v["category"] = "Gols"
            v["bookie"] = "Winamax" if w_o35 else "Bet365"
            v["bookie_odd_winamax"] = w_o35
            v["bookie_odd_bet365"] = b_o35
            v["provenance"] = "WINAMAX" if w_o35 else "BET365"
            single_markets.append(v)

        w_u35 = o_goals.get("Under 3.5")
        b_u35 = b_goals.get("Under 3.5")
        odd_u35 = w_u35 or b_u35
        if odd_u35 and odd_u35 > 1.0:
            prob_under35 = 1.0 - (p_goals.get("over_under", {}).get("over_3_5", 28.0) / 100.0)
            v = self.calculate_market_value(prob_under35, odd_u35)
            v["name"] = "Menys de 3.5 Gols (Under 3.5)"
            v["category"] = "Gols"
            v["bookie"] = "Winamax" if w_u35 else "Bet365"
            v["bookie_odd_winamax"] = w_u35
            v["bookie_odd_bet365"] = b_u35
            v["provenance"] = "WINAMAX" if w_u35 else "BET365"
            single_markets.append(v)

        # Gols per equip (Team Goals)
        o_team_goals = winamax_odds.get("team_goals", {})
        b_team_goals = bet365_odds.get("team_goals", {})
        h_tg = o_team_goals.get("home", {})
        a_tg = o_team_goals.get("away", {})
        b_h_tg = b_team_goals.get("home", {})
        b_a_tg = b_team_goals.get("away", {})

        p_team_goals = p_goals.get("team_goals", {})
        prob_home_scores = p_team_goals.get("home_scores", 75.0) / 100.0
        prob_away_scores = p_team_goals.get("away_scores", 68.0) / 100.0

        if "Over 0.5" in h_tg and h_tg["Over 0.5"] > 1.0:
            v = self.calculate_market_value(prob_home_scores, h_tg["Over 0.5"])
            v["name"] = f"{h_name} marca (+0.5 gols)"
            v["category"] = "Gols"
            v["bookie"] = "Winamax"
            v["bookie_odd_winamax"] = h_tg["Over 0.5"]
            v["bookie_odd_bet365"] = b_h_tg.get("Over 0.5") if b_h_tg else None
            v["provenance"] = "WINAMAX"
            single_markets.append(v)
        if "Over 1.5" in h_tg and h_tg["Over 1.5"] > 1.0:
            prob_h_o15 = min(0.95, prob_home_scores * 0.70)
            v = self.calculate_market_value(prob_h_o15, h_tg["Over 1.5"])
            v["name"] = f"{h_name} marca més d'1.5 gols"
            v["category"] = "Gols"
            v["bookie"] = "Winamax"
            v["bookie_odd_winamax"] = h_tg["Over 1.5"]
            v["bookie_odd_bet365"] = b_h_tg.get("Over 1.5") if b_h_tg else None
            v["provenance"] = "WINAMAX"
            single_markets.append(v)

        if "Over 0.5" in a_tg and a_tg["Over 0.5"] > 1.0:
            v = self.calculate_market_value(prob_away_scores, a_tg["Over 0.5"])
            v["name"] = f"{a_name} marca (+0.5 gols)"
            v["category"] = "Gols"
            v["bookie"] = "Winamax"
            v["bookie_odd_winamax"] = a_tg["Over 0.5"]
            v["bookie_odd_bet365"] = b_a_tg.get("Over 0.5") if b_a_tg else None
            v["provenance"] = "WINAMAX"
            single_markets.append(v)
        if "Over 1.5" in a_tg and a_tg["Over 1.5"] > 1.0:
            prob_a_o15 = min(0.95, prob_away_scores * 0.70)
            v = self.calculate_market_value(prob_a_o15, a_tg["Over 1.5"])
            v["name"] = f"{a_name} marca més d'1.5 gols"
            v["category"] = "Gols"
            v["bookie"] = "Winamax"
            v["bookie_odd_winamax"] = a_tg["Over 1.5"]
            v["bookie_odd_bet365"] = b_a_tg.get("Over 1.5") if b_a_tg else None
            v["provenance"] = "WINAMAX"
            single_markets.append(v)

        # 4. Ambdós Marquen (BTTS)
        o_btts = winamax_odds.get("btts", {})
        b_btts = bet365_odds.get("btts", {})
        w_btts_y = o_btts.get("Sí") or o_btts.get("si") or o_btts.get("yes")
        b_btts_y = b_btts.get("yes") or b_btts.get("Yes")
        odd_btts_yes = w_btts_y or b_btts_y

        w_btts_n = o_btts.get("No") or o_btts.get("no")
        b_btts_n = b_btts.get("no") or b_btts.get("No")
        odd_btts_no = w_btts_n or b_btts_n

        if odd_btts_yes and odd_btts_yes > 1.0:
            v = self.calculate_market_value(prob_btts_yes, odd_btts_yes)
            v["name"] = "Ambdós Equips Marquen (BTTS Sí)"
            v["category"] = "BTTS"
            v["bookie"] = "Winamax" if w_btts_y else "Bet365"
            v["bookie_odd_winamax"] = w_btts_y
            v["bookie_odd_bet365"] = b_btts_y
            v["provenance"] = "WINAMAX" if w_btts_y else "BET365"
            single_markets.append(v)

        if odd_btts_no and odd_btts_no > 1.0:
            v = self.calculate_market_value(prob_btts_no, odd_btts_no)
            v["name"] = "Ambdós Equips Marquen (BTTS No)"
            v["category"] = "BTTS"
            v["bookie"] = "Winamax" if w_btts_n else "Bet365"
            v["bookie_odd_winamax"] = w_btts_n
            v["bookie_odd_bet365"] = b_btts_n
            v["provenance"] = "WINAMAX" if w_btts_n else "BET365"
            single_markets.append(v)

        # 5. Targetes (Regla estricta: Si àrbitre és genèric / pendent, no s'avaluen)
        ref_res = model_prediction.get("referee_resolution", {})
        is_ref_generic = ref_res.get("is_generic", False) or "Standard" in ref_res.get("name", "") or "Pendent" in ref_res.get("name", "")
        cards_warning = None

        if is_ref_generic:
            cards_warning = "⚠️ Àrbitre pendent de designació oficial pel CTA: No és recomanable apostar a targetes."
        else:
            o_cards = winamax_odds.get("cards", {})
            b_cards = bet365_odds.get("cards", {})
            lambda_cards = float(p_cards.get("expected_total_cards", 4.8))

            if o_cards:
                for card_key, card_odd in o_cards.items():
                    if not card_odd or card_odd <= 1.0:
                        continue
                    if "vermella" in card_key.lower() or "expulsi" in card_key.lower():
                        v = self.calculate_market_value(prob_red_card, card_odd)
                        v["name"] = "Hi haurà Expulsió (Vermella Sí)"
                        v["category"] = "Targetes"
                        v["bookie"] = "Winamax"
                        v["bookie_odd_winamax"] = card_odd
                        v["bookie_odd_bet365"] = b_cards.get("Targeta Vermella (Sí)") or b_cards.get("Red Card (Yes)") if b_cards else None
                        v["provenance"] = "WINAMAX"
                        single_markets.append(v)
                    else:
                        is_under = "under" in card_key.lower() or "menys" in card_key.lower()
                        m_c_th = re.search(r'(\d+(?:\.\d+)?)', card_key)
                        if m_c_th:
                            thresh = float(m_c_th.group(1))
                            k_max = int(thresh)
                            prob_under = float(poisson.cdf(k_max, lambda_cards))
                            prob_over = 1.0 - prob_under
                            model_p = prob_under if is_under else prob_over
                            dir_label = f"Menys de {thresh} Targetes" if is_under else f"Més de {thresh} Targetes"
                            b_card_odd = self._find_matching_card_odd(b_cards, is_under, thresh)
                            v = self.calculate_market_value(model_p, card_odd)
                            v["name"] = dir_label
                            v["category"] = "Targetes"
                            v["bookie"] = "Winamax"
                            v["bookie_odd_winamax"] = card_odd
                            v["bookie_odd_bet365"] = b_card_odd
                            v["provenance"] = "WINAMAX"
                            single_markets.append(v)
            elif b_cards:
                for card_key, card_odd in b_cards.items():
                    if not card_odd or card_odd <= 1.0:
                        continue
                    is_under = "under" in card_key.lower()
                    m_c_th = re.search(r'(\d+(?:\.\d+)?)', card_key) or re.search(r'(\d+)_(\d+)', card_key)
                    if m_c_th:
                        thresh = float(f"{m_c_th.group(1)}.{m_c_th.group(2)}") if m_c_th.lastindex == 2 else float(m_c_th.group(1))
                        k_max = int(thresh)
                        prob_under = float(poisson.cdf(k_max, lambda_cards))
                        prob_over = 1.0 - prob_under
                        model_p = prob_under if is_under else prob_over
                        dir_label = f"Menys de {thresh} Targetes" if is_under else f"Més de {thresh} Targetes"
                        w_card_odd = self._find_matching_card_odd(o_cards, is_under, thresh)
                        v = self.calculate_market_value(model_p, card_odd)
                        v["name"] = dir_label
                        v["category"] = "Targetes"
                        v["bookie"] = "Bet365"
                        v["bookie_odd_winamax"] = w_card_odd
                        v["bookie_odd_bet365"] = card_odd
                        v["provenance"] = "BET365"
                        single_markets.append(v)

        # 6. Córners (Classificació explícita OVER, UNDER, EXACT, UNKNOWN i format EUROPEAN_3WAY / ASIAN_2WAY / HALF_BALL_2WAY)
        o_corners = winamax_odds.get("corners", {})
        b_corners = bet365_odds.get("corners", {})
        lambda_corn = float(p_corners.get("expected_total_corners", 9.4))

        evaluated_lines = set()

        # Primer avaluar Winamax
        if o_corners:
            for c_key, c_odd in o_corners.items():
                if not c_odd or c_odd <= 1.0:
                    continue
                parsed = self.classify_corner_market(c_key, available_keys=o_corners.keys())
                m_type = parsed["market_type"]
                m_format = parsed["market_format"]
                thresh = parsed["threshold"]
                if m_type not in ("OVER", "UNDER") or m_format == "UNKNOWN" or thresh is None:
                    continue
                line_key = (m_format, m_type, round(thresh, 1))
                if line_key in evaluated_lines:
                    continue
                evaluated_lines.add(line_key)

                probs = self.calculate_corner_probabilities(m_type, thresh, lambda_corn, market_format=m_format)
                if not probs or probs["p_win"] <= 0.0:
                    continue

                b_odd = self._find_matching_corner_odd(b_corners, m_type, thresh, target_format=m_format)
                asian_tag = " (Asiàtic)" if m_format == "ASIAN_2WAY" else ""
                dir_label = f"Menys de {thresh} Córners{asian_tag}" if m_type == "UNDER" else f"Més de {thresh} Córners{asian_tag}"
                v = self.calculate_market_value(probs["p_win"], c_odd, push_prob=probs["p_push"], loss_prob=probs["p_loss"])
                v["name"] = dir_label
                v["category"] = "Córners"
                v["market_type"] = m_type
                v["market_format"] = m_format
                v["threshold"] = thresh
                v["push_prob"] = round(probs["p_push"] * 100, 1)
                v["loss_prob"] = round(probs["p_loss"] * 100, 1)
                v["bookie"] = "Winamax"
                v["bookie_odd_winamax"] = c_odd
                v["bookie_odd_bet365"] = b_odd
                v["provenance"] = "WINAMAX"
                single_markets.append(v)

        # Després avaluar Bet365 per a línies no cobertes o com a fallback
        if b_corners:
            for c_key, c_odd in b_corners.items():
                if not c_odd or c_odd <= 1.0:
                    continue
                parsed = self.classify_corner_market(c_key, available_keys=b_corners.keys())
                m_type = parsed["market_type"]
                m_format = parsed["market_format"]
                thresh = parsed["threshold"]
                # EXACT es descarta explícitament de value bets (mai Over), i mercats UNKNOWN es descarten
                if m_type not in ("OVER", "UNDER") or m_format == "UNKNOWN" or thresh is None:
                    continue
                line_key = (m_format, m_type, round(thresh, 1))
                if line_key in evaluated_lines:
                    continue
                evaluated_lines.add(line_key)

                probs = self.calculate_corner_probabilities(m_type, thresh, lambda_corn, market_format=m_format)
                if not probs or probs["p_win"] <= 0.0:
                    continue

                w_odd = self._find_matching_corner_odd(o_corners, m_type, thresh, target_format=m_format)
                asian_tag = " (Asiàtic)" if m_format == "ASIAN_2WAY" else ""
                dir_label = f"Menys de {thresh} Córners{asian_tag}" if m_type == "UNDER" else f"Més de {thresh} Córners{asian_tag}"
                v = self.calculate_market_value(probs["p_win"], c_odd, push_prob=probs["p_push"], loss_prob=probs["p_loss"])
                v["name"] = dir_label
                v["category"] = "Córners"
                v["market_type"] = m_type
                v["market_format"] = m_format
                v["threshold"] = thresh
                v["push_prob"] = round(probs["p_push"] * 100, 1)
                v["loss_prob"] = round(probs["p_loss"] * 100, 1)
                v["bookie"] = "Bet365"
                v["bookie_odd_winamax"] = w_odd
                v["bookie_odd_bet365"] = c_odd
                v["provenance"] = "BET365"
                single_markets.append(v)

        for m in single_markets:
            prov = m.get("provenance")
            if prov == "WINAMAX":
                m["is_stale"] = w_is_stale
                m["freshness"] = w_freshness
                if w_is_stale:
                    m["rejection_reason"] = w_reason
            elif prov == "BET365":
                m["is_stale"] = b_is_stale
                m["freshness"] = b_freshness
                if b_is_stale:
                    m["rejection_reason"] = b_reason

        # Ordenar mercats pel major Valor Esperat (+EV%)
        single_markets.sort(key=lambda x: x["ev_pct"], reverse=True)
        value_bets = [
            m for m in single_markets
            if m["is_value"] and not m.get("is_stale") and m.get("freshness") not in ("STALE", "UNKNOWN")
        ]

        return {
            "single_markets": single_markets,
            "combo_markets": [],
            "value_bets": value_bets,
            "best_value_bet": value_bets[0] if value_bets else None,
            "cards_warning": cards_warning
        }
