"""The memory, operated: the forced extraction pass, and the lore view
both frontends browse and correct.

The view is loaded whole and reloaded after every edit — cheap for one
story, and the store's own invalidation (edits null the rollups built
from them) is never mirrored by hand.
"""

import threading
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Literal

from otaku.backend.session import NO_MODEL_HINT, NO_STORY_HINT, Refused, Session
from otaku.backend.story import StorySettings
from otaku.context.assembler import ContextShape
from otaku.formatting import flatten
from otaku.store.schema import Character, Journal, Scene
from otaku.worker import Job
from otaku.worker.extraction import ExtractionSettings, PassResult, Report

# Every field a detail view shows. Hand-editable is what is written once
# and read from then on (titles, summaries, entries, descriptions,
# cards); the extractor's own — `state` (superseded by the next scene's
# row), `history` and `scene-history` (rebuilt from the primitives) —
# is shown and refused by `edit`.
FieldKind = Literal[
    "scene-title",
    "scene-summary",
    "scene-history",
    "description",
    "card",
    "entry",
    "state",
    "history",
]


def build_job(session: Session, *, force: bool = False) -> Job:
    """The worker job for the session as it stands: the story, the model,
    the extraction settings, and the snapshot the warm-up rebuilds the
    next request from. The one composer, so play's idle arming and the
    forced close can never disagree on the snapshot."""
    story_id = session.story_id
    assert story_id is not None  # callers gate on a recorded story
    config, prompts = session._config, session._prompts
    settings = StorySettings(session._settings_db, session._store, session._paths.prompts_file)
    return Job(
        provider=session.provider,
        model=session.model,
        story_id=story_id,
        system=session.system,
        messages=list(session.messages),
        injections=settings.injections,
        tool_set=settings.tool_set,
        prompts=prompts,
        shape=ContextShape(
            head_messages=config.head_messages,
            min_tail_messages=config.min_tail_messages,
            max_context=config.max_context,  # the cap; the warm-up adds the model's window
        ),
        extraction_settings=ExtractionSettings(
            extract_template=prompts.extract_prompt,
            scene_history_template=prompts.scene_history_prompt,
            journal_history_template=prompts.journal_history_prompt,
            settle=config.settle_messages,
            min_chars=config.scene_min_chars,
            min_messages=config.scene_min_messages,
        ),
        force=force,
    )


class WorkerRun:
    """A forced WORKER pass in flight. Channel-safe by contract (like the
    session's channel methods): `wait`, `poll`, and `cancel` touch only
    the run's own event and report — the worker sets, any thread may
    ask — so a web GET can answer without holding the one session
    executor."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._done = threading.Event()
        self._report = ""
        self._failed = False

    def wait(self) -> str:
        """Block until the pass returns; the report to show — scenes
        closed, journals written, rollups refreshed, or what failed and
        why (the worker's held status names a crash's real cause)."""
        self._done.wait()
        return self._report

    def poll(self) -> str | None:
        """The report when the pass has returned, None while it runs —
        the non-blocking form the web's GET polls."""
        return self._report if self._done.is_set() else None

    def settled(self, timeout: float) -> bool:
        """Wait up to `timeout` seconds for the pass to return; True when
        it has. A foreground wait refreshes its status line between
        these, and wakes the moment the pass is done rather than at the
        end of whatever sleep it was in."""
        return self._done.wait(timeout)

    @property
    def failed(self) -> bool:
        """Whether the returned pass FAILED — the report is a refusal,
        and a frontend shows it the way it shows any other."""
        return self._failed

    def cancel(self) -> str:
        """Abort mid-stream; nothing half-done commits. The notice.

        Guarded on this run's own end first: `_worker.cancel()` aborts
        whatever the worker is running NOW, and a pass that finished
        between the caller's poll and this call may have handed the
        worker something else — an automatic pass the caller never
        started. A finished run answers with its report instead. The
        guard narrows that race to a moment; closing it entirely would
        take cancel-by-identity in the worker."""
        if self._done.is_set():
            return self._report or "The pass already finished."
        self._session._worker.cancel()
        return "Cancelled — nothing half-done commits; already-closed scenes stay."

    # The worker's side (package-internal): called on the worker thread,
    # before the warm-up.
    def _finish(self, result: PassResult, report: Report) -> None:
        self._report = _pass_report(result, report, held=self._session.status())
        self._failed = result is PassResult.FAILED
        self._done.set()


