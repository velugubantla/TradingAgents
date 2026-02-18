"""Chainlit UI for local TradingAgents opportunity runs."""

from __future__ import annotations

import asyncio
import json
import re
from threading import Lock
from typing import Any

import chainlit as cl

from app.db import TradingAgentsDB
from app.runner import AppSettings, RunnerService, load_settings_from_env
from app.scheduler import DailyOpportunityScheduler, next_run_at

PAGE_SIZE = 10
DISCLAIMER = (
    "### RESEARCH-ONLY WARNING\n"
    "**This local web UI is for research and experimentation only.** "
    "It does **not** place real trades, execute broker orders, or provide "
    "financial advice."
)

SETTINGS: AppSettings = load_settings_from_env()
DB = TradingAgentsDB(SETTINGS.db_path)
RUNNER = RunnerService(settings=SETTINGS, db=DB)
SCHEDULER = DailyOpportunityScheduler(
    settings=SETTINGS,
    run_callback=RUNNER.run_once,
    db=DB,
)
_SCHEDULER_INIT_LOCK = Lock()
_SCHEDULER_STARTED = False


def _ensure_scheduler_started() -> bool:
    global _SCHEDULER_STARTED
    with _SCHEDULER_INIT_LOCK:
        if _SCHEDULER_STARTED:
            return True
        _SCHEDULER_STARTED = SCHEDULER.start()
        return _SCHEDULER_STARTED


def _dashboard_actions() -> list[cl.Action]:
    return [
        cl.Action(name="run_now", payload={}, label="Run now"),
        cl.Action(name="refresh_dashboard", payload={}, label="Refresh"),
        cl.Action(name="show_runs", payload={"page": 1}, label="Runs list"),
        cl.Action(name="show_config", payload={}, label="Config"),
    ]


def _escape_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _build_dashboard_markdown() -> str:
    latest_run = DB.get_latest_run()
    latest_opps = DB.get_latest_opportunities(limit=8)
    scheduler_next = next_run_at(SETTINGS.run_time, SETTINGS.timezone)

    lines = [
        "## Local Opportunities Dashboard",
        "",
        f"- **Runner status:** `{RUNNER.status}`",
        f"- **Scheduler time:** `{SETTINGS.run_time}` ({SETTINGS.timezone})",
        f"- **Next scheduled run:** `{scheduler_next}`",
    ]
    if latest_run:
        lines.extend(
            [
                f"- **Last run id:** `{latest_run['id']}`",
                f"- **Last run timestamp:** `{latest_run['created_at']}`",
                f"- **Last run status:** `{latest_run['status']}`",
            ]
        )
    else:
        lines.append("- **Last run:** none yet")

    lines.extend(["", "### Latest opportunities"])
    if not latest_opps:
        lines.append("_No opportunities stored yet._")
        return "\n".join(lines)

    lines.append("| Run ID | Ticker | Action | Confidence | Created At | Summary |")
    lines.append("|---|---|---|---:|---|---|")
    for opp in latest_opps:
        confidence = "-" if opp["confidence"] is None else f"{opp['confidence']:.2f}"
        lines.append(
            "| "
            f"{opp['run_id']} | "
            f"{_escape_cell(opp['ticker'])} | "
            f"{_escape_cell(opp['action'])} | "
            f"{confidence} | "
            f"{_escape_cell(opp['created_at'])} | "
            f"{_escape_cell(opp['rationale_summary'])} |"
        )
    return "\n".join(lines)


def _format_run_result(result: dict[str, Any]) -> str:
    if result.get("status") == "running":
        return "A run is already in progress. Wait for it to finish and try again."

    lines = [
        "### Run completed",
        f"- **Run id:** `{result.get('run_id', '-')}`",
        f"- **Status:** `{result.get('status', '-')}`",
        f"- **Duration (ms):** `{result.get('duration_ms', '-')}`",
        f"- **Tickers:** `{', '.join(result.get('tickers', []))}`",
        f"- **Opportunities stored:** `{result.get('opportunities_count', '-')}`",
    ]
    errors = result.get("errors") or []
    if errors:
        lines.append("- **Errors:**")
        for error in errors:
            lines.append(f"  - `{error}`")
    return "\n".join(lines)


