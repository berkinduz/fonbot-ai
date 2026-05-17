import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_store import ResearchStore
from strategy_loader import load_weights


class ResearchStoreTests(unittest.TestCase):
    def test_record_then_load_recent_returns_note_with_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            store = ResearchStore(Path(td))
            path = store.record(
                topic="tech-fonlari-grok",
                source="grok",
                relevance="medium",
                body="AFT için tech sektörü pozitif görünüyor, kısa vadeli sentiment yukarı.",
                funds=["AFT", "AAL"],
                date="2026-05-15",
            )

            self.assertTrue(path.exists())
            notes = store.load_recent(days=60)

            self.assertEqual(len(notes), 1)
            note = notes[0]
            self.assertEqual(note.source, "grok")
            self.assertEqual(note.relevance, "medium")
            self.assertEqual(note.funds, ["AFT", "AAL"])
            self.assertIn("AFT", note.summary)

    def test_load_recent_filters_by_fund_codes(self):
        with tempfile.TemporaryDirectory() as td:
            store = ResearchStore(Path(td))
            store.record(topic="aft-note", source="grok", relevance="high", body="aft summary", funds=["AFT"], date="2026-05-10")
            store.record(topic="zzz-note", source="grok", relevance="low", body="zzz summary", funds=["ZZZ"], date="2026-05-12")

            aft_only = store.load_recent(days=60, fund_codes=["AFT"])

            self.assertEqual(len(aft_only), 1)
            self.assertIn("AFT", aft_only[0].funds)

    def test_old_notes_dropped_by_lookback_window(self):
        with tempfile.TemporaryDirectory() as td:
            store = ResearchStore(Path(td))
            store.record(topic="ancient", source="news", relevance="low", body="ancient", funds=None, date="2020-01-01")

            recent = store.load_recent(days=60)

            self.assertEqual(recent, [])


class StrategyLoaderTests(unittest.TestCase):
    def test_loader_returns_defaults_when_file_missing(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = load_weights(Path(td) / "missing.json")

            self.assertIn("scorer", cfg)
            self.assertIn("allocator", cfg)
            self.assertEqual(cfg["scorer"]["return_3m_weight"], 0.45)

    def test_loader_returns_defaults_on_corrupt_json(self):
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "weights.json"
            bad.write_text("{not valid json")

            cfg = load_weights(bad)

            self.assertEqual(cfg["scorer"]["return_3m_weight"], 0.45)

    def test_partial_override_keeps_other_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            partial = Path(td) / "weights.json"
            partial.write_text(json.dumps({"scorer": {"return_3m_weight": 0.60}}))

            cfg = load_weights(partial)

            self.assertEqual(cfg["scorer"]["return_3m_weight"], 0.60)
            self.assertEqual(cfg["scorer"]["return_6m_weight"], 0.25)
            self.assertEqual(cfg["allocator"]["opportunity_weight"], 0.70)


if __name__ == "__main__":
    unittest.main()
