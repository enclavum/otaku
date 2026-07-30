"""The prompt assembler.

Its contract is the wire promise: the model sees the stored messages and
nothing the code invented but the recap. Framing joins its body at wire
time, consecutive same-role rows become one turn, and an overlong
transcript loses its oldest messages rather than its shape. A long story
goes out as HEAD + RECAP + TAIL: the opening verbatim, the covered scenes
as their summaries (capped, the story-so-far standing in for dropped
ones), and the recent tail verbatim starting right after the last
summarized scene.
"""

from otaku.lore.assembler import assemble, render_preview
from otaku.store.schema import Message, Scene


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


class TestShaping:
    """40 turns, head 5 / tail 10: a covering scene turns the middle into a
    recap; without one everything goes verbatim."""

    def test_short_transcripts_go_out_whole(self) -> None:
        prompt = assemble(
            "", turns(10), 8192, scenes=[scene(8, "sum")], head_messages=5, tail_messages=10
        )
        assert prompt.transcript_kept == 10
        assert prompt.scenes_summarized == 0

    def test_a_covering_scene_becomes_the_recap(self) -> None:
        prompt = assemble(
            "",
            turns(40),
            8192,
            scenes=[scene(20, "The heist unfolded.")],
            recap_header="[So far:]",
            head_messages=5,
            tail_messages=10,
        )
        assert prompt.scenes_summarized == 1
        sent = "\n".join(m.body for m in prompt.messages)
        assert "[So far:]" in sent
        assert "The heist unfolded." in sent

    def test_the_tail_starts_right_after_the_last_summarized_scene(self) -> None:
        prompt = assemble(
            "",
            turns(40),
            8192,
            scenes=[scene(20, "sum")],
            head_messages=5,
            tail_messages=10,
        )
        sent = "\n".join(m.body for m in prompt.messages)
        assert "turn 21" in sent  # first after the boundary
        assert "turn 20" not in sent  # summarized away
        assert prompt.head_count == 5

    def test_a_scene_ending_in_head_or_tail_is_not_summarized(self) -> None:
        prompt = assemble(
            "",
            turns(40),
            8192,
            scenes=[scene(3, "opening"), scene(38, "finale")],
            head_messages=5,
            tail_messages=10,
        )
        assert prompt.scenes_summarized == 0
        assert prompt.transcript_kept == 40

    def test_no_covering_scene_falls_back_to_verbatim(self) -> None:
        prompt = assemble("", turns(40), 8192, head_messages=5, tail_messages=10)
        assert prompt.scenes_summarized == 0
        assert prompt.transcript_kept == 40

    def test_an_oversized_recap_drops_oldest_and_leads_with_the_story_so_far(self) -> None:
        big = "s " * 8000  # ~4000 tokens — alone above the recap's budget share
        scenes = [
            scene(15, big, history="Arc so far."),
            scene(20, "Recent scene summary.", history="Newest arc."),
        ]
        prompt = assemble("", turns(40), 8192, scenes=scenes, head_messages=5, tail_messages=10)
        assert prompt.scenes_summarized == 1
        sent = "\n".join(m.body for m in prompt.messages)
        assert "Newest arc." in sent  # the rollup stands in for dropped summaries
        assert "Recent scene summary." in sent


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


def user(body: str, framing: str | None = None) -> Message:
    return Message(role="user", body=body, framing=framing)


def assistant(body: str) -> Message:
    return Message(role="assistant", body=body)


def turns(n: int) -> list[Message]:
    """n alternating turns with stable ids 1..n."""
    out = []
    for i in range(1, n + 1):
        role = "user" if i % 2 else "assistant"
        out.append(Message(role=role, body=f"turn {i}", id=i))
    return out


def scene(end_id: int, summary: str = "", history: str = "") -> Scene:
    return Scene(
        id=end_id, start_message_id=1, end_message_id=end_id, summary=summary, history=history
    )
