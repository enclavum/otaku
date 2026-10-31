"""The request the assembler builds, and how each wire sends it.

Its text is collected as pieces in order, each with where it comes from
(`PromptPiece`, `Provenance`), and kept as pieces (`AssembledPrompt`):
the chat wire merges them into turns (`as_chat`), the text wire joins
them into one text (`as_text`), and a preview reads them as they are
(`text_pieces` for the text wire's). Every figure the pieces can tell
is read off them; the rest — the budget, the shape of the transcript —
is what the assembly decided (`context.assembler`).
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import Enum

from otaku.providers.openai.templates import TextTemplate
from otaku.store.schema import InjectionPosition

# What one picture costs on the wire, as the budget counts it. Engines
# differ widely (Gemma 3 spends 256 a picture, Qwen2.5-VL about 3,000 at
# the wire size, Llama 3.2 1,600 a tile); the stats line reports the
# real prompt count after every reply, so this is a first estimate.
IMAGE_TOKENS_ESTIMATE = 1_500


@dataclass(frozen=True)
class WirePicture:
    """One picture as SENT: its bytes and their media type, read from
    the files folder for a verbatim row. Satisfies `providers.WireImage`."""

    data: bytes
    media_type: str


class JoinType(Enum):
    """How a row joins the text before it — the ONE seam vocabulary, on
    a piece, a wire turn and on the wire to a frontend (`value`): INLINE,
    one space before it; PARAGRAPH, a blank line. `assembler.get_join_type`
    decides, and `join` writes it."""

    INLINE = "inline"
    PARAGRAPH = "paragraph"


class Provenance(Enum):
    """Where a piece of the request's text comes from. The summary that
    stands in for the middle comes in three: the line over its summaries
    (`HEADER`), the story so far through the scenes rolled up into it
    (`HISTORY`), and each kept scene's summary (`SCENE`)."""

    SYSTEM = "system"  # the premise; on the text wire, the template's markers and framing too
    MESSAGE = "message"  # a stored row as sent: a line, a reply, a chapter, a tool result
    # A card's row, imported rather than played: its text composed from
    # the character's profile, which the reader may never have read whole.
    CARD = "card"
    # A pinned message that survived its scene's summary, quoted whole after
    # it, out of its place, under the PINNED_HEADER that opens the quoted
    # run. Anywhere else a pinned message is a MESSAGE.
    PINNED_MESSAGE = "pinned_message"
    PINNED_HEADER = "pinned_header"
    HEADER = "header"
    HISTORY = "history"
    SCENE = "scene"
    INJECTION = "injection"  # a text sent besides the story: a setting's, a card's


@dataclass(frozen=True)
class PromptPieceMetadata:
    """Where a piece comes from (`provenance`), and which one: a scene's
    summary names its scene (its number as the dossier counts it, and its
    title), an injection its owner (a setting's name, a card's) and where
    it was asked to go, a card the character it came in under — empty
    where it does not apply."""

    provenance: Provenance
    scene_num: int | None = None
    scene_title: str = ""
    injection_owner: str = ""
    injection_position: InjectionPosition | None = None
    card_name: str = ""


@dataclass(frozen=True, kw_only=True)
class PromptPiece:
    """One piece of the request's text as the assembler collects it, in
    order: its role — a stored row's own, "system" in the system message —
    its wire text, the pictures riding it, its seam with the piece before
    it, and where it comes from. Distinct from the stored `Message` on
    purpose: a stored row's body is the line as typed, a piece's text is
    what the model receives."""

    role: str
    text: str
    images: tuple[WirePicture, ...] = ()
    join_type: JoinType = JoinType.PARAGRAPH
    metadata: PromptPieceMetadata


@dataclass(frozen=True)
class WireTurn:
    """One turn as SENT over the chat wire, merged from the pieces of one
    role (`AssembledPrompt.as_chat`): its text and the pictures riding
    on it, nothing else. (`body`, not `text`, so it satisfies
    `providers.WireMessage`.)"""

    role: str
    body: str
    images: tuple[WirePicture, ...] = ()
    # Reads differently next request: an injection that moves with the
    # end landed in it, or above it. A prompt-cache mark belongs before.
    volatile: bool = False
    # How it joins the text before it: its first piece's seam.
    join_type: JoinType = JoinType.PARAGRAPH


@dataclass(frozen=True)
class InjectionSent:
    """One injection as the request carries it: whose (its `owner`, a
    setting's name or a card's), where it was asked to go, and what it
    costs — in `system_tokens` at the system position, in
    `transcript_tokens` at a depth."""

    owner: str
    position: InjectionPosition
    tokens: int


