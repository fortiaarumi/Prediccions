@echo off
chcp 65001 > nul
echo ==============================================================================
echo    CONFIGURACIÓ AUTOMÀTICA AL PLANIFICADOR DE TASQUES DE WINDOWS
echo ==============================================================================
echo.
echo Creant les dues tasques programades automàtiques:
echo 1. Dimarts a les 09:00h: Actualització de resultats i balanç financer PnL
echo 2. Divendres a les 16:00h: Prediccions completes, generació de PDFs i enviament
echo.

set PYTHON_PATH=C:\Users\forti\AppData\Local\Python\pythoncore-3.14-64\python.exe
set PROJECT_DIR=c:\Fortia\Personal\PreddicionsLliga

:: 1. Tasca de Dimarts
schtasks /create /tn "PrediccionsFutbol_Dimarts_Resultats" /tr "\"%PYTHON_PATH%\" \"%PROJECT_DIR%\auto_pipeline.py\" --update-results" /sc weekly /d TUE /st 09:00 /f

:: 2. Tasca de Divendres
schtasks /create /tn "PrediccionsFutbol_Divendres_Informes" /tr "\"%PYTHON_PATH%\" \"%PROJECT_DIR%\auto_pipeline.py\"" /sc weekly /d FRI /st 16:00 /f

echo.
echo ==============================================================================
echo    [ÈXIT] Les dues tasques han quedat registrades a Windows correctament!
echo ==============================================================================
pause