async def _send_dashboard() -> None:
    await cl.Message(content=_build_dashboard_markdown(), actions=_dashboard_actions()).send()


async def _send_runs_page(page: int) -> None:
    page = max(page, 1)
    offset = (page - 1) * PAGE_SIZE
    rows = DB.list_runs(limit=PAGE_SIZE + 1, offset=offset)
    has_next = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]

    lines = [f"## Runs (page {page})", ""]
    if not rows:
        lines.append("_No runs recorded yet._")
    else:
        lines.extend(
            [
                "| ID | Created At | Status | Provider | Models | Tickers | Run Date | Duration (ms) |",
                "|---:|---|---|---|---|---|---|---:|",
            ]
        )
        for row in rows:
            models = f"{row['deep_model']} / {row['quick_model']}"
            duration = "-" if row["duration_ms"] is None else str(row["duration_ms"])
            lines.append(
                "| "
                f"{row['id']} | "
                f"{_escape_cell(row['created_at'])} | "
                f"{_escape_cell(row['status'])} | "
                f"{_escape_cell(row['provider'])} | "
                f"{_escape_cell(models)} | "
                f"{_escape_cell(row['tickers'])} | "
                f"{_escape_cell(row['run_date'])} | "
                f"{duration} |"
            )

    actions: list[cl.Action] = [cl.Action(name="refresh_dashboard", payload={}, label="Dashboard")]
    if page > 1:
        actions.append(cl.Action(name="show_runs", payload={"page": page - 1}, label="Prev page"))
    if has_next:
        actions.append(cl.Action(name="show_runs", payload={"page": page + 1}, label="Next page"))
    actions.append(cl.Action(name="show_config", payload={}, label="Config"))
    for row in rows:
        actions.append(
            cl.Action(
                name="run_detail",
                payload={"run_id": row["id"]},
                label=f"Run #{row['id']}",
            )
        )

    await cl.Message(content="\n".join(lines), actions=actions).send()


def _to_text_elements(opportunities: list[dict], artifacts: list[dict]) -> list[cl.Text]:
    elements: list[cl.Text] = []

    def _safe_name(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]", "_", value)

    for opp in opportunities:
        name = _safe_name(f"opportunity-{opp['id']}-raw.txt")
        elements.append(cl.Text(name=name, content=opp["raw_payload"], display="side"))
    for artifact in artifacts:
        name = _safe_name(f"artifact-{artifact['id']}-{artifact['kind']}.txt")
        elements.append(cl.Text(name=name, content=artifact["content"], display="side"))
    return elements


