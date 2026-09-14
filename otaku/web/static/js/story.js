/* Inside one story: the level-2 dossier — premise, messages, scenes and
   cast as four tabs of one panel, the way a case file is read.

   Scenes and cast are the same pane with the fields transposed: an
   index of one kind, a reading column at full measure, and the
   apparatus margin beside it holding everything the extractor wrote and
   nobody may edit. What IS editable opens where it is read.

   Any story opens whole, and any story EDITS: every write carries the
   story it belongs to, so a correction lands where it was read whether
   or not that story is the open one. The story browser stays open
   UNDERNEATH this panel — leaving reveals it as it was left, and with
   none to reveal the back button opens it on this story.

   Every write goes out through its own endpoint (`api.editScene`,
   `api.editJournal`, …) with the ids the row itself carries; the
   screen never invents an address. A save rebuilds nothing: the field
   shows what it saved, and what it answered is the footnote — inside a
   popup the status line is behind a modal, and nobody reads it there. */

import * as api from "./api.js";
import {
  ask,
  browser,
  closeAll,
  edited,
  editable,
  footnote,
  guard,
  popups,
} from "./browser.js";
import { $, $$, actionButton, element, pickFile, row, span } from "./dom.js";
import { ago, excerpt, label } from "./format.js";
import { typeset } from "./prose.js";
import { landed } from "./shell.js";
import { showCorrected } from "./transcript.js";

/* What a premise may be read from: the two shapes a person keeps prose
   in, and nothing else. A file picker offers anything, and a `.png` read
   as text is a screenful of noise where the premise was. */
const _PREMISE_FILES = ".txt,.md,.markdown,text/plain,text/markdown";

/** Open the dossier — on the open story by default, or on `story` (a
    row of the browser's). `tab` is where it opens; `allStories` is where
    the back button goes when no story browser waits underneath. */
export async function openStory({ story = null, tab = "messages", allStories } = {}) {
  const popup = popups.get("/story");
  /* ONE paint, after the read: the dialog appears already whole.
     Painting before the data flashed a half state every time: an
     opening click showed the shell (its "← All stories" button reading
     as another screen) or the LAST visit's pane. The reads are local and
     quick; the click answers with the finished panel. */
  const facts = await api.facts();
  const subject = story ?? { id: facts.story_id, label: facts.story || "" };
  const inside = subject.id === null || subject.id === facts.story_id;
  /* ONE read for all four tabs: three would let an extraction pass land
     between two of them and hand this panel a torn story. */
  const opened = subject.id === null ? null : await api.story(subject.id);

  $("[data-story-title]", popup).textContent = label(subject.label) || "(untitled)";
  const read = opened?.read_through ?? 0;
  $("[data-tabs-aside]", popup).textContent = [
    read ? `read through ${read}` : "",
    opened?.unread ? `${opened.unread} unread` : "",
  ]
    .filter(Boolean)
    .join(" · ");

  const view = {
    popup,
    subject,
    inside,
    facts,
    messages: opened?.messages ?? [],
    premise: opened?.premise ?? "",
    memory: opened && !opened.notice ? opened : null,
    refused: opened?.notice ?? "",
    /* Each tab is built ONCE per opening and then stands — its rail's
       scroll, its selection, an editor left open — so a tab switched
       away from and back is exactly as it was left. `built` says which
       are up; `select[tab]` is how a built tab takes a target in place;
       `footnotes[tab]` is the line under it, reapplied on a switch. */
    built: new Set(),
    select: {},
    footnotes: {},
  };

  for (const button of $$(".otk-tab", popup)) {
    button.onclick = () => show(view, button.dataset.tab);
  }
  $("[data-back]", popup).onclick = () => {
    // the browser underneath was never closed; without one, this opens
    // it on this story
    popup.close();
    if (!popups.get("/stories")?.open) allStories?.(subject.id);
  };
  show(view, tab);
  if (!popup.open) popup.showModal();
}

/** The fork question, asked the same way from every door that forks —
    the rail's /fork and the story browser's button — before a story is
    made. `run` fires only on the answer, after the screens go. */