def extract(session: Session) -> WorkerRun:
    """Run the extraction right now, through the worker (one path into a
    pass, so a forced close can never race an automatic one): gate and
    settle margin dropped, closing right up to the last message. Raises
    Refused without a story or a model."""
    if session.story_id is None:
        raise Refused(NO_STORY_HINT)
    if session._client() is None:
        raise Refused(NO_MODEL_HINT)
    run = WorkerRun(session)
    session._worker.schedule(replace(build_job(session, force=True), on_done=run._finish), now=True)
    return run


def merge_raw(session: Session, raw: str) -> str:
    """`SOURCE into TARGET` (raw text, multi-word names surviving): fold
    an extraction duplicate into the real character. NAMES are what a
    typed line carries, so resolving them is this function's whole job —
    what it does with the two it finds is `merge_by_id` below."""
    if session.story_id is None:
        raise Refused(NO_STORY_HINT)
    src_raw, sep, dst_raw = raw.partition(" into ")
    if not sep or not src_raw.strip() or not dst_raw.strip():
        raise Refused("Usage: /merge SOURCE into TARGET")
    source = session._store.characters.find(session.story_id, src_raw)
    target = session._store.characters.find(session.story_id, dst_raw)
    if source is None or target is None:
        missing = src_raw if source is None else dst_raw
        raise Refused(f"No character named '{missing.strip()}' in this story (see /cast).")
    return merge_by_id(session, source.id, target.id)


def merge_by_id(session: Session, source_id: int, target_id: int) -> str:
    """Fold one character into another BY ID — speakers and journals
    follow, the source becomes an alias. Refused toward the direction
    that would destroy an imported card's archive. Returns the
    confirmation. A surface that already holds the cast (a browser)
    names the two rows; a typed line resolves names to ids first."""
    if session.story_id is None:
        raise Refused(NO_STORY_HINT)
    # Resolved against THIS story's cast, so an id from another story is
    # as refused as an id that is not there at all.
    cast = {row.id: row for row in session._store.characters.list(session.story_id)}
    source, target = cast.get(source_id), cast.get(target_id)
    if source is None or target is None:
        raise Refused("No such character in this story (see /cast).")
    if source.id == target.id:
        raise Refused(f"'{source.name}' and '{target.name}' are already the same character.")
    if source.card:
        # A merge deletes the source row, its card archive with it — and
        # the card rows pointing at the target would send their typed
        # line instead of the composed block. Refused toward the
        # direction that keeps the archive.
        raise Refused(
            f"{source.name} carries an imported card, which a merge would destroy — "
            f"merge the other direction (/merge {target.name} into {source.name}) "
            f"so the card survives."
        )
    session._store.characters.merge(session.story_id, source.id, target.id)
    # SOURCE is now an alias of TARGET, so a later /me or /you naming it
    # still resolves — nothing else to update.
    return f"Merged {source.name} into {target.name} ('{source.name}' is now an alias)."


def cast(session: Session) -> tuple[Character, ...]:
    """The story's characters in appearance order — the cheap read the
    prompt menus and the pickers fire per keystroke. Empty when no story
    (a menu question is never a refusal)."""
    if session.story_id is None:
        return ()
    return tuple(session._store.characters.list(session.story_id))


@dataclass(frozen=True)
class Field:
    """One row of a detail view: a label, the text it holds, and the
    address (`kind`, `target`) an edit or a pivot on it names."""

    label: str
    kind: FieldKind
    text: str
    target: int  # scene id / character id / journal id, per kind
    editable: bool
    pivot: int | None = None  # the other parent: character id / scene id
    scene_no: int | None = None  # a journal row's scene number, in the cast view


