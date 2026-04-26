# WooCommerce Order Confirmation Email Template

Copy and formatting guide for the WooCommerce "Completed Order" email — the message customers receive when their purchase is confirmed and their license key is delivered.

---

## Email Subject Line

```
Your Cascadia OS license is ready — Order #{order_number}
```

---

## Email Body

Paste the following into **WooCommerce → Settings → Emails → Completed Order → Manage → Email body additional content**. WooCommerce automatically prepends the order summary table and appends the footer.

---

**Subject:** Your Cascadia OS license is ready — Order #{order_number}

---

Hi {customer_first_name},

Your order is confirmed and your license key is ready to activate.

**Your license key is included in the order details below.** Copy it exactly as shown — it is case-sensitive.

---

### Getting Started

1. **Download Cascadia OS** from [zyrcon.com/download](https://zyrcon.com/download)
2. **Run the installer** and follow the setup wizard
3. When prompted, enter your license key to activate
4. Complete the LLM setup (takes ~5 minutes on first run)

Need help? The full setup guide is at [docs.zyrcon.com](https://docs.zyrcon.com).

---

### Your Subscription

- **Plan:** {product_name}
- **Renews:** {subscription_next_payment_date}
- **Manage subscription:** [My Account → Subscriptions](https://zyrcon.store/my-account/subscriptions/)

You can upgrade, pause, or cancel your subscription at any time from your account page.

---

### Support

- **Documentation:** [docs.zyrcon.com](https://docs.zyrcon.com)
- **Email support:** support@zyrcon.com
- **Response time:** within 1 business day (Pro), same business day (Business)

---

Thank you for choosing Cascadia OS.

— The Zyrcon Team

---

## One-Time Purchase Variant (AI Server)

For the `cascadia-ai-server` SKU, use this additional content instead:

---

Hi {customer_first_name},

Thank you for purchasing Cascadia OS AI Server Setup.

A member of the Zyrcon team will contact you within **1 business day** to schedule your remote provisioning session. Please have the following ready:

- Server hostname or IP address
- SSH access credentials (we will generate a temporary key pair)
- Your preferred setup time (weekdays, business hours preferred)

Once provisioning is complete, you will receive a separate confirmation with your server's Cascadia OS access URL.

Questions before then? Email us at support@zyrcon.com with your order number: **#{order_number}**

— The Zyrcon Team

---

## Formatting Notes

### WooCommerce Template Variables

| Variable | Value |
|----------|-------|
| `{order_number}` | WooCommerce order ID |
| `{customer_first_name}` | Billing first name |
| `{product_name}` | Product title as listed in WooCommerce |
| `{subscription_next_payment_date}` | Next renewal date (subscriptions only) |

These are resolved by WooCommerce at send time. Do not change the variable names.

### Serial Number Placement

The WooCommerce Serial Numbers plugin injects the license key directly into the order details table — no manual placement is needed. Customers see:

```
Product                     Serial Number
Cascadia OS — Pro Individual  CZPRO-XXXX-XXXX-XXXX-XXXX
```

### Email Design

WooCommerce uses its own email template engine. To match brand colors:

1. **WooCommerce → Settings → Emails → Email template**
2. Set header image: upload the Zyrcon wordmark (white on transparent, 300×80px)
3. Header background color: `#1a1a2e`
4. Body background: `#f8f8f8`
5. Body text color: `#1a1a2e`
6. Base color (buttons, links): `#7c3aed`

### Testing

Before going live, send test emails using:
- **WooCommerce → Settings → Emails → Completed Order → Preview**
- Place a $0 test order in Stripe test mode and verify the full email renders correctly with the license key injected
