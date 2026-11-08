"""
surebet_daemon.py
=================
Dimoni lleuger i independent de monitoratge continu i automàtic de Surebets (Arbitratge Esportiu).
Integra en temps real les tres cases d'apostes reals:
1. Winamax
2. Bet365
3. Betway

Característiques principals:
- Política de freqüències adaptativa basada en la proximitat dels partits (Zona horària Europe/Madrid).
- Proteccions robustes:
  * Lockfile atòmic per evitar instàncies simultànies (.surebet_daemon.lock).
  * Serialització d'execució (mai execucions solapades).
  * Timeout per scraper.
  * Circuit breaker independent per casa (fallades consecutives no bloquegen les altres cases).
  * Recuperació automàtica quan la casa restablerta torna a respondre.
  * Degradació elegant: funciona amb 2 cases si una 3a falla temporalment.
- Control estricte de frescor:
  * Timestamps independents per cada casa d'apostes.
  * Marques temporals reals (captured_at), mai falsificades.
  * Classificació entre Surebets Fresques, Near-Arbs i Oportunitats amb quotes obsoletes (Stale).
- Actualització atòmica de les dades consumides per la web (web/data/data.json).
"""

import os
import sys
import time
import json
import signal
import atexit
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

# Assegurar codificació UTF-8 a la consola per evitar errors de 'charmap' a Windows
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from datetime import timezone as ZoneInfo  # fallback si Python antic

PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from database.db_manager import DatabaseManager
from engine.arbitrage_engine import ArbitrageEngine
from scraper.winamax_scraper import WinamaxScraper
from scraper.bet365_scraper import Bet365Scraper
from scraper.betway_scraper import BetwayScraper

LOCK_FILE = PROJECT_ROOT / ".surebet_daemon.lock"
WEB_DATA_FILE = PROJECT_ROOT / "web" / "data" / "data.json"
TZ_MADRID = ZoneInfo("Europe/Madrid") if hasattr(ZoneInfo, "__call__") else timezone.utc

# Llindars de circuit breaker
CIRCUIT_BREAKER_MAX_FAILURES = 3
CIRCUIT_BREAKER_COOLDOWN_SECONDS = 600.0  # 10 minuts de suspensió temporal

# Freqüències per defecte (segons) segons l'estat dels partits
POLICY_FREQUENCIES = {
    "NO_UPCOMING": {
        "winamax": 900,   # 15 min
        "betway": 900,    # 15 min
        "bet365": 1800,   # 30 min
        "cycle_min": 900
    },
    "WITHIN_4H": {
        "winamax": 300,   # 5 min
        "betway": 300,    # 5 min
        "bet365": 600,    # 10 min
        "cycle_min": 300
    },
    "WITHIN_2H": {
        "winamax": 180,   # 3 min
        "betway": 180,    # 3 min
        "bet365": 300,    # 5 min
        "cycle_min": 180
    },
    "IMMINENT": {
        "winamax": 60,    # 1 min
        "betway": 90,     # 1.5 min
        "bet365": 180,    # 3 min
        "cycle_min": 60
    }
}


class ProcessLock:
    """Gestió atòmica del fitxer de bloqueig per evitar instàncies simultànies del daemon."""

    def __init__(self, lock_path: Path = LOCK_FILE):
        self.lock_path = lock_path
        self.acquired = False

    def acquire(self, force: bool = False) -> bool:
        pid = os.getpid()
        if self.lock_path.exists() and not force:
            try:
                with open(self.lock_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        data = json.loads(content)
                        old_pid = data.get("pid")
                        # Comprovar si el procés antic continua viu
                        if old_pid and self._is_pid_alive(old_pid):
                            print(f"[!] ERROR: Ja hi ha una instància del surebet_daemon en execució (PID {old_pid}).")
                            return False
            except Exception:
                pass  # Si el fitxer de lock està corromput o orfe, el recreem

        # Crear o sobresriure el lockfile
        try:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.lock_path, "w", encoding="utf-8") as f:
                json.dump({
                    "pid": pid,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "command": " ".join(sys.argv)
                }, f)
            self.acquired = True
            return True
        except Exception as e:
            print(f"[!] Error adquirint lockfile: {e}")
            return False

    def release(self) -> None:
        if self.acquired and self.lock_path.exists():
            try:
                self.lock_path.unlink()
            except Exception:
                pass
            self.acquired = False

    @staticmethod
    def _is_pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        if os.name == "nt":
            import subprocess
            try:
                out = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                    capture_output=True, text=True, timeout=3
                )
                return str(pid) in out.stdout
            except Exception:
                return False
        else:
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False


