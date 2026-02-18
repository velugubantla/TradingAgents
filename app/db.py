"""SQLite persistence utilities for local TradingAgents runs."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

DEFAULT_DB_PATH = Path("./data/tradingagents.db")


@dataclass(frozen=True)
class RunCreate:
    """Input payload used when creating a run row."""

    status: str
    provider: str
    deep_model: str
    quick_model: str
    tickers: Sequence[str] | str
    run_date: str
    raw_output_ref: str | None = None


class TradingAgentsDB:
    """Small DAO wrapper around the local sqlite database."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    status TEXT NOT NULL,
                    provider TEXT,
                    deep_model TEXT,
                    quick_model TEXT,
                    tickers TEXT NOT NULL,
                    run_date TEXT NOT NULL,
                    duration_ms INTEGER,
                    error TEXT,
                    raw_output_ref TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_runs_created_at
                    ON runs(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_runs_run_date
                    ON runs(run_date DESC);

                CREATE TABLE IF NOT EXISTS opportunities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    ticker TEXT NOT NULL,
                    action TEXT NOT NULL,
                    confidence REAL,
                    rationale_summary TEXT NOT NULL,
                    sources_used TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    raw_payload TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_opps_run_id
                    ON opportunities(run_id);
                CREATE INDEX IF NOT EXISTS idx_opps_created_at
                    ON opportunities(created_at DESC);

                CREATE TABLE IF NOT EXISTS artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_artifacts_run_id
                    ON artifacts(run_id);
                """
            )

    @staticmethod
    def normalize_tickers(tickers: Sequence[str] | str) -> str:
        if isinstance(tickers, str):
            raw = tickers.split(",")
        else:
            raw = list(tickers)
        cleaned = sorted({token.strip().upper() for token in raw if token and token.strip()})
        return ",".join(cleaned)

    @staticmethod
    def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
        return dict(row) if row is not None else None

    def create_run(self, payload: RunCreate) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO runs (
                    status, provider, deep_model, quick_model, tickers, run_date, raw_output_ref
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.status,
                    payload.provider,
                    payload.deep_model,
                    payload.quick_model,
                    self.normalize_tickers(payload.tickers),
                    payload.run_date,
                    payload.raw_output_ref,
                ),
            )
            return int(cursor.lastrowid)

    def update_run(
        self,
        run_id: int,
        *,
        status: str,
        duration_ms: int | None = None,
        error: str | None = None,
        raw_output_ref: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE runs
                SET status = ?,
                    duration_ms = ?,
                    error = ?,
                    raw_output_ref = ?
                WHERE id = ?
                """,
                (status, duration_ms, error, raw_output_ref, run_id),
            )

    def insert_opportunity(
        self,
        *,
        run_id: int,
        ticker: str,
        action: str,
        confidence: float | None,
        rationale_summary: str,
        sources_used: str | None,
        raw_payload: str,
    ) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO opportunities (
                    run_id, ticker, action, confidence, rationale_summary, sources_used, raw_payload
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    ticker.upper(),
                    action.lower(),
                    confidence,
                    rationale_summary,
                    sources_used,
                    raw_payload,
                ),
            )
            return int(cursor.lastrowid)

    def insert_artifact(self, *, run_id: int, kind: str, content: str) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO artifacts (run_id, kind, content)
                VALUES (?, ?, ?)
                """,
                (run_id, kind, content),
            )
            return int(cursor.lastrowid)

    def get_run(self, run_id: int) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            return self._row_to_dict(row)

    def get_latest_run(self) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return self._row_to_dict(row)

    def list_runs(self, *, limit: int = 20, offset: int = 0) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_opportunities_for_run(self, run_id: int) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM opportunities
                WHERE run_id = ?
                ORDER BY id ASC
                """,
                (run_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_latest_opportunities(self, *, limit: int = 10, offset: int = 0) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM opportunities
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_artifacts_for_run(self, run_id: int) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM artifacts
                WHERE run_id = ?
                ORDER BY id ASC
                """,
                (run_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def has_run_for_date(
        self,
        *,
        run_date: str,
        tickers: Sequence[str] | str,
        statuses: Sequence[str] = ("running", "completed", "completed_with_errors"),
    ) -> bool:
        normalized = self.normalize_tickers(tickers)
        placeholders = ",".join("?" for _ in statuses)
        query = (
            "SELECT 1 FROM runs WHERE run_date = ? AND tickers = ? "
            f"AND status IN ({placeholders}) LIMIT 1"
        )
        with self.connect() as conn:
            row = conn.execute(query, (run_date, normalized, *statuses)).fetchone()
            return row is not None

