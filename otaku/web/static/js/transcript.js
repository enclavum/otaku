/* How a turn looks — stored or arriving.

   Bodies cross from the backend VERBATIM: this file decides how a slash
   token is drawn and where the caret rides, and rewrites none of the
   text itself — a message is one `pre-wrap` block, its line breaks the
   text's own.

   The page draws the story as a book does: a reply is prose in the flow,
   a played line a centred interjection under a mono rubric — the
   terminal's `>` band in this medium's shape. Waiting and streaming are
   ONE state. Three kinds of line are not the story and never read as
   it: the model's reasoning, the verbose stats line, and a failure.

   It draws the undo/regen bar without knowing what those do — the
   buttons carry `data-turn` and whoever owns commands listens — and a
   stored turn as the dossier's editor, handing the write to whoever
   `whenEdited` names, which keeps this a drawing, not a controller. */

import * as api from "./api.js";
import { editable, edited } from "./browser.js";
import { $, $$, element, span } from "./dom.js";
import { typesetBody } from "./prose.js";
import { tell, working } from "./status.js";
import { isToken } from "./table.js";

const transcript = $(".otk-transcript");
const stop = $("[data-stop]");
const send = $("[data-send]");

// The reply in flight, so Stop has something to stop, and a promise that
// keeps until it is over, for whoever asked it to stop.
let arriving = null;
let settled = Promise.resolve();

// one turn at a time, as in the terminal
let playing = false;

// the rubric's number for the NEXT drawn turn, counted the way the story
// browser counts messages
let ordinal = 0;

// who writes a corrected turn (`whenEdited`)
let saveEdit = null;

export const isPlaying = () => playing;

// ---------- what is on screen ----------

export function showTurns(turns, { keepPlace = false } = {}) {
  /* `keepPlace` is for a redraw the reader did not ask to be moved by —
     a refused regenerate: the turns change, the scroll does not. */
  ordinal = turns.length;
  const drawn = turns.map((turn, i) => drawTurn(turn, i + 1));
  if (keepPlace) {
    holdSpace(() => transcript.replaceChildren(...drawn));
    showTurnBar();
    return;
  }
  release();
  transcript.replaceChildren(...drawn);
  showTurnBar();
  toBottom();
}

/** An undo, drawn: the turns past what the store still holds come off
    where they stand, as the terminal erases the exchange in place, and
    the turns left keep what only a live reply draws beside them — the
    stats line, a roll's dice. A page that is not the store's turns with
    the end cut off is drawn again instead. */
export function takeBack(turns) {
  const drawn = storedTurns();
  const same = turns.every(
    (turn, i) => drawn[i] && (turn.role === "user") === drawn[i].classList.contains("otk-turn"),
  );
  if (!same) {
    showTurns(turns, { keepPlace: true });
    return;
  }
  const kept = drawn[turns.length - 1];
  const gone = [];
  let node = kept ? kept.nextElementSibling : transcript.firstElementChild;
  for (; node; node = node.nextElementSibling) {
    if (!node.classList.contains("otk-gap")) gone.push(node);
  }
  ordinal = turns.length;
  holdSpace(() => gone.forEach((each) => each.remove()));
  showTurnBar();
}

export function clear() {
  transcript.replaceChildren();
}

/** Who writes a corrected turn: handed the turn's place among the stored
    turns, the text the page drew it from, and the new text, and answering
    as `browser.editable`'s `save` does. */
export function whenEdited(save) {
  saveEdit = save;
}

/** A turn corrected elsewhere — the dossier — shown where the transcript
    draws it: `position` counts stored turns from 1, as the dossier does. */
export function showCorrected(position, text) {
  const turn = storedTurns()[position - 1];
  const field = turn && $("textarea.otk-editable", turn);
  if (!field) return;
  field._settle(text);
  $(".otk-reader", turn)._repaint();
}

/** The drawn turns the store holds, in its order: a failed attempt keeps
    its block to say so but stores nothing (`endTurn` marks those). */
