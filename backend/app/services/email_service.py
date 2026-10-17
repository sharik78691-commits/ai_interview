"""Outbound email (password-reset links). Stdlib smtplib only — no new deps.

``send_password_reset_email`` returns True on delivery, False otherwise, and
NEVER raises: a mail failure must not break the forgot-password endpoint
(which always answers with the same generic message either way).
"""
import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_SMTP_TIMEOUT_SECONDS = 10


def _build_reset_message(to_email: str, reset_link: str) -> EmailMessage:
    settings = get_settings()
    app_name = settings.app_name
    msg = EmailMessage()
    msg["Subject"] = f"Reset your {app_name} password"
    msg["From"] = settings.smtp_from
    msg["To"] = to_email
    msg.set_content(
        f"""Hi,

Someone requested a password reset for your {app_name} account
({to_email}). If that was you, choose a new password here:

{reset_link}

This link works once and expires in 1 hour. If you did not ask for a
reset, just ignore this email — your password stays unchanged.

— The {app_name} team
"""
    )
    msg.add_alternative(
        f"""<p>Hi,</p>
<p>Someone requested a password reset for your {app_name} account
({to_email}). If that was you, choose a new password here:</p>
<p><a href="{reset_link}">Reset my password</a></p>
<p><small>This link works once and expires in 1 hour. If you did not ask
for a reset, just ignore this email — your password stays unchanged.</small></p>
<p>— The {app_name} team</p>
""",
        subtype="html",
    )
    return msg


def send_password_reset_email(to_email: str, reset_link: str) -> bool:
    """Send the reset link. Returns True on delivery, False otherwise."""
    settings = get_settings()
    if not settings.smtp_configured:
        logger.info(
            "SMTP not configured — reset link NOT emailed (see server log in dev)."
        )
        return False
    try:
        msg = _build_reset_message(to_email, reset_link)
        if settings.smtp_use_tls:
            context = ssl.create_default_context()
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port,
                              timeout=_SMTP_TIMEOUT_SECONDS) as server:
                server.starttls(context=context)
                if settings.smtp_user:
                    server.login(settings.smtp_user, settings.smtp_password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port,
                              timeout=_SMTP_TIMEOUT_SECONDS) as server:
                if settings.smtp_user:
                    server.login(settings.smtp_user, settings.smtp_password)
                server.send_message(msg)
        logger.info("Password reset email sent (delivery accepted by SMTP host).")
        return True
    except Exception as exc:
        # Never leak the recipient address into logs (same PII rule as the
        # enumeration-resistant auth responses).
        logger.warning("Password reset email failed to send (%s: %s)",
                       type(exc).__name__, exc)
        return False
