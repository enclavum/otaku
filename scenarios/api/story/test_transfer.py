"""Import and export: files round-trip through the app.

The chapel fixture is deliberately Cyrillic: importing it proves non-Latin
content survives the whole path — parse, sealed store, session, export —
byte for byte. The SillyTavern and plain-text imports arrive memoryless, so
each triggers the same forced extraction pass live play gets. Beside the
synthetic minimal inputs, two checked-in artifacts keep the wild shapes
honest: a tavern chat as SillyTavern really writes one — the metadata
header, swipes, runtime junk, a hidden line — and a prose file with real
typography.
"""

import json
from pathlib import Path

from otaku.backend.domain._formats import EXPORT_MARKER
from otaku.backend.domain._formats.exports import read_story
from otaku.backend.domain._formats.imports import parse_story
from scenarios.support import server as scripted
from scenarios.support.harness import App

CHAPEL = Path(__file__).parents[2] / "fixtures" / "chapel.md"

TAVERN = CHAPEL.parent / "sillytavern-chat.jsonl"

LANTERN = CHAPEL.parent / "lantern.txt"

# fmt: off
TAVERN_LINES = (
    {"user_name": "User", "character_name": "Elara", "create_date": "2026-05-01@12h00m00s"},
    {"name": "Elara", "is_user": False, "is_system": False, "mes": "Come in from the rain."},
    {"name": "User", "is_user": True, "is_system": False, "mes": "I step in and lower my hood."},
    {"name": "Elara", "is_user": False, "is_system": True, "mes": "(a hidden note)"},
    {"name": "Elara", "is_user": False, "is_system": False, "mes": "Welcome, traveler."},
)

PROSE = """The chapel stood silent at the edge of the marsh, its windows dark.

"Who goes there?" the Keeper called from within. The heavy door creaked
on its hinges.
"""



class TestImport:
    def test_a_full_export_imports_verbatim_without_model_calls(self, app: App, capsys) -> None:
        app.play(f"/import {CHAPEL}")
        out = capsys.readouterr().out
        assert "1 scene(s) applied verbatim" in out
        # A native export carries its extraction state: no pass runs, no
        # model is called — the story arrives exactly as it was.
        assert app.server.requests == []

        # The chat switched onto the imported story, memory and all.
        story_id = app.story_id
        assert len(app.messages) == 4
        assert app.premise.startswith("Ты — рассказчик")
        cast = app.store.characters.list(story_id)
        assert [c.name for c in cast] == ["Кассиан", "Элоиза"]

    def test_the_reimported_story_equals_the_file(self, app: App, tmp_path: Path) -> None:
        app.play(f"/import {CHAPEL}")
        assert read_story(app.store, app.story_id) == parse_story(CHAPEL.read_text())

    def test_a_missing_file_imports_nothing(self, app: App, capsys) -> None:
        app.play(f"/import {Path('/nowhere') / 'gone.md'}")
        assert capsys.readouterr().out.strip()  # refused out loud
        assert app.story_id is None
        assert app.server.requests == []

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
        assert app.story_id is None
        assert app.server.requests == []

    def test_a_newer_format_is_refused_with_directions(
        self, app: App, capsys, tmp_path: Path
    ) -> None:
        # The reader's law across releases: any older format parses (the
        # chapel fixture itself declares format-version 1, read by every
        # test here), a newer one refuses instead of importing silently
        # wrong.
        newer = tmp_path / "newer.md"
        newer.write_text(
            CHAPEL.read_text().replace(EXPORT_MARKER, f"{EXPORT_MARKER}\nformat-version: 99", 1)
        )
        app.play(f"/import {newer}")
        assert "newer otaku" in capsys.readouterr().out
        assert app.story_id is None
        assert app.server.requests == []

    def test_json_that_is_not_a_tavern_chat_is_refused(
        self, app: App, capsys, tmp_path: Path
    ) -> None:
        data = tmp_path / "data.jsonl"
        data.write_text('{"hello": "world"}\n')
        app.play(f"/import {data}")
        assert "not a SillyTavern chat" in capsys.readouterr().out
        assert app.story_id is None
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
        assert app.story_id is None

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
        chain = app.store.stories.get_messages(app.story_id)
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
        story_id = app.story_id
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
        chain = app.store.stories.get_messages(app.story_id)
        assert [m.speaker for m in chain] == ["Elara", "Guest", "Elara"]

    def test_a_real_export_with_its_runtime_junk_imports_clean(self, app: App) -> None:
        # The checked-in artifact is shaped the way SillyTavern writes a
        # chat: metadata header, swipes, gen stamps, `extra` blobs, one
        # hidden line.
        app.play(f"/import {TAVERN}")
        chain = app.store.stories.get_messages(app.story_id)
        # Attribution reads through the junk; a named persona is a speaker.
        assert [(m.role, m.speaker) for m in chain] == [
            ("assistant", "Seraphina"),
            ("user", "Rin"),
            ("assistant", "Seraphina"),
            ("user", "Rin"),
            ("assistant", "Seraphina"),
        ]
        joined = " ".join(m.body for m in chain)
        # Only the ACTIVE swipe arrives; the alternate greeting stays behind.
        assert chain[0].body.endswith("The storm carried you far.”")
        assert "forest is quiet" not in joined
        # The hidden narrator note and the runtime junk leave no trace.
        assert "storm has passed" not in joined
        assert "gpt-4o-mini" not in joined and "token_count" not in joined
        ids = app.store.stories.get_messages_ids(app.story_id)
        assert app.store.scenes.get_current(app.story_id, ids) != []


