from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd

from config import FundbotConfig
from data_fetcher import TEFASDataFetcher
from data_providers import BaseDataProvider


def run_provider_smoke_checks(
    config: FundbotConfig,
    providers: Optional[List[BaseDataProvider]] = None,
    sample_code: str = "AFT",
    universe_codes: Optional[Iterable[str]] = None,
) -> List[Dict[str, object]]:
    """Run provider-layer smoke checks without producing recommendations.

    The checks are deliberately data-only. Failures are returned as structured
    rows so the caller can report provider health without allowing allocator or
    portfolio logic to proceed on unreliable data.
    """

    codes = list(universe_codes or [sample_code])
    checks: List[Dict[str, object]] = []

    short = _safe_fetch(config, providers, [sample_code], lookback_days=min(config.lookback_days, 45), force_refresh=True)
    checks.append(_row("single_fund_short_fetch", short, required_code=sample_code))

    long = _safe_fetch(config, providers, [sample_code], lookback_days=max(config.lookback_days, 230), force_refresh=True)
    checks.append(_row("single_fund_long_fetch", long, required_code=sample_code))

    universe = _safe_fetch(config, providers, codes, lookback_days=min(config.lookback_days, 90), force_refresh=True)
    checks.append(_row("small_universe_fetch", universe, min_histories=min(1, len(codes))))

    failed_provider_observed = any(
        "failed" in " ".join(result.get("unavailable_data", []))
        for result in [short, long, universe]
    )
    checks.append(
        {
            "name": "provider_failure_simulation",
            "status": "pass" if failed_provider_observed else "warn",
            "detail": "fallback path observed provider failure" if failed_provider_observed else "no provider failure observed in this run",
        }
    )

    stale = _stale_cache_check(config, sample_code)
    checks.append(stale)
    return checks


def _safe_fetch(config: FundbotConfig, providers: Optional[List[BaseDataProvider]], codes: List[str], lookback_days: int, force_refresh: bool) -> Dict[str, object]:
    scoped = FundbotConfig(
        root_dir=config.root_dir,
        cache_path=config.cache_path,
        reports_dir=config.reports_dir,
        history_path=config.history_path,
        min_history_months=config.min_history_months,
        lookback_days=lookback_days,
        tefas_kinds=config.tefas_kinds,
        cache_stale_after_days=config.cache_stale_after_days,
        provider_conflict_tolerance=config.provider_conflict_tolerance,
        manual_snapshot_path=config.manual_snapshot_path,
        min_aum=config.min_aum,
        anomaly_return_abs_limit=config.anomaly_return_abs_limit,
        money_market_keywords=config.money_market_keywords,
        defensive_min_score=config.defensive_min_score,
        aggressive_ratios=config.aggressive_ratios,
        verified_quant_label=config.verified_quant_label,
        tefas_inter_provider_backoff_seconds=config.tefas_inter_provider_backoff_seconds,
    )
    try:
        result = TEFASDataFetcher(scoped, providers=providers).fetch(codes=codes, force_refresh=force_refresh)
        return {
            "ok": bool(result.histories),
            "histories": result.histories,
            "metadata_empty": result.metadata.empty,
            "verified_data": result.verified_data,
            "unavailable_data": result.unavailable_data,
            "source_attribution": result.source_attribution,
            "provider_health": result.provider_health,
        }
    except Exception as exc:  # healthcheck reports; it does not raise through CLI flows
        return {"ok": False, "histories": {}, "unavailable_data": [f"healthcheck exception: {type(exc).__name__}: {exc}"]}


def _row(name: str, result: Dict[str, object], required_code: Optional[str] = None, min_histories: int = 1) -> Dict[str, object]:
    histories = result.get("histories", {}) or {}
    unavailable = result.get("unavailable_data", []) or []
    ok = bool(result.get("ok")) and len(histories) >= min_histories and (required_code is None or required_code in histories)
    return {
        "name": name,
        "status": "pass" if ok else "fail",
        "histories": sorted(histories.keys()) if isinstance(histories, dict) else [],
        "source_attribution": result.get("source_attribution", {}),
        "unavailable_data": unavailable,
    }


def _stale_cache_check(config: FundbotConfig, sample_code: str) -> Dict[str, object]:
    # Save deliberately old-dated prices under an isolated synthetic code; fetcher must report them stale and not return them as recommendation-grade data.
    stale_code = f"{sample_code}_STALE"
    fetcher = TEFASDataFetcher(config, providers=[])
    old = pd.DataFrame(
        {
            "date": pd.date_range("2020-01-01", periods=3, freq="30D"),
            "code": stale_code,
            "price": [10.0, 10.1, 10.2],
        }
    )
    fetcher.cache.save_prices(old, source="healthcheck-stale-simulation", fetched_at="2020-01-01T00:00:00+00:00")
    result = fetcher.fetch(codes=[stale_code], force_refresh=True)
    reported = any(age.code == stale_code and age.is_stale for age in result.cache_ages) or "stale cache" in " ".join(result.unavailable_data)
    return {
        "name": "stale_cache_simulation",
        "status": "pass" if reported and stale_code not in result.histories else "fail",
        "unavailable_data": result.unavailable_data,
    }
