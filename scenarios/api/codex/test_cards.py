"""All characters: the codex's cards — made by hand, from a card file, or
from a story's character; read, listed, corrected, given pictures,
merged and deleted — and the stories' characters no card claims.

A card is the codex's own: a story's character links to one and holds a
copy of its fields, so nothing here changes what a story sends. The
fixtures are SillyTavern's own bundled Seraphina (a real PNG card, its
picture inside) and a minimal JSON one, Echo.
"""

from pathlib import Path

import pytest

from otaku.backend.api import Refused
from otaku.backend.api.codex.cards import RawFile
from otaku.backend.domain.codex.cards import NEW_CARD_NAME
from otaku.store.schema import Message
from scenarios.support.harness import App

FIXTURES = Path(__file__).parents[2] / "fixtures"
SERAPHINA = FIXTURES / "seraphina.png"
ECHO = FIXTURES / "spec_v2_minimal.json"
CAT = FIXTURES / "cat.jpg"


class TestCards:
    def test_a_card_made_by_hand_reads_back_whole(self, app: App) -> None:
        made = app.api.cards.create(
            name="Mira",
            aliases=("the thief", " ", "Mira", "the thief"),
            tagline="Steals what was never hers",
            description="A thief of maps.",
            examples=("Mira: hi", "Mira: bye"),
        )
        card = app.api.cards.get(made.value.id)
        assert (card.name, card.tagline, card.description) == (
            "Mira",
            "Steals what was never hers",
            "A thief of maps.",
        )
        # trimmed, the empty and the name itself left out, each once
        assert card.aliases == ("the thief",)
        assert card.examples == ("Mira: hi", "Mira: bye")
        assert (card.file_name, card.pictures, card.stories) == ("", (), ())
        (row,) = app.api.cards.list()
        assert (row.id, row.story_count, row.pictures) == (card.id, 0, ())

    def test_a_card_made_with_nothing_takes_the_name_kept_for_one(self, app: App) -> None:
        # the page makes one so, and opens it on its name to be typed over
        assert app.api.cards.create().value.name == NEW_CARD_NAME

    def test_a_name_is_needed_and_a_tagline_is_one_line(self, app: App) -> None:
        with pytest.raises(Refused):
            app.api.cards.create(name="  ")
        with pytest.raises(Refused):
            app.api.cards.create(name="Mira", tagline="one\ntwo")
        assert app.api.cards.list() == []

    def test_a_reminders_depth_is_a_whole_number_from_one(self, app: App) -> None:
        with pytest.raises(Refused):
            app.api.cards.create(name="Mira", reminder_depth=0)
        card = app.api.cards.create(name="Mira", reminder="r").value
        assert card.reminder_depth == 2
        with pytest.raises(Refused):
            app.api.cards.update(card.id, reminder_depth=0)
        assert app.api.cards.get(card.id).reminder_depth == 2

    def test_the_list_is_the_readers_order_and_answers_a_query(self, app: App) -> None:
        # a card made goes last
        app.api.cards.create(name="zed")
        app.api.cards.create(name="Alma", aliases=("the river",))
        app.api.cards.create(name="Bo", tagline="keeps the river clean")
        assert [card.name for card in app.api.cards.list()] == ["zed", "Alma", "Bo"]
        # a name, an alias or the tagline, cased as the reader likes
        assert [card.name for card in app.api.cards.list(query="RIVER")] == ["Alma", "Bo"]

    def test_a_correction_replaces_the_parts_given_and_leaves_the_rest(self, app: App) -> None:
        card = app.api.cards.create(
            name="Mira", aliases=("the thief",), description="old", scenario="a city"
        ).value
        changed = app.api.cards.update(card.id, description="new", tagline="a thief").value
        assert (changed.description, changed.tagline, changed.scenario) == (
            "new",
            "a thief",
            "a city",
        )
        # renamed to an alias: the alias goes, the name never stands twice
        renamed = app.api.cards.update(card.id, name="the thief").value
        assert (renamed.name, renamed.aliases) == ("the thief", ())
        with pytest.raises(Refused):
            app.api.cards.update(card.id)  # nothing to change
        with pytest.raises(Refused):
            app.api.cards.update(card.id, name=" ")

    def test_a_card_deleted_leaves_its_story_characters_unlinked(self, app: App) -> None:
        story = app.ensure_story()
        character = app.store.characters.add(story, "Mira")
        card = app.api.cards.create(name="Mira").value
        app.api.stories.characters.update(story, character, card_id=card.id)
        app.api.cards.delete(card.id)
        assert app.api.cards.list() == []
        (kept,) = app.store.characters.list(story)
        assert (kept.name, kept.card_id) == ("Mira", None)
        with pytest.raises(Refused):
            app.api.cards.get(card.id)


