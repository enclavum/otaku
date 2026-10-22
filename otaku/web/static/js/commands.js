/* What a row of the contents opens, and the few doors that are a call
   rather than a screen.

   Every one of these is reached by a BUTTON — nothing here composes a
   command line for the far end to parse. `SCREENS` is keyed by the token
   the contents rail carries; what each one asks of otaku is an endpoint,
   named in the page's client (`api/`). */

import { api } from "./api/index.js";
import { ask, askConfirm, closeAll, popups } from "./kit/browser.js";
import { $ } from "./kit/dom.js";
import * as held from "./play/held.js";
import { openHelp, openSupport } from "./screens/help.js";
import { openSettings } from "./screens/settings.js";
import { openModels } from "./screens/models.js";
import { openBalance, openContext, openInfo, openUsage } from "./screens/reports.js";
import { landed, refresh, watchExtraction } from "./play/shell.js";
import { openCharacters } from "./screens/characters.js";
import { openStories } from "./screens/stories.js";
import { confirmFork, openStory } from "./screens/dossier.js";
import { tell } from "./kit/status.js";
import { clear, isPlaying, play, stopPlaying, takeBack } from "./play/transcript.js";
import { exportStory, importCard, importDocument } from "./screens/transfer.js";

const SCREENS = {
  "/stories": openStories,
  /* The two that make a story are asked about first: both are one click
     from the story you are reading, and `/new` in particular reads as
     "close this". A TYPED `/new TITLE` or `/fork TITLE` goes straight
     to the backend — the reader who spelled it out has said yes. */
  "/new": newStory,
  "/fork": () =>
    confirmFork(async () => {
      const story = held.story();
      if (story === null) return tell(_NO_STORY.fork, "otk-error");
      const forked = await api.stories.forks.create(story);
      // play goes on in the copy
      if (!forked.refused) held.hold(forked.value.id);
      await landed(forked.notice, { redraw: "always", refused: forked.refused });
    }),
  /* The story dossier answers three commands, one tab each — the bare
     token opens it there; `/system some text` is answered by the
     backend, exactly as `/model` and `/model x/y` divide. */
  "/system": () => openStory({ tab: "premise", allStories: backToStories }),
  "/lore": () => openStory({ tab: "scenes", allStories: backToStories }),
  "/cast": () => openStory({ tab: "cast", allStories: backToStories }),
  "/model": openModels,
  "/set": openSettings,
  "/context": openContext,
  "/usage": openUsage,
  "/balance": openBalance,
  "/info": openInfo,
  "/support": openSupport,
  "/help": () => openHelp(),
  "/import": importStory,
  "/card": importCard,
  "/export": exportStory,
  "/extract": extractNow,
  "/undo": undo,
  "/regen": regenerate,
  "/last": () => refresh(),
  "/clear": () => clear(),
  // A tab is not the server: closing one must not stop a server the
  // reader may be using from another, so /quit says where the exit is.
  "/quit": () => tell("Close the tab. The server stops in the terminal."),
};

export async function run(token) {
  /* One row of the contents, opened. The token is a UI key — what the
     rail's button carries — and never a line anybody typed. */
  try {
    const screen = SCREENS[token];
    if (screen) await screen();
    else console.warn("otaku: no screen for", token);
  } catch (e) {
    // The reader gets the sentence; the console gets the stack, because
    // a TypeError inside a screen is a bug, not an answer.
    console.error(e);
    tell(String(e.message ?? e), "otk-error");
  }
}

/** Where a dossier's back button lands when no story browser waits
    underneath it: the browser, positioned on the story it came from. */
const backToStories = (storyId) => openStories("", { selectId: storyId });

/** The contents row that is not a command: the open story's messages,
    which live on the dossier the way its scenes and cast do. A UI door,
    not a token — so the dossier stays reachable without inventing a
    command nobody typed. */
export async function openTab(tab) {
  try {
    await openStory({ tab, allStories: backToStories });
  } catch (e) {
    console.error(e);
    tell(String(e.message ?? e), "otk-error");
  }
}

