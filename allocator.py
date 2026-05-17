from __future__ import annotations

import hashlib
from typing import List

from config import FundbotConfig
from models import AllocationDecision, DataIntegrity, FundLeg, utc_now_iso


class FundAllocator:
    def __init__(self, config: FundbotConfig):
        self.config = config

    def allocate(
        self,
        opportunity_code: str,
        opportunity_name: str,
        opportunity_score: float,
        money_market_code: str,
        money_market_name: str,
        regime_score: float,
        risk_penalty: float,
        previous_code: str | None = None,
    ) -> AllocationDecision:
        composite = max(0.0, min(100.0, opportunity_score * 0.70 + regime_score * 0.25 - risk_penalty * 0.05))
        if composite >= 80:
            aggressive_ratio = 0.90
        elif composite >= 70:
            aggressive_ratio = 0.75
        elif composite >= 58:
            aggressive_ratio = 0.65
        elif composite >= 45:
            aggressive_ratio = 0.50
        else:
            aggressive_ratio = 0.35
        defensive_ratio = round(1.0 - aggressive_ratio, 2)
        action = "BUY" if previous_code is None else ("HOLD" if previous_code == opportunity_code else "SWITCH")
        if previous_code == opportunity_code and aggressive_ratio < 0.65:
            action = "REDUCE"
        elif previous_code == opportunity_code and aggressive_ratio >= 0.75:
            action = "INCREASE"
        created_at = utc_now_iso()
        raw_id = f"{created_at}-{opportunity_code}-{money_market_code}"
        decision_id = "fundbot-" + hashlib.sha1(raw_id.encode()).hexdigest()[:12]
        reasons = [
            "Momentum is treated as the primary signal; regime and volatility only modify sizing.",
            f"Composite conviction {composite:.1f}/100 produced {int(aggressive_ratio*100)}% aggressive allocation.",
            "Defensive leg remains a money market buffer, not the main return engine.",
        ]
        data_integrity = DataIntegrity(
            verified_data=[self.config.verified_quant_label],
            unavailable_data=[
                "X/Twitter sentiment not accessed by this local engine",
                "live news not accessed by this local engine",
                "broker-specific liquidity/availability must be checked manually before execution",
            ],
            estimated_data=["regime score may be neutral fallback if macro proxies are unavailable"],
            user_provided_data=[],
        )
        return AllocationDecision(
            decision_id=decision_id,
            created_at=created_at,
            action=action,
            aggressive_fund=FundLeg(opportunity_code, opportunity_name, aggressive_ratio, "main_opportunity"),
            defensive_fund=FundLeg(money_market_code, money_market_name, defensive_ratio, "defensive_money_market"),
            aggressive_ratio=aggressive_ratio,
            defensive_ratio=defensive_ratio,
            confidence=round(composite, 2),
            regime_score=round(regime_score, 2),
            opportunity_score=round(opportunity_score, 2),
            risk_penalty=round(risk_penalty, 2),
            reasons=reasons,
            rerun_triggers=[
                "3M momentum breaks down or top fund rank deteriorates materially",
                "volatility spike or trailing drawdown accelerates",
                "macro regime shifts sharply",
                "credible external research shows fund-specific structural issue",
            ],
            data_integrity=data_integrity,
        )
