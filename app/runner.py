"""Run orchestration for local and scheduled TradingAgents executions."""

from __future__ import annotations

import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import Any, Callable, Protocol
from zoneinfo import ZoneInfo
import os

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

from app.db import RunCreate, TradingAgentsDB
from app.parsing import parse_opportunity, serialize_raw_payload

VALID_ANALYSTS = ("market", "social", "news", "fundamentals")


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_tickers(value: str | None) -> list[str]:
    default = ["NVDA", "MSFT", "TSLA"]
    if not value:
        return default
    tickers: list[str] = []
    for token in value.split(","):
        cleaned = token.strip().upper()
        if cleaned and cleaned not in tickers:
            tickers.append(cleaned)
    return tickers or default


def _parse_analysts(value: str | None) -> list[str]:
    if not value:
        return list(VALID_ANALYSTS)
    analysts: list[str] = []
    for token in value.split(","):
        cleaned = token.strip().lower()
        if cleaned in VALID_ANALYSTS and cleaned not in analysts:
            analysts.append(cleaned)
    return analysts or list(VALID_ANALYSTS)


def _validate_run_time(value: str) -> str:
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except ValueError as exc:
        raise ValueError("RUN_TIME must be formatted as HH:MM (24h).") from exc
    return parsed.strftime("%H:%M")


