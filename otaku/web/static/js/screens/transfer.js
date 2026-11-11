/* The transfer screens: a card in, a story in, a story out.

   The Import card dialog carries the whole card import into the story —
   a file or a link, which goes into Characters too unless the reader
   keeps it to the story — the persona included: who the card's {{user}} is, asked on the same
   face the card is chosen on, prefilled from the story's own memory of
   it, so a story is asked once and later imports follow it.

   A path over HTTP would name a file on the SERVER, so nothing here
   sends one: an import sends what the browser read, a card sends its
   bytes, and an export is saved by the browser where the reader says. */

import { api } from "../api/index.js";
import { $, element, pickFile, span } from "../kit/dom.js";
import { ask, closeAll, footnote, guard, report, wiring } from "../kit/browser.js";
import * as held from "../play/held.js";
import { landed, watchExtraction } from "../play/shell.js";
import { tell } from "../kit/status.js";

// How long the link field waits after the last keystroke before it reads the link.
const _LINK_SETTLE_MS = 800;
// px between the supported sites' toggle and their list, and the list and the window's edge
const _SITES_GAP = 6;

export async function importDocument() {
  const file = await pickFile(".md,.jsonl,.txt");
  if (!file) return;
  const answer = await api.imports.create({ text: await file.text(), file_name: file.name });
  // refused, nothing was made: said on the screen it was asked from
  if (answer.refused) {
    report(answer.notice);
    return;
  }
  // the story the document made is played from now on
  held.hold(answer.value.story_id);
  await landed(answer.notice, { redraw: "always" });
  // The memoryless shapes build their memory now, through the same
  // forced pass a manual close runs. A native export arrives with its
  // memory and starts none, so nothing polls for a report never filed.
  if (answer.value?.extraction?.status === "running") watchExtraction(answer.value.story_id);
}