class TestOrder:
    """The reader's own order of the codex: a card made goes last, and a
    card moves just before another, or last."""

    def test_a_card_goes_just_before_another_or_last(self, app: App) -> None:
        a, _, c = (app.api.cards.create(name=name).value.id for name in ("A", "B", "C"))
        app.api.cards.update(c, before=a)
        assert _names(app) == ["C", "A", "B"]
        app.api.cards.update(c, before=None)
        assert _names(app) == ["A", "B", "C"]
        app.api.cards.update(a, before=c)
        assert _names(app) == ["B", "A", "C"]

    def test_a_move_that_cannot_be_made_takes_the_correction_with_it(self, app: App) -> None:
        a = app.api.cards.create(name="A").value.id
        with pytest.raises(Refused):
            app.api.cards.update(a, tagline="first", before=a)  # before itself
        with pytest.raises(Refused):
            app.api.cards.update(a, tagline="first", before=999)  # no such card
        assert app.api.cards.get(a).tagline == ""

    def test_a_correction_and_a_move_land_together(self, app: App) -> None:
        a, b = (app.api.cards.create(name=name).value.id for name in ("A", "B"))
        app.api.cards.update(b, tagline="first", before=a)
        assert (_names(app), app.api.cards.get(b).tagline) == (["B", "A"], "first")

    def test_nothing_to_change_is_refused(self, app: App) -> None:
        a = app.api.cards.create(name="A").value.id
        with pytest.raises(Refused):
            app.api.cards.update(a)


class TestCardFiles:
    def test_a_png_card_becomes_a_card_with_its_portrait(self, app: App) -> None:
        imported = app.api.cards.create(data=SERAPHINA.read_bytes(), file_name="seraphina.png")
        assert imported.value.made
        card = imported.value.card
        assert (card.name, card.file_name) == ("Seraphina", "seraphina.png")
        assert card.description  # the card's own fields, macros as written
        (portrait,) = card.pictures
        assert portrait.file.startswith(f"crd-{card.id:04d}-")
        assert app.store.files.get(portrait.file) is not None
        # what was the card file's text is not in the picture kept
        data, _ = app.store.files.get(portrait.file)
        assert b"chara" not in data

    def test_the_same_file_again_makes_nothing(self, app: App) -> None:
        first = app.api.cards.create(data=SERAPHINA.read_bytes(), file_name="seraphina.png")
        again = app.api.cards.create(data=SERAPHINA.read_bytes(), file_name="sera-copy.png")
        assert not again.value.made
        assert again.value.card.id == first.value.card.id
        assert len(app.api.cards.list()) == 1

    def test_the_check_names_the_card_a_file_is_and_those_of_its_name(self, app: App) -> None:
        before = app.api.cards.create(
            data=SERAPHINA.read_bytes(), file_name="seraphina.png", validate_only=True
        )
        assert (before.name, before.has_picture, before.same_card, before.similar_cards) == (
            "Seraphina",
            True,
            None,
            (),
        )
        assert app.api.cards.list() == []  # vetted, nothing stored
        card = app.api.cards.create(data=SERAPHINA.read_bytes(), file_name="s.png").value.card
        after = app.api.cards.create(
            data=SERAPHINA.read_bytes(), file_name="s.png", validate_only=True
        )
        assert after.same_card is not None and after.same_card.id == card.id
        # another file under the same name: offered, never taken unasked
        other = app.api.cards.create(
            data=ECHO.read_bytes(), file_name="echo.json", rename="Seraphina", validate_only=True
        )
        assert other.same_card is None
        assert [row.id for row in other.similar_cards] == [card.id]

    def test_reuse_says_which_card_a_file_is(self, app: App) -> None:
        card = app.api.cards.create(name="Echo").value
        kept = app.api.cards.create(data=ECHO.read_bytes(), file_name="echo.json", reuse=card.id)
        assert not kept.value.made and kept.value.card.id == card.id
        assert len(app.api.cards.list()) == 1
        # the file is that card from now on
        check = app.api.cards.create(
            data=ECHO.read_bytes(), file_name="echo.json", validate_only=True
        )
        assert check.same_card is not None and check.same_card.id == card.id

    def test_a_json_card_has_no_picture(self, app: App) -> None:
        card = app.api.cards.create(data=ECHO.read_bytes(), file_name="echo.json").value.card
        assert (card.name, card.pictures) == ("Echo", ())

    def test_a_file_that_is_no_card_is_refused(self, app: App) -> None:
        with pytest.raises(Refused):
            app.api.cards.create(data=b"not a card", file_name="x.json")
        assert app.api.cards.list() == []


