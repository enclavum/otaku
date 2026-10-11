"""A story's next request as a frontend reaches it: the request
previewed (`ContextResource`) and how full it is (`ContextFillResource`).
What a resource answers is the report — the facts it is made of, and
`text()`, the same facts as a terminal pages them — as every report is
(`api.app.reports`). The facts are gathered by the domain
(`domain.story.context`)."""

from __future__ import annotations

from dataclasses import dataclass

from otaku.backend.domain.app import App
from otaku.backend.domain.story import context as domain_context
from otaku.backend.errors import NotFound
from otaku.context.assembler import IMAGE_TOKENS, AssembledPrompt
from otaku.formatting import format_context
from otaku.store.schema import InjectionPosition


class ContextResource:
    """/stories/{story}/context — a story's next request, previewed."""

    def __init__(self, app: App) -> None:
        self._app = app
        self.fill = ContextFillResource(app)

    def get(self, story_id: int) -> ContextReport:
        """Preview a story's next request: what it is made of, said in
        words, and one part per message it will carry. No model: the
        preview still stands, over the assembler's default window — what
        WOULD be sent is a question that needs no server. A model not
        loaded is not loaded for a preview, so its window is the
        substitute too, and the report says so. Raises NotFound for a
        story that is not there, Conflict for one that cannot fit."""
        found = domain_context.get_context(self._app, story_id)
        return ContextReport(
            prompt=found.prompt,
            tail_setting=found.tail_setting,
            parts=tuple(ContextPart(part.role, part.body, part.pictures) for part in found.parts),
            injections=tuple(
                ContextInjection(each.label, each.position, each.position.text, each.tokens)
                for each in found.injections
            ),
            note=found.note,
            wire=found.wire,
            template=found.template,
        )


class ContextFillResource:
    """/stories/{story}/context/fill — how full a story's next request
    is."""

    def __init__(self, app: App) -> None:
        self._app = app

    def get(self, story_id: int) -> ContextFill:
        """How full a story's next request is — the context report's own
        figure, read after every turn. Never waits on the internet.
        Raises NotFound while the window is not known (no model, a model
        not loaded, a catalog that has not answered) — a figure against
        the assembler's stand-in would mislead — and for a story that is
        not there."""
        found = domain_context.get_context_fill(self._app, story_id)
        if found is None:
            raise NotFound("The model's window is not known yet.")
        return ContextFill(*found)


@dataclass(frozen=True)
class ContextReport:
    """The next request EXACTLY as it will be sent: the assembled prompt
    — the facts the diagram and the summary are drawn from — the
    injections it carries (in the request's order: the system message
    first, then the deepest first, the costlier first at one place), and
    one part per message — or, over the text wire, ONE part, the text as the
    text wire sends it (`wire`), rendered in the model's template.

    Nothing in it is otaku's own text except the summary and the role
    markers `text` brackets (they stand for the JSON role field — `prompt`
    for the text wire's one part) — every other line is content the model
    receives, in order."""

    prompt: AssembledPrompt
    tail_setting: int  # the configured min_tail_messages the rung is compared with
    parts: tuple[ContextPart, ...]
    injections: tuple[ContextInjection, ...] = ()
    # What the preview could not know: "" when it knew everything, else
    # the sentence — the model is not loaded, so the window it was cut
    # to is the assembler's substitute until the turn loads it.
    note: str = ""
    # The text wire: the request as ONE TEXT, exactly as the wire sends
    # it (`AssembledPrompt.as_text`), and the template it is rendered in;
    # "" in chat mode, where the parts are the wire.
    wire: str = ""
    template: str = ""

    @property
    def total_tokens(self) -> int:
        """What the request costs, at the assembler's estimate: the
        messages', or the text's where the text is the wire — its
        markers and framing counted."""
        return domain_context.total_tokens(self.prompt, self.wire)

    @property
    def kept(self) -> int:
        """Messages sent verbatim — head and tail together."""
        return self.prompt.head + self.prompt.tail

    @property
    def used(self) -> int:
        """Percent of the limit."""
        return domain_context.used(self.prompt, self.wire)

    @property
    def summary(self) -> str:
        """What the request is made of, in words — the same figures the
        diagram is drawn from, said in the report's own sentences."""
        return _summary(self)

    def text(self, *, dim: str = "", reset: str = "") -> str:
        """The whole preview as a terminal pages it. `dim`/`reset`
        bracket the role markers, so a terminal can fade them."""
        out = [self.summary] + ([self.note] if self.note else [])
        for part in self.parts:
            # The marker names the pictures riding the part beside the
            # role: they are content the wire carries, drawn as a count.
            plural = "s" if part.pictures != 1 else ""
            riding = f" · {part.pictures} picture{plural} attached" if part.pictures else ""
            out.extend(["", f"{dim}[{part.role}{riding}]{reset}", part.body])
        return "\n".join(out)


