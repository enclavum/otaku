"""/card — a character card imported into the story.

An import is three writes and no model call: the `/card` line as typed
(`kind='card'`, speaker-linked to the cast), the cast row carrying the
card TOML, and the greeting — the character's own words, an authored
assistant turn with the speaker set. The wire composes the block from
the archive at request time, so a `/lore` correction reaches the next
request; `/undo` takes the import's exchange whole. The extraction never
reads a card (a story holding only one extracts nothing, and a forced
pass summarizes around it). The fixtures are SillyTavern's own bundled
Seraphina (a real PNG card) and a minimal JSON one.
"""

import tomllib
from pathlib import Path

from scenarios.support.harness import App

SERAPHINA = Path(__file__).parent.parent / "fixtures" / "seraphina.png"
MINIMAL = Path(__file__).parent.parent / "fixtures" / "spec_v2_minimal.json"


class TestImport:
    def test_an_import_is_the_typed_row_the_cast_row_and_the_greeting(self, app: App) -> None:
        app.play(f"/card {SERAPHINA}")
        rows = app.store.stories.get_messages(app.session.story_id)
        assert [(m.role, m.kind) for m in rows] == [("user", "card"), ("assistant", "dialogue")]
        row, greeting = rows
        # The row is the line as typed — the block composes at wire time.
        assert row.body == f"/card {SERAPHINA}" and row.template is None
        (character,) = app.store.characters.list(app.session.story_id)
        assert character.name == "Seraphina"
        # Made by the card, spelled as the greeting spells its source.
        assert (character.provider, character.model) == ("card", "seraphina.png")
        # Both rows carry the speaker link; the card row's is the compose key.
        assert (row.speaker, row.speaker_id) == ("Seraphina", character.id)
        assert (greeting.speaker, greeting.speaker_id) == ("Seraphina", character.id)
        # Authored, not generated — the source spelled the way generated
        # rows spell theirs.
        assert (greeting.provider, greeting.model) == ("card", "seraphina.png")
        assert "{{" not in greeting.body  # macros bound

    def test_the_archive_records_the_bindings(self, app: App) -> None:
        app.play(f"/card {SERAPHINA} Warden")
        (character,) = app.store.characters.list(app.session.story_id)
        data = tomllib.loads(character.card)
        assert (data["name"], data["user"]) == ("Warden", "you")
        assert data["file_name"] == "seraphina.png"

    def test_a_card_without_a_greeting_imports_the_block_alone(self, app: App) -> None:
        app.play(f"/card {MINIMAL}")
        rows = app.store.stories.get_messages(app.session.story_id)
        assert [m.kind for m in rows] == ["card"]

    def test_a_duplicate_name_is_refused_untouched(self, app: App) -> None:
        app.play(f"/card {SERAPHINA}")
        before = len(app.store.stories.get_messages(app.session.story_id))
        app.play(f"/card {SERAPHINA}")
        assert len(app.store.stories.get_messages(app.session.story_id)) == before
        assert len(app.store.characters.list(app.session.story_id)) == 1

    def test_the_import_calls_no_model(self, app: App) -> None:
        app.play(f"/card {SERAPHINA}")
        assert app.server.requests == []

    def test_the_block_and_the_greeting_ride_the_next_request(self, app: App) -> None:
        # The stored row is the typed line; the WIRE carries the composed
        # block — the dualism every framing command lives by.
        app.play(f"/card {SERAPHINA}")
        app.play("I sit up slowly.")
        wire = "\n".join(str(m["content"]) for m in app.server.requests[-1]["messages"])
        assert "Seraphina joins the story" in wire
        assert "You wake with a start" in wire
        assert "/card" not in wire  # the typed line never sends

    def test_a_fork_carries_the_card_to_the_wire(self, app: App) -> None:
        # The fork copies the archive with the cast, so the branch's card
        # rows keep composing — the model must never see the typed line.
        app.play(f"/card {SERAPHINA}")
        app.play("/fork")
        app.play("I look around the glade.")
        wire = "\n".join(str(m["content"]) for m in app.server.requests[-1]["messages"])
        # Card CONTENT only the composed block carries — the greeting rows
        # never mention the description fields.
        assert "Seraphina's Personality" in wire
        assert "/card" not in wire

    def test_a_lore_correction_reaches_the_next_request(self, app: App) -> None:
        # The archive is the source at WIRE time, not at import: a /lore
        # edit of the card TOML changes what every later request sends.
        app.play(f"/card {SERAPHINA}")
        (character,) = app.store.characters.list(app.session.story_id)
        edited = character.card.replace("Seraphina's Personality", "Seraphina's Nature")
        app.store.characters.set_card(character.id, edited)
        app.play("I sit up slowly.")
        wire = "\n".join(str(m["content"]) for m in app.server.requests[-1]["messages"])
        assert "Seraphina's Nature" in wire
        assert "Seraphina's Personality" not in wire

    def test_undo_takes_the_import_exchange_whole(self, app: App) -> None:
        # The card introduces the character and the character answers —
        # one exchange, so /undo discards the typed row, the greeting, AND
        # the unplayed character: the retry below is the reason, and it
        # must not be refused as a duplicate.
        app.play(f"/card {SERAPHINA}")
        app.play("/undo")
        assert app.store.stories.get_messages(app.session.story_id) == []
        assert app.store.characters.list(app.session.story_id) == []
        app.play(f"/card {SERAPHINA}")
        assert len(app.store.characters.list(app.session.story_id)) == 1

    def test_undo_keeps_a_character_the_story_journaled(self, app: App) -> None:
        # A journal marks them played — extraction wove them into the
        # story — so the cast row outlives the import rows it arrived on.
        app.play(f"/card {SERAPHINA}")
        story_id = app.session.story_id
        (character,) = app.store.characters.list(story_id)
        ids = app.store.stories.get_messages_ids(story_id)
        scene_id = app.store.scenes.add(
            story_id, start_message_id=ids[0], end_message_id=ids[-1], summary="She woke."
        )
        app.store.journals.add(story_id, scene_id, character.id, entry="Woke them.", state="warm")
        app.play("/undo")
        assert app.store.stories.get_messages(story_id) == []
        assert len(app.store.characters.list(story_id)) == 1


class TestExtraction:
    def test_a_story_of_one_card_extracts_nothing(self, app: App) -> None:
        # A card is not story: alone (this fixture has no greeting) there
        # is nothing to close a scene over, however many characters of
        # text the block holds — even forced.
        app.play(f"/card {MINIMAL}")
        app.play("/extract")
        assert (
            app.store.scenes.get_current(
                app.session.story_id, app.store.stories.get_messages_ids(app.session.story_id)
            )
            == []
        )

    def test_a_forced_pass_summarizes_around_the_card(self, app: App) -> None:
        app.play(f"/card {SERAPHINA}")
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.play("/extract")
        story_id = app.session.story_id
        ids = app.store.stories.get_messages_ids(story_id)
        scenes = app.store.scenes.get_current(story_id, ids)
        assert scenes  # played turns closed as a scene
        extraction = next(
            str(r["messages"][-1]["content"])
            for r in app.server.requests
            if "story analyst" in str(r["messages"][-1]["content"])
        )
        # The card BLOCK never reaches the pass — but the greeting does:
        # the character's first words are the story's first beat, and the
        # scene rightly remembers them.
        assert "Seraphina joins the story" not in extraction
        assert "You wake with a start" in extraction
