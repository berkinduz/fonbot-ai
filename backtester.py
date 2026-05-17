from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class BacktestResult:
    periods: int
    total_return: float
    cagr: float
    volatility: float
    max_drawdown: float
    sharpe: float


class SimpleBacktester:
    """Monthly equal placeholder backtest utilities; no fake precision, no parameter search."""

    def evaluate_returns(self, monthly_returns: pd.Series) -> BacktestResult:
        r = monthly_returns.dropna().astype(float)
        if r.empty:
            return BacktestResult(0, 0, 0, 0, 0, 0)
        equity = (1 + r).cumprod()
        total = float(equity.iloc[-1] - 1)
        years = max(len(r) / 12, 1 / 12)
        cagr = float(equity.iloc[-1] ** (1 / years) - 1)
        vol = float(r.std(ddof=0) * np.sqrt(12))
        dd = float((equity / equity.cummax() - 1).min())
        sharpe = float((r.mean() * 12) / vol) if vol else 0.0
        return BacktestResult(len(r), total, cagr, vol, dd, sharpe)
