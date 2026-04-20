# MATURITY: PRODUCTION — Append-only step log. Source of truth for resume.
from __future__ import annotations

from typing import Any, Dict, List

from .run_store import RunStore


class StepJournal:
    """Owns append-only step records. Does not own side-effect commit tracking."""

    def __init__(self, run_store: RunStore) -> None:
        self.run_store = run_store

    def append_step(self, *, run_id: str, step_name: str, step_index: int, started_at: str, completed_at: str | None = None, input_state: Dict[str, Any] | None = None, output_state: Dict[str, Any] | None = None, failure_reason: str | None = None) -> None:
        """Owns insertion of step rows. Does not own run-level state transitions."""
        with self.run_store.connection() as conn:
            conn.execute('INSERT INTO steps (run_id, step_name, step_index, started_at, completed_at, input_state, output_state, failure_reason) VALUES (?,?,?,?,?,?,?,?)', (run_id, step_name, step_index, started_at, completed_at, self.run_store.dump_json(input_state or {}), self.run_store.dump_json(output_state or {}), failure_reason))

    def list_steps(self, run_id: str) -> List[Dict[str, Any]]:
        """Owns retrieval of step rows. Does not own replay decisions."""
        with self.run_store.connection() as conn:
            rows = conn.execute('SELECT * FROM steps WHERE run_id = ? ORDER BY step_index ASC, id ASC', (run_id,)).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item['input_state'] = self.run_store.load_json(item['input_state'])
            item['output_state'] = self.run_store.load_json(item['output_state'])
            out.append(item)
        return out

    def last_per_step(self, run_id: str) -> list[dict]:
        """
        Return the last (highest id) record for each step_index.
        This is the authoritative record — begin-only rows are overridden
        by the committed row written after the step completes.
        """
        with self.run_store.connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM steps
                WHERE id IN (
                    SELECT MAX(id) FROM steps
                    WHERE run_id = ?
                    GROUP BY step_index
                )
                ORDER BY step_index ASC
                """,
                (run_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item['input_state'] = self.run_store.load_json(item['input_state'])
            item['output_state'] = self.run_store.load_json(item['output_state'])
            result.append(item)
        return result