/** The contents row that is a screen of the page's own: All characters,
    the codex's cards — no command yet, the terminal's screen to come. */
export async function openCodex() {
  try {
    await openCharacters();
  } catch (e) {
    console.error(e);
    tell(String(e.message ?? e), "otk-error");
  }
}

/** Whether a command carried by a panel's own chrome KEEPS that panel.
    A popup closes before a command in its header runs, the answer
    usually landing in the flow behind it — but two kinds stay where they
    were fired from: one that asks a question (cancelled, it has no
    answer, and the reader was taken out of the screen for nothing), and
    one whose whole result belongs to that screen. */
export function keepsScreen(token) {
  return ["/new", "/fork", "/extract", "/import", "/export", "/card"].includes(token);
}

async function newStory() {
  /* A new story is one question with an optional answer: what to call
     it. Left empty, the listing names it from its first rollup — which
     is the sentence under the field. */
  const dialog = $('dialog[data-dialog="new-story"]');
  const field = $("input", dialog);
  const choice = await ask("new-story", () => (field.value = ""));
  if (choice !== "start") return;
  closeAll();
  const made = await api.stories.create({ title: field.value.trim() });
  if (!made.refused) held.hold(made.value.id);
  await landed(made.notice, { redraw: "always", refused: made.refused });
}

async function importStory() {
  /* The imported story lands in the library the reader is looking at, so
     the list is asked again and left on what just arrived. */
  await importDocument();
  if (popups.get("/stories")?.open) await openStories("", { selectId: held.story() });
}

async function confirmed({ title, body, note = "", action, cancel = "Cancel", run }) {
  /* One question, one button that answers it. The words are set here:
     what a command is about to do to the story on screen is the page's
     to say, as the delete confirm says what a delete takes. */
  if ((await askConfirm({ title, body, note, action, cancel })) !== "confirm") return;
  // Now the screens go: the story is about to change under them, and the
  // sentence that says so belongs in the flow.
  closeAll();
  await run();
}

/** Play a line as story. A LOST connection already drew the offline
    state (`api.whenLost` → `shell.disconnected`); what is reported here
    is the other kind — a fault the server ANSWERED, which is a bug to
    show and never a state to draw. */
export async function playLine(line, files = []) {
  try {
    const refused = await play(line, { files });
    /* A played line is a write like any other and the runhead is drawn
       from facts that just changed — a first line makes the story, and
       every line after it moves the count. No notice: the
       reply IS the answer — unless the line was refused, and then the
       refusal stands through the landing rather than being wiped by it. */
    await landed(refused ?? "", { refused: Boolean(refused), keepPlace: true });
  } catch (e) {
    if (e?.answered) tell(String(e.message ?? e), "otk-error");
  }
}

// ---------- the rows that are not one call ----------

/* Both doors to these — the turn bar and the composer — come through
   here, so the refusal is written once. Taking a turn back while the
   next one is still arriving would take back the WRONG one: the request
   queues behind the reply and lands after it. */

const _MID_REPLY = "Wait for the reply to finish, or stop it.";

export function midReply() {
  if (!isPlaying()) return false;
  // Under the prompt, where the reader is: this is about the box, not
  // about the scene, and the story must not carry a line nobody played.
  tell(_MID_REPLY);
  return true;
}

async function undo() {
  if (midReply()) return;
  /* The notice is not shown: the exchange coming off the screen IS the
     answer. A refusal — nothing to undo — still speaks, under the
     prompt, and the FLAG is what says so; the page never reads the
     wording. */
  const story = held.story();
  const answer =
    story === null ? { notice: _NOTHING_TO_UNDO, refused: true } : await api.stories.undos.create(story);
  if (!answer.refused) takeBack(await api.stories.messages.list(story), answer.value.question);
  await landed("");
  if (answer.refused) tell(answer.notice, "otk-error");
}

