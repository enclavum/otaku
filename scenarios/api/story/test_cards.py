"""/card — a character card imported into the story.

An import is three writes and no model call: the `/card` line as typed
(`kind='card'`, speaker-linked to the cast), the cast row carrying the
card TOML, and the greeting — the character's own words, an authored
assistant turn with the speaker set. The wire composes the block from
the archive at request time, so a `/lore` correction reaches the next
request; `/undo` takes the import's exchange whole. Beside its block a
card's system prompt joins the system message and its reminder rides on
its own at its depth, read from the same archive. The extraction never
reads a card (a story holding only one extracts nothing, and a forced
pass summarizes around it). The fixtures are SillyTavern's own bundled
Seraphina (a real PNG card) and a minimal JSON one.
"""

import io
import json
import tomllib
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from PIL import Image

from otaku.backend.api import HeadMoved, Refused
from otaku.backend.codex.cards.schemas import RawFile
from otaku.backend.codex.cards.services import NEW_CARD_NAME
from otaku.backend.story import request
from otaku.store.pictures import Attachment
from scenarios.support.harness import App
from scenarios.support.site import Site, serving

SERAPHINA = Path(__file__).parents[2] / "fixtures" / "seraphina.png"

MINIMAL = Path(__file__).parents[2] / "fixtures" / "spec_v2_minimal.json"
CAT = Path(__file__).parents[2] / "fixtures" / "cat.jpg"


class TestImport:
    def test_an_import_is_the_typed_row_the_cast_row_and_the_greeting(self, app: App) -> None:
        app.play(f"/card {SERAPHINA}")
        rows = app.store.stories.get_messages(app.story_id)
        assert [(m.role, m.kind) for m in rows] == [("user", "card"), ("assistant", "dialogue")]
        row, greeting = rows
        # The row is the line as typed — the block composes at wire time.
        assert row.body == f"/card {SERAPHINA}" and row.template is None
        (character,) = app.store.characters.list(app.story_id)
        assert character.name == "Seraphina"
        # Made by the card, no model: the file it came from is in its profile.
        assert (character.provider, character.model) == ("card", None)
        # Both rows carry the speaker link; the card row's is the compose key.
        assert (row.speaker, row.speaker_id) == ("Seraphina", character.id)
        assert (greeting.speaker, greeting.speaker_id) == ("Seraphina", character.id)
        # Authored, not generated — the source spelled the way generated
        # rows spell theirs.
        assert (greeting.provider, greeting.model) == ("card", "seraphina.png")
        assert "{{" not in greeting.body  # macros bound

    def test_the_profile_records_the_persona_and_the_row_the_name(self, app: App) -> None:
        app.play(f"/card {SERAPHINA} Warden")
        (character,) = app.store.characters.list(app.story_id)
        data = tomllib.loads(character.profile)
        assert (character.name, data["user"]) == ("Warden", "you")
        assert "name" not in data  # the row keeps it, and a rename changes the row
        assert data["file_name"] == "seraphina.png"

    def test_a_bare_picture_is_refused_here_and_the_codex_left_alone(self, app: App) -> None:
        # A picture alone makes a card in All characters only.
        with pytest.raises(Refused):
            app.api.stories.cards.create(
                app.ensure_story(), data=CAT.read_bytes(), file_name="cat.jpg"
            )
        assert app.api.cards.list() == []

    def test_a_card_without_a_greeting_imports_the_block_alone(self, app: App) -> None:
        app.play(f"/card {MINIMAL}")
        rows = app.store.stories.get_messages(app.story_id)
        assert [m.kind for m in rows] == ["card"]

    def test_a_duplicate_name_is_refused_untouched(self, app: App) -> None:
        app.play(f"/card {SERAPHINA}")
        before = len(app.store.stories.get_messages(app.story_id))
        app.play(f"/card {SERAPHINA}")
        assert len(app.store.stories.get_messages(app.story_id)) == before
        assert len(app.store.characters.list(app.story_id)) == 1

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
        (character,) = app.store.characters.list(app.story_id)
        edited = character.profile.replace("Seraphina's Personality", "Seraphina's Nature")
        app.store.characters.set_profile(character.id, edited)
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
        assert app.store.stories.get_messages(app.story_id) == []
        assert app.store.characters.list(app.story_id) == []
        app.play(f"/card {SERAPHINA}")
        assert len(app.store.characters.list(app.story_id)) == 1

    def test_undo_keeps_a_character_the_story_journaled(self, app: App) -> None:
        # A journal marks them played — extraction wove them into the
        # story — so the cast row outlives the import rows it arrived on.
        app.play(f"/card {SERAPHINA}")
        story_id = app.story_id
        (character,) = app.store.characters.list(story_id)
        ids = app.store.stories.get_messages_ids(story_id)
        scene_id = app.store.scenes.add(
            story_id, start_message_id=ids[0], end_message_id=ids[-1], summary="She woke."
        )
        app.store.journals.add(story_id, scene_id, character.id, entry="Woke them.", state="warm")
        app.play("/undo")
        assert app.store.stories.get_messages(story_id) == []
        assert len(app.store.characters.list(story_id)) == 1


