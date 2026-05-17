"""TEFAS fund asset-allocation profiler.

pytefas's `breakdown` view exposes the real asset composition of each TEFAS fund
(stock_ratio, bond_ratio, money_market_ratio, foreign_equity_ratio, gold_ratio,
participation_ratio, etc. — 50+ columns). This module fetches that view and
enriches the metadata frame so downstream layers can:

- Detect money market funds deterministically (money_market_ratio > threshold)
  instead of pattern-matching on the fund name (kırılgan + cache hit'te kayboluyor).
- Surface real sector / asset class exposure in reports.
- Provide structural inputs to regime/conviction logic in the future.

If the breakdown call fails (rate-limit, schema change, optional library missing),
this layer degrades silently: it returns the input metadata unchanged and records
the failure in `unavailable_data`. The engine never blocks on enrichment failure.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional

import pandas as pd


# Money market funds have very high participation in money market instruments,
# repos, and short-term deposits. Threshold is conservative: a "real" YPF
# (Para Piyasası Fonu) is usually >95% money market instruments. We allow a
# little headroom (85%) so that a fund holding 90% money market and 10% short
# bonds still qualifies.
MONEY_MARKET_RATIO_THRESHOLD = 0.85

# pytefas breakdown columns we care about. The actual list is much larger; we
# read defensively and tolerate missing columns.
BREAKDOWN_COLUMNS_OF_INTEREST = [
    "money_market",
    "reverse_repo",
    "repo",
    "stock",
    "foreign_equity",
    "foreign_debt",
    "government_bond",
    "private_sector_bond",
    "treasury_bill",
    "bank_bills",
    "gold",
    "other_precious_metals",
    "real_estate_certificate",
    "fund_participation",
    "deposit_tl",
    "deposit_fx",
    "term_deposit_tl",
    "term_deposit_fx",
]


@dataclass(frozen=True)
class FundProfile:
    code: str
    breakdown: Dict[str, float] = field(default_factory=dict)
    money_market_ratio: float = 0.0
    equity_ratio: float = 0.0
    foreign_equity_ratio: float = 0.0
    bond_ratio: float = 0.0
    gold_ratio: float = 0.0
    is_money_market: bool = False
    dominant_class: str = "unknown"
    summary: str = ""


@dataclass
class FundProfilerResult:
    profiles: Dict[str, FundProfile] = field(default_factory=dict)
    verified_data: List[str] = field(default_factory=list)
    unavailable_data: List[str] = field(default_factory=list)


class FundProfiler:
    """Enrich funds with their real asset-allocation breakdown.

    Uses pytefas's breakdown view when available; degrades silently otherwise.
    Failure here NEVER blocks the engine — money market detection falls back to
    the keyword-based path in UniverseBuilder.
    """

    def __init__(self, lookback_days: int = 10):
        self.lookback_days = lookback_days

    def profile(self, codes: List[str]) -> FundProfilerResult:
        codes = [c.strip().upper() for c in codes if c.strip()]
        if not codes:
            return FundProfilerResult(unavailable_data=["fund profiler called with empty code list"])
        try:
            from pytefas import Crawler  # type: ignore
        except ImportError:
            return FundProfilerResult(unavailable_data=["pytefas not installed; fund breakdown enrichment skipped"])
        crawler = Crawler(timeout=90, max_retry=6)
        start = date.today() - timedelta(days=self.lookback_days)
        end = date.today()
        profiles: Dict[str, FundProfile] = {}
        verified: List[str] = []
        unavailable: List[str] = []
        for code in codes:
            try:
                df = crawler.fetch(start=start, end=end, kind="YAT", columns="breakdown", fund_code=code)
            except Exception as exc:
                unavailable.append(f"breakdown fetch failed for {code}: {type(exc).__name__}: {exc}")
                continue
            if df is None or df.empty:
                unavailable.append(f"breakdown returned empty for {code}")
                continue
            profile = self._build_profile(code, df)
            if profile is not None:
                profiles[code] = profile
                verified.append(f"breakdown profiled: {code} ({profile.dominant_class})")
        if profiles:
            verified.insert(0, f"fund breakdown profiler enriched {len(profiles)} of {len(codes)} funds")
        return FundProfilerResult(profiles=profiles, verified_data=verified, unavailable_data=unavailable)

    def _build_profile(self, code: str, df: pd.DataFrame) -> Optional[FundProfile]:
        latest = df.sort_values("date").iloc[-1].to_dict() if "date" in df.columns else df.iloc[-1].to_dict()
        breakdown: Dict[str, float] = {}
        for col in BREAKDOWN_COLUMNS_OF_INTEREST:
            value = latest.get(col)
            try:
                if value is None or (isinstance(value, float) and pd.isna(value)):
                    continue
                f = float(value)
            except (TypeError, ValueError):
                continue
            # pytefas breakdown values are percentages 0..100. Normalize to 0..1.
            if f > 1.5:
                f = f / 100.0
            if f <= 0:
                continue
            breakdown[col] = round(f, 4)
        if not breakdown:
            return None
        money_market_ratio = sum(breakdown.get(k, 0.0) for k in ("money_market", "reverse_repo", "repo", "deposit_tl", "term_deposit_tl"))
        equity_ratio = breakdown.get("stock", 0.0)
        foreign_equity_ratio = breakdown.get("foreign_equity", 0.0)
        bond_ratio = sum(breakdown.get(k, 0.0) for k in ("government_bond", "private_sector_bond", "treasury_bill", "bank_bills", "foreign_debt"))
        gold_ratio = sum(breakdown.get(k, 0.0) for k in ("gold", "other_precious_metals"))
        ratios = {
            "money_market": money_market_ratio,
            "equity_tr": equity_ratio,
            "equity_foreign": foreign_equity_ratio,
            "bond": bond_ratio,
            "gold": gold_ratio,
        }
        dominant_class = max(ratios, key=ratios.get) if any(ratios.values()) else "unknown"
        is_money_market = money_market_ratio >= MONEY_MARKET_RATIO_THRESHOLD
        summary = ", ".join(f"{k} {v*100:.0f}%" for k, v in ratios.items() if v >= 0.05) or "diversified"
        return FundProfile(
            code=code,
            breakdown=breakdown,
            money_market_ratio=round(money_market_ratio, 4),
            equity_ratio=round(equity_ratio, 4),
            foreign_equity_ratio=round(foreign_equity_ratio, 4),
            bond_ratio=round(bond_ratio, 4),
            gold_ratio=round(gold_ratio, 4),
            is_money_market=is_money_market,
            dominant_class=dominant_class,
            summary=summary,
        )
