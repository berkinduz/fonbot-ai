from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from utils.jsonl import append_jsonl


class PortfolioStore:
    """Append-only transaction ledger + derived portfolio state.

    Source of truth is user-confirmed manual transactions. Pending/unconfirmed
    records are stored in history but do not mutate portfolio_state.json.
    """

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = Path(base_dir) if base_dir else Path(__file__).resolve().parent
        self.portfolio_dir = self.base_dir / 'portfolio'
        self.history_path = self.portfolio_dir / 'transaction_history.jsonl'
        self.state_path = self.portfolio_dir / 'portfolio_state.json'
        self.snapshots_dir = self.portfolio_dir / 'snapshots'
        self.portfolio_dir.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        # Monotonic per-instance counter so two snapshots in the same microsecond
        # still sort deterministically by creation order.
        self._snapshot_seq = self._initial_snapshot_seq()
        if not self.state_path.exists():
            self._write_state(self._empty_state())

    def _initial_snapshot_seq(self) -> int:
        """Continue snapshot sequence numbering across process restarts."""
        if not self.snapshots_dir.exists():
            return 0
        max_seq = 0
        for path in self.snapshots_dir.glob('*.json'):
            parts = path.stem.split('_')
            # Filename: {timestamp}_{seq6}_{suffix}.json
            if len(parts) >= 2 and parts[1].isdigit():
                max_seq = max(max_seq, int(parts[1]))
        return max_seq

    def _now(self) -> str:
        # seconds precision: stable id space for transactions
        return datetime.now(timezone.utc).isoformat(timespec='seconds')

    def _now_for_filename(self) -> str:
        # microsecond precision + Z, safe for filesystem (no colons)
        return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%S.%fZ')

    def _empty_state(self) -> Dict[str, Any]:
        return {
            'schema': 'fundbot_portfolio_state_v1',
            'updated_at': self._now(),
            'source_of_truth': 'user_confirmed_manual_transactions_only',
            'positions': {},
            'notes': ['No broker sync. Do not assume unreported transactions.'],
        }

    def load_state(self) -> Dict[str, Any]:
        if not self.state_path.exists():
            return self._empty_state()
        return json.loads(self.state_path.read_text(encoding='utf-8'))

    def _write_state(self, state: Dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')

    def _snapshot(self, state: Dict[str, Any], suffix: str) -> Path:
        # Deterministic ordering: timestamp with microseconds + monotonic seq.
        # Two snapshots written in the same microsecond still sort correctly by
        # the zero-padded seq segment, so portfolio_manager's snapshot diff
        # never picks the "wrong" previous snapshot.
        self._snapshot_seq += 1
        stamp = self._now_for_filename()
        path = self.snapshots_dir / f'{stamp}_{self._snapshot_seq:06d}_{suffix}.json'
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        return path

    def record_transaction(
        self,
        code: str,
        name: str,
        action: str,
        amount: float,
        trade_date: str,
        confirmed: bool,
        allocation_ratio: Optional[float] = None,
        role: Optional[str] = None,
        note: str = '',
    ) -> Dict[str, Any]:
        action = action.upper().strip()
        code = code.upper().strip()
        status = 'confirmed' if confirmed else 'pending_user_confirmation'
        raw_id = f'{self._now()}-{code}-{action}-{amount}-{trade_date}-{status}'
        tx = {
            'id': 'fundtx-' + hashlib.sha1(raw_id.encode()).hexdigest()[:12],
            'dt': self._now(),
            'trade_date': trade_date,
            'type': 'fundbot_transaction',
            'status': status,
            'code': code,
            'name': name,
            'action': action,
            'amount': float(amount),
            'allocation_ratio': allocation_ratio,
            'role': role or self._infer_role(action),
            'note': note,
            'source': 'user_manual_statement',
            'broker_sync': False,
        }
        append_jsonl(self.history_path, tx)
        if confirmed:
            state = self._apply_transaction(self.load_state(), tx)
            self._write_state(state)
            self._snapshot(state, tx['id'])
        return tx

    def _infer_role(self, action: str) -> str:
        return 'unknown'

    def _apply_transaction(self, state: Dict[str, Any], tx: Dict[str, Any]) -> Dict[str, Any]:
        positions = dict(state.get('positions', {}))
        code = tx['code']
        amount = float(tx['amount'])
        action = tx['action']
        if action in {'BUY', 'INCREASE'}:
            pos = dict(positions.get(code, {'code': code, 'name': tx['name'], 'cost_amount': 0.0, 'role': tx.get('role') or 'unknown', 'first_trade_date': tx['trade_date'], 'confirmed_transactions': 0}))
            pos['name'] = tx['name'] or pos.get('name') or code
            pos['cost_amount'] = round(float(pos.get('cost_amount', 0.0)) + amount, 2)
            pos['last_trade_date'] = tx['trade_date']
            pos['allocation_ratio'] = tx.get('allocation_ratio') if tx.get('allocation_ratio') is not None else pos.get('allocation_ratio')
            pos['role'] = tx.get('role') or pos.get('role') or 'unknown'
            pos['confirmed_transactions'] = int(pos.get('confirmed_transactions', 0)) + 1
            positions[code] = pos
        elif action in {'SELL', 'REDUCE'}:
            if code in positions:
                pos = dict(positions[code])
                remaining = round(float(pos.get('cost_amount', 0.0)) - amount, 2)
                if remaining <= 0:
                    positions.pop(code, None)
                else:
                    pos['cost_amount'] = remaining
                    pos['last_trade_date'] = tx['trade_date']
                    pos['confirmed_transactions'] = int(pos.get('confirmed_transactions', 0)) + 1
                    positions[code] = pos
        elif action in {'CLOSE'}:
            positions.pop(code, None)
        else:
            raise ValueError(f'Unsupported transaction action: {action}')
        state = dict(state)
        state['positions'] = positions
        state['updated_at'] = self._now()
        state['total_cost_amount'] = round(sum(float(p.get('cost_amount', 0.0)) for p in positions.values()), 2)
        return state
