"""The prompt assembler, tested case by case after docs/context_design.md.

Cases 1-2: a story below the verbatim threshold, or one without covering
summaries, goes out whole. Case 3: HEAD verbatim, the covered scenes as
summaries, the TAIL verbatim and scene-aligned; a card is never
summarized away. Case 4: over the limit — the smaller of the window and
`max_context` — the oldest summaries are replaced by the story-so-far
through the last replaced scene. Case 5: still over, the tail target
steps down to its floor; past the floor the assembly refuses. Across all
of them the wire promise holds: the model sees the stored messages and
nothing the code invented but the recap — and the injections it was
handed, each where it was told to go, a stored reply's tool calls as the
story's tool set has them, a tool's turn as the reader's, framed. The
join rule (`get_join_type`, its write side `join`) decides how a row
joins the text before it — off the two rows' modes and the line breaks
at their edges — on the wire and in the canvas alike.
"""

from dataclasses import dataclass, replace

import pytest

from otaku.context.assembler import (
    IMAGE_TOKENS,
    ContextOverflowError,
    ContextShape,
    JoinType,
    WirePicture,
    assemble_story,
    context_in_force,
    get_join_type,
    join,
)
from otaku.context.injections import Injection
from otaku.context.tool_calls import ToolSet
from otaku.providers import PicturesRide
from otaku.providers.openai.templates import PLAIN, TEXT_TEMPLATES
from otaku.store.schema import Attachment, InjectionPosition, Message, Scene, StoryMode

CAT = Attachment(file="pic-0001-20260918-a3f9c1e2.jpg", width=2, height=2, size=3)
CAT_PICTURE = WirePicture(b"cat", "image/jpeg")
NO_TOOLS = ToolSet()  # every call leaves the wire


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
    files=None,
    newest_pictures_only: bool = False,
    injections: tuple[Injection, ...] = (),
    tool_set: ToolSet = NO_TOOLS,
):
    """The doc's vocabulary over the door's arguments, so every case
    below reads like its section, with a stand-in store answering the
    reads the door makes. `files` is the folder the pictures are read
    from; None, the default, is a model that cannot see."""
    shape = ContextShape(
        head_messages=head_messages,
        min_tail_messages=min_tail_messages,
        max_context=context_in_force(max_context, max_context_setting),
    )
    if files is None:
        pictures_ride = PicturesRide.NONE
    else:
        pictures_ride = PicturesRide.LATEST if newest_pictures_only else PicturesRide.EACH
    return assemble_story(
        _Store(scenes, files),
        1,
        system=system,
        messages=messages,
        injections=injections,
        tool_set=tool_set,
        prompts=_Prompts(recap_header),
        shape=shape,
        pictures_ride=pictures_ride,
    )


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
        assert prompt.head + prompt.tail == 40
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
        assert prompt.head + prompt.tail == 40


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
        assert prompt.head == 20

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
        assert at_boundary.head + at_boundary.tail == 220
        before_boundary = assemble("", turns(220), 65536, scenes=(scene(69, "sum"),))
        sent = "\n".join(m.body for m in before_boundary.messages)
        assert before_boundary.scenes_summarized == 1
        assert "turn 70." in sent  # the tail holds min_tail plus the boundary message
        assert "turn 69." not in sent

    def test_no_history_and_the_full_tail_in_the_plain_case(self) -> None:
        prompt = assemble("", turns(220), 65536, scenes=(scene(64, "sum"),))
        assert not prompt.history
        assert prompt.tail_target == 150


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
        assert prompt.history and "Arc through two." in sent
        assert prompt.scenes_rolled_up == 2
        assert prompt.scenes_summarized == 1
        assert "delta delta" in sent  # the kept summary
        assert "bravo bravo" not in sent  # replaced by the history
        assert "Arc through three." not in sent  # a kept scene's rung never rides

    def test_a_replaced_scene_without_a_rung_falls_back_to_an_older_one(self) -> None:
        one, two, three = self._scenes()
        scenes = (one, replace_history(two, ""), three)
        prompt = assemble("", turns(40), 2524, scenes=scenes, head_messages=5, min_tail_messages=10)
        assert prompt.history and "Arc through one." in prompt.recap
        assert prompt.scenes_rolled_up == 1

    def test_without_any_rung_the_dropped_scenes_go_uncovered(self) -> None:
        scenes = tuple(replace_history(s, "") for s in self._scenes())
        prompt = assemble("", turns(40), 2524, scenes=scenes, head_messages=5, min_tail_messages=10)
        assert not prompt.history
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
        assert prompt.history and "Arc through two." in prompt.recap
        assert prompt.limit == 1500  # the cap minus the reserve

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
        assert not prompt.history  # everything fits — case 4 never fires
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
        assert prompt.tail_target == 50
        assert prompt.tail == 80  # messages 321-400
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

    def test_the_refusal_carries_the_figures_that_did_not_meet(self) -> None:
        # The smallest the story gets, the limit it had to fit and the
        # window — numbers a status can show as a percent over 100.
        rows = [user(f"turn {i}. " + "x" * 400, message_id=i) for i in range(1, 41)]
        with pytest.raises(ContextOverflowError) as caught:
            assemble("", rows, 2048, head_messages=5, min_tail_messages=10)
        refusal = caught.value
        assert refusal.context == 2048
        assert 0 < refusal.limit < refusal.context
        assert refusal.tokens > refusal.limit


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


