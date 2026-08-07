"""Import and export: files round-trip through the app.

The chapel fixture is deliberately Cyrillic: importing it proves non-Latin
content survives the whole path — parse, sealed store, session, export —
byte for byte. The SillyTavern and plain-text imports arrive memoryless, so
each triggers the same forced extraction pass live play gets.
"""

import json
from pathlib import Path

from otaku.transfer.exports import read_story
from otaku.transfer.imports import parse_story
from scenarios.support import server as scripted
from scenarios.support.harness import RULE, App

CHAPEL = Path(__file__).parent.parent / "fixtures" / "chapel.md"

# fmt: off
TAVERN_LINES = (
    {"user_name": "User", "character_name": "Elara", "create_date": "2026-05-01@12h00m00s"},
    {"name": "Elara", "is_user": False, "is_system": False, "mes": "Come in from the rain."},
    {"name": "User", "is_user": True, "is_system": False, "mes": "I step in and lower my hood."},
    {"name": "Elara", "is_user": False, "is_system": True, "mes": "(a hidden note)"},
    {"name": "Elara", "is_user": False, "is_system": False, "mes": "Welcome, traveler."},
)
# fmt: on

PROSE = """The chapel stood silent at the edge of the marsh, its windows dark.

"Who goes there?" the Keeper called from within. The heavy door creaked
on its hinges.
"""


class TestImport:
    def test_a_full_export_imports_verbatim_without_model_calls(self, app: App, capsys) -> None:
        app.play(f"/import {CHAPEL}")
        out = capsys.readouterr().out
        assert "1 scene(s) applied verbatim" in out
        assert "\x1b[48;2;240;240;240m" in out  # the scene echo: last turns, banded
        assert out.index(RULE) < out.index("\x1b[48;2;240;240;240m")  # the scene is fenced off
        # A native export carries its extraction state: no pass runs, no
        # model is called — the story arrives exactly as it was.
        assert app.server.requests == []

        # The session switched onto the imported story, memory and all.
        story_id = app.session.story_id
        assert len(app.session.messages) == 4
        assert app.session.system.startswith("Ты — рассказчик")
        cast = app.store.characters.list(story_id)
        assert [c.name for c in cast] == ["Кассиан", "Элоиза"]

    def test_the_reimported_story_equals_the_file(self, app: App, tmp_path: Path) -> None:
        app.play(f"/import {CHAPEL}")
        assert read_story(app.store, app.session.story_id) == parse_story(CHAPEL.read_text())

    def test_a_missing_file_imports_nothing(self, app: App, capsys) -> None:
        app.play(f"/import {Path('/nowhere') / 'gone.md'}")
        assert capsys.readouterr().out.strip()  # refused out loud
        assert app.session.story_id is None
        assert app.server.requests == []

    def test_a_bare_import_shows_usage(self, app: App, capsys) -> None:
        app.play("/import")
        assert "Usage: /import FILE" in capsys.readouterr().out

    def test_a_leading_at_is_the_completion_trigger_not_the_name(
        self, app: App, capsys, tmp_path: Path
    ) -> None:
        tale = tmp_path / "tale.txt"
        tale.write_text("First beat.", encoding="utf-8")
        app.play(f"/import @{tale}")
        assert "Imported 1 message(s)" in capsys.readouterr().out

    def test_a_broken_export_is_refused_not_read_as_prose(
        self, app: App, capsys, tmp_path: Path
    ) -> None:
        # The marker claims the native format; a failed parse must refuse,
        # never degrade the scaffolding into a plain-text story.
        broken = tmp_path / "broken.md"
        broken.write_text("<!-- otaku export -->\nno structure here")
        app.play(f"/import {broken}")
        assert "to import" in capsys.readouterr().out  # refused, not imported
        assert app.session.story_id is None
        assert app.server.requests == []

    def test_json_that_is_not_a_tavern_chat_is_refused(
        self, app: App, capsys, tmp_path: Path
    ) -> None:
        data = tmp_path / "data.jsonl"
        data.write_text('{"hello": "world"}\n')
        app.play(f"/import {data}")
        assert "not a SillyTavern chat" in capsys.readouterr().out
        assert app.session.story_id is None
        assert app.server.requests == []

    def test_an_unknown_format_is_refused(self, app: App, capsys, tmp_path: Path) -> None:
        # The file's name and contents must agree: an unknown extension,
        # or a .md without the export marker, match nothing.
        stray = tmp_path / "story.doc"
        stray.write_text("Some prose.")
        app.play(f"/import {stray}")
        assert "Cannot detect file format" in capsys.readouterr().out
        markerless = tmp_path / "story.md"
        markerless.write_text("Just markdown prose.")
        app.play(f"/import {markerless}")
        assert "Cannot detect file format" in capsys.readouterr().out
        assert app.session.story_id is None

    def test_a_reply_full_of_markdown_survives_the_round_trip(
        self, app: App, tmp_path: Path, monkeypatch
    ) -> None:
        # Model replies routinely hold headings and fences; the document
        # must carry them byte-exact through export and back.
        monkeypatch.chdir(tmp_path)
        reply = "### Chapter One\n\nThe hall was dark.\n```\nan unclosed fence"
        app.server.script = lambda body: reply
        app.play("I open the book.")
        app.play("The story goes on.")
        app.play("/title Book")
        app.play("/export")
        app.play(f"/import {tmp_path / 'book.md'}")
        chain = app.store.stories.get_messages(app.session.story_id)
        assert [m.body for m in chain] == [
            "I open the book.",
            reply,
            "The story goes on.",
            reply,
        ]


