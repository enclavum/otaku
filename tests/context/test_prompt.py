"""The request as the assembler hands it over, built here from pieces by
hand: what it tells of itself — the system message, the figures read off
its pieces — and how each wire sends it. The chat wire merges each run
of one role into a turn, a tool's result the reader's, a turn volatile
from the first injected piece on; the text wire joins the chat wire's
turns into one text in a template's markers; the preview's pieces of
the text wire join back into exactly what is sent.
"""

from itertools import pairwise

from otaku.context.prompt import (
    IMAGE_TOKENS_ESTIMATE,
    AssembledPrompt,
    CardSent,
    InjectionSent,
    JoinType,
    PromptPiece,
    PromptPieceMetadata,
    Provenance,
    WirePicture,
    WireTurn,
    estimate_tokens,
    join,
)
from otaku.providers.openai.templates import PLAIN, TEXT_TEMPLATES
from otaku.store.schema import InjectionPosition

CAT = WirePicture(b"cat", "image/jpeg")
DOG = WirePicture(b"dog", "image/png")
FRAMING = "<<frame {premise}>>"
AT_SYSTEM = InjectionPosition()
NEWEST = InjectionPosition(1)


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


class TestEstimateTokens:
    def test_four_characters_make_a_token(self) -> None:
        assert estimate_tokens("x" * 400) == 100

    def test_any_text_costs_at_least_one(self) -> None:
        assert estimate_tokens("Hi") == 1


class TestTheSystemMessage:
    """The system message is its pieces — the premise, then each
    injection at the system position — a blank line apart, "" when the
    request carries none; the text wire frames it with `story_framing`."""

    def test_the_premise_and_the_injections_after_it_a_blank_line_apart(self) -> None:
        prompt = request(premise("Be brief."), injected("ASK HOW", AT_SYSTEM), said("user", "Hi."))
        assert prompt.system == "Be brief.\n\nASK HOW"

    def test_without_a_premise_the_system_message_is_the_injection(self) -> None:
        assert request(injected("ASK HOW", AT_SYSTEM), said("user", "Hi.")).system == "ASK HOW"

    def test_a_request_without_one_has_none(self) -> None:
        prompt = request(said("user", "Hi."))
        assert prompt.system == ""
        assert prompt.system_tokens == 0

    def test_its_cost_is_its_estimate_and_the_total_adds_the_transcripts(self) -> None:
        prompt = request(premise("x" * 400), said("user", "Hi."), transcript_tokens=7)
        assert prompt.system_tokens == 100
        assert prompt.total_tokens == 107

    def test_the_text_wires_system_is_the_framing_around_it(self) -> None:
        injected_too = request(premise("Be brief."), injected("ASK HOW", AT_SYSTEM))
        assert injected_too.text_completion_system == "<<frame Be brief.\n\nASK HOW>>"

    def test_the_framing_stands_without_a_premise(self) -> None:
        # a model turn needs a turn before it, and a new story's premise is empty
        assert request(said("user", "Hi.")).text_completion_system == "<<frame >>"