class TestThroughTheCodex:
    """A card file brought into a story goes into All characters first:
    the story's character is a copy of the codex card, linked to it, and
    a file the codex holds already is the same card again."""

    def test_an_import_keeps_the_card_in_the_codex_linked(self, app: App) -> None:
        app.play(f"/card {SERAPHINA}")
        (card,) = app.api.cards.list()
        (character,) = app.store.characters.list(app.story_id)
        assert (card.name, card.story_count) == ("Seraphina", 1)
        assert character.card_id == card.id
        assert len(card.pictures) == 1  # the PNG's picture, kept by the codex

    def test_a_charx_brings_every_picture_into_the_codex_each_of_its_kind(self, app: App) -> None:
        story = app.ensure_story()
        brought = app.api.stories.cards.create(
            story,
            data=_charx(),
            file_name="ann.charx",
            name="Ann",
            persona="you",
            expected_head=None,
        ).value
        assert brought.card_id is not None
        stored = app.store.cards.get(brought.card_id)
        assert stored is not None and stored.original_file is not None
        assert [(picture.kind.value, picture.name) for picture in stored.pictures] == [
            ("portrait", ""),
            ("expression", "joy"),
        ]

    def test_the_same_file_in_two_stories_is_one_card(self, app: App) -> None:
        app.play(f"/card {SERAPHINA}")
        other = app.api.stories.create(title="Another").value.id
        brought = app.api.stories.cards.create(
            other,
            data=SERAPHINA.read_bytes(),
            file_name="seraphina.png",
            name="Seraphina",
            persona="you",
            expected_head=None,
        ).value
        (card,) = app.api.cards.list()
        assert brought.card_id == card.id and card.story_count == 2

    def test_a_moved_head_writes_nothing_not_even_the_codex(self, app: App) -> None:
        story = app.ensure_story()
        with pytest.raises(HeadMoved):
            app.api.stories.cards.create(
                story,
                data=SERAPHINA.read_bytes(),
                file_name="s.png",
                name="Seraphina",
                persona="you",
                expected_head=7,
            )
        assert app.api.cards.list() == []

    def test_the_check_says_what_the_codex_holds(self, app: App) -> None:
        held = app.api.cards.create(data=SERAPHINA.read_bytes(), file_name="s.png").value.card
        story = app.ensure_story()
        check = app.api.stories.cards.create(
            story, data=SERAPHINA.read_bytes(), file_name="s.png", validate_only=True
        )
        assert check.same_card is not None and check.same_card.id == held.id
        assert check.has_picture

    def test_the_file_the_reader_says_a_card_is_brings_that_card_in(self, app: App) -> None:
        echo = app.api.cards.create(name="Echo", description="the codex's own").value
        story = app.ensure_story()
        brought = app.api.stories.cards.create(
            story,
            data=MINIMAL.read_bytes(),
            file_name="echo.json",
            reuse=echo.id,
            name="Echo",
            persona="you",
            expected_head=None,
        ).value
        assert brought.card_id == echo.id
        (character,) = app.store.characters.list(story)
        assert tomllib.loads(character.profile)["description"] == "the codex's own"

    def test_the_check_shows_the_files_picture_small(self, app: App) -> None:
        check = app.api.stories.cards.create(
            app.ensure_story(), data=SERAPHINA.read_bytes(), file_name="s.png", validate_only=True
        )
        assert check.thumbnail is not None and check.thumbnail.startswith(b"\xff\xd8")  # a JPEG

    def test_a_card_made_on_the_way_takes_the_tagline_sent(self, app: App) -> None:
        app.api.stories.cards.create(
            app.ensure_story(),
            data=MINIMAL.read_bytes(),
            file_name="echo.json",
            name="Echo",
            tagline="A voice in the hills.",
            persona="you",
            expected_head=None,
        )
        (card,) = app.api.cards.list()
        assert card.tagline == "A voice in the hills."

    def test_a_file_kept_in_the_story_alone_leaves_the_codex_untouched(self, app: App) -> None:
        story = app.ensure_story()
        brought = app.api.stories.cards.create(
            story,
            data=SERAPHINA.read_bytes(),
            file_name="s.png",
            name="Seraphina",
            persona="you",
            with_pictures=True,
            story_only=True,
            expected_head=None,
        ).value
        assert brought.card_id is None
        assert app.api.cards.list() == []
        (character,) = app.store.characters.list(story)
        assert character.card_id is None
        (portrait,) = app.store.stories.get_messages(story)[0].attachments
        assert portrait.file.startswith("pic-")  # a picture of the story's own


