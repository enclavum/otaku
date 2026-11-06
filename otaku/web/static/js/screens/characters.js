/* Characters: the codex's cards — the characters the reader keeps
   across stories — as a gallery or a list, the stories' characters no
   card claims beside them, and a card opened as a sheet over both: its
   pictures, its fields, the stories it is in and its journals there —
   and its text written by the model in use from its pictures, each run
   kept as a version to step back and forth through while the card stays
   open.
   And the asks the codex shares with a story's cast: the card picker
   (`pickCard`) — a card to link, to merge into — and a card added to the
   story held (`addCharacter`).

   Every write goes out through its endpoint (`api.cards`, a story's
   characters) and what stands after it is read again; the sentence a
   write answers with is said in the foot of the window it came from — a
   card's while one is open, else the panel's. A story character is
   dragged onto a card to link it — and, where there is no dragging (a
   touch screen, the keyboard), its own sheet has the same two doors. */

import { api } from "../api/index.js";
import {
  ask,
  askConfirm,
  asksBeforeClosing,
  closeAll,
  edited,
  editable,
  footnote,
  guard,
  popups,
  putDown,
  report,
  wiring,
} from "../kit/browser.js";
import {
  $,
  $$,
  LINE_STROKE,
  actionButton,
  element,
  encodeFile,
  pickFiles,
  span,
  svgElement,
  switchButton,
} from "../kit/dom.js";
import { label } from "../kit/format.js";
import { markdown } from "../kit/markdown.js";
import { openPicture } from "../kit/pictures.js";
import * as held from "../play/held.js";
import { landed } from "../play/shell.js";

/* Where the view is kept — the list, or the gallery at a tile size —
   this browser's storage, as the theme's pin is; the unlinked column's
   fold beside it. */
const _VIEW_KEY = "otaku-characters-view";
const _FOLD_KEY = "otaku-characters-unlinked";
/* Each gallery view's tile width — app.css's own (`.otk-chars[data-view]`)
   — which a tile's picture is chosen for. */
const _TILE_WIDTHS = { small: 112, mid: 150, large: 220 };
/* A thumbnail's long edge, as the HTTP spec promises it (`getThumbnail`;
   `backend.app.pictures.services.THUMB_EDGE`). */
const _THUMB_EDGE = 512;
/* The drag between the unlinked column and the cards: a story character,
   by its story and its id. */
const _CHARACTER = "application/x-otaku-character";
// A card dragged among the others, to its place in the reader's order.
const _CARD = "application/x-otaku-card";
// The narrow screen of the stylesheet's own rule: the unlinked column
// starts folded there, until the reader unfolds it.
const _NARROW = "(max-width: 720px), (pointer: coarse) and (max-height: 540px)";
// A picture flipped slides across its frame in this long, easing out.
const _SLIDE = { duration: 240, easing: "cubic-bezier(0.2, 0, 0, 1)" };
const _CALM = "(prefers-reduced-motion: reduce)";
// Cards and a card's thumbnails making way for one dragged among them
// glide this long.
const _MAKE_WAY = { duration: 160, easing: _SLIDE.easing };
// What a card's thumbnail carries while it is dragged.
const _THUMB = "text/x-otaku-picture";
// What scrolls in a card's window: the narrow layout's one column, the
// pictures, the fields, a story's entries and the stories beside them
const _SCROLLERS = ".otk-sheet__body, .otk-pics, .otk-sheet__fields, .otk-sheet__entries, .otk-sheet__storylist";
// The picture viewer's chevrons (`.otk-lightbox__arrow` in `index.html`),
// by way, drawn on a picture's flip buttons.
const _CHEVRONS = { prev: "M10 2.5 4.5 8 10 13.5", next: "M6 2.5 11.5 8 6 13.5" };

/* A card's fields as the sheet edits them, in the card's own order: the
   texts, the two lists — edited as one text, their items between lines
   of three dashes — and the reminder's depth, a number. */
const _FIELDS = [
  ["description", "description"],
  ["personality", "personality"],
  ["scenario", "scenario"],
  ["greeting", "greeting"],
  ["alternate_greetings", "alternate greetings", "list"],
  ["examples", "examples", "list"],
  ["reminder", "reminder"],
  ["reminder_depth", "reminder injection depth", "number"],
  ["system_prompt", "system prompt"],
  ["post_history_instructions", "post-history instructions"],
  ["creator_notes", "creator notes"],
];
const _ITEMS = /\n\s*---\s*\n/;
// What a generation writes, and its history keeps (`backend.codex.cards.services`).
const _GENERATED = ["name", "tagline", "description", "personality", "scenario", "greeting"];
/* Why Generate card from picture is off, as the backend refuses a generation —
   `backend.codex.cards.services`' sentences, copied: nothing is asked
   before the click. */
const _CANNOT_SEE = "The model in use cannot see pictures.";
const _NO_PICTURE = "The card has no picture.";
// How often a generation's time is read again.
const _CLOCK_MS = 250;
// How long the links box waits after the last keystroke before it reads them.
const _LINKS_SETTLE_MS = 800;
// The versions' arrows, back and forth, as `svg` paths.
const _PREVIOUS = ["m15 5-7 7 7 7"];
const _NEXT = ["m9 5 7 7-7 7"];
/* The languages a card is generated in, the menu's own: each by its code,
   its own name — what a reader finds theirs by — and the English name the
   model is asked in. */
const _LANGUAGES = [
  ["en", "English", "English"],
  ["de", "Deutsch", "German"],
  ["es", "Español", "Spanish"],
  ["pt", "Português", "Portuguese"],
  ["ru", "Русский", "Russian"],
  ["ja", "日本語", "Japanese"],
  ["zh", "中文", "Simplified Chinese"],
];
/* The browser's languages beyond them the menu can name, which join it
   at the top where the browser speaks one: by code, as `_LANGUAGES`. */
const _BROWSER_LANGUAGES = {
  fr: ["Français", "French"],
  it: ["Italiano", "Italian"],
  nl: ["Nederlands", "Dutch"],
  pl: ["Polski", "Polish"],
  uk: ["Українська", "Ukrainian"],
  cs: ["Čeština", "Czech"],
  sv: ["Svenska", "Swedish"],
  tr: ["Türkçe", "Turkish"],
  ko: ["한국어", "Korean"],
  id: ["Bahasa Indonesia", "Indonesian"],
  vi: ["Tiếng Việt", "Vietnamese"],
  th: ["ไทย", "Thai"],
  ar: ["العربية", "Arabic"],
  hi: ["हिन्दी", "Hindi"],
};
// Where the language last picked is kept, as the view is.
const _LANGUAGE_KEY = "otaku-card-language";
/* The formats a card is exported in, by the code its button shows: the
   design's examples, standing until export is built. */
const _EXPORT_FORMATS = [
  ["png", "Character card, with the picture"],
  ["json", "Character card, text only"],
  ["md", "Plain text, for reading"],
];
// Where the export format last picked is kept, as the language is.
const _EXPORT_KEY = "otaku-card-export";

const state = {
  // the cards and unlinked characters as last read, and how many of each
  // there are with no filter
  cards: [],
  unlinked: [],
  total: { cards: 0, unlinked: 0 },
  // every card read so far, by id: the picked ones are named from here
  // while a filter hides them
  known: new Map(),
  // the cards ticked for a story, in pick order
  picked: [],
  // the picture each tile shows, by card id
  frames: new Map(),
  // the view: "list", or a key of `_TILE_WIDTHS`
  view: "mid",
  query: "",
  unlinkedQuery: "",
  // the card the card window shows, null while it is closed
  shown: null,
  // the card a generation is being written for, while one is, since when
  // (`performance.now()`), and whether the reader gave it up
  generating: null,
  generatingSince: 0,
  cancelled: false,
  // whether the window is asking whether to keep generated text
  asking: false,
  // whether the model in use sees pictures, as the card window last read it
  canSee: false,
  /* the faces the library drew last, by view and card id, each with the
     card it was drawn for and the tile size it was drawn at: a card whose
     pictures stand as they did keeps its face, its text set in place, so
     drawing the library again loads no picture again (`drawView`) */
  faces: { gallery: new Map(), list: new Map() },
  /* the shown card's versions, the page's alone and gone when the window
     closes: its generated fields as they stood before the first
     generation — the original — and as each generation left them, a hand
     edit changing the version the card stands at; and which that is;
     null before the first */
  generations: null,
};

/** Open Characters — on the card `cardId` where one is asked. */
export async function openCharacters({ cardId = null } = {}) {
  const popup = popups.get("/characters");
  if (!popup.open) popup.showModal();
  const chars = $(".otk-chars", popup);
  chars.dataset.unlinked = readFold();
  setView(popup, readView());
  state.query = state.unlinkedQuery = "";
  $("[data-filter-cards]", popup).value = "";
  $("[data-filter-unlinked]", popup).value = "";
  closeSheets();
  wireChrome(popup);
  await redraw(popup);
  if (cardId !== null) await openCard(popup, cardId);
}

/** One card's window on its own, over what is open (the dossier's cast),
    no panel under it. Settles once the card is put down — "" then, for
    the asker to read again what it shows of the card — or at once with
    the sentence where the card cannot be read. */
export async function openCardWindow(cardId) {
  const refused = await openCard(popups.get("/characters"), cardId);
  if (refused) return refused.notice;
  const sheet = sheetOf("card");
  if (sheet.open) {
    await new Promise((resolve) => sheet.addEventListener("close", resolve, { once: true }));
  }
  return "";
}

/** Add a character: a card from Characters into the story held, in one
    step. The cards are named in a list — each with the stories it is in,
    or why it can't be picked: in this story already, or an empty card —
    the filter under it; the card picked is previewed beside it (its
    portrait, name, tagline and description) and vetted for the
    story, which fills the character's name and the persona it speaks to
    (the story's memory of it, else the reader's to type); its picture's switch where it
    has one. Invite brings it in, the dialog staying, its refusal said in
    its foot, where the story refuses. */
