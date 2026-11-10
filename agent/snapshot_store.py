"""
agent/snapshot_store.py
=======================
Magatzem de snapshots amb política d'historial de 30 minuts (1800 segons).

Regles:
1. Retenció basada estrictament en l'antiguitat real de les quotes (fins a 1800s),
   no simplement en un recompte de cicles (ex: 5 cicles).
2. Deduplicació: no duplica snapshots si les quotes i timestamps són idèntics.
3. Si les quotes canvien entre cicles, conserva tant el snapshot anterior (com a històric)
   com el nou snapshot, fins que superin els 1800s.
4. Purgat automàtic de snapshots que superen els 1800 segons.
5. Pressupost d'emmagatzematge de seguretat configurable (max_snapshots).
"""

from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from agent.protocol import generate_opportunity_id, generate_snapshot_id, SnapshotRecord


class SnapshotStore:
    def __init__(self, max_snapshots: int = 500, max_age_seconds: float = 1800.0):
        self.max_snapshots = max_snapshots
        self.max_age_seconds = max_age_seconds
        # Indexació principal: snapshot_id -> SnapshotRecord
        self._snapshots: Dict[str, SnapshotRecord] = {}
        # Historial per opportunity_id: opportunity_id -> List[snapshot_id] ordenats cronològicament
        self._opp_history: Dict[str, List[str]] = {}

    def _parse_ts(self, ts_str: Optional[str]) -> Optional[datetime]:
        if not ts_str:
            return None
        try:
            s = str(ts_str).strip()
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None

    def add_opportunity(
        self,
        session_id: str,
        cycle_index: int,
        arb: Dict[str, Any],
        now: Optional[datetime] = None
    ) -> Optional[SnapshotRecord]:
        """
        Afegeix una oportunitat detectada al magatzem de snapshots si és vàlida i no supera els 30 minuts.
        Aplica deduplicació i preserva la traçabilitat històrica.
        """
        ref_now = now or datetime.now(timezone.utc)
        legs = arb.get("legs", [])
        if not legs:
            return None

        # Calcular l'edat màxima de les potes
        leg_ages = []
        for leg in legs:
            ts = self._parse_ts(leg.get("captured_at"))
            if ts:
                age_s = max(0.0, (ref_now - ts).total_seconds())
                leg_ages.append(age_s)
            else:
                leg_ages.append(0.0)

        age_max = max(leg_ages) if leg_ages else 0.0

        # Si l'edat supera els 1800s (30 minuts), queda descartada
        if age_max > self.max_age_seconds:
            return None

        match_id = arb.get("match_id") or arb.get("matchup", "unknown")
        market_name = arb.get("market_name", "standard")
        opp_id = generate_opportunity_id(match_id, market_name, legs)
        snap_id = generate_snapshot_id(opp_id, legs)

        # Classificació de frescor segons l'edat màxima
        if age_max <= 60.0:
            tier = "VERIFIED_FRESH"
        elif age_max <= 300.0:
            tier = "POTENTIALLY_STALE"
        else:
            tier = "RECENT_HISTORICAL"

        # Deduplicació: si ja existeix exactament aquest snapshot, actualitzem l'edat i retornem
        if snap_id in self._snapshots:
            existing = self._snapshots[snap_id]
            existing.age_max_seconds = round(age_max, 1)
            existing.freshness_tier = tier
            return existing

        # Crear nou registre de snapshot immutable
        cap_at = legs[0].get("captured_at") or ref_now.isoformat()
        record = SnapshotRecord(
            snapshot_id=snap_id,
            opportunity_id=opp_id,
            session_id=session_id,
            cycle_index=cycle_index,
            matchup=arb.get("matchup", ""),
            market_name=market_name,
            profit_pct=float(arb.get("profit_pct", 0.0)),
            freshness_tier=tier,
            legs=legs,
            captured_at=cap_at,
            age_max_seconds=round(age_max, 1)
        )

        self._snapshots[snap_id] = record
        if opp_id not in self._opp_history:
            self._opp_history[opp_id] = []
        self._opp_history[opp_id].append(snap_id)

        # Control del pressupost de seguretat
        self.prune_stale(now=ref_now)

        return record

    def prune_stale(self, now: Optional[datetime] = None) -> int:
        """
        Purga tots els snapshots que superin els 1800s d'antiguitat real o que sobrepassin
        el pressupost d'emmagatzematge.
        Retorna el nombre de snapshots eliminats.
        """
        ref_now = now or datetime.now(timezone.utc)
        to_delete = []

        for snap_id, record in list(self._snapshots.items()):
            # Re-avaluar l'edat actual a partir del captured_at immutable
            ts = self._parse_ts(record.captured_at)
            age = (ref_now - ts).total_seconds() if ts else record.age_max_seconds
            if age > self.max_age_seconds:
                to_delete.append(snap_id)

        # Si encara sobrepassa max_snapshots, eliminar els més antics
        if len(self._snapshots) - len(to_delete) > self.max_snapshots:
            remaining = [
                (sid, rec) for sid, rec in self._snapshots.items() if sid not in to_delete
            ]
            remaining.sort(key=lambda x: self._parse_ts(x[1].captured_at) or ref_now)
            excess = len(remaining) - self.max_snapshots
            for i in range(excess):
                to_delete.append(remaining[i][0])

        deleted_count = 0
        for sid in to_delete:
            rec = self._snapshots.pop(sid, None)
            if rec and rec.opportunity_id in self._opp_history:
                try:
                    self._opp_history[rec.opportunity_id].remove(sid)
                except ValueError:
                    pass
            deleted_count += 1

        return deleted_count

    def get_active_snapshots(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Retorna la vista completa de snapshots dels darrers 30 minuts, agrupada per nivells
        de frescor. Una oportunitat de fa 20 minuts continua visible a recent_historical.
        """
        ref_now = now or datetime.now(timezone.utc)
        self.prune_stale(now=ref_now)

        fresh: List[Dict[str, Any]] = []
        potentially_stale: List[Dict[str, Any]] = []
        recent_historical: List[Dict[str, Any]] = []

        for record in self._snapshots.values():
            ts = self._parse_ts(record.captured_at)
            age = max(0.0, (ref_now - ts).total_seconds()) if ts else record.age_max_seconds

            rec_dict = record.to_dict()
            rec_dict["current_age_seconds"] = round(age, 1)

            if age <= 60.0:
                rec_dict["freshness_tier"] = "VERIFIED_FRESH"
                fresh.append(rec_dict)
            elif age <= 300.0:
                rec_dict["freshness_tier"] = "POTENTIALLY_STALE"
                potentially_stale.append(rec_dict)
            elif age <= self.max_age_seconds:
                rec_dict["freshness_tier"] = "RECENT_HISTORICAL"
                recent_historical.append(rec_dict)

        # Ordenar de major a menor benefici percentual
        fresh.sort(key=lambda x: x["profit_pct"], reverse=True)
        potentially_stale.sort(key=lambda x: x["profit_pct"], reverse=True)
        recent_historical.sort(key=lambda x: x["profit_pct"], reverse=True)

        return {
            "summary": {
                "total_snapshots": len(self._snapshots),
                "total_fresh": len(fresh),
                "total_potentially_stale": len(potentially_stale),
                "total_recent_historical": len(recent_historical),
                "max_retention_seconds": self.max_age_seconds
            },
            "verified_fresh": fresh,
            "potentially_stale": potentially_stale,
            "recent_historical": recent_historical,
            "all_active": fresh + potentially_stale + recent_historical
        }

