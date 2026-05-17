# Fundbot Operating Protocol

This file describes the operating rules of Fundbot independent of any host system. It applies whether the engine is invoked directly from the CLI or wrapped by another agent.

## Required Input

- Monthly investable amount in TL.

If the amount is missing, only ask for that. Do not require the user to manage the module internals.

## Decision Output

Each run produces exactly:

1. One aggressive main opportunity fund.
2. One defensive money market fund.
3. Allocation ratio.
4. BUY / HOLD / SWITCH / REDUCE / INCREASE / PARTIAL SWITCH action.
5. Confidence score.
6. Missing data list.
7. Manual execution reminder.

## Data Hierarchy

1. Quantitative fund data: primary.
2. Macro/regime context: modifier.
3. External news / social / qualitative research: tertiary.

External narrative data must never override quantitative scoring.

## Integrity Rules

- Never hallucinate live market/news/social data.
- Never pretend TEFAS data exists if provider/cache failed.
- If no investable universe can be built, return "veri yok" and provide the missing-data list.
- User-provided external research is stored as context only; it is not the main decision source.
- Reports are saved under `reports/`.
- `reports/decisions.jsonl` is append-only.

## Typical Flow

1. Confirm investable amount.
2. Run:
   `python3 main.py --amount <TL>`
3. If the engine returns missing data, inspect whether cached/live data can be added.
4. If external qualitative context is needed, use `prompts/grok_research_prompt.md`.
5. Save the generated report; summarize the final decision in plain language.

## Provider / Cache Boundary

Provider priority is pytefas → direct TEFAS JSON → verified crawler fallback → manual CSV/XLSX snapshot. Each fund history carries source attribution in reports/history.

Cache is not a truth source:

- Fresh cache can avoid repeated provider calls for requested codes.
- Stale cache is reported with age and blocked from recommendation-grade use.
- If live fetch fails and fresh cache is used, confidence is degraded and provider failure remains visible.
- If providers conflict on latest price beyond tolerance, that fund history is blocked rather than silently chosen.

Historical fetch should be staged when doing broad discovery:

1. Stage 1: fast universe scan without histories.
2. Stage 2: deep history fetch only for shortlist codes.

## Portfolio State Layer

Fundbot is a stateful tactical portfolio manager layered above the quant/scoring/regime engine.

Runtime state lives under:

- `portfolio/transaction_history.jsonl`
- `portfolio/portfolio_state.json`
- `portfolio/snapshots/`

Rules:

- Source of truth is the user's explicit manual transaction confirmation.
- A natural-language report like "42k AFT aldım" can mutate state only if the user is clearly reporting a completed transaction. If phrasing is ambiguous, record as pending or ask for confirmation.
- Never assume broker sync.
- Never infer current holdings from old recommendations.
- Monthly reports answer both:
  - A) zero-position fresh allocation
  - B) current-portfolio action
- Supported actions: BUY, HOLD, INCREASE, REDUCE, SWITCH, PARTIAL SWITCH.
- Existing holdings get no loyalty bonus; hold only if current score/rank/trend/regime and switch advantage justify continuity.

## Manual Execution Boundary

Fundbot does not place orders, does not connect to bank/broker accounts, and does not automate trades. The user manually executes through their own bank/broker apps.