function storedTurns() {
  return $$(".otk-turn, .otk-reply", transcript).filter((a) => !a.dataset.unstored);
}

/** Send while the box is the reader's, Stop while the model has it. */
function turnOver(streaming) {
  if (send) send.hidden = streaming;
  if (stop) stop.hidden = !streaming;
}

/** Give up on the reply that is arriving, and answer when it is over.
    The connection going away IS the backend's cancel-and-keep door — the
    one a closed tab takes — so what streamed stays in the story. */
export function stopPlaying() {
  arriving?.abort();
  return settled;
}

/* ---------- holding the reader's place ----------

   Taking turns away shortens the flow, and the browser clamps a scroll
   position that no longer exists — everything above jumps. So an empty
   block is held open after the last turn, exactly as tall as the
   position needs to stay legal:

       held = (where we are) + (what we can see) - (what is left)

   It is zero the moment the flow grows back into it, and never more than
   one screenful. A BLOCK rather than padding on the flow: padding is
   part of the scroller's own box, and a scroller that grows takes the
   page with it. `overflow-anchor` is off in the stylesheet so Chrome and
   Firefox do not fight this with anchoring of their own.

   Where we are is where the reader is NOW, not where they were when the
   space opened: they may scroll away while it is held, so the block is
   only ever resized and the page never scrolls to keep it. */

let holding = false; // whether a block is held open after the last turn

function holdSpace(change) {
  const top = transcript.scrollTop;
  change();
  holding = true;
  reserve(top);
  // measuring laid out the shorter flow and clamped the position
  transcript.scrollTop = top;
}

function reserve(top = transcript.scrollTop) {
  if (!holding) return;
  const gap = room();
  // Where the block starts, not the scroll height less the block: that is
  // never less than the view, so it cannot measure a story that fits.
  const view = transcript.getBoundingClientRect();
  const story =
    gap.getBoundingClientRect().top - view.top - transcript.clientTop + transcript.scrollTop +
    parseFloat(getComputedStyle(transcript).paddingBottom);
  const needed = Math.max(0, top + transcript.clientHeight - story);
  gap.style.height = `${needed}px`;
  // caught up: holding now only keeps a scrollbar longer than the story
  if (!needed) release();
}

function release() {
  holding = false;
  room().style.height = "0px";
}

function room() {
  let gap = $(".otk-gap", transcript);
  if (!gap) {
    gap = element("div", "otk-gap");
    gap.setAttribute("aria-hidden", "true");
  }
  // `replaceChildren` takes it with the turns; it belongs last, always.
  if (gap.parentElement !== transcript || gap.nextSibling) transcript.append(gap);
  return gap;
}

function atTail() {
  const gap = $(".otk-gap", transcript);
  const empty = gap ? gap.getBoundingClientRect().height : 0;
  // held space is not story: a reader sitting above it is at the tail
  return transcript.scrollHeight - empty - transcript.scrollTop - transcript.clientHeight < 120;
}

/* ---------- following a reply down ----------

   A reply carries the reader down only while they stay at the end: any
   scroll up — wheel, trackpad, scrollbar, key — lets go of them, and
   scrolling back to the end takes them along again. Whether they were
   at the tail when the line went out decides where it starts.

   The page's own jump must not swallow the reader's scroll, so it waits
   for the next frame — the browser reports where the reader went BEFORE
   frame callbacks run — and records where it left the flow. A move up
   from there is the reader's, unless it lands on the very end: that is
   the browser clamping a flow that got shorter. */

const REJOIN = 32; // about a line of prose from the end counts as at it

let following = false; // whether the reply in flight carries the reader down
let lastTop = 0; // where the flow was last seen, moved by either side
let snap = 0; // the frame a jump waits for, 0 when none

transcript.addEventListener(
  "scroll",
  () => {
    const top = transcript.scrollTop;
    const left = transcript.scrollHeight - top - transcript.clientHeight;
    if (top < lastTop && left >= 1) following = false;
    else if (left <= REJOIN) following = true;
    lastTop = top;
  },
  { passive: true },
);