def _safe_json_payload(value: Any) -> Any:
    """Recursively coerce non-serializable values into strings."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _safe_json_payload(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_safe_json_payload(item) for item in value]
    return str(value)


@dataclass
class AppSettings:
    """Environment-driven runtime settings for local operation."""

    tickers: list[str]
    run_time: str
    timezone: str
    provider: str
    deep_model: str
    quick_model: str
    run_on_startup: bool
    db_path: Path = Path("./data/tradingagents.db")
    backend_url: str | None = DEFAULT_CONFIG.get("backend_url")
    dry_run: bool = False
    selected_analysts: list[str] = field(default_factory=lambda: list(VALID_ANALYSTS))
    max_debate_rounds: int = 1
    max_risk_discuss_rounds: int = 1

    @classmethod
    def from_env(cls) -> "AppSettings":
        # Optional import so local .env files still work without hard dependency.
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except Exception:
            pass

        run_time = _validate_run_time(os.getenv("RUN_TIME", "06:30"))
        timezone = os.getenv("TIMEZONE", "America/New_York")

        return cls(
            tickers=_parse_tickers(os.getenv("TICKERS")),
            run_time=run_time,
            timezone=timezone,
            provider=os.getenv("PROVIDER", DEFAULT_CONFIG["llm_provider"]).strip().lower(),
            deep_model=os.getenv("DEEP_MODEL", DEFAULT_CONFIG["deep_think_llm"]).strip(),
            quick_model=os.getenv("QUICK_MODEL", DEFAULT_CONFIG["quick_think_llm"]).strip(),
            run_on_startup=_parse_bool(os.getenv("RUN_ON_STARTUP"), default=False),
            db_path=Path(
                os.getenv(
                    "TRADINGAGENTS_DB_PATH",
                    os.getenv("DB_PATH", "./data/tradingagents.db"),
                )
            ),
            backend_url=os.getenv("BACKEND_URL", DEFAULT_CONFIG.get("backend_url")),
            dry_run=_parse_bool(os.getenv("DRY_RUN"), default=False)
            or _parse_bool(os.getenv("FAKE_LLM"), default=False),
            selected_analysts=_parse_analysts(os.getenv("ANALYSTS")),
            max_debate_rounds=int(os.getenv("MAX_DEBATE_ROUNDS", "1")),
            max_risk_discuss_rounds=int(os.getenv("MAX_RISK_DISCUSS_ROUNDS", "1")),
        )

    def to_public_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["db_path"] = str(self.db_path)
        return payload

    def current_run_date(self) -> str:
        timezone = ZoneInfo(self.timezone)
        return datetime.now(timezone).date().isoformat()

    def run_time_parts(self) -> tuple[int, int]:
        hour, minute = self.run_time.split(":")
        return int(hour), int(minute)


class AgentExecutor(Protocol):
    """Contract used by the runner to execute one ticker."""

    def run_ticker(self, ticker: str, run_date: str, settings: AppSettings) -> dict[str, Any]:
        ...


class TradingAgentsExecutor:
    """Real executor that calls the upstream TradingAgents graph."""

    def run_ticker(self, ticker: str, run_date: str, settings: AppSettings) -> dict[str, Any]:
        config = DEFAULT_CONFIG.copy()
        config["llm_provider"] = settings.provider
        config["deep_think_llm"] = settings.deep_model
        config["quick_think_llm"] = settings.quick_model
        config["backend_url"] = settings.backend_url
        config["max_debate_rounds"] = settings.max_debate_rounds
        config["max_risk_discuss_rounds"] = settings.max_risk_discuss_rounds

        graph = TradingAgentsGraph(
            selected_analysts=settings.selected_analysts,
            debug=False,
            config=config,
        )
        final_state, processed_signal = graph.propagate(ticker, run_date)
        return {
            "ticker": ticker.upper(),
            "processed_signal": str(processed_signal),
            "final_trade_decision": final_state.get("final_trade_decision"),
            "final_state": _safe_json_payload(final_state),
        }


class FakeLLMExecutor:
    """Deterministic fake executor used for tests and low-cost local demos."""

    def run_ticker(self, ticker: str, run_date: str, settings: AppSettings) -> dict[str, Any]:
        checksum = sum(ord(ch) for ch in ticker.upper())
        actions = ("buy", "hold", "sell")
        action = actions[checksum % len(actions)]
        confidence = round(0.55 + ((checksum % 35) / 100.0), 2)
        return {
            "ticker": ticker.upper(),
            "action": action,
            "confidence": confidence,
            "rationale_summary": (
                f"Dry-run result for {ticker.upper()} on {run_date}. "
                "This is synthetic output from FakeLLMExecutor."
            ),
            "sources_used": ["local_fake_feed"],
            "final_trade_decision": action.upper(),
            "provider": settings.provider,
            "models": {
                "deep_model": settings.deep_model,
                "quick_model": settings.quick_model,
            },
        }


class RunnerService:
    """Coordinates single-run execution and persistence into sqlite."""

    def __init__(
        self,
        *,
        settings: AppSettings | None = None,
        db: TradingAgentsDB | None = None,
        executor_factory: Callable[[AppSettings], AgentExecutor] | None = None,
    ):
        self.settings = settings or AppSettings.from_env()
        self.db = db or TradingAgentsDB(self.settings.db_path)
        self.executor_factory = executor_factory
        self._run_lock = Lock()
        self._state_lock = Lock()
        self._is_running = False

    @property
    def status(self) -> str:
        with self._state_lock:
            return "running" if self._is_running else "idle"

    def _set_running(self, value: bool) -> None:
        with self._state_lock:
            self._is_running = value

    def _build_executor(self) -> AgentExecutor:
        if self.executor_factory is not None:
            return self.executor_factory(self.settings)
        if self.settings.dry_run:
            return FakeLLMExecutor()
        return TradingAgentsExecutor()

    def run_once(
        self,
        *,
        source: str = "manual",
        tickers: list[str] | None = None,
        run_date: str | None = None,
    ) -> dict[str, Any]:
        if not self._run_lock.acquire(blocking=False):
            return {
                "status": "running",
                "message": "A run is already in progress.",
            }

        self._set_running(True)
        started = perf_counter()
        selected_tickers = tickers or self.settings.tickers
        effective_run_date = run_date or self.settings.current_run_date()
        executor = self._build_executor()

        run_id = self.db.create_run(
            RunCreate(
                status="running",
                provider=self.settings.provider,
                deep_model=self.settings.deep_model,
                quick_model=self.settings.quick_model,
                tickers=selected_tickers,
                run_date=effective_run_date,
            )
        )

        artifact_ids: list[int] = []
        errors: list[str] = []
        opportunities_count = 0

        try:
            for ticker in selected_tickers:
                try:
                    raw_output = executor.run_ticker(ticker, effective_run_date, self.settings)
                except Exception as exc:  # pragma: no cover - exercised indirectly via run flow
                    errors.append(f"{ticker.upper()}: {exc}")
                    raw_output = {
                        "ticker": ticker.upper(),
                        "error": str(exc),
                        "traceback": traceback.format_exc(),
                    }

                raw_blob = serialize_raw_payload(raw_output)
                artifact_id = self.db.insert_artifact(
                    run_id=run_id,
                    kind=f"{source}_raw_output:{ticker.upper()}",
                    content=raw_blob,
                )
                artifact_ids.append(artifact_id)

                parsed = parse_opportunity(ticker, raw_output)
                self.db.insert_opportunity(
                    run_id=run_id,
                    ticker=parsed["ticker"],
                    action=parsed["action"],
                    confidence=parsed["confidence"],
                    rationale_summary=parsed["rationale_summary"],
                    sources_used=parsed["sources_used"],
                    raw_payload=parsed["raw_payload"],
                )
                opportunities_count += 1

            duration_ms = int((perf_counter() - started) * 1000)
            if errors and len(errors) == len(selected_tickers):
                status = "failed"
            elif errors:
                status = "completed_with_errors"
            else:
                status = "completed"

            self.db.update_run(
                run_id,
                status=status,
                duration_ms=duration_ms,
                error="\n".join(errors) if errors else None,
                raw_output_ref="artifacts:" + ",".join(str(item) for item in artifact_ids),
            )
            return {
                "run_id": run_id,
                "status": status,
                "duration_ms": duration_ms,
                "errors": errors,
                "opportunities_count": opportunities_count,
                "tickers": [ticker.upper() for ticker in selected_tickers],
                "run_date": effective_run_date,
                "source": source,
            }
        finally:
            self._set_running(False)
            self._run_lock.release()


def load_settings_from_env() -> AppSettings:
    return AppSettings.from_env()

