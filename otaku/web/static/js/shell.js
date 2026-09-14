/* The frame around the transcript: what the runhead and the rail say
   about the session, whether otaku is there at all, and the one door
   every write comes back through.

   `landed` carries the rule that a redraw REPLACES the flow, so a
   sentence said before one is swept away by its own result. Said after,
   once, here, so no call site has to remember. */

import * as api from "./api.js";
import { guard } from "./browser.js";
import { $, $$ } from "./dom.js";
import { label } from "./format.js";
import { offline, reached, tell, told, working } from "./status.js";
import { isPlaying, showTurns } from "./transcript.js";

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

export function showFacts(facts) {
  const provider = [facts.provider, facts.max_context && `${facts.max_context} context`]
    .filter(Boolean)
    .join(" · ");
  const fields = {
    version: `v${facts.version}`,
    model: facts.model,
    provider: provider,
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
  drawn = facts.story_id;
}

/** The session again, and the transcript with it. */
export async function refresh() {
  showFacts(await api.facts());
  showTurns(await api.turns());
}

/** What a write answered with, shown where it belongs. The facts are
    always asked again; the transcript is redrawn only when the story it
    draws is no longer the one that is open, because a redraw costs the
    reader their place. */
export async function landed(notice, { redraw = "if-moved", keepPlace = false } = {}) {
  const facts = await api.facts();
  const moved = facts.story_id !== drawn;
  showFacts(facts);
  if (redraw === "always" || (redraw === "if-moved" && moved)) {
    // `keepPlace` is for a write that TAKES something away: what is
    // above it must not move, and the space it emptied stays open.
    showTurns(await api.turns(), { keepPlace });
  }
  tell(notice);
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

    `boot` is what a RETURN runs. An otaku that answers again has been
    restarted, and what it is open on is its own business, so the page
    asks for everything rather than lifting the mark off a transcript
    that may no longer be the session's. */
export function watchServer(boot) {
  // One answered request is proof otaku is back, one refused connection
  // proof it is gone — said here rather than at every call site.
  let reachable = true;
  api.whenReached(() => {
    const returned = !reachable;
    reachable = true;
    disconnected(false);
    if (returned) boot();
  });
  api.whenLost(() => {
    reachable = false;
    disconnected();
  });
  let beating = ""; // the worker's line, while the page is showing it
  setInterval(async () => {
    try {
      const beat = await api.status();
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
    const { notice } = await api.stopExtract(story);
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
      ({ report } = await api.extractionReport(story));
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

/** The one failure the page has a state for: otaku stopped answering.
    Said the page's own three ways — the status line, the mark in the
    spine greyed, the tab icon with it — and every control that would
    reach the session turned off, a door onto nothing not looking like a
    door. Nothing is offered to press: the heartbeat is already asking,
    and the page picks the session up the moment it answers. The TITLE
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
  /* Every door that reaches the session is shut while there is none:
     the contents, the model in the rail's foot, the box and the verbs
     beside it — Send, Regen, Undo — and the prefix menu, which has
     nothing to insert into. Stop is not one of them: it gives up a
     reply THIS page is holding, which is this side of the connection. */
  for (const control of $$(
    ".otk-toc button, .otk-rail__foot, .otk-composer textarea, [data-send], [data-turn], [data-prefixes]",
  )) {
    control.disabled = gone;
  }
}