export async function addCharacter() {
  const story = held.story();
  if (story === null) return;
  const [cards, cast] = await Promise.all([api.cards.list(), api.stories.characters.list(story)]);
  // the cards the story's cast is linked to: listed, not to be picked again
  const inStory = new Set((cast.refused ? [] : cast).map((one) => one.card_id).filter((id) => id != null));
  let picked = null;
  let sending = false;
  let vetted = null;
  const answer = { value: null };
  await ask("add-character", (dialog) => {
    const list = $("[data-add-list]", dialog);
    const filter = $("[data-add-filter]", dialog);
    const preview = $("[data-add-preview]", dialog);
    const name = $("#otk-add-name", dialog);
    const persona = $("#otk-add-persona", dialog);
    const picsend = $("[data-add-picsend]", dialog);
    const picsendSwitch = $("[data-add-picsend-switch]", dialog);
    const invite = $('[data-choice="invite"]', dialog);
    filter.value = name.value = persona.value = "";
    filter.placeholder = `filter ${cards.length} ${cards.length === 1 ? "card" : "cards"}`;
    sending = false;
    picsendSwitch.setAttribute("aria-checked", "false");
    picsendSwitch.onclick = () => {
      sending = !sending;
      picsendSwitch.setAttribute("aria-checked", String(sending));
    };
    // why a card can't be picked, in the list's own words; "" for none
    const barred = (card) => (inStory.has(card.id) ? "already in" : card.is_blank ? "empty card" : "");
    const stories = (card) =>
      card.story_count ? `${card.story_count} ${card.story_count === 1 ? "story" : "stories"}` : "not in a story";
    const arm = () => {
      invite.disabled = !(picked && vetted && name.value.trim() && persona.value.trim());
    };
    let turn = 0;
    const choose = guard(async (card, row) => {
      /* The card picked: read whole, its picture decoded, and vetted for
         the story — what fills the names — and only then drawn, the card
         before it standing meanwhile, so the preview changes once; a
         refusal said in the foot, Invite off. */
      const mine = ++turn;
      picked = card;
      vetted = null;
      for (const each of $$(".otk-pick__row", list)) {
        each.setAttribute("aria-selected", String(each === row));
      }
      arm();
      const [whole, check, portrait] = await Promise.all([
        api.cards.get(card.id),
        api.stories.cards.create(story, { card_id: card.id }, { validateOnly: true }),
        card.pictures.length ? decoded(api.pictures.thumbnail.url(card.pictures[0].file)) : null,
      ]);
      if (mine !== turn) return;
      drawPreview(preview, card, whole.refused ? "" : whole.description, portrait);
      name.value = card.name;
      picsend.hidden = !card.pictures.length;
      if (check.refused) {
        report(check.notice);
        return;
      }
      footnote(dialog, "");
      vetted = check;
      if (!persona.value.trim()) persona.value = check.persona;
      arm();
    });
    const draw = (query) => {
      // the cards whose name, an alias or the tagline holds the filter's words
      const needle = query.trim().toLowerCase();
      const shown = cards.filter(
        (card) => !needle || [card.name, ...card.aliases, card.tagline].join(" ").toLowerCase().includes(needle),
      );
      if (!shown.length) {
        list.replaceChildren(empty(needle ? "No card matches that." : "No cards yet."));
        return;
      }
      list.replaceChildren(
        ...shown.map((card) => {
          const row = element("button", "otk-row otk-pick__row");
          row.type = "button";
          row.setAttribute("role", "option");
          row.setAttribute("aria-selected", String(picked?.id === card.id));
          const why = barred(card);
          if (why) row.setAttribute("aria-disabled", "true");
          row.append(span("otk-pick__name", card.name), span("otk-pick__meta", why || stories(card)));
          row.onclick = () => choose(card, row);
          return row;
        }),
      );
    };
    const signal = wiring(dialog);
    filter.addEventListener("input", () => draw(filter.value), { signal });
    name.addEventListener("input", arm, { signal });
    persona.addEventListener("input", arm, { signal });
    /* Invite settles nothing on its own: the card is brought in here, and
       a refusal answers into the open dialog. Wired before `ask` adds its
       own listeners, so the stop is the whole veto — the button and the
       dialog's Enter alike. */
    const veto = guard(async (event) => {
      event.preventDefault();
      event.stopImmediatePropagation();
      if (invite.disabled) return;
      if (!name.value.trim()) return name.reportValidity();
      if (!persona.value.trim()) return persona.reportValidity();
      invite.disabled = true;
      const brought = await api.stories.cards.create(story, {
        card_id: picked.id,
        name: name.value.trim(),
        persona: persona.value.trim(),
        with_pictures: sending && !picsend.hidden,
      });
      if (brought.refused) {
        report(brought.notice);
        arm();
        return;
      }
      answer.value = brought;
      dialog.close();
    });
    invite.addEventListener("click", veto, { signal });
    dialog.addEventListener(
      "keydown",
      (event) => {
        if (event.key === "Enter" && !event.shiftKey && !event.target.matches(".otk-filter")) veto(event);
      },
      { signal },
    );
    draw("");
    /* The preview stands from the start, its picture's placeholder in the
       frame, so the first card read fills it rather than popping it in. */
    preview.hidden = false;
    preview.removeAttribute("data-nopic");
    $("[data-add-portrait]", preview).replaceChildren();
    for (const part of $$("[data-add-title], [data-add-tag], [data-add-desc]", preview)) {
      part.replaceChildren();
    }
    arm();
    // the first card that can be picked, picked
    const first = $('.otk-pick__row:not([aria-disabled="true"])', list);
    if (first) first.click();
  });
  if (!answer.value) return;
  held.hold(story);
  // The card is in: the screen it was asked from goes, and the landing is
  // read in the flow, as an import's is.
  closeAll();
  await landed(answer.value.notice, { redraw: "always" });
}

async function decoded(url) {
  // a picture loaded and decoded, ready to be shown without a blank frame
  const image = element("img");
  image.alt = "";
  image.src = url;
  try {
    await image.decode();
  } catch {
    // one that will not decode is shown as it is: the frame says so
  }
  return image;
}

function drawPreview(preview, card, description, portrait) {
  // the card picked, as the preview shows it, all of it at once
  preview.hidden = false;
  preview.toggleAttribute("data-nopic", !portrait);
  $("[data-add-portrait]", preview).replaceChildren(...(portrait ? [portrait] : []));
  $("[data-add-title]", preview).textContent = card.name;
  $("[data-add-tag]", preview).textContent = card.tagline;
  const text = $("[data-add-desc]", preview);
  text.replaceChildren(...markdown(description));
  // a card read from its top, wherever the last one was left
  text.scrollTop = 0;
}

/** Which card: a card to link, to merge into, through the one picker
    (`pick`). `{card}` for the one chosen, null for none. `exclude` leaves
    a card out (the one being merged). */
export async function pickCard({ title = "Choose a card", exclude = null } = {}) {
  const picked = await pick({
    title,
    read: async (query) => (await api.cards.list({ query })).filter((card) => card.id !== exclude),
    none: (query) => (query ? ["No card matches that."] : ["No cards yet.", "import one"]),
  });
  return picked?.chosen ? { card: picked.chosen } : picked;
}

/** The picker every "which one" goes through — a codex card (`pickCard`),
    or a story's character to merge another into (the dossier's cast):
    `read(query)` answers the rows, each with an id, a name, a tagline and
    its pictures, the first shown; `none(query)` what an empty list says;
    `filtering` the filter's placeholder and `rows` the list's name for a
    screen reader. `{chosen}` for the row chosen, null for none. */
export async function pick({ title, read, none, filtering = "filter cards", rows: named = "Cards" }) {
  const dialog = $('dialog[data-dialog="pick-card"]');
  let chosen = null;
  const choice = await ask("pick-card", () => {
    $("[data-title]", dialog).textContent = title;
    const list = $("[data-pick]", dialog);
    const filter = $("[data-filter-pick]", dialog);
    const confirm = $('[data-choice="choose"]', dialog);
    const signal = wiring(dialog);
    filter.value = "";
    filter.placeholder = filtering;
    list.setAttribute("aria-label", named);
    confirm.disabled = true;
    let asked = 0;
    const draw = async (query) => {
      // one read per keystroke; only the newest paints
      const mine = ++asked;
      const rows = await read(query);
      if (mine !== asked || signal.aborted) return;
      if (!rows.length) {
        list.replaceChildren(empty(...none(query)));
        return;
      }
      list.replaceChildren(
        ...rows.map((item) => {
          const row = element("button", "otk-row otk-pickrow");
          row.type = "button";
          row.setAttribute("role", "option");
          row.setAttribute("aria-selected", String(chosen?.id === item.id));
          // the picture, the name, and the tagline after it on its line
          row.append(portrait("otk-charrow__pic", item), span("otk-tile__name", item.name));
          if (item.tagline) row.append(span("otk-tile__tag", item.tagline));
          row.onclick = () => {
            chosen = item;
            for (const each of $$(".otk-pickrow", list)) each.setAttribute("aria-selected", String(each === row));
            confirm.disabled = false;
          };
          row.ondblclick = () => confirm.click();
          return row;
        }),
      );
    };
    filter.addEventListener("input", guard(() => draw(filter.value.trim())), { signal });
    // Choose answers only with a row in hand: Enter on an empty pick
    // stays in the dialog.
    const veto = (event) => {
      if (chosen) return;
      event.preventDefault();
      event.stopImmediatePropagation();
    };
    confirm.addEventListener("click", veto, { signal });
    dialog.addEventListener(
      "keydown",
      (event) => {
        if (event.key === "Enter" && !event.shiftKey) veto(event);
      },
      { signal },
    );
    guard(() => draw(""))();
  });
  if (choice === "choose" && chosen) return { chosen };
  return null;
}

// ---------- the panel ----------

function wireChrome(popup) {
  /* The panel's own controls, wired once per opening: the view switch,
     the fold, the filters, the picked bar and Start — and the panel
     closing puts an open sheet down with it. */
  const signal = wiring(popup);
  const chars = $(".otk-chars", popup);
  for (const button of $$(".otk-seg__btn", popup)) {
    button.addEventListener(
      "click",
      () => {
        setView(popup, button.dataset.view, { kept: true });
        // each tile's picture is chosen for its width: drawn again
        drawCards(popup);
      },
      { signal },
    );
  }
  for (const toggle of $$(".otk-unlinked__toggle", popup)) {
    toggle.addEventListener(
      "click",
      () => {
        chars.dataset.unlinked = chars.dataset.unlinked === "closed" ? "open" : "closed";
        keep(_FOLD_KEY, chars.dataset.unlinked);
        fitWidth(popup);
      },
      { signal },
    );
  }
  window.addEventListener("resize", () => fitWidth(popup), { signal });
  const turns = { cards: 0, unlinked: 0 };
  $("[data-filter-cards]", popup).addEventListener(
    "input",
    guard(async (event) => {
      const mine = ++turns.cards;
      state.query = event.target.value.trim();
      const cards = await api.cards.list({ query: state.query });
      if (mine !== turns.cards || signal.aborted) return;
      state.cards = cards;
      drawCards(popup);
    }),
    { signal },
  );
  $("[data-filter-unlinked]", popup).addEventListener(
    "input",
    guard(async (event) => {
      const mine = ++turns.unlinked;
      state.unlinkedQuery = event.target.value.trim();
      const unlinked = await api.cards.unlinked.list({ query: state.unlinkedQuery });
      if (mine !== turns.unlinked || signal.aborted) return;
      state.unlinked = unlinked;
      drawUnlinked(popup);
    }),
    { signal },
  );
  $("[data-clear]", popup).addEventListener(
    "click",
    () => {
      state.picked = [];
      drawCards(popup);
    },
    { signal },
  );
  $("[data-start]", popup).addEventListener("click", guard(() => startStory(popup)), { signal });
  $("[data-import-cards]", popup).addEventListener("click", guard(() => importDialog(popup)), {
    signal,
  });
  // the panel closing takes an open sheet with it
  popup.addEventListener("close", () => closeSheets(), { signal });
}

function readView() {
  try {
    const view = localStorage.getItem(_VIEW_KEY) ?? "";
    return view === "list" || Object.hasOwn(_TILE_WIDTHS, view) ? view : "mid";
  } catch {
    return "mid";
  }
}

function setView(popup, view, { kept = false } = {}) {
  state.view = view;
  $(".otk-chars", popup).dataset.view = view;
  for (const button of $$(".otk-seg__btn", popup)) {
    button.setAttribute("aria-pressed", String(button.dataset.view === view));
  }
  if (kept) keep(_VIEW_KEY, view);
  fitWidth(popup);
}

function fitWidth(popup) {
  /* The panel as wide as the gallery's columns need, no wider: as many
     tiles as fit the widest the panel may be, and not a pixel over, so
     the room right of the last column is the gallery's padding, as left
     of the first — the tiles keep their size, the panel takes the slack.
     Measured from the layout at the stylesheet's own width; the list,
     and a screen the panel fills (where the grid centres instead), keep
     that width. Again on a tile size, a fold, a window resized. */
  popup.style.width = "";
  const tile = _TILE_WIDTHS[state.view];
  if (!popup.open || !tile || matchMedia(_NARROW).matches) return;
  const gallery = $("[data-gallery]", popup);
  const shape = getComputedStyle(gallery);
  const pad = parseFloat(shape.paddingLeft) + parseFloat(shape.paddingRight);
  const gap = parseFloat(shape.columnGap) || 0;
  // all the panel is besides the gallery: the unlinked column, the borders
  const around = popup.getBoundingClientRect().width - gallery.offsetWidth;
  const gutter = gallery.offsetWidth - gallery.clientWidth;
  const columns = Math.max(1, Math.floor((gallery.clientWidth - pad + gap) / (tile + gap)));
  const width = around + gutter + pad + columns * tile + (columns - 1) * gap;
  popup.style.width = `${Math.ceil(width)}px`;
}

function readFold() {
  // the reader's own fold; without one, a narrow screen starts folded
  try {
    const fold = localStorage.getItem(_FOLD_KEY);
    if (fold === "open" || fold === "closed") return fold;
  } catch {
    // nothing kept: the screen decides
  }
  return matchMedia(_NARROW).matches ? "closed" : "open";
}

function keep(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // a browser that keeps nothing still switches
  }
}

/** Read the cards and the unlinked characters again — under the filters
    as typed — and draw both; what a write answered (`answer`) is the
    footnote, else the counts. */
async function redraw(popup, answer = null) {
  const answered = answer?.notice ?? "";
  const refused = Boolean(answer?.refused);
  // An answer is said where the reader is looking: on the card's window
  // when one is open — alone, with no panel under it to draw, or over
  // the panel, which then says what it stands at.
  const card = sheetOf("card");
  const onCard = Boolean(answered) && card.open;
  if (onCard) footnote(card, answered, refused);
  if (!popup.open) return;
  const [cards, unlinked] = await Promise.all([
    api.cards.list({ query: state.query }),
    api.cards.unlinked.list({ query: state.unlinkedQuery }),
  ]);
  state.cards = cards;
  state.unlinked = unlinked;
  if (!state.query) state.total.cards = cards.length;
  if (!state.unlinkedQuery) state.total.unlinked = unlinked.length;
  for (const card of cards) state.known.set(card.id, card);
  // a picked card deleted, or merged away, is no longer picked
  if (!state.query) state.picked = state.picked.filter((id) => cards.some((card) => card.id === id));
  drawCards(popup);
  drawUnlinked(popup);
  const { cards: n, unlinked: m } = state.total;
  const standing =
    `${n} ${n === 1 ? "card" : "cards"} · ${m} unlinked story ${m === 1 ? "character" : "characters"}`;
  footnote(popup, onCard ? standing : answered || standing, !onCard && refused);
}

