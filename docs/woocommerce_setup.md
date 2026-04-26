# WooCommerce Setup Guide — Zyrcon Cascadia OS

Complete setup guide for selling Cascadia OS licenses on WooCommerce with Stripe, subscription management, and automatic license key delivery.

---

## 1. Plugin Installation

Install the following plugins from the WordPress Plugin Directory:

| Plugin | Purpose |
|--------|---------|
| **WooCommerce** | Core e-commerce engine |
| **WooCommerce Subscriptions** | Recurring billing for monthly plans |
| **WooCommerce Serial Numbers** | Automatic license key delivery on purchase |
| **Stripe for WooCommerce** (WooPayments or Stripe plugin) | Payment processing |

**Installation steps:**

1. Log in to WordPress Admin → **Plugins → Add New**
2. Search for each plugin by name, install, and activate
3. WooCommerce setup wizard will launch automatically — complete it:
   - Store country: United States (or your location)
   - Currency: USD
   - Industry: Technology / Software
   - Product type: Downloads / Virtual

---

## 2. Stripe Connection

### Connect Stripe to WooCommerce

1. Navigate to **WooCommerce → Settings → Payments**
2. Enable **Stripe** and click **Set up**
3. Click **Connect with Stripe** — you will be redirected to Stripe
4. Log in to your Stripe account and authorize the WooCommerce connection
5. Return to WordPress — your Stripe keys will be populated automatically

### Configure Stripe Settings

- **Mode**: Live (for production) / Test (for development)
- **Payment Request Buttons**: Enable (adds Apple Pay / Google Pay)
- **Saved Cards**: Enable (improves repeat purchase flow)
- **Statement Descriptor**: `ZYRCON CASCADIA`

### Webhook (for license delivery on payment)

In your Stripe Dashboard → **Developers → Webhooks → Add endpoint**:

```
https://yourstore.com/?wc-api=wc_stripe
```

Select events:
- `payment_intent.succeeded`
- `customer.subscription.created`
- `customer.subscription.deleted`
- `invoice.payment_succeeded`
- `invoice.payment_failed`

Copy the **Signing Secret** and paste it into WooCommerce → Settings → Payments → Stripe → **Webhook Secret**.

---

## 3. Product Definitions

Create one WooCommerce product per plan. All software products are **Virtual** (no shipping).

### Product: Pro Individual

| Field | Value |
|-------|-------|
| Name | Cascadia OS — Pro Individual |
| Type | Simple subscription |
| Price | $49.00 / month |
| SKU | `cascadia-pro-individual` |
| Virtual | Yes |
| Subscription period | Monthly |
| Subscription length | Never expires |
| Short description | Single-user license. Full AI workflow automation, all built-in operators, local LLM support. |

### Product: Pro Workspace

| Field | Value |
|-------|-------|
| Name | Cascadia OS — Pro Workspace |
| Type | Simple subscription |
| Price | $99.00 / month |
| SKU | `cascadia-pro-workspace` |
| Virtual | Yes |
| Subscription period | Monthly |
| Subscription length | Never expires |
| Short description | Up to 5 seats. Shared operator library, team approval workflows, centralized vault. |

### Product: Business Starter

| Field | Value |
|-------|-------|
| Name | Cascadia OS — Business Starter |
| Type | Simple subscription |
| Price | $299.00 / month |
| SKU | `cascadia-business-starter` |
| Virtual | Yes |
| Subscription period | Monthly |
| Subscription length | Never expires |
| Short description | Up to 20 seats. Enterprise dashboard, fleet management, audit logs, priority support. |

### Product: Business Growth

| Field | Value |
|-------|-------|
| Name | Cascadia OS — Business Growth |
| Type | Simple subscription |
| Price | $499.00 / month |
| SKU | `cascadia-business-growth` |
| Virtual | Yes |
| Subscription period | Monthly |
| Subscription length | Never expires |
| Short description | Up to 50 seats. All Starter features plus advanced analytics, white-label options, dedicated onboarding. |

### Product: Business Max

| Field | Value |
|-------|-------|
| Name | Cascadia OS — Business Max |
| Type | Simple subscription |
| Price | $999.00 / month |
| SKU | `cascadia-business-max` |
| Virtual | Yes |
| Subscription period | Monthly |
| Subscription length | Never expires |
| Short description | Unlimited seats. Full enterprise suite, custom integrations, SLA support, quarterly business review. |

