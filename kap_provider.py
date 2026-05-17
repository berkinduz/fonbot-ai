"""KAP (Kamuyu Aydınlatma Platformu) provider.

KAP is the official disclosure platform for Turkish capital markets. Every
fund structural event (tasfiye, yönetim değişikliği, prospektüs değişikliği,
unvan değişikliği, fee structure changes, fund termination, manager change)
is published here BEFORE it reaches Google News. KAP is the authoritative
source for fund-specific structural risk.

This provider fetches the public KAP feed and filters for fund-relevant
disclosures. It runs as part of the external scanner and contributes items
to the `fund_specific` section (with `source: kap`), which the intelligence
layer treats as a high-confidence confirmation (counts as ≥2 sources for
cross-source confirmation logic in external_intelligence).

The KAP web app uses a JSON API; we try that first, then fall back to a
generic news search as a degraded mode. Failure is silent — the scanner
continues with whatever it has.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Callable, List, Optional


KAP_DISCLOSURE_API = "https://www.kap.org.tr/tr/api/disclosures"
KAP_DISCLOSURE_PAGE = "https://www.kap.org.tr/tr/Bildirim"

# Disclosure types likely to be material for a TEFAS fund. KAP uses Turkish
# type names; we match case-insensitively on substrings.
MATERIAL_TYPE_KEYWORDS = (
    "fon",
    "portföy",
    "yatırım fonu",
    "tasfiye",
    "kurucu",
    "kurul",
    "izahname",
    "yönetim",
    "unvan",
    "birleşme",
)

STRUCTURAL_KEYWORDS = (
    "tasfiye",
    "kapanma",
    "işlem durdur",
    "durdurma",
    "soruşturma",
    "manipülasyon",
    "yönetim değişikliği",
    "kurucu değişikliği",
    "unvan değişikliği",
    "izahname değişikliği",
    "birleşme",
    "devir",
    "stratejide değişiklik",
)

FetchText = Callable[[str], str]


def default_fetch_text(url: str, timeout: int = 20) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "fonbot-kap-provider/1.0",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


class KAPProvider:
    """Fetch recent KAP disclosures relevant to TEFAS funds."""

    def __init__(self, fetch_text: Optional[FetchText] = None, max_items: int = 80):
        self.fetch_text = fetch_text or default_fetch_text
        self.max_items = max_items

    def fetch_recent(self, codes: List[str], days: int = 30) -> dict:
        """Return a section dict matching the external_scan schema."""
        codes = [c.strip().upper() for c in codes if c.strip()]
        facts: List[str] = []
        unknowns: List[str] = []
        items: List[dict] = []
        sources: List[str] = []
        # First try the public disclosures API
        primary = self._try_primary_api(codes, days)
        items.extend(primary["items"])
        facts.extend(primary["facts"])
        unknowns.extend(primary["unknowns"])
        sources.extend(primary["sources"])
        # If the direct API was blocked or returned nothing, fall back to a
        # Google News RSS query restricted to kap.org.tr. This is degraded mode
        # but still surfaces KAP disclosures via Google's index. Items found
        # this way are still marked source=kap so the intelligence layer treats
        # them as authoritative.
        if not items:
            fallback = self._try_news_fallback(codes)
            items.extend(fallback["items"])
            facts.extend(fallback["facts"])
            unknowns.extend(fallback["unknowns"])
            sources.extend(fallback["sources"])
        return {"verified_facts": facts, "unknowns": unknowns, "items": items, "sources": sources}

    def _try_primary_api(self, codes: List[str], days: int) -> dict:
        facts: List[str] = []
        unknowns: List[str] = []
        items: List[dict] = []
        sources: List[str] = []
        params = {
            "fromDate": _days_ago(days),
            "toDate": _today(),
            "year": "",
            "prd": "",
            "term": "",
            "ruleType": "",
            "bdkReview": "",
            "disclosureClass": "",
            "index": "",
            "market": "",
            "isLate": "",
            "subjectList": "",
            "mkkMemberOidList": "",
            "inactiveMkkMemberOidList": "",
            "bdkMemberOidList": "",
            "mainSector": "",
            "sector": "",
            "subSector": "",
            "memberType": "IGS",  # collective investment vehicles (fon)
            "fromSrc": "N",
            "srcCategory": "",
            "discIndex": "",
        }
        url = KAP_DISCLOSURE_API + "?" + urllib.parse.urlencode(params)
        try:
            raw = self.fetch_text(url)
            payload = json.loads(raw)
            disclosures = payload if isinstance(payload, list) else payload.get("disclosures", [])
            sources.append(url)
            count = 0
            for disc in disclosures[: self.max_items]:
                item = self._normalize_disclosure(disc, codes)
                if item is None:
                    continue
                items.append(item)
                if item.get("link"):
                    sources.append(item["link"])
                count += 1
            if count:
                facts.append(f"KAP disclosures fetched: {count} relevant items")
            else:
                unknowns.append(f"KAP returned {len(disclosures)} disclosures, none matched material/fund filters")
        except Exception as exc:
            unknowns.append(f"KAP disclosure API failed: {type(exc).__name__}: {exc}")
        return {"facts": facts, "unknowns": unknowns, "items": items, "sources": sources}

    def _try_news_fallback(self, codes: List[str]) -> dict:
        """Fallback: search Google News with site:kap.org.tr for fund disclosures.

        This is degraded mode (we lose the disclosure type metadata and have to
        infer 'structural' from the title) but still gives us KAP-grade coverage
        when the direct API blocks our user agent.
        """
        import xml.etree.ElementTree as ET
        from email.utils import parsedate_to_datetime
        from datetime import datetime, timezone

        facts: List[str] = []
        unknowns: List[str] = []
        items: List[dict] = []
        sources: List[str] = []
        queries = ["site:kap.org.tr fon"]
        for code in codes:
            queries.append(f"site:kap.org.tr {code}")
            queries.append(f"site:kap.org.tr {code} fon")
        for query in queries:
            url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
                {"q": query, "hl": "tr", "gl": "TR", "ceid": "TR:tr"}
            )
            try:
                raw = self.fetch_text(url)
                root = ET.fromstring(raw)
                count = 0
                for entry in root.findall(".//item")[:10]:
                    title = (entry.findtext("title") or "").strip()
                    link = (entry.findtext("link") or "").strip()
                    published = (entry.findtext("pubDate") or "").strip()
                    if not title:
                        continue
                    # Try to identify which fund code this disclosure refers to
                    matched_code: Optional[str] = None
                    upper = title.upper()
                    for code in codes:
                        if code in upper.split() or f"{code} " in upper or f" {code}" in upper:
                            matched_code = code
                            break
                    item = {
                        "title": title,
                        "link": link,
                        "published": _try_parse_pubdate(published),
                        "query": query,
                        "source": "kap",
                        "publisher": "KAP (via news index)",
                    }
                    if matched_code:
                        item["code"] = matched_code
                    if any(k in title.lower() for k in STRUCTURAL_KEYWORDS):
                        item["structural"] = True
                    items.append(item)
                    if link:
                        sources.append(link)
                    count += 1
                sources.append(url)
                if count:
                    facts.append(f"KAP via news fallback for '{query}': {count} items")
            except Exception as exc:
                unknowns.append(f"KAP news fallback failed for '{query}': {type(exc).__name__}: {exc}")
        return {"facts": facts, "unknowns": unknowns, "items": items, "sources": sources}

    def _normalize_disclosure(self, disc: dict, codes: List[str]) -> Optional[dict]:
        if not isinstance(disc, dict):
            return None
        basic = disc.get("basic", disc)
        subject = str(basic.get("subject") or basic.get("disclosureClass") or "").strip()
        title = str(basic.get("title") or subject).strip()
        publisher = str(basic.get("publisherName") or basic.get("companyName") or "").strip()
        publisher_codes = basic.get("stockCodes") or basic.get("memberCode") or ""
        if isinstance(publisher_codes, list):
            publisher_code_text = " ".join(str(c) for c in publisher_codes)
        else:
            publisher_code_text = str(publisher_codes)
        published = str(basic.get("publishDate") or basic.get("disclosureDate") or "").strip()
        disclosure_id = basic.get("disclosureIndex") or basic.get("index") or basic.get("id")
        link = f"{KAP_DISCLOSURE_PAGE}/{disclosure_id}" if disclosure_id else ""
        searchable = f"{title} {subject} {publisher} {publisher_code_text}".lower()
        if not any(keyword in searchable for keyword in MATERIAL_TYPE_KEYWORDS):
            return None
        matched_code: Optional[str] = None
        upper_haystack = (title + " " + publisher + " " + publisher_code_text).upper()
        for code in codes:
            if code in upper_haystack.split() or code in publisher_code_text.upper():
                matched_code = code
                break
        item = {
            "title": title or subject or "(no title)",
            "link": link,
            "published": _published_iso(published),
            "query": f"KAP IGS {subject}",
            "source": "kap",
            "publisher": publisher,
        }
        if matched_code:
            item["code"] = matched_code
        # Pre-tag structural risk so intelligence layer can boost confidence
        if any(k in searchable for k in STRUCTURAL_KEYWORDS):
            item["structural"] = True
        return item


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _days_ago(days: int) -> str:
    from datetime import timedelta
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")


def _try_parse_pubdate(value: str) -> str:
    if not value:
        return ""
    try:
        from email.utils import parsedate_to_datetime
        from datetime import timezone
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except Exception:
        return value


def _published_iso(value: str) -> str:
    if not value:
        return ""
    try:
        # KAP returns "DD.MM.YYYY HH:MM:SS"
        dt = datetime.strptime(value, "%d.%m.%Y %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(value)
        return dt.isoformat()
    except ValueError:
        return value
