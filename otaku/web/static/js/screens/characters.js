/* All characters: the codex's cards — the characters the reader keeps
   across stories — as a gallery or a list, the stories' characters no
   card claims beside them, and a card opened as a sheet over both: its
   pictures, its fields, the stories it is in and its journals there.
   And the asks the codex shares with a story's cast: the card picker
   (`pickCard`) — a card to link, to invite, to merge into — and a card
   invited into the story held (`inviteCard`).

   Every write goes out through its endpoint (`api.cards`, a story's
   characters) and what stands after it is read again; the sentence a
   write answers with is the panel's footnote. A story character is
   dragged onto a card to link it — and, where there is no dragging (a
   touch screen, the keyboard), its own sheet has the same two doors. */

import { api } from "../api/index.js";
import {
  ask,
  closeAll,
  edited,
  editable,
  footnote,
  guard,
  popups,
  report,
  wiring,
} from "../kit/browser.js";
import { $, $$, actionButton, element, encodeFile, pickFiles, span } from "../kit/dom.js";
import { label } from "../kit/format.js";
import { openPicture } from "../kit/pictures.js";
import * as held from "../play/held.js";
import { landed } from "../play/shell.js";
import { importCard } from "./transfer.js";

/* Where the view is kept — the list, or the gallery at a tile size —
   this browser's storage, as the theme's pin is; the unlinked column's
   fold beside it. */
const _VIEW_KEY = "otaku-characters-view";
const _FOLD_KEY = "otaku-characters-unlinked";
/* Each gallery view's tile width — app.css's own (`.otk-chars[data-view]`)
   — which a tile's picture is chosen for. */
const _TILE_WIDTHS = { small: 112, mid: 150, large: 220 };
/* A thumbnail's long edge, as the HTTP spec promises it (`getThumbnail`;
   `backend.domain.app.pictures.THUMB_EDGE`). */
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
const _CHEVRON_STROKE = {
  fill: "none",
  stroke: "currentColor",
  "stroke-width": "1.45",
  "stroke-linecap": "round",
  "stroke-linejoin": "round",
};

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
};

/** Open All characters — on the card `cardId` where one is asked. */
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

/** A codex card into the story held: picked, then brought in through
    the card dialog, the codex card where a file would be — or, by the
    picker's Import…, a card file into the story. */
export async function inviteCard() {
  const picked = await pickCard({ importing: true });
  if (!picked) return;
  if (picked.import) return importCard();
  return importCard({ cardId: picked.card.id });
}

/** Which card: a card to link, to invite, to merge into, through the
    one picker (`pick`). `{card}` for the one chosen, `{import: true}` for
    the picker's Import…, null for none. `exclude` leaves a card out (the
    one being merged); `importing` shows Import…. */
