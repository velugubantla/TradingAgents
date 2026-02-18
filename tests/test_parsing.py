from app.parsing import parse_opportunity


def test_parse_opportunity_from_json_payload():
    raw_payload = {
        "action": "BUY",
        "confidence": 72,
        "rationale_summary": "Earnings momentum and strong guidance.",
        "sources_used": ["10-K", "Earnings Call"],
    }

    parsed = parse_opportunity("nvda", raw_payload)

    assert parsed["ticker"] == "NVDA"
    assert parsed["action"] == "buy"
    assert parsed["confidence"] == 0.72
    assert parsed["rationale_summary"] == "Earnings momentum and strong guidance."
    assert parsed["sources_used"] == "10-K, Earnings Call"
    assert '"action": "BUY"' in parsed["raw_payload"]


def test_parse_opportunity_from_text_payload():
    raw_payload = (
        "Portfolio Manager Decision: SELL due to deteriorating margin profile. "
        "Confidence: 45%."
    )

    parsed = parse_opportunity("MSFT", raw_payload)

    assert parsed["action"] == "sell"
    assert parsed["confidence"] == 0.45
    assert "Portfolio Manager Decision" in parsed["rationale_summary"]


def test_parse_opportunity_handles_missing_fields():
    raw_payload = {"unexpected": "shape"}

    parsed = parse_opportunity("TSLA", raw_payload)

    assert parsed["action"] == "unknown"
    assert parsed["confidence"] is None
    assert parsed["rationale_summary"] != ""
    assert parsed["sources_used"] is None
