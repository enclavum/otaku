"""Lore extraction: one pass distilling played messages into memory.

An `Extractor` binds one story to one model plus the channels a pass
reports through; `run` owns the whole pass, in order: gate, scene closes,
rollups. The unextracted tail of the story — everything after the last
current scene, minus the newest `settle` messages — must hold both
`min_chars` of body text and `min_messages` messages; a long backlog is
packed into spans each meeting both minimums. Per span, one completion
(the `extract_prompt`) closes a scene: a narrative summary, new characters
joining the cast, one journal row per character present, and speaker
labels filled onto unattributed in-character rows. The rollups then bring
every history up to date — the story-so-far on the newest scene, each
active character's history on their newest journal row. Both self-gate on
"newest row lacks a history", which is also what heals a rollup nulled by
an edit (or lost to a cancel) on the next idle, whether or not a scene
closes.

Cancellation: the extractor takes a `threading.Event` (the worker's
shutdown flag); every step checks it, and once set, the in-flight
completion stops mid-stream and nothing more is written — a half-extracted
scene never commits. The gates run on id-only queries — the story is
decrypted only when a scene actually closes. `progress` (a status line)
may carry names; `log` (the system log) stays content-free: ids and
counts, never prose.

Every row of the numbered scene goes to the analysis model with its stored
framing composed, so `/me`, `/you`, and `/ooc` turns show their
`((OOC: …))` enclosure as stored. The one row type with nothing stored to
compose — the assistant's reply to an /ooc, kind `ooc` with no framing —
gets the enclosure added, because the extract prompt reads "out of
character" off that marker. Out-of-character rows are mined for decisions
but never speaker-attributed and never part of the scene's story.
"""

import builtins
import enum
import json
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Self

import httpx

from otaku.formatting import combine_framing
from otaku.providers.base import OpenAIClient, Stats, Text, WireMessage
from otaku.settings.prompts import Prompts
from otaku.store import Store
from otaku.store.lore import CharacterMemory
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

# The extraction model must see an out-of-character turn AS out of
# character; a stored framing already shows the enclosure, a bare ooc row
# (an /ooc reply) gets it here. Analysis-side only — never on the wire.
_OOC_MARK = "((OOC: {body}))"

_NOT_A_NAME = frozenset({"null", "none", "narrator", "narration", "user", "assistant"})


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
    arc_updated: bool = False  # the story-so-far rollup rebuilt
    characters: list[str] = field(default_factory=list)  # new cast members
    attributed: int = 0  # messages given a speaker by the extraction
    skipped: int = 0  # malformed extraction items dropped


class Cast:
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

    def list(self) -> builtins.list[str]:
        return sorted(self._name_by_id.values(), key=str.casefold)

    def get_name(self, cid: int) -> str:
        return self._name_by_id.get(cid, "?")

    def resolve(self, name: str) -> int | None:
        return self._id_by_name.get(name.strip().casefold())

    def prompt_block(self) -> str:
        rows = [f"- {name}" for name in self.list()]
        return "\n".join(rows) if rows else "(none yet)"


