"""
engine/pdf_report_generator.py
==============================
Generador d'informes PDF elegants, nets i professionals per a les prediccions de LaLiga.

Característiques:
- Disseny visual d'alta qualitat (paleta de colors acurada, targetes, taules modernes).
- Tipografia Arial TTF de sistema amb suport complet de caràcters UTF-8 (accents catalans/castellans).
- Informes de Jornada Completa: Separa clarament partits jugats i partits predits,
  indica l'àrbitre oficial designat o 'Pendent CTA (Àrbitre Mitjà)', i destaca les millors apostes +EV%.
- Sense talls arbitraris de text ni parèntesis oberts: neteja intel·ligent dels noms de mercat.
"""

import os
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional
from fpdf import FPDF
from database.db_manager import DatabaseManager

REPORTS_DIR = Path(__file__).parent.parent / "reports"

def clean_market_name(name: str) -> str:
    """
    Neteja i compacta el nom del mercat per evitar truncaments i parèntesis oberts.
    Ex: 'Ambdós Equips Marquen (BTTS No)' -> 'Ambdós Marquen: No'
        'Menys de 2.5 Gols (Under 2.5)'   -> 'Menys de 2.5 Gols'
        '1 - Victòria Local (Getafe CF)'   -> '1 - Victòria Local'
    """
    if not name:
        return ""
    text = name.strip()
    # 1. Eliminar etiquetes redundants en anglès entre parèntesis
    text = re.sub(r'\s*\((?:Under|Over)\s*[\d\.]+\)', '', text, flags=re.IGNORECASE)
    text = re.sub(r'Ambdós Equips Marquen \(BTTS ([^\)]+)\)', r'Ambdós Marquen: \1', text)
    text = re.sub(r'\(BTTS\s*[^\)]*\)', '', text, flags=re.IGNORECASE)
    # 2. Netejar redundàncies d'equips entre parèntesis
    text = re.sub(r'\s*\([A-Za-z\s\.-]+(?:CF|FC|CD|UD|SAD)\)', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s*\([^\)]*Local[^\)]*\)', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s*\([^\)]*Visitant[^\)]*\)', '', text, flags=re.IGNORECASE)
    # 3. Assegurar que qualsevol parèntesi obert quedi tancat
    if "(" in text and ")" not in text[text.rfind("("):]:
        text += ")"
    return text.strip()

def compact_matchup(matchup: str) -> str:
    """Acorta els noms dels equips de forma neta perquè càpiguen a la perfecció."""
    if " vs " in matchup:
        parts = matchup.split(" vs ", 1)
        def short_team(t):
            t = re.sub(r'^(?:RC|RCD|CA|UD|CF|FC|CD)\s+', '', t)
            t = re.sub(r'\s+(?:CF|FC|CD|UD|SAD)$', '', t)
            t = t.replace("Deportivo de La Coruña", "Deportivo")
            t = t.replace("Deportivo de A Coruña", "Deportivo")
            t = t.replace("Racing de Santander", "Racing")
            t = t.replace("Atlético de Madrid", "At. Madrid")
            t = t.replace("Celta de Vigo", "Celta")
            t = t.replace("Rayo Vallecano", "Rayo")
            t = t.replace("Athletic Club", "Athletic")
            t = t.replace("Real Sociedad", "R. Sociedad")
            t = t.replace("Real Madrid", "R. Madrid")
            return t.strip()
        h = short_team(parts[0])
        a = short_team(parts[1])
        return f"{h} vs {a}"
    return matchup

