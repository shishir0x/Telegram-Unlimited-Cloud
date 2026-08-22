"""
Email Service — OTP Delivery Abstraction (Resend API & SMTP)
============================================================
Sends OTP emails via:
  1. Resend HTTP API (Recommended for cloud platforms like Render where SMTP is blocked)
  2. Standard SMTP / SMTPS (Gmail, self-hosted, etc.)

Configuration via environment variables:
  Resend API:
    RESEND_API_KEY     — Resend API Key (starts with re_...)
    RESEND_FROM_EMAIL  — Sender address (default: "TG Drive <onboarding@resend.dev>")

  SMTP (Fallback):
    SMTP_HOST          — SMTP server hostname (e.g. smtp.gmail.com)
    SMTP_PORT          — SMTP port (587 or 465)
    SMTP_USER          — SMTP authentication username / email
    SMTP_PASSWORD      — SMTP authentication password / app password
    FROM_EMAIL         — Sender address shown to recipients

Security notes:
    - OTP values are NEVER logged, even on error.
    - Credentials are read from environment, never from client input.
"""

import asyncio
import json
import logging
import os
import smtplib
import ssl
import urllib.request
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

logger = logging.getLogger(__name__)


class EmailDeliveryError(Exception):
    """Raised when OTP email delivery fails. Safe to surface to callers."""
    pass


