---
name: example-task
goal: Find warehouse facility managers and operations directors in Houston TX with contact details
model: qwen2.5-3b-instruct-q4_k_m.gguf
fields:
  - full_name: Full name of the person
  - title: Job title
  - company: Company name
  - email: Work email address
  - phone: Direct phone number
  - linkedin: LinkedIn profile URL
  - source_url: Where this was found

stop:
  mode: quantity        # Options: quantity | time | status
  quantity: 100         # Stop after N data rows collected
  # time: 24h           # Alternatives: 60m | 6h | 24h | 7d
  # mode: status        # Stop only via dashboard button or status: active below

status: active          # Change to: stop  —  to halt the worker

interval: 15           

queries:
  - warehouse facility manager Houston TX contact
  - industrial operations director Houston Texas
  - warehouse company HR manager Houston directory
  - logistics center manager Houston TX email
---

## Notes
- Focus on companies with 50,000+ sq ft warehouse space.
- Prefer LinkedIn, official company websites, and industry directories.
- Exclude staffing agencies and recruiters.
- If a person appears on multiple sources, merge into one record.
