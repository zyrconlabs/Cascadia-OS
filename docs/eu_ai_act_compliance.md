# EU AI Act Articles 8–15 Compliance Reference
### Cascadia OS v0.47 · April 2026

---

## Overview

The EU AI Act (Regulation 2024/1689), Articles 8–15, imposes mandatory requirements on providers of high-risk AI systems. These requirements apply to AI systems used in healthcare, agriculture, industrial automation, legal services, employment, critical infrastructure, and several other regulated domains.

**Deadline:** August 2, 2026 — the date by which high-risk AI systems must demonstrate compliance.

**Non-compliance penalties:** Up to 7% of global annual turnover or €35,000,000 — whichever is higher.

**Cascadia OS compliance is architectural, not an add-on.** Every operator built on Cascadia OS inherits full Articles 8–15 compliance from the core runtime. No additional work is required from operator developers.

---

## Compliance Statement

Cascadia OS v0.47 is designed from its foundations to satisfy the risk management, data governance, technical documentation, logging, transparency, human oversight, and accuracy requirements of EU AI Act Articles 8–15 for high-risk AI systems.

This compliance is implemented at the kernel level and enforced by the runtime. It cannot be accidentally circumvented by operator developers.

Contact: compliance@zyrcon.ai

---

## Article Mapping

### Article 9 — Risk Management System

**Requirement:** Providers must establish, implement, document, and maintain a risk management system throughout the lifecycle of the AI system. Risk assessments must be performed, risks identified, mitigation measures applied, and residual risks evaluated.

**Cascadia OS implementation: SENTINEL**

SENTINEL (`cascadia.security.sentinel`, port 5102) is the real-time risk classification engine that runs before every consequential action.

- Every operator action is evaluated against a risk policy before execution.
- Actions are classified as LOW / MEDIUM / HIGH risk.
- HIGH-risk actions are blocked by default until explicitly approved.
- MEDIUM-risk actions trigger approval gates.
- Risk rules are declared in configuration and enforced at the enforcement point — not in operator code.
- The risk policy cannot be bypassed at the operator layer.
- Risk classification logs are append-only and chain-hashed.

SENTINEL implements continuous, automated risk management at every step of every workflow, satisfying the lifecycle-continuous requirement of Article 9.

---

### Article 10 — Data and Data Governance

**Requirement:** High-risk AI systems must use training, validation, and testing data that meets quality criteria. Providers must implement data governance practices and ensure data relevance, accuracy, and protection.

**Cascadia OS implementation: VAULT + CURTAIN**

VAULT (`cascadia.memory.vault`, port 5101) is the durable, access-controlled memory store:

- All data writes are validated against a declared schema before persistence.
- Access to stored data requires CREW capability validation — operators cannot read outside their declared namespace.
- Data lineage is maintained through the run journal.
- Personal and sensitive data is flagged at ingestion and handled separately.

CURTAIN (`cascadia.encryption.curtain`, port 5103) provides field-level encryption:

- AES-256-GCM encryption protects sensitive fields at rest.
- HMAC-SHA256 envelope signing ensures data integrity.
- Encryption keys are managed separately from data — breach of storage does not breach keys.
- All data access is logged with actor identity and timestamp.

Together, VAULT and CURTAIN implement data governance controls that satisfy Article 10 requirements for data quality, protection, and access control.

---

### Article 11 — Technical Documentation

**Requirement:** Providers must prepare and maintain technical documentation demonstrating compliance before placing the system on the market. Documentation must describe system capabilities, limitations, and intended purposes.

**Cascadia OS implementation: manifest_schema**

`cascadia.shared.manifest_schema` enforces a validated, machine-readable manifest for every operator:

- Every operator must declare: `id`, `name`, `version`, `type`, `capabilities`, `autonomy_level`, `health_hook`, `description`, and `requested_permissions`.
- Manifests are validated at registration time — invalid manifests are rejected.
- The manifest schema is versioned and published.
- `autonomy_level` declarations (`manual_only`, `assistive`, `semi_autonomous`, `autonomous`) define the operational envelope in machine-readable form.
- All manifests are available via the CREW registry for audit.

The manifest system provides the technical documentation layer required by Article 11, in a format that is both human-readable and automatically enforced.

---

### Article 12 — Record-Keeping

**Requirement:** High-risk AI systems must be designed and built to automatically log events through the operational lifetime. Logs must enable monitoring and post-market surveillance, and must be retained for appropriate periods.

**Cascadia OS implementation: audit_log + run_store**

`cascadia.system.audit_log`:

- Append-only audit log for all consequential actions.
- SHA-256 chain-hashed: each entry includes the hash of the previous entry, creating a tamper-evident chain.
- Exportable as CSV for regulatory submission.
- Hash chain verification endpoint: `/api/prism/audit/verify`.
- Events logged: actions taken, approvals requested and recorded, operator registrations, configuration changes, risk classification results.

`cascadia.durability.run_store` + `cascadia.durability.step_journal`:

- Complete durable record of every workflow run: start, each step, each decision, each side effect, completion or failure.
- Records are written atomically — partial records cannot exist.
- Run records survive process crashes and system restarts.
- Retention is configurable; defaults to indefinite for compliance purposes.

Together, these systems provide the complete, tamper-evident, automatically-generated operational log required by Article 12.

