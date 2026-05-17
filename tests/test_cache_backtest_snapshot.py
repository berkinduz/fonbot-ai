import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from allocator import FundAllocator
from backtester import SimpleBacktester
from cache import SQLiteCache
from config import FundbotConfig
from portfolio_manager import PortfolioManager
from portfolio_store import PortfolioStore
from utils.jsonl import append_jsonl


def _prices_df(code, start, n=40, slope=0.005):
    dates = pd.date_range(start, periods=n, freq="D")
    base = 10.0
    return pd.DataFrame({"code": code, "date": dates, "price": [base * (1 + slope) ** i for i in range(n)]})


class CacheMetadataTests(unittest.TestCase):
    def test_save_and_load_metadata_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            cache = SQLiteCache(Path(td) / "fundbot.sqlite")
            df = pd.DataFrame([
                {"code": "AFT", "name": "Ak Portföy Yeni Teknolojiler", "category": "YAT", "aum": 2.5e10, "stock_ratio": None},
                {"code": "PPF", "name": "X Para Piyasası Fonu", "category": "YAT", "aum": 1e9, "stock_ratio": None},
            ])

            cache.save_metadata(df)
            loaded = cache.load_metadata(["AFT", "PPF"])

            self.assertEqual(len(loaded), 2)
            names = dict(zip(loaded["code"], loaded["name"]))
            self.assertEqual(names["AFT"], "Ak Portföy Yeni Teknolojiler")
            self.assertIn("Para Piyasası", names["PPF"])

    def test_load_metadata_returns_empty_when_no_codes(self):
        with tempfile.TemporaryDirectory() as td:
            cache = SQLiteCache(Path(td) / "fundbot.sqlite")
            result = cache.load_metadata([])
            self.assertTrue(result.empty)

    def test_data_fetcher_uses_cached_metadata_for_money_market_detection(self):
        # Simulate the cache-after-failure path: provider returns nothing,
        # but a previous run cached prices and metadata for a money market fund.
        # UniverseBuilder must see "para piyasası" in the cached name even
        # though the live provider failed.
        from data_fetcher import TEFASDataFetcher
        from data_providers import BaseDataProvider, FetchScope, ProviderResponse

        class DeadProvider(BaseDataProvider):
            def __init__(self):
                super().__init__("dead", 10)
            def fetch(self, scope):
                raise TimeoutError("provider down")

        with tempfile.TemporaryDirectory() as td:
            cache_path = Path(td) / "fundbot.sqlite"
            config = FundbotConfig(cache_path=cache_path, cache_stale_after_days=30, tefas_inter_provider_backoff_seconds=0)
            cache = SQLiteCache(cache_path)
            prices = _prices_df("PPF", datetime.utcnow().date() - timedelta(days=20), n=20)
            cache.save_prices(prices, source="historical")
            cache.save_metadata(pd.DataFrame([{"code": "PPF", "name": "X Para Piyasası Fonu", "category": "YAT", "aum": 1e9, "stock_ratio": None}]))

            fetcher = TEFASDataFetcher(config, providers=[DeadProvider()])
            result = fetcher.fetch(codes=["PPF"], force_refresh=True)

            self.assertIn("PPF", result.histories)
            meta = result.metadata
            self.assertEqual(meta.iloc[0]["name"], "X Para Piyasası Fonu")


