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

import { api } from "../api/index.js";
import { editable, edited } from "../kit/browser.js";
import { $, $$, element, span } from "../kit/dom.js";
import * as held from "./held.js";
import { thumbs } from "../kit/pictures.js";
import { typesetBody } from "../kit/prose.js";
import { complain, settle, tell, working } from "../kit/status.js";
import { isToken } from "./language.js";

/* What the backend says with nothing to take again — COPIED, as the page
   has no story to ask (`backend.domain.story.stories.NOTHING_TO_REGENERATE`). */
const _NOTHING_TO_REGENERATE = "Nothing to regenerate.";

const transcript = $(".otk-transcript");
// How long a word stands on the status row at least, so a quick one is read
// rather than flashed.
const _SAY_MS = 250;
// A reply's rubric, worded as the played turn's is: the page's own word
// for the model's voice, the one its question panel uses.
const _replyRubric = (position) => `${position} · narrator`;
// The tools whose calls the reader is shown (`displayTools`); every
// other call draws as nothing. Of those, the ones whose notes are shown
// open — the rest fold to their label, the text its hover hint.
let shownTools = new Set();
let openNotes = new Set();
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
    on (`Story.question`) — a story resumed on an unanswered one shows it
    again. */
export function showPlayed({ messages, question = null }, options = {}) {
  showTurns(messages, { ...options, posed: Boolean(question) });
  if (question) showAsk(question);
}

/** Which tools' calls the reader is shown, as the story says: those it
    shows (`Story.shown_tools`) open, those it folds (`Story.folded_tools`)
    by their label alone, the text the hover hint — every other call is
    drawn as nothing. None for a story not made yet. Whoever sets it draws
    the transcript again: what it draws just changed. */
export function displayTools(story) {
  shownTools = new Set([...(story?.shown_tools ?? []), ...(story?.folded_tools ?? [])]);
  openNotes = new Set(story?.shown_tools ?? []);
}

/** The question the story held stands on (`Story.question`), as the
    backend reads it once a reply stands — null for none, or for a story
    that cannot be read. */
async function standingQuestion() {
  const story = held.story();
  if (story === null) return null;
  const facts = await api.stories.get(story);
  return facts.refused ? null : facts.question;
}

export function showTurns(turns, { keepPlace = false, posed = false } = {}) {
  shelved = null;
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
    showTail();
    return;
  }
  release();
  transcript.replaceChildren(...drawn);
  showTurnBar();
  showTail();
  toBottom();
}

/** An undo, drawn: the turns past what the store still holds come off
    where they stand, as the terminal erases the exchange in place, and
    the turns left keep what only a live reply draws beside them — the
    stats line, a roll's dice. A page that is not the store's turns with
    the end cut off is drawn again instead. */
export function takeBack(turns, question = null) {
  shelved = null;
  // the question the story stands on again once its answer is taken back
  // (`Story.question`, the undo's answer): posed anew, as the terminal
  // poses it (`dispatch.story.stories._pose_again`)
  const call = question;
  const drawn = storedTurns();
  const same = turns.every(
    (turn, i) => drawn[i] && (turn.role === "user") === drawn[i].classList.contains("otk-turn"),
  );
  if (!same) {
    showTurns(turns, { keepPlace: true, posed: Boolean(call) });
    if (call) showAsk(call);
    return;
  }
  const kept = drawn[turns.length - 1];
  const gone = [];
  let node = kept ? kept.nextElementSibling : transcript.firstElementChild;
  for (; node; node = node.nextElementSibling) {
    if (!node.classList.contains("otk-gap")) gone.push(node);
  }
  ordinal = turns.length;
  holdSpace(() => {
    gone.forEach((each) => each.remove());
    if (call) {
      // drawn again without the past question the answer left in it
      kept.replaceWith(drawTurn(turns.at(-1), turns.length, { posed: true }));
      showAsk(call);
    }
  });
  showTurnBar();
  showTail();
}

/** A question the model asked, posed under the reply it closed: the
    question, and the answers to pick from — none for a free-form one.
    Any earlier one goes; a typed line or a redraw takes it down too. */
export function showAsk(call) {
  shelved = null;
  clearAsk();
  // A reader at the end before the panel goes in is carried down to it:
  // `following` alone would miss a fresh draw, whose own scroll to the
  // end has not reported yet when the panel lands under it.
  const carry = following || atTail();
  const box = askBox(call, { armed: true });
  /* Under the words of the reply that asked, inside its article — ahead
     of the status row that reply keeps, so the question sits against
     the answer's last line and not under the row's reserved height. */
  const reply = $$(".otk-reply", transcript).at(-1);
  if (!reply) transcript.append(box);
  else reply.insertBefore(box, $(".otk-generating__status", reply));
  // Space held is in view: a panel that fits in it moves nothing, one
  // that outgrows it takes the reader on to its end.
  reserve();
  if (carry && !holding) toBottom();
}

function askBox({ question, options }, { armed }) {
  /* The live panel's box, the same whether the question is still
     streaming or has landed — so the one turns into the other without a
     move. `armed` is whether the options may be sent: not while the
     reply is still arriving. */
  const box = element("div", "otk-ask otk-ask--live");
  box._call = { question, options }; // what `retireAsk` keeps of it
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
      pick.disabled = !armed;
      pick.dataset.askSend = text;
      const copy = element("button", "otk-ask__copy", "edit →");
      copy.type = "button";
      copy.disabled = !armed;
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
  return box;
}

// An answer to pick from, as the tool reads one: `1.` `1)` `a.` `a)` at the
// head of a line, then the answer. The RULE is `ToolQuestion._OPTION`'s
// (`backend.domain.story._tools`), copied for the page to read a question as
// it streams — the language barrier being the one reason a rule exists twice.
const _OPTION = /^\s*(?:\d{1,2}|[A-Za-z])[.)]\s+(\S.*)$/;
// A line still arriving that may yet turn out an option: its head not
// complete, or complete and followed by a space — held until its end tells.
const _MAY_BE_OPTION = /^\s*(?:(?:\d{1,2}|[A-Za-z])?[.)]?|(?:\d{1,2}|[A-Za-z])[.)]\s.*)$/;
const _MAX_OPTIONS = 9;

function readAsk(text, closed) {
  /* A question call's inside as the tool reads it, from what has arrived:
     the question is the lines before the first option, the options the
     numbered lines after — whatever follows them that is not one is
     dropped. The last line is unfinished until the call closes: held
     while it may still become an option, shown as the question's while
     it cannot. */
  const lines = text.split("\n");
  const unfinished = closed ? null : lines.pop();
  const question = [];
  const options = [];
  for (const line of lines) {
    const found = _OPTION.exec(line);
    if (found) {
      if (options.length < _MAX_OPTIONS) options.push(found[1].trimEnd());
    } else if (!options.length) {
      question.push(line);
    }
  }
  if (unfinished !== null && !options.length && !_MAY_BE_OPTION.test(unfinished)) {
    question.push(unfinished);
  }
  return { question: question.join("\n").trim(), options };
}

// ---------- the canvas typed in: its own end, editable ----------

/* The canvas's own end, editable: the reader's next line typed where it
   lands. It goes on in the paragraph by default — the one space the join
   draws is DRAWN here too (`data-seam`, `app.css`), so no caret can stand
   before it; nothing of it is sent, the join is the backend's.
   Enter (or the Send button) sends — on a touch screen the Send button
   alone, Enter there being the keyboard's only way to a new paragraph.
   Ctrl/⌘+Enter opens a PARAGRAPH: a block of its own (`.otk-tail__p`),
   spaced as the story's paragraphs are, with no empty line between to
   step onto; on send each is the blank line the story stores. A reply
   under way takes it down; the next landing puts it back. */
