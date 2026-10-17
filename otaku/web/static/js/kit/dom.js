/* Making and finding elements. Nothing here knows what otaku is. */

export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

// The stroke of the page's line icons drawn in script: a picture's flip
// chevrons, the mark that opens a card.
export const LINE_STROKE = {
  fill: "none",
  stroke: "currentColor",
  "stroke-width": "1.45",
  "stroke-linecap": "round",
  "stroke-linejoin": "round",
};

export function element(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined) el.textContent = text;
  return el;
}

export function span(className, text) {
  return element("span", className, text);
}

export function row(...parts) {
  const button = element("button", "otk-row");
  button.type = "button";
  button.setAttribute("role", "option");
  button.append(...parts);
  return button;
}

/** One verb of an action row. `onclick` arrives already answered-for:
    this module knows nothing about screens, guards included. */
export function actionButton(label, { kind = "", off = false, onclick } = {}) {
  const button = element("button", kind ? `otk-btn ${kind}` : "otk-btn", label);
  button.type = "button";
  button.disabled = off;
  if (onclick) button.onclick = onclick;
  return button;
}

/** An SVG element and its attributes. */
export function svgElement(tag, attributes) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  return node;
}

/** A switch, on or off by `aria-checked` as `checked` says, named for a
    screen reader by `label`; what pressing it does is the caller's. */
export function switchButton(label, checked) {
  const button = element("button", "otk-switch otk-switch--h");
  button.type = "button";
  button.setAttribute("role", "switch");
  button.setAttribute("aria-checked", String(checked));
  button.setAttribute("aria-label", label);
  button.append(span("otk-switch__knob"));
  return button;
}

// ---------- textareas that size to their text ----------

// Safari has no `field-sizing: content` yet; where it exists this stays
// out of the way entirely.
const NEEDS_AUTOSIZE = !CSS.supports("field-sizing", "content");

export function autosize(field) {
  if (!NEEDS_AUTOSIZE) return;
  field.style.height = "auto";
  field.style.height = `${field.scrollHeight}px`;
}

/* Setting .value fires no input event, so every programmatic write says
   so itself — a command prefilling the composer, an editor opening on
   existing text. Without this the box opens one line tall with the text
   scrolled out of sight. */
export function setValue(field, text) {
  field.value = text;
  autosize(field);
}

export function watchTextareas() {
  if (!NEEDS_AUTOSIZE) return;
  document.addEventListener("input", (event) => {
    if (event.target.matches("textarea")) autosize(event.target);
  });
  $$("textarea").forEach(autosize);
}

/** The machine's own file dialog, as a promise: the picked file, or null
    if the reader closed it. Cancelling fires `cancel`, not `change` —
    without that arm the promise never settles and its caller waits
    forever. Two screens open it (a story to import, a premise to read),
    which is why it lives here rather than in either of them. */
export function pickFile(accept) {
  return pickFiles(accept, { several: false }).then((files) => files[0] ?? null);
}

/** The same dialog for several files at once — the pictures a line
    attaches — as an array, empty if the reader closed it. */
export function pickFiles(accept, { several = true } = {}) {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = accept;
    input.multiple = several;
    /* In the document while it asks, though never seen: iOS Safari
       answers a click on an input that is not in the document with
       nothing at all, and the promise would never settle. */
    input.hidden = true;
    const settle = (files) => {
      input.remove();
      resolve(files);
    };
    input.onchange = () => settle([...input.files]);
    input.oncancel = () => settle([]);
    document.body.append(input);
    input.click();
  });
}

/** One picked file as the play body carries it: the name and the bytes
    as base64 — the data URL's payload, which is how a browser encodes a
    file without touching its bytes. No type: the backend tells it from
    the bytes, never from what the browser reported. */
export function encodeFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error);
    reader.onload = () =>
      resolve({
        name: file.name,
        data: String(reader.result).split(",", 2)[1] ?? "",
      });
    reader.readAsDataURL(file);
  });
}
