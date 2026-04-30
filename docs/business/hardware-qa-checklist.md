# Hardware QA Checklist — Cascadia OS AI Node

Use this checklist for every Mac mini unit before shipping to a client.
Complete all items in order. Sign off at the bottom when done.

---

## Pre-Setup (Out of Box)

- [ ] Verify serial number matches purchase order
- [ ] Inspect for physical damage (dents, scratches, loose ports)
- [ ] Confirm Mac mini model: **M4, 16 GB RAM minimum, 512 GB SSD minimum**
  - Apple menu → About This Mac → confirm chip and memory
- [ ] Power on and confirm boots to macOS without errors
- [ ] Confirm macOS version is 14 (Sonoma) or later

---

## Software Installation

- [ ] Run macOS updates: **Settings → General → Software Update** → install all
- [ ] Reboot after updates complete
- [ ] Enable FileVault: **Settings → Privacy & Security → FileVault → Turn On**
  - Save the recovery key in the client's onboarding folder
- [ ] Install Cascadia OS:
  ```bash
  curl -fsSL https://zyrcon.ai/install.sh | bash
  ```
- [ ] Verify installation completes without errors (watch for red text)
- [ ] Run pre-flight check:
  ```bash
  bash ~/Zyrcon/cascadia-os/scripts/pre-flight.sh
  ```
- [ ] Confirm all pre-flight checks pass (25/25 ✅)
  - If any fail, resolve before continuing

---

## Operator Verification

- [ ] Open PRISM dashboard: [http://localhost:6300](http://localhost:6300)
- [ ] Confirm all core services show **green**:
  - FLINT (LLM engine)
  - CREW (orchestrator)
  - VAULT (secrets store)
  - BELL (notification bus)
- [ ] Run full test suite:
  ```bash
  cd ~/Zyrcon/cascadia-os
  python -m pytest tests/ -q
  ```
- [ ] Confirm **354+ tests passing, 0 failures**
  - Record actual count: _______ tests passing

---

## License Activation

- [ ] Open `~/Zyrcon/cascadia-os/config.json`
- [ ] Enter the client's license key in the `"license_key"` field
- [ ] Save the file
- [ ] Restart Cascadia OS:
  ```bash
  bash ~/Zyrcon/cascadia-os/stop.sh && bash ~/Zyrcon/cascadia-os/start.sh
  ```
- [ ] Verify correct tier shown in PRISM dashboard header
- [ ] Confirm operator limits match client's contracted tier:
  - Lite: up to 5 operators
  - Pro: up to 20 operators
  - Enterprise: unlimited

---

## Operator Setup

- [ ] Install client-specific operators via DEPOT (PRISM → DEPOT tab)
- [ ] Configure each operator with client credentials:
  - [ ] Scout: configure lead source (website form, email forwarding)
  - [ ] Aurelia: configure outbound email (SMTP or Gmail)
  - [ ] Any additional operators per client contract
- [ ] Run a test lead through the full pipeline:
  1. Submit a test lead via the configured intake source
  2. Confirm lead appears in PRISM → Scout
  3. Confirm draft reply is generated (check PRISM → Aurelia queue)
  4. Approve the draft from the test iPhone
  5. Confirm the approval notification is received and the draft is sent
- [ ] Verify approval notification appears on test iPhone ✅

---

## Network Configuration

- [ ] Set a static IP address on the client's network:
  - **Settings → Network → [Ethernet/WiFi] → Details → TCP/IP → Manual**
  - Record the assigned IP below
- [ ] Confirm PRISM is accessible from another machine on the same LAN:
  ```
  http://[static-ip]:6300
  ```
- [ ] Test remote approval from client's iPhone on the **same WiFi** ✅
- [ ] Test remote approval from client's iPhone on **cellular** (or via VPN if required) ✅
  - Note: cellular access requires the LAN to be accessible externally or via VPN
  - Recommended: set up Tailscale for zero-config remote access

---

## Final Checks

- [ ] Set Mac mini to restart automatically after power loss:
  **System Settings → Energy → Start up automatically after a power failure**
- [ ] Set Cascadia OS to launch on startup:
  ```bash
  bash ~/Zyrcon/cascadia-os/scripts/setup-autostart.sh
  ```
- [ ] Confirm autostart is active by rebooting and verifying PRISM loads at
  `http://localhost:6300` without manual intervention
- [ ] Run the demo script and walk the client through PRISM:
  ```bash
  bash ~/Zyrcon/cascadia-os/demo.sh
  ```
- [ ] Document final configuration in client folder (see below)
- [ ] Leave printed copy of QUICKSTART_MACOS.md with the client

---

## Sign-Off

| Field | Value |
|---|---|
| Serial number | |
| License key (last 8 characters) | |
| Static IP assigned | |
| macOS version | |
| Cascadia OS version | |
| Test count (should be 354+) | |
| Client name | |
| Installation address | |
| QA completed by | |
| Date | |

---

*Keep a copy of this completed checklist in the client's onboarding folder.*
*Questions: support@zyrcon.ai*