class TestTheName:
    """The name a card's character is brought in under: the one the
    import carries, which a check vets where it is given and answers as
    the card's own where it is not; a blank one is refused, nothing
    written. The row names the file, and the name where it is not the
    card's own."""

    def test_a_check_answers_the_cards_own_name_or_vets_the_one_given(self, app: App) -> None:
        story = app.ensure_story()
        file = {"data": SERAPHINA.read_bytes(), "file_name": "s.png"}
        assert app.api.stories.cards.create(story, **file, validate_only=True).name == "Seraphina"
        named = app.api.stories.cards.create(story, **file, name="Sera", validate_only=True)
        assert named.name == "Sera"
        with pytest.raises(Refused):
            app.api.stories.cards.create(story, **file, name="  ", validate_only=True)

    def test_a_blank_name_is_refused_and_nothing_written(self, app: App) -> None:
        story = app.ensure_story()
        with pytest.raises(Refused):
            app.api.stories.cards.create(
                story,
                data=SERAPHINA.read_bytes(),
                file_name="s.png",
                name=" ",
                persona="you",
                expected_head=None,
            )
        assert (app.store.characters.list(story), app.api.cards.list()) == ([], [])
        assert app.store.stories.get_messages(story) == []

    def test_the_row_names_the_character_where_it_is_not_the_cards_own(self, app: App) -> None:
        own, other = app.ensure_story(), app.api.stories.create(title="Another").value.id
        for story, name in ((own, "Seraphina"), (other, "Sera")):
            app.api.stories.cards.create(
                story,
                data=SERAPHINA.read_bytes(),
                file_name="s.png",
                name=name,
                persona="you",
                expected_head=None,
            )
        assert app.store.stories.get_messages(own)[0].body == "/card s.png"
        assert app.store.stories.get_messages(other)[0].body == "/card s.png Sera"
        (card,) = app.api.cards.list()
        assert card.name == "Seraphina"  # the codex keeps the card's own


class TestFromALink:
    """A card fetched from a link — a file's own, on a site of the
    test's — read for the story and brought in as a file is, the row
    recording the link."""

    @pytest.fixture
    def site(self) -> Iterator[Site]:
        with serving() as site:
            yield site

    def test_a_links_card_is_read_then_brought_in(self, app: App, site: Site) -> None:
        url = site.serve("/seraphina.png", SERAPHINA.read_bytes(), "image/png")
        story = app.ensure_story()
        check = app.api.stories.cards.fetches.create(story, url=url, validate_only=True)
        assert (check.name, check.data) == ("Seraphina", SERAPHINA.read_bytes())
        assert app.store.characters.list(story) == []
        brought = app.api.stories.cards.fetches.create(
            story, url=url, name="Sera", persona="you", expected_head=None
        ).value
        assert brought.line.body == f"/card {url} Sera"
        (character,) = app.store.characters.list(story)
        assert (character.name, character.card_id) == ("Sera", brought.card_id)

    def test_a_link_to_nothing_is_refused_and_nothing_written(self, app: App, site: Site) -> None:
        story = app.ensure_story()
        with pytest.raises(Refused):
            app.api.stories.cards.fetches.create(
                story, url=site.url("/gone"), name="X", persona="you", expected_head=None
            )
        assert app.store.stories.get_messages(story) == []


