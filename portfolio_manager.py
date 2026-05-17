from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from models import AllocationDecision


@dataclass(frozen=True)
class PortfolioDecision:
    fresh_allocation: AllocationDecision
    portfolio_action: str
    question_a: str
    question_b: str
    current_exposure: Dict[str, float]
    previous_month_change: Dict[str, Any]
    current_position_evaluation: List[str]
    unrealized_status: str
    continuation_reasoning: List[str]
    recommended_transactions: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class PortfolioManager:
    """Decision-continuity layer above the stateless quant allocator.

    It does not change scores, signal hierarchy, or regime logic. It compares the
    fresh allocation with user-confirmed current state and decides continuity vs
    turnover without defending old recommendations.
    """

    def evaluate(
        self,
        fresh_allocation: AllocationDecision,
        portfolio_state: Dict[str, Any],
        current_scores: Dict[str, float],
        switch_advantage_threshold: float = 8.0,
        snapshots_dir: Optional[Path] = None,
    ) -> PortfolioDecision:
        positions = portfolio_state.get('positions', {}) or {}
        exposure = self._exposure(positions)
        previous_change = self._diff_against_previous_snapshot(positions, snapshots_dir)
        fresh_code = fresh_allocation.aggressive_fund.code
        existing_main = self._main_position(positions)
        current_position_evaluation: List[str] = []
        recommended_transactions: List[Dict[str, Any]] = []

        if not existing_main:
            action = 'BUY'
            current_position_evaluation.append('No confirmed current opportunity position; portfolio decision follows fresh allocation.')
            recommended_transactions.append({'action': 'BUY', 'code': fresh_code, 'ratio': fresh_allocation.aggressive_ratio})
        else:
            existing_code = existing_main['code']
            existing_score = float(current_scores.get(existing_code, 0.0))
            fresh_score = float(current_scores.get(fresh_code, fresh_allocation.opportunity_score))
            score_advantage = fresh_score - existing_score
            existing_still_strong = existing_score >= 70
            same_fund = existing_code == fresh_code
            current_position_evaluation.append(
                f"Existing main position {existing_code} score {existing_score:.1f}; fresh candidate {fresh_code} score {fresh_score:.1f}; advantage {score_advantage:.1f}."
            )
            if same_fund:
                action = 'INCREASE' if fresh_allocation.aggressive_ratio >= 0.75 else 'HOLD'
                recommended_transactions.append({'action': action, 'code': existing_code, 'ratio': fresh_allocation.aggressive_ratio if action == 'INCREASE' else 0})
                current_position_evaluation.append('Existing fund remains the fresh top candidate; no defensive justification needed.')
            elif existing_still_strong and score_advantage < switch_advantage_threshold:
                action = 'HOLD'
                recommended_transactions.append({'action': 'BUY', 'code': fresh_code, 'ratio': fresh_allocation.aggressive_ratio, 'note': 'new-money-only optional allocation; avoid forced full turnover'})
                current_position_evaluation.append('Existing fund remains strong and switch advantage is low; unnecessary full turnover avoided.')
            elif existing_score >= 55 and score_advantage >= switch_advantage_threshold:
                action = 'PARTIAL SWITCH'
                recommended_transactions.append({'action': 'REDUCE', 'code': existing_code, 'ratio': 'partial'})
                recommended_transactions.append({'action': 'BUY', 'code': fresh_code, 'ratio': fresh_allocation.aggressive_ratio})
                current_position_evaluation.append('Existing fund is not broken, but fresh candidate has a meaningful advantage; partial switch preferred.')
            else:
                action = 'SWITCH'
                recommended_transactions.append({'action': 'SELL', 'code': existing_code, 'ratio': 'full'})
                recommended_transactions.append({'action': 'BUY', 'code': fresh_code, 'ratio': fresh_allocation.aggressive_ratio})
                current_position_evaluation.append('Existing fund no longer deserves protection; sunk-cost avoidance points to switch.')

        if fresh_allocation.defensive_ratio > 0:
            recommended_transactions.append({'action': 'BUY_OR_HOLD', 'code': fresh_allocation.defensive_fund.code, 'ratio': fresh_allocation.defensive_ratio})

        return PortfolioDecision(
            fresh_allocation=fresh_allocation,
            portfolio_action=action,
            question_a=(
                f"Fresh allocation if starting from zero: {fresh_allocation.aggressive_fund.code} "
                f"%{int(fresh_allocation.aggressive_ratio*100)} + {fresh_allocation.defensive_fund.code} "
                f"%{int(fresh_allocation.defensive_ratio*100)}."
            ),
            question_b=f"Current portfolio action: {action}.",
            current_exposure=exposure,
            previous_month_change=previous_change,
            current_position_evaluation=current_position_evaluation,
            unrealized_status='unknown: broker/current market value not synced; user must provide current values for P/L.',
            continuation_reasoning=self._continuation_reasoning(action, existing_main, fresh_code),
            recommended_transactions=recommended_transactions,
        )

    def _main_position(self, positions: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not positions:
            return None
        role_matches = [p for p in positions.values() if p.get('role') == 'main_opportunity']
        if role_matches:
            return max(role_matches, key=lambda p: float(p.get('cost_amount', 0.0)))
        return max(positions.values(), key=lambda p: float(p.get('cost_amount', 0.0)))

    def _exposure(self, positions: Dict[str, Dict[str, Any]]) -> Dict[str, float]:
        totals: Dict[str, float] = {'main_opportunity': 0.0, 'defensive_money_market': 0.0, 'unknown': 0.0, 'total': 0.0}
        for pos in positions.values():
            role = pos.get('role') or 'unknown'
            amount = float(pos.get('cost_amount', 0.0))
            totals[role] = totals.get(role, 0.0) + amount
            totals['total'] += amount
        return {k: round(v, 2) for k, v in totals.items()}

    def _diff_against_previous_snapshot(self, current_positions: Dict[str, Dict[str, Any]], snapshots_dir: Optional[Path]) -> Dict[str, Any]:
        """Compare current positions against the most recent snapshot.

        Snapshots live under portfolio/snapshots/*.json and are written every
        time a confirmed transaction mutates state. The "previous snapshot" is
        the snapshot taken before the latest mutation — i.e. the second-most-
        recent one. If only one snapshot exists, compare against empty.
        """
        snap_dir = Path(snapshots_dir) if snapshots_dir else Path(__file__).resolve().parent / 'portfolio' / 'snapshots'
        if not snap_dir.exists():
            return {'status': 'no_snapshots_yet', 'positions_now': len(current_positions)}
        snapshots = sorted(snap_dir.glob('*.json'))
        if len(snapshots) < 2:
            return {
                'status': 'first_snapshot_only',
                'positions_now': len(current_positions),
                'note': 'Need at least two snapshots to compute change.',
            }
        # Compare the previous snapshot (second to last) against current state
        prev_path = snapshots[-2]
        try:
            prev_state = json.loads(prev_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            return {'status': 'snapshot_unreadable', 'error': str(exc)}
        prev_positions = prev_state.get('positions', {}) or {}
        prev_codes = set(prev_positions.keys())
        curr_codes = set(current_positions.keys())
        added = sorted(curr_codes - prev_codes)
        removed = sorted(prev_codes - curr_codes)
        kept = sorted(curr_codes & prev_codes)
        cost_changes: List[Dict[str, Any]] = []
        for code in kept:
            prev_cost = float(prev_positions[code].get('cost_amount', 0.0))
            curr_cost = float(current_positions[code].get('cost_amount', 0.0))
            delta = round(curr_cost - prev_cost, 2)
            if abs(delta) >= 0.01:
                cost_changes.append({'code': code, 'prev_cost': prev_cost, 'curr_cost': curr_cost, 'delta': delta})
        prev_updated = str(prev_state.get('updated_at') or '')
        return {
            'status': 'compared',
            'previous_snapshot': prev_path.name,
            'previous_updated_at': prev_updated,
            'positions_added': added,
            'positions_removed': removed,
            'positions_kept': kept,
            'cost_amount_changes': cost_changes,
            'no_change': not (added or removed or cost_changes),
        }

    def _continuation_reasoning(self, action: str, existing_main: Optional[Dict[str, Any]], fresh_code: str) -> List[str]:
        existing_code = existing_main.get('code') if existing_main else None
        if action in {'HOLD', 'INCREASE'}:
            return [f"Continuation is justified only because existing {existing_code or fresh_code} remains competitive under current scores/trend context."]
        if action == 'PARTIAL SWITCH':
            return [f"Continuation is partial: reduce {existing_code}, add {fresh_code}; avoids both inertia and unnecessary full turnover."]
        if action == 'SWITCH':
            return [f"No loyalty to previous position {existing_code}; fresh opportunity {fresh_code} has enough advantage to replace it."]
        return [f"No current main position; start from fresh allocation {fresh_code}."]