function drawCards(popup) {
  drawView(popup, state.cards, "gallery");
  drawView(popup, state.cards, "list");
  drawPicked(popup);
}

function drawView(popup, cards, view) {
  /* One view of the cards — the gallery's tiles or the list's rows —
     ending in the new card; with no card yet, it stands alone, first in
     its place. Dragged among the others, a card takes its place in the
     reader's order — the whole of it only: under a filter, where the
     cards between are hidden, a place is not one the reader can see. */
  const gallery = view === "gallery";
  const box = $(gallery ? "[data-gallery]" : "[data-rows]", popup);
  const kept = state.faces[view];
  const faces = new Map();
  const items = cards.map((card) => {
    const old = kept.get(card.id);
    if (old && old.size === state.view && samePictures(old.card, card)) {
      // the card object its handlers hold, brought up to date
      Object.assign(old.card, card);
      setFace(old.element, old.card);
      faces.set(card.id, old);
      return old.element;
    }
    const element = gallery ? tile(popup, card) : listRow(popup, card);
    faces.set(card.id, { card, element, size: state.view });
    return element;
  });
  state.faces[view] = faces;
  const none =
    !cards.length && state.query
      ? empty("No card matches that.", `clear the filter to see all ${state.total.cards}`)
      : null;
  const made = newCard(popup, gallery ? "otk-tile otk-tile--new" : "otk-charrow otk-charrow--new");
  box.replaceChildren(...(none ? [none] : []), ...items, made);
  for (const each of items) each.draggable = !state.query;
  // a face kept from an unfiltered draw drops its drag wiring under a filter
  if (state.query) wiring(box);
  else reorderable(box, items, _CARD, (order, from) => moveCard(popup, cards, order, from));
}

function samePictures(was, card) {
  // Whether a card's pictures stand as they did: the same files, in order.
  return (
    was.pictures.length === card.pictures.length &&
    was.pictures.every((picture, at) => picture.file === card.pictures[at].file)
  );
}

function setFace(face, card) {
  /* A tile's or a row's text set where it stands — the name, the tagline,
     the stories it is in, its tick — its picture left as it is. */
  face.setAttribute("aria-label", card.name);
  $(".otk-tile__name", face).textContent = card.name;
  $(".otk-tile__tag", face).textContent = card.tagline;
  $(".otk-tile__meta", face).textContent = meta(card);
  $(".otk-tick", face)?.setAttribute("aria-checked", String(state.picked.includes(card.id)));
}

async function moveCard(popup, cards, order, from) {
  /* The card dragged, put where it was let go: just before the card that
     now follows it, or last. The view it was dragged in stands as asked
     already, so a move that lands draws only the other one again — a
     gallery drawn anew loads every picture again — and a refused one
     puts back what stands. */
  const at = order.indexOf(from);
  const next = order[at + 1];
  const answer = await api.cards.update(cards[from].id, {
    before: next === undefined ? null : cards[next].id,
  });
  if (answer.refused) {
    await redraw(popup, answer);
    return;
  }
  state.cards = order.map((index) => cards[index]);
  drawView(popup, state.cards, state.view === "list" ? "gallery" : "list");
  footnote(popup, answer.notice);
}

function drawPicked(popup) {
  const names = state.picked.map((id) => state.known.get(id)?.name).filter(Boolean);
  $("[data-picked]", popup).textContent = names.join(", ");
  $("[data-clear]", popup).hidden = !names.length;
  $("[data-start]", popup).disabled = !names.length;
}

function tile(popup, card) {
  /* A card in the gallery: its picture — flipped through where it has
     several, its tick for a story in the corner — its name, tagline and
     the stories it is in. A card with no picture takes one from its
     frame. */
  const box = element("div", "otk-tile");
  box.tabIndex = 0;
  box.setAttribute("role", "button");
  box.setAttribute("aria-label", card.name);
  const pic = element("div", "otk-tile__pic");
  if (card.pictures.length) {
    const at = Math.min(state.frames.get(card.id) ?? 0, card.pictures.length - 1);
    const image = element("img");
    image.alt = "";
    image.loading = "lazy";
    image.draggable = false; // the card is what drags
    framed(image, card.pictures[at]);
    pic.append(image);
    pic._shown = image;
    if (card.pictures.length > 1) {
      const arrows = flipButtons(card.pictures.length, (to, by) => {
        state.frames.set(card.id, to);
        const next = element("img");
        next.alt = "";
        next.draggable = false;
        framed(next, card.pictures[to]);
        guard(() => slideIn(pic, next, by))();
      });
      arrows.mark(at);
      pic.append(...arrows.buttons);
    }
  } else {
    // the short of it on a small tile, clear of the tick in the corner
    const add = element("button", "otk-tile__empty");
    add.append("+ ", span("otk-tile__empty-verb", "add "), "picture");
    add.type = "button";
    add.onclick = guard(async (event) => {
      event.stopPropagation();
      await addPictures(popup, card.id);
    });
    pic.append(add);
  }
  pic.append(tick(popup, card));
  box.append(
    pic,
    span("otk-tile__name", card.name),
    span("otk-tile__tag", card.tagline),
    span("otk-tile__meta", meta(card)),
  );
  opens(box, () => openCard(popup, card.id));
  linksDropped(popup, box, card);
  return box;
}

function listRow(popup, card) {
  const line = element("div", "otk-charrow");
  line.tabIndex = 0;
  line.setAttribute("role", "button");
  line.setAttribute("aria-label", card.name);
  line.append(
    tick(popup, card, { inline: true }),
    portrait("otk-charrow__pic", card),
    nameAndTag(card),
    span("otk-tile__meta", meta(card)),
  );
  opens(line, () => openCard(popup, card.id));
  linksDropped(popup, line, card);
  return line;
}

function framed(image, picture) {
  /* A tile's picture, sharp at the tile's size on this screen without
     loading more than that: the thumbnail and the picture itself, each
     with its width, for the browser to take the smaller that will do. A
     tile crops to 3:4, so a picture wider than that needs as many more
     pixels as it is wider. */
  const thumbnail = api.pictures.thumbnail.url(picture.file);
  const scale = _THUMB_EDGE / Math.max(picture.width, picture.height);
  if (scale < 1) {
    const wide = Math.max(1, (picture.width / picture.height) * (4 / 3));
    // in the list the tiles are hidden, and drawn again when a size is picked
    image.sizes = `${Math.ceil((_TILE_WIDTHS[state.view] ?? _TILE_WIDTHS.mid) * wide)}px`;
    image.srcset = [
      `${thumbnail} ${Math.round(picture.width * scale)}w`,
      `${api.pictures.url(picture.file)} ${picture.width}w`,
    ].join(", ");
  }
  // where the thumbnail is the picture whole, it is all there is to take
  image.src = thumbnail;
}

function newCard(popup, className) {
  /* The card made three ways: an unlinked story character dropped on it —
     promoted, and linked — a card file dropped on it or chosen by its
     button, read into the codex as it is, or, clicked, a card by hand,
     made at once and opened on its name to be filled in. */
  const box = element("div", className);
  box.tabIndex = 0;
  box.setAttribute("role", "button");
  box.setAttribute("aria-label", "New card — or drop an unlinked character or a card file here");
  // the long of it, and the short a small tile has room for
  const choose = element("button", "otk-tile--new__file");
  choose.append(
    span("otk-tile--new__file-long", "choose a card file"),
    span("otk-tile--new__file-short", "choose a file"),
  );
  choose.type = "button";
  choose.onclick = guard(async (event) => {
    event.stopPropagation(); // the file, not a card by hand
    await importCards(popup, await pickFiles(".png,.json,image/*"));
  });
  box.append(
    span("otk-tile--new__plus", "+"),
    span("otk-tile--new__label", "new card"),
    span("otk-tile--new__hint", "drop an unlinked character or a card file here"),
    choose,
  );
  opens(box, () => newCardByHand(popup));
  dropTarget(box, async (dropped) => {
    const choice = await askConfirm({
      title: `Register ${dropped.name} as a card?`,
      body: `A new card is made from ${dropped.name} in "${storyName(dropped)}" and linked to it.`,
      action: "Register",
    });
    if (choice === "confirm") await promote(popup, dropped);
  });
  filesDropped(box, (files) => importCards(popup, files));
  return box;
}

function filesDropped(target, onDrop) {
  // files dragged in from outside the page
  dropsOn(target, "Files", "copy", (data) => onDrop([...data.files]));
}

