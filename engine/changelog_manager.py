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

    def add_entry(self, title: str, items: List[str], badge: str = "Actualització", badge_type: str = "info", date_str: Optional[str] = None):
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
            # Combinar items sense duplicar
            current_set = set(existing.get("items", []))
            for item in items:
                if item not in current_set:
                    existing.setdefault("items", []).append(item)
        else:
            entries.insert(0, {
                "date": target_date,
                "timestamp": now.isoformat(),
                "title": title,
                "badge": badge,
                "badge_type": badge_type,
                "items": items
            })

        # Mantenir un màxim de 30 entrades històriques
        entries = entries[:30]
        self.save_entries(entries)

    def record_daily_event(
        self,
        scraped_matches_count: int = 0,
        evaluated_combos: List[Dict[str, Any]] = None,
        referee_assigned_count: int = 0,
        new_combos_count: int = 0,
        value_bets_count: int = 0
    ):
        """Registra automàticament els canvis diaris derivats de l'execució."""
        items = []
        now = datetime.now()

        if referee_assigned_count > 0:
            items.append(f"⚖️ Revisades les designacions arbitrals oficials: {referee_assigned_count} partits amb àrbitre confirmat.")
        else:
            items.append("⚖️ Comprovació de designacions arbitrals del CTA / PGMOL: sense canvis d'última hora.")

        if scraped_matches_count > 0:
            items.append(f"⚽ Ingestats resultats i estadístiques oficials de {scraped_matches_count} partits finalitzats.")

        if evaluated_combos:
            won = sum(1 for c in evaluated_combos if c.get("status") == "WON")
            lost = sum(1 for c in evaluated_combos if c.get("status") == "LOST")
            items.append(f"📈 Avaluació de combinades: {won} encertades, {lost} no encertades. Balanç actualitzat.")

        if new_combos_count > 0:
            items.append(f"🎯 Generades {new_combos_count} combinades noves per a la propera jornada.")

        if value_bets_count > 0:
            items.append(f"💎 S'han detectat {value_bets_count} oportunitats amb valor matemàtic (+EV) enfront de les quotes de Winamax.")

        title = f"Actualització Diària del Model · {now.strftime('%d/%m/%Y')}"
        self.add_entry(title=title, items=items, badge="Diari", badge_type="primary")

    def get_feed(self, limit: int = 15) -> List[Dict[str, Any]]:
        entries = self.load_entries()
        return entries[:limit]

if __name__ == "__main__":
    mgr = ChangelogManager()
    print(f"Changelog inicialitzat amb {len(mgr.load_entries())} entrades.")
