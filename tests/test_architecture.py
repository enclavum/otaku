"""CLAUDE.md's architecture, held as a test: the import graph (each
package may import only what its row allows), the inertness of the
backend bridge (its re-exports are data, never behavior), the privacy
of the `Session` handles (the backend package is the only user of its
underscore surface), the parity of the two frontends (every command the
shared table declares is answered by both), and the web page's own
module graph — which is JavaScript, and so is read as text.

A deliberate exception to the unit suite's pure-function rule: the
subject here IS the source tree, so the test reads it — and nothing
else. The tables mirror `CLAUDE.md`; a new package or arrow lands in
both in the same commit.
"""

import ast
import dataclasses
import enum
import re
from pathlib import Path

import otaku.backend
from otaku.backend.commands import COMMANDS, CommandKind, CommandSpec
from otaku.terminal.chat import bindings
from otaku.web import api as web_api
from otaku.web import server as web_server

PACKAGE = "otaku"
_ROOT = Path(__file__).resolve().parent.parent

# CLAUDE.md's import table. formatting is the stdlib-like leaf — anyone
# above may use it (its arrows are not drawn in the diagram), so it is
# listed per package here to keep the table explicit.
_ALLOWED = {
    # `python -m otaku`: the module entry, which only calls the real one.
    "__main__": {"cli"},
    "cli": {"terminal", "web", "backend", "logging", "update", "formatting"},
    # `web` because `/web` serves this session to a browser and waits: a
    # frontend that can hand the reader to the other one calls it, and
    # the arrow only points this way — the page has nowhere to send
    # anybody, so `web` still knows nothing of `terminal`.
    "terminal": {"console", "backend", "web", "formatting"},
    "web": {"console", "backend", "formatting"},
    # What a frontend draws in the terminal it was LAUNCHED from — the
    # banner both open with, and the tail under the web's. A leaf: it is
    # handed what it draws.
    "console": {"formatting"},
    "worker": {"context", "providers", "store", "logging", "formatting"},
    "backend": {
        "worker",
        "context",
        "providers",
        "store",
        "settings",
        "encryption",
        "logging",
        "formatting",
    },
    "context": {"store"},
    # `formatting` for `Money`: what a provider reports a balance IN is a
    # value type, and value types live in the leaf everyone may reach.
    "providers": {"settings", "formatting"},
    "store": {"encryption"},
    "settings": {"formatting"},
    "logging": {"providers", "encryption", "formatting"},
    "encryption": {"formatting"},
    "update": set(),
    "formatting": set(),
}


class TestArrows:
    def test_every_package_imports_only_its_allowed_arrows(self) -> None:
        violations = [
            f"{src} -> {dst}  ({where})"
            for src, dst, where in _edges()
            if dst not in _ALLOWED.get(src, set())
        ]
        assert not violations, "forbidden imports:\n" + "\n".join(violations)

    def test_every_package_has_a_declared_row(self) -> None:
        # A new top-level package must take a row here and in CLAUDE.md
        # before it may exist — even one that imports nothing yet.
        undeclared = _children() - set(_ALLOWED)
        assert not undeclared, f"packages without a declared row: {sorted(undeclared)}"


class TestBridge:
    def test_the_backend_bridge_reexports_only_inert_data(self) -> None:
        # backend/__init__ may re-export from lower packages ONLY data —
        # frozen dataclasses, enums and exceptions. Nothing with state
        # or behavior can be laundered through it into a frontend.
        alive = [
            name for name in otaku.backend.__all__ if not _is_inert(getattr(otaku.backend, name))
        ]
        assert not alive, f"backend re-exports with behavior: {alive}"


class TestSessionPrivacy:
    def test_the_session_underscore_surface_stays_inside_backend(self) -> None:
        # The backend's own modules are the implementation and may use
        # the package-private `Session` handles; nothing else ever does —
        # the suites included, which assert through a second Store
        # connection instead.
        needle = "session" + "._"  # split so this file never matches itself
        uses = []
        for tree in (PACKAGE, "tests", "scenarios"):
            for path in sorted((_ROOT / tree).rglob("*.py")):
                if "__pycache__" in path.parts:
                    continue
                if path.is_relative_to(_ROOT / PACKAGE / "backend"):
                    continue
                for number, line in enumerate(path.read_text().splitlines(), start=1):
                    if needle in line:
                        uses.append(f"{path.relative_to(_ROOT)}:{number}: {line.strip()}")
        assert not uses, f"{needle} outside the backend:\n" + "\n".join(uses)


