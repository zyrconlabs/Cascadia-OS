"""tests/test_vault_encryption.py — Vault AES-256-GCM at-rest encryption tests."""
from __future__ import annotations

import base64
import os
import secrets
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from cascadia.memory.vault import VaultStore


def _make_store(key: bytes = None) -> tuple[VaultStore, str]:
    """Return (VaultStore, db_path) with a temp db and a fixed or random key."""
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    key_b64 = base64.b64encode(key or secrets.token_bytes(32)).decode()
    with patch.dict(os.environ, {'VAULT_ENCRYPTION_KEY': key_b64}):
        store = VaultStore(tmp.name)
    return store, tmp.name


def _insert_plaintext(db_path: str, key: str, value_json: str, namespace: str = 'default') -> None:
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            'INSERT INTO vault (key, namespace, value, created_by, created_at, updated_at) VALUES (?,?,?,?,?,?)',
            (key, namespace, value_json, 'test', now, now),
        )


class TestVaultEncryption(unittest.TestCase):

    def test_write_encrypts_value(self):
        store, db_path = _make_store()
        store.write('mykey', 'plaintext value', created_by='test')
        with sqlite3.connect(db_path) as conn:
            row = conn.execute('SELECT value FROM vault WHERE key=?', ('mykey',)).fetchone()
        self.assertIsNotNone(row)
        self.assertNotEqual(row[0], 'plaintext value')
        self.assertNotEqual(row[0], '"plaintext value"')

    def test_read_decrypts_value(self):
        key = secrets.token_bytes(32)
        store, _ = _make_store(key=key)
        store.write('roundtrip', 'hello encrypted world', created_by='test')
        result = store.read('roundtrip')
        self.assertEqual(result, 'hello encrypted world')

    def test_backward_compat_plaintext(self):
        key = secrets.token_bytes(32)
        store, db_path = _make_store(key=key)
        _insert_plaintext(db_path, 'legacy_key', '"legacy_value"')
        result = store.read('legacy_key')
        self.assertEqual(result, 'legacy_value')

    def test_migrate_to_encrypted(self):
        key = secrets.token_bytes(32)
        store, db_path = _make_store(key=key)
        _insert_plaintext(db_path, 'migrate_key', '"migrate_value"')
        count = store.migrate_to_encrypted()
        self.assertEqual(count, 1)
        with sqlite3.connect(db_path) as conn:
            row = conn.execute('SELECT value FROM vault WHERE key=?', ('migrate_key',)).fetchone()
        self.assertNotEqual(row[0], '"migrate_value"')
        result = store.read('migrate_key')
        self.assertEqual(result, 'migrate_value')

    def test_key_from_env(self):
        key = secrets.token_bytes(32)
        key_b64 = base64.b64encode(key).decode()
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
            db_path = tmp.name
        with patch.dict(os.environ, {'VAULT_ENCRYPTION_KEY': key_b64}):
            store = VaultStore(db_path)
            store.write('envkey', {'nested': 'data', 'count': 42}, created_by='test')
            result = store.read('envkey')
        self.assertEqual(result, {'nested': 'data', 'count': 42})

    def test_no_key_passthrough(self):
        """Without VAULT_ENCRYPTION_KEY, values are stored and read as plaintext JSON."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
            db_path = tmp.name
        env = {k: v for k, v in os.environ.items() if k != 'VAULT_ENCRYPTION_KEY'}
        with patch.dict(os.environ, env, clear=True):
            store = VaultStore(db_path)
            store.write('plainkey', 'no encryption here', created_by='test')
            result = store.read('plainkey')
        self.assertEqual(result, 'no encryption here')
        with sqlite3.connect(db_path) as conn:
            row = conn.execute('SELECT value FROM vault WHERE key=?', ('plainkey',)).fetchone()
        self.assertEqual(row[0], '"no encryption here"')


if __name__ == '__main__':
    unittest.main()
