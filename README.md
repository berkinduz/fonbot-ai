# Fundbot

Local-first tactical TEFAS fund allocation engine.

Given an investable amount in TL, Fundbot inspects available TEFAS data and produces a disciplined monthly allocation: one aggressive opportunity fund plus one defensive money market fund. It is a decision-support tool, not an auto-trading bot — you execute all buys/sells manually.

## Philosophy

- Momentum is the primary signal.
- 3M momentum has the highest weight; 6M confirms persistence.
- Trend, volatility, drawdown, and macro regime modify conviction and sizing.
- Social/news context is tertiary only.
- Missing data is explicitly reported as missing. The system must never invent unavailable information.

## Portfolio Shape

Each decision contains exactly two legs:

1. Main opportunity fund: aggressive, highest conviction tactical opportunity.
2. Defensive money market fund: buffer / temporary parking layer.

Typical allocation bands:

- Strong: 90% aggressive / 10% money market
- Good: 75% / 25%
- Medium: 65% / 35%
- Weak/mixed: 50% / 50% or 35% / 65%

The engine does not default to 100% money market unless future rules explicitly add that emergency state.

## Installation

From this folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Optional live data providers:

```bash
pip install pytefas yfinance
```

If optional providers are unavailable, the system degrades honestly and uses only cached/supplied data.

## CLI Usage

```bash
python3 main.py --amount 35000
python3 main.py --amount 35000 --codes FUND1,FUND2,MMF1
python3 main.py --deep-analysis
python3 main.py --force-refresh
python3 main.py --backtest
python3 main.py --explain
```

## Architecture

- `main.py` — thin entrypoint
- `cli.py` — arguments and orchestration
- `config.py` — parameters and paths
- `data_fetcher.py` — provider orchestration, cache safety, source attribution, no fake live data
- `data_providers.py` — pytefas, direct TEFAS JSON, crawler placeholder, manual export providers
- `data_provider_healthcheck.py` — provider smoke checks without recommendation generation
- `cache.py` — SQLite storage for histories plus cache age/source metadata
- `universe_builder.py` — investable universe filtering
- `analyzer.py` — momentum/trend/volatility/drawdown metrics
- `scorer.py` — opportunity and money market scoring
- `regime_detector.py` — lightweight macro regime modifier
- `allocator.py` — two-leg allocation decision
- `reporter.py` — markdown report + append-only JSONL decision history
- `backtester.py` — simple monthly return evaluation tools
- `portfolio_store.py` — user-confirmed manual transaction ledger, derived `portfolio_state.json`, snapshots
- `portfolio_manager.py` — stateful continuity layer above the quant allocator
- `prompts/` — external research prompts when needed
- `tests/` — behavior tests

## Data Integrity Guarantees

Provider order:

1. Primary: `pytefas` (https://github.com/mirzazad/pytefas).
2. Secondary: direct TEFAS JSON endpoint wrapper.
3. Tertiary: crawler/fetcher fallback, disabled unless TEFAS web response schema is verified.
4. Quaternary: user-provided manual CSV/XLSX snapshot import.

The provider layer tracks source attribution, success/timeout/failure rates, average latency, stale risk, and last successful fetch. Cache is performance/degraded-mode support only: cache age is reported, stale cache is blocked from high-confidence recommendation use, and live/provider failure remains visible in the report.

The module explicitly separates:

- verified data
- unavailable data
- estimated/inferred data
- user-provided data

It never:

- hallucinates social/news sentiment
- pretends to access external news sources
- fabricates macro context
- invents unavailable TEFAS metrics
- treats user-provided narrative as the main decision source

## Reports and History

Reports are written under:

```text
reports/YYYY-MM-DD_fundbot-<id>.md
```

Decision history is append-only:

```text
reports/decisions.jsonl
```

Each decision stores reasons, used data, missing data, confidence, rerun triggers, and portfolio-continuity reasoning when portfolio state exists.

## Portfolio State

Fundbot is stateful only from user-confirmed manual transactions.

Files created at runtime:

```text
portfolio/transaction_history.jsonl
portfolio/portfolio_state.json
portfolio/snapshots/*.json
```

Rules:

- The user is the source of truth.
- Pending/unconfirmed transaction records do not mutate `portfolio_state.json`.
- There is no broker sync.
- The engine answers two separate questions:
  1. If starting from zero today, what allocation would be chosen?
  2. Given current portfolio state, what action makes sense?
- Possible portfolio actions: BUY, HOLD, INCREASE, REDUCE, SWITCH, PARTIAL SWITCH.
- Existing positions are not defended for emotional continuity; they are held only if current momentum/ranking/regime context still justifies it.

Manual transaction examples:

```bash
python3 main.py --record-transaction --tx-code AFT --tx-name "Ak Portföy Yeni Teknolojiler" --tx-action BUY --tx-amount 42000 --tx-date 2026-05-20 --tx-confirmed --tx-role main_opportunity
python3 main.py --record-transaction --tx-code AAL --tx-action SELL --tx-amount 10000 --tx-date 2026-06-01 --tx-confirmed --tx-role defensive_money_market
```

## Data Source: pytefas

The primary data path is the [pytefas](https://github.com/mirzazad/pytefas) library, a modern Python client for the official TEFAS (Türkiye Elektronik Fon Alım Satım Platformu) JSON API.

Why pytefas:

- Uses the new Next.js TEFAS site's official JSON endpoints directly (no HTML scraping).
- Auto-chunks long date ranges around the TEFAS ~1-month-per-request limit.
- Handles the TEFAS rate limit (6 requests/minute) in the background.
- Exposes both `info` (price, shares outstanding, investor count, portfolio size) and `breakdown` (50+ asset-allocation columns) views.
- Supports YAT / EMK / BYF / GYF / GSYF fund kinds.

Fundbot mainly uses the `info` view of YAT funds via:

```python
from pytefas import Crawler
crawler = Crawler(timeout=60, max_retry=3)
df = crawler.fetch(start, end, kind="YAT", columns="info", fund_code=code)
```

If `pytefas` is not installed, the orchestrator falls through to the built-in direct TEFAS JSON wrapper (`DirectTEFASProvider`), which posts to the same `fonGnlBlgSiraliGetir` endpoint that pytefas wraps. The direct wrapper is intentionally minimal and exists so the project keeps working when `pytefas` is unavailable; for normal use, prefer pytefas.

## Limitations

- TEFAS provider integration depends on the public API remaining stable; if endpoints change, providers must be updated.
- Money market fund yield selection is approximate unless fresh money market histories are available.
- Macro regime is a modifier, not a prediction engine.
- Backtesting is intentionally simple and not an institutional optimizer.
- Manual broker availability/liquidity checks remain the user's execution responsibility.
