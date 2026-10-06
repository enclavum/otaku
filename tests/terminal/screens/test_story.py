"""The dossier's pure edges: a setting's caption wrapped for the panel,
its bold opening kept bold across however many rows it wraps to, and
the caption table naming every setting the tab draws in its order."""

from otaku.terminal.screens.story import _ABOUT, _LEAD, _OPENS_GROUP, _caption

BOLD = "class:preview.body bold"
BODY = "class:preview.body"


class TestCaption:
    def test_a_caption_without_a_lead_is_plain_rows_with_a_newline_between(self) -> None:
        assert _caption("One two three.", "", 9) == [
            (BODY, "One two"),
            (BODY, "\n"),
            (BODY, "three."),
        ]

    def test_the_lead_is_bold_and_the_rest_is_not(self) -> None:
        assert _caption("Only here. The rest follows.", "Only here.", 80) == [
            (BOLD, "Only here."),
            (BODY, " The rest follows."),
        ]

    def test_a_lead_that_wraps_stays_bold_on_every_row_it_lands_on(self) -> None:
        rows = _caption("Only in the canvas. The rest.", "Only in the canvas.", 12)
        assert rows == [
            (BOLD, "Only in the"),
            (BODY, "\n"),
            (BOLD, "canvas."),
            (BODY, " The"),
            (BODY, "\n"),
            (BODY, "rest."),
        ]

    def test_no_trailing_newline(self) -> None:
        # a window sized to its content counts a trailing newline as a row
        for width in (8, 20, 80):
            assert _caption("Short words only here.", "Short", width)[-1][1] != "\n"


class TestCaptionTable:
    def test_every_lead_opens_its_caption(self) -> None:
        for name, lead in _LEAD.items():
            assert _ABOUT[name].startswith(lead), name

    def test_the_groups_open_after_the_first_row_in_the_tables_order(self) -> None:
        names = list(_ABOUT)
        assert names[0] not in _OPENS_GROUP
        assert set(names) >= _OPENS_GROUP
        # the tools, then the reminders, then the canvas's own
        assert names.index("use_story_reminder") < names.index("emulate_text_completion")
