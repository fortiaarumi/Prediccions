"""
notifier/email_notifier.py
==========================
Mòdul d'alertes automàtiques per correu electrònic i notificacions del sistema.
Avisa a l'usuari quan es detecten partits sense quotes a Winamax o Bet365 perquè
pugui revisar-los o introduir les quotes manualment.
"""

import os
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import List, Dict, Any, Optional

ROOT_DIR = Path(__file__).parent.parent
CONFIG_FILE = ROOT_DIR / "config" / "email_alerts.json"
ALERTS_DATA_FILE = ROOT_DIR / "data" / "missing_odds_alerts.json"

def get_email_config() -> Dict[str, Any]:
    """Obté la configuració de correu des de variables d'entorn o config/email_alerts.json."""
    config = {
        "enabled": False,
        "smtp_host": os.environ.get("SMTP_HOST", ""),
        "smtp_port": int(os.environ.get("SMTP_PORT", "587")),
        "smtp_user": os.environ.get("SMTP_USER", ""),
        "smtp_password": os.environ.get("SMTP_PASSWORD", ""),
        "smtp_use_tls": os.environ.get("SMTP_USE_TLS", "true").lower() in ["1", "true", "yes"],
        "email_to": os.environ.get("ALERT_EMAIL_TO", ""),
        "email_from": os.environ.get("ALERT_EMAIL_FROM", "")
    }

    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                file_cfg = json.load(f)
                for k, v in file_cfg.items():
                    if v and not config.get(k):
                        config[k] = v
        except Exception:
            pass

    if config["smtp_host"] and config["email_to"]:
        config["enabled"] = True

    if not config["email_from"] and config["smtp_user"]:
        config["email_from"] = config["smtp_user"]

    return config

def record_local_alerts(alerts: List[Dict[str, Any]]):
    """Guarda les alertes locals a data/missing_odds_alerts.json per a consulta ràpida."""
    try:
        ALERTS_DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(ALERTS_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "count": len(alerts),
                "alerts": alerts
            }, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[!] Error desant alertes locals: {e}")

def send_missing_odds_alert(alerts: List[Dict[str, Any]]) -> bool:
    """
    Envia un correu electrònic a l'usuari amb la llista de partits que no tenen
    quotes detectades a Winamax o Bet365.
    """
    if not alerts:
        # Si no hi ha alertes pendents, buidar fitxer d'alertes locals
        record_local_alerts([])
        return True

    # Desar registre local d'alertes
    record_local_alerts(alerts)

    cfg = get_email_config()
    if not cfg["enabled"]:
        print("\n" + "!" * 78)
        print("🔔 [AVÍS DE QUOTES NO DETECTADES]")
        print(f"   S'han detectat {len(alerts)} partits sense quotes automàtiques a Winamax o Bet365.")
        print("   Pots revisar-los i introduir les quotes manualment a la web o a data/manual_odds_override.json.")
        print("   ℹ️ Per rebre avisos per correu, configura 'ALERT_EMAIL_TO' i 'SMTP_HOST' a config/email_alerts.json")
        print("!" * 78 + "\n")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"⚠️ [Prediccions Futbol] {len(alerts)} partits sense quotes detectades a Winamax / Bet365"
        msg["From"] = cfg["email_from"] or "alertes@prediccionsfutbol.cat"
        msg["To"] = cfg["email_to"]

        # Text pla
        text_lines = [
            f"S'han detectat {len(alerts)} partits actius sense quotes oficials a Winamax o Bet365:",
            ""
        ]
        for a in alerts:
            text_lines.append(f"- [{a.get('competition_name', a.get('competition_id'))}] {a.get('matchup')} ({a.get('date', 'Pendent')})")
            text_lines.append(f"  Falten quotes a: {', '.join(a.get('missing_bookmakers', []))}")
            text_lines.append("")
        text_lines.append("Accedeix a la web per introduir-les manualment o afegeix-les a 'data/manual_odds_override.json'.")
        plain_body = "\n".join(text_lines)

        # HTML
        html_rows = ""
        for a in alerts:
            b_tags = "".join([f"<span style='background:#ff4444;color:#fff;padding:2px 8px;border-radius:4px;font-size:12px;margin-right:4px;'>{b}</span>" for b in a.get("missing_bookmakers", [])])
            html_rows += f"""
            <tr style="border-bottom:1px solid #333;">
                <td style="padding:10px 14px;color:#00ffcc;font-weight:bold;">{a.get('competition_name', a.get('competition_id'))}</td>
                <td style="padding:10px 14px;color:#ffffff;font-size:14px;">{a.get('matchup')}</td>
                <td style="padding:10px 14px;color:#aaa;font-size:13px;">{a.get('date', 'Pendent')}</td>
                <td style="padding:10px 14px;">{b_tags}</td>
            </tr>
            """

        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; background-color: #0b0f19; color: #e2e8f0; padding: 24px;">
            <div style="max-width: 650px; margin: 0 auto; background: #121826; border-radius: 10px; border: 1px solid #2d3748; padding: 24px;">
                <h2 style="color: #f59e0b; margin-top: 0;">⚠️ Partits Pendents de Quotes Reals</h2>
                <p style="color: #cbd5e1; font-size: 14px; line-height: 1.6;">
                    El model ha detectat <strong>{len(alerts)} partits</strong> de la propera jornada que no tenen quotes detectades
                    a Winamax o a Bet365. El model <strong>no inventa quotes amb models matemàtics</strong> per evitar dades errònies.
                </p>
                <table style="width: 100%; border-collapse: collapse; margin-top: 16px; background: #1a2234; border-radius: 6px; overflow: hidden;">
                    <thead>
                        <tr style="background: #242f45; text-align: left; color: #94a3b8; font-size: 12px; text-transform: uppercase;">
                            <th style="padding: 10px 14px;">Lliga</th>
                            <th style="padding: 10px 14px;">Partit</th>
                            <th style="padding: 10px 14px;">Data</th>
                            <th style="padding: 10px 14px;">Casa Faltant</th>
                        </tr>
                    </thead>
                    <tbody>
                        {html_rows}
                    </tbody>
                </table>
                <div style="margin-top: 24px; padding: 14px; background: #1e293b; border-left: 4px solid #3b82f6; border-radius: 4px; font-size: 13px; color: #94a3b8;">
                    💡 <strong>Com solucionar-ho:</strong> Pots obrir la plataforma web i prémer el botó <em>'Modificar Quotes'</em> al banner superior, o bé afegir-les al fitxer <code>data/manual_odds_override.json</code>.
                </div>
            </div>
        </body>
        </html>
        """

        msg.attach(MIMEText(plain_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=10) as server:
            if cfg["smtp_use_tls"]:
                server.starttls()
            if cfg["smtp_user"] and cfg["smtp_password"]:
                server.login(cfg["smtp_user"], cfg["smtp_password"])
            server.send_message(msg)

        print(f"[+] Alerta per correu enviada correctament a {cfg['email_to']}.")
        return True
    except Exception as e:
        print(f"[!] Error enviant alerta per correu electrònic: {e}")
        return False
