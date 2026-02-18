from pathlib import Path

from app.db import RunCreate, TradingAgentsDB


def test_db_insert_and_retrieve_runs_and_opportunities(tmp_path: Path):
    db_path = tmp_path / "tradingagents.db"
    db = TradingAgentsDB(db_path)

    run_id = db.create_run(
        RunCreate(
            status="running",
            provider="openai",
            deep_model="gpt-5.2",
            quick_model="gpt-5-mini",
            tickers=["MSFT", "NVDA"],
            run_date="2026-02-18",
        )
    )
    artifact_id = db.insert_artifact(
        run_id=run_id,
        kind="raw_output",
        content='{"ticker":"NVDA","decision":"BUY"}',
    )
    opp_id = db.insert_opportunity(
        run_id=run_id,
        ticker="NVDA",
        action="buy",
        confidence=0.81,
        rationale_summary="Strong demand outlook from data center segment.",
        sources_used="10-K, earnings call",
        raw_payload='{"decision":"BUY","confidence":0.81}',
    )
    db.update_run(
        run_id,
        status="completed",
        duration_ms=1540,
        raw_output_ref=f"artifact:{artifact_id}",
    )

    assert run_id > 0
    assert opp_id > 0

    run = db.get_run(run_id)
    assert run is not None
    assert run["status"] == "completed"
    assert run["duration_ms"] == 1540
    assert run["tickers"] == "MSFT,NVDA"
    assert run["raw_output_ref"] == f"artifact:{artifact_id}"

    runs = db.list_runs(limit=10, offset=0)
    assert len(runs) == 1
    assert runs[0]["id"] == run_id

    opportunities = db.get_opportunities_for_run(run_id)
    assert len(opportunities) == 1
    assert opportunities[0]["ticker"] == "NVDA"
    assert opportunities[0]["action"] == "buy"

    latest_opps = db.get_latest_opportunities(limit=5)
    assert len(latest_opps) == 1
    assert latest_opps[0]["id"] == opp_id

    artifacts = db.get_artifacts_for_run(run_id)
    assert len(artifacts) == 1
    assert artifacts[0]["id"] == artifact_id

    assert db.has_run_for_date(run_date="2026-02-18", tickers=["NVDA", "MSFT"]) is True
    assert db.has_run_for_date(run_date="2026-02-19", tickers=["NVDA", "MSFT"]) is False