async def _send_run_detail(run_id: int) -> None:
    run = DB.get_run(run_id)
    if not run:
        await cl.Message(content=f"Run `{run_id}` was not found.").send()
        return

    opportunities = DB.get_opportunities_for_run(run_id)
    artifacts = DB.get_artifacts_for_run(run_id)

    lines = [
        f"## Run detail: #{run_id}",
        "",
        f"- **Created at:** `{run['created_at']}`",
        f"- **Status:** `{run['status']}`",
        f"- **Provider:** `{run['provider']}`",
        f"- **Deep model:** `{run['deep_model']}`",
        f"- **Quick model:** `{run['quick_model']}`",
        f"- **Tickers:** `{run['tickers']}`",
        f"- **Run date:** `{run['run_date']}`",
        f"- **Duration (ms):** `{run['duration_ms']}`",
        f"- **Error:** `{run['error']}`",
        "",
        "### Parsed opportunities",
    ]

    if not opportunities:
        lines.append("_No parsed opportunities stored for this run._")
    else:
        lines.extend(
            [
                "| ID | Ticker | Action | Confidence | Created At | Summary | Sources |",
                "|---:|---|---|---:|---|---|---|",
            ]
        )
        for opp in opportunities:
            confidence = "-" if opp["confidence"] is None else f"{opp['confidence']:.2f}"
            lines.append(
                "| "
                f"{opp['id']} | "
                f"{_escape_cell(opp['ticker'])} | "
                f"{_escape_cell(opp['action'])} | "
                f"{confidence} | "
                f"{_escape_cell(opp['created_at'])} | "
                f"{_escape_cell(opp['rationale_summary'])} | "
                f"{_escape_cell(opp['sources_used'])} |"
            )

    lines.extend(
        [
            "",
            (
                f"Raw outputs are attached as side panel text elements "
                f"({len(opportunities) + len(artifacts)} files)."
            ),
        ]
    )

    await cl.Message(
        content="\n".join(lines),
        elements=_to_text_elements(opportunities, artifacts),
        actions=[
            cl.Action(name="show_runs", payload={"page": 1}, label="Back to runs"),
            cl.Action(name="refresh_dashboard", payload={}, label="Dashboard"),
        ],
    ).send()


async def _send_config_view() -> None:
    payload = SETTINGS.to_public_dict()
    config_text = json.dumps(payload, indent=2, ensure_ascii=True)
    content = (
        "## Current local configuration\n\n"
        "```json\n"
        f"{config_text}\n"
        "```\n\n"
        "> Reminder: do not store API secrets or private keys in sqlite."
    )
    await cl.Message(content=content, actions=_dashboard_actions()).send()


@cl.on_chat_start
async def on_chat_start() -> None:
    scheduler_started = _ensure_scheduler_started()
    await cl.Message(content=DISCLAIMER).send()
    if not scheduler_started:
        await cl.Message(
            content=(
                "Scheduler lock already held by another process. "
                "Daily trigger is not started in this process, but data remains available."
            )
        ).send()
    await _send_dashboard()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    text = message.content.strip().lower()

    if text in {"run", "run now", "/run"}:
        await _handle_run_now()
        return
    if text in {"dashboard", "/dashboard", "/home"}:
        await _send_dashboard()
        return
    if text.startswith("/runs"):
        parts = text.split(maxsplit=1)
        page = 1
        if len(parts) > 1 and parts[1].isdigit():
            page = int(parts[1])
        await _send_runs_page(page)
        return
    if text.startswith("/run "):
        parts = text.split(maxsplit=1)
        if len(parts) > 1 and parts[1].isdigit():
            await _send_run_detail(int(parts[1]))
            return
    if text in {"config", "/config"}:
        await _send_config_view()
        return

    await cl.Message(
        content=(
            "Commands: `run`, `/runs [page]`, `/run <id>`, `/dashboard`, `/config`.\n"
            "You can also use the action buttons."
        )
    ).send()


async def _handle_run_now() -> None:
    await cl.Message(content="Starting run now...").send()
    result = await asyncio.to_thread(RUNNER.run_once, source="manual")
    await cl.Message(content=_format_run_result(result), actions=_dashboard_actions()).send()
    await _send_dashboard()


@cl.action_callback("run_now")
async def action_run_now(_: cl.Action) -> None:
    await _handle_run_now()


@cl.action_callback("refresh_dashboard")
async def action_refresh_dashboard(_: cl.Action) -> None:
    await _send_dashboard()


@cl.action_callback("show_runs")
async def action_show_runs(action: cl.Action) -> None:
    page = int(action.payload.get("page", 1))
    await _send_runs_page(page)


@cl.action_callback("run_detail")
async def action_run_detail(action: cl.Action) -> None:
    run_id = int(action.payload["run_id"])
    await _send_run_detail(run_id)


@cl.action_callback("show_config")
async def action_show_config(_: cl.Action) -> None:
    await _send_config_view()

