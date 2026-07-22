"""
test_license_ed25519.py — STAGE 2 ACCEPTANCE CRITERIA (written before the rewrite).

These tests define what the ed25519 (v3) license validator MUST do. They are
expected to FAIL until Stage 2 lands the v3 implementation in
cascadia/licensing/tier_validator.py — that failure is the point: the
invariants are pinned down before any code can quietly violate them.

WHY THIS EXISTS
    Licenses are currently symmetric HMAC: the secret that VERIFIES a key is
    the same secret that SIGNS one, so a secret cannot safely ship to a
    customer. v3 moves to Ed25519 — the customer receives only a PUBLIC key and
    can verify but cannot forge.

THE INVARIANT THAT MATTERS MOST
    A validator that cannot verify must DENY, never allow. If the public key is
    missing, empty, malformed, or simply wrong, the answer is "not valid" — not
    "valid" and not an uncaught exception. Tests 1, 2 and 4 exist solely to
    stop a fail-open regression, which would accept every forged key silently.

FORMAT CONTRACT (v3)
    key:     zyrcon_{tier}_{customer_id}_{expiry_epoch}_v3_{sig_hex}
    signed:  b"zyrcon_{tier}_{customer_id}_{expiry_epoch}_v3"   (the key minus
             the trailing _{sig})
    sig:     Ed25519 over that message, HEX-encoded (128 chars, [0-9a-f]).

    ⚠ HEX IS NOT COSMETIC. The key parser splits on "_" and reads positionally
    from the end. base64url — the encoding depot/signing.py uses — includes "_"
    in its alphabet, so a base64url signature would corrupt the split for
    whichever signatures happen to contain that character: an intermittent,
    data-dependent parse failure. Test 7 pins hex so this cannot regress.

STAGE 2 DESIGN NOTE (open question, decide when implementing)
    The v3 format carries no key_id, so the validator should try every public
    key in cascadia/licensing/zyrcon_license_keys.json and accept if any
    verifies. That keeps the key string identical in shape to v2 and makes key
    rotation additive. If a key_id field is added instead, update test 7.
"""
from __future__ import annotations

import base64
import time
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

LICENSING_KEY_PATH = Path.home() / ".config" / "zyrcon" / "licensing.key"
BUNDLE_PATH = Path(__file__).resolve().parents[1] / "cascadia" / "licensing" / "zyrcon_license_keys.json"
LICENSING_KEY_ID = "zyrcon-licensing-2026-q3"

# Far-future / long-past expiries so the suite never becomes time-flaky.
FAR_FUTURE = int(time.time()) + 365 * 24 * 3600
LONG_PAST = int(time.time()) - 30 * 24 * 3600


def _mint_v3(tier: str, customer_id: str, expiry: int,
             private_key: Ed25519PrivateKey) -> str:
    """VENDOR-SIDE minting, reproduced here so the tests are self-contained.

    Stage 2 must produce byte-identical output from the real signer. This is
    deliberately the only place in the test suite that touches a private key.
    """
    message = f"zyrcon_{tier}_{customer_id}_{expiry}_v3".encode()
    sig_hex = private_key.sign(message).hex()
    return f"zyrcon_{tier}_{customer_id}_{expiry}_v3_{sig_hex}"


def _pub_b64(private_key: Ed25519PrivateKey) -> str:
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    raw = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _validator(public_key_b64):
    """Construct the Stage 2 v3 validator, or fail with a clear reason.

    Kept in one place so an unimplemented Stage 2 reports "not implemented"
    rather than an ImportError cascade across every test.
    """
    try:
        from cascadia.licensing.tier_validator import TierValidator
    except ImportError as exc:  # pragma: no cover
        raise AssertionError(f"STAGE 2 NOT IMPLEMENTED — cannot import TierValidator: {exc}")
    try:
        return TierValidator(public_key_b64, key_version="v3")
    except TypeError as exc:
        raise AssertionError(
            "STAGE 2 NOT IMPLEMENTED — TierValidator does not yet accept an "
            f"Ed25519 public key (still HMAC-secret shaped): {exc}"
        )


class TestV3FailClosed(unittest.TestCase):
    """1, 2, 4 — the validator must DENY whenever it cannot verify."""

    @classmethod
    def setUpClass(cls):
        if not LICENSING_KEY_PATH.exists():
            raise unittest.SkipTest(f"licensing private key absent: {LICENSING_KEY_PATH}")
        cls.priv = Ed25519PrivateKey.from_private_bytes(LICENSING_KEY_PATH.read_bytes())
        cls.pub = _pub_b64(cls.priv)
        cls.key = _mint_v3("enterprise", "acme-corp", FAR_FUTURE, cls.priv)

    def test_1_no_public_key_denies(self):
        """No key material at all → never valid, and no exception escapes.

        Absence of key material is a NORMAL state (a node that has not been
        configured), not an exceptional one, so it must return a clean
        "not valid" rather than propagating an error to the caller.
        """
        for empty in ("", None):
            with self.subTest(public_key=empty):
                try:
                    result = _validator(empty).validate(self.key)
                except AssertionError:
                    raise
                except Exception as exc:
                    self.fail(
                        f"absent public key raised {type(exc).__name__}: {exc} — "
                        "must return not-valid instead of raising"
                    )
                self.assertFalse(result.get("valid"),
                                 "FAIL-OPEN: validated a key with no public key available")

    def test_2_malformed_public_key_denies_without_raising(self):
        """Garbage key material → invalid, and no exception escapes."""
        for bad in ("not-base64!!", "AAAA", "z" * 43):
            with self.subTest(public_key=bad):
                try:
                    result = _validator(bad).validate(self.key)
                except AssertionError:
                    raise
                except Exception as exc:
                    self.fail(f"malformed public key raised {type(exc).__name__}: {exc}")
                self.assertFalse(result.get("valid"),
                                 "FAIL-OPEN: validated against a malformed public key")

    def test_4_wrong_public_key_is_invalid_signature(self):
        """A well-formed but WRONG key → invalid_signature, not valid."""
        other = Ed25519PrivateKey.generate()
        result = _validator(_pub_b64(other)).validate(self.key)
        self.assertFalse(result.get("valid"))
        self.assertEqual(result.get("error"), "invalid_signature")


