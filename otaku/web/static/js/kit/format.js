/* Dates and numbers as the page spells them — the row, preview and
   report spellings the screens share. Nothing here knows what otaku is. */

/** The preview's fuller date: the weekday earns its place because a
    story is remembered as "that Saturday", not as a number. */
export function when(iso) {
  const d = new Date(iso);
  const pad = (n) => String(n).padStart(2, "0");
  const day = d.toLocaleDateString("en-US", { weekday: "short" });
  return `${day} ${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function ago(iso) {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  for (const [unit, size] of [["d", 86400], ["h", 3600], ["m", 60]]) {
    if (seconds >= size) return `${Math.floor(seconds / size)}${unit} ago`;
  }
  return "just now";
}

/** The same distance in a column's width: "2h", "3d", "now". */
export function since(iso) {
  const worded = ago(iso);
  return worded === "just now" ? "now" : worded.replace(" ago", "");
}

/** One line of a text, as much as the limit holds — for a row that cuts
    with an ellipsis rather than wrapping. */
export function excerpt(text, limit = 120) {
  const line = text.replace(/\s+/g, " ").trim();
  return line.length > limit ? `${line.slice(0, limit)}…` : line;
}

/* How wide a story's name is written, anywhere on the page. The backend
   cuts to the same number inside its own sentences
   (`otaku.formatting.LABEL_WIDTH`), so a name reads the same length
   whether the page drew it or otaku said it. */
const LABEL_WIDTH = 50;
const _FORK_NUMBER = / - \d+$/;

/** A story's name, cut for one line — the RULE `formatting.truncate_label`
    holds for the terminal, copied here because the two languages cannot
    share a function: flattened, at most `limit` characters, and a trailing
    fork number (" - 2") survives the cut, because it is the only thing
    telling two copies of one story apart and it sits exactly where the cut
    would land. */
export function label(text, limit = LABEL_WIDTH) {
  const flat = String(text ?? "").replace(/\s+/g, " ").trim();
  const cut = (line, n) => (line.length <= n ? line : n <= 1 ? line.slice(0, n) : `${line.slice(0, n - 1)}…`);
  const number = flat.match(_FORK_NUMBER);
  if (!number) return cut(flat, limit);
  return `${cut(flat.slice(0, number.index), limit - number[0].length)}${number[0]}`;
}

/** A number as the reports write them: grouped, so a token count can be
    read at a glance. The terminal's own `:,` in a page's spelling. */
export function count(n) {
  return n.toLocaleString("en-US");
}

/** A model's size on disk as the picker writes it, one decimal in GB;
    "" where it is not known — the rule `otaku.formatting.format_size`
    holds, copied, because the two languages cannot share a function. */
export function gigabytes(size) {
  return size > 0 ? `${(size / 1024 ** 3).toFixed(1)} GB` : "";
}

/** A context window the way the catalogs label it: "8K", "128K", "1M";
    "" when unknown — the rule `otaku.formatting.format_context` holds,
    copied: K and M decimal first, an exact power of two taking its
    vendor's label (131,072 is 128K too), anything else rounded to a whole
    unit as Python rounds, half to even. */
export function contextLabel(tokens) {
  if (!(tokens > 0)) return "";
  for (const [unit, decimal, binary] of [
    ["M", 1_000_000, 1_048_576],
    ["K", 1_000, 1_024],
  ]) {
    if (tokens >= decimal && tokens % decimal === 0) return `${tokens / decimal}${unit}`;
    if (tokens >= binary && tokens % binary === 0) return `${tokens / binary}${unit}`;
    if (tokens >= decimal) return `${halfEven(tokens / decimal)}${unit}`;
  }
  return String(tokens);
}

const halfEven = (x) => {
  const rounded = Math.round(x);
  return x % 1 === 0.5 && rounded % 2 !== 0 ? rounded - 1 : rounded;
};
