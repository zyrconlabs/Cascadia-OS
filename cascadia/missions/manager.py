"""Mission Manager — read and write API on port 6207."""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict

from cascadia.missions.registry import MissionRegistry
from cascadia.missions.runner import (
    MissionNotFoundError,
    MissionNotInstalledError,
    MissionRunner,
    StitchMissionAdapter,
    TierNotAllowedError,
    WorkflowNotFoundError,
)
from cascadia.shared.config import load_config
from cascadia.shared.service_runtime import ServiceRuntime

PORT = 6207
NAME = "mission_manager"

_registry: MissionRegistry | None = None
_runner: MissionRunner | None = None
log = logging.getLogger(__name__)


def _db_path() -> str:
    try:
        p = Path(__file__).parent.parent.parent / "config.json"
        if p.exists():
            cfg = json.loads(p.read_text(encoding="utf-8"))
            return cfg.get("database_path", "./data/runtime/cascadia.db")
    except Exception:
        pass
    return "./data/runtime/cascadia.db"


def _mission_summary(m: dict) -> dict:
    return {
        "id": m.get("id"),
        "type": m.get("type", "mission"),
        "name": m.get("name"),
        "version": m.get("version"),
        "description": m.get("description"),
        "tier_required": m.get("tier_required"),
        "industries": m.get("industries", []),
        "installed": bool(m.get("installed")),
        "status": "installed" if m.get("installed") else "available",
        "prism": m.get("prism"),
        "mobile": m.get("mobile"),
    }


# ── Handlers ──────────────────────────────────────────────────────────────────

