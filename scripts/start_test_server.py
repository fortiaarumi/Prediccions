"""
scripts/start_test_server.py
============================
Servidor web local privat per validar la interfície web i la cerca de Surebets
des del navegador del PC o des de l'iPhone (a la mateixa xarxa Wi-Fi local).

Característiques:
- Serveix el contingut estàtic de la carpeta `web/`.
- Detecta la IP local (LAN) per mostrar l'enllaç exacte per a mòbil (iPhone).
- No altera ni desplega res a Vercel (el manteniment públic continua actiu).
- Tota la comunicació es fa directament contra Supabase via HTTPS amb la clau publicable.
"""

import os
import sys
import socket
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

PORT = 8080
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "web"))


def get_local_ip() -> str:
    """Detecta la IP local de la interfície de xarxa activa per connectar des del mòbil."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # No envia dades reals, només resol la interfície de sortida cap a internet
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


class WebHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def log_message(self, format, *args):
        # Format concís de registres d'accés
        sys.stdout.write(f"[{self.log_date_time_string()}] {args[0]} - {args[1]}\n")


def main():
    os.chdir(BASE_DIR)
    local_ip = get_local_ip()

    server = ThreadingHTTPServer(("0.0.0.0", PORT), WebHandler)

    print("\n" + "=" * 70)
    print("      PREDICCIONS LLIGA — SERVIDOR WEB PRIVAT DE VALIDACIÓ")
    print("=" * 70)
    print(f"Directori servit:     {BASE_DIR}")
    print(f"Port HTTP:             {PORT}")
    print("-" * 70)
    print("ACCÉS A LA WEB DE PROVES:")
    print(f"  💻 Des del PC local:    http://localhost:{PORT}")
    print(f"  📱 Des del teu iPhone:  http://{local_ip}:{PORT} (connectat a la mateixa Wi-Fi)")
    print("-" * 70)
    print("SEGURETAT I OPERACIÓ:")
    print("  • La web requereix login d'operador (fortiaarumi@gmail.com).")
    print("  • Es connecta directament a Supabase via HTTPS.")
    print("  • El manteniment públic a Vercel es manté actiu i protegit.")
    print("=" * 70)
    print("Prem Ctrl+C per aturar el servidor web.\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nAturant el servidor web privat...")
        server.server_close()
        print("Servidor aturat correctament.")


if __name__ == "__main__":
    main()