### Product: AI Server (Hardware)

| Field | Value |
|-------|-------|
| Name | Cascadia OS — AI Server Setup |
| Type | Simple product (one-time) |
| Price | $899.00 |
| SKU | `cascadia-ai-server` |
| Virtual | Yes |
| Short description | One-time professional setup for a dedicated on-premise AI inference server. Includes remote provisioning and 90-day support. |

---

## 4. Serial Numbers (License Keys) Setup

### Configure WooCommerce Serial Numbers Plugin

1. Go to **Serial Numbers → Settings**
2. Enable: **Auto-complete orders** for virtual products
3. Enable: **Send keys in email** (uses WooCommerce order email template)
4. Key separator: None (keys are self-delimited with dashes)

### Upload Pre-Generated Keys

For each product SKU, generate a batch of keys using the bulk generator:

```bash
python3 scripts/generate_bulk_licenses.py \
    --tier pro \
    --count 500 \
    --days 365 \
    --output data/license_keys/pro_individual_batch.csv
```

Import into Serial Numbers:

1. **Serial Numbers → Add Serial Numbers → Import CSV**
2. Select product: match to the correct SKU
3. Upload the CSV file
4. Keys are now queued for automatic delivery

> **Note:** Re-run the generator and re-import before a batch runs out. The plugin will alert you at 10% remaining.

---

## 5. Subscription Settings

### WooCommerce Subscriptions Configuration

Navigate to **WooCommerce → Settings → Subscriptions**:

| Setting | Value |
|---------|-------|
| Mixed checkout | Allow (subscribers can add one-time items) |
| Multiple subscriptions | Allow |
| Turn off automatic payments | No |
| Renewal payment retry | 3 attempts (days 1, 3, 7 after failure) |
| Failed payment email | Enable |
| Cancelled subscription email | Enable |
| Synchronize renewals | No (each renews on its own anniversary) |

### Proration

- Enable proration on plan upgrades
- Disable proration on downgrades (immediate at next billing cycle)

### Cancellation Policy

- Allow subscribers to cancel immediately from My Account
- Add a note: "Access continues until end of billing period"
- Grace period after expiry: 3 days (to accommodate payment retries)

---

## 6. Email Settings

### WooCommerce Email Configuration

Navigate to **WooCommerce → Settings → Emails**:

| Email | Status | Notes |
|-------|--------|-------|
| New order | Enable | Admin notification only |
| Processing order | Disable | Not needed for virtual products |
| Completed order | Enable | **This is when license keys are delivered** |
| Customer invoice | Enable | Sent on renewal |
| Failed order | Enable | Triggers retry flow |
| Subscription trial ending | Enable | If trials are offered |
| Subscription expired | Enable | License invalidation notice |

### Email Sender

- From name: `Zyrcon`
- From address: `noreply@zyrcon.store` (or your domain)

### SMTP (Recommended)

Use WP Mail SMTP plugin with a transactional provider (Postmark, SendGrid, or Mailgun):

1. Install **WP Mail SMTP**
2. Navigate to WP Mail SMTP → Settings
3. Mailer: Postmark (recommended) or SendGrid
4. Enter your API key from the transactional email provider
5. Send a test email to verify delivery

### Order Completion Email

The order completed email automatically includes the serial number(s) purchased. Customize the email template in:

**WooCommerce → Settings → Emails → Completed Order → Manage**

See `docs/woocommerce_email_template.md` for the full copy and formatting guide.

---

## Quick Reference — Product SKUs and Prices

| SKU | Name | Price | Type |
|-----|------|-------|------|
| `cascadia-pro-individual` | Pro Individual | $49/mo | Subscription |
| `cascadia-pro-workspace` | Pro Workspace | $99/mo | Subscription |
| `cascadia-business-starter` | Business Starter | $299/mo | Subscription |
| `cascadia-business-growth` | Business Growth | $499/mo | Subscription |
| `cascadia-business-max` | Business Max | $999/mo | Subscription |
| `cascadia-ai-server` | AI Server Setup | $899 | One-time |
