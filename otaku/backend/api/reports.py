"""The read-only reports both frontends show.

One function per report, and what it returns is the report: the facts it
is made of, and `text()` — the same facts as a terminal pages them. A
frontend that PRINTS asks for the text; one that DRAWS (a table, a
definition list, the window diagram) takes the facts and draws them.
Neither invents a fact, neither rewords a sentence, and the two cannot
drift because the text is rendered from the facts beside it.
"""

from dataclasses import dataclass

from otaku.backend.api import stories
from otaku.backend.session import NO_MODEL_HINT, Refused, Session
from otaku.context.assembler import AssembledPrompt, ContextOverflowError
from otaku.formatting import (
    Money,
    format_context,
    format_size,
    pretty_path,
    printable,
    truncate_label,
)
from otaku.providers import ALL_CLIENTS, Locality, ModelState, reasoning


@dataclass(frozen=True)
class AssembledShape:
    """What the next request is MADE of — the facts the diagram and the
    summary line are drawn from, in the window's own order: the verbatim
    head, the middle and how the recap tells it, the verbatim tail, and
    what it all costs against the limit.

    Not the assembler's `ContextShape`, which is the SETTING (how many
    to keep); this is what came of it."""

    head: int  # opening messages, verbatim
    middle: int  # messages the recap stands in for — never a third slice of what is sent
    history: bool  # a story-so-far opens the recap (old summaries folded into it)
    rolled_up: int  # the scenes it covers; 0 without one
    summaries: int  # scene summaries riding after it
    tail: int  # recent messages, verbatim
    tail_target: int  # the tail aimed for — below tail_setting, the limit forced it
    tail_setting: int  # the configured min_tail_messages
    system_tokens: int
    transcript_tokens: int
    limit: int  # what the prompt measured against: min(window, max_context) - reply reserve

    @property
    def kept(self) -> int:
        """Messages sent verbatim — head and tail together."""
        return self.head + self.tail

    @property
    def total_tokens(self) -> int:
        return self.system_tokens + self.transcript_tokens

    @property
    def used(self) -> int:
        """Percent of the limit."""
        return round(100 * self.total_tokens / self.limit) if self.limit else 0


@dataclass(frozen=True)
class ContextPart:
    """One message of the request, as the wire will carry it."""

    role: str
    body: str


@dataclass(frozen=True)
class ContextReport:
    """The next request EXACTLY as it will be sent: what it is made of,
    the summary that says so in words, and one part per message.

    Nothing in it is otaku's own text except the summary and the role
    markers `text` brackets (they stand for the JSON role field) — every
    other line is content the model receives, in order."""

    shape: AssembledShape
    summary: str
    parts: tuple[ContextPart, ...]

    def text(self, *, dim: str = "", reset: str = "") -> str:
        """The whole preview as a terminal pages it. `dim`/`reset`
        bracket the role markers, so a terminal can fade them."""
        out = [self.summary]
        for part in self.parts:
            out.extend(["", f"{dim}[{part.role}]{reset}", part.body])
        return "\n".join(out)


def context(session: Session) -> ContextReport:
    """Preview the next request: what it is made of, said in words, and
    one part per message it will carry. No model: the preview still
    stands, over the assembler's default window — what WOULD be sent is
    a question that needs no server."""
    client = session._client()
    found = client.models.get(session.model) if client is not None else None
    max_context = found.max_context if found else None
    try:
        prompt = session.assemble(max_context)
    except ContextOverflowError as e:
        # The preview of a request that would not be sent is its refusal.
        raise Refused(str(e)) from e
    shape = _shape(prompt)
    return ContextReport(
        shape=shape,
        summary=_summary(shape),
        parts=tuple(
            ContextPart(turn.role, "\n".join(_preview_body(printable(turn.body), prompt.recap)))
            for turn in prompt.messages
        ),
    )


# What each recorded purpose is CALLED. The stored word is the store's
# ("chat", "lore", "rollup"); what a reader is shown is decided once,
# here, so the terminal's column and the page's section head can never
# name the same spend two different things. An unknown purpose keeps
# its stored word — a new one shows up honestly rather than vanishing.
USAGE_PURPOSES: dict[str, str] = {
    "chat": "Played turns",
    "lore": "Extraction",
    "rollup": "History rollup",
}


