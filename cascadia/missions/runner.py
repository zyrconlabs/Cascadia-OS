"""Mission Runner — turns mission manifests into executable runs via STITCH."""
from __future__ import annotations

import json
import logging
import sqlite3
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from cascadia.missions.constants import DEFAULT_ORGANIZATION_ID
from cascadia.missions.events import (
    APPROVAL_CREATED,
    APPROVAL_RESOLVED,
    MISSION_APPROVAL_REQUESTED,
    MISSION_COMPLETED,
    MISSION_FAILED,
    MISSION_STARTED,
)
from cascadia.missions.registry import MissionRegistry

log = logging.getLogger(__name__)

# Step actions that require approval before dispatch to STITCH
EXTERNAL_ACTIONS = [
    "email.send", "sms.send", "campaign.post",
    "quote.send", "invoice.send", "payment.request", "crm.write",
]


# ── Custom exceptions ─────────────────────────────────────────────────────────

class MissionNotFoundError(Exception): pass
class MissionNotInstalledError(Exception): pass
class WorkflowNotFoundError(Exception): pass
class TierNotAllowedError(Exception): pass
class MissionRunError(Exception): pass


# ── Event publishing ──────────────────────────────────────────────────────────

def publish_mission_event(event_type: str, payload: Dict[str, Any]) -> None:
    """Publish via MobileMissionEventBridge; log at INFO regardless."""
    log.info("MISSION_EVENT: %s %s", event_type, payload)
    try:
        from cascadia.missions.mobile_events import get_bridge
        get_bridge().publish(event_type, payload)
    except Exception as exc:
        log.debug("mobile_events bridge unavailable (non-fatal): %s", exc)


# ── Tier check ────────────────────────────────────────────────────────────────

def check_tier_allowed(manifest: dict, organization_tier: str,
                       workflow_id: str, trigger_type: str) -> bool:
    """Return True if this org tier may run this workflow with this trigger type."""
    limits = manifest.get("limits") or {}
    if organization_tier not in limits:
        return True
    tier_limits = limits[organization_tier]
    if not tier_limits.get("enabled", True):
        return False
    if trigger_type == "schedule" and tier_limits.get("manual_runs_only", False):
        return False
    return True


# ── HTTP helper ───────────────────────────────────────────────────────────────

def _http_post(url: str, data: dict, timeout: int = 10) -> dict:
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ── STITCH adapter ────────────────────────────────────────────────────────────

class StitchMissionAdapter:
    """
    Narrow HTTP adapter for dispatching mission workflows via STITCH (port 6201).

    STITCH is HTTP-only — no direct Python callable from runner.py.

    resume_workflow() calls POST /run/resume which delegates to WorkflowRuntime.
    For runs that were paused before STITCH was ever called (approval gate fired
    pre-dispatch), there is no STITCH run record to resume. In that case the
    caller should treat resume as a fresh start_workflow() call.

    If resume returns {"supported": False} the caller must mark the run
    retry_pending rather than faking completion.
    """

    STITCH_PORT = 6201

    def __init__(self, host: str = "127.0.0.1", port: int = STITCH_PORT) -> None:
        self._base = f"http://{host}:{port}"

    def start_workflow(self, workflow_def: dict, payload: dict) -> str:
        """Register and start a mission workflow via STITCH. Returns stitch run_id."""
        steps = [
            {
                "name": s.get("id", ""),
                "operator": s.get("operator", ""),
                "action": s.get("action", ""),
                "on_failure": "stop",
            }
            for s in workflow_def.get("steps", [])
        ]
        wf_id = workflow_def.get("id", f"mission_{uuid.uuid4().hex[:8]}")
        _http_post(self._base + "/workflow/register", {
            "workflow_id": wf_id,
            "name": workflow_def.get("name", wf_id),
            "steps": steps,
            "description": workflow_def.get("description", ""),
        })
        result = _http_post(self._base + "/run/start", {
            "workflow_id": wf_id,
            "goal": workflow_def.get("name", ""),
            "input": payload,
        })
        return result.get("run_id", "")

    def resume_workflow(self, stitch_run_id: str, payload: dict) -> dict:
        """Try to resume a STITCH run. Returns {"supported": False} if unavailable."""
        try:
            return _http_post(self._base + "/run/resume", {"run_id": stitch_run_id})
        except Exception as exc:
            log.warning("STITCH resume unavailable for %s: %s", stitch_run_id, exc)
            return {"error": str(exc), "supported": False}