class TestInvite:
    """A codex card brought into a story as it stands: the typed row, a
    character holding a copy of its fields, linked to it, its greeting."""

    def test_an_invite_is_the_row_the_linked_character_and_the_greeting(self, app: App) -> None:
        card = app.api.cards.create(data=SERAPHINA.read_bytes(), file_name="s.png").value.card
        story = app.ensure_story()
        brought = app.api.stories.cards.create(
            story, card_id=card.id, name=card.name, persona="Alex", expected_head=None
        ).value
        rows = app.store.stories.get_messages(story)
        assert [(m.role, m.kind) for m in rows] == [("user", "card"), ("assistant", "dialogue")]
        assert rows[0].body == "/card Seraphina"
        (character,) = app.store.characters.list(story)
        assert (brought.card_id, character.card_id) == (card.id, card.id)
        data = tomllib.loads(character.profile)
        assert (character.name, data["user"]) == ("Seraphina", "Alex")
        assert "{{" not in rows[1].body  # the greeting bound

    def test_the_copy_is_the_storys_own(self, app: App) -> None:
        card = app.api.cards.create(name="Mira", description="before").value
        story = app.ensure_story()
        app.api.stories.cards.create(
            story, card_id=card.id, name=card.name, persona="you", expected_head=None
        )
        app.api.cards.update(card.id, description="after")
        (character,) = app.store.characters.list(story)
        assert tomllib.loads(character.profile)["description"] == "before"

    def test_a_card_in_the_story_already_is_refused(self, app: App) -> None:
        card = app.api.cards.create(name="Mira", description="A thief.").value
        story = app.ensure_story()
        app.api.stories.cards.create(
            story, card_id=card.id, name=card.name, persona="you", expected_head=None
        )
        with pytest.raises(Refused):
            app.api.stories.cards.create(
                story, card_id=card.id, name="Mira Two", persona="you", expected_head=app.head()
            )
        assert len(app.store.characters.list(story)) == 1

    def test_the_portrait_rides_the_card_row_where_asked(self, app: App) -> None:
        card = app.api.cards.create(data=SERAPHINA.read_bytes(), file_name="s.png").value.card
        story = app.ensure_story()
        app.api.stories.cards.create(
            story,
            card_id=card.id,
            name=card.name,
            persona="you",
            with_pictures=True,
            expected_head=None,
        )
        row = app.store.stories.get_messages(story)[0]
        # the portrait as a turn's picture: its file and measure
        assert row.attachments == tuple(Attachment.of(p) for p in card.pictures[:1])

    def test_no_portrait_rides_unasked(self, app: App) -> None:
        card = app.api.cards.create(data=SERAPHINA.read_bytes(), file_name="s.png").value.card
        story = app.ensure_story()
        app.api.stories.cards.create(
            story, card_id=card.id, name=card.name, persona="you", expected_head=None
        )
        assert app.store.stories.get_messages(story)[0].attachments == ()

    def test_a_card_with_no_text_yet_is_refused_and_nothing_written(self, app: App) -> None:
        bare = app.api.cards.create(data=CAT.read_bytes(), file_name="cat.jpg").value.card
        story = app.ensure_story()
        with pytest.raises(Refused):
            app.api.stories.cards.create(
                story, card_id=bare.id, name=NEW_CARD_NAME, persona="you", expected_head=None
            )
        assert app.store.stories.get_messages(story) == []
        with pytest.raises(Refused):
            app.api.stories.create(card_ids=(bare.id,), persona="you")

    def test_undo_takes_the_character_and_leaves_the_card(self, app: App) -> None:
        card = app.api.cards.create(name="Mira", greeting="Hello.").value
        story = app.ensure_story()
        app.api.stories.cards.create(
            story, card_id=card.id, name=card.name, persona="you", expected_head=None
        )
        app.api.stories.undos.create(story, expected_head=app.head())
        assert app.store.characters.list(story) == []
        assert [row.id for row in app.api.cards.list()] == [card.id]


