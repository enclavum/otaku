/* The shapes every screen is built from: the list-and-detail browser,
   the editors that open where the text already is, and the ask dialogs.

   The browser takes its rows and its drawing callbacks and owns nothing
   else. A grouped list (the model picker) hands in `groupOf`/`drawGroup`
   and the captions are drawn between the rows without ever being rows:
   the cursor walks `.otk-row` alone.

   The kit finds its parts by structural hooks, not by look: `[data-list]`
   is the scroller it fills, `[data-detail]` the pane beside it,
   `[data-note]` the footer's one slot. A panel with two lists on two
   tabs hands in `root`, the pane its browser lives in. */

import { $, $$, autosize, element, span } from "./dom.js";
import { tell } from "./status.js";

/* Every popup in the markup, by the command that opens it — here, so no
   screen module has to import chrome from a sibling screen module. */
export const popups = new Map($$("dialog[data-popup]").map((d) => [d.dataset.popup, d]));

export function closeAll() {
  for (const dialog of $$("dialog[open]")) dialog.close();
}

export function footnote(popup, text) {
  /* The footer's fact slot: the panel's standing fact, and what a WRITE
     answered with — carried into the redraw the write triggers, or the
     standing fact goes back over the answer milliseconds later. Answers
     whether there was a slot: an ask dialog has no footer, and its
     notices go to the flow instead. */
  const slot = $("[data-note]", popup);
  if (slot) slot.textContent = text;
  return Boolean(slot);
}

/** A screen's own action, answered for. Every `on*` handler and every
    promise nobody awaits goes through here: otherwise a request that
    fails inside a popup is an unhandled rejection, and the reader is
    told nothing at all. The stack goes to the console — a failure here
    is a bug, not an answer. */
export function guard(action) {
  if (!action) return action;
  return (...args) => {
    try {
      return Promise.resolve(action(...args)).catch(failed);
    } catch (e) {
      failed(e);
      return undefined;
    }
  };
}

function failed(e) {
  console.error(e);
  report(String(e?.message ?? e));
}

/** A sentence put where the reader is looking: the open screen's
    footnote, else the status line. Failures and refusals share the
    routing — said in the flow, a screen's answer is said BEHIND the
    modal covering it, and the write that did not happen reads as a
    click that did nothing — but only a failure logs. */
function report(sentence, kind = "otk-error") {
  const open = $$("dialog[open]").at(-1);
  if (open && footnote(open, sentence)) return;
  tell(sentence, kind);
}

/** One live wiring per OWNER — a popup, or one pane of a tabbed popup
    whose tabs stand at once: a screen built again — a lens, a save —
    drops the last one's listeners before adding its own. The controller
    hangs on the owner, which is what outlives the call. */
export function wiring(owner) {
  owner._wiring?.abort();
  return (owner._wiring = new AbortController()).signal;
}

/** The filter's keys, wired ONCE for every screen that has one. The Esc
    ladder is innermost-first, and a filter with text in it is a depth of
    its own: Esc empties it and goes no further, not even to the popup's
    own keydown — which is why this registers BEFORE it. `/` walks in
    from anywhere that is not already a field. */
export function wireFilter(popup, { refilter, focus }, signal, root = popup) {
  const filter = $(".otk-filter", root);
  if (!filter) return;
  filter.value = "";
  filter.addEventListener("input", () => refilter(filter.value), { signal });
  popup.addEventListener(
    "keydown",
    (event) => {
      if (event.key === "Escape" && filter.value) {
        event.preventDefault();
        event.stopImmediatePropagation();
        filter.value = "";
        refilter("");
        focus();
      } else if (event.key === "/" && !event.target.matches("input, textarea")) {
        event.preventDefault();
        filter.focus();
      }
    },
    { signal },
  );
}