@dataclass(frozen=True)
class UsageRow:
    """One thing tokens were spent on: a purpose, on a model, at a
    provider. `rate` is completion tokens per second, 0.0 unmeasured;
    `cached_tokens` is the slice of the prompt the provider served from
    its cache — 0 wherever caching never engaged."""

    purpose: str
    provider: str
    model: str
    requests: int
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    rate: float

    @property
    def purpose_label(self) -> str:
        """What to call this row's purpose — the shared spelling."""
        return USAGE_PURPOSES.get(self.purpose, self.purpose)


@dataclass(frozen=True)
class UsageReport:
    """What the tokens went on, by purpose and by model."""

    scope: str  # what was counted: "this story" or "all stories"
    rows: tuple[UsageRow, ...]
    requests: int
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def note(self) -> str:
        """The figures said in a sentence — and the one thing a column
        of numbers cannot say: how much of the spend nobody asked for.
        Extraction runs on its own, and on a paid provider it is billed
        exactly like a turn somebody typed."""
        said = f"{self.prompt_tokens:,} asked, {self.completion_tokens:,} answered"
        if self.cached_tokens:
            said += f", {self.cached_tokens:,} of it served from cache"
        unasked = sum(
            row.prompt_tokens + row.completion_tokens for row in self.rows if row.purpose != "chat"
        )
        if not unasked:
            return f"{said}."
        return (
            f"{said}. {unasked:,} of that is the extractor's, spent without being asked "
            "— a paid provider bills it like any other request."
        )

    def text(self) -> str:
        """The rows as a terminal prints them — a column each, the text
        columns joined with " · ". `note` is NOT printed: a table of
        figures said again in a sentence is a page's idea of a summary,
        and a terminal that already shows every column has said it."""
        purpose_w = max(len("total"), max(len(r.purpose_label) for r in self.rows))
        provider_w = max(len(r.provider) for r in self.rows)
        model_w = max(len(r.model) for r in self.rows)
        # The header and total rows blank the separator out, so the
        # numeric columns stay aligned under it.
        head = f"  {'':<{purpose_w}}   {'':<{provider_w}}   {'':<{model_w}}"
        out = [
            f"Token usage — {self.scope}:",
            f"{head}  {'REQS':>5}  {'PROMPT':>10}  {'CACHED':>10}  {'REPLY':>10}  {'TOK/S':>7}",
        ]
        for r in self.rows:
            named = f"{r.purpose_label:<{purpose_w}} · {r.provider:<{provider_w}}"
            out.append(
                f"  {named} · {r.model:<{model_w}}  "
                f"{r.requests:>5,}  {r.prompt_tokens:>10,}  {r.cached_tokens:>10,}"
                f"  {r.completion_tokens:>10,}  {r.rate:>7.1f}"
            )
        out.append(
            f"  {'total':<{purpose_w}}   {'':<{provider_w}}   {'':<{model_w}}"
            f"  {self.requests:>5,}  {self.prompt_tokens:>10,}  {self.cached_tokens:>10,}"
            f"  {self.completion_tokens:>10,}"
            f"  {'':>7}\n  ({self.total_tokens:,} tokens across "
            f"{len(self.rows)} model/purpose pairs)"
        )
        return "\n".join(out)


# What /usage can count: the argument a frontend passes, and what the
# report calls that scope. Declared here because both are the report's
# own language — the terminal takes the argument on the command line,
# and the page draws one tab per row.
USAGE_SCOPES: tuple[tuple[str, str], ...] = (("", "this story"), ("all", "all stories"))


def usage(session: Session, raw: str = "") -> UsageReport:
    """Tokens spent on this story — or on every story when `raw` is
    "all" (the one argument this command knows; anything else is Refused
    with the usage line). Grouped by what the tokens were spent on
    (chat, lore, …), then by provider and model. Raises Refused when
    there is nothing to report."""
    argument = raw.strip().lower()
    if argument not in ("", "all"):
        raise Refused("Usage: /usage [all]")
    everything = argument == "all"
    if not everything and session.story_id is None:
        raise Refused("No story yet — send a message first, or use /usage all.")
    totals = session._store.usage.get_totals(None if everything else session.story_id)
    if not totals:
        raise Refused(
            "No recorded usage yet." if everything else "No recorded usage for this story."
        )
    rows = tuple(
        UsageRow(
            purpose=row.purpose,
            provider=row.provider,
            model=row.model,
            requests=row.requests,
            prompt_tokens=row.prompt_tokens,
            completion_tokens=row.completion_tokens,
            cached_tokens=row.cached_tokens,
            rate=row.completion_tokens / row.seconds if row.seconds > 0 else 0.0,
        )
        for row in totals
    )
    return UsageReport(
        scope=dict(USAGE_SCOPES)[argument],
        rows=rows,
        requests=sum(row.requests for row in rows),
        prompt_tokens=sum(row.prompt_tokens for row in rows),
        completion_tokens=sum(row.completion_tokens for row in rows),
        cached_tokens=sum(row.cached_tokens for row in rows),
    )


