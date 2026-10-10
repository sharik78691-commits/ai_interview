"""Google OAuth 2.0 / OpenID Connect helpers.

Flow (authorization code):
1. ``/api/auth/google/login`` builds the Google authorize URL with a random
   ``state`` (stored in a short-lived HttpOnly cookie) and redirects.
2. Google redirects back to ``/api/auth/google/callback`` with ``code`` + ``state``.
3. The backend verifies ``state``, exchanges the code for tokens, and validates
   the ``id_token`` (issuer, audience, expiry, signature via Google's JWKS).
4. Only a **verified** Google email is accepted; the local user is created or
   linked, then a normal session cookie is issued.

The Google client secret never leaves the backend.
"""
import logging
import secrets
from urllib.parse import urlencode

import httpx

from app.core.config import get_settings
from app.schemas.auth import GoogleCallbackResult

logger = logging.getLogger(__name__)

GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = {"https://accounts.google.com", "accounts.google.com"}

# Minimal scopes: identity only. No Google password is ever requested.
GOOGLE_SCOPES = "openid email profile"

# Seconds of tolerance when validating an id_token's exp/nbf/iat claims.
# Google issues `iat` from Google's clock; a server whose clock runs even a
# little behind rejects the token as "issued in the future" without this.
CLOCK_SKEW_LEEWAY = 60


class OAuthError(Exception):
    """Raised when the Google exchange/validation fails."""


def new_state() -> str:
    return secrets.token_urlsafe(24)


def build_authorize_url(state: str) -> str:
    settings = get_settings()
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": GOOGLE_SCOPES,
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
        "include_granted_scopes": "true",
    }
    return f"{GOOGLE_AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code_for_identity(code: str) -> GoogleCallbackResult:
    """Exchange the auth code and validate the returned id_token.

    Validates issuer, audience, expiry and signature (via Google JWKS). Only a
    verified email is accepted.
    """
    settings = get_settings()
    if not settings.google_configured:
        raise OAuthError("Google OAuth is not configured.")

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "redirect_uri": settings.google_redirect_uri,
                    "grant_type": "authorization_code",
                },
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            logger.warning("Google token exchange failed: %s", exc)
            raise OAuthError("Unable to sign in with Google.") from exc

        if resp.status_code != 200:
            logger.warning("Google token endpoint returned %s", resp.status_code)
            raise OAuthError("Unable to sign in with Google.")

        payload = resp.json()
        id_token = payload.get("id_token")
        if not id_token:
            raise OAuthError("Google did not return an identity token.")

        claims = await _verify_id_token(client, id_token)

    subject = claims.get("sub")
    email = claims.get("email")
    email_verified = claims.get("email_verified")
    if isinstance(email_verified, str):
        email_verified = email_verified.lower() == "true"

    if not subject or not email:
        raise OAuthError("Google identity is missing required claims.")
    if not email_verified:
        # Only verified Google email identities are accepted.
        raise OAuthError("Your Google email is not verified.")

    return GoogleCallbackResult(
        subject=str(subject),
        email=str(email),
        email_verified=True,
        name=str(claims.get("name") or ""),
    )


async def _verify_id_token(client: httpx.AsyncClient, id_token: str) -> dict:
    """Verify signature + standard claims of a Google id_token.

    Uses Google's published JWKS. Falls back to the tokeninfo endpoint only if
    JWKS verification is unavailable, so a network hiccup does not silently
    disable validation.
    """
    settings = get_settings()
    try:
        from authlib.jose import JsonWebKey, jwt as jose_jwt
    except Exception as exc:  # pragma: no cover - dependency missing
        raise OAuthError("Google token verification is unavailable.") from exc

    try:
        jwks_resp = await client.get(GOOGLE_JWKS_URL)
        jwks_resp.raise_for_status()
        jwks = jwks_resp.json()
        key_set = JsonWebKey.import_key_set(jwks)
        claims = jose_jwt.decode(id_token, key_set)
        # leeway tolerates clock skew: without it, `iat` a moment ahead of the
        # server clock fails verification with "issued in the future".
        claims.validate(leeway=CLOCK_SKEW_LEEWAY)  # exp / nbf / iat
    except OAuthError:
        raise
    except Exception as exc:
        logger.warning("id_token JWKS verification failed: %s", exc)
        raise OAuthError("Unable to verify your Google identity.") from exc

    # Audience must be our client id.
    aud = claims.get("aud")
    if isinstance(aud, list):
        aud_ok = settings.google_client_id in aud
    else:
        aud_ok = aud == settings.google_client_id
    if not aud_ok:
        raise OAuthError("Google token audience mismatch.")

    # Issuer must be Google.
    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise OAuthError("Google token issuer mismatch.")

    return dict(claims)
