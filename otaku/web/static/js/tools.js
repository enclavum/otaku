/* The dossier's Tools tab: the story's settings, one switch each — a
   tool the model may use, a reminder the context carries — with the
   one control each governs (where its text rides, on the depth ruler;
   a tool's prompt in its dialog; the notes' display switch) and the
   reminders' texts under their rows. Built by `story` as its fifth tab
   and handed the dossier's `tabNote`, so this module stays below it.

   The backend names each setting and says where it stands; what a
   switch DOES, what the ruler calls a row and what a prompt dialog is
   titled are the page's own words, in the tables below. */

import * as api from "./api.js";
import { ask, edited, editable, footnote, guard } from "./browser.js";
import { $, element, span } from "./dom.js";
import { landed } from "./shell.js";

/* What each story setting does, under its name — in the order the
   tab draws them. A caption is the page's own, as the settings slip's
   are (`settings._ABOUT`): the backend names the setting and says
   where it stands, and this says what switching it does. A setting
   without a caption is not drawn — nothing on the page could say what
   it is for. */
const _ABOUT = {
  allow_questions:
    "The narrator may ask you something directly and wait for an answer before going on.",
  allow_assistant_notes:
    "The narrator may keep a short note to itself between turns, never played into the story.",
  use_story_reminder: "Sent with every reply in this story, wherever you place it.",
  use_shared_reminder: "Sent with every reply in every story.",
};

/* The one setting whose text is not the story's: every story is sent
   the same reminder, read and written at its own endpoint. */
const _SHARED_REMINDER = "use_shared_reminder";

/* What the instructions dialog is titled, per tool — the page's own
   words, as the captions are. */
const _PROMPT_TITLE = {
  question: "questions prompt",
  note: "assistant notes prompt",
};

/* What the depth ruler calls each setting — the page's own words, as
   the captions are: the setting shorn of its verb, since a row of the
   ruler is a thing placed, not a switch. */
const _RULER_NAME = {
  allow_questions: "Questions",
  allow_assistant_notes: "Assistant notes",
  use_story_reminder: "Story reminder",
  use_shared_reminder: "Shared reminder",
};

export async function buildTools(view, pane, tabNote) {
  /* Built once, like the other tabs. Every switch is the story's, so
     a story that does not exist yet has none to show — the same story
     the premise tab makes on its first save. */
  const box = $("[data-features]", pane);
  if (view.subject.id === null) {
    // `backend.session.NO_STORY_HINT`, copied: no story, no endpoint to ask.
    box.replaceChildren(element("p", "otk-note", "No story yet — send a message first."));
    tabNote(view, "tools", "");
    return;
  }
  const [{ settings }, shared] = await Promise.all([
    api.storySettings(view.subject.id),
    api.sharedReminder(),
  ]);
  const drawn = Object.keys(_ABOUT)
    .map((name) => settings.find((setting) => setting.name === name))
    .filter(Boolean);
  const on = drawn.filter((setting) => setting.enabled).length;
  tabNote(view, "tools", `${on} of ${drawn.length} on`);
  // every row's depth opens the one ruler, which places them all
  const open = () => placement(view, drawn, box);
  box.replaceChildren(...drawn.map((setting) => feature(view, setting, shared.text, open)));
}

function feature(view, setting, sharedText, openPlacement) {
  /* One setting: the switch, its name and what it does, and the one
     control it governs — where its text rides. A reminder carries the
     text itself under the row, a field like every other on this
     dossier. Off, what the switch governs steps back. */
  const box = element("div", "otk-feature");
  box.dataset.on = String(setting.enabled);
  box.dataset.setting = setting.name;
  const head = element("div", "otk-feature__head");
  const toggle = element("button", "otk-switch otk-switch--h");
  toggle.type = "button";
  toggle.setAttribute("role", "switch");
  toggle.setAttribute("aria-checked", String(setting.enabled));
  toggle.setAttribute("aria-label", setting.label);
  toggle.append(span("otk-switch__knob"));
  toggle.onclick = guard(async () => {
    const wanted = toggle.getAttribute("aria-checked") !== "true";
    const answer = await saveSetting(view, setting, { enabled: wanted });
    if (answer.refused) return;
    toggle.setAttribute("aria-checked", String(wanted));
    box.dataset.on = String(wanted);
    // what a tool with something to display draws just changed with it
    if (setting.display_notes !== null && view.inside) await landed("", { redraw: "always" });
  });
  const text = element("span", "otk-feature__text");
  const title = element("span", "otk-feature__title");
  title.append(span("otk-feature__name", setting.label));
  // A second switch the row may carry sits on the name's line, after it.
  if (setting.display_notes !== null) title.append(shownSwitch(view, setting));
  text.append(title, element("p", "otk-feature__note", _ABOUT[setting.name]));
  head.append(toggle, text);
  if (setting.tool || setting.allowed_positions.length) {
    const control = element("span", "otk-feature__control");
    const knobs = element("span", "otk-feature__knobs");
    if (setting.tool) knobs.append(instructionsButton(view, setting));
    if (setting.allowed_positions.length) knobs.append(depthButton(setting, openPlacement));
    control.append(knobs);
    head.append(control);
  }
  box.append(head);
  /* The reminders' texts: the story's own is the setting's `reminder_text`, the
     shared one every story's, saved where it is read. */
  if (setting.name === _SHARED_REMINDER) {
    box.append(reminderBody(view, sharedText, (typed) => api.setSharedReminder(typed)));
  } else if (setting.reminder_text !== null) {
    box.append(
      reminderBody(view, setting.reminder_text, (typed) =>
        saveSetting(view, setting, { reminder_text: typed }),
      ),
    );
  }
  return box;
}

