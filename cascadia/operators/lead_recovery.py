# MATURITY: PRODUCTION — Scores missed leads from IMAP-scanned emails.
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


_INTENT_KEYWORDS = [
    "quote", "pricing", "interested", "proposal", "estimate",
    "cost", "bid", "project", "contract", "service", "inquiry",
]
_URGENT_KEYWORDS = ["urgent", "asap", "immediately", "deadline", "today", "rush"]


class LeadRecovery:
    """
    Scores inbound emails that look like missed leads.
    Does not own IMAP scanning (VANGUARD's responsibility).
    Does not own outreach scheduling (operator's responsibility).
    """

    def score_email(self, email: Dict[str, Any]) -> Dict[str, Any]:
        """
        Score one email dict for lead likelihood.
        Expected keys: subject, body, sender, received_at.
        Returns: {score 0-100, priority, signals, is_lead}.
        """
        subject = (email.get("subject") or "").lower()
        body    = (email.get("body")    or "").lower()
        sender  = (email.get("sender")  or "").lower()
        text    = f"{subject} {body}"

        signals: List[str] = []
        score = 0

        # Intent keywords
        for kw in _INTENT_KEYWORDS:
            if kw in text:
                score += 8
                signals.append(f"intent:{kw}")

        # Urgency bump
        for kw in _URGENT_KEYWORDS:
            if kw in text:
                score += 5
                signals.append(f"urgent:{kw}")

        # Non-personal sender domain (business email)
        if sender and "@" in sender:
            domain = sender.split("@", 1)[1]
            if domain not in ("gmail.com", "yahoo.com", "hotmail.com", "outlook.com"):
                score += 10
                signals.append("business_domain")

        # Question in subject
        if "?" in email.get("subject", ""):
            score += 5
            signals.append("question_in_subject")

        # Dollar amount mentioned
        if re.search(r"\$\s*\d+", text):
            score += 10
            signals.append("dollar_amount")

        score = min(score, 100)
        priority = "high" if score >= 60 else "medium" if score >= 30 else "low"

        return {
            "score": score,
            "priority": priority,
            "signals": signals,
            "is_lead": score >= 30,
            "sender": email.get("sender", ""),
            "subject": email.get("subject", ""),
            "received_at": email.get("received_at", ""),
        }

    def score_batch(self, emails: List[Dict[str, Any]], min_score: int = 0) -> List[Dict[str, Any]]:
        """Score a list of emails and return sorted by score descending."""
        scored = [self.score_email(e) for e in emails]
        filtered = [r for r in scored if r["score"] >= min_score]
        filtered.sort(key=lambda x: -x["score"])
        return filtered

    def filter_leads(self, emails: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Return only emails scored as likely leads (score >= 30)."""
        return self.score_batch(emails, min_score=30)
