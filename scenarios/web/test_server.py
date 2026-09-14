"""The page over a real session: what the browser asks for, and gets.

One thread serves, the test's own stands in for the browser. The stories
here are the ones a person plays through the page — open it, read the
story, play a line, run a command — asserted on the wire and in the
store, never on the screen.
"""

import base64
import threading
import time
import tomllib
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import urlsplit

from otaku.backend.session import THINK_MENU
from scenarios.support.server import ModelServer
from scenarios.web.conftest import Page

SERAPHINA = Path(__file__).parent.parent / "fixtures" / "seraphina.png"


class TestServing:
    def test_the_page_and_everything_it_needs_are_served(self, page: Page) -> None:
        assert b"<title>otaku</title>" in page.get("/")
        for asset in ("/app.css", "/app.js", "/js/stories.js", "/js/story.js"):
            assert page.status(asset) == 200, asset

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
        (theirs / "notes.txt").write_text("not a typeface")
        assert page.status("/web-fonts/notes.txt") == 404
        assert page.status("/web-fonts/../../configs/providers.toml") == 404

    def test_only_the_table_may_be_served(self, page: Page) -> None:
        # A path is looked up, never joined onto a directory.
        for escape in ("/../pyproject.toml", "/../../etc/passwd", "/configs/providers.toml"):
            assert page.status(escape) == 404, escape

    def test_an_unknown_path_is_not_found(self, page: Page) -> None:
        assert page.status("/api/nonesuch") == 404
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
    """`/api/status` is how a tab that is asking for nothing else learns
    that otaku stopped — and what the backend is doing while nobody
    asked. It is in neither lane, and the difference shows
    exactly when the session's thread is not free."""

    def test_it_answers_what_can_be_answered_off_the_session_s_thread(self, page: Page) -> None:
        beat = page.get("/api/status")
        assert beat == {"status": "", "notices": []}

    def test_it_answers_while_a_reply_is_streaming(self, page: Page, server: ModelServer) -> None:
        # The one moment the session's thread is unavailable: it is
        # holding a reply, chunk by chunk. The heartbeat never asks for
        # that thread, which is what makes it an answer about the SERVER
        # — so it comes back with the reply still going.
        server.chunk_delay = 1.0
        replying = threading.Thread(target=page.play, args=("A long look at the water.",))
        replying.start()
        try:
            time.sleep(0.5)
            assert page.status("/api/status") == 200
            assert replying.is_alive(), "the reply was over — the story proves nothing"
        finally:
            replying.join(timeout=30)