@dataclass(frozen=True)
class LoreView:
    """The story's memory as the browser shows it: current scenes, the
    cast in appearance order, the current timeline's journal rows, and
    the field lists a detail view opens into. Immutable — edit through
    `edit` and reload."""

    story_id: int
    total_messages: int
    scenes: tuple[Scene, ...]
    cast: tuple[Character, ...]
    journals: tuple[Journal, ...]
    # message id → 1-based chain position, for spans and vintages.
    _ordinal: dict[int, int]

    def scene_fields(self, scene_id: int) -> list[Field]:
        out: list[Field] = []
        scene = self._scene_by_id(scene_id)
        if scene is None:
            return out
        out += [
            Field("title", "scene-title", scene.title, scene.id, True),
            Field("summary", "scene-summary", scene.summary, scene.id, True),
            Field("history", "scene-history", scene.history, scene.id, False),
        ]
        for r in self._by_scene().get(scene.id, []):
            char = self._char_by_id(r.character_id)
            name = char.name if char else "?"
            out.append(Field(f"{name} · entry", "entry", r.entry, r.id, True, r.character_id))
            out.append(Field(f"{name} · state", "state", r.state, r.id, False, r.character_id))
        return out

    def char_fields(self, character_id: int) -> list[Field]:
        char = self._char_by_id(character_id)
        if char is None:
            return []
        out = [Field("description", "description", char.description, char.id, True)]
        if char.card is not None:
            # Only an imported character carries a card; the archive is
            # the author's to correct like any primitive — emptied to
            # nothing included, so the row cannot vanish under the cursor.
            out.append(Field("card", "card", char.card, char.id, True))
        for r in self._by_char().get(char.id, []):
            scene = self._scene_by_id(r.scene_id)
            no = self.scene_no(r.scene_id)
            label = flatten(scene.title) if scene and scene.title else f"scene {no}"
            out.append(
                Field(f"{label} · entry", "entry", r.entry, r.id, True, r.scene_id, scene_no=no)
            )
            out.append(
                Field(f"{label} · state", "state", r.state, r.id, False, r.scene_id, scene_no=no)
            )
        history, hrow = self._current_history(char.id)
        if history and hrow is not None:
            out.append(Field("history (so far)", "history", history, hrow.id, False))
        return out

    def scene_label(self, scene_id: int) -> str:
        """ "4  61-84  The Crossing" — number, span, title (flattened,
        UNCUT: display width is the frontend's). Composed from the two
        parts below; a frontend that wants the parts asks for THEM — a
        label with the span dropped reads a title as a range."""
        scene = self._scene_by_id(scene_id)
        if scene is None:
            return "?"
        parts = [str(self.scene_no(scene_id))]
        span = self.scene_span(scene_id)
        if span:
            parts.append(span)
        if scene.title:
            parts.append(flatten(scene.title))
        return "  ".join(parts)

    def scene_no(self, scene_id: int) -> int:
        """1-based position of a scene in story order — the number every
        label leads with; 0 for a scene not in the view."""
        return next((i + 1 for i, s in enumerate(self.scenes) if s.id == scene_id), 0)

    def scene_span(self, scene_id: int) -> str:
        """The scene's "N-M" message range on the CURRENT chain — "" when
        either end is off it (an edit moved the head past the scene).
        The one home of the span, as data: both frontends print it
        beside the title, and neither may fish it back out of the
        label."""
        scene = self._scene_by_id(scene_id)
        if scene is None:
            return ""
        start = self._ordinal.get(scene.start_message_id)
        end = self._ordinal.get(scene.end_message_id)
        if start is None or end is None:
            return ""
        return f"{start}-{end}"

    def read_through(self) -> int:
        """The last message a closed scene covers, as a 1-based chain
        position — 0 when nothing has been read yet. Derived, never
        stored: a scene closes at a message, so the newest scene's end
        IS how far the extractor has read."""
        ends = [self._ordinal.get(s.end_message_id, 0) for s in self.scenes]
        return max(ends, default=0)

    def unread(self) -> int:
        """Messages played past the last closed scene. Not a fault: the
        settle margin leaves the newest turns open on purpose, and a
        forced pass is what closes them early."""
        return max(0, self.total_messages - self.read_through())

    def unread_span(self) -> str:
        """Those messages as an "N-M" range, spelled like `scene_span`
        so the two read as one vocabulary; "" when none are open."""
        read = self.read_through()
        return f"{read + 1}-{self.total_messages}" if self.total_messages > read else ""

    def vintage(self, row_id: int) -> str:
        """How current a journal row's state is, for the preview: its
        scene, and how far the story has moved past it."""
        r = next((j for j in self.journals if j.id == row_id), None)
        scene = self._scene_by_id(r.scene_id) if r is not None else None
        if r is None or scene is None:
            return ""
        no = self.scene_no(r.scene_id)
        ago = self.total_messages - self._ordinal.get(scene.end_message_id, self.total_messages)
        title = f" '{scene.title}'" if scene.title else ""
        return f"scene {no}{title}, {ago} msgs ago"

    def character_history(self, character_id: int) -> str:
        """The character's arc SO FAR — their newest non-empty rollup, or
        "" before a pass has written one. A scene's arc is a column of
        its own (`Scene.history`); a character's is not, so this is where
        both frontends read it, and neither picks the row itself."""
        return self._current_history(character_id)[0]

    # ---------- view internals (recomputed on the fly; one story is small) ----------

    def _scene_by_id(self, scene_id: int) -> Scene | None:
        return next((s for s in self.scenes if s.id == scene_id), None)

    def _char_by_id(self, character_id: int) -> Character | None:
        return next((c for c in self.cast if c.id == character_id), None)

    def _by_scene(self) -> dict[int, list[Journal]]:
        out: dict[int, list[Journal]] = {}
        for r in self.journals:
            out.setdefault(r.scene_id, []).append(r)
        return out

    def _by_char(self) -> dict[int, list[Journal]]:
        """A character's rows in STORY order — the cast drill-in walks
        them scene by scene, and the newest scene's row is the live one."""
        position = {s.id: i for i, s in enumerate(self.scenes)}
        out: dict[int, list[Journal]] = {}
        for r in self.journals:
            out.setdefault(r.character_id, []).append(r)
        for rows in out.values():
            rows.sort(key=lambda r: position.get(r.scene_id, 0))
        return out

    def _current_history(self, character_id: int) -> tuple[str, Journal | None]:
        """The character's newest non-empty rollup, as the store's
        `get_current` would pick it."""
        for r in reversed(self._by_char().get(character_id, [])):
            if r.history:
                return r.history, r
        return "", None


