/* How a turn looks — stored or arriving.

   Bodies cross from the backend VERBATIM: this file decides where a
   paragraph breaks, how a slash token is drawn and where the caret
   rides, and rewrites none of the text itself.

   The page draws the story as a book does: a reply is prose in the flow,
   a played line a centred interjection under a mono rubric — the
   terminal's `>` band in this medium's shape. Waiting and streaming are
   ONE state. Three kinds of line are not the story and never read as
   it: the model's reasoning, the verbose stats line, and a failure.

   It draws the undo/regen bar without knowing what those do — the
   buttons carry `data-turn` and whoever owns commands listens — which
   keeps this a drawing, not a controller. */

import * as api from "./api.js";
import { $, $$, element, span } from "./dom.js";
import { typeset } from "./prose.js";
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

export const isPlaying = () => playing;

// ---------- what is on screen ----------

export function showTurns(turns, { keepPlace = false } = {}) {
  /* `keepPlace` is for a redraw the reader did not ask to be moved by —
     an undo, a regenerate: the turns change, the scroll does not. */
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
  toBottom({ force: true });
}

export function clear() {
  transcript.replaceChildren();
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
   Firefox do not fight this with anchoring of their own. */

let holdAt = null; // the scroll position being protected, null when none

function holdSpace(change) {
  const top = transcript.scrollTop;
  change();
  holdAt = top;
  reserve();
}

function reserve() {
  if (holdAt === null) return;
  const gap = room();
  gap.style.height = "0px";
  const needed = Math.max(0, holdAt + transcript.clientHeight - transcript.scrollHeight);
  gap.style.height = `${needed}px`;
  transcript.scrollTop = holdAt;
  // caught up: holding now only keeps a scrollbar longer than the story
  if (!needed) release();
}

function release() {
  holdAt = null;
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

function toBottom({ force = false } = {}) {
  /* Only when the reader is already there: a story being read further up
     must not be yanked down by an undo or a re-run. `force` is the one
     case that is the reader asking — a line they just sent. */
  if (force || atTail()) transcript.scrollTop = transcript.scrollHeight;
}

// ---------- drawing a turn ----------

function drawTurn(turn, position) {
  if (turn.role === "user") {
    const article = element("article", "otk-turn");
    const rubric = element("span", "otk-turn__rubric", `◆ ${position} · you`);
    const line = element("p", "otk-turn__body");
    line.append(...withSlashTokens(turn.body));
    article.append(rubric, line);
    return article;
  }
  const article = element("article", "otk-reply");
  drawProse(article, turn.body);
  return article;
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

function drawProse(article, text, { streaming = false } = {}) {
  /* The reply as it stands, re-typeset from the whole text rather than
     appended to: a paragraph break arrives mid-stream like any other
     character, and only the whole text knows where the breaks are. */
  const paragraphs = text.split(/\n\s*\n/).filter((part) => part.trim());
  const drawn = $$(".otk-prose", article);
  paragraphs.forEach((paragraph, i) => {
    const p = drawn[i] ?? article.insertBefore(element("p"), $(".otk-generating__status", article));
    // one accent, two shapes: an all-speech paragraph takes it whole, a
    // mixed one a run at a time
    const { spoken, nodes } = typeset(paragraph.trim());
    p.className = spoken ? "otk-prose otk-prose--dialogue" : "otk-prose";
    p.replaceChildren(...nodes);
  });
  // the caret rides the end of what has arrived
  $(".otk-caret", article)?.remove();
  if (streaming) {
    const caret = element("span", "otk-caret");
    caret.setAttribute("aria-hidden", "true");
    ($$(".otk-prose", article).at(-1) ?? article).append(caret);
  }
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
  /* A played line is the reader asking for something new at the bottom;
     a regenerate is not — it redraws into the space the old take was
     read in, and follows the text down only once that space is filled,
     and only for a reader who was at the tail. Measured before anything
     moves. */
  const tail = atTail();
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
  const held = element("p", "otk-prose");
  const caret = element("span", "otk-caret");
  caret.setAttribute("aria-hidden", "true");
  held.append(caret);
  const state = element("span", "otk-generating__text", "waiting");
  const elapsed = element("span", "otk-generating__text otk-generating__elapsed", "0.0s");
  const status = element("div", "otk-generating__status");
  status.setAttribute("aria-hidden", "true");
  status.append(element("span", "otk-generating__rule"), state, elapsed);
  article.append(held, status);
  const started = Date.now();
  const ticking = setInterval(() => {
    elapsed.textContent = `${((Date.now() - started) / 1000).toFixed(1)}s`;
  }, 100);
  /* A regenerate takes the standing reply off the screen before the
     request is away: the take being replaced must not sit there while
     the model thinks. The backend validates a regenerate eagerly, so a
     refusal comes back before anything is lost. */
  if (regenerate) holdSpace(dropLastReply);
  showTurnBar();
  return { article, status, state, ticking, over, tail, reasoning: null, prose: "" };
}

function draw(turn, happened) {
  // A regenerate sends no Recorded — its prompt is already on screen —
  // so the block joins the flow at the first sign of the reply, or it
  // would stream into nothing.
  if (!turn.article.isConnected) transcript.append(turn.article);
  DRAW[happened.type]?.(turn, happened);
  // what arrives goes into the space the old take was read in
  reserve();
  follow(turn);
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
    drawProse(turn.article, turn.prose, { streaming: true });
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
  // the status row STAYS, hidden, so nothing above it moves
  turn.article.classList.add("otk-generating--idle");
  turn.article.classList.remove("is-streaming");
  turn.article.setAttribute("aria-busy", "false");
  $(".otk-caret", turn.article)?.remove();
  // the paragraph that held the answer's place, when nothing came
  for (const p of $$(".otk-prose", turn.article)) if (!p.textContent) p.remove();
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
  follow(turn);
}

function follow(turn) {
  // While space is held the flow does not move — the text is going where
  // the reader is already looking. Once that space is filled the flow
  // follows it down, for whoever was at the tail to begin with.
  if (holdAt === null && turn.tail) toBottom({ force: true });
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
