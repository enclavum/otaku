"""The certificate the local server serves under, and the key beside it.

Generated once into the state dir's `cert/` and then left alone: what is
there is what is served, so a reader who drops in a pair of their own —
mkcert, `tailscale cert`, anything a browser already trusts — simply
replaces these two files and otaku never writes over them.

Never writes over them is the whole posture, so an EMPTY directory is
the only state that generates. One file without the other, or a pair
that cannot be served with, is somebody's decision half-carried out, and
the reader is told rather than having a private key replaced under them.

A generated certificate is its own issuer, so no browser trusts it and
every reader meets the warning once per browser. That is the trade taken
on purpose. The warning is about IDENTITY; the encryption behind it is
the same as any other TLS connection, which is what keeps a story off
the wire on a network somebody else is also on. Naming every address the
machine might be reached at only removes the warning where it CAN be
removed — it is never required, because a name mismatch and an unknown
issuer are the same single click.

Which is also why the pair is dated ten years out: a certificate that
stops covering the reader's new DHCP lease costs them nothing they were
not already paying, so an address change is no reason to write here
again.
"""

import datetime as dt
import ipaddress
import os
import re
import socket
import ssl
from collections.abc import Callable
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

__all__ = ["CertError", "get_context"]

_CERT_NAME = "cert.pem"
_KEY_NAME = "key.pem"

# Ten years. The 398-day ceiling browsers enforce is a rule about
# certificates chaining to a root they shipped with, and exempts one a
# person trusted themselves — which is the only way this one is ever
# trusted at all.
_VALIDITY = dt.timedelta(days=3652)

# Backdated, so a machine whose clock sits behind this one is not handed
# a certificate that has not started yet. A month rather than an hour
# because the clocks that are wrong are wrong by days: a phone set to the
# wrong date, a board with no battery behind its clock.
_BACKDATE = dt.timedelta(days=30)

# A hostname worth putting in a certificate: letters, digits, hyphens and
# dots. Anything else the machine calls itself is dropped rather than
# encoded, since a name no browser could send is no use in there.
_HOSTNAME = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9.-]*\Z")

_SUBJECT = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "otaku")])


class CertError(Exception):
    """There is no pair to serve with and none may be written: half of
    one is present, or what is present cannot be loaded. Carries the
    sentence the reader gets; the cause under it is for the error log."""


def get_context(directory: Path, show: Callable[[str], None] | None = None) -> ssl.SSLContext:
    """What a listening socket is wrapped in, from the pair in
    `directory` — generated first when there is nothing there at all.
    Raises CertError for every other absence.

    An empty directory is a first launch under https and is the only
    state this writes into. One file alone is either a reader mid-way
    through installing their own or a crash of ours between two renames,
    and both are answered the same way: say so, name the directory, and
    let a person decide. A pair that will not load — mismatched halves,
    an encrypted key, a truncated file — is likewise theirs to fix.

    `show` is handed the one line a launch here is worth, and only when
    a certificate was MADE: the browser warning that follows wants
    explaining before it arrives rather than after. Where that line
    appears is the caller's, the way it is for everything `server`
    hands to its hooks.
    """
    cert, key = directory / _CERT_NAME, directory / _KEY_NAME
    # These two names and nothing else: whatever else the reader keeps
    # in here — an old pair, a CA they installed, a note to themselves —
    # is theirs and is never looked at.
    has_cert, has_key = cert.exists(), key.exists()
    if has_cert and has_key:
        return _load(cert, key)
    if has_cert or has_key:
        raise CertError(
            f"{directory} holds only one of {_CERT_NAME} and {_KEY_NAME} — "
            f"add the other, or delete it and otaku will generate a self-signed pair"
        )
    _generate(cert, key)
    if show is not None:
        show(f"a new self-signed certificate has been generated in {directory}")
    return _load(cert, key)


