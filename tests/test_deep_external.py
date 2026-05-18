import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from analyzer import FundAnalyzer
from article_fetcher import fetch_article
from breadth_analyzer import BreadthAnalyzer
from external_calendar import event_modifier, upcoming_events
from external_intelligence import ExternalIntelligenceAnalyzer
from external_scan import ExternalScanner
from fund_profiler import FundProfile
from kap_provider import KAPProvider
from universe_builder import UniverseBuilder
from config import FundbotConfig


def sample_prices(code, values, start="2025-09-30"):
    dates = pd.date_range(start, periods=len(values), freq="ME")
    return pd.DataFrame({"date": dates, "code": code, "price": values})


class FundProfilerIntegrationTests(unittest.TestCase):
    def test_universe_builder_uses_profile_money_market_ratio_over_keyword(self):
        # The fund name says "Tech" (no money market keyword) but the profile
        # marks it as money market via ratio. UniverseBuilder must trust the
        # profile, not the name.
        metadata = pd.DataFrame([{"code": "MMX", "name": "Money Market Tech Hybrid", "category": "YAT", "aum": 100_000_000}])
        histories = {"MMX": sample_prices("MMX", [10, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7])}
        profile = FundProfile(code="MMX", breakdown={"money_market": 0.92}, money_market_ratio=0.92, is_money_market=True, dominant_class="money_market", summary="money_market 92%")

        universe = UniverseBuilder(FundbotConfig()).build(metadata, histories, profiles={"MMX": profile})

        self.assertEqual(len(universe), 1)
        self.assertTrue(universe[0].is_money_market)

    def test_universe_builder_falls_back_to_keyword_when_no_profile(self):
        metadata = pd.DataFrame([
            {"code": "PPF", "name": "X Portföy Para Piyasası Fonu", "category": "YAT", "aum": 100_000_000},
            {"code": "TEC", "name": "Y Portföy Teknoloji Fonu", "category": "YAT", "aum": 100_000_000},
        ])
        histories = {
            "PPF": sample_prices("PPF", [10, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7]),
            "TEC": sample_prices("TEC", [10, 11, 12, 13, 14, 15, 16, 17]),
        }

        universe = UniverseBuilder(FundbotConfig()).build(metadata, histories, profiles={})

        roles = {r.code: r.is_money_market for r in universe}
        self.assertTrue(roles["PPF"])
        self.assertFalse(roles["TEC"])


class MacroMultiWindowTests(unittest.TestCase):
    def test_persistent_downtrend_amplifies_modifier(self):
        ctx = {
            "sections": {
                "macro_regime": {
                    "items": [{"label": "BIST100", "change_1m_pct": -10, "change_3m_pct": -15, "change_6m_pct": -22}],
                },
            }
        }

        result = ExternalIntelligenceAnalyzer().analyze(ctx)

        self.assertGreater(result.risk_penalty_delta, 14)  # base 14 amplified by persistence
        self.assertLess(result.regime_score_delta, -12)
        self.assertIsNotNone(result.confidence_cap)
        self.assertLessEqual(result.confidence_cap, 75)

    def test_vix_surge_triggers_risk_modifier(self):
        ctx = {"sections": {"macro_regime": {"items": [{"label": "VIX", "change_1m_pct": 45}]}}}
        result = ExternalIntelligenceAnalyzer().analyze(ctx)
        self.assertGreaterEqual(result.risk_penalty_delta, 10)
        self.assertLessEqual(result.regime_score_delta, -6)

    def test_tr_stress_pattern_bist_down_usdtry_up(self):
        ctx = {
            "sections": {
                "macro_regime": {
                    "items": [
                        {"label": "BIST100", "change_1m_pct": -7},
                        {"label": "USDTRY", "change_1m_pct": 6},
                    ],
                }
            }
        }
        result = ExternalIntelligenceAnalyzer().analyze(ctx)
        self.assertTrue(any("TR-specific stress" in r for r in result.reasons))


