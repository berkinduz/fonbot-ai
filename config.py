from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List


@dataclass(frozen=True)
class FundbotConfig:
    """Central parameters. Conservative defaults are avoided; risk is disciplined, not timid."""

    root_dir: Path = Path(__file__).resolve().parent
    cache_path: Path = Path(__file__).resolve().parent / "data" / "fundbot.sqlite"
    reports_dir: Path = Path(__file__).resolve().parent / "reports"
    history_path: Path = Path(__file__).resolve().parent / "reports" / "decisions.jsonl"
    min_history_months: int = 6
    lookback_days: int = 230
    tefas_kinds: tuple[str, ...] = ("YAT",)
    cache_stale_after_days: int = 7
    provider_conflict_tolerance: float = 0.01
    manual_snapshot_path: Path | None = None
    min_aum: float = 0.0
    anomaly_return_abs_limit: float = 2.5
    money_market_keywords: List[str] = field(default_factory=lambda: ["para piyasası", "money market", "likit", "kısa vadeli"])
    defensive_min_score: float = 50
    aggressive_ratios: tuple[float, ...] = (0.90, 0.75, 0.65, 0.50, 0.35)
    verified_quant_label: str = "TEFAS/library price history cached or fetched by local fundbot"
    tefas_inter_provider_backoff_seconds: float = 12.0
    external_context_path: Path | None = Path(__file__).resolve().parent / "context" / "current_external_context.json"
    external_context_max_age_days: int = 3
    external_context_auto_refresh: bool = True
