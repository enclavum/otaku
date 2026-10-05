"""The streaming typesetter, and the request highlighter beside it.

The typesetter's contract: text arrives in arbitrary chunks and is written
out immediately with ANSI styling, never repainted. So the tests check two
things — that the visible text survives (markers consumed, content kept),
and that how the input is split into chunks changes nothing.

The highlighter's contract is the vocabulary: it colours the words it is
GIVEN and nothing else, so which `/word` is a command is the caller's
answer, never a guess from the slash — the same boundaries cut a text
into its commands and the rest (`command_pieces`), whole. An aside is
written beside the typesetting: its text in its own style, the
typesetter's state untouched — a quote open around it stays open, a
line's held opening resolves as the text it was. Both are checked by structure — the
escapes that open and close a span — never by which shade the terminal
running the tests happens to pick.
"""

import io
import re
from collections.abc import Iterator

import pytest

from otaku.settings.config import TerminalSettings
from otaku.terminal.tty.theme import _CURRENT, color, theme, use
from otaku.terminal.tty.typography import (
    Streamer,
    command_pieces,
    highlight_commands,
    highlight_toml,
)


def escape(spec: str) -> str:
    """A color spec as the foreground escape it resolves to. Defined here,
    above the constants, because they are built from it."""
    return color(spec).fg


_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_BOLD = "\x1b[1m"
_ITALIC = "\x1b[3m"
_RESET = "\x1b[0m"
_SPEECH_SPEC = "cyan"  # pinned by the fixture, so no test reads the real terminal
_SPEECH = escape(_SPEECH_SPEC)
_DEFAULT_FG = "\x1b[39m"  # what closes a highlighted command
# A vocabulary with the overlaps that matter: a command that prefixes
# another, and one that prefixes an ordinary word.
_COMMANDS = ("/me", "/merge", "/ooc", "/cue", "/set", "/bye")


@pytest.fixture(autouse=True)
def settled() -> Iterator[None]:
    """A known theme for every case. The typesetter takes its dialogue look
    from the theme now, so without this the tests would read whatever
    background the terminal running them reports."""
    saved = list(_CURRENT)
    use(
        TerminalSettings(
            theme="auto", dialogue_color=_SPEECH_SPEC, dialogue_bold=False, show_banner=True
        )
    )
    yield
    _CURRENT[:] = saved


class TestPlainText:
    def test_passes_prose_through_unchanged(self) -> None:
        assert plain("The door opens.\n") == "The door opens.\n"

    def test_keeps_blank_lines(self) -> None:
        assert plain("one\n\ntwo\n") == "one\n\ntwo\n"

    def test_keeps_text_without_a_trailing_newline(self) -> None:
        assert plain("no newline") == "no newline"


class TestInlineMarkup:
    def test_bolds_double_asterisks(self) -> None:
        assert _BOLD in typeset("**loud**\n")

    def test_consumes_the_bold_markers(self) -> None:
        assert plain("**loud**\n") == "loud\n"

    def test_italicises_single_asterisks(self) -> None:
        assert _ITALIC in typeset("*soft*\n")

    def test_consumes_the_italic_markers(self) -> None:
        assert plain("*soft*\n") == "soft\n"

    def test_consumes_inline_code_backticks(self) -> None:
        assert plain("say `run` now\n") == "say run now\n"

    def test_keeps_an_escaped_asterisk(self) -> None:
        assert plain("2 \\* 3\n") == "2 * 3\n"

    def test_closes_styles_at_the_end_of_a_line(self) -> None:
        assert typeset("**unclosed\n").endswith(f"{_RESET}\n")


class TestBlocks:
    def test_bolds_a_header(self) -> None:
        assert _BOLD in typeset("# Title\n")

    def test_consumes_the_header_marker(self) -> None:
        assert plain("# Title\n") == "Title\n"

    def test_renders_a_bullet_for_a_list_item(self) -> None:
        assert plain("* item\n") == "• item\n"
        assert plain("+ item\n") == "• item\n"

    def test_a_hyphen_is_not_a_list_marker(self) -> None:
        # `- ` is the dash-dialogue convention, so the hyphen must survive
        # as typed — a bullet would rewrite the spoken line's own mark.
        assert plain("- word\n") == "- word\n"

    def test_keeps_ordered_list_numbering(self) -> None:
        assert plain("1. first\n") == "1. first\n"

    def test_renders_a_quote_bar(self) -> None:
        assert plain("> quoted\n") == "│ quoted\n"

    def test_renders_a_horizontal_rule(self) -> None:
        assert set(plain("---\n").strip()) == {"─"}

    def test_keeps_a_lone_asterisk_line_as_text(self) -> None:
        assert plain("*\n") == "*\n"