class TestV3HappyPath(unittest.TestCase):
    """3, 5, 6 — correct keys validate; tampered and expired ones do not."""

    @classmethod
    def setUpClass(cls):
        if not LICENSING_KEY_PATH.exists():
            raise unittest.SkipTest(f"licensing private key absent: {LICENSING_KEY_PATH}")
        cls.priv = Ed25519PrivateKey.from_private_bytes(LICENSING_KEY_PATH.read_bytes())
        cls.pub = _pub_b64(cls.priv)

    def test_3_valid_key_with_correct_public_key(self):
        key = _mint_v3("enterprise", "acme-corp", FAR_FUTURE, self.priv)
        result = _validator(self.pub).validate(key)
        self.assertTrue(result.get("valid"), f"genuine v3 key rejected: {result}")
        self.assertEqual(result.get("tier"), "enterprise")
        self.assertEqual(result.get("customer_id"), "acme-corp")
        self.assertEqual(result.get("expires_at"), FAR_FUTURE)

    def test_5_tampered_tier_is_invalid_signature(self):
        """Upgrade pro → enterprise in the string; signature must not survive."""
        key = _mint_v3("pro", "acme-corp", FAR_FUTURE, self.priv)
        tampered = key.replace("zyrcon_pro_", "zyrcon_enterprise_", 1)
        self.assertNotEqual(key, tampered)
        result = _validator(self.pub).validate(tampered)
        self.assertFalse(result.get("valid"),
                         "FAIL-OPEN: accepted a key whose tier was edited after signing")
        self.assertEqual(result.get("error"), "invalid_signature")

    def test_6_expired_key_is_not_valid(self):
        """Correctly signed but past expiry → not valid."""
        key = _mint_v3("enterprise", "acme-corp", LONG_PAST, self.priv)
        result = _validator(self.pub).validate(key)
        self.assertFalse(result.get("valid"))
        self.assertEqual(result.get("error"), "expired")


class TestV3FormatContract(unittest.TestCase):
    """7 — the hex-signature contract, which protects the parser.

    These assertions hold TODAY against the minting helper, so they guard the
    format decision immediately rather than waiting for Stage 2.
    """

    @classmethod
    def setUpClass(cls):
        if not LICENSING_KEY_PATH.exists():
            raise unittest.SkipTest(f"licensing private key absent: {LICENSING_KEY_PATH}")
        cls.priv = Ed25519PrivateKey.from_private_bytes(LICENSING_KEY_PATH.read_bytes())

    def test_7a_signature_is_128_hex_chars(self):
        key = _mint_v3("enterprise", "acme-corp", FAR_FUTURE, self.priv)
        sig = key.rsplit("_", 1)[-1]
        self.assertEqual(len(sig), 128, "ed25519 sig must be 64 bytes = 128 hex chars")
        self.assertRegex(sig, r"^[0-9a-f]{128}$", "signature must be lowercase hex")

    def test_7b_signature_never_contains_the_delimiter(self):
        """The parser splits on '_'. Hex cannot contain it; base64url can.

        Minting many keys makes the point empirically: hex is always safe,
        whereas base64url would eventually produce a '_' and corrupt the split.
        """
        base64url_would_have_collided = False
        for i in range(200):
            key = _mint_v3("enterprise", f"cust{i}", FAR_FUTURE, self.priv)
            sig = key.rsplit("_", 1)[-1]
            self.assertNotIn("_", sig, "hex signature must never contain the delimiter")
            message = f"zyrcon_enterprise_cust{i}_{FAR_FUTURE}_v3".encode()
            b64 = base64.urlsafe_b64encode(self.priv.sign(message)).rstrip(b"=").decode()
            if "_" in b64 or "-" in b64:
                base64url_would_have_collided = True
        self.assertTrue(
            base64url_would_have_collided,
            "expected base64url to collide with the parser at least once in 200 keys "
            "— this is the reason v3 uses hex",
        )

    def test_7c_parser_round_trip_positional_split(self):
        """A v3 key must split exactly as the existing parser expects."""
        key = _mint_v3("pro_workspace", "acme_corp_intl", FAR_FUTURE, self.priv)
        remainder = key[len("zyrcon_pro_workspace_"):].split("_")
        self.assertEqual(remainder[-1], key.rsplit("_", 1)[-1])   # sig
        self.assertEqual(remainder[-2], "v3")                      # version tag
        self.assertEqual(remainder[-3], str(FAR_FUTURE))           # expiry
        self.assertEqual("_".join(remainder[:-3]), "acme_corp_intl")  # underscored id


if __name__ == "__main__":
    unittest.main(verbosity=2)
