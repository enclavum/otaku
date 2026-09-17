"""The prompt assembler, tested case by case after docs/context_design.md.

Cases 1-2: a story below the verbatim threshold, or one without covering
summaries, goes out whole. Case 3: HEAD verbatim, the covered scenes as
summaries, the TAIL verbatim and scene-aligned; a card is never
summarized away. Case 4: over the limit — the smaller of the window and
`max_context` — the oldest summaries are replaced by the story-so-far
through the last replaced scene. Case 5: still over, the tail target
steps down to its floor; past the floor the assembly refuses. Across all
of them the wire promise holds: the model sees the stored messages and
nothing the code invented but the recap.
"""

import pytest

from otaku.context.assembler import ContextOverflowError, ContextShape, _assemble
from otaku.store.schema import Message, Scene


def assemble(
    system: str,
    messages: list[Message],
    max_context: int | None,
    *,
    scenes: tuple = (),
    recap_header: str = "",
    head_messages: int = 20,
    min_tail_messages: int = 150,
    max_context_setting: int = 0,
):
    """The doc's vocabulary over the `shape` argument, so every case
    below reads like its section."""
    shape = ContextShape(
        head_messages=head_messages,
        min_tail_messages=min_tail_messages,
        max_context_setting=max_context_setting,
        recap_header=recap_header,
        card_framing="",
    )
    return _assemble(system, messages, max_context, scenes=scenes, shape=shape)


class TestShortStory:
    """Case 1: fewer messages than the verbatim threshold."""

    def test_everything_is_sent_verbatim(self) -> None:
        prompt = assemble("", turns(40), 8192, head_messages=20, min_tail_messages=150)
        sent = "\n".join(m.body for m in prompt.messages)
        assert all(f"turn {i}." in sent for i in range(1, 41))
        assert prompt.scenes_summarized == 0
        assert prompt.recap == ""

    def test_even_with_summaries_extracted(self) -> None:
        # The threshold governs, not the summaries' existence.
        prompt = assemble(
            "",
            turns(40),
            8192,
            scenes=(scene(20, "The heist unfolded."),),
            head_messages=20,
            min_tail_messages=150,
        )
        assert prompt.scenes_summarized == 0
        assert "The heist unfolded." not in "\n".join(m.body for m in prompt.messages)


class TestNoSummaries:
    """Case 2: at least the threshold of messages, nothing to cover the
    middle with."""

    def test_everything_is_sent_verbatim(self) -> None:
        prompt = assemble("", turns(40), 8192, head_messages=5, min_tail_messages=10)
        assert prompt.transcript_kept == 40
        assert prompt.scenes_summarized == 0

    def test_a_scene_ending_in_head_or_tail_does_not_count(self) -> None:
        prompt = assemble(
            "",
            turns(40),
            8192,
            scenes=(scene(3, "opening"), scene(38, "finale")),
            head_messages=5,
            min_tail_messages=10,
        )
        assert prompt.scenes_summarized == 0
        assert prompt.transcript_kept == 40


class TestScenesCoverTheMiddle:
    """Case 3: the covered scenes ride as summaries between the verbatim
    head and the scene-aligned tail — the doc's options A and B."""

    def test_option_a_the_tail_starts_after_the_last_covered_scene(self) -> None:
        # 220 messages, scenes ending at 25/42/64/95; 220 - 150 = 70
        # lands inside scene 4, so scenes 1-3 are summarized and the
        # tail runs from message 65.
        scenes = (
            scene(25, "sum one"),
            scene(42, "sum two"),
            scene(64, "sum three"),
            scene(95, "sum four"),
        )
        prompt = assemble("", turns(220), 65536, scenes=scenes, min_tail_messages=150)
        sent = "\n".join(m.body for m in prompt.messages)
        assert prompt.scenes_summarized == 3
        assert "sum three" in sent
        assert "sum four" not in sent  # its scene rides verbatim in the tail
        assert "turn 65." in sent  # the tail starts right after scene 3
        assert "turn 64." not in sent  # summarized away
        assert "turn 20." in sent and "turn 21." not in sent  # the head's edge
        assert prompt.head_count == 20

    def test_option_b_fewer_scenes_move_the_boundary_earlier(self) -> None:
        scenes = (scene(25, "sum one"), scene(42, "sum two"))
        prompt = assemble("", turns(220), 65536, scenes=scenes, min_tail_messages=150)
        sent = "\n".join(m.body for m in prompt.messages)
        assert prompt.scenes_summarized == 2
        assert "turn 43." in sent
        assert "turn 42." not in sent

    def test_the_recap_header_opens_the_recap(self) -> None:
        prompt = assemble(
            "",
            turns(220),
            65536,
            scenes=(scene(64, "sum"),),
            recap_header="[So far:]",
        )
        sent = "\n".join(m.body for m in prompt.messages)
        assert sent.index("[So far:]") < sent.index("sum")

    def test_a_scene_ending_at_the_tails_first_message_stays_verbatim(self) -> None:
        # min_tail_messages is a MINIMUM: with 220 messages and a floor
        # of 150 the tail's first message is 70, so a scene ending
        # exactly there is not summarized — its whole span rides
        # verbatim and the tail grows past the minimum.
        at_boundary = assemble("", turns(220), 65536, scenes=(scene(70, "sum"),))
        assert at_boundary.scenes_summarized == 0
        assert at_boundary.transcript_kept == 220
        before_boundary = assemble("", turns(220), 65536, scenes=(scene(69, "sum"),))
        sent = "\n".join(m.body for m in before_boundary.messages)
        assert before_boundary.scenes_summarized == 1
        assert "turn 70." in sent  # the tail holds min_tail plus the boundary message
        assert "turn 69." not in sent

    def test_no_history_and_the_full_tail_in_the_plain_case(self) -> None:
        prompt = assemble("", turns(220), 65536, scenes=(scene(64, "sum"),))
        assert prompt.history == ""
        assert prompt.tail_target == prompt.tail_setting == 150


