# Developer Agreement

**Zyrcon, Inc.**
Effective Date: January 1, 2026

---

## 1. Overview

This Developer Agreement ("Agreement") governs your participation as a developer in the DEPOT operator and connector marketplace, operated by Zyrcon, Inc. ("Zyrcon"). By submitting an operator or connector to DEPOT, you agree to this Agreement in addition to Zyrcon's Terms of Service.

---

## 2. Eligibility

To publish on DEPOT, you must:

- Be at least 18 years of age, or be a validly formed legal entity
- Have the legal authority to enter into this Agreement
- Maintain a valid Zyrcon developer account with accurate contact and payment information
- Comply with all applicable laws in your jurisdiction

Zyrcon reserves the right to verify eligibility and to reject or revoke developer status at its discretion.

---

## 3. Account Requirements

Developer accounts require a verified email address and a connected Stripe account for receiving payments. You are responsible for keeping your account information accurate and your credentials secure. Each developer may maintain one primary developer account; exceptions require written approval from Zyrcon.

---

## 4. Operator Submission Standards

All operators submitted to DEPOT must:

- **Quality:** Function as described in the listing, pass automated functional tests, and not produce materially misleading outputs
- **Security:** Not contain malicious code, unauthorized data collection, or network calls not disclosed in the operator manifest
- **Manifest compliance:** Include a complete and accurate `operator.manifest.json` specifying all required permissions, external endpoints, and data flows
- **Approval gate:** Implement the DEPOT approval gate for any action with external side effects (sending messages, writing to external systems, making purchases, etc.); users must explicitly approve such actions before execution

Zyrcon may update submission standards with 30 days' notice. Existing operators must comply within 60 days of notice.

---

## 5. Prohibited Content

Operators may not:

- Collect, transmit, or store user data beyond what is explicitly disclosed in the manifest and approved by the user
- Facilitate spam, harassment, illegal surveillance, or scraping without user consent
- Contain adult content, hate speech, or content that promotes violence or illegal activity
- Impersonate other operators, developers, or Zyrcon itself
- Bypass or disable platform security mechanisms or approval gates
- Interfere with other operators or with Cascadia OS system processes

---

## 6. Review Process

Zyrcon will review each operator submission within **five (5) business days** of receipt. We will notify you of approval, rejection, or a request for additional information via email. Incomplete submissions restart the review clock upon resubmission.

**Appeals:** If your submission is rejected, you may appeal by emailing legal@zyrcon.ai within 14 days of the rejection notice. Appeals are reviewed by a senior member of the DEPOT team within 10 business days. The outcome of the appeal is final.

---

## 7. Revenue Share

Zyrcon processes all operator payments on your behalf through Stripe. Revenue share terms are:

| Lifetime Earnings (USD) | Developer Share | Zyrcon Share |
|---|---|---|
| $0 – $25,000 | 100% | 0% |
| Above $25,000 | 80% | 20% |

Lifetime earnings are calculated on a per-developer-account basis across all published operators. Revenue share terms may change with 90 days' written notice; existing sales at the time of notice are not affected.

---

## 8. Payment Schedule

Payments are issued monthly via Stripe on or before the 15th day of the month following the period in which earnings accrued. A minimum payout threshold of $25.00 applies; balances below the threshold carry forward to the next period. You are responsible for any taxes applicable to your earnings.

---

## 9. Intellectual Property

You retain full ownership of all intellectual property in the operators you create and submit. By submitting an operator to DEPOT, you grant Zyrcon a non-exclusive, worldwide, royalty-free license to host, distribute, and display your operator listing and associated marketing assets solely for the purpose of operating DEPOT. This license terminates when your operator is removed from DEPOT.

Zyrcon does not acquire any rights to your source code.

---

## 10. Data Handling Requirements

Operators must handle user data in strict compliance with Zyrcon's Acceptable Use Policy and all applicable privacy laws. Specifically:

- Operators must not transmit user business data to third-party servers without explicit user consent disclosed in the manifest
- Operators must not retain user data beyond what is necessary to perform the operator's stated function
- Operators must honor user requests to delete locally stored data

Zyrcon may audit operator behavior at any time. Violations may result in immediate removal and account termination.

---

## 11. Termination

Zyrcon may suspend or terminate your developer account and remove your operators from DEPOT if you:

- Violate these terms, the Terms of Service, or the Acceptable Use Policy
- Submit operators with security vulnerabilities, malicious behavior, or manifest misrepresentations
- Engage in fraudulent activity or misuse the payment system
- Receive sustained user complaints that indicate systematic quality or compliance failures

You may terminate your developer account at any time by contacting legal@zyrcon.ai. Termination does not affect payment obligations for earnings already accrued.

---

## 12. Indemnification

You agree to indemnify, defend, and hold harmless Zyrcon and its officers, directors, employees, and agents from and against any claims, damages, losses, liabilities, and expenses (including reasonable attorneys' fees) arising out of or related to: (a) your operators, (b) your breach of this Agreement, or (c) your violation of any third-party rights.

---

## 13. Disclaimer and Limitation of Liability

ZYRCON MAKES NO WARRANTIES REGARDING OPERATOR SALES VOLUMES, REVENUE, OR MARKETPLACE PERFORMANCE. ZYRCON'S LIABILITY TO YOU UNDER THIS AGREEMENT SHALL NOT EXCEED THE REVENUE SHARE AMOUNTS OWED TO YOU IN THE THREE MONTHS PRECEDING THE CLAIM.

---

**Contact:** legal@zyrcon.ai | Zyrcon, Inc., San Francisco, CA
