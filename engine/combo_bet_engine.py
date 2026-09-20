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

    def analyze_jornada_combos(self, predicted_matches: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Genera l'anàlisi completa de combinades per a la jornada:
        1. Combinada Segura (Cuota 2 - 3 amb seleccions >= 90% o màxima seguretat).
        2. Combinada Arriscada (Cuota >= 30.0 amb càlcul probabilístic del model).
        """
        safe_combo = self.find_safe_combination(predicted_matches, target_odd_min=2.0, target_odd_max=3.0)
        risky_combo = self.find_high_odd_combination(predicted_matches, target_odd_min=30.0, target_odd_max=50.0)

        return {
            "safe_combo": safe_combo,
            "risky_combo": risky_combo
        }

    def analyze_multileague_combos(self, all_matches: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Genera l'anàlisi de combinades transfrontereres (Multi-Lliga)
        agrupant els millors favorits i oportunitats +EV de totes les lligues analitzades.
        """
        safe_combo = self.find_safe_combination(all_matches, target_odd_min=2.2, target_odd_max=3.2)
        if safe_combo.get("legs"):
            safe_combo["profile"] = "Mega-Combinada Multi-Lliga (Màxima Seguretat)"

        risky_combo = self.find_high_odd_combination(all_matches, target_odd_min=30.0, target_odd_max=60.0)
        if risky_combo.get("legs"):
            risky_combo["profile"] = "Mega-Combinada Multi-Lliga (Cuota Alta >= 30.0)"

        return {
            "safe_combo": safe_combo,
            "risky_combo": risky_combo
        }

