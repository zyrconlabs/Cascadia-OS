"""
Shared pytest fixtures for cascadia-os test suite.
"""
import logging
import os

import pytest


@pytest.fixture(autouse=True, scope='session')
def set_test_env():
    """Provide minimal env vars so tests run without external services."""
    # 32 zero bytes base64-encoded — valid AES-256 key for testing only
    os.environ.setdefault('VAULT_ENCRYPTION_KEY', 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=')
    yield


@pytest.fixture(autouse=True)
def suppress_nats_logs():
    """Suppress NATS connection-refused tracebacks — NATS is not running in CI."""
    for name in ('nats', 'nats.aio', 'nats.aio.client'):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    yield
    for name in ('nats', 'nats.aio', 'nats.aio.client'):
        logging.getLogger(name).setLevel(logging.WARNING)
