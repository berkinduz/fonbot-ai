# SIGNAL_TEMPLATE.md

Use this when the user asks to add a new signal (a new momentum measure, a new trend filter, a new risk modifier, etc.).

## Contract

A signal is a measurable property computed from a fund's price history. It enters `FundMetrics` and is consumed by `FundScorer`. A signal is **not** a strategy on its own — it is an input the scorer combines with weights.

A new signal goes through three layers:

1. **Computation** in `analyzer.py` — pure function of price history.
2. **Storage** in `models.FundMetrics` — add the new field (frozen dataclass).
3. **Use** in `scorer.py` + a default weight in `strategy/weights.json`.

## Steps

1. **Add the metric to `FundMetrics`** in `models.py`. Keep it a primitive (float / bool). No nested objects.

2. **Compute it in `FundAnalyzer.analyze_fund`** in `analyzer.py`. Keep the computation deterministic, vectorized, and short. If it requires more than ~15 lines, factor a helper inside the same file.

3. **Add a default weight in `strategy/weights.json`** under `scorer`. **Start with weight 0** so existing behavior is unchanged. Document the parameter in the JSON's `note` field or as a comment in the loader if Python defaults need updating.

4. **Use it in `FundScorer._score_one`** in `scorer.py`. Read the weight from `self.s["your_new_weight"]`. Surface the contribution in the `reasons` list when it's non-trivial.

5. **Append to `strategy/history.jsonl`** documenting the addition:
   ```json
   {"dt": "...", "change_type": "signal_added", "version": N, "signal": "your_signal_name", "default_weight": 0, "reason": "...", "approved_by": "user"}
   ```

6. **Add a test** in `tests/test_fundbot_core.py`:
   - Construct a synthetic price series that should produce your signal.
   - Assert `FundAnalyzer().analyze_fund(...)` returns the expected value.
   - Optional: assert scorer with weight=N changes the score in the expected direction.

7. **Run** `python3 -m unittest discover -s tests`. Must pass.

8. **Mention the new signal in README**'s philosophy section if it's a primary signal; in the roadmap if it's experimental.

## Approval flow

- Default weight is 0 → behavior is unchanged → no user approval needed to land the **code**.
- Changing the default weight from 0 to non-zero → **requires explicit user approval** per AGENTS.md §3.4.

## Anti-patterns

- Do not add a signal that depends on external data (news, sentiment). Those go through `research/`, not `analyzer.py`.
- Do not add a signal whose computation requires more than the fund's own price history. Cross-fund / macro signals belong in `regime_detector.py`.
- Do not bump multiple weights in the same change. One signal, one decision, one history entry.
- Do not skip the test. A signal without a test is a future regression.
