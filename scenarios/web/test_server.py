"""The page over a real app: what the browser asks for, and gets.

One thread serves, the test's own stands in for the browser. The stories
here are the ones a person plays through the page — open it, read the
story, play a line, run a command — asserted on the wire and in the
store, never on the screen. The server holds no story: the page names
the one it plays in every path, as `Page` does.
"""

import base64
import json
import socket
import threading
import time
import tomllib
import urllib.error
import urllib.request
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from otaku.backend.api import Layout
from otaku.backend.app.models.services import PARAMETERS, THINK_MENU
from otaku.backend.app.paths import Paths
from otaku.backend.codex.cards.services import NEW_CARD_NAME
from otaku.integrations import SupportDesk
from otaku.settings import models as models_file
from otaku.store.files import Owner
from otaku.store.schema import Message
from scenarios.support import server as scripted
from scenarios.support.harness import MODEL, set_config, set_config_provider
from scenarios.support.server import ModelServer
from scenarios.web.conftest import Page, serving

SERAPHINA = Path(__file__).parent.parent / "fixtures" / "seraphina.png"
CAT = Path(__file__).parent.parent / "fixtures" / "cat.jpg"


class TestServing:
    def test_the_page_and_everything_it_needs_are_served(self, page: Page) -> None:
        assert b"<title>otaku</title>" in page.get("/")
        screens = ("/js/screens/stories.js", "/js/screens/dossier.js", "/js/play/held.js")
        for asset in ("/app.css", "/app.js", *screens):
            assert page.status(asset) == 200, asset

    def test_the_client_is_served_from_its_own_directory(self, page: Page) -> None:
        # The page's modules live in folders of their own; a nested one is
        # served as any script is.
        for module in ("/js/api/index.js", "/js/api/_http.js", "/js/api/story.js"):
            assert page.status(module) == 200, module
            assert "javascript" in page.headers(module)["content-type"], module

    def test_nothing_is_cached_and_the_fonts_are(self, page: Page) -> None:
        # The rule the whole frontend is built on: what is on disk is
        # what the browser has. The fonts carry their version in the name.
        for asset in ("/", "/app.css", "/app.js", "/custom.css"):
            assert "no-store" in page.headers(asset)["cache-control"], asset
        assert "immutable" in page.headers("/fonts/IBMPlexSans-v3.201-400.woff2")["cache-control"]

    def test_a_missing_stylesheet_of_the_reader_s_own_is_an_empty_one(self, page: Page) -> None:
        # Absent is the normal case: a red line in the console is not a
        # state to design for.
        assert page.get("/custom.css") == b""

    def test_a_typeface_of_the_reader_s_own_is_served(self, page: Page) -> None:
        """`web/fonts/` in the state dir, so `custom.css` can be a whole
        theme and not only a palette. Their directory, their files — and
        the name is looked up in a LISTING of it, never joined onto it."""
        theirs = page.root / "web" / "fonts"
        theirs.mkdir(parents=True, exist_ok=True)
        (theirs / "Mine.woff2").write_bytes(b"wOF2-not-really")
        assert page.get("/web-fonts/Mine.woff2") == b"wOF2-not-really"
        assert page.headers("/web-fonts/Mine.woff2")["content-type"] == "font/woff2"
        # Absent, wrong kind, and a name that is not a name at all.
        assert page.status("/web-fonts/Absent.woff2") == 404
        (theirs / "notes.txt").write_text("not a typeface", encoding="utf-8")
        assert page.status("/web-fonts/notes.txt") == 404
        assert page.status("/web-fonts/../../configs/providers.toml") == 404

    def test_only_the_table_may_be_served(self, page: Page) -> None:
        # A path is looked up, never joined onto a directory.
        for escape in ("/../pyproject.toml", "/../../etc/passwd", "/configs/providers.toml"):
            assert page.status(escape) == 404, escape

    def test_an_unknown_path_is_not_found(self, page: Page) -> None:
        assert page.status("/api/nonesuch") == 404
        assert page.status("/api/stories/1/nonesuch", method="POST") == 404

    def test_the_watch_stream_opens_and_names_its_retry(self, page: Page) -> None:
        # The page holds this one open for as long as the tab is, so it
        # is read a frame at a time and never to the end. The first
        # frame is the retry — how soon a browser comes back after a
        # restart, which is what makes the tab reload itself.
        opened = page.stream("/api/watch")
        assert opened.headers["Content-Type"] == "text/event-stream"
        assert opened.readline().startswith(b"retry:")
        opened.close()

    def test_a_closed_tab_does_not_leave_its_watch_stream_running(self, page: Page) -> None:
        # The stream is silent for as long as no file changes, and a
        # silent stream never learns its reader is gone: without the
        # keepalive it holds a thread and a socket per reload, for the
        # life of the process. Two ticks to notice — the first write
        # after a close still buffers.
        before = _watchers()
        opened = [page.stream("/api/watch") for _ in range(3)]
        for stream in opened:
            stream.readline()
        time.sleep(0.3)
        assert _watchers() >= before + 3, "the streams never started"
        for stream in opened:
            stream.close()
        deadline = time.time() + 8
        while time.time() < deadline and _watchers() > before:
            time.sleep(0.2)
        # At most the baseline: an earlier story's request may still be
        # finishing, and what this asserts is that none of OURS is.
        assert _watchers() <= before, f"{_watchers() - before} watcher(s) still running"


class TestTheHeartbeat:
    """`/api/app/status` is how a tab that is asking for nothing else
    learns that otaku stopped — and what the backend is doing while
    nobody asked. It is in neither lane, and the difference shows exactly
    when the app's thread is not free."""

    def test_it_answers_what_can_be_answered_off_the_app_s_thread(self, page: Page) -> None:
        assert page.get("/api/app/status") == {"status": "", "notices": []}

    def test_it_answers_while_a_reply_is_streaming(self, page: Page, server: ModelServer) -> None:
        # The one moment the app's thread is unavailable: it is holding a
        # reply, chunk by chunk. The heartbeat never asks for that thread,
        # which is what makes it an answer about the SERVER — so it comes
        # back with the reply still going.
        page.made()
        server.chunk_delay = 1.0
        replying = threading.Thread(target=page.play, args=("A long look at the water.",))
        replying.start()
        try:
            time.sleep(0.5)
            assert page.status("/api/app/status") == 200
            assert replying.is_alive(), "the reply was over — the story proves nothing"
        finally:
            replying.join(timeout=30)


class TestTheSurface:
    """What every path keeps: a collection answers `items`, an answer
    about one story carries its head as the `ETag`, a write that moves a
    head names the one it moves from in `If-Match` — missing, the page's
    fault; stale, a refusal naming where the head stands; `*`, the head
    as it stands — and an entity made answers 201 with where it lives."""

    def test_a_collection_answers_its_items(self, page: Page) -> None:
        page.play("I listen at the culvert mouth.")
        assert set(page.get("/api/stories")) == {"items"}
        assert set(page.get(f"/api/stories/{page.story}/messages")) == {"items"}

    def test_an_answer_about_a_story_carries_its_head(self, page: Page) -> None:
        story = page.made()
        assert page.headers(f"/api/stories/{story}")["etag"] == '"0"'  # nothing played
        page.play("I listen at the culvert mouth.")
        head = page.messages()[-1]["id"]
        for path in (f"/api/stories/{story}", f"/api/stories/{story}/messages"):
            assert page.headers(path)["etag"] == f'"{head}"', path
        assert page.get(f"/api/stories/{story}")["head"] == head

    def test_a_write_that_moves_the_head_must_name_it(self, page: Page) -> None:
        page.play("I listen at the culvert mouth.")
        story, first = page.story, page.messages()[0]["id"]
        path = f"/api/stories/{story}"
        assert page.status(path, method="PATCH", data=json.dumps({"head": first}).encode()) == 428
        assert page.status(f"{path}/undos", method="POST") == 428
        # a title moves no head and names none
        assert page.status(path, method="PATCH", data=b'{"title": "The Weir"}') == 200

    def test_a_head_moved_since_it_was_read_is_refused_with_where_it_stands(
        self, page: Page
    ) -> None:
        page.play("I listen at the culvert mouth.")
        story, first = page.story, page.messages()[0]["id"]
        stale = page.headers(f"/api/stories/{story}")["etag"]
        page.play("I wade in.")  # the head moves on, elsewhere
        status, headers, problem = page.answer(
            "PATCH", f"/api/stories/{story}", {"head": first}, if_match=stale
        )
        head = page.messages()[-1]["id"]
        assert status == 412
        assert problem["head"] == head and headers["etag"] == f'"{head}"'
        assert problem["detail"]
        assert page.messages()[-1]["id"] == head  # nothing was moved

    def test_the_tag_last_read_or_the_head_as_it_stands_moves_it(self, page: Page) -> None:
        page.play("I listen at the culvert mouth.")
        page.play("I wade in.")
        story, first = page.story, page.messages()[0]["id"]
        tag = page.headers(f"/api/stories/{story}")["etag"]
        answer = page.patch(f"/api/stories/{story}", {"head": first}, if_match=tag)
        assert answer["value"]["head"] == first
        undone = page.post(f"/api/stories/{story}/undos", if_match="*")
        assert undone["value"]["head"] is None and undone["notice"]

    def test_a_story_made_answers_where_it_now_lives(self, page: Page) -> None:
        # A POST that MAKES something answers 201 and names it, so the
        # page never has to ask which story it just got.
        status, headers, answer = page.answer("POST", "/api/stories", {"title": "The Weir"})
        assert status == 201
        assert headers["location"] == f"/api/stories/{answer['value']['id']}"
        assert answer["value"]["title"] == "The Weir" and answer["notice"]

    def test_a_creation_that_refuses_names_nothing(self, page: Page) -> None:
        # Nothing was made, so there is nowhere to point: the refusal is
        # the answer. An empty story is the case — there is no turn to copy.
        story = page.post("/api/stories", {"title": "Empty"})["value"]["id"]
        status, headers, problem = page.answer("POST", f"/api/stories/{story}/forks")
        assert status == 409 and "location" not in headers
        assert problem["detail"]


