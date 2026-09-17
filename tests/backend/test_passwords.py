"""Password hashing.

`backend.passwords` promises that a password hash matches the password
it was made from and no other; that two hashes of the same password share
nothing, the salt being random; that a hash is told apart from a typed
password by its shape alone; and that a hash that is damaged, or asks for
more than a check may spend, is refused rather than raised on — a sign-in
has to be answered either way.
"""

import pytest

from otaku.backend import passwords


class TestHash:
    def test_it_matches_the_password_it_was_made_from(self, password_hash: str) -> None:
        assert passwords.check("hunter2", password_hash)

    def test_it_refuses_every_other(self, password_hash: str) -> None:
        for other in ("hunter3", "Hunter2", "hunter2 ", "", "hunter"):
            assert not passwords.check(other, password_hash), other

    def test_it_never_holds_the_password(self, password_hash: str) -> None:
        assert "hunter2" not in password_hash

    def test_the_same_password_twice_stores_two_unrelated_values(self) -> None:
        # The salt is random: nothing computed against one install's
        # hash helps against another's.
        first, second = passwords.hash("hunter2"), passwords.hash("hunter2")
        assert first != second
        assert passwords.check("hunter2", first) and passwords.check("hunter2", second)

    def test_a_password_in_any_script_round_trips(self) -> None:
        for typed in ("пароль", "パスワード", "pa ss\tword", "🔑"):
            assert passwords.check(typed, passwords.hash(typed)), typed


class TestIsHashed:
    def test_a_password_hash_is_hashed(self, password_hash: str) -> None:
        assert passwords.is_hashed(password_hash)

    def test_anything_typed_is_not(self) -> None:
        for typed in (
            "",
            "hunter2",
            "$scrypt$",
            "scrypt$ln=15,r=8,p=1$c2FsdA$aGFzaA",
            "$2b$12$abc",
        ):
            assert not passwords.is_hashed(typed), typed


class TestCheck:
    def test_a_damaged_hash_is_refused_not_raised_on(self, password_hash: str) -> None:
        scheme, params, salt, digest = password_hash.split("$")[1:]
        for damaged in (
            f"${scheme}${params}${salt}",
            f"$bcrypt${params}${salt}${digest}",
            f"${scheme}$ln=15,r=8${salt}${digest}",
            f"${scheme}$ln=x,r=8,p=1${salt}${digest}",
            f"${scheme}${params}$!!!${digest}",
            f"${scheme}${params}${salt}$@@@",
            f"${scheme}${params}$${digest}",
        ):
            assert not passwords.check("hunter2", damaged), damaged

    def test_a_hash_asking_for_too_much_is_refused(self, password_hash: str) -> None:
        # A hand-edited config must not make every sign-in allocate
        # gigabytes: out-of-bounds parameters are not a password hash at all.
        _, salt, digest = password_hash.rsplit("$", 2)
        for greedy in ("ln=40,r=8,p=1", "ln=15,r=4096,p=1", "ln=15,r=8,p=64", "ln=0,r=8,p=1"):
            asking = f"$scrypt${greedy}${salt}${digest}"
            assert not passwords.is_hashed(asking), greedy
            assert not passwords.check("hunter2", asking), greedy


@pytest.fixture(scope="module")
def password_hash() -> str:
    # Made once: it is slow on purpose.
    return passwords.hash("hunter2")
