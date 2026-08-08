"""Playing a story: turns, the wire promise, undo, regenerate, and the
roleplay commands /me, /you, /ooc."""

from otaku.paths import Paths
from scenarios.support import server as scripted
from scenarios.support.harness import RULE, App, launch, set_config


class TestTurns:
    def test_a_message_gets_a_reply_and_both_persist(self, app: App) -> None:
        app.play("I open the door.")
        chain = app.store.stories.get_messages(app.session.story_id)
        assert [(m.role, m.body) for m in chain] == [
            ("user", "I open the door."),
            ("assistant", scripted.CHAT_REPLY),
        ]

    def test_a_played_turn_echoes_as_the_grey_block(self, app: App, capsys) -> None:
        app.play("I open the door.")
        assert "> I open the door." in capsys.readouterr().out

    def test_the_configured_dialogue_look_styles_the_reply(self, server, tmp_path, capsys) -> None:
        set_config(tmp_path / "state", dialogue_color="magenta", dialogue_bold=True)
        app = launch(tmp_path / "state", server)
        app.server.script = lambda body: '"Come in," she said.'
        app.play("I knock.")
        app.close()
        assert "\x1b[35m\x1b[1m" in capsys.readouterr().out  # magenta + bold speech

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
        assert stored.body == "I step into the light."  # the body stays bare

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

    def test_an_instant_failure_prints_without_a_blank(self, server, tmp_path, capsys) -> None:
        # A request that dies before any output starts at the margin —
        # the designed gap above the reply, nothing more. The harness
        # seeds llamacpp on a dead port; playing it fails instantly.
        app = launch(tmp_path / "state", server, spec="llamacpp/test-model")
        try:
            app.play("I enter the hall.")
            out = capsys.readouterr().out
            assert "[ error: could not reach" in out
            assert "\n\n\n" not in out
        finally:
            app.close()


class TestMe:
    def test_the_typed_line_echoes_as_the_grey_block(self, app: App, capsys) -> None:
        app.play("/me Elara: I step into the light.")
        assert "> /me Elara: I step into the light." in capsys.readouterr().out

    def test_a_usage_error_stays_plain(self, app: App, capsys) -> None:
        app.play("/me no colon here")
        out = capsys.readouterr().out
        assert "Usage: /me NAME: PROMPT" in out
        assert "> /me" not in out  # no block for a turn that never played

    def test_a_cast_name_resolves_to_its_canonical_form(self, app: App) -> None:
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.play("/extract")  # the Keeper joins the cast
        app.play("/me keeper: I bow.")
        sent = scripted.chat_request(app.server, "I bow.")["messages"][-1]["content"]
        assert "Keeper" in sent  # canonical, not as typed
        assert sent.endswith("I bow.")


class TestYou:
    def test_the_typed_line_echoes_as_the_grey_block(self, app: App, capsys) -> None:
        app.play("I enter the hall.")
        app.play("/you Elara")
        assert "> /you Elara" in capsys.readouterr().out

    def test_you_hands_the_scene_to_the_named_character(self, app: App) -> None:
        app.play("I enter the hall.")
        app.play("/you Elara")
        sent = app.server.requests[-1]["messages"][-1]["content"]
        assert sent.startswith("((OOC")
        assert "Elara" in sent
        chain = app.store.stories.get_messages(app.session.story_id)
        # One body-less turn — the framing IS the turn — and a normal reply.
        assert [(m.role, m.body) for m in chain[-2:]] == [
            ("user", ""),
            ("assistant", scripted.CHAT_REPLY),
        ]

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
    def test_the_typed_line_echoes_as_the_grey_block(self, app: App, capsys) -> None:
        app.play("/ooc What genre is this?")
        assert "> /ooc What genre is this?" in capsys.readouterr().out

    def test_ooc_is_framed_and_marks_both_sides(self, app: App) -> None:
        app.play("I enter the hall.")
        app.play("/ooc What genre is this?")
        sent = app.server.requests[-1]["messages"][-1]["content"]
        assert sent.startswith("((OOC")
        assert "What genre is this?" in sent
        chain = app.store.stories.get_messages(app.session.story_id)
        assert chain[-2].body == "What genre is this?"  # the body stays bare
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

    def test_undo_on_an_empty_story_says_so(self, app: App, capsys) -> None:
        app.play("/undo")
        assert "Nothing to undo." in capsys.readouterr().out

    def test_undo_reports_the_new_ending_when_it_cannot_erase(self, app: App, capsys) -> None:
        # Off a terminal the ledger can never prove an erase (no cursor
        # answer), so /undo falls back to reporting — the erased look is a
        # pty story (scenarios/cli/test_main.py).
        app.play("The first turn.")
        app.play("The second turn.")
        capsys.readouterr()
        app.play("/undo")
        out = capsys.readouterr().out
        assert "[ undone. the story now ends with: ]" in out
        assert "> The first turn." in out  # the surviving exchange, re-echoed
        assert RULE in out  # the break the report lands on is drawn


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

    def test_regen_on_an_empty_story_says_so(self, app: App, capsys) -> None:
        app.play("/regen")
        assert "Nothing to regenerate." in capsys.readouterr().out

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

    def test_regen_announces_itself_when_it_cannot_erase(self, app: App, capsys) -> None:
        app.play("I roll the dice.")
        capsys.readouterr()
        app.play("/regen")
        out = capsys.readouterr().out
        assert "[ regenerating ]" in out
        assert "> I roll the dice." in out  # the prompt being re-run, echoed
        assert RULE in out  # the break the marker lands on is drawn


class TestLast:
    def test_shows_the_recent_exchanges_as_played(self, app: App, capsys) -> None:
        for n in range(3):
            app.play(f"Turn {n}.")
        capsys.readouterr()
        app.play("/last")
        out = capsys.readouterr().out
        assert "> Turn 0." in out
        assert "> Turn 2." in out
        assert scripted.CHAT_REPLY in out
        # The copy is fenced off from the turns it repeats, and named.
        assert out.index(RULE) < out.index("The last 3 turns of this story:")

    def test_a_count_limits_the_echo(self, app: App, capsys) -> None:
        for n in range(3):
            app.play(f"Turn {n}.")
        capsys.readouterr()
        app.play("/last 1")
        out = capsys.readouterr().out
        assert "> Turn 2." in out
        assert "> Turn 1." not in out

    def test_an_empty_story_says_so(self, app: App, capsys) -> None:
        app.play("/last")
        assert "No turns yet." in capsys.readouterr().out

    def test_rejects_a_bad_count(self, app: App, capsys) -> None:
        app.play("/last riddle")
        assert "Usage: /last [N]" in capsys.readouterr().out


class TestClear:
    def test_wipes_the_screen_and_keeps_the_story(self, app: App, capsys) -> None:
        app.play("I open the door.")
        app.play("/clear")
        assert "\x1b[H\x1b[2J" in capsys.readouterr().out
        assert len(app.session.messages) == 2  # the story is untouched


def sent(app: App) -> str:
    """The content of the last message the server was actually given."""
    return str(app.server.requests[-1]["messages"][-1]["content"])
