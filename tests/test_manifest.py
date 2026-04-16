from __future__ import annotations

import unittest
from pathlib import Path

from cascadia.shared.manifest_schema import load_manifest


class ManifestTests(unittest.TestCase):
    def test_three_manifests_validate(self) -> None:
        base = Path('cascadia/operators')
        for name in ('main_operator.json', 'gmail_operator.json', 'calendar_operator.json'):
            manifest = load_manifest(base / name)
            self.assertTrue(manifest.id)
            self.assertTrue(manifest.name)

if __name__ == '__main__':
    unittest.main()
