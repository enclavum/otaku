"""A story's memory loaded whole (`Memory`): the arithmetic a scene's
representation carries as data — its number in story order and its span
on the current chain, so no frontend fishes them out of a label — how
far the extractor has read and the story has moved past a scene, a
character's arc so far, and a row looked up in the memory or refused."""

import pytest

from otaku.backend.domain.story.lore import Memory
from otaku.backend.errors import NotFound
from otaku.store.schema import Character, Journal, Scene


class TestSceneParts:
    def test_the_span_is_the_chain_positions_of_the_scene_ends(self) -> None:
        memory = _memory(Scene(id=1, start_message_id=61, end_message_id=84, title="The Crossing"))
        assert memory.scene_span(1) == "61-84"

    def test_a_scene_with_an_end_off_the_chain_has_no_span(self) -> None:
        # An edit moved the head past the scene: its positions cannot be
        # said, and the empty span is the promise — not a guessed range.
        memory = _memory(Scene(id=1, start_message_id=61, end_message_id=999))
        assert memory.scene_span(1) == ""

    def test_an_unknown_scene_has_no_span(self) -> None:
        memory = _memory(Scene(id=1, start_message_id=61, end_message_id=84))
        assert memory.scene_span(7) == ""

    def test_scenes_are_numbered_in_story_order_from_one(self) -> None:
        memory = _memory(
            Scene(id=5, start_message_id=61, end_message_id=62),
            Scene(id=3, start_message_id=63, end_message_id=64),
        )
        assert memory.scene_no(5) == 1
        assert memory.scene_no(3) == 2
        assert memory.scene_no(99) == 0


class TestProgress:
    def test_nothing_is_read_before_a_scene_closes(self) -> None:
        assert _memory().extracted_up_to() == 0

    def test_the_newest_scene_end_is_how_far_the_extractor_has_read(self) -> None:
        memory = _memory(
            Scene(id=1, start_message_id=1, end_message_id=20),
            Scene(id=2, start_message_id=21, end_message_id=48),
        )
        assert memory.extracted_up_to() == 48

    def test_the_story_moves_past_a_scene_one_message_at_a_time(self) -> None:
        # a chain of 100 messages, the scene ending at the 60th
        memory = _memory(Scene(id=1, start_message_id=1, end_message_id=60))
        assert memory.messages_since(1) == 40


class TestCharacterHistory:
    def test_the_newest_non_empty_rollup_is_the_arc_so_far(self) -> None:
        memory = _peopled(history=("h1", "h2"))
        assert memory.character_history(7) == ("h2", memory.journals[1])

    def test_a_scene_with_no_rollup_yet_leaves_the_older_one_standing(self) -> None:
        memory = _peopled(history=("h1", ""))
        assert memory.character_history(7) == ("h1", memory.journals[0])

    def test_nothing_before_a_pass_has_written_one(self) -> None:
        assert _peopled(history=("", "")).character_history(7) == ("", None)


class TestLookups:
    def test_a_row_of_the_memory_is_found_by_its_id(self) -> None:
        memory = _peopled(history=("", ""))
        assert memory.scene(2) is memory.scenes[1]
        assert memory.character(7) is memory.cast[0]
        assert memory.journal(12) is memory.journals[1]

    def test_a_row_not_in_the_memory_is_not_found(self) -> None:
        # another story's, or a rewound timeline's
        memory = _peopled(history=("", ""))
        with pytest.raises(NotFound):
            memory.scene(99)
        with pytest.raises(NotFound):
            memory.character(99)
        with pytest.raises(NotFound):
            memory.journal(99)


def _memory(*scenes: Scene) -> Memory:
    """A memory over a 100-message chain whose message ids ARE their
    positions — spans read directly against it."""
    return Memory(
        story_id=1,
        total_messages=100,
        scenes=scenes,
        cast=(),
        journals=(),
        ordinal={mid: mid for mid in range(1, 101)},
    )


def _peopled(*, history: tuple[str, str]) -> Memory:
    """Two scenes, one character, a journal record in each carrying the
    two rollups given — the smallest memory where the newest record
    differs from an older one."""
    return Memory(
        story_id=1,
        total_messages=4,
        scenes=(
            Scene(id=1, start_message_id=1, end_message_id=2),
            Scene(id=2, start_message_id=3, end_message_id=4),
        ),
        cast=(Character(id=7, name="Keeper"),),
        journals=(
            Journal(id=11, scene_id=1, character_id=7, entry="e1", state="s1", history=history[0]),
            Journal(id=12, scene_id=2, character_id=7, entry="e2", state="s2", history=history[1]),
        ),
        ordinal={n: n for n in range(1, 5)},
    )
