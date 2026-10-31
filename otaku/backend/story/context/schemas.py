"""What `story.context.resources` answers: its schemas, made by one
builder (`as_context_report`) from the preview the service gathered —
its facts copied, the parts set apart where the service's rule says
(`separate`) — and the report's words: its summary, its note, and the
text a terminal pages."""

from __future__ import annotations

from collections.abc import Mapping
from itertools import pairwise
from typing import assert_never

from otaku.backend.base import Report, Schema
from otaku.backend.story.context import services as domain_context
from otaku.context.prompt import (
    IMAGE_TOKENS_ESTIMATE,
    PromptPiece,
    PromptPieceMetadata,
    Provenance,
)
from otaku.formatting import format_context
from otaku.store.schema import InjectionPosition


class ContextReport(Report):
    """The next request EXACTLY as it will be sent: its shape in numbers
    (`figures`), the cards and the injections it carries, in the
    request's order, and its text in parts, each with where it comes
    from, a separator between two that stand apart — over the text wire,
    the pieces of the one text it sends, rendered in `template` ("" over
    the chat wire) — with what it is made of in words (`summary`), and
    what the preview could not know (`note`, "" when it knew
    everything).

    Nothing in it is otaku's own text except the summary, the note and
    the markers `text` brackets (each part's provenance) — every other
    line is content the model receives, in order."""

    figures: ContextFigures
    cards: tuple[ContextCard, ...]
    injections: tuple[ContextInjection, ...]
    parts: tuple[ContextPart | ContextSeparator, ...]
    summary: str
    note: str
    template: str

    def text(self, *, dim: str = "", reset: str = "", rule: str = "") -> str:
        """The whole preview as a terminal pages it: the summary, then
        each part after a blank line, under a marker naming where it comes
        from, its own blank lines dropped; `rule` under the summary and
        where a separator stands. `dim`/`reset` bracket the markers, so a
        terminal can fade them, and `rule` is the terminal's own line (""
        leaves a blank one)."""
        out = [self.summary] + ([self.note] if self.note else []) + ["", rule]
        for part in self.parts:
            if isinstance(part, ContextSeparator):
                out.extend(["", rule])
            else:
                lines = [line for line in part.body.splitlines() if line.strip()]
                out.extend(["", f"{dim}[{part.caption}]{reset}", *lines])
        return "\n".join(out)


class ContextFigures(Schema):
    """The request's shape in numbers, the diagram's: the messages sent
    verbatim — the head, the tail, both together (`kept`) — and those
    the summary stands in for (`middle`), as scene summaries and as the
    story so far over the scenes it rolls up; the tail aimed at and the
    one configured; the tokens of the system message, of the transcript
    and of the whole request — the one text's over the text wire —
    against the limit, and the percent used; what the cards and the
    injections cost together; the pictures sent, held back, and not
    sent."""

    head: int
    middle: int
    tail: int
    kept: int
    tail_target: int
    tail_setting: int
    scenes_summarized: int
    scenes_rolled_up: int
    history_sent: bool
    system_tokens: int
    transcript_tokens: int
    total_tokens: int
    limit: int
    used: int  # percent of the limit, over 100 where it cannot fit
    card_tokens: int
    injection_tokens: int
    pictures_sent: int
    pictures_held: int
    pictures_omitted: int


class ContextCard(Schema):
    """One character card the request carries: the name it came in under
    and what it costs, its portrait counted."""

    name: str
    tokens: int


class ContextPart(Schema):
    """One piece of the request's text, as the wire will carry it: the
    role it goes in, its text, how many pictures ride it, and where it
    comes from (`provenance`) — a scene's summary its scene's number and
    title, an injection its name and place (and the place as the depth
    ruler names it), a card the character it came in under; "" and None
    where they do not apply — and the caption both frontends head it
    with."""

    role: str
    body: str
    pictures: int
    provenance: Provenance
    scene_num: int | None = None
    scene_title: str = ""
    injection_label: str = ""
    injection_position: InjectionPosition | None = None
    injection_position_text: str = ""
    card_name: str = ""
    caption: str = ""


class ContextSeparator(Schema):
    """Where the preview sets one part apart from the next (`separate`)."""


class ContextInjection(Schema):
    """One text the request carries besides the story: its name — the
    setting's `injection_label`, what the depth ruler places — where it
    rides (the `position`, and its `position_text` as the ruler names
    it) and what it costs."""

    label: str
    position: InjectionPosition
    position_text: str
    tokens: int


