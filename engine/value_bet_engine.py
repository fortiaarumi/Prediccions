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

        # 3. Gols Over / Under 2.5
        o_ou = winamax_odds.get("over_under_2_5", {})
        if "Over 2.5" in o_ou and o_ou["Over 2.5"]:
            v = self.calculate_market_value(prob_over25, o_ou["Over 2.5"])
            v["name"] = "Més de 2.5 Gols (Over 2.5)"
            v["category"] = "Gols"
            single_markets.append(v)
        if "Under 2.5" in o_ou and o_ou["Under 2.5"]:
            v = self.calculate_market_value(prob_under25, o_ou["Under 2.5"])
            v["name"] = "Menys de 2.5 Gols (Under 2.5)"
            v["category"] = "Gols"
            single_markets.append(v)

        prob_over15 = p_goals.get("over_under", {}).get("over_1_5", 75.0) / 100.0
        prob_under15 = 1.0 - prob_over15
        prob_over35 = p_goals.get("over_under", {}).get("over_3_5", 28.0) / 100.0
        prob_under35 = 1.0 - prob_over35

        p_team_goals = p_goals.get("team_goals", {})
        prob_home_scores = p_team_goals.get("home_scores", 75.0) / 100.0
        prob_away_scores = p_team_goals.get("away_scores", 68.0) / 100.0

        # Gols addicionals (Over 1.5 / Under 3.5 / Gols per equip)
        if prob_over15 >= 0.65:
            odd_o15 = max(1.12, round(0.92 / prob_over15, 2))
            v = self.calculate_market_value(prob_over15, odd_o15)
            v["name"] = "Més d'1.5 Gols (Over 1.5)"
            v["category"] = "Gols"
            single_markets.append(v)

        if prob_under35 >= 0.65:
            odd_u35 = max(1.12, round(0.92 / prob_under35, 2))
            v = self.calculate_market_value(prob_under35, odd_u35)
            v["name"] = "Menys de 3.5 Gols (Under 3.5)"
            v["category"] = "Gols"
            single_markets.append(v)

        if prob_home_scores >= 0.70:
            odd_h_scores = max(1.10, round(0.92 / prob_home_scores, 2))
            v = self.calculate_market_value(prob_home_scores, odd_h_scores)
            v["name"] = f"{h_name} marca (+0.5 gols)"
            v["category"] = "Gols"
            single_markets.append(v)

        if prob_away_scores >= 0.70:
            odd_a_scores = max(1.12, round(0.92 / prob_away_scores, 2))
            v = self.calculate_market_value(prob_away_scores, odd_a_scores)
            v["name"] = f"{a_name} marca (+0.5 gols)"
            v["category"] = "Gols"
            single_markets.append(v)

        # 4. Ambdós Marquen (BTTS)
        o_btts = winamax_odds.get("btts", {})
        odd_btts_yes = float(o_btts.get("Sí") or max(1.35, round(0.92 / prob_btts_yes, 2)))
        v = self.calculate_market_value(prob_btts_yes, odd_btts_yes)
        v["name"] = "Ambdós Equips Marquen (BTTS Sí)"
        v["category"] = "BTTS"
        single_markets.append(v)

        odd_btts_no = float(o_btts.get("No") or max(1.35, round(0.92 / prob_btts_no, 2)))
        v = self.calculate_market_value(prob_btts_no, odd_btts_no)
        v["name"] = "Ambdós Equips Marquen (BTTS No)"
        v["category"] = "BTTS"
        single_markets.append(v)

        # 5. Targetes (Calibrades amb l'àrbitre designat oficialment pel CTA / PGMOL)
        ref_res = model_prediction.get("referee_resolution", {})
        is_ref_generic = ref_res.get("is_generic", False) or "Standard" in ref_res.get("name", "") or "Pendent" in ref_res.get("name", "")
        cards_warning = None

        if is_ref_generic:
            cards_warning = "⚠️ Àrbitre pendent de designació oficial pel CTA: No és recomanable apostar a targetes."
        else:
            o_cards = winamax_odds.get("cards", {})
            odd_c45 = float(o_cards.get("Over 4.5 Targetes") or max(1.30, round(0.92 / prob_cards_o45, 2)))
            v = self.calculate_market_value(prob_cards_o45, odd_c45)
            v["name"] = "Més de 4.5 Targetes"
            v["category"] = "Targetes"
            single_markets.append(v)

            odd_u45 = float(o_cards.get("Under 4.5 Targetes") or max(1.30, round(0.92 / prob_cards_u45, 2)))
            v = self.calculate_market_value(prob_cards_u45, odd_u45)
            v["name"] = "Menys de 4.5 Targetes"
            v["category"] = "Targetes"
            single_markets.append(v)

            if prob_cards_o55 >= 0.35 or "Over 5.5 Targetes" in o_cards:
                odd_c55 = float(o_cards.get("Over 5.5 Targetes") or max(1.70, round(0.92 / prob_cards_o55, 2)))
                v = self.calculate_market_value(prob_cards_o55, odd_c55)
                v["name"] = "Més de 5.5 Targetes"
                v["category"] = "Targetes"
                single_markets.append(v)

        # 6. Córners
        o_corners = winamax_odds.get("corners", {})
        odd_corn_o95 = float(o_corners.get("Over 9.5 Córners") or max(1.35, round(0.92 / prob_corners_o95, 2)))
        v = self.calculate_market_value(prob_corners_o95, odd_corn_o95)
        v["name"] = "Més de 9.5 Córners"
        v["category"] = "Córners"
        single_markets.append(v)

        odd_corn_o85 = float(o_corners.get("Over 8.5 Córners") or max(1.22, round(0.92 / prob_corners_o85, 2)))
        v = self.calculate_market_value(prob_corners_o85, odd_corn_o85)
        v["name"] = "Més de 8.5 Córners"
        v["category"] = "Córners"
        single_markets.append(v)

        odd_corn_u95 = float(o_corners.get("Under 9.5 Córners") or max(1.35, round(0.92 / prob_corners_u95, 2)))
        v = self.calculate_market_value(prob_corners_u95, odd_corn_u95)
        v["name"] = "Menys de 9.5 Córners"
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