class TestReading:
    def test_the_app_names_the_model_in_use(self, page: Page) -> None:
        model = page.get("/api/app")["model"]
        # the bare model and the provider beside it — the banner's own split
        assert (model["provider"], model["name"]) == ("generic", "test-model")

    def test_the_extraction_model_is_pinned_named_and_unpinned(self, page: Page) -> None:
        # The app names the model extraction asks — the one in use until
        # one is pinned — and whether it is pinned, which the picker marks.
        def extraction() -> tuple[str, bool]:
            found = page.get("/api/app")["extraction_model"]
            return f"{found['provider']}/{found['name']}", found["pinned"]

        assert extraction() == ("generic/test-model", False)
        pinned = page.patch("/api/app", {"extraction_model": {"provider": "generic", "name": "x"}})
        assert pinned["notice"] and pinned["value"]["extraction_model"]["name"] == "x"
        assert extraction() == ("generic/x", True)
        assert page.patch("/api/app", {"extraction_model": None})["notice"]
        assert extraction() == ("generic/test-model", False)
        # unpinning none is a no-op, and says so
        assert page.patch("/api/app", {"extraction_model": None})["notice"]

    def test_scenes_and_characters_say_who_made_them(self, page: Page) -> None:
        page.play("I listen at the culvert mouth.")
        story = page.story
        ids = page.store.stories.get_messages_ids(story)
        first, last = ids[0], ids[-1]
        page.store.scenes.add(
            story, start_message_id=first, end_message_id=last, summary="s", provider="p", model="m"
        )
        page.store.characters.add(story, "Maren", provider="card", model="maren.png")
        page.store.characters.add(story, "Tallis")
        lore = page.get(f"/api/stories/{story}/lore")
        assert [(s["provider"], s["model"]) for s in lore["scenes"]] == [("p", "m")]
        made = [(c["provider"], c["model"]) for c in lore["characters"]]
        # a card made the first — no model, whatever its row once recorded
        assert made == [("card", None), (None, None)]

    def test_the_context_fill_is_the_preview_s_own_figure(self, tmp_path: Path) -> None:
        # Not known while the model is not loaded; once a turn loaded it,
        # the figure /context reports, against the model's window.
        engine = ModelServer(managed=True)
        engine.contexts["test-model"] = 32768
        try:
            with serving(engine, tmp_path, provider="ollama") as page:
                story = page.made()
                assert page.refused("GET", f"/api/stories/{story}/context/fill")["status"] == 404
                page.play("I listen at the culvert mouth.")
                fill = page.get(f"/api/stories/{story}/context/fill")
                assert fill["percent"] == page.get(f"/api/stories/{story}/context")["shape"]["used"]
                assert fill["context_in_force"] == 32768 and fill["text"]
        finally:
            engine.close()

    def test_the_messages_are_the_story_as_the_store_has_it(self, page: Page) -> None:
        page.play("I listen at the culvert mouth.")
        assert [message["body"] for message in page.messages()] == [
            message.body for message in page.store.stories.get_messages(page.story)
        ]

    def test_the_typed_language_is_the_story_s_own(self, page: Page) -> None:
        # The story's own framing, declared once below both frontends —
        # and NOT the terminal's commands: a button is how the page acts.
        language = page.get("/api/language")
        assert {row["token"] for row in language["openers"]} >= {"/me", "/you", "/ooc"}
        assert {row["token"] for row in language["inliners"]} >= {"… /cue"}
        assert language["prose"]

    def test_a_read_that_refuses_answers_with_the_sentence(self, page: Page) -> None:
        # A refusal IS the answer — its status, and the sentence in a
        # problem body the page shows as it is.
        problem = page.refused("GET", "/api/stories/9999")
        assert problem["status"] == 404

    def test_the_cast_answers_the_composers_name_menu(self, page: Page) -> None:
        # Empty before a pass has named anybody — a menu question is never
        # a refusal — and found by name once one has.
        story = page.made()
        assert page.get(f"/api/stories/{story}/characters") == {"items": []}
        page.store.characters.add(story, "Maren")
        named = page.get(f"/api/stories/{story}/characters?name=maren")["items"]
        assert [c["name"] for c in named] == ["Maren"]

    def test_the_balance_roster_asks_no_network_and_marks_the_unasked(self, page: Page) -> None:
        """`?probe=false` is the slip's first paint: every cloud row
        present, a KEYED one carrying an empty note (not asked yet) and
        no figure — offline-provable, since nothing may be probed."""
        page.patch("/api/providers/openrouter", {"api_key": "k-test"})
        rows = {row["provider"]: row for row in page.get("/api/balance?probe=false")["rows"]}
        assert rows["openrouter"]["note"] == ""  # keyed: not asked yet
        assert rows["openrouter"]["money"] is None
        assert rows["nanogpt"]["note"]  # keyless: says which nothing it is

    def test_an_emptied_provider_field_is_forgotten(self, page: Page) -> None:
        # An empty value is the terminal's Del: the url or the stored key
        # is cleared in the file and the app both. The page's Save sends
        # a field as the reader left it, and nothing else could take a key
        # out again.
        page.patch("/api/providers/openrouter", {"api_key": "k-test"})
        providers = page.root / "configs/providers.toml"
        assert tomllib.loads(providers.read_text(encoding="utf-8"))["openrouter"]["api_key"]
        answer = page.patch("/api/providers/openrouter", {"api_key": ""})
        assert answer["value"]["key_source"] is None
        assert tomllib.loads(providers.read_text(encoding="utf-8"))["openrouter"]["api_key"] == ""
        page.patch("/api/providers/generic", {"url": ""})
        assert tomllib.loads(providers.read_text(encoding="utf-8"))["generic"]["url"] == ""

    def test_a_provider_says_where_its_key_comes_from(self, page: Page, monkeypatch) -> None:
        # The field's caption, never the value: the section's key while
        # there is one, the shell's once it is cleared, and a section
        # that does not exist yet is asked the same way.
        monkeypatch.setenv("GENERIC_API_KEY", "from-env")
        monkeypatch.setenv("NANOGPT_API_KEY", "from-env")
        monkeypatch.delenv("LLAMACPP_API_KEY", raising=False)
        source = lambda name: page.get(f"/api/providers/{name}")["key_source"]  # noqa: E731
        assert source("generic") == "config"
        page.patch("/api/providers/generic", {"api_key": ""})
        assert source("generic") == "env"
        page.patch("/api/providers/generic", {"api_key": "k-test"})
        assert source("generic") == "config"
        assert source("nanogpt") == "env"  # no section yet
        assert source("llamacpp") is None
        assert "api_key" not in page.get("/api/providers/generic")  # never the value

    def test_the_settings_say_how_long_the_pass_waits(self, page: Page) -> None:
        # `[lore] idle_seconds`, as the app was opened with it: a fact the
        # page's confirmation says, never a guess of its own.
        assert page.get("/api/settings")["idle_seconds"] == 300.0

    def test_a_model_s_settings_carry_the_shared_effort_ladder(self, page: Page) -> None:
        # The order is declared ONCE, below both frontends — the page
        # draws it, never re-sorts it. A model the provider says nothing
        # of takes every rung.
        own = page.get(f"/api/providers/generic/models/{MODEL}/settings")
        assert own["choices"]["levels"] == list(THINK_MENU)
        assert own["think"] == "unset"

    def test_a_model_s_settings_list_every_parameter_and_say_which_reach(
        self, server: ModelServer, tmp_path: Path
    ) -> None:
        # Every parameter rides the read, in /set's order, each saying
        # whether the provider in use reads it for the model — the page
        # draws an unsupported one closed rather than dropping it. LM
        # Studio's wire reads top_k and not min_p.
        def studio(root: Path) -> None:
            set_config_provider(root, server, name="lmstudio")

        with serving(server, tmp_path, prepare=studio) as page:
            page.patch("/api/app", {"model": {"provider": "lmstudio", "name": "test-model"}})
            rows = page.get("/api/providers/lmstudio/models/test-model/settings")["parameters"]
            assert [row["name"] for row in rows] == list(PARAMETERS)
            supported = {row["name"]: row["supported"] for row in rows}
            assert supported["top_k"] and not supported["min_p"]

    def test_a_model_s_settings_carry_each_parameters_bounds(self, page: Page) -> None:
        # The bounds the setter holds a value to ride each row, null
        # where there is none, so the page can say them before a save.
        found = page.get(f"/api/providers/generic/models/{MODEL}/settings")["parameters"]
        rows = {row["name"]: row for row in found}
        assert (rows["temperature"]["low"], rows["temperature"]["high"]) == (0, 2)
        assert (rows["top_k"]["low"], rows["top_k"]["high"]) == (0, None)
        assert (rows["seed"]["low"], rows["seed"]["high"]) == (None, None)
        assert rows["stop"]["kind"] == "list"  # a text field: the setter reads the JSON strings

    def test_a_parameter_saved_reads_back_as_typed_and_as_its_kind(self, page: Page) -> None:
        path = f"/api/providers/generic/models/{MODEL}/settings"
        saved = page.patch(path, {"parameters": {"temperature": "0.70"}})
        row = next(r for r in saved["value"]["parameters"] if r["name"] == "temperature")
        assert (row["value"], row["text"]) == (0.7, "0.7")
        unset = page.patch(path, {"parameters": {"temperature": None}})
        row = next(r for r in unset["value"]["parameters"] if r["name"] == "temperature")
        assert (row["value"], row["text"]) == (None, "")

    def test_the_search_matches_buried_content_and_the_row_s_face(self, page: Page) -> None:
        """One filter rule for both browsers: a story is found by the
        text of its chain AND by what its listing row shows — here the
        title, which is never a message."""
        page.play("I listen at the culvert mouth.")
        page.patch(f"/api/stories/{page.story}", {"title": "The Beached Ferry"})

        def found(needle: str) -> list[int]:
            return [row["id"] for row in page.get(f"/api/stories?query={needle}")["items"]]

        assert page.story in found("culvert")  # buried in the chain
        assert page.story in found("beached")  # the listing row's own face
        assert page.story not in found("zeppelin")

    def test_the_machine_says_how_full_its_memory_is(self, page: Page) -> None:
        # the one machine fact a picker needs, read again while a model loads
        assert set(page.get("/api/app/machine")) == {"memory"}

    def test_the_usage_is_counted_for_the_story_or_all_of_them(self, page: Page) -> None:
        page.play("I listen at the culvert mouth.")
        mine = page.get(f"/api/usage?story={page.story}")
        everything = page.get("/api/usage")
        assert mine["scope"] != everything["scope"]
        assert mine["requests"] == everything["requests"] >= 1
        assert {row["purpose"] for row in mine["rows"]} == {"chat"}

    def test_the_info_report_is_its_sections(self, page: Page) -> None:
        sections = page.get(f"/api/info?story={page.made()}")["sections"]
        assert sections and all(len(row) == 2 for s in sections for row in s["rows"])

    def test_the_last_messages_alone_where_a_count_is_asked(self, page: Page) -> None:
        page.play("I listen at the culvert mouth.")
        last = page.get(f"/api/stories/{page.story}/messages?last=1")["items"]
        assert [m["id"] for m in last] == [page.messages()[-1]["id"]]
        assert page.status(f"/api/stories/{page.story}/messages?last=0") == 400

    def test_a_path_whose_id_is_not_one_addresses_nothing(self, page: Page) -> None:
        # A row id is digits, so a path carrying anything else names no
        # resource and matches no route: 404, and no handler is ever
        # handed a number that is not one. `null` is the one that
        # matters — it is what a page that lost its story would send.
        assert page.status("/api/stories/abc") == 404
        assert page.status("/api/stories/null/export") == 404
        assert page.status("/api/stories/3/messages/null", method="PATCH") == 404

    def test_a_subject_that_is_not_there_answers_404(self, page: Page) -> None:
        # The path parses but names nothing — a story another tab
        # deleted, a provider nothing answers to — and the page is told
        # in the backend's own sentence.
        assert page.refused("GET", "/api/stories/9999")["status"] == 404
        assert page.refused("GET", "/api/providers/nobody")["status"] == 404
        # And a DELETE that removed nothing must not say it did.
        assert page.refused("DELETE", "/api/stories/9999")["status"] == 404


