"""
cascadia/chief/server.py - Cascadia OS 2026.5
CHIEF: Task orchestrator and operator router.

Owns: inbound task receipt, operator selection, BEACON dispatch,
      reply formatting, CREW self-registration.
Does not own: operator execution, capability validation (BEACON/CREW own that),
              session management (BELL owns that), channel I/O (VANGUARD owns that).

CHIEF is how a task finds the right worker.
"""
# MATURITY: PRODUCTION — keyword+capability selector, BEACON dispatch, sync reply path.
from __future__ import annotations

import argparse
import json
import os
import threading
import time
import urllib.request
import urllib.error
import uuid
from typing import Any, Dict

from cascadia.shared.config import load_config
from cascadia.shared.service_runtime import ServiceRuntime
from cascadia.chief.models import TaskRequest, TaskResponse
from cascadia.chief.operator_selector import select_target
from cascadia.chief.fallback import intelligent_fallback
from cascadia.chief.commands import (
    parse_command, build_help_text, build_operators_text,
    parse_contact_command, parse_quote_command, parse_approval_command,
)
from cascadia.chief.intent_router import (
    classify_intent,
    validate_routing_decision,
    append_history,
    get_history,
    get_last_action,
    set_last_action,
    OPERATOR_CATALOG,
    CONFIDENCE_DISPATCH,
    CONFIDENCE_CLARIFY,
)

_VERSION = "1.0.0"

# Environment overrides — ports resolved from config at startup, not hardcoded
CHIEF_PORT = int(os.environ.get("CHIEF_PORT", "6211"))
CREW_URL   = os.environ.get("CREW_URL", "http://127.0.0.1:5100")
BEACON_URL = os.environ.get("BEACON_URL", "http://127.0.0.1:6200")
MISSION_MANAGER_URL = os.environ.get("MISSION_MANAGER_URL", "http://127.0.0.1:6207")
BELL_URL   = os.environ.get("BELL_URL", "http://127.0.0.1:6204")
TELEGRAM_URL = os.environ.get("TELEGRAM_URL", "http://127.0.0.1:9000")
OM_URL     = os.environ.get("OM_URL", "http://127.0.0.1:6210")

_WAKE_WAIT = 35  # seconds to wait for operator health after wake (covers OM's 30s poll cycle)


def _http_post(url: str, payload: dict, timeout: int = 30) -> dict:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _http_get(url: str, timeout: int = 5) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _get_source(metadata: dict | None) -> str:
    """Source channel from task metadata ('prism', 'telegram', ...)."""
    return metadata.get("source", "") if metadata else ""


def _is_prism(metadata: dict | None) -> bool:
    """True when the task originated from the PRISM dashboard chat bar.
    PRISM expects full results returned synchronously in reply_text rather
    than pushed to Telegram."""
    return _get_source(metadata) == "prism"


