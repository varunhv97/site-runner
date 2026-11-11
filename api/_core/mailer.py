"""Outgoing email through Resend's HTTP API (https://resend.com). Used for abuse-report notifications.

Off until RESEND_API_KEY is set. Sending never raises: callers get False and the reason is logged, so a mail
outage can't break what triggered it (a report is stored either way).
"""

import logging
import os

import httpx

log = logging.getLogger("site-runner")

RESEND_URL = "https://api.resend.com/emails"
DEFAULT_FROM = "Site Runner <reports@spinsite.app>"


def enabled() -> bool:
    return bool(os.environ.get("RESEND_API_KEY"))


async def send(to: str, subject: str, text: str, reply_to: str | None = None) -> bool:
    """Send a plain-text email. True when Resend accepted it."""
    key = os.environ.get("RESEND_API_KEY")
    if not key or not to:
        return False
    body = {
        "from": os.environ.get("MAIL_FROM") or DEFAULT_FROM,
        "to": [to],
        "subject": subject[:200],
        "text": text,
    }
    if reply_to:
        body["reply_to"] = reply_to
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.post(RESEND_URL, json=body, headers={"Authorization": f"Bearer {key}"})
        if r.status_code >= 300:
            log.error("email to %s not sent: %s %s", to, r.status_code, r.text[:300])
            return False
        return True
    except httpx.HTTPError as e:
        log.error("email to %s not sent: %s", to, e)
        return False