class TestPictures:
    def test_pictures_are_added_ordered_and_removed(self, app: App) -> None:
        card = app.api.cards.create(data=SERAPHINA.read_bytes(), file_name="s.png").value.card
        (portrait,) = card.pictures
        added = app.api.cards.pictures.create(card.id, file=RawFile(CAT.read_bytes(), "cat.jpg"))
        cat = added.value.pictures[1]  # after the others
        assert added.value.pictures[0] == portrait
        ordered = app.api.cards.update(card.id, pictures=(cat.file, portrait.file)).value
        assert [p.file for p in ordered.pictures] == [cat.file, portrait.file]
        # one removed by its name, and the folder lets it go
        kept = app.api.cards.pictures.delete(card.id, portrait.file).value
        assert kept.pictures == (cat,)
        assert app.store.files.get(portrait.file) is None

    def test_an_order_names_every_picture_once(self, app: App) -> None:
        card = app.api.cards.create(data=SERAPHINA.read_bytes(), file_name="s.png").value.card
        (portrait,) = card.pictures
        cat = app.api.cards.pictures.create(
            card.id, file=RawFile(CAT.read_bytes(), "cat.jpg")
        ).value.pictures[1]
        for order in ((cat.file,), (cat.file, cat.file), (cat.file, portrait.file, cat.file)):
            with pytest.raises(Refused):
                app.api.cards.update(card.id, pictures=order)
        assert app.api.cards.get(card.id).pictures == (portrait, cat)
        assert app.store.files.get(portrait.file) is not None

    def test_a_picture_the_card_lacks_cannot_be_removed(self, app: App) -> None:
        card = app.api.cards.create(name="Mira").value
        with pytest.raises(Refused):
            app.api.cards.pictures.delete(card.id, "crd-0001-20260101-deadbeef.jpg")

    def test_a_picture_not_the_cards_is_refused(self, app: App) -> None:
        card = app.api.cards.create(name="Mira").value
        with pytest.raises(Refused):
            app.api.cards.update(card.id, pictures=("crd-0001-20260101-deadbeef.jpg",))

    def test_the_same_picture_twice_is_refused(self, app: App) -> None:
        card = app.api.cards.create(name="Mira").value
        app.api.cards.pictures.create(card.id, file=RawFile(CAT.read_bytes(), "cat.jpg"))
        with pytest.raises(Refused):
            app.api.cards.pictures.create(card.id, file=RawFile(CAT.read_bytes(), "cat.jpg"))
        assert len(app.api.cards.get(card.id).pictures) == 1


class TestMerges:
    def test_a_card_merged_brings_its_links_its_name_and_its_pictures(self, app: App) -> None:
        river = app.ensure_story()
        tour = app.api.stories.create(title="Tour").value.id
        a = app.api.cards.create(name="Merin", aliases=("the ferryman",)).value
        app.api.cards.pictures.create(a.id, file=RawFile(CAT.read_bytes(), "cat.jpg"))
        b = app.api.cards.create(name="Tallis").value
        in_river = app.store.characters.add(river, "Tallis")
        in_tour = app.store.characters.add(tour, "Merin")
        app.api.stories.characters.update(river, in_river, card_id=b.id)
        app.api.stories.characters.update(tour, in_tour, card_id=a.id)
        merged = app.api.cards.merges.create(a.id, into=b.id).value
        assert merged.aliases == ("Merin", "the ferryman")
        assert len(merged.pictures) == 1
        assert {(row.story_id, row.character_id) for row in merged.stories} == {
            (river, in_river),
            (tour, in_tour),
        }
        assert [card.id for card in app.api.cards.list()] == [b.id]

    def test_two_cards_one_story_holds_both_of_are_refused(self, app: App) -> None:
        story = app.ensure_story()
        a = app.api.cards.create(name="A").value
        b = app.api.cards.create(name="B").value
        for name, card in (("A", a), ("B", b)):
            app.api.stories.characters.update(
                story, app.store.characters.add(story, name), card_id=card.id
            )
        with pytest.raises(Refused):
            app.api.cards.merges.create(a.id, into=b.id)
        assert len(app.api.cards.list()) == 2

    def test_a_card_into_itself_is_refused(self, app: App) -> None:
        card = app.api.cards.create(name="A").value
        with pytest.raises(Refused):
            app.api.cards.merges.create(card.id, into=card.id)


