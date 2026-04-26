"""Tests for install_operator() in CrewService (Task 11 — Sprint v2)."""
import base64
import io
import json
import zipfile
import pytest

from cascadia.registry.crew import CrewService


def _make_zip(manifest: dict, extra_files: dict | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for name, content in (extra_files or {}).items():
            zf.writestr(name, content)
    return buf.getvalue()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


VALID_MANIFEST = {
    "operator_id": "test_op",
    "name": "Test Operator",
    "version": "1.0.0",
    "capabilities": ["email.send", "crm.write"],
    "autonomy_level": "assistive",
}


@pytest.fixture
def crew(tmp_path, monkeypatch):
    # Patch _OPERATORS_DIR so extraction goes to tmp
    import cascadia.registry.crew as crew_module
    monkeypatch.setattr(crew_module, "_OPERATORS_DIR", tmp_path / "operators")
    # Build a minimal fake config so CrewService doesn't blow up
    from unittest.mock import MagicMock, patch
    mock_runtime = MagicMock()
    mock_runtime.port = 5100
    mock_runtime.logger = MagicMock()
    svc = CrewService.__new__(CrewService)
    svc.registry = {}
    svc.runtime = mock_runtime
    return svc


def test_install_valid_operator(crew, tmp_path):
    zdata = _make_zip(VALID_MANIFEST, {"main.py": "print('hello')"})
    status, body = crew.install_operator({"zip_b64": _b64(zdata)})
    assert status == 201
    assert body["installed"] == "test_op"
    assert body["manifest"]["version"] == "1.0.0"
    assert "test_op" in crew.registry


def test_install_missing_zip_b64(crew):
    status, body = crew.install_operator({})
    assert status == 400
    assert "zip_b64" in body["error"]


def test_install_invalid_base64(crew):
    status, body = crew.install_operator({"zip_b64": "not-valid-base64!!!"})
    assert status == 400
    assert "base64" in body["error"]


def test_install_not_a_zip(crew):
    status, body = crew.install_operator({"zip_b64": _b64(b"this is not a zip")})
    assert status == 400
    assert "zip" in body["error"].lower()


def test_install_missing_manifest(crew):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("main.py", "print('no manifest')")
    status, body = crew.install_operator({"zip_b64": _b64(buf.getvalue())})
    assert status == 400
    assert "manifest" in body["error"].lower()


def test_install_invalid_manifest_json(crew):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", "not json {{{")
    status, body = crew.install_operator({"zip_b64": _b64(buf.getvalue())})
    assert status == 400
    assert "json" in body["error"].lower()


def test_install_missing_required_fields(crew):
    bad = {"operator_id": "x", "name": "X"}  # missing version, capabilities
    zdata = _make_zip(bad)
    status, body = crew.install_operator({"zip_b64": _b64(zdata)})
    assert status == 400
    assert "missing" in body["error"].lower()


def test_install_registers_in_registry(crew, tmp_path):
    zdata = _make_zip(VALID_MANIFEST)
    crew.install_operator({"zip_b64": _b64(zdata)})
    assert crew.registry["test_op"]["capabilities"] == ["email.send", "crm.write"]
    assert crew.registry["test_op"]["source"] == "installed"


def test_validate_manifest_helper():
    manifest, err = CrewService._extract_and_validate_manifest(_make_zip(VALID_MANIFEST))
    assert err == ""
    assert manifest["operator_id"] == "test_op"


def test_validate_manifest_capabilities_must_be_list():
    bad = dict(VALID_MANIFEST)
    bad["capabilities"] = "not-a-list"
    _, err = CrewService._extract_and_validate_manifest(_make_zip(bad))
    assert "list" in err
