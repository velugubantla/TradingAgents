"""Daily scheduler for local TradingAgents opportunity runs."""

from __future__ import annotations

import atexit
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any, Callable
from zoneinfo import ZoneInfo
import os

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.db import TradingAgentsDB
from app.runner import AppSettings


class DailyOpportunityScheduler:
    """APScheduler wrapper with a PID lock to avoid duplicate schedulers."""

    def __init__(
        self,
        *,
        settings: AppSettings,
        run_callback: Callable[..., dict[str, Any]],
        db: TradingAgentsDB,
        lock_path: str | Path = "./data/scheduler.pid",
    ):
        self.settings = settings
        self.run_callback = run_callback
        self.db = db
        self.lock_path = Path(lock_path)
        self._lock = Lock()
        self._owns_pid_lock = False
        self._scheduler: BackgroundScheduler | None = None

    @property
    def running(self) -> bool:
        return self._scheduler is not None and bool(self._scheduler.running)

    def start(self) -> bool:
        with self._lock:
            if self.running:
                return True
            if not self._acquire_pid_lock():
                return False

            timezone = ZoneInfo(self.settings.timezone)
            hour, minute = self.settings.run_time_parts()
            scheduler = BackgroundScheduler(timezone=timezone)
            scheduler.add_job(
                self._scheduled_job,
                trigger=CronTrigger(hour=hour, minute=minute, timezone=timezone),
                id="daily-opportunity-run",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=300,
            )
            scheduler.start()
            self._scheduler = scheduler
            atexit.register(self.stop)

        if self.settings.run_on_startup:
            self.trigger_now(source="startup")
        return True

    def stop(self) -> None:
        with self._lock:
            if self._scheduler is not None:
                self._scheduler.shutdown(wait=False)
                self._scheduler = None
            self._release_pid_lock()

    def trigger_now(self, *, source: str = "manual") -> dict[str, Any]:
        return self.run_callback(source=source)

    def _scheduled_job(self) -> dict[str, Any] | None:
        run_date = self.settings.current_run_date()
        if self.db.has_run_for_date(run_date=run_date, tickers=self.settings.tickers):
            return {"status": "skipped", "reason": "already_ran_for_date", "run_date": run_date}
        return self.run_callback(source="scheduled")

    def _acquire_pid_lock(self) -> bool:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        current_pid = os.getpid()

        if self.lock_path.exists():
            try:
                existing_pid = int(self.lock_path.read_text(encoding="utf-8").strip())
            except ValueError:
                existing_pid = -1

            if existing_pid > 0 and _pid_exists(existing_pid):
                return False

        self.lock_path.write_text(f"{current_pid}\n", encoding="utf-8")
        self._owns_pid_lock = True
        return True

    def _release_pid_lock(self) -> None:
        if not self._owns_pid_lock:
            return
        if self.lock_path.exists():
            try:
                lock_pid = int(self.lock_path.read_text(encoding="utf-8").strip())
            except ValueError:
                lock_pid = -1
            if lock_pid == os.getpid():
                self.lock_path.unlink(missing_ok=True)
        self._owns_pid_lock = False


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def next_run_at(run_time: str, timezone: str) -> str:
    """Expose next expected run timestamp for UI display."""
    tz = ZoneInfo(timezone)
    now = datetime.now(tz=tz)
    hour, minute = [int(piece) for piece in run_time.split(":")]
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate = candidate + timedelta(days=1)
    return candidate.isoformat()

