from __future__ import annotations

from typing import Iterable, List

from models import FundMetrics, ScoredFund


class FundScorer:
    def score_opportunity_funds(self, metrics: Iterable[FundMetrics]) -> List[ScoredFund]:
        scored = [self._score_one(m) for m in metrics if m.data_quality != "insufficient"]
        return sorted(scored, key=lambda x: x.score, reverse=True)

    def score_money_market_funds(self, metrics: Iterable[FundMetrics]) -> List[ScoredFund]:
        scored = []
        for m in metrics:
            score = 50 + min(max(m.return_1m * 1200, -10), 30) - min(abs(m.max_drawdown) * 100, 15)
            scored.append(ScoredFund(m.code, m.name, m.category, round(score, 2), round(min(max(score, 0), 100), 2), m, ["money market stability and recent yield proxy"]))
        return sorted(scored, key=lambda x: x.score, reverse=True)

    def _score_one(self, m: FundMetrics) -> ScoredFund:
        reasons: List[str] = []
        score = 0.0
        score += min(max(m.return_3m * 100, -50), 80) * 0.45
        score += min(max(m.return_6m * 100, -50), 90) * 0.25
        score += min(max(m.return_1m * 100, -30), 40) * 0.10
        if m.absolute_momentum:
            score += 12
            reasons.append("positive absolute momentum")
        if m.trend_confirmed:
            score += 12
            reasons.append("trend confirmed by moving averages and slope")
        if m.return_3m > m.return_6m / 2:
            score += 4
            reasons.append("recent momentum acceleration is constructive")
        vol_penalty = min(m.volatility_3m * 10, 15)
        dd_penalty = min(abs(m.max_drawdown) * 40, 18)
        score -= vol_penalty + dd_penalty
        rejections = []
        if not m.absolute_momentum:
            rejections.append("absolute momentum is negative")
        if not m.trend_confirmed:
            rejections.append("trend confirmation is weak")
        confidence = min(max(score + 35, 0), 100)
        return ScoredFund(m.code, m.name, m.category, round(min(max(score + 50, 0), 100), 2), round(confidence, 2), m, reasons or ["quant score derived from momentum/trend/risk"], rejections)
