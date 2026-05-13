"""
CHIEF operator selector.
Two-pass selection: keyword match → capability match against CREW registry.
Returns the best registered operator for a given task text.
"""
from __future__ import annotations

import json
import urllib.request
import urllib.error
import logging

log = logging.getLogger(__name__)

# Status commands checked before keyword matching
_STATUS_TRIGGERS = ("/status", "/missions", "/help", "/operators")

# keyword groups → preferred operator names and/or capability signals
_KEYWORD_MAP = [
    {
        "keywords": [
            "quote", "proposal", "estimate", "bid", "pricing",
            "mezzanine", "installation", "warehouse", "brief",
            "draft proposal", "service agreement",
        ],
        "preferred_operators": ["quote_brief"],
        "capabilities": [
            "quote.generate", "proposal.draft",
            "brief.generate", "business.brief",
        ],
    },
    {
        "keywords": [
            "lead", "prospect", "customer", "outreach", "sales",
            "follow up", "followup", "contact", "pipeline",
        ],
        "capabilities": [
            "lead.find", "lead.qualify", "lead.enrich",
            "email.draft", "email.send", "crm.write",
        ],
    },
    {
        "keywords": [
            "seo", "google", "ranking", "citation",
            "review", "reputation", "listing", "local",
        ],
        "capabilities": [
            "seo.audit", "citation.manage",
            "review.monitor", "content.create",
        ],
    },
    {
        "keywords": [
            "competitor", "price", "market",
            "compare", "comparison", "competitive",
        ],
        "capabilities": [
            "competitor.research", "market.research", "price.compare",
        ],
    },
    {
        "keywords": [
            "contract", "deadline", "agreement",
            "overdue", "vendor", "project", "reminder",
        ],
        "capabilities": [
            "contract.watch", "deadline.track", "project.followup",
        ],
    },
    {
        "keywords": [
            "research", "find", "enrich", "lookup",
            "investigate", "data", "search",
        ],
        "capabilities": [
            "data.research", "lead.enrich", "web.search", "recon",
        ],
    },
    {
        "keywords": [
            "code", "bug", "github", "test",
            "deploy", "website", "fix", "script",
        ],
        "capabilities": [
            "code.review", "code.write", "github.read", "website.update",
        ],
    },
]


def select_target(task_text: str, crew_url: str) -> dict:
    """
    Returns:
      {
        "ok": bool,
        "selected_type": "operator"|"mission"|"status"|"none",
        "target": str | None,
        "reason": str,
        "confidence": float
      }
    """
    stripped = task_text.strip()

    # Status commands — check before keyword matching
    if any(stripped.startswith(t) for t in _STATUS_TRIGGERS):
        return {
            "ok": True,
            "selected_type": "status",
            "target": stripped.split()[0],
            "reason": "status command",
            "confidence": 1.0,
        }

    # Pass 1: keyword → matched group
    text_lower = task_text.lower()
    matched_group = None
    for group in _KEYWORD_MAP:
        if any(kw in text_lower for kw in group["keywords"]):
            matched_group = group
            break

    if matched_group is None:
        return {
            "ok": False,
            "selected_type": "none",
            "target": None,
            "reason": "no keyword match found in task text",
            "confidence": 0.0,
        }

    # Pass 2: match against registered operators from CREW
    try:
        operators = _get_crew_operators(crew_url)
    except Exception as exc:
        return {
            "ok": False,
            "selected_type": "none",
            "target": None,
            "reason": f"CREW unreachable: {exc}",
            "confidence": 0.0,
        }

    preferred = matched_group.get("preferred_operators", [])
    preferred_caps = matched_group.get("capabilities", [])

    best_operator: str | None = None
    best_score = 0.0

    for op in operators:
        op_name = op.get("operator_id") or op.get("name") or ""
        op_caps = op.get("capabilities", [])

        if op_name in preferred:
            best_operator = op_name
            best_score = 1.0
            break

        if preferred_caps:
            matches = sum(1 for cap in preferred_caps if cap in op_caps)
            if matches > 0:
                score = matches / len(preferred_caps)
                if score > best_score:
                    best_score = score
                    best_operator = op_name

    if best_operator is None:
        return {
            "ok": False,
            "selected_type": "none",
            "target": None,
            "reason": (
                f"no registered operator matches required capabilities: "
                f"{preferred_caps}"
            ),
            "confidence": 0.0,
        }

    return {
        "ok": True,
        "selected_type": "operator",
        "target": best_operator,
        "reason": f"keyword match → capability match (score={best_score:.2f})",
        "confidence": best_score,
    }


def _get_crew_operators(crew_url: str) -> list[dict]:
    """
    GET {crew_url}/crew → extract list of operator dicts.
    Response shape: {"crew_size": N, "operators": {op_id: {operator_id, capabilities, ...}}}
    """
    url = crew_url.rstrip("/") + "/crew"
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            data = json.loads(r.read().decode())
    except urllib.error.URLError as exc:
        raise ConnectionError(f"CREW GET /crew failed: {exc.reason}") from exc
    except Exception as exc:
        raise ConnectionError(f"CREW GET /crew error: {exc}") from exc

    operators_map = data.get("operators") or {}
    if isinstance(operators_map, dict):
        return list(operators_map.values())
    if isinstance(operators_map, list):
        return operators_map
    return []
