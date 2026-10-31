/* The page over the story it plays: what it asks for at boot, and every
   DOM listener it ever registers — both in one place, so "what is wired
   when this loads?" is answered by reading one function.

   The three rules the medium forces on us, kept where they are enforced:

   - A screen is opened by a BUTTON, and every one of them goes through
     `commands.run`. A button carries the token the contents rail keys
     by, never behaviour of its own — and never its label as a decision,
     which is why every one of them has a `data-` attribute instead.
   - Esc is ours, not the dialog's. A native <dialog> closes itself on
     Esc before anything else is asked, which would collapse every depth
     into one: clearing a filter and leaving a field editor would both
     shut the whole popup. So `cancel` is refused and Esc is routed here.
   - Bodies arrive verbatim. Nothing on this side rewrites the text. */

import { api, login, whenUnauthorized } from "./js/api/index.js";
import { closeAll, topmost } from "./js/kit/browser.js";
import { editTurn, keepsScreen, openCodex, openTab, run as runCommand } from "./js/commands.js";
import { showSignOut, signIn, signOut } from "./js/screens/login.js";
import { draft, focusComposer, primeHistory, submit, wire as wireComposer } from "./js/composer.js";
import { $, $$, watchTextareas } from "./js/kit/dom.js";
import * as held from "./js/play/held.js";
import {
  disconnected,
  drawPlayed,
  facts,
  landed,
  showContextUse,
  showFacts,
  watchForChanges,
  watchServer,
  wireFullscreen,
  wireLayout,
  wireTheme,
} from "./js/play/shell.js";
import { load as loadLanguage } from "./js/play/language.js";
import { whenEdited } from "./js/play/transcript.js";

async function boot() {
  try {
    /* Before anything is drawn: a password asked for and this browser
       not through it means the dialog comes first, rather than four
       reads refused in the background. A cookie that runs out LATER is
       caught at the request it refuses (`whenUnauthorized`). */
    const signing = await login.get();
    showSignOut(signing.required);
    // The dialog opens as `signIn` is called, so it is already in front
    // when the shell is shown behind it.
    const signingIn = signing.required && !signing.signed_in ? signIn() : null;
    shown();
    if (signingIn) await signingIn;
    // the story a launch resumes — this page plays it until it lands in another
    await held.resume();
    const [read, language, history] = await Promise.all([
      facts(),
      api.language.get(),
      api.history.list(),
    ]);
    loadLanguage(language);
    showFacts(read);
    await Promise.all([drawPlayed(read.story_id), showContextUse()]);
    primeHistory(history);
    disconnected(false);
  } catch {
    shown();
    disconnected();
  }
}

/** The shell, shown once the page knows what comes first (`data-booting`). */
function shown() {
  delete document.documentElement.dataset.booting;
}

