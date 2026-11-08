@echo off
chcp 65001 > nul
title Prediccions Lliga - Servidor Web Privat de Proves

echo =====================================================================
echo       PREDICCIONS LLIGA — SERVIDOR WEB PRIVAT DE VALIDACIÓ
echo =====================================================================
echo [*] Inicialitzant servidor web per a proves al PC i mòbil (iPhone)...
echo [*] El manteniment públic a Vercel es manté actiu sense canvis.
echo =====================================================================

where python >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR CRITIC]: No s'ha trobat Python al PATH del sistema.
    pause
    exit /b 1
)

set PYTHONUNBUFFERED=1
python scripts\start_test_server.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [!] El servidor web s'ha aturat amb el codi %ERRORLEVEL%.
    pause
)