export async function confirmFork(run) {
  const choice = await ask("confirm", (dialog) => {
    $("[data-title]", dialog).textContent = "Fork this story?";
    $(".otk-dialog__body", dialog).textContent =
      "A copy from here on. The original stays as it is, and play continues in the copy.";
    const aside = $("[data-note]", dialog);
    aside.textContent = "";
    aside.hidden = true;
    $('[data-choice="confirm"]', dialog).textContent = "Fork";
    $('[data-choice="cancel"]', dialog).textContent = "Cancel";
  });
  if (choice !== "confirm") return;
  closeAll();
  await run();
}

/** The landing every "continue here" goes through — exported because the
    level above (the story browser) lands the same way, with no message:
    a resume takes the story as it is, and a fork copies from its head. */
export async function land(storyId, messageId, action) {
  /* Resuming and setting aside MOVE the session's head; forking MAKES a
     story, so it is a creation on the story it copies. */
  const { notice } =
    action === "fork"
      ? await api.fork(storyId, messageId)
      : await api.setHead(storyId, messageId, action === "truncate");
  closeAll();
  await landed(notice, { redraw: "always" });
}

// ---------- the shell of the four tabs ----------

function show(view, tab, targetId = null) {
  for (const button of $$(".otk-tab", view.popup)) {
    button.setAttribute("aria-selected", String(button.dataset.tab === tab));
  }
  for (const pane of $$("[data-pane]", view.popup)) {
    pane.hidden = pane.dataset.pane !== tab;
  }
  const pane = $(`[data-pane="${tab}"]`, view.popup);
  if (!view.built.has(tab)) {
    view.built.add(tab);
    if (tab === "messages") buildMessages(view, pane);
    else if (tab === "scenes") buildScenes(view, pane);
    else if (tab === "cast") buildCast(view, pane, targetId);
    else buildPremise(view, pane);
    return;
  }
  // Up already: a target (a pivot) is selected in place, and without one
  // the tab is exactly as it was left.
  if (targetId !== null) view.select[tab]?.(targetId);
  footnote(view.popup, view.footnotes[tab] ?? "");
}

/** The line under a tab, kept so a switch back to it says it again. */
function tabNote(view, tab, text) {
  view.footnotes[tab] = text;
  footnote(view.popup, text);
}

/** The rail's mark moved to one item — a pick, or a target — with the
    rail itself standing as built. */
function markRail(rail, id) {
  for (const item of $$(".otk-index__item[data-id]", rail)) {
    item.setAttribute("aria-selected", String(item.dataset.id === String(id)));
  }
}

// ---------- messages: index rail, one line a turn, a reader ----------

function buildMessages(view, pane) {
  /* Built once. The index beside the list JUMPS WITHIN it: picking a
     scene selects its first message — the reader asked where something
     is, not for another tab. A scene whose span fell off the chain has
     no first message and the pick does nothing. */
  const rail = $("[data-index]", pane);
  sceneIndex(view, rail, null, {
    onPick: (scene) => {
      const first = sceneStart(view, scene);
      if (first === null) return;
      markRail(rail, scene.id);
      view.select.messages(first);
    },
  });
  const rows = view.messages.map((message, i) => ({
    ...message,
    position: i + 1,
    last: i === view.messages.length - 1,
    haystack: `${message.body} ${message.speaker ?? ""}`.toLowerCase(),
  }));
  tabNote(view, "messages", `${rows.length} ${rows.length === 1 ? "message" : "messages"}`);
  if (!rows.length) $("[data-actions]", pane).replaceChildren();
  // `e` is the keyboard's way to the same verb the pointer takes.
  const edit = () => $("[data-detail] .otk-edit__verb--edit", pane)?.click();
  const list = browser(view.popup, {
    root: pane,
    rows,
    drawRow: (message) => {
      const who = message.role === "user" ? "you" : message.speaker || "—";
      const drawn = row(
        span("otk-row__id", String(message.position)),
        span(message.role === "user" ? "otk-row__who otk-row__who--you" : "otk-row__who", who),
        span("otk-row__title", excerpt(message.body, 300)),
      );
      if (message.kind && message.kind !== "dialogue") drawn.append(span("otk-tag", message.kind));
      // what a save finds its row by: the cursor may be elsewhere by then
      drawn.dataset.id = String(message.id);
      return drawn;
    },
    drawPreview: (message) => drawReader(view, pane, message, edit),
    onOpen: (message) => resumeAt(view, message),
    onEdit: edit,
    empty: (filtered) =>
      filtered
        ? { line: "No turn matches that.", hint: `clear the filter to see all ${rows.length}` }
        : { line: "Nothing played yet.", hint: "continue this story to play into it" },
  });
  view.select.messages = (id) => list.select((message) => message.id === id);
}

