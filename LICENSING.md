# Cascadia OS — Licensing

Cascadia OS uses a layered licensing model: an open-source core
and separate commercial layers built on top.

## Open Source Core — Apache License 2.0

The Cascadia OS core runtime and infrastructure are licensed under
the Apache License 2.0. See [LICENSE](./LICENSE) for the full text.

The Apache 2.0 core includes:

- **FLINT** — process supervision, tiered startup, health polling
- **Watchdog** — external FLINT liveness monitor
- **CREW** — operator registry with capability validation
- **VAULT** — SQLite-backed durable memory and state
- **CURTAIN** — AES-256-GCM encryption and HMAC-SHA256 signing
- **SENTINEL** — risk classification and policy enforcement
- **BEACON** — capability-checked routing and HTTP forwarding
- **STITCH** — workflow sequencing engine
- **VANGUARD** — inbound channel normalization
- **HANDSHAKE** — webhook, HTTP, SMTP execution
- **BELL** — chat sessions, workflow execution, approval collection
- **ALMANAC** — component catalog and glossary
- **PRISM** (base dashboard) — status and observability shell
- **Operator SDK and protocol** — registration contract and base classes

You are free to use, modify, distribute, and build on the open source
core for any purpose, including commercial use, subject to the terms
of the Apache License 2.0.

## Commercial Layers

Certain components, products, and services built on top of the open
source core are developed, distributed, or supported by Zyrcon Labs
under separate commercial terms.

See [COMMERCIAL.md](./COMMERCIAL.md) for the full breakdown, or
contact zyrconlabs@gmail.com for commercial inquiries.

## Dependency Stack

Cascadia OS builds on the following third-party components:

| Component | Licence |
|---|---|
| llama.cpp | MIT |
| Qwen3 (model weights) | Apache 2.0 |
| cryptography | Apache 2.0 / BSD |
| Flask | BSD-3-Clause |
| requests | Apache 2.0 |
| ddgs | MIT |

All dependencies are permissively licensed. A full software bill
of materials is available on request.

## Contributions

By submitting a contribution (pull request, patch, or issue with
code) to the Cascadia OS core repository, you agree that your
contribution is licensed under the Apache License 2.0.

## Previous Licence

Prior to v0.44.0, Cascadia OS was licensed under the MIT License.
Code in the repository history before the v0.44.0 tag remains
available under its original MIT terms. From v0.44.0 forward,
the Apache License 2.0 applies.

---

*Last updated: April 2026 · Zyrcon Labs · Houston, Texas*
