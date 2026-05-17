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
from research_store import ResearchStore
from scorer import FundScorer
from universe_builder import UniverseBuilder
from utils.jsonl import read_jsonl

try:
    from rich.console import Console
except Exception:  # pragma: no cover
    Console = None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fundbot: tactical TEFAS fund allocation engine, designed to be operated by an AI agent (Claude Code, Codex, Gemini CLI, etc.)")
    parser.add_argument("--codes", type=str, default="", help="Comma-separated fund codes to restrict the universe (debug/sanity-check). Default: full TEFAS YAT universe.")
    parser.add_argument("--deep-analysis", action="store_true", help="Keep more verbose candidate context in report")
    parser.add_argument("--force-refresh", action="store_true", help="Ignore cached histories where provider supports refresh")
    parser.add_argument("--backtest", action="store_true", help="Run simple backtest helper (requires prepared returns; placeholder safe)")
    parser.add_argument("--explain", action="store_true", help="Print strategy explanation")
    parser.add_argument("--status", action="store_true", help="Print engine state for AI operators: cache age, last decision, pending research, last strategy change. Use this at the start of every session.")
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
    parser.add_argument("--record-research", action="store_true", help="Ingest a user-supplied external research note (Grok answer, X thread, news excerpt) into research/. Context only; never overrides quant scoring.")
    parser.add_argument("--research-topic", type=str, default="", help="Short topic slug, e.g. 'tech-fonlari-grok-ozeti'")
    parser.add_argument("--research-source", type=str, default="user", help="grok | x | news | gemini | user")
    parser.add_argument("--research-relevance", type=str, default="medium", help="high | medium | low")
    parser.add_argument("--research-funds", type=str, default="", help="Comma-separated fund codes this note refers to (optional)")
    parser.add_argument("--research-body-file", type=str, default="", help="Path to file with the note body. If omitted, body is read from stdin.")
    return parser


def explain() -> str:
    return "Momentum primary; 6M/trend confirmation and regime modify sizing; social/news data is tertiary, surfaced from research/ as context only, and never overrides quant scoring."


def _print_status(out) -> int:
    config = FundbotConfig()
    lines: List[str] = []

    import sqlite3
    cache_summary = {"funds": 0, "newest": None, "oldest": None}
    try:
        with sqlite3.connect(config.cache_path) as con:
            row = con.execute("SELECT COUNT(DISTINCT code), MAX(date), MIN(date) FROM fund_prices").fetchone()
            cache_summary = {"funds": row[0] or 0, "newest": row[1], "oldest": row[2]}
    except Exception as exc:
        cache_summary["error"] = str(exc)
    lines.append(f"cache: {cache_summary['funds']} funds; newest_price_date={cache_summary.get('newest')}; oldest={cache_summary.get('oldest')}")

    # Last decision
    decisions = read_jsonl(config.history_path) if config.history_path.exists() else []
    if decisions:
        last = decisions[-1]
        dec = last.get("decision", {})
        lines.append(f"last_decision: {dec.get('decision_id')} | {dec.get('action')} | {dec.get('aggressive_fund',{}).get('code')} %{int(dec.get('aggressive_ratio',0)*100)} + {dec.get('defensive_fund',{}).get('code')} %{int(dec.get('defensive_ratio',0)*100)} | {dec.get('created_at')}")
    else:
        lines.append("last_decision: none yet")

    # Pending research
    research = ResearchStore().load_recent(days=60)
    if research:
        lines.append(f"research_notes_last_60d: {len(research)}")
        for note in research[-5:]:
            lines.append(f"  - {note.to_brief()}")
    else:
        lines.append("research_notes_last_60d: 0")

    # Strategy
    strategy_history = Path(__file__).resolve().parent / "strategy" / "history.jsonl"
    history = read_jsonl(strategy_history) if strategy_history.exists() else []
    if history:
        last_strategy = history[-1]
        lines.append(f"last_strategy_change: v{last_strategy.get('version')} | {last_strategy.get('change_type')} | {last_strategy.get('dt')} | approved_by={last_strategy.get('approved_by')}")
    else:
        lines.append("last_strategy_change: none")

    # Portfolio
    state = PortfolioStore().load_state()
    positions = state.get("positions", {})
    if positions:
        lines.append(f"portfolio_positions: {len(positions)} | total_cost={state.get('total_cost_amount', 0)}")
        for code, pos in positions.items():
            lines.append(f"  - {code} role={pos.get('role')} cost={pos.get('cost_amount')}")
    else:
        lines.append("portfolio_positions: 0 (no confirmed transactions)")

    for line in lines:
        out.print(line) if out else print(line)
    return 0


def _record_research(args, out) -> int:
    if not args.research_topic:
        text = "Research rejected: --research-topic is required."
        out.print(text) if out else print(text)
        return 4
    if args.research_body_file:
        body = Path(args.research_body_file).read_text(encoding="utf-8")
    else:
        import sys
        body = sys.stdin.read()
    if not body.strip():
        text = "Research rejected: body is empty (provide --research-body-file or pipe via stdin)."
        out.print(text) if out else print(text)
        return 4
    funds = [f.strip() for f in args.research_funds.split(",") if f.strip()]
    path = ResearchStore().record(
        topic=args.research_topic,
        source=args.research_source,
        relevance=args.research_relevance,
        body=body,
        funds=funds or None,
    )
    text = f"Recorded research note at {path}"
    out.print(text) if out else print(text)
    return 0


def run(argv: List[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    out = Console() if Console else None
    if args.explain:
        text = explain()
        out.print(text) if out else print(text)
        return 0
    if args.status:
        return _print_status(out)
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
    if args.record_research:
        return _record_research(args, out)
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
    research_notes = ResearchStore().load_recent(days=60, fund_codes=[top.code, mm.code])
    paths = DecisionReporter().save(
        decision,
        candidate_rows,
        fetch.unavailable_data + regime.unavailable_inputs,
        portfolio_decision=portfolio_decision,
        source_attribution=fetch.source_attribution,
        research_notes=research_notes,
    )
    text = f"{portfolio_decision.portfolio_action}: zero-based {decision.aggressive_fund.code} %{int(decision.aggressive_ratio*100)} + {decision.defensive_fund.code} %{int(decision.defensive_ratio*100)} | report: {paths['report']}"
    if research_notes:
        text += f" | {len(research_notes)} research note(s) attached as context"
    out.print(text) if out else print(text)
    return 0