/** The id of a scene's first message on the current chain — null when
    its span fell off it (an edit moved the head past the scene). */
function sceneStart(view, scene) {
  const first = parseInt(scene.span, 10);
  return Number.isNaN(first) ? null : (view.messages[first - 1]?.id ?? null);
}

function reader(text, save) {
  /* A played turn READS as it played — dialogue and `*emphasis*` set as
     the transcript sets them — and is still the field that corrects it.
     So the two are one box, sharing the editable's face and box, the
     rendering keeping its newlines, so opening one moves nothing. Which
     is shown is the stylesheet's, off `data-editing`, so nothing here
     keeps state about it. */
  const box = element("div", "otk-editable otk-prose otk-prose--read otk-typeset");
  const field = editable("otk-prose otk-prose--read", { text, save });

  const paint = () => {
    const { spoken, nodes } = typeset(field.value);
    box.classList.toggle("otk-prose--dialogue", spoken);
    box.replaceChildren(...nodes);
  };
  paint();

  const both = element("div", "otk-reader");
  // what is read once the field closes, saved or discarded; `edited`
  // asks for it, closing being what it knows and this box does not
  both._repaint = paint;
  both.append(box, field);
  return both;
}

function drawReader(view, pane, message, edit) {
  const head = element("div", "otk-detail__head");
  head.append(span("otk-label otk-label--ink", `Message ${message.position}`));
  if (message.kind) head.append(span("otk-tag", message.kind));

  // The message is corrected where it is read: the reader IS the field.
  const body = element("div", "otk-detail__section");
  body.append(edited(reader(message.body, (text) => saveMessage(view, message, text)), "message"));

  const out = [head, body];

  /* What the session recorded WITH the turn. The model rides a fact row;
     the template is the TEXT the direction filled, and prose wraps
     rather than running under its own label. */
  const recorded = [];
  if (message.provider || message.model) {
    recorded.push(fact("answered by", [message.provider, message.model].filter(Boolean).join(" · ")));
  }
  if (message.template) {
    recorded.push(derivedRow("template", "read only", element("p", "otk-derived", message.template)));
  }
  if (recorded.length) out.push(sectionOf("Recorded with the turn", recorded));

  // What the extractor writes onto it afterwards — present, or pending.
  if (message.role === "assistant") {
    out.push(
      sectionOf("Written by the extractor", [
        message.speaker
          ? derivedRow("speaker", "read only", element("p", "otk-derived", message.speaker))
          : derivedRow(
              "speaker",
              "pending",
              element("span", "otk-pending", "named on the next pass"),
            ),
      ]),
    );
  }

  // pinned under the pane, so a longer or shorter message never moves
  // the button being aimed at
  $("[data-actions]", pane).replaceChildren(
    actionButton("Resume here", {
      kind: "otk-btn--primary",
      onclick: guard(() => resumeAt(view, message)),
    }),
    actionButton("Edit", { onclick: () => edit() }),
    actionButton("Fork here", {
      onclick: guard(() => land(view.subject.id, message.id, "fork")),
    }),
  );
  return out;
}

async function saveMessage(view, message, text) {
  const answer = await saveField(
    view,
    () => api.editMessage(view.subject.id, message.id, text),
    () => {
      message.body = text;
      message.haystack = `${text} ${message.speaker ?? ""}`.toLowerCase();
      const line = $(`[data-pane="messages"] .otk-row[data-id="${message.id}"] .otk-row__title`, view.popup);
      if (line) line.textContent = excerpt(text, 300);
      // the open story's transcript reads the same turn
      if (view.inside) showCorrected(message.position, text);
    },
  );
  // the story's name falls back to its first line, so the runhead asks again
  if (!answer.refused) await landed("");
  return answer;
}

function resumeAt(view, message) {
  // The last turn resumes as it stands; an earlier one has to say what
  // to do with everything after it.
  if (message.last) return land(view.subject.id, message.id, "resume");
  return askLanding(view, message);
}

