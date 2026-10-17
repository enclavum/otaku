"""The Cast tab's detail as a field list (`char_fields`): which rows a
character shows, in what order, and which of them a hand may correct —
the description and an imported card, each journal entry scene by scene
in story order, and the arc so far, which is the extractor's own."""

from otaku.terminal.api.story.lore import Character, Journal, Lore, Scene
from otaku.terminal.screens.dossier.cast import char_fields


class TestCharFields:
    def test_the_description_opens_and_is_the_authors(self) -> None:
        first = char_fields(_lore(_character()), 7)[0]
        assert (first.kind, first.editable) == ("description", True)

    def test_an_imported_card_is_a_row_of_its_own_to_correct(self) -> None:
        fields = char_fields(_lore(_character(profile="[card]")), 7)
        assert [(f.kind, f.editable) for f in fields][:2] == [
            ("description", True),
            ("profile", True),
        ]

    def test_states_are_never_editable_and_entries_always_are(self) -> None:
        # Through every scene, the newest record included: a state is
        # superseded by the next scene's, so no hand corrects any of them,
        # while the entry stays the writable primitive.
        fields = char_fields(_lore(_character()), 7)
        states = [f for f in fields if f.kind == "state"]
        entries = [f for f in fields if f.kind == "entry"]
        assert len(states) == len(entries) == 2
        assert not any(f.editable for f in states)
        assert all(f.editable for f in entries)

    def test_the_records_follow_the_story_not_the_order_they_arrive_in(self) -> None:
        fields = char_fields(_lore(_character(), journals=(12, 11)), 7)
        assert [f.target for f in fields if f.kind == "entry"] == [11, 12]

    def test_the_arc_so_far_closes_the_list_and_is_derived(self) -> None:
        fields = char_fields(_lore(_character(history="So far.", history_journal_id=12)), 7)
        last = fields[-1]
        assert (last.kind, last.text, last.target, last.editable) == (
            "history",
            "So far.",
            12,
            False,
        )

    def test_no_arc_row_before_a_pass_has_written_one(self) -> None:
        assert all(f.kind != "history" for f in char_fields(_lore(_character()), 7))


def _lore(character: Character, *, journals: tuple[int, ...] = (11, 12)) -> Lore:
    """Two scenes and one character with a journal record in each — the
    records listed in the order given, 11 the first scene's."""
    scene_of = {11: 1, 12: 2}
    return Lore(
        story_id=1,
        message_count=4,
        extracted_up_to=4,
        scenes=(_scene(1), _scene(2)),
        characters=(character,),
        journals=tuple(_journal(journal_id, scene_of[journal_id]) for journal_id in journals),
    )


def _character(
    *, profile: str | None = None, history: str = "", history_journal_id: int | None = None
) -> Character:
    return Character(
        id=7,
        name="Keeper",
        aliases=(),
        description="Keeps the light.",
        profile=profile,
        profile_fields=None,
        history=history,
        history_journal_id=history_journal_id,
        card_id=None,
        card_name=None,
        card_portrait=None,
        portrait_sent=None,
        provider=None,
        model=None,
        updated_at="",
    )


def _scene(scene_id: int) -> Scene:
    return Scene(
        id=scene_id,
        no=scene_id,
        span="",
        title=f"Scene {scene_id}",
        summary="",
        history="",
        start_message_id=scene_id,
        end_message_id=scene_id,
        provider=None,
        model=None,
        updated_at="",
    )


def _journal(journal_id: int, scene_id: int) -> Journal:
    return Journal(
        id=journal_id,
        scene_id=scene_id,
        scene_no=scene_id,
        character_id=7,
        entry=f"entry {journal_id}",
        state=f"state {journal_id}",
        history="",
        messages_since=0,
        provider=None,
        model=None,
        updated_at="",
    )