function dropsOn(target, type, effect, onDrop) {
  /* A target for one kind of drag — `type`, as `dataTransfer.types`
     names it — and nothing else: anything else dragged here is another
     target's. `onDrop` is handed what was dropped. */
  const carries = (event) => event.dataTransfer?.types.includes(type);
  target.addEventListener("dragover", (event) => {
    if (!carries(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = effect;
    target.classList.add("is-dragover");
  });
  target.addEventListener("dragleave", () => target.classList.remove("is-dragover"));
  target.addEventListener(
    "drop",
    guard(async (event) => {
      target.classList.remove("is-dragover");
      if (!carries(event)) return;
      event.preventDefault();
      await onDrop(event.dataTransfer);
    }),
  );
}

async function importCards(popup, files) {
  /* Card files into the codex, each as it is — renamed, if at all, once it
     is there: one the codex holds already answers its card, any other is
     made a card of its own, last in the order, and a picture that holds
     no card a blank one, the picture its portrait. One at a time, the first
     refused stopping the rest and saying why; the panel says the last
     answer. */
  if (!files.length) return;
  const answer = await sendEach(files, ({ name, data }) => api.cards.create({ data, file_name: name }));
  await redraw(popup, answer);
}

async function sendEach(files, send) {
  /* Each file encoded and sent in turn (`send`), the first refused
     stopping the rest: the last answer, which says why. */
  let answer = null;
  for (const file of files) {
    answer = await send(await encodeFile(file));
    if (answer.refused) break;
  }
  return answer;
}

async function importDialog(popup) {
  /* Import cards, as the design draws it: card files and pictures dropped
     or chosen, links pasted one per line — each READ as it arrives, a row
     of the list: a file vetted as it is (`validateOnly`), a link
     fetched and vetted, the file it gave kept in the row, so a link is
     fetched once. A row says what it is — its picture, its name, its
     state — and the backend's notes on it, in three lines that never
     wrap; a card still to be imported renamed in place, and read again
     under its name; a row skipped stays, faint, to be brought back — one
     similar to a card Characters has skipped from the start. Import adds
     the rows not skipped, ready or similar, each sent as a card file in
     turn, and the foot then holds Close alone, the rows left to be read. Files and links
     are read in two queues, each one at a time, so no site is asked twice
     at once; put down, the dialog reads and sends nothing more. */
  const rows = [];
  let importing = false;
  let done = false;
  let last = null;
  await ask("cards", (dialog) => {
    const zone = $(".otk-dropzone", dialog);
    const picker = $("input[type=file]", dialog);
    const links = $("[data-links]", dialog);
    const list = $("[data-rows]", dialog);
    const count = $("[data-count]", dialog);
    const countAfter = $("[data-count-after]", dialog);
    const importButton = $("[data-import]", dialog);
    const cancel = $('[data-choice="cancel"]', $("[data-before]", dialog));
    const before = $("[data-before]", dialog);
    const after = $("[data-after]", dialog);
    picker.value = "";
    links.value = "";
    list.replaceChildren();
    before.hidden = false;
    after.hidden = true;
    zone.inert = links.disabled = false;
    zone.classList.remove("is-dragover");

    const adding = (row) => !row.skipped && (row.state === "ready" || row.state === "similar");
    const recount = () => {
      list.hidden = !rows.length;
      const ready = rows.filter(adding).length;
      const many = rows.length === 1 ? "character" : "characters";
      count.textContent = importing
        ? "Importing\u2026"
        : rows.length
          ? `${rows.length} ${many} · ${ready} to import`
          : "";
      importButton.setAttribute("aria-disabled", String(importing || !ready));
    };
    const drawRow = (row) => {
      /* A row as its state stands: the source while it is read or where it
         was refused, the card's name over the source once read. */
      const read = row.check && row.state !== "refused";
      const thumb = span(read ? "otk-imp__thumb" : "otk-imp__thumb otk-imp__thumb--empty");
      if (read && row.check.thumbnail) {
        const image = element("img");
        image.alt = "";
        image.src = `data:image/jpeg;base64,${row.check.thumbnail}`;
        thumb.append(image);
      }
      // three lines, each cut short where it runs long, whole in its tooltip
      const line = (className, words) => {
        const each = span(className, words);
        each.title = words;
        return each;
      };
      const text = span("otk-imp__text");
      if (read) {
        const name = line("otk-imp__name", row.check.name);
        const nameLine = span("otk-imp__nameline");
        nameLine.append(name);
        // a card still to be imported is named here, as it will be kept
        if (!done && (row.state === "ready" || row.state === "similar")) {
          nameLine.append(renameButton(name, (typed) => renamed(row, typed)));
        }
        text.append(nameLine, line("otk-imp__sub", row.label));
      } else {
        text.append(line("otk-imp__name otk-imp__name--src", row.label));
      }
      const notes = read ? row.check.notes : [];
      if (notes.length) text.append(line("otk-imp__notes", notes.join(" ")));
      const state = span("otk-imp__state", stateOf(row));
      // why a row is similar to a card Characters has, the backend's sentence
      if (row.state === "similar") state.title = row.check.similar_note;
      /* Skipping a row keeps it, faint, its cross turned a plus that
         brings it back; only a link's row leaves the list, its line taken
         out of the links. */
      const toggle = element("button", "otk-imp__remove");
      toggle.type = "button";
      toggle.title = row.skipped ? "Import this row" : "Skip this row";
      toggle.setAttribute("aria-label", row.skipped ? "Import" : "Skip");
      toggle.hidden = done;
      toggle.append(row.skipped ? plusGlyph() : crossGlyph());
      toggle.onclick = () => {
        row.skipped = !row.skipped;
        row.chosen = true;
        drawRow(row);
      };
      row.box.dataset.state = row.state;
      row.box.toggleAttribute("data-skipped", Boolean(row.skipped));
      row.box.replaceChildren(thumb, text, state, toggle);
      recount();
    };
    const vetted = (row, answer) => {
      // what a read answered — the first, or one under a new name — drawn
      if (row.gone || !dialog.open) return;
      if (answer.refused) {
        row.state = "refused";
        row.reason = answer.notice;
      } else {
        row.check = answer;
        row.state = answer.same_card ? "duplicate" : answer.similar_cards.length ? "similar" : "ready";
        // similar to a card Characters has, likely the same: left out until the
        // reader brings it back — the reader's own choice standing
        if (!row.chosen) row.skipped = row.state === "similar";
      }
      drawRow(row);
    };
    const landed = (row, answer) => {
      // what an import answered, drawn
      if (row.gone || !dialog.open) return;
      if (answer.refused) {
        row.state = "refused";
        row.reason = answer.notice;
      } else {
        // a card another row brought in meanwhile is the codex's already
        row.state = answer.value.made ? "added" : "duplicate";
        last = answer;
      }
      drawRow(row);
    };
    const renamed = guard(async (row, name) => {
      /* A row under the name the reader typed: read again with it —
         the file in hand, so a link is not fetched again — for what
         Characters may hold of that name; the import makes it so. */
      row.rename = name;
      vetted(row, await api.cards.create({ ...row.file, name }, { validateOnly: true }));
    });
    const queues = { file: [], link: [] };
    const running = { file: false, link: false };
    const read = guard(async (kind) => {
      // one queue's rows, one at a time, while the dialog stands
      if (running[kind]) return;
      running[kind] = true;
      try {
        while (queues[kind].length && dialog.open) {
          const row = queues[kind].shift();
          if (row.gone) continue;
          if (kind === "file") {
            const { name, data } = await encodeFile(row.source);
            row.file = { file_name: name, data };
            vetted(row, await api.cards.create(row.file, { validateOnly: true }));
          } else {
            const answer = await api.cards.fetches.create({ url: row.source }, { validateOnly: true });
            // a link's file, and the line its site shows the card under
            if (!answer.refused) {
              row.file = { file_name: answer.file_name, data: answer.data, tagline: answer.tagline };
            }
            vetted(row, answer);
          }
        }
      } finally {
        running[kind] = false;
      }
    });
    const add = (kind, source, label) => {
      const row = { kind, source, label, state: "reading", check: null, box: element("li", "otk-imp__row") };
      rows.push(row);
      list.append(row.box);
      drawRow(row);
      queues[kind].push(row);
      read(kind);
    };
    const drop = (row) => {
      // a link's row, gone with its line from the links
      row.gone = true;
      rows.splice(rows.indexOf(row), 1);
      row.box.remove();
      recount();
    };
    const addFiles = (picked) => {
      zone.classList.remove("is-dragover");
      if (importing || done) return;
      // a file dropped again is the row it has already
      const same = (a, b) =>
        a.name === b.name && a.size === b.size && a.lastModified === b.lastModified;
      for (const file of picked) {
        if (!rows.some((row) => row.kind === "file" && same(row.source, file))) {
          add("file", file, file.name);
        }
      }
    };
    const readLinks = () => {
      /* The box's links as rows: a link new to it read, one gone from it
         dropped, each link once. */
      if (importing || done) return;
      const wanted = linksIn(links.value);
      for (const row of rows.filter((each) => each.kind === "link")) {
        if (!wanted.includes(row.source)) drop(row);
      }
      for (const link of wanted) {
        if (!rows.some((row) => row.kind === "link" && row.source === link)) add("link", link, link);
      }
    };

    picker.onchange = () => {
      addFiles([...picker.files]);
      // emptied, so the same file chosen again is another change
      picker.value = "";
    };
    zone.ondragover = (event) => {
      event.preventDefault();
      zone.classList.add("is-dragover");
    };
    zone.ondragleave = () => zone.classList.remove("is-dragover");
    zone.ondrop = (event) => {
      event.preventDefault();
      addFiles([...event.dataTransfer.files]);
    };

    const run = guard(async () => {
      /* The rows not skipped, ready or similar, each sent as the
         card file it was read as; then the foot holds Close alone. */
      // both buttons off while the cards go in: nothing to press but Esc
      importing = true;
      zone.inert = links.disabled = cancel.disabled = true;
      recount();
      try {
        for (const row of rows.filter(adding)) {
          if (!dialog.open) break;
          landed(row, await api.cards.create({ ...row.file, name: row.rename ?? row.check.name }));
        }
      } finally {
        importing = false;
        cancel.disabled = false;
      }
      if (!dialog.open) {
        // put down midway: the library shows what landed meanwhile
        await redraw(popup);
        return;
      }
      done = true;
      for (const row of rows) drawRow(row);
      const added = rows.filter((row) => row.state === "added").length;
      countAfter.textContent = `Imported ${added} ${added === 1 ? "card" : "cards"}`;
      before.hidden = true;
      after.hidden = false;
      $("[data-done]", dialog).focus();
      await redraw(popup);
    });

    /* Import settles nothing on its own: the dialog stays to show what
       each row came to. Wired before `ask` adds its own listeners, so the
       stop is the whole veto — the button and the dialog's Enter alike;
       `wiring` drops the last opening's veto with its stale closures. */
    const signal = wiring(dialog);
    let settling = null;
    links.addEventListener(
      "input",
      () => {
        clearTimeout(settling);
        settling = setTimeout(readLinks, _LINKS_SETTLE_MS);
      },
      { signal },
    );
    // a paste is read once it lands, an edit once the typing stops
    links.addEventListener("paste", () => setTimeout(readLinks), { signal });
    const veto = (event) => {
      event.preventDefault();
      event.stopImmediatePropagation();
      if (done) dialog.close();
      else if (importButton.getAttribute("aria-disabled") !== "true") run();
    };
    importButton.addEventListener("click", veto, { signal });
    dialog.addEventListener(
      "keydown",
      (event) => {
        if (event.key === "Enter" && !event.shiftKey && !event.target.matches("textarea")) {
          veto(event);
        }
      },
      { signal },
    );
    dialog.addEventListener("close", () => clearTimeout(settling), { signal });
    recount();
  });
  // what the last import answered, below the library read again
  await redraw(popup, last);
}

function stateOf(row) {
  /* A row's state in its own words: the page's, but a refusal's reason,
     which is the backend's sentence, set lowercase in the row. */
  switch (row.state) {
    case "reading":
      return "reading…";
    case "duplicate":
      return "already in Characters · skipped";
    case "similar":
      return `similar to ${row.check.similar_cards[0].name}`;
    case "refused":
      return `refused · ${row.reason.slice(0, 1).toLowerCase()}${row.reason.slice(1).replace(/\.$/, "")}`;
    default:
      return row.state;
  }
}

function crossGlyph() {
  // a row's skip, a cross
  return rowGlyph("M6 6l12 12M18 6 6 18");
}

function plusGlyph() {
  // a skipped row's way back, a plus
  return rowGlyph("M12 5v14M5 12h14");
}

function renameButton(name, onrename) {
  /* The pen after a row's name: it opens the name for typing, the caret
     at its end; Enter or leaving it keeps what was typed — none, the name
     it had — and Esc puts it back, the dialog's own keys untouched. */
  const pen = element("button", "otk-imp__rename");
  pen.type = "button";
  pen.title = "Rename";
  pen.setAttribute("aria-label", "Rename");
  const glyph = svgElement("svg", { width: "11", height: "11", viewBox: "0 0 24 24", "aria-hidden": "true" });
  glyph.append(
    svgElement("path", {
      d: "M21.17 6.81a2.82 2.82 0 0 0-3.99-3.99L3.84 16.17a2 2 0 0 0-.5.83l-1.32 4.35a.5.5 0 0 0 .62.62l4.35-1.32a2 2 0 0 0 .83-.5Z",
    }),
    svgElement("path", { d: "m15 5 4 4" }),
  );
  pen.append(glyph);
  const was = name.textContent;
  // the press would blur the name first, which saves: the pen then opens it again
  pen.addEventListener("mousedown", (event) => {
    if (name.isContentEditable) event.preventDefault();
  });
  pen.onclick = () => {
    if (name.isContentEditable) {
      name.blur();
      return;
    }
    name.contentEditable = "true";
    name.focus();
    const range = document.createRange();
    range.selectNodeContents(name);
    range.collapse(false);
    getSelection().removeAllRanges();
    getSelection().addRange(range);
  };
  name.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== "Escape") return;
    event.preventDefault();
    event.stopPropagation();
    if (event.key === "Escape") name.textContent = was;
    name.blur();
  });
  name.addEventListener("blur", () => {
    name.contentEditable = "false";
    const typed = name.textContent.replace(/\s+/g, " ").trim();
    name.textContent = typed || was;
    if (typed && typed !== was) onrename(typed);
  });
  return pen;
}

function rowGlyph(d) {
  const svg = svgElement("svg", { width: "14", height: "14", viewBox: "0 0 24 24", "aria-hidden": "true" });
  svg.append(
    svgElement("path", {
      d,
      fill: "none",
      stroke: "currentColor",
      "stroke-width": "1.8",
      "stroke-linecap": "round",
    }),
  );
  return svg;
}

function linksIn(text) {
  // the pasted links, each once, in the order pasted
  return [...new Set(text.split(/\s+/).filter(Boolean))];
}

function tick(popup, card, { inline = false } = {}) {
  const box = element("button", inline ? "otk-tick otk-tick--inline" : "otk-tick");
  box.type = "button";
  box.setAttribute("role", "checkbox");
  box.setAttribute("aria-checked", String(state.picked.includes(card.id)));
  box.setAttribute("aria-label", `Select ${card.name}`);
  box.dataset.card = String(card.id);
  box.onclick = (event) => {
    event.stopPropagation();
    state.picked = state.picked.includes(card.id)
      ? state.picked.filter((id) => id !== card.id)
      : [...state.picked, card.id];
    // both views draw the tick: the other one follows
    for (const each of $$(`.otk-tick[data-card="${card.id}"]`, popup)) {
      each.setAttribute("aria-checked", String(state.picked.includes(card.id)));
    }
    drawPicked(popup);
  };
  return box;
}

function meta(card) {
  return card.story_count
    ? `${card.story_count} ${card.story_count === 1 ? "story" : "stories"}`
    : "not in a story";
}