def _children() -> set[str]:
    """The top-level packages and modules under the distribution root."""
    out = set()
    for path in (_ROOT / PACKAGE).iterdir():
        if path.is_dir() and (path / "__init__.py").exists():
            out.add(path.name)
        elif path.suffix == ".py" and path.stem != "__init__":
            out.add(path.stem)
    return out


def _edges() -> list[tuple[str, str, str]]:
    """Every cross-package import as (source package, target package,
    file:line), read from the source tree."""
    children = _children()
    out = []
    for path in sorted((_ROOT / PACKAGE).rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        src = _package_of(path)
        for target, line in _imports(path, children):
            if target != src:
                out.append((src, target, f"{path.relative_to(_ROOT)}:{line}"))
    return out


def _package_of(path: Path) -> str:
    """The top-level package a file belongs to (a root module like
    cli.py is its own row)."""
    relative = path.relative_to(_ROOT / PACKAGE)
    return relative.parts[0] if len(relative.parts) > 1 else relative.stem


def _imports(path: Path, children: set[str]) -> list[tuple[str, int]]:
    """The distribution-internal imports of one file as (target package,
    line). A bare root import carries no edge — only __version__ lives on
    the root — but `from otaku import x` is an edge per imported child."""
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(f"{PACKAGE}."):
                    found.append((alias.name.split(".")[1], node.lineno))
        elif isinstance(node, ast.ImportFrom):
            module = _absolute(node, path)
            if module == PACKAGE:
                found.extend(
                    (alias.name, node.lineno) for alias in node.names if alias.name in children
                )
            elif module.startswith(f"{PACKAGE}."):
                found.append((module.split(".")[1], node.lineno))
    return found


def _absolute(node: ast.ImportFrom, path: Path) -> str:
    """The absolute module a `from … import` names — relative imports
    resolved against the importing file's package, so none can dodge
    the check."""
    if not node.level:
        return node.module or ""
    context = list(path.parent.relative_to(_ROOT).parts)
    base = context[: len(context) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else []))


def _is_inert(obj: object) -> bool:
    """Data, not behavior: a frozen dataclass, an enum (a closed set of
    constants) or an exception type."""
    if not isinstance(obj, type):
        return False
    if issubclass(obj, BaseException | enum.Enum):
        return True
    return dataclasses.is_dataclass(obj) and obj.__dataclass_params__.frozen


# How the PAGE reaches every command the terminal answers. HAND-KEPT and
# not derived: the page routes by endpoint now, so nothing in the source
# can say which endpoint stands for which command — only a person can.
# Three shapes, and the third is the point of writing it down:
#
#   screen:TOKEN     a row of `SCREENS` in `web/static/js/commands.js`
#   METHOD /path     a row of the API tables, reached by a button
#   none:REASON      deliberately not on the page, and why
#
# A new command fails the suite here until somebody decides which of the
# three it is. That decision is CLAUDE.md's, under Architecture — the
# story's language.
_ON_THE_PAGE = {
    "/undo": "screen:/undo",
    "/regen": "screen:/regen",
    "/last": "screen:/last",
    "/clear": "screen:/clear",
    "/stories": "screen:/stories",
    "/fork": "screen:/fork",
    "/system": "screen:/system",
    "/title": "PUT /api/stories/{story}/title",
    "/new": "screen:/new",
    "/lore": "screen:/lore",
    "/cast": "screen:/cast",
    "/extract": "screen:/extract",
    "/merge": "none:no UI yet — the cast pane has no merge, by decision",
    "/context": "screen:/context",
    "/balance": "screen:/balance",
    "/usage": "screen:/usage",
    "/info": "screen:/info",
    "/card": "screen:/card",
    "/import": "screen:/import",
    "/export": "screen:/export",
    "/model": "screen:/model",
    "/set think": "screen:/set",
    "/set parameter": "screen:/set",
    "/set verbose": "screen:/set",
    "/set autocorrect": "screen:/set",
    "/set notification": "screen:/set",
    "/set max_context": "screen:/set",
    "/help": "screen:/help",
    # The one gap that can never be filled: this command's whole purpose
    # is to reach the page, and the reader of the page is already there.
    "/web": "none:the page IS this — there is nowhere for it to send anyone",
    "/bye": "screen:/bye",
}


