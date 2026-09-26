"""`@path` in a played line — the terminal's rule for attaching a picture.

`extract_pictures` takes out every `@` token whose path the reader
answers for, one adjacent space with it, and hands the pictures back in
order; a token the reader declines, or an `@` glued to a word, stays
exactly as typed.
`token_at` is the token being typed at the cursor, for the menu. Spaces in
a path travel escaped; the reader is asked with them unescaped.
"""

from otaku.backend.files import RawFile
from otaku.terminal.prompt.pictures import escape, extract_pictures, token_at, unescape

PICTURES = {"cat.jpg", "pics/dog.png", "my pics/cat.jpg", "~/cat.heic"}


def _known(path: str) -> RawFile | None:
    """A reader over a folder of four, answering with the path as the name."""
    return RawFile(b"bytes", path) if path in PICTURES else None


def split(line: str) -> tuple[str, list[str]]:
    """`extract_pictures` over that folder, the pictures as the paths they
    were named by."""
    rest, files = extract_pictures(line, _known)
    return rest, [file.name for file in files]


class TestExtractPictures:
    def test_a_picture_token_leaves_the_line_with_its_space(self) -> None:
        assert split("look @cat.jpg now") == ("look now", ["cat.jpg"])

    def test_a_token_at_the_end_takes_the_space_before_it(self) -> None:
        assert split("look @cat.jpg") == ("look", ["cat.jpg"])

    def test_a_token_at_the_start_takes_the_space_after_it(self) -> None:
        assert split("@cat.jpg look") == ("look", ["cat.jpg"])

    def test_a_line_that_is_only_a_picture_empties(self) -> None:
        assert split("@cat.jpg") == ("", ["cat.jpg"])

    def test_several_come_back_in_order(self) -> None:
        line = "@cat.jpg and @pics/dog.png then"
        assert split(line) == ("and then", ["cat.jpg", "pics/dog.png"])

    def test_a_token_naming_nothing_stays_as_typed(self) -> None:
        assert split("ask @Mara about it") == ("ask @Mara about it", [])

    def test_an_at_inside_a_word_is_prose(self) -> None:
        assert split("mail me@cat.jpg") == ("mail me@cat.jpg", [])

    def test_a_bare_at_is_prose(self) -> None:
        assert split("look @ this") == ("look @ this", [])

    def test_an_escaped_space_is_part_of_the_path(self) -> None:
        assert split("look @my\\ pics/cat.jpg") == ("look", ["my pics/cat.jpg"])

    def test_the_home_shorthand_reaches_the_reader_as_typed(self) -> None:
        assert split("@~/cat.heic") == ("", ["~/cat.heic"])

    def test_exactly_one_space_goes_with_the_token_the_rest_stays(self) -> None:
        # A played line's spacing is the reader's own and reaches the
        # wire verbatim; the resolver takes the token and one space, no
        # more, so what it leaves is what was typed minus the token.
        assert split("I wait.   @cat.jpg   With   spaces?") == (
            "I wait.     With   spaces?",
            ["cat.jpg"],
        )

    def test_the_bytes_come_with_the_name(self) -> None:
        _rest, (picture,) = extract_pictures("@cat.jpg", _known)
        assert picture == RawFile(b"bytes", "cat.jpg")


class TestTokenAt:
    def test_right_after_the_at_the_token_is_empty(self) -> None:
        assert token_at("look @") == ""

    def test_a_partly_typed_path_is_the_token(self) -> None:
        assert token_at("look @pics/d") == "pics/d"

    def test_past_a_space_there_is_no_token(self) -> None:
        assert token_at("look @cat.jpg ") is None

    def test_an_escaped_space_keeps_the_token_open(self) -> None:
        assert token_at("look @my\\ pi") == "my\\ pi"

    def test_an_at_inside_a_word_is_no_token(self) -> None:
        assert token_at("mail me@ca") is None

    def test_prose_has_no_token(self) -> None:
        assert token_at("look at this") is None


class TestEscaping:
    def test_spaces_travel_escaped_and_come_back(self) -> None:
        assert escape("my pics/cat.jpg") == "my\\ pics/cat.jpg"
        assert unescape(escape("my pics/cat.jpg")) == "my pics/cat.jpg"