@dataclass(frozen=True)
class CardSent:
    """One card row as the request carries it: whose (the name it came in
    under) and what it costs, its portrait counted — in
    `transcript_tokens`, whether it rides verbatim or whole in the recap."""

    name: str
    tokens: int


@dataclass(frozen=True)
class AssembledPrompt:
    """The request as the assembler collected it — its pieces in order —
    and what the assembly decided beside them. Each wire concatenates
    the pieces as it sends them: the chat wire into turns (`as_chat`),
    the text wire into one text (`as_text`)."""

    # Every piece of the request's text, in order: the system message's —
    # the premise, then each injection at the system position — then the
    # transcript's.
    pieces: tuple[PromptPiece, ...]
    # prompts.toml's `story_framing`: the TEXT wire's standing instruction
    # around the system message (`text_completion_system`).
    story_framing: str

    # The budget.
    limit: int  # what the prompt measured against: the context less the reply reserve
    transcript_tokens: int  # head + recap + tail estimate, the injected rows among them

    # The transcript, split: verbatim, stood in for, verbatim.
    head: int
    middle: int  # what the recap stands in for
    tail: int
    tail_target: int  # the case-5 rung it was built at; below the setting, the limit forced it
    scenes_rolled_up: int  # scenes the history covers instead, 0 without one

    # The pictures not sent.
    pictures_omitted: int = 0  # attached to verbatim rows but not sent: no vision, or a file gone
    pictures_held: int = (
        0  # on earlier verbatim rows, held back: the engine takes the latest turn's alone
    )

    @property
    def system(self) -> str:
        """The system message's text: its pieces — the premise, then each
        injection at the system position — a blank line apart; "" when
        the request carries none: the chat wire then sends no system
        row, and the text wire frames the story with its standing
        instruction alone."""
        return "\n\n".join(piece.text for piece in self.pieces if piece.role == "system")

    @property
    def text_completion_system(self) -> str:
        """The system message for the TEXT wire: `story_framing` around it,
        `{premise}` filled — the standing instruction a model turn needs
        before it (a model turn with no turn before it degenerates, and a
        new story's premise is empty). Never "": the framing stands."""
        return self.story_framing.replace("{premise}", self.system).strip()

    @property
    def system_tokens(self) -> int:
        """The system message's estimate — the premise and what was
        injected after it."""
        return estimate_tokens(self.system) if self.system else 0

    @property
    def total_tokens(self) -> int:
        return self.system_tokens + self.transcript_tokens

    @property
    def kept(self) -> int:
        """Messages sent verbatim — the head and the tail together."""
        return self.head + self.tail

    @property
    def history(self) -> bool:
        """Whether a story-so-far opens the recap (case 4's mark)."""
        return any(p.metadata.provenance is Provenance.HISTORY for p in self.pieces)

    @property
    def scenes_summarized(self) -> int:
        """Scene summaries standing in for the middle."""
        return sum(1 for p in self.pieces if p.metadata.provenance is Provenance.SCENE)

    @property
    def pictures_sent(self) -> int:
        """Pictures riding the rows sent whole, `IMAGE_TOKENS_ESTIMATE` each in
        `transcript_tokens`."""
        return sum(len(piece.images) for piece in self.pieces)

    @property
    def injections(self) -> tuple[InjectionSent, ...]:
        """The injections, in the order sent — what `/context` reports of
        them."""
        out: list[InjectionSent] = []
        for piece in self.pieces:
            meta = piece.metadata
            if meta.provenance is Provenance.INJECTION and meta.injection_position is not None:
                tokens = estimate_tokens(piece.text)
                out.append(InjectionSent(meta.injection_owner, meta.injection_position, tokens))
        return tuple(out)

    @property
    def cards(self) -> tuple[CardSent, ...]:
        """The card rows, in the order sent, each costed with its portrait
        — what `/context` reports of them."""
        return tuple(
            CardSent(
                piece.metadata.card_name,
                estimate_tokens(piece.text) + IMAGE_TOKENS_ESTIMATE * len(piece.images),
            )
            for piece in self.pieces
            if piece.metadata.provenance is Provenance.CARD
        )

    @property
    def card_tokens(self) -> int:
        """What the card rows cost together, their portraits counted."""
        return sum(card.tokens for card in self.cards)

    @property
    def injection_tokens(self) -> int:
        """What the injections cost together, wherever each rides."""
        return sum(injection.tokens for injection in self.injections)

    def as_chat(self) -> list[WireTurn]:
        """The request as the CHAT wire sends it: the system message, then
        the transcript's pieces, each run of one role merged into one
        turn, pictures and all: storage granularity is otaku's
        bookkeeping, the model sees one prompt per exchange. A turn opens
        on its first piece's words, the seam before them its `join_type`.
        A tool's result goes as the reader's — the chat wire has two
        sides, and a tool's result is what the model is told. A turn is
        volatile from the first injected piece on: it moves with the end,
        so a prompt-cache mark belongs before it."""
        out = [WireTurn(role="system", body=self.system)] if self.system else []
        volatile = False
        for piece in self.pieces:
            if piece.role == "system":
                continue
            volatile = volatile or piece.metadata.provenance is Provenance.INJECTION
            role = "user" if piece.role == "tool" else piece.role
            if out and out[-1].role == role:
                # a turn opened by a picture alone has no words to seam to
                body = out[-1].body
                out[-1] = WireTurn(
                    role=role,
                    body=join(body, piece.join_type, piece.text) if body else piece.text.lstrip(),
                    images=out[-1].images + piece.images,
                    volatile=volatile,
                    join_type=out[-1].join_type,
                )
            else:
                text = piece.text.lstrip()
                out.append(WireTurn(role, text, piece.images, volatile, piece.join_type))
        return out

    def as_text(self, template: TextTemplate, opener: str = "") -> str:
        """The request as the TEXT wire sends it: `text_completion_system`
        and the story (`as_story_text`, `opener` appended — a retry's
        paragraph break) rendered in `template`'s turn markers, the story
        as the model's own turn in progress."""
        return template.render(self.text_completion_system, self.as_story_text() + opener)

    def as_story_text(self) -> str:
        """The story as ONE TEXT, for the text wire: the chat wire's turns
        in order, the system's aside; no roles, no labels, each seam its
        turn's (`join`). The text keeps the last turn's trailing break, a
        paragraph the reader opened for the model to write in. The
        pictures do not ride it (the caller holds them: `pictures_held`)."""
        out = ""
        for turn in self.as_chat():
            if turn.role == "system":
                continue
            out = join(out, turn.join_type, turn.body) if out else turn.body.lstrip()
        return out

    def text_pieces(self, template: TextTemplate) -> list[PromptPiece]:
        """The request as the TEXT wire sends it, without an opener, in
        pieces for a preview — cut out of what is sent (`_cut`): the
        template's render up to the story, holding the system message's
        pieces, and the story, holding the transcript's. Joined, exactly
        `as_text(template)`."""
        head = template.render(self.text_completion_system, "")
        own = [piece for piece in self.pieces if piece.role == "system"]
        story = [replace(piece, images=()) for piece in self.pieces if piece.role != "system"]
        return [*_cut(head, own), *_cut(self.as_story_text(), story)]


