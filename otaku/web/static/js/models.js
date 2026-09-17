/* The model picker: every catalog otaku can reach on one tab, and the
   providers behind them on the other — two halves of one panel, because
   a model that is missing is fixed on the provider side.

   It opens the way the terminal's picker does: on the providers on this machine,
   NOW, with each cloud catalog's rows merged in behind the open panel
   when it answers. Testing a connection re-asks that same read for one
   provider — a provider that answers the catalog IS the test.

   An api key's VALUE never arrives on this side, only whether one is
   set. */

import * as api from "./api.js";
import { ask, browser, closeAll, footnote, guard, popups, wiring } from "./browser.js";
import { $, $$, actionButton, element, row, span } from "./dom.js";
import { landed } from "./shell.js";

export async function openModels(answered = "", tab = "models") {
  const popup = popups.get("/model");
  // The panel is up before its data: a click must answer NOW, and the
  // modal keeps further clicks from queueing screens behind it.
  if (!popup.open) popup.showModal();

  /* One opening at a time: a catalog can take seconds, so a picker
     closed and reopened inside that window has TWO openings in flight —
     and the older one's answers must not rebuild the newer's panel. */
  const epoch = ++_opening;
  const local = await api.providers("local");
  if (epoch !== _opening) return;
  const state = { popup, panel: local, tab, picked: null };
  $("[data-memory]", popup).textContent = state.panel.memory || "";
  watchMemory(popup);
  const build = (wanted, notice = "") => {
    state.tab = wanted;
    for (const button of $$(".otk-tab", popup)) {
      button.setAttribute("aria-selected", String(button.dataset.tab === wanted));
    }
    for (const pane of $$("[data-pane]", popup)) {
      pane.hidden = pane.dataset.pane !== wanted;
    }
    if (wanted === "models") buildModels(state, notice);
    else buildProviders(state, notice);
  };
  state.build = build;
  for (const button of $$(".otk-tab", popup)) {
    button.onclick = () => build(button.dataset.tab);
  }
  build(tab, answered);

  // The cloud catalogs answer at their own pace, behind the open
  // panel — merged in and redrawn wherever the reader is by then, in
  // the panel's own order: each card says its position, and the one
  // that answers last may belong first (the generic provider).
  api.providers("cloud").then(
    guard((cloud) => {
      if (!popup.open || epoch !== _opening) return;
      state.panel = {
        ...state.panel,
        providers: [...state.panel.providers, ...cloud.providers].sort(
          (a, b) => a.order - b.order || a.id.localeCompare(b.id),
        ),
      };
      build(state.tab);
    }),
    // A cloud phase that could not be asked adds nothing: the local
    // rows stand, and the heartbeat says if otaku itself is gone.
    () => {},
  );
}

// The opening `openModels` is on — bumped per call, so an answer that
// arrives for a superseded one is recognised and dropped.
let _opening = 0;

// How often the gauge is re-read while the picker is open: loading a
// model fills a machine while the reader watches, so the figure moves.
const _MEMORY_MS = 1000;

let _gauge = 0;

function watchMemory(popup) {
  /* Its own read: the picker's inventory costs every provider a probe,
     this costs a syscall. One timer at a time, dying with the panel, so
     a closed picker asks nothing. */
  clearInterval(_gauge);
  const slot = $("[data-memory]", popup);
  _gauge = setInterval(async () => {
    if (!popup.open) return clearInterval(_gauge);
    /* A gauge is not worth an error. Every other floating promise here
       goes through `guard`, which SAYS what failed; nobody asked for
       this one, so a tick that cannot be answered leaves the last figure
       standing and the next tick tries again. */
    let memory = "";
    try {
      ({ memory } = await api.machine());
    } catch {
      return;
    }
    // Only while it is still up: a read in flight when the panel closed
    // must not write into a slot the next screen is using.
    if (popup.open) slot.textContent = memory || "";
  }, _MEMORY_MS);
}

// ---------- the models tab ----------