export function browser(popup, options) {
  /* Every callback a screen hands in reaches the session and can fail,
     so every one of them is answered for here. */
  const { rows, drawRow, drawPreview, groupOf, drawGroup, search, onKey, empty } = options;
  const onOpen = guard(options.onOpen);
  const onDelete = guard(options.onDelete);
  const onEdit = guard(options.onEdit);
  const onTab = guard(options.onTab);
  const onPivot = guard(options.onPivot);
  const onMove = options.onMove;
  const onBack = guard(options.onBack);
  // The pane this browser lives in: the whole popup for a single-view
  // panel, one `[data-pane]` of it for a tabbed one.
  const root = options.root ?? popup;
  const list = $("[data-list]", root);
  const preview = $("[data-detail]", root);
  /* The verbs under the pane, filled by the SELECTED row's `drawPreview`
     — so with no row selected they must be empty, or Delete is aimed at
     a row nobody can see. */
  const actions = $("[data-actions]", root);
  let shown = rows;
  let cursor = 0;
  let filtering = "";

  // Owned by the pane when there is one: the dossier's tabs are built
  // once and stand together, so one tab's list must not unwire another's.
  const signal = wiring(root);

  /* Two steps, and the split is what makes a double click possible: rows
     are BUILT when the data changes and only MARKED when the cursor
     moves. Rebuilding on every selection replaces the row under the
     pointer between the two clicks, and `dblclick` fires only when both
     land on the same element. */
  function mark() {
    // captions are not rows: the cursor walks `.otk-row` alone, which
    // keeps a grouped list's index math straight
    const drawn = $$(".otk-row", list);
    drawn.forEach((row, i) => {
      row.setAttribute("aria-selected", String(i === cursor));
    });
    // A screen without a per-row preview keeps whatever it drew.
    if (drawPreview) preview.replaceChildren(...(shown.length ? drawPreview(shown[cursor]) : []));
    if (shown.length) onMove?.(shown[cursor], cursor, shown.length);
    drawn[cursor]?.scrollIntoView({ block: "nearest" });
    // A rebuild destroys the focused row and focus falls to `body`,
    // outside the dialog, where no key reaches the handler below.
    if (!popup.contains(document.activeElement)) list.focus();
  }

  function paint() {
    const nodes = [];
    /* A list with nothing in it says what is missing and the way out: a
       first run and a filter that matched nothing are different
       absences, and a blank column tells a reader neither. */
    if (!shown.length && empty) {
      const { line, hint: way } = empty(Boolean(filtering));
      const box = element("div", "otk-empty");
      box.append(span("otk-empty__line", line));
      if (way) box.append(span("otk-empty__hint", way));
      list.replaceChildren(box);
      if (drawPreview) preview.replaceChildren();
      actions?.replaceChildren();
      return;
    }
    shown.forEach((item, i) => {
      // A caption is drawn before the first row under it and never
      // selected, so a group a filter emptied drops out with its rows.
      if (groupOf && (i === 0 || groupOf(item) !== groupOf(shown[i - 1]))) {
        nodes.push(...[drawGroup(groupOf(item))].flat());
      }
      const button = drawRow(item);
      button.addEventListener("click", () => {
        cursor = i;
        mark();
      });
      // whatever `enter` does on a row, this does too
      button.addEventListener("dblclick", () => onOpen?.(shown[i]));
      nodes.push(button);
    });
    list.replaceChildren(...nodes);
    mark();
  }

  let asked = 0;

  async function refilter(raw) {
    /* One request per keystroke, and they can answer out of order. Each
       carries its turn: only the newest paints, and none paints after
       this browser has been replaced. */
    const mine = ++asked;
    const needle = raw.trim().toLowerCase();
    filtering = needle;
    let matched;
    if (search && needle) {
      /* Answered below both frontends — the listing's `q` owns the union
         of buried content and the row's own face, so this browser and
         the terminal's can never find different stories. A screen
         without a `search` filters its rows' own haystack. */
      const found = new Set(await search(needle));
      matched = rows.filter((item) => found.has(item.id));
    } else {
      matched = needle ? rows.filter((item) => item.haystack.includes(needle)) : rows;
    }
    if (mine !== asked || signal.aborted) return;
    shown = matched;
    cursor = Math.min(cursor, Math.max(0, shown.length - 1));
    paint();
  }

  // Registered first, so the filter's Esc outranks the ladder below.
  wireFilter(popup, { refilter, focus: () => list.focus() }, signal, root);
  popup.addEventListener(
    "keydown",
    (event) => {
      // A list in a pane that is not the one shown keeps its listeners
      // and answers nothing: the keys belong to the tab in front.
      if (root !== popup && root.hidden) return;
      /* Esc reaching here found the filter empty (`wireFilter` consumed
         it otherwise): what is left inside is a drill-in's way back,
         and past that the popup itself, which `app.js` closes. */
      if (event.key === "Escape") {
        if (onBack) {
          event.preventDefault();
          event.stopPropagation();
          onBack();
        }
        return;
      }
      // a key with a modifier belongs to the browser: ⌘R is a reload
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (event.target.matches("input, textarea")) return;
      // A control in the detail pane carries its own Enter; taking the
      // key here would run the LIST's action instead.
      if (event.target.closest("[data-detail], [data-reading], [data-margin]")) return;
      if (event.key === "ArrowRight" && onPivot && shown.length) {
        event.preventDefault();
        onPivot(shown[cursor]);
      } else if (event.key === "Tab" && onTab) {
        event.preventDefault();
        onTab();
      } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const step = event.key === "ArrowDown" ? 1 : -1;
        cursor = Math.max(0, Math.min(shown.length - 1, cursor + step));
        mark();
      } else if (event.key === "Enter" && shown.length) {
        event.preventDefault();
        onOpen?.(shown[cursor]);
      } else if (event.key === "e" && onEdit && shown.length) {
        // the one row-level write that is not what Enter does
        event.preventDefault();
        onEdit(shown[cursor]);
      } else if ((event.key === "Delete" || event.key === "Backspace") && shown.length) {
        event.preventDefault();
        onDelete?.(shown[cursor]);
      } else if (onKey?.(event, shown[cursor])) {
        // a screen's own key, asked LAST so it cannot shadow the kit's
        event.preventDefault();
      }
    },
    { signal },
  );

  paint();
  /* Where a browser is read from. `showModal` focuses the first
     focusable descendant — the filter, where every motion key below is
     ignored — unless something claims it with `autofocus`. The direct
     call covers a browser rebuilt on a popup already open. */
  list.setAttribute("autofocus", "");
  list.focus();
  return {
    select(matches) {
      const found = shown.findIndex(matches);
      if (found >= 0) {
        cursor = found;
        mark();
      }
    },
    /** The row the cursor is on — what a header button acts on, since
        a button outside the list cannot know where the list is. */
    current() {
      return shown[cursor];
    },
    /** And what `enter` does to it, for the button that says so. */
    open() {
      if (shown.length) onOpen?.(shown[cursor]);
    },
  };
}

