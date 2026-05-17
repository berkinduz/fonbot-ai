"""Translate scanned external context into bounded decision modifiers.

This is the layer that decides how much the engine should adjust risk,
regime, confidence and avoid-fund behavior in response to external evidence.

Hard rules:

- Quantitative TEFAS momentum remains the primary signal. This layer only
  *modifies* — it never replaces.
- Modifiers are bounded (max 100 risk delta, max -60 regime delta). One bad
  news item cannot collapse the whole decision.
- All deltas are derived from explicit evidence in the context payload. If
  evidence is absent, deltas are 0 (not negative-by-default).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List


@dataclass(frozen=True)
class ExternalIntelligenceResult:
    risk_penalty_delta: float = 0.0
    regime_score_delta: float = 0.0
    confidence_cap: float | None = None
    avoid_funds: List[str] = field(default_factory=list)
    verified_data: List[str] = field(default_factory=list)
    unavailable_data: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    rerun_triggers: List[str] = field(default_factory=list)


class ExternalIntelligenceAnalyzer:
    """Bounded modifier translator. Quant primary, this layer only modifies."""

    STRUCTURAL_KEYWORDS = {
        "tasfiye",
        "işlem durdur",
        "islem durdur",
        "durdurma",
        "soruşturma",
        "sorusturma",
        "manipülasyon",
        "manipulasyon",
        "strateji değişikliği",
        "unvan değişikliği",
        "yönetim değişikliği",
    }
    MARKET_STRESS_KEYWORDS = {"sert satış", "tedirgin", "panik", "çıkış", "regülasyon", "volatilite"}

    def analyze(self, context: dict | None) -> ExternalIntelligenceResult:
        context = context or {}
        raw_sections = context.get("sections")
        sections = raw_sections if isinstance(raw_sections, dict) else {}
        risk = 0.0
        regime_delta = 0.0
        caps: List[float] = []
        avoid: set[str] = set()
        verified: List[str] = []
        unavailable: List[str] = []
        reasons: List[str] = []
        triggers: List[str] = []

        macro = sections.get("macro_regime") or {}
        macro_items = macro.get("items") if isinstance(macro, dict) else []
        if macro_items:
            verified.append("external intelligence macro modifiers evaluated")
        else:
            unavailable.append("external intelligence macro items missing")
        for item in macro_items or []:
            label = str(item.get("label", "")).upper()
            change = _to_float(item.get("change_1m_pct"))
            if change is None:
                continue
            if label == "USDTRY" and change > 5:
                risk += 8
                regime_delta -= 5
                reasons.append(f"External intelligence: USDTRY rose {change:.1f}% over 1M; FX stress reduces aggressive conviction.")
                triggers.append("macro FX stress changes materially")
            if label in {"BIST100", "XU100.IS"} and change < -8:
                risk += 14
                regime_delta -= 12
                caps.append(75)
                reasons.append(f"External intelligence: BIST100 fell {abs(change):.1f}% over 1M; local equity backdrop weakened.")
                triggers.append("macro equity backdrop recovers or deteriorates further")
            if label == "NASDAQ" and change < -8:
                risk += 8
                regime_delta -= 6
                reasons.append(f"External intelligence: Nasdaq fell {abs(change):.1f}% over 1M; global risk appetite weakened.")
                triggers.append("global risk appetite changes materially")
            if label == "GOLD" and change > 8:
                risk += 4
                regime_delta -= 3
                reasons.append(f"External intelligence: gold rose {change:.1f}% over 1M; defensive demand may be elevated.")

        rates = sections.get("rates_inflation") or {}
        rate_items = rates.get("items") if isinstance(rates, dict) else []
        if rate_items:
            verified.append("external intelligence rates/inflation modifiers evaluated")
        else:
            unavailable.append("policy-rate/inflation items missing")
        for item in rate_items or []:
            policy = _to_float(item.get("policy_rate") or item.get("rate"))
            inflation = _to_float(item.get("inflation_yoy") or item.get("inflation"))
            if policy is not None and inflation is not None:
                real_rate = policy - inflation
                if real_rate < -10:
                    risk += 10
                    regime_delta -= 8
                    caps.append(75)
                    reasons.append(f"External intelligence: negative real-rate gap {real_rate:.1f}pp; macro pressure lowers conviction.")
                    triggers.append("policy-rate/inflation real-rate gap changes materially")
                elif real_rate > 5 and policy >= 35:
                    risk += 2
                    reasons.append(f"External intelligence: high positive real-rate gap {real_rate:.1f}pp keeps money-market alternative attractive.")
            elif policy is not None and policy >= 40:
                reasons.append(f"External intelligence: policy rate {policy:.1f}% keeps defensive leg relevant.")

        market_news = sections.get("market_news") or {}
        fund_specific = sections.get("fund_specific") or {}
        all_news_items = []
        if isinstance(market_news, dict):
            all_news_items.extend(market_news.get("items") or [])
        if isinstance(fund_specific, dict):
            all_news_items.extend(fund_specific.get("items") or [])
        if all_news_items:
            verified.append("external intelligence news/fund-specific risk modifiers evaluated")
        else:
            unavailable.append("news/fund-specific items missing or clean")

        for item in all_news_items:
            title = str(item.get("title", ""))
            text = title.lower()
            query = str(item.get("query", ""))
            code = _extract_code(query, title)
            if any(keyword in text for keyword in self.STRUCTURAL_KEYWORDS):
                risk += 25
                regime_delta -= 15
                caps.append(55)
                if code:
                    avoid.add(code)
                reasons.append(f"External intelligence: structural fund/news risk detected: {title[:140]}")
                triggers.append("structural fund-specific risk is clarified or resolved")
            elif any(keyword in text for keyword in self.MARKET_STRESS_KEYWORDS):
                risk += 6
                regime_delta -= 4
                reasons.append(f"External intelligence: market stress narrative detected: {title[:140]}")
                triggers.append("market stress narrative clears")

        if context.get("risks"):
            for item in context.get("risks") or []:
                if str(item).strip():
                    risk += 4
                    reasons.append(f"External intelligence: scanner risk note: {item}")

        cap = min(caps) if caps else None
        return ExternalIntelligenceResult(
            risk_penalty_delta=round(min(risk, 100), 2),
            regime_score_delta=round(max(regime_delta, -60), 2),
            confidence_cap=cap,
            avoid_funds=sorted(avoid),
            verified_data=verified,
            unavailable_data=unavailable,
            reasons=_dedupe(reasons),
            rerun_triggers=_dedupe(triggers),
        )


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_code(query: str, title: str) -> str | None:
    tokens = (query + " " + title).upper().replace("-", " ").replace("/", " ").split()
    for token in tokens:
        cleaned = "".join(ch for ch in token if ch.isalnum())
        if 2 <= len(cleaned) <= 5 and cleaned.isupper() and any(ch.isalpha() for ch in cleaned):
            if cleaned not in {"KAP", "TEFAS", "FON", "TL", "USD", "BIST"}:
                return cleaned
    return None


def _dedupe(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
