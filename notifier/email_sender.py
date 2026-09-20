"""
notifier/email_sender.py
========================
Mòdul d'enviament automàtic d'informes PDF per correu electrònic.

Llegeix la llista de destinataris de:
- config/recipients.txt (un correu per línia)
- O config/recipients.json

I la configuració SMTP de:
- config/email_config.json
"""

import os
import sys
import json
import smtplib
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from typing import List, Dict, Any, Optional

# UTF-8 per a consola de Windows
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

CONFIG_DIR = Path(__file__).parent.parent / "config"
RECIPIENTS_TXT = CONFIG_DIR / "recipients.txt"
RECIPIENTS_JSON = CONFIG_DIR / "recipients.json"
EMAIL_CONFIG_PATH = CONFIG_DIR / "email_config.json"

class EmailSender:
    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path if config_path else EMAIL_CONFIG_PATH
        self.config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        cfg = {}
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception as e:
                print(f"[AVÍS] No s'ha pogut carregar la configuració SMTP: {e}")

        # Suport de Variables d'Entorn (GitHub Actions Secrets al núvol)
        if os.getenv("GMAIL_SENDER_EMAIL"):
            cfg["sender_email"] = os.getenv("GMAIL_SENDER_EMAIL").strip()
        if os.getenv("GMAIL_APP_PASSWORD"):
            cfg["sender_password"] = os.getenv("GMAIL_APP_PASSWORD").strip()
        if "smtp_server" not in cfg:
            cfg["smtp_server"] = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        if "smtp_port" not in cfg:
            cfg["smtp_port"] = int(os.getenv("SMTP_PORT", "587"))
        if "use_tls" not in cfg:
            cfg["use_tls"] = True
        if "sender_name" not in cfg:
            cfg["sender_name"] = "Sistema de Prediccions de Futbol"

        return cfg

    def get_recipients(self) -> List[str]:
        """Recupera la llista de destinataris des de variables d'entorn o fitxers."""
        recipients = set()

        # 0. Suport de variable d'entorn per a GitHub Secrets
        env_rec = os.getenv("EMAIL_RECIPIENTS")
        if env_rec:
            for item in env_rec.split(","):
                clean = item.strip()
                if clean and "@" in clean:
                    recipients.add(clean)

        # 1. Llegir recipients.txt
        if RECIPIENTS_TXT.exists():
            with open(RECIPIENTS_TXT, "r", encoding="utf-8") as f:
                for line in f:
                    clean = line.strip()
                    if clean and not clean.startswith("#") and "@" in clean:
                        recipients.add(clean)

        # 2. Llegir recipients.json si existeix
        if RECIPIENTS_JSON.exists():
            try:
                with open(RECIPIENTS_JSON, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        for item in data:
                            if isinstance(item, str) and "@" in item:
                                recipients.add(item.strip())
                    elif isinstance(data, dict):
                        for item in data.get("recipients", []):
                            if isinstance(item, str) and "@" in item:
                                recipients.add(item.strip())
            except Exception:
                pass

        return sorted(list(recipients))

    def is_configured(self) -> bool:
        """Comprova si el servidor SMTP està configurat correctament."""
        user = self.config.get("sender_email", "")
        pw = self.config.get("sender_password", "")
        if not user or not pw or "EL_TEU_CORREU" in user or "LA_TEVA_CONTRASENYA" in pw:
            return False
        return True

    def build_html_body(self, jornada: int, simulation_summary: Optional[Dict[str, Any]] = None, pdf_names: List[str] = None) -> str:
        """Construeix el cos del correu en HTML net, executiu i modern."""
        pnl_html = ""
        if simulation_summary:
            tot_stake = simulation_summary.get("total_stake", 0.0)
            net_profit = simulation_summary.get("net_profit", 0.0)
            roi_pct = simulation_summary.get("roi_pct", 0.0)
            pnl_color = "#10B981" if net_profit >= 0 else "#EF4444"

            pnl_html = f"""
            <div style="background-color: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 8px; padding: 16px; margin: 20px 0;">
                <h3 style="margin-top: 0; color: #1E293B; font-size: 15px;">📊 Tauler 'Què Hagués Passat Si...' (Simulador 25€ / 5€):</h3>
                <table style="width: 100%; border-collapse: collapse; text-align: left; font-size: 14px;">
                    <tr>
                        <td style="padding: 6px 0; color: #64748B;">Inversió Acumulada:</td>
                        <td style="padding: 6px 0; font-weight: bold; color: #0F172A;">{tot_stake:.2f} €</td>
                    </tr>
                    <tr>
                        <td style="padding: 6px 0; color: #64748B;">Benefici Net (PnL):</td>
                        <td style="padding: 6px 0; font-weight: bold; color: {pnl_color};">{net_profit:+.2f} €</td>
                    </tr>
                    <tr>
                        <td style="padding: 6px 0; color: #64748B;">Rendibilitat (ROI):</td>
                        <td style="padding: 6px 0; font-weight: bold; color: #2563EB;">{roi_pct:+.1f}%</td>
                    </tr>
                </table>
            </div>
            """

        pdf_list_html = "".join([f"<li style='margin-bottom: 6px; color: #334155;'>📄 <strong>{name}</strong></li>" for name in (pdf_names or [])])

        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.5; color: #0F172A; }}
            </style>
        </head>
        <body style="margin: 0; padding: 24px; background-color: #F1F5F9;">
            <div style="max-width: 600px; margin: 0 auto; background: #FFFFFF; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);">
                <!-- Header -->
                <div style="background-color: #0F172A; padding: 24px; text-align: left; border-bottom: 3px solid #6366F1;">
                    <span style="background-color: #6366F1; color: white; padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: bold; text-transform: uppercase;">Informe Oficial</span>
                    <h1 style="color: #FFFFFF; margin: 10px 0 4px 0; font-size: 20px;">Prediccions de Futbol · Jornada {jornada}</h1>
                    <p style="color: #94A3B8; margin: 0; font-size: 13px;">LaLiga EA Sports + Premier League + LaLiga Hypermotion + Mega-Combinada Multi-Lliga</p>
                </div>

                <!-- Body Content -->
                <div style="padding: 24px;">
                    <p style="font-size: 15px; color: #334155; margin-top: 0;">
                        Hola! Ja estan disponibles els nous informes matemàtics de la <strong>Jornada {jornada}</strong> generats pel model estadístic i contrastats amb les cuotes en directe de Winamax Espanya.
                    </p>

                    {pnl_html}

                    <div style="margin: 20px 0;">
                        <h4 style="color: #1E293B; margin-bottom: 10px; font-size: 14px;">Fitxers PDF adjunts en aquest correu:</h4>
                        <ul style="padding-left: 20px; font-size: 14px;">
                            {pdf_list_html}
                        </ul>
                    </div>

                    <div style="background-color: #EFF6FF; border-left: 4px solid #3B82F6; padding: 12px 16px; margin: 20px 0; border-radius: 0 6px 6px 0;">
                        <p style="margin: 0; font-size: 13px; color: #1E40AF;">
                            <strong>💡 Recordatori d'apostes:</strong> A l'informe <em>Multi-Lliga</em> trobaràs la Mega-Combinada Segura (Cuota 2-3) i la Mega-Combinada de Cuota Alta (&gt;=30) amb enllaços directes a Winamax.
                        </p>
                    </div>
                </div>

                <!-- Footer -->
                <div style="background-color: #F8FAFC; padding: 16px 24px; border-top: 1px solid #E2E8F0; text-align: center; font-size: 12px; color: #94A3B8;">
                    Creat per Fortià Arumí Casals · Document generat automàticament pel Model Poisson GLM
                </div>
            </div>
        </body>
        </html>
        """

    def send_reports(
        self,
        pdf_paths: List[Path],
        jornada: int,
        simulation_summary: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Envia els fitxers PDF adjunts a tots els destinataris configurats.
        """
        recipients = self.get_recipients()
        if not recipients:
            print("[AVÍS CORREU]: No s'han trobat adreces de correu a 'config/recipients.txt'.")
            return False

        if not self.is_configured():
            print("\n" + "=" * 75)
            print("   ℹ️ [CONFIGURACIÓ PENDENT DE CORREU]")
            print("   Els informes PDF s'han generat i desat amb èxit a la carpeta 'reports/'.")
            print("   Per rebre'ls per correu automàticament, edita 'config/email_config.json' i")
            print("   posa-hi el teu correu i la teva contrasenya d'aplicació de Gmail.")
            print("=" * 75 + "\n")
            return False

        smtp_server = self.config.get("smtp_server", "smtp.gmail.com")
        smtp_port = int(self.config.get("smtp_port", 587))
        use_tls = self.config.get("use_tls", True)
        sender_email = self.config["sender_email"]
        sender_pw = self.config["sender_password"]
        sender_name = self.config.get("sender_name", "Prediccions de Futbol")

        pdf_names = [p.name for p in pdf_paths if p.exists()]
        subject = f"⚽ Informes Prediccions Jornada {jornada} (LaLiga, Premier, Hypermotion & Multi-Lliga)"

        msg = MIMEMultipart("mixed")
        msg["Subject"] = subject
        msg["From"] = f"{sender_name} <{sender_email}>"
        msg["To"] = ", ".join(recipients)

        # Cos HTML
        html_content = self.build_html_body(jornada, simulation_summary, pdf_names)
        msg_alternative = MIMEMultipart("alternative")
        msg_alternative.attach(MIMEText(html_content, "html", "utf-8"))
        msg.attach(msg_alternative)

        # Adjuntar els PDFs
        attached_count = 0
        for p in pdf_paths:
            if p.exists():
                try:
                    with open(p, "rb") as f:
                        part = MIMEApplication(f.read(), Name=p.name)
                    part["Content-Disposition"] = f'attachment; filename="{p.name}"'
                    msg.attach(part)
                    attached_count += 1
                except Exception as err:
                    print(f"[!] Error adjuntant {p.name}: {err}")

        print(f"\n[*] Connectant al servidor SMTP ({smtp_server}:{smtp_port}) per enviar a {len(recipients)} destinataris...")

        try:
            with smtplib.SMTP(smtp_server, smtp_port) as server:
                if use_tls:
                    server.starttls()
                server.login(sender_email, sender_pw)
                server.sendmail(sender_email, recipients, msg.as_string())

            print(f"   [ÈXIT CORREU] S'han enviat {attached_count} informes PDF per correu a:")
            for r in recipients:
                print(f"      • {r}")
            return True

        except Exception as e:
            print(f"[!] Error enviant correu SMTP: {e}")
            return False

    def send_web_alert(
        self,
        jornada_info: str,
        web_url: str = "https://prediccions.vercel.app",
        simulation_summary: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Envia un avís per correu comunicant que la nova jornada és a punt de començar
        amb l'enllaç directe a la nova web interactiva de Vercel.
        """
        recipients = self.get_recipients()
        if not recipients:
            print("[AVÍS CORREU]: No s'han trobat adreces de correu a 'config/recipients.txt'.")
            return False

        if not self.is_configured():
            print("\n" + "=" * 75)
            print("   ℹ️ [CONFIGURACIÓ PENDENT DE CORREU]")
            print("   Per rebre avisos per correu, configura 'config/email_config.json'.")
            print("=" * 75 + "\n")
            return False

        smtp_server = self.config.get("smtp_server", "smtp.gmail.com")
        smtp_port = int(self.config.get("smtp_port", 587))
        use_tls = self.config.get("use_tls", True)
        sender_email = self.config["sender_email"]
        sender_pw = self.config["sender_password"]
        sender_name = self.config.get("sender_name", "Prediccions de Futbol")

        subject = f"⚽ La Jornada ({jornada_info}) és a punt de començar! Revisa la web"

        pnl_html = ""
        if simulation_summary:
            tot_stake = simulation_summary.get("total_stake", 0.0)
            net_profit = simulation_summary.get("net_profit", 0.0)
            roi_pct = simulation_summary.get("roi_pct", 0.0)
            pnl_color = "#10B981" if net_profit >= 0 else "#EF4444"
            pnl_html = f"""
            <div style="background-color: #1E293B; border: 1px solid rgba(255,255,255,0.1); border-radius: 8px; padding: 14px; margin: 18px 0;">
                <table style="width: 100%; border-collapse: collapse; font-size: 13px; color: #F8FAFC;">
                    <tr>
                        <td style="color: #94A3B8;">Inversió Acumulada: <strong>{tot_stake:.2f} €</strong></td>
                        <td style="color: #94A3B8; text-align: center;">Balanç Net: <strong style="color: {pnl_color};">{net_profit:+.2f} €</strong></td>
                        <td style="color: #94A3B8; text-align: right;">ROI: <strong style="color: #6366F1;">{roi_pct:+.1f}%</strong></td>
                    </tr>
                </table>
            </div>
            """

        html = f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset="utf-8"></head>
        <body style="margin: 0; padding: 24px; background-color: #0B0F19; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
            <div style="max-width: 600px; margin: 0 auto; background: #111827; border: 1px solid rgba(255,255,255,0.1); border-radius: 16px; overflow: hidden; color: #F8FAFC;">
                <div style="background: linear-gradient(135deg, #4338CA, #6366F1); padding: 28px; text-align: left;">
                    <span style="background: rgba(255,255,255,0.2); color: white; padding: 4px 10px; border-radius: 999px; font-size: 11px; font-weight: bold; text-transform: uppercase;">Alerta Oficial</span>
                    <h1 style="color: #FFFFFF; margin: 12px 0 6px 0; font-size: 22px;">⚽ La Jornada ({jornada_info}) és a punt de començar!</h1>
                    <p style="color: #C7D2FE; margin: 0; font-size: 14px;">Tots els pronòstics, 6 combinades i el tauler de diners ja estan a la web.</p>
                </div>

                <div style="padding: 24px;">
                    <p style="font-size: 15px; color: #94A3B8; margin-top: 0; line-height: 1.6;">
                        Hola! Les designacions arbitrals del <strong>CTA</strong> i <strong>PGMOL</strong> ja són oficials i el model matemàtic ha actualitzat totes les probabilitats i les cuotes de Winamax.
                    </p>

                    <div style="text-align: center; margin: 28px 0;">
                        <a href="{web_url}" target="_blank" style="background: linear-gradient(135deg, #10B981, #059669); color: #FFFFFF; text-decoration: none; padding: 14px 28px; border-radius: 12px; font-size: 16px; font-weight: bold; display: inline-block; box-shadow: 0 4px 14px rgba(16, 185, 129, 0.4);">
                            👉 Obrir la Web de Prediccions
                        </a>
                    </div>

                    <div style="background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 12px; padding: 16px; margin-bottom: 20px;">
                        <h4 style="margin: 0 0 10px 0; font-size: 14px; color: #F8FAFC;">Què trobaràs a la plataforma web?</h4>
                        <ul style="margin: 0; padding-left: 20px; font-size: 13px; color: #94A3B8; line-height: 1.8;">
                            <li>⚡ <strong>Power Rànquings Elo:</strong> Taula de classificació real ajustada per dificultat.</li>
                            <li>🔮 <strong>Prediccions Partit a Partit:</strong> xG esperat, 1X2, Over/Under 2.5 i àrbitres.</li>
                            <li>💎 <strong>Apostes de Valor (+EV%):</strong> Oportunitats matemàtiques davant Winamax.</li>
                            <li>🎯 <strong>Suite de 6 Combinades:</strong> 2 Segures (25€), 2 Semi (10€) i 2 Arriscades (5€).</li>
                            <li>💰 <strong>Simulador Financer:</strong> 'Què hagués passat si...' amb balanç acumulatiu.</li>
                        </ul>
                    </div>

                    {pnl_html}
                </div>

                <div style="background-color: rgba(0,0,0,0.2); padding: 16px 24px; border-top: 1px solid rgba(255,255,255,0.05); text-align: center; font-size: 12px; color: #64748B;">
                    Creat per Fortià Arumí Casals · Sistema autònom al núvol (GitHub Actions & Vercel)
                </div>
            </div>
        </body>
        </html>
        """

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{sender_name} <{sender_email}>"
        msg["To"] = ", ".join(recipients)
        msg.attach(MIMEText(html, "html", "utf-8"))

        print(f"\n[*] Enviant correu d'avís de la web a {len(recipients)} destinataris...")
        try:
            with smtplib.SMTP(smtp_server, smtp_port) as server:
                if use_tls:
                    server.starttls()
                server.login(sender_email, sender_pw)
                server.sendmail(sender_email, recipients, msg.as_string())
            print(f"   [ÈXIT CORREU] S'ha enviat l'avís de la web correctament a tots els destinataris!")
            return True
        except Exception as e:
            print(f"[!] Error enviant avís per correu: {e}")
            return False

