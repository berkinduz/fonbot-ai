from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional


_DEFAULT_WEIGHTS: Dict[str, Any] = {
    "scorer": {
        "return_3m_weight": 0.45,
        "return_6m_weight": 0.25,
        "return_1m_weight": 0.10,
        "return_3m_cap_high": 80, "return_3m_cap_low": -50,
        "return_6m_cap_high": 90, "return_6m_cap_low": -50,
        "return_1m_cap_high": 40, "return_1m_cap_low": -30,
        "absolute_momentum_bonus": 12,
        "trend_confirmed_bonus": 12,
        "acceleration_bonus": 4,
        "volatility_penalty_multiplier": 10,
        "volatility_penalty_cap": 15,
        "drawdown_penalty_multiplier": 40,
        "drawdown_penalty_cap": 18,
        "score_offset": 50,
        "confidence_offset": 35,
    },
    "money_market_scorer": {
        "base": 50,
        "return_1m_multiplier": 1200,
        "return_1m_bonus_min": -10,
        "return_1m_bonus_max": 30,
        "drawdown_penalty_multiplier": 100,
        "drawdown_penalty_cap": 15,
    },
    "allocator": {
        "opportunity_weight": 0.70,
        "regime_weight": 0.25,
        "risk_penalty_weight": 0.05,
        "bands": [
            {"min_conviction": 80, "aggressive_ratio": 0.90},
            {"min_conviction": 70, "aggressive_ratio": 0.75},
            {"min_conviction": 58, "aggressive_ratio": 0.65},
            {"min_conviction": 45, "aggressive_ratio": 0.50},
            {"min_conviction": 0,  "aggressive_ratio": 0.35},
        ],
    },
}


def load_weights(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load strategy weights JSON.

    If the file is missing or corrupted, return built-in defaults. The defaults
    must always match the JSON shipped in strategy/weights.json so behavior is
    identical whether the file exists or not.
    """
    weights_path = Path(path) if path else Path(__file__).resolve().parent / "strategy" / "weights.json"
    if not weights_path.exists():
        return _DEFAULT_WEIGHTS
    try:
        data = json.loads(weights_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _DEFAULT_WEIGHTS
    merged = {section: {**defaults, **data.get(section, {})} for section, defaults in _DEFAULT_WEIGHTS.items() if section != "allocator"}
    allocator = {**_DEFAULT_WEIGHTS["allocator"], **data.get("allocator", {})}
    if "bands" in data.get("allocator", {}):
        allocator["bands"] = data["allocator"]["bands"]
    merged["allocator"] = allocator
    return merged