---

### Article 13 — Transparency and Provision of Information

**Requirement:** High-risk AI systems must be designed to ensure sufficient transparency that deployers can interpret the system's output and use it appropriately. Providers must supply instructions for use.

**Cascadia OS implementation: ALMANAC + PRISM**

ALMANAC (`cascadia.guide.almanac`, port 6205):

- Natural-language queryable documentation system embedded in the runtime.
- Operators and users can query `What does this component do?`, `What risk level is assigned to this action?`, `Why was this step blocked?`.
- Runbooks, component catalog, and glossary are searchable at runtime.
- ALMANAC provides explainability at the point of use, not only in external documentation.

PRISM (`cascadia.dashboard.prism`, port 6300):

- Live dashboard showing every run, its current state, pending approvals, risk classifications, and system health.
- Non-technical users can understand system state without reading logs.
- Every approval gate displays the action, its risk classification, and the reason for requiring approval.
- The run timeline shows each step, its outcome, and any side effects committed.

Together, ALMANAC and PRISM provide the transparency infrastructure required by Article 13 for both technical operators and end users.

---

### Article 14 — Human Oversight

**Requirement:** High-risk AI systems must be designed to enable effective human oversight. This includes enabling humans to understand the system's capabilities and limitations, to monitor operation, and to interrupt or override the system's outputs.

**Cascadia OS implementation: ApprovalStore + WorkflowRuntime**

`cascadia.system.approval_store.ApprovalStore`:

- Every consequential action that exceeds the configured risk threshold requires explicit human approval before execution.
- Approval gates cannot be bypassed at the operator layer.
- Approvals include: the action proposed, the risk classification, the context, the operator that requested it.
- Humans can approve, deny, or approve with edited content.
- Denied actions halt the workflow immediately.
- Approval decisions are logged permanently.

`cascadia.automation.workflow_runtime.WorkflowRuntime`:

- Workflows can be paused at any step by a human.
- Paused workflows cannot auto-resume.
- Humans can resume from any checkpoint or halt permanently.
- Confidence-based escalation: operators declare their confidence level; the runtime inserts an approval gate if confidence falls below the operator's declared threshold.

These systems implement the stop-button, override, and monitoring requirements of Article 14 at the kernel level.

---

### Article 15 — Accuracy, Robustness, and Cybersecurity

**Requirement:** High-risk AI systems must achieve appropriate levels of accuracy. They must be robust to errors, faults, and inconsistencies. They must be resilient to attempts to alter their behavior and resistant to adversarial interference.

**Cascadia OS implementation: CURTAIN + FLINT + step_journal**

CURTAIN (`cascadia.encryption.curtain`):

- AES-256-GCM encryption with authenticated encryption — tampering with ciphertext is cryptographically detectable.
- HMAC-SHA256 envelope signing on all inter-component messages — message integrity is verified before processing.
- Internal API key authentication (`CASCADIA_INTERNAL_KEY`, X-Cascadia-Key header) — unauthenticated internal calls are rejected.

FLINT (`cascadia.kernel.flint`):

- Process supervision with restart backoff — components restart automatically on failure with exponential backoff.
- Tiered startup — dependencies must be healthy before dependent services start.
- LLM health monitoring — inference failures are detected and logged within 30 seconds.
- Graceful drain protocol — running workflows are not abandoned on shutdown.

`cascadia.durability.step_journal`:

- Every step's input and output are recorded atomically.
- Idempotency enforcement — side effects are never executed twice, even after crash and resume.
- Database integrity check on startup — corrupted databases are detected before any workflow executes.

`cascadia.durability.backup.BackupManager`:

- Automated daily SQLite backup with gzip compression.
- 30-day retention by default.
- Backup integrity verification via SQLite magic-byte check.

Together, these systems satisfy the accuracy, robustness, and cybersecurity requirements of Article 15.

---

## Summary Table

| Article | Requirement | Cascadia OS Component |
|---------|-------------|----------------------|
| Article 9 | Risk management system | SENTINEL |
| Article 10 | Data and data governance | VAULT + CURTAIN |
| Article 11 | Technical documentation | manifest_schema (CREW) |
| Article 12 | Record-keeping | audit_log + run_store |
| Article 13 | Transparency | ALMANAC + PRISM |
| Article 14 | Human oversight | ApprovalStore + WorkflowRuntime |
| Article 15 | Accuracy, robustness, cybersecurity | CURTAIN + FLINT + step_journal |

---

## Analog Guard Channel Note

Cascadia OS manages the AI decision layer. Physical safety enforcement is the responsibility of your hardware infrastructure.

For industrial, agricultural, and medical deployments where physical actuators are involved, hardware guard channels (emergency stop, hardware limiter, physical interlock, PLC safety relay) must operate independently of the software state and must be capable of vetoing unsafe commands without relying on Cascadia OS being operational.

This separation of software logic from physical safety enforcement is the correct legal architecture for regulated deployments. Cascadia OS does not replace physical safety systems. It coordinates with them.

---

*Cascadia OS is developed by Zyrcon Labs and released under the Apache 2.0 License.*  
*For compliance inquiries: compliance@zyrcon.ai*  
*For enterprise deployment questions: enterprise@zyrcon.ai*
