"""The tools tab: the story's settings, one row each — a checkbox and the
name; Space checks and unchecks it — with the setting's fields in the
panel beside: the notes' display, a reminder's text, a setting's
instructions (its prompt), the web search key, and last where its text
rides. Every change is written at once, through the door."""

from dataclasses import dataclass
from typing import Literal

from prompt_toolkit.formatted_text import StyleAndTextTuples

from otaku.terminal.api import UNCHANGED, InjectionPosition, Refused, StorySetting
from otaku.terminal.api import prompts as api_prompts
from otaku.terminal.screens.base import (
    wrap_text,
)
from otaku.terminal.screens.dossier.base import DossierBase

ToolFieldKind = Literal["display", "reminder", "prompt", "key", "position"]


# The kinds that are a text to edit: a reminder where it is read, a
# prompt over the whole panel, the web search key typed over its standing.
TEXT_KINDS: frozenset[ToolFieldKind] = frozenset({"reminder", "prompt", "key"})


@dataclass(frozen=True)
class _ToolField:
    """One field of a setting, as the panel walks them: the notes'
    display, a reminder's text, a setting's instructions (its prompt), the
    web search key, where its text rides. `text` is the field as read;
    a reminder edits in place, the key is typed in place over where it
    stands (never shown), the instructions open the prompt over the
    whole panel — the rest toggle or choose."""

    kind: ToolFieldKind
    label: str
    text: str


# What each story setting does, in the order the tab draws them. A
# caption is the frontend's own: the backend names the setting and says
# where it stands, and this says what switching it does — the page keeps
# the same table (`web/static/js/screens/tools.js`). A setting without a caption
# is not drawn: nothing here could say what it is for.
_ABOUT: dict[str, str] = {
    "allow_questions": (
        "The narrator may ask you something directly and wait for an answer before going on."
    ),
    "allow_web_search": "The narrator may look something up on the internet.",
    "allow_system_info": "The narrator may ask for local system facts.",
    "allow_assistant_notes": (
        "The narrator may keep a short note to itself between turns, never played into the story."
    ),
    "use_story_reminder": "Injected into this story with every reply.",
    "use_shared_reminder": "Injected into the story with every reply, common across stories.",
    "emulate_text_completion": (
        "Only in the story mode. The narrator is told to continue the text from where it "
        "ends, as a text completion would, instead of answering it."
    ),
}


# A caption's bold opening, where it has one: the words the caption
# starts with, drawn bold.
_LEAD: dict[str, str] = {"emulate_text_completion": "Only in the story mode."}


# The settings that open a group: the list draws a blank row before
# each, after the first row — the tools, then the reminders, then the
# canvas's own.
_OPENS_GROUP = frozenset({"use_story_reminder", "emulate_text_completion"})


# The one setting whose text is not the story's: every story is sent the
# same reminder, read and written through its own operations.
SHARED_REMINDER = "use_shared_reminder"


# The one setting with a key under it: the web search key, every
# story's, read and written through its own operations.
_WEB_SEARCH = "allow_web_search"


# A checkbox, as a row draws it.
_CHECKED, _UNCHECKED = "[x]", "[ ]"


