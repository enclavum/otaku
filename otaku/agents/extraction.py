"""Lore extraction: one pass distilling played messages into memory.

An `Extractor` binds one story to one model plus the channels a pass
reports through; `run` owns the whole pass in order: gate, scene closes,
rollups. The unextracted tail of the story — everything after the last
current scene — is planned into scenes (`plan_scenes`): no scene crosses
a chapter, each meeting both `min_chars` of body text and `min_messages`
messages, cut as evenly as the sizes let, none closing over the newest
`settle` messages; the chapter in play keeps its newest scene open, so
the end of a chapter never leaves a stub. Per span, one completion
(the extract template) closes a scene: a narrative summary, new
characters joining the cast, one journal row per character present, and
speaker labels filled onto unattributed in-character rows. The journal
row is a CONTRACT, not enrichment: one per character PRESENT in the
scene, silent bystanders included, entries naming arrivals and
departures — a journal row asserts presence, and a character's
perspective on the summarized past derives from nothing else. The
rollups then bring every history up to date — the story-so-far on the
newest scene, each active character's history on their newest journal
row; a single-source rollup is its source verbatim, no model pass. Both
self-gate on "newest row lacks a history", which is also what heals a
rollup nulled by an edit (or lost to a cancel) on the next idle,
whether or not a scene closes.

Cancellation is an Event checked at every step: once set, the in-flight
completion stops mid-stream, nothing more is written, and a
half-extracted scene never commits. A pass decrypts its unextracted
tail alone, to weigh and close it — never the whole story. `progress` (a
status line) may carry names; `log` (the system log) stays
content-free: ids and counts, never prose.

Every row of the numbered scene goes to the analysis model with its
stored template composed, so `/me`, `/you`, and `/ooc` turns show their
`((OOC: …))` enclosure as stored. A REPLY to an /ooc line is enclosed
here instead: it is the one out-of-character row with nothing of its
own to mark it — the line it answers carries the enclosure in its
template, while a reply has no template at all — and the extract
template reads "out of character" off that shape, so unmarked it would
be read as something that happened in the scene. Out-of-character rows
are mined for decisions but never speaker-attributed and never part of
the scene's story. A tool call in a body (`context.tool_calls`: any
fenced block marked `otk-NAME`) is the model's own aside, not the
scene: it is left out, fences and all, before
a row is measured by the gate, packed into a span, or numbered for the
analysis model. A picture the reader attached IS part of the scene, and
the pass is the one moment its content can be put into words that last
— a summarized row loses its picture — so each is marked `(picture n)`
at its line and, while the model can see, every one rides the request
in that order; the template asks for what matters in them.
"""

import builtins
import enum
import json
import threading
import time
from bisect import bisect_left
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Self

from otaku.context import tool_calls
from otaku.context.assembler import WirePicture, WireTurn
from otaku.context.syntax import OOC_FRAME, to_wire
from otaku.formatting import format_duration, render
from otaku.providers import (
    OpenAIClient,
    PicturesRide,
    ProviderError,
    Stats,
    StatusError,
    Text,
    UnreachableError,
    WireMessage,
)
from otaku.store import Store
from otaku.store.ops.lore import CharacterMemory
from otaku.store.schema import Message

_TIMEOUT = 600.0
# Caps runaway JSON repetition loops. Sized so a large-cast scene — a
# 250-400-word summary, ~100 speaker labels, and a journal entry for
# everyone present — still fits; a truncated reply fails the parse and
# loses the whole scene's extraction.
_MAX_TOKENS = 8_192
# A background completion is idempotent and unwatched, so a transient
# failure is retried before giving up: a transport failure (the server
# dropping the stream mid-body, a lost socket, a read timeout) — the
# usual cause is a passing collision for the one model, gone by the time
# the backoff elapses — and the two statuses that say "not now": a rate
# limit (429) and an engine busy with one request (503; KoboldCpp serves
# one at a time, and a user's turn collides with the pass routinely).
# The wait is the server's own where it named one (`Retry-After`),
# capped, else the backoff. Any other status is not transient and is
# never retried.
_ATTEMPTS = 3
_BACKOFF_SECONDS = 1.0
_TRANSIENT_STATUSES = frozenset({429, 503})
_RETRY_AFTER_CAP = 60.0  # the longest a pass waits on the server's word

# Null-object cancel: callers pass a real Event or nothing; normalizing to
# a never-set Event deletes the `is not None` guard at every check site.
_NEVER_CANCELLED = threading.Event()

