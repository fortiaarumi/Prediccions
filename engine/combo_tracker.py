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


def parse_bool_flag(val: Any) -> Optional[bool]:
    """
    Normalitza de manera segura valors booleans que poden arribar com a:
    bool (True, False), int (1, 0), o string ("true", "false", "1", "0", etc.).
    Evita que bool("false") o bool("0") s'avaluïn erròniament com a True.
    """
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val != 0
    if isinstance(val, str):
        s = val.strip().lower()
        if s in ("true", "1", "yes", "si", "sí", "y"):
            return True
        if s in ("false", "0", "no", "n", ""):
            return False
    return None


def is_unambiguous_void_note(note: str) -> bool:
    """
    Verifica si una nota textual indica de manera inequívoca i confirmada que el partit és VOID.
    Rebutja immediatament negacions, incerteses o resolucions pendents
    (ex: 'void not confirmed', 'not void', 'no anul·lat', 'anul·lació pendent', etc.).
    """
    if not note:
        return False
    n = note.strip().lower()
    # Paraules de negació o incertesa que invaliden automàticament la declaració de void:
    negations = [
        "not void", "no void", "void not", "not confirmed", "no confirmat", "no confirmada",
        "no anul·lat", "no cancel·lat", "sense anul·lar", "pendent", "pending", "unconfirmed",
        "possible", "provisional", "reclamació", "descartat", "descartada", "espera"
    ]
    if any(neg in n for neg in negations):
        return False

    confirmed_void_patterns = [
        "anul·lat definitivament", "anul·lació confirmada", "anul·lat oficialment",
        "partit anul·lat", "partit cancel·lat", "cancel·lat definitivament",
        "declarat void", "declarat nul", "void confirmat", "officially cancelled",
        "confirmed void", "partit suspès definitivament"
    ]
    return any(p in n for p in confirmed_void_patterns)


