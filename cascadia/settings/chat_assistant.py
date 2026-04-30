"""
cascadia/settings/chat_assistant.py

Settings conversation flow handler for Cascadia OS.
Called by BELL (or the /api/config/chat route) when it detects a /settings prefix.
Never saves settings without user confirmation — all mutating actions return
a preview dict with confirmed=False.

This module does NOT replace BELL. It is a focused handler for settings
commands that BELL can delegate to via a single if-statement.
"""
from __future__ import annotations


class SettingsChatAssistant:
    """
    Handles /settings commands in operator chat.

    Called by BELL when it detects a /settings prefix.
    Never saves settings without user confirmation.

    Integration point in bell.py (one if-statement at the top of the handler):

        if message.startswith('/settings'):
            from cascadia.settings.chat_assistant import SettingsChatAssistant
            return SettingsChatAssistant().handle(message, context)
    """

    COMMANDS = {'/settings', '/settings auto', '/settings reset', '/settings advanced'}

    def handle(self, message: str, context: dict) -> dict:
        """
        Process a /settings command or a numbered flow response.

        Returns: {response: str, options: list[str], preview: dict | None}
        """
        msg = message.strip().lower()
        if msg == '/settings auto':
            return self._auto_profile(context)
        if msg == '/settings reset':
            return self._reset_preview(context)
        if msg == '/settings advanced':
            return self._advanced_summary(context)
        if msg.startswith('/settings'):
            return self._current_summary(context)
        # Numbered response from user continuing a flow
        return self._handle_flow_response(message, context)

    # ------------------------------------------------------------------
    # Private handlers
    # ------------------------------------------------------------------

    def _current_summary(self, context: dict) -> dict:
        operator = context.get('operator', 'this operator')
        return {
            'response': (
                f"Configuring: {operator.upper()}\n\n"
                "What would you like to change?\n"
                "1. Lead source\n"
                "2. CRM destination\n"
                "3. Approval rules\n"
                "4. Lead score threshold\n"
                "5. Notifications\n"
                "6. Reset to recommended defaults\n\n"
                "Type a number or describe what you want to change."
            ),
            'options': ['1', '2', '3', '4', '5', '6'],
            'preview': None,
        }

    def _auto_profile(self, context: dict) -> dict:
        business_type = context.get('business_type', 'general')
        defaults = self._load_profile_defaults(business_type)
        lines = [f"- {k}: {v}" for k, v in defaults.items()]
        return {
            'response': f"Recommended {business_type.title()} defaults:\n" + "\n".join(lines),
            'options': ['Apply These Settings', 'Review First', 'Cancel'],
            'preview': {
                'type': 'auto_profile',
                'settings': defaults,
                'confirmed': False,
            },
        }

    def _reset_preview(self, context: dict) -> dict:
        operator = context.get('operator', 'this operator')
        return {
            'response': f"Reset {operator.upper()} to recommended defaults?",
            'options': ['Yes, Reset', 'Cancel'],
            'preview': {
                'type': 'reset',
                'operator': operator,
                'confirmed': False,
            },
        }

    def _advanced_summary(self, context: dict) -> dict:
        return {
            'response': (
                "Advanced settings mode. All fields visible. Proceed with care.\n\n"
                "Use /settings to return to the standard menu."
            ),
            'options': [],
            'preview': None,
        }

    def _handle_flow_response(self, message: str, context: dict) -> dict:
        """
        Handle a numbered response from the user continuing a flow.
        Stateless — the caller is responsible for maintaining conversation state.
        """
        return {
            'response': (
                "I didn't understand that. "
                "Type /settings to see what you can configure."
            ),
            'options': [],
            'preview': None,
        }

    def _load_profile_defaults(self, business_type: str) -> dict:
        """
        Load defaults for a business type.
        Tries cascadia.settings.profiles first; falls back to built-in defaults.
        """
        try:
            from cascadia.settings import profiles  # type: ignore
            return profiles.get_defaults(business_type)
        except (ImportError, AttributeError):
            pass
        # Built-in fallbacks keyed by business_type
        _BUILT_IN: dict[str, dict] = {
            'contractor': {
                'Lead source': 'Gmail',
                'Destination': 'Google Sheets',
                'Approval': 'Always ask',
                'Notifications': 'SMS',
                'Business hours only': 'Yes',
            },
            'retail': {
                'Lead source': 'Web Form',
                'Destination': 'Google Sheets',
                'Approval': 'High-risk only',
                'Notifications': 'Email',
                'Business hours only': 'No',
            },
            'professional_services': {
                'Lead source': 'Gmail',
                'Destination': 'HubSpot',
                'Approval': 'High-risk only',
                'Notifications': 'Slack',
                'Business hours only': 'Yes',
            },
        }
        return _BUILT_IN.get(business_type, {
            'Lead source': 'Gmail',
            'Destination': 'Google Sheets',
            'Approval': 'Always ask',
            'Notifications': 'SMS',
            'Business hours only': 'Yes',
        })