class ToolsTab(DossierBase):
    """The settings, their panel, and its writes."""

    def _reload_settings(self) -> None:
        """The story's settings again — after every write, since the
        backend lays what was stored over the defaults — and with them
        the shared reminder's text and each injection setting's prompt,
        read once here rather than per redraw."""
        held = self.api.stories.settings.list(self.story_id)
        by_name = {setting.name: setting for setting in held}
        self.settings = [setting for name in _ABOUT if (setting := by_name.get(name)) is not None]
        self.shared_text = self.api.shared_reminder.get().text
        self.web_search = self.api.integrations.web_search.get()
        self.prompts = {
            setting.name: api_prompts.read_prompt(self.api, setting)
            for setting in self.settings
            if setting.prompt_name is not None
        }
        self.shown_tools = self.api.stories.get(self.story_id).shown_tools

    def _tool_fields(self, setting: StorySetting) -> list[_ToolField]:
        """A setting's fields as the panel walks them: the notes' display,
        for the one with something to display; the reminder's text, for
        the story's own and the shared one; the instructions of one that
        sends a prompt, a row that opens it; and last, for one that injects, where
        its text rides — each reading as the setting stands now."""
        rows: list[_ToolField] = []
        if setting.display_notes is not None:
            rows.append(_ToolField("display", "display notes", _checkbox(setting.display_notes)))
        if setting.name == SHARED_REMINDER:
            rows.append(_ToolField("reminder", "shared text", self.shared_text))
        elif setting.reminder_text is not None:
            rows.append(_ToolField("reminder", "reminder text", setting.reminder_text))
        if setting.name in self.prompts:
            rows.append(_ToolField("prompt", "instructions", self.prompts[setting.name]))
        if setting.name == _WEB_SEARCH:
            rows.append(_ToolField("key", "web search key", self._key_text()))
        if setting.injection_position is not None:
            rows.append(_ToolField("position", "depth", setting.injection_position.text))
        return rows

    def _key_text(self) -> str:
        """Where the web search key stands, as its field reads — never
        the key."""
        return "(key set)" if self.web_search.has_key else "(none)"

    def _tool_field(self) -> _ToolField | None:
        """The field the panel's cursor is on; None off the tab or with
        nothing to walk."""
        if self.tab != "tools" or not self.settings:
            return None
        rows = self._tool_fields(self.settings[self.cursor])
        return rows[min(self.field, len(rows) - 1)] if rows else None

    def _tool_rows(self) -> StyleAndTextTuples:
        """One row per setting: its checkbox and its name, a blank row
        before each group after the first (`_gaps`). The band stays on
        the list's row while the cursor is in the panel, dimmed so the
        panel reads as the place the cursor went."""
        out: StyleAndTextTuples = []
        if not self.settings:
            out.append(("class:muted", "  (no settings in this build)"))
            return out
        gaps = self._gaps()
        for row_i, setting in enumerate(self.settings):
            if row_i in gaps:
                out.append(("", "\n"))
            row = f"{_checkbox(setting.enabled)} {setting.label}"
            self._emit_row(out, row_i == self.cursor, row, dim=self.in_panel)
        return out

    def _gaps(self) -> frozenset[int]:
        """The rows of the tools list a blank row is drawn before: each
        setting that opens a group (`_OPENS_GROUP`), the first row aside."""
        return frozenset(i for i, s in enumerate(self.settings) if i and s.name in _OPENS_GROUP)

    def _tool_parts(self) -> tuple[list[_ToolField], list[_ToolField], _ToolField | None]:
        """The setting's fields around its text — a reminder's, or the
        web search key's standing: those above it, those below, and the
        text itself — None for a setting with none, whose fields are all
        above."""
        fields = self._tool_fields(self.settings[self.cursor]) if self.settings else []
        text = next((f for f in fields if f.kind in ("reminder", "key")), None)
        if text is None:
            return fields, [], None
        at = fields.index(text)
        return fields[:at], fields[at + 1 :], text

    def _tool_focused(self, f: _ToolField) -> bool:
        return self.in_panel and self._tool_field() == f

    def _tool_lines(self, f: _ToolField) -> list[tuple[str, str]]:
        """One field as lines at the margin — a checkbox and its label,
        the depth and its place, a text's label, the instructions' name
        alone — banded while the panel's
        cursor is on it. The depth with its dropdown open is its label
        over the closed list of places, the one under the cursor banded:
        the list opens where the field is, and nothing else moves. No
        trailing newline: the three windows join their lines themselves,
        and a trailing one would render as a blank row between two."""
        style = "class:row.selected" if self._tool_focused(f) else "class:preview.body"
        if f.kind == "display":
            return [(style, f"{f.text} {f.label}")]
        if f.kind == "prompt":
            return [(style, f"[ {f.label} ]")]  # a row to press: its text is shown only to edit
        if f.kind != "position":
            return [(style, f"{f.label}:")]
        if not self.choosing:
            return [(style, f"{f.label}: {f.text}")]
        setting = self.settings[self.cursor]
        return [
            ("class:preview.body", f"{f.label}:"),
            *(
                (
                    "class:row.selected" if i == self.choice else "class:preview.body",
                    f"  {p.text}",
                )
                for i, p in enumerate(setting.allowed_positions)
            ),
        ]

    def _tool_above(self) -> StyleAndTextTuples:
        """What the panel opens with: the setting's caption, then every
        field above its text and the text's label, one blank line
        between."""
        if not self.settings:
            return [("class:preview.muted", "nothing to preview")]
        setting = self.settings[self.cursor]
        width = max(10, self._preview_inner_width())
        out = _caption(_ABOUT[setting.name], _LEAD.get(setting.name, ""), width)
        above, _, text = self._tool_parts()
        for f in [*above, *([text] if text is not None else [])]:
            out += [("class:preview.body", "\n\n"), *_joined(self._tool_lines(f))]
        return out

    def _tool_text(self) -> StyleAndTextTuples:
        """The reminder's text as read, or the key's standing, where the
        editor opens over it."""
        _, _, text = self._tool_parts()
        if text is None:
            return []
        width = max(10, self._preview_inner_width())
        body = wrap_text(text.text or "(nothing yet)", width)
        return _joined([("class:preview.body", line) for line in body])

    def _tool_below(self) -> StyleAndTextTuples:
        """The fields under the text — the depth, last — each after one
        blank line."""
        _, below, _ = self._tool_parts()
        lines: list[tuple[str, str]] = []
        for f in below:
            lines += [("class:preview.body", ""), *self._tool_lines(f)]
        return _joined(lines)

    def _tool_has_text(self) -> bool:
        return self.tab == "tools" and self._tool_parts()[2] is not None

    def _enter_panel(self) -> None:
        """Into the panel — Enter or Tab on the list: the cursor moves to
        the setting's fields, the list's row stays banded behind it, and
        only Esc brings it back."""
        if self.settings and self._tool_fields(self.settings[self.cursor]):
            self.in_panel = True
            self.field = 0

    def _toggle_checkbox(self) -> None:
        """Space: on the list, the setting's own switch; in the panel, the
        checkbox under the cursor, where there is one."""
        if not self.settings:
            return
        setting = self.settings[self.cursor]
        if not self.in_panel:
            self._write_setting(setting, enabled=not setting.enabled)
            return
        f = self._tool_field()
        if f is not None and f.kind == "display":
            self._write_setting(setting, display_notes=not setting.display_notes)

    def _forget_key(self) -> None:
        """Delete on the web search key's field, outside the editor:
        forget the key — the file and the running app both, the
        models screen's Del. Bare, the field has nothing to forget."""
        f = self._tool_field()
        if f is None or f.kind != "key" or not self.web_search.has_key:
            return
        try:
            self.notice = self.api.integrations.web_search.update(api_key="").notice
        except Refused as e:
            self.notice = str(e)
            return
        self._reload_settings()

    def _move_field(self, delta: int) -> None:
        fields = self._tool_fields(self.settings[self.cursor]) if self.settings else []
        if fields:
            self.field = max(0, min(len(fields) - 1, self.field + delta))

    def _act_on_tool_field(self) -> None:
        """Enter on a field: a checkbox toggles, the depth opens its
        dropdown, a reminder edits in place, the instructions open the
        prompt over the panel."""
        f = self._tool_field()
        if f is None:
            return
        setting = self.settings[self.cursor]
        if f.kind == "display":
            self._write_setting(setting, display_notes=not setting.display_notes)
        elif f.kind == "position":
            places = setting.allowed_positions
            at = setting.injection_position
            self.choice = places.index(at) if at in places else 0
            self.choosing = True
        else:
            self._start_edit()

    def _choose_place(self, *, confirm: bool) -> None:
        """The dropdown closes: Enter writes the place under the cursor,
        Esc leaves the setting where it was."""
        self.choosing = False
        if not confirm:
            return
        setting = self.settings[self.cursor]
        self._write_setting(setting, position=setting.allowed_positions[self.choice])

    def _write_setting(
        self,
        setting: StorySetting,
        *,
        enabled: bool | None = None,
        position: InjectionPosition | None = None,
        display_notes: bool | None = None,
        reminder_text: str | None = None,
    ) -> None:
        """One field of one setting through `api.stories.settings`. Nothing
        it says is shown — the row shows where the setting now stands,
        once the tab has read the story again; a refusal is kept in
        `notice`, drawn nowhere."""
        try:
            self.api.stories.settings.update(
                self.story_id,
                setting.name,
                enabled=UNCHANGED if enabled is None else enabled,
                position=UNCHANGED if position is None else position,
                display_notes=UNCHANGED if display_notes is None else display_notes,
                reminder_text=UNCHANGED if reminder_text is None else reminder_text,
            )
        except Refused as e:
            self.notice = str(e)
            return
        self._reload_settings()


def _checkbox(on: bool) -> str:
    return _CHECKED if on else _UNCHECKED


def _caption(text: str, lead: str, width: int) -> StyleAndTextTuples:
    """A setting's caption wrapped to `width`, a newline between its
    lines and none after (as `_joined` lays lines), the `lead` it opens
    with — where it has one — in bold, across the lines it wraps to."""
    out: StyleAndTextTuples = []
    remaining = len(lead)
    lines = wrap_text(text, width)
    for i, line in enumerate(lines):
        head, tail = line[:remaining], line[remaining:]
        remaining = max(0, remaining - len(line) - 1)  # the space the wrap took
        if head:
            out.append(("class:preview.body bold", head))
        if tail or not head:
            out.append(("class:preview.body", tail))
        if i < len(lines) - 1:
            out.append(("class:preview.body", "\n"))
    return out


def _joined(lines: list[tuple[str, str]]) -> StyleAndTextTuples:
    """Styled lines as one text, a newline BETWEEN them and none after:
    a window sized to its content counts a trailing newline as a row."""
    out: StyleAndTextTuples = []
    for i, (style, text) in enumerate(lines):
        out.append((style, text if i == len(lines) - 1 else text + "\n"))
    return out
