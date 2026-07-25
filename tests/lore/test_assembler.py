"""The prompt assembler.

Its contract is the wire promise: the model sees the stored messages and
nothing the code invented. Framing joins its body only here, consecutive
same-role rows become one turn, and an overlong transcript loses its
oldest messages rather than its shape.
"""

from otaku.lore.assembler import assemble, combine_framing, render_preview
from otaku.store.schema import Message


def user(body: str, framing: str | None = None) -> Message:
    return Message(role="user", body=body, framing=framing)


def assistant(body: str) -> Message:
    return Message(role="assistant", body=body)


class TestCombineFraming:
    def test_a_turn_without_framing_is_its_body(self) -> None:
        assert combine_framing("I open the door.", None) == "I open the door."

    def test_a_placeholder_slots_the_body_in(self) -> None:
        framing = "((OOC: as Ryn.))\n{body}"
        assert combine_framing("I wait.", framing) == "((OOC: as Ryn.))\nI wait."

    def test_framing_without_a_placeholder_precedes_the_body(self) -> None:
        assert combine_framing("I wait.", "((OOC: note.))") == "((OOC: note.))\n\nI wait."

    def test_framing_alone_when_there_is_no_body(self) -> None:
        assert combine_framing("", "((OOC: you play Anna.))") == "((OOC: you play Anna.))"

    def test_other_braces_survive(self) -> None:
        framing = "((OOC: roll {2d6} for {name}.))\n{body}"
        assert combine_framing("I roll.", framing) == "((OOC: roll {2d6} for {name}.))\nI roll."

    def test_a_body_with_braces_survives(self) -> None:
        assert combine_framing("I say {hello}.", "note\n{body}") == "note\nI say {hello}."


class TestAssemble:
    def test_sends_a_single_turn_verbatim(self) -> None:
        prompt = assemble("", [user("I open the door.")], 8192)
        assert [(m.role, m.body) for m in prompt.messages] == [("user", "I open the door.")]

    def test_puts_the_system_prompt_first(self) -> None:
        prompt = assemble("Be terse.", [user("Hi.")], 8192)
        assert prompt.messages[0].role == "system"
        assert prompt.messages[0].body == "Be terse."

    def test_omits_an_empty_system_prompt(self) -> None:
        prompt = assemble("", [user("Hi.")], 8192)
        assert all(m.role != "system" for m in prompt.messages)

    def test_keeps_roles_as_stored(self) -> None:
        prompt = assemble("", [user("a"), assistant("b"), user("c")], 8192)
        assert [m.role for m in prompt.messages] == ["user", "assistant", "user"]

    def test_merges_consecutive_same_role_turns(self) -> None:
        prompt = assemble("", [user("first"), user("second"), assistant("reply")], 8192)
        assert [(m.role, m.body) for m in prompt.messages] == [
            ("user", "first\n\nsecond"),
            ("assistant", "reply"),
        ]

    def test_joins_framing_to_its_body_on_the_wire(self) -> None:
        prompt = assemble("", [user("I wait.", "((OOC: as Ryn.))\n{body}")], 8192)
        assert prompt.messages[0].body == "((OOC: as Ryn.))\nI wait."

    def test_adds_nothing_of_its_own(self) -> None:
        messages = [user("I open the door."), assistant("It creaks.")]
        prompt = assemble("Be terse.", messages, 8192)
        sent = "".join(m.body for m in prompt.messages)
        assert sent == "Be terse.I open the door.It creaks."

    def test_reports_what_it_kept(self) -> None:
        prompt = assemble("", [user("a"), assistant("b")], 8192)
        assert prompt.transcript_total == 2
        assert prompt.transcript_kept == 2

    def test_defaults_the_window_when_unknown(self) -> None:
        assert assemble("", [user("a")], None).context_max > 0


class TestBudget:
    LONG = "word " * 500  # ~2500 chars, ~625 tokens

    def test_drops_the_oldest_messages_when_over_budget(self) -> None:
        messages = [user(f"{i} {self.LONG}") for i in range(10)]
        prompt = assemble("", messages, 1024)
        assert prompt.transcript_kept < prompt.transcript_total

    def test_keeps_the_newest_message(self) -> None:
        messages = [user(f"{i} {self.LONG}") for i in range(10)]
        prompt = assemble("", messages, 1024)
        assert "9 " in prompt.messages[-1].body

    def test_never_trims_below_two_messages(self) -> None:
        messages = [user(self.LONG), assistant(self.LONG), user(self.LONG)]
        prompt = assemble("", messages, 64)
        assert prompt.transcript_kept >= 2

    def test_keeps_everything_that_fits(self) -> None:
        messages = [user("short"), assistant("also short")]
        prompt = assemble("", messages, 8192)
        assert prompt.transcript_kept == 2


class TestPreview:
    def test_shows_every_message_that_will_be_sent(self) -> None:
        preview = render_preview(assemble("Be terse.", [user("Hi."), assistant("Hello.")], 8192))
        assert "Be terse." in preview
        assert "Hi." in preview
        assert "Hello." in preview

    def test_marks_each_role(self) -> None:
        preview = render_preview(assemble("", [user("Hi.")], 8192))
        assert "[user]" in preview

    def test_reports_the_window(self) -> None:
        preview = render_preview(assemble("", [user("Hi.")], 8192))
        assert "8,192" in preview
