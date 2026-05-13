"""Tests for cascadia.depot.canonicalization — all 7 public functions."""
from __future__ import annotations

import hashlib
import json
import unittest

from cascadia.depot.canonicalization import (
    canonical_file_bytes,
    canonical_manifest_bytes,
    compute_package_digest,
    file_sha256,
    is_text_file,
    normalize_line_endings,
    normalize_path,
)


class TestNormalizePath(unittest.TestCase):

    def test_simple_path(self):
        self.assertEqual(normalize_path("workflows/main.json"), "workflows/main.json")

    def test_backslash_converted(self):
        self.assertEqual(normalize_path("workflows\\main.json"), "workflows/main.json")

    def test_leading_dot_stripped(self):
        self.assertEqual(normalize_path("./workflows/main.json"), "workflows/main.json")

    def test_double_slash_collapsed(self):
        self.assertEqual(normalize_path("workflows//main.json"), "workflows/main.json")

    def test_trailing_slash_stripped(self):
        self.assertEqual(normalize_path("workflows/"), "workflows")

    def test_dot_segment_removed(self):
        self.assertEqual(normalize_path("a/./b.json"), "a/b.json")

    def test_depth_8_allowed(self):
        # 8 separators = 9 parts = 8 directory levels — exactly at the limit.
        path = "/".join([f"d{i}" for i in range(8)] + ["file.json"])
        result = normalize_path(path)
        self.assertEqual(result.count("/"), 8)

    def test_depth_exceeds_8_raises(self):
        # 9 separators = 10 parts — one beyond the 8-level limit.
        path = "/".join([f"d{i}" for i in range(9)] + ["file.json"])
        with self.assertRaises(ValueError):
            normalize_path(path)

    def test_dotdot_stripped_not_navigated(self):
        # '..' is stripped from the parts list but does NOT act as directory
        # navigation. Callers reject packages with '..' before normalization.
        result = normalize_path("a/../b.json")
        self.assertEqual(result, "a/b.json")

    def test_empty_after_normalization_raises(self):
        with self.assertRaises(ValueError):
            normalize_path("./")

    def test_absolute_path_leading_slash_stripped(self):
        # Leading slash (PurePosixPath root sentinel) is filtered out.
        result = normalize_path("/absolute/path.json")
        self.assertEqual(result, "absolute/path.json")


class TestIsTextFile(unittest.TestCase):

    def test_json_is_text(self):
        self.assertTrue(is_text_file("mission.json"))

    def test_md_is_text(self):
        self.assertTrue(is_text_file("templates/quote.md"))

    def test_py_is_text(self):
        self.assertTrue(is_text_file("scripts/setup.py"))

    def test_sh_is_text(self):
        self.assertTrue(is_text_file("install.sh"))

    def test_sql_is_text(self):
        self.assertTrue(is_text_file("data/schema.sql"))

    def test_png_is_binary(self):
        self.assertFalse(is_text_file("icon.png"))

    def test_jpg_is_binary(self):
        self.assertFalse(is_text_file("photo.jpg"))

    def test_db_is_binary(self):
        self.assertFalse(is_text_file("data.db"))

    def test_unknown_extension_is_binary(self):
        self.assertFalse(is_text_file("data.xyz"))

    def test_no_extension_is_binary(self):
        self.assertFalse(is_text_file("Makefile"))

    def test_case_insensitive_extension(self):
        self.assertTrue(is_text_file("file.JSON"))

    def test_yaml_is_text(self):
        self.assertTrue(is_text_file("config.yaml"))

    def test_yml_is_text(self):
        self.assertTrue(is_text_file("config.yml"))


