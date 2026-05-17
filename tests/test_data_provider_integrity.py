import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from config import FundbotConfig
from data_fetcher import TEFASDataFetcher
from data_provider_healthcheck import run_provider_smoke_checks
from data_providers import (
    BaseDataProvider,
    FetchScope,
    ProviderOrchestrator,
    ProviderResponse,
    ProviderStatus,
    import_manual_snapshot,
)


def prices(code: str, start="2026-01-01", periods=8, last_price=18.0):
    values = [10 + i for i in range(periods - 1)] + [last_price]
    return pd.DataFrame({"date": pd.date_range(start, periods=periods, freq="30D"), "code": code, "price": values})


def metadata(codes):
    return pd.DataFrame(
        [{"code": c, "name": f"Fund {c}", "category": "YAT", "aum": 100_000_000, "stock_ratio": None} for c in codes]
    )


class FakeProvider(BaseDataProvider):
    def __init__(self, name, priority, scan_codes=None, histories=None, fail=False, latency=0.01):
        super().__init__(name=name, priority=priority, stale_after_hours=24)
        self.scan_codes = scan_codes or []
        self.histories = histories or {}
        self.fail = fail
        self.latency = latency
        self.calls = []

    def fetch(self, scope: FetchScope) -> ProviderResponse:
        self.calls.append(scope)
        if self.fail:
            raise TimeoutError(f"{self.name} timeout")
        selected = list(scope.codes or self.scan_codes)
        histories = {c: self.histories[c] for c in selected if c in self.histories and scope.include_history}
        return ProviderResponse(
            provider=self.name,
            metadata=metadata(selected),
            histories=histories,
            fetched_at="2026-05-17T12:00:00+00:00",
            source_attribution={c: self.name for c in selected},
            latency_seconds=self.latency,
        )


class DataProviderIntegrityTests(unittest.TestCase):
    def test_fallback_uses_secondary_provider_and_records_source_attribution_when_primary_times_out(self):
        primary = FakeProvider("pytefas", 10, fail=True)
        secondary = FakeProvider("direct-tefas", 20, scan_codes=["AAA"], histories={"AAA": prices("AAA")})

        result = ProviderOrchestrator([primary, secondary], tefas_backoff_seconds=0).fetch(codes=["AAA"])

        self.assertIn("AAA", result.histories)
        self.assertEqual(result.source_attribution["AAA"], "direct-tefas")
        self.assertIn("pytefas failed: TimeoutError", " ".join(result.unavailable_data))
        self.assertGreater(result.provider_health["pytefas"].timeout_rate, 0)
        self.assertGreater(result.provider_health["direct-tefas"].success_rate, 0)

    def test_two_stage_fetch_scans_fast_universe_then_fetches_history_only_for_shortlist(self):
        provider = FakeProvider(
            "direct-tefas",
            20,
            scan_codes=["AAA", "BBB", "CCC"],
            histories={"AAA": prices("AAA"), "CCC": prices("CCC")},
        )

        result = ProviderOrchestrator([provider]).fetch(shortlist_codes=["AAA", "CCC"])

        self.assertEqual([scope.include_history for scope in provider.calls], [False, True])
        self.assertEqual(set(result.metadata["code"]), {"AAA", "BBB", "CCC"})
        self.assertEqual(set(result.histories), {"AAA", "CCC"})

    def test_stale_cache_is_reported_as_cache_fallback_and_does_not_allow_high_confidence_data(self):
        with tempfile.TemporaryDirectory() as td:
            config = FundbotConfig(cache_path=Path(td) / "fundbot.sqlite", cache_stale_after_days=1, tefas_inter_provider_backoff_seconds=0)
            fetcher = TEFASDataFetcher(config, providers=[FakeProvider("pytefas", 10, fail=True)])
            stale = prices("AAA", start="2025-01-01", periods=8)
            fetcher.cache.save_prices(stale, source="manual-old", fetched_at="2025-01-01T00:00:00+00:00")

            result = fetcher.fetch(codes=["AAA"], force_refresh=True)

            self.assertEqual(result.histories, {})
            self.assertIn("stale cache for AAA", " ".join(result.unavailable_data))
            self.assertTrue(any(age.code == "AAA" and age.is_stale for age in result.cache_ages))

    def test_conflicting_provider_latest_prices_block_history_instead_of_silently_choosing_one(self):
        primary = FakeProvider("pytefas", 10, scan_codes=["AAA"], histories={"AAA": prices("AAA", last_price=18.0)})
        secondary = FakeProvider("direct-tefas", 20, scan_codes=["AAA"], histories={"AAA": prices("AAA", last_price=22.0)})

        result = ProviderOrchestrator([primary, secondary], conflict_tolerance=0.05).fetch(codes=["AAA"], cross_check=True)

        self.assertNotIn("AAA", result.histories)
        self.assertIn("provider conflict for AAA", " ".join(result.unavailable_data))
        self.assertEqual(result.metadata.empty, True)

    def test_manual_snapshot_import_is_user_provided_fallback_with_source_attribution(self):
        with tempfile.TemporaryDirectory() as td:
            csv_path = Path(td) / "snapshot.csv"
            pd.DataFrame(
                [
                    {"date": "2026-01-01", "code": "AAA", "price": 10.0, "name": "Fund AAA", "category": "YAT"},
                    {"date": "2026-02-01", "code": "AAA", "price": 11.0, "name": "Fund AAA", "category": "YAT"},
                ]
            ).to_csv(csv_path, index=False)

            response = import_manual_snapshot(csv_path)

            self.assertEqual(response.provider, "manual-export")
            self.assertIn("AAA", response.histories)
            self.assertEqual(response.source_attribution["AAA"], "manual-export")
            self.assertIn("user-provided", response.verified_data[0])
    def test_provider_healthcheck_reports_short_long_universe_failure_and_stale_cache_checks(self):
        with tempfile.TemporaryDirectory() as td:
            config = FundbotConfig(cache_path=Path(td) / "fundbot.sqlite", cache_stale_after_days=1, tefas_inter_provider_backoff_seconds=0)
            ok_provider = FakeProvider("direct-tefas", 20, scan_codes=["AAA", "BBB"], histories={"AAA": prices("AAA"), "BBB": prices("BBB", periods=10)})
            failing_provider = FakeProvider("pytefas", 10, fail=True)
            checks = run_provider_smoke_checks(config, providers=[failing_provider, ok_provider], sample_code="AAA", universe_codes=["AAA", "BBB"])

            names = {check["name"] for check in checks}
            self.assertTrue({"single_fund_short_fetch", "single_fund_long_fetch", "small_universe_fetch", "provider_failure_simulation", "stale_cache_simulation"}.issubset(names))
            self.assertTrue(any(check["status"] == "pass" for check in checks if check["name"] == "single_fund_short_fetch"))
            self.assertTrue(any(check["status"] == "pass" for check in checks if check["name"] == "provider_failure_simulation"))
            self.assertTrue(any(check["status"] == "pass" for check in checks if check["name"] == "stale_cache_simulation"))


if __name__ == "__main__":
    unittest.main()
