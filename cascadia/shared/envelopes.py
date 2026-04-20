# MATURITY: PRODUCTION — IPC message envelope schema.
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict


@dataclass(slots=True)
class Envelope:
    """Owns message envelope structure. Does not own transport or delivery guarantees."""
    sender: str
    target: str
    message_type: str
    payload: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
