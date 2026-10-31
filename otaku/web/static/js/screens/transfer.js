/* The transfer screens: a card in, a story in, a story out.

   The drop-zone dialog carries the whole card import into the story —
   a file, which goes into All characters too, or a codex card invited
   where a file would be — the persona included: who the card's {{user}} is, asked on
   the same face the card is chosen on, prefilled from the story's own
   memory of it, so a story is asked once and later imports follow it.

   A path over HTTP would name a file on the SERVER, so nothing here
   sends one: an import sends what the browser read, a card sends its
   bytes, and an export is saved by the browser where the reader says. */

import { api } from "../api/index.js";
import { $, $$, element, pickFile, span } from "../kit/dom.js";
import { ask, closeAll, footnote, guard, report, wiring } from "../kit/browser.js";
import * as held from "../play/held.js";
import { landed, watchExtraction } from "../play/shell.js";
import { tell } from "../kit/status.js";

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

export async function importCard({ cardId = null } = {}) {
  /* The card dialog: choose a file — or bring in the codex card
     `cardId`, shown where the file would be — read what the import WILL
     do, say who it speaks to, and only then commit, into the story (a
     file into All characters too; a card file into All characters alone
     is dropped on its new card tile). The outcomes are the backend's own
     sentences about this card beside the page's lines about what an
     import does — the dialog decides where they appear, never what the
     backend's say. A refusal is said in the dialog's foot (`report`). */
  const inviting = cardId !== null;
  const invited = inviting ? await api.cards.get(cardId) : null;
  if (invited?.refused) {
    report(invited.notice);
    return;
  }
  let data = "";
  let chosen = null;
  let prepared = null;
  let renamed = "";
  // the codex card the reader says the file is ("" for a new one), and
  // whether the card's picture goes into the context
  let reuse = null;
  let sending = false;
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
  /* The card read and vetted, nothing stored: `{card}` — its name, the
     backend's notes, whether it carries a picture, the codex card it is
     already and those of its name, its size in tokens, whether that is
     worth a warning, and the persona the story remembers
     — or the refusal. */
  const prepare = async (fileName, bytes, rename) => {
    const story = await storyFor();
    if (story.refused) return story;
    const fields = inviting ? { card_id: cardId, rename } : { data: bytes, file_name: fileName, rename };
    const check = await api.stories.cards.create(story.id, fields, { validateOnly: true });
    // the story held, deleted elsewhere: read again for one made for it
    if (check.refused && check.status === 404 && (await held.lost(story.id))) {
      return prepare(fileName, bytes, rename);
    }
    return check.refused ? check : { card: { ...check, tokens: check.block_tokens } };
  };

  const choice = await ask("card", (dialog) => {
    const zone = $(".otk-dropzone", dialog);
    const picker = $("input[type=file]", dialog);
    const card = $("[data-chosen]", dialog);
    const name = $("#otk-card-name", dialog);
    const persona = $("#otk-card-persona", dialog);
    const personaNote = $("[data-persona-note]", dialog);
    const filename = $("[data-file]", dialog);
    const similarLabel = $("[data-similar-label]", dialog);
    const similar = $("[data-similar]", dialog);
    const picsend = $("[data-picsend]", dialog);
    const picsendThumb = $("[data-picsend-thumb]", dialog);
    const picsendSwitch = $("[data-picsend-switch]", dialog);
    const outcomes = $("[data-outcomes]", dialog);
    const heading = $("[data-outcomes-label]", dialog);
    const note = $("[data-card-note]", dialog);
    const escape = $("[data-card-escape]", dialog);
    const importButton = $('[data-choice="import"]', dialog);
    $("[data-title]", dialog).textContent = inviting ? "Invite" : "Import";
    importButton.textContent = inviting ? "Invite" : "Import";
    name.value = "";
    name.placeholder = "optional rename";
    persona.value = "";
    // Emptied so the SAME file chosen again fires `change`: a card
    // refused as a duplicate is picked again with a rename beside it.
    picker.value = "";
    zone.hidden = inviting;
    card.hidden = !inviting;
    if (inviting) card.replaceChildren(...chosenCard(invited));
    personaNote.hidden = true;
    filename.hidden = true;
    similar.hidden = similarLabel.hidden = true;
    picsend.hidden = true;
    outcomes.hidden = true;
    heading.hidden = true;
    note.hidden = true;
    zone.classList.remove("is-filled", "is-dragover");

    sending = false;
    picsendSwitch.setAttribute("aria-checked", "false");
    picsendSwitch.onclick = () => {
      sending = !sending;
      picsendSwitch.setAttribute("aria-checked", String(sending));
    };

    let taking = 0;
    const take = async (file) => {
      if (!file && !inviting) return;
      /* Drops can overlap — a second card while the first still encodes
         or prepares — and only the LAST one dropped may become the
         dialog's card: each take carries its turn, and a stale one
         stops before writing anything. */
      const mine = ++taking;
      const bytes = file ? await encode(file) : "";
      if (mine !== taking) return;
      if (file) {
        chosen = file;
        data = bytes;
        filename.textContent = file.name;
        filename.hidden = false;
        zone.classList.add("is-filled");
        zone.classList.remove("is-dragover");
      }
      const answer = await prepare(file?.name ?? "", bytes, (renamed = name.value.trim()));
      if (mine !== taking) return;
      prepared = answer;
      if (!prepared.card) {
        // the refusal is the dialog's error, said in its foot
        report(prepared.notice);
        note.hidden = true;
        similar.hidden = similarLabel.hidden = picsend.hidden = true;
        outcomes.hidden = heading.hidden = true;
        // A rename is the way out of a refusal, so the hint stops
        // calling it optional. (Every refusal: the page reads the flag,
        // never the sentence, so a duplicate and a non-card look alike.)
        name.placeholder = "rename";
        arm();
        return;
      }
      name.placeholder = "optional rename";
      footnote(dialog, ""); // a refusal standing goes with the card read
      const read = prepared.card;
      // Who the card speaks to: the story's memory of it when there is
      // one, the plain default when there is not — and the note says what
      // the field DOES either way. The ask's default — never over a name
      // the reader already typed.
      if (!persona.value.trim()) persona.value = read.persona || "you";
      personaNote.hidden = false;
      /* A card of its name the codex holds — not this file: the reader
         says whether it is that card, or a new one beside it. */
      reuse = null;
      const offered = !inviting && !read.same_card && read.similar_cards.length ? read.similar_cards : [];
      similar.replaceChildren(
        ...offered.map((one) =>
          choiceRow(`same:${one.id}`, `Same as ${one.name}`, one.tagline || "already in All characters"),
        ),
        ...(offered.length ? [choiceRow("new", "A new card", "Saved beside it")] : []),
      );
      const pick = (option) => {
        reuse = option.dataset.select === "new" ? null : Number(option.dataset.select.slice(5));
        for (const each of $$("[data-select]", similar)) {
          each.setAttribute("aria-checked", String(each === option));
        }
      };
      for (const option of $$("[data-select]", similar)) option.onclick = () => pick(option);
      const fresh = $('[data-select="new"]', similar);
      if (fresh) pick(fresh);
      similar.hidden = similarLabel.hidden = !offered.length;
      // The picture switch, only for a card with a picture.
      picsend.hidden = !read.has_picture;
      picsendThumb.replaceChildren(...pictureOf(inviting ? invited : read.same_card, chosen));
      escape.textContent = !picsend.hidden ? "the picture is saved either way" : "joins the current story";
      // What an import always does (the three halves of the product's
      // own rule), and under them what the BACKEND said about this
      // particular card — its own sentences, unchanged.
      outcomes.replaceChildren(
        outcome("send the card's text as your next message, so it is a normal prompt"),
        outcome(`add ${read.name} to the cast of this story`),
        outcome("play the card's greeting as their first message"),
        ...(inviting
          ? []
          : [
              outcome(
                read.same_card
                  ? `use ${read.same_card.name} from All characters, linked`
                  : "save the card to All characters, linked",
              ),
            ]),
      );
      outcomes.hidden = false;
      heading.hidden = false;
      const caveats = [...read.notes];
      // A card that rides every request is worth saying out loud before
      // it is imported, not after.
      if (read.large) {
        caveats.push(`This card is about ${read.tokens} tokens, and rides every request.`);
      }
      note.textContent = caveats.join(" ");
      note.hidden = !caveats.length;
      arm();
    };

    picker.onchange = guard(() => take(picker.files[0]));
    zone.ondragover = (event) => {
      event.preventDefault();
      zone.classList.add("is-dragover");
    };
    zone.ondragleave = () => zone.classList.remove("is-dragover");
    zone.ondrop = guard((event) => {
      event.preventDefault();
      return take(event.dataTransfer.files[0]);
    });

    /* The dialog's MANDATORY fields, enforced: a card — a file, or the
       codex card — and a user name: without them, the
       button does nothing but say what is missing (a dialog button is no
       form submit, so the field's `required` needs this hand-wiring). No
       file: the note asks for one. A name emptied: the field says so —
       it binds `{{user}}` everywhere, and a silent default would decide
       it for the reader. A card that refused to read: the refusal
       already on screen is the answer, and the rename is the way out.
       Wired before `ask` adds its own listeners, so the stop is the
       whole veto — the button and the dialog's Enter alike; `wiring`
       drops the last opening's veto with its stale closures. */
    const signal = wiring(dialog);
    const named = () => persona.value.trim();
    /* The button SAYS it: disabled until a card is in hand — read, or
       refused with a NEW rename typed, which is another read waiting to
       happen — and a user name is typed. */
    const arm = () => {
      const another = name.value.trim() !== renamed;
      importButton.disabled = !((chosen || inviting) && named() && (prepared?.card || another));
    };
    arm();
    persona.addEventListener("input", arm, { signal });
    name.addEventListener("input", arm, { signal });
    const veto = (event) => {
      // Settle only with a read card, a user name, and
      // a rename that IS the read one: every other press stays in the
      // dialog. A changed rename re-prepares HERE for both a refused and
      // a read card, so a refusal always answers into the open dialog,
      // never past it.
      if (prepared?.card && named() && name.value.trim() === renamed) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      if (!chosen && !inviting) {
        note.textContent = "Choose a card file first.";
        note.hidden = false;
      } else if (!named()) {
        // Asked BEFORE any re-read: the read's prefill must never
        // answer for a reader who left the field empty.
        persona.reportValidity();
      } else if (name.value.trim() !== renamed) {
        /* The door out of a refusal the terminal always had
           (`/card FILE NAME`): the new name is another read.
           Re-prepare under it and press again when it takes — a name
           refused again keeps the dialog open with its fresh answer. */
        guard(async () => {
          await take(chosen);
          if (prepared?.card && named()) importButton.click();
        })();
      }
      // a refused read under an unchanged name stays refused
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
    // A codex card is in hand from the start: read it now.
    if (inviting) guard(() => take(null))();
  });

  if (choice !== "import" || (!chosen && !inviting)) {
    if (made !== null) await held.takeBack(made);
    return;
  }
  const wanted = $("#otk-card-name")?.value.trim() ?? "";
  // The name may have been typed after the card was read: the prepared
  // one is bound to the name it was prepared with, so a changed name
  // means preparing again.
  if (!prepared?.card || wanted !== renamed) {
    prepared = await prepare(chosen?.name ?? "", data, wanted);
  }
  if (!prepared.card) {
    if (made !== null) await held.takeBack(made);
    report(prepared.notice);
    return;
  }
  // Never empty here: Import settles only through the veto above, and
  // no default stands in for a name — the prefill IS the default.
  const persona = $("#otk-card-persona").value.trim();
  const story = held.story();
  const fields = inviting
    ? { card_id: cardId, rename: wanted, persona, with_pictures: sending }
    : { data, file_name: chosen.name, rename: wanted, persona, reuse, with_pictures: sending };
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

function chosenCard(card) {
  /* A codex card where a file would be dropped: its portrait, its name
     and its tagline. */
  const frame = span("otk-charrow__pic");
  frame.append(...pictureOf(card, null));
  const text = element("span", "otk-charrow__text");
  text.append(span("otk-tile__name", card.name), span("otk-tile__tag", card.tagline));
  return [frame, text];
}

function pictureOf(card, file) {
  /* The picture a card would send: a codex card's portrait, else the PNG
     file in hand, read in this browser. */
  const image = element("img");
  image.alt = "";
  if (card?.pictures?.length) image.src = api.pictures.thumbnail.url(card.pictures[0].file);
  else if (file) image.src = URL.createObjectURL(file);
  else return [];
  return [image];
}

function choiceRow(value, name, description) {
  const option = element("button", "otk-choice");
  option.type = "button";
  option.setAttribute("role", "radio");
  option.setAttribute("aria-checked", "false");
  option.dataset.select = value;
  option.append(span("otk-choice__name", name), span("otk-choice__desc", description));
  return option;
}

function outcome(sentence) {
  return element("p", "otk-outcome", sentence);
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
  const url = URL.createObjectURL(new Blob([answer.text], { type: "text/markdown" }));
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
  tell(`Exported ${filename}.`);
}
