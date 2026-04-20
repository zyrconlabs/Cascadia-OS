# MATURITY: PRODUCTION — JSON config loader.
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


def load_config(config_path: str) -> Dict[str, Any]:
    """Owns loading JSON config. Does not own validation beyond basic file existence."""
    return json.loads(Path(config_path).read_text(encoding='utf-8'))
