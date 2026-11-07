#!/usr/bin/env python3
"""
scripts/surebet_daemon.py
=========================
Servei autònom de monitoratge continu d'arbitratges esportius (Surebets)
dissenyat per a execució 24/7 en un servidor VPS o contenidor Docker.

Característiques principals:
1. Política de mostreig adaptativa segons la proximitat del partit:
   - > 48h: mostreig cada 60 minuts
   - 6h - 48h: mostreig cada 15 minuts
   - 1h - 6h: mostreig cada 5 minuts
   - < 1h: mostreig intensiu cada 60 segons
   - Partits en viu (Live): mostreig continu cada 10 segons
2. Protecció de concurrència i control de taxa de peticions (rate limiting amb jitter).
3. Sistema de registre rotatiu (RotatingFileHandler) a 'logs/surebet_daemon.log'.
4. Tolerància a fallades amb backoff exponencial davant errors de xarxa o timeouts.
5. Aturada neta (Graceful shutdown) capturant SIGINT i SIGTERM.
6. Health check integrat mitjançant 'data/surebet_health.json'.
"""

import os
import sys
import time
import json
import random
import signal
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from logging.handlers import RotatingFileHandler

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.arbitrage_engine import ArbitrageEngine

# Directoris de treball
DATA_DIR = ROOT / "data"
LOGS_DIR = ROOT / "logs"
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

HEALTH_FILE = DATA_DIR / "surebet_health.json"
SUREBETS_LIVE_FILE = DATA_DIR / "surebets_live.json"
LOG_FILE = LOGS_DIR / "surebet_daemon.log"

# Configuració del logger rotatiu
logger = logging.getLogger("SurebetDaemon")
logger.setLevel(logging.INFO)

formatter = logging.Formatter(
    fmt="[%(asctime)s UTC] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
formatter.converter = time.gmtime  # Registre en UTC

file_handler = RotatingFileHandler(
    LOG_FILE,
    maxBytes=10 * 1024 * 1024,  # 10 MB per arxiu
    backupCount=5,
    encoding="utf-8"
)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)


