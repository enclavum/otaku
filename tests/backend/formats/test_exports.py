"""The export side of the story document.

Rendering is exercised end to end by the round-trip in
`test_imports.py`; here, what only the renderer decides: an untitled
story gets no heading, and a pin is written after the kind.
"""

from otaku.backend.formats import ExportedMessage, ExportedStory
from otaku.backend.formats.exports import render_story


class TestRenderStory:
    def test_an_untitled_export_has_no_heading(self) -> None:
        bare = ExportedStory(messages=(ExportedMessage(role="user", body="Hi."),))
        assert not render(bare).lstrip().startswith("# ")

    def test_a_pin_is_written_after_the_kind(self) -> None:
        pinned = ExportedStory(
            messages=(
                ExportedMessage(role="user", body="Aside.", kind="ooc", pinned=True),
                ExportedMessage(role="assistant", body="Reply.", pinned=True),
                ExportedMessage(role="user", body="On.", kind="ooc"),
            )
        )
        headers = [line for line in render(pinned).splitlines() if line.startswith("### ")]
        assert headers == [
            "### 1 · user (ooc, pinned)",
            "### 2 · assistant (pinned)",
            "### 3 · user (ooc)",
        ]


def render(export: ExportedStory) -> str:
    return render_story(
        export, otaku_version="0.2.0", model="omlx/test", exported="2026-07-29 12:00"
    )
