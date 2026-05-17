# AGENTS.md — Fonbot AI Operator Manual

This file is the **primary instruction surface for AI agents** (Claude Code, Codex, Gemini CLI, OpenHands, Hermes, etc.) operating fonbot on behalf of a user.

**Read this entire file at the start of every session before touching the engine.**

---

## 1. What you are operating

Fonbot is a Python engine that produces tactical TEFAS fund allocation recommendations. It is **not** an autonomous decision-maker. It is **not** a chatbot. It is a deterministic ratio recommender driven by quantitative scoring of TEFAS fund momentum/trend/volatility/regime.

You — the AI agent — are the operator. You sit between the human user and the engine. The user expresses intent in natural language ("bu ayın kararını verelim", "şu Grok cevabını işle", "şu göstergeyi denesek mi"). You translate that intent into engine invocations, read the engine's output, and explain it back to the user in plain language.

The split:

| Layer | Owner | Responsibility |
|---|---|---|
| **Engine** | Python (deterministic) | Fetch TEFAS data, score funds, produce a recommendation, write reports |
| **Operator** | You (AI agent) | Decide *when* to run, *what* to feed, *how* to explain, *what to evolve* |
| **Human** | The user | Approves changes to strategy/state, executes trades, supplies external research |

---

## 2. Mandatory first action in every session

Run:

```bash
python3 main.py --status
```

This prints engine state: cache age, last decision, pending research notes, last strategy change, current portfolio. **Use the output to orient yourself before doing anything else.** Do not skip this step.

---

## 3. Operator playbooks

### 3.1 Monthly recommendation ("bu ayın fonunu seçelim")

1. `python3 main.py --status` — orient. Pay attention to `external_context: status=...`. If status is `missing` / `incomplete` / `stale`, the engine will auto-refresh on the next run (default), so this is informational.
2. If cache is older than a week or user says so: `python3 main.py --force-refresh`. Otherwise just `python3 main.py`. The engine will:
   - Fetch TEFAS data (cached or live).
   - If external context is missing/stale, auto-run the scanner (Yahoo Finance for USDTRY/Nasdaq/Gold/BIST100, Google News RSS for TR rates + news + per-fund queries).
   - Apply external-context modifiers (risk_penalty_delta, regime_score_delta, confidence_cap, avoid_funds) to the allocator.
   - Write a report.
3. Read the generated markdown report from `reports/`.
4. Explain to the user in plain Turkish: which aggressive fund, which money market fund, what ratio, confidence level, what changed since last month.
5. If there is a portfolio_decision section, explain whether the action is BUY / HOLD / SWITCH / etc. and *why*.
6. If the report shows a confidence cap from external context, surface that: "BIST -12% ve faiz/enflasyon gap negatif olduğu için engine confidence'ı 75'e cap'ledi — agresif bandı bilerek aşağıda tuttu."
7. If the report shows `avoid_funds`, explain which fund was skipped and why: "AFT için tasfiye haberi tespit edildi, otomatik avoid listesine alındı; bir sonraki temiz aday seçildi."
8. If pending research notes exist, mention that they were attached as context but did not influence the score.

**Do not paraphrase the rationale.** Quote the engine's reasons. The engine is the source of truth for *why*.

### 3.1b Force a context refresh ("Yahoo/haber verilerini güncelleyelim")

If the user explicitly wants a fresh macro/news scan without producing a recommendation:

```bash
python3 main.py --scan-only --codes AFT,AAL
```

This re-runs the scanner and writes `context/current_external_context.json`. Use when the user mentions a market event ("BIST düştü bugün", "Fed kararı çıktı") and wants the next recommendation to reflect it.

If the user wants a recommendation that explicitly refreshes context first regardless of age:

```bash
python3 main.py --refresh-external-context
```

### 3.2 Transaction reporting ("42k AFT aldım")

If the user reports a completed trade in natural language:

1. Confirm understanding: "anladığım kadarıyla AFT'den 42.000 TL alım yaptın, doğru mu?"
2. Only if user confirms: run `python3 main.py --record-transaction --tx-code AFT --tx-amount 42000 --tx-date YYYY-MM-DD --tx-confirmed --tx-role main_opportunity`
3. If user is ambiguous ("almayı düşünüyorum"): record as `--record-transaction` **without** `--tx-confirmed`. Pending records do not mutate state.
4. Never assume a trade happened. Never sync from a broker — there is no broker integration.

### 3.3 External research ingestion ("Grok şunu söyledi")

When the user shares an external research output (Grok, X, news, Gemini, etc.):

1. Decide source category: `grok | x | news | gemini | user`.
2. Decide relevance: `high | medium | low` (high = directly about a fund or sector fonbot picks from; low = general market mood).
3. Identify which fund codes the note refers to (if any).
4. Pick a short topic slug (kebab-case, Turkish OK): `tech-fonlari-grok-ozeti`.
5. Record:
   ```bash
   python3 main.py --record-research \
     --research-topic tech-fonlari-grok-ozeti \
     --research-source grok \
     --research-relevance medium \
     --research-funds AFT,AAL \
     --research-body-file /tmp/note.md
   ```
   Or pipe body via stdin if there is no file.
6. Tell the user: "Notu `research/` altına kaydettim. Sonraki karar koşulduğunda bağlam olarak rapora eklenecek."

