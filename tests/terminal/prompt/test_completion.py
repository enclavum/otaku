"""Which completion menu belongs at the cursor, and what it filters by.

Two surfaces, asked the same two questions. `applies` decides which one is
in play — a line opening with a slash is the command menu's, a slash token
being typed inside prose is the inliner menu's, and ordinary prose is
neither's, so the menu never pops mid-sentence. `partial` is the token
being completed, empty when a menu belongs with nothing typed into it yet.

`SlashCompleter.partial` is the two of them as one question, which is what
the prompt asks: it must anchor the menu and decide whether one can be
open at all without knowing which surface answered. It reads the line in
the context of the message it belongs to, so on a continuation line inside
an open block a leading slash is an inliner's place, never a command's.
"""

from prompt_toolkit.completion import CompleteEvent, Completion
from prompt_toolkit.document import Document

from otaku.backend.session import PARAMETERS, THINK_MENU
from otaku.terminal.prompt.completion import CommandCompleter, InlinerCompleter, SlashCompleter


class TestInlinerSurface:
    def test_a_slash_after_a_word_opens_it(self) -> None:
        assert InlinerCompleter.partial("she looks up /") == "/"

    def test_a_partly_typed_inliner_is_the_filter(self) -> None:
        assert InlinerCompleter.partial("she looks up /c") == "/c"

    def test_a_fully_typed_inliner_is_still_the_token(self) -> None:
        assert InlinerCompleter.partial("she looks up /cue") == "/cue"

    def test_past_the_space_the_token_is_finished(self) -> None:
        assert InlinerCompleter.applies("she looks up /cue ") is False

    def test_the_inliners_own_text_is_not_a_token(self) -> None:
        assert InlinerCompleter.applies("she looks up /cue keep") is False

    def test_prose_alone_opens_nothing(self) -> None:
        assert InlinerCompleter.applies("she looks up") is False

    def test_an_empty_line_opens_nothing(self) -> None:
        assert InlinerCompleter.applies("") is False

    def test_a_command_line_belongs_to_the_other_surface(self) -> None:
        assert InlinerCompleter.applies("/cu") is False
        assert InlinerCompleter.applies("  /cu") is False

    # Prose keeps its slashes: the slash must follow whitespace.
    def test_a_slash_inside_a_word_opens_nothing(self) -> None:
        assert InlinerCompleter.applies("she looks up and/") is False

    def test_a_url_opens_nothing(self) -> None:
        assert InlinerCompleter.applies("read https://") is False

    def test_a_date_opens_nothing(self) -> None:
        assert InlinerCompleter.applies("on 24/") is False

    def test_a_slash_typed_after_a_space_does_open_it(self) -> None:
        # The rule's one cost: a slash following a space cannot be told
        # apart from one starting a command, so a spaced "and /or" opens the
        # menu for a keystroke. The next character filters every row away.
        assert InlinerCompleter.partial("and /") == "/"


class TestCommandSurface:
    def test_a_slash_line_is_its_own(self) -> None:
        assert CommandCompleter.applies("/me") is True

    def test_leading_space_still_counts(self) -> None:
        assert CommandCompleter.applies("  /me") is True

    def test_prose_is_not_its_own(self) -> None:
        assert CommandCompleter.applies("she looks up /") is False

    def test_the_last_word_is_the_filter(self) -> None:
        assert CommandCompleter.partial("/set thi") == "thi"

    def test_after_a_space_nothing_is_typed_yet(self) -> None:
        assert CommandCompleter.partial("/set ") == ""


class TestPartial:
    def test_a_command_line_answers(self) -> None:
        assert _partial("/me") == "/me"

    def test_an_argument_about_to_be_typed_answers_empty(self) -> None:
        # Empty is not None: a menu belongs, and it anchors at the cursor.
        assert _partial("/set ") == ""

    def test_an_inliner_answers(self) -> None:
        assert _partial("she looks up /c") == "/c"

    def test_prose_answers_with_no_menu(self) -> None:
        assert _partial("she looks up") is None

    def test_inside_a_block_a_leading_slash_is_an_inliner(self) -> None:
        # Each continuation line is its own buffer, so without the block's
        # text a `/` opening one looks like the start of a submission.
        assert _partial("/", block="she looks up\n") == "/"
        assert InlinerCompleter.applies("she looks up\n/") is True
        assert CommandCompleter.applies("she looks up\n/") is False

    def test_a_command_still_opens_the_first_line_of_a_block(self) -> None:
        assert _partial("/me", block="") == "/me"

    def test_an_empty_first_line_still_counts_as_inside(self) -> None:
        # `\"\"\"` alone collects one empty line, so the prefix is just a
        # newline — the message has begun even though nothing is in it.
        assert CommandCompleter.applies("\n/") is False
        assert _partial("/", block="\n") == "/"

    def test_the_two_surfaces_never_both_answer(self) -> None:
        for text in ("/me", "  /set ", "she looks up /c", "she looks up", "", "and/or"):
            answered = [s for s in (CommandCompleter, InlinerCompleter) if s.applies(text)]
            assert len(answered) <= 1, text


