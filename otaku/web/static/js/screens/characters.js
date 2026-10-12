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
import { ask, closeAll, edited, editable, footnote, guard, popups, wiring } from "../kit/browser.js";
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
// The narrow screen of the stylesheet's own rule: the unlinked column
// starts folded there, until the reader unfolds it.
const _NARROW = "(max-width: 720px), (pointer: coarse) and (max-height: 540px)";

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
  ["reminder_depth", "reminder depth", "number"],
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
  setView(popup, readView());
  chars.dataset.unlinked = readFold();
  state.query = state.unlinkedQuery = "";
  $("[data-filter-cards]", popup).value = "";
  $("[data-filter-unlinked]", popup).value = "";
  closeSheets(popup);
  wireChrome(popup);
  await redraw(popup);
  if (cardId !== null) await openCard(popup, cardId);
}

/** A codex card into the story held: picked, then brought in through
    the card dialog, the codex card where a file would be — or, by the
    picker's Import…, a card file into the story. */
export async function inviteCard() {
  const picked = await pickCard({ title: "Invite a card", importing: true });
  if (!picked) return;
  if (picked.import) return importCard({ destination: "story" });
  return importCard({ cardId: picked.card.id });
}

/** The card a story character is to be linked to: picked — or, by the
    picker's Import…, a card file put into All characters for it. Its id,
    or null for none. */
export async function chooseLink(name) {
  const picked = await pickCard({ title: `Link ${name} to\u2026`, importing: true });
  if (!picked) return null;
  if (!picked.import) return picked.card.id;
  const done = await importCard({ destination: "codex" });
  return done?.cardId ?? null;
}

/** Which card: the picker every "which card" goes through — a card to
    link, to invite, to merge into. `{card}` for the one chosen,
    `{import: true}` for the picker's Import…, null for none. `exclude`
    leaves a card out (the one being merged); `importing` shows Import…. */
export async function pickCard({ title = "Choose a card", exclude = null, importing = false } = {}) {
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
    confirm.disabled = true;
    let asked = 0;
    const draw = async (query) => {
      // one read per keystroke; only the newest paints
      const mine = ++asked;
      const rows = (await api.cards.list({ query })).filter((card) => card.id !== exclude);
      if (mine !== asked || signal.aborted) return;
      if (!rows.length) {
        list.replaceChildren(empty(query ? "No card matches that." : "No cards yet.", query ? "" : "import one"));
        return;
      }
      list.replaceChildren(
        ...rows.map((card) => {
          const row = element("button", "otk-row otk-pickrow");
          row.type = "button";
          row.setAttribute("role", "option");
          row.setAttribute("aria-selected", String(chosen?.id === card.id));
          row.append(portrait("otk-charrow__pic", card), nameAndTag(card));
          row.onclick = () => {
            chosen = card;
            for (const each of $$(".otk-pickrow", list)) each.setAttribute("aria-selected", String(each === row));
            confirm.disabled = false;
          };
          row.ondblclick = () => confirm.click();
          return row;
        }),
      );
    };
    filter.addEventListener("input", guard(() => draw(filter.value.trim())), { signal });
    // Choose answers only with a card in hand: Enter on an empty pick
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
  if (choice === "choose" && chosen) return { card: chosen };
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
      },
      { signal },
    );
  }
  $("[data-import-card]", popup).addEventListener(
    "click",
    guard(async () => {
      const done = await importCard({ destination: "codex" });
      if (done) await redraw(popup, done.notice);
    }),
    { signal },
  );
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
  /* A click in the panel outside an open sheet puts it down, as Esc does
     — one pressed inside it and let go outside (a selection dragged past
     its edge) excepted, and while one of its fields is being edited,
     which would lose the words. The path is the one dispatched: a click
     that redraws the sheet has detached its target by the time it gets
     here. */
  let pressedInside = false;
  popup.addEventListener(
    "pointerdown",
    (event) => {
      const open = $$(".otk-sheet", popup).find((sheet) => !sheet.hidden);
      pressedInside = Boolean(open && event.composedPath().includes(open));
    },
    { signal },
  );
  popup.addEventListener(
    "click",
    (event) => {
      const open = $$(".otk-sheet", popup).find((sheet) => !sheet.hidden);
      const inside = pressedInside || (open && event.composedPath().includes(open));
      pressedInside = false;
      if (!open || inside || $(".otk-edit[data-editing]", open)) return;
      open.hidden = true;
    },
    { signal },
  );
  popup.addEventListener(
    "keydown",
    (event) => {
      if (event.key !== "Escape") return;
      const open = $$(".otk-sheet", popup).find((sheet) => !sheet.hidden);
      // a field editor open in the sheet has taken its own Esc already
      if (!open || event.defaultPrevented) return;
      event.preventDefault();
      event.stopPropagation();
      open.hidden = true;
    },
    { signal },
  );
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
    as typed — and draw both; `answered` is the footnote, else the
    counts. */
