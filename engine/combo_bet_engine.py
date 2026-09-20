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
from typing import List, Dict, Any, Optional

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
                "winamax_url": self.winamax_base_url
            }

        odds_list = [leg["bookie_odd"] for leg in legs]
        probs_list = [leg["model_prob"] for leg in legs]

        combined_odd = self.calculate_combined_odds(odds_list)
        p_dec = self.calculate_combined_probability(probs_list)
        combined_prob_pct = round(p_dec * 100.0, 1)

        fair_odd = round(1.0 / p_dec, 2) if p_dec > 0.0 else 999.0
        ev_pct = round((p_dec * combined_odd - 1.0) * 100.0, 1)

        return {
            "profile": profile_name,
            "legs": legs,
            "legs_count": len(legs),
            "combined_odd": combined_odd,
            "combined_prob_pct": combined_prob_pct,
            "fair_odd": fair_odd,
            "ev_pct": ev_pct,
            "is_value": ev_pct > 0,
            "winamax_url": self.winamax_base_url
        }

    def _extract_all_match_markets(self, predicted_matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Extreu tots els mercats individuals disponibles per a cada partit,
        assegurant la informació del partit i l'enllaç de Winamax.
        """
        match_markets = []
        for p in predicted_matches:
            matchup = p.get("matchup", "Partit")
            date_str = p.get("date", "Pendent")
            odds_info = p.get("odds", {})
            match_url = odds_info.get("url") or self.winamax_base_url
            bet_analysis = p.get("bet_analysis", {})
            single_markets = bet_analysis.get("single_markets", [])

            valid_markets = []
            for m in single_markets:
                odd = m.get("bookie_odd")
                prob = m.get("model_prob")
                if odd and odd > 1.05 and prob and prob > 0:
                    item = dict(m)
                    item["matchup"] = matchup
                    item["date"] = date_str
                    item["url"] = match_url
                    valid_markets.append(item)

            if not valid_markets:
                # Si Winamax no té cuotes obertes encara, utilitzem cuotes de mercat estimades segons el model
                goals = p.get("goals", {})
                prob_1x2 = goals.get("prob_1X2", {})
                p1 = float(prob_1x2.get("1", 0.33))
                px = float(prob_1x2.get("X", 0.33))
                p2 = float(prob_1x2.get("2", 0.33))
                h_name = p.get("home_team", {}).get("name") if isinstance(p.get("home_team"), dict) else str(p.get("home_team", "Local"))
                a_name = p.get("away_team", {}).get("name") if isinstance(p.get("away_team"), dict) else str(p.get("away_team", "Visitant"))

                p_1x = p1 + px
                p_x2 = px + p2

                synth = [
                    {"name": f"1X - {h_name} o Empat", "category": "Doble Oportunitat", "bookie_odd": max(1.10, round(0.93 / p_1x, 2)), "model_prob": round(p_1x * 100, 1)},
                    {"name": f"X2 - Empat o {a_name}", "category": "Doble Oportunitat", "bookie_odd": max(1.10, round(0.93 / p_x2, 2)), "model_prob": round(p_x2 * 100, 1)},
                    {"name": f"1 - Victòria Local ({h_name})", "category": "1X2", "bookie_odd": max(1.15, round(0.93 / p1, 2)), "model_prob": round(p1 * 100, 1)},
                    {"name": f"2 - Victòria Visitant ({a_name})", "category": "1X2", "bookie_odd": max(1.15, round(0.93 / p2, 2)), "model_prob": round(p2 * 100, 1)},
                ]

                prob_ou = goals.get("prob_over_under_2_5", {})
                p_over = float(prob_ou.get("over", 0.5))
                p_under = float(prob_ou.get("under", 0.5))
                if p_over > 0:
                    synth.append({"name": "Més de 2.5 gols", "category": "Gols", "bookie_odd": max(1.20, round(0.93 / p_over, 2)), "model_prob": round(p_over * 100, 1)})
                if p_under > 0:
                    synth.append({"name": "Menys de 2.5 gols", "category": "Gols", "bookie_odd": max(1.20, round(0.93 / p_under, 2)), "model_prob": round(p_under * 100, 1)})

                for s in synth:
                    s["matchup"] = matchup
                    s["date"] = date_str
                    s["url"] = match_url
                    valid_markets.append(s)

            if valid_markets:
                match_markets.append({
                    "matchup": matchup,
                    "date": date_str,
                    "url": match_url,
                    "markets": valid_markets
                })

        return match_markets

    def find_safe_combination(self, predicted_matches: List[Dict[str, Any]], target_odd_min: float = 2.0, target_odd_max: float = 3.0) -> Dict[str, Any]:
        """
        Troba la Combinada Segura:
        - Cada selecció individual té probabilitat model >= 90% (o el màxim possible >= 85%-80%).
        - Afegeix seleccions de partits independents fins a assolir una cuota acumulada entre 2.0 i 3.0.
        - En cas de múltiples opcions, prioritza aquelles amb millor valor esperat (+EV%).
        """
        match_markets = self._extract_all_match_markets(predicted_matches)
        if not match_markets:
            return self.calculate_combo_summary([], "Combinada Segura (Cuota 2-3)")

        # Intentem primer llindar estricte >= 90.0%, si cal baixem a 85.0%, després a 80.0%
        for min_prob_threshold in [90.0, 86.0, 82.0, 78.0]:
            candidates_by_match = []
            for mm in match_markets:
                # Filtrar mercats amb probabilitat >= threshold
                high_prob = [m for m in mm["markets"] if m["model_prob"] >= min_prob_threshold and m["bookie_odd"] >= 1.08]
                if high_prob:
                    # Triar la millor opció del partit (major probabilitat i millor EV)
                    best_option = max(high_prob, key=lambda x: (x["model_prob"], x.get("ev_pct", 0)))
                    candidates_by_match.append(best_option)

            if len(candidates_by_match) >= 2:
                # Ordenar candidats per probabilitat descendent (de més segur a menys)
                candidates_by_match.sort(key=lambda x: (x["model_prob"], x.get("ev_pct", 0)), reverse=True)

                # Anar afegint seleccions d'equips diferents fins a assolir la cuota objectiu [2.0, 3.0]
                chosen_legs = []
                current_odd = 1.0

                for cand in candidates_by_match:
                    chosen_legs.append(cand)
                    current_odd *= cand["bookie_odd"]
                    if current_odd >= target_odd_min:
                        break

                if current_odd >= 1.70: # Molt a prop o dins del rang 2-3
                    return self.calculate_combo_summary(chosen_legs, "Combinada de Màxima Seguretat (Cuota 2 - 3)")

        # Si no s'arriba exactament pel rang de cuotes baixes, agafar els 3 millors favorits
        fallback_candidates = []
        for mm in match_markets:
            if mm["markets"]:
                safest = max(mm["markets"], key=lambda x: x["model_prob"])
                fallback_candidates.append(safest)
        fallback_candidates.sort(key=lambda x: x["model_prob"], reverse=True)

        chosen_legs = []
        current_odd = 1.0
        for cand in fallback_candidates[:5]:
            chosen_legs.append(cand)
            current_odd *= cand["bookie_odd"]
            if current_odd >= target_odd_min:
                break

        return self.calculate_combo_summary(chosen_legs, "Combinada de Màxima Seguretat (Cuota 2 - 3)")

    def find_high_odd_combination(self, predicted_matches: List[Dict[str, Any]], target_odd_min: float = 30.0, target_odd_max: float = 65.0) -> Dict[str, Any]:
        """
        Troba la Combinada Arriscada (Cuota >= 30.0) amb Major Seguretat:
        - Cada selecció individual té com a mínim un 50% de probabilitat segons el model (o el màxim possible per arribar a 30).
        - Afegeix seleccions de més partits independents (de 3 a 8 partits) per assolir cuota >= 30.0.
        - Avalua totes les combinacions possibles i tria la de Cuota >= 30.0 amb la MAJOR SEGURETAT (màxima probabilitat conjunta P_comb).
        """
        match_markets = self._extract_all_match_markets(predicted_matches)
        if not match_markets:
            return self.calculate_combo_summary([], "Combinada Cuota Alta (>= 30.0)")

        best_combo = None
        best_score = -999999.0

        # Provem llindars de probabilitat individual començant estrictament per >= 50.0%
        for min_p in [50.0, 45.0, 40.0, 35.0]:
            pools_by_match = []
            for mm in match_markets:
                valid_m = [m for m in mm["markets"] if m["model_prob"] >= min_p and m["bookie_odd"] >= 1.15]
                if valid_m:
                    valid_m.sort(key=lambda x: (x["model_prob"], x.get("ev_pct", 0)), reverse=True)
                    pools_by_match.append(valid_m[:3])

            if len(pools_by_match) < 3:
                continue

            # Limitar a un màxim de 7 partits candidats per garantir execució en mil·lisegons
            if len(pools_by_match) > 7:
                pools_by_match.sort(key=lambda p: max(m["model_prob"] for m in p), reverse=True)
                pools_by_match = pools_by_match[:7]

            max_possible_odd = 1.0
            for p in pools_by_match:
                max_possible_odd *= max(m["bookie_odd"] for m in p)

            if max_possible_odd < target_odd_min:
                continue

            match_indices = list(range(len(pools_by_match)))
            max_k = min(len(pools_by_match), 8)

            for k in range(3, max_k + 1):
                for match_subset in itertools.combinations(match_indices, k):
                    leg_options = [pools_by_match[idx] for idx in match_subset]
                    for leg_combo in itertools.product(*leg_options):
                        combo_odd = 1.0
                        for leg in leg_combo:
                            combo_odd *= leg["bookie_odd"]

                        if combo_odd >= target_odd_min:
                            probs = [leg["model_prob"] for leg in leg_combo]
                            p_dec = self.calculate_combined_probability(probs)
                            penalty = max(0.0, combo_odd - target_odd_max) * 0.0002
                            score = p_dec - penalty

                            if score > best_score:
                                best_score = score
                                best_combo = list(leg_combo)

            if best_combo:
                break

        if not best_combo:
            flat = []
            seen = set()
            for mm in match_markets:
                if mm["matchup"] not in seen and mm["markets"]:
                    seen.add(mm["matchup"])
                    best_m = max(mm["markets"], key=lambda x: (x["model_prob"] >= 50.0, x["bookie_odd"]))
                    flat.append(best_m)
            flat.sort(key=lambda x: x["bookie_odd"], reverse=True)
            chosen = []
            odd = 1.0
            for c in flat:
                chosen.append(c)
                odd *= c["bookie_odd"]
                if odd >= target_odd_min:
                    break
            best_combo = chosen

        return self.calculate_combo_summary(best_combo, "Combinada de Cuota Alta (Cuota >= 30.0 | Major Seguretat)")

    def find_semi_combination(
        self,
        predicted_matches: List[Dict[str, Any]],
        target_odd_min: float = 8.0,
        target_odd_max: float = 14.0,
        exclude_legs: List[str] = None
    ) -> Dict[str, Any]:
        """
        Genera una Combinada Semi-Arriscada (Cuota ~10.0):
        - Seleccions amb probabilitat individual >= 70% (o 65%).
        - Objectiu cuota acumulada entre 8.0 i 14.0 (ideal ~10.0).
        """
        match_markets = self._extract_all_match_markets(predicted_matches)
        if not match_markets:
            return self.calculate_combo_summary([], "Combinada Semi-Arriscada (Cuota ~10.0)")

        exclude_set = set(exclude_legs or [])

        for min_prob in [75.0, 70.0, 65.0]:
            pools_by_match = []
            for mm in match_markets:
                valid_m = [
                    m for m in mm["markets"]
                    if m["model_prob"] >= min_prob and m["bookie_odd"] >= 1.25
                    and f"{m['matchup']}_{m['name']}" not in exclude_set
                ]
                if valid_m:
                    valid_m.sort(key=lambda x: (x["model_prob"], x.get("ev_pct", 0)), reverse=True)
                    pools_by_match.append(valid_m[:2])

            if len(pools_by_match) < 3:
                continue

            # Limitar a un màxim de 7 partits candidats per garantir execució instantània
            if len(pools_by_match) > 7:
                pools_by_match.sort(key=lambda p: max(m["model_prob"] for m in p), reverse=True)
                pools_by_match = pools_by_match[:7]

            best_combo = None
            best_diff = 999.0

            for k in range(3, min(len(pools_by_match), 7) + 1):
                for match_subset in itertools.combinations(range(len(pools_by_match)), k):
                    leg_options = [pools_by_match[idx] for idx in match_subset]
                    for leg_combo in itertools.product(*leg_options):
                        odd = 1.0
                        for leg in leg_combo:
                            odd *= leg["bookie_odd"]

                        if target_odd_min <= odd <= target_odd_max:
                            diff = abs(odd - 10.0)
                            if diff < best_diff:
                                best_diff = diff
                                best_combo = list(leg_combo)

            if best_combo:
                return self.calculate_combo_summary(best_combo, "Combinada Semi-Arriscada (Cuota ~10.0)")

        # Fallback greedy
        flat = []
        for mm in match_markets:
            candidates = [m for m in mm["markets"] if m["model_prob"] >= 60.0 and f"{m['matchup']}_{m['name']}" not in exclude_set]
            if candidates:
                best_m = max(candidates, key=lambda x: (x["model_prob"], x["bookie_odd"]))
                flat.append(best_m)
        flat.sort(key=lambda x: x["model_prob"], reverse=True)
        chosen = []
        c_odd = 1.0
        for m in flat:
            chosen.append(m)
            c_odd *= m["bookie_odd"]
            if c_odd >= target_odd_min:
                break

        return self.calculate_combo_summary(chosen, "Combinada Semi-Arriscada (Cuota ~10.0)")

    def generate_full_combos_suite(self, matches: List[Dict[str, Any]], is_multi: bool = False) -> Dict[str, Any]:
        """
        Genera exactament el paquet complet sol·licitat per l'usuari:
        - 2 Combinades Segures (cuota 2.0 - 3.5)
        - 2 Combinades Semi-Arriscades (cuota ~10.0)
        - 2 Combinades Arriscades (cuota >= 30.0)
        """
        prefix = "Mega-Combinada Multi-Lliga" if is_multi else "Combinada"

        # 1. Segura #1
        safe_1 = self.find_safe_combination(matches, target_odd_min=2.0, target_odd_max=3.5)
        safe_1["profile"] = f"{prefix} Segura #1 (Cuota 2-3)"
        safe_1["recommended_stake"] = 25.0
        safe_1["category_code"] = "SAFE"

        # 2. Segura #2 (amb variacions)
        used_s1 = [f"{l['matchup']}_{l['name']}" for l in safe_1.get("legs", [])]
        # Si tenim suficients partits, intentem diversificar
        safe_2 = self.find_safe_combination(matches, target_odd_min=2.2, target_odd_max=3.8)
        safe_2["profile"] = f"{prefix} Segura #2 (Cuota 2-3 Alternativa)"
        safe_2["recommended_stake"] = 25.0
        safe_2["category_code"] = "SAFE"

        # 3. Semi #1 (cuota ~10.0)
        semi_1 = self.find_semi_combination(matches, target_odd_min=7.5, target_odd_max=13.0)
        semi_1["profile"] = f"{prefix} Semi-Arriscada #1 (Cuota ~10.0)"
        semi_1["recommended_stake"] = 10.0
        semi_1["category_code"] = "SEMI"

        # 4. Semi #2 (cuota ~10.0 alternativa)
        used_semi1 = [f"{l['matchup']}_{l['name']}" for l in semi_1.get("legs", [])]
        semi_2 = self.find_semi_combination(matches, target_odd_min=8.5, target_odd_max=15.0, exclude_legs=used_semi1[:2])
        semi_2["profile"] = f"{prefix} Semi-Arriscada #2 (Cuota ~10.0 Alternativa)"
        semi_2["recommended_stake"] = 10.0
        semi_2["category_code"] = "SEMI"

        # 5. Arriscada #1 (cuota >= 30.0)
        risky_1 = self.find_high_odd_combination(matches, target_odd_min=28.0, target_odd_max=55.0)
        risky_1["profile"] = f"{prefix} Arriscada #1 (Cuota >= 30.0)"
        risky_1["recommended_stake"] = 5.0
        risky_1["category_code"] = "RISKY"

        # 6. Arriscada #2 (cuota >= 30.0 alternativa)
        risky_2 = self.find_high_odd_combination(matches, target_odd_min=32.0, target_odd_max=70.0)
        risky_2["profile"] = f"{prefix} Arriscada #2 (Cuota >= 30.0 Alternativa)"
        risky_2["recommended_stake"] = 5.0
        risky_2["category_code"] = "RISKY"

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