def view(session: Session, story_id: int | None = None) -> LoreView:
    """The memory loaded whole — the open story's, or `story_id`'s for a
    browser reading one that is not open. Raises Refused without a
    story to read."""
    if story_id is None:
        story_id = session.story_id
    if story_id is None:
        raise Refused(NO_STORY_HINT)
    store = session._store
    ids = store.stories.get_messages_ids(story_id)
    scenes = tuple(store.scenes.get_current(story_id, ids))
    current = {s.id for s in scenes}
    return LoreView(
        story_id=story_id,
        total_messages=len(ids),
        scenes=scenes,
        # In order of appearance (row order), not by name: the cast reads
        # as the story introduced it.
        cast=tuple(store.characters.list(story_id)),
        journals=tuple(j for j in store.journals.list(story_id) if j.scene_id in current),
        _ordinal={mid: i + 1 for i, mid in enumerate(ids)},
    )


def edit(session: Session, kind: FieldKind, target: int, text: str, story_id: int) -> str:
    """Apply the author's correction: `kind` and `target` are a Field's
    address fields (the view row itself never travels back — display
    state is not a write address), and `story_id` is the story the
    caller believes the row belongs to — CHECKED, the way a message edit
    checks its chain: a browser addresses any story, and a correction
    that landed on another story's row would rewrite memory nobody was
    looking at. The store writers carry the invalidation. Returns the
    notice; raises Refused for a row that is not in the story's memory,
    for an edit that cannot be one (a non-TOML card, an emptied
    summary), and for the extractor's own rows (`state`, `history`,
    `scene-history`), which are corrected through their inputs."""
    # The extractor's own rows refuse before anything is looked up.
    if kind == "state":
        raise Refused("The state is the extractor's own — correct the entry instead.")
    if kind == "scene-history":
        raise Refused("The scene's history is derived — correct the summaries and it rebuilds.")
    if kind == "history":
        raise Refused("The history is derived — correct the entries and it rebuilds.")
    store = session._store
    owned = view(session, story_id)
    if kind == "scene-title":
        _theirs(owned.scenes, target, "scene")
        store.scenes.update(target, title=text)
    elif kind == "scene-summary":
        _theirs(owned.scenes, target, "scene")
        if not text.strip():
            # A cleared summary would silently swallow the scene's span:
            # the assembler covers a scene only while its summary exists,
            # and a later scene's coverage still moves the tail past this
            # one. Extraction refuses to commit exactly this state.
            raise Refused("An emptied summary would swallow its scene — not saved.")
        store.scenes.update(target, summary=text)
    elif kind == "description":
        _theirs(owned.cast, target, "character")
        store.characters.set_description(target, text)
    elif kind == "card":
        _theirs(owned.cast, target, "character")
        try:
            tomllib.loads(text)
        except tomllib.TOMLDecodeError as e:
            # The archive is data other features parse — a broken edit is
            # refused the way an emptied summary is.
            raise Refused(f"Not valid TOML — not saved: {e}") from e
        store.characters.set_card(target, text)
    elif kind == "entry":
        _theirs(owned.journals, target, "journal")
        store.journals.set_entry(target, text)
    else:
        raise ValueError(f"unknown field kind {kind!r}")
    return "Saved."


