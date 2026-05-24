"""
tests/test_crew_operator_limits.py

Unit tests for _check_operator_limit() and operator limit enforcement
inside install_operator().

Tier limits verified:
    lite       → 2
    pro        → 6
    business   → 12
    enterprise → 999

HTTP 403 operator_limit_reached with upgrade_url when install would
exceed the tier cap.

Reinstall behaviour: an existing operator_id does not consume a new slot
(upgrade is always allowed, even at the cap).
"""
from __future__ import annotations

import base64
import io
import json
import zipfile
from unittest.mock import MagicMock, patch

import pytest

from cascadia.depot.kill_switch import NoopKillSwitchProvider
from cascadia.depot.signing import Verifier
from cascadia.registry import crew as crew_module
from cascadia.registry.crew import CrewService, _check_operator_limit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _entitlement(tier: str, max_ops: int) -> dict:
    return {
        "tier": tier,
        "features": {},
        "limits": {"max_operators": max_ops},
    }


def _mock_entitlement(tier: str, max_ops: int):
    """Patch _get_entitlement to return a fixed profile."""
    return patch(
        "cascadia.registry.crew._get_entitlement",
        return_value=_entitlement(tier, max_ops),
    )


def _registry_with_n(n: int) -> dict:
    """Return a registry dict pre-populated with n dummy operators."""
    return {
        "version": "0.44",
        "operators": [{"id": f"op_{i}", "name": f"Op {i}"} for i in range(n)],
    }


