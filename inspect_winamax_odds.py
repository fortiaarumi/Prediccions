"""
inspect_winamax_odds.py
=======================
Eina per verificar l'extracció neta de cuotes reals de Winamax (sense jugadors ni palla).

Com utilitzar-lo des del teu PowerShell:
    python inspect_winamax_odds.py --home "FC Barcelona" --away "Athletic Bilbao"
    python inspect_winamax_odds.py --home "Osasuna" --away "Levante"
    python inspect_winamax_odds.py --home "Real Madrid" --away "Real Sociedad"
"""

import sys
import os
import io
import argparse

# Sortida UTF-8 per a Windows
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from scraper.winamax_scraper import WinamaxScraper

def main():
    parser = argparse.ArgumentParser(description="Verifica l'scrapping de cuotes reals de Winamax")
    parser.add_argument("--home", type=str, required=True, help="Nom de l'equip local")
    parser.add_argument("--away", type=str, required=True, help="Nom de l'equip visitant")

    args = parser.parse_args()

    print("\n" + "=" * 85)
    print(f"   EXTRACCIÓ EN DIRECTE DE CUOTES DE WINAMAX ESPANYA")
    print(f"   Partit: {args.home} vs {args.away}")
    print("=" * 85)

    scraper = WinamaxScraper(headless=True)
    odds = scraper.get_match_odds(args.home, args.away)

    if not odds.get("matched"):
        print(f"\n[!] No s'ha trobat cap partit actiu a Winamax per a '{args.home}' vs '{args.away}'.")
        return

    print("\n" + "-" * 85)
    print(f"   DADES DEL PARTIT LOCALITZAT")
    print(f"   • Títol Oficial:    {odds['matched_title']}")
    print(f"   • ID de Winamax:    {odds['match_id']}")
    print(f"   • Enllaç Web Real:  {odds['url']}")
    print("-" * 85)

    # 1. Mercats Principals (1X2 i Doble Oportunitat)
    print("\n📊 1. RESULTAT FINAL (1X2) & DOBLE OPORTUNITAT:")
    if odds.get("1X2") and "1" in odds["1X2"]:
        print(f"   • 1 ({args.home}): {odds['1X2'].get('1'):.2f} | X (Empat): {odds['1X2'].get('X'):.2f} | 2 ({args.away}): {odds['1X2'].get('2'):.2f}")
    else:
        print("   • [Mercat 1X2 no disponible]")

    if odds.get("double_chance") and "1X" in odds["double_chance"]:
        print(f"   • 1X ({args.home} o Empat): {odds['double_chance'].get('1X'):.2f} | 12: {odds['double_chance'].get('12', 0.0):.2f} | X2 (Empat o {args.away}): {odds['double_chance'].get('X2'):.2f}")
    else:
        print("   • [Doble Oportunitat no disponible]")

    # 2. Mercats de Gols
    print("\n⚽ 2. MERCATS DE GOLS:")
    if odds.get("over_under_2_5") and "Over 2.5" in odds["over_under_2_5"]:
        print(f"   • Més de 2.5 Gols (Over 2.5):  {odds['over_under_2_5'].get('Over 2.5'):.2f}")
        print(f"   • Menys de 2.5 Gols (Under 2.5): {odds['over_under_2_5'].get('Under 2.5'):.2f}")
    else:
        print("   • Total de Gols (2.5): [ENCARA NO PUBLICAT PER WINAMAX]")

    if odds.get("btts") and "Sí" in odds["btts"]:
        print(f"   • Ambdós Marquen (Sí): {odds['btts'].get('Sí'):.2f} | Ambdós Marquen (No): {odds['btts'].get('No'):.2f}")
    else:
        print("   • Ambdós Equips Marquen: [ENCARA NO PUBLICAT PER WINAMAX]")

    # 3. Mercats de Targetes
    print("\n🟨 3. MERCATS DE TARGETES:")
    if odds.get("cards"):
        for k, v in odds["cards"].items():
            print(f"   • {k:<25} -> {v:.2f}")
    else:
        print("   • [ENCARA NO PUBLICAT PER WINAMAX] (S'obre 24-48h abans del partit)")

    # 4. Mercats de Córners
    print("\n⛳ 4. MERCATS DE CÓRNERS:")
    if odds.get("corners"):
        for k, v in odds["corners"].items():
            print(f"   • {k:<25} -> {v:.2f}")
    else:
        print("   • [ENCARA NO PUBLICAT PER WINAMAX] (S'obre 24-48h abans del partit)")

    print("\n" + "=" * 85)
    print(f"[OK] Pots contrastar aquestes dades directament a l'enllaç:")
    print(f"     {odds['url']}")
    print("=" * 85 + "\n")

if __name__ == "__main__":
    main()
