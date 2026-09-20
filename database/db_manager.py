import sqlite3
import os
import re
import unicodedata
from pathlib import Path
from typing import List, Dict, Any, Optional

DEFAULT_DB_PATH = Path(__file__).parent.parent / "data" / "laliga.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"

class DatabaseManager:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=60.0)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        """Inicialitza l'esquema relacional si no existeix."""
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            schema_sql = f.read()
        with self.get_connection() as conn:
            conn.executescript(schema_sql)
            conn.commit()

    def seed_initial_data(self):
        """Introdueix els 20 equips oficials de La Lliga i els 20 àrbitres principals de camp del CTA."""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # 1. Competicions
            cursor.execute(
                "INSERT OR REPLACE INTO competitions (id, name, type, weight) VALUES ('LALIGA', 'LaLiga EA Sports', 'LEAGUE', 1.0)"
            )
            cursor.execute(
                "INSERT OR REPLACE INTO competitions (id, name, type, weight) VALUES ('PREMIER', 'Premier League', 'LEAGUE', 1.0)"
            )
            cursor.execute(
                "INSERT OR REPLACE INTO competitions (id, name, type, weight) VALUES ('HYPERMOTION', 'LaLiga Hypermotion', 'LEAGUE', 0.85)"
            )

            # 2. Els 20 Equips Oficials de Primera Divisió
            teams_laliga = [
                ("ATM", "Atlético de Madrid", "At. Madrid", "Atlético de Madrid,Atletico Madrid,Atlético,Atletico,ATM,Atleti", "Riyadh Air Metropolitano", "Madrid"),
                ("RMA", "Real Madrid", "R. Madrid", "Real Madrid,Madrid,RMA,Real Madrid CF", "Santiago Bernabéu", "Madrid"),
                ("FCB", "FC Barcelona", "Barcelona", "FC Barcelona,Barcelona,Barça,Barca,FCB", "Spotify Camp Nou", "Barcelona"),
                ("VIL", "Villarreal CF", "Villarreal", "Villarreal CF,Villarreal,VIL", "Estadio de la Cerámica", "Vila-real"),
                ("ATH", "Athletic Club", "Athletic", "Athletic Club,Athletic Bilbao,Athletic,ATH", "San Mamés", "Bilbao"),
                ("RSO", "Real Sociedad", "R. Sociedad", "Real Sociedad,Real Soc,RSO,La Real", "Reale Arena", "Donostia"),
                ("BET", "Real Betis", "Betis", "Real Betis,Betis,BET,Real Betis Balompié", "Benito Villamarín", "Sevilla"),
                ("SEV", "Sevilla FC", "Sevilla", "Sevilla FC,Sevilla,SEV", "Ramón Sánchez-Pizjuán", "Sevilla"),
                ("VAL", "Valencia CF", "Valencia", "Valencia CF,Valencia,VAL", "Mestalla", "València"),
                ("OSA", "CA Osasuna", "Osasuna", "CA Osasuna,Osasuna,OSA", "El Sadar", "Pamplona"),
                ("CEL", "RC Celta de Vigo", "Celta", "RC Celta de Vigo,Celta Vigo,Celta,CEL,Celta de Vigo", "Abanca-Balaídos", "Vigo"),
                ("RAY", "Rayo Vallecano", "Rayo", "Rayo Vallecano,Rayo,RAY", "Vallecas", "Madrid"),
                ("GET", "Getafe CF", "Getafe", "Getafe CF,Getafe,GET", "Coliseum", "Getafe"),
                ("ALA", "Deportivo Alavés", "Alavés", "Deportivo Alavés,Alavés,Alaves,ALA", "Mendizorroza", "Vitoria-Gasteiz"),
                ("ESP", "RCD Espanyol", "Espanyol", "RCD Espanyol,Espanyol,ESP", "Stage Front Stadium", "Barcelona"),
                ("RDS", "Racing de Santander", "Racing", "Real Racing Club,Racing de Santander,Racing,RDS,Santander,R. Racing Club", "El Sardinero", "Santander"),
                ("DEP", "RC Deportivo de La Coruña", "Deportivo", "RC Deportivo de La Coruña,Deportivo de La Coruña,Deportivo de A Coruña,Depor,DEP,Deportivo", "Riazor", "A Coruña"),
                ("MAL", "Málaga CF", "Málaga", "Málaga CF,Malaga CF,Málaga,Malaga,MAL", "La Rosaleda", "Málaga"),
                ("LEV", "Levante UD", "Levante", "Levante UD,Levante,LEV", "Ciutat de València", "València"),
                ("ELC", "Elche CF", "Elche", "Elche CF,Elche,ELC", "Martínez Valero", "Elx"),
            ]

            # 3. Equips de Premier League
            teams_premier = [
                ("MCI", "Manchester City", "Man. City", "Manchester City,Man City,MCI,City", "Etihad Stadium", "Manchester"),
                ("ARS", "Arsenal", "Arsenal", "Arsenal,Arsenal FC,ARS,The Gunners", "Emirates Stadium", "London"),
                ("LIV", "Liverpool", "Liverpool", "Liverpool,Liverpool FC,LIV,The Reds", "Anfield", "Liverpool"),
                ("AVL", "Aston Villa", "Aston Villa", "Aston Villa,Aston Villa FC,AVL,Villa", "Villa Park", "Birmingham"),
                ("TOT", "Tottenham Hotspur", "Tottenham", "Tottenham Hotspur,Tottenham,Spurs,TOT", "Tottenham Hotspur Stadium", "London"),
                ("CHE", "Chelsea", "Chelsea", "Chelsea,Chelsea FC,CHE,The Blues", "Stamford Bridge", "London"),
                ("NEW", "Newcastle United", "Newcastle", "Newcastle United,Newcastle,NEW,The Magpies", "St. James' Park", "Newcastle"),
                ("MUN", "Manchester United", "Man. United", "Manchester United,Man United,MUN,United", "Old Trafford", "Manchester"),
                ("WHU", "West Ham United", "West Ham", "West Ham United,West Ham,WHU,The Hammers", "London Stadium", "London"),
                ("BHA", "Brighton & Hove Albion", "Brighton", "Brighton & Hove Albion,Brighton,BHA,The Seagulls", "Amex Stadium", "Brighton"),
                ("BOU", "AFC Bournemouth", "Bournemouth", "AFC Bournemouth,Bournemouth,BOU,The Cherries", "Vitality Stadium", "Bournemouth"),
                ("FUL", "Fulham", "Fulham", "Fulham,Fulham FC,FUL,The Cottagers", "Craven Cottage", "London"),
                ("WOL", "Wolverhampton Wanderers", "Wolves", "Wolverhampton Wanderers,Wolverhampton,Wolves,WOL", "Molineux", "Wolverhampton"),
                ("CRY", "Crystal Palace", "Crystal Palace", "Crystal Palace,Palace,CRY,The Eagles", "Selhurst Park", "London"),
                ("BRE", "Brentford", "Brentford", "Brentford,Brentford FC,BRE,The Bees", "Gtech Community Stadium", "Brentford"),
                ("EVE", "Everton", "Everton", "Everton,Everton FC,EVE,The Toffees", "Goodison Park", "Liverpool"),
                ("NFO", "Nottingham Forest", "Nottm Forest", "Nottingham Forest,Nottingham,Forest,NFO", "City Ground", "Nottingham"),
                ("IPS", "Ipswich Town", "Ipswich", "Ipswich Town,Ipswich,IPS,The Tractor Boys", "Portman Road", "Ipswich"),
                ("LEI", "Leicester City", "Leicester", "Leicester City,Leicester,LEI,The Foxes", "King Power Stadium", "Leicester"),
                ("SOU", "Southampton", "Southampton", "Southampton,Southampton FC,SOU,The Saints", "St Mary's Stadium", "Southampton"),
            ]

            # 4. Equips de LaLiga Hypermotion (2a Divisió)
            teams_hypermotion = [
                ("ZAR", "Real Zaragoza", "Zaragoza", "Real Zaragoza,Zaragoza,ZAR", "La Romareda", "Zaragoza"),
                ("ROV", "Real Oviedo", "Oviedo", "Real Oviedo,Oviedo,ROV", "Carlos Tartiere", "Oviedo"),
                ("SPG", "Sporting de Gijón", "Sporting", "Sporting de Gijón,Sporting Gijón,Sporting,SPG", "El Molinón", "Gijón"),
                ("EIB", "SD Eibar", "Eibar", "SD Eibar,Eibar,EIB", "Ipurua", "Eibar"),
                ("CAS", "CD Castellón", "Castellón", "CD Castellón,Castellon,Castellón,CAS", "SkyFi Castalia", "Castelló"),
                ("BUR", "Burgos CF", "Burgos", "Burgos CF,Burgos,BUR", "El Plantío", "Burgos"),
                ("ALB", "Albacete Balompié", "Albacete", "Albacete Balompié,Albacete,ALB", "Carlos Belmonte", "Albacete"),
                ("HUE", "SD Huesca", "Huesca", "SD Huesca,Huesca,HUE", "El Alcoraz", "Huesca"),
                ("GRA", "Granada CF", "Granada", "Granada CF,Granada,GRA", "Los Cármenes", "Granada"),
                ("ALM", "UD Almería", "Almería", "UD Almería,Almeria,Almería,ALM", "Power Horse Stadium", "Almería"),
                ("CAD", "Cádiz CF", "Cádiz", "Cádiz CF,Cadiz,Cádiz,CAD", "Nuevo Mirandilla", "Cádiz"),
                ("COR", "Córdoba CF", "Córdoba", "Córdoba CF,Cordoba,Córdoba,COR", "El Arcángel", "Córdoba"),
                ("MIR", "CD Mirandés", "Mirandés", "CD Mirandés,Mirandes,Mirandés,MIR", "Anduva", "Miranda de Ebro"),
                ("ELD", "CD Eldense", "Eldense", "CD Eldense,Eldense,ELD", "Pepico Amat", "Elda"),
                ("RFE", "Racing de Ferrol", "R. Ferrol", "Racing de Ferrol,Racing Ferrol,RFE", "A Malata", "Ferrol"),
                ("TEN", "CD Tenerife", "Tenerife", "CD Tenerife,Tenerife,TEN", "Heliodoro Rodríguez López", "Santa Cruz de Tenerife"),
                ("CAR", "FC Cartagena", "Cartagena", "FC Cartagena,Cartagena,CAR", "Cartagonova", "Cartagena"),
            ]

            all_teams = teams_laliga + teams_premier + teams_hypermotion
            cursor.executemany(
                "INSERT OR REPLACE INTO teams (id, name, short_name, aliases, stadium, city) VALUES (?, ?, ?, ?, ?, ?)",
                all_teams
            )

            # 5. Àrbitres Oficials de Camp (CTA i Estàndard)
            referees = [
                ("REF_HERNANDEZ_HERNANDEZ", "Alejandro Hernández Hernández", 215, 5.40, 0.35, 27.5, 0.38, 0.44, 1.20),
                ("REF_BUSQUETS_FERRER", "Mateo Busquets Ferrer", 42, 5.65, 0.40, 28.2, 0.36, 0.42, 1.25),
                ("REF_GIL_MANZANO", "Jesús Gil Manzano", 235, 5.10, 0.30, 26.0, 0.32, 0.46, 1.13),
                ("REF_SANCHEZ_MARTINEZ", "José María Sánchez Martínez", 195, 4.80, 0.22, 25.5, 0.28, 0.45, 1.06),
                ("REF_SOTO_GRADO", "César Soto Grado", 125, 4.90, 0.25, 26.5, 0.30, 0.43, 1.10),
                ("REF_DE_BURGOS_BENGOETXEA", "Ricardo De Burgos Bengoetxea", 190, 4.20, 0.18, 23.5, 0.25, 0.47, 0.92),
                ("REF_ALBEROLA_ROJAS", "Javier Alberola Rojas", 155, 3.65, 0.12, 21.0, 0.20, 0.50, 0.80),
                ("REF_MARTINEZ_MUNUERA", "Juan Martínez Munuera", 225, 4.45, 0.20, 24.2, 0.26, 0.48, 0.98),
                ("REF_MUNUERA_MONTERO", "José Luis Munuera Montero", 168, 4.60, 0.23, 25.0, 0.27, 0.45, 1.02),
                ("REF_ORTIZ_ARIAS", "Miguel Ángel Ortiz Arias", 98, 4.70, 0.22, 25.2, 0.29, 0.45, 1.04),
                ("REF_PULIDO_SANTANA", "Juan Luis Pulido Santana", 78, 5.00, 0.28, 26.5, 0.31, 0.43, 1.12),
                ("REF_GARCIA_VERDURA", "Víctor García Verdura", 48, 4.40, 0.18, 24.0, 0.24, 0.46, 0.95),
                ("REF_QUINTERO_GONZALEZ", "Alejandro Quintero González", 38, 4.55, 0.20, 24.8, 0.25, 0.45, 1.00),
                ("REF_SESMA_ESPINOSA", "Miguel Sesma Espinosa", 25, 4.60, 0.22, 25.0, 0.26, 0.45, 1.01),
                ("REF_MUNIZ_RUIZ_C", "Carlos Muñiz", 15, 4.80, 0.25, 25.5, 0.28, 0.44, 1.06),
                ("REF_GONZALEZ_ESTEBAN", "Jon Ander González", 15, 5.10, 0.30, 26.5, 0.30, 0.43, 1.13),
                ("REF_BESTARD_SERVERA", "Luis Bestard", 15, 4.75, 0.20, 24.5, 0.25, 0.46, 1.05),
                ("REF_HERNANDEZ_MAESO", "Francisco José Hernández Maeso", 55, 5.20, 0.28, 26.8, 0.32, 0.44, 1.15),
                ("REF_DIAZ_DE_MERA", "Isidro Díaz de Mera Escuderos", 92, 5.30, 0.32, 27.2, 0.34, 0.45, 1.18),
                ("REF_CUADRA_FERNANDEZ", "Guillermo Cuadra Fernández", 145, 4.65, 0.24, 25.4, 0.28, 0.46, 1.03),
                ("REF_MELERO_LOPEZ", "Mario Melero López", 180, 4.85, 0.26, 26.0, 0.30, 0.45, 1.07),

                # Premier League Referees
                ("REF_TAYLOR_A", "Anthony Taylor", 370, 3.90, 0.16, 21.5, 0.28, 0.46, 1.15),
                ("REF_OLIVER_M", "Michael Oliver", 360, 3.65, 0.14, 20.8, 0.30, 0.47, 1.05),
                ("REF_TIERNEY_P", "Paul Tierney", 175, 3.55, 0.12, 21.0, 0.24, 0.45, 1.02),
                ("REF_HOOPER_S", "Simon Hooper", 95, 3.70, 0.10, 22.0, 0.22, 0.46, 1.06),
                ("REF_KAVANAGH_C", "Chris Kavanagh", 130, 3.85, 0.15, 21.8, 0.26, 0.44, 1.10),
                ("REF_PAWSON_C", "Craig Pawson", 235, 3.95, 0.18, 22.5, 0.27, 0.45, 1.14),
                ("REF_ATTWELL_S", "Stuart Attwell", 195, 3.75, 0.14, 21.2, 0.25, 0.46, 1.08),
                ("REF_JONES_R", "Robert Jones", 80, 4.10, 0.15, 22.8, 0.28, 0.43, 1.18),
                ("REF_BROOKS_J", "John Brooks", 55, 4.30, 0.18, 23.5, 0.30, 0.44, 1.22),
                ("REF_MADLEY_A", "Andy Madley", 90, 3.40, 0.09, 20.2, 0.20, 0.48, 0.96),
                ("REF_GILLETT_J", "Jarred Gillett", 48, 4.20, 0.12, 22.0, 0.25, 0.45, 1.17),
                ("REF_BANKES_P", "Peter Bankes", 82, 4.35, 0.15, 23.0, 0.28, 0.44, 1.20),
                ("REF_ENGLAND_D", "Darren England", 55, 3.80, 0.11, 21.5, 0.23, 0.46, 1.08),
                ("REF_ROBINSON_T", "Tim Robinson", 38, 4.15, 0.14, 22.6, 0.26, 0.44, 1.18),
                ("REF_BRAMALL_T", "Thomas Bramall", 30, 3.60, 0.10, 20.5, 0.21, 0.46, 1.00),
                ("REF_SALISBURY_M", "Michael Salisbury", 32, 3.50, 0.10, 20.8, 0.20, 0.47, 0.98),
                ("REF_BARROTT_S", "Samuel Barrott", 25, 3.35, 0.08, 19.8, 0.18, 0.48, 0.94),
                ("REF_BOND_D", "Darren Bond", 20, 3.70, 0.12, 21.4, 0.22, 0.45, 1.05),

                # LaLiga Hypermotion (Segunda División) Referees
                ("REF_CID_CAMACHO", "Germán Cid Camacho", 50, 5.30, 0.32, 26.5, 0.32, 0.43, 1.18),
                ("REF_ARCEDIANO", "Dámaso Arcediano Monescillo", 280, 5.55, 0.36, 27.8, 0.36, 0.42, 1.24),
                ("REF_FUENTES_MOLINA", "Andrés Fuentes Molina", 48, 5.10, 0.28, 26.0, 0.30, 0.44, 1.14),
                ("REF_PEREZ_HERNANDEZ", "Manuel Ángel Pérez Hernández", 42, 4.95, 0.25, 25.5, 0.28, 0.45, 1.10),
                ("REF_GONZALEZ_DIAZ", "Miguel González Díaz", 45, 5.25, 0.30, 26.8, 0.31, 0.43, 1.17),
                ("REF_LAX_FRANCO", "Salvador Lax Franco", 40, 5.40, 0.34, 27.2, 0.33, 0.43, 1.20),
                ("REF_HUERTA_DE_AZA", "Marta Huerta de Aza", 25, 4.80, 0.20, 25.0, 0.26, 0.45, 1.06),
                ("REF_MUNIZ_MUNOZ", "Carlos Muñiz Muñoz", 28, 5.15, 0.28, 26.2, 0.29, 0.44, 1.15),
                ("REF_MALLO_FERNANDEZ", "Eder Mallo Fernández", 24, 5.35, 0.30, 27.0, 0.32, 0.43, 1.19),
                ("REF_SANCHEZ_VILLALOBOS", "José Antonio Sánchez Villalobos", 26, 5.20, 0.28, 26.4, 0.30, 0.44, 1.16),
                ("REF_MORENO_ARAGON", "Álvaro Moreno Aragón", 140, 5.45, 0.35, 27.5, 0.34, 0.42, 1.22),
                ("REF_AIS_REIG", "Saúl Ais Reig", 195, 5.10, 0.28, 26.0, 0.30, 0.44, 1.14),
                ("REF_SANCHEZ_LOPEZ", "Rafael Sánchez López", 88, 5.30, 0.31, 26.9, 0.32, 0.43, 1.18),
                ("REF_MURESAN_MURESAN", "Sergiu Claudiu Muresan Muresan", 42, 5.60, 0.38, 28.0, 0.36, 0.41, 1.25),
                ("REF_OJAOS_VALERA", "Antonio Ojaos Valera", 28, 5.20, 0.28, 26.3, 0.30, 0.44, 1.16),
                ("REF_GUZMAN_MANSILLA", "José Luis Guzmán Mansilla", 45, 5.35, 0.30, 27.0, 0.32, 0.43, 1.19),

                ("REF_DEFAULT", "Àrbitre Mitjà (Standard)", 500, 4.50, 0.25, 25.0, 0.28, 0.45, 1.00),
            ]
            cursor.executemany(
                """INSERT OR REPLACE INTO referees 
                   (id, name, matches_count, yellow_cards_avg, red_cards_avg, fouls_avg, penalties_avg, home_win_pct, strictness_index, updated_at) 
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                referees
            )

            # 6. Rànquings base inicials
            for t_id, _, _, _, _, _ in all_teams:
                # LaLiga ratings
                if t_id == "FCB":
                    ranks = (1.0, 1.0, 1.5, 1960.0)
                elif t_id == "RMA":
                    ranks = (2.0, 1.5, 1.5, 1950.0)
                elif t_id == "ATM":
                    ranks = (3.0, 3.0, 2.5, 1860.0)
                elif t_id in ["VIL", "ATH", "RSO", "BET"]:
                    ranks = (5.5, 5.5, 5.5, 1740.0)
                elif t_id in ["SEV", "VAL", "OSA", "CEL", "RAY", "GET", "ALA", "ESP"]:
                    ranks = (11.0, 11.0, 11.0, 1590.0)
                # Premier League ratings
                elif t_id in ["MCI", "ARS", "LIV"]:
                    ranks = (1.5, 1.5, 1.5, 1980.0)
                elif t_id in ["AVL", "TOT", "CHE", "NEW"]:
                    ranks = (4.0, 4.0, 4.0, 1820.0)
                elif t_id in ["MUN", "WHU", "BHA", "BOU", "FUL"]:
                    ranks = (8.0, 8.0, 8.0, 1680.0)
                elif t_id in ["WOL", "CRY", "BRE", "EVE", "NFO"]:
                    ranks = (13.0, 13.0, 13.0, 1580.0)
                elif t_id in ["IPS", "LEI", "SOU"]:
                    ranks = (18.0, 18.0, 18.0, 1490.0)
                # Hypermotion ratings
                elif t_id in ["GRA", "ALM", "CAD", "ZAR", "EIB", "ROV", "SPG", "LEV"]:
                    ranks = (5.0, 5.0, 5.0, 1520.0)
                else: # RDS, DEP, MAL, ELC, CAS, BUR, ALB, HUE, COR, MIR, ELD, RFE, TEN, CAR
                    ranks = (14.0, 14.0, 14.0, 1440.0)

                cursor.execute(
                    """INSERT OR REPLACE INTO team_ratings 
                       (team_id, general_rank, off_rank, def_rank, elo_rating, rest_days, updated_at) 
                       VALUES (?, ?, ?, ?, ?, 7.0, datetime('now'))""",
                    (t_id, ranks[0], ranks[1], ranks[2], ranks[3])
                )

            conn.commit()

    def find_team_id(self, query_name: str) -> Optional[str]:
        """
        Troba l'ID de l'equip de forma precisa i sense col·lisions d'àlies curts.
        """
        if not query_name:
            return None
        
        q = query_name.strip().lower()
        # Neteja de caràcters especials per a comparació
        q_norm = q.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")

        # Diccionari directe de resolució inequívoca per a noms habituals a Flashscore/Sofascore
        exact_mappings = {
            "atletico de madrid": "ATM",
            "atletico madrid": "ATM",
            "atletico": "ATM",
            "atleti": "ATM",
            "real madrid": "RMA",
            "madrid": "RMA",
            "fc barcelona": "FCB",
            "barcelona": "FCB",
            "barca": "FCB",
            "sevilla": "SEV",
            "sevilla fc": "SEV",
            "villarreal": "VIL",
            "villarreal cf": "VIL",
            "athletic club": "ATH",
            "athletic bilbao": "ATH",
            "athletic": "ATH",
            "real sociedad": "RSO",
            "real betis": "BET",
            "betis": "BET",
            "valencia": "VAL",
            "valencia cf": "VAL",
            "osasuna": "OSA",
            "ca osasuna": "OSA",
            "celta de vigo": "CEL",
            "celta": "CEL",
            "rc celta de vigo": "CEL",
            "rayo vallecano": "RAY",
            "rayo": "RAY",
            "getafe": "GET",
            "getafe cf": "GET",
            "alaves": "ALA",
            "deportivo alaves": "ALA",
            "espanyol": "ESP",
            "rcd espanyol": "ESP",
            "racing de santander": "RDS",
            "r. racing club": "RDS",
            "real racing club": "RDS",
            "racing": "RDS",
            "deportivo de la coruna": "DEP",
            "deportivo de a coruna": "DEP",
            "rc deportivo de la coruna": "DEP",
            "deportivo": "DEP",
            "depor": "DEP",
            "malaga": "MAL",
            "malaga cf": "MAL",
            "levante": "LEV",
            "levante ud": "LEV",
            "elche": "ELC",
            "elche cf": "ELC",

            # Premier League
            "manchester city": "MCI",
            "man city": "MCI",
            "mci": "MCI",
            "arsenal": "ARS",
            "arsenal fc": "ARS",
            "ars": "ARS",
            "liverpool": "LIV",
            "liverpool fc": "LIV",
            "liv": "LIV",
            "aston villa": "AVL",
            "villa": "AVL",
            "avl": "AVL",
            "tottenham": "TOT",
            "tottenham hotspur": "TOT",
            "spurs": "TOT",
            "tot": "TOT",
            "chelsea": "CHE",
            "chelsea fc": "CHE",
            "che": "CHE",
            "newcastle": "NEW",
            "newcastle united": "NEW",
            "new": "NEW",
            "manchester united": "MUN",
            "man united": "MUN",
            "man utd": "MUN",
            "mun": "MUN",
            "west ham": "WHU",
            "west ham united": "WHU",
            "whu": "WHU",
            "brighton": "BHA",
            "brighton & hove albion": "BHA",
            "brighton and hove albion": "BHA",
            "bha": "BHA",
            "bournemouth": "BOU",
            "afc bournemouth": "BOU",
            "bou": "BOU",
            "fulham": "FUL",
            "fulham fc": "FUL",
            "ful": "FUL",
            "wolves": "WOL",
            "wolverhampton": "WOL",
            "wolverhampton wanderers": "WOL",
            "wol": "WOL",
            "crystal palace": "CRY",
            "palace": "CRY",
            "cry": "CRY",
            "brentford": "BRE",
            "bre": "BRE",
            "everton": "EVE",
            "eve": "EVE",
            "nottingham forest": "NFO",
            "nottingham": "NFO",
            "nottm forest": "NFO",
            "nfo": "NFO",
            "ipswich": "IPS",
            "ipswich town": "IPS",
            "ips": "IPS",
            "leicester": "LEI",
            "leicester city": "LEI",
            "lei": "LEI",
            "southampton": "SOU",
            "southampton fc": "SOU",
            "sou": "SOU",

            # LaLiga Hypermotion (2a Divisió)
            "zaragoza": "ZAR",
            "real zaragoza": "ZAR",
            "zar": "ZAR",
            "oviedo": "ROV",
            "real oviedo": "ROV",
            "rov": "ROV",
            "sporting de gijon": "SPG",
            "sporting gijon": "SPG",
            "sporting": "SPG",
            "spg": "SPG",
            "eibar": "EIB",
            "sd eibar": "EIB",
            "eib": "EIB",
            "castellon": "CAS",
            "cd castellon": "CAS",
            "cas": "CAS",
            "burgos": "BUR",
            "burgos cf": "BUR",
            "bur": "BUR",
            "albacete": "ALB",
            "albacete balompie": "ALB",
            "alb": "ALB",
            "huesca": "HUE",
            "sd huesca": "HUE",
            "hue": "HUE",
            "granada": "GRA",
            "granada cf": "GRA",
            "gra": "GRA",
            "almeria": "ALM",
            "ud almeria": "ALM",
            "alm": "ALM",
            "cadiz": "CAD",
            "cadiz cf": "CAD",
            "cad": "CAD",
            "cordoba": "COR",
            "cordoba cf": "COR",
            "cor": "COR",
            "mirandes": "MIR",
            "cd mirandes": "MIR",
            "mir": "MIR",
            "eldense": "ELD",
            "cd eldense": "ELD",
            "eld": "ELD",
            "racing de ferrol": "RFE",
            "racing ferrol": "RFE",
            "r. ferrol": "RFE",
            "rfe": "RFE",
            "tenerife": "TEN",
            "cd tenerife": "TEN",
            "ten": "TEN",
            "cartagena": "CAR",
            "fc cartagena": "CAR",
            "car": "CAR",
        }

        if q_norm in exact_mappings:
            return exact_mappings[q_norm]

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, name, short_name, aliases FROM teams")
            rows = cursor.fetchall()

            # 1. Cerca exacta
            for row in rows:
                if q == row["id"].lower() or q == row["name"].lower() or (row["short_name"] and q == row["short_name"].lower()):
                    return row["id"]

            # 2. Cerca per àlies exactes
            for row in rows:
                if row["aliases"]:
                    aliases = [a.strip().lower() for a in row["aliases"].split(",")]
                    if q in aliases or q_norm in [a.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u") for a in aliases]:
                        return row["id"]

        return None

    def get_or_create_team(self, team_name: str, default_id: str = None) -> str:
        """Retorna l'ID de l'equip existent o el crea automàticament si és un nou club."""
        existing_id = self.find_team_id(team_name)
        if existing_id:
            return existing_id

        t_id = default_id or "".join([w[0] for w in team_name.split() if w.isalnum()])[:3].upper()
        if len(t_id) < 3:
            t_id = (team_name[:3]).upper()

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR IGNORE INTO teams (id, name, short_name, aliases) VALUES (?, ?, ?, ?)",
                (t_id, team_name, team_name, team_name)
            )
            cursor.execute(
                "INSERT OR IGNORE INTO team_ratings (team_id, general_rank, off_rank, def_rank, elo_rating, rest_days, updated_at) VALUES (?, 14.0, 14.0, 14.0, 1500.0, 7.0, datetime('now'))",
                (t_id,)
            )
            conn.commit()
        return t_id

    def find_referee_id(self, query_name: str) -> str:
        """
        Troba l'ID de l'àrbitre amb resolució avançada per a formats de Flashscore
        (ex: 'Manzano J.', 'Sesma M.', 'Diaz I.', 'Garcia V.', 'Maeso F.', 'Cuadra G.', 'Munuera J.').
        """
        if not query_name:
            return "REF_DEFAULT"
            
        def _norm(s: str) -> str:
            return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")

        q_clean = query_name.strip()
        q_norm = _norm(q_clean)

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, name FROM referees WHERE id != 'REF_DEFAULT'")
            all_refs = [dict(r) for r in cursor.fetchall()]

        # 1. Coincidència directa per ID
        for ref in all_refs:
            if q_norm == _norm(ref["id"]):
                return ref["id"]

        # 2. Resolució per format Flashscore: 'Cognom Inicial.' (ex: 'Manzano J.', 'Munuera J.', 'Cuadra G.')
        parts = q_norm.replace(".", "").split()
        if len(parts) >= 2 and len(parts[-1]) == 1:
            initial = parts[-1]
            fs_surname = parts[0]
            
            # Prioritat 2a: Coincidència de primer cognom i inicial
            for ref in all_refs:
                fn_words = _norm(ref["name"]).split()
                first_name = fn_words[0]
                if first_name.startswith(initial):
                    # Si té 3 paraules (Nom, 1r Cognom, 2n Cognom), el 1r cognom és words[1]
                    if len(fn_words) >= 3 and fs_surname == fn_words[-2]:
                        return ref["id"]
                    # Si té 4 paraules com 'José Luis Munuera Montero', 1r cognom és words[2]
                    if len(fn_words) >= 4 and fs_surname == fn_words[2]:
                        return ref["id"]

            # Prioritat 2b: Qualsevol dels cognoms de l'àrbitre que coincideixi amb la inicial
            for ref in all_refs:
                fn_words = _norm(ref["name"]).split()
                first_name = fn_words[0]
                if first_name.startswith(initial) and any(fs_surname == w for w in fn_words[1:]):
                    return ref["id"]

        # 3. Cerca per subcadena o coincidència de nom complet
        for ref in all_refs:
            ref_name_norm = _norm(ref["name"])
            if q_norm == ref_name_norm or q_norm in ref_name_norm or ref_name_norm in q_norm:
                return ref["id"]

        return "REF_DEFAULT"

    def get_or_create_referee(self, query_name: str, competition_id: str = "LALIGA") -> str:
        """
        Retorna l'ID de l'àrbitre si existeix, o en crea un de nou autònomament
        amb les mitjanes estadístiques de targetes de la seva lliga.
        """
        ref_id = self.find_referee_id(query_name)
        if ref_id and ref_id != "REF_DEFAULT":
            return ref_id
        if not query_name or query_name.upper() == "REF_DEFAULT":
            return "REF_DEFAULT"

        clean = query_name.strip()
        comp = competition_id.upper()
        if comp == "PREMIER":
            y_avg, r_avg, f_avg, strict = 3.80, 0.12, 21.0, 1.00
        elif comp == "HYPERMOTION":
            y_avg, r_avg, f_avg, strict = 5.20, 0.30, 26.5, 1.15
        else:
            y_avg, r_avg, f_avg, strict = 4.80, 0.24, 25.5, 1.08

        norm_parts = re.sub(r'[^a-zA-Z0-9]+', '_', clean.upper()).strip('_')
        new_id = f"REF_{norm_parts}"[:30]

        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute("""
                INSERT OR IGNORE INTO referees
                (id, name, matches_count, yellow_cards_avg, red_cards_avg, fouls_avg, penalties_avg, home_win_pct, strictness_index, updated_at)
                VALUES (?, ?, 25, ?, ?, ?, 0.28, 0.45, ?, datetime('now'))
            """, (new_id, clean, y_avg, r_avg, f_avg, strict))
            conn.commit()
        return new_id

    def get_referee(self, ref_id: str) -> Dict[str, Any]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM referees WHERE id = ?", (ref_id,))
            row = cursor.fetchone()
            if row:
                return dict(row)
            cursor.execute("SELECT * FROM referees WHERE id = 'REF_DEFAULT'")
            return dict(cursor.fetchone())

    def get_team_rating(self, team_id: str) -> Dict[str, Any]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT t.id, t.name, t.short_name, t.stadium, 
                          r.general_rank, r.off_rank, r.def_rank, r.elo_rating, 
                          r.rest_days, r.avg_cards_for_5, r.avg_corners_for_5, r.avg_corners_against_5
                   FROM teams t
                   LEFT JOIN team_ratings r ON t.id = r.team_id
                   WHERE t.id = ?""",
                (team_id,)
            )
            row = cursor.fetchone()
            if row:
                return dict(row)
            return {
                "id": team_id,
                "name": team_id,
                "general_rank": 14.0,
                "off_rank": 14.0,
                "def_rank": 14.0,
                "elo_rating": 1500.0,
                "rest_days": 7.0,
                "avg_cards_for_5": 2.2,
                "avg_corners_for_5": 4.8,
                "avg_corners_against_5": 4.8,
            }

    def get_league_standings_elo(self, competition_id: str) -> List[Dict[str, Any]]:
        """
        Retorna la classificació oficial de la competició ordenada per Elo Rating descendent.
        """
        comp = competition_id.upper()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            query = """
                SELECT t.id, t.name, t.short_name, t.stadium, 
                       r.elo_rating, r.general_rank, r.off_rank, r.def_rank,
                       r.avg_cards_for_5, r.avg_corners_for_5
                FROM teams t
                JOIN team_ratings r ON t.id = r.team_id
                WHERE t.id IN (
                    SELECT home_team_id FROM matches WHERE competition_id = ?
                    UNION
                    SELECT away_team_id FROM matches WHERE competition_id = ?
                )
                ORDER BY r.elo_rating DESC
            """
            cursor.execute(query, (comp, comp))
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    def save_match(self, match_data: Dict[str, Any]):
        """Desa o actualitza un partit a la base de dades."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT OR REPLACE INTO matches (
                    id, competition_id, season, jornada, date_time,
                    home_team_id, away_team_id, referee_id,
                    home_goals, away_goals, home_xg, away_xg,
                    home_corners, away_corners, home_yellow_cards, away_yellow_cards,
                    home_red_cards, away_red_cards, home_fouls, away_fouls,
                    status, url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    match_data["id"],
                    match_data.get("competition_id", "LALIGA"),
                    match_data.get("season", "2026-2027"),
                    match_data.get("jornada"),
                    match_data["date_time"],
                    match_data["home_team_id"],
                    match_data["away_team_id"],
                    match_data.get("referee_id", "REF_DEFAULT"),
                    match_data.get("home_goals"),
                    match_data.get("away_goals"),
                    match_data.get("home_xg"),
                    match_data.get("away_xg"),
                    match_data.get("home_corners"),
                    match_data.get("away_corners"),
                    match_data.get("home_yellow_cards"),
                    match_data.get("away_yellow_cards"),
                    match_data.get("home_red_cards"),
                    match_data.get("away_red_cards"),
                    match_data.get("home_fouls"),
                    match_data.get("away_fouls"),
                    match_data.get("status", "FINISHED"),
                    match_data.get("url"),
                )
            )
            conn.commit()

    # -------------------------------------------------------------------------
    # GESTIÓ D'APOSTES COMBINADES I SEGUIMENT ('QUÈ HAGUÉS PASSAT SI...')
    # -------------------------------------------------------------------------

    def save_combo_recommendation(
        self,
        competition_id: str,
        season: str,
        jornada: int,
        profile: str,
        stake: float,
        combo_summary: Dict[str, Any]
    ) -> str:
        """Desa una aposta combinada recomanada i les seves seleccions individuals."""
        legs = combo_summary.get("legs", [])
        if not legs:
            return ""

        import re
        safe_prof = re.sub(r'[^A-Za-z0-9_]', '_', profile).strip('_').upper()
        combo_id = f"COMBO_{season}_{competition_id}_J{jornada}_{safe_prof}"
        combined_odd = float(combo_summary.get("combined_odd", 1.0))
        prob_pct = float(combo_summary.get("combined_prob_pct", 0.0))
        fair_odd = float(combo_summary.get("fair_odd", 1.0))
        ev_pct = float(combo_summary.get("ev_pct", 0.0))
        wmx_url = combo_summary.get("winamax_url", "")

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO combo_recommendations
                (id, competition_id, season, jornada, profile, stake, combined_odd, combined_prob_pct, fair_odd, ev_pct, status, winamax_url, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, datetime('now'))
            """, (combo_id, competition_id, season, jornada, profile, stake, combined_odd, prob_pct, fair_odd, ev_pct, wmx_url))

            # Esborrar legs anteriors si ja existien per a evitar duplicats
            cursor.execute("DELETE FROM combo_legs WHERE combo_id = ?", (combo_id,))

            for leg in legs:
                matchup = leg.get("matchup", "")
                parts = matchup.split(" vs ", 1)
                h_id = self.find_team_id(parts[0]) if len(parts) == 2 else None
                a_id = self.find_team_id(parts[1]) if len(parts) == 2 else None

                cursor.execute("""
                    INSERT INTO combo_legs
                    (combo_id, matchup, selection_name, category, bookie_odd, model_prob, match_date, url, home_team_id, away_team_id, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')
                """, (
                    combo_id,
                    matchup,
                    leg.get("name", ""),
                    leg.get("category", ""),
                    float(leg.get("bookie_odd", 1.0)),
                    float(leg.get("model_prob", 0.0)),
                    leg.get("date", ""),
                    leg.get("url", ""),
                    h_id,
                    a_id
                ))

            conn.commit()
        return combo_id

    def get_pending_combos(self) -> List[Dict[str, Any]]:
        """Retorna totes les combinades pendents d'avaluació."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM combo_recommendations WHERE status = 'PENDING' ORDER BY jornada ASC")
            combos = [dict(r) for r in cursor.fetchall()]
            for c in combos:
                cursor.execute("SELECT * FROM combo_legs WHERE combo_id = ?", (c["id"],))
                c["legs"] = [dict(l) for l in cursor.fetchall()]
        return combos

    def update_combo_evaluation(
        self,
        combo_id: str,
        status: str,
        payout: float,
        profit: float,
        legs_evaluation: List[Dict[str, Any]]
    ):
        """Actualitza l'estat d'una combinada a WON o LOST i avalua cada selecció."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE combo_recommendations
                SET status = ?, payout = ?, profit = ?, evaluated_at = datetime('now')
                WHERE id = ?
            """, (status, payout, profit, combo_id))

            for leg in legs_evaluation:
                cursor.execute("""
                    UPDATE combo_legs
                    SET status = ?, actual_result = ?, evaluated_at = datetime('now')
                    WHERE id = ?
                """, (leg.get("status"), leg.get("actual_result"), leg.get("id")))

            conn.commit()

    def get_bankroll_history(self, competition_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Calcula el resum global 'Què hagués passat si...'
        Inversió: 25€ a la Segura (SAFE) i 5€ a la Difícil (RISKY).
        """
        query = "SELECT * FROM combo_recommendations"
        params = []
        if competition_id:
            query += " WHERE competition_id = ?"
            params.append(competition_id)
        query += " ORDER BY jornada ASC, profile ASC"

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            combos = [dict(r) for r in cursor.fetchall()]

            for c in combos:
                cursor.execute("SELECT * FROM combo_legs WHERE combo_id = ?", (c["id"],))
                c["legs"] = [dict(l) for l in cursor.fetchall()]

        total_stake = 0.0
        total_payout = 0.0
        safe_count = 0
        safe_won = 0
        semi_count = 0
        semi_won = 0
        risky_count = 0
        risky_won = 0

        for c in combos:
            if c["status"] in ["WON", "LOST"]:
                total_stake += c["stake"]
                total_payout += c["payout"]
                prof = c["profile"].upper()
                if "SAFE" in prof or "SEGURA" in prof:
                    safe_count += 1
                    if c["status"] == "WON":
                        safe_won += 1
                elif "SEMI" in prof:
                    semi_count += 1
                    if c["status"] == "WON":
                        semi_won += 1
                elif "RISKY" in prof or "ARRISCADA" in prof:
                    risky_count += 1
                    if c["status"] == "WON":
                        risky_won += 1

        net_profit = total_payout - total_stake
        roi_pct = round((net_profit / total_stake * 100.0), 2) if total_stake > 0 else 0.0

        return {
            "total_combos": len(combos),
            "evaluated_combos": safe_count + semi_count + risky_count,
            "total_stake": round(total_stake, 2),
            "total_payout": round(total_payout, 2),
            "net_profit": round(net_profit, 2),
            "roi_pct": roi_pct,
            "safe": {
                "total": safe_count,
                "won": safe_won,
                "hit_rate_pct": round(safe_won / safe_count * 100.0, 1) if safe_count > 0 else 0.0
            },
            "semi": {
                "total": semi_count,
                "won": semi_won,
                "hit_rate_pct": round(semi_won / semi_count * 100.0, 1) if semi_count > 0 else 0.0
            },
            "risky": {
                "total": risky_count,
                "won": risky_won,
                "hit_rate_pct": round(risky_won / risky_count * 100.0, 1) if risky_count > 0 else 0.0
            },
            "combos": combos
        }

    def get_last_round_evaluated_combos(self, jornada: Optional[int] = None, competition_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Recupera les combinades de la jornada indicada (o la més recent avaluada)."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if jornada is not None:
                q = "SELECT * FROM combo_recommendations WHERE jornada = ?"
                p = [jornada]
                if competition_id:
                    q += " AND competition_id = ?"
                    p.append(competition_id)
                q += " ORDER BY profile ASC"
                cursor.execute(q, p)
            else:
                q = "SELECT * FROM combo_recommendations WHERE status IN ('WON', 'LOST')"
                p = []
                if competition_id:
                    q += " AND competition_id = ?"
                    p.append(competition_id)
                q += " ORDER BY jornada DESC LIMIT 2"
                cursor.execute(q, p)

            combos = [dict(r) for r in cursor.fetchall()]
            for c in combos:
                cursor.execute("SELECT * FROM combo_legs WHERE combo_id = ?", (c["id"],))
                c["legs"] = [dict(l) for l in cursor.fetchall()]
        return combos