class TestReading:
    def test_the_session_facts_name_the_model(self, page: Page) -> None:
        facts = page.get("/api/session")
        # The bare model in the header, the provider beside it — the
        # banner's own split, which the page draws in two places.
        assert facts["model"] == "test-model"
        assert facts["provider"]

    def test_the_turns_are_the_story_as_the_store_has_it(self, page: Page) -> None:
        page.play("I listen at the culvert mouth.")
        story = page.get("/api/session")["story_id"]
        assert [turn["body"] for turn in page.get("/api/play")["messages"]] == [
            message.body for message in page.store.stories.get_messages(story)
        ]

    def test_the_typed_language_is_the_shared_one(self, page: Page) -> None:
        # The story's own framing, declared once below both frontends —
        # and NOT the commands, which are endpoints here.
        language = page.get("/api/play/syntax")
        assert {row["token"] for row in language["openers"]} >= {"/me", "/you", "/ooc"}
        assert {row["token"] for row in language["inliners"]} >= {"… /cue"}
        assert language["prose"]

    def test_a_read_that_refuses_answers_with_the_sentence(self, page: Page) -> None:
        # A refusal IS the answer — 200 and a notice, not an error page.
        assert page.get("/api/usage")["notice"].startswith("No story yet")

    def test_the_cast_read_answers_the_composers_name_menu(self, page: Page) -> None:
        # Empty with no story — a menu question is never a refusal —
        # and the story's characters once a pass has named them.
        assert page.get("/api/cast") == {"characters": []}

    def test_the_balance_roster_asks_no_network_and_marks_the_unasked(self, page: Page) -> None:
        """`?probe=none` is the slip's first paint: every cloud row
        present, a KEYED one carrying an empty note (not asked yet) and
        no figure — offline-provable, since nothing may be probed."""
        page.patch("/api/providers/openrouter", {"api_key": "k-test"})
        rows = {row["provider"]: row for row in page.get("/api/balance?probe=none")["rows"]}
        assert rows["openrouter"]["note"] == ""  # keyed: not asked yet
        assert rows["openrouter"]["money"] is None
        assert rows["nanogpt"]["note"]  # keyless: says which nothing it is

    def test_an_emptied_provider_field_is_forgotten(self, page: Page) -> None:
        # An empty value is the terminal's Del: the url or the stored key
        # is cleared in the file and the session both. The page's Save
        # sends a field as the reader left it, and nothing else could take
        # a key out again.
        page.patch("/api/providers/openrouter", {"api_key": "k-test"})
        providers = page.root / "configs/providers.toml"
        assert tomllib.loads(providers.read_text())["openrouter"]["api_key"]
        answer = page.patch("/api/providers/openrouter", {"api_key": ""})
        assert not answer.get("refused")
        assert tomllib.loads(providers.read_text())["openrouter"]["api_key"] == ""
        page.patch("/api/providers/generic", {"url": ""})
        assert tomllib.loads(providers.read_text())["generic"]["url"] == ""

    def test_the_settings_read_carries_the_shared_effort_ladder(self, page: Page) -> None:
        # The order is declared ONCE, below both frontends — the page
        # draws it, never re-sorts it.
        assert page.get("/api/settings")["think_levels"] == list(THINK_MENU)

    def test_the_search_matches_buried_content_and_the_row_s_face(self, page: Page) -> None:
        """One filter rule for both browsers: a story is found by the
        text of its chain AND by what its listing row shows — here the
        title, which is never a message."""
        page.play("I listen at the culvert mouth.")
        story = page.get("/api/session")["story_id"]
        page.put(f"/api/stories/{story}/title", {"title": "The Beached Ferry"})

        def found(q: str) -> list[int]:
            return [row["id"] for row in page.get(f"/api/stories?q={q}")["stories"]]

        assert story in found("culvert")  # buried in the chain
        assert story in found("beached")  # the listing row's own face
        assert story not in found("zeppelin")

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
        # deleted, a provider nothing is configured under. The spec
        # draws no line between this and an unknown path, so neither
        # does the wire: 404, never an empty dossier dressed as a story.
        assert page.status("/api/stories/9999") == 404
        assert page.status("/api/providers/nobody") == 404
        # And a DELETE that removed nothing must not say it did.
        assert page.status("/api/stories/9999", method="DELETE") == 404


class TestPlaying:
    def test_a_line_plays_and_lands_in_the_store(self, page: Page) -> None:
        events = page.play("I unroll the county survey.")
        kinds = [event["type"] for event in events]
        assert kinds[0] == "recorded"
        assert "text" in kinds
        assert kinds[-1] == "done"
        story = page.get("/api/session")["story_id"]
        stored = page.store.stories.get_messages(story)
        assert stored[-2].body == "I unroll the county survey."
        assert stored[-1].role == "assistant"

    def test_the_wire_carries_what_the_page_typed(self, page: Page, server: ModelServer) -> None:
        page.play("I mark the river's true course.")
        assert server.requests[-1]["messages"][-1]["content"] == "I mark the river's true course."

    def test_a_refusal_arrives_before_a_byte_of_the_stream(self, page: Page) -> None:
        # Eager validation: invalid syntax leaves the story untouched and
        # the usage line is the whole answer.
        answer = page.play("/you")
        assert answer[0]["refused"] is True
        assert answer[0]["notice"]

    def test_a_roll_arrives_with_its_note(self, page: Page) -> None:
        # The dice are rolled below both frontends and ride the recorded
        # event as its note — the page never rolls, and never parses the
        # frozen template to find out what fell.
        events = page.play("/roll 1d6 I duck behind the crates.")
        assert events[0]["type"] == "recorded"
        assert "1d6 = " in events[0]["note"]


