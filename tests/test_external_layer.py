import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from external_context import load_external_context, missing_context
from external_intelligence import ExternalIntelligenceAnalyzer
from external_scan import ExternalScanner
from official_macro import OfficialMacroScanner


# Minimal fake fetcher: returns deterministic payloads per URL substring.
def make_fake_fetcher(yahoo_payload: dict, rss_xml: str):
    def fake_fetch(url: str) -> str:
        if "query1.finance.yahoo.com" in url:
            return json.dumps(yahoo_payload)
        if "news.google.com/rss" in url:
            return rss_xml
        raise ValueError(f"unexpected url: {url}")
    return fake_fetch


YAHOO_BIST_DOWN = {
    "chart": {
        "result": [{
            "indicators": {
                "quote": [{"close": [100.0, 95.0, 92.0, 88.0]}]
            }
        }]
    }
}

RSS_CLEAN = """<?xml version="1.0" encoding="UTF-8"?>
<rss><channel>
  <item><title>TCMB politika faizini %42 olarak sabit tuttu</title><link>https://example.com/1</link><pubDate>Wed, 15 May 2026 10:00:00 GMT</pubDate></item>
  <item><title>TÜİK enflasyon yıllık %55 açıklandı</title><link>https://example.com/2</link><pubDate>Wed, 15 May 2026 10:00:00 GMT</pubDate></item>
</channel></rss>
"""

RSS_STRUCTURAL = """<?xml version="1.0" encoding="UTF-8"?>
<rss><channel>
  <item><title>AFT fonu tasfiye sürecine girdi</title><link>https://example.com/aft</link><pubDate>Wed, 15 May 2026 10:00:00 GMT</pubDate></item>
</channel></rss>
"""


