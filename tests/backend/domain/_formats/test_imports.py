"""The import side of the story document.

Its contract is the round-trip: `parse_story(render_story(x))` returns
`x` exactly — titles, system, story-so-far, cast with aliases and
descriptions, scenes with spans, summaries and journals, and messages
with their kind, speaker, and verbatim template and bodies. A file without
the export marker parses as None; a declared format above the reader's
raises, while any older or absent one is read.
"""

import pytest

from otaku.backend.domain._formats import (
    EXPORT_FORMAT_VERSION,
    ExportedCharacter,
    ExportedJournal,
    ExportedMessage,
    ExportedScene,
    ExportedStory,
)
from otaku.backend.domain._formats.exports import render_story
from otaku.backend.domain._formats.imports import NewerFormatError, parse_story

FULL = ExportedStory(
    title="Болотная часовня",
    system="Ты — рассказчик.",
    cast=(
        ExportedCharacter(
            "Кассиан",
            ("Кас",),
            "усталый наёмник",
            # Deliberately structure-shaped content: the profile block must
            # survive lines that look like the document's own headings and
            # roster bullets.
            profile='name = "Кассиан"\n\ndescription = """\n### Глава\n- **точка** списка\n"""',
        ),
        ExportedCharacter("Элоиза"),
    ),
    scenes=(
        ExportedScene(
            title="Часовня",
            span=(1, 3),
            summary="Кассиан входит и встречает Элоизу.",
            history="Кассиан пришёл в часовню.\nТам ждала Элоиза.",
            journals=(
                ExportedJournal(
                    "Кассиан", entry="Я вошёл.", state="у алтаря", history="Всё, что я видел."
                ),
                ExportedJournal("Элоиза", entry="Он пришёл.", state="в тени"),
            ),
        ),
        # The newest scene's history doubles as the document's story-so-far
        # preface; the round-trip proves the fold never overwrites it.
        ExportedScene(span=(4, 4), summary="Разговор продолжается.", history="Кассиан в часовне."),
    ),
    messages=(
        ExportedMessage(role="user", body="Я вхожу в часовню.", speaker="Кассиан"),
        ExportedMessage(role="assistant", body="Дверь скрипит.\n\nВнутри темно."),
        ExportedMessage(
            role="user",
            body="Кто здесь?",
            template="((OOC: The user writes as Кассиан.))\n{body}",
        ),
        ExportedMessage(role="assistant", body="Хороший план.", kind="ooc"),
        ExportedMessage(role="user", body="Тишина висела в воздухе.", kind="narration"),
    ),
)


class TestRoundTrip:
    def test_a_full_story_survives_exactly(self) -> None:
        assert parse_story(render(FULL)) == FULL

    def test_a_body_led_by_a_heading_survives(self) -> None:
        x = ExportedStory(
            messages=(ExportedMessage(role="user", body="### Chapter One\nThe hall was dark."),)
        )
        assert parse_story(render(x)) == x

    def test_a_body_with_a_section_heading_keeps_later_messages(self) -> None:
        x = ExportedStory(
            messages=(
                ExportedMessage(role="user", body="## Recap\nSo far so good."),
                ExportedMessage(role="assistant", body="A later reply."),
            )
        )
        assert parse_story(render(x)) == x

    def test_an_unbalanced_fence_swallows_nothing(self) -> None:
        x = ExportedStory(
            messages=(
                ExportedMessage(role="user", body="An unbalanced fence:\n```\ncode here"),
                ExportedMessage(role="assistant", body="The next reply."),
            )
        )
        assert parse_story(render(x)) == x

    def test_an_escape_shaped_line_survives(self) -> None:
        x = ExportedStory(
            messages=(ExportedMessage(role="user", body="\\### literally backslashed"),)
        )
        assert parse_story(render(x)) == x

    def test_a_summary_and_a_system_with_headings_survive(self) -> None:
        x = ExportedStory(
            system="## House rules\nNo dragons.",
            scenes=(
                ExportedScene(
                    title="One",
                    span=(1, 1),
                    summary="## Twist\nIt happened.",
                    history="### So far\nA guest arrived.",
                ),
            ),
            messages=(ExportedMessage(role="user", body="Hi."),),
        )
        assert parse_story(render(x)) == x

    def test_a_bare_story_survives(self) -> None:
        bare = ExportedStory(
            messages=(
                ExportedMessage(role="user", body="Hi."),
                ExportedMessage(role="assistant", body="Hello."),
            )
        )
        assert parse_story(render(bare)) == bare

    def test_multiline_framing_survives_verbatim(self) -> None:
        template = "((OOC: line one.\n\nline two.))\n{body}"
        export = ExportedStory(
            messages=(ExportedMessage(role="user", body="Go.", template=template),)
        )
        parsed = parse_story(render(export))
        assert parsed is not None
        assert parsed.messages[0].template == template

    def test_body_edges_strip_but_interior_blank_lines_stay(self) -> None:
        export = ExportedStory(messages=(ExportedMessage(role="assistant", body="One.\n\nTwo."),))
        parsed = parse_story(render(export))
        assert parsed is not None
        assert parsed.messages[0].body == "One.\n\nTwo."