class TestLayoutFold:
    """The menu filter folds ЙЦУКЕН to the QWERTY keys at the same physical
    positions, so a command types without leaving the Russian layout —
    and accepting a row replaces the typed token with the real command,
    Cyrillic never reaching the line."""

    def test_cyrillic_filters_by_physical_position(self) -> None:
        assert _offered("/ьу") == ["/me", "/merge"]  # the physical m-e keys

    def test_case_folds_with_the_layout(self) -> None:
        assert _offered("/РУДЗ") == ["/help"]

    def test_the_replacement_covers_what_was_typed(self) -> None:
        rows = _rows_for("/ьу")
        assert all(r.start_position == -3 for r in rows)

    def test_an_inliner_folds_the_same_way(self) -> None:
        assert _offered("она ждёт /сгу") == ["/cue"]

    def test_ascii_is_untouched(self) -> None:
        assert _offered("/m") == ["/me", "/merge", "/model"]


class TestRowShape:
    """A row reads as the line it starts: the command, then what it takes,
    then the key that runs it without typing at all — the last two dimmed,
    since only the command is inserted. The keys are a COLUMN of their own,
    so they read down the menu as a list rather than trailing each label."""

    def test_a_shortcut_ends_the_row(self) -> None:
        assert _shown("/undo").endswith("Ctrl+U")
        assert _shown("/model").startswith("/model [PROVIDER/MODEL]")

    def test_the_keys_share_one_column(self) -> None:
        starts = {_shown(command).index("Ctrl+") for command in ("/undo", "/model", "/bye")}
        assert len(starts) == 1

    def test_a_command_without_one_shows_none(self) -> None:
        assert _shown("/clear") == "/clear"
        assert _shown("/card") == "/card FILE [NAME]"

    def test_only_the_command_is_undimmed(self) -> None:
        # What is inserted is what reads as typed; the rest is guidance.
        row = next(r for r in _rows_for("/und") if r.text == "/undo")
        assert [style for style, _ in row.display if _.strip()] == ["", "dim"]

    def test_every_row_pads_to_one_width(self) -> None:
        # Fixed over the WHOLE menu, so the description column never moves
        # as the filter narrows it.
        widths = {len("".join(text for _, text in r.display)) for r in _rows_for("/")}
        assert len(widths) == 1


class TestTakesArgument:
    """The flag the prompt accepts a row by: True takes a space and waits,
    False sends. Anything that can follow counts — a bracketed parameter
    is offered because it was chosen, and the bare line is one Enter away
    either way."""

    def test_a_required_parameter_takes_one(self) -> None:
        for command in ("/title", "/import", "/me", "/system", "/merge"):
            assert _takes(command) is True, command

    def test_an_optional_parameter_takes_one_too(self) -> None:
        for command in ("/new", "/fork", "/usage", "/last", "/export", "/model"):
            assert _takes(command) is True, command

    def test_a_command_that_takes_nothing_does_not(self) -> None:
        for command in ("/undo", "/regen", "/clear", "/info", "/help", "/bye"):
            assert _takes(command) is False, command

    def test_a_subcommand_counts_as_a_follower(self) -> None:
        # /set is incomplete without one of its family, and each of those
        # takes its own value.
        assert _takes("/set") is True
        row = next(r for r in _rows_for("/set ") if r.text == "think")
        assert row.takes_argument is True