/** A text that is edited where it is READ: the field IS the text. It
    carries the class the paragraph would have, so it inherits that face,
    measure and box exactly and nothing reflows when a reader opens one.

    Ctrl+S saves, a one-line value is finished by Enter, and Esc puts the
    stored text back. Clicking away SAVES, as Enter would — a value typed
    and walked away from is a value meant; a field wrapped by `edited` is
    closed by that save too.

    `save` is handed the new text and returns the backend's ANSWER: one
    marked `refused` keeps the field open with the words still in it. */
export function editable(className, { text, save: write, readonly = false, line = false }) {
  const field = element("textarea", `${className} otk-editable`.trim());
  field.value = text ?? "";
  field.readOnly = readonly;
  if (readonly) return field;
  field.spellcheck = false;

  field._restore = () => {
    field.value = text ?? "";
  };
  /* What the store holds now, when a write settled on other words than
     were typed — a number normalised, a cleared value's default: the
     field shows them, and they are what Esc puts back from here on. */
  field._settle = (stored) => {
    text = stored ?? "";
    field.value = text;
  };
  // what the store holds, as far as this field knows
  field._stored = () => text;
  /* Answers whether the field is FINISHED: a refusal is reported and
     leaves it open with the words still in it, which a write that did
     not happen must never cost. `save` returns the backend's answer for
     exactly that — a refusal RESOLVES (200 with the flag), it does not
     throw, and reading the flag here is what keeps the promise. The
     save callbacks skip their redraw on a refusal for the same reason:
     a rebuilt pane would destroy the open editor under the caret. */
  let pending = null;
  field._commit = () => {
    /* One write at a time: Enter is followed by the blur that finishing
       causes, and a click away can land while Enter's write is still in
       flight — both must not write the same words twice. */
    pending ??= commit().finally(() => (pending = null));
    return pending;
  };
  const commit = async () => {
    if (field.value === text) return true;
    try {
      const answer = await write(field.value);
      if (answer?.refused) {
        report(answer.notice, "");
        return false;
      }
      /* What was saved IS the stored text from here on. Without this,
         finishing a field blurs it and puts back what it held before —
         the old value standing on screen until the screen the write
         asked for arrives to replace it. */
      text = field.value;
      return true;
    } catch (e) {
      failed(e);
      return false;
    }
  };

  /* The wrapper, when there is one. Its presence IS the difference: a
     field with verbs is closed by them alone, one without them by
     leaving it. */
  const wrapper = () => field.closest(".otk-edit");
  const finish = () => (wrapper() ? wrapper()._close() : field.blur());

  field.addEventListener("blur", async () => {
    /* Leaving a field finishes it; refused, the words stay where they
       are, as they do after Enter. A wrapped one is finished only while
       open — its own closing blurs it — and not when the WINDOW loses
       focus: the reader went to copy something, or to a file dialog. */
    const box = wrapper();
    if (!box) return field._commit();
    if (!box.dataset.editing || !document.hasFocus()) return;
    if (await field._commit()) box._close();
  });
  field.addEventListener("keydown", async (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      field._restore();
      finish();
      return;
    }
    // A one-line value is finished by Enter; a passage of prose needs
    // Enter for its paragraphs, and is finished by ctrl+s.
    const commits =
      (line && event.key === "Enter" && !event.shiftKey) ||
      (event.key === "s" && (event.metaKey || event.ctrlKey));
    if (commits) {
      event.preventDefault();
      if (await field._commit()) finish();
    }
  });
  return field;
}