class EmailService:
    """
    Email delivery service supporting both Resend HTTPS API and SMTP.
    """

    @property
    def resend_api_key(self) -> str:
        return os.getenv("RESEND_API_KEY", "").strip().strip('"').strip("'")

    @property
    def resend_from_email(self) -> str:
        custom = os.getenv("RESEND_FROM_EMAIL", "").strip().strip('"').strip("'")
        if custom:
            return custom
        # Default to Resend sandbox sender
        return f"{self.from_name} <onboarding@resend.dev>"

    @property
    def host(self) -> str:
        return os.getenv("SMTP_HOST", "smtp.gmail.com").strip().strip('"').strip("'")

    @property
    def port(self) -> int:
        val = os.getenv("SMTP_PORT", "587").strip().strip('"').strip("'")
        try:
            return int(val)
        except ValueError:
            return 587

    @property
    def user(self) -> str:
        return os.getenv("SMTP_USER", "").strip().strip('"').strip("'")

    @property
    def password(self) -> str:
        raw = os.getenv("SMTP_PASSWORD", "").strip().strip('"').strip("'")
        if "gmail.com" in self.host.lower():
            return raw.replace(" ", "")
        return raw

    @property
    def from_email(self) -> str:
        return (os.getenv("FROM_EMAIL") or self.user).strip().strip('"').strip("'")

    @property
    def from_name(self) -> str:
        return os.getenv("FROM_NAME", "TG Drive").strip().strip('"').strip("'")

    @property
    def is_configured(self) -> bool:
        """Returns True if either Resend API or SMTP is configured."""
        if bool(self.resend_api_key):
            return True
        return bool(self.host and self.user and self.password and self.from_email)

    def _build_content(self, otp_placeholder: str) -> tuple[str, str]:
        text_body = f"""Your TG Drive verification code is: {otp_placeholder}

This code expires in 5 minutes and can only be used once.

If you did not request this code, someone may be attempting to access your drive. You can safely ignore this email.

— TG Drive Security
"""
        html_body = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background:#f6f8fb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background:#f6f8fb;padding:40px 20px;">
    <tr><td align="center">
      <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="480" style="background:#ffffff;border-radius:12px;box-shadow:0 2px 12px rgba(0,0,0,0.08);overflow:hidden;">
        <!-- Header -->
        <tr>
          <td style="background:linear-gradient(135deg,#1a73e8 0%,#0d47a1 100%);padding:32px 40px;text-align:center;">
            <h1 style="margin:0;color:#ffffff;font-size:22px;font-weight:600;letter-spacing:-0.3px;">🔐 TG Drive</h1>
            <p style="margin:6px 0 0;color:rgba(255,255,255,0.85);font-size:14px;">Verification Code</p>
          </td>
        </tr>
        <!-- Body -->
        <tr>
          <td style="padding:40px 40px 32px;">
            <p style="margin:0 0 24px;color:#202124;font-size:16px;line-height:1.6;">
              Enter the following code to complete your sign-in:
            </p>
            <div style="background:#f1f3f4;border-radius:10px;padding:24px;text-align:center;margin:0 0 24px;">
              <span style="font-size:38px;font-weight:700;letter-spacing:10px;color:#1a73e8;font-family:'Courier New',monospace;">{otp_placeholder}</span>
            </div>
            <p style="margin:0 0 8px;color:#5f6368;font-size:14px;line-height:1.6;">
              ⏱️ This code expires in <strong>5 minutes</strong> and can only be used once.
            </p>
            <p style="margin:0;color:#5f6368;font-size:14px;line-height:1.6;">
              If you did not request this code, someone may be attempting to access your drive. Ignore this email — your account remains secure.
            </p>
          </td>
        </tr>
        <!-- Footer -->
        <tr>
          <td style="background:#f8f9fa;padding:20px 40px;border-top:1px solid #e8eaed;">
            <p style="margin:0;color:#9aa0a6;font-size:12px;text-align:center;">
              This is an automated security email from TG Drive. Do not reply.
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""
        return text_body, html_body

    def _send_via_resend(self, to_email: str, otp: str) -> None:
        """
        Sends OTP email via Resend HTTPS REST API (Port 443 — never blocked).
        """
        text_body, html_body = self._build_content(otp)
        payload = {
            "from": self.resend_from_email,
            "to": [to_email],
            "subject": "Your TG Drive verification code",
            "html": html_body,
            "text": text_body
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            "https://api.resend.com/emails",
            data=data,
            headers={
                "Authorization": f"Bearer {self.resend_api_key}",
                "Content-Type": "application/json",
                "User-Agent": "TG-Drive-App/1.0"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=12) as response:
                if response.status in (200, 201):
                    logger.info(f"OTP email sent successfully via Resend API to {to_email[:3]}***@***")
                    return
                else:
                    body = response.read().decode("utf-8")
                    logger.error(f"Resend API error status {response.status}: {body}")
                    raise EmailDeliveryError(f"Resend API returned status {response.status}")
        except urllib.error.HTTPError as he:
            body = he.read().decode("utf-8") if he.fp else ""
            logger.error(f"Resend API HTTP error: {he.code} - {body}")
            raise EmailDeliveryError(f"Resend API error: {he.code}")
        except Exception as e:
            logger.error(f"Resend API request failed: {type(e).__name__} ({e})")
            raise EmailDeliveryError("Failed to connect to Resend API")

    def _send_via_smtp(self, to_email: str, otp: str) -> None:
        """
        Synchronous SMTP delivery with automatic dual-port fallback (587 STARTTLS / 465 SSL).
        """
        text_body, html_body = self._build_content(otp)
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Your TG Drive verification code"
        msg["From"] = formataddr((self.from_name, self.from_email))
        msg["To"] = to_email
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        host = self.host
        user = self.user
        pwd = self.password
        from_addr = self.from_email

        ports_to_try = [self.port]
        fallback_port = 465 if self.port == 587 else 587
        if fallback_port not in ports_to_try:
            ports_to_try.append(fallback_port)

        errors = []
        for p in ports_to_try:
            try:
                context = ssl.create_default_context()
                if p == 465:
                    with smtplib.SMTP_SSL(host, p, context=context, timeout=10) as server:
                        server.login(user, pwd)
                        server.sendmail(from_addr, [to_email], msg.as_string())
                else:
                    with smtplib.SMTP(host, p, timeout=10) as server:
                        server.ehlo()
                        server.starttls(context=context)
                        server.ehlo()
                        server.login(user, pwd)
                        server.sendmail(from_addr, [to_email], msg.as_string())

                logger.info(f"OTP email sent successfully via SMTP to {to_email[:3]}***@*** via {host}:{p}")
                return
            except Exception as e:
                logger.warning(f"SMTP delivery attempt via {host}:{p} failed: {type(e).__name__} ({e})")
                errors.append(f"{p}: {type(e).__name__}")

        logger.error(f"All SMTP delivery attempts to {host} failed: {', '.join(errors)}")
        raise EmailDeliveryError("Failed to send verification email via SMTP.")

    def _send_sync(self, to_email: str, otp: str) -> None:
        # 1. Prefer Resend API if API key is configured
        if self.resend_api_key:
            try:
                self._send_via_resend(to_email, otp)
                return
            except Exception as re_err:
                logger.warning(f"Resend delivery failed, attempting SMTP fallback: {re_err}")

        # 2. Try SMTP if configured
        if self.host and self.user and self.password:
            self._send_via_smtp(to_email, otp)
            return

        raise EmailDeliveryError("No valid email service (Resend or SMTP) is configured.")

    async def send_otp(self, to_email: str, otp: str) -> None:
        """
        Async wrapper — runs the email dispatch in a thread executor.
        Raises EmailDeliveryError on failure with a safe user-facing message.
        """
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._send_sync, to_email, otp)


# Singleton — import this instance throughout the application
email_service = EmailService()