class TestTheChatWire:
    """`as_chat`: the system message first, then each run of one role
    merged into one turn by its pieces' seams, pictures and all; a tool's
    result as the reader's; volatile from the first injected piece on."""

    def test_the_system_message_is_the_first_turn(self) -> None:
        prompt = request(premise("Be brief."), injected("ASK HOW", AT_SYSTEM), said("user", "Hi."))
        assert roles_and_bodies(prompt) == [("system", "Be brief.\n\nASK HOW"), ("user", "Hi.")]

    def test_without_a_system_message_there_is_no_system_turn(self) -> None:
        assert roles_and_bodies(request(said("user", "Hi."))) == [("user", "Hi.")]

    def test_roles_alternate_as_the_pieces_hold_them(self) -> None:
        prompt = request(said("assistant", "It begins."), said("user", "I nod."))
        assert roles_and_bodies(prompt) == [("assistant", "It begins."), ("user", "I nod.")]

    def test_a_run_of_one_role_is_one_turn_joined_by_each_pieces_seam(self) -> None:
        prompt = request(
            said("user", "I was waiting"),
            said("user", "for the bus.", JoinType.INLINE),
            said("user", "It rained."),
            said("assistant", "And?"),
        )
        assert roles_and_bodies(prompt) == [
            ("user", "I was waiting for the bus.\n\nIt rained."),
            ("assistant", "And?"),
        ]

    def test_a_turn_joins_by_its_first_pieces_seam(self) -> None:
        prompt = request(
            said("user", "I was waiting"),
            said("assistant", "for the bus.", JoinType.INLINE),
            said("assistant", "It rained."),
        )
        assert [turn.join_type for turn in prompt.as_chat()] == [
            JoinType.PARAGRAPH,
            JoinType.INLINE,
        ]

    def test_a_turn_opens_on_its_first_pieces_words(self) -> None:
        prompt = request(said("user", "One."), said("assistant", "\n\n Two."))
        assert prompt.as_chat()[1].body == "Two."

    def test_a_turn_opened_by_a_picture_alone_starts_at_the_next_words(self) -> None:
        prompt = request(
            said("user", "", images=(CAT,)),
            said("user", "Look.", JoinType.INLINE),
            said("user", "And this.", images=(DOG,)),
        )
        assert prompt.as_chat() == [
            WireTurn("user", "Look.\n\nAnd this.", (CAT, DOG)),
        ]

    def test_pictures_ride_their_turn_in_order(self) -> None:
        prompt = request(
            said("user", "Look.", images=(CAT,)),
            said("user", "And this.", images=(DOG,)),
            said("assistant", "Two animals."),
        )
        assert [turn.images for turn in prompt.as_chat()] == [(CAT, DOG), ()]

    def test_a_tools_result_goes_as_the_readers_turn(self) -> None:
        prompt = request(
            said("user", "Look it up."),
            said("assistant", "```otk-search\ntides\n```"),
            said("tool", "<<search: high at six>>"),
            said("assistant", "It is six."),
        )
        assert [turn.role for turn in prompt.as_chat()] == [
            "user",
            "assistant",
            "user",
            "assistant",
        ]

    def test_a_tools_result_merges_with_the_readers_line_beside_it(self) -> None:
        prompt = request(said("tool", "<<search: high at six>>"), said("user", "So?"))
        assert roles_and_bodies(prompt) == [("user", "<<search: high at six>>\n\nSo?")]

    def test_the_turn_an_injection_lands_in_and_every_turn_after_are_volatile(self) -> None:
        prompt = request(
            premise("Be brief."),
            said("user", "One."),
            said("assistant", "Two."),
            injected("Remember.", NEWEST),
            said("user", "Three."),
        )
        assert [turn.volatile for turn in prompt.as_chat()] == [False, False, False, True]
        deeper = request(
            said("user", "One."),
            said("assistant", "Two."),
            injected("Remember.", InjectionPosition(2)),
            said("user", "Three."),
            said("assistant", "Four."),
            said("user", "Five."),
        )
        assert [turn.volatile for turn in deeper.as_chat()] == [False, False, True, True, True]

    def test_a_system_injection_makes_nothing_volatile(self) -> None:
        prompt = request(injected("ASK HOW", AT_SYSTEM), said("user", "One."))
        assert not any(turn.volatile for turn in prompt.as_chat())


class TestTheFigures:
    """What the request tells of itself, read off its pieces: the history,
    the scene summaries, the pictures sent, the injections and the cards —
    each in the order sent."""

    def test_a_history_piece_is_the_history(self) -> None:
        assert request(recap_piece(Provenance.HISTORY, "So far."), said("user", "Hi.")).history
        assert not request(recap_piece(Provenance.SCENE, "A scene."), said("user", "Hi.")).history

    def test_the_scenes_summarized_are_the_scene_pieces(self) -> None:
        prompt = request(
            recap_piece(Provenance.HEADER, "[So far:]"),
            recap_piece(Provenance.HISTORY, "So far."),
            recap_piece(Provenance.SCENE, "One."),
            recap_piece(Provenance.SCENE, "Two."),
            said("user", "Hi."),
        )
        assert prompt.scenes_summarized == 2

    def test_the_pictures_sent_are_every_pieces(self) -> None:
        prompt = request(
            card_piece("Elara", "Elara.", images=(CAT,)),
            said("user", "Look.", images=(CAT, DOG)),
        )
        assert prompt.pictures_sent == 3

    def test_each_injection_is_listed_in_the_order_sent_with_its_cost(self) -> None:
        prompt = request(
            injected("x" * 40, AT_SYSTEM, owner="ask"),
            said("user", "One."),
            injected("y" * 80, NEWEST, owner="reminder"),
            said("user", "Two."),
        )
        assert prompt.injections == (
            InjectionSent("ask", AT_SYSTEM, 10),
            InjectionSent("reminder", NEWEST, 20),
        )

    def test_each_card_is_listed_in_the_order_sent_its_portrait_counted(self) -> None:
        prompt = request(
            card_piece("Elara", "x" * 40, images=(CAT,)),
            said("user", "Hi."),
            card_piece("Wren", "y" * 80),
        )
        assert prompt.cards == (
            CardSent("Elara", 10 + IMAGE_TOKENS_ESTIMATE),
            CardSent("Wren", 20),
        )

    def test_the_cards_and_the_injections_cost_together_what_each_costs(self) -> None:
        prompt = request(
            card_piece("Elara", "x" * 40, images=(CAT,)),
            injected("y" * 40, AT_SYSTEM),
            said("user", "Hi."),
            card_piece("Wren", "y" * 80),
            injected("z" * 80, NEWEST),
            said("user", "On."),
        )
        assert prompt.card_tokens == sum(card.tokens for card in prompt.cards)
        assert prompt.injection_tokens == sum(sent.tokens for sent in prompt.injections) == 30

    def test_the_messages_kept_are_the_head_and_the_tail(self) -> None:
        assert request(said("user", "Hi."), head=20, tail=156).kept == 176

    def test_a_request_without_any_lists_none(self) -> None:
        prompt = request(said("user", "Hi."))
        assert (prompt.injections, prompt.cards, prompt.pictures_sent) == ((), (), 0)
        assert (prompt.card_tokens, prompt.injection_tokens) == (0, 0)