function drawUnlinked(popup) {
  const list = $("[data-unlinked-list]", popup);
  const rows = state.unlinked;
  const { unlinked: total } = state.total;
  $("[data-unlinked-count]", popup).textContent = String(total);
  $("[data-unlinked-strip]", popup).textContent = `Unlinked story characters · ${total}`;
  if (!rows.length) {
    list.replaceChildren(
      state.unlinkedQuery
        ? empty("No story character matches that.")
        : empty("Every story character is linked."),
    );
    return;
  }
  list.replaceChildren(
    ...rows.map((row) => {
      const item = element("div", "otk-unlinked__item");
      item.tabIndex = 0;
      item.setAttribute("role", "button");
      item.draggable = true;
      const text = element("span", "otk-unlinked__text");
      text.append(
        span("otk-tile__name", row.name),
        span("otk-tile__tag", label(row.story_label) || "(untitled)"),
        span("otk-unlinked__count", journalsLabel(row.journal_count)),
      );
      item.append(span("otk-grip"), text);
      item.addEventListener("dragstart", (event) => {
        event.dataTransfer.effectAllowed = "link";
        event.dataTransfer.setData(_CHARACTER, JSON.stringify(row));
      });
      opens(item, () => openUnlinked(popup, row));
      return item;
    }),
  );
}

// ---------- links: dropped, picked, made ----------

function linksDropped(popup, target, card) {
  /* Asked first, as the drop on New card is: let go a moment early, or
     over the next tile, a drop links where it was not meant to. */
  dropTarget(target, async (dropped) => {
    const choice = await askConfirm({
      title: `Link ${dropped.name} to ${card.name}?`,
      body: `${dropped.name} in "${storyName(dropped)}" will show this card's picture in its Cast tab.`,
      action: "Link",
    });
    if (choice === "confirm") await link(popup, dropped, card.id);
  });
}

/** A story character's story as a card's lists name it. */
function storyName(row) {
  return label(row.story_label) || "(untitled)";
}

function dropTarget(target, onDrop) {
  // a story character from the unlinked column
  dropsOn(target, _CHARACTER, "link", (data) => onDrop(JSON.parse(data.getData(_CHARACTER))));
}

async function link(popup, character, cardId) {
  const { story_id: story, character_id: id } = character;
  const vetted = api.stories.characters.update(story, id, { card_id: cardId }, { validateOnly: true });
  const withCopies = await takeCopies(popup, vetted);
  if (withCopies === null) return;
  const answer = await api.stories.characters.update(story, id, {
    card_id: cardId,
    with_copies: withCopies,
  });
  // refused, the window it was asked from stays, saying why
  if (!answer.refused) closeSheets();
  await redraw(popup, answer);
}

async function promote(popup, character) {
  const fields = { story_id: character.story_id, character_id: character.character_id };
  const withCopies = await takeCopies(popup, api.cards.create(fields, { validateOnly: true }));
  if (withCopies === null) return;
  const answer = await api.cards.create({ ...fields, with_copies: withCopies });
  if (!answer.refused) closeSheets();
  await redraw(popup, answer);
}

/** Whether a link takes the character's copies in forked stories along —
    asked only where the vetted link (`vetted`) finds any. null for no
    link at all: the vetting refused, said where it was asked from, or
    the question put down. */
async function takeCopies(popup, vetted) {
  const check = await vetted;
  if (check.refused) {
    await redraw(popup, check);
    return null;
  }
  const n = check.copies;
  if (!n) return false;
  const choice = await askConfirm({
    title: "Also in forked stories",
    body:
      n === 1
        ? "The character also appears in 1 other story that was forked, link it too? " +
          "You can review and unlink it if needed in the card later."
        : `The character also appears in ${n} other stories that were forked, link them all? ` +
          "You can review and unlink them if needed in the card later.",
    action: "Link all",
    cancel: "Only this one",
  });
  return choice === null ? null : choice === "confirm";
}

async function newCardByHand(popup) {
  /* A card made at once, under the name the backend gives one asked for
     none, and opened on that name — open to be typed over. The panel shows
     it once it is kept: every write in its window draws the panel again;
     put down just as it was made, it goes again, never having shown. */
  const made = await api.cards.create({});
  if (made.refused) {
    report(made.notice);
    return;
  }
  await openCard(popup, made.value.id, { naming: true });
  const sheet = sheetOf("card");
  if (!sheet.open) {
    await redraw(popup, made);
    return;
  }
  sheet.addEventListener("close", guard(() => dropUntouched(popup, made.value)), { once: true });
}

async function dropUntouched(popup, made) {
  /* The card as it stands against the card as it was made, all but when it
     was last written: the same, it is deleted, saying nothing — gone
     already (merged, deleted), there is nothing to do. A delete refused
     leaves the card, shown with why. */
  const card = await api.cards.get(made.id);
  if (card.refused) return;
  const kept = ({ updated_at: _, ...rest }) => JSON.stringify(rest);
  if (kept(card) !== kept(made)) return;
  const answer = await api.cards.delete(made.id);
  if (!answer.refused) return;
  if (popup.open) await redraw(popup, answer);
  else report(answer.notice);
}

// ---------- the open card ----------

function sheets() {
  return $$("dialog[data-sheet]");
}

function sheetOf(name) {
  return $(`dialog[data-sheet="${name}"]`);
}

/** Put the open sheets down — but `kept`, which is drawn again where it
    stands rather than closed and opened again. */
function closeSheets(kept = null) {
  for (const sheet of sheets()) if (sheet !== kept && sheet.open) sheet.close();
}

function showSheet(sheet) {
  /* A sheet is a window of its own, over the panel or (a card from the
     dossier's cast) over whatever is open: Esc and the scrim put it down
     as they do every window (`app.js`). */
  if (!sheet.open) sheet.showModal();
}

async function openCard(popup, cardId, { tab = "card", storyId = null, naming = false } = {}) {
  /* One card, read whole and drawn as a sheet over the panel: its Card
     tab — pictures, fields, the stories it is in — and its Journals tab,
     a story's entries at a time. `naming`, its name open in its editor,
     selected whole: a card just made, to be named. */
  const [card, app] = await Promise.all([api.cards.get(cardId), api.app.get()]);
  if (card.refused) {
    if (popup.open) report(card.notice);
    return card;
  }
  const sheet = sheetOf("card");
  const opening = !sheet.open;
  /* Drawn again where it stands — the same card, still open — its
     pictures are kept as they are where the card's stand as they did, so
     none loads again, and the columns keep their scroll. */
  const again = !opening && state.shown === card.id;
  const shownPictures = again ? $(".otk-pics", sheet) : null;
  const keptPictures =
    shownPictures?._card && samePictures(shownPictures._card, card) ? shownPictures : null;
  const scrolled = again ? $$(_SCROLLERS, sheet).map((scroller) => scroller.scrollTop) : [];
  closeSheets(sheet);
  // A generation's history is the open window's, and one card's.
  if (state.generations?.cardId !== card.id) state.generations = null;
  state.shown = card.id;
  state.canSee = app.model?.capabilities?.vision === true;
  if (opening) {
    asksBeforeClosing(sheet, () => mayCloseCard(popup));
    sheet.addEventListener(
      "close",
      () => {
        // a generation still running is given up with its window
        if (state.generating !== null) api.cards.generation.delete(state.generating).catch(() => {});
        state.shown = null;
        state.generations = null;
      },
      { once: true },
    );
  }
  sheet.setAttribute("aria-label", card.name);

  const head = element("div", "otk-sheet__head otk-sheet__head--card");
  const name = editable("otk-detail__title", {
    text: card.name,
    line: true,
    save: (text) => saveCard(popup, card, { name: text }),
  });
  name.rows = 1;
  // the name needs no label: it is the sheet's title
  const titled = edited(name, "", "otk-edit--title");
  head.append(titled, ...headLines(popup, card), closeButton(sheet));

  const tabs = element("div", "otk-tabs otk-tabs--sheet");
  tabs.setAttribute("role", "tablist");
  const cardTab = tabButton("Card", "card");
  const journalsTab = tabButton("Journals", "journals");
  tabs.append(cardTab, journalsTab);

  const body = element("div", "otk-sheet__body");
  body.dataset.pane = "card";
  body.append(keptPictures ?? pictures(popup, card), fields(popup, card));

  const journals = element("div", "otk-sheet__journals");
  journals.dataset.pane = "journals";

  /* What the card's actions answer is said in its own foot, the panel's
     being under the scrim; a card drawn again where it stands keeps the
     foot it had, and the sentence or the error standing in it. */
  const foot = (sheet.open && $(".otk-sheet__foot", sheet)) || element("div", "otk-sheet__foot");
  const note = $("[data-note]", foot) ?? span("otk-sheet__note");
  note.dataset.note = "";
  foot.replaceChildren(
    exportButton(),
    note,
    actionButton("Merge into…", { onclick: guard(() => mergeCard(popup, card)) }),
    actionButton("Delete", { kind: "otk-btn--danger", onclick: guard(() => deleteCard(popup, card)) }),
  );

  const select = (which, story = null) => {
    cardTab.setAttribute("aria-selected", String(which === "card"));
    journalsTab.setAttribute("aria-selected", String(which === "journals"));
    body.hidden = which !== "card";
    journals.hidden = which !== "journals";
    if (which === "journals") guard(() => drawJournals(popup, card, journals, story))();
  };
  cardTab.onclick = () => select("card");
  journalsTab.onclick = () => select("journals");
  body.addEventListener("click", (event) => {
    const row = event.target.closest("[data-journal]");
    if (row) select("journals", Number(row.dataset.journal));
  });
  sheet.replaceChildren(head, tabs, body, journals, foot);
  showSheet(sheet);
  select(tab, storyId);
  // Opened at its top: the window is one element, and a browser keeps the
  // scroll a closed element had, and scrolls to what opening focused.
  if (opening) for (const scroller of [sheet, ...$$(_SCROLLERS, sheet)]) scroller.scrollTop = 0;
  else if (again) $$(_SCROLLERS, sheet).forEach((scroller, at) => (scroller.scrollTop = scrolled[at] ?? 0));
  if (naming) {
    $(".otk-edit__verb--edit", titled).click();
    name.select();
  }
}

function headLines(popup, card) {
  /* The head's lines after the name, each edited where it is read: the
     tagline, and the aliases at the end with "aka" before them, which
     stays as they open — typed as one line, a comma between two. Either,
     cut short, says the whole of it under the pointer. An empty one stands
     last among the fields instead (`fields`). */
  const oneLine = (text, save) => {
    const field = editable("", { text, line: true, save });
    field.rows = 1;
    field._absent = "(empty)";
    return field;
  };
  const whole = (read) =>
    read.addEventListener("pointerenter", () => {
      if (read.scrollWidth > read.clientWidth) read.title = read.textContent;
      else read.removeAttribute("title");
    });
  const lines = [];
  if (card.tagline.trim()) {
    const tagline = oneLine(card.tagline, (value) => saveCard(popup, card, { tagline: value }));
    const tag = edited(tagline, "", "otk-sheet__tag");
    whole($(".otk-typeset", tag));
    lines.push(tag);
  }
  lines.push(span("otk-push"));
  if (card.aliases.length) {
    const aliases = oneLine(card.aliases.join(", "), (value) =>
      saveCard(popup, card, { aliases: aliasesIn(value) }),
    );
    const read = element("p", `${aliases.className} otk-typeset`);
    whole(read);
    const reader = element("div", "otk-reader");
    reader._repaint = () => {
      const bare = !aliases.value.trim();
      read.textContent = bare ? aliases._absent : aliases.value;
      read.classList.toggle("otk-absent", bare);
    };
    reader.append(span("", "aka"), read, aliases);
    lines.push(edited(reader, "", "otk-sheet__aka"));
  }
  return lines;
}

function aliasesIn(value) {
  // the aliases as typed in one line, a comma between two
  return value
    .split(/[·,\n]/)
    .map((alias) => alias.trim())
    .filter(Boolean);
}

async function saveCard(popup, card, fields) {
  /* A field saved: the card as it now stands replaces the one drawn, and
     the lists are read again — a name, a tagline, an alias show there
     too. The answer goes back to the editor, which keeps itself open on
     a refusal. */
  const answer = await api.cards.update(card.id, fields);
  if (!answer.refused) {
    Object.assign(card, answer.value);
    // a hand edit of a generated field changes the version it stands at
    const versions = state.generations;
    if (versions?.cardId === card.id && _GENERATED.some((field) => field in fields)) {
      versions.states[versions.at] = generatedFields(card);
    }
    await redraw(popup, answer);
    sheetOf("card").setAttribute("aria-label", card.name);
  }
  return answer;
}

// ---------- a card written from its pictures ----------