async function askLanding(view, message) {
  /* Picking an earlier turn has to say what becomes of everything after
     it. The radios choose; the button applies — a click on "Set aside"
     must not set anything aside. */
  let chosen = "fork";
  const follow = view.messages.length - message.position;
  const choice = await ask("landing", (dialog) => {
    $("[data-title]", dialog).textContent = `Resume from message ${message.position}?`;
    $(".otk-dialog__body", dialog).textContent =
      `${follow === 1 ? "One turn follows" : `${follow} turns follow`} this one. ` +
      "Choose what happens to them.";
    const options = $$("[data-select]", dialog);
    const confirm = $('[data-choice="confirm"]', dialog);
    const select = (picked) => {
      chosen = picked.dataset.select;
      for (const option of options) {
        option.setAttribute("aria-checked", String(option === picked));
      }
      // the button says what it will DO, never the name of another door
      if (confirm) confirm.textContent = $(".otk-choice__name", picked)?.textContent ?? chosen;
    };
    for (const option of options) option.onclick = () => select(option);
    // fork is the default: the one choice that loses nothing
    const fork = options.find((option) => option.dataset.select === "fork");
    if (fork) select(fork);
  });
  // Returned, so the guard on the door that opened this dialog catches
  // a landing that fails — fired bare, it would reject with nobody
  // attached and the reader would believe the head moved.
  if (choice === "confirm") return land(view.subject.id, message.id, chosen);
}

// ---------- scenes: index, reading column, apparatus margin ----------

function buildScenes(view, pane) {
  /* Built once: the rail stands, and a pick is a selection — the rail
     re-marked, the reading and the margin redrawn for that scene — so
     the rail keeps its scroll and the tab its pick. */
  const scenes = view.memory?.scenes ?? [];
  tabNote(view, "scenes", `${scenes.length} ${scenes.length === 1 ? "scene" : "scenes"}`);
  const rail = $("[data-index]", pane);
  sceneIndex(view, rail, null, { onPick: (picked) => view.select.scenes(picked.id) });
  view.select.scenes = (id) => {
    const scene = scenes.find((entry) => entry.id === id) ?? scenes[0];
    markRail(rail, scene?.id ?? null);
    drawScene(view, pane, scene);
  };
  view.select.scenes(null);
}

function drawScene(view, pane, scene) {
  const reading = $("[data-reading]", pane);
  const margin = $("[data-margin]", pane);
  if (!scene) {
    reading.replaceChildren(
      element(
        "p",
        "otk-note",
        view.refused ||
          "No scenes yet. The extractor reads played messages into scenes as the story grows.",
      ),
    );
    margin.replaceChildren();
    return;
  }

  reading.replaceChildren(
    span(
      "otk-label otk-label--accent",
      scene.span ? `Scene ${scene.number} · messages ${scene.span}` : `Scene ${scene.number}`,
    ),
    title(
      view,
      scene.title,
      "(untitled scene)",
      (text) => api.editScene(view.subject.id, scene.id, { title: text }),
      (text) => retitle(view, scene, text),
    ),
    ...lede(view, {
      text: scene.summary,
      empty: "(no summary yet)",
      name: "summary",
      save: (text) => api.editScene(view.subject.id, scene.id, { summary: text }),
      shown: (text) => (scene.summary = text),
    }),
    journalPassages(
      view,
      scene.journals,
      (record) => named(view.memory?.characters, record.character, "name", "someone"),
      (name) => `${name} records`,
    ),
  );

  const facts = [];
  if (scene.present.length) facts.push(marginFact("present", presentLine(view, scene.present)));
  if (scene.updated_at) {
    facts.push(
      marginFact("extracted", element("p", "otk-margin__value otk-margin__value--mono", ago(scene.updated_at))),
    );
  }
  if (scene.history) {
    facts.push(
      marginBlock(
        "the arc through here",
        element("p", "otk-passage__body otk-prose--margin", scene.history),
      ),
    );
  }
  margin.replaceChildren(span("otk-label", "Apparatus"), ...facts, marginFoot());
}