class TestTheStoryAsOneText:
    """`as_story_text`: the chat wire's turns in order, the system's
    aside, each seam its turn's; the last turn's trailing break kept."""

    def test_the_turns_read_as_one_text_with_their_seams(self) -> None:
        prompt = request(
            premise("Be brief."),
            said("user", "I was waiting"),
            said("assistant", "for the bus.", JoinType.INLINE),
            said("user", "Then?"),
            said("assistant", "Nothing came."),
            said("user", "said no one"),
            said("assistant", "and left.", JoinType.INLINE),
        )
        assert prompt.as_story_text() == (
            "I was waiting for the bus.\n\nThen?\n\nNothing came.\n\nsaid no one and left."
        )

    def test_a_paragraphs_blank_line_lead_is_not_drawn_twice(self) -> None:
        prompt = request(
            said("user", "One."),
            said("assistant", "\n\nTwo."),
            said("assistant", "\n\n\nThree."),
        )
        assert prompt.as_story_text() == "One.\n\nTwo.\n\nThree."

    def test_the_last_turns_trailing_break_is_kept(self) -> None:
        # a paragraph the reader opened for the model to write in
        prompt = request(said("assistant", "It rained."), said("user", "Then?\n\n"))
        assert prompt.as_story_text() == "It rained.\n\nThen?\n\n"

    def test_as_text_renders_the_framed_system_and_the_story_in_the_template(self) -> None:
        prompt = request(
            premise("Be brief."),
            said("user", "I was waiting"),
            said("assistant", "for the bus.", JoinType.INLINE),
        )
        assert prompt.as_text(PLAIN) == "<<frame Be brief.>>\n\nI was waiting for the bus."
        assert prompt.as_text(TEXT_TEMPLATES["gemma4"], "\n\n") == (
            "<bos><|turn>system\n<<frame Be brief.>><turn|>\n"
            "<|turn>model\nI was waiting for the bus.\n\n"
        )


class TestTheTextWireInPieces:
    """`text_pieces`: the text wire's request cut into pieces for a
    preview — the system message's and the story's, each where it comes
    from, the template's markers and the framing the system's own — that
    join back into exactly what is sent."""

    def test_joined_they_are_exactly_what_is_sent(self) -> None:
        prompt = everything()
        for template in TEXT_TEMPLATES.values():
            joined = "".join(piece.text for piece in prompt.text_pieces(template))
            assert joined == prompt.as_text(template), template.name

    def test_the_story_pieces_keep_where_they_come_from_in_order(self) -> None:
        prompt = everything()
        sent = [p for p in prompt.pieces if p.role != "system" and p.text.strip()]
        cut = [p for p in prompt.text_pieces(TEXT_TEMPLATES["gemma4"]) if p.role != "system"]
        assert [p.metadata for p in cut] == [p.metadata for p in sent]
        assert [p.text.strip() for p in cut] == [p.text.strip() for p in sent]

    def test_a_system_injection_keeps_its_owner(self) -> None:
        cut = everything().text_pieces(TEXT_TEMPLATES["gemma4"])
        (ask,) = [
            p for p in cut if p.metadata.provenance is Provenance.INJECTION and p.role == "system"
        ]
        assert ask.metadata.injection_owner == "ask"
        assert ask.text.strip() == "ASK HOW"

    def test_the_markers_and_the_framing_are_the_systems(self) -> None:
        cut = everything().text_pieces(TEXT_TEMPLATES["gemma4"])
        assert cut[0].metadata.provenance is Provenance.SYSTEM
        assert cut[0].text.startswith("<bos><|turn>system\n<<frame ")
        assert "<|turn>model\n" in "".join(
            p.text for p in cut if p.metadata.provenance is Provenance.SYSTEM
        )

    def test_the_systems_own_text_is_one_piece_between_two_others(self) -> None:
        cut = everything().text_pieces(TEXT_TEMPLATES["gemma4"])
        provenances = [p.metadata.provenance for p in cut]
        assert (Provenance.SYSTEM, Provenance.SYSTEM) not in pairwise(provenances)

    def test_no_piece_is_empty(self) -> None:
        for template in TEXT_TEMPLATES.values():
            assert all(piece.text for piece in everything().text_pieces(template)), template.name

    def test_the_pieces_carry_no_pictures(self) -> None:
        cut = everything().text_pieces(TEXT_TEMPLATES["gemma4"])
        assert all(piece.images == () for piece in cut)

    def test_a_system_message_the_framing_leaves_out_leaves_the_head_the_systems(self) -> None:
        # a framing without `{premise}` sends no system message: nothing
        # in the head is the premise's or an injection's
        prompt = request(
            premise("Be brief."),
            injected("ASK HOW", AT_SYSTEM),
            said("user", "Hi."),
            story_framing="Continue the story.",
        )
        template = TEXT_TEMPLATES["chatml"]
        cut = prompt.text_pieces(template)
        assert "".join(p.text for p in cut) == prompt.as_text(template)
        assert [p.metadata.provenance for p in cut] == [Provenance.SYSTEM, Provenance.MESSAGE]