class TestFencedCode:
    def test_keeps_the_code_body(self) -> None:
        assert "x = 1" in plain("```\nx = 1\n```\n")

    def test_consumes_the_fence_lines(self) -> None:
        assert "```" not in plain("```\nx = 1\n```\n")

    def test_labels_the_language(self) -> None:
        assert plain("```python\nx = 1\n```\n").startswith("python\n")

    def test_leaves_markup_inside_code_alone(self) -> None:
        assert "**not bold**" in plain("```\n**not bold**\n```\n")

    def test_closes_an_unterminated_fence_on_flush(self) -> None:
        assert "x = 1" in plain("```\nx = 1")


class TestDialogue:
    """Speech is colored in both conventions writers use: paired quotes, and
    a dash-opened line whose attribution is handed over on a dash that
    follows sentence punctuation."""

    def test_a_quoted_span_is_spoken(self) -> None:
        assert spoken('He said "come in" softly.\n') == ['"come in"']

    def test_every_quote_pairing_closes(self) -> None:
        assert spoken("«Da», sagte er.\n") == ["«Da»"]
        assert spoken("\u201cYes,\u201d she said.\n") == ["\u201cYes,\u201d"]
        assert spoken("\u201eJa\u201c, sagte er.\n") == ["\u201eJa\u201c"]
        # The strict low pairing is often typed with the other curly mark.
        assert spoken("\u201eJa\u201d, sagte er.\n") == ["\u201eJa\u201d"]

    def test_an_apostrophe_never_opens_a_span(self) -> None:
        assert spoken("Don't stop, it's fine.\n") == []

    def test_a_dash_line_speaks_until_the_attribution(self) -> None:
        assert spoken("— Hello, — he said.\n") == ["— Hello, "]

    def test_the_attribution_hands_speech_back(self) -> None:
        assert spoken("— Hello, — he said. — Come in.\n") == ["— Hello, ", "— Come in."]

    def test_a_parenthetical_dash_keeps_the_voice(self) -> None:
        # Preceded by a word, not by punctuation: not a handover.
        assert spoken("— I thought — and it matters — that it would.\n") == [
            "— I thought — and it matters — that it would."
        ]

    def test_a_citation_inside_speech_stays_speech(self) -> None:
        # One speaker quoting another: the quote interrupts the spoken line
        # without ending it, so the words after it are still spoken and the
        # attribution after the handover dash is still not.
        assert spoken("— \u00abNot so\u00bb, — he repeated.\n") == [
            "— ",
            "\u00abNot so\u00bb",
            ", ",
        ]

    def test_a_citation_in_the_attribution_stays_narration(self) -> None:
        spans = spoken("— Yes, — he said, quoting \u00abthe sign\u00bb.\n")
        assert spans == ["— Yes, ", "\u00abthe sign\u00bb"]

    def test_a_list_marker_is_not_a_dialogue_dash(self) -> None:
        assert spoken("* an item\n") == []

    def test_a_hyphen_line_speaks_like_a_dash_line(self) -> None:
        assert spoken("- Hello, - he said. - Come in.\n") == ["- Hello, ", "- Come in."]

    def test_a_compound_word_is_not_a_handover(self) -> None:
        # The hyphen inside "Semi-formal" follows a letter, so the voice
        # holds; the dash after the comma is the attribution as usual.
        assert spoken("- Semi-formal, - he said.\n") == ["- Semi-formal, "]

    def test_a_dash_outside_a_dash_line_is_narration(self) -> None:
        assert spoken("The hall — long and unlit — smelled of stone.\n") == []
        assert spoken("A well-known road, 3-4 miles.\n") == []

    def test_code_is_never_spoken(self) -> None:
        assert spoken('Try `printf("hi")` now.\n') == []
        assert spoken('```py\nprint("— no —")\n```\n') == []

    def test_speech_never_crosses_a_line(self) -> None:
        assert spoken('An unclosed "quote here\nand the next line.\n') == ['"quote here']

    def test_chunking_does_not_change_the_result(self) -> None:
        text = '— Hello, — he said. — Come in.\nShe said "no" and left.\n'
        assert typeset(text) == typeset(text, chunk=1) == typeset(text, chunk=3)

    def test_flush_closes_speech_left_open_at_stream_end(self) -> None:
        assert spoken("— unfinished") == ["— unfinished"]

    def test_speech_takes_the_themes_dialogue_color(self) -> None:
        assert theme().dialogue.fg + '"hi"' in typeset('"hi"\n')

    def test_speech_is_not_bold_unless_the_theme_says_so(self) -> None:
        assert _SPEECH + _BOLD not in typeset('"hi"\n')
        use(
            TerminalSettings(
                theme="auto", dialogue_color=_SPEECH_SPEC, dialogue_bold=True, show_banner=True
            )
        )
        assert _SPEECH + _BOLD in typeset('"hi"\n')


