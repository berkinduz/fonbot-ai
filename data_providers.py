from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd


@dataclass(frozen=True)
class FetchScope:
    codes: Optional[List[str]] = None
    include_history: bool = True
    lookback_days: int = 230
    stage: str = "deep"


@dataclass(frozen=True)
class CacheAge:
    code: str
    latest_date: str
    age_days: int
    is_stale: bool
    source: str = "cache"


@dataclass
class ProviderHealth:
    name: str
    attempts: int = 0
    successes: int = 0
    timeouts: int = 0
    failures: int = 0
    total_latency_seconds: float = 0.0
    last_successful_fetch: Optional[str] = None
    stale_risk: str = "unknown"

    @property
    def success_rate(self) -> float:
        return self.successes / self.attempts if self.attempts else 0.0

    @property
    def timeout_rate(self) -> float:
        return self.timeouts / self.attempts if self.attempts else 0.0

    @property
    def average_latency(self) -> float:
        return self.total_latency_seconds / self.successes if self.successes else 0.0


@dataclass(frozen=True)
class ProviderStatus:
    name: str
    priority: int
    healthy: bool
    message: str


@dataclass
class ProviderResponse:
    provider: str
    metadata: pd.DataFrame
    histories: Dict[str, pd.DataFrame]
    fetched_at: str
    source_attribution: Dict[str, str] = field(default_factory=dict)
    verified_data: List[str] = field(default_factory=list)
    unavailable_data: List[str] = field(default_factory=list)
    cache_ages: List[CacheAge] = field(default_factory=list)
    latency_seconds: float = 0.0
    confidence_multiplier: float = 1.0


@dataclass
class DataFetchResult:
    metadata: pd.DataFrame
    histories: Dict[str, pd.DataFrame]
    verified_data: List[str]
    unavailable_data: List[str]
    source_attribution: Dict[str, str] = field(default_factory=dict)
    provider_health: Dict[str, ProviderHealth] = field(default_factory=dict)
    cache_ages: List[CacheAge] = field(default_factory=list)
    confidence_multiplier: float = 1.0


class BaseDataProvider:
    def __init__(self, name: str, priority: int, stale_after_hours: int = 24):
        self.name = name
        self.priority = priority
        self.stale_after_hours = stale_after_hours

    def status(self) -> ProviderStatus:
        return ProviderStatus(self.name, self.priority, True, "configured")

    def fetch(self, scope: FetchScope) -> ProviderResponse:  # pragma: no cover - interface
        raise NotImplementedError


class PytefasProvider(BaseDataProvider):
    def __init__(self, tefas_kinds=("YAT",), priority: int = 10):
        super().__init__("pytefas", priority)
        self.tefas_kinds = tefas_kinds

    def fetch(self, scope: FetchScope) -> ProviderResponse:
        from datetime import date, timedelta
        from pytefas import Crawler  # type: ignore

        crawler = Crawler(timeout=60, max_retry=3)
        start = date.today() - timedelta(days=scope.lookback_days)
        end = date.today()
        frames: List[pd.DataFrame] = []
        if scope.codes:
            for code in scope.codes:
                frames.append(crawler.fetch(start=start, end=end, kind="YAT", columns="info", fund_code=code))
        else:
            for kind in self.tefas_kinds:
                frames.append(crawler.fetch(start=start, end=end, kind=kind, columns="info"))
        raw = pd.concat([f for f in frames if f is not None and not f.empty], ignore_index=True) if frames else pd.DataFrame()
        metadata, histories = normalize_tefas_frame(raw)
        if not scope.include_history:
            histories = {}
        return ProviderResponse(
            provider=self.name,
            metadata=metadata,
            histories=histories,
            fetched_at=utc_now_iso(),
            source_attribution={c: self.name for c in set(metadata.get("code", [])) | set(histories)},
            verified_data=[f"pytefas TEFAS provider: {len(histories)} histories"],
        )