class TestWrites:
    def test_a_write_is_answered_with_its_sentence(self, page: Page) -> None:
        page.play("I listen at the culvert mouth.")
        story = page.get("/api/session")["story_id"]
        answer = page.put(f"/api/stories/{story}/title", {"title": "The Beached Ferry"})
        assert "The Beached Ferry" in answer["notice"]
        assert page.store.stories.get(story).title == "The Beached Ferry"

    def test_a_write_a_screen_performs_lands_in_the_store(self, page: Page) -> None:
        page.play("I listen at the culvert mouth.")
        story = page.get("/api/session")["story_id"]
        first = page.store.stories.get_messages(story)[0]
        answer = page.patch(f"/api/stories/{story}/messages/{first.id}", {"text": "I listen."})
        assert answer["notice"]
        assert page.store.stories.get_messages(story)[0].body == "I listen."

    def test_a_malformed_body_is_the_page_s_fault_not_a_crash(self, page: Page) -> None:
        # A field the page did not send is a bad request, not a 500.
        page.play("I listen at the culvert mouth.")
        story = page.get("/api/session")["story_id"]
        first = page.store.stories.get_messages(story)[0]
        assert page.status(f"/api/stories/{story}/messages/{first.id}", method="PATCH") == 400

    def test_a_journal_state_has_no_write(self, page: Page) -> None:
        # The entry is a journal's one writable field; a state is the
        # extractor's own, so a state-only body is refused as an answer
        # before any row is even looked up.
        page.play("I listen at the culvert mouth.")
        story = page.get("/api/session")["story_id"]
        answer = page.patch(f"/api/stories/{story}/journals/1", {"state": "standing"})
        assert answer["refused"] is True

    def test_a_story_that_was_made_answers_where_it_now_lives(self, page: Page) -> None:
        # A POST that MAKES something answers 201 and names it, so the
        # page never has to ask which story it just got.
        code, where, answer = page.sent("POST", "/api/stories", {"title": "The Weir"})
        assert code == 201
        assert where == f"/api/stories/{page.get('/api/session')['story_id']}"
        assert answer["notice"]

    def test_a_creation_that_refuses_names_nothing(self, page: Page) -> None:
        # Nothing was made, so there is nowhere to point: a refusal keeps
        # 200 and the page reads the flag, as it does everywhere else.
        # An empty story is the case — there is no turn to copy.
        story = page.sent("POST", "/api/stories", {"title": "Empty"})[2]["story"]
        code, where, answer = page.sent("POST", f"/api/stories/{story}/fork")
        assert code == 200
        assert where == ""
        assert answer["refused"] is True

    def test_a_name_in_the_path_arrives_decoded(self, page: Page) -> None:
        # A provider or a model is a NAME, and the page sends it through
        # `encodeURIComponent` — `llama3:8b` is the ordinary local model,
        # not the exotic one. Undecoded, the escape reaches the engine as
        # part of the name and nothing it asks for exists.
        plain = page.get("/api/providers/generic")
        assert plain["providers"], "the fixture's provider should be there"
        assert page.get("/api/providers/gener%69c") == plain

    def test_a_fault_answers_in_the_body_whatever_the_reason_says(self, page: Page) -> None:
        # The reason carries a story title, a character name, a
        # provider's own error text — and the HTTP status line is
        # latin-1, so a reason written in Cyrillic or Japanese could not
        # go there. It goes in the body, which is where the page reads
        # every other sentence anyway.
        japanese = '{"story": "\u65e5\u672c"}'.encode()
        assert page.status("/api/session/head", method="PUT", data=japanese) == 400

    def test_a_null_where_a_value_belongs_is_never_the_string_none(self, page: Page) -> None:
        # Coerced, a null would store the literal title "None" — a
        # malformed request that lands rather than being refused.
        page.play("I listen at the culvert mouth.")
        story = page.get("/api/session")["story_id"]
        was = page.store.stories.get(story).title
        null = b'{"title": null}'
        assert page.status(f"/api/stories/{story}/title", method="PUT", data=null) == 400
        assert page.store.stories.get(story).title == was

    def test_a_message_is_corrected_only_under_its_own_story(self, page: Page) -> None:
        # The browser addresses any story, so the path is the claim and
        # the chain is the check: a correction that landed on another
        # story's message would rewrite a story nobody was looking at.
        page.play("I listen at the culvert mouth.")
        mine = page.get("/api/session")["story_id"]
        first = page.store.stories.get_messages(mine)[0]
        other = page.sent("POST", "/api/stories", {"title": "Elsewhere"})[2]["story"]
        answer = page.patch(f"/api/stories/{other}/messages/{first.id}", {"text": "rewritten"})
        assert answer["refused"] is True
        assert page.store.stories.get_messages(mine)[0].body == first.body

    def test_a_null_where_a_number_belongs_is_the_page_s_fault(self, page: Page) -> None:
        # Malformed, not broken: a 400 and no crash filed. Anything that
        # scans this port can send one.
        assert page.status("/api/session/head", method="PUT", data=b'{"story": null}') == 400

    def test_a_refusal_is_marked_so_the_page_never_reads_the_wording(self, page: Page) -> None:
        # Nothing to undo is an expected answer: 200, the sentence, and
        # the FLAG — the wire contract that keeps sentence-sniffing out
        # of the page.
        answer = page.delete("/api/play/last")
        assert answer["refused"] is True
        assert answer["notice"]

    def test_an_unknown_setting_is_refused_with_a_sentence(self, page: Page) -> None:
        # A knob the page thinks exists is a refusal, not a 404: the path
        # is real, and what it says is the backend's own sentence.
        answer = page.put("/api/settings/bogus", {"value": "on"})
        assert answer["refused"] is True
        assert answer["notice"]

    def test_the_composer_history_is_the_store_s(self, page: Page) -> None:
        # The page records what was submitted and reads it back most
        # recent first — the same lines the terminal prompt walks, so a
        # reload starts with the history it left.
        page.post("/api/history", {"line": "I listen at the culvert mouth."})
        page.post("/api/history", {"line": "/stories"})
        recent = page.get("/api/history")["lines"]
        assert recent[:2] == ["/stories", "I listen at the culvert mouth."]

    def test_a_story_with_nothing_played_is_resumed_by_its_id_alone(self, page: Page) -> None:
        # Nothing played means no message to land on — and a resume never
        # used one. Without this the browser's Continue had nothing to
        # send, and a story started and left could only be deleted.
        page.play("I listen at the culvert mouth.")
        played = page.get("/api/session")["story_id"]
        blank = page.sent("POST", "/api/stories", {"title": "Blank"})[2]["story"]
        assert not page.put("/api/session/head", {"story": played}).get("refused")
        assert page.get("/api/session")["story_id"] == played
        assert not page.put("/api/session/head", {"story": blank}).get("refused")
        assert page.get("/api/session")["story_id"] == blank
        assert page.get("/api/play")["messages"] == []
        # Discarding cuts AT a message: without one the request is malformed.
        cut = f'{{"story": {played}, "discard": true}}'.encode()
        assert page.status("/api/session/head", method="PUT", data=cut) == 400
        assert page.get("/api/session")["story_id"] == blank


