# otaku web — the token contract

Every colour and every typeface the interface uses is a custom property on
`:root` in `app.css`, and so is every size in the scale below. What this page
does not list — title cuts, the tabs, tracking, one-off lengths — is the
design's own, hard-coded on purpose, and no theme's business.

**A theme is a list of token overrides and nothing else.** Put them in the
state dir's `web/custom.css`, which loads last and wins. Override
`--otk-paper` and the page, every panel, every docket, every dialog and every
field change together — there is no second place where paper is defined.

Names are a contract. Nothing here is ever renamed; a rename is a breaking
change for anybody's `custom.css`. The contract is the tokens on this page
plus the hooks under **State you can style** — every other class and
attribute in the markup is internal and may change in any release, so a
stylesheet that reaches past the contract accepts the breakage itself.

## Ground

| Token | Used by |
|---|---|
| `--otk-desk` | the surface the page lies on |
| `--otk-spine-bg` | the app's vertical left rail |
| `--otk-paper` | the page, panels, dockets, dialogs, filters |
| `--otk-well` | the inside of an editable field |
| `--otk-edit-bg` | that field's ground while the caret is in it |
| `--otk-band` | panel footers, the composer, index rails |

## Ink

| Token | Used by |
|---|---|
| `--otk-ink` | titles, the primary button, hard rules |
| `--otk-body` | running prose, list rows |
| `--otk-muted` | labels, secondary text |
| `--otk-faint` | read-only values, hints, absent values, a line's words with the model (`.otk-ooc`) |

## Rules

| Token | Used by |
|---|---|
| `--otk-line` | panel edges, button borders, hard rules |
| `--otk-line-soft` | rules inside paper |
| `--otk-hair` | every rule width — there is only one |
| `--otk-mark` | the selection rule on a row, the active tab |
| `--otk-rule-w` | the accent rule of a panel title |

## Accent and status

| Token | Used by |
|---|---|
| `--otk-accent` | title rules, selection marks, the caret |
| `--otk-accent-ink` | accent used as text: links, rubrics, tokens, a chapter's number and its big initial |
| `--otk-accent-tint` | the ground of a selected row |
| `--otk-ok` | answering, resident, loaded |
| `--otk-danger` | delete, stop, refusals and failures — nothing else |
| `--otk-danger-tint` | the ground of a window's foot holding an error |
| `--otk-danger-flash` | the one flash of that ground as the error arrives |
| `--otk-chat-story` | the rule on the story's side in the chat layout |
| `--otk-chat-you` | the rule on the reader's side in the chat layout |

The tab icon is the one accent a stylesheet cannot reach: a favicon is
markup, so `index.html` carries the square as a hard-coded copy of the
accent (and a grey one for a stopped otaku). Retheming the accent leaves
it as it is, and the tab keeps its mark.

## The request's bands

The context panel draws the request in bands, each part where it comes
from: a hue for each kind of text sent besides the story's own lines, and
the summary in a grey fold.

| Token | Used by |
|---|---|
| `--otk-system-bg` / `--otk-system-line` / `--otk-system-ink` | the system message's band: its ground, its dotted edges, its gutter word and rubrics |
| `--otk-card-bg` / `--otk-card-line` / `--otk-card-ink` | a character card's band |
| `--otk-inject-bg` / `--otk-inject-line` / `--otk-inject-ink` | an injection's band: a text sent besides the story |
| `--otk-pinned-bg` / `--otk-pinned-line` / `--otk-pinned-ink` | the band of the pinned messages one header heads |
| `--otk-summary-bg` | the ground of the summary's fold |

## Typefaces of your own

Put a `.woff2` (or `.woff`, `.ttf`, `.otf`) in `web/fonts/` beside your
`custom.css` in the state dir, and ask for it under `/web-fonts/`:

```css
@font-face {
  font-family: 'Mine';
  src: url('/web-fonts/Mine.woff2') format('woff2');
  font-display: swap;
}
:root { --otk-font-prose: 'Mine', Georgia, serif; }
```

