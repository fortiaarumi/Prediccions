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
"""

import math
from typing import Dict, List, Any, Optional

class ArbitrageEngine:
    def __init__(self, min_profit_threshold: float = 0.0, near_arb_threshold: float = -2.5, max_profit_threshold: float = 15.0):
        """
        :param min_profit_threshold: % mínim de benefici net garantit per considerar-ho Surebet (ex: 0.0% o 1.0%).
        :param near_arb_threshold: % límit per classificar com a 'Near-Arb' (marge estret en monitorització).
        :param max_profit_threshold: % límit màxim de seguretat (palpable error filter). Si supera el 15%,
                                     és un error de feed o quota errònia que la casa cancel·larà.
        """
        self.min_profit = min_profit_threshold
        self.near_arb_limit = near_arb_threshold
        self.max_profit = max_profit_threshold

    def calculate_2way_arbitrage(
        self,
        leg1: Dict[str, Any],
        leg2: Dict[str, Any],
        market_name: str,
        match_info: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Calcula l'arbitratge entre dos resultats mútuament excloents i exhaustius."""
        o1 = float(leg1.get("odd") or 0.0)
        o2 = float(leg2.get("odd") or 0.0)

        if o1 <= 1.01 or o2 <= 1.01 or o1 > 15.0 or o2 > 15.0:
            return None

        # Suma de probabilitats implícites (Invers de les quotes)
        sum_inv = (1.0 / o1) + (1.0 / o2)
        if sum_inv <= 0.0:
            return None

        profit_pct = round(((1.0 / sum_inv) - 1.0) * 100.0, 2)
        margin_pct = round(sum_inv * 100.0, 2)

        # Filtre anti errors palpables o quotes corruptes (>15% és irreal pre-partit)
        if profit_pct < self.near_arb_limit or profit_pct > self.max_profit:
            return None

        is_pure_surebet = (profit_pct > self.min_profit)

        # Distribució de capital òptima sobre 100 €
        base_bankroll = 100.0
        exact_s1 = (base_bankroll / sum_inv) / o1
        exact_s2 = (base_bankroll / sum_inv) / o2

        # Arrodonir a nombres enters per seguretat anti-limitació DGOJ
        round_s1 = max(1.0, round(exact_s1))
        round_s2 = max(1.0, base_bankroll - round_s1)

        ret1 = round(round_s1 * o1, 2)
        ret2 = round(round_s2 * o2, 2)
        min_return = min(ret1, ret2)
        actual_net_profit = round(min_return - base_bankroll, 2)

        return {
            "type": "2-WAY",
            "market_name": market_name,
            "matchup": match_info.get("matchup", "Partit"),
            "date": match_info.get("date", "Pendent"),
            "competition_id": match_info.get("competition_id", ""),
            "competition_name": match_info.get("competition_name", ""),
            "is_surebet": is_pure_surebet,
            "status": "SUREBET" if is_pure_surebet else "NEAR_ARB",
            "profit_pct": profit_pct,
            "margin_pct": margin_pct,
            "base_investment": base_bankroll,
            "net_guaranteed_profit": actual_net_profit,
            "min_guaranteed_return": min_return,
            "legs": [
                {
                    "bookmaker": leg1["bookmaker"],
                    "selection": leg1["selection"],
                    "odd": o1,
                    "stake_pct": round(exact_s1, 1),
                    "stake_rounded": int(round_s1),
                    "expected_return": ret1,
                    "url": leg1.get("url", "")
                },
                {
                    "bookmaker": leg2["bookmaker"],
                    "selection": leg2["selection"],
                    "odd": o2,
                    "stake_pct": round(exact_s2, 1),
                    "stake_rounded": int(round_s2),
                    "expected_return": ret2,
                    "url": leg2.get("url", "")
                }
            ]
        }

    def calculate_3way_arbitrage(
        self,
        leg1: Dict[str, Any],
        legX: Dict[str, Any],
        leg2: Dict[str, Any],
        match_info: Dict[str, Any]
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

        sum_inv = (1.0 / o1) + (1.0 / ox) + (1.0 / o2)
        if sum_inv <= 0.0:
            return None

        profit_pct = round(((1.0 / sum_inv) - 1.0) * 100.0, 2)
        margin_pct = round(sum_inv * 100.0, 2)

        # Filtre anti errors palpables o quotes corruptes (>15% és irreal pre-partit)
        if profit_pct < self.near_arb_limit or profit_pct > self.max_profit:
            return None

        is_pure_surebet = (profit_pct > self.min_profit)

        base_bankroll = 100.0
        exact_s1 = (base_bankroll / sum_inv) / o1
        exact_sx = (base_bankroll / sum_inv) / ox
        exact_s2 = (base_bankroll / sum_inv) / o2

        round_s1 = max(1.0, round(exact_s1))
        round_sx = max(1.0, round(exact_sx))
        round_s2 = max(1.0, base_bankroll - round_s1 - round_sx)

        ret1 = round(round_s1 * o1, 2)
        retx = round(round_sx * ox, 2)
        ret2 = round(round_s2 * o2, 2)
        min_return = min(ret1, retx, ret2)
        actual_net_profit = round(min_return - base_bankroll, 2)

        return {
            "type": "3-WAY",
            "market_name": "1X2 Cross-Bookmaker",
            "matchup": match_info.get("matchup", "Partit"),
            "date": match_info.get("date", "Pendent"),
            "competition_id": match_info.get("competition_id", ""),
            "competition_name": match_info.get("competition_name", ""),
            "is_surebet": is_pure_surebet,
            "status": "SUREBET" if is_pure_surebet else "NEAR_ARB",
            "profit_pct": profit_pct,
            "margin_pct": margin_pct,
            "base_investment": base_bankroll,
            "net_guaranteed_profit": actual_net_profit,
            "min_guaranteed_return": min_return,
            "legs": [
                {
                    "bookmaker": leg1["bookmaker"],
                    "selection": leg1["selection"],
                    "odd": o1,
                    "stake_pct": round(exact_s1, 1),
                    "stake_rounded": int(round_s1),
                    "expected_return": ret1,
                    "url": leg1.get("url", "")
                },
                {
                    "bookmaker": legX["bookmaker"],
                    "selection": legX["selection"],
                    "odd": ox,
                    "stake_pct": round(exact_sx, 1),
                    "stake_rounded": int(round_sx),
                    "expected_return": retx,
                    "url": legX.get("url", "")
                },
                {
                    "bookmaker": leg2["bookmaker"],
                    "selection": leg2["selection"],
                    "odd": o2,
                    "stake_pct": round(exact_s2, 1),
                    "stake_rounded": int(round_s2),
                    "expected_return": ret2,
                    "url": leg2.get("url", "")
                }
            ]
        }

    def scan_match(self, match: Dict[str, Any]) -> List[Dict[str, Any]]:
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

        w_url = match.get("url_winamax") or "https://www.winamax.es"
        b_url = "https://www.bet365.es/#/AS/B1/"

        # 1. 1X2 Winamax & Bet365
        w_1x2 = match.get("odds_1x2_winamax") or {}
        b_1x2 = match.get("odds_1x2_bet365") or {}

        w1 = float(w_1x2.get("1") or 0.0)
        wx = float(w_1x2.get("X") or 0.0)
        w2 = float(w_1x2.get("2") or 0.0)

        b1 = float(b_1x2.get("1") or 0.0)
        bx = float(b_1x2.get("X") or 0.0)
        b2 = float(b_1x2.get("2") or 0.0)

        # 1X2 3-way Cross Bookmaker
        if (w1 > 1.0 or b1 > 1.0) and (wx > 1.0 or bx > 1.0) and (w2 > 1.0 or b2 > 1.0):
            leg1 = {
                "bookmaker": "Winamax" if w1 >= b1 else "Bet365",
                "selection": f"1 - Victòria {h_name}",
                "odd": max(w1, b1),
                "url": w_url if w1 >= b1 else b_url
            }
            legX = {
                "bookmaker": "Winamax" if wx >= bx else "Bet365",
                "selection": "X - Empat",
                "odd": max(wx, bx),
                "url": w_url if wx >= bx else b_url
            }
            leg2 = {
                "bookmaker": "Winamax" if w2 >= b2 else "Bet365",
                "selection": f"2 - Victòria {a_name}",
                "odd": max(w2, b2),
                "url": w_url if w2 >= b2 else b_url
            }
            arb_1x2 = self.calculate_3way_arbitrage(leg1, legX, leg2, match_info)
            if arb_1x2:
                arbs.append(arb_1x2)

        # 2. Doble Oportunitat vs Guanyador Simple (2-way)
        w_dc = match.get("odds_dc_winamax") or {}
        b_dc = match.get("odds_dc_bet365") or {}

        # 1X (Local o Empat) vs 2 (Visitant)
        w_1x = float(w_dc.get("1X") or 0.0)
        b_1x = float(b_dc.get("1X") or 0.0)
        if w_1x > 1.0 and b2 > 1.0:
            arb = self.calculate_2way_arbitrage(
                {"bookmaker": "Winamax", "selection": f"1X - {h_name} o Empat", "odd": w_1x, "url": w_url},
                {"bookmaker": "Bet365", "selection": f"2 - Victòria {a_name}", "odd": b2, "url": b_url},
                "Doble Oportunitat (1X vs 2)",
                match_info
            )
            if arb: arbs.append(arb)
        if b_1x > 1.0 and w2 > 1.0:
            arb = self.calculate_2way_arbitrage(
                {"bookmaker": "Bet365", "selection": f"1X - {h_name} o Empat", "odd": b_1x, "url": b_url},
                {"bookmaker": "Winamax", "selection": f"2 - Victòria {a_name}", "odd": w2, "url": w_url},
                "Doble Oportunitat (1X vs 2)",
                match_info
            )
            if arb: arbs.append(arb)

        # X2 (Empat o Visitant) vs 1 (Local)
        w_x2 = float(w_dc.get("X2") or 0.0)
        b_x2 = float(b_dc.get("X2") or 0.0)
        if w_x2 > 1.0 and b1 > 1.0:
            arb = self.calculate_2way_arbitrage(
                {"bookmaker": "Winamax", "selection": f"X2 - Empat o {a_name}", "odd": w_x2, "url": w_url},
                {"bookmaker": "Bet365", "selection": f"1 - Victòria {h_name}", "odd": b1, "url": b_url},
                "Doble Oportunitat (X2 vs 1)",
                match_info
            )
            if arb: arbs.append(arb)
        if b_x2 > 1.0 and w1 > 1.0:
            arb = self.calculate_2way_arbitrage(
                {"bookmaker": "Bet365", "selection": f"X2 - Empat o {a_name}", "odd": b_x2, "url": b_url},
                {"bookmaker": "Winamax", "selection": f"1 - Victòria {h_name}", "odd": w1, "url": w_url},
                "Doble Oportunitat (X2 vs 1)",
                match_info
            )
            if arb: arbs.append(arb)

        # 12 (Local o Visitant) vs X (Empat)
        w_12 = float(w_dc.get("12") or 0.0)
        b_12 = float(b_dc.get("12") or 0.0)
        if w_12 > 1.0 and bx > 1.0:
            arb = self.calculate_2way_arbitrage(
                {"bookmaker": "Winamax", "selection": f"12 - {h_name} o {a_name}", "odd": w_12, "url": w_url},
                {"bookmaker": "Bet365", "selection": "X - Empat", "odd": bx, "url": b_url},
                "Doble Oportunitat (12 vs X)",
                match_info
            )
            if arb: arbs.append(arb)
        if b_12 > 1.0 and wx > 1.0:
            arb = self.calculate_2way_arbitrage(
                {"bookmaker": "Bet365", "selection": f"12 - {h_name} o {a_name}", "odd": b_12, "url": b_url},
                {"bookmaker": "Winamax", "selection": "X - Empat", "odd": wx, "url": w_url},
                "Doble Oportunitat (12 vs X)",
                match_info
            )
            if arb: arbs.append(arb)

        # 3. Over / Under 2.5 Gols
        w_ou = match.get("odds_ou25_winamax") or {}
        b_ou = match.get("odds_ou25_bet365") or {}
        w_over = float(w_ou.get("over") or 0.0)
        w_under = float(w_ou.get("under") or 0.0)
        b_over = float(b_ou.get("over") or 0.0)
        b_under = float(b_ou.get("under") or 0.0)

        if w_over > 1.0 and b_under > 1.0:
            arb = self.calculate_2way_arbitrage(
                {"bookmaker": "Winamax", "selection": "Més de 2.5 Gols (Over)", "odd": w_over, "url": w_url},
                {"bookmaker": "Bet365", "selection": "Menys de 2.5 Gols (Under)", "odd": b_under, "url": b_url},
                "Total Gols (Més/Menys 2.5)",
                match_info
            )
            if arb: arbs.append(arb)
        if b_over > 1.0 and w_under > 1.0:
            arb = self.calculate_2way_arbitrage(
                {"bookmaker": "Bet365", "selection": "Més de 2.5 Gols (Over)", "odd": b_over, "url": b_url},
                {"bookmaker": "Winamax", "selection": "Menys de 2.5 Gols (Under)", "odd": w_under, "url": w_url},
                "Total Gols (Més/Menys 2.5)",
                match_info
            )
            if arb: arbs.append(arb)

        # 4. Ambdós Equips Marquen (BTTS)
        w_btts = match.get("odds_btts_winamax") or {}
        b_btts = match.get("odds_btts_bet365") or {}
        w_byes = float(w_btts.get("yes") or 0.0)
        w_bno = float(w_btts.get("no") or 0.0)
        b_byes = float(b_btts.get("yes") or 0.0)
        b_bno = float(b_btts.get("no") or 0.0)

        if w_byes > 1.0 and b_bno > 1.0:
            arb = self.calculate_2way_arbitrage(
                {"bookmaker": "Winamax", "selection": "Ambdós Marquen: SÍ", "odd": w_byes, "url": w_url},
                {"bookmaker": "Bet365", "selection": "Ambdós Marquen: NO", "odd": b_bno, "url": b_url},
                "Ambdós Marquen (BTTS)",
                match_info
            )
            if arb: arbs.append(arb)
        if b_byes > 1.0 and w_bno > 1.0:
            arb = self.calculate_2way_arbitrage(
                {"bookmaker": "Bet365", "selection": "Ambdós Marquen: SÍ", "odd": b_byes, "url": b_url},
                {"bookmaker": "Winamax", "selection": "Ambdós Marquen: NO", "odd": w_bno, "url": w_url},
                "Ambdós Marquen (BTTS)",
                match_info
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