function sceneIndex(view, rail, currentId, { onPick }) {
  const scenes = view.memory?.scenes ?? [];
  const head = element("div", "otk-index__head");
  head.append(span("otk-label", "Scenes"), span("otk-index__sub otk-push", String(scenes.length)));
  const list = element("div", "otk-index__list");
  for (const scene of scenes) {
    const item = element("button", "otk-index__item");
    item.type = "button";
    item.dataset.id = String(scene.id);
    if (scene.id === currentId) item.setAttribute("aria-selected", "true");
    item.append(
      span("otk-index__title", scene.title || "(untitled scene)"),
      span("otk-index__sub", scene.span ? `msg ${scene.span}` : ""),
    );
    item.onclick = guard(() => onPick(scene));
    list.append(item);
  }
  // What the extractor has NOT read, as a dashed row where the scene
  // that will cover it goes: its absence has a place in the list.
  if (view.memory?.unread) {
    const pending = element("div", "otk-index__item otk-index__item--pending");
    pending.append(
      span("otk-index__title otk-absent", "not read yet"),
      span("otk-index__sub", `msg ${view.memory.unread_span}`),
    );
    list.append(pending);
  }
  const parts = [head, list];
  if (!scenes.length && !view.memory?.unread) {
    parts.push(
      element(
        "p",
        "otk-note otk-index__note",
        view.refused || "The extractor reads played messages into scenes.",
      ),
    );
  }
  if (view.inside) parts.push(extractBlock(view));
  rail.replaceChildren(...parts);
}

function extractBlock(view) {
  /* The pass is a background job, so it needs a state and not only a
     button: how much is unread, and the control that reads it now. At
     the foot of the index, the column a pass fills. */
  const box = element("div", "otk-extract");
  const line = element("div", "otk-extract__line");
  const unread = view.memory?.unread ?? 0;
  line.append(
    element("span", unread ? "otk-dot otk-dot--off" : "otk-dot"),
    span("otk-extract__state", unread ? `${unread} unread` : "all read"),
  );
  const button = element("button", "otk-btn", "Extract now");
  button.type = "button";
  button.dataset.command = "/extract";
  box.append(line, button);
  return box;
}

// ---------- cast: the same pane with the fields transposed ----------

function buildCast(view, pane, characterId = null) {
  /* Built once, like the scenes tab: the rail stands and a pick is a
     selection. */
  const cast = view.memory?.characters ?? [];
  tabNote(view, "cast", `${cast.length} ${cast.length === 1 ? "character" : "characters"}`);
  const rail = $("[data-index]", pane);
  const head = element("div", "otk-index__head");
  head.append(span("otk-label", "Cast"), span("otk-index__sub otk-push", String(cast.length)));
  const list = element("div", "otk-index__list");
  for (const entry of cast) {
    const item = element("button", "otk-index__item");
    item.type = "button";
    item.dataset.id = String(entry.id);
    item.append(
      span("otk-index__title", entry.name),
      span("otk-index__sub", inScenes(entry)),
    );
    item.onclick = guard(() => view.select.cast(entry.id));
    list.append(item);
  }
  const parts = [head, list];
  if (view.inside) parts.push(extractBlock(view));
  rail.replaceChildren(...parts);
  view.select.cast = (id) => {
    const character = cast.find((entry) => entry.id === id) ?? cast[0];
    markRail(rail, character?.id ?? null);
    drawCharacter(view, pane, character);
  };
  view.select.cast(characterId);
}

function drawCharacter(view, pane, character) {
  const reading = $("[data-reading]", pane);
  const margin = $("[data-margin]", pane);
  if (!character) {
    reading.replaceChildren(
      element(
        "p",
        "otk-note",
        view.refused || "No characters yet. The extractor names the cast as the story grows.",
      ),
    );
    margin.replaceChildren();
    return;
  }

  reading.replaceChildren(
    span(
      "otk-label otk-label--accent",
      `Character · ${inScenes(character)}`,
    ),
    /* A name is the extractor's own — the cast is keyed by it, and
       `/merge` is how two become one — so it takes the label line
       without the verbs, in the box a scene's title sits in. */
    edited(element("h3", "otk-reading__title", character.name), "name", "otk-edit--title"),
    ...lede(view, {
      text: character.description,
      empty: "(no description yet)",
      name: "description",
      save: (text) => api.editCharacter(view.subject.id, character.id, { description: text }),
      shown: (text) => (character.description = text),
    }),
    journalPassages(
      view,
      character.journals,
      (record) => named(view.memory?.scenes, record.scene, "title", "a scene"),
      (name) => name,
    ),
  );

  const facts = [];
  facts.push(
    marginFact(
      "aliases",
      element("p", "otk-derived", character.aliases.join(" · ") || "none recorded"),
    ),
  );
  /* Their arc so far, the counterpart of the scene's own: same block,
     same rule above it, so the two lenses read as one apparatus. */
  if (character.history) {
    facts.push(
      marginBlock(
        `${character.name}'s history so far`,
        element("p", "otk-passage__body otk-prose--margin", character.history),
      ),
    );
  }
  /* The card ARCHIVE: hand-kept source, so it wears a plain face and the
     same verbs every corrected field wears — inert until `edit` is taken.
     The fact's key already names it, so the hint line carries only the
     verbs, anchored right. */
  facts.push(
    character.card
      ? marginFact("card", edited(editable("otk-card", {
          text: character.card,
          save: (text) => saveField(
            view,
            () => api.editCharacter(view.subject.id, character.id, { card: text }),
            () => (character.card = text),
          ),
        }), ""))
      : marginFact(
          "card",
          element("p", "otk-derived", "none — extracted from the story, not imported"),
        ),
  );
  margin.replaceChildren(span("otk-label", "Apparatus"), ...facts, marginFoot());
}