class TestNormalizeLineEndings(unittest.TestCase):

    def test_crlf_to_lf(self):
        self.assertEqual(normalize_line_endings(b"hello\r\nworld\r\n"), b"hello\nworld\n")

    def test_bare_cr_to_lf(self):
        self.assertEqual(normalize_line_endings(b"hello\rworld\r"), b"hello\nworld\n")

    def test_lf_unchanged(self):
        data = b"hello\nworld\n"
        self.assertEqual(normalize_line_endings(data), data)

    def test_mixed_line_endings(self):
        self.assertEqual(
            normalize_line_endings(b"a\r\nb\rc\n"),
            b"a\nb\nc\n"
        )

    def test_empty_bytes(self):
        self.assertEqual(normalize_line_endings(b""), b"")

    def test_no_line_endings(self):
        self.assertEqual(normalize_line_endings(b"hello"), b"hello")


class TestCanonicalFileBytes(unittest.TestCase):

    def test_text_file_lf_normalized(self):
        result = canonical_file_bytes("template.md", b"hello\r\nworld\r\n")
        self.assertEqual(result, b"hello\nworld\n")

    def test_binary_file_unchanged(self):
        raw = b"\xff\xfe\r\nsome binary\r\n"
        self.assertEqual(canonical_file_bytes("image.png", raw), raw)

    def test_json_file_normalized(self):
        result = canonical_file_bytes("data.json", b'{"key":"val"}\r\n')
        self.assertEqual(result, b'{"key":"val"}\n')


class TestFileSha256(unittest.TestCase):

    def test_text_file_lf_normalized_before_hash(self):
        content_with_crlf = b"Hello, {name}!\r\n"
        content_with_lf = b"Hello, {name}!\n"
        result = file_sha256("template.md", content_with_crlf)
        expected = hashlib.sha256(content_with_lf).hexdigest()
        self.assertEqual(result, expected)

    def test_binary_file_hashed_raw(self):
        raw = b"\x00\x01\x02\x03"
        result = file_sha256("data.bin", raw)
        self.assertEqual(result, hashlib.sha256(raw).hexdigest())

    # Spec test vector — Vector 1
    def test_vector1_quote_md(self):
        raw = b"Hello, {name}!\r\n"
        result = file_sha256("templates/quote.md", raw)
        self.assertEqual(result, "1e86d5b60d7dc623fef1b8cf2b847c6e4c9b47fbcd00a3b0eadcce51c54df492")

    # Worked example file hashes
    def test_worked_example_quote_md(self):
        raw = b"Dear {{contact_name}},\r\n\r\nPlease find the proposal attached.\r\n"
        result = file_sha256("templates/quote.md", raw)
        self.assertEqual(result, "2e7bd21b199f1c1c852eb1b041a2b7cd4f22bfa702810c6a7cbfc3537fb30872")

    def test_worked_example_main_json(self):
        raw = b'{\n  "steps": [\n    {"id": "step_scout", "type": "operator", "operator": "scout"}\n  ]\n}\n'
        result = file_sha256("workflows/main.json", raw)
        self.assertEqual(result, "a0dbc5f4c21d1c208946c7dca485de5cff8ec1bcc8e2ce377b92e5f4816d7908")


