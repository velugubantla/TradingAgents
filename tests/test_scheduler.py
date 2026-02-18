from pathlib import Path

from app.runner import AppSettings
from app.scheduler import DailyOpportunityScheduler


class StubDB:
    def __init__(self, should_skip: bool):
        self.should_skip = should_skip
        self.calls = 0

    def has_run_for_date(self, *, run_date: str, tickers):
        self.calls += 1
        return self.should_skip


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        tickers=["NVDA", "MSFT"],
        run_time="06:30",
        timezone="America/New_York",
        provider="openai",
        deep_model="gpt-5.2",
        quick_model="gpt-5-mini",
        run_on_startup=False,
        db_path=tmp_path / "tradingagents.db",
        dry_run=True,
    )


def test_scheduler_triggers_runner_when_not_previously_run(tmp_path: Path):
    db = StubDB(should_skip=False)
    calls: list[str] = []

    scheduler = DailyOpportunityScheduler(
        settings=_settings(tmp_path),
        run_callback=lambda source: calls.append(source) or {"status": "completed"},
        db=db,
        lock_path=tmp_path / "scheduler.pid",
    )
    result = scheduler._scheduled_job()

    assert db.calls == 1
    assert calls == ["scheduled"]
    assert result == {"status": "completed"}


def test_scheduler_skips_when_run_already_exists(tmp_path: Path):
    db = StubDB(should_skip=True)
    calls: list[str] = []

    scheduler = DailyOpportunityScheduler(
        settings=_settings(tmp_path),
        run_callback=lambda source: calls.append(source) or {"status": "completed"},
        db=db,
        lock_path=tmp_path / "scheduler.pid",
    )
    result = scheduler._scheduled_job()

    assert db.calls == 1
    assert calls == []
    assert result is not None
    assert result["status"] == "skipped"
    assert result["reason"] == "already_ran_for_date"


def test_scheduler_pid_lock_prevents_duplicate_instance(tmp_path: Path):
    shared_lock_path = tmp_path / "scheduler.pid"
    scheduler_a = DailyOpportunityScheduler(
        settings=_settings(tmp_path),
        run_callback=lambda source: {"status": source},
        db=StubDB(should_skip=False),
        lock_path=shared_lock_path,
    )
    scheduler_b = DailyOpportunityScheduler(
        settings=_settings(tmp_path),
        run_callback=lambda source: {"status": source},
        db=StubDB(should_skip=False),
        lock_path=shared_lock_path,
    )

    assert scheduler_a._acquire_pid_lock() is True
    assert scheduler_b._acquire_pid_lock() is False
    scheduler_a._release_pid_lock()