The prefix is its own so a file of yours can never shadow one of otaku's.
Three families carry the page — see below — and each is a token, so one
line re-voices everything set in it.

## Prose marks

What the MODEL writes into a reply, as against what the page draws.

| Token | Used by |
|---|---|
| `--otk-em` | `*emphasis*` — italic; `inherit`, so no colour of its own |
| `--otk-strong` | `**weight**` — likewise `inherit` |
| `--otk-strong-weight` | how heavy that is; `600` is a real cut of the prose face |

The marks are the TEXT, only italic or heavy. They take no accent: a
stage direction is the commonest thing in a roleplay reply, and
colouring every one of them would leave the page shouting — the accent
belongs to what the PAGE says, its links, labels and rubrics.

Inheriting is also what keeps a spoken line whole: emphasis inside a
quote takes the spoken colour, because it is still someone talking. Set
one of these to a real colour and it applies wherever the mark does.

`` `code` `` takes `--otk-font-mono` and nothing else.

## Dialogue

| Token | Used by |
|---|---|
| `--otk-dialogue` | spoken lines — quoted speech, wherever it is read |
| `--otk-dialogue-weight` | their weight; `inherit` leaves them at the prose's |

Speech has a colour of its own rather than sharing the accent, because
recolouring it should not recolour every link and rubric on the page.
Two lines give the whole transcript a different voice:

```css
:root { --otk-dialogue: #2f5fa8; --otk-dialogue-weight: 600; }
```

Spoken lines are UPRIGHT: the quotation marks already mark them as
speech, and italic is the model's own `*emphasis*`, which is a different
thing. There is no token for that — a stylesheet that wants its dialogue
slanted sets `font-style` on `.otk-quote, .otk-prose--dialogue` itself.

Emphasis inside prose (`*like this*`) is not speech: it takes whatever
colour it stands in, so inside a quote it is spoken too.

One accent. The primary button is **ink, not accent** — a stamp, not a
highlight. Colour is never the only signal, so an override cannot break
meaning: a selected row carries an accent rule as well as its tint, and a
dimmed one carries a word, a dashed border or a `read only` flag.

## Three voices, one job each

| Token | Default | Rule |
|---|---|---|
| `--otk-font-prose` | `'Newsreader', 'Literata', Georgia, serif` | anything read as a sentence |
| `--otk-font-display` | `'Archivo', 'IBM Plex Sans', system-ui, sans-serif` | names, titles, every control |
| `--otk-font-mono` | `'IBM Plex Mono', ui-monospace, monospace` | anything the machine produced |

The test: *could a person have written it?* prose. *Is it a name or something
you press?* display. *Did the machine produce it?* mono.

All faces are bundled; nothing is fetched from a font service. Name your own
first in the stack and keep the rest, and the bundled faces stay as the
fallback — CSS falls back **per glyph**, so a face that lacks a script loses
only that script.

## Type scale

| Token | Used by |
|---|---|
| `--otk-size-display` | a reading column's own title |
| `--otk-size-title` | the subject of a panel |
| `--otk-size-head` | a group head (provider, scene) |
| `--otk-size-page` | the story |
| `--otk-size-lead` | panel ledes, margin prose, the composer |
| `--otk-size-read` | long prose inside a panel |
| `--otk-size-row` | a list row |
| `--otk-size-note` | notes, captions, choices |
| `--otk-size-ui` | **every** button |
| `--otk-size-mono` | fields, filters, model names |
| `--otk-size-mono-sm` | facts, urls, ids |
| `--otk-size-small` | counts |
| `--otk-size-label` | a section eyebrow, a chapter's number |
| `--otk-size-foot` | panel footers |
| `--otk-size-tag` | kind tags, the turn rubric |

Leading: `--otk-leading-page` · `--otk-leading-read` · `--otk-leading-row`.
Tracking: `--otk-track-eyebrow` (panel title) · `--otk-track-label` (section) ·
`--otk-track-foot` (footers) · `--otk-track-tag`.

