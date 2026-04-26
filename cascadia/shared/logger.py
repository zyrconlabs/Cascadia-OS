# MATURITY: PRODUCTION — Structured file + stream logger.
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

_5MB = 5 * 1024 * 1024


def configure_logging(log_dir: str, name: str) -> logging.Logger:
    """Owns logger creation for one component. Does not own remote shipping."""
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s | %(name)-12s | %(levelname)-8s | %(message)s')
    fh = logging.handlers.RotatingFileHandler(
        Path(log_dir) / f'{name}.log',
        maxBytes=_5MB,
        backupCount=3,
    )
    fh.setFormatter(formatter)
    sh = logging.StreamHandler()
    sh.setFormatter(formatter)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


def get_logger(name: str) -> logging.Logger:
    """Lightweight logger for components that don't own a log directory."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s | %(name)-12s | %(levelname)-8s | %(message)s')
    sh = logging.StreamHandler()
    sh.setFormatter(formatter)
    logger.addHandler(sh)
    return logger