class TestTheStorysCopy:
    """A story character's copy of its card's profile, read field by field
    and corrected a field at a time — the persona it binds kept — and,
    where it no longer reads, mended whole."""

    def test_the_copy_reads_field_by_field(self, app: App) -> None:
        story, character = _invited(app, description="keeps the light", reminder="short")
        fields = app.api.stories.characters.get(story, character).profile_fields
        assert fields is not None
        assert (fields.description, fields.reminder, fields.scenario) == (
            "keeps the light",
            "short",
            "",
        )

    def test_a_field_corrected_keeps_the_rest_and_the_persona(self, app: App) -> None:
        story, character = _invited(app, description="keeps the light", reminder="short")
        changed = app.api.stories.characters.update(
            story, character, profile_fields={"scenario": "a lighthouse"}
        ).value
        assert changed.profile_fields is not None
        assert (changed.profile_fields.scenario, changed.profile_fields.description) == (
            "a lighthouse",
            "keeps the light",
        )
        stored = tomllib.loads(app.store.characters.list(story)[0].profile or "")
        assert (stored["user"], stored["scenario"]) == ("Alex", "a lighthouse")

    def test_what_a_card_has_not_or_a_depth_below_one_is_refused(self, app: App) -> None:
        story, character = _invited(app, description="keeps the light")
        before = app.store.characters.list(story)[0].profile
        for changes in ({"colour": "grey"}, {"reminder_depth": 0}):
            with pytest.raises(Refused):
                app.api.stories.characters.update(story, character, profile_fields=changes)
        assert app.store.characters.list(story)[0].profile == before

    def test_a_character_no_card_brought_has_no_fields_to_correct(self, app: App) -> None:
        story = app.ensure_story()
        keeper = app.store.characters.add(story, "Keeper")
        assert app.api.stories.characters.get(story, keeper).profile_fields is None
        with pytest.raises(Refused):
            app.api.stories.characters.update(story, keeper, profile_fields={"scenario": "x"})

    def test_a_profile_that_no_longer_reads_is_mended_whole(self, app: App) -> None:
        story, character = _invited(app, description="keeps the light")
        app.store.characters.set_profile(character, "description = = 'broken'")
        assert app.api.stories.characters.get(story, character).profile_fields is None
        with pytest.raises(Refused):
            app.api.stories.characters.update(story, character, profile_fields={"scenario": "x"})
        with pytest.raises(Refused):
            app.api.stories.characters.update(story, character, profile="still = = broken")
        mended = app.api.stories.characters.update(
            story, character, profile="user = 'Alex'\n\ndescription = 'mended'"
        ).value
        assert mended.profile_fields is not None and mended.profile_fields.description == "mended"


class TestThePortrait:
    """Whether a card's picture rides the row that brought its character
    in: switched on, the row carries the card's portrait; off, nothing."""

    def test_the_portrait_rides_the_card_row_when_asked(self, app: App) -> None:
        card = app.api.cards.create(name="Mira", description="A thief.").value.id
        app.api.cards.pictures.create(card, file=RawFile(CAT.read_bytes(), "cat.jpg"))
        portrait = app.api.cards.get(card).pictures[0]
        story = app.ensure_story()
        app.api.stories.cards.create(
            story, card_id=card, name="Mira", persona="you", expected_head=None
        )
        (character,) = app.api.stories.characters.list(story)
        assert (character.portrait_sent, character.card_portrait) == (False, portrait)
        on = app.api.stories.characters.update(story, character.id, portrait_sent=True).value
        assert on.portrait_sent is True
        assert _card_row(app, story).attachments == (Attachment.of(portrait),)
        off = app.api.stories.characters.update(story, character.id, portrait_sent=False).value
        assert off.portrait_sent is False
        assert _card_row(app, story).attachments == ()

    def test_a_card_with_no_picture_has_none_to_send(self, app: App) -> None:
        story, character = _invited(app)
        with pytest.raises(Refused):
            app.api.stories.characters.update(story, character, portrait_sent=True)

    def test_a_character_no_card_row_brought_has_no_portrait_to_send(self, app: App) -> None:
        story = app.ensure_story()
        keeper = app.store.characters.add(story, "Keeper")
        assert app.api.stories.characters.get(story, keeper).portrait_sent is None
        with pytest.raises(Refused):
            app.api.stories.characters.update(story, keeper, portrait_sent=False)


