"""Who may ask, where the answer is pure.

`web.auth` promises three things running the app cannot show one by one:
a token is accepted under the password hash it was signed with and no
other, which is what a changed password signs everybody out by; a token
that has been touched anywhere — its claims, its header, its signature —
is refused; and a token past its expiry is refused however well it is
signed. The clock is the one thing held still, so expiry is a case and not
a wait.
"""

import base64
import hashlib
import hmac
import json

import pytest

from otaku.web import auth

# Shaped like what the launch stores; signing never looks inside one.
PASSWORD_HASH = "$scrypt$ln=15,r=8,p=1$c2FsdHNhbHRzYWx0$aGFzaGhhc2hoYXNoaGFzaA"
OTHER_PASSWORD_HASH = "$scrypt$ln=15,r=8,p=1$b3RoZXJzYWx0$b3RoZXJoYXNob3RoZXJoYQ"


class TestVerify:
    """`web.auth.generate_token` and `verify_token` — a token, and whether it is ours."""

    def test_a_token_is_accepted_under_the_hash_that_signed_it(self) -> None:
        assert auth.verify_token(
            auth.generate_token(PASSWORD_HASH, auth.SHORT_LIFETIME), PASSWORD_HASH
        )

    def test_a_token_is_refused_under_another_hash(self) -> None:
        # A changed password is a new hash: every token signed with the
        # old one stops working, with nothing kept to revoke.
        assert not auth.verify_token(
            auth.generate_token(PASSWORD_HASH, auth.SHORT_LIFETIME), OTHER_PASSWORD_HASH
        )

    def test_a_token_whose_claims_were_changed_is_refused(self) -> None:
        header, _, signature = auth.generate_token(PASSWORD_HASH, auth.SHORT_LIFETIME).split(".")
        forever = _segment({"exp": 2**40})
        assert not auth.verify_token(f"{header}.{forever}.{signature}", PASSWORD_HASH)

    def test_a_token_naming_another_algorithm_is_refused(self) -> None:
        # The `alg: none` family: a token that tells the code checking it how
        # to check it, signed with nothing at all.
        _, claims, _ = auth.generate_token(PASSWORD_HASH, auth.SHORT_LIFETIME).split(".")
        assert not auth.verify_token(f"{_segment({'alg': 'none'})}.{claims}.", PASSWORD_HASH)

    def test_a_token_with_its_signature_changed_is_refused(self) -> None:
        header, claims, signature = auth.generate_token(PASSWORD_HASH, auth.SHORT_LIFETIME).split(
            "."
        )
        flipped = ("A" if signature[0] != "A" else "B") + signature[1:]
        assert not auth.verify_token(f"{header}.{claims}.{flipped}", PASSWORD_HASH)

    def test_anything_that_is_not_a_token_is_refused(self) -> None:
        for text in ("", "token", "a.b", "a.b.c", "a.b.c.d", "...", "é.é.é"):
            assert not auth.verify_token(text, PASSWORD_HASH), text

    def test_a_token_is_good_until_its_lifetime_is_over(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(auth.time, "time", lambda: 1_000_000.0)
        token = auth.generate_token(PASSWORD_HASH, auth.SHORT_LIFETIME)
        monkeypatch.setattr(auth.time, "time", lambda: 1_000_000.0 + auth.SHORT_LIFETIME - 1)
        assert auth.verify_token(token, PASSWORD_HASH)
        monkeypatch.setattr(auth.time, "time", lambda: 1_000_000.0 + auth.SHORT_LIFETIME)
        assert not auth.verify_token(token, PASSWORD_HASH)

    def test_signed_claims_without_an_expiry_are_refused(self) -> None:
        # Well-formed and correctly signed, but saying nothing about when
        # it ends: not a token this module ever issues.
        header = auth.generate_token(PASSWORD_HASH, auth.SHORT_LIFETIME).split(".")[0]
        body = f"{header}.{_segment({'sub': 'reader'})}"
        signed = hmac.new(PASSWORD_HASH.encode(), body.encode(), hashlib.sha256).digest()
        signature = base64.urlsafe_b64encode(signed).rstrip(b"=").decode()
        assert not auth.verify_token(f"{body}.{signature}", PASSWORD_HASH)


def _segment(claims: dict[str, object]) -> str:
    return base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
