"""Calendar awareness: pre-known event risk.

TCMB MPC dates, TÜİK CPI release dates and Fed FOMC dates are published in
advance. Knowing "the next inflation print is in 2 days" should reduce
conviction slightly because the engine cannot price an unknown shock.

This module exposes:

- `upcoming_events(within_days)` — events within the next N days.
- `event_modifier(within_days)` — a small bounded modifier dict the
  intelligence layer can fold into its delta:
  {risk_delta, regime_delta, confidence_cap, reasons, triggers}

Calendar entries are hard-coded for a rolling window. They are not fetched
because public ICS feeds for TCMB/TÜİK are inconsistent and parsing them
adds fragility. When a year rolls over, the data file (or this module) is
updated; this is a known maintenance touchpoint and is acceptable for a
once-a-year update.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional
import json


# Schedule is updated annually. Sources:
# - TCMB Para Politikası Kurulu (PPK) takvimi: https://www.tcmb.gov.tr/
# - TÜİK Veri Yayım Takvimi: https://www.tuik.gov.tr/
# - Fed FOMC Calendar: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
#
# Dates are ISO 8601 (YYYY-MM-DD). Each event has a kind and a short label.
# The 2026 calendar is illustrative; the AI operator should refresh it from
# official sources at the start of each year — see strategy/history.jsonl
# convention for any modification.
DEFAULT_CALENDAR: List[dict] = [
    {"date": "2026-01-23", "kind": "TCMB_MPC", "label": "TCMB PPK toplantısı"},
    {"date": "2026-02-20", "kind": "TCMB_MPC", "label": "TCMB PPK toplantısı"},
    {"date": "2026-03-19", "kind": "TCMB_MPC", "label": "TCMB PPK toplantısı"},
    {"date": "2026-04-23", "kind": "TCMB_MPC", "label": "TCMB PPK toplantısı"},
    {"date": "2026-05-21", "kind": "TCMB_MPC", "label": "TCMB PPK toplantısı"},
    {"date": "2026-06-25", "kind": "TCMB_MPC", "label": "TCMB PPK toplantısı"},
    {"date": "2026-07-23", "kind": "TCMB_MPC", "label": "TCMB PPK toplantısı"},
    {"date": "2026-09-10", "kind": "TCMB_MPC", "label": "TCMB PPK toplantısı"},
    {"date": "2026-10-22", "kind": "TCMB_MPC", "label": "TCMB PPK toplantısı"},
    {"date": "2026-12-17", "kind": "TCMB_MPC", "label": "TCMB PPK toplantısı"},
    # TÜİK enflasyon (TÜFE/ÜFE) — typically 3rd business day of each month
    {"date": "2026-01-05", "kind": "TUIK_CPI", "label": "TÜİK TÜFE/ÜFE açıklaması"},
    {"date": "2026-02-03", "kind": "TUIK_CPI", "label": "TÜİK TÜFE/ÜFE açıklaması"},
    {"date": "2026-03-03", "kind": "TUIK_CPI", "label": "TÜİK TÜFE/ÜFE açıklaması"},
    {"date": "2026-04-03", "kind": "TUIK_CPI", "label": "TÜİK TÜFE/ÜFE açıklaması"},
    {"date": "2026-05-05", "kind": "TUIK_CPI", "label": "TÜİK TÜFE/ÜFE açıklaması"},
    {"date": "2026-06-03", "kind": "TUIK_CPI", "label": "TÜİK TÜFE/ÜFE açıklaması"},
    {"date": "2026-07-03", "kind": "TUIK_CPI", "label": "TÜİK TÜFE/ÜFE açıklaması"},
    {"date": "2026-08-04", "kind": "TUIK_CPI", "label": "TÜİK TÜFE/ÜFE açıklaması"},
    {"date": "2026-09-03", "kind": "TUIK_CPI", "label": "TÜİK TÜFE/ÜFE açıklaması"},
    {"date": "2026-10-05", "kind": "TUIK_CPI", "label": "TÜİK TÜFE/ÜFE açıklaması"},
    {"date": "2026-11-03", "kind": "TUIK_CPI", "label": "TÜİK TÜFE/ÜFE açıklaması"},
    {"date": "2026-12-03", "kind": "TUIK_CPI", "label": "TÜİK TÜFE/ÜFE açıklaması"},
    # Fed FOMC
    {"date": "2026-01-28", "kind": "FOMC", "label": "Fed FOMC toplantısı"},
    {"date": "2026-03-18", "kind": "FOMC", "label": "Fed FOMC toplantısı"},
    {"date": "2026-04-29", "kind": "FOMC", "label": "Fed FOMC toplantısı"},
    {"date": "2026-06-17", "kind": "FOMC", "label": "Fed FOMC toplantısı"},
    {"date": "2026-07-29", "kind": "FOMC", "label": "Fed FOMC toplantısı"},
    {"date": "2026-09-16", "kind": "FOMC", "label": "Fed FOMC toplantısı"},
    {"date": "2026-11-04", "kind": "FOMC", "label": "Fed FOMC toplantısı"},
    {"date": "2026-12-16", "kind": "FOMC", "label": "Fed FOMC toplantısı"},
]


@dataclass(frozen=True)
class UpcomingEvent:
    date: str
    kind: str
    label: str
    days_until: int


@dataclass(frozen=True)
class CalendarModifier:
    risk_delta: float = 0.0
    regime_delta: float = 0.0
    confidence_cap: Optional[float] = None
    upcoming: List[UpcomingEvent] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    rerun_triggers: List[str] = field(default_factory=list)


def _load_calendar(custom_path: Optional[Path] = None) -> List[dict]:
    if custom_path and Path(custom_path).exists():
        try:
            return json.loads(Path(custom_path).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return DEFAULT_CALENDAR
    return DEFAULT_CALENDAR


def upcoming_events(within_days: int = 7, custom_path: Optional[Path] = None, today: Optional[date] = None) -> List[UpcomingEvent]:
    today = today or date.today()
    out: List[UpcomingEvent] = []
    for entry in _load_calendar(custom_path):
        try:
            event_date = datetime.fromisoformat(entry["date"]).date()
        except (KeyError, ValueError):
            continue
        delta = (event_date - today).days
        if 0 <= delta <= within_days:
            out.append(UpcomingEvent(date=entry["date"], kind=entry["kind"], label=entry["label"], days_until=delta))
    return sorted(out, key=lambda e: e.days_until)


def event_modifier(within_days: int = 7, custom_path: Optional[Path] = None, today: Optional[date] = None) -> CalendarModifier:
    events = upcoming_events(within_days=within_days, custom_path=custom_path, today=today)
    if not events:
        return CalendarModifier()
    risk = 0.0
    regime = 0.0
    cap: Optional[float] = None
    reasons: List[str] = []
    triggers: List[str] = []
    for ev in events:
        if ev.kind == "TCMB_MPC":
            # 0-2 days: significant; 3-5: mild; 6-7: minimal
            tier = 3 if ev.days_until <= 2 else 2 if ev.days_until <= 5 else 1
            risk += {1: 1, 2: 3, 3: 5}[tier]
            regime -= {1: 1, 2: 2, 3: 4}[tier]
            if tier == 3:
                cap = 80 if cap is None else min(cap, 80)
            reasons.append(f"Calendar: {ev.label} {ev.days_until} gün içinde — kararı bilmeden agresif pozisyon almak risklidir.")
            triggers.append(f"{ev.label} sonucu açıklanır")
        elif ev.kind == "TUIK_CPI":
            tier = 3 if ev.days_until <= 1 else 2 if ev.days_until <= 3 else 1
            risk += {1: 1, 2: 3, 3: 5}[tier]
            regime -= {1: 1, 2: 2, 3: 3}[tier]
            reasons.append(f"Calendar: {ev.label} {ev.days_until} gün içinde — enflasyon sürprizi makro rejimi değiştirebilir.")
            triggers.append(f"{ev.label} sonucu açıklanır")
        elif ev.kind == "FOMC":
            tier = 2 if ev.days_until <= 2 else 1
            risk += {1: 2, 2: 4}[tier]
            regime -= {1: 1, 2: 3}[tier]
            reasons.append(f"Calendar: {ev.label} {ev.days_until} gün içinde — global risk iştahı değişebilir.")
            triggers.append(f"{ev.label} sonucu açıklanır")
    # Bounded
    risk = min(risk, 15)
    regime = max(regime, -10)
    return CalendarModifier(
        risk_delta=round(risk, 2),
        regime_delta=round(regime, 2),
        confidence_cap=cap,
        upcoming=events,
        reasons=reasons[:5],
        rerun_triggers=triggers[:5],
    )