class TestFrontendParity:
    """CLAUDE.md, Architecture — the story's language: a command is
    declared once and reachable in both. A row nobody wired is a button that does
    nothing, and the terminal's own dispatch would raise a KeyError
    where the page can only shrug — so it is caught here instead.

    The terminal still dispatches by token, so its half is derived. The
    page does not — it routes by endpoint — so its half is the hand-kept
    `_ON_THE_PAGE` above, which is also where a deliberate gap is
    recorded instead of going quiet.

    The web's screen table is JavaScript, so it is read from the source
    the way this module reads everything else."""

    def test_every_command_is_answered_by_the_terminal(self) -> None:
        assert _dispatchable() - _terminal_tokens() == set()

    def test_every_command_says_how_the_page_reaches_it(self) -> None:
        assert _dispatchable() == set(_ON_THE_PAGE)

    def test_every_screen_it_names_exists(self) -> None:
        assert _screens_named() - _web_screens() == set()

    def test_every_endpoint_it_names_is_served(self) -> None:
        served = {f"{method} {template}" for method, template in _api_surface()}
        assert _endpoints_named() - served == set()

    def test_every_row_says_which_of_the_three_it_is(self) -> None:
        # A misspelt `screen:` would otherwise read as an endpoint and
        # fail somewhere else, saying something that is not true.
        for token, row in _ON_THE_PAGE.items():
            reached = row.startswith(("screen:", "none:")) or row in _endpoints_named()
            assert reached, f"{token} says {row!r}, which is none of the three shapes"

    def test_neither_frontend_answers_a_command_that_does_not_exist(self) -> None:
        assert _terminal_tokens() - _answerable() == set()
        assert _web_screens() - _answerable() == set()


def _dispatchable() -> set[str]:
    """Every command a frontend must answer. SYNTAX rows are the story's
    own language — they play, they do not dispatch."""
    return {spec.token for spec in COMMANDS if spec.kind is not CommandKind.SYNTAX}


def _answerable() -> set[str]:
    """What a frontend is allowed to answer: a declared row, or the
    first word of a FAMILY of them. `/set` is not a command — the table
    declares `/set think`, `/set verbose` and the rest — but a frontend
    may open one screen for the family, as the web does. Inventing any
    other token is inventing a command."""
    declared = {spec.token for spec in COMMANDS}
    return declared | {token.split(" ")[0] for token in declared if " " in token}


def _terminal_tokens() -> set[str]:
    return set(bindings.OPERATIONS) | set(bindings._INTERACTIVE)


def _web_screens() -> set[str]:
    """Every screen the page opens by token — a JavaScript object
    literal, read as text, the same way this module reads the import
    graph."""
    source = (_ROOT / "otaku" / "web" / "static" / "js" / "commands.js").read_text()
    body = source.split("const SCREENS = {", 1)[1].split("\n};", 1)[0]
    return set(re.findall(r'^\s*"(/[a-z ]+)":', body, re.M))


def _api_surface() -> set[tuple[str, str]]:
    """Every method and path the code answers, whichever table holds it."""
    return set(web_api.ROUTES) | set(web_api.FLOWS)


def _screens_named() -> set[str]:
    """The screens `_ON_THE_PAGE` says a command opens."""
    return {
        row.removeprefix("screen:") for row in _ON_THE_PAGE.values() if row.startswith("screen:")
    }


def _endpoints_named() -> set[str]:
    """The endpoints it says a button calls — anything that is neither a
    screen nor a declared gap."""
    return {row for row in _ON_THE_PAGE.values() if not row.startswith(("screen:", "none:"))}


class TestPageModules:
    """The page is a set of ES modules with no build step and nothing to
    enforce their direction but a habit. The graph below is that habit
    written down: a module may import only what its row allows, and the
    edges that matter are one-way — `commands` reaches the screens,
    never the other way, or a screen could not be opened from the table
    that routes to it; and `table` (the language) is a leaf, so the
    composer's menu and the transcript's highlighting depend on no
    screen."""

    def test_every_module_imports_only_what_its_row_allows(self) -> None:
        for module, imported in _page_imports().items():
            assert imported <= _PAGE[module], f"{module} imports {imported - _PAGE[module]}"

    def test_every_module_is_in_the_table(self) -> None:
        assert set(_page_imports()) == set(_PAGE)

    def test_every_module_is_served(self) -> None:
        # A module the server does not list is a 404 at the first import.
        served = {name.removeprefix("js/").removesuffix(".js") for name in web_server._SCRIPTS}
        assert set(_PAGE) <= served


