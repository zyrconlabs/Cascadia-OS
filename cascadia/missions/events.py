"""Canonical event names for the Missions layer."""

# Mission lifecycle
MISSION_INSTALLED = "mission.installed"
MISSION_REMOVED = "mission.removed"
MISSION_STARTED = "mission.started"
MISSION_COMPLETED = "mission.completed"
MISSION_FAILED = "mission.failed"
MISSION_APPROVAL_REQUESTED = "mission.approval_requested"

# Approval lifecycle
APPROVAL_CREATED = "approval.created"
APPROVAL_RESOLVED = "approval.resolved"

# Lead lifecycle
LEAD_DETECTED = "lead.detected"
LEAD_ENRICHED = "lead.enriched"
LEAD_SCORED = "lead.scored"

# Quote lifecycle
QUOTE_DRAFTED = "quote.drafted"
QUOTE_APPROVAL_REQUESTED = "quote.approval_requested"
QUOTE_APPROVED = "quote.approved"
QUOTE_REJECTED = "quote.rejected"

# Email
EMAIL_SENT = "email.sent"

# Invoice
INVOICE_CREATED = "invoice.created"
INVOICE_OVERDUE = "invoice.overdue"

# Campaign
CAMPAIGN_DRAFTED = "campaign.drafted"
CAMPAIGN_APPROVAL_REQUESTED = "campaign.approval_requested"
CAMPAIGN_SCHEDULED = "campaign.scheduled"

# Review
REVIEW_REQUESTED = "review.requested"

# Task
TASK_CREATED = "task.created"
TASK_COMPLETED = "task.completed"

# Brief / Debrief
BRIEF_GENERATED = "brief.generated"
DEBRIEF_GENERATED = "debrief.generated"

# Mobile
MOBILE_CAPTURE_RECEIVED = "mobile.capture.received"

# Schedule triggers consumed by missions
SCHEDULE_DAILY = "schedule.daily"
JOB_COMPLETED = "job.completed"
LEAD_COLD = "lead.cold"
WEBFORM_SUBMITTED = "webform.submitted"
DOCUMENT_RECEIVED = "document.received"
