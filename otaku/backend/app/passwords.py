"""Password hashing: the web password stored as a salted hash, which is
enough to check a typed password against and never enough to recover it.

The launch replaces a password typed into `[web] password` with its hash
(`launch._load_config`), so what a frontend is handed in
`WebSettings.password` is always a hash or nothing, and the page's
sign-in checks what was typed against it here. This is hashing and not
encryption: nothing is sealed, and there is no key.

The stored form is a PHC string — `$scrypt$ln=15,r=8,p=1$<salt>$<hash>`,
the shape password libraries write — so the parameters travel with the
value and a later change to them never strands one already stored. The
salt is random per password, which is the point over a key derived under
a fixed one: the same password on two machines stores two unrelated
values, and nothing computed against one helps against another.

The parameters are the ones `encryption.data` derives a passphrase KEK
under, rather than a second opinion about the same question — memory-hard,
and some tens of milliseconds. That cost is paid on every check, which makes it
the whole of the rate limiting anyone guessing at a password meets.
"""

import base64
import binascii
import hashlib
import hmac
import secrets

__all__ = ["check", "hash", "is_hashed"]

_SCHEME = "scrypt"
_LOG_N, _R, _P = 15, 8, 1
_SALT_LEN = 16
_HASH_LEN = 32

# The most a stored value may ask for. Past these a hand-edited config
# could make every sign-in allocate gigabytes, so the check refuses to
# run it rather than obeying it.
_MAX_LOG_N, _MAX_R, _MAX_P = 20, 32, 16


def hash(password: str) -> str:
    """`password` as it may be stored: a fresh salt, and the scrypt of
    the two, in the PHC string form."""
    salt = secrets.token_bytes(_SALT_LEN)
    digest = _derive(password, salt, _LOG_N, _R, _P)
    return f"${_SCHEME}$ln={_LOG_N},r={_R},p={_P}${_b64(salt)}${_b64(digest)}"


def is_hashed(value: str) -> bool:
    """Whether `value` is a password hash this module made — as opposed
    to a password somebody typed into the file, which is anything else."""
    return _parsed(value) is not None


def check(password: str, password_hash: str) -> bool:
    """Whether `password` is the one `password_hash` was made from,
    compared in constant time. False for a hash that does not parse or
    asks for more than a check may spend — never an exception, since a
    sign-in must be answered either way."""
    parsed = _parsed(password_hash)
    if parsed is None:
        return False
    log_n, r, p, salt, expected = parsed
    try:
        digest = _derive(password, salt, log_n, r, p, length=len(expected))
    except (ValueError, MemoryError):
        return False
    return hmac.compare_digest(digest, expected)


def _derive(
    password: str, salt: bytes, log_n: int, r: int, p: int, *, length: int = _HASH_LEN
) -> bytes:
    """The scrypt of `password` under `salt`: `length` bytes, at a cost of
    2**`log_n` (N, what the time and memory grow with), block size `r` and
    parallelism `p`.

    `maxmem` is set because scrypt needs about 128·N·r bytes, and at this
    module's own parameters (`_LOG_N`, `_R`) that is 32 MiB — exactly the
    cap `hashlib` applies when none is given, which it refuses. Twice the
    need leaves room for the overhead. Raises ValueError for parameters
    scrypt will not run."""
    n = 1 << log_n
    return hashlib.scrypt(
        password.encode(), salt=salt, n=n, r=r, p=p, dklen=length, maxmem=128 * n * r * 2
    )


def _parsed(value: str) -> tuple[int, int, int, bytes, bytes] | None:
    """The five parts of a password hash, or None for anything that is
    not one: `$scrypt$ln=…,r=…,p=…$salt$hash`, every number in bounds."""
    parts = value.split("$")
    if len(parts) != 5 or parts[0] or parts[1] != _SCHEME:
        return None
    try:
        fields = dict(pair.split("=", 1) for pair in parts[2].split(","))
        log_n, r, p = int(fields["ln"]), int(fields["r"]), int(fields["p"])
        salt, digest = _unb64(parts[3]), _unb64(parts[4])
    except (KeyError, ValueError, binascii.Error):
        return None
    in_bounds = 1 <= log_n <= _MAX_LOG_N and 1 <= r <= _MAX_R and 1 <= p <= _MAX_P
    if not in_bounds or not salt or len(digest) < 16:
        return None
    return log_n, r, p, salt, digest


def _b64(raw: bytes) -> str:
    """Base64 without padding, as a PHC string spells its salt and hash."""
    return base64.b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.b64decode(text + "=" * (-len(text) % 4), validate=True)
