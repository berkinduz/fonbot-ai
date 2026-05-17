from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from models import AllocationDecision
from utils.jsonl import append_jsonl


class DecisionReporter:
    def __init__(self, base_dir: Path | None = None):
        self.base_dir = Path(base_dir) if base_dir else Path(__file__).resolve().parent
        self.reports_dir = self.base_dir / "reports"
        self.history_path = self.reports_dir / "decisions.jsonl"

    def save(self, decision: AllocationDecision, candidates: List[Dict[str, Any]], missing_data: List[str], portfolio_decision: Any | None = None, source_attribution: Dict[str, str] | None = None, research_notes: Any | None = None) -> Dict[str, Path]:
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        date_part = decision.created_at[:10]
        report_path = self.reports_dir / f"{date_part}_{decision.decision_id}.md"
        research_briefs = [n.to_brief() for n in (research_notes or [])]
        append_jsonl(self.history_path, {
            "id": decision.decision_id,
            "dt": decision.created_at,
            "type": "fundbot_decision",
            "decision": decision.to_dict(),
            "portfolio_decision": portfolio_decision.to_dict() if portfolio_decision else None,
            "candidates": candidates,
            "missing_data": missing_data,
            "source_attribution": source_attribution or {},
            "research_notes": research_briefs,
            "status": "active",
        })
        report_path.write_text(self.render_markdown(decision, candidates, missing_data, portfolio_decision, source_attribution, research_notes), encoding="utf-8")
        return {"report": report_path, "history": self.history_path}

    def render_markdown(self, decision: AllocationDecision, candidates: List[Dict[str, Any]], missing_data: List[str], portfolio_decision: Any | None = None, source_attribution: Dict[str, str] | None = None, research_notes: Any | None = None) -> str:
        top = "\n".join([f"- {c.get('code')} — score {c.get('score')}" for c in candidates[:3]]) or "- Veri yok"
        unavailable = decision.data_integrity.unavailable_data + missing_data
        unavailable_text = "\n".join([f"- {x}" for x in unavailable]) or "- veri yok"
        sources = "\n".join([f"- {code}: {provider}" for code, provider in sorted((source_attribution or {}).items())]) or "- veri yok"
        reasons = "\n".join([f"- {r}" for r in decision.reasons])
        triggers = "\n".join([f"- {r}" for r in decision.rerun_triggers])
        portfolio_block = self._portfolio_block(portfolio_decision)
        return f"""# Fundbot Karar Raporu — {decision.created_at[:10]}

## Nihai Karar
- Aksiyon: **{decision.action}**
- Agresif ana fon: **{decision.aggressive_fund.code} — {decision.aggressive_fund.name}** (toplam yatırılacak tutarın %{int(decision.aggressive_ratio*100)}'i)
- Para piyasası fonu: **{decision.defensive_fund.code} — {decision.defensive_fund.name}** (toplam yatırılacak tutarın %{int(decision.defensive_ratio*100)}'i)
- Güven: **{decision.confidence}/100**

## Top 3 Aday
{top}

{portfolio_block}

## Neden Bu Dağılım
{reasons}

## Veri Bütünlüğü
Erişilen/doğrulanan veri: {', '.join(decision.data_integrity.verified_data) or 'veri yok'}

Kaynak atfı:
{sources}

Erişilemeyen / veri yok:
{unavailable_text}

Not: X/Twitter, haber ve canlı piyasa verisi otomatik varsayılmaz. Kullanıcı dış araştırma getirirse destekleyici bağlamdır; ana karar kaynağı değildir.

## Kullanıcı Bağlamı (research/)
{self._research_block(research_notes)}

## Yeniden Çalıştırma Tetikleri
{triggers}
"""

    def _research_block(self, research_notes: Any | None) -> str:
        if not research_notes:
            return "- Bu karar için kullanıcı sağlamalı dış araştırma notu yok. Engine yalnızca quant veri kullandı."
        lines = []
        for note in research_notes:
            lines.append(f"- {note.to_brief()}")
            lines.append(f"  → kaynak: {note.path.name}")
        lines.append("")
        lines.append("Not: Bu notlar bağlam olarak eklendi; skorlamayı ve oran kararını etkilemedi.")
        return "\n".join(lines)

    def _portfolio_block(self, portfolio_decision: Any | None) -> str:
        if not portfolio_decision:
            return "## Portföy Sürekliliği\n- Mevcut portföy verisi yok veya değerlendirmeye dahil edilmedi."
        exposure = "\n".join([f"- {k}: {v:,.0f} TL" for k, v in portfolio_decision.current_exposure.items()])
        evals = "\n".join([f"- {x}" for x in portfolio_decision.current_position_evaluation]) or "- veri yok"
        cont = "\n".join([f"- {x}" for x in portfolio_decision.continuation_reasoning]) or "- veri yok"
        txs = "\n".join([f"- {t.get('action')} {t.get('code')} (ratio: {t.get('ratio')})" for t in portfolio_decision.recommended_transactions]) or "- işlem önerisi yok"
        return f"""## Portföy Sürekliliği
### A) Sıfırdan Başlasaydık
{portfolio_decision.question_a}

### B) Mevcut Portföye Göre
{portfolio_decision.question_b}

### Mevcut Portföy Özeti / Current Exposure
{exposure}

### Mevcut Pozisyon Değerlendirmesi
{evals}

### Unrealized Durum
- {portfolio_decision.unrealized_status}

### Continuation Reasoning
{cont}

### Önerilen Manuel İşlem Taslağı
{txs}
"""