// ---------- the field shapes both lenses share ----------

function title(view, text, fallback, save, shown) {
  /* The scene's own name, edited where it is READ: its heading, not a
     row in the apparatus column — that column holds what the extractor
     writes, and a hand may correct a title. */
  const head = editable("otk-reading__title", {
    text,
    save: (corrected) => saveField(view, () => save(corrected), () => shown(corrected)),
  });
  head.placeholder = fallback;
  head.rows = 1;
  return edited(head, "title", "otk-edit--title");
}

function lede(view, { text, empty, name, save, shown }) {
  /* The reading column's own text — a scene's summary, a character's
     description. It IS a field, with the line under it naming what it
     holds and then how to commit it. */
  if (!save) return [element("p", "otk-reading__lede", text || empty)];
  return [
    edited(
      editable("otk-reading__lede", {
        text,
        save: (corrected) => saveField(view, () => save(corrected), () => shown(corrected)),
      }),
      name,
    ),
  ];
}

function journalPassages(view, journals, nameOf, rubricOf) {
  /* One passage per record: the rubric names who (or which scene)
     records, the body is the entry, and the state rides under it as a
     note. The entry opens for correction where it is read; the state is
     the extractor's line about a moment that has passed.

     One record read from either side — a scene draws its characters'
     lines, a character the scenes they were in — and both address the
     same journal id when a correction is saved. */
  const box = element("div", "otk-passages");
  for (const record of journals ?? []) {
    const passage = element("div", "otk-passage");
    // what a correction finds the record's other passage by (`reentry`)
    passage.dataset.record = String(record.id);
    passage.append(span("otk-passage__rubric", rubricOf(nameOf(record))));
    passage.append(
      edited(
        editable("otk-passage__body", {
          text: record.entry,
          save: (text) =>
            saveField(
              view,
              () => api.editJournal(view.subject.id, record.id, { entry: text }),
              () => reentry(view, record.id, text),
            ),
        }),
        "entry",
      ),
    );
    if (record.state) passage.append(element("p", "otk-note", record.state));
    box.append(passage);
  }
  return box;
}

/** The name of the row on the OTHER side of a journal record — the
    character a scene's entry was written by, the scene a character's
    entry was written in. */
function named(rows, id, key, fallback) {
  return (rows ?? []).find((row) => row.id === id)?.[key] || fallback;
}

/** How much of the story a character is in, counted from their own
    records rather than sent as a figure of its own. */
function inScenes(character) {
  const scenes = new Set(character.journals.map((record) => record.scene)).size;
  return `in ${scenes} ${scenes === 1 ? "scene" : "scenes"}`;
}

async function saveField(view, write, shown) {
  /* Nothing is rebuilt: the field shows what it saved, `shown` carries
     the text to the other places this dossier draws it, and a click away
     that saved is not undone. A refusal travels back to `browser.editable`,
     which keeps the field open with the words. */
  const answer = await write();
  if (!answer.refused) {
    shown();
    footnote(view.popup, answer.notice);
  }
  return answer;
}

/** A scene's corrected title in the index rails that name it — the
    scenes tab's and the messages tab's. */
function retitle(view, scene, text) {
  scene.title = text;
  const item = `.otk-index__item[data-id="${scene.id}"] .otk-index__title`;
  for (const name of $$(`[data-pane="scenes"] ${item}, [data-pane="messages"] ${item}`, view.popup)) {
    name.textContent = text || "(untitled scene)";
  }
}