async function redraw(popup, answered = "") {
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
  footnote(
    popup,
    answered ||
      `${n} ${n === 1 ? "card" : "cards"} · ${m} unlinked story ${m === 1 ? "character" : "characters"}`,
  );
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
  drawPicked(popup);
}

function drawPicked(popup) {
  const names = state.picked.map((id) => state.known.get(id)?.name).filter(Boolean);
  $("[data-picked]", popup).textContent = names.join(", ");
  $("[data-clear]", popup).hidden = !names.length;
  $("[data-start]", popup).disabled = !names.length;
}

function tile(popup, card) {
  /* A card in the gallery: its picture — flipped through where it has
     several — its tick for a story, its name, tagline and how much of it
     there is. A card with no picture takes one from its frame. */
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
    framed(image, card.pictures[at]);
    pic.append(image);
    if (card.pictures.length > 1) {
      const flip = (by) => (event) => {
        event.stopPropagation();
        state.frames.set(card.id, (at + by + card.pictures.length) % card.pictures.length);
        box.replaceWith(tile(popup, card));
      };
      pic.append(
        navButton("otk-tile__nav otk-tile__nav--prev", "Previous picture", "‹", flip(-1)),
        navButton("otk-tile__nav otk-tile__nav--next", "Next picture", "›", flip(1)),
        span("otk-tile__count", `${at + 1} / ${card.pictures.length}`),
      );
    }
  } else {
    const add = element("button", "otk-tile__empty", "+ add picture");
    add.type = "button";
    add.onclick = guard(async (event) => {
      event.stopPropagation();
      await addPictures(popup, card.id);
    });
    pic.append(add);
  }
  box.append(
    pic,
    tick(popup, card),
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
  /* The card made two ways: an unlinked story character dropped on it —
     promoted, and linked — or, clicked, a card by hand, its name asked
     and the card opened to be filled in. */
  const box = element("div", className);
  box.tabIndex = 0;
  box.setAttribute("role", "button");
  box.setAttribute("aria-label", "New card — or drop an unlinked character here");
  box.append(
    span("otk-tile--new__plus", "+"),
    span("otk-tile--new__label", "new card"),
    span("otk-tile--new__hint", "drop an unlinked character here"),
  );
  opens(box, () => newCardByHand(popup));
  dropTarget(box, (dropped) => promote(popup, dropped));
  return box;
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
  const stories = card.story_count
    ? `${card.story_count} ${card.story_count === 1 ? "story" : "stories"}`
    : "not in a story";
  const n = card.pictures.length;
  return `${stories} · ${n ? `${n} ${n === 1 ? "picture" : "pictures"}` : "no picture"}`;
}

function drawUnlinked(popup) {
  const list = $("[data-unlinked-list]", popup);
  const rows = state.unlinked;
  const { unlinked: total } = state.total;
  $("[data-unlinked-count]", popup).textContent = String(total);
  $("[data-unlinked-strip]", popup).textContent = `Unlinked · ${total}`;
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
  const answer = await api.stories.characters.update(character.story_id, character.character_id, {
    card_id: cardId,
  });
  closeSheets(popup);
  await redraw(popup, answer.notice);
}