class TestInjections:
    """Text the context carries besides the story. At "system" it is
    appended to the system message, after the premise; at a number it
    is a user row of its own, placed before that one of the reader's
    messages counted from the end — 1 the newest, which so keeps the
    last word — that never climbs past the recap, and rejoins its
    same-role neighbour like any row. An empty one is not sent. It
    costs its tokens, and the turn a numbered one lands in, with every
    turn after, is `volatile`: next request it reads differently."""

    def test_one_rides_before_the_newest_message(self) -> None:
        prompt = assemble("", exchange(), 8192, injections=(end("Remember."),))
        assert [(m.role, m.body) for m in prompt.messages] == [
            ("user", "One."),
            ("assistant", "Two."),
            ("user", "Remember.\n\nThree."),
        ]

    def test_a_position_counts_the_readers_messages_from_the_end(self) -> None:
        # A reply is never counted: 2 is the reader's previous message.
        rows = [user("One."), assistant("Two."), user("Three."), assistant("Four."), user("Five.")]
        previous = assemble("", rows, 8192, injections=(end("Remember.", 2),))
        assert [m.body for m in previous.messages] == [
            "One.",
            "Two.",
            "Remember.\n\nThree.",
            "Four.",
            "Five.",
        ]
        third = assemble("", rows, 8192, injections=(end("Remember.", 3),))
        assert third.messages[0].body == "Remember.\n\nOne."

    def test_the_newest_message_always_keeps_the_last_word(self) -> None:
        # The nearest place is before the newest message: no position
        # names one after it.
        prompt = assemble("", exchange(), 8192, injections=(end("Remember.", 1),))
        assert prompt.messages[-1].body == "Remember.\n\nThree."

    def test_a_position_past_the_top_stops_at_the_top(self) -> None:
        prompt = assemble("", exchange(), 8192, injections=(end("Remember.", 99),))
        assert prompt.messages[0].body == "Remember.\n\nOne."

    def test_the_recap_is_a_wall(self) -> None:
        # The recap stands where the middle was; an injection stays in the
        # tail under it, however deep it was asked to go.
        prompt = assemble(
            "",
            turns(40),
            8192,
            scenes=(scene(20, "The heist unfolded."),),
            head_messages=5,
            min_tail_messages=10,
            injections=(end("Remember.", 99),),
        )
        sent = "\n\n".join(m.body for m in prompt.messages)
        recap, injected, tail = (
            sent.index("The heist unfolded."),
            sent.index("Remember."),
            sent.index("turn 21."),
        )
        assert recap < injected < tail

    def test_the_same_place_keeps_the_order_given(self) -> None:
        both = (end("First."), end("Second."))
        prompt = assemble("", exchange(), 8192, injections=both)
        assert prompt.messages[-1].body == "First.\n\nSecond.\n\nThree."

    def test_a_system_injection_follows_the_premise(self) -> None:
        prompt = assemble("Be terse.", exchange(), 8192, injections=(system("Ask rarely."),))
        assert (prompt.messages[0].role, prompt.messages[0].body) == (
            "system",
            "Be terse.\n\nAsk rarely.",
        )
        assert [m.body for m in prompt.messages[1:]] == ["One.", "Two.", "Three."]

    def test_without_a_premise_the_system_message_is_the_injection(self) -> None:
        prompt = assemble("", exchange(), 8192, injections=(system("Ask rarely."),))
        assert (prompt.messages[0].role, prompt.messages[0].body) == ("system", "Ask rarely.")

    def test_an_empty_injection_is_not_sent(self) -> None:
        plain = assemble("", exchange(), 8192)
        prompt = assemble("", exchange(), 8192, injections=(end(""), system("")))
        assert prompt.messages == plain.messages

    def test_an_injection_costs_its_tokens(self) -> None:
        text = "x" * 400
        plain = assemble("", exchange(), 8192)
        at_end = assemble("", exchange(), 8192, injections=(end(text),))
        assert at_end.transcript_tokens == plain.transcript_tokens + 100
        in_system = assemble("", exchange(), 8192, injections=(system(text),))
        assert in_system.system_tokens == 100
        assert in_system.transcript_tokens == plain.transcript_tokens

    def test_an_injection_that_does_not_fit_refuses_like_the_story(self) -> None:
        with pytest.raises(ContextOverflowError):
            assemble("", exchange(), 2048, injections=(end("x" * 40_000),))

    def test_the_newest_cue_stays_live_under_an_injection(self) -> None:
        rows = [user("One."), assistant("Two."), user("I go. /cue hurry")]
        prompt = assemble("", rows, 8192, injections=(end("Remember."),))
        assert prompt.messages[-1].body == "Remember.\n\nI go. ((OOC: hurry))"

    def test_a_picture_still_rides_its_own_message(self) -> None:
        rows = [user("One."), assistant("Two."), replace(user("Look."), attachments=(CAT,))]
        prompt = assemble("", rows, 8192, files=_CatFolder(), injections=(end("Remember.", 2),))
        assert [m.images for m in prompt.messages] == [(), (), (CAT_PICTURE,)]

    def test_the_turn_it_lands_in_and_every_turn_after_are_volatile(self) -> None:
        newest = assemble("Be terse.", exchange(), 8192, injections=(end("Remember."),))
        assert [m.volatile for m in newest.messages] == [False, False, False, True]
        deeper = assemble("Be terse.", exchange(), 8192, injections=(end("Remember.", 2),))
        assert [m.volatile for m in deeper.messages] == [False, True, True, True]

    def test_a_system_injection_makes_nothing_volatile(self) -> None:
        prompt = assemble("", exchange(), 8192, injections=(system("Ask rarely."),))
        assert not any(m.volatile for m in prompt.messages)


