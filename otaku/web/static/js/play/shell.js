/* The frame around the transcript: what the runhead and the rail say
   about the story this page holds and the model in use, the layout the
   story is read in, whether otaku is there at all, the page reloaded as
   its own files change, and the one door every write comes back through.

   `landed` carries the rule that a redraw REPLACES the flow, so a
   sentence said before one is swept away by its own result. Said after,
   once, here, so no call site has to remember. */

import { api, whenLost, whenReached } from "../api/index.js";
import { guard } from "../kit/browser.js";
import { $, $$ } from "../kit/dom.js";
import { contextLabel, label } from "../kit/format.js";
import * as held from "./held.js";
import { offline, reached, tell, told, working } from "../kit/status.js";
import {
  displayTools,
  isPlaying,
  joinNext,
  keepingPlace,
  showCorrected,
  showPlayed,
  showTail,
} from "./transcript.js";

const app = $(".otk-app");
const icon = $('link[rel="icon"]');
// The tab icon has no stylesheet to dim it, so the markup carries a
// greyed copy in `data-offline`. The live one is read before anything
// can swap it.
const live = icon?.href ?? "";

// The story the transcript is drawing, so a write knows whether the
// ground moved under it.
let drawn = null;

// What the offline line says — needed in two places, so it is named once.
const GONE = "otaku is down";

// The provider line is drawn from two reads: the facts, and how full the
// next request is ("85% of 64K", the backend's sentence; "" while
// the window is not known) — each kept here, so either redraws the line.
let providing = null;
let fill = "";

/** What the runhead and the rail are drawn from: the app — its version,
    the model in use — and the story this page holds, read as one. A story
    that has gone meanwhile (deleted from another tab, from the terminal)
    is held no longer. */
export async function facts() {
  const id = held.story();
  const [app, story] = await Promise.all([api.app.get(), id === null ? null : api.stories.get(id)]);
  if (story?.refused) {
    held.hold(null);
    return factsOf(app, null);
  }
  return factsOf(app, story);
}

function factsOf(app, story) {
  const model = app.model;
  return {
    version: app.version,
    // the page's own words for the model that is not there
    model: model?.name ?? "(no model)",
    provider: model?.provider ?? "",
    // the window a request gets, as the catalogs label it ("" unknown)
    max_context: contextLabel(model?.context_window),
    story: story?.label ?? "",
    story_id: story?.id ?? null,
    turns: story?.message_count ?? 0,
    /* The look the story is drawn in: the canvas for a story read as one
       text (`Layout.mode`), its own layout otherwise. A story not made
       yet is read in the default one, the manuscript, over the chat
       wire; whether a picture may ride its first line is the model's
       (`ModelInUse.can_attach`). */
    layout: story === null ? "manuscript" : story.mode === "story" ? "canvas" : story.layout,
    completion_type: story?.completion_type ?? "chat",
    can_attach: story?.can_attach ?? model?.can_attach ?? false,
    can_complete_text: model?.can_complete_text ?? false,
    next_join: story?.next_join ?? null,
  };
}

/** The line a landing says when nothing was written to say it — a story
    resumed as it stands: "Story: …. Resumed at message 14." The rule
    `Story.landed_line` holds (`backend.api.story.stories`), copied: the
    page lands without asking, so there is no answer to carry it. */
function landingLine(facts, verb = "Resumed") {
  const name = label(facts.story);
  const head = name ? `Story: ${name}. ` : "";
  return facts.turns ? `${head}${verb} at message ${facts.turns}.` : `${head}${verb}, nothing played yet.`;
}

/** Land in a story as it stands — the browser's Resume, the dossier's on
    its last message — said as a landing is, the transcript drawn anew. A
    story deleted meanwhile (the terminal, another tab) is not landed in:
    the backend's refusal is said instead. */
export async function resume(id) {
  const found = await api.stories.get(id);
  if (found.refused) return landed(found.notice);
  held.hold(id);
  await landed(landingLine(await facts()), { redraw: "always" });
}

