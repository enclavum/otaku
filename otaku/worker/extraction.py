"""Lore extraction: one pass distilling played messages into memory.

An `Extractor` binds one story to one model plus the channels a pass
reports through; `run` owns the whole pass in order: gate, scene closes,
rollups. The unextracted tail of the story — everything after the last
current scene, minus the newest `settle` messages — must hold both
`min_chars` of body text and `min_messages` messages; a long backlog is
packed into spans each meeting both minimums. Per span, one completion
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
half-extracted scene never commits. The gates run on id-only queries —
the story is decrypted only when a scene actually closes. `progress` (a
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
the scene's story.
"""

import builtins
import enum
import json
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Self

from otaku.context.assembler import WireTurn
from otaku.context.syntax import OOC_FRAME, to_wire
from otaku.formatting import format_duration, render
from otaku.providers import Client, ProviderError, Stats, Text, UnreachableError, WireMessage
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
# transport failure (the server dropping the stream mid-body, a lost
# socket, a read timeout) is retried before giving up — the usual cause is
# a passing collision for the one model, gone by the time the backoff
# elapses. A bad HTTP status is not transient and is never retried.
_ATTEMPTS = 3
_BACKOFF_SECONDS = 1.0

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


class _Cast:
    """Name → character-id resolution (case-insensitive, alias-aware),
    creating unknown characters on first mention."""

    def __init__(self, store: Store, story_id: int) -> None:
        self._store = store
        self._story_id = story_id
        self._id_by_name: dict[str, int] = {}
        self._name_by_id: dict[int, str] = {}

    @classmethod
    def load(cls, store: Store, story_id: int) -> Self:
        """A cast seeded with the story's existing characters, so the pass
        resolves against the live cast instead of starting empty."""
        cast = cls(store, story_id)
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
            self._story_id, name, aliases=aliases, description=description
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
        client: Client,
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

    def run(self, *, force: bool = False) -> tuple[PassResult, Report]:
        """One pass over the story's unextracted tail: scene closes, then
        the rollups — which run whatever the gate said, so an invalidated
        or missing history heals on the next idle. The gates come from
        the extraction settings; `force` (the manual close) drops the
        gate and the settle margin — the spans still pack to the
        configured minimums."""
        settings = self._settings
        report = Report()
        if not self._store.stories.exists(self._story_id):
            return PassResult.NO_STORY, report

        ids = self._store.stories.get_messages_ids(self._story_id)
        # Card rows never enter a pass: not the char gate (one import would
        # clear `min_chars` alone), not the spans, not the numbered chat —
        # a scene about a character sheet is not a scene. Their retention
        # is the assembler's business.
        cards = set(self._store.stories.get_card_message_ids(self._story_id))
        ids = [i for i in ids if i not in cards]
        ends = self._store.scenes.get_current_ends(self._story_id, ids)
        last_end = max(ends, default=None)
        tail_ids = ids if last_end is None else [i for i in ids if i > last_end]
        if not force and settings.settle > 0:
            tail_ids = tail_ids[: max(0, len(tail_ids) - settings.settle)]
        chars = self._store.messages.count_body_chars(tail_ids)
        too_short = chars < settings.min_chars or len(tail_ids) < settings.min_messages
        if not tail_ids or (not force and too_short):
            self._log(
                f"extraction declined (story {self._story_id}): {chars} settled chars in "
                f"{len(tail_ids)} messages, need {settings.min_chars} in {settings.min_messages}"
            )
            result = PassResult.TOO_SHORT
        else:
            result = self._close_scenes(tail_ids, report)
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
        mid-stream → "" and nothing recorded; transient transport
        failures retry with a cancel-aware backoff; a bad HTTP status
        propagates on the first try."""
        messages = [WireTurn(role="user", body=prompt)] if isinstance(prompt, str) else prompt
        last_exc: UnreachableError | None = None
        for attempt in range(_ATTEMPTS):
            if self._cancel.is_set():
                return ""
            try:
                return self._stream_once(messages, purpose, params, timeout)
            except UnreachableError as e:
                last_exc = e
                more = attempt + 1 < _ATTEMPTS
                if more:
                    self._log(
                        f"{purpose} request dropped ({type(e).__name__}); "
                        f"retrying ({attempt + 2}/{_ATTEMPTS})"
                    )
                # A set event ends the backoff early — same as a cancel.
                if more and self._cancel.wait(_BACKOFF_SECONDS * (attempt + 1)):
                    return ""
        assert last_exc is not None  # the loop reaches here only via except
        self._log(f"{purpose} request failed after {_ATTEMPTS} attempts: {type(last_exc).__name__}")
        raise last_exc

    # ---------- the pass internals ----------

    def _close_scenes(self, tail_ids: list[int], report: Report) -> PassResult:
        """Close the tail as one scene — or several, when it has run long.
        Only now is the story decrypted; the spans' journals feed each next
        extraction, so the story stays continuous across them."""
        settings = self._settings
        chain = self._store.stories.get_messages(self._story_id)
        by_id = {m.id: m for m in chain}
        if any(i not in by_id for i in tail_ids):
            # The story moved between the gate's snapshot and this read (an
            # undo on the frontend thread) — this pass is stale; the next
            # one sees the new chain.
            self._log(f"extraction declined (story {self._story_id}): the story changed mid-pass")
            return PassResult.CANCELLED
        tail = [by_id[i] for i in tail_ids]
        sizes = [len(m.body) for m in tail]
        spans = [
            tail[a:b]
            for a, b in pack(
                sizes, min_chars=settings.min_chars, min_messages=settings.min_messages
            )
        ]
        # Message NUMBER = 1-based position on the chain (what the resume
        # line counts), so the progress line can name the span's range —
        # off the SAME read the staleness check guarded: a second read
        # could see an undo that landed after it.
        number = {m.id: i for i, m in enumerate(chain, 1)}
        cast = _Cast.load(self._store, self._story_id)
        pass_started = time.monotonic()
        self._log(
            f"extraction started (story {self._story_id}): "
            f"{len(tail_ids)} messages in {len(spans)} span(s)"
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
                # Best-effort: the unclosed tail stays unextracted, retried
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
        raw = self.complete(prompt, purpose)
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
                # stays NULL and the next pass tries again — best-effort,
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

    def _stream_once(
        self,
        messages: Sequence[WireMessage],
        purpose: str,
        params: dict[str, object] | None,
        timeout: float,
    ) -> str:
        """One attempt of `complete`: stream, accumulate, record usage.
        Raises the provider error so the wrapper can retry."""
        buf: list[str] = []
        final: Stats | None = None
        # max_tokens bounds a repetition loop — without it a looping model
        # generates until someone kills it (streaming resets the read
        # timeout).
        params = params or {"temperature": 0.2, "max_tokens": _MAX_TOKENS}
        stream = self._client.complete_chat(
            self._model,
            messages,
            params,
            think_level="off",
            purpose=purpose,
            timeout=timeout,
            watched=False,  # accumulated into a string; nobody watches it
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
        if final is not None:
            self._store.usage.record(
                self._client.config.name,
                self._model,
                purpose,
                story_id=self._story_id,
                prompt_tokens=final.prompt_tokens,
                completion_tokens=final.completion_tokens,
                cached_tokens=final.cached_tokens,
                duration_seconds=final.duration_seconds,
            )
        return "".join(buf)


def pack(sizes: list[int], *, min_chars: int, min_messages: int) -> list[tuple[int, int]]:
    """Cut item sizes into contiguous [start, end) spans, each meeting BOTH
    minimums: a span grows until it holds `min_chars` of content AND
    `min_messages` items, then cuts. A leftover under the minimums merges
    into the span before it — every scene meets the minimums, so long
    messages can never produce a handful-of-messages scene."""
    spans: list[tuple[int, int]] = []
    start = 0
    size = 0
    for i, n in enumerate(sizes):
        size += n
        if size >= min_chars and i - start + 1 >= min_messages:
            spans.append((start, i + 1))
            start, size = i + 1, 0
    if start < len(sizes):
        if spans:
            spans[-1] = (spans[-1][0], len(sizes))
        else:
            spans.append((0, len(sizes)))
    return spans


def numbered_chat(span: Sequence[Message]) -> str:
    """The numbered scene block for the extract template — the one owner
    of the `[n] Speaker: …` format.

    A row is composed for the wire FIRST and decorated after, with the two
    things the analysis model needs and the wire must never carry: the
    speaker on an attributed line, and the `((OOC: …))` enclosure on a
    reply to an /ooc line. The order matters — decorating first would hide
    the body's own syntax from the composer, which reads it to strip a
    direction and fill its template."""
    lines: list[str] = []
    for n, item in enumerate(span, 1):
        # is_last=False always: a cue steers one reply, it is not something
        # that happened in the scene.
        text = to_wire(item, is_last=False)
        if item.kind == "ooc" and item.role == "assistant":
            text = OOC_FRAME.replace("{body}", text)
        elif item.speaker and item.body:
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
