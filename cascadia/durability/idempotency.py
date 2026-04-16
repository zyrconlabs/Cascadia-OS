# MATURITY: PRODUCTION — SHA-256 keyed, UNIQUE DB constraint enforced.
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .run_store import RunStore


class IdempotencyManager:
    """Owns side-effect registration and commit state. Does not own the side-effect execution itself."""

    def __init__(self, run_store: RunStore) -> None:
        self.run_store = run_store

    def register_planned(self, *, run_id: str, step_index: int, effect_type: str, effect_key: str, target: str, payload: Dict[str, Any], created_at: str) -> bool:
        """Owns planned-effect insertion. Does not own duplicate-retry policy beyond unique keys."""
        try:
            with self.run_store.connection() as conn:
                conn.execute('INSERT INTO side_effects (run_id, step_index, effect_type, effect_key, status, target, payload, created_at, committed_at) VALUES (?,?,?,?,?,?,?,?,?)', (run_id, step_index, effect_type, effect_key, 'planned', target, self.run_store.dump_json(payload), created_at, None))
            return True
        except Exception:
            return False

    def commit(self, effect_key: str, committed_at: str) -> None:
        """Owns commit marking for side effects. Does not own compensation logic."""
        with self.run_store.connection() as conn:
            conn.execute('UPDATE side_effects SET status = ?, committed_at = ? WHERE effect_key = ?', ('committed', committed_at, effect_key))

    def all_for_step(self, run_id: str, step_index: int) -> List[Dict[str, Any]]:
        """Owns step-level effect queries. Does not own resume logic."""
        with self.run_store.connection() as conn:
            rows = conn.execute('SELECT * FROM side_effects WHERE run_id = ? AND step_index = ? ORDER BY id ASC', (run_id, step_index)).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item['payload'] = self.run_store.load_json(item['payload'])
            out.append(item)
        return out
