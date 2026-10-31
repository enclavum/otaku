"""The cast tab: the story's characters listed and previewed, a character
opened as its field list (`char_fields`) — the memory's second lens —
and a character deleted after a y/n confirm."""

from prompt_toolkit.formatted_text import StyleAndTextTuples

from otaku.formatting import flatten, truncate
from otaku.terminal.api import Refused
from otaku.terminal.api.lore import Character, Lore
from otaku.terminal.screens.base import (
    wrap_text,
)
from otaku.terminal.screens.dossier.base import DossierBase, Field, made_by


class CastTab(DossierBase):
    """The cast, a character's lookups, and its delete."""

    def _char_by_id(self, cid: int) -> Character | None:
        assert self.view is not None
        return next((c for c in self.view.characters if c.id == cid), None)

    def _latest_state(self, cid: int) -> Field | None:
        """The character's newest state row, as their field list shows it."""
        assert self.view is not None
        states = [f for f in char_fields(self.view, cid) if f.kind == "state"]
        return states[-1] if states else None

    def _history_field(self, cid: int) -> Field | None:
        assert self.view is not None
        return next((f for f in char_fields(self.view, cid) if f.kind == "history"), None)

    def _cast_rows(self) -> StyleAndTextTuples:
        assert self.view is not None
        out: StyleAndTextTuples = []
        if not self.cast_f:
            msg = "(no matches)" if self.query else "(no characters yet)"
            out.append(("class:muted", "  " + msg))
            return out
        name_w = min(20, max(len(c.name) for c in self.view.characters))
        avail = max(10, self._max_row_content_width() - 4 - name_w - 3)
        for row_i, idx in enumerate(self.cast_f):
            c = self.view.characters[idx]
            latest = self._latest_state(c.id)
            snippet = f"now: {latest.text}" if latest else "(no journal yet)"
            row = f"{truncate(c.name, name_w):<{name_w}} · {truncate(flatten(snippet), avail)}"
            self._emit_row(out, row_i == self.cursor, row)
        return out

    def _cast_preview(self, width: int) -> StyleAndTextTuples:
        assert self.view is not None
        if not self.cast_f:
            return [("class:preview.muted", "nothing to preview")]
        out: StyleAndTextTuples = []
        c = self.view.characters[self.cast_f[self.cursor]]
        aka = f" (aka {', '.join(c.aliases)})" if c.aliases else ""
        out.append(("class:preview.title", f"{c.name}{aka}\n"))
        if c.description:
            out.append(("class:preview.body", "\n"))
            for line in wrap_text(c.description, width):
                out.append(("class:preview.body", line + "\n"))
        latest = self._latest_state(c.id)
        if latest is not None:
            out.append(("class:preview.body", "\n"))
            out.append(("class:preview.muted", f"now ({_vintage(self.view, latest.target)}):\n"))
            for line in wrap_text(latest.text, width):
                out.append(("class:preview.body", line + "\n"))
            history = self._history_field(c.id)
            if history is not None:
                out.append(("class:preview.body", "\n"))
                out.append(("class:preview.muted", "so far (rebuilt from entries):\n"))
                for line in wrap_text(history.text, width):
                    out.append(("class:preview.muted", line + "\n"))
        else:
            out.append(("class:preview.body", "\n"))
            out.append(("class:preview.muted", "(no journal yet)\n"))
        out += made_by(c.provider, c.model, width)
        return out

    def _cast_target(self) -> Character | None:
        """The character under the cast list's cursor; None anywhere else,
        an open detail included."""
        if not self.dossier_on or self.tab != "cast" or self.detail is not None:
            return None
        if not self.cast_f or self.view is None:
            return None
        return self.view.characters[self.cast_f[self.cursor]]

    def _request_cast_delete(self) -> None:
        self.notice = ""
        if self._cast_target() is not None:
            self.confirming_cast_delete = True

    def _do_cast_delete(self) -> None:
        """y on the confirm: the character deleted (`api.stories.characters`),
        then the memory and the cast's visible rows read again. A refusal
        is kept in `notice`, drawn nowhere, and the row stays."""
        self.confirming_cast_delete = False
        target = self._cast_target()
        if target is None:
            return
        try:
            self.notice = self.api.stories.characters.delete(self.story_id, target.id).notice
        except Refused as e:
            self.notice = str(e)
            return
        self._reload()
        self._refilter()

    def _cast_delete_text(self) -> StyleAndTextTuples:
        """The confirm: the question naming the character, a blank row,
        the keys."""
        target = self._cast_target()
        if target is None:
            return []
        return [
            ("class:dialog.title", f"Delete {truncate(flatten(target.name), 50)}?\n"),
            ("class:dialog.body", "\n"),
            ("class:dialog.muted", "y to confirm     n / esc to cancel"),
        ]


def char_fields(view: Lore, character_id: int) -> list[Field]:
    """A character's detail: the description, the card an imported one
    carries — the author's to correct like any primitive, emptied to
    nothing included, so the row cannot vanish under the cursor — its
    journal records scene by scene in story order, and its arc so far."""
    char = next((c for c in view.characters if c.id == character_id), None)
    if char is None:
        return []
    out = [Field("description", "description", char.description, char.id, True)]
    if char.profile is not None:
        out.append(Field("card", "profile", char.profile, char.id, True))
    scenes = {s.id: s for s in view.scenes}
    position = {s.id: i for i, s in enumerate(view.scenes)}
    rows = sorted(
        (j for j in view.journals if j.character_id == char.id),
        key=lambda r: position.get(r.scene_id, 0),
    )
    for r in rows:
        scene = scenes.get(r.scene_id)
        label = flatten(scene.title) if scene and scene.title else f"scene {r.scene_no}"
        out.append(
            Field(f"{label} · entry", "entry", r.entry, r.id, True, r.scene_id, scene_no=r.scene_no)
        )
        out.append(
            Field(
                f"{label} · state", "state", r.state, r.id, False, r.scene_id, scene_no=r.scene_no
            )
        )
    if char.history and char.history_journal_id is not None:
        out.append(
            Field("history (so far)", "history", char.history, char.history_journal_id, False)
        )
    return out


def _vintage(view: Lore, journal_id: int) -> str:
    """How current a journal record's state is, for the preview: its
    scene, and how far the story has moved past it."""
    r = next((j for j in view.journals if j.id == journal_id), None)
    scene = next((s for s in view.scenes if s.id == r.scene_id), None) if r is not None else None
    if r is None or scene is None:
        return ""
    title = f" '{scene.title}'" if scene.title else ""
    return f"scene {scene.no}{title}, {r.messages_since} msgs ago"
