"""Who is calling? Checks the Google ID token the web page sends with every request.

How sign-in works:
  1. The page shows Google's "Sign in with Google" button. Google checks the person and
     gives the page an ID token: a small signed note saying "this is Google account 123,
     valid for 1 hour, issued for AgentForge".
  2. The page sends it with every request:  Authorization: Bearer <token>
  3. We check Google's signature, that it was issued for OUR client ID, and that it hasn't
     expired. Nobody can forge one without Google's private key.
  4. The account's ID (the "sub" claim) becomes user_id, and every query already filters
     by user_id, so each person only ever sees their own runs.

We never see a password. We store only Google's account ID: not the email, not the name.
"""

import time
from dataclasses import dataclass

from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

GOOGLE_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}
_CACHE_LIMIT = 10_000


@dataclass(frozen=True)
class VerifiedToken:
    user_id: str  # Google's "sub": unique per account, never reused, never changes
    expires_at: float  # seconds since 1970, from the token's "exp"


# The page asks for a run's status every 2 seconds. Checking a token means fetching
# Google's public keys, so a token we've already checked is remembered until it expires.
_verified: dict[str, VerifiedToken] = {}
_google = google_requests.Request()


def user_id_from_token(token: str, client_id: str) -> str:
    """The Google account ID inside a valid token. Raises ValueError if it isn't valid."""
    cached = _verified.get(token)
    if cached and cached.expires_at > time.time():
        return cached.user_id

    # Checks the signature (made with Google's private key), that "aud" is our client
    # ID (a token issued to another website is refused), and that it hasn't expired.
    claims = id_token.verify_oauth2_token(token, _google, client_id)
    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise ValueError("token wasn't issued by Google")

    if len(_verified) >= _CACHE_LIMIT:
        _verified.clear()  # simple and safe: everyone is just checked once more
    _verified[token] = VerifiedToken(user_id=claims["sub"], expires_at=float(claims["exp"]))
    return claims["sub"]
