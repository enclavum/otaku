/* The transfer screens: a card in, a story in, a story out.

   The drop-zone dialog carries the whole card import, the persona
   included: who the card's {{user}} is, asked on the same face the card
   is chosen on — prefilled from the story's own memory of it, so a
   story is asked once and later imports follow it.

   A path over HTTP would name a file on the SERVER, so nothing here
   sends one: an import sends what the browser read, a card sends its
   bytes, and an export is saved by the browser where the reader says. */

import * as api from "./api.js";
import { $, element, pickFile } from "./dom.js";
import { ask, closeAll, guard, wiring } from "./browser.js";
import { landed, watchExtraction } from "./shell.js";
import { tell } from "./status.js";

export async function importDocument() {
  const file = await pickFile(".md,.jsonl,.txt");
  if (!file) return;
  const answer = await api.importStory(file.name, await file.text());
  await landed(answer.notice, { redraw: "always" });
  // The memoryless shapes build their memory now, through the same
  // forced pass a manual close runs. A native export arrives with its
  // memory and starts none, so nothing polls for a report never filed.
  if (answer.watching) watchExtraction(answer.story);
}

export async function importCard() {
  /* The card dialog: choose a file, read what the import WILL do, say
     who it speaks to, and only then commit. The outcomes are the
     backend's own sentences about this card — the dialog decides where
     they appear, never what they say. */
  let data = "";
  let chosen = null;
  let prepared = null;
  let renamed = "";

  const choice = await ask("card", (dialog) => {
    const zone = $(".otk-dropzone", dialog);
    const picker = $("input[type=file]", dialog);
    const name = $("#otk-card-name", dialog);
    const persona = $("#otk-card-persona", dialog);
    const personaNote = $("[data-persona-note]", dialog);
    const filename = $("[data-file]", dialog);
    const outcomes = $("[data-outcomes]", dialog);
    const heading = $("[data-outcomes-label]", dialog);
    const note = $("[data-card-note]", dialog);
    name.value = "";
    name.placeholder = "optional rename";
    persona.value = "";
    // Emptied so the SAME file chosen again fires `change`: a card
    // refused as a duplicate is picked again with a rename beside it.
    picker.value = "";
    personaNote.hidden = true;
    filename.hidden = true;
    outcomes.hidden = true;
    heading.hidden = true;
    note.hidden = true;
    zone.classList.remove("is-filled", "is-dragover");

    let taking = 0;
    const take = async (file) => {
      if (!file) return;
      /* Drops can overlap — a second card while the first still encodes
         or prepares — and only the LAST one dropped may become the
         dialog's card: each take carries its turn, and a stale one
         stops before writing anything. */
      const mine = ++taking;
      const bytes = await encode(file);
      if (mine !== taking) return;
      chosen = file;
      data = bytes;
      filename.textContent = file.name;
      filename.hidden = false;
      zone.classList.add("is-filled");
      zone.classList.remove("is-dragover");
      const answer = await api.prepareCard(file.name, bytes, (renamed = name.value.trim()));
      if (mine !== taking) return;
      prepared = answer;
      if (!prepared.card) {
        note.textContent = prepared.notice;
        note.hidden = false;
        // A rename is the way out of a refusal, so the hint stops
        // calling it optional. (Every refusal: the page reads the flag,
        // never the sentence, so a duplicate and a non-card look alike.)
        name.placeholder = "rename";
        arm();
        return;
      }
      name.placeholder = "optional rename";
      // Who the card speaks to: the story's memory of it when there is
      // one, the plain default when there is not — and the note says
      // what the field DOES either way.
      // The ask's default — never over a name the reader already typed.
      if (!persona.value.trim()) persona.value = prepared.card.persona || "you";
      personaNote.hidden = false;
      // What an import always does (the three halves of the product's
      // own rule), and under them what the BACKEND said about this
      // particular card — its own sentences, unchanged.
      outcomes.replaceChildren(
        outcome("send the card's text as your next message, so it is a normal prompt"),
        outcome(`add ${prepared.card.name} to the cast of this story`),
        outcome("play the card's greeting as their first message"),
      );
      outcomes.hidden = false;
      heading.hidden = false;
      const caveats = [...prepared.card.notes];
      // A card that rides every request is worth saying out loud before
      // it is imported, not after.
      if (prepared.card.large) {
        caveats.push(`This card is about ${prepared.card.tokens} tokens, and rides every request.`);
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

    /* The dialog's two MANDATORY fields, enforced: a card file and a
       user name — without both, Import does nothing but say what is
       missing (a dialog button is no form submit, so the field's
       `required` needs this hand-wiring). No file: the note asks for
       one. A name emptied: the field says so — it binds `{{user}}`
       everywhere, and a silent default would decide it for the reader.
       A file that refused to read: the refusal already on screen stays
       the answer until the rename changes. Wired before `ask` adds its
       own listeners, so the stop is the whole veto — the button and
       the dialog's Enter alike; `wiring` drops the last opening's veto
       with its stale closures. */
    const signal = wiring(dialog);
    const importButton = $('[data-choice="import"]', dialog);
    /* The button SAYS it: disabled until a user name is typed AND a
       card is in hand — read, or refused with a NEW rename typed, which
       is another read waiting to happen. The name is asked in every
       state: a refusal moves nothing onto the rename field. */
    const arm = () => {
      const another = name.value.trim() !== renamed;
      importButton.disabled = !(chosen && persona.value.trim() && (prepared?.card || another));
    };
    arm();
    persona.addEventListener("input", arm, { signal });
    name.addEventListener("input", arm, { signal });
    const veto = (event) => {
      // Settle only with a read card, a user name, and a rename that IS
      // the read one: every other press stays in the dialog. A changed
      // rename re-prepares HERE for both a refused and a read card, so a
      // refusal always answers into the open dialog, never past it.
      if (prepared?.card && persona.value.trim() && name.value.trim() === renamed) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      if (!chosen) {
        note.textContent = "Choose a card file first.";
        note.hidden = false;
      } else if (!persona.value.trim()) {
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
          if (prepared?.card && persona.value.trim()) importButton.click();
        })();
      }
      // a refused read under an unchanged name keeps its refusal shown
    };
    $('[data-choice="import"]', dialog).addEventListener("click", veto, { signal });
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

  if (choice !== "import" || !chosen) return;
  const wanted = $("#otk-card-name")?.value.trim() ?? "";
  // The name may have been typed after the card was read: the prepared
  // one is bound to the name it was prepared with, so a changed name
  // means preparing again.
  if (!prepared?.card || wanted !== renamed) {
    prepared = await api.prepareCard(chosen.name, data, wanted);
  }
  if (!prepared.card) {
    tell(prepared.notice, "otk-error");
    return;
  }
  // Never empty here: Import settles only through the veto above, and
  // no default stands in for a name — the prefill IS the default.
  const persona = $("#otk-card-persona").value.trim();
  const { notice } = await api.addCard(prepared.token, persona);
  // The card is in: the screen it was asked from goes, as a confirmed
  // question's does, and the landing is read in the flow. A cancelled
  // dialog left that screen exactly as it was.
  closeAll();
  await landed(notice, { redraw: "always" });
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
  // (`backend.api.transfer.export`), which says it in every other case.
  const id = storyId ?? (await api.facts()).story_id;
  if (id === null) {
    tell("Nothing to export yet.", "otk-error");
    return;
  }
  const answer = await api.exportDocument(id);
  if (!answer.text) {
    // Whatever it refused with, in its own words.
    tell(answer.notice, "otk-error");
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