/** A journal record's corrected entry on both sides it is read from — a
    scene's passages and a character's draw the same record — leaving
    alone a passage open in its own editor. */
function reentry(view, id, text) {
  for (const holder of [...(view.memory?.scenes ?? []), ...(view.memory?.characters ?? [])]) {
    for (const record of holder.journals ?? []) if (record.id === id) record.entry = text;
  }
  for (const passage of $$(`.otk-passage[data-record="${id}"]`, view.popup)) {
    if ($(".otk-edit", passage).dataset.editing) continue;
    $("textarea.otk-editable", passage)._settle(text);
    $(".otk-reader", passage)._repaint();
  }
}

function presentLine(view, present) {
  /* Who was in the scene, each name a door to that character — the
     journal is the same entry from the other side. */
  const line = element("p", "otk-margin__value");
  present.forEach((name, i) => {
    if (i) line.append(", ");
    const character = (view.memory?.characters ?? []).find((entry) => entry.name === name);
    if (!character) {
      line.append(name);
      return;
    }
    const link = element("a", "", name);
    link.href = "#";
    link.onclick = guard((event) => {
      event.preventDefault();
      show(view, "cast", character.id);
    });
    line.append(link);
  });
  return line;
}

function marginFact(key, valueNode) {
  /* No row here is flagged: the column says once, in its foot, that
     nothing in it is written by hand. `derivedRow` is the other case —
     the message detail, where editable and derived rows stand side by
     side and each has to say which it is. */
  const box = element("div", "otk-margin__fact");
  box.append(span("otk-margin__key", key), valueNode);
  return box;
}

function marginBlock(key, valueNode) {
  const box = marginFact(key, valueNode);
  box.classList.add("otk-margin__block");
  return box;
}

function marginFoot() {
  return element(
    "p",
    "otk-note otk-note--push",
    "Nothing in this column is rewritten by hand — the extractor writes it on every pass.",
  );
}

function derivedRow(key, flag, valueNode) {
  const box = element("div", "otk-field-row");
  const name = span("otk-margin__key", key);
  name.append(span("otk-derived__flag", flag));
  box.append(name, valueNode);
  return box;
}

// ---------- premise: one long text, and the two ways it gets there ----------

function buildPremise(view, pane) {
  tabNote(view, "premise", "sent as the system message");
  /* The premise is a field like every other on this dossier: inert
     until `edit` is taken, closed by `save`, `cancel` or a click away. */
  const field = editable("otk-premise__body", {
    text: view.premise,
    save: (text) => savePremise(view, text),
  });
  field.placeholder = "(no premise yet)";
  const box = edited(field, "premise");
  $("[data-premise-field]", pane).replaceChildren(box);
  const importing = $("[data-import-system]", pane);
  // pressing it is not a click away: the text it reads lands in the open editor
  importing.onmousedown = (event) => event.preventDefault();
  importing.onclick = guard(async () => {
    /* The file is read HERE, so what is saved is what the reader can see
       and correct — and a path sent over HTTP would name a file on the
       machine otaku runs on, not the one it was picked from. It lands in
       the OPEN editor: reading a file is an edit half-made, and the same
       save or cancel settles it. */
    const picked = await pickFile(_PREMISE_FILES);
    if (!picked) return;
    if (!box.dataset.editing) $(".otk-edit__verb--edit", box).click();
    field.value = await picked.text();
  });
}

async function savePremise(view, text) {
  /* A premise lives ON a story, so one written before the first message
     has to make one (the terminal reaches the same place through
     `session._ensure_story`). The view KEEPS it: a second save corrects
     the premise it just wrote, not starts another story. */
  if (view.subject.id === null) view.subject.id = (await api.newStory()).story;
  const answer = await saveField(
    view,
    () => api.setPremise(view.subject.id, text),
    () => (view.premise = text),
  );
  if (!answer.refused) await landed("");
  return answer;
}

// ---------- small shared shapes ----------

function sectionOf(label, children) {
  const box = element("section", "otk-detail__section");
  box.append(span("otk-label", label), ...children);
  return box;
}

function fact(key, value) {
  const line = element("p", "otk-fact");
  line.append(span("", key), span("", value));
  return line;
}