**Hard rule:** research notes are CONTEXT ONLY. They never override quant scoring. If the user expects fonbot to "act on Grok's tip", you explain this rule clearly. The engine treats narrative as tertiary; you must too.

### 3.4 Strategy evolution ("şu ağırlığı denesek")

If the user proposes a strategy tweak (e.g. "3 aylık momentum ağırlığı çok yüksek değil mi?"):

1. Read `strategy/weights.json` and the corresponding code that consumes it (`scorer.py`, `allocator.py`).
2. Explain the current value and what it controls.
3. Propose the change in plain language, including the expected directional effect.
4. **Do not edit `strategy/weights.json` without explicit user approval per change.** Approval can be in this session ("evet değiştir") — that is enough, but it must be explicit.
5. If approved:
   - Edit `strategy/weights.json`.
   - Append a new entry to `strategy/history.jsonl` with: `{dt, change_type, before, after, reason, approved_by}`. `approved_by` is the user's name or `"user"`.
   - Run tests: `python3 -m unittest discover -s tests`.
   - Run `python3 main.py --status` and `python3 main.py --codes <a,b>` to sanity-check the new score.
6. If the user is unsure, do not change anything. Suggest the user re-evaluate after the next monthly run.

**Do not auto-tune.** Do not run a backtest sweep and pick the best parameters silently. Overfitting is the failure mode.

### 3.5 Adding a new data provider or signal

See `PROVIDER_TEMPLATE.md` and `SIGNAL_TEMPLATE.md`. Both files spell out the contract and the minimum tests.

Key invariants you must preserve:

- Providers never fabricate data. On failure, raise; the orchestrator handles fallback.
- Signals never silently change scoring weights — if a new signal needs weight, add it to `strategy/weights.json` with default 0 and explain.
- All new code must have at least one behavior test in `tests/`.

---

## 4. Hard rules — never violate

1. **Never fabricate market or news data.** If something is unavailable, the engine says "veri yok" and so do you. The external scanner produces real Yahoo / Google News data with source URLs — do not invent items beyond what `context/current_external_context.json` actually contains.
2. **Never override quant scoring with external narrative.** Research notes are context, not signal.
3. **Never mutate `portfolio/portfolio_state.json` without an explicit user-confirmed transaction.** Pending records are fine; confirmed records require user words like "evet aldım" / "evet sattım".
4. **Never change `strategy/weights.json` without explicit per-change user approval.** No batch tuning.
5. **Never commit changes that break `python3 -m unittest discover -s tests`.** Tests must pass before commit.
6. **Never add AI attribution to commits or PRs** (no `Co-Authored-By: Claude`, no "Generated with Claude Code" footer). The user's repos are AI-attribution-free.
7. **Never bypass the rate-limit handling.** TEFAS limits to ~6 req/min. The provider layer respects this. Do not work around it.
8. **Never commit `portfolio/` or `reports/`.** They are gitignored for a reason — they contain personal state.

---

## 5. How to talk to the user

- Turkish by default. Match user's register.
- Short. The engine produces the long report; you produce the readable summary.
- Quote the engine when stating *why*. Paraphrase only for *what*.
- Surface uncertainty: if a signal is weak or data is partial, say so.
- Never pretend to know something fonbot did not output.

---

## 6. Self-improvement loop

You — the AI operator — are how fonbot evolves. The Python is frozen; the strategy is mutable.

Healthy evolution looks like:

1. User notices a pattern over months ("AFT hep önerildi ama portföyde tutmuyorum, neden?").
2. You read `reports/decisions.jsonl` and `portfolio/transaction_history.jsonl` to find the pattern.
3. You propose a tweak to scoring weights, regime thresholds, or a new signal.
4. User approves.
5. You implement, test, commit.
6. You log the change in `strategy/history.jsonl` so future sessions know what changed and why.

Unhealthy evolution is anything that bypasses user approval, ignores tests, or fabricates the justification.

---

## 7. Files you should know

| Path | Purpose |
|---|---|
| `main.py`, `cli.py` | Entrypoints. CLI flags are the contract. |
| `strategy/weights.json` | Mutable strategy params. Change only with approval. |
| `strategy/history.jsonl` | Append-only log of strategy changes. |
| `research/` | User-supplied external context. Ingested via `--record-research`. |
| `reports/` | Generated markdown reports + `decisions.jsonl`. Gitignored. |
| `portfolio/` | Append-only transaction ledger + derived state. Gitignored. |
| `data/fundbot.sqlite` | Provider cache. Performance only, not source of truth. |
| `context/current_external_context.json` | Latest scanner output. Gitignored. Refreshed by `--refresh-external-context` or auto when stale. |
| `external_scan.py` | Yahoo + Google News fetcher. Pure data; no inference. |
| `external_intelligence.py` | Translates scan into bounded modifiers (risk/regime deltas, confidence cap, avoid_funds). |
| `external_context.py` | Gate that loads the scan, enforces freshness, runs intelligence. |
| `tests/` | Behavior tests. Run before every commit. |

---

## 8. When in doubt

Ask the user. The cost of asking is one extra turn. The cost of acting on a wrong assumption is a wrong recommendation, a lost commit, or a corrupted state file. Always prefer the cheap option.