class TestTheJoin:
    """`get_join_type`, the one join rule for the text wire and the
    canvas — read off the two rows: their modes, and the line breaks at
    the boundary. Spaces at the edges decide nothing."""

    def test_two_story_rows_join_inline_whatever_the_spaces_at_their_edges(self) -> None:
        for before, after in (
            ("She waited", "for the bus."),
            ("She waited ", "for the bus."),
            ("She waited", "  for the bus."),
            ("She waited\t", " for the bus."),
        ):
            got = get_join_type(story(user(before)), story(assistant(after)))
            assert got is JoinType.INLINE, (before, after)
        # the reader's line after a reply the same
        assert get_join_type(story(assistant("Rain.")), story(user("And the door"))) is (
            JoinType.INLINE
        )

    def test_a_chat_row_on_either_side_is_a_paragraph(self) -> None:
        assert get_join_type(user("One"), story(assistant("Two"))) is JoinType.PARAGRAPH
        assert get_join_type(story(user("One")), assistant("Two")) is JoinType.PARAGRAPH
        assert get_join_type(user("One "), assistant(" Two")) is JoinType.PARAGRAPH

    def test_a_line_break_at_the_boundary_is_a_paragraph(self) -> None:
        for before, after in (
            ("She waited\n", "The bus."),
            ("She waited\n ", "The bus."),
            ("She waited", "\n\nThe bus."),
            ("She waited", " \n The bus."),
        ):
            got = get_join_type(story(user(before)), story(assistant(after)))
            assert got is JoinType.PARAGRAPH, (before, after)

    def test_the_first_row_and_a_cards_rows_stand_alone_in_any_mode(self) -> None:
        assert get_join_type(None, story(user("the bus."))) is JoinType.PARAGRAPH
        # a card's line, and the greeting its card made
        assert get_join_type(story(user("One")), story(card(1, "Mara"))) is JoinType.PARAGRAPH
        greeting = story(replace(assistant("Welcome."), provider="card"))
        assert get_join_type(story(user("One")), greeting) is JoinType.PARAGRAPH
        assert get_join_type(greeting, story(user("I nod."))) is JoinType.PARAGRAPH

    def test_a_tool_turn_runs_on_in_story_mode_as_any_row(self) -> None:
        # story mode draws no tool's turn, so the reply after it goes on in
        # the paragraph the call interrupted
        assert get_join_type(story(assistant("Look")), story(tool("Tides."))) is JoinType.INLINE
        assert get_join_type(story(tool("Tides.")), story(assistant("Six."))) is JoinType.INLINE
        # in chat mode, a paragraph as every chat row
        assert get_join_type(tool("Tides."), story(assistant("Six."))) is JoinType.PARAGRAPH

    def test_the_wire_joins_consecutive_rows_by_it(self) -> None:
        prompt = assemble(
            "",
            [
                story(user("I was waiting")),
                story(assistant("for the bus.")),
                story(assistant("\n\nIt rained.")),
                story(user("and the door")),
                story(assistant("swung open.")),
                user("Then?"),
            ],
            None,
        )
        assert [(t.role, t.body, t.join_type) for t in prompt.messages] == [
            ("user", "I was waiting", JoinType.PARAGRAPH),
            ("assistant", "for the bus.\n\nIt rained.", JoinType.INLINE),
            ("user", "and the door", JoinType.INLINE),
            ("assistant", "swung open.", JoinType.INLINE),
            ("user", "Then?", JoinType.PARAGRAPH),
        ]

    def test_a_paragraphs_rows_join_after_a_blank_line(self) -> None:
        rows = [user("One."), assistant("Two."), assistant("\n\n\nThree."), user("Four.")]
        prompt = assemble("", rows, None)
        assert [(t.body, t.join_type) for t in prompt.messages] == [
            ("One.", JoinType.PARAGRAPH),
            ("Two.\n\nThree.", JoinType.PARAGRAPH),
            ("Four.", JoinType.PARAGRAPH),
        ]


