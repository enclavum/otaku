/* The input: what a submitted line becomes, and the prefix menu that
   offers the story's own typed openers.

   `submit` is the one door — Enter and the Send button both call it, so
   the button never has to synthesize a keystroke to reach the logic.

   Everything submitted here is STORY. The menu offers the story's own
   framing — the openers where a line begins, the inline words where the
   caret is mid-sentence — and nothing else: a command is a button, and
   the box has never been a place to type one. */

import * as api from "./api.js";
import { midReply, playLine, run } from "./commands.js";
import { $, element, setValue, span } from "./dom.js";
import { rows as syntaxRows } from "./table.js";
import { tell } from "./status.js";
import { stopPlaying } from "./transcript.js";

const composer = $(".otk-composer__input textarea");
const menu = $(".otk-prefixes");

// A finger for a pointer means an on-screen keyboard — the test `app.js`
// makes before it focuses the box.
const _TOUCH = window.matchMedia("(pointer: coarse)");

/* What the menu says about each opener, and which half of the language
   it belongs to. A menu row has one line to say what a word DOES —
   `/help` is where the table's full sentence is read — so the caption is
   written here, short and lowercase. Keyed by the bare token, so the
   inline form of a word (`… /ooc`) reads the same as the opening one. */
const _MENU = {
  "/me": ["Take a turn", "your own action, narrated"],
  "/you": ["Take a turn", "speak to someone present"],
  "/ooc": ["Speak to the narrator", "a note, never played"],
  "/cue": ["Speak to the narrator", "steer the next reply"],
  // the caption matches the shared table's group label for /roll
  // (`commands.GROUP_LABELS["special"]`), so the two menus file it alike
  "/roll": ["Special", "roll real dice; the model narrates"],
};

/** The word itself, without the `… ` the table marks an inliner with: the
    menu only ever offers one of the two forms, so the mark says nothing a
    reader needs here. */
const _bare = (token) => token.replace(/^…\s*/, "");

let offered = [];
let picked = 0;

/* What has been sent from this box, newest last, and where the reader is
   in it. `at === null` means "not walking": the arrows walk only when
   there is nothing half-typed to lose, so a multi-line message keeps its
   own caret movement.

   The lines are the STORE's — the history the terminal prompt walks —
   primed at boot and recorded back, so a reload starts where it left. */
const history = [];
let at = null;

/** Put the caret back in the box. The page's resting state is a reader
    about to write, so every screen that closes hands the keys back to
    it — and a disabled box (otaku gone) is left alone. */
export function focusComposer() {
  if (!composer.disabled) composer.focus();
}

/** The store's recent lines, most recent first — called at every boot,
    because a restarted otaku may have played elsewhere since. */
export function primeHistory(lines) {
  history.length = 0;
  history.push(...[...lines].reverse());
  at = null;
}

/** One submitted line, wherever it came from. */
export function submit(line) {
  const said = line.trim();
  // A line typed during a reply is refused the way every other door
  // refuses it — silence here trains the reader to press Enter twice.
  if (!said || midReply()) return;
  if (history.at(-1) !== said) history.push(said);
  // Into the store's history too (blanks and immediate repeats are the
  // session's to skip) — fire-and-forget: the submission itself is the
  // event, and a lost record must not delay or fail it.
  api.recordHistory(said).catch(() => {});
  at = null;
  setValue(composer, "");
  hideMenu();
  // Everything typed here is STORY: the framing words ride inside the
  // line and `context.syntax` reads them at the far end. A line opening
  // with an unknown slash word is prose that starts with a slash.
  playLine(said);
}

export function wire() {
  composer.addEventListener("input", () => {
    // A line about the last attempt is over the moment the next one is
    // being typed.
    tell("");
    updateMenu();
  });
  composer.addEventListener("blur", hideMenu);
  composer.addEventListener("keydown", onKey);
  // Sent by the button, the caret comes back to the box: the next line
  // is typed, not clicked for.
  $(".otk-composer [data-send]")?.addEventListener("click", () => {
    submit(composer.value);
    focusComposer();
  });
  // The same place, the other half of the turn: what the model is doing
  // is stopped where it was asked for.
  $(".otk-composer [data-stop]")?.addEventListener("click", stopPlaying);
  // The hint that opens the menu by pointer — for the reader who has not
  // found the slash yet.
  $(".otk-composer [data-prefixes]")?.addEventListener("click", () => {
    if (menu.hidden) {
      composer.focus();
      updateMenu({ everything: true });
    } else {
      hideMenu();
    }
  });
}