@dataclass(frozen=True)
class InfoSection:
    """One block of `info`: labelled facts, or the one sentence that
    stands where the facts would be (no model configured)."""

    rows: tuple[tuple[str, str], ...] = ()
    note: str = ""


@dataclass(frozen=True)
class InfoReport:
    """Everything otaku knows about this session, in blocks."""

    sections: tuple[InfoSection, ...]

    def text(self) -> str:
        """The blocks as a terminal prints them: one labelled fact per
        line, a blank line between blocks."""
        width = (
            max((len(label) for section in self.sections for label, _ in section.rows), default=0)
            + 2
        )
        out: list[str] = []
        for section in self.sections:
            if out:
                out.append("")
            if section.note:
                out.append(section.note)
            out.extend(f"{label + ':':<{width}}{value}" for label, value in section.rows)
        return "\n".join(out)


def info(session: Session) -> InfoReport:
    """Everything otaku knows about the active model and session, in
    blocks, best-effort: network-backed fields are silently skipped.
    Without a model only that block is missing — the state dir, the
    story, its premise and the parameters are the session's own, and
    reporting them needs no provider."""
    state = InfoSection(rows=(("State dir", pretty_path(session._paths.root)),))
    if session._client() is None:
        model = InfoSection(note=NO_MODEL_HINT)
    else:
        model = InfoSection(rows=_model_info(session))
    return InfoReport((state, model, InfoSection(rows=_session_rows(session))))


@dataclass(frozen=True)
class Balance:
    provider: str  # the configured section's name
    label: str  # what to CALL it: the provider's own caption
    money: Money | None  # None when the account would not say
    note: str = ""  # why there is no figure \u2014 never empty when money is None

    @property
    def value(self) -> str:
        """The row as one string: the figure, or the reason there is
        none. What both frontends print when they print a line."""
        return str(self.money) if self.money is not None else self.note


@dataclass(frozen=True)
class BalanceReport:
    """What each cloud account has left, and what THIS story spends
    against it."""

    rows: tuple[Balance, ...]
    note: str = ""  # what the story on screen costs, in one sentence

    @property
    def total(self) -> Money | None:
        """Everything on account, when it can be added up: one currency
        across every row that answered. Mixed currencies have no total \u2014
        adding them would be a conversion, and otaku has no rate."""
        figures = [row.money for row in self.rows if row.money is not None]
        if not figures or len({money.currency for money in figures}) != 1:
            return None
        return sum(figures[1:], figures[0])

    def text(self) -> str:
        """The rows, aligned for a terminal. `total` and `note` are
        FACTS this report carries and NOT part of what it prints: a
        summed line and a sentence about the story on screen are a
        page's shape, and a reader looking down four figures has added
        them already."""
        width = max(len(row.label) for row in self.rows)
        return "\n".join(f"{row.label:<{width}}  {row.value}" for row in self.rows)


# What stands where a figure would be. Not a zero and not a blank: a row
# that is there because the provider is, with nothing to report on it yet.
NO_KEY = "no key set"
NO_ANSWER = "\u2014"


