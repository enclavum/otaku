"""Playing a story: turns, the wire promise, undo, regenerate, /me."""

from scenarios.support import server as scripted
from scenarios.support.harness import App


class TestTurns:
    def test_a_message_gets_a_reply_and_both_persist(self, app: App) -> None:
        app.play("Я открываю дверь.")
        chain = app.store.stories.get_messages(app.session.story_id)
        assert [(m.role, m.body) for m in chain] == [
            ("user", "Я открываю дверь."),
            ("assistant", scripted.CHAT_REPLY),
        ]

    def test_the_wire_carries_the_message_verbatim(self, app: App) -> None:
        app.play("/system Ты — рассказчик.")
        app.play("Я жду.   С пробелами?")
        sent = app.server.requests[-1]["messages"]
        assert sent[0] == {"role": "system", "content": "Ты — рассказчик."}
        assert sent[1] == {"role": "user", "content": "Я жду.   С пробелами?"}

    def test_me_frames_the_line_at_wire_time_only(self, app: App) -> None:
        app.play("/me Рин: Я выхожу на свет.")
        sent = app.server.requests[-1]["messages"][-1]["content"]
        assert sent == "((OOC: The user writes as Рин.))\nЯ выхожу на свет."
        stored = app.store.stories.get_messages(app.session.story_id)[0]
        assert stored.body == "Я выхожу на свет."  # the body stays bare


class TestUndo:
    def test_undo_discards_the_exchange_but_keeps_it_in_the_tree(self, app: App) -> None:
        app.play("Первый ход.")
        app.play("Второй ход.")
        app.play("/undo")
        chain = app.store.stories.get_messages(app.session.story_id)
        assert [m.body for m in chain] == ["Первый ход.", scripted.CHAT_REPLY]
        # Nothing was deleted: the undone turns stay as siblings.
        assert app.session.messages == chain
        assert app.store.messages.get_parent(3) == 2

    def test_undo_on_an_empty_story_says_so(self, app: App, capsys) -> None:
        app.play("/undo")
        assert "Nothing to undo." in capsys.readouterr().out


class TestRegenerate:
    def test_regen_replaces_the_reply_and_siblings_the_old_one(self, app: App) -> None:
        app.play("Бросаю кость.")
        app.server.script = lambda body: "Выпала шестёрка."
        app.play("/regen")
        chain = app.store.stories.get_messages(app.session.story_id)
        assert [m.body for m in chain] == ["Бросаю кость.", "Выпала шестёрка."]
        # Both replies hang off the same prompt: the old one is a sibling.
        assert app.store.messages.get_parent(2) == app.store.messages.get_parent(3) == 1