class CircuitBreaker:
    """Controlador de fallades consecutives, suspensió automàtica i mètriques detallades d'una font d'apostes."""

    def __init__(self, name: str, max_failures: int = CIRCUIT_BREAKER_MAX_FAILURES, cooldown_seconds: float = CIRCUIT_BREAKER_COOLDOWN_SECONDS):
        self.name = name
        self.max_failures = max_failures
        self.cooldown_seconds = cooldown_seconds
        self.consecutive_failures = 0
        self.status = "active"  # "active", "suspended", "error", "updating"
        self.suspended_until: Optional[datetime] = None
        self.last_error: Optional[str] = None
        self.last_success: Optional[datetime] = None

        # Mètriques detallades de frescor i traçabilitat temporal (FASE 1)
        self.last_attempt_status: str = "success"  # "success", "error", "partial"
        self.last_attempt_time: Optional[datetime] = None
        self.query_start: Optional[datetime] = None
        self.query_end: Optional[datetime] = None
        self.http_captured_at: Optional[Any] = None
        self.events_count: int = 0
        self.odds_count: int = 0
        self.partial_details: Optional[str] = None
        self.feed_synced_at: Optional[str] = None
        self.provider_updated_at: Optional[str] = None
        self.verifiable_odds_count: int = 0
        self.unverifiable_odds_count: int = 0
        self.is_historical: bool = False

    def record_attempt_start(self, now: Optional[datetime] = None) -> None:
        self.query_start = now or datetime.now(timezone.utc)

    def record_success(
        self,
        now: Optional[datetime] = None,
        *,
        query_start: Optional[datetime] = None,
        query_end: Optional[datetime] = None,
        http_captured_at: Optional[Any] = None,
        events_count: int = 0,
        odds_count: int = 0,
        feed_synced_at: Optional[str] = None,
        provider_updated_at: Optional[str] = None,
        verifiable_odds_count: int = 0,
        unverifiable_odds_count: int = 0,
        is_historical: bool = False
    ) -> None:
        now_dt = now or datetime.now(timezone.utc)
        self.consecutive_failures = 0
        self.status = "active"
        self.suspended_until = None
        self.last_error = None
        self.last_success = now_dt
        self.last_attempt_status = "success"
        self.last_attempt_time = now_dt
        if query_start:
            self.query_start = query_start
        self.query_end = query_end or now_dt
        self.http_captured_at = http_captured_at or now_dt
        self.events_count = events_count
        self.odds_count = odds_count
        self.partial_details = None
        self.feed_synced_at = feed_synced_at
        self.provider_updated_at = provider_updated_at
        self.verifiable_odds_count = verifiable_odds_count
        self.unverifiable_odds_count = unverifiable_odds_count
        self.is_historical = is_historical

    def record_partial(
        self,
        reason: str = "",
        now: Optional[datetime] = None,
        *,
        query_start: Optional[datetime] = None,
        query_end: Optional[datetime] = None,
        http_captured_at: Optional[Any] = None,
        events_count: int = 0,
        odds_count: int = 0,
        feed_synced_at: Optional[str] = None,
        provider_updated_at: Optional[str] = None,
        verifiable_odds_count: int = 0,
        unverifiable_odds_count: int = 0,
        is_historical: bool = False
    ) -> None:
        now_dt = now or datetime.now(timezone.utc)
        self.consecutive_failures = 0
        self.status = "active"
        self.suspended_until = None
        self.last_error = None
        self.last_success = now_dt
        self.last_attempt_status = "partial"
        self.last_attempt_time = now_dt
        if query_start:
            self.query_start = query_start
        self.query_end = query_end or now_dt
        self.http_captured_at = http_captured_at or now_dt
        self.events_count = events_count
        self.odds_count = odds_count
        self.partial_details = reason
        self.feed_synced_at = feed_synced_at
        self.provider_updated_at = provider_updated_at
        self.verifiable_odds_count = verifiable_odds_count
        self.unverifiable_odds_count = unverifiable_odds_count
        self.is_historical = is_historical

    def record_failure(
        self,
        error_msg: str,
        now: Optional[datetime] = None,
        *,
        query_start: Optional[datetime] = None,
        query_end: Optional[datetime] = None
    ) -> None:
        now_dt = now or datetime.now(timezone.utc)
        self.consecutive_failures += 1
        self.last_error = error_msg
        self.last_attempt_status = "error"
        self.last_attempt_time = now_dt
        if query_start:
            self.query_start = query_start
        self.query_end = query_end or now_dt
        # Important FASE 1: Conservar informació de l'últim monitoratge correcte
        # (self.last_success, self.http_captured_at, self.events_count, self.odds_count)
        if self.consecutive_failures >= self.max_failures:
            self.status = "suspended"
            self.suspended_until = now_dt + timedelta(seconds=self.cooldown_seconds)
            print(f"⚠️  [CIRCUIT BREAKER] {self.name} ha fallat {self.consecutive_failures} cops consecutius.")
            print(f"    Suspès temporalment durant {self.cooldown_seconds/60:.0f} minuts (fins a {self.suspended_until.isoformat()}).")
        else:
            self.status = "error"

    def is_available(self, now: Optional[datetime] = None) -> bool:
        now_dt = now or datetime.now(timezone.utc)
        if self.status == "suspended":
            if self.suspended_until and now_dt >= self.suspended_until:
                print(f"🔄 [CIRCUIT BREAKER] El període de refredament per a {self.name} ha finalitzat. Reintentant...")
                self.status = "active"
                self.suspended_until = None
                return True
            return False
        return True

    def to_dict(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        now_dt = now or datetime.now(timezone.utc)
        age_s = None
        if self.last_success:
            age_s = round((now_dt - self.last_success).total_seconds(), 1)
        
        captured_str = None
        if isinstance(self.http_captured_at, datetime):
            captured_str = self.http_captured_at.isoformat()
        elif self.http_captured_at:
            captured_str = str(self.http_captured_at)

        return {
            "name": self.name,
            "status": self.status,
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error,
            "last_success": self.last_success.isoformat() if self.last_success else None,
            "age_seconds": age_s,
            "suspended_until": self.suspended_until.isoformat() if self.suspended_until else None,
            "last_attempt_status": self.last_attempt_status,
            "last_attempt_time": self.last_attempt_time.isoformat() if self.last_attempt_time else None,
            "query_start": self.query_start.isoformat() if self.query_start else None,
            "query_end": self.query_end.isoformat() if self.query_end else None,
            "http_captured_at": captured_str,
            "events_count": self.events_count,
            "odds_count": self.odds_count,
            "partial_details": self.partial_details,
            "feed_synced_at": self.feed_synced_at,
            "provider_updated_at": self.provider_updated_at,
            "verifiable_odds_count": self.verifiable_odds_count,
            "unverifiable_odds_count": self.unverifiable_odds_count,
            "is_historical": bool(self.is_historical or (age_s is not None and age_s > 300.0))
        }


class SurebetDaemon:
    """Orquestrador del monitoratge continu de quotes i càlcul de Surebets."""

    def __init__(
        self,
        db: Optional[DatabaseManager] = None,
        enforce_strict_freshness: bool = True,
        max_odds_age_seconds: float = 60.0,
        max_skew_seconds: float = 30.0,
        dry_run: bool = False,
        offline: bool = False,
        once: bool = False,
        export_live_path: Optional[str] = None,
        cost_guard: Optional[Any] = None
    ):
        self.db = db or DatabaseManager()
        self.enforce_strict_freshness = enforce_strict_freshness
        self.max_odds_age_seconds = max_odds_age_seconds
        self.max_skew_seconds = max_skew_seconds
        self.dry_run = dry_run
        self.offline = offline
        self.once = once
        self.export_live_path = export_live_path
        self.cost_guard = cost_guard

        # Motors de surebets: un amb frescor estricta per a quotes executables
        # i un altre per detectar near-arbs i oportunitats amb quotes obsoletes
        self.strict_engine = ArbitrageEngine(
            min_profit_threshold=0.0,
            max_odds_age_seconds=max_odds_age_seconds,
            max_skew_seconds=max_skew_seconds,
            enforce_strict_freshness=True
        )
        self.broad_engine = ArbitrageEngine(
            min_profit_threshold=-2.5,
            max_odds_age_seconds=max_odds_age_seconds,
            max_skew_seconds=max_skew_seconds,
            enforce_strict_freshness=False
        )

        # Scrapers
        self.winamax_scraper = WinamaxScraper(db=self.db, offline=offline)
        self.bet365_scraper = Bet365Scraper(offline=offline)
        self.betway_scraper = BetwayScraper(db=self.db, offline=offline)

        # Circuit breakers per cada casa
        self.breakers = {
            "winamax": CircuitBreaker("Winamax"),
            "bet365": CircuitBreaker("Bet365"),
            "betway": CircuitBreaker("Betway")
        }

        # Timestamps d'última execució de cada scraper
        self.last_run_times: Dict[str, Optional[datetime]] = {
            "winamax": None,
            "bet365": None,
            "betway": None
        }

        self.running = False

        # Control de sessió cancellable i cicle de vida (FASE 2)
        # Transicions d'estat: PENDING -> RUNNING -> CANCEL_REQUESTED -> CANCELLED
        # Finalització natural sense cancel·lació: RUNNING -> COMPLETED
        self.session_id: Optional[str] = None
        self.session_status: str = "PENDING"
        self.session_start_time: Optional[float] = None
        self.max_duration_seconds: Optional[int] = None
        self.cancel_requested: bool = False
        self.session_cycle_count: int = 0
        self.last_bookmakers_status: Dict[str, Any] = {}
        self.last_generation_iso: Optional[str] = None
        self.evaluated_matches_count: int = 0

    def start_session(self, session_id: str, max_duration_seconds: Optional[int] = None) -> None:
        """Inicia una nova sessió de monitoratge amb límits temporals configurables."""
        self.session_id = session_id
        self.session_status = "RUNNING"
        self.session_start_time = time.time()
        self.max_duration_seconds = max_duration_seconds
        self.cancel_requested = False
        self.session_cycle_count = 0
        print(f"[*] Sessió {session_id} iniciada amb estat RUNNING (durada màxima: {max_duration_seconds}s).")

    def get_bookmakers_status(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """Retorna l'estat consolidat dels Circuit Breakers per a cada bookmaker."""
        now_dt = now or datetime.now(timezone.utc)
        return {
            "winamax": self.breakers["winamax"].to_dict(now_dt),
            "bet365": self.breakers["bet365"].to_dict(now_dt),
            "betway": self.breakers["betway"].to_dict(now_dt)
        }

    def request_cancellation(self) -> None:
        """Sol·licita la cancel·lació cooperativa de la sessió en curs."""
        self.cancel_requested = True
        self.session_status = "CANCEL_REQUESTED"
        print(f"⚠️  Cancel·lació sol·licitada per a la sessió {self.session_id}. Estat: CANCEL_REQUESTED.")

    def check_cancellation(self) -> bool:
        """Comprova si s'ha sol·licitat la cancel·lació i aplica la transició atòmica a CANCELLED."""
        if self.cancel_requested:
            self.session_status = "CANCELLED"
            self.running = False
            print(f"🛑 Sessió {self.session_id} cancel·lada correctament. Estat final: CANCELLED.")
            return True
        return False

    def check_duration_limit(self) -> bool:
        """Comprova si s'ha assolit la durada màxima de la sessió."""
        if self.max_duration_seconds and self.session_start_time:
            elapsed = time.time() - self.session_start_time
            if elapsed >= self.max_duration_seconds:
                # IMPORTANT: Si hi ha una cancel·lació demanada, preval CANCELLED
                if self.cancel_requested:
                    self.session_status = "CANCELLED"
                else:
                    self.session_status = "COMPLETED"
                self.running = False
                print(f"🏁 Sessió {self.session_id} ha assolit la durada màxima ({elapsed:.1f}s >= {self.max_duration_seconds}s). Estat: {self.session_status}.")
                return True
        return False

    def get_session_state(self) -> Dict[str, Any]:
        """Retorna l'estat actual de la sessió per a telemetria web i API."""
        elapsed = round(time.time() - self.session_start_time, 1) if self.session_start_time else 0.0
        remaining = None
        if self.max_duration_seconds and self.session_start_time:
            remaining = max(0.0, round(self.max_duration_seconds - elapsed, 1))
        return {
            "session_id": self.session_id,
            "status": self.session_status,
            "cancel_requested": self.cancel_requested,
            "elapsed_seconds": elapsed,
            "remaining_seconds": remaining,
            "max_duration_seconds": self.max_duration_seconds
        }

    # ----- Avaluació de la finestra de partits i càlcul de freqüències -----

    @staticmethod
    def parse_match_datetime(dt_val: Any, default_tz: Any = TZ_MADRID) -> Optional[datetime]:
        """
        Parseja de manera robusta una data/hora de partit emmagatzemada a la BD o scrapers.
        Assumeix Europe/Madrid per defecte si no té zona horària explícita,
        i retorna sempre un objecte datetime amb tzinfo=timezone.utc.
        Admet formats ISO, formats amb barra (%d/%m/%Y, %Y-%m-%d) i formats amb punts (%d.%m.%Y).
        Si la data és nul·la, buida o invàlida, retorna None sense aixecar cap excepció.
        """
        if not dt_val:
            return None
        if isinstance(dt_val, datetime):
            if dt_val.tzinfo is None:
                return dt_val.replace(tzinfo=default_tz).astimezone(timezone.utc)
            return dt_val.astimezone(timezone.utc)

        s = str(dt_val).strip()
        if not s or s.lower() in ("tbd", "pendent", "none", "null", "postponed"):
            return None

        # Format ISO (ex: "2026-11-08T21:00:00" o "2026-11-08T20:00:00Z")
        iso_str = s[:-1] + "+00:00" if s.endswith("Z") else s
        try:
            dt_obj = datetime.fromisoformat(iso_str)
            if dt_obj.tzinfo is None:
                dt_obj = dt_obj.replace(tzinfo=default_tz)
            return dt_obj.astimezone(timezone.utc)
        except Exception:
            pass

        # Formats comuns amb hora o data (punts, barres o guions)
        for fmt in (
            "%d.%m.%Y %H:%M",
            "%d.%m.%Y %H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%d/%m/%Y %H:%M",
            "%d/%m/%Y %H:%M:%S",
            "%Y-%m-%d",
            "%d.%m.%Y",
            "%d/%m/%Y",
        ):
            try:
                dt_obj = datetime.strptime(s, fmt)
                if dt_obj.tzinfo is None:
                    dt_obj = dt_obj.replace(tzinfo=default_tz)
                return dt_obj.astimezone(timezone.utc)
            except Exception:
                pass

        return None

    def get_upcoming_matches_window(self, now_utc: Optional[datetime] = None) -> Tuple[List[Dict[str, Any]], str, float]:
        """
        Consulta els partits programats a la BD i en determina la finestra de proximitat:
        - IMMINENT: Partits que comencen en < 1 hora.
        - WITHIN_2H: Partits que comencen en 1 a 2 hores.
        - WITHIN_4H: Partits que comencen en 2 a 4 hores.
        - NO_UPCOMING: Sense partits en les pròximes 4 hores.
        """
        now = now_utc or datetime.now(timezone.utc)
        conn = self.db.get_connection()
        c = conn.cursor()

        # Obtenir tots els partits programats
        query = """
            SELECT m.id, m.competition_id, m.date_time, ht.name, at.name, m.status
            FROM matches m
            JOIN teams ht ON m.home_team_id = ht.id
            JOIN teams at ON m.away_team_id = at.id
            WHERE m.status = 'SCHEDULED'
            ORDER BY m.date_time ASC
        """
        try:
            c.execute(query)
            rows = c.fetchall()
        except Exception as e:
            print(f"[!] Error consultant partits programats: {e}")
            rows = []

        scheduled_matches = []
        min_hours_until = 999.0

        for r in rows:
            m_id, comp_id, dt_str, h_name, a_name, status = r
            # Parsejar data/hora assumint Europe/Madrid si no té timezone
            m_dt_utc = self.parse_match_datetime(dt_str)

            if m_dt_utc:
                diff_hours = (m_dt_utc - now).total_seconds() / 3600.0
                # Només ens interessen partits prepartit (encara no començats o començats fa menys de 10 min)
                if diff_hours >= -0.15:
                    scheduled_matches.append({
                        "id": m_id,
                        "competition_id": comp_id,
                        "date_time": dt_str,
                        "kickoff_utc": m_dt_utc,
                        "home_team": {"name": h_name},
                        "away_team": {"name": a_name},
                        "diff_hours": diff_hours
                    })
                    if diff_hours < min_hours_until:
                        min_hours_until = diff_hours

        # Determinar la categoria de freqüència
        category = self.get_frequency_category(min_hours_until)

        return scheduled_matches, category, min_hours_until

    @staticmethod
    def get_frequency_category(min_hours_until: float) -> str:
        """Determina la categoria de freqüència segons les hores restants per al proper partit."""
        if min_hours_until <= 1.0:
            return "IMMINENT"
        elif min_hours_until <= 2.0:
            return "WITHIN_2H"
        elif min_hours_until <= 4.0:
            return "WITHIN_4H"
        return "NO_UPCOMING"

    @staticmethod
    def determine_policy(closest_kickoff_utc: Optional[datetime], now_utc: datetime) -> Dict[str, Any]:
        """Calcula la política de freqüències a partir del proper inici i la data actual."""
        if not closest_kickoff_utc:
            return POLICY_FREQUENCIES["NO_UPCOMING"]
        diff_hours = (closest_kickoff_utc - now_utc).total_seconds() / 3600.0
        category = SurebetDaemon.get_frequency_category(diff_hours)
        return POLICY_FREQUENCIES[category]

    # ----- Execució d'un cicle de monitoratge -----

    def run_cycle(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Executa un cicle complet del daemon:
        1. Identifica partits a monitoritzar i finestra de freqüència.
        2. Executa els scrapers disponibles respectant circuit breakers.
        3. Recull i unifica les quotes de Winamax, Bet365 i Betway.
        4. Executa el motor de surebets amb validació de frescor.
        5. Actualitza les dades de la web.
        """
        cycle_start = time.time()
        self.session_cycle_count += 1
        is_manual_session = bool(self.session_id)
        now_utc = now or datetime.now(timezone.utc)
        now_madrid = now_utc.astimezone(TZ_MADRID)
        now_iso = now_utc.isoformat()

        print("\n" + "=" * 70)
        print(f"🕒 [SUREBET DAEMON] Cicle #{self.session_cycle_count} iniciat: {now_madrid.strftime('%Y-%m-%d %H:%M:%S')} (Europe/Madrid)")
        print("=" * 70)

        # 0. Vigilància econòmica (CostGuard)
        if self.cost_guard is not None:
            can_run, reason = self.cost_guard.can_restart()
            if not can_run:
                print(f"[!] ATURADA ECONÒMICA PREVENTIVA: {reason}. Cicle cancel·lat.")
                self.running = False
                return {"error": "ECONOMIC_SHUTDOWN", "reason": reason}

        # 1. Finestra de partits
        matches, freq_category, min_hours = self.get_upcoming_matches_window(now_utc)
        policy = POLICY_FREQUENCIES[freq_category]
        print(f"📊 Finestra: {freq_category} (Pròxim partit en: {min_hours:.1f}h) | Partits programats: {len(matches)}")

        # 2. Avaluar quins scrapers han d'executar-se en aquest cicle
        scrapers_to_run = []
        for bk_name in ("winamax", "bet365", "betway"):
            breaker = self.breakers[bk_name]
            if not breaker.is_available(now_utc):
                print(f"   • {bk_name.capitalize()}: SUSPÈS (Circuit Breaker actiu)")
                continue

            # Si és una cerca manual iniciada des del web, forcem la consulta de totes les cases
            # en el primer cicle per garantir quotes fresques sense esperar intervals de 15/30 minuts.
            if is_manual_session and self.session_cycle_count <= 1:
                scrapers_to_run.append(bk_name)
                print(f"   • {bk_name.capitalize()}: FORÇAT PER CERCA MANUAL EN LÍNIA")
                continue

            # Comprovar si toca per política de freqüència de fons
            last_run = self.last_run_times[bk_name]
            interval_s = policy.get(bk_name, 300)
            if last_run is None or (now_utc - last_run).total_seconds() >= interval_s:
                scrapers_to_run.append(bk_name)
                print(f"   • {bk_name.capitalize()}: TOCA ACTUALITZAR (interval: {interval_s/60:.1f}m)")
            else:
                elapsed = (now_utc - last_run).total_seconds()
                print(f"   • {bk_name.capitalize()}: FRESC ({elapsed:.0f}s / {interval_s:.0f}s)")

        # 3. Recollir quotes partit a partit
        # Prioritzem partits en menys de 48h; si n'hi ha menys de 15, completem amb els següents fins a 15 partits
        # per evitar que períodes entre jornades o amb pocs partits imminents analitzin només 1 partit.
        imminent = [m for m in matches if m.get("diff_hours", 999) <= 48.0]
        if len(imminent) >= 15:
            matches_to_eval = imminent[:15]
        else:
            matches_to_eval = matches[:15]

        evaluated_matches_flat: List[Dict[str, Any]] = []

        # Estadístiques d'execució d'aquest cicle per a cada casa (FASE 1 & FASE 4)
        cycle_stats = {
            "winamax": {"events": 0, "odds": 0, "captured_at": None, "errors": [], "start": datetime.now(timezone.utc), "feed_synced_at": None, "provider_updated_at": None, "verifiable_odds": 0, "unverifiable_odds": 0, "is_historical": False},
            "bet365": {"events": 0, "odds": 0, "captured_at": None, "errors": [], "start": datetime.now(timezone.utc), "feed_synced_at": None, "provider_updated_at": None, "verifiable_odds": 0, "unverifiable_odds": 0, "is_historical": False},
            "betway": {"events": 0, "odds": 0, "captured_at": None, "errors": [], "start": datetime.now(timezone.utc), "feed_synced_at": None, "provider_updated_at": None, "verifiable_odds": 0, "unverifiable_odds": 0, "is_historical": False},
        }

        if self.check_cancellation():
            return {}

        # Pre-sincronització eficient del feed oficial MoviOdds si Bet365 s'ha d'actualitzar
        if "bet365" in scrapers_to_run and not self.offline:
            try:
                from scraper.moviodds_client import MoviOddsClient
                m_cli = MoviOddsClient.get_instance()
                if not m_cli.has_feed():
                    print("   • Bet365: Sincronitzant feed oficial en viu de MoviOdds abans d'avaluar partits...")
                    m_cli.fetch_feed()
                if m_cli.has_feed():
                    cycle_stats["bet365"]["feed_synced_at"] = m_cli._last_fetch_iso
            except Exception as e:
                print(f"   • Bet365: Avís pre-fetch MoviOdds: {e}")

        for m in matches_to_eval:
            if self.check_cancellation():
                break
            h_name = m["home_team"]["name"]
            a_name = m["away_team"]["name"]
            comp_id = m.get("competition_id", "LALIGA")
            dt_str = m.get("date_time", "")

            m_record: Dict[str, Any] = {
                "id": m.get("id"),
                "competition_id": comp_id,
                "date": dt_str,
                "home_team": {"name": h_name},
                "away_team": {"name": a_name},
                "status": "SCHEDULED"
            }

            # 3.1 Winamax
            if "winamax" in scrapers_to_run or self.breakers["winamax"].status == "active":
                try:
                    w_odds = self.winamax_scraper.get_match_odds(h_name, a_name, competition_id=comp_id, now=now_utc)
                    if w_odds and w_odds.get("matched"):
                        cycle_stats["winamax"]["events"] += 1
                        cnt = 0
                        if w_odds.get("1X2"):
                            m_record["odds_1x2_winamax"] = w_odds["1X2"]
                            m_record["market_id_1x2_winamax"] = w_odds.get("match_id")
                            cnt += len(w_odds["1X2"])
                        if w_odds.get("double_chance"):
                            m_record["odds_dc_winamax"] = w_odds["double_chance"]
                            cnt += len(w_odds["double_chance"])
                        if w_odds.get("over_under_2_5"):
                            m_record["odds_ou25_winamax"] = w_odds["over_under_2_5"]
                            cnt += len(w_odds["over_under_2_5"])
                        if w_odds.get("btts"):
                            m_record["odds_btts_winamax"] = w_odds["btts"]
                            cnt += len(w_odds["btts"])
                        cycle_stats["winamax"]["odds"] += cnt
                        cycle_stats["winamax"]["verifiable_odds"] += cnt
                        cap = w_odds.get("captured_at")
                        if cap:
                            cycle_stats["winamax"]["captured_at"] = cap
                            cycle_stats["winamax"]["provider_updated_at"] = cap
                        m_record["odds_winamax_captured_at"] = cap
                        m_record["odds_winamax_polled_at"] = cap
                        m_record["odds_winamax_provider_updated_at"] = cap
                        m_record["odds_winamax_origin_freshness"] = "CONFIRMED"
                        m_record["url_winamax"] = w_odds.get("url")
                        m_record["odds_winamax"] = w_odds
                except Exception as e:
                    cycle_stats["winamax"]["errors"].append(str(e))

            # 3.2 Bet365
            if "bet365" in scrapers_to_run or self.breakers["bet365"].status == "active":
                try:
                    b_odds = self.bet365_scraper.get_match_odds(h_name, a_name, competition_id=comp_id, match_date_time=dt_str, now=now_utc)
                    if b_odds and b_odds.get("matched"):
                        cycle_stats["bet365"]["events"] += 1
                        cnt = 0
                        if b_odds.get("1X2"):
                            m_record["odds_1x2_bet365"] = b_odds["1X2"]
                            m_record["market_id_1x2_bet365"] = b_odds.get("match_id")
                            cnt += len(b_odds["1X2"])
                        if b_odds.get("double_chance"):
                            m_record["odds_dc_bet365"] = b_odds["double_chance"]
                            cnt += len(b_odds["double_chance"])
                        if b_odds.get("over_under_2_5"):
                            m_record["odds_ou25_bet365"] = b_odds["over_under_2_5"]
                            cnt += len(b_odds["over_under_2_5"])
                        if b_odds.get("btts"):
                            m_record["odds_btts_bet365"] = b_odds["btts"]
                            cnt += len(b_odds["btts"])
                        cycle_stats["bet365"]["odds"] += cnt
                        cap = b_odds.get("captured_at")
                        if cap:
                            cycle_stats["bet365"]["captured_at"] = cap
                        if b_odds.get("feed_synced_at"):
                            cycle_stats["bet365"]["feed_synced_at"] = b_odds.get("feed_synced_at")
                        if b_odds.get("provider_updated_at"):
                            cycle_stats["bet365"]["provider_updated_at"] = b_odds.get("provider_updated_at")
                        cycle_stats["bet365"]["verifiable_odds"] += b_odds.get("verifiable_odds_count", 0)
                        cycle_stats["bet365"]["unverifiable_odds"] += b_odds.get("unverifiable_odds_count", 0)
                        m_record["odds_bet365_captured_at"] = cap
                        m_record["odds_bet365_polled_at"] = b_odds.get("polled_at")
                        m_record["odds_bet365_provider_updated_at"] = b_odds.get("provider_updated_at")
                        m_record["odds_bet365_origin_freshness"] = b_odds.get("origin_freshness", "UNKNOWN")
                        m_record["url_bet365"] = b_odds.get("url")
                        m_record["odds_bet365"] = b_odds
                except Exception as e:
                    cycle_stats["bet365"]["errors"].append(str(e))

            # 3.3 Betway
            if "betway" in scrapers_to_run or self.breakers["betway"].status == "active":
                try:
                    bw_odds = self.betway_scraper.get_match_odds(h_name, a_name, competition_id=comp_id, match_date_time=dt_str, now=now_utc)
                    if bw_odds and bw_odds.get("matched"):
                        cycle_stats["betway"]["events"] += 1
                        cnt = 0
                        if bw_odds.get("1X2"):
                            m_record["odds_1x2_betway"] = bw_odds["1X2"]
                            m_record["market_id_1x2_betway"] = bw_odds.get("market_id_1x2")
                            cnt += len(bw_odds["1X2"])
                        if bw_odds.get("double_chance"):
                            m_record["odds_dc_betway"] = bw_odds["double_chance"]
                            cnt += len(bw_odds["double_chance"])
                        if bw_odds.get("over_under_2_5"):
                            m_record["odds_ou25_betway"] = bw_odds["over_under_2_5"]
                            cnt += len(bw_odds["over_under_2_5"])
                        if bw_odds.get("btts"):
                            m_record["odds_btts_betway"] = bw_odds["btts"]
                            cnt += len(bw_odds["btts"])
                        cycle_stats["betway"]["odds"] += cnt
                        cycle_stats["betway"]["verifiable_odds"] += cnt
                        cap = bw_odds.get("captured_at")
                        if cap:
                            cycle_stats["betway"]["captured_at"] = cap
                            cycle_stats["betway"]["provider_updated_at"] = cap
                        m_record["odds_betway_captured_at"] = cap
                        m_record["odds_betway_polled_at"] = cap
                        m_record["odds_betway_provider_updated_at"] = cap
                        m_record["odds_betway_origin_freshness"] = "CONFIRMED"
                        m_record["url_betway"] = bw_odds.get("url")
                        m_record["odds_betway"] = bw_odds
                except Exception as e:
                    cycle_stats["betway"]["errors"].append(str(e))

            evaluated_matches_flat.append(m_record)

        # Consolidar mètriques de cicle a cada Circuit Breaker (FASE 1 & FASE 4)
        cycle_end = datetime.now(timezone.utc)
        for bk_name in ("winamax", "bet365", "betway"):
            if bk_name in scrapers_to_run:
                st = cycle_stats[bk_name]
                if st["errors"] and st["events"] == 0:
                    self.breakers[bk_name].record_failure(
                        st["errors"][0],
                        now_utc,
                        query_start=st["start"],
                        query_end=cycle_end
                    )
                elif st["errors"] and st["events"] > 0:
                    self.breakers[bk_name].record_partial(
                        reason=f"{len(st['errors'])} errors puntuals en partits",
                        now=now_utc,
                        query_start=st["start"],
                        query_end=cycle_end,
                        http_captured_at=st["captured_at"] or now_utc,
                        events_count=st["events"],
                        odds_count=st["odds"],
                        feed_synced_at=st.get("feed_synced_at"),
                        provider_updated_at=st.get("provider_updated_at"),
                        verifiable_odds_count=st.get("verifiable_odds", 0),
                        unverifiable_odds_count=st.get("unverifiable_odds", 0),
                        is_historical=st.get("is_historical", False)
                    )
                elif st["events"] > 0:
                    self.breakers[bk_name].record_success(
                        now_utc,
                        query_start=st["start"],
                        query_end=cycle_end,
                        http_captured_at=st["captured_at"] or now_utc,
                        events_count=st["events"],
                        odds_count=st["odds"],
                        feed_synced_at=st.get("feed_synced_at"),
                        provider_updated_at=st.get("provider_updated_at"),
                        verifiable_odds_count=st.get("verifiable_odds", 0),
                        unverifiable_odds_count=st.get("unverifiable_odds", 0),
                        is_historical=st.get("is_historical", False)
                    )
                else:
                    self.breakers[bk_name].record_partial(
                        reason="Cap partit amb quotes disponibles en aquesta finestra",
                        now=now_utc,
                        query_start=st["start"],
                        query_end=cycle_end,
                        http_captured_at=st["captured_at"] or now_utc,
                        events_count=0,
                        odds_count=0,
                        feed_synced_at=st.get("feed_synced_at"),
                        provider_updated_at=st.get("provider_updated_at"),
                        verifiable_odds_count=st.get("verifiable_odds", 0),
                        unverifiable_odds_count=st.get("unverifiable_odds", 0),
                        is_historical=st.get("is_historical", False)
                    )

        # Actualitzar marques temporals d'última execució dels scrapers que han corregut
        for bk in scrapers_to_run:
            self.last_run_times[bk] = now_utc

        # 4. Escaneig de Surebets amb el motor matemàtic
        print(f"🔍 Escanejant oportunitats d'arbitratge per a {len(evaluated_matches_flat)} partits...")

        # 4.1 Escaneig ampli amb política de 30 minuts
        broad_report = self.broad_engine.scan_all_matches(evaluated_matches_flat, now=now_utc)
        all_found = broad_report.get("all_opportunities", [])

        # 4.2 Classificació estricta en 4 nivells de frescor (fins a 30 minuts)
        verified_fresh_surebets: List[Dict[str, Any]] = []
        potentially_stale_surebets: List[Dict[str, Any]] = []
        recent_historical_surebets: List[Dict[str, Any]] = []
        near_arbs: List[Dict[str, Any]] = []

        for arb in all_found:
            tier = arb.get("freshness_tier")
            is_surebet = arb.get("is_surebet", False)
            if is_surebet:
                if tier == ArbitrageEngine.TIER_VERIFIED_FRESH:
                    verified_fresh_surebets.append(arb)
                elif tier == ArbitrageEngine.TIER_POTENTIALLY_STALE:
                    potentially_stale_surebets.append(arb)
                elif tier == ArbitrageEngine.TIER_RECENT_HISTORICAL:
                    recent_historical_surebets.append(arb)
            else:
                if tier != ArbitrageEngine.TIER_EXCLUDED:
                    near_arbs.append(arb)

        verified_fresh_surebets.sort(key=lambda x: x.get("profit_pct", 0.0), reverse=True)
        potentially_stale_surebets.sort(key=lambda x: x.get("profit_pct", 0.0), reverse=True)
        recent_historical_surebets.sort(key=lambda x: x.get("profit_pct", 0.0), reverse=True)
        near_arbs.sort(key=lambda x: x.get("profit_pct", -999.0), reverse=True)

        max_profit = verified_fresh_surebets[0]["profit_pct"] if verified_fresh_surebets else (
            potentially_stale_surebets[0]["profit_pct"] if potentially_stale_surebets else (
                recent_historical_surebets[0]["profit_pct"] if recent_historical_surebets else (
                    near_arbs[0]["profit_pct"] if near_arbs else 0.0
                )
            )
        )
        avg_profit = round(sum(a["profit_pct"] for a in verified_fresh_surebets) / len(verified_fresh_surebets), 2) if verified_fresh_surebets else 0.0

        # Estat consolidat de les cases d'apostes amb timestamps independents
        bookmakers_status = {
            "winamax": self.breakers["winamax"].to_dict(now_utc),
            "bet365": self.breakers["bet365"].to_dict(now_utc),
            "betway": self.breakers["betway"].to_dict(now_utc)
        }
        self.last_bookmakers_status = bookmakers_status
        self.last_generation_iso = now_iso
        self.evaluated_matches_count = len(evaluated_matches_flat)

        arbitrage_payload = {
            "summary": {
                "total_surebets": len(verified_fresh_surebets),
                "total_verified_fresh": len(verified_fresh_surebets),
                "total_potentially_stale": len(potentially_stale_surebets),
                "total_recent_historical": len(recent_historical_surebets),
                "total_stale_surebets": len(potentially_stale_surebets) + len(recent_historical_surebets),
                "total_near_arbs": len(near_arbs),
                "max_profit_pct": max_profit,
                "avg_profit_pct": avg_profit,
                "evaluated_matches": len(evaluated_matches_flat),
                "frequency_category": freq_category,
                "min_hours_until_kickoff": round(min_hours, 2),
                "enforce_strict_freshness": self.enforce_strict_freshness
            },
            "surebets": verified_fresh_surebets,
            "verified_fresh_surebets": verified_fresh_surebets,
            "potentially_stale_surebets": potentially_stale_surebets,
            "recent_historical_surebets": recent_historical_surebets,
            "stale_surebets": potentially_stale_surebets + recent_historical_surebets,
            "near_arbs": near_arbs[:10],
            "all_opportunities": verified_fresh_surebets + potentially_stale_surebets + recent_historical_surebets + near_arbs[:10],
            "bookmakers_status": bookmakers_status,
            "session": self.get_session_state(),
            "last_generation": now_iso,
            "cycle_duration_seconds": round(time.time() - cycle_start, 2),
            "next_cycle_seconds": policy["cycle_min"]
        }

        print(f"✅ Resultats: {len(verified_fresh_surebets)} Verificades (≤60s) | {len(potentially_stale_surebets)} Potencialment Desactualitzades (1-5m) | {len(recent_historical_surebets)} Històriques Recents (5-30m) | {len(near_arbs)} Near-Arbs")
        print(f"⏱️  Cicle completat en {arbitrage_payload['cycle_duration_seconds']}s. Proper cicle en {policy['cycle_min']}s.")
        if not scrapers_to_run:
            print(f"ℹ️  [Freqüència] Reavaluació en memòria ({arbitrage_payload['cycle_duration_seconds']}s): Scrapers en repòs per política de refresc ({policy['cycle_min']}s).")

        # 5. Actualització atòmica a web/data/data.json
        if not self.dry_run:
            self._update_web_data(arbitrage_payload, now_iso)

        return arbitrage_payload

    def _update_web_data(self, arbitrage_payload: Dict[str, Any], now_iso: str) -> None:
        """Actualitza de forma atòmica la secció arbitrage a web/data/data.json o al fitxer separat de live."""
        if self.export_live_path:
            try:
                p = Path(self.export_live_path)
                p.parent.mkdir(parents=True, exist_ok=True)
                live_payload = {
                    "metadata": {
                        "last_surebet_scan": now_iso,
                        "export_mode": "LIVE_ISOLATED"
                    },
                    "arbitrage": arbitrage_payload,
                    "cost_guard": self.cost_guard.get_public_web_status() if self.cost_guard else {
                        "service_status": "OPERATIONAL",
                        "is_active": True,
                        "last_verified_utc": now_iso,
                        "mode": "LIVE"
                    }
                }
                tmp_file = p.with_suffix(".tmp")
                with open(tmp_file, "w", encoding="utf-8") as f:
                    json.dump(live_payload, f, indent=2, ensure_ascii=False)
                tmp_file.replace(p)
                print(f"💾 Dades en viu exportades atòmicament a {self.export_live_path}")
                return
            except Exception as e:
                print(f"[!] Error exportant a {self.export_live_path}: {e}")
                return

        if not WEB_DATA_FILE.exists():
            return
        try:
            with open(WEB_DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            data["arbitrage"] = arbitrage_payload
            meta = data.setdefault("metadata", {})
            meta["last_surebet_scan"] = now_iso

            tmp_file = WEB_DATA_FILE.with_suffix(".tmp")
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            tmp_file.replace(WEB_DATA_FILE)
            print("💾 web/data/data.json actualitzat correctament.")
        except Exception as e:
            print(f"[!] Error actualitzant web/data/data.json: {e}")

    # ----- Bucle continu del daemon -----

    def start(self, once: bool = False, interval_override: Optional[int] = None) -> None:
        """Inicia el bucle d'execució continu del daemon amb suport per a cancel·lació cooperativa."""
        self.running = True
        if self.session_status == "PENDING":
            self.session_status = "RUNNING"
            self.session_start_time = time.time()
        print("[*] Iniciant Surebet Daemon persistent...")

        while self.running:
            if self.check_cancellation():
                break
            if self.check_duration_limit():
                break
            try:
                res = self.run_cycle()
                if self.check_cancellation():
                    break
                if self.check_duration_limit():
                    break
                if once:
                    if not self.cancel_requested:
                        self.session_status = "COMPLETED"
                    break

                sleep_s = interval_override if interval_override is not None else res.get("next_cycle_seconds", 300)
                print(f"💤 Esperant {sleep_s} segons fins al següent cicle...\n")

                # Dormir en intervals petits de 1s per permetre cancel·lació cooperativa immediata
                for _ in range(int(sleep_s)):
                    if not self.running or self.cancel_requested:
                        break
                    if self.max_duration_seconds and self.session_start_time:
                        if time.time() - self.session_start_time >= self.max_duration_seconds:
                            break
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\n[!] Aturada manual del Surebet Daemon.")
                self.session_status = "CANCELLED"
                self.running = False
                break
            except Exception as e:
                print(f"[!] Error inesperat al bucle del daemon: {e}")
                if once:
                    break
                time.sleep(5)

        if self.session_status == "CANCEL_REQUESTED":
            self.session_status = "CANCELLED"
        elif self.session_status == "RUNNING" and not self.cancel_requested:
            self.session_status = "COMPLETED"


def main():
    parser = argparse.ArgumentParser(description="Surebet Daemon - Monitoratge automàtic d'arbitratge esportiu.")
    parser.add_argument("--once", action="store_true", help="Executa un sol cicle i finalitza.")
    parser.add_argument("--interval", type=int, default=None, help="Sobresriu l'interval d'espera en segons.")
    parser.add_argument("--force", action="store_true", help="Força l'adquisició del lockfile si ha quedat orfe.")
    parser.add_argument("--dry-run", action="store_true", help="No escriu a web/data/data.json.")
    parser.add_argument("--offline", action="store_true", help="Mode offline (utilitza dades locals de memòria cau).")
    parser.add_argument("--lax-freshness", action="store_true", help="Desactiva l'exclusió estricta de quotes caducades.")
    parser.add_argument("--max-age", type=float, default=60.0, help="Edat màxima d'una quota en segons (defecte: 60s).")
    parser.add_argument("--max-skew", type=float, default=30.0, help="Decalatge màxim entre potes en segons (defecte: 30s).")
    parser.add_argument("--export-live", type=str, default=None, help="Escriu les dades en viu a un fitxer JSON separat (ex: surebets_live.json).")

    args = parser.parse_args()

    lock = ProcessLock()
    if not lock.acquire(force=args.force):
        sys.exit(1)

    def handle_exit(signum=None, frame=None):
        lock.release()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)
    atexit.register(lock.release)

    daemon = SurebetDaemon(
        enforce_strict_freshness=not args.lax_freshness,
        max_odds_age_seconds=args.max_age,
        max_skew_seconds=args.max_skew,
        dry_run=args.dry_run,
        offline=args.offline,
        export_live_path=args.export_live
    )

    daemon.start(once=args.once, interval_override=args.interval)


if __name__ == "__main__":
    main()