def balances(session: Session, *, probe: bool = True) -> BalanceReport:
    """Every provider with an account to bill, and what each says it has
    left. EVERY one: a provider with no key, a wrong key or an outage
    keeps its row with a dash, because a report of only what answered
    cannot be told apart from one that found nothing — and the row with
    the dash is usually the one the reader came to look at. The cloud
    catalogs are asked concurrently; a provider on this machine has
    no account.

    `probe=False` asks no network at all and returns the ROSTER — each
    keyed row with no figure and an EMPTY note, meaning "not asked yet"
    rather than "would not answer": the page paints the whole slip from
    it and fills the figures from one probed report. The terminal asks
    plainly and gets everything in one wait."""
    registry = session._providers_registry
    configured = set(registry.list())

    def account(provider: str) -> Balance | None:
        client = registry.get(provider)
        if client is None or client.locality is not Locality.REMOTE:
            return None  # a provider on this machine has no account to ask
        # What to CALL it: the provider's own caption — "OpenRouter", not
        # "openrouter". A section somebody named themselves keeps THEIR
        # name, with the provider in brackets: two sections of one kind
        # are two accounts, and a report of balances that cannot tell
        # them apart is a report of one number twice.
        named = client.label if provider == client.id else f"{provider} ({client.label})"
        # An account nobody has a key for was never asked: that is a
        # different fact from an account that would not answer, and the
        # reader can act on one of them.
        if client.auth.key_source is None:
            return Balance(provider, named, None, NO_KEY)
        if not probe:
            return Balance(provider, named, None, "")
        try:
            money = client.balance(timeout=5.0)
        except Exception:
            money = None
        return Balance(provider, named, money, "" if money else NO_ANSWER)

    rows = [row for row in registry.map(account) if row]
    # A cloud provider otaku ships a client for and nobody has configured
    # is still an account a reader may be about to open: it belongs in
    # the list, with nothing in it.
    rows += [
        Balance(id, cls.label, None, NO_KEY)
        for id, cls in ALL_CLIENTS.items()
        if cls.locality is Locality.REMOTE and id not in configured
    ]
    if not rows:
        raise Refused("No cloud providers.")
    # What the story on screen costs, in one sentence. A balance is only
    # ever read as "can I afford to keep playing", and the answer depends
    # on what this story is PLAYING on — which a list of accounts does
    # not say.
    playing, client = session.provider, session._client()
    if client is None or not playing:
        spending = "Paid providers are charged only when you play on one."
    elif client.locality is Locality.REMOTE:
        spending = f"This story runs on {playing}, and every reply is billed to that account."
    else:
        spending = (
            f"This story runs on {playing}, which spends nothing. "
            "Paid providers are charged only when you switch to one."
        )
    return BalanceReport(tuple(rows), spending)


# ---------- report internals ----------


# A model's load state as the Loaded row says it.
_STATE_WORDS = {
    ModelState.LOADED: "yes",
    ModelState.UNLOADED: "no",
    ModelState.LOADING: "loading",
    ModelState.UNKNOWN: "unknown",
}


def _model_info(session: Session) -> tuple[tuple[str, str], ...]:
    """The active model's block of `info` — the caller checked a model
    is active."""
    client = session._client()
    assert client is not None
    # The registry's copy, not a snapshot: a URL or key edited in the
    # picker panel shows here immediately.
    config = client.config
    # Two facts, not one: a frontend that wants to set the URL under the
    # backend's own line cannot split a parenthesis back apart.
    out = [("Model", session.full_model_name), ("Backend", client.id), ("URL", config.url)]
    if client.auth.key_source is not None:
        out.append(("Auth", "api_key configured"))
    # The model's own row. Load state and size only where loading is a
    # real state (a cloud catalog serves everything statically); the
    # capabilities from any provider that reports them, a catalog included
    # — that one costs a full catalog fetch, and /info is the place to
    # pay it. The generic provider reports none of these, wherever its
    # url points, so it is not asked.
    row = client.models.get(session.model) if client.locality is not Locality.UNKNOWN else None
    if row is not None and client.locality is Locality.LOCAL:
        out.append(("Loaded", _STATE_WORDS[row.state]))
        if row.size:
            out.append(("Size", format_size(row.size)))
    # What a request gets — the loaded instance's context size, or the
    # model's own where nothing loads — and the model's own beside it
    # when it is known and differs.
    max_context = format_context(row.max_context if row else None)
    if max_context:
        out.append(("Max context", max_context))
    catalogue = format_context(row.max_context_catalogue if row else None)
    if catalogue and catalogue != max_context:
        out.append(("Model context", catalogue))
    # What the model can do, as the provider says it — one fact per row,
    # "unknown" where the provider could not say (and the app offers
    # nothing on it). The efforts are listed in the wire's order; a
    # model no effort reaches supports none.
    caps = row.capabilities if row is not None else None
    out.append(("Vision", _yes_no(caps.vision if caps else None)))
    efforts = caps.reasoning if caps else None
    if efforts is None:
        out.append(("Reasoning efforts", "unknown"))
    else:
        named = ", ".join(effort for effort in reasoning.EFFORTS if effort in efforts)
        out.append(("Reasoning efforts", named or "no efforts supported"))
    out.append(("Text completion", _yes_no(caps.text_completion if caps else None)))
    out.append(("Thinking", session.think if session.think else "default"))
    if config.keep_alive:
        out.append(("Keep-alive", str(config.keep_alive)))
    if client.capabilities.prompt_cache:
        # Displayed here, decided in providers.toml — the keep_alive
        # pattern: behaviour keys are read in /info, edited in the file.
        out.append(("Prompt cache", config.prompt_cache or "5m"))
    return tuple(out)


