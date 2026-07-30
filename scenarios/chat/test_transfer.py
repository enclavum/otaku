"""Import and export: files round-trip through the app.

The chapel fixture is deliberately Cyrillic: importing it proves non-Latin
content survives the whole path — parse, sealed store, session, export —
byte for byte. The SillyTavern and free-text imports arrive memoryless, so
each triggers the same forced extraction pass live play gets.
"""

import json
from pathlib import Path

from otaku.terminal import clipboard
from otaku.transfer.exports import read_story
from otaku.transfer.imports import parse_story
from scenarios.support import server as scripted
from scenarios.support.harness import App

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
        app.play(f"/import chat {CHAPEL}")
        out = capsys.readouterr().out
        assert "1 scene(s) applied verbatim" in out
        assert "Nothing new since the last scene." in out  # the extract pass declined
        assert app.server.requests == []  # memory came from the file, not a model

        # The session switched onto the imported story, memory and all.
        story_id = app.session.story_id
        assert len(app.session.messages) == 4
        assert app.session.system.startswith("Ты — рассказчик")
        cast = app.store.characters.list(story_id)
        assert [c.name for c in cast] == ["Кассиан", "Элоиза"]

    def test_the_reimported_story_equals_the_file(self, app: App, tmp_path: Path) -> None:
        app.play(f"/import chat {CHAPEL}")
        assert read_story(app.store, app.session.story_id) == parse_story(CHAPEL.read_text())

    def test_a_missing_file_imports_nothing(self, app: App, capsys) -> None:
        app.play(f"/import chat {Path('/nowhere') / 'gone.md'}")
        assert capsys.readouterr().out.strip()  # refused out loud
        assert app.session.story_id is None
        assert app.server.requests == []

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
        app.play("/rename Book")
        app.play("/export")
        app.play(f"/import chat {tmp_path / 'book.md'}")
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
        app.play(f"/import chat {tavern_file(tmp_path)}")
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
        app.play(f"/import chat {tavern_file(tmp_path)}")
        chain = app.store.stories.get_messages(app.session.story_id)
        assert [m.speaker for m in chain] == ["Elara", "Guest", "Elara"]


class TestFreetext:
    def test_prose_is_dismantled_verbatim_and_memory_is_built(
        self, app: App, tmp_path: Path, capsys
    ) -> None:
        path = tmp_path / "chapel.txt"
        path.write_text(PROSE)
        app.play(f"/import text {path}")
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
        app.play("/rename Hall")
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


class TestCopy:
    def test_copy_puts_the_last_reply_on_the_clipboard(self, app: App, monkeypatch) -> None:
        copied: list[str] = []

        def fake_copy(text: str) -> str:
            copied.append(text)
            return "stub"

        monkeypatch.setattr(clipboard, "copy", fake_copy)
        app.play("I enter the hall.")
        app.play("/copy")
        assert copied == [scripted.CHAT_REPLY]

    def test_copy_all_is_a_readable_transcript(self, app: App, monkeypatch) -> None:
        copied: list[str] = []
        monkeypatch.setattr(clipboard, "copy", lambda text: copied.append(text) or "stub")
        app.play("I enter the hall.")
        app.play("/copy all")
        (transcript,) = copied
        assert "I enter the hall." in transcript
        assert scripted.CHAT_REPLY in transcript
        assert "## " in transcript  # role headers, readable as Markdown


def tavern_file(tmp_path: Path) -> Path:
    path = tmp_path / "elara.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in TAVERN_LINES))
    return path