let tail = null;
// the join's one space, drawn just before the tail and outside it, so the
// caret always stands after it — where the typed text will
let seam = null;
// when Ctrl/⌘+Enter last opened a paragraph in the tail, so a menu the
// same key asked for does not open another
let openedAt = -Infinity;
// how a line typed now would join the newest row, as the backend says
// (`facts.next_join`): "inline", "paragraph", null in a story not begun
let nextJoin = null;
// the question an option was taken from to be edited in the tail
// (`draftTail`): posed again once the tail is empty, as the terminal's
// empty line brings its menu back; forgotten when the story moves on
let shelved = null;

export function tailText() {
  if (!tail?.isConnected) return null;
  return tailParagraphs().join("\n\n");
}

/** The tail's paragraphs as typed: the part that goes on from the text,
    then one per paragraph opened. */
function tailParagraphs() {
  const out = [""];
  for (const node of tail.childNodes) {
    if (isParagraph(node)) out.push(node.textContent);
    else out[out.length - 1] += node.textContent;
  }
  // the sentinel a line break at a paragraph's end stands on, and that
  // break itself, which ends nothing
  return out.map((text) => text.replaceAll("\u200b", "").replace(/\n+$/, ""));
}

export function showTail() {
  if (transcript.dataset.transcript !== "canvas" || playing) {
    tail?.remove();
    seam?.remove();
    return;
  }
  if (!tail) {
    seam = element("span", "otk-tail-seam", " ");
    seam.setAttribute("aria-hidden", "true");
    tail = element("span", "otk-tail");
    tail.contentEditable = "plaintext-only";
    tail.addEventListener("keydown", (event) => {
      if (event.isComposing) return;
      // Esc clears what was typed, as it clears the composer
      if (event.key === "Escape") {
        event.preventDefault();
        tail.replaceChildren();
        markBlank();
        focusTail();
        poseShelved();
        return;
      }
      if (event.key === "Backspace" && joinParagraph()) {
        event.preventDefault();
        return;
      }
      // the turn's two verbs, as the composer answers them
      const verb = { r: "regen", u: "undo" }[event.key];
      if (verb && event.ctrlKey && !event.metaKey && !event.altKey) {
        event.preventDefault();
        $(`[data-turn="${verb}"]`)?.click();
        return;
      }
      if ((event.key === "ArrowUp" || event.key === "ArrowDown") && !event.shiftKey && atEdge(event.key)) {
        event.preventDefault();
        return;
      }
      if (event.key !== "Enter") return;
      event.preventDefault();
      if (event.shiftKey) breakLine();
      else if (event.ctrlKey || event.metaKey) {
        openedAt = event.timeStamp;
        openParagraph();
      } else if (window.matchMedia("(pointer: coarse)").matches) openParagraph();
      else $(".otk-composer [data-send]")?.click();
    });
    /* Firefox on a Mac takes Control+Return for the context menu before
       the Enter reaches the page: a menu asked for with Control held and
       no pointer pressed for it is that key, and opens a paragraph — once,
       should the Enter have come through too. A click's menu stands.
       Control is tracked here, as the menu's own event may not carry it. */
    let control = false;
    let pressedAt = -Infinity;
    tail.addEventListener("keydown", (event) => {
      if (event.key === "Control") control = true;
    });
    tail.addEventListener("keyup", (event) => {
      if (event.key === "Control") control = false;
    });
    tail.addEventListener("blur", () => {
      control = false;
    });
    tail.addEventListener("pointerdown", (event) => {
      pressedAt = event.timeStamp;
    });
    tail.addEventListener("contextmenu", (event) => {
      if (!(event.ctrlKey || control) || event.timeStamp - pressedAt < 500) return;
      event.preventDefault();
      if (event.timeStamp - openedAt > 500) openParagraph();
    });
    tail.addEventListener("input", () => {
      // a break the browser leaves in the text that goes on from the
      // story would draw a line of its own: paragraphs are blocks here
      for (const br of tail.querySelectorAll(":scope > br")) br.remove();
      markBlank();
      revealTail();
      poseShelved();
    });
    // entered blank, the caret goes where the text goes on: after the seam
    tail.addEventListener("focus", () => {
      if (tail.dataset.blank !== undefined) requestAnimationFrame(focusTail);
    });
    // the page under the text is the tail's too: a click there types on
    transcript.addEventListener("click", (event) => {
      if (event.target === transcript || event.target.matches(".otk-gap, .otk-tail-seam")) focusTail();
    });
  }
  // a blank tail shows the caret a reply streams behind until something is
  // typed (`app.css`)
  tail.replaceChildren();
  placeTail();
  transcript.insertBefore(seam, $(".otk-gap", transcript));
  transcript.insertBefore(tail, $(".otk-gap", transcript));
  // the caret back where the story is written, when nothing else holds it
  // — the composer's own controls included: a regenerate clicked on the
  // canvas leaves the focus on its button, the tail being down meanwhile
  const active = document.activeElement;
  const idle = active === document.body || Boolean(active?.closest(".otk-composer"));
  if (idle && !window.matchMedia("(pointer: coarse)").matches) focusTail();
}

const isParagraph = (node) => node?.nodeType === Node.ELEMENT_NODE && node.matches(".otk-tail__p");

/** The canvas's own end as a place a line is typed, for the prefix menu
    (`composer.js`), as the box is one: the text before the caret in its
    paragraph, whether what is typed is one line yet, the whole of it, the
    `count` characters before the caret replaced — the slash word, a name
    begun — and where the caret stands, the menu's anchor. Null while the
    canvas is not typed in. */
export function tailHost() {
  if (!tail?.isConnected) return null;
  const point = () => {
    const selection = getSelection();
    return selection.rangeCount && tail.contains(selection.anchorNode) ? selection.getRangeAt(0) : null;
  };
  return {
    before() {
      const at = point();
      if (!at) return "";
      const range = document.createRange();
      range.setStart(paragraphAt(at.startContainer, at.startOffset).own, 0);
      range.setEnd(at.startContainer, at.startOffset);
      return range.toString().replaceAll("\u200b", "");
    },
    oneLine: () => !(tailText() ?? "").includes("\n"),
    text: () => tailText() ?? "",
    replace(count, text) {
      const at = point();
      if (!at) return;
      const range = at.cloneRange();
      range.collapse(true);
      if (count && range.startContainer.nodeType === Node.TEXT_NODE && range.startOffset >= count) {
        range.setStart(range.startContainer, range.startOffset - count);
      }
      range.deleteContents();
      const written = document.createTextNode(text);
      range.insertNode(written);
      const caret = document.createRange();
      caret.setStart(written, text.length);
      const selection = getSelection();
      selection.removeAllRanges();
      selection.addRange(caret);
      markBlank();
      revealTail();
    },
    anchor() {
      const at = point();
      const rect = at?.getClientRects()[0];
      return rect?.height ? rect : tail.getBoundingClientRect();
    },
  };
}

/** The paragraph a point stands in — a block opened by its key, or the tail
    itself for the part that goes on from the story, which runs up to the
    first block — and the range from the point to that paragraph's end. */
function paragraphAt(container, offset) {
  let own = container;
  while (own !== tail && !isParagraph(own)) own = own.parentNode;
  const rest = document.createRange();
  rest.setStart(container, offset);
  if (own === tail) {
    const first = [...tail.childNodes].findIndex(isParagraph);
    rest.setEnd(tail, first < 0 ? tail.childNodes.length : first);
  } else {
    rest.setEnd(own, own.childNodes.length);
  }
  return { own, rest };
}

