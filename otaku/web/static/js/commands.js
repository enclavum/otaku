/* What a row of the contents opens, and the few doors that are a call
   rather than a screen.

   Every one of these is reached by a BUTTON — nothing here composes a
   command line for the far end to parse. `SCREENS` is keyed by the token
   the contents rail carries; what each one asks of otaku is an endpoint,
   named in `api.js`. */

import * as api from "./api.js";
import { ask, closeAll, popups } from "./browser.js";
import { $ } from "./dom.js";
import { openHelp } from "./help.js";
import { openSettings } from "./settings.js";
import { openModels } from "./models.js";
import { openBalance, openContext, openInfo, openUsage } from "./reports.js";
import { landed, refresh, watchExtraction } from "./shell.js";
import { openStories } from "./stories.js";
import { confirmFork, openStory } from "./story.js";
import { tell } from "./status.js";
import { clear, isPlaying, play, stopPlaying, takeBack } from "./transcript.js";
import { exportStory, importCard, importDocument } from "./transfer.js";

const SCREENS = {
  "/stories": openStories,
  /* The two that make a story are asked about first: both are one click
     from the story you are reading, and `/new` in particular reads as
     "close this". A TYPED `/new TITLE` or `/fork TITLE` goes straight
     to the backend — the reader who spelled it out has said yes. */
  "/new": newStory,
  "/fork": () =>
    confirmFork(async () => {
      const story = (await api.facts()).story_id;
      if (story === null) return tell(_NO_STORY.fork, "otk-error");
      const { notice } = await api.fork(story);
      await landed(notice, { redraw: "always" });
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
  "/help": () => openHelp(),
  "/import": importStory,
  "/card": importCard,
  "/export": exportStory,
  "/extract": extractNow,
  "/undo": undo,
  "/regen": regenerate,
  "/last": () => refresh(),
  "/clear": () => clear(),
  // A tab is not the session: closing one must not stop a server the
  // reader may be using from another, so /bye says where the exit is.
  "/bye": () => tell("Close the tab. The server stops in the terminal."),
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
export async function openMessages() {
  try {
    await openStory({ tab: "messages", allStories: backToStories });
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
  return ["/new", "/fork", "/extract", "/import", "/export"].includes(token);
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
  const { notice } = await api.newStory(field.value.trim());
  await landed(notice, { redraw: "always" });
}

async function importStory() {
  /* The imported story lands in the library the reader is looking at, so
     the list is asked again and left on what just arrived. */
  await importDocument();
  if (popups.get("/stories")?.open) {
    const facts = await api.facts();
    await openStories("", { selectId: facts.story_id });
  }
}

async function confirmed({ title, body, note = "", action, cancel = "Cancel", run }) {
  /* One question, one button that answers it. The words are set here:
     what a command is about to do to the story on screen is the page's
     to say, as the delete confirm says what a delete takes. */
  const choice = await ask("confirm", (dialog) => {
    $("[data-title]", dialog).textContent = title;
    $(".otk-dialog__body", dialog).textContent = body;
    // A second line, for a question whose answer has a consequence
    // worth spelling out; hidden for the plain ones.
    const aside = $("[data-note]", dialog);
    aside.textContent = note;
    aside.hidden = !note;
    $('[data-choice="confirm"]', dialog).textContent = action;
    $('[data-choice="cancel"]', dialog).textContent = cancel;
  });
  if (choice !== "confirm") return;
  // Now the screens go: the story is about to change under them, and the
  // sentence that says so belongs in the flow.
  closeAll();
  await run();
}

/** Play a line as story. A LOST connection already drew the offline
    state (`api.whenLost` → `shell.disconnected`); what is reported here
    is the other kind — a fault the server ANSWERED, which is a bug to
    show and never a state to draw. */
export async function playLine(line) {
  try {
    await play(line);
    /* A played line is a write like any other and the runhead is drawn
       from facts that just changed — the first line of a session makes
       the story, and every line after it moves the count. No notice: the
       reply IS the answer. */
    await landed("");
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
  const answer = await api.undo();
  if (!answer.refused) takeBack(await api.turns());
  await landed("");
  if (answer.refused) tell(answer.notice);
}

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
  const [facts, turns] = await Promise.all([api.facts(), api.turns()]);
  const turn = turns[position];
  if (turn?.body !== drawn) return { notice: _BEHIND, refused: true };
  const answer = await api.editMessage(facts.story_id, turn.id, text);
  // the story's name falls back to its first line, so the runhead asks again
  if (!answer.refused) await landed("");
  return answer;
}

/* What the backend says when there is no story to act on. COPIED,
   because the page cannot ask: these endpoints address a story by id,
   and with no story there is no id for the path. Their homes are
   `backend.api.stories.fork` and `backend.api.lore.extract`. */
const _NO_STORY = {
  fork: "Nothing to fork yet — send a message first.",
  extract: "No story yet — send a message first.",
};

async function extractNow() {
  /* A pass is minutes of model time, so the question says so — and says
     what happens if the reader waits instead, waiting being the normal
     way this runs. */
  const facts = await api.facts();
  const opened = facts.story_id == null ? null : await api.story(facts.story_id).catch(() => null);
  const unread = opened?.unread ?? 0;
  await confirmed({
    title: "Read them now?",
    body:
      (unread
        ? `${unread} ${unread === 1 ? "message has" : "messages have"} not been read into scenes and cast. `
        : "Everything played has been read already. ") +
      "Reading asks the model for a summary, a history and a journal per character — it can take a minute or two.",
    note: "Otherwise it happens on its own, five minutes after you stop typing.",
    action: "Read now",
    cancel: "Wait",
    run: () =>
      facts.story_id === null
        ? tell(_NO_STORY.extract, "otk-error")
        : extract(facts.story_id),
  });
}

async function extract(story) {
  const { notice, watching } = await api.extract(story);
  closeAll();
  tell(notice);
  if (watching) watchExtraction(story);
}
