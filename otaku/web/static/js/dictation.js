/* Dictation at the composer, as the design has it: a click on the
   microphone starts listening and another stops it; what was said is
   written down by the model saved for dictation (`api.app.dictation`) and
   added after what was typed, to be read over before it is sent. Enter
   while listening stops it as the click does; Esc discards what was
   heard, and Esc while it is being written down cancels that.
   The terminal's twin is `terminal.prompt.dictation`, Ctrl+G there.

   The composer carries the state for the stylesheet (`data-dictating`):
   "true" while listening — the level, the voice's spectrum, and the
   elapsed time at the end of the writing line — and "transcribing" until
   the text arrives, the bars grey with a peak travelling across them. While
   it listens the rest of the composer is out of reach (`inert`), the box
   given its focus back after; while the text is on its way the line is
   not sent (`composer.holdSend`). */

import { api } from "./api/index.js";
import { focusComposer, holdSend, insertDictated } from "./composer.js";
import { guard } from "./kit/browser.js";
import { $, encodeFile } from "./kit/dom.js";
import { canRecord, record } from "./kit/microphone.js";
import { complain } from "./kit/status.js";

// How often the elapsed time is read again.
const _CLOCK_MS = 250;
// How tall every bar of the level stands in silence, alike, so a quiet
// row is flat; and how far a bar falls back each frame once its band goes
// quiet, of its full height.
const _REST_PX = 2;
const _FALL = 0.035;
// What else the composer holds, out of reach while it listens: the box,
// every verb but the microphone, the staged pictures, the prefix menu and
// its hint.
const _SHUT_WHILE_LISTENING =
  "#otk-msg, .otk-composer__verbs > button:not([data-dictate]), .otk-attach, .otk-prefixes, [data-prefixes]";

/* The words this medium says itself: why the browser gave no microphone —
   faults the backend never sees. */
const _NEEDS_HTTPS = "The microphone needs HTTPS, or otaku opened on this machine.";
const _NOT_ALLOWED = "The browser was not allowed to use the microphone.";
const _NO_MICROPHONE = "No microphone could be opened.";

const footer = $(".otk-composer");
const time = $(".otk-dictation__time");
const bars = [...document.querySelectorAll(".otk-dictation__level > i")];

/* Where it stands: idle; starting (the check and the microphone asked
   for); listening; transcribing (the text on its way). */
let state = "idle";
let current = null; // the recording, while there is one
let started = 0;
let clock = 0;
let frames = 0;
let levels = [];
let resting = []; // each bar's scale at `_REST_PX`, its full height being its own
// Bumped by a cancel, so text that lands after it knows it is too late.
let turn = 0;
// Whether the box had the focus when listening began: it gets it back.
let boxFocused = false;

export function wire() {
  for (const button of document.querySelectorAll("[data-dictate]")) {
    // the caret stays where it is typed in — the box's, or the canvas's
    button.addEventListener("pointerdown", (event) => event.preventDefault());
    button.addEventListener("click", () => {
      if (state === "idle") guard(start)();
      else if (state === "listening") guard(transcribe)();
      else if (state === "starting") discard(); // stopped before anything was heard
    });
  }
  document.addEventListener(
    "keydown",
    (event) => {
      if (event.key !== "Enter" && event.key !== "Escape") return;
      if (state === "starting") {
        event.preventDefault();
        event.stopImmediatePropagation();
        discard(); // nothing heard yet: nothing to send, nothing to keep
      } else if (state === "listening") {
        event.preventDefault();
        event.stopImmediatePropagation();
        if (event.key === "Enter") guard(transcribe)();
        else discard();
      } else if (state === "transcribing") {
        event.preventDefault();
        event.stopImmediatePropagation();
        if (event.key === "Escape") cancel();
      }
    },
    true,
  );
}

/** Listening, shown at the click: the check and the microphone are asked
    for side by side behind it — a microphone opens slowly the first time —
    and either one refusing takes it back, saying why. The bars come alive
    once the microphone is open; the time runs from the click. */