class CrossSourceConfirmationTests(unittest.TestCase):
    def test_single_news_source_does_not_avoid_only_warns(self):
        ctx = {
            "sections": {
                "fund_specific": {
                    "items": [{"title": "AFT tasfiye iddiası", "query": "AFT fon", "source": "news"}],
                }
            }
        }

        result = ExternalIntelligenceAnalyzer().analyze(ctx)

        self.assertNotIn("AFT", result.avoid_funds)
        self.assertTrue(any("candidate structural signal" in r.lower() for r in result.reasons))

    def test_kap_source_alone_confirms_avoid(self):
        ctx = {
            "sections": {
                "fund_specific": {
                    "items": [{
                        "title": "AFT yönetim değişikliği duyurusu",
                        "query": "KAP",
                        "source": "kap",
                        "structural": True,
                        "code": "AFT",
                    }],
                }
            }
        }

        result = ExternalIntelligenceAnalyzer().analyze(ctx)

        self.assertIn("AFT", result.avoid_funds)
        self.assertTrue(any("CONFIRMED" in r for r in result.reasons))

    def test_two_news_sources_confirm_avoid(self):
        ctx = {
            "sections": {
                "fund_specific": {
                    "items": [
                        {"title": "AFT fonu tasfiye süreci", "query": "AFT fon", "source": "news", "code": "AFT"},
                        {"title": "AFT için soruşturma", "query": "AFT", "source": "news", "code": "AFT"},
                    ],
                }
            }
        }

        result = ExternalIntelligenceAnalyzer().analyze(ctx)

        self.assertIn("AFT", result.avoid_funds)


class BreadthAnalyzerTests(unittest.TestCase):
    def _build_metric(self, code, r3, r6, trend_ok):
        analyzer = FundAnalyzer()
        # Build a synthetic price series that yields the desired returns. Easier
        # to just analyze a curated history.
        if r3 > 0 and r6 > 0:
            prices = [10, 10.5, 11, 12, 13, 14, 15, 16]
        elif r3 < 0 and r6 < 0:
            prices = [16, 15, 14, 13, 12, 11, 10.5, 10]
        else:
            prices = [10, 11, 12, 13, 12, 11, 10, 9.5]
        return analyzer.analyze_fund(code, code, "Equity", sample_prices(code, prices))

    def test_breadth_strong_when_most_funds_have_positive_momentum(self):
        metrics = [self._build_metric(f"F{i}", 0.1, 0.2, True) for i in range(10)]
        breadth = BreadthAnalyzer().analyze(metrics)
        self.assertGreaterEqual(breadth.score, 75)
        self.assertEqual(breadth.label, "strong")

    def test_breadth_weak_when_most_funds_have_negative_momentum(self):
        metrics = [self._build_metric(f"F{i}", -0.1, -0.2, False) for i in range(10)]
        breadth = BreadthAnalyzer().analyze(metrics)
        self.assertLess(breadth.score, 45)
        self.assertEqual(breadth.label, "weak")

    def test_breadth_returns_neutral_on_empty_universe(self):
        breadth = BreadthAnalyzer().analyze([])
        self.assertEqual(breadth.label, "unknown")
        self.assertEqual(breadth.score, 50.0)

    def test_breadth_verified_inputs_are_report_ready_without_legacy_macro_unavailable_noise(self):
        metrics = [self._build_metric(f"F{i}", 0.1, 0.2, True) for i in range(5)]

        breadth = BreadthAnalyzer().analyze(metrics)
        report_inputs = list(breadth.verified_inputs) + [
            f"regime baseline: breadth {breadth.label} ({breadth.score}/100, {int(breadth.positive_3m_pct*100)}% positive 3M)"
        ]

        joined = " ".join(report_inputs)
        self.assertIn("breadth from", joined)
        self.assertNotIn("interest-rate and inflation context not fetched automatically", joined)
        self.assertNotIn("BIST/USDTRY/gold/Nasdaq live context not available", joined)