class ChiefService:
    """
    CHIEF - Task orchestrator.
    Owns operator selection and BEACON dispatch.
    Does not own execution or session state.
    """

    def __init__(self, config_path: str, name: str) -> None:
        self.config = load_config(config_path)
        component = next(c for c in self.config["components"] if c["name"] == name)

        # Resolve URLs from config port map so no hardcoded ports leak
        port_map = {c["name"]: c["port"] for c in self.config.get("components", [])}
        global CREW_URL, BEACON_URL, MISSION_MANAGER_URL, BELL_URL, OM_URL
        if not os.environ.get("CREW_URL"):
            CREW_URL = f"http://127.0.0.1:{port_map.get('crew', 5100)}"
        if not os.environ.get("BEACON_URL"):
            BEACON_URL = f"http://127.0.0.1:{port_map.get('beacon', 6200)}"
        if not os.environ.get("MISSION_MANAGER_URL"):
            MISSION_MANAGER_URL = f"http://127.0.0.1:{port_map.get('mission_manager', 6207)}"
        if not os.environ.get("BELL_URL"):
            BELL_URL = f"http://127.0.0.1:{port_map.get('bell', 6204)}"
        if not os.environ.get("OM_URL"):
            OM_URL = f"http://127.0.0.1:{port_map.get('operator_manager', 6210)}"

        self.runtime = ServiceRuntime(
            name=name,
            port=component["port"],
            pulse_file=component["pulse_file"],
            log_dir=self.config["log_dir"],
        )

        self.runtime.register_route("GET",  "/health",          self.health)
        self.runtime.register_route("POST", "/task",            self.handle_task)
        self.runtime.register_route("GET",  "/tasks/{task_id}", self.get_task)
        self.runtime.register_route("GET",  "/tasks",           self.list_tasks)
        self.runtime.register_route("POST", "/outreach/approval/request",
                                    self._handle_outreach_approval_request)

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def health(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        return 200, {
            "ok": True,
            "service": "chief",
            "role": "orchestrator",
            "version": _VERSION,
            "crew_url": CREW_URL,
            "beacon_url": BEACON_URL,
        }

    # ------------------------------------------------------------------
    # Task dispatch
    # ------------------------------------------------------------------

    def handle_task(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        task_id  = uuid.uuid4().hex
        req      = TaskRequest.from_dict(payload)
        chat_id  = str(req.metadata.get("chat_id") or "")

        self.runtime.logger.info(
            "CHIEF received task [%s] from %s via %s",
            task_id, req.sender, req.source_channel,
        )

        # ── Step 0 — Slash command fast-path (100% accuracy, no LLM) ────────
        # /contact_N must come FIRST — not in COMMANDS dict, would otherwise be "unknown"
        contact_cmd = parse_contact_command(req.task)
        if contact_cmd is not None:
            reply_text = self._mark_lead_contacted(
                contact_cmd["row_id"], contact_cmd["status"], chat_id
            )
            return 200, TaskResponse(
                ok=True, task_id=task_id,
                selected_type="status", selected_target="/contact",
                reply_text=reply_text,
            ).to_dict()

        quote_cmd = parse_quote_command(req.task)
        if quote_cmd is not None:
            if not chat_id:
                return 200, TaskResponse(
                    ok=False, task_id=task_id, selected_type="none",
                    reply_text="❌ /quote_N requires a Telegram chat_id.",
                ).to_dict()
            threading.Thread(
                target=self._generate_quote_for_lead,
                args=(quote_cmd["row_id"], quote_cmd["description"], chat_id),
                daemon=True,
                name=f"chief-quote-{task_id[:8]}",
            ).start()
            return 200, TaskResponse(
                ok=True, task_id=task_id,
                selected_type="status", selected_target="/quote",
                reply_text=f"📄 Drafting proposal for lead #{quote_cmd['row_id']}. Stand by...",
            ).to_dict()

        approval_cmd = parse_approval_command(req.task)
        if approval_cmd is not None:
            reply_text = self._handle_approval(
                approval_cmd["action"], approval_cmd["row_id"], chat_id
            )
            return 200, TaskResponse(
                ok=True, task_id=task_id,
                selected_type="status", selected_target=f"/{approval_cmd['action']}",
                reply_text=reply_text,
            ).to_dict()

        parsed_cmd = parse_command(req.task)
        if parsed_cmd is not None:
            cmd = parsed_cmd["command"]
            if parsed_cmd.get("unknown"):
                reply_text = (
                    f"I don't know that command: {cmd}\n"
                    f"Try /help to see what's available."
                )
                return 200, TaskResponse(
                    ok=True, task_id=task_id, selected_type="none",
                    reply_text=reply_text,
                ).to_dict()
            if cmd == "/help":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=build_help_text(),
                ).to_dict()
            if cmd == "/operators":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=build_operators_text(OPERATOR_CATALOG),
                ).to_dict()
            if cmd == "/status":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._status_summary(),
                ).to_dict()
            if cmd == "/missions":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._missions_summary(),
                ).to_dict()
            if cmd == "/pipeline":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._pipeline_snapshot(),
                ).to_dict()
            if cmd == "/preview":
                if _is_prism(req.metadata):
                    return 200, TaskResponse(
                        ok=True, task_id=task_id,
                        selected_type="status", selected_target=cmd,
                        reply_text=self._preview_sync_for_prism(),
                    ).to_dict()
                if not chat_id:
                    return 200, TaskResponse(
                        ok=False, task_id=task_id, selected_type="none",
                        reply_text="❌ /preview requires a Telegram chat_id.",
                    ).to_dict()
                threading.Thread(
                    target=self._preview_and_notify,
                    args=(chat_id,),
                    daemon=True,
                    name=f"chief-preview-{task_id[:8]}",
                ).start()
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text="👁 Generating preview — stand by...",
                ).to_dict()
            if cmd == "/approve_all":
                if _is_prism(req.metadata):
                    return 200, TaskResponse(
                        ok=True, task_id=task_id,
                        selected_type="status", selected_target=cmd,
                        reply_text=self._approve_all_sync_for_prism(),
                    ).to_dict()
                if not chat_id:
                    return 200, TaskResponse(
                        ok=False, task_id=task_id, selected_type="none",
                        reply_text="❌ /approve_all requires a Telegram chat_id.",
                    ).to_dict()
                n_out = len(self._load_outreach_approvals())
                n_q   = len(self._load_approvals())
                if n_out + n_q == 0:
                    return 200, TaskResponse(
                        ok=True, task_id=task_id,
                        selected_type="status", selected_target=cmd,
                        reply_text="✅ Nothing pending — queue is empty.",
                    ).to_dict()
                threading.Thread(
                    target=self._approve_all_and_notify,
                    args=(chat_id,),
                    daemon=True,
                    name=f"chief-approveall-{task_id[:8]}",
                ).start()
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=f"⚙️ Approving {n_out + n_q} pending item(s)... Stand by.",
                ).to_dict()
            if cmd == "/outreach":
                if _is_prism(req.metadata):
                    return 200, TaskResponse(
                        ok=True, task_id=task_id,
                        selected_type="status", selected_target=cmd,
                        reply_text=self._outreach_sync_for_prism(),
                    ).to_dict()
                if not chat_id:
                    return 200, TaskResponse(
                        ok=False, task_id=task_id, selected_type="none",
                        reply_text="❌ /outreach requires a Telegram chat_id.",
                    ).to_dict()
                threading.Thread(
                    target=self._run_outreach_and_notify,
                    args=(chat_id,),
                    daemon=True,
                    name=f"chief-outreach-{task_id[:8]}",
                ).start()
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text="📋 Pulling top leads for outreach. Stand by...",
                ).to_dict()
            if cmd == "/send_outreach":
                if not chat_id:
                    return 200, TaskResponse(
                        ok=False, task_id=task_id, selected_type="none",
                        reply_text="❌ /send_outreach requires a Telegram chat_id.",
                    ).to_dict()
                threading.Thread(
                    target=self._run_send_outreach_and_notify,
                    args=(chat_id,),
                    daemon=True,
                    name=f"chief-send-outreach-{task_id[:8]}",
                ).start()
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text="📤 Drafting and sending to top leads. Stand by...",
                ).to_dict()
            if cmd == "/followups":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._followups_snapshot(),
                ).to_dict()
            if cmd == "/replies":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._replies_snapshot(),
                ).to_dict()
            if parsed_cmd["operator"]:
                target = parsed_cmd["operator"]
                self.runtime.logger.info(
                    "CHIEF command fast-path: %s → operator=%s", cmd, target
                )
                raw_result = self._dispatch_via_beacon(req, target, task_id)
                reply_text = self._format_reply(target, raw_result)
                append_history(chat_id, "user",      req.task)
                append_history(chat_id, "assistant", reply_text[:500])
                set_last_action(chat_id, "dispatch_operator", target, reply_text[:300], original_task=req.task)
                ok = "error" not in raw_result
                return 200, TaskResponse(
                    ok=ok, task_id=task_id,
                    selected_type="operator", selected_target=target,
                    reply_text=reply_text, raw_result=raw_result,
                ).to_dict()

        # ── A. Status commands — fast-path, not recorded in history ──────────
        selection = select_target(req.task, CREW_URL)
        if selection["selected_type"] == "status":
            reply_text = self._handle_status_command(selection["target"])
            resp = TaskResponse(
                ok=True, task_id=task_id,
                selected_type="status", selected_target=selection["target"],
                reply_text=reply_text,
            )
            return 200, resp.to_dict()

        # Record user message now (after status gate — we don't track /status etc.)
        append_history(chat_id, "user", req.task)

        # ── A2. Repeat fast-path — "do it again" without LLM ────────────────
        _REPEAT_PHRASES = frozenset({
            "do it again", "run it again", "run again", "repeat that",
            "again", "repeat", "same again", "do that again",
        })
        if req.task.lower().strip() in _REPEAT_PHRASES and chat_id:
            last = get_last_action(chat_id)
            if last and last.get("action") == "dispatch_operator" and last.get("target"):
                target = last["target"]
                original = last.get("original_task") or req.task
                self.runtime.logger.info(
                    "CHIEF repeat fast-path: repeating %s with original task=%r", target, original[:40]
                )
                # Replay with the original task so the operator recognizes the request
                repeat_req = TaskRequest(
                    task=original,
                    source_channel=req.source_channel,
                    reply_channel=req.reply_channel,
                    sender=req.sender,
                    tenant_id=req.tenant_id,
                    metadata=req.metadata,
                )
                raw_result = self._dispatch_via_beacon(repeat_req, target, task_id)
                reply_text = self._format_reply(target, raw_result)
                append_history(chat_id, "assistant", reply_text[:500])
                set_last_action(chat_id, "dispatch_operator", target, reply_text[:300], original_task=original)
                ok = "error" not in raw_result
                return 200, TaskResponse(
                    ok=ok, task_id=task_id,
                    selected_type="operator", selected_target=target,
                    reply_text=reply_text, raw_result=raw_result,
                ).to_dict()

        # ── B. Keyword fast-path (confidence >= 0.90, no LLM needed) ─────────
        kw_confidence = selection.get("confidence", 0.0)
        if selection["ok"] and kw_confidence >= 0.90:
            self.runtime.logger.info(
                "CHIEF keyword fast-path: target=%s confidence=%.2f",
                selection["target"], kw_confidence,
            )
            target     = selection["target"]
            raw_result = self._dispatch_via_beacon(req, target, task_id)
            reply_text = self._format_reply(target, raw_result)
            append_history(chat_id, "assistant", reply_text[:500])
            set_last_action(chat_id, "dispatch_operator", target, reply_text[:300], original_task=req.task)
            ok   = "error" not in raw_result
            resp = TaskResponse(
                ok=ok, task_id=task_id,
                selected_type="operator", selected_target=target,
                reply_text=reply_text, raw_result=raw_result,
            )
            return 200, resp.to_dict()

        # ── C. LLM intent classifier ── pass stored history ───────────────────
        history  = get_history(chat_id)
        decision = classify_intent(req.task, history, chat_id=chat_id)

        # ── D. Validate against catalog + policy ──────────────────────────────
        decision  = validate_routing_decision(decision)
        validated = decision.action not in ("conversation",) or decision.confidence > 0

        # ── E. Apply confidence thresholds ────────────────────────────────────
        if decision.action == "dispatch_operator":
            if decision.confidence < CONFIDENCE_DISPATCH:
                decision.action = "ask_clarification"
                if not decision.question:
                    decision.question = (
                        "I think I know what you need but I'm not sure — "
                        "could you give me a bit more detail?"
                    )
            elif CONFIDENCE_CLARIFY <= decision.confidence < CONFIDENCE_DISPATCH:
                decision.action = "ask_clarification"

        # ── Audit log ─────────────────────────────────────────────────────────
        self.runtime.logger.info(
            'INTENT_ROUTER | msg="%s" | action=%s | target=%s | '
            'confidence=%.2f | reason="%s" | validated=%s | final_action=%s',
            req.task[:50],
            decision.action,
            decision.target,
            decision.confidence,
            decision.reason[:60],
            "pass" if validated else "fail",
            decision.action,
        )

        # ── F. Dispatch operator ──────────────────────────────────────────────
        if decision.action == "dispatch_operator":
            target     = decision.target
            raw_result = self._dispatch_via_beacon(req, target, task_id)
            reply_text = self._format_reply(target, raw_result)
            append_history(chat_id, "assistant", reply_text[:500])
            set_last_action(chat_id, "dispatch_operator", target, reply_text[:300], original_task=req.task)
            ok   = "error" not in raw_result
            resp = TaskResponse(
                ok=ok, task_id=task_id,
                selected_type="operator", selected_target=target,
                reply_text=reply_text, raw_result=raw_result,
            )
            return 200, resp.to_dict()

        # ── G. Ask clarification ──────────────────────────────────────────────
        if decision.action == "ask_clarification":
            question = decision.question or "Could you give me a bit more detail?"
            append_history(chat_id, "assistant", question)
            set_last_action(chat_id, "conversation", None, question[:200])
            resp = TaskResponse(
                ok=True, task_id=task_id,
                selected_type="none",
                reply_text=question,
                raw_result={"reason": decision.reason, "missing": decision.missing_inputs},
            )
            return 200, resp.to_dict()

        # ── H. Multi-step plan ────────────────────────────────────────────────
        if decision.action == "multi_step_plan":
            targets    = decision.targets or []
            plan_lines = ["Here's my plan:"]
            for i, t in enumerate(targets, 1):
                plan_lines.append(f"  {i}. {t}")
            plan_lines.append("\nStarting with the first step now...")
            plan_summary = "\n".join(plan_lines)

            if targets:
                first_target = targets[0]
                raw_result   = self._dispatch_via_beacon(req, first_target, task_id)
                first_reply  = self._format_reply(first_target, raw_result)
                reply_text   = plan_summary + "\n\n" + first_reply
            else:
                reply_text = plan_summary

            append_history(chat_id, "assistant", reply_text[:500])
            set_last_action(chat_id, "dispatch_operator", targets[0] if targets else None, reply_text[:300])
            resp = TaskResponse(
                ok=True, task_id=task_id,
                selected_type="operator",
                selected_target=targets[0] if targets else None,
                reply_text=reply_text,
            )
            return 200, resp.to_dict()

        # ── I. Conversation / fallback ────────────────────────────────────────
        reply_text = intelligent_fallback(req.task, req.source_channel)
        append_history(chat_id, "assistant", reply_text[:500])
        set_last_action(chat_id, "conversation", None, reply_text[:200])
        resp = TaskResponse(
            ok=True, task_id=task_id,
            selected_type="none",
            reply_text=reply_text,
            raw_result={"intent_reason": decision.reason},
        )
        return 200, resp.to_dict()

    def get_task(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        return 200, {
            "ok": False,
            "message": "Durable task state not yet implemented in v1",
        }

    def list_tasks(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        return 200, {
            "ok": False,
            "message": "Task history not yet implemented in v1",
        }

    # ------------------------------------------------------------------
    # BEACON dispatch
    # ------------------------------------------------------------------

    def _wake_and_wait(self, target: str) -> bool:
        """Ask OM to wake a sleeping operator; poll its health until ready or timeout."""
        try:
            body = json.dumps({"reason": "dispatch_requested"}).encode()
            req = urllib.request.Request(
                f"{OM_URL}/operators/{target}/wake",
                data=body, method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                result = json.loads(r.read().decode())
            if not result.get("ok"):
                self.runtime.logger.warning(
                    "CHIEF: OM rejected wake for %s: %s", target, result
                )
                return False
        except Exception as exc:
            self.runtime.logger.warning(
                "CHIEF: OM unreachable for wake(%s): %s", target, exc
            )
            return False

        # Get the operator's port so we can poll its health directly (faster than
        # waiting for OM's 30s monitoring cycle to mark it running)
        op_port = None
        try:
            with urllib.request.urlopen(
                f"{OM_URL}/operators/{target}/status", timeout=3
            ) as r:
                op_port = json.loads(r.read().decode()).get("port")
        except Exception:
            pass

        self.runtime.logger.info(
            "CHIEF: waking %s (port %s) — waiting up to %ds",
            target, op_port, _WAKE_WAIT,
        )
        deadline = time.time() + _WAKE_WAIT
        while time.time() < deadline:
            if op_port:
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{op_port}/api/health", timeout=2
                    ) as r:
                        if r.status == 200:
                            time.sleep(2)  # allow CREW registration to complete
                            return True
                except Exception:
                    pass
            time.sleep(1)

        self.runtime.logger.warning(
            "CHIEF: %s did not become healthy within %ds", target, _WAKE_WAIT
        )
        return False

    # Fallback ports for operators that register dynamically with CREW.
    # Used when BEACON can't find the operator (duplicate CREW instances,
    # registration lag, or CREW restart clearing in-memory registry).
    _OPERATOR_FALLBACK_PORTS: dict[str, tuple[int, str]] = {
        "recon":       (8002, "/api/task"),
        "quote_brief": (8006, "/api/task"),
        "scout":       (7002, "/api/run"),
        "email":       (8010, "/api/task"),
        "debrief":     (8008, "/api/task"),
        "quote":       (8007, "/api/task"),
        "social":      (8011, "/api/task"),
    }

    def _dispatch_via_beacon(
        self, req: TaskRequest, target: str, task_id: str
    ) -> dict:
        message = {
            "task": req.task,
            "task_id": task_id,
            "source_channel": req.source_channel,
            "reply_channel": req.reply_channel,
            "sender": req.sender,
            "tenant_id": req.tenant_id,
            "metadata": req.metadata,
        }
        payload = {
            "sender": "chief",
            "message_type": "run.execute",
            "target": target,
            "message": message,
            "timeout": 60,
        }
        try:
            result = _http_post(f"{BEACON_URL}/route", payload, timeout=75)
            # BEACON wraps the operator response in forward_response
            if result.get("forwarded") is False:
                # BEACON couldn't find the operator port — fall back to direct dispatch
                self.runtime.logger.warning(
                    "CHIEF: BEACON could not forward to %s — trying direct dispatch", target
                )
                direct = self._dispatch_direct(target, message)
                if "error" not in direct:
                    return direct
                # direct failed — try wake+retry (operator may be sleeping/evicted from CREW)
                self.runtime.logger.info(
                    "CHIEF: %s not reachable — attempting wake via OM", target
                )
                if self._wake_and_wait(target):
                    retry = _http_post(f"{BEACON_URL}/route", payload, timeout=75)
                    if retry.get("forwarded") is not False:
                        return retry.get("forward_response") or retry
                    return self._dispatch_direct(target, message)
                return direct

            forward_resp = result.get("forward_response") or result
            # Target was found in CREW but its port returned 503 (sleeping process)
            if (
                result.get("forward_status") == 503
                and isinstance(forward_resp, dict)
                and "unreachable" in str(forward_resp.get("error", ""))
            ):
                self.runtime.logger.info(
                    "CHIEF: %s unreachable (503) — attempting wake via OM", target
                )
                if self._wake_and_wait(target):
                    retry = _http_post(f"{BEACON_URL}/route", payload, timeout=75)
                    if retry.get("forwarded") is not False:
                        return retry.get("forward_response") or retry
            return forward_resp
        except urllib.error.URLError as exc:
            self.runtime.logger.warning(
                "CHIEF: BEACON dispatch to %s failed: %s", target, exc
            )
            return {"error": f"worker timed out or unreachable: {exc.reason}", "target": target}
        except Exception as exc:
            self.runtime.logger.warning(
                "CHIEF: BEACON dispatch to %s error: %s", target, exc
            )
            return {"error": str(exc), "target": target}

    def _dispatch_direct(self, target: str, message: dict) -> dict:
        """Direct operator dispatch — used when BEACON can't find the operator port."""
        # Try CREW lookup first (handles dynamic registration)
        try:
            crew_data = _http_get(f"{CREW_URL}/crew", timeout=3)
            op = crew_data.get("operators", {}).get(target)
            if op and op.get("port"):
                port     = op["port"]
                task_hook = op.get("task_hook", "/api/task")
                self.runtime.logger.info(
                    "CHIEF direct dispatch: %s → port %d%s (via CREW)", target, port, task_hook
                )
                return _http_post(f"http://127.0.0.1:{port}{task_hook}", message, timeout=60)
        except Exception as exc:
            self.runtime.logger.warning("CHIEF: CREW lookup for %s failed: %s", target, exc)

        # Fall back to known static ports
        if target in self._OPERATOR_FALLBACK_PORTS:
            port, task_hook = self._OPERATOR_FALLBACK_PORTS[target]
            self.runtime.logger.info(
                "CHIEF direct dispatch: %s → port %d%s (fallback)", target, port, task_hook
            )
            try:
                return _http_post(f"http://127.0.0.1:{port}{task_hook}", message, timeout=60)
            except Exception as exc:
                return {"error": f"direct dispatch to {target} failed: {exc}"}

        return {"error": f"operator '{target}' not reachable — not in CREW and no fallback port"}

    # ------------------------------------------------------------------
    # Reply formatting
    # ------------------------------------------------------------------

    def _format_reply(self, target: str, result: dict) -> str:
        if "error" in result:
            err = str(result["error"]).lower()
            if any(x in err for x in (
                "unreachable", "connection refused", "did not wake",
                "not reachable", "timed out",
            )):
                return (
                    f"The {target} worker is starting up — this can take up to "
                    f"30 seconds on first use. Please try again in a moment."
                )
            return (
                f"Task could not be completed.\n"
                f"Worker: {target}\n"
                f"Reason: {result['error']}"
            )
        content = (
            result.get("result")
            or result.get("output")
            or result.get("summary")
            or result.get("text")
            or result.get("message")
            or result.get("data")
            or "Task completed."
        )
        if isinstance(content, dict):
            content = "\n".join(
                f"{k}: {v}" for k, v in list(content.items())[:5]
            )
        reply = str(content)
        return reply[:3500]

    # ------------------------------------------------------------------
    # Status command handlers
    # ------------------------------------------------------------------

    def _handle_status_command(self, command: str) -> str:
        if command == "/status":
            return self._status_summary()
        if command == "/missions":
            return self._missions_summary()
        if command == "/operators":
            return self._operators_summary()
        return self._help_text()

    def _status_summary(self) -> str:
        lines = ["Cascadia OS Status\n"]
        checks = [
            ("CREW",            CREW_URL + "/health"),
            ("BEACON",          BEACON_URL + "/health"),
            ("Mission Manager", MISSION_MANAGER_URL + "/healthz"),
            ("BELL",            BELL_URL + "/health"),
        ]
        for name, url in checks:
            try:
                _http_get(url, timeout=2)
                lines.append(f"  {name}: ready")
            except Exception:
                lines.append(f"  {name}: unreachable")
        try:
            crew_data = _http_get(CREW_URL + "/crew", timeout=2)
            n = crew_data.get("crew_size", "?")
            lines.append(f"  Operators registered: {n}")
        except Exception:
            pass

        stats = self._read_pipeline_stats()
        if stats:
            lines.append(
                "\n📋 OUTREACH PIPELINE\n"
                f"  Leads ready:    {stats['total']}\n"
                f"  Contacted:      {stats['contacted']}\n"
                f"  Pending:        {stats['pending']}\n"
                f"  Follow-ups due: {stats['followups_due']}\n"
                f"  Skipped:        {stats['skipped']}\n"
                f"  Exhausted:      {stats['exhausted']}"
            )
        else:
            lines.append("\n📋 Pipeline data unavailable")
        return "\n".join(lines)

    def _read_pipeline_stats(self) -> dict | None:
        """Read outreach_ready.csv → pipeline KPIs. None on any error."""
        import csv
        from datetime import datetime, timezone
        csv_path = os.path.join(self._OUTPUT_DIR, "outreach_ready.csv")
        try:
            with open(csv_path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        except Exception:
            return None

        def _n(v):
            return sum(1 for r in rows if (r.get("contacted") or "").strip() == v)

        now = datetime.now(timezone.utc)
        due = 0
        for r in rows:
            d = (r.get("followup_due_at") or "").strip()
            if not d or (r.get("contacted") or "").strip() != "yes":
                continue
            try:
                if datetime.fromisoformat(d) <= now:
                    due += 1
            except ValueError:
                pass
        return {
            "total": len(rows), "contacted": _n("yes"), "pending": _n("pending"),
            "skipped": _n("skipped"), "exhausted": _n("exhausted"),
            "followups_due": due,
        }

    def _missions_summary(self) -> str:
        try:
            data = _http_get(
                f"{MISSION_MANAGER_URL}/api/missions/runs?limit=5", timeout=5
            )
            runs = data.get("runs", [])
            if not runs:
                return "No recent mission runs."
            lines = ["Recent mission runs:\n"]
            for r in runs:
                lines.append(
                    f"  {r.get('id','?')[:8]}  {r.get('status','?')}  "
                    f"{r.get('started_at','?')[:16]}"
                )
            return "\n".join(lines)
        except Exception:
            return "Mission Manager unreachable."

    def _help_text(self) -> str:
        return (
            "Cascadia OS — Commands:\n\n"
            "/status     System health\n"
            "/missions   Recent mission runs\n"
            "/operators  Registered workers\n"
            "/help       This message\n\n"
            "Or describe your task naturally:\n"
            "  \"Find leads for HVAC contractors in Houston\"\n"
            "  \"Draft a proposal for warehouse installation\"\n"
            "  \"Research competitor pricing\""
        )

    def _operators_summary(self) -> str:
        try:
            data = _http_get(CREW_URL + "/crew", timeout=3)
            operators = data.get("operators") or {}
            if not operators:
                return "No operators currently registered."
            lines = [f"Registered operators ({data.get('crew_size', 0)}):\n"]
            for op_id, op in operators.items():
                caps = op.get("capabilities", [])[:3]
                cap_str = ", ".join(caps) if caps else "—"
                lines.append(f"  {op_id}: {cap_str}")
            summary = "\n".join(lines)
            return summary[:3500]
        except Exception:
            return "CREW unreachable — cannot list operators."

    # ------------------------------------------------------------------
    # Pipeline snapshot + outreach dispatch
    # ------------------------------------------------------------------

    # Resolve paths from the runtime user's home so this works on any host
    # (was hardcoded to /Users/andy, which does not exist on every node).
    _OUTPUT_DIR     = os.path.join(
        os.path.expanduser("~"),
        "Zyrcon", "operators", "cascadia-os-operators", "recon", "output",
    )
    _CSV_PATH       = os.path.join(_OUTPUT_DIR, "houston_contractors.csv")
    _REPLIES_PATH   = os.path.join(_OUTPUT_DIR, "replies.json")
    _APPROVALS_FILE = os.path.join(_OUTPUT_DIR, "pending_quotes.json")
    _OUTREACH_FILE  = os.path.join(_OUTPUT_DIR, "pending_outreach.json")
    _RECON_URL      = "http://127.0.0.1:8002"
    _approvals_lock = threading.Lock()
    _outreach_lock  = threading.Lock()

    def _pipeline_snapshot(self) -> str:
        """Read houston_contractors.csv and return a formatted pipeline snapshot."""
        import csv
        from pathlib import Path
        csv_path = Path(self._CSV_PATH)
        if not csv_path.exists():
            return "📊 No lead data found — run /recon first."
        try:
            rows = list(csv.DictReader(csv_path.open(newline="", encoding="utf-8")))
        except Exception as exc:
            return f"📊 Could not read lead data: {exc}"

        def _s(r, k, default=""):
            v = r.get(k) or default
            return str(v).strip().lower()

        total          = len(rows)
        high           = sum(1 for r in rows if _s(r, "confidence") == "high")
        medium         = sum(1 for r in rows if _s(r, "confidence") == "medium")
        contacted      = sum(1 for r in rows if _s(r, "contacted") == "yes")
        not_interested = sum(1 for r in rows if _s(r, "contacted") == "not_interested")
        pending        = sum(1 for r in rows if _s(r, "contacted") == "pending")
        replied        = sum(1 for r in rows if _s(r, "reply_received") == "yes")
        quoted         = sum(1 for r in rows if _s(r, "deal_stage") == "quoted")
        uncontacted    = sum(
            1 for r in rows
            if _s(r, "contacted") not in ("yes", "not_interested", "pending")
        )
        top5 = [
            r for r in rows
            if _s(r, "confidence") == "high"
            and _s(r, "phone")
            and _s(r, "contacted") not in ("yes", "not_interested", "pending")
        ][:5]

        email_high     = sum(1 for r in rows if _s(r, "email_quality") == "high")
        email_medium   = sum(1 for r in rows if _s(r, "email_quality") == "medium")
        email_inferred = sum(1 for r in rows if _s(r, "email_quality") == "inferred")
        email_none     = total - email_high - email_medium - email_inferred

        lines = [
            "📊 Lead Pipeline — Houston Contractors\n",
            f"Total: {total} | High confidence: {high} | Medium: {medium}\n",
            f"✅ Contacted:        {contacted}",
            f"❌ Not interested:   {not_interested}",
            f"⏳ Pending outreach: {pending}",
            f"📋 Uncontacted:      {uncontacted}",
            f"📩 Replied:          {replied}",
            f"📄 Quoted:           {quoted}\n",
            f"📧 Emails — named: {email_high} | generic: {email_medium} | inferred: {email_inferred} | none: {email_none}",
        ]
        if top5:
            lines.append("\nTop uncontacted (high confidence):")
            for i, r in enumerate(top5, 1):
                email_tag = f" | 📧 {r['email']}" if r.get("email") else ""
                lines.append(f"{i}. {r['business_name']} — {r.get('phone', 'no phone')}{email_tag}")
        lines.append("\nRun /outreach to brief these leads.")
        return "\n".join(lines)

    def _followups_snapshot(self) -> str:
        """Read outreach_ready.csv and list contacted leads whose follow-up is
        due now (contacted==yes AND followup_due_at <= now)."""
        import csv as _csv
        import os
        from datetime import datetime, timezone
        path = os.path.join(self._OUTPUT_DIR, "outreach_ready.csv")
        try:
            rows = list(_csv.DictReader(open(path, newline="", encoding="utf-8")))
        except Exception as exc:
            return f"📬 Could not read outreach_ready.csv: {exc}"

        now = datetime.now(timezone.utc)
        due: list[tuple[dict, str]] = []
        for r in rows:
            if (r.get("contacted") or "").strip().lower() != "yes":
                continue
            due_str = (r.get("followup_due_at") or "").strip()
            if not due_str:
                continue
            try:
                if datetime.fromisoformat(due_str) <= now:
                    due.append((r, due_str))
            except ValueError:
                continue

        if not due:
            return "✅ No follow-ups due right now.\nPULSE checks daily at 9am."

        lines = ["⏰ FOLLOW-UPS DUE\n"]
        for r, due_str in due:
            lines.append(f"• {r.get('business_name','')} ({r.get('city','')})")
            lines.append(f"  Due: {due_str[:10]}")
        lines.append("──────────────────")
        lines.append("Run /outreach to queue follow-up drafts for approval.")
        return "\n".join(lines)

    def _replies_snapshot(self) -> str:
        """Read replies.json and return a formatted summary of recent replies."""
        from pathlib import Path
        path = Path(self._REPLIES_PATH)
        if not path.exists():
            return "📩 No replies yet. Leads appear here when they reply to outreach emails."
        try:
            records = json.loads(path.read_text())
        except Exception as exc:
            return f"📩 Could not load replies: {exc}"
        if not records:
            return "📩 No replies yet."
        lines = [f"📩 Inbox Replies ({len(records)} total)\n"]
        for r in reversed(records[-10:]):
            ts    = (r.get("detected_at") or "")[:16].replace("T", " ")
            biz   = r.get("business_name", "?")
            email = r.get("from_email", "")
            subj  = (r.get("subject") or "")[:60]
            idx   = r.get("row_idx", "")
            lines.append(f"• {biz} <{email}>")
            lines.append(f"  \"{subj}\"  {ts} UTC")
            if idx != "":
                lines.append(f"  → /quote_{idx} to draft a proposal")
            lines.append("")
        return "\n".join(lines).strip()

    # ------------------------------------------------------------------
    # Pending quote store
    # ------------------------------------------------------------------

    def _load_approvals(self) -> dict:
        from pathlib import Path
        path = Path(self._APPROVALS_FILE)
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}

    def _save_approvals(self, data: dict) -> None:
        from pathlib import Path
        Path(self._APPROVALS_FILE).write_text(json.dumps(data, indent=2))

    def _store_pending_quote(self, row_id: str, entry: dict) -> None:
        with self._approvals_lock:
            data = self._load_approvals()
            data[row_id] = entry
            self._save_approvals(data)

    # ------------------------------------------------------------------
    # Pending outreach store (mirror of the quote store, separate file)
    # ------------------------------------------------------------------

    def _load_outreach_approvals(self) -> dict:
        from pathlib import Path
        path = Path(self._OUTREACH_FILE)
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}

    def _save_outreach_approvals(self, data: dict) -> None:
        from pathlib import Path
        Path(self._OUTREACH_FILE).write_text(json.dumps(data, indent=2))

    def _is_outreach_actioned(self, row_id: str) -> bool:
        """True if the lead at row_id in outreach_ready.csv has a non-empty
        'contacted' value (yes/skipped/exhausted/pending) — i.e. it was already
        handled, so an approve/reject for it is a no-op rather than a quote."""
        import csv
        try:
            idx = int(row_id)
        except (TypeError, ValueError):
            return False
        path = os.path.join(self._OUTPUT_DIR, "outreach_ready.csv")
        try:
            with open(path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        except Exception:
            return False
        if 0 <= idx < len(rows):
            return bool((rows[idx].get("contacted") or "").strip())
        return False

    def _handle_outreach_approval_request(
        self, payload: Dict[str, Any]
    ) -> tuple[int, Dict[str, Any]]:
        """Inbound from RECON: stage an outreach draft, post the approval
        prompt to Telegram. The actual email send happens later on /approve_N.
        """
        from datetime import datetime, timezone

        row_id = str(payload.get("row_id", "")).strip()
        if not row_id:
            return 400, {"ok": False, "error": "row_id required"}

        biz     = payload.get("business_name", f"lead #{row_id}")
        email   = payload.get("email", "")
        btype   = payload.get("business_type", "contractor")
        city    = payload.get("city", "")
        subject = payload.get("subject", "")
        body    = payload.get("body", "")
        chat_id = str(payload.get("chat_id", ""))
        # Originating operator gets the approve/reject callback. Defaults to
        # RECON for backward compatibility (first-touch outreach). PULSE passes
        # its own URL so follow-up bookkeeping runs in PULSE, not RECON.
        source_url = (payload.get("source_url") or self._RECON_URL).rstrip("/")
        kind       = payload.get("kind") or "outreach"

        entry = {
            "row_id":        row_id,
            "business_name": biz,
            "email":         email,
            "business_type": btype,
            "city":          city,
            "subject":       subject,
            "body":          body,
            "chat_id":       chat_id,
            "source_url":    source_url,
            "kind":          kind,
            "created_at":    datetime.now(timezone.utc).isoformat(),
        }
        with self._outreach_lock:
            data = self._load_outreach_approvals()
            data[row_id] = entry
            self._save_outreach_approvals(data)

        header = (
            "🔁 FOLLOW-UP DRAFT — approval needed" if kind == "followup"
            else "📋 OUTREACH DRAFT — approval needed"
        )
        msg = (
            f"{header}\n\n"
            f"🏢 {biz}\n"
            f"🔧 {btype} | {city}\n"
            f"📧 {email}\n\n"
            f"Subject: {subject}\n\n"
            f"{body}\n\n"
            "──────────────────────────\n"
            f"/approve_{row_id} ✅ Send this email\n"
            f"/reject_{row_id} ❌ Skip this lead"
        )
        if chat_id:
            try:
                _http_post(
                    f"{TELEGRAM_URL}/send",
                    {"chat_id": chat_id, "text": msg},
                    timeout=5,
                )
            except Exception as exc:
                self.runtime.logger.warning(
                    "CHIEF outreach approval Telegram post failed: %s", exc
                )

        return 200, {"ok": True, "queued": True, "row_id": row_id}

    # ------------------------------------------------------------------
    # Quote generation + approval
    # ------------------------------------------------------------------

    def _generate_quote_for_lead(self, row_id: str, description: str, chat_id: str) -> None:
        """Background: read lead → call quote operator → store + preview via Telegram."""
        import csv
        from pathlib import Path
        from datetime import datetime, timezone

        def _tg(text: str) -> None:
            try:
                _http_post(
                    f"{TELEGRAM_URL}/send",
                    {"chat_id": chat_id, "text": text},
                    timeout=5,
                )
            except Exception:
                pass

        try:
            rows = list(csv.DictReader(Path(self._CSV_PATH).open(newline="", encoding="utf-8")))
        except Exception as exc:
            _tg(f"❌ Could not read lead data: {exc}")
            return

        try:
            idx = int(row_id)
        except ValueError:
            _tg(f"❌ Invalid row ID: {row_id!r}")
            return

        if idx < 0 or idx >= len(rows):
            _tg(f"❌ Lead #{row_id} not found (only {len(rows)} rows).")
            return

        lead  = rows[idx]
        biz   = lead.get("business_name", "").strip() or f"Lead #{row_id}"
        email = lead.get("email", "").strip()
        btype = (lead.get("business_type") or lead.get("notes") or "contractor").strip()[:60]
        city  = lead.get("city", "Houston").strip() or "Houston"
        phone = lead.get("phone", "").strip()
        scope = description or f"Pipeline management and lead generation for {btype} contractors in {city}"

        try:
            result = _http_post(
                "http://127.0.0.1:8007/api/task",
                {
                    "task": scope,
                    "context": {
                        "company_name":  biz,
                        "contact_email": email,
                        "contact_name":  "",
                        "opportunity":   scope,
                    },
                },
                timeout=30,
            )
        except Exception as exc:
            _tg(f"❌ Quote operator unreachable: {exc}")
            return

        proposal = result.get("result") or {}
        if not proposal:
            _tg("❌ Quote operator returned no proposal.")
            return

        budget   = proposal.get("budget", "TBD")
        sections = proposal.get("sections") or {}

        body_parts = ["Hi there,\n\nThank you for getting back to us.\n"]
        if sections.get("scope"):
            body_parts.append(str(sections["scope"]))
        if sections.get("approach"):
            body_parts.append(str(sections["approach"]))
        if budget and budget != "TBD":
            body_parts.append(f"Investment: {budget}")
        if sections.get("next_steps"):
            body_parts.append(str(sections["next_steps"]))
        else:
            body_parts.append("Worth a quick call to walk through the details?")
        body_parts.append("\nAndy | Zyrcon Labs")
        email_body    = "\n\n".join(body_parts)
        email_subject = f"Proposal — Zyrcon Labs for {biz}"

        entry = {
            "row_id":        row_id,
            "business_name": biz,
            "email":         email,
            "phone":         phone,
            "proposal_id":   proposal.get("id", ""),
            "subject":       email_subject,
            "body":          email_body,
            "budget":        str(budget),
            "created_at":    datetime.now(timezone.utc).isoformat(),
        }
        self._store_pending_quote(row_id, entry)

        scope_preview = ""
        if sections.get("scope"):
            scope_preview = f"\n\n{str(sections['scope'])[:200]}"

        _tg(
            f"📄 Proposal ready — {biz}\n"
            f"📧 To: {email or '(no email)'}\n"
            f"💰 Budget: {budget}"
            f"{scope_preview}\n\n"
            f"Subject: {email_subject}\n\n"
            f"{email_body[:500]}\n\n"
            f"/approve_{row_id} ✅ Send  |  /reject_{row_id} ❌ Discard"
        )

    def _handle_outreach_decision(
        self, action: str, row_id: str, entry: dict, chat_id: str
    ) -> str:
        """Handle /approve_N or /reject_N for a staged OUTREACH draft.
        On approve: send via the email operator, then tell RECON to mark the
        lead contacted. On reject: tell RECON the lead was skipped.
        """
        biz   = entry.get("business_name", f"lead #{row_id}")
        email = entry.get("email", "")
        # Call back the originating operator (RECON for first-touch, PULSE for
        # follow-ups). Falls back to RECON for entries staged before this field.
        source_url = (entry.get("source_url") or self._RECON_URL).rstrip("/")

        def _drop() -> None:
            with self._outreach_lock:
                data = self._load_outreach_approvals()
                data.pop(row_id, None)
                self._save_outreach_approvals(data)

        if action == "reject":
            try:
                _http_post(
                    f"{source_url}/api/outreach/skipped",
                    {"row_id": row_id},
                    timeout=10,
                )
            except Exception as exc:
                self.runtime.logger.warning(
                    "CHIEF outreach skipped callback failed: %s", exc
                )
            _drop()
            return f"⏭ Skipped {biz}"

        # approve — send the email
        if not email:
            return f"❌ No email address for {biz} — cannot send."
        try:
            send_result = _http_post(
                "http://127.0.0.1:8010/api/task",
                {
                    "to":       email,
                    "subject":  entry.get("subject", ""),
                    "body":     entry.get("body", ""),
                    "reply_to": "hello@zyrcon.ai",
                },
                timeout=20,
            )
        except Exception as exc:
            return f"❌ Email operator unreachable: {exc}"
        if not send_result.get("ok"):
            return f"❌ Email send failed: {send_result.get('error', 'unknown')}"

        try:
            _http_post(
                f"{source_url}/api/outreach/approved",
                {"row_id": row_id},
                timeout=10,
            )
        except Exception as exc:
            self.runtime.logger.warning(
                "CHIEF outreach approved callback failed: %s", exc
            )
        _drop()
        return f"✅ Email sent to {biz} ({email})"

    def _handle_approval(self, action: str, row_id: str, chat_id: str) -> str:
        """Handle /approve_N or /reject_N. Returns reply text.

        Outreach drafts and quote proposals share the row_id keyspace, so check
        the outreach store FIRST; if the row_id isn't there, fall through to the
        quote-approval logic below.
        """
        import csv
        from pathlib import Path
        from datetime import datetime, timezone

        with self._outreach_lock:
            outreach_entry = self._load_outreach_approvals().get(row_id)
        if outreach_entry is not None:
            return self._handle_outreach_decision(
                action, row_id, outreach_entry, chat_id
            )

        with self._approvals_lock:
            data  = self._load_approvals()
            entry = data.get(row_id)

        if not entry:
            if self._is_outreach_actioned(row_id):
                return (
                    f"ℹ️ Lead #{row_id} was already handled "
                    "(outreach previously approved or skipped)."
                )
            return (
                f"⚠️ No pending item for lead #{row_id}. "
                "It may have already been sent or discarded."
            )

        biz = entry.get("business_name", f"lead #{row_id}")

        if action == "reject":
            with self._approvals_lock:
                data = self._load_approvals()
                data.pop(row_id, None)
                self._save_approvals(data)
            return f"❌ Proposal discarded — {biz}."

        # approve — send email
        email   = entry.get("email", "")
        subject = entry.get("subject", "Proposal — Zyrcon Labs")
        body    = entry.get("body", "")

        if not email:
            return f"❌ No email address for {biz} — cannot send proposal."

        try:
            send_result = _http_post(
                "http://127.0.0.1:8010/api/task",
                {"to": email, "subject": subject, "body": body},
                timeout=20,
            )
        except Exception as exc:
            return f"❌ Email operator unreachable: {exc}"

        if not send_result.get("ok"):
            err = send_result.get("error", "unknown")
            return f"❌ Email send failed: {err}"

        # Mark deal_stage=quoted in CSV
        try:
            csv_path = Path(self._CSV_PATH)
            rows     = list(csv.DictReader(csv_path.open(newline="", encoding="utf-8")))
            idx      = int(row_id)
            if 0 <= idx < len(rows):
                fieldnames = list(rows[0].keys())
                if "quoted_at" not in fieldnames:
                    fieldnames.append("quoted_at")
                    for r in rows:
                        r.setdefault("quoted_at", "")
                rows[idx]["deal_stage"] = "quoted"
                rows[idx]["quoted_at"]  = datetime.now(timezone.utc).isoformat()
                with csv_path.open("w", newline="", encoding="utf-8") as f:
                    w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                    w.writeheader()
                    w.writerows(rows)
        except Exception as exc:
            self.runtime.logger.error("CHIEF: mark_quoted CSV update failed: %s", exc)

        with self._approvals_lock:
            data = self._load_approvals()
            data.pop(row_id, None)
            self._save_approvals(data)

        return f"✅ Proposal sent to {biz} at {email}.\nDeal stage: quoted."

    def _mark_lead_contacted(self, row_id: str, status: str, chat_id: str) -> str:
        """Call RECON /api/contact and return a user-facing reply."""
        _LABELS = {
            "yes": "✅ Contacted",
            "not_interested": "❌ Not interested",
            "pending": "⏳ Pending",
        }
        try:
            result = _http_post(
                f"{self._RECON_URL}/api/contact",
                {"row_id": row_id, "status": status},
                timeout=10,
            )
            if result.get("ok"):
                biz   = result.get("business_name", f"lead {row_id}")
                label = _LABELS.get(status, status)
                return f"{label}: {biz} marked."
            return f"❌ Could not mark lead: {result.get('error', 'unknown error')}"
        except Exception as exc:
            self.runtime.logger.error("CHIEF: mark_contacted failed: %s", exc)
            return f"❌ Could not reach RECON to mark lead: {exc}"

    # ------------------------------------------------------------------
    # PRISM synchronous variants (source=="prism")
    # Same work as the Telegram threaded handlers, but the full result is
    # returned to the caller as a string instead of pushed to Telegram.
    # ------------------------------------------------------------------

    def _preview_sync_for_prism(self) -> str:
        """Sync /preview for PRISM: GET a draft preview from RECON (read-only —
        stages nothing, marks nothing) and return it as text."""
        try:
            result = _http_post(f"{self._RECON_URL}/api/preview", {}, timeout=45)
        except Exception as exc:
            return f"❌ Preview unavailable: {exc}"
        if not result.get("ok"):
            return ("No leads available for preview. "
                    "RECON is finding more — check back soon.")
        return (
            "👁 OUTREACH PREVIEW\n\n"
            f"Business: {result.get('business_name','')}\n"
            f"Type: {result.get('business_type','')} | {result.get('city','')}\n"
            f"To: {result.get('email','')}\n\n"
            f"Subject: {result.get('subject','')}\n\n"
            f"{result.get('body','')}\n\n"
            "──────────────────────────\n"
            "This is a preview only.\n"
            "To queue for approval run /outreach"
        )

    def _outreach_sync_for_prism(self) -> str:
        """Sync /outreach for PRISM: trigger RECON to draft + stage one outreach
        lead, wait for RECON's callback to land the draft in pending_outreach.json,
        then return the staged draft as text. RECON drafts asynchronously, so we
        poll for the new entry (LLM draft + callback take a few seconds)."""
        with self._outreach_lock:
            before = set(self._load_outreach_approvals().keys())
        try:
            _http_post(
                f"{self._RECON_URL}/api/outreach",
                {"chat_id": "prism", "limit": 1},
                timeout=10,
            )
        except Exception as exc:
            self.runtime.logger.error("CHIEF prism outreach dispatch failed: %s", exc)
            return f"❌ Could not start outreach: {exc}"

        # Poll for the newly staged draft. Kept under the PRISM proxy's 30s
        # request timeout.
        deadline = time.time() + 20
        new_entry = None
        while time.time() < deadline:
            with self._outreach_lock:
                data = self._load_outreach_approvals()
            new_keys = set(data.keys()) - before
            if new_keys:
                row_id    = sorted(new_keys)[0]
                new_entry = {**data[row_id], "row_id": row_id}
                break
            time.sleep(1)

        if not new_entry:
            return (
                "📋 Outreach started, but no draft was staged yet — RECON may "
                "still be drafting or no eligible leads were available. "
                "Try /preview or run /outreach again shortly."
            )

        row_id = new_entry.get("row_id", "")
        return (
            "📋 DRAFT QUEUED FOR APPROVAL\n\n"
            f"Business: {new_entry.get('business_name','')}\n"
            f"Type: {new_entry.get('business_type','')} | {new_entry.get('city','')}\n"
            f"To: {new_entry.get('email','')}\n\n"
            f"Subject: {new_entry.get('subject','')}\n\n"
            f"{new_entry.get('body','')}\n\n"
            "──────────────────────────\n"
            "Draft is queued.\n"
            f"Type /approve_all to send or /reject_{row_id} to skip."
        )

    def _approve_all_sync_for_prism(self) -> str:
        """Sync /approve_all for PRISM: approve every pending outreach draft, then
        every pending quote, synchronously. No thread spawn, no Telegram pushes —
        a summary string is returned. One failure is logged and skipped."""
        with self._outreach_lock:
            outreach = dict(self._load_outreach_approvals())
        with self._approvals_lock:
            quotes = dict(self._load_approvals())

        if not outreach and not quotes:
            return "✅ Nothing pending — queue is empty."

        approved: list[str] = []
        failed:   list[str] = []

        # Outreach first (each decision pops its own entry from the store).
        for row_id, entry in outreach.items():
            biz = entry.get("business_name", f"lead #{row_id}")
            try:
                res = self._handle_outreach_decision("approve", row_id, entry, "prism")
                if res.startswith("✅"):
                    approved.append(biz)
                else:
                    failed.append(f"{biz}: {res}")
            except Exception as exc:
                self.runtime.logger.error(
                    "prism approve_all outreach %s failed: %s", row_id, exc
                )
                failed.append(f"{biz}: {exc}")

        # Quotes next (outreach store now drained, so _handle_approval routes
        # these to the quote branch).
        for row_id, entry in quotes.items():
            biz = entry.get("business_name", f"quote #{row_id}")
            try:
                res = self._handle_approval("approve", row_id, "prism")
                if res.startswith("✅"):
                    approved.append(biz)
                else:
                    failed.append(f"{biz}: {res}")
            except Exception as exc:
                self.runtime.logger.error(
                    "prism approve_all quote %s failed: %s", row_id, exc
                )
                failed.append(f"{biz}: {exc}")

        lines: list[str] = []
        if approved:
            lines.append(f"✅ Approved {len(approved)} item(s):")
            lines.extend(f"• {b}" for b in approved)
        else:
            lines.append("⚠️ Nothing was approved.")
        if failed:
            lines.append("")
            lines.append(f"⚠️ {len(failed)} failed:")
            lines.extend(f"• {f}" for f in failed)
        return "\n".join(lines)

    def _preview_and_notify(self, chat_id: str) -> None:
        """Background: GET a draft preview from RECON and post it to Telegram.
        Read-only — RECON's /api/preview stages nothing and marks nothing."""
        def _tg(text: str) -> None:
            try:
                _http_post(f"{TELEGRAM_URL}/send",
                           {"chat_id": chat_id, "text": text}, timeout=5)
            except Exception:
                pass
        try:
            result = _http_post(f"{self._RECON_URL}/api/preview", {}, timeout=45)
        except Exception as exc:
            _tg(f"❌ Preview unavailable: {exc}")
            return
        if not result.get("ok"):
            _tg("No leads available for preview. "
                "RECON is finding more — check back soon.")
            return
        _tg(
            "👁 OUTREACH PREVIEW\n\n"
            f"Business: {result.get('business_name','')}\n"
            f"Type: {result.get('business_type','')} | {result.get('city','')}\n"
            f"To: {result.get('email','')}\n\n"
            f"Subject: {result.get('subject','')}\n\n"
            f"{result.get('body','')}\n\n"
            "──────────────────────────\n"
            "This is a preview only — nothing has been queued.\n"
            "To queue this lead for approval run /outreach"
        )

    def _approve_all_and_notify(self, chat_id: str) -> None:
        """Background: approve every pending outreach draft (then every pending
        quote), posting one Telegram message per item plus a final summary.
        One failure is logged and skipped — the batch continues."""
        import time as _time

        def _tg(text: str) -> None:
            try:
                _http_post(f"{TELEGRAM_URL}/send",
                           {"chat_id": chat_id, "text": text}, timeout=5)
            except Exception:
                pass

        approved: list[str] = []

        # Outreach first (snapshot keys; each decision pops its own entry).
        with self._outreach_lock:
            outreach = dict(self._load_outreach_approvals())
        for row_id, entry in outreach.items():
            biz = entry.get("business_name", f"lead #{row_id}")
            try:
                _tg(self._handle_outreach_decision("approve", row_id, entry, chat_id))
                approved.append(biz)
            except Exception as exc:
                self.runtime.logger.error("approve_all outreach %s failed: %s", row_id, exc)
                _tg(f"❌ Failed to approve {biz}: {exc}")
            _time.sleep(0.5)

        # Quotes next (outreach store now drained, so _handle_approval routes
        # these to the quote branch).
        with self._approvals_lock:
            quotes = dict(self._load_approvals())
        for row_id, entry in quotes.items():
            biz = entry.get("business_name", f"quote #{row_id}")
            try:
                _tg(self._handle_approval("approve", row_id, chat_id))
                approved.append(biz)
            except Exception as exc:
                self.runtime.logger.error("approve_all quote %s failed: %s", row_id, exc)
                _tg(f"❌ Failed to approve {biz}: {exc}")
            _time.sleep(0.5)

        if approved:
            _tg("✅ Approved {} item(s):\n{}".format(
                len(approved), "\n".join(f"• {b}" for b in approved)))
        else:
            _tg("✅ Nothing pending — queue is empty.")

    def _run_outreach_and_notify(self, chat_id: str) -> None:
        """Background: POST to RECON /api/outreach with chat_id."""
        try:
            result = _http_post(
                f"{self._RECON_URL}/api/outreach",
                {"chat_id": chat_id, "limit": 5},
                timeout=10,
            )
            self.runtime.logger.info("CHIEF outreach started: %s", result)
        except Exception as exc:
            self.runtime.logger.error("CHIEF outreach dispatch failed: %s", exc)
            try:
                _http_post(
                    f"{TELEGRAM_URL}/send",
                    {"chat_id": chat_id, "text": f"❌ Could not start outreach: {exc}"},
                    timeout=5,
                )
            except Exception:
                pass

    def _run_send_outreach_and_notify(self, chat_id: str) -> None:
        """Background: POST to RECON /api/send_outreach — drafts and sends emails."""
        try:
            result = _http_post(
                f"{self._RECON_URL}/api/send_outreach",
                {"chat_id": chat_id, "limit": 5},
                timeout=10,
            )
            self.runtime.logger.info("CHIEF send_outreach started: %s", result)
        except Exception as exc:
            self.runtime.logger.error("CHIEF send_outreach dispatch failed: %s", exc)
            try:
                _http_post(
                    f"{TELEGRAM_URL}/send",
                    {"chat_id": chat_id, "text": f"❌ Could not start send_outreach: {exc}"},
                    timeout=5,
                )
            except Exception:
                pass

    # ------------------------------------------------------------------
    # CREW self-registration
    # ------------------------------------------------------------------

    def _try_register_with_crew(self, retries: int = 3) -> None:
        payload = json.dumps({
            "operator_id": "chief",
            "name": "chief",
            "display_name": "CHIEF",
            "description": (
                "Zyrcon orchestrator — task routing and worker assignment"
            ),
            "port": CHIEF_PORT,
            "type": "system",
            "autonomy_level": "autonomous",
            "capabilities": [
                "task.orchestrate",
                "run.execute",
                "mission.select",
                "operator.assign",
                "report.request",
            ],
            "health_hook": "/health",
        }).encode()
        for attempt in range(retries):
            try:
                req = urllib.request.Request(
                    f"{CREW_URL}/register",
                    data=payload,
                    method="POST",
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=3)
                self.runtime.logger.info("CHIEF registered with CREW")
                return
            except Exception as exc:
                self.runtime.logger.warning(
                    "CHIEF: CREW registration attempt %d/%d failed: %s",
                    attempt + 1, retries, exc,
                )
                if attempt < retries - 1:
                    time.sleep(2)
        self.runtime.logger.warning(
            "CHIEF: CREW registration failed after %d attempts", retries
        )

    # ------------------------------------------------------------------
    # Start
    # ------------------------------------------------------------------

    def start(self) -> None:
        threading.Thread(
            target=self._try_register_with_crew, daemon=True, name="chief-crew-reg"
        ).start()
        self.runtime.logger.info(
            "CHIEF orchestrator active — port %d | crew=%s | beacon=%s",
            CHIEF_PORT, CREW_URL, BEACON_URL,
        )
        self.runtime.start()


def main() -> None:
    p = argparse.ArgumentParser(description="CHIEF - Cascadia OS task orchestrator")
    p.add_argument("--config", required=True)
    p.add_argument("--name", required=True)
    a = p.parse_args()
    ChiefService(a.config, a.name).start()


if __name__ == "__main__":
    main()
