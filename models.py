from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class FundRecord:
    code: str
    name: str
    category: str
    aum: Optional[float] = None
    stock_ratio: Optional[float] = None
    is_money_market: bool = False


@dataclass(frozen=True)
class FundMetrics:
    code: str
    name: str
    category: str
    observations: int
    latest_date: str
    return_1m: float
    return_3m: float
    return_6m: float
    volatility_3m: float
    max_drawdown: float
    trend_slope: float
    price_above_ma3: bool
    price_above_ma6: bool
    absolute_momentum: bool
    trend_confirmed: bool
    data_quality: str
    notes: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ScoredFund:
    code: str
    name: str
    category: str
    score: float
    confidence: float
    metrics: FundMetrics
    reasons: List[str]
    rejections: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class RegimeSnapshot:
    score: float
    label: str
    verified_inputs: List[str]
    unavailable_inputs: List[str]
    notes: List[str]


@dataclass(frozen=True)
class FundLeg:
    code: str
    name: str
    ratio: float
    amount: float
    role: str


@dataclass(frozen=True)
class DataIntegrity:
    verified_data: List[str]
    unavailable_data: List[str]
    estimated_data: List[str]
    user_provided_data: List[str]


@dataclass(frozen=True)
class AllocationDecision:
    decision_id: str
    created_at: str
    amount: float
    action: str
    aggressive_fund: FundLeg
    defensive_fund: FundLeg
    aggressive_ratio: float
    defensive_ratio: float
    confidence: float
    regime_score: float
    opportunity_score: float
    risk_penalty: float
    reasons: List[str]
    rerun_triggers: List[str]
    data_integrity: DataIntegrity

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