class TestPlaying:
    def test_a_page_that_hangs_up_in_the_wait_frees_the_app_at_once(
        self, server: ModelServer, tmp_path: Path
    ) -> None:
        # Before the first token nothing is written to the page, so its
        # leaving could only be seen at the first frame. The wait asks
        # every tick instead: the reply ends the moment the page is gone,
        # its request cut, and the app's thread is free for the next write
        # instead of queued behind a prefill nobody wants.
        smoothed = serving(
            server, tmp_path, prepare=lambda root: set_config(root, smooth_streaming=True)
        )
        with smoothed as page:
            _hang_up_in_the_wait(page, server)

    def test_a_page_that_hangs_up_in_the_wait_frees_the_app_with_the_pacing_off(
        self, server: ModelServer, tmp_path: Path
    ) -> None:
        # The same, with smoothing off (the harness's default): the tick
        # is the app's contract, not the pacing's — a setting a reader
        # takes for cosmetic must not decide what the server can answer.
        with serving(server, tmp_path) as page:
            _hang_up_in_the_wait(page, server)

    def test_a_line_plays_and_lands_in_the_store(self, page: Page) -> None:
        events = page.play("I unroll the county survey.")
        kinds = [event["type"] for event in events]
        assert kinds[0] == "user_message"
        assert "assistant_text" in kinds
        # the model's row lands as its own event before the end
        assert kinds.index("assistant_message") < kinds.index("done") == len(kinds) - 1
        # The turn's end carries the reply's report as facts and its
        # notice — nothing to say of a reply the model finished.
        assert events[-1]["notice"] == ""
        assert events[-1]["report"]["finish_reason"] == "stop"
        assert events[-1]["report"]["truncated"] is False
        stored = page.store.stories.get_messages(page.story)
        assert stored[-2].body == "I unroll the county survey."
        assert stored[-1].role == "assistant"

    def test_a_first_line_plays_into_the_story_made_for_it(self, page: Page) -> None:
        # The page holds no story yet: it makes one, and plays into it.
        page.play("I unroll the county survey.")
        assert [row["id"] for row in page.get("/api/stories")["items"]] == [page.story]

    def test_a_tool_call_reaches_the_page_split_and_read(
        self, page: Page, server: ModelServer
    ) -> None:
        # The page parses nothing: a call streams as `assistant_tool_call`
        # pieces, the landed message arrives in segments with the call
        # read — and the story's messages carry the same, for a page
        # opening on it.
        server.script = lambda body: "The door creaks.\n```otk-question\nGo in?\n1. Yes\n```"
        events = page.play("I enter the hall.")
        calls = [event for event in events if event["type"] == "assistant_tool_call"]
        assert calls and all(event["tool"] == "question" for event in calls)
        assert "".join(event["text"] for event in calls) == "Go in?\n1. Yes"
        assert all("```" not in e["text"] for e in events if e["type"] == "assistant_text")
        landed = next(e for e in events if e["type"] == "assistant_message")["message"]
        assert landed["segments"] == [
            {"kind": "prose", "text": "The door creaks."},
            {
                "kind": "tool_call",
                "tool": "question",
                "label": "Questions",
                "actor": "user",
                "text": "Go in?\n1. Yes",
                "question": "Go in?",
                "options": ["Yes"],
            },
        ]
        assert page.messages()[-1]["segments"] == landed["segments"]

    def test_a_lines_words_with_the_model_reach_the_page_apart(self, page: Page) -> None:
        # A played line is split by the backend too: its story, and what
        # follows an inliner as words with the model — in the landing and
        # in the messages alike, so the page parses nothing.
        events = page.play("I wait at the gate. /cue make it rain")
        landed = next(e for e in events if e["type"] == "user_message")["message"]
        assert landed["segments"] == [
            {"kind": "prose", "text": "I wait at the gate. /cue"},
            {"kind": "ooc", "text": " make it rain"},
        ]
        assert page.messages()[-2]["segments"] == landed["segments"]

    def test_the_wire_carries_what_the_page_typed(self, page: Page, server: ModelServer) -> None:
        page.play("I mark the river's true course.")
        assert server.requests[-1]["messages"][-1]["content"] == "I mark the river's true course."

    def test_a_refusal_arrives_before_a_byte_of_the_stream(self, page: Page) -> None:
        # Eager validation: invalid syntax leaves the story untouched and
        # the usage line is the whole answer, under the status that says so.
        (problem,) = page.play("/you")
        assert problem["status"] == 422 and problem["detail"]
        assert page.messages() == []

    def test_a_roll_arrives_with_its_note(self, page: Page) -> None:
        # The dice are rolled below both frontends and ride the recorded
        # event as its note — the page never rolls, and never parses the
        # frozen template to find out what fell.
        events = page.play("/roll 1d6 I duck behind the crates.")
        assert events[0]["type"] == "user_message"
        assert "1d6 = " in events[0]["note"]

    def test_a_fresh_take_streams_and_sets_the_old_reply_aside(
        self, page: Page, server: ModelServer
    ) -> None:
        page.play("I listen at the culvert mouth.")
        first = page.messages()[-1]
        server.script = lambda body: "A second take."
        events = page.play("", regenerate=True)
        assert [e["type"] for e in events][-1] == "done"
        assert page.messages()[-1]["body"] == "A second take."
        assert page.store.messages.get_parent(first["id"]) is not None  # still in the tree