/** Ctrl/⌘+Enter (Enter on a touch screen): the paragraph the caret is in
    splits there, what follows the caret opening a new block, the caret at
    its start. */
function openParagraph() {
  const selection = getSelection();
  if (!selection.rangeCount) return;
  const range = selection.getRangeAt(0);
  range.deleteContents();
  const { own, rest } = paragraphAt(range.startContainer, range.startOffset);
  const paragraph = element("div", "otk-tail__p");
  paragraph.append(rest.extractContents());
  // a stray break the browser left in an emptied block measures nothing
  for (const br of paragraph.querySelectorAll("br")) br.remove();
  if (own === tail) tail.insertBefore(paragraph, [...tail.childNodes].find(isParagraph) ?? null);
  else own.after(paragraph);
  const caret = document.createRange();
  caret.setStart(paragraph, 0);
  caret.collapse(true);
  selection.removeAllRanges();
  selection.addRange(caret);
  markBlank();
  revealTail();
}

/** Backspace at the very start of a paragraph: it joins the one before,
    the caret where they meet — the same in every engine. False where the
    caret stands anywhere else, for the key to do what it does. */
function joinParagraph() {
  const selection = getSelection();
  if (!selection.rangeCount || !selection.isCollapsed) return false;
  const caret = selection.getRangeAt(0);
  const block = (caret.startContainer.nodeType === Node.ELEMENT_NODE
    ? caret.startContainer
    : caret.startContainer.parentElement
  )?.closest(".otk-tail__p");
  if (!block || !tail.contains(block)) return false;
  const before = document.createRange();
  before.setStart(block, 0);
  before.setEnd(caret.startContainer, caret.startOffset);
  if (before.toString() !== "") return false;
  const previous = isParagraph(block.previousElementSibling) ? block.previousElementSibling : null;
  const meet = document.createRange();
  if (previous) {
    meet.setStart(previous, previous.childNodes.length);
    previous.append(...block.childNodes);
  } else {
    // into the text that goes on from the story, which runs up to here
    meet.setStartBefore(block);
    block.before(...block.childNodes);
  }
  block.remove();
  for (const br of tail.querySelectorAll("br")) br.remove();
  meet.collapse(true);
  selection.removeAllRanges();
  selection.addRange(meet);
  markBlank();
  revealTail();
  return true;
}

/** An option taken to be edited, on the canvas: the tail holds it, the
    caret at its end, and the question goes — as the terminal's → takes
    an answer into the line and dismisses the menu. False off the canvas,
    where the box takes it (`composer.draft`). */
export function draftTail(line) {
  if (!tail?.isConnected) return false;
  shelved = $(".otk-ask--live", transcript)?._call ?? null;
  holdSpace(clearAsk);
  tail.replaceChildren(line);
  markBlank();
  focusTail();
  revealTail();
  return true;
}

function poseShelved() {
  if (!shelved || !tail.hasAttribute("data-blank")) return;
  const call = shelved;
  shelved = null;
  showAsk(call);
}

/** How a line typed now would join the story, as the backend says
    (`facts.next_join`, read after every write): the tail placed again. */
export function joinNext(join) {
  nextJoin = join ?? null;
  if (tail?.isConnected) placeTail();
}

function placeTail() {
  // the join is the backend's: the one space drawn before the tail, or a
  // paragraph it opens — neither in a story not begun
  tail.toggleAttribute("data-seam", nextJoin === "inline");
  if (nextJoin === "paragraph") tail.dataset.join = "paragraph";
  else delete tail.dataset.join;
  markBlank();
}

function markBlank() {
  const text = tailParagraphs().join("\n\n");
  // the drawn caret stands until something is typed — the browser's is not typing
  tail.toggleAttribute("data-blank", !tail.textContent && !tail.querySelector(".otk-tail__p"));
  // a line that opens with whitespace — or a paragraph — needs no drawn seam
  if (seam) seam.hidden = !tail.hasAttribute("data-seam") || /^\s/.test(text);
}

/** Shift+Enter: a line break inside the paragraph — a single newline. One
    that ends its paragraph stands on a sentinel (a zero-width space, never
    sent), or the line it opens would not be drawn and the caret could not
    stand on it. */
function breakLine() {
  const selection = getSelection();
  if (!selection.rangeCount) return;
  const range = selection.getRangeAt(0);
  range.deleteContents();
  const lineBreak = document.createTextNode("\n");
  range.insertNode(lineBreak);
  const point = document.createRange();
  point.setStartAfter(lineBreak);
  const { rest } = paragraphAt(point.startContainer, point.startOffset);
  const caret = document.createRange();
  if (rest.toString().replaceAll("\u200b", "") === "") {
    const sentinel = document.createTextNode("\u200b");
    lineBreak.after(sentinel);
    caret.setStart(sentinel, 0);
  } else {
    caret.setStartAfter(lineBreak);
  }
  caret.collapse(true);
  selection.removeAllRanges();
  selection.addRange(caret);
  markBlank();
  revealTail();
}

/** ↑ on the tail's first line takes the caret to its start, ↓ on its
    last line to its end, as a text box does: the first line is shared
    with the story's own text, which the caret must not wander into, nor
    snap back from to wherever the browser finds editable. True where it
    moved the caret; anywhere else the key is the browser's. */
function atEdge(key) {
  const selection = getSelection();
  if (!selection.rangeCount) return false;
  const caret = caretBox(selection.getRangeAt(0));
  const boxes = [...tail.getClientRects(), ...[...tail.querySelectorAll(".otk-tail__p")].map((p) => p.getBoundingClientRect())];
  if (!caret || !boxes.length) return false;
  const top = Math.min(...boxes.map((b) => b.top));
  const bottom = Math.max(...boxes.map((b) => b.bottom));
  const range = document.createRange();
  if (key === "ArrowUp" && caret.top - top < 4) {
    range.setStart(tail, 0);
  } else if (key === "ArrowDown" && bottom - caret.bottom < 4) {
    range.selectNodeContents(isParagraph(tail.lastElementChild) ? tail.lastElementChild : tail);
    range.collapse(false);
  } else {
    return false;
  }
  range.collapse(key === "ArrowUp");
  selection.removeAllRanges();
  selection.addRange(range);
  return true;
}

/** The box of the line the caret stands on: the collapsed range's own
    where it has one, else the character after it, else its container. */
function caretBox(range) {
  let rects = range.getClientRects();
  if (rects.length) return rects[rects.length - 1];
  const probe = range.cloneRange();
  try {
    probe.setEnd(range.endContainer, range.endOffset + 1);
    rects = probe.getClientRects();
    if (rects.length) return rects[0];
  } catch {
    // no character after: the container's box below
  }
  const holder =
    range.startContainer.nodeType === Node.ELEMENT_NODE ? range.startContainer : range.startContainer.parentElement;
  return holder?.getBoundingClientRect() ?? null;
}

/** The caret kept in view as the tail changes — a paragraph opened, a
    line broken, a line wrapping — none of which the browser scrolls to
    when the change is made in code; and, writing at the end, the page
    under it too, down to the flow's own padding, so the text never sits
    against the bottom edge. A space held open is filled first. */
function revealTail() {
  const selection = getSelection();
  if (!selection.rangeCount || !tail.contains(selection.anchorNode)) return;
  reserve();
  const caret = selection.getRangeAt(0);
  let bottom = caretBox(caret)?.bottom ?? 0;
  const rest = document.createRange();
  rest.setStart(caret.startContainer, caret.startOffset);
  rest.setEnd(tail, tail.childNodes.length);
  if (rest.toString().replaceAll("\u200b", "") === "") {
    const pad = parseFloat(getComputedStyle(transcript).paddingBottom);
    bottom = Math.max(bottom, ...[...tail.getClientRects()].map((r) => r.bottom)) + pad;
  }
  const over = bottom - transcript.getBoundingClientRect().bottom;
  if (over > 0) {
    transcript.scrollTop += over;
    lastTop = transcript.scrollTop; // the move is the page's, not the reader's
  }
}

