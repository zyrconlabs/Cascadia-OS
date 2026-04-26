# Apple Pay Setup Guide

How to enable Apple Pay on your WooCommerce store and configure it for the Cascadia OS iOS app (if applicable).

---

## Overview

Apple Pay on your WooCommerce store is handled entirely by Stripe — you do **not** need an Apple Developer account for website payments. Stripe manages domain verification, the merchant certificate, and the payment session automatically.

For the Cascadia OS iOS app, Apple Pay requires configuration in Xcode with your Apple Developer account.

---

## Part 1: Apple Pay on Your WooCommerce Store

### Prerequisites

- WooCommerce with Stripe plugin installed and connected (see `docs/woocommerce_setup.md`)
- HTTPS enabled on your domain (required by Apple)
- Domain verified with Stripe (see below)

### Step 1: Enable Payment Request Buttons in Stripe

1. Go to **WooCommerce → Settings → Payments → Stripe → Settings**
2. Scroll to **Payment Request Buttons**
3. Toggle: **Enable Payment Request Buttons** → On
4. Button type: **Buy now** (or **Apple Pay** to show the Apple Pay badge explicitly)
5. Save changes

When enabled, Stripe automatically displays:
- **Apple Pay** on Safari (Mac, iPhone, iPad)
- **Google Pay** on Chrome
- **Microsoft Pay** on Edge

### Step 2: Domain Verification

Stripe automatically handles domain verification for you. When you connect WooCommerce to Stripe and enable Payment Request Buttons, Stripe:

1. Registers your domain with Apple
2. Hosts the required `/.well-known/apple-developer-merchantid-domain-association` file via its payment gateway
3. Renews the verification automatically

You do not need to manually upload any files or create an Apple Merchant ID for website payments.

**To confirm verification is working:**

```
https://yourstore.com/.well-known/apple-developer-merchantid-domain-association
```

This URL should return a JSON file (served by Stripe's integration). If it returns 404, the WooCommerce Stripe plugin is not correctly intercepting the request — check that the plugin is active and WooCommerce permalinks are flushed (**Settings → Permalinks → Save Changes**).

### Step 3: Test Apple Pay

1. Open your WooCommerce store in Safari on an iPhone or Mac with Apple Pay configured
2. Add a product to cart and proceed to checkout
3. The Apple Pay button should appear above the standard checkout form
4. Complete a test transaction using Stripe test mode cards

> If the button does not appear in Safari, confirm:
> - You are on HTTPS
> - Safari has at least one card configured in Wallet
> - You are testing on a real device (Apple Pay does not work in simulators for web)

---

## Part 2: Apple Pay in a Native iOS App

If you are building or distributing a Cascadia OS iOS companion app, follow these steps to enable Apple Pay in the app.

### Prerequisites

- Apple Developer account (required for app distribution)
- Stripe iOS SDK integrated in your Xcode project
- App ID configured in the Apple Developer portal

### Step 1: Create a Merchant ID

1. Log in to [developer.apple.com](https://developer.apple.com)
2. Go to **Certificates, Identifiers & Profiles → Identifiers → Merchant IDs**
3. Click **+** to register a new Merchant ID
4. Identifier: `merchant.ai.zyrcon`
5. Description: `Zyrcon Cascadia OS`
6. Click **Continue** and **Register**

### Step 2: Configure in Xcode

1. Open your Xcode project
2. Select your app target → **Signing & Capabilities**
3. Click **+ Capability** and add **Apple Pay**
4. In the Apple Pay capability, add merchant ID: `merchant.ai.zyrcon`

### Step 3: Connect Merchant ID to Stripe

1. In your Stripe Dashboard → **Settings → Payment methods → Apple Pay**
2. Click **Add new domain** (for web) or **Add new application** (for iOS)
3. For iOS: Stripe will generate an Apple Pay certificate — download it
4. In Apple Developer portal → **Merchant IDs → merchant.ai.zyrcon → Apple Pay Payment Processing Certificate**
5. Upload the certificate signing request (CSR) from Stripe
6. Download the resulting certificate and upload it back to Stripe

### Step 4: Implement in Code (Stripe iOS SDK)

```swift
import StripePaymentSheet

let merchantId = "merchant.ai.zyrcon"

var configuration = PaymentSheet.Configuration()
configuration.applePay = .init(
    merchantId: merchantId,
    merchantCountryCode: "US"
)

let paymentSheet = PaymentSheet(
    paymentIntentClientSecret: clientSecret,
    configuration: configuration
)
```

### Step 5: Test on Device

Apple Pay **cannot be tested in the iOS Simulator** for in-app payments. You must use:

- A physical iPhone or iPad
- A Stripe test card added to Apple Wallet via Stripe's test card feature
- Stripe test mode enabled in your app

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Apple Pay button not showing on website | Check HTTPS, flush WooCommerce permalinks, confirm Stripe Payment Request Buttons enabled |
| Domain verification file returns 404 | Deactivate and reactivate the Stripe WooCommerce plugin, flush permalinks |
| Apple Pay not showing in Safari | User has no cards in Wallet, or device does not support Apple Pay |
| iOS app: merchant validation failed | Stripe certificate not uploaded or expired — regenerate in Stripe Dashboard |
| iOS app: button appears but payment fails | Merchant ID mismatch between Xcode capability and Stripe configuration |

---

## References

- [Stripe Apple Pay Documentation](https://stripe.com/docs/apple-pay)
- [WooCommerce Stripe Plugin Docs](https://woocommerce.com/document/stripe/)
- [Apple Developer — Merchant IDs](https://developer.apple.com/account/resources/identifiers/list/merchant)
- [Stripe iOS SDK — Apple Pay](https://stripe.com/docs/apple-pay?platform=ios)