class TestWrites:
    def test_a_write_is_answered_with_its_sentence_and_the_thing_as_it_stands(
        self, page: Page
    ) -> None:
        page.play("I listen at the culvert mouth.")
        answer = page.patch(f"/api/stories/{page.story}", {"title": "The Beached Ferry"})
        assert "The Beached Ferry" in answer["notice"]
        assert answer["value"]["title"] == "The Beached Ferry"
        assert page.store.stories.get(page.story).title == "The Beached Ferry"

    def test_a_message_corrected_lands_in_the_store(self, page: Page) -> None:
        page.play("I listen at the culvert mouth.")
        first = page.store.stories.get_messages(page.story)[0]
        answer = page.patch(f"/api/stories/{page.story}/messages/{first.id}", {"body": "I listen."})
        assert answer["notice"] and answer["value"]["body"] == "I listen."
        assert page.store.stories.get_messages(page.story)[0].body == "I listen."

    def test_a_message_pinned_lands_in_the_store(self, page: Page) -> None:
        page.play("I listen at the culvert mouth.")
        first = page.store.stories.get_messages(page.story)[0]
        path = f"/api/stories/{page.story}/messages/{first.id}"
        answer = page.patch(path, {"pinned": True})
        assert answer["notice"] and answer["value"]["pinned"] is True
        assert page.store.messages.get(first.id).pinned
        # a pin is a boolean, never a word that reads as one
        assert page.status(path, method="PATCH", data=b'{"pinned": "yes"}') == 400

    def test_a_malformed_body_is_the_page_s_fault_not_a_crash(self, page: Page) -> None:
        # A text sent as null is a bad request, not a 500; a body naming
        # nothing to change is the backend's refusal, with its sentence.
        page.play("I listen at the culvert mouth.")
        first = page.store.stories.get_messages(page.story)[0]
        path = f"/api/stories/{page.story}/messages/{first.id}"
        assert page.status(path, method="PATCH", data=b'{"body": null}') == 400
        page.refused("PATCH", path, {})

    def test_a_journal_state_has_no_write(self, page: Page) -> None:
        # The entry is a journal's one writable field; a state is the
        # extractor's own, so a state-only body writes nothing.
        page.play("I listen at the culvert mouth.")
        page.refused("PATCH", f"/api/stories/{page.story}/journals/1", {"state": "standing"})

    def test_a_name_in_the_path_arrives_decoded(self, page: Page) -> None:
        # A provider or a model is a NAME, and the page sends it through
        # `encodeURIComponent` — `llama3:8b` is the ordinary local model,
        # not the exotic one. Undecoded, the escape reaches the engine as
        # part of the name and nothing it asks for exists.
        assert page.get("/api/providers/gener%69c") == page.get("/api/providers/generic")

    def test_a_request_s_own_text_never_breaks_its_answer(self, page: Page) -> None:
        # A request carrying a story title, a character name, in Cyrillic
        # or Japanese is answered — malformed here, and said in the body,
        # where the page reads every sentence anyway.
        story = page.made()
        japanese = '{"head": "日本"}'.encode()
        assert page.status(f"/api/stories/{story}", method="PATCH", data=japanese) == 400

    def test_a_null_where_a_value_belongs_is_never_the_string_none(self, page: Page) -> None:
        # Coerced, a null would store the literal title "None" — a
        # malformed request that lands rather than being refused.
        page.play("I listen at the culvert mouth.")
        was = page.store.stories.get(page.story).title
        null = b'{"title": null}'
        assert page.status(f"/api/stories/{page.story}", method="PATCH", data=null) == 400
        assert page.store.stories.get(page.story).title == was

    def test_a_message_is_corrected_only_under_its_own_story(self, page: Page) -> None:
        # The browser addresses any story, so the path is the claim and
        # the chain is the check: a correction that landed on another
        # story's message would rewrite a story nobody was looking at.
        page.play("I listen at the culvert mouth.")
        mine = page.story
        first = page.store.stories.get_messages(mine)[0]
        other = page.post("/api/stories", {"title": "Elsewhere"})["value"]["id"]
        page.refused("PATCH", f"/api/stories/{other}/messages/{first.id}", {"body": "rewritten"})
        assert page.store.stories.get_messages(mine)[0].body == first.body

    def test_a_character_is_deleted_only_under_its_own_story(self, page: Page) -> None:
        page.play("I listen at the culvert mouth.")
        mine = page.story
        stranger = page.store.characters.add(mine, "Stranger")
        other = page.post("/api/stories", {"title": "Elsewhere"})["value"]["id"]
        page.refused("DELETE", f"/api/stories/{other}/characters/{stranger}")
        assert [c.id for c in page.store.characters.list(mine)] == [stranger]
        answer = page.delete(f"/api/stories/{mine}/characters/{stranger}")
        assert answer["notice"] and answer["value"] is None
        assert page.store.characters.list(mine) == []

    def test_a_null_where_a_number_belongs_is_the_page_s_fault(self, page: Page) -> None:
        # Malformed, not broken: a 400 and no crash filed. Anything that
        # scans this port can send one.
        story = page.made()
        null = b'{"head": null}'
        assert page.status(f"/api/stories/{story}", method="PATCH", data=null) == 400

    def test_nothing_to_undo_is_an_answer_the_page_shows(self, page: Page) -> None:
        # An expected answer: the status that says why, and the sentence —
        # the page reads the status, never the wording.
        story = page.made()
        problem = page.refused("POST", f"/api/stories/{story}/undos", if_match="*")
        assert problem["status"] == 409

    def test_a_setting_no_story_has_is_refused_with_a_sentence(self, page: Page) -> None:
        # The path is real, and a name it carries that names nothing is the
        # backend's own sentence — never a bare unknown path.
        story = page.made()
        problem = page.refused("PATCH", f"/api/stories/{story}/settings/plan", {"enabled": True})
        assert problem["status"] == 404

    def test_the_composer_history_is_the_store_s(self, page: Page) -> None:
        # The page records what was submitted and reads it back most
        # recent first — the same lines the terminal prompt walks, so a
        # reload starts with the history it left.
        page.post("/api/history", {"text": "I listen at the culvert mouth."})
        page.post("/api/history", {"text": "/stories"})
        recent = page.get("/api/history")["items"]
        assert recent[:2] == ["/stories", "I listen at the culvert mouth."]

    def test_the_story_a_launch_resumes_is_the_page_s_to_write(self, page: Page) -> None:
        # Nothing played means no message to land on — and a resume never
        # used one: the story the page plays is written back by id alone.
        page.play("I listen at the culvert mouth.")
        played = page.story
        blank = page.post("/api/stories", {"title": "Blank"})["value"]["id"]
        assert page.patch("/api/settings", {"last_story": blank})["value"]["last_story"] == blank
        assert page.get("/api/settings")["last_story"] == blank
        assert page.get(f"/api/stories/{blank}/messages")["items"] == []
        page.patch("/api/settings", {"last_story": played})
        assert page.get("/api/settings")["last_story"] == played

    def test_a_story_rewound_keeps_what_came_after_in_the_tree(self, page: Page) -> None:
        page.play("I listen at the culvert mouth.")
        page.play("I wade in.")
        first = page.messages()[0]["id"]
        rewound = page.patch(f"/api/stories/{page.story}", {"head": first}, if_match="*")
        assert rewound["value"]["head"] == first and rewound["notice"]
        assert [m["id"] for m in page.messages()] == [first]
        assert len(page.store.stories.get_messages_ids(page.story)) == 1


class TestStorySettings:
    """The story's settings over HTTP: every setting with the same
    fields, a PATCH of one field with the backend's refusals as answers
    and a malformed flag as a bad request, the shared reminder and the
    prompts at their own paths."""

    FIELDS = (
        "name",
        "label",
        "tool",
        "enabled",
        "injection_label",
        "prompt_name",
        "injection_position",
        "allowed_positions",
        "reminder_text",
        "display_notes",
        "layout",
    )

    def test_every_setting_answers_the_same_questions(self, page: Page) -> None:
        story = page.made()
        settings = page.get(f"/api/stories/{story}/settings")["items"]
        assert settings and all(tuple(row) == self.FIELDS for row in settings)
        by_name = {row["name"]: row for row in settings}
        # What a setting has none of is null: the questions tool no
        # reminder and nothing to display, a reminder no tool.
        assert by_name["allow_questions"]["tool"] == "question"
        assert by_name["allow_questions"]["reminder_text"] is None
        assert by_name["allow_questions"]["display_notes"] is None
        assert by_name["allow_assistant_notes"]["display_notes"] is True
        assert by_name["use_story_reminder"]["tool"] is None
        # what each injects is named, lowercase
        assert by_name["allow_assistant_notes"]["injection_label"] == "assistant notes"
        assert by_name["use_story_reminder"]["injection_label"] == "story reminder"
        assert by_name["use_story_reminder"]["reminder_text"] == ""
        places = by_name["use_story_reminder"]["allowed_positions"]
        assert [place["value"] for place in places] == list(range(1, 9))
        assert places[0]["text"] == "2nd last"
        # the layout is a value, not a switch: always on, no position,
        # the manuscript until the story says otherwise; a switch has none
        assert by_name["layout"]["enabled"] is True
        assert by_name["layout"]["allowed_positions"] == []
        assert by_name["layout"]["layout"] == "manuscript"
        assert by_name["allow_questions"]["layout"] is None
        # the emulation is off until the story switches it on, as every
        # switch is, in the system message by default, its text named
        emulation = by_name["emulate_text_completion"]
        assert (emulation["enabled"], emulation["tool"]) == (False, None)
        assert emulation["injection_position"] == {"value": "system", "text": "system"}
        assert emulation["injection_label"] == "text completion emulation"
        assert [p["value"] for p in emulation["allowed_positions"]] == ["system", *range(1, 9)]
        assert (emulation["reminder_text"], emulation["display_notes"]) == (None, None)

    def test_the_layout_is_patched_as_a_value_and_the_store_follows(self, page: Page) -> None:
        story = page.made()
        path = f"/api/stories/{story}/settings/layout"
        answer = page.patch(path, {"layout": "canvas"})
        assert answer["notice"] and answer["value"]["layout"] == "canvas"
        row = next(s for s in page.store.stories.get_settings(story) if s.name == "layout")
        assert row.layout is Layout.CANVAS
        # it has no switch to move, and takes the four names alone
        assert page.refused("PATCH", path, {"enabled": False})["status"] == 422
        assert page.status(path, method="PATCH", data=b'{"layout": "scroll"}') == 400
        # the story carries the two derived facts: the look, and the wire —
        # the chat's, on a model that cannot complete text
        facts = page.facts()
        assert (facts["layout"], facts["mode"], facts["completion_type"]) == (
            "canvas",
            "story",
            "chat",
        )
        assert page.get("/api/app")["model"]["can_complete_text"] is False
        # the novel stored on such a model is played as the canvas over the chat
        page.patch(path, {"layout": "novel"})
        facts = page.facts()
        assert (facts["layout"], facts["mode"], facts["completion_type"]) == (
            "novel",
            "story",
            "chat",
        )

    def test_the_story_says_how_a_line_typed_now_would_join(self, page: Page) -> None:
        # nothing played: the story is begun
        assert page.facts()["next_join"] is None
        page.play("I listen at the culvert mouth.")
        assert page.facts()["next_join"] == "paragraph"
        # read as the canvas, the rows played in the manuscript still stand apart
        page.patch(f"/api/stories/{page.story}/settings/layout", {"layout": "canvas"})
        assert page.facts()["next_join"] == "paragraph"
        # a row played in the canvas: the next line runs on from it
        page.play("I wade in.")
        assert page.facts()["next_join"] == "inline"

    def test_a_patch_moves_the_fields_given_and_the_store_follows(self, page: Page) -> None:
        story = page.made()
        path = f"/api/stories/{story}/settings/use_story_reminder"
        answer = page.patch(path, {"enabled": True, "position": 3, "reminder_text": "Rain."})
        row = answer["value"]
        assert answer["notice"]
        assert (row["enabled"], row["injection_position"]["value"], row["reminder_text"]) == (
            True,
            3,
            "Rain.",
        )
        stored = {s.name: s for s in page.store.stories.get_settings(story)}
        assert stored["use_story_reminder"].reminder_text == "Rain."
        notes = f"/api/stories/{story}/settings/allow_assistant_notes"
        assert page.patch(notes, {"display_notes": False})["value"]["display_notes"] is False
        assert page.store.stories.get_settings(story)[-1].display_notes is False

    def test_what_a_setting_does_not_take_is_a_refusal_not_a_fault(self, page: Page) -> None:
        story = page.made()
        path = f"/api/stories/{story}/settings/allow_questions"
        assert page.refused("PATCH", path, {"position": 9})["status"] == 422
        assert page.refused("PATCH", path, {"display_notes": False})["status"] == 422

    def test_a_flag_that_is_not_a_boolean_is_a_bad_request(self, page: Page) -> None:
        story = page.made()
        path = f"/api/stories/{story}/settings/allow_assistant_notes"
        assert page.status(path, method="PATCH", data=b'{"display_notes": "yes"}') == 400
        assert page.status(path, method="PATCH", data=b'{"enabled": 1}') == 400

    def test_what_is_not_a_position_is_a_bad_request(self, page: Page) -> None:
        # "system" or a count from 1 is a position (which the setting may
        # still refuse); anything else is a malformed request.
        story = page.made()
        path = f"/api/stories/{story}/settings/allow_questions"
        for body in (
            b'{"position": "end"}',
            b'{"position": 0}',
            b'{"position": true}',
            b'{"position": "2"}',
        ):
            assert page.status(path, method="PATCH", data=body) == 400, body

    def test_the_context_preview_carries_the_injections(self, page: Page) -> None:
        page.play("I listen at the culvert mouth.")
        story = page.story
        assert page.get(f"/api/stories/{story}/context")["injections"] == []
        page.patch(
            f"/api/stories/{story}/settings/allow_questions",
            {"enabled": True, "position": "system"},
        )
        page.patch(
            f"/api/stories/{story}/settings/use_story_reminder",
            {"enabled": True, "position": 2, "reminder_text": "Rain."},
        )
        injections = page.get(f"/api/stories/{story}/context")["injections"]
        assert [(i["label"], i["position"], i["position_text"]) for i in injections] == [
            ("questions", "system", "system"),
            ("story reminder", 2, "3rd last"),
        ]
        assert all(i["tokens"] > 0 for i in injections)

    def test_the_context_preview_carries_the_cards_and_what_they_inject(self, page: Page) -> None:
        page.play("I listen at the culvert mouth.")
        story = page.story
        assert page.get(f"/api/stories/{story}/context")["cards"] == []
        wren = {"name": "Wren", "system_prompt": "Never lie.", "reminder": "Riddles."}
        card = page.post("/api/cards", {**wren, "reminder_depth": 1})["value"]["id"]
        page.post(f"/api/stories/{story}/cards", {"card_id": card, "persona": "Alex"}, if_match="*")
        preview = page.get(f"/api/stories/{story}/context")
        (sent,) = preview["cards"]
        assert sent["name"] == "Wren" and sent["tokens"] > 0
        assert [i["position"] for i in preview["injections"]] == ["system", 1]

    def test_the_shared_reminder_has_a_path_of_its_own(self, page: Page) -> None:
        assert page.get("/api/shared-reminder") == {"text": ""}
        answer = page.patch("/api/shared-reminder", {"text": "Stay grim."})
        assert answer == {"value": {"text": "Stay grim."}, "notice": answer["notice"]}
        assert answer["notice"]
        assert page.get("/api/shared-reminder") == {"text": "Stay grim."}
        assert page.store.settings.get_shared_reminder() == "Stay grim."

    def test_a_tools_prompt_is_read_and_written_at_its_tool_s_path(self, page: Page) -> None:
        path = "/api/tools/question/prompt"
        shipped = page.get(path)["text"]
        assert shipped and page.get(path)["name"] == "tool_questions_prompt"
        answer = page.patch(path, {"text": "ASK ANEW"})
        assert answer["notice"] and answer["value"]["text"] == "ASK ANEW"
        assert page.get(path)["text"] == "ASK ANEW"
        assert "ASK ANEW" in Paths.resolve(page.root).prompts_file.read_text(encoding="utf-8")
        page.patch(path, {"text": ""})
        assert page.get(path)["text"] == shipped
        # a name no tool answers to
        assert page.refused("GET", "/api/tools/plan/prompt")["status"] == 404

    def test_a_prompt_no_tool_owns_is_read_and_written_by_its_key(self, page: Page) -> None:
        path = "/api/prompts/emulate_text_completion_prompt"
        shipped = page.get(path)["text"]
        assert shipped
        page.patch(path, {"text": "Go on."})
        assert page.get(path)["text"] == "Go on."
        assert page.delete(path)["value"]["text"] == shipped
        # a key no setting sends, the reader's own text, and a tool's
        for key in ("plan", "use_story_reminder", "tool_questions_prompt"):
            assert page.refused("GET", f"/api/prompts/{key}")["status"] == 404, key


