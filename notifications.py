"""
notifications.py — SMTP email paging for Vigil.
Falls back to logging if SMTP not configured.
"""
from __future__ import annotations

import asyncio
import logging
import os
import aiosmtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

SMTP_HOST     = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER     = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
# Any hardcoded defaults or credentials check
ALERT_TO      = os.getenv("ALERT_EMAIL_TO", SMTP_USER)

_CONFIGURED = bool(SMTP_USER and SMTP_PASSWORD and "your_gmail" not in SMTP_USER)


def _build_html(incident_id, service, summary, hypothesis, recommended_action, confidence):
    pct = f"{confidence:.0f}%"
    color = "#ef4444" if confidence < 50 else "#f59e0b" if confidence < 80 else "#22c55e"
    return f"""<!DOCTYPE html><html><body style="font-family:sans-serif;background:#0f172a;color:#e2e8f0;padding:24px">
<div style="max-width:600px;margin:0 auto;background:#1e293b;border:1px solid #334155;border-radius:12px;padding:24px">
  <h2 style="color:#f8fafc;margin:0 0 8px">⚠️ Vigil Incident Alert #{incident_id[:8]}</h2>
  <p style="color:#64748b;margin:0 0 16px">Service: <b style="color:#e2e8f0">{service}</b></p>
  <div style="background:#0f172a;padding:12px;border-radius:8px;margin-bottom:16px">
    <span style="color:#94a3b8">Confidence: </span><b style="color:{color};font-size:20px">{pct}</b>
    <div style="background:#1e293b;height:8px;border-radius:4px;margin-top:8px">
      <div style="background:{color};width:{pct};height:100%;border-radius:4px"></div>
    </div>
  </div>
  <h3 style="color:#94a3b8;font-size:12px;text-transform:uppercase">Summary</h3>
  <p style="color:#cbd5e1">{summary}</p>
  <h3 style="color:#94a3b8;font-size:12px;text-transform:uppercase">Root Cause Hypothesis</h3>
  <p style="background:#0f172a;border-left:3px solid #f59e0b;padding:12px;color:#fde68a">{hypothesis}</p>
  <h3 style="color:#94a3b8;font-size:12px;text-transform:uppercase">Recommended Action</h3>
  <p style="background:#0f172a;border-left:3px solid #22c55e;padding:12px;color:#86efac">{recommended_action}</p>
  <p style="font-size:12px;color:#475569;text-align:center;margin-top:24px;border-top:1px solid #334155;padding-top:16px">
    Sent by Vigil Autonomous Incident Response Agent
  </p>
</div></body></html>"""


async def send_page_email(incident_id, service, summary, root_cause_hypothesis,
                          recommended_action, confidence) -> dict:
    logger.warning(
        "📟 PAGE — incident=%s service=%s confidence=%.0f\n  %s\n  → %s",
        incident_id, service, confidence, root_cause_hypothesis, recommended_action,
    )

    if not _CONFIGURED:
        # INTENTIONAL DESIGN CHOICE: SMTP is optional for this demo.
        # When SMTP_USER / SMTP_PASSWORD are not set in .env, Vigil falls back
        # to structured logging so the paging event is never silently lost.
        # To enable real email: set SMTP_USER, SMTP_PASSWORD, ALERT_EMAIL_TO in .env.
        logger.warning(
            "EMAIL FALLBACK: would have sent alert to '%s' for incident %s on %s "
            "(confidence %.0f%%). Set SMTP_USER/SMTP_PASSWORD in .env to enable real email.",
            ALERT_TO or "<no ALERT_EMAIL_TO set>", incident_id, service, confidence,
        )
        return {"success": False, "error": "SMTP not configured — email logged as fallback"}

    html = _build_html(incident_id, service, summary, root_cause_hypothesis, recommended_action, confidence)
    plain = f"VIGIL ALERT\nService: {service}\nConfidence: {confidence:.0f}%\n\n{summary}\n\nHypothesis: {root_cause_hypothesis}\nAction: {recommended_action}"
    subject = f"[VIGIL] {service} needs attention — confidence {confidence:.0f}%"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"Vigil <{SMTP_USER}>"
    msg["To"]      = ALERT_TO
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html,  "html"))

    try:
        smtp = aiosmtplib.SMTP(
            hostname=SMTP_HOST,
            port=SMTP_PORT,
            use_tls=(SMTP_PORT == 465),
            timeout=10,
        )
        await smtp.connect()
        if SMTP_PORT == 587:
            await smtp.starttls()
        await smtp.login(SMTP_USER, SMTP_PASSWORD)
        await smtp.send_message(msg)
        await smtp.quit()
        logger.info("📧 Alert email sent for incident %s", incident_id)
        return {"success": True}
    except Exception as exc:
        logger.error("📧 SMTP failed: %s", exc)
        return {"success": False, "error": str(exc)}

