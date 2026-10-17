"""A message's pictures: what the model gets, what the store keeps, and
the refusals — a picture rides only while the model can see.
"""

import contextlib
from collections.abc import Iterator
from pathlib import Path

import pytest

from otaku.backend.api import (
    ModelRef,
    Refused,
)
from otaku.backend.api.story.messages import (
    RawFile,
)
from scenarios.support import server as scripted
from scenarios.support.harness import App, launch, set_config_provider

CAT = Path(__file__).parents[2] / "fixtures" / "cat.jpg"


class TestPictures:
    """A turn with pictures: what the model gets, what the store keeps,
    and the refusals — a picture rides only while the model can see."""

    def test_a_picture_rides_its_turn_as_a_part_after_the_text(self, tmp_path) -> None:
        with _seeing(tmp_path) as app:
            _play(app, "What is this?", [_cat()])
            content = app.server.requests[-1]["messages"][-1]["content"]
            assert content[0] == {"type": "text", "text": "What is this?"}
            assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")

    def test_the_turn_records_its_picture_and_the_folder_holds_it(self, tmp_path) -> None:
        with _seeing(tmp_path) as app:
            _play(app, "What is this?", [_cat()])
            turn, _reply = app.store.stories.get_messages(app.story_id)
            (picture,) = turn.attachments
            assert turn.body == "What is this?"
            assert picture.file.endswith(".jpg")
            assert max(picture.width, picture.height) <= 1568
            assert app.store.files.get(picture.file) is not None
            assert app.store.files.get_thumb(picture.file) is not None

    def test_every_verbatim_turn_keeps_its_picture_on_the_wire(self, tmp_path) -> None:
        with _seeing(tmp_path) as app:
            _play(app, "What is this?", [_cat()])
            app.play("And now?")
            messages = app.server.requests[-1]["messages"]
            assert [part["type"] for part in messages[0]["content"]] == ["text", "image_url"]
            assert messages[-1]["content"] == "And now?"

    def test_regenerate_sends_the_picture_again(self, tmp_path) -> None:
        with _seeing(tmp_path) as app:
            _play(app, "What is this?", [_cat()])
            list(app.api.stories.regens.create(app.story_id, expected_head=app.head(app.story_id)))
            content = app.server.requests[-1]["messages"][-1]["content"]
            assert content[1]["type"] == "image_url"

    def test_a_line_may_be_pictures_alone(self, tmp_path) -> None:
        with _seeing(tmp_path) as app:
            _play(app, "", [_cat()])
            content = app.server.requests[-1]["messages"][-1]["content"]
            assert [part["type"] for part in content] == ["image_url"]
            turn, _reply = app.store.stories.get_messages(app.story_id)
            assert turn.body == "" and len(turn.attachments) == 1

    def test_the_terminal_resolves_an_at_path_and_keeps_it_out_of_the_body(self, tmp_path) -> None:
        with _seeing(tmp_path) as app:
            app.play(f"What is this? @{CAT}")
            turn, _reply = app.store.stories.get_messages(app.story_id)
            assert turn.body == "What is this?"
            assert len(turn.attachments) == 1
            content = app.server.requests[-1]["messages"][-1]["content"]
            assert content[0] == {"type": "text", "text": "What is this?"}

    def test_a_story_not_made_yet_takes_a_picture_where_the_model_sees(self, tmp_path) -> None:
        # what both frontends show the attach affordances off before a story exists
        with _seeing(tmp_path) as app:
            model = app.api.app.get().model
            assert model is not None and model.can_attach

    def test_a_story_not_made_yet_takes_none_where_the_model_cannot_see(self, app: App) -> None:
        model = app.api.app.get().model
        assert model is not None and not model.can_attach

    def test_a_model_that_cannot_see_is_refused_and_nothing_lands(self, app: App) -> None:
        with pytest.raises(Refused, match="cannot see"):
            app.api.stories.messages.create(
                app.ensure_story(), line="look", files=[_cat()], expected_head=app.head()
            )
        assert app.messages == []
        assert not (app.paths.database_dir / "files").exists()

    def test_too_many_pictures_are_refused(self, tmp_path) -> None:
        with _seeing(tmp_path) as app:
            with pytest.raises(Refused, match="At most 8"):
                app.api.stories.messages.create(
                    app.ensure_story(),
                    line="look",
                    files=[_cat() for _ in range(9)],
                    expected_head=app.head(),
                )
            assert app.messages == []

    def test_a_file_that_is_no_picture_is_refused_before_anything_lands(self, tmp_path) -> None:
        with _seeing(tmp_path) as app:
            with pytest.raises(Refused, match="cannot accept"):
                app.api.stories.messages.create(
                    app.ensure_story(),
                    line="look",
                    files=[_cat(), RawFile(b"not one", "x.jpg")],
                    expected_head=app.head(),
                )
            assert app.messages == []
            assert not (app.paths.database_dir / "files").exists()

    def test_the_context_preview_counts_the_pictures_riding_each_message(self, tmp_path) -> None:
        with _seeing(tmp_path) as app:
            _play(app, "What is this?", [_cat()])
            report = app.api.stories.context.get(app.story_id)
            assert [part.pictures for part in report.parts] == [1, 0]
            # The preview IS the wire: the marker beside the role is the
            # count the request carries, so asserting it is asserting
            # the payload.
            assert "[user · 1 picture attached]" in report.text()
            assert "1 picture riding" in report.summary

    def test_an_engine_that_gathers_pictures_gets_the_latest_turns_alone(self, tmp_path) -> None:
        # omlx puts every picture in a request on the latest prompt, so
        # a second turn's picture would arrive stacked with the first's:
        # the earlier one is held back, and the preview says so.
        with _seeing_omlx(tmp_path) as app:
            _play(app, "What is this?", [_cat()])
            _play(app, "And this?", [_cat()])
            messages = app.server.requests[-1]["messages"]
            assert messages[0]["content"] == "What is this?"
            assert [part["type"] for part in messages[-1]["content"]] == ["text", "image_url"]
            report = app.api.stories.context.get(app.story_id)
            assert "held back" in report.summary
            assert [part.pictures for part in report.parts][-1] == 0  # the reply is newest now

    def test_a_model_that_cannot_see_gets_none_of_the_storys_pictures(self, tmp_path) -> None:
        with _seeing(tmp_path) as app:
            _play(app, "What is this?", [_cat()])
            app.api.app.update(model=ModelRef("generic", "test-model"))
            app.play("And now?")
            messages = app.server.requests[-1]["messages"]
            assert all(isinstance(message["content"], str) for message in messages)
            assert "not sent" in app.api.stories.context.get(app.story_id).summary