function depthButton(setting, openPlacement) {
  /* Where the text rides, read at a glance: a figure in a right-hand
     column the rows line up on, a small head naming it. A click opens
     the ruler, where it is moved. A number names one of the reader's
     messages, counted from the end, and the text goes before it. */
  const button = element("button", "otk-depth");
  button.type = "button";
  button.title = "Click to change where it goes";
  button.append(span("otk-depth__head", "depth"), span("otk-depth__value"));
  showDepth(button, setting.position);
  button.onclick = guard(openPlacement);
  return button;
}

function showDepth(button, position) {
  button.setAttribute("aria-label", `Injection depth: ${placeName(position)}`);
  $(".otk-depth__value", button).textContent = placeName(position);
}

async function placement(view, settings, features) {
  /* The ruler: one row per setting that rides somewhere, one column
     per place — the system message, then the reader's messages counted
     from the end, the newest last, where nothing may go. The columns
     are the union of the closed lists the backend offers; a row draws
     a dash where its own list has no such place. Built each time it
     opens, from the settings as they stand. */
  const rows = settings.filter((setting) => setting.allowed_positions.length);
  const numbered = [...new Set(rows.flatMap((s) => s.allowed_positions.filter(Number.isInteger)))];
  numbered.sort((a, b) => b - a); // the deepest on the left, the newest message on the right
  const dialog = $('dialog[data-dialog="placement"]');
  const ruler = $("[data-ruler]", dialog);
  ruler.style.setProperty("--otk-ruler-slots", String(numbered.length + 1));
  const head = element("div", "otk-ruler__row otk-ruler__row--head");
  const scale = element("div", "otk-ruler__track");
  scale.append(span("", "sys"), ...numbered.map((n) => span("", nth(n))), span("otk-ruler__last", "last"));
  head.append(element("span"), scale);
  ruler.replaceChildren(head, ...rows.map((setting) => rulerRow(view, setting, numbered, features)));
  await ask("placement");
}

function rulerRow(view, setting, numbered, features) {
  /* One setting on the ruler. A tick moves it there and saves at once;
     the marker drags too, snapping along the track and saving where it
     is let go — a refusal puts it back. An off setting keeps its place,
     drawn hollow. The depth on its row behind follows every move. */
  const name = _RULER_NAME[setting.name] ?? setting.label;
  const row = element("div", "otk-ruler__row");
  row.dataset.on = String(setting.enabled);
  if (!setting.allowed_positions.includes("system")) row.classList.add("otk-ruler__row--nosys");
  const title = span("otk-ruler__name", name);
  title.append(span("otk-ruler__off", "off"));
  const track = element("div", "otk-ruler__track");
  const ticks = new Map(); // a place → its tick
  const mark = (position) => {
    for (const [at, tick] of ticks) tick.setAttribute("aria-pressed", String(at === position));
  };
  let saving = false;
  const move = guard(async (position) => {
    if (saving || position === setting.position) return;
    saving = true;
    try {
      // `setting.position` comes back where the backend left it
      await saveSetting(view, setting, { position });
      mark(setting.position);
      showDepth($(`.otk-feature[data-setting="${setting.name}"] .otk-depth`, features), setting.position);
    } finally {
      saving = false;
    }
  });
  /* A press-and-drag along the track: the marker snaps to the nearest
     tick as the pointer goes, wherever it goes, and where it is let go
     is saved. A plain click is the tick's own; the click a drag fires
     as the pointer lifts is passed over. An off setting is not moved
     here at all — its ticks are disabled and its track takes no drag —
     as its depth on the tab behind is inert while it is off. */
  let held = null; // while the pointer is down: whether it moved, and where the marker snapped
  let dragged = false;
  const nearest = (x) => {
    let best = setting.position;
    let gap = Infinity;
    for (const [at, tick] of ticks) {
      const box = tick.getBoundingClientRect();
      const away = Math.abs(box.left + box.width / 2 - x);
      if (away < gap) [best, gap] = [at, away];
    }
    return best;
  };
  track.addEventListener("pointerdown", (event) => {
    if (!setting.enabled || event.button !== 0 || held) return;
    event.preventDefault(); // nothing selected along the way
    dragged = false;
    held = { moved: false, at: setting.position };
    const during = new AbortController();
    const { signal } = during;
    document.addEventListener(
      "pointermove",
      (moved) => {
        held.moved = true;
        row.dataset.dragging = "true";
        held.at = nearest(moved.clientX);
        mark(held.at);
      },
      { signal },
    );
    const release = () => {
      during.abort();
      const { moved, at } = held;
      held = null;
      delete row.dataset.dragging;
      if (!moved) return;
      dragged = true;
      move(at);
    };
    document.addEventListener("pointerup", release, { signal });
    document.addEventListener("pointercancel", release, { signal });
  });
  const cell = (position) => {
    if (!setting.allowed_positions.includes(position)) {
      const no = span("otk-ruler__cell otk-ruler__cell--no", "—");
      no.setAttribute("aria-hidden", "true");
      return no;
    }
    const tick = element("button", "otk-ruler__cell");
    tick.type = "button";
    tick.disabled = !setting.enabled;
    tick.setAttribute("aria-label", `${name} at ${placeName(position)}`);
    tick.append(span("otk-ruler__mark"));
    tick.onclick = () => {
      if (dragged) dragged = false;
      else move(position);
    };
    ticks.set(position, tick);
    return tick;
  };
  track.append(cell("system"), ...numbered.map(cell), span("otk-ruler__cell"));
  mark(setting.position);
  row.append(title, track);
  return row;
}