_NOT_A_NAME = frozenset({"null", "none", "narrator", "narration", "user", "assistant"})


@dataclass(frozen=True)
class ExtractionSettings:
    """What one pass runs under — the templates and the gates, bundled
    so they travel together (a Job field, an Extractor argument)."""

    extract_template: str
    scene_history_template: str
    journal_history_template: str
    settle: int
    min_chars: int
    min_messages: int


class PassResult(enum.Enum):
    NO_STORY = "nothing to extract from"
    TOO_SHORT = "not enough new play yet"
    CANCELLED = "cancelled"
    FAILED = "extraction failed"  # didn't parse, or the request errored
    CLOSED = "scene closed"


@dataclass
class Report:
    """What one pass wrote — every counter the callers report from."""

    scenes: int = 0
    journals: int = 0  # journal entries written
    histories: int = 0  # character history rollups rebuilt
    scene_histories: int = 0  # story-so-far rollups rebuilt
    characters: list[str] = field(default_factory=list)  # new cast members
    attributed: int = 0  # messages given a speaker by the extraction
    skipped: int = 0  # malformed extraction items dropped
    last_scene_title: str = ""  # the newest closed scene's title, for the report


@dataclass(frozen=True)
class _Plan:
    """The scenes one pass would close, as runs of the tail's rows, and
    the measure of what is settled — for the log, when it closes none."""

    spans: list[list[Message]]
    chars: int
    messages: int


class _Cast:
    """Name → character-id resolution (case-insensitive, alias-aware),
    creating unknown characters on first mention — made by the pass's
    `provider` and `model`."""

    def __init__(self, store: Store, story_id: int, provider: str, model: str) -> None:
        self._store = store
        self._story_id = story_id
        self._provider = provider
        self._model = model
        self._id_by_name: dict[str, int] = {}
        self._name_by_id: dict[int, str] = {}

    @classmethod
    def load(cls, store: Store, story_id: int, provider: str, model: str) -> Self:
        """A cast seeded with the story's existing characters, so the pass
        resolves against the live cast instead of starting empty."""
        cast = cls(store, story_id, provider, model)
        for character in store.characters.list(story_id):
            cast._id_by_name[character.name.casefold()] = character.id
            for alias in character.aliases:
                cast._id_by_name.setdefault(alias.casefold(), character.id)
            cast._name_by_id[character.id] = character.name
        return cast

    def get_or_add(
        self,
        name: str,
        *,
        aliases: tuple[str, ...] = (),
        description: str | None = None,
    ) -> int:
        """The character's id, creating the row on first mention."""
        name = name.strip()
        found = self.resolve(name)
        if found is not None:
            # A speaker label creates the row bare, before the extraction's
            # `characters` entry arrives — enrich it rather than dropping
            # the aliases and description that entry carries.
            if aliases or description:
                self._store.characters.update(found, aliases=aliases, description=description)
                for alias in aliases:
                    self._id_by_name.setdefault(alias.strip().casefold(), found)
            return found
        cid = self._store.characters.add(
            self._story_id,
            name,
            aliases=aliases,
            description=description,
            provider=self._provider,
            model=self._model,
        )
        self._id_by_name[name.casefold()] = cid
        for alias in aliases:
            self._id_by_name.setdefault(alias.strip().casefold(), cid)
        self._name_by_id[cid] = name
        return cid

    def names(self) -> builtins.list[str]:
        return sorted(self._name_by_id.values(), key=str.casefold)

    def get_name(self, cid: int) -> str:
        return self._name_by_id.get(cid, "?")

    def resolve(self, name: str) -> int | None:
        return self._id_by_name.get(name.strip().casefold())

    def prompt_block(self) -> str:
        rows = [f"- {name}" for name in self.names()]
        return "\n".join(rows) if rows else "(none yet)"