def handle_healthz(payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
    return 200, {"status": "ok", "service": NAME, "port": PORT}


def handle_catalog(payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
    if _registry is None:
        return 200, {"missions": []}
    return 200, {"missions": [_mission_summary(m) for m in _registry.list_catalog()]}


def handle_installed(payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
    if _registry is None:
        return 200, {"missions": []}
    raw = _registry.list_installed()
    missions = []
    for entry in raw:
        if isinstance(entry, dict):
            missions.append(_mission_summary({**entry, "installed": True}))
        elif isinstance(entry, str):
            m = _registry.get_mission(entry)
            if m:
                missions.append(_mission_summary({**m, "installed": True}))
    return 200, {"missions": missions}


def handle_mission_detail(payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
    mission_id = payload.get("mission_id", "")
    if _registry is None:
        return 404, {"error": "mission_not_found", "mission_id": mission_id}
    m = _registry.get_mission(mission_id)
    if m is None:
        return 404, {"error": "mission_not_found", "mission_id": mission_id}
    installed_ids = _installed_ids()
    return 200, _mission_summary({**m, "installed": mission_id in installed_ids})


def handle_status(payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
    mission_id = payload.get("mission_id", "")
    if _registry is None:
        return 404, {"error": "mission_not_found", "mission_id": mission_id}
    m = _registry.get_mission(mission_id)
    if m is None:
        return 404, {"error": "mission_not_found", "mission_id": mission_id}
    installed = mission_id in _installed_ids()

    pending_approvals = active_runs = failed_runs_24h = 0
    try:
        conn = sqlite3.connect(_db_path())
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM approvals WHERE mission_id = ? AND decision IS NULL",
                (mission_id,),
            ).fetchone()
            pending_approvals = row[0] if row else 0
            row = conn.execute(
                "SELECT COUNT(*) FROM mission_runs WHERE mission_id = ? AND status IN ('running','waiting_approval')",
                (mission_id,),
            ).fetchone()
            active_runs = row[0] if row else 0
            row = conn.execute(
                "SELECT COUNT(*) FROM mission_runs "
                "WHERE mission_id = ? AND status = 'failed' AND failed_at > datetime('now','-24 hours')",
                (mission_id,),
            ).fetchone()
            failed_runs_24h = row[0] if row else 0
        finally:
            conn.close()
    except Exception:
        pass

    required_operators = {op: "unknown" for op in (m.get("operators") or {}).get("required", [])}
    required_connectors = {c: "unknown" for c in (m.get("connectors") or {}).get("required", [])}

    return 200, {
        "mission_id": mission_id,
        "name": m.get("name"),
        "version": m.get("version"),
        "status": "installed" if installed else "available",
        "installed": installed,
        "last_run_at": None,
        "last_run_status": None,
        "pending_approvals": pending_approvals,
        "active_runs": active_runs,
        "failed_runs_24h": failed_runs_24h,
        "required_operators": required_operators,
        "required_connectors": required_connectors,
        "tier_required": m.get("tier_required"),
    }


def handle_mobile_schema(payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
    mission_id = payload.get("mission_id", "")
    if _registry is None:
        return 404, {"error": "mobile_schema_not_found", "mission_id": mission_id}
    path = _registry.get_mobile_schema_path(mission_id)
    if path is None:
        return 404, {"error": "mobile_schema_not_found", "mission_id": mission_id}
    try:
        return 200, json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return 404, {"error": "mobile_schema_not_found", "mission_id": mission_id}


def handle_prism_schema(payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
    mission_id = payload.get("mission_id", "")
    if _registry is None:
        return 404, {"error": "prism_schema_not_found", "mission_id": mission_id}
    path = _registry.get_prism_schema_path(mission_id)
    if path is None:
        return 404, {"error": "prism_schema_not_found", "mission_id": mission_id}
    try:
        return 200, json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return 404, {"error": "prism_schema_not_found", "mission_id": mission_id}


def handle_health(payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
    mission_id = payload.get("mission_id", "")
    if _registry is None:
        return 404, {"error": "mission_not_found", "mission_id": mission_id}
    m = _registry.get_mission(mission_id)
    if m is None:
        return 404, {"error": "mission_not_found", "mission_id": mission_id}

    installed = mission_id in _installed_ids()
    score = 0
    checks = []

    checks.append({"id": "manifest_valid", "label": "Manifest valid", "status": "pass"})
    score += 25

    mobile_path = _registry.get_mobile_schema_path(mission_id)
    mobile_ok = mobile_path is not None and Path(mobile_path).exists()
    checks.append({"id": "mobile_schema_exists", "label": "Mobile schema exists",
                   "status": "pass" if mobile_ok else "fail"})
    if mobile_ok:
        score += 15

    prism_path = _registry.get_prism_schema_path(mission_id)
    prism_ok = prism_path is not None and Path(prism_path).exists()
    checks.append({"id": "prism_schema_exists", "label": "PRISM schema exists",
                   "status": "pass" if prism_ok else "fail"})
    if prism_ok:
        score += 15

    base_path = m.get("_base_path", "")
    workflows = m.get("workflows") or {}
    wf_ok = all(
        Path(base_path, rel).exists() for rel in workflows.values()
    ) if workflows else True
    checks.append({"id": "workflow_files_exist", "label": "Workflow files exist",
                   "status": "pass" if wf_ok else "fail"})
    if wf_ok:
        score += 15

    checks.append({"id": "installed", "label": "Mission installed",
                   "status": "pass" if installed else "warn"})
    if installed:
        score += 15

    checks.append({"id": "operators", "label": "Required operators online", "status": "unknown"})

    if not installed:
        overall = "not_installed"
    elif score >= 85:
        overall = "healthy"
    elif score >= 60:
        overall = "degraded"
    else:
        overall = "unhealthy"

    return 200, {"mission_id": mission_id, "score": score, "status": overall, "checks": checks}


def handle_runs(payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
    mission_id = payload.get("mission_id", "")
    if _registry is None:
        return 404, {"error": "mission_not_found", "mission_id": mission_id}
    if _registry.get_mission(mission_id) is None:
        return 404, {"error": "mission_not_found", "mission_id": mission_id}

    runs: list = []
    try:
        conn = sqlite3.connect(_db_path())
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.execute(
                "SELECT id, workflow_id, trigger_type, trigger_data, "
                "status, started_at, completed_at "
                "FROM mission_runs WHERE mission_id = ? "
                "ORDER BY started_at DESC LIMIT 20",
                (mission_id,),
            )
            for row in cur.fetchall():
                row = dict(row)
                try:
                    td = json.loads(row.get("trigger_data") or "{}") if row.get("trigger_data") else {}
                except Exception:
                    td = {}
                runs.append({
                    "id": row["id"],
                    "workflow_id": row.get("workflow_id") or td.get("workflow_id"),
                    "status": row["status"],
                    "trigger_type": row.get("trigger_type") or td.get("trigger_type"),
                    "started_at": row.get("started_at"),
                    "completed_at": row.get("completed_at"),
                })
        finally:
            conn.close()
    except Exception:
        pass

    return 200, {"mission_id": mission_id, "runs": runs}


# ── Write handlers ────────────────────────────────────────────────────────────

def handle_run_mission(payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
    mission_id = payload.get("mission_id", "")
    workflow_id = payload.get("workflow_id", "")
    trigger_type = payload.get("trigger_type", "manual")
    if _runner is None:
        return 503, {"error": "runner_not_available"}
    try:
        result = _runner.start_mission(
            mission_id, workflow_id, trigger_type, payload.get("input")
        )
        return 200, result
    except MissionNotFoundError:
        return 404, {"error": "mission_not_found", "mission_id": mission_id}
    except MissionNotInstalledError:
        return 409, {"error": "mission_not_installed", "mission_id": mission_id}
    except WorkflowNotFoundError:
        return 404, {"error": "workflow_not_found", "workflow_id": workflow_id}
    except TierNotAllowedError as exc:
        return 403, {"error": "tier_not_allowed", "detail": str(exc)}
    except Exception as exc:
        log.error("handle_run_mission error: %s", exc)
        return 500, {"error": "internal_error", "detail": str(exc)}


def handle_resume_mission(payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
    run_id = payload.get("run_id", "")
    if _runner is None:
        return 503, {"error": "runner_not_available"}
    result = _runner.resume_mission(run_id, {
        "decision": payload.get("decision", ""),
        "approval_id": payload.get("approval_id"),
        "note": payload.get("note", ""),
        "edited_payload": payload.get("edited_payload"),
    })
    return 200, result


def handle_retry_mission(payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
    run_id = payload.get("run_id", "")
    if _runner is None:
        return 503, {"error": "runner_not_available"}
    result = _runner.retry_mission_run(run_id)
    return 200, result


def handle_fail_mission(payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
    run_id = payload.get("run_id", "")
    if _runner is None:
        return 503, {"error": "runner_not_available"}
    result = _runner.fail_mission(
        run_id,
        payload.get("error", "unknown error"),
        payload.get("failed_step"),
    )
    return 200, result or {}


def handle_complete_mission(payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
    run_id = payload.get("run_id", "")
    if _runner is None:
        return 503, {"error": "runner_not_available"}
    result = _runner.complete_mission(run_id, payload.get("output"))
    return 200, result or {}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _installed_ids() -> set:
    if _registry is None:
        return set()
    raw = _registry.list_installed()
    result = set()
    for entry in raw:
        if isinstance(entry, dict):
            result.add(entry.get("id"))
        elif isinstance(entry, str):
            result.add(entry)
    return result


# ── Service class ─────────────────────────────────────────────────────────────

class MissionManagerService:

    def __init__(self, config_path: str, name: str) -> None:
        global _registry, _runner
        config = load_config(config_path)
        component = next(c for c in config["components"] if c["name"] == name)
        packages_root = (config.get("missions") or {}).get("packages_root") or None
        _registry = MissionRegistry(packages_root=packages_root)
        _runner = MissionRunner(
            registry=_registry,
            adapter=StitchMissionAdapter(),
        )
        self.runtime = ServiceRuntime(
            name=name,
            port=component["port"],
            heartbeat_file=component["heartbeat_file"],
            log_dir=config["log_dir"],
        )
        self.runtime.register_route("GET",  "/healthz",                                          handle_healthz)
        self.runtime.register_route("GET",  "/api/missions/catalog",                             handle_catalog)
        self.runtime.register_route("GET",  "/api/missions/installed",                           handle_installed)
        self.runtime.register_route("GET",  "/api/missions/{mission_id}",                        handle_mission_detail)
        self.runtime.register_route("GET",  "/api/missions/{mission_id}/status",                 handle_status)
        self.runtime.register_route("GET",  "/api/missions/{mission_id}/mobile_schema",          handle_mobile_schema)
        self.runtime.register_route("GET",  "/api/missions/{mission_id}/prism_schema",           handle_prism_schema)
        self.runtime.register_route("GET",  "/api/missions/{mission_id}/health",                 handle_health)
        self.runtime.register_route("GET",  "/api/missions/{mission_id}/runs",                   handle_runs)
        self.runtime.register_route("POST", "/api/missions/{mission_id}/run/{workflow_id}",      handle_run_mission)
        self.runtime.register_route("POST", "/api/missions/{mission_id}/runs/{run_id}/resume",   handle_resume_mission)
        self.runtime.register_route("POST", "/api/missions/{mission_id}/runs/{run_id}/retry",    handle_retry_mission)
        self.runtime.register_route("POST", "/api/missions/{mission_id}/runs/{run_id}/fail",     handle_fail_mission)
        self.runtime.register_route("POST", "/api/missions/{mission_id}/runs/{run_id}/complete", handle_complete_mission)

    def start(self) -> None:
        self.runtime.logger.info("MISSION MANAGER read API active on port %s", PORT)
        self.runtime.start()


def main() -> None:
    p = argparse.ArgumentParser(description="Mission Manager — Cascadia OS read API")
    p.add_argument("--config", required=True)
    p.add_argument("--name", required=True)
    a = p.parse_args()
    MissionManagerService(a.config, a.name).start()


if __name__ == "__main__":
    main()