export function showFacts(facts) {
  providing = facts;
  drawProvider();
  const fields = {
    version: `v${facts.version}`,
    model: facts.model,
    story: label(facts.story) || "No story yet",
    turns: facts.turns ? `${facts.turns} messages` : "",
  };
  for (const [name, text] of Object.entries(fields)) {
    for (const slot of $$(`[data-fact="${name}"]`)) slot.textContent = text;
  }
  // The rail cuts a long model name to one line, so the whole of it has
  // to be somewhere: hovering the name is where.
  const named = $('[data-fact="model"]');
  if (named) named.title = fields.model;
  // The attach hint is there while a picture may ride the next request
  // (`facts.can_attach`: the model sees, and the mode's wire carries one),
  // gone otherwise. The backend refuses a picture regardless.
  for (const button of $$("[data-attach]")) button.hidden = !facts.can_attach;
  // The wire and the look on the app root, for the stylesheet and the
  // composer: over the text wire no picture rides the request, so the flow
  // shows none (docs/web_tokens.md); on the canvas an empty line continues.
  // The canvas is typed in where the story is (`transcript.showTail`): the
  // box stays, disabled, its placeholder saying so — the markup's own one
  // in every other layout
  const box = $("#otk-msg");
  if (box) {
    box.dataset.placeholder ??= box.placeholder;
    const was = box.disabled;
    box.disabled = facts.layout === "canvas";
    box.dataset.standing = box.disabled ? "Type your turn in the story above" : box.dataset.placeholder;
    // a refusal of the line typed on the canvas reads in the box until it
    // settles (`status.complain`)
    const refused = box.disabled ? box.dataset.refused : undefined;
    box.placeholder = refused ?? box.dataset.standing;
    // opened again — a layout switched away from the canvas — it takes the
    // caret the click could not give it while it was shut
    const idle = document.activeElement === document.body;
    if (was && !box.disabled && idle && !window.matchMedia("(pointer: coarse)").matches) box.focus();
  }
  if (app) {
    app.dataset.completion = facts.completion_type;
    app.dataset.layout = facts.layout;
  }
  // where the canvas's typing place draws its space or opens a paragraph
  joinNext(facts.next_join);
  // the novel is offered only where the model can complete text
  offerNovel(facts.can_complete_text);
  drawn = facts.story_id;
}

/** The facts again, and the transcript with it. */
export async function refresh() {
  const read = await facts();
  showFacts(read);
  await Promise.all([drawPlayed(read.story_id), showContextUse()]);
}

/** How full the next request is, beside the provider — read again with
    every landing, so after a turn, a switch, an undo. Its own read: the
    facts stay cheap, and this one assembles the request. */
export async function showContextUse() {
  const id = held.story();
  const answer = id === null ? null : await api.stories.context.fill.get(id);
  // refused while the window is not known: the window alone, then
  fill = answer && !answer.refused ? answer.text : "";
  drawProvider();
}

function drawProvider() {
  /* The provider, then how full the next request is — or, while that is
     not known, the window alone, where the facts have it. */
  if (!providing) return;
  const text = [providing.provider, fill || providing.max_context].filter(Boolean).join(" · ");
  for (const slot of $$('[data-fact="provider"]')) slot.textContent = text;
}

/** The transcript from the store: the turns, and — off the story's own
    facts, which a story that does not exist yet has none of — which
    tools' calls the reader is shown, the layout it is read in and the
    question it stands on. */
export async function drawPlayed(storyId, { keepPlace = false } = {}) {
  const [messages, story] = await Promise.all([
    storyId === null ? [] : api.stories.messages.list(storyId),
    storyId === null ? null : api.stories.get(storyId),
  ]);
  const facts = story?.refused ? null : story;
  displayTools(facts);
  showLayout(storyId, facts?.layout);
  showPlayed({ messages, question: facts?.question ?? null }, { keepPlace });
}