`--otk-row-h` is a list row's height, declared rather than left to whatever
fills it: every cell states `--otk-leading-row` and the row states this floor,
so a list of mixed scripts does not ripple. Raise both together.

## Boxes

| Token | Rule |
|---|---|
| `--otk-radius` | `0px` — print discipline: nothing is rounded except dots |
| `--otk-btn-pad` | one box for every button |
| `--otk-field-pad` | fields and filters |
| `--otk-panel-pad` | the gutter of every panel |
| `--otk-page-pad` | the page's own margin |
| `--otk-dialog-pad` | every dialog |
| `--otk-col-lamp` | the resident / answering lamp gutter |
| `--otk-col-size` | a model's size column |
| `--otk-col-count` | a count column |
| `--otk-col-messages` | a story's message count, "1234 msg" |
| `--otk-col-ago` | a "2h" column |
| `--otk-reader-w` | the right pane, identical in every lens |
| `--otk-measure` | a reading line, wherever prose is set |
| `--otk-premise-w` | the premise measure |

## Shell and the panel ladder

| Token | Used by |
|---|---|
| `--otk-spine-w` | the vertical app rail |
| `--otk-rail-w` | the contents |
| `--otk-page-max` | the page's measure |
| `--otk-panel-wide` | models: list plus detail |
| `--otk-panel-story` | stories and a story: one box, so neither resizes into the other |
| `--otk-panel-read` | context: one reading column |
| `--otk-dialog-w` | every dialog |
| `--otk-docket-w` | a slip's width; `.otk-docket--narrow` and `--wide` re-set it |
| `--otk-docket-h` | the height of the one slip that is torn to a set length |

## Scrollbars

The app styles its own, so a list edge stays a hairline instead of picking up
the platform's chrome.

| Token | Used by |
|---|---|
| `--otk-scrollbar-w` | the track width |
| `--otk-scrollbar-thumb` | the thumb — ink-family, so it reads as drawn |
| `--otk-scrollbar-track` | the channel it runs in |

`--otk-scrollbar-w` reaches only a browser that lacks `scrollbar-color`:
the standard `scrollbar-width`/`scrollbar-color` pair is what every
current browser is given, and `thin` is its own width. The two colour
tokens reach both.

## Depth

`--otk-shadow-page` · `--otk-shadow-panel` · `--otk-shadow-slip` ·
`--otk-scrim` · `--otk-scrim-blur`.

## Dark theme

The page follows the OS (`prefers-color-scheme`) until the switch at the
spine's foot (`[data-theme-switch]`, `role="switch"`) is first clicked;
`data-theme="dark"` or `"light"` on `<html>` pins it either way, and a click
sets one of the two, nothing else. The knob shows the theme in force — the
pin, or what the OS chose while there is none — and the pin is kept in the
browser's own storage under `otaku-theme` — per browser, never in the state
dir. A dark theme is the same token names overridden and nothing more —
`custom.css` outranks all three.

## State you can style

A token override reaches every element at once. To reach one state of one
thing, write a rule against the hooks below: state lives in attributes, not
in classes, so a stylesheet can name it the same way the scripts do.

