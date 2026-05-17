from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:  # pragma: no cover
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None


@dataclass(frozen=True)
class ResearchNote:
    path: Path
    date: str
    topic: str
    source: str
    relevance: str
    funds: List[str] = field(default_factory=list)
    summary: str = ""

    def to_brief(self) -> str:
        funds_part = f" ({', '.join(self.funds)})" if self.funds else ""
        return f"[{self.source}|{self.relevance}] {self.date} — {self.topic}{funds_part}: {self.summary[:200]}"


class ResearchStore:
    """User-provided external research (Grok answers, X threads, news excerpts).

    Notes live in research/ as markdown files with a small YAML-ish frontmatter:

        ---
        date: 2026-05-17
        topic: tech-fonlari-grok-ozeti
        source: grok            # grok | x | news | user | gemini
        relevance: medium       # high | medium | low
        funds: [AFT, AAL]
        ---
        Free-form summary / context paragraphs.

    These notes are NEVER used to override quantitative scoring. The reporter
    surfaces them as user-provided context in the data integrity block, and
    appends recent ones to AllocationDecision.data_integrity.user_provided_data
    so the AI operator can reason about them when explaining decisions.
    """

    FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = Path(base_dir) if base_dir else Path(__file__).resolve().parent
        self.research_dir = self.base_dir / "research"
        self.research_dir.mkdir(parents=True, exist_ok=True)

    def load_recent(self, days: int = 60, fund_codes: Optional[List[str]] = None) -> List[ResearchNote]:
        cutoff = datetime.now(timezone.utc).date()
        notes: List[ResearchNote] = []
        for path in sorted(self.research_dir.glob("*.md")):
            note = self._parse(path)
            if not note:
                continue
            try:
                note_date = datetime.strptime(note.date, "%Y-%m-%d").date()
            except ValueError:
                continue
            age = (cutoff - note_date).days
            if age > days:
                continue
            if fund_codes:
                if note.funds and not (set(c.upper() for c in fund_codes) & set(f.upper() for f in note.funds)):
                    continue
            notes.append(note)
        return notes

    def record(self, topic: str, source: str, relevance: str, body: str, funds: Optional[List[str]] = None, date: Optional[str] = None) -> Path:
        date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-") or "note"
        path = self.research_dir / f"{date}_{source}_{slug}.md"
        funds_part = "[" + ", ".join(f.upper() for f in (funds or [])) + "]"
        frontmatter = (
            "---\n"
            f"date: {date}\n"
            f"topic: {topic}\n"
            f"source: {source}\n"
            f"relevance: {relevance}\n"
            f"funds: {funds_part}\n"
            "---\n"
        )
        path.write_text(frontmatter + body.strip() + "\n", encoding="utf-8")
        return path

    def _parse(self, path: Path) -> Optional[ResearchNote]:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return None
        match = self.FRONTMATTER_RE.match(text)
        if not match:
            return None
        meta = self._parse_frontmatter(match.group(1))
        body = match.group(2).strip()
        funds_field = meta.get("funds", "")
        if isinstance(funds_field, list):
            funds = [str(f).strip().upper() for f in funds_field if str(f).strip()]
        else:
            funds = [f.strip().upper() for f in re.sub(r"[\[\]]", "", str(funds_field)).split(",") if f.strip()]
        summary_line = body.splitlines()[0] if body else ""
        return ResearchNote(
            path=path,
            date=str(meta.get("date", "")).strip(),
            topic=str(meta.get("topic", path.stem)).strip(),
            source=str(meta.get("source", "user")).strip(),
            relevance=str(meta.get("relevance", "medium")).strip(),
            funds=funds,
            summary=summary_line or body[:200],
        )

    def _parse_frontmatter(self, raw: str) -> Dict[str, Any]:
        if yaml is not None:
            try:
                return yaml.safe_load(raw) or {}
            except Exception:
                pass
        data: Dict[str, Any] = {}
        for line in raw.splitlines():
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            data[key.strip()] = value.strip()
        return data
