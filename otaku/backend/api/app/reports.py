"""The read-only reports both frontends show on the installation: the
tokens spent (`UsageResource`), what otaku knows of the model and the
run (`InfoResource`), and what each cloud account has left
(`BalanceResource`); a story's next request is the story's
(`api.story.context`).

What a resource answers is the report: the facts it is made of, and
`text()` — the same facts as a terminal pages them. A frontend that
PRINTS asks for the text; one that DRAWS (a table, a definition list,
the window diagram) takes the facts and draws them. Neither invents a
fact, neither rewords a sentence, and the two cannot drift because the
text is rendered from the facts beside it. The facts are gathered by the
domain (`domain.app.reports`).
"""

from __future__ import annotations

from dataclasses import dataclass

from otaku.backend.domain.app import App
from otaku.backend.domain.app import reports as domain_reports
from otaku.backend.errors import NO_MODEL_HINT
from otaku.formatting import (
    Money,
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


# What /usage can count: the argument a frontend passes, and what the
# report calls that scope. Declared here because both are the report's
# own language — the terminal takes the argument on the command line,
# and the page draws one tab per row.
USAGE_SCOPES: tuple[tuple[str, str], ...] = (("", "this story"), ("all", "all stories"))


class UsageResource:
    """/usage — the tokens spent."""

    def __init__(self, app: App) -> None:
        self._app = app

    def get(self, *, story_id: int | None = None) -> UsageReport:
        """Tokens spent on a story — on every story where none is named.
        Grouped by what the tokens were spent on (chat, lore, …), then by
        provider and model. Raises NotFound when there is nothing to
        report."""
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
            for row in domain_reports.get_usage(self._app, story_id)
        )
        return UsageReport(
            scope=dict(USAGE_SCOPES)["all" if story_id is None else ""],
            rows=rows,
            requests=sum(row.requests for row in rows),
            prompt_tokens=sum(row.prompt_tokens for row in rows),
            completion_tokens=sum(row.completion_tokens for row in rows),
            cached_tokens=sum(row.cached_tokens for row in rows),
        )


class InfoResource:
    """/info — what otaku knows of the model and the run."""

    def __init__(self, app: App) -> None:
        self._app = app

    def get(self, *, story_id: int | None = None) -> InfoReport:
        """Everything otaku knows about the active model and this run, in
        blocks, best-effort: network-backed fields are silently skipped.
        Without a model only that block is missing."""
        state, model, rows = domain_reports.get_info(self._app, story_id)
        return InfoReport(
            (
                InfoSection(rows=state),
                InfoSection(rows=model) if model is not None else InfoSection(note=NO_MODEL_HINT),
                InfoSection(rows=rows),
            )
        )


class BalanceResource:
    """/balance — what each cloud account has left."""

    def __init__(self, app: App) -> None:
        self._app = app

    def get(self, *, probe: bool = True) -> BalanceReport:
        """Every provider with an account to bill, and what each says it
        has left — EVERY one, a row with a dash where there is no figure,
        since that row is usually the one the reader came to look at.
        `probe=False` asks no network at all and returns the ROSTER —
        each keyed row with no figure and an EMPTY note, meaning "not
        asked yet": the page paints the whole slip from it and fills the
        figures from one probed report. Raises NotFound with no cloud
        providers."""
        rows, spending = domain_reports.get_balance(self._app, probe=probe)
        return BalanceReport(
            tuple(Balance(row.provider, row.label, row.money, row.note) for row in rows), spending
        )


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
class InfoReport:
    """Everything otaku knows about this run, in blocks."""

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


@dataclass(frozen=True)
class InfoSection:
    """One block of `info`: labelled facts, or the one sentence that
    stands where the facts would be (no model configured)."""

    rows: tuple[tuple[str, str], ...] = ()
    note: str = ""


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
