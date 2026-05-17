from __future__ import annotations

from typing import Iterable, List, Optional

from models import FundMetrics, ScoredFund
from strategy_loader import load_weights


class FundScorer:
    def __init__(self, weights: Optional[dict] = None):
        cfg = weights or load_weights()
        self.s = cfg["scorer"]
        self.m = cfg["money_market_scorer"]

    def score_opportunity_funds(self, metrics: Iterable[FundMetrics]) -> List[ScoredFund]:
        scored = [self._score_one(m) for m in metrics if m.data_quality != "insufficient"]
        return sorted(scored, key=lambda x: x.score, reverse=True)

    def score_money_market_funds(self, metrics: Iterable[FundMetrics]) -> List[ScoredFund]:
        m = self.m
        scored = []
        for fm in metrics:
            score = m["base"] + min(max(fm.return_1m * m["return_1m_multiplier"], m["return_1m_bonus_min"]), m["return_1m_bonus_max"]) - min(abs(fm.max_drawdown) * m["drawdown_penalty_multiplier"], m["drawdown_penalty_cap"])
            scored.append(ScoredFund(fm.code, fm.name, fm.category, round(score, 2), round(min(max(score, 0), 100), 2), fm, ["money market stability and recent yield proxy"]))
        return sorted(scored, key=lambda x: x.score, reverse=True)

    def _score_one(self, fm: FundMetrics) -> ScoredFund:
        s = self.s
        reasons: List[str] = []
        score = 0.0
        score += min(max(fm.return_3m * 100, s["return_3m_cap_low"]), s["return_3m_cap_high"]) * s["return_3m_weight"]
        score += min(max(fm.return_6m * 100, s["return_6m_cap_low"]), s["return_6m_cap_high"]) * s["return_6m_weight"]
        score += min(max(fm.return_1m * 100, s["return_1m_cap_low"]), s["return_1m_cap_high"]) * s["return_1m_weight"]
        if fm.absolute_momentum:
            score += s["absolute_momentum_bonus"]
            reasons.append("positive absolute momentum")
        if fm.trend_confirmed:
            score += s["trend_confirmed_bonus"]
            reasons.append("trend confirmed by moving averages and slope")
        if fm.return_3m > fm.return_6m / 2:
            score += s["acceleration_bonus"]
            reasons.append("recent momentum acceleration is constructive")
        vol_penalty = min(fm.volatility_3m * s["volatility_penalty_multiplier"], s["volatility_penalty_cap"])
        dd_penalty = min(abs(fm.max_drawdown) * s["drawdown_penalty_multiplier"], s["drawdown_penalty_cap"])
        score -= vol_penalty + dd_penalty
        rejections = []
        if not fm.absolute_momentum:
            rejections.append("absolute momentum is negative")
        if not fm.trend_confirmed:
            rejections.append("trend confirmation is weak")
        confidence = min(max(score + s["confidence_offset"], 0), 100)
        return ScoredFund(fm.code, fm.name, fm.category, round(min(max(score + s["score_offset"], 0), 100), 2), round(confidence, 2), fm, reasons or ["quant score derived from momentum/trend/risk"], rejections)
