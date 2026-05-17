from __future__ import annotations

from typing import Dict, List

import pandas as pd

from analyzer import FundAnalyzer
from models import RegimeSnapshot


class RegimeDetector:
    def detect(self, macro_histories: Dict[str, pd.DataFrame] | None = None) -> RegimeSnapshot:
        if not macro_histories:
            return RegimeSnapshot(
                score=60,
                label="neutral-unknown",
                verified_inputs=[],
                unavailable_inputs=[
                    "BIST/USDTRY/gold/Nasdaq live context not available in local cache",
                    "interest-rate and inflation context not fetched automatically",
                ],
                notes=["Regime layer degraded gracefully; allocation uses fund-level quantitative data as primary source."],
            )
        analyzer = FundAnalyzer()
        positives = 0
        total = 0
        verified: List[str] = []
        notes: List[str] = []
        for name, hist in macro_histories.items():
            if hist is None or hist.empty:
                continue
            m = analyzer.analyze_fund(name, name, "macro", hist)
            total += 1
            verified.append(f"macro proxy {name}")
            if m.absolute_momentum and m.trend_confirmed:
                positives += 1
        if total == 0:
            return self.detect(None)
        score = 40 + 60 * positives / total
        label = "strong" if score >= 75 else "mixed" if score >= 55 else "weak"
        return RegimeSnapshot(round(score, 2), label, verified, [], notes)
