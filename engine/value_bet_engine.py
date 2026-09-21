"""
engine/value_bet_engine.py
==========================
Motor d'anàlisi de valor (+EV%) i detecció d'apostes rendibles.
Avalua ÚNICAMENT les cuotes que realment estan publicades a Winamax.
"""

from typing import Dict, Any, List

class ValueBetEngine:
    def __init__(self, kelly_fraction: float = 0.25):
        self.kelly_fraction = kelly_fraction

    def calculate_market_value(self, model_prob: float, bookie_odd: float) -> Dict[str, Any]:
        """Calcula el valor esperat (+EV%), cuota justa i percentatge Kelly."""
        if bookie_odd is None or bookie_odd <= 1.0 or model_prob <= 0.0:
            return {
                "model_prob": round(model_prob * 100, 1),
                "bookie_odd": None,
                "fair_odd": 99.0,
                "implied_prob": 0.0,
                "ev_pct": -100.0,
                "kelly_pct": 0.0,
                "is_value": False,
                "rating": "SENSE VALOR"
            }

        p = model_prob
        b = bookie_odd - 1.0
        q = 1.0 - p

        fair_odd = 1.0 / p if p > 0 else 99.0
        implied_prob = (1.0 / bookie_odd) * 100.0
        ev_pct = (p * bookie_odd - 1.0) * 100.0

        # Càlcul de Kelly fraccional
        kelly_full = (b * p - q) / b if b > 0 else 0.0
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
            "model_prob": round(p * 100, 1),
            "bookie_odd": round(bookie_odd, 2),
            "fair_odd": round(fair_odd, 2),
            "implied_prob": round(implied_prob, 1),
            "ev_pct": round(ev_pct, 1),
            "kelly_pct": round(kelly_stake, 1),
            "is_value": is_value,
            "rating": rating
        }

    def analyze_match_betting(self, model_prediction: Dict[str, Any], winamax_odds: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analitza exclusivament els mercats que Winamax té oberts en directe.
        """
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

        prob_corners_o95 = p_corners["prob_over_corners"].get("over_9_5", 50.0) / 100.0
        prob_corners_u95 = 1.0 - prob_corners_o95
        prob_corners_o85 = min(0.92, prob_corners_o95 * 1.25)
        prob_corners_o105 = max(0.08, prob_corners_o95 * 0.75)

        single_markets = []
        h_name = model_prediction.get("home_team", "Local")
        a_name = model_prediction.get("away_team", "Visitant")

        # 1. Mercat 1X2
        o_1x2 = winamax_odds.get("1X2", {})
        if "1" in o_1x2 and o_1x2["1"]:
            v = self.calculate_market_value(prob_1, o_1x2["1"])
            v["name"] = f"1 - Victòria Local ({h_name})"
            v["category"] = "1X2"
            single_markets.append(v)
        if "X" in o_1x2 and o_1x2["X"]:
            v = self.calculate_market_value(prob_x, o_1x2["X"])
            v["name"] = "X - Empat"
            v["category"] = "1X2"
            single_markets.append(v)
        if "2" in o_1x2 and o_1x2["2"]:
            v = self.calculate_market_value(prob_2, o_1x2["2"])
            v["name"] = f"2 - Victòria Visitant ({a_name})"
            v["category"] = "1X2"
            single_markets.append(v)

        # 2. Doble Oportunitat
        o_dc = winamax_odds.get("double_chance", {})
        if "1X" in o_dc and o_dc["1X"]:
            v = self.calculate_market_value(prob_1x, o_dc["1X"])
            v["name"] = f"1X - {h_name} o Empat"
            v["category"] = "Doble Oportunitat"
            single_markets.append(v)
        if "X2" in o_dc and o_dc["X2"]:
            v = self.calculate_market_value(prob_x2, o_dc["X2"])
            v["name"] = f"X2 - Empat o {a_name}"
            v["category"] = "Doble Oportunitat"
            single_markets.append(v)

        # 3. Gols Over / Under (1.5, 2.5, 3.5) - NOMÉS SI WINAMAX HO OFEREIX REALMENT
        o_goals = winamax_odds.get("goals", {})
        o_ou25 = winamax_odds.get("over_under_2_5", {})

        odd_o25 = o_goals.get("Over 2.5") or o_ou25.get("Over 2.5")
        if odd_o25 and odd_o25 > 1.0:
            v = self.calculate_market_value(prob_over25, odd_o25)
            v["name"] = "Més de 2.5 Gols (Over 2.5)"
            v["category"] = "Gols"
            single_markets.append(v)

        odd_u25 = o_goals.get("Under 2.5") or o_ou25.get("Under 2.5")
        if odd_u25 and odd_u25 > 1.0:
            v = self.calculate_market_value(prob_under25, odd_u25)
            v["name"] = "Menys de 2.5 Gols (Under 2.5)"
            v["category"] = "Gols"
            single_markets.append(v)

        odd_o15 = o_goals.get("Over 1.5")
        if odd_o15 and odd_o15 > 1.0:
            prob_over15 = p_goals.get("over_under", {}).get("over_1_5", 75.0) / 100.0
            v = self.calculate_market_value(prob_over15, odd_o15)
            v["name"] = "Més d'1.5 Gols (Over 1.5)"
            v["category"] = "Gols"
            single_markets.append(v)

        odd_u15 = o_goals.get("Under 1.5")
        if odd_u15 and odd_u15 > 1.0:
            prob_under15 = 1.0 - (p_goals.get("over_under", {}).get("over_1_5", 75.0) / 100.0)
            v = self.calculate_market_value(prob_under15, odd_u15)
            v["name"] = "Menys d'1.5 Gols (Under 1.5)"
            v["category"] = "Gols"
            single_markets.append(v)

        odd_o35 = o_goals.get("Over 3.5")
        if odd_o35 and odd_o35 > 1.0:
            prob_over35 = p_goals.get("over_under", {}).get("over_3_5", 28.0) / 100.0
            v = self.calculate_market_value(prob_over35, odd_o35)
            v["name"] = "Més de 3.5 Gols (Over 3.5)"
            v["category"] = "Gols"
            single_markets.append(v)

        odd_u35 = o_goals.get("Under 3.5")
        if odd_u35 and odd_u35 > 1.0:
            prob_under35 = 1.0 - (p_goals.get("over_under", {}).get("over_3_5", 28.0) / 100.0)
            v = self.calculate_market_value(prob_under35, odd_u35)
            v["name"] = "Menys de 3.5 Gols (Under 3.5)"
            v["category"] = "Gols"
            single_markets.append(v)

        # Gols per equip (NOMÉS SI WINAMAX HO PUBLICA REALMENT)
        o_team_goals = winamax_odds.get("team_goals", {})
        h_tg = o_team_goals.get("home", {})
        a_tg = o_team_goals.get("away", {})

        p_team_goals = p_goals.get("team_goals", {})
        prob_home_scores = p_team_goals.get("home_scores", 75.0) / 100.0
        prob_away_scores = p_team_goals.get("away_scores", 68.0) / 100.0

        if "Over 0.5" in h_tg and h_tg["Over 0.5"] > 1.0:
            v = self.calculate_market_value(prob_home_scores, h_tg["Over 0.5"])
            v["name"] = f"{h_name} marca (+0.5 gols)"
            v["category"] = "Gols"
            single_markets.append(v)
        if "Over 1.5" in h_tg and h_tg["Over 1.5"] > 1.0:
            prob_h_o15 = min(0.95, prob_home_scores * 0.70)
            v = self.calculate_market_value(prob_h_o15, h_tg["Over 1.5"])
            v["name"] = f"{h_name} marca més d'1.5 gols"
            v["category"] = "Gols"
            single_markets.append(v)

        if "Over 0.5" in a_tg and a_tg["Over 0.5"] > 1.0:
            v = self.calculate_market_value(prob_away_scores, a_tg["Over 0.5"])
            v["name"] = f"{a_name} marca (+0.5 gols)"
            v["category"] = "Gols"
            single_markets.append(v)
        if "Over 1.5" in a_tg and a_tg["Over 1.5"] > 1.0:
            prob_a_o15 = min(0.95, prob_away_scores * 0.70)
            v = self.calculate_market_value(prob_a_o15, a_tg["Over 1.5"])
            v["name"] = f"{a_name} marca més d'1.5 gols"
            v["category"] = "Gols"
            single_markets.append(v)

        # 4. Ambdós Marquen (BTTS) - NOMÉS SI WINAMAX HO OFEREIX
        o_btts = winamax_odds.get("btts", {})
        if "Sí" in o_btts and o_btts["Sí"] and o_btts["Sí"] > 1.0:
            v = self.calculate_market_value(prob_btts_yes, o_btts["Sí"])
            v["name"] = "Ambdós Equips Marquen (BTTS Sí)"
            v["category"] = "BTTS"
            single_markets.append(v)

        if "No" in o_btts and o_btts["No"] and o_btts["No"] > 1.0:
            v = self.calculate_market_value(prob_btts_no, o_btts["No"])
            v["name"] = "Ambdós Equips Marquen (BTTS No)"
            v["category"] = "BTTS"
            single_markets.append(v)

        # 5. Targetes - NOMÉS SI WINAMAX HO OFEREIX
        ref_res = model_prediction.get("referee_resolution", {})
        is_ref_generic = ref_res.get("is_generic", False) or "Standard" in ref_res.get("name", "") or "Pendent" in ref_res.get("name", "")
        cards_warning = None

        if is_ref_generic:
            cards_warning = "⚠️ Àrbitre pendent de designació oficial pel CTA: No és recomanable apostar a targetes."
        else:
            o_cards = winamax_odds.get("cards", {})
            if "Over 4.5 Targetes" in o_cards and o_cards["Over 4.5 Targetes"] > 1.0:
                v = self.calculate_market_value(prob_cards_o45, o_cards["Over 4.5 Targetes"])
                v["name"] = "Més de 4.5 Targetes"
                v["category"] = "Targetes"
                single_markets.append(v)

            if "Under 4.5 Targetes" in o_cards and o_cards["Under 4.5 Targetes"] > 1.0:
                v = self.calculate_market_value(prob_cards_u45, o_cards["Under 4.5 Targetes"])
                v["name"] = "Menys de 4.5 Targetes"
                v["category"] = "Targetes"
                single_markets.append(v)

            if "Over 5.5 Targetes" in o_cards and o_cards["Over 5.5 Targetes"] > 1.0:
                v = self.calculate_market_value(prob_cards_o55, o_cards["Over 5.5 Targetes"])
                v["name"] = "Més de 5.5 Targetes"
                v["category"] = "Targetes"
                single_markets.append(v)

            if "Targeta Vermella (Sí)" in o_cards and o_cards["Targeta Vermella (Sí)"] > 1.0:
                v = self.calculate_market_value(prob_red_card, o_cards["Targeta Vermella (Sí)"])
                v["name"] = "Hi haurà Expulsió (Vermella Sí)"
                v["category"] = "Targetes"
                single_markets.append(v)

        # 6. Córners - NOMÉS SI WINAMAX HO OFEREIX
        o_corners = winamax_odds.get("corners", {})
        if "Over 8.5 Córners" in o_corners and o_corners["Over 8.5 Córners"] > 1.0:
            v = self.calculate_market_value(prob_corners_o85, o_corners["Over 8.5 Córners"])
            v["name"] = "Més de 8.5 Córners"
            v["category"] = "Córners"
            single_markets.append(v)

        if "Over 9.5 Córners" in o_corners and o_corners["Over 9.5 Córners"] > 1.0:
            v = self.calculate_market_value(prob_corners_o95, o_corners["Over 9.5 Córners"])
            v["name"] = "Més de 9.5 Córners"
            v["category"] = "Córners"
            single_markets.append(v)

        if "Under 9.5 Córners" in o_corners and o_corners["Under 9.5 Córners"] > 1.0:
            v = self.calculate_market_value(prob_corners_u95, o_corners["Under 9.5 Córners"])
            v["name"] = "Menys de 9.5 Córners"
            v["category"] = "Córners"
            single_markets.append(v)

        if "Over 10.5 Córners" in o_corners and o_corners["Over 10.5 Córners"] > 1.0:
            v = self.calculate_market_value(prob_corners_o105, o_corners["Over 10.5 Córners"])
            v["name"] = "Més de 10.5 Córners"
            v["category"] = "Córners"
            single_markets.append(v)

        # Ordenar mercats pel major Valor Esperat (+EV%)
        single_markets.sort(key=lambda x: x["ev_pct"], reverse=True)
        value_bets = [m for m in single_markets if m["is_value"]]

        return {
            "single_markets": single_markets,
            "combo_markets": [],
            "value_bets": value_bets,
            "best_value_bet": value_bets[0] if value_bets else None,
            "cards_warning": cards_warning
        }