function generationBar(popup, card) {
  /* The fields' bar, under them: once there is a first generation, the
     versions — the original and each run, a hand edit changing the one it
     is made on — the arrows stepping through them and a label naming
     which the fields now show; and Generate card from picture,
     off while the model in use cannot see or the card has no picture —
     why, over the bar, since an off button takes no pointer. While the
     model writes the button is Cancel, its size kept — both captions in
     it, the stylesheet showing one — and the time runs where the versions
     stand. */
  const bar = element("div", "otk-gen");
  const busy = state.generating === card.id;
  if (busy) bar.dataset.busy = "";
  const reason = !state.canSee ? _CANNOT_SEE : card.pictures.length ? "" : _NO_PICTURE;
  bar.title = reason;
  const steps = element("span", "otk-gen__steps");
  const versions = state.generations?.cardId === card.id ? state.generations : null;
  steps.hidden = !versions || busy;
  if (versions) {
    steps.append(
      versionStep("Previous version", _PREVIOUS, busy || versions.at === 0, () =>
        stepGeneration(popup, card, -1),
      ),
      versionLabel(versions),
      versionStep("Next version", _NEXT, busy || versions.at === versions.states.length - 1, () =>
        stepGeneration(popup, card, 1),
      ),
    );
  }
  bar.append(steps);
  if (busy) bar.append(span("otk-gen__elapsed", elapsed(state.generatingSince)));
  bar.append(generateButton(popup, card, { busy, off: !busy && Boolean(reason) }));
  return bar;
}

function generateButton(popup, card, { busy, off }) {
  /* Generate card from picture, split — "pictures" where the card has
     several, all of them sent: its main part generates in the language
     in force, its code beside the caption; its arrow opens the menu of
     languages — the browser's own at the top where it is none of the
     menu's — and a pick generates at once and is the language from then
     on. While the model writes, the main part is Cancel and the arrow
     takes nothing. */
  const language = languageOf(readLanguage());
  const go = actionButton("", {
    kind: "otk-swap",
    off,
    onclick: guard(() => (busy ? cancelGeneration(card) : generate(popup, card, language))),
  });
  const idle = span(
    "otk-swap__idle",
    card.pictures.length > 1 ? "Generate card from pictures" : "Generate card from picture",
  );
  idle.append(span("otk-split__code", language.code.toUpperCase()));
  go.replaceChildren(idle, span("otk-swap__busy", "Cancel"));
  const menu = choiceMenu("Generate in", (picked) => {
    keep(_LANGUAGE_KEY, picked);
    split._closeMenu();
    guard(() => generate(popup, card, languageOf(picked)))();
  });
  const browser = browserLanguage();
  if (browser && !_LANGUAGES.some(([code]) => code === browser.code)) {
    menu._add(browser.code, browser.own, browser.code === language.code, "browser");
    menu.append(span("otk-langs__sep"));
  }
  for (const each of menuLanguages()) menu._add(each.code, each.own, each.code === language.code);
  const split = splitButton(go, menu, { label: "Language", off });
  split.dataset.lang = language.code;
  return split;
}

function exportButton() {
  /* Export, split: the main part exports in the format last picked, its
     code beside the caption; the arrow opens the formats, and a pick
     exports at once and is the format from then on. Export itself is
     not built yet: a click keeps the pick and does nothing more. */
  const format = readFormat();
  const go = actionButton("Export");
  go.append(span("otk-split__code", format.toUpperCase()));
  const menu = choiceMenu("Export as", (picked) => {
    keep(_EXPORT_KEY, picked);
    split._closeMenu();
    const shown = $(".otk-split__code", go);
    shown.textContent = picked.toUpperCase();
    split.dataset.format = picked;
    for (const row of $$(".otk-langs__opt", menu)) {
      row.setAttribute("aria-checked", String(row.dataset.code === picked));
    }
  });
  menu.classList.add("otk-langs--wide");
  for (const [code, name] of _EXPORT_FORMATS) menu._add(code, name, code === format);
  const split = splitButton(go, menu, { label: "Format" });
  split.classList.add("otk-split--export");
  split.dataset.format = format;
  return split;
}

function readFormat() {
  // the export format last picked, kept in this browser; else the first
  try {
    const kept = localStorage.getItem(_EXPORT_KEY);
    if (_EXPORT_FORMATS.some(([code]) => code === kept)) return kept;
  } catch {
    // nothing kept: the first format
  }
  return _EXPORT_FORMATS[0][0];
}

function splitButton(go, menu, { label, off = false }) {
  /* A split button: `go` acts with the choice in force, the arrow beside
     it — named `label` — opens `menu` under the button, or over it where
     there is no room below (`openMenu`); a click elsewhere or Esc puts
     the menu down, and only the menu, the window staying. A pick puts it
     down through the split's `_closeMenu`. */
  const split = span("otk-split");
  go.classList.add("otk-split__go");
  const arrow = element("button", "otk-btn otk-split__menu");
  arrow.type = "button";
  arrow.disabled = off;
  arrow.setAttribute("aria-label", label);
  arrow.setAttribute("aria-haspopup", "menu");
  arrow.setAttribute("aria-expanded", "false");
  const glyph = svgElement("svg", { width: "10", height: "10", viewBox: "0 0 10 10", "aria-hidden": "true" });
  glyph.append(svgElement("path", { d: "M1.5 3.5 5 7 8.5 3.5" }));
  arrow.append(glyph);
  const outside = (event) => {
    if (!split.contains(event.target)) closeMenu();
  };
  const closeMenu = () => {
    menu.hidden = true;
    arrow.setAttribute("aria-expanded", "false");
    document.removeEventListener("pointerdown", outside, true);
  };
  split._closeMenu = closeMenu;
  arrow.onclick = () => {
    if (!menu.hidden) {
      closeMenu();
      return;
    }
    openMenu(split, menu);
    arrow.setAttribute("aria-expanded", "true");
    document.addEventListener("pointerdown", outside, true);
    $('[aria-checked="true"]', menu)?.focus();
  };
  menu.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    event.preventDefault();
    event.stopPropagation();
    closeMenu();
    arrow.focus();
  });
  split.append(go, arrow, menu);
  return split;
}

function choiceMenu(head, onpick) {
  /* A split button's menu under its `head`, the choice in force ticked;
     its `_add` puts a row in — a code, a name, a note after it — and a
     row taken hands its code to `onpick`. */
  const menu = span("otk-langs");
  menu.hidden = true;
  menu.setAttribute("role", "menu");
  menu.setAttribute("aria-label", head);
  menu.append(span("otk-langs__head", head));
  menu._add = (code, name, checked, note = "") => {
    const row = element("button", "otk-langs__opt");
    row.type = "button";
    row.setAttribute("role", "menuitemradio");
    row.setAttribute("aria-checked", String(checked));
    row.dataset.code = code;
    row.append(
      span("otk-langs__code", code.toUpperCase()),
      span("otk-langs__name", name),
      ...(note ? [span("otk-langs__note", note)] : []),
      span("otk-langs__check", "\u2713"),
    );
    row.onclick = () => onpick(code);
    menu.append(row);
  };
  return menu;
}

function openMenu(split, menu) {
  /* The menu shown under the button — or over it, where the column it
     scrolls in has no room below and has some above (`data-up`). */
  delete menu.dataset.up;
  menu.hidden = false;
  let clip = split.parentElement;
  while (clip && clip !== document.body && !/(auto|scroll|hidden)/.test(getComputedStyle(clip).overflowY)) {
    clip = clip.parentElement;
  }
  const box = clip && clip !== document.body ? clip.getBoundingClientRect() : null;
  const bottom = box ? Math.min(box.bottom, innerHeight) : innerHeight;
  const top = box ? Math.max(box.top, 0) : 0;
  const at = split.getBoundingClientRect();
  const room = menu.offsetHeight + 6;
  if (at.bottom + room > bottom && at.top - room >= top) menu.dataset.up = "";
}

function menuLanguages() {
  return _LANGUAGES.map(([code, own, english]) => ({ code, own, english }));
}

function browserLanguage() {
  // the browser's own language, where the menu can name it; null where not
  const code = (navigator.language || "").split("-")[0].toLowerCase();
  const listed = _LANGUAGES.find(([each]) => each === code);
  if (listed) return { code, own: listed[1], english: listed[2] };
  const more = _BROWSER_LANGUAGES[code];
  return more ? { code, own: more[0], english: more[1] } : null;
}

function readLanguage() {
  /* The language in force: the last picked, kept in this browser; the
     first time, the browser's own where the menu has it, else English. */
  try {
    const kept = localStorage.getItem(_LANGUAGE_KEY);
    if (kept) return kept;
  } catch {
    // nothing kept: the browser decides
  }
  return browserLanguage()?.code ?? "en";
}

function languageOf(code) {
  // a language by its code, among the menu's and the browser's own
  const browser = browserLanguage();
  if (browser?.code === code) return browser;
  return menuLanguages().find((language) => language.code === code) ?? menuLanguages()[0];
}

