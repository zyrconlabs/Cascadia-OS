# Cascadia OS — Security Questionnaire Responses

Pre-written answers for enterprise and commercial buyers. Use these verbatim or
adapt to the specific questionnaire format you receive.

---

## Data Security

**Q: Where is our data stored?**

All data remains on your hardware. Cascadia OS is local-first — no data is
transmitted to Zyrcon Labs or any cloud service. Your customer records, lead
data, and workflow history live exclusively on the Mac mini or server you own
and control.

---

**Q: Is our data encrypted?**

Yes. Data at rest is encrypted using AES-256-GCM. Data in transit between
internal components uses HMAC-SHA256 signed envelopes over localhost — no
plaintext inter-service communication.

---

**Q: Who has access to our data?**

Only you. Zyrcon Labs has zero access to your installation or data. There is no
remote access, no telemetry, and no analytics collection. The software is
delivered and then operates entirely under your control.

---

**Q: Is data backed up automatically?**

Cascadia OS does not perform automatic off-site backups (that would require
sending data externally). We recommend configuring macOS Time Machine or a
local NAS backup on your network. The data directory is at `~/Zyrcon/data/` and
can be included in any standard backup solution.

---

**Q: How long is data retained?**

There is no automatic data expiration. You control retention. The audit log and
workflow history accumulate until you clear them. We recommend a quarterly
review and archival routine.

---

## Compliance

**Q: Is Cascadia OS SOC 2 compliant?**

Cascadia OS is not currently SOC 2 certified. The Enterprise tier includes a
full, immutable audit log that records every operator action, every approval
decision, and every outbound send. This log is designed to support your own
compliance requirements and can be exported for auditor review.

---

**Q: Is Cascadia OS GDPR or CCPA compliant?**

Because all data is processed and stored on your own hardware, you are the data
controller and processor under GDPR and CCPA. Zyrcon Labs does not process
personal data on your behalf and is not a data processor in your GDPR chain.
You are responsible for your own data subject rights, retention, and deletion
policies.

---

**Q: Does Cascadia OS support HIPAA requirements?**

Cascadia OS's local-first architecture is well-suited to healthcare environments
because no data leaves your network. However, Zyrcon Labs does not currently
offer a Business Associate Agreement (BAA). Customers in regulated healthcare
environments should review the system with their compliance officer before
deployment.

---

## Network Security

**Q: What ports does Cascadia OS use?**

All inter-service communication is on `127.0.0.1` (localhost). No inbound
internet ports are required for core operation. The PRISM dashboard listens on
port 6300, accessible from your local network (not the internet).

Outbound connections are initiated only when an operator sends an external
message (email, SMS, Telegram) — and only after explicit human approval.

Full port reference:

| Service | Port | Accessible from |
|---|---|---|
| PRISM dashboard | 6300 | LAN only |
| NATS message bus | 4222 | localhost only |
| FLINT (LLM engine) | 5000 | localhost only |
| CREW (orchestrator) | 5100 | localhost only |
| VAULT (secrets) | 5200 | localhost only |
| Connectors | 9000–9099 | localhost only |

---

**Q: Does Cascadia OS require internet access?**

Core functionality — lead capture, AI drafting, approval workflows, and the
PRISM dashboard — works fully offline. Internet access is used for:

- License key validation (one-time on first start, periodic re-check)
- Outbound sends (email, SMS, Telegram) when operators deliver approved messages
- Optional model updates

The system continues to operate normally during internet outages; outbound sends
queue until connectivity is restored.

---

**Q: Does Cascadia OS use a VPN or require firewall changes?**

No VPN is required. No inbound firewall rules need to be opened for core
operation. If you want to access the PRISM dashboard from outside your local
network, we recommend a VPN (such as Tailscale) rather than exposing port 6300
to the internet.

---

## Software Security

**Q: How are software updates delivered?**

Updates are manual only. There is no automatic code execution from external
servers. You download a new release, review the changelog, and run the update
script at your chosen time. This means you are never subject to a surprise
automatic update that changes behavior.

---

**Q: Is the source code auditable?**

Yes. The core of Cascadia OS is open source at
`github.com/zyrconlabs/cascadia-os` under the Apache 2.0 license. You can
review, audit, or fork the code at any time. Commercial operator packages in
the DEPOT marketplace are proprietary, but the runtime that executes them is
fully open.

---

**Q: How are vulnerabilities reported?**

Security issues should be reported to **security@zyrcon.ai**. We aim to
acknowledge reports within 48 hours and provide a remediation timeline within
5 business days. Critical vulnerabilities receive a patch release; lower-
severity issues are addressed in the next scheduled release.

---

**Q: What is your vulnerability management process?**

We conduct dependency audits on every release using `pip-audit` and `safety`.
Third-party dependencies are pinned to specific versions and updated
deliberately. We do not use auto-merge bots for dependency updates. Known CVEs
in dependencies are remediated before the next release or, for critical issues,
in an out-of-band patch.

---

**Q: Has Cascadia OS undergone penetration testing?**

Cascadia OS has not undergone a formal third-party penetration test as of
April 2026. Given its local-first, no-inbound-ports architecture, the attack
surface is substantially smaller than a cloud SaaS product. Enterprise customers
requiring a formal pentest report may engage their own tester against a
dedicated test installation; Zyrcon Labs will provide technical cooperation.

---

**Q: How are third-party dependencies managed?**

All Python dependencies are declared in `pyproject.toml` with pinned versions.
We minimize third-party dependencies — the core system uses only the standard
library plus `nats-py`, `aiohttp`, and `anthropic`. New dependencies require
review before addition. The full dependency list is available in the open-source
repository.

---

## Incident Response

**Q: What is your incident response process?**

Because Cascadia OS runs on your hardware, incident response is primarily your
responsibility. Zyrcon Labs provides:

1. A dedicated support channel at **support@zyrcon.ai** for incidents during
   your pilot or subscription
2. Diagnostic tooling — `python scripts/diagnose.py` — that generates a system
   health report without transmitting data externally
3. Rollback instructions in `UNINSTALL.md` and `CHANGELOG.md` for every release

For suspected security incidents (unauthorized access to the host machine),
follow your organization's existing incident response procedures.

---

**Q: What is your business continuity plan?**

Cascadia OS is designed with offline resilience in mind. If the host machine
fails, you can restore from a Time Machine or local backup onto a replacement
Mac mini. Full restoration from backup typically takes 30–60 minutes.

Zyrcon Labs is a product company, not an infrastructure provider. There is no
Zyrcon-operated server whose outage would affect your installation.

---

## Physical Security

**Q: What are the physical security requirements for the AI Node hardware?**

The Mac mini AI Node should be located in a physically secure area — a locked
server closet, office, or equipment room. Physical access to the machine is
equivalent to administrative access to Cascadia OS. Standard recommendations:

- Secure the machine with a Kensington lock or in a locked enclosure
- Restrict physical access to authorized personnel
- Enable FileVault disk encryption (enabled by default in our setup script)
- Set a firmware password to prevent boot from external media

---

*Document version: 1.0 — April 2026*
*Contact: security@zyrcon.ai*