class ComboTracker:
    def __init__(self, db: Optional[DatabaseManager] = None):
        self.db = db if db else DatabaseManager()

    def evaluate_leg(self, leg: Dict[str, Any], match: Optional[Dict[str, Any]]) -> Tuple[str, str]:
        """
        Avalua una selecció concreta a partir de les dades del partit.
        Distingeix rigorosament els estats estructurats i normalitzats:
        - Definitivament anul·lat / declarat void (CANCELLED, VOID o flags explícits): ('VOID', motiu)
        - Abandonat (ABANDONED): només void si hi ha resolució confirmada; altrament PENDING.
        - Temporalment suspès (SUSPENDED): ('PENDING', motiu)
        - Ajornat o reprogramat (POSTPONED, RESCHEDULED): ('PENDING', motiu)
        - Pendent de disputar o en joc (SCHEDULED, LIVE): ('PENDING', motiu)
        - Finalitzat amb resultat oficial (FINISHED amb marcadors): ('WON'|'LOST', motiu)
        - Estats desconeguts o ambigus: sempre PENDING.
        """
        if not match:
            return "PENDING", "Partit pendent de disputar"

        m_status = str(match.get("status", "")).strip().upper()
        status_note = str(match.get("status_note", "")).strip()

        is_void_flag = parse_bool_flag(match.get("is_void"))
        is_cancelled_flag = parse_bool_flag(match.get("cancelled"))
        has_void_note = is_unambiguous_void_note(status_note)

        # 1. Partit finalitzat amb marcadors oficials (FINISHED)
        if m_status == "FINISHED":
            if match.get("home_goals") is None or match.get("away_goals") is None:
                return "PENDING", "Partit finalitzat a l'espera de marcador oficial"
            # Si hi ha discrepància flag void/cancelled vs FINISHED, no liquidar automàticament; deixar pendent
            if is_void_flag is True or is_cancelled_flag is True:
                return "PENDING", "Discrepància detectada: partit FINISHED amb flag de void/cancel·lació (pendent de resolució)"
            # Les notes textuals no verificades no prevalen sobre el resultat oficial estructurat.

        # 2. Partit definitivament anul·lat o declarat void per estat oficial
        elif m_status in ("CANCELLED", "CANCELED", "VOID"):
            return "VOID", "Partit cancel·lat / declarat void (quota 1.00)"

        # 3. Partit abandonat (ABANDONED): només void si hi ha resolució confirmada; altrament PENDING
        elif m_status == "ABANDONED":
            if is_void_flag is True or has_void_note:
                return "VOID", "Partit abandonat i declarat void oficialment (quota 1.00)"
            return "PENDING", "Partit abandonat (a l'espera de resolució oficial)"

        # 4. Declaració explícita de void mitjançant flag sobre partit no finalitzat
        elif is_void_flag is True or is_cancelled_flag is True:
            return "VOID", "Partit declarat void explícitament (quota 1.00)"

        # 5. Partit ajornat o reprogramat (no liquidar prematurament)
        elif m_status in ("POSTPONED", "RESCHEDULED", "AJORNAT", "REPROGRAMAT"):
            return "PENDING", "Partit ajornat / reprogramat (a l'espera de nova data)"

        # 6. Partit temporalment suspès (no void automàtic, continua pendent de resolució)
        elif m_status == "SUSPENDED":
            return "PENDING", "Partit suspès (a l'espera de resolució oficial)"

        # 7. Partit pendent o en joc
        elif m_status in ("SCHEDULED", "LIVE", "IN_PLAY", "FIRST_HALF", "SECOND_HALF", "HALFTIME"):
            if m_status == "LIVE":
                lh = match.get("home_goals", 0)
                la = match.get("away_goals", 0)
                return "PENDING", f"En joc ({lh}-{la})"
            return "PENDING", "Partit pendent de disputar"

        # 8. Estat desconegut o no reconegut
        else:
            return "PENDING", f"Partit pendent (estat desconegut: {m_status or 'sense_estat'})"

        h_goals = int(match["home_goals"])
        a_goals = int(match["away_goals"])
        tot_goals = h_goals + a_goals

        h_cards = int(match.get("home_yellow_cards") or 0) + int(match.get("home_red_cards") or 0)
        a_cards = int(match.get("away_yellow_cards") or 0) + int(match.get("away_red_cards") or 0)
        tot_cards = h_cards + a_cards

        h_corn = int(match.get("home_corners") or 0)
        a_corn = int(match.get("away_corners") or 0)
        tot_corn = h_corn + a_corn

        sel_name = (leg.get("selection_name") or leg.get("name") or "").lower()
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

        # 5. Targetes (Tant Més/Over com Menys/Under i qualsevol línia)
        elif "targetes" in sel_name or "tarjetas" in sel_name or "cards" in sel_name:
            import re
            is_under = any(w in sel_name for w in ["menys", "menos", "under", "-"])
            m_cards = re.search(r'(\d+(?:\.\d+)?)', sel_name)
            if m_cards:
                line_val = float(m_cards.group(1))
                if is_under:
                    won = tot_cards < line_val
                    return ("WON" if won else "LOST"), f"{tot_cards} targetes ({'Menys de ' + str(line_val) + ' encertat' if won else 'Més de ' + str(line_val)})"
                else:
                    won = tot_cards > line_val
                    return ("WON" if won else "LOST"), f"{tot_cards} targetes ({'Més de ' + str(line_val) + ' encertat' if won else 'Menys de ' + str(line_val)})"
            won = tot_cards >= 5
            return ("WON" if won else "LOST"), f"{tot_cards} targetes"

        # 6. Córners (Tant Més/Over com Menys/Under i qualsevol línia)
        elif "córner" in sel_name or "corner" in sel_name or "corners" in sel_name:
            import re
            is_under = any(w in sel_name for w in ["menys", "menos", "under", "-"])
            m_corn = re.search(r'(\d+(?:\.\d+)?)', sel_name)
            if m_corn:
                line_val = float(m_corn.group(1))
                if is_under:
                    won = tot_corn < line_val
                    return ("WON" if won else "LOST"), f"{tot_corn} córners ({'Menys de ' + str(line_val) + ' encertat' if won else 'Més de ' + str(line_val)})"
                else:
                    won = tot_corn > line_val
                    return ("WON" if won else "LOST"), f"{tot_corn} córners ({'Més de ' + str(line_val) + ' encertat' if won else 'Menys de ' + str(line_val)})"
            won = tot_corn >= 10
            return ("WON" if won else "LOST"), f"{tot_corn} córners"

        # 7. Marcador Exacte (ex: "Marcador Exacte: 2-1" o "Resultat Exacte 1-0")
        elif "marcador exacte" in sel_name or "resultat exacte" in sel_name or "resultado exacto" in sel_name:
            import re
            m_sc = re.search(r'(\d+)\s*[-:]\s*(\d+)', sel_name)
            if m_sc:
                exp_h = int(m_sc.group(1))
                exp_a = int(m_sc.group(2))
                won = (h_goals == exp_h and a_goals == exp_a)
                return ("WON" if won else "LOST"), f"{h_goals}-{a_goals} ({'Marcador exacte encertat!' if won else f'Esperat {exp_h}-{exp_a}'})"

        # Per defecte si no es reconeix
        return "PENDING", f"{h_goals}-{a_goals}"

    def evaluate_combo_settlement(self, combo: Dict[str, Any], matches_by_matchup: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """
        Avalua de forma pura en memòria (sense dependències de DB) l'estat i liquidació d'una combinada:
        - Si alguna selecció és 'LOST': combinada 'LOST' (payout = 0.0, profit = -stake).
        - Si hi ha seleccions 'PENDING' (i cap 'LOST'): combinada 'PENDING'.
        - Si totes les seleccions són 'VOID': combinada 'VOID' (retorn d'stake, profit = 0.0).
        - Si hi ha seleccions 'WON' i 'VOID': combinada 'WON', tractant les void com a quota 1.00.
        """
        legs = combo.get("legs", [])
        evaluated_legs = []
        stake = float(combo.get("stake", 10.0))

        for leg in legs:
            m_key = leg.get("matchup", "")
            match = matches_by_matchup.get(m_key)
            if not match and leg.get("home_team_id") and leg.get("away_team_id"):
                match = matches_by_matchup.get(f"{leg['home_team_id']}::{leg['away_team_id']}")

            leg_status, actual_res = self.evaluate_leg(leg, match)
            leg_eval = dict(leg)
            leg_eval["status"] = leg_status
            leg_eval["actual_result"] = actual_res
            evaluated_legs.append(leg_eval)

        has_lost = any(l["status"] == "LOST" for l in evaluated_legs)
        has_pending = any(l["status"] == "PENDING" for l in evaluated_legs)
        all_void = len(evaluated_legs) > 0 and all(l["status"] == "VOID" for l in evaluated_legs)
        all_won_or_void = len(evaluated_legs) > 0 and all(l["status"] in ("WON", "VOID") for l in evaluated_legs) and any(l["status"] == "WON" for l in evaluated_legs)

        if has_lost:
            final_status = "LOST"
            effective_odd = 0.0
            payout = 0.0
            profit = -round(stake, 2)
        elif has_pending:
            final_status = "PENDING"
            effective_odd = float(combo.get("combined_odd", 1.0))
            payout = 0.0
            profit = 0.0
        elif all_void:
            final_status = "VOID"
            effective_odd = 1.0
            payout = round(stake, 2)
            profit = 0.0
        elif all_won_or_void:
            final_status = "WON"
            raw_effective_odd = 1.0
            for l in evaluated_legs:
                if l["status"] == "WON":
                    raw_effective_odd *= float(l.get("bookie_odd", 1.0))
                elif l["status"] == "VOID":
                    raw_effective_odd *= 1.0
            payout = round(stake * raw_effective_odd, 2)
            profit = round(payout - stake, 2)
            effective_odd = round(raw_effective_odd, 4)
        else:
            # Estats desconeguts o incongruents mai es tracten implícitament com a WON
            final_status = "PENDING"
            effective_odd = float(combo.get("combined_odd", 1.0))
            payout = 0.0
            profit = 0.0

        return {
            "id": combo.get("id"),
            "status": final_status,
            "stake": stake,
            "effective_odd": effective_odd,
            "payout": payout,
            "profit": profit,
            "legs": evaluated_legs
        }

    def evaluate_all_pending_combos(self) -> List[Dict[str, Any]]:
        """
        Revisa totes les combinades en estat 'PENDING' a la base de dades.
        Avalua de manera segura i idempotent:
        - Si alguna selecció és 'LOST': la combinada és 'LOST' (payout = 0.0, profit = -stake).
        - Si no hi ha cap selecció 'LOST' i hi ha seleccions 'PENDING': continua 'PENDING'.
        - Si totes les seleccions han finalitzat sense cap 'LOST':
          • Si totes són 'VOID': estat 'VOID', retorn de l'stake (payout = stake, profit = 0.0).
          • Si hi ha seleccions 'WON' (i potser 'VOID' com a 1.00): estat 'WON', payout = stake * odd_efectiva.
        """
        pending = self.db.get_pending_combos()
        evaluated = []

        with self.db.get_connection() as conn:
            cursor = conn.cursor()

            for combo in pending:
                legs = combo.get("legs", [])
                if not legs:
                    continue

                evaluated_legs = []

                for leg in legs:
                    h_id = leg.get("home_team_id")
                    a_id = leg.get("away_team_id")
                    if not h_id or not a_id:
                        m_str = leg.get("matchup", "")
                        if " vs " in m_str:
                            p_parts = m_str.split(" vs ", 1)
                            h_id = h_id or self.db.find_team_id(p_parts[0].strip())
                            a_id = a_id or self.db.find_team_id(p_parts[1].strip())

                    match_row = None
                    if h_id and a_id:
                        cursor.execute("""
                            SELECT * FROM matches
                            WHERE home_team_id = ? AND away_team_id = ?
                            ORDER BY CASE WHEN status = 'FINISHED' THEN 1 ELSE 2 END, date_time DESC LIMIT 1
                        """, (h_id, a_id))
                        row = cursor.fetchone()
                        if row:
                            match_row = dict(row)

                    leg_status, actual_res = self.evaluate_leg(leg, match_row)

                    # Actualitzar la selecció a SQLite immediatament si no és PENDING i ha canviat
                    if leg.get("id") and leg_status != "PENDING" and (leg_status != leg.get("status") or actual_res != leg.get("actual_result")):
                        cursor.execute("""
                            UPDATE combo_legs
                            SET status = ?, actual_result = ?, evaluated_at = datetime('now')
                            WHERE id = ?
                        """, (leg_status, actual_res, leg["id"]))
                        conn.commit()

                    leg_eval = dict(leg)
                    leg_eval["status"] = leg_status
                    leg_eval["actual_result"] = actual_res
                    evaluated_legs.append(leg_eval)

                has_lost = any(l["status"] == "LOST" for l in evaluated_legs)
                has_pending = any(l["status"] == "PENDING" for l in evaluated_legs)
                all_void = len(evaluated_legs) > 0 and all(l["status"] == "VOID" for l in evaluated_legs)
                all_won_or_void = len(evaluated_legs) > 0 and all(l["status"] in ("WON", "VOID") for l in evaluated_legs) and any(l["status"] == "WON" for l in evaluated_legs)

                stake = float(combo.get("stake", 10.0))

                if has_lost:
                    final_status = "LOST"
                    payout = 0.0
                    profit = -round(stake, 2)
                elif has_pending:
                    final_status = "PENDING"
                    payout = 0.0
                    profit = 0.0
                elif all_void:
                    # Totes les seleccions void: devolució íntegra de l'stake, PnL net 0.0
                    final_status = "VOID"
                    payout = round(stake, 2)
                    profit = 0.0
                elif all_won_or_void:
                    # Guanyadora amb quotes efectives (seleccions void tractades com a 1.00)
                    final_status = "WON"
                    raw_effective_odd = 1.0
                    for l in evaluated_legs:
                        if l["status"] == "WON":
                            raw_effective_odd *= float(l.get("bookie_odd", 1.0))
                        elif l["status"] == "VOID":
                            raw_effective_odd *= 1.0
                    payout = round(stake * raw_effective_odd, 2)
                    profit = round(payout - stake, 2)
                else:
                    final_status = "PENDING"
                    payout = 0.0
                    profit = 0.0

                if final_status in ["WON", "LOST", "VOID"]:
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
