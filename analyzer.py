"""Per-fund quantitative analyzer.

All return/volatility/MA windows are **calendar-based**, not "last N observations".
With daily TEFAS data, "3M return" must mean "price now vs. price ~90 calendar
days ago", NOT "price now vs. 3 observations ago". The old implementation
silently computed the latter, which made every recommendation wrong against
real (daily) provider data.

Volatility annualization auto-detects observation frequency: daily-ish ->
sqrt(252), weekly-ish -> sqrt(52), monthly-ish -> sqrt(12). This keeps the
metric meaningful regardless of provider granularity and keeps legacy
monthly-data tests honest.

data_quality:
  - "ok": enough history for 6M (≥180 calendar days) AND ≥20 observations
  - "thin": at least 3M (≥90 days) but not 6M
  - "insufficient": cannot even compute 3M; metric is unreliable; scorer skips
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from models import FundMetrics


class FundAnalyzer:
    def analyze_fund(self, code: str, name: str, category: str, history: pd.DataFrame) -> FundMetrics:
        df = history.copy().dropna(subset=["price"]).sort_values("date").reset_index(drop=True)
        if df.empty or len(df) < 2:
            return FundMetrics(code, name, category, len(df), "", 0, 0, 0, 0, 0, 0, False, False, False, False, "insufficient", ["insufficient price history"])
        df["date"] = pd.to_datetime(df["date"])
        prices = df["price"].astype(float)
        latest_date = df["date"].iloc[-1]
        latest_price = float(prices.iloc[-1])
        notes: list = []

        r1 = _calendar_return(df, days=30)
        r3 = _calendar_return(df, days=90)
        r6 = _calendar_return(df, days=180)

        ann_factor = _annualization_factor(df["date"])
        volatility_3m = _calendar_volatility(df, days=90, annualization_factor=ann_factor)
        max_drawdown = float((prices / prices.cummax() - 1).min())

        ma_short = _calendar_mean_price(df, days=30)
        ma_long = _calendar_mean_price(df, days=90)
        trend_slope = _calendar_trend_slope(df, days=90)

        price_above_ma_short = bool(ma_short is not None and latest_price >= ma_short)
        price_above_ma_long = bool(ma_long is not None and latest_price >= ma_long)
        absolute_momentum = bool(r3 is not None and r6 is not None and r3 > 0 and r6 > 0)
        trend_confirmed = bool(price_above_ma_short and price_above_ma_long and trend_slope is not None and trend_slope > 0)

        data_quality = _classify_quality(df, latest_date, r3, r6, ann_factor)
        if data_quality == "insufficient":
            notes.append("insufficient calendar lookback: <90 days of usable history")
        elif data_quality == "thin":
            notes.append("thin calendar lookback: 6M return not computable, 3M only")
        if volatility_3m > 1.2:
            notes.append("high volatility")
        if max_drawdown < -0.25:
            notes.append("large trailing drawdown")
        # Anomaly notes (kept from previous behavior)
        returns = prices.pct_change().dropna()
        if len(returns) and returns.abs().max() > 0.10:
            jumps = int((returns.abs() > 0.10).sum())
            notes.append(f"price anomaly: {jumps} observation(s) with >10% absolute return — possible split/corporate action/data spike")
        date_diffs = df["date"].diff().dt.days.dropna()
        if not date_diffs.empty:
            max_gap = int(date_diffs.max())
            if max_gap > 14:
                notes.append(f"price gap: longest interval between observations is {max_gap} days")

        return FundMetrics(
            code=code,
            name=name,
            category=category,
            observations=len(df),
            latest_date=str(latest_date.date()),
            return_1m=_or_zero(r1),
            return_3m=_or_zero(r3),
            return_6m=_or_zero(r6),
            volatility_3m=volatility_3m,
            max_drawdown=max_drawdown,
            trend_slope=trend_slope if trend_slope is not None else 0.0,
            price_above_ma3=price_above_ma_short,
            price_above_ma6=price_above_ma_long,
            absolute_momentum=absolute_momentum,
            trend_confirmed=trend_confirmed,
            data_quality=data_quality,
            notes=notes,
        )


def _calendar_return(df: pd.DataFrame, days: int) -> Optional[float]:
    """Return computed over a calendar-day window.

    Looks for the price observation whose date is on/before (latest_date - days)
    and closest to that target. This tolerates weekends, holidays and sparse
    data, and answers what the metric NAME claims (1M/3M/6M) instead of
    "last N observations".
    """
    if df.empty:
        return None
    latest_date = df["date"].iloc[-1]
    latest_price = float(df["price"].iloc[-1])
    if latest_price <= 0:
        return None
    target = latest_date - pd.Timedelta(days=days)
    eligible = df[df["date"] <= target]
    if eligible.empty:
        return None
    base_price = float(eligible.iloc[-1]["price"])
    if base_price <= 0:
        return None
    return latest_price / base_price - 1


def _calendar_volatility(df: pd.DataFrame, days: int, annualization_factor: float) -> float:
    if df.empty:
        return 0.0
    latest_date = df["date"].iloc[-1]
    cutoff = latest_date - pd.Timedelta(days=days)
    window = df[df["date"] > cutoff]
    if len(window) < 2:
        return 0.0
    returns = window["price"].astype(float).pct_change().dropna()
    if returns.empty:
        return 0.0
    return float(returns.std(ddof=0) * np.sqrt(annualization_factor))


def _calendar_mean_price(df: pd.DataFrame, days: int) -> Optional[float]:
    if df.empty:
        return None
    latest_date = df["date"].iloc[-1]
    cutoff = latest_date - pd.Timedelta(days=days)
    window = df[df["date"] > cutoff]
    if window.empty:
        return None
    return float(window["price"].astype(float).mean())


def _calendar_trend_slope(df: pd.DataFrame, days: int) -> Optional[float]:
    if df.empty:
        return None
    latest_date = df["date"].iloc[-1]
    cutoff = latest_date - pd.Timedelta(days=days)
    window = df[df["date"] > cutoff]
    if len(window) < 2:
        return None
    y = window["price"].astype(float).to_numpy()
    x = np.arange(len(y))
    slope = float(np.polyfit(x, y, 1)[0])
    mean = max(float(y.mean()), 1e-9)
    return slope / mean


def _annualization_factor(dates: pd.Series) -> float:
    """Auto-detect observation frequency and return the sqrt-able annualization
    base. ~1-2 day gaps -> 252 (daily). ~7 -> 52 (weekly). >=20 -> 12 (monthly).
    """
    diffs = pd.to_datetime(dates).sort_values().diff().dropna().dt.days
    if diffs.empty:
        return 252.0
    median_gap = float(diffs.median())
    if median_gap <= 2.5:
        return 252.0
    if median_gap <= 10:
        return 52.0
    return 12.0


def _classify_quality(df: pd.DataFrame, latest_date: pd.Timestamp, r3: Optional[float], r6: Optional[float], ann_factor: float) -> str:
    if r3 is None:
        return "insufficient"
    if r6 is None:
        return "thin"
    # Min observation count depends on observation frequency. Daily data needs
    # many more observations than monthly data to compute a stable 6M signal.
    if ann_factor >= 200:
        min_obs = 60   # ≈3 months of business days
    elif ann_factor >= 40:
        min_obs = 12   # weekly
    else:
        min_obs = 5    # monthly
    if len(df) < min_obs:
        return "thin"
    span_days = (latest_date - df["date"].iloc[0]).days
    if span_days < 90:
        return "insufficient"
    if span_days < 180:
        return "thin"
    return "ok"


def _or_zero(x: Optional[float]) -> float:
    return float(x) if x is not None else 0.0