export function focusTail() {
  if (!tail?.isConnected) return false;
  // WebKit scrolls a focused element into view its own way — far up the
  // page, for an inline one at the text's end; the caret is brought into
  // view as typing needs it (`revealTail`), and nothing moves for a focus
  tail.focus({ preventScroll: true });
  // the caret at the end of the last paragraph typed
  const range = document.createRange();
  range.selectNodeContents(isParagraph(tail.lastElementChild) ? tail.lastElementChild : tail);
  range.collapse(false);
  const selection = getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
  return true;
}

export function clearAsk() {
  $(".otk-ask--live", transcript)?.remove();
}

function retireAsk() {
  /* The question was answered — the line that landed is the answer,
     whatever it says: the options go, the question stays, drawn as a
     past one where the panel stood. The next full draw from the store
     puts it in the same place among the reply's own nodes. */
  const live = $(".otk-ask--live", transcript);
  if (live) live.replaceWith(pastAsk(live._call));
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
  field._settle(text.trim()); // the field holds no edges (`correctable`)
  await redrawTurn(position);
}

async function redrawTurn(position) {
  /* Read whole: a turn is drawn from what the store splits it into (a
     reply's calls, a line's words with the model), and a corrected newest
     reply may have changed the question the story stands on, and the
     panel follows it. */
  const story = held.story();
  if (story === null) return;
  const [messages, facts] = await Promise.all([
    api.stories.messages.list(story),
    api.stories.get(story),
  ]);
  const stored = messages[position - 1];
  const drawn = storedTurns()[position - 1];
  if (!stored || !drawn) return;
  const newest = position === messages.length;
  const call = newest && !facts.refused ? facts.question : null;
  drawn.replaceWith(drawTurn(stored, position, { posed: Boolean(call) }));
  if (newest) {
    if (call) showAsk(call);
    else clearAsk();
  }
}

/** The drawn turns the store holds, in its order: a failed attempt keeps
    its block to say so but stores nothing (`endTurn` marks those). */
function storedTurns() {
  // a tool's turn is a stored row like any other: counted, or every turn
  // after one would be addressed one place off the store's
  return $$(".otk-turn, .otk-reply, .otk-tool", transcript).filter((a) => !a.dataset.unstored);
}