class TestChunking:
    DOCUMENT = (
        "# Title\n\nPlain **bold** and *italic* text.\n\n"
        "- one\n- two\n\n> a quote\n\n```py\nx = 1\n```\n\nlast line\n"
    )

    def test_one_chunk_and_char_by_char_agree(self) -> None:
        assert typeset(self.DOCUMENT) == typeset(self.DOCUMENT, chunk=1)

    def test_every_chunk_size_agrees(self) -> None:
        expected = typeset(self.DOCUMENT)
        for size in (2, 3, 5, 7, 13):
            assert typeset(self.DOCUMENT, chunk=size) == expected

    def test_a_marker_split_across_chunks_still_bolds(self) -> None:
        out = io.StringIO()
        streamer = Streamer(out)
        streamer.feed("**bo")
        streamer.feed("ld**\n")
        streamer.flush()
        assert _BOLD in out.getvalue()
        assert _ANSI.sub("", out.getvalue()) == "bold\n"


class TestFlush:
    def test_leaves_no_style_open(self) -> None:
        for text in ("**bold", "*italic", "`code", "# header", "text"):
            assert not _open_style(typeset(text))


class TestRenderMarkdown:
    """`render` returns as a string exactly what the streamer
    would have printed for the same text."""

    def test_matches_the_streamers_output(self) -> None:
        text = "# Scene\nShe *waits*.\n**Bold** move, `run()`.\n> said so\n"
        assert typeset(text) == typeset(text)

    def test_returns_plain_prose_unchanged(self) -> None:
        assert typeset("The door opens.") == "The door opens."

    def test_returns_empty_for_empty_input(self) -> None:
        assert typeset("") == ""

    def test_closes_styles_at_the_end(self) -> None:
        assert typeset("**unclosed").endswith(_RESET)


class TestHighlightCommands:
    """The request side: the words the caller named, and only those."""

    def test_picks_out_a_command_opening_the_line(self) -> None:
        assert highlighted("/me Elara: I step in.", _COMMANDS) == ["/me"]

    def test_picks_out_an_inliner_mid_line(self) -> None:
        assert highlighted("I step in. /ooc who else is here?", _COMMANDS) == ["/ooc"]

    def test_picks_out_one_command_per_line(self) -> None:
        assert highlighted("/me I wait.\n/ooc still there?", _COMMANDS) == ["/me", "/ooc"]

    def test_leaves_a_word_the_vocabulary_does_not_name(self) -> None:
        assert highlighted("/nonesuch and /Me and /ME", _COMMANDS) == []

    def test_leaves_a_slash_inside_a_word(self) -> None:
        for text in ("and/or", "https://example.com/me", "TCP/IP"):
            assert highlighted(text, _COMMANDS) == [], text

    def test_leaves_a_command_that_only_opens_a_longer_word(self) -> None:
        assert highlighted("/mention /setting /byes", _COMMANDS) == []

    def test_prefers_the_longer_command(self) -> None:
        assert highlighted("/merge A into B", _COMMANDS) == ["/merge"]

    def test_colours_the_slash_word_alone(self) -> None:
        # The name and the prose after it are the user's words, not syntax.
        assert highlighted("/me Elara: I step in.", _COMMANDS) == ["/me"]

    def test_keeps_the_visible_text_intact(self) -> None:
        text = "/me Elara: I step in. /cue keep it short"
        assert visible(text, _COMMANDS) == text

    def test_an_empty_vocabulary_highlights_nothing(self) -> None:
        text = "/me Elara: I step in."
        assert highlight_commands(text, ()) == text

    def test_empty_text_stays_empty(self) -> None:
        assert highlight_commands("", _COMMANDS) == ""


class TestCommandPieces:
    def test_the_pieces_are_the_text_exactly(self) -> None:
        for text in ("/me Elara: I step in. /ooc hi", "and/or", "", "/merge A into B /bye"):
            assert "".join(piece for piece, _ in command_pieces(text, _COMMANDS)) == text

    def test_the_commands_are_the_highlighters(self) -> None:
        text = "/me I wait. and/or /mention /ooc who?"
        picked = [piece for piece, command in command_pieces(text, _COMMANDS) if command]
        assert picked == ["/me", "/ooc"] == highlighted(text, _COMMANDS)

    def test_no_vocabulary_leaves_one_piece(self) -> None:
        assert command_pieces("/me hi", ()) == [("/me hi", False)]
        assert command_pieces("", _COMMANDS) == []


