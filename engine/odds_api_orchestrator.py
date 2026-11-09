"""
engine/odds_api_orchestrator.py
===============================
Orquestrador intel·ligent i adaptatiu per a The Odds API v4.

Requisits clau implementats:
1. Optimització de calendari: Consulta l'endpoint gratuït /events (0 crèdits) abans de gastar.
   - Agrupa en finestres de 24h, 48h i 72h en UTC (mostrat en Europe/Madrid).
   - Descarta partits passats/en joc (només prematch).
   - Si no hi ha partits en 72h: 0 crèdits gastats.
   - Si només hi ha partits de N lligues: consulta només aquelles N lligues.
2. Pressupost gratuït adaptatiu:
   - 500 crèdits/període (450 operatius, 50 reserva intocable).
   - Reconciliació amb capçaleres x-requests-remaining, x-requests-used, x-requests-last.
   - Bloqueig si el cost estimat supera el pressupost disponible.
3. Mercats:
   - 1X2 en bloc per lliga activa (1 crèdit per lliga).
   - Doble Oportunitat selectiva (només partits candidats amb alta dispersió o near-arb 1X2).
   - Transparència de cobertura (informa exactament quins partits i mercats s'han consultat).
4. Jurisdicció i protecció DGOJ:
   - Diferencia operadors espanyols (DGOJ) d'internacionals (teòrics).
   - Impedeix barrejar orígens incompatibles.
5. Reutilització del motor matemàtic:
   - Empra ArbitrageEngine per a càlculs exhaustius (1X2, 1X vs 2, X2 vs 1, 12 vs X, DC+DC).
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple, Set

from scraper.odds_api_client import (
    TheOddsApiClient,
    CANONICAL_LEAGUES,
    SPORT_KEY_TO_COMPETITION,
    TheOddsApiError,
    TheOddsApiQuotaExhaustedError
)
from engine.arbitrage_engine import ArbitrageEngine

# Operadors DGOJ reconeguts a Espanya o amb filial regulada
SPANISH_DGOJ_BOOKMAKERS: Set[str] = {
    "winamax",
    "bet365",
    "betway",
    "betfair",
    "williamhill",
    "codere",
    "sportium",
    "kirolbet",
    "retabet",
    "bwin",
    "pokerstars",
    "marcaapuestas"
}

# Límits del pla gratuït
MONTHLY_MAX_ALLOWANCE = 500
OPERATIONAL_CREDITS_LIMIT = 450
SAFETY_RESERVE_CREDITS = 50


class CalendarMatch:
    def __init__(
        self,
        event_id: str,
        sport_key: str,
        competition_id: str,
        home_team: str,
        away_team: str,
        commence_time_utc: datetime,
    ):
        self.event_id = event_id
        self.sport_key = sport_key
        self.competition_id = competition_id
        self.home_team = home_team
        self.away_team = away_team
        self.commence_time_utc = commence_time_utc

    @property
    def matchup(self) -> str:
        return f"{self.home_team} vs {self.away_team}"

    def hours_until(self, now_utc: datetime) -> float:
        return (self.commence_time_utc - now_utc).total_seconds() / 3600.0


class CalendarInspection:
    def __init__(self, now_utc: datetime):
        self.now_utc = now_utc
        self.all_matches: List[CalendarMatch] = []
        self.matches_within_24h: List[CalendarMatch] = []
        self.matches_within_48h: List[CalendarMatch] = []
        self.matches_within_72h: List[CalendarMatch] = []
        self.active_leagues: Dict[str, str] = {}  # {COMPETITION_ID: sport_key}
        self.empty_leagues: List[str] = []

    @property
    def total_upcoming_72h(self) -> int:
        return len(self.matches_within_72h)


class OddsApiOrchestrator:
    def __init__(
        self,
        client: Optional[TheOddsApiClient] = None,
        arbitrage_engine: Optional[ArbitrageEngine] = None,
        operational_limit: int = OPERATIONAL_CREDITS_LIMIT,
        safety_reserve: int = SAFETY_RESERVE_CREDITS,
        max_dc_candidates_per_run: int = 5,
        default_regions: str = "eu",
    ):
        self.client = client or TheOddsApiClient()
        self.arbitrage_engine = arbitrage_engine or ArbitrageEngine(
            min_profit_threshold=0.0,
            near_arb_threshold=-2.5,
            max_profit_threshold=15.0
        )
        self.operational_limit = operational_limit
        self.safety_reserve = safety_reserve
        self.max_dc_candidates = max_dc_candidates_per_run
        self.default_regions = default_regions

        # Estat local de consum si no s'utilitza base de dades
        self.local_credits_consumed = 0

    # =========================================================================
    # 1. INSPECCIÓ DE CALENDARI (0 CRÈDITS)
    # =========================================================================

    def inspect_calendar(
        self,
        now_utc: Optional[datetime] = None,
        leagues: Optional[Dict[str, str]] = None
    ) -> CalendarInspection:
        """
        Consulta /events per a les competicions indicades (0 crèdits).
        Classifica els partits en finestres temporals i determina quines lligues tenen activitat.
        """
        now = now_utc or datetime.now(timezone.utc)
        target_leagues = leagues or CANONICAL_LEAGUES
        inspection = CalendarInspection(now_utc=now)

        for comp_id, sport_key in target_leagues.items():
            try:
                events = self.client.get_events(sport_key)
            except Exception as e:
                # Si una lliga falla puntualment, no avortar la resta
                events = []

            league_matches: List[CalendarMatch] = []
            for ev in events:
                commence_raw = ev.get("commence_time")
                if not commence_raw:
                    continue
                try:
                    # Parseig ISO UTC
                    dt = datetime.fromisoformat(commence_raw.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    continue

                # Descartar partits ja començats o passats (només prematch)
                hours_left = (dt - now).total_seconds() / 3600.0
                if hours_left <= 0:
                    continue

                # Només ens interessen partits fins a 72 hores
                if hours_left > 72.0:
                    continue

                cm = CalendarMatch(
                    event_id=ev.get("id", ""),
                    sport_key=sport_key,
                    competition_id=comp_id,
                    home_team=ev.get("home_team", ""),
                    away_team=ev.get("away_team", ""),
                    commence_time_utc=dt
                )
                league_matches.append(cm)
                inspection.matches_within_72h.append(cm)
                if hours_left <= 48.0:
                    inspection.matches_within_48h.append(cm)
                if hours_left <= 24.0:
                    inspection.matches_within_24h.append(cm)

            if league_matches:
                inspection.active_leagues[comp_id] = sport_key
            else:
                inspection.empty_leagues.append(comp_id)

        return inspection

    # =========================================================================
    # 2. GESTIÓ ADAPTATIVA DEL PRESSUPOST
    # =========================================================================

    def get_remaining_operational_budget(self) -> int:
        """Calcula els crèdits operatius restants respectant la reserva de seguretat."""
        if self.client.requests_remaining is not None:
            # Reconciliació directa amb la capçalera oficial del proveïdor
            rem = self.client.requests_remaining
            return max(0, rem - self.safety_reserve)
        return max(0, self.operational_limit - self.local_credits_consumed)

    def estimate_scan_cost(self, num_active_leagues: int, num_dc_events: int = 0) -> int:
        """Estima el consum en crèdits per a una cerca planificada."""
        # 1X2: 1 crèdit per lliga en regió eu
        cost_1x2 = num_active_leagues * 1
        # DC: 1 crèdit per partit consultat individualment
        cost_dc = num_dc_events * 1
        return cost_1x2 + cost_dc

    def can_execute_scan(self, estimated_cost: int) -> Tuple[bool, str]:
        """Comprova si el pressupost restant permet executar la cerca."""
        avail = self.get_remaining_operational_budget()
        if avail < estimated_cost:
            return False, (
                f"Pressupost operatiu insuficient: calen {estimated_cost} crèdits, "
                f"però només queden {avail} operatius (reserva protegida de {self.safety_reserve} crèdits)."
            )
        return True, "Pressupost autoritzat."

    # =========================================================================
    # 3. EXTRACCIÓ I PRIORITZACIÓ DE CANDIDATS PER A DOBLE OPORTUNITAT
    # =========================================================================

    def _prioritize_dc_candidates(
        self,
        matches_odds_1x2: List[Dict[str, Any]],
        max_candidates: int
    ) -> List[Dict[str, Any]]:
        """
        Analitza les quotes 1X2 per triar els partits amb més probabilitat d'arbitratge
        amb Doble Oportunitat:
        - Marge 1X2 proper a 1.0 (near-arbs).
        - Alta dispersió de quotes entre cases.
        - Partits més propers al kickoff.
        """
        candidates = []
        for m in matches_odds_1x2:
            # Calcular millors quotes 1X2
            best_1 = 0.0
            best_x = 0.0
            best_2 = 0.0
            bookies_count = len(m.get("bookmakers", []))
            if bookies_count < 2:
                continue

            for bk in m.get("bookmakers", []):
                for mkt in bk.get("markets", []):
                    if mkt.get("key") == "h2h":
                        for out in mkt.get("outcomes", []):
                            price = float(out.get("price") or 0.0)
                            name = out.get("name")
                            if name == m.get("home_team"):
                                best_1 = max(best_1, price)
                            elif name == m.get("away_team"):
                                best_2 = max(best_2, price)
                            elif name in ("Draw", "Empat", "X"):
                                best_x = max(best_x, price)

            if best_1 > 1.0 and best_x > 1.0 and best_2 > 1.0:
                margin = (1.0 / best_1) + (1.0 / best_x) + (1.0 / best_2)
                # Com més baix el marge (més proper a 1.0 o inferior), millor candidat
                candidates.append({
                    "event_id": m.get("id"),
                    "sport_key": m.get("sport_key"),
                    "home_team": m.get("home_team"),
                    "away_team": m.get("away_team"),
                    "commence_time": m.get("commence_time"),
                    "margin": margin,
                    "bookies_count": bookies_count,
                    "best_1": best_1,
                    "best_x": best_x,
                    "best_2": best_2,
                })

        # Ordenar candidats: menor marge primer
        candidates.sort(key=lambda c: c["margin"])
        return candidates[:max_candidates]

    # =========================================================================
    # 4. EXECUCIÓ INTEGRAL D'UNA CERCA GLOBAL ADAPTATIVA
    # =========================================================================

    def execute_global_search(
        self,
        allow_dc: bool = True,
        max_dc_candidates: Optional[int] = None,
        now_utc: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Executa una cerca intel·ligent completa seguint la política de calendari i pressupost.
        """
        now = now_utc or datetime.now(timezone.utc)
        dc_limit = max_dc_candidates if max_dc_candidates is not None else self.max_dc_candidates

        # Pas 1: Detecció gratuïta del calendari (0 crèdits)
        calendar = self.inspect_calendar(now_utc=now)

        if not calendar.active_leagues:
            return {
                "status": "NO_UPCOMING_MATCHES",
                "message": "No hi ha partits pròxims en les pròximes 72 hores a cap de les 7 lligues.",
                "credits_spent": 0,
                "credits_remaining": self.get_remaining_operational_budget(),
                "active_leagues": [],
                "empty_leagues": calendar.empty_leagues,
                "matches_examined": 0,
                "opportunities": [],
                "coverage_summary": {
                    "leagues_queried": 0,
                    "events_with_1x2": 0,
                    "events_with_dc": 0,
                    "total_upcoming_in_72h": 0
                }
            }

        # Pas 2: Estimació de cost i comprovació de pressupost
        num_active = len(calendar.active_leagues)
        # Si tenim poc pressupost, reduir o anul·lar Doble Oportunitat
        budget_left = self.get_remaining_operational_budget()
        cost_1x2_only = self.estimate_scan_cost(num_active, 0)

        can_run, reason = self.can_execute_scan(cost_1x2_only)
        if not can_run:
            raise TheOddsApiQuotaExhaustedError(reason)

        planned_dc_count = min(dc_limit, max(0, budget_left - cost_1x2_only)) if allow_dc else 0
        total_estimated_cost = self.estimate_scan_cost(num_active, planned_dc_count)

        # Pas 3: Consulta en bloc de 1X2 per a cada lliga activa
        all_raw_odds: List[Dict[str, Any]] = []
        leagues_queried: List[str] = []
        actual_credits_spent = 0

        for comp_id, sport_key in calendar.active_leagues.items():
            try:
                odds = self.client.get_odds(sport_key=sport_key, regions=self.default_regions, markets="h2h")
                actual_credits_spent += 1
                self.local_credits_consumed += 1
                leagues_queried.append(comp_id)
                for item in odds:
                    item["competition_id"] = comp_id
                    all_raw_odds.append(item)
            except Exception as e:
                # Si una lliga falla, continuar amb les altres
                pass

        # Pas 4: Consulta selectiva de Doble Oportunitat per als millors candidats
        dc_odds_by_event: Dict[str, Dict[str, Any]] = {}
        if planned_dc_count > 0 and all_raw_odds:
            candidates = self._prioritize_dc_candidates(all_raw_odds, max_candidates=planned_dc_count)
            for cand in candidates:
                ev_id = cand["event_id"]
                sp_key = cand["sport_key"]
                try:
                    dc_res = self.client.get_event_odds(
                        sport_key=sp_key,
                        event_id=ev_id,
                        regions=self.default_regions,
                        markets="double_chance"
                    )
                    actual_credits_spent += 1
                    self.local_credits_consumed += 1
                    dc_odds_by_event[ev_id] = dc_res
                except Exception:
                    pass

        # Pas 5: Càlcul d'arbitratges mitjançant ArbitrageEngine
        parsed_matches = self._parse_and_fuse_odds(all_raw_odds, dc_odds_by_event, now)
        opportunities: List[Dict[str, Any]] = []

        for m_data in parsed_matches:
            arbs = self.arbitrage_engine.scan_match(m_data, now=now)
            for a in arbs:
                self._enrich_jurisdiction_and_tags(a)
                opportunities.append(a)

        # Ordenar de major a menor ROI net
        opportunities.sort(key=lambda o: float(o.get("net_roi_pct") or 0.0), reverse=True)

        return {
            "status": "COMPLETED",
            "message": f"Cerca completada satisfactòriament: {len(opportunities)} oportunitats analitzades.",
            "credits_spent": actual_credits_spent,
            "credits_remaining": self.get_remaining_operational_budget(),
            "active_leagues": leagues_queried,
            "empty_leagues": calendar.empty_leagues,
            "matches_examined": len(all_raw_odds),
            "opportunities": opportunities,
            "coverage_summary": {
                "leagues_queried": len(leagues_queried),
                "events_with_1x2": len(all_raw_odds),
                "events_with_dc": len(dc_odds_by_event),
                "total_upcoming_in_72h": calendar.total_upcoming_72h,
                "scope_note": (
                    f"1X2 analitzat en {len(all_raw_odds)} partits de {len(leagues_queried)} lligues actives. "
                    f"Doble Oportunitat consultada per a {len(dc_odds_by_event)} partits candidats "
                    f"per preservació de quota."
                )
            }
        }

    # =========================================================================
    # 5. NORMALITZACIÓ, FUSIÓ DE QUOTES I JURISDICCIÓ
    # =========================================================================

    def _parse_and_fuse_odds(
        self,
        raw_1x2_list: List[Dict[str, Any]],
        dc_odds_by_event: Dict[str, Dict[str, Any]],
        now: datetime
    ) -> List[Dict[str, Any]]:
        """Converteix el JSON de The Odds API a l'estructura estàndard consumida per ArbitrageEngine."""
        matches: List[Dict[str, Any]] = []

        for ev in raw_1x2_list:
            ev_id = ev.get("id", "")
            h_team = ev.get("home_team", "")
            a_team = ev.get("away_team", "")
            comp_id = ev.get("competition_id", "DESCONEGUDA")
            commence = ev.get("commence_time", "")

            match_dict: Dict[str, Any] = {
                "id": ev_id,
                "home_team": {"name": h_team},
                "away_team": {"name": a_team},
                "matchup": f"{h_team} vs {a_team}",
                "competition_id": comp_id,
                "competition_name": comp_id,
                "date": commence,
                "source_pipeline": "THE_ODDS_API"
            }

            # Extreure 1X2 de cada bookmaker
            for bk in ev.get("bookmakers", []):
                bk_key = str(bk.get("key") or "").strip().lower()
                last_up = bk.get("last_update") or now.isoformat()
                for mkt in bk.get("markets", []):
                    if mkt.get("key") == "h2h":
                        odds_1x2: Dict[str, float] = {}
                        for out in mkt.get("outcomes", []):
                            name = out.get("name")
                            price = float(out.get("price") or 0.0)
                            if name == h_team:
                                odds_1x2["1"] = price
                            elif name == a_team:
                                odds_1x2["2"] = price
                            elif name in ("Draw", "Empat", "X"):
                                odds_1x2["X"] = price

                        if "1" in odds_1x2 and "X" in odds_1x2 and "2" in odds_1x2:
                            match_dict[f"odds_1x2_{bk_key}"] = odds_1x2
                            match_dict[f"provider_ts_{bk_key}"] = last_up
                            match_dict[f"captured_at_{bk_key}"] = now.isoformat()

            # Afegir Doble Oportunitat si n'hi ha per a aquest partit
            if ev_id in dc_odds_by_event:
                dc_ev = dc_odds_by_event[ev_id]
                for bk in dc_ev.get("bookmakers", []):
                    bk_key = str(bk.get("key") or "").strip().lower()
                    last_up = bk.get("last_update") or now.isoformat()
                    for mkt in bk.get("markets", []):
                        if mkt.get("key") == "double_chance":
                            dc_dict: Dict[str, float] = {}
                            for out in mkt.get("outcomes", []):
                                name = out.get("name")
                                price = float(out.get("price") or 0.0)
                                # The Odds API pot retornar "Home/Draw", "Draw/Away", "Home/Away" o "1X", "X2", "12"
                                if name in ("Home/Draw", f"{h_team}/Draw", "1X"):
                                    dc_dict["1X"] = price
                                elif name in ("Draw/Away", f"Draw/{a_team}", "X2"):
                                    dc_dict["X2"] = price
                                elif name in ("Home/Away", f"{h_team}/{a_team}", "12"):
                                    dc_dict["12"] = price

                            if dc_dict:
                                match_dict[f"odds_dc_{bk_key}"] = dc_dict
                                if f"provider_ts_{bk_key}" not in match_dict:
                                    match_dict[f"provider_ts_{bk_key}"] = last_up
                                    match_dict[f"captured_at_{bk_key}"] = now.isoformat()

            matches.append(match_dict)

        return matches

    def _enrich_jurisdiction_and_tags(self, opp: Dict[str, Any]) -> None:
        """
        Enriqueix una oportunitat amb informació de jurisdicció,
        marcant si és directament executable a Espanya o és teòrica/internacional.
        """
        opp["source_pipeline"] = "THE_ODDS_API"
        legs = opp.get("legs", [])
        all_spanish_dgoj = True

        for leg in legs:
            bk_raw = str(leg.get("bookmaker") or "").lower().strip()
            # Netetjar sufixos com _eu o _uk
            bk_base = bk_raw.split("_")[0]
            if bk_base in SPANISH_DGOJ_BOOKMAKERS:
                leg["jurisdiction"] = "ES_DGOJ"
                leg["is_local_spanish"] = True
            else:
                leg["jurisdiction"] = "INT_OFFSHORE"
                leg["is_local_spanish"] = False
                all_spanish_dgoj = False

        opp["executable_in_spain"] = all_spanish_dgoj
        opp["is_theoretical"] = not all_spanish_dgoj
        if not all_spanish_dgoj:
            opp["verification_badge"] = "⚠️ Quotes Internacionals (Teòric / Revisa disponibilitat a Espanya)"
        else:
            opp["verification_badge"] = "✅ Operadors Regulats a Espanya (DGOJ)"