class TestParseExport:
    def test_text_without_the_marker_is_not_an_export(self) -> None:
        assert parse_story("# A story\n\nJust some prose.") is None

    def test_the_title_comes_from_the_heading(self) -> None:
        parsed = parse_story(render(FULL))
        assert parsed is not None
        assert parsed.title == "Болотная часовня"


class TestFormatVersion:
    """The reader's law: every older format parses, a newer declared one
    is refused, and a block with no version line is read best-effort."""

    def test_an_older_format_parses(self) -> None:
        older = _with_version(render(FULL), "format-version: 1")
        assert parse_story(older) == FULL

    def test_a_newer_format_is_refused(self) -> None:
        newer = _with_version(render(FULL), f"format-version: {EXPORT_FORMAT_VERSION + 1}")
        with pytest.raises(NewerFormatError):
            parse_story(newer)

    def test_no_version_line_is_read_best_effort(self) -> None:
        assert parse_story(_with_version(render(FULL), "")) == FULL

    def test_an_old_documents_story_so_far_folds_onto_the_newest_scene(self) -> None:
        # Before format 3, the arc lived only in the `### Story so far`
        # block; the parser IS the upward migration, so it lands as the
        # newest scene's own history.
        doc = (
            "<!-- otaku export\nformat-version: 2\n-->\n\n## Story\n\n"
            "### Story so far\n\nA guest arrived.\n\n## Scenes\n\n"
            "### 1 · One\n- **Messages:** 1\n\nIt happened.\n\n"
            "## Messages\n\n### 1 · user\nHi.\n"
        )
        parsed = parse_story(doc)
        assert parsed is not None
        assert parsed.scenes[-1].history == "A guest arrived."


class TestMessageHeaders:
    """The header's trailing fields — a bare speaker, a JSON-quoted
    template, or both, in that order; anything unparseable degrades to
    absent rather than corrupting the message."""

    def parse_one(self, header: str) -> ExportedMessage:
        doc = f"<!-- otaku export\n-->\n\n## Messages\n\n### {header}\nBody.\n"
        parsed = parse_story(doc)
        assert parsed is not None and len(parsed.messages) == 1
        return parsed.messages[0]

    def test_a_bare_header(self) -> None:
        message = self.parse_one("1 · user")
        assert (message.speaker, message.template) == (None, None)

    def test_a_speaker_alone(self) -> None:
        message = self.parse_one("1 · user · Рин")
        assert (message.speaker, message.template) == ("Рин", None)

    def test_a_framing_alone(self) -> None:
        message = self.parse_one('1 · user · "((OOC: x))\\n{body}"')
        assert message.speaker is None
        assert message.template == "((OOC: x))\n{body}"

    def test_a_speaker_and_a_template(self) -> None:
        message = self.parse_one('1 · user (ooc) · Рин · "((OOC: y))"')
        assert message.kind == "ooc"
        assert message.speaker == "Рин"
        assert message.template == "((OOC: y))"

    def test_an_unterminated_framing_degrades_to_absent(self) -> None:
        message = self.parse_one('1 · user · Рин · "unterminated')
        assert message.speaker == "Рин"
        assert message.template is None
        assert message.body == "Body."

    def test_a_non_string_after_the_quote_degrades_to_absent(self) -> None:
        assert self.parse_one('1 · user · "123"').template == "123"
        assert self.parse_one("1 · user · [1]").speaker == "[1]"


def render(export: ExportedStory) -> str:
    return render_story(
        export, otaku_version="0.2.0", model="omlx/test", exported="2026-07-29 12:00"
    )


def _with_version(document: str, line: str) -> str:
    """The document with its format-version line replaced by `line` —
    dropped entirely when `line` is empty."""
    current = f"format-version: {EXPORT_FORMAT_VERSION}\n"
    return document.replace(current, f"{line}\n" if line else "")