/** What a write answered with, shown where it belongs. The facts are
    always asked again; the transcript is redrawn only when the story it
    draws is no longer the one that is open, because a redraw costs the
    reader their place. `corrected` — `[position, text]` — is one turn
    corrected elsewhere, shown where the transcript draws it instead.
    `keepPlace`: what a redraw would draw is on the page already — a
    played line's turns, its story made by that line — so it moves
    nothing. */
export async function landed(
  notice,
  { redraw = "if-moved", corrected = null, kind = "", keepPlace = false } = {},
) {
  const read = await facts();
  const moved = read.story_id !== drawn;
  showFacts(read);
  const using = showContextUse();
  if (redraw === "always" || (redraw === "if-moved" && moved)) {
    await drawPlayed(read.story_id, { keepPlace });
  } else if (corrected) {
    await showCorrected(...corrected);
  }
  await using;
  tell(notice, kind);
}

/* How often the page asks whether otaku is still there. Without it the
   answer arrives only when the reader next asks for something, and a
   background tab asks for nothing. A browser throttles this to about a
   minute once the tab is hidden, which is right for what it says. */
const HEARTBEAT = 5000;

/** Both directions — the page finds out that otaku stopped, and that it
    is back. It also carries the background worker's voice: a pass that
    starts on its own after five idle minutes says so here, as it does
    in the terminal's status row.

    `boot` is what a RETURN runs. An otaku that answers again may have been
    restarted, so the page asks for everything rather than lifting the
    mark off a transcript it read from the otaku that went away — the
    story it holds kept, and dropped where it is gone (`facts`). */
export function watchServer(boot) {
  // One answered request is proof otaku is back, one refused connection
  // proof it is gone — said here rather than at every call site.
  let reachable = true;
  whenReached(() => {
    const returned = !reachable;
    reachable = true;
    disconnected(false);
    if (returned) boot();
  });
  whenLost(() => {
    reachable = false;
    disconnected();
  });
  let beating = ""; // the worker's line, while the page is showing it
  setInterval(async () => {
    try {
      const beat = await api.app.status.get();
      /* Every sentence of the beat, not the last: the beat DRAINS the
         worker's mailbox, so one shown is the rest lost forever. One
         line holds them ellipsised, and hovering reads them whole
         (`status.tell` puts the text in the title). */
      const sentence = beat.notices?.length ? beat.notices.join(" ") : "";
      if (sentence) {
        tell(sentence);
        beating = "";
      } else if (beat.status) {
        tell(beat.status);
        beating = beat.status;
      } else if (beating && told() === beating) {
        // The pass is over. Its line goes with it — unless the reader
        // has been told something else since, which stays.
        tell("");
        beating = "";
      }
      /* The lamp says what is RUNNING, whoever started it. This beat
         speaks for the WORKER alone, so it must never put out a lamp it
         did not light: a reply is minutes long, the beat seconds. */
      if (!watcher && !isPlaying()) working(Boolean(beat.status));
    } catch {
      // A beat that cannot be made is the disconnection above, said once.
    }
  }, HEARTBEAT);
}

/* How long the page waits for a forced pass to report, in seconds. A
   long extraction is minutes of model time, so this is generous. */
const EXTRACTION_PATIENCE = 600;
const GAVE_UP = "The pass stopped without a report — see the system log.";

let watcher = null;

/** Watch a forced extraction pass and say what it reports. A pass is one
    line at the END, not a running account, so this polls rather than
    holding a stream open. One watcher at a time — two would announce the
    same pass twice — and bounded, or a server stopped mid-pass leaves a
    timer running for the life of the tab.

    A pass the PAGE forced is one it can also give up on, so the status
    line carries `stop` while this runs. `story` is the story it was
    forced on: a pass belongs to one, and so does the poll. */