class TestJoin:
    """`join`, the join rule's write side: one space inline, a blank line
    for a paragraph, the whitespace at the two edges given up for it."""

    def test_inline_is_one_space(self) -> None:
        assert join("She waited  ", JoinType.INLINE, "\tfor the bus.") == (
            "She waited for the bus."
        )

    def test_a_paragraph_is_one_blank_line(self) -> None:
        assert join("One.\n", JoinType.PARAGRAPH, "\n\n\nTwo.") == "One.\n\nTwo."
        assert join("One.", JoinType.PARAGRAPH, "Two.") == "One.\n\nTwo."


class TestTheStoryAsOneText:
    def test_the_turns_read_as_one_text_with_their_seams(self) -> None:
        prompt = assemble(
            "Be brief.",
            [
                story(user("I was waiting")),
                story(assistant("for the bus.")),
                user("Then?"),
                assistant("Nothing came."),
                story(user("said no one")),
                story(assistant("and left.")),
            ],
            None,
        )
        assert prompt.as_story_text() == (
            "I was waiting for the bus.\n\nThen?\n\nNothing came.\n\nsaid no one and left."
        )

    def test_a_paragraphs_blank_line_lead_is_not_drawn_twice(self) -> None:
        rows = [story(user("One.")), story(assistant("\n\nTwo.")), story(assistant("\n\n\nThree."))]
        assert assemble("", rows, None).as_story_text() == "One.\n\nTwo.\n\nThree."

    def test_a_tool_turn_is_a_paragraph_of_its_own_framed_as_the_model_reads_it(self) -> None:
        rows = [
            user("Look it up."),
            assistant("```otk-search\ntides\n```"),
            tool("Tides: high at six."),
            assistant(" It is six."),
        ]
        prompt = assemble("", rows, None, tool_set=ToolSet(on=frozenset({"search"})))
        assert prompt.as_story_text() == (
            "Look it up.\n\n```otk-search\ntides\n```\n\n"
            "<<search: Tides: high at six.>>\n\nIt is six."
        )

    def test_the_system_text_is_the_premise_and_what_was_injected_after_it(self) -> None:
        prompt = assemble("Be brief.", exchange(), None, injections=(system("ASK HOW"),))
        assert prompt.system == "Be brief.\n\nASK HOW"
        assert prompt.system == next(t.body for t in prompt.messages if t.role == "system")
        assert "Be brief." not in prompt.as_story_text()

    def test_without_a_premise_there_is_no_system_turn_and_the_text_is_empty(self) -> None:
        prompt = assemble("", exchange(), None)
        assert prompt.system == ""
        assert all(t.role != "system" for t in prompt.messages)

    def test_the_text_wires_system_is_the_framing_around_the_premise(self) -> None:
        # the standing instruction stands even where the premise is empty:
        # a model turn needs a turn before it
        assert assemble("Be brief.", exchange(), None).story_system == "<<frame Be brief.>>"
        assert assemble("", exchange(), None).story_system == "<<frame >>"
        injected = assemble("Be brief.", exchange(), None, injections=(system("ASK HOW"),))
        assert injected.story_system == "<<frame Be brief.\n\nASK HOW>>"

    def test_as_text_renders_the_framed_premise_and_the_story_in_the_template(self) -> None:
        rows = [story(user("I was waiting")), story(assistant("for the bus."))]
        prompt = assemble("Be brief.", rows, None)
        assert prompt.as_text(PLAIN) == "<<frame Be brief.>>\n\nI was waiting for the bus."
        assert prompt.as_text(TEXT_TEMPLATES["gemma4"], "\n\n") == (
            "<bos><|turn>system\n<<frame Be brief.>><turn|>\n"
            "<|turn>model\nI was waiting for the bus.\n\n"
        )


