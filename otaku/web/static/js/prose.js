/* One paragraph of a reply, read into typed RUNS.

   Two conventions meet in the same text and neither may eat the other,
   so both are decided in ONE pass over the characters with independent
   flags: a word can be spoken and emphasised at once, and forcing an
   order would lose one of them.

   DIALOGUE is the terminal's rule, copied — the language barrier is the
   one reason it exists twice, and its home is
   `otaku/terminal/tty/typography.py`, where the whole convention is
   decided. Paired quotes open and close a spoken span; a line opening
   with a dash is spoken until a dash that FOLLOWS sentence punctuation
   hands over to the attribution ("— Yes, — he said. — Come in."), which
   hands back on the next such dash. A dash after an ordinary word is a
   parenthetical and changes nothing. Dialogue is consulted BEFORE the
   markup: a line opening `- ` is speech, never a list, because that is
   what a model types for the dash convention.

   MARKDOWN is the page's own and owes the terminal nothing: `*em*` or
   `_em_`, `**strong**` or `__strong__`, `` `code` ``, and `\` escaping
   any of them. A span closes on the mark it opened with, so a
   file_name inside *emphasis* cannot end it. Inline only — a heading, a
   list or a fence is block structure, and a reply is prose, so they
   stay as the characters the model wrote.

   Forward-only, because a reply arrives a character at a time and there
   is nothing ahead to read: a quote left open is spoken to the end of
   the paragraph, which mid-stream is a line still arriving. */

import { element } from "./dom.js";

// Opening quote → the marks that may answer it. `“` both opens English
// speech and closes German, the straight quote closes itself, and `„`
// takes either curly mark because the strict pairing is rarely typed.
const _CLOSERS = { "«": "»", "“": "”", "„": "“”", '"': '"' };

// The ASCII hyphen counts as a dash: models type it for the convention
// constantly.
const _DASHES = "—–-";

// A dash hands over to (or back from) the attribution only after
// sentence punctuation — anywhere else it is a parenthetical.
const _HANDOVER_AFTER = ",.!?…:;";

// What may emphasise. `_` is held to the stricter rule below, because it
// lives inside file_names and snake_case where `*` does not.
const _MARKS = "*_";

const _WORD = /[\p{L}\p{N}]/u;

/** One paragraph as NODES, ready to put in a `<p>`: the runs above, each
    wrapped in what it is. `spoken` says the whole paragraph was speech,
    which the design draws on the paragraph rather than run by run.

    Every surface that shows a reply uses this — the transcript and the
    dossier's message reader — so a correction reads exactly as it played. */
export function typeset(paragraph) {
  const parts = runs(paragraph);
  const spoken = parts.length > 0 && parts.every((part) => part.spoken);
  return { spoken, nodes: parts.map((part) => draw(part, spoken)) };
}

/** A whole message as NODES for one `pre-wrap` block, typeset paragraph
    by paragraph. The blank lines between paragraphs, and the whitespace
    around each, stay the characters they are: a field holding the same
    text breaks at the same places, which is what lets a message be
    corrected where it is read without a word moving. */
export function typesetBody(text) {
  return text.split(/(\n\s*\n)/).flatMap((part, i) => {
    const core = part.trim();
    if (i % 2 || !core) return [document.createTextNode(part)];
    const lead = part.slice(0, part.indexOf(core));
    const trail = part.slice(lead.length + core.length);
    const { spoken, nodes } = typeset(core);
    // one accent, two shapes: an all-speech paragraph takes it whole, a
    // mixed one a run at a time
    let drawn = nodes;
    if (spoken) {
      const whole = element("span", "otk-prose--dialogue");
      whole.append(...nodes);
      drawn = [whole];
    }
    return [document.createTextNode(lead), ...drawn, document.createTextNode(trail)];
  });
}

function draw(run, wholeParagraphSpoken) {
  let node = document.createTextNode(run.text);
  if (run.code) node = wrap("code", "otk-code", node);
  if (run.em) node = wrap("em", "", node);
  if (run.strong) node = wrap("strong", "", node);
  if (run.spoken && !wholeParagraphSpoken) node = wrap("span", "otk-quote", node);
  return node;
}