function toBottom() {
  transcript.scrollTop = transcript.scrollHeight;
  lastTop = transcript.scrollTop;
}

function follow() {
  // While space is held the flow does not move — the text is going where
  // the reader is already looking. Once that space is filled the flow
  // follows it down, for as long as the reader stays with it.
  if (snap) return;
  snap = requestAnimationFrame(() => {
    snap = 0;
    if (!holding && following) toBottom();
  });
}

// ---------- drawing a turn ----------

function drawTurn(turn, position) {
  if (turn.role === "user") {
    const article = element("article", "otk-turn");
    const rubric = element("span", "otk-turn__rubric", `◆ ${position} · you`);
    article.append(rubric, correctable("otk-turn__body", turn.body, withSlashTokens));
    return article;
  }
  const article = element("article", "otk-reply");
  article.append(correctable("otk-prose", turn.body, typesetBody));
  return article;
}

function correctable(className, body, nodesOf) {
  /* A stored turn is corrected where it is read, as the dossier corrects
     one (`story.reader`): the block that reads and the field that edits
     wear one class in one box, both keeping the text's own line breaks,
     so opening it moves nothing but what the marks and faces change. */
  const read = element("div", `otk-editable ${className} otk-typeset`);
  const both = element("div", "otk-reader");
  const field = editable(className, {
    text: body,
    save: (text) => saveEdit(storedTurns().indexOf(both.closest("article")), field._stored(), text),
  });
  both._repaint = () => read.replaceChildren(...nodesOf(field.value));
  both.append(read, field);
  return edited(both, "", "otk-edit--turn");
}

function withSlashTokens(line) {
  /* The typed line with its command words picked out, as the terminal
     highlights them. Split on whitespace runs, so the line comes back
     exactly as it went in. */
  return line.split(/(\s+)/).map((piece) => {
    if (!isToken(piece)) return document.createTextNode(piece);
    return element("span", "otk-slash", piece);
  });
}

function caret() {
  const it = element("span", "otk-caret");
  it.setAttribute("aria-hidden", "true");
  return it;
}

function showTurnBar() {
  /* undo and regenerate belong to the last exchange and to no other, as
     in the terminal. They live under the PROMPT rather than the turn:
     they are what you do next, and next is where the cursor is. Drawn
     once in the markup, so this only decides what there is to act on. */
  const turns = $$(".otk-turn, .otk-reply", transcript).length;
  /* Regenerate answers mid-reply too — it means "not this one" and takes
     the door Stop does; undo cannot, the turn having not landed yet. */
  const undo = $("[data-turn='undo']");
  const regen = $("[data-turn='regen']");
  undo?.setAttribute("aria-disabled", String(!turns || playing));
  regen?.setAttribute("aria-disabled", String(!turns));
}

/* ---------- a turn arriving ----------

   One reply, told in three parts: `beginTurn` opens the block the reply
   will land in, one drawer per event kind fills it (the same closed
   union `web.api.event` writes), and `endTurn` settles it — whatever
   ended it. `play` below is only the order of those. */

/** Play a line and draw the reply as it streams. `line` is null for a
    regenerate, where the prompt is already on screen and in the store. */
export async function play(line, { regenerate = false } = {}) {
  // Without this the first of two plays to finish unlocks the composer
  // for both, leaving prose on screen that the store never took.
  if (playing) return;
  const turn = beginTurn(regenerate);
  let refused = null;
  try {
    const answer = await api.play(line ?? "", { regenerate, signal: arriving.signal });
    // A line that is not valid syntax never becomes a stream: the usage
    // sentence comes back instead, and the story is untouched.
    if (answer.refused) {
      refused = answer.refused;
      // the backend promised the story is untouched, so the screen says
      // the same and the refused regenerate keeps its reply
      if (regenerate) showTurns(await api.turns(), { keepPlace: true });
      return;
    }
    for await (const happened of answer.events) {
      draw(turn, happened);
    }
  } catch (e) {
    // stopping is not failing: what streamed is already in the story
    if (e?.name !== "AbortError") throw e;
  } finally {
    endTurn(turn);
    // said AFTER the turn settles, or its cleanup sweeps the refusal
    // away before anyone reads it
    if (refused) tell(refused, "otk-error");
  }
}