class TestToolCalls:
    """A stored reply's tool calls go on the wire as the story's tool set
    has them: a call of a tool that is on as written, one of any other
    tool — switched off, or one this build does not have — gone, the
    reader's rows untouched."""

    def test_a_call_of_a_tool_that_is_on_goes_as_written(self) -> None:
        prompt = assemble("", noted(), 8192, tool_set=ToolSet(on=frozenset({"note"})))
        assert prompt.messages[1].body == "Two.\n\n```otk-note\nthe seal\n```"

    def test_a_call_of_any_other_tool_leaves_the_wire(self) -> None:
        prompt = assemble("", noted(), 8192, tool_set=NO_TOOLS)
        assert [m.body for m in prompt.messages] == ["One.", "Two.", "Three."]

    def test_a_tool_turn_goes_as_the_readers_turn_framed_by_the_prompts(self) -> None:
        # the wire has two sides, and the result is what the model is
        # told: the tool named as the row's `provider` names it, the
        # framing prompts.toml's
        rows = [
            user("Look it up."),
            assistant("```otk-search\ntides\n```"),
            tool("Tides: high at six."),
            assistant("It is six."),
        ]
        prompt = assemble("", rows, 8192, tool_set=ToolSet(on=frozenset({"search"})))
        assert [(m.role, m.body) for m in prompt.messages] == [
            ("user", "Look it up."),
            ("assistant", "```otk-search\ntides\n```"),
            ("user", "<<search: Tides: high at six.>>"),
            ("assistant", "It is six."),
        ]