function elapsed(since) {
  // A generation's time so far, as m:ss.
  const seconds = Math.floor((performance.now() - since) / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

async function cancelGeneration(card) {
  /* The generation given up — nothing written — the sentence said in the
     card's foot; the generation's own answer, its refusal, then said
     nowhere. */
  state.cancelled = true;
  const answer = await api.cards.generation.delete(card.id);
  footnote(sheetOf("card"), answer.notice, Boolean(answer.refused));
}

function versionStep(label, paths, off, onclick) {
  // One of the versions' arrows: an icon named for a screen reader.
  const button = element("button", "otk-gen__step");
  button.type = "button";
  button.disabled = off;
  button.setAttribute("aria-label", label);
  button.title = label;
  const icon = svgElement("svg", { width: "16", height: "16", viewBox: "0 0 24 24", "aria-hidden": "true" });
  icon.append(...paths.map((d) => svgElement("path", { d })));
  button.append(icon);
  button.onclick = guard(onclick);
  return button;
}

function drawGenerationBar(popup, card) {
  // The bar alone drawn again where it stands: the rest of the window, its
  // scroll and an editor open in it, as they were.
  $(".otk-gen", sheetOf("card"))?.replaceWith(generationBar(popup, card));
}

async function generate(popup, card, language) {
  /* The card written by the model in use from its pictures, in
     `language`, the bar busy meanwhile, its time running: noted as a
     version — after the card as it stood before, the first time — and the
     card drawn again, where the window still shows it. */
  const before = generatedFields(card);
  state.generating = card.id;
  state.generatingSince = performance.now();
  state.cancelled = false;
  drawGenerationBar(popup, card);
  const clock = setInterval(() => {
    const time = $(".otk-gen__elapsed", sheetOf("card"));
    if (time) time.textContent = elapsed(state.generatingSince);
  }, _CLOCK_MS);
  let answer;
  try {
    answer = await api.cards.generation.create(card.id, { language: language.english });
  } finally {
    clearInterval(clock);
    state.generating = null;
  }
  if (state.shown !== card.id) return;
  if (answer.refused) {
    drawGenerationBar(popup, card);
    if (state.cancelled) return; // the cancel said what happened
  } else {
    Object.assign(card, answer.value);
    noteGeneration(card.id, before, generatedFields(card));
    await openCard(popup, card.id);
    showFace(popup, card);
  }
  footnote(sheetOf("card"), answer.notice, Boolean(answer.refused));
}

async function stepGeneration(popup, card, by) {
  /* The previous version (-1) or the next (+1): the generated fields
     written back as it holds them — those that differ alone, through the
     card's own update — and the card drawn again, its foot quiet. */
  const steps = state.generations;
  const wanted = steps.states[steps.at + by];
  const changes = Object.fromEntries(
    Object.entries(wanted).filter(([field, value]) => card[field] !== value),
  );
  let answer = null;
  if (Object.keys(changes).length) {
    answer = await api.cards.update(card.id, changes);
    if (answer.refused) {
      report(answer.notice);
      return;
    }
    Object.assign(card, answer.value);
  }
  steps.at += by;
  await openCard(popup, card.id);
  if (answer) showFace(popup, card);
  // the bar names the version the card now stands at: the foot says nothing
  footnote(sheetOf("card"), "");
}

function versionLabel(versions) {
  /* The name of the version the fields show, as wide as the longest name
     among the card's versions — fixed while the reader steps through
     them, so the arrows on either side never move, and no wider, so they
     stand close to it. */
  const label = span("otk-gen__count", versionName(versions));
  const names = versions.states.map((_, at) => versionName({ ...versions, at }));
  label.style.minWidth = `${Math.max(...names.map((name) => name.length))}ch`;
  return label;
}

function versionName(versions) {
  // Which version the fields show: the original, or a generation —
  // numbered where there are several.
  if (versions.at === 0) return "original";
  return versions.states.length > 2 ? `generated ${versions.at}` : "generated";
}

async function mayCloseCard(popup) {
  /* Whether the card's window may be put down: at once, unless the card
     stands at a generation — then the reader says whether to keep it
     (Save), put the card back as it stood before the first generation
     (Revert), or stay with it (Close, Esc), its versions still there. */
  const versions = state.generations;
  if (!versions || versions.cardId !== state.shown || versions.at === 0) return true;
  if (state.asking) return false;
  state.asking = true;
  let choice;
  try {
    choice = await ask("keep-generated");
  } finally {
    state.asking = false;
  }
  if (choice === "save") return true;
  if (choice !== "revert") return false;
  const [original, standing] = [versions.states[0], versions.states[versions.at]];
  const changes = Object.fromEntries(
    Object.entries(original).filter(([field, value]) => standing[field] !== value),
  );
  if (!Object.keys(changes).length) return true;
  const answer = await api.cards.update(versions.cardId, changes);
  if (answer.refused) {
    report(answer.notice);
    return false;
  }
  showFace(popup, answer.value);
  return true;
}

function noteGeneration(cardId, before, after) {
  /* A generation, noted as a version at the end — every one kept,
     whichever the card stood at — the versions opened with the card as
     it stood `before`, the first time; the card stands at the new one. */
  if (state.generations?.cardId !== cardId) {
    state.generations = { cardId, states: [before], at: 0 };
  }
  const versions = state.generations;
  versions.states = [...versions.states, after];
  versions.at = versions.states.length - 1;
}

function showFace(popup, card) {
  /* What the library behind the window shows of a card — its name and
     tagline, on its tile and its row — set where they stand, without
     asking for the list again: a generation and a step through the
     versions change those alone. */
  for (const view of ["gallery", "list"]) {
    const face = state.faces[view].get(card.id);
    if (!face) continue;
    Object.assign(face.card, { name: card.name, aliases: card.aliases, tagline: card.tagline });
    setFace(face.element, face.card);
  }
}

function generatedFields(card) {
  // The fields a generation writes, as the card holds them.
  return Object.fromEntries(_GENERATED.map((field) => [field, card[field]]));
}

function pictures(popup, card) {
  /* The card's pictures: the one shown large, the strip under it — a
     thumb picks, a thumb dragged reorders, the first is the main one —
     and the two ways in: + to upload, images dropped. Built once for the
     pictures the card holds — a write that changes them draws the card
     again — the one shown slid across for the next (`slideIn`). */
  const box = element("div", "otk-pics");
  // what it was built for, so a redraw of the same card can keep it
  box._card = card;
  const count = card.pictures.length;
  let at = 0;
  const main = element("div", "otk-pics__main");
  const large = (i) => {
    const image = element("img");
    image.alt = "";
    image.src = api.pictures.url(card.pictures[i].file);
    image.onclick = () => openPicture(card.pictures, at);
    return image;
  };
  const shown = span("otk-tile__count");
  // the shown picture's cross, in its corner
  const remove = element("button", "otk-pics__remove", "\u2715");
  remove.type = "button";
  remove.setAttribute("aria-label", "Remove this picture");
  remove.onclick = guard(() => removePicture(popup, card, at));
  // flipped through as a large tile is
  const arrows = flipButtons(count, (to) => show(to));
  const thumbs = card.pictures.map((picture, i) => {
    const thumb = element("button", "otk-pics__thumb");
    thumb.type = "button";
    thumb.draggable = true;
    thumb.dataset.index = String(i);
    thumb.setAttribute("aria-label", `picture ${i + 1}`);
    const small = element("img");
    small.alt = "";
    small.src = api.pictures.thumbnail.url(picture.file);
    thumb.append(small);
    thumb.onclick = () => show(i);
    return thumb;
  });
  const show = (to) => {
    const by = to - at;
    at = to;
    if (by) guard(() => slideIn(main, large(at), by))();
    shown.textContent = `${at + 1} / ${count}`;
    arrows.mark(at);
    thumbs.forEach((thumb, i) => thumb.setAttribute("aria-current", String(i === at)));
  };
  const add = element("button", "otk-pics__add", "+");
  add.type = "button";
  add.setAttribute("aria-label", "Upload pictures");
  add.onclick = guard(() => addPictures(popup, card.id));
  if (count) {
    main._shown = large(0);
    // the count and the arrows only where there is more than one
    main.append(main._shown, ...(count > 1 ? [shown, ...arrows.buttons] : []), remove);
    show(0);
  }
  const strip = element("div", "otk-pics__thumbs");
  strip.append(...thumbs, add);
  reorderable(strip, thumbs, _THUMB, (order) =>
    reopenAfter(
      popup,
      card,
      api.cards.update(card.id, { pictures: order.map((i) => card.pictures[i].file) }),
    ),
  );
  filesDropped(box, (files) => addPictures(popup, card.id, files));
  box.append(
    main,
    strip,
    span("otk-pics__hint", "drop images here, or + to upload · drag to reorder"),
  );
  return box;
}

/** Items put in order by dragging one among the others — a card's
    thumbnails, the cards in the gallery and in the list: while it moves,
    it stands, faded, where it would land, the others gliding aside to make
    way. Let go in `container`, `reorder(order, from)` is handed the new
    order, as the items' first indices, and the index of the one dragged
    — the order they stand in from then on; let go anywhere else, or given
    up, they go back to where they stood, ahead of whatever else the
    container holds. A drag of anything else, told apart by its `type`, is
    left to its own handlers. One wiring per container: drawn again, the
    last one's listeners go. */
function reorderable(container, items, type, reorder) {
  const signal = wiring(container);
  let dragged = null;
  let dropped = false;
  let settled = items.map((_, index) => index);
  // an item still gliding is not one the pointer is over yet
  const gliding = new Set();
  const making = (move) => {
    const calm = matchMedia(_CALM).matches;
    const was = new Map(items.map((item) => [item, item.getBoundingClientRect()]));
    move();
    if (calm) return;
    for (const item of items) {
      if (item === dragged) continue;
      const before = was.get(item);
      const after = item.getBoundingClientRect();
      const [dx, dy] = [before.left - after.left, before.top - after.top];
      if (!dx && !dy) continue;
      gliding.add(item);
      const done = () => gliding.delete(item);
      item
        .animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }], _MAKE_WAY)
        .finished.then(done, done);
    }
  };
  const carries = (event) => dragged && event.dataTransfer.types.includes(type);
  // each item's listeners go with the container's wiring: an item kept
  // across a redraw (`drawView`) is wired once, for the order it is in now
  for (const item of items) {
    item.addEventListener(
      "dragstart",
      (event) => {
        event.dataTransfer.setData(type, String(items.indexOf(item)));
        event.dataTransfer.effectAllowed = "move";
        dragged = item;
        item.classList.add("is-dragging");
      },
      { signal },
    );
    item.addEventListener(
      "dragend",
      () => {
        item.classList.remove("is-dragging");
        const order = [...container.children]
          .filter((child) => items.includes(child))
          .map((child) => items.indexOf(child));
        const kept = dropped && order.some((index, at) => index !== settled[at]);
        const from = items.indexOf(item);
        dragged = null;
        dropped = false;
        if (kept) {
          settled = order;
          guard(() => reorder(order, from))();
        } else container.prepend(...settled.map((index) => items[index]));
      },
      { signal },
    );
    // over another, it takes that one's place: after one that came after
    // it, before one that came before
    item.addEventListener(
      "dragover",
      (event) => {
        if (!carries(event)) return;
        event.preventDefault();
        if (item === dragged || gliding.has(item)) return;
        const places = [...container.children];
        const later = places.indexOf(dragged) < places.indexOf(item);
        making(() => (later ? item.after(dragged) : item.before(dragged)));
      },
      { signal },
    );
  }
  // the container itself takes the drop, so a gap between items keeps it
  container.addEventListener(
    "dragover",
    (event) => {
      if (carries(event)) event.preventDefault();
    },
    { signal },
  );
  container.addEventListener(
    "drop",
    (event) => {
      if (!carries(event)) return;
      event.preventDefault();
      dropped = true;
    },
    { signal },
  );
}

async function addPictures(popup, cardId, files = null) {
  /* Each picture through the one intake, one at a time; the first
     refused stops the rest and says why. The card is drawn again from
     what stands. */
  const taken = files ?? (await pickFiles("image/*"));
  if (!taken.length) return;
  const answer = await sendEach(taken, (encoded) => api.cards.pictures.create(cardId, encoded));
  await redraw(popup, answer);
  if (sheetOf("card").open) await openCard(popup, cardId);
}

async function removePicture(popup, card, at) {
  const picture = card.pictures[at];
  if (!picture) return;
  const choice = await askConfirm({
    title: "Remove this picture?",
    body: `It goes from ${card.name}'s pictures. A story it was sent to keeps its copy.`,
    action: "Remove",
  });
  if (choice !== "confirm") return;
  await reopenAfter(popup, card, api.cards.pictures.delete(card.id, picture.file));
}

async function reopenAfter(popup, card, write) {
  const answer = await write;
  await redraw(popup, answer);
  await openCard(popup, card.id);
}

/** A card's own fields — a codex card's, or a story character's copy of
    one — each edited where it is read, in the card's order: a text read as
    the Markdown it is written in, an empty one as "(empty)", a list edited as
    one text, its items between lines of three dashes, and the reminder's
    depth as a whole number.
    `save(changes)` writes one field and answers what the backend did. */
export function cardFieldEditors(values, save, className = "otk-passage__body") {
  return _FIELDS.map(([key, name, kind]) => {
    const write =
      kind === "list"
        ? (value) =>
            value
              .split(_ITEMS)
              .map((item) => item.trim())
              .filter(Boolean)
        : kind === "number"
          ? // what is no whole number goes as 0, for the backend's refusal
            (value) => (/^\s*\d+\s*$/.test(value) ? Number(value) : 0)
          : (value) => value;
    const text =
      kind === "list"
        ? values[key].join("\n\n---\n\n")
        : kind === "number"
          ? String(values[key])
          : values[key];
    return fieldEditor(
      className,
      name,
      { text, line: kind === "number", save: (value) => save({ [key]: write(value) }) },
      { rich: kind !== "number" },
    );
  });
}

function fieldEditor(className, name, options, { rich = false } = {}) {
  /* One field edited where it is read (`editable`, `options` its own),
     its name over it and an empty one reading "(empty)" — `rich`, read as
     Markdown (`kit/markdown.js`) and edited as the text it is. */
  const input = editable(className, options);
  // said while closed alone: opened, the field stands empty
  input._absent = "(empty)";
  if (!rich) return edited(input, name, "", { headed: true });
  const read = element("div", `${input.className} otk-typeset otk-md`);
  const both = element("div", "otk-reader");
  // what is read once the field closes, saved or discarded (`edited`)
  both._repaint = () => {
    const bare = !input.value.trim();
    read.classList.toggle("otk-absent", bare);
    read.replaceChildren(...(bare ? [input._absent] : markdown(input.value)));
  };
  both.append(read, input);
  return edited(both, name, "", { headed: true });
}

function fields(popup, card) {
  /* The card's own fields in order, each edited where it is read — an
     empty one reads "(empty)" — then the tagline and the aliases while
     they are empty, the head holding them once they are not
     (`headLines`), and the bar that generates them under them. The
     stories it is in close the column, each row a door to its journals. */
  const box = element("div", "otk-sheet__fields");
  box.append(...cardFieldEditors(card, (changes) => saveCard(popup, card, changes)));
  if (!card.tagline.trim()) {
    box.append(
      fieldEditor("otk-passage__body", "tagline", {
        text: "",
        line: true,
        save: (value) => saveCard(popup, card, { tagline: value }),
      }),
    );
  }
  if (!card.aliases.length) {
    box.append(
      fieldEditor("otk-passage__body", "aliases", {
        text: "",
        line: true,
        save: (value) => saveCard(popup, card, { aliases: aliasesIn(value) }),
      }),
    );
  }
  box.append(generationBar(popup, card));
  const stories = element("div", "otk-v");
  const n = card.stories.length;
  stories.append(span("otk-label otk-sheet__section", n ? `in ${n} ${n === 1 ? "story" : "stories"}` : "not in a story"));
  for (const story of card.stories) {
    const row = element("button", "otk-linkrow");
    row.type = "button";
    row.dataset.journal = String(story.story_id);
    row.append(
      span("otk-linkrow__name", label(story.story_label) || "(untitled)"),
      span("otk-linkrow__count", scenesLabel(story.journal_count)),
    );
    stories.append(row);
  }
  box.append(stories);
  return box;
}