def _theirs(rows: Iterable[Scene | Character | Journal], target: int, noun: str) -> None:
    """The addressed row must be in the story's own memory — the same
    rule `stories.edit_message` holds over its chain. The rows come from
    the story's VIEW, so a row of a rewound timeline refuses along with
    another story's: neither is in the memory any browser shows."""
    if all(row.id != target for row in rows):
        raise Refused(f"That {noun} is not in this story's memory.")


def _pass_report(result: PassResult, report: Report, *, held: str) -> str:
    """The outcome of a forced close, as one sentence. `held` is the
    worker's held status row: when the pass CRASHED it names the real
    cause, and the report repeats it instead of blaming the model's
    reply for every failure. Composed from the pass's own data alone —
    no store reads, so the worker thread can set it."""
    if result is PassResult.NO_STORY:
        return "Nothing is recorded for this story, so there is nothing to extract."
    if result is PassResult.TOO_SHORT:
        return "Nothing new since the last scene."
    if result is PassResult.CANCELLED:
        return "Cancelled — nothing half-done commits; already-closed scenes stay."
    if result is PassResult.FAILED:
        reason = "bad reply or request error"
        if held.startswith("extraction failed (") and held.endswith(")"):
            reason = held[len("extraction failed (") : -1]
        return f"Extraction failed ({reason}) — the tail stays open; try again."
    rolled = f", {report.histories} history rollup(s)" if report.histories else ""
    refreshed = "; story-so-far refreshed" if report.scene_histories else ""
    journals = f"{report.journals} journal(s) written{rolled}{refreshed}."
    if report.scenes > 1:
        return f"{report.scenes} scenes closed: {journals}"
    title = f" '{report.last_scene_title}'" if report.last_scene_title else ""
    return f"Scene{title} closed: {journals}"
