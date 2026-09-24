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
   `whenEdited` names, which keeps this a drawing, not a controller.
   A question the model asks (`showAsk`) is drawn where its reply
   landed, its verbs carrying `data-ask-send` and `data-ask-copy` for
   the same listener the turn bar has. */

import * as api from "./api.js";
import { editable, edited } from "./browser.js";
import { $, $$, element, span } from "./dom.js";
import { thumbs } from "./pictures.js";
import { typesetBody } from "./prose.js";
import { complain, settle, tell, working } from "./status.js";
import { isToken } from "./table.js";

const transcript = $(".otk-transcript");
// What the status row says while a tool call streams, whichever tool.
const _CALLING = "calling a tool";
// The tools whose calls the reader is shown (`displayTools`); every
// other call draws as nothing.
let shownTools = new Set();
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

/** The story as played, whole: its turns, and the question it stands
    on — a story resumed on an unanswered one shows it again. */
export function showPlayed({ messages }, options = {}) {
  const call = questionPosed(messages.at(-1));
  showTurns(messages, { ...options, posed: Boolean(call) });
  if (call) showAsk(call);
}

/** The question a turn stands on, when it is the newest: a reply whose
    last call is a question — nothing after it but notes and whitespace;
    prose, or a call of a tool this page does not know, means the model
    went on past it. The page's own reading of the segments, the tools
    named here: the next line played answers it. */
function questionPosed(turn) {
  if (turn?.role !== "assistant") return null;
  for (const segment of [...(turn.segments ?? [])].reverse()) {
    if (segment.kind === "prose") {
      if (segment.text.trim()) return null;
    } else if (segment.tool === "question") {
      return segment;
    } else if (segment.tool !== "note") {
      return null;
    }
  }
  return null;
}

/** Which tools' calls the reader is shown, from the story's settings
    (`GET /api/stories/{story}/settings`): a tool that is on, with its
    display switch on — the notes. The terminal keeps the same rule in
    its own language (`terminal.tty.render.notes_displayed`): a
    conjunction of two facts the setting reports, so each frontend reads
    it off them. Whoever sets it draws the transcript again: what it
    draws just changed. */
export function displayTools(settings) {
  shownTools = new Set(
    settings.filter((s) => s.tool && s.enabled && s.display_notes).map((s) => s.tool),
  );
}

