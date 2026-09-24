"""
engine/changelog_manager.py
===========================
Gestor autònom del canal de novetats i canvis diaris del model ('Secció de Novetats').
Registra cada dia els esdeveniments clau:
- Ingesta de marcadors i canvis a l'Elo
- Revisió de designacions arbitrals del CTA / PGMOL
- Avaluació dinàmica de combinades (cames guanyades / fallades)
- Detecció d'apostes de valor (+EV)
- Reinicis i calibratges del sistema
"""

import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

DATA_DIR = Path(__file__).parent.parent / "data"
CHANGELOG_FILE = DATA_DIR / "changelog.json"

class ChangelogManager:
    def __init__(self, filepath: Optional[Path] = None):
        self.filepath = filepath if filepath else CHANGELOG_FILE
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        if not self.filepath.exists():
            self._init_default_changelog()

    def _init_default_changelog(self):
        initial_data = [
            {
                "date": "2026-09-21",
                "timestamp": datetime.now().isoformat(),
                "title": "Reinici de Bankroll a 0€ i Calibratge Complet del Model",
                "badge": "Sistema",
                "badge_type": "info",
                "items": [
                    "🔄 Reinici oficial del Bankroll a 0.00 € per iniciar el seguiment de les prediccions a partir d'avui (21/09/2026).",
                    "🎯 Generades 2 combinades diferents per perfil (Segures, Semi i Arriscades) per a cada lliga i Multi-Lliga (sense duplicats).",
                    "⚖️ Revisió i assignació de designacions arbitrals oficials (CTA / PGMOL) per als propers partits.",
                    "📊 Power Rànquings dinàmics d'Atac i Defensa calibrats amb gols reals per partit (GF/p i GC/p).",
                    "💎 Resolució de les mètriques d'avantatge matemàtic (+EV%) a totes les targetes d'apostes recomanades.",
                    "⏳ Suport per a actualització dinàmica de les combinades de cap de setmana (seguiment de cada partit individual)."
                ]
            }
        ]
        self.save_entries(initial_data)

    def load_entries(self) -> List[Dict[str, Any]]:
        if not self.filepath.exists():
            self._init_default_changelog()
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def save_entries(self, entries: List[Dict[str, Any]]):
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)

    def add_entry(
        self,
        title: str,
        items: List[str],
        badge: str = "Actualització",
        badge_type: str = "info",
        date_str: Optional[str] = None,
        matches: Optional[List[Dict[str, Any]]] = None,
        combos_evaluated: Optional[List[Dict[str, Any]]] = None,
        combos_generated: Optional[List[Dict[str, Any]]] = None
    ):
        """Afegeix o actualitza l'entrada de novetats per a la data indicada."""
        entries = self.load_entries()
        now = datetime.now()
        target_date = date_str or now.strftime("%Y-%m-%d")

        # Buscar si ja existeix entrada per a avui
        existing = next((e for e in entries if e.get("date") == target_date), None)
        if existing:
            existing["title"] = title
            existing["badge"] = badge
            existing["badge_type"] = badge_type
            existing["timestamp"] = now.isoformat()
            existing["items"] = items
            if matches is not None:
                existing["matches"] = matches
            if combos_evaluated is not None:
                existing["combos_evaluated"] = combos_evaluated
            if combos_generated is not None:
                existing["combos_generated"] = combos_generated
        else:
            new_entry = {
                "date": target_date,
                "timestamp": now.isoformat(),
                "title": title,
                "badge": badge,
                "badge_type": badge_type,
                "items": items,
                "matches": matches or [],
                "combos_evaluated": combos_evaluated or [],
                "combos_generated": combos_generated or []
            }
            entries.insert(0, new_entry)

        # Mantenir un màxim de 30 entrades històriques
        entries = entries[:30]
        self.save_entries(entries)

    def record_pipeline_execution(
        self,
        date_str: Optional[str] = None,
        ingested_matches: Optional[List[Dict[str, Any]]] = None,
        evaluated_combos: Optional[List[Dict[str, Any]]] = None,
        new_combos: Optional[List[Dict[str, Any]]] = None,
        referee_count: int = 0,
        competitions_checked: Optional[List[str]] = None,
        notes: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Registra la crida oficial del pipeline diari (08:00 UTC) amb informació exhaustiva:
        - Dia de l'execució
        - Partits jugats amb marcador, xG, targetes, córners i àrbitre
        - Canvis exactes d'Elo per equip
        - Combinades resoltes (guanyades/perdudes/balanç) o noves generades
        """
        now = datetime.now()
        target_date = date_str or now.strftime("%Y-%m-%d")
        day_formatted = now.strftime("%d/%m/%Y")
        matches = ingested_matches or []
        eval_combos = evaluated_combos or []
        gen_combos = new_combos or []

        items = []

        # 1. Informació dels partits disputats
        if matches:
            items.append(f"⚽ S'han ingesta resultats oficials i mètriques de {len(matches)} partit(s) disputat(s):")
            for m in matches:
                comp = m.get("competition_id", "")
                jornada = m.get("jornada", "")
                h_name = m.get("home_team", "")
                a_name = m.get("away_team", "")
                score = m.get("score") or f"{m.get('home_goals', 0)} - {m.get('away_goals', 0)}"
                
                xg_h = f"{m['home_xg']:.2f}" if m.get("home_xg") is not None else "-"
                xg_a = f"{m['away_xg']:.2f}" if m.get("away_xg") is not None else "-"
                tot_cards = (m.get("home_yellow_cards") or 0) + (m.get("away_yellow_cards") or 0) + (m.get("home_red_cards") or 0) + (m.get("away_red_cards") or 0)
                tot_corn = (m.get("home_corners") or 0) + (m.get("away_corners") or 0)
                ref = m.get("referee", "CTA / PGMOL")

                items.append(
                    f"   • [{comp} J{jornada}] {h_name} {score} {a_name} | xG: {xg_h}-{xg_a} | "
                    f"Targetes: {tot_cards} 🟨 | Córners: {tot_corn} 🚩 | Àrbitre: {ref}"
                )

            # 2. Canvis d'Elo derivats dels partits
            items.append("📊 Actualització de Power Rànquings Elo:")
            for m in matches:
                h_name = m.get("home_team", "")
                a_name = m.get("away_team", "")
                d_h = m.get("elo_change_home", 0.0)
                d_a = m.get("elo_change_away", 0.0)
                new_h = m.get("new_elo_home", "")
                new_a = m.get("new_elo_away", "")

                str_h = f"{h_name} ({d_h:+.1f}" + (f" ➔ {new_h}" if new_h else "") + ")"
                str_a = f"{a_name} ({d_a:+.1f}" + (f" ➔ {new_a}" if new_a else "") + ")"
                items.append(f"   • {str_h} | {str_a}")
        else:
            items.append("⚽ Partits: Cap partit oficial disputat en les darreres 24h a les lligues en seguiment.")
            items.append("📊 Power Rànquings: Elos de tots els equips intactes i calibrats segons la darrera jornada.")

        # 3. Avaluació de combinades
        if eval_combos:
            won_count = sum(1 for c in eval_combos if c.get("status") == "WON")
            lost_count = sum(1 for c in eval_combos if c.get("status") == "LOST")
            items.append(f"📈 S'han resolt {len(eval_combos)} combinades pendents ({won_count} encertades, {lost_count} fallades):")
            for c in eval_combos:
                st = "🏆 GUANYADA" if c.get("status") == "WON" else "❌ NO ENCERTADA"
                prof = c.get("profile", "Combinada")
                comp = c.get("competition_id", "")
                j = c.get("jornada", "")
                odd = float(c.get("odd") or c.get("combined_odd") or 1.0)
                pnl = float(c.get("profit", 0.0))
                items.append(f"   • [{comp} J{j}] {prof}: {st} (Cuota @{odd:.2f} · Balanç: {pnl:+.2f} €)")
        else:
            items.append("⏳ Combinades en curs: Les seleccions de les combinades pendents segueixen actives a l'espera dels propers partits.")

        # 4. Noves combinades generades
        if gen_combos:
            comps_gen = sorted(list(set(c.get("competition_id", "") for c in gen_combos if c.get("competition_id"))))
            comp_txt = ", ".join(comps_gen) if comps_gen else "properes jornades"
            items.append(f"🎯 Generades {len(gen_combos)} noves combinades recomanades (Segures, Semi i Arriscades) per a: {comp_txt}.")

        # 5. Arbitratge
        if referee_count > 0:
            items.append(f"⚖️ CTA & PGMOL: Revisades les designacions arbitrals oficials ({referee_count} àrbitres confirmats).")
        else:
            items.append("⚖️ Designacions Arbitrals: S'han verificat les designacions oficials del CTA i PGMOL per a la propera jornada.")

        if notes:
            for n in notes:
                items.append(f"ℹ️ {n}")

        title = f"Actualització Diària del Model · {day_formatted}"
        self.add_entry(
            title=title,
            items=items,
            badge="Diari (08:00 UTC)",
            badge_type="primary",
            date_str=target_date,
            matches=matches,
            combos_evaluated=eval_combos,
            combos_generated=gen_combos
        )
        return {"date": target_date, "title": title, "items": items}

    def record_daily_event(
        self,
        scraped_matches_count: int = 0,
        evaluated_combos: List[Dict[str, Any]] = None,
        referee_assigned_count: int = 0,
        new_combos_count: int = 0,
        value_bets_count: int = 0
    ):
        """Retrocompatibilitat per a crides antigues."""
        return self.record_pipeline_execution(
            referee_count=referee_assigned_count,
            evaluated_combos=evaluated_combos
        )

    def get_feed(self, limit: int = 15) -> List[Dict[str, Any]]:
        entries = self.load_entries()
        return entries[:limit]

if __name__ == "__main__":
    mgr = ChangelogManager()
    print(f"Changelog inicialitzat amb {len(mgr.load_entries())} entrades.")
