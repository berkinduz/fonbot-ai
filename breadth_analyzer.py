"""Cross-sectional breadth analyzer.

Computes regime quality from the universe itself instead of relying solely
on macro proxies. The intuition: when many funds simultaneously show positive
3M momentum, the local market is in a constructive regime; when most are
negative, it's a weak regime. This signal is independent of Yahoo / Google
and works even when external scanner is down.

The breadth score is the base regime score used by the allocator. Official
macro, Yahoo/KAP/news and calendar context apply bounded deltas on top through
`external_context`; they do not create a second neutral regime layer. Breadth is
bounded to [25, 95] so it never produces extreme all-in or all-out behavior on
its own — it is a modifier, not a strategy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List

from models import FundMetrics


@dataclass(frozen=True)
class BreadthSnapshot:
    universe_size: int
    positive_3m_pct: float
    positive_6m_pct: float
    trend_confirmed_pct: float
    median_3m_return: float
    median_6m_return: float
    score: float
    label: str
    verified_inputs: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


class BreadthAnalyzer:
    def analyze(self, opportunity_metrics: Iterable[FundMetrics]) -> BreadthSnapshot:
        metrics = [m for m in opportunity_metrics if m.data_quality != "insufficient"]
        n = len(metrics)
        if n == 0:
            return BreadthSnapshot(0, 0.0, 0.0, 0.0, 0.0, 0.0, 50.0, "unknown", notes=["no scored opportunity funds; breadth uses neutral fallback"])
        positive_3m = sum(1 for m in metrics if m.return_3m > 0)
        positive_6m = sum(1 for m in metrics if m.return_6m > 0)
        trend_ok = sum(1 for m in metrics if m.trend_confirmed)
        pos_3m_pct = positive_3m / n
        pos_6m_pct = positive_6m / n
        trend_pct = trend_ok / n
        median_3m = _median([m.return_3m for m in metrics])
        median_6m = _median([m.return_6m for m in metrics])
        # Blend: 3M breadth carries most weight; 6M confirms; trend filter caps the upside.
        raw = 100 * (0.45 * pos_3m_pct + 0.30 * pos_6m_pct + 0.25 * trend_pct)
        # Median-return modifier: if breadth is high but median return is barely positive,
        # the regime is shallow; pull the score down. If median is strongly positive, lift.
        modifier = max(-10, min(10, median_3m * 100 * 0.5))
        score = max(25.0, min(95.0, raw + modifier))
        label = self._label(score, pos_3m_pct)
        return BreadthSnapshot(
            universe_size=n,
            positive_3m_pct=round(pos_3m_pct, 4),
            positive_6m_pct=round(pos_6m_pct, 4),
            trend_confirmed_pct=round(trend_pct, 4),
            median_3m_return=round(median_3m, 4),
            median_6m_return=round(median_6m, 4),
            score=round(score, 2),
            label=label,
            verified_inputs=[
                f"breadth from {n} scored opportunity funds",
                f"3M positive: {int(pos_3m_pct*100)}%",
                f"6M positive: {int(pos_6m_pct*100)}%",
                f"trend confirmed: {int(trend_pct*100)}%",
                f"median 3M return: {median_3m*100:.2f}%",
            ],
        )

    def _label(self, score: float, pos_3m_pct: float) -> str:
        if score >= 75 and pos_3m_pct >= 0.65:
            return "strong"
        if score >= 60:
            return "constructive"
        if score >= 45:
            return "mixed"
        return "weak"


def _median(values: List[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    mid = len(s) // 2
    if len(s) % 2 == 0:
        return (s[mid - 1] + s[mid]) / 2
    return s[mid]