export async function pickCard({ title = "Choose a card", exclude = null, importing = false } = {}) {
  const picked = await pick({
    title,
    importing,
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
    screen reader. `{chosen}` for the row chosen,
    `{import: true}` for Import… (shown with `importing`), null for none. */
export async function pick({
  title,
  read,
  none,
  filtering = "filter cards",
  rows: named = "Cards",
  importing = false,
}) {
  const dialog = $('dialog[data-dialog="pick-card"]');
  let chosen = null;
  const choice = await ask("pick-card", () => {
    $("[data-title]", dialog).textContent = title;
    $('[data-choice="import"]', dialog).hidden = !importing;
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
  if (choice === "import") return { import: true };
  if (choice === "choose" && chosen) return { chosen };
  return null;
}

// ---------- the panel ----------

function wireChrome(popup) {
  /* The panel's own controls, wired once per opening: the view switch,
     the fold, Import, the filters, the picked bar — and a click outside
     an open sheet, or Esc, which puts it down before it reaches the
     panel. */
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
  // a card's window on its own has no panel under it to draw: its answer
  // is said on the card
  if (!popup.open) {
    if (answered && sheetOf("card").open) footnote(sheetOf("card"), answered, refused);
    return;
  }
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
  // an answer is said where the reader is looking: on the open card
  const card = sheetOf("card");
  if (answered && card.open && footnote(card, answered, refused)) footnote(popup, standing);
  else footnote(popup, answered || standing, refused);
}

function drawCards(popup) {
  const gallery = $("[data-gallery]", popup);
  const rows = $("[data-rows]", popup);
  const cards = state.cards;
  const tiles = cards.map((card) => tile(popup, card));
  const lines = cards.map((card) => listRow(popup, card));
  // with no card yet, the new card stands alone, first in its place
  const none =
    !cards.length && state.query
      ? empty("No card matches that.", `clear the filter to see all ${state.total.cards}`)
      : null;
  gallery.replaceChildren(...(none ? [none] : []), ...tiles, newCard(popup, "otk-tile otk-tile--new"));
  rows.replaceChildren(
    ...(none ? [none.cloneNode(true)] : []),
    ...lines,
    newCard(popup, "otk-charrow otk-charrow--new"),
  );
  /* Dragged among the others, a card takes its place in the reader's
     order — the whole of it only: under a filter, where the cards between
     are hidden, a place is not one the reader can see. */
  if (!state.query) {
    const moved = (order, from) => moveCard(popup, cards, order, from);
    for (const each of [...tiles, ...lines]) each.draggable = true;
    reorderable(gallery, tiles, _CARD, moved);
    reorderable(rows, lines, _CARD, moved);
  }
  drawPicked(popup);
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
  const ordered = order.map((index) => cards[index]);
  state.cards = ordered;
  const listed = state.view === "list";
  const other = $(listed ? "[data-gallery]" : "[data-rows]", popup);
  const drawn = ordered.map((card) => (listed ? tile(popup, card) : listRow(popup, card)));
  for (const each of drawn) each.draggable = true;
  other.replaceChildren(
    ...drawn,
    newCard(popup, listed ? "otk-tile otk-tile--new" : "otk-charrow otk-charrow--new"),
  );
  reorderable(other, drawn, _CARD, (again, moved) => moveCard(popup, ordered, again, moved));
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
    await importCards(popup, await pickFiles(".png,.json"));
  });
  box.append(
    span("otk-tile--new__plus", "+"),
    span("otk-tile--new__label", "new card"),
    span("otk-tile--new__hint", "drop an unlinked character or a card file here"),
    choose,
  );
  opens(box, () => newCardByHand(popup));
  dropTarget(box, (dropped) => promote(popup, dropped));
  filesDropped(box, (files) => importCards(popup, files));
  return box;
}

function filesDropped(target, onDrop) {
  /* Files dragged in from outside the page, and nothing else: a story
     character or a card dragged here is another handler's. */
  const carries = (event) => event.dataTransfer?.types.includes("Files");
  target.addEventListener("dragover", (event) => {
    if (!carries(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    target.classList.add("is-dragover");
  });
  target.addEventListener("dragleave", () => target.classList.remove("is-dragover"));
  target.addEventListener(
    "drop",
    guard(async (event) => {
      target.classList.remove("is-dragover");
      if (!carries(event)) return;
      event.preventDefault();
      await onDrop([...event.dataTransfer.files]);
    }),
  );
}

async function importCards(popup, files) {
  /* Card files into the codex, each as it is — renamed, if at all, once it
     is there: one the codex holds already answers its card, any other is
     made a card of its own, last in the order. One at a time, the first
     refused stopping the rest and saying why; the panel says the last
     answer. */
  if (!files.length) return;
  let answer = null;
  for (const file of files) {
    const { name, data } = await encodeFile(file);
    answer = await api.cards.create({ data, file_name: name });
    if (answer.refused) break;
  }
  await redraw(popup, answer);
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
  dropTarget(target, (dropped) => link(popup, dropped, card.id));
}

function dropTarget(target, onDrop) {
  /* A story character from the unlinked column, and nothing else: a file
     or a text dragged in is not this target's. */
  const carries = (event) => event.dataTransfer?.types.includes(_CHARACTER);
  target.addEventListener("dragover", (event) => {
    if (!carries(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "link";
    target.classList.add("is-dragover");
  });
  target.addEventListener("dragleave", () => target.classList.remove("is-dragover"));
  target.addEventListener(
    "drop",
    guard(async (event) => {
      target.classList.remove("is-dragover");
      if (!carries(event)) return;
      event.preventDefault();
      await onDrop(JSON.parse(event.dataTransfer.getData(_CHARACTER)));
    }),
  );
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
  const choice = await ask("confirm", (dialog) => {
    $("[data-title]", dialog).textContent = "Also in forked stories";
    $(".otk-dialog__body", dialog).textContent =
      n === 1
        ? "The character also appears in 1 other story that was forked, link it too? " +
          "You can review and unlink it if needed in the card later."
        : `The character also appears in ${n} other stories that were forked, link them all? ` +
          "You can review and unlink them if needed in the card later.";
    const aside = $("[data-note]", dialog);
    aside.textContent = "";
    aside.hidden = true;
    $('[data-choice="confirm"]', dialog).textContent = "Link all";
    $('[data-choice="cancel"]', dialog).textContent = "Only this one";
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
    footnote(popup, made.notice, true);
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
     dossier's cast) over whatever is open: Esc puts it down (a field
     editor open in it takes its own Esc first), and so does a click on
     the scrim around it — but not one pressed inside it and let go
     outside (a selection dragged past its edge), nor while one of its
     fields is being edited, which would lose the words. */
  if (sheet.open) return;
  const signal = wiring(sheet);
  let pressedOutside = false;
  sheet.addEventListener("pointerdown", (event) => (pressedOutside = event.target === sheet), {
    signal,
  });
  sheet.addEventListener(
    "click",
    (event) => {
      const outside = pressedOutside && event.target === sheet;
      pressedOutside = false;
      if (outside && !$(".otk-edit[data-editing]", sheet)) sheet.close();
    },
    { signal },
  );
  sheet.showModal();
}

async function openCard(popup, cardId, { tab = "card", storyId = null, naming = false } = {}) {
  /* One card, read whole and drawn as a sheet over the panel: its Card
     tab — pictures, fields, the stories it is in — and its Journals tab,
     a story's entries at a time. `naming`, its name open in its editor,
     selected whole: a card just made, to be named. */
  const card = await api.cards.get(cardId);
  if (card.refused) {
    if (popup.open) footnote(popup, card.notice, true);
    return card;
  }
  const sheet = sheetOf("card");
  const opening = !sheet.open;
  closeSheets(sheet);
  sheet.setAttribute("aria-label", card.name);

  const head = element("div", "otk-sheet__head");
  const name = editable("otk-detail__title", {
    text: card.name,
    line: true,
    save: (text) => saveCard(popup, card, { name: text }),
  });
  name.rows = 1;
  // the name needs no label: it is the sheet's title
  const titled = edited(name, "", "otk-edit--title");
  head.append(titled, closeButton(sheet));

  const tabs = element("div", "otk-tabs otk-tabs--sheet");
  tabs.setAttribute("role", "tablist");
  const cardTab = tabButton("Card", "card");
  const journalsTab = tabButton("Journals", "journals");
  tabs.append(cardTab, journalsTab);

  const body = element("div", "otk-sheet__body");
  body.dataset.pane = "card";
  body.append(pictures(popup, card), fields(popup, card));

  const journals = element("div", "otk-sheet__journals");
  journals.dataset.pane = "journals";

  /* What the card's actions answer is said in its own foot, the panel's
     being under the scrim; a card drawn again where it stands keeps the
     foot it had, and the sentence or the error standing in it. */
  const foot = (sheet.open && $(".otk-sheet__foot", sheet)) || element("div", "otk-sheet__foot");
  const note = $("[data-note]", foot) ?? span("otk-sheet__note");
  note.dataset.note = "";
  foot.replaceChildren(
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
  /* The name's verbs go under it, at its end — or after it, where under it
     they would reach over the tabs (16px kept clear of them). Measured
     against the name itself, which a row with the verbs after it widens
     the box past, and again as the name or the verbs change width. */
  const hint = $(".otk-edit__hint", titled);
  const placeVerbs = () => {
    const under = titled.firstElementChild.getBoundingClientRect().right - hint.offsetWidth;
    const clear = journalsTab.getBoundingClientRect().right + 16;
    titled.dataset.verbs = under >= clear ? "under" : "after";
  };
  sheet._verbs?.disconnect();
  sheet._verbs = new ResizeObserver(placeVerbs);
  sheet._verbs.observe(titled);
  sheet._verbs.observe(hint);
  placeVerbs();
  if (naming) {
    $(".otk-edit__verb--edit", titled).click();
    name.select();
  }
}

async function saveCard(popup, card, fields) {
  /* A field saved: the card as it now stands replaces the one drawn, and
     the lists are read again — a name, a tagline, an alias show there
     too. The answer goes back to the editor, which keeps itself open on
     a refusal. */
  const answer = await api.cards.update(card.id, fields);
  if (!answer.refused) {
    Object.assign(card, answer.value);
    await redraw(popup, answer);
    sheetOf("card").setAttribute("aria-label", card.name);
  }
  return answer;
}

function pictures(popup, card) {
  /* The card's pictures: the one shown large, the strip under it — a
     thumb picks, a thumb dragged reorders, the first is the main one —
     and the two ways in: + to upload, images dropped. Built once for the
     pictures the card holds — a write that changes them draws the card
     again — the one shown slid across for the next (`slideIn`). */
  const box = element("div", "otk-pics");
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
  box.addEventListener("dragover", (event) => {
    if (!event.dataTransfer.types.includes("Files")) return;
    event.preventDefault();
    box.classList.add("is-dragover");
  });
  box.addEventListener("dragleave", () => box.classList.remove("is-dragover"));
  box.addEventListener(
    "drop",
    guard(async (event) => {
      box.classList.remove("is-dragover");
      if (!event.dataTransfer.types.includes("Files")) return;
      event.preventDefault();
      await addPictures(popup, card.id, [...event.dataTransfer.files]);
    }),
  );
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
  for (const item of items) {
    item.addEventListener("dragstart", (event) => {
      event.dataTransfer.setData(type, String(items.indexOf(item)));
      event.dataTransfer.effectAllowed = "move";
      dragged = item;
      item.classList.add("is-dragging");
    });
    item.addEventListener("dragend", () => {
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
    });
    // over another, it takes that one's place: after one that came after
    // it, before one that came before
    item.addEventListener("dragover", (event) => {
      if (!carries(event)) return;
      event.preventDefault();
      if (item === dragged || gliding.has(item)) return;
      const places = [...container.children];
      const later = places.indexOf(dragged) < places.indexOf(item);
      making(() => (later ? item.after(dragged) : item.before(dragged)));
    });
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
  let answer = null;
  for (const file of taken) {
    answer = await api.cards.pictures.create(cardId, await encodeFile(file));
    if (answer.refused) break;
  }
  await redraw(popup, answer);
  if (sheetOf("card").open) await openCard(popup, cardId);
}

async function removePicture(popup, card, at) {
  const picture = card.pictures[at];
  if (!picture) return;
  const choice = await ask("confirm", (dialog) => {
    $("[data-title]", dialog).textContent = "Remove this picture?";
    $(".otk-dialog__body", dialog).textContent =
      `It goes from ${card.name}'s pictures. A story it was sent to keeps its copy.`;
    const aside = $("[data-note]", dialog);
    aside.textContent = "";
    aside.hidden = true;
    $('[data-choice="confirm"]', dialog).textContent = "Remove";
    $('[data-choice="cancel"]', dialog).textContent = "Cancel";
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
    one — each edited where it is read, in the card's order: an empty one
    reads as a dash, a list is edited as one text, its items between lines
    of three dashes, and the reminder's depth as a whole number.
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
    const input = editable(className, {
      text,
      line: kind === "number",
      save: (value) => save({ [key]: write(value) }),
    });
    input.placeholder = "\u2014";
    return edited(input, name);
  });
}

function fields(popup, card) {
  /* The card's text, each part edited where it is read: the tagline the
     gallery shows, the aliases, then the card's own fields in order — an
     empty one reads as a dash. The stories it is in close the column, each
     row a door to its journals. */
  const box = element("div", "otk-sheet__fields");
  const field = (className, key, name, text, { line = false, write = (value) => value } = {}) => {
    const input = editable(className, {
      text,
      line,
      save: (value) => saveCard(popup, card, { [key]: write(value) }),
    });
    input.placeholder = "\u2014";
    return edited(input, name);
  };
  box.append(
    field("otk-reading__lede otk-sheet__tagline", "tagline", "tagline", card.tagline, { line: true }),
    field("otk-passage__body", "aliases", "aliases", card.aliases.join(" · "), {
      line: true,
      write: (value) =>
        value
          .split(/[·,\n]/)
          .map((alias) => alias.trim())
          .filter(Boolean),
    }),
    ...cardFieldEditors(card, (changes) => saveCard(popup, card, changes)),
  );
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
  const choice = await ask("confirm", (dialog) => {
    $("[data-title]", dialog).textContent = `Delete ${card.name}?`;
    $(".otk-dialog__body", dialog).textContent =
      "The card and its pictures go from All characters. Its characters in stories stay, unlinked.";
    const aside = $("[data-note]", dialog);
    aside.textContent = "";
    aside.hidden = true;
    $('[data-choice="confirm"]', dialog).textContent = "Delete";
    $('[data-choice="cancel"]', dialog).textContent = "Cancel";
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
    if (!persona.value.trim()) persona.value = "you";
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
        const toggle = element("button", "otk-switch otk-switch--h");
        toggle.type = "button";
        toggle.setAttribute("role", "switch");
        toggle.setAttribute("aria-checked", "false");
        toggle.setAttribute("aria-label", `Send ${card.name}'s picture to the model`);
        toggle.append(span("otk-switch__knob"));
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
    footnote(popup, made.notice, true);
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
  svg.append(svgElement("path", { d: _CHEVRONS[way], ..._CHEVRON_STROKE }));
  button.append(svg);
  button.onclick = onclick;
  return button;
}

function svgElement(tag, attributes) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  return node;
}

function closeButton(sheet) {
  // Esc puts a sheet down too, as it does every window: named as every close names it
  const button = actionButton("Close ", { onclick: () => sheet.close() });
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
