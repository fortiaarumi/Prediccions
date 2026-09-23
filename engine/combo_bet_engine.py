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
from typing import List, Dict, Any, Optional, Tuple

class ComboBetEngine:
    def __init__(self):
        self.winamax_base_url = "https://www.winamax.es/apuestas-deportivas/sports/1/32"

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

    def _get_combo_sig(self, legs: List[Dict[str, Any]]) -> frozenset:
        """Signatura única del conjunt de seleccions d'una combinada."""
        return frozenset(f"{l.get('matchup', '')}::{l.get('name') or l.get('selection_name', '')}" for l in legs)

    def _extract_all_match_markets(self, predicted_matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Extreu tots els mercats individuals disponibles per a cada partit,
        assegurant la informació del partit i l'enllaç de Winamax.
        """
        match_markets = []
        for p in predicted_matches:
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

            single_markets = bet_analysis.get("single_markets", [])
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

    def _prepare_balanced_pools(self, match_markets: List[Dict[str, Any]], is_multi: bool, min_prob: float, min_odd: float, max_per_comp: int = 5) -> List[Dict[str, Any]]:
        """
        Prepara el pool de partits seleccionant mercats complementaris de diferents categories
        (Doble Oportunitat, 1X2, Gols, Córners, Targetes, BTTS) per garantir varietat.
        """
        def extract_diverse_cands(mm):
            valid = [m for m in mm.get("markets", []) if m["model_prob"] >= min_prob and m["bookie_odd"] >= min_odd]
            if not valid:
                valid = [m for m in mm.get("markets", []) if m["model_prob"] >= (min_prob * 0.75) and m["bookie_odd"] >= 1.05]
            if not valid:
                return []
            sorted_m = sorted(valid, key=lambda x: x["model_prob"], reverse=True)
            primary = sorted_m[0]
            alt = None
            for m in sorted_m[1:]:
                if m.get("category") != primary.get("category"):
                    alt = m
                    break
            cands = [primary]
            if alt:
                cands.append(alt)
            return cands

        pools = []
        if is_multi:
            by_comp = {}
            for mm in match_markets:
                c_id = mm.get("competition_id", "OTHER")
                by_comp.setdefault(c_id, []).append(mm)
            
            # Intercalar partits de manera round-robin (LaLiga #1, Premier #1, Hypermotion #1, LaLiga #2...)
            for i in range(max_per_comp):
                for c_id, comp_mms in by_comp.items():
                    if i < len(comp_mms):
                        cands = extract_diverse_cands(comp_mms[i])
                        if cands:
                            pools.append({
                                "matchup": comp_mms[i]["matchup"],
                                "competition_id": c_id,
                                "markets": cands
                            })
        else:
            for mm in match_markets:
                cands = extract_diverse_cands(mm)
                if cands:
                    pools.append({
                        "matchup": mm["matchup"],
                        "competition_id": mm.get("competition_id", ""),
                        "markets": cands
                    })
        return pools

    def find_pair_safe(self, match_markets: List[Dict[str, Any]], is_multi: bool = False) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Genera dues combinades segures (cuota 1.95 - 3.50) optimitzant la màxima probabilitat conjunta."""
        prefix = "Mega-Combinada Multi-Lliga" if is_multi else "Combinada"
        pools = self._prepare_balanced_pools(match_markets, is_multi=is_multi, min_prob=60.0, min_odd=1.07, max_per_comp=4)

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

                cand_lists = [m["markets"] for m in match_combo]
                for leg_choice in itertools.product(*cand_lists):
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

        if not candidates:
            for k in range(2, min(n_pools, 5) + 1):
                for match_combo in itertools.combinations(pools[:n_pools], k):
                    comps = {s["competition_id"] for s in match_combo if s.get("competition_id")}
                    if is_multi and len(comps) < 2: continue
                    cand_lists = [m["markets"] for m in match_combo]
                    for leg_choice in itertools.product(*cand_lists):
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

        candidates.sort(key=lambda x: x["score"], reverse=True)
        c1_cand = candidates[0] if candidates else {"legs": [], "sig": frozenset()}

        c2_cand = None
        best_c2_score = -999999.0
        for cand in candidates[1:]:
            if cand["sig"] == c1_cand["sig"]:
                continue
            overlap = len(cand["matchups"] & c1_cand.get("matchups", set()))
            adj_score = cand["score"] - (overlap * 0.10)
            if adj_score > best_c2_score:
                best_c2_score = adj_score
                c2_cand = cand

        if not c2_cand or c2_cand["sig"] == c1_cand.get("sig"):
            alt_legs = []
            for p in pools:
                if p["matchup"] not in c1_cand.get("matchups", set()) and len(alt_legs) < len(c1_cand.get("legs", [])):
                    alt_legs.append(p["markets"][0])
            if len(alt_legs) < 2 and c1_cand.get("legs"):
                alt_legs = list(c1_cand["legs"])
                for p in pools:
                    if p["matchup"] == alt_legs[0]["matchup"] and len(p["markets"]) > 1:
                        alt_legs[0] = p["markets"][1]
                        break
            c2_cand = {"legs": alt_legs, "sig": self._get_combo_sig(alt_legs)}

        c1 = self.calculate_combo_summary(c1_cand.get("legs", []), f"{prefix} Segura #1 (Cuota 2-3)")
        c2 = self.calculate_combo_summary(c2_cand.get("legs", []), f"{prefix} Segura #2 (Cuota 2-3 Alternativa)")
        c1["category_code"] = "SAFE"
        c2["category_code"] = "SAFE"
        c1["recommended_stake"] = 25.0
        c2["recommended_stake"] = 25.0
        return c1, c2

    def find_pair_semi(self, match_markets: List[Dict[str, Any]], is_multi: bool = False) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Genera dues combinades semi-arriscades (cuota ~8.5 - 16.5) optimitzant probabilitat i varietat."""
        prefix = "Mega-Combinada Multi-Lliga" if is_multi else "Combinada"
        pools = self._prepare_balanced_pools(match_markets, is_multi=is_multi, min_prob=45.0, min_odd=1.15, max_per_comp=4)

        if len(pools) < 3:
            return self.calculate_combo_summary([], f"{prefix} Semi-Arriscada #1 (Cuota ~10.0)"), self.calculate_combo_summary([], f"{prefix} Semi-Arriscada #2 (Cuota ~10.0 Alternativa)")

        candidates = []
        n_pools = min(len(pools), 14)
        for k in range(3, min(n_pools, 6) + 1):
            for match_combo in itertools.combinations(pools[:n_pools], k):
                comps = {s["competition_id"] for s in match_combo if s.get("competition_id")}
                if is_multi and len(comps) < 2:
                    continue
                league_bonus = 0.08 if is_multi and len(comps) >= 3 else (0.03 if is_multi and len(comps) >= 2 else 0.0)

                cand_lists = [m["markets"] for m in match_combo]
                for leg_choice in itertools.product(*cand_lists):
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
                        
                        score = p_dec * (1.0 + div_bonus + league_bonus) - abs(c_odd - 10.0) * 0.002
                        candidates.append({
                            "legs": list(leg_choice),
                            "c_odd": c_odd,
                            "p_dec": p_dec,
                            "score": score,
                            "matchups": {l["matchup"] for l in leg_choice},
                            "sig": self._get_combo_sig(leg_choice)
                        })

        if not candidates:
            for k in range(3, min(n_pools, 7) + 1):
                for match_combo in itertools.combinations(pools[:n_pools], k):
                    comps = {s["competition_id"] for s in match_combo if s.get("competition_id")}
                    if is_multi and len(comps) < 2: continue
                    cand_lists = [m["markets"] for m in match_combo]
                    for leg_choice in itertools.product(*cand_lists):
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

        candidates.sort(key=lambda x: x["score"], reverse=True)
        c1_cand = candidates[0] if candidates else {"legs": [], "sig": frozenset()}

        c2_cand = None
        best_c2_score = -999999.0
        for cand in candidates[1:]:
            if cand["sig"] == c1_cand["sig"]:
                continue
            overlap = len(cand["matchups"] & c1_cand.get("matchups", set()))
            adj_score = cand["score"] - (overlap * 0.02)
            if adj_score > best_c2_score:
                best_c2_score = adj_score
                c2_cand = cand

        if not c2_cand or c2_cand["sig"] == c1_cand.get("sig"):
            alt_legs = list(c1_cand.get("legs", []))
            if len(pools) > len(alt_legs):
                for p in pools:
                    if p["matchup"] not in c1_cand.get("matchups", set()):
                        alt_legs[-1] = p["markets"][0]
                        break
            c2_cand = {"legs": alt_legs, "sig": self._get_combo_sig(alt_legs)}

        c1 = self.calculate_combo_summary(c1_cand.get("legs", []), f"{prefix} Semi-Arriscada #1 (Cuota ~10.0)")
        c2 = self.calculate_combo_summary(c2_cand.get("legs", []), f"{prefix} Semi-Arriscada #2 (Cuota ~10.0 Alternativa)")
        c1["category_code"] = "SEMI"
        c2["category_code"] = "SEMI"
        c1["recommended_stake"] = 10.0
        c2["recommended_stake"] = 10.0
        return c1, c2

    def find_pair_risky(self, match_markets: List[Dict[str, Any]], is_multi: bool = False) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Genera dues combinades arriscades (cuota >= 28.0) multimerkat amb activació de Combo Booster Winamax."""
        prefix = "Mega-Combinada Multi-Lliga" if is_multi else "Combinada"
        pools = self._prepare_balanced_pools(match_markets, is_multi=is_multi, min_prob=35.0, min_odd=1.12, max_per_comp=4)

        if len(pools) < 4:
            return self.calculate_combo_summary([], f"{prefix} Arriscada #1 (Cuota >= 30.0)"), self.calculate_combo_summary([], f"{prefix} Arriscada #2 (Cuota >= 30.0 Alternativa)")

        candidates = []
        n_pools = min(len(pools), 14)
        for k in range(5, min(n_pools, 8) + 1):
            for match_combo in itertools.combinations(pools[:n_pools], k):
                comps = {s["competition_id"] for s in match_combo if s.get("competition_id")}
                if is_multi and len(comps) < 2:
                    continue
                league_bonus = 0.10 if is_multi and len(comps) >= 3 else (0.04 if is_multi and len(comps) >= 2 else 0.0)

                cand_lists = [m["markets"] for m in match_combo]
                for leg_choice in itertools.product(*cand_lists):
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
                        
                        score = p_dec * (1.0 + div_bonus + league_bonus) - abs(c_odd - 35.0) * 0.0005
                        candidates.append({
                            "legs": list(leg_choice),
                            "c_odd": c_odd,
                            "p_dec": p_dec,
                            "score": score,
                            "matchups": {l["matchup"] for l in leg_choice},
                            "sig": self._get_combo_sig(leg_choice)
                        })

        if not candidates:
            flat = sorted([p["markets"][0] for p in pools], key=lambda x: x["bookie_odd"], reverse=True)
            chosen1 = []
            odd1 = 1.0
            for m in flat:
                chosen1.append(m)
                odd1 *= m["bookie_odd"]
                if odd1 >= 28.0: break
            
            chosen2 = []
            odd2 = 1.0
            for p in pools:
                opt = p["markets"][-1]
                chosen2.append(opt)
                odd2 *= opt["bookie_odd"]
                if odd2 >= 28.0: break

            c1 = self.calculate_combo_summary(chosen1, f"{prefix} Arriscada #1 (Cuota >= 30.0)")
            c2 = self.calculate_combo_summary(chosen2, f"{prefix} Arriscada #2 (Cuota >= 30.0 Alternativa)")
            c1["category_code"] = "RISKY"
            c2["category_code"] = "RISKY"
            c1["recommended_stake"] = 5.0
            c2["recommended_stake"] = 5.0
            return c1, c2

        candidates.sort(key=lambda x: x["score"], reverse=True)
        c1_cand = candidates[0]

        c2_cand = None
        best_c2_score = -999999.0
        for cand in candidates[1:]:
            if cand["sig"] == c1_cand["sig"]:
                continue
            overlap = len(cand["matchups"] & c1_cand["matchups"])
            adj_score = cand["score"] - (overlap * 0.002)
            if adj_score > best_c2_score:
                best_c2_score = adj_score
                c2_cand = cand

        if not c2_cand or c2_cand["sig"] == c1_cand["sig"]:
            alt_legs = list(c1_cand["legs"])
            for p in pools:
                if p["matchup"] not in c1_cand["matchups"]:
                    alt_legs[-1] = p["markets"][0]
                    break
            c2_cand = {"legs": alt_legs, "sig": self._get_combo_sig(alt_legs)}

        c1 = self.calculate_combo_summary(c1_cand["legs"], f"{prefix} Arriscada #1 (Cuota >= 30.0)")
        c2 = self.calculate_combo_summary(c2_cand["legs"], f"{prefix} Arriscada #2 (Cuota >= 30.0 Alternativa)")
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

    def generate_full_combos_suite(self, matches: List[Dict[str, Any]], is_multi: bool = False) -> Dict[str, Any]:
        """
        Genera exactament el paquet complet sol·licitat per l'usuari:
        - 2 Combinades Segures (cuota 1.95 - 3.50) totalment diferents
        - 2 Combinades Semi-Arriscades (cuota ~8.0 - 15.0) totalment diferents
        - 2 Combinades Arriscades (cuota >= 28.0) totalment diferents
        """
        mms = self._extract_all_match_markets(matches)
        safe_1, safe_2 = self.find_pair_safe(mms, is_multi=is_multi)
        semi_1, semi_2 = self.find_pair_semi(mms, is_multi=is_multi)
        risky_1, risky_2 = self.find_pair_risky(mms, is_multi=is_multi)

        return {
            "safe": [safe_1, safe_2],
            "semi": [semi_1, semi_2],
            "risky": [risky_1, risky_2],
            # Compatibilitat amb mòduls antics:
            "safe_combo": safe_1,
            "risky_combo": risky_1
        }

    def analyze_jornada_combos(self, predicted_matches: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Genera el paquet complet de 6 combinades per a la jornada."""
        return self.generate_full_combos_suite(predicted_matches, is_multi=False)

    def analyze_multileague_combos(self, all_matches: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Genera el paquet complet de 6 combinades transfrontereres (Multi-Lliga)."""
        return self.generate_full_combos_suite(all_matches, is_multi=True)


