"""Import and export: the fixture story round-trips through the app."""

from pathlib import Path

from otaku.transfer.exports import read_story
from otaku.transfer.imports import parse_story
from scenarios.support.harness import App

CHAPEL = Path(__file__).parent / "fixtures" / "chapel.md"


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


class TestExport:
    def test_export_writes_the_document_the_import_reads_back(
        self, app: App, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        app.play("Я вхожу в зал.")
        app.play("/rename Зал")
        app.play("/export")
        exported = parse_story((tmp_path / "зал.md").read_text())
        assert exported is not None
        assert exported.title == "Зал"
        assert [m.body for m in exported.messages] == [
            "Я вхожу в зал.",
            app.session.messages[-1].body,
        ]