/** A field with the line that belongs under it: the name of what it
    holds, and the verbs that open and close it.

    Until `edit` is taken the field is INERT — it cannot be typed in,
    tabbed to, or opened by a single click on the text; a double click
    takes the verb, with the caret where the pointer was — and `save`,
    `cancel` or a click away closes it again. The line holds one height
    in every state, so no state of the field moves a word around it.

    Two MODES, two ELEMENTS: what is read is a paragraph, what is edited
    is a field. A readonly field standing in for the paragraph would
    still take a caret and a selection — prose that behaves as an input.
    The pair carries one class, so both wear the same face at the same
    measure, and the stylesheet shows exactly one of them.

    `field` may already be such a pair (`story.reader`), or a HEADING
    that cannot be edited at all — then it takes the label line and no
    verbs, which is how a name only the extractor writes keeps the same
    box as the title beside it. */
export function edited(field, name, extra = "") {
  const box = element("div", `otk-edit ${extra}`.trim());
  const input = field.matches("textarea") ? field : $("textarea.otk-editable", field);
  if (!input) {
    const label = element("span", "otk-edit__hint");
    label.append(span("", name));
    box.append(field, label);
    return box;
  }

  /* A real button, because a verb is the ONLY way into a closed field —
     itself out of the tab order — and the keyboard has to reach it. */
  const verb = (kind, word) => {
    const it = element("button", `otk-edit__verb otk-edit__verb--${kind}`, word);
    it.type = "button";
    // Taking a verb must not move the caret out of the field: a refused
    // save leaves it open, cursor where the reader left it.
    it.addEventListener("mousedown", (event) => event.preventDefault());
    return it;
  };
  const open = verb("edit", "edit");
  const save = verb("save", "save");
  const cancel = verb("cancel", "cancel");

  /* The reading half. An empty value says what is absent in words, the
     way every other unset value does, rather than collapsing the box. */
  let shown = field;
  if (field === input) {
    const read = element("p", `${input.className} otk-typeset`);
    shown = element("div", "otk-reader");
    shown.append(read, input);
    shown._repaint = () => {
      const bare = !input.value.trim();
      read.textContent = bare ? input.placeholder : input.value;
      read.classList.toggle("otk-absent", bare);
    };
  }

  box._close = () => {
    delete box.dataset.editing;
    input.readOnly = true;
    input.tabIndex = -1;
    input.blur();
    shown._repaint?.();
  };
  open.addEventListener("click", () => {
    box.dataset.editing = "true";
    input.readOnly = false;
    input.tabIndex = 0;
    // shown only now, so a browser without `field-sizing` sizes it now
    autosize(input);
    input.focus();
    // Opened to be continued, not retyped: the caret lands at the end.
    input.setSelectionRange(input.value.length, input.value.length);
  });
  save.addEventListener(
    "click",
    guard(async () => {
      // A refused write leaves the field open, with the words in it.
      if (await input._commit()) box._close();
    }),
  );
  cancel.addEventListener("click", () => {
    input._restore();
    box._close();
  });

  /* A double click on what is read is the verb, taken, with the caret
     where the pointer was rather than at the end — measured before the
     verb swaps the paragraph out for the field. The second press would
     select a word first, for a frame before the swap. */
  const read = $(".otk-typeset", shown);
  read.addEventListener("mousedown", (event) => {
    if (event.detail > 1) event.preventDefault();
  });
  read.addEventListener("dblclick", (event) => {
    const at = drawnOffset(read, event.clientX, event.clientY);
    open.click();
    if (at === null) return;
    const caret = storedOffset(input.value, read.textContent, at);
    input.setSelectionRange(caret, caret);
  });

  const verbs = element("span", "otk-edit__verbs");
  verbs.append(open, save, cancel);
  const hint = element("span", "otk-edit__hint");
  hint.append(span("", name), verbs);
  box.append(shown, hint);
  box._close();
  return box;
}