function onKey(event) {
  // While the menu is up it owns Enter, the arrows and the page keys.
  if (!menu.hidden && offered.length) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      picked = (picked + (event.key === "ArrowDown" ? 1 : offered.length - 1)) % offered.length;
      paintMenu();
      return;
    }
    if (event.key === "PageDown" || event.key === "PageUp") {
      // A page is what the box shows; the jump clamps at the ends
      // rather than wrapping, as page keys do everywhere.
      event.preventDefault();
      const row = $(".otk-prefix", menu);
      const step = row ? Math.max(1, Math.floor(menu.clientHeight / row.offsetHeight) - 1) : 1;
      picked =
        event.key === "PageDown"
          ? Math.min(offered.length - 1, picked + step)
          : Math.max(0, picked - step);
      paintMenu();
      return;
    }
    /* Enter takes the highlighted row unless the line already IS that
       row — a fully typed `/me` sends rather than re-completing. An
       inline word never claims Enter: the reader is mid-sentence, and
       Enter there is how the sentence is sent. */
    const typed = composer.value.trim();
    const settled = offered[picked]?.token === typed;
    if (event.key === "Tab" || (event.key === "Enter" && !settled && typed.startsWith("/"))) {
      event.preventDefault();
      accept(offered[picked]);
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      hideMenu();
      return;
    }
  }
  if (event.key === "Escape") {
    setValue(composer, "");
    at = null;
    return;
  }
  /* The two verbs beside the box, on the keys the hints advertise —
     answered only HERE, while the box has focus: anywhere else ctrl+r
     belongs to whatever owns it (and ⌘R stays the browser's reload). */
  if (event.ctrlKey && !event.metaKey && (event.key === "r" || event.key === "u")) {
    event.preventDefault();
    run(event.key === "r" ? "/regen" : "/undo");
    return;
  }
  if ((event.key === "ArrowUp" || event.key === "ArrowDown") && _walkable()) {
    event.preventDefault();
    _walk(event.key === "ArrowUp" ? -1 : 1);
    return;
  }
  if (event.key === "Enter" && !event.shiftKey) {
    /* On a touch screen Enter is the keyboard's only way to a new line,
       so there it writes one and Send sends; a keyboard attached to the
       device still sends with ctrl or ⌘. */
    if (_TOUCH.matches && !event.ctrlKey && !event.metaKey) return;
    event.preventDefault();
    submit(composer.value);
  }
}

// ---------- what was sent before ----------

function _walkable() {
  /* Only with nothing to lose: an empty box, or a line this walk put
     there. Anything the reader typed keeps the arrows for its caret. */
  if (!history.length) return false;
  return !composer.value.trim() || (at !== null && composer.value === history[at]);
}

function _walk(step) {
  // Down from a box nobody is walking has nothing to go forward TO.
  if (at === null && step > 0) return;
  const next = at === null ? history.length - 1 : at + step;
  if (next < 0) {
    at = 0;
  } else if (next >= history.length) {
    // Past the newest is the empty box the reader started from.
    at = null;
    setValue(composer, "");
    return;
  } else {
    at = next;
  }
  setValue(composer, history[at]);
  composer.setSelectionRange(composer.value.length, composer.value.length);
}

// ---------- the prefix menu ----------

/* The menu offers what the caret can take: a DIRECTION while the line is
   nothing but its slash word, and the INLINE words while the slash is
   inside a sentence. The rows come from the shared table either way, so
   the language appears here by existing; `… ` is how the table marks the
   inline half, and a reader types the bare word after it. */

const _INLINE = "… ";

function _prefixes() {
  return syntaxRows().map((row) => ({
    token: row.token.replace(_INLINE, ""),
    inline: row.token.startsWith(_INLINE),
    args: row.args,
  }));
}

/* Cast names where a direction expects one — the RULE is the
   terminal's (`terminal.prompt.completion._cast_rows`, its home, held
   there because the two menus must offer one gesture): names come from
   `api.lore.cast`, filter on the raw argument so spaced names match
   whole, none are offered past a `:`, and `/me` inserts `Name: ` where
   the others insert the bare name (the hint is a colon away). */

let _cast = null; // fetched when a name slot first asks; dropped with the menu
let _castAsked = 0;

function nameSlot() {
  /* The opener taking a NAME and the partial before the caret — null
     when the caret is anywhere else. One-line lines only, like the
     opener menu itself. */
  if (composer.value.includes("\n")) return null;
  const before = composer.value.slice(0, composer.selectionStart ?? composer.value.length);
  const m = before.match(/^\s*(\/[a-z]+)\s+([^:]*)$/);
  if (!m) return null;
  const spec = _prefixes().find((row) => !row.inline && row.token === m[1]);
  if (!spec || !spec.args.startsWith("NAME")) return null;
  return { token: m[1], partial: m[2], start: before.length - m[2].length };
}

async function offerNames({ token, partial, start }) {
  /* Per keystroke, answered out of order like the browser's filter:
     only the newest paints. The cast is read once per menu visit —
     `hideMenu` drops it, so a `/merge` a moment ago is not stale. */
  const mine = ++_castAsked;
  if (_cast === null) {
    try {
      _cast = (await api.cast()).characters;
    } catch {
      _cast = [];
    }
  }
  if (mine !== _castAsked) return;
  const needle = partial.toLowerCase();
  const insert = (name) => (token === "/me" ? `${name}: ` : `${name} `);
  const was = offered.map((spec) => spec.token).join(" ");
  const matched = _cast.filter((row) => row.name.toLowerCase().startsWith(needle));
  // Every match, in the menu's own scroll — the header carrying the
  // count once the cast runs past what fits at a glance.
  const caption = matched.length > 7 ? `Cast (${matched.length} total)` : "Cast";
  offered = matched.map((row) => ({
    isName: true,
    token: row.name,
    caption,
    insert: insert(row.name),
    start,
  }));
  if (offered.map((spec) => spec.token).join(" ") !== was) picked = 0;
  picked = Math.min(picked, Math.max(0, offered.length - 1));
  menu.hidden = offered.length === 0;
  paintMenu();
}