class ContextFill(Report):
    """How full the next request is, as `/context` measures it: its
    `percent` of the prompt's limit — over 100 where the story cannot fit
    even fully summarized — and `context_in_force`, what the request is
    cut to."""

    percent: int
    context_in_force: int

    def text(self) -> str:
        return f"{self.percent}% of {format_context(self.context_in_force)}"


# ---------- the context report, built ----------


def as_context_report(preview: domain_context.Preview) -> ContextReport:
    """The report a preview makes: its figures, its cards and injections
    by name, each piece of the request a part, and its words."""
    prompt = preview.prompt
    figures = ContextFigures(
        head=prompt.head,
        middle=prompt.middle,
        tail=prompt.tail,
        kept=prompt.kept,
        tail_target=prompt.tail_target,
        tail_setting=preview.tail_setting,
        scenes_summarized=prompt.scenes_summarized,
        scenes_rolled_up=prompt.scenes_rolled_up,
        history_sent=prompt.history,
        system_tokens=prompt.system_tokens,
        transcript_tokens=prompt.transcript_tokens,
        total_tokens=preview.total_tokens,
        limit=prompt.limit,
        used=preview.used,
        card_tokens=prompt.card_tokens,
        injection_tokens=prompt.injection_tokens,
        pictures_sent=prompt.pictures_sent,
        pictures_held=prompt.pictures_held,
        pictures_omitted=prompt.pictures_omitted,
    )
    cards = tuple(ContextCard(name=card.name, tokens=card.tokens) for card in prompt.cards)
    injections = tuple(
        ContextInjection(
            label=preview.labels[sent.owner],
            position=sent.position,
            position_text=sent.position.text,
            tokens=sent.tokens,
        )
        for sent in prompt.injections
    )
    return ContextReport(
        figures=figures,
        cards=cards,
        injections=injections,
        parts=_laid([_part_of(piece, preview.labels) for piece in preview.pieces]),
        summary=_summary(figures, cards, injections, preview.template),
        note=_note(preview),
        template=preview.template,
    )


def _laid(parts: list[ContextPart]) -> tuple[ContextPart | ContextSeparator, ...]:
    """The parts in order, a separator between two that stand apart."""
    laid: list[ContextPart | ContextSeparator] = [*parts[:1]]
    for previous, part in pairwise(parts):
        if domain_context.separate(previous.provenance, part.provenance):
            laid.append(ContextSeparator())
        laid.append(part)
    return tuple(laid)


def _part_of(piece: PromptPiece, labels: Mapping[str, str]) -> ContextPart:
    """One piece of the request as the report's part, an injection by
    its name."""
    meta = piece.metadata
    position = meta.injection_position
    label = labels.get(meta.injection_owner, "")
    place = position.text if position is not None else ""
    pictures = len(piece.images)
    return ContextPart(
        role=piece.role,
        body=piece.text,
        pictures=pictures,
        provenance=meta.provenance,
        scene_num=meta.scene_num,
        scene_title=meta.scene_title,
        injection_label=label,
        injection_position=position,
        injection_position_text=place,
        card_name=meta.card_name,
        caption=_caption(meta, role=piece.role, pictures=pictures, label=label, place=place),
    )


# ---------- the context report's words ----------


def _caption(meta: PromptPieceMetadata, *, role: str, pictures: int, label: str, place: str) -> str:
    """What both frontends head a part with: where it comes from — a
    message by its role, the pictures riding it counted — and, for a
    card, a summary's scene or an injection, which."""
    plural = "s" if pictures != 1 else ""
    riding = f" · {pictures} picture{plural} attached" if pictures else ""
    match meta.provenance:
        case Provenance.SYSTEM:
            return "system"
        case Provenance.MESSAGE:
            return f"{role}{riding}"
        case Provenance.CARD:
            return f"card · {meta.card_name}{riding}" if meta.card_name else f"card{riding}"
        case Provenance.PINNED_MESSAGE:
            return f"pinned · {role}{riding}"
        case Provenance.PINNED_HEADER:
            return "pinned · header"
        case Provenance.HEADER:
            return "summary · header"
        case Provenance.HISTORY:
            return "summary · history"
        case Provenance.SCENE:
            title = f" · {meta.scene_title}" if meta.scene_title else ""
            return f"summary · scene {meta.scene_num}{title}"
        case Provenance.INJECTION if meta.injection_position == InjectionPosition():
            # a part of the system message, named so first
            return f"{place} · injection · {label}"
        case Provenance.INJECTION:
            return f"injection · {label} · {place}"
        case _:
            assert_never(meta.provenance)


