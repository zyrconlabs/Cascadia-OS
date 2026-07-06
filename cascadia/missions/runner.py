"""Mission Runner — turns mission manifests into executable runs via WorkflowRuntime."""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from cascadia.automation.workflow_runtime import WorkflowRuntime
from cascadia.automation.stitch import WorkflowDefinition, WorkflowStep

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
    ) -> None:
        self._registry = registry or MissionRegistry()
        self._db_path = db_path or _resolve_db_path()

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

        # No external actions — dispatch directly to WorkflowRuntime
        try:
            wrt_run_id = self._execute_workflow_direct(
                mission_id=mission_id,
                workflow_id=workflow_id,
                wf_def=wf_def,
                payload=payload or {},
                on_done=self._make_on_done(run_id),
            )
            self._update_run(run_id, {
                "trigger_data": json.dumps({
                    "workflow_id": workflow_id,
                    "trigger_type": trigger_type,
                    "stitch_run_id": wrt_run_id,
                    "input": payload or {},
                }),
            })
        except Exception as exc:
            log.warning("WorkflowRuntime direct dispatch failed for run %s: %s", run_id, exc)

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

        dispatch_ok = False
        mission_id = run.get("mission_id", "")
        workflow_id = run.get("workflow_id") or td.get("workflow_id", "")
        if mission_id and workflow_id and self._registry:
            try:
                wf_path = self._registry.get_workflow_path(mission_id, workflow_id)
                wf_def = json.loads(Path(wf_path).read_text(encoding="utf-8"))
                edited_payload = (
                    approval_decision.get("edited_payload") or td.get("input", {})
                )
                wrt_run_id = self._execute_workflow_direct(
                    mission_id=mission_id,
                    workflow_id=workflow_id,
                    wf_def=wf_def,
                    payload=edited_payload,
                    on_done=self._make_on_done(mission_run_id),
                )
                dispatch_ok = bool(wrt_run_id)
            except Exception as exc:
                log.warning("WorkflowRuntime dispatch failed for %s: %s",
                            mission_run_id, exc)

        if not dispatch_ok:
            self._update_run(mission_run_id, {"status": "retry_pending", "updated_at": now})
            publish_mission_event(APPROVAL_RESOLVED, {
                "mission_run_id": mission_run_id,
                "decision": decision,
                "note": "WorkflowRuntime dispatch unavailable — manual retry required",
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

    # ── Query / event-driven public API ───────────────────────────────────────

    def get_run_status(self, mission_run_id: str) -> dict:
        """Return a mission_run record as a dict, or an error dict if not found."""
        run = self._get_run(mission_run_id)
        if run is None:
            return {"error": "run_not_found", "mission_run_id": mission_run_id}
        return {
            "mission_run_id": run["id"],
            "mission_id": run.get("mission_id", ""),
            "workflow_id": run.get("workflow_id"),
            "status": run.get("status", ""),
            "trigger_type": run.get("trigger_type"),
            "started_at": run.get("started_at"),
            "completed_at": run.get("completed_at"),
            "error": run.get("error"),
            "retry_count": run.get("retry_count", 0),
        }

    def get_run_output(self, mission_run_id: str) -> dict:
        """Return a run's completed output content, or null until it completes.

        complete_mission stores context_data as {"output": <content>}. This
        reads it back so callers can retrieve the actual mission result over
        HTTP. Mirrors get_run_status's state reporting but adds `output`,
        which stays null for any status other than 'completed'.
        """
        run = self._get_run(mission_run_id)
        if run is None:
            return {"error": "run_not_found", "mission_run_id": mission_run_id}
        status = run.get("status", "")
        output = None
        if status == "completed":
            try:
                ctx = json.loads(run.get("context_data") or "{}")
                output = ctx.get("output")
            except (TypeError, ValueError):
                output = None
        return {
            "mission_run_id": run["id"],
            "status": status,
            "output": output,
        }

    def list_recent_runs(
        self,
        mission_id: Optional[str] = None,
        limit: int = 20,
    ) -> list:
        """Return recent mission_runs ordered by started_at DESC.

        Optionally filter by mission_id. Returns empty list on DB error.
        """
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            try:
                if mission_id:
                    rows = conn.execute(
                        "SELECT id, mission_id, workflow_id, trigger_type, status, "
                        "started_at, completed_at, error, retry_count "
                        "FROM mission_runs WHERE mission_id = ? "
                        "ORDER BY started_at DESC LIMIT ?",
                        (mission_id, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT id, mission_id, workflow_id, trigger_type, status, "
                        "started_at, completed_at, error, retry_count "
                        "FROM mission_runs "
                        "ORDER BY started_at DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()
        except Exception as exc:
            log.error("list_recent_runs failed: %s", exc)
            return []

    def trigger_from_event(self, event_type: str, event_data: dict) -> Optional[str]:
        """Start a mission whose manifest declares it consumes this event_type.

        Checks all installed missions. Uses the first workflow whose name
        matches the event or falls back to the first declared workflow.
        Returns mission_run_id on success, None if no mission matches or on error.
        """
        installed_ids = self._installed_ids()
        for mission_id in sorted(installed_ids):
            manifest = self._registry.get_mission(mission_id)
            if not manifest:
                continue
            consumes = (manifest.get("events") or {}).get("consumes", [])
            if event_type not in consumes:
                continue
            # Find best workflow: prefer one whose id matches event name component
            workflows: dict = manifest.get("workflows") or {}
            if not workflows:
                log.warning("trigger_from_event: mission %s has no workflows", mission_id)
                continue
            event_tail = event_type.split(".")[-1]
            workflow_id = next(
                (wid for wid in workflows if event_tail in wid),
                next(iter(workflows)),
            )
            try:
                result = self.start_mission(
                    mission_id=mission_id,
                    workflow_id=workflow_id,
                    trigger_type="event",
                    payload=event_data,
                )
                run_id = result.get("mission_run_id")
                log.info(
                    "trigger_from_event: %s → %s/%s run=%s",
                    event_type, mission_id, workflow_id, run_id,
                )
                return run_id
            except Exception as exc:
                log.error(
                    "trigger_from_event: failed to start %s/%s: %s",
                    mission_id, workflow_id, exc,
                )
        return None

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

    def _execute_workflow_direct(
        self,
        mission_id: str,
        workflow_id: str,
        wf_def: dict,
        payload: dict,
        on_done: Optional[Any] = None,
    ) -> str:
        """Execute workflow via WorkflowRuntime in-process. Returns WorkflowRuntime run_id.

        policy_rules={} — MissionRunner's pre-dispatch gate (EXTERNAL_ACTIONS) has
        already handled external-action approval before this call is reached.
        WorkflowRuntime's internal gate must not fire a second time on the same action.
        RuntimePolicy default flags email.send as approval_required (workflow_runtime.py:95).
        """
        steps = [
            WorkflowStep(
                name=s.get("id", ""),
                operator=s.get("operator", ""),
                action=s.get("action", ""),
                condition=s.get("condition"),
            )
            for s in wf_def.get("steps", [])
        ]
        definition = WorkflowDefinition(
            workflow_id=wf_def.get("id", workflow_id),
            name=wf_def.get("name", workflow_id),
            steps=steps,
        )
        runtime = WorkflowRuntime(self._db_path, policy_rules={})
        wrt_run_id = runtime.create_run(workflow_id, definition, payload)

        def _run() -> None:
            try:
                result = runtime.execute(workflow_id, definition, {"run_id": wrt_run_id})
                if on_done is not None:
                    try:
                        on_done(result.to_dict())
                    except Exception as exc:
                        log.warning("on_done callback failed for %s: %s", wrt_run_id, exc)
            except Exception as exc:
                log.warning("WorkflowRuntime direct execute failed for %s: %s", wrt_run_id, exc)
                if on_done is not None:
                    on_done({"error": str(exc), "run_state": "failed"})

        threading.Thread(
            target=_run,
            daemon=True,
            name=f"wf-direct-{wrt_run_id}",
        ).start()
        return wrt_run_id

    def _make_on_done(self, mission_run_id: str):
        """Build WorkflowRuntime completion callback bound to this mission_run."""
        def _on_done(response: dict) -> None:
            rs = (response or {}).get("run_state") or ""
            if rs == "complete":
                self.complete_mission(mission_run_id,
                    output=(response or {}).get("state_snapshot") or {})
            elif rs == "waiting_human":
                self._update_run(mission_run_id, {
                    "status": "waiting_approval",
                    "updated_at": _now(),
                })
            elif rs == "failed" or (response or {}).get("error"):
                self.fail_mission(mission_run_id,
                    error=(response or {}).get("assistant_message")
                          or (response or {}).get("error")
                          or "workflow failed",
                    failed_step=(response or {}).get("current_step"))
        return _on_done

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
