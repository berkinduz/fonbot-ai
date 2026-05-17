from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

from config import FundbotConfig
from models import FundRecord


class UniverseBuilder:
    def __init__(self, config: FundbotConfig):
        self.config = config

    def build(self, metadata: pd.DataFrame, histories: Dict[str, pd.DataFrame], profiles: Optional[Dict[str, object]] = None) -> List[FundRecord]:
        """Build the investable universe.

        If `profiles` (FundProfile dict from FundProfiler) is provided, money
        market detection uses the real money_market_ratio from the TEFAS
        breakdown view. Otherwise it falls back to keyword matching on the
        fund name + category (kept for backward compatibility and degraded
        operation when pytefas breakdown is unavailable).
        """
        records: List[FundRecord] = []
        profiles = profiles or {}
        for row in metadata.to_dict("records"):
            code = str(row.get("code", "")).strip()
            if not code or code not in histories:
                continue
            hist = histories[code]
            if not self._history_ok(hist):
                continue
            category = str(row.get("category") or "unknown")
            name = str(row.get("name") or code)
            aum = row.get("aum")
            if aum is not None and pd.notna(aum) and float(aum) < self.config.min_aum:
                continue
            profile = profiles.get(code)
            if profile is not None and getattr(profile, "breakdown", None):
                is_money_market = bool(getattr(profile, "is_money_market", False))
            else:
                is_money_market = self._is_money_market(name, category)
            records.append(
                FundRecord(
                    code=code,
                    name=name,
                    category=category,
                    aum=None if aum is None or pd.isna(aum) else float(aum),
                    stock_ratio=None if row.get("stock_ratio") is None or pd.isna(row.get("stock_ratio")) else float(row.get("stock_ratio")),
                    is_money_market=is_money_market,
                )
            )
        return records

    def _history_ok(self, hist: pd.DataFrame) -> bool:
        if hist is None or hist.empty or "price" not in hist:
            return False
        clean = hist.dropna(subset=["price"]).sort_values("date")
        if len(clean) < self.config.min_history_months:
            return False
        if (clean["price"] <= 0).any():
            return False
        returns = clean["price"].pct_change().dropna()
        if returns.empty:
            return False
        if returns.abs().max() > self.config.anomaly_return_abs_limit:
            return False
        return True

    def _is_money_market(self, name: str, category: str) -> bool:
        text = f"{name} {category}".lower()
        return any(k in text for k in self.config.money_market_keywords)
