"""
Cascadia OS
Version is read from pyproject.toml — never hardcode it elsewhere.
"""
import pathlib, re

def _read_version() -> str:
    """Read version from pyproject.toml at repo root."""
    try:
        root = pathlib.Path(__file__).parent.parent
        text = (root / "pyproject.toml").read_text()
        m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
        return m.group(1) if m else "0.0.0"
    except Exception:
        return "0.0.0"

__version__ = _read_version()
VERSION      = __version__
VERSION_SHORT = ".".join(__version__.split(".")[:2])  # "0.34" from "0.34.0"

__all__ = ["__version__", "VERSION", "VERSION_SHORT"]