# What each page module may import. `api`, `dom`, `format`, `table` and
# `status` are the leaves; `browser` is what a screen is built from; one
# module per screen; `commands` is the dispatch over all of them; `app`
# is the composition root and may reach anything.
_PAGE = {
    "api": set(),
    "dom": set(),
    "format": set(),
    "table": set(),
    # The one line otaku speaks in. A leaf, so everything with something
    # to say can reach it without reaching for a screen.
    "status": {"dom"},
    "watch": {"dom"},
    "prose": {"dom"},
    "transcript": {"api", "dom", "prose", "status", "table"},
    "browser": {"dom", "status"},
    # `format` for the runhead's story name: a title is cut the same way
    # wherever the page writes one.
    # `browser` for `guard` alone — the kit's one barricade, which every
    # floating promise on this page goes through, the extraction Stop
    # included. The kit is below the frame, so this is not a cycle.
    "shell": {"api", "browser", "dom", "format", "status", "transcript"},
    "help": {"browser", "dom", "table"},
    # `story` is the dossier under the browser: the browser reaches into
    # it (Open story), never the other way — its route back is a command.
    "story": {"api", "browser", "dom", "format", "prose", "shell"},
    "stories": {"api", "browser", "dom", "format", "shell", "story", "transfer"},
    "models": {"api", "browser", "dom", "shell"},
    "settings": {"api", "browser", "dom", "table"},
    "reports": {"api", "browser", "dom", "format"},
    "transfer": {"api", "browser", "dom", "shell", "status"},
    "commands": {
        "api",
        "browser",
        "dom",
        "help",
        "models",
        "reports",
        "settings",
        "shell",
        "status",
        "stories",
        "story",
        "table",
        "transcript",
        "transfer",
    },
    "composer": {"api", "commands", "dom", "status", "table", "transcript"},
    "app": {
        "api",
        "browser",
        "commands",
        "composer",
        "dom",
        "shell",
        "table",
        "transcript",
        "watch",
    },
}


def _page_imports() -> dict[str, set[str]]:
    """Every `import … from "./x.js"` in the page's own modules, read as
    text — there is no import system here to ask."""
    static = _ROOT / "otaku" / "web" / "static"
    files = [static / "app.js", *sorted((static / "js").glob("*.js"))]
    return {
        path.stem: set(re.findall(r'from "\./(?:js/)?(\w+)\.js"', path.read_text()))
        for path in files
    }


class TestPageCaptions:
    """The page holds hand-kept captions for what the backend declares —
    the composer menu's `_MENU`, the help sheet's `_MEANS`, the settings
    slip's `_ABOUT` — because a caption is the medium's own words
    (CLAUDE.md, Copy conventions). Like `_ON_THE_PAGE`, each is a
    decision only a person can record, so a new syntax row or knob fails
    here until its caption exists, instead of rendering blank."""

    def test_every_syntax_token_has_a_menu_caption(self) -> None:
        offered = _js_keys("composer.js", "_MENU")
        bare = {spec.token.removeprefix("… ") for spec in _syntax_rows()}
        assert bare - offered == set()

    def test_every_syntax_row_has_a_help_meaning(self) -> None:
        assert {spec.token for spec in _syntax_rows()} - _js_keys("help.js", "_MEANS") == set()

    def test_every_knob_is_drawn_and_captioned_on_the_slip(self) -> None:
        source = _page_source("settings.js")
        for knob in web_api._KNOBS:
            assert f'"{knob}"' in source, f"the settings slip never draws {knob}"
        # `think` explains itself with its ladder; every other knob
        # carries a caption under its leader.
        assert set(web_api._KNOBS) - {"think"} - _js_keys("settings.js", "_ABOUT") == set()


def _syntax_rows() -> list[CommandSpec]:
    return [spec for spec in COMMANDS if spec.kind is CommandKind.SYNTAX]


def _page_source(name: str) -> str:
    return (_ROOT / "otaku" / "web" / "static" / "js" / name).read_text()


def _js_keys(name: str, constant: str) -> set[str]:
    """The keys of one JavaScript object literal, read as text —
    quoted (`"/me":`) or bare (`verbose:`)."""
    body = _page_source(name).split(f"const {constant} = {{", 1)[1].split("\n};", 1)[0]
    return {quoted or bare for quoted, bare in re.findall(r'^\s*(?:"([^"]+)"|(\w+)):', body, re.M)}