class TestTheFlows:
    """The writes whose result outlives their request: a forced pass the
    page polls for, a card read before it is brought in, a document landed
    as a story."""

    def test_a_forced_extraction_is_started_and_its_report_polled(self, page: Page) -> None:
        page.play("I listen at the culvert mouth.")
        page.play("I wade into the dark after the voice.")
        story = page.story
        status, headers, started = page.answer("POST", f"/api/stories/{story}/extraction")
        assert status == 202
        assert headers["location"] == f"/api/stories/{story}/extraction"
        assert started["value"]["status"] == "running"
        report = _polled(page, story)
        assert report  # the scripted server closes a scene; the report says so
        ids = [m.id for m in page.store.stories.get_messages(story)]
        assert page.store.scenes.get_current(story, ids)

    def test_a_card_is_read_for_the_story_then_brought_in(self, page: Page) -> None:
        page.play("I listen at the culvert mouth.")
        story = page.story
        card = {"data": base64.b64encode(SERAPHINA.read_bytes()).decode(), "file_name": "s.png"}
        checked = page.post(f"/api/stories/{story}/cards?validate_only=true", card)
        assert checked["name"] == "Seraphina" and checked["block_tokens"] > 0
        assert page.store.characters.list(story) == []  # read, nothing stored
        status, headers, imported = page.answer(
            "POST", f"/api/stories/{story}/cards", {**card, "persona": "Maren"}, if_match="*"
        )
        assert status == 201 and "Seraphina" in imported["notice"]
        character = imported["value"]["character_id"]
        assert headers["location"] == f"/api/stories/{story}/characters/{character}"
        assert "Seraphina" in {c.name for c in page.store.characters.list(story)}

    def test_a_card_brought_in_without_a_persona_is_the_page_s_fault(self, page: Page) -> None:
        story = page.made()
        card = {"data": base64.b64encode(SERAPHINA.read_bytes()).decode(), "file_name": "s.png"}
        body = json.dumps(card).encode()
        assert page.status(f"/api/stories/{story}/cards", method="POST", data=body) in {400, 428}
        assert page.store.characters.list(story) == []

    def test_an_imported_document_starts_the_pass_the_page_watches(self, page: Page) -> None:
        status, headers, landed = page.answer(
            "POST",
            "/api/imports",
            {"text": "A lantern swings on the pier.\n\nNobody holds it.", "file_name": "pier.txt"},
        )
        story = landed["value"]["story_id"]
        assert status == 201 and headers["location"] == f"/api/stories/{story}"
        # memoryless shape: memory builds now
        assert landed["value"]["extraction"]["status"] == "running"
        assert _polled(page, story)
        assert [m.body for m in page.store.stories.get_messages(story)] == [
            "A lantern swings on the pier.",
            "Nobody holds it.",
        ]

    def test_a_story_is_exported_as_one_document(self, page: Page) -> None:
        page.play("I listen at the culvert mouth.")
        exported = page.get(f"/api/stories/{page.story}/export")
        assert exported["name"] and "I listen at the culvert mouth." in exported["text"]


