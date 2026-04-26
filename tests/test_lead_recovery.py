"""Tests for LeadRecovery missed-lead scorer (Task 13 — Sprint v2)."""
import pytest

from cascadia.operators.lead_recovery import LeadRecovery


@pytest.fixture
def lr():
    return LeadRecovery()


def _email(subject="", body="", sender="contact@acme.com", received_at="2026-04-25T08:00:00Z"):
    return {"subject": subject, "body": body, "sender": sender, "received_at": received_at}


def test_empty_email_is_low_score(lr):
    result = lr.score_email(_email())
    assert result["score"] < 30
    assert result["is_lead"] is False


def test_intent_keyword_raises_score(lr):
    result = lr.score_email(_email(subject="Pricing inquiry for your services"))
    assert result["score"] >= 30
    assert result["is_lead"] is True


def test_multiple_keywords_stack(lr):
    result = lr.score_email(_email(body="I need a quote for this project. Interested in your pricing."))
    assert result["score"] >= 40


def test_urgency_keyword_bumps_score(lr):
    base = lr.score_email(_email(body="I need a quote"))
    urgent = lr.score_email(_email(body="I need a quote ASAP"))
    assert urgent["score"] > base["score"]


def test_business_domain_adds_signal(lr):
    result = lr.score_email(_email(sender="john@customlogistics.com"))
    assert "business_domain" in result["signals"]


def test_personal_domain_no_signal(lr):
    result = lr.score_email(_email(sender="john@gmail.com", body="hi"))
    assert "business_domain" not in result["signals"]


def test_question_in_subject(lr):
    result = lr.score_email(_email(subject="Can you give me a quote?"))
    assert "question_in_subject" in result["signals"]


def test_dollar_amount_signal(lr):
    result = lr.score_email(_email(body="Our budget is around $15,000 for this project"))
    assert "dollar_amount" in result["signals"]
    assert result["score"] >= 10


def test_priority_high_for_strong_lead(lr):
    result = lr.score_email(_email(
        subject="Urgent pricing quote?",
        body="I need an estimate for our $50,000 warehouse project asap",
        sender="ceo@industrialcorp.com",
    ))
    assert result["priority"] == "high"


def test_score_capped_at_100(lr):
    big = _email(
        subject="Urgent project quote? Need pricing immediately!",
        body=" ".join(["quote pricing interested proposal estimate"] * 10) + " $100,000 ASAP deadline today",
        sender="buyer@bigcorp.com",
    )
    result = lr.score_email(big)
    assert result["score"] <= 100


def test_filter_leads_removes_low_score(lr):
    emails = [
        _email(subject="hello"),
        _email(subject="Pricing inquiry for warehouse project", sender="ops@acme.com"),
    ]
    leads = lr.filter_leads(emails)
    assert len(leads) == 1
    assert leads[0]["is_lead"] is True


def test_score_batch_sorted_desc(lr):
    emails = [
        _email(subject="hi"),
        _email(subject="Urgent quote needed ASAP", sender="mgr@corp.com"),
        _email(subject="Pricing for project?", body="$25,000 budget"),
    ]
    results = lr.score_batch(emails)
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)
