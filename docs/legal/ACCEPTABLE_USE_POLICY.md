# Acceptable Use Policy

**Zyrcon, Inc.**
Effective Date: January 1, 2026

---

## 1. Scope

This Acceptable Use Policy ("AUP") applies to all users of Cascadia OS and the DEPOT marketplace, including end users, developers who publish operators, and Enterprise account holders. Use of the platform constitutes agreement to this AUP. This AUP supplements Zyrcon's Terms of Service and Developer Agreement.

---

## 2. General Prohibited Uses

Regardless of tier or role, you may not use the Cascadia OS platform or DEPOT to:

- **Spam:** Send unsolicited commercial messages, bulk emails, or automated outreach without recipient consent
- **Surveillance:** Monitor individuals without their knowledge or consent, or build operators designed to track people without disclosure
- **Unauthorized scraping:** Collect data from websites or services in violation of their terms of service or without a lawful basis
- **Illegal content:** Create, transmit, or store content that violates applicable law, including content that infringes intellectual property rights, constitutes defamation, or violates export control regulations
- **Malware distribution:** Publish, distribute, or install software designed to damage systems, steal credentials, or perform unauthorized actions
- **Mass messaging abuse:** Use the platform's communication connectors to send automated messages at volumes that constitute abuse, harassment, or violation of platform policies
- **Impersonation:** Misrepresent your identity, impersonate another person, organization, or Zyrcon itself
- **Credential stuffing:** Use the platform to test stolen credential lists against any service
- **Circumventing security controls:** Attempt to bypass, disable, or undermine the DEPOT approval gate, operator manifest validation, or any other platform security mechanism

---

## 3. DEPOT-Specific Rules

Operators and connectors published on DEPOT are subject to the following additional rules, in addition to the Developer Agreement:

### 3.1 Data Handling
Operators must not transmit user data — including conversation content, business records, file contents, or API keys — to any third-party server that is not explicitly declared in the operator manifest and approved by the user at install time. Any undisclosed data exfiltration is grounds for immediate removal and account termination.

### 3.2 Operator Isolation
Operators must not read from, write to, modify, or interfere with other operators installed on the same Cascadia OS instance. Each operator must operate within its declared resource and permission boundaries.

### 3.3 Approval Gate Compliance
All operators that perform actions with external side effects — including sending messages, posting to external APIs, writing to databases, executing financial transactions, or modifying files outside the operator's declared working directory — must implement the DEPOT approval gate. Users must explicitly approve each category of external action before the operator may execute it. Operators that bypass or simulate the approval gate will be removed from DEPOT without appeal.

### 3.4 Manifest Accuracy
Operator manifests must accurately describe all network endpoints contacted, permissions requested, data retained, and third-party services invoked. Omissions are treated as violations. Zyrcon audits manifests on a rolling basis and may require updates at any time.

---

## 4. Enforcement

Zyrcon takes violations of this AUP seriously. Depending on the severity and frequency of the violation, enforcement actions may include:

| Severity | Action |
|---|---|
| First-time / minor violation | Written warning via email |
| Repeated or moderate violation | Temporary account or operator suspension |
| Serious or intentional violation | Permanent account termination, DEPOT de-listing |
| Illegal activity | Referral to appropriate law enforcement authorities |

Zyrcon reserves the right to take immediate action without warning when a violation poses a risk to user safety, platform integrity, or third-party systems. Enforcement decisions are made at Zyrcon's sole discretion.

---

## 5. Appeals

If you believe an enforcement action was taken in error, you may appeal by emailing legal@zyrcon.ai within 14 days of the action. Your appeal should include a detailed explanation and any supporting documentation. Appeals are reviewed within 10 business days. Accounts suspended for confirmed illegal activity are not eligible for appeal.

---

## 6. Reporting Violations

If you observe behavior that violates this AUP — including operators that appear to exfiltrate data, impersonate legitimate tools, or engage in prohibited activities — please report it to:

**abuse@zyrcon.ai**

Include the operator name (if applicable), a description of the behavior, and any supporting evidence (screenshots, logs). All reports are reviewed within three (3) business days. Zyrcon will not disclose the identity of reporters without consent.

---

## 7. Changes to This Policy

Zyrcon may update this AUP at any time. Material changes will be communicated via email and in-app notice at least 14 days before taking effect. Continued use of the platform after the effective date constitutes acceptance of the revised policy.

---

**Violations contact:** abuse@zyrcon.ai
**All other legal inquiries:** legal@zyrcon.ai
**Zyrcon, Inc., San Francisco, CA**
