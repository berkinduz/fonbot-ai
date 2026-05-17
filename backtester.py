"""Real backtester: replay past decisions, evaluate realized returns.

The placeholder version of this module returned a fixed dataclass and never
touched the data. This version actually:

- Reads `reports/decisions.jsonl` (append-only history of every recommendation).
- For each decision, fetches the relevant funds' price history from the cache
  for the period AFTER the decision date.
- Computes the realized return of the recommended allocation over the
  evaluation window (default 30 days, configurable).
- Compares against two baselines: (a) 100% money market (the defensive leg),
  (b) equal-weight basket of the top-3 candidates considered at the time.
- Outputs summary metrics: number of decisions evaluated, hit rate vs each
  baseline, mean and median outperformance, and per-decision breakdown.

It is deliberately simple: no parameter optimization, no walk-forward sweep,
no leakage controls beyond "evaluate only the period strictly AFTER the
decision date". Backtests are sanity checks, not validation suites.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

import pandas as pd

from cache import SQLiteCache
from config import FundbotConfig
from utils.jsonl import read_jsonl


@dataclass(frozen=True)
class EvaluatedDecision:
    decision_id: str
    decided_at: str
    aggressive_code: str
    defensive_code: str
    aggressive_ratio: float
    defensive_ratio: float
    portfolio_return_pct: float
    money_market_only_return_pct: float
    top3_equal_weight_return_pct: Optional[float]
    outperformance_vs_money_market_pct: float
    outperformance_vs_top3_pct: Optional[float]
    evaluation_days: int
    evaluation_end: str
    notes: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class BacktestSummary:
    decisions_evaluated: int
    decisions_skipped: int
    skip_reasons: List[str]
    mean_portfolio_return_pct: float
    median_portfolio_return_pct: float
    hit_rate_vs_money_market: float
    hit_rate_vs_top3: Optional[float]
    mean_outperformance_vs_money_market_pct: float
    mean_outperformance_vs_top3_pct: Optional[float]
    evaluated: List[EvaluatedDecision]
    window_days: int


class SimpleBacktester:
    """Replay decisions.jsonl + cached prices to evaluate realized strategy returns."""

    def __init__(self, config: FundbotConfig, evaluation_window_days: int = 30):
        self.config = config
        self.cache = SQLiteCache(config.cache_path)
        self.evaluation_window_days = evaluation_window_days

    def run(self, history_path: Optional[Path] = None) -> BacktestSummary:
        path = Path(history_path) if history_path else self.config.history_path
        if not path.exists():
            return _empty_summary(self.evaluation_window_days, skip_reasons=[f"history file not found: {path}"])
        records = read_jsonl(path)
        decisions = [r for r in records if r.get("type") == "fundbot_decision"]
        if not decisions:
            return _empty_summary(self.evaluation_window_days, skip_reasons=["no fundbot_decision records in history"])
        evaluated: List[EvaluatedDecision] = []
        skip_reasons: List[str] = []
        for record in decisions:
            outcome = self._evaluate_one(record)
            if isinstance(outcome, EvaluatedDecision):
                evaluated.append(outcome)
            else:
                skip_reasons.append(outcome)
        if not evaluated:
            return _empty_summary(self.evaluation_window_days, skip_reasons=skip_reasons, decisions_skipped=len(decisions))
        portfolio_returns = [e.portfolio_return_pct for e in evaluated]
        outperf_mm = [e.outperformance_vs_money_market_pct for e in evaluated]
        outperf_top3 = [e.outperformance_vs_top3_pct for e in evaluated if e.outperformance_vs_top3_pct is not None]
        hit_rate_mm = sum(1 for x in outperf_mm if x > 0) / len(outperf_mm)
        hit_rate_top3 = (sum(1 for x in outperf_top3 if x > 0) / len(outperf_top3)) if outperf_top3 else None
        return BacktestSummary(
            decisions_evaluated=len(evaluated),
            decisions_skipped=len(decisions) - len(evaluated),
            skip_reasons=skip_reasons,
            mean_portfolio_return_pct=round(statistics.fmean(portfolio_returns), 3),
            median_portfolio_return_pct=round(statistics.median(portfolio_returns), 3),
            hit_rate_vs_money_market=round(hit_rate_mm, 3),
            hit_rate_vs_top3=round(hit_rate_top3, 3) if hit_rate_top3 is not None else None,
            mean_outperformance_vs_money_market_pct=round(statistics.fmean(outperf_mm), 3),
            mean_outperformance_vs_top3_pct=round(statistics.fmean(outperf_top3), 3) if outperf_top3 else None,
            evaluated=evaluated,
            window_days=self.evaluation_window_days,
        )

    def _evaluate_one(self, record: dict):
        decision = record.get("decision") or {}
        decision_id = decision.get("decision_id") or record.get("id") or "(no id)"
        decided_at_raw = decision.get("created_at") or record.get("dt") or ""
        try:
            decided_at = datetime.fromisoformat(decided_at_raw.replace("Z", "+00:00")).date()
        except (ValueError, AttributeError):
            return f"{decision_id}: invalid created_at: {decided_at_raw!r}"
        agg = decision.get("aggressive_fund") or {}
        defn = decision.get("defensive_fund") or {}
        agg_code = (agg.get("code") or "").upper()
        def_code = (defn.get("code") or "").upper()
        if not agg_code or not def_code:
            return f"{decision_id}: missing fund codes"
        agg_ratio = float(decision.get("aggressive_ratio") or 0)
        def_ratio = float(decision.get("defensive_ratio") or 0)
        eval_end = decided_at + timedelta(days=self.evaluation_window_days)
        agg_return = self._period_return(agg_code, decided_at, eval_end)
        def_return = self._period_return(def_code, decided_at, eval_end)
        if agg_return is None or def_return is None:
            missing = [c for c, r in ((agg_code, agg_return), (def_code, def_return)) if r is None]
            return f"{decision_id}: insufficient cached prices in evaluation window for {missing}"
        portfolio_return = agg_ratio * agg_return + def_ratio * def_return
        money_market_only_return = def_return
        # Top-3 equal weight baseline (if we have all three histories)
        candidates = record.get("candidates") or []
        top3_codes = [str(c.get("code", "")).upper() for c in candidates[:3] if c.get("code")]
        top3_returns: List[float] = []
        notes: List[str] = []
        for code in top3_codes:
            r = self._period_return(code, decided_at, eval_end)
            if r is not None:
                top3_returns.append(r)
        top3_return = sum(top3_returns) / len(top3_returns) if len(top3_returns) >= 2 else None
        if top3_return is None and top3_codes:
            notes.append("top-3 baseline skipped: insufficient cached prices for candidates")
        outperf_mm = portfolio_return - money_market_only_return
        outperf_top3 = (portfolio_return - top3_return) if top3_return is not None else None
        return EvaluatedDecision(
            decision_id=decision_id,
            decided_at=decided_at.isoformat(),
            aggressive_code=agg_code,
            defensive_code=def_code,
            aggressive_ratio=agg_ratio,
            defensive_ratio=def_ratio,
            portfolio_return_pct=round(portfolio_return * 100, 3),
            money_market_only_return_pct=round(money_market_only_return * 100, 3),
            top3_equal_weight_return_pct=round(top3_return * 100, 3) if top3_return is not None else None,
            outperformance_vs_money_market_pct=round(outperf_mm * 100, 3),
            outperformance_vs_top3_pct=round(outperf_top3 * 100, 3) if outperf_top3 is not None else None,
            evaluation_days=self.evaluation_window_days,
            evaluation_end=eval_end.isoformat(),
            notes=notes,
        )

    def _period_return(self, code: str, start, end) -> Optional[float]:
        prices = self.cache.load_prices(code)
        if prices.empty:
            return None
        prices = prices.sort_values("date")
        prices["date"] = pd.to_datetime(prices["date"]).dt.date
        on_start = prices[prices["date"] >= start]
        on_end = prices[prices["date"] <= end]
        if on_start.empty or on_end.empty:
            return None
        first = on_start.iloc[0]
        last = on_end.iloc[-1]
        if first["date"] >= last["date"]:
            return None
        first_price = float(first["price"])
        last_price = float(last["price"])
        if first_price <= 0:
            return None
        return last_price / first_price - 1


def _empty_summary(window: int, skip_reasons: Optional[List[str]] = None, decisions_skipped: int = 0) -> BacktestSummary:
    return BacktestSummary(
        decisions_evaluated=0,
        decisions_skipped=decisions_skipped,
        skip_reasons=skip_reasons or [],
        mean_portfolio_return_pct=0.0,
        median_portfolio_return_pct=0.0,
        hit_rate_vs_money_market=0.0,
        hit_rate_vs_top3=None,
        mean_outperformance_vs_money_market_pct=0.0,
        mean_outperformance_vs_top3_pct=None,
        evaluated=[],
        window_days=window,
    )


def render_summary(summary: BacktestSummary) -> str:
    if summary.decisions_evaluated == 0:
        lines = [f"Backtest: 0 decisions evaluated (window={summary.window_days}d)."]
        if summary.decisions_skipped:
            lines.append(f"Skipped {summary.decisions_skipped} decision(s):")
            for reason in summary.skip_reasons[:10]:
                lines.append(f"  - {reason}")
        if summary.skip_reasons and not summary.decisions_skipped:
            for reason in summary.skip_reasons:
                lines.append(f"  - {reason}")
        return "\n".join(lines)
    lines = [
        f"Backtest: {summary.decisions_evaluated} decision(s) evaluated, {summary.decisions_skipped} skipped (window={summary.window_days}d).",
        f"  mean portfolio return:        {summary.mean_portfolio_return_pct:+.2f}%",
        f"  median portfolio return:      {summary.median_portfolio_return_pct:+.2f}%",
        f"  mean outperformance vs MM:    {summary.mean_outperformance_vs_money_market_pct:+.2f}%  (hit rate {summary.hit_rate_vs_money_market*100:.0f}%)",
    ]
    if summary.mean_outperformance_vs_top3_pct is not None:
        lines.append(
            f"  mean outperformance vs top3:  {summary.mean_outperformance_vs_top3_pct:+.2f}%  (hit rate {(summary.hit_rate_vs_top3 or 0)*100:.0f}%)"
        )
    lines.append("")
    lines.append("Per-decision:")
    for ev in summary.evaluated[-10:]:
        lines.append(
            f"  {ev.decided_at}  {ev.decision_id}  {ev.aggressive_code} %{int(ev.aggressive_ratio*100)} + "
            f"{ev.defensive_code} %{int(ev.defensive_ratio*100)}  → {ev.portfolio_return_pct:+.2f}% "
            f"(vs MM {ev.outperformance_vs_money_market_pct:+.2f}%)"
        )
    return "\n".join(lines)
