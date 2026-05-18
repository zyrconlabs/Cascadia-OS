"""
cascadia/depot/operator_install.py
DEPOT-driven operator install module. stdlib only — no requests.

Owns: entitlement-aware operator install from DEPOT catalog.
  1. get_entitlement()       — fetch tier profile from License Gate
  2. get_entitled_catalog()  — filter DEPOT catalog by entitlement
  3. install_from_package()  — download zip from DEPOT, call CREW
  4. install_from_manifest() — manifest-only CREW registration (files already on disk)
  5. install_all_entitled()  — install everything the tier allows

Called by: enterprise/install.sh, enterprise/upgrade.sh, CLI.

Two install modes:
  package_url: CREW downloads and extracts zip (remote install, no operators on disk).
  manifest:    POST manifest only — CREW registers without extracting (files already on disk).
               Used by enterprise installer when operators are already cloned.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

CREW_URL = f"http://127.0.0.1:{os.environ.get('CREW_PORT', '5100')}"
DEPOT_URL = f"http://127.0.0.1:{os.environ.get('DEPOT_PORT', '6212')}"
LG_URL = "http://127.0.0.1:6100"
TIMEOUT = 30

TIER_RANK: Dict[str, int] = {
    "lite": 0, "pro": 1, "business": 2, "enterprise": 3,
}

_LG_LITE_PROFILE: Dict[str, Any] = {
    "tier": "lite",
    "features": {"paid_operators": False},
    "limits": {"max_operators": 2},
}


# ── Service calls (stdlib) ────────────────────────────────────────────────────

def _get(url: str, timeout: int = 5) -> Optional[Dict[str, Any]]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def _post(url: str, body: Dict[str, Any], timeout: int = TIMEOUT) -> Dict[str, Any]:
    raw = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=raw, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            err = json.loads(exc.read())
        except Exception:
            err = {}
        return {"ok": False, "error": f"HTTP {exc.code}", "detail": err}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── Entitlement ───────────────────────────────────────────────────────────────

def get_entitlement() -> Dict[str, Any]:
    """Fetch entitlement profile from License Gate. Returns lite on failure."""
    result = _get(f"{LG_URL}/api/license/entitlement", timeout=5)
    return result if result else _LG_LITE_PROFILE


# ── Catalog ───────────────────────────────────────────────────────────────────

def get_catalog() -> List[Dict[str, Any]]:
    """Fetch operator catalog from DEPOT. Returns empty list on failure."""
    result = _get(f"{DEPOT_URL}/v1/operators", timeout=TIMEOUT)
    if result and "operators" in result:
        return result["operators"]
    return []


def get_entitled_catalog() -> Dict[str, Any]:
    """
    Return catalog split into installable and locked based on current entitlement.
    {tier, installable: [...], locked: [...], total: int}
    """
    profile = get_entitlement()
    tier = profile.get("tier", "lite")
    current_rank = TIER_RANK.get(tier, 0)
    paid_ok = profile.get("features", {}).get("paid_operators", False)

    installable: List[Dict[str, Any]] = []
    locked: List[Dict[str, Any]] = []

    for op in get_catalog():
        op_tier = op.get("tier_required", "lite")
        op_rank = TIER_RANK.get(op_tier, 0)

        if op_rank == 0:
            op["install_reason"] = "free"
            installable.append(op)
        elif not paid_ok:
            op["lock_reason"] = "requires_paid_license"
            locked.append(op)
        elif current_rank >= op_rank:
            op["install_reason"] = f"entitled_{tier}"
            installable.append(op)
        else:
            op["lock_reason"] = "tier_insufficient"
            op["required_tier"] = op_tier
            locked.append(op)

    return {
        "tier": tier,
        "installable": installable,
        "locked": locked,
        "total": len(installable) + len(locked),
    }


# ── Install modes ─────────────────────────────────────────────────────────────

def install_from_package(op_id: str, version: str) -> Dict[str, Any]:
    """
    Install via DEPOT package URL → CREW downloads and extracts zip.
    Use when operator files are NOT already on disk.
    """
    package_url = f"{DEPOT_URL}/v1/packages/{op_id}/{version}/download"
    result = _post(f"{CREW_URL}/install_operator", {
        "operator_id": op_id,
        "package_url": package_url,
        "source": "depot",
    })
    return {"ok": result.get("ok", False), "id": op_id, "version": version,
            "mode": "package_url", "detail": result}


def install_from_manifest(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """
    Register operator with CREW using manifest only (no zip transfer).
    Use when operator directory is already on disk (e.g. after git clone).
    CREW validates the manifest and writes to registry.json.
    """
    op_id = manifest.get("id", "")
    result = _post(f"{CREW_URL}/install_operator", {
        "operator_id": op_id,
        "manifest": manifest,
        "source": "depot_manifest",
    })
    ok = result.get("ok", False)
    if not ok and "already registered" in str(result.get("error", "")):
        ok = True  # already registered counts as success
    return {"ok": ok, "id": op_id,
            "version": manifest.get("version", ""), "mode": "manifest", "detail": result}


def install_operator(op_id: str, version: str = "",
                     operators_dir: Optional[Path] = None) -> Dict[str, Any]:
    """
    Install one operator. Chooses manifest-only if files are on disk,
    otherwise downloads from DEPOT.
    """
    # Try manifest-only first if operator dir exists
    if operators_dir:
        op_dir = operators_dir / op_id
        manifest_file = op_dir / "manifest.json"
        if manifest_file.exists():
            manifest = json.loads(manifest_file.read_text())
            result = install_from_manifest(manifest)
            if result["ok"]:
                return result
            # Fall through to package download on failure

    # Try package download from DEPOT
    if not version:
        catalog = get_catalog()
        op = next((o for o in catalog if o.get("id") == op_id), None)
        version = op.get("version", "1.0.0") if op else "1.0.0"

    manifest_data = _get(f"{DEPOT_URL}/v1/packages/{op_id}/{version}/manifest")
    if manifest_data:
        return install_from_package(op_id, version)

    return {"ok": False, "id": op_id, "error": "not in catalog and not packaged"}


# ── Batch ─────────────────────────────────────────────────────────────────────

def _scan_operators_dir(
    operators_dir: Path, tier: str, paid_ok: bool,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Scan operators_dir for manifest.json files, split into installable/locked
    by the current tier entitlement.  Skips entries without a top-level 'id'.
    """
    current_rank = TIER_RANK.get(tier, 0)
    installable: List[Dict[str, Any]] = []
    locked: List[Dict[str, Any]] = []
    for op_dir in sorted(operators_dir.iterdir()):
        if not op_dir.is_dir():
            continue
        mf = op_dir / "manifest.json"
        if not mf.is_file():
            continue
        try:
            manifest = json.loads(mf.read_text())
        except Exception:
            continue
        if "id" not in manifest:
            continue
        op_tier = manifest.get("tier_required", "lite")
        op_rank = TIER_RANK.get(op_tier, 0)
        if op_rank == 0:
            manifest["install_reason"] = "free"
            installable.append(manifest)
        elif not paid_ok:
            manifest["lock_reason"] = "requires_paid_license"
            locked.append(manifest)
        elif current_rank >= op_rank:
            manifest["install_reason"] = f"entitled_{tier}"
            installable.append(manifest)
        else:
            manifest["lock_reason"] = "tier_insufficient"
            manifest["required_tier"] = op_tier
            locked.append(manifest)
    return installable, locked