function beginTurn(regenerate) {
  /* The reply follows only a reader who was at the tail. A regenerate
     also redraws into the space the old take was read in, and follows
     only once that space is filled. Measured before anything moves. */
  following = atTail();
  playing = true;
  arriving = new AbortController();
  let over;
  settled = new Promise((resolve) => (over = resolve));
  turnOver(true);
  tell("");
  // Stop is the composer's own button, where the reply was asked for, so
  // the status line says only that something is running
  working(true);
  showTurnBar();
  const article = element("article", "otk-reply otk-generating is-streaming");
  /* The transcript is a polite live region and a reply rewrites its text
     several times a second: `aria-busy` holds the subtree, so a screen
     reader announces it once when it settles instead of forty times. */
  article.setAttribute("aria-busy", "true");
  // Waiting and writing are ONE state: the caret holds the answer's
  // place from the first moment, with the status line under it.
  const block = element("div", "otk-editable otk-prose otk-typeset");
  block.append(caret());
  const state = element("span", "otk-generating__text", "waiting");
  const elapsed = element("span", "otk-generating__text otk-generating__elapsed", "0.0s");
  const status = element("div", "otk-generating__status");
  status.setAttribute("aria-hidden", "true");
  status.append(element("span", "otk-generating__rule"), state, elapsed);
  article.append(block, status);
  const started = Date.now();
  const ticking = setInterval(() => {
    elapsed.textContent = `${((Date.now() - started) / 1000).toFixed(1)}s`;
  }, 100);
  /* A regenerate takes the standing reply off the screen before the
     request is away: the take being replaced must not sit there while
     the model thinks. The backend validates a regenerate eagerly, so a
     refusal comes back before anything is lost. The waiting block goes
     in its place in the same move — a regenerate sends no Recorded for
     the block to join the flow on (`draw`), and the wait must show from
     the first moment here as it does on a send. */
  if (regenerate) {
    holdSpace(() => {
      dropLastReply();
      transcript.insertBefore(article, $(".otk-gap", transcript));
    });
  }
  showTurnBar();
  return { article, block, status, state, ticking, over, reasoning: null, prose: "" };
}

function draw(turn, happened) {
  // A send's block joins the flow on its first event, the Recorded that
  // draws the line above it; a regenerate's is in place already
  // (`beginTurn`). Ahead of the held space, which belongs last: behind
  // it, the block would sit between two turns.
  if (!turn.article.isConnected) transcript.insertBefore(turn.article, $(".otk-gap", transcript));
  DRAW[happened.type]?.(turn, happened);
  // what arrives goes into the space the old take was read in
  reserve();
  follow();
}