def _make_op_zip(manifest: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
    return buf.getvalue()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


_VALID_MANIFEST = {
    "operator_id": "new_op",
    "name": "New Operator",
    "version": "1.0.0",
    "capabilities": ["test.read"],
    "autonomy_level": "assistive",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cfg(tmp_path) -> dict:
    """Config dict that points at a temp registry.json."""
    return {"operators_registry_path": str(tmp_path / "registry.json")}


@pytest.fixture
def crew_svc(tmp_path, monkeypatch) -> CrewService:
    """Minimal CrewService wired to tmp_path with no live services."""
    monkeypatch.setattr(crew_module, "_OPERATORS_DIR", tmp_path / "operators")
    mock_runtime = MagicMock()
    mock_runtime.port = 5100
    mock_runtime.logger = MagicMock()
    svc = CrewService.__new__(CrewService)
    svc.registry = {}
    svc.runtime = mock_runtime
    svc._config = {"operators_registry_path": str(tmp_path / "registry.json")}
    svc._kill_switch = NoopKillSwitchProvider()
    svc._verifier = Verifier.from_bundle({})
    return svc


# ---------------------------------------------------------------------------
# _check_operator_limit — parametrized limit values
# ---------------------------------------------------------------------------

class TestCheckOperatorLimitValues:
    """Each tier blocks exactly at its documented limit."""

    @pytest.mark.parametrize("tier,limit", [
        ("lite",        2),
        ("pro",         6),
        ("business",   12),
        ("enterprise", 999),
    ])
    def test_one_below_limit_is_allowed(self, tmp_path, cfg, tier, limit):
        """(limit - 1) operators installed → new install allowed."""
        (tmp_path / "registry.json").write_text(
            json.dumps(_registry_with_n(limit - 1))
        )
        with _mock_entitlement(tier, limit):
            ok, msg = _check_operator_limit(cfg)
        assert ok is True, f"{tier}: expected allowed at {limit - 1} operators"
        assert msg == ""

    @pytest.mark.parametrize("tier,limit", [
        ("lite",        2),
        ("pro",         6),
        ("business",   12),
        ("enterprise", 999),
    ])
    def test_at_limit_is_blocked(self, tmp_path, cfg, tier, limit):
        """limit operators installed → next install blocked."""
        (tmp_path / "registry.json").write_text(
            json.dumps(_registry_with_n(limit))
        )
        with _mock_entitlement(tier, limit):
            ok, msg = _check_operator_limit(cfg)
        assert ok is False, f"{tier}: expected blocked at {limit} operators"
        assert "operator_limit_reached" in msg

    # Explicit spot checks ---------------------------------------------------

    def test_lite_exact_boundary(self, tmp_path, cfg):
        """Lite: 1 op → allowed; 2 ops → blocked."""
        (tmp_path / "registry.json").write_text(
            json.dumps(_registry_with_n(1))
        )
        with _mock_entitlement("lite", 2):
            ok, _ = _check_operator_limit(cfg)
        assert ok is True

        (tmp_path / "registry.json").write_text(
            json.dumps(_registry_with_n(2))
        )
        with _mock_entitlement("lite", 2):
            ok, msg = _check_operator_limit(cfg)
        assert ok is False
        assert "operator_limit_reached" in msg

    def test_pro_exact_boundary(self, tmp_path, cfg):
        """Pro: 5 ops → allowed; 6 ops → blocked."""
        (tmp_path / "registry.json").write_text(
            json.dumps(_registry_with_n(5))
        )
        with _mock_entitlement("pro", 6):
            ok, _ = _check_operator_limit(cfg)
        assert ok is True

        (tmp_path / "registry.json").write_text(
            json.dumps(_registry_with_n(6))
        )
        with _mock_entitlement("pro", 6):
            ok, msg = _check_operator_limit(cfg)
        assert ok is False
        assert "operator_limit_reached" in msg

    def test_business_exact_boundary(self, tmp_path, cfg):
        """Business: 11 ops → allowed; 12 ops → blocked."""
        (tmp_path / "registry.json").write_text(
            json.dumps(_registry_with_n(11))
        )
        with _mock_entitlement("business", 12):
            ok, _ = _check_operator_limit(cfg)
        assert ok is True

        (tmp_path / "registry.json").write_text(
            json.dumps(_registry_with_n(12))
        )
        with _mock_entitlement("business", 12):
            ok, msg = _check_operator_limit(cfg)
        assert ok is False

    def test_enterprise_exact_boundary(self, tmp_path, cfg):
        """Enterprise: 998 ops → allowed; 999 ops → blocked."""
        (tmp_path / "registry.json").write_text(
            json.dumps(_registry_with_n(998))
        )
        with _mock_entitlement("enterprise", 999):
            ok, _ = _check_operator_limit(cfg)
        assert ok is True

        (tmp_path / "registry.json").write_text(
            json.dumps(_registry_with_n(999))
        )
        with _mock_entitlement("enterprise", 999):
            ok, msg = _check_operator_limit(cfg)
        assert ok is False
        assert "operator_limit_reached" in msg

    def test_empty_registry_always_allowed(self, tmp_path, cfg):
        """Empty registry is always under any limit."""
        (tmp_path / "registry.json").write_text(
            json.dumps(_registry_with_n(0))
        )
        with _mock_entitlement("lite", 2):
            ok, msg = _check_operator_limit(cfg)
        assert ok is True
        assert msg == ""


# ---------------------------------------------------------------------------
# _check_operator_limit — reinstall bypass
# ---------------------------------------------------------------------------

class TestCheckOperatorLimitReinstall:
    """Reinstalling an existing operator must never be blocked by the limit."""

    def test_reinstall_bypasses_limit(self, tmp_path, cfg):
        """op_id already in registry → allowed even when current == limit."""
        reg = {
            "version": "0.44",
            "operators": [
                {"id": "op_0", "name": "Op 0"},
                {"id": "op_1", "name": "Op 1"},   # at lite limit (2)
            ],
        }
        (tmp_path / "registry.json").write_text(json.dumps(reg))

        with _mock_entitlement("lite", 2):
            ok, msg = _check_operator_limit(cfg, op_id="op_0")

        assert ok is True, "reinstall must be allowed even when at the tier cap"
        assert msg == ""

    def test_new_op_still_blocked_at_limit(self, tmp_path, cfg):
        """A brand-new op_id is still blocked when at the limit."""
        (tmp_path / "registry.json").write_text(
            json.dumps(_registry_with_n(2))
        )
        with _mock_entitlement("lite", 2):
            ok, msg = _check_operator_limit(cfg, op_id="brand_new_op")

        assert ok is False
        assert "operator_limit_reached" in msg

    def test_reinstall_with_no_op_id_uses_count(self, tmp_path, cfg):
        """When op_id='' (default), full count check applies."""
        (tmp_path / "registry.json").write_text(
            json.dumps(_registry_with_n(2))
        )
        with _mock_entitlement("lite", 2):
            ok, msg = _check_operator_limit(cfg)   # no op_id

        assert ok is False
        assert "operator_limit_reached" in msg


# ---------------------------------------------------------------------------
# install_operator — HTTP 403 enforcement
# ---------------------------------------------------------------------------

class TestInstallOperatorLimitEnforcement:
    """install_operator must return 403 with the correct payload when capped."""

    def test_403_at_lite_cap(self, crew_svc, tmp_path):
        """Lite tier at 2 operators: third install → 403."""
        (tmp_path / "registry.json").write_text(
            json.dumps(_registry_with_n(2))
        )
        with _mock_entitlement("lite", 2):
            status, body = crew_svc.install_operator(
                {"zip_b64": _b64(_make_op_zip(_VALID_MANIFEST))}
            )
        assert status == 403
        assert body["error"] == "operator_limit_reached"
        assert "reason" in body
        assert "upgrade_url" in body
        assert body["upgrade_url"] == "https://zyrcon.store"

    def test_403_reason_contains_limit_info(self, crew_svc, tmp_path):
        """reason string must mention operator_limit_reached."""
        (tmp_path / "registry.json").write_text(
            json.dumps(_registry_with_n(2))
        )
        with _mock_entitlement("lite", 2):
            status, body = crew_svc.install_operator(
                {"zip_b64": _b64(_make_op_zip(_VALID_MANIFEST))}
            )
        assert status == 403
        assert "operator_limit_reached" in body["reason"]

    def test_403_at_pro_cap(self, crew_svc, tmp_path):
        """Pro tier at 6 operators: seventh install → 403."""
        (tmp_path / "registry.json").write_text(
            json.dumps(_registry_with_n(6))
        )
        with _mock_entitlement("pro", 6):
            status, body = crew_svc.install_operator(
                {"zip_b64": _b64(_make_op_zip(_VALID_MANIFEST))}
            )
        assert status == 403
        assert body["error"] == "operator_limit_reached"

    def test_201_under_pro_cap(self, crew_svc, tmp_path):
        """Pro tier at 5 operators: sixth install → 201."""
        (tmp_path / "registry.json").write_text(
            json.dumps(_registry_with_n(5))
        )
        with _mock_entitlement("pro", 6):
            status, body = crew_svc.install_operator(
                {"zip_b64": _b64(_make_op_zip(_VALID_MANIFEST))}
            )
        assert status == 201
        assert body["installed"] == "new_op"

    def test_403_at_business_cap(self, crew_svc, tmp_path):
        """Business tier at 12 operators: thirteenth install → 403."""
        (tmp_path / "registry.json").write_text(
            json.dumps(_registry_with_n(12))
        )
        with _mock_entitlement("business", 12):
            status, body = crew_svc.install_operator(
                {"zip_b64": _b64(_make_op_zip(_VALID_MANIFEST))}
            )
        assert status == 403
        assert body["error"] == "operator_limit_reached"

    def test_reinstall_at_lite_cap_returns_201(self, crew_svc, tmp_path):
        """Reinstalling an existing op at the lite cap → 201, not 403."""
        reinstall_manifest = {
            **_VALID_MANIFEST,
            "operator_id": "op_0",
            "version": "2.0.0",
        }
        reg = {
            "version": "0.44",
            "operators": [
                {"id": "op_0", "name": "Op 0"},
                {"id": "op_1", "name": "Op 1"},
            ],
        }
        (tmp_path / "registry.json").write_text(json.dumps(reg))

        with _mock_entitlement("lite", 2):
            status, body = crew_svc.install_operator(
                {"zip_b64": _b64(_make_op_zip(reinstall_manifest))}
            )
        assert status == 201, (
            f"Expected 201 for reinstall at cap, got {status}: {body}"
        )
        assert body["installed"] == "op_0"

    def test_dry_run_still_enforces_limit(self, crew_svc, tmp_path):
        """dry_run=True must also report the limit violation (no false ok)."""
        (tmp_path / "registry.json").write_text(
            json.dumps(_registry_with_n(2))
        )
        with _mock_entitlement("lite", 2):
            status, body = crew_svc.install_operator(
                {
                    "zip_b64": _b64(_make_op_zip(_VALID_MANIFEST)),
                    "dry_run": True,
                }
            )
        assert status == 403
        assert body["error"] == "operator_limit_reached"
