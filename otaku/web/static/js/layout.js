/* Which of the four layouts the story is read in — novel, canvas,
   manuscript, chat: the same turns, drawn three ways by the stylesheet
   off ONE attribute, `data-transcript` on the flow, the novel drawn as
   the canvas. Nothing here moves a node; the looks are `app.css`'s, and
   the names are the backend's (`schema.Layout`).

   The choice is the STORY's: its `layout` setting, read with the rest
   where the transcript lands (`shell.drawPlayed`) and written where
   every setting is (`PATCH /api/stories/{story}/settings/layout`). A
   story that does not exist yet has no settings to write, so the glyphs
   are inert until its first turn makes it — as the Tools tab has
   nothing to show for it — and while a reply is arriving: the write
   reads the facts again, the mode among them, and a wire is not changed
   under a reply that is still coming over it. */

import * as api from "./api.js";
import { guard } from "./browser.js";
import { $, $$ } from "./dom.js";
import { tell } from "./status.js";
import { isPlaying, keepingPlace, showTail } from "./transcript.js";

const SETTING = "layout";
const DEFAULT = "manuscript"; // the backend's, for a story with no settings yet
// The look each layout is drawn in — `Layout.mode`'s rule, copied:
// the novel is drawn as the canvas.
const LAYOUT_OF = { novel: "canvas", canvas: "canvas", manuscript: "manuscript", chat: "chat" };

const transcript = $(".otk-transcript");
let story = null; // the story whose layout is shown; null while there is none

/** The layout the story is read in, off its settings as `drawPlayed`
    read them — none for a story not made yet. */
export function showLayout(storyId, settings) {
  story = storyId;
  const setting = settings.find((each) => each.name === SETTING);
  apply(setting?.layout ?? DEFAULT);
  for (const button of $$("[data-transcript-mode]")) {
    button.setAttribute("aria-disabled", String(story === null));
  }
}

/** The three glyphs in the contents rail: the one taken is written to
    the story and shown once the backend has it, and `written` — the
    page's `landed` — reads the facts again, the mode among them, which
    the layout may have changed; the switch says nothing, the page
    showing it. A refusal is said on the status line and moves nothing. */
export function wireLayout(written) {
  for (const button of $$("[data-transcript-mode]")) {
    button.addEventListener(
      "click",
      guard(async () => {
        if (story === null || isPlaying() || button.getAttribute("aria-pressed") === "true") return;
        const value = button.dataset.transcriptMode;
        const answer = await api.updateSetting(story, SETTING, { layout: value });
        if (answer.refused) {
          tell(answer.notice, "otk-error");
          return;
        }
        apply(value);
        // the page redrawn IS the answer: nothing said on the status line
        await written("");
      }),
    );
  }
}

/** The novel glyph is offered only while the model in use can complete
    text (`facts.can_complete_text`); hidden otherwise, since the backend
    would refuse the write. */
export function offerNovel(available) {
  const glyph = $('[data-transcript-mode="novel"]');
  if (glyph) glyph.hidden = !available;
}

function apply(value) {
  // the whole flow reflows; the reader stays on the turn they were reading
  keepingPlace(() => {
    transcript.dataset.transcript = LAYOUT_OF[value] ?? DEFAULT;
    showTail(); // the canvas is typed in, every other layout in the box
  });
  for (const button of $$("[data-transcript-mode]")) {
    button.setAttribute("aria-pressed", String(button.dataset.transcriptMode === value));
  }
}