class DirectTEFASProvider(BaseDataProvider):
    """Direct wrapper for TEFAS official JSON endpoint used by the web app.

    This intentionally does not call pytefas; it posts to the public TEFAS JSON
    endpoint with the minimal info schema. If the endpoint rate-limits or changes,
    the orchestrator records the failure and falls through to lower-priority sources.
    """

    INFO_URL = "https://www.tefas.gov.tr/api/funds/fonGnlBlgSiraliGetir"
    HEADERS = {
        "Accept": "*/*",
        "Content-Type": "application/json",
        "Origin": "https://www.tefas.gov.tr",
        "Referer": "https://www.tefas.gov.tr/tr/fon-verileri",
        "User-Agent": "Mozilla/5.0 fundbot-direct-provider",
    }
    FIELD_MAP = {
        "fonKodu": "fund_code",
        "fonUnvan": "fund_name",
        "tarih": "date",
        "fiyat": "price",
        "portfoyBuyukluk": "portfolio_size",
    }

    def __init__(self, priority: int = 20, timeout: int = 30):
        super().__init__("direct-tefas", priority)
        self.timeout = timeout

    def fetch(self, scope: FetchScope) -> ProviderResponse:
        from datetime import date, timedelta
        import requests

        end = date.today()
        start = end - timedelta(days=scope.lookback_days)
        frames: List[pd.DataFrame] = []
        codes = scope.codes or [None]
        for code in codes:
            cur = start
            while cur <= end:
                chunk_end = min(cur + timedelta(days=27), end)
                body = {
                    "fonTipi": "YAT",
                    "fonKodu": code,
                    "aramaMetni": None,
                    "fonTurKod": None,
                    "fonGrubu": None,
                    "sfonTurKod": None,
                    "fonTurAciklama": None,
                    "kurucuKod": None,
                    "basTarih": cur.strftime("%Y%m%d"),
                    "bitTarih": chunk_end.strftime("%Y%m%d"),
                    "basSira": 1,
                    "bitSira": 100000,
                    "dil": "TR",
                    "sFonTurKod": "",
                    "fonKod": "",
                    "fonGrup": "",
                    "fonUnvanTip": "",
                }
                response = requests.post(self.INFO_URL, json=body, headers=self.HEADERS, timeout=self.timeout)
                response.raise_for_status()
                payload = response.json()
                err = payload.get("errorMessage")
                if err and "veri bulunamadı" not in str(err).lower() and "out of bounds" not in str(err).lower():
                    raise RuntimeError(f"TEFAS direct API error: {err}")
                rows = payload.get("resultList") or []
                if rows:
                    frames.append(pd.DataFrame([{target: row.get(source) for source, target in self.FIELD_MAP.items()} | {"kind": "YAT"} for row in rows]))
                cur = chunk_end + timedelta(days=1)
        raw = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        metadata, histories = normalize_tefas_frame(raw)
        if not scope.include_history:
            histories = {}
        return ProviderResponse(
            provider=self.name,
            metadata=metadata,
            histories=histories,
            fetched_at=utc_now_iso(),
            source_attribution={c: self.name for c in set(metadata.get("code", [])) | set(histories)},
            verified_data=[f"direct TEFAS JSON provider: {len(histories)} histories"],
        )


class TEFASCrawlerProvider(BaseDataProvider):
    def __init__(self, priority: int = 30):
        super().__init__("tefas-crawler", priority)

    def fetch(self, scope: FetchScope) -> ProviderResponse:
        raise RuntimeError("crawler fallback disabled until TEFAS web response schema is verified")


class ManualSnapshotProvider(BaseDataProvider):
    def __init__(self, snapshot_path: Path, priority: int = 40):
        super().__init__("manual-export", priority, stale_after_hours=24 * 30)
        self.snapshot_path = Path(snapshot_path)

    def fetch(self, scope: FetchScope) -> ProviderResponse:
        response = import_manual_snapshot(self.snapshot_path)
        if scope.codes:
            codes = set(scope.codes)
            response.metadata = response.metadata[response.metadata["code"].isin(codes)].copy()
            response.histories = {c: h for c, h in response.histories.items() if c in codes}
            response.source_attribution = {c: v for c, v in response.source_attribution.items() if c in codes}
        if not scope.include_history:
            response.histories = {}
        return response