class TestCardPortrait:
    """A codex card's portrait, sent with the card where the reader asked:
    it rides the card row as a turn's picture rides its turn."""

    def test_the_portrait_reaches_a_model_that_sees(self, tmp_path) -> None:
        with _seeing(tmp_path) as app:
            card = app.api.cards.create(name="Mira").value
            app.api.cards.pictures.create(card.id, file=_cat())
            story = app.ensure_story()
            app.api.stories.cards.create(
                story, card_id=card.id, persona="you", with_pictures=True, expected_head=None
            )
            app.play("Who is she?")
            sent = [
                part
                for message in app.server.requests[-1]["messages"]
                if isinstance(message["content"], list)
                for part in message["content"]
                if part.get("type") == "image_url"
            ]
            assert len(sent) == 1


def _cat() -> RawFile:
    return RawFile(CAT.read_bytes(), "cat.jpg")


@contextlib.contextmanager
def _seeing(tmp_path: Path) -> Iterator[App]:
    """A session on a model whose engine says it sees — Ollama's card,
    as a managed scripted server plays it; the generic provider stands
    beside it for a switch to a model that cannot."""
    server = scripted.ModelServer(managed=True)
    server.capabilities["test-model"] = ["completion", "vision"]
    try:
        set_config_provider(tmp_path / "state", server, name="ollama")
        app = launch(tmp_path / "state", server, spec="ollama/test-model")
        try:
            yield app
        finally:
            app.close()
    finally:
        server.close()


@contextlib.contextmanager
def _seeing_omlx(tmp_path: Path) -> Iterator[App]:
    """The same on omlx's status, which types the model "vlm"."""
    server = scripted.ModelServer()
    server.status = True
    server.types["test-model"] = "vlm"
    try:
        set_config_provider(tmp_path / "state", server, name="omlx")
        app = launch(tmp_path / "state", server, spec="omlx/test-model")
        try:
            yield app
        finally:
            app.close()
    finally:
        server.close()


def _play(app: App, line: str, files: list[RawFile]) -> None:
    list(
        app.api.stories.messages.create(
            app.ensure_story(), line=line, files=files, expected_head=app.head()
        )
    )