// One drawer per event kind — `web.api.event`'s closed union, drawn.
const DRAW = {
  recorded(turn, happened) {
    ordinal += 1;
    const drawn = drawTurn(happened.turn, ordinal);
    /* The record's own dim line — the dice a /roll rolled, verbatim.
       Drawn at record time the way the terminal prints its dim block:
       a redraw from the store does not carry it, and neither keeps it. */
    if (happened.note) drawn.append(element("p", "otk-turn__note", happened.note));
    /* The line takes the place of the row the reply above kept when it
       landed: the flow grows by more than the row, so nothing moves. */
    const above = turn.article.previousElementSibling;
    if (above?.classList.contains("otk-generating--idle")) {
      $(".otk-generating__status", above)?.remove();
    }
    transcript.insertBefore(drawn, turn.article);
  },
  reasoning(turn, happened) {
    if (!turn.reasoning) {
      turn.reasoning = element("p", "otk-reasoning", "(reasoning) ");
      turn.article.prepend(turn.reasoning);
    }
    turn.reasoning.textContent += happened.text;
    turn.state.textContent = "reasoning";
  },
  text(turn, happened) {
    turn.prose += happened.text;
    turn.state.textContent = "writing";
    /* Typeset again from the whole text rather than appended to: a quote
       or a mark closes mid-stream, and only the whole text knows what it
       closed. The caret rides the end of what has arrived. */
    turn.block.replaceChildren(...typesetBody(turn.prose), caret());
  },
  declined(turn, happened) {
    turn.article.append(failure("The model declined", happened.reason));
  },
  failed(turn, happened) {
    turn.article.append(failure("The reply stopped", happened.reason));
  },
  done(turn, happened) {
    /* The stats line the terminal prints after a reply, verbatim — off
       unless the reader asked for it (`/set verbose`). It goes BEFORE
       the status row, which stays and keeps its height once a reply
       lands: appended after it, the line would sit against the row
       rather than against the answer it reports on. */
    if (!happened.stats) return;
    turn.article.insertBefore(
      element("p", "otk-verbose", happened.stats),
      $(".otk-generating__status", turn.article),
    );
  },
};

function failure(label, reason) {
  /* A failure is not a turn and never reads as the story: its own rule,
     its own voice, two doors out. The LABEL is the medium's own word;
     the sentence under it is the backend's, unchanged. */
  const box = element("div", "otk-error");
  const actions = element("div", "otk-error__actions");
  const again = element("button", "otk-btn", "Try again");
  again.type = "button";
  again.dataset.turn = "regen";
  const other = element("button", "otk-btn", "Choose another model");
  other.type = "button";
  other.dataset.command = "/model";
  actions.append(again, other);
  box.append(
    span("otk-error__label", label),
    element("p", "otk-error__body", reason),
    actions,
  );
  return box;
}

function endTurn(turn) {
  // Whatever ended it — the last event, Stop, a connection that went
  // away — the turn stops looking like it is still arriving.
  playing = false;
  arriving = null;
  clearInterval(turn.ticking);
  turn.over();
  turnOver(false);
  working(false);
  // what the page had to say about the attempt is over with it
  tell("");
  // the status row STAYS, hidden, so nothing above it moves — until the
  // next line takes its place (`recorded`)
  turn.article.classList.add("otk-generating--idle");
  turn.article.classList.remove("is-streaming");
  turn.article.setAttribute("aria-busy", "false");
  /* What arrived is stored exactly as it streamed, so it becomes the
     same editor a stored reply is drawn as, in the same box; the block
     that held the answer's place goes when nothing came. */
  if (turn.prose) turn.block.replaceWith(correctable("otk-prose", turn.prose, typesetBody));
  else turn.block.remove();
  // a reply that never arrived leaves no empty block behind
  if (!turn.prose && !$(".otk-error, .otk-verbose, .otk-reasoning", turn.article)) {
    turn.article.remove();
  } else if (turn.article.isConnected && turn.prose) {
    /* The reply landed, so the story is one turn longer than the rubric
       counted. TEXT is the test, not the block: a declined or failed
       attempt keeps its block to say so but stores no message
       (`backend.api.play._land_reply` records a reply row only when
       some text arrived), and counting it would number every later turn
       one too high. */
    ordinal += 1;
  } else if (turn.article.isConnected) {
    // Kept only to say what happened, storing nothing: marked, so a
    // regenerate dropping it takes no number with it.
    turn.article.dataset.unstored = "true";
  }
  showTurnBar();
  follow();
}

/* the standing reply comes off, so the fresh take streams in its place
   rather than under it */
function dropLastReply() {
  const last = [...$$(".otk-turn, .otk-reply", transcript)].pop();
  if (last && last.classList.contains("otk-reply")) {
    last.remove();
    // an article kept only to say a take failed was never counted, so
    // dropping it must not un-count a turn (`endTurn` marks those)
    if (!last.dataset.unstored) ordinal -= 1;
  }
}