class BacktesterTests(unittest.TestCase):
    def _seed_history_and_cache(self, tmp_path: Path) -> FundbotConfig:
        config = FundbotConfig(
            cache_path=tmp_path / "fundbot.sqlite",
            history_path=tmp_path / "decisions.jsonl",
            tefas_inter_provider_backoff_seconds=0,
        )
        cache = SQLiteCache(config.cache_path)
        # Aggressive fund: rises strongly
        cache.save_prices(_prices_df("AGG", datetime(2026, 1, 1).date(), n=45, slope=0.01))
        # Money market: rises slowly
        cache.save_prices(_prices_df("MMF", datetime(2026, 1, 1).date(), n=45, slope=0.002))
        # Build a fresh allocation decision and append it to decisions.jsonl
        decision = FundAllocator(config).allocate(
            opportunity_code="AGG",
            opportunity_name="Aggressive",
            opportunity_score=85,
            money_market_code="MMF",
            money_market_name="Money Market",
            regime_score=70,
            risk_penalty=5,
        )
        record = {
            "id": decision.decision_id,
            "dt": "2026-01-05T00:00:00+00:00",
            "type": "fundbot_decision",
            "decision": {
                **decision.to_dict(),
                "created_at": "2026-01-05T00:00:00+00:00",
            },
            "candidates": [{"code": "AGG", "score": 85}, {"code": "MMF", "score": 50}],
        }
        append_jsonl(config.history_path, record)
        return config

    def test_backtester_evaluates_decision_and_outperforms_money_market(self):
        with tempfile.TemporaryDirectory() as td:
            config = self._seed_history_and_cache(Path(td))

            summary = SimpleBacktester(config, evaluation_window_days=30).run()

            self.assertEqual(summary.decisions_evaluated, 1)
            self.assertGreater(summary.mean_portfolio_return_pct, 0)
            # Aggressive fund returns much more than MM; outperformance should be positive
            self.assertGreater(summary.mean_outperformance_vs_money_market_pct, 0)
            self.assertEqual(summary.hit_rate_vs_money_market, 1.0)

    def test_backtester_handles_empty_history(self):
        with tempfile.TemporaryDirectory() as td:
            config = FundbotConfig(history_path=Path(td) / "nonexistent.jsonl")
            summary = SimpleBacktester(config).run()
            self.assertEqual(summary.decisions_evaluated, 0)
            self.assertTrue(any("not found" in r for r in summary.skip_reasons))

    def test_backtester_skips_decisions_with_no_cached_prices(self):
        with tempfile.TemporaryDirectory() as td:
            config = FundbotConfig(
                cache_path=Path(td) / "fundbot.sqlite",
                history_path=Path(td) / "decisions.jsonl",
            )
            # Write a decision but never seed prices
            append_jsonl(config.history_path, {
                "id": "x",
                "type": "fundbot_decision",
                "decision": {
                    "decision_id": "x",
                    "created_at": "2026-01-05T00:00:00+00:00",
                    "aggressive_fund": {"code": "NONE", "name": "x", "ratio": 0.75, "role": "main_opportunity"},
                    "defensive_fund": {"code": "MISS", "name": "y", "ratio": 0.25, "role": "defensive_money_market"},
                    "aggressive_ratio": 0.75,
                    "defensive_ratio": 0.25,
                },
                "candidates": [],
            })

            summary = SimpleBacktester(config).run()

            self.assertEqual(summary.decisions_evaluated, 0)
            self.assertEqual(summary.decisions_skipped, 1)
            self.assertTrue(any("insufficient cached prices" in r for r in summary.skip_reasons))


class SnapshotComparisonTests(unittest.TestCase):
    def _fresh_allocation(self):
        return FundAllocator(FundbotConfig()).allocate(
            opportunity_code="AGG",
            opportunity_name="Aggressive",
            opportunity_score=80,
            money_market_code="MMF",
            money_market_name="Money Market",
            regime_score=70,
            risk_penalty=5,
        )

    def test_first_run_reports_no_snapshots_yet(self):
        with tempfile.TemporaryDirectory() as td:
            fresh = self._fresh_allocation()
            result = PortfolioManager().evaluate(
                fresh,
                portfolio_state={},
                current_scores={"AGG": 80},
                snapshots_dir=Path(td),  # empty dir
            )
            self.assertIn(result.previous_month_change["status"], {"no_snapshots_yet", "first_snapshot_only"})

    def test_diff_after_buy_records_added_position(self):
        with tempfile.TemporaryDirectory() as td:
            store = PortfolioStore(Path(td))
            # First snapshot from empty state (record dummy that creates state)
            store.record_transaction("AGG", "Aggressive", "BUY", 10000, "2026-01-05", confirmed=True, role="main_opportunity")
            # Second snapshot: increase
            store.record_transaction("AGG", "Aggressive", "INCREASE", 5000, "2026-01-15", confirmed=True, role="main_opportunity")
            state = store.load_state()

            fresh = self._fresh_allocation()
            result = PortfolioManager().evaluate(
                fresh,
                portfolio_state=state,
                current_scores={"AGG": 80},
                snapshots_dir=store.snapshots_dir,
            )

            change = result.previous_month_change
            self.assertEqual(change["status"], "compared")
            # Between snapshot[-2] (after first BUY, cost=10k) and current
            # (after INCREASE, cost=15k) the delta for AGG should be +5000.
            agg_changes = [c for c in change["cost_amount_changes"] if c["code"] == "AGG"]
            self.assertEqual(len(agg_changes), 1)
            self.assertAlmostEqual(agg_changes[0]["delta"], 5000.0, places=2)


if __name__ == "__main__":
    unittest.main()