class TestSillyTavern:
    def test_a_tavern_chat_imports_attributed_and_builds_memory(
        self, app: App, tmp_path: Path
    ) -> None:
        app.play(f"/import {tavern_file(tmp_path)}")
        story_id = app.session.story_id
        chain = app.store.stories.get_messages(story_id)
        # The hidden `is_system` line is skipped; the rest arrive verbatim.
        assert [(m.role, m.body) for m in chain] == [
            ("assistant", "Come in from the rain."),
            ("user", "I step in and lower my hood."),
            ("assistant", "Welcome, traveler."),
        ]
        # Real names become speakers; ST's "User" placeholder does not.
        assert [m.speaker for m in chain] == ["Elara", None, "Elara"]
        # The chat carries no memory, so the import's pass built it.
        ids = app.store.stories.get_messages_ids(story_id)
        assert app.store.scenes.get_current(story_id, ids) != []

    def test_extraction_fills_speakers_but_never_overwrites_them(
        self, app: App, tmp_path: Path
    ) -> None:
        # The pass names a speaker for EVERY message; rows attributed by
        # the import keep their names — extraction fills only the holes.
        def renaming(body: dict) -> str:
            prompt = str(body.get("messages", [{}])[-1].get("content", ""))
            if "You are a story analyst" not in prompt:
                return scripted.default_script(body)
            return json.dumps(
                {
                    "scene": {"title": "The Gate", "summary": "A guest arrived."},
                    "speakers": [
                        {"n": 1, "speaker": "Impostor"},
                        {"n": 2, "speaker": "Guest"},
                        {"n": 3, "speaker": "Impostor"},
                    ],
                    "characters": [],
                    "journals": [],
                }
            )

        app.server.script = renaming
        app.play(f"/import {tavern_file(tmp_path)}")
        chain = app.store.stories.get_messages(app.session.story_id)
        assert [m.speaker for m in chain] == ["Elara", "Guest", "Elara"]


class TestPlaintext:
    def test_prose_is_dismantled_verbatim_and_memory_is_built(
        self, app: App, tmp_path: Path, capsys
    ) -> None:
        path = tmp_path / "chapel.txt"
        path.write_text(PROSE)
        app.play(f"/import {path}")
        assert "Imported" in capsys.readouterr().out

        story_id = app.session.story_id
        chain = app.store.stories.get_messages(story_id)
        assert len(chain) >= 2  # narration and speech split apart
        # Nothing is rewritten: every segment is a verbatim slice.
        for message in chain:
            assert message.body in PROSE
        assert chain[0].body.startswith("The chapel stood silent")
        assert all(m.kind == "narration" for m in chain)
        # The pass built the memory from the imported text.
        ids = app.store.stories.get_messages_ids(story_id)
        assert app.store.scenes.get_current(story_id, ids) != []


class TestExport:
    def test_export_writes_the_document_the_import_reads_back(
        self, app: App, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        app.play("I enter the hall.")
        app.play("/title Hall")
        app.play("/export")
        exported = parse_story((tmp_path / "hall.md").read_text())
        assert exported is not None
        assert exported.title == "Hall"
        assert [m.body for m in exported.messages] == [
            "I enter the hall.",
            app.session.messages[-1].body,
        ]

    def test_overwriting_an_existing_file_needs_a_yes(
        self, app: App, tmp_path: Path, monkeypatch
    ) -> None:
        target = tmp_path / "kept.md"
        target.write_text("precious notes")
        app.play("I enter the hall.")
        monkeypatch.setattr("builtins.input", lambda prompt="": "")  # the [y/N] default
        app.play(f"/export {target}")
        assert target.read_text() == "precious notes"


def tavern_file(tmp_path: Path) -> Path:
    path = tmp_path / "elara.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in TAVERN_LINES))
    return path
