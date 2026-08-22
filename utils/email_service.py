"""
Email Service — OTP Delivery Abstraction
=========================================
Sends OTP emails via standard SMTP (compatible with Gmail, Resend SMTP,
Mailgun SMTP, SendGrid, or any self-hosted server).

Configuration via environment variables:
    SMTP_HOST       — SMTP server hostname (e.g. smtp.gmail.com)
    SMTP_PORT       — SMTP port (587 for STARTTLS, 465 for SSL, 25 for plain)
    SMTP_USER       — SMTP authentication username / email
    SMTP_PASSWORD   — SMTP authentication password / app password
    FROM_EMAIL      — Sender address shown to recipients
    SMTP_USE_TLS    — "true" to use SMTPS (port 465), "false" for STARTTLS (default)

Security notes:
    - OTP values are NEVER logged, even on error.
    - SMTP credentials are read from environment, never from client input.
    - Connection errors produce safe user-facing messages only.
"""

import asyncio
import smtplib
import ssl
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
import os

logger = logging.getLogger(__name__)


class EmailDeliveryError(Exception):
    """Raised when OTP email delivery fails. Safe to surface to callers."""
    pass


class EmailService:
    """
    SMTP-backed email service for OTP delivery.

    Instantiated once at application startup. All configuration comes
    from environment variables — no credentials are accepted at call time.
    """

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
        raw = (os.getenv("FROM_EMAIL") or self.user).strip().strip('"').strip("'")
        return raw

    @property
    def from_name(self) -> str:
        return os.getenv("FROM_NAME", "TG Drive").strip().strip('"').strip("'")

    @property
    def use_ssl(self) -> bool:
        return os.getenv("SMTP_USE_TLS", "false").strip().lower() == "true" or self.port == 465

    @property
    def is_configured(self) -> bool:
        """Returns True if all required SMTP env vars are present."""
        return bool(self.host and self.user and self.password and self.from_email)

    def _build_otp_message(self, to_email: str, otp_placeholder: str) -> MIMEMultipart:
        """
        Builds the email MIME object.
        otp_placeholder is the 6-digit code — caller is responsible for
        passing only the display value, not logging it.
        """
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Your TG Drive verification code"
        msg["From"] = formataddr((self.from_name, self.from_email))
        msg["To"] = to_email

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

        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))
        return msg

    def _send_sync(self, to_email: str, otp: str) -> None:
        """
        Synchronous SMTP delivery with automatic dual-port fallback (587 STARTTLS / 465 SSL).
        """
        if not self.is_configured:
            logger.error("SMTP is not configured. Missing required SMTP variables.")
            raise EmailDeliveryError(
                "SMTP is not configured. Set SMTP_HOST, SMTP_USER, SMTP_PASSWORD, and FROM_EMAIL."
            )

        msg = self._build_otp_message(to_email, otp)
        host = self.host
        user = self.user
        pwd = self.password
        from_addr = self.from_email

        # Determine ports to try: primary port first, then fallback port
        ports_to_try = [self.port]
        fallback_port = 465 if self.port == 587 else 587
        if fallback_port not in ports_to_try:
            ports_to_try.append(fallback_port)

        errors = []
        for p in ports_to_try:
            try:
                context = ssl.create_default_context()
                if p == 465:
                    # SMTPS — direct SSL connection
                    with smtplib.SMTP_SSL(host, p, context=context, timeout=12) as server:
                        server.login(user, pwd)
                        server.sendmail(from_addr, [to_email], msg.as_string())
                else:
                    # STARTTLS — plain connection upgraded to TLS
                    with smtplib.SMTP(host, p, timeout=12) as server:
                        server.ehlo()
                        server.starttls(context=context)
                        server.ehlo()
                        server.login(user, pwd)
                        server.sendmail(from_addr, [to_email], msg.as_string())

                logger.info(f"OTP email sent successfully to {to_email[:3]}***@*** via {host}:{p}")
                return
            except Exception as e:
                logger.warning(f"SMTP delivery attempt via {host}:{p} failed: {type(e).__name__} ({e})")
                errors.append(f"{p}: {type(e).__name__}")

        # If all ports failed
        logger.error(f"All SMTP delivery attempts to {host} failed: {', '.join(errors)}")
        raise EmailDeliveryError("Failed to send verification email. Check SMTP configuration.")

    async def send_otp(self, to_email: str, otp: str) -> None:
        """
        Async wrapper — runs the blocking SMTP call in a thread executor.
        Raises EmailDeliveryError on failure with a safe user-facing message.
        OTP value is never logged.
        """
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._send_sync, to_email, otp)


# Singleton — import this instance throughout the application
email_service = EmailService()
