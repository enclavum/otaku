"""The Scenes tab's detail as a field list (`scene_fields`): which rows a
scene shows, in what order, and which of them a hand may correct — the
author's primitives, never what the extractor derives."""

from otaku.terminal.api.lore import Character, Journal, Lore, Scene
from otaku.terminal.screens.dossier.scenes import scene_fields


class TestSceneFields:
    def test_the_scene_history_sits_after_the_summary_in_every_scene(self) -> None:
        # Read-only in each: the scene's history is the extractor's own,
        # and the row exists even before a pass has written it — an
        # empty text, not an absent field.
        view = _lore(
            scenes=(_scene(1, summary="A.", history="Arc."), _scene(2, summary="B.")),
        )
        for scene_id in (1, 2):
            fields = scene_fields(view, scene_id)
            assert [f.kind for f in fields][:3] == ["scene-title", "scene-summary", "scene-history"]
            assert [f.editable for f in fields][:3] == [True, True, False]
        assert scene_fields(view, 1)[2].text == "Arc."
        assert scene_fields(view, 2)[2].text == ""

    def test_each_record_of_the_scene_is_an_editable_entry_and_a_derived_state(self) -> None:
        view = _lore(
            scenes=(_scene(1), _scene(2)),
            characters=(_character(7, "Keeper"),),
            journals=(
                _journal(11, scene_id=1, character_id=7),
                _journal(12, scene_id=2, character_id=7),
            ),
        )
        rows = [(f.kind, f.target, f.editable) for f in scene_fields(view, 2)[3:]]
        assert rows == [("entry", 12, True), ("state", 12, False)]


def _lore(
    *,
    scenes: tuple[Scene, ...] = (),
    characters: tuple[Character, ...] = (),
    journals: tuple[Journal, ...] = (),
) -> Lore:
    return Lore(
        story_id=1,
        message_count=4,
        extracted_up_to=4,
        scenes=scenes,
        characters=characters,
        journals=journals,
    )


def _scene(scene_id: int, *, summary: str = "", history: str = "") -> Scene:
    return Scene(
        id=scene_id,
        no=scene_id,
        span="",
        title=f"Scene {scene_id}",
        summary=summary,
        history=history,
        start_message_id=scene_id,
        end_message_id=scene_id,
        provider=None,
        model=None,
        updated_at="",
    )


def _character(character_id: int, name: str) -> Character:
    return Character(
        id=character_id,
        name=name,
        aliases=(),
        description="",
        profile=None,
        profile_fields=None,
        history="",
        history_journal_id=None,
        card_id=None,
        card_name=None,
        card_portrait=None,
        portrait_sent=None,
        provider=None,
        model=None,
        updated_at="",
    )


def _journal(journal_id: int, *, scene_id: int, character_id: int) -> Journal:
    return Journal(
        id=journal_id,
        scene_id=scene_id,
        scene_no=scene_id,
        character_id=character_id,
        entry=f"entry {journal_id}",
        state=f"state {journal_id}",
        history="",
        messages_since=0,
        provider=None,
        model=None,
        updated_at="",
    )