function buildModels(state, notice) {
  const { popup, panel } = state;
  const pane = $('[data-pane="models"]', popup);
  const offered = panel.providers.flatMap((provider) =>
    provider.models.map((model) => ({ provider, model, haystack: model.name.toLowerCase() })),
  );
  const local = offered.filter((entry) => entry.provider.locality === "local").length;
  const remote = offered.length - local;
  $("[data-tabs-aside]", popup).textContent =
    `${offered.length} ${offered.length === 1 ? "model" : "models"}`;
  footnote(popup, notice || `${local} on this machine · ${remote} over the wire`);

  const use = async (entry) => {
    const { notice: said } = await api.switchModel(entry.provider.id, entry.model.name);
    closeAll();
    await landed(said, { redraw: "always" });
  };

  const setLoaded = async (entry, wanted) => {
    if (!entry?.provider.capabilities?.model_management) return;
    /* Asked first, as the terminal's picker asks: a load takes the
       engine's memory and its time, and `u` sits one key beside `l`. */
    const verb = wanted ? "Load" : "Unload";
    const choice = await ask("confirm", (dialog) => {
      $("[data-title]", dialog).textContent = `${verb} ${entry.model.name}?`;
      $(".otk-dialog__body", dialog).textContent = wanted
        ? "The engine loads it into memory, which can take a while."
        : "The engine frees its memory; picking the model later loads it again.";
      const aside = $("[data-note]", dialog);
      aside.textContent = "";
      aside.hidden = true;
      $('[data-choice="confirm"]', dialog).textContent = verb;
      $('[data-choice="cancel"]', dialog).textContent = "Cancel";
    });
    if (choice !== "confirm") return;
    const answer = await api.setLoaded(entry.provider.id, entry.model.name, wanted);
    /* One flag on one model changed, so that is what changes here:
       asking the catalogs again costs every provider a round trip to
       redraw a lamp, and moves the list under the reader. Only when the
       engine actually DID it — a refused load must not light a lamp on
       a model nothing loaded. */
    if (!answer.refused) entry.model.loaded = wanted;
    state.build("models", answer.notice);
  };

  if (!offered.length) $("[data-actions]", pane).replaceChildren();
  /* Read BEFORE the browser paints: its first paint moves the cursor to
     row 0 and `onMove` would overwrite what the reader was on. And only
     the reader's own moves are remembered: the kit reports that first
     paint and the select below as moves too, and a model that arrives
     with the cloud phase must still be found by that rebuild — not the
     row the paint happened to land on. */
  const wanted = state.picked ?? panel.current;
  let settled = false;
  const view = browser(popup, {
    root: pane,
    rows: offered,
    /* Providers in their own order, each under its caption, and only
       the ones that ANSWERED — one with nothing to offer is dealt with
       on the providers tab. A provider a filter emptied drops out
       rather than captioning an empty stretch. */
    groupOf: (entry) => entry.provider,
    drawGroup: providerHeading,
    drawRow: (entry) => modelRow(entry, panel.current),
    drawPreview: (entry) => modelDetail(pane, entry, panel.current, { use, setLoaded }),
    onOpen: use,
    onMove: (entry) => {
      if (settled) state.picked = `${entry.provider.id}/${entry.model.name}`;
    },
    onKey: (event, entry) => {
      if (event.key !== "l" && event.key !== "u") return false;
      guard(setLoaded)(entry, event.key === "l");
      return true;
    },
    /* Nothing to play: the filter is too narrow, or no provider has
       answered — and the way out of that is the tab beside this list. */
    empty: (filtered) =>
      filtered
        ? { line: "No model matches that.", hint: "clear the filter, or set up a provider" }
        : { line: "No model yet.", hint: "start a local engine, or set up a provider" },
  });
  // Open where the reader was — or on the model the session is playing,
  // the way back to it.
  view.select((entry) => `${entry.provider.id}/${entry.model.name}` === wanted);
  settled = true;
}

