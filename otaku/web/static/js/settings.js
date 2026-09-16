/* The settings docket: the /set family as a slip of leaders — the value
   each knob stands at on the right, and under it the other values it
   could take. There is no control here that is not one of those words:
   clicking a word sets it, which is exactly what typing the command
   does, because the click puts the same value through the same setter
   and redraws from its answer. The slip and the typed form can never
   disagree.

   Two blocks, because where a value persists is a real distinction: the
   app's own settings, and what is kept per model — the thinking level
   and the parameters. */

import * as api from "./api.js";
import { editable, footnote, guard, popups, wiring } from "./browser.js";
import { $, $$, element, span } from "./dom.js";

// What a knob nobody has set reads as — the model's own value, or the
// model's own max context. Either is an absence, and is drawn like one.
const DEFAULT_VALUE = "default";

// The context limit's field accepts a count: digits alone.
const _COUNT = /^\d*$/;

/* What a parameter's field accepts while typed, from the type the backend
   states for the value and the floor it holds it to
   (`Settings.parameters[]`): an integer is digits, a float digits with
   one point, a minus only where the floor lets a value go below zero, a
   stop string anything. A pattern the whole value must match while it is
   typed, so the in-between states ("-", "0.") pass; the magnitude is the
   backend's to refuse, and `outOfRange` marks it meanwhile. */
function mask(parameter) {
  if (parameter.type === "str") return null;
  const sign = parameter.min === null || parameter.min < 0 ? "-?" : "";
  return new RegExp(parameter.type === "int" ? `^${sign}\\d*$` : `^${sign}\\d*\\.?\\d*$`);
}

/* Whether a typed value has left the parameter's bounds — marked while
   typing, refused by the backend on save. Nothing typed, or no number
   yet, is not out of range. */
function outOfRange(parameter, text) {
  if (parameter.min === null && parameter.max === null) return false;
  const number = Number(text);
  if (!text.trim() || Number.isNaN(number)) return false;
  return (
    (parameter.min !== null && number < parameter.min) ||
    (parameter.max !== null && number > parameter.max)
  );
}

/* An unset parameter's placeholder: the model's own value, and the
   bounds a typed one is held to, where there are any. */
function placeholder(parameter) {
  if (parameter.min === null) return DEFAULT_VALUE;
  const bounds =
    parameter.max === null
      ? `at least ${parameter.min}`
      : `${parameter.min} to ${parameter.max}`;
  return `${DEFAULT_VALUE} · ${bounds}`;
}

export async function openSettings(answered = "") {
  // Read before the docket shows: it takes its height from the slip, so
  // opened empty it stands minimal and grows when the answer lands. The
  // pickers open before their data because a catalog can take seconds;
  // this read cannot, and the dossier (story.js) opens the same way.
  const knobs = await api.settings();
  const popup = popups.get("/set");

  const global = element("div", "otk-v otk-v--md");
  global.append(section("Global", "app settings"));

  for (const name of ["verbose", "autocorrect", "notification"]) {
    global.append(
      knob(
        leader(name, toggle(knobs[name], (wanted) => setKnob(name, wanted))),
        element("p", "otk-note", about(name)),
      ),
    );
  }

  /* One number, edited where it is read. No cap is the model's whole
     window, which the backend spells 0 — an absence, so the field is
     EMPTY and says `default` the way an unset parameter does. Clearing
     it sets it: 0 goes out, and the same absence comes back. */
  const limit = editableLeader(
    "max_context",
    stands.max_context(knobs),
    (value, field) =>
      typedKnob(field, () => api.setSetting("max_context", value.trim() || "0"), stands.max_context),
    _COUNT,
  );
  limit.lastElementChild.placeholder = DEFAULT_VALUE;
  global.append(knob(limit, element("p", "otk-note", about("max_context"))));

  const perModel = element("div", "otk-v otk-v--md");
  perModel.append(section(knobs.model || "no model", "this model"));
  const params = element("div", "otk-v otk-v--sm");
  // The thinking level first: every level the model takes on one line,
  // the one it stands at marked, the rest a click away.
  params.append(
    leader("think", ladder(knobs.think_levels, knobs.think, (level) => setKnob("think", level))),
  );
  for (const parameter of knobs.parameters) {
    // A parameter nobody has set stands at the model's own value. That is
    // an absence, so it is the field's PLACEHOLDER and not its text — and
    // it reads the same whether it was never set or was just cleared.
    const line = editableLeader(
      parameter.name,
      parameter.value,
      (value, field) =>
        typedKnob(
          field,
          // An emptied field is the model's own default, which is an
          // absence and has its own door.
          () =>
            value.trim()
              ? api.setParameter(parameter.name, value.trim())
              : api.resetParameter(parameter.name),
          (fresh) => stands.parameter(fresh, parameter.name),
        ),
      mask(parameter),
    );
    const field = line.lastElementChild;
    field.placeholder = placeholder(parameter);
    field.addEventListener("input", () => {
      field.classList.toggle("is-out", outOfRange(parameter, field.value));
    });
    params.append(line);
  }
  perModel.append(params);

  $("[data-knobs]", popup).replaceChildren(global, element("div", "otk-rule--double"), perModel);
  footnote(popup, answered);
  // After `showModal`: its focusing steps would land on the docket
  // body's `autofocus`, and a focus() into a still-closed dialog is
  // silently dropped — the knobs' focus only sticks once it is open.
  if (!popup.open) popup.showModal();
  knobKeys(popup);
}