class TestTheFlows:
    """The writes whose result outlives their request: a forced pass the
    page polls for, and a card import split around its persona ask."""

    def test_a_forced_extraction_is_started_and_its_report_polled(self, page: Page) -> None:
        page.play("I listen at the culvert mouth.")
        page.play("I wade into the dark after the voice.")
        started = page.post(f"/api/stories/{page.get('/api/session')['story_id']}/extraction")
        assert started["watching"] is True
        report = _polled(page)
        assert report  # the scripted server closes a scene; the report says so
        story = page.get("/api/session")["story_id"]
        ids = [m.id for m in page.store.stories.get_messages(story)]
        assert page.store.scenes.get_current(story, ids)

    def test_a_card_lands_through_the_prepare_and_add_halves(self, page: Page) -> None:
        page.play("I listen at the culvert mouth.")
        prepared = page.post(
            "/api/cards",
            {"data": base64.b64encode(SERAPHINA.read_bytes()).decode(), "name": "seraphina.png"},
        )
        assert prepared["card"]["name"] == "Seraphina"
        answer = page.put(f"/api/cards/{prepared['token']}", {"persona": "Maren"})
        assert "Seraphina" in answer["notice"]
        story = page.get("/api/session")["story_id"]
        names = {c.name for c in page.store.characters.list(story)}
        assert "Seraphina" in names

    def test_a_token_nobody_holds_adds_nothing(self, page: Page) -> None:
        # A reload between the halves, or a page that asked twice: an
        # ordinary answer, and the cast is untouched.
        page.play("I listen at the culvert mouth.")
        answer = page.put("/api/cards/gone", {"persona": "Maren"})
        assert answer["notice"]
        story = page.get("/api/session")["story_id"]
        assert page.store.characters.list(story) == []

    def test_an_imported_document_starts_the_pass_the_page_watches(self, page: Page) -> None:
        landed = page.post(
            "/api/stories",
            {
                "import": {
                    "text": "A lantern swings on the pier.\n\nNobody holds it.",
                    "name": "pier.txt",
                }
            },
        )
        assert landed["watching"] is True  # memoryless shape: memory builds now
        assert _polled(page)
        story = page.get("/api/session")["story_id"]
        assert [m.body for m in page.store.stories.get_messages(story)] == [
            "A lantern swings on the pier.",
            "Nobody holds it.",
        ]