# ---------- fixtures ----------


def user(body: str, message_id: int = 0) -> Message:
    return Message(id=message_id, role="user", body=body)


def assistant(body: str) -> Message:
    return Message(role="assistant", body=body)


def story(row: Message) -> Message:
    """The row as written in story mode."""
    return replace(row, mode=StoryMode.STORY)


def card(message_id: int, body: str) -> Message:
    return Message(id=message_id, role="user", body=body, kind="card")


def tool(body: str, name: str = "search") -> Message:
    """A tool's turn: its result, the tool's name its provider."""
    return Message(role="tool", kind="tool", body=body, provider=name)


def exchange() -> list[Message]:
    """A played exchange awaiting its reply: the newest message is the user's."""
    return [user("One."), assistant("Two."), user("Three.")]


def noted() -> list[Message]:
    """An exchange whose reply carries a note."""
    return [user("One."), assistant("Two.\n\n```otk-note\nthe seal\n```"), user("Three.")]


def end(text: str, depth: int = 1) -> Injection:
    """An injection before one of the reader's messages, counted from the end."""
    return Injection(owner="reminder", text=text, position=InjectionPosition(depth))


def system(text: str) -> Injection:
    return Injection(owner="ask", text=text, position=InjectionPosition())


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


class TestPictures:
    """A picture rides the verbatim row it was attached to, and costs
    the estimate there; a summarized row's is neither sent nor missed;
    a loader that answers nothing — no vision, a file gone — sends none
    and counts the omission on the verbatim rows alone."""

    def test_a_picture_rides_its_verbatim_row(self) -> None:
        messages = [Message("user", "look", attachments=(CAT,)), Message("assistant", "a cat")]
        prompt = assemble("", messages, None, files=CAT_FOLDER)
        assert prompt.messages[0].images == (CAT_PICTURE,)
        assert prompt.messages[1].images == ()
        assert (prompt.pictures_sent, prompt.pictures_omitted) == (1, 0)

    def test_a_picture_costs_its_estimate(self) -> None:
        with_it = assemble(
            "", [Message("user", "look", attachments=(CAT,))], None, files=CAT_FOLDER
        )
        without = assemble("", [Message("user", "look")], None)
        assert with_it.transcript_tokens - without.transcript_tokens == IMAGE_TOKENS

    def test_without_a_loader_nothing_rides_and_the_omission_is_counted(self) -> None:
        prompt = assemble("", [Message("user", "look", attachments=(CAT,))], None)
        assert prompt.messages[0].images == ()
        assert (prompt.pictures_sent, prompt.pictures_omitted) == (0, 1)
        assert (
            prompt.transcript_tokens
            == assemble("", [Message("user", "look")], None).transcript_tokens
        )

    def test_a_file_the_folder_cannot_answer_for_is_an_omission(self) -> None:
        gone = Attachment(file="pic-0001-20260918-7b02d4ee.png", width=1, height=1, size=1)
        messages = [Message("user", "look", attachments=(CAT, gone))]
        prompt = assemble("", messages, None, files=CAT_FOLDER)
        assert prompt.messages[0].images == (CAT_PICTURE,)
        assert (prompt.pictures_sent, prompt.pictures_omitted) == (1, 1)

    def test_a_summarized_row_loses_its_picture_and_a_tail_row_keeps_it(self) -> None:
        # Option A's story: 220 turns, scenes 1-3 summarized, the tail
        # from turn 65. A picture on turn 30 rides the summary away —
        # neither sent nor counted as missed; one on turn 200 rides.
        story = turns(220)
        story[29] = replace(story[29], attachments=(CAT,))
        story[199] = replace(story[199], attachments=(CAT,))
        scenes = (scene(25, "sum one"), scene(42, "sum two"), scene(64, "sum three"))
        prompt = assemble("", story, 65536, scenes=scenes, min_tail_messages=150, files=CAT_FOLDER)
        assert prompt.scenes_summarized == 3
        (with_it,) = [turn for turn in prompt.messages if turn.images]
        assert "turn 200." in with_it.body and "turn 30." not in with_it.body
        assert (prompt.pictures_sent, prompt.pictures_omitted) == (1, 0)

    def test_with_newest_pictures_only_earlier_rows_pictures_are_held_back(self) -> None:
        # An engine that gathers every picture onto the latest prompt
        # gets the newest row's alone; the earlier row's is held, not
        # omitted, and costs nothing.
        messages = [
            Message("user", "one", attachments=(CAT,)),
            Message("assistant", "a cat"),
            Message("user", "two", attachments=(CAT,)),
        ]
        prompt = assemble("", messages, None, files=CAT_FOLDER, newest_pictures_only=True)
        assert [turn.images for turn in prompt.messages] == [(), (), (CAT_PICTURE,)]
        assert (prompt.pictures_sent, prompt.pictures_held, prompt.pictures_omitted) == (1, 1, 0)
        plain = assemble("", [replace(m, attachments=()) for m in messages], None)
        assert prompt.transcript_tokens - plain.transcript_tokens == IMAGE_TOKENS

    def test_with_newest_pictures_only_the_newest_pictured_row_rides(self) -> None:
        # A follow-up without a picture still reaches the model with the
        # picture it is about: the newest row that carries any rides,
        # however many rows after it carry none.
        messages = [
            Message("user", "one", attachments=(CAT,)),
            Message("assistant", "a cat"),
            Message("user", "and the left corner?"),
        ]
        prompt = assemble("", messages, None, files=CAT_FOLDER, newest_pictures_only=True)
        assert [turn.images for turn in prompt.messages] == [(CAT_PICTURE,), (), ()]
        assert (prompt.pictures_sent, prompt.pictures_held) == (1, 0)

    def test_same_role_rows_rejoin_with_their_pictures(self) -> None:
        messages = [
            Message("user", "one", attachments=(CAT,)),
            Message("user", "two", attachments=(CAT,)),
        ]
        prompt = assemble("", messages, None, files=CAT_FOLDER)
        (turn,) = prompt.messages
        assert turn.images == (CAT_PICTURE, CAT_PICTURE)
        assert prompt.pictures_sent == 2


class _CatFolder:
    """A files folder holding the cat and nothing else: the store's own
    `get`, answered in memory."""

    def get(self, name: str) -> tuple[bytes, str] | None:
        return (b"cat", "image/jpeg") if name == CAT.file else None


CAT_FOLDER = _CatFolder()


class _Store:
    """The store as the door reads it — the story's current scenes, its
    cast's archives (none: a card row sends its body as it stands) and
    the files folder — answered in memory, nothing on disk."""

    def __init__(self, scenes: tuple, files: object) -> None:
        self.scenes = _Scenes(scenes)
        self.characters = _Characters()
        self.files = files if files is not None else _CatFolder()


class _Scenes:
    def __init__(self, scenes: tuple) -> None:
        self._scenes = list(scenes)

    def get_current(self, story_id: int, message_ids: list[int]) -> list[Scene]:
        return self._scenes


class _Characters:
    def list(self, story_id: int) -> list:
        return []


@dataclass(frozen=True)
class _Prompts:
    """What the door reads of the prompts: the recap header and the card
    template (empty: no archive to compose from anyway)."""

    recap_header: str
    card_framing: str = ""
    story_framing: str = "<<frame {premise}>>"
    tool_framing: str = "<<{tool}: {body}>>"