export function showTurns(turns, { keepPlace = false, posed = false } = {}) {
  /* `keepPlace` is for a redraw the reader did not ask to be moved by —
     a refused regenerate: the turns change, the scroll does not.
     `posed`: the newest reply's question is the live one, posed in its
     own panel, so the reply does not draw it a second time as past. */
  ordinal = turns.length;
  const drawn = turns.map((turn, i) =>
    drawTurn(turn, i + 1, { posed: posed && i === turns.length - 1 }),
  );
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

/** A question the model asked, posed under the reply it closed: the
    question, and the answers to pick from — none for a free-form one.
    Any earlier one goes; a typed line or a redraw takes it down too. */
export function showAsk({ question, options }) {
  clearAsk();
  const box = element("div", "otk-ask otk-ask--live");
  box.setAttribute("role", "group");
  box.setAttribute("aria-label", "The narrator is asking");
  box.append(span("otk-ask__label", "The narrator needs an answer"));
  if (question) box.append(element("p", "otk-ask__body", question));
  if (options.length) {
    const list = element("div", "otk-ask__options");
    options.forEach((text, i) => {
      const line = element("div", "otk-ask__option");
      const pick = element("button", "otk-ask__pick", text);
      pick.type = "button";
      pick.dataset.askSend = text;
      const copy = element("button", "otk-ask__copy", "edit →");
      copy.type = "button";
      copy.dataset.askCopy = text;
      copy.setAttribute("aria-label", "Copy to the composer");
      line.append(span("otk-ask__n", String(i + 1)), pick, copy);
      list.append(line);
    });
    box.append(list);
  }
  box.append(
    span(
      "otk-ask__foot",
      options.length
        ? "Click an option to send it — or write your own answer below."
        : "Write your answer below.",
    ),
  );
  /* Under the words of the reply that asked, inside its article — ahead
     of the status row that reply keeps, so the question sits against
     the answer's last line and not under the row's reserved height. */
  const reply = $$(".otk-reply", transcript).at(-1);
  if (!reply) transcript.append(box);
  else reply.insertBefore(box, $(".otk-generating__status", reply));
  if (following) transcript.scrollTop = transcript.scrollHeight;
}

export function clearAsk() {
  $(".otk-ask--live", transcript)?.remove();
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
    draws it: `position` counts stored turns from 1, as the dossier does.
    A reply is READ AGAIN rather than repainted from the words: how it
    splits into prose and tool calls is the backend's to say. */
export async function showCorrected(position, text) {
  const turn = storedTurns()[position - 1];
  const field = turn && $("textarea.otk-editable", turn);
  if (!field) return;
  field._settle(text);
  if (turn.matches(".otk-reply")) await redrawReply(position);
  else $(".otk-reader", turn)._repaint();
}

async function redrawReply(position) {
  /* Read whole: a corrected newest reply may have changed the question
     the story stands on, and the panel follows it. */
  const { messages } = await api.played();
  const stored = messages[position - 1];
  const drawn = storedTurns()[position - 1];
  if (!stored || !drawn) return;
  const newest = position === messages.length;
  const call = newest ? questionPosed(stored) : null;
  drawn.replaceWith(drawTurn(stored, position, { posed: Boolean(call) }));
  if (newest) {
    if (call) showAsk(call);
    else clearAsk();
  }
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

function drawTurn(turn, position, { posed = false } = {}) {
  if (turn.role === "user") {
    const article = element("article", "otk-turn");
    const rubric = element("span", "otk-turn__rubric", `◆ ${position} · you`);
    article.append(rubric, correctable("otk-turn__body", turn.body, withSlashTokens));
    // The pictures that rode the line, under it: the row's own facts,
    // the tiles asked for by address ([] on a turn without any).
    if (turn.attachments?.length) article.append(thumbs(turn.attachments));
    return article;
  }
  const article = element("article", "otk-reply");
  article.append(replyReader(turn, { posed }));
  return article;
}

function replyReader(turn, { posed = false } = {}) {
  /* The reader view is drawn from the turn's SEGMENTS — the prose, and
     each tool call as the backend read it — while the editor holds the body
     whole, tags and all: what is corrected is what was stored. A save
     reads the turn again for the same reason. */
  const parts = turn.segments ?? [];
  return correctable("otk-prose", turn.body, () => replyNodes(parts, storedCall(parts, posed)), {
    saved: (position) => redrawReply(position),
  });
}

function storedCall(segments, posed) {
  /* How a stored tool call draws, as what it is and never as its tag: a
     question the model asked, kept as a past one without its verbs,
     unless it is the one the story stands on (`posed`) — the reply's
     LAST one, wherever the model's asides left it, which the live
     panel draws; the notes, while the reader is shown them. A tool with
     no drawing leaves no trace. */
  const pending = posed ? segments.findLastIndex((segment) => segment.tool === "question") : -1;
  return (segment, i) => {
    if (segment.tool === "question") return i === pending ? null : pastAsk(segment);
    return shownTools.has(segment.tool) ? notesBlock(segment.text) : null;
  };
}

function replyNodes(segments, callNode) {
  /* The reply's nodes from its segments in order — prose typeset as
     prose, each tool call as `callNode` draws it, null drawing nothing —
     for the landed reply and the streaming one alike, so nothing moves
     when it lands. The whitespace around a tag is the tag's, not the
     prose's: a run of prose is trimmed at its edges, and two runs a
     call that draws nothing stood between rejoin as paragraphs — else
     the blank lines that wrapped the call would print as a gap where
     nothing is drawn. */
  const out = [];
  let prose = "";
  const flush = () => {
    if (prose) out.push(...typesetBody(prose));
    prose = "";
  };
  segments.forEach((segment, i) => {
    if (segment.kind === "prose") {
      const text = segment.text.trim();
      if (text) prose = prose ? `${prose}\n\n${text}` : text;
      return;
    }
    const drawn = callNode(segment, i);
    if (!drawn) return;
    flush();
    out.push(drawn);
  });
  flush();
  return out;
}

function notesBlock(text) {
  /* The model's notes, drawn as the question is drawn — the same shape,
     no label, the text itself — and never as a tag. */
  const box = element("div", "otk-ask otk-ask--notes");
  box.append(element("p", "otk-ask__body", text.trim()));
  return box;
}

function pastAsk({ question, options }) {
  const box = element("div", "otk-ask otk-ask--past");
  box.append(span("otk-ask__label", "The narrator asked"));
  if (question) box.append(element("p", "otk-ask__body", question));
  if (options?.length) {
    const list = element("div", "otk-ask__options");
    options.forEach((text, i) => {
      const line = element("div", "otk-ask__option");
      line.append(span("otk-ask__n", String(i + 1)), span("otk-ask__pick", text));
      list.append(line);
    });
    box.append(list);
  }
  return box;
}

function correctable(className, body, nodesOf, { saved = null } = {}) {
  /* A stored turn is corrected where it is read, as the dossier corrects
     one (`story.reader`): the block that reads and the field that edits
     wear one class in one box, both keeping the text's own line breaks,
     so opening it moves nothing but what the marks and faces change.
     `saved` runs after a write that landed, for a turn whose reading
     is not the words' own. */
  const read = element("div", `otk-editable ${className} otk-typeset`);
  const both = element("div", "otk-reader");
  const field = editable(className, {
    text: body,
    save: async (text) => {
      const position = storedTurns().indexOf(both.closest("article"));
      const answer = await saveEdit(position, field._stored(), text);
      if (!answer.refused && saved) await saved(position + 1);
      return answer;
    },
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
export async function play(line, { regenerate = false, files = [] } = {}) {
  // Without this the first of two plays to finish unlocks the composer
  // for both, leaving prose on screen that the store never took.
  if (playing) return;
  const turn = beginTurn(regenerate);
  let refused = null;
  try {
    const answer = await api.play(line ?? "", { regenerate, signal: arriving.signal, files });
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
    // away before anyone reads it — and above the box, where it stands
    if (refused) {
      tell(refused, "otk-error");
      complain(refused);
    }
  }
  // The caller lands the turn and would otherwise say nothing over
  // this; handed back so the refusal stands.
  return refused;
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
  settle(); // a new attempt takes the last refusal down
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
  // `pieces`: what has arrived, in order — prose runs and tool calls, the
  // segments a stored turn carries, kept as they stream so each call
  // draws in its place
  return { article, block, status, state, ticking, over, reasoning: null, pieces: [] };
}

function streamed(turn) {
  /* The reply as it stands so far, drawn by the landed reply's rule
     (`replyNodes`) from the pieces in the order they arrived. The caret
     rides the end of the prose that has arrived — the last character,
     not the blank line a model leaves before a call, and not a call
     being written under it. */
  const boxes = turn.pieces.map((piece) => piece.box).filter(Boolean);
  const nodes = replyNodes(turn.pieces, (piece) => piece.box);
  const lastProse = nodes.findLastIndex((node) => !boxes.includes(node));
  nodes.splice(lastProse + 1, 0, caret());
  turn.block.replaceChildren(...nodes);
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
    clearAsk(); // the line that landed is the answer, whatever it says
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
    turn.state.textContent = "writing";
    // prose grows the run it is in; after a call it starts a new one
    const last = turn.pieces.at(-1);
    if (last?.kind === "prose") last.text += happened.text;
    else turn.pieces.push({ kind: "prose", text: happened.text });
    /* Typeset again from the whole text rather than appended to: a quote
       or a mark closes mid-stream, and only the whole text knows what it
       closed. */
    streamed(turn);
  },
  tool_call(turn, happened) {
    /* A tool call is not story: the status row says what the model is
       doing, so the running line never reads as stalled. One the reader
       is shown — the notes, while the story says so — draws as it
       arrives, in its place among the prose, in the box the landed
       reply draws it in; a question never draws raw, the panel poses
       it when the reply lands. A call's pieces grow one piece of the
       turn until the one that closed it; the next tag opens its own. */
    turn.state.textContent = _CALLING;
    let call = turn.pieces.at(-1);
    if (call?.kind !== "tool_call" || call.tool !== happened.tool || call.closed) {
      const box = shownTools.has(happened.tool) ? notesBlock("") : null;
      call = { kind: "tool_call", tool: happened.tool, text: "", closed: false, box };
      turn.pieces.push(call);
    }
    call.text += happened.text;
    call.closed = happened.closed;
    if (call.box) $(".otk-ask__body", call.box).textContent = call.text.trim();
    streamed(turn);
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
    if (happened.stats) {
      turn.article.insertBefore(
        element("p", "otk-verbose", happened.stats),
        $(".otk-generating__status", turn.article),
      );
    }
    // The reply as stored, to land in place; and the question it closed
    // on, to pose under itself.
    turn.reply = happened.reply;
    turn.call = questionPosed(happened.reply);
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
  // the question a reply ended on, drawn once the reply stands
  if (turn.call) showAsk(turn.call);
  // the status row STAYS, hidden, so nothing above it moves — until the
  // next line takes its place (`recorded`)
  turn.article.classList.add("otk-generating--idle");
  turn.article.classList.remove("is-streaming");
  turn.article.setAttribute("aria-busy", "false");
  /* What arrived is stored exactly as it streamed, so it becomes the
     same editor a stored reply is drawn as, in the same box — drawn
     from the stored turn `done` carried, calls and all; a stream cut
     short carries none, and what streamed stands in: its prose, and
     the calls the reader was shown (the others' facts were never
     read). The block that held the answer's place goes when nothing
     came. */
  const prose = turn.pieces.filter((piece) => piece.kind === "prose").map((piece) => piece.text).join("");
  const landed =
    turn.reply ??
    (prose ? { body: prose, segments: turn.pieces.filter((piece) => piece.kind === "prose" || piece.box) } : null);
  if (landed) turn.block.replaceWith(replyReader(landed, { posed: Boolean(turn.call) }));
  else turn.block.remove();
  // a reply that never arrived leaves no empty block behind
  if (!landed && !$(".otk-error, .otk-verbose, .otk-reasoning", turn.article)) {
    turn.article.remove();
  } else if (turn.article.isConnected && landed) {
    /* The reply landed, so the story is one turn longer than the rubric
       counted. The STORED turn is the test, not the block: a declined or
       failed attempt keeps its block to say so but stores no message
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
