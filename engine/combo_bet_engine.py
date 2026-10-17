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
from collections import Counter
from typing import List, Dict, Any, Optional, Tuple, Set

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

    def _get_leg_key(self, leg: Dict[str, Any]) -> str:
        """Identificador únic normalitzat d'una selecció exacta (matchup + nom de mercat)."""
        name = leg.get("name") or leg.get("selection_name") or ""
        matchup = leg.get("matchup") or ""
        return f"{matchup.strip()}::{name.strip()}"

    def _get_combo_sig(self, legs: List[Dict[str, Any]]) -> frozenset:
        """Signatura única del conjunt de seleccions d'una combinada."""
        return frozenset(self._get_leg_key(l) for l in legs)

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
        for p in pools:
            if p["matchup"] not in c1_matchups:
                for m in p["markets"]:
                    k = self._get_leg_key(m)
                    if k not in forbidden_exact and k not in c1_leg_keys:
                        alt_legs.append(m)
                        break
                if len(alt_legs) >= k_len:
                    break

        if len(alt_legs) >= 2:
            return {"legs": alt_legs, "sig": self._get_combo_sig(alt_legs)}

        return {"legs": [], "sig": frozenset()}

    def find_pair_safe(
        self,
        match_markets: List[Dict[str, Any]],
        is_multi: bool = False,
        global_counts: Optional[Dict[str, int]] = None,
        forbidden_exact: Optional[Set[str]] = None,
        max_usage: int = 2
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

                cand_lists = [m["markets"][:2] for m in match_combo]
                for leg_choice in itertools.product(*cand_lists):
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
                    cand_lists = [m["markets"][:2] for m in match_combo]
                    for leg_choice in itertools.product(*cand_lists):
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
        max_usage: int = 2
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

                cand_lists = [m["markets"][:2] for m in match_combo]
                for leg_choice in itertools.product(*cand_lists):
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
            for k in range(3, min(n_pools, 6) + 1):
                for match_combo in itertools.combinations(pools[:n_pools], k):
                    comps = {s["competition_id"] for s in match_combo if s.get("competition_id")}
                    if is_multi and len(comps) < 2 and len(set(m.get("competition_id") for m in pools if m.get("competition_id"))) >= 2:
                        continue
                    cand_lists = [m["markets"][:2] for m in match_combo]
                    for leg_choice in itertools.product(*cand_lists):
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
        c1_cand = candidates[0] if candidates else {"legs": [], "sig": frozenset()}

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
        )

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
        max_usage: int = 2
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Genera dues combinades arriscades (cuota >= 28.0) multimerkat amb activació de Combo Booster Winamax."""
        prefix = "Mega-Combinada Multi-Lliga" if is_multi else "Combinada"
        global_counts = global_counts if global_counts is not None else Counter()
        forbidden_exact = forbidden_exact or set()

        pools = self._prepare_balanced_pools(
            match_markets,
            is_multi=is_multi,
            min_prob=25.0,
            min_odd=1.28,
            max_per_comp=4,
            forbidden_exact=forbidden_exact,
            counts=global_counts,
            max_usage=max_usage
        )

        if len(pools) < 4:
            return self.calculate_combo_summary([], f"{prefix} Arriscada #1 (Cuota >= 30.0)"), self.calculate_combo_summary([], f"{prefix} Arriscada #2 (Cuota >= 30.0 Alternativa)")

        candidates = []
        n_pools = min(len(pools), 14)
        for k in range(4, min(n_pools, 8) + 1):
            for match_combo in itertools.combinations(pools[:n_pools], k):
                comps = {s["competition_id"] for s in match_combo if s.get("competition_id")}
                if is_multi and len(comps) < 2:
                    continue
                league_bonus = 0.10 if is_multi and len(comps) >= 3 else (0.04 if is_multi and len(comps) >= 2 else 0.0)

                cand_lists = [m["markets"][:2] for m in match_combo]
                for leg_choice in itertools.product(*cand_lists):
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
            # Fallback si el rang estricte no es compleix amb el filtre
            for k in range(4, min(n_pools, 8) + 1):
                for match_combo in itertools.combinations(pools[:n_pools], k):
                    comps = {s["competition_id"] for s in match_combo if s.get("competition_id")}
                    if is_multi and len(comps) < 2 and len(set(m.get("competition_id") for m in pools if m.get("competition_id"))) >= 2:
                        continue
                    cand_lists = [m["markets"][:2] for m in match_combo]
                    for leg_choice in itertools.product(*cand_lists):
                        if any(self._get_leg_key(l) in forbidden_exact or global_counts.get(self._get_leg_key(l), 0) >= max_usage for l in leg_choice):
                            continue
                        odds = [l["bookie_odd"] for l in leg_choice]
                        c_odd = round(self.calculate_combined_odds(odds), 2)
                        if c_odd >= 20.0:
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
        c1_cand = candidates[0] if candidates else {"legs": [], "sig": frozenset()}

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
        )

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
        matchup = match.get("matchup", "Partit")
        comp_id = match.get("competition_id", "")
        date_str = match.get("date", "")
        url = match.get("url") or match.get("winamax_url") or self.winamax_base_url

        if " vs " in matchup:
            parts = matchup.split(" vs ", 1)
            h_name = parts[0].strip()
            a_name = parts[1].strip()
        else:
            h_name = match.get("home_team", {}).get("name") if isinstance(match.get("home_team"), dict) else str(match.get("home_team") or "Local")
            a_name = match.get("away_team", {}).get("name") if isinstance(match.get("away_team"), dict) else str(match.get("away_team") or "Visitant")

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
                seen_keys.add(self._get_leg_key(item_sc))

        return markets

    def generate_single_match_combos_suite(self, match: Dict[str, Any]) -> Dict[str, Any]:
        """
        Genera el paquet complet de 6 combinades (2 Segures, 2 Semi, 2 Arriscades)
        a partir d'un ÚNIC PARTIT (multimerkat Bet Builder).
        """
        markets = self._extract_single_match_markets(match)
        if not markets:
            return {"safe": [], "semi": [], "risky": []}

        by_cat = {}
        for m in markets:
            by_cat.setdefault(m["category"], []).append(m)

        all_cats = list(by_cat.keys())

        def find_combinations(target_min_odd: float, target_max_odd: float, min_legs: int, max_legs: int, min_leg_prob: float = 0.0, allow_exact: bool = True):
            cands = []
            for k in range(min_legs, min(max_legs, len(all_cats)) + 1):
                for cat_combo in itertools.combinations(all_cats, k):
                    if not allow_exact and "Marcador Exacte" in cat_combo:
                        continue
                    if "1X2" in cat_combo and "Doble Oportunitat" in cat_combo:
                        continue
                    if "Marcador Exacte" in cat_combo and ("1X2" in cat_combo or "Gols" in cat_combo):
                        continue

                    cand_lists = [by_cat[c] for c in cat_combo]
                    for choice in itertools.product(*cand_lists):
                        if any(l["model_prob"] < min_leg_prob for l in choice):
                            continue
                        odds = [l["bookie_odd"] for l in choice]
                        c_odd = round(self.calculate_combined_odds(odds), 2)
                        if target_min_odd <= c_odd <= target_max_odd:
                            p_dec = self.calculate_combined_probability([l["model_prob"] for l in choice])
                            cands.append({
                                "legs": list(choice),
                                "c_odd": c_odd,
                                "p_dec": p_dec,
                                "score": p_dec - abs(c_odd - (target_min_odd + target_max_odd) / 2.0) * 0.001,
                                "sig": self._get_combo_sig(choice)
                            })
            cands.sort(key=lambda x: x["score"], reverse=True)
            return cands

        # 1. Segures (1.90 - 3.50)
        safe_cands = find_combinations(1.90, 3.50, min_legs=2, max_legs=3, min_leg_prob=50.0, allow_exact=False)
        if not safe_cands:
            safe_cands = find_combinations(1.70, 4.00, min_legs=2, max_legs=3, min_leg_prob=40.0, allow_exact=False)

        s1_cand = safe_cands[0] if safe_cands else {"legs": []}
        s2_cand = None
        for cand in safe_cands[1:]:
            if cand["sig"] != s1_cand.get("sig") and len(set(cand["sig"]) & set(s1_cand.get("sig", []))) < len(cand["sig"]):
                s2_cand = cand
                break
        if not s2_cand and len(safe_cands) > 1:
            s2_cand = safe_cands[1]
        elif not s2_cand and safe_cands:
            s2_cand = safe_cands[0]

        # 2. Semi-Arriscades (8.50 - 16.50)
        semi_cands = find_combinations(8.50, 16.50, min_legs=3, max_legs=4, min_leg_prob=30.0, allow_exact=False)
        if not semi_cands:
            semi_cands = find_combinations(6.50, 20.00, min_legs=3, max_legs=4, min_leg_prob=20.0, allow_exact=False)

        sm1_cand = semi_cands[0] if semi_cands else {"legs": []}
        sm2_cand = None
        for cand in semi_cands[1:]:
            if cand["sig"] != sm1_cand.get("sig") and len(set(cand["sig"]) & set(sm1_cand.get("sig", []))) < len(cand["sig"]):
                sm2_cand = cand
                break
        if not sm2_cand and len(semi_cands) > 1:
            sm2_cand = semi_cands[1]
        elif not sm2_cand and semi_cands:
            sm2_cand = semi_cands[0]

        # 3. Arriscades (>= 28.0)
        risky_cands = find_combinations(28.0, 75.0, min_legs=3, max_legs=5, min_leg_prob=10.0, allow_exact=True)
        if not risky_cands:
            risky_cands = find_combinations(22.0, 95.0, min_legs=3, max_legs=5, min_leg_prob=5.0, allow_exact=True)

        r1_cand = risky_cands[0] if risky_cands else {"legs": []}
        r2_cand = None
        for cand in risky_cands[1:]:
            if cand["sig"] != r1_cand.get("sig") and len(set(cand["sig"]) & set(r1_cand.get("sig", []))) < len(cand["sig"]):
                r2_cand = cand
                break
        if not r2_cand and len(risky_cands) > 1:
            r2_cand = risky_cands[1]
        elif not r2_cand and risky_cands:
            r2_cand = risky_cands[0]

        safe_1 = self.calculate_combo_summary(s1_cand.get("legs", []), "Combinada del Dia Segura #1 (Cuota 2-3)")
        safe_2 = self.calculate_combo_summary(s2_cand.get("legs", []), "Combinada del Dia Segura #2 (Cuota 2-3 Alternativa)")
        safe_1["category_code"] = "SAFE"
        safe_2["category_code"] = "SAFE"
        safe_1["recommended_stake"] = 25.0
        safe_2["recommended_stake"] = 25.0

        semi_1 = self.calculate_combo_summary(sm1_cand.get("legs", []), "Combinada del Dia Semi-Arriscada #1 (Cuota ~10.0)")
        semi_2 = self.calculate_combo_summary(sm2_cand.get("legs", []), "Combinada del Dia Semi-Arriscada #2 (Cuota ~10.0 Alternativa)")
        semi_1["category_code"] = "SEMI"
        semi_2["category_code"] = "SEMI"
        semi_1["recommended_stake"] = 10.0
        semi_2["recommended_stake"] = 10.0

        risky_1 = self.calculate_combo_summary(r1_cand.get("legs", []), "Combinada del Dia Arriscada #1 (Cuota >= 30.0)")
        risky_2 = self.calculate_combo_summary(r2_cand.get("legs", []), "Combinada del Dia Arriscada #2 (Cuota >= 30.0 Alternativa)")
        risky_1["category_code"] = "RISKY"
        risky_2["category_code"] = "RISKY"
        risky_1["recommended_stake"] = 5.0
        risky_2["recommended_stake"] = 5.0

        return {
            "safe": [safe_1, safe_2],
            "semi": [semi_1, semi_2],
            "risky": [risky_1, risky_2],
            "safe_combo": safe_1,
            "risky_combo": risky_1
        }

    def generate_full_combos_suite(self, matches: List[Dict[str, Any]], is_multi: bool = False) -> Dict[str, Any]:
        """
        Genera exactament el paquet complet sol·licitat per l'usuari:
        - 2 Combinades Segures (cuota 1.95 - 3.50) totalment diferents
        - 2 Combinades Semi-Arriscades (cuota ~8.0 - 15.0) totalment diferents
        - 2 Combinades Arriscades (cuota >= 28.0) totalment diferents
        """
        if len(matches) == 1:
            return self.generate_single_match_combos_suite(matches[0])

        mms = self._extract_all_match_markets(matches)
        if len(mms) == 1:
            return self.generate_single_match_combos_suite(matches[0])

        global_counts = Counter()

        # 1. Combinades Segures
        safe_1, safe_2 = self.find_pair_safe(mms, is_multi=is_multi, global_counts=global_counts, max_usage=2)
        safe_keys = {self._get_leg_key(l) for l in safe_1.get("legs", []) + safe_2.get("legs", [])}

        # 2. Combinades Semi-Arriscades
        semi_1, semi_2 = self.find_pair_semi(mms, is_multi=is_multi, global_counts=global_counts, max_usage=2)

        # 3. Combinades Arriscades (prohibeix seleccions exactes de Safe, manté màxim 2 usos globals)
        risky_1, risky_2 = self.find_pair_risky(
            mms,
            is_multi=is_multi,
            global_counts=global_counts,
            forbidden_exact=safe_keys,
            max_usage=2
        )

        return {
            "safe": [safe_1, safe_2],
            "semi": [semi_1, semi_2],
            "risky": [risky_1, risky_2],
            "safe_combo": safe_1,
            "risky_combo": risky_1
        }

    def generate_few_matches_combos_suite(self, day_matches: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Genera el paquet complet de 6 combinades (2 Segures, 2 Semi, 2 Arriscades)
        per a dies amb 2 o 3 partits, combinant intel·ligentment tots els partits del dia
        a través de mercats de diverses categories (1X2, Doble Oportunitat, Gols, Córners, Targetes).
        """
        for m in day_matches:
            if not m.get("matchup"):
                h = m.get("home_team", {}).get("name") if isinstance(m.get("home_team"), dict) else "Local"
                a = m.get("away_team", {}).get("name") if isinstance(m.get("away_team"), dict) else "Visitant"
                m["matchup"] = f"{h} vs {a}"

        all_buckets = {}
        for m in day_matches:
            mkts = self._extract_single_match_markets(m)
            for l in mkts:
                key = (l["matchup"], l["category"])
                all_buckets.setdefault(key, []).append(l)

        # Ordenar bucket_keys per la màxima probabilitat / valor de les seleccions
        bucket_keys.sort(
            key=lambda b: max((float(l.get("model_prob", 0.0)) for l in all_buckets.get(b, [])), default=0.0),
            reverse=True
        )

        def find_combos(target_min_odd: float, target_max_odd: float, min_legs: int, max_legs: int, min_leg_prob: float = 0.0, allow_exact: bool = False, require_all_matches: bool = False):
            candidates = []
            effective_require_all = require_all_matches and (len(day_matches) <= max_legs)
            n_keys = min(len(bucket_keys), 14)
            for k in range(min_legs, min(max_legs, n_keys) + 1):
                for b_combo in itertools.combinations(bucket_keys[:n_keys], k):
                    matchups = {b[0] for b in b_combo}
                    if effective_require_all and len(matchups) < len(day_matches):
                        continue
                    elif len(day_matches) >= 2 and len(matchups) < 2:
                        continue

                    if not allow_exact and any(b[1] == "Marcador Exacte" for b in b_combo):
                        continue

                    conflicts = False
                    match_cats = {}
                    for m_up, cat in b_combo:
                        match_cats.setdefault(m_up, set()).add(cat)
                    for m_up, cats in match_cats.items():
                        if "1X2" in cats and "Doble Oportunitat" in cats:
                            conflicts = True
                            break
                        if "Marcador Exacte" in cats and ("1X2" in cats or "Gols" in cats):
                            conflicts = True
                            break
                    if conflicts:
                        continue

                    cand_lists = [all_buckets[b][:2] for b in b_combo]
                    for choice in itertools.product(*cand_lists):
                        if any(l["model_prob"] < min_leg_prob for l in choice):
                            continue
                        odds = [l["bookie_odd"] for l in choice]
                        c_odd = round(self.calculate_combined_odds(odds), 2)
                        if target_min_odd <= c_odd <= target_max_odd:
                            p_dec = self.calculate_combined_probability([l["model_prob"] for l in choice])
                            coverage_bonus = (len(matchups) / len(day_matches)) * 0.15
                            score = p_dec * (1.0 + coverage_bonus) - abs(c_odd - (target_min_odd + target_max_odd) / 2.0) * 0.001
                            candidates.append({
                                "legs": list(choice),
                                "c_odd": c_odd,
                                "p_dec": p_dec,
                                "score": score,
                                "matchups": matchups,
                                "sig": self._get_combo_sig(choice)
                            })
                            if len(candidates) >= 100:
                                break
                    if len(candidates) >= 100:
                        break
            candidates.sort(key=lambda x: x["score"], reverse=True)
            return candidates

        # 1. Segures (1.90 - 3.50)
        safe_cands = find_combos(1.90, 3.50, min_legs=2, max_legs=3, min_leg_prob=50.0, require_all_matches=False)
        if not safe_cands:
            safe_cands = find_combos(1.70, 4.00, min_legs=2, max_legs=3, min_leg_prob=40.0)

        s1 = safe_cands[0] if safe_cands else {"legs": []}
        s2 = None
        missing_m = set(m["matchup"] for m in day_matches) - s1.get("matchups", set())
        for c in safe_cands[1:]:
            if c["sig"] != s1.get("sig"):
                if missing_m and (c["matchups"] & missing_m):
                    s2 = c
                    break
        if not s2:
            for c in safe_cands[1:]:
                if c["sig"] != s1.get("sig") and len(set(c["sig"]) & set(s1.get("sig", []))) < len(c["sig"]):
                    s2 = c
                    break
        if not s2 and len(safe_cands) > 1: s2 = safe_cands[1]
        elif not s2 and safe_cands: s2 = safe_cands[0]

        # 2. Semi-Arriscades (8.50 - 16.50)
        semi_cands = find_combos(8.50, 16.50, min_legs=3, max_legs=5, min_leg_prob=30.0, require_all_matches=True)
        if not semi_cands:
            semi_cands = find_combos(6.00, 20.00, min_legs=3, max_legs=5, min_leg_prob=20.0, require_all_matches=True)
        if not semi_cands:
            semi_cands = find_combos(6.00, 20.00, min_legs=2, max_legs=5, min_leg_prob=20.0, require_all_matches=False)

        sm1 = semi_cands[0] if semi_cands else {"legs": []}
        sm2 = None
        for c in semi_cands[1:]:
            if c["sig"] != sm1.get("sig") and len(set(c["sig"]) & set(sm1.get("sig", []))) < len(c["sig"]):
                sm2 = c
                break
        if not sm2 and len(semi_cands) > 1: sm2 = semi_cands[1]
        elif not sm2 and semi_cands: sm2 = semi_cands[0]

        # 3. Arriscades (>= 28.0)
        risky_cands = find_combos(28.0, 75.0, min_legs=3, max_legs=6, min_leg_prob=15.0, allow_exact=True, require_all_matches=True)
        if not risky_cands:
            risky_cands = find_combos(20.0, 95.0, min_legs=3, max_legs=6, min_leg_prob=10.0, allow_exact=True, require_all_matches=True)
        if not risky_cands:
            risky_cands = find_combos(20.0, 95.0, min_legs=3, max_legs=6, min_leg_prob=10.0, allow_exact=True, require_all_matches=False)

        r1 = risky_cands[0] if risky_cands else {"legs": []}
        r2 = None
        for c in risky_cands[1:]:
            if c["sig"] != r1.get("sig") and len(set(c["sig"]) & set(r1.get("sig", []))) < len(c["sig"]):
                r2 = c
                break
        if not r2 and len(risky_cands) > 1: r2 = risky_cands[1]
        elif not r2 and risky_cands: r2 = risky_cands[0]

        prefix = "Mega-Combinada Multi-Lliga" if len(set(m.get("competition_id") for m in day_matches if m.get("competition_id"))) > 1 else "Combinada"

        safe_1 = self.calculate_combo_summary(s1.get("legs", []), f"{prefix} Segura #1 (Cuota 2-3)")
        safe_2 = self.calculate_combo_summary(s2.get("legs", []), f"{prefix} Segura #2 (Cuota 2-3 Alternativa)")
        safe_1["category_code"] = "SAFE"
        safe_2["category_code"] = "SAFE"
        safe_1["recommended_stake"] = 25.0
        safe_2["recommended_stake"] = 25.0

        semi_1 = self.calculate_combo_summary(sm1.get("legs", []), f"{prefix} Semi-Arriscada #1 (Cuota ~10.0)")
        semi_2 = self.calculate_combo_summary(sm2.get("legs", []), f"{prefix} Semi-Arriscada #2 (Cuota ~10.0 Alternativa)")
        semi_1["category_code"] = "SEMI"
        semi_2["category_code"] = "SEMI"
        semi_1["recommended_stake"] = 10.0
        semi_2["recommended_stake"] = 10.0

        risky_1 = self.calculate_combo_summary(r1.get("legs", []), f"{prefix} Arriscada #1 (Cuota >= 30.0)")
        risky_2 = self.calculate_combo_summary(r2.get("legs", []), f"{prefix} Arriscada #2 (Cuota >= 30.0 Alternativa)")
        risky_1["category_code"] = "RISKY"
        risky_2["category_code"] = "RISKY"
        risky_1["recommended_stake"] = 5.0
        risky_2["recommended_stake"] = 5.0

        return {
            "safe": [safe_1, safe_2],
            "semi": [semi_1, semi_2],
            "risky": [risky_1, risky_2],
            "safe_combo": safe_1,
            "risky_combo": risky_1
        }

    def generate_daily_combos_suite(self, day_matches: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Genera la Suite Oficial de 6 Combinades del Dia (2 Segures, 2 Semi, 2 Arriscades):
        - Si no hi ha partits aquell dia: has_matches = False.
        - Si només juga 1 partit: combinades multimerkat d'aquell duel (Bet Builder).
        - Si juguen 2 o 3 partits: combinades multilliga combinant tots els partits del dia.
        - Si juguen 4 o més partits: combinades completes de tota la lliga / multilliga.
        """
        if not day_matches:
            return {
                "has_matches": False,
                "matches_count": 0,
                "league_note": "Sense partits programats per a avui a cap de les 4 lligues analitzades.",
                "safe": [],
                "semi": [],
                "risky": [],
                "safe_combo": {},
                "risky_combo": {}
            }

        comps = {m.get("competition_id") for m in day_matches if m.get("competition_id")}
        comp_map = {
            "LALIGA": "LaLiga EA Sports",
            "PREMIER": "Premier League",
            "HYPERMOTION": "LaLiga Hypermotion",
            "CHAMPIONSHIP": "EFL Championship"
        }
        comp_names = [comp_map.get(c, c) for c in comps]

        if len(day_matches) == 1:
            suite = self.generate_single_match_combos_suite(day_matches[0])
            m_title = day_matches[0].get("matchup") or "Partit Únic"
            suite["has_matches"] = True
            suite["matches_count"] = 1
            suite["league_note"] = f"Partit únic d'avui: {m_title} · Suite de 6 combinades multimerkat del mateix partit"
            return suite

        if len(day_matches) <= 3:
            suite = self.generate_few_matches_combos_suite(day_matches)
        else:
            suite = self.generate_full_combos_suite(day_matches, is_multi=(len(comps) > 1))

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



