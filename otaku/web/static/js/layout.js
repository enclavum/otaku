/* Which of the three layouts the transcript is read in — canvas,
   manuscript, chat: the same turns, drawn three ways by the stylesheet
   off ONE attribute, `data-transcript` on the flow. Nothing here moves
   a node; the three looks are `app.css`'s, and the names are the
   backend's (`TranscriptLayout`).

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
import { isPlaying, keepingPlace } from "./transcript.js";

const SETTING = "layout";
const DEFAULT = "manuscript"; // the backend's, for a story with no settings yet

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
    page's `landed` — carries the confirmation on and reads the facts
    again, the mode among them, which the layout may have changed. A
    refusal is said on the status line and moves nothing. */
export function wireLayout(written) {
  for (const button of $$("[data-transcript-mode]")) {
    button.addEventListener(
      "click",
      guard(async () => {
        if (story === null || isPlaying() || button.getAttribute("aria-pressed") === "true") return;
        const layout = button.dataset.transcriptMode;
        const answer = await api.updateSetting(story, SETTING, { layout });
        if (answer.refused) {
          tell(answer.notice, "otk-error");
          return;
        }
        apply(layout);
        await written(answer.notice);
      }),
    );
  }
}

/** The canvas glyph's hint: its name — and, while the model in use can
    play story mode (`facts.is_story_mode_ready`), that it is the story mode
    too. The other two glyphs' hints are their names, in the markup. */
export function hintCanvas(storyMode) {
  const glyph = $('[data-transcript-mode="canvas"]');
  if (!glyph) return;
  const hint = storyMode ? "Canvas and the story mode" : "Canvas";
  glyph.title = hint;
  glyph.setAttribute("aria-label", hint);
}

function apply(layout) {
  // the whole flow reflows; the reader stays on the turn they were reading
  keepingPlace(() => {
    transcript.dataset.transcript = layout;
  });
  for (const button of $$("[data-transcript-mode]")) {
    button.setAttribute("aria-pressed", String(button.dataset.transcriptMode === layout));
  }
}
