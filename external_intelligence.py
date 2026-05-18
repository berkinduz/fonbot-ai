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
        official_macro = sections.get("official_macro") or {}
        macro_items = _merge_macro_items(
            macro.get("items") if isinstance(macro, dict) else [],
            official_macro.get("items") if isinstance(official_macro, dict) else [],
        )
        if macro_items:
            verified.append("external intelligence macro modifiers evaluated")
        else:
            unavailable.append("external intelligence macro items missing")
        for item in macro_items or []:
            label = str(item.get("label", "")).upper()
            c1m = _to_float(item.get("change_1m_pct"))
            c3m = _to_float(item.get("change_3m_pct"))
            c6m = _to_float(item.get("change_6m_pct"))
            if c1m is None and c3m is None:
                continue
            change = c1m if c1m is not None else c3m  # primary signal still 1M
            # Trend persistence multiplier: if 1M and 3M both negative for risk
            # assets, the move is a trend not a one-off; modifiers are amplified.
            persistent_down = (c1m is not None and c3m is not None and c1m < 0 and c3m < 0)
            persistent_up = (c1m is not None and c3m is not None and c1m > 0 and c3m > 0)
            if label in {"USDTRY", "EURTRY"} and change > 5:
                mult = 1.5 if persistent_up else 1.0
                risk += 8 * mult
                regime_delta -= 5 * mult
                reasons.append(f"External intelligence: {label} rose {change:.1f}% over 1M ({_window_summary(c1m, c3m, c6m)}); FX stress reduces aggressive conviction.")
                triggers.append("macro FX stress changes materially")
            if label in {"BIST100", "XU100.IS"} and change < -8:
                mult = 1.5 if persistent_down else 1.0
                risk += 14 * mult
                regime_delta -= 12 * mult
                caps.append(70 if persistent_down else 75)
                reasons.append(f"External intelligence: BIST100 fell {abs(change):.1f}% over 1M ({_window_summary(c1m, c3m, c6m)}); local equity backdrop weakened.")
                triggers.append("macro equity backdrop recovers or deteriorates further")
            if label in {"NASDAQ", "SP500"} and change < -8:
                mult = 1.5 if persistent_down else 1.0
                risk += 8 * mult
                regime_delta -= 6 * mult
                reasons.append(f"External intelligence: {label} fell {abs(change):.1f}% over 1M ({_window_summary(c1m, c3m, c6m)}); global risk appetite weakened.")
                triggers.append("global risk appetite changes materially")
            if label == "GOLD" and change > 8:
                risk += 4
                regime_delta -= 3
                reasons.append(f"External intelligence: gold rose {change:.1f}% over 1M ({_window_summary(c1m, c3m, c6m)}); defensive demand may be elevated.")
            if label == "VIX" and change is not None and change > 30:
                risk += 10
                regime_delta -= 6
                caps.append(75)
                reasons.append(f"External intelligence: VIX surged {change:.0f}% over 1M ({_window_summary(c1m, c3m, c6m)}); volatility regime is elevated.")
                triggers.append("volatility regime normalizes")
            if label == "BRENT" and change is not None and change > 12:
                risk += 5
                regime_delta -= 3
                reasons.append(f"External intelligence: Brent rose {change:.1f}% over 1M; inflation/import-cost headwind.")
            if label == "US10Y" and change is not None and abs(change) > 15:
                risk += 4
                reasons.append(f"External intelligence: US10Y yield moved {change:+.1f}% over 1M; rate environment is volatile.")
            if label == "EM_EQUITY" and change is not None and change < -8:
                risk += 5
                regime_delta -= 4
                reasons.append(f"External intelligence: EM equity benchmark fell {abs(change):.1f}% over 1M; broader EM weakness.")
        # Cross-asset divergence: TR-specific stress
        bist_change = next((_to_float(i.get("change_1m_pct")) for i in (macro_items or []) if str(i.get("label", "")).upper() == "BIST100"), None)
        usdtry_change = next((_to_float(i.get("change_1m_pct")) for i in (macro_items or []) if str(i.get("label", "")).upper() == "USDTRY"), None)
        if bist_change is not None and usdtry_change is not None and bist_change < -5 and usdtry_change > 3:
            risk += 8
            regime_delta -= 6
            caps.append(70)
            reasons.append(f"External intelligence: TR-specific stress detected (BIST {bist_change:+.1f}% while USDTRY {usdtry_change:+.1f}% over 1M).")
            triggers.append("TR-specific stress pattern (BIST↓ + USDTRY↑) resolves")

        rates = sections.get("rates_inflation") or {}
        official_rate_items = [
            item for item in ((official_macro.get("items") or []) if isinstance(official_macro, dict) else [])
            if item.get("policy_rate") is not None or item.get("inflation_yoy") is not None
        ]
        rate_items = official_rate_items or list((rates.get("items") or []) if isinstance(rates, dict) else [])
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

        # Cross-source confirmation for structural events:
        # - 1 non-KAP source mentioning a structural keyword = "candidate" (note only)
        # - 2+ sources OR ≥1 KAP source = "confirmed" → avoid_funds
        structural_hits: dict[str, list[dict]] = {}
        candidate_hits: list[dict] = []
        for item in all_news_items:
            title = str(item.get("title", ""))
            text = title.lower()
            query = str(item.get("query", ""))
            code = item.get("code") or _extract_code(query, title)
            is_kap = item.get("source") == "kap" or item.get("structural")
            if any(keyword in text for keyword in self.STRUCTURAL_KEYWORDS) or (is_kap and item.get("structural")):
                key = code or f"_uncoded_{title[:40]}"
                structural_hits.setdefault(key, []).append({"item": item, "is_kap": is_kap, "code": code})
            elif any(keyword in text for keyword in self.MARKET_STRESS_KEYWORDS):
                risk += 6
                regime_delta -= 4
                reasons.append(f"External intelligence: market stress narrative detected: {title[:140]}")
                triggers.append("market stress narrative clears")

        for key, hits in structural_hits.items():
            source_count = len(hits)
            has_kap = any(h["is_kap"] for h in hits)
            code = next((h["code"] for h in hits if h["code"]), None)
            sample_title = str(hits[0]["item"].get("title", ""))[:140]
            if has_kap or source_count >= 2:
                # Confirmed structural risk
                risk += 25
                regime_delta -= 15
                caps.append(55)
                if code:
                    avoid.add(code)
                src_summary = "KAP + " + str(source_count - 1) + " other" if has_kap else f"{source_count} sources"
                reasons.append(f"External intelligence: CONFIRMED structural risk ({src_summary}) for {code or '(no code)'}: {sample_title}")
                triggers.append("structural fund-specific risk is clarified or resolved")
            else:
                # Single non-KAP source — candidate only, no avoid
                risk += 8
                candidate_hits.append({"code": code, "title": sample_title})
                reasons.append(f"External intelligence: candidate structural signal (single source, unconfirmed) for {code or '(no code)'}: {sample_title}. Add to watchlist; not auto-avoided.")
                triggers.append("single-source structural signal gets corroborated or refuted")

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


def _window_summary(c1m: float | None, c3m: float | None, c6m: float | None) -> str:
    parts = []
    if c1m is not None:
        parts.append(f"1M {c1m:+.1f}%")
    if c3m is not None:
        parts.append(f"3M {c3m:+.1f}%")
    if c6m is not None:
        parts.append(f"6M {c6m:+.1f}%")
    return " / ".join(parts)


def _merge_macro_items(base_items: list, official_items: list) -> list:
    """Prefer official observations over same-label market proxies."""
    merged: dict[str, dict] = {}
    order: list[str] = []
    for item in list(base_items or []) + list(official_items or []):
        if "change_1m_pct" not in item:
            continue
        label = str(item.get("label") or "").upper()
        if not label:
            continue
        if label not in merged:
            order.append(label)
        merged[label] = item
    return [merged[label] for label in order]


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