def _polled(page: Page, timeout: float = 30.0) -> str:
    """The report as the page's own poll would read it — None until the
    pass returns, then the sentence."""
    deadline = time.monotonic() + timeout
    story = page.get("/api/session")["story_id"]
    while time.monotonic() < deadline:
        report = page.get(f"/api/stories/{story}/extraction")["report"]
        if report is not None:
            return str(report)
        time.sleep(0.1)
    raise AssertionError("the pass never reported")


class TestWhoIsAsking:
    """With no password set the page asks nobody who they are — but a
    page on another origin, in the same browser, must still not be able
    to drive it. A write can do its damage without ever reading the
    answer; a read cannot, and is not asked."""

    def test_a_cross_site_write_is_refused(self, page: Page) -> None:
        # What an auto-submitting form on another site sends. It cannot
        # forge `Sec-Fetch-Site`, and a form's content type is never
        # application/json without a preflight this server never answers.
        page.play("I listen at the culvert mouth.")
        story = page.get("/api/session")["story_id"]
        assert (
            page.status(
                f"/api/stories/{story}/title",
                method="PUT",
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
                "/api/providers/demo",
                method="POST",
                headers={"Origin": "http://evil.example"},
                data=b'{"provider":"generic","field":"url","value":"http://attacker.example/v1"}',
            )
            == 403
        )

    def test_the_page_s_own_writes_are_let_through(self, page: Page) -> None:
        # What the page itself sends — same-origin fetch metadata.
        page.play("I listen at the culvert mouth.")
        story = page.get("/api/session")["story_id"]
        assert (
            page.status(
                f"/api/stories/{story}/title",
                method="PUT",
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
        assert page.status("/api/session", headers=fetched) == 200

    def test_a_request_addressed_to_another_name_is_misdirected(self, page: Page) -> None:
        # DNS rebinding is the one attack a loopback bind does not stop:
        # the name in the request is what gives it away.
        assert page.status("/api/play", headers={"Host": "attacker.example"}) == 421
        assert page.status("/api/play", headers={"Host": "localhost"}) == 200

    def test_a_malformed_length_is_a_bad_request_not_a_crash(self, page: Page) -> None:
        import socket
        from urllib.parse import urlsplit

        where = urlsplit(page.url)
        with socket.create_connection((where.hostname, where.port), timeout=5) as scanner:
            scanner.sendall(
                b"POST /api/history HTTP/1.1\r\nHost: localhost\r\nContent-Length: abc\r\n\r\n{}"
            )
            answered = scanner.recv(64)
        assert answered.startswith(b"HTTP/1.")


class TestThePicker:
    def test_a_provider_configured_by_hand_is_in_the_picker(self, page: Page) -> None:
        # The scenario's own provider is a hand-written section named
        # `test` — not one of the providers otaku ships a client for. The
        # session is PLAYING on it, so a picker without it is a picker
        # with no way back to the story's own model.
        panel = page.get("/api/providers")
        mine = next(p for p in panel["providers"] if p["id"] == "generic")
        assert [model["name"] for model in mine["models"]] == ["test-model"]
        assert panel["current"] == "generic/test-model"
        assert mine["connected"] is True
        assert mine["locality"] == "unknown"  # a hand-written section: nobody can say

    def test_the_panel_says_where_each_provider_runs(self, page: Page) -> None:
        # The vocabulary the page's captions and the demo's fake read:
        # the generic provider first and unable to say, a provider on this
        # machine, a catalog over the wire.
        panel = page.get("/api/providers")
        assert panel["providers"][0]["id"] == "generic"
        by_name = {p["id"]: p["locality"] for p in panel["providers"]}
        assert by_name["generic"] == "unknown"
        assert by_name["llamacpp"] == "local"
        assert by_name["openrouter"] == "remote"

    def test_the_two_phases_carry_the_panel_order(self, page: Page) -> None:
        # The page asks in two phases and merges by each card's `order`:
        # the generic provider answers in the second phase and belongs
        # first, a hand-written section last. Sorting both answers by it
        # restores the unscoped panel exactly.
        whole = [p["id"] for p in page.get("/api/providers")["providers"]]
        local = page.get("/api/providers?scope=local")["providers"]
        cloud = page.get("/api/providers?scope=cloud")["providers"]
        assert {p["id"] for p in cloud} >= {"generic", "openrouter", "nanogpt"}
        assert all(p["id"] not in {"generic", "openrouter"} for p in local)
        merged = sorted(local + cloud, key=lambda p: (p["order"], p["id"]))
        assert [p["id"] for p in merged] == whole
        assert merged[0]["id"] == "generic" and merged[0]["order"] == 0


class TestOpenToTheNetwork:
    """`[web] host = 0.0.0.0` is the documented way to reach otaku from
    another machine. The rebinding guard must not be what stops it: a
    wildcard bind has already decided to answer everyone, and the
    address a LAN client names is one otaku cannot know."""

    def test_a_wildcard_bind_answers_to_any_name(self, wide: Page) -> None:
        assert wide.status("/", headers={"Host": "192.168.178.21:9600"}) == 200
        assert wide.status("/api/play", headers={"Host": "otaku.lan"}) == 200

    def test_a_write_from_the_app_one_port_over_is_still_refused(self, wide: Page) -> None:
        # The neighbour this guard is for: another app on the same host,
        # which shares the name but not the origin.
        assert (
            wide.status(
                "/api/providers/demo",
                method="POST",
                headers={"Host": "otaku.lan:9600", "Origin": "http://otaku.lan:8080"},
                data=b'{"provider":"generic","field":"url","value":"http://attacker.example/v1"}',
            )
            == 403
        )

    def test_the_page_s_own_write_is_let_through(self, wide: Page) -> None:
        wide.play("I listen at the culvert mouth.")
        story = wide.get("/api/session")["story_id"]
        assert (
            wide.status(
                f"/api/stories/{story}/title",
                method="PUT",
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
        held.request("GET", "/api/play")
        held.getresponse().read()  # kept alive
        page.stop()
        held.request("GET", "/api/play")
        answered = held.getresponse()
        answered.read()
        held.close()
        assert answered.status == 503


def _watchers() -> int:
    """The serving threads this process is holding — one per request in
    flight, which for a held-open stream means one per open tab."""
    return sum(1 for thread in threading.enumerate() if "process_request" in thread.name)