class SurebetDaemon:
    def __init__(
        self,
        enforce_freshness: bool = True,
        max_odds_age_seconds: Optional[float] = None,
        max_skew_seconds: Optional[float] = None,
        commission_rate: float = 0.0,
        bookmaker_commissions: Optional[dict] = None
    ):
        self.running = True
        self.start_time = time.time()
        self.cycle_count = 0
        self.last_surebets_found = 0
        self.consecutive_errors = 0

        self.engine = ArbitrageEngine(
            enforce_strict_freshness=enforce_freshness,
            max_odds_age_seconds=max_odds_age_seconds,
            max_skew_seconds=max_skew_seconds,
            commission_rate=commission_rate,
            bookmaker_commissions=bookmaker_commissions
        )

        # Registre de senyals per aturada neta
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    def _handle_shutdown(self, signum, frame):
        logger.info(f"Rebut senyal d'aturada ({signum}). Iniciant graceful shutdown...")
        self.running = False

    def compute_polling_interval(self, matches: list) -> int:
        """
        Determina l'interval de mostreig en segons segons el partit més proper:
        - Si hi ha algun partit LIVE: 10s
        - Si el proper partit és < 1h: 60s
        - Si és < 6h: 300s (5 min)
        - Si és < 48h: 900s (15 min)
        - Si és > 48h o no hi ha partits: 3600s (60 min)
        """
        now = datetime.now(timezone.utc)
        min_hours = 9999.0
        has_live = False

        for m in matches:
            if m.get("status") == "LIVE":
                has_live = True
                break
            d_str = m.get("date") or m.get("date_time")
            if not d_str or d_str == "Pendent":
                continue
            try:
                clean_s = str(d_str).strip()
                if "." in clean_s:
                    m_dt = datetime.strptime(clean_s[:16], "%d.%m.%Y %H:%M").replace(tzinfo=timezone.utc)
                elif "-" in clean_s:
                    m_dt = datetime.strptime(clean_s[:16], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                else:
                    continue
                diff_h = (m_dt - now).total_seconds() / 3600.0
                if diff_h >= 0 and diff_h < min_hours:
                    min_hours = diff_h
            except Exception:
                continue

        if has_live:
            return 10
        if min_hours < 1.0:
            return 60
        if min_hours < 6.0:
            return 300
        if min_hours < 48.0:
            return 900
        return 3600

    def load_active_matches(self) -> list:
        """Carrega els partits pendents des de web/data/data.json."""
        data_file = ROOT / "web" / "data" / "data.json"
        if not data_file.exists():
            logger.warning(f"No s'ha trobat {data_file}. Esperant generació inicial...")
            return []

        try:
            with open(data_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            preds = data.get("predictions", {})
            matches = []
            for comp_id, c_data in preds.items():
                for m in c_data.get("matches", []):
                    if m.get("status") in ("SCHEDULED", "LIVE"):
                        matches.append(m)
            return matches
        except Exception as e:
            logger.error(f"Error llegint dades de partits: {e}")
            return []

    def update_health(self, status: str = "HEALTHY"):
        """Escriu el batec d'estat (heartbeat) a data/surebet_health.json."""
        uptime = int(time.time() - self.start_time)
        health_data = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "uptime_seconds": uptime,
            "cycles_completed": self.cycle_count,
            "last_surebets_found": self.last_surebets_found,
            "consecutive_errors": self.consecutive_errors,
            "status": status,
            "pid": os.getpid()
        }
        try:
            with open(HEALTH_FILE, "w", encoding="utf-8") as f:
                json.dump(health_data, f, indent=2)
        except Exception as e:
            logger.error(f"No s'ha pogut desar health check: {e}")

    def run_cycle(self):
        """Executa un cicle complet d'escaneig."""
        self.cycle_count += 1
        matches = self.load_active_matches()

        logger.info(f"--- Cicle #{self.cycle_count} --- Partits actius en seguiment: {len(matches)}")

        if not matches:
            self.update_health(status="HEALTHY")
            return 60

        # Escanejar amb ArbitrageEngine
        report = self.engine.scan_all_matches(matches)
        surebets = report.get("surebets", [])
        near_arbs = report.get("near_arbs", [])
        self.last_surebets_found = len(surebets)

        if surebets:
            logger.info(f"🚨 TROBADES {len(surebets)} SUREBETS AMB RETORN GARANTIT!")
            for sb in surebets:
                logger.info(f"  -> {sb.get('matchup')} | {sb.get('market_name')} | Benefici Net: +{sb.get('profit_pct')}%")
        else:
            logger.info(f"Cap Surebet pura trobada. Oportunitats properes (Near-Arbs): {len(near_arbs)}")

        # Desar dades per al consumidor web o API
        try:
            with open(SUREBETS_LIVE_FILE, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error desant surebets_live.json: {e}")

        self.consecutive_errors = 0
        self.update_health(status="HEALTHY")

        # Calcular interval per al proper cicle
        interval = self.compute_polling_interval(matches)
        jitter = random.uniform(1.0, 3.0)  # Evitar sincronització exacta anti-scraping
        return int(interval + jitter)

    def start(self):
        """Inicia el bucle continu del daemon."""
        logger.info("==================================================")
        logger.info("🚀 SUREBET CONTINUOUS DAEMON INICIAT")
        logger.info(f"Directori base: {ROOT}")
        logger.info(f"Frescor estricta: {self.engine.enforce_strict_freshness} (Max edat: {self.engine.max_odds_age_seconds}s, Max skew: {self.engine.max_skew_seconds}s)")
        logger.info("==================================================")

        while self.running:
            try:
                sleep_secs = self.run_cycle()
                logger.info(f"Propera comprovació en {sleep_secs}s.")
                # Bucle de son interrompible
                slept = 0
                while self.running and slept < sleep_secs:
                    time.sleep(1)
                    slept += 1
            except Exception as e:
                self.consecutive_errors += 1
                logger.error(f"Excepció no controlada al cicle #{self.cycle_count}: {e}", exc_info=True)
                self.update_health(status="DEGRADED")
                # Backoff exponencial amb límit de 120s
                backoff = min(120, 10 * (2 ** min(self.consecutive_errors - 1, 3)))
                logger.warning(f"Esperant {backoff}s per recuperació d'error...")
                time.sleep(backoff)

        logger.info("Daemon aturat netament. Alliberant recursos.")
        self.update_health(status="STOPPED")


if __name__ == "__main__":
    daemon = SurebetDaemon(
        enforce_freshness=True,
        max_odds_age_seconds=300.0,
        max_skew_seconds=60.0
    )
    daemon.start()