class TestCharacterCards:
    """Case 3's special case: a card is never summarized away — it rides
    verbatim in front of its scene's summary."""

    def test_a_card_rides_in_front_of_its_scenes_summary(self) -> None:
        rows = turns(220)
        rows[46] = card(47, "((OOC: Elara joins.))")  # inside scene 3's span
        scenes = (scene(42, "sum two"), scene(64, "sum three"))
        prompt = assemble("", rows, 65536, scenes=scenes, min_tail_messages=150)
        sent = "\n".join(m.body for m in prompt.messages)
        assert sent.index("sum two") < sent.index("((OOC: Elara joins.))")
        assert sent.index("((OOC: Elara joins.))") < sent.index("sum three")

    def test_a_card_body_is_never_read_as_syntax(self) -> None:
        prompt = assemble("", [card(1, "Speech example: hi /cue whisper"), user("I wave.")], 8192)
        assert prompt.messages[0].body.startswith("Speech example: hi /cue whisper")

    def test_a_card_in_the_recap_is_never_read_as_syntax(self) -> None:
        rows = turns(220)
        rows[46] = card(47, "Example: breathe /cue whisper softly")
        prompt = assemble("", rows, 65536, scenes=(scene(64, "sum"),))
        assert "breathe /cue whisper softly" in "\n".join(m.body for m in prompt.messages)


class TestRecapDegrades:
    """Case 4: over the limit, the oldest summaries are replaced by the
    story-so-far through the last replaced scene — never a kept scene's
    rung, which would retell the summaries still riding behind it."""

    # Three covered scenes of ~1000 tokens each; the budget in each test
    # decides how many survive (the reply reserve is 1024).
    def _scenes(self) -> tuple[Scene, ...]:
        return (
            scene(10, "alpha " * 700, history="Arc through one."),
            scene(20, "bravo " * 700, history="Arc through two."),
            scene(25, "delta " * 700, history="Arc through three."),
        )

    def test_the_last_replaced_scenes_history_stands_in(self) -> None:
        # Budget ~1500: the history through scene 2 plus scene 3's
        # summary fit; anything more does not.
        prompt = assemble(
            "",
            turns(40),
            2524,
            scenes=self._scenes(),
            head_messages=5,
            min_tail_messages=10,
        )
        sent = "\n".join(m.body for m in prompt.messages)
        assert prompt.history == "Arc through two."
        assert prompt.scenes_rolled_up == 2
        assert prompt.scenes_summarized == 1
        assert "delta delta" in sent  # the kept summary
        assert "bravo bravo" not in sent  # replaced by the history
        assert "Arc through three." not in sent  # a kept scene's rung never rides

    def test_a_replaced_scene_without_a_rung_falls_back_to_an_older_one(self) -> None:
        one, two, three = self._scenes()
        scenes = (one, replace_history(two, ""), three)
        prompt = assemble("", turns(40), 2524, scenes=scenes, head_messages=5, min_tail_messages=10)
        assert prompt.history == "Arc through one."
        assert prompt.scenes_rolled_up == 1

    def test_without_any_rung_the_dropped_scenes_go_uncovered(self) -> None:
        scenes = tuple(replace_history(s, "") for s in self._scenes())
        prompt = assemble("", turns(40), 2524, scenes=scenes, head_messages=5, min_tail_messages=10)
        assert prompt.history == ""
        assert prompt.scenes_rolled_up == 0
        assert prompt.scenes_summarized == 1

    def test_a_replaced_scenes_card_floats_in_front_of_the_history(self) -> None:
        rows = turns(40)
        rows[7] = card(8, "((OOC: Elara joins.))")  # inside scene 1's span
        prompt = assemble(
            "", rows, 2524, scenes=self._scenes(), head_messages=5, min_tail_messages=10
        )
        sent = "\n".join(m.body for m in prompt.messages)
        assert sent.index("((OOC: Elara joins.))") < sent.index("Arc through two.")

    def test_max_context_caps_a_larger_window(self) -> None:
        # The window would hold everything; the setting caps it, the
        # reply reserve coming off the capped value.
        prompt = assemble(
            "",
            turns(40),
            131072,
            scenes=self._scenes(),
            head_messages=5,
            min_tail_messages=10,
            max_context_setting=2524,
        )
        assert prompt.history == "Arc through two."
        assert prompt.limit == 1500  # the cap minus the reserve
        assert prompt.max_context == 131072

    def test_max_context_zero_means_the_whole_window(self) -> None:
        prompt = assemble(
            "",
            turns(40),
            65536,
            scenes=self._scenes(),
            head_messages=5,
            min_tail_messages=10,
            max_context_setting=0,
        )
        assert prompt.history == ""  # everything fits — case 4 never fires
        assert prompt.scenes_summarized == 3
        assert prompt.limit == 65536 - 1024  # the window minus the reserve (no replies yet)