def _yes_no(fact: bool | None) -> str:
    return "unknown" if fact is None else ("yes" if fact else "no")


def _session_rows(session: Session) -> tuple[tuple[str, str], ...]:
    """The session's block of `info` — what is loaded, not what answers."""
    out = []
    if label := stories.headline(session):
        out.append(("Story", truncate_label(label, stories.LABEL_WIDTH)))
    out.append(("Messages", str(len(session.messages))))
    # No premise row: a premise is a DOCUMENT, not a fact about the
    # session — it is as long as a reader made it, and `/system` reports
    # it on its own, at whatever length that is. A report of one-line
    # facts is the wrong place to print an imported lorebook.
    if session.params:
        out.append(("Parameters", ", ".join(f"{k} = {v}" for k, v in session.params.items())))
    return tuple(out)


def _shape(prompt: AssembledPrompt) -> AssembledShape:
    """What was assembled, counted — the arithmetic both the diagram and
    the summary line stand on, done once."""
    return AssembledShape(
        head=prompt.head_count,
        middle=prompt.transcript_total - prompt.transcript_kept,
        history=bool(prompt.history),
        rolled_up=prompt.scenes_rolled_up,
        summaries=prompt.scenes_summarized,
        tail=prompt.transcript_kept - prompt.head_count,
        tail_target=prompt.tail_target,
        tail_setting=prompt.tail_setting,
        system_tokens=prompt.system_tokens,
        transcript_tokens=prompt.transcript_tokens,
        limit=prompt.limit,
    )


def _summary(shape: AssembledShape) -> str:
    """What the request is made of, in words — the same arithmetic the
    diagram is drawn from, said in the report's own sentences."""
    lines = ["Context preview — the exact request to be sent. Context summary:", ""]
    lines.append(f"  ~{shape.total_tokens:,} tokens · {shape.used}% of the {shape.limit:,} limit")
    if shape.system_tokens:
        lines.append(f"  system {shape.system_tokens:,} · transcript {shape.transcript_tokens:,}")
    # The summaries are not a third slice of the transcript: they STAND IN
    # for the messages between head and tail. Naming that count is what
    # makes the line add up to the story's length instead of to nothing.
    if shape.history:
        # Case 4: old summaries folded into the story so far. Displaced
        # summaries are named, not folded in: the story so far covers
        # the replaced scenes, and the line says so or the count would
        # claim the kept summaries cover the whole middle.
        plural = "s" if shape.rolled_up != 1 else ""
        lines.append(
            f"  {shape.head} head + {shape.tail} tail verbatim, plus {shape.middle} middle "
            f"inserted in between as the story so far ({shape.rolled_up} "
            f"scene{plural}) and {shape.summaries} scene summaries"
        )
    elif shape.summaries:
        # Case 3: the covered middle rides as its scene summaries.
        lines.append(
            f"  {shape.head} head + {shape.tail} tail verbatim, plus {shape.middle} middle "
            f"inserted in between as {shape.summaries} scene summaries"
        )
    else:
        # Cases 1-2: a short story, or nothing covering the middle —
        # everything verbatim.
        lines.append(f"  {shape.kept} messages verbatim")
    if shape.tail_target < shape.tail_setting:
        # Case 5: the tail stepped down so the context could fit (a
        # case-6 refusal never reaches this report — the preview refuses
        # with the same sentence the turn would).
        lines.append(
            f"  the tail aims at {shape.tail_target} messages instead of the configured "
            f"{shape.tail_setting}, so the context fits the limit"
        )
    return "\n".join(lines)


def _preview_body(text: str, recap: str) -> list[str]:
    """Content lines for the preview. Blank lines are dropped to keep it
    tight, EXCEPT in the turn carrying the recap, where paragraph breaks
    are load-bearing: they separate one scene summary from the next (and
    the last summary from any message text merged in after it)."""
    if recap and recap in text:
        out: list[str] = []
        for line in text.splitlines():
            if line.strip():
                out.append(line)
            elif out and out[-1] != "":
                out.append("")  # collapse runs, keep one
        return out
    return [line for line in text.splitlines() if line.strip()]
