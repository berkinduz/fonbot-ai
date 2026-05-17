"""External macro + news scanner. Autonomous data collection layer.

This module is what makes fonbot independent of user-pasted research. It fetches:

- Macro proxies from Yahoo Finance (USDTRY, Nasdaq, Gold, BIST100) — 1M change
- Turkey policy-rate / inflation context via Google News RSS
- Market and fund-specific news via Google News RSS

The output is a strictly factual JSON document. It records what was fetched,
what failed, and what is unknown. It never infers buy/sell from news copy —
that interpretation happens in `external_intelligence.py`.

Design rules:

- No fabricated data: every section reports its source URLs and unknowns.
- Network failures degrade gracefully: a failed source becomes an "unknowns"
  entry, not an exception that crashes the pipeline.
- Cacheable: the result is written to a JSON file the rest of the engine reads.
"""
from __future__ import annotations

import argparse
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable, Iterable, List

from config import FundbotConfig

FetchText = Callable[[str], str]


class ExternalScanner:
    """Autonomous macro + news collector for the external-context gate."""

    YAHOO_SYMBOLS = {
        "USDTRY": "TRY=X",
        "Nasdaq": "^IXIC",
        "Gold": "GC=F",
        "BIST100": "XU100.IS",
    }

    NEWS_QUERIES_GENERAL = ["TEFAS fon piyasası", "BIST hisse senedi fonları"]
    NEWS_QUERIES_PER_FUND = [
        "{code} fon",
        "{code} fon KAP",
        "{code} fon tasfiye yönetim değişikliği",
        "{code} fon kurucu duyuru",
    ]
    RATES_QUERIES = ["TCMB politika faizi", "TÜİK enflasyon yıllık"]

    def __init__(self, fetch_text: FetchText | None = None):
        self.fetch_text = fetch_text or default_fetch_text

    def scan(self, codes: Iterable[str], output_path: Path | None = None) -> dict:
        codes = [c.strip().upper() for c in codes if c.strip()]
        macro = self._scan_macro()
        rates = self._scan_rates_inflation()
        news = self._scan_news(codes)
        context = {
            "schema": "fundbot_external_context_v1",
            "date": date.today().isoformat(),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "codes": codes,
            "sections": {
                "macro_regime": macro,
                "rates_inflation": rates,
                "market_news": news,
                "fund_specific": self._fund_specific_from_news(codes, news),
                "execution_timing": {
                    "verified_facts": ["TEFAS-listed funds are generally buyable; orders should be placed during business hours"],
                    "unknowns": [],
                    "items": [],
                },
            },
            "risks": self._derive_risks(macro, rates, news),
            "sources": sorted(set(macro.get("sources", []) + rates.get("sources", []) + news.get("sources", []))),
        }
        if output_path is not None:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(context, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        return context

    def _scan_macro(self) -> dict:
        facts: List[str] = []
        unknowns: List[str] = []
        items = []
        sources = []
        for label, symbol in self.YAHOO_SYMBOLS.items():
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?range=1mo&interval=1d"
            try:
                payload = json.loads(self.fetch_text(url))
                closes = _extract_yahoo_closes(payload)
                if len(closes) < 2:
                    unknowns.append(f"macro source returned insufficient closes for {label}/{symbol}")
                    continue
                change = (closes[-1] / closes[0] - 1) * 100
                facts.append(f"{label} checked via Yahoo chart: 1M change {change:.2f}%")
                items.append({"label": label, "symbol": symbol, "first": closes[0], "latest": closes[-1], "change_1m_pct": round(change, 2)})
                sources.append(url)
            except Exception as exc:
                unknowns.append(f"macro source failed for {label}/{symbol}: {type(exc).__name__}: {exc}")
        if facts:
            facts.append("external macro market proxies checked")
        return {"verified_facts": facts, "unknowns": unknowns, "items": items, "sources": sources}

    def _scan_rates_inflation(self) -> dict:
        facts: List[str] = []
        unknowns: List[str] = []
        items = []
        sources = []
        for query in self.RATES_QUERIES:
            url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": query, "hl": "tr", "gl": "TR", "ceid": "TR:tr"})
            try:
                parsed = _parse_rss_items(self.fetch_text(url), limit=3)
                for parsed_item in parsed:
                    parsed_item = {"query": query, **parsed_item}
                    numbers = _extract_percent_numbers(parsed_item.get("title", ""))
                    if numbers:
                        parsed_item["percent_numbers"] = numbers
                    items.append(parsed_item)
                    if parsed_item.get("link"):
                        sources.append(parsed_item["link"])
                sources.append(url)
            except Exception as exc:
                unknowns.append(f"policy/inflation RSS source failed for {query}: {type(exc).__name__}: {exc}")
        # Best-effort: try to extract policy rate / inflation from RSS titles.
        derived_item = self._derive_rate_inflation_from_items(items)
        if derived_item:
            items.append(derived_item)
            facts.append(f"derived from RSS titles: policy={derived_item.get('policy_rate')} inflation={derived_item.get('inflation_yoy')}")
        if items and not facts:
            facts.append("external policy-rate/inflation RSS context checked")
        if facts:
            facts.append("external policy-rate/inflation context checked")
        return {"verified_facts": facts, "unknowns": unknowns, "items": items, "sources": sources}

    def _derive_rate_inflation_from_items(self, items: List[dict]) -> dict | None:
        policy_value = None
        inflation_value = None
        for item in items:
            title = (item.get("title") or "").lower()
            numbers = item.get("percent_numbers") or []
            if not numbers:
                continue
            top = numbers[0]
            if any(k in title for k in ["politika faiz", "tcmb faiz", "faiz oranı"]) and policy_value is None and 5 <= top <= 100:
                policy_value = top
            if any(k in title for k in ["enflasyon", "tüfe", "yıllık fiyat"]) and inflation_value is None and 5 <= top <= 200:
                inflation_value = top
        if policy_value is None and inflation_value is None:
            return None
        derived: dict = {"label": "Turkey policy/inflation (RSS-derived)"}
        if policy_value is not None:
            derived["policy_rate"] = policy_value
        if inflation_value is not None:
            derived["inflation_yoy"] = inflation_value
        if policy_value is not None and inflation_value is not None:
            derived["real_rate_gap"] = round(policy_value - inflation_value, 2)
        return derived

    def _scan_news(self, codes: List[str]) -> dict:
        queries = list(self.NEWS_QUERIES_GENERAL)
        for code in codes:
            for template in self.NEWS_QUERIES_PER_FUND:
                queries.append(template.format(code=code))
        facts: List[str] = []
        unknowns: List[str] = []
        items = []
        sources = []
        for query in queries:
            url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": query, "hl": "tr", "gl": "TR", "ceid": "TR:tr"})
            try:
                text = self.fetch_text(url)
                parsed = _parse_rss_items(text, limit=5)
                for item in parsed:
                    item = {"query": query, **item}
                    items.append(item)
                    if item.get("link"):
                        sources.append(item["link"])
                sources.append(url)
            except Exception as exc:
                unknowns.append(f"news RSS source failed for {query}: {type(exc).__name__}: {exc}")
        if items:
            facts.append("external market/news RSS checked")
            facts.append(f"news items collected: {len(items)}")
        return {"verified_facts": facts, "unknowns": unknowns, "items": items, "sources": sources}

    def _fund_specific_from_news(self, codes: List[str], news: dict) -> dict:
        code_set = set(codes)
        matched = [
            item
            for item in news.get("items", [])
            if _is_recent(item.get("published")) and any(code in str(item.get("title", "")).upper().split() for code in code_set)
        ]
        facts = ["fund-specific news search checked"] if codes else []
        unknowns = [] if matched else (["no recent fund-code-specific news items found in RSS scan"] if codes else ["no selected fund codes supplied"])
        return {"verified_facts": facts, "unknowns": unknowns, "items": matched}

    def _derive_risks(self, macro: dict, rates: dict, news: dict) -> List[str]:
        risks: List[str] = []
        for item in macro.get("items", []):
            if item.get("label") == "USDTRY" and item.get("change_1m_pct", 0) > 5:
                risks.append("USDTRY rose more than 5% over 1M; check FX/risk regime before aggressive allocation")
            if item.get("label") in {"Nasdaq", "BIST100"} and item.get("change_1m_pct", 0) < -8:
                risks.append(f"{item.get('label')} fell more than 8% over 1M; equity-fund risk backdrop weakened")
        for item in rates.get("items", []):
            policy = item.get("policy_rate")
            inflation = item.get("inflation_yoy")
            if policy is not None and inflation is not None and policy - inflation < -10:
                risks.append("policy rate is more than 10pp below inflation; negative real-rate backdrop flagged")
        titles = " ".join(str(i.get("title", "")).lower() for i in news.get("items", []))
        for keyword in ["tasfiye", "durdur", "soruşturma", "manipülasyon"]:
            if keyword in titles:
                risks.append(f"news scan contains risk keyword: {keyword}")
        return risks


def default_fetch_text(url: str, timeout: int = 20) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "fonbot-external-scan/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _extract_yahoo_closes(payload: dict) -> List[float]:
    result = (payload.get("chart", {}).get("result") or [])[0]
    quote = (result.get("indicators", {}).get("quote") or [])[0]
    closes = quote.get("close") or []
    return [float(v) for v in closes if v is not None and float(v) > 0]


def _extract_percent_numbers(text: str) -> List[float]:
    values = []
    for match in re.findall(r"%\s*(\d+(?:[\.,]\d+)?)|(\d+(?:[\.,]\d+)?)\s*%", text):
        raw = match[0] or match[1]
        try:
            values.append(float(raw.replace(",", ".")))
        except ValueError:
            continue
    return values


def _parse_rss_items(text: str, limit: int = 5) -> List[dict]:
    root = ET.fromstring(text)
    items = []
    for item in root.findall(".//item")[:limit]:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        published = (item.findtext("pubDate") or "").strip()
        if title or link:
            items.append({"title": title, "link": link, "published": published})
    return items


def _is_recent(published: str | None, max_age_days: int = 120) -> bool:
    if not published:
        return False
    try:
        dt = parsedate_to_datetime(published)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError, OverflowError):
        return False
    age = datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
    return 0 <= age.days <= max_age_days


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Autonomously collect fonbot external macro/news context")
    parser.add_argument("--codes", default="", help="Comma-separated fund codes to include in news search")
    parser.add_argument("--output", default=str(FundbotConfig().external_context_path), help="Output JSON path")
    return parser


def main(argv: List[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    output = Path(args.output) if args.output else None
    context = ExternalScanner().scan(codes=codes, output_path=output)
    print(f"external context written: {output}")
    print(f"sources={len(context.get('sources', []))} risks={len(context.get('risks', []))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
