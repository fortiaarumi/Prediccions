@echo off
chcp 65001 > nul
title Prediccions Lliga - Surebet Local Agent (Windows)

echo =====================================================================
echo          PREDICCIONS LLIGA - SUREBET LOCAL MONITOR AGENT
echo =====================================================================
echo [*] Inicialitzant entorn d'execucio per a Windows...
echo [*] Seguretat: Connexio sortint exclusiva (Zero ports oberts al router).
echo [*] Mode: SIMULAT (Zero peticions a bookmakers / Zero apostes reals).
echo [*] Prem Ctrl+C per aturar l'agent en qualsevol moment.
echo =====================================================================

:: Comprovar si Python esta instal·lat i disponible al PATH
where python >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR CRITIC]: No s'ha trobat Python al PATH del sistema.
    echo Assegura't de tenir Python 3.10 o superior instal·lat.
    echo.
    pause
    exit /b 1
)

:: Executar l'agent de monitoratge persistent en mode simulat
set PYTHONUNBUFFERED=1
python cli.py surebet-agent --simulated

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [!] L'agent s'ha aturat amb el codi de sortida %ERRORLEVEL%.
    pause
)