class TestTailDegrades:
    """Case 5: degrading summaries is not enough — the tail target steps
    down 50 at a time to the 50-message floor, the context rebuilt at
    each rung; past the floor the assembly refuses."""

    def test_the_tail_steps_down_until_the_context_fits(self) -> None:
        # 400 messages of ~15 tokens: the configured 150-message tail
        # alone overflows the budget. A scene ending at message 320 is
        # coverable only once the target drops to 50 — the rebuild then
        # summarizes it and the tail shrinks to 80 messages.
        rows = [user(f"turn {i}. " + "x" * 55, message_id=i) for i in range(1, 401)]
        scenes = (
            scene(100, "sum one", history="Arc through one."),
            scene(200, "sum two", history="Arc through two."),
            scene(240, "sum three", history="Arc through three."),
            scene(320, "sum four", history="Arc through four."),
        )
        prompt = assemble("", rows, 3024, scenes=scenes, head_messages=5, min_tail_messages=150)
        assert prompt.tail_setting == 150
        assert prompt.tail_target == 50
        assert prompt.transcript_kept - prompt.head_count == 80  # messages 321-400
        assert prompt.transcript_tokens <= 3024 - 1024


class TestRejected:
    """Case 6: nothing left to degrade — the assembly refuses with the
    sentence naming the remedies."""

    def test_past_the_floor_the_assembly_refuses(self) -> None:
        # No scene near the end: no rung of the ladder can shrink the
        # scene-aligned tail, and even the floor does not fit.
        rows = [user(f"turn {i}. " + "x" * 55, message_id=i) for i in range(1, 401)]
        scenes = (scene(100, "sum one", history="Arc through one."),)
        with pytest.raises(ContextOverflowError) as caught:
            assemble("", rows, 3024, scenes=scenes, head_messages=5, min_tail_messages=150)
        assert "/extract" in str(caught.value)

    def test_a_story_without_scenes_over_the_limit_refuses_too(self) -> None:
        # Cases 1-2 have nothing to degrade: verbatim either fits or the
        # assembly refuses rather than silently dropping story.
        rows = [user(f"turn {i}. " + "x" * 400, message_id=i) for i in range(1, 41)]
        with pytest.raises(ContextOverflowError):
            assemble("", rows, 2048, head_messages=5, min_tail_messages=10)


class TestWirePromise:
    """Cross-case: the model sees the stored messages and nothing the
    code invented but the recap."""

    def test_sends_a_single_turn_verbatim(self) -> None:
        prompt = assemble("", [user("I open the door.")], 8192)
        assert [(m.role, m.body) for m in prompt.messages] == [("user", "I open the door.")]

    def test_puts_the_system_prompt_first(self) -> None:
        prompt = assemble("Be terse.", [user("Hi.")], 8192)
        assert prompt.messages[0].role == "system"
        assert prompt.messages[0].body == "Be terse."

    def test_omits_an_empty_system_prompt(self) -> None:
        prompt = assemble("", [user("Hi.")], 8192)
        assert prompt.messages[0].role == "user"

    def test_consecutive_same_role_rows_merge_into_one_turn(self) -> None:
        rows = [user("One."), user("Two."), assistant("Three.")]
        prompt = assemble("", rows, 8192)
        assert [m.role for m in prompt.messages] == ["user", "assistant"]
        assert prompt.messages[0].body == "One.\n\nTwo."

    def test_roles_stay_as_stored(self) -> None:
        rows = [assistant("It begins."), user("I nod.")]
        prompt = assemble("", rows, 8192)
        assert [m.role for m in prompt.messages] == ["assistant", "user"]


# ---------- fixtures ----------


def user(body: str, message_id: int = 0) -> Message:
    return Message(id=message_id, role="user", body=body)


def assistant(body: str) -> Message:
    return Message(role="assistant", body=body)


def card(message_id: int, body: str) -> Message:
    return Message(id=message_id, role="user", body=body, kind="card")


def turns(n: int) -> list[Message]:
    return [user(f"turn {i}.", message_id=i) for i in range(1, n + 1)]


def scene(end_id: int, summary: str = "", history: str = "") -> Scene:
    return Scene(
        id=end_id, start_message_id=1, end_message_id=end_id, summary=summary, history=history
    )


def replace_history(s: Scene, history: str) -> Scene:
    return Scene(
        id=s.id,
        start_message_id=s.start_message_id,
        end_message_id=s.end_message_id,
        summary=s.summary,
        history=history,
    )