class ExternalScanTests(unittest.TestCase):
    def test_scanner_writes_context_with_macro_rates_and_news_sections(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "ctx.json"
            scanner = ExternalScanner(fetch_text=make_fake_fetcher(YAHOO_BIST_DOWN, RSS_CLEAN))

            ctx = scanner.scan(codes=["AFT"], output_path=out)

            self.assertTrue(out.exists())
            self.assertEqual(ctx["schema"], "fundbot_external_context_v1")
            self.assertIn("macro_regime", ctx["sections"])
            self.assertIn("official_macro", ctx["sections"])
            self.assertIn("rates_inflation", ctx["sections"])
            self.assertIn("market_news", ctx["sections"])
            self.assertGreater(len(ctx["sections"]["macro_regime"]["items"]), 0)
            self.assertGreater(len(ctx["sections"]["rates_inflation"]["items"]), 0)

    def test_scanner_records_unknowns_when_source_fails(self):
        def bad_fetcher(url):
            raise RuntimeError("network down")
        scanner = ExternalScanner(fetch_text=bad_fetcher)

        ctx = scanner.scan(codes=["AFT"], output_path=None)

        self.assertGreater(len(ctx["sections"]["macro_regime"]["unknowns"]), 0)
        self.assertGreater(len(ctx["sections"]["market_news"]["unknowns"]), 0)


class OfficialMacroScannerTests(unittest.TestCase):
    def test_evds_without_key_is_optional_unknown_not_exception(self):
        def fake_fetch(url):
            raise AssertionError("EVDS should not be called without a key")

        result = OfficialMacroScanner(fetch_text=fake_fetch, evds_key="").scan()

        self.assertEqual(result["items"], [])
        self.assertTrue(any("TCMB_EVDS_API_KEY" in u for u in result["unknowns"]))

    def test_evds_parses_fx_and_policy_inflation_gap(self):
        def fake_fetch(url):
            if "TP.DK.USD.A" in url:
                return json.dumps({"items": [
                    {"Tarih": "01-04-2026", "TP_DK_USD_A": "32.0"},
                    {"Tarih": "01-05-2026", "TP_DK_USD_A": "34.0"},
                ]})
            if "TP.DK.EUR.A" in url:
                return json.dumps({"items": [{"Tarih": "01-05-2026", "TP_DK_EUR_A": "37.0"}]})
            if "TP.FE.OKTG01" in url:
                return json.dumps({"items": [{"Tarih": "01-05-2026", "TP_FE_OKTG01": "55.0"}]})
            if "TP.APIFON4" in url:
                return json.dumps({"items": [{"Tarih": "01-05-2026", "TP_APIFON4": "42.0"}]})
            if "bddk.org.tr" in url:
                return "Tarih: 30 Nisan 2026 Toplam Krediler 16.230.183 9.324.320 25.554.504"
            raise ValueError(url)

        result = OfficialMacroScanner(fetch_text=fake_fetch, evds_key="test-key").scan()

        usdtry = next(i for i in result["items"] if i.get("label") == "USDTRY")
        real_rate = next(i for i in result["items"] if i.get("label") == "Turkey policy/inflation (EVDS)")
        self.assertEqual(usdtry["change_1m_pct"], 6.25)
        self.assertEqual(real_rate["real_rate_gap"], -13.0)

    def test_bddk_weekly_parses_known_credit_metrics(self):
        html = """
        <html><body>Tarih: 30 Nisan 2026 Perşembe
        Toplam Krediler 16.230.183 9.324.320 25.554.504
        Tüketici Kredileri ve Bireysel Kredi Kartları 6.324.470 11.024 6.335.495
        </body></html>
        """

        result = OfficialMacroScanner(fetch_text=lambda url: html, evds_key="")._scan_bddk_weekly()

        metrics = result["items"][0]["metrics"]
        self.assertEqual(metrics["total_loans"]["total"], 25554504.0)
        self.assertEqual(metrics["consumer_loans_and_cards"]["tl"], 6324470.0)


class ExternalIntelligenceTests(unittest.TestCase):
    def test_bist_crash_increases_risk_and_lowers_regime(self):
        ctx = {
            "sections": {
                "macro_regime": {"items": [{"label": "BIST100", "change_1m_pct": -12.0}]}
            }
        }

        result = ExternalIntelligenceAnalyzer().analyze(ctx)

        self.assertGreater(result.risk_penalty_delta, 0)
        self.assertLess(result.regime_score_delta, 0)
        self.assertIsNotNone(result.confidence_cap)
        self.assertTrue(any("BIST100" in r for r in result.reasons))

    def test_structural_keyword_confirmed_by_two_sources_adds_avoid(self):
        # Cross-source confirmation: 2+ sources for the same fund → confirmed avoid
        ctx = {
            "sections": {
                "fund_specific": {"items": [
                    {"title": "AFT fonu tasfiye sürecine girdi", "query": "AFT fon", "source": "news", "code": "AFT"},
                    {"title": "AFT için soruşturma açıldı", "query": "AFT fon", "source": "news", "code": "AFT"},
                ]}
            }
        }

        result = ExternalIntelligenceAnalyzer().analyze(ctx)

        self.assertIn("AFT", result.avoid_funds)
        self.assertIsNotNone(result.confidence_cap)
        self.assertLessEqual(result.confidence_cap, 55)

    def test_no_evidence_means_no_modifiers(self):
        result = ExternalIntelligenceAnalyzer().analyze({"sections": {}})

        self.assertEqual(result.risk_penalty_delta, 0)
        self.assertEqual(result.regime_score_delta, 0)
        self.assertEqual(result.avoid_funds, [])

    def test_official_macro_replaces_same_label_proxy_for_fx_stress(self):
        ctx = {
            "sections": {
                "macro_regime": {"items": [{"label": "USDTRY", "change_1m_pct": 1.0}]},
                "official_macro": {"items": [{"source": "tcmb_evds", "label": "USDTRY", "change_1m_pct": 8.0}]},
            }
        }

        result = ExternalIntelligenceAnalyzer().analyze(ctx)

        self.assertGreaterEqual(result.risk_penalty_delta, 8)
        self.assertTrue(any("USDTRY rose 8.0%" in r for r in result.reasons))

    def test_official_policy_inflation_replaces_rss_derived_rates(self):
        ctx = {
            "sections": {
                "rates_inflation": {"items": [{"policy_rate": 42, "inflation_yoy": 40}]},
                "official_macro": {"items": [{"source": "tcmb_evds", "label": "Turkey policy/inflation (EVDS)", "policy_rate": 42, "inflation_yoy": 55}]},
            }
        }

        result = ExternalIntelligenceAnalyzer().analyze(ctx)

        self.assertGreaterEqual(result.risk_penalty_delta, 10)
        self.assertTrue(any("negative real-rate gap -13.0pp" in r for r in result.reasons))


class ExternalContextGateTests(unittest.TestCase):
    def test_missing_file_returns_missing_status_with_confidence_cap(self):
        result = load_external_context(Path("/nonexistent/path.json"))

        self.assertEqual(result.status, "missing")
        self.assertEqual(result.confidence_cap, 70)

    def test_fresh_full_context_returns_ready_status_without_cap(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ctx.json"
            payload = {
                "schema": "fundbot_external_context_v1",
                "date": date.today().isoformat(),
                "sections": {
                    "macro_regime": {"verified_facts": ["checked"], "items": [{"label": "USDTRY", "change_1m_pct": 1.0}]},
                    "rates_inflation": {"verified_facts": ["checked"], "items": [{"policy_rate": 42, "inflation_yoy": 40}]},
                    "market_news": {"verified_facts": ["clean"], "items": []},
                    "fund_specific": {"verified_facts": ["clean"], "items": []},
                },
                "risks": [],
                "sources": ["https://example.com"],
            }
            path.write_text(json.dumps(payload), encoding="utf-8")

            result = load_external_context(path)

            self.assertEqual(result.status, "ready")
            self.assertIsNone(result.confidence_cap)
            self.assertEqual(result.age_days, 0)

    def test_stale_context_marked_incomplete_and_capped(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ctx.json"
            payload = {
                "date": "2020-01-01",
                "sections": {
                    "macro_regime": {"verified_facts": ["x"], "items": []},
                    "rates_inflation": {"verified_facts": ["x"], "items": []},
                    "market_news": {"verified_facts": ["x"], "items": []},
                    "fund_specific": {"verified_facts": ["x"], "items": []},
                },
                "sources": ["https://example.com"],
            }
            path.write_text(json.dumps(payload), encoding="utf-8")

            result = load_external_context(path, max_age_days=3)

            self.assertEqual(result.status, "incomplete")
            self.assertEqual(result.confidence_cap, 70)
            self.assertTrue(any("stale" in u for u in result.unavailable_data))

    def test_structural_news_in_context_propagates_avoid_funds(self):
        # Single KAP source confirms avoid (no second source needed)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ctx.json"
            payload = {
                "date": date.today().isoformat(),
                "sections": {
                    "macro_regime": {"verified_facts": ["x"], "items": []},
                    "rates_inflation": {"verified_facts": ["x"], "items": []},
                    "market_news": {"verified_facts": ["x"], "items": []},
                    "fund_specific": {
                        "verified_facts": ["x"],
                        "items": [{
                            "title": "AFT yönetim değişikliği",
                            "query": "KAP IGS Fon",
                            "source": "kap",
                            "structural": True,
                            "code": "AFT",
                        }],
                    },
                },
                "sources": ["https://example.com"],
            }
            path.write_text(json.dumps(payload), encoding="utf-8")

            result = load_external_context(path)

            self.assertIn("AFT", result.avoid_funds)


if __name__ == "__main__":
    unittest.main()
