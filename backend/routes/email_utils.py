"""
email_utils.py — Transactional email helpers, provider-agnostic.

Send order: (1) generic SMTP if SMTP_HOST is set — works with Brevo, Mailgun,
Amazon SES, Gmail app-password, any provider; (2) SendGrid HTTP API as
fallback if only SENDGRID_API_KEY is set. Switching providers = changing env
vars, never code.

Brevo setup (recommended, free 300/day):
  SMTP_HOST=smtp-relay.brevo.com  SMTP_PORT=587
  SMTP_USER=<brevo account email> SMTP_PASS=<brevo SMTP key>
  SMTP_FROM=noreply@autosparefinder.co.il
"""
import os
import asyncio
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
import httpx

SENDGRID_API = "https://api.sendgrid.com/v3/mail/send"
FROM_EMAIL = os.getenv("SMTP_FROM", os.getenv("SENDGRID_FROM_EMAIL", "noreply@autosparefinder.co.il"))
FROM_NAME  = "AutoSpareFinder"


def _smtp_send_blocking(to_email: str, to_name: str, subject: str,
                        html: str, text: str) -> bool:
    host = os.getenv("SMTP_HOST", "").strip()
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASS", "").strip()

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr((FROM_NAME, FROM_EMAIL))
    msg["To"] = formataddr((to_name or to_email, to_email))
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP(host, port, timeout=20) as server:
        server.starttls()
        if user and password:
            server.login(user, password)
        server.sendmail(FROM_EMAIL, [to_email], msg.as_string())
    return True


async def _send_via_smtp(to_email: str, to_name: str, subject: str,
                         html: str, text: str) -> bool:
    try:
        return await asyncio.to_thread(
            _smtp_send_blocking, to_email, to_name, subject, html, text
        )
    except Exception as e:
        print(f"[Email] SMTP send failed: {e}")
        return False


async def _send_via_sendgrid(to_email: str, to_name: str, subject: str,
                              html: str, text: str) -> bool:
    # Preferred path: generic SMTP (Brevo/Mailgun/SES/Gmail — provider-agnostic)
    if os.getenv("SMTP_HOST", "").strip():
        return await _send_via_smtp(to_email, to_name, subject, html, text)

    key = os.getenv("SENDGRID_API_KEY", "").strip()
    if not key or key.startswith("SG.CHA"):  # unset or CHANGE_ME placeholder
        print(f"[Email] No SMTP_HOST and no valid SENDGRID_API_KEY — skipping email to {to_email}")
        return False
    payload = {
        "personalizations": [{"to": [{"email": to_email, "name": to_name}]}],
        "from": {"email": FROM_EMAIL, "name": FROM_NAME},
        "subject": subject,
        "content": [
            {"type": "text/plain", "value": text},
            {"type": "text/html",  "value": html},
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                SENDGRID_API,
                json=payload,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
            if r.status_code >= 400:
                print(f"[Email] SendGrid error {r.status_code}: {r.text[:200]}")
                return False
        return True
    except Exception as e:
        print(f"[Email] Send failed: {e}")
        return False


async def send_email(to_email: str, to_name: str, subject: str, html: str, text: str) -> bool:
    """Provider-neutral transactional send. Uses Gmail (or any) SMTP when SMTP_HOST is
    set — e.g. SMTP_HOST=smtp.gmail.com SMTP_PORT=587 SMTP_USER=<gmail> SMTP_PASS=<app
    password> — and falls back to the SendGrid HTTP API only if no SMTP is configured.
    Switching providers is env-only, never code."""
    return await _send_via_sendgrid(to_email, to_name, subject, html, text)


async def send_template(to_email: str, to_name: str, template: tuple) -> bool:
    """Send a (subject, html, text) tuple produced by email_templates.py."""
    try:
        subject, html, text = template
    except Exception:
        print("[Email] send_template got a malformed template tuple")
        return False
    return await send_email(to_email, to_name, subject, html, text)


async def send_order_confirmation_email(
    to_email: str,
    full_name: str,
    order_number: str,
    tracking_number: str,
    tracking_url: str,
    order_url: str,
) -> bool:
    # Use the shared branded RTL shell (email_templates) instead of ad-hoc inline HTML.
    try:
        from email_templates import delivery_update
        subject, html, text = delivery_update(
            full_name, order_number, "אושרה ונשלחה לספק", tracking_number, tracking_url)
    except Exception:
        subject = f"✅ הזמנתך אושרה — {order_number} | AutoSpareFinder"
        html = (f'<div dir="rtl" style="font-family:Arial,sans-serif">שלום {full_name}, '
                f'ההזמנה {order_number} אושרה. מעקב: <a href="{tracking_url}">{tracking_number}</a></div>')
        text = f"שלום {full_name}, הזמנה {order_number} אושרה. מעקב: {tracking_url}"
    return await _send_via_sendgrid(to_email, full_name, subject, html, text)


async def send_payment_failed_email(
    to_email: str,
    full_name: str,
    order_number: str,
    support_url: str = "https://autosparefinder.co.il/support",
) -> bool:
    subject = f"⚠️ בעיה בתשלום — {order_number} | AutoSpareFinder"
    html = f"""
    <div dir="rtl" style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px">
      <h2 style="color:#e63946">שלום {full_name},</h2>
      <p>נתקלנו בבעיה בעיבוד תשלום ההזמנה <strong>{order_number}</strong>.</p>
      <p>נציג שירות ייצור איתך קשר בהקדם. לתמיכה מיידית:
         <a href="{support_url}">לחץ כאן</a></p>
    </div>
    """
    text = f"שלום {full_name},\nנתקלנו בבעיה בהזמנה {order_number}. נציג יצור קשר.\n"
    return await _send_via_sendgrid(to_email, full_name, subject, html, text)
