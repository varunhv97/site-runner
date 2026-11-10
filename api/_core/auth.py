"""Google sign-in (OAuth 2.0 authorization-code flow) and signed session cookies.

No session store: the cookie holds the user's Google id, email and an expiry, signed with
SESSION_SECRET (HMAC-SHA256). It is httpOnly, so page scripts (including shared sites) can't read it.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from urllib.parse import urlencode

import httpx

SESSION_COOKIE = "sr_session"
STATE_COOKIE = "sr_oauth_state"
SESSION_TTL = 30 * 24 * 3600
STATE_TTL = 600

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


class AuthError(Exception):
    pass


def enabled() -> bool:
    return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))


def _secret() -> bytes:
    secret = os.environ.get("SESSION_SECRET")
    if not secret:
        raise AuthError("SESSION_SECRET is not set")
    return secret.encode()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(payload: dict) -> str:
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    mac = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{mac}"


def verify(value: str | None) -> dict | None:
    """The payload of a signed value that is intact and unexpired, else None."""
    if not value or "." not in value:
        return None
    body, mac = value.rsplit(".", 1)
    try:
        good = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(mac, good):
            return None
        payload = json.loads(_unb64(body))
    except (AuthError, ValueError):
        return None
    if payload.get("exp", 0) < time.time():
        return None
    return payload


def new_state(next_path: str) -> tuple[str, str]:
    """(state for the Google URL, signed cookie value that remembers it and where to go after)."""
    state = secrets.token_urlsafe(24)
    safe_next = next_path if next_path.startswith("/") and not next_path.startswith("//") else "/app"
    return state, sign({"state": state, "next": safe_next, "exp": int(time.time()) + STATE_TTL})


def login_url(state: str, redirect_uri: str) -> str:
    params = {
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


async def fetch_google_user(code: str, redirect_uri: str) -> dict:
    """Exchange the code for tokens, then read the signed-in user's profile from Google."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            token_res = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": os.environ["GOOGLE_CLIENT_ID"],
                    "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            token_res.raise_for_status()
            access_token = token_res.json()["access_token"]
            info_res = await client.get(GOOGLE_USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"})
            info_res.raise_for_status()
            info = info_res.json()
    except (httpx.HTTPError, KeyError, ValueError) as e:
        raise AuthError(f"Google sign-in failed: {e}") from e
    if not info.get("sub") or not info.get("email_verified", False):
        raise AuthError("Google account has no verified email")
    return {"sub": info["sub"], "email": info.get("email", ""), "name": info.get("name", "")}


def session_value(user: dict) -> str:
    return sign(
        {"sub": user["sub"], "email": user["email"], "name": user["name"], "exp": int(time.time()) + SESSION_TTL}
    )