async function drawJournals(popup, card, pane, storyId) {
  /* A story at a time: the stories the card is in on the left, the
     chosen one's entries on the right — newest first, the scene each was
     written in, the state under it — and the way to unlink it there. */
  const stories = card.stories;
  if (!stories.length) {
    pane.replaceChildren(empty("Not in a story yet.", "link it from a story's cast, or start one with it"));
    return;
  }
  const chosen = stories.find((story) => story.story_id === storyId) ?? stories[0];
  const left = element("div", "otk-sheet__stories");
  const list = element("div", "otk-sheet__storylist");
  for (const story of stories) {
    const row = element("button", "otk-jstory");
    row.type = "button";
    row.setAttribute("aria-selected", String(story === chosen));
    row.append(
      span("otk-jstory__name", label(story.story_label) || "(untitled)"),
      span("otk-index__sub", scenesLabel(story.journal_count)),
    );
    row.onclick = guard(() => drawJournals(popup, card, pane, story.story_id));
    list.append(row);
  }
  const pinned = element("div", "otk-sheet__pinned");
  const filter = element("input", "otk-filter");
  filter.type = "search";
  filter.placeholder = "filter journals";
  filter.setAttribute("aria-label", "Filter journals");
  pinned.append(filter);
  left.append(list, pinned);

  const right = element("div", "otk-sheet__entries");
  const unlink = actionButton("Unlink", {
    kind: "otk-btn--quiet",
    onclick: guard(async () => {
      const choice = await askConfirm({
        title: `Unlink ${chosen.name} from ${card.name}?`,
        body: `${chosen.name} in "${storyName(chosen)}" keeps its profile; drop it on the card to relink.`,
        action: "Unlink",
      });
      if (choice !== "confirm") return;
      const answer = await api.stories.characters.update(chosen.story_id, chosen.character_id, {
        card_id: null,
      });
      await redraw(popup, answer);
      await openCard(popup, card.id, { tab: "journals" });
    }),
  });
  const storyhead = element("div", "otk-sheet__storyhead");
  storyhead.append(
    span("otk-sheet__storyname", label(chosen.story_label) || "(untitled)"),
    span("otk-index__sub", `as ${chosen.name}`),
    unlink,
  );
  const passages = await entries(chosen.story_id, chosen.character_id);
  const paint = () => {
    const needle = filter.value.trim().toLowerCase();
    const shown = passages.filter((each) => !needle || each.text.includes(needle));
    right.replaceChildren(
      storyhead,
      ...(shown.length
        ? shown.map((each) => each.node)
        : [empty(needle ? "No entry matches that." : "No journal entries yet.")]),
    );
  };
  filter.addEventListener("input", paint);
  paint();
  pane.replaceChildren(left, right);
}

async function entries(storyId, characterId) {
  /* A story character's journal entries as passages, newest first: the
     scene's title and number for a rubric, the entry, the state under
     it — each with the text a filter looks in. Read from the story's own
     memory, the one the dossier draws. */
  const lore = await api.stories.lore.get(storyId);
  if (lore.refused) return [];
  const scenes = new Map(lore.scenes.map((scene) => [scene.id, scene]));
  const records = lore.journals
    .filter((record) => record.character_id === characterId)
    .sort((a, b) => (scenes.get(b.scene_id)?.no ?? 0) - (scenes.get(a.scene_id)?.no ?? 0));
  return records.map((record, i) => {
    const scene = scenes.get(record.scene_id);
    const rubric = `${scene?.title || "(untitled scene)"} · scene ${scene?.no ?? "?"}${i === 0 ? " · latest" : ""}`;
    const node = element("div", "otk-passage");
    node.append(span("otk-passage__rubric", rubric), element("p", "otk-passage__body", record.entry));
    if (record.state) node.append(element("p", "otk-note", record.state));
    return { node, text: `${rubric} ${record.entry} ${record.state}`.toLowerCase() };
  });
}

async function mergeCard(popup, card) {
  const picked = await pickCard({ title: `Merge ${card.name} into…`, exclude: card.id });
  if (!picked) return;
  const answer = await api.cards.merges.create(card.id, { into: picked.card.id });
  await redraw(popup, answer);
  await openCard(popup, answer.refused ? card.id : picked.card.id);
}

async function deleteCard(popup, card) {
  const choice = await askConfirm({
    title: `Delete ${card.name}?`,
    body: "The card and its pictures go from Characters. Its characters in stories stay, unlinked.",
    action: "Delete",
  });
  if (choice !== "confirm") return;
  const answer = await api.cards.delete(card.id);
  if (!answer.refused) {
    closeSheets();
    state.picked = state.picked.filter((id) => id !== card.id);
  }
  await redraw(popup, answer);
}

// ---------- an unlinked story character ----------

async function openUnlinked(popup, row) {
  /* A story character read before it is linked: its journal entries, and
     the two doors a drag stands for — a card to link it to, or a new
     card made of it. */
  const sheet = sheetOf("unlinked");
  closeSheets(sheet);
  sheet.setAttribute("aria-label", row.name);
  const head = element("div", "otk-sheet__head");
  head.append(
    element("h3", "otk-detail__title", row.name),
    span(
      "otk-index__sub",
      `in ${label(row.story_label) || "(untitled)"} · ${journalsLabel(row.journal_count)}`,
    ),
    span("otk-push"),
    closeButton(sheet),
  );
  const body = element("div", "otk-sheet__entries otk-sheet__entries--solo");
  const passages = await entries(row.story_id, row.character_id);
  body.replaceChildren(...(passages.length ? passages.map((each) => each.node) : [empty("No journal entries yet.")]));
  const foot = element("div", "otk-sheet__foot otk-sheet__foot--hint");
  const hint = span("", "drag onto a card to link · onto ");
  hint.append(element("b", "", "new card"), " to make one");
  // the foot's words, which an error stands over
  hint.dataset.note = "";
  foot.append(
    actionButton("Link to a card…", {
      onclick: guard(async () => {
        const picked = await pickCard({ title: `Link ${row.name} to…` });
        if (picked) await link(popup, row, picked.card.id);
      }),
    }),
    actionButton("Register as a card", { onclick: guard(() => promote(popup, row)) }),
    hint,
  );
  sheet.replaceChildren(head, body, foot);
  showSheet(sheet);
}

// ---------- a story started with cards ----------

async function startStory(popup) {
  /* The picked cards, in pick order, into a story made for them: its
     title (optional), who the reader plays, and per card with a picture
     whether its portrait goes into the context — off until switched on. */
  const cards = state.picked.map((id) => state.known.get(id)).filter(Boolean);
  if (!cards.length) return;
  const dialog = $('dialog[data-dialog="start"]');
  const title = $("#otk-start-name", dialog);
  const persona = $("#otk-start-persona", dialog);
  const sending = new Set();
  const choice = await ask("start", () => {
    title.value = "";
    const picturedCards = cards.filter((card) => card.pictures.length);
    // the switches' caption, where there are switches
    const caption = $("[data-start-caption]", dialog);
    caption.textContent = "choose which pictures to send to the model";
    caption.hidden = !picturedCards.length;
    const box = $("[data-start-pictures]", dialog);
    box.replaceChildren(
      box.firstElementChild,
      ...picturedCards.map((card) => {
        const line = element("div", "otk-picsend");
        const toggle = switchButton(`Send ${card.name}'s picture to the model`, false);
        toggle.onclick = () => {
          const on = !sending.has(card.id);
          if (on) sending.add(card.id);
          else sending.delete(card.id);
          toggle.setAttribute("aria-checked", String(on));
        };
        line.append(portrait("otk-picsend__thumb", card), span("otk-picsend__text", card.name), toggle);
        return line;
      }),
    );
    // Start answers only with a persona name: it binds {{user}} in every card.
    const signal = wiring(dialog);
    const veto = (event) => {
      if (persona.value.trim()) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      persona.reportValidity();
    };
    $('[data-choice="start"]', dialog).addEventListener("click", veto, { signal });
    dialog.addEventListener(
      "keydown",
      (event) => {
        if (event.key === "Enter" && !event.shiftKey) veto(event);
      },
      { signal },
    );
  });
  if (choice !== "start") return;
  const made = await api.stories.create({
    title: title.value.trim(),
    card_ids: cards.map((card) => card.id),
    card_pictures: [...sending],
    persona: persona.value.trim(),
  });
  if (made.refused) {
    report(made.notice);
    return;
  }
  held.hold(made.value.id);
  state.picked = [];
  closeAll();
  await landed(made.notice, { redraw: "always" });
}

// ---------- the drawing's small parts ----------

function portrait(className, card) {
  /* A card's first picture in a frame of its own size; stripes stand in
     where it has none. */
  const frame = span(className);
  if (card.pictures.length) {
    const image = element("img");
    image.alt = "";
    image.loading = "lazy";
    image.draggable = false; // where it is a row's, the row is what drags
    image.src = api.pictures.thumbnail.url(card.pictures[0].file);
    frame.append(image);
  }
  return frame;
}

function nameAndTag(card) {
  const text = element("span", "otk-charrow__text");
  text.append(span("otk-tile__name", card.name), span("otk-tile__tag", card.tagline));
  return text;
}

/** A picture's two arrows over `count` pictures, each stepping one way
    to the end and no further: the first has no previous, the last no
    next, and a spent arrow does nothing but take its click — so a tile
    behind it never opens. `step(to, by)` shows picture `to`, `by` away;
    `mark(at)` dims the arrows for the one shown, done by a step and by
    whoever else shows one. */
function flipButtons(count, step) {
  let at = 0;
  const arrow = (way, by, name) =>
    navButton(`otk-tile__nav otk-tile__nav--${way}`, name, way, (event) => {
      event.stopPropagation();
      const to = at + by;
      if (to < 0 || to >= count) return;
      step(to, by);
      mark(to);
    });
  const buttons = [arrow("prev", -1, "Previous picture"), arrow("next", 1, "Next picture")];
  const mark = (shown) => {
    at = shown;
    buttons[0].setAttribute("aria-disabled", String(at === 0));
    buttons[1].setAttribute("aria-disabled", String(at === count - 1));
  };
  mark(0);
  return { buttons, mark };
}

/** `frame`'s picture changed to `next`: slid in from the side it lies
    on — `by` below 0 the left — once it has loaded, the one shown
    (`frame._shown`) staying up until then and sliding out the other way.
    A tile's and a card's pictures fill their frames, which keep their
    size. Only the latest asked for is shown, a slide still running is
    finished first, and where the reader asked for less motion the swap
    is instant. */
async function slideIn(frame, next, by) {
  frame._wanted = next;
  try {
    await next.decode();
  } catch {
    // one that cannot be decoded ahead is shown as it loads
  }
  if (frame._wanted !== next) return;
  for (const running of frame._slides ?? []) running.finish();
  const current = frame._shown;
  current.after(next);
  frame._shown = next;
  if (matchMedia(_CALM).matches) {
    current.remove();
    frame._slides = [];
    return;
  }
  const side = by > 0 ? 100 : -100;
  frame._slides = [
    next.animate([{ transform: `translateX(${side}%)` }, { transform: "none" }], _SLIDE),
    // held off the frame until it is gone
    current.animate([{ transform: "none" }, { transform: `translateX(${-side}%)` }], {
      ..._SLIDE,
      fill: "forwards",
    }),
  ];
  const gone = () => current.remove();
  frame._slides[1].finished.then(gone, gone);
}

function navButton(className, name, way, onclick) {
  const button = element("button", className);
  button.type = "button";
  button.setAttribute("aria-label", name);
  const svg = svgElement("svg", { viewBox: "0 0 16 16", width: "14", height: "14", "aria-hidden": "true" });
  svg.append(svgElement("path", { d: _CHEVRONS[way], ...LINE_STROKE }));
  button.append(svg);
  button.onclick = onclick;
  return button;
}

function closeButton(sheet) {
  // Esc puts a sheet down too, as it does every window: named as every close names it
  const button = actionButton("Close ", { onclick: () => putDown(sheet) });
  button.append(span("otk-btn__key", "esc"));
  return button;
}

function tabButton(text, pane) {
  const button = element("button", "otk-tab", text);
  button.type = "button";
  button.setAttribute("role", "tab");
  button.dataset.tab = pane;
  return button;
}

/** A row that opens on a click, and on Enter or Space where it has the
    focus — what a button does, for a box that holds buttons of its own. */
function opens(box, open) {
  box.addEventListener("click", guard(open));
  box.addEventListener("keydown", (event) => {
    if (event.target !== box || (event.key !== "Enter" && event.key !== " ")) return;
    event.preventDefault();
    guard(open)();
  });
}

function empty(line, hint = "") {
  const box = element("div", "otk-empty");
  box.append(span("otk-empty__line", line));
  if (hint) box.append(span("otk-empty__hint", hint));
  return box;
}

function scenesLabel(n) {
  return `${n} ${n === 1 ? "scene" : "scenes"}`;
}

function journalsLabel(n) {
  return `${n} ${n === 1 ? "journal" : "journals"}`;
}
