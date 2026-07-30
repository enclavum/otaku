"""Playing a story: turns, the wire promise, undo, regenerate, and the
roleplay commands /me, /you, /ooc."""

from scenarios.support import server as scripted
from scenarios.support.harness import App


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
        assert stored.body == "I step into the light."  # the body stays bare

    def test_a_stream_error_keeps_what_already_arrived(self, app: App, capsys) -> None:
        # The prose the user watched stream is in the story, error or not —
        # the screen and the story must never diverge.
        app.server.fail_after = 1
        app.play("I enter the hall.")
        assert "[error:" in capsys.readouterr().out
        chain = app.store.stories.get_messages(app.session.story_id)
        first_chunk = scripted.CHAT_REPLY[: max(1, len(scripted.CHAT_REPLY) // 3)]
        assert [m.body for m in chain] == ["I enter the hall.", first_chunk]


class TestMe:
    def test_a_cast_name_resolves_to_its_canonical_form(self, app: App) -> None:
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.play("/extract")  # the Keeper joins the cast
        app.play("/me keeper: I bow.")
        sent = scripted.chat_request(app.server, "I bow.")["messages"][-1]["content"]
        assert "Keeper" in sent  # canonical, not as typed
        assert sent.endswith("I bow.")


class TestYou:
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


class TestOoc:
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

    def test_undo_on_an_empty_story_says_so(self, app: App, capsys) -> None:
        app.play("/undo")
        assert "Nothing to undo." in capsys.readouterr().out


class TestRegenerate:
    def test_regen_replaces_the_reply_and_siblings_the_old_one(self, app: App) -> None:
        app.play("I roll the dice.")
        app.server.script = lambda body: "It came up six."
        app.play("/regen")
        chain = app.store.stories.get_messages(app.session.story_id)
        assert [m.body for m in chain] == ["I roll the dice.", "It came up six."]
        # Both replies hang off the same prompt: the old one is a sibling.
        assert app.store.messages.get_parent(2) == app.store.messages.get_parent(3) == 1
