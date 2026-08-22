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
        return os.getenv("SMTP_HOST", "").strip()

    @property
    def port(self) -> int:
        try:
            return int(os.getenv("SMTP_PORT", "587").strip())
        except ValueError:
            return 587

    @property
    def user(self) -> str:
        return os.getenv("SMTP_USER", "").strip()

    @property
    def password(self) -> str:
        raw = os.getenv("SMTP_PASSWORD", "").strip()
        if "gmail.com" in self.host.lower() and len(raw.replace(" ", "")) == 16:
            return raw.replace(" ", "")
        return raw

    @property
    def from_email(self) -> str:
        return (os.getenv("FROM_EMAIL") or self.user).strip()

    @property
    def from_name(self) -> str:
        return os.getenv("FROM_NAME", "TG Drive").strip()

    @property
    def use_ssl(self) -> bool:
        return os.getenv("SMTP_USE_TLS", "false").strip().lower() == "true"

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
        Synchronous SMTP delivery. Run via executor to keep it non-blocking.
        OTP value is only used to build the email body — never logged.
        """
        if not self.is_configured:
            raise EmailDeliveryError(
                "SMTP is not configured. Set SMTP_HOST, SMTP_USER, SMTP_PASSWORD, and FROM_EMAIL."
            )

        msg = self._build_otp_message(to_email, otp)

        try:
            if self.use_ssl:
                # SMTPS — direct SSL connection (port 465)
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(self.host, self.port, context=context, timeout=15) as server:
                    server.login(self.user, self.password)
                    server.sendmail(self.from_email, [to_email], msg.as_string())
            else:
                # STARTTLS — plain connection upgraded to TLS (port 587)
                context = ssl.create_default_context()
                with smtplib.SMTP(self.host, self.port, timeout=15) as server:
                    server.ehlo()
                    server.starttls(context=context)
                    server.ehlo()
                    server.login(self.user, self.password)
                    server.sendmail(self.from_email, [to_email], msg.as_string())

            # Log success WITHOUT including OTP value
            logger.info(f"OTP email sent successfully to {to_email[:3]}***@***")

        except smtplib.SMTPAuthenticationError:
            logger.error("SMTP authentication failed — check SMTP_USER and SMTP_PASSWORD")
            raise EmailDeliveryError("Email delivery failed: authentication error")
        except smtplib.SMTPConnectError:
            logger.error(f"Cannot connect to SMTP server {self.host}:{self.port}")
            raise EmailDeliveryError("Email delivery failed: cannot reach mail server")
        except smtplib.SMTPException as e:
            logger.error(f"SMTP error during OTP delivery: {type(e).__name__}")
            raise EmailDeliveryError("Email delivery failed: SMTP error")
        except TimeoutError:
            logger.error(f"SMTP connection timed out to {self.host}:{self.port}")
            raise EmailDeliveryError("Email delivery failed: connection timed out")
        except OSError as e:
            logger.error(f"Network error during email delivery: {type(e).__name__}")
            raise EmailDeliveryError("Email delivery failed: network error")

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
