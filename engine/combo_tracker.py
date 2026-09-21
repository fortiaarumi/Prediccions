"""
engine/combo_tracker.py
=======================
Motor de verificació i simulador financer 'Què hagués passat si...'.

Lògica financera:
- Cada jornada s'assigna:
  • 25.00 € a la Combinada Segura (Cuota 2-3 | Seleccions >= 90% seguretat)
  • 5.00 € a la Combinada Arriscada / Cuota Alta (Cuota >= 30.0)
- Avalua de forma rigorosa i automàtica si cada selecció del report anterior
  va entrar o fallar consultant els marcadors i estadístiques oficials.
- Calcula el balanç acumulat d'avui, el PnL net (€), el ROI % i la taxa d'encert.
"""

from typing import Dict, List, Any, Optional, Tuple
from database.db_manager import DatabaseManager

class ComboTracker:
    def __init__(self, db: Optional[DatabaseManager] = None):
        self.db = db if db else DatabaseManager()

    def evaluate_leg(self, leg: Dict[str, Any], match: Dict[str, Any]) -> Tuple[str, str]:
        """
        Avalua una selecció concreta a partir de les estadístiques del partit.
        Retorna (status: 'WON'|'LOST'|'PENDING', actual_result: str)
        """
        if not match or match.get("status") != "FINISHED" or match.get("home_goals") is None:
            return "PENDING", "Partit pendent de disputar"

        h_goals = int(match["home_goals"])
        a_goals = int(match["away_goals"])
        tot_goals = h_goals + a_goals

        h_cards = int(match.get("home_yellow_cards") or 0) + int(match.get("home_red_cards") or 0)
        a_cards = int(match.get("away_yellow_cards") or 0) + int(match.get("away_red_cards") or 0)
        tot_cards = h_cards + a_cards

        h_corn = int(match.get("home_corners") or 0)
        a_corn = int(match.get("away_corners") or 0)
        tot_corn = h_corn + a_corn

        sel_name = leg.get("selection_name", "").lower()
        cat = (leg.get("category") or "").lower()
        matchup = leg.get("matchup", "")
        parts = matchup.split(" vs ", 1)
        h_name = parts[0].strip().lower() if len(parts) == 2 else ""
        a_name = parts[1].strip().lower() if len(parts) == 2 else ""

        # 1. Doble Oportunitat
        if "1x" in sel_name or ("o empat" in sel_name and (h_name in sel_name or "local" in sel_name)):
            won = h_goals >= a_goals
            return ("WON" if won else "LOST"), f"{h_goals}-{a_goals} ({'1X encertat' if won else 'victòria visitant'})"
        elif "x2" in sel_name or ("o empat" in sel_name and (a_name in sel_name or "visitant" in sel_name)):
            won = a_goals >= h_goals
            return ("WON" if won else "LOST"), f"{h_goals}-{a_goals} ({'X2 encertat' if won else 'victòria local'})"
        elif "12" in sel_name or "sense empat" in sel_name:
            won = h_goals != a_goals
            return ("WON" if won else "LOST"), f"{h_goals}-{a_goals} ({'12 encertat' if won else 'empat'})"

        # 2. 1X2 Principal
        elif sel_name.startswith("1 -") or "victòria local" in sel_name or (f"victòria {h_name}" in sel_name):
            won = h_goals > a_goals
            return ("WON" if won else "LOST"), f"{h_goals}-{a_goals} ({'1 encertat' if won else 'no victòria local'})"
        elif sel_name.startswith("2 -") or "victòria visitant" in sel_name or (f"victòria {a_name}" in sel_name):
            won = a_goals > h_goals
            return ("WON" if won else "LOST"), f"{h_goals}-{a_goals} ({'2 encertat' if won else 'no victòria visitant'})"
        elif sel_name.startswith("x -") or "empat" in sel_name:
            won = h_goals == a_goals
            return ("WON" if won else "LOST"), f"{h_goals}-{a_goals} ({'Empat encertat' if won else 'sense empat'})"

        # 3. Gols (Més / Menys d'1.5, 2.5, 3.5 gols i equip marca)
        elif "més d'1.5" in sel_name or "més de 1.5" in sel_name or "over 1.5" in sel_name or "+1.5 gols" in sel_name:
            won = tot_goals >= 2
            return ("WON" if won else "LOST"), f"{tot_goals} gols ({'Over 1.5 encertat' if won else 'Menys de 2 gols'})"
        elif "menys d'1.5" in sel_name or "menys de 1.5" in sel_name or "under 1.5" in sel_name or "-1.5 gols" in sel_name:
            won = tot_goals <= 1
            return ("WON" if won else "LOST"), f"{tot_goals} gols ({'Under 1.5 encertat' if won else 'Més d\'1 gol'})"
        elif "més de 2.5" in sel_name or "over 2.5" in sel_name or "+2.5" in sel_name:
            won = tot_goals >= 3
            return ("WON" if won else "LOST"), f"{tot_goals} gols ({'Over 2.5 encertat' if won else 'Under 2.5'})"
        elif "menys de 2.5" in sel_name or "under 2.5" in sel_name or "-2.5" in sel_name:
            won = tot_goals <= 2
            return ("WON" if won else "LOST"), f"{tot_goals} gols ({'Under 2.5 encertat' if won else 'Over 2.5'})"
        elif "menys de 3.5" in sel_name or "under 3.5" in sel_name or "-3.5" in sel_name:
            won = tot_goals <= 3
            return ("WON" if won else "LOST"), f"{tot_goals} gols ({'Under 3.5 encertat' if won else 'Over 3.5'})"
        elif "més de 3.5" in sel_name or "over 3.5" in sel_name or "+3.5" in sel_name:
            won = tot_goals >= 4
            return ("WON" if won else "LOST"), f"{tot_goals} gols ({'Over 3.5 encertat' if won else 'Under 3.5'})"
        elif "marca (+0.5" in sel_name or "marca (+0.5 gols)" in sel_name:
            if (h_name and h_name in sel_name) or "local" in sel_name:
                won = h_goals >= 1
                return ("WON" if won else "LOST"), f"{h_goals} gols locals ({'Local ha marcat' if won else 'Local no ha marcat'})"
            elif (a_name and a_name in sel_name) or "visitant" in sel_name:
                won = a_goals >= 1
                return ("WON" if won else "LOST"), f"{a_goals} gols visitants ({'Visitant ha marcat' if won else 'Visitant no ha marcat'})"
            else:
                won = tot_goals >= 1
                return ("WON" if won else "LOST"), f"{tot_goals} gols"

        # 4. Ambdós Marquen (BTTS)
        elif "ambdós marquen: sí" in sel_name or "ambos equipos marcan: si" in sel_name or "btts sí" in sel_name or "btts yes" in sel_name:
            won = h_goals > 0 and a_goals > 0
            return ("WON" if won else "LOST"), f"{h_goals}-{a_goals} ({'BTTS Sí encertat' if won else 'no han marcat ambdós'})"
        elif "ambdós marquen: no" in sel_name or "ambos equipos marcan: no" in sel_name or "btts no" in sel_name:
            won = h_goals == 0 or a_goals == 0
            return ("WON" if won else "LOST"), f"{h_goals}-{a_goals} ({'BTTS No encertat' if won else 'han marcat ambdós'})"

        # 5. Targetes
        elif "targetes" in sel_name or "tarjetas" in sel_name or "cards" in sel_name:
            if "4.5" in sel_name:
                won = tot_cards >= 5
                return ("WON" if won else "LOST"), f"{tot_cards} targetes"
            elif "5.5" in sel_name:
                won = tot_cards >= 6
                return ("WON" if won else "LOST"), f"{tot_cards} targetes"

        # 6. Córners
        elif "córner" in sel_name or "corner" in sel_name:
            if "8.5" in sel_name:
                won = tot_corn >= 9
                return ("WON" if won else "LOST"), f"{tot_corn} córners"
            elif "9.5" in sel_name:
                won = tot_corn >= 10
                return ("WON" if won else "LOST"), f"{tot_corn} córners"
            elif "10.5" in sel_name:
                won = tot_corn >= 11
                return ("WON" if won else "LOST"), f"{tot_corn} córners"

        # Per defecte si no es reconeix
        return "PENDING", f"{h_goals}-{a_goals}"

    def evaluate_all_pending_combos(self) -> List[Dict[str, Any]]:
        """
        Revisa totes les combinades en estat 'PENDING' a la base de dades.
        Si tots els partits implicats han acabat, determina si ha guanyat o perdut
        i actualitza el seu balanç i estat.
        """
        pending = self.db.get_pending_combos()
        evaluated = []

        with self.db.get_connection() as conn:
            cursor = conn.cursor()

            for combo in pending:
                legs = combo.get("legs", [])
                if not legs:
                    continue

                all_legs_finished = True
                any_leg_lost = False
                evaluated_legs = []

                for leg in legs:
                    h_id = leg.get("home_team_id")
                    a_id = leg.get("away_team_id")
                    match_row = None

                    if h_id and a_id:
                        cursor.execute("""
                            SELECT * FROM matches
                            WHERE home_team_id = ? AND away_team_id = ?
                            ORDER BY date_time DESC LIMIT 1
                        """, (h_id, a_id))
                        row = cursor.fetchone()
                        if row:
                            match_row = dict(row)

                    if not match_row or match_row.get("status") != "FINISHED":
                        all_legs_finished = False
                        leg_status = "PENDING"
                        actual_res = "Pendent de disputar"
                    else:
                        leg_status, actual_res = self.evaluate_leg(leg, match_row)

                    if leg_status == "LOST":
                        any_leg_lost = True
                    elif leg_status == "PENDING":
                        all_legs_finished = False

                    leg_eval = dict(leg)
                    leg_eval["status"] = leg_status
                    leg_eval["actual_result"] = actual_res
                    evaluated_legs.append(leg_eval)

                # Si tots els partits han finalitzat:
                if all_legs_finished:
                    if any_leg_lost:
                        final_status = "LOST"
                        payout = 0.0
                        profit = -float(combo["stake"])
                    else:
                        final_status = "WON"
                        odd = float(combo["combined_odd"])
                        stake = float(combo["stake"])
                        payout = round(stake * odd, 2)
                        profit = round(payout - stake, 2)

                    self.db.update_combo_evaluation(
                        combo_id=combo["id"],
                        status=final_status,
                        payout=payout,
                        profit=profit,
                        legs_evaluation=evaluated_legs
                    )

                    combo_res = dict(combo)
                    combo_res["status"] = final_status
                    combo_res["payout"] = payout
                    combo_res["profit"] = profit
                    combo_res["legs"] = evaluated_legs
                    evaluated.append(combo_res)

        return evaluated

    def get_simulation_summary(self, competition_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Retorna el resum financer complet 'Què hagués passat si...':
        - Inversió acumulada (25€ segura + 5€ arriscada per jornada)
        - Retorn brut i Benefici net (€)
        - ROI global (%)
        - Detall de la darrera jornada avaluada
        """
        # Primer pas: avalua qualsevol aposta que ja hagi finalitzat
        self.evaluate_all_pending_combos()

        # Obtenir el balanç històric de la base de dades
        history = self.db.get_bankroll_history(competition_id=competition_id)
        last_evaluated = self.db.get_last_round_evaluated_combos(competition_id=competition_id)

        return {
            "total_stake": history["total_stake"],
            "total_payout": history["total_payout"],
            "net_profit": history["net_profit"],
            "roi_pct": history["roi_pct"],
            "safe": history["safe"],
            "semi": history.get("semi", {"total": 0, "won": 0, "hit_rate_pct": 0.0}),
            "risky": history["risky"],
            "evaluated_combos": history["evaluated_combos"],
            "last_round_combos": last_evaluated,
            "all_combos": history["combos"]
        }
