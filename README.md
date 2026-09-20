# ⚽ Sistema de Prediccions de Futbol Multi-Lliga (Poisson GLM + Winamax + ROI Tracker)

Plataforma avançada de predicció matemàtica i anàlisi d'apostes esportives amb valor esperat (+EV%) per a:
- **LaLiga EA Sports** (1a Divisió Espanyola)
- **Premier League** (Anglaterra)
- **LaLiga Hypermotion** (2a Divisió Espanyola)

---

## 🚀 Característiques Principals

1. **Model Estadístic Poisson Bivariant & xG**:
   - Predicció precisa de marcadors, probabilitats 1X2, Over/Under 2.5 gols, Ambdós Marquen (BTTS), targetes i córners.
   - Rànquings dinàmics d'Elo i mètriques combinades de perill real ofensiu i solidesa defensiva.

2. **Scraper en Directe de Flashscore & Winamax Espanya**:
   - Descobriment autònom del calendari de partits, resultats oficials i estadístiques en directe.
   - Extracció en temps real de cuotes de Winamax Espanya (1X2, Doble Oportunitat, Gols, BTTS, Targetes, Córners).

3. **Motor de Seguiment Financer ("Què hagués passat si...")**:
   - Simulador de bankroll des de la primera jornada:
     - **25,00 €** a la Combinada Segura (Cuota 2 - 3).
     - **5,00 €** a la Combinada Arriscada (Cuota >= 30.0).
   - Verificació automàtica de cada selecció quan finalitzen els partits.
   - Tauler financer amb **Inversió Total**, **Retorn Brut**, **Benefici Net (PnL €)**, **ROI %** i **Taxa d'encert**.

4. **Informes PDF de Disseny Executiu**:
   - Informes individuals per a cada competició: `informe_jornada_{N}_laliga.pdf`, `informe_jornada_{N}_premier.pdf`, `informe_jornada_{N}_hypermotion.pdf`.
   - **Mega-Informe Multi-Lliga (`informe_jornada_{N}_multilliga.pdf`)**: Tauler financer PnL, Mega-Combinades transfrontereres i Radar de Valor dels millors duels del continent.

5. **Enviament Automàtic per Correu Electrònic**:
   - Distribució automàtica dels PDFs adjunts a la llista de destinataris de `config/recipients.txt`.

---

## 📁 Estructura del Projecte

```
PreddicionsLliga/
├── auto_pipeline.py          # Orquestrador autònom integral (1 sol pas)
├── schedule_weekly.bat       # Executable de Windows per córrer tot el pipeline
├── main.py                   # Comandes manuals per terminal (CLI)
├── config/
│   ├── recipients.txt        # Llista de correus destinataris (un per línia)
│   └── email_config.json     # Configuració del servidor SMTP de correu
├── data/
│   └── laliga.db             # Base de dades SQLite relacional oficial
├── database/
│   ├── schema.sql            # Esquema de taules (partits, equips, combinades, PnL)
│   └── db_manager.py         # Connexió i consultes a la base de dades
├── engine/
│   ├── combo_tracker.py      # Simulador financer i verificador 'Què hagués passat si...'
│   ├── combo_bet_engine.py   # Motor matemàtic de combinades mono-lliga i multi-lliga
│   ├── jornada_predictor.py  # Predictor de partits i resolució d'àrbitres
│   ├── value_bet_engine.py   # Càlcul de valor esperat (+EV%) i Kelly Criterion
│   └── pdf_report_generator.py # Generador d'informes PDF d'alta qualitat
├── model/
│   ├── goal_model.py         # Model de gols Poisson bivariant
│   ├── card_model.py         # Model de targetes i severitat arbitral
│   ├── corner_model.py       # Model de córners
│   └── rank_engine.py        # Motor d'Elo i rànquings ofensius/defensius
├── notifier/
│   └── email_sender.py       # Enviament de correus amb fitxers PDF adjunts
├── scraper/
│   ├── live_crawler.py       # Rastrejador Flashscore multi-lliga
│   ├── match_scraper.py      # Extracció d'estadístiques avançades (xG, targetes, córners)
│   ├── winamax_scraper.py    # Extracció de cuotes de Winamax Espanya
│   └── referee_resolver.py   # Resolució d'àrbitres oficials CTA
└── reports/                  # Carpeta on es desen els PDFs generats
```

---

## 🛠️ Com Utilitzar-lo

### 1. Execució amb 1 Sol Clic (Windows)
Fes doble clic sobre `schedule_weekly.bat`.

### 2. Execució per Terminal
```bash
# Executar el pipeline complet (avaluació de resultats, prediccions, PDFs i enviament per correu)
python auto_pipeline.py --jornada 6

# Executar sense enviar correu (només generar els PDFs a reports/)
python auto_pipeline.py --jornada 6 --no-email
```