async function start() {
  if (!canRecord()) {
    complain(_NEEDS_HTTPS);
    return;
  }
  state = "starting";
  started = performance.now();
  levels = bars.map(() => 0);
  show("true");
  rest();
  tick();
  clock = setInterval(tick, _CLOCK_MS);
  const checking = api.app.dictation.create({}, { validateOnly: true });
  let recording;
  try {
    recording = await record(checking.then((check) => (check.refused ? ["wav"] : check.audio_formats)));
  } catch (error) {
    if (state !== "starting") return; // stopped meanwhile: nothing to say
    discard();
    const check = await checking.catch(() => null);
    if (check?.refused) complain(check.notice);
    else complain(error?.name === "NotAllowedError" ? _NOT_ALLOWED : _NO_MICROPHONE);
    return;
  }
  const check = await checking;
  if (state !== "starting") {
    recording.discard(); // stopped while it opened
    return;
  }
  if (check.refused) {
    recording.discard();
    discard();
    complain(check.notice);
    return;
  }
  current = recording;
  state = "listening";
  draw();
}

/** Listening stopped and what was heard written down, added after what
    was typed. */
async function transcribe() {
  const recording = current;
  if (!recording) return;
  current = null;
  stopClocks();
  state = "transcribing";
  show("transcribing");
  holdSend(true);
  const mine = turn;
  let answer;
  try {
    const { blob, format } = await recording.stop();
    const { data } = await encodeFile(blob);
    answer = await api.app.dictation.create({ audio: data, audio_format: format });
  } finally {
    if (mine === turn) {
      state = "idle";
      holdSend(false);
      show(null);
    }
  }
  if (mine !== turn) return; // cancelled while it was on its way
  if (answer.refused) {
    complain(answer.notice);
    return;
  }
  if (answer.value.text) insertDictated(answer.value.text);
}

/** Listening stopped, and what was heard dropped. */
function discard() {
  current?.discard();
  current = null;
  stopClocks();
  state = "idle";
  show(null);
}

/** The text on its way dropped when it lands; the line is the reader's again. */
function cancel() {
  turn += 1;
  state = "idle";
  holdSend(false);
  show(null);
}

/** The state on the composer, for the stylesheet: "true", "transcribing",
    or none; the microphone pressed while it listens and the rest of the
    composer out of reach meanwhile, the box given its focus back after. */
function show(dictating) {
  const listening = dictating === "true";
  if (dictating) footer.dataset.dictating = dictating;
  else delete footer.dataset.dictating;
  for (const button of document.querySelectorAll("[data-dictate]")) {
    button.classList.toggle("is-recording", listening);
  }
  if (listening) boxFocused = document.activeElement === $("#otk-msg");
  for (const part of footer.querySelectorAll(_SHUT_WHILE_LISTENING)) part.inert = listening;
  if (!listening && boxFocused) {
    boxFocused = false;
    focusComposer();
  }
}

/** The level, a frame at a time while it listens: each bar its band's
    loudness, falling back slowly once the band goes quiet. */
function draw() {
  if (!current) return;
  const heard = current.bands();
  levels = levels.map((before, i) => Math.max(heard[i], before - _FALL));
  bars.forEach((bar, i) => {
    bar.style.transform = `scaleY(${Math.max(resting[i], levels[i])})`;
  });
  frames = requestAnimationFrame(draw);
}

/** Every bar at `_REST_PX`, whatever its full height: the row flat. Read
    once the level is shown, when the bars have their heights. */
function rest() {
  resting = bars.map((bar) => Math.min(1, _REST_PX / (bar.offsetHeight || _REST_PX)));
  bars.forEach((bar, i) => {
    bar.style.transform = `scaleY(${resting[i]})`;
  });
}

function stopClocks() {
  clearInterval(clock);
  cancelAnimationFrame(frames);
  for (const bar of bars) bar.style.transform = "";
}

function tick() {
  const seconds = Math.floor((performance.now() - started) / 1000);
  time.textContent = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}