class Extractor:
    """One story bound to one model, plus the reporting channels — so the
    steps don't each thread six arguments. `run` is the whole pass;
    `complete` is the one-completion step the warm-up reuses."""

    def __init__(
        self,
        store: Store,
        client: OpenAIClient,
        model: str,
        story_id: int,
        *,
        extraction_settings: ExtractionSettings,
        cancel: threading.Event | None = None,
        progress: Callable[[str], None] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._store = store
        self._client = client
        self._model = model
        self._story_id = story_id
        self._settings = extraction_settings
        self._cancel = cancel or _NEVER_CANCELLED
        self._progress = progress or (lambda line: None)
        self._log = log or (lambda line: None)
        # Whether a request reached for the model — what tells the worker
        # the pass may have loaded it on the engine.
        self.asked = False

    def run(self, *, force: bool = False) -> tuple[PassResult, Report]:
        """One pass over the story's unextracted tail: scene closes, then
        the rollups — which run whatever the gate said, so an invalidated
        or missing history heals on the next idle. The gates come from
        the extraction settings; `force` (the manual close) closes all
        of the tail, the settle margin and the scene held open dropped —
        the spans still pack to the configured minimums."""
        report = Report()
        if not self._store.stories.exists(self._story_id):
            return PassResult.NO_STORY, report
        plan = self._plan(force)
        if not plan.spans:
            settings = self._settings
            self._log(
                f"extraction idle (story {self._story_id}): {plan.chars} settled chars in "
                f"{plan.messages} messages close no scene yet (one takes {settings.min_chars} "
                f"in {settings.min_messages}, and the newest stays open)"
            )
            result = PassResult.TOO_SHORT
        else:
            result = self._close_scenes(plan.spans, report)
            if result in (PassResult.CANCELLED, PassResult.FAILED):
                return result, report

        # Deliberately unconditional: these refresh previously NOT refreshed
        # rollups, if any — nulled by a lore edit, or lost to a failure or
        # cancel — so they heal on the next pass whatever the gate said. They
        # self-gate on id-only queries, so a current story costs nothing.
        self._refresh_scene_history(report)
        self._refresh_histories(report)
        return result, report

    def complete(
        self,
        prompt: str | Sequence[WireMessage],
        purpose: str,
        *,
        params: dict[str, object] | None = None,
        timeout: float = _TIMEOUT,
    ) -> str:
        """One completion, accumulated silently; usage recorded against
        the story under `purpose`. A str prompt wraps into a WireTurn —
        never a stored Message: only wire types travel here, so the
        warm-up's assembled turns arrive as they are. Cancelled
        mid-stream → "" and nothing recorded; a transient failure — a
        transport failure, a rate limit, a busy engine — retries with a
        cancel-aware wait, the server's own where it named one; any
        other status propagates on the first try."""
        messages = [WireTurn(role="user", body=prompt)] if isinstance(prompt, str) else prompt
        last_exc: ProviderError | None = None
        for attempt in range(_ATTEMPTS):
            if self._cancel.is_set():
                return ""
            try:
                return self._stream_once(messages, purpose, params, timeout)
            except (UnreachableError, StatusError) as e:
                if isinstance(e, StatusError) and e.status not in _TRANSIENT_STATUSES:
                    raise
                last_exc = e
                more = attempt + 1 < _ATTEMPTS
                wait = _BACKOFF_SECONDS * (attempt + 1)
                if isinstance(e, StatusError):
                    what = f"refused ({e.status})"
                    if e.retry_after is not None:
                        wait = min(e.retry_after, _RETRY_AFTER_CAP)
                else:
                    what = f"dropped ({type(e).__name__})"
                if more:
                    self._log(
                        f"{purpose} request {what}; retrying ({attempt + 2}/{_ATTEMPTS}) "
                        f"in {wait:.0f}s"
                    )
                # A set event ends the backoff early — same as a cancel.
                if more and self._cancel.wait(wait):
                    return ""
        assert last_exc is not None  # the loop reaches here only via except
        self._log(f"{purpose} request failed after {_ATTEMPTS} attempts: {type(last_exc).__name__}")
        raise last_exc

    # ---------- the pass internals ----------

    def _plan(self, force: bool) -> _Plan:
        """The scenes this pass would close over the unextracted tail —
        everything after the last current scene but the card rows: a scene
        about a character sheet is not a scene, and one import would clear
        `min_chars` alone; their retention is the assembler's business.
        The settle margin counts the newest of the rest, a tool's turn
        among them; the scenes are planned over all but the tools' turns,
        which are not the scene (`plan_scenes`)."""
        settings = self._settings
        ids = self._store.stories.get_messages_ids(self._story_id)
        last_end = max(self._store.scenes.get_current_ends(self._story_id, ids), default=None)
        tail_ids = ids if last_end is None else [i for i in ids if i > last_end]
        tail = [m for m in self._store.messages.list(tail_ids) if m.kind != "card"]
        settling = 0 if force else settings.settle
        settled_ids = {m.id for m in tail[: max(0, len(tail) - settling)]}
        rows = [m for m in tail if m.role != "tool"]
        sizes = [len(tool_calls.strip(m.body)) for m in rows]
        settled = sum(1 for m in rows if m.id in settled_ids)
        spans = plan_scenes(
            sizes,
            [i for i, m in enumerate(rows) if m.is_scene_start],
            settled=settled,
            min_chars=settings.min_chars,
            min_messages=settings.min_messages,
            force=force,
        )
        return _Plan([rows[a:b] for a, b in spans], chars=sum(sizes[:settled]), messages=settled)

    def _close_scenes(self, spans: list[list[Message]], report: Report) -> PassResult:
        """Close the planned spans, a scene each, in order (`_plan`); the
        spans' journals feed each next extraction, so the story stays
        continuous across them."""
        # Message NUMBER = 1-based position on the chain (what the resume
        # line counts), so the progress line can name the span's range; the
        # same read tells a story that moved since the plan was made (an
        # undo on the frontend thread): this pass is stale, and the next
        # one sees the new chain.
        number = {
            mid: i for i, mid in enumerate(self._store.stories.get_messages_ids(self._story_id), 1)
        }
        if any(m.id not in number for span in spans for m in span):
            self._log(f"extraction skipped (story {self._story_id}): the story changed mid-pass")
            return PassResult.CANCELLED
        cast = _Cast.load(self._store, self._story_id, self._client.id, self._model)
        pass_started = time.monotonic()
        self._log(
            f"extraction started (story {self._story_id}): "
            f"{sum(len(span) for span in spans)} messages in {len(spans)} span(s)"
        )
        for k, span in enumerate(spans, 1):
            if self._cancel.is_set():
                self._log(
                    f"extraction cancelled (story {self._story_id}) "
                    f"({format_duration(time.monotonic() - pass_started)})"
                )
                return PassResult.CANCELLED
            span_range = f"{number[span[0].id]} - {number[span[-1].id]}"
            part = f" ({k}/{len(spans)})" if len(spans) > 1 else ""
            # If some spans already closed, the failure notes say so — "the
            # tail stays open" would read as if the whole pass was lost.
            kept = f"{report.scenes} scene(s) closed, the rest of " if report.scenes else ""
            self._log(
                f"scene close started (story {self._story_id}): "
                f"{len(span)} messages ({span_range}){part}"
            )
            self._progress(f"closing a scene over {len(span)} messages ({span_range}){part}…")
            journals_before = report.journals
            scene_started = time.monotonic()
            try:
                closed = self._close_scene(cast, span, "lore", report)
            except ProviderError as e:
                self._log(
                    f"extraction failed (story {self._story_id}): {type(e).__name__} "
                    f"({format_duration(time.monotonic() - pass_started)})"
                )
                self._progress(f"extraction failed ({e}) — {kept}the tail stays open")
                return PassResult.FAILED
            except (ValueError, json.JSONDecodeError) as e:
                # Best-level: the unclosed tail stays unextracted, retried
                # on the next idle. Say why — a model that never returns
                # a usable reply would otherwise build no memory at all,
                # silently forever.
                reason = (
                    "the reply did not parse" if isinstance(e, json.JSONDecodeError) else str(e)
                )
                self._log(
                    f"extraction failed (story {self._story_id}): {reason} "
                    f"({format_duration(time.monotonic() - pass_started)})"
                )
                self._progress(f"extraction failed ({reason}) — {kept}the tail stays open")
                return PassResult.FAILED
            if not closed:  # cancelled mid-stream
                self._log(
                    f"extraction cancelled (story {self._story_id}) "
                    f"({format_duration(time.monotonic() - pass_started)})"
                )
                return PassResult.CANCELLED
            self._log(
                f"scene close finished (story {self._story_id}): "
                f"{len(span)} messages, {report.journals - journals_before} journal entries "
                f"({format_duration(time.monotonic() - scene_started)})"
            )
            # The rollups follow each scene immediately — summary and entries
            # land, then the histories composed from them — so the journals
            # block fed to the NEXT span stays one-history-sized instead of
            # accumulating every entry across a long backlog.
            self._refresh_scene_history(report)
            self._refresh_histories(report)
        self._log(
            f"extraction finished (story {self._story_id}): {report.scenes} scene(s), "
            f"{report.journals} journal entries, {report.histories} history rollup(s) "
            f"({format_duration(time.monotonic() - pass_started)})"
        )
        return PassResult.CLOSED

    def _close_scene(
        self, cast: _Cast, span: Sequence[Message], purpose: str, report: Report
    ) -> bool:
        """Extract ONE scene over `span` and apply it — the single
        per-scene step, so the extraction lives in exactly one place.
        Reads the current journals (feeding each character's story
        forward), renders the numbered scene, completes, parses, and
        writes: characters, the scene row, journals, speakers.

        Returns False when cancelled mid-stream (nothing written). Raises
        `ProviderError` (request failed) or `ValueError` (unparsable
        reply) — the caller decides what that means."""
        chain = self._store.stories.get_messages_ids(self._story_id)
        current = self._store.journals.get_current(self._story_id, chain)
        prompt = render(
            self._settings.extract_template,
            cast=cast.prompt_block(),
            journals=_journals_block(current, cast),
            chunk=numbered_chat(span),
        )
        request = WireTurn(role="user", body=prompt, images=self._scene_pictures(span))
        raw = self.complete([request], purpose)
        if not raw:
            if self._cancel.is_set():
                return False  # cancelled mid-stream
            raise ValueError("the reply was empty")
        data = _parse_json(raw)
        self._apply_scene(cast, data, span, current, report)
        report.scenes += 1
        return True

    def _apply_scene(
        self,
        cast: _Cast,
        data: dict[str, object],
        span: Sequence[Message],
        current: dict[int, CharacterMemory],
        report: Report,
    ) -> None:
        """Write one extraction into the store: new characters, the scene
        row, the journals (state carries forward when the extraction omits
        it), and speaker labels — fill-only, never onto an attributed or
        ooc row."""
        scene = data.get("scene")
        scene_data = scene if isinstance(scene, dict) else {}
        summary = _as_str(scene_data.get("summary"))
        if not summary:
            # A committed scene without a summary would swallow its span:
            # not in the head, not in the tail, not in the recap. Refuse
            # the whole extraction BEFORE anything is written.
            raise ValueError("the reply held no scene summary")

        for item in _as_list(data.get("characters")):
            if not isinstance(item, dict):
                continue
            name = _as_str(item.get("name"))
            if name is None:
                report.skipped += 1
                continue
            aliases = tuple(a for a in (_as_str(x) for x in _as_list(item.get("aliases"))) if a)
            if cast.resolve(name) is None:
                report.characters.append(name)
            cast.get_or_add(name, aliases=aliases, description=_as_str(item.get("description")))

        scene_id = self._store.scenes.add(
            self._story_id,
            start_message_id=span[0].id,
            end_message_id=span[-1].id,
            title=_as_str(scene_data.get("title")),
            summary=summary,
            provider=self._client.id,
            model=self._model,
        )
        report.last_scene_title = _as_str(scene_data.get("title")) or ""

        written: set[int] = set()
        for item in _as_list(data.get("journals")):
            if not isinstance(item, dict):
                continue
            name = _as_str(item.get("character"))
            entry = _as_str(item.get("entry"))
            state = _as_str(item.get("state"))
            if name is None or (entry is None and state is None):
                report.skipped += 1
                continue
            cid = cast.get_or_add(name)
            if cid in written:  # one row per (scene, character) — see schema
                report.skipped += 1
                continue
            written.add(cid)
            prev = current.get(cid)
            self._store.journals.add(
                self._story_id,
                scene_id,
                cid,
                # An entry describes one scene, so a missing one stays
                # empty rather than repeating the previous scene's; state
                # carries forward, since it is a standing snapshot.
                entry=entry or "",
                state=state or (prev.state if prev else ""),
                provider=self._client.id,
                model=self._model,
            )
            report.journals += 1

        by_number = dict(enumerate(span, 1))
        for n, name in _speakers(data).items():
            row = by_number.get(n)
            if row is None or row.speaker is not None or row.kind == "ooc":
                continue
            cid = cast.get_or_add(name)
            self._store.messages.set_speaker(row.id, cid, name)
            report.attributed += 1

    def _refresh_scene_history(self, report: Report) -> None:
        """Rebuild the history of every current scene that lacks one — the
        story so far THROUGH that scene, which is all "the arc" ever is:
        composed from the summaries up to and including its own, never
        from the previous rollup (a photocopy of a photocopy). Oldest
        first and best-effort per scene — a failure leaves that row NULL
        and the next pass retries — so the arc exists on every scene once
        a pass has caught up."""
        ids = self._store.stories.get_messages_ids(self._story_id)
        due = self._store.scenes.get_rollups_due(self._story_id, ids)
        if not due or self._cancel.is_set():
            return
        scenes = self._store.scenes.get_current(self._story_id, ids)
        position = {s.id: i for i, s in enumerate(scenes)}
        for scene_id in due:
            if self._cancel.is_set():
                return
            index = position.get(scene_id)
            if index is None:
                continue
            no = index + 1
            summaries = [s.summary for s in scenes[:no] if s.summary]
            if not summaries:
                continue
            if len(summaries) == 1:
                # The rollup of one summary is that summary: a model pass
                # over a single source adds nothing and loses detail to
                # paraphrase.
                self._store.scenes.set_history(scene_id, summaries[0])
                report.scene_histories += 1
                self._log(
                    f"story-so-far rollup finished (story {self._story_id}, scene {no}): "
                    f"the one summary, verbatim"
                )
                continue
            self._progress(
                f"composing the story so far through scene {no} ({len(summaries)} summaries)…"
            )
            started = time.monotonic()
            self._log(
                f"story-so-far rollup started (story {self._story_id}, scene {no}): "
                f"{len(summaries)} summaries"
            )
            try:
                story_so_far = self.complete(
                    render(self._settings.scene_history_template, summaries="\n\n".join(summaries)),
                    "rollup",
                ).strip()
            except ProviderError as e:
                # A decline is skipped like a transport failure: the row
                # stays NULL and the next pass tries again — best-level,
                # never the whole pass.
                self._log(
                    f"story-so-far rollup failed (story {self._story_id}, scene {no}): "
                    f"{type(e).__name__} ({format_duration(time.monotonic() - started)})"
                )
                self._progress(f"story-so-far rollup failed ({e})")
                continue
            if self._cancel.is_set():
                return
            if not story_so_far:
                continue
            self._store.scenes.set_history(scene_id, story_so_far)
            report.scene_histories += 1
            self._log(
                f"story-so-far rollup finished (story {self._story_id}, scene {no}): "
                f"{len(summaries)} summaries ({format_duration(time.monotonic() - started)})"
            )

    def _refresh_histories(self, report: Report) -> None:
        """Rebuild the history of every character whose newest journal row
        lacks one — those active in the just-closed scene, plus any whose
        rollup an edit invalidated. Composed from the character's own
        entries, never from the previous history. Best-effort per
        character."""
        names = {c.id: c.name for c in self._store.characters.list(self._story_id)}
        chain = self._store.stories.get_messages_ids(self._story_id)
        for character_id, journal_id in self._store.journals.get_rollups_due(self._story_id, chain):
            if self._cancel.is_set():
                return
            entries = self._store.journals.get_entries(self._story_id, character_id, chain)
            if not entries:
                continue
            if len(entries) == 1:
                # One entry: the history IS that entry, verbatim — no model
                # pass, no paraphrase drift, the language trivially kept.
                self._store.journals.set_history(journal_id, entries[0])
                report.histories += 1
                self._log(
                    f"history rollup finished (story {self._story_id}, "
                    f"character {character_id}): the one entry, verbatim"
                )
                continue
            name = names.get(character_id, "?")
            self._progress(f"rebuilding {name}'s history from {len(entries)} entries…")
            rollup_started = time.monotonic()
            self._log(
                f"history rollup started (story {self._story_id}, "
                f"character {character_id}): {len(entries)} entries"
            )
            prompt = render(
                self._settings.journal_history_template,
                name=name,
                entries="\n\n".join(f"{n}. {text}" for n, text in enumerate(entries, 1)),
            )
            try:
                text = self.complete(prompt, "rollup")
            except ProviderError as e:
                # A decline is skipped like a transport failure: the row
                # stays NULL and the next pass tries again.
                self._log(
                    f"history rollup failed (story {self._story_id}, "
                    f"character {character_id}): {type(e).__name__} "
                    f"({format_duration(time.monotonic() - rollup_started)})"
                )
                continue
            if text.strip() and not self._cancel.is_set():
                self._store.journals.set_history(journal_id, text.strip())
                report.histories += 1
                self._log(
                    f"history rollup finished (story {self._story_id}, "
                    f"character {character_id}): {len(entries)} entries "
                    f"({format_duration(time.monotonic() - rollup_started)})"
                )

    def _scene_pictures(self, span: Sequence[Message]) -> tuple[WirePicture, ...]:
        """The scene's pictures as the request carries them, every one, in
        the order `numbered_chat` numbers them — read from the folder while
        the model can see. None for a model that cannot: the markers alone
        say a picture was shown. A file that is gone is skipped."""
        if self._client.pictures_ride(self._model) is PicturesRide.NONE:
            return ()
        named = [a for item in span for a in item.attachments]
        found = (self._store.files.get(a.file) for a in named)
        return tuple(WirePicture(*f) for f in found if f is not None)

    def _stream_once(
        self,
        messages: Sequence[WireMessage],
        purpose: str,
        params: dict[str, object] | None,
        timeout: float,
    ) -> str:
        """One attempt of `complete`: stream, accumulate, record usage.
        Raises the provider error so the wrapper can retry."""
        self.asked = True
        buf: list[str] = []
        final: Stats | None = None
        # max_tokens bounds a repetition loop — without it a looping model
        # generates until someone kills it (streaming resets the read
        # timeout).
        params = params or {"temperature": 0.2, "max_tokens": _MAX_TOKENS}
        stream = self._client.completion.chat(
            self._model,
            messages,
            params,
            level="none",
            purpose=purpose,
            timeout=timeout,
            watched=False,  # accumulated into a string; nobody watches it
            # Ticked while the reply is waited on, so a cancel — the
            # user typing, the shutdown — cuts a prefill in flight rather
            # than waiting out the engine's next word.
            on_idle=lambda: not self._cancel.is_set(),
        )
        try:
            for chunk in stream:
                if self._cancel.is_set():
                    return ""  # finally closes the stream → server stops
                if isinstance(chunk, Text):
                    buf.append(chunk.text)
                elif isinstance(chunk, Stats):
                    final = chunk
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
        if self._cancel.is_set():
            return ""  # cut in the wait: what came is no answer
        if final is not None:
            self._store.usage.record(
                self._client.config.name,
                self._model,
                purpose,
                story_id=self._story_id,
                prompt_tokens=final.prompt_tokens,
                completion_tokens=final.completion_tokens,
                cached_tokens=final.cached_tokens,
                duration_seconds=final.total_seconds,
            )
        return "".join(buf)


def plan_scenes(
    sizes: Sequence[int],
    scene_starts: Sequence[int],
    *,
    settled: int,
    min_chars: int,
    min_messages: int,
    force: bool = False,
) -> list[tuple[int, int]]:
    """The scenes one pass closes over the unextracted tail, as [start,
    end) spans in order — `sizes` its rows' (each at least 0),
    `scene_starts` the rows a scene must begin at (a chapter's heading;
    one outside the tail is ignored), the first `settled` rows past the
    settle margin (0 to all of them); the minimums are at least 1. No
    scene crosses a start: the tail is cut before each, and each part
    packs on its own (`pack`); a start at 0 needs no cut, but marks row 0
    as a heading. A part that has ended closes whole once its last row
    is settled — a short one as one short scene; the part in play closes
    its settled rows but the newest span, held open, so a chapter ending
    never leaves a stub behind. `force` (the manual close) closes
    everything but a heading alone — a part that is nothing but its
    heading — which never closes a scene."""
    starts = set(scene_starts)
    bounds = [0, *sorted(i for i in starts if 0 < i < len(sizes)), len(sizes)]
    out: list[tuple[int, int]] = []
    for index in range(len(bounds) - 1):
        start, end = bounds[index], bounds[index + 1]
        in_play = index == len(bounds) - 2
        ready_end = end if force else min(end, settled)  # what may close now ends here
        if not in_play and ready_end < end:
            break  # an ended part waits to be settled whole
        empty = ready_end <= start
        heading_alone = start in starts and ready_end - start == 1
        if empty or heading_alone:
            continue
        spans = pack(sizes[start:ready_end], min_chars=min_chars, min_messages=min_messages)
        if in_play and not force:
            spans = spans[:-1]
        out += [(start + a, start + b) for a, b in spans]
    return out


def pack(sizes: Sequence[int], *, min_chars: int, min_messages: int) -> list[tuple[int, int]]:
    """Cut item sizes into contiguous [start, end) spans, each meeting BOTH
    minimums — `min_chars` of content and `min_messages` items — as many
    as the minimums allow, cut as evenly by size as they let: 59 like
    items at 20 a span are 29 and 30, never 20 and 39. Items under the
    minimums are one span all the same."""
    items = len(sizes)
    if not items:
        return []
    totals = [0]
    for size in sizes:
        totals.append(totals[-1] + size)

    def meets(start: int, end: int) -> bool:
        return end - start >= min_messages and totals[end] - totals[start] >= min_chars

    # The spans fitted from the end, each starting as late as it may:
    # `latest[j]` is where the (j+1)-th span from the end starts, so what
    # follows a cut at or before it still holds j+1 spans.
    latest: list[int] = []
    end = items
    for start in range(items - 1, -1, -1):
        if meets(start, end):
            latest.append(start)
            end = start
    parts = len(latest)
    if parts <= 1:
        return [(0, items)]
    out = []
    start = 0
    for i in range(1, parts):
        # this span meets both minimums, and what follows still holds the rest
        low = max(start + min_messages, bisect_left(totals, totals[start] + min_chars))
        high = latest[parts - i - 1]
        # the cut nearest an even share of the size, between those bounds,
        # the earliest of equally near ones (empty items make ties)
        target = totals[items] * i / parts
        at = bisect_left(totals, target, low, high + 1)
        if at > high or (at > low and target - totals[at - 1] <= totals[at] - target):
            at -= 1
        at = bisect_left(totals, totals[at], low, at + 1)
        out.append((start, at))
        start = at
    out.append((start, items))
    return out


def numbered_chat(span: Sequence[Message]) -> str:
    """The numbered scene block for the extract template — the one owner
    of the `[n] Speaker: …` format. A tool call is left out first; a
    row that empties keeps its number, since the speaker labels come
    back by number.

    A row is composed for the wire FIRST and decorated after, with the
    things the analysis model needs and the wire must never carry: a
    `(picture n)` mark for each picture the row carried, numbered through
    the scene in the order the request attaches them; the speaker on an
    attributed line; and the `((OOC: …))` enclosure on a reply to an /ooc
    line. The order matters — decorating first would hide the body's own
    syntax from the composer, which reads it to strip a direction and
    fill its template."""
    lines: list[str] = []
    pictures = 0
    for n, item in enumerate(span, 1):
        # is_last=False always: a cue steers one reply, it is not something
        # that happened in the scene.
        body = tool_calls.strip(item.body)
        text = to_wire(replace(item, body=body), is_last=False)
        if item.attachments:
            marks = " ".join(
                f"(picture {pictures + i})" for i in range(1, len(item.attachments) + 1)
            )
            pictures += len(item.attachments)
            text = f"{marks} {text}".rstrip()
        if item.kind == "ooc" and item.role == "assistant":
            text = OOC_FRAME.replace("{body}", text)
        elif item.speaker and text:
            text = f"{item.speaker}: {text}"
        lines.append(f"[{n}] {text}")
    return "\n".join(lines)


def _parse_json(text: str) -> dict[str, object]:
    """Parse the extraction reply: tolerate code fences and surrounding
    prose by slicing from the first '{' to the last '}' — and JSON whose
    syntax was typed with typographic double quotes (a small model
    mirrors the story's own punctuation into `“summary”: “…”`) by
    retrying with them straightened. Only the retry touches them: a reply
    that parses keeps every curly quote INSIDE its values verbatim."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in reply")
    sliced = text[start : end + 1]
    try:
        obj = json.loads(sliced)
    except json.JSONDecodeError:
        obj = json.loads(sliced.replace("“", '"').replace("”", '"'))
    if not isinstance(obj, dict):
        raise ValueError("extraction reply is not a JSON object")
    return obj


def _journals_block(current: dict[int, CharacterMemory], cast: _Cast) -> str:
    """The current journals rendered for the extract template — the
    feedback loop that keeps each character's story continuous across
    scenes. History plus the entries it doesn't cover yet is the whole
    story as they know it, with no gap and no overlap."""
    parts: list[str] = []
    for cid, memory in sorted(current.items()):
        lines = [f"{cast.get_name(cid)}:"]
        if memory.history:
            lines.append(f"  so far: {memory.history}")
        lines.extend(f"  then: {entry}" for entry in memory.entries)
        lines.append(f"  now: {memory.state}")
        parts.append("\n".join(lines))
    return "\n".join(parts) if parts else "(none yet)"


def _speakers(data: dict[str, object]) -> dict[int, str]:
    """n → character name from the extraction's `speakers` labels;
    non-names (null/narrator/the role tags echoed back) are dropped."""
    out: dict[int, str] = {}
    for item in _as_list(data.get("speakers")):
        if not isinstance(item, dict):
            continue
        n = item.get("n")
        name = _as_str(item.get("speaker"))
        if isinstance(n, int) and name and name.casefold() not in _NOT_A_NAME:
            out[n] = name
    return out


def _as_str(obj: object) -> str | None:
    return obj.strip() if isinstance(obj, str) and obj.strip() else None


def _as_list(obj: object) -> list[object]:
    return obj if isinstance(obj, list) else []
