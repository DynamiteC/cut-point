"""Authentication for the state-changing half of the API.

POST /analyze and POST /jobs each run the full pipeline: ClickHouse queries, one
ffmpeg extraction per detected cliff, and one Gemini video inference per clip
with the whole clip sent as inline bytes. Deployed to Cloud Run without a guard,
that is an open funnel into the project's Vertex AI bill -- an attacker pays
nothing, there is no per-caller accounting, and GET /trailers publicly
enumerates every id that makes a valid request.

Read-only endpoints (/trailers, /report/*) stay public so the static UI hosted on
GitHub Pages can fetch them cross-origin without shipping a credential.

Fails closed: auth is required unless CUTPOINT_REQUIRE_AUTH is explicitly "false".
"""

from __future__ import annotations

import os

from fastapi import Header, HTTPException

_GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")


def auth_required() -> bool:
    return os.environ.get("CUTPOINT_REQUIRE_AUTH", "true").lower() != "false"


def _allowed_callers() -> set[str]:
    """Optional service-account allowlist, comma separated. Empty means any
    caller holding a valid Google-signed token for this audience.
    """
    raw = os.environ.get("CUTPOINT_ALLOWED_INVOKERS", "")
    return {e.strip() for e in raw.split(",") if e.strip()}


def verify_google_identity(authorization: str | None = Header(default=None)) -> str:
    """FastAPI dependency. Returns the verified caller identity.

    Verifies the bearer token is Google-signed and, when CUTPOINT_AUDIENCE is
    set, that it was minted for this service rather than replayed from another.
    """
    if not auth_required():
        return "auth-disabled"

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()

    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    # An unset CUTPOINT_AUDIENCE silently disabled the audience check, so a
    # token minted for any other service would have been accepted on signature
    # alone. deploy_all.sh always sets it, but a guard that depends on remembering
    # to set an environment variable is not a guard. In cloud mode its absence is
    # now a refusal rather than a downgrade.
    audience = os.environ.get("CUTPOINT_AUDIENCE") or None
    if audience is None and os.environ.get("K_SERVICE"):
        raise HTTPException(
            status_code=500,
            detail="server misconfigured: CUTPOINT_AUDIENCE is required in a deployment",
        )
    try:
        claims = id_token.verify_oauth2_token(
            token, google_requests.Request(), audience=audience
        )
    except Exception as exc:  # any verification failure is a 401, never a 500
        raise HTTPException(status_code=401, detail="invalid identity token") from exc

    if claims.get("iss") not in _GOOGLE_ISSUERS:
        raise HTTPException(status_code=401, detail="untrusted token issuer")

    caller = claims.get("email") or claims.get("sub") or "unknown"
    allowed = _allowed_callers()
    if allowed and caller not in allowed:
        raise HTTPException(status_code=403, detail="caller not permitted")
    return caller