class TestPlaintext:
    def test_prose_is_dismantled_verbatim_and_memory_is_built(
        self, app: App, tmp_path: Path, capsys
    ) -> None:
        path = tmp_path / "chapel.txt"
        path.write_text(PROSE)
        app.play(f"/import {path}")
        assert "Imported" in capsys.readouterr().out

        story_id = app.story_id
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

    def test_a_prose_file_dismantles_with_its_typography_intact(self, app: App) -> None:
        # The checked-in artifact carries what inline strings avoid: curly
        # quotes, em dashes, a cited word, a dash-dialogue block.
        app.play(f"/import {LANTERN}")
        raw = LANTERN.read_text(encoding="utf-8")
        chain = app.store.stories.get_messages(app.story_id)
        assert [m.kind for m in chain] == ["narration"] * 5
        bodies = [m.body for m in chain]
        # Verbatim slices — the typography reaches the store byte for byte.
        assert all(body in raw for body in bodies)
        assert bodies[0].startswith("The lantern above the harbor stair")
        # “blind” is a cited word, too short for speech: the paragraph holds.
        assert bodies[0].endswith("“blind” now.")
        # The spoken line travels whole: quote, attribution tag, second breath.
        assert bodies[1] == (
            "“You’re late,” the ferryman said, without turning. "
            "“The water knows it, even if you don’t.”"
        )
        assert bodies[3].startswith("— Then row faster")
        ids = app.store.stories.get_messages_ids(app.story_id)
        assert app.store.scenes.get_current(app.story_id, ids) != []


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
            app.messages[-1].body,
        ]

    def test_a_card_story_exports_typed_and_the_archive_travels(
        self, app: App, tmp_path: Path, monkeypatch
    ) -> None:
        """A card row exports as its typed `/card` line, and the archive
        it composes from rides the Cast section — so a re-import restores
        the dualism whole: the typed line stored, `characters.card` back
        in the cast, and the wire composed from the archive, never the
        literal command line."""
        monkeypatch.chdir(tmp_path)
        card = Path(__file__).parents[2] / "fixtures" / "seraphina.png"
        app.play(f"/card {card}")
        app.play("/title Glade")
        archive = app.store.characters.find(app.story_id, "Seraphina").card
        app.play("/export")
        document = (tmp_path / "glade.md").read_text()
        assert f"/card {card}" in document
        assert "#### Seraphina" in document

        app.play("/new")
        app.play(f"/import {tmp_path / 'glade.md'}")
        restored = app.store.characters.find(app.story_id, "Seraphina")
        assert restored is not None and restored.card == archive
        assert app.messages[0].body == f"/card {card}"
        app.play("I sit up slowly.")
        wire = "\n".join(str(m["content"]) for m in app.server.requests[-1]["messages"])
        assert "Seraphina joins the story" in wire
        assert "/card" not in wire

    def test_a_tools_turn_stays_out_of_the_export(self, app: App, tmp_path: Path) -> None:
        # the format has no place for it yet: the reply that called and
        # the reply that went on are story, the result between them is not
        app.api.stories.settings.update(app.ensure_story(), "allow_system_info", enabled=True)
        call = "```otk-system-info\n```"

        def script(body: dict) -> str:
            newest = scripted.content_text(body["messages"][-1])
            if "You are a story analyst" in newest or newest.startswith(("Combine", "Write ")):
                return scripted.default_script(body)
            return "It is late." if "Time: " in newest else call

        app.server.script = script
        app.play("What time is it?")
        assert [m.role for m in app.messages] == ["user", "assistant", "tool", "assistant"]
        out = tmp_path / "story.md"
        app.play(f"/export {out}")
        text = out.read_text(encoding="utf-8")
        assert "Moon: " not in text and "Time: " not in text
        assert call in text and "It is late." in text
        # and the document imports back as three turns
        parsed = parse_story(text)
        assert len(parsed.messages) == 3

    def test_a_name_without_an_extension_gets_md(self, app: App, tmp_path: Path) -> None:
        app.play("I enter the hall.")
        app.play(f"/export {tmp_path / 'glade'}")
        assert (tmp_path / "glade.md").read_text().startswith(EXPORT_MARKER)
        assert not (tmp_path / "glade").exists()

    def test_a_name_with_one_keeps_it(self, app: App, tmp_path: Path) -> None:
        app.play("I enter the hall.")
        app.play(f"/export {tmp_path / 'glade.txt'}")
        assert (tmp_path / "glade.txt").read_text().startswith(EXPORT_MARKER)
        assert not (tmp_path / "glade.txt.md").exists()

    def test_an_existing_directory_is_left_to_fail(self, app: App, tmp_path: Path, capsys) -> None:
        # `.md` here would write a file BESIDE the directory the user
        # named; the write refusing out loud is the honest answer.
        target = tmp_path / "somewhere"
        target.mkdir()
        app.play("I enter the hall.")
        capsys.readouterr()
        app.play(f"/export {target}")
        assert "Could not write" in capsys.readouterr().out
        assert not (tmp_path / "somewhere.md").exists()

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
