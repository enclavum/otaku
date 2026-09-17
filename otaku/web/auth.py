"""Who may ask: the token the page carries, and what signs it.

The page signs in ONCE, with the password in the body of one request,
and the server answers with the token in an `HttpOnly` cookie the browser
sends on every request after (`server._set_cookie`). What the token is, is an
HS256 JWT — the ordinary shape, so anything that reads one can read this.
What it is NOT is server state: nothing here is remembered between
requests, so signing out is the cookie being taken away and there is
nothing to revoke.

Tokens are signed with the stored password hash as the HMAC key rather
than a secret of their own, so a changed password — a new hash, with a new
random salt — invalidates every token ever issued, with no list to keep.
Checking a typed password is not this module's: that is
`backend.passwords`', and its cost is the rate limiting. Nor does a token
captured off the wire help anyone guess at the password: the hash that
signed it is out of reach without the salt, which never leaves the file.

The header is PINNED, never read. A JWT that names its own algorithm is
where the `alg: none` family of bugs comes from: the code checking the
token asks the token how to check it. Here the first segment must equal
the one we emit, byte for byte, and nothing in it is ever parsed.
"""

import base64
import binascii
import hashlib
import hmac
import json
import time

__all__ = ["LONG_LIFETIME", "SHORT_LIFETIME", "generate_token", "verify_token"]


def _b64(raw: bytes) -> str:
    """base64url with the padding stripped, which is how a JWT spells
    every one of its three parts."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    """The other way, the padding put back — a JWT carries none."""
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _compact(claims: dict[str, object]) -> str:
    """One JWT segment: the JSON with no spaces in it, base64url."""
    return _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())


# How long a sign-in lasts: 30 days for a reader who asked to stay signed
# in, 4 hours for one who did not. The token carries it, so a browser that
# keeps or restores its cookies past that still cannot keep anyone in.
LONG_LIFETIME = 30 * 24 * 60 * 60
SHORT_LIFETIME = 4 * 60 * 60

# What we emit and what we require, the whole of the header's use.
_HEADER = _compact({"alg": "HS256", "typ": "JWT"})


def generate_token(password_hash: str, lifetime: int) -> str:
    """A token signed with `password_hash` — the whole stored hash, salt
    and all — good for `lifetime` seconds."""
    body = f"{_HEADER}.{_compact({'exp': int(time.time()) + lifetime})}"
    signature = hmac.new(password_hash.encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64(signature)}"


def verify_token(token: str, password_hash: str) -> bool:
    """Whether `token` was signed with `password_hash` and is still in date.

    In that order, and the signature before the claims: nothing untrusted
    reaches a parser until it has been proved to come from us."""
    parts = token.split(".")
    if len(parts) != 3:
        return False
    header, claims, signature = parts
    # As bytes: `compare_digest` refuses a str with anything non-ASCII in
    # it by RAISING, and whatever can reach the port can put that in a
    # cookie.
    if not hmac.compare_digest(header.encode(), _HEADER.encode()):
        return False
    if not _is_signed(f"{header}.{claims}", signature, password_hash):
        return False
    # A payload that does not parse, or says nothing about when it ends,
    # is not a token of ours however well it is signed.
    try:
        said = json.loads(_unb64(claims))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return False
    ends = said.get("exp") if isinstance(said, dict) else None
    return isinstance(ends, int) and not isinstance(ends, bool) and ends > time.time()


def _is_signed(body: str, signature: str, password_hash: str) -> bool:
    """Whether `signature` is `body`'s under `password_hash`, compared in
    constant time. One that is not even base64 is simply not a signature.
    The HMAC is the one `generate_token` signs with, and the round trip is
    what a change to either would break first."""
    try:
        given = _unb64(signature)
    except (binascii.Error, ValueError):
        return False
    expected = hmac.new(password_hash.encode(), body.encode(), hashlib.sha256).digest()
    return hmac.compare_digest(expected, given)
