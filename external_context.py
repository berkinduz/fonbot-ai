"""External context gate. Loads scanner output and enforces freshness.

The engine reads its external context from a JSON file produced by
`external_scan.py`. This module:

- Loads that file
- Checks freshness (default: 3 days)
- Verifies required sections are present
- Runs the intelligence analyzer to derive bounded modifiers
- Caps recommendation confidence when context is missing/stale (default cap 70)

Hard rules:

- Missing context does NOT block recommendations — it caps confidence and
  marks the report as quant-only. The user is the one who decides to act.
- Modifiers are additive, bounded, and explainable. Every modifier has a
  matching reason string the AI operator can quote.
- The scanner can be re-run autonomously; the gate just enforces freshness.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, List

from external_calendar import event_modifier
from external_intelligence import ExternalIntelligenceAnalyzer


@dataclass(frozen=True)
class ExternalContextResult:
    status: str
    verified_data: List[str] = field(default_factory=list)
    unavailable_data: List[str] = field(default_factory=list)
    user_provided_data: List[str] = field(default_factory=list)
    confidence_cap: float | None = None
    risk_penalty_delta: float = 0.0
    regime_score_delta: float = 0.0
    avoid_funds: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    rerun_triggers: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    age_days: int | None = None


REQUIRED_SECTIONS = {
    "macro_regime": "macro regime context not checked",
    "market_news": "market/fund news not checked",
    "rates_inflation": "policy-rate/inflation context not checked",
    "fund_specific": "fund-specific structural issues not checked",
}

TEFAS_AVAILABILITY_FACT = "TEFAS-listed funds treated as generally buyable during market business hours"
EXECUTION_TIMING_REMINDER = "manual execution timing still required: place orders during business hours"


def load_external_context(path: Path | None, max_age_days: int = 3) -> ExternalContextResult:
    if path is None:
        return missing_context()
    path = Path(path)
    if not path.exists():
        return missing_context([f"external context file not found: {path}"])
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return ExternalContextResult(
            status="invalid",
            unavailable_data=[f"external context JSON invalid: {exc}"],
            confidence_cap=65,
            notes=["External context gate failed; recommendation is quant-only."],
        )

    verified: List[str] = []
    unavailable: List[str] = []
    user_provided: List[str] = [f"external context file: {path}"]
    notes: List[str] = []

    context_date = str(payload.get("date") or payload.get("dt") or "").strip()
    age_days: int | None = None
    if context_date:
        age_days = _age_days(context_date)
        if age_days is None:
            unavailable.append(f"external context date could not be parsed: {context_date}")
        elif age_days > max_age_days:
            unavailable.append(f"external context stale: {context_date} age {age_days}d exceeds {max_age_days}d")
        else:
            verified.append(f"external context freshness checked: {context_date}")
    else:
        unavailable.append("external context date missing")

    sections = payload.get("sections") or {}
    if not isinstance(sections, dict):
        sections = {}
        unavailable.append("external context sections must be an object")

    for key, missing_message in REQUIRED_SECTIONS.items():
        section = sections.get(key)
        if _section_has_verified_content(section):
            verified.append(f"external {key} checked")
        else:
            unavailable.append(missing_message)

    verified.append(TEFAS_AVAILABILITY_FACT)

    risks = payload.get("risks") or []
    if risks:
        user_provided.extend([f"external risk: {risk}" for risk in risks if str(risk).strip()])

    sources = payload.get("sources") or []
    if sources:
        verified.append(f"external sources listed: {len(sources)}")
    else:
        unavailable.append("external sources missing")

    status = "ready" if not unavailable else "incomplete"
    cap: float | None = None if status == "ready" else 70
    if status != "ready":
        notes.append("External context incomplete; final recommendation remains quant-only until this gate is satisfied.")

    intelligence = ExternalIntelligenceAnalyzer().analyze(payload)
    verified.extend(intelligence.verified_data)
    unavailable.extend(intelligence.unavailable_data)
    if intelligence.confidence_cap is not None:
        cap = min(cap, intelligence.confidence_cap) if cap is not None else intelligence.confidence_cap

    # Calendar layer: pre-known event risk (TCMB MPC, TÜİK CPI, FOMC).
    calendar = event_modifier(within_days=7)
    if calendar.upcoming:
        verified.append(f"calendar: {len(calendar.upcoming)} known event(s) within 7 days")
    if calendar.confidence_cap is not None:
        cap = min(cap, calendar.confidence_cap) if cap is not None else calendar.confidence_cap
    reasons = list(intelligence.reasons) + list(calendar.reasons)
    triggers = list(intelligence.rerun_triggers) + list(calendar.rerun_triggers)
    risk_total = intelligence.risk_penalty_delta + calendar.risk_delta
    regime_total = intelligence.regime_score_delta + calendar.regime_delta

    return ExternalContextResult(
        status=status,
        verified_data=verified,
        unavailable_data=unavailable,
        user_provided_data=user_provided,
        confidence_cap=cap,
        risk_penalty_delta=round(risk_total, 2),
        regime_score_delta=round(regime_total, 2),
        avoid_funds=intelligence.avoid_funds,
        reasons=reasons,
        rerun_triggers=triggers,
        notes=notes,
        age_days=age_days,
    )


def missing_context(extra: List[str] | None = None) -> ExternalContextResult:
    unavailable = [
        "external context gate not run",
        *REQUIRED_SECTIONS.values(),
        "external sources missing",
        EXECUTION_TIMING_REMINDER,
    ]
    if extra:
        unavailable.extend(extra)
    return ExternalContextResult(
        status="missing",
        unavailable_data=unavailable,
        confidence_cap=70,
        notes=["Recommendation is quant-only; run external context pass (python3 -m external_scan or --refresh-external-context) before real execution."],
    )


def _section_has_verified_content(section: Any) -> bool:
    if isinstance(section, str):
        return bool(section.strip())
    if isinstance(section, list):
        return any(str(item).strip() for item in section)
    if isinstance(section, dict):
        facts = section.get("verified_facts") or section.get("facts") or section.get("summary")
        if _section_has_verified_content(facts):
            return True
        items = section.get("items")
        return _section_has_verified_content(items)
    return False


def _age_days(value: str) -> int | None:
    try:
        parsed = datetime.fromisoformat(value[:10]).date()
    except ValueError:
        return None
    return (date.today() - parsed).days