def join(previous: str, join_type: JoinType, next: str) -> str:
    """`next` after `previous` across a seam `join_type` decided: one
    space inline, a blank line for a paragraph — the whitespace at the
    two edges given up for it, so the space is never stored and a word
    is never completed across rows."""
    return previous.rstrip() + (" " if join_type is JoinType.INLINE else "\n\n") + next.lstrip()


def estimate_tokens(text: str) -> int:
    """~4 chars/token, never less than one — close enough for budgeting
    without a tokenizer."""
    return max(1, len(text) // 4)


def _cut(text: str, pieces: Sequence[PromptPiece]) -> list[PromptPiece]:
    """`text` cut into `pieces`, each found in it in order: the whitespace
    before a piece is its seam and goes with it; text between two pieces
    — a template's marker, the framing — is the system's own; whitespace
    after the last goes with it. A piece the text does not hold where the
    order puts it leaves the whole text the system's."""
    out: list[PromptPiece] = []
    at = 0
    for piece in pieces:
        core = piece.text.strip()
        found = text.find(core, at)
        if found < 0:
            return [_system_piece(text)] if text else []
        gap = text[at:found]
        if gap.strip():
            out.append(_system_piece(gap))
            gap = ""
        out.append(replace(piece, text=gap + core))
        at = found + len(core)
    rest = text[at:]
    if out and not rest.strip():
        out[-1] = replace(out[-1], text=out[-1].text + rest)
    elif rest:
        out.append(_system_piece(rest))
    return _merged_system(out)


def _system_piece(text: str) -> PromptPiece:
    """A piece of the system's own: the premise, or on the text wire a
    marker or the framing."""
    return PromptPiece(role="system", text=text, metadata=PromptPieceMetadata(Provenance.SYSTEM))


def _merged_system(pieces: list[PromptPiece]) -> list[PromptPiece]:
    """The pieces with each run of the system's own made one, and an
    empty piece — a picture's alone, which the text wire does not
    carry — dropped."""
    out: list[PromptPiece] = []
    for piece in pieces:
        if not piece.text:
            continue
        is_system = piece.metadata.provenance is Provenance.SYSTEM
        if is_system and out and out[-1].metadata.provenance is Provenance.SYSTEM:
            out[-1] = replace(out[-1], text=out[-1].text + piece.text)
        else:
            out.append(piece)
    return out
