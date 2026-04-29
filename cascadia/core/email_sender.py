"""
Simple SMTP email sender using Bluehost (or any SMTP server).
Uses stdlib only - no external dependencies.
"""

import os
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import structlog

log = structlog.get_logger()


def send_license_email(
    to_email: str,
    license_key: str,
    tier: str,
) -> bool:
    """
    Send license key via SMTP.
    Returns True if sent successfully.
    """
    
    # Load SMTP config from environment
    smtp_host = os.getenv("EMAIL_SMTP_HOST", "mail.zyrcon.ai")
    smtp_port = int(os.getenv("EMAIL_SMTP_PORT", "465"))
    smtp_user = os.getenv("EMAIL_SMTP_USER", "hello@zyrcon.ai")
    smtp_password = os.getenv("EMAIL_SMTP_PASSWORD")
    from_email = os.getenv("EMAIL_FROM", "hello@zyrcon.ai")
    from_name = os.getenv("EMAIL_FROM_NAME", "Zyrcon Labs")
    
    if not smtp_password:
        log.error("email.smtp.no_password", msg="EMAIL_SMTP_PASSWORD not set in .env")
        return False
    
    # Create message
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Your Cascadia OS {tier.title()} License Key"
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = to_email
    
    # Plain text version
    text = f"""
Welcome to Cascadia OS {tier.title()}!

Your license key:
{license_key}

To activate:
1. Open PRISM at http://localhost:6300
2. Go to Settings → License
3. Enter your license key above
4. Click Activate

Need help? Reply to this email or visit https://zyrcon.ai/support

Best regards,
The Zyrcon Labs Team
    """.strip()
    
    # HTML version
    html = f"""
<html>
  <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
    <h2 style="color: #2563eb;">Welcome to Cascadia OS {tier.title()}!</h2>
    
    <p>Thank you for your purchase. Here's your license key:</p>
    
    <div style="background: #f3f4f6; padding: 16px; border-radius: 8px; font-family: monospace; font-size: 18px; text-align: center; margin: 20px 0;">
      {license_key}
    </div>
    
    <h3 style="margin-top: 30px;">How to Activate:</h3>
    <ol style="line-height: 1.8;">
      <li>Open PRISM at <code>http://localhost:6300</code></li>
      <li>Go to <strong>Settings → License</strong></li>
      <li>Enter your license key above</li>
      <li>Click <strong>Activate</strong></li>
    </ol>
    
    <p style="margin-top: 30px; color: #6b7280;">
      Need help? Reply to this email or visit 
      <a href="https://zyrcon.ai/support" style="color: #2563eb;">zyrcon.ai/support</a>
    </p>
    
    <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 30px 0;">
    
    <p style="color: #9ca3af; font-size: 12px;">
      Zyrcon Labs<br>
      Houston, TX<br>
      <a href="https://zyrcon.ai" style="color: #9ca3af;">zyrcon.ai</a>
    </p>
  </body>
</html>
    """.strip()
    
    # Attach both versions
    part1 = MIMEText(text, "plain")
    part2 = MIMEText(html, "html")
    msg.attach(part1)
    msg.attach(part2)
    
    # Send via SMTP
    try:
        # Create secure SSL context
        context = ssl.create_default_context()
        
        # Connect and send
        with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context) as server:
            server.login(smtp_user, smtp_password)
            server.sendmail(from_email, to_email, msg.as_string())
        
        log.info(
            "email.sent",
            to=to_email,
            tier=tier,
            license_key=license_key[:13] + "..."
        )
        return True
        
    except Exception as e:
        log.error(
            "email.send_failed",
            to=to_email,
            error=str(e)
        )
        return False


def test_smtp_connection() -> bool:
    """
    Test SMTP connection without sending email.
    Returns True if connection successful.
    """
    smtp_host = os.getenv("EMAIL_SMTP_HOST", "mail.zyrcon.ai")
    smtp_port = int(os.getenv("EMAIL_SMTP_PORT", "465"))
    smtp_user = os.getenv("EMAIL_SMTP_USER")
    smtp_password = os.getenv("EMAIL_SMTP_PASSWORD")
    
    if not smtp_password:
        log.error("email.test.no_password")
        return False
    
    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context) as server:
            server.login(smtp_user, smtp_password)
        
        log.info("email.test.success", host=smtp_host)
        return True
        
    except Exception as e:
        log.error("email.test.failed", error=str(e))
        return False
