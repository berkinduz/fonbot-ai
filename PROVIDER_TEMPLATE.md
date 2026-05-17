# PROVIDER_TEMPLATE.md

Use this when the user asks to add a new data provider (e.g. yfinance for NASDAQ, CoinGecko for crypto, a different TEFAS wrapper, Alpha Vantage, etc.).

## Contract

A provider is a subclass of `BaseDataProvider` in `data_providers.py`. It must:

1. Implement `fetch(self, scope: FetchScope) -> ProviderResponse`.
2. Raise on failure — do **not** return empty silently. The orchestrator depends on exception-based fallback.
3. Return prices in the normalized shape: a `pd.DataFrame` per fund with columns `["date", "code", "price"]`.
4. Provide a `ProviderResponse` with:
   - `provider` set to a unique name (e.g. `"yfinance-nasdaq"`)
   - `metadata` DataFrame with columns `["code", "name", "category", "aum", "stock_ratio"]`
   - `histories` dict mapping `code -> price DataFrame`
   - `source_attribution` dict (`code -> provider name`)
   - `verified_data` list with a 1-line human-readable summary
   - `fetched_at` ISO timestamp
5. Handle rate limits itself. Use jittered backoff. Honor `Retry-After` / similar headers.
6. Respect `scope.codes` (if given, fetch only those) and `scope.include_history` (if False, skip price history).

## Skeleton

```python
class MyProvider(BaseDataProvider):
    def __init__(self, priority: int = 50):
        super().__init__("my-provider", priority)

    def fetch(self, scope: FetchScope) -> ProviderResponse:
        # 1. Translate scope into provider-native query
        # 2. Call the API with rate-limit handling
        # 3. Normalize into the standard shape
        # 4. Return ProviderResponse
        raise NotImplementedError
```

## Registration

Add the provider to `TEFASDataFetcher._default_providers()` (or a new `MultiAssetDataFetcher` if it's not TEFAS-backed). Set `priority` so higher priority = lower number = tried first.

If the new provider hits the same backend as an existing TEFAS-backed one, add it to `TEFAS_BACKED_PROVIDERS` so the orchestrator's inter-provider cooldown protects it. If it's a fully different backend, leave it out.

## Tests required before commit

Add to `tests/test_data_provider_integrity.py`:

1. **Happy path**: provider returns histories for requested codes.
2. **Failure path**: provider raises → orchestrator falls through (use the existing `FakeProvider` pattern).
3. **Rate-limit path**: simulate 429 / empty-body / decode error → provider's own backoff handles it without surfacing the error.
4. **Conflict detection**: if you add a second provider for the same backend, verify the orchestrator blocks conflicting prices.

## Anti-patterns — do not do this

- Do not silently return empty DataFrames on failure. Raise. The orchestrator catches it.
- Do not hardcode credentials. Use env vars; document them in README.
- Do not bypass `FetchScope.include_history` — the orchestrator uses 2-stage fetch (scan then deep) and your provider must respect it for performance.
- Do not introduce hidden global state. Providers are constructed fresh per fetch chain.
