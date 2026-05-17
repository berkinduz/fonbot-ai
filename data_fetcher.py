from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd

from cache import SQLiteCache
from config import FundbotConfig
from data_providers import (
    BaseDataProvider,
    CacheAge,
    DataFetchResult,
    DirectTEFASProvider,
    ManualSnapshotProvider,
    ProviderOrchestrator,
    PytefasProvider,
    TEFASCrawlerProvider,
)

log = logging.getLogger(__name__)


FetchResult = DataFetchResult


class TEFASDataFetcher:
    """TEFAS data layer with provider fallback, cache safety and explicit missing-data reporting.

    Provider order:
    1. pytefas
    2. direct TEFAS endpoint wrapper
    3. minimal crawler wrapper
    4. user-provided manual snapshot, when configured

    Cache is only a performance/degraded-mode helper. Stale cache is reported and
    not promoted into recommendation-grade data.
    """

    def __init__(self, config: FundbotConfig, providers: Optional[List[BaseDataProvider]] = None):
        self.config = config
        self.cache = SQLiteCache(config.cache_path)
        self.providers = providers if providers is not None else self._default_providers()

    def fetch(self, codes: Optional[Iterable[str]] = None, force_refresh: bool = False) -> FetchResult:
        unavailable: List[str] = []
        verified: List[str] = []
        requested_codes = [c.strip().upper() for c in codes or [] if c.strip()]

        if requested_codes and not force_refresh:
            histories, metadata, cache_ages = self._load_fresh_cached_codes(requested_codes)
            if histories:
                verified.append(f"fresh cached TEFAS history for {len(histories)} requested funds")
                unavailable.extend(self._missing_cache_codes(requested_codes, histories))
                unavailable.extend(self._always_unavailable_context())
                return FetchResult(
                    metadata=metadata,
                    histories=histories,
                    verified_data=verified,
                    unavailable_data=unavailable,
                    source_attribution={c: "cache" for c in histories},
                    cache_ages=cache_ages,
                    confidence_multiplier=0.9,
                )

        orchestrator = ProviderOrchestrator(
            self.providers,
            conflict_tolerance=self.config.provider_conflict_tolerance,
            tefas_backoff_seconds=self.config.tefas_inter_provider_backoff_seconds,
        )
        result = orchestrator.fetch(codes=requested_codes or None, lookback_days=self.config.lookback_days)
        for code, hist in result.histories.items():
            source = result.source_attribution.get(code, "provider")
            self.cache.save_prices(hist, source=source)
        if not result.metadata.empty:
            self.cache.save_metadata(result.metadata)

        if not result.histories and requested_codes:
            histories, metadata, cache_ages = self._load_fresh_cached_codes(requested_codes)
            result.cache_ages.extend(cache_ages)
            if histories:
                result.histories = histories
                result.metadata = metadata
                result.source_attribution.update({c: "cache-after-live-failure" for c in histories})
                result.verified_data.append(f"degraded fresh cache fallback for {len(histories)} requested funds")
                result.confidence_multiplier = min(result.confidence_multiplier, 0.65)
            else:
                result.unavailable_data.extend(self._stale_or_missing_cache_notes(requested_codes))

        if result.metadata.empty or not result.histories:
            if not result.unavailable_data:
                result.unavailable_data.append("all providers returned empty TEFAS dataset")
        result.unavailable_data.extend(self._always_unavailable_context())
        return result

    def _default_providers(self) -> List[BaseDataProvider]:
        providers: List[BaseDataProvider] = [
            PytefasProvider(tefas_kinds=self.config.tefas_kinds),
            DirectTEFASProvider(),
            TEFASCrawlerProvider(),
        ]
        if self.config.manual_snapshot_path:
            providers.append(ManualSnapshotProvider(Path(self.config.manual_snapshot_path)))
        return providers

    def _load_fresh_cached_codes(self, codes: List[str]) -> tuple[Dict[str, pd.DataFrame], pd.DataFrame, List[CacheAge]]:
        histories: Dict[str, pd.DataFrame] = {}
        ages: List[CacheAge] = []
        for code in codes:
            age = self.cache.price_cache_age(code)
            if not age:
                continue
            is_stale = age["age_days"] > self.config.cache_stale_after_days
            ages.append(CacheAge(code=code, latest_date=age["latest_date"], age_days=age["age_days"], is_stale=is_stale, source=age["source"]))
            if is_stale:
                continue
            cached = self.cache.load_prices(code)
            if not cached.empty:
                histories[code] = cached
        # Prefer cached metadata when available — keyword-based detection
        # downstream needs real names/categories, not "cached" placeholders.
        cached_meta = self.cache.load_metadata(list(histories.keys()))
        meta_by_code = {row["code"]: row for _, row in cached_meta.iterrows()} if not cached_meta.empty else {}
        rows = []
        for c in histories:
            m = meta_by_code.get(c)
            if m is not None:
                rows.append({
                    "code": c,
                    "name": m.get("name") or c,
                    "category": m.get("category") or "cached",
                    "aum": None if pd.isna(m.get("aum")) else float(m.get("aum")),
                    "stock_ratio": None if pd.isna(m.get("stock_ratio")) else float(m.get("stock_ratio")),
                })
            else:
                rows.append({"code": c, "name": c, "category": "cached", "aum": None, "stock_ratio": None})
        metadata = pd.DataFrame(rows)
        return histories, metadata, ages

    def _stale_or_missing_cache_notes(self, codes: List[str]) -> List[str]:
        notes: List[str] = []
        for code in codes:
            age = self.cache.price_cache_age(code)
            if not age:
                notes.append(f"no cache for {code}")
            elif age["age_days"] > self.config.cache_stale_after_days:
                notes.append(
                    f"stale cache for {code}: latest {age['latest_date']} age {age['age_days']}d exceeds {self.config.cache_stale_after_days}d threshold"
                )
        return notes

    def _missing_cache_codes(self, requested: List[str], histories: Dict[str, pd.DataFrame]) -> List[str]:
        return [f"no fresh cache for {code}" for code in requested if code not in histories]

    def _always_unavailable_context(self) -> List[str]:
        return [
            "X/Twitter sentiment unavailable unless user supplies external research",
            "live market news unavailable unless user supplies verified excerpts",
            "broker/bank fund availability and order constraints not accessible from fundbot",
        ]
