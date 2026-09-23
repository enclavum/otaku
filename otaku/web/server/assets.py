"""What may be served, as a CLOSED table: a request path is looked up
here and never joined onto a directory, so nothing composes its way to
`configs/providers.toml`. That is a property of the LOOKUP, not of who
wrote the list — the two families that grow, the page's scripts and its
typefaces, are listed from the package at import, so adding one is one
file and no row. Every packaged byte is read per request, which is what
makes an edit visible without a restart. The reader's own files — their
stylesheet, their typefaces — are looked up the same way against their
directory in the state dir."""

from importlib.resources import files
from pathlib import Path

# The packaged assets, read per request — and the same directory as a
# plain path, which is what a watcher can stat: a Traversable promises no
# mtime.
STATIC = files("otaku.web") / "static"
STATIC_PATH = Path(__file__).parents[1] / "static"  # otaku/web/static, beside this package

HTML = "text/html; charset=utf-8"
CSS = "text/css; charset=utf-8"
JS = "text/javascript; charset=utf-8"
EVENT_STREAM = "text/event-stream"
JSON = "application/json; charset=utf-8"
WOFF2 = "font/woff2"
MANIFEST = "application/manifest+json"

# A local page is never worth a stale byte; the fonts change about once
# per Plex release and their names change with them.
NO_STORE = "no-store, no-cache, must-revalidate, max-age=0"
IMMUTABLE = "public, max-age=31536000, immutable"

# Where the reader's own typefaces are asked for, and what may be one.
# Under a prefix of its own rather than `/fonts/`, so a name of theirs can
# never shadow one of ours — the packaged table is looked up first, and a
# reader debugging their own stylesheet should not have to know that.
CUSTOM_FONTS = "/web-fonts/"
FONT_SUFFIXES = {".woff2": WOFF2, ".woff": "font/woff", ".ttf": "font/ttf", ".otf": "font/otf"}


def _packaged(*where: str, suffix: str) -> tuple[str, ...]:
    """Every packaged file of one kind, listed from the directory it
    lives in — sorted, so the table is built the same way twice.

    Read at import, which is what makes the table below CLOSED: its keys
    are exactly the files that shipped, and a request path is looked up
    in it rather than joined onto a directory. Listing rather than typing
    them out is only about who keeps the inventory: adding a script or a
    font is then one file, not one file and one row. A file added while
    otaku is running needs a restart to be served — editing one does
    not, because the bytes are read per request."""
    directory = STATIC_PATH.joinpath(*where)
    prefix = "".join(f"{part}/" for part in where)
    if not directory.is_dir():
        return ()
    return tuple(
        sorted(f"{prefix}{found.name}" for found in directory.iterdir() if found.suffix == suffix)
    )


# The page's own modules and its typefaces, both taken from the package.
SCRIPTS = ("app.js", *_packaged("js", suffix=".js"))
FONTS = _packaged("fonts", suffix=".woff2")

# request path -> (packaged file, content type, cache policy). A path is
# LOOKED UP here and never joined onto a directory, which is what keeps a
# request from composing its way to `configs/providers.toml`. That is a
# property of the lookup, not of who wrote the list — so the two families
# that grow are listed from disk above.
ASSETS: dict[str, tuple[str, str, str]] = {
    "/": ("index.html", HTML, NO_STORE),
    "/app.css": ("app.css", CSS, NO_STORE),
    # what a home screen reads to open the page as an app of its own
    "/manifest.webmanifest": ("manifest.webmanifest", MANIFEST, NO_STORE),
    **{f"/{name}": (name, JS, NO_STORE) for name in SCRIPTS},
    **{f"/{name}": (name, WOFF2, IMMUTABLE) for name in FONTS},
}


def asset(name: str) -> bytes:
    """One packaged file, read now — which is why editing it needs no
    restart. The name comes from the table, never from the request."""
    target = STATIC
    for part in name.split("/"):
        target = target / part
    return target.read_bytes()


def own_font(custom_web_dir: Path, name: str) -> tuple[bytes, str] | None:
    """A typeface of the reader's own, from `web/fonts/` in the state
    dir — what makes `custom.css` a whole theme and not a palette:
    `@font-face { src: url("/web-fonts/Mine.woff2") }` and it is
    theirs. Their directory, their files. None when there is no such
    file — or no such directory, which is the normal case.

    Looked up the way the packaged assets are: the directory is LISTED
    and the name must be one of its entries, so nothing a request
    carries is ever joined onto a path — `..` is not a name `iterdir`
    returns. Not cached, unlike the packaged fonts: those carry a
    version in the name and these do not."""
    directory = custom_web_dir / "fonts"
    try:
        found = next(
            (f for f in directory.iterdir() if f.name == name and f.suffix in FONT_SUFFIXES),
            None,
        )
        if found is None:
            return None
        return found.read_bytes(), FONT_SUFFIXES[found.suffix]
    except OSError:
        # a file that went away between the listing and the read is the
        # same answer as no directory at all
        return None


def custom_css(custom_web_dir: Path) -> bytes:
    """The reader's own stylesheet, or nothing. Absent is the normal
    case, so it answers with an empty stylesheet rather than a 404 —
    a red line in the console is not a state to design for."""
    try:
        return (custom_web_dir / "custom.css").read_bytes()
    except OSError:
        return b""