async function promote(popup, character) {
  const answer = await api.cards.create({
    story_id: character.story_id,
    character_id: character.character_id,
  });
  closeSheets(popup);
  await redraw(popup, answer.notice);
}

async function newCardByHand(popup) {
  const dialog = $('dialog[data-dialog="new-card"]');
  const field = $("input", dialog);
  const choice = await ask("new-card", () => (field.value = ""));
  if (choice !== "create") return;
  const made = await api.cards.create({ name: field.value.trim() });
  await redraw(popup, made.notice);
  if (!made.refused) await openCard(popup, made.value.id);
}

// ---------- the open card ----------

function closeSheets(popup) {
  for (const sheet of $$(".otk-sheet", popup)) sheet.hidden = true;
}

async function openCard(popup, cardId, { tab = "card", storyId = null } = {}) {
  /* One card, read whole and drawn as a sheet over the panel: its Card
     tab — pictures, fields, the stories it is in — and its Journals tab,
     a story's entries at a time. */
  const card = await api.cards.get(cardId);
  if (card.refused) {
    footnote(popup, card.notice);
    return;
  }
  closeSheets(popup);
  const sheet = $('[data-sheet="card"]', popup);
  sheet.setAttribute("aria-label", card.name);

  const head = element("div", "otk-sheet__head");
  const name = editable("otk-detail__title", {
    text: card.name,
    line: true,
    save: (text) => saveCard(popup, card, { name: text }),
  });
  name.rows = 1;
  // the name needs no label: it is the sheet's title
  head.append(edited(name, "", "otk-edit--title"), closeButton(sheet));

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

  const foot = element("div", "otk-sheet__foot");
  foot.append(
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
  sheet.hidden = false;
  select(tab, storyId);
}

async function saveCard(popup, card, fields) {
  /* A field saved: the card as it now stands replaces the one drawn, and
     the lists are read again — a name, a tagline, an alias show there
     too. The answer goes back to the editor, which keeps itself open on
     a refusal. */
  const answer = await api.cards.update(card.id, fields);
  if (!answer.refused) {
    Object.assign(card, answer.value);
    await redraw(popup, answer.notice);
    $('[data-sheet="card"]', popup).setAttribute("aria-label", card.name);
  }
  return answer;
}

function pictures(popup, card) {
  /* The card's pictures: the one shown large, the strip under it — a
     thumb picks, a thumb dragged reorders, the first is the main one —
     and the two ways in: + to upload, images dropped. */
  const box = element("div", "otk-pics");
  let at = 0;
  const main = element("div", "otk-pics__main");
  const strip = element("div", "otk-pics__thumbs");
  const remove = element("button", "otk-pics__remove", "remove");
  remove.type = "button";
  const draw = () => {
    main.replaceChildren();
    if (card.pictures.length) {
      const image = element("img");
      image.alt = "";
      image.src = api.pictures.url(card.pictures[at].file);
      image.onclick = () => openPicture(card.pictures, at);
      main.append(image, span("otk-tile__count", `${at + 1} / ${card.pictures.length}`));
    }
    strip.replaceChildren(
      ...card.pictures.map((picture, i) => {
        const thumb = element("button", "otk-pics__thumb");
        thumb.type = "button";
        thumb.draggable = true;
        thumb.setAttribute("aria-label", `picture ${i + 1}`);
        thumb.setAttribute("aria-current", String(i === at));
        const image = element("img");
        image.alt = "";
        image.src = api.pictures.thumbnail.url(picture.file);
        thumb.append(image);
        thumb.onclick = () => {
          at = i;
          draw();
        };
        thumb.addEventListener("dragstart", (event) => {
          event.dataTransfer.setData("text/x-otaku-picture", String(i));
          thumb.classList.add("is-dragging");
        });
        thumb.addEventListener("dragend", () => thumb.classList.remove("is-dragging"));
        thumb.addEventListener("dragover", (event) => {
          if (event.dataTransfer.types.includes("text/x-otaku-picture")) event.preventDefault();
        });
        thumb.addEventListener(
          "drop",
          guard(async (event) => {
            const from = Number(event.dataTransfer.getData("text/x-otaku-picture"));
            if (Number.isNaN(from) || from === i) return;
            event.preventDefault();
            const order = card.pictures.map((each) => each.file);
            order.splice(i, 0, ...order.splice(from, 1));
            await reopenAfter(popup, card, api.cards.update(card.id, { pictures: order }));
          }),
        );
        return thumb;
      }),
      addButton(),
    );
    remove.hidden = !card.pictures.length;
  };
  const addButton = () => {
    const add = element("button", "otk-pics__add", "+");
    add.type = "button";
    add.setAttribute("aria-label", "Upload pictures");
    add.onclick = guard(() => addPictures(popup, card.id));
    return add;
  };
  remove.onclick = guard(() => removePicture(popup, card, at));
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
  draw();
  box.append(
    main,
    strip,
    remove,
    span("otk-pics__hint", "drop images here, or + to upload · drag to reorder · the first is the main one"),
  );
  return box;
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
  await redraw(popup, answer.notice);
  if (!$('[data-sheet="card"]', popup).hidden) await openCard(popup, cardId);
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
  await redraw(popup, answer.notice);
  await openCard(popup, card.id);
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
    field("otk-reading__lede otk-sheet__tagline", "tagline", "short description", card.tagline, { line: true }),
    field("otk-passage__body", "aliases", "aliases", card.aliases.join(" · "), {
      line: true,
      write: (value) =>
        value
          .split(/[·,\n]/)
          .map((alias) => alias.trim())
          .filter(Boolean),
    }),
    ...(_FIELDS.map(([key, name, kind]) =>
      kind === "list"
        ? field("otk-passage__body", key, name, card[key].join("\n\n---\n\n"), {
            write: (value) =>
              value
                .split(_ITEMS)
                .map((item) => item.trim())
                .filter(Boolean),
          })
        : kind === "number"
          ? field("otk-passage__body", key, name, String(card[key]), {
              line: true,
              // what is no whole number goes as 0, for the backend's refusal
              write: (value) => (/^\s*\d+\s*$/.test(value) ? Number(value) : 0),
            })
          : field("otk-passage__body", key, name, card[key]),
    )),
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
      await redraw(popup, answer.notice);
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
  await redraw(popup, answer.notice);
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
    closeSheets(popup);
    state.picked = state.picked.filter((id) => id !== card.id);
  }
  await redraw(popup, answer.notice);
}