class BaseLaLigaPDF(FPDF):
    """Classe base FPDF configurada amb tipografia Arial UTF-8 i capçalera/peu oficial."""
    def __init__(self, title_text: str = "LALIGA EA SPORTS"):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.doc_title = title_text
        self.set_margins(left=14, top=14, right=14)
        self.set_auto_page_break(auto=True, margin=15)

        # Càrrega de fonts TTF del sistema Windows
        font_dir = Path("C:/Windows/Fonts")
        arial = str(font_dir / "arial.ttf")
        arial_bd = str(font_dir / "arialbd.ttf")
        arial_it = str(font_dir / "ariali.ttf")

        if os.path.exists(arial):
            self.add_font("Arial", "", arial)
            self.add_font("Arial", "B", arial_bd if os.path.exists(arial_bd) else arial)
            self.add_font("Arial", "I", arial_it if os.path.exists(arial_it) else arial)
            self.main_font = "Arial"
        else:
            self.main_font = "Helvetica"

    def header(self):
        # Franja superior estilitzada
        self.set_fill_color(15, 23, 42) # Slate 900
        self.rect(0, 0, 210, 10, "F")
        self.set_xy(14, 2)
        self.set_font(self.main_font, "B", 8)
        self.set_text_color(226, 232, 240)
        self.cell(85, 6, f"{self.doc_title.upper()} | PREDICCIONS AVANÇADES", align="L")
        self.set_xy(100, 2)
        self.set_text_color(148, 163, 184)
        now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
        self.cell(96, 6, f"Creat per Fortià Arumí Casals  |  Data informe: {now_str}", align="R")
        self.ln(12)

    def footer(self):
        self.set_y(-12)
        self.set_font(self.main_font, "", 8)
        self.set_text_color(148, 163, 184)
        self.cell(0, 6, f"Pàgina {self.page_no()}/{{nb}} | Document oficial generat automàticament pel Model Poisson GLM + Winamax", align="C")

    # Helpers de disseny
    def section_title(self, title: str, subtitle: str = None, color=(30, 58, 138)):
        self.ln(2)
        self.set_font(self.main_font, "B", 13)
        self.set_text_color(*color)
        self.cell(0, 7, title, new_x="LMARGIN", new_y="NEXT")
        if subtitle:
            self.set_font(self.main_font, "I", 8.5)
            self.set_text_color(100, 116, 139)
            self.cell(0, 5, subtitle, new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(203, 213, 225)
        self.set_line_width(0.4)
        self.line(14, self.get_y() + 1, 196, self.get_y() + 1)
        self.ln(3)

class PDFReportGenerator:
    def __init__(self, output_dir: Path = None):
        self.output_dir = output_dir if output_dir else REPORTS_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_jornada_report(
        self,
        jornada: int,
        played_matches: List[Dict[str, Any]],
        predicted_matches: List[Dict[str, Any]],
        combo_bets: Optional[Dict[str, Any]] = None,
        competition_id: str = "LALIGA"
    ) -> Path:
        """
        Genera l'informe complet de la jornada per a la lliga indicada:
        - Títol: PARTITS JORNADA {jornada} ({competition_id})
        - Partits ja jugats (resultats reals + xG + targetes + córners)
        - Apostes combinades recomanades (Segura cuota 2-3 i Arriscada cuota >= 30)
        - Millors apostes de valor (+EV%) de tota la jornada
        - Predicció dels partits que queden per disputar amb l'àrbitre trobat o genèric
        """
        comp_id = competition_id.upper()
        comp_names = {
            "LALIGA": "LaLiga EA Sports",
            "PREMIER": "Premier League",
            "HYPERMOTION": "LaLiga Hypermotion",
            "CHAMPIONSHIP": "EFL Championship"
        }
        comp_name = comp_names.get(comp_id, comp_id)

        pdf = BaseLaLigaPDF(f"{comp_name} - Jornada {jornada}")
        pdf.alias_nb_pages()
        pdf.add_page()
        font = pdf.main_font

        # -------------------------------------------------------------
        # CAPÇALERA PRINCIPAL DEL DOCUMENT
        # -------------------------------------------------------------
        pdf.set_fill_color(241, 245, 249)
        pdf.set_draw_color(203, 213, 225)
        pdf.set_line_width(0.5)
        pdf.rect(14, 15, 182, 24, "FD")

        pdf.set_xy(18, 17)
        pdf.set_font(font, "B", 18)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(100, 10, f"PARTITS JORNADA {jornada}", align="L")

        pdf.set_xy(18, 27)
        pdf.set_font(font, "", 9.5)
        pdf.set_text_color(71, 85, 105)
        n_played = len(played_matches)
        n_pred = len(predicted_matches)
        pdf.cell(100, 7, f"{comp_name} 2026/2027 | {n_played} partit jugat · {n_pred} partits pendents de predir", align="L")

        # Badge d'estat
        pdf.set_xy(145, 20)
        pdf.set_fill_color(37, 99, 235)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font(font, "B", 9)
        pdf.cell(45, 8, f"{comp_id} OFICIAL", align="C", fill=True)
        pdf.set_y(44)

        # -------------------------------------------------------------
        # SECCIÓ 1: PARTITS JA JUGATS
        # -------------------------------------------------------------
        if played_matches:
            pdf.section_title(
                f"1. PARTITS JA JUGATS DE LA JORNADA {jornada} ({len(played_matches)})",
                "Resultats oficials confirmats i mètriques reals extretes per l'scrapper"
            )

            for m in played_matches:
                pdf.set_fill_color(255, 255, 255)
                pdf.set_draw_color(226, 232, 240)
                y_start = pdf.get_y()
                pdf.rect(14, y_start, 182, 24, "FD")

                # Marcador i equips
                pdf.set_xy(18, y_start + 2)
                pdf.set_font(font, "B", 12)
                pdf.set_text_color(15, 23, 42)
                score_str = f"{m.get('home_goals', 0)} - {m.get('away_goals', 0)}"
                pdf.cell(90, 7, f"{m['home']}  {score_str}  {m['away']}", align="L")

                pdf.set_xy(110, y_start + 2)
                pdf.set_font(font, "I", 9)
                pdf.set_text_color(100, 116, 139)
                pdf.cell(80, 7, f"Data: {m.get('date', 'Finalitzat')}", align="R")

                # Estadístiques reals
                pdf.set_xy(18, y_start + 9)
                pdf.set_font(font, "", 8.5)
                pdf.set_text_color(51, 65, 85)

                xg_h = f"{m.get('home_xg', 0.0):.2f}" if m.get('home_xg') is not None else "N/A"
                xg_a = f"{m.get('away_xg', 0.0):.2f}" if m.get('away_xg') is not None else "N/A"
                c_h = m.get('home_corners', '-')
                c_a = m.get('away_corners', '-')
                y_h = m.get('home_yellow_cards', '-')
                y_a = m.get('away_yellow_cards', '-')

                stats_text = f"xG (Gols Esperats): {xg_h} vs {xg_a}   |   Córners: {c_h}-{c_a}   |   Targetes Grogues: {y_h}-{y_a}"
                pdf.cell(174, 6, stats_text, align="L")

                # Àrbitre del partit
                pdf.set_xy(18, y_start + 16)
                pdf.set_font(font, "B", 8)
                ref_name = m.get('referee_name', m.get('referee', 'Oficial CTA'))
                pdf.set_text_color(16, 185, 129)
                pdf.cell(174, 5, f"Àrbitre: {ref_name} (Col·legi CTA)", align="L")

                pdf.set_y(y_start + 28)
        else:
            pdf.section_title(
                f"1. ESTAT DE LA JORNADA {jornada}",
                "Jornada íntegrament per disputar"
            )
            pdf.set_fill_color(248, 250, 252)
            pdf.set_draw_color(226, 232, 240)
            y_info = pdf.get_y()
            pdf.rect(14, y_info, 182, 11, "FD")
            pdf.set_xy(18, y_info + 2.5)
            pdf.set_font(font, "I", 8.5)
            pdf.set_text_color(71, 85, 105)
            pdf.cell(174, 6, f"Tots els {len(predicted_matches)} partits de la Jornada {jornada} estan pendents de disputar-se.", align="L")
            pdf.set_y(y_info + 15)

        # -------------------------------------------------------------
        # SECCIÓ 2: APOSTES COMBINADES DESTACADES (SEGURA & CUOTA ALTA)
        # -------------------------------------------------------------
        if not combo_bets:
            from engine.combo_bet_engine import ComboBetEngine
            combo_engine = ComboBetEngine()
            combo_bets = combo_engine.analyze_jornada_combos(predicted_matches)

        safe_combo = combo_bets.get("safe_combo", {})
        risky_combo = combo_bets.get("risky_combo", {})

        if pdf.get_y() > 185:
            pdf.add_page()

        pdf.section_title(
            f"2. ANÀLISI D'APOSTES COMBINADES (ALTA PROBABILITAT & CUOTA >= 30)",
            "Càlcul matemàtic de probabilitats conjuntes per a esdeveniments independents a Winamax Espanya"
        )

        # 2.1 Combinada de Màxima Seguretat (Cuota 2 - 3)
        if safe_combo and safe_combo.get("legs"):
            if pdf.get_y() > 210:
                pdf.add_page()

            y_s = pdf.get_y()
            legs_s = safe_combo["legs"]
            card_h_s = 14 + 6.5 + (len(legs_s) * 5.5) + 6

            pdf.set_fill_color(240, 253, 244) # Emerald 50
            pdf.set_draw_color(16, 185, 129)  # Emerald 500
            pdf.rect(14, y_s, 182, card_h_s, "FD")

            pdf.set_xy(18, y_s + 1.5)
            pdf.set_font(font, "B", 9)
            pdf.set_text_color(6, 95, 70)
            pdf.cell(110, 5, "COMBINADA DE MÀXIMA SEGURETAT (CUOTA 2 - 3 | SELECCIONS >=90% MODEL)", align="L")

            pdf.set_xy(125, y_s + 1.5)
            pdf.set_font(font, "B", 8)
            pdf.set_text_color(5, 150, 105)
            pdf.cell(67, 5, f"Avantatge: +{safe_combo['ev_pct']:.1f}% EV", align="R")

            pdf.set_xy(18, y_s + 7)
            pdf.set_font(font, "", 8)
            pdf.set_text_color(51, 65, 85)
            metric_s_str = f"Cuota Winamax: {safe_combo['combined_odd']:.2f}   |   Probabilitat Model: {safe_combo['combined_prob_pct']:.1f}%   |   Cuota Justa: {safe_combo['fair_odd']:.2f}   |   Partits: {len(legs_s)}"
            pdf.cell(174, 5, metric_s_str, align="L")

            y_tbl_s = y_s + 13.5
            pdf.set_xy(16, y_tbl_s)
            pdf.set_fill_color(30, 41, 59)
            pdf.set_text_color(255, 255, 255)
            pdf.set_font(font, "B", 7.5)
            pdf.cell(46, 6, "PARTIT", fill=True)
            pdf.cell(50, 6, "SELECCIÓ", fill=True)
            pdf.cell(24, 6, "MERCAT", fill=True)
            pdf.cell(18, 6, "C. WMX", align="C", fill=True)
            pdf.cell(22, 6, "PROB. MODEL", align="C", fill=True)
            pdf.cell(18, 6, "ENLLAÇ", align="C", fill=True, new_x="LMARGIN", new_y="NEXT")

            pdf.set_font(font, "", 7.5)
            for idx, leg in enumerate(legs_s):
                bg = (248, 250, 252) if idx % 2 == 0 else (255, 255, 255)
                pdf.set_fill_color(*bg)
                pdf.set_text_color(15, 23, 42)
                pdf.set_x(16)
                pdf.cell(46, 5.5, compact_matchup(leg['matchup']), fill=True)
                pdf.cell(50, 5.5, clean_market_name(leg['name']), fill=True)
                pdf.cell(24, 5.5, str(leg.get('category', '-')), fill=True)
                pdf.cell(18, 5.5, f"{leg['bookie_odd']:.2f}", align="C", fill=True)
                pdf.set_text_color(5, 150, 105)
                pdf.set_font(font, "B", 7.5)
                pdf.cell(22, 5.5, f"{leg['model_prob']:.1f}%", align="C", fill=True)
                pdf.set_font(font, "U", 7.5)
                pdf.set_text_color(37, 99, 235)
                leg_url = leg.get('url', "https://www.winamax.es/apuestas-deportivas/sports/1/32")
                pdf.cell(18, 5.5, "Obrir", align="C", fill=True, link=leg_url, new_x="LMARGIN", new_y="NEXT")
                pdf.set_font(font, "", 7.5)

            pdf.set_x(18)
            pdf.set_font(font, "I", 7)
            pdf.set_text_color(100, 116, 139)
            pdf.cell(174, 5, "Per validar o compartir a Winamax: Afegeix les seleccions a la teva Cesta i prem 'Compartir' a la capçalera del cupó.", link="https://www.winamax.es/apuestas-deportivas/sports/1/32")
            pdf.set_y(y_s + card_h_s + 4)

        # 2.2 Combinada de Cuota Alta (Cuota >= 30.0)
        if risky_combo and risky_combo.get("legs"):
            if pdf.get_y() > 205:
                pdf.add_page()

            y_r = pdf.get_y()
            legs_r = risky_combo["legs"]
            card_h_r = 14 + 6.5 + (len(legs_r) * 5.5) + 6

            pdf.set_fill_color(245, 243, 255) # Indigo 50
            pdf.set_draw_color(99, 102, 241)  # Indigo 500
            pdf.rect(14, y_r, 182, card_h_r, "FD")

            pdf.set_xy(18, y_r + 1.5)
            pdf.set_font(font, "B", 9)
            pdf.set_text_color(67, 56, 202)
            pdf.cell(110, 5, "COMBINADA DE CUOTA ALTA (OBJECTIU CUOTA >= 30.0 | MULTIPLICADOR ARRISCAT)", align="L")

            pdf.set_xy(125, y_r + 1.5)
            pdf.set_font(font, "B", 8)
            pdf.set_text_color(79, 70, 229)
            pdf.cell(67, 5, f"Avantatge: +{risky_combo['ev_pct']:.1f}% EV", align="R")

            pdf.set_xy(18, y_r + 7)
            pdf.set_font(font, "", 8)
            pdf.set_text_color(51, 65, 85)
            metric_r_str = f"Cuota Winamax: {risky_combo['combined_odd']:.2f}   |   Probabilitat Model: {risky_combo['combined_prob_pct']:.1f}%   |   Cuota Justa: {risky_combo['fair_odd']:.2f}   |   Partits: {len(legs_r)}"
            pdf.cell(174, 5, metric_r_str, align="L")

            y_tbl_r = y_r + 13.5
            pdf.set_xy(16, y_tbl_r)
            pdf.set_fill_color(30, 41, 59)
            pdf.set_text_color(255, 255, 255)
            pdf.set_font(font, "B", 7.5)
            pdf.cell(46, 6, "PARTIT", fill=True)
            pdf.cell(50, 6, "SELECCIÓ", fill=True)
            pdf.cell(24, 6, "MERCAT", fill=True)
            pdf.cell(18, 6, "C. WMX", align="C", fill=True)
            pdf.cell(22, 6, "PROB. MODEL", align="C", fill=True)
            pdf.cell(18, 6, "ENLLAÇ", align="C", fill=True, new_x="LMARGIN", new_y="NEXT")

            pdf.set_font(font, "", 7.5)
            for idx, leg in enumerate(legs_r):
                bg = (248, 250, 252) if idx % 2 == 0 else (255, 255, 255)
                pdf.set_fill_color(*bg)
                pdf.set_text_color(15, 23, 42)
                pdf.set_x(16)
                pdf.cell(46, 5.5, compact_matchup(leg['matchup']), fill=True)
                pdf.cell(50, 5.5, clean_market_name(leg['name']), fill=True)
                pdf.cell(24, 5.5, str(leg.get('category', '-')), fill=True)
                pdf.cell(18, 5.5, f"{leg['bookie_odd']:.2f}", align="C", fill=True)
                pdf.set_text_color(79, 70, 229)
                pdf.set_font(font, "B", 7.5)
                pdf.cell(22, 5.5, f"{leg['model_prob']:.1f}%", align="C", fill=True)
                pdf.set_font(font, "U", 7.5)
                pdf.set_text_color(37, 99, 235)
                leg_url = leg.get('url', "https://www.winamax.es/apuestas-deportivas/sports/1/32")
                pdf.cell(18, 5.5, "Obrir", align="C", fill=True, link=leg_url, new_x="LMARGIN", new_y="NEXT")
                pdf.set_font(font, "", 7.5)

            pdf.set_x(18)
            pdf.set_font(font, "I", 7)
            pdf.set_text_color(100, 116, 139)
            pdf.cell(174, 5, "Per validar o compartir a Winamax: Afegeix les seleccions a la teva Cesta i prem 'Compartir' a la capçalera del cupó.", link="https://www.winamax.es/apuestas-deportivas/sports/1/32")
            pdf.set_y(y_r + card_h_r + 4)

        # -------------------------------------------------------------
        # SECCIÓ 3: MILLORS OPORTUNITATS D'APOSTA (+EV%) DE LA JORNADA
        # -------------------------------------------------------------
        all_value_bets = []
        for p in predicted_matches:
            if p.get("bet_analysis", {}).get("value_bets"):
                for vb in p["bet_analysis"]["value_bets"]:
                    vb_copy = dict(vb)
                    vb_copy["matchup"] = p["matchup"]
                    vb_copy["date"] = p.get("date", "")
                    all_value_bets.append(vb_copy)

        # Ordenar per EV% descendent
        all_value_bets.sort(key=lambda x: x.get("ev_pct", 0), reverse=True)

        if all_value_bets:
            if pdf.get_y() > 210:
                pdf.add_page()

            pdf.section_title(
                f"3. TOP APOSTES DE VALOR (+EV%) A WINAMAX ESPANYA",
                "Oportunitats on la probabilitat real del model supera el preu ofert per la casa d'apostes"
            )

            # Capçalera taula de 7 columnes (Ample total: 182 mm)
            pdf.set_fill_color(30, 41, 59)
            pdf.set_text_color(255, 255, 255)
            pdf.set_font(font, "B", 8)
            pdf.cell(46, 7, "PARTIT", fill=True)
            pdf.cell(48, 7, "SELECCIÓ D'APOSTA", fill=True)
            pdf.cell(20, 7, "MERCAT", fill=True)
            pdf.cell(18, 7, "C. WMX", align="C", fill=True)
            pdf.cell(18, 7, "C. JUSTA", align="C", fill=True)
            pdf.cell(18, 7, "AVANTATGE", align="C", fill=True)
            pdf.cell(14, 7, "KELLY", align="C", fill=True, new_x="LMARGIN", new_y="NEXT")

            pdf.set_font(font, "", 8)
            for idx, vb in enumerate(all_value_bets[:8]):
                bg = (248, 250, 252) if idx % 2 == 0 else (255, 255, 255)
                pdf.set_fill_color(*bg)
                pdf.set_text_color(15, 23, 42)

                # Noms nets i sense tallar parèntesis
                partit_str = compact_matchup(vb['matchup'])
                seleccio_str = clean_market_name(vb['name'])

                pdf.cell(46, 6, partit_str, fill=True)
                pdf.cell(48, 6, seleccio_str, fill=True)
                pdf.cell(20, 6, str(vb.get('category', 'Mercat')), fill=True)
                pdf.cell(18, 6, f"{vb['bookie_odd']:.2f}", align="C", fill=True)
                pdf.cell(18, 6, f"{vb['fair_odd']:.2f}", align="C", fill=True)

                pdf.set_text_color(5, 150, 105)
                pdf.set_font(font, "B", 8)
                pdf.cell(18, 6, f"+{vb['ev_pct']:.1f}%", align="C", fill=True)

                kelly_str = f"{vb['kelly_pct']:.1f}%" if vb.get('kelly_pct', 0) > 0 else "-"
                pdf.set_text_color(71, 85, 105)
                pdf.set_font(font, "", 8)
                pdf.cell(14, 6, kelly_str, align="C", fill=True, new_x="LMARGIN", new_y="NEXT")

            pdf.ln(4)

        # -------------------------------------------------------------
        # SECCIÓ 4: RÀNQUING I CLASSIFICACIÓ ELO DE LA LLIGA
        # -------------------------------------------------------------
        db = DatabaseManager()
        standings = db.get_league_standings_elo(comp_id)
        if standings:
            pdf.add_page()
            pdf.section_title(
                f"4. CLASSIFICACIÓ DE FORÇA ELO I RÀNQUINGS ({comp_name.upper()})",
                f"Puntuació oficial del model estadístic Poisson GLM actualitzada jornada a jornada · {len(standings)} equips"
            )

            # Taula d'Elos
            pdf.set_fill_color(30, 41, 59)
            pdf.set_text_color(255, 255, 255)
            pdf.set_font(font, "B", 8)
            pdf.cell(14, 7, "POS", align="C", fill=True)
            pdf.cell(64, 7, "EQUIP", fill=True)
            pdf.cell(26, 7, "ELO RATING", align="C", fill=True)
            pdf.cell(26, 7, "RÀNQ. GEN.", align="C", fill=True)
            pdf.cell(26, 7, "RÀNQ. ATAC", align="C", fill=True)
            pdf.cell(26, 7, "RÀNQ. DEF.", align="C", fill=True, new_x="LMARGIN", new_y="NEXT")

            for idx, st in enumerate(standings, start=1):
                bg = (248, 250, 252) if idx % 2 == 0 else (255, 255, 255)
                pdf.set_fill_color(*bg)

                is_top = idx <= 4
                if is_top:
                    pdf.set_font(font, "B", 8)
                    pdf.set_text_color(30, 58, 138)
                else:
                    pdf.set_font(font, "", 8)
                    pdf.set_text_color(15, 23, 42)

                pdf.cell(14, 5.5, f"#{idx}", align="C", fill=True)
                pdf.cell(64, 5.5, str(st.get("name", st.get("id"))), fill=True)

                pdf.set_font(font, "B", 8)
                pdf.set_text_color(15, 23, 42)
                pdf.cell(26, 5.5, f"{st.get('elo_rating', 1500.0):.1f}", align="C", fill=True)

                pdf.set_font(font, "", 8)
                pdf.set_text_color(71, 85, 105)
                pdf.cell(26, 5.5, f"#{st.get('general_rank', 10.0):.1f}", align="C", fill=True)
                pdf.cell(26, 5.5, f"#{st.get('off_rank', 10.0):.1f}", align="C", fill=True)
                pdf.cell(26, 5.5, f"#{st.get('def_rank', 10.0):.1f}", align="C", fill=True, new_x="LMARGIN", new_y="NEXT")

            pdf.ln(5)

        # -------------------------------------------------------------
        # SECCIÓ 5: PREDICCIÓ DETALLADA DELS PARTITS PENDENTS
        # -------------------------------------------------------------
        pdf.add_page()
        pdf.section_title(
            f"5. PREDICCIÓ DELS PARTITS PENDENTS ({len(predicted_matches)} PARTITS)",
            "Pronòstic probabilístic (Poisson GLM + xG), assignació arbitral i cuotes de mercat"
        )

        for p in predicted_matches:
            # Comprovar si cal salt de pàgina per evitar que la targeta quedi tallada
            if pdf.get_y() > 215:
                pdf.add_page()

            card_h = 48
            y0 = pdf.get_y()
            pdf.set_fill_color(255, 255, 255)
            pdf.set_draw_color(203, 213, 225)
            pdf.rect(14, y0, 182, card_h, "FD")

            # Capçalera targeta partit
            pdf.set_fill_color(241, 245, 249)
            pdf.rect(14, y0, 182, 8, "FD")

            pdf.set_xy(18, y0 + 1)
            pdf.set_font(font, "B", 10)
            pdf.set_text_color(15, 23, 42)
            pdf.cell(100, 6, p["matchup"], align="L")

            pdf.set_xy(118, y0 + 1)
            pdf.set_font(font, "I", 8)
            pdf.set_text_color(100, 116, 139)
            pdf.cell(74, 6, f"Data: {p.get('date', 'Pendent')}", align="R")

            # Fila 1: Probabilitats 1X2 i Veredicte (y0 + 9.5)
            pdf.set_xy(18, y0 + 9.5)
            pdf.set_font(font, "B", 8)
            pdf.set_text_color(30, 41, 59)
            pdf.cell(26, 5, "Pronòstic 1X2:", align="L")

            pdf.set_font(font, "", 8)
            p1 = p['goals']['prob_1X2']['1']
            px = p['goals']['prob_1X2']['X']
            p2 = p['goals']['prob_1X2']['2']
            p1x2_str = f"1: {p1:.1f}% (Q.J: {100.0/p1:.2f})  |  X: {px:.1f}% (Q.J: {100.0/px:.2f})  |  2: {p2:.1f}% (Q.J: {100.0/p2:.2f})"
            pdf.cell(96, 5, p1x2_str, align="L")

            pdf.set_font(font, "B", 8)
            pdf.set_text_color(37, 99, 235)
            pdf.cell(52, 5, f"Veredicte: {p['verdict_1x2']}", align="R")

            # Fila 2: Gols Esperats (xG), Over 2.5 i BTTS (y0 + 16.5)
            pdf.set_xy(18, y0 + 16.5)
            pdf.set_font(font, "B", 8)
            pdf.set_text_color(30, 41, 59)
            pdf.cell(26, 5, "Gols esperats:", align="L")

            pdf.set_font(font, "", 8)
            xg_h = p['goals']['expected_goals_home']
            xg_a = p['goals']['expected_goals_away']
            ov = p['goals']['over_under']['over_2_5']
            btts = p['goals']['btts']['yes']
            top_score = p['goals']['top_scorelines'][0]['score'] if p['goals']['top_scorelines'] else "1-0"
            gols_str = f"Local: {xg_h:.2f} xG  |  Visitant: {xg_a:.2f} xG  |  Més de 2.5: {ov:.1f}%  |  BTTS: {btts:.1f}%"
            pdf.cell(108, 5, gols_str, align="L")

            pdf.set_font(font, "I", 8)
            pdf.set_text_color(71, 85, 105)
            pdf.cell(40, 5, f"Marcador top: {top_score}", align="R")

            # Fila 3: Àrbitre i Disciplina (y0 + 23.5)
            pdf.set_xy(18, y0 + 23.5)
            pdf.set_font(font, "B", 8)
            pdf.set_text_color(30, 41, 59)
            pdf.cell(26, 5, "Àrbitre:", align="L")

            ref_info = p.get("referee_resolution", {})
            ref_name = ref_info.get("name") or p.get("referee", {}).get("name", "Standard")
            is_generic = ref_info.get("is_generic", False)

            if is_generic or "Standard" in ref_name or "Pendent" in ref_name:
                pdf.set_font(font, "I", 7.5)
                pdf.set_text_color(217, 119, 6) # Amber
                ref_display = "Pendent CTA / RFEF (No recomanable apostar a targetes)"
                cards_str = f"Targetes: Pendent CTA | Córners: {p['corners']['expected_total_corners']}"
            else:
                pdf.set_font(font, "B", 8)
                pdf.set_text_color(16, 185, 129) # Green
                ref_display = f"{ref_name} (Designació Oficial CTA)"
                cards_str = f"Targetes: {p['cards']['expected_total_cards']} | Córners: {p['corners']['expected_total_corners']}"

            pdf.cell(98, 5, ref_display, align="L")
            pdf.set_font(font, "", 8)
            pdf.set_text_color(71, 85, 105)
            pdf.cell(50, 5, cards_str, align="R")

            # Fila 4: Cuotes Winamax & Oportunitats d'aposta (+EV%) (y0 + 30.5)
            pdf.set_xy(18, y0 + 30.5)
            pdf.set_font(font, "B", 8)
            pdf.set_text_color(30, 41, 59)
            pdf.cell(26, 6.5, "Cuotes Winamax:", align="L")

            odds_1x2 = p.get("odds", {}).get("1X2") or p.get("odds", {}).get("odds_1x2", {})
            o1 = f"{odds_1x2.get('1'):.2f}" if isinstance(odds_1x2.get('1'), (int, float)) else str(odds_1x2.get('1', '-'))
            ox = f"{odds_1x2.get('X'):.2f}" if isinstance(odds_1x2.get('X'), (int, float)) else str(odds_1x2.get('X', '-'))
            o2 = f"{odds_1x2.get('2'):.2f}" if isinstance(odds_1x2.get('2'), (int, float)) else str(odds_1x2.get('2', '-'))
            odds_str = f"1: {o1}  |  X: {ox}  |  2: {o2}"

            pdf.set_font(font, "", 8)
            pdf.cell(44, 6.5, odds_str, align="L")

            # Valor detectat net i sense tallar parèntesis
            vbs = p.get("bet_analysis", {}).get("value_bets", [])
            if vbs:
                best_vb = vbs[0]
                clean_name = clean_market_name(best_vb['name'])
                vb_txt = f"[VALOR +EV] {clean_name} @ {best_vb['bookie_odd']:.2f} (+{best_vb['ev_pct']:.1f}% EV)"
                pdf.set_fill_color(236, 253, 245)
                pdf.set_text_color(5, 150, 105)
                pdf.set_font(font, "B", 7.5)
                pdf.cell(104, 6.5, vb_txt, align="R", fill=True)
            else:
                pdf.set_text_color(148, 163, 184)
                pdf.set_font(font, "I", 7.5)
                pdf.cell(104, 6.5, "Sense avantatge matemàtic clar (+EV > 0) a les cuotes obertes", align="R")

            # Fila 5: Enllaç directe (y0 + 39.5)
            pdf.set_xy(18, y0 + 39.5)
            pdf.set_font(font, "I", 7)
            pdf.set_text_color(37, 99, 235)
            winamax_url = p.get("odds", {}).get("url", "https://www.winamax.es/apuestas-deportivas")
            pdf.cell(174, 4.5, f"Enllaç Winamax en directe: {winamax_url}", align="L")

            pdf.set_y(y0 + card_h + 4)

        # -------------------------------------------------------------
        # GUIA RÀPIDA DE CONCEPTES
        # -------------------------------------------------------------
        if pdf.get_y() > 220:
            pdf.add_page()

        pdf.section_title("5. GUIA RÀPIDA PER APOSTAR AMB VALOR MATEMÀTIC (+EV%)")
        pdf.set_fill_color(248, 250, 252)
        pdf.set_draw_color(226, 232, 240)
        y_g = pdf.get_y()
        pdf.rect(14, y_g, 182, 28, "FD")

        pdf.set_xy(18, y_g + 2)
        pdf.set_font(font, "B", 8)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(0, 5, "Com interpretar les dades d'aquest informe:", new_x="LMARGIN", new_y="NEXT")

        pdf.set_font(font, "", 7.5)
        pdf.set_text_color(71, 85, 105)
        pdf.set_x(18)
        pdf.cell(0, 4.5, "• Cuota Winamax: El pagament de la casa per cada 1 euro apostat.", new_x="LMARGIN", new_y="NEXT")
        pdf.set_x(18)
        pdf.cell(0, 4.5, "• Cuota Justa Model: El preu matemàtic real segons la probabilitat calculada pel model Poisson GLM.", new_x="LMARGIN", new_y="NEXT")
        pdf.set_x(18)
        pdf.cell(0, 4.5, "• Avantatge (+EV %): El benefici esperat a llarg termini. Si és POSITIU (+), la casa paga més del compte.", new_x="LMARGIN", new_y="NEXT")
        pdf.set_x(18)
        pdf.cell(0, 4.5, "• Criteri Kelly: Percentatge màxim de la teva banca recomanat per optimitzar guanys i minimitzar el risc.", new_x="LMARGIN", new_y="NEXT")

        # Desa el fitxer
        out_filename = self.output_dir / f"informe_jornada_{jornada}_{comp_id.lower()}.pdf"
        pdf.output(str(out_filename))
        return out_filename

    def generate_match_report(self, match_pred: Dict[str, Any], odds: Dict[str, Any], bet_analysis: Dict[str, Any]) -> Path:
        """
        Genera l'informe PDF individual per a un partit analitzat.
        """
        matchup = match_pred.get("matchup", "Partit")
        date_str = match_pred.get("date", datetime.now().strftime("%Y-%m-%d"))
        pdf = BaseLaLigaPDF(f"Predicció: {matchup}")
        pdf.alias_nb_pages()
        pdf.add_page()
        font = pdf.main_font

        # Capçalera
        pdf.set_fill_color(241, 245, 249)
        pdf.set_draw_color(203, 213, 225)
        pdf.rect(14, 15, 182, 22, "FD")

        pdf.set_xy(18, 17)
        pdf.set_font(font, "B", 16)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(110, 8, matchup, align="L")

        pdf.set_xy(18, 26)
        pdf.set_font(font, "", 9)
        pdf.set_text_color(71, 85, 105)
        pdf.cell(110, 6, f"LaLiga EA Sports 2026/2027 · Data: {date_str}", align="L")

        pdf.set_xy(135, 18)
        pdf.set_fill_color(37, 99, 235)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font(font, "B", 8)
        pdf.cell(55, 7, "INFORME DE PARTIT", align="C", fill=True)

        pdf.set_xy(135, 27)
        pdf.set_font(font, "I", 7)
        pdf.set_text_color(37, 99, 235)
        winamax_link = odds.get("url", "https://www.winamax.es/apuestas-deportivas")
        pdf.cell(55, 5, "Enllaç Winamax disponible", align="C")

        pdf.set_y(41)

        # 1. Model Estadístic
        pdf.section_title("1. PRONÒSTIC DEL MODEL ESTADÍSTIC (Poisson GLM + xG)")
        p1 = match_pred['goals']['prob_1X2']['1']
        px = match_pred['goals']['prob_1X2']['X']
        p2 = match_pred['goals']['prob_1X2']['2']

        # Taula 1X2
        pdf.set_fill_color(30, 41, 59)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font(font, "B", 8)
        pdf.cell(60, 7, "SELECCIÓ 1X2", fill=True)
        pdf.cell(40, 7, "PROBABILITAT MODEL", align="C", fill=True)
        pdf.cell(40, 7, "CUOTA JUSTA TEÒRICA", align="C", fill=True)
        pdf.cell(42, 7, "VEREDICTE MODEL", align="C", fill=True, new_x="LMARGIN", new_y="NEXT")

        rows = [
            (f"1 (Victòria {match_pred['home_team']})", f"{p1:.1f}%", f"{100.0/p1:.2f}"),
            ("X (Empat)", f"{px:.1f}%", f"{100.0/px:.2f}"),
            (f"2 (Victòria {match_pred['away_team']})", f"{p2:.1f}%", f"{100.0/p2:.2f}"),
        ]

        pdf.set_font(font, "", 8)
        for idx, (sel, prob, cj) in enumerate(rows):
            bg = (248, 250, 252) if idx % 2 == 0 else (255, 255, 255)
            pdf.set_fill_color(*bg)
            pdf.set_text_color(15, 23, 42)
            pdf.cell(60, 6, sel, fill=True)
            pdf.cell(40, 6, prob, align="C", fill=True)
            pdf.cell(40, 6, cj, align="C", fill=True)
            if idx == 0:
                pdf.set_font(font, "B", 8)
                pdf.set_text_color(37, 99, 235)
                pdf.cell(42, 18, match_pred['verdict_1x2'][:24], align="C", fill=True, new_x="LMARGIN", new_y="NEXT")
                pdf.set_font(font, "", 8)
            else:
                pdf.set_x(154)
                pdf.ln(6)

        pdf.ln(2)

        # Mètriques de gols i disciplina en targeta de 2 columnes
        y_cards = pdf.get_y()
        pdf.set_fill_color(248, 250, 252)
        pdf.set_draw_color(226, 232, 240)
        pdf.rect(14, y_cards, 88, 26, "FD")
        pdf.rect(108, y_cards, 88, 26, "FD")

        # Columna esquerra: Gols
        pdf.set_xy(18, y_cards + 2)
        pdf.set_font(font, "B", 8.5)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(80, 5, "MÈTRIQUES DE GOLS (xG)", new_x="LMARGIN", new_y="NEXT")

        pdf.set_font(font, "", 8)
        pdf.set_text_color(71, 85, 105)
        pdf.set_x(18)
        pdf.cell(80, 4.5, f"• Gols esperats: {match_pred['goals']['expected_goals_home']} (Local) - {match_pred['goals']['expected_goals_away']} (Visitant)", new_x="LMARGIN", new_y="NEXT")
        pdf.set_x(18)
        pdf.cell(80, 4.5, f"• Més de 2.5 gols: {match_pred['goals']['over_under']['over_2_5']}% | Menys: {match_pred['goals']['over_under']['under_2_5']}%", new_x="LMARGIN", new_y="NEXT")
        pdf.set_x(18)
        pdf.cell(80, 4.5, f"• Ambdós Marquen (BTTS): {match_pred['goals']['btts']['yes']}% Sí | {match_pred['goals']['btts']['no']}% No", new_x="LMARGIN", new_y="NEXT")

        # Columna dreta: Disciplina i Córners
        pdf.set_xy(112, y_cards + 2)
        pdf.set_font(font, "B", 8.5)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(80, 5, "DISCIPLINA & CÓRNERS", new_x="LMARGIN", new_y="NEXT")

        pdf.set_font(font, "", 8)
        pdf.set_text_color(71, 85, 105)
        ref_n = match_pred.get("referee", {}).get("name", "Standard")
        pdf.set_x(112)
        pdf.cell(80, 4.5, f"• Àrbitre: {ref_n[:30]}", new_x="LMARGIN", new_y="NEXT")
        pdf.set_x(112)
        pdf.cell(80, 4.5, f"• Targetes totals estimades: {match_pred['cards']['expected_total_cards']} (Over 4.5: {match_pred['cards']['prob_over_cards']['over_4_5']}%)", new_x="LMARGIN", new_y="NEXT")
        pdf.set_x(112)
        pdf.cell(80, 4.5, f"• Córners estimats: {match_pred['corners']['expected_total_corners']} (Over 9.5: {match_pred['corners']['prob_over_corners']['over_9_5']}%)", new_x="LMARGIN", new_y="NEXT")

        pdf.set_y(y_cards + 30)

        # 2. Taula de Cuotes Winamax
        pdf.section_title("2. COMPARATIVA DE CUOTES REALS WINAMAX ESPANYA")
        markets = bet_analysis.get("single_markets", [])

        if markets:
            pdf.set_fill_color(30, 41, 59)
            pdf.set_text_color(255, 255, 255)
            pdf.set_font(font, "B", 8)
            pdf.cell(70, 6.5, "MERCAT / SELECCIÓ", fill=True)
            pdf.cell(28, 6.5, "CATEGORIA", fill=True)
            pdf.cell(28, 6.5, "CUOTA WINAMAX", align="C", fill=True)
            pdf.cell(28, 6.5, "CUOTA JUSTA", align="C", fill=True)
            pdf.cell(28, 6.5, "AVANTATGE (EV)", align="C", fill=True, new_x="LMARGIN", new_y="NEXT")

            pdf.set_font(font, "", 7.5)
            for idx, m in enumerate(markets[:14]):
                bg = (248, 250, 252) if idx % 2 == 0 else (255, 255, 255)
                pdf.set_fill_color(*bg)
                pdf.set_text_color(15, 23, 42)
                clean_m_name = clean_market_name(m['name'])
                pdf.cell(70, 5.5, clean_m_name, fill=True)
                pdf.cell(28, 5.5, str(m.get('category', '-')), fill=True)
                pdf.cell(28, 5.5, f"{m['bookie_odd']:.2f}", align="C", fill=True)
                pdf.cell(28, 5.5, f"{m['fair_odd']:.2f}", align="C", fill=True)

                ev = m['ev_pct']
                if ev > 0:
                    pdf.set_text_color(5, 150, 105)
                    pdf.set_font(font, "B", 7.5)
                    pdf.cell(28, 5.5, f"+{ev:.1f}%", align="C", fill=True, new_x="LMARGIN", new_y="NEXT")
                else:
                    pdf.set_text_color(225, 29, 72)
                    pdf.set_font(font, "", 7.5)
                    pdf.cell(28, 5.5, f"{ev:.1f}%", align="C", fill=True, new_x="LMARGIN", new_y="NEXT")
                pdf.set_font(font, "", 7.5)

        # 3. Value Bets (+EV%)
        value_bets = bet_analysis.get("value_bets", [])
        if value_bets:
            pdf.ln(2)
            pdf.section_title("3. LES MILLORS OPORTUNITATS D'APOSTA (+EV%)")
            for vb in value_bets[:4]:
                pdf.set_fill_color(236, 253, 245)
                pdf.set_draw_color(167, 243, 208)
                y_vb = pdf.get_y()
                pdf.rect(14, y_vb, 182, 12, "FD")

                pdf.set_xy(18, y_vb + 1.5)
                pdf.set_font(font, "B", 8.5)
                pdf.set_text_color(6, 95, 70)
                clean_rating = vb.get('rating', '').replace('🔥', '').replace('⭐', '').strip()
                clean_vb_name = clean_market_name(vb['name'])
                pdf.cell(100, 5, f"[{vb.get('category')}] {clean_vb_name}  ->  Cuota Winamax: {vb['bookie_odd']:.2f} (Cuota justa: {vb['fair_odd']:.2f})", align="L")

                pdf.set_xy(120, y_vb + 1.5)
                pdf.cell(72, 5, f"Avantatge: +{vb['ev_pct']:.1f}% EV · {clean_rating}", align="R")

                pdf.set_xy(18, y_vb + 6.5)
                pdf.set_font(font, "", 7.5)
                pdf.set_text_color(4, 120, 87)
                k_txt = f"Recomanació Kelly: Apostar com a màxim el {vb['kelly_pct']:.1f}% de la teva banca" if vb.get('kelly_pct', 0) > 0 else "Aposta de baix risc"
                pdf.cell(174, 4, k_txt, align="L")

                pdf.set_y(y_vb + 14)
        else:
            pdf.ln(2)
            pdf.set_font(font, "I", 8)
            pdf.set_text_color(100, 116, 139)
            pdf.cell(0, 6, "[INFO] No s'han trobat apostes amb valor esperat clar (+EV% > 0) a les cuotes obertes.", align="L")
            pdf.ln(4)

        # Desa el fitxer
        safe_name = f"{match_pred['home_team']}_vs_{match_pred['away_team']}".replace(" ", "_").replace(".", "")
        out_filename = self.output_dir / f"prediccio_{safe_name}.pdf"
        pdf.output(str(out_filename))
        return out_filename

    def generate_multileague_report(
        self,
        jornada: int,
        multileague_combos: Dict[str, Any],
        simulation_summary: Dict[str, Any],
        highlight_matches: List[Dict[str, Any]],
        competitions_data: Optional[Dict[str, Any]] = None
    ) -> Path:
        """
        Genera l'informe executiu VIP Multi-Lliga (Europa):
        - Tauler financer 'Què hagués passat si...' (25€ Segura, 5€ Difícil).
        - Mega-Combinada Multi-Lliga Segura (Cuota 2-3).
        - Mega-Combinada Multi-Lliga Arriscada (Cuota >= 30.0).
        - Radar de Valor dels partits estrella d'Europa.
        """
        pdf = BaseLaLigaPDF(f"Multi-Lliga Europa - Jornada {jornada}")
        pdf.alias_nb_pages()
        pdf.add_page()
        font = pdf.main_font

        # -------------------------------------------------------------
        # CAPÇALERA PRINCIPAL MULTI-LLIGA
        # -------------------------------------------------------------
        pdf.set_fill_color(15, 23, 42) # Slate 900
        pdf.set_draw_color(30, 41, 59)
        pdf.rect(14, 15, 182, 25, "F")

        pdf.set_xy(18, 17)
        pdf.set_font(font, "B", 16)
        pdf.set_text_color(255, 255, 255)
        pdf.cell(110, 8, f"MEGA-INFORME MULTI-LLIGA · JORNADA {jornada}", align="L")

        pdf.set_xy(18, 26)
        pdf.set_font(font, "", 8.5)
        pdf.set_text_color(148, 163, 184)
        pdf.cell(120, 6, "LaLiga EA Sports + Premier + Hypermotion + Championship | Winamax Espanya", align="L")

        # Badge VIP
        pdf.set_xy(145, 19)
        pdf.set_fill_color(99, 102, 241) # Indigo 500
        pdf.set_text_color(255, 255, 255)
        pdf.set_font(font, "B", 8.5)
        pdf.cell(45, 7.5, "VIP MULTI-COMPETICIÓ", align="C", fill=True)
        pdf.set_y(44)

        # -------------------------------------------------------------
        # SECCIÓ 1: TAULER FINANCER 'QUÈ HAGUÉS PASSAT SI...'
        # -------------------------------------------------------------
        pdf.section_title(
            "1. TAULER DE RENDIBILITAT 'QUÈ HAGUÉS PASSAT SI...'",
            "Simulador de bankroll acumulat: 25€ fixos a la Segura i 5€ fixos a la Difícil des del primer report"
        )

        tot_stake = simulation_summary.get("total_stake", 0.0)
        tot_payout = simulation_summary.get("total_payout", 0.0)
        net_profit = simulation_summary.get("net_profit", 0.0)
        roi_pct = simulation_summary.get("roi_pct", 0.0)
        safe_hit = simulation_summary.get("safe", {}).get("hit_rate_pct", 0.0)
        risky_hit = simulation_summary.get("risky", {}).get("hit_rate_pct", 0.0)

        # 4 Targetes Mètriques
        y_kpi = pdf.get_y()
        kw = 43.5
        gap = 2.6

        # Card 1: Inversió Total
        pdf.set_fill_color(248, 250, 252)
        pdf.set_draw_color(226, 232, 240)
        pdf.rect(14, y_kpi, kw, 16, "FD")
        pdf.set_xy(16, y_kpi + 1.5)
        pdf.set_font(font, "B", 7)
        pdf.set_text_color(100, 116, 139)
        pdf.cell(kw - 4, 4, "INVERSIÓ TOTAL", align="L")
        pdf.set_xy(16, y_kpi + 6.5)
        pdf.set_font(font, "B", 12)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(kw - 4, 6, f"{tot_stake:.2f} €", align="L")

        # Card 2: Retorn Brut
        x2 = 14 + kw + gap
        pdf.rect(x2, y_kpi, kw, 16, "FD")
        pdf.set_xy(x2 + 2, y_kpi + 1.5)
        pdf.set_font(font, "B", 7)
        pdf.set_text_color(100, 116, 139)
        pdf.cell(kw - 4, 4, "RETORN BRUT", align="L")
        pdf.set_xy(x2 + 2, y_kpi + 6.5)
        pdf.set_font(font, "B", 12)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(kw - 4, 6, f"{tot_payout:.2f} €", align="L")

        # Card 3: Benefici Net (PnL)
        x3 = x2 + kw + gap
        is_profit = net_profit >= 0
        card_bg = (240, 253, 244) if is_profit else (254, 242, 242)
        card_border = (16, 185, 129) if is_profit else (239, 68, 68)
        pdf.set_fill_color(*card_bg)
        pdf.set_draw_color(*card_border)
        pdf.rect(x3, y_kpi, kw, 16, "FD")
        pdf.set_xy(x3 + 2, y_kpi + 1.5)
        pdf.set_font(font, "B", 7)
        pdf.set_text_color(6, 95, 70 if is_profit else 153)
        pdf.cell(kw - 4, 4, "BENEFICI NET (PnL)", align="L")
        pdf.set_xy(x3 + 2, y_kpi + 6.5)
        pdf.set_font(font, "B", 12)
        pdf.set_text_color(5, 150, 105 if is_profit else 220)
        pdf.cell(kw - 4, 6, f"{net_profit:+.2f} €", align="L")

        # Card 4: ROI % i Encerts
        x4 = x3 + kw + gap
        pdf.set_fill_color(248, 250, 252)
        pdf.set_draw_color(226, 232, 240)
        pdf.rect(x4, y_kpi, kw, 16, "FD")
        pdf.set_xy(x4 + 2, y_kpi + 1.5)
        pdf.set_font(font, "B", 7)
        pdf.set_text_color(100, 116, 139)
        pdf.cell(kw - 4, 4, "RENDIBILITAT (ROI)", align="L")
        pdf.set_xy(x4 + 2, y_kpi + 6.5)
        pdf.set_font(font, "B", 12)
        pdf.set_text_color(37, 99, 235)
        pdf.cell(kw - 4, 6, f"{roi_pct:+.1f}%", align="L")

        pdf.set_y(y_kpi + 20)

        # Balanç de la Jornada Anterior
        last_combos = simulation_summary.get("last_round_combos", [])
        if last_combos:
            pdf.set_fill_color(248, 250, 252)
            pdf.set_draw_color(203, 213, 225)
            y_box = pdf.get_y()
            pdf.rect(14, y_box, 182, 18, "FD")
            pdf.set_xy(18, y_box + 2)
            pdf.set_font(font, "B", 8)
            pdf.set_text_color(30, 41, 59)
            pdf.cell(170, 4.5, "VERIFICACIÓ DE LA JORNADA ANTERIOR:", align="L")

            pdf.set_xy(18, y_box + 7.5)
            pdf.set_font(font, "", 7.5)
            pdf.set_text_color(71, 85, 105)
            info_txts = []
            for lc in last_combos:
                st_str = "ENCERTADA (+)" if lc["status"] == "WON" else "FALLADA (-)"
                info_txts.append(f"{lc['profile']} (J{lc['jornada']}): {st_str} -> PnL: {lc['profit']:+.2f} €")
            pdf.cell(170, 4.5, "  |  ".join(info_txts), align="L")
            pdf.set_y(y_box + 22)

        # -------------------------------------------------------------
        # SECCIÓ 2: MEGA-COMBINADA SEGURA MULTI-LLIGA (CUOTA 2 - 3)
        # -------------------------------------------------------------
        safe_combo = multileague_combos.get("safe_combo", {})
        if safe_combo and safe_combo.get("legs"):
            pdf.section_title(
                "2. MEGA-COMBINADA MULTI-LLIGA DE MÀXIMA SEGURETAT (CUOTA 2 - 3)",
                "Seleccions amb major certesa matemàtica (>= 88-90% model) creuant LaLiga, Premier, Hypermotion i Championship"
            )

            y_s = pdf.get_y()
            legs_s = safe_combo["legs"]
            card_h_s = 14 + 6.5 + (len(legs_s) * 5.5) + 6

            pdf.set_fill_color(240, 253, 244) # Emerald 50
            pdf.set_draw_color(16, 185, 129)  # Emerald 500
            pdf.rect(14, y_s, 182, card_h_s, "FD")

            pdf.set_xy(18, y_s + 1.5)
            pdf.set_font(font, "B", 9)
            pdf.set_text_color(6, 95, 70)
            pdf.cell(110, 5, "MEGA-COMBINADA SEGURA (ASSIGNACIÓ: 25.00 € A WINAMAX)", align="L")

            pdf.set_xy(125, y_s + 1.5)
            pdf.set_font(font, "B", 8)
            pdf.set_text_color(5, 150, 105)
            pdf.cell(67, 5, f"Avantatge: +{safe_combo['ev_pct']:.1f}% EV", align="R")

            pdf.set_xy(18, y_s + 7)
            pdf.set_font(font, "", 8)
            pdf.set_text_color(51, 65, 85)
            metric_s_str = f"Cuota Winamax: {safe_combo['combined_odd']:.2f}   |   Probabilitat Model: {safe_combo['combined_prob_pct']:.1f}%   |   Cuota Justa: {safe_combo['fair_odd']:.2f}   |   Partits: {len(legs_s)}"
            pdf.cell(174, 5, metric_s_str, align="L")

            y_tbl_s = y_s + 13.5
            pdf.set_xy(16, y_tbl_s)
            pdf.set_fill_color(30, 41, 59)
            pdf.set_text_color(255, 255, 255)
            pdf.set_font(font, "B", 7.5)
            pdf.cell(46, 6, "PARTIT", fill=True)
            pdf.cell(50, 6, "SELECCIÓ", fill=True)
            pdf.cell(24, 6, "MERCAT", fill=True)
            pdf.cell(18, 6, "C. WMX", align="C", fill=True)
            pdf.cell(22, 6, "PROB. MODEL", align="C", fill=True)
            pdf.cell(18, 6, "ENLLAÇ", align="C", fill=True, new_x="LMARGIN", new_y="NEXT")

            pdf.set_font(font, "", 7.5)
            for idx, leg in enumerate(legs_s):
                bg = (248, 250, 252) if idx % 2 == 0 else (255, 255, 255)
                pdf.set_fill_color(*bg)
                pdf.set_text_color(15, 23, 42)
                pdf.set_x(16)
                pdf.cell(46, 5.5, compact_matchup(leg['matchup']), fill=True)
                pdf.cell(50, 5.5, clean_market_name(leg['name']), fill=True)
                pdf.cell(24, 5.5, str(leg.get('category', '-')), fill=True)
                pdf.cell(18, 5.5, f"{leg['bookie_odd']:.2f}", align="C", fill=True)
                pdf.set_text_color(5, 150, 105)
                pdf.set_font(font, "B", 7.5)
                pdf.cell(22, 5.5, f"{leg['model_prob']:.1f}%", align="C", fill=True)
                pdf.set_font(font, "U", 7.5)
                pdf.set_text_color(37, 99, 235)
                leg_url = leg.get('url', "https://www.winamax.es")
                pdf.cell(18, 5.5, "Obrir", align="C", fill=True, link=leg_url, new_x="LMARGIN", new_y="NEXT")
                pdf.set_font(font, "", 7.5)

            pdf.set_y(y_s + card_h_s + 4)

        # -------------------------------------------------------------
        # PÀGINA 2: COMBINADA ARRISCADA & RADAR D'EUROPA
        # -------------------------------------------------------------
        pdf.add_page()

        # SECCIÓ 3: MEGA-COMBINADA ARRISCADA MULTI-LLIGA (CUOTA >= 30.0)
        risky_combo = multileague_combos.get("risky_combo", {})
        if risky_combo and risky_combo.get("legs"):
            pdf.section_title(
                "3. MEGA-COMBINADA MULTI-LLIGA DE CUOTA ALTA (OBJECTIU >= 30.0)",
                "Multiplicador agressiu d'alt valor matemàtic (+EV%) creuant les grans competicions"
            )

            y_r = pdf.get_y()
            legs_r = risky_combo["legs"]
            card_h_r = 14 + 6.5 + (len(legs_r) * 5.5) + 6

            pdf.set_fill_color(245, 243, 255) # Indigo 50
            pdf.set_draw_color(99, 102, 241)  # Indigo 500
            pdf.rect(14, y_r, 182, card_h_r, "FD")

            pdf.set_xy(18, y_r + 1.5)
            pdf.set_font(font, "B", 9)
            pdf.set_text_color(67, 56, 202)
            pdf.cell(110, 5, "MEGA-COMBINADA ARRISCADA (ASSIGNACIÓ: 5.00 € A WINAMAX)", align="L")

            pdf.set_xy(125, y_r + 1.5)
            pdf.set_font(font, "B", 8)
            pdf.set_text_color(79, 70, 229)
            pdf.cell(67, 5, f"Avantatge: +{risky_combo['ev_pct']:.1f}% EV", align="R")

            pdf.set_xy(18, y_r + 7)
            pdf.set_font(font, "", 8)
            pdf.set_text_color(51, 65, 85)
            metric_r_str = f"Cuota Winamax: {risky_combo['combined_odd']:.2f}   |   Probabilitat Model: {risky_combo['combined_prob_pct']:.1f}%   |   Cuota Justa: {risky_combo['fair_odd']:.2f}   |   Partits: {len(legs_r)}"
            pdf.cell(174, 5, metric_r_str, align="L")

            y_tbl_r = y_r + 13.5
            pdf.set_xy(16, y_tbl_r)
            pdf.set_fill_color(30, 41, 59)
            pdf.set_text_color(255, 255, 255)
            pdf.set_font(font, "B", 7.5)
            pdf.cell(46, 6, "PARTIT", fill=True)
            pdf.cell(50, 6, "SELECCIÓ", fill=True)
            pdf.cell(24, 6, "MERCAT", fill=True)
            pdf.cell(18, 6, "C. WMX", align="C", fill=True)
            pdf.cell(22, 6, "PROB. MODEL", align="C", fill=True)
            pdf.cell(18, 6, "ENLLAÇ", align="C", fill=True, new_x="LMARGIN", new_y="NEXT")

            pdf.set_font(font, "", 7.5)
            for idx, leg in enumerate(legs_r):
                bg = (248, 250, 252) if idx % 2 == 0 else (255, 255, 255)
                pdf.set_fill_color(*bg)
                pdf.set_text_color(15, 23, 42)
                pdf.set_x(16)
                pdf.cell(46, 5.5, compact_matchup(leg['matchup']), fill=True)
                pdf.cell(50, 5.5, clean_market_name(leg['name']), fill=True)
                pdf.cell(24, 5.5, str(leg.get('category', '-')), fill=True)
                pdf.cell(18, 5.5, f"{leg['bookie_odd']:.2f}", align="C", fill=True)
                pdf.set_text_color(79, 70, 229)
                pdf.set_font(font, "B", 7.5)
                pdf.cell(22, 5.5, f"{leg['model_prob']:.1f}%", align="C", fill=True)
                pdf.set_font(font, "U", 7.5)
                pdf.set_text_color(37, 99, 235)
                leg_url = leg.get('url', "https://www.winamax.es")
                pdf.cell(18, 5.5, "Obrir", align="C", fill=True, link=leg_url, new_x="LMARGIN", new_y="NEXT")
                pdf.set_font(font, "", 7.5)

            pdf.set_y(y_r + card_h_r + 4)

        # -------------------------------------------------------------
        # SECCIÓ 4: RADAR D'EUROPA (ELS PARTITS DESTACATS DE LA JORNADA)
        # -------------------------------------------------------------
        if highlight_matches:
            pdf.section_title(
                "4. RADAR EUROPEU: PARTITS MÉS IMPORTANTS DEL CAP DE SETMANA",
                "Anàlisi dels enfrontaments de major impacte a LaLiga, Premier League, Hypermotion i Championship"
            )

            for m in highlight_matches[:4]:
                if pdf.get_y() > 240:
                    pdf.add_page()

                y_m = pdf.get_y()
                pdf.set_fill_color(255, 255, 255)
                pdf.set_draw_color(226, 232, 240)
                pdf.rect(14, y_m, 182, 22, "FD")

                # Header partit
                pdf.set_xy(18, y_m + 1.5)
                pdf.set_font(font, "B", 9)
                pdf.set_text_color(15, 23, 42)
                comp_tag = m.get("competition_id", "EURO")
                pdf.cell(110, 5, f"[{comp_tag}] {m.get('matchup', 'Partit')}  ({m.get('date', 'Pendent')})", align="L")

                pdf.set_xy(130, y_m + 1.5)
                pdf.set_font(font, "B", 8)
                pdf.set_text_color(37, 99, 235)
                pdf.cell(62, 5, f"Pronòstic: {m.get('verdict_1x2', '-')}", align="R")

                # xG i detalls
                pdf.set_xy(18, y_m + 7.5)
                pdf.set_font(font, "", 7.5)
                pdf.set_text_color(71, 85, 105)
                goals = m.get("goals", {})
                xgh = goals.get("expected_goals_home", 0.0)
                xga = goals.get("expected_goals_away", 0.0)
                p1 = goals.get("prob_1X2", {}).get("1", 0.0)
                px = goals.get("prob_1X2", {}).get("X", 0.0)
                p2 = goals.get("prob_1X2", {}).get("2", 0.0)
                info_line = f"xG: {xgh:.2f} vs {xga:.2f}   |   Probabilitats: 1: {p1:.1f}% · X: {px:.1f}% · 2: {p2:.1f}%"
                pdf.cell(174, 4.5, info_line, align="L")

                # Millor aposta
                pdf.set_xy(18, y_m + 13)
                vbs = m.get("bet_analysis", {}).get("value_bets", [])
                if vbs:
                    vb = vbs[0]
                    pdf.set_font(font, "B", 7.5)
                    pdf.set_text_color(5, 150, 105)
                    pdf.cell(174, 4.5, f"Millor Aposta Winamax: {clean_market_name(vb['name'])} @ {vb['bookie_odd']:.2f} (+{vb['ev_pct']:.1f}% EV)", align="L")
                else:
                    pdf.set_font(font, "I", 7.5)
                    pdf.set_text_color(148, 163, 184)
                    pdf.cell(174, 4.5, "Sense avantatge matemàtic clar (+EV > 0)", align="L")

                pdf.set_y(y_m + 25)

        # Desa el fitxer
        out_filename = self.output_dir / f"informe_jornada_{jornada}_multilliga.pdf"
        pdf.output(str(out_filename))
        return out_filename