def _polled(page: Page, story: int, timeout: float = 30.0) -> str:
    """The report as the page's own poll reads it — while the pass runs
    nothing, then its sentence."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = page.get(f"/api/stories/{story}/extraction")
        if found["status"] != "running":
            return str(found["report"])
        time.sleep(0.1)
    raise AssertionError("the pass never reported")


class TestTheLoreOverTheWire:
    """The dossier's reads and corrections, each at its own path with the
    ids the row carries: a scene's title and summary, a character's
    description, a journal record's entry, a character folded into
    another — and a forced pass given up."""

    def test_a_scene_is_read_and_corrected_at_its_path(self, page: Page) -> None:
        story, scene, _, _ = _seeded(page)
        listed = page.get(f"/api/stories/{story}/scenes")["items"]
        assert [(s["id"], s["no"], s["summary"]) for s in listed] == [(scene, 1, "s")]
        answer = page.patch(f"/api/stories/{story}/scenes/{scene}", {"title": "The Culvert"})
        assert answer["value"]["title"] == "The Culvert" and answer["notice"]
        assert page.get(f"/api/stories/{story}/scenes/{scene}")["summary"] == "s"  # untouched

    def test_a_character_and_a_journal_entry_are_corrected_at_their_paths(self, page: Page) -> None:
        story, _, keeper, journal = _seeded(page)
        path = f"/api/stories/{story}/characters/{keeper}"
        assert page.patch(path, {"description": "Keeps the light."})["value"]["description"] == (
            "Keeps the light."
        )
        entry = page.patch(f"/api/stories/{story}/journals/{journal}", {"entry": "She waited."})
        assert entry["value"]["entry"] == "She waited."
        assert page.get(f"/api/stories/{story}/journals")["items"][0]["entry"] == "She waited."

    def test_a_character_is_folded_into_another(self, page: Page) -> None:
        story, _, keeper, _ = _seeded(page)
        twin = page.store.characters.add(story, "The Keeper")
        folded = page.post(f"/api/stories/{story}/characters/{twin}/merges", {"into": keeper})
        assert folded["value"]["id"] == keeper and folded["notice"]
        assert [c.id for c in page.store.characters.list(story)] == [keeper]

    def test_a_row_of_another_story_is_not_found_under_this_one(self, page: Page) -> None:
        _, scene, _, _ = _seeded(page)
        other = page.post("/api/stories", {"title": "Elsewhere"})["value"]["id"]
        page.refused("PATCH", f"/api/stories/{other}/scenes/{scene}", {"title": "x"})

    def test_giving_up_a_pass_none_is_running_is_refused(self, page: Page) -> None:
        story = page.made()
        assert page.refused("DELETE", f"/api/stories/{story}/extraction")["status"] == 409


def _seeded(page: Page) -> tuple[int, int, int, int]:
    """A played story with a scene closed over it, a character, and the
    character's journal record of the scene, through the scenario's own
    store: (story, scene, character, journal)."""
    page.play("I listen at the culvert mouth.")
    story = page.made()
    ids = page.store.stories.get_messages_ids(story)
    scene = page.store.scenes.add(
        story, start_message_id=ids[0], end_message_id=ids[-1], summary="s"
    )
    keeper = page.store.characters.add(story, "Keeper")
    journal = page.store.journals.add(
        story, scene_id=scene, character_id=keeper, entry="e", state="st"
    )
    return story, scene, keeper, journal


class TestWhoIsAsking:
    """With no password set the page asks nobody who they are — but a
    page on another origin, in the same browser, must still not be able
    to drive it. A write can do its damage without ever reading the
    answer; a read cannot, and is not asked."""

    def test_a_cross_site_write_is_refused(self, page: Page) -> None:
        # What an auto-submitting form on another site sends. It cannot
        # forge `Sec-Fetch-Site`, and a form's content type is never
        # application/json without a preflight this server never answers.
        story = page.made()
        assert (
            page.status(
                f"/api/stories/{story}",
                method="PATCH",
                headers={
                    "Sec-Fetch-Site": "cross-site",
                    "Origin": "http://evil.example",
                    "Content-Type": "text/plain;charset=UTF-8",
                },
                data=b'{"title": "Pwned"}',
            )
            == 403
        )
        assert page.store.stories.get(story).title != "Pwned"

    def test_a_cross_origin_write_is_refused_without_the_fetch_metadata(self, page: Page) -> None:
        assert (
            page.status(
                "/api/providers/generic",
                method="PATCH",
                headers={"Origin": "http://evil.example"},
                data=b'{"url": "http://attacker.example/v1"}',
            )
            == 403
        )

    def test_the_page_s_own_writes_are_let_through(self, page: Page) -> None:
        # What the page itself sends — same-origin fetch metadata.
        story = page.made()
        assert (
            page.status(
                f"/api/stories/{story}",
                method="PATCH",
                headers={"Sec-Fetch-Site": "same-origin", "Origin": page.url},
                data=b'{"title": "The Lock"}',
            )
            == 200
        )

    def test_following_a_link_from_another_site_opens_the_page(self, page: Page) -> None:
        # A read moves nothing and its answer cannot be seen from the page
        # that caused it, so it is not asked where it came from — or a
        # reader could not open otaku from a link somewhere else.
        navigated = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate"}
        assert page.status("/", headers=navigated) == 200
        fetched = {"Sec-Fetch-Site": "cross-site", "Origin": "http://elsewhere.example"}
        assert page.status("/api/app", headers=fetched) == 200

    def test_a_request_addressed_to_another_name_is_misdirected(self, page: Page) -> None:
        # DNS rebinding is the one attack a loopback bind does not stop:
        # the name in the request is what gives it away.
        assert page.status("/api/app", headers={"Host": "attacker.example"}) == 421
        assert page.status("/api/app", headers={"Host": "localhost"}) == 200

    def test_a_malformed_length_is_a_bad_request_not_a_crash(self, page: Page) -> None:
        where = urlsplit(page.url)
        with socket.create_connection((where.hostname, where.port), timeout=5) as scanner:
            scanner.sendall(
                b"POST /api/history HTTP/1.1\r\nHost: localhost\r\nContent-Length: abc\r\n\r\n{}"
            )
            answered = scanner.recv(64)
        assert answered.startswith(b"HTTP/1.")


class TestThePicker:
    def test_the_generic_provider_is_in_the_panel_with_what_it_serves(self, page: Page) -> None:
        # The scenario's own provider is the [generic] section: a url and
        # nothing more. The app is PLAYING on it, so a panel without it
        # is a panel with no way back to the story's own model.
        panel = page.get("/api/providers")
        mine = next(p for p in panel["providers"] if p["id"] == "generic")
        assert [model["name"] for model in mine["models"]] == ["test-model"]
        assert (mine["asked"], mine["answered"], mine["failure"]) == (True, True, None)
        assert mine["locality"] == "unknown"  # a url: nobody can say where it points
        assert mine["url"]  # the panel's field
        # What it can do rides the provider, never a model: the generic
        # provider manages nothing, counts nothing exactly, marks no
        # cache, and sends every parameter the app knows.
        capabilities = mine["capabilities"]
        assert (
            capabilities["tokenizer"],
            capabilities["prompt_cache"],
            capabilities["model_management"],
        ) == (False, False, False)
        assert sorted(capabilities["supported_params"]) == sorted(PARAMETERS)
        # the vocabulary's order, which a provider's and a model's lists are read in
        assert panel["parameters"] == list(PARAMETERS)
        model = mine["models"][0]
        assert model["capabilities"] is None  # it says nothing of its models
        # The info report's two rows ride the model, in the report's words:
        # a provider that says nothing reads unknown.
        assert (model["reasoning_words"], model["capability_words"]) == ("unknown", "unknown")
        assert model["context_loaded"] is None  # nothing loads on a generic url
        unanswered = next(p for p in panel["providers"] if p["id"] == "llamacpp")
        assert unanswered["capabilities"] is None
        assert unanswered["answered"] is False and unanswered["failure"]  # the page is told why

    def test_the_model_in_use_is_named_where_the_panel_rows_it(self, page: Page) -> None:
        model = page.get("/api/app")["model"]
        assert (model["provider"], model["listed_name"]) == ("generic", "test-model")

    def test_the_panel_says_where_each_provider_runs(self, page: Page) -> None:
        # The vocabulary the page's captions and the demo's fake read:
        # the generic provider unable to say, a provider on this machine,
        # a catalog over the wire — in the panel's order, the engines on
        # this machine first, the generic provider between them and the
        # catalogs.
        panel = page.get("/api/providers")
        assert [p["id"] for p in panel["providers"]][:6] == [
            "llamacpp",
            "koboldcpp",
            "ollama",
            "omlx",
            "lmstudio",
            "generic",
        ]
        by_name = {p["id"]: p["locality"] for p in panel["providers"]}
        assert by_name["generic"] == "unknown"
        assert by_name["llamacpp"] == "local"
        assert by_name["openrouter"] == "remote"

    def test_the_two_phases_ask_the_panel_between_them(self, page: Page) -> None:
        # The page asks in two phases, each listing the whole panel in its
        # order and marking which it asked: every provider is asked by one
        # phase, and the generic provider by the second, the url that could
        # point anywhere going with the catalogs.
        whole = [p["id"] for p in page.get("/api/providers")["providers"]]
        local = page.get("/api/providers?scope=local")["providers"]
        cloud = page.get("/api/providers?scope=cloud")["providers"]
        assert [p["id"] for p in local] == [p["id"] for p in cloud] == whole
        asked_locally = {p["id"] for p in local if p["asked"]}
        asked_in_the_cloud = {p["id"] for p in cloud if p["asked"]}
        assert asked_in_the_cloud >= {"generic", "openrouter", "nanogpt"}
        assert asked_locally.isdisjoint(asked_in_the_cloud)
        assert asked_locally | asked_in_the_cloud == set(whole)

    def test_a_managed_engine_s_model_is_loaded_at_its_path(self, tmp_path: Path) -> None:
        managed = ModelServer(managed=True)
        try:
            with serving(managed, tmp_path, provider="ollama") as page:
                models = page.get("/api/providers/ollama/models")["items"]
                assert [m["name"] for m in models] == [MODEL]
                answer = page.patch(f"/api/providers/ollama/models/{MODEL}", {"loaded": True})
                assert answer["value"]["state"] == "loaded" and answer["notice"]
                assert "/api/generate" in managed.posts  # the engine was asked to load it
        finally:
            managed.close()

    def test_a_probe_asks_nobody(self, page: Page) -> None:
        listed = page.get("/api/providers?probe=false")["providers"]
        assert not any(p["asked"] for p in listed)


class TestOpenToTheNetwork:
    """`[web] host = 0.0.0.0` is the documented way to reach otaku from
    another machine. The rebinding guard must not be what stops it: a
    wildcard bind has already decided to answer everyone, and the
    address a LAN client names is one otaku cannot know."""

    def test_a_wildcard_bind_answers_to_any_name(self, wide: Page) -> None:
        assert wide.status("/", headers={"Host": "192.168.178.21:9600"}) == 200
        assert wide.status("/api/app", headers={"Host": "otaku.lan"}) == 200

    def test_a_write_from_the_app_one_port_over_is_still_refused(self, wide: Page) -> None:
        # The neighbour this guard is for: another app on the same host,
        # which shares the name but not the origin.
        assert (
            wide.status(
                "/api/providers/generic",
                method="PATCH",
                headers={"Host": "otaku.lan:9600", "Origin": "http://otaku.lan:8080"},
                data=b'{"url": "http://attacker.example/v1"}',
            )
            == 403
        )

    def test_the_page_s_own_write_is_let_through(self, wide: Page) -> None:
        story = wide.made()
        assert (
            wide.status(
                f"/api/stories/{story}",
                method="PATCH",
                headers={"Host": "otaku.lan:9600", "Origin": "http://otaku.lan:9600"},
                data=b'{"title": "From the page itself"}',
            )
            == 200
        )


class TestStopping:
    def test_a_request_that_outlives_the_serving_is_answered(self, page: Page) -> None:
        """A browser holds its connection open, so a request can arrive
        after the serving is over. Not a crash and not a wait: the
        reader stopped otaku, which is an answer a request can get."""
        where = urlsplit(page.url)
        held = HTTPConnection(where.hostname, where.port, timeout=10)
        held.request("GET", "/api/app")
        held.getresponse().read()  # kept alive
        page.stop()
        held.request("GET", "/api/app")
        answered = held.getresponse()
        answered.read()
        held.close()
        assert answered.status == 503


class TestPictures:
    """The page's side of a picture: the play body's files, the two
    picture routes, and what a message and the story say. The fixture's
    model cannot see, which is what makes the refusal the sentence; the
    files themselves are put in the folder through the scenario's own
    store."""

    def test_the_story_says_whether_a_picture_may_ride(self, page: Page) -> None:
        assert page.facts()["can_attach"] is False

    def test_a_message_without_pictures_carries_an_empty_list(self, page: Page) -> None:
        page.play("I listen.")
        assert [message["attachments"] for message in page.messages()] == [[], []]

    def test_a_message_carries_how_it_joins_the_one_before(self, page: Page) -> None:
        # a typed line and a chat completion's reply: paragraphs both
        page.play("I listen.")
        assert [message["join_type"] for message in page.messages()] == ["paragraph"] * 2

    def test_files_for_a_model_that_cannot_see_are_refused_eagerly(self, page: Page) -> None:
        (problem,) = page.play("look", files=[_file(b"x")])
        # the model in use rules it out: a state, not the request's fault
        assert (problem["status"], problem["detail"]) == (409, "This model cannot see pictures.")
        assert page.messages() == []

    def test_a_file_that_is_not_base64_is_a_400(self, page: Page) -> None:
        story = page.made()
        body = json.dumps({"line": "look", "files": [{"name": "x", "data": "@@@"}]}).encode()
        assert page.status(f"/api/stories/{story}/messages", method="POST", data=body) == 400

    def test_a_stored_picture_and_its_thumbnail_are_kept_by_this_browser_alone(
        self, page: Page
    ) -> None:
        # A file never changes once written, so the browser keeps it — but
        # it sits behind the sign-in, so no cache between may.
        name = page.store.files.add(Owner.STORY, 1, CAT.read_bytes(), "image/jpeg", thumb=b"thumb")
        status, headers, body = _fetch(page, f"/api/pictures/{name}")
        assert (status, body) == (200, CAT.read_bytes())
        assert headers["Content-Type"] == "image/jpeg"
        assert "immutable" in headers["Cache-Control"]
        assert "private" in headers["Cache-Control"] and "public" not in headers["Cache-Control"]
        status, headers, body = _fetch(page, f"/api/pictures/{name}/thumbnail")
        assert (status, headers["Content-Type"], body) == (200, "image/jpeg", b"thumb")
        assert "private" in headers["Cache-Control"]

    def test_a_name_nothing_is_stored_under_is_404(self, page: Page) -> None:
        assert page.status("/api/pictures/pic-0001-20260101-deadbeef.jpg") == 404
        assert page.status("/api/pictures/pic-0001-20260101-deadbeef.jpg/thumbnail") == 404


class TestTheCodexOverTheWire:
    """All characters at its paths: a card made three ways — by hand, from
    a card file (201 where one is made, 200 where the codex held it), from
    a story's character — vetted, corrected, merged and deleted, a picture
    added, the unlinked listed; a story's character linked by `card_id`,
    and a story started with cards. A card's id is digits, as every row
    id is."""

    def test_a_card_made_by_hand_lives_where_its_location_says(self, page: Page) -> None:
        status, headers, answer = page.answer(
            "POST", "/api/cards", {"name": "Mira", "aliases": ["the thief"], "examples": ["a"]}
        )
        card = answer["value"]
        assert status == 201 and headers["location"] == f"/api/cards/{card['id']}"
        assert page.get(headers["location"])["examples"] == ["a"]
        (row,) = page.get("/api/cards")["items"]
        assert (row["name"], row["story_count"], row["pictures"]) == ("Mira", 0, [])
        assert page.get("/api/cards?query=thief")["items"][0]["id"] == card["id"]

    def test_a_card_file_is_vetted_then_made_then_held(self, page: Page) -> None:
        data = base64.b64encode(SERAPHINA.read_bytes()).decode()
        body = {"data": data, "file_name": "seraphina.png"}
        check = page.post("/api/cards?validate_only=true", body)
        assert (check["name"], check["has_picture"], check["same_card"]) == (
            "Seraphina",
            True,
            None,
        )
        status, headers, made = page.answer("POST", "/api/cards", body)
        assert status == 201 and made["value"]["made"] is True
        assert headers["location"] == f"/api/cards/{made['value']['card']['id']}"
        status, _, again = page.answer("POST", "/api/cards", body)
        assert status == 200 and again["value"]["made"] is False

    def test_a_card_is_corrected_merged_and_deleted(self, page: Page) -> None:
        a = page.post("/api/cards", {"name": "A"})["value"]["id"]
        b = page.post("/api/cards", {"name": "B"})["value"]["id"]
        fixed = page.patch(f"/api/cards/{a}", {"tagline": "first", "alternate_greetings": ["hi"]})
        assert fixed["value"]["tagline"] == "first"
        assert fixed["value"]["alternate_greetings"] == ["hi"]
        merged = page.post(f"/api/cards/{a}/merges", {"into": b})
        assert merged["value"]["aliases"] == ["A"]
        assert page.delete(f"/api/cards/{b}")["value"] is None
        assert page.get("/api/cards")["items"] == []
        assert page.refused("GET", f"/api/cards/{b}")["status"] == 404

    def test_a_picture_is_added_to_a_card_and_removed(self, page: Page) -> None:
        card = page.post("/api/cards", {"name": "Mira"})["value"]["id"]
        file = {"name": "cat.jpg", "data": base64.b64encode(CAT.read_bytes()).decode()}
        (picture,) = page.post(f"/api/cards/{card}/pictures", file)["value"]["pictures"]
        assert page.status(f"/api/pictures/{picture['file']}") == 200
        path = f"/api/cards/{card}/pictures/{picture['file']}"
        assert page.delete(path)["value"]["pictures"] == []
        assert page.refused("DELETE", path)["status"] == 404

    def test_a_reminders_depth_is_a_whole_number_from_one(self, page: Page) -> None:
        assert page.refused("POST", "/api/cards", {"name": "Mira", "reminder_depth": 0})
        card = page.post("/api/cards", {"name": "Mira", "reminder": "r"})["value"]
        assert card["reminder_depth"] == 2
        assert page.refused("PATCH", f"/api/cards/{card['id']}", {"reminder_depth": 0})
        body = b'{"name": "Mira", "reminder_depth": "2"}'
        assert page.status("/api/cards", method="POST", data=body) == 400

    def test_a_character_is_promoted_linked_and_unlinked(self, page: Page) -> None:
        story = page.made()
        keeper = page.store.characters.add(story, "Keeper")
        (row,) = page.get("/api/cards/unlinked")["items"]
        assert (row["name"], row["story_id"], row["journal_count"]) == ("Keeper", story, 0)
        status, _, made = page.answer(
            "POST", "/api/cards", {"story_id": story, "character_id": keeper}
        )
        assert status == 201 and page.get("/api/cards/unlinked")["items"] == []
        path = f"/api/stories/{story}/characters/{keeper}"
        assert page.patch(path, {"card_id": None})["value"]["card_id"] is None
        linked = page.patch(path, {"card_id": made["value"]["id"]})["value"]
        assert (linked["card_id"], linked["card_name"]) == (made["value"]["id"], "Keeper")

    def test_a_story_starts_with_cards(self, page: Page) -> None:
        mira = page.post("/api/cards", {"name": "Mira", "greeting": "Hello."})["value"]["id"]
        status, _, made = page.answer(
            "POST", "/api/stories", {"card_ids": [mira], "card_pictures": [], "persona": "you"}
        )
        story = made["value"]["id"]
        assert status == 201 and made["value"]["title"] == "Mira"
        rows = page.get(f"/api/stories/{story}/messages")["items"]
        assert [row["kind"] for row in rows] == ["card", "dialogue"]
        bad = {"card_ids": ["one"]}
        assert page.status("/api/stories", method="POST", data=json.dumps(bad).encode()) == 400

    def test_a_card_id_is_digits(self, page: Page) -> None:
        assert page.status("/api/cards/mira") == 404

    def test_a_card_made_with_nothing_is_named_for_the_reader_to_replace(self, page: Page) -> None:
        status, _, made = page.answer("POST", "/api/cards", {})
        assert status == 201 and made["value"]["name"] == NEW_CARD_NAME

    def test_a_card_moves_in_the_readers_order(self, page: Page) -> None:
        a, b, c = (page.post("/api/cards", {"name": n})["value"]["id"] for n in "ABC")
        page.patch(f"/api/cards/{c}", {"before": a})
        assert [row["id"] for row in page.get("/api/cards")["items"]] == [c, a, b]
        page.patch(f"/api/cards/{c}", {"before": None})
        assert [row["id"] for row in page.get("/api/cards")["items"]] == [a, b, c]
        # a move that cannot be made takes the correction with it
        assert page.refused("PATCH", f"/api/cards/{a}", {"tagline": "x", "before": a})
        assert page.get(f"/api/cards/{a}")["tagline"] == ""
        body = json.dumps({"before": "B"}).encode()
        assert page.status(f"/api/cards/{a}", method="PATCH", data=body) == 400

    def test_a_link_and_a_promotion_vetted_count_the_copies(self, page: Page) -> None:
        story = page.made()
        page.store.stories.append(story, Message(role="user", body="I enter."), after=None)
        maren = page.store.characters.add(story, "Maren")
        fork = page.post(f"/api/stories/{story}/forks", {})["value"]["id"]
        assert [row["id"] for row in page.get(f"/api/stories/{story}/forks")["items"]] == [fork]
        card = page.post("/api/cards", {"name": "Maren"})["value"]["id"]
        path = f"/api/stories/{story}/characters/{maren}"
        assert page.patch(f"{path}?validate_only=true", {"card_id": card}) == {"copies": 1}
        assert page.get(path)["card_id"] is None  # vetted, nothing stored
        promoting = {"story_id": story, "character_id": maren}
        assert page.post("/api/cards?validate_only=true", promoting) == {"copies": 1}
        page.patch(path, {"card_id": card, "with_copies": True})
        (copy,) = page.get(f"/api/stories/{fork}/characters")["items"]
        assert copy["card_id"] == card

    def test_a_copy_is_read_and_corrected_field_by_field(self, page: Page) -> None:
        story = page.made()
        mira = page.store.characters.add(
            story, "Mira", profile="user = 'you'\n\ndescription = 'keeps the light'"
        )
        path = f"/api/stories/{story}/characters/{mira}"
        read = page.get(path)
        assert read["profile_fields"]["description"] == "keeps the light"
        changed = page.patch(path, {"profile_fields": {"scenario": "a lighthouse"}})["value"]
        assert changed["profile_fields"]["scenario"] == "a lighthouse"
        assert "user = 'you'" in changed["profile"]
        bad = json.dumps({"profile_fields": {"examples": "one"}}).encode()
        assert page.status(path, method="PATCH", data=bad) == 400


class TestTheNovelOverTheWire:
    """The page's half of the novel, over a served omlx whose model is
    named a plain-text model by hand: the facts that show the novel
    glyph and caption the send button, the listing's template, the
    preview as one text, and a continuation's join."""

    def test_the_facts_the_listing_and_the_preview_say_the_novel(
        self, server: ModelServer, tmp_path: Path
    ) -> None:
        server.status = True
        server.types[MODEL] = "vlm"
        server.config_types[MODEL] = "gemma4"

        def plain(root: Path) -> None:
            paths = Paths.resolve(root)
            models_file.save(paths.models_file, MODEL, {models_file.TEMPLATE_KEY: "plain"})

        with serving(server, tmp_path, provider="omlx", prepare=plain) as page:
            # the model can complete text, and sees; the manuscript is the chat's wire
            assert page.get("/api/app")["model"]["can_complete_text"] is True
            facts = page.facts()
            assert (facts["completion_type"], facts["layout"], facts["can_attach"]) == (
                "chat",
                "manuscript",
                True,
            )
            panel = page.get("/api/providers")
            mine = next(p for p in panel["providers"] if p["id"] == "omlx")
            assert mine["models"][0]["text_template"] == "plain"
            page.play("I was waiting for")
            path = f"/api/stories/{page.story}/settings/layout"
            assert page.patch(path, {"layout": "novel"})["notice"]
            # the novel goes over the text wire, read as one text: no
            # picture rides it
            facts = page.facts()
            assert (facts["completion_type"], facts["mode"], facts["can_attach"]) == (
                "text",
                "story",
                False,
            )
            # the preview is the one text the wire sends: the story in it,
            # and none of an instruct template's turn markers
            preview = page.get(f"/api/stories/{page.story}/context")
            assert [part["role"] for part in preview["parts"]] == ["prompt"]
            assert "\n\nI was waiting for" in preview["parts"][0]["body"]
            assert "<|turn>" not in preview["parts"][0]["body"]
            assert preview["shape"]["total_tokens"] > 0
            # a line played in the novel, then an empty line, the continue:
            # no message of the reader's for it; the line opens a paragraph
            # after the chat rows before it, and each reply goes on in it
            page.play("She waited for")
            page.play("")
            messages = page.messages()
            assert [m["role"] for m in messages][-3:] == ["user", "assistant", "assistant"]
            assert [m["join_type"] for m in messages][-3:] == ["paragraph", "inline", "inline"]
            # the space between is the join's, never stored
            assert not messages[-2]["body"].startswith(" ")

    def test_the_novel_is_not_offered_on_a_model_that_cannot_complete_text(
        self, page: Page
    ) -> None:
        # the rail hides the glyph off `can_complete_text`
        assert page.get("/api/app")["model"]["can_complete_text"] is False


