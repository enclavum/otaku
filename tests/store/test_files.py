"""The files folder's names, as far as they are pure: the digest a
picture's bytes are named by (`digest`), and the one a stored file's
name carries (`digest_of`) — the same bytes, the same digest, whoever
owns the file, a thumbnail's name carrying its picture's, and an
original's — a file kept as it came — its own."""

from otaku.store.files import digest, digest_of


class TestDigest:
    def test_the_same_bytes_name_the_same_digest(self) -> None:
        assert digest(b"a picture") == digest(b"a picture")
        assert digest(b"a picture") != digest(b"another")

    def test_a_digest_is_eight_hex_characters(self) -> None:
        found = digest(b"a picture")
        assert len(found) == 8 and int(found, 16) >= 0


class TestDigestOf:
    def test_a_name_gives_back_the_digest_it_was_made_with(self) -> None:
        made = digest(b"a picture")
        assert digest_of(f"pic-0024-20260918-{made}.jpg") == made

    def test_every_owner_the_thumbnail_and_an_original_carry_the_same(self) -> None:
        made = digest(b"a picture")
        names = (
            f"pic-0024-20260918-{made}.jpg",
            f"crd-0012-20261011-{made}.png",
            f"pic-0024-20260918-{made}-thumb.jpg",
            f"crd-0012-20261011-{made}-orig.charx",
        )
        assert {digest_of(name) for name in names} == {made}
