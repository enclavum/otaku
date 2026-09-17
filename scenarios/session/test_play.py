"""Playing a story: turns, the wire promise, undo, regenerate, the
roleplay commands /me, /you, /ooc, and the inline pair typed inside a
line."""

from pathlib import Path

from otaku.backend.api import play as api_play
from otaku.backend.api.play import Done
from otaku.backend.formats import EXPORT_MARKER
from otaku.backend.paths import Paths
from scenarios.support import server as scripted
from scenarios.support.harness import App, launch, set_config, set_config_provider


class TestTurns:
    def test_a_message_gets_a_reply_and_both_persist(self, app: App) -> None:
        app.play("I open the door.")
        chain = app.store.stories.get_messages(app.session.story_id)
        assert [(m.role, m.body) for m in chain] == [
            ("user", "I open the door."),
            ("assistant", scripted.CHAT_REPLY),
        ]

    def test_the_wire_carries_the_message_verbatim(self, app: App) -> None:
        app.play("/system You are the narrator.")
        app.play("I wait.   With   spaces?")
        sent = app.server.requests[-1]["messages"]
        assert sent[0] == {"role": "system", "content": "You are the narrator."}
        assert sent[1] == {"role": "user", "content": "I wait.   With   spaces?"}

    def test_me_frames_the_line_at_wire_time_only(self, app: App) -> None:
        app.play("/me Elara: I step into the light.")
        sent = app.server.requests[-1]["messages"][-1]["content"]
        assert sent == "((OOC: The user writes as Elara.))\nI step into the light."
        stored = app.store.stories.get_messages(app.session.story_id)[0]
        assert stored.body == "/me Elara: I step into the light."  # the line as typed

    def test_the_framing_templates_come_from_the_prompts_file(self, server, tmp_path) -> None:
        # The file IS the injection, not a copy of it. Asserting the
        # built-in wording would pass even if the load path were dropped
        # and the defaults hardcoded, so the templates here are edited.
        root = tmp_path / "state"
        paths = Paths.resolve(root)
        paths.ensure_tree()
        paths.prompts_file.write_text(
            'me_framing = "<<{name} speaks>>\\n{body}"\n'
            'you_framing = "<<now play {name}>>"\n'
            'ooc_framing = "<<aside: {body}>>"\n'
        )
        app = launch(root, server)
        try:
            app.play("/me Elara: I step into the light.")
            assert sent(app) == "<<Elara speaks>>\nI step into the light."
            app.play("/you Ryn")
            assert sent(app) == "<<now play Ryn>>"
            app.play("/ooc What genre is this?")
            assert sent(app) == "<<aside: What genre is this?>>"
        finally:
            app.close()

    def test_a_turn_keeps_the_wording_it_was_played_with(self, server, tmp_path) -> None:
        # The template is snapshotted onto the row as it plays, so editing
        # prompts.toml afterwards cannot rewrite what a past turn said to
        # the model — only what the next one will.
        root = tmp_path / "state"
        paths = Paths.resolve(root)
        paths.ensure_tree()
        paths.prompts_file.write_text('me_framing = "<<first {name}>>\\n{body}"\n')
        app = launch(root, server)
        try:
            app.play("/me Elara: one.")
        finally:
            app.close()
        paths.prompts_file.write_text('me_framing = "<<second {name}>>\\n{body}"\n')
        app = launch(root, server)
        try:
            app.play("/me Elara: two.")
            wire = app.server.requests[-1]["messages"]
            assert wire[0]["content"] == "<<first Elara>>\none."
            assert wire[-1]["content"] == "<<second Elara>>\ntwo."
        finally:
            app.close()

    def test_a_stream_error_keeps_what_already_arrived(self, app: App, capsys) -> None:
        # The prose the user watched stream is in the story, error or not —
        # the screen and the story must never diverge.
        app.server.fail_after = 1
        app.play("I enter the hall.")
        assert "[ error:" in capsys.readouterr().out
        chain = app.store.stories.get_messages(app.session.story_id)
        first_chunk = scripted.CHAT_REPLY[: max(1, len(scripted.CHAT_REPLY) // 3)]
        assert [m.body for m in chain] == ["I enter the hall.", first_chunk]

    def test_model_control_bytes_never_reach_the_screen(self, app: App, capsys) -> None:
        # A hostile or glitchy stream cannot move the cursor, retitle the
        # window, or desync the screen ledger — the story keeps the bytes.
        app.server.script = lambda body: "safe\x1b[2Atext\x07"
        app.play("I enter the hall.")
        out = capsys.readouterr().out
        assert "\x1b[2A" not in out
        assert "\x07" not in out
        assert app.session.messages[-1].body == "safe\x1b[2Atext\x07"

    def test_a_replys_padding_blank_lines_never_print(self, app: App, capsys) -> None:
        # Some cloud models wrap the reply in blank lines; the screen and
        # the record both start at the first real character and end at
        # the last.
        app.server.script = lambda body: "\n\nThe hall glows.\n\n"
        app.play("I enter the hall.")
        assert "\n\n\n" not in capsys.readouterr().out
        assert app.session.messages[-1].body == "The hall glows."


class TestReplyReport:
    def test_a_reply_cut_at_the_limit_is_said_so(self, app: App) -> None:
        # The wire's "length" is a reply the model did not finish: the
        # turn's end carries the fact and the sentence for it, and the
        # reply is recorded as it came.
        app.server.finish = "length"
        events = list(api_play.submit(app.session, "I enter the hall."))
        done = events[-1]
        assert isinstance(done, Done)
        assert done.report is not None and done.report.stats.finish_reason == "length"
        assert done.report.truncated and done.report.notice
        assert done.reply is not None and done.reply.body

    def test_a_finished_reply_has_nothing_to_notice(self, app: App) -> None:
        events = list(api_play.submit(app.session, "I enter the hall."))
        done = events[-1]
        assert isinstance(done, Done)
        assert done.report is not None and not done.report.truncated
        assert done.report.notice == "" and done.stats == ""  # verbose is off


class TestProviderFailures:
    """What the reader is told when a turn cannot be played: the
    provider package's own sentence, which names the provider and
    carries the server's explanation — never a transport type."""

    def test_an_error_status_names_the_provider_and_the_status(self, app: App, capsys) -> None:
        app.server.refuse = lambda body: 503
        app.play("I enter the hall.")
        out = capsys.readouterr().out
        assert "[ error: Refused by generic with HTTP 503: " in out
        assert "refused by the script" in out

    def test_a_rate_limited_turn_says_when_to_try_again(self, app: App, capsys) -> None:
        app.server.refuse = lambda body: 429
        app.server.retry_after = "30"
        app.play("I enter the hall.")
        out = capsys.readouterr().out
        assert "[ error: Refused by generic with HTTP 429: " in out
        assert "Try again in 30 seconds." in out

    def test_a_declining_model_says_so_in_its_words(self, app: App, capsys) -> None:
        app.server.decline = "content filtered"
        app.play("I enter the hall.")
        assert "[ error: The reply broke off: content filtered ]" in capsys.readouterr().out

    def test_a_dead_provider_cannot_be_reached(self, server, tmp_path, capsys) -> None:
        dead = scripted.ModelServer()
        dead.close()  # the port is known, and nothing answers on it
        set_config_provider(tmp_path / "state", dead)
        app = launch(tmp_path / "state", dead)
        try:
            app.play("I enter the hall.")
            assert "[ error: Could not reach generic. ]" in capsys.readouterr().out
        finally:
            app.close()


class TestAttribution:
    def test_a_turn_to_openrouter_names_the_app(self, server, tmp_path) -> None:
        root = tmp_path / "state"
        set_config_provider(root, server, name="openrouter")
        app = launch(root, server, spec="openrouter/test-model")
        try:
            app.play("I open the door.")
            headers = sent_headers(app)
        finally:
            app.close()
        assert headers["http-referer"] == "https://otaku.sh"
        assert headers["x-openrouter-title"] == "otaku"
        assert headers["x-openrouter-categories"] == "roleplay,creative-writing"
        # Added to the auth, never in place of it.
        assert headers["authorization"] == "Bearer scenario-key"

    def test_no_other_provider_is_told_anything(self, app: App) -> None:
        # The hook is per client: a generic OpenAI endpoint — and every
        # local engine — sends what it always sent.
        app.play("I open the door.")
        headers = sent_headers(app)
        assert [name for name in headers if name.startswith("x-openrouter")] == []
        assert "http-referer" not in headers


class TestMe:
    def test_a_name_the_cast_knows_is_settled_before_the_line_is_stored(self, app: App) -> None:
        # Whatever the writer typed, the story keeps the character it
        # already has — the lookup is case-insensitive and follows aliases
        # (store.characters.find owns that). Settled in the LINE, so the
        # store and the wire cannot say different things.
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.play("/extract")  # the Keeper joins the cast
        app.play("/me keeper: I bow.")
        stored = app.store.stories.get_messages(app.session.story_id)[-2]
        assert stored.body == "/me Keeper: I bow."
        sent = scripted.chat_request(app.server, "I bow.")["messages"][-1]["content"]
        assert "Keeper" in sent
        assert sent.endswith("I bow.")

    def test_a_known_name_is_recorded_as_the_requests_speaker(self, app: App) -> None:
        # /me says the line IS that character's, so the row is attributed
        # right away — deterministically, where extraction would guess —
        # and extraction's own labeling is fill-only, so it stands. The
        # reply stays unattributed: the model may answer as anyone.
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.play("/extract")  # the Keeper joins the cast
        app.play("/me keeper: I bow.")
        keeper = app.store.characters.find(app.session.story_id, "Keeper")
        request, reply = app.store.stories.get_messages(app.session.story_id)[-2:]
        assert (request.speaker, request.speaker_id) == ("Keeper", keeper.id)
        assert (reply.speaker, reply.speaker_id) == (None, None)

    def test_autocorrect_off_leaves_the_text_but_still_attributes(self, app: App) -> None:
        # The toggle governs rewriting what was typed; who spoke is not a
        # rewrite, and extraction would label the row later anyway — this
        # just does it with certainty instead of a guess.
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.play("/extract")
        app.play("/set autocorrect off")
        app.play("/me keeper: I bow.")
        stored = app.store.stories.get_messages(app.session.story_id)[-2]
        assert stored.body == "/me keeper: I bow."
        assert stored.speaker == "Keeper"

    def test_an_inliner_survives_the_settling(self, app: App) -> None:
        # The name is replaced in place; what closes the line rides along.
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.play("/extract")
        app.play("/me keeper: I bow. /cue keep it short")
        stored = app.store.stories.get_messages(app.session.story_id)[-2]
        assert stored.body == "/me Keeper: I bow. /cue keep it short"

    def test_a_name_the_cast_does_not_know_is_left_alone(self, app: App) -> None:
        # Nobody is extracted yet, and a character still has to be able to
        # speak before the pass that discovers them.
        app.play("/me keeper: I bow.")
        stored = app.store.stories.get_messages(app.session.story_id)[-2]
        assert stored.body == "/me keeper: I bow."
        assert (stored.speaker, stored.speaker_id) == (None, None)
        sent = scripted.chat_request(app.server, "I bow.")["messages"][-1]["content"]
        assert "keeper" in sent and "Keeper" not in sent

    def test_a_played_name_does_not_shift_when_the_cast_does(self, app: App) -> None:
        # The name is settled once, as the line plays. A turn already said
        # reads the same tomorrow — folding its character into another
        # cannot rewrite what the story already contains.
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.play("/extract")  # the Keeper joins the cast
        app.play("/me keeper: I bow.")
        story_id = app.session.story_id
        assert story_id is not None
        app.store.characters.add(story_id, "Gatewarden")
        app.play("/merge Keeper into Gatewarden")
        app.play("/regen")
        sent = scripted.chat_request(app.server, "I bow.")["messages"][-1]["content"]
        assert "Keeper" in sent and "Gatewarden" not in sent

    def test_a_malformed_direction_plays_nothing(self, app: App) -> None:
        # No colon, so no prose to send. The story must be untouched — a
        # line the app cannot read is not half-played.
        before = len(app.server.requests)
        app.play("/me Elara")
        assert len(app.server.requests) == before
        assert app.store.stories.get_messages(app.session.story_id) == []


class TestYou:
    def test_you_hands_the_scene_to_the_named_character(self, app: App) -> None:
        app.play("I enter the hall.")
        app.play("/you Elara")
        sent = app.server.requests[-1]["messages"][-1]["content"]
        assert sent.startswith("((OOC")
        assert "Elara" in sent
        chain = app.store.stories.get_messages(app.session.story_id)
        # The line as typed — it carries no prose of its own, so the
        # template alone reaches the model — and a normal reply.
        assert [(m.role, m.body) for m in chain[-2:]] == [
            ("user", "/you Elara"),
            ("assistant", scripted.CHAT_REPLY),
        ]

    def test_a_hint_after_the_name_stays_with_the_turn(self, app: App) -> None:
        # `/you NAME: HINT` — the hint is a standing aside: still on the
        # wire when the turn is no longer the newest (a cue would be gone).
        app.play("/you Elara: always answer in riddles")
        app.play("Tell me about the gate.")
        wire = scripted.chat_request(app.server, "Tell me about the gate.")["messages"]
        you_turn = next(str(m["content"]) for m in wire if "play Elara" in str(m["content"]))
        # Its own block, not blended into the play-as contract: the model
        # sees where the app's instruction ends and the writer's begins.
        assert "((OOC: always answer in riddles))" in you_turn
        assert you_turn.count("((OOC") == 2

    def test_a_known_name_is_recorded_as_the_replys_speaker(self, app: App) -> None:
        # /you asks that character to answer, so the attribution lands on
        # the REPLY; the request is an instruction, nobody's line.
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.play("/extract")  # the Keeper joins the cast
        app.play("/you keeper")
        keeper = app.store.characters.find(app.session.story_id, "Keeper")
        request, reply = app.store.stories.get_messages(app.session.story_id)[-2:]
        assert (request.speaker, request.speaker_id) == (None, None)
        assert (reply.speaker, reply.speaker_id) == ("Keeper", keeper.id)

    def test_a_regenerated_reply_is_the_same_characters(self, app: App) -> None:
        # The prompt decides what its answer is, the speaker included —
        # a fresh take on /you Keeper is still Keeper speaking.
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.play("/extract")
        app.play("/you keeper")
        app.play("/regen")
        reply = app.store.stories.get_messages(app.session.story_id)[-1]
        assert reply.role == "assistant"
        assert reply.speaker == "Keeper"

    def test_resending_a_failed_switch_answers_in_character(self, app: App) -> None:
        # The switch rides an ooc row, yet what it asks for is the scene:
        # the resent take is a normal turn, not an ooc aside.
        app.play("I enter the hall.")
        app.server.fail_after = 0
        app.play("/you Elara")
        app.server.fail_after = None
        app.play("/regen")
        assert app.store.stories.get_messages(app.session.story_id)[-1].kind == "dialogue"


class TestOoc:
    def test_ooc_is_framed_and_marks_both_sides(self, app: App) -> None:
        app.play("I enter the hall.")
        app.play("/ooc What genre is this?")
        sent = app.server.requests[-1]["messages"][-1]["content"]
        assert sent.startswith("((OOC")
        assert "What genre is this?" in sent
        chain = app.store.stories.get_messages(app.session.story_id)
        assert chain[-2].body == "/ooc What genre is this?"  # the line as typed
        assert (chain[-2].kind, chain[-1].kind) == ("ooc", "ooc")

    def test_regenerating_an_ooc_reply_stays_ooc(self, app: App) -> None:
        app.play("I enter the hall.")
        app.play("/ooc What genre is this?")
        app.server.script = lambda body: "Dark fantasy."
        app.play("/regen")
        chain = app.store.stories.get_messages(app.session.story_id)
        assert chain[-1].body == "Dark fantasy."
        assert chain[-1].kind == "ooc"

    def test_resending_a_failed_ooc_question_stays_ooc(self, app: App) -> None:
        app.play("I enter the hall.")
        app.server.fail_after = 0
        app.play("/ooc What genre is this?")
        app.server.fail_after = None
        app.play("/regen")
        chain = app.store.stories.get_messages(app.session.story_id)
        assert (chain[-2].kind, chain[-1].kind) == ("ooc", "ooc")


class TestRoll:
    def test_a_roll_is_rolled_at_record_time_and_frozen(self, app: App) -> None:
        # otaku rolls — the OS's dice, never the model's — and the numbers
        # freeze into the turn's template as it records: the store, the
        # wire, and every later request tell the same roll.
        app.play("/roll 2d6+3 I strike at the shade.")
        stored = app.store.stories.get_messages(app.session.story_id)[0]
        assert stored.body == "/roll 2d6+3 I strike at the shade."  # the line as typed
        assert stored.template is not None and "2d6+3 = " in stored.template
        total = int(stored.template.split("2d6+3 = ", 1)[1].split(" ", 1)[0])
        assert 5 <= total <= 15
        wire = app.server.requests[-1]["messages"][-1]["content"]
        assert f"2d6+3 = {total}" in wire  # the wire carries the same roll
        assert wire.endswith("I strike at the shade.")
        assert "/roll" not in wire  # the spec never reaches the model to re-roll

    def test_a_regenerate_narrates_the_same_numbers(self, app: App) -> None:
        # The roll happened; a fresh take re-tells it, never re-rolls it.
        app.play("/roll 1d20 I leap the chasm.")
        first = app.server.requests[-1]["messages"][-1]["content"]
        app.play("/regen")
        assert app.server.requests[-1]["messages"][-1]["content"] == first

    def test_the_framing_comes_from_the_prompts_file(self, server, tmp_path) -> None:
        # Like every direction: the file IS the injection (see TestTurns).
        root = tmp_path / "state"
        paths = Paths.resolve(root)
        paths.ensure_tree()
        paths.prompts_file.write_text('roll_framing = "<<rolled {dice}>> {body}"\n')
        app = launch(root, server)
        try:
            app.play("/roll 1d4 I duck.")
            assert sent(app).startswith("<<rolled 1d4 = ")
            assert sent(app).endswith(">> I duck.")
        finally:
            app.close()

    def test_a_spec_that_is_not_dice_records_nothing(self, app: App) -> None:
        # Eager validation, like every direction: the refusal lands
        # before a story, a row, or a request exists.
        app.play("/roll banana I flail.")
        assert app.session.story_id is None
        assert app.server.requests == []


class TestInliners:
    def test_prose_keeps_its_slashes_and_plays(self, app: App) -> None:
        line = "She looks up and/or down on 24/08/2026 at https://x.co/cue"
        app.play(line)
        assert sent(app) == line

    def test_an_aside_is_enclosed_and_stays(self, app: App) -> None:
        app.play("She looks up /ooc make her nervous")
        assert sent(app) == "She looks up ((OOC: make her nervous))"
        app.play("She waits.")
        # Still there a turn later: an aside is part of the story from here
        # on, unlike a cue.
        assert app.server.requests[-1]["messages"][0]["content"] == (
            "She looks up ((OOC: make her nervous))"
        )

    def test_a_cue_never_reaches_the_extraction(self, app: App) -> None:
        # A steer is a direction to the model, not a moment in the story —
        # letting the analysis pass read it would write it into the scene.
        app.play("She waits /cue keep it tense")
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.play("/extract")
        chunks = [
            m["content"]
            for request in app.server.requests
            for m in request["messages"]
            if "SCENE" in m["content"]
        ]
        assert chunks, "no extraction request was made"
        assert all("keep it tense" not in chunk for chunk in chunks)
        assert any("She waits" in chunk for chunk in chunks)

    def test_a_cue_reaches_the_model_once_and_is_then_gone(self, app: App) -> None:
        app.play("She waits /cue keep it tense")
        assert sent(app) == "She waits ((OOC: keep it tense))"
        app.play("She waits again.")
        # Spent: the steer shaped one reply and leaves no trace in the next
        # request — though the line itself is stored whole, so the screen
        # and the picker still show what was typed.
        assert app.server.requests[-1]["messages"][0]["content"] == "She waits"
        assert app.store.stories.get_messages(app.session.story_id)[0].body == (
            "She waits /cue keep it tense"
        )


class TestPromptCache:
    """The prompt-cache breakpoints a marked provider's requests carry:
    the system row and the final row as content parts with
    `cache_control`, the middle plain strings — and none of it where the
    provider cannot honour markers or the section said off. The wire is
    the whole assertion."""

    def test_an_openrouter_request_carries_the_markers(self, server, tmp_path) -> None:
        app = _cloud(server, tmp_path)
        try:
            app.play("/system Be terse.")
            app.play("I enter the hall.")
            app.play("I listen.")
            sent = scripted.chat_request(app.server, "I listen.")["messages"]
            assert sent[0]["role"] == "system"
            assert sent[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
            assert sent[-1]["content"][0]["cache_control"] == {"type": "ephemeral"}
            assert all(isinstance(m["content"], str) for m in sent[1:-1])
        finally:
            app.close()

    def test_the_hour_ttl_rides_the_marker(self, server, tmp_path) -> None:
        app = _cloud(server, tmp_path, prompt_cache="1h")
        try:
            app.play("I enter the hall.")
            sent = scripted.chat_request(app.server, "I enter the hall.")["messages"]
            assert sent[-1]["content"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
        finally:
            app.close()

    def test_the_section_can_turn_it_off(self, server, tmp_path) -> None:
        app = _cloud(server, tmp_path, prompt_cache="off")
        try:
            app.play("I enter the hall.")
            sent = scripted.chat_request(app.server, "I enter the hall.")["messages"]
            assert all(isinstance(m["content"], str) for m in sent)
        finally:
            app.close()

    def test_a_local_provider_sends_plain_strings(self, app: App) -> None:
        # The capability is class knowledge: no section key can make a
        # local provider mark, and its wire stays exactly as it was.
        app.play("I enter the hall.")
        sent = scripted.chat_request(app.server, "I enter the hall.")["messages"]
        assert all(isinstance(m["content"], str) for m in sent)


class TestCancelAndKeep:
    def test_a_ctrl_c_in_the_wait_keeps_the_partial_with_smoothing_on(
        self, server, tmp_path
    ) -> None:
        # With smoothing on, the wait is the wrapper's own tick inside the
        # play's frame, and that is where a Ctrl+C lands: what streamed
        # must be recorded all the same, the way a closed stream's is.
        server.script = lambda body: "The light went out, and something stirred in the dark. " * 8
        server.chunk_size = 6
        server.chunk_delay = 0.03
        set_config(tmp_path / "state", smooth_streaming=True)
        app = launch(tmp_path / "state", server)
        try:
            ticks = 0

            def interrupt() -> None:
                nonlocal ticks
                ticks += 1
                if ticks == 40:  # some 0.8 s into the reply: words have streamed
                    raise KeyboardInterrupt

            app.session.set_on_idle(interrupt)
            app.play("I enter the hall.")
            reply = app.store.stories.get_messages(app.session.story_id)[-1]
            assert reply.role == "assistant"
            assert 0 < len(reply.body) < len(server.script({}))
            # The prefill was spent: the request is in /usage, with the
            # time it took and whatever the wire had stated by then.
            chat = next(
                t for t in app.store.usage.get_totals(app.session.story_id) if t.purpose == "chat"
            )
            assert chat.requests == 1 and chat.seconds > 0
        finally:
            app.close()


def _cloud(server: scripted.ModelServer, tmp_path: Path, *, prompt_cache: str = "") -> App:
    """The app over a provider the registry builds as the marking cloud
    client — the section's NAME picks the class."""
    root = tmp_path / "state"
    set_config_provider(root, server, name="openrouter", prompt_cache=prompt_cache)
    return launch(root, server, spec="openrouter/test-model")


class TestLegacyTurns:
    def test_a_turn_written_before_the_syntax_sends_what_it_always_sent(
        self, app: App, tmp_path: Path
    ) -> None:
        # Back then the prefix was stripped before storing and the name was
        # baked into the template. Such a row must compose as it always
        # did: no prefix to strip, no `{name}` to fill. An export document
        # is how one arrives.
        document = (
            "# Old story\n\n"
            f"{EXPORT_MARKER}\n\n"
            "## Messages\n\n"
            '### 1 · user · "((OOC: The user writes as Elara.))\\n{body}"\n'
            "I step into the light.\n\n"
            "### 2 · assistant\n"
            "The hall answers.\n"
        )
        path = tmp_path / "old.md"
        path.write_text(document)
        app.play(f"/import {path}")
        app.play("She waits.")
        wire = app.server.requests[-1]["messages"]
        assert wire[0]["content"] == "((OOC: The user writes as Elara.))\nI step into the light."


class TestUndo:
    def test_undo_discards_the_exchange_but_keeps_it_in_the_tree(self, app: App) -> None:
        app.play("The first turn.")
        app.play("The second turn.")
        app.play("/undo")
        chain = app.store.stories.get_messages(app.session.story_id)
        assert [m.body for m in chain] == ["The first turn.", scripted.CHAT_REPLY]
        # Nothing was deleted: the undone turns stay as siblings.
        assert app.session.messages == chain
        assert app.store.messages.get_parent(3) == 2

    def test_undo_never_swallows_sequential_user_messages(self, app: App, tmp_path) -> None:
        # Sequential user rows are story, not one submission — /undo takes
        # back the played exchange only. A text import is how a run of
        # user rows arises through the surface.
        tale = tmp_path / "tale.txt"
        tale.write_text("First beat.\n\nSecond beat.\n\nThird beat.", encoding="utf-8")
        app.play(f"/import {tale}")
        imported = [m.body for m in app.session.messages]
        app.play("I look around.")
        app.play("/undo")
        assert [m.body for m in app.session.messages] == imported


class TestRegenerate:
    def test_regen_replaces_the_reply_and_siblings_the_old_one(self, app: App) -> None:
        app.play("I roll the dice.")
        app.server.script = lambda body: "It came up six."
        app.play("/regen")
        chain = app.store.stories.get_messages(app.session.story_id)
        assert [m.body for m in chain] == ["I roll the dice.", "It came up six."]
        # Both replies hang off the same prompt: the old one is a sibling.
        assert app.store.messages.get_parent(2) == app.store.messages.get_parent(3) == 1

    def test_regen_resends_the_prompt_a_failure_left_unanswered(self, app: App) -> None:
        # A request that dies before any content records no reply, so the
        # prompt stands alone at the end of the story: /regen sends it again.
        app.server.fail_after = 0
        app.play("I roll the dice.")
        assert [m.role for m in app.session.messages] == ["user"]
        app.server.fail_after = None
        app.play("/regen")
        chain = app.store.stories.get_messages(app.session.story_id)
        assert [m.body for m in chain] == ["I roll the dice.", scripted.CHAT_REPLY]
        assert app.server.requests[-1]["messages"][-1]["content"] == "I roll the dice."

    def test_regen_without_a_model_touches_nothing(self, server, tmp_path, capsys) -> None:
        # The guard runs before the drop and the erase: the reply stays
        # in the chain and on the screen; only the hint prints.
        set_config(tmp_path / "state", seed_sample=True)
        app = launch(tmp_path / "state", server, spec=None)
        try:
            capsys.readouterr()
            before = [m.id for m in app.session.messages]
            app.play("/regen")
            out = capsys.readouterr().out
            assert "No model selected" in out
            assert "Nothing to regenerate" not in out
            assert [m.id for m in app.session.messages] == before
        finally:
            app.close()


def sent(app: App) -> str:
    """The content of the last message the server was actually given."""
    return str(app.server.requests[-1]["messages"][-1]["content"])


def sent_headers(app: App) -> dict[str, str]:
    """The last request's headers, keyed lowercase — a header name is
    case-insensitive on the wire, and the assertion should not care."""
    return {name.lower(): value for name, value in app.server.request_headers[-1].items()}
