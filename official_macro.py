"""Official Turkish macro data collectors for external context.

This module fetches factual official macro inputs only. Interpretation stays in
`external_intelligence.py` so source failures never become hidden assumptions.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Dict, List


FetchText = Callable[[str], str]


EVDS_URL = "https://evds2.tcmb.gov.tr/service/evds/"
BDDK_WEEKLY_URL = "https://www.bddk.org.tr/bultenhaftalik"


@dataclass(frozen=True)
class EVDSSeries:
    label: str
    code: str
    kind: str


DEFAULT_EVDS_SERIES = [
    EVDSSeries("USDTRY", "TP.DK.USD.A", "macro"),
    EVDSSeries("EURTRY", "TP.DK.EUR.A", "macro"),
    EVDSSeries("Inflation", "TP.FE.OKTG01", "inflation"),
    # TCMB weighted average funding cost is the closest stable official proxy
    # here. It is kept configurable because EVDS series names can change.
    EVDSSeries("PolicyRate", "TP.APIFON4", "policy_rate"),
]


class OfficialMacroScanner:
    """Collects TCMB EVDS and BDDK official macro context."""

    def __init__(self, fetch_text: FetchText, evds_key: str | None = None):
        self.fetch_text = fetch_text
        self.evds_key = evds_key if evds_key is not None else os.environ.get("TCMB_EVDS_API_KEY", "")

    def scan(self) -> dict:
        tcmb = self._scan_evds()
        bddk = self._scan_bddk_weekly()
        verified = list(tcmb.get("verified_facts", [])) + list(bddk.get("verified_facts", []))
        unknowns = list(tcmb.get("unknowns", [])) + list(bddk.get("unknowns", []))
        items = list(tcmb.get("items", [])) + list(bddk.get("items", []))
        sources = list(tcmb.get("sources", [])) + list(bddk.get("sources", []))
        return {"verified_facts": verified, "unknowns": unknowns, "items": items, "sources": sources}

    def _scan_evds(self) -> dict:
        if not self.evds_key:
            return {
                "verified_facts": [],
                "unknowns": ["TCMB EVDS skipped: TCMB_EVDS_API_KEY is not set"],
                "items": [],
                "sources": [],
            }
        series = _configured_evds_series()
        end = date.today()
        start = end - timedelta(days=45)
        facts: List[str] = []
        unknowns: List[str] = []
        items: List[dict] = []
        sources: List[str] = []
        latest_by_kind: Dict[str, float] = {}

        for spec in series:
            url = _evds_url(spec.code, start, end, self.evds_key)
            try:
                payload = json.loads(self.fetch_text(url))
                values = _extract_evds_values(payload, spec.code)
                if not values:
                    unknowns.append(f"TCMB EVDS returned no numeric observations for {spec.label}/{spec.code}")
                    continue
                latest = values[-1]
                item = {
                    "source": "tcmb_evds",
                    "label": spec.label,
                    "series": spec.code,
                    "latest": latest["value"],
                    "latest_date": latest["date"],
                }
                if len(values) >= 2 and values[0]["value"] > 0:
                    item["change_1m_pct"] = round((latest["value"] / values[0]["value"] - 1) * 100, 2)
                if spec.kind in {"policy_rate", "inflation"}:
                    latest_by_kind[spec.kind] = latest["value"]
                items.append(item)
                sources.append(url)
                facts.append(f"TCMB EVDS {spec.label}: {latest['value']} ({latest['date']})")
            except Exception as exc:
                unknowns.append(f"TCMB EVDS failed for {spec.label}/{spec.code}: {type(exc).__name__}: {exc}")

        if "policy_rate" in latest_by_kind or "inflation" in latest_by_kind:
            derived = {"source": "tcmb_evds", "label": "Turkey policy/inflation (EVDS)"}
            if "policy_rate" in latest_by_kind:
                derived["policy_rate"] = latest_by_kind["policy_rate"]
            if "inflation" in latest_by_kind:
                derived["inflation_yoy"] = latest_by_kind["inflation"]
            if "policy_rate" in latest_by_kind and "inflation" in latest_by_kind:
                derived["real_rate_gap"] = round(latest_by_kind["policy_rate"] - latest_by_kind["inflation"], 2)
            items.append(derived)
            facts.append("TCMB EVDS policy/inflation official context checked")

        return {"verified_facts": facts, "unknowns": unknowns, "items": items, "sources": sources}

    def _scan_bddk_weekly(self) -> dict:
        try:
            html = self.fetch_text(BDDK_WEEKLY_URL)
        except Exception as exc:
            return {
                "verified_facts": [],
                "unknowns": [f"BDDK weekly bulletin failed: {type(exc).__name__}: {exc}"],
                "items": [],
                "sources": [BDDK_WEEKLY_URL],
            }
        text = _html_to_text(html)
        bulletin_date = _extract_bddk_date(text)
        metrics = _extract_bddk_metrics(text)
        item = {
            "source": "bddk_weekly",
            "label": "BDDK weekly banking bulletin",
            "date": bulletin_date,
            "metrics": metrics,
        }
        facts = ["BDDK weekly banking sector bulletin checked"]
        if bulletin_date:
            facts.append(f"BDDK weekly bulletin date: {bulletin_date}")
        if metrics:
            facts.append(f"BDDK weekly metrics parsed: {', '.join(sorted(metrics)[:5])}")
        unknowns = [] if metrics else ["BDDK weekly bulletin parsed but no known metrics were extracted"]
        return {"verified_facts": facts, "unknowns": unknowns, "items": [item], "sources": [BDDK_WEEKLY_URL]}


def _configured_evds_series() -> List[EVDSSeries]:
    raw = os.environ.get("FONBOT_EVDS_SERIES_JSON", "").strip()
    if not raw:
        return DEFAULT_EVDS_SERIES
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return DEFAULT_EVDS_SERIES
    series: List[EVDSSeries] = []
    for item in payload if isinstance(payload, list) else []:
        try:
            series.append(EVDSSeries(str(item["label"]), str(item["code"]), str(item["kind"])))
        except (KeyError, TypeError):
            continue
    return series or DEFAULT_EVDS_SERIES


def _evds_url(series: str, start: date, end: date, key: str) -> str:
    params = {
        "series": series,
        "startDate": start.strftime("%d-%m-%Y"),
        "endDate": end.strftime("%d-%m-%Y"),
        "type": "json",
        "key": key,
    }
    return EVDS_URL + urllib.parse.urlencode(params)


def _extract_evds_values(payload: dict, series: str) -> List[dict]:
    field = series.replace(".", "_")
    values = []
    for row in payload.get("items") or []:
        raw = row.get(field) or row.get(series) or row.get(field.upper())
        try:
            value = float(str(raw).replace(",", "."))
        except (TypeError, ValueError):
            continue
        if value <= 0:
            continue
        values.append({"date": str(row.get("Tarih") or row.get("tarih") or ""), "value": value})
    return values


def _html_to_text(html: str) -> str:
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_bddk_date(text: str) -> str | None:
    match = re.search(r"(\d{1,2}\s+[A-Za-zÇĞİÖŞÜçğıöşü]+\s+\d{4})", text)
    return match.group(1) if match else None


def _extract_bddk_metrics(text: str) -> dict:
    patterns = {
        "total_loans": r"Toplam Krediler\s+([0-9\.\,]+)\s+([0-9\.\,]+)\s+([0-9\.\,]+)",
        "consumer_loans_and_cards": r"Tüketici Kredileri ve Bireysel Kredi Kartları\s+([0-9\.\,]+)\s+([0-9\.\,]+)\s+([0-9\.\,]+)",
        "sme_loans": r"KOBİ Kredileri\s+([0-9\.\,]+)\s+([0-9\.\,]+)\s+([0-9\.\,]+)",
    }
    metrics = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        values = [_parse_tr_number(v) for v in match.groups()]
        metrics[key] = {"tl": values[0], "fx": values[1], "total": values[2]}
    return metrics


def _parse_tr_number(value: str) -> float:
    return float(value.replace(".", "").replace(",", "."))