class TestThinkMenu:
    """The /set think menu is what the model in use takes, looked up
    live through `levels` — nothing is offered past a level."""

    def test_the_menu_is_what_the_session_answers(self) -> None:
        completer = SlashCompleter.build(levels=lambda: ("unset", "none", "low"))
        rows = list(completer.get_completions(Document("/set think "), CompleteEvent()))
        assert [r.text for r in rows] == ["unset", "none", "low"]
        rows = list(completer.get_completions(Document("/set think u"), CompleteEvent()))
        assert [r.text for r in rows] == ["unset"]

    def test_without_a_session_the_whole_ladder_is_offered(self) -> None:
        assert _offered("/set think ") == list(THINK_MENU)

    def test_nothing_follows_a_level(self) -> None:
        assert _offered("/set think high ") == []


class TestParameterMenu:
    """The /set parameter menu is what the provider in use reads, looked
    up live through `parameters` — each followed by its `reset`."""

    def test_the_menu_is_what_the_session_answers(self) -> None:
        completer = SlashCompleter.build(parameters=lambda: ("temperature", "seed"))
        rows = list(completer.get_completions(Document("/set parameter "), CompleteEvent()))
        assert [r.text for r in rows] == ["temperature", "seed"]
        rows = list(completer.get_completions(Document("/set parameter seed "), CompleteEvent()))
        assert [r.text for r in rows] == ["reset"]

    def test_without_a_session_every_parameter_is_offered(self) -> None:
        assert _offered("/set parameter ") == list(PARAMETERS)


class TestCastSuggestions:
    """The commands whose argument is a character offer the cast, shaped
    per command: /me inserts `Name:` and the prompt follows, /you inserts
    the bare name (the hint being the rarer option), /merge completes both
    sides of `A into B`. The rows come from the injected callable — the
    real one reads the live story."""

    CAST = (("Elara", "warden of the gate"), ("The Keeper", "keeps the door"))

    def test_me_offers_names_with_the_colon(self) -> None:
        rows = self._rows("/me ")
        assert [r.text for r in rows] == ["Elara:", "The Keeper:"]
        assert all(r.takes_argument for r in rows)

    def test_you_offers_bare_names(self) -> None:
        rows = self._rows("/you el")
        assert [(r.text, r.start_position, r.takes_argument) for r in rows] == [
            ("Elara", -2, False)
        ]

    def test_a_name_with_spaces_filters_whole(self) -> None:
        # The segment is the raw argument text, not the last token.
        assert [r.text for r in self._rows("/me the k")] == ["The Keeper:"]

    def test_merge_completes_both_sides(self) -> None:
        first = self._rows("/merge ")
        assert [r.text for r in first] == ["Elara into", "The Keeper into"]
        assert all(r.takes_argument for r in first)
        second = self._rows("/merge The Keeper into el")
        assert [(r.text, r.start_position, r.takes_argument) for r in second] == [
            ("Elara", -2, False)
        ]

    def test_past_the_colon_the_argument_is_content(self) -> None:
        assert self._rows("/me Elara: I st") == []
        assert self._rows("/you Elara: be co") == []

    def test_the_description_rides_the_meta_column(self) -> None:
        (row,) = self._rows("/me el")
        assert "warden of the gate" in str(row.display_meta)

    def test_without_a_cast_nothing_is_offered(self) -> None:
        completer = SlashCompleter.build()
        assert list(completer.get_completions(Document("/me "), CompleteEvent())) == []

    def _rows(self, text: str) -> list[Completion]:
        completer = SlashCompleter.build(cast=lambda: self.CAST)
        return list(completer.get_completions(Document(text), CompleteEvent()))


def _offered(text: str) -> list[str]:
    return [r.text for r in _rows_for(text)]


def _shown(command: str) -> str:
    """A row's visible label, padding dropped."""
    row = next(r for r in _rows_for(command) if r.text == command)
    return "".join(text for _, text in row.display).rstrip()


def _takes(command: str) -> bool:
    """Whether accepting `command`'s own row leaves the line open."""
    row = next(r for r in _rows_for(command) if r.text == command)
    return bool(row.takes_argument)


# The captions arrive as DATA (`chat.bindings.SHORTCUTS` in the app);
# these rows test the column mechanism with the same shape injected.
SHORTCUTS = {"/undo": "Ctrl+U", "/model": "Ctrl+O", "/bye": "Ctrl+D"}


def _rows_for(text: str) -> list[Completion]:
    completer = SlashCompleter.build(shortcuts=SHORTCUTS)
    return list(completer.get_completions(Document(text), CompleteEvent()))


def _partial(text: str, block: str = "") -> str | None:
    """What the prompt would ask, with `block` standing in for whatever an
    open \"\"\" block has collected so far."""
    return SlashCompleter.build(lambda: block).partial(text)