class TestAside:
    def test_the_visible_text_keeps_its_order(self) -> None:
        assert _ANSI.sub("", aside("She said ", "/cue", " go.")) == "She said /cue go."

    def test_a_quote_open_around_it_stays_open(self) -> None:
        rendered = aside('"Wait ', "/cue", ' here," she said.')
        spans = re.findall(f"{re.escape(_SPEECH)}(.*?){re.escape(_RESET)}", rendered, re.S)
        assert [_ANSI.sub("", span) for span in spans] == ['"Wait ', ' here,"']

    def test_a_held_line_opening_resolves_as_the_text_it_was(self) -> None:
        # a lone dash could still open a list or a rule: written as itself
        assert _ANSI.sub("", aside("-", "/x", "- y")) == "-/x- y"

    def test_its_style_closes_behind_it(self) -> None:
        rendered = aside("a ", "aside", " b", style=_ITALIC)
        styled, behind = rendered.split("aside")
        assert styled.endswith(_ITALIC) and behind.startswith(_RESET)
        assert not _open_style(rendered)


class TestHighlightToml:
    """The card archive's view: keys and macros light up, key-agnostic;
    value text stays prose, block bodies included."""

    def test_colours_a_key_and_not_its_value(self) -> None:
        assert toml_highlighted("name = 'Elara'") == ["name"]

    def test_colours_a_macro_inside_a_value(self) -> None:
        assert toml_highlighted("greeting = 'hi {{user}}'") == ["greeting", "{{user}}"]

    def test_a_key_shape_inside_a_block_is_prose(self) -> None:
        text = "description = '''\nmood = calm\n'''"
        assert toml_highlighted(text) == ["description"]

    def test_a_macro_inside_a_block_still_colours(self) -> None:
        text = "description = '''\nsmiles at {{char}}\n'''"
        assert toml_highlighted(text) == ["description", "{{char}}"]

    def test_keeps_the_visible_text_intact(self) -> None:
        text = "name = 'Elara'\n\ndescription = '''\nhi {{user}}\n'''\n"
        assert _ANSI.sub("", highlight_toml(text)) == text


def typeset(text: str, *, chunk: int = 0) -> str:
    """`text` through the typesetter, in one chunk or in `chunk`-sized
    bites. The dialogue look comes from the theme the fixture settled."""
    out = io.StringIO()
    streamer = Streamer(out)
    if chunk:
        for i in range(0, len(text), chunk):
            streamer.feed(text[i : i + chunk])
    else:
        streamer.feed(text)
    streamer.flush()
    return out.getvalue()


def aside(before: str, text: str, after: str, *, style: str = "\x1b[2m") -> str:
    """`before` typeset, `text` written beside it in `style`, `after`
    typeset on — one typesetter throughout."""
    out = io.StringIO()
    streamer = Streamer(out)
    streamer.feed(before)
    streamer.aside(text, style)
    streamer.feed(after)
    streamer.flush()
    return out.getvalue()


def highlighted(text: str, commands: tuple[str, ...]) -> list[str]:
    """The words the highlighter picked out, in order — read off the
    default-foreground escape that closes each one, so the test never
    names a shade the background chose."""
    rendered = highlight_commands(text, commands)
    return re.findall(rf"\x1b\[[0-9;]*m(.*?){re.escape(_DEFAULT_FG)}", rendered, re.S)


def visible(text: str, commands: tuple[str, ...]) -> str:
    """The highlighted text with the styling stripped — what a reader sees."""
    return _ANSI.sub("", highlight_commands(text, commands))


def toml_highlighted(text: str) -> list[str]:
    """The parts `highlight_toml` picked out, in order — read off the
    default-foreground escape that closes each one."""
    rendered = highlight_toml(text)
    return re.findall(rf"\x1b\[[0-9;]*m(.*?){re.escape(_DEFAULT_FG)}", rendered, re.S)


def plain(text: str, *, chunk: int = 0) -> str:
    """The rendered output with the styling stripped — what a reader sees."""
    return _ANSI.sub("", typeset(text, chunk=chunk))


def spoken(text: str) -> list[str]:
    """The visible text of each span the typesetter marked as speech."""
    spans = re.findall(f"{re.escape(_SPEECH)}(.*?){re.escape(_RESET)}", typeset(text), re.S)
    return [_ANSI.sub("", span) for span in spans]


def _open_style(rendered: str) -> bool:
    """True when the output ends inside a style (no RESET after the last
    style escape) — the terminal would stay coloured."""
    codes = _ANSI.findall(rendered)
    for code in reversed(codes):
        if code == _RESET:
            return False
        if code in (_BOLD, _ITALIC, "\x1b[2m"):
            return True
    return False
