"""Playing a story: turns, the wire promise, undo, regenerate, /me."""

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