class TestComputePackageDigest(unittest.TestCase):

    def test_empty_file_map(self):
        result = compute_package_digest({})
        expected = "sha256:" + hashlib.sha256(b"").hexdigest()
        self.assertEqual(result, expected)

    def test_result_has_sha256_prefix(self):
        result = compute_package_digest({"a.json": b"content"})
        self.assertTrue(result.startswith("sha256:"))

    def test_hex_part_is_64_chars(self):
        result = compute_package_digest({"a.json": b"content"})
        self.assertEqual(len(result), len("sha256:") + 64)

    def test_order_is_lexicographic(self):
        m1 = compute_package_digest({"a.json": b"aaa", "b.json": b"bbb"})
        m2 = compute_package_digest({"b.json": b"bbb", "a.json": b"aaa"})
        self.assertEqual(m1, m2)

    def test_different_contents_different_digest(self):
        m1 = compute_package_digest({"a.json": b"aaa"})
        m2 = compute_package_digest({"a.json": b"bbb"})
        self.assertNotEqual(m1, m2)

    def test_different_paths_different_digest(self):
        m1 = compute_package_digest({"a.json": b"same"})
        m2 = compute_package_digest({"b.json": b"same"})
        self.assertNotEqual(m1, m2)

    # Spec test vector — Vector 2
    def test_vector2_two_files(self):
        file_map = {
            "templates/quote.md": b"Hello, {name}!\n",
            "workflows/main.json": b'{"steps":[]}\n',
        }
        result = compute_package_digest(file_map)
        self.assertEqual(result, "sha256:7084c017325aee7e0ef448a47413a9f2066795ef6955fab1c97f4c6b21aa6924")

    # Worked example package digest
    def test_worked_example_package_digest(self):
        quote_canonical = b"Dear {{contact_name}},\n\nPlease find the proposal attached.\n"
        main_canonical = b'{\n  "steps": [\n    {"id": "step_scout", "type": "operator", "operator": "scout"}\n  ]\n}\n'
        file_map = {
            "templates/quote.md": quote_canonical,
            "workflows/main.json": main_canonical,
        }
        result = compute_package_digest(file_map)
        self.assertEqual(result, "sha256:48144dde1a45c9bd553bdb855185608c263918f314913f0b070583ef7f7f3305")


class TestCanonicalManifestBytes(unittest.TestCase):

    def test_signature_field_excluded(self):
        m = {"id": "test", "signature": "AAAA", "type": "mission"}
        result = canonical_manifest_bytes(m)
        parsed = json.loads(result)
        self.assertNotIn("signature", parsed)

    def test_keys_sorted(self):
        m = {"z": 1, "a": 2, "m": 3}
        result = canonical_manifest_bytes(m)
        parsed = json.loads(result.decode())
        self.assertEqual(list(parsed.keys()), sorted(parsed.keys()))

    def test_no_spaces_in_output(self):
        m = {"id": "test", "type": "mission"}
        result = canonical_manifest_bytes(m)
        self.assertNotIn(b" ", result)

    def test_no_trailing_newline(self):
        m = {"id": "test"}
        result = canonical_manifest_bytes(m)
        self.assertFalse(result.endswith(b"\n"))

    def test_utf8_encoded(self):
        m = {"name": "Étoile"}
        result = canonical_manifest_bytes(m)
        self.assertIn("Étoile".encode("utf-8"), result)

    def test_no_signature_field_in_manifest_is_ok(self):
        m = {"id": "test", "type": "mission"}
        result = canonical_manifest_bytes(m)
        self.assertIsInstance(result, bytes)

    def test_signed_by_and_key_id_remain(self):
        m = {
            "id": "test",
            "signed_by": "zyrcon-labs",
            "key_id": "zyrcon-2026-q2",
            "signature": "AAAA",
        }
        result = canonical_manifest_bytes(m)
        parsed = json.loads(result)
        self.assertIn("signed_by", parsed)
        self.assertIn("key_id", parsed)
        self.assertNotIn("signature", parsed)

    # Spec test vector — Vector 3
    def test_vector3_canonical_manifest(self):
        m = {
            "version": "1.0.0",
            "id": "example_mission",
            "type": "mission",
            "signature": "AAAA...",
            "name": "Example",
        }
        result = canonical_manifest_bytes(m)
        expected = b'{"id":"example_mission","name":"Example","type":"mission","version":"1.0.0"}'
        self.assertEqual(result, expected)

    # Worked example canonical manifest SHA-256
    def test_worked_example_canonical_manifest_hash(self):
        manifest = {
            "type": "mission", "id": "lead_qualification", "version": "1.0.0",
            "name": "Lead Qualification Pipeline", "description": "Qualifies inbound leads.",
            "tier_required": "pro", "runtime": "server", "author": "zyrcon-labs",
            "signed_by": "zyrcon-labs", "signature_algorithm": "Ed25519",
            "key_id": "zyrcon-2026-q2",
            "package_digest": "sha256:48144dde1a45c9bd553bdb855185608c263918f314913f0b070583ef7f7f3305",
            "files": [
                {"path": "templates/quote.md",
                 "sha256": "2e7bd21b199f1c1c852eb1b041a2b7cd4f22bfa702810c6a7cbfc3537fb30872",
                 "size_bytes": 59},
                {"path": "workflows/main.json",
                 "sha256": "a0dbc5f4c21d1c208946c7dca485de5cff8ec1bcc8e2ce377b92e5f4816d7908",
                 "size_bytes": 87},
            ],
            "capabilities": ["crm.read", "email.send"],
            "requires_approval": ["email.send"],
            "risk_level": "medium",
            "operators": {"required": ["scout"], "optional": []},
            "connectors": {"required": [], "optional": []},
            "workflows": {"main": "workflows/main.json"},
        }
        canon = canonical_manifest_bytes(manifest)
        result_hash = hashlib.sha256(canon).hexdigest()
        self.assertEqual(result_hash, "f754c2961ecff1388143bbbad941bdf398ea7985d7dae92d003b6d7ebaef3039")


