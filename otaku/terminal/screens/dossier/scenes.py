"""The scenes tab: the story's scenes listed and previewed, and a scene
opened as its field list (`scene_fields`) — the memory's first lens."""

from prompt_toolkit.formatted_text import StyleAndTextTuples

from otaku.formatting import flatten, truncate
from otaku.terminal.api.lore import Lore, Scene
from otaku.terminal.screens.base import (
    wrap_text,
)
from otaku.terminal.screens.dossier.base import DossierBase, Field, made_by


class ScenesTab(DossierBase):
    """The scenes, and a scene's lookups."""

    def _scene_by_id(self, scene_id: int) -> Scene | None:
        assert self.view is not None
        return next((s for s in self.view.scenes if s.id == scene_id), None)

    def _label_parts(self, scene_id: int) -> tuple[str, str, str]:
        """The label's three columns — number, span, title — as the scene
        carries them (`Scene.no`, `Scene.span`), so the aligned list never
        has to fish a part back out of display text."""
        scene = self._scene_by_id(scene_id)
        if scene is None:
            return "0", "", ""
        return str(scene.no), scene.span, flatten(scene.title) if scene.title else ""

    def _scene_rows(self) -> StyleAndTextTuples:
        assert self.view is not None
        out: StyleAndTextTuples = []
        if not self.scenes_f:
            msg = "(no matches)" if self.query else "(no scenes yet — they close as you play)"
            out.append(("class:muted", "  " + msg))
            return out
        parts = [self._label_parts(self.view.scenes[i].id) for i in self.scenes_f]
        no_w = max(len(no) for no, _, _ in parts)
        span_w = max(len(span) for _, span, _ in parts)
        # prefix(4) + no + "  " + span + "  " — the row's own spacing.
        avail = max(10, self._max_row_content_width() - 4 - no_w - 2 - span_w - 2)
        for row_i, (no, span, title) in enumerate(parts):
            row = f"{no:>{no_w}}  {span:>{span_w}}  {truncate(flatten(title), avail)}"
            self._emit_row(out, row_i == self.cursor, row)
        return out

    def _scene_preview(self, width: int) -> StyleAndTextTuples:
        assert self.view is not None
        if not self.scenes_f:
            return [("class:preview.muted", "nothing to preview")]
        out: StyleAndTextTuples = []
        s = self.view.scenes[self.scenes_f[self.cursor]]
        no, span, title = self._label_parts(s.id)
        out.append(("class:preview.title", (flatten(title) or f"scene {no}") + "\n"))
        out.append(("class:preview.muted", f"messages {span}\n"))
        out.append(("class:preview.body", "\n"))
        for line in wrap_text(s.summary or "(no summary)", width):
            out.append(("class:preview.body", line + "\n"))
        present = [
            c.name
            for c in self.view.characters
            if any(j.scene_id == s.id and j.character_id == c.id for j in self.view.journals)
        ]
        if present:
            out.append(("class:preview.body", "\n"))
            out.append(("class:preview.muted", "present: " + ", ".join(present) + "\n"))
        out += made_by(s.provider, s.model, width)
        return out


def scene_fields(view: Lore, scene_id: int) -> list[Field]:
    """A scene's detail: its title and summary (editable), its arc, and
    each journal record of it — the entry editable, the state not."""
    scene = next((s for s in view.scenes if s.id == scene_id), None)
    if scene is None:
        return []
    out = [
        Field("title", "scene-title", scene.title, scene.id, True),
        Field("summary", "scene-summary", scene.summary, scene.id, True),
        Field("history", "scene-history", scene.history, scene.id, False),
    ]
    names = {c.id: c.name for c in view.characters}
    for r in (j for j in view.journals if j.scene_id == scene.id):
        name = names.get(r.character_id, "?")
        out.append(Field(f"{name} · entry", "entry", r.entry, r.id, True, r.character_id))
        out.append(Field(f"{name} · state", "state", r.state, r.id, False, r.character_id))
    return out