class TestWhatACardInjects:
    def test_the_system_prompt_joins_the_system_message(self, app: App) -> None:
        card = app.api.cards.create(name="Wren", system_prompt="{{char}} never lies to {{user}}.")
        story = app.ensure_story()
        app.api.stories.cards.create(
            story, card_id=card.value.id, name=card.value.name, persona="Alex", expected_head=None
        )
        app.play("I knock.")
        (system, *_) = app.server.requests[-1]["messages"]
        assert system["role"] == "system"
        assert str(system["content"]).endswith("Wren never lies to Alex.")

    def test_the_reminder_rides_before_the_readers_message_at_its_depth(self, app: App) -> None:
        card = app.api.cards.create(
            name="Wren", reminder="{{char}} answers in riddles.", reminder_depth=2
        )
        story = app.ensure_story()
        app.api.stories.cards.create(
            story, card_id=card.value.id, name=card.value.name, persona="Alex", expected_head=None
        )
        app.play("I knock.")
        app.play("I knock again.")
        texts = [str(m["content"]) for m in app.server.requests[-1]["messages"]]
        newest = next(text for text in texts if "I knock again." in text)
        before = next(text for text in texts if "I knock." in text)
        assert "Wren answers in riddles." not in newest
        assert before.index("Wren answers in riddles.") < before.index("I knock.")

    def test_the_storys_copy_is_read_at_request_time(self, app: App) -> None:
        card = app.api.cards.create(name="Wren", reminder="Riddles.", reminder_depth=1)
        story = app.ensure_story()
        app.api.stories.cards.create(
            story, card_id=card.value.id, name=card.value.name, persona="Alex", expected_head=None
        )
        app.api.cards.update(card.value.id, reminder="Songs.")  # the codex's, not the story's
        (character,) = app.store.characters.list(story)
        app.store.characters.set_profile(
            character.id, character.profile.replace("Riddles.", "Rhymes.")
        )
        app.play("I knock.")
        wire = "\n".join(str(m["content"]) for m in app.server.requests[-1]["messages"])
        assert "Rhymes." in wire
        assert "Riddles." not in wire and "Songs." not in wire

    def test_undo_takes_them_with_the_card(self, app: App) -> None:
        card = app.api.cards.create(name="Wren", system_prompt="Never lie.", reminder="Riddles.")
        story = app.ensure_story()
        app.api.stories.cards.create(
            story, card_id=card.value.id, name=card.value.name, persona="Alex", expected_head=None
        )
        app.api.stories.undos.create(story, expected_head=app.head())
        app.play("I knock.")
        wire = "\n".join(str(m["content"]) for m in app.server.requests[-1]["messages"])
        assert "Never lie." not in wire and "Riddles." not in wire

    def test_the_preview_and_the_warm_up_carry_what_the_turn_sends(self, app: App) -> None:
        card = app.api.cards.create(name="Wren", system_prompt="Never lie.", reminder="Riddles.")
        story = app.ensure_story()
        app.api.stories.cards.create(
            story, card_id=card.value.id, name=card.value.name, persona="Alex", expected_head=None
        )
        app.play("I knock.")
        turn = request.assemble(app.backend, story, None).injections
        job = request.build_job(app.backend, story).injections
        assert [(i.owner, i.position) for i in turn] == [(i.owner, i.position) for i in job]
        assert {i.text for i in job} == {"Never lie.", "Riddles."}
        report = app.api.stories.context.get(story)
        assert len(report.injections) == 2
        (sent,) = report.cards
        assert sent.name == "Wren" and sent.tokens > 0


class TestExtraction:
    def test_a_story_of_one_card_extracts_nothing(self, app: App) -> None:
        # A card is not story: alone (this fixture has no greeting) there
        # is nothing to close a scene over, however many characters of
        # text the block holds — even forced.
        app.play(f"/card {MINIMAL}")
        app.play("/extract")
        assert (
            app.store.scenes.get_current(
                app.story_id, app.store.stories.get_messages_ids(app.story_id)
            )
            == []
        )

    def test_a_forced_pass_summarizes_around_the_card(self, app: App) -> None:
        app.play(f"/card {SERAPHINA}")
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.play("/extract")
        story_id = app.story_id
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


def _invited(app: App, **fields: str) -> tuple[int, int]:
    """A codex card with `fields`, invited into a new story for Alex; the
    story and the character it became."""
    card = app.api.cards.create(name="Mira", **{"description": "A thief.", **fields}).value.id
    story = app.ensure_story()
    brought = app.api.stories.cards.create(
        story, card_id=card, name="Mira", persona="Alex", expected_head=None
    ).value
    return story, brought.character_id


def _card_row(app: App, story: int):
    """The story's card row."""
    return next(m for m in app.store.stories.get_messages(story) if m.kind == "card")


def _charx() -> bytes:
    """A V3 card in a CHARX, written: its main icon and an expression."""
    assets = [
        {"type": "icon", "name": "main", "uri": "embeded://assets/icon/main.png", "ext": "png"},
        {"type": "emotion", "name": "joy", "uri": "embeded://assets/emotion/joy.png", "ext": "png"},
    ]
    text = {
        "spec": "chara_card_v3",
        "spec_version": "3.0",
        "data": {"name": "Ann", "description": "A cook.", "assets": assets},
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("card.json", json.dumps(text))
        for path, color in (
            ("assets/icon/main.png", (200, 0, 0)),
            ("assets/emotion/joy.png", (0, 200, 0)),
        ):
            picture = io.BytesIO()
            Image.new("RGB", (16, 16), color).save(picture, "PNG")
            archive.writestr(path, picture.getvalue())
    return out.getvalue()