function shownSwitch(view, setting) {
  /* Whether the reader sees what the tool writes — the notes: a second
     switch on the row, its own label beside it. The transcript is read
     again when it moves, since what it draws just changed. */
  const box = element("span", "otk-feature__shown");
  const toggle = element("button", "otk-switch otk-switch--h");
  toggle.type = "button";
  toggle.setAttribute("role", "switch");
  toggle.setAttribute("aria-checked", String(setting.display_notes));
  toggle.setAttribute("aria-label", "Display notes");
  toggle.append(span("otk-switch__knob"));
  toggle.onclick = guard(async () => {
    const wanted = toggle.getAttribute("aria-checked") !== "true";
    const answer = await saveSetting(view, setting, { display_notes: wanted });
    if (answer.refused) return;
    toggle.setAttribute("aria-checked", String(wanted));
    if (view.inside) await landed("", { redraw: "always" });
  });
  box.append(toggle, span("otk-feature__shown-label", "Display notes"));
  return box;
}

function instructionsButton(view, setting) {
  /* The tool's prompt — "Instructions" on the page — edited whole in a
     dialog: one text for every story, read fresh when the dialog opens
     so a hand edit of prompts.toml shows, and written back on Save. */
  const button = element("button", "otk-btn", "Instructions");
  button.type = "button";
  button.onclick = guard(async () => {
    const { text } = await api.prompt(setting.tool);
    const dialog = $('dialog[data-dialog="instruction"]');
    const field = $("textarea", dialog);
    const choice = await ask("instruction", () => {
      $("[data-title]", dialog).textContent = _PROMPT_TITLE[setting.tool] ?? `${setting.tool} prompt`;
      field.value = text;
      // Read from the top, and nothing typed by accident: the text
      // starts at its first line and the focus rests on Cancel.
      field.setSelectionRange(0, 0);
      field.scrollTop = 0;
    });
    if (choice !== "save") return;
    const answer = await api.setPrompt(setting.tool, field.value);
    footnote(view.popup, answer.notice);
  });
  return button;
}

/* A position's name is decided below both frontends (`InjectionPosition.text`,
   `store.schema`); this is the rule copied for the page's own drawing. */
function nth(position) {
  const n = position + 1; // 1 → 2nd, 8 → 9th: the reader's newest message is the last
  const suffix = n === 2 ? "nd" : n === 3 ? "rd" : "th";
  return `${n}${suffix}`;
}

function placeName(position) {
  return position === "system" ? "system" : `${nth(position)} last`;
}

function reminderBody(view, text, write) {
  const body = element("div", "otk-feature__body");
  const field = editable("otk-prose otk-prose--read", {
    text,
    save: async (typed) => {
      // nothing is rebuilt: the field shows what it saved, and what the
      // backend said of it is the footnote
      const answer = await write(typed);
      if (!answer.refused) footnote(view.popup, answer.notice);
      return answer;
    },
  });
  field.placeholder = "(nothing yet)";
  body.append(edited(field, ""));
  return body;
}

async function saveSetting(view, setting, fields) {
  /* One field of one setting, the rest left as it stands; what the
     backend says of it is the footnote, and a refusal keeps the
     control where it was. */
  const answer = await api.updateSetting(view.subject.id, setting.name, fields);
  footnote(view.popup, answer.notice);
  if (!answer.refused) Object.assign(setting, fields);
  return answer;
}
