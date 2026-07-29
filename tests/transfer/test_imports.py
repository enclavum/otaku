"""The import side of the story document.

Its contract is the round-trip: `parse_story(render_story(x))` returns
`x` exactly — titles, system, story-so-far, cast with aliases and
descriptions, scenes with spans, summaries and journals, and messages
with their kind, speaker, and verbatim framing and bodies. A file without
the export marker parses as None.
"""

from otaku.transfer import (
    ExportedCharacter,
    ExportedJournal,
    ExportedMessage,
    ExportedScene,
    StoryExport,
)
from otaku.transfer.exports import render_story
from otaku.transfer.imports import parse_story


def render(export: StoryExport) -> str:
    return render_story(
        export, otaku_version="0.2.0", model="omlx/test", exported="2026-07-29 12:00"
    )


FULL = StoryExport(
    title="Болотная часовня",
    system="Ты — рассказчик.",
    story_so_far="Кассиан добрался до часовни.",
    cast=(
        ExportedCharacter("Кассиан", ("Кас",), "усталый наёмник"),
        ExportedCharacter("Элоиза"),
    ),
    scenes=(
        ExportedScene(
            title="Часовня",
            span=(1, 3),
            summary="Кассиан входит и встречает Элоизу.",
            journals=(
                ExportedJournal(
                    "Кассиан", entry="Я вошёл.", state="у алтаря", history="Всё, что я видел."
                ),
                ExportedJournal("Элоиза", entry="Он пришёл.", state="в тени"),
            ),
        ),
        ExportedScene(span=(4, 4), summary="Разговор продолжается."),
    ),
    messages=(
        ExportedMessage(role="user", body="Я вхожу в часовню.", speaker="Кассиан"),
        ExportedMessage(role="assistant", body="Дверь скрипит.\n\nВнутри темно."),
        ExportedMessage(
            role="user",
            body="Кто здесь?",
            framing="((OOC: The user writes as Кассиан.))\n{body}",
        ),
        ExportedMessage(role="assistant", body="Хороший план.", kind="ooc"),
        ExportedMessage(role="user", body="Тишина висела в воздухе.", kind="narration"),
    ),
)


class TestRoundTrip:
    def test_a_full_story_survives_exactly(self) -> None:
        assert parse_story(render(FULL)) == FULL

    def test_a_bare_story_survives(self) -> None:
        bare = StoryExport(
            messages=(
                ExportedMessage(role="user", body="Hi."),
                ExportedMessage(role="assistant", body="Hello."),
            )
        )
        assert parse_story(render(bare)) == bare

    def test_multiline_framing_survives_verbatim(self) -> None:
        framing = "((OOC: line one.\n\nline two.))\n{body}"
        export = StoryExport(messages=(ExportedMessage(role="user", body="Go.", framing=framing),))
        parsed = parse_story(render(export))
        assert parsed is not None
        assert parsed.messages[0].framing == framing

    def test_body_edges_strip_but_interior_blank_lines_stay(self) -> None:
        export = StoryExport(messages=(ExportedMessage(role="assistant", body="One.\n\nTwo."),))
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
