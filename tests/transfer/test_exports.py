"""The export side of the story document.

Rendering is exercised end to end by the round-trip in
`test_imports.py`; here, what only the renderer decides: an untitled
story gets no heading.
"""

from otaku.transfer import ExportedMessage, StoryExport
from otaku.transfer.exports import render_story


def render(export: StoryExport) -> str:
    return render_story(
        export, otaku_version="0.2.0", model="omlx/test", exported="2026-07-29 12:00"
    )


class TestRenderStory:
    def test_an_untitled_export_has_no_heading(self) -> None:
        bare = StoryExport(messages=(ExportedMessage(role="user", body="Hi."),))
        assert not render(bare).lstrip().startswith("# ")