function modelRow(entry, current) {
  const lamp = span("otk-row__lamp", "");
  const managed = entry.provider.capabilities?.model_management;
  if (managed && entry.model.loaded) lamp.append(span("otk-dot otk-dot--sm", ""));
  // The chosen model wears its tag right after its name, where the eye
  // reads the name, not out past the figures.
  const title = span("otk-row__title", entry.model.name);
  if (`${entry.provider.id}/${entry.model.name}` === current) title.append(" ", span("otk-tag", "chosen"));
  const button = row(
    lamp,
    title,
    span("otk-row__num otk-row__num--size", entry.model.size),
    // what a request would get: the running instance's size while one
    // runs, the model's own otherwise
    span(
      "otk-row__num otk-row__num--count",
      entry.model.max_context_loaded || entry.model.max_context_catalogue,
    ),
  );
  button.classList.add("otk-row--indent", "otk-row--mono");
  /* Bold is loaded, dim is not, and only where loading is a thing that
     happens: a cloud model is always "loaded", so weighting it would
     say something true of nothing. */
  button.classList.toggle("is-loaded", managed && entry.model.loaded);
  button.classList.toggle("is-dim", managed && !entry.model.loaded);
  return button;
}

function modelDetail(pane, entry, current, { use, setLoaded }) {
  const managed = entry.provider.capabilities?.model_management;
  const where = whereItRuns(entry.provider);
  const state = !managed ? "" : entry.model.loaded ? " · loaded" : " · not loaded";
  const chosen = `${entry.provider.id}/${entry.model.name}` === current;

  const facts = element("div", "otk-detail__section");
  if (entry.model.size) facts.append(fact("size", entry.model.size));
  if (entry.model.max_context_catalogue) facts.append(fact("max context", entry.model.max_context_catalogue));
  // the running instance's size, where it is known and is not the model's
  // own — the info report's rule (`backend.api.reports`)
  const loaded = entry.model.max_context_loaded;
  if (loaded && loaded !== entry.model.max_context_catalogue) facts.append(fact("served max context", loaded));
  // how its thinking is set, and what else it can do: the info report's
  // two rows, in the report's own words (`backend.api.reports`)
  facts.append(fact("reasoning", entry.model.reasoning_words));
  facts.append(fact("capabilities", entry.model.capability_words));
  facts.append(fact("provider", entry.provider.label));

  // Pinned under the pane: the row above is a name of any length, and
  // the button must not move with it.
  const verbs = [
    actionButton("Use for this story", {
      kind: "otk-btn--primary",
      onclick: guard(() => use(entry)),
    }),
  ];
  if (managed) {
    verbs.push(
      actionButton(entry.model.loaded ? "Unload" : "Load", {
        onclick: guard(() => setLoaded(entry, !entry.model.loaded)),
      }),
      element(
        "p",
        "otk-note otk-actions__note",
        "A model can be chosen without being loaded; the engine loads it on the first reply.",
      ),
    );
  }
  $("[data-actions]", pane).replaceChildren(...verbs);

  return [
    span("otk-label otk-label--accent", `${where}${state}`),
    element("h3", "otk-detail__title", entry.model.name),
    chosen && element("p", "otk-note otk-accent-ink", "chosen for this story"),
    facts,
  ].filter(Boolean);
}

function providerHeading(provider) {
  /* A caption per provider: the lamp, its name, and what it holds. */
  const heading = element("h3", "otk-group");
  heading.append(
    element("span", provider.connected ? "otk-dot" : "otk-dot otk-dot--off"),
    span("otk-group__name", provider.label),
    span("otk-group__count", `${provider.models.length} ${whereItRuns(provider)}`),
  );
  return [heading, element("div", "otk-rule")];
}

// ---------- the providers tab ----------

