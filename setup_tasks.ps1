$python = "C:\Users\forti\AppData\Local\Python\pythoncore-3.14-64\python.exe"
$proj = "c:\Fortia\Personal\PreddicionsLliga"

Write-Host "=============================================================================="
Write-Host "   CONFIGURANT PLANIFICADOR DE TASQUES DE WINDOWS AUTOMÀTIC"
Write-Host "=============================================================================="

# 1. Dimarts a les 09:00: Actualització de resultats del cap de setmana
$tr_dimarts = "`"$python`" `"$proj\auto_pipeline.py`" --update-results"
& schtasks /create /tn "PrediccionsFutbol_Dimarts" /tr $tr_dimarts /sc weekly /d TUE /st 09:00 /f

# 2. Divendres a les 16:00: Prediccions completes, generació de PDFs i enviament per correu
$tr_divendres = "`"$python`" `"$proj\auto_pipeline.py`""
& schtasks /create /tn "PrediccionsFutbol_Divendres" /tr $tr_divendres /sc weekly /d FRI /st 16:00 /f

Write-Host ""
Write-Host "Comprovant estat de les tasques:"
& schtasks /query /tn "PrediccionsFutbol_Dimarts" /fo TABLE
& schtasks /query /tn "PrediccionsFutbol_Divendres" /fo TABLE
Write-Host "=============================================================================="
