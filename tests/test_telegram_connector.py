"""Tests for cascadia/connectors/telegram/connector.py."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch, call


def _make_update(update_id: int, chat_id: int, text: str) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "chat": {"id": chat_id},
            "text": text,
        },
    }


class TestSaveOwnerChatId:
    """_save_owner_chat_id — first /start saves; subsequent /start is a no-op."""

    def _run(self, vault_has_existing: bool, chat_id: int = 99001) -> dict:
        """
        Run _save_owner_chat_id with a fake VaultStore and stubbed HTTP calls.
        Returns a spy dict:  {vault_written, prism_posted, reply_sent}
        """
        import cascadia.connectors.telegram.connector as mod

        store = MagicMock()
        store.read.return_value = "existing" if vault_has_existing else None

        spy = {"vault_written": False, "prism_posted": False, "reply_sent": False}

        def fake_write(key, value, created_by, namespace):
            spy["vault_written"] = True

        store.write.side_effect = fake_write

        def fake_urlopen(req, timeout=None):
            if "/api/config/connector/telegram/save" in req.full_url:
                spy["prism_posted"] = True
            resp = MagicMock()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            resp.read.return_value = b'{"ok": true}'
            return resp

        def fake_send_message(cid, text, token):
            spy["reply_sent"] = True
            return {"ok": True}

        with (
            patch.object(mod, "_vault_store", return_value=store),
            patch.object(mod.urllib.request, "urlopen", side_effect=fake_urlopen),
            patch.object(mod, "send_message", side_effect=fake_send_message),
            patch.object(mod, "_BOT_TOKEN", "fake-token"),
        ):
            mod._save_owner_chat_id(chat_id)

        return spy

    def test_first_start_saves_to_vault_prism_and_replies(self):
        spy = self._run(vault_has_existing=False)
        assert spy["vault_written"] is True, "vault write must happen on first /start"
        assert spy["prism_posted"] is True, "PRISM config POST must happen on first /start"
        assert spy["reply_sent"] is True, "confirmation reply must be sent on first /start"

    def test_subsequent_start_is_noop(self):
        spy = self._run(vault_has_existing=True)
        assert spy["vault_written"] is False, "vault must NOT be overwritten on repeat /start"
        assert spy["prism_posted"] is False, "PRISM must NOT be called on repeat /start"
        assert spy["reply_sent"] is False, "no reply should be sent on repeat /start"

    def test_start_text_is_not_forwarded_to_vanguard(self):
        """When text == '/start', _forward_to_vanguard must not be called."""
        import cascadia.connectors.telegram.connector as mod

        updates = [_make_update(1001, 99001, "/start")]
        getUpdates_resp = json.dumps({"ok": True, "result": updates}).encode()

        forward_spy = MagicMock()
        store = MagicMock()
        store.read.return_value = None  # first /start

        def fake_urlopen(req, timeout=None):
            resp = MagicMock()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            if "getUpdates" in req.full_url:
                resp.read.return_value = getUpdates_resp
            else:
                resp.read.return_value = b'{"ok": true}'
            return resp

        stop = mod._poll_stop.__class__()

        call_count = 0

        def controlled_wait(interval):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                stop.set()

        stop.wait = controlled_wait
        stop.is_set = lambda: call_count >= 1

        with (
            patch.object(mod, "_BOT_TOKEN", "fake-token"),
            patch.object(mod, "_poll_stop", stop),
            patch.object(mod, "_vault_store", return_value=store),
            patch.object(mod.urllib.request, "urlopen", side_effect=fake_urlopen),
            patch.object(mod, "send_message", return_value={"ok": True}),
            patch.object(mod, "_forward_to_vanguard", forward_spy),
        ):
            mod._poll_updates()

        forward_spy.assert_not_called()
