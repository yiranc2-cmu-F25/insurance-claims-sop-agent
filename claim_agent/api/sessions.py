"""Opaque browser sessions and per-session locking for this single-worker demo."""

import re
import secrets
from threading import RLock


SESSION_COOKIE = "insurance_session"
SESSION_MAX_AGE = 60 * 60 * 24
SESSION_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
# Serialize state changes for a browser session, including multiple tabs.
# Fixed stripes avoid accumulating one lock per session in this single-worker demo.
SESSION_LOCKS = [RLock() for _ in range(64)]


def session_context(response, cookie):
    session_id = cookie if cookie and SESSION_PATTERN.fullmatch(cookie) else secrets.token_urlsafe(32)
    if session_id != cookie:
        response.set_cookie(
            key=SESSION_COOKIE, value=session_id, max_age=SESSION_MAX_AGE,
            httponly=True, samesite="lax", secure=False,
        )
    response.headers["Cache-Control"] = "no-store"
    return {"configurable": {"thread_id": session_id}}, SESSION_LOCKS[hash(session_id) % len(SESSION_LOCKS)]