function typing() {
  /* The slash word the caret is in, and whether it opens the line. "" if
     the caret is not in one — the menu has nothing to offer then. */
  const before = composer.value.slice(0, composer.selectionStart ?? composer.value.length);
  const word = before.split(/\s/).pop() ?? "";
  if (!word.startsWith("/")) return { word: "", opens: false };
  return { word, opens: before.trimStart() === word && !composer.value.includes("\n") };
}

function updateMenu({ everything = false } = {}) {
  /* `everything` is the hint button: with the caret outside a slash
     word, position decides which half applies — a line being opened
     takes the directions, a sentence underway the inline words. */
  const names = everything ? null : nameSlot();
  if (names) {
    if (!offered[0]?.isName) {
      /* The openers must not keep standing — and answering Enter —
         while the cast is fetched: a beat with no menu over a stale
         accept. The names paint the moment the read returns. */
      offered = [];
      menu.hidden = true;
    }
    offerNames(names);
    return;
  }
  _castAsked++; // a paint in flight must not land over the openers
  const { word, opens } = typing();
  const was = offered.map((spec) => spec.token).join(" ");
  if (everything) {
    const before = composer.value.slice(0, composer.selectionStart ?? composer.value.length);
    const opening = !before.trim();
    offered = _prefixes().filter((spec) => spec.inline !== opening);
  } else {
    offered = !word
      ? []
      : _prefixes().filter((spec) => spec.inline !== opens && spec.token.startsWith(word));
  }
  // A different set of rows is a different question: keeping the old
  // position would preselect a prefix nobody navigated to, and Enter
  // would take it.
  if (offered.map((spec) => spec.token).join(" ") !== was) picked = 0;
  picked = Math.min(picked, Math.max(0, offered.length - 1));
  menu.hidden = offered.length === 0;
  paintMenu();
}

function paintMenu() {
  // The cast wears its own box: narrower, capped, scrolling.
  menu.classList.toggle("otk-prefixes--cast", Boolean(offered[0]?.isName));
  const rows = [];
  let heading = null;
  offered.forEach((spec, i) => {
    // Every token has a row in `_MENU` — held by the architecture test,
    // so a new framing word fails the suite instead of a blank caption.
    // A cast name is not a token: its caption is the cast's own, and
    // the row is the name alone — full width, never wrapped.
    const [group, means] = spec.isName
      ? [spec.caption, ""]
      : (_MENU[_bare(spec.token)] ?? [null, ""]);
    // A caption between the rows, wherever the half of the language changes.
    // It is not a row: the cursor walks `.otk-prefix` alone.
    if (group && group !== heading) {
      heading = group;
      const caption = element("span", "otk-prefixes__group");
      caption.append(span("otk-label", group));
      rows.push(caption);
    }
    const option = element("button", spec.isName ? "otk-prefix otk-prefix--name" : "otk-prefix");
    option.type = "button";
    option.setAttribute("role", "option");
    option.setAttribute("aria-selected", String(i === picked));
    option.append(span("otk-prefix__token", _bare(spec.token)));
    if (!spec.isName) option.append(span("otk-prefix__desc", means));
    if (i === picked) queueMicrotask(() => option.scrollIntoView({ block: "nearest" }));
    option.onmousedown = (event) => {
      event.preventDefault();
      accept(spec);
    };
    rows.push(option);
  });
  menu.replaceChildren(...rows);
}

function accept(spec) {
  if (spec.isName) {
    // The name replaces the argument typed so far; what follows it is
    // the rule's (`Name: ` after /me, the bare name elsewhere).
    const caret = composer.selectionStart ?? composer.value.length;
    setValue(composer, composer.value.slice(0, spec.start) + spec.insert + composer.value.slice(caret));
    composer.focus();
    const at = spec.start + spec.insert.length;
    composer.setSelectionRange(at, at);
    hideMenu();
    return;
  }
  /* A prefix is chosen to be written after, so the space comes with it.
     It replaces the slash word the caret is in — or lands at the caret
     when the menu was opened by the hint — and leaves the prompt around
     it alone. */
  const taken = `${spec.token} `;
  const caret = composer.selectionStart ?? composer.value.length;
  const before = composer.value.slice(0, caret);
  const word = before.split(/\s/).pop() ?? "";
  const opened = word.startsWith("/") ? before.length - word.length : caret;
  setValue(composer, composer.value.slice(0, opened) + taken + composer.value.slice(caret));
  composer.focus();
  composer.setSelectionRange(opened + taken.length, opened + taken.length);
  hideMenu();
  // A framing word that takes a name continues straight into the cast:
  // the menu follows the caret rather than waiting for a keystroke.
  updateMenu();
}

function hideMenu() {
  menu.hidden = true;
  offered = [];
  _cast = null;
}