@dataclass(frozen=True)
class ContextPart:
    """One message of the request, as the wire will carry it — its text,
    and how many pictures ride it."""

    role: str
    body: str
    pictures: int = 0


@dataclass(frozen=True)
class ContextInjection:
    """One text the request carries besides the story: its name — the
    setting's `injection_label`, what the depth ruler places — where it
    rides (the `position`, and its `position_text` as the ruler names
    it) and what it costs."""

    label: str
    position: InjectionPosition
    position_text: str
    tokens: int


@dataclass(frozen=True)
class ContextFill:
    """How full the next request is, as `/context` measures it: its
    `percent` of the prompt's limit — over 100 where the story cannot fit
    even fully summarized — and `context_in_force`, what the request is
    cut to."""

    percent: int
    context_in_force: int

    def text(self) -> str:
        return f"{self.percent}% of {format_context(self.context_in_force)}"


# ---------- the context report's summary ----------


def _summary(report: ContextReport) -> str:
    prompt = report.prompt
    lines = ["Context preview — the exact request to be sent. Context summary:", ""]
    lines.append(
        f"  ~{report.total_tokens:,} tokens projected · "
        f"{report.used}% of the {prompt.limit:,} limit"
    )
    if report.wire:
        lines.append(f"  text wire: one text, in the {report.template} template")
    if prompt.system_tokens:
        lines.append(f"  system {prompt.system_tokens:,} · transcript {prompt.transcript_tokens:,}")
    # The summaries are not a third slice of the transcript: they STAND IN
    # for the messages between head and tail. Naming that count is what
    # makes the line add up to the story's length instead of to nothing.
    if prompt.history:
        # Case 4: old summaries folded into the story so far. Displaced
        # summaries are named, not folded in: the story so far covers
        # the replaced scenes, and the line says so or the count would
        # claim the kept summaries cover the whole middle.
        plural = "s" if prompt.scenes_rolled_up != 1 else ""
        lines.append(
            f"  {prompt.head} head + {prompt.tail} tail verbatim, plus {prompt.middle} middle "
            f"inserted in between as the story so far ({prompt.scenes_rolled_up} "
            f"scene{plural}) and {prompt.scenes_summarized} scene summaries"
        )
    elif prompt.scenes_summarized:
        # Case 3: the covered middle rides as its scene summaries.
        lines.append(
            f"  {prompt.head} head + {prompt.tail} tail verbatim, plus {prompt.middle} middle "
            f"inserted in between as {prompt.scenes_summarized} scene summaries"
        )
    else:
        # Cases 1-2: a short story, or nothing covering the middle —
        # everything verbatim.
        lines.append(f"  {report.kept} messages verbatim")
    if prompt.pictures_sent:
        plural = "s" if prompt.pictures_sent != 1 else ""
        lines.append(
            f"  {prompt.pictures_sent} picture{plural} riding the verbatim messages, counted at "
            f"~{IMAGE_TOKENS:,} tokens each"
        )
    if prompt.pictures_held:
        plural = "s" if prompt.pictures_held != 1 else ""
        lines.append(
            f"  {prompt.pictures_held} picture{plural} on earlier messages held back: this engine "
            "puts every picture in a request on the latest message, so only the latest turn's ride"
        )
    if prompt.pictures_omitted:
        plural = "s" if prompt.pictures_omitted != 1 else ""
        lines.append(
            f"  {prompt.pictures_omitted} picture{plural} on the verbatim messages not sent: "
            "the model cannot see, or the file is gone"
        )
    if report.injections:
        # What rides besides the story, each where the story put it — a
        # bullet each, named as the setting names what it injects; the
        # total where there is something to total. The texts themselves
        # are in the parts, being the wire.
        total = sum(i.tokens for i in report.injections)
        summed = f" (~{total:,} total)" if len(report.injections) > 1 else ""
        lines.append(f"  injected{summed}:")
        lines.extend(
            f"  - {i.label} ({i.position_text}, ~{i.tokens:,} tokens)" for i in report.injections
        )
    if prompt.tail_target < report.tail_setting:
        # Case 5: the tail stepped down so the context could fit (a
        # case-6 refusal never reaches this report — the preview refuses
        # with the same sentence the turn would).
        lines.append(
            f"  the tail aims at {prompt.tail_target} messages instead of the configured "
            f"{report.tail_setting}, so the context fits the limit"
        )
    return "\n".join(lines)
