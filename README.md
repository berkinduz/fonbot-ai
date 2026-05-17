# fonbot-ai

Local-first tactical TEFAS fund allocation engine. Written for one specific job: every month, decide where this month's TL goes.

You bring an amount. Fonbot reads TEFAS data, scores the investable universe on momentum, trend, volatility and regime, and returns one aggressive opportunity fund plus one defensive money market fund with a clear allocation ratio. You execute the trades manually through your bank / broker.

It is a decision-support tool, not an auto-trader. It does not connect to your account, place orders, or move money. It also does not pretend to know things it cannot know — no fabricated news, no invented sentiment, no fake "live data". If TEFAS is unreachable, it says so.

## Why this exists

Most "fund recommendation" scripts pick the highest-trailing-return fund and call it a day. That is not a strategy; that is a backtest of the last three months.

Fonbot is built around a small, defensible thesis:

- Momentum is the primary signal. 3M momentum carries the most weight; 6M confirms persistence.
- Trend, volatility, drawdown and macro regime are **modifiers** — they change conviction and sizing, not direction.
- Social / news / sentiment is tertiary. The user can supply external context, but it never overrides quantitative scoring.
- Missing data is reported as missing. The system never invents what it cannot see.

The output is intentionally narrow: one main fund, one money market buffer, one ratio, one action. Not a leaderboard. Not a thesis essay.

## What you get per run

```
SWITCH: zero-based AFT %75 + AFA %25 | report: reports/2026-05-17_fundbot-ab12cd34ef56.md
```

The markdown report behind that one-liner includes:

- The chosen aggressive fund + money market fund + ratio + TL amounts
- Top 3 ranked candidates with scores
- Why this allocation (composite conviction breakdown)
- Portfolio continuity reasoning if you have recorded transactions (BUY / HOLD / INCREASE / REDUCE / SWITCH / PARTIAL SWITCH)
- Data integrity block: which provider served each fund, what was verified, what was unavailable
- Rerun triggers — concrete conditions that should make you re-evaluate

Every decision is also appended to `reports/decisions.jsonl` (append-only).

## Portfolio shape

Each decision contains exactly two legs:

1. **Main opportunity fund** — aggressive, highest-conviction tactical pick.
2. **Defensive money market fund** — buffer / temporary parking layer.

Allocation bands by composite conviction:

| Conviction | Aggressive | Defensive |
|---|---|---|
| Strong (≥80) | 90% | 10% |
| Good (≥70) | 75% | 25% |
| Medium (≥58) | 65% | 35% |
| Weak (≥45) | 50% | 50% |
| Mixed | 35% | 65% |

The engine does not collapse to 100% money market. If everything looks bad, it reduces — it does not hide.

## Install

Requires Python 3.10+ (3.9 works thanks to `from __future__ import annotations`, but is not the target).

```bash
git clone https://github.com/berkinduz/fonbot-ai.git
cd fonbot-ai
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install pytefas yfinance  # optional but strongly recommended; pytefas is the primary TEFAS provider
```

## Usage

```bash
# Monthly recommendation
python3 main.py --amount 35000

# Restrict the universe to specific codes (must include at least one money market fund)
python3 main.py --amount 35000 --codes AFT,AFA

# Skip cache and force a fresh fetch
python3 main.py --amount 35000 --force-refresh

# Verify the data layer without producing a recommendation
python3 main.py --healthcheck
python3 main.py --healthcheck --healthcheck-code AFT

# Print the strategy explanation
python3 main.py --explain
```

### Recording a transaction

The engine is stateful only from transactions you explicitly confirm. There is no broker sync.

```bash
python3 main.py --record-transaction \
  --tx-code AFT --tx-name "Ak Portföy Yeni Teknolojiler" \
  --tx-action BUY --tx-amount 42000 --tx-date 2026-05-20 \
  --tx-confirmed --tx-role main_opportunity
```

Unconfirmed records are appended to history but do not mutate `portfolio/portfolio_state.json`.

## Data integrity