class Extractor:
    """One story bound to one model, plus the channels a pass reports
    through — so the steps don't each thread six arguments. `run` is the
    whole pass; `complete` is the step the warm-up reuses."""

    def __init__(
        self,
        store: Store,
        client: OpenAIClient,
        model: str,
        story_id: int,
        prompts: Prompts,
        *,
        cancel: threading.Event | None = None,
        progress: Callable[[str], None] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._store = store
        self._client = client
        self._model = model
        self._story_id = story_id
        self._prompts = prompts
        self._cancel = cancel or _NEVER_CANCELLED
        self._progress = progress or (lambda line: None)
        self._log = log or (lambda line: None)

    def run(
        self, *, settle: int, min_chars: int, min_messages: int, force: bool = False
    ) -> tuple[PassResult, Report]:
        """One extraction pass over the story's unextracted tail: scene
        closes, then the rollups — which run whatever the gate said, so an
        invalidated or missing history heals on the next idle. `force` (the
        manual close) drops the gate and the settle margin; the spans still
        pack to the configured minimums."""
        report = Report()
        if not self._store.stories.exists(self._story_id):
            return PassResult.NO_STORY, report

        ids = self._store.stories.get_messages_ids(self._story_id)
        ends = self._store.scenes.get_current_ends(self._story_id, ids)
        last_end = max(ends, default=None)
        tail_ids = ids if last_end is None else [i for i in ids if i > last_end]
        if not force and settle > 0:
            tail_ids = tail_ids[: len(tail_ids) - settle]
        chars = self._store.messages.count_body_chars(tail_ids)
        if not tail_ids or (not force and (chars < min_chars or len(tail_ids) < min_messages)):
            self._log(
                f"extraction declined (story {self._story_id}): {chars} settled chars in "
                f"{len(tail_ids)} messages, need {min_chars} in {min_messages}"
            )
            result = PassResult.TOO_SHORT
        else:
            result = self._close_scenes(
                tail_ids, min_chars=min_chars, min_messages=min_messages, report=report
            )
            if result in (PassResult.CANCELLED, PassResult.FAILED):
                return result, report

        # Deliberately unconditional: these refresh previously NOT refreshed
        # rollups, if any — nulled by a /lore edit, or lost to a failure or
        # cancel — so they heal on the next pass whatever the gate said. They
        # self-gate on id-only queries, so a current story costs nothing.
        self._refresh_arc(report)
        self._refresh_histories(report)
        return result, report

    def complete(
        self,
        prompt: str | list[Message],
        purpose: str,
        *,
        params: dict[str, object] | None = None,
        timeout: float = _TIMEOUT,
    ) -> str:
        """One completion, silently (accumulate the stream); records token
        usage against the story under `purpose`. If the cancel event fires
        mid-stream the call returns "" and nothing is recorded. Transient
        transport failures retry with a cancel-aware backoff; a bad HTTP
        status propagates on the first try."""
        messages = [Message(role="user", body=prompt)] if isinstance(prompt, str) else prompt
        last_exc: httpx.TransportError | None = None
        for attempt in range(_ATTEMPTS):
            if self._cancel.is_set():
                return ""
            try:
                return self._stream_once(messages, purpose, params, timeout)
            except httpx.TransportError as e:
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

    def _close_scenes(
        self, tail_ids: list[int], *, min_chars: int, min_messages: int, report: Report
    ) -> PassResult:
        """Close the tail as one scene — or several, when it has run long.
        Only now is the story decrypted; the spans' journals feed each next
        extraction, so the story stays continuous across them."""
        by_id = {m.id: m for m in self._store.stories.get_messages(self._story_id)}
        tail = [by_id[i] for i in tail_ids]
        sizes = [len(m.body) for m in tail]
        spans = [tail[a:b] for a, b in pack(sizes, min_chars=min_chars, min_messages=min_messages)]
        # Message NUMBER = 1-based position on the chain (what the resume
        # line counts), so the progress line can name the span's range.
        ids = self._store.stories.get_messages_ids(self._story_id)
        number = {mid: i for i, mid in enumerate(ids, 1)}
        cast = Cast.load(self._store, self._story_id)
        for k, span in enumerate(spans, 1):
            if self._cancel.is_set():
                self._log(f"extraction cancelled (story {self._story_id})")
                return PassResult.CANCELLED
            span_range = f"{number[span[0].id]} - {number[span[-1].id]}"
            part = f" ({k}/{len(spans)})" if len(spans) > 1 else ""
            # If some spans already closed, the failure notes say so — "the
            # tail stays open" would read as if the whole pass was lost.
            kept = f"{report.scenes} scene(s) closed, the rest of " if report.scenes else ""
            self._log(
                f"extraction (story {self._story_id}): "
                f"closing a scene over {len(span)} messages ({span_range}){part}"
            )
            self._progress(f"closing a scene over {len(span)} messages ({span_range}){part}…")
            journals_before = report.journals
            try:
                closed = self._close_scene(cast, span, "lore", report)
            except httpx.HTTPError as e:
                self._log(f"extraction failed (story {self._story_id}): {type(e).__name__}")
                self._progress(f"extraction failed ({e}) — {kept}the tail stays open")
                return PassResult.FAILED
            except ValueError, json.JSONDecodeError:
                # Best-effort: the unclosed tail stays unextracted, retried
                # on the next idle. Say so — a model that never returns
                # parsable JSON would otherwise build no memory at all,
                # silently forever.
                self._log(f"extraction failed (story {self._story_id}): the reply did not parse")
                self._progress(
                    f"extraction failed (the reply did not parse) — {kept}the tail stays open"
                )
                return PassResult.FAILED
            if not closed:  # cancelled mid-stream
                self._log(f"extraction cancelled (story {self._story_id})")
                return PassResult.CANCELLED
            self._log(
                f"scene closed (story {self._story_id}): "
                f"{len(span)} messages, {report.journals - journals_before} journal entries"
            )
        return PassResult.CLOSED

    def _close_scene(
        self, cast: Cast, span: Sequence[Message], purpose: str, report: Report
    ) -> bool:
        """Extract ONE scene over `span` and apply it — the single
        per-scene step, shared so the
        extraction lives in exactly one place. Reads the current journals
        (feeding each character's story forward), renders the numbered
        scene, completes, parses, and writes: characters, the scene row,
        journals, speakers.

        Returns False when cancelled mid-stream (nothing written). Raises
        `httpx.HTTPError` (request failed) or `ValueError` (unparsable
        reply) — the caller decides what that means."""
        current = self._store.journals.get_current(self._story_id)
        prompt = self._prompts.extract_prompt.format(
            cast=cast.prompt_block(),
            journals=_journals_block(current, cast),
            chunk=numbered_chat(span),
        )
        raw = self.complete(prompt, purpose)
        if not raw:
            return False  # cancelled mid-stream
        data = _parse_json(raw)
        self._apply_scene(cast, data, span, current, report)
        report.scenes += 1
        return True

    def _apply_scene(
        self,
        cast: Cast,
        data: dict[str, object],
        span: Sequence[Message],
        current: dict[int, CharacterMemory],
        report: Report,
    ) -> None:
        """Write one extraction into the store: new characters, the scene
        row, the journals (state carries forward when the extraction omits
        it), and speaker labels — fill-only, never onto an attributed or
        ooc row."""
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

        scene = data.get("scene")
        scene_data = scene if isinstance(scene, dict) else {}
        scene_id = self._store.scenes.add(
            self._story_id,
            start_message_id=span[0].id,
            end_message_id=span[-1].id,
            title=_as_str(scene_data.get("title")),
            summary=_as_str(scene_data.get("summary")),
        )

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

    def _refresh_arc(self, report: Report) -> None:
        """Rebuild the story-so-far rollup when the newest current scene
        lacks one — composed from the scene summaries, never from the
        previous rollup (a photocopy of a photocopy). Best-effort: a
        failure leaves the row NULL and the next pass retries."""
        ids = self._store.stories.get_messages_ids(self._story_id)
        due = self._store.scenes.get_rollups_due(self._story_id, ids)
        if not due or self._cancel.is_set():
            return
        scenes = self._store.scenes.get_current(self._story_id, ids)
        summaries = [s.summary for s in scenes if s.summary]
        if not summaries:
            return
        self._progress(f"composing the story so far from {len(summaries)} scene summaries…")
        started = time.monotonic()
        try:
            arc = self.complete(
                self._prompts.arc_prompt.format(summaries="\n\n".join(summaries)), "rollup"
            ).strip()
        except httpx.HTTPError as e:
            self._log(f"story-so-far rollup failed (story {self._story_id}): {type(e).__name__}")
            self._progress(f"story-so-far rollup failed ({e})")
            return
        if not arc or self._cancel.is_set():
            return
        self._store.scenes.set_history(due[-1], arc)
        report.arc_updated = True
        self._log(
            f"story-so-far rebuilt (story {self._story_id}) from {len(summaries)} scene "
            f"summaries in {_fmt_duration(time.monotonic() - started)}"
        )

    def _refresh_histories(self, report: Report) -> None:
        """Rebuild the history of every character whose newest journal row
        lacks one — those active in the just-closed scene, plus any whose
        rollup an edit invalidated. Composed from the character's own
        entries, never from the previous history. Best-effort per
        character."""
        names = {c.id: c.name for c in self._store.characters.list(self._story_id)}
        for character_id, journal_id in self._store.journals.get_rollups_due(self._story_id):
            if self._cancel.is_set():
                return
            entries = self._store.journals.get_entries(self._story_id, character_id)
            if not entries:
                continue
            name = names.get(character_id, "?")
            self._progress(f"rebuilding {name}'s history from {len(entries)} entries…")
            prompt = self._prompts.history_prompt.format(
                name=name,
                entries="\n\n".join(f"{n}. {text}" for n, text in enumerate(entries, 1)),
            )
            try:
                text = self.complete(prompt, "rollup")
            except httpx.HTTPError as e:
                self._log(
                    f"history rollup failed (story {self._story_id}, "
                    f"character {character_id}): {type(e).__name__}"
                )
                continue
            if text.strip() and not self._cancel.is_set():
                self._store.journals.set_history(journal_id, text.strip())
                report.histories += 1
                self._log(
                    f"history rebuilt (story {self._story_id}): "
                    f"character {character_id} from {len(entries)} entries"
                )

    def _stream_once(
        self,
        messages: Sequence[WireMessage],
        purpose: str,
        params: dict[str, object] | None,
        timeout: float,
    ) -> str:
        """One attempt of `complete`: stream, accumulate, record usage.
        Raises the underlying httpx error so the wrapper can retry."""
        buf: list[str] = []
        final: Stats | None = None
        # max_tokens bounds a repetition loop — without it a looping model
        # generates until someone kills it (streaming resets the read
        # timeout).
        params = params or {"temperature": 0.2, "max_tokens": _MAX_TOKENS}
        stream = self._client.chat_stream(
            self._model,
            messages,
            params,
            think="none",
            purpose=purpose,
            timeout=timeout,
            smooth=False,  # accumulated into a string; nobody watches it
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
                self._client.provider.name,
                self._model,
                purpose,
                story_id=self._story_id,
                prompt_tokens=final.prompt_tokens,
                completion_tokens=final.completion_tokens,
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
    """The numbered scene block for `extract_prompt` — the one owner of the
    `[n] Speaker: …` format. An attributed line carries its speaker; an
    out-of-character row shows its `((OOC: …))` enclosure (via its stored
    framing, or `_OOC_MARK` when it has none)."""
    lines: list[str] = []
    for n, item in enumerate(span, 1):
        if item.kind == "ooc" and item.framing is None:
            body = _OOC_MARK.replace("{body}", item.body)
        elif item.speaker and item.body:
            body = f"{item.speaker}: {item.body}"
        else:
            body = item.body
        lines.append(f"[{n}] {combine_framing(body, item.framing)}")
    return "\n".join(lines)


def _parse_json(text: str) -> dict[str, object]:
    """Parse the extraction reply: tolerate code fences and surrounding
    prose by slicing from the first '{' to the last '}'."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in reply")
    obj = json.loads(text[start : end + 1])
    if not isinstance(obj, dict):
        raise ValueError("extraction reply is not a JSON object")
    return obj


def _journals_block(current: dict[int, CharacterMemory], cast: Cast) -> str:
    """The current journals rendered for the extraction prompt — the
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


def _fmt_duration(seconds: float) -> str:
    if seconds >= 60:
        return f"{int(seconds // 60)}m {int(seconds % 60):02d}s"
    return f"{seconds:.0f}s"
