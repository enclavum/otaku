/* Inside one story: the level-2 dossier — premise, messages, scenes,
   cast and tools as five tabs of one panel, the way a case file is
   read. The Tools tab is `tools.js`, built from here.

   Scenes and cast are the same pane with the fields transposed: an
   index of one kind, a reading column at full measure, and the
   apparatus margin beside it holding everything the extractor wrote and
   nobody may edit — a character's ending in the merge and the delete.
   What IS editable opens where it is read.

   Any story opens whole, and any story EDITS: every write carries the
   story it belongs to, so a correction lands where it was read whether
   or not that story is the open one. The story browser stays open
   UNDERNEATH this panel — leaving reveals it as it was left, and with
   none to reveal the back button opens it on this story.

   Every write goes out through its own endpoint
   (`api.stories.scenes.update`, `api.stories.journals.update`, …) with
   the ids the row itself carries; the screen never invents an address.
   A save rebuilds nothing: the field shows what it saved, and what it
   answered is the footnote — inside a
   popup the status line is behind a modal, and nobody reads it there. */

import { api } from "../api/index.js";
import {
  ask,
  askConfirm,
  browser,
  closeAll,
  edited,
  editable,
  footnote,
  guard,
  popups,
  report,
} from "../kit/browser.js";
import {
  $,
  $$,
  LINE_STROKE,
  actionButton,
  element,
  pickFile,
  row,
  span,
  svgElement,
  switchButton,
} from "../kit/dom.js";
import { ago, excerpt, label } from "../kit/format.js";
import * as held from "../play/held.js";
import { thumbs } from "../kit/pictures.js";
import { typeset } from "../kit/prose.js";
import { facts, landed, resume, showContextUse } from "../play/shell.js";
import { cardFieldEditors, inviteCard, openCardWindow, pick } from "./characters.js";
import { buildTools } from "./tools.js";

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
  const read = await facts();
  const subject = story ?? { id: read.story_id, label: read.story || "" };
  const inside = subject.id === null || subject.id === read.story_id;
  // the story, its messages and its memory, asked at once (`opening`)
  const opened = subject.id === null ? null : await opening(subject.id);

  $("[data-story-title]", popup).textContent = label(subject.label) || "(untitled)";

  const view = {
    popup,
    subject,
    inside,
    facts: read,
    messages: opened?.messages ?? [],
    premise: opened?.premise ?? "",
    memory: opened?.memory ?? null,
    refused: opened?.refused ?? "",
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
    the rail's /fork, the story browser's button and the Messages tab's
    Fork here — before a story is made. `run` fires only on the answer,
    after the screens go. */
export async function confirmFork(run) {
  const choice = await askConfirm({
    title: "Fork this story?",
    body: "A copy from here on. The original stays as it is, and play continues in the copy.",
    action: "Fork",
  });
  if (choice !== "confirm") return;
  closeAll();
  await run();
}

/** The landing every "continue here" goes through — exported because the
    level above (the story browser) lands the same way, with no message:
    a resume takes the story as it is, and a fork copies from its head. */
export async function land(storyId, messageId, action) {
  /* Resuming takes the story as it stands; setting the later turns aside
     MOVES its head, from the one this page last read; forking MAKES a
     story, so it is a creation on the story it copies. Either way this
     page plays where it landed. */
  if (action === "resume") {
    closeAll();
    await resume(storyId);
    return;
  }
  const answer =
    action === "fork"
      ? await api.stories.forks.create(storyId, messageId === null ? {} : { at: messageId })
      : await api.stories.update(storyId, { head: messageId });
  if (!answer.refused) held.hold(answer.value.id);
  closeAll();
  await landed(answer.notice, { redraw: "always", refused: answer.refused });
}

/** One story as the dossier draws it: its facts, its messages and its
    memory, asked at once — three reads, each whole, so a tab never draws
    half of one — and the sentence a refusal answered with. */
async function opening(storyId) {
  const [story, messages, lore] = await Promise.all([
    api.stories.get(storyId),
    api.stories.messages.list(storyId),
    api.stories.lore.get(storyId),
  ]);
  const refused = story.refused ? story.notice : lore.refused ? lore.notice : "";
  return {
    premise: story.refused ? "" : story.premise,
    messages,
    memory: refused ? null : memoryOf(lore),
    refused,
  };
}

/** The memory as the two lenses draw it: each scene with its number, who
    was present and its journal records, each character with theirs —
    a record hangs between a scene and a character, so both lenses show
    the same rows — and how far the extractor has read. The page's own
    view of the story's `Lore`; its rules are the ones the backend spoke
    before it answered the facts alone (`Lore.extracted_up_to`). */
function memoryOf(lore) {
  const records = lore.journals.map((record) => ({
    ...record,
    scene: record.scene_id,
    character: record.character_id,
  }));
  const read = lore.extracted_up_to;
  const total = lore.message_count;
  return {
    read_through: read,
    // the messages played past the last closed scene, and their "N-M" range
    unread: Math.max(0, total - read),
    unread_span: total > read ? `${read + 1}-${total}` : "",
    scenes: lore.scenes.map((scene) => ({
      ...scene,
      number: scene.no,
      present: lore.characters
        .filter((character) =>
          records.some((record) => record.scene === scene.id && record.character === character.id),
        )
        .map((character) => character.name),
      journals: records.filter((record) => record.scene === scene.id),
    })),
    characters: lore.characters.map((character) => ({
      ...character,
      // the profile an imported character carries; "" for one the
      // extractor named out of the story itself
      profile: character.profile ?? "",
      journals: records.filter((record) => record.character === character.id),
    })),
  };
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
    else if (tab === "tools") buildTools(view, pane, tabNote);
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
  padScrollbar($("[data-detail]", pane));
  if (!rows.length) $("[data-actions]", pane).replaceChildren();
  // `e` is the keyboard's way to the same verb the pointer takes.
  const edit = () => $("[data-detail] .otk-edit__verb--edit", pane)?.click();
  const list = browser(view.popup, {
    root: pane,
    rows,
    drawRow: (message) => {
      // who the row is: a tool's result and a card's line by what they
      // are, the reader's own line, or the speaker the pass named
      const who =
        message.kind === "tool" || message.kind === "card"
          ? message.kind
          : message.role === "user"
            ? "you"
            : message.speaker || "—";
      /* The words, and right after them the turn's pictures counted, as
         the terminal's list sets its note (`terminal.screens.dossier.messages`): the
         count reads with the line, and stays in view when the line is
         cut. */
      const title = element("span", "otk-row__title otk-row__title--noted");
      title.append(span("otk-row__text", excerpt(message.body, 300)));
      const pictures = message.attachments?.length ?? 0;
      if (pictures) title.append(span("otk-tag", `${pictures} picture${pictures === 1 ? "" : "s"}`));
      const drawn = row(
        span("otk-row__id", String(message.position)),
        span(who === "you" ? "otk-row__who otk-row__who--you" : "otk-row__who", who),
        title,
      );
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

function padScrollbar(scroll) {
  /* A scrollbar that takes room in the reader stands 10px off the text,
     whenever it comes and goes: a message drawn, its editor grown, the
     window resized. Watched once: the pane serves every story. */
  if (scroll._padded) return;
  scroll._padded = true;
  const check = () => {
    const pad = scroll.offsetWidth > scroll.clientWidth ? "10px" : "";
    if (scroll.style.paddingRight !== pad) scroll.style.paddingRight = pad;
  };
  new ResizeObserver(check).observe(scroll);
  new MutationObserver(check).observe(scroll, { childList: true, subtree: true, attributes: true, characterData: true });
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

  // The message is corrected where it is read: the reader IS the field —
  // without the whitespace at its edges, the row's join, which the save
  // keeps (`stories.update_message`), as the transcript's field does.
  const body = element("div", "otk-detail__section");
  body.append(edited(reader(message.body.trim(), (text) => saveMessage(view, message, text)), ""));

  const out = [head, body];

  // The pictures that rode the turn, as the transcript shows them: tiles
  // that open the picture at full size.
  if (message.attachments?.length) out.push(sectionOf("Attachments", [thumbs(message.attachments)]));

  /* What otaku recorded WITH the turn. The model rides a fact row;
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
      onclick: guard(() => confirmFork(() => land(view.subject.id, message.id, "fork"))),
    }),
  );
  return out;
}

async function saveMessage(view, message, text) {
  const answer = await saveField(
    view,
    () => api.stories.messages.update(view.subject.id, message.id, { body: text }),
    () => {
      message.body = text;
      message.haystack = `${text} ${message.speaker ?? ""}`.toLowerCase();
      const line = $(`[data-pane="messages"] .otk-row[data-id="${message.id}"] .otk-row__text`, view.popup);
      if (line) line.textContent = excerpt(text, 300);
    },
  );
  /* The story's name falls back to its first line, so the runhead asks
     again; and the open story's transcript reads the same turn. */
  if (!answer.refused) {
    await landed("", { corrected: view.inside ? [message.position, text] : null });
  }
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
      (text) => api.stories.scenes.update(view.subject.id, scene.id, { title: text }),
      (text) => retitle(view, scene, text),
    ),
    ...lede(view, {
      text: scene.summary,
      empty: "(no summary yet)",
      name: "summary",
      save: (text) => api.stories.scenes.update(view.subject.id, scene.id, { summary: text }),
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
  if (scene.model) facts.push(madeBy(scene));
  if (scene.history) {
    facts.push(
      marginBlock(
        "the arc through here",
        element("p", "otk-passage__body otk-prose--margin", scene.history),
      ),
    );
  }
  margin.replaceChildren(
    span("otk-label", "Apparatus"),
    ...facts,
    marginFoot("Nothing in this column is rewritten by hand — the extractor writes it on every pass."),
  );
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
      span("otk-index__title otk-absent", "not summarized yet"),
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

function extractBlock(view, { adding = false } = {}) {
  /* The pass is a background job, so it needs a state and not only a
     button: how much is unread, and the control that reads it now. At
     the foot of the index, the column a pass fills — the cast's with
     the other door into the cast beside it (`adding`). */
  const box = element("div", "otk-extract");
  const line = element("div", "otk-extract__line");
  const unread = view.memory?.unread ?? 0;
  line.append(
    element("span", unread ? "otk-dot otk-dot--off" : "otk-dot"),
    span(
      "otk-extract__state",
      unread ? `${unread} ${unread === 1 ? "message" : "messages"} not extracted` : "all extracted",
    ),
  );
  // Two doors into the cast: a card from All characters — or, by its
  // picker's Import…, from a file — and the pass.
  const importing = adding ? [actionButton("Add character", { onclick: guard(inviteCard) })] : [];
  const button = element("button", "otk-btn", "Extract now");
  button.type = "button";
  button.dataset.command = "/extract";
  // Nothing open, nothing to read: the verb is greyed rather than
  // answered with a notice behind the dossier.
  button.disabled = !unread;
  box.append(line, ...importing, button);
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
  if (view.inside) parts.push(extractBlock(view, { adding: true }));
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
    // where they are, once they are anywhere
    span(
      "otk-label otk-label--accent",
      sceneCount(character) ? `Character · ${inScenes(character)}` : "Character",
    ),
    /* A name is the extractor's own — the cast is keyed by it, and
       `/merge` is how two become one — so it takes the label line
       without the verbs, in the box a scene's title sits in; linked to a
       codex card, the way to it follows the name. */
    edited(heading(view, character), "name", "otk-edit--title"),
    ...lede(view, {
      text: character.description,
      empty: "(no description yet)",
      name: "description",
      save: (text) => api.stories.characters.update(view.subject.id, character.id, { description: text }),
      shown: (text) => (character.description = text),
    }),
    journalPassages(
      view,
      character.journals,
      (record) => named(view.memory?.scenes, record.scene, "title", "a scene"),
      (name) => name,
    ),
  );

  /* Two heads, each where it has something under it: their arc so far
     and the model that wrote them; then, under the rule a scene's history
     stands under, the card they came in with, its picture's switch
     first. */
  const history = [];
  if (character.history) {
    history.push(element("p", "otk-passage__body otk-prose--margin", character.history));
  }
  if (character.model) history.push(madeBy(character));
  const card = [];
  if (character.card_portrait && character.portrait_sent !== null) {
    card.push(pictureSwitch(view, character));
  }
  if (character.profile) card.push(cardText(view, character));
  const cardHead = span(history.length ? "otk-label otk-margin__block" : "otk-label", "Card");
  // the facts scroll; the buttons stay pinned under them
  const known = element("div", "otk-margin__scroll");
  known.append(
    ...(history.length ? [span("otk-label", "History"), ...history] : []),
    ...(card.length ? [cardHead, ...card] : []),
  );
  margin.replaceChildren(known, manageBlock(view, character));
}

function pictureSwitch(view, character) {
  /* Whether the card's picture rides the row that brought the character
     in — chosen at a start or an invite, changed here. What the next
     request costs changes with it. */
  const toggle = switchButton(
    `Send ${character.name}'s picture along with the card`,
    character.portrait_sent,
  );
  toggle.onclick = guard(async () => {
    const answer = await api.stories.characters.update(view.subject.id, character.id, {
      portrait_sent: !character.portrait_sent,
    });
    if (!answer.refused) {
      character.portrait_sent = answer.value.portrait_sent;
      toggle.setAttribute("aria-checked", String(character.portrait_sent));
      await showContextUse();
    }
    footnote(view.popup, answer.notice, answer.refused);
  });
  const row = element("p", "otk-margin__switch");
  row.append(toggle, span("", "send the picture along with the card"));
  return marginFact("picture", row);
}

function cardText(view, character) {
  /* The card a character came in with: its copy of the card's profile,
     field by field as the card window shows a card's, each name over its
     field and each saved on its own. A profile that is no TOML reads as
     no fields — it is the hand-kept source, then, edited whole until it
     reads again. */
  if (character.profile_fields) {
    const box = element("div", "otk-v otk-v--sm");
    box.append(
      ...cardFieldEditors(
        character.profile_fields,
        async (changes) => {
          const answer = await api.stories.characters.update(view.subject.id, character.id, {
            profile_fields: changes,
          });
          if (!answer.refused) {
            character.profile = answer.value.profile;
            character.profile_fields = answer.value.profile_fields;
            footnote(view.popup, answer.notice);
            await showContextUse(); // the card's block is what the next request carries
          }
          return answer;
        },
        "otk-passage__body otk-prose--margin",
      ),
    );
    return box;
  }
  return edited(
    editable("otk-card", {
      text: character.profile,
      save: (text) =>
        saveField(
          view,
          () => api.stories.characters.update(view.subject.id, character.id, { profile: text }),
          () => (character.profile = text),
        ),
    }),
    "",
  );
}

function heading(view, character) {
  /* The character's name — and, linked to a codex card, after it its
     portrait where the card has one and a mark, either opening the card's
     window on its own; renamed, merged or deleted there, the card reads
     differently here, so the story is read again. Its aliases close it,
     "(aka …)". */
  const name = element("h3", "otk-reading__title");
  name.append(character.name);
  if (character.card_id != null) name.append(...cardLinks(view, character));
  if (character.aliases.length) {
    // one line, cut where it is wider than the column: the whole of it on hover
    const aka = span("otk-cast-aka", `(aka ${character.aliases.join(", ")})`);
    aka.title = aka.textContent;
    name.append(aka);
  }
  return name;
}

function cardLinks(view, character) {
  /* The ways from a linked character into its card's window. */
  const links = [];
  const openCard = guard(async () => {
    const refused = await openCardWindow(character.card_id);
    if (refused) report(refused);
    else await reread(view, character.id);
  });
  if (character.card_portrait) {
    // the mark beside it is the keyboard's way in; this, the pointer's
    const portrait = element("button", "otk-cast-portrait");
    portrait.type = "button";
    portrait.tabIndex = -1;
    portrait.setAttribute("aria-hidden", "true");
    const image = element("img");
    image.alt = "";
    image.src = api.pictures.thumbnail.url(character.card_portrait.file);
    portrait.append(image);
    portrait.onclick = openCard;
    links.push(portrait);
  }
  const open = element("button", "otk-card-open");
  open.type = "button";
  open.title = "Open the card";
  open.setAttribute("aria-label", "Open the card");
  const svg = svgElement("svg", { viewBox: "0 0 16 16", "aria-hidden": "true" });
  const mark = "M12 9.5v3.5a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h3.5M9.5 2H14v4.5M14 2 7.5 8.5";
  svg.append(svgElement("path", { d: mark, ...LINE_STROKE }));
  open.append(svg);
  open.onclick = openCard;
  links.push(open);
  return links;
}

function manageBlock(view, character) {
  /* What is done TO the character, pinned under what is known about it.
     A merge needs another character to fold into. */
  const row = element("div", "otk-manage__row");
  row.append(
    actionButton("Merge into…", {
      off: (view.memory?.characters ?? []).length < 2,
      onclick: guard(() => askMerge(view, character)),
    }),
    actionButton("Delete", {
      kind: "otk-btn--danger",
      onclick: guard(() => askDelete(view, character)),
    }),
  );
  const box = element("div", "otk-manage");
  box.append(row);
  return box;
}

async function askMerge(view, character) {
  /* Which character this one really is, picked from the rest of the cast
     as a card is picked (`characters.pick`): each with its card's picture
     where it is linked to one, found by a name or an alias. Nothing is
     picked up front, so a stray Enter merges nobody. */
  const others = (view.memory?.characters ?? []).filter((entry) => entry.id !== character.id);
  const picked = await pick({
    title: `Merge ${character.name} into\u2026`,
    filtering: "filter characters",
    rows: "Characters",
    read: async (query) => {
      const needle = query.toLowerCase();
      return others
        .filter((entry) => [entry.name, ...entry.aliases].join(" ").toLowerCase().includes(needle))
        .map((entry) => ({
          id: entry.id,
          name: entry.name,
          tagline: "",
          pictures: entry.card_portrait ? [entry.card_portrait] : [],
        }));
    },
    none: () => ["No character matches that."],
  });
  if (!picked?.chosen) return;
  const into = picked.chosen.id;
  const merged = await api.stories.characters.merges.create(view.subject.id, character.id, { into });
  await recast(view, merged, into);
}

async function askDelete(view, character) {
  const choice = await ask("delete-character", (dialog) => {
    $(".otk-dialog__name", dialog).textContent = character.name;
    // what lets a reader catch a wrong selection, in a write with no undo
    $(".otk-dialog__subject .otk-index__sub", dialog).textContent = inScenes(character);
  });
  if (choice !== "delete") return;
  await recast(view, await api.stories.characters.delete(view.subject.id, character.id), null);
}

async function recast(view, answer, selectId) {
  /* A merge or a delete changes the cast itself and every tab that draws
     it — a scene's "present", a message's speaker — so the story is read
     again and those tabs are built anew, the cast at once. A refusal
     changed nothing; either way the answer is the footnote. */
  if (!answer.refused) await reread(view, selectId);
  footnote(view.popup, answer.notice, answer.refused);
}

async function reread(view, selectId) {
  /* The story read again, the tabs that draw its cast built anew, and the
     cast shown on `selectId`. */
  const opened = await opening(view.subject.id);
  view.messages = opened.messages;
  view.memory = opened.memory;
  view.refused = opened.refused;
  for (const tab of ["messages", "scenes", "cast"]) view.built.delete(tab);
  show(view, "cast", selectId);
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
    const entry = edited(
      editable("otk-passage__body", {
        text: record.entry,
        save: (text) =>
          saveField(
            view,
            () => api.stories.journals.update(view.subject.id, record.id, { entry: text }),
            () => reentry(view, record.id, text),
          ),
      }),
      "",
    );
    passage.append(entry);
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
  const scenes = sceneCount(character);
  return `in ${scenes} ${scenes === 1 ? "scene" : "scenes"}`;
}

function sceneCount(character) {
  return new Set(character.journals.map((record) => record.scene)).size;
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

/** Who made a scene or a character — the pass's engine and model, or
    the card and its file — joined as the message detail joins a turn's
    "answered by". Absent on a row no pass made, or an older one. */
function madeBy(row) {
  const value = [row.provider, row.model].filter(Boolean).join(" · ");
  return marginFact("model", element("p", "otk-margin__value otk-margin__value--mono", value));
}

function marginBlock(key, valueNode) {
  const box = marginFact(key, valueNode);
  box.classList.add("otk-margin__block");
  return box;
}

function marginFoot(text) {
  return element("p", "otk-note otk-note--push", text);
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
  const box = edited(field, "");
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
     has to make one — taken back where the premise is refused
     (`held.writing`) — and this page plays it from then on. The view
     KEEPS it: a second save corrects the premise it just wrote, not
     starts another story. */
  const write = (id) =>
    saveField(
      view,
      () => api.stories.update(id, { premise: text }),
      () => (view.premise = text),
    );
  let answer;
  if (view.subject.id !== null) answer = await write(view.subject.id);
  else {
    try {
      answer = await held.writing((id) => {
        view.subject.id = id;
        return write(id);
      });
    } finally {
      // the story made for it, taken back, is none to write to
      if (held.story() !== view.subject.id) view.subject.id = null;
    }
  }
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