/** Send while the box is the reader's, Stop while the model has it. */
function turnOver(streaming) {
  /* Send gives way to Stop, and the root says a reply is arriving —
     what the stylesheet holds still meanwhile (the layout glyphs) reads
     it there. */
  if (send) send.hidden = streaming;
  if (stop) stop.hidden = !streaming;
  $(".otk-app")?.toggleAttribute("data-playing", streaming);
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
  lastTop = top; // the move is the page's, not the reader's
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
  // At the top any position is legal: nothing to keep, and a block filling
  // the view only brings a scrollbar a story that fits never needed.
  const needed = top > 0 ? Math.max(0, top + transcript.clientHeight - story) : 0;
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

/** Run `change` — a reflow of the whole flow, as a layout switched is —
    with the reader's place kept: the turn under the top of the view stays
    where it was, and a reader at the end stays at the end. Anchoring is
    off on the flow (`overflow-anchor: none`, for `holdSpace`), so the
    browser would otherwise keep the NUMBER and move the text under it. */
export function keepingPlace(change) {
  const tail = atTail();
  const anchor = tail ? null : topmost();
  const view = () => transcript.getBoundingClientRect().top;
  const before = anchor ? anchor.getBoundingClientRect().top - view() : 0;
  change();
  if (tail) toBottom();
  else if (anchor) {
    transcript.scrollTop += anchor.getBoundingClientRect().top - view() - before;
    lastTop = transcript.scrollTop; // the move is the page's, not the reader's
  }
  reserve(); // a space held open is measured against the new heights
}

/** The turn under the top edge of the view: the first whose bottom is
    below it. */
function topmost() {
  const top = transcript.getBoundingClientRect().top;
  return $$(".otk-turn, .otk-reply", transcript).find((turn) => turn.getBoundingClientRect().bottom > top) ?? null;
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

/** Whether the open story is read on the canvas, off the app root
    (`shell.showFacts`): where a reply arriving now joins the text. */
const canvas = () => $(".otk-app")?.dataset.layout === "canvas";

function drawTurn(turn, position, { posed = false } = {}) {
  // How the turn joins the one before it — the backend's join rule
  // (`join_type`), drawn by the canvas: inline after one space, or a
  // paragraph.
  const joined = (article) => {
    article.dataset.join = turn.join_type ?? "paragraph";
    return article;
  };
  if (turn.role === "user") {
    const article = joined(element("article", "otk-turn"));
    const rubric = element("span", "otk-turn__rubric", `${position} · you`);
    // The whitespace at a line's edges is the joins' to draw: an inline
    // line gets the join's one space before it, drawn as a reply's is
    // (`.otk-seam`), and a paragraph's blank line is the article's own.
    // The stored line is drawn from its segments; a text not stored yet —
    // an edit settling — plain, until the store's redraw (`redrawTurn`).
    const drawn = (line) => {
      const body = line === turn.body.trim() ? lineNodes(turn.segments) : withSlashTokens(line.trim());
      return turn.join_type === "inline" ? [joinSpace(), ...body] : body;
    };
    article.append(
      rubric,
      correctable("otk-turn__body", turn.body, drawn, { saved: (position) => redrawTurn(position) }),
    );
    // The pictures that rode the line, under it: the row's own facts,
    // the tiles asked for by address ([] on a turn without any).
    if (turn.attachments?.length) article.append(thumbs(turn.attachments));
    return article;
  }
  if (turn.role === "tool") {
    /* A tool turn — a tool's result: nobody's line, no reply — drawn
       as the notes block is, but the tool's label ALONE: the result is
       the model's to read, not the story's, so its text is the block's
       hover hint and never in the flow. Never opened for editing. */
    const article = joined(element("article", "otk-tool"));
    const label = turn.tool_label || "Tool";
    const box = element("div", "otk-ask otk-ask--notes");
    box.title = turn.body;
    box.append(span("otk-ask__label", label));
    article.append(
      element("span", "otk-turn__rubric otk-reply__rubric", `${position} · tool`),
      box,
    );
    return article;
  }
  const article = joined(element("article", "otk-reply"));
  article.append(replyRubric(position), replyReader(turn, { posed }));
  return article;
}

/** Who wrote a reply, above its words — the played turn's rubric in its
    shape, drawn on every reply, landed or arriving; whether it shows is
    the layout's (`app.css`, the chat). */
function replyRubric(position) {
  return element("span", "otk-turn__rubric otk-reply__rubric", _replyRubric(position));
}

function replyReader(turn, { posed = false } = {}) {
  /* The reader view is drawn from the turn's SEGMENTS — the prose, and
     each tool call as the backend read it — while the editor holds the body
     whole, fences and all: what is corrected is what was stored. A save
     reads the turn again for the same reason. */
  const parts = turn.segments ?? [];
  // A row that joins the text before it inline gets the join's one space
  // before it (`turn.join_type`).
  const inline = turn.join_type === "inline";
  const box = correctable(
    "otk-prose",
    turn.body,
    () => replyNodes(parts, storedCall(parts, posed), { inline }),
    { saved: (position) => redrawTurn(position) },
  );
  // its text as the canvas draws it: the prose, each call left out and the
  // paragraph break it stood for drawn (`replyNodes`, `blockGap`)
  box._shown = () =>
    parts
      .filter((part) => part.kind === "prose")
      .map((part) => part.text.trim())
      .filter(Boolean)
      .join("\n\n");
  return box;
}

function storedCall(segments, posed) {
  /* How a stored tool call draws, as what it is and never as its tag: a
     question the model asked, kept as a past one without its verbs,
     unless it is the one the story stands on (`posed`) — the reply's
     LAST one, wherever the model's asides left it, which the live
     panel draws; each, questions and notes, while the reader is shown
     its tool (`displayTools`). A tool with no drawing leaves no trace. */
  const pending = posed ? segments.findLastIndex((segment) => segment.tool === "question") : -1;
  return (segment, i) => {
    if (segment.tool === "question") {
      return !shownTools.has("question") || i === pending ? null : pastAsk(segment);
    }
    // a call otaku runs — the search's query — is the model's, not the
    // story's: nothing drawn; its result follows as a turn of its own
    if (segment.actor === "system") return null;
    if (!shownTools.has(segment.tool)) return null;
    return notesBlock(segment.text, { open: openNotes.has(segment.tool) });
  };
}

function replyNodes(segments, callNode, { inline = false } = {}) {
  /* The reply's nodes from its segments in order — prose typeset as
     prose, each tool call as `callNode` draws it, null drawing nothing —
     for the landed reply and the streaming one alike, so nothing moves
     when it lands. The whitespace around a tag is the tag's, not the
     prose's: a run of prose is trimmed at its edges, and two runs a
     call that draws nothing stood between rejoin as paragraphs — else
     the blank lines that wrapped the call would print as a gap where
     nothing is drawn. `inline`: the reply joins the text before it
     inline (`turn.join_type`), so a reply opening with prose gets the
     join's one space first, as its own span, which the canvas shows and
     every other layout hides (`.otk-seam`), each turn being a block of
     its own there. Two runs a DRAWN block stands between get the
     paragraph break it stands for as well (`blockGap`), for the canvas,
     which hides the block. */
  const out = [];
  let prose = "";
  let sawProse = false; // whether a run of prose is drawn yet
  let between = false; // whether a block stands since the last run
  const flush = () => {
    if (!prose) return;
    if (between) out.push(blockGap());
    out.push(...typesetBody(prose));
    prose = "";
    sawProse = true;
    between = false;
  };
  segments.forEach((segment, i) => {
    if (segment.kind === "prose") {
      if (inline && !prose && !out.length && segment.text.trim()) out.push(joinSpace());
      const text = segment.text.trim();
      if (text) prose = prose ? `${prose}\n\n${text}` : text;
      return;
    }
    const drawn = callNode(segment, i);
    if (!drawn) return;
    flush();
    out.push(drawn);
    between = sawProse;
  });
  flush();
  /* A block among the text stands the one gap from whatever is on either
     side of it, and flush with the edge of the reply where nothing is:
     said here, on the block, because the prose around it is text nodes,
     which no `:first-child` or `:last-child` can see past — and said the
     same way whether the reply is streaming or landed, so a block never
     moves once drawn. */
  out.forEach((node, i) => {
    if (!(node instanceof Element)) return;
    node.classList.toggle("otk-ask--first", i === 0);
    node.classList.toggle("otk-ask--last", i === out.length - 1);
  });
  return out;
}

function notesBlock(text, { open }) {
  /* The model's notes, drawn as the question is drawn — the same shape,
     a label over the text itself — and never as a tag; folded (`open`
     false: their display switch off), the label alone with the text its
     hover hint, as a tool turn is drawn. */
  const box = element("div", "otk-ask otk-ask--notes");
  box.append(span("otk-ask__label", "Notes"));
  if (open) box.append(element("p", "otk-ask__body", text.trim()));
  else box.title = text.trim();
  return box;
}

function fillNotes(box, text) {
  /* A streaming note's text, where its block keeps it: the body of an
     open one, the hint of a folded one. */
  const body = $(".otk-ask__body", box);
  if (body) body.textContent = text.trim();
  else box.title = text.trim();
}

function pastAsk({ question, options }) {
  /* A question the story has moved past: the question alone, as the
     terminal shows one — its options were the moment's, and the line
     that followed answered it — and the options its hover hint. */
  const box = element("div", "otk-ask otk-ask--past");
  if (options.length) {
    box.title = `Options:\n\n${options.map((option, i) => `${i + 1}. ${option}`).join("\n")}`;
  }
  box.append(span("otk-ask__label", "The narrator asked"));
  if (question) box.append(element("p", "otk-ask__body", question));
  return box;
}

function correctable(className, body, nodesOf, { saved = null } = {}) {
  /* A stored turn is corrected where it is read, as the dossier corrects
     one (`story.reader`): the block that reads and the field that edits
     wear one class in one box, both keeping the text's own line breaks,
     so opening it moves nothing but what the marks and faces change.
     `saved` runs after a write that landed, for a turn whose reading
     is not the words' own. The field holds the text without the
     whitespace at its edges: a break there is the row's join, which the
     canvas draws as the paragraph gap and the other layouts leave out,
     so in the field it would read as blank lines. Kept beside it
     (`bodyOf`), and the save keeps it (`api.stories.messages.update`). */
  const read = element("div", `otk-editable ${className} otk-typeset`);
  const both = element("div", "otk-reader");
  const core = body.trim();
  const lead = body.slice(0, body.indexOf(core));
  const field = editable(className, {
    text: core,
    save: async (text) => {
      const position = storedTurns().indexOf(both.closest("article"));
      const answer = await saveEdit(position, bodyOf(field), text);
      if (!answer.refused && saved) await saved(position + 1);
      return answer;
    },
  });
  field._edges = [lead, body.slice(lead.length + core.length)];
  both._repaint = () => read.replaceChildren(...nodesOf(field._typed()));
  both.append(read, field);
  /* In the canvas a paragraph of several rows opens as ONE field holding
     all their text, at the paragraph's place and in its metrics, so the
     paragraph keeps its size: the field's own text is transparent and
     the read stays behind it as a MIRROR — the edited row's text in ink,
     the other rows' dimmed — and typing is held to the edited row's
     span, so the save writes that row and nothing else. */
  const box = edited(both, "", "otk-edit--turn", {
    opening: () => frame(both, field, read),
    opened: (height) => holdHeight(both, field, read, height),
    closing: () => {
      unframe(both, field);
      releaseHeight(field, read);
    },
  });
  // the text as drawn, for a paragraph edited as one field (`frame`); a
  // reply's leaves its calls out (`replyReader`)
  box._shown = () => field._stored().trim();
  field.addEventListener("input", () => {
    if (field._frame) mirror(read, field);
    // typing may make the text outgrow the held height, and bring a bar
    if (field.style.height) hangScrollbar(field);
  });
  // a mirror behind the field scrolls with it (`holdHeight`)
  field.addEventListener("scroll", () => {
    if (field._frame) read.scrollTop = field.scrollTop;
  });
  field.addEventListener("beforeinput", (event) => holdToSpan(field, event));
  return box;
}

/** A turn's whole text as the store holds it: the field's words between
    the edges it keeps aside (`correctable`) — the words trimmed, as the
    store trims a correction; what a save proves the page still draws. */
function bodyOf(field) {
  const [lead, trail] = field._edges ?? ["", ""];
  return lead + field._stored().trim() + trail;
}

/** The rows drawn as one paragraph with `article`: from the row that
    opened it (a paragraph join) through every row joined inline after. */
function paragraphOf(article) {
  const rows = storedTurns();
  const at = rows.indexOf(article);
  if (at < 0) return [article];
  let start = at;
  while (start > 0 && rows[start].dataset.join === "inline") start -= 1;
  let end = at;
  while (end + 1 < rows.length && rows[end + 1].dataset.join === "inline") end += 1;
  return rows.slice(start, end + 1);
}

function frame(both, field, read) {
  if (transcript.dataset.transcript !== "canvas") return;
  const article = both.closest("article");
  const group = paragraphOf(article);
  // the rows' text as the canvas draws it: one space at each join, drawn
  // and never stored (`joinSpace`) — every row after the first joins
  // inline — and nothing of a tool's turn, which it hides
  const shown = group.filter((row) => !row.matches(".otk-tool"));
  if (shown.length < 2) return;
  // as drawn: a row not edited shows no call's fences, nobody types in it
  const textOf = (row) => $(".otk-edit", row)._shown();
  const at = shown.indexOf(article);
  field._frame = {
    prefix: shown.slice(0, at).map((row) => `${textOf(row)} `).join(""),
    suffix: shown.slice(at + 1).map((row) => ` ${textOf(row)}`).join(""),
  };
  field.value = field._frame.prefix + field._stored() + field._frame.suffix;
  // the other rows step out of the flow while the one field stands for
  // them all; the first keeps the blank line before the paragraph
  for (const other of group) if (other !== article) other.dataset.framed = "true";
  both.closest(".otk-edit").dataset.framing = "true";
  mirror(read, field);
}

function unframe(both, field) {
  if (!field._frame) return;
  field._frame = null;
  for (const other of $$("[data-framed]", transcript)) delete other.dataset.framed;
  delete both.closest(".otk-edit").dataset.framing;
  field.value = field._stored();
}

function holdHeight(both, field, read, height) {
  /* The field takes the height of what it stands in for and scrolls
     inside it, so opening moves nothing below: the reading's, as it stood
     — a reply's calls drawn as blocks, not their raw fences. On the canvas
     the paragraph is text alone: the field's own text with its row as
     drawn (`_shown`), measured in the field's own metrics. */
  if (canvas()) {
    const raw = field.value;
    const own = both.closest(".otk-edit")._shown();
    field.style.height = "auto";
    field.value = (field._frame?.prefix ?? "") + own + (field._frame?.suffix ?? "");
    height = field.scrollHeight;
    field.value = raw;
  }
  if (!height) return;
  field.style.height = `${height}px`;
  field.style.overflowY = "auto";
  hangScrollbar(field);
}

function hangScrollbar(field) {
  /* A scrollbar takes its width out of the text's and rewraps it: the
     field is widened by it instead, the bar standing outside the text's
     measure. An overlay scrollbar takes none, and nothing changes. */
  field.style.width = "";
  field.style.marginRight = "";
  const bar = field.offsetWidth - field.clientWidth;
  if (bar <= 0) return;
  field.style.width = `calc(100% + ${bar}px)`;
  field.style.marginRight = `-${bar}px`;
}

function releaseHeight(field, read) {
  field.style.height = "";
  field.style.overflowY = "";
  field.style.width = "";
  field.style.marginRight = "";
  read.scrollTop = 0;
}

/** The mirror behind the framed field: the same text, raw — what wraps
    in the field must wrap here — the frame's parts dimmed. */
function mirror(read, field) {
  const { prefix, suffix } = field._frame;
  read.replaceChildren(
    span("otk-framed", prefix),
    document.createTextNode(field._typed()),
    span("otk-framed", suffix),
  );
}

/** Typing held to the field's own span: an edit that would touch the
    frame — a selection reaching into it, a delete that would eat into
    it from the span's edge — is dropped before it lands. */
function holdToSpan(field, event) {
  if (!field._frame) return;
  const start = field._frame.prefix.length;
  const end = field.value.length - field._frame.suffix.length;
  const from = field.selectionStart;
  const to = field.selectionEnd;
  const deleting = event.inputType.startsWith("delete");
  const crossing =
    from < start ||
    to > end ||
    (from === to && deleting && event.inputType.endsWith("Backward") && from === start) ||
    (from === to && deleting && event.inputType.endsWith("Forward") && from === end);
  if (crossing) event.preventDefault();
}

/** The paragraph break a block among the prose stands for, as a
    `.otk-blockgap` span — shown on the canvas alone, which hides the
    blocks (`app.css`), so the runs either side read as two paragraphs
    rather than one line glued at the seam. */
function blockGap() {
  return span("otk-blockgap", "\n\n");
}

/** The one space an inline join draws, as a `.otk-seam` span — the
    canvas shows it and the other layouts hide it; never stored. */
function joinSpace() {
  return span("otk-seam", " ");
}

function lineNodes(segments) {
  /* The reader's line from the store's split (`segments`): its commands
     picked out, its words with the model faint — all of a /you or /ooc
     line after the token, whatever follows a /ooc or /cue to its end —
     in every layout; the edges trimmed, as the line is (`drawTurn`). */
  const last = segments.length - 1;
  return segments.flatMap((segment, i) => {
    let text = segment.text;
    if (i === 0) text = text.trimStart();
    if (i === last) text = text.trimEnd();
    const nodes = withSlashTokens(text);
    if (segment.kind !== "ooc") return nodes;
    const ooc = span("otk-ooc");
    ooc.append(...nodes);
    return [ooc];
  });
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
   union `web.api.story.messages.event` writes), and `endTurn` settles it — whatever
   ended it. `play` below is only the order of those. */

/** Play a line and draw the reply as it streams. `line` is null for a
    regenerate, where the prompt is already on screen and in the store. */
export async function play(line, { regenerate = false, files = [] } = {}) {
  // Without this the first of two plays to finish unlocks the composer
  // for both, leaving prose on screen that the store never took.
  if (playing) return;
  // a continue: no line of the reader's to record above the reply
  const continues = !regenerate && !(line ?? "").trim() && !files.length;
  const turn = beginTurn(regenerate, { continues });
  let refused = null;
  let stopped = false;
  try {
    const signal = arriving.signal;
    /* A fresh take on the story held — or the line played on it, a first
       line making the story and a refused one taking it back
       (`held.writing`). */
    const answer = regenerate
      ? held.story() === null
        ? { notice: _NOTHING_TO_REGENERATE, refused: true }
        : await api.stories.regens.create(held.story(), { signal })
      : await held.writing((story) =>
          api.stories.messages.create(story, { line: line ?? "", files }, { signal }),
        );
    // A line that is not valid syntax never becomes a stream: the usage
    // sentence comes back instead, and the story is untouched.
    if (answer.refused) {
      refused = answer.notice;
      // the backend promised the story is untouched, so the screen says
      // the same and the refused regenerate keeps its reply
      if (regenerate && held.story() !== null) {
        showTurns(await api.stories.messages.list(held.story()), { keepPlace: true });
      }
      return;
    }
    for await (const happened of answer.events) {
      draw(turn, happened);
    }
    // the question the reply leaves the story standing on, posed under it
    // once it stands (`endTurn`)
    if (turn.reply) turn.call = await standingQuestion();
  } catch (e) {
    // stopping is not failing: what streamed is already in the story
    if (e?.name !== "AbortError") throw e;
    stopped = true;
  } finally {
    endTurn(turn);
    // said AFTER the turn settles, or its cleanup sweeps the refusal
    // away before anyone reads it — and above the box, where it stands
    if (refused) {
      tell(refused, "otk-error");
      complain(refused);
    }
  }
  // A stopped reply is drawn from what reached this page, which can
  // fall short of what the backend stored: the socket takes events for
  // a moment after the page lets go, and the backend records all it
  // read. The store is the truth — the turn is read back and drawn as
  // stored, so the text is whole and an edit finds the text it drew.
  if (stopped) await settleFromStore(turn);
  // The caller lands the turn and would otherwise say nothing over
  // this; handed back so the refusal stands.
  return refused;
}

async function settleFromStore(turn) {
  if (!turn.article.isConnected || turn.article.dataset.unstored) return;
  const position = storedTurns().indexOf(turn.article) + 1;
  if (!position) return;
  /* The backend lands the reply when its next write to the closed
     stream fails — a moment after the page let go — and a read answered
     in the gap still sees the story without it. So the store is asked
     again, briefly, until the reply is there; a store that moved on
     past this turn is left alone. */
  for (let attempt = 0; attempt < 15; attempt += 1) {
    const messages = await api.stories.messages.list(held.story());
    if (messages.length > position) return;
    const stored = messages[position - 1];
    if (messages.length === position && stored.role === "assistant") {
      const call = await standingQuestion();
      const drawn = drawTurn(stored, position, { posed: Boolean(call) });
      // The status row goes over with its slot: a landed reply keeps the
      // row's room (`endTurn`), and a redraw that dropped it would shrink
      // the flow and move the text the reader is looking at.
      const status = $(".otk-generating__status", turn.article);
      if (status) {
        drawn.classList.add("otk-generating", "otk-generating--idle");
        drawn.append(status);
      }
      // what streamed beside the text — the reasoning — is not stored, so
      // the stored turn can stand shorter: held, as a landing is (`endTurn`)
      holdSpace(() => {
        turn.article.replaceWith(drawn);
        if (call) showAsk(call);
        else clearAsk();
      });
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
}

function beginTurn(regenerate, { continues = false } = {}) {
  shelved = null;
  /* The reply follows only a reader who was at the tail. A regenerate
     also redraws into the space the old take was read in, and follows
     only once that space is filled. Measured before anything moves. */
  following = atTail();
  playing = true;
  // the tail goes as the line it held is sent; what it took — an empty
  // paragraph opened at its end among it — is held, so nothing moves
  if (tail?.isConnected) {
    holdSpace(() => {
      tail.remove();
      seam?.remove();
    });
  }
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
  const turn = openArticle();
  const { article } = turn;
  /* A regenerate takes the standing reply off the screen before the
     request is away: the take being replaced must not sit there while
     the model thinks. The backend validates a regenerate eagerly, so a
     refusal comes back before anything is lost. The waiting block goes
     in its place in the same move — a regenerate sends no user turn for
     the block to join the flow on (`draw`), and the wait must show from
     the first moment here as it does on a send. */
  if (regenerate) {
    holdSpace(() => {
      dropLastReply();
      transcript.insertBefore(article, $(".otk-gap", transcript));
    });
  } else if (continues) {
    /* A continue records nothing above the reply, so no user turn would
       bring the block into the flow before the first word: it goes in
       now, at the end, and the wait shows from the first moment as it
       does on a send. */
    transcript.insertBefore(article, $(".otk-gap", transcript));
    follow();
  }
  showTurnBar();
  turn.over = over;
  return turn;
}

function openArticle() {
  /* The block a reply streams into, as `beginTurn` opens one and as the
     reply going on after a tool ran gets one of its own
     (`DRAW.tool_message`): the article, its caret and status row, and the
     rubric numbered as the next drawn turn. */
  const article = element("article", "otk-reply otk-generating is-streaming");
  /* The reply arriving joins as the landed one will (`turn.join_type`):
     on the canvas as its first piece says (`join_type`) and then the
     landed row; a paragraph everywhere else, where the join is not
     drawn. */
  article.dataset.join = canvas() ? "inline" : "paragraph";
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
  /* Numbered as the next drawn turn — right for a regenerate, whose take
     came off above; a send's line lands first and renumbers it
     (`recorded`). */
  const rubric = replyRubric(ordinal + 1);
  article.prepend(rubric);
  // `pieces`: what has arrived, in order — prose runs and tool calls, the
  // segments a stored turn carries, kept as they stream so each call
  // draws in its place
  // `said`, `saidAt`: the status row's word and when it went up (`sayDoing`)
  return { article, block, status, state, rubric, ticking, said: "waiting", saidAt: started, over: null, reasoning: null, ask: null, pieces: [] };
}

function sayDoing(turn, text, { call = false } = {}) {
  /* What the reply is doing, on its status row. Every word stands a moment
     at least (`_SAY_MS`), so a quick one — a call otaku runs at once, a
     moment of reasoning — is read rather than flashed. A word that comes
     sooner waits its turn: a call's is kept, any other is replaced by the
     next, which says the same moment better. */
  const queue = (turn.waiting ??= []);
  const last = queue.at(-1);
  if (text === (last?.text ?? turn.said)) return;
  if (last && !last.call) queue.pop();
  if (text !== (queue.at(-1)?.text ?? turn.said)) queue.push({ text, call });
  showDoing(turn);
}

function showDoing(turn) {
  // the next waiting word, once the one up has stood its time
  if (turn.deferred || !turn.waiting?.length) return;
  const left = turn.saidAt + _SAY_MS - Date.now();
  if (left > 0) {
    turn.deferred = setTimeout(() => {
      turn.deferred = null;
      showDoing(turn);
    }, left);
    return;
  }
  const { text } = turn.waiting.shift();
  turn.state.textContent = text;
  turn.said = text;
  turn.saidAt = Date.now();
  showDoing(turn);
}

function streamAsk(turn, call) {
  /* The question as it arrives, posed where the landed one will be —
     under the reply, ahead of its status row — and redrawn from what
     has come each time: the same box `showAsk` builds, its options
     unarmed until the reply lands. Read by the tool's own rule
     (`readAsk`); a question the model then goes on past is retired when
     the reply lands (`settle`). */
  const fresh = askBox(readAsk(call.text, call.closed), { armed: false });
  if (turn.ask) turn.ask.replaceWith(fresh);
  else {
    clearAsk();
    turn.article.insertBefore(fresh, turn.status);
  }
  turn.ask = fresh;
}

function streamed(turn) {
  /* The reply as it stands so far, drawn by the landed reply's rule
     (`replyNodes`) from the pieces in the order they arrived. The caret
     rides the end of the prose that has arrived — the last character,
     not the blank line a model leaves before a call — and only while
     prose is what is arriving: a call being written has its own place
     (the status row says so), and a caret left on the prose above it
     would point at where nothing is happening. */
  const boxes = turn.pieces.map((piece) => piece.box).filter(Boolean);
  const nodes = replyNodes(turn.pieces, (piece) => piece.box, {
    inline: turn.article.dataset.join === "inline",
  });
  if (turn.pieces.at(-1)?.kind === "prose") {
    const lastProse = nodes.findLastIndex((node) => !boxes.includes(node));
    nodes.splice(lastProse + 1, 0, caret());
  }
  turn.block.replaceChildren(...nodes);
}

function draw(turn, happened) {
  // A send's block joins the flow on its first event, the user turn that
  // draws the line above it; a regenerate's is in place already
  // (`beginTurn`). Ahead of the held space, which belongs last: behind
  // it, the block would sit between two turns.
  if (!turn.article.isConnected) transcript.insertBefore(turn.article, $(".otk-gap", transcript));
  DRAW[happened.type]?.(turn, happened);
  // what arrives goes into the space the old take was read in
  reserve();
  follow();
}

// One drawer per event kind — `web.api.story.messages.event`'s closed union, drawn.
const DRAW = {
  user_message(turn, happened) {
    ordinal += 1;
    turn.rubric.textContent = _replyRubric(ordinal + 1); // the reply follows the line
    retireAsk(); // the line that landed is the answer: the options go, the question stays
    const drawn = drawTurn(happened.message, ordinal);
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
  assistant_reasoning(turn, happened) {
    if (!turn.reasoning) {
      turn.reasoning = element("p", "otk-reasoning");
      turn.rubric.after(turn.reasoning);
    }
    turn.reasoning.textContent += happened.text;
    sayDoing(turn, "reasoning");
  },
  assistant_text(turn, happened) {
    sayDoing(turn, "writing");
    // the reply's first piece says how it joins the text, on the canvas
    if (canvas() && happened.join_type) turn.article.dataset.join = happened.join_type;
    // prose grows the run it is in; after a call it starts a new one
    const last = turn.pieces.at(-1);
    if (last?.kind === "prose") last.text += happened.text;
    else turn.pieces.push({ kind: "prose", text: happened.text });
    /* Typeset again from the whole text rather than appended to: a quote
       or a mark closes mid-stream, and only the whole text knows what it
       closed. */
    streamed(turn);
  },
  assistant_tool_call(turn, happened) {
    /* A tool call is not story: the status row says what the model is
       doing, so the running line never reads as stalled — and for a
       call otaku runs that is all: the query is the model's, nothing of
       it draws. One the reader is shown — the notes, while the story
       says so — draws as it arrives, in its place among the prose, in
       the box the landed reply draws it in; a question never draws raw
       — the live panel grows as it streams (`streamAsk`), question
       first and each option as its line completes, and the landed
       reply confirms or retires it. A call's pieces grow one piece of
       the turn until the one that closed it; the next tag opens its
       own. */
    // the backend's sentence for the call, as the terminal's status row says it
    sayDoing(turn, happened.status, { call: true });
    let call = turn.pieces.at(-1);
    if (call?.kind !== "tool_call" || call.tool !== happened.tool || call.closed) {
      // a question never takes a notes box: it is the live panel's
      const box =
        happened.tool !== "question" && shownTools.has(happened.tool)
          ? notesBlock("", { open: openNotes.has(happened.tool) })
          : null;
      call = { kind: "tool_call", tool: happened.tool, text: "", closed: false, box };
      turn.pieces.push(call);
    }
    call.text += happened.text;
    call.closed = happened.closed;
    if (call.box) fillNotes(call.box, call.text);
    if (happened.tool === "question" && shownTools.has("question")) streamAsk(turn, call);
    streamed(turn);
  },
  assistant_message(turn, happened) {
    /* The model's row as recorded — it ends where the stream did, the
       closing fence of a call there whether or not the model wrote one
       — kept to land in place of what streamed (`endTurn`, or
       `tool_message` first when a tool runs); the question it closed on
       is the story's to say once it stands (`play`). */
    turn.reply = happened.message;
  },
  tool_message(turn, happened) {
    /* A tool ran. The reply holding the call, recorded just before
       (`assistant_message`), is landed in place; the tool turn is drawn
       after it, and a fresh block opened for the reply that continues:
       one turn, three articles, the block moving on. */
    clearInterval(turn.ticking);
    ordinal += 1;
    const landed = drawTurn(turn.reply, ordinal);
    turn.article.replaceWith(landed);
    ordinal += 1;
    const result = drawTurn(happened.message, ordinal);
    landed.after(result);
    const fresh = openArticle();
    result.after(fresh.article);
    // the block that goes on says what the row said until its time is out
    const { said, saidAt } = turn;
    Object.assign(turn, fresh, { over: turn.over, reply: null, call: null, said, saidAt });
    turn.state.textContent = said;
    sayDoing(turn, "waiting");
    follow();
  },
  /* A failure goes BEFORE the status row, as the stats line does
     (`done`): the row stays, hidden, until the next line, and after it
     its room would stand above the box. */
  declined(turn, happened) {
    turn.article.insertBefore(
      failure("The model declined", happened.reason),
      $(".otk-generating__status", turn.article),
    );
  },
  failed(turn, happened) {
    turn.article.insertBefore(
      failure("The reply stopped", happened.reason),
      $(".otk-generating__status", turn.article),
    );
  },
  done(turn, happened) {
    /* The stats line the terminal prints after a reply, verbatim — off
       unless the reader asked for it (`/set verbose`). It goes BEFORE
       the status row, which stays and keeps its height once a reply
       lands: appended after it, the line would sit against the row
       rather than against the answer it reports on. On the canvas a
       line would break the one text: the stats are the reply's hover
       hint there, as the terminal puts them on its status row. */
    if (happened.stats && canvas()) {
      turn.article.title = happened.stats;
    } else if (happened.stats) {
      // one line, cut where the column ends (`.otk-verbose`): whole in its hover hint
      const line = element("p", "otk-verbose", happened.stats);
      line.title = happened.stats;
      turn.article.insertBefore(line, $(".otk-generating__status", turn.article));
    }
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
  // a word still waiting its turn has nothing left to say
  clearTimeout(turn.deferred);
  turn.waiting = [];
  turn.over();
  turnOver(false);
  working(false);
  // what the page had to say about the attempt is over with it
  tell("");
  /* Nothing the landing changes moves the text the reader is on. The
     caret going, the streamed panel giving way to a past question, a
     reply that never came taken off: each can leave the flow shorter,
     and the browser would pull a reader at its end down with it. The
     space is held instead, until the next turn grows into it. */
  holdSpace(() => {
    // the question a reply ended on, drawn once the reply stands — over
    // the streamed panel, which goes either way: a question the model went
    // on past is a past one now, drawn in place by the reader
    if (turn.call) showAsk(turn.call);
    else if (turn.ask) clearAsk();
    turn.ask = null;
    // the status row STAYS, hidden, so nothing above it moves — until the
    // next line takes its place (`user_message`)
    turn.article.classList.add("otk-generating--idle");
    turn.article.classList.remove("is-streaming");
    turn.article.setAttribute("aria-busy", "false");
    /* What arrived is stored exactly as it streamed, so it becomes the
       same editor a stored reply is drawn as, in the same box — drawn
       from the stored turn `assistant_message` carried, calls and all; a stream cut
       short carries none, and what streamed stands in: its prose, and
       the calls the reader was shown (the others' facts were never
       read). The block that held the answer's place goes when nothing
       came. */
    const prose = turn.pieces.filter((piece) => piece.kind === "prose").map((piece) => piece.text).join("");
    const landed =
      turn.reply ??
      (prose
        ? {
            body: prose,
            segments: turn.pieces.filter((piece) => piece.kind === "prose" || piece.box),
            // the join it streamed with: the stand-in must not lose it
            join_type: turn.article.dataset.join,
          }
        : null);
    if (landed) turn.block.replaceWith(replyReader(landed, { posed: Boolean(turn.call) }));
    else turn.block.remove();
    // a reply that never arrived leaves no empty block behind
    if (!landed && !$(".otk-error, .otk-verbose, .otk-reasoning", turn.article)) {
      turn.article.remove();
    } else if (turn.article.isConnected && landed) {
      /* The reply landed, so the story is one turn longer than the rubric
         counted. The STORED turn is the test, not the block: a declined or
         failed attempt keeps its block to say so but stores no message
         (`backend.domain.story._play._land_reply` records a reply row only when
         some text arrived), and counting it would number every later turn
         one too high. */
      ordinal += 1;
    } else if (turn.article.isConnected) {
      // Kept only to say what happened, storing nothing: marked, so a
      // regenerate dropping it takes no number with it; and no turn, so
      // no rubric.
      turn.article.dataset.unstored = "true";
      turn.rubric.remove();
    }
    // the canvas's typing place back in the flow takes the room the row
    // kept there (`app.css`), so it goes back inside the held change
    showTail();
  });
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