# ---------- helpers ----------


def request(
    *pieces: PromptPiece,
    story_framing: str = FRAMING,
    transcript_tokens: int = 0,
    head: int = 0,
    tail: int = 0,
) -> AssembledPrompt:
    """A request holding `pieces` as the assembler would hand them over;
    the figures the assembly decides, but those asked for, are beside
    the point here."""
    return AssembledPrompt(
        pieces=pieces,
        story_framing=story_framing,
        limit=8192,
        transcript_tokens=transcript_tokens,
        head=head,
        middle=0,
        tail=tail,
        tail_target=150,
        scenes_rolled_up=0,
    )


def everything() -> AssembledPrompt:
    """A request with a piece of every kind: the premise and a system
    injection, a card with its portrait, the recap, an inline seam, a
    tool's result, an injection at a depth, and the reader's trailing
    break."""
    return request(
        premise("Be brief."),
        injected("ASK HOW", AT_SYSTEM, owner="ask"),
        card_piece("Elara", "Elara, a smuggler.", images=(CAT,)),
        recap_piece(Provenance.HEADER, "[So far:]"),
        recap_piece(Provenance.HISTORY, "The heist went wrong."),
        recap_piece(Provenance.SCENE, "They hid.", scene_num=3, scene_title="The weir"),
        said("user", "I was waiting"),
        said("assistant", "for the bus.", JoinType.INLINE),
        said("tool", "<<search: high at six>>"),
        said("assistant", "It is six."),
        injected("Remember.", NEWEST, owner="reminder"),
        said("user", "Then?\n\n"),
    )


def roles_and_bodies(prompt: AssembledPrompt) -> list[tuple[str, str]]:
    return [(turn.role, turn.body) for turn in prompt.as_chat()]


def premise(text: str) -> PromptPiece:
    return PromptPiece(role="system", text=text, metadata=PromptPieceMetadata(Provenance.SYSTEM))


def said(
    role: str,
    text: str,
    join_type: JoinType = JoinType.PARAGRAPH,
    *,
    images: tuple[WirePicture, ...] = (),
) -> PromptPiece:
    """A stored row's piece."""
    metadata = PromptPieceMetadata(Provenance.MESSAGE)
    return PromptPiece(role=role, text=text, images=images, join_type=join_type, metadata=metadata)


def card_piece(name: str, text: str, *, images: tuple[WirePicture, ...] = ()) -> PromptPiece:
    metadata = PromptPieceMetadata(Provenance.CARD, card_name=name)
    return PromptPiece(role="user", text=text, images=images, metadata=metadata)


def recap_piece(
    provenance: Provenance, text: str, *, scene_num: int | None = None, scene_title: str = ""
) -> PromptPiece:
    metadata = PromptPieceMetadata(provenance, scene_num=scene_num, scene_title=scene_title)
    return PromptPiece(role="user", text=text, metadata=metadata)


def injected(text: str, position: InjectionPosition, *, owner: str = "reminder") -> PromptPiece:
    """An injection's piece: in the system message at the system
    position, a user row of its own at a depth."""
    metadata = PromptPieceMetadata(
        Provenance.INJECTION, injection_owner=owner, injection_position=position
    )
    role = "system" if position.depth is None else "user"
    return PromptPiece(role=role, text=text, metadata=metadata)