function buildProviders(state, notice) {
  const { popup, panel } = state;
  const pane = $('[data-pane="providers"]', popup);
  const answering = panel.providers.filter((provider) => provider.connected);
  $("[data-tabs-aside]", popup).textContent =
    `${answering.length} of ${panel.providers.length} answering`;
  footnote(popup, notice || `${answering.length} ${answering.length === 1 ? "provider" : "providers"} answering`);

  /* In the registry's order (`providers.registry.ALL_CLIENTS`), which is the
     terminal's picker's: a frontend that re-sorted them would invent an
     order the other does not have. */
  const rows = panel.providers.map((provider) => ({ provider, haystack: provider.label.toLowerCase() }));
  // Read before the paint, for the same reason the models tab does.
  const wanted = state.pickedProvider;

  const view = browser(popup, {
    root: pane,
    rows,
    drawRow: (entry) => {
      const stack = element("span", "otk-stack");
      stack.append(
        span("otk-choice__name", entry.provider.label),
        span("otk-index__sub", entry.provider.url),
      );
      return row(
        element("span", entry.provider.connected ? "otk-dot" : "otk-dot otk-dot--off"),
        stack,
        span("otk-row__num", entry.provider.connected ? "answering" : "not answering"),
      );
    },
    drawPreview: (entry) => providerDetail(state, pane, entry.provider),
    onOpen: () => $("[data-detail] input", pane)?.focus(),
    onMove: (entry) => (state.pickedProvider = entry.provider.id),
  });
  // A rebuild — a save's, a test's, a tab switched away and back —
  // stays on the provider it was about.
  if (wanted) view.select((entry) => entry.provider.id === wanted);
}

function providerDetail(state, pane, provider) {
  const models = provider.models.length;
  const head = span(
    "otk-label",
    provider.connected ? `answering · ${models} ${models === 1 ? "model" : "models"}` : "not answering",
  );

  const fields = element("div", "otk-detail__section");
  const url = urlField(state, provider);
  const key = keyField(state, provider);
  fields.append(span("otk-margin__key", "url"), url);
  fields.append(span("otk-margin__key", "api key"), key);
  fields.append(
    element(
      "p",
      "otk-note",
      "The key is kept in the state dir on this machine and sent only to this provider.",
    ),
  );

  const save = actionButton("Save", {
    kind: "otk-btn--primary",
    onclick: guard(() => saveProvider(state, provider)),
  });
  /* A test asks the CONFIGURED provider, and its answer redraws the
     panel: with changes pending it would test the old values and throw
     the new ones away. So the two verbs take turns — Save while
     something is pending, Test while nothing is — marked rather than
     removed (`aria-disabled` keeps a verb in the row and out of reach),
     and a door that does nothing should not look like one.

     HIDDEN for now: a save re-asks the provider and redraws the row
     with its answer, so the button tested nothing Save had not. It
     comes back when it can test the fields as typed, unsaved — which
     needs a probe door of its own (its transport is the open question:
     a GET would put the key in the request line, a POST files a read
     in the write lane). */
  const test = actionButton("Test connection", {
    onclick: guard(() => {
      if (test.getAttribute("aria-disabled") === "true") return undefined;
      return testProvider(state, provider);
    }),
  });
  test.hidden = true;
  const settle = () => {
    const pending = dirty(state.popup, provider);
    save.setAttribute("aria-disabled", String(!pending));
    test.setAttribute("aria-disabled", String(pending));
  };
  settle();
  for (const input of [url, key]) input.addEventListener("input", settle);
  $("[data-actions]", pane).replaceChildren(save, test);

  return [head, element("h3", "otk-detail__title", provider.label), fields];
}

function urlField(state, provider) {
  /* A local URL is editable — a port moves. A cloud URL is the
     provider's own and shown dim, because reading it is useful and
     changing it is not. */
  const input = element("input", "otk-field otk-field--mono");
  input.type = "url";
  input.dataset.provider = "url";
  input.value = provider.url;
  input.disabled = provider.locality === "remote";
  if (!input.disabled) saveOnEnter(state, input, provider, "url");
  return input;
}

/* Where a provider's server runs, as its client knows it (`backend.Locality`):
   the generic provider is a url and cannot say, so its caption says
   neither. The footnote's count above puts the unknowns with the
   remote ones — the side that may cost money. */
function whereItRuns(provider) {
  if (provider.locality === "local") return "on this machine";
  if (provider.locality === "remote") return "over the wire";
  return "wherever the url points";
}

// What a key that is SET looks like. The value never arrives on this
// side, so these stand for it: cleared when the field is entered, and
// never saved back.
const _MASK = "••••••";

