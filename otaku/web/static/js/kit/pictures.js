/* A turn's pictures, wherever the page shows a turn: the tiles under it,
   and the picture at full size that a tile opens. The transcript and the
   dossier's reader draw the same tiles from the same row facts, so the
   drawing lives once, below both. Nothing here knows which screen asked. */

import { api } from "../api/index.js";
import { $, element } from "./dom.js";

export function thumbs(attachments) {
  /* One tile per picture, the thumbnail the store cut for it; a tile is
     a way in to the picture at full size. The alt text is the measure —
     no name is stored, and the reader of a screen reader gets what the
     row knows. */
  const strip = element("div", "otk-thumbs");
  attachments.forEach((picture, at) => {
    const tile = element("button", "otk-thumb");
    tile.type = "button";
    tile.setAttribute("aria-label", `picture, ${picture.width} × ${picture.height}, open`);
    const image = element("img");
    image.alt = "";
    image.loading = "lazy";
    image.src = api.pictures.thumbnail.url(picture.file);
    tile.append(image);
    tile.onclick = () => openPicture(attachments, at);
    strip.append(tile);
  });
  return strip;
}

export function openPicture(attachments, at) {
  /* The file as the model saw it, on the desk: esc closes it as any
     dialog, Close and a click on the desk do the same. The measure
     under it is the row's — width by height, and the bytes stored. A
     turn with several pictures is walked from here, by the arrows on
     the picture's sides or the arrow keys, clamped at the ends — in a
     window as wide as the widest of them and as tall as the tallest
     needs, so a flip never changes its size. */
  const dialog = $('dialog[data-dialog="picture"]');
  const frame = $(".otk-lightbox__frame", dialog);
  const image = $("img", frame);
  const prev = $("[data-prev]", dialog);
  const next = $("[data-next]", dialog);
  const several = attachments.length > 1;
  const show = (index) => {
    at = index;
    const picture = attachments[at];
    // The row knows the picture's measure, so the frame takes its size
    // before a byte arrives — on a slow machine the dialog would otherwise
    // open as tall as its arrows and grow when the picture lands.
    image.width = picture.width;
    image.height = picture.height;
    image.src = api.pictures.url(picture.file);
    $(".otk-lightbox__open", dialog).href = api.pictures.url(picture.file);
    $(".otk-lightbox__meta", dialog).textContent = measure(picture);
    // one picture alone has no count, and its measure takes the count's place
    const count = $(".otk-lightbox__count", dialog);
    count.textContent = several ? `${at + 1} of ${attachments.length}` : "";
    count.hidden = !several;
    prev.hidden = next.hidden = !several;
    prev.setAttribute("aria-disabled", String(at === 0));
    next.setAttribute("aria-disabled", String(at === attachments.length - 1));
  };
  const step = (by) => {
    const to = at + by;
    if (to >= 0 && to < attachments.length) show(to);
  };
  const wired = new AbortController();
  const down = () => {
    wired.abort();
    if (dialog.open) dialog.close();
    image.removeAttribute("src");
  };
  $("[data-close]", dialog).addEventListener("click", down, { signal: wired.signal });
  prev.addEventListener("click", () => step(-1), { signal: wired.signal });
  next.addEventListener("click", () => step(1), { signal: wired.signal });
  dialog.addEventListener(
    "keydown",
    (event) => {
      if (event.key === "ArrowLeft") step(-1);
      else if (event.key === "ArrowRight") step(1);
      else return;
      event.preventDefault();
    },
    { signal: wired.signal },
  );
  dialog.addEventListener("click", (event) => event.target === dialog && down(), { signal: wired.signal });
  dialog.addEventListener("close", down, { signal: wired.signal });
  show(at);
  dialog.showModal();
  fit(attachments, dialog, frame, image);
}

function fit(attachments, dialog, frame, image) {
  /* `frame` as wide as the widest of `attachments` and as tall as the
     tallest, each shown at its own size, never enlarged, and held as the
     stylesheet holds `image`, the one shown: to the frame's widest (its
     `max-width`) and to the window (`max-height`, `.otk-lightbox img`).
     Never narrower than the foot needs for the longest measure among
     them, which would leave the dialog wider than its frame. Measured
     with the dialog open. */
  frame.style.width = frame.style.minHeight = "";
  const room = frame.clientWidth;
  const held = parseFloat(getComputedStyle(image).maxHeight) || Infinity;
  const shown = attachments.map((each) => Math.min(1, room / each.width, held / each.height));
  const foot = $(".otk-lightbox__foot", dialog);
  const meta = $(".otk-lightbox__meta", dialog);
  const said = meta.textContent;
  foot.style.width = "max-content";
  let least = 0;
  for (const each of attachments) {
    meta.textContent = measure(each);
    least = Math.max(least, foot.offsetWidth);
  }
  meta.textContent = said;
  foot.style.width = "";
  const widest = Math.max(...attachments.map((each, at) => each.width * shown[at]));
  frame.style.width = `${Math.ceil(Math.min(room, Math.max(least, widest)))}px`;
  frame.style.minHeight = `${Math.ceil(Math.max(...attachments.map((each, at) => each.height * shown[at])))}px`;
}

function measure(picture) {
  // The foot's measure of a picture: width by height, and the bytes stored.
  return `${picture.width} × ${picture.height} · ${sized(picture.size)}`;
}

function sized(bytes) {
  // Decimal, as a phone and the intake's own limit count it.
  return bytes >= 1_000_000 ? `${(bytes / 1_000_000).toFixed(1)} MB` : `${Math.round(bytes / 1000)} KB`;
}