def _note(preview: domain_context.Preview) -> str:
    """What the preview could not know, in words; "" when it knew
    everything."""
    if not preview.cold:
        return ""
    return (
        f"The model is not loaded: the window is {format_context(preview.prompt.limit)} of the "
        "assembler's own until the next turn loads it."
    )


def _summary(
    figures: ContextFigures,
    cards: tuple[ContextCard, ...],
    injections: tuple[ContextInjection, ...],
    template: str,
) -> str:
    """What the request is made of, in words — the same figures the
    diagram is drawn from, said in the report's own sentences."""
    lines = ["Context preview — the exact request to be sent. Context summary:", ""]
    lines.append(
        f"  ~{figures.total_tokens:,} tokens projected · "
        f"{figures.used}% of the {figures.limit:,} limit"
    )
    if template:
        lines.append(f"  text wire: one text, in the {template} template")
    if figures.system_tokens:
        lines.append(
            f"  system {figures.system_tokens:,} · transcript {figures.transcript_tokens:,}"
        )
    # The summaries are not a third slice of the transcript: they STAND IN
    # for the messages between head and tail. Naming that count is what
    # makes the line add up to the story's length instead of to nothing.
    if figures.history_sent:
        # Case 4: old summaries folded into the story so far. Displaced
        # summaries are named, not folded in: the story so far covers
        # the replaced scenes, and the line says so or the count would
        # claim the kept summaries cover the whole middle.
        plural = "s" if figures.scenes_rolled_up != 1 else ""
        lines.append(
            f"  {figures.head} head + {figures.tail} tail verbatim, plus {figures.middle} middle "
            f"inserted in between as the story so far ({figures.scenes_rolled_up} "
            f"scene{plural}) and {figures.scenes_summarized} scene summaries"
        )
    elif figures.scenes_summarized:
        # Case 3: the covered middle rides as its scene summaries.
        lines.append(
            f"  {figures.head} head + {figures.tail} tail verbatim, plus {figures.middle} middle "
            f"inserted in between as {figures.scenes_summarized} scene summaries"
        )
    else:
        # Cases 1-2: a short story, or nothing covering the middle —
        # everything verbatim.
        lines.append(f"  {figures.kept} messages verbatim")
    if figures.pictures_sent:
        plural = "s" if figures.pictures_sent != 1 else ""
        lines.append(
            f"  {figures.pictures_sent} picture{plural} riding the verbatim messages, counted at "
            f"~{IMAGE_TOKENS_ESTIMATE:,} tokens each"
        )
    if figures.pictures_held:
        plural = "s" if figures.pictures_held != 1 else ""
        lines.append(
            f"  {figures.pictures_held} picture{plural} on earlier messages held back: this engine "
            "puts every picture in a request on the latest message, so only the latest turn's ride"
        )
    if figures.pictures_omitted:
        plural = "s" if figures.pictures_omitted != 1 else ""
        lines.append(
            f"  {figures.pictures_omitted} picture{plural} on the verbatim messages not sent: "
            "the model cannot see, or the file is gone"
        )
    if cards:
        # The character cards the request carries, wherever each rides —
        # a bullet each, its portrait counted; the total where there is
        # something to total. Their texts are in the parts.
        summed = f" (~{figures.card_tokens:,} total)" if len(cards) > 1 else ""
        lines.append(f"  cards{summed}:")
        lines.extend(f"  - {card.name} (~{card.tokens:,} tokens)" for card in cards)
    if injections:
        # What rides besides the story, each where the story put it — a
        # bullet each, named as the setting names what it injects; the
        # total where there is something to total. The texts themselves
        # are in the parts, being the wire.
        summed = f" (~{figures.injection_tokens:,} total)" if len(injections) > 1 else ""
        lines.append(f"  injected{summed}:")
        lines.extend(f"  - {i.label} ({i.position_text}, ~{i.tokens:,} tokens)" for i in injections)
    if figures.tail_target < figures.tail_setting:
        # Case 5: the tail stepped down so the context could fit (a
        # case-6 refusal never reaches this report — the preview refuses
        # with the same sentence the turn would).
        lines.append(
            f"  the tail aims at {figures.tail_target} messages instead of the configured "
            f"{figures.tail_setting}, so the context fits the limit"
        )
    return "\n".join(lines)