// ---------- an unlinked story character ----------

async function openUnlinked(popup, row) {
  /* A story character read before it is linked: its journal entries, and
     the two doors a drag stands for — a card to link it to, or a new
     card made of it. */
  closeSheets(popup);
  const sheet = $('[data-sheet="unlinked"]', popup);
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
  const hint = span("", "drag from the list onto a card to link · onto ");
  hint.append(element("b", "", "new card"), " to make one");
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
  sheet.hidden = false;
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
    const n = cards.length;
    $("[data-start-count]", dialog).textContent =
      `${n} ${n === 1 ? "character" : "characters"}` + (picturedCards.length ? " · send picture to the context" : "");
    const box = $("[data-start-pictures]", dialog);
    box.replaceChildren(
      box.firstElementChild,
      ...picturedCards.map((card) => {
        const line = element("div", "otk-picsend");
        const toggle = element("button", "otk-switch otk-switch--h");
        toggle.type = "button";
        toggle.setAttribute("role", "switch");
        toggle.setAttribute("aria-checked", "false");
        toggle.setAttribute("aria-label", `Send ${card.name}'s picture to the context`);
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
    // Start answers only with a user name: it binds {{user}} in every card.
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
    footnote(popup, made.notice);
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

function navButton(className, name, glyph, onclick) {
  const button = element("button", className, glyph);
  button.type = "button";
  button.setAttribute("aria-label", name);
  button.onclick = onclick;
  return button;
}

function closeButton(sheet) {
  // Esc puts a sheet down too (`wireChrome`): named as every close names it
  const button = actionButton("Close ", { onclick: () => (sheet.hidden = true) });
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
