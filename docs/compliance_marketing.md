# EU AI Act compliant by architecture. Not by retrofit.

---

## Sub-headline

Every operator running on Cascadia OS inherits:

- EU AI Act Article 12 compliant logging
- SENTINEL real-time risk classification
- SHA-256 chain-hashed audit trail
- AES-256-GCM encrypted memory
- Human approval gates before every consequential action

Compliance infrastructure included. No additional work required from operator developers.

---

## Market Context

AI Infrastructure Software growing at 83% annually ($126B → $230B in 2026).

EU AI Act deadline August 2, 2026 is creating procurement urgency in healthcare, agriculture, industrial, and legal markets.

Cascadia OS is the only private AI runtime at the SMB price point with native EU AI Act compliance in the core architecture.

On-premise AI is 46% of total market with 24% CAGR. Enterprises and regulated industries are not moving to cloud AI — they are building private AI infrastructure. Cascadia OS is the operating system for that infrastructure.

---

## EU AI Act Articles 8–15: What Each Requires and How Cascadia OS Delivers

| Article | Requirement | Cascadia OS Component | What It Does |
|---------|-------------|----------------------|--------------|
| Article 9 | Risk management system | SENTINEL | Real-time risk classification before every action. HIGH-risk actions blocked. Lifecycle-continuous. |
| Article 10 | Data and data governance | VAULT + CURTAIN | Schema-validated storage, capability-gated access, AES-256-GCM field encryption, HMAC-SHA256 integrity. |
| Article 11 | Technical documentation | manifest_schema | Validated, machine-readable operator manifests. Autonomy level, capabilities, and permissions declared and enforced. |
| Article 12 | Record-keeping | audit_log + run_store | SHA-256 chain-hashed append-only log. Complete durable run journal. Tamper-evident. Exportable. |
| Article 13 | Transparency | ALMANAC + PRISM | Queryable runtime documentation. Live dashboard. Approval gates display action, risk, and reason. |
| Article 14 | Human oversight | ApprovalStore + WorkflowRuntime | Approval gates before every consequential action. Stop-button at every step. Confidence-based escalation. |
| Article 15 | Accuracy, robustness, cybersecurity | CURTAIN + FLINT + step_journal | Authenticated encryption, envelope signing, process supervision, idempotent execution, integrity checking. |

Full technical mapping: [docs/eu_ai_act_compliance.md](./eu_ai_act_compliance.md)

---

## Analog Guard Channel Statement

Cascadia OS manages the AI decision layer. Physical safety is your hardware responsibility. We provide the integration pattern.

This is the correct legal architecture for industrial and agricultural deployments where physical safety liability cannot be delegated to software.

For industrial, agricultural, and medical deployments: the software layer (Cascadia OS) provides AI analysis, risk classification, approval gating, and decision logging. The hardware layer — emergency stop, hardware limiter, thrust clamp, physical interlock, PLC safety relay — must operate independently of the software state and must be capable of vetoing unsafe commands within microseconds, without relying on Cascadia OS being operational.

Cascadia OS does not replace physical safety systems. It coordinates with them.

This separation of software logic from physical safety enforcement is the correct legal architecture for regulated deployments.

---

## DEPOT Developer Program

Build operators for Cascadia OS and publish to DEPOT.

Keep 100% of revenue on your first $25,000 in sales. 80/20 split (you keep 80%) after that.

No advertising in DEPOT. Revenue from sales only.

Apply at depot.zyrcon.ai

---

## Use Cases by Regulated Industry

**Healthcare:** Patient intake automation, appointment scheduling, document processing. SENTINEL blocks any action that accesses protected health information without an explicit approval gate. Audit trail satisfies Article 12 record-keeping.

**Agriculture:** Crop monitoring, yield prediction, irrigation scheduling, pest detection. CONDUIT IoT bridge processes sensor data. Analog guard channels coordinate with physical equipment. No software command directly actuates hardware.

**Industrial / Manufacturing:** Predictive maintenance, quality control, process optimization. Sensor data flows through CONDUIT → VANGUARD → operators. Physical actuator commands require Enterprise tier, hardware guard channels, and explicit human approval.

**Legal / Compliance:** Document review, contract analysis, regulatory reporting. Full audit trail. Every AI-generated output includes confidence score. Low-confidence outputs automatically trigger human review.

**Field Services:** Lead qualification, work order dispatch, customer communication. 4-minute average response time vs 47-hour industry average. Approval gates on every customer communication.

---

## Deployment Model

Cascadia OS runs on your hardware. Not ours.

- MacOS (Apple Silicon): Mac mini M4, $899 base
- Linux (x86/ARM): Any Ubuntu 22.04+ server
- Edge IoT: Raspberry Pi 5 8GB for sensor gateway nodes (v0.48)

No data leaves your premises unless you configure an operator to send it. VAULT stores all memory locally. CURTAIN encrypts everything at rest.

---

*Cascadia OS is developed by Zyrcon Labs and released under the Apache 2.0 License.*  
*Compliance inquiries: compliance@zyrcon.ai*  
*Enterprise deployments: enterprise@zyrcon.ai*