def _load(cert: Path, key: Path) -> ssl.SSLContext:
    """The pair as a server context, which is also the whole of what
    "usable" means: it fails here, at the launch where a person is
    reading, rather than at the bind.

    The password is passed rather than left out because OpenSSL would
    otherwise ask for one on a terminal nobody is watching, and a server
    stuck on an invisible prompt is a hang nobody diagnoses.
    `ssl.SSLError` is an `OSError`, and so is a file that cannot be read
    at all."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    try:
        context.load_cert_chain(cert, key, password=b"")
    except OSError as e:
        raise CertError(f"the certificate pair in {cert.parent} cannot be loaded — {e}") from e
    return context


def _generate(cert: Path, key: Path) -> None:
    """A fresh self-signed pair over a new P-256 key. Both halves are
    written beside their names and only then renamed into place, so the
    window in which this directory holds one file — the state
    `get_context` can do nothing with — is the gap between two
    renames."""
    private = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.UTC)
    built = (
        x509.CertificateBuilder()
        .subject_name(_SUBJECT)
        .issuer_name(_SUBJECT)
        .public_key(private.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _BACKDATE)
        .not_valid_after(now + _VALIDITY)
        .add_extension(x509.SubjectAlternativeName(_names()), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=False,
                key_agreement=True,
                content_commitment=False,
                data_encipherment=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .sign(private, hashes.SHA256())
    )
    staged_key = _write_staged(
        key,
        private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ),
        0o600,
    )
    # The certificate is the public half: readable, unlike the key beside
    # it, because a reader may well want to hand it to another machine.
    staged_cert = _write_staged(cert, built.public_bytes(serialization.Encoding.PEM), 0o644)
    _rename(staged_key, key)
    _rename(staged_cert, cert)


def _names() -> list[x509.GeneralName]:
    """Every address this machine might be asked for, as far as it can
    be known here: loopback under all three spellings, what the machine
    calls itself, that name under mDNS, and the address it routes out
    of. A miss costs the reader one more click, never access."""
    names: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
        x509.IPAddress(ipaddress.IPv6Address("::1")),
    ]
    names.extend(x509.DNSName(name) for name in _host_names())
    address = _lan_address()
    if address is not None:
        names.append(x509.IPAddress(address))
    return names


def _host_names() -> list[str]:
    """What this machine calls itself, and its mDNS name — the one that
    survives a new DHCP lease, which the address below does not."""
    try:
        host = socket.gethostname().rstrip(".")
    except OSError:
        return []
    if host in ("", "localhost") or not _HOSTNAME.fullmatch(host):
        return []
    return [host] if "." in host else [host, f"{host}.local"]


def _lan_address() -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """The address this machine routes out of, or None when it routes
    nowhere. Asked of the routing table rather than of DNS, which on a
    laptop answers with whatever its name resolved to somewhere else:
    a UDP socket is CONNECTED to a documentation address that is never
    routed anywhere, which sends no packet and picks the interface."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            return ipaddress.ip_address(probe.getsockname()[0])
    except (OSError, ValueError):
        return None


def _write_staged(path: Path, data: bytes, mode: int) -> Path:
    """One half written beside its name, at its own permissions from the
    moment it exists — never a window at the umask's.

    O_BINARY because this is `os.open` rather than `open`: only the io
    module adds it for you, and a text-mode descriptor would write the
    PEM's newlines as CRLF on Windows. The mode bits are POSIX's alone —
    Windows keeps the key to one user through the profile directory's
    inherited ACL, which is the protection `~/.otaku` already has."""
    staged = path.with_name(f".{path.name}.new")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        fd = os.open(
            staged, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), mode
        )
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    except OSError as e:
        raise CertError(f"cannot write {path} — {e.strerror or e}") from e
    return staged


def _rename(staged: Path, path: Path) -> None:
    try:
        os.replace(staged, path)
    except OSError as e:
        raise CertError(f"cannot write {path} — {e.strerror or e}") from e