class ProviderOrchestrator:
    def __init__(self, providers: Iterable[BaseDataProvider], conflict_tolerance: float = 0.01):
        self.providers = sorted(list(providers), key=lambda p: p.priority)
        self.conflict_tolerance = conflict_tolerance
        self.health: Dict[str, ProviderHealth] = {p.name: ProviderHealth(p.name) for p in self.providers}

    def fetch(
        self,
        codes: Optional[Iterable[str]] = None,
        shortlist_codes: Optional[Iterable[str]] = None,
        lookback_days: int = 230,
        cross_check: bool = False,
    ) -> DataFetchResult:
        requested = [c.strip().upper() for c in codes or [] if c.strip()]
        shortlist = [c.strip().upper() for c in shortlist_codes or [] if c.strip()]
        unavailable: List[str] = []
        verified: List[str] = []
        collected: List[ProviderResponse] = []

        if not requested and shortlist:
            scan = self._first_success(FetchScope(None, include_history=False, lookback_days=lookback_days, stage="scan"), unavailable)
            if scan:
                collected.append(scan)
            deep = self._first_success(FetchScope(shortlist, include_history=True, lookback_days=lookback_days, stage="deep"), unavailable)
            if deep:
                collected.append(deep)
        elif cross_check:
            for provider in self.providers:
                response = self._call_provider(provider, FetchScope(requested or None, True, lookback_days, "cross-check"), unavailable)
                if response and response.histories:
                    collected.append(response)
        else:
            deep = self._first_success(FetchScope(requested or None, include_history=True, lookback_days=lookback_days, stage="deep"), unavailable)
            if deep:
                collected.append(deep)

        if not collected:
            return DataFetchResult(empty_metadata(), {}, verified, unavailable, provider_health=self.health)

        result = self._merge(collected, unavailable)
        result.provider_health = self.health
        result.verified_data.extend(verified)
        return result

    def _first_success(self, scope: FetchScope, unavailable: List[str]) -> Optional[ProviderResponse]:
        for provider in self.providers:
            response = self._call_provider(provider, scope, unavailable)
            if response and (not response.metadata.empty or response.histories):
                return response
        return None

    def _call_provider(self, provider: BaseDataProvider, scope: FetchScope, unavailable: List[str]) -> Optional[ProviderResponse]:
        stats = self.health[provider.name]
        stats.attempts += 1
        start = time.perf_counter()
        try:
            response = provider.fetch(scope)
            latency = response.latency_seconds or (time.perf_counter() - start)
            stats.successes += 1
            stats.total_latency_seconds += latency
            stats.last_successful_fetch = response.fetched_at
            stats.stale_risk = "low" if response.confidence_multiplier >= 0.9 else "medium"
            response.latency_seconds = latency
            return response
        except TimeoutError as exc:
            stats.timeouts += 1
            stats.failures += 1
            unavailable.append(f"{provider.name} failed: TimeoutError: {exc}")
        except Exception as exc:
            stats.failures += 1
            unavailable.append(f"{provider.name} failed: {type(exc).__name__}: {exc}")
        return None

    def _merge(self, responses: List[ProviderResponse], unavailable: List[str]) -> DataFetchResult:
        histories: Dict[str, pd.DataFrame] = {}
        source: Dict[str, str] = {}
        metadata_frames: List[pd.DataFrame] = []
        verified: List[str] = []
        cache_ages: List[CacheAge] = []
        confidence = 1.0

        by_code: Dict[str, List[ProviderResponse]] = {}
        for response in responses:
            if not response.metadata.empty:
                metadata_frames.append(response.metadata)
            verified.extend(response.verified_data or [f"{response.provider} provider data"])
            cache_ages.extend(response.cache_ages)
            confidence = min(confidence, response.confidence_multiplier)
            for code in response.histories:
                by_code.setdefault(code, []).append(response)

        for code, providers in by_code.items():
            if len(providers) > 1 and self._has_conflict(code, providers):
                unavailable.append(f"provider conflict for {code}: latest prices differ beyond tolerance; history blocked")
                continue
            winner = providers[0]
            histories[code] = winner.histories[code]
            source[code] = winner.source_attribution.get(code, winner.provider)

        if metadata_frames and (histories or not by_code):
            metadata = pd.concat(metadata_frames, ignore_index=True).drop_duplicates(subset=["code"], keep="first")
        else:
            metadata = empty_metadata()
        if histories and metadata.empty:
            metadata = pd.DataFrame([{"code": c, "name": c, "category": "unknown", "aum": None, "stock_ratio": None} for c in histories])
        return DataFetchResult(metadata, histories, verified, unavailable, source, self.health, cache_ages, confidence)

    def _has_conflict(self, code: str, responses: List[ProviderResponse]) -> bool:
        latest_prices = []
        for response in responses:
            hist = response.histories[code].sort_values("date")
            if hist.empty:
                continue
            latest_prices.append(float(hist.iloc[-1]["price"]))
        if len(latest_prices) < 2:
            return False
        low, high = min(latest_prices), max(latest_prices)
        return low > 0 and (high - low) / low > self.conflict_tolerance