class TestPromote:
    def test_a_story_character_becomes_a_card_linked_to_it(self, app: App) -> None:
        story = app.ensure_story()
        character = app.store.characters.add(
            story, "Keeper", aliases=("the warden",), description="keeps the gate"
        )
        card = app.api.cards.create(story_id=story, character_id=character).value
        assert (card.name, card.aliases, card.description) == (
            "Keeper",
            ("the warden",),
            "keeps the gate",
        )
        assert app.store.characters.list(story)[0].card_id == card.id
        with pytest.raises(Refused):
            app.api.cards.create(story_id=story, character_id=character)  # linked already

    def test_an_imported_character_brings_its_cards_fields_and_not_its_binding(
        self, app: App
    ) -> None:
        app.play(f"/card {SERAPHINA}")
        story = app.story_id
        (character,) = app.store.characters.list(story)
        app.api.stories.characters.update(story, character.id, card_id=None)
        card = app.api.cards.create(story_id=story, character_id=character.id).value
        assert card.name == "Seraphina" and card.description
        assert card.file_name == "seraphina.png"

    def test_a_character_not_in_the_story_is_refused(self, app: App) -> None:
        story = app.ensure_story()
        with pytest.raises(Refused):
            app.api.cards.create(story_id=story, character_id=999)

    def test_a_promotion_takes_the_copies_in_forked_stories_when_asked(self, app: App) -> None:
        story = app.ensure_story()
        app.store.stories.append(story, Message(role="user", body="I enter."), after=None)
        keeper = app.store.characters.add(story, "Keeper", description="keeps the gate")
        fork = app.api.stories.forks.create(story).value.id
        again = app.api.stories.forks.create(fork).value.id
        vetted = app.api.cards.create(story_id=story, character_id=keeper, validate_only=True)
        assert vetted.copies == 2
        assert app.api.cards.list() == []  # vetted, nothing made
        card = app.api.cards.create(story_id=story, character_id=keeper, with_copies=True).value
        linked = [app.store.characters.list(each)[0].card_id for each in (story, fork, again)]
        assert linked == [card.id] * 3

    def test_a_promotion_keeps_the_storys_persona_out_of_the_codex(self, app: App) -> None:
        card = app.api.cards.create(name="Mira", description="the codex's").value
        story = app.ensure_story()
        app.api.stories.cards.create(story, card_id=card.id, persona="Alex", expected_head=None)
        (character,) = app.store.characters.list(story)
        app.api.stories.characters.update(story, character.id, card_id=None)
        made = app.api.cards.create(story_id=story, character_id=character.id).value
        assert "Alex" not in app.store.cards.get(made.id).profile
        assert made.description == "the codex's"


class TestUnlinked:
    def test_every_storys_unlinked_character_the_most_written_about_first(self, app: App) -> None:
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.play("/extract")  # the scripted analyst names the Keeper, journal and all
        story = app.story_id
        app.store.characters.add(story, "Bystander")
        linked = app.store.characters.add(story, "Mira")
        app.api.stories.characters.update(
            story, linked, card_id=app.api.cards.create(name="Mira").value.id
        )
        rows = app.api.cards.unlinked.list()
        assert [(row.name, row.journal_count) for row in rows] == [
            ("Keeper", 1),
            ("Bystander", 0),
        ]
        assert {row.story_id for row in rows} == {story}
        assert [row.name for row in app.api.cards.unlinked.list(query="BY")] == ["Bystander"]

    def test_the_query_searches_what_the_journals_hold(self, app: App) -> None:
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.play("/extract")  # the Keeper: "I saw the guest.", "at the gate"
        app.store.characters.add(app.story_id, "Bystander")
        for query in ("SAW THE GUEST", "the gate"):
            found = app.api.cards.unlinked.list(query=query)
            assert [row.name for row in found] == ["Keeper"], query
        assert not app.api.cards.unlinked.list(query="the moon")


def _names(app: App) -> list[str]:
    return [card.name for card in app.api.cards.list()]