class TestWebTheme:
    """`docs/web_tokens.md` is the web's public theming contract
    (CLAUDE.md, Web conventions): every token `:root` declares is
    documented and nothing undeclared is — and the dark theme, one list
    written twice (once to pin, once to follow the OS), stays ONE list.
    Nothing else fails when either drifts, so it is held here."""

    def test_every_token_is_documented_and_nothing_more(self) -> None:
        declared = set(re.findall(r"(--otk-[a-z-]+):", _light_block()))
        documented = set(
            re.findall(r"--otk-[a-z-]+", (_ROOT / "docs" / "web_tokens.md").read_text())
        )
        assert declared == documented, (
            f"undocumented: {sorted(declared - documented)}; "
            f"documented but never declared: {sorted(documented - declared)}"
        )

    def test_the_two_dark_blocks_are_one_list(self) -> None:
        pinned = _dark_overrides('[data-theme="dark"] {')
        followed = _dark_overrides(':root:not([data-theme="light"]) {')
        assert pinned == followed, (
            f"only where data-theme pins: {sorted(pinned.items() - followed.items())}; "
            f"only where the OS is followed: {sorted(followed.items() - pinned.items())}"
        )


def _app_css() -> str:
    return (_ROOT / "otaku" / "web" / "static" / "app.css").read_text()


def _light_block() -> str:
    return _app_css().split(":root {", 1)[1].split("\n}", 1)[0]


def _dark_overrides(opener: str) -> dict[str, str]:
    """One dark block's `--otk-*` declarations, name to value."""
    block = _app_css().split(opener, 1)[1].split("}", 1)[0]
    return {
        name: value.strip() for name, value in re.findall(r"(--otk-[a-z-]+):\s*([^;]+);", block)
    }


class TestDemo:
    """The deployable web demo (`demo/web/`) fakes the whole HTTP surface in
    the visitor's browser. Its router must know every path the spec
    lists — read as text, like everything else here — or a new endpoint
    ships with a demo that silently cannot answer it."""

    def test_the_demo_routes_every_path_the_spec_lists(self) -> None:
        router = (_ROOT / "demo" / "web" / "demo.js").read_text()
        missing = [path for path in _spec_paths() if path not in router]
        assert not missing, f"paths the demo does not route: {missing}"


class TestWebApiSpec:
    """`otaku/web/api.yaml` is the HTTP surface as OpenAPI, maintained by
    hand (CLAUDE.md, Web conventions). Its path list is held against the
    code's own tables — the file read as text, like everything else this
    module reads — so an endpoint added, renamed, or dropped without the
    spec fails the suite. The schemas' truth stays the review's."""

    def test_the_spec_lists_exactly_the_served_api(self) -> None:
        spec = set(_spec_paths())
        assert spec == _served_paths(), (
            f"only in the spec: {sorted(spec - _served_paths())}; "
            f"only in the code: {sorted(_served_paths() - spec)}"
        )

    def test_every_served_method_is_in_the_spec(self) -> None:
        # A path can be in the spec with only some of its methods — a
        # DELETE added to an existing path is exactly the change that
        # slips through a path-only check.
        text = _spec_text()
        for method, template in {**web_api.ROUTES, **web_api.FLOWS}:
            block = text.split(f"\n  {template}:", 1)
            assert len(block) == 2, f"the spec does not list {template}"
            under = block[1].split("\n  /", 1)[0]
            listed = f"\n    {method.lower()}:" in under
            assert listed, f"the spec does not list {method} {template}"


def _spec_text() -> str:
    return (_ROOT / "otaku" / "web" / "api.yaml").read_text()


def _spec_paths() -> list[str]:
    """Every path the hand-kept OpenAPI lists, in the order it lists
    them — read as text, like everything else this module reads."""
    return re.findall(r"^  (/api/\S+):", _spec_text(), re.M)


def _served_paths() -> set[str]:
    """Every path the code answers: both API tables, plus the four the
    server holds itself — the heartbeat, the watch stream, the extraction
    poll (a run's own channel-safe poll, never the session's thread) and
    the two that PLAY, which answer with a stream rather than a payload."""
    tables = {template for _, template in {**web_api.ROUTES, **web_api.FLOWS}}
    return tables | {
        "/api/alive",
        "/api/watch",
        "/api/stories/{story}/extraction",
        "/api/play",
        "/api/play/last",
    }
