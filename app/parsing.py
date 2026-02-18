"""Opportunity extraction helpers for raw TradingAgents outputs."""

from __future__ import annotations

import json
import re
from typing import Any

ALLOWED_ACTIONS = {"buy", "sell", "hold", "unknown"}
MAX_SUMMARY_LENGTH = 280


def serialize_raw_payload(payload: Any) -> str:
    """Serialize payload to text for persistence, keeping raw text untouched."""
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    try:
        return json.dumps(payload, ensure_ascii=True, indent=2, default=str)
    except (TypeError, ValueError):
        return str(payload)


def normalize_action(action: Any) -> str:
    if not action:
        return "unknown"
    lowered = str(action).strip().lower()
    if lowered in ALLOWED_ACTIONS:
        return lowered
    if "buy" in lowered:
        return "buy"
    if "sell" in lowered:
        return "sell"
    if "hold" in lowered:
        return "hold"
    return "unknown"


def parse_opportunity(ticker: str, raw_payload: Any) -> dict:
    """Parse a single opportunity from raw output (dict/json/text)."""
    raw_text = serialize_raw_payload(raw_payload)
    payload_object = _coerce_to_mapping(raw_payload, raw_text)

    action = _extract_action(payload_object, raw_text)
    confidence = _extract_confidence(payload_object, raw_text)
    rationale = _extract_rationale(payload_object, raw_text)
    sources_used = _extract_sources(payload_object, raw_text)

    return {
        "ticker": ticker.upper(),
        "action": action,
        "confidence": confidence,
        "rationale_summary": rationale,
        "sources_used": sources_used,
        "raw_payload": raw_text,
    }


def _coerce_to_mapping(raw_payload: Any, raw_text: str) -> dict[str, Any]:
    if isinstance(raw_payload, dict):
        return raw_payload
    if isinstance(raw_payload, str):
        json_payload = _parse_json_string(raw_payload)
        if isinstance(json_payload, dict):
            return json_payload
    json_payload = _parse_json_string(raw_text)
    if isinstance(json_payload, dict):
        return json_payload
    return {}


def _parse_json_string(value: str) -> Any:
    value = value.strip()
    if not value:
        return None

    try:
        return json.loads(value)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```json\s*([\s\S]*?)\s*```", value, flags=re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return None
    return None


def _extract_action(payload: dict[str, Any], raw_text: str) -> str:
    for key in ("action", "signal", "decision", "recommendation"):
        if key in payload:
            normalized = normalize_action(payload.get(key))
            if normalized != "unknown":
                return normalized

    if "final_trade_decision" in payload:
        normalized = normalize_action(payload.get("final_trade_decision"))
        if normalized != "unknown":
            return normalized

    match = re.search(r"\b(buy|sell|hold)\b", raw_text, flags=re.IGNORECASE)
    return normalize_action(match.group(1) if match else None)


def _extract_confidence(payload: dict[str, Any], raw_text: str) -> float | None:
    for key in ("confidence", "confidence_score", "conviction", "probability"):
        if key in payload:
            parsed = _parse_confidence_value(payload.get(key))
            if parsed is not None:
                return parsed

    match = re.search(
        r"confidence[^0-9]{0,16}([0-9]+(?:\.[0-9]+)?)\s*%?",
        raw_text,
        flags=re.IGNORECASE,
    )
    if match:
        return _parse_confidence_value(match.group(1))
    return None


def _parse_confidence_value(value: Any) -> float | None:
    if value is None:
        return None

    if isinstance(value, str):
        value = value.strip().rstrip("%")
        if not value:
            return None

    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None

    if confidence < 0:
        return None
    if confidence > 1 and confidence <= 100:
        confidence = confidence / 100.0
    if confidence > 1:
        return None
    return round(confidence, 4)


def _extract_rationale(payload: dict[str, Any], raw_text: str) -> str:
    for key in (
        "rationale_summary",
        "rationale",
        "summary",
        "reasoning",
        "explanation",
        "final_trade_decision",
        "investment_plan",
    ):
        candidate = payload.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return _truncate_summary(candidate.strip())

    first_line = raw_text.strip().splitlines()[0] if raw_text.strip() else ""
    if first_line:
        return _truncate_summary(first_line)
    return "No rationale provided."


def _extract_sources(payload: dict[str, Any], raw_text: str) -> str | None:
    for key in ("sources_used", "sources", "news_sources", "references"):
        if key not in payload:
            continue
        value = payload.get(key)
        if value is None:
            return None
        if isinstance(value, str):
            cleaned = value.strip()
            return cleaned or None
        if isinstance(value, list):
            flattened = [str(item).strip() for item in value if str(item).strip()]
            return ", ".join(flattened) or None
        return str(value).strip() or None

    match = re.search(r"sources?\s*:\s*(.+)", raw_text, flags=re.IGNORECASE)
    if match:
        value = match.group(1).strip()
        return value or None
    return None


def _truncate_summary(text: str) -> str:
    if len(text) <= MAX_SUMMARY_LENGTH:
        return text
    return text[: MAX_SUMMARY_LENGTH - 3].rstrip() + "..."

