@echo off
chcp 65001 > nul
echo ==============================================================================
echo    SISTEMA AUTÒNOM DE PREDICCIONS DE FUTBOL (LALIGA, PREMIER, HYPERMOTION)
echo ==============================================================================
echo.
cd /d "c:\Fortia\Personal\PreddicionsLliga"

echo [*] Executant pipeline complet:
echo     1. Avaluacio d'apostes anteriors ('Que plagues passat si...')
echo     2. Prediccions de LaLiga EA Sports, Premier League i LaLiga Hypermotion
echo     3. Cuotes en directe de Winamax Espanya
echo     4. Mega-Combinades Multi-Lliga
echo     5. Generacio de 4 Informes PDF a reports/
echo     6. Enviament per correu electronic a config/recipients.txt
echo.

python auto_pipeline.py

echo.
echo ==============================================================================
echo    [FI DEL PROCES] Prem qualsevol tecla per tancar aquesta finestra.
echo ==============================================================================
pause > nul