class TestWebSearchKeyOverTheWire:
    """ONE box on the Tools tab: what stands is a read, never the key;
    the key a PATCH, the engine told from its shape; "" forgets it."""

    def test_the_key_goes_in_and_never_comes_back_out(self, page: Page) -> None:
        # whether a key is held, and nothing else: no engine named
        path = "/api/integrations/web-search"
        assert page.get(path) == {"has_key": False}
        answer = page.patch(path, {"api_key": "tvly-abc"})
        assert answer["notice"] and answer["value"] == {"has_key": True}
        assert page.get(path) == {"has_key": True}
        raw = Paths.resolve(page.root).integrations_file.read_text(encoding="utf-8")
        assert "tvly-abc" not in raw and "tavily" not in raw
        page.patch(path, {"api_key": "any-other"})
        assert page.get(path) == {"has_key": True}
        page.patch(path, {"api_key": ""})
        assert page.get(path) == {"has_key": False}
        # forgetting none is a no-op, and says so
        again = page.patch(path, {"api_key": ""})
        assert again["notice"] and again["value"] == {"has_key": False}

    def test_the_prompts_of_the_tools_otaku_runs_are_read_at_their_paths(self, page: Page) -> None:
        for tool in ("system-info", "web-search"):
            assert page.get(f"/api/tools/{tool}/prompt")["text"]


