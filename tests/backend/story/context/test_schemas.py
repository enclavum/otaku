"""The context report, built from a preview (`as_context_report`): its
figures copied from the prompt and the preview, each piece a part in
order with its facts and its caption, a separator wherever the service's
rule sets two parts apart, the injections by the names the preview gives
them and the cards in the order sent, the note said only where the
window is a stand-in; and `text`, the report as a terminal pages it —
each part under its caption, a part's own blank lines dropped, the
terminal's rule under the summary and at each separator."""

from collections.abc import Mapping
from itertools import pairwise

from otaku.backend.story.context.schemas import ContextPart, ContextSeparator, as_context_report
from otaku.backend.story.context.services import Preview, separate
from otaku.context.prompt import (
    AssembledPrompt,
    PromptPiece,
    PromptPieceMetadata,
    Provenance,
    WirePicture,
)
from otaku.store.schema import InjectionPosition

CAT = WirePicture(b"cat", "image/jpeg")
AT_SYSTEM = InjectionPosition()
DEEP = InjectionPosition(3)


class TestFigures:
    def test_the_figures_are_the_prompts_and_the_previews(self) -> None:
        pieces = (
            piece(Provenance.CARD, "Elara.", images=(CAT,), card_name="Elara"),
            piece(Provenance.INJECTION, "Remember.", injection_owner="r", injection_position=DEEP),
            piece(Provenance.MESSAGE, "Hi.", images=(CAT,)),
        )
        found = preview(
            *pieces, head=3, middle=40, tail=7, total_tokens=321, used=12, labels={"r": "reminder"}
        )
        figures = as_context_report(found).figures
        prompt = found.prompt
        assert (figures.head, figures.middle, figures.tail, figures.kept) == (3, 40, 7, 10)
        assert (figures.total_tokens, figures.used, figures.limit) == (321, 12, prompt.limit)
        assert figures.tail_setting == found.tail_setting
        assert (figures.card_tokens, figures.injection_tokens) == (
            prompt.card_tokens,
            prompt.injection_tokens,
        )
        assert figures.pictures_sent == 2
        assert (figures.system_tokens, figures.transcript_tokens) == (
            prompt.system_tokens,
            prompt.transcript_tokens,
        )


class TestParts:
    def test_each_piece_is_a_part_in_order_its_facts_copied(self) -> None:
        pieces = (
            piece(Provenance.SYSTEM, "Be brief.", role="system"),
            piece(Provenance.CARD, "Elara.", images=(CAT,), card_name="Elara"),
            piece(Provenance.SCENE, "They hid.", scene_num=3, scene_title="The Weir"),
            piece(Provenance.INJECTION, "Remember.", injection_owner="r", injection_position=DEEP),
            piece(Provenance.MESSAGE, "Hi.", role="assistant"),
        )
        report = as_context_report(preview(*pieces, labels={"r": "story reminder"}))
        parts = [part for part in report.parts if isinstance(part, ContextPart)]
        assert [(p.provenance, p.role, p.body, p.pictures) for p in parts] == [
            (Provenance.SYSTEM, "system", "Be brief.", 0),
            (Provenance.CARD, "user", "Elara.", 1),
            (Provenance.SCENE, "user", "They hid.", 0),
            (Provenance.INJECTION, "user", "Remember.", 0),
            (Provenance.MESSAGE, "assistant", "Hi.", 0),
        ]
        card, scene, injection = parts[1], parts[2], parts[3]
        assert card.card_name == "Elara"
        assert (scene.scene_num, scene.scene_title) == (3, "The Weir")
        assert (
            injection.injection_label,
            injection.injection_position,
            injection.injection_position_text,
        ) == ("story reminder", DEEP, DEEP.text)

    def test_a_separator_stands_exactly_where_the_rule_sets_two_parts_apart(self) -> None:
        kinds = (
            Provenance.SYSTEM,
            Provenance.INJECTION,
            Provenance.CARD,
            Provenance.HEADER,
            Provenance.HISTORY,
            Provenance.SCENE,
            Provenance.PINNED_HEADER,
            Provenance.PINNED_MESSAGE,
            Provenance.PINNED_MESSAGE,
            Provenance.SCENE,
            Provenance.MESSAGE,
            Provenance.MESSAGE,
        )
        report = as_context_report(preview(*(piece(kind, "x") for kind in kinds)))
        laid = list(report.parts)
        assert [p.provenance for p in laid if isinstance(p, ContextPart)] == list(kinds)
        for before, after in pairwise(laid):
            assert not (
                isinstance(before, ContextSeparator) and isinstance(after, ContextSeparator)
            )
        separated = [
            isinstance(after, ContextSeparator)
            for before, after in pairwise(laid)
            if isinstance(before, ContextPart)
        ]
        assert separated == [separate(a, b) for a, b in pairwise(kinds)]

    def test_no_pieces_no_parts(self) -> None:
        assert as_context_report(preview()).parts == ()

    def test_every_part_has_a_caption(self) -> None:
        report = as_context_report(preview(*(piece(kind, "x") for kind in Provenance)))
        assert all(part.caption for part in report.parts if isinstance(part, ContextPart))


