/* The help docket: the language, the two keys that go with it, and what
   the transcript answers to.

   What a reader needs written down is what they cannot see on the page:
   the words a line may open or close with, the two verbs that have a
   key, and the double click that opens a message — no button says so. Everything else IS on the page — a row in the contents, a
   button on its panel, a hint under the box — which is what the footer
   says.

   Every token, argument shape and group name is read from `table.js`,
   the language's one home on this side. What each row MEANS is written
   here, lowercase: a sheet has room for a caption, not for the table's
   full sentence. */

import { popups, wiring } from "./browser.js";
import { $, element, span } from "./dom.js";
import { opens, prose, rows as syntaxRows } from "./table.js";

/* The keys the PAGE answers to. They belong beside the language they
   are used on, which is why they are grouped by the shared table's own
   group and not listed apart. */
/* What the two halves of the language are called on the sheet. The
   medium's own headings: where a word is drawn, and under what, is the
   page's business. */
const _GROUPS = { playing: "Playing", inline: "Inside a prompt" };

const _KEYS = {
  playing: [
    [["ctrl", "r"], "re-run the last prompt"],
    [["ctrl", "u"], "take back the last exchange"],
  ],
};

/* What the transcript answers to, in a section of its own after the
   language: the page's gestures, not a word typed. */
const _TRANSCRIPT = [["double-click", "edit a message in place"]];

/* What each row of the language means, by its token. */
const _MEANS = {
  "/me": "send the message as a character",
  "/you": "ask the model to play a character",
  "/ooc": "talk to the model out of character",
  "/roll": "roll real dice (1d20+5) — the model narrates exactly what fell",
  "… /ooc": "an aside out of character",
  "… /cue": "steer just the next reply — not kept in context afterwards",
};

export function openHelp() {
  const popup = popups.get("/help");
  const body = $("[data-help-body]", popup);
  const scroller = $(".otk-docket__body", popup);
  wiring(popup);

  const blocks = [];
  let group = null;
  let grid = null;
  // The story's own language and nothing else: a command is a row in the
  // contents and a button on the panel it belongs to, and a sheet that
  // repeats the menu is a sheet nobody reads.
  for (const row of syntaxRows()) {
    const half = opens(row.token) ? "playing" : "inline";
    if (half !== group) {
      group = half;
      if (blocks.length) blocks.push(element("div", "otk-rule--double"));
      const block = element("div", "otk-v otk-v--md");
      block.append(span("otk-label", _GROUPS[half]));
      // The row that is not a word: what typing anything else does.
      if (half === "playing") {
        block.append(element("p", "otk-prose otk-prose--lead", prose()));
      }
      grid = element("div", "otk-keys-grid");
      for (const [chord, meaning] of _KEYS[half] ?? []) {
        grid.append(keys(...chord.map((key) => span("otk-key", key))), span("otk-keys__meaning", meaning));
      }
      block.append(grid);
      blocks.push(block);
    }
    const label = keys(span("otk-key", row.token));
    if (row.args) label.append(span("otk-keys__plus", row.args));
    grid.append(label, span("otk-keys__meaning", _MEANS[row.token] ?? ""));
  }

  const gestures = element("div", "otk-keys-grid");
  for (const [gesture, meaning] of _TRANSCRIPT) {
    gestures.append(keys(span("otk-key", gesture)), span("otk-keys__meaning", meaning));
  }
  const transcript = element("div", "otk-v otk-v--md");
  transcript.append(span("otk-label", "Transcript"), gestures);
  blocks.push(element("div", "otk-rule--double"), transcript);

  body.replaceChildren(...blocks);
  /* `showModal` focuses the first focusable descendant unless something
     claims it with `autofocus`. The sheet is what the reader came for,
     so the slip's body takes the focus and the keys scroll it. */
  if (!popup.open) popup.showModal();
  scroller.focus();
}

function keys(...parts) {
  /* One chord, or one typed token: the same box either way, joined by
     the same `+`. */
  const box = element("span", "otk-keys");
  parts.forEach((part, i) => {
    if (i) box.append(span("otk-keys__plus", "+"));
    box.append(part);
  });
  return box;
}