Provider order:

1. **pytefas** — primary, uses the official TEFAS JSON endpoints; rate-limit aware.
2. **Direct TEFAS JSON wrapper** — fallback with its own 429 / empty-body / decode handling and jittered backoff.
3. **Crawler placeholder** — disabled until the TEFAS web schema is re-verified.
4. **Manual CSV / XLSX snapshot** — last-resort user-provided import.

Between consecutive TEFAS-backed providers there is a configurable cooldown (default 12s) so a failed primary does not immediately hammer the same backend.

The cache is performance / degraded-mode support only, not a truth source:

- Fresh cache can serve repeat lookups for specific codes without a provider call.
- Stale cache (configurable threshold, default 7 days) is **reported as stale** and **blocked** from recommendation-grade use.
- If providers conflict on the latest price beyond tolerance, that fund's history is blocked rather than silently chosen.

Every report explicitly separates:

- verified data
- unavailable data
- estimated / inferred data
- user-provided data

The engine will not:

- hallucinate market / news / social sentiment
- pretend to access external APIs it does not have
- fabricate macro context
- treat user-provided narrative as the main decision source

If the engine cannot build an investable universe, it returns `veri yok` with a missing-data list. It does not make something up to look complete.

## Portfolio state model

Source of truth is **you, confirming what you did**. There is no automated reconciliation.

Files created at runtime:

```
portfolio/transaction_history.jsonl   # append-only ledger
portfolio/portfolio_state.json        # derived from confirmed transactions only
portfolio/snapshots/*.json            # snapshot per confirmed mutation
```

Each monthly analysis answers two separate questions:

- **A)** *If I were starting from zero today, what allocation would the engine pick?*
- **B)** *Given my current positions, what action makes sense?*

The two answers can differ. Existing holdings get no loyalty bonus. A fund is kept only if current momentum / ranking / regime still justify it and the switch advantage to a fresher candidate is small.

Supported portfolio actions: `BUY`, `HOLD`, `INCREASE`, `REDUCE`, `SWITCH`, `PARTIAL SWITCH`.

## Architecture

```
main.py                          thin entrypoint
cli.py                           argument parsing and orchestration
config.py                        parameters and paths
data_fetcher.py                  provider orchestration + cache safety
data_providers.py                pytefas, direct TEFAS, crawler, manual snapshot providers
data_provider_healthcheck.py     provider smoke checks (used by --healthcheck)
cache.py                         SQLite storage with source attribution + age metadata
universe_builder.py              investable universe filtering
analyzer.py                      momentum / trend / volatility / drawdown metrics
scorer.py                        opportunity and money market scoring
regime_detector.py               lightweight macro regime modifier
allocator.py                     two-leg allocation decision
reporter.py                      markdown report + append-only JSONL decision history
backtester.py                    simple monthly return evaluation utilities
portfolio_store.py               append-only transaction ledger + derived state
portfolio_manager.py             stateful continuity layer above the quant engine
prompts/                         external research prompts (used only when needed)
tests/                           behavior tests
```

## Tests

```bash
python3 -m unittest discover -s tests
```

15 tests, runs in well under a second.

## Limitations and honest caveats

- TEFAS public API can change without notice. When it does, the provider layer must be updated.
- TEFAS rate-limits aggressively (~6 requests/min). Wide universe fetches are slow on purpose; the engine prefers correctness over speed.
- Money market yield selection uses a recent return proxy, not a clean yield curve.
- The macro regime layer is a **modifier**, not a prediction engine. Treat it as that.
- The backtester is deliberately simple. It is for sanity checks, not parameter optimization.
- Broker availability, fund subscription windows, lot rules and order constraints are not visible to fonbot — you check those yourself before executing.

## Philosophy in one line

> Be aggressive when the data is clear, defensive when it isn't, and honest about the difference.

## License

MIT.

## Disclaimer

This is a personal decision-support tool, not investment advice. It does not know your tax situation, your liquidity needs, or your risk capacity. You are responsible for every trade you place.
