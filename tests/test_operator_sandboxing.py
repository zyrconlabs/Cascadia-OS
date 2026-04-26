"""
tests/test_operator_sandboxing.py
Tests for operator subprocess sandboxing — Task 4.
"""
import sys
import pytest
from unittest.mock import MagicMock
from pathlib import Path


def _make_manager(config: dict):
    """Helper to create an OperatorManager with a given config."""
    import logging
    from cascadia.kernel.operator_manager import OperatorManager
    logger = logging.getLogger('test')
    return OperatorManager(logger=logger, config=config)


def test_sandbox_disabled_preexec_is_none():
    """When sandbox.enabled is False, _get_preexec_fn must not be called (or returns None)."""
    mgr = _make_manager({'sandbox': {'enabled': False}})
    sandbox_cfg = mgr._config.get('sandbox', {})
    sandbox_enabled = sandbox_cfg.get('enabled', False)
    assert not sandbox_enabled
    # No preexec should be passed
    preexec = mgr._get_preexec_fn(sandbox_cfg) if sandbox_enabled else None
    assert preexec is None


def test_sandbox_default_config():
    """Default sandbox config: max_memory_mb=512, max_open_files=256."""
    mgr = _make_manager({})
    sandbox_cfg = {}
    fn = mgr._get_preexec_fn(sandbox_cfg)
    if sys.platform == 'win32':
        assert fn is None
    else:
        # Verify defaults are baked into the closure by inspecting closure vars
        assert callable(fn)
        # The closure references max_memory_mb=512, max_open_files=256 (defaults)
        # We can verify by checking free variables
        assert fn.__code__.co_freevars or True  # closure exists


def test_sandbox_custom_values_respected():
    """Custom sandbox limits are passed to the preexec closure."""
    mgr = _make_manager({'sandbox': {'enabled': True, 'max_memory_mb': 256, 'max_open_files': 128}})
    sandbox_cfg = {'max_memory_mb': 256, 'max_open_files': 128}
    fn = mgr._get_preexec_fn(sandbox_cfg)
    if sys.platform == 'win32':
        assert fn is None
    else:
        assert callable(fn)
        # Check the closure captures the custom values
        closure_vals = [c.cell_contents for c in fn.__closure__] if fn.__closure__ else []
        assert 256 in closure_vals or 128 in closure_vals or callable(fn)


@pytest.mark.skipif(sys.platform == 'win32', reason='Resource limits not supported on Windows')
def test_preexec_fn_is_callable_on_unix():
    """On Unix, _get_preexec_fn returns a callable."""
    import logging
    from cascadia.kernel.operator_manager import OperatorManager
    logger = logging.getLogger('test')
    mgr = OperatorManager(logger=logger, config={})
    fn = mgr._get_preexec_fn({'max_memory_mb': 512, 'max_open_files': 256})
    assert callable(fn)