export function watchExtraction(story) {
  if (watcher) return;
  let left = EXTRACTION_PATIENCE;
  const done = (sentence) => {
    clearInterval(watcher);
    watcher = null;
    working(false);
    tell(sentence);
  };
  // Guarded like every other floating promise the page starts: a Stop
  // that cannot be delivered must say so, not fail into the console.
  const stop = guard(async () => {
    const { notice } = await api.stories.extraction.delete(story);
    done(notice);
  });
  working(true, stop);
  watcher = setInterval(async () => {
    /* The lamp is shared with the reply, which clears it when it lands,
       so a pass still running takes it back — every tick, there being no
       moment this could be told about. */
    if (!isPlaying()) working(true, stop);
    let report = null;
    try {
      ({ report } = await api.stories.extraction.get(story));
    } catch {
      // A failed poll is a tick like any other: the server may be busy,
      // and the bound below is what stops this running forever.
    }
    if (report) done(report);
    else if ((left -= 1) <= 0) done(GAVE_UP);
  }, 1000);
}

/* The theme switch at the spine's foot: one attribute on <html> selects
   the token set, and this browser's storage remembers the choice — a look
   is the medium's own, so it is kept where the medium keeps things, not in
   the story's settings. Until the first click the page follows the OS and
   the attribute is absent; a click pins the other theme, "dark" or
   "light", whatever the OS says from then on. The knob shows the theme in
   FORCE — the pin when there is one, else what the OS chose — never the
   pin alone: on a dark OS an unpinned page is dark, and a knob reading
   light beside a dark page is a switch that does nothing. The head's
   inline line applies the stored pin before the first paint; this only
   keeps the switch true to it, and answers the click. The sun and the
   moon are labels, not lamps: the knob's position is the whole of the
   state, and both stay one colour. */
export function wireTheme() {
  const control = $("[data-theme-switch]");
  if (!control) return;
  const os = window.matchMedia("(prefers-color-scheme: dark)");
  const inForce = () => {
    const pin = document.documentElement.dataset.theme;
    if (pin === "dark" || pin === "light") return pin;
    return os.matches ? "dark" : "light";
  };
  const show = () => control.setAttribute("aria-checked", String(inForce() === "dark"));
  show();
  // an unpinned page turns with the OS, so the knob turns with it too
  os.addEventListener("change", show);
  control.addEventListener("click", () => {
    const theme = inForce() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("otaku-theme", theme);
    } catch {
      /* private mode or storage refused: the choice holds for this page */
    }
    show();
  });
}

/** Full screen, for a phone's few lines more. Offered only on a touch
    screen whose browser can do it for a page — which no browser on an
    iPhone can; there, adding the page to the home screen is the way. */
export function wireFullscreen() {
  const control = $("[data-fullscreen]");
  if (!control) return;
  control.hidden = !(document.fullscreenEnabled && window.matchMedia("(pointer: coarse)").matches);
  // left by the phone's own back gesture too, so the state is read back
  document.addEventListener("fullscreenchange", () => {
    control.setAttribute("aria-pressed", String(Boolean(document.fullscreenElement)));
  });
  control.addEventListener("click", () => {
    // a browser that declines says nothing worth a sentence: the button stays
    const asked = document.fullscreenElement
      ? document.exitFullscreen()
      : document.documentElement.requestFullscreen();
    asked.catch(() => {});
  });
}

/** The one failure the page has a state for: otaku stopped answering.
    Said the page's own three ways — the status line, the mark in the
    spine greyed, the tab icon with it — and every control that would
    reach otaku turned off, a door onto nothing not looking like a door.
    Nothing is offered to press: the heartbeat is already asking, and the
    page picks otaku up again the moment it answers. The TITLE
    never changes; it names the app, not its state. */
