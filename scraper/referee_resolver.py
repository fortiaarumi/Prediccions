"""
scraper/referee_resolver.py
===========================
Mòdul intel·ligent per resoldre l'àrbitre oficial designat per a cada partit.

Comportament:
1. Comprova si el partit ja té àrbitre guardat a SQLite.
2. Comprova les designacions oficials publicades pel CTA (RFEF) per a la jornada.
3. Comprova en directe els feeds de comentaris/acta de Flashscore si estan disponibles.
4. Si troba l'àrbitre oficial, l'assigna amb el seu perfil i estadístiques reals.
5. Si encara NO s'ha publicat (ex: partits de diumenge/dilluns pendents pel CTA),
   assigna l'àrbitre genèric ('REF_DEFAULT') i marca 'is_generic = True' per indicar
   que s'aplica el perfil mitjà de la lliga pendent de designació oficial.
"""

import re
import subprocess
from typing import Optional, Dict, Any
from database.db_manager import DatabaseManager

class RefereeResolver:
    def __init__(self, db: DatabaseManager = None):
        self.db = db if db else DatabaseManager()

        # Cache opcional de designacions prèvies o arxiu
        self.official_designations = {}

    def resolve_referee(self, home_name: str, away_name: str, match_code: str = None, match_date: str = None) -> Dict[str, Any]:
        """
        Retorna la informació de l'àrbitre per al partit de forma 100% autònoma:
        1. Comprovació en directe als feeds oficials de Flashscore (df_sui_1 / df_su_1 / df_lc_1).
        2. Comprovació a la base de dades local SQLite si el partit ja s'ha disputat o registrat.
        3. Comprovació a les designacions oficials conegudes (fallback).
        4. Si el CTA encara no ha publicat les designacions oficials (ex: jornada 30 predita setmanes abans),
           retorna l'àrbitre per defecte amb is_generic = True (perfil mitjà, sense apostes de targetes).
        """
        home_id = self.db.find_team_id(home_name)
        away_id = self.db.find_team_id(away_name)

        # 1. Resolució autònoma en directe des de Flashscore (acta i informació del partit)
        if match_code:
            ref_id = self._scrape_referee_from_flashscore(match_code)
            if ref_id and ref_id != "REF_DEFAULT":
                ref_data = self.db.get_referee(ref_id)
                return {
                    "id": ref_id,
                    "name": ref_data["name"],
                    "is_generic": False,
                    "ref_data": ref_data
                }

        # 2. Comprovació si el partit ja té un àrbitre guardat a SQLite
        if home_id and away_id:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT referee_id FROM matches WHERE home_team_id = ? AND away_team_id = ? ORDER BY id DESC LIMIT 1",
                    (home_id, away_id)
                )
                row = cursor.fetchone()
                if row and row["referee_id"] and row["referee_id"] != "REF_DEFAULT":
                    ref_id = row["referee_id"]
                    ref_data = self.db.get_referee(ref_id)
                    return {
                        "id": ref_id,
                        "name": ref_data["name"],
                        "is_generic": False,
                        "ref_data": ref_data
                    }

        # 3. Fallback a registre de designacions prèvies si existís
        if home_id and away_id and (home_id, away_id) in self.official_designations:
            ref_id = self.official_designations[(home_id, away_id)]
            ref_data = self.db.get_referee(ref_id)
            return {
                "id": ref_id,
                "name": ref_data["name"],
                "is_generic": False,
                "ref_data": ref_data
            }

        # 4. Si encara no s'ha anunciat oficialment pel CTA (partit futur):
        ref_default_data = self.db.get_referee("REF_DEFAULT")
        return {
            "id": "REF_DEFAULT",
            "name": "Pendent CTA (Àrbitre Mitjà Standard)",
            "is_generic": True,
            "ref_data": ref_default_data
        }

    def _scrape_referee_from_flashscore(self, match_code: str) -> Optional[str]:
        """
        Extreu autònomament l'àrbitre oficial des dels feeds estructurats de Flashscore:
        - Feed df_sui_1 / df_su_1: Informació oficial del partit (camp MIT -> REF -> MIV).
        - Feed df_lc_1: Comentaris en viu i presentació de l'àrbitre.
        """
        try:
            # A. Feed d'informació estructurada del partit (df_sui_1)
            cmd_info = ['curl.exe', '-s', '-H', 'x-fsign: SW9D1eZo', f'https://local-global.flashscore.ninja/2/x/feed/df_sui_1_{match_code}']
            res_info = subprocess.run(cmd_info, capture_output=True, text=True, encoding='utf-8', errors='replace')
            if res_info.stdout and len(res_info.stdout) > 50:
                # Patró del feed: MIT...REF...MIV...<Nom Àrbitre> (ex: MITREFMIVManzano J.)
                m = re.search(r'MIT[^\w]*REF[^\w]*MIV[^\w]*([A-Za-z\s\.-]+)', res_info.stdout)
                if m:
                    raw_name = m.group(1).strip()
                    ref_id = self.db.find_referee_id(raw_name)
                    if ref_id and ref_id != "REF_DEFAULT":
                        return ref_id

            # B. Feed de comentaris en viu (df_lc_1)
            cmd_lc = ['curl.exe', '-s', '-H', 'x-fsign: SW9D1eZo', f'https://local-global.flashscore.ninja/2/x/feed/df_lc_1_{match_code}']
            res_lc = subprocess.run(cmd_lc, capture_output=True, text=True, encoding='utf-8', errors='replace')
            if res_lc.stdout and len(res_lc.stdout) > 50:
                m_ref = re.search(r'([A-Za-z\s\.-]+)\s+will referee today\'s match', res_lc.stdout)
                if not m_ref:
                    m_ref = re.search(r'([A-Za-z\s\.-]+)\s+is introduced as the referee', res_lc.stdout)
                if m_ref:
                    raw_name = m_ref.group(1).strip()
                    ref_id = self.db.find_referee_id(raw_name)
                    if ref_id and ref_id != "REF_DEFAULT":
                        return ref_id
        except Exception:
            pass
        return None