// How long "Checking" stands before it is allowed to become the answer.
// A reader who pressed Test has to see that the question was asked.
const _ASKING_MS = 1000;

const _beat = (ms) => new Promise((wake) => setTimeout(wake, ms));

function keyField(state, provider) {
  /* One shape whether a key is set or not: a password field, empty for a
     provider with no key, masked for one that has it in its section, and
     captioned for one the shell carries. Entering clears
     the stand-in so a new key can be typed; leaving without typing one
     puts it back, the key still being there. Delete or Backspace on the
     bare field marks the key to be FORGOTTEN: the field stays bare with
     a caption saying so, and Save (or Enter) applies it — the terminal's
     Del, one save later. Typing a key instead replaces it. */
  const input = element("input", "otk-field otk-field--mono");
  input.type = "password";
  input.dataset.provider = "api_key";
  if (provider.key_source === "env") {
    /* A key the shell carries: nothing here to mask or forget, so the
       field stays bare and says where the key is until one is typed
       over it — the terminal's caption for the same fact
       (`terminal.screens.models`). */
    input.dataset.rest = "(from environment)";
    keepKey(input);
  }
  if (provider.key_source === "config") {
    const mask = () => {
      input.value = _MASK;
      input.dataset.mask = "yes";
    };
    mask();
    input.addEventListener("focus", () => {
      if (!input.dataset.mask) return;
      delete input.dataset.mask;
      input.value = "";
    });
    input.addEventListener("blur", () => {
      if (!input.value && !input.dataset.forget) mask();
    });
    input.addEventListener("keydown", (event) => {
      if (event.key !== "Delete" && event.key !== "Backspace") return;
      // Erasing typed characters is ordinary editing; the bare field is
      // where the key itself is meant.
      if (input.value && !input.dataset.mask) return;
      event.preventDefault();
      forgetKey(input);
      input.dispatchEvent(new Event("input"));
    });
    input.addEventListener("input", () => {
      if (input.value) keepKey(input);
    });
  }
  saveOnEnter(state, input, provider, "api_key");
  return input;
}

function forgetKey(input) {
  delete input.dataset.mask;
  input.value = "";
  input.dataset.forget = "yes";
  input.placeholder = "cleared";
}

function keepKey(input) {
  delete input.dataset.forget;
  input.placeholder = input.dataset.rest ?? "";
}

/** What a field would write, or null for nothing: a url that differs
    from the configured one — emptied included, which clears it — a
    typed key, or "" for a key marked to be forgotten. */
function pending(input, provider, attr) {
  if (!input || input.disabled) return null;
  if (attr === "url") {
    const value = input.value.trim();
    return value === provider.url ? null : value;
  }
  if (input.dataset.forget) return "";
  return input.value.trim() && !input.dataset.mask ? input.value : null;
}

function saveOnEnter(state, input, provider, attr) {
  /* Enter saves the field it is in, as Save saves both. A field left
     without saving is left alone: a half-typed URL must not become the
     configuration because the reader clicked elsewhere. */
  input.addEventListener(
    "keydown",
    guard(async (event) => {
      /* Esc unwinds one layer, innermost first: the field, not the list
         or the panel behind it. Stopped here, or the popup's own Esc
         takes the whole picker with the half-typed key. */
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        input.value = attr === "url" ? provider.url : "";
        if (attr === "api_key") keepKey(input);
        input.dispatchEvent(new Event("input"));
        $("[data-list]", input.closest("[data-pane]"))?.focus();
        return;
      }
      if (event.key !== "Enter") return;
      event.preventDefault();
      /* What the field would write (`pending`): nothing for an unchanged
         url — writing it anyway makes the no-op surgery answer with a
         could-not-write warning for a value that needed no saving — or
         for a key nobody typed; "" for an emptied url or a forgotten
         key, which the route clears. */
      const value = pending(input, provider, attr);
      if (value === null) return;
      const { notice } = await api.saveProviderField(provider.id, attr, value);
      await refreshProvider(state, provider, notice);
    }),
  );
}