class CalendarTests(unittest.TestCase):
    def test_event_modifier_returns_zero_when_no_events_within_window(self):
        # 2026-08-15: previous TUIK was Aug 4, next is Sep 3 (19 days). No
        # TCMB MPC in August. No FOMC in August. Within-7 window is empty.
        mod = event_modifier(within_days=7, today=date(2026, 8, 15))
        self.assertEqual(mod.risk_delta, 0.0)
        self.assertEqual(mod.regime_delta, 0.0)
        self.assertIsNone(mod.confidence_cap)

    def test_event_modifier_returns_risk_when_event_imminent(self):
        # TCMB MPC on 2026-01-23; today = 2026-01-22 → 1 day until
        mod = event_modifier(within_days=7, today=date(2026, 1, 22))
        self.assertGreater(mod.risk_delta, 0)
        self.assertLess(mod.regime_delta, 0)
        self.assertIsNotNone(mod.confidence_cap)
        # At least the TCMB event must be in the upcoming list
        kinds = {e.kind for e in mod.upcoming}
        self.assertIn("TCMB_MPC", kinds)

    def test_upcoming_events_sorted_by_proximity(self):
        events = upcoming_events(within_days=30, today=date(2026, 1, 1))
        if len(events) > 1:
            self.assertLessEqual(events[0].days_until, events[1].days_until)


class KAPProviderTests(unittest.TestCase):
    def _fake_fetch_factory(self, payload):
        def fetch(url):
            return json.dumps(payload)
        return fetch

    def test_kap_returns_material_disclosure_with_structural_flag(self):
        disclosures = [{
            "basic": {
                "subject": "Yatırım Fonu - Yönetim değişikliği",
                "title": "AFT yatırım fonu yönetim değişikliği duyurusu",
                "publisherName": "Ak Portföy",
                "stockCodes": ["AFT"],
                "publishDate": "01.05.2026 10:00:00",
                "disclosureIndex": 999,
            }
        }]
        provider = KAPProvider(fetch_text=self._fake_fetch_factory(disclosures))

        section = provider.fetch_recent(["AFT"], days=30)

        self.assertGreater(len(section["items"]), 0)
        item = section["items"][0]
        self.assertEqual(item["source"], "kap")
        self.assertTrue(item.get("structural"))
        self.assertEqual(item.get("code"), "AFT")

    def test_kap_filters_out_non_fund_disclosures(self):
        disclosures = [{
            "basic": {
                "subject": "Bağımsız denetim raporu",
                "title": "Holding bağımsız denetçi seçimi",
                "publisherName": "ACME Holding",
                "publishDate": "01.05.2026 10:00:00",
                "disclosureIndex": 1,
            }
        }]
        provider = KAPProvider(fetch_text=self._fake_fetch_factory(disclosures))

        section = provider.fetch_recent(["AFT"], days=30)

        self.assertEqual(section["items"], [])

    def test_kap_failure_returns_unknowns_not_raise(self):
        def bad_fetch(url):
            raise RuntimeError("network")
        provider = KAPProvider(fetch_text=bad_fetch)

        section = provider.fetch_recent(["AFT"], days=30)

        self.assertEqual(section["items"], [])
        self.assertTrue(any("failed" in u for u in section["unknowns"]))


class AnomalyDetectionTests(unittest.TestCase):
    def test_analyzer_flags_single_day_jump_as_anomaly(self):
        history = sample_prices("ANO", [10, 10.1, 10.2, 12.5, 12.6, 12.7, 12.8, 12.9])  # >20% jump

        metrics = FundAnalyzer().analyze_fund("ANO", "Anomaly Fund", "Equity", history)

        self.assertTrue(any("anomaly" in n.lower() for n in metrics.notes))


class ArticleFetcherTests(unittest.TestCase):
    def test_invalid_url_returns_error(self):
        result = fetch_article("not a url")
        self.assertEqual(result.error, "invalid url")
        self.assertIsNone(result.text)


if __name__ == "__main__":
    unittest.main()
