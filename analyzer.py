from __future__ import annotations

import numpy as np
import pandas as pd

from models import FundMetrics


class FundAnalyzer:
    def analyze_fund(self, code: str, name: str, category: str, history: pd.DataFrame) -> FundMetrics:
        df = history.copy().dropna(subset=["price"]).sort_values("date")
        prices = df["price"].astype(float).reset_index(drop=True)
        notes = []
        if len(prices) < 2:
            return FundMetrics(code, name, category, len(prices), "", 0, 0, 0, 0, 0, 0, False, False, False, False, "insufficient", ["insufficient price history"])

        def ret(periods: int) -> float:
            if len(prices) <= periods:
                return float(prices.iloc[-1] / prices.iloc[0] - 1)
            return float(prices.iloc[-1] / prices.iloc[-periods - 1] - 1)

        returns = prices.pct_change().dropna()
        recent_returns = returns.tail(3)
        volatility_3m = float(recent_returns.std(ddof=0) * np.sqrt(12)) if len(recent_returns) else 0.0
        cumulative = prices / prices.cummax() - 1
        max_drawdown = float(cumulative.min())
        ma3 = float(prices.tail(3).mean())
        ma6 = float(prices.tail(6).mean()) if len(prices) >= 6 else ma3
        x = np.arange(len(prices.tail(6)))
        y = prices.tail(6).to_numpy()
        trend_slope = float(np.polyfit(x, y, 1)[0] / max(y.mean(), 1e-9)) if len(y) >= 2 else 0.0
        r1, r3, r6 = ret(1), ret(3), ret(6)
        price_above_ma3 = bool(prices.iloc[-1] >= ma3)
        price_above_ma6 = bool(prices.iloc[-1] >= ma6)
        absolute_momentum = bool(r3 > 0 and r6 > 0)
        trend_confirmed = bool(price_above_ma3 and price_above_ma6 and trend_slope > 0)
        if volatility_3m > 1.2:
            notes.append("high volatility")
        if max_drawdown < -0.25:
            notes.append("large trailing drawdown")
        data_quality = "ok" if len(prices) >= 6 else "thin"
        return FundMetrics(
            code=code,
            name=name,
            category=category,
            observations=len(prices),
            latest_date=str(pd.to_datetime(df["date"].iloc[-1]).date()),
            return_1m=r1,
            return_3m=r3,
            return_6m=r6,
            volatility_3m=volatility_3m,
            max_drawdown=max_drawdown,
            trend_slope=trend_slope,
            price_above_ma3=price_above_ma3,
            price_above_ma6=price_above_ma6,
            absolute_momentum=absolute_momentum,
            trend_confirmed=trend_confirmed,
            data_quality=data_quality,
            notes=notes,
        )