| Hook | On | Meaning |
|---|---|---|
| `aria-selected="true"` | `.otk-row`, `.otk-tab`, `.otk-index__item`, `.otk-prefix`, `.otk-jstory` | the current one; on a card, the story whose journals are shown |
| `aria-checked="true"` / `"false"` | `.otk-choice`, `.otk-switch`, `.otk-toggle`, `.otk-tick` | the one in force; a switch on or off; a card picked for a story |
| `aria-pressed="true"` | `.otk-mode`, `.otk-ruler__cell`, `.otk-seg__btn` | the layout in force; the depth a tool's text goes in at; how All characters shows its cards |
| `aria-disabled="true"` | `.otk-btn`, `.otk-mode`, `.otk-lightbox__arrow` | out of reach for now |
| `aria-current="true"` | `.otk-spine__dot`, `.otk-pics__thumb` | the lit one of the spine's dots; the picture of a card shown large |
| `hidden` | any pane or menu | not mounted |
| `data-popup="/model"` | a `<dialog>` | which command opens it |
| `data-theme="dark"` / `"light"` | `<html>` | the token set pinned; absent, the OS decides |
| `data-booting` | `<html>` | the shell unseen until the page knows whether a sign-in comes first |
| `data-transcript="canvas"` / `"manuscript"` / `"chat"` | `.otk-transcript` | which of the three layouts the same turns are read in |
| `data-completion="chat"` / `"text"` | `.otk-app` | the wire the next request goes over; over the text wire the flow shows no picture tiles |
| `data-layout="canvas"` / `"manuscript"` / `"chat"` | `.otk-app` | the look the open story is drawn in; on the canvas the story is typed in at its end, the composer standing below |
| `data-playing` | `.otk-app` | a reply is arriving; the layout glyphs are inert meanwhile |
| `data-open="true"` | `.otk-rail` | the contents, on a narrow window |
| `data-view="list"` / `"small"` / `"mid"` / `"large"` | `.otk-chars` | All characters as rows, or as pictures at one of three tile sizes |
| `data-unlinked="open"` / `"closed"` | `.otk-chars` | the stories' unlinked characters beside the cards, or folded to a strip |
| `data-editing` | `.otk-edit` | the field editor is open |
| `data-framing` / `data-framed` | `.otk-edit` / a turn in the canvas | a paragraph of several rows is open as one field: the editor holding it, and the rows standing aside for it |
| `data-join="inline"` / `"paragraph"` | a turn in the transcript; `.otk-tail` | how it joins the turn before it: as it stands, or after a blank line — what the canvas layout draws by; on the typing place, `"paragraph"` alone, where the line typed will open one |
| `data-blank` | `.otk-tail` | the canvas's typing place holds nothing yet, so it shows the caret where the line goes |
| `data-refused` | the composer's box | on the canvas, where the box stands disabled, it holds the refusal of the line typed in the story, in place of its placeholder |
| `data-on="false"` | `.otk-feature`, `.otk-ruler__row` | the setting is off: its row on the Tools tab, and the depth ruler belonging to it |
| `data-dragging` | `.otk-ruler__row` | the depth mark is being dragged |
| `data-pinned="true"` / `"false"` | `.otk-row__id` in the messages list, `.otk-detail__pin` in its reader | the message is pinned: its number shows as a filled pin, as does the reader's pin; absent on a row that may not be pinned |
| `.is-offline` | `.otk-app` | the server stopped answering |
| `.is-loaded` / `.is-dim` | `.otk-row` | a model resident in memory, or not |
| `.is-dragover` / `.is-filled` | `.otk-dropzone` | a file over the drop zone, and one dropped |
| `.is-dragover` | `.otk-tile`, `.otk-charrow`, `.otk-pics` | a story character over a card or over new card, a card file over new card; pictures over a card's |
| `.is-dragging` | `.otk-pics__thumb`, `.otk-tile`, `.otk-charrow` | a card's picture, or a card, being moved to another place |
| `.is-out` | `.otk-editable` | a value outside its bounds, while typed |
| `.is-waiting` | `.otk-docket` | a report's slip with nothing to show yet |
| `.otk-generating--idle` | the generating block | the reply landed; the slot keeps its height |
| `.otk-status--working` / `--offline` / `--idle` | `.otk-status` | which of the three the one status line shows |
| `.otk-status--said` / `--error` | `.otk-status` | the sentence on it is news, or a refusal or a failure — an error, gone after thirty seconds |
| `.otk-panel__foot--error` / `.otk-docket__foot--error` / `.otk-dialog__foot--error` / `.otk-sheet__foot--error` | a window's foot | an error stands in it: one `.otk-alert` over its usual words, its ground flashed once and then tinted, until the foot says something else, the window closes, or thirty seconds pass |