export async function importCard() {
  /* Import card, as the design draws it: a card file dropped or chosen,
     or a link pasted, read as soon as it is given, and the fields filled from
     what the backend read: the character's name in this story, the
     persona it speaks to (the story's own memory of it), the cards
     Characters has like it with the switch that saves this one beside
     them or keeps it in this story alone, the picture's switch, and
     what the import will do — the backend's notes under it. A link is
     fetched once: its reading brings the file back, which the import
     sends as a file. A refusal is said in the dialog's foot (`report`). */
  // what the card is read from: the file dropped or chosen, or the link
  // pasted — the last given — and the file the import sends
  let source = null;
  let file = null;
  let prepared = null;
  let renamed = "";
  let sending = false;
  let saving = true;
  let similar = false;
  /* A card is read FOR a story: the one held, or — a reader with none —
     one made for it, taken back where the dialog ends without an import
     (`held.makeFor`). */
  let made = null;
  const storyFor = async () => {
    if (held.story() !== null) return { id: held.story() };
    const answer = await held.makeFor();
    if (!answer.refused) made = answer.id;
    return answer;
  };
  /* The card read and vetted, nothing stored — under `rename`, the name
     it would take in the story, the card's own where it is empty — or the
     refusal. A link is read through the server, which brings its file
     back; read again under a new name, that file is sent, so the link is
     not fetched twice. */
  const prepare = async (rename) => {
    const story = await storyFor();
    if (story.refused) return story;
    const asked = rename ? { name: rename } : {};
    let check;
    if (file) {
      check = await api.stories.cards.create(story.id, { ...file, ...asked }, { validateOnly: true });
    } else {
      check = await api.stories.cards.fetches.create(
        story.id,
        { url: source.url, ...asked },
        { validateOnly: true },
      );
      // a link's file, and the line its site shows the card under
      if (!check.refused) file = { data: check.data, file_name: check.file_name, tagline: check.tagline };
    }
    // the story held, deleted elsewhere: read again for one made for it
    if (check.refused && check.status === 404 && (await held.lost(story.id))) return prepare(rename);
    return check.refused ? check : { card: { ...check, tokens: check.block_tokens } };
  };

  const choice = await ask("card", (dialog) => {
    const zone = $(".otk-dropzone", dialog);
    const picker = $("input[type=file]", dialog);
    const filename = $("[data-file]", dialog);
    const link = $("[data-link]", dialog);
    const name = $("#otk-card-name", dialog);
    const persona = $("#otk-card-persona", dialog);
    const similarBox = $("[data-similar]", dialog);
    const similarCards = $("[data-similar-cards]", dialog);
    const saveSwitch = $("[data-save-switch]", dialog);
    const picsend = $("[data-picsend]", dialog);
    const picsendSwitch = $("[data-picsend-switch]", dialog);
    const outcomes = $("[data-outcomes]", dialog);
    const will = $("[data-will]", dialog);
    const note = $("[data-card-note]", dialog);
    const importButton = $('[data-choice="import"]', dialog);
    name.value = persona.value = link.value = "";
    // emptied, so the same file chosen again is another change
    picker.value = "";
    filename.hidden = true;
    zone.classList.remove("is-filled", "is-dragover");
    similarBox.hidden = picsend.hidden = outcomes.hidden = true;
    sending = false;
    saving = true;
    picsendSwitch.setAttribute("aria-checked", "false");
    saveSwitch.setAttribute("aria-checked", "true");
    picsendSwitch.onclick = () => {
      sending = !sending;
      picsendSwitch.setAttribute("aria-checked", String(sending));
    };
    saveSwitch.onclick = () => {
      saving = !saving;
      saveSwitch.setAttribute("aria-checked", String(saving));
      drawWill();
    };

    /* What the import will do (the product's own rule, in the page's
       words), and under it what the backend said of this card — its own
       sentences, unchanged. */
    const drawWill = () => {
      const read = prepared?.card;
      if (!read) return;
      const library = read.same_card
        ? `use ${read.same_card.name} from Characters, linked`
        : similar && !saving
          ? "keep the card in this story only"
          : "save the card to Characters, linked";
      will.replaceChildren(
        ...[
          "send the card's text as the next message",
          `add ${name.value.trim() || read.name} to this story's cast`,
          "play the card's greeting as their first message",
          library,
        ].map((line) => element("li", "", line)),
      );
    };

    let taking = 0;
    const take = async (given) => {
      /* A card given — a file, a link, or null to read the one in hand
         again — read,
         and the fields filled from it. Gifts can overlap — a second card
         while the first still reads — and only the LAST given may become
         the dialog's card: each take carries its turn, and a stale one
         stops before writing anything. */
      const mine = ++taking;
      if (given?.kind === "file") {
        const bytes = await encode(given.file);
        if (mine !== taking) return;
        file = { data: bytes, file_name: given.file.name };
        link.value = "";
        filename.textContent = given.file.name;
        filename.hidden = false;
        zone.classList.add("is-filled");
      } else if (given?.kind === "link") {
        file = null;
        filename.hidden = true;
        zone.classList.remove("is-filled");
      }
      if (given) source = given;
      zone.classList.remove("is-dragover");
      prepared = null;
      arm();
      const answer = await prepare((renamed = name.value.trim()));
      if (mine !== taking) return;
      prepared = answer;
      if (!prepared.card) {
        // the refusal is the dialog's error, said in its foot
        report(prepared.notice);
        similarBox.hidden = picsend.hidden = outcomes.hidden = true;
        arm();
        return;
      }
      footnote(dialog, ""); // a refusal standing goes with the card read
      const read = prepared.card;
      // the character's name: the card's own, until the reader types one
      if (!name.value.trim()) name.value = renamed = read.name;
      // who the card speaks to: the story's memory of it, else the reader's to type
      if (!persona.value.trim()) persona.value = read.persona;
      // the cards Characters has like this one, by name or picture
      similar = !read.same_card && read.similar_cards.length > 0;
      similarCards.replaceChildren(
        ...(similar ? read.similar_cards.map((one) => element("div", "otk-imp__src")) : []),
      );
      if (similar) {
        [...similarCards.children].forEach((row, at) => row.append(...cardRow(read.similar_cards[at])));
      }
      similarBox.hidden = !similar;
      // the picture's switch, only for a card with a picture
      picsend.hidden = !read.has_picture;
      drawWill();
      const caveats = [...read.notes];
      // A card that rides every request is worth saying out loud before
      // it is imported, not after.
      if (read.large) caveats.push(`This card is about ${read.tokens} tokens, and rides every request.`);
      note.textContent = caveats.join(" ");
      note.hidden = !caveats.length;
      outcomes.hidden = false;
      arm();
    };

    picker.onchange = guard(() => picker.files[0] && take({ kind: "file", file: picker.files[0] }));
    zone.ondragover = (event) => {
      event.preventDefault();
      zone.classList.add("is-dragover");
    };
    zone.ondragleave = () => zone.classList.remove("is-dragover");
    zone.ondrop = guard((event) => {
      event.preventDefault();
      const dropped = event.dataTransfer.files[0];
      zone.classList.remove("is-dragover");
      return dropped && take({ kind: "file", file: dropped });
    });

    /* The dialog's MANDATORY fields, enforced: a card read, a character
       name and a persona name — without them the button stays off, and a
       press says what is missing (a dialog button is no form submit, so
       a field's `required` needs this hand-wiring). Wired before `ask`
       adds its own listeners, so the stop is the whole veto — the button
       and the dialog's Enter alike; `wiring` drops the last opening's
       veto with its stale closures. */
    const signal = wiring(dialog);
    wireSupportedSites(dialog, signal);
    const named = () => name.value.trim() && persona.value.trim();
    const arm = () => {
      importButton.disabled = !(prepared?.card && named());
    };
    arm();
    persona.addEventListener("input", arm, { signal });
    name.addEventListener(
      "input",
      () => {
        arm();
        drawWill();
      },
      { signal },
    );
    // a link is read once a paste lands, an edit once the typing stops, or on Enter
    let settling = null;
    const readLink = () => {
      clearTimeout(settling);
      const url = link.value.trim();
      if (url && url !== (source?.kind === "link" ? source.url : "")) {
        guard(() => take({ kind: "link", url }))();
      }
    };
    link.addEventListener(
      "input",
      () => {
        clearTimeout(settling);
        settling = setTimeout(readLink, _LINK_SETTLE_MS);
      },
      { signal },
    );
    link.addEventListener("paste", () => setTimeout(readLink), { signal });
    link.addEventListener(
      "keydown",
      (event) => {
        if (event.key !== "Enter") return;
        event.preventDefault();
        event.stopPropagation();
        readLink();
      },
      { signal },
    );
    dialog.addEventListener("close", () => clearTimeout(settling), { signal });
    const veto = (event) => {
      // Settle only with a read card, both names, and a character name
      // that IS the one it was read under: a changed name is read again
      // here, so a refusal of it answers into the open dialog.
      if (prepared?.card && named() && name.value.trim() === renamed) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      if (!source) {
        report("Choose a card file first.");
      } else if (!name.value.trim()) {
        name.reportValidity();
      } else if (!persona.value.trim()) {
        persona.reportValidity();
      } else if (prepared?.card && name.value.trim() !== renamed) {
        guard(async () => {
          await take(null);
          if (prepared?.card && named()) importButton.click();
        })();
      }
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
  });

  if (choice !== "import" || !prepared?.card) {
    if (made !== null) await held.takeBack(made);
    return;
  }
  const story = held.story();
  const wanted = $("#otk-card-name")?.value.trim() ?? "";
  const persona = $("#otk-card-persona").value.trim();
  const fields = {
    ...file,
    name: wanted,
    persona,
    with_pictures: sending,
    story_only: similar && !saving,
  };
  let answer;
  try {
    answer = await api.stories.cards.create(story, fields);
  } catch (e) {
    if (made !== null) await held.takeBack(made);
    throw e;
  }
  if (answer.refused && made !== null) await held.takeBack(made);
  else if (!answer.refused) held.hold(story);
  // The card is in: the screen it was asked from goes, as a confirmed
  // question's does, and the landing is read in the flow. A cancelled
  // dialog left that screen exactly as it was.
  closeAll();
  await landed(answer.notice, { redraw: "always", refused: answer.refused });
}

function cardRow(card) {
  /* A library card as a row of the dialog: its portrait, its name and its
     tagline. */
  const frame = span("otk-imp__thumb");
  frame.append(...portraitOf(card));
  const text = element("span", "otk-imp__text");
  text.append(span("otk-imp__name", card.name), span("otk-imp__tag", card.tagline));
  return [frame, text];
}

function portraitOf(card) {
  /* A library card's portrait, as its thumbnail; none for a card without one. */
  if (!card.pictures?.length) return [];
  const image = element("img");
  image.alt = "";
  image.src = api.pictures.thumbnail.url(card.pictures[0].file);
  return [image];
}

async function encode(file) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

export async function exportStory({ storyId = null } = {}) {
  // Any story exports from where it is read; without one, the open
  // story. With no story at all there is nothing to address, and no id
  // to ask with — so the sentence is COPIED from its home
  // (`backend.story.transfer.services.NOTHING_TO_EXPORT`), which says it
  // in every other case.
  const id = storyId ?? held.story();
  if (id === null) {
    report("Nothing to export yet.");
    return;
  }
  const answer = await api.stories.export.get(id);
  if (!answer.text) {
    // Whatever it refused with, in its own words.
    report(answer.notice);
    return;
  }
  // The filename is the one the backend composed: naming it here would
  // name a file on the SERVER, and the browser saves where the reader
  // says anyway.
  const filename = answer.name;
  saveFile(filename, new Blob([answer.text], { type: "text/markdown" }));
  tell(`Exported ${filename}.`);
}

/* The sites a card's page is fetched from, each with its hosts, the
   backend's (`api.integrations.cards`), read once for every dialog. */
let cardSites = null;

export function wireSupportedSites(dialog, signal) {
  /* A link field's supported sites: a popover, in the top layer over the
     dialog, so the dialog never clips it — opened and closed by its
     toggle (`popovertarget`), and closed too by a click outside it, by
     Esc (going no further, so the dialog stays open), by the dialog
     scrolling or closing and the window resizing. It opens under its
     toggle, its right edge on the toggle's, and above it where the
     window has no room below. */
  const toggle = $("[data-sites]", dialog);
  const list = $(`#${toggle.getAttribute("popovertarget")}`, dialog);
  const hide = () => {
    if (list.matches(":popover-open")) list.hidePopover();
  };
  guard(() => drawSupportedSites(list))();
  list.addEventListener(
    "beforetoggle",
    (event) => {
      if (event.newState !== "open") return;
      const at = toggle.getBoundingClientRect();
      list.style.top = `${at.bottom + _SITES_GAP}px`;
      list.style.right = `${document.documentElement.clientWidth - at.right}px`;
    },
    { signal },
  );
  list.addEventListener(
    "toggle",
    (event) => {
      const open = event.newState === "open";
      toggle.setAttribute("aria-expanded", String(open));
      const at = toggle.getBoundingClientRect();
      if (open && at.bottom + _SITES_GAP + list.offsetHeight > window.innerHeight) {
        list.style.top = `${Math.max(_SITES_GAP, at.top - _SITES_GAP - list.offsetHeight)}px`;
      }
    },
    { signal },
  );
  // a scroll of the dialog's or a field's moves the toggle; the list's own does not
  dialog.addEventListener(
    "scroll",
    (event) => {
      if (!list.contains(event.target)) hide();
    },
    { signal, capture: true },
  );
  dialog.addEventListener("close", hide, { signal });
  window.addEventListener("resize", hide, { signal });
  dialog.addEventListener(
    "keydown",
    (event) => {
      // Enter on the toggle is the toggle's, never the dialog's action
      if (event.key === "Enter" && event.target === toggle) {
        event.stopImmediatePropagation();
        return;
      }
      // before the page's own Esc, which would put the dialog down
      if (event.key !== "Escape" || !list.matches(":popover-open")) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      hide();
    },
    { signal, capture: true },
  );
}

async function drawSupportedSites(list) {
  /* The sites drawn before the list's note, once per dialog: a row each,
     its name and its hosts, the main one first. */
  if ($(".otk-sites__row", list)) return;
  cardSites ??= await api.integrations.cards.list();
  $(".otk-sites__note", list).before(
    ...cardSites.map((site) => {
      const hosts = span("otk-sites__hosts");
      hosts.append(...site.hosts.map((host) => span("otk-sites__domain", host)));
      const row = element("span", "otk-sites__row");
      row.append(span("otk-sites__name", site.name), hosts);
      return row;
    }),
  );
}

export function saveFile(filename, blob) {
  /* A file saved where the browser saves, under `filename`. */
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  // Connected before the click and revoked after the turn of the event
  // loop: a detached link is ignored by some browsers, and revoking in
  // the same tick cancels a download that has not started.
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
