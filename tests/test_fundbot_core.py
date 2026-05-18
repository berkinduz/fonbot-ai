import math
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from config import FundbotConfig
from analyzer import FundAnalyzer
from scorer import FundScorer
from allocator import FundAllocator
from reporter import DecisionReporter
from universe_builder import UniverseBuilder


def sample_prices(code: str, values):
    dates = pd.date_range("2025-01-31", periods=len(values), freq="ME")
    return pd.DataFrame({"date": dates, "code": code, "price": values})


class FundbotCoreTests(unittest.TestCase):
    def test_analyzer_computes_momentum_trend_volatility_and_drawdown(self):
        history = sample_prices("AAA", [10, 11, 12, 13, 15, 16, 18])

        metrics = FundAnalyzer().analyze_fund("AAA", "Aggressive A", "Equity", history)

        self.assertEqual(metrics.code, "AAA")
        self.assertGreater(metrics.return_3m, 0.30)
        self.assertGreater(metrics.return_6m, 0.70)
        self.assertTrue(metrics.absolute_momentum)
        self.assertTrue(metrics.trend_confirmed)
        self.assertGreaterEqual(metrics.volatility_3m, 0)
        self.assertLessEqual(metrics.max_drawdown, 0)
        self.assertEqual(metrics.data_quality, "ok")

    def test_scorer_prefers_strong_momentum_with_trend_and_penalizes_broken_funds(self):
        analyzer = FundAnalyzer()
        strong = analyzer.analyze_fund("AAA", "Aggressive A", "Equity", sample_prices("AAA", [10, 11, 12, 13, 15, 16, 18]))
        weak = analyzer.analyze_fund("BBB", "Aggressive B", "Equity", sample_prices("BBB", [18, 17, 16, 15, 14, 13, 12]))

        scored = FundScorer().score_opportunity_funds([strong, weak])

        self.assertEqual(scored[0].code, "AAA")
        self.assertGreater(scored[0].score, scored[1].score)
        self.assertGreater(scored[0].confidence, scored[1].confidence)

    def test_allocator_outputs_two_fund_decision_with_dynamic_ratio(self):
        decision = FundAllocator(FundbotConfig()).allocate(
            opportunity_code="AAA",
            opportunity_name="Aggressive A",
            opportunity_score=86,
            money_market_code="MMF",
            money_market_name="Money Market Fund",
            regime_score=72,
            risk_penalty=5,
        )

        self.assertEqual(decision.aggressive_fund.code, "AAA")
        self.assertEqual(decision.defensive_fund.code, "MMF")
        self.assertIn(decision.aggressive_ratio, {0.9, 0.75, 0.65, 0.5, 0.35})
        self.assertTrue(math.isclose(decision.aggressive_ratio + decision.defensive_ratio, 1.0))
        self.assertIn(decision.action, {"BUY", "HOLD", "SWITCH", "REDUCE", "INCREASE"})
        self.assertTrue(decision.data_integrity.verified_data)
        # broker availability is no longer a confidence-blocking gap; it lives
        # in estimated_data as an operational note only.
        self.assertIn("execution timing", " ".join(decision.data_integrity.estimated_data).lower())

    def test_universe_builder_filters_short_stale_and_anomalous_histories(self):
        metadata = pd.DataFrame(
            [
                {"code": "OK1", "name": "Good Fund", "category": "Equity", "aum": 100_000_000},
                {"code": "NEW", "name": "New Fund", "category": "Equity", "aum": 100_000_000},
                {"code": "BAD", "name": "Bad Spike", "category": "Equity", "aum": 100_000_000},
            ]
        )
        histories = {
            "OK1": sample_prices("OK1", [10, 10.5, 11, 11.3, 12, 12.5, 13, 13.5, 14]),
            "NEW": sample_prices("NEW", [10, 11]),
            "BAD": sample_prices("BAD", [10, 10.1, 10.2, 80, 10.3, 10.4, 10.5, 10.6, 10.7]),
        }

        universe = UniverseBuilder(FundbotConfig(min_history_months=6)).build(metadata, histories)

        self.assertEqual([fund.code for fund in universe], ["OK1"])

    def test_reporter_writes_append_only_jsonl_and_markdown_report(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            decision = FundAllocator(FundbotConfig()).allocate(
                opportunity_code="AAA",
                opportunity_name="Aggressive A",
                opportunity_score=88,
                money_market_code="MMF",
                money_market_name="Money Market Fund",
                regime_score=80,
                risk_penalty=4,
            )

            reporter = DecisionReporter(base_dir=tmp_path)
            paths = reporter.save(decision, candidates=[{"code": "AAA", "score": 88}], missing_data=["live news unavailable"])
            reporter.save(decision, candidates=[{"code": "AAA", "score": 88}], missing_data=["live news unavailable"])

            self.assertTrue(paths["report"].exists())
            self.assertTrue(paths["history"].exists())
            history_lines = paths["history"].read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(history_lines), 2)
            report_text = paths["report"].read_text(encoding="utf-8")
            self.assertIn("veri yok", report_text.lower())
            self.assertIn("execution timing", report_text.lower())


if __name__ == "__main__":
    unittest.main()