/* What a typed knob's field shows, read off the settings: the limit is
   a figure or nothing (0 is the model's own max context — an absence, so the
   field is EMPTY and says `default` the way an unset parameter does), a
   parameter its value or nothing. */
const stands = {
  max_context: (knobs) => (knobs.max_context ? String(knobs.max_context) : ""),
  parameter: (knobs, name) => knobs.parameters.find((p) => p.name === name)?.value ?? "",
};

function section(name, count) {
  const head = element("div", "otk-section", name);
  head.append(span("otk-section__count", count));
  return head;
}

function knob(line, aside) {
  const box = element("div", "otk-v otk-v--xs");
  box.append(line, aside);
  return box;
}

function leader(label, value, kind = "") {
  const line = element("div", "otk-leader");
  line.append(span("", label), value instanceof Node ? value : span(kind, String(value)));
  return line;
}

function ladder(levels, current, set) {
  /* Every level the model takes, in the shared ladder's order — the one
     it stands at marked, the rest a click away: the value is a word, and
     the word is the control, as on a toggle. Wraps where the row is too
     narrow for all of them. */
  const box = element("span", "otk-ladder");
  levels.forEach((level, i) => {
    if (i) box.append(span("otk-faint", "·"));
    const step = element("button", "otk-toggle", level);
    step.type = "button";
    step.setAttribute("aria-checked", String(level === current));
    step.setAttribute("role", "radio");
    step.onclick = guard(() => set(level));
    box.append(step);
  });
  return box;
}

function toggle(on, set) {
  /* on/off written out, the one in force marked: the value is a word,
     and the word is the control. */
  const box = element("span", "otk-keys");
  for (const [word, wanted] of [
    ["on", true],
    ["off", false],
  ]) {
    if (wanted === false) box.append(span("otk-faint", "/"));
    const option = element("button", "otk-toggle", word);
    option.type = "button";
    option.setAttribute("aria-checked", String(on === wanted));
    option.onclick = guard(() => set(word));
    box.append(option);
  }
  return box;
}

function editableLeader(label, value, save, mask = null) {
  /* A value edited where it is READ: the figure IS the field. A knob is
     one line long, so Enter finishes it, and so does leaving it; Esc
     puts it back — none written down, a slip having no room to explain
     its own keys. `save` is handed the words and the field, so the
     write can settle the field afterwards; `mask` is what the field
     accepts while typed (`_MASKS`). */
  const row = element("div", "otk-leader");
  const field = editable("", {
    text: value,
    save: (typed) => save(typed, field),
    line: true,
    mask,
  });
  row.append(span("", label), field);
  return row;
}

/** The caption under a knob. A slip has room for a caption and not for
    a sentence: `/help` prints the table's full row for the same
    command, and this says it in the space a leader leaves. Lowercase,
    as a caption is. */
const _ABOUT = {
  verbose: "the stats line after each reply",
  autocorrect: "settle names to the cast's spelling",
  notification: "a sound when a reply lands",
  max_context: "limit the model context size",
};

function about(name) {
  return _ABOUT[name] ?? "";
}

function knobKeys(popup) {
  /* A slip is a column of controls, so the arrows move focus between
     them and Enter presses the one you are on. Without this the docket
     opens with `Close` focused and Enter shuts it. */
  const signal = wiring(popup);
  const controls = () => $$("button:not(.otk-close)", popup);
  popup.addEventListener(
    "keydown",
    (event) => {
      // ⌘R is a reload, not a reset: a key with a modifier belongs to
      // the browser, and `preventDefault` on one is taking it.
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (event.target.matches("input, textarea")) return;
      const all = controls();
      const at = all.indexOf(document.activeElement);
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const step = event.key === "ArrowDown" ? 1 : -1;
        all[Math.max(0, Math.min(all.length - 1, at + step))]?.focus();
      }
    },
    { signal },
  );
  /* Not the close button, which `showModal` would focus — and then
     Enter closes the docket the reader came to edit in. Focused
     outright as well as marked: every control here is a write, a write
     rebuilds the slip and drops the focused control, and an already-open
     dialog honours no autofocus. */
  const first = controls()[0];
  first?.setAttribute("autofocus", "");
  first?.focus();
}

async function setKnob(name, value) {
  /* A BUTTON's write — the ladder, a toggle. The slip is rebuilt from
     the answer rather than patched: a setter may settle on a value the
     reader did not choose, and the read is the only thing that knows.
     A REFUSAL skips the rebuild, and the rebuild is awaited, so the
     guard on the click catches a redraw that fails rather than leaving
     a stale slip with nobody told. */
  const answer = await api.setSetting(name, value);
  if (!answer.refused) await openSettings(answer.notice);
  return answer;
}

async function typedKnob(field, write, stands) {
  /* A typed FIELD's write: the request goes out, the footnote takes
     the answer, and the slip stays as it is — the reader has left this
     field for the next one, and a rebuild would take that one from
     under them. This field alone is then settled to what the store
     holds, read back rather than trusted: a number is normalised on
     the way in ("0.70" lands as 0.7), and the field must show what the
     file holds. A refusal — a value that does not parse, or one outside
     the parameter's range — is the field's own to report
     (`browser.editable` reads the flag) and changes nothing: the field
     goes back to what the store holds. */
  const answer = await write();
  if (answer.refused) {
    field._restore();
    field.dispatchEvent(new Event("input")); // the mark follows the value
    return answer;
  }
  footnote(popups.get("/set"), answer.notice);
  field._settle(stands(await api.settings()));
  field.dispatchEvent(new Event("input"));
  return answer;
}
