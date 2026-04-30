"""
tests/test_settings_chat.py

Tests for the SettingsChatAssistant (Phase 5 — Settings Chat Assistant).

Covers:
- /settings returns a current state summary
- /settings auto returns a profile preview (confirmed=False)
- /settings reset returns a reset preview
- Settings are never saved (confirmed=False) until explicit user confirmation
- Preview is always present for mutating commands
- Numbered flow responses are handled gracefully
"""
import pytest
from cascadia.settings.chat_assistant import SettingsChatAssistant


@pytest.fixture
def assistant():
    return SettingsChatAssistant()


@pytest.fixture
def ctx():
    return {'operator': 'scout', 'business_type': 'contractor'}


# ── Basic command dispatch ──────────────────────────────────────────────────

def test_settings_returns_current_summary(assistant, ctx):
    r = assistant.handle('/settings', ctx)
    assert 'SCOUT' in r['response']
    assert r['preview'] is None
    assert '1' in r['options']


def test_settings_auto_returns_profile_preview(assistant, ctx):
    r = assistant.handle('/settings auto', ctx)
    assert r['preview'] is not None
    assert r['preview']['confirmed'] is False
    assert 'Apply These Settings' in r['options']


def test_settings_reset_returns_reset_preview(assistant, ctx):
    r = assistant.handle('/settings reset', ctx)
    assert r['preview']['type'] == 'reset'
    assert 'Yes, Reset' in r['options']


def test_settings_advanced_returns_summary(assistant, ctx):
    r = assistant.handle('/settings advanced', ctx)
    assert isinstance(r['response'], str)
    assert 'Advanced' in r['response'] or 'advanced' in r['response']
    assert r['preview'] is None


# ── Safety guarantees ───────────────────────────────────────────────────────

def test_settings_not_saved_until_confirmed(assistant, ctx):
    r = assistant.handle('/settings auto', ctx)
    assert r['preview']['confirmed'] is False


def test_settings_reset_not_confirmed(assistant, ctx):
    r = assistant.handle('/settings reset', ctx)
    assert r['preview']['confirmed'] is False


def test_settings_preview_before_save(assistant, ctx):
    """Both /settings auto and /settings reset must include a preview dict."""
    for cmd in ('/settings auto', '/settings reset'):
        r = assistant.handle(cmd, ctx)
        assert r['preview'] is not None, f"{cmd!r} must return a preview"


# ── Flow response handling ──────────────────────────────────────────────────

def test_numbered_flow_response(assistant, ctx):
    r = assistant.handle('1', ctx)
    assert isinstance(r['response'], str)
    assert isinstance(r['options'], list)


def test_unknown_command_falls_through(assistant, ctx):
    r = assistant.handle('hello', ctx)
    assert isinstance(r['response'], str)
    assert r['preview'] is None or isinstance(r['preview'], (dict, type(None)))


# ── Return structure ────────────────────────────────────────────────────────

def test_response_structure_for_all_commands(assistant, ctx):
    cmds = ['/settings', '/settings auto', '/settings reset', '/settings advanced']
    for cmd in cmds:
        r = assistant.handle(cmd, ctx)
        assert 'response' in r, f"{cmd!r} missing 'response'"
        assert 'options' in r, f"{cmd!r} missing 'options'"
        assert 'preview' in r, f"{cmd!r} missing 'preview'"
        assert isinstance(r['response'], str)
        assert isinstance(r['options'], list)


# ── Profile defaults ────────────────────────────────────────────────────────

def test_contractor_profile_has_expected_keys(assistant):
    ctx = {'operator': 'scout', 'business_type': 'contractor'}
    r = assistant.handle('/settings auto', ctx)
    settings = r['preview']['settings']
    assert 'Lead source' in settings
    assert 'Destination' in settings


def test_unknown_business_type_returns_fallback(assistant):
    ctx = {'operator': 'recon', 'business_type': 'unknown_type_xyz'}
    r = assistant.handle('/settings auto', ctx)
    assert r['preview'] is not None
    settings = r['preview']['settings']
    assert len(settings) > 0


# ── Context fallback ────────────────────────────────────────────────────────

def test_missing_operator_in_context(assistant):
    r = assistant.handle('/settings', {})
    # Should not raise, should mention some operator name
    assert isinstance(r['response'], str)
    assert r['preview'] is None


def test_missing_business_type_in_context(assistant):
    r = assistant.handle('/settings auto', {'operator': 'scout'})
    # Should return a preview with general defaults
    assert r['preview'] is not None
    assert r['preview']['confirmed'] is False