/* What the backend says with no exchange to take back — COPIED, as the
   page has no story to ask (`backend.story.stories.services.NOTHING_TO_UNDO`). */
const _NOTHING_TO_UNDO = "Nothing to undo.";

async function regenerate() {
  /* Mid-reply, regenerate means "not this one, try again", as `/regen`
     does in the terminal. Stop is the same door, so this takes it and
     waits for the socket to be over before asking for the next take. */
  if (isPlaying()) await stopPlaying();
  // `play` takes the old reply off the screen once the request is
  // accepted, so a refusal leaves the story exactly as it was. A lost
  // connection is `api.whenLost`'s; an answered fault is shown.
  try {
    await play(null, { regenerate: true });
  } catch (e) {
    if (e?.answered) tell(String(e.message ?? e), "otk-error");
  }
}

// The page's own sentence: a fault of the medium, not the story's.
const _BEHIND = "The story changed since this page drew it — reload the page to edit this message.";

/** A turn corrected in the transcript (`transcript.whenEdited`). The page
    keeps no message ids: the turn is found by its place, and written only
    while it still holds the text the page drew. */
export async function editTurn(position, drawn, text) {
  // a write mid-reply would wait behind the reply with the field open
  if (isPlaying()) return { notice: _MID_REPLY, refused: true };
  const story = held.story();
  const turns = story === null ? [] : await api.stories.messages.list(story);
  const turn = turns[position];
  if (turn?.body !== drawn) return { notice: _BEHIND, refused: true };
  const answer = await api.stories.messages.update(story, turn.id, { body: text });
  // the story's name falls back to its first line, so the runhead asks again
  if (!answer.refused) await landed("");
  return answer;
}

/* What the backend says when there is no story to act on. COPIED,
   because the page cannot ask: these endpoints address a story by id,
   and with no story there is no id for the path. Their homes are
   `backend.story.stories.services.fork_story` and `backend.errors.NO_STORY_HINT`. */
const _NO_STORY = {
  fork: "Nothing to fork yet — send a message first.",
  extract: "No story yet — send a message first.",
};

async function extractNow() {
  /* The question says what a pass does — and that, left alone, it
     happens anyway, which is the normal way this runs. With nothing
     outside a scene there is nothing to ask: the pass would only
     decline. */
  const story = held.story();
  if (story === null) {
    tell(_NO_STORY.extract, "otk-error");
    return;
  }
  const [memory, knobs] = await Promise.all([
    api.stories.lore.get(story).catch(() => null),
    api.settings.get().catch(() => null),
  ]);
  if (memory?.refused) {
    tell(memory.notice, "otk-error");
    return;
  }
  // the messages past the last one a scene closed
  const pending = (memory?.message_count ?? 0) - (memory?.extracted_up_to ?? 0);
  if (!pending) {
    tell("Every message already belongs to a scene. There is nothing to extract.");
    return;
  }
  await confirmed({
    title: "Extract the lore now?",
    body:
      `The last ${pending} ${pending === 1 ? "message belongs" : "messages belong"} to no scene yet. ` +
      "Extracting cuts them into scenes — a title and a summary each, a journal entry for every " +
      "character present.",
    note: `Left alone, otaku does this by itself after ${idleSpan(knobs?.idle_seconds)} at the prompt.`,
    action: "Extract now",
    run: () => extract(story),
  });
}

function idleSpan(seconds) {
  /* The configured idle wait, said as a reader counts it: whole minutes
     where it is one, seconds otherwise; the default where the figure
     did not arrive. */
  const total = Number.isFinite(seconds) ? seconds : 300;
  if (total >= 60 && total % 60 === 0) {
    const minutes = total / 60;
    return `${minutes} ${minutes === 1 ? "minute" : "minutes"}`;
  }
  return `${total} ${total === 1 ? "second" : "seconds"}`;
}

async function extract(story) {
  const started = await api.stories.extraction.create(story);
  closeAll();
  tell(started.notice, started.refused ? "otk-error" : "");
  if (started.value?.status === "running") watchExtraction(story);
}
