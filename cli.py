from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import List

from allocator import FundAllocator
from analyzer import FundAnalyzer
from config import FundbotConfig
from data_fetcher import TEFASDataFetcher
from data_provider_healthcheck import run_provider_smoke_checks
from portfolio_manager import PortfolioManager
from portfolio_store import PortfolioStore
from regime_detector import RegimeDetector
from reporter import DecisionReporter
from scorer import FundScorer
from universe_builder import UniverseBuilder

try:
    from rich.console import Console
except Exception:  # pragma: no cover
    Console = None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fundbot: tactical TEFAS fund allocation engine")
    parser.add_argument("--amount", type=float, default=0.0, help="Monthly investable amount in TL")
    parser.add_argument("--codes", type=str, default="", help="Comma-separated fund codes to inspect from cache/provider")
    parser.add_argument("--deep-analysis", action="store_true", help="Keep more verbose candidate context in report")
    parser.add_argument("--force-refresh", action="store_true", help="Ignore cached histories where provider supports refresh")
    parser.add_argument("--backtest", action="store_true", help="Run simple backtest helper (requires prepared returns; placeholder safe)")
    parser.add_argument("--explain", action="store_true", help="Print strategy explanation")
    parser.add_argument("--healthcheck", action="store_true", help="Run data provider smoke checks and exit (no recommendation)")
    parser.add_argument("--healthcheck-code", type=str, default="AFT", help="Sample fund code for healthcheck")
    parser.add_argument("--record-transaction", action="store_true", help="Record a user-confirmed/manual portfolio transaction")
    parser.add_argument("--tx-code", type=str, default="", help="Transaction fund code")
    parser.add_argument("--tx-name", type=str, default="", help="Transaction fund name")
    parser.add_argument("--tx-action", type=str, default="BUY", help="BUY, SELL, INCREASE, REDUCE, CLOSE")
    parser.add_argument("--tx-amount", type=float, default=0.0, help="Transaction amount in TL")
    parser.add_argument("--tx-date", type=str, default="", help="Trade date YYYY-MM-DD")
    parser.add_argument("--tx-confirmed", action="store_true", help="Only confirmed transactions mutate portfolio_state.json")
    parser.add_argument("--tx-role", type=str, default="", help="main_opportunity or defensive_money_market if known")
    return parser


def explain() -> str:
    return "Momentum primary; 6M/trend confirmation and regime modify sizing; social/news data is tertiary and never hallucinated."


def run(argv: List[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    out = Console() if Console else None
    if args.explain:
        text = explain()
        out.print(text) if out else print(text)
        return 0
    if args.backtest:
        text = "Backtest module is available; provide prepared monthly returns before interpreting results. No fake backtest generated."
        out.print(text) if out else print(text)
        return 0
    if args.healthcheck:
        config = FundbotConfig()
        rows = run_provider_smoke_checks(config, sample_code=args.healthcheck_code.upper())
        for row in rows:
            line = f"[{row.get('status','?').upper():4}] {row.get('name')}"
            unavailable = row.get("unavailable_data") or []
            if unavailable:
                line += " | " + "; ".join(str(u) for u in unavailable[:3])
            histories = row.get("histories") or []
            if histories:
                line += f" | histories={histories}"
            out.print(line) if out else print(line)
        return 0 if all(r.get("status") == "pass" for r in rows) else 1
    if args.record_transaction:
        if not args.tx_code or not args.tx_date:
            text = "Transaction rejected: --tx-code and --tx-date are required. State is unchanged."
            out.print(text) if out else print(text)
            return 4
        tx = PortfolioStore().record_transaction(
            code=args.tx_code,
            name=args.tx_name or args.tx_code,
            action=args.tx_action,
            amount=args.tx_amount,
            trade_date=args.tx_date,
            confirmed=args.tx_confirmed,
            role=args.tx_role or None,
            note="CLI/user manual transaction record",
        )
        text = f"Recorded {tx['status']} transaction {tx['id']}. Portfolio state mutated: {args.tx_confirmed}."
        out.print(text) if out else print(text)
        return 0

    config = FundbotConfig()
    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    fetch = TEFASDataFetcher(config).fetch(codes=codes or None, force_refresh=args.force_refresh)
    if fetch.metadata.empty or not fetch.histories:
        text = "Veri yok: investable TEFAS universe could not be fetched from this environment. No recommendation generated."
        text += "\nEksik veri: " + "; ".join(fetch.unavailable_data)
        out.print(text) if out else print(text)
        return 2

    universe = UniverseBuilder(config).build(fetch.metadata, fetch.histories)
    analyzer = FundAnalyzer()
    metrics = [analyzer.analyze_fund(f.code, f.name, f.category, fetch.histories[f.code]) for f in universe]
    opportunity_metrics = [m for m, f in zip(metrics, universe) if not f.is_money_market]
    money_metrics = [m for m, f in zip(metrics, universe) if f.is_money_market]
    scorer = FundScorer()
    opportunities = scorer.score_opportunity_funds(opportunity_metrics)
    money = scorer.score_money_market_funds(money_metrics)
    if not opportunities or not money:
        text = "Veri yok: at least one aggressive candidate and one money market candidate are required."
        out.print(text) if out else print(text)
        return 3
    regime = RegimeDetector().detect()
    top = opportunities[0]
    mm = money[0]
    decision = FundAllocator(config).allocate(
        amount=args.amount,
        opportunity_code=top.code,
        opportunity_name=top.name,
        opportunity_score=top.score,
        money_market_code=mm.code,
        money_market_name=mm.name,
        regime_score=regime.score,
        risk_penalty=max(top.metrics.volatility_3m * 10 + abs(top.metrics.max_drawdown) * 20, 0),
    )
    candidate_rows = [{"code": c.code, "name": c.name, "score": c.score, "confidence": c.confidence} for c in opportunities[:3]]
    current_scores = {c.code: c.score for c in opportunities}
    current_scores.update({c.code: c.score for c in money})
    portfolio_state = PortfolioStore().load_state()
    portfolio_decision = PortfolioManager().evaluate(decision, portfolio_state, current_scores=current_scores)
    paths = DecisionReporter().save(
        decision,
        candidate_rows,
        fetch.unavailable_data + regime.unavailable_inputs,
        portfolio_decision=portfolio_decision,
        source_attribution=fetch.source_attribution,
    )
    text = f"{portfolio_decision.portfolio_action}: zero-based {decision.aggressive_fund.code} %{int(decision.aggressive_ratio*100)} + {decision.defensive_fund.code} %{int(decision.defensive_ratio*100)} | report: {paths['report']}"
    out.print(text) if out else print(text)
    return 0
