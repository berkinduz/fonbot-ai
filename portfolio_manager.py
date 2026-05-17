from __future__ import annotations

from dataclasses import asdict, dataclass
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
    ) -> PortfolioDecision:
        positions = portfolio_state.get('positions', {}) or {}
        exposure = self._exposure(positions)
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
            previous_month_change={'status': 'unknown_without_prior_snapshot_comparison'},
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

    def _continuation_reasoning(self, action: str, existing_main: Optional[Dict[str, Any]], fresh_code: str) -> List[str]:
        existing_code = existing_main.get('code') if existing_main else None
        if action in {'HOLD', 'INCREASE'}:
            return [f"Continuation is justified only because existing {existing_code or fresh_code} remains competitive under current scores/trend context."]
        if action == 'PARTIAL SWITCH':
            return [f"Continuation is partial: reduce {existing_code}, add {fresh_code}; avoids both inertia and unnecessary full turnover."]
        if action == 'SWITCH':
            return [f"No loyalty to previous position {existing_code}; fresh opportunity {fresh_code} has enough advantage to replace it."]
        return [f"No current main position; start from fresh allocation {fresh_code}."]