def install_all_entitled(operators_dir: Optional[Path] = None,
                         verbose: bool = True) -> Dict[str, Any]:
    """
    Install all operators the current tier is entitled to.
    When operators_dir exists, scans it for manifests directly (private operators).
    Falls back to DEPOT catalog when no local dir is given (built-in connectors).
    Returns summary: {tier, installed, failed, locked, counts}.
    """
    profile = get_entitlement()
    tier = profile.get("tier", "lite")
    paid_ok = profile.get("features", {}).get("paid_operators", False)

    if operators_dir and operators_dir.exists():
        installable, locked = _scan_operators_dir(operators_dir, tier, paid_ok)
    else:
        catalog = get_entitled_catalog()
        tier = catalog["tier"]
        installable = catalog["installable"]
        locked = catalog["locked"]

    if verbose:
        print(f"  Tier: {tier.upper()}")
        print(f"  Entitled: {len(installable)} operators")
        if locked:
            print(f"  Locked:   {len(locked)} operators (upgrade to unlock)")
            for op in locked[:5]:
                reason = op.get("lock_reason", "")
                needs = op.get("required_tier", "")
                print(f"    🔒 {op.get('id')} — {reason}" + (f" (needs {needs})" if needs else ""))

    installed: List[str] = []
    failed: List[Dict[str, Any]] = []

    for op in installable:
        op_id = op.get("id", "")
        if verbose:
            print(f"  Installing {op_id}...")
        # Full manifest already in hand (local scan) — use manifest-only path
        if "start_cmd" in op or operators_dir:
            result = install_from_manifest(op)
            if not result["ok"]:
                # Fall through to package download if manifest-only fails
                result = install_operator(op_id, op.get("version", ""), operators_dir)
        else:
            result = install_operator(op_id, op.get("version", ""), operators_dir)
        if result.get("ok"):
            installed.append(op_id)
            if verbose:
                print(f"  ✅ {op_id}")
        else:
            err = result.get("error") or result.get("detail", {}).get("error", "unknown")
            failed.append({"id": op_id, "error": err})
            if verbose:
                print(f"  ⚠ {op_id}: {err}")

    return {
        "tier": tier,
        "installed": installed,
        "failed": failed,
        "locked": [o.get("id") for o in locked],
        "installed_count": len(installed),
        "failed_count": len(failed),
        "locked_count": len(locked),
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    ops_dir_arg = next((a for a in args if a.startswith("--operators-dir=")), None)
    operators_dir = Path(ops_dir_arg.split("=", 1)[1]) if ops_dir_arg else None
    targets = [a for a in args if not a.startswith("--")]

    if targets:
        for op_id in targets:
            result = install_operator(op_id, operators_dir=operators_dir)
            print(json.dumps(result, indent=2))
    else:
        result = install_all_entitled(operators_dir)
        print(json.dumps(result, indent=2))