function dirty(popup, provider) {
  /* Whether a save would write anything (`pending`): a moved or emptied
     url, a typed key, a key marked to be forgotten. */
  const url = $('[data-detail] input[data-provider="url"]', popup);
  const key = $('[data-detail] input[data-provider="api_key"]', popup);
  return pending(url, provider, "url") !== null || pending(key, provider, "api_key") !== null;
}

async function saveProvider(state, provider) {
  /* Both fields at once, skipping what did not change; an emptied one
     is cleared. Saving nothing is an answer too. */
  const url = $('[data-detail] input[data-provider="url"]', state.popup);
  const key = $('[data-detail] input[data-provider="api_key"]', state.popup);
  const notices = [];
  for (const [input, attr] of [[url, "url"], [key, "api_key"]]) {
    const value = pending(input, provider, attr);
    if (value === null) continue;
    const { notice } = await api.saveProviderField(provider.id, attr, value);
    notices.push(notice);
  }
  await refreshProvider(state, provider, notices.join(" ") || "Nothing to save.");
}

function testProvider(state, provider) {
  /* The test IS the catalog read for this one provider — the same read
     the picker draws from, no new backend door. The dialog opens FIRST:
     a dead host answers by timing out, and the reader who pressed Test
     must not be left looking at an unchanged screen. */
  const dialog = $('dialog[data-dialog="told"]');
  const answered = ask("told", () => {
    $("[data-title]", dialog).textContent = "Checking";
    $(".otk-dialog__body", dialog).textContent = `Asking ${provider.label} at ${provider.url}…`;
  });
  // A local engine answers in milliseconds, and a question asked and
  // answered inside one frame reads as nothing having happened.
  const asking = Promise.all([api.provider(provider.id), _beat(_ASKING_MS)]).then(([fresh]) => fresh);
  asking.then(
    guard((fresh) => {
      const found = fresh.providers.find((entry) => entry.id === provider.id);
      patch(state, provider, found);
      const models = found?.models.length ?? 0;
      // The dialog the reader is already looking at becomes the answer.
      $("[data-title]", dialog).textContent = found?.connected ? "Connected" : "No answer";
      $(".otk-dialog__body", dialog).textContent = found?.connected
        ? `${provider.label} answered with ${models} ${models === 1 ? "model" : "models"}.`
        : `${provider.label} did not answer at ${provider.url}.`;
    }),
    guard(() => {
      // The question itself could not be asked — the dialog still
      // becomes the answer, never a "Checking" frozen forever.
      $("[data-title]", dialog).textContent = "No answer";
      $(".otk-dialog__body", dialog).textContent = `${provider.label} could not be asked — otaku did not answer.`;
    }),
  );
  // The list catches up once the reader is done with the answer: a
  // rebuild under an open dialog would take the focus out from under it.
  return answered.then(() => state.build("providers", ""));
}

async function refreshProvider(state, provider, notice) {
  /* One provider re-asked and patched into the panel, wherever it now
     stands — the terminal's own one-provider refresh, over the wire. */
  const fresh = await api.provider(provider.id);
  patch(state, provider, fresh.providers.find((entry) => entry.id === provider.id));
  state.build("providers", notice);
}

function patch(state, provider, found) {
  /* One provider's row replaced in place — the rest of the panel is
     what it was, and the list keeps its order and its cursor. */
  if (!found) return;
  state.panel = {
    ...state.panel,
    providers: state.panel.providers.map((entry) => (entry.id === provider.id ? found : entry)),
  };
}

function fact(key, value) {
  // Clipped, never wrapped: the pane keeps its width, and a value too
  // long for it — a ladder of levels — shows as far as it fits, whole
  // in its hover hint. Measured once the pane holds it: the kit
  // attaches the preview right after drawing it, before the microtask.
  const line = element("p", "otk-fact otk-fact--clip");
  const shown = span("", value);
  line.append(span("", key), shown);
  queueMicrotask(() => {
    if (shown.isConnected && shown.scrollWidth > shown.clientWidth) shown.title = String(value);
  });
  return line;
}
