"""
scripts/test_odds_api_live_probe.py
===================================
Script de prova real mínima i controlada per a The Odds API.

OBJECTIU DE SEGURETAT:
- Validar connexió, lligues, format de respostes i capçaleres reals de consum sense malgastar quota.
- L'endpoint /sports és 100% GRATUÏT (0 crèdits) i permet comprovar els crèdits disponibles.
- L'endpoint /events és 100% GRATUÏT (0 crèdits) i mostra els pròxims partits de LaLiga.
- Només si s'especifica el flag --execute-1credit-probe es realitza la consulta 1X2 de LaLiga (1 crèdit).

Ús:
    python scripts/test_odds_api_live_probe.py --dry-run
    python scripts/test_odds_api_live_probe.py --execute-1credit-probe
"""

import os
import sys
import argparse
from pathlib import Path
from datetime import datetime, timezone

# Afegir arrel al path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scraper.odds_api_client import TheOddsApiClient, CANONICAL_LEAGUES


def run_probe(execute_paid_call: bool = False):
    print("=" * 70)
    print("   PROVA MÍNIMA CONTROLADA DE THE ODDS API (FASE 2)")
    print("=" * 70)

    # 1. Comprovació de credencial en variable d'entorn
    api_key = os.environ.get("THE_ODDS_API_KEY", "").strip()
    if not api_key:
        print("[!] ERROR: No s'ha trobat la variable d'entorn THE_ODDS_API_KEY.")
        print("    Per configurar-la temporalment a PowerShell:")
        print("    $env:THE_ODDS_API_KEY=\"la_teva_clau_aqui\"")
        print("    O al fitxer .env local.")
        return 1

    masked_key = f"{api_key[:4]}...{api_key[-4:]}" if len(api_key) >= 10 else "***"
    print(f"[*] Clau API detectada: {masked_key}")

    client = TheOddsApiClient(api_key=api_key)

    # 2. PROVA 1 (0 CRÈDITS): /v4/sports
    print("\n[*] PAS 1: Verificació de connectivitat i lectura de quota (/v4/sports)...")
    print("    -> Cost nominal: 0 crèdits (Gratuït)")
    try:
        sports = client.get_sports()
        print(f"[+] Connexió establerta satisfactòriament! ({len(sports)} esports al catàleg)")
        print(f"    • Crèdits restants (x-requests-remaining): {client.requests_remaining}")
        print(f"    • Crèdits utilitzats (x-requests-used):     {client.requests_used}")
    except Exception as e:
        print(f"[!] Error connectant amb The Odds API: {e}")
        return 1

    # 3. PROVA 2 (0 CRÈDITS): /v4/sports/soccer_spain_la_liga/events
    print("\n[*] PAS 2: Inspecció de calendari de LaLiga (/events)...")
    print("    -> Cost nominal: 0 crèdits (Gratuït)")
    try:
        la_liga_key = CANONICAL_LEAGUES["LALIGA"]
        events = client.get_events(sport_key=la_liga_key)
        print(f"[+] S'han trobat {len(events)} esdeveniments programats a LaLiga.")
        now_utc = datetime.now(timezone.utc)
        upcoming_count = 0
        for ev in events[:5]:
            commence = ev.get("commence_time", "N/A")
            h = ev.get("home_team")
            a = ev.get("away_team")
            print(f"    • {h} vs {a} ({commence})")
            upcoming_count += 1
    except Exception as e:
        print(f"[!] Error inspeccionant esdeveniments de LaLiga: {e}")
        return 1

    # 4. PROVA 3 (1 CRÈDIT): /v4/sports/soccer_spain_la_liga/odds (OPCIONAL)
    if not execute_paid_call:
        print("\n" + "=" * 70)
        print("[+] PROVA EN MODE GRATUÏT (0 CRÈDITS) COMPLETADA AMB ÈXIT.")
        print("    Per executar la consulta real de quotes 1X2 (consum de 1 crèdit):")
        print("    python scripts/test_odds_api_live_probe.py --execute-1credit-probe")
        print("=" * 70)
        return 0

    print("\n[*] PAS 3: Consulta real de quotes 1X2 per a LaLiga (/odds)...")
    print("    -> ATENCIÓ: Consum real de 1 crèdit de la teva quota!")
    initial_rem = client.requests_remaining

    try:
        odds_list = client.get_odds(sport_key=la_liga_key, regions="eu", markets="h2h")
        print(f"[+] S'han rebut quotes per a {len(odds_list)} partits.")
        print(f"    • Crèdits restants anteriors: {initial_rem}")
        print(f"    • Crèdits restants actuals:   {client.requests_remaining}")
        print(f"    • Cost oficial de la petició: {client.requests_last_cost} crèdit(s)")

        if odds_list:
            first_m = odds_list[0]
            print(f"\n    Mostra del primer partit: {first_m.get('home_team')} vs {first_m.get('away_team')}")
            for bk in first_m.get("bookmakers", [])[:3]:
                print(f"      - Casa: {bk.get('title')} ({bk.get('key')})")

    except Exception as e:
        print(f"[!] Error en consultar quotes: {e}")
        return 1

    print("\n" + "=" * 70)
    print("[+] PROVA DE VALIDAICÓ REAL DE 1 CRÈDIT FINALITZADA AMB ÈXIT.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sonda mínima controlada per a The Odds API.")
    parser.add_argument("--execute-1credit-probe", action="store_true", help="Executa la consulta 1X2 d'1 crèdit.")
    parser.add_argument("--dry-run", action="store_true", help="Només consulta endpoints de 0 crèdits.")
    args = parser.parse_args()

    sys.exit(run_probe(execute_paid_call=args.execute_1credit_probe))
