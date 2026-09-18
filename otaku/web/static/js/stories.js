/* The story browser: every story, and what the highlighted one can be
   done to — the level-1 panel. Going INSIDE one — premise, messages,
   scenes, cast — is `story.js`, the level under this.

   A screen fetches what it shows as it opens: a popup drawing
   yesterday's numbers is worse than none. Every write goes out through
   its endpoint in `api.js` and comes back through `landed`, so no
   screen decides what a sentence means or when the flow is redrawn.
   Its ask dialogs are filled here — what an action does to the story
   on screen is this screen's to say. */

import * as api from "./api.js";
import { ask, browser, footnote, guard, popups } from "./browser.js";
import { $, actionButton, element, row, span } from "./dom.js";
import { ago, label, since, when } from "./format.js";
import { landed } from "./shell.js";
import { confirmFork, land, openStory } from "./story.js";
import { exportStory } from "./transfer.js";

export async function openStories(answered = "", { selectId = null } = {}) {
  const popup = popups.get("/stories");
  // The panel is up before its data: a click must answer NOW, and the
  // modal keeps further clicks from queueing screens behind it.
  if (!popup.open) popup.showModal();
  // No haystack on these rows: the listing answers its own filter —
  // the union of buried content and the row's face is the backend's.
  const rows = await api.stories();
  footnote(popup, answered || `${rows.length} ${rows.length === 1 ? "story" : "stories"}`);
  const view = browser(popup, {
    rows,
    drawRow: (story) => {
      const drawn = row(span("otk-row__title", label(story.label) || "(untitled)"));
      if (story.open) drawn.append(span("otk-tag", "open"));
      drawn.append(
        span("otk-row__num otk-row__num--count", String(story.turns)),
        span("otk-row__num otk-row__num--ago", since(story.updated_at)),
      );
      return drawn;
    },
    drawPreview: (story) => {
      /* The verbs are pinned under the pane, not appended to the body:
         a story with a long recap and one with none must put the button
         a reader is aiming at in the same place. */
      $("[data-actions]", popup).replaceChildren(
        // Continue is never off: a story with nothing played is resumed
        // by its id alone. What copies or exports a chain has none to
        // work on, so Fork and Export are off — and the verbs that do
        // not read the chain (its inside, its name, its life) stay on.
        actionButton("Continue", {
          kind: "otk-btn--primary",
          onclick: guard(() => landOn(story, "resume")),
        }),
        actionButton("Into the story →", { onclick: guard(() => goInside(story)) }),
        actionButton("Rename", { onclick: guard(() => renameStory(story)) }),
        actionButton("Fork", {
          off: !story.turns,
          onclick: guard(() => confirmFork(() => landOn(story, "fork"))),
        }),
        actionButton("Export", { off: !story.turns, onclick: guard(() => exportOne(story)) }),
        actionButton("Delete", {
          kind: "otk-btn--danger",
          onclick: guard(() => confirmDelete(story)),
        }),
        note(
          "Continue returns to the page. Into the story opens its premise, messages, scenes and cast.",
        ),
      );
      return [
        // the number a story's files are named by (`store.files`)
        span("otk-label", `touched ${when(story.updated_at)} · ${ago(story.updated_at)} · id ${story.id}`),
        element("h3", "otk-detail__title", label(story.label) || "(untitled)"),
        span(
          "otk-meta",
          `${story.turns} ${story.turns === 1 ? "message" : "messages"}` +
            (story.model ? ` · ${story.model}` : ""),
        ),
        story.story_so_far && section("The story so far", story.story_so_far),
        section("First prompt", story.first_user || "(nothing played yet)"),
      ].filter(Boolean);
    },
    onOpen: (story) => landOn(story, "resume"),
    onDelete: confirmDelete,
    search: async (needle) => (await api.stories(needle)).map((story) => story.id),
    empty: (filtered) =>
      filtered
        ? { line: "No story matches that.", hint: `clear the filter to see all ${rows.length}` }
        : { line: "No story yet.", hint: "new story, or import one" },
  });
  // Where the browser opens: on the story asked for — the one a dossier
  // was left from — and failing that on the one that is open.
  view.select(selectId !== null ? (story) => story.id === selectId : (story) => story.open);
}

/** Into the dossier — with the way back: reopened on this story when
    this browser is no longer underneath to reveal. */
function goInside(story) {
  return openStory({ story, allStories: (id) => openStories("", { selectId: id }) });
}

function section(name, text) {
  const box = element("section", "otk-detail__section");
  box.append(span("otk-label", name), element("p", "otk-prose otk-prose--read", text));
  return box;
}

function note(text) {
  return element("p", "otk-note otk-actions__note", text);
}

function exportOne(story) {
  /* Any story exports from where it is read: the document is rendered
     for the story the button is under, and the browser stays open on
     it — nothing about saving a file moves the reader.  */
  return exportStory({ storyId: story.id });
}

// ---------- the landings ----------

async function landOn(story, action) {
  /* Continuing and forking are the landing performed inside a story at
     its LAST message — which neither needs named: a resume takes the
     story as it is, and a fork handed no message copies from the head.
     So the chain is not read here, where a list that knew every story's
     last message would have read every story's chain to draw itself. */
  await land(story.id, null, action);
}

// ---------- the writes ----------

async function renameStory(story) {
  /* A story is titled where it is read — any story, not only the open
     one. The field opens with the title it has, because a rename is
     usually a correction; an empty one is refused by the backend, whose
     sentence says so. */
  if (!story) return;
  const dialog = $('dialog[data-dialog="rename"]');
  const field = $("input", dialog);
  const choice = await ask("rename", () => {
    $(".otk-dialog__body", dialog).textContent = `Renaming: ${label(story.label) || "(untitled)"}`;
    field.value = story.label || "";
  });
  if (choice !== "rename") return;
  const { notice } = await api.setTitle(story.id, field.value);
  // The shell carries the open story's title, and this may be it.
  await landed("");
  openStories(notice, { selectId: story.id });
}

async function confirmDelete(story) {
  const choice = await ask("delete-story", (dialog) => {
    $(".otk-dialog__name", dialog).textContent = label(story.label) || "(untitled)";
    // The two facts that let a reader catch a wrong selection, in the
    // one operation that cannot be undone — from THIS story, never the
    // template's.
    $(".otk-dialog__subject .otk-index__sub", dialog).textContent =
      `${story.turns} messages · last played ${ago(story.updated_at)}`;
    $(".otk-dialog__body", dialog).textContent =
      "The story, its scenes and its cast are removed from this machine. There is no undo.";
  });
  if (choice !== "delete") return;
  const { notice } = await api.deleteStory(story.id);
  await landed(notice, { redraw: "always" });
  openStories();
}