class TestEndToEndWorkedExample(unittest.TestCase):
    """Reproduces the complete worked example from package-canonicalization.md."""

    def test_full_worked_example(self):
        quote_raw = b"Dear {{contact_name}},\r\n\r\nPlease find the proposal attached.\r\n"
        quote_canonical = normalize_line_endings(quote_raw)
        main_canonical = b'{\n  "steps": [\n    {"id": "step_scout", "type": "operator", "operator": "scout"}\n  ]\n}\n'

        self.assertEqual(len(quote_canonical), 59)
        self.assertEqual(len(main_canonical), 87)

        self.assertEqual(
            hashlib.sha256(quote_canonical).hexdigest(),
            "2e7bd21b199f1c1c852eb1b041a2b7cd4f22bfa702810c6a7cbfc3537fb30872",
        )
        self.assertEqual(
            hashlib.sha256(main_canonical).hexdigest(),
            "a0dbc5f4c21d1c208946c7dca485de5cff8ec1bcc8e2ce377b92e5f4816d7908",
        )

        file_map = {"templates/quote.md": quote_canonical, "workflows/main.json": main_canonical}
        pkg_digest = compute_package_digest(file_map)
        self.assertEqual(pkg_digest, "sha256:48144dde1a45c9bd553bdb855185608c263918f314913f0b070583ef7f7f3305")

        manifest = {
            "type": "mission", "id": "lead_qualification", "version": "1.0.0",
            "name": "Lead Qualification Pipeline", "description": "Qualifies inbound leads.",
            "tier_required": "pro", "runtime": "server", "author": "zyrcon-labs",
            "signed_by": "zyrcon-labs", "signature_algorithm": "Ed25519",
            "key_id": "zyrcon-2026-q2", "package_digest": pkg_digest,
            "files": [
                {"path": "templates/quote.md",
                 "sha256": hashlib.sha256(quote_canonical).hexdigest(), "size_bytes": 59},
                {"path": "workflows/main.json",
                 "sha256": hashlib.sha256(main_canonical).hexdigest(), "size_bytes": 87},
            ],
            "capabilities": ["crm.read", "email.send"],
            "requires_approval": ["email.send"],
            "risk_level": "medium",
            "operators": {"required": ["scout"], "optional": []},
            "connectors": {"required": [], "optional": []},
            "workflows": {"main": "workflows/main.json"},
        }
        canon = canonical_manifest_bytes(manifest)
        self.assertEqual(
            hashlib.sha256(canon).hexdigest(),
            "f754c2961ecff1388143bbbad941bdf398ea7985d7dae92d003b6d7ebaef3039",
        )


if __name__ == "__main__":
    unittest.main()