/** Where in a drawn paragraph the pointer is, as a count of the
    characters before it; null when it is not over the paragraph's text. */
function drawnOffset(paragraph, x, y) {
  const position = document.caretPositionFromPoint?.(x, y);
  const range = position ? null : document.caretRangeFromPoint?.(x, y);
  const node = position?.offsetNode ?? range?.startContainer;
  if (!node || !paragraph.contains(node)) return null;
  const before = document.createRange();
  before.setStart(paragraph, 0);
  before.setEnd(node, position?.offset ?? range.startOffset);
  return before.toString().length;
}

/** The place in the stored text of a place in the drawn one. What is drawn
    is the stored text, or the stored text with typeset marks taken out —
    every drawn character is a stored one, in order — so walking the two
    together finds it. A caret before a drawn character stands after the
    marks that open it; a placeholder drawn for an empty value lands at 0. */
function storedOffset(stored, drawn, at) {
  let j = 0;
  for (let i = 0; i < at && i < drawn.length; i++) {
    while (j < stored.length && stored[j] !== drawn[i]) j++;
    j++;
  }
  if (at < drawn.length) while (j < stored.length && stored[j] !== drawn[at]) j++;
  return Math.min(j, stored.length);
}

/** One of the markup's ask dialogs, by name. Its buttons carry
    `data-choice`; the answer is which one was pressed, or null for Esc
    and the scrim. Copy is never read — renaming a button must not
    change what it does. */
export function ask(name, fill) {
  const dialog = $(`dialog[data-dialog="${name}"]`);
  fill?.(dialog);
  return new Promise((resolve) => {
    const wired = new AbortController();
    const settle = (choice) => {
      wired.abort();
      dialog.close();
      resolve(choice);
    };
    for (const button of $$("[data-choice]", dialog)) {
      button.addEventListener("click", () => settle(button.dataset.choice), {
        signal: wired.signal,
      });
    }
    /* Enter is the dialog's ACTION wherever the focus is, and never
       cancels: leaving without answering is Esc's alone. A textarea
       keeps its own Enter, where the key is a newline. */
    dialog.addEventListener(
      "keydown",
      (event) => {
        if (event.key !== "Enter" || event.shiftKey) return;
        if (event.target.matches("textarea")) return;
        const action = $$("[data-choice]", dialog).findLast(
          (button) => button.dataset.choice !== "cancel",
        );
        if (!action) return;
        event.preventDefault();
        settle(action.dataset.choice);
      },
      { signal: wired.signal },
    );
    dialog.addEventListener("close", () => settle(null), { signal: wired.signal, once: true });
    dialog.showModal();
  });
}
