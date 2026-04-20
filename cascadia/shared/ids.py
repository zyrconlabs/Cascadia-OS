# MATURITY: PRODUCTION — Deterministic SHA-256 effect keys.
from __future__ import annotations

import hashlib
import uuid


def new_run_id() -> str:
    """Owns generation of unique run IDs. Does not own persistence."""
    return f'run_{uuid.uuid4().hex[:12]}'


def effect_key(run_id: str, step_index: int, action: str, target: str) -> str:
    """Owns deterministic side-effect key generation. Does not own action execution."""
    payload = f'{run_id}:{step_index}:{action}:{target}'.encode('utf-8')
    return hashlib.sha256(payload).hexdigest()
