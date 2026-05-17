from __future__ import annotations

import hashlib
from typing import List, Optional

from config import FundbotConfig
from models import AllocationDecision, DataIntegrity, FundLeg, utc_now_iso
from strategy_loader import load_weights


class FundAllocator:
    def __init__(self, config: FundbotConfig, weights: Optional[dict] = None):
        self.config = config
        cfg = weights or load_weights()
        self.a = cfg["allocator"]

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
        external_verified_data: Optional[List[str]] = None,
        external_unavailable_data: Optional[List[str]] = None,
        external_user_provided_data: Optional[List[str]] = None,
        confidence_cap: Optional[float] = None,
        external_reasons: Optional[List[str]] = None,
        external_rerun_triggers: Optional[List[str]] = None,
    ) -> AllocationDecision:
        composite = max(0.0, min(100.0, opportunity_score * self.a["opportunity_weight"] + regime_score * self.a["regime_weight"] - risk_penalty * self.a["risk_penalty_weight"]))
        if confidence_cap is not None:
            composite = min(composite, float(confidence_cap))
        aggressive_ratio = self._band_for(composite)
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
        if confidence_cap is not None:
            reasons.append(f"Confidence capped at {confidence_cap:.0f}/100 by external context gate.")
        reasons.extend(external_reasons or [])
        verified = [self.config.verified_quant_label]
        verified.extend(external_verified_data or [])
        unavailable = [
            "broker-specific liquidity/availability must be checked manually before execution",
        ]
        unavailable.extend(external_unavailable_data or [])
        user_provided = list(external_user_provided_data or [])
        data_integrity = DataIntegrity(
            verified_data=verified,
            unavailable_data=unavailable,
            estimated_data=["regime score modified by external context macro proxies when present"],
            user_provided_data=user_provided,
        )
        rerun_triggers = [
            "3M momentum breaks down or top fund rank deteriorates materially",
            "volatility spike or trailing drawdown accelerates",
            "macro regime shifts sharply",
            "credible external research shows fund-specific structural issue",
        ]
        rerun_triggers.extend(external_rerun_triggers or [])
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
            rerun_triggers=rerun_triggers,
            data_integrity=data_integrity,
        )

    def _band_for(self, composite: float) -> float:
        for band in sorted(self.a["bands"], key=lambda b: -b["min_conviction"]):
            if composite >= band["min_conviction"]:
                return float(band["aggressive_ratio"])
        return float(self.a["bands"][-1]["aggressive_ratio"])
