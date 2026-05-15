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
from cascadia.chief.commands import parse_command, build_help_text, build_operators_text
from cascadia.chief.intent_router import (
    classify_intent,
    validate_routing_decision,
    append_history,
    get_history,
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
        global CREW_URL, BEACON_URL, MISSION_MANAGER_URL, BELL_URL
        if not os.environ.get("CREW_URL"):
            CREW_URL = f"http://127.0.0.1:{port_map.get('crew', 5100)}"
        if not os.environ.get("BEACON_URL"):
            BEACON_URL = f"http://127.0.0.1:{port_map.get('beacon', 6200)}"
        if not os.environ.get("MISSION_MANAGER_URL"):
            MISSION_MANAGER_URL = f"http://127.0.0.1:{port_map.get('mission_manager', 6207)}"
        if not os.environ.get("BELL_URL"):
            BELL_URL = f"http://127.0.0.1:{port_map.get('bell', 6204)}"

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
            if parsed_cmd["operator"]:
                target = parsed_cmd["operator"]
                self.runtime.logger.info(
                    "CHIEF command fast-path: %s → operator=%s", cmd, target
                )
                raw_result = self._dispatch_via_beacon(req, target, task_id)
                reply_text = self._format_reply(target, raw_result)
                append_history(chat_id, "user",      req.task)
                append_history(chat_id, "assistant", reply_text[:500])
                set_last_action(chat_id, "dispatch_operator", target, reply_text[:300])
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
            set_last_action(chat_id, "dispatch_operator", target, reply_text[:300])
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
            set_last_action(chat_id, "dispatch_operator", target, reply_text[:300])
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

    def _dispatch_via_beacon(
        self, req: TaskRequest, target: str, task_id: str
    ) -> dict:
        payload = {
            "sender": "chief",
            "message_type": "run.execute",
            "target": target,
            "message": {
                "task": req.task,
                "task_id": task_id,
                "source_channel": req.source_channel,
                "reply_channel": req.reply_channel,
                "sender": req.sender,
                "tenant_id": req.tenant_id,
                "metadata": req.metadata,
            },
        }
        try:
            result = _http_post(f"{BEACON_URL}/route", payload, timeout=30)
            # BEACON wraps the operator response in forward_response
            return result.get("forward_response") or result
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

    # ------------------------------------------------------------------
    # Reply formatting
    # ------------------------------------------------------------------

    def _format_reply(self, target: str, result: dict) -> str:
        if "error" in result:
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
        reply = f"Completed by {target}\n\n{str(content)}"
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
        return "\n".join(lines)

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
