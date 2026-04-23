from __future__ import annotations

from typing import Any, Dict, List


def _clean_platforms(value: Any) -> List[str]:
    if isinstance(value, str):
        raw = [p.strip().lower() for p in value.replace(';', ',').split(',')]
    else:
        raw = [str(p).strip().lower() for p in (value or [])]
    allowed = []
    seen = set()
    for item in raw:
        if item in {"x", "instagram", "facebook"} and item not in seen:
            allowed.append(item)
            seen.add(item)
    return allowed or ["x"]


def build_master_package(state: Dict[str, Any]) -> Dict[str, Any]:
    topic = (state.get("topic") or state.get("content") or "Zyrcon update").strip()
    audience = (state.get("audience") or "builders, operators, and technical buyers").strip()
    goal = (state.get("campaign_goal") or "share a meaningful product update and drive replies").strip()
    cta = (state.get("cta") or "Reply if you want details or an early look.").strip()
    proof_points = state.get("proof_points") or [
        "durable execution",
        "approval gates",
        "crash recovery",
        "local-first reliability",
    ]
    if isinstance(proof_points, str):
        proof_points = [p.strip() for p in proof_points.split(',') if p.strip()]
    tone = state.get("tone") or "founder-built, practical, direct"
    platforms = _clean_platforms(state.get("platforms"))
    master_message = (
        f"{topic}. Built for {audience}. "
        f"Goal: {goal}. Key proof: {', '.join(proof_points[:4])}. "
        f"CTA: {cta}"
    )
    return {
        **state,
        "platforms": platforms,
        "master_content_package": {
            "topic": topic,
            "audience": audience,
            "campaign_goal": goal,
            "cta": cta,
            "proof_points": proof_points,
            "tone": tone,
            "master_message": master_message,
        },
        "draft_preview": f"Master social package ready for {', '.join(platforms)}.",
    }


def render_platform(state: Dict[str, Any], platform: str) -> Dict[str, Any]:
    package = dict(state.get("master_content_package") or {})
    topic = package.get("topic", "Zyrcon update")
    cta = package.get("cta", "Reply if you want details.")
    proofs = package.get("proof_points") or []
    short_proofs = ', '.join(proofs[:3]) if proofs else 'durable AI operations'

    if platform == 'x':
        bullets = '\n'.join(f'• {p}' for p in (proofs[:3] or ['durable execution']))
        body = (
            f"Built another step forward for {topic}.\n\n"
            f"What changed:\n{bullets}\n\n"
            f"AI should handle real work, not just demos. {cta}"
        )
        hashtags = ["#AI", "#LocalAI", "#Automation", "#BuildInPublic"]
    elif platform == 'instagram':
        body = (
            f"{topic}\n\n"
            f"Built to make AI more dependable in real work: {short_proofs}.\n\n"
            f"{cta}"
        )
        hashtags = ["#startup", "#ai", "#founder", "#buildinpublic", "#automation"]
    elif platform == 'facebook':
        body = (
            f"Quick update on {topic}: we are focusing on systems that recover, ask for approval before risky actions, "
            f"and stay reliable during real operations. Recent improvements include {short_proofs}.\n\n{cta}"
        )
        hashtags = ["#AI", "#SmallBusiness", "#Automation"]
    else:
        body = package.get("master_message", topic)
        hashtags = ["#AI"]

    rendered = {
        "platform": platform,
        "body": body.strip(),
        "hashtags": hashtags,
        "status": "draft_ready",
    }
    new_state = dict(state)
    drafts = dict(new_state.get("platform_drafts") or {})
    drafts[platform] = rendered
    new_state["platform_drafts"] = drafts
    new_state["draft_preview"] = f"{platform.title()} draft ready. Approval required before publish."
    return new_state


def mark_published(state: Dict[str, Any], platform: str, post_id: str) -> Dict[str, Any]:
    new_state = dict(state)
    published = dict(new_state.get("published_posts") or {})
    published[platform] = {
        "post_id": post_id,
        "published": True,
    }
    new_state["published_posts"] = published
    statuses = dict(new_state.get("publish_status") or {})
    statuses[platform] = 'simulated_sent'
    new_state["publish_status"] = statuses
    new_state["draft_preview"] = f"{platform.title()} post published (simulated)."
    return new_state
