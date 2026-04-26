"""
tests/test_iot_trigger.py
Tests for TriggerDefinition and TriggerEngine — Task 9.
"""
import pytest
from unittest.mock import patch


def test_all_operators_evaluate_correctly():
    """All 6 operators (gt, lt, gte, lte, eq, neq) evaluate correctly."""
    from cascadia.iot.trigger import TriggerDefinition

    cases = [
        ('gt',  10.0, 5.0,  True),
        ('gt',  5.0,  10.0, False),
        ('lt',  3.0,  5.0,  True),
        ('lt',  5.0,  3.0,  False),
        ('gte', 5.0,  5.0,  True),
        ('gte', 4.9,  5.0,  False),
        ('lte', 5.0,  5.0,  True),
        ('lte', 5.1,  5.0,  False),
        ('eq',  5.0,  5.0,  True),
        ('eq',  5.1,  5.0,  False),
        ('neq', 5.0,  5.0,  False),
        ('neq', 5.1,  5.0,  True),
    ]
    for op, value, threshold, expected in cases:
        td = TriggerDefinition('t', 'dev', 'field', op, threshold, 'wf_1')
        assert td.evaluate(value) == expected, f'{op}({value}, {threshold}) should be {expected}'


def test_cooldown_prevents_double_firing():
    """Cooldown prevents a trigger from firing twice in quick succession."""
    from cascadia.iot.trigger import TriggerDefinition

    td = TriggerDefinition('t', 'dev', 'temp', 'gt', 80.0, 'wf_cool', cooldown_seconds=60)
    assert td.is_cooled_down() is True
    td.mark_fired()
    assert td.is_cooled_down() is False


def test_invalid_operator_raises_value_error():
    """Registering a trigger with an invalid operator raises ValueError."""
    from cascadia.iot.trigger import TriggerDefinition
    with pytest.raises(ValueError, match='Invalid operator'):
        TriggerDefinition('t', 'dev', 'field', 'badop', 10.0, 'wf_1')


def test_process_filters_by_device_id():
    """process() only fires triggers for the matching device_id."""
    from cascadia.iot.trigger import TriggerDefinition, TriggerEngine

    engine = TriggerEngine(vanguard_port=6202)
    engine.register(TriggerDefinition('t1', 'correct_device', 'temp', 'gt', 80.0, 'wf_heat'))

    # Wrong device — should fire nothing
    with patch.object(engine, '_fire') as mock_fire:
        result = engine.process('wrong_device', {'temp': 100.0})
        mock_fire.assert_not_called()
    assert result == []


def test_process_returns_empty_with_no_triggers():
    """process() returns empty list when no triggers are registered."""
    from cascadia.iot.trigger import TriggerEngine

    engine = TriggerEngine(vanguard_port=6202)
    result = engine.process('any_device', {'temp': 99.0})
    assert result == []