# ── DB helpers ────────────────────────────────────────────────────────────────

def _resolve_db_path() -> str:
    try:
        p = Path(__file__).parent.parent.parent / "config.json"
        if p.exists():
            cfg = json.loads(p.read_text(encoding="utf-8"))
            return cfg.get("database_path", "./data/runtime/cascadia.db")
    except Exception:
        pass
    return "./data/runtime/cascadia.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_external_action(action: str) -> bool:
    action_lower = action.lower()
    return any(ext in action_lower for ext in EXTERNAL_ACTIONS)


# ── MissionRunner ─────────────────────────────────────────────────────────────

class MissionRunner:

    def __init__(
        self,
        registry: Optional[MissionRegistry] = None,
        db_path: Optional[str] = None,
        adapter: Optional[StitchMissionAdapter] = None,
    ) -> None:
        self._registry = registry or MissionRegistry()
        self._db_path = db_path or _resolve_db_path()
        self._adapter = adapter or StitchMissionAdapter()

    # ── Start ─────────────────────────────────────────────────────────────────

    def start_mission(
        self,
        mission_id: str,
        workflow_id: str,
        trigger_type: str = "manual",
        payload: Optional[dict] = None,
        organization_id: Optional[str] = None,
    ) -> dict:
        # a. Load manifest
        manifest = self._registry.get_mission(mission_id)
        if manifest is None:
            raise MissionNotFoundError(mission_id)

        # b. Check installed
        if mission_id not in self._installed_ids():
            raise MissionNotInstalledError(mission_id)

        # c. Check workflow exists in manifest
        workflows = manifest.get("workflows") or {}
        if workflow_id not in workflows:
            raise WorkflowNotFoundError(workflow_id)

        # d. Load workflow JSON
        wf_path = self._registry.get_workflow_path(mission_id, workflow_id)
        wf_def = json.loads(Path(wf_path).read_text(encoding="utf-8"))

        # e. Check tier limits
        if organization_id:
            org_tier = self._get_org_tier(organization_id)
            if not check_tier_allowed(manifest, org_tier, workflow_id, trigger_type):
                raise TierNotAllowedError(
                    f"tier {org_tier!r} does not allow {trigger_type!r} runs for {mission_id}"
                )

        # f. Create mission_runs row
        run_id = str(uuid.uuid4())
        org_id = organization_id or DEFAULT_ORGANIZATION_ID
        now = _now()
        trigger_data = json.dumps({
            "workflow_id": workflow_id,
            "trigger_type": trigger_type,
            "input": payload or {},
        })
        self._insert_run(run_id, mission_id, org_id, workflow_id, trigger_type, trigger_data, now)

        # g. Publish MISSION_STARTED
        publish_mission_event(MISSION_STARTED, {
            "mission_id": mission_id,
            "mission_run_id": run_id,
            "workflow_id": workflow_id,
        })

        # h. Check for external steps — pause before dispatching
        steps = wf_def.get("steps", [])
        first_external = next(
            (s for s in steps if _is_external_action(s.get("action", ""))), None
        )
        if first_external:
            risk_level = self._risk_level_for_action(manifest, first_external.get("action", ""))
            self.pause_for_approval(run_id, {
                "title": f"Approve: {first_external.get('id', workflow_id)}",
                "summary": (
                    f"Mission {mission_id!r} workflow {workflow_id!r} requires approval "
                    f"before step {first_external.get('id', '')!r} "
                    f"({first_external.get('action', '')})"
                ),
                "payload": payload or {},
                "action": first_external.get("action", ""),
                "step_id": first_external.get("id", ""),
                "mission_id": mission_id,
                "risk_level": risk_level,
            })
            return {
                "mission_run_id": run_id,
                "mission_id": mission_id,
                "workflow_id": workflow_id,
                "status": "waiting_approval",
            }

        # No external actions — dispatch to STITCH
        try:
            stitch_run_id = self._adapter.start_workflow(wf_def, payload or {})
            self._update_run(run_id, {
                "trigger_data": json.dumps({
                    "workflow_id": workflow_id,
                    "trigger_type": trigger_type,
                    "stitch_run_id": stitch_run_id,
                    "input": payload or {},
                }),
            })
        except Exception as exc:
            log.warning("STITCH dispatch failed for run %s: %s", run_id, exc)

        return {
            "mission_run_id": run_id,
            "mission_id": mission_id,
            "workflow_id": workflow_id,
            "status": "running",
        }

    # ── Pause ─────────────────────────────────────────────────────────────────

    def pause_for_approval(self, mission_run_id: str, approval_payload: dict) -> dict:
        # a. Load mission_run
        run = self._get_run(mission_run_id)
        mission_id = (run or {}).get("mission_id") or approval_payload.get("mission_id", "")
        action = approval_payload.get("action", "external_action")
        risk_level = approval_payload.get("risk_level", "medium")
        now = _now()

        # b. Insert approval with mission_id and mission_run_id direct columns
        approval_id = self._insert_approval(
            run_id=mission_run_id,
            action_key=action,
            summary=approval_payload.get("summary", ""),
            mission_id=mission_id,
            mission_run_id=mission_run_id,
            now=now,
        )

        # c. Update mission_run status
        self._update_run(mission_run_id, {"status": "waiting_approval", "updated_at": now})

        # d/e. Publish events
        publish_mission_event(MISSION_APPROVAL_REQUESTED, {
            "mission_id": mission_id,
            "mission_run_id": mission_run_id,
            "approval_id": approval_id,
            "action": action,
        })
        publish_mission_event(APPROVAL_CREATED, {
            "approval_id": approval_id,
            "mission_run_id": mission_run_id,
            "action": action,
        })

        return {
            "approval_id": str(approval_id),
            "mission_run_id": mission_run_id,
            "status": "waiting_approval",
        }

    # ── Resume ────────────────────────────────────────────────────────────────

    def resume_mission(self, mission_run_id: str, approval_decision: dict) -> dict:
        # a. Load and validate state
        run = self._get_run(mission_run_id)
        if not run:
            return {"error": "run_not_found", "mission_run_id": mission_run_id}
        if run.get("status") != "waiting_approval":
            return {
                "error": "invalid_state",
                "mission_run_id": mission_run_id,
                "current_status": run.get("status"),
            }

        decision = approval_decision.get("decision", "")
        approval_id = approval_decision.get("approval_id")
        now = _now()

        # b. Rejected — cancel
        if decision == "rejected":
            self._update_run(mission_run_id, {
                "status": "cancelled", "completed_at": now, "updated_at": now,
            })
            if approval_id:
                self._update_approval_decision(approval_id, "rejected",
                                               approval_decision.get("note", ""))
            publish_mission_event(APPROVAL_RESOLVED, {
                "mission_run_id": mission_run_id, "decision": "rejected",
            })
            return {"status": "cancelled", "reason": "rejected",
                    "mission_run_id": mission_run_id}

        # c. Approved or edited — try STITCH dispatch
        self._update_run(mission_run_id, {"status": "running", "updated_at": now})
        if approval_id:
            self._update_approval_decision(approval_id, "approved",
                                           approval_decision.get("note", ""))

        td: dict = {}
        if run.get("trigger_data"):
            try:
                td = json.loads(run["trigger_data"])
            except Exception:
                pass

        stitch_run_id = td.get("stitch_run_id", "")
        stitch_ok = False

        if stitch_run_id:
            # Resume existing STITCH run
            result = self._adapter.resume_workflow(stitch_run_id, approval_decision)
            stitch_ok = result.get("supported") is not False and "error" not in result
        else:
            # No STITCH run yet — start fresh after approval
            mission_id = run.get("mission_id", "")
            workflow_id = run.get("workflow_id") or td.get("workflow_id", "")
            if mission_id and workflow_id and self._registry:
                try:
                    wf_path = self._registry.get_workflow_path(mission_id, workflow_id)
                    wf_def = json.loads(Path(wf_path).read_text(encoding="utf-8"))
                    edited_payload = (
                        approval_decision.get("edited_payload") or td.get("input", {})
                    )
                    stitch_run_id = self._adapter.start_workflow(wf_def, edited_payload)
                    stitch_ok = bool(stitch_run_id)
                except Exception as exc:
                    log.warning("STITCH start-after-approval failed for %s: %s",
                                mission_run_id, exc)

        if not stitch_ok:
            # STITCH resume/start unavailable — manual retry required
            self._update_run(mission_run_id, {"status": "retry_pending", "updated_at": now})
            publish_mission_event(APPROVAL_RESOLVED, {
                "mission_run_id": mission_run_id,
                "decision": decision,
                "note": "STITCH resume not available — manual retry required",
            })
            return {"status": "retry_pending", "mission_run_id": mission_run_id}

        publish_mission_event(APPROVAL_RESOLVED, {
            "mission_run_id": mission_run_id, "decision": decision,
        })
        run = self._get_run(mission_run_id) or {}
        return {"status": run.get("status", "running"), "mission_run_id": mission_run_id}

    # ── Fail ──────────────────────────────────────────────────────────────────

    def fail_mission(self, mission_run_id: str, error: str,
                     failed_step: Optional[str] = None) -> dict:
        now = _now()
        updates: dict = {
            "status": "failed",
            "error": str(error),
            "completed_at": now,
            "failed_at": now,
            "updated_at": now,
        }
        if failed_step:
            updates["context_data"] = json.dumps({"failed_step": failed_step})
        self._update_run(mission_run_id, updates)
        run = self._get_run(mission_run_id) or {}
        publish_mission_event(MISSION_FAILED, {
            "mission_run_id": mission_run_id,
            "mission_id": run.get("mission_id", ""),
            "error": str(error),
            "failed_step": failed_step,
        })
        return run

    # ── Complete ──────────────────────────────────────────────────────────────

    def complete_mission(self, mission_run_id: str, output: Optional[dict] = None) -> dict:
        now = _now()
        self._update_run(mission_run_id, {
            "status": "completed",
            "context_data": json.dumps({"output": output or {}}),
            "completed_at": now,
            "updated_at": now,
        })
        run = self._get_run(mission_run_id) or {}
        publish_mission_event(MISSION_COMPLETED, {
            "mission_run_id": mission_run_id,
            "mission_id": run.get("mission_id", ""),
            "output": output or {},
        })
        return run

    # ── Retry ─────────────────────────────────────────────────────────────────

    def retry_mission_run(self, mission_run_id: str) -> dict:
        run = self._get_run(mission_run_id)
        if not run:
            return {"error": "run_not_found", "mission_run_id": mission_run_id}

        status = run.get("status", "")
        if status == "completed":
            return {"error": "retry_not_available", "reason": "run already completed"}
        if status == "waiting_approval":
            return {"error": "retry_not_available",
                    "reason": "run is waiting for approval"}
        if status not in ("failed", "retry_pending", "cancelled"):
            return {"error": "retry_not_available",
                    "reason": f"run has status {status!r}"}

        # Record retry attempt on original run
        retry_count = (run.get("retry_count") or 0) + 1
        self._update_run(mission_run_id, {"retry_count": retry_count})

        # Extract original params
        td: dict = {}
        if run.get("trigger_data"):
            try:
                td = json.loads(run["trigger_data"])
            except Exception:
                pass

        mission_id = run.get("mission_id", "")
        workflow_id = run.get("workflow_id") or td.get("workflow_id", "")
        trigger_type = run.get("trigger_type") or td.get("trigger_type", "manual")
        original_payload = td.get("input", {})
        org_id = run.get("org_id") or DEFAULT_ORGANIZATION_ID

        try:
            return self.start_mission(
                mission_id=mission_id,
                workflow_id=workflow_id,
                trigger_type=trigger_type,
                payload=original_payload,
                organization_id=org_id if org_id != DEFAULT_ORGANIZATION_ID else None,
            )
        except Exception as exc:
            return {"error": str(exc), "mission_run_id": mission_run_id}

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _installed_ids(self) -> set:
        raw = self._registry.list_installed()
        result = set()
        for entry in raw:
            if isinstance(entry, dict):
                result.add(entry.get("id"))
            elif isinstance(entry, str):
                result.add(entry)
        return result

    def _get_org_tier(self, org_id: str) -> str:
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT tier FROM organizations WHERE id = ?", (org_id,)
                ).fetchone()
                return row["tier"] if row else "business"
            finally:
                conn.close()
        except Exception:
            return "business"

    def _insert_run(self, run_id: str, mission_id: str, org_id: str,
                    workflow_id: str, trigger_type: str,
                    trigger_data: str, now: str) -> None:
        try:
            conn = sqlite3.connect(self._db_path)
            try:
                try:
                    conn.execute(
                        "INSERT INTO mission_runs "
                        "(id, mission_id, org_id, workflow_id, trigger_type, status, "
                        "trigger_data, context_data, started_at, retry_count, "
                        "created_at, updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (run_id, mission_id, org_id, workflow_id, trigger_type,
                         "running", trigger_data, "{}", now, 0, now, now),
                    )
                except sqlite3.OperationalError:
                    # Pre-migration schema without workflow_id/trigger_type columns
                    conn.execute(
                        "INSERT INTO mission_runs "
                        "(id, mission_id, org_id, status, trigger_data, "
                        "context_data, started_at, retry_count, created_at, updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (run_id, mission_id, org_id, "running",
                         trigger_data, "{}", now, 0, now, now),
                    )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            log.error("Failed to insert mission_run %s: %s", run_id, exc)
            raise MissionRunError(f"Failed to create run record: {exc}") from exc

    def _update_run(self, run_id: str, updates: dict) -> None:
        if not updates:
            return
        parts = [f"{k} = ?" for k in updates]
        values = list(updates.values()) + [run_id]
        try:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.execute(
                    f"UPDATE mission_runs SET {', '.join(parts)} WHERE id = ?", values
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            log.error("Failed to update mission_run %s: %s", run_id, exc)

    def _get_run(self, run_id: str) -> Optional[dict]:
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT * FROM mission_runs WHERE id = ?", (run_id,)
                ).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()
        except Exception:
            return None

    def _insert_approval(self, run_id: str, action_key: str, summary: str,
                         mission_id: str, mission_run_id: str, now: str) -> int:
        """Insert into approvals with mission_id and mission_run_id columns.

        Uses mission_run_id as run_id. approvals.run_id has a FK to runs.run_id
        but SQLite FK enforcement is OFF by default so the insert succeeds.
        """
        try:
            conn = sqlite3.connect(self._db_path)
            try:
                cur = conn.execute(
                    "INSERT INTO approvals "
                    "(run_id, step_index, action_key, decision, actor, reason, "
                    "created_at, decided_at, mission_id, mission_run_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (run_id, 0, action_key, "pending", None, summary,
                     now, None, mission_id, mission_run_id),
                )
                approval_id = cur.lastrowid
                conn.commit()
                return approval_id
            finally:
                conn.close()
        except Exception as exc:
            log.error("Failed to insert approval for run %s: %s", run_id, exc)
            raise MissionRunError(f"Failed to create approval: {exc}") from exc

    def _update_approval_decision(self, approval_id: Any, decision: str,
                                  note: str = "") -> None:
        now = _now()
        try:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.execute(
                    "UPDATE approvals SET decision=?, reason=?, decided_at=? WHERE id=?",
                    (decision, note, now, int(approval_id)),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            log.warning("Failed to update approval %s: %s", approval_id, exc)

    def _risk_level_for_action(self, manifest: dict, action: str) -> str:
        for af in (manifest.get("approval_flows") or []):
            af_action = af.get("action", "")
            if af_action in ("*", action) or action.startswith(af_action):
                return af.get("risk_level", "medium")
        return "medium"