function wrap(tag, className, inner) {
  const box = element(tag, className);
  box.append(inner);
  return box;
}

/** A paragraph as runs: `{ text, spoken, em, strong, code }`, in order,
    each one a stretch over which nothing changed. */
export function runs(paragraph) {
  const out = [];
  let text = "";
  const state = { spoken: false, em: false, strong: false, code: false };
  let closer = ""; // the mark that will close the open quote
  let outer = false; // was speech already on when the quote opened?
  let dashLine = false; // this line opened with a dialogue dash
  let atLineStart = true; // nothing of this line's content taken yet
  let lastSig = ""; // last non-space character taken
  let prev = ""; // last character taken, spaces included
  let escaped = false;
  let pending = ""; // a mark waiting to learn whether it has a twin
  let emMark = ""; // which mark opened the emphasis, so only it closes it
  let strongMark = "";

  const cut = () => {
    if (text) out.push({ text, ...state });
    text = "";
  };
  const take = (ch) => {
    text += ch;
    prev = ch;
    if (ch !== " " && ch !== "\n") lastSig = ch;
    atLineStart = ch === "\n";
    if (ch === "\n") dashLine = false;
  };
  const flip = (flag) => {
    cut();
    state[flag] = !state[flag];
  };

  const dialogue = (ch) => {
    if (closer) {
      if (!closer.includes(ch)) return false;
      take(ch); // the closing mark belongs to the speech
      closer = "";
      cut();
      // Back to whatever the quote interrupted, which is NOT always
      // narration: inside a dash-opened line, one speaker quoting
      // another is speech within speech.
      state.spoken = outer;
      return true;
    }
    if (_CLOSERS[ch]) {
      cut();
      outer = state.spoken;
      state.spoken = true;
      closer = _CLOSERS[ch];
      take(ch); // and an opening one joins it
      return true;
    }
    if (!_DASHES.includes(ch)) return false;
    if (atLineStart) {
      cut();
      dashLine = true;
      state.spoken = true;
      take(ch);
      return true;
    }
    if (dashLine && _HANDOVER_AFTER.includes(lastSig)) {
      cut();
      state.spoken = !state.spoken;
      take(ch);
      return true;
    }
    return false;
  };

  /* A single mark, resolved by what stands on either side of it. An
     opening one needs a word after it, so a lone `*` between spaces is
     arithmetic and not emphasis; `_` needs a non-word before it too, or
     snake_case would emphasise its own middle. A closing one needs only
     to match the mark that opened. */
  const single = (mark, ch) => {
    if (emMark === mark && (mark === "*" || !_WORD.test(ch))) {
      flip("em");
      emMark = "";
      return true;
    }
    const opens = !emMark && ch !== " " && ch !== "\n" && (mark === "*" || !_WORD.test(prev));
    if (!opens) return false;
    flip("em");
    emMark = mark;
    return true;
  };

  const consume = (ch) => {
    if (escaped) {
      escaped = false;
      take(ch);
      return;
    }
    // Inside a code span nothing else is markup: what it holds is meant
    // to be read as it was typed.
    if (state.code) {
      if (ch === "`") flip("code");
      else take(ch);
      return;
    }
    if (pending) {
      const mark = pending;
      pending = "";
      if (ch === mark) {
        // Doubled: weight, which likewise closes only on its own mark.
        if (strongMark === mark) {
          flip("strong");
          strongMark = "";
        } else if (!strongMark) {
          flip("strong");
          strongMark = mark;
        } else {
          take(mark);
          take(mark);
        }
        return;
      }
      if (!single(mark, ch)) take(mark);
      consume(ch); // the character after the mark, under the new state
      return;
    }
    if (ch === "\\") {
      escaped = true;
      return;
    }
    if (_MARKS.includes(ch)) {
      pending = ch;
      return;
    }
    if (ch === "`") return flip("code");
    if (dialogue(ch)) return;
    take(ch);
  };

  for (const ch of paragraph) consume(ch);
  /* A mark still waiting when the paragraph ends: the one that CLOSES an
     open span, most often — `*worn*` ending a line — and otherwise a
     character the model simply typed. */
  if (pending) {
    if (emMark === pending) flip("em");
    else take(pending);
  }
  cut();
  return out;
}