class TestInjectionsAndCards:
    def test_each_injection_is_named_by_the_preview_in_the_order_sent(self) -> None:
        pieces = (
            piece(
                Provenance.INJECTION,
                "Ask.",
                role="system",
                injection_owner="a",
                injection_position=AT_SYSTEM,
            ),
            piece(Provenance.MESSAGE, "Hi."),
            piece(Provenance.INJECTION, "Rain.", injection_owner="b", injection_position=DEEP),
        )
        found = preview(*pieces, labels={"a": "questions", "b": "story reminder"})
        injections = as_context_report(found).injections
        assert [(i.label, i.position, i.position_text) for i in injections] == [
            ("questions", AT_SYSTEM, AT_SYSTEM.text),
            ("story reminder", DEEP, DEEP.text),
        ]
        assert [i.tokens for i in injections] == [sent.tokens for sent in found.prompt.injections]

    def test_the_cards_in_the_order_sent(self) -> None:
        pieces = (
            piece(Provenance.CARD, "Elara.", card_name="Elara"),
            piece(Provenance.MESSAGE, "Hi."),
            piece(Provenance.CARD, "Wren.", card_name="Wren"),
        )
        found = preview(*pieces)
        cards = as_context_report(found).cards
        assert [(c.name, c.tokens) for c in cards] == [
            (sent.name, sent.tokens) for sent in found.prompt.cards
        ]


class TestWords:
    def test_the_note_is_said_only_where_the_window_is_a_stand_in(self) -> None:
        assert as_context_report(preview(piece(Provenance.MESSAGE, "Hi."))).note == ""
        assert as_context_report(preview(piece(Provenance.MESSAGE, "Hi."), cold=True)).note

    def test_the_template_is_the_previews(self) -> None:
        assert as_context_report(preview(template="plain")).template == "plain"


class TestText:
    def test_each_part_is_headed_by_the_caption_the_page_shows_too(self) -> None:
        report = as_context_report(preview(*(piece(kind, "x") for kind in Provenance)))
        text = report.text()
        for part in report.parts:
            if isinstance(part, ContextPart):
                assert f"[{part.caption}]" in text, part.caption

    def test_the_rule_stands_under_the_summary_and_at_each_separator(self) -> None:
        report = as_context_report(preview(*(piece(kind, "x") for kind in Provenance)))
        separators = sum(isinstance(part, ContextSeparator) for part in report.parts)
        assert report.text(rule="~rule~").count("~rule~") == 1 + separators

    def test_a_parts_own_blank_lines_are_dropped(self) -> None:
        report = as_context_report(preview(piece(Provenance.MESSAGE, "One.\n\n\nTwo.")))
        assert "One.\nTwo." in report.text()


# ---------- helpers ----------


def piece(
    provenance: Provenance,
    text: str,
    *,
    role: str = "user",
    images: tuple[WirePicture, ...] = (),
    **metadata: object,
) -> PromptPiece:
    return PromptPiece(
        role=role,
        text=text,
        images=images,
        metadata=PromptPieceMetadata(provenance, **metadata),  # type: ignore[arg-type]
    )


def preview(
    *pieces: PromptPiece,
    head: int = 0,
    middle: int = 0,
    tail: int = 0,
    total_tokens: int = 100,
    used: int = 1,
    labels: Mapping[str, str] | None = None,
    template: str = "",
    cold: bool = False,
) -> Preview:
    """A preview over `pieces` as the service would hand it to the builder."""
    prompt = AssembledPrompt(
        pieces=pieces,
        story_framing="",
        limit=8192,
        transcript_tokens=90,
        head=head,
        middle=middle,
        tail=tail,
        tail_target=150,
        scenes_rolled_up=0,
    )
    return Preview(
        prompt=prompt,
        pieces=pieces,
        template=template,
        total_tokens=total_tokens,
        used=used,
        labels=labels or {},
        tail_setting=150,
        cold=cold,
    )
