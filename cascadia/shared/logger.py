# MATURITY: PRODUCTION — Structured file + stream logger.
from __future__ import annotations

import logging
from pathlib import Path


def configure_logging(log_dir: str, name: str) -> logging.Logger:
    """Owns logger creation for one component. Does not own log rotation or remote shipping."""
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s | %(name)-12s | %(levelname)-8s | %(message)s')
    fh = logging.FileHandler(Path(log_dir) / f'{name}.log')
    fh.setFormatter(formatter)
    sh = logging.StreamHandler()
    sh.setFormatter(formatter)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger
