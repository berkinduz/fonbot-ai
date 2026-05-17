import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from allocator import FundAllocator
from config import FundbotConfig
from portfolio_manager import PortfolioManager
from portfolio_store import PortfolioStore


class FundbotPortfolioStateTests(unittest.TestCase):
    def test_confirmed_transactions_update_state_and_append_history(self):
        with tempfile.TemporaryDirectory() as td:
            store = PortfolioStore(Path(td))

            pending = store.record_transaction(
                code="AFT",
                name="Ak Portföy Yeni Teknolojiler",
                action="BUY",
                amount=42000,
                trade_date="2026-05-20",
                confirmed=False,
                note="user said maybe bought",
            )
            self.assertEqual(store.load_state()["positions"], {})

            confirmed = store.record_transaction(
                code="AFT",
                name="Ak Portföy Yeni Teknolojiler",
                action="BUY",
                amount=42000,
                trade_date="2026-05-20",
                confirmed=True,
            )
            state = store.load_state()
            self.assertIn("AFT", state["positions"])
            self.assertEqual(state["positions"]["AFT"]["cost_amount"], 42000)
            self.assertEqual(state["positions"]["AFT"]["confirmed_transactions"], 1)
            lines = (Path(td) / "portfolio" / "transaction_history.jsonl").read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[0])["status"], "pending_user_confirmation")
            self.assertEqual(json.loads(lines[1])["status"], "confirmed")

    def test_sell_and_partial_sell_reduce_or_close_position_without_overwriting_history(self):
        with tempfile.TemporaryDirectory() as td:
            store = PortfolioStore(Path(td))
            store.record_transaction("AFT", "AFT Fund", "BUY", 40000, "2026-05-20", confirmed=True)
            store.record_transaction("AFT", "AFT Fund", "SELL", 10000, "2026-06-01", confirmed=True)
            state = store.load_state()
            self.assertEqual(state["positions"]["AFT"]["cost_amount"], 30000)

            store.record_transaction("AFT", "AFT Fund", "SELL", 30000, "2026-06-15", confirmed=True)
            state = store.load_state()
            self.assertNotIn("AFT", state["positions"])
            snapshots = list((Path(td) / "portfolio" / "snapshots").glob("*.json"))
            self.assertGreaterEqual(len(snapshots), 3)

    def test_portfolio_manager_separates_fresh_allocation_from_current_portfolio_action(self):
        fresh = FundAllocator(FundbotConfig()).allocate(
            opportunity_code="AFT",
            opportunity_name="Ak Portföy Yeni Teknolojiler",
            opportunity_score=84,
            money_market_code="AAL",
            money_market_name="Ata Para Piyasası",
            regime_score=76,
            risk_penalty=4,
        )
        portfolio_state = {
            "positions": {
                "OLD": {"code": "OLD", "name": "Old Fund", "cost_amount": 30000, "role": "main_opportunity"},
                "AAL": {"code": "AAL", "name": "Ata Para Piyasası", "cost_amount": 5000, "role": "defensive_money_market"},
            }
        }

        result = PortfolioManager().evaluate(fresh, portfolio_state, current_scores={"OLD": 42, "AFT": 88}, switch_advantage_threshold=8)

        self.assertEqual(result.fresh_allocation.aggressive_fund.code, "AFT")
        self.assertEqual(result.portfolio_action, "SWITCH")
        self.assertIn("OLD", " ".join(result.continuation_reasoning))
        self.assertIn("AFT", " ".join(result.continuation_reasoning))
        self.assertGreater(result.current_exposure["main_opportunity"], 0)

    def test_portfolio_manager_can_hold_existing_strong_candidate_to_avoid_unnecessary_turnover(self):
        fresh = FundAllocator(FundbotConfig()).allocate(
            opportunity_code="AFT",
            opportunity_name="Ak Portföy Yeni Teknolojiler",
            opportunity_score=82,
            money_market_code="AAL",
            money_market_name="Ata Para Piyasası",
            regime_score=74,
            risk_penalty=4,
        )
        portfolio_state = {
            "positions": {
                "AFT": {"code": "AFT", "name": "Ak Portföy Yeni Teknolojiler", "cost_amount": 30000, "role": "main_opportunity"},
                "AAL": {"code": "AAL", "name": "Ata Para Piyasası", "cost_amount": 10000, "role": "defensive_money_market"},
            }
        }

        result = PortfolioManager().evaluate(fresh, portfolio_state, current_scores={"AFT": 80}, switch_advantage_threshold=8)

        self.assertIn(result.portfolio_action, {"HOLD", "INCREASE"})
        self.assertIn("fresh allocation", result.question_a.lower())
        self.assertIn("current portfolio", result.question_b.lower())


if __name__ == "__main__":
    unittest.main()