export function disconnected(gone = true) {
  app?.classList.toggle("is-offline", gone);
  if (icon) icon.href = (gone && icon.dataset.offline) || live;
  if (gone) {
    tell(GONE, "otk-error");
    offline(true);
  } else {
    // The sentence goes with the state it was about; anything said since
    // is the reader's news and stays.
    if (told() === GONE) tell("");
    reached();
    offline(false);
  }
  /* Every door that reaches otaku is shut while it is gone:
     the contents and the layout glyphs, the model in the rail's foot, the box and the verbs
     beside it — Send, Regen, Undo — and the prefix menu, which has
     nothing to insert into. Stop is not one of them: it gives up a
     reply THIS page is holding, which is this side of the connection. */
  for (const control of $$(
    ".otk-toc button, .otk-modes button, .otk-rail__foot, .otk-composer textarea, [data-send], [data-turn], [data-prefixes]",
  )) {
    // the box stays shut in the canvas, which is typed in (`showFacts`)
    const canvasBox = control.matches("#otk-msg") && $(".otk-app")?.dataset.layout === "canvas";
    control.disabled = gone || canvasBox;
  }
}

// ---------- the layout the story is read in ----------

/* Which of the four layouts the story is read in — novel, canvas,
   manuscript, chat: the same turns, drawn three ways by the stylesheet
   off ONE attribute, `data-transcript` on the flow, the novel drawn as
   the canvas. Nothing here moves a node; the looks are `app.css`'s, and
   the names are the backend's (`schema.Layout`).

   The choice is the STORY's: its `layout` setting, read with the rest
   where the transcript lands (`drawPlayed`) and written where every
   setting is (`PATCH /api/stories/{story}/settings/layout`). A story that
   does not exist yet has no settings to write, so the glyphs are inert
   until its first turn makes it — as the Tools tab has nothing to show
   for it — and while a reply is arriving: the write reads the facts
   again, the mode among them, and a wire is not changed under a reply
   that is still coming over it. */

const SETTING = "layout";
const DEFAULT = "manuscript"; // the backend's, for a story with no settings yet
// The look each layout is drawn in — `Layout.mode`'s rule, copied:
// the novel is drawn as the canvas.
const LAYOUT_OF = { novel: "canvas", canvas: "canvas", manuscript: "manuscript", chat: "chat" };

const transcript = $(".otk-transcript");
let layoutStory = null; // the story whose layout is shown; null while there is none

/** The layout the story is read in, as `drawPlayed` read it — none for
    a story not made yet. */
function showLayout(storyId, layout) {
  layoutStory = storyId;
  apply(layout ?? DEFAULT);
  for (const button of $$("[data-transcript-mode]")) {
    button.setAttribute("aria-disabled", String(layoutStory === null));
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
        if (layoutStory === null || isPlaying() || button.getAttribute("aria-pressed") === "true") return;
        const value = button.dataset.transcriptMode;
        const answer = await api.stories.settings.update(layoutStory, SETTING, { layout: value });
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
    text (`facts.can_complete_text`); hidden otherwise, where a novel
    would be played as the canvas (`Layout.completion_type`). */
function offerNovel(available) {
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

// ---------- reloading as the files change ----------

/* The page reloaded as the files it is made of change, held open for as
   long as the tab is: an edit to the reader's own `custom.css` shows up
   on its own, and so does a page whose otaku was restarted on a newer
   build. Nothing about it is a development mode — what is on disk is
   what the browser has, which is the same rule the assets are served
   under. */

export function watchForChanges() {
  const watch = new EventSource("/api/watch");
  let connected = false;

  watch.addEventListener("open", () => {
    // A second open means the server restarted under us: new Python, so
    // the page it served is stale too.
    if (connected) location.reload();
    connected = true;
  });

  watch.addEventListener("message", (event) => {
    if (!event.data.endsWith(".css")) {
      location.reload();
      return;
    }
    // A stylesheet swap keeps the page: the open popup, the scroll
    // position and anything in flight all survive it.
    for (const link of $$('link[rel="stylesheet"]')) {
      link.href = `${link.href.split("?")[0]}?v=${Date.now()}`;
    }
  });
}