def import_manual_snapshot(path: Path) -> ProviderResponse:
    path = Path(path)
    if path.suffix.lower() in {".xlsx", ".xls"}:
        df = pd.read_excel(path)
    else:
        df = pd.read_csv(path)
    required = {"date", "code", "price"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"manual snapshot missing columns: {sorted(missing)}")
    metadata, histories = normalize_snapshot_frame(df)
    return ProviderResponse(
        provider="manual-export",
        metadata=metadata,
        histories=histories,
        fetched_at=utc_now_iso(),
        source_attribution={c: "manual-export" for c in histories},
        verified_data=[f"user-provided manual export snapshot: {len(histories)} funds"],
        confidence_multiplier=0.75,
    )


def normalize_tefas_frame(raw: pd.DataFrame) -> tuple[pd.DataFrame, Dict[str, pd.DataFrame]]:
    if raw.empty:
        return empty_metadata(), {}
    df = raw.rename(columns={"fund_code": "code", "fund_name": "name", "portfolio_size": "aum"}).copy()
    return normalize_snapshot_frame(df)


def normalize_snapshot_frame(df: pd.DataFrame) -> tuple[pd.DataFrame, Dict[str, pd.DataFrame]]:
    if df.empty:
        return empty_metadata(), {}
    working = df.copy()
    working["code"] = working["code"].astype(str).str.upper().str.strip()
    working["date"] = pd.to_datetime(working["date"])
    working["price"] = pd.to_numeric(working["price"], errors="coerce")
    if "name" not in working:
        working["name"] = working["code"]
    if "category" not in working:
        working["category"] = working.get("kind", "YAT")
    if "aum" not in working:
        working["aum"] = None
    histories: Dict[str, pd.DataFrame] = {}
    rows = []
    for code, group in working.dropna(subset=["price"]).groupby("code"):
        group = group.sort_values("date")
        histories[str(code)] = group[["date", "code", "price"]].copy()
        latest = group.iloc[-1]
        rows.append(
            {
                "code": str(code),
                "name": str(latest.get("name") or code),
                "category": str(latest.get("category") or latest.get("kind") or "YAT"),
                "aum": None if pd.isna(latest.get("aum")) else float(latest.get("aum")),
                "stock_ratio": None,
            }
        )
    return pd.DataFrame(rows), histories


def empty_metadata() -> pd.DataFrame:
    return pd.DataFrame(columns=["code", "name", "category", "aum", "stock_ratio"])


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