class TestToolMessagesOverTheWire:
    """A tool otaku runs, as the page sees it: the call streams named,
    with who acts on it and what the status row says; the result lands
    as a message of its own with the label the page draws; the reply that
    goes on lands after it."""

    def test_a_tool_otaku_runs_lands_as_a_message_the_page_names(
        self, page: Page, server: ModelServer
    ) -> None:
        story = page.made()
        answer = page.patch(f"/api/stories/{story}/settings/allow_system_info", {"enabled": True})
        assert answer["value"]["enabled"] is True
        call = "```otk-system-info\n```"

        def script(body: dict) -> str:
            newest = scripted.content_text(body["messages"][-1])
            if "You are a story analyst" in newest or newest.startswith(("Combine", "Write ")):
                return scripted.default_script(body)
            return "It is late." if "Time: " in newest else call

        server.script = script
        events = page.play("What time is it?")
        kinds = [e["type"] for e in events]
        assert kinds[0] == "user_message" and kinds[-1] == "done"
        assert kinds.count("assistant_message") == 2 and kinds.count("tool_message") == 1
        assert kinds.index("assistant_message") < kinds.index("tool_message")
        called = next(e for e in events if e["type"] == "assistant_tool_call")
        assert (called["tool"], called["label"], called["actor"]) == (
            "system-info",
            "System info",
            "system",
        )
        assert called["status"]  # the status row's sentence, the backend's
        told = next(e for e in events if e["type"] == "tool_message")["message"]
        assert (told["role"], told["kind"], told["tool_label"]) == (
            "tool",
            "tool",
            "System info call",
        )
        assert told["body"].startswith("Time: ") and told["join_type"] == "paragraph"
        rows = page.messages()
        assert [(r["role"], r["tool_label"]) for r in rows[-4:]] == [
            ("user", None),
            ("assistant", None),
            ("tool", "System info call"),
            ("assistant", None),
        ]
        assert rows[-1]["body"] == "It is late."
        # the reply holding the call and the result are one unit with the reply
        assert rows[-1]["unit"] == rows[-3]["id"]


class TestSupportOverTheWire:
    """The support docket's request: its message and its email apart,
    posted to the desk; a refusal is an answer, and a body without a
    message is a bad request."""

    @pytest.fixture(autouse=True)
    def _desk_at_the_scripted_server(
        self, server: ModelServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The desk at the scripted server's door — set before the page's
        app binds it to its endpoint."""
        monkeypatch.setattr(
            SupportDesk, "endpoint", server.url.removesuffix("/v1") + "/api/support"
        )

    def test_a_request_reaches_the_desk_and_a_refusal_is_an_answer(
        self, page: Page, server: ModelServer
    ) -> None:
        path = "/api/integrations/support-requests"
        answer = page.post(path, {"body": "The canvas froze.", "email": "me@example.com"})
        assert answer["notice"] and answer["value"] is None
        assert server.posts[-1] == "/api/support"
        assert server.requests[-1] == {"message": "The canvas froze.", "email": "me@example.com"}
        # no address, none sent; one that is none, refused before anything leaves
        page.post(path, {"body": "Thanks."})
        assert server.requests[-1] == {"message": "Thanks."}
        sent = len(server.posts)
        assert page.refused("POST", path, {"body": "Hi", "email": "me@localhost"})["status"] == 422
        assert page.refused("POST", path, {"body": "  "})["status"] == 422
        assert len(server.posts) == sent
        assert page.status(path, method="POST", data=b'{"email": "me@example.com"}') == 400


def _hang_up_in_the_wait(page: Page, server: ModelServer) -> None:
    """A line played over a raw socket, hung up while the reply waits for
    its first token — then a write, answered at once."""
    story = page.made()
    server.headers_delay = 3.0
    parts = urlsplit(page.url)
    assert parts.hostname and parts.port
    raw = socket.create_connection((parts.hostname, parts.port), timeout=5)
    body = json.dumps({"line": "I enter the hall."}).encode()
    raw.sendall(
        f"POST /api/stories/{story}/messages HTTP/1.1\r\nHost: {parts.netloc}\r\n"
        f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n"
        "If-Match: *\r\nConnection: close\r\n\r\n".encode()
        + body
    )
    time.sleep(0.5)  # the reply is in the wait for the first token
    raw.close()
    started = time.monotonic()
    page.patch("/api/settings", {"verbose": False})  # a write: the thread in turn
    assert time.monotonic() - started < 1.5  # never the server's three seconds
    server.headers_delay = 0.0


def _watchers() -> int:
    """The serving threads this process is holding — one per request in
    flight, which for a held-open stream means one per open tab."""
    return sum(1 for thread in threading.enumerate() if "process_request" in thread.name)


def _file(data: bytes) -> dict[str, str]:
    return {"name": "cat.jpg", "media_type": "image/jpeg", "data": base64.b64encode(data).decode()}


def _fetch(page: Page, path: str) -> tuple[int, dict[str, str], bytes]:
    """A raw GET: the status, the headers, the bytes."""
    with urllib.request.urlopen(page.url + path, timeout=10, context=page.context) as reply:
        return reply.status, dict(reply.headers), reply.read()