function start() {
  whenUnauthorized(signIn);
  // the transcript draws a turn as an editor and hands the write here
  whenEdited(editTurn);
  wireComposer();
  wireTheme();
  // a layout written lands like every other write: the facts again,
  // the mode among them
  wireLayout(landed);
  wireFullscreen();
  watchTextareas();
  /* The page opens with the caret where the story is written, so the
     first keystroke is the first word. Not on a phone: a focused box
     there raises the keyboard over half the screen before the reader
     has read a line, and a tap on the box is one tap away. */
  if (!window.matchMedia("(pointer: coarse)").matches) focusComposer();
  /* One stream, opened once and held: what is on disk is what the
     browser has, so a page whose files changed under it replaces
     itself. Not in `boot`, which runs again every time otaku comes
     back — that would leave a stream open per restart. */
  watchForChanges();
  /* The heartbeat and the connected mark, both directions — the page
     finds out that otaku stopped, and that it is back, in which case
     `boot` runs again: a restarted otaku may resume another story. */
  watchServer(boot);

  // Every command button, wherever it is: the contents, a panel's
  // header, the turn bar beside the composer.
  document.addEventListener("click", (event) => {
    const command = event.target.closest("[data-command]");
    if (command) {
      /* A command carried by a panel's own chrome — the dossier's
         "Extract now", its "← All stories" — answers into the flow,
         which is behind the modal. Close first, so the reader sees what
         it did. Unless it ASKS first: a question belongs over the
         screen it was asked from, and a cancelled one must leave that
         screen exactly as it was (`commands.confirmed` closes them once
         it is answered). */
      if (command.closest("dialog[open]") && !keepsScreen(command.dataset.command)) closeAll();
      runCommand(command.dataset.command);
      foldRail();
      return;
    }
    // The contents row that leaves the session rather than opening
    // anything: not a command, since the terminal has none to leave.
    if (event.target.closest("[data-sign-out]")) {
      foldRail();
      signOut();
      return;
    }
    // The contents row of a screen with no command yet: All characters.
    if (event.target.closest("[data-characters]")) {
      openCodex();
      foldRail();
      return;
    }
    // The contents rows that are no command: a tab of the open story's
    // dossier — its messages, its tools.
    const goto = event.target.closest("button[data-goto]");
    if (goto) {
      openTab(goto.dataset.goto);
      foldRail();
      return;
    }
    if (event.target.closest("#otk-rail-toggle")) {
      toggleRail();
      return;
    }
    // A layout taken (`shell.wireLayout` writes it): the story is read
    // another way, and the next line is still typed — the caret comes
    // back to the box.
    if (event.target.closest("[data-transcript-mode]")) {
      refocus();
      return;
    }
    // The model's question: an option picked is the line, sent, and the
    // caret comes back to the box for the next; copied, it lands in the
    // box to be edited first — on the canvas in the text, the question
    // going (`transcript.draftTail`).
    const pick = event.target.closest("[data-ask-send]");
    if (pick) {
      submit(pick.dataset.askSend);
      refocus();
      return;
    }
    const copy = event.target.closest("[data-ask-copy]");
    if (copy) {
      draft(copy.dataset.askCopy);
      return;
    }
    // The two verbs beside the composer, and the "try again" a failure
    // offers. A verb with nothing to act on is marked, not removed —
    // `aria-disabled` keeps it in the row and out of reach.
    const turn = event.target.closest("[data-turn]");
    if (turn && turn.getAttribute("aria-disabled") !== "true") {
      runCommand(turn.dataset.turn === "undo" ? "/undo" : "/regen");
      // Either verb is about the last exchange, and the next one is
      // typed: the caret comes back to the box.
      refocus();
    }
    // Revert, the first of the verbs: what the last Undo or Regen took, put
    // back — pressed by ctrl+y on the canvas too, hidden or not.
    if (event.target.closest("[data-revert]")) {
      runCommand("/revert");
      refocus();
    }
  });
  // Revert takes itself away once its fade has run out.
  document.addEventListener("animationend", (event) => {
    if (event.animationName === "otk-revert-fade") event.target.hidden = true;
  });

  for (const dialog of $$("dialog")) {
    dialog.addEventListener("cancel", (event) => event.preventDefault());
    $(".otk-close", dialog)?.addEventListener("click", () => dialog.close());
    /* Leaving a screen puts the reader back where the story is written.
       Only when nothing else is up: closing a dialog that was opened
       over a panel returns to the panel, not past it to the page. */
    dialog.addEventListener("close", () => {
      if (!$("dialog[open]")) focusComposer();
    });
    /* The scrim closes it, as a scrim does — pressed AND let go there: a
       selection dragged past the edge is let go on the dialog too. Never
       while a field in it is being edited, which would lose the words. A
       click on the dialog's own padding reports the dialog as its target
       too, so the test is where the pointer WAS: outside the panel's box
       (a keyboard-activated button reports the button, never this). */
    let pressedOutside = false;
    dialog.addEventListener("pointerdown", (event) => {
      pressedOutside = onScrim(dialog, event);
    });
    dialog.addEventListener("click", (event) => {
      const outside = pressedOutside && onScrim(dialog, event);
      pressedOutside = false;
      // A fixed dialog has nothing behind it to fall back to.
      if (!outside || dialog.hasAttribute("data-fixed")) return;
      if (!$(".otk-edit[data-editing]", dialog)) dialog.close();
    });
  }

  document.addEventListener("keydown", onKey);
  /* A file let go where no target takes it — the composer included —
     would have the browser open it in this tab, the draft, the staged
     pictures and every open editor going with the page. A target that
     takes files has claimed the drag before it reaches here. */
  document.addEventListener("dragover", (event) => {
    if (event.defaultPrevented || !event.dataTransfer?.types.includes("Files")) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "none";
  });
  document.addEventListener("drop", (event) => {
    if (!event.defaultPrevented && event.dataTransfer?.types.includes("Files")) event.preventDefault();
  });
}

/* The contents fold away on a narrow window and the spine carries the
   toggle; a row taken folds them back, so the panel it opened is not
   behind a drawer. */
function toggleRail(force) {
  const rail = $("#otk-rail");
  const toggle = $("#otk-rail-toggle");
  if (!rail || !toggle) return;
  const open = force !== undefined ? force : rail.dataset.open !== "true";
  rail.dataset.open = String(open);
  toggle.setAttribute("aria-expanded", String(open));
}

function foldRail() {
  toggleRail(false);
}

/* The caret back in the composer after a click that ends with the next
   line to type — not on a touch screen, where the focus raises the
   keyboard over the reply about to be read. */
function refocus() {
  if (!window.matchMedia("(pointer: coarse)").matches) focusComposer();
}

function onKey(event) {
  /* Escape, and nothing else that DOES anything. There are no keyboard
     shortcuts on this page: every command is a button and a line you
     can type, and a key that does something a reader cannot see is a
     key nobody finds. What the KEYBOARD still answers is what a screen
     advertises in its own footer — the arrows in a list, `/` for its
     filter, enter to take a row — and those belong to the screen, not
     to the document. */
  const verbKey = event.key === "r" || event.key === "u";
  if (verbKey && event.ctrlKey && !event.metaKey && !event.altKey) {
    /* The prompt's two verbs (composer.js: ctrl+r regenerates, ctrl+u
       undoes) are the browser's reload and view-source. Pressed with
       the focus anywhere ELSE on this page they reach neither: a reload
       for a reader who meant a regen throws the page away, and the
       verbs answer in the prompt alone. ⌘R stays the browser's. */
    event.preventDefault();
    return;
  }
  if (event.key === "Escape") {
    // The one on TOP — `dialog[open]` in markup order would close
    // whatever happens to be written first.
    const dialog = topmost();
    if (!dialog) {
      foldRail();
      return;
    }
    event.preventDefault();
    // Nor does Esc close one: under it is a page that cannot answer.
    if (dialog.hasAttribute("data-fixed")) return;
    /* The depths inside a popup — a filter, a field editor, a confirm —
       claim Esc before it reaches here. What is left is
       the outermost level: the popup itself. */
    dialog.close();
    return;
  }
}

/** Whether a pointer event on a dialog fell on its scrim: the dialog
    itself the target, the point outside its box. */
function onScrim(dialog, event) {
  if (event.target !== dialog) return false;
  const box = dialog.getBoundingClientRect();
  return (
    event.clientX < box.left ||
    event.clientX > box.right ||
    event.clientY < box.top ||
    event.clientY > box.bottom
  );
}

start();
boot();
